"""stark_systems/schema.py: the world.json `systems` extension, schema version 1.

One declarative spec (SPEC) drives three things:
  validate_systems(world)  -> list of readable errors ('' paths like systems.combat.weapons.bow.damage)
  json_schema()            -> a JSON Schema (draft 2020-12) document for the UE5 / Godot bridges
  panels()                 -> editor panel specs (one panel per system: fields, types, ranges, help) for the studio UI

Backward compatible: a world without `systems` is valid; every field is optional; unknown keys are kept (and
reported as warnings, never errors) so newer studios can add fields without breaking older runtimes.

  python -m stark_systems.schema --json-schema OUT.json | --panels OUT.json | --check WORLD.json
"""
import json, os, re, sys

from . import SCHEMA_VERSION

HERE = os.path.dirname(os.path.abspath(__file__))
GENRES = ('action_rpg', 'platformer', 'shooter', 'survival', 'horror', 'racing', 'puzzle', 'strategy', 'visual_novel',
          'custom')
CONTROLLERS = ('third_person', 'first_person', 'top_down', 'side_scroll', 'vehicle', 'point_click', 'none')
RIGS = ('follow', 'side', 'first_person', 'top_down', 'orbit', 'fixed')
VIEWS = ('top', 'side', 'fp', 'grid', 'vn')
WEATHER = ('clear', 'auto', 'rain', 'storm', 'snow', 'fog', 'embers', 'pollen', 'bubbles', 'fireflies')
AGES = ('child', 'teen', 'adult')
MODS = ('move_speed', 'vision', 'hearing', 'grip', 'fire_damage', 'stamina_drain', 'warmth', 'spawn_rate', 'light',
        'work')
ID = r'^[A-Za-z0-9_.:-]{1,64}$'


# ------------------------------------------------------------------ spec helpers
def S(help='', n=2000, req=False, default=None):
    return {'type': 'str', 'max': n, 'help': help, 'req': req, 'default': default}


def N(lo=-1e6, hi=1e6, help='', integer=False, default=None, req=False):
    return {'type': 'int' if integer else 'num', 'min': lo, 'max': hi, 'help': help, 'default': default, 'req': req}


def B(help='', default=None):
    return {'type': 'bool', 'help': help, 'default': default}


def E(values, help='', default=None, req=False):
    return {'type': 'enum', 'values': list(values), 'help': help, 'default': default, 'req': req}


def I(help='', req=False):
    return {'type': 'id', 'help': help, 'req': req}


def L(item, help='', max_items=5000):
    return {'type': 'list', 'item': item, 'help': help, 'max': max_items}


def M(value, help='', max_items=2000):
    """A map from user ids to `value`."""
    return {'type': 'map', 'value': value, 'help': help, 'max': max_items}


def O(fields, help='', title=None, open_=True):
    return {'type': 'object', 'fields': fields, 'help': help, 'title': title, 'open': open_}


def A(help=''):
    return {'type': 'any', 'help': help}


def NUMMAP(help=''):
    return {'type': 'map', 'value': N(-1e6, 1e6), 'help': help, 'max': 500}


POINT = O({'x': N(-1e5, 1e5), 'z': N(-1e5, 1e5), 'y': N(-1e5, 1e5), 'name': S(n=80)})
COND = O({'has': I(), 'n': N(1, 9999), 'lacks': I(), 'done': I(), 'active': I(), 'notStarted': I(), 'flag': I(),
          'minLevel': N(1, 999), 'var': A('{name: {eq|ne|gte|lte|gt|lt: value}}'), 'rep': NUMMAP('faction: minimum'),
          'affinity': NUMMAP('character: minimum friendship'), 'chance': N(0, 1), 'time': E(('day', 'night')),
          'skill': I(), 'item': I()})
ACTION_FIELDS = {'start': I('start quest'), 'complete': I('complete quest'), 'advance': I('counts as talking to'),
                 'give': A('item id or [ids]'), 'take': I(), 'gold': N(-99999, 99999), 'xp': N(0, 99999), 'flag': I(),
                 'heal': N(0, 9999), 'win': B(), 'set': A('{var: value}'), 'inc': NUMMAP('{var: amount}'),
                 'rep': NUMMAP('{faction: change}'), 'affinity': NUMMAP('{character: friendship change}'),
                 'romance': A('adult characters only (child-safety guarded)'), 'shop': I(), 'craft': I(),
                 'event': S(n=80), 'sfx': S(n=40), 'music': S(n=40)}
STEP = O({'type': E(('collect', 'defeat', 'talk', 'reach', 'escort', 'craft', 'deliver', 'interact', 'survive', 'wait',
                     'custom'), req=True),
          'item': I(), 'kind': S(n=40), 'id': I(), 'npc': I(), 'count': N(1, 9999), 'x': N(-1e5, 1e5), 'z': N(-1e5, 1e5),
          'r': N(0.2, 500), 'label': S(n=120), 'level': N(0, 99), 'text': S(n=300), 'consume': B(), 'place': I(),
          'event': S(n=80), 'seconds': N(0, 36000), 'recipe': I()})
QUEST = O({'id': I(req=True), 'title': S(n=120), 'desc': S(n=1000), 'giver': I(), 'auto': B(), 'final': B(), 'next': I(),
           'steps': L(STEP, max_items=40), 'give': I(),
           'reward': O({'xp': N(0, 1e6), 'gold': N(0, 1e6), 'items': L(I()), 'item': I(), 'rep': NUMMAP()}),
           'fail': O({'time': N(0, 36000)})})
WEAPON = O({'type': E(('melee', 'ranged', 'hitscan'), req=True), 'damage': N(0, 9999), 'range': N(0, 1000),
            'arc': N(1, 360), 'cooldown': N(0, 60), 'knockback': N(0, 100), 'stagger': N(0, 10), 'heavy': B(),
            'projectile': O({'speed': N(0, 500), 'life': N(0, 30), 'radius': N(0, 10), 'pierce': N(0, 99, integer=True)}),
            'ammo': O({'clip': N(1, 9999, integer=True), 'reserve': N(0, 99999, integer=True), 'reload': N(0, 30)}),
            'spread': N(0, 90), 'pellets': N(1, 64, integer=True), 'dtype': S(n=30)})
ABILITY = O({'cooldown': N(0, 600), 'cost': O({'stamina': N(0, 999), 'mana': N(0, 999)}), 'requires': I(),
             'effect': O({'type': E(('damage', 'aoe', 'heal', 'buff', 'dash', 'projectile', 'shield'), req=True),
                          'amount': N(0, 9999), 'radius': N(0, 100), 'duration': N(0, 600), 'stat': S(n=30),
                          'mult': N(0, 100), 'add': N(-999, 999), 'weapon': I(), 'knockback': N(0, 100),
                          'pierce': N(0, 99, integer=True), 'dtype': S(n=30)})})
BT_NODE = A('behaviour-tree node: {sel|seq: [...]}, {not}, {cool, child}, {rand, child}, {cond, v}, {act, ...}')
BEHAVIOR = O({'type': E(('bt', 'fsm', 'schedule'), req=True), 'root': BT_NODE, 'start': S(n=40),
              'states': M(O({'do': S(n=40), 'go': L(O({'if': S(n=40), 'to': S(n=40), 'v': N()}))}))})
CHARACTER = O({'name': S(n=80), 'age': A('child | teen | adult or a number of years (default adult)'),
               'desc': S(n=2000), 'backstory': S(n=4000), 'bio': S(n=2000), 'kind': E(('npc', 'enemy', 'animal', 'vehicle', 'player')),
               'x': N(-1e5, 1e5), 'z': N(-1e5, 1e5), 'y': N(-1e5, 1e5), 'level': N(0, 99, integer=True),
               'faction': I(), 'hp': N(1, 1e6), 'ai': S(n=40), 'schedule': A('schedule id or a list of entries'),
               'dialog': I(), 'shop': I(), 'romanceable': B('adults only - the child-safety rules block it for minors'),
               'hostile': B(), 'team': S(n=20), 'role': S(n=40), 'weapon': I(), 'outfit': A(), 'col': S(n=9),
               'loot': I(), 'tags': L(S(n=40)), 'gold': N(0, 1e6), 'controller': E(CONTROLLERS), 'skill': N(0, 1)})
ITEM = O({'name': S(n=80, req=True), 'kind': S(n=30), 'stack': N(1, 9999, integer=True), 'weight': N(0, 10000),
          'value': N(0, 1e6), 'price': N(0, 1e6), 'desc': S(n=300), 'tags': L(S(n=30)), 'heal': N(0, 9999),
          'use': O({'heal': N(0, 9999), 'food': N(0, 100), 'water': N(0, 100), 'warmth': N(0, 100),
                    'buff': O({'stat': S(n=30), 'mult': N(0, 100), 'add': N(-999, 999), 'duration': N(0, 3600)})}),
          'equip': O({'slot': S(n=20), 'atk': N(-999, 999), 'def': N(-999, 999), 'hp': N(-999, 999), 'speed': N(0.1, 5),
                      'warmth': N(-100, 100), 'light': N(0, 100)}),
          'weapon': I(), 'col': S(n=9), 'gold': N(0, 1e6)})
WIDGET = O({'type': E(('bar', 'text', 'icons', 'quests', 'minimap', 'crosshair', 'timer', 'prompt', 'compass', 'messages'), req=True),
            'bind': S(n=60), 'max': A('number or bind'), 'text': S(n=200), 'x': N(-4000, 4000), 'y': N(-4000, 4000),
            'w': N(1, 4000), 'h': N(1, 4000), 'anchor': E(('tl', 'tr', 'bl', 'br', 'tc', 'bc', 'c', 'cl', 'cr')),
            'color': S(n=20), 'label': S(n=40), 'when': S(n=60)})
TRACK = O({'type': E(('camera', 'subtitle', 'move', 'say', 'fade', 'letterbox', 'event'), req=True), 'at': N(0, 3600),
           'dur': N(0, 3600), 'to': N(), 'x': N(), 'y': N(), 'z': N(), 'yaw': N(), 'zoom': N(0.05, 20), 'text': S(n=300),
           'entity': I(), 'ease': E(('linear', 'inOut', 'in', 'out')), 'speed': N(0, 100), 'event': S(n=80)})
GRAPH_NODE = O({'id': I(req=True), 'type': S(n=60, req=True), 'params': A(), 'x': N(), 'y': N()})
GRAPH_LINK = O({'from': I(req=True), 'out': S(n=30), 'to': I(req=True), 'in': S(n=30)})

SPEC = O({
    'version': N(1, SCHEMA_VERSION, integer=True, help='systems schema version (1)', default=1),
    'genre': E(GENRES, 'which genre kit runs (game rules); every system stays available', 'custom'),
    'player': E(('gsys', 'legacy'), 'gsys: the Stark World Engine Systems player plays this world; legacy: the studio engine'),
    'view': E(VIEWS, 'reference-player view (default from genre / controller)'),
    'locale': O({'languages': L(S(n=8), 'languages the game text uses (sharpens the child-safety checker)')}, title='Languages'),
    'controller': O({'type': E(CONTROLLERS, default='top_down'), 'speed': N(0, 200), 'run': N(0, 10), 'accel': N(0, 1000),
                     'friction': N(0, 1000), 'jump': N(0, 200), 'gravity': N(0, 500), 'coyote': N(0, 1), 'buffer': N(0, 1),
                     'cut': N(0, 1), 'maxFall': N(0, 500), 'airAccel': N(0, 1000), 'airControl': N(0, 1), 'sens': N(0, 0.1),
                     'maxSpeed': N(0, 500), 'brake': N(0, 500), 'reverse': N(0, 500), 'steer': N(0, 20), 'grip': N(0, 50),
                     'drag': N(0, 10), 'boost': N(0, 500), 'boostTime': N(0, 60), 'boostCool': N(0, 600)},
                    'player character controller', title='Controller'),
    'physics': O({'mode': E(('plane', 'side')), 'gravity': N(0, 500), 'ground': N(-1e4, 1e4), 'flat': B(),
                  'bounds': O({'x0': N(), 'z0': N(), 'x1': N(), 'z1': N()}),
                  'tiles': O({'rows': L(S(n=4000), max_items=4000), 'solid': S(n=20), 'oneway': S(n=20), 'cell': N(0.1, 100),
                              'ox': N(), 'oz': N()}),
                  'boxes': L(O({'x0': N(req=True), 'z0': N(req=True), 'x1': N(req=True), 'z1': N(req=True), 'color': S(n=20),
                                'nonsolid': B(), 'oneway': B()}))}, 'collision world', title='Physics'),
    'zones': L(O({'id': I(req=True), 'x': N(), 'z': N(), 'r': N(0, 1e4), 'box': O({'x0': N(), 'z0': N(), 'x1': N(), 'z1': N()}),
                  'label': S(n=80)}), 'trigger zones (enterZone / exitZone events)'),
    'camera': O({'rig': E(RIGS), 'distance': N(0, 1000), 'height': N(-1000, 1000), 'lag': N(0, 100), 'lookahead': N(0, 100),
                 'zoom': N(0.05, 20), 'scale': N(1, 400), 'yaw': N(), 'pitch': N(), 'deadzone': L(N(0, 100), max_items=2),
                 'intro': I('cinematic to play at start'),
                 'cinematics': M(O({'duration': N(0, 3600), 'tracks': L(TRACK, max_items=500)}))}, title='Camera'),
    'characters': M(CHARACTER, 'cast: every character has an age group (default adult)'),
    'entities': L(A('{kind, x, z, name, ...}: things the studio object list has no type for (campfire, vehicle, exit, '
                    'building, shrine, animal, board, station, resource nodes)'), 'extra entities', max_items=20000),
    'combat': O({'weapons': M(WEAPON), 'abilities': M(ABILITY), 'player_weapon': I(),
                 'hit_reactions': M(O({'stagger': N(0, 10), 'knockback': N(0, 100)})),
                 'crit': O({'chance': N(0, 1), 'mult': N(1, 100)}), 'iframes': N(0, 10), 'friendly_fire': B()}, title='Combat'),
    'ai': O({'behaviors': M(BEHAVIOR), 'perception': O({'sight': N(0, 1000), 'fov': N(1, 360), 'hearing': N(0, 1000),
                                                         'memory': N(0, 600)}),
             'squads': M(O({'members': L(I()), 'leader': I(), 'formation': E(('line', 'wedge', 'circle', 'column')),
                            'tactic': E(('flank', 'surround', 'swarm', 'hold')), 'spacing': N(0.2, 50)})),
             'schedules': M(L(O({'from': N(0, 24), 'to': N(0, 24), 'at': A('{x, z} or a poi id'), 'do': S(n=40),
                                 'say': S(n=200)}))),
             'pois': M(POINT), 'capture': L(A())}, title='AI'),
    'items': M(ITEM, 'extends world.items'),
    'inventory': O({'slots': N(1, 1000, integer=True), 'max_weight': N(0, 1e6), 'start': NUMMAP('item: count'),
                    'auto_equip': B()}, title='Inventory'),
    'recipes': M(O({'in': NUMMAP(), 'out': NUMMAP(), 'station': S(n=40), 'time': N(0, 3600), 'level': N(0, 999),
                    'skill': I()})),
    'loot_tables': M(O({'rolls': A('n or [min, max]'), 'guaranteed': L(O({'item': I(req=True), 'n': N(1, 9999)})),
                        'entries': L(O({'item': I(), 'table': I(), 'w': N(0, 1e6), 'min': N(0, 9999), 'max': N(0, 9999),
                                        'chance': N(0, 1), 'minLevel': N(0, 999)})),
                        'gold': L(N(0, 1e6), max_items=2)})),
    'progression': O({'xp_curve': O({'base': N(1, 1e6), 'exp': N(0.5, 4)}), 'max_level': N(1, 999, integer=True),
                      'points_per_level': N(0, 99), 'hp_per_level': N(0, 999), 'levels_per_atk': N(1, 999),
                      'start_points': N(0, 999),
                      'skill_trees': M(O({'name': S(n=80), 'nodes': M(O({'name': S(n=80), 'desc': S(n=300), 'cost': N(0, 99),
                                                                          'requires': L(I()), 'level': N(0, 999),
                                                                          'effect': O({'stat': S(n=30), 'add': N(), 'mult': N(0, 100),
                                                                                       'ability': I(), 'perk': I()})}))})),
                      'perks': M(O({'name': S(n=80), 'desc': S(n=300), 'auto': B(),
                                    'requires': O({'level': N(0, 999), 'quest': I(), 'skill': I()}),
                                    'effect': O({'stat': S(n=30), 'add': N(), 'mult': N(0, 100)})}))}, title='Progression'),
    'quests': L(QUEST, 'quests in addition to the studio quests'),
    'quests_radiant': O({'templates': L(O({'id': I(req=True), 'verb': S(n=30, req=True), 'title': S(n=160), 'desc': S(n=600),
                                           'steps': L(A()), 'n': L(N(1, 999), max_items=2), 'give': I(), 'needs': S(n=30)})),
                         'pools': O({'enemies': L(O({'id': I(req=True), 'name': S(n=80)})), 'items': L(O({'id': I(req=True), 'name': S(n=80)})),
                                     'places': L(O({'id': I(req=True), 'name': S(n=80)})), 'givers': L(I()),
                                     'crafts': L(O({'id': I(req=True), 'name': S(n=80)}))}),
                         'variety': O({'history': N(1, 999), 'template_cooldown': N(0, 99), 'max_same_verb': N(1, 99),
                                       'verb_window': N(1, 99), 'place_cooldown': N(0, 99), 'giver_cooldown': N(0, 99),
                                       'target_cooldown': N(0, 99), 'candidates': N(1, 200), 'followup_every': N(1, 999)}),
                         'reward': O({'xp': L(N(0, 1e5), max_items=2), 'gold': L(N(0, 1e5), max_items=2)}),
                         'adjectives': L(S(n=40)), 'reasons': L(S(n=120)), 'lost': L(S(n=80))}, title='Radiant quests'),
    'dialogs': M(A('studio dialog format + vars / rep / affinity conditions and actions')),
    'dialogue': O({'vars': A('starting dialogue variables')}),
    'factions': M(O({'name': S(n=80), 'desc': S(n=1000), 'relations': NUMMAP('-100..100'), 'members': L(I()),
                     'hostile_below': N(-100, 100), 'player': N(-100, 100),
                     'ranks': L(O({'at': N(-100, 100), 'name': S(n=60)}))})),
    'economy': O({'currency': S(n=30), 'start': N(0, 1e9), 'rep_price': N(0, 1),
                  'shops': M(O({'name': S(n=80), 'owner': I(), 'faction': I(), 'buy_mult': N(0, 100), 'sell_mult': N(0, 100),
                                'restock_hours': N(0, 1e5), 'dynamic': B(),
                                'stock': L(O({'item': I(req=True), 'qty': N(0, 1e6), 'price': N(0, 1e9)}))}))}, title='Economy'),
    'environment': O({'day_length': N(0, 1e6, help='seconds per in-game day (0 = time stands still)'), 'start_hour': N(0, 24),
                      'weather': O({'start': E(WEATHER), 'pattern': L(O({'type': E(WEATHER, req=True), 'w': N(0, 1e6),
                                                                          'min': N(0, 1), 'max': N(0, 1)})),
                                    'change_every': L(N(1, 1e6), max_items=2)}),
                      'effects': M(M(N(0, 100)), 'per weather / night: {modifier: multiplier}')}, title='Day/night + weather'),
    'save': O({'slots': N(1, 99, integer=True), 'autosave_s': N(0, 1e5)}, title='Save/load'),
    'hud': O({'widgets': L(WIDGET, max_items=200)}, title='HUD'),
    'audio': O({'events': M(S(n=40)), 'cues': M(O({'wave': E(('square', 'saw', 'sine', 'noise', 'triangle')), 'freq': N(1, 20000),
                                                     'slide': N(-20000, 20000), 'dur': N(0, 10), 'vol': N(0, 1), 'range': N(0, 1e4)})),
                'music': M(S(n=120))}, title='Audio'),
    'input': O({'abilities': L(I(), max_items=9), 'weapons': L(I(), max_items=9)}),
    'scripts': L(O({'id': I(req=True), 'entity': I(), 'source': S(n=200000, req=True), 'budget': N(1000, 5e6)}), max_items=500),
    'graphs': L(O({'id': I(req=True), 'entity': I(), 'nodes': L(GRAPH_NODE, max_items=2000), 'links': L(GRAPH_LINK, max_items=8000)}),
                max_items=500),
    'streaming': O({'chunk': N(4, 4096), 'radius': N(0, 32, integer=True), 'keep': N(0, 8, integer=True),
                    'lod': L(O({'dist': N(0, 1e9), 'level': N(0, 9, integer=True)}), max_items=8),
                    'budgets': O({'instances': N(1, 1e7, integer=True), 'per_kind': NUMMAP(), 'active_ai': N(0, 1e5, integer=True)}),
                    'generator': E(('region',)), 'region': O({'camp': N(0, 1), 'shrine': N(0, 1)})}, title='Streaming'),
    'procgen': O({'names': O({'first': L(S(n=40)), 'last': L(S(n=40))}), 'syllables': L(S(n=12)), 'jobs': L(S(n=40)),
                  'enemies': L(S(n=40)), 'loot': I()}, title='Procedural content'),
    'genre_data': A('per-genre settings (see SYSTEMS_GUIDE.md)'),
    'relationships': L(O({'a': I(req=True), 'b': I(req=True), 'type': S(n=40, req=True)}), 'family / social / romantic links'),
    'romance': A('romance options (adults only)'),
    'safety': A('informational stamp written by the export gate; never read to relax any rule'),
}, 'Stark World Engine Systems: world.json["systems"]', title='Stark World Engine Systems')


# ------------------------------------------------------------------ validation
def _check(v, spec, path, errs, warns):
    t = spec['type']
    if v is None:
        return
    if t == 'any':
        return
    if t == 'str':
        if not isinstance(v, str):
            errs.append(f'{path} must be text')
        elif len(v) > spec.get('max', 2000):
            errs.append(f'{path} is too long (at most {spec["max"]} characters)')
    elif t in ('num', 'int'):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v in (float('inf'), float('-inf')):
            errs.append(f'{path} must be a number')
        elif not spec.get('min', -1e18) <= v <= spec.get('max', 1e18):
            errs.append(f'{path} must be between {spec["min"]:g} and {spec["max"]:g}')
        elif t == 'int' and v != int(v):
            errs.append(f'{path} must be a whole number')
    elif t == 'bool':
        if not isinstance(v, bool):
            errs.append(f'{path} must be true or false')
    elif t == 'enum':
        if v not in spec['values']:
            errs.append(f'{path} must be one of: {", ".join(map(str, spec["values"]))}')
    elif t == 'id':
        if not isinstance(v, str) or not re.match(ID, v):
            errs.append(f'{path} must be a short id (letters, digits, _ . : -)')
    elif t == 'list':
        if not isinstance(v, list):
            errs.append(f'{path} must be a list')
            return
        if len(v) > spec.get('max', 5000):
            errs.append(f'{path} has too many entries (at most {spec["max"]})')
        for i, x in enumerate(v[:spec.get('max', 5000)]):
            _check(x, spec['item'], f'{path}[{i}]', errs, warns)
    elif t == 'map':
        if not isinstance(v, dict):
            errs.append(f'{path} must be an object of id: value')
            return
        if len(v) > spec.get('max', 2000):
            errs.append(f'{path} has too many entries (at most {spec["max"]})')
        for k, x in v.items():
            if not re.match(ID, str(k)):
                errs.append(f'{path}: "{k}" is not a valid id')
            _check(x, spec['value'], f'{path}.{k}', errs, warns)
    elif t == 'object':
        if not isinstance(v, dict):
            errs.append(f'{path} must be an object')
            return
        f = spec['fields']
        for k, fs in f.items():
            if fs.get('req') and v.get(k) in (None, ''):
                errs.append(f'{path}.{k} is required')
        for k, x in v.items():
            if k in f:
                _check(x, f[k], f'{path}.{k}', errs, warns)
            elif not k.startswith('_'):
                warns.append(f'{path}.{k} is not a known field (kept, ignored by this version)')


def validate_systems(world):
    """-> (errors, warnings) for world['systems'] (and nothing to report when it is absent)."""
    sy = world.get('systems') if isinstance(world, dict) else None
    errs, warns = [], []
    if sy is None:
        return errs, warns
    _check(sy, SPEC, 'systems', errs, warns)
    if isinstance(sy, dict):
        if isinstance(sy.get('version'), int) and sy['version'] > SCHEMA_VERSION:
            warns.append(f'systems.version {sy["version"]} is newer than this runtime ({SCHEMA_VERSION}); unknown parts are ignored')
        errs += _cross_checks(world, sy)
    return errs, warns


def _cross_checks(world, sy):
    errs = []
    cat = node_catalog()['nodes']
    for gi, g in enumerate(sy.get('graphs') or []):
        if not isinstance(g, dict):
            continue
        ids = set()
        for n in g.get('nodes') or []:
            if isinstance(n, dict):
                if n.get('id') in ids:
                    errs.append(f'systems.graphs[{gi}]: duplicate node id {n.get("id")}')
                ids.add(n.get('id'))
                if n.get('type') not in cat:
                    errs.append(f'systems.graphs[{gi}]: node "{n.get("id")}" has unknown type "{n.get("type")}"')
        for li, lk in enumerate(g.get('links') or []):
            if isinstance(lk, dict):
                for end in ('from', 'to'):
                    if lk.get(end) not in ids:
                        errs.append(f'systems.graphs[{gi}].links[{li}]: "{end}" node "{lk.get(end)}" does not exist')
                src = next((n for n in g.get('nodes') or [] if isinstance(n, dict) and n.get('id') == lk.get('from')), None)
                if src and src.get('type') in cat:
                    d = cat[src['type']]
                    out = lk.get('out') or 'next'
                    if (lk.get('in') or 'exec') == 'exec' and d['kind'] != 'data' and out not in d.get('out', []):
                        errs.append(f'systems.graphs[{gi}].links[{li}]: node "{src["id"]}" has no output "{out}"')
    items = set((sy.get('items') or {}).keys()) | set((world.get('items') or {}).keys())
    for L in world.get('levels') or []:
        if isinstance(L, dict):
            items |= set((L.get('items') or {}).keys())
    for rid, r in (sy.get('recipes') or {}).items() if isinstance(sy.get('recipes'), dict) else []:
        if isinstance(r, dict) and not (r.get('out') or {}):
            errs.append(f'systems.recipes.{rid} makes nothing (out is empty)')
    for tid, t in (sy.get('loot_tables') or {}).items() if isinstance(sy.get('loot_tables'), dict) else []:
        for e in (t.get('entries') or []) if isinstance(t, dict) else []:
            if isinstance(e, dict) and e.get('table') and e['table'] not in (sy.get('loot_tables') or {}):
                errs.append(f'systems.loot_tables.{tid}: nested table "{e["table"]}" does not exist')
    return errs


def node_catalog():
    with open(os.path.join(HERE, 'schema', 'node_catalog.json'), encoding='utf-8') as f:
        return json.load(f)


# ------------------------------------------------------------------ exports
def _js(spec):
    t = spec['type']
    out = {'description': spec['help']} if spec.get('help') else {}
    if t == 'str':
        out.update(type='string', maxLength=spec.get('max', 2000))
    elif t in ('num', 'int'):
        out.update(type='integer' if t == 'int' else 'number', minimum=spec['min'], maximum=spec['max'])
    elif t == 'bool':
        out['type'] = 'boolean'
    elif t == 'enum':
        out['enum'] = spec['values']
    elif t == 'id':
        out.update(type='string', pattern=ID)
    elif t == 'list':
        out.update(type='array', items=_js(spec['item']), maxItems=spec.get('max', 5000))
    elif t == 'map':
        out.update(type='object', additionalProperties=_js(spec['value']), propertyNames={'pattern': ID})
    elif t == 'object':
        out.update(type='object', properties={k: _js(v) for k, v in spec['fields'].items()}, additionalProperties=True)
        req = [k for k, v in spec['fields'].items() if v.get('req')]
        if req:
            out['required'] = req
    if spec.get('default') is not None:
        out['default'] = spec['default']
    return out


def json_schema():
    s = _js(SPEC)
    s.update({'$schema': 'https://json-schema.org/draft/2020-12/schema', '$id': 'stark-systems/systems.v1.json',
              'title': 'Stark World Engine Systems world.json "systems" extension (version 1)'})
    return s


PANEL_TITLES = {'controller': 'Controller', 'physics': 'Physics', 'camera': 'Camera + cinematics', 'combat': 'Combat',
                'ai': 'AI', 'inventory': 'Inventory', 'progression': 'Progression', 'quests_radiant': 'Radiant quests',
                'economy': 'Economy + shops', 'environment': 'Day/night + weather', 'save': 'Save/load', 'hud': 'HUD',
                'audio': 'Audio events', 'streaming': 'Streaming', 'procgen': 'Procedural content', 'locale': 'Languages'}


def _fields(spec, prefix, out, depth=0):
    for k, v in spec['fields'].items():
        path = f'{prefix}.{k}'
        f = {'path': path, 'label': k.replace('_', ' '), 'type': v['type'], 'help': v.get('help', '')}
        for key in ('values', 'min', 'max', 'default'):
            if v.get(key) is not None:
                f[key] = v[key]
        if v['type'] == 'object' and depth < 2:
            _fields(v, path, out, depth + 1)
            continue
        if v['type'] in ('map', 'list'):
            f['editor'] = 'table' if (v.get('value') or v.get('item') or {}).get('type') == 'object' else 'list'
            inner = v.get('value') or v.get('item')
            if inner and inner['type'] == 'object':
                f['columns'] = [{'key': ck, 'type': cv['type'], 'values': cv.get('values'), 'help': cv.get('help', '')}
                                for ck, cv in inner['fields'].items()]
        out.append(f)


def panels():
    """Editor panel specs: [{id, title, help, fields: [{path, label, type, values?, min?, max?, help, editor?}]}]."""
    out = []
    for k, v in SPEC['fields'].items():
        if v['type'] == 'object':
            fl = []
            _fields(v, 'systems.' + k, fl)
            out.append({'id': k, 'title': PANEL_TITLES.get(k, v.get('title') or k), 'help': v.get('help', ''), 'fields': fl})
        else:
            f = {'path': 'systems.' + k, 'label': k.replace('_', ' '), 'type': v['type'], 'help': v.get('help', '')}
            if v.get('values'):
                f['values'] = v['values']
            out.append({'id': k, 'title': PANEL_TITLES.get(k, k.replace('_', ' ').title()), 'help': v.get('help', ''),
                        'fields': [f]})
    return {'version': SCHEMA_VERSION, 'panels': out, 'node_catalog': node_catalog()['nodes']}


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--json-schema')
    ap.add_argument('--panels')
    ap.add_argument('--check')
    a = ap.parse_args(argv)
    if a.json_schema:
        with open(a.json_schema, 'w', encoding='utf-8') as f:
            json.dump(json_schema(), f, indent=1)
    if a.panels:
        with open(a.panels, 'w', encoding='utf-8') as f:
            json.dump(panels(), f, indent=1)
    if a.check:
        with open(a.check, encoding='utf-8') as f:
            errs, warns = validate_systems(json.load(f))
        for e in errs:
            print('ERROR', e)
        for w in warns:
            print('warn ', w)
        return 1 if errs else 0
    return 0


if __name__ == '__main__':
    sys.exit(main())
