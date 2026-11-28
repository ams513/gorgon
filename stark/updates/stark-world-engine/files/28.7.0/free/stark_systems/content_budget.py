"""stark_systems/content_budget.py: an HONEST estimate of how long a world takes to play.

  python -m stark_systems.content_budget WORLD.json

It is a model, not a measurement: it counts what the world contains and multiplies by per-activity times taken from
a stated table (BASIS below, adjustable). It reports a low-high range and every line of its working. It cannot know
how fun or how hard the content is, and repeatable radiant quests are counted only up to the number of distinct
combinations the variety guards allow (beyond that they are replay, and the report says so). Calibrate BASIS with
real playtests (time a few players) before quoting hours anywhere.
"""
import json, math, sys

BASIS = {                       # minutes (low, high)
    'talk_step': (0.5, 1.5), 'collect_item': (0.4, 1.2), 'defeat_enemy': (0.3, 0.8), 'reach_step': (1.0, 3.0),
    'escort_step': (2.0, 5.0), 'craft_step': (1.0, 3.0), 'survive_per_min': (1.0, 1.0),
    'dialogue_words_per_min': (220, 140),          # reading speed (fast, slow)
    'explore_per_1000m2': (0.25, 0.8),             # free exploration of a 3D level
    'platformer_per_100_cols': (2.0, 6.0), 'puzzle_level': (2.0, 8.0), 'race_lap': (0.8, 1.5),
    'survival_day': (3.0, 3.0), 'radiant_quest': (4.0, 9.0), 'combat_encounter_enemy': (0.3, 0.9),
    'retry_factor': (1.1, 1.6),                    # deaths, backtracking, menus
}


def _levels(w):
    return [w] + [L for L in (w.get('levels') or []) if isinstance(L, dict)]


def estimate(world, basis=None):
    B = dict(BASIS, **(basis or {}))
    lines, lo, hi = [], 0.0, 0.0

    def add(label, n, key, per=1.0):
        nonlocal lo, hi
        if not n:
            return
        a, b = B[key]
        lo += n * a * per
        hi += n * b * per
        lines.append(f'{label}: {n:g} x {a:g}-{b:g} min')
    sy = world.get('systems') or {}
    quests = [q for L in _levels(world) for q in (L.get('quests') or []) if isinstance(q, dict)] + \
        [q for q in (sy.get('quests') or []) if isinstance(q, dict)]
    counts = {}
    for q in quests:
        for s in q.get('steps') or []:
            t, n = s.get('type'), int(s.get('count') or 1)
            if t == 'talk':
                counts['talk_step'] = counts.get('talk_step', 0) + 1
            elif t == 'collect':
                counts['collect_item'] = counts.get('collect_item', 0) + n
            elif t == 'defeat':
                counts['defeat_enemy'] = counts.get('defeat_enemy', 0) + n
            elif t in ('reach', 'deliver', 'interact'):
                counts['reach_step'] = counts.get('reach_step', 0) + 1
            elif t == 'escort':
                counts['escort_step'] = counts.get('escort_step', 0) + 1
            elif t == 'craft':
                counts['craft_step'] = counts.get('craft_step', 0) + 1
            elif t in ('survive', 'wait'):
                counts['survive_per_min'] = counts.get('survive_per_min', 0) + float(s.get('seconds') or 60) / 60
    lines.append(f'{len(quests)} handcrafted quests')
    for k, n in counts.items():
        add(f'  quest {k.replace("_", " ")}', n, k)
    words = 0
    for L in _levels(world):
        for d in (L.get('dialogs') or {}).values():
            for n in ((d or {}).get('nodes') or {}).values():
                if isinstance(n, dict):
                    words += len(str(n.get('text') or '').split())
    if words:
        f, s = B['dialogue_words_per_min']
        lo, hi = lo + words / f, hi + words / s
        lines.append(f'dialogue: {words} words at {s}-{f} words/min')
    enemies = sum(1 for L in _levels(world) for o in (L.get('objects') or []) if isinstance(o, dict) and o.get('t') == 'enemy')
    add('enemies placed (fights outside quests)', enemies, 'combat_encounter_enemy')
    for L in _levels(world):
        if L.get('mode') == '2d' and L.get('tiles'):
            add(f'2D level "{L.get("name", "")}"', len(L['tiles'][0]) / 100, 'platformer_per_100_cols')
        else:
            size = float(L.get('size') or 48)
            add(f'3D level "{L.get("name", "")}" exploration ({(2 * size) ** 2:.0f} m2)', (2 * size) ** 2 / 1000, 'explore_per_1000m2')
    gd, g = sy.get('genre_data') or {}, sy.get('genre')
    if g == 'puzzle':
        add('puzzle levels', len(gd.get('levels') or []), 'puzzle_level')
    if g == 'racing':
        add('race laps', int(gd.get('laps') or 3), 'race_lap')
    if g == 'survival':
        add('survival days', int(gd.get('days_to_win') or 3) * max(1, float((sy.get('environment') or {}).get('day_length') or 180) / 180),
            'survival_day')
    if g == 'visual_novel':
        lines.append('visual novel: branches counted as words read once; replays of other branches not counted')
    rad = sy.get('quests_radiant')
    if rad is not None or (g == 'action_rpg' and (gd.get('board'))):
        pools = (rad or {}).get('pools') or {}
        tpl = len((rad or {}).get('templates') or []) or 11
        places = len(pools.get('places') or []) or 1
        targets = max(1, len(pools.get('enemies') or []) + len(pools.get('items') or []))
        givers = len(pools.get('givers') or []) or 1
        distinct = min(tpl * places * targets, 400)
        ra, rb = B['radiant_quest']
        radiant = (distinct * ra / 60, distinct * rb / 60)
        lines.append(f'radiant (repeatable) quests, reported SEPARATELY: {distinct} distinct template/place/target '
                     f'combinations ({tpl} templates, {places} places, {targets} targets, {givers} givers) x {ra:g}-{rb:g} min; '
                     f'they reuse the same places and enemies, so they are optional extra play, not core content')
    else:
        radiant = (0, 0)
    a, b = B['retry_factor']
    lo, hi = lo * a, hi * b
    lines.append(f'x {a:g}-{b:g} for deaths, retries, backtracking and menus')
    return {'hours_low': round(lo / 60, 1), 'hours_high': round(hi / 60, 1), 'minutes_low': round(lo), 'minutes_high': round(hi),
            'radiant_hours_low': round(radiant[0], 1), 'radiant_hours_high': round(radiant[1], 1),
            'lines': lines, 'basis': B,
            'note': 'Model estimate from content counts x the BASIS table, not a measurement. Calibrate with playtests '
                    'before quoting it; it says nothing about quality or difficulty.'}


def report(world):
    e = estimate(world)
    extra = (f' (+ {e["radiant_hours_low"]}-{e["radiant_hours_high"]} hours of optional repeatable radiant quests)'
             if e['radiant_hours_high'] else '')
    return '\n'.join([f'Estimated play time: {e["hours_low"]}-{e["hours_high"]} hours{extra}'] + ['  ' + l for l in e['lines']] + [e['note']])


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    with open(argv[0], encoding='utf-8') as f:
        print(report(json.load(f)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
