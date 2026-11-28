"""bonemap.py - map any humanoid skeleton onto the Stark World Engine humanoid rig.

Three passes, each only filling what the previous left open:
  1. named profiles (Mixamo, Rokoko, Unreal UE4/UE5 Mannequin, CMU (BVH + ASF names), Biovision, OptiTrack Motive,
     3ds Max Biped, Xsens MVN, Unity Humanoid / VRM, Daz Genesis, Blender Rigify, Quaternius = Stark World Engine itself);
  2. fuzzy names (side + body-part keywords, chains validated parent -> child);
  3. pure topology (no usable names at all): hips = first branch point, legs = branches reaching lowest, arms = the
     chest's lateral branches, two longest bones of a limb = upper/lower segment, left/right from the toes' facing.
The spine and neck are always derived from topology between the mapped hips, neck and head, so 2-, 3-, 4- and
5-bone spines all land on spine_01..spine_03 sensibly (unmapped in-between joints are blended by the retargeter).
"""
import json, os, re

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
MAPS_JSON = os.path.join(HERE, 'data', 'bonemaps.json')

CORE = ['pelvis', 'thigh_l', 'calf_l', 'foot_l', 'thigh_r', 'calf_r', 'foot_r', 'upperarm_l', 'lowerarm_l',
        'hand_l', 'upperarm_r', 'lowerarm_r', 'hand_r', 'Head']
BODY = CORE + ['spine_01', 'spine_02', 'spine_03', 'neck_01', 'clavicle_l', 'clavicle_r', 'ball_l', 'ball_r']
FINGERS = [f'{f}_0{k}_{s}' for s in 'lr' for f in ('thumb', 'index', 'middle', 'ring', 'pinky') for k in (1, 2, 3)]


def _sided(table):
    """Expand {'thigh': ['LeftUpLeg']} with '{L}'/'{R}' style templates into per-side entries."""
    out = {}
    for canon, aliases in table.items():
        if '{s}' in canon:
            for s, L in (('l', 0), ('r', 1)):
                out[canon.replace('{s}', s)] = [a.format(S=('Left', 'Right')[L], s=('l', 'r')[L], X=('L', 'R')[L],
                                                         x=('l', 'r')[L]) for a in aliases]
        else:
            out[canon] = list(aliases)
    return out


def _fingers(fmt_list):
    """fmt_list: templates with {S} side word / {X} side letter, {F} finger name per style, {k} 1..3."""
    out = {}
    for f_i, f in enumerate(('thumb', 'index', 'middle', 'ring', 'pinky')):
        for k in (1, 2, 3):
            out[f'{f}_0{k}_{{s}}'] = [t(f_i, k) for t in fmt_list]
    return out


_FN = ['Thumb', 'Index', 'Middle', 'Ring', 'Pinky']
PROFILES = {
    'quaternius': _sided({
        'root': ['root'], 'pelvis': ['pelvis'], 'spine_01': ['spine_01'], 'spine_02': ['spine_02'],
        'spine_03': ['spine_03'], 'neck_01': ['neck_01'], 'Head': ['Head', 'head'],
        'clavicle_{s}': ['clavicle_{x}'], 'upperarm_{s}': ['upperarm_{x}'], 'lowerarm_{s}': ['lowerarm_{x}'],
        'hand_{s}': ['hand_{x}'], 'thigh_{s}': ['thigh_{x}'], 'calf_{s}': ['calf_{x}'], 'foot_{s}': ['foot_{x}'],
        'ball_{s}': ['ball_{x}'],
        **{f'{f}_0{k}_{{s}}': [f'{f}_0{k}_{{x}}'] for f in ('thumb', 'index', 'middle', 'ring', 'pinky')
           for k in (1, 2, 3)}}),
    'ue5_manny': _sided({
        'root': ['root'], 'pelvis': ['pelvis'], 'spine_01': ['spine_01'], 'spine_02': ['spine_03'],
        'spine_03': ['spine_05'], 'neck_01': ['neck_01'], 'Head': ['head'],
        'clavicle_{s}': ['clavicle_{x}'], 'upperarm_{s}': ['upperarm_{x}'], 'lowerarm_{s}': ['lowerarm_{x}'],
        'hand_{s}': ['hand_{x}'], 'thigh_{s}': ['thigh_{x}'], 'calf_{s}': ['calf_{x}'], 'foot_{s}': ['foot_{x}'],
        'ball_{s}': ['ball_{x}'],
        **{f'{f}_0{k}_{{s}}': [f'{f}_0{k}_{{x}}'] for f in ('thumb', 'index', 'middle', 'ring', 'pinky')
           for k in (1, 2, 3)}}),
    'mixamo': _sided({
        'pelvis': ['Hips'], 'spine_01': ['Spine'], 'spine_02': ['Spine1'], 'spine_03': ['Spine2'],
        'neck_01': ['Neck'], 'Head': ['Head'], 'clavicle_{s}': ['{S}Shoulder'], 'upperarm_{s}': ['{S}Arm'],
        'lowerarm_{s}': ['{S}ForeArm'], 'hand_{s}': ['{S}Hand'], 'thigh_{s}': ['{S}UpLeg'], 'calf_{s}': ['{S}Leg'],
        'foot_{s}': ['{S}Foot'], 'ball_{s}': ['{S}ToeBase'],
        **{f'{f}_0{k}_{{s}}': [f'{{S}}Hand{_FN[i]}{k}'] for i, f in enumerate(('thumb', 'index', 'middle', 'ring',
                                                                               'pinky')) for k in (1, 2, 3)}}),
    'rokoko': _sided({
        'pelvis': ['Hips'], 'spine_01': ['Spine1', 'Spine'], 'spine_02': ['Spine2'], 'spine_03': ['Spine4', 'Spine3'],
        'neck_01': ['Neck'], 'Head': ['Head'], 'clavicle_{s}': ['{S}Shoulder'], 'upperarm_{s}': ['{S}Arm'],
        'lowerarm_{s}': ['{S}ForeArm'], 'hand_{s}': ['{S}Hand'], 'thigh_{s}': ['{S}Thigh', '{S}UpLeg'],
        'calf_{s}': ['{S}Shin', '{S}Leg'], 'foot_{s}': ['{S}Foot'], 'ball_{s}': ['{S}Toe', '{S}ToeBase'],
        **{f'{f}_0{k}_{{s}}': [f'{{S}}{_FN[i]}{("Proximal", "Medial", "Distal")[k - 1]}',
                               f'{{S}}Finger{i + 1}{("Proximal", "Medial", "Distal")[k - 1]}']
           for i, f in enumerate(('thumb', 'index', 'middle', 'ring', 'pinky')) for k in (1, 2, 3)}}),
    'cmu': _sided({
        'pelvis': ['Hips', 'root'], 'spine_01': ['LowerBack', 'lowerback'], 'spine_02': ['Spine', 'upperback'],
        'spine_03': ['Spine1', 'thorax'], 'neck_01': ['Neck', 'lowerneck'], 'Head': ['Head', 'head'],
        'clavicle_{s}': ['{S}Shoulder', '{x}clavicle'], 'upperarm_{s}': ['{S}Arm', '{x}humerus'],
        'lowerarm_{s}': ['{S}ForeArm', '{x}radius'], 'hand_{s}': ['{S}Hand', '{x}wrist'],
        'thigh_{s}': ['{S}UpLeg', '{x}femur'], 'calf_{s}': ['{S}Leg', '{x}tibia'], 'foot_{s}': ['{S}Foot', '{x}foot'],
        'ball_{s}': ['{S}ToeBase', '{x}toes'], 'thumb_01_{s}': ['{X}Thumb', '{x}thumb'],
        'index_01_{s}': ['{S}HandIndex1', '{x}fingers']}),
    'biovision': _sided({
        'pelvis': ['Hips', 'Hip'], 'spine_01': ['Chest'], 'spine_03': ['Chest2', 'Chest3'], 'neck_01': ['Neck'],
        'Head': ['Head'], 'clavicle_{s}': ['{S}Collar'], 'upperarm_{s}': ['{S}Shoulder'],
        'lowerarm_{s}': ['{S}Elbow'], 'hand_{s}': ['{S}Wrist'], 'thigh_{s}': ['{S}Hip'], 'calf_{s}': ['{S}Knee'],
        'foot_{s}': ['{S}Ankle'], 'ball_{s}': ['{S}Toe']}),
    'optitrack': _sided({
        'pelvis': ['Hip', 'Hips'], 'spine_01': ['Ab'], 'spine_03': ['Chest'], 'neck_01': ['Neck'], 'Head': ['Head'],
        'clavicle_{s}': ['{X}Shoulder'], 'upperarm_{s}': ['{X}UArm'], 'lowerarm_{s}': ['{X}FArm'],
        'hand_{s}': ['{X}Hand'], 'thigh_{s}': ['{X}Thigh'], 'calf_{s}': ['{X}Shin'], 'foot_{s}': ['{X}Foot'],
        'ball_{s}': ['{X}Toe'],
        **{f'{f}_0{k}_{{s}}': [f'{{X}}{_FN[i]}{k}'] for i, f in enumerate(('thumb', 'index', 'middle', 'ring',
                                                                           'pinky')) for k in (1, 2, 3)}}),
    'biped': _sided({
        'pelvis': ['Bip01 Pelvis', 'Bip001 Pelvis'], 'spine_01': ['Bip01 Spine', 'Bip001 Spine'],
        'spine_02': ['Bip01 Spine1', 'Bip001 Spine1'], 'spine_03': ['Bip01 Spine2', 'Bip001 Spine2', 'Bip01 Spine3'],
        'neck_01': ['Bip01 Neck', 'Bip001 Neck'], 'Head': ['Bip01 Head', 'Bip001 Head'],
        'clavicle_{s}': ['Bip01 {X} Clavicle', 'Bip001 {X} Clavicle'],
        'upperarm_{s}': ['Bip01 {X} UpperArm', 'Bip001 {X} UpperArm'],
        'lowerarm_{s}': ['Bip01 {X} Forearm', 'Bip001 {X} Forearm'], 'hand_{s}': ['Bip01 {X} Hand', 'Bip001 {X} Hand'],
        'thigh_{s}': ['Bip01 {X} Thigh', 'Bip001 {X} Thigh'], 'calf_{s}': ['Bip01 {X} Calf', 'Bip001 {X} Calf'],
        'foot_{s}': ['Bip01 {X} Foot', 'Bip001 {X} Foot'], 'ball_{s}': ['Bip01 {X} Toe0', 'Bip001 {X} Toe0'],
        **{f'{f}_0{k}_{{s}}': [f'Bip01 {{X}} Finger{i}' + ('' if k == 1 else str(k - 1)),
                               f'Bip001 {{X}} Finger{i}' + ('' if k == 1 else str(k - 1))]
           for i, f in enumerate(('thumb', 'index', 'middle', 'ring', 'pinky')) for k in (1, 2, 3)}}),
    'xsens': _sided({
        'pelvis': ['Pelvis'], 'spine_01': ['L5'], 'spine_02': ['L3', 'T12'], 'spine_03': ['T8'], 'neck_01': ['Neck'],
        'Head': ['Head'], 'clavicle_{s}': ['{S}Shoulder'], 'upperarm_{s}': ['{S}UpperArm'],
        'lowerarm_{s}': ['{S}ForeArm'], 'hand_{s}': ['{S}Hand'], 'thigh_{s}': ['{S}UpperLeg'],
        'calf_{s}': ['{S}LowerLeg'], 'foot_{s}': ['{S}Foot'], 'ball_{s}': ['{S}Toe']}),
    'unity_vrm': _sided({
        'pelvis': ['Hips', 'J_Bip_C_Hips'], 'spine_01': ['Spine', 'J_Bip_C_Spine'],
        'spine_02': ['Chest', 'J_Bip_C_Chest'], 'spine_03': ['UpperChest', 'J_Bip_C_UpperChest'],
        'neck_01': ['Neck', 'J_Bip_C_Neck'], 'Head': ['Head', 'J_Bip_C_Head'],
        'clavicle_{s}': ['{S}Shoulder', 'J_Bip_{X}_Shoulder'], 'upperarm_{s}': ['{S}UpperArm', 'J_Bip_{X}_UpperArm'],
        'lowerarm_{s}': ['{S}LowerArm', 'J_Bip_{X}_LowerArm'], 'hand_{s}': ['{S}Hand', 'J_Bip_{X}_Hand'],
        'thigh_{s}': ['{S}UpperLeg', 'J_Bip_{X}_UpperLeg'], 'calf_{s}': ['{S}LowerLeg', 'J_Bip_{X}_LowerLeg'],
        'foot_{s}': ['{S}Foot', 'J_Bip_{X}_Foot'], 'ball_{s}': ['{S}Toes', 'J_Bip_{X}_ToeBase'],
        **{f'{f}_0{k}_{{s}}': [f'{{S}}{_FN[i]}{("Proximal", "Intermediate", "Distal")[k - 1]}',
                               f'J_Bip_{{X}}_{_FN[i] if i else "Thumb"}{k}'.replace('Pinky', 'Little')]
           for i, f in enumerate(('thumb', 'index', 'middle', 'ring', 'pinky')) for k in (1, 2, 3)}}),
    'daz': _sided({
        'pelvis': ['hip'], 'spine_01': ['abdomenLower', 'abdomen'], 'spine_02': ['abdomenUpper', 'abdomen2'],
        'spine_03': ['chestUpper', 'chestLower', 'chest'], 'neck_01': ['neckLower', 'neck'], 'Head': ['head'],
        'clavicle_{s}': ['{x}Collar'], 'upperarm_{s}': ['{x}ShldrBend', '{x}Shldr'],
        'lowerarm_{s}': ['{x}ForearmBend', '{x}ForeArm'], 'hand_{s}': ['{x}Hand'],
        'thigh_{s}': ['{x}ThighBend', '{x}Thigh'], 'calf_{s}': ['{x}Shin'], 'foot_{s}': ['{x}Foot'],
        'ball_{s}': ['{x}Toe']}),
    'rigify': _sided({
        'pelvis': ['DEF-spine', 'spine', 'DEF-hips', 'hips'], 'spine_01': ['DEF-spine.001', 'spine.001'],
        'spine_02': ['DEF-spine.002', 'spine.002'], 'spine_03': ['DEF-spine.003', 'spine.003'],
        'neck_01': ['DEF-spine.004', 'spine.004', 'DEF-neck', 'neck'], 'Head': ['DEF-spine.006', 'spine.006',
                                                                              'DEF-head', 'head'],
        'clavicle_{s}': ['DEF-shoulder.{X}', 'shoulder.{X}'], 'upperarm_{s}': ['DEF-upper_arm.{X}', 'upper_arm.{X}'],
        'lowerarm_{s}': ['DEF-forearm.{X}', 'forearm.{X}'], 'hand_{s}': ['DEF-hand.{X}', 'hand.{X}'],
        'thigh_{s}': ['DEF-thigh.{X}', 'thigh.{X}'], 'calf_{s}': ['DEF-shin.{X}', 'shin.{X}'],
        'foot_{s}': ['DEF-foot.{X}', 'foot.{X}'], 'ball_{s}': ['DEF-toe.{X}', 'toe.{X}']}),
}
PROFILE_NAMES = {'quaternius': 'Quaternius / Stark World Engine humanoid', 'ue5_manny': 'Unreal Engine 5 Mannequin (Manny/Quinn)',
                 'mixamo': 'Mixamo', 'rokoko': 'Rokoko', 'cmu': 'CMU mocap (BVH / ASF names)',
                 'biovision': 'Biovision BVH', 'optitrack': 'OptiTrack Motive', 'biped': '3ds Max Biped',
                 'xsens': 'Xsens MVN', 'unity_vrm': 'Unity Humanoid / VRM', 'daz': 'Daz Genesis',
                 'rigify': 'Blender Rigify'}

SKIP_WORDS = ('twist', 'roll', 'ik', 'pole', 'target', 'nub', 'tip', 'helper', 'correct', 'corrective', 'end',
              'leaf', 'prop', 'weapon', 'attach', 'socket', 'dummy', 'null', 'bend2', 'share')


def norm_name(n):
    """'mixamorig:LeftArm' -> 'leftarm'; 'Armature|Hips' -> 'hips'; 'Bip01 L Thigh' -> 'bip01lthigh'."""
    n = str(n)
    n = re.split(r'[:|]', n)[-1]
    return re.sub(r'[^a-z0-9]', '', n.lower())


def profile_match(names, profile):
    """-> {canonical: source name} for one profile (first alias present wins)."""
    lookup = {}
    for nm in names:
        lookup.setdefault(norm_name(nm), nm)
    out = {}
    for canon, aliases in PROFILES[profile].items():
        for a in aliases:
            hit = lookup.get(norm_name(a))
            if hit is not None and hit not in out.values():
                out[canon] = hit
                break
    return out


# names that only one rig family has: break ties between profiles that otherwise match equally well
DISTINCT = {'ue5_manny': ['spine_05', 'neck_02', 'spine_04'], 'mixamo': ['mixamorighips', 'headtopend'],
            'cmu': ['lhipjoint', 'lowerback', 'lthumb'], 'rokoko': ['spine4', 'leftshin'], 'xsens': ['l5', 't8'],
            'biped': ['bip01pelvis', 'bip001pelvis'], 'unity_vrm': ['jbipchips', 'upperchest'],
            'daz': ['abdomenlower', 'lshldrbend'], 'rigify': ['defspine', 'defthighl'], 'optitrack': ['luarm', 'lfarm']}


def _score(m, names=None):
    s = sum(3 for c in CORE if c in m) + sum(1 for c in m if c not in CORE)
    return s


# ---------------------------------------------------------------- fuzzy names
_SIDE_PATTERNS = [
    (re.compile(r'(^|[^a-z])left|left($|[^a-z])', re.I), 'l'), (re.compile(r'(^|[^a-z])right|right($|[^a-z])', re.I), 'r'),
    (re.compile(r'[_.\s-]l$|^l[_.\s-]|[_.\s-]l[_.\s-]', re.I), 'l'), (re.compile(r'[_.\s-]r$|^r[_.\s-]|[_.\s-]r[_.\s-]', re.I), 'r'),
    (re.compile(r'^L(?=[A-Z])'), 'l'), (re.compile(r'^R(?=[A-Z])'), 'r'), (re.compile(r'^l(?=[A-Z])'), 'l'),
    (re.compile(r'^r(?=[A-Z])'), 'r'), (re.compile(r'Left'), 'l'), (re.compile(r'Right'), 'r'),
]
PARTS = [  # (canonical part, keywords) - first keyword hit wins, so specific words come first
    ('foot', ('foot', 'ankle')), ('ball', ('toebase', 'toe', 'ball')),
    ('lowerarm', ('forearm', 'lowerarm', 'elbow', 'radius', 'farm', 'ulna')),
    ('upperarm', ('upperarm', 'uparm', 'humerus', 'shldr', 'uarm')),
    ('hand', ('hand', 'wrist', 'palm')), ('clavicle', ('clavicle', 'collar', 'scapula')),
    ('calf', ('calf', 'shin', 'lowerleg', 'knee', 'tibia', 'crus')),
    ('thigh', ('thigh', 'upleg', 'upperleg', 'femur')),
    ('neck', ('neck',)), ('Head', ('head', 'skull')),
]


def side_of(name):
    base = re.split(r'[:|]', str(name))[-1]
    for pat, s in _SIDE_PATTERNS:
        if pat.search(base):
            return s
    return None


def part_of(name):
    n = norm_name(name)
    if any(w in n for w in SKIP_WORDS) and not n.endswith(('hand', 'foot', 'head')):
        return None
    for part, kws in PARTS:
        for k in kws:
            if k in n:
                return part
    if re.search(r'(?<!up)arm', n):
        return 'upperarm'
    if re.search(r'leg', n):
        return 'calf'
    if 'shoulder' in n:
        return 'clavicle?'
    if re.search(r'hip|pelvis', n):
        return 'thigh?' if side_of(name) else 'pelvis'
    return None


def fuzzy_match(skel, have):
    out = dict(have)
    used = set(out.values())
    cands = {}
    for i, nm in enumerate(skel.names):
        if nm in used:
            continue
        p, s = part_of(nm), side_of(nm)
        if p is None:
            continue
        cands.setdefault((p, s), []).append(i)

    def take(canon, keys):
        if canon in out:
            return
        for k in keys:
            lst = [i for i in cands.get(k, []) if skel.names[i] not in out.values()]
            if lst:
                # prefer the joint closest to the root (the main bone, not a helper below it)
                lst.sort(key=lambda i: len(skel.chain(skel.roots[0], i)) or 999)
                out[canon] = skel.names[lst[0]]
                return

    take('pelvis', [('pelvis', None)])
    for s in 'lr':
        take(f'thigh_{s}', [('thigh', s), ('thigh?', s)])
        take(f'calf_{s}', [('calf', s)])
        take(f'foot_{s}', [('foot', s)])
        take(f'ball_{s}', [('ball', s)])
        take(f'upperarm_{s}', [('upperarm', s)])
        take(f'lowerarm_{s}', [('lowerarm', s)])
        take(f'hand_{s}', [('hand', s)])
        take(f'clavicle_{s}', [('clavicle', s), ('clavicle?', s)])
        # 'shoulder' on a rig without any other upper-arm bone is the upper arm
        if f'upperarm_{s}' not in out and out.get(f'clavicle_{s}') and part_of(out[f'clavicle_{s}']) == 'clavicle?':
            out[f'upperarm_{s}'] = out.pop(f'clavicle_{s}')
    take('neck_01', [('neck', None)])
    take('Head', [('Head', None)])
    return out


# ---------------------------------------------------------------- topology
def _leaf_paths(skel, i):
    out = []
    for d in skel.descendants(i):
        if not skel.children[d]:
            out.append(d)
    return out


def _up_axis(gp):
    ext = gp.max(0) - gp.min(0)
    ax = int(np.argmax(ext))
    v = np.zeros(3)
    v[ax] = 1.0
    lo, hi = gp[:, ax].min(), gp[:, ax].max()
    span = max(hi - lo, 1e-9)
    near_lo = np.sum(gp[:, ax] < lo + 0.08 * span)
    near_hi = np.sum(gp[:, ax] > hi - 0.08 * span)
    return v if near_lo >= near_hi else -v


def _subtree_length(skel, gp, i):
    return sum(float(np.linalg.norm(gp[d] - gp[skel.parents[d]])) for d in skel.descendants(i, include=False))


def _find_hips(skel, gp):
    """First joint (walking down from the root) with three significant branches (two legs + spine); helper chains
    such as UE's ik_foot_root / ik_hand_root are too small to count."""
    total = _subtree_length(skel, gp, skel.roots[0]) or 1.0
    i = skel.roots[0]
    for _ in range(len(skel)):
        sig = [c for c in skel.children[i]
               if _subtree_length(skel, gp, c) + float(np.linalg.norm(gp[c] - gp[i])) > 0.12 * total]
        if len(sig) >= 3 or not skel.children[i]:
            return i
        i = max(skel.children[i], key=lambda c: _subtree_length(skel, gp, c))
    return skel.roots[0]


def _up_from_legs(skel, gp, hips):
    """Gravity from structure: of the hips' branches, the two whose far ends point the same way are the legs."""
    total = _subtree_length(skel, gp, hips) or 1.0
    br = [c for c in skel.children[hips] if _subtree_length(skel, gp, c) + np.linalg.norm(gp[c] - gp[hips]) > 0.12 * total]
    if len(br) < 3:
        return None
    vec = []
    for b in br:
        leaves = _leaf_paths(skel, b)
        far = max(leaves, key=lambda l: np.linalg.norm(gp[l] - gp[hips]))
        v = gp[far] - gp[hips]
        vec.append(v / max(np.linalg.norm(v), 1e-9))
    best, pair = -2.0, None
    for i in range(len(vec)):
        for j in range(i + 1, len(vec)):
            c = float(np.dot(vec[i], vec[j]))
            if c > best:
                best, pair = c, (i, j)
    if pair is None or best < 0.5:
        return None
    up = -(vec[pair[0]] + vec[pair[1]])
    up /= max(np.linalg.norm(up), 1e-9)
    ax = int(np.argmax(np.abs(up)))
    if abs(up[ax]) > 0.866:
        up = np.zeros(3)
        up[ax] = 1.0 if (-(vec[pair[0]] + vec[pair[1]]))[ax] > 0 else -1.0
    return up


def topology_match(skel, have, gp=None):
    """Fill missing limbs/hips/head from the hierarchy + rest positions only."""
    out = dict(have)
    gp = skel.rest_globals()[1] if gp is None else gp
    idx = skel.index
    # hips: walk from the root while there is a single child
    hips = idx(out['pelvis']) if 'pelvis' in out else None
    if hips is None:
        hips = _find_hips(skel, gp)
        out['pelvis'] = skel.names[hips]
    up = _up_from_legs(skel, gp, hips)
    if up is None:
        up = _up_axis(gp)
    h = gp @ up
    tot = _subtree_length(skel, gp, hips) or 1.0
    branches = [c for c in skel.children[hips] if _subtree_length(skel, gp, c) + np.linalg.norm(gp[c] - gp[hips]) > 0.12 * tot]
    # branch with the highest leaf = spine; the two with the lowest leaves = legs
    info = []
    for b in branches:
        leaves = _leaf_paths(skel, b)
        info.append((b, min(h[l] for l in leaves), max(h[l] for l in leaves)))
    if len(info) < 3:
        return out, up
    legs = sorted(info, key=lambda x: x[1])[:2]
    spine = max(info, key=lambda x: x[2])[0]

    def limb(chain_root, upper, lower, end, extra=None):
        leaves = _leaf_paths(skel, chain_root)
        leaf = min(leaves, key=lambda l: h[l]) if extra == 'leg' else max(
            leaves, key=lambda l: np.linalg.norm(gp[l] - gp[chain_root]))
        ch = skel.chain(chain_root, leaf)
        lens = [np.linalg.norm(gp[ch[k + 1]] - gp[ch[k]]) for k in range(len(ch) - 1)]
        if len(lens) < 2:
            return {}
        if extra == 'arm':
            # anatomy, not length ranking (a long clavicle can beat the forearm): the hand is where the fingers
            # branch, or the first joint after which only short (finger) bones remain
            total = sum(lens)
            hand = None
            for k in range(2, len(ch)):
                rest = sum(lens[k:])
                branching = [c for c in skel.children[ch[k]] if skel.children[c]]  # twist/helper leaves do not count
                if len(branching) >= 2 or rest < 0.22 * total:
                    hand = k
                    break
            if hand is None:
                hand = len(ch) - 1
            res = {end: ch[hand], lower: ch[hand - 1], upper: ch[hand - 2]}
            # zero-length connector joints (CMU LeftShoulder at the chest) count as the clavicle when present
            if hand - 3 >= 0:
                res['clavicle'] = ch[hand - 3]
            return res
        order = sorted(range(len(lens)), key=lambda k: -lens[k])[:2]
        a, b = sorted(order)
        res = {upper: ch[a], lower: ch[b]}
        if b + 1 < len(ch):
            res[end] = ch[b + 1]
        if extra == 'leg' and b + 2 < len(ch) and skel.children[ch[b + 2]]:
            res['ball'] = ch[b + 2]
        return res

    leg_maps = [limb(l[0], 'thigh', 'calf', 'foot', 'leg') for l in legs]
    # chest = first joint up the spine with >= 3 children (neck + 2 arms)
    i = spine
    while len(skel.children[i]) < 3 and skel.children[i]:
        i = max(skel.children[i], key=lambda c: max(h[l] for l in _leaf_paths(skel, c)))
    chest = i
    arm_roots, head_root = [], None
    if len(skel.children[chest]) >= 3:
        kids = skel.children[chest]
        head_root = max(kids, key=lambda c: max(h[l] for l in _leaf_paths(skel, c)))
        arm_roots = [c for c in kids if c != head_root][:2]
    arm_maps = [limb(a, 'upperarm', 'lowerarm', 'hand', 'arm') for a in arm_roots]
    # facing: toes point forward; else assume +Z
    fwd = np.array([0, 0, 1.0])
    toes = [m['ball'] for m in leg_maps if 'ball' in m] or [m['foot'] for m in leg_maps if 'foot' in m]
    feet = [m['foot'] for m in leg_maps if 'foot' in m]
    if toes and feet and len(toes) == len(feet) and toes != feet:
        f = np.mean([gp[t] - gp[fo] for t, fo in zip(toes, feet)], axis=0)
        f -= up * np.dot(f, up)
        if np.linalg.norm(f) > 1e-6:
            fwd = f / np.linalg.norm(f)
    left = np.cross(up, fwd)

    def side(m):
        k = next(iter(m.values()))
        return 'l' if np.dot(gp[k] - gp[hips], left) > 0 else 'r'

    for m in leg_maps + arm_maps:
        if not m:
            continue
        s = side(m)
        for part, j in m.items():
            canon = f'{part}_{s}'
            if canon not in out and skel.names[j] not in out.values():
                out[canon] = skel.names[j]
    if 'Head' not in out and head_root is not None:
        leaves = _leaf_paths(skel, head_root)
        top = max(leaves, key=lambda l: h[l])
        ch = skel.chain(head_root, top)
        height = float(h.max() - h.min()) or 1.0
        bone = float(np.linalg.norm(gp[top] - gp[skel.parents[top]])) if skel.parents[top] >= 0 else 0.0
        tip = (skel.names[top] in (skel.meta.get('end_sites') or []) or
               bool(re.search(r'end|top|nub|tip|leaf', norm_name(skel.names[top]))) or bone > 0.07 * height)
        cand = ch[-2] if len(ch) >= 2 and tip else ch[-1]
        out['Head'] = skel.names[cand]
    return out, up


# ---------------------------------------------------------------- spine/neck from topology
def derive_spine(skel, m):
    """Re-derive spine_01..03 and neck_01 from the chain hips -> head (or hips -> neck -> head)."""
    if 'pelvis' not in m or 'Head' not in m:
        return m
    hips, head = skel.index(m['pelvis']), skel.index(m['Head'])
    ch = skel.chain(hips, head)
    if len(ch) < 2:
        return m
    # chest = where the clavicles/arms branch off (deepest common ancestor of the two upper arms on this chain)
    chest_pos = None
    for key in ('clavicle_l', 'upperarm_l', 'clavicle_r', 'upperarm_r'):
        if key in m:
            a = skel.index(m[key])
            anc = set()
            p = a
            while p >= 0:
                anc.add(p)
                p = skel.parents[p]
            on = [k for k, j in enumerate(ch) if j in anc]
            if on:
                chest_pos = on[-1] if chest_pos is None else min(chest_pos, on[-1])
    if chest_pos is None or chest_pos == 0:
        chest_pos = max(1, len(ch) - 3)
    spine = ch[1:chest_pos + 1]
    neck = ch[chest_pos + 1:-1]
    out = {k: v for k, v in m.items() if k not in ('spine_01', 'spine_02', 'spine_03', 'neck_01')}
    if len(spine) >= 1:
        out['spine_03'] = skel.names[spine[-1]]
    if len(spine) >= 2:
        out['spine_01'] = skel.names[spine[0]]
    if len(spine) >= 3:
        out['spine_02'] = skel.names[spine[len(spine) // 2]] if len(spine) != 4 else skel.names[spine[1]]
    if neck:
        out['neck_01'] = skel.names[neck[0]]
    return out


def validate(skel, m):
    """Drop mappings that break parent -> child order; returns (map, problems)."""
    problems = []
    chains = [('pelvis', 'thigh_{s}', 'calf_{s}', 'foot_{s}', 'ball_{s}'),
              ('spine_03', 'clavicle_{s}', 'upperarm_{s}', 'lowerarm_{s}', 'hand_{s}')]
    out = dict(m)
    for s in 'lr':
        for ch in chains:
            prev = None
            for c in ch:
                c = c.format(s=s)
                if c not in out:
                    continue
                if prev is not None:
                    a, b = skel.index(out[prev]), skel.index(out[c])
                    if not skel.chain(a, b):
                        problems.append(f'{c} ({out[c]}) is not below {prev} ({out[prev]})')
                        del out[c]
                        continue
                prev = c
    return out, problems


def auto_map(skel, prefer=None):
    """-> dict(map={canonical: source}, profile, method, coverage, problems, up)."""
    best, best_p, best_s = {}, None, -1
    normed = {norm_name(n) for n in skel.names}
    for p in ([prefer] if prefer else []) + [k for k in PROFILES if k != prefer]:
        m = profile_match(skel.names, p)
        sc = _score(m) + 2 * sum(1 for d in DISTINCT.get(p, []) if norm_name(d) in normed) if m else -1
        if sc > best_s:
            best, best_p, best_s = m, p, sc
    method = ['profile:' + best_p] if best_p and best else []
    m = best
    m2 = fuzzy_match(skel, m)
    if len(m2) > len(m):
        method.append('fuzzy-names')
    m = m2
    m3, up = topology_match(skel, m)
    if len(m3) > len(m):
        method.append('topology')
    m = derive_spine(skel, m3)
    m, problems = validate(skel, m)
    missing = [c for c in CORE if c not in m]
    return {'map': m, 'profile': best_p, 'method': '+'.join(method) or 'none', 'missing_core': missing,
            'coverage': round(sum(c in m for c in BODY) / len(BODY), 3), 'problems': problems, 'up': up.tolist()}


def export_json(path=MAPS_JSON):
    """Write the profile tables for the JS runtime (name binding) and for documentation."""
    d = {'about': 'Stark World Engine humanoid bone-name profiles: canonical Stark World Engine joint -> aliases per source rig. '
                  'Names are matched after norm(): drop namespace (text up to the last : or |), lowercase, '
                  'keep only a-z0-9. Generated by bonemap.export_json().',
         'profiles': {k: {'title': PROFILE_NAMES.get(k, k), 'joints': v} for k, v in PROFILES.items()}}
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(d, f, indent=1)
    return path
