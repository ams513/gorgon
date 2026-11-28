"""retarget.py - automatic retargeting of any humanoid motion onto the Stark World Engine humanoid rig (or any target rig).

Method (per mapped joint, all in world space):
  1. Normalise the source: up = feet -> head, left = right hip -> left hip, forward = left x up, so Z-up files,
     characters facing -X, centimetres or inches all land in the target's metres / +Y up / +Z forward frame.
  2. Reference pose: the source rest pose (BVH zero pose / glTF bind pose); when that does not look like a
     standing T/A-pose (e.g. a BVH whose zero pose is garbage) the most T-pose-like frame is used instead.
  3. Pose alignment: for every mapped joint a rotation C aligns the source's bone frame (direction to the next
     mapped joint + a lateral/up/forward reference) to the target's rest bone frame. This is what makes T-pose ->
     A-pose (and any other rest-pose difference) transfer correctly.
  4. Every frame: target global = source global * inv(source ref global) * inv(C) * target rest global, i.e. the
     source bone's world-space motion away from the aligned pose, applied to the target bone.
     Target joints between two mapped joints (e.g. a 3rd spine bone fed by a 2-bone spine) blend the two deltas;
     other unmapped joints (fingers when the source has none) keep their rest pose.
  5. Hips: positions scaled by the leg-length ratio; the source floor is estimated from foot contacts so feet that
     touch the ground in the capture touch the ground on the target.
  6. Root motion: 'extract' (default) moves the trajectory + heading into clip.motion and leaves the body in place
     facing +Z at frame 0; 'in_place' drops it; 'keep' leaves the capture's world motion in the body tracks.
Both sides are described by canonical (Stark World Engine) joint maps, so the same code retargets Stark World Engine -> UE5 Mannequin,
CMU -> Stark World Engine, Mixamo -> Stark World Engine, and so on.
"""
import os

import numpy as np

from . import bonemap
from . import qmath as Q
from .clip import Clip
from .rig import stark_rig, classify_rest_pose, RIG_ID

PRIMARY_CHILD = {  # canonical joint -> ordered candidates for the joint its primary axis points at
    'pelvis': ['spine_01', 'spine_02', 'spine_03', 'neck_01', 'Head'],
    'spine_01': ['spine_02', 'spine_03', 'neck_01', 'Head'], 'spine_02': ['spine_03', 'neck_01', 'Head'],
    'spine_03': ['neck_01', 'Head'], 'neck_01': ['Head'],
    'clavicle_{s}': ['upperarm_{s}'], 'upperarm_{s}': ['lowerarm_{s}'], 'lowerarm_{s}': ['hand_{s}'],
    'hand_{s}': ['middle_01_{s}', 'index_01_{s}', 'ring_01_{s}'],
    'thigh_{s}': ['calf_{s}'], 'calf_{s}': ['foot_{s}'], 'foot_{s}': ['ball_{s}'],
}
for _f in ('thumb', 'index', 'middle', 'ring', 'pinky'):
    PRIMARY_CHILD[f'{_f}_01_{{s}}'] = [f'{_f}_02_{{s}}']
    PRIMARY_CHILD[f'{_f}_02_{{s}}'] = [f'{_f}_03_{{s}}']


def _expand(d):
    out = {}
    for k, v in d.items():
        if '{s}' in k:
            for s in 'lr':
                out[k.format(s=s)] = [x.format(s=s) for x in v]
        else:
            out[k] = list(v)
    return out


PRIMARY_CHILD = _expand(PRIMARY_CHILD)
# when the primary target is missing, continue the parent's bone direction (leaf bones)
CONTINUE_FROM = {'Head': 'neck_01', 'hand_l': 'lowerarm_l', 'hand_r': 'lowerarm_r', 'ball_l': 'foot_l',
                 'ball_r': 'foot_r', 'foot_l': 'calf_l', 'foot_r': 'calf_r'}
for _s in 'lr':
    for _f in ('thumb', 'index', 'middle', 'ring', 'pinky'):
        CONTINUE_FROM[f'{_f}_03_{_s}'] = f'{_f}_02_{_s}'


class RetargetError(ValueError):
    pass


def _secondary(canon, P, up, fwd, left):
    """World reference vector for the joint's second alignment axis (computed from canonical positions P)."""
    def diff(a, b):
        return P[a] - P[b] if a in P and b in P else None
    if canon == 'pelvis' or canon.startswith('spine'):
        pairs = (('thigh_l', 'thigh_r'), ('upperarm_l', 'upperarm_r'), ('clavicle_l', 'clavicle_r'))
        if canon == 'spine_03':
            pairs = pairs[1:] + pairs[:1]
        for a, b in pairs:
            v = diff(a, b)
            if v is not None:
                return v
        return left
    if canon in ('neck_01', 'Head'):
        for a, b in (('upperarm_l', 'upperarm_r'), ('clavicle_l', 'clavicle_r'), ('thigh_l', 'thigh_r')):
            v = diff(a, b)
            if v is not None:
                return v
        return left
    if canon.startswith(('thigh', 'calf')):
        return fwd
    if canon.startswith('hand'):
        s = canon[-1]
        v = diff(f'index_01_{s}', f'pinky_01_{s}')
        return v if v is not None else up
    return up


def _frame_vectors(canon, P, up, fwd, left):
    prim = None
    for c in PRIMARY_CHILD.get(canon, []):
        if c in P and canon in P and np.linalg.norm(P[c] - P[canon]) > 1e-6:
            prim = P[c] - P[canon]
            break
    if prim is None and canon in CONTINUE_FROM and CONTINUE_FROM[canon] in P and canon in P:
        prim = P[canon] - P[CONTINUE_FROM[canon]]
    if prim is None or np.linalg.norm(prim) < 1e-9:
        return None
    return prim, _secondary(canon, P, up, fwd, left)


def tpose_score(P, up):
    """Lower is better: how far a pose (canonical positions) is from a standing T/A-pose, in degrees."""
    def ang(v, w):
        v = v / max(np.linalg.norm(v), 1e-9)
        w = w / max(np.linalg.norm(w), 1e-9)
        return np.degrees(np.arccos(np.clip(np.dot(v, w), -1, 1)))
    s, n = 0.0, 0
    for side in 'lr':
        if f'thigh_{side}' in P and f'foot_{side}' in P:
            s += ang(P[f'foot_{side}'] - P[f'thigh_{side}'], -up)
            n += 1
        if f'upperarm_{side}' in P and f'hand_{side}' in P:
            a = P[f'hand_{side}'] - P[f'upperarm_{side}']
            s += max(0.0, ang(a, a - up * np.dot(a, up)) - 50)  # up to 50 deg below horizontal is fine
            n += 1
    if 'pelvis' in P and 'Head' in P:
        s += ang(P['Head'] - P['pelvis'], up)
        n += 1
    return s / max(n, 1)


def _basis(P):
    """-> (q, up, fwd, left): q rotates the skeleton's own (left, up, forward) onto (+X, +Y, +Z)."""
    ankles = [P[k] for k in ('foot_l', 'foot_r') if k in P] or [P[k] for k in ('ball_l', 'ball_r') if k in P]
    hips = [P[k] for k in ('thigh_l', 'thigh_r') if k in P] or [P[k] for k in ('pelvis',) if k in P]
    top = P.get('Head', P.get('neck_01', P.get('spine_03')))
    if not ankles or not hips:
        raise RetargetError('cannot find hips and feet to orient the skeleton')
    # the leg axis (hips -> ankles) is the most posture-independent vertical; the head only breaks ties
    up = np.mean(hips, axis=0) - np.mean(ankles, axis=0)
    if np.linalg.norm(up) < 1e-9 and top is not None:
        up = top - np.mean(ankles, axis=0)
    up = up / max(np.linalg.norm(up), 1e-9)
    if top is not None and np.dot(top - np.mean(ankles, axis=0), up) < 0:
        up = -up
    # file conventions are axis-aligned (Y-up, Z-up, ...): snap, so a leaning rest pose cannot tilt the ground
    # plane of the whole motion (small posture differences are handled per bone by the alignment step)
    ax = int(np.argmax(np.abs(up)))
    if abs(up[ax]) > 0.866:
        up = np.zeros(3)
        up[ax] = 1.0 if (top is None or (top - np.mean(ankles, axis=0))[ax] >= 0) else -1.0
    left = None
    for a, b in (('thigh_l', 'thigh_r'), ('upperarm_l', 'upperarm_r'), ('foot_l', 'foot_r'), ('hand_l', 'hand_r')):
        if a in P and b in P:
            left = P[a] - P[b]
            break
    if left is None:
        raise RetargetError('cannot tell left from right (no paired hips/arms/feet mapped)')
    left = left - up * np.dot(left, up)
    left = left / max(np.linalg.norm(left), 1e-9)
    fwd = np.cross(left, up)
    S = np.stack([left, up, fwd], axis=1)  # columns = the skeleton's axes in its own coordinates
    return Q.q_from_matrix(S.T), up, fwd, left


def _canon_map(skel, mapping):
    if mapping is None:
        am = bonemap.auto_map(skel)
    else:
        am = {'map': dict(mapping), 'method': 'given', 'profile': None, 'problems': []}
    am['map'] = {c: n for c, n in am['map'].items() if skel.has(n)}
    return am


def _gauss(x, sigma):
    r = int(max(1, round(3 * sigma)))
    t = np.arange(-r, r + 1)
    w = np.exp(-0.5 * (t / sigma) ** 2)
    w /= w.sum()
    xp = np.concatenate([np.full(r, x[0]), x, np.full(r, x[-1])])
    return np.convolve(xp, w, mode='valid')


def retarget(src_skel, src_clip, target=None, mapping=None, target_mapping=None, ref_pose='auto', floor='auto',
             scale='auto', root_motion='extract', fps=None, fingers=True, name=None, motion_smooth=0.25):
    """Retarget src_clip (played on src_skel) onto `target` (default: the Stark World Engine humanoid rig).
    mapping / target_mapping: {canonical Stark World Engine joint: joint name} per side (auto-detected when None; the Stark World Engine
    rig maps to itself). Returns (Clip on the target's joints, report dict)."""
    tgt = target or stark_rig()
    if fps and abs(fps - src_clip.fps) > 1e-6:
        src_clip = src_clip.resample(fps)
    am = _canon_map(src_skel, mapping)
    if target_mapping is None and tgt.meta.get('id') == RIG_ID:
        tmap_all = {n: n for n in tgt.names}
    else:
        tmap_all = _canon_map(tgt, target_mapping)['map']
    common = [c for c in am['map'] if c in tmap_all]
    if not fingers:
        common = [c for c in common if c not in bonemap.FINGERS]
    smap = {c: am['map'][c] for c in common}
    tmap = {c: tmap_all[c] for c in common}
    missing = [c for c in ('pelvis', 'thigh_l', 'thigh_r') if c not in smap]
    if missing:
        raise RetargetError(f'skeletons share no {missing}; source mapped: {sorted(am["map"])}')
    F = src_clip.frames
    # ---- source FK (clip joints -> skeleton order)
    lr = np.broadcast_to(src_skel.rest_r, (F, len(src_skel), 4)).copy()
    lt = np.broadcast_to(src_skel.rest_t, (F, len(src_skel), 3)).copy()
    for k, n in enumerate(src_clip.joints):
        i = src_skel.index(n)
        if i is not None:
            lr[:, i] = src_clip.rot[:, k]
            lt[:, i] = src_clip.pos[:, k]
    sgr, sgp = src_skel.fk(lr, lt)
    rgr, rgp = src_skel.rest_globals()
    si = {c: src_skel.index(n) for c, n in smap.items()}
    ti_of = {c: tgt.index(n) for c, n in tmap.items()}
    canon_at = {i: c for c, i in ti_of.items()}
    # ---- reference pose
    P_rest = {c: rgp[i] for c, i in si.items()}
    try:
        _, up0, _, _ = _basis(P_rest)
        rest_score = tpose_score(P_rest, up0)
    except RetargetError:
        rest_score = 1e9
    ref_used = 'rest'
    explicit = isinstance(ref_pose, int) and not isinstance(ref_pose, bool)
    if explicit or ref_pose == 'first' or (ref_pose == 'auto' and rest_score > 25):
        fr = None
        if ref_pose == 'auto':
            best = rest_score
            for f in range(0, F, max(1, F // 200)):
                Pf = {c: sgp[f, i] for c, i in si.items()}
                try:
                    _, upf, _, _ = _basis(Pf)
                except RetargetError:
                    continue
                sc = tpose_score(Pf, upf)
                if sc < best:
                    best, fr = sc, f
        else:
            fr = 0 if ref_pose == 'first' else int(ref_pose)
        if fr is not None:
            rgr, rgp = sgr[fr].copy(), sgp[fr].copy()
            ref_used = f'frame {fr}'
    P_ref = {c: rgp[i] for c, i in si.items()}
    B, _, _, _ = _basis(P_ref)
    sgr = Q.q_mul(B, sgr)
    sgp = Q.q_rotate(B, sgp)
    rgr = Q.q_mul(B, rgr)
    rgp = Q.q_rotate(B, rgp)
    P_src = {c: rgp[i] for c, i in si.items()}
    tgr, tgp = tgt.rest_globals()
    P_tgt = {c: tgp[i] for c, i in ti_of.items()}
    up, fwd, left = np.array([0, 1.0, 0]), np.array([0, 0, 1.0]), np.array([1.0, 0, 0])
    # the target need not be +Y up / +Z forward either: Bt takes its own axes to the normalised frame
    Bt, up_t, _, _ = _basis(P_tgt)
    Bt_inv = Q.q_conj(Bt)
    P_tn = {c: Q.q_rotate(Bt, v) for c, v in P_tgt.items()}

    # ---- scale + floor (in normalised axes)
    def leg_len(P, s):
        if all(k in P for k in (f'thigh_{s}', f'calf_{s}', f'foot_{s}')):
            return np.linalg.norm(P[f'calf_{s}'] - P[f'thigh_{s}']) + np.linalg.norm(P[f'foot_{s}'] - P[f'calf_{s}'])
        return None
    ls = [x for x in (leg_len(P_src, 'l'), leg_len(P_src, 'r')) if x]
    lt_ = [x for x in (leg_len(P_tn, 'l'), leg_len(P_tn, 'r')) if x]
    if scale == 'auto':
        k = float(np.mean(lt_) / np.mean(ls)) if ls and lt_ else 1.0
    else:
        k = float(scale)
    # vertical hips motion scales with "hips above the feet" (thigh offsets and foot heights differ between rigs)
    fk_s = [c for c in ('foot_l', 'foot_r', 'ball_l', 'ball_r') if c in P_src and c in P_tn]
    k_v = k
    if scale == 'auto' and fk_s and 'pelvis' in P_src:
        hs = P_src['pelvis'][1] - min(P_src[c][1] for c in fk_s)
        ht = P_tn['pelvis'][1] - min(P_tn[c][1] for c in fk_s)
        if hs > 1e-9 and ht > 1e-9:
            k_v = float(ht / hs)
    foot_keys = [c for c in ('foot_l', 'foot_r', 'ball_l', 'ball_r') if c in si]
    # source floor: the capture's own ground plane (y = 0) unless told otherwise; 'auto' then pins the target's
    # feet to the ground at detected contact frames (target-space, so rig proportions below the hips do not matter)
    if isinstance(floor, (int, float)) and not isinstance(floor, bool):
        g = float(floor)
    elif floor == 'rest' and foot_keys:
        t_low = min(float(P_tn[c][1]) for c in foot_keys)
        g = float(min(rgp[si[c]][1] for c in foot_keys)) - t_low / k
    else:
        g = 0.0
    # ---- per-joint alignment: target global = Bt^-1 * source global * K
    order = [canon_at[i] for i in tgt.order if i in canon_at]
    K, C_of, aligned = {}, {}, {}
    for c in order:
        ti = ti_of[c]
        fv_s = _frame_vectors(c, P_src, up, fwd, left)
        fv_t = _frame_vectors(c, P_tn, up, fwd, left)
        if fv_s is not None and fv_t is not None:
            C = Q.q_align_two(fv_s[0], fv_s[1], fv_t[0], fv_t[1])
            aligned[c] = True
        else:
            p, C = tgt.parents[ti], None
            while p >= 0 and C is None:
                C = C_of.get(canon_at.get(p))
                p = tgt.parents[p]
            C = Q.quat_identity() if C is None else C
            aligned[c] = False
        C_of[c] = C
        K[c] = Q.q_mul(Q.q_mul(Q.q_mul(Q.q_conj(rgr[si[c]]), Q.q_conj(C)), Bt), tgr[ti])
    J = len(tgt)
    G = np.empty((F, J, 4))
    have = np.zeros(J, bool)
    for c in order:
        ti = ti_of[c]
        G[:, ti] = Q.q_mul(Bt_inv[None], Q.q_mul(sgr[:, si[c]], K[c][None]))
        have[ti] = True
    # a target bone spanning several source bones (e.g. 1-bone spine fed by a 3-bone spine) aims at the source chord
    chord_fixed = []
    for c in order:
        ti = ti_of[c]
        cc = next((p for p in PRIMARY_CHILD.get(c, []) if p in ti_of and p in si), None)
        if cc is None or tgt.parents[ti_of[cc]] != ti or src_skel.parents[si[cc]] == si[c]:
            continue
        if not src_skel.chain(si[c], si[cc]):
            continue
        rest_dir = tgp[ti_of[cc]] - tgp[ti]
        if np.linalg.norm(rest_dir) < 1e-6:
            continue
        cur = Q.q_rotate(Q.q_mul(G[:, ti], Q.q_conj(tgr[ti])[None]), np.broadcast_to(rest_dir, (F, 3)))
        want = Q.q_rotate(Bt_inv[None], sgp[:, si[cc]] - sgp[:, si[c]])
        G[:, ti] = Q.q_mul(Q.q_between(cur, want), G[:, ti])
        chord_fixed.append(c)
    blended = []
    for ti in tgt.order:
        if have[ti]:
            continue
        a = tgt.parents[ti]
        while a >= 0 and not have[a]:
            a = tgt.parents[a]
        d = ti
        while len(tgt.children[d]) == 1 and not have[d]:
            d = tgt.children[d][0]
        p = tgt.parents[ti]
        if a >= 0 and have[d] and d != ti:
            seg = tgt.chain(a, d)
            lens = np.array([np.linalg.norm(tgp[seg[q + 1]] - tgp[seg[q]]) for q in range(len(seg) - 1)])
            w = lens[:seg.index(ti)].sum() / max(lens.sum(), 1e-9)
            Da = Q.q_mul(G[:, a], Q.q_conj(tgr[a])[None])
            Db = Q.q_mul(G[:, d], Q.q_conj(tgr[d])[None])
            G[:, ti] = Q.q_mul(Q.q_slerp(Da, Db, np.full(F, w)), tgr[ti][None])
            blended.append(ti)
        elif p >= 0:
            G[:, ti] = Q.q_mul(G[:, p], tgt.rest_r[ti][None])
        else:
            G[:, ti] = tgr[ti]
        have[ti] = True
    # ---- quality: source vs target bone directions (before any root-motion change)
    err = _direction_error(tgt, G, tgr, tgp, Q.q_rotate(Bt_inv[None], sgp), si, ti_of, src_skel)
    # ---- hips translation (normalised axes)
    pi = ti_of['pelvis']
    hip = sgp[:, si['pelvis']]
    hip_n = np.stack([hip[:, 0] * k, (hip[:, 1] - g) * k_v, hip[:, 2] * k], axis=1)
    floor_fix, contacts = 0.0, 0
    if floor == 'auto':
        floor_fix, contacts = _contact_floor(tgt, G, Q.q_rotate(Bt_inv[None], hip_n), pi, tgr, tgp, Bt, ti_of,
                                             src_clip.fps)
        hip_n[:, 1] += floor_fix
    # ---- root motion: body into the root's moving frame, trajectory + heading into clip.motion
    motion = None
    if root_motion in ('extract', 'in_place'):
        Gn = Q.q_mul(Bt[None, None], G)
        pel_delta = Q.q_mul(Gn[:, pi], Q.q_conj(Q.q_mul(Bt, tgr[pi]))[None])
        fa = Q.q_rotate(pel_delta, np.broadcast_to(fwd, (F, 3)))
        yaw = np.unwrap(np.arctan2(fa[:, 0], fa[:, 2]))
        traj = hip_n.copy()
        traj[:, 1] = 0.0
        sig = motion_smooth * src_clip.fps
        if sig >= 1 and F > 3:
            traj[:, 0] = _gauss(traj[:, 0], sig)
            traj[:, 2] = _gauss(traj[:, 2], sig)
            yaw = _gauss(yaw, sig)
        qinv = Q.q_from_yaw(-yaw)
        G = Q.q_mul(Bt_inv[None, None], Q.q_mul(qinv[:, None], Gn))
        hip_n = Q.q_rotate(qinv, hip_n - traj)
        if root_motion == 'extract':
            q0 = Q.q_from_yaw(np.full(F, -yaw[0]))
            motion = np.concatenate([Q.q_rotate(q0, traj - traj[:1]), (yaw - yaw[0])[:, None]], axis=1)
    elif root_motion != 'keep':
        raise RetargetError("root_motion must be 'extract', 'in_place' or 'keep'")
    LR, LT = _locals(tgt, G, Q.q_rotate(Bt_inv[None], hip_n), pi, tgr, tgp)
    LR = Q.q_continuity(LR, axis=0)
    out = Clip(name or src_clip.name, tgt.names, LR, LT, fps=src_clip.fps, loop=src_clip.loop, motion=motion,
               events=[dict(e) for e in src_clip.events], tags=list(src_clip.tags),
               meta=dict(src_clip.meta), rig=tgt.meta.get('id'))
    rest_pose = 'unknown'
    if 'upperarm_l' in P_src and 'lowerarm_l' in P_src:
        d = P_src['lowerarm_l'] - P_src['upperarm_l']
        ang = np.degrees(np.arcsin(np.clip(-d[1] / max(np.linalg.norm(d), 1e-9), -1, 1)))
        rest_pose = classify_rest_pose(ang)
    report = {'profile': am.get('profile'), 'method': am.get('method'), 'mapped': len(smap),
              'missing_core': [c for c in bonemap.CORE if c not in smap], 'problems': am.get('problems'),
              'scale': round(k, 6), 'scale_vertical': round(k_v, 6), 'floor': round(g, 6), 'floor_fix': round(float(floor_fix), 4),
              'contact_frames': int(contacts), 'reference_pose': ref_used, 'source_rest_pose': rest_pose,
              'aligned_joints': int(sum(aligned.values())), 'blended_joints': [tgt.names[i] for i in blended],
              'chord_aimed': chord_fixed,
              'root_motion': root_motion, 'direction_error_deg': err, 'frames': F, 'fps': src_clip.fps,
              'map': smap, 'target_map': tmap}
    out.meta.update({'retarget': {k2: v for k2, v in report.items() if k2 not in ('map', 'target_map')},
                     'ref_hip_height': float(np.dot(tgp[pi], up_t)), 'ref_hips_rest': [round(float(x), 6) for x in
                                                                                     tgt.rest_t[pi]],
                     'source_map_profile': am.get('profile')})
    return out, report


def _locals(tgt, G, hip_w, pi, tgr, tgp):
    """Target world rotations G [F, J, 4] + hips world position -> local rotations / translations.
    Roots other than the hips stay at rest; the hips translation is expressed under its (static) parent."""
    F, J = G.shape[:2]
    G = G.copy()
    for r in tgt.roots:
        if r != pi:
            G[:, r] = tgr[r]
    LR = np.empty((F, J, 4))
    LT = np.broadcast_to(tgt.rest_t, (F, J, 3)).copy()
    for ti in range(J):
        p = tgt.parents[ti]
        LR[:, ti] = G[:, ti] if p < 0 else Q.q_mul(Q.q_conj(G[:, p]), G[:, ti])
    pp = tgt.parents[pi]
    LT[:, pi] = Q.q_rotate(Q.q_conj(tgr[pp])[None], hip_w - tgp[pp][None]) if pp >= 0 else hip_w
    return LR, LT


def _contact_floor(tgt, G, hip_w, pi, tgr, tgp, Bt, ti_of, fps):
    """Vertical offset that puts the target's lowest foot joint on its rest height at stance frames.
    -> (offset, number of contact frames); (0, 0) when the clip never plants a foot (jumps, swimming)."""
    feet = [(ti_of[a], ti_of.get(b)) for a, b in (('foot_l', 'ball_l'), ('foot_r', 'ball_r')) if a in ti_of]
    if not feet:
        return 0.0, 0
    LR, LT = _locals(tgt, G, hip_w, pi, tgr, tgp)
    _, gp = tgt.fk(LR, LT)
    gp = Q.q_rotate(Bt[None, None], gp)
    rest_n = Q.q_rotate(Bt[None], tgp)
    hs, rest_low = [], []
    for a, b in feet:
        js = [a] + ([b] if b is not None else [])
        hs.append(np.min(gp[:, js, 1], axis=1))
        lowj = js[int(np.argmin(rest_n[js, 1]))]
        rest_low.append(rest_n[lowj, 1])
        # speed of the foot's lowest joint, horizontal + vertical
    vals = []
    for (a, b), h, rl in zip(feet, hs, rest_low):
        js = [a] + ([b] if b is not None else [])
        low = gp[np.arange(len(h)), np.array(js)[np.argmin(gp[:, js, 1], axis=1)]]
        v = np.zeros(len(h))
        if len(h) > 1:
            v[1:] = np.linalg.norm(np.diff(low, axis=0), axis=1) * fps
            v[0] = v[1]
        thr_h = np.percentile(h, 15) + 0.03
        stance = (v < max(0.35, 0.06 * float(v.max()))) & (h <= thr_h)
        if stance.sum() >= max(2, 0.05 * len(h)):
            vals.append(rl - np.percentile(h[stance], 20))
    if not vals:
        return 0.0, 0
    return float(np.mean(vals)), int(sum(1 for _ in vals))


def _direction_error(tgt, G, tgr, tgp, sgp, si, ti_of, src_skel):
    """Mean/max angle (degrees) between each mapped limb bone on the source and on the retargeted target."""
    errs = []
    canon_at = {ti: c for c, ti in ti_of.items()}
    for c, ti in ti_of.items():
        if c not in si or c == 'pelvis':
            continue
        prim = next((p for p in PRIMARY_CHILD.get(c, []) if p in ti_of and p in si), None)
        for ch in tgt.children[ti]:
            cc = canon_at.get(ch)
            if cc is None or cc != prim or not src_skel.chain(si[c], si[cc]):
                continue
            rest_dir = tgp[ch] - tgp[ti]
            if np.linalg.norm(rest_dir) < 1e-6:
                continue
            dt = Q.q_rotate(Q.q_mul(G[:, ti], Q.q_conj(tgr[ti])[None]),
                            np.broadcast_to(rest_dir, G.shape[:1] + (3,)))
            ds = sgp[:, si[cc]] - sgp[:, si[c]]
            if np.linalg.norm(ds, axis=1).min() < 1e-6:
                continue  # zero-length connector bone on the source (CMU Neck, LHipJoint...): no direction to compare
            dt = dt / np.maximum(np.linalg.norm(dt, axis=1, keepdims=True), 1e-9)
            ds = ds / np.maximum(np.linalg.norm(ds, axis=1, keepdims=True), 1e-9)
            errs.append(np.degrees(np.arccos(np.clip(np.sum(ds * dt, axis=1), -1, 1))))
    if not errs:
        return None
    e = np.concatenate(errs)
    return {'mean': round(float(e.mean()), 3), 'max': round(float(e.max()), 3), 'bones': len(errs)}


# ---------------------------------------------------------------- file-level helpers
def load_source(path, fps=30.0):
    """-> (Skeleton, [Clip], info) for .bvh / .gltf / .glb / .fbx (FBX through an installed converter only)."""
    ext = os.path.splitext(path)[1].lower()
    if ext == '.bvh':
        from .bvh import load_bvh
        s, c, info = load_bvh(path)
        return s, [c], info
    if ext in ('.gltf', '.glb'):
        from .gltf_io import load_gltf_clips
        s, cs = load_gltf_clips(path, fps=fps)
        return s, cs, {'clips': len(cs)}
    if ext == '.fbx':
        from .fbx import fbx_to_glb
        glb, conv = fbx_to_glb(path)
        from .gltf_io import load_gltf_clips
        s, cs = load_gltf_clips(glb, fps=fps)
        return s, cs, {'clips': len(cs), 'converter': conv}
    raise RetargetError(f'unsupported motion file type: {ext} (use .bvh, .gltf, .glb or .fbx)')


def import_motion(path, fps=30.0, root_motion='extract', only=None, **kw):
    """Load any supported motion file and retarget every clip in it to the Stark World Engine rig -> [(Clip, report)]."""
    skel, clips, info = load_source(path, fps=fps)
    out = []
    for c in clips:
        if only and c.name not in only:
            continue
        rc, rep = retarget(skel, c, root_motion=root_motion, fps=fps, **kw)
        rep['source_info'] = info
        out.append((rc, rep))
    return out
