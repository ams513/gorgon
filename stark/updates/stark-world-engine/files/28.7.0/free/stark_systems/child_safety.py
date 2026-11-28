"""stark_systems/child_safety.py - Stark World Engine child-safety rules.

HARD RULE: nothing involving the abuse of children, ever. Nothing in this module can be switched off: there is no
setting, flag, world field or argument that disables a blocking rule. The cartridge runtime (js/05_safety.js) and
the UE5 / Godot bridges enforce the same rules; CHILD_SAFETY.md is the spec and safety/test_vectors.json keeps every
implementation in step.

  python -m stark_systems.child_safety WORLD.json [--json]     report; exit 1 when export would be blocked

  * Age groups: every character is 'child', 'teen' or 'adult' (default adult). Numbers (age: 12) and synonyms
    ('kid', 'teenager') are normalised. A character tagged adult or untagged whose own description says it is a
    child is TREATED as a child and the creator is warned (CS012).
  * A deterministic content checker over every authored string (names, dialogue, quests, items, descriptions,
    script string literals, node-graph parameters, any systems.* text). Word lists for 12 languages live in ONE file,
    safety/lexicon.json. Matching is whole-word (so 'kidding', 'Sussex', 'therapist' never match), with exception
    phrases ('naked eye', 'child's play', 'children of the night', biological 'sex of'), and an evasion pass
    (leetspeak, s.p.a.c.e.d letters, zero-width characters, look-alike Cyrillic letters, missing accents).
  * Data rules: children and teens can never be enemies, combat targets, captured, romanced, undressed or harmed by
    dialogue actions, quest steps, scripts or node graphs.
  * enforce_export(world) returns the world with explicit ages (what every exporter must use) or raises
    ChildSafetyError (a ValueError) whose message says what to fix.
"""
import copy, json, os, re, sys, threading, unicodedata
from bisect import bisect_right

HERE = os.path.dirname(os.path.abspath(__file__))
LEXICON_PATH = os.path.join(HERE, 'safety', 'lexicon.json')
POLICY = 'stark-child-safety'
POLICY_VERSION = 1
AGE_GROUPS = ('child', 'teen', 'adult')
MINOR_AGES = frozenset(('child', 'teen'))

RULES = {
    'CS001': ('block', 'a term that refers to sexual content involving minors'),
    'CS002': ('block', 'sexual or nudity wording in the same sentence as a reference to a minor'),
    'CS003': ('block', 'sexual wording in the same text as a reference to a minor'),
    'CS004': ('block', 'sexual wording in a conversation, quest or script that involves a child or teen character'),
    'CS005': ('block', "sexualised or nudity wording in a child or teen character's own name or description"),
    'CS006': ('block', 'a child or teen character in romance or intimacy data'),
    'CS007': ('block', 'a child or teen character set up as an enemy or combat target'),
    'CS008': ('block', 'abuse wording connected to a minor'),
    'CS009': ('block', 'violence directed at a minor'),
    'CS010': ('block', 'nudity or undressing data for a child or teen character'),
    'CS011': ('block', 'a child or teen character set up to be captured'),
    'CS014': ('block', 'a script or node graph that would harm, capture, romance or undress a child or teen character'),
    'CS015': ('block', 'an age group that is not child, teen or adult'),
    'CS012': ('warn', 'described as a child or teen but not tagged as one: treated as a minor'),
    'CS013': ('warn', 'sexual wording next to "girl" or "boy": every character involved must be an adult'),
    'CS104': ('warn', 'romantic or nudity wording in a conversation or quest that involves a child or teen character'),
    'CS109': ('warn', 'mentions past violence against a minor (allowed as backstory; the game can never show it)'),
}

ROMANCE_KEYS = frozenset(('romance', 'romance_start', 'start_romance', 'intimacy', 'intimate', 'date', 'marry',
                          'propose', 'seduce', 'flirt', 'kiss', 'affinity_romance', 'love'))
UNDRESS_KEYS = frozenset(('undress', 'strip', 'nude', 'nudity', 'remove_clothes', 'unclothe'))
CAPTURE_KEYS = frozenset(('capture', 'kidnap', 'abduct', 'imprison', 'enslave', 'cage', 'jail', 'arrest', 'bind'))
HARM_KEYS = frozenset(('damage', 'harm', 'hurt', 'kill', 'attack', 'execute', 'sacrifice', 'torture', 'punish', 'hit',
                       'slap', 'whip', 'beat', 'wound', 'poison', 'burn', 'stun'))
ROMANTIC_RELATIONS = frozenset(('romance', 'romantic', 'lover', 'lovers', 'partner', 'spouse', 'married', 'marriage',
                                'dating', 'date', 'betrothed', 'engaged', 'intimate', 'crush', 'fling', 'affair'))
NUDE_OUTFITS = frozenset(('none', 'nude', 'naked', 'underwear', 'lingerie', 'topless', 'bare', 'undressed', 'nothing'))
HOSTILE_ROLES = frozenset(('enemy', 'hostile', 'boss', 'monster', 'raider', 'bandit', 'target', 'combatant', 'foe'))
HARM_NODE_TYPES = frozenset(('combat.damage', 'combat.kill', 'combat.attack', 'combat.applyStatus', 'entity.kill',
                             'entity.damage', 'entity.setHostile', 'ai.setTarget', 'ai.attack', 'ai.capture',
                             'romance.start', 'romance.addAffinity', 'appearance.undress', 'appearance.setOutfit'))
SCRIPT_HARM_RE = re.compile(
    r'(?:\b(?:combat|ai|romance|appearance|entity|world|physics)\s*\.\s*)?\b(damage|kill|harm|hurt|attack|capture|'
    r'kidnap|undress|strip|startRomance|addAffinity|setHostile|setTarget|execute|sacrifice|torture|applyStatus|'
    r'setOutfit|start)\s*\(\s*([\'"])([^\'"\\\n]{1,64})\2(?:\s*,\s*([\'"])([^\'"\\\n]{1,64})\4)?')
SCRIPT_FIND_HARM_RE = re.compile(
    r'\b(?:find|get|entity)\s*\(\s*([\'"])([^\'"\\\n]{1,64})\1\s*\)\s*\.\s*(damage|kill|harm|hurt|attack|undress|'
    r'setHostile|capture|setOutfit|torture)\b')

# text fields: keys that never hold displayed text (enums, colours, file paths, tile maps, technical names)
SKIP_KEYS = frozenset(('t', 'kind', 'mode', 'mood', 'weather', 'col', 'color', 'colour', 'tiles', 'sprite', 'music',
                       'slot', 'wave', 'age', 'type', 'shape', 'icon', 'font', 'anchor', 'align', 'bind', 'src',
                       'path', 'file', 'url', 'version', 'genre', 'controller', 'rig', 'view', 'formation', 'tactic',
                       'easing', 'blend', 'op', 'event', 'events_on', 'stat', 'lang', 'languages', 'policy', 'source',
                       'nodes_layout', 'layout', 'ease', 'key_code', 'input', 'axis', 'button', 'sfx', 'cue', 'channel',
                       'material', 'mesh', 'texture', 'model', 'anim', 'animation', 'pose', 'curve', 'operator',
                       'cmp', 'unit', 'units', 'format_id', 'damage_type', 'ammo_type', 'surface', 'biome', 'terrain_kind'))
ID_KEYS = frozenset(('id', 'start', 'goto', 'next', 'node', 'dialog', 'giver', 'npc', 'item', 'flag', 'has', 'lacks',
                     'done', 'active', 'notStarted', 'complete', 'advance', 'take', 'give', 'entity', 'target',
                     'faction', 'shop', 'recipe', 'quest', 'from', 'to', 'out', 'in', 'lock', 'key', 'with', 'a', 'b',
                     'owner', 'station', 'behavior', 'behaviour', 'schedule', 'squad', 'loot', 'table', 'weapon',
                     'ability', 'perk', 'skill', 'tree', 'requires', 'unlock', 'graph', 'script', 'zone', 'region',
                     'template', 'pool', 'char', 'character', 'leader', 'members', 'candidates', 'partner', 'spawn'))
OWN_TEXT_KEYS = ('name', 'title', 'desc', 'description', 'bio', 'backstory', 'text', 'greeting', 'label', 'lines',
                 'appearance_text', 'look', 'traits', 'personality', 'job', 'role_text', 'epithet', 'nickname')
SCHEMA_KEYS = SKIP_KEYS | ID_KEYS | frozenset(OWN_TEXT_KEYS) | frozenset((
    'x', 'y', 'z', 's', 'rot', 'hp', 'n', 'gold', 'patrol', 'speaker', 'choices', 'nodes', 'branches', 'if', 'do', 'end',
    'steps', 'reward', 'xp', 'items', 'count', 'r', 'level', 'consume', 'auto', 'final', 'objects', 'quests', 'dialogs',
    'levels', 'story', 'author', 'difficulty', 'seed', 'size', 'water', 'day', 'len', 'spawn', 'player', 'heal', 'atk',
    'def', 'speed', 'min', 'max', 'value', 'values', 'params', 'links', 'weight', 'price', 'stock', 'qty', 'rate',
    'cooldown', 'range', 'radius', 'duration', 'time', 'delay', 'chance', 'w', 'h', 'width', 'height', 'damage',
    'knockback', 'stagger', 'cost', 'effect', 'effects', 'stats', 'state', 'states', 'tags', 'tag', 'role', 'team',
    'hostile', 'romanceable', 'outfit', 'appearance', 'schedules', 'characters', 'scripts', 'graphs', 'systems',
    'relations', 'reputation', 'factions', 'shops', 'recipes', 'loot_tables', 'skill_trees', 'perks', 'abilities',
    'weapons', 'widgets', 'tracks', 'keys', 'entries', 'rolls', 'guaranteed', 'templates', 'pools', 'variety',
    'chunk', 'lod', 'budgets', 'members', 'tiles', 'age_years', 'age_group', 'description', 'options', 'enabled',
    'romance', 'romance_start', 'intimacy', 'date', 'marry', 'kiss', 'seduce', 'flirt', 'undress', 'strip', 'nude', 'capture', 'kidnap', 'imprison', 'enslave', 'damage', 'harm', 'hurt', 'kill', 'attack', 'affinity', 'inc', 'set', 'bg', 'show', 'hide', 'shop', 'craft', 'win', 'heal', 'sfx', 'music'))

_CJK = '぀-ヿ㐀-䶿一-鿿豈-﫿'
_HANGUL = '가-힣'
TOKEN_RE = re.compile(rf'[{_CJK}]|[{_HANGUL}]+|(?:(?![{_CJK}{_HANGUL}])\w)+')
SENT_RE = re.compile(r'[.!?;\n。！？；…]+')
HAS_CJK_RE = re.compile(rf'[{_CJK}{_HANGUL}]')
LETTER_RE = re.compile(r'[^\W\d_]')
B_PY, E_PY = r'(?<!\w)', r'(?!\w)'
FOLD_SPECIAL = {'ł': 'l', 'ø': 'o', 'đ': 'd', 'ß': 'ss', 'æ': 'ae', 'œ': 'oe',
                'ı': 'i', 'þ': 'th', 'ð': 'd'}
_LEET_RUN = re.compile(r'[a-z0-9@$]+')
_SPACED = re.compile(r'(?<!\w)(?:[^\W\d_][ ._*~+-]){2,}[^\W\d_](?!\w)')
_SPACED_SEP = re.compile(r'[ ._*~+-]')
_REPEAT = re.compile(r'([^\W\d_])\1{2,}')
_KATAKANA_ONLY = re.compile(r'^[゠-ヿ]+$')


class ChildSafetyError(ValueError):
    """Raised by enforce_export. str(e) is a short message for the studio (<= 300 characters); e.report has all."""

    def __init__(self, report):
        self.report = report
        super().__init__(summary(report))


# ------------------------------------------------------------------ lexicon
def _fold(s):
    s = unicodedata.normalize('NFD', s)
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    return ''.join(FOLD_SPECIAL.get(c, c) for c in s)


def _term_tokens(term):
    """'faire l'amour' -> (['faire', 'l', 'amour'], ['s', "'"]): tokens plus the separator class between them."""
    toks, seps, last = [], [], None
    for m in TOKEN_RE.finditer(term):
        if last is not None:
            gap = term[last:m.start()]
            seps.append("'" if "'" in gap else '-' if '-' in gap else 's')
        toks.append(m.group(0))
        last = m.end()
    return toks, seps


def _rx(pattern, numbers=None, minor_alt=None):
    num = '(' + '|'.join([r'\d{1,2}'] + sorted((re.escape(k) for k in (numbers or {})), key=len, reverse=True)) + ')'
    p = pattern.replace('{B}', B_PY).replace('{E}', E_PY).replace('{NUM}', num)
    if minor_alt is not None:
        p = p.replace('{MINOR}', minor_alt)
    return re.compile(p)


def _alt(terms):
    """Regex alternation for lexicon terms (whole words, spaces = whitespace, trailing * = stem)."""
    out = []
    for t in sorted(set(terms), key=len, reverse=True):
        wild = t.endswith('*')
        t = t[:-1] if wild else t
        s = r'\s+'.join(re.escape(p) for p in t.split())
        out.append(s + (r'\w*' if wild else ''))
    return '(?:' + '|'.join(out) + ')' if out else '(?!x)x'


class Lexicon:
    """Compiled form of safety/lexicon.json (token tables + regexes). Use lexicon() to get the cached instance."""
    CATS = ('always_block', 'minor', 'teen', 'minor_weak', 'minor_time', 'sex_strong', 'sex_weak', 'abuse',
            'protective')

    def __init__(self, data):
        self.data = data
        self.version = data.get('version', 1)
        self.weak_window = int(data.get('weak_window', 8))
        self.weak_window_cjk = int(data.get('weak_window_cjk', 16))
        self.min_stop = int(data.get('min_stopword_hits', 2))
        nz = data.get('normalize', {})
        self.zero_width = re.compile(nz.get('zero_width', '[​]'))
        self.apos = re.compile(nz.get('apostrophes', '[’]'))
        self.leet = nz.get('leet', {})
        self.homo = nz.get('homoglyphs', {})
        self.age_syn = data.get('age_synonyms', {})
        self.common_names = set(data.get('common_name_words', []))
        self.words = data.get('word_languages', {})
        self.cjk = data.get('cjk_languages', {})
        self.langs = list(self.words) + list(self.cjk)
        # which languages list each (folded) term: a term found in exactly one language "activates" that language
        owners = {}
        for code, L in self.words.items():
            for cat in self.CATS:
                for t in L.get(cat, []):
                    if not t.startswith('~'):
                        owners.setdefault(t.lower(), set()).add(code)
        self.tables = {False: self._tables(owners, folded=False), True: self._tables(owners, folded=True)}
        self.stop = {code: set(L.get('stopwords', [])) for code, L in self.words.items()}
        self.tilde = {}
        for code, L in self.words.items():
            plain, stems = set(), set()
            for cat in ('minor', 'teen', 'minor_weak'):
                for t in L.get(cat, []):
                    if t.startswith('~'):
                        (stems if t.endswith('*') else plain).add(t[1:].rstrip('*').lower())
            self.tilde[code] = (plain, stems)
        self.exc_case, self.exc_nocase = [], []
        for code, L in self.words.items():
            for e in L.get('exceptions', []):
                (self.exc_case if e.get('case') else self.exc_nocase).append(_rx(e['re']))
        self.cjk_exc = [x for L in self.cjk.values() for x in L.get('exceptions', [])]
        self.age = {}
        for code, L in self.words.items():
            pats = []
            for p in L.get('age_patterns', []):
                p = {'re': p} if isinstance(p, str) else p
                pats.append((_rx(p['re'], L.get('numbers')), int(p.get('minus', 0))))
            self.age[code] = pats
        for code, L in self.cjk.items():
            self.age[code] = [(re.compile(p), 0) for p in L.get('age_patterns', [])]
        en = self.words.get('en', {})
        self.numbers = {code: {k.lower(): v for k, v in L.get('numbers', {}).items()} for code, L in self.words.items()}
        self.grade_re = _rx(en['grade_pattern']) if en.get('grade_pattern') else None
        self.grade_numbers = en.get('grade_numbers', {})
        self.minor_res = [_rx(p) for p in en.get('minor_patterns', [])]
        self.teen_re = re.compile(B_PY + _alt([t.lstrip('~') for L in self.words.values() for t in L.get('teen', [])]) + E_PY)
        self.child_re = re.compile(B_PY + _alt([t.lstrip('~') for L in self.words.values() for t in L.get('minor', [])]) + E_PY)
        self.cjk_re = {}
        for code, L in self.cjk.items():
            mode = L.get('boundary', 'substring')
            per = {}
            for cat in self.CATS:
                alts = []
                for t in sorted(set(L.get(cat, [])), key=len, reverse=True):
                    e = re.escape(t)
                    if mode == 'kana' and _KATAKANA_ONLY.match(t):
                        e = r'(?<![゠-ヿ])' + e + r'(?![゠-ヿ])'
                    elif mode == 'eojeol_start':
                        e = r'(?<![가-힣])' + e
                    alts.append(e)
                if alts:
                    per[cat] = re.compile('|'.join(alts))
            masks = {cat: [m for m in ms] for cat, ms in L.get('category_masks', {}).items()}
            self.cjk_re[code] = (per, masks)
        self.violence = {}
        for code, L in self.words.items():
            v = L.get('violence') or {}
            if v.get('verbs') or v.get('abuse_verbs'):
                vt = set()
                for k in ('verbs', 'abuse_verbs', 'kill_participles', 'abuse_participles', 'infinitives'):
                    for w in v.get(k, []):
                        vt.update(_term_tokens(w.lower())[0])
                self.violence[code] = (v, vt)
        self._viol_cache = {}
        self._self_cache = {}

    def _tables(self, owners, folded):
        single, stems, phrases = {}, {}, {}
        for code, L in self.words.items():
            for cat in self.CATS:
                for raw in L.get(cat, []):
                    cond = raw.startswith('~')
                    term = raw[1:] if cond else raw
                    wild = term.endswith('*')
                    base = unicodedata.normalize('NFKC', term.rstrip('*')).lower()
                    if folded:
                        base = _fold(base)
                    toks, seps = _term_tokens(base)
                    if not toks:
                        continue
                    unique = (not cond) and len(owners.get(term.lower(), ())) == 1
                    entry = (code, cat, raw, cond, unique)
                    if len(toks) == 1 and wild:
                        stems.setdefault(toks[0], []).append(entry)
                    elif len(toks) == 1:
                        single.setdefault(toks[0], []).append(entry)
                    else:
                        phrases.setdefault(toks[0], []).append((tuple(toks[1:]), tuple(seps), wild, entry))
        return single, stems, sorted({len(s) for s in stems}), phrases

    def minor_alt(self, code, names=(), tilde=True):
        L = self.words.get(code, {})
        terms = [t.lstrip('~') for c in ('minor', 'teen') for t in L.get(c, []) if tilde or not t.startswith('~')]
        if code != 'en':
            terms += [t.lstrip('~') for c in ('minor', 'teen') for t in self.words['en'].get(c, [])]
        alt = _alt(terms)
        if names:
            alt = '(?:' + alt + '|' + '|'.join(r'\s+'.join(re.escape(p) for p in n.split())
                                             for n in sorted(names, key=len, reverse=True)) + ')'
        return alt

    def violence_res(self, code, names):
        key = (code, tuple(sorted(names)))
        if key in self._viol_cache:
            return self._viol_cache[key]
        v, _ = self.violence[code]
        minor = '(' + self.minor_alt(code, names) + ')'
        det = '|'.join(re.escape(d) for d in sorted(v.get('det', []), key=len, reverse=True)) or '(?!x)x'
        eli = '|'.join(re.escape(d) for d in v.get('det_elided', []))
        slot = rf'(?:(?:{det})\s+' + (rf'|(?:{eli})' if eli else '') + ')'
        aux = '|'.join(r'\s+'.join(map(re.escape, a.split())) for a in sorted(v.get('aux', []), key=len, reverse=True))
        adv = (rf'(?:{v["adverb"]}\s+)?' if v.get('adverb') else '')
        res = []
        for kind, verbs in (('abuse', v.get('abuse_verbs', [])), ('kill', v.get('verbs', []))):
            if verbs:
                res.append((kind, 'active', re.compile(rf'{B_PY}(?:{_alt(verbs)})\s+{slot}{{0,3}}{minor}(?!\'){E_PY}')))
        for kind, parts in (('abuse', v.get('abuse_participles', [])), ('kill', v.get('kill_participles', []))):
            if parts and aux:
                res.append((kind, 'passive', re.compile(rf'{B_PY}{minor}\s+(?:{aux})\s+{adv}(?:{_alt(parts)}){E_PY}')))
        if v.get('object_before_verb') and v.get('infinitives'):
            ab = set(v.get('abuse_verbs', []))
            for kind, inf in (('abuse', [i for i in v['infinitives'] if i in ab]),
                              ('kill', [i for i in v['infinitives'] if i not in ab])):
                if inf:
                    res.append((kind, 'active', re.compile(rf'{B_PY}{slot}{{0,3}}{minor}\s+(?:{_alt(inf)}){E_PY}')))
        self._viol_cache[key] = res
        return res

    def self_res(self, code):
        if code in self._self_cache:
            return self._self_cache[code]
        L = self.words.get(code, {})
        minor = self.minor_alt(code, tilde=False)       # false friends ('kind') never infer an age
        pats = L.get('self_patterns')
        if not pats:
            det = '|'.join(re.escape(d) for d in (L.get('violence') or {}).get('det', [])) or '(?!x)x'
            pats = [rf'^\s*(?:(?:{det})\s+)?(?:[\w\'-]+\s+){{0,1}}(?:{{MINOR}}){{E}}']
        res = [_rx(p, L.get('numbers'), minor) for p in pats]
        self._self_cache[code] = res
        return res


_LEX = {}
_LEX_LOCK = threading.Lock()


def lexicon(path=None):
    path = path or LEXICON_PATH
    with _LEX_LOCK:
        st = os.stat(path)
        key = (path, st.st_mtime_ns, st.st_size)
        if key not in _LEX:
            with open(path, encoding='utf-8') as f:
                _LEX.clear()
                _LEX[key] = Lexicon(json.load(f))
        return _LEX[key]


# ------------------------------------------------------------------ text analysis
def _mask(s, regexes):
    for rx in regexes:
        s = rx.sub(lambda m: ' ' * len(m.group(0)), s)
    return s


def prepare(text, lex):
    """-> (case-preserving text, lowered text): NFKC, zero-width removed, apostrophes unified, exceptions masked."""
    s = unicodedata.normalize('NFKC', str(text))
    s = lex.zero_width.sub('', s)
    s = lex.apos.sub("'", s)
    s = _mask(s, lex.exc_case)
    low = s.lower()
    low = _mask(low, lex.exc_nocase)
    for x in lex.cjk_exc:
        if x in low:
            low = low.replace(x, ' ' * len(x))
    if len(low) == len(s):
        s = ''.join(c if low[i] != ' ' or c == ' ' else ' ' for i, c in enumerate(s))
    else:
        s = low
    return s, low


def skeleton(low, lex):
    """The evasion-resistant form of an already lowered text: no accents, look-alike letters mapped in mixed-script
    words, leetspeak mapped in mixed letter/digit runs, s.p.a.c.e.d letters joined, 3+ repeated letters collapsed."""
    s = _fold(low)

    def homo(m):
        w = m.group(0)
        if re.search('[a-z]', w) and any(c in lex.homo for c in w):
            return ''.join(lex.homo.get(c, c) for c in w)
        return w
    s = re.sub(r'\w+', homo, s)

    def leet(m):
        w = m.group(0)
        if re.search('[a-z]', w) and re.search('[013457@$]', w) and not w.isdigit():
            return ''.join(lex.leet.get(c, c) for c in w)
        return w
    s = _LEET_RUN.sub(leet, s)
    s = s.replace('_', ' ')
    s = _SPACED.sub(lambda m: _SPACED_SEP.sub('', m.group(0)), s)
    s = _REPEAT.sub(r'\1', s)
    return _mask(s, lex.exc_nocase)


class Analysis:
    """Every lexicon hit in one text variant ('text' or 'skeleton'), with token and sentence positions."""

    def __init__(self, variant, text):
        self.variant = variant
        self.text = text
        self.hits = []          # (cat, lang, term, start, end, tok_index, sent_index)
        self.ages = []          # (age, start, end, tok_index, sent_index)
        self.violence = []      # (kind, mode, start, end, sent_index, excerpt)
        self.langs = set()
        self.protective = False
        bounds = [m.end() for m in SENT_RE.finditer(text)]
        self._bounds = bounds
        self.tokens = [(m.start(), m.end(), m.group(0)) for m in TOKEN_RE.finditer(text)]
        self._tstarts = [t[0] for t in self.tokens]

    def sent(self, pos):
        return bisect_right(self._bounds, pos)

    def tok(self, pos):
        return bisect_right(self._tstarts, pos) - 1

    def sentence_text(self, idx):
        b = [0] + self._bounds + [len(self.text)]
        return self.text[b[idx]:b[idx + 1]].strip()

    def add(self, cat, lang, term, start, end):
        self.hits.append((cat, lang, term, start, end, self.tok(start), self.sent(start)))

    def cats(self, *cats):
        return [h for h in self.hits if h[0] in cats]


MINOR_CATS = ('minor', 'teen', 'minor_name')


def _match_words(an, case_text, lex, folded, names, declared):
    single, stems, stem_lens, phrases = lex.tables[folded]
    toks, text = an.tokens, an.text
    raw = []
    for i, (s, e, t) in enumerate(toks):
        for entry in single.get(t, ()):
            raw.append((entry, s, e))
        for n in stem_lens:
            if n <= len(t):
                for entry in stems.get(t[:n], ()):
                    raw.append((entry, s, e))
        for rest, seps, wild, entry in phrases.get(t, ()):
            j, ok, end = i, True, e
            for k, want in enumerate(rest):
                j += 1
                if j >= len(toks):
                    ok = False
                    break
                gap = text[toks[j - 1][1]:toks[j][0]]
                sep = seps[k]
                if (sep == 's' and (not gap or gap.strip())) or (sep in ("'", '-') and gap != sep):
                    ok = False
                    break
                got = toks[j][2]
                if not (got == want or (wild and k == len(rest) - 1 and got.startswith(want))):
                    ok = False
                    break
                end = toks[j][1]
            if ok:
                raw.append((entry, s, end))
    # names of child / teen characters (full names any case; a lone first name only when capitalised)
    for i, (s, e, t) in enumerate(toks):
        for ntoks, needs_cap in names.get(t, ()):
            n = len(ntoks)
            if i + n > len(toks) or any(toks[i + k][2] != ntoks[k] for k in range(1, n)):
                continue
            if any(text[toks[i + k][1]:toks[i + k + 1][0]].strip() for k in range(n - 1)):
                continue
            if needs_cap and (folded or not case_text[s:s + 1].isupper()):
                continue
            an.add('minor_name', '*', ' '.join(ntoks), s, toks[i + n - 1][1])
    # language activation: declared + stopwords + an unambiguous term of the language
    active = set(declared) | {'en'}
    counts = {code: len({t for _, _, t in toks if t in sw}) for code, sw in lex.stop.items()}
    best = max(counts.values()) if counts else 0
    for code, c in counts.items():
        if c >= lex.min_stop and c * 2 >= best:
            active.add(code)
    for (code, cat, term, cond, unique), s, e in raw:
        if unique:
            active.add(code)
    an.langs |= active
    for (code, cat, term, cond, unique), s, e in raw:
        if cond and code not in active:
            continue
        if cat == 'protective':
            an.protective = True
        an.add(cat, code, term, s, e)
    return active


def _match_cjk(an, lex):
    text = an.text
    if not HAS_CJK_RE.search(text):
        return
    for code, (per, masks) in lex.cjk_re.items():
        for cat, rx in per.items():
            spans = []
            for mk in masks.get(cat, ()):
                start = text.find(mk)
                while start >= 0:
                    spans.append((start, start + len(mk)))
                    start = text.find(mk, start + 1)
            for m in rx.finditer(text):
                if any(a <= m.start() and m.end() <= b for a, b in spans):
                    continue
                if cat == 'protective':
                    an.protective = True
                an.add(cat, code, m.group(0), m.start(), m.end())
                an.langs.add(code)


def _ages(an, lex, active):
    text = an.text
    has_digit = any(ch.isdigit() for ch in text)
    for code, pats in lex.age.items():
        if code in lex.cjk:
            if not HAS_CJK_RE.search(text):
                continue
        elif not (code in active or has_digit):
            continue
        nums = lex.numbers.get(code, {})
        for rx, minus in pats:
            for m in rx.finditer(text):
                val = next((g for g in m.groups() if g), None)
                if val is None:
                    continue
                n = int(val) if val.isdigit() else nums.get(val.lower(), lex.numbers['en'].get(val.lower()))
                if n is None:
                    continue
                n -= minus
                if 0 <= n < 18:
                    an.ages.append((n, m.start(), m.end(), an.tok(m.start()), an.sent(m.start())))
    if lex.grade_re and ('grade' in text):
        for m in lex.grade_re.finditer(text):
            g = m.group(1) or m.group(2)
            n = int(g) if g.isdigit() else lex.grade_numbers.get(g, 0)
            if 0 < n <= 12:
                an.ages.append((n + 5, m.start(), m.end(), an.tok(m.start()), an.sent(m.start())))
    if lex.minor_res and 'minor' in text:
        for rx in lex.minor_res:
            for m in rx.finditer(text):
                an.add('minor', 'en', 'minor', m.start(), m.end())


def _violence(an, lex, active, names):
    if not (an.cats(*MINOR_CATS) or an.ages):
        return
    words = {t for _, _, t in an.tokens}
    for code, (v, vt) in lex.violence.items():
        if code not in active or not (words & vt):
            continue
        plain, stems = lex.tilde.get(code, (set(), set()))
        for kind, mode, rx in lex.violence_res(code, names):
            for m in rx.finditer(an.text):
                who = (m.group(1) or '').lower()
                if (who in plain or any(who.startswith(s) for s in stems)) and code not in active:
                    continue
                an.violence.append((kind, mode, m.start(), m.end(), an.sent(m.start()), m.group(0)))


class NameIndex(dict):
    """first token -> [(tokens, needs_capital)] for the names of child / teen characters."""

    def __init__(self, entries=()):
        super().__init__()
        self.full = []
        for toks, cap in entries:
            self.setdefault(toks[0], []).append((toks, cap))
            if not cap:
                self.full.append(' '.join(toks))


def analyse(text, lex=None, names=None, declared=(), variants=True):
    """-> [Analysis] for the text and (when it differs) its evasion skeleton. names: a NameIndex."""
    lex = lex or lexicon()
    names = names if isinstance(names, NameIndex) else NameIndex(names or ())
    case_text, low = prepare(text, lex)
    out = []
    an = Analysis('text', low)
    active = _match_words(an, case_text, lex, False, names, declared)
    _match_cjk(an, lex)
    _ages(an, lex, active)
    _violence(an, lex, active, names.full)
    out.append(an)
    if variants:
        sk = skeleton(low, lex)
        if sk != low:
            an2 = Analysis('skeleton', sk)
            active2 = _match_words(an2, sk, lex, True, names, declared)
            _match_cjk(an2, lex)
            _ages(an2, lex, active2)
            _violence(an2, lex, active2, names.full)
            out.append(an2)
    return out


def _finding(rule, path, where, message, terms=None, excerpt=None):
    sev, title = RULES[rule]
    f = {'rule': rule, 'severity': sev, 'title': title, 'path': path, 'where': where, 'message': message}
    if terms:
        f['terms'] = terms
    if excerpt:
        f['excerpt'] = excerpt[:120]
    return f


def _q(terms):
    return ', '.join(f'"{t}"' for t in sorted(set(terms))[:3])


def text_findings(analyses, path='text', where='text', lex=None):
    """The sentence- and text-level rules (CS001, CS002, CS003, CS008, CS009, CS013, CS109) for one string."""
    lex = lex or lexicon()
    out, seen = [], set()

    def add(rule, msg, terms=None, excerpt=None, si=-1):
        # one finding per rule and sentence: the evasion pass or a second mechanism never repeats a finding
        k = (rule, si)
        if k not in seen:
            seen.add(k)
            out.append(_finding(rule, path, where, msg, terms, excerpt))
    for an in analyses:
        ab = an.cats('always_block')
        if ab:
            add('CS001', f'{_q(h[2] for h in ab)} refers to sexual content involving minors. Stark World Engine never '
                         'exports it. Remove it.', {'always_block': [h[2] for h in ab]})
        sents = {}
        for h in an.hits:
            sents.setdefault(h[6], []).append(h)
        for a in an.ages:
            sents.setdefault(a[4], []).append(('age', 'num', f'age {a[0]}', a[1], a[2], a[3], a[4]))
        minors_all, strong_all = [], []
        for si, hs in sorted(sents.items()):
            minors = [h for h in hs if h[0] in MINOR_CATS or h[0] == 'age']
            weakm = [h for h in hs if h[0] == 'minor_weak']
            strong = [h for h in hs if h[0] == 'sex_strong']
            weak = [h for h in hs if h[0] == 'sex_weak']
            abuse = [h for h in hs if h[0] == 'abuse']
            mtime = [h for h in hs if h[0] == 'minor_time']
            minors_all += minors
            strong_all += strong
            excerpt = an.sentence_text(si)
            if minors and strong:
                add('CS002', f'{_q(h[2] for h in strong)} is in the same sentence as {_q(h[2] for h in minors)}. '
                             'Sexual content involving minors is never allowed. Rewrite or remove this sentence.',
                    {'sexual': [h[2] for h in strong], 'minor': [h[2] for h in minors]}, excerpt, si)
            if minors and weak:
                win = lex.weak_window_cjk if HAS_CJK_RE.search(excerpt) else lex.weak_window
                near = [w for w in weak if any(abs(w[5] - m[5]) <= win for m in minors)]
                if near:
                    add('CS002', f'{_q(h[2] for h in near)} is close to {_q(h[2] for h in minors)}. Nudity, '
                                 'undressing and romance are never allowed for minors. Rewrite this sentence.',
                        {'sexual': [h[2] for h in near], 'minor': [h[2] for h in minors]}, excerpt, si)
            if (minors or mtime) and abuse:
                add('CS008', f'{_q(h[2] for h in abuse)} is connected to {_q(h[2] for h in minors + mtime)}. '
                             'Content about abusing children is never allowed. Remove this sentence.',
                    {'abuse': [h[2] for h in abuse], 'minor': [h[2] for h in minors + mtime]}, excerpt, si)
            if weakm and strong and not minors:
                add('CS013', f'{_q(h[2] for h in strong)} is next to {_q(h[2] for h in weakm)}. If anyone here is '
                             'under 18 this is not allowed; if they are adults, say so (e.g. "woman", "man").',
                    {'sexual': [h[2] for h in strong], 'ambiguous': [h[2] for h in weakm]}, excerpt, si)
        if minors_all and strong_all and not any(f['rule'] == 'CS002' for f in out):
            add('CS003', f'{_q(h[2] for h in strong_all)} and {_q(h[2] for h in minors_all)} are in the same text. '
                         'Sexual content and minors can never share a text. Split or rewrite it.',
                {'sexual': [h[2] for h in strong_all], 'minor': [h[2] for h in minors_all]})
        for kind, mode, s, e, si, frag in an.violence:
            if kind == 'abuse':
                add('CS008', f'"{frag.strip()}" describes abuse of a minor. Content about abusing children is never '
                             'allowed. Remove it.', {'abuse': [frag.strip()]}, an.sentence_text(si), si)
            elif mode == 'active':
                if not an.protective:
                    add('CS009', f'"{frag.strip()}" directs violence at a minor. Children can never be harmed in a '
                                 'Stark World Engine game. Remove it or make it about protecting them.', {'violence': [frag.strip()]},
                        an.sentence_text(si), si)
            else:
                add('CS109', f'"{frag.strip()}" mentions past harm to a minor. Allowed as backstory; the game itself '
                             'can never show or let anyone harm a child character.', {'violence': [frag.strip()]},
                    an.sentence_text(si), si)
    return out


def scan_text(text, names=(), declared=(), path='text', where='text'):
    """Quick check of one string. names: child/teen character names. -> list of findings."""
    lex = lexicon()
    return text_findings(analyse(text, lex, NameIndex(_name_entries(names, lex)), declared), path, where, lex)


def text_is_blocked(text, names=(), declared=()):
    return any(f['severity'] == 'block' for f in scan_text(text, names, declared))


# ------------------------------------------------------------------ ages
def normalize_age(value, lex=None):
    """-> ('child'|'teen'|'adult'|None, ok). Numbers are years; None means not set (adult by default)."""
    lex = lex or lexicon()
    if value is None or value == '':
        return None, True
    if isinstance(value, bool):
        return None, False
    if isinstance(value, (int, float)):
        if value != value or value < 0:
            return None, False
        return ('child' if value < 13 else 'teen' if value < 18 else 'adult'), True
    v = str(value).strip().lower()
    if v in AGE_GROUPS:
        return v, True
    if v.isdigit():
        return normalize_age(int(v), lex)
    if v in lex.age_syn:
        return lex.age_syn[v], True
    return None, False


def infer_age(texts, lex=None, declared=()):
    """-> 'child' | 'teen' | None when a character's OWN description says it is a minor ('A ten-year-old who...',
    'She is only 12', 'A shy young boy')."""
    lex = lex or lexicon()
    best = None
    for raw in texts:
        if not isinstance(raw, str) or not raw.strip():
            continue
        _, low = prepare(raw, lex)
        for code in lex.words:
            for rx in lex.self_res(code):
                m = rx.search(low)
                if not m or low[m.end():m.end() + 1] == "'":      # "Children's teacher" is not a child
                    continue
                frag = m.group(0)
                age = _age_in(frag, lex, code)
                if age is None:
                    age = 'teen' if lex.teen_re.search(frag) and not lex.child_re.search(frag) else 'child'
                if age in MINOR_AGES:
                    best = 'child' if 'child' in (best, age) else 'teen'
        head = low.strip()[:8]
        for code, L in lex.cjk.items():
            if any(t in head for t in L.get('minor', [])):
                best = best or 'child'
            elif any(t in head for t in L.get('teen', [])):
                best = best or 'teen'
            for rx, _ in lex.age.get(code, ()):
                m = rx.search(low.strip()[:12])
                if m and int(m.group(1)) < 18:
                    best = 'child' if int(m.group(1)) < 13 else (best or 'teen')
    return best


def _age_in(frag, lex, code):
    for rx, minus in lex.age.get(code, []) + lex.age.get('en', []):
        m = rx.search(frag)
        if m:
            val = next((g for g in m.groups() if g), None)
            if val:
                n = int(val) if val.isdigit() else lex.numbers.get(code, {}).get(val, lex.numbers['en'].get(val))
                if n is not None:
                    n -= minus
                    return 'child' if n < 13 else 'teen' if n < 18 else 'adult'
    # a self-description pattern only contains a number where it is an age ("Age: 15", "she is only twelve")
    words = dict(lex.numbers.get('en', {}), **lex.numbers.get(code, {}))
    for tok in TOKEN_RE.findall(frag):
        n = int(tok) if tok.isdigit() and len(tok) <= 2 else words.get(tok)
        if n is not None:
            return 'child' if n < 13 else 'teen' if n < 18 else 'adult'
    return None


def _name_entries(names, lex):
    out = []
    for nm in names:
        if not isinstance(nm, str) or not nm.strip():
            continue
        _, low = prepare(nm, lex)
        toks = [m.group(0) for m in TOKEN_RE.finditer(low)]
        if not toks or (len(toks) == 1 and len(toks[0]) < 2):
            continue
        if len(toks) == 1 and HAS_CJK_RE.search(toks[0]) is None and toks[0] in lex.common_names:
            out.append((tuple(toks), True))
        else:
            out.append((tuple(toks), False))
        first = toks[0]
        if len(toks) > 1 and len(first) >= 3 and first not in lex.common_names and not first.isdigit():
            out.append(((first,), True))
    return list(dict.fromkeys(out))


# ------------------------------------------------------------------ world walk
class _Char:
    __slots__ = ('key', 'id', 'name', 'raw_age', 'declared', 'age_ok', 'inferred', 'age', 'texts', 'path', 'where',
                 'kind', 'rec', 'level')

    def __init__(self, key, cid, rec, path, where, kind, level):
        self.key, self.id, self.rec, self.path, self.where, self.kind, self.level = key, cid, rec, path, where, kind, level
        self.name = rec.get('name') if isinstance(rec.get('name'), str) else None
        self.raw_age = rec.get('age', rec.get('age_years', rec.get('age_group')))
        self.texts = []
        for k in OWN_TEXT_KEYS:
            v = rec.get(k)
            if isinstance(v, str):
                self.texts.append((k, v))
            elif isinstance(v, list):
                self.texts += [(f'{k}[{i}]', x) for i, x in enumerate(v) if isinstance(x, str)]
        self.declared, self.age_ok, self.inferred, self.age = None, True, None, 'adult'


def _lv_where(li):
    return 'the world' if li == 0 else f'level {li + 1}'


def _lv_path(li):
    return 'world' if li == 0 else f'levels[{li - 1}]'


class _Scan:
    def __init__(self, world, lex, declared):
        self.w, self.lex, self.declared = world, lex, tuple(declared)
        self.fields = []          # (path, where, text, ctx)  ctx: {'dialog', 'quest', 'char', 'script', 'graph', 'id'}
        self.chars = []
        self.by_id = {}
        self.contexts = {}        # context key -> {'participants': set(char keys), 'fields': [field idx], 'where'}
        self.findings = []
        self.dialog_owner = {}    # dialog id -> set(char keys)
        self.data_checks = []     # deferred data rules (need effective ages)

    # -- fields
    def field(self, path, where, text, ctx=None, is_id=False):
        if not isinstance(text, str) or not text.strip():
            return
        if is_id:
            text = re.sub(r'[_.\-]+', ' ', text)
        self.fields.append((path, where, text, ctx or {}))
        for k in ('dialog', 'quest', 'char', 'script', 'graph'):
            if ctx and ctx.get(k):
                self.contexts.setdefault((k, ctx[k]), {'participants': set(), 'fields': [], 'where': where})
                self.contexts[(k, ctx[k])]['fields'].append(len(self.fields) - 1)

    def generic(self, obj, path, where, ctx=None, depth=0):
        if depth > 12:
            return
        if isinstance(obj, str):
            self.field(path, where, obj, ctx)
        elif isinstance(obj, dict):
            for k, v in obj.items():
                if not isinstance(k, str):
                    continue
                if k not in SCHEMA_KEYS:                    # user-chosen keys (ids) are authored text too
                    self.field(f'{path}.{k}#key', where, k, ctx, is_id=True)
                if k in SKIP_KEYS:
                    continue
                if k in ID_KEYS and isinstance(v, str):
                    self.field(f'{path}.{k}', where, v, ctx, is_id=True)
                    continue
                self.generic(v, f'{path}.{k}', where, ctx, depth + 1)
        elif isinstance(obj, list):
            for i, v in enumerate(obj[:5000]):
                self.generic(v, f'{path}[{i}]', where, ctx, depth + 1)

    # -- characters
    def char(self, key, cid, rec, path, where, kind, level):
        c = _Char(key, cid, rec, path, where, kind, level)
        self.chars.append(c)
        if cid:
            self.by_id.setdefault(str(cid), []).append(c)
        return c

    def walk(self):
        w = self.w
        levels = [w] + [L for L in (w.get('levels') or []) if isinstance(L, dict)]
        for li, L in enumerate(levels):
            lp, lw = _lv_path(li), _lv_where(li)
            for k in ('name', 'story', 'author', 'title', 'desc', 'intro', 'outro', 'credits'):
                if isinstance(L.get(k), str):
                    self.field(f'{lp}.{k}', f'{lw}: {k}', L[k])
            for i, o in enumerate(L.get('objects') or []):
                if not isinstance(o, dict):
                    continue
                op = f'{lp}.objects[{i}]'
                t = o.get('t')
                label = o.get('name') or o.get('id') or t
                ow = f'{lw}: {t} "{label}"'
                is_char = t in ('npc', 'enemy') or 'age' in o or 'age_years' in o
                c = self.char(f'obj:{li}:{i}', o.get('id'), o, op, ow, t or 'object', li) if is_char else None
                ctx = {'char': c.key} if c else {}
                for k, v in o.items():
                    if k in SKIP_KEYS or k in ('x', 'z', 'y', 's', 'rot', 'hp', 'n', 'gold', 'patrol'):
                        continue
                    if k in ID_KEYS and isinstance(v, str):
                        self.field(f'{op}.{k}', ow, v, ctx, is_id=True)
                    else:
                        self.generic(v, f'{op}.{k}', ow, ctx)
                if c and isinstance(o.get('dialog'), str):
                    self.dialog_owner.setdefault(o['dialog'], set()).add(c.key)
            for iid, it in (L.get('items') or {}).items() if isinstance(L.get('items'), dict) else []:
                self.field(f'{lp}.items.{iid}#key', f'{lw}: item "{iid}"', iid, is_id=True)
                self.generic(it, f'{lp}.items.{iid}', f'{lw}: item "{iid}"')
            for qi, q in enumerate(L.get('quests') or []):
                if not isinstance(q, dict):
                    continue
                qid = str(q.get('id') or f'{li}:{qi}')
                qw = f'{lw}: quest "{q.get("title") or qid}"'
                self.generic(q, f'{lp}.quests[{qi}]', qw, {'quest': qid})
                ctxp = self.contexts.setdefault(('quest', qid), {'participants': set(), 'fields': [], 'where': qw})
                ctxp.setdefault('ids', set()).update(x for x in [q.get('giver')] if isinstance(x, str))
                for s in q.get('steps') or []:
                    if isinstance(s, dict):
                        for k in ('npc', 'id'):
                            if isinstance(s.get(k), str):
                                ctxp['ids'].add(s[k])
                        if s.get('type') == 'defeat' and isinstance(s.get('id'), str):
                            self.data_checks.append(('defeat', s['id'], f'{lp}.quests[{qi}]', qw))
            dialogs = L.get('dialogs') if isinstance(L.get('dialogs'), dict) else {}
            for did, d in dialogs.items():
                dw = f'{lw}: dialogue "{did}"'
                dp = f'{lp}.dialogs.{did}'
                self.generic(d, dp, dw, {'dialog': str(did)})
                self.contexts.setdefault(('dialog', str(did)), {'participants': set(), 'fields': [], 'where': dw})
                if isinstance(d, dict):
                    for nid, n in (d.get('nodes') or {}).items() if isinstance(d.get('nodes'), dict) else []:
                        if not isinstance(n, dict):
                            continue
                        if isinstance(n.get('speaker'), str):
                            self.contexts[('dialog', str(did))].setdefault('speakers', set()).add(n['speaker'].strip().lower())
                        acts = [n.get('do')] + [c for c in (n.get('choices') or []) if isinstance(c, dict)]
                        for a in acts:
                            if isinstance(a, dict):
                                self.data_checks.append(('action', str(did), a, f'{dp}.nodes.{nid}', f'{dw}, line "{nid}"'))
            known = {'name', 'story', 'author', 'title', 'desc', 'intro', 'outro', 'credits', 'objects', 'items', 'quests',
                     'dialogs', 'levels', 'systems', 'child_safety'}
            for k, v in L.items():
                if k not in known and k not in SKIP_KEYS and k not in ('seed', 'size', 'terrain', 'water', 'day', 'spawn',
                                                                         'player', 'difficulty', 'sfx'):
                    self.generic(v, f'{lp}.{k}', f'{lw}: {k}')
            if isinstance(L.get('player'), dict):
                self.generic({k: v for k, v in L['player'].items() if k not in ('hp', 'col', 'items')}, f'{lp}.player',
                             f'{lw}: player')
        self.walk_systems(w.get('systems'))

    def walk_systems(self, sy):
        if not isinstance(sy, dict):
            return
        chars = sy.get('characters')
        items = chars.items() if isinstance(chars, dict) else (
            [(c.get('id', str(i)), c) for i, c in enumerate(chars) if isinstance(c, dict)] if isinstance(chars, list) else [])
        for cid, rec in items:
            if not isinstance(rec, dict):
                continue
            cp, cw = f'systems.characters.{cid}', f'character "{rec.get("name") or cid}"'
            c = self.char(f'char:{cid}', str(cid), rec, cp, cw, 'character', None)
            self.field(f'{cp}#key', cw, str(cid), {'char': c.key}, is_id=True)
            for k, v in rec.items():
                if k in SKIP_KEYS:
                    continue
                if k in ID_KEYS and isinstance(v, str):
                    self.field(f'{cp}.{k}', cw, v, {'char': c.key}, is_id=True)
                else:
                    self.generic(v, f'{cp}.{k}', cw, {'char': c.key})
            if isinstance(rec.get('dialog'), str):
                self.dialog_owner.setdefault(rec['dialog'], set()).add(c.key)
        for i, ent in enumerate(sy.get('entities') or []):
            if not isinstance(ent, dict):
                continue
            ep, ew = f'systems.entities[{i}]', f'entity "{ent.get("name") or ent.get("id") or ent.get("kind") or i}"'
            is_char = str(ent.get('kind', '')).lower() in ('npc', 'enemy', 'animal', 'player', 'character') or 'age' in ent \
                or 'age_years' in ent
            c = self.char(f'ent:{i}', ent.get('id'), ent, ep, ew, str(ent.get('kind') or 'entity'), None) if is_char else None
            ctx = {'char': c.key} if c else {}
            for k, v in ent.items():
                if k in SKIP_KEYS or k in ('x', 'z', 'y', 's', 'rot', 'hp', 'n', 'gold', 'patrol'):
                    continue
                if k in ID_KEYS and isinstance(v, str):
                    self.field(f'{ep}.{k}', ew, v, ctx, is_id=True)
                else:
                    self.generic(v, f'{ep}.{k}', ew, ctx)
            if c and isinstance(ent.get('dialog'), str):
                self.dialog_owner.setdefault(ent['dialog'], set()).add(c.key)
        for i, sc in enumerate(sy.get('scripts') or []):
            if not isinstance(sc, dict):
                continue
            sid = str(sc.get('id') or i)
            sp, sw = f'systems.scripts[{i}]', f'script "{sid}"'
            ctx = {'script': sid}
            self.contexts.setdefault(('script', sid), {'participants': set(), 'fields': [], 'where': sw,
                                                       'ids': {sc['entity']} if isinstance(sc.get('entity'), str) else set()})
            src = sc.get('source') if isinstance(sc.get('source'), str) else ''
            for j, lit in enumerate(script_strings(src)):
                self.field(f'{sp}.source#string{j}', sw, lit, ctx)
            for k, v in sc.items():
                if k not in ('source', 'entity', 'id'):
                    self.generic(v, f'{sp}.{k}', sw, ctx)
            self.data_checks.append(('script', src, sp, sw))
        for i, g in enumerate(sy.get('graphs') or []):
            if not isinstance(g, dict):
                continue
            gid = str(g.get('id') or i)
            gp, gw = f'systems.graphs[{i}]', f'node graph "{gid}"'
            ctx = {'graph': gid}
            self.contexts.setdefault(('graph', gid), {'participants': set(), 'fields': [], 'where': gw,
                                                      'ids': {g['entity']} if isinstance(g.get('entity'), str) else set()})
            for j, n in enumerate(g.get('nodes') or []):
                if isinstance(n, dict):
                    self.generic(n.get('params'), f'{gp}.nodes[{j}].params', f'{gw}, node "{n.get("id", j)}"', ctx)
                    self.data_checks.append(('node', n, f'{gp}.nodes[{j}]', f'{gw}, node "{n.get("id", j)}"'))
        for k, v in sy.items():
            if k in ('characters', 'scripts', 'graphs', 'safety', 'entities'):
                continue
            self.generic(v, f'systems.{k}', f'systems: {k}')
            if k in ('romance', 'relationships', 'romances', 'dating'):
                self.data_checks.append(('romance', v, f'systems.{k}', f'systems: {k}'))
            if k == 'ai' and isinstance(v, dict):
                for sq_id, sq in (v.get('squads') or {}).items() if isinstance(v.get('squads'), dict) else []:
                    if isinstance(sq, dict):
                        self.data_checks.append(('squad', sq, f'systems.ai.squads.{sq_id}', f'squad "{sq_id}"'))
                for cap in (v.get('capture') or []) if isinstance(v.get('capture'), list) else []:
                    self.data_checks.append(('capture', cap, 'systems.ai.capture', 'systems: ai capture'))

    # -- ages
    def resolve_ages(self):
        lex = self.lex
        for c in self.chars:
            age, ok = normalize_age(c.raw_age, lex)
            c.declared, c.age_ok = age, ok
            if not ok:
                self.findings.append(_finding('CS015', c.path + '.age', c.where,
                                              f'Age "{c.raw_age}" is not child, teen or adult. Set one of those three '
                                              '(or a number of years).'))
            texts = [t for _, t in c.texts]
            inf = infer_age(texts, lex, self.declared)
            c.inferred = inf
            if age in MINOR_AGES:
                c.age = age
            elif inf:
                c.age = inf
                self.findings.append(_finding('CS012', c.path, c.where,
                                              f'"{c.name or c.id}" is described as a {inf} but tagged '
                                              f'{age or "adult (default)"}. It is treated as a {inf}: immune to harm, '
                                              'never romanced. Set its age group to make this explicit.'))
            elif not ok:
                c.age = 'child'           # unknown age value: fail safe until the creator fixes it
            else:
                c.age = age or 'adult'

    def minors(self):
        return [c for c in self.chars if c.age in MINOR_AGES]

    def is_minor_id(self, cid):
        return any(c.age in MINOR_AGES for c in self.by_id.get(str(cid), ()))

    # -- rules
    def run(self):
        lex = self.lex
        self.walk()
        self.resolve_ages()
        minors = self.minors()
        name_entries = NameIndex(_name_entries([c.name for c in minors if c.name], lex))
        minor_keys = {c.key for c in minors}
        # participants of each context
        for (kind, cid), cx in self.contexts.items():
            parts = cx['participants']
            if kind == 'dialog':
                parts |= self.dialog_owner.get(cid, set())
                sp = cx.get('speakers', set())
                parts |= {c.key for c in self.chars if c.name and c.name.strip().lower() in sp}
            for i in cx.get('ids', ()):
                parts |= {c.key for c in self.by_id.get(str(i), ())}
            if kind == 'char':
                parts.add(cid)
        analyses = []
        for idx, (path, where, text, ctx) in enumerate(self.fields):
            ans = analyse(text, lex, name_entries, self.declared)
            analyses.append(ans)
            self.findings += text_findings(ans, path, where, lex)
        # context rules
        for (kind, cid), cx in self.contexts.items():
            if not (cx['participants'] & minor_keys):
                continue
            who = sorted({c.name or c.id or c.key for c in self.chars if c.key in cx['participants'] and c.key in minor_keys})
            for idx in cx['fields']:
                path, where, text, ctx = self.fields[idx]
                for an in analyses[idx]:
                    strong = an.cats('sex_strong')
                    weak = an.cats('sex_weak')
                    if kind == 'char':
                        if strong or weak:
                            self.findings.append(_finding('CS005', path, where,
                                                          f'{_q(h[2] for h in strong + weak)} in the own text of '
                                                          f'{_q(who)} (a minor). Rewrite it.',
                                                          {'sexual': [h[2] for h in strong + weak]}))
                        continue
                    if strong:
                        self.findings.append(_finding('CS004', path, where,
                                                      f'{_q(h[2] for h in strong)} appears in a {kind} that involves '
                                                      f'{_q(who)} (a minor). Remove it.',
                                                      {'sexual': [h[2] for h in strong]}))
                    elif weak:
                        self.findings.append(_finding('CS104', path, where,
                                                      f'{_q(h[2] for h in weak)} appears in a {kind} that involves '
                                                      f'{_q(who)} (a minor). Check it is not about them.',
                                                      {'sexual': [h[2] for h in weak]}))
        self.data_rules()
        return self.report()

    def data_rules(self):
        F = self.findings
        for c in self.minors():
            rec = c.rec
            if c.kind == 'enemy' or str(rec.get('role', '')).lower() in HOSTILE_ROLES or rec.get('hostile') is True \
                    or str(rec.get('team', '')).lower() in HOSTILE_ROLES or str(rec.get('ai', '')).lower() in ('hostile', 'aggressive'):
                F.append(_finding('CS007', c.path, c.where, f'"{c.name or c.id}" is a {c.age} and cannot be an enemy or '
                                                            'fight. Make it a friendly character.'))
            for k in ('romanceable', 'romance', 'dateable', 'marriageable', 'romance_option'):
                if rec.get(k) not in (None, False, 0, '', 'no', 'none'):
                    F.append(_finding('CS006', f'{c.path}.{k}', c.where, f'"{c.name or c.id}" is a {c.age}: children '
                                                                         'and teens are never romance options.'))
            for k in ('outfit', 'appearance', 'outfits', 'clothing', 'look'):
                if _nude(rec.get(k)):
                    F.append(_finding('CS010', f'{c.path}.{k}', c.where, f'"{c.name or c.id}" is a {c.age}: no nudity, '
                                                                         'underwear-only or undressed looks.'))
            if rec.get('capturable') or rec.get('kidnappable'):
                F.append(_finding('CS011', c.path, c.where, f'"{c.name or c.id}" is a {c.age} and cannot be captured.'))
        for chk in self.data_checks:
            kind = chk[0]
            if kind == 'defeat' and self.is_minor_id(chk[1]):
                F.append(_finding('CS007', chk[2], chk[3], f'This quest asks the player to defeat "{chk[1]}", a child '
                                                           'or teen character. Children can never be combat targets.'))
            elif kind == 'action':
                _, did, act, path, where = chk
                owners = self.dialog_owner.get(did, set())
                for key, val in act.items():
                    k = str(key).lower()
                    rule = ('CS006' if k in ROMANCE_KEYS else 'CS010' if k in UNDRESS_KEYS else
                            'CS011' if k in CAPTURE_KEYS else 'CS007' if k in HARM_KEYS else None)
                    if not rule:
                        continue
                    targets = _targets(val)
                    hit = [t for t in targets if t in ('self', 'npc', True) and owners & {c.key for c in self.minors()}]
                    hit += [t for t in targets if isinstance(t, str) and t not in ('self', 'npc', 'player') and self.is_minor_id(t)]
                    if hit:
                        F.append(_finding(rule, f'{path}.{key}', where, f'Dialogue action "{key}" targets a child or teen '
                                                                     'character. That is never allowed. Remove it.'))
            elif kind == 'script':
                _, src, path, where = chk
                clean = _strip_comments(src)
                for m in SCRIPT_HARM_RE.finditer(clean):
                    fn, ids = m.group(1), [m.group(3), m.group(5)]
                    if fn == 'start' and 'romance' not in clean[max(0, m.start() - 12):m.start() + 8]:
                        continue
                    if any(i and self.is_minor_id(i) for i in ids):
                        F.append(_finding('CS014', path, where, f'The script calls {fn}() on a child or teen character '
                                                                f'("{next(i for i in ids if i and self.is_minor_id(i))}"). '
                                                                'The runtime would refuse it; remove the call.'))
                for m in SCRIPT_FIND_HARM_RE.finditer(clean):
                    if self.is_minor_id(m.group(2)):
                        F.append(_finding('CS014', path, where, f'The script calls {m.group(3)}() on "{m.group(2)}", a '
                                                                'child or teen character. Remove the call.'))
            elif kind == 'node':
                _, n, path, where = chk
                if str(n.get('type')) in HARM_NODE_TYPES:
                    p = n.get('params') if isinstance(n.get('params'), dict) else {}
                    ids = [p.get(k) for k in ('target', 'entity', 'a', 'b', 'with', 'npc', 'who')]
                    if n.get('type') == 'appearance.setOutfit' and not _nude(p.get('outfit')):
                        continue
                    if n.get('type') == 'entity.setHostile' and p.get('value') is False:
                        continue
                    if any(isinstance(i, str) and self.is_minor_id(i) for i in ids):
                        F.append(_finding('CS014', path, where, f'Node "{n.get("type")}" targets a child or teen '
                                                                'character. Remove it or change the target.'))
            elif kind == 'romance':
                _, v, path, where = chk
                for t in _romance_ids(v, not path.endswith('relationships')):
                    if self.is_minor_id(t):
                        F.append(_finding('CS006', path, where, f'"{t}" is a child or teen character and cannot be part '
                                                                'of romance or relationship data.'))
                        break
            elif kind == 'squad':
                _, sq, path, where = chk
                for mid in sq.get('members') or []:
                    if isinstance(mid, str) and self.is_minor_id(mid):
                        F.append(_finding('CS007', path, where, f'"{mid}" is a child or teen character and cannot be in '
                                                                'a combat squad.'))
            elif kind == 'capture':
                _, cap, path, where = chk
                for t in _targets(cap):
                    if isinstance(t, str) and self.is_minor_id(t):
                        F.append(_finding('CS011', path, where, f'"{t}" is a child or teen character and cannot be '
                                                                'captured.'))

    def report(self):
        uniq, seen = [], set()
        for f in self.findings:
            k = (f['rule'], f['path'], f['message'])
            if k not in seen:
                seen.add(k)
                uniq.append(f)
        uniq.sort(key=lambda f: (f['severity'] != 'block', f['rule'], f['path']))
        blocks = sum(1 for f in uniq if f['severity'] == 'block')
        return {'policy': POLICY, 'policy_version': POLICY_VERSION, 'lexicon_version': self.lex.version,
                'blocked': blocks > 0, 'findings': uniq,
                'counts': {'block': blocks, 'warn': len(uniq) - blocks, 'texts': len(self.fields),
                           'characters': len(self.chars), 'minors': len(self.minors())},
                'characters': [{'key': c.key, 'id': c.id, 'name': c.name, 'age': c.age, 'declared': c.declared,
                                'inferred': c.inferred, 'path': c.path} for c in self.chars]}


def _nude(v):
    if isinstance(v, str):
        return v.strip().lower() in NUDE_OUTFITS
    if isinstance(v, dict):
        if v.get('nude') is True or v.get('naked') is True or v.get('undressed') is True:
            return True
        cov = str(v.get('coverage', '')).lower()
        return cov in ('none', 'underwear', 'partial', 'minimal') or _nude(v.get('outfit')) or _nude(v.get('name'))
    if isinstance(v, list):
        return any(_nude(x) for x in v)
    return False


def _targets(v):
    if v is True:
        return [True]
    if isinstance(v, str):
        return [v]
    if isinstance(v, (int, float)):
        return []
    if isinstance(v, list):
        return [t for x in v for t in _targets(x)]
    if isinstance(v, dict):
        out = []
        for k in ('target', 'with', 'npc', 'id', 'who', 'entity', 'a', 'b', 'partner', 'char', 'character'):
            if k in v:
                out += _targets(v[k])
        return out
    return []


def _romance_ids(v, romantic=True, depth=0):
    """Character ids inside romance data. systems.romance: every id counts. systems.relationships: only entries
    whose type is romantic (a parent/child or sibling relationship is family data, not romance)."""
    out = []
    if depth > 8:
        return out
    if isinstance(v, dict):
        t = v.get('type', v.get('kind', v.get('relation')))
        here = romantic if t is None else str(t).lower() in ROMANTIC_RELATIONS
        for k, x in v.items():
            if here and isinstance(x, str) and k not in ('name', 'text', 'desc', 'title', 'type', 'kind', 'relation'):
                out.append(x)
            elif here and isinstance(k, str) and isinstance(x, (dict, list)) and depth <= 1 and t is None:
                out.append(k)
            if isinstance(x, (dict, list)):
                out += _romance_ids(x, here, depth + 1)
    elif isinstance(v, list):
        for x in v:
            if isinstance(x, str):
                if romantic:
                    out.append(x)
            else:
                out += _romance_ids(x, romantic, depth + 1)
    return out


# ------------------------------------------------------------------ scripts
def _strip_comments(src):
    out, i, n = [], 0, len(src or '')
    while i < n:
        c = src[i]
        if c in '\'"`':
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == '\\' else 1
            out.append(src[i:j + 1])
            i = j + 1
        elif src.startswith('//', i):
            j = src.find('\n', i)
            i = n if j < 0 else j
        elif src.startswith('/*', i):
            j = src.find('*/', i + 2)
            i = n if j < 0 else j + 2
        else:
            out.append(c)
            i += 1
    return ''.join(out)


def script_strings(src):
    """String literals (and template-literal text) of a StarkScript / JavaScript source, plus comments' text."""
    out, i, n = [], 0, len(src or '')
    esc = {'n': '\n', 't': '\t', 'r': '', '0': ''}
    while i < n:
        c = src[i]
        if c in '\'"`':
            j, buf = i + 1, []
            while j < n and src[j] != c:
                if src[j] == '\\' and j + 1 < n:
                    nx = src[j + 1]
                    if nx == 'u' and re.match(r'[0-9a-fA-F]{4}', src[j + 2:j + 6]):
                        buf.append(chr(int(src[j + 2:j + 6], 16)))
                        j += 6
                        continue
                    buf.append(esc.get(nx, nx))
                    j += 2
                    continue
                if c == '`' and src.startswith('${', j):
                    depth, j = 1, j + 2
                    while j < n and depth:
                        depth += {'{': 1, '}': -1}.get(src[j], 0)
                        j += 1
                    buf.append(' ')
                    continue
                buf.append(src[j])
                j += 1
            s = ''.join(buf)
            if s.strip():
                out.append(s)
            i = j + 1
        elif src.startswith('//', i):
            j = src.find('\n', i)
            j = n if j < 0 else j
            out.append(src[i + 2:j])
            i = j
        elif src.startswith('/*', i):
            j = src.find('*/', i + 2)
            j = n if j < 0 else j
            out.append(src[i + 2:j])
            i = j + 2
        else:
            i += 1
    return [s for s in out if s.strip()]


# ------------------------------------------------------------------ public API
def _declared_langs(world):
    sy = world.get('systems') if isinstance(world, dict) else None
    loc = sy.get('locale') if isinstance(sy, dict) else None
    langs = loc.get('languages') if isinstance(loc, dict) else None
    return [str(x).lower()[:2] for x in langs] if isinstance(langs, list) else []


def scan_world(world, lex=None):
    """-> report dict: {'blocked', 'findings': [...], 'counts', 'characters': [...]} (see CHILD_SAFETY.md)."""
    if not isinstance(world, dict):
        raise ValueError('world must be an object')
    lex = lex or lexicon()
    return _Scan(world, lex, _declared_langs(world)).run()


def tag_world(world, report=None, generated=False):
    """-> a deep copy where every character (npc / enemy objects, systems.characters) has an explicit age group.
    Characters treated as minors by inference get age_inferred: true. generated=True: Make-me-a-game output, whose
    child characters carry role 'child' (tagged child)."""
    w = copy.deepcopy(world)
    if generated:
        for L in [w] + [x for x in (w.get('levels') or []) if isinstance(x, dict)]:
            for o in L.get('objects') or []:
                if isinstance(o, dict) and o.get('t') == 'npc' and str(o.get('role', '')).lower() in ('child', 'kid'):
                    o['age'] = 'child'
        sy = w.get('systems')
        if isinstance(sy, dict) and isinstance(sy.get('characters'), dict):
            for c in sy['characters'].values():
                if isinstance(c, dict) and str(c.get('role', '')).lower() in ('child', 'kid'):
                    c['age'] = 'child'
    report = report or scan_world(w)
    ages = {c['key']: c for c in report['characters']}
    levels = [w] + [x for x in (w.get('levels') or []) if isinstance(x, dict)]
    for li, L in enumerate(levels):
        for i, o in enumerate(L.get('objects') or []):
            c = ages.get(f'obj:{li}:{i}')
            if c and isinstance(o, dict):
                o['age'] = c['age']
                if c['inferred'] and c['declared'] not in MINOR_AGES:
                    o['age_inferred'] = True
    sy = w.get('systems')
    chars = sy.get('characters') if isinstance(sy, dict) else None
    if isinstance(chars, dict):
        for cid, rec in chars.items():
            c = ages.get(f'char:{cid}')
            if c and isinstance(rec, dict):
                rec['age'] = c['age']
                if c['inferred'] and c['declared'] not in MINOR_AGES:
                    rec['age_inferred'] = True
    elif isinstance(chars, list):
        for i, rec in enumerate(chars):
            if isinstance(rec, dict):
                c = ages.get(f'char:{rec.get("id", str(i))}')
                if c:
                    rec['age'] = c['age']
    for i, ent in enumerate((sy.get('entities') or []) if isinstance(sy, dict) else []):
        c = ages.get(f'ent:{i}')
        if c and isinstance(ent, dict):
            ent['age'] = c['age']
            if c['inferred'] and c['declared'] not in MINOR_AGES:
                ent['age_inferred'] = True
    return w


def enforce_export(world):
    """The export gate. -> the world to export (explicit ages + a child_safety stamp), or raise ChildSafetyError."""
    report = scan_world(world)
    if report['blocked']:
        raise ChildSafetyError(report)
    w = tag_world(world, report)
    w['child_safety'] = {'policy': POLICY, 'policy_version': POLICY_VERSION, 'lexicon_version': report['lexicon_version'],
                         'minors': sorted({c['id'] or c['key'] for c in report['characters'] if c['age'] in MINOR_AGES}),
                         'warnings': report['counts']['warn'],
                         'note': 'informational; every runtime re-applies the rules and never trusts this block'}
    return w


def summary(report, limit=300):
    """One short line for the studio's error box."""
    c = report['counts']
    if not report['blocked']:
        return f'Child-safety check passed ({c["warn"]} warning{"s" if c["warn"] != 1 else ""}).'
    first = next(f for f in report['findings'] if f['severity'] == 'block')
    msg = (f'Export blocked by the child-safety rules ({c["block"]} problem{"s" if c["block"] != 1 else ""}). '
           f'{first["where"]}: {first["title"]}. Open the Safety panel for the full list.')
    return msg[:limit]


def explain(report):
    """Every finding as readable lines (for a console, a log or the Safety panel)."""
    lines = [summary(report, 10000)]
    for f in report['findings']:
        lines.append(f'[{f["severity"].upper()} {f["rule"]}] {f["where"]} ({f["path"]})')
        lines.append(f'    {f["message"]}')
        if f.get('excerpt'):
            lines.append(f'    in: "{f["excerpt"]}"')
    return '\n'.join(lines)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description='Stark World Engine child-safety check for a world.json')
    ap.add_argument('world')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)
    with open(a.world, encoding='utf-8') as f:
        world = json.load(f)
    rep = scan_world(world)
    print(json.dumps(rep, indent=1, ensure_ascii=False) if a.json else explain(rep))
    return 1 if rep['blocked'] else 0


if __name__ == '__main__':
    sys.exit(main())
