"""cleanup.py - mocap clean-up for Stark World Engine clips: jitter smoothing, foot-contact detection + locking (two-bone IK),
trim, seamless loop, mirror, speed, and automatic events (footsteps from contacts, hit frame from limb speed).

All functions take a Clip (and its Skeleton, default: the Stark World Engine rig) and return a new Clip; inputs are not changed.
Clips with extracted root motion are handled in world space where it matters (contacts, locking) and stored back
in root space.
"""
import numpy as np

from . import qmath as Q
from .clip import Clip
from .rig import stark_rig, mirror_name

FEET = {'l': ('thigh_l', 'calf_l', 'foot_l', 'ball_l'), 'r': ('thigh_r', 'calf_r', 'foot_r', 'ball_r')}
HANDS = {'l': ('upperarm_l', 'lowerarm_l', 'hand_l'), 'r': ('upperarm_r', 'lowerarm_r', 'hand_r')}


def _skel(skel):
    return skel or stark_rig()


def _full_pose(clip, skel):
    """Clip -> local rot/pos arrays in skeleton joint order (missing joints at rest)."""
    F = clip.frames
    lr = np.broadcast_to(skel.rest_r, (F, len(skel), 4)).copy()
    lt = np.broadcast_to(skel.rest_t, (F, len(skel), 3)).copy()
    for k, n in enumerate(clip.joints):
        i = skel.index(n)
        if i is not None:
            lr[:, i] = clip.rot[:, k]
            lt[:, i] = clip.pos[:, k]
    return lr, lt


def _store(clip, skel, lr, lt, **over):
    rot = clip.rot.copy()
    pos = clip.pos.copy()
    for k, n in enumerate(clip.joints):
        i = skel.index(n)
        if i is not None:
            rot[:, k] = lr[:, i]
            pos[:, k] = lt[:, i]
    return clip.copy(rot=Q.q_continuity(rot, 0), pos=pos, **over)


def world_pose(clip, skel=None):
    """Global rotations/positions in WORLD space (root motion applied when the clip has it)."""
    skel = _skel(skel)
    lr, lt = _full_pose(clip, skel)
    if clip.motion is None:
        return skel.fk(lr, lt)
    m = clip.motion
    return skel.fk(lr, lt, root_r=Q.q_from_yaw(m[:, 3]), root_t=m[:, :3])


# ---------------------------------------------------------------- smoothing
def _gauss_kernel(sigma):
    r = int(max(1, round(3 * sigma)))
    t = np.arange(-r, r + 1)
    w = np.exp(-0.5 * (t / sigma) ** 2)
    return w / w.sum(), r


def _filt(x, sigma, loop=False):
    """Zero-phase Gaussian filter along axis 0 (edges clamped, or wrapped for loops)."""
    if sigma <= 0 or x.shape[0] < 3:
        return x.copy()
    w, r = _gauss_kernel(sigma)
    if loop:
        body = x[:-1]
        xp = np.concatenate([body[-r:], body, body[:r]], axis=0)
    else:
        xp = np.concatenate([np.repeat(x[:1], r, 0), x, np.repeat(x[-1:], r, 0)], axis=0)
    out = np.zeros_like(xp[r:-r] if r else xp)
    for k, wk in enumerate(w):
        out += wk * xp[k:k + out.shape[0]]
    if loop:
        out = np.concatenate([out, out[:1]], axis=0)
    return out


def smooth(clip, sigma_frames=1.5, positions=True, joints=None):
    """Remove jitter: zero-phase Gaussian smoothing of rotations (hemisphere-consistent) and translations."""
    q = Q.q_continuity(clip.rot, 0)
    sel = list(range(len(clip.joints))) if joints is None else [clip.jindex(j) for j in joints if clip.jindex(j) is not None]
    rot = q.copy()
    rot[:, sel] = Q.q_normalize(_filt(q[:, sel], sigma_frames, clip.loop))
    pos = clip.pos.copy()
    if positions:
        pos[:, sel] = _filt(clip.pos[:, sel], sigma_frames, clip.loop)
    mot = None if clip.motion is None else clip.motion.copy()
    return clip.copy(rot=rot, pos=pos, motion=mot)


class OneEuro:
    """One Euro filter (Casiez et al. 2012) for streams of vectors; used live by the webcam pipeline too."""

    def __init__(self, min_cutoff=1.0, beta=0.3, d_cutoff=1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.x = self.dx = None

    @staticmethod
    def _alpha(cutoff, dt):
        tau = 1.0 / (2 * np.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x, dt):
        x = np.asarray(x, np.float64)
        if self.x is None:
            self.x, self.dx = x.copy(), np.zeros_like(x)
            return x.copy()
        dx = (x - self.x) / dt
        a_d = self._alpha(self.d_cutoff, dt)
        self.dx = a_d * dx + (1 - a_d) * self.dx
        cutoff = self.min_cutoff + self.beta * np.abs(self.dx)
        a = self._alpha(cutoff, dt)
        self.x = a * x + (1 - a) * self.x
        return self.x.copy()


def jitter(clip, joints=None):
    """Mean angular acceleration (deg / frame^2) over the clip - a simple jitter score."""
    q = Q.q_continuity(clip.rot, 0)
    sel = slice(None) if joints is None else [clip.jindex(j) for j in joints]
    r = Q.q_log(q[:, sel])
    if r.shape[0] < 3:
        return 0.0
    acc = r[2:] - 2 * r[1:-1] + r[:-2]
    return float(np.degrees(np.mean(np.linalg.norm(acc, axis=-1))) * 2)


# ---------------------------------------------------------------- foot contacts
def foot_contacts(clip, skel=None, height=None, speed=None, min_frames=3):
    """-> {'l': [(start, end), ...], 'r': [...]} stance intervals (frame indices, inclusive), detected from the
    lowest foot joint's height and speed in world space with hysteresis."""
    skel = _skel(skel)
    _, gp = world_pose(clip, skel)
    leg = skel.meta.get('leg_length') or 0.9
    height = 0.06 * leg / 0.9 if height is None else height
    speed = 0.45 * leg / 0.9 if speed is None else speed
    out = {}
    lows = {}
    for s, (_, _, foot, ball) in FEET.items():
        js = [skel.index(foot)] + ([skel.index(ball)] if skel.has(ball) else [])
        low_i = np.argmin(gp[:, js, 1], axis=1)
        lows[s] = gp[np.arange(clip.frames), np.array(js)[low_i]]
    floor = min(np.percentile(v[:, 1], 5) for v in lows.values())
    for s in FEET:
        low = lows[s]
        h = low[:, 1] - floor
        v = np.zeros(clip.frames)
        if clip.frames > 1:
            v[1:] = np.linalg.norm(np.diff(low, axis=0), axis=1) * clip.fps
            v[0] = v[1]
        sp = speed
        if not np.any((h < height) & (v < sp)):  # stylised runs slide a little: relax once
            sp = max(speed, 0.06 * float(v.max()))
        on = (h < height) & (v < sp)
        off = (h > height * 1.5) | (v > sp * 1.5)
        state, cur, iv = False, None, []
        for f in range(clip.frames):
            if not state and on[f]:
                state, cur = True, f
            elif state and off[f]:
                if f - cur >= min_frames:
                    iv.append((cur, f - 1))
                state = False
        if state and clip.frames - cur >= min_frames:
            iv.append((cur, clip.frames - 1))
        out[s] = iv
    return out


def two_bone_ik(a, b, c, target, bend_hint=None):
    """Analytic two-bone IK on positions: a (root) b (mid) c (end) -> new mid and end positions reaching `target`
    in the plane of the current bend (or bend_hint). Works on single 3-vectors."""
    l1, l2 = np.linalg.norm(b - a), np.linalg.norm(c - b)
    d_vec = target - a
    d = np.clip(np.linalg.norm(d_vec), 1e-6, (l1 + l2) * 0.9999)
    u = d_vec / max(np.linalg.norm(d_vec), 1e-9)
    n = np.cross(b - a, c - a)
    if np.linalg.norm(n) < 1e-9 * max(l1 * l2, 1e-9):
        n = np.cross(u, bend_hint if bend_hint is not None else np.array([0, 0, 1.0]))
    n /= max(np.linalg.norm(n), 1e-12)
    v = np.cross(n, u)
    if np.linalg.norm(v) < 1e-9:  # target along the old bend normal: any perpendicular will do
        v = np.cross(u, [1.0, 0, 0]) if abs(u[0]) < 0.9 else np.cross(u, [0, 1.0, 0])
    v /= np.linalg.norm(v)  # unit and perpendicular to u, so both bone lengths are kept exactly
    if np.dot(v, b - a) < 0 and bend_hint is None:
        v = -v
    cos_a = np.clip((l1 * l1 + d * d - l2 * l2) / (2 * l1 * d), -1, 1)
    mid = a + l1 * (cos_a * u + np.sqrt(1 - cos_a * cos_a) * v)
    end = a + u * d
    return mid, end


def _smoothstep(x):
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3 - 2 * x)


PLANT_H = 0.02  # a foot part within 2 cm of its lowest point in a stance counts as planted


def lock_feet(clip, skel=None, contacts=None, blend_frames=3):
    """Remove foot sliding during stance: the heel (ankle) is pinned while it is down, the toe (ball) once only the
    toe is down (its anchor comes from the locked flat-foot phase, so the hand-over cannot pop). The foot is
    shifted horizontally only (heights and the foot's own roll stay as animated) and the leg follows by two-bone
    IK; blended in/out over `blend_frames`."""
    skel = _skel(skel)
    contacts = foot_contacts(clip, skel) if contacts is None else contacts
    lr, lt = _full_pose(clip, skel)
    F = clip.frames
    root_r = root_t = None
    if clip.motion is not None:
        root_r = Q.q_from_yaw(clip.motion[:, 3])
        root_t = clip.motion[:, :3]
    gr, gp = skel.fk(lr, lt, root_r=root_r, root_t=root_t)
    moved = 0
    for s, iv in contacts.items():
        th, ca, fo, ba = (skel.index(n) for n in FEET[s])
        shift = np.zeros((F, 3))
        w = np.zeros(F)
        for (a0, a1) in iv:
            fr = np.arange(a0, a1 + 1)
            ank = gp[fr, fo]
            heel = (ank[:, 1] - ank[:, 1].min()) < PLANT_H
            if ba is not None:
                bal = gp[fr, ba]
                toe = (bal[:, 1] - bal[:, 1].min()) < PLANT_H
            else:
                bal, toe = ank, heel
            if not heel.any() and not toe.any():
                continue
            anchor_a = ank[heel].mean(0) if heel.any() else None
            both = heel & toe
            if anchor_a is not None and both.any():
                anchor_b = (bal[both] + (anchor_a - ank[both])).mean(0)   # where the locked flat foot put the toe
            else:
                anchor_b = bal[toe].mean(0) if toe.any() else None
            for k, f in enumerate(fr):
                if heel[k] and anchor_a is not None:
                    d = anchor_a - ank[k]
                elif toe[k] and anchor_b is not None:
                    d = anchor_b - bal[k]
                else:
                    d = shift[f - 1] if f > 0 else np.zeros(3)
                shift[f] = [d[0], 0.0, d[2]]
                w[f] = 1.0
            for f in range(max(0, a0 - blend_frames), a0):
                wf = 1 - (a0 - f) / (blend_frames + 1)
                if wf > w[f]:
                    w[f], shift[f] = wf, shift[a0]
            for f in range(a1 + 1, min(F, a1 + blend_frames + 1)):
                wf = 1 - (f - a1) / (blend_frames + 1)
                if wf > w[f]:
                    w[f], shift[f] = wf, shift[a1]
        for f in np.nonzero(w > 1e-6)[0]:
            A, Bk, C = gp[f, th], gp[f, ca], gp[f, fo]
            target = C + shift[f] * w[f]
            if np.linalg.norm(target - C) < 1e-7:
                continue
            mid, end = two_bone_ik(A, Bk, C, target)
            g_th = Q.q_mul(Q.q_between(Bk - A, mid - A), gr[f, th])
            g_ca_tmp = Q.q_mul(g_th, lr[f, ca])
            g_ca = Q.q_mul(Q.q_between(Q.q_rotate(g_ca_tmp, lt[f, fo]), end - mid), g_ca_tmp)
            lr[f, th] = Q.q_mul(Q.q_conj(gr[f, skel.parents[th]]), g_th)
            lr[f, ca] = Q.q_mul(Q.q_conj(g_th), g_ca)
            lr[f, fo] = Q.q_mul(Q.q_conj(g_ca), gr[f, fo])  # foot keeps its animated world orientation
            moved += 1
    out = _store(clip, skel, lr, lt)
    out.meta = dict(out.meta, foot_lock={'frames': int(moved), 'contacts': {k: [list(map(int, x)) for x in v]
                                                                            for k, v in contacts.items()}})
    return out


def foot_sliding(clip, skel=None, contacts=None):
    """Mean horizontal drift (metres) of the planted part of each foot during stance: the heel (ankle) over the
    frames it is down, the toe (ball) over the frames it is down; the worse of the two per stance. Lower is better."""
    skel = _skel(skel)
    contacts = foot_contacts(clip, skel) if contacts is None else contacts
    _, gp = world_pose(clip, skel)
    d = []
    for s, iv in contacts.items():
        fo, ba = skel.index(FEET[s][2]), skel.index(FEET[s][3])
        for a0, a1 in iv:
            fr = np.arange(a0, a1 + 1)
            parts = []
            ank = gp[fr, fo]
            heel_down = (ank[:, 1] - ank[:, 1].min()) < PLANT_H
            if heel_down.sum() >= 2:
                parts.append(ank[heel_down][:, [0, 2]])
            if ba is not None:
                bal = gp[fr, ba]
                toe_down = (bal[:, 1] - bal[:, 1].min()) < PLANT_H
                if toe_down.sum() >= 2:
                    parts.append(bal[toe_down][:, [0, 2]])
            if parts:
                d.append(max(float(np.max(np.linalg.norm(p - p.mean(0), axis=1))) for p in parts))
    return float(np.mean(d)) if d else 0.0


# ---------------------------------------------------------------- events
def footstep_frames_from_minima(clip, skel=None, min_gap=0.2):
    """Fallback for fast/stylised cycles: each foot's height minima near the floor are its footfalls."""
    skel = _skel(skel)
    _, gp = world_pose(clip, skel)
    lows = {}
    for s, (_, _, foot, ball) in FEET.items():
        js = [skel.index(foot)] + ([skel.index(ball)] if skel.has(ball) else [])
        lows[s] = np.min(gp[:, js, 1], axis=1)
    floor = min(v.min() for v in lows.values())
    out, gap = {}, max(1, int(round(min_gap * clip.fps)))
    for s, h in lows.items():
        F = len(h)
        cand = []
        for f in range(F):
            a, b = h[(f - 1) % F] if clip.loop else h[max(f - 1, 0)], h[(f + 1) % F] if clip.loop else h[min(f + 1, F - 1)]
            if h[f] <= a and h[f] <= b and h[f] - floor < 0.08 and not (clip.loop and f == F - 1):
                if not cand or f - cand[-1] >= gap:
                    cand.append(f)
        out[s] = cand
    return out


def add_footstep_events(clip, skel=None, contacts=None, replace=True):
    """Footstep events at the start of each stance (contacts); cycles where a foot shows no stance (fast runs)
    fall back to that foot's height minima."""
    contacts = foot_contacts(clip, skel) if contacts is None else contacts
    ev = [e for e in clip.events if not (replace and e.get('type') == 'footstep')]
    minima = None
    for s, iv in contacts.items():
        frames = [a0 for a0, _ in iv]
        if not frames:
            minima = footstep_frames_from_minima(clip, skel) if minima is None else minima
            frames = minima.get(s, [])
        for f in frames:
            ev.append({'t': round(f / clip.fps, 4), 'type': 'footstep', 'foot': s})
    return clip.copy(events=sorted(ev, key=lambda e: e['t']))


def hit_frame(clip, skel=None):
    """Frame where the fastest end effector (hands, feet) peaks - the 'impact' of an attack."""
    skel = _skel(skel)
    _, gp = world_pose(clip, skel)
    best, bf, limb = -1.0, 0, None
    for name in ('hand_l', 'hand_r', 'foot_l', 'foot_r'):
        i = skel.index(name)
        if i is None or clip.frames < 3:
            continue
        v = np.linalg.norm(np.diff(gp[:, i], axis=0), axis=1) * clip.fps
        f = int(np.argmax(v))
        if v[f] > best:
            best, bf, limb = float(v[f]), f + 1, name
    return bf, limb, best


def add_hit_event(clip, skel=None, replace=True):
    f, limb, v = hit_frame(clip, skel)
    ev = [e for e in clip.events if not (replace and e.get('type') == 'hit')]
    ev.append({'t': round(f / clip.fps, 4), 'type': 'hit', 'limb': limb, 'speed': round(v, 3)})
    return clip.copy(events=sorted(ev, key=lambda e: e['t']))


# ---------------------------------------------------------------- trim / loop / speed / mirror
def trim(clip, start=0.0, end=None):
    """Keep [start, end] seconds (frames rounded); events and root motion are re-based."""
    f0 = int(round(max(0.0, start) * clip.fps))
    f1 = clip.frames - 1 if end is None else int(round(min(end, clip.duration) * clip.fps))
    if f1 <= f0:
        raise ValueError('trim range is empty')
    sl = slice(f0, f1 + 1)
    mot = None
    if clip.motion is not None:
        m = clip.motion[sl].copy()
        q0 = Q.q_from_yaw(np.full(len(m), -m[0, 3]))
        m[:, :3] = Q.q_rotate(q0, m[:, :3] - m[:1, :3])
        m[:, 3] -= m[0, 3]
        mot = m
    t0 = f0 / clip.fps
    ev = [dict(e, t=round(e['t'] - t0, 4)) for e in clip.events if t0 - 1e-9 <= e['t'] <= f1 / clip.fps + 1e-9]
    return clip.copy(rot=clip.rot[sl].copy(), pos=clip.pos[sl].copy(), motion=mot, events=ev, loop=False)


def _pose_dist(clip, i, j):
    return float(np.mean(Q.q_angle_between(clip.rot[i], clip.rot[j])))


def make_loop(clip, blend=0.25, search=True, min_len=0.5):
    """Seamless loop: optionally pick the end frame whose pose (and next-frame pose) best matches the start, then
    spread the remaining seam difference over the last `blend` seconds (offset blending keeps the motion's own
    dynamics) so the last frame equals the first exactly."""
    c = clip
    if search and clip.frames > 8:
        lo = int(min(clip.frames - 2, max(2, round(min_len * clip.fps))))
        best = min(range(lo, clip.frames),
                   key=lambda f: _pose_dist(clip, 0, f) + 0.5 * _pose_dist(clip, 1, min(f + 1, clip.frames - 1)))
        c = trim(clip, 0, best / clip.fps)
    F = c.frames
    seam = float(np.degrees(_pose_dist(c, 0, F - 1)))
    nb = int(min(F - 1, max(1, round(blend * c.fps))))
    rot = Q.q_continuity(c.rot.copy(), 0)
    pos = c.pos.copy()
    d_r = Q.q_mul(rot[0], Q.q_conj(rot[-1]))       # rot[0] = d_r * rot[-1]   (per joint, parent space)
    d_p = pos[0] - pos[-1]
    ident = Q.quat_identity((rot.shape[1],))
    for f in range(F - 1 - nb, F):
        w = (f - (F - 1 - nb)) / nb
        w = w * w * (3 - 2 * w)
        rot[f] = Q.q_mul(Q.q_slerp(ident, d_r, np.full(rot.shape[1], w)), rot[f])
        pos[f] = pos[f] + w * d_p
    rot[-1] = rot[0]
    pos[-1] = pos[0]
    out = c.copy(rot=rot, pos=pos, loop=True)
    out.meta = dict(out.meta, loop_fix={'frames': F, 'blend_frames': nb, 'seam_deg_before': round(seam, 3)})
    return out


def speed(clip, factor):
    """Play `factor` times faster (2 = twice as fast); fps stays, frames are resampled, events re-timed."""
    if factor <= 0:
        raise ValueError('speed factor must be > 0')
    src = clip.copy()
    src.fps = clip.fps * factor
    out = src.resample(clip.fps)
    out.events = [dict(e, t=round(e['t'] / factor, 4)) for e in clip.events]
    return out


def mirror(clip, skel=None):
    """Left <-> right mirror across the character's YZ plane (x -> -x), in world space, any symmetric rig."""
    skel = _skel(skel)
    lr, lt = _full_pose(clip, skel)
    gr, gp = skel.fk(lr, lt)
    rgr, rgp = skel.rest_globals()
    D = Q.q_mul(gr, Q.q_conj(rgr)[None])
    Dm = D * np.array([1.0, -1.0, -1.0, 1.0])  # reflect rotation across x = 0
    partner = [skel.index(mirror_name(n)) if skel.has(mirror_name(n)) else i for i, n in enumerate(skel.names)]
    G2 = np.empty_like(gr)
    for i in range(len(skel)):
        G2[:, partner[i]] = Q.q_mul(Dm[:, i], rgr[partner[i]][None])
    lr2 = np.empty_like(lr)
    for i in range(len(skel)):
        p = skel.parents[i]
        lr2[:, i] = G2[:, i] if p < 0 else Q.q_mul(Q.q_conj(G2[:, p]), G2[:, i])
    lt2 = lt.copy()
    X = np.array([-1.0, 1, 1])
    for i in range(len(skel)):
        p = skel.parents[i]
        if p < 0:
            lt2[:, partner[i]] = gp[:, i] * X
        elif np.max(np.abs(lt[:, i] - skel.rest_t[i])) > 1e-6:
            pp = partner[p]
            lt2[:, partner[i]] = Q.q_rotate(Q.q_conj(G2[:, pp]), gp[:, i] * X - gp[:, p] * X)
    mot = None
    if clip.motion is not None:
        mot = clip.motion * np.array([-1.0, 1, 1, -1])
    ev = []
    for e in clip.events:
        e2 = dict(e)
        for key in ('foot', 'hand', 'side'):
            if e2.get(key) in ('l', 'r'):
                e2[key] = 'r' if e2[key] == 'l' else 'l'
        if isinstance(e2.get('limb'), str):
            e2['limb'] = mirror_name(e2['limb'])
        ev.append(e2)
    out = _store(clip, skel, lr2, lt2, motion=mot, events=ev)
    out.name = clip.name + '_mirror'
    return out


def clean(clip, skel=None, smooth_sigma=1.2, lock=True, footsteps=True, loop=None):
    """One-call clean-up used by the importer: smooth -> lock feet -> footstep events (-> loop)."""
    c = smooth(clip, smooth_sigma) if smooth_sigma else clip
    if lock:
        c = lock_feet(c, skel)
    if footsteps:
        c = add_footstep_events(c, skel)
    if loop:
        c = make_loop(c)
    return c
