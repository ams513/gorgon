"""Stark World Engine Classroom mode (part of the Stark Education Suite).

  profile    is_classroom(data_dir)      the classroom profile, set in config.json: "classroom_profile": true
  packs      list_packs() / pack_world() teacher lesson packs: starter project + goal + steps + "check your work"
  lint       lint_world(world)           plain-language "things worth a look" for a student's level
  reflection reflection_text(...)        the short reflection file that is exported next to the student's game

Everything is local. Nothing in this package opens a socket, runs student code or stores student data anywhere
except the files the student exports into their own My_Games folder."""
import datetime
import json
import os

from .packs import get_pack, list_packs, pack_world, solved_debug_world  # noqa: F401

PROFILE_KEY = 'classroom_profile'
_ON = (True, 1, '1', 'true', 'yes', 'on', 'classroom', 'student')

# Studio API paths that reach the network or are publishing helpers: refused while the profile is on.
BLOCKED_PATHS = ('/api/updates', '/api/download_godot')
# What the profile switches off, shown to the teacher (GET /api/classroom/profile) and in the Teacher Guide.
HIDDEN_FEATURES = ('publishing helpers (publishers.py)', 'YouTube upload', 'phone hub (creator_hub.py)',
                   'in-app updates', 'Godot download', 'itch.io and channel statistics')


class ClassroomLocked(PermissionError):
    """Raised when a feature is switched off by the classroom profile."""
    errno = None


def is_classroom(data_dir):
    """True when <data_dir>/config.json has "classroom_profile": true. Read-only; a missing or broken file = off."""
    try:
        with open(os.path.join(data_dir, 'config.json'), encoding='utf-8-sig') as f:
            v = json.load(f).get(PROFILE_KEY)
    except (OSError, ValueError, AttributeError):
        return False
    return (v.strip().lower() if isinstance(v, str) else v) in _ON and v is not False


def require_off(feature, data_dir):
    """Raise ClassroomLocked if the classroom profile is on."""
    if is_classroom(data_dir):
        raise ClassroomLocked(f'{feature} is switched off in classroom mode')


def profile(data_dir):
    on = is_classroom(data_dir)
    return {'classroom': on, 'hidden': list(HIDDEN_FEATURES) if on else [],
            'student_data': 'Nothing is sent anywhere. Games and reflections are files in this computer\'s My_Games folder.'}


# ---------------- "Check my level" ----------------
def _levels(w):
    return [w] + [L for L in (w.get('levels') or []) if isinstance(L, dict)]


def _starts(dialogs):
    """Quest ids that some dialogue line or choice starts."""
    out = set()
    for d in dialogs.values():
        for n in (d.get('nodes') or {}).values():
            for a in [n.get('do') or {}] + list(n.get('choices') or []):
                if isinstance(a, dict) and a.get('start'):
                    out.add(a['start'])
    return out


def lint_world(w):
    """-> [{'code', 'where', 'text'}]. Looks for things that stop a level being finishable. Never changes the world."""
    lv = _levels(w)
    objs = [(o, L.get('name') or 'level') for L in lv for o in (L.get('objects') or []) if isinstance(o, dict)]
    dialogs, quests, items = {}, [], {}
    for L in lv:
        dialogs.update(L.get('dialogs') or {})
        quests += [q for q in (L.get('quests') or []) if isinstance(q, dict)]
        items.update(L.get('items') or {})
    out = []

    def supply(item):
        n = sum(1 for L in lv for P in [L.get('player') or {}] for x in (P.get('items') or []) if x == item)
        for o, _ in objs:
            if o.get('t') in ('item', 'chest') and o.get('item') == item:
                n += int(o.get('n') or 1)
            elif o.get('t') == 'crystal' and item == 'crystal':
                n += 1
        n += sum(1 for q in quests for x in ((q.get('reward') or {}).get('items') or []) if x == item)
        for d in dialogs.values():
            for nd in (d.get('nodes') or {}).values():
                for a in [nd.get('do') or {}] + list(nd.get('choices') or []):
                    g = a.get('give') if isinstance(a, dict) else None
                    n += sum(1 for x in (g if isinstance(g, list) else [g]) if x == item)
        return n

    for o, where in objs:
        if o.get('t') == 'door':
            key = o.get('key') or 'key'
            if supply(key) < 1:
                nm = (items.get(key) or {}).get('name') or key
                out.append({'code': 'door_key', 'where': where, 'text':
                            f'A locked door needs "{nm}" but nothing in the game gives it out.'})
        if o.get('t') == 'npc' and o.get('dialog') and o['dialog'] not in dialogs:
            out.append({'code': 'dialog_missing', 'where': where, 'text':
                        f'{o.get("name") or o.get("id") or "A person"} talks using the dialogue "{o["dialog"]}", '
                        'which does not exist.'})
    npc_ids = {o.get('id') for o, _ in objs if o.get('t') == 'npc'}
    started, nexts = _starts(dialogs), {q.get('next') for q in quests}
    for q in quests:
        qn = q.get('title') or q.get('id')
        for s in q.get('steps') or []:
            if s.get('type') == 'collect' and s.get('item'):
                need, have = int(s.get('count') or 1), supply(s['item'])
                if have < need:
                    out.append({'code': 'supply', 'where': qn, 'text':
                                f'Quest "{qn}" needs {need} of "{s["item"]}" but only {have} can be found.'})
        if q.get('giver') and q['giver'] not in npc_ids:
            out.append({'code': 'giver_missing', 'where': qn, 'text':
                        f'Quest "{qn}" is given by "{q["giver"]}", but no person with that id is in the game.'})
        if not q.get('auto') and q.get('id') not in started and q.get('id') not in nexts:
            out.append({'code': 'quest_unstarted', 'where': qn, 'text':
                        f'Quest "{qn}" never starts: it is not automatic, no dialogue starts it, and no quest leads to it.'})
    if quests and not any(q.get('final') for q in quests):
        out.append({'code': 'no_ending', 'where': 'game', 'text':
                    'No quest is marked as the final one, so finishing the quests does not end the game.'})
    for L in lv:
        if L.get('mode') == '2d' and any('D' in r for r in L.get('tiles') or []) \
                and not any('k' in r for r in L.get('tiles') or []):
            out.append({'code': 'door_key', 'where': L.get('name') or 'level', 'text':
                        'This 2D level has a locked door (D) but no key (k).'})
    return out


# ---------------- reflection file ----------------
def _clip(v, n):
    return ' '.join(str(v or '').split())[:n] if not isinstance(v, list) else ''


def reflection_text(world, answers=None, student='', pack=None, issues=None, when=None):
    """A short plain-text reflection to hand in with the game. Student answers are clipped, nothing is sent anywhere."""
    when = when or datetime.date.today().isoformat()
    lv = _levels(world)
    count = lambda k: sum(len(L.get(k) or []) if k != 'items' else len(L.get(k) or {}) for L in lv)
    prompts = list((pack or {}).get('reflect') or ['What did you build?', 'What was hardest, and how did you solve it?',
                                                   'What would you change next?'])
    answers = answers if isinstance(answers, list) else []
    lines = [f'Reflection: {world.get("name", "My game")}', f'Student: {_clip(student, 80) or "(not given)"}',
             f'Date: {when}']
    if pack:
        lines += [f'Lesson: {pack["title"]}', f'Goal: {pack["goal"]}']
    lines += ['', 'About the game (counted from the saved game):',
              f'  Levels: {len(lv)}   Objects: {sum(len(L.get("objects") or []) for L in lv)}   '
              f'Quests: {count("quests")}   Custom items: {count("items")}',
              f'  Checker: {"no problems found" if not issues else str(len(issues)) + " thing(s) still worth a look"}']
    for i in issues or []:
        lines.append(f'    - {i["text"]}')
    lines.append('')
    for n, q in enumerate(prompts):
        a = _clip(answers[n], 1500) if n < len(answers) else ''
        lines += [f'{n + 1}. {q}', '   ' + (a or '(no answer)'), '']
    lines.append('Free write: ' + (_clip(answers[len(prompts)], 2000) if len(answers) > len(prompts) else '(none)'))
    lines += ['', 'This file was written on this computer. Nothing was uploaded.']
    return '\n'.join(lines) + '\n'
