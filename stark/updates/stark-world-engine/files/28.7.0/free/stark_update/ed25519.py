"""Ed25519 (RFC 8032) in pure Python for the Stark World Engine update feed. verify() is all a buyer's copy needs;
sign() / public_key() are used only by the seller-side feed tool (feed.py) and the tests. Not constant-time: it only
handles public data (feeds and signatures) on the buyer side, and the seller signs on his own machine."""
import hashlib

_P = 2 ** 255 - 19
_Q = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, -1, _P) % _P
_I = pow(2, (_P - 1) // 4, _P)


def _xrecover(y, sign):
    x2 = (y * y - 1) * pow(_D * y * y + 1, -1, _P) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * _I % _P
    if (x * x - x2) % _P:
        return None
    return _P - x if (x & 1) != sign else x


_BY = 4 * pow(5, -1, _P) % _P
_BX = _xrecover(_BY, 0)
_B = (_BX, _BY, 1, _BX * _BY % _P)


def _add(p, q):
    a = (p[1] - p[0]) * (q[1] - q[0]) % _P
    b = (p[1] + p[0]) * (q[1] + q[0]) % _P
    c = 2 * p[3] * q[3] * _D % _P
    d = 2 * p[2] * q[2] % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return e * f % _P, g * h % _P, f * g % _P, e * h % _P


def _mul(s, p):
    r = (0, 1, 1, 0)
    while s:
        if s & 1:
            r = _add(r, p)
        p = _add(p, p)
        s >>= 1
    return r


def _enc(p):
    zi = pow(p[2], -1, _P)
    x, y = p[0] * zi % _P, p[1] * zi % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, 'little')


def _dec(b):
    if len(b) != 32:
        return None
    y = int.from_bytes(b, 'little')
    sign, y = y >> 255, y & ((1 << 255) - 1)
    if y >= _P:
        return None
    x = _xrecover(y, sign)
    return None if x is None else (x, y, 1, x * y % _P)


def _h(m):
    return int.from_bytes(hashlib.sha512(m).digest(), 'little')


def _secret(seed):
    if len(seed) != 32:
        raise ValueError('an Ed25519 seed is 32 bytes')
    digest = hashlib.sha512(seed).digest()
    a = int.from_bytes(digest[:32], 'little')
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, digest[32:]


def public_key(seed):
    return _enc(_mul(_secret(seed)[0], _B))


def sign(seed, msg):
    a, prefix = _secret(seed)
    pk = _enc(_mul(a, _B))
    r = _h(prefix + msg) % _Q
    rb = _enc(_mul(r, _B))
    s = (r + _h(rb + pk + msg) * a) % _Q
    return rb + int.to_bytes(s, 32, 'little')


def verify(pub, msg, sig):
    """True only for a valid signature of msg by pub (both bytes)."""
    if not isinstance(pub, (bytes, bytearray)) or not isinstance(sig, (bytes, bytearray)) or len(pub) != 32 or len(sig) != 64:
        return False
    a, r = _dec(bytes(pub)), _dec(bytes(sig[:32]))
    s = int.from_bytes(sig[32:], 'little')
    if a is None or r is None or s >= _Q:
        return False
    k = _h(bytes(sig[:32]) + bytes(pub) + msg) % _Q
    lhs, rhs = _mul(s, _B), _add(r, _mul(k, a))
    return (lhs[0] * rhs[2] - rhs[0] * lhs[2]) % _P == 0 and (lhs[1] * rhs[2] - rhs[1] * lhs[2]) % _P == 0
