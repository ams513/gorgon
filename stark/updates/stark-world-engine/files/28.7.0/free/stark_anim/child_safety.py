"""child_safety.py - the animation side of the Stark World Engine hard child-safety rule:
no harm animations and no intimacy animations can ever be applied to a child character.

Every clip gets a safety class: 'neutral', 'harm' (attacks, weapons, being hit, dying, fighting...) or 'intimacy'
(kissing, caressing, seductive dancing...). The class is declared in clip.meta['safety'] by the importer and can
only be RAISED by keywords, never lowered (a clip named 'Kiss' stays 'intimacy' even if someone marks it neutral).
Names are split into words (camelCase, underscores, digits); short stems must match a whole word (with common
endings: hit, hits, hitting), long stems match word starts (execut -> execute, execution).
The cartridge runtime (runtime/stark_anim.js, StarkAnim.safetyClassOf) uses the same lists; tests keep the two
in sync. If stark_systems/CHILD_SAFETY.md exists, refusals point to it, and its machine-readable block
(```child-safety-anim {"block_keywords": [...]} ```) can ADD blocked words - it can never remove these defaults.
"""
import json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
POLICY_MD = os.path.join(ROOT, 'stark_systems', 'CHILD_SAFETY.md')

# keep in sync with HARM_STEMS / INTIMACY_STEMS in runtime/stark_anim.js (tests check this)
HARM_STEMS = ['attack', 'punch', 'kick', 'jab', 'uppercut', 'slash', 'stab', 'sword', 'axe', 'shoot', 'pistol', 'rifle',
              'gun', 'aim', 'reload', 'weapon', 'strike', 'hit', 'hurt', 'damage', 'death', 'die', 'dying', 'dead',
              'kill', 'fight', 'combat', 'brawl', 'choke', 'strangl', 'execut', 'stomp', 'headbutt', 'grapple', 'knock',
              'wound', 'injur', 'blood', 'tortur', 'slap', 'smack', 'shove', 'tackle', 'arrow', 'spear', 'dagger',
              'knife', 'bomb', 'grenade', 'explod', 'assault', 'murder', 'stun']
INTIMACY_STEMS = ['kiss', 'caress', 'cuddl', 'flirt', 'seduc', 'intimat', 'romanc', 'sensual', 'erotic', 'twerk',
                  'undress', 'lingerie', 'grope', 'fondl', 'makeout', 'lapdance', 'striptease', 'poledance']
INTIMACY_PHRASES = ['lap dance', 'pole dance', 'make out', 'strip tease', 'slow dance']
SHORT_ENDINGS = ('', 's', 'ed', 'ing', 'ting', 'er', 'ers', 'd')
CLASSES = ('neutral', 'harm', 'intimacy')
BLOCKED_FOR_CHILD = ('harm', 'intimacy')
CHILD_WORDS = {'child', 'children', 'kid', 'kids', 'toddler', 'baby', 'infant', 'boy', 'girl', 'minor', 'teen',
               'teenager', 'youth', 'schoolkid', 'schoolchild'}


class ChildSafetyError(PermissionError):
    pass


def words(text):
    text = re.sub(r'([a-z])([A-Z])', r'\1 \2', str(text))
    return [w for w in re.split(r'[^a-z]+', text.lower()) if w]


def _stem_hit(ws, stems):
    for w in ws:
        for st in stems:
            if len(st) <= 3:
                if any(w == st + e for e in SHORT_ENDINGS) or (st == 'hit' and w == 'hitting'):
                    return True
            elif w.startswith(st):
                return True
    return False


def _policy():
    if not os.path.isfile(POLICY_MD):
        return None, []
    try:
        with open(POLICY_MD, encoding='utf-8') as f:
            txt = f.read()
    except OSError:
        return None, []
    m = re.search(r'```child-safety-anim\s*(\{.*?\})\s*```', txt, re.S)
    extra = []
    if m:
        try:
            extra = [str(x).lower() for x in json.loads(m.group(1)).get('block_keywords', [])]
        except ValueError:
            extra = []
    return txt, extra


def classify(name, tags=(), declared=None):
    """-> 'neutral' | 'harm' | 'intimacy' (a declared class can be raised by keywords, never lowered)."""
    text = f"{name} {' '.join(map(str, tags or []))}"
    ws = words(text)
    joined = ' '.join(ws)
    if declared in BLOCKED_FOR_CHILD:
        return declared
    if _stem_hit(ws, INTIMACY_STEMS) or any(p in joined for p in INTIMACY_PHRASES):
        return 'intimacy'
    _, extra = _policy()
    if _stem_hit(ws, HARM_STEMS) or (extra and _stem_hit(ws, extra)):
        return 'harm'
    return declared if declared in CLASSES else 'neutral'


def classify_clip(clip):
    c = classify(clip.name, clip.tags, clip.meta.get('safety'))
    clip.meta['safety'] = c
    return c


def _systems_age(value):
    """stark_systems.child_safety.normalize_age when that module ships: -> 'child'|'teen'|'adult'|None."""
    try:
        import sys
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        from stark_systems.child_safety import normalize_age
    except Exception:  # noqa: BLE001 - optional sibling module
        return None
    try:
        group, ok = normalize_age(value)
    except Exception:  # noqa: BLE001
        return None
    return group if ok else 'child'  # an unreadable age is treated as a minor, like the systems runtime


def is_child(character):
    """True for minors (child OR teen). character: dict with 'child' / 'age' / 'tags' / 'name' (a world.json NPC or
    player entry). child=True, a child/teen age group, or an age under 18 always wins; child-like words in the name
    or tags count unless child is explicitly False."""
    if not isinstance(character, dict):
        return False
    if character.get('child') is True:
        return True
    age = character.get('age')
    sys_group = _systems_age(age) if age is not None else None
    if sys_group in ('child', 'teen'):
        return True
    if isinstance(age, str) and age.strip().lower() in ('child', 'teen', 'kid', 'teenager', 'minor'):
        return True
    try:
        if age is not None and float(age) < 18:
            return True
    except (TypeError, ValueError):
        pass
    if character.get('child') is False:
        return False
    text = ' '.join([str(character.get('name', '')), ' '.join(map(str, character.get('tags', []) or [])),
                     str(character.get('role', ''))])
    return bool(CHILD_WORDS & set(words(text)))


def check_apply(clip, character):
    """Raise ChildSafetyError when `clip` may not be applied to `character`; return the clip's class otherwise."""
    if hasattr(clip, 'name'):
        c = classify(clip.name, clip.tags, clip.meta.get('safety'))
    else:
        c = classify(str(clip))
    if c in BLOCKED_FOR_CHILD and is_child(character):
        policy, _ = _policy()
        why = f"'{getattr(clip, 'name', clip)}' is a {c} animation; it can never be applied to a child character"
        if policy:
            why += ' (studio rule: stark_systems/CHILD_SAFETY.md)'
        raise ChildSafetyError(why)
    return c


def filter_for(character, clips):
    """Only the clips that may be applied to this character."""
    if not is_child(character):
        return list(clips)
    return [c for c in clips if classify(c.name, c.tags, c.meta.get('safety')) not in BLOCKED_FOR_CHILD]
