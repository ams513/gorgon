"""rig.py - skeletons, forward kinematics, rest-pose analysis and the Stark World Engine humanoid rig.

The Stark World Engine humanoid rig is the 65-joint skeleton shared by the Quaternius Universal Base Characters and the
Universal Animation Library (Unreal-style names: root, pelvis, spine_01..03, neck_01, Head, clavicle_l, upperarm_l,
lowerarm_l, hand_l, fingers, thigh_l, calf_l, foot_l, ball_l ...). Its definition ships as
data/stark_humanoid.rig.json (numbers only, derived from the CC0 base character), so nothing at runtime needs the
original asset pack.

Space convention (same as glTF): metres, +Y up, the character faces +Z, its left side is +X.
"""
import json, os

import numpy as np

from . import qmath as Q

HERE = os.path.dirname(os.path.abspath(__file__))
RIG_PATH = os.path.join(HERE, 'data', 'stark_humanoid.rig.json')
RIG_ID = 'stark_humanoid_v1'

# semantic roles of the Stark World Engine rig joints (used by retargeting, IK, mirroring and the runtime)
ROLE = {
    'root': 'root', 'pelvis': 'hips', 'spine_01': 'spine1', 'spine_02': 'spine2', 'spine_03': 'chest',
    'neck_01': 'neck', 'Head': 'head',
    'clavicle_l': 'shoulder_l', 'upperarm_l': 'upperarm_l', 'lowerarm_l': 'forearm_l', 'hand_l': 'hand_l',
    'clavicle_r': 'shoulder_r', 'upperarm_r': 'upperarm_r', 'lowerarm_r': 'forearm_r', 'hand_r': 'hand_r',
    'thigh_l': 'thigh_l', 'calf_l': 'shin_l', 'foot_l': 'foot_l', 'ball_l': 'toe_l',
    'thigh_r': 'thigh_r', 'calf_r': 'shin_r', 'foot_r': 'foot_r', 'ball_r': 'toe_r',
}
for _side in 'lr':
    for _f in ('thumb', 'index', 'middle', 'ring', 'pinky'):
        for _k in (1, 2, 3):
            ROLE[f'{_f}_0{_k}_{_side}'] = f'{_f}{_k}_{_side}'
ROLE_TO_JOINT = {v: k for k, v in ROLE.items()}
UPPER_BODY_ROOT = 'spine_01'  # layered upper-body actions start here


def mirror_name(name):
    """Left/right partner of a joint name (Stark World Engine '_l'/'_r' suffixes plus the common Left/Right spellings)."""
    for a, b in (('_l', '_r'), ('_L', '_R'), ('.l', '.r'), ('.L', '.R')):
        if name.endswith(a):
            return name[:-len(a)] + b
        if name.endswith(b):
            return name[:-len(b)] + a
    for a, b in (('Left', 'Right'), ('left', 'right'), ('LEFT', 'RIGHT')):
        if a in name:
            return name.replace(a, b, 1)
        if b in name:
            return name.replace(b, a, 1)
    for a, b in (('L_', 'R_'), ('l_', 'r_'), ('L ', 'R ')):
        if name.startswith(a):
            return b + name[len(a):]
        if name.startswith(b):
            return a + name[len(b):]
    return name


class Skeleton:
    """Joint hierarchy with rest local transforms. parents[i] < 0 for roots. Scales are kept for reference but
    retargeting and FK treat joints as rigid (scales baked into translations at import)."""

    def __init__(self, names, parents, rest_t, rest_r, rest_s=None, meta=None):
        self.names = list(names)
        self.parents = np.asarray(parents, np.int64)
        n = len(self.names)
        self.rest_t = np.asarray(rest_t, np.float64).reshape(n, 3)
        self.rest_r = Q.q_normalize(np.asarray(rest_r, np.float64).reshape(n, 4))
        self.rest_s = np.ones((n, 3)) if rest_s is None else np.asarray(rest_s, np.float64).reshape(n, 3)
        self.meta = dict(meta or {})
        self._index = {nm: i for i, nm in enumerate(self.names)}
        self.children = [[] for _ in range(n)]
        for i, p in enumerate(self.parents):
            if p >= 0:
                self.children[p].append(i)
        self.order = self._topo()

    def _topo(self):
        order, seen = [], set()
        roots = [i for i, p in enumerate(self.parents) if p < 0]
        stack = list(reversed(roots))
        while stack:
            i = stack.pop()
            if i in seen:
                continue
            seen.add(i)
            order.append(i)
            stack.extend(reversed(self.children[i]))
        if len(order) != len(self.names):
            raise ValueError('skeleton has a cycle or a parent index out of range')
        return np.asarray(order)

    def __len__(self):
        return len(self.names)

    def index(self, name, default=None):
        return self._index.get(name, default)

    def has(self, name):
        return name in self._index

    @property
    def roots(self):
        return [i for i, p in enumerate(self.parents) if p < 0]

    def fk(self, local_r=None, local_t=None, root_r=None, root_t=None):
        """Forward kinematics. local_r [..., J, 4] (default rest), local_t [..., J, 3] (default rest).
        Returns (global_r [..., J, 4], global_p [..., J, 3])."""
        lr = self.rest_r if local_r is None else np.asarray(local_r, np.float64)
        lt = self.rest_t if local_t is None else np.asarray(local_t, np.float64)
        shape = np.broadcast_shapes(lr.shape[:-2], lt.shape[:-2])
        lr = np.broadcast_to(lr, shape + lr.shape[-2:])
        lt = np.broadcast_to(lt, shape + lt.shape[-2:])
        gr = np.empty(shape + (len(self), 4))
        gp = np.empty(shape + (len(self), 3))
        for i in self.order:
            p = self.parents[i]
            if p < 0:
                r, t = lr[..., i, :], lt[..., i, :]
                if root_r is not None:
                    r = Q.q_mul(root_r, r)
                    t = Q.q_rotate(root_r, t)
                if root_t is not None:
                    t = t + root_t
                gr[..., i, :] = r
                gp[..., i, :] = t
            else:
                gr[..., i, :] = Q.q_mul(gr[..., p, :], lr[..., i, :])
                gp[..., i, :] = gp[..., p, :] + Q.q_rotate(gr[..., p, :], lt[..., i, :])
        return gr, gp

    def rest_globals(self):
        if not hasattr(self, '_rest_g'):
            self._rest_g = self.fk()
        return self._rest_g

    def to_local(self, global_r, global_p=None):
        """Inverse FK: global rotations (and optional positions) -> local rotations (and translations)."""
        gr = np.asarray(global_r, np.float64)
        lr = np.empty_like(gr)
        lt = None if global_p is None else np.empty(gr.shape[:-1] + (3,))
        for i in range(len(self)):
            p = self.parents[i]
            if p < 0:
                lr[..., i, :] = gr[..., i, :]
                if lt is not None:
                    lt[..., i, :] = global_p[..., i, :]
            else:
                ip = Q.q_conj(gr[..., p, :])
                lr[..., i, :] = Q.q_mul(ip, gr[..., i, :])
                if lt is not None:
                    lt[..., i, :] = Q.q_rotate(ip, global_p[..., i, :] - global_p[..., p, :])
        return Q.q_normalize(lr), lt

    def chain(self, start, end):
        """Joint indices from start down to end (inclusive), or [] when end is not below start."""
        s, e = self.index(start) if isinstance(start, str) else start, self.index(end) if isinstance(end, str) else end
        if s is None or e is None:
            return []
        out = [e]
        while out[-1] != s:
            p = self.parents[out[-1]]
            if p < 0:
                return []
            out.append(int(p))
        return out[::-1]

    def descendants(self, i, include=True):
        i = self.index(i) if isinstance(i, str) else i
        out, stack = [], [i]
        while stack:
            j = stack.pop()
            out.append(j)
            stack.extend(self.children[j])
        return out if include else out[1:]

    def bone_length(self, i):
        i = self.index(i) if isinstance(i, str) else i
        return float(np.linalg.norm(self.rest_t[i]))

    def to_dict(self):
        return {'names': self.names, 'parents': [int(p) for p in self.parents],
                'rest_t': np.round(self.rest_t, 6).tolist(), 'rest_r': np.round(self.rest_r, 7).tolist(),
                'rest_s': np.round(self.rest_s, 6).tolist(), 'meta': self.meta}

    @classmethod
    def from_dict(cls, d):
        return cls(d['names'], d['parents'], d['rest_t'], d['rest_r'], d.get('rest_s'), d.get('meta'))

    def copy(self):
        return Skeleton.from_dict(self.to_dict())

    def reordered(self, names):
        """Same skeleton restricted/reordered to `names` (each kept joint's parent becomes its nearest kept
        ancestor; rest transforms re-expressed accordingly)."""
        keep = [self.index(n) for n in names]
        if any(k is None for k in keep):
            raise KeyError('unknown joint in reorder list')
        gr, gp = self.rest_globals()
        pos = {k: i for i, k in enumerate(keep)}
        parents, lt, lr = [], [], []
        for k in keep:
            p = self.parents[k]
            while p >= 0 and p not in pos:
                p = self.parents[p]
            parents.append(pos[p] if p >= 0 else -1)
            if p < 0:
                lt.append(gp[k])
                lr.append(gr[k])
            else:
                ip = Q.q_conj(gr[p])
                lt.append(Q.q_rotate(ip, gp[k] - gp[p]))
                lr.append(Q.q_mul(ip, gr[k]))
        return Skeleton(names, parents, lt, lr, meta=self.meta)


# ---------------------------------------------------------------- rest-pose analysis
def _first(skel, names):
    for n in names:
        i = skel.index(n)
        if i is not None:
            return i
    return None


def arm_pose_angle(skel, upperarm, forearm, global_p=None):
    """Angle (degrees) of the upper arm below horizontal: ~0 = T-pose, 30..55 = A-pose."""
    gp = skel.rest_globals()[1] if global_p is None else global_p
    d = gp[forearm] - gp[upperarm]
    up = skel.meta.get('up', [0, 1, 0])
    d = d / max(np.linalg.norm(d), 1e-9)
    return float(np.degrees(np.arcsin(np.clip(-np.dot(d, up), -1, 1))))


def classify_rest_pose(angle_deg):
    if angle_deg < 15:
        return 'T-pose'
    if angle_deg < 65:
        return 'A-pose'
    return 'arms-down'


def leg_length(skel, thigh, calf, foot):
    gp = skel.rest_globals()[1]
    return float(np.linalg.norm(gp[calf] - gp[thigh]) + np.linalg.norm(gp[foot] - gp[calf]))


# ---------------------------------------------------------------- the Stark World Engine humanoid rig
_RIG = None


def stark_rig():
    """The canonical Stark World Engine humanoid Skeleton (cached). Meta carries skin order + inverse bind matrices."""
    global _RIG
    if _RIG is None:
        with open(RIG_PATH, encoding='utf-8') as f:
            d = json.load(f)
        _RIG = Skeleton.from_dict(d['skeleton'])
        _RIG.meta.update({k: v for k, v in d.items() if k != 'skeleton'})
    return _RIG


def build_rig_from_gltf(path, out_path=RIG_PATH):
    """Dev step: derive data/stark_humanoid.rig.json from a Quaternius base character (.gltf/.glb)."""
    from .gltf_io import load_gltf_skeleton
    skel, info = load_gltf_skeleton(path)
    gr, gp = skel.rest_globals()
    i = skel.index
    up_arm = arm_pose_angle(skel, i('upperarm_l'), i('lowerarm_l'))
    feet_y = min(gp[i('foot_l')][1], gp[i('ball_l')][1])
    meta = {
        'id': RIG_ID, 'source': os.path.basename(path), 'licence': 'CC0 1.0 (derived from Quaternius Universal Base '
        'Characters)', 'up': [0, 1, 0], 'forward': [0, 0, 1], 'left': [1, 0, 0], 'units': 'metres',
        'rest_pose': classify_rest_pose(up_arm), 'arm_angle_deg': round(up_arm, 2),
        'hip_height': round(float(gp[i('pelvis')][1]), 5), 'foot_joint_height': round(float(feet_y), 5),
        'leg_length': round(leg_length(skel, i('thigh_l'), i('calf_l'), i('foot_l')), 5),
        'height': round(float(gp[:, 1].max() - min(0.0, gp[:, 1].min())), 4),
        'upper_body_root': UPPER_BODY_ROOT, 'roles': ROLE,
    }
    skel.meta.update(meta)
    d = {'format': 'stark-rig', 'version': 1, 'skeleton': skel.to_dict(), 'skin_joints': info['skin_joints'],
         'inverse_bind': info['inverse_bind'], 'armature_node': info.get('armature')}
    with open(out_path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(d, f, separators=(',', ':'))
    global _RIG
    _RIG = None
    return out_path
