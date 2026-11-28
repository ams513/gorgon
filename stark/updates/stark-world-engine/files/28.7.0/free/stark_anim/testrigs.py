"""testrigs.py - reference skeletons of other rig families, for fixtures, round-trip tests and export targets.

  cmu_skeleton()       CMU mocap BVH (cgspeed layout): Y-up, +Z forward, ~5.6 cm units, inverted-V legs,
                       zero-length LHipJoint / LowerBack / Neck / LeftShoulder connector bones, end sites
  mixamo_skeleton()    'mixamorig:' names, centimetres, bones oriented along +Y (FBX/Blender style rest rotations)
  biovision_zup_apose() classic BVH names (LeftCollar/LeftShoulder/LeftElbow), Z-up, facing -Y, A-pose, inches
  ue5_manny_skeleton() UE5 Manny/Quinn names: 5 spine + 2 neck bones, twist + ik bones, glTF axes, metres
Offsets are realistic but rebuilt by hand (no third-party data is copied).
"""
import numpy as np

from . import qmath as Q
from .rig import Skeleton


def skeleton_from_offsets(spec, bone_aligned=False, scale=1.0, basis=None, meta=None, end_sites=()):
    """spec: [(name, parent name or None, (x, y, z) offset in world axes at rest)]."""
    names = [s[0] for s in spec]
    idx = {n: i for i, n in enumerate(names)}
    parents = [idx[s[1]] if s[1] else -1 for s in spec]
    off = np.array([s[2] for s in spec], np.float64) * scale
    J = len(names)
    if basis is not None:
        off = Q.q_rotate(np.broadcast_to(basis, (J, 4)), off)
    if not bone_aligned:
        sk = Skeleton(names, parents, off, Q.quat_identity((J,)), meta=meta)
    else:
        kids = [[] for _ in range(J)]
        for i, p in enumerate(parents):
            if p >= 0:
                kids[p].append(i)
        R = np.zeros((J, 4))
        for i in range(J):
            d = None
            for c in kids[i]:
                if np.linalg.norm(off[c]) > 1e-9:
                    d = off[c]
                    break
            if d is None:
                R[i] = R[parents[i]] if parents[i] >= 0 else Q.quat_identity()
            else:
                R[i] = Q.q_between(np.array([0, 1.0, 0]), d)
        lr = np.zeros((J, 4))
        lt = np.zeros((J, 3))
        for i in range(J):
            p = parents[i]
            if p < 0:
                lr[i], lt[i] = R[i], off[i]
            else:
                lr[i] = Q.q_mul(Q.q_conj(R[p]), R[i])
                lt[i] = Q.q_rotate(Q.q_conj(R[p]), off[i])
        sk = Skeleton(names, parents, lt, lr, meta=meta)
    sk.meta['end_sites'] = list(end_sites)
    return sk


def cmu_skeleton():
    S = [('Hips', None, (0, 0, 0)), ('LHipJoint', 'Hips', (0, 0, 0)),
         ('LeftUpLeg', 'LHipJoint', (1.6567, -1.8028, 0.6248)), ('LeftLeg', 'LeftUpLeg', (2.5972, -7.1358, 0)),
         ('LeftFoot', 'LeftLeg', (2.4924, -6.8477, 0)), ('LeftToeBase', 'LeftFoot', (0.197, -0.5414, 2.1458)),
         ('LeftToeBase_End', 'LeftToeBase', (0, 0, 1.1125)),
         ('RHipJoint', 'Hips', (0, 0, 0)), ('RightUpLeg', 'RHipJoint', (-1.6107, -1.8028, 0.6248)),
         ('RightLeg', 'RightUpLeg', (-2.595, -7.1298, 0)), ('RightFoot', 'RightLeg', (-2.4678, -6.7802, 0)),
         ('RightToeBase', 'RightFoot', (-0.2302, -0.6326, 2.1337)),
         ('RightToeBase_End', 'RightToeBase', (0, 0, 1.1157)),
         ('LowerBack', 'Hips', (0, 0, 0)), ('Spine', 'LowerBack', (0.0196, 2.0545, -0.1411)),
         ('Spine1', 'Spine', (0.0102, 2.0644, -0.0592)), ('Neck', 'Spine1', (0, 0, 0)),
         ('Neck1', 'Neck', (0.0071, 1.5671, 0.1497)), ('Head', 'Neck1', (0.0343, 1.5604, -0.1001)),
         ('Head_End', 'Head', (0.013, 1.6256, -0.0475)),
         ('LeftShoulder', 'Spine1', (0, 0, 0)), ('LeftArm', 'LeftShoulder', (3.542, 0.9044, -0.1736)),
         ('LeftForeArm', 'LeftArm', (4.8651, 0, 0)), ('LeftHand', 'LeftForeArm', (3.3555, 0, 0)),
         ('LeftFingerBase', 'LeftHand', (0, 0, 0)), ('LeftHandIndex1', 'LeftFingerBase', (0.6612, 0, 0)),
         ('LeftHandIndex1_End', 'LeftHandIndex1', (0.5331, 0, 0)), ('LThumb', 'LeftHand', (0, 0, 0)),
         ('LThumb_End', 'LThumb', (0.5412, 0, 0.5412)),
         ('RightShoulder', 'Spine1', (0, 0, 0)), ('RightArm', 'RightShoulder', (-3.498, 0.7599, -0.3262)),
         ('RightForeArm', 'RightArm', (-5.0262, 0, 0)), ('RightHand', 'RightForeArm', (-3.3767, 0, 0)),
         ('RightFingerBase', 'RightHand', (0, 0, 0)), ('RightHandIndex1', 'RightFingerBase', (-0.7069, 0, 0)),
         ('RightHandIndex1_End', 'RightHandIndex1', (-0.5699, 0, 0)), ('RThumb', 'RightHand', (0, 0, 0)),
         ('RThumb_End', 'RThumb', (-0.5786, 0, 0.5786))]
    ends = [s[0] for s in S if s[0].endswith('_End')]
    return skeleton_from_offsets(S, meta={'name': 'cmu', 'units': 'cmu'}, end_sites=ends)


def mixamo_skeleton():
    p = 'mixamorig:'
    S = [('Hips', None, (0, 99.8, 0)), ('Spine', 'Hips', (0, 9.9, 1.2)), ('Spine1', 'Spine', (0, 11.6, 0)),
         ('Spine2', 'Spine1', (0, 13.3, 0)), ('Neck', 'Spine2', (0, 15.0, -1.0)), ('Head', 'Neck', (0, 10.2, 2.0)),
         ('HeadTop_End', 'Head', (0, 18.5, 0.5))]
    for side, sx in (('Left', 1), ('Right', -1)):
        S += [(f'{side}Shoulder', 'Spine2', (sx * 6.1, 9.1, -0.2)), (f'{side}Arm', f'{side}Shoulder', (sx * 12.9, 0, 0)),
              (f'{side}ForeArm', f'{side}Arm', (sx * 27.4, 0, 0)), (f'{side}Hand', f'{side}ForeArm', (sx * 27.6, 0, 0))]
        for f, (dy, dz) in (('Thumb', (-1.5, 3.0)), ('Index', (0, 2.6)), ('Middle', (0, 0.4)), ('Ring', (0, -1.6)),
                            ('Pinky', (0, -3.2))):
            prev = f'{side}Hand'
            for k, ln in ((1, 4.0 if f == 'Thumb' else 9.5), (2, 3.6), (3, 2.9), (4, 2.5)):
                off = (sx * ln, dy, dz) if k == 1 else (sx * ln, 0, 0)
                if f == 'Thumb' and k == 1:
                    off = (sx * 3.0, -1.6, 3.3)
                S.append((f'{side}Hand{f}{k}', prev, off))
                prev = f'{side}Hand{f}{k}'
        S += [(f'{side}UpLeg', 'Hips', (sx * 9.1, -6.6, 0.1)), (f'{side}Leg', f'{side}UpLeg', (0, -42.1, 0.4)),
              (f'{side}Foot', f'{side}Leg', (0, -41.9, -2.2)), (f'{side}ToeBase', f'{side}Foot', (0, -7.9, 11.6)),
              (f'{side}Toe_End', f'{side}ToeBase', (0, 0, 8.0))]
    S = [(p + n, (p + par) if par else None, o) for n, par, o in S]
    return skeleton_from_offsets(S, bone_aligned=True, meta={'name': 'mixamo', 'units': 'cm'})


def biovision_zup_apose():
    """Z-up, character faces -Y (Blender's front), so its left side is +X; arms 40 degrees below horizontal, inches."""
    c, s = np.cos(np.radians(40)), np.sin(np.radians(40))
    S = [('Hips', None, (0, 0, 39.0)), ('Chest', 'Hips', (0, 0.3, 8.0)), ('Chest2', 'Chest', (0, 0, 8.5)),
         ('Neck', 'Chest2', (0, 0.4, 6.5)), ('Head', 'Neck', (0, -0.6, 4.5)), ('Head_End', 'Head', (0, 0, 7.5))]
    for side, sx in (('Left', 1), ('Right', -1)):
        S += [(f'{side}Collar', 'Chest2', (sx * 1.5, 0, 5.2)), (f'{side}Shoulder', f'{side}Collar', (sx * 5.0, 0, 0)),
              (f'{side}Elbow', f'{side}Shoulder', (sx * 11.0 * c, 0, -11.0 * s)),
              (f'{side}Wrist', f'{side}Elbow', (sx * 10.0 * c, 0, -10.0 * s)),
              (f'{side}Wrist_End', f'{side}Wrist', (sx * 3.0 * c, 0, -3.0 * s)),
              (f'{side}Hip', 'Hips', (sx * 3.6, 0, -1.5)), (f'{side}Knee', f'{side}Hip', (0, 0, -17.5)),
              (f'{side}Ankle', f'{side}Knee', (0, 0.3, -17.0)), (f'{side}Toe', f'{side}Ankle', (0, -4.6, -2.6)),
              (f'{side}Toe_End', f'{side}Toe', (0, -2.0, 0))]
    ends = [x[0] for x in S if x[0].endswith('_End')]
    return skeleton_from_offsets(S, meta={'name': 'biovision_zup', 'units': 'inch'}, end_sites=ends)


def ue5_manny_skeleton():
    """UE5 Manny bone names in glTF axes (+Y up, facing +Z, left = +X), metres, bones along +X (UE style)."""
    S = [('root', None, (0, 0, 0)), ('pelvis', 'root', (0, 0.96, -0.01)),
         ('spine_01', 'pelvis', (0, 0.05, 0.01)), ('spine_02', 'spine_01', (0, 0.10, 0.0)),
         ('spine_03', 'spine_02', (0, 0.10, -0.005)), ('spine_04', 'spine_03', (0, 0.09, -0.005)),
         ('spine_05', 'spine_04', (0, 0.12, -0.01)), ('neck_01', 'spine_05', (0, 0.12, 0.01)),
         ('neck_02', 'neck_01', (0, 0.05, 0.01)), ('head', 'neck_02', (0, 0.05, 0.01)),
         ('ik_foot_root', 'root', (0, 0, 0)), ('ik_hand_root', 'root', (0, 0, 0))]
    for sx, s in ((1, 'l'), (-1, 'r')):
        S += [(f'clavicle_{s}', 'spine_05', (sx * 0.03, 0.06, 0.05)), (f'upperarm_{s}', f'clavicle_{s}', (sx * 0.16, 0, -0.04)),
              (f'upperarm_twist_01_{s}', f'upperarm_{s}', (sx * 0.0, 0, 0)), (f'lowerarm_{s}', f'upperarm_{s}', (sx * 0.28, 0, 0)),
              (f'lowerarm_twist_01_{s}', f'lowerarm_{s}', (sx * 0.12, 0, 0)), (f'hand_{s}', f'lowerarm_{s}', (sx * 0.26, 0, 0))]
        for f, dz in (('thumb', 0.03), ('index', 0.025), ('middle', 0.005), ('ring', -0.015), ('pinky', -0.03)):
            prev = f'hand_{s}'
            if f != 'thumb':
                S.append((f'{f}_metacarpal_{s}', prev, (sx * 0.03, 0, dz)))
                prev = f'{f}_metacarpal_{s}'
            for k, ln in ((1, 0.06 if f != 'thumb' else 0.03), (2, 0.035), (3, 0.025)):
                off = (sx * ln, 0, 0) if k > 1 or f != 'thumb' else (sx * 0.025, -0.015, 0.035)
                S.append((f'{f}_0{k}_{s}', prev, off))
                prev = f'{f}_0{k}_{s}'
        S += [(f'thigh_{s}', 'pelvis', (sx * 0.09, -0.03, 0.0)), (f'thigh_twist_01_{s}', f'thigh_{s}', (0, -0.2, 0)),
              (f'calf_{s}', f'thigh_{s}', (0, -0.44, 0.01)), (f'calf_twist_01_{s}', f'calf_{s}', (0, -0.2, 0)),
              (f'foot_{s}', f'calf_{s}', (0, -0.42, -0.03)), (f'ball_{s}', f'foot_{s}', (0, -0.07, 0.13)),
              (f'ik_foot_{s}', 'ik_foot_root', (sx * 0.09, 0.08, -0.04))]
    return skeleton_from_offsets(S, bone_aligned=True, meta={'name': 'ue5_manny', 'units': 'm'})
