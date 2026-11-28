"""library.py - the animation library: an index of Stark World Engine clips (.gclip.json) that the studio's library browser
lists and cartridges embed (spec: LIBRARY_BROWSER_SPEC.md).

  python -m stark_anim library build     rebuild library/quaternius_ual from the staged Quaternius UAL (dev step)
  python -m stark_anim library index     re-scan library/ and rewrite library/index.json
Index entries: id, name, file, category, tags, loop, duration, frames, fps, events (counts), root_motion
(distance m, speed m/s, turn deg), safety, source, licence, credit, preview (GIF/MP4 when rendered).
"""
import json, os, re

import numpy as np

from .clip import Clip
from . import child_safety

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LIB = os.path.join(HERE, 'library')
UAL_RM = os.path.join(ROOT, 'third_party_assets', '_staging', 'quaternius_universal-animation-library', 'x',
                      'Universal Animation Library[Standard]', 'Unreal-Godot', 'UAL1_Standard_RM.glb')
CATEGORIES = [  # (regex on the clip name, category, extra tags) - first match wins
    (r'walk|jog|sprint|run|crouch_fwd|swim_fwd', 'locomotion', ['cycle']),
    (r'jump|roll|land', 'traversal', []),
    (r'attack|punch|kick|slash|stab|shoot', 'combat', ['action']),
    (r'hit_|death|die', 'reaction', []),
    (r'talk|sitting|interact|pick|push|fix|driv|dance', 'interaction', []),
    (r'pistol|spell|torch|sword', 'stance', []),
    (r'idle|tpose', 'idle', []),
]
EXTRACT_ROOT = ('locomotion', 'traversal', 'combat')  # these move the character; others keep motion in the body


def categorize(name):
    n = name.lower()
    for rx, cat, tags in CATEGORIES:
        if re.search(rx, n):
            return cat, list(tags)
    return 'other', []


def motion_stats(clip):
    if clip.motion is None:
        return None
    m = clip.motion
    dist = float(np.linalg.norm(m[-1, [0, 2]] - m[0, [0, 2]]))
    turn = float(np.degrees(m[-1, 3] - m[0, 3]))
    return {'distance': round(dist, 3), 'speed': round(dist / max(clip.duration, 1e-6), 3), 'turn': round(turn, 2)}


def entry(clip, rel_file, source=None, licence=None, credit=None):
    cat = clip.meta.get('category') or categorize(clip.name)[0]
    ev = {}
    for e in clip.events:
        ev[e.get('type', '?')] = ev.get(e.get('type', '?'), 0) + 1
    return {'id': os.path.splitext(os.path.splitext(rel_file)[0])[0].replace('\\', '/'), 'name': clip.name,
            'file': rel_file.replace('\\', '/'), 'category': cat, 'tags': clip.tags, 'loop': clip.loop,
            'duration': round(clip.duration, 4), 'frames': clip.frames, 'fps': clip.fps, 'events': ev,
            'root_motion': motion_stats(clip), 'safety': child_safety.classify(clip.name, clip.tags,
                                                                              clip.meta.get('safety')),
            'source': source or clip.meta.get('source'), 'licence': licence or clip.meta.get('licence'),
            'credit': credit or clip.meta.get('credit'), 'preview': clip.meta.get('preview')}


def build_quaternius(src=UAL_RM, out_dir=os.path.join(LIB, 'quaternius_ual'), fps=30, log=print):
    """Retarget every Universal Animation Library clip onto the Stark World Engine rig (root motion extracted), clean it
    (foot lock for locomotion, footstep + hit events), classify safety, write .gclip.json files + index."""
    from .gltf_io import load_gltf_clips
    from .retarget import retarget
    from . import cleanup
    if not os.path.isfile(src):
        raise FileNotFoundError('Universal Animation Library not staged: ' + src)
    skel, clips = load_gltf_clips(src, fps=fps)
    os.makedirs(out_dir, exist_ok=True)
    entries = []
    for c in clips:
        cat, tags = categorize(c.name)
        rc, rep = retarget(skel, c, root_motion='extract' if cat in EXTRACT_ROOT else 'keep')
        rc.tags = sorted(set(tags + [cat]))
        rc.loop = bool(re.search(r'loop', rc.name, re.I))
        st = motion_stats(rc)
        if st and st['distance'] < 0.02 and abs(st['turn']) < 2:
            rc.motion = None  # stationary clip: no root motion track
        if cat == 'locomotion' and rc.loop and not re.search(r'swim', rc.name, re.I):
            rc = cleanup.lock_feet(rc)
            rc = cleanup.add_footstep_events(rc)
        if cat == 'combat':
            rc = cleanup.add_hit_event(rc)
        rc.meta.update({'source': 'Quaternius Universal Animation Library (UAL1_Standard_RM.glb)',
                        'licence': 'CC0 1.0', 'credit': 'Quaternius', 'category': cat,
                        'source_url': 'https://quaternius.itch.io/universal-animation-library'})
        rc.meta.pop('foot_lock', None)
        child_safety.classify_clip(rc)
        fn = re.sub(r'[^A-Za-z0-9_]+', '_', rc.name) + '.gclip.json'
        rc.save(os.path.join(out_dir, fn))
        e = entry(rc, os.path.relpath(os.path.join(out_dir, fn), LIB))
        entries.append(e)
        log(f"  {rc.name:24s} {cat:11s} {rc.duration:5.2f}s loop={rc.loop!s:5s} safety={e['safety']:8s} "
            f"events={e['events']} motion={e['root_motion']}")
    write_index()
    return entries


def scan(lib=LIB):
    out = []
    for dp, _, files in os.walk(lib):
        for f in sorted(files):
            if f.endswith('.gclip.json'):
                p = os.path.join(dp, f)
                try:
                    c = Clip.load(p)
                except (ValueError, KeyError, OSError) as ex:
                    out.append({'file': os.path.relpath(p, lib).replace('\\', '/'), 'error': str(ex)})
                    continue
                out.append(entry(c, os.path.relpath(p, lib)))
    return out


def write_index(lib=LIB):
    os.makedirs(lib, exist_ok=True)
    items = scan(lib)
    cats = {}
    for e in items:
        cats[e.get('category', 'other')] = cats.get(e.get('category', 'other'), 0) + 1
    idx = {'format': 'stark-anim-library', 'version': 1, 'rig': 'stark_humanoid_v1', 'count': len(items),
           'categories': cats, 'clips': items}
    with open(os.path.join(lib, 'index.json'), 'w', encoding='utf-8', newline='\n') as f:
        json.dump(idx, f, indent=1)
    return idx


def search(query='', category=None, safe_for_child=False, loop=None, lib=LIB):
    """Library browser search: words in name/tags, optional category / loop filter, child-safe filter."""
    p = os.path.join(lib, 'index.json')
    if os.path.isfile(p):
        with open(p, encoding='utf-8') as f:
            idx = json.load(f)
    else:
        idx = write_index(lib)
    q = [w for w in re.split(r'\s+', query.lower()) if w]
    out = []
    for e in idx['clips']:
        if 'error' in e:
            continue
        hay = (e['name'] + ' ' + ' '.join(e.get('tags', [])) + ' ' + e.get('category', '')).lower()
        if q and not all(w in hay for w in q):
            continue
        if category and e.get('category') != category:
            continue
        if loop is not None and bool(e.get('loop')) != bool(loop):
            continue
        if safe_for_child and e.get('safety') in child_safety.BLOCKED_FOR_CHILD:
            continue
        out.append(e)
    return out
