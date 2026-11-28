"""stark_systems/procgen.py: author-time procedural content, baked into world.json so the creator can edit it.
(The cartridge has runtime twins in js/94_procgen.js for endless content: dungeon floors, streamed region chunks.)

  settlement(seed, x, z)      -> {'objects': studio houses/lamps/npcs, 'characters': {...}, 'schedules': {...}}
  npcs(seed, n, town)         -> people in families: names, ages (children tagged child), jobs, traits, backstories,
                                 daily schedules, and a fetch-quest hook for children (never a combat role)
  dungeon(seed, depth)        -> BSP floor: tile rows ('#' wall), rooms, entry/exit, spawns, loot spots
  region(seed, size, biome)   -> scattered studio objects (trees, rocks, ruins, lamps) with clearings
  bake_settlement(world, ...) -> adds a settlement to a world in place (objects + systems.characters + schedules)

Every generated text is checked with the child-safety checker before it is used.
"""
import math, random

from . import child_safety

SYL = ['al', 'bri', 'cor', 'da', 'el', 'fen', 'gwy', 'ha', 'is', 'jo', 'ka', 'li', 'mo', 'nel', 'or', 'pim', 'qua', 'ro',
       'sa', 'tor', 'ul', 'vi', 'wen', 'ya', 'zed', 'mar', 'tes', 'lin', 'bel', 'dor']
JOBS = ['baker', 'smith', 'farmer', 'fisher', 'herbalist', 'carpenter', 'weaver', 'miller', 'guard', 'innkeeper',
        'hunter', 'scribe', 'potter', 'merchant']
TRAITS = ['cheerful', 'stubborn', 'curious', 'gentle', 'proud', 'quiet', 'restless', 'kind', 'clever', 'cautious',
          'generous', 'superstitious']
BACK = ['Born in {town}, {first} took over the {job} trade from a parent.',
        'Came to {town} after a hard winter in the hills and stayed for the people.',
        'Known in {town} for a {trait} heart and a long memory for stories.',
        'Spent years on the road before settling in {town} as a {job}.',
        'Keeps an old map of the hills and swears there is treasure in the north.']
KID_BACK = ['The youngest in the family; loves the pond and asks endless questions.',
            'Collects shiny stones and knows every cat in {town} by name.',
            'Wants to be a {job} like their parent one day.',
            'Is learning letters from the scribe and reads aloud to anyone who listens.']
KID_WANTS = [('kite', 'Kite', 'My kite is stuck somewhere past the mill!'),
             ('marble', 'Blue marble', 'I lost my lucky marble near the well.'),
             ('kitten', 'Grey kitten', 'Have you seen my kitten? She ran toward the woods.'),
             ('ribbon', 'Red ribbon', 'The wind took my ribbon. It is bright red!')]


def name(r):
    s = r.choice(SYL) + r.choice(SYL) + (r.choice(SYL) if r.random() < 0.3 else '')
    return s.capitalize()


def _safe(text, fallback=''):
    return fallback if child_safety.text_is_blocked(text) else text


def npcs(seed, n=20, town='Hollowbrook', x=0.0, z=0.0, spread=14.0):
    r = random.Random(f'npcs:{seed}')
    out, homes = [], []
    while len(out) < n:
        family = name(r)
        hx, hz = x + r.uniform(-spread, spread), z + r.uniform(-spread, spread)
        homes.append((hx, hz))
        adults, kids = r.randint(1, 2), (r.randint(1, 2) if r.random() < 0.6 else 0)
        parent_job = r.choice(JOBS)
        for k in range(adults + kids):
            if len(out) >= n:
                break
            child = k >= adults
            first = name(r)
            job = None if child else (parent_job if k == 0 else r.choice(JOBS))
            trait = r.choice(TRAITS)
            ctx = {'town': town, 'first': first, 'job': job or parent_job, 'trait': trait}
            back = (r.choice(KID_BACK) if child else r.choice(BACK)).format(**ctx)
            work = (x + r.uniform(-spread, spread), z + r.uniform(-spread, spread))
            home = {'x': round(hx, 1), 'z': round(hz, 1)}
            if child:
                sched = [{'from': 21, 'to': 7, 'at': home, 'do': 'sleep'},
                         {'from': 7, 'to': 12, 'at': {'x': round(x, 1), 'z': round(z, 1)}, 'do': 'play'},
                         {'from': 12, 'to': 13, 'at': home, 'do': 'eat'},
                         {'from': 13, 'to': 18, 'at': {'x': round(x + r.uniform(-5, 5), 1), 'z': round(z + r.uniform(-5, 5), 1)}, 'do': 'play'},
                         {'from': 18, 'to': 21, 'at': home, 'do': 'home'}]
            else:
                sched = [{'from': 22, 'to': 6, 'at': home, 'do': 'sleep'}, {'from': 6, 'to': 8, 'at': home, 'do': 'eat'},
                         {'from': 8, 'to': 17, 'at': {'x': round(work[0], 1), 'z': round(work[1], 1)}, 'do': 'work'},
                         {'from': 17, 'to': 20, 'at': {'x': round(x + 3, 1), 'z': round(z - 3, 1)}, 'do': 'tavern'},
                         {'from': 20, 'to': 22, 'at': home, 'do': 'home'}]
            p = {'id': f'pg{seed}_{len(out)}', 'name': _safe(f'{first} {family}', 'Villager'), 'family': family,
                 'age': 'child' if child else 'adult', 'role': 'child' if child else job, 'job': job, 'traits': [trait],
                 'backstory': _safe(back), 'home': home, 'x': round(hx + r.uniform(-1, 1), 1), 'z': round(hz + 2, 1),
                 'schedule': sched}
            if child:
                p['wants'] = list(r.choice(KID_WANTS))
            out.append(p)
    return out


def settlement(seed, x=0.0, z=0.0, n_people=16, town=None):
    """-> studio objects (houses, lamps, npcs) + systems characters, schedules and kids' fetch quests."""
    r = random.Random(f'settlement:{seed}')
    town = town or (name(r) + r.choice(['ford', 'brook', 'holm', 'stead', 'wick', 'dale']))
    people = npcs(seed, n_people, town, x, z)
    homes = sorted({(p['home']['x'], p['home']['z']) for p in people})
    objs = [{'t': 'house', 'x': hx, 'z': hz, 'rot': round(math.atan2(x - hx, z - hz), 2)} for hx, hz in homes]
    objs += [{'t': 'lamp', 'x': round(x + math.cos(a) * 5, 1), 'z': round(z + math.sin(a) * 5, 1)} for a in (0.4, 2.5, 4.4)]
    chars, schedules, quests, dialogs, items = {}, {}, [], {}, {}
    for p in people:
        objs.append({'t': 'npc', 'x': p['x'], 'z': p['z'], 'id': p['id'], 'name': p['name'], 'age': p['age'],
                     'dialog': p['id'], 'desc': p['backstory']})
        schedules[p['id']] = p['schedule']
        chars[p['id']] = {'name': p['name'], 'age': p['age'], 'role': p['role'], 'schedule': p['id'], 'ai': 'villager',
                          'backstory': p['backstory'], 'traits': p['traits']}
        if p['age'] == 'child':
            iid, iname, line = p['wants']
            qid = f'{p["id"]}_fetch'
            items[f'{iid}_{p["id"]}'] = {'name': iname, 'kind': 'quest'}
            objs.append({'t': 'item', 'x': round(x + r.uniform(-30, 30), 1), 'z': round(z + r.uniform(-30, 30), 1),
                         'item': f'{iid}_{p["id"]}'})
            quests.append({'id': qid, 'title': f'{p["name"].split()[0]}\'s {iname}', 'giver': p['id'],
                           'desc': f'{p["name"]} asked you to find a {iname.lower()}.',
                           'steps': [{'type': 'collect', 'item': f'{iid}_{p["id"]}', 'count': 1, 'consume': True},
                                     {'type': 'talk', 'npc': p['id']}], 'reward': {'xp': 25, 'gold': 3}})
            dialogs[p['id']] = {'start': 'a', 'branches': [{'if': {'done': qid}, 'node': 'thanks'}, {'if': {'active': qid}, 'node': 'wait'}],
                                'nodes': {'a': {'speaker': p['name'], 'text': line, 'choices': [{'text': 'I will look for it.', 'start': qid, 'end': True},
                                                                                                    {'text': 'Not now.', 'end': True}]},
                                          'wait': {'speaker': p['name'], 'text': 'Did you find it yet?'},
                                          'thanks': {'speaker': p['name'], 'text': 'You found it! Thank you!'}}}
        else:
            dialogs[p['id']] = {'start': 'a', 'nodes': {'a': {'speaker': p['name'],
                                                              'text': _safe(f'Welcome to {town}. I am the {p["job"]} here. {p["backstory"]}', 'Hello.')}}}
    return {'town': town, 'objects': objs, 'characters': chars, 'schedules': schedules, 'quests': quests,
            'dialogs': dialogs, 'items': items, 'people': people}


def bake_settlement(world, seed, x=0.0, z=0.0, n_people=16):
    """Add a generated settlement to `world` in place (studio-valid objects + systems data). -> the plan."""
    plan = settlement(seed, x, z, n_people)
    world.setdefault('objects', []).extend(plan['objects'])
    world.setdefault('quests', []).extend(plan['quests'])
    world.setdefault('dialogs', {}).update(plan['dialogs'])
    world.setdefault('items', {}).update(plan['items'])
    sy = world.setdefault('systems', {'version': 1})
    sy.setdefault('characters', {}).update(plan['characters'])
    sy.setdefault('ai', {}).setdefault('schedules', {}).update(plan['schedules'])
    return plan


def dungeon(seed, depth=1, w=None, h=None):
    """BSP dungeon floor. Every floor tile is reachable from the entry (corridors join consecutive rooms + a loop)."""
    r = random.Random(f'dungeon:{seed}:{depth}')
    W, H = w or 48 + min(32, depth * 4), h or 36 + min(20, depth * 2)
    g = [['#'] * W for _ in range(H)]
    leaves = []

    def split(x, y, ww, hh, d):
        if d > 5 or (ww < 16 and hh < 14) or (d > 2 and r.random() < 0.15):
            leaves.append((x, y, ww, hh))
            return
        vert = ww / hh > 1.25 or (hh / ww <= 1.25 and r.random() < 0.5)
        if vert and ww >= 16:
            s = r.randint(int(ww * 0.35), int(ww * 0.65))
            split(x, y, s, hh, d + 1), split(x + s, y, ww - s, hh, d + 1)
        elif hh >= 14:
            s = r.randint(int(hh * 0.35), int(hh * 0.65))
            split(x, y, ww, s, d + 1), split(x, y + s, ww, hh - s, d + 1)
        else:
            leaves.append((x, y, ww, hh))
    split(1, 1, W - 2, H - 2, 0)
    kinds = ['den', 'treasure', 'shrine', 'library', 'barracks', 'well', 'garden', 'crypt']
    rooms = []
    for lx, ly, lw, lh in leaves:
        rw, rh = r.randint(max(4, lw // 2), max(4, lw - 2)), r.randint(max(3, lh // 2), max(3, lh - 2))
        rx, ry = lx + r.randint(0, max(0, lw - rw - 1)), ly + r.randint(0, max(0, lh - rh - 1))
        rooms.append({'x': rx, 'y': ry, 'w': rw, 'h': rh, 'cx': rx + rw // 2, 'cy': ry + rh // 2, 'type': r.choice(kinds)})
        for yy in range(ry, min(H - 1, ry + rh)):
            for xx in range(rx, min(W - 1, rx + rw)):
                g[yy][xx] = '.'
    rooms.sort(key=lambda a: a['cx'] + a['cy'] * 0.5)

    def carve(a, b):
        x, y = a['cx'], a['cy']
        for tx, ty in ((b['cx'], y), (b['cx'], b['cy'])) if r.random() < 0.5 else ((x, b['cy']), (b['cx'], b['cy'])):
            while x != tx:
                x += 1 if x < tx else -1
                g[y][x] = '.' if g[y][x] == '#' else g[y][x]
            while y != ty:
                y += 1 if y < ty else -1
                g[y][x] = '.' if g[y][x] == '#' else g[y][x]
    for i in range(1, len(rooms)):
        carve(rooms[i - 1], rooms[i])
    if len(rooms) > 3:
        carve(rooms[0], rooms[r.randint(2, len(rooms) - 1)])
    start, exit_ = rooms[0], rooms[-1]
    start['type'], exit_['type'] = 'entry', 'stairs'
    spawns, loot = [], []
    for rm in rooms[1:]:
        n = r.randint(2, 3 + depth) if rm['type'] == 'den' else r.randint(0, 2)
        for _ in range(n):
            spawns.append({'x': rm['x'] + 1 + r.randint(0, max(0, rm['w'] - 3)) + 0.5,
                           'z': rm['y'] + 1 + r.randint(0, max(0, rm['h'] - 3)) + 0.5,
                           'kind': r.choice(['grunt', 'archer', 'brute'][:1 + min(2, depth // 2 + 1)])})
        if rm['type'] == 'treasure' or r.random() < 0.25:
            loot.append({'x': rm['cx'] + 0.5, 'z': rm['cy'] + 0.5, 'room': rm['type']})
    return {'w': W, 'h': H, 'rows': [''.join(row) for row in g], 'rooms': rooms, 'start': {'x': start['cx'] + 0.5, 'z': start['cy'] + 0.5},
            'exit': {'x': exit_['cx'] + 0.5, 'z': exit_['cy'] + 0.5}, 'spawns': spawns, 'loot': loot, 'depth': depth}


def region(seed, size=96, biome='forest', clear=((0, 0, 12),)):
    """Scattered studio objects for a region of +-size metres; `clear` = [(x, z, r)] spots kept empty."""
    r = random.Random(f'region:{seed}:{biome}')
    dens = {'forest': (1.4, 0.4, 0.06), 'meadow': (0.35, 0.2, 0.04), 'rocky': (0.2, 1.2, 0.08)}.get(biome, (0.8, 0.4, 0.05))
    area = (size * 2) ** 2 / 400.0
    objs = []
    for t, d in zip(('tree', 'rock', 'ruin'), dens):
        for _ in range(int(d * area)):
            x, z = r.uniform(-size, size), r.uniform(-size, size)
            if any(math.hypot(x - a, z - b) < rr for a, b, rr in clear):
                continue
            objs.append({'t': t, 'x': round(x, 1), 'z': round(z, 1), 's': round(r.uniform(0.8, 1.4), 2)})
    return objs
