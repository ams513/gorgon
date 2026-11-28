"""bvh.py - full Biovision Hierarchy (BVH) reader and writer.

Reader: any channel layout per joint (3 or 6 channels, rotation and position channels in any order, partial
rotation sets), joint names with spaces ("Bip01 L Thigh"), braces on their own line or not, several ROOTs,
"End Site" leaves (kept as '<parent>_End' joints so leaf bones keep a direction), CRLF/LF, tabs, truncated motion
blocks (reports how many frames were usable). Rotations follow the BVH rule: CHANNELS Zrotation Xrotation Yrotation
means local = Rz * Rx * Ry. Position channels REPLACE the OFFSET (Blender/MotionBuilder convention; for CMU-style
files the root OFFSET is zero, so both readings agree); a non-root joint whose position channels hover around zero
while its OFFSET does not is read as offset + channel instead.
Writer: Skeleton + Clip -> BVH (root 6 channels, others 3; any Euler order), used for exports and tests.
"""
import math, os, re

import numpy as np

from . import qmath as Q
from .clip import Clip
from .rig import Skeleton

ROT = {'xrotation': 'X', 'yrotation': 'Y', 'zrotation': 'Z'}
POS = {'xposition': 0, 'yposition': 1, 'zposition': 2}


class BVHError(ValueError):
    pass


class _Joint:
    __slots__ = ('name', 'parent', 'offset', 'channels', 'end')

    def __init__(self, name, parent, end=False):
        self.name, self.parent, self.offset, self.channels, self.end = name, parent, np.zeros(3), [], end


def _tokens(lines):
    for ln, line in enumerate(lines, 1):
        for m in re.finditer(r'\S+', line):
            yield ln, m.group(0), line


def parse_bvh_text(text, name='bvh'):
    """-> (Skeleton, Clip, info). Units are left as in the file; Skeleton.meta['end_sites'] lists pseudo joints."""
    if text.startswith('﻿'):
        text = text[1:]
    lines = text.splitlines()
    joints = []
    stack = []
    i = 0
    n = len(lines)

    def strip_brace(s):
        return s.replace('{', '').strip()

    # ---- hierarchy (line oriented, so names may contain spaces)
    while i < n and not lines[i].strip().upper().startswith('HIERARCHY'):
        if lines[i].strip():
            raise BVHError('not a BVH file (no HIERARCHY header)')
        i += 1
    if i >= n:
        raise BVHError('not a BVH file (no HIERARCHY header)')
    i += 1
    pending = None
    while i < n:
        raw = lines[i]
        s = raw.strip()
        i += 1
        if not s:
            continue
        up = s.upper()
        if up.startswith('MOTION'):
            break
        if up.startswith('ROOT') or up.startswith('JOINT'):
            nm = strip_brace(s.split(None, 1)[1] if len(s.split(None, 1)) > 1 else '')
            if not nm:
                raise BVHError(f'line {i}: joint without a name')
            par = stack[-1] if stack else -1
            if up.startswith('JOINT') and not stack:
                raise BVHError(f'line {i}: JOINT outside a ROOT block')
            joints.append(_Joint(nm, par))
            pending = len(joints) - 1
            if '{' in s:
                stack.append(pending)
                pending = None
            continue
        if up.startswith('END'):
            if not stack:
                raise BVHError(f'line {i}: End Site outside a joint')
            par = stack[-1]
            joints.append(_Joint(joints[par].name + '_End', par, end=True))
            pending = len(joints) - 1
            if '{' in s:
                stack.append(pending)
                pending = None
            continue
        if s.startswith('{'):
            if pending is None:
                raise BVHError(f'line {i}: unexpected "{{"')
            stack.append(pending)
            pending = None
            rest = s[1:].strip()
            if not rest:
                continue
            s, up = rest, rest.upper()
        if s.startswith('}'):
            if not stack:
                raise BVHError(f'line {i}: unbalanced "}}"')
            stack.pop()
            continue
        if up.startswith('OFFSET'):
            vals = s.split()[1:4]
            if len(vals) < 3 or not stack:
                raise BVHError(f'line {i}: bad OFFSET')
            joints[stack[-1]].offset = np.array([float(v) for v in vals])
            continue
        if up.startswith('CHANNELS'):
            parts = s.split()
            try:
                cnt = int(parts[1])
            except (IndexError, ValueError):
                raise BVHError(f'line {i}: bad CHANNELS line')
            ch = [c.lower() for c in parts[2:2 + cnt]]
            if len(ch) != cnt or any(c not in ROT and c not in POS for c in ch):
                raise BVHError(f'line {i}: unknown channel in {parts[2:]}')
            if not stack:
                raise BVHError(f'line {i}: CHANNELS outside a joint')
            joints[stack[-1]].channels = ch
            continue
        raise BVHError(f'line {i}: unexpected "{s[:40]}"')
    if stack:
        raise BVHError('hierarchy ends inside an open "{" block')
    if not joints:
        raise BVHError('BVH hierarchy has no joints')
    # ---- motion
    frames, ftime = None, None
    while i < n:
        s = lines[i].strip()
        i += 1
        if not s:
            continue
        low = s.lower()
        if low.startswith('frames:'):
            frames = int(float(s.split(':', 1)[1]))
        elif low.startswith('frame time:'):
            ftime = float(s.split(':', 1)[1])
            break
        else:
            raise BVHError(f'line {i}: expected "Frames:" / "Frame Time:"')
    if frames is None or ftime is None:
        raise BVHError('MOTION block lacks "Frames:" or "Frame Time:"')
    C = sum(len(j.channels) for j in joints)
    nums = np.array(' '.join(lines[i:]).split(), dtype=np.float64) if C else np.zeros(0)
    usable = len(nums) // C if C else frames
    warnings = []
    if usable < frames:
        warnings.append(f'motion block truncated: {usable} of {frames} frames usable')
        frames = usable
    if frames <= 0:
        raise BVHError('BVH has no complete motion frames')
    data = nums[:frames * C].reshape(frames, C) if C else np.zeros((frames, 0))
    # ---- channels -> local transforms
    J = len(joints)
    rot = Q.quat_identity((frames, J))
    pos = np.zeros((frames, J, 3))
    col = 0
    orders = {}
    for k, jt in enumerate(joints):
        pos[:, k] = jt.offset
        rch = [(c, idx) for idx, c in enumerate(jt.channels) if c in ROT]
        pch = [(c, idx) for idx, c in enumerate(jt.channels) if c in POS]
        if rch:
            order = ''.join(ROT[c] for c, _ in rch)
            orders[jt.name] = order
            ang = np.stack([data[:, col + idx] for _, idx in rch], axis=1)
            q = None
            for a_i, (c, _) in enumerate(rch):
                qi = Q.q_from_axis_angle(np.broadcast_to(Q.AXES[ROT[c]], (frames, 3)), np.radians(ang[:, a_i]))
                q = qi if q is None else Q.q_mul(q, qi)
            rot[:, k] = q
        if pch:
            p = jt.offset.copy() * np.ones((frames, 3))
            vals = np.zeros((frames, 3))
            for c, idx in pch:
                vals[:, POS[c]] = data[:, col + idx]
            if jt.parent >= 0:
                off = np.linalg.norm(jt.offset)
                if off > 1e-6 and np.mean(np.linalg.norm(vals, axis=1)) < 0.25 * off:
                    p = jt.offset + vals  # additive reading
                else:
                    p = vals
            else:
                p = vals if len(pch) == 3 else jt.offset + vals
            pos[:, k] = p
        col += len(jt.channels)
    names, parents = [j.name for j in joints], [j.parent for j in joints]
    rest_t = np.stack([j.offset for j in joints])
    skel = Skeleton(names, parents, rest_t, Q.quat_identity((J,)),
                    meta={'source': name, 'format': 'bvh', 'end_sites': [j.name for j in joints if j.end],
                          'euler_orders': orders})
    rot = Q.q_continuity(rot, axis=0)
    clip = Clip(os.path.splitext(name)[0], names, rot, pos, fps=1.0 / ftime if ftime > 0 else 30.0,
                meta={'source': name, 'source_format': 'bvh', 'frame_time': ftime})
    info = {'frames': frames, 'frame_time': ftime, 'channels': C, 'joints': J, 'warnings': warnings,
            'end_sites': skel.meta['end_sites'], 'euler_orders': sorted(set(orders.values()))}
    return skel, clip, info


def load_bvh(path):
    with open(path, 'rb') as f:
        raw = f.read()
    for enc in ('utf-8-sig', 'cp1252', 'latin-1'):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    return parse_bvh_text(text, os.path.basename(path))


def _fmt(v):
    s = f'{v:.6f}'.rstrip('0').rstrip('.')
    return '0' if s in ('-0', '') else s


def to_bvh_convention(skel, clip):
    """Re-express a skeleton with rest rotations (glTF style) in BVH form: identity rest rotations, world-axis
    offsets, so that the BVH zero pose equals the skeleton's rest pose and FK reproduces every frame exactly."""
    gr_rest, gp_rest = skel.rest_globals()
    idx = {n: k for k, n in enumerate(clip.joints)}
    F, J = clip.frames, len(skel)
    lr = np.broadcast_to(skel.rest_r, (F, J, 4)).copy()
    lt = np.broadcast_to(skel.rest_t, (F, J, 3)).copy()
    for i, n in enumerate(skel.names):
        k = idx.get(n)
        if k is not None:
            lr[:, i] = clip.rot[:, k]
            lt[:, i] = clip.pos[:, k]
    gr, gp = skel.fk(lr, lt)
    gd = Q.q_mul(gr, Q.q_conj(gr_rest)[None])           # world delta from rest per joint
    new_t = np.zeros((J, 3))
    new_r = np.zeros((F, J, 4))
    new_p = np.zeros((F, J, 3))
    for i in range(J):
        p = skel.parents[i]
        if p < 0:
            new_t[i] = gp_rest[i]
            new_r[:, i] = gd[:, i]
            new_p[:, i] = gp[:, i]
        else:
            new_t[i] = gp_rest[i] - gp_rest[p]
            new_r[:, i] = Q.q_mul(Q.q_conj(gd[:, p]), gd[:, i])
            new_p[:, i] = Q.q_rotate(Q.q_conj(gd[:, p]), gp[:, i] - gp[:, p])
    s2 = Skeleton(skel.names, skel.parents, new_t, Q.quat_identity((J,)), meta=dict(skel.meta))
    c2 = clip.copy(joints=list(skel.names), rot=Q.q_continuity(new_r, 0), pos=new_p)
    return s2, c2


def bvh_text(skel, clip, order='ZXY', root_channels=6, all_positions=False, scale=1.0, world_aligned=True):
    """Skeleton + Clip (same joint names) -> BVH text. End-site pseudo joints (meta end_sites or *_End leaves)
    are written as 'End Site' blocks. world_aligned converts glTF-style rest rotations to the BVH convention
    first (see to_bvh_convention), so other tools see the rest pose as the zero pose."""
    if world_aligned and np.max(Q.q_angle(skel.rest_r)) > 1e-6:
        skel, clip = to_bvh_convention(skel, clip)
    end_sites = set(skel.meta.get('end_sites') or []) | {n for i, n in enumerate(skel.names)
                                                         if not skel.children[i] and n.endswith('_End')}
    idx = {n: k for k, n in enumerate(clip.joints)}
    moving = set()
    for i, n in enumerate(skel.names):
        k = idx.get(n)
        if k is not None and np.max(np.abs(clip.pos[:, k] - skel.rest_t[i])) > 1e-6 * max(1.0, scale):
            moving.add(i)
    out = ['HIERARCHY']
    chan_order = []  # (joint index, has_pos)
    rch = ' '.join(f'{a}rotation' for a in order)

    def emit(i, depth):
        pad = '\t' * depth
        nm = skel.names[i]
        if nm in end_sites and not skel.children[i]:
            out.append(f'{pad}End Site')
            out.append(f'{pad}{{')
            o = skel.rest_t[i] * scale
            out.append(f'{pad}\tOFFSET {_fmt(o[0])} {_fmt(o[1])} {_fmt(o[2])}')
            out.append(f'{pad}}}')
            return
        kw = 'ROOT' if skel.parents[i] < 0 else 'JOINT'
        out.append(f'{pad}{kw} {nm}')
        out.append(f'{pad}{{')
        o = skel.rest_t[i] * scale
        out.append(f'{pad}\tOFFSET {_fmt(o[0])} {_fmt(o[1])} {_fmt(o[2])}')
        has_pos = (skel.parents[i] < 0 and root_channels == 6) or all_positions or i in moving
        if has_pos:
            out.append(f'{pad}\tCHANNELS 6 Xposition Yposition Zposition {rch}')
        else:
            out.append(f'{pad}\tCHANNELS 3 {rch}')
        chan_order.append((i, has_pos))
        kids = skel.children[i]
        if not kids:
            out.append(f'{pad}\tEnd Site')
            out.append(f'{pad}\t{{')
            out.append(f'{pad}\t\tOFFSET 0 0 0')
            out.append(f'{pad}\t}}')
        for c in kids:
            emit(c, depth + 1)
        out.append(f'{pad}}}')

    for r in skel.roots:
        emit(r, 0)
    out.append('MOTION')
    out.append(f'Frames: {clip.frames}')
    out.append(f'Frame Time: {1.0 / clip.fps:.7f}')
    cols = []
    for i, has_pos in chan_order:
        k = idx.get(skel.names[i])
        if has_pos:
            p = (clip.pos[:, k] if k is not None else np.broadcast_to(skel.rest_t[i], (clip.frames, 3))) * scale
            cols.append(p)
        q = clip.rot[:, k] if k is not None else np.broadcast_to(skel.rest_r[i], (clip.frames, 4))
        cols.append(Q.q_to_euler(q, order))
    M = np.concatenate(cols, axis=1) if cols else np.zeros((clip.frames, 0))
    for row in M:
        out.append(' '.join(_fmt(v) for v in row))
    return '\n'.join(out) + '\n'


def write_bvh(path, skel, clip, **kw):
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(bvh_text(skel, clip, **kw))
    return path
