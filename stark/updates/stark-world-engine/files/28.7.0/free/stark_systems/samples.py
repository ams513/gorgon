"""stark_systems/samples.py: nine small playable games that prove the genre systems.

  python -m stark_systems.samples [--out DIR]      write <genre>.world.json + <genre>.html + index.html (default:
                                                    stark_systems/samples/)

Every sample is a valid studio world (studio_app.validate_world passes: studio object types only, studio quest /
dialogue / item formats) plus a world['systems'] block; the .html files are one-file cartridges played by the Stark World Engine
Systems reference player (offline, no network). Content is original.
"""
import json, math, os, random, re, sys

from . import bundle, child_safety

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'samples')


def _o(t, x, z, **kw):
    d = {'t': t, 'x': round(x, 2), 'z': round(z, 2)}
    d.update(kw)
    return d


def _base(name, mood, story, mode='3d', seed=7):
    return {'name': name, 'mode': mode, 'mood': mood, 'seed': seed, 'story': story, 'objects': [], 'version': 2,
            'author': 'Stark World Engine Systems sample'}


def _scatter(r, t, n, cx, cz, spread, avoid=None, **kw):
    out = []
    while len(out) < n:
        x, z = cx + r.uniform(-spread, spread), cz + r.uniform(-spread, spread)
        if avoid and any(math.hypot(x - a, z - b) < rr for a, b, rr in avoid):
            continue
        out.append(_o(t, x, z, s=round(r.uniform(0.8, 1.3), 2), **kw))
    return out


# ------------------------------------------------------------------ 1. action RPG
def action_rpg():
    r = random.Random(11)
    w = _base('Emberfall Vale', 'Alpine Sunset', 'Bandits have dug in east of Emberfall. Help the village, earn the '
              'smith\'s best blade, and end the Bandit Chief\'s raids.')
    village = [(0, 0, 9)]
    w['objects'] += [_o('house', math.cos(a) * 11, math.sin(a) * 11, rot=round(-a + math.pi / 2, 2)) for a in
                     [k * 1.25 for k in range(5)]]
    w['objects'] += [_o('lamp', x, z) for x, z in ((4, 4), (-4, 4), (4, -4), (-4, -4))]
    w['objects'] += _scatter(r, 'tree', 45, 26, 6, 18, village + [(30, -18, 8)]) + _scatter(r, 'tree', 20, -24, 10, 12, village)
    w['objects'] += _scatter(r, 'rock', 14, 10, 22, 16, village)
    w['objects'] += [_o('npc', -3, 2, id='elder', name='Elder Maren', dialog='elder', col='#c9a0dc'),
                     _o('npc', 6, -2, id='smith', name='Bram the Smith', dialog='smith', col='#d48c5a'),
                     _o('npc', 2, 6, id='wren', name='Wren', dialog='wren', age='child', col='#9fd3ff',
                        desc='A cheerful kid who has lost her kitten.'),
                     _o('npc', -6, -4, id='captain', name='Captain Ilse', dialog='captain', col='#8fb3a0')]
    camp = (32, -18)
    w['objects'].append(_o('ruin', *camp, s=1.3))
    bandits = []
    for i, (dx, dz, kind) in enumerate(((0, 3, 'grunt'), (3, 1, 'grunt'), (-3, 1, 'grunt'), (2, -3, 'archer'),
                                         (-2, -3, 'archer'), (4, -1, 'grunt'))):
        bid = f'bandit{i + 1}'
        bandits.append(bid)
        w['objects'].append(_o('enemy', camp[0] + dx, camp[1] + dz, id=bid, name='Bandit', kind=kind, hp=4, patrol=4))
    w['objects'].append(_o('enemy', camp[0] + 5, camp[1] + 4, id='bandit_chief', name='Bandit Chief', kind='brute', hp=14,
                           patrol=2))
    w['objects'].append(_o('chest', camp[0] - 4, camp[1] + 4, item='potion', gold=25))
    w['objects'].append(_o('item', -22, 14, item='kitten'))
    w['objects'] += [_o('item', 18 + r.uniform(-6, 6), 16 + r.uniform(-6, 6), item='herb') for _ in range(8)]
    w['objects'] += [_o('item', 12 + r.uniform(-4, 4), 26 + r.uniform(-4, 4), item='iron_ore') for _ in range(6)]
    w['items'] = {'kitten': {'name': "Wren's kitten", 'kind': 'quest', 'desc': 'Small, grey and very cross.'},
                  'herb': {'name': 'Moonherb', 'kind': 'quest', 'col': '#b6f5c9'},
                  'iron_ore': {'name': 'Iron ore', 'kind': 'quest', 'col': '#9aa0aa'},
                  'potion': {'name': 'Healing potion', 'kind': 'consumable', 'heal': 4},
                  'iron_sword': {'name': 'Emberfall blade', 'kind': 'equip', 'slot': 'weapon', 'atk': 2},
                  'leather': {'name': 'Leather jerkin', 'kind': 'equip', 'slot': 'armor', 'def': 1}}
    w['dialogs'] = {
        'elder': {'start': 'a', 'branches': [{'if': {'done': 'q_chief'}, 'node': 'end'}], 'nodes': {
            'a': {'speaker': 'Elder Maren', 'text': 'Welcome to Emberfall, traveller. The bandits in the east grow bolder '
                  'every night. Captain Ilse needs a strong arm; the job board by the well always has work.',
                  'choices': [{'text': 'Tell me about the old shrine.', 'goto': 'shrine'}, {'text': 'I will help.', 'end': True}]},
            'shrine': {'speaker': 'Elder Maren', 'text': 'It stands north of the mill. Touch the stones once and they '
                       'remember you. Few remember the shrine.'},
            'end': {'speaker': 'Elder Maren', 'text': 'The roads are safe again. Emberfall will sing about you for years.'}}},
        'smith': {'start': 'a', 'nodes': {
            'a': {'speaker': 'Bram the Smith', 'text': 'Need gear? Bring me three lumps of iron ore and I will show you how '
                  'to forge a proper blade at my anvil.',
                  'choices': [{'text': 'Show me your wares.', 'shop': 'forge_shop', 'end': True},
                              {'text': 'I will find the ore.', 'start': 'q_blade', 'end': True},
                              {'text': 'Later.', 'end': True}]}}},
        'wren': {'start': 'a', 'branches': [{'if': {'done': 'q_kitten'}, 'node': 'thanks'}, {'if': {'active': 'q_kitten'}, 'node': 'wait'}], 'nodes': {
            'a': {'speaker': 'Wren', 'text': 'Have you seen my kitten? She ran off toward the west woods and I am not '
                  'allowed past the fence!', 'choices': [{'text': 'I will find her.', 'start': 'q_kitten', 'end': True},
                                                        {'text': 'Not right now.', 'end': True}]},
            'wait': {'speaker': 'Wren', 'text': 'She is grey and very small. She likes the big oak by the stones.'},
            'thanks': {'speaker': 'Wren', 'text': 'You found her! She is purring. You are the best grown-up in the whole vale.'}}},
        'captain': {'start': 'a', 'branches': [{'if': {'done': 'q_bandits'}, 'node': 'chief'}, {'if': {'active': 'q_bandits'}, 'node': 'wait'}], 'nodes': {
            'a': {'speaker': 'Captain Ilse', 'text': 'Their camp is east, past the ridge. Thin their numbers and I will '
                  'pay you from the guard purse.', 'choices': [{'text': 'Consider it done.', 'start': 'q_bandits', 'end': True},
                                                              {'text': 'Not yet.', 'end': True}]},
            'wait': {'speaker': 'Captain Ilse', 'text': 'Watch their archers. They will try to come at you from the sides.'},
            'chief': {'speaker': 'Captain Ilse', 'text': 'Only the Chief is left. End this, and Emberfall can sleep.',
                      'choices': [{'text': 'I am going.', 'start': 'q_chief', 'end': True}]}}}}
    w['quests'] = [{'id': 'q_bandits', 'title': 'Thin the Bandits', 'giver': 'captain', 'desc': 'Defeat four bandits at the '
                    'eastern camp.', 'steps': [{'type': 'defeat', 'kind': 'any', 'count': 4},
                                               {'type': 'talk', 'npc': 'captain', 'text': 'Report to Captain Ilse'}],
                    'reward': {'xp': 120, 'gold': 40}},
                   {'id': 'q_chief', 'title': 'The Bandit Chief', 'giver': 'captain', 'final': True,
                    'desc': 'Defeat the Bandit Chief.', 'steps': [{'type': 'defeat', 'id': 'bandit_chief'}], 'reward': {'xp': 250, 'gold': 80}}]
    pois = {'old_mill': {'x': -18, 'z': -16, 'name': 'the Old Mill'}, 'north_ridge': {'x': 4, 'z': -30, 'name': 'the North Ridge'},
            'shrine': {'x': -14, 'z': -26, 'name': 'the Old Shrine'}, 'camp': {'x': camp[0], 'z': camp[1], 'name': 'the bandit camp'},
            'west_woods': {'x': -24, 'z': 12, 'name': 'the West Woods'}}
    w['systems'] = {
        'version': 1, 'genre': 'action_rpg', 'player': 'gsys', 'locale': {'languages': ['en']},
        'controller': {'type': 'top_down', 'speed': 6.5},
        'camera': {'rig': 'top_down', 'scale': 30, 'intro': 'intro',
                   'cinematics': {'intro': {'duration': 6, 'tracks': [
                       {'type': 'letterbox', 'at': 0, 'to': 1}, {'type': 'camera', 'at': 0, 'x': -14, 'z': -26},
                       {'type': 'subtitle', 'at': 0.3, 'dur': 2.6, 'text': 'Emberfall Vale. The shrine still remembers.'},
                       {'type': 'camera', 'at': 3.5, 'x': 0, 'z': 0, 'ease': 'inOut'},
                       {'type': 'subtitle', 'at': 3.2, 'dur': 2.6, 'text': 'But the village needs a hero tonight.'},
                       {'type': 'letterbox', 'at': 5.8, 'to': 0}]}}},
        'characters': {'elder': {'name': 'Elder Maren', 'age': 'adult', 'faction': 'villagers'},
                       'smith': {'name': 'Bram the Smith', 'age': 'adult', 'faction': 'villagers', 'shop': 'forge_shop'},
                       'wren': {'name': 'Wren', 'age': 'child', 'faction': 'villagers', 'schedule': 'wren_day'},
                       'captain': {'name': 'Captain Ilse', 'age': 'adult', 'faction': 'guards'},
                       'bandit_chief': {'name': 'Bandit Chief', 'age': 'adult', 'faction': 'bandits', 'loot': 'bandit', 'xp': 120}},
        'entities': [{'kind': 'station', 'station': 'forge', 'name': 'Forge', 'x': 8, 'z': -3},
                     {'kind': 'board', 'board': True, 'name': 'Job Board', 'x': 1.5, 'z': -1.5},
                     {'id': 'shrine', 'kind': 'shrine', 'name': 'Old Shrine', 'x': -14, 'z': -26, 'interact': True}],
        'items': {'herb': {'name': 'Moonherb', 'kind': 'material', 'value': 3}, 'iron_ore': {'name': 'Iron ore', 'kind': 'material', 'value': 5},
                  'potion': {'name': 'Healing potion', 'kind': 'consumable', 'use': {'heal': 4}, 'value': 8},
                  'iron_sword': {'name': 'Emberfall blade', 'kind': 'equip', 'equip': {'slot': 'weapon', 'atk': 2}, 'value': 60, 'stack': 1},
                  'leather': {'name': 'Leather jerkin', 'kind': 'equip', 'equip': {'slot': 'armor', 'def': 1}, 'value': 30, 'stack': 1}},
        'inventory': {'slots': 24, 'start': {'potion': 2}},
        'recipes': {'potion': {'in': {'herb': 2}, 'out': {'potion': 1}},
                    'iron_sword': {'in': {'iron_ore': 3}, 'out': {'iron_sword': 1}, 'station': 'forge', 'time': 1.5}},
        'loot_tables': {'bandit': {'rolls': 1, 'entries': [{'item': 'potion', 'w': 2}, {'item': 'iron_ore', 'w': 3, 'min': 1, 'max': 2}], 'gold': [3, 9]},
                        'grunt': {'rolls': 1, 'entries': [{'item': 'herb', 'w': 3}, {'item': 'iron_ore', 'w': 2}], 'gold': [1, 5]},
                        'archer': {'rolls': 1, 'entries': [{'item': 'potion', 'w': 1}, {'item': 'herb', 'w': 2}], 'gold': [2, 6]}},
        'combat': {'player_weapon': 'sword',
                   'abilities': {'whirl': {'cooldown': 4, 'cost': {'stamina': 4}, 'effect': {'type': 'aoe', 'amount': 2, 'radius': 3.2}},
                                 'mend': {'cooldown': 10, 'cost': {'mana': 3}, 'effect': {'type': 'heal', 'amount': 4}}}},
        'input': {'abilities': ['whirl', 'mend']},
        'ai': {'perception': {'sight': 10, 'fov': 140, 'hearing': 8},
               'squads': {'bandits': {'members': bandits, 'leader': 'bandit1', 'formation': 'wedge', 'tactic': 'flank'}},
               'schedules': {'wren_day': [{'from': 7, 'to': 19, 'at': {'x': 2, 'z': 6}, 'do': 'play'},
                                          {'from': 19, 'to': 7, 'at': {'x': 10, 'z': 4}, 'do': 'sleep'}]},
               'pois': pois},
        'progression': {'xp_curve': {'base': 40, 'exp': 1.4},
                        'skill_trees': {'warrior': {'name': 'Warrior', 'nodes': {
                            'power': {'name': 'Power strike (+1 attack)', 'cost': 1, 'effect': {'stat': 'atk', 'add': 1}},
                            'tough': {'name': 'Toughness (+1 defence)', 'cost': 1, 'requires': ['power'], 'effect': {'stat': 'def', 'add': 1}},
                            'fury': {'name': 'Fury (+25% attack)', 'cost': 2, 'requires': ['tough'], 'level': 4, 'effect': {'stat': 'atk', 'mult': 1.25}}}}},
                        'perks': {'veteran': {'name': 'Veteran', 'auto': True, 'requires': {'level': 3}, 'effect': {'stat': 'def', 'add': 1}}}},
        'quests': [{'id': 'q_kitten', 'title': 'The Lost Kitten', 'giver': 'wren', 'desc': "Find Wren's kitten in the West Woods.",
                    'steps': [{'type': 'collect', 'item': 'kitten', 'count': 1, 'consume': True},
                              {'type': 'talk', 'npc': 'wren', 'text': 'Bring the kitten back to Wren'}],
                    'reward': {'xp': 40, 'gold': 5, 'rep': {'villagers': 10}}},
                   {'id': 'q_blade', 'title': 'A Proper Blade', 'giver': 'smith', 'desc': 'Gather iron ore and forge a blade at the anvil.',
                    'steps': [{'type': 'collect', 'item': 'iron_ore', 'count': 3}, {'type': 'craft', 'item': 'iron_sword'}],
                    'reward': {'xp': 60}}],
        'quests_radiant': {'pools': {'enemies': [{'id': 'grunt', 'name': 'Bandit'}, {'id': 'archer', 'name': 'Bandit Archer'}],
                                     'items': [{'id': 'herb', 'name': 'Moonherb'}, {'id': 'iron_ore', 'name': 'Iron ore'}],
                                     'places': [{'id': k, 'name': v['name']} for k, v in pois.items()],
                                     'givers': ['elder', 'captain', 'smith']}},
        'genre_data': {'board': True, 'board_size': 3, 'respawn': {'x': 0, 'z': 3}},
        'factions': {'villagers': {'name': 'Emberfall', 'relations': {'bandits': -80}, 'members': ['elder', 'smith', 'wren', 'captain'],
                                   'ranks': [{'at': 0, 'name': 'Stranger'}, {'at': 20, 'name': 'Friend'}, {'at': 50, 'name': 'Hero'}]},
                     'guards': {'name': 'Vale Guard', 'relations': {'bandits': -100}, 'members': ['captain']},
                     'bandits': {'name': 'Ash Bandits', 'relations': {'villagers': -80}, 'members': bandits + ['bandit_chief'], 'hostile_below': 100}},
        'economy': {'start': 20, 'shops': {'forge_shop': {'name': "Bram's Forge", 'owner': 'smith', 'stock': [
            {'item': 'potion', 'qty': 6, 'price': 8}, {'item': 'leather', 'qty': 1, 'price': 30}]}}},
        'environment': {'day_length': 240, 'start_hour': 9, 'weather': {'start': 'clear', 'change_every': [60, 120], 'pattern': [
            {'type': 'clear', 'w': 3}, {'type': 'rain', 'w': 1, 'min': 0.6, 'max': 1}, {'type': 'fog', 'w': 1, 'min': 0.5, 'max': 0.9}]}},
        'save': {'slots': 3, 'autosave_s': 90},
        'zones': [{'id': 'old_mill', 'x': -18, 'z': -16, 'r': 5, 'label': 'the Old Mill'}],
        'scripts': [{'id': 'dusk_wolves', 'source': (
            "// Wolves come out of the woods at dusk and slink back at dawn.\n"
            "var wolves = [];\n"
            "on('dusk', function () {\n"
            "  for (var i = 0; i < 3; i++) wolves.push(world.spawn({kind: 'enemy', name: 'Wolf', x: 30 + i * 2, z: 12, hp: 3, team: 'enemy', ai: 'melee', loot: 'grunt'}));\n"
            "  ui.toast('Wolves howl in the east woods...');\n"
            "});\n"
            "on('dawn', function () { wolves.forEach(function (w) { if (!w.dead) w.despawn(); }); wolves = []; });\n"
            "function onEnterZone(ev) { if (ev.zone === 'old_mill') ui.toast('The old mill creaks in the wind.'); }\n")}],
        'graphs': [{'id': 'shrine_blessing', 'entity': 'shrine', 'nodes': [
            {'id': 'n1', 'type': 'event.interact'}, {'id': 'n2', 'type': 'flow.once'},
            {'id': 'n3', 'type': 'ui.toast', 'params': {'text': 'The shrine stones glow. You feel stronger.'}},
            {'id': 'n4', 'type': 'progression.award', 'params': {'xp': 25}},
            {'id': 'n5', 'type': 'combat.heal', 'params': {'target': 'player', 'amount': 10}}],
            'links': [{'from': 'n1', 'out': 'exec', 'to': 'n2'}, {'from': 'n2', 'out': 'next', 'to': 'n3'},
                      {'from': 'n3', 'out': 'next', 'to': 'n4'}, {'from': 'n4', 'out': 'next', 'to': 'n5'}]}],
        'audio': {'music': {'explore': 'gentle', 'combat': 'drums'}},
    }
    w['spawn'] = {'x': 0, 'z': 3}
    return w


# ------------------------------------------------------------------ 2. platformer
def platformer():
    W, H = 96, 15
    rows = [['.'] * W for _ in range(H)]
    ground = H - 2
    for x in range(W):
        if x not in range(18, 21) and x not in range(41, 44) and x not in range(66, 69):
            rows[ground][x] = '#'
            rows[ground + 1][x] = '#'
    for x0, y, n in ((8, 10, 4), (24, 9, 5), (34, 8, 3), (48, 10, 4), (56, 7, 5), (74, 9, 4), (82, 8, 3)):
        for x in range(x0, x0 + n):
            rows[y][x] = '='
    for x0, y in ((10, 9), (11, 9), (26, 8), (27, 8), (28, 8), (35, 7), (49, 9), (58, 6), (59, 6), (75, 8), (76, 8), (88, 12)):
        rows[y][x0] = 'o'
    for x in (30, 31, 52, 61, 62, 79):
        rows[ground - 1][x] = '^'
    for x in (15, 38, 54, 71, 85):
        rows[ground - 1][x] = 'e'
    rows[ground - 1][2] = 'P'
    rows[ground - 1][46] = 'C'
    rows[ground - 1][93] = 'G'
    rows[9][19] = 'M'
    rows[9][42] = 'M'
    rows[10][67] = 'M'
    for y in range(5, ground):
        rows[y][W - 1] = '#'
    gs = [''.join(r) for r in rows]
    w = _base('Sky Spire Dash', 'Sunken Ruins', 'Run, jump and stomp across the floating ruins to the golden flag. '
              'Coyote time and jump buffering make every jump feel fair.', mode='2d', seed=3)
    w['tiles'] = [re.sub('[M=]', '.', r) for r in gs]           # the studio engine's own 2D tile set
    w['systems'] = {'version': 1, 'genre': 'platformer', 'player': 'gsys', 'locale': {'languages': ['en']},
                    'controller': {'type': 'side_scroll', 'speed': 8.5, 'jump': 15.5, 'coyote': 0.1, 'buffer': 0.12},
                    'physics': {'mode': 'side', 'gravity': 46, 'tiles': {'rows': gs, 'solid': '#', 'oneway': '='}},
                    'camera': {'rig': 'side', 'scale': 34, 'deadzone': [1.5, 1.5]},
                    'combat': {'iframes': 1}}
    return w


# ------------------------------------------------------------------ 3. shooter
def shooter():
    w = _base('Neon Yard', 'Bioluminescent Twilight', 'Hold the yard. Waves of raiders come from every corner and they '
              'flank. Mouse aims, click fires, 1/2 switches weapons, R reloads.', seed=21)
    w['objects'] = [_o('lamp', x, z) for x, z in ((-8, -6), (8, -6), (-8, 6), (8, 6), (0, 0))]
    boxes = [(-6, -3, -4, -1), (4, 1, 6, 3), (-2, 5, 2, 6), (-2, -6, 2, -5), (-12, 2, -10, 6), (10, -6, 12, -2)]
    w['systems'] = {'version': 1, 'genre': 'shooter', 'player': 'gsys', 'locale': {'languages': ['en']},
                    'controller': {'type': 'top_down', 'speed': 7},
                    'camera': {'rig': 'top_down', 'scale': 28},
                    'physics': {'bounds': {'x0': -17, 'z0': -12, 'x1': 17, 'z1': 12},
                                'boxes': [{'x0': a, 'z0': b, 'x1': c, 'z1': d, 'color': '#2b3b5a'} for a, b, c, d in boxes]},
                    'combat': {'player_weapon': 'pistol', 'iframes': 0.6},
                    'input': {'weapons': ['pistol', 'shotgun']},
                    'items': {'ammo_box': {'name': 'Ammo box', 'kind': 'pickup'}, 'medkit': {'name': 'Medkit', 'kind': 'consumable', 'use': {'heal': 4}}},
                    'loot_tables': {'drop': {'rolls': 1, 'entries': [{'item': 'ammo_box', 'w': 3}, {'item': 'medkit', 'w': 1, 'chance': 0.6}]}},
                    'genre_data': {'waves': [{'grunt': 4}, {'grunt': 5, 'archer': 2}, {'grunt': 6, 'archer': 3, 'brute': 1},
                                             {'grunt': 8, 'archer': 4, 'brute': 2}],
                                   'spawns': [{'x': -15, 'z': -10}, {'x': 15, 'z': -10}, {'x': -15, 'z': 10}, {'x': 15, 'z': 10}]},
                    'environment': {'day_length': 0, 'start_hour': 22},
                    'hud': {'widgets': [{'type': 'bar', 'bind': 'player.hp', 'max': 'player.maxHp', 'label': 'HP', 'anchor': 'tl', 'x': 12, 'y': 12, 'w': 180, 'color': '#e05555'},
                                        {'type': 'text', 'text': 'Ammo {ammo}/{reserve}', 'anchor': 'br', 'x': 16, 'y': 16},
                                        {'type': 'text', 'text': 'Wave {wave}   Score {score}', 'anchor': 'tr', 'x': 16, 'y': 12},
                                        {'type': 'crosshair'}, {'type': 'messages', 'anchor': 'bc', 'y': 90}]}}
    w['spawn'] = {'x': 0, 'z': 2}
    return w


# ------------------------------------------------------------------ 4. survival
def survival():
    r = random.Random(5)
    w = _base('First Frost', 'Sub-Zero Aurora', 'Winter came early. Gather wood and stone, craft an axe and a campfire, '
              'keep fed, watered and warm, and survive three nights. Wolves hunt after dark.', seed=5)
    ents = []
    for _ in range(14):
        ents.append({'kind': 'tree', 'name': 'Pine', 'x': r.uniform(-28, 28), 'z': r.uniform(-24, 24), 'resource': 'wood', 'yield': 1, 'uses': 3})
    for _ in range(6):
        ents.append({'kind': 'tree', 'name': 'Old pine (axe)', 'x': r.uniform(-30, 30), 'z': r.uniform(-26, 26), 'resource': 'wood', 'yield': 3, 'uses': 4, 'tool': 'stone_axe', 's': 1.5})
    for _ in range(9):
        ents.append({'kind': 'rock', 'name': 'Rock', 'x': r.uniform(-28, 28), 'z': r.uniform(-24, 24), 'resource': 'stone', 'yield': 1, 'uses': 3})
    for _ in range(7):
        ents.append({'kind': 'bush', 'name': 'Frost berries', 'x': r.uniform(-24, 24), 'z': r.uniform(-20, 20), 'resource': 'berries', 'yield': 2, 'uses': 2})
    ents.append({'kind': 'pond', 'name': 'Pond', 'x': 12, 'z': -8, 'water': True})
    w['systems'] = {'version': 1, 'genre': 'survival', 'player': 'gsys', 'locale': {'languages': ['en']},
                    'controller': {'type': 'top_down', 'speed': 5.5},
                    'camera': {'rig': 'top_down', 'scale': 28},
                    'entities': ents,
                    'items': {'wood': {'name': 'Wood', 'kind': 'material'}, 'stone': {'name': 'Stone', 'kind': 'material'},
                              'berries': {'name': 'Frost berries', 'kind': 'food', 'use': {'food': 22, 'water': 4}},
                              'stone_axe': {'name': 'Stone axe', 'kind': 'equip', 'equip': {'slot': 'weapon', 'atk': 1}, 'stack': 1},
                              'campfire': {'name': 'Campfire kit', 'kind': 'placeable'}},
                    'recipes': {'stone_axe': {'in': {'wood': 2, 'stone': 2}, 'out': {'stone_axe': 1}},
                                'campfire': {'in': {'wood': 4, 'stone': 2}, 'out': {'campfire': 1}}},
                    'combat': {'player_weapon': 'fists'},
                    'environment': {'day_length': 180, 'start_hour': 8, 'weather': {'start': 'snow', 'change_every': [50, 100], 'pattern': [
                        {'type': 'snow', 'w': 2, 'min': 0.4, 'max': 1}, {'type': 'clear', 'w': 2}, {'type': 'storm', 'w': 1, 'min': 0.5, 'max': 1}]},
                        'effects': {'night': {'warmth': 0.5}}},
                    'genre_data': {'days_to_win': 3, 'rates': {'food': 0.3, 'water': 0.45, 'warmth': 0.35}},
                    'scripts': [{'id': 'night_wolves', 'source': (
                        "var pack = [];\n"
                        "on('dusk', function () { for (var i = 0; i < 2 + world.day; i++) pack.push(world.spawn({kind: 'enemy', name: 'Wolf', x: -30 + i * 3, z: 26, hp: 3, team: 'enemy', ai: 'melee'})); ui.toast('Night falls. Stay near your fire.'); });\n"
                        "on('dawn', function () { pack.forEach(function (w) { if (!w.dead) w.despawn(); }); pack = []; });\n")}]}
    w['spawn'] = {'x': 0, 'z': 0}
    return w


# ------------------------------------------------------------------ 5. horror
def horror():
    grid = ['########################',
            '#......#.....%.........#',
            '#.####.#.###.%.#######.#',
            '#.#....#...#.%.......#.#',
            '#.#.######.#.%%%%%.#.#.#',
            '#.#........#.....%.#...#',
            '#.##########.###.%.###.#',
            '#............#...%...#.#',
            '#.&&&&&&.###.#.#####.#.#',
            '#.&......#...#.....#...#',
            '#.&.####.#.#######.###.#',
            '#.&.#..#.#.......#...#.#',
            '#...#..#.#######.###.#.#',
            '#.###..#.........#...#.#',
            '#......#####.#####.###.#',
            '########...#.......#...#',
            '#..........#.#####.#.###',
            '#.#######..#.#...#.#...#',
            '#.#.....#....#.#.#.###.#',
            '#.#.###.######.#.#.....#',
            '#...#.........#..#####.#',
            '#####.#######.##.......#',
            '#.....................##',
            '########################']
    w = _base('The Lantern House', 'Crimson Citadel', 'Find three old keys and get out of the house. Your flashlight is '
              'weak. Something listens: walking is quiet, running is not. Hide in wardrobes if it comes.', seed=13)
    ents = [{'kind': 'item', 'item': 'old_key', 'name': 'Old key', 'x': 5.5, 'z': 11.5},
            {'kind': 'item', 'item': 'old_key', 'name': 'Old key', 'x': 20.5, 'z': 1.5},
            {'kind': 'item', 'item': 'old_key', 'name': 'Old key', 'x': 6.5, 'z': 18.5},
            {'kind': 'item', 'item': 'battery', 'name': 'Battery', 'x': 15.5, 'z': 9.5},
            {'kind': 'item', 'item': 'battery', 'name': 'Battery', 'x': 9.5, 'z': 15.5},
            {'kind': 'closet', 'name': 'Wardrobe', 'hide': True, 'x': 3.5, 'z': 9.5},
            {'kind': 'closet', 'name': 'Wardrobe', 'hide': True, 'x': 18.5, 'z': 13.5},
            {'kind': 'closet', 'name': 'Wardrobe', 'hide': True, 'x': 12.5, 'z': 20.5},
            {'kind': 'exit', 'name': 'Front door', 'x': 21.5, 'z': 22.0},
            {'kind': 'enemy', 'name': 'The Hollow', 'x': 16.5, 'z': 18.5, 'ai': 'stalker', 'hp': 999, 'speed': 3.0, 'sight': 7,
             'fov': 90, 'hearing': 10, 'team': 'enemy', 'weapon': 'claw'}]
    w['systems'] = {'version': 1, 'genre': 'horror', 'player': 'gsys', 'locale': {'languages': ['en']},
                    'controller': {'type': 'first_person', 'speed': 3.2, 'run': 1.9},
                    'camera': {'rig': 'first_person'},
                    'physics': {'flat': True, 'tiles': {'rows': grid, 'solid': '#%&'}},
                    'entities': ents,
                    'items': {'old_key': {'name': 'Old key', 'kind': 'key'}, 'battery': {'name': 'Battery', 'kind': 'pickup'}},
                    'combat': {'player_weapon': 'none', 'weapons': {'none': {'type': 'melee', 'damage': 0, 'range': 0.1, 'cooldown': 1}}, 'iframes': 1.5},
                    'environment': {'day_length': 0, 'start_hour': 23},
                    'genre_data': {'keys': 3, 'drain': 1.1}}
    w['spawn'] = {'x': 1.5, 'z': 1.5}
    w['player'] = {'hp': 6}
    return w


# ------------------------------------------------------------------ 6. racing
def racing():
    pts = []
    for k in range(16):
        a = k / 16 * math.tau
        rx, rz = 60 + 14 * math.sin(a * 2), 34 + 8 * math.cos(a * 3)
        pts.append({'x': round(math.cos(a) * rx, 1), 'z': round(math.sin(a) * rz, 1)})
    w = _base('Coastline Cup', 'Sunken Ruins', 'Three laps around the coast road. W/S throttle and brake, A/D steer, '
              'Shift boosts. Rain makes the road slippery; off the asphalt you lose grip.', seed=31)
    ents = [{'kind': 'vehicle', 'name': f'Rival {i + 1}', 'controller': 'vehicle', 'skill': s, 'col': c}
            for i, (s, c) in enumerate(((0.9, '#ff6b6b'), (0.94, '#7dff9a'), (0.86, '#c7b6ff')))]
    w['systems'] = {'version': 1, 'genre': 'racing', 'player': 'gsys', 'locale': {'languages': ['en']},
                    'controller': {'type': 'vehicle', 'maxSpeed': 28, 'accel': 17, 'grip': 6.5},
                    'camera': {'rig': 'top_down', 'scale': 9, 'lag': 4},
                    'entities': ents,
                    'genre_data': {'track': pts, 'width': 11, 'laps': 3},
                    'environment': {'day_length': 0, 'start_hour': 15, 'weather': {'start': 'clear', 'change_every': [40, 80], 'pattern': [
                        {'type': 'clear', 'w': 2}, {'type': 'rain', 'w': 1, 'min': 0.7, 'max': 1}]}}}
    w['spawn'] = {'x': pts[0]['x'], 'z': pts[0]['z']}
    return w


# ------------------------------------------------------------------ 7. puzzle
PUZZLES = [
    ['#######',
     '#P....#',
     '#.b.x.#',
     '#.....#',
     '#######'],
    ['########',
     '#..x...#',
     '#.b##b.#',
     '#P....x#',
     '########'],
    ['##########',
     '#P.......#',
     '#..b...s.#',
     '####d#####',
     '#..b..x..#',
     '#........#',
     '##########'],
    ['#########',
     '#P......#',
     '#.b.iiix#',
     '#.......#',
     '#########'],
]


def puzzle():
    w = _base('Crate Garden', 'Alpine Sunset', 'Push every crate onto a gold square. Switches open doors while '
              'something stands on them; crates slide on ice. Z undoes, R restarts.', seed=2)
    w['systems'] = {'version': 1, 'genre': 'puzzle', 'player': 'gsys', 'locale': {'languages': ['en']},
                    'controller': {'type': 'none'}, 'view': 'grid', 'genre_data': {'levels': PUZZLES}}
    return w


# ------------------------------------------------------------------ 8. strategy / management
def strategy():
    w = _base('Hearthvale Founding', 'Alpine Sunset', 'Found a village. Press 1-5 to pick a building and click to place '
              'it. Workers staff buildings by day and go home at night. Grow to twelve villagers.', seed=17)
    w['objects'] = [_o('tree', x, z) for x, z in ((-20, -12), (-18, -15), (-22, -9), (18, 12), (21, 14), (16, 15), (-15, 16))]
    w['systems'] = {'version': 1, 'genre': 'strategy', 'player': 'gsys', 'locale': {'languages': ['en']},
                    'controller': {'type': 'point_click', 'speed': 7},
                    'camera': {'rig': 'top_down', 'scale': 22},
                    'environment': {'day_length': 120, 'start_hour': 7},
                    'genre_data': {'start': {'wood': 40, 'stone': 10, 'food': 30, 'gold': 0}, 'workers': 3, 'goal': {'population': 12},
                                   'buildings': {'house': {'name': 'House', 'cost': {'wood': 10}, 'housing': 4},
                                                 'farm': {'name': 'Farm', 'cost': {'wood': 8}, 'workers': 2, 'produces': {'food': 0.6}},
                                                 'lumber': {'name': 'Lumber camp', 'cost': {'wood': 5, 'food': 5}, 'workers': 2, 'produces': {'wood': 0.5}},
                                                 'quarry': {'name': 'Quarry', 'cost': {'wood': 10}, 'workers': 2, 'produces': {'stone': 0.4}},
                                                 'market': {'name': 'Market', 'cost': {'wood': 15, 'stone': 10}, 'workers': 1, 'produces': {'gold': 0.3}, 'consumes': {'food': 0.2}}}}}
    return w


# ------------------------------------------------------------------ 9. visual novel
def visual_novel():
    w = _base('Letters from Lindmere', 'Bioluminescent Twilight', 'You come home to Lindmere after ten years away, '
              'carrying a letter you never sent. Choices shape who you trust and where you end up.', seed=8)
    nodes = {
        'arrive': {'speaker': 'Narrator', 'text': 'The ferry bumps the jetty. Lindmere smells of rain and woodsmoke, exactly '
                   'as you remember.', 'next': 'hesta', 'bg': 'dusk', 'show': []},
        'hesta': {'speaker': 'Aunt Hesta', 'text': 'Look at you, all grown and still late for supper. Pip has been watching '
                  'the water since noon.', 'show': ['hesta', 'pip'],
                  'choices': [{'text': 'I missed you, Aunt Hesta.', 'goto': 'pip', 'inc': {'trust_hesta': 1}},
                              {'text': 'I am only here for the week.', 'goto': 'pip', 'inc': {'trust_hesta': -1}}]},
        'pip': {'speaker': 'Pip', 'text': 'Are you the cousin from the city? I found a letter in the old post box with your '
                'name on it! Can we open it together?', 'show': ['hesta', 'pip'],
                'choices': [{'text': 'Of course. Show me, Pip.', 'goto': 'letter', 'affinity': {'pip': 1}},
                            {'text': 'Maybe tomorrow.', 'goto': 'inn'}]},
        'letter': {'speaker': 'Narrator', 'text': 'It is your own handwriting, ten years old: an apology to Rowan you never '
                   'posted. Pip beams, proud of the find.', 'next': 'inn', 'set': {'found_letter': True},
                   'do': {'flag': 'found_letter'}},
        'inn': {'speaker': 'Rowan', 'text': 'I heard you were back. The inn is quiet tonight. Sit with me?', 'bg': 'inn',
                'show': ['rowan'], 'choices': [
                    {'text': 'I owe you an apology.', 'goto': 'apology', 'if': {'flag': 'found_letter'}, 'inc': {'trust_rowan': 2}},
                    {'text': 'Just catching up, like old friends.', 'goto': 'friends', 'inc': {'trust_rowan': 1}},
                    {'text': 'I should go.', 'goto': 'leave'}]},
        'apology': {'speaker': 'Rowan', 'text': 'You kept it all these years? ... I am glad you are home. Walk with me '
                    'tomorrow, by the lake?', 'choices': [
                        {'text': 'I would like that.', 'goto': 'lake', 'romance': 'rowan', 'affinity': {'rowan': 2}},
                        {'text': 'As friends, yes.', 'goto': 'friends', 'affinity': {'rowan': 1}}]},
        'friends': {'speaker': 'Rowan', 'text': 'Then let us be friends who actually write letters this time.', 'next': 'morning'},
        'lake': {'speaker': 'Narrator', 'text': 'Morning on the lake. Rowan rows; you talk about everything you missed.',
                 'bg': 'garden', 'show': ['rowan'], 'next': 'morning'},
        'leave': {'speaker': 'Narrator', 'text': 'You leave the inn early. The rain follows you back to the jetty.', 'bg': 'rain',
                  'show': [], 'next': 'end_leave'},
        'morning': {'speaker': 'Aunt Hesta', 'text': 'So. Will you stay a while this time?', 'bg': 'day', 'show': ['hesta', 'pip'],
                    'choices': [{'text': 'I think I will stay.', 'goto': 'end_stay'}, {'text': 'I have to go back.', 'goto': 'end_leave'}]},
        'end_stay': {'speaker': 'Narrator', 'text': 'You stay. Pip teaches you every shortcut in Lindmere, and the post box is '
                     'never empty again. THE END.', 'do': {'win': True}},
        'end_leave': {'speaker': 'Narrator', 'text': 'The ferry pulls away. This time you write back. THE END.', 'do': {'win': True}}}
    w['dialogs'] = {'story': {'start': 'arrive', 'nodes': nodes}}
    w['systems'] = {'version': 1, 'genre': 'visual_novel', 'player': 'gsys', 'locale': {'languages': ['en']},
                    'controller': {'type': 'none'}, 'view': 'vn',
                    'characters': {'hesta': {'name': 'Aunt Hesta', 'age': 'adult', 'col': '#d9a066'},
                                   'rowan': {'name': 'Rowan', 'age': 'adult', 'romanceable': True, 'col': '#7fb2e5'},
                                   'pip': {'name': 'Pip', 'age': 'child', 'col': '#f2d16b', 'desc': 'Your little cousin, nine years old and full of questions.'},
                                   'post': {'name': 'Postmaster Odo', 'age': 'adult', 'col': '#a5c27a'}},
                    'relationships': [{'a': 'hesta', 'b': 'pip', 'type': 'guardian'}, {'a': 'rowan', 'b': 'player', 'type': 'friend'}],
                    'dialogue': {'vars': {'trust_hesta': 0, 'trust_rowan': 0}},
                    'genre_data': {'start': 'story', 'bg': 'dusk'},
                    'save': {'slots': 3}}
    return w


SAMPLES = {'action_rpg': action_rpg, 'platformer': platformer, 'shooter': shooter, 'survival': survival,
           'horror': horror, 'racing': racing, 'puzzle': puzzle, 'strategy': strategy, 'visual_novel': visual_novel}
HELP = {'action_rpg': 'WASD move, J / click attack, K whirl, L mend, E talk / use, Tab bag + skills, C craft, Q potion, F5/F9 save/load, Esc menu',
        'platformer': 'A/D move, Space jump (hold for higher), stomp enemies, reach the flag',
        'shooter': 'WASD move, mouse aim, click fire, 1/2 weapons, R reload',
        'survival': 'WASD move, E gather / drink, C craft (axe, campfire), Tab eat, survive 3 nights',
        'horror': 'Click to look, WASD move, Shift run (noisy), F flashlight, E pick up / hide / open',
        'racing': 'W/S throttle + brake, A/D steer, Shift boost - 3 laps',
        'puzzle': 'Arrows / WASD push crates onto gold squares, Z undo, R restart',
        'strategy': '1-5 choose a building, click to place, grow to 12 villagers',
        'visual_novel': 'Click or Space to continue, 1-3 choose'}


def all_worlds():
    return {k: fn() for k, fn in SAMPLES.items()}


PLAYER_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>%%TITLE%%</title>
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="generator" content="Stark World Engine Systems"><link rel="icon" href="data:,">
<style>html,body{margin:0;height:100%;background:#000;color:#fff;overflow:hidden;font:14px system-ui,sans-serif}#game{position:fixed;inset:0}
#help{position:fixed;left:50%;top:10px;transform:translateX(-50%);background:rgba(0,0,0,.55);padding:6px 12px;border-radius:8px;font-size:12px;z-index:20;pointer-events:none;transition:opacity 1s}</style></head>
<body><div id="game"></div><div id="help">%%HELP%%</div>
<script>%%BUNDLE%%</script>
<script>
var WORLD=%%WORLD%%;
var GAME=GSYS.play(document.getElementById('game'),WORLD);
setTimeout(function(){document.getElementById('help').style.opacity='0';},9000);
</script></body></html>
"""


def player_html(world, help_text='', js=None):
    """A one-file HTML5 cartridge for `world` played by the Stark World Engine Systems reference player."""
    data = (json.dumps(world).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
            .replace('\u2028', '\\u2028').replace('\u2029', '\\u2029'))
    title = re.sub(r'[<>&"%]', '', str(world.get('name', 'Game')))[:120]
    helptext = re.sub(r'[<>&"%]', '', help_text)[:300]
    return (PLAYER_HTML.replace('%%TITLE%%', title).replace('%%HELP%%', helptext)
            .replace('%%BUNDLE%%', js or bundle.bundle()).replace('%%WORLD%%', data))


def build(out=None):
    """Write every sample (world.json + .html) and an index page. -> list of written paths."""
    out = out or OUT
    os.makedirs(out, exist_ok=True)
    js, written, rows = bundle.bundle(), [], []
    for key, world in all_worlds().items():
        child_safety.enforce_export(world)                      # samples obey the same gate as everything else
        wp = os.path.join(out, f'{key}.world.json')
        with open(wp, 'w', encoding='utf-8') as f:
            json.dump(world, f, indent=1, ensure_ascii=False)
        hp = os.path.join(out, f'{key}.html')
        with open(hp, 'w', encoding='utf-8') as f:
            f.write(player_html(world, HELP.get(key, ''), js))
        written += [wp, hp]
        rows.append(f'<li><a href="{key}.html">{world["name"]}</a> <span>({key.replace("_", " ")})</span><br><small>'
                    f'{HELP.get(key, "")}</small></li>')
    idx = os.path.join(out, 'index.html')
    with open(idx, 'w', encoding='utf-8') as f:
        f.write('<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Stark World Engine Systems samples</title>'
                '<meta name="viewport" content="width=device-width,initial-scale=1"><style>body{font:16px system-ui;'
                'background:#14161f;color:#eee;max-width:760px;margin:40px auto;padding:0 16px}a{color:#ffd166}'
                'li{margin:14px 0}span{opacity:.6}small{opacity:.75}</style></head><body><h1>Stark World Engine Systems samples</h1>'
                '<p>Nine small games, one per genre kit. Each file is a complete offline game: double-click to play.</p>'
                '<ul>' + ''.join(rows) + '</ul></body></html>')
    written.append(idx)
    return written


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--out', default=OUT)
    a = ap.parse_args(argv)
    for p in build(a.out):
        print(p)
    return 0


if __name__ == '__main__':
    sys.exit(main())
