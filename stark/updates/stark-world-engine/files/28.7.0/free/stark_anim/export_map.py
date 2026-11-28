"""export_map.py - how Stark World Engine clips reach Unreal Engine 5 and Godot 4 (for the bridge department's exporters).

UE5: the Stark World Engine rig uses UE4-Mannequin bone names, so the clean path is
  1. import the Stark World Engine / Quaternius character + clip GLBs (write_ue5_package) with the AnimSequence settings in
     ue5_import.json (Interchange glTF importer, one skeleton asset shared by every clip),
  2. retarget to the UE5 Mannequin with an IK Retargeter whose chains are in ue5_retarget.json (the same chain names
     as Epic's IK_Mannequin, so Manny/Quinn, MetaHumans and any UE5 skeleton work).
  ue5_bake=True additionally bakes each clip onto a UE5-named skeleton here (approximate Manny proportions) for
  pipelines without an IK Retargeter.
Godot 4: write_godot_library -> AnimationLibrary .tres with rotation_3d / position_3d bone tracks, root motion baked
  onto the 'root' bone (AnimationTree.root_motion_track), and a method track calling _on_anim_event(type, data) for
  footstep / hit events. Every export carries the clip's safety class so engine-side code can enforce the
  child-safety rule too.
"""
import json, os, re

import numpy as np

from . import qmath as Q
from .clip import Clip
from .rig import stark_rig

# Stark World Engine joint -> UE5 Manny bone (UE5 has spine_01..05 and neck_01..02; the extras are blended by the retargeter)
UE5_MAP = {'root': 'root', 'pelvis': 'pelvis', 'spine_01': 'spine_01', 'spine_02': 'spine_03', 'spine_03': 'spine_05',
           'neck_01': 'neck_01', 'Head': 'head'}
for _s in 'lr':
    UE5_MAP.update({f'clavicle_{_s}': f'clavicle_{_s}', f'upperarm_{_s}': f'upperarm_{_s}', f'lowerarm_{_s}': f'lowerarm_{_s}',
                    f'hand_{_s}': f'hand_{_s}', f'thigh_{_s}': f'thigh_{_s}', f'calf_{_s}': f'calf_{_s}',
                    f'foot_{_s}': f'foot_{_s}', f'ball_{_s}': f'ball_{_s}'})
    for _f in ('thumb', 'index', 'middle', 'ring', 'pinky'):
        for _k in (1, 2, 3):
            UE5_MAP[f'{_f}_0{_k}_{_s}'] = f'{_f}_0{_k}_{_s}'

# IK Retargeter chains: name -> (Stark World Engine start, Stark World Engine end, UE5 start, UE5 end); names follow Epic's IK_Mannequin
UE5_CHAINS = [('Root', 'root', 'root', 'root', 'root'), ('Spine', 'spine_01', 'spine_03', 'spine_01', 'spine_05'),
              ('Neck', 'neck_01', 'neck_01', 'neck_01', 'neck_02'), ('Head', 'Head', 'Head', 'head', 'head')]
for _S, _s in (('Left', 'l'), ('Right', 'r')):
    UE5_CHAINS += [(f'{_S}Clavicle', f'clavicle_{_s}', f'clavicle_{_s}', f'clavicle_{_s}', f'clavicle_{_s}'),
                   (f'{_S}Arm', f'upperarm_{_s}', f'hand_{_s}', f'upperarm_{_s}', f'hand_{_s}'),
                   (f'{_S}Leg', f'thigh_{_s}', f'ball_{_s}', f'thigh_{_s}', f'ball_{_s}')]
    for _F, _f in (('Thumb', 'thumb'), ('Index', 'index'), ('Middle', 'middle'), ('Ring', 'ring'), ('Pinky', 'pinky')):
        UE5_CHAINS.append((f'{_S}{_F}', f'{_f}_01_{_s}', f'{_f}_03_{_s}', f'{_f}_01_{_s}', f'{_f}_03_{_s}'))
UE5_IK_GOALS = {'foot_l': 'LeftFootIK', 'foot_r': 'RightFootIK', 'hand_l': 'LeftHandIK', 'hand_r': 'RightHandIK'}


def ue5_retarget_spec():
    return {'about': 'IK Rig + IK Retargeter definition: Stark World Engine (UE4-Mannequin names) -> UE5 Mannequin',
            'source_skeleton': 'Stark World Engine humanoid (Quaternius, 65 joints)', 'target_skeleton': 'SK_Mannequin (UE5)',
            'retarget_root': {'stark': 'pelvis', 'ue5': 'pelvis'},
            'chains': [{'name': n, 'stark': [a, b], 'ue5': [c, d]} for n, a, b, c, d in UE5_CHAINS],
            'ik_goals': [{'bone': k, 'goal': v, 'chain': ('Left' if k.endswith('_l') else 'Right') +
                          ('Leg' if k.startswith('foot') else 'Arm')} for k, v in UE5_IK_GOALS.items()],
            'bone_map': UE5_MAP,
            'notes': ['Stark World Engine rest pose is a T-pose; UE5 Manny is an A-pose: set the retarget pose of the Stark World Engine IK '
                      'rig to its reference (T) pose and let the retargeter align (Auto-Align All Bones).',
                      'Root motion: clips with root motion move the "root" bone; enable "Root Motion" on the AnimSequence.']}


def ue5_import_settings(clip, skeleton='/Game/Stark/Characters/SK_Stark_Skeleton'):
    """AnimSequence import options (Interchange glTF / FBX pipeline names) for one clip."""
    return {'clip': clip.name, 'file': re.sub(r'[^A-Za-z0-9_]+', '_', clip.name) + '.glb', 'skeleton': skeleton,
            'import_meshes': False, 'import_animations': True, 'import_uniform_scale': 1.0,
            'convert_scene_unit': True, 'force_front_x_axis': False,
            'animation': {'use_default_sample_rate': False, 'custom_sample_rate': int(round(clip.fps)),
                          'snap_to_closest_frame_boundary': True, 'animation_length': 'ExportedTime',
                          'remove_redundant_keys': False, 'import_bone_tracks': True,
                          'delete_existing_morph_target_curves': False, 'do_not_import_curve_with_zero': True},
            'anim_sequence': {'enable_root_motion': clip.motion is not None, 'root_motion_root_lock': 'RefPose',
                              'force_root_lock': False, 'loop': bool(clip.loop),
                              'notifies': [{'time': e['t'], 'name': 'Stark_' + str(e.get('type', 'event')),
                                            'payload': {k: v for k, v in e.items() if k not in ('t', 'type')}}
                                           for e in clip.events]},
            'metadata': {'stark_safety': clip.meta.get('safety', 'neutral'), 'stark_tags': clip.tags,
                         'source': clip.meta.get('source')}}


def bake_root_motion(clip, rig=None):
    """Clip with clip.motion -> same motion with the trajectory/heading on the 'root' joint (engines that read root
    motion from a bone: UE5, Godot AnimationTree)."""
    rig = rig or stark_rig()
    if clip.motion is None:
        return clip.copy()
    c = clip.copy()
    ri = c.jindex('root')
    if ri is None:
        return c
    m = clip.motion
    c.rot[:, ri] = Q.q_mul(Q.q_from_yaw(m[:, 3]), np.broadcast_to(rig.rest_r[rig.index('root')], (c.frames, 4)))
    c.pos[:, ri] = m[:, :3]
    c.motion = None
    c.meta = dict(c.meta, root_motion_baked=True)
    return c


def write_ue5_package(clips, out_dir, rig=None, ue5_bake=False):
    """One GLB per clip on the Stark World Engine skeleton (root motion on 'root') + ue5_import.json + ue5_retarget.json."""
    from .gltf_io import write_glb
    rig = rig or stark_rig()
    os.makedirs(out_dir, exist_ok=True)
    files, settings = [], []
    for c in clips:
        b = bake_root_motion(c, rig)
        fn = re.sub(r'[^A-Za-z0-9_]+', '_', c.name) + '.glb'
        write_glb(os.path.join(out_dir, fn), rig, [b], generator='Stark World Engine stark_anim (UE5 export)')
        files.append(os.path.join(out_dir, fn))
        settings.append(ue5_import_settings(c))
        if ue5_bake:
            from .retarget import retarget
            from .testrigs import ue5_manny_skeleton
            T = ue5_manny_skeleton()
            tc, _ = retarget(rig, b, target=T, root_motion='keep')
            fn2 = re.sub(r'[^A-Za-z0-9_]+', '_', c.name) + '_UE5Manny.glb'
            write_glb(os.path.join(out_dir, fn2), T, [tc], generator='Stark World Engine stark_anim (UE5 Manny bake)')
            files.append(os.path.join(out_dir, fn2))
    for name, data in (('ue5_import.json', {'clips': settings}), ('ue5_retarget.json', ue5_retarget_spec())):
        p = os.path.join(out_dir, name)
        with open(p, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(data, f, indent=1)
        files.append(p)
    return files


# ---------------------------------------------------------------- Godot 4
def _f(v):
    s = f'{float(v):.6g}'
    return '0' if s in ('-0', '-0.0') else s


def _packed(rows):
    return 'PackedFloat32Array(' + ', '.join(_f(x) for r in rows for x in r) + ')'


def _gd_value(v):
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, (int, float)):
        return _f(v)
    if isinstance(v, dict):
        return '{' + ', '.join(f'{json.dumps(str(k))}: {_gd_value(x)}' for k, x in v.items()) + '}'
    if isinstance(v, (list, tuple)):
        return '[' + ', '.join(_gd_value(x) for x in v) + ']'
    return json.dumps(str(v))


def godot_animation(clip, skeleton_path='Armature/Skeleton3D', rig=None, events_method='_on_anim_event',
                    sub_id=None, const_eps=1e-6):
    """One Godot 4 Animation sub-resource (text). Root motion is baked onto the root bone."""
    rig = rig or stark_rig()
    c = bake_root_motion(clip, rig)
    F = c.frames
    ts = np.arange(F) / c.fps
    lines = [f'[sub_resource type="Animation" id="{sub_id}"]', f'resource_name = {json.dumps(c.name)}',
             f'length = {_f(max(c.duration, 1.0 / c.fps))}', f'loop_mode = {1 if c.loop else 0}', f'step = {_f(1.0 / c.fps)}']
    k = 0
    rot = Q.q_continuity(c.rot, 0)
    for j, n in enumerate(c.joints):
        r = rot[:, j]
        p = c.pos[:, j]
        tracks = []
        if np.max(Q.q_angle_between(r, r[:1])) > const_eps or n in ('root', 'pelvis'):
            tracks.append(('rotation_3d', [[t, 1.0, *v] for t, v in zip(ts, r)]))
        if n in ('root', 'pelvis') and (np.max(np.abs(p - p[:1])) > const_eps or n == 'pelvis'):
            tracks.append(('position_3d', [[t, 1.0, *v] for t, v in zip(ts, p)]))
        for typ, keys in tracks:
            lines += [f'tracks/{k}/type = "{typ}"', f'tracks/{k}/imported = false', f'tracks/{k}/enabled = true',
                      f'tracks/{k}/path = NodePath("{skeleton_path}:{n}")', f'tracks/{k}/interp = 1',
                      f'tracks/{k}/loop_wrap = true', f'tracks/{k}/keys = {_packed(keys)}']
            k += 1
    if c.events:
        vals = ', '.join('{"args": [%s, %s], "method": &"%s"}' % (json.dumps(str(e.get('type', 'event'))),
                                                                  _gd_value({a: b for a, b in e.items() if a not in ('t', 'type')}),
                                                                  events_method) for e in c.events)
        lines += [f'tracks/{k}/type = "method"', f'tracks/{k}/imported = false', f'tracks/{k}/enabled = true',
                  f'tracks/{k}/path = NodePath(".")', f'tracks/{k}/interp = 1', f'tracks/{k}/loop_wrap = true',
                  f'tracks/{k}/keys = {{', f'"times": PackedFloat32Array({", ".join(_f(e["t"]) for e in c.events)}),',
                  f'"transitions": PackedFloat32Array({", ".join("1" for _ in c.events)}),', f'"values": [{vals}]', '}']
    lines += [f'metadata/stark_safety = {json.dumps(c.meta.get("safety", "neutral"))}',
              f'metadata/stark_tags = {_gd_value(list(c.tags))}']
    return '\n'.join(lines)


def write_godot_library(clips, path, skeleton_path='Armature/Skeleton3D', rig=None):
    """Godot 4 AnimationLibrary (.tres) holding every clip; load it on an AnimationPlayer (add_animation_library)."""
    rig = rig or stark_rig()
    ids, subs = [], []
    for i, c in enumerate(clips):
        sid = f'Animation_{i}_{re.sub(r"[^A-Za-z0-9]", "", c.name)[:24]}'
        ids.append((c.name, sid))
        subs.append(godot_animation(c, skeleton_path, rig, sub_id=sid))
    out = [f'[gd_resource type="AnimationLibrary" load_steps={len(clips) + 1} format=3]', '']
    for s in subs:
        out += [s, '']
    out += ['[resource]', '_data = {']
    out += [',\n'.join(f'&{json.dumps(n)}: SubResource("{sid}")' for n, sid in ids)]
    out += ['}', '']
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(out))
    return path
