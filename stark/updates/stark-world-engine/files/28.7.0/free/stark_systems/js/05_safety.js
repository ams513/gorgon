/* Stark World Engine Systems runtime: CHILD SAFETY (hard rule; nothing here can be switched off by a world, a script or a
   setting). Port of stark_systems/child_safety.py driven by the same safety/lexicon.json, plus the runtime guards:
   child and teen characters are immune to damage, death, capture and combat targeting, and are excluded from
   romance, undressing and harm actions. Ages are locked when an entity is created. Spec: CHILD_SAFETY.md. */
(function (G) {
  'use strict';
  var S = G.safety = {};
  S.POLICY = 'stark-child-safety';
  S.POLICY_VERSION = 1;
  S.AGE_GROUPS = ['child', 'teen', 'adult'];
  var MINOR = { child: 1, teen: 1 };

  class SafetyError extends Error {
    constructor(message, info) { super(message); this.name = 'SafetyError'; this.safety = true; this.info = info || null; }
  }
  S.SafetyError = SafetyError;

  var RULES = S.RULES = {
    CS001: ['block', 'a term that refers to sexual content involving minors'],
    CS002: ['block', 'sexual or nudity wording in the same sentence as a reference to a minor'],
    CS003: ['block', 'sexual wording in the same text as a reference to a minor'],
    CS004: ['block', 'sexual wording in a conversation, quest or script that involves a child or teen character'],
    CS005: ['block', "sexualised or nudity wording in a child or teen character's own name or description"],
    CS006: ['block', 'a child or teen character in romance or intimacy data'],
    CS007: ['block', 'a child or teen character set up as an enemy or combat target'],
    CS008: ['block', 'abuse wording connected to a minor'],
    CS009: ['block', 'violence directed at a minor'],
    CS010: ['block', 'nudity or undressing data for a child or teen character'],
    CS011: ['block', 'a child or teen character set up to be captured'],
    CS014: ['block', 'a script or node graph that would harm, capture, romance or undress a child or teen character'],
    CS015: ['block', 'an age group that is not child, teen or adult'],
    CS012: ['warn', 'described as a child or teen but not tagged as one: treated as a minor'],
    CS013: ['warn', 'sexual wording next to "girl" or "boy": every character involved must be an adult'],
    CS104: ['warn', 'romantic or nudity wording in a conversation or quest that involves a child or teen character'],
    CS109: ['warn', 'mentions past violence against a minor (allowed as backstory; the game can never show it)']
  };
  function set(a) { var o = Object.create(null); a.forEach(function (k) { o[k] = 1; }); return o; }
  var ROMANCE_KEYS = set(['romance', 'romance_start', 'start_romance', 'intimacy', 'intimate', 'date', 'marry', 'propose', 'seduce', 'flirt', 'kiss', 'affinity_romance', 'love']);
  var UNDRESS_KEYS = set(['undress', 'strip', 'nude', 'nudity', 'remove_clothes', 'unclothe']);
  var CAPTURE_KEYS = set(['capture', 'kidnap', 'abduct', 'imprison', 'enslave', 'cage', 'jail', 'arrest', 'bind']);
  var HARM_KEYS = set(['damage', 'harm', 'hurt', 'kill', 'attack', 'execute', 'sacrifice', 'torture', 'punish', 'hit', 'slap', 'whip', 'beat', 'wound', 'poison', 'burn', 'stun']);
  var ROMANTIC_REL = set(['romance', 'romantic', 'lover', 'lovers', 'partner', 'spouse', 'married', 'marriage', 'dating', 'date', 'betrothed', 'engaged', 'intimate', 'crush', 'fling', 'affair']);
  var NUDE_OUTFITS = set(['none', 'nude', 'naked', 'underwear', 'lingerie', 'topless', 'bare', 'undressed', 'nothing']);
  var HOSTILE_ROLES = set(['enemy', 'hostile', 'boss', 'monster', 'raider', 'bandit', 'target', 'combatant', 'foe']);
  var HARM_NODES = set(['combat.damage', 'combat.kill', 'combat.attack', 'combat.applyStatus', 'entity.kill', 'entity.damage', 'entity.setHostile', 'ai.setTarget', 'ai.attack', 'ai.capture', 'romance.start', 'romance.addAffinity', 'appearance.undress', 'appearance.setOutfit']);
  S.HARM_NODES = HARM_NODES;
  var SKIP_KEYS = set(['t', 'kind', 'mode', 'mood', 'weather', 'col', 'color', 'colour', 'tiles', 'sprite', 'music', 'slot', 'wave', 'age', 'type', 'shape', 'icon', 'font', 'anchor', 'align', 'bind', 'src', 'path', 'file', 'url', 'version', 'genre', 'controller', 'rig', 'view', 'formation', 'tactic', 'easing', 'blend', 'op', 'event', 'events_on', 'stat', 'lang', 'languages', 'policy', 'source', 'nodes_layout', 'layout', 'ease', 'key_code', 'input', 'axis', 'button', 'sfx', 'cue', 'channel', 'material', 'mesh', 'texture', 'model', 'anim', 'animation', 'pose', 'curve', 'operator', 'cmp', 'unit', 'units', 'format_id', 'damage_type', 'ammo_type', 'surface', 'biome', 'terrain_kind']);
  var ID_KEYS = set(['id', 'start', 'goto', 'next', 'node', 'dialog', 'giver', 'npc', 'item', 'flag', 'has', 'lacks', 'done', 'active', 'notStarted', 'complete', 'advance', 'take', 'give', 'entity', 'target', 'faction', 'shop', 'recipe', 'quest', 'from', 'to', 'out', 'in', 'lock', 'key', 'with', 'a', 'b', 'owner', 'station', 'behavior', 'behaviour', 'schedule', 'squad', 'loot', 'table', 'weapon', 'ability', 'perk', 'skill', 'tree', 'requires', 'unlock', 'graph', 'script', 'zone', 'region', 'template', 'pool', 'char', 'character', 'leader', 'members', 'candidates', 'partner', 'spawn']);
  var OWN_TEXT_KEYS = ['name', 'title', 'desc', 'description', 'bio', 'backstory', 'text', 'greeting', 'label', 'lines', 'appearance_text', 'look', 'traits', 'personality', 'job', 'role_text', 'epithet', 'nickname'];
  var SCHEMA_KEYS = set(Object.keys(SKIP_KEYS).concat(Object.keys(ID_KEYS), OWN_TEXT_KEYS, ['x', 'y', 'z', 's', 'rot', 'hp', 'n', 'gold', 'patrol', 'speaker', 'choices', 'nodes', 'branches', 'if', 'do', 'end', 'steps', 'reward', 'xp', 'items', 'count', 'r', 'level', 'consume', 'auto', 'final', 'objects', 'quests', 'dialogs', 'levels', 'story', 'author', 'difficulty', 'seed', 'size', 'water', 'day', 'len', 'spawn', 'player', 'heal', 'atk', 'def', 'speed', 'min', 'max', 'value', 'values', 'params', 'links', 'weight', 'price', 'stock', 'qty', 'rate', 'cooldown', 'range', 'radius', 'duration', 'time', 'delay', 'chance', 'w', 'h', 'width', 'height', 'damage', 'knockback', 'stagger', 'cost', 'effect', 'effects', 'stats', 'state', 'states', 'tags', 'tag', 'role', 'team', 'hostile', 'romanceable', 'outfit', 'appearance', 'schedules', 'characters', 'scripts', 'graphs', 'systems', 'relations', 'reputation', 'factions', 'shops', 'recipes', 'loot_tables', 'skill_trees', 'perks', 'abilities', 'weapons', 'widgets', 'tracks', 'keys', 'entries', 'rolls', 'guaranteed', 'templates', 'pools', 'variety', 'chunk', 'lod', 'budgets', 'members', 'tiles', 'age_years', 'age_group', 'options', 'enabled', 'romance', 'romance_start', 'intimacy', 'date', 'marry', 'kiss', 'seduce', 'flirt', 'undress', 'strip', 'nude', 'capture', 'kidnap', 'imprison', 'enslave', 'damage', 'harm', 'hurt', 'kill', 'attack', 'affinity', 'inc', 'set', 'bg', 'show', 'hide', 'shop', 'craft', 'win', 'heal', 'sfx', 'music']));

  var CJK = '\\u3040-\\u30ff\\u3400-\\u4dbf\\u4e00-\\u9fff\\uf900-\\ufaff', HANG = '\\uac00-\\ud7a3';
  var TOKEN_RE = new RegExp('[' + CJK + ']|[' + HANG + ']+|(?:(?![' + CJK + HANG + '])[\\p{L}\\p{N}_])+', 'gu');
  var SENT_RE = /[.!?;\n。！？；…]+/g;
  var HAS_CJK = new RegExp('[' + CJK + HANG + ']', 'u');
  var KATA_ONLY = /^[゠-ヿ]+$/;
  var FOLD_SPECIAL = { 'ł': 'l', 'ø': 'o', 'đ': 'd', 'ß': 'ss', 'æ': 'ae', 'œ': 'oe', 'ı': 'i', 'þ': 'th', 'ð': 'd' };

  // Python-re subset -> JS RegExp (u): \w is Unicode in Python, so it becomes [\p{L}\p{N}_] here
  function pyre(p) {
    var out = '', cls = false;
    for (var i = 0; i < p.length; i++) {
      var c = p[i];
      if (c === '\\') { var n = p[i + 1]; if (n === 'w') { out += cls ? '\\p{L}\\p{N}_' : '[\\p{L}\\p{N}_]'; i++; continue; } out += c + n; i++; continue; }
      if (c === '[' && !cls) cls = true; else if (c === ']' && cls) cls = false;
      out += c;
    }
    return out;
  }
  var B = '(?<!\\w)', E = '(?!\\w)';
  function esc(s) { return s.replace(/[.*+?^${}()|[\]\\\/]/g, '\\$&'); }
  function rx(pattern, numbers, minorAlt, flags) {
    var words = Object.keys(numbers || {}).map(esc).sort(function (a, b) { return b.length - a.length; });
    var num = '(' + ['\\d{1,2}'].concat(words).join('|') + ')';
    var p = pattern.split('{B}').join(B).split('{E}').join(E).split('{NUM}').join(num);
    if (minorAlt != null) p = p.split('{MINOR}').join(minorAlt);
    return new RegExp(pyre(p), flags || 'gu');
  }
  function alt(terms) {
    var seen = {}, list = [];
    terms.forEach(function (t) { if (!seen[t]) { seen[t] = 1; list.push(t); } });
    list.sort(function (a, b) { return b.length - a.length; });
    var out = list.map(function (t) { var wild = t.charAt(t.length - 1) === '*'; if (wild) t = t.slice(0, -1); return t.split(/\s+/).filter(Boolean).map(esc).join('\\s+') + (wild ? '\\w*' : ''); });
    return out.length ? '(?:' + out.join('|') + ')' : '(?!x)x';
  }
  function fold(s) {
    s = s.normalize('NFD').replace(/\p{Mn}/gu, '');
    var o = ''; for (var i = 0; i < s.length; i++) o += FOLD_SPECIAL[s[i]] || s[i];
    return o;
  }
  function tokenize(s) { var out = [], m; TOKEN_RE.lastIndex = 0; while ((m = TOKEN_RE.exec(s))) out.push([m.index, m.index + m[0].length, m[0]]); return out; }
  function termTokens(term) {
    var toks = [], seps = [], last = null, m; TOKEN_RE.lastIndex = 0;
    while ((m = TOKEN_RE.exec(term))) { if (last !== null) { var gap = term.slice(last, m.index); seps.push(gap.indexOf("'") >= 0 ? "'" : gap.indexOf('-') >= 0 ? '-' : 's'); } toks.push(m[0]); last = m.index + m[0].length; }
    return [toks, seps];
  }
  function strip(s) { return s.replace(/^\s+|\s+$/g, ''); }

  // ---------------------------------------------------------------- lexicon
  var CATS = ['always_block', 'minor', 'teen', 'minor_weak', 'minor_time', 'sex_strong', 'sex_weak', 'abuse', 'protective'];
  function Lexicon(data) {
    var self = this;
    this.data = data;
    this.version = data.version || 1;
    this.weakWindow = +data.weak_window || 8;
    this.weakWindowCjk = +data.weak_window_cjk || 16;
    this.minStop = +data.min_stopword_hits || 2;
    var nz = data.normalize || {};
    this.zeroWidth = new RegExp(nz.zero_width || '[\\u200b]', 'gu');
    this.apos = new RegExp(nz.apostrophes || '[\\u2019]', 'gu');
    this.leet = nz.leet || {};
    this.homo = nz.homoglyphs || {};
    this.ageSyn = data.age_synonyms || {};
    this.commonNames = set(data.common_name_words || []);
    this.words = data.word_languages || {};
    this.cjk = data.cjk_languages || {};
    this.langs = Object.keys(this.words).concat(Object.keys(this.cjk));
    var owners = Object.create(null);
    Object.keys(this.words).forEach(function (code) {
      var L = self.words[code];
      CATS.forEach(function (cat) { (L[cat] || []).forEach(function (t) { if (t[0] !== '~') { var k = t.toLowerCase(); (owners[k] = owners[k] || {})[code] = 1; } }); });
    });
    this.tables = { plain: this._tables(owners, false), folded: this._tables(owners, true) };
    this.stop = {};
    Object.keys(this.words).forEach(function (code) { self.stop[code] = set(self.words[code].stopwords || []); });
    this.excCase = []; this.excNocase = [];
    Object.keys(this.words).forEach(function (code) { (self.words[code].exceptions || []).forEach(function (e) { (e.case ? self.excCase : self.excNocase).push(rx(e.re)); }); });
    this.cjkExc = [];
    Object.keys(this.cjk).forEach(function (code) { (self.cjk[code].exceptions || []).forEach(function (x) { self.cjkExc.push(x); }); });
    this.age = {};
    this.numbers = {};
    Object.keys(this.words).forEach(function (code) {
      var L = self.words[code], nums = {};
      Object.keys(L.numbers || {}).forEach(function (k) { nums[k.toLowerCase()] = L.numbers[k]; });
      self.numbers[code] = nums;
      self.age[code] = (L.age_patterns || []).map(function (p) { p = typeof p === 'string' ? { re: p } : p; return [rx(p.re, L.numbers), +p.minus || 0]; });
    });
    Object.keys(this.cjk).forEach(function (code) { self.age[code] = (self.cjk[code].age_patterns || []).map(function (p) { return [new RegExp(p, 'gu'), 0]; }); });
    var en = this.words.en || {};
    this.gradeRe = en.grade_pattern ? rx(en.grade_pattern) : null;
    this.gradeNumbers = en.grade_numbers || {};
    this.minorRes = (en.minor_patterns || []).map(function (p) { return rx(p); });
    var teens = [], kids = [];
    Object.keys(this.words).forEach(function (code) { (self.words[code].teen || []).forEach(function (t) { teens.push(t.replace(/^~/, '')); }); (self.words[code].minor || []).forEach(function (t) { kids.push(t.replace(/^~/, '')); }); });
    this.teenRe = new RegExp(pyre(B + alt(teens) + E), 'u');
    this.childRe = new RegExp(pyre(B + alt(kids) + E), 'u');
    this.cjkRe = {};
    Object.keys(this.cjk).forEach(function (code) {
      var L = self.cjk[code], mode = L.boundary || 'substring', per = {};
      CATS.forEach(function (cat) {
        var terms = (L[cat] || []).slice().sort(function (a, b) { return b.length - a.length; });
        var alts = terms.map(function (t) { var e = esc(t); if (mode === 'kana' && KATA_ONLY.test(t)) e = '(?<![\\u30a0-\\u30ff])' + e + '(?![\\u30a0-\\u30ff])'; else if (mode === 'eojeol_start') e = '(?<![\\uac00-\\ud7a3])' + e; return e; });
        if (alts.length) per[cat] = new RegExp(alts.join('|'), 'gu');
      });
      self.cjkRe[code] = [per, L.category_masks || {}];
    });
    this.tilde = {};
    Object.keys(this.words).forEach(function (code) {
      var plain = {}, stems = [];
      ['minor', 'teen', 'minor_weak'].forEach(function (cat) { (self.words[code][cat] || []).forEach(function (t) { if (t[0] === '~') { var b = t.slice(1).replace(/\*$/, '').toLowerCase(); if (t.slice(-1) === '*') stems.push(b); else plain[b] = 1; } }); });
      self.tilde[code] = [plain, stems];
    });
    this.violence = {};
    Object.keys(this.words).forEach(function (code) {
      var v = self.words[code].violence || {};
      if ((v.verbs && v.verbs.length) || (v.abuse_verbs && v.abuse_verbs.length)) {
        var vt = {};
        ['verbs', 'abuse_verbs', 'kill_participles', 'abuse_participles', 'infinitives'].forEach(function (k) { (v[k] || []).forEach(function (w) { termTokens(w.toLowerCase())[0].forEach(function (t) { vt[t] = 1; }); }); });
        self.violence[code] = [v, vt];
      }
    });
    this._viol = {}; this._self = {};
  }
  Lexicon.prototype._tables = function (owners, folded) {
    var single = new Map(), stems = new Map(), phrases = new Map(), lens = {}, self = this;
    Object.keys(this.words).forEach(function (code) {
      var L = self.words[code];
      CATS.forEach(function (cat) {
        (L[cat] || []).forEach(function (raw) {
          var cond = raw[0] === '~', term = cond ? raw.slice(1) : raw, wild = term.slice(-1) === '*';
          var base = term.replace(/\*$/, '').normalize('NFKC').toLowerCase();
          if (folded) base = fold(base);
          var tt = termTokens(base), toks = tt[0], seps = tt[1];
          if (!toks.length) return;
          var o = owners[term.toLowerCase()];
          var unique = !cond && !!o && Object.keys(o).length === 1;
          var entry = [code, cat, raw, cond, unique];
          function push(m, k, v) { var l = m.get(k); if (!l) m.set(k, l = []); l.push(v); }
          if (toks.length === 1 && wild) { push(stems, toks[0], entry); lens[toks[0].length] = 1; }
          else if (toks.length === 1) push(single, toks[0], entry);
          else push(phrases, toks[0], [toks.slice(1), seps, wild, entry]);
        });
      });
    });
    return [single, stems, Object.keys(lens).map(Number).sort(function (a, b) { return a - b; }), phrases];
  };
  Lexicon.prototype.minorAlt = function (code, names, noTilde) {
    var L = this.words[code] || {}, terms = [];
    ['minor', 'teen'].forEach(function (c) { (L[c] || []).forEach(function (t) { if (!(noTilde && t[0] === '~')) terms.push(t.replace(/^~/, '')); }); });
    if (code !== 'en') ['minor', 'teen'].forEach(function (c) { (this.words.en[c] || []).forEach(function (t) { terms.push(t.replace(/^~/, '')); }); }, this);
    var a = alt(terms);
    if (names && names.length) a = '(?:' + a + '|' + names.slice().sort(function (x, y) { return y.length - x.length; }).map(function (n) { return n.split(/\s+/).map(esc).join('\\s+'); }).join('|') + ')';
    return a;
  };
  Lexicon.prototype.violenceRes = function (code, names) {
    var key = code + '|' + names.slice().sort().join('|');
    if (this._viol[key]) return this._viol[key];
    var v = this.violence[code][0];
    var minor = '(' + this.minorAlt(code, names) + ')';
    var det = (v.det || []).slice().sort(function (a, b) { return b.length - a.length; }).map(esc).join('|') || '(?!x)x';
    var eli = (v.det_elided || []).map(esc).join('|');
    var slot = '(?:(?:' + det + ')\\s+' + (eli ? '|(?:' + eli + ')' : '') + ')';
    var aux = (v.aux || []).slice().sort(function (a, b) { return b.length - a.length; }).map(function (a) { return a.split(/\s+/).map(esc).join('\\s+'); }).join('|');
    var adv = v.adverb ? '(?:' + v.adverb + '\\s+)?' : '';
    var res = [];
    [['abuse', v.abuse_verbs || []], ['kill', v.verbs || []]].forEach(function (p) { if (p[1].length) res.push([p[0], 'active', new RegExp(pyre(B + '(?:' + alt(p[1]) + ')\\s+' + slot + '{0,3}' + minor + "(?!')" + E), 'gu')]); });
    [['abuse', v.abuse_participles || []], ['kill', v.kill_participles || []]].forEach(function (p) { if (p[1].length && aux) res.push([p[0], 'passive', new RegExp(pyre(B + minor + '\\s+(?:' + aux + ')\\s+' + adv + '(?:' + alt(p[1]) + ')' + E), 'gu')]); });
    if (v.object_before_verb && v.infinitives && v.infinitives.length) {
      var ab = set(v.abuse_verbs || []);
      var a1 = v.infinitives.filter(function (i) { return ab[i]; }), k1 = v.infinitives.filter(function (i) { return !ab[i]; });
      [['abuse', a1], ['kill', k1]].forEach(function (p) { if (p[1].length) res.push([p[0], 'active', new RegExp(pyre(B + slot + '{0,3}' + minor + '\\s+(?:' + alt(p[1]) + ')' + E), 'gu')]); });
    }
    this._viol[key] = res;
    return res;
  };
  Lexicon.prototype.selfRes = function (code) {
    if (this._self[code]) return this._self[code];
    var L = this.words[code] || {}, minor = this.minorAlt(code, null, true), pats = L.self_patterns;
    if (!pats || !pats.length) {
      var det = ((L.violence || {}).det || []).map(esc).join('|') || '(?!x)x';
      pats = ['^\\s*(?:(?:' + det + ")\\s+)?(?:[\\w'-]+\\s+){0,1}(?:{MINOR}){E}"];
    }
    this._self[code] = pats.map(function (p) { return rx(p, L.numbers, minor, 'u'); });
    return this._self[code];
  };

  var LEX = null;
  S.lexicon = function (data) {
    if (data) LEX = new Lexicon(data);
    if (!LEX) { if (!G.LEXICON && G.root.GSYS_LEXICON) G.LEXICON = G.root.GSYS_LEXICON; if (!G.LEXICON) throw new SafetyError('child-safety word list missing: the runtime cannot start without it'); LEX = new Lexicon(G.LEXICON); }
    return LEX;
  };

  // ---------------------------------------------------------------- text analysis
  function mask(s, list) { list.forEach(function (r) { r.lastIndex = 0; s = s.replace(r, function (m) { return ' '.repeat(m.length); }); }); return s; }
  function prepare(text, lex) {
    var s = String(text).normalize('NFKC');
    s = s.replace(lex.zeroWidth, '').replace(lex.apos, "'");
    s = mask(s, lex.excCase);
    var low = mask(s.toLowerCase(), lex.excNocase);
    lex.cjkExc.forEach(function (x) { if (low.indexOf(x) >= 0) low = low.split(x).join(' '.repeat(x.length)); });
    if (low.length === s.length) { var o = ''; for (var i = 0; i < s.length; i++) o += (low[i] === ' ' && s[i] !== ' ') ? ' ' : s[i]; s = o; } else s = low;
    return [s, low];
  }
  S.prepare = function (t) { return prepare(t, S.lexicon()); };
  function skeleton(low, lex) {
    var s = fold(low);
    s = s.replace(/[\p{L}\p{N}_]+/gu, function (w) { if (/[a-z]/.test(w) && w.split('').some(function (c) { return lex.homo[c]; })) return w.split('').map(function (c) { return lex.homo[c] || c; }).join(''); return w; });
    s = s.replace(/[a-z0-9@$]+/g, function (w) { if (/[a-z]/.test(w) && /[013457@$]/.test(w) && !/^\d+$/.test(w)) return w.split('').map(function (c) { return lex.leet[c] || c; }).join(''); return w; });
    s = s.split('_').join(' ');
    s = s.replace(/(?<![\p{L}\p{N}_])(?:\p{L}[ ._*~+\-]){2,}\p{L}(?![\p{L}\p{N}_])/gu, function (m) { return m.replace(/[ ._*~+\-]/g, ''); });
    s = s.replace(/(\p{L})\1{2,}/gu, '$1');
    return mask(s, lex.excNocase);
  }
  S.skeleton = function (t) { return skeleton(t, S.lexicon()); };

  function Analysis(variant, text) {
    this.variant = variant; this.text = text; this.hits = []; this.ages = []; this.violence = []; this.langs = {}; this.protective = false;
    var b = [], m; SENT_RE.lastIndex = 0; while ((m = SENT_RE.exec(text))) b.push(m.index + m[0].length);
    this.bounds = b; this.tokens = tokenize(text); this.tstarts = this.tokens.map(function (t) { return t[0]; });
  }
  function bisectRight(a, x) { var lo = 0, hi = a.length; while (lo < hi) { var mid = (lo + hi) >> 1; if (x < a[mid]) hi = mid; else lo = mid + 1; } return lo; }
  Analysis.prototype.sent = function (p) { return bisectRight(this.bounds, p); };
  Analysis.prototype.tok = function (p) { return bisectRight(this.tstarts, p) - 1; };
  Analysis.prototype.sentenceText = function (i) { var b = [0].concat(this.bounds, [this.text.length]); return strip(this.text.slice(b[i], b[i + 1])); };
  Analysis.prototype.add = function (cat, lang, term, s, e) { this.hits.push([cat, lang, term, s, e, this.tok(s), this.sent(s)]); };
  Analysis.prototype.cats = function () { var c = set([].slice.call(arguments)); return this.hits.filter(function (h) { return c[h[0]]; }); };
  var MINOR_CATS = set(['minor', 'teen', 'minor_name']);

  function matchWords(an, caseText, lex, folded, names, declared) {
    var tb = lex.tables[folded ? 'folded' : 'plain'], single = tb[0], stems = tb[1], lens = tb[2], phrases = tb[3];
    var toks = an.tokens, text = an.text, raw = [];
    toks.forEach(function (tk, i) {
      var s = tk[0], e = tk[1], t = tk[2];
      (single.get(t) || []).forEach(function (en) { raw.push([en, s, e]); });
      for (var li = 0; li < lens.length; li++) { var n = lens[li]; if (n <= t.length) (stems.get(t.slice(0, n)) || []).forEach(function (en) { raw.push([en, s, e]); }); }
      (phrases.get(t) || []).forEach(function (ph) {
        var rest = ph[0], seps = ph[1], wild = ph[2], j = i, ok = true, end = e;
        for (var k = 0; k < rest.length; k++) {
          j++;
          if (j >= toks.length) { ok = false; break; }
          var gap = text.slice(toks[j - 1][1], toks[j][0]), sep = seps[k];
          if ((sep === 's' && (!gap || strip(gap))) || ((sep === "'" || sep === '-') && gap !== sep)) { ok = false; break; }
          var got = toks[j][2];
          if (!(got === rest[k] || (wild && k === rest.length - 1 && got.indexOf(rest[k]) === 0))) { ok = false; break; }
          end = toks[j][1];
        }
        if (ok) raw.push([ph[3], s, end]);
      });
      (names.idx.get(t) || []).forEach(function (nm) {
        var nt = nm[0], cap = nm[1], n = nt.length;
        if (i + n > toks.length) return;
        for (var k = 1; k < n; k++) if (toks[i + k][2] !== nt[k]) return;
        for (k = 0; k < n - 1; k++) if (strip(text.slice(toks[i + k][1], toks[i + k + 1][0]))) return;
        if (cap) { var c = caseText.slice(s, s + 1); if (folded || !(c !== c.toLowerCase() && c === c.toUpperCase())) return; }
        an.add('minor_name', '*', nt.join(' '), s, toks[i + n - 1][1]);
      });
    });
    var active = {}; (declared || []).forEach(function (d) { active[d] = 1; }); active.en = 1;
    var counts = {}, best = 0;
    Object.keys(lex.stop).forEach(function (code) { var sw = lex.stop[code], seen = {}; toks.forEach(function (tk) { if (sw[tk[2]]) seen[tk[2]] = 1; }); counts[code] = Object.keys(seen).length; if (counts[code] > best) best = counts[code]; });
    Object.keys(counts).forEach(function (code) { if (counts[code] >= lex.minStop && counts[code] * 2 >= best) active[code] = 1; });
    raw.forEach(function (r) { if (r[0][4]) active[r[0][0]] = 1; });
    Object.keys(active).forEach(function (k) { an.langs[k] = 1; });
    raw.forEach(function (r) { var en = r[0]; if (en[3] && !active[en[0]]) return; if (en[1] === 'protective') an.protective = true; an.add(en[1], en[0], en[2], r[1], r[2]); });
    return active;
  }
  function matchCjk(an, lex) {
    var text = an.text;
    if (!HAS_CJK.test(text)) return;
    Object.keys(lex.cjkRe).forEach(function (code) {
      var per = lex.cjkRe[code][0], masks = lex.cjkRe[code][1];
      Object.keys(per).forEach(function (cat) {
        var spans = [];
        (masks[cat] || []).forEach(function (mk) { var st = text.indexOf(mk); while (st >= 0) { spans.push([st, st + mk.length]); st = text.indexOf(mk, st + 1); } });
        var r = per[cat], m; r.lastIndex = 0;
        while ((m = r.exec(text))) {
          var a = m.index, b = a + m[0].length;
          if (!spans.some(function (sp) { return sp[0] <= a && b <= sp[1]; })) { if (cat === 'protective') an.protective = true; an.add(cat, code, m[0], a, b); an.langs[code] = 1; }
          if (!m[0].length) r.lastIndex++;
        }
      });
    });
  }
  function firstGroup(m) { for (var i = 1; i < m.length; i++) if (m[i]) return m[i]; return null; }
  function ages(an, lex, active) {
    var text = an.text, hasDigit = /\d/.test(text);
    Object.keys(lex.age).forEach(function (code) {
      if (lex.cjk[code]) { if (!HAS_CJK.test(text)) return; }
      else if (!(active[code] || hasDigit)) return;
      var nums = lex.numbers[code] || {};
      lex.age[code].forEach(function (p) {
        var r = p[0], m; r.lastIndex = 0;
        while ((m = r.exec(text))) {
          var val = firstGroup(m);
          if (val != null) {
            var n = /^\d+$/.test(val) ? parseInt(val, 10) : (nums[val.toLowerCase()] != null ? nums[val.toLowerCase()] : lex.numbers.en[val.toLowerCase()]);
            if (n != null) { n -= p[1]; if (n >= 0 && n < 18) an.ages.push([n, m.index, m.index + m[0].length, an.tok(m.index), an.sent(m.index)]); }
          }
          if (!m[0].length) r.lastIndex++;
        }
      });
    });
    if (lex.gradeRe && text.indexOf('grade') >= 0) {
      var r = lex.gradeRe, m; r.lastIndex = 0;
      while ((m = r.exec(text))) { var g = m[1] || m[2], n = /^\d+$/.test(g) ? parseInt(g, 10) : (lex.gradeNumbers[g] || 0); if (n > 0 && n <= 12) an.ages.push([n + 5, m.index, m.index + m[0].length, an.tok(m.index), an.sent(m.index)]); }
    }
    if (lex.minorRes.length && text.indexOf('minor') >= 0) lex.minorRes.forEach(function (r) { var m; r.lastIndex = 0; while ((m = r.exec(text))) an.add('minor', 'en', 'minor', m.index, m.index + m[0].length); });
  }
  function violence(an, lex, active, names) {
    if (!(an.cats('minor', 'teen', 'minor_name').length || an.ages.length)) return;
    var words = {}; an.tokens.forEach(function (t) { words[t[2]] = 1; });
    Object.keys(lex.violence).forEach(function (code) {
      var vt = lex.violence[code][1];
      if (!active[code] || !Object.keys(words).some(function (w) { return vt[w]; })) return;
      lex.violenceRes(code, names).forEach(function (p) {
        var r = p[2], m; r.lastIndex = 0;
        while ((m = r.exec(an.text))) an.violence.push([p[0], p[1], m.index, m.index + m[0].length, an.sent(m.index), m[0]]);
      });
    });
  }
  function NameIndex(entries) { this.idx = new Map(); this.full = []; var self = this; (entries || []).forEach(function (e) { var l = self.idx.get(e[0][0]); if (!l) self.idx.set(e[0][0], l = []); l.push(e); if (!e[1]) self.full.push(e[0].join(' ')); }); }
  S.NameIndex = NameIndex;
  function analyse(text, lex, names, declared) {
    lex = lex || S.lexicon(); names = names instanceof NameIndex ? names : new NameIndex(names || []);
    var pr = prepare(text, lex), caseText = pr[0], low = pr[1], out = [];
    var an = new Analysis('text', low), active = matchWords(an, caseText, lex, false, names, declared);
    matchCjk(an, lex); ages(an, lex, active); violence(an, lex, active, names.full); out.push(an);
    var sk = skeleton(low, lex);
    if (sk !== low) { var an2 = new Analysis('skeleton', sk), a2 = matchWords(an2, sk, lex, true, names, declared); matchCjk(an2, lex); ages(an2, lex, a2); violence(an2, lex, a2, names.full); out.push(an2); }
    return out;
  }
  S.analyse = analyse;

  function finding(rule, path, where, message, terms, excerpt) {
    var f = { rule: rule, severity: RULES[rule][0], title: RULES[rule][1], path: path, where: where, message: message };
    if (terms) f.terms = terms;
    if (excerpt) f.excerpt = excerpt.slice(0, 120);
    return f;
  }
  function q(terms) { var u = {}, l = []; terms.forEach(function (t) { if (!u[t]) { u[t] = 1; l.push(t); } }); l.sort(); return l.slice(0, 3).map(function (t) { return '"' + t + '"'; }).join(', '); }
  function terms(hs) { return hs.map(function (h) { return h[2]; }); }

  function textFindings(analyses, path, where, lex) {
    lex = lex || S.lexicon(); path = path || 'text'; where = where || 'text';
    var out = [], seen = {};
    function add(rule, msg, t, ex, si) { var k = rule + '|' + (si == null ? -1 : si); if (seen[k]) return; seen[k] = 1; out.push(finding(rule, path, where, msg, t, ex)); }
    analyses.forEach(function (an) {
      var ab = an.cats('always_block');
      if (ab.length) add('CS001', q(terms(ab)) + ' refers to sexual content involving minors. Stark World Engine never exports it. Remove it.', { always_block: terms(ab) });
      var sents = {};
      an.hits.forEach(function (h) { (sents[h[6]] = sents[h[6]] || []).push(h); });
      an.ages.forEach(function (a) { (sents[a[4]] = sents[a[4]] || []).push(['age', 'num', 'age ' + a[0], a[1], a[2], a[3], a[4]]); });
      var minorsAll = [], strongAll = [];
      Object.keys(sents).map(Number).sort(function (a, b) { return a - b; }).forEach(function (si) {
        var hs = sents[si];
        var minors = hs.filter(function (h) { return MINOR_CATS[h[0]] || h[0] === 'age'; });
        var weakm = hs.filter(function (h) { return h[0] === 'minor_weak'; }), strong = hs.filter(function (h) { return h[0] === 'sex_strong'; });
        var weak = hs.filter(function (h) { return h[0] === 'sex_weak'; }), abuse = hs.filter(function (h) { return h[0] === 'abuse'; }), mtime = hs.filter(function (h) { return h[0] === 'minor_time'; });
        minorsAll = minorsAll.concat(minors); strongAll = strongAll.concat(strong);
        var ex = an.sentenceText(si);
        if (minors.length && strong.length) add('CS002', q(terms(strong)) + ' is in the same sentence as ' + q(terms(minors)) + '. Sexual content involving minors is never allowed. Rewrite or remove this sentence.', { sexual: terms(strong), minor: terms(minors) }, ex, si);
        if (minors.length && weak.length) {
          var win = HAS_CJK.test(ex) ? lex.weakWindowCjk : lex.weakWindow;
          var near = weak.filter(function (w) { return minors.some(function (m) { return Math.abs(w[5] - m[5]) <= win; }); });
          if (near.length) add('CS002', q(terms(near)) + ' is close to ' + q(terms(minors)) + '. Nudity, undressing and romance are never allowed for minors. Rewrite this sentence.', { sexual: terms(near), minor: terms(minors) }, ex, si);
        }
        if ((minors.length || mtime.length) && abuse.length) add('CS008', q(terms(abuse)) + ' is connected to ' + q(terms(minors.concat(mtime))) + '. Content about abusing children is never allowed. Remove this sentence.', { abuse: terms(abuse), minor: terms(minors.concat(mtime)) }, ex, si);
        if (weakm.length && strong.length && !minors.length) add('CS013', q(terms(strong)) + ' is next to ' + q(terms(weakm)) + '. If anyone here is under 18 this is not allowed; if they are adults, say so (e.g. "woman", "man").', { sexual: terms(strong), ambiguous: terms(weakm) }, ex, si);
      });
      if (minorsAll.length && strongAll.length && !out.some(function (f) { return f.rule === 'CS002'; })) add('CS003', q(terms(strongAll)) + ' and ' + q(terms(minorsAll)) + ' are in the same text. Sexual content and minors can never share a text. Split or rewrite it.', { sexual: terms(strongAll), minor: terms(minorsAll) });
      an.violence.forEach(function (v) {
        var frag = strip(v[5]), si = v[4];
        if (v[0] === 'abuse') add('CS008', '"' + frag + '" describes abuse of a minor. Content about abusing children is never allowed. Remove it.', { abuse: [frag] }, an.sentenceText(si), si);
        else if (v[1] === 'active') { if (!an.protective) add('CS009', '"' + frag + '" directs violence at a minor. Children can never be harmed in a Stark World Engine game. Remove it or make it about protecting them.', { violence: [frag] }, an.sentenceText(si), si); }
        else add('CS109', '"' + frag + '" mentions past harm to a minor. Allowed as backstory; the game itself can never show or let anyone harm a child character.', { violence: [frag] }, an.sentenceText(si), si);
      });
    });
    return out;
  }
  S.textFindings = textFindings;

  function nameEntries(names, lex) {
    var out = [], seen = {};
    (names || []).forEach(function (nm) {
      if (typeof nm !== 'string' || !strip(nm)) return;
      var low = prepare(nm, lex)[1], toks = tokenize(low).map(function (t) { return t[2]; });
      if (!toks.length || (toks.length === 1 && toks[0].length < 2)) return;
      var ents = [[toks, toks.length === 1 && !HAS_CJK.test(toks[0]) && !!lex.commonNames[toks[0]]]];
      var first = toks[0];
      if (toks.length > 1 && first.length >= 3 && !lex.commonNames[first] && !/^\d+$/.test(first)) ents.push([[first], true]);
      ents.forEach(function (e) { var k = e[0].join(' ') + '|' + e[1]; if (!seen[k]) { seen[k] = 1; out.push(e); } });
    });
    return out;
  }
  S.nameEntries = function (names) { return nameEntries(names, S.lexicon()); };

  /** One string -> findings (the runtime check for text a script or generator produces). */
  S.scanText = function (text, names, declared, path, where) {
    var lex = S.lexicon();
    return textFindings(analyse(text, lex, new NameIndex(nameEntries(names || [], lex)), declared || []), path, where, lex);
  };
  S.textBlocked = function (text, names, declared) { return S.scanText(text, names, declared).some(function (f) { return f.severity === 'block'; }); };

  // ---------------------------------------------------------------- ages
  S.normalizeAge = function (v) {
    var lex = S.lexicon();
    if (v === null || v === undefined || v === '') return [null, true];
    if (typeof v === 'boolean') return [null, false];
    if (typeof v === 'number') { if (!(v >= 0)) return [null, false]; return [v < 13 ? 'child' : v < 18 ? 'teen' : 'adult', true]; }
    var s = strip(String(v)).toLowerCase();
    if (S.AGE_GROUPS.indexOf(s) >= 0) return [s, true];
    if (/^\d+$/.test(s)) return S.normalizeAge(parseInt(s, 10));
    if (lex.ageSyn[s]) return [lex.ageSyn[s], true];
    return [null, false];
  };
  function ageIn(frag, lex, code) {
    var pats = (lex.age[code] || []).concat(lex.age.en || []);
    for (var i = 0; i < pats.length; i++) {
      var r = pats[i][0], m; r.lastIndex = 0; m = r.exec(frag);
      if (m) { var val = firstGroup(m); if (val) { var n = /^\d+$/.test(val) ? parseInt(val, 10) : ((lex.numbers[code] || {})[val] != null ? lex.numbers[code][val] : lex.numbers.en[val]); if (n != null) { n -= pats[i][1]; return n < 13 ? 'child' : n < 18 ? 'teen' : 'adult'; } } }
    }
    var words = {}, k; for (k in lex.numbers.en) words[k] = lex.numbers.en[k]; for (k in (lex.numbers[code] || {})) words[k] = lex.numbers[code][k];
    var toks = tokenize(frag);
    for (i = 0; i < toks.length; i++) { var t = toks[i][2], n2 = (/^\d+$/.test(t) && t.length <= 2) ? parseInt(t, 10) : words[t]; if (n2 != null) return n2 < 13 ? 'child' : n2 < 18 ? 'teen' : 'adult'; }
    return null;
  }
  S.inferAge = function (texts) {
    var lex = S.lexicon(), best = null;
    (texts || []).forEach(function (raw) {
      if (typeof raw !== 'string' || !strip(raw)) return;
      var low = prepare(raw, lex)[1];
      Object.keys(lex.words).forEach(function (code) {
        lex.selfRes(code).forEach(function (r) {
          var m = r.exec(low);
          if (!m || low.charAt(m.index + m[0].length) === "'") return;
          var frag = m[0], age = ageIn(frag, lex, code);
          if (age == null) age = (lex.teenRe.test(frag) && !lex.childRe.test(frag)) ? 'teen' : 'child';
          if (MINOR[age]) best = (best === 'child' || age === 'child') ? 'child' : 'teen';
        });
      });
      var head = strip(low).slice(0, 8);
      Object.keys(lex.cjk).forEach(function (code) {
        var L = lex.cjk[code];
        if ((L.minor || []).some(function (t) { return head.indexOf(t) >= 0; })) best = best || 'child';
        else if ((L.teen || []).some(function (t) { return head.indexOf(t) >= 0; })) best = best || 'teen';
        (lex.age[code] || []).forEach(function (p) { p[0].lastIndex = 0; var m = p[0].exec(strip(low).slice(0, 12)); if (m && parseInt(m[1], 10) < 18) best = parseInt(m[1], 10) < 13 ? 'child' : (best || 'teen'); });
      });
    });
    return best;
  };

  // ---------------------------------------------------------------- scripts
  function stripComments(src) {
    var out = '', i = 0, n = (src || '').length;
    while (i < n) {
      var c = src[i];
      if (c === "'" || c === '"' || c === '`') { var j = i + 1; while (j < n && src[j] !== c) j += src[j] === '\\' ? 2 : 1; out += src.slice(i, j + 1); i = j + 1; }
      else if (src.substr(i, 2) === '//') { var e = src.indexOf('\n', i); i = e < 0 ? n : e; }
      else if (src.substr(i, 2) === '/*') { var e2 = src.indexOf('*/', i + 2); i = e2 < 0 ? n : e2 + 2; }
      else { out += c; i++; }
    }
    return out;
  }
  S.scriptStrings = function (src) {
    var out = [], i = 0, n = (src || '').length, escs = { n: '\n', t: '\t', r: '', '0': '' };
    while (i < n) {
      var c = src[i];
      if (c === "'" || c === '"' || c === '`') {
        var j = i + 1, buf = '';
        while (j < n && src[j] !== c) {
          if (src[j] === '\\' && j + 1 < n) { var nx = src[j + 1]; if (nx === 'u' && /^[0-9a-fA-F]{4}$/.test(src.substr(j + 2, 4))) { buf += String.fromCharCode(parseInt(src.substr(j + 2, 4), 16)); j += 6; continue; } buf += escs[nx] != null ? escs[nx] : nx; j += 2; continue; }
          if (c === '`' && src.substr(j, 2) === '${') { var depth = 1; j += 2; while (j < n && depth) { if (src[j] === '{') depth++; else if (src[j] === '}') depth--; j++; } buf += ' '; continue; }
          buf += src[j]; j++;
        }
        if (strip(buf)) out.push(buf);
        i = j + 1;
      } else if (src.substr(i, 2) === '//') { var e = src.indexOf('\n', i); e = e < 0 ? n : e; out.push(src.slice(i + 2, e)); i = e; }
      else if (src.substr(i, 2) === '/*') { var e2 = src.indexOf('*/', i + 2); e2 = e2 < 0 ? n : e2; out.push(src.slice(i + 2, e2)); i = e2 + 2; }
      else i++;
    }
    return out.filter(function (s) { return strip(s); });
  };
  var SCRIPT_HARM_RE = /(?:\b(?:combat|ai|romance|appearance|entity|world|physics)\s*\.\s*)?\b(damage|kill|harm|hurt|attack|capture|kidnap|undress|strip|startRomance|addAffinity|setHostile|setTarget|execute|sacrifice|torture|applyStatus|setOutfit|start)\s*\(\s*(['"])([^'"\\\n]{1,64})\2(?:\s*,\s*(['"])([^'"\\\n]{1,64})\4)?/g;
  var SCRIPT_FIND_HARM_RE = /\b(?:find|get|entity)\s*\(\s*(['"])([^'"\\\n]{1,64})\1\s*\)\s*\.\s*(damage|kill|harm|hurt|attack|undress|setHostile|capture|setOutfit|torture)\b/g;

  // ---------------------------------------------------------------- world scan (same walk as the Python gate)
  function lvWhere(li) { return li === 0 ? 'the world' : 'level ' + (li + 1); }
  function lvPath(li) { return li === 0 ? 'world' : 'levels[' + (li - 1) + ']'; }
  function isObj(o) { return o !== null && typeof o === 'object' && !Array.isArray(o); }
  function nude(v) {
    if (typeof v === 'string') return !!NUDE_OUTFITS[strip(v).toLowerCase()];
    if (isObj(v)) { if (v.nude === true || v.naked === true || v.undressed === true) return true; var cov = String(v.coverage || '').toLowerCase(); return cov === 'none' || cov === 'underwear' || cov === 'partial' || cov === 'minimal' || nude(v.outfit) || nude(v.name); }
    if (Array.isArray(v)) return v.some(nude);
    return false;
  }
  function targets(v) {
    if (v === true) return [true];
    if (typeof v === 'string') return [v];
    if (Array.isArray(v)) return v.reduce(function (a, x) { return a.concat(targets(x)); }, []);
    if (isObj(v)) { var out = []; ['target', 'with', 'npc', 'id', 'who', 'entity', 'a', 'b', 'partner', 'char', 'character'].forEach(function (k) { if (k in v) out = out.concat(targets(v[k])); }); return out; }
    return [];
  }
  function romanceIds(v, romantic, depth) {
    var out = []; depth = depth || 0;
    if (depth > 8) return out;
    if (isObj(v)) {
      var t = v.type != null ? v.type : v.kind != null ? v.kind : v.relation;
      var here = t == null ? romantic : !!ROMANTIC_REL[String(t).toLowerCase()];
      Object.keys(v).forEach(function (k) {
        var x = v[k];
        if (here && typeof x === 'string' && ['name', 'text', 'desc', 'title', 'type', 'kind', 'relation'].indexOf(k) < 0) out.push(x);
        else if (here && (isObj(x) || Array.isArray(x)) && depth <= 1 && t == null) out.push(k);
        if (isObj(x) || Array.isArray(x)) out = out.concat(romanceIds(x, here, depth + 1));
      });
    } else if (Array.isArray(v)) v.forEach(function (x) { if (typeof x === 'string') { if (romantic) out.push(x); } else out = out.concat(romanceIds(x, romantic, depth + 1)); });
    return out;
  }

  function Scan(world, lex, declared) {
    this.w = world; this.lex = lex; this.declared = declared || [];
    this.fields = []; this.chars = []; this.byId = {}; this.contexts = {}; this.findings = []; this.dialogOwner = {}; this.checks = [];
  }
  Scan.prototype.ctx = function (kind, id, where) { var k = kind + '\u0000' + id; return this.contexts[k] || (this.contexts[k] = { kind: kind, id: id, participants: {}, fields: [], where: where, ids: {}, speakers: {} }); };
  Scan.prototype.field = function (path, where, text, ctx, isId) {
    if (typeof text !== 'string' || !strip(text)) return;
    if (isId) text = text.replace(/[_.\-]+/g, ' ');
    this.fields.push([path, where, text, ctx || {}]);
    var self = this;
    if (ctx) ['dialog', 'quest', 'char', 'script', 'graph'].forEach(function (k) { if (ctx[k]) self.ctx(k, ctx[k], where).fields.push(self.fields.length - 1); });
  };
  Scan.prototype.generic = function (obj, path, where, ctx, depth) {
    depth = depth || 0;
    if (depth > 12) return;
    var self = this;
    if (typeof obj === 'string') this.field(path, where, obj, ctx);
    else if (isObj(obj)) Object.keys(obj).forEach(function (k) {
      var v = obj[k];
      if (!SCHEMA_KEYS[k]) self.field(path + '.' + k + '#key', where, k, ctx, true);
      if (SKIP_KEYS[k]) return;
      if (ID_KEYS[k] && typeof v === 'string') { self.field(path + '.' + k, where, v, ctx, true); return; }
      self.generic(v, path + '.' + k, where, ctx, depth + 1);
    });
    else if (Array.isArray(obj)) obj.slice(0, 5000).forEach(function (v, i) { self.generic(v, path + '[' + i + ']', where, ctx, depth + 1); });
  };
  Scan.prototype.char = function (key, cid, rec, path, where, kind, level) {
    var c = { key: key, id: cid != null ? String(cid) : null, rec: rec, path: path, where: where, kind: kind, level: level, name: typeof rec.name === 'string' ? rec.name : null, texts: [], declared: null, ok: true, inferred: null, age: 'adult' };
    c.raw = rec.age !== undefined ? rec.age : rec.age_years !== undefined ? rec.age_years : rec.age_group;
    OWN_TEXT_KEYS.forEach(function (k) { var v = rec[k]; if (typeof v === 'string') c.texts.push(v); else if (Array.isArray(v)) v.forEach(function (x) { if (typeof x === 'string') c.texts.push(x); }); });
    this.chars.push(c);
    if (c.id) (this.byId[c.id] = this.byId[c.id] || []).push(c);
    return c;
  };
  Scan.prototype.walk = function () {
    var self = this, w = this.w;
    var levels = [w].concat((Array.isArray(w.levels) ? w.levels : []).filter(isObj));
    levels.forEach(function (L, li) {
      var lp = lvPath(li), lw = lvWhere(li);
      ['name', 'story', 'author', 'title', 'desc', 'intro', 'outro', 'credits'].forEach(function (k) { if (typeof L[k] === 'string') self.field(lp + '.' + k, lw + ': ' + k, L[k]); });
      (Array.isArray(L.objects) ? L.objects : []).forEach(function (o, i) {
        if (!isObj(o)) return;
        var op = lp + '.objects[' + i + ']', t = o.t, label = o.name || o.id || t, ow = lw + ': ' + t + ' "' + label + '"';
        var isChar = t === 'npc' || t === 'enemy' || 'age' in o || 'age_years' in o;
        var c = isChar ? self.char('obj:' + li + ':' + i, o.id, o, op, ow, t || 'object', li) : null;
        var ctx = c ? { char: c.key } : {};
        Object.keys(o).forEach(function (k) {
          if (SKIP_KEYS[k] || ['x', 'z', 'y', 's', 'rot', 'hp', 'n', 'gold', 'patrol'].indexOf(k) >= 0) return;
          var v = o[k];
          if (ID_KEYS[k] && typeof v === 'string') self.field(op + '.' + k, ow, v, ctx, true);
          else self.generic(v, op + '.' + k, ow, ctx);
        });
        if (c && typeof o.dialog === 'string') (self.dialogOwner[o.dialog] = self.dialogOwner[o.dialog] || {})[c.key] = 1;
      });
      if (isObj(L.items)) Object.keys(L.items).forEach(function (iid) { self.field(lp + '.items.' + iid + '#key', lw + ': item "' + iid + '"', iid, null, true); self.generic(L.items[iid], lp + '.items.' + iid, lw + ': item "' + iid + '"'); });
      (Array.isArray(L.quests) ? L.quests : []).forEach(function (q, qi) {
        if (!isObj(q)) return;
        var qid = String(q.id || (li + ':' + qi)), qw = lw + ': quest "' + (q.title || qid) + '"';
        self.generic(q, lp + '.quests[' + qi + ']', qw, { quest: qid });
        var cx = self.ctx('quest', qid, qw);
        if (typeof q.giver === 'string') cx.ids[q.giver] = 1;
        (q.steps || []).forEach(function (s) { if (!isObj(s)) return; ['npc', 'id'].forEach(function (k) { if (typeof s[k] === 'string') cx.ids[s[k]] = 1; }); if (s.type === 'defeat' && typeof s.id === 'string') self.checks.push(['defeat', s.id, lp + '.quests[' + qi + ']', qw]); });
      });
      if (isObj(L.dialogs)) Object.keys(L.dialogs).forEach(function (did) {
        var d = L.dialogs[did], dw = lw + ': dialogue "' + did + '"', dp = lp + '.dialogs.' + did;
        self.generic(d, dp, dw, { dialog: String(did) });
        var cx = self.ctx('dialog', String(did), dw);
        if (isObj(d) && isObj(d.nodes)) Object.keys(d.nodes).forEach(function (nid) {
          var n = d.nodes[nid];
          if (!isObj(n)) return;
          if (typeof n.speaker === 'string') cx.speakers[strip(n.speaker).toLowerCase()] = 1;
          [n.do].concat((n.choices || []).filter(isObj)).forEach(function (a) { if (isObj(a)) self.checks.push(['action', String(did), a, dp + '.nodes.' + nid, dw + ', line "' + nid + '"']); });
        });
      });
      var known = set(['name', 'story', 'author', 'title', 'desc', 'intro', 'outro', 'credits', 'objects', 'items', 'quests', 'dialogs', 'levels', 'systems', 'child_safety', 'seed', 'size', 'terrain', 'water', 'day', 'spawn', 'player', 'difficulty', 'sfx']);
      Object.keys(L).forEach(function (k) { if (!known[k] && !SKIP_KEYS[k]) self.generic(L[k], lp + '.' + k, lw + ': ' + k); });
      if (isObj(L.player)) { var p = {}; Object.keys(L.player).forEach(function (k) { if (['hp', 'col', 'items'].indexOf(k) < 0) p[k] = L.player[k]; }); self.generic(p, lp + '.player', lw + ': player'); }
    });
    this.walkSystems(w.systems);
  };
  Scan.prototype.walkSystems = function (sy) {
    if (!isObj(sy)) return;
    var self = this, chars = sy.characters, list = [];
    if (isObj(chars)) list = Object.keys(chars).map(function (k) { return [k, chars[k]]; });
    else if (Array.isArray(chars)) list = chars.filter(isObj).map(function (c, i) { return [c.id != null ? String(c.id) : String(i), c]; });
    list.forEach(function (p) {
      var cid = p[0], rec = p[1];
      if (!isObj(rec)) return;
      var cp = 'systems.characters.' + cid, cw = 'character "' + (rec.name || cid) + '"';
      var c = self.char('char:' + cid, cid, rec, cp, cw, 'character', null);
      self.field(cp + '#key', cw, String(cid), { char: c.key }, true);
      Object.keys(rec).forEach(function (k) { if (SKIP_KEYS[k]) return; var v = rec[k]; if (ID_KEYS[k] && typeof v === 'string') self.field(cp + '.' + k, cw, v, { char: c.key }, true); else self.generic(v, cp + '.' + k, cw, { char: c.key }); });
      if (typeof rec.dialog === 'string') (self.dialogOwner[rec.dialog] = self.dialogOwner[rec.dialog] || {})[c.key] = 1;
    });
    (Array.isArray(sy.entities) ? sy.entities : []).forEach(function (ent, i) {
      if (!isObj(ent)) return;
      var ep = 'systems.entities[' + i + ']', ew = 'entity "' + (ent.name || ent.id || ent.kind || i) + '"';
      var isChar = ['npc', 'enemy', 'animal', 'player', 'character'].indexOf(String(ent.kind || '').toLowerCase()) >= 0 || 'age' in ent || 'age_years' in ent;
      var c = isChar ? self.char('ent:' + i, ent.id, ent, ep, ew, String(ent.kind || 'entity'), null) : null;
      var ctx = c ? { char: c.key } : {};
      Object.keys(ent).forEach(function (k) {
        if (SKIP_KEYS[k] || ['x', 'z', 'y', 's', 'rot', 'hp', 'n', 'gold', 'patrol'].indexOf(k) >= 0) return;
        var v = ent[k];
        if (ID_KEYS[k] && typeof v === 'string') self.field(ep + '.' + k, ew, v, ctx, true); else self.generic(v, ep + '.' + k, ew, ctx);
      });
      if (c && typeof ent.dialog === 'string') (self.dialogOwner[ent.dialog] = self.dialogOwner[ent.dialog] || {})[c.key] = 1;
    });
    (Array.isArray(sy.scripts) ? sy.scripts : []).forEach(function (sc, i) {
      if (!isObj(sc)) return;
      var sid = String(sc.id != null ? sc.id : i), sp = 'systems.scripts[' + i + ']', sw = 'script "' + sid + '"', cx = self.ctx('script', sid, sw);
      if (typeof sc.entity === 'string') cx.ids[sc.entity] = 1;
      var src = typeof sc.source === 'string' ? sc.source : '';
      S.scriptStrings(src).forEach(function (lit, j) { self.field(sp + '.source#string' + j, sw, lit, { script: sid }); });
      Object.keys(sc).forEach(function (k) { if (['source', 'entity', 'id'].indexOf(k) < 0) self.generic(sc[k], sp + '.' + k, sw, { script: sid }); });
      self.checks.push(['script', src, sp, sw]);
    });
    (Array.isArray(sy.graphs) ? sy.graphs : []).forEach(function (g, i) {
      if (!isObj(g)) return;
      var gid = String(g.id != null ? g.id : i), gp = 'systems.graphs[' + i + ']', gw = 'node graph "' + gid + '"', cx = self.ctx('graph', gid, gw);
      if (typeof g.entity === 'string') cx.ids[g.entity] = 1;
      (g.nodes || []).forEach(function (n, j) { if (!isObj(n)) return; var nw = gw + ', node "' + (n.id != null ? n.id : j) + '"'; self.generic(n.params, gp + '.nodes[' + j + '].params', nw, { graph: gid }); self.checks.push(['node', n, gp + '.nodes[' + j + ']', nw]); });
    });
    Object.keys(sy).forEach(function (k) {
      if (['characters', 'scripts', 'graphs', 'safety', 'entities'].indexOf(k) >= 0) return;
      self.generic(sy[k], 'systems.' + k, 'systems: ' + k);
      if (['romance', 'relationships', 'romances', 'dating'].indexOf(k) >= 0) self.checks.push(['romance', sy[k], 'systems.' + k, 'systems: ' + k]);
      if (k === 'ai' && isObj(sy[k])) {
        if (isObj(sy.ai.squads)) Object.keys(sy.ai.squads).forEach(function (sq) { if (isObj(sy.ai.squads[sq])) self.checks.push(['squad', sy.ai.squads[sq], 'systems.ai.squads.' + sq, 'squad "' + sq + '"']); });
        if (Array.isArray(sy.ai.capture)) sy.ai.capture.forEach(function (cap) { self.checks.push(['capture', cap, 'systems.ai.capture', 'systems: ai capture']); });
      }
    });
  };
  Scan.prototype.resolveAges = function () {
    var self = this;
    this.chars.forEach(function (c) {
      var r = S.normalizeAge(c.raw); c.declared = r[0]; c.ok = r[1];
      if (!c.ok) self.findings.push(finding('CS015', c.path + '.age', c.where, 'Age "' + c.raw + '" is not child, teen or adult. Set one of those three (or a number of years).'));
      var inf = S.inferAge(c.texts); c.inferred = inf;
      if (MINOR[c.declared]) c.age = c.declared;
      else if (inf) { c.age = inf; self.findings.push(finding('CS012', c.path, c.where, '"' + (c.name || c.id) + '" is described as a ' + inf + ' but tagged ' + (c.declared || 'adult (default)') + '. It is treated as a ' + inf + ': immune to harm, never romanced. Set its age group to make this explicit.')); }
      else if (!c.ok) c.age = 'child';
      else c.age = c.declared || 'adult';
    });
  };
  Scan.prototype.minors = function () { return this.chars.filter(function (c) { return MINOR[c.age]; }); };
  Scan.prototype.isMinorId = function (id) { return (this.byId[String(id)] || []).some(function (c) { return MINOR[c.age]; }); };
  Scan.prototype.run = function () {
    var self = this, lex = this.lex;
    this.walk(); this.resolveAges();
    var minors = this.minors(), minorKeys = {};
    minors.forEach(function (c) { minorKeys[c.key] = 1; });
    var names = new NameIndex(nameEntries(minors.filter(function (c) { return c.name; }).map(function (c) { return c.name; }), lex));
    Object.keys(this.contexts).forEach(function (k) {
      var cx = self.contexts[k], parts = cx.participants;
      if (cx.kind === 'dialog') {
        Object.keys(self.dialogOwner[cx.id] || {}).forEach(function (ck) { parts[ck] = 1; });
        self.chars.forEach(function (c) { if (c.name && cx.speakers[strip(c.name).toLowerCase()]) parts[c.key] = 1; });
      }
      Object.keys(cx.ids).forEach(function (i) { (self.byId[i] || []).forEach(function (c) { parts[c.key] = 1; }); });
      if (cx.kind === 'char') parts[cx.id] = 1;
    });
    var analyses = this.fields.map(function (f) { var a = analyse(f[2], lex, names, self.declared); self.findings = self.findings.concat(textFindings(a, f[0], f[1], lex)); return a; });
    Object.keys(this.contexts).forEach(function (k) {
      var cx = self.contexts[k];
      if (!Object.keys(cx.participants).some(function (p) { return minorKeys[p]; })) return;
      var who = []; self.chars.forEach(function (c) { if (cx.participants[c.key] && minorKeys[c.key]) who.push(c.name || c.id || c.key); });
      cx.fields.forEach(function (idx) {
        var f = self.fields[idx];
        analyses[idx].forEach(function (an) {
          var strong = an.cats('sex_strong'), weak = an.cats('sex_weak');
          if (cx.kind === 'char') { if (strong.length || weak.length) self.findings.push(finding('CS005', f[0], f[1], q(terms(strong.concat(weak))) + ' in the own text of ' + q(who) + ' (a minor). Rewrite it.', { sexual: terms(strong.concat(weak)) })); return; }
          if (strong.length) self.findings.push(finding('CS004', f[0], f[1], q(terms(strong)) + ' appears in a ' + cx.kind + ' that involves ' + q(who) + ' (a minor). Remove it.', { sexual: terms(strong) }));
          else if (weak.length) self.findings.push(finding('CS104', f[0], f[1], q(terms(weak)) + ' appears in a ' + cx.kind + ' that involves ' + q(who) + ' (a minor). Check it is not about them.', { sexual: terms(weak) }));
        });
      });
    });
    this.dataRules();
    return this.report();
  };
  Scan.prototype.dataRules = function () {
    var F = this.findings, self = this, minorKeys = {};
    this.minors().forEach(function (c) {
      minorKeys[c.key] = 1;
      var rec = c.rec, nm = '"' + (c.name || c.id) + '"';
      if (c.kind === 'enemy' || HOSTILE_ROLES[String(rec.role || '').toLowerCase()] || rec.hostile === true || HOSTILE_ROLES[String(rec.team || '').toLowerCase()] || ['hostile', 'aggressive'].indexOf(String(rec.ai || '').toLowerCase()) >= 0)
        F.push(finding('CS007', c.path, c.where, nm + ' is a ' + c.age + ' and cannot be an enemy or fight. Make it a friendly character.'));
      ['romanceable', 'romance', 'dateable', 'marriageable', 'romance_option'].forEach(function (k) { var v = rec[k]; if (!(v === undefined || v === null || v === false || v === 0 || v === '' || v === 'no' || v === 'none')) F.push(finding('CS006', c.path + '.' + k, c.where, nm + ' is a ' + c.age + ': children and teens are never romance options.')); });
      ['outfit', 'appearance', 'outfits', 'clothing', 'look'].forEach(function (k) { if (nude(rec[k])) F.push(finding('CS010', c.path + '.' + k, c.where, nm + ' is a ' + c.age + ': no nudity, underwear-only or undressed looks.')); });
      if (rec.capturable || rec.kidnappable) F.push(finding('CS011', c.path, c.where, nm + ' is a ' + c.age + ' and cannot be captured.'));
    });
    this.checks.forEach(function (chk) {
      var kind = chk[0];
      if (kind === 'defeat' && self.isMinorId(chk[1])) F.push(finding('CS007', chk[2], chk[3], 'This quest asks the player to defeat "' + chk[1] + '", a child or teen character. Children can never be combat targets.'));
      else if (kind === 'action') {
        var owners = self.dialogOwner[chk[1]] || {}, act = chk[2];
        var ownerMinor = Object.keys(owners).some(function (k) { return minorKeys[k]; });
        Object.keys(act).forEach(function (key) {
          var k = key.toLowerCase(), rule = ROMANCE_KEYS[k] ? 'CS006' : UNDRESS_KEYS[k] ? 'CS010' : CAPTURE_KEYS[k] ? 'CS011' : HARM_KEYS[k] ? 'CS007' : null;
          if (!rule) return;
          var tg = targets(act[key]);
          var hit = tg.some(function (t) { return (t === 'self' || t === 'npc' || t === true) && ownerMinor; }) || tg.some(function (t) { return typeof t === 'string' && ['self', 'npc', 'player'].indexOf(t) < 0 && self.isMinorId(t); });
          if (hit) F.push(finding(rule, chk[3] + '.' + key, chk[4], 'Dialogue action "' + key + '" targets a child or teen character. That is never allowed. Remove it.'));
        });
      } else if (kind === 'script') {
        var clean = stripComments(chk[1]), m;
        SCRIPT_HARM_RE.lastIndex = 0;
        while ((m = SCRIPT_HARM_RE.exec(clean))) {
          var fn = m[1], ids = [m[3], m[5]];
          if (fn === 'start' && clean.slice(Math.max(0, m.index - 12), m.index + 8).indexOf('romance') < 0) continue;
          var bad = ids.filter(function (i) { return i && self.isMinorId(i); });
          if (bad.length) F.push(finding('CS014', chk[2], chk[3], 'The script calls ' + fn + '() on a child or teen character ("' + bad[0] + '"). The runtime would refuse it; remove the call.'));
        }
        SCRIPT_FIND_HARM_RE.lastIndex = 0;
        while ((m = SCRIPT_FIND_HARM_RE.exec(clean))) if (self.isMinorId(m[2])) F.push(finding('CS014', chk[2], chk[3], 'The script calls ' + m[3] + '() on "' + m[2] + '", a child or teen character. Remove the call.'));
      } else if (kind === 'node') {
        var n = chk[1];
        if (!HARM_NODES[String(n.type)]) return;
        var p = isObj(n.params) ? n.params : {};
        if (n.type === 'appearance.setOutfit' && !nude(p.outfit)) return;
        if (n.type === 'entity.setHostile' && p.value === false) return;
        if (['target', 'entity', 'a', 'b', 'with', 'npc', 'who'].some(function (k) { return typeof p[k] === 'string' && self.isMinorId(p[k]); }))
          F.push(finding('CS014', chk[2], chk[3], 'Node "' + n.type + '" targets a child or teen character. Remove it or change the target.'));
      } else if (kind === 'romance') {
        var ids2 = romanceIds(chk[1], !/relationships$/.test(chk[2]));
        for (var i = 0; i < ids2.length; i++) if (self.isMinorId(ids2[i])) { F.push(finding('CS006', chk[2], chk[3], '"' + ids2[i] + '" is a child or teen character and cannot be part of romance or relationship data.')); break; }
      } else if (kind === 'squad') {
        (chk[1].members || []).forEach(function (mid) { if (typeof mid === 'string' && self.isMinorId(mid)) F.push(finding('CS007', chk[2], chk[3], '"' + mid + '" is a child or teen character and cannot be in a combat squad.')); });
      } else if (kind === 'capture') {
        targets(chk[1]).forEach(function (t) { if (typeof t === 'string' && self.isMinorId(t)) F.push(finding('CS011', chk[2], chk[3], '"' + t + '" is a child or teen character and cannot be captured.')); });
      }
    });
  };
  Scan.prototype.report = function () {
    var uniq = [], seen = {};
    this.findings.forEach(function (f) { var k = f.rule + '|' + f.path + '|' + f.message; if (!seen[k]) { seen[k] = 1; uniq.push(f); } });
    uniq.sort(function (a, b) { var x = (a.severity !== 'block') - (b.severity !== 'block'); if (x) return x; if (a.rule !== b.rule) return a.rule < b.rule ? -1 : 1; return a.path < b.path ? -1 : a.path > b.path ? 1 : 0; });
    var blocks = uniq.filter(function (f) { return f.severity === 'block'; }).length;
    return { policy: S.POLICY, policy_version: S.POLICY_VERSION, lexicon_version: this.lex.version, blocked: blocks > 0, findings: uniq,
      counts: { block: blocks, warn: uniq.length - blocks, texts: this.fields.length, characters: this.chars.length, minors: this.minors().length },
      characters: this.chars.map(function (c) { return { key: c.key, id: c.id, name: c.name, age: c.age, declared: c.declared, inferred: c.inferred, path: c.path }; }) };
  };
  function declaredLangs(w) { var sy = isObj(w) && isObj(w.systems) ? w.systems : null, loc = sy && isObj(sy.locale) ? sy.locale : null; return loc && Array.isArray(loc.languages) ? loc.languages.map(function (x) { return String(x).toLowerCase().slice(0, 2); }) : []; }
  S.scanWorld = function (world) { if (!isObj(world)) throw new SafetyError('world must be an object'); return new Scan(world, S.lexicon(), declaredLangs(world)).run(); };
  S.summary = function (rep) {
    var c = rep.counts;
    if (!rep.blocked) return 'Child-safety check passed (' + c.warn + ' warning' + (c.warn !== 1 ? 's' : '') + ').';
    var first = rep.findings.filter(function (f) { return f.severity === 'block'; })[0];
    return ('Blocked by the child-safety rules (' + c.block + ' problem' + (c.block !== 1 ? 's' : '') + '). ' + first.where + ': ' + first.title + '.').slice(0, 300);
  };

  // ---------------------------------------------------------------- runtime registry + guards
  var AGES = new Map();        // entity id / character id -> locked age group, filled by boot() and adopt()
  S.registry = AGES;
  function idOf(x) { return x == null ? null : typeof x === 'string' ? x : x.id != null ? String(x.id) : null; }
  S.ageOf = function (x) {
    if (x == null) return 'adult';
    if (typeof x === 'object' && x.age && MINOR[x.age]) return x.age;
    if (typeof x === 'object' && x.o && x.o.age && MINOR[x.o.age]) return x.o.age;
    var id = idOf(x);
    if (id != null && AGES.has(id)) return AGES.get(id);
    if (typeof x === 'object') { var r = S.normalizeAge(x.age !== undefined ? x.age : (x.o ? x.o.age : undefined)); if (!r[1]) return 'child'; return r[0] || 'adult'; }
    return 'adult';
  };
  S.isMinor = function (x) { return !!MINOR[S.ageOf(x)]; };
  S.canHarm = function (x) { return !S.isMinor(x); };
  /** Lock an entity's age group for its whole life: scripts, saves and other systems can read it, never change it. */
  S.lockAge = function (e, age) {
    age = age || 'adult';
    var cur = Object.getOwnPropertyDescriptor(e, 'age');
    if (cur && !cur.configurable) return;
    Object.defineProperty(e, 'age', { value: age, writable: false, enumerable: true, configurable: false });
    if (e.id != null) { var prev = AGES.get(String(e.id)); if (!prev || !MINOR[prev] || MINOR[age]) AGES.set(String(e.id), MINOR[prev] ? prev : age); }
  };
  var HARM_WORDS = set(['harm', 'damage', 'kill', 'attack', 'target', 'hostile', 'enemy', 'capture', 'status', 'knockback', 'execute', 'death']);
  var MESSAGES = {
    harm: 'is a child or teen character: it cannot be damaged, killed or attacked',
    target: 'is a child or teen character: it can never be a combat target',
    hostile: 'is a child or teen character: it can never be made hostile or an enemy',
    capture: 'is a child or teen character: it can never be captured',
    romance: 'is a child or teen character: it is never part of romance or intimacy',
    undress: 'is a child or teen character: it can never be undressed or shown nude'
  };
  /** Throw a SafetyError when `action` would harm / target / capture / romance / undress a minor. */
  S.guard = function (action, target, other) {
    var a = String(action || 'harm').toLowerCase();
    var group = HARM_WORDS[a] ? (a === 'target' ? 'target' : a === 'hostile' || a === 'enemy' ? 'hostile' : a === 'capture' ? 'capture' : 'harm') : a === 'romance' || a === 'intimacy' || a === 'affinity' ? 'romance' : a === 'undress' || a === 'nudity' || a === 'outfit' ? 'undress' : 'harm';
    var who = [target].concat(group === 'romance' && other !== undefined ? [other] : []).filter(function (x) { return x != null && S.isMinor(x); });
    if (who.length) {
      var w = who[0], nm = typeof w === 'string' ? w : (w.name || w.id || 'this character');
      throw new SafetyError('Child safety: "' + nm + '" ' + MESSAGES[group] + '. (' + a + ' refused)', { action: a, target: idOf(w) });
    }
    return true;
  };
  S.filterTargets = function (list) { return (list || []).filter(function (e) { return e && !S.isMinor(e); }); };
  /** Runtime text check (scripts, generators, HUD templates). Returns the text or throws. */
  S.assertText = function (text, where) {
    var f = S.scanText(String(text == null ? '' : text), S.minorNames()).filter(function (x) { return x.severity === 'block'; });
    if (f.length) throw new SafetyError('Child safety: text refused (' + f[0].title + ')' + (where ? ' in ' + where : '') + '.', { findings: f });
    return text;
  };
  S.safeText = function (text, fallback) { try { return S.assertText(text); } catch (e) { if (e.safety) { G.warn(e.message); return fallback == null ? '...' : fallback; } throw e; } };
  var NAMES = [];
  S.minorNames = function () { return NAMES; };
  /** Called by Runtime.spawn: normalise + lock the age; minors spawned from data become friendly, from scripts throw. */
  S.adopt = function (e, spec, fromData, rt) {
    spec = spec || {};
    var r = S.normalizeAge(spec.age !== undefined ? spec.age : spec.age_years);
    var age = r[1] ? (r[0] || 'adult') : 'child';
    var known = AGES.get(String(e.id));
    if (known && MINOR[known]) age = known;
    if (!MINOR[age]) { var inf = S.inferAge([spec.desc, spec.description, spec.bio, spec.backstory].filter(function (x) { return typeof x === 'string'; })); if (inf) age = inf; }
    S.lockAge(e, age);
    if (MINOR[age]) {
      if (e.name && NAMES.indexOf(e.name) < 0) NAMES.push(e.name);
      var hostile = e.kind === 'enemy' || e.team === 'enemy' || spec.hostile === true || HOSTILE_ROLES[String(spec.role || '').toLowerCase()];
      if (hostile) {
        if (!fromData) throw new SafetyError('Child safety: "' + e.name + '" is a ' + age + ' and cannot be spawned as an enemy.', { action: 'spawn', target: e.id });
        G.warn('child-safety: "' + e.name + '" (' + age + ') was set up as an enemy; it is a friendly character instead');
      }
      if (hostile || e.kind === 'enemy') { e.kind = 'npc'; }
      e.team = 'neutral';
      e.flags.protected = true;
      e.tags.add('protected');
    }
  };
  /** Clamp for every damage path (environment, falls, scripts): minors never lose health. */
  S.clampDamage = function (target, amount) { return S.isMinor(target) ? 0 : amount; };

  /** Boot check for a world: the same scan as the export gate. {ok, message, report}. Fills the age registry. */
  S.boot = function (world) {
    var rep;
    try { rep = S.scanWorld(world); }
    catch (e) { return { ok: false, message: 'The child-safety check could not run: ' + e.message, report: null }; }
    NAMES = [];
    AGES.clear();
    rep.characters.forEach(function (c) { if (c.id != null) { var prev = AGES.get(c.id); if (!prev || !MINOR[prev]) AGES.set(c.id, c.age); } if (MINOR[c.age] && c.name && NAMES.indexOf(c.name) < 0) NAMES.push(c.name); });
    S.lastReport = rep;
    return { ok: !rep.blocked, message: rep.blocked ? 'This game cannot run: it breaks the Stark World Engine child-safety rules. ' + S.summary(rep) : S.summary(rep), report: rep };
  };
  /** In-place fail-safe for a world about to be played by an engine that does not use GSYS entities (the legacy
      studio runtime): ages written onto objects, child/teen enemies turned into friendly villagers. */
  S.sanitize = function (world) {
    var rep = S.boot(world);
    if (!rep.report) return rep;
    var ages = {}; rep.report.characters.forEach(function (c) { ages[c.key] = c.age; });
    function lock(o, a) { try { Object.defineProperty(o, 'age', { value: a, writable: false, enumerable: true, configurable: false }); } catch (e) { o.age = a; } }
    [world].concat(Array.isArray(world.levels) ? world.levels : []).forEach(function (L, li) {
      (L && Array.isArray(L.objects) ? L.objects : []).forEach(function (o, i) {
        var a = ages['obj:' + li + ':' + i];
        if (!a || !isObj(o)) return;
        lock(o, a);
        if (MINOR[a] && o.t === 'enemy') { o.t = 'npc'; delete o.kind; delete o.patrol; o.text = o.text || 'Hello!'; }
      });
    });
    var sy = isObj(world.systems) ? world.systems : {};
    (Array.isArray(sy.entities) ? sy.entities : []).forEach(function (en, i) { var a = ages['ent:' + i]; if (a && isObj(en)) { lock(en, a); if (MINOR[a] && en.kind === 'enemy') en.kind = 'npc'; } });
    if (isObj(sy.characters)) Object.keys(sy.characters).forEach(function (id) { var a = ages['char:' + id], c = sy.characters[id]; if (a && isObj(c)) { lock(c, a); if (MINOR[a]) { if (c.kind === 'enemy') c.kind = 'npc'; c.hostile = false; c.romanceable = false; } } });
    return rep;
  };
  G.register('safety', 0, function (rt) {
    return {
      init: function () {
        var r = rt.opt.safetyReport || S.boot(rt.world);
        rt.safety = r;
        if (!r.ok) throw new SafetyError(r.message, r.report);
      },
      guard: S.guard, isMinor: S.isMinor, canHarm: S.canHarm, ageOf: S.ageOf, assertText: S.assertText,
      update: function () {
        rt.ents.forEach(function (e) { if (e.age && MINOR[e.age]) { if (e.hp < 1 || e.dead) { e.hp = Math.max(1, e.maxHp || 1); e.dead = false; } if (e.team === 'enemy') e.team = 'neutral'; } });
      }
    };
  });
})(GSYS);
