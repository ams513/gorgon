"""python -m stark_anim <command> ...

  import FILE [--out DIR] [--fps 30] [--root-motion extract|in_place|keep] [--raw] [--preview]
                                  BVH / glTF / GLB / FBX (installed converter) -> retargeted, cleaned .gclip.json
  clean CLIP [--smooth 1.5] [--lock] [--loop] [--mirror] [--speed X] [--trim A B] [--footsteps] [--hit] [--out F]
  mocap TAKE.json [--out F] [--live-smoothing]   webcam / replay landmark take -> clip
  preview CLIP [--out BASE] [--fps 20] [--size 360x400] [--character male_adventurer] [--no-mp4]
  export CLIP... --target godot|ue5 [--out PATH] [--ue5-bake]
  library build | index | search [WORDS] [--category C] [--child]
  models status | fetch --approved
  rig build --from CHARACTER.gltf   |   bonemaps export
  map FILE                        show how a skeleton's joints map onto the Stark World Engine rig
"""
import argparse, json, os, sys


def _clip(path):
    from .clip import Clip
    return Clip.load(path)


def cmd_import(a):
    from .retarget import import_motion
    from . import cleanup, child_safety
    res = import_motion(a.file, fps=a.fps, root_motion=a.root_motion)
    out_dir = a.out or os.path.dirname(os.path.abspath(a.file))
    os.makedirs(out_dir, exist_ok=True)
    for clip, rep in res:
        c = clip
        if not a.raw:
            c = cleanup.smooth(c, 1.0)
            c = cleanup.lock_feet(c)
            c = cleanup.add_footstep_events(c)
        child_safety.classify_clip(c)
        base = os.path.join(out_dir, os.path.splitext(os.path.basename(a.file))[0] + ('' if len(res) == 1 else '_' + clip.name))
        c.save(base + '.gclip.json')
        print(json.dumps({'saved': base + '.gclip.json', 'profile': rep['profile'], 'method': rep['method'],
                          'source_rest_pose': rep['source_rest_pose'], 'scale': rep['scale'],
                          'direction_error_deg': rep['direction_error_deg'], 'summary': c.summary()}, indent=1))
        if a.preview:
            from .preview import Previewer
            with Previewer() as pv:
                print(pv.render(c, base, fps=20, loops=2))


def cmd_clean(a):
    from . import cleanup
    c = _clip(a.clip)
    if a.trim:
        c = cleanup.trim(c, a.trim[0], a.trim[1])
    if a.smooth:
        c = cleanup.smooth(c, a.smooth)
    if a.lock:
        c = cleanup.lock_feet(c)
    if a.speed:
        c = cleanup.speed(c, a.speed)
    if a.mirror:
        c = cleanup.mirror(c)
    if a.loop:
        c = cleanup.make_loop(c)
    if a.footsteps:
        c = cleanup.add_footstep_events(c)
    if a.hit:
        c = cleanup.add_hit_event(c)
    out = a.out or a.clip.replace('.gclip.json', '_clean.gclip.json')
    c.save(out)
    print(json.dumps({'saved': out, 'summary': c.summary(), 'foot_sliding_cm': round(cleanup.foot_sliding(c) * 100, 2)}))


def cmd_mocap(a):
    from . import mocap, cleanup
    take = mocap.load_take(a.take)
    c, rep = mocap.take_to_clip(take, smooth='live' if a.live_smoothing else True)
    c = cleanup.add_footstep_events(cleanup.lock_feet(c))
    out = a.out or a.take.replace('.json', '') + '.gclip.json'
    c.save(out)
    print(json.dumps({'saved': out, 'report': rep, 'summary': c.summary()}))


def cmd_preview(a):
    from .preview import Previewer, find_character_model
    w, h = (int(x) for x in a.size.lower().split('x'))
    with Previewer(w, h, model=find_character_model(a.character)) as pv:
        print(json.dumps(pv.render(a.clip, a.out or a.clip.replace('.gclip.json', ''), fps=a.fps, loops=a.loops, mp4=not a.no_mp4)))


def cmd_export(a):
    from . import export_map
    clips = [_clip(p) for p in a.clips]
    if a.target == 'godot':
        print(export_map.write_godot_library(clips, a.out or 'animations.tres'))
    else:
        for f in export_map.write_ue5_package(clips, a.out or 'ue5_export', ue5_bake=a.ue5_bake):
            print(f)


def cmd_library(a):
    from . import library
    if a.action == 'build':
        library.build_quaternius()
    elif a.action == 'index':
        print(json.dumps({k: v for k, v in library.write_index().items() if k != 'clips'}))
    else:
        for e in library.search(' '.join(a.words), a.category, a.child):
            print(f"{e['id']:40s} {e['category']:11s} {e['duration']:6.2f}s loop={e['loop']!s:5s} {e['safety']}")


def cmd_models(a):
    from . import model_store
    return model_store.main([a.action] + (['--approved'] if a.approved else []))


def cmd_rig(a):
    from .rig import build_rig_from_gltf
    print(build_rig_from_gltf(a.source))


def cmd_bonemaps(a):
    from .bonemap import export_json
    print(export_json())


def cmd_map(a):
    from .retarget import load_source
    from .bonemap import auto_map
    skel, clips, _ = load_source(a.file)
    m = auto_map(skel)
    print(json.dumps({'profile': m['profile'], 'method': m['method'], 'coverage': m['coverage'],
                      'missing_core': m['missing_core'], 'problems': m['problems'], 'map': m['map']}, indent=1))


def main(argv=None):
    ap = argparse.ArgumentParser(prog='python -m stark_anim', description='Stark World Engine animation + mocap tools')
    sp = ap.add_subparsers(dest='cmd', required=True)
    p = sp.add_parser('import'); p.add_argument('file'); p.add_argument('--out'); p.add_argument('--fps', type=float, default=30)
    p.add_argument('--root-motion', default='extract', choices=['extract', 'in_place', 'keep']); p.add_argument('--raw', action='store_true')
    p.add_argument('--preview', action='store_true'); p.set_defaults(fn=cmd_import)
    p = sp.add_parser('clean'); p.add_argument('clip'); p.add_argument('--smooth', type=float); p.add_argument('--lock', action='store_true')
    p.add_argument('--loop', action='store_true'); p.add_argument('--mirror', action='store_true'); p.add_argument('--speed', type=float)
    p.add_argument('--trim', type=float, nargs=2); p.add_argument('--footsteps', action='store_true'); p.add_argument('--hit', action='store_true')
    p.add_argument('--out'); p.set_defaults(fn=cmd_clean)
    p = sp.add_parser('mocap'); p.add_argument('take'); p.add_argument('--out'); p.add_argument('--live-smoothing', action='store_true')
    p.set_defaults(fn=cmd_mocap)
    p = sp.add_parser('preview'); p.add_argument('clip'); p.add_argument('--out'); p.add_argument('--fps', type=float, default=20)
    p.add_argument('--size', default='360x400'); p.add_argument('--character', default='male_adventurer'); p.add_argument('--loops', type=float, default=2)
    p.add_argument('--no-mp4', action='store_true'); p.set_defaults(fn=cmd_preview)
    p = sp.add_parser('export'); p.add_argument('clips', nargs='+'); p.add_argument('--target', required=True, choices=['godot', 'ue5'])
    p.add_argument('--out'); p.add_argument('--ue5-bake', action='store_true'); p.set_defaults(fn=cmd_export)
    p = sp.add_parser('library'); p.add_argument('action', choices=['build', 'index', 'search']); p.add_argument('words', nargs='*')
    p.add_argument('--category'); p.add_argument('--child', action='store_true'); p.set_defaults(fn=cmd_library)
    p = sp.add_parser('models'); p.add_argument('action', choices=['status', 'fetch']); p.add_argument('--approved', action='store_true')
    p.set_defaults(fn=cmd_models)
    p = sp.add_parser('rig'); p.add_argument('action', choices=['build']); p.add_argument('--from', dest='source', required=True); p.set_defaults(fn=cmd_rig)
    p = sp.add_parser('bonemaps'); p.add_argument('action', choices=['export']); p.set_defaults(fn=cmd_bonemaps)
    p = sp.add_parser('map'); p.add_argument('file'); p.set_defaults(fn=cmd_map)
    a = ap.parse_args(argv)
    r = a.fn(a)
    return r if isinstance(r, int) else 0


if __name__ == '__main__':
    sys.exit(main())
