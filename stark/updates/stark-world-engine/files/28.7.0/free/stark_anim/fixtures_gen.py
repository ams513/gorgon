"""fixtures_gen.py - (re)build the test fixtures in stark_anim/fixtures from the CC0 starter library.

  python -m stark_anim.fixtures_gen
Motion fixtures are real clips re-targeted onto other rig families and written in their native formats, so the
importers are tested on files that look like what creators bring (CMU BVH with inverted-V legs and zero-length
connector bones, a Z-up A-pose Biovision BVH in inches, a Mixamo-named GLB in centimetres with bone-aligned rest
rotations, a UE5 Manny GLB with 5 spine bones). Mocap fixtures are MediaPipe-format takes with known ground truth.
take_mediapipe_*.json come from the REAL Pose Landmarker run on rendered frames (webcam/mediapipe_selftest.html)
and are not rebuilt here.
"""
import json, os

from . import bvh, gltf_io, mocap, testrigs
from .clip import Clip
from .retarget import retarget
from .rig import stark_rig

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, 'fixtures')
LIB = os.path.join(HERE, 'library', 'quaternius_ual')


def lib_clip(name, in_place=True):
    c = Clip.load(os.path.join(LIB, name + '.gclip.json'))
    if in_place:
        c.motion = None
    return c


def make_all(log=print):
    os.makedirs(FIX, exist_ok=True)
    R = stark_rig()
    out = []
    jobs = [('cmu_walk.bvh', 'Walk_Loop', testrigs.cmu_skeleton, 'bvh'),
            ('biovision_zup_apose_sword.bvh', 'Sword_Attack', testrigs.biovision_zup_apose, 'bvh'),
            ('mixamo_jog.glb', 'Jog_Fwd_Loop', testrigs.mixamo_skeleton, 'glb'),
            ('ue5_manny_dance.glb', 'Dance_Loop', testrigs.ue5_manny_skeleton, 'glb')]
    for fn, clip_name, mk, fmt in jobs:
        src = lib_clip(clip_name)
        T = mk()
        tc, rep = retarget(R, src, target=T, root_motion='keep')
        tc.name = clip_name
        p = os.path.join(FIX, fn)
        if fmt == 'bvh':
            bvh.write_bvh(p, T, tc)
        else:
            gltf_io.write_glb(p, T, [tc], generator='stark_anim fixtures')
        out.append(p)
        log(f'  {fn:34s} from {clip_name:14s} ({len(T)} joints, out-trip error {rep["direction_error_deg"]})')
    takes = [('take_synthetic_walk.json', 'Walk_Loop', True), ('take_synthetic_talk.json', 'Idle_Talking_Loop', True),
             ('take_synthetic_dance_2d.json', 'Dance_Loop', False)]
    for fn, clip_name, world in takes:
        t = mocap.synth_take(lib_clip(clip_name), noise_mm=4.0, world=world, name=clip_name + '_synthetic')
        mocap.save_take(t, os.path.join(FIX, fn))
        out.append(os.path.join(FIX, fn))
        log(f'  {fn:34s} {len(t["frames"])} frames, world landmarks: {world}')
    vis = {'rhubarb': {'metadata': {'soundFile': 'hello.wav', 'duration': 1.2},
                       'mouthCues': [{'start': 0.0, 'end': 0.1, 'value': 'X'}, {'start': 0.1, 'end': 0.25, 'value': 'C'},
                                     {'start': 0.25, 'end': 0.4, 'value': 'H'}, {'start': 0.4, 'end': 0.7, 'value': 'E'},
                                     {'start': 0.7, 'end': 0.9, 'value': 'F'}, {'start': 0.9, 'end': 1.2, 'value': 'X'}]},
           # stark_audio.lipsync.compact(): ms deltas, Rhubarb-style shapes, openness digits
           'stark_audio_compact': {'d': [0, 100, 150, 150, 300, 200], 'v': 'XCHEFX', 'o': '054620'}}
    for k, v in vis.items():
        p = os.path.join(FIX, f'visemes_{k}.json')
        with open(p, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(v, f, indent=1)
        out.append(p)
    return out


if __name__ == '__main__':
    make_all()
