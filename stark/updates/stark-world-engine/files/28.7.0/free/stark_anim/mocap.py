"""mocap.py - webcam / video motion capture, Python reference implementation (the studio runs the same algorithm
live in webcam/stark_mocap.js; tests check the two agree).

Input: a *take* - MediaPipe Pose Landmarker results per frame (33 BlazePose landmarks: normalised image coords
'landmarks' and, when the model provides them, metric hip-centred 'worldLandmarks'), optional face blendshapes.
  take = {"format": "stark-mocap-take", "version": 1, "fps": 30, "source": "webcam|replay|video|synthetic",
          "image": {"width": 640, "height": 480, "mirrored": false},
          "frames": [{"t": ms, "landmarks": [{x,y,z,visibility}...33], "worldLandmarks": [{x,y,z}...33] | null,
                      "face": {"blendshapes": {"jawOpen": 0.3, ...}} | null}]}
Pipeline: landmarks -> Stark World Engine space (+X left, +Y up, +Z toward the camera) -> 2D->3D lifting when world landmarks
are missing (bone-length priors, depth sign from the model's relative z / temporal continuity) -> One Euro
smoothing with visibility gating -> solver (two-vector bone frames vs the rig's rest frames, spine/neck distributed,
elbow/knee bend planes fix twist) -> hips height from foot contact -> Clip on the Stark World Engine rig -> cleanup.
Only landmark numbers are ever stored: no image or video frame is kept.
"""
import json, math, os

import numpy as np

from . import qmath as Q
from .cleanup import OneEuro
from .clip import Clip
from .rig import stark_rig

LM = ['nose', 'left_eye_inner', 'left_eye', 'left_eye_outer', 'right_eye_inner', 'right_eye', 'right_eye_outer',
      'left_ear', 'right_ear', 'mouth_left', 'mouth_right', 'left_shoulder', 'right_shoulder', 'left_elbow',
      'right_elbow', 'left_wrist', 'right_wrist', 'left_pinky', 'right_pinky', 'left_index', 'right_index',
      'left_thumb', 'right_thumb', 'left_hip', 'right_hip', 'left_knee', 'right_knee', 'left_ankle', 'right_ankle',
      'left_heel', 'right_heel', 'left_foot_index', 'right_foot_index']
I = {n: k for k, n in enumerate(LM)}
TAKE_FORMAT = 'stark-mocap-take'
TAKE_FORMATS = (TAKE_FORMAT, 'gorgon-mocap-take')   # + takes recorded before 28.7
# bones used for lifting: (parent landmark, child landmark, Stark World Engine joints whose rest distance gives the length)
BONES = [('left_hip', 'left_knee', ('thigh_l', 'calf_l')), ('left_knee', 'left_ankle', ('calf_l', 'foot_l')),
         ('right_hip', 'right_knee', ('thigh_r', 'calf_r')), ('right_knee', 'right_ankle', ('calf_r', 'foot_r')),
         ('left_shoulder', 'left_elbow', ('upperarm_l', 'lowerarm_l')), ('left_elbow', 'left_wrist', ('lowerarm_l', 'hand_l')),
         ('right_shoulder', 'right_elbow', ('upperarm_r', 'lowerarm_r')), ('right_elbow', 'right_wrist', ('lowerarm_r', 'hand_r')),
         ('left_ankle', 'left_foot_index', ('foot_l', 'ball_leaf_l')), ('right_ankle', 'right_foot_index', ('foot_r', 'ball_leaf_r'))]


# ---------------------------------------------------------------- landmark <-> Stark World Engine space
def world_to_stark(w):
    """MediaPipe world landmarks (x right in the image, y down, z away from the camera, metres, hips origin) ->
    Stark World Engine space for a person facing the camera: +X = person's left, +Y up, +Z toward the camera."""
    w = np.asarray(w, np.float64)
    return np.stack([w[..., 0], -w[..., 1], -w[..., 2]], axis=-1)


def stark_to_world(g):
    g = np.asarray(g, np.float64)
    return np.stack([g[..., 0], -g[..., 1], -g[..., 2]], axis=-1)


def _lm_array(frame, key):
    lms = frame.get(key)
    if not lms:
        return None
    if isinstance(lms[0], list):  # [[x,y,z,(v)]...]
        a = np.array([[p[0], p[1], p[2], p[3] if len(p) > 3 else 1.0] for p in lms], np.float64)
    else:
        a = np.array([[p['x'], p['y'], p.get('z', 0.0), p.get('visibility', 1.0)] for p in lms], np.float64)
    return a


# ---------------------------------------------------------------- 2D -> 3D lifting
def rig_bone_lengths(rig=None):
    rig = rig or stark_rig()
    _, gp = rig.rest_globals()
    out = {}
    for a, b, (ja, jb) in BONES:
        out[(a, b)] = float(np.linalg.norm(gp[rig.index(jb)] - gp[rig.index(ja)]))
    out['shoulders'] = float(np.linalg.norm(gp[rig.index('upperarm_l')] - gp[rig.index('upperarm_r')]))
    out['hips'] = float(np.linalg.norm(gp[rig.index('thigh_l')] - gp[rig.index('thigh_r')]))
    out['torso'] = float(np.linalg.norm((gp[rig.index('upperarm_l')] + gp[rig.index('upperarm_r')]) / 2 -
                                        (gp[rig.index('thigh_l')] + gp[rig.index('thigh_r')]) / 2))
    return out


def lift_2d(frames2d, aspect=4 / 3, rig=None):
    """Lift image landmarks [F, 33, 4] (x, y in 0..1, relative z, visibility) to metric engine-space points
    [F, 33, 3]: orthographic camera, scale from the torso, each bone's depth from its known length, depth sign from
    the model's relative z, flipped when that contradicts the previous frame by more than the alternative."""
    L = rig_bone_lengths(rig)
    F = len(frames2d)
    out = np.zeros((F, 33, 3))
    prev = None
    # metres per image unit: torso is the least foreshortened span; use a robust high percentile over the take
    torso_px = []
    for fr in frames2d:
        p = fr[:, :2] * np.array([aspect, 1.0])
        sh, hp = (p[I['left_shoulder']] + p[I['right_shoulder']]) / 2, (p[I['left_hip']] + p[I['right_hip']]) / 2
        torso_px.append(np.linalg.norm(sh - hp))
    s = L['torso'] / max(np.percentile(torso_px, 90), 1e-6)
    for f, fr in enumerate(frames2d):
        p = fr[:, :2] * np.array([aspect, 1.0]) * s
        P = np.zeros((33, 3))
        P[:, 0], P[:, 1] = p[:, 0], -p[:, 1]
        P[:, 2] = -fr[:, 2] * aspect * s  # relative depth hint (MediaPipe z: smaller = nearer)
        hips = (P[I['left_hip']] + P[I['right_hip']]) / 2
        P -= hips
        Z = P[:, 2].copy()
        for a, b, _ in BONES:
            ia, ib = I[a], I[b]
            ln = L[(a, b)]
            dxy = P[ib, :2] - P[ia, :2]
            dz2 = ln * ln - float(dxy @ dxy)
            dz = math.sqrt(dz2) if dz2 > 0 else 0.0
            sign = 1.0 if (Z[ib] - Z[ia]) >= 0 else -1.0
            if prev is not None and dz > 1e-4:
                cand = [P[ia, 2] + sign * dz, P[ia, 2] - sign * dz]
                pz = prev[ib, 2]
                if abs(cand[1] - pz) + 0.02 < abs(cand[0] - pz) and abs(Z[ib] - Z[ia]) < 0.3 * dz:
                    sign = -sign
            P[ib, 2] = P[ia, 2] + sign * dz
        # image x grows to the person's left when they face a non-mirrored camera: already +X = left
        out[f] = P
        prev = P
    return out


# ---------------------------------------------------------------- smoothing
def smooth_points_offline(P, vis, sigma=1.0, vis_min=0.5):
    """Zero-phase clean-up for recorded takes: hold low-visibility samples, 3-tap median (kills single-frame
    spikes), then a Gaussian (no lag, unlike the live One Euro filter)."""
    from .cleanup import _filt
    P = P.copy()
    for f in range(1, len(P)):
        bad = vis[f] < vis_min
        P[f, bad] = P[f - 1, bad]
    if len(P) >= 3:
        med = np.median(np.stack([np.concatenate([P[:1], P[:-1]]), P, np.concatenate([P[1:], P[-1:]])]), axis=0)
        P = med
    F = len(P)
    return _filt(P.reshape(F, -1), sigma).reshape(P.shape)


def smooth_points(P, vis, fps, min_cutoff=1.6, beta=0.6, vis_min=0.5):
    """One Euro per landmark; low-visibility samples hold the last good value (gap filling)."""
    F = len(P)
    out = np.zeros_like(P)
    filt = [OneEuro(min_cutoff, beta) for _ in range(P.shape[1])]
    last = P[0].copy()
    for f in range(F):
        for k in range(P.shape[1]):
            x = P[f, k] if vis[f, k] >= vis_min else last[k]
            out[f, k] = filt[k](x, 1.0 / fps)
            last[k] = out[f, k]
    return out


# ---------------------------------------------------------------- solver
def frame_vectors(P, bend_prev=None, straight=False):
    """Two-vector frames (primary, secondary) for each solved body part from 33 landmark positions (Stark World Engine space).
    The same function builds the rest references, so a rest pose solves to the rest rotations exactly."""
    L = lambda n: P[I[n]]
    mid = lambda a, b: (L(a) + L(b)) / 2
    hips, shoulders, ears = mid('left_hip', 'right_hip'), mid('left_shoulder', 'right_shoulder'), mid('left_ear', 'right_ear')
    lat_hips, lat_sh, lat_ears = L('left_hip') - L('right_hip'), L('left_shoulder') - L('right_shoulder'), L('left_ear') - L('right_ear')
    fwd_body = np.cross(lat_sh, shoulders - hips)
    V = {'pelvis': (shoulders - hips, lat_hips), 'chest': (ears - shoulders, lat_sh),
         'head': (np.cross(L('nose') - ears, lat_ears), lat_ears)}
    bend = {} if bend_prev is None else bend_prev

    def bend_normal(key, a, b, c, fallback):
        n = np.cross(b - a, c - b)
        if not straight and np.linalg.norm(n) > 0.15 * np.linalg.norm(b - a) * np.linalg.norm(c - b):
            n = n / np.linalg.norm(n)
            if key in bend and np.dot(n, bend[key]) < -0.5 and np.dot(n, fallback) < 0:
                n = -n  # elbows / knees do not bend backwards: keep the flexion side
            bend[key] = n
            return n
        f = fallback / max(np.linalg.norm(fallback), 1e-9)
        return bend.get(key, f) if bend_prev is not None else f

    for s, side in (('l', 'left'), ('r', 'right')):
        sh, el, wr = L(f'{side}_shoulder'), L(f'{side}_elbow'), L(f'{side}_wrist')
        na = bend_normal('arm_' + s, sh, el, wr, np.cross(el - sh, fwd_body))
        V[f'upperarm_{s}'] = (el - sh, na)
        V[f'lowerarm_{s}'] = (wr - el, na)
        V[f'hand_{s}'] = ((L(f'{side}_index') + L(f'{side}_pinky')) / 2 - wr, L(f'{side}_index') - L(f'{side}_pinky'))
        hp, kn, an = L(f'{side}_hip'), L(f'{side}_knee'), L(f'{side}_ankle')
        nl = bend_normal('leg_' + s, hp, kn, an, np.cross(kn - hp, -fwd_body))
        V[f'thigh_{s}'] = (kn - hp, nl)
        V[f'calf_{s}'] = (an - kn, nl)
        toe, heel = L(f'{side}_foot_index'), L(f'{side}_heel')
        lat = np.cross(toe - heel, an - heel)
        V[f'foot_{s}'] = (toe - an, np.cross(lat, toe - heel))
    return V


class Solver:
    """Landmarks in Stark World Engine space -> Stark World Engine rig rotations (world deltas from rest, two-vector frames per body part;
    spine and neck distributed between pelvis, chest and head; elbows/knees keep their bend plane)."""

    def __init__(self, rig=None):
        self.rig = rig or stark_rig()
        self.gr, self.gp = self.rig.rest_globals()
        self.rest = frame_vectors(landmarks_from_pose(self.rig, self.gr, self.gp))
        self.bend = {}
        self.calibrated = False

    CALIBRATE_PARTS = ('pelvis', 'thigh_', 'calf_', 'foot_')

    def calibrate(self, points, parts=CALIBRATE_PARTS):
        """points: [N, 33, 3] (or [33, 3]) landmarks of the performer holding the rig's rest pose (T-pose); their
        own body becomes 'rest' for `parts`. Measured on real Pose Landmarker output (fixtures/take_mediapipe_*):
        hips/legs/feet neutral to slightly better, arms 3-6 degrees WORSE (the model's depth for outstretched arms is
        unreliable) - so only the lower body is calibrated by default, and calibration is opt-in."""
        P = np.asarray(points, np.float64)
        if P.ndim == 3:
            P = np.median(P, axis=0)
        fv = frame_vectors(P, straight=True)  # a T-pose has straight limbs: no bend plane to trust
        for k, v in fv.items():
            if any(k.startswith(x) for x in parts):
                self.rest[k] = v
        self.calibrated = True

    def _fit_spine(self, W, P, iters=3):
        """Swing spine_01/02 until the hips -> shoulders chord of the solved spine matches the landmarks (the
        chest orientation itself stays as observed)."""
        r, gp = self.rig, self.gp
        idx = r.index
        o = lambda a, b: gp[idx(b)] - gp[idx(a)]
        mid_sh_rest = (gp[idx('upperarm_l')] + gp[idx('upperarm_r')]) / 2
        mid_hp_rest = (gp[idx('thigh_l')] + gp[idx('thigh_r')]) / 2
        segs = [('pelvis', None, mid_hp_rest, gp[idx('spine_01')]), ('spine_01', None, None, None),
                ('spine_02', None, None, None), ('spine_03', None, None, None)]
        want = (P[I['left_shoulder']] + P[I['right_shoulder']]) / 2 - (P[I['left_hip']] + P[I['right_hip']]) / 2
        for _ in range(iters):
            cur = (Q.q_rotate(W['pelvis'], gp[idx('spine_01')] - mid_hp_rest) +
                   Q.q_rotate(W['spine_01'], o('spine_01', 'spine_02')) +
                   Q.q_rotate(W['spine_02'], o('spine_02', 'spine_03')) +
                   Q.q_rotate(W['spine_03'], mid_sh_rest - gp[idx('spine_03')]))
            S = Q.q_between(cur, want)
            if Q.q_angle(S) < 1e-4:
                break
            W['spine_01'] = Q.q_mul(S, W['spine_01'])
            W['spine_02'] = Q.q_mul(S, W['spine_02'])

    def solve(self, P):
        """P: [33, 3] engine-space landmark positions -> (local rotations [J, 4], hips midpoint [3])."""
        r = self.rig
        cur = frame_vectors(P, self.bend)
        D = {k: Q.q_align_two(self.rest[k][0], self.rest[k][1], v[0], v[1]) for k, v in cur.items()}
        W = {'pelvis': D['pelvis'], 'spine_01': Q.q_slerp(D['pelvis'], D['chest'], 1 / 3),
             'spine_02': Q.q_slerp(D['pelvis'], D['chest'], 2 / 3), 'spine_03': D['chest'],
             'neck_01': Q.q_slerp(D['chest'], D['head'], 0.5), 'Head': D['head']}
        self._fit_spine(W, P)
        for s in 'lr':
            for k in ('upperarm', 'lowerarm', 'hand', 'thigh', 'calf', 'foot'):
                W[f'{k}_{s}'] = D[f'{k}_{s}']
            W[f'clavicle_{s}'] = Q.q_slerp(D['chest'], D[f'upperarm_{s}'], 0.15)
            W[f'ball_{s}'] = D[f'foot_{s}']
        J = len(r)
        G = np.empty((J, 4))
        for i in r.order:
            n = r.names[i]
            if n in W:
                G[i] = Q.q_mul(W[n], self.gr[i])
            elif r.parents[i] >= 0:
                G[i] = Q.q_mul(G[r.parents[i]], r.rest_r[i])  # fingers etc. follow their parent at rest
            else:
                G[i] = r.rest_r[i]
        lr = np.empty((J, 4))
        for i in range(J):
            p = r.parents[i]
            lr[i] = G[i] if p < 0 else Q.q_mul(Q.q_conj(G[p]), G[i])
        lr[r.roots[0]] = r.rest_r[r.roots[0]]
        hips = (P[I['left_hip']] + P[I['right_hip']]) / 2
        return lr, hips


def take_to_clip(take, rig=None, fps=30.0, smooth=True, ground=True, name=None, translate='image'):
    """Recorded / replayed take -> (Clip on the Stark World Engine rig, report). translate: 'image' (default) moves the hips
    sideways with the hips in the picture (a dance's hip sway, stepping left/right; depth from a single camera is
    too noisy and is not used); 'none' keeps the hips over the origin."""
    rig = rig or stark_rig()
    frames = take['frames']
    if not frames:
        raise ValueError('take has no frames')
    img = take.get('image') or {}
    aspect = float(img.get('width', 4)) / float(img.get('height', 3))
    ts = np.array([fr.get('t', k * 1000.0 / take.get('fps', 30)) for k, fr in enumerate(frames)], np.float64) / 1000.0
    ts -= ts[0]
    have_world = all(fr.get('worldLandmarks') for fr in frames)
    lm2d = [_lm_array(fr, 'landmarks') for fr in frames]
    vis = np.array([a[:, 3] if a is not None else np.zeros(33) for a in lm2d])
    if have_world:
        P = np.array([world_to_stark(_lm_array(fr, 'worldLandmarks')[:, :3]) for fr in frames])
        method = 'world-landmarks'
    else:
        if any(a is None for a in lm2d):
            raise ValueError('take frames need landmarks or worldLandmarks')
        P = lift_2d(np.array(lm2d), aspect, rig)
        method = 'lifted-2d'
    if take.get('image', {}).get('mirrored'):
        P = P * np.array([-1.0, 1, 1])
        P = P[:, [MIRROR[k] for k in range(33)]]
    src_fps = (len(ts) - 1) / max(ts[-1], 1e-6) if len(ts) > 1 else fps
    if smooth == 'live':
        P = smooth_points(P, vis, src_fps)
    elif smooth:
        P = smooth_points_offline(P, vis)
    sol = Solver(rig)
    calib = take.get('calibration') or []
    if calib:
        C = [world_to_stark(_lm_array(fr, 'worldLandmarks')[:, :3]) for fr in calib if fr.get('worldLandmarks')]
        if C:
            C = np.array(C)
            if take.get('image', {}).get('mirrored'):
                C = (C * np.array([-1.0, 1, 1]))[:, [MIRROR[k] for k in range(33)]]
            sol.calibrate(C)
    rots, hips = [], []
    for f in range(len(P)):
        lr, h = sol.solve(P[f])
        rots.append(lr)
        hips.append(h)
    rots = Q.q_continuity(np.array(rots), 0)
    hips = np.array(hips)
    pi = rig.index('pelvis')
    F = len(rots)
    pos = np.broadcast_to(rig.rest_t, (F, len(rig), 3)).copy()
    gr_rest, gp_rest = rig.rest_globals()
    root = rig.parents[pi]
    hip_w = np.zeros((F, 3))
    hip_w[:, 1] = gp_rest[pi][1]
    if translate == 'image' and lm2d[0] is not None:
        c = np.array([(a[I['left_hip'], :2] + a[I['right_hip'], :2]) / 2 for a in lm2d])
        k = rig_bone_lengths(rig)['torso'] / max(np.percentile([np.linalg.norm(
            ((a[I['left_shoulder'], :2] + a[I['right_shoulder'], :2]) / 2 - (a[I['left_hip'], :2] + a[I['right_hip'], :2]) / 2)
            * np.array([aspect, 1])) for a in lm2d], 90), 1e-6)
        from .cleanup import _filt
        hip_w[:, 0] = _filt(((c[:, 0] - c[0, 0]) * aspect * k)[:, None], 1.0)[:, 0]
        if take.get('image', {}).get('mirrored'):
            hip_w[:, 0] *= -1
    # pelvis local translation under the static root
    for f in range(F):
        pos[f, pi] = Q.q_rotate(Q.q_conj(gr_rest[root]), hip_w[f] - gp_rest[root]) if root >= 0 else hip_w[f]
    clip = Clip(name or take.get('name', 'mocap_take'), rig.names, rots, pos, fps=src_fps,
                meta={'source': 'mocap:' + take.get('source', 'webcam'), 'method': method, 'safety': 'neutral',
                      'ref_hip_height': float(gp_rest[pi][1]), 'ref_hips_rest': [float(x) for x in rig.rest_t[pi]]},
                rig=rig.meta.get('id'))
    if abs(src_fps - fps) > 0.5:
        clip = clip.resample(fps)
    if ground:
        clip = ground_feet(clip, rig)
    rep = {'frames': F, 'fps_in': round(src_fps, 3), 'method': method, 'mean_visibility': round(float(vis.mean()), 3),
           'calibrated': sol.calibrated}
    return clip, rep


MIRROR = [0, 4, 5, 6, 1, 2, 3, 8, 7, 10, 9, 12, 11, 14, 13, 16, 15, 18, 17, 20, 19, 22, 21, 24, 23, 26, 25, 28, 27,
          30, 29, 32, 31]


def ground_feet(clip, rig=None, smooth_frames=2.0):
    """Hips height so the lower foot rests on the ground (y=0 at the soles), lightly smoothed; keeps jumps whose
    both feet leave the ground only as far as the pose allows (monocular capture has no absolute height)."""
    rig = rig or stark_rig()
    from .cleanup import _full_pose, _store, _filt
    lr, lt = _full_pose(clip, rig)
    gr, gp = rig.fk(lr, lt)
    _, gp_rest = rig.rest_globals()
    feet = [rig.index(n) for n in ('foot_l', 'foot_r', 'ball_l', 'ball_r')]
    rest_low = min(gp_rest[j][1] for j in feet)
    low = gp[:, feet, 1].min(axis=1)
    dy = _filt((rest_low - low)[:, None], smooth_frames)[:, 0]
    pi = rig.index('pelvis')
    root = rig.parents[pi]
    gr_rest, _ = rig.rest_globals()
    for f in range(clip.frames):
        lt[f, pi] = lt[f, pi] + Q.q_rotate(Q.q_conj(gr_rest[root]), np.array([0, dy[f], 0])) if root >= 0 else \
            lt[f, pi] + np.array([0, dy[f], 0])
    return _store(clip, rig, lr, lt)


# ---------------------------------------------------------------- synthetic takes (fixtures with ground truth)
def landmarks_from_pose(rig, gr, gp):
    """33 BlazePose landmark positions (Stark World Engine space, metres) for one posed Stark World Engine rig frame."""
    g = lambda n: gp[rig.index(n)]
    head_i = rig.index('Head')
    rg, rp = rig.rest_globals()
    D = Q.q_mul(gr[head_i], Q.q_conj(rg[head_i]))  # head world delta from rest (rest faces +Z)
    hp = g('Head')
    def head(off):
        return hp + Q.q_rotate(D, np.asarray(off, np.float64))
    P = np.zeros((33, 3))
    P[I['nose']] = head([0, 0.07, 0.11])
    for side, sx in (('left', 1), ('right', -1)):
        P[I[f'{side}_eye_inner']] = head([sx * 0.015, 0.10, 0.095])
        P[I[f'{side}_eye']] = head([sx * 0.032, 0.10, 0.09])
        P[I[f'{side}_eye_outer']] = head([sx * 0.048, 0.10, 0.08])
        P[I[f'{side}_ear']] = head([sx * 0.075, 0.08, 0.0])
        P[I[f'mouth_{side}']] = head([sx * 0.025, 0.03, 0.10])
        s = 'l' if side == 'left' else 'r'
        P[I[f'{side}_shoulder']] = g(f'upperarm_{s}')
        P[I[f'{side}_elbow']] = g(f'lowerarm_{s}')
        P[I[f'{side}_wrist']] = g(f'hand_{s}')
        P[I[f'{side}_pinky']] = g(f'pinky_01_{s}')
        P[I[f'{side}_index']] = g(f'index_01_{s}')
        P[I[f'{side}_thumb']] = g(f'thumb_02_{s}')
        P[I[f'{side}_hip']] = g(f'thigh_{s}')
        P[I[f'{side}_knee']] = g(f'calf_{s}')
        P[I[f'{side}_ankle']] = g(f'foot_{s}')
        fd = Q.q_mul(gr[rig.index(f'foot_{s}')], Q.q_conj(rg[rig.index(f'foot_{s}')]))
        P[I[f'{side}_heel']] = g(f'foot_{s}') + Q.q_rotate(fd, np.array([0, -0.06, -0.05]))
        P[I[f'{side}_foot_index']] = g(f'ball_leaf_{s}')
    return P


def synth_take(clip, rig=None, fps=30.0, noise_mm=4.0, image_noise=0.002, cam_dist=3.2, size=(640, 480), seed=7,
               world=True, drop_visibility=0.03, name=None):
    """Render a clip into a MediaPipe-format take (what the Pose Landmarker would output for that motion seen by a
    camera in front of the character), with noise and the occasional low-visibility landmark."""
    rig = rig or stark_rig()
    from .cleanup import _full_pose
    c = clip.resample(fps) if abs(clip.fps - fps) > 1e-6 else clip
    lr, lt = _full_pose(c, rig)
    gr, gp = rig.fk(lr, lt)
    rng = np.random.default_rng(seed)
    w, h = size
    fpx = 0.9 * w  # focal length in pixels
    frames = []
    for f in range(c.frames):
        P = landmarks_from_pose(rig, gr[f], gp[f])
        hips = (P[I['left_hip']] + P[I['right_hip']]) / 2
        Pw = P + rng.normal(0, noise_mm / 1000.0, P.shape)
        wl = stark_to_world(Pw - hips)
        # camera at z = cam_dist looking toward -Z (the character faces it); image x = person's left
        cam = np.array([0.0, 1.0, cam_dist])
        rel = P - cam
        depth = -rel[:, 2]
        u = 0.5 + (rel[:, 0] * fpx / depth) / w
        v = 0.5 - (rel[:, 1] * fpx / depth) / h
        zrel = (depth - depth[[I['left_hip'], I['right_hip']]].mean()) * fpx / w
        vis = np.clip(rng.normal(0.97, 0.02, 33), 0, 1)
        vis[rng.random(33) < drop_visibility] = 0.2
        lms = [{'x': float(u[k] + rng.normal(0, image_noise)), 'y': float(v[k] + rng.normal(0, image_noise)),
                'z': float(zrel[k]), 'visibility': float(vis[k])} for k in range(33)]
        fr = {'t': round(f * 1000.0 / fps, 3), 'landmarks': lms}
        fr['worldLandmarks'] = [{'x': float(p[0]), 'y': float(p[1]), 'z': float(p[2])} for p in wl] if world else None
        frames.append(fr)
    return {'format': TAKE_FORMAT, 'version': 1, 'fps': fps, 'source': 'synthetic', 'name': name or c.name,
            'image': {'width': w, 'height': h, 'mirrored': False}, 'frames': frames,
            'ground_truth': {'clip': c.name, 'noise_mm': noise_mm}}


def compare_to_truth(clip, truth, rig=None):
    """Mean / 90th percentile angle (deg) between limb bone directions of a solved clip and the true clip."""
    rig = rig or stark_rig()
    from .cleanup import _full_pose
    a = clip.resample(truth.fps) if abs(clip.fps - truth.fps) > 1e-6 else clip
    n = min(a.frames, truth.frames)
    _, g1 = rig.fk(*[x[:n] for x in _full_pose(a, rig)])
    _, g2 = rig.fk(*[x[:n] for x in _full_pose(truth, rig)])
    pairs = [(x, y) for s in 'lr' for x, y in ((f'thigh_{s}', f'calf_{s}'), (f'calf_{s}', f'foot_{s}'),
                                                (f'upperarm_{s}', f'lowerarm_{s}'), (f'lowerarm_{s}', f'hand_{s}'))]
    pairs += [('pelvis', 'spine_03'), ('spine_03', 'Head')]
    errs = {}
    allv = []
    for x, y in pairs:
        d1 = g1[:, rig.index(y)] - g1[:, rig.index(x)]
        d2 = g2[:, rig.index(y)] - g2[:, rig.index(x)]
        d1 /= np.linalg.norm(d1, axis=1, keepdims=True)
        d2 /= np.linalg.norm(d2, axis=1, keepdims=True)
        e = np.degrees(np.arccos(np.clip(np.sum(d1 * d2, 1), -1, 1)))
        errs[f'{x}->{y}'] = round(float(e.mean()), 2)
        allv.append(e)
    allv = np.concatenate(allv)
    return {'mean_deg': round(float(allv.mean()), 2), 'p90_deg': round(float(np.percentile(allv, 90)), 2),
            'per_bone': errs}


def load_take(path):
    with open(path, encoding='utf-8') as f:
        t = json.load(f)
    if t.get('format') not in TAKE_FORMATS:
        raise ValueError('not a stark-mocap-take file')
    return t


def save_take(take, path):
    """Takes hold landmark numbers only - refuse anything that looks like image or video data."""
    s = json.dumps(take, separators=(',', ':'))
    if 'data:image' in s or 'data:video' in s:
        raise ValueError('a take must not contain image or video data')
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(s)
    return path
