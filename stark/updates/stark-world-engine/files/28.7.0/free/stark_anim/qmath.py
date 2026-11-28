"""qmath.py - vectorised quaternion / rotation helpers (numpy). Quaternions are [..., 4] in (x, y, z, w) order,
the glTF convention. q_mul(a, b) applies b first, then a (so global = parent_global * local)."""
import numpy as np

EPS = 1e-9


def quat_identity(shape=()):
    q = np.zeros(tuple(shape) + (4,), np.float64)
    q[..., 3] = 1.0
    return q


def q_normalize(q):
    q = np.asarray(q, np.float64)
    n = np.linalg.norm(q, axis=-1, keepdims=True)
    return np.where(n > EPS, q / np.maximum(n, EPS), quat_identity(q.shape[:-1]))


def q_mul(a, b):
    a = np.asarray(a, np.float64)
    b = np.asarray(b, np.float64)
    ax, ay, az, aw = a[..., 0], a[..., 1], a[..., 2], a[..., 3]
    bx, by, bz, bw = b[..., 0], b[..., 1], b[..., 2], b[..., 3]
    return np.stack([aw * bx + ax * bw + ay * bz - az * by,
                     aw * by - ax * bz + ay * bw + az * bx,
                     aw * bz + ax * by - ay * bx + az * bw,
                     aw * bw - ax * bx - ay * by - az * bz], axis=-1)


def q_conj(q):
    q = np.array(q, np.float64, copy=True)
    q[..., :3] *= -1.0
    return q


q_inv = q_conj  # unit quaternions


def q_rotate(q, v):
    """Rotate vectors v [..., 3] by unit quaternions q [..., 4] (broadcasting)."""
    q = np.asarray(q, np.float64)
    v = np.asarray(v, np.float64)
    u = q[..., :3]
    w = q[..., 3:4]
    t = 2.0 * np.cross(u, v)
    return v + w * t + np.cross(u, t)


def q_from_axis_angle(axis, angle):
    axis = np.asarray(axis, np.float64)
    angle = np.asarray(angle, np.float64)
    n = np.linalg.norm(axis, axis=-1, keepdims=True)
    axis = axis / np.maximum(n, EPS)
    h = angle[..., None] * 0.5
    return np.concatenate([axis * np.sin(h), np.cos(h)], axis=-1)


def q_to_axis_angle(q):
    q = q_normalize(q)
    q = np.where(q[..., 3:4] < 0, -q, q)
    s = np.linalg.norm(q[..., :3], axis=-1)
    ang = 2.0 * np.arctan2(s, q[..., 3])
    axis = np.where(s[..., None] > EPS, q[..., :3] / np.maximum(s[..., None], EPS), np.array([1.0, 0, 0]))
    return axis, ang


def q_angle(q):
    """Rotation angle (radians, 0..pi) of each quaternion."""
    q = q_normalize(q)
    return 2.0 * np.arccos(np.clip(np.abs(q[..., 3]), 0.0, 1.0))


def q_angle_between(a, b):
    """Angle (radians) of the rotation taking a to b."""
    d = np.abs(np.sum(q_normalize(a) * q_normalize(b), axis=-1))
    return 2.0 * np.arccos(np.clip(d, 0.0, 1.0))


def q_from_matrix(m):
    """Rotation matrices [..., 3, 3] (column vectors: v' = M v) -> quaternions (vectorised Shepperd)."""
    m = np.asarray(m, np.float64)
    m00, m11, m22 = m[..., 0, 0], m[..., 1, 1], m[..., 2, 2]
    tr = m00 + m11 + m22
    cands = np.stack([
        np.stack([m[..., 2, 1] - m[..., 1, 2], m[..., 0, 2] - m[..., 2, 0], m[..., 1, 0] - m[..., 0, 1], 1.0 + tr], -1),
        np.stack([1.0 + m00 - m11 - m22, m[..., 0, 1] + m[..., 1, 0], m[..., 0, 2] + m[..., 2, 0],
                  m[..., 2, 1] - m[..., 1, 2]], -1),
        np.stack([m[..., 0, 1] + m[..., 1, 0], 1.0 + m11 - m00 - m22, m[..., 1, 2] + m[..., 2, 1],
                  m[..., 0, 2] - m[..., 2, 0]], -1),
        np.stack([m[..., 0, 2] + m[..., 2, 0], m[..., 1, 2] + m[..., 2, 1], 1.0 + m22 - m00 - m11,
                  m[..., 1, 0] - m[..., 0, 1]], -1)], axis=-2)
    pick = np.argmax(np.stack([tr, m00, m11, m22], -1), axis=-1)
    q = np.take_along_axis(cands, pick[..., None, None].repeat(4, -1), axis=-2)[..., 0, :]
    return q_normalize(q)


def q_to_matrix(q):
    q = q_normalize(q)
    x, y, z, w = q[..., 0], q[..., 1], q[..., 2], q[..., 3]
    m = np.empty(q.shape[:-1] + (3, 3))
    m[..., 0, 0] = 1 - 2 * (y * y + z * z)
    m[..., 0, 1] = 2 * (x * y - z * w)
    m[..., 0, 2] = 2 * (x * z + y * w)
    m[..., 1, 0] = 2 * (x * y + z * w)
    m[..., 1, 1] = 1 - 2 * (x * x + z * z)
    m[..., 1, 2] = 2 * (y * z - x * w)
    m[..., 2, 0] = 2 * (x * z - y * w)
    m[..., 2, 1] = 2 * (y * z + x * w)
    m[..., 2, 2] = 1 - 2 * (x * x + y * y)
    return m


def q_between(a, b):
    """Shortest-arc rotation taking direction a to direction b (both [..., 3])."""
    a = np.asarray(a, np.float64)
    b = np.asarray(b, np.float64)
    a = a / np.maximum(np.linalg.norm(a, axis=-1, keepdims=True), EPS)
    b = b / np.maximum(np.linalg.norm(b, axis=-1, keepdims=True), EPS)
    d = np.sum(a * b, axis=-1, keepdims=True)
    c = np.cross(a, b)
    q = np.concatenate([c, 1.0 + d], axis=-1)
    # opposite vectors: rotate 180 degrees about any axis perpendicular to a
    opp = (d[..., 0] < -1.0 + 1e-7)
    if np.any(opp):
        ax = np.cross(a, np.array([1.0, 0, 0]))
        small = np.linalg.norm(ax, axis=-1) < 1e-6
        ax = np.where(small[..., None], np.cross(a, np.array([0, 1.0, 0])), ax)
        q = np.where(opp[..., None], np.concatenate([ax, np.zeros_like(d)], axis=-1), q)
    return q_normalize(q)


def frame_from_vectors(primary, secondary):
    """Orthonormal frame (3x3, columns = x, y, z axes) with x along primary and y in the primary/secondary plane."""
    x = np.asarray(primary, np.float64)
    x = x / max(np.linalg.norm(x), EPS)
    s = np.asarray(secondary, np.float64)
    y = s - x * np.dot(s, x)
    if np.linalg.norm(y) < 1e-8:
        y = np.cross(x, [0, 0, 1.0]) if abs(x[2]) < 0.9 else np.cross(x, [0, 1.0, 0])
    y = y / max(np.linalg.norm(y), EPS)
    z = np.cross(x, y)
    return np.stack([x, y, z], axis=1)


def q_align_two(src_primary, src_secondary, dst_primary, dst_secondary):
    """Rotation R with R*src_primary = dst_primary exactly and R*src_secondary as close as possible to
    dst_secondary (two-vector 'look rotation')."""
    fs = frame_from_vectors(src_primary, src_secondary)
    fd = frame_from_vectors(dst_primary, dst_secondary)
    return q_from_matrix(fd @ fs.T)


def q_slerp(a, b, t):
    """Spherical interpolation; t broadcasts against a.shape[:-1]."""
    a = q_normalize(a)
    b = q_normalize(b)
    t = np.asarray(t, np.float64)
    d = np.sum(a * b, axis=-1, keepdims=True)
    b = np.where(d < 0, -b, b)
    d = np.abs(d)
    tt = t[..., None]
    th = np.arccos(np.clip(d, -1.0, 1.0))
    sn = np.sin(th)
    lin = sn < 1e-6
    k0 = np.where(lin, 1.0 - tt, np.sin((1.0 - tt) * th) / np.where(lin, 1.0, sn))
    k1 = np.where(lin, tt, np.sin(tt * th) / np.where(lin, 1.0, sn))
    return q_normalize(a * k0 + b * k1)


def q_nlerp(a, b, t):
    a = np.asarray(a, np.float64)
    b = np.asarray(b, np.float64)
    d = np.sum(a * b, axis=-1, keepdims=True)
    b = np.where(d < 0, -b, b)
    tt = np.asarray(t, np.float64)[..., None]
    return q_normalize(a * (1.0 - tt) + b * tt)


def q_continuity(q, axis=0):
    """Flip signs along `axis` (time) so consecutive quaternions stay in the same hemisphere."""
    q = np.array(q, np.float64, copy=True)
    q = np.moveaxis(q, axis, 0)
    for i in range(1, q.shape[0]):
        d = np.sum(q[i] * q[i - 1], axis=-1)
        q[i][d < 0] *= -1.0
    return np.moveaxis(q, 0, axis)


def q_log(q):
    """Quaternion log -> rotation vectors scaled by 1/2 ([..., 3])."""
    q = q_normalize(q)
    v = q[..., :3]
    s = np.linalg.norm(v, axis=-1, keepdims=True)
    ang = np.arctan2(s, q[..., 3:4])
    return np.where(s > EPS, v / np.maximum(s, EPS) * ang, v)


def q_exp(r):
    r = np.asarray(r, np.float64)
    a = np.linalg.norm(r, axis=-1, keepdims=True)
    return q_normalize(np.concatenate([np.where(a > EPS, r / np.maximum(a, EPS) * np.sin(a), r), np.cos(a)], axis=-1))


def q_swing_twist(q, axis):
    """Split q = swing * twist where twist rotates about `axis` (unit, in the same frame as q)."""
    q = q_normalize(q)
    axis = np.asarray(axis, np.float64)
    p = np.sum(q[..., :3] * axis, axis=-1, keepdims=True) * axis
    twist = np.concatenate([p, q[..., 3:4]], axis=-1)
    n = np.linalg.norm(twist, axis=-1, keepdims=True)
    twist = np.where(n > EPS, twist / np.maximum(n, EPS), quat_identity(q.shape[:-1]))
    swing = q_mul(q, q_conj(twist))
    return swing, twist


def q_yaw(q, up=(0.0, 1.0, 0.0), fwd=(0.0, 0.0, 1.0)):
    """Heading angle (radians, about +Y) of the forward axis rotated by q, projected on the ground plane."""
    f = q_rotate(q, np.broadcast_to(np.asarray(fwd, np.float64), np.shape(q)[:-1] + (3,)))
    return np.arctan2(f[..., 0], f[..., 2])


def q_from_yaw(yaw):
    yaw = np.asarray(yaw, np.float64)
    h = yaw * 0.5
    z = np.zeros_like(h)
    return np.stack([z, np.sin(h), z, np.cos(h)], axis=-1)


AXES = {'X': np.array([1.0, 0, 0]), 'Y': np.array([0, 1.0, 0]), 'Z': np.array([0, 0, 1.0])}


def q_from_euler(angles_deg, order):
    """Intrinsic Euler angles in the given channel order (e.g. 'ZXY' as written in BVH CHANNELS):
    q = R(order[0], a0) * R(order[1], a1) * R(order[2], a2). angles_deg [..., 3]."""
    a = np.radians(np.asarray(angles_deg, np.float64))
    q = None
    for i, ax in enumerate(order.upper()):
        qi = q_from_axis_angle(np.broadcast_to(AXES[ax], a.shape[:-1] + (3,)), a[..., i])
        q = qi if q is None else q_mul(q, qi)
    return q


def q_to_euler(q, order):
    """Inverse of q_from_euler (intrinsic, BVH channel order), degrees [..., 3]."""
    m = q_to_matrix(q)
    order = order.upper()
    i, j, k = ('XYZ'.index(c) for c in order)
    # M = R_i(a) R_j(b) R_k(c); parity of the permutation decides signs
    par = 1.0 if (j - i) % 3 == 1 else -1.0
    out = np.empty(m.shape[:-2] + (3,))
    sj = np.clip(par * m[..., i, k], -1.0, 1.0)
    out[..., 1] = np.arcsin(sj)
    cj = np.cos(out[..., 1])
    ok = cj > 1e-6
    out[..., 0] = np.where(ok, np.arctan2(-par * m[..., j, k], m[..., k, k]), np.arctan2(par * m[..., k, j], m[..., j, j]))
    out[..., 2] = np.where(ok, np.arctan2(-par * m[..., i, j], m[..., i, i]), 0.0)
    return np.degrees(out)


def mat4_from_trs(t, q, s=(1.0, 1.0, 1.0)):
    m = np.eye(4)
    m[:3, :3] = q_to_matrix(q) * np.asarray(s, np.float64)[None, :]
    m[:3, 3] = t
    return m


def unwrap(a):
    return np.unwrap(np.asarray(a, np.float64))
