"""Stark Covenant - optical_airgap: move an (encrypted) bundle between machines that share NO network,
as a sequence of QR codes shown full-screen on one screen and read by the other machine's camera.

Standard library only, Python 3.10+.

Pipeline
  bundle bytes (already encrypted, or sealed here with seal())
    -> encode_frames(): header frame 0 (name, size, SHA-256 of the whole bundle, chunk size, encoding) +
       data frames 1..N, each "GCAG1|sid|seq|total|base64|crc32" (CRC32 over everything before the last '|')
    -> sender_html(): one self-contained HTML page (no network, no external scripts) that cycles the frames
       full-screen (Fullscreen API), adjustable speed, header repeated every 8 frames, and a "resend only
       these" box for the chunk numbers the receiver reports missing.
    -> receiver_html(): page that reads frames from the camera (getUserMedia) or from dropped images, with
       the browser BarcodeDetector where it exists and otherwise the built-in camera decoder (_QR_VISION_JS,
       run in a Web Worker made from the page's own source, or time-sliced on the main thread), verifies
       every CRC32, shows which chunks are missing, reassembles, checks the SHA-256 and offers the
       bundle for download (and decrypts a sealed bundle in the browser with WebCrypto when given the
       passphrase).
    -> Reassembler (Python): same checks for frames read any other way (e.g. decode_png on saved frames).

QR codes: pure-Python encoder (adapted from Gorgon Quill writer_app.py, generalised from versions 1-10 to
1-40 and ECC levels L / M / Q / H; byte mode; all 8 masks scored with the standard penalty rules) and a
decoder: qr_decode_matrix(correct=False) is the strict structural check of clean renders;
qr_decode_matrix(correct=True) BCH-corrects the format / version words (up to 3 bit errors each) and
Reed-Solomon-corrects every block (syndromes, Berlekamp-Massey, Chien search, Forney) up to the ISO 18004
capacity (ecc - p) / 2 codewords per block.

Camera / photo reading (scan_luminance in Python, the same algorithm line for line in _QR_VISION_JS):
integral-image adaptive threshold (3x3 mean vs local mean, three offsets) -> finder patterns by 1:1:3:1:1
run ratios with vertical / horizontal / diagonal cross-checks -> best finder triple -> module size along
the finder axes -> grid size (+ BCH version information) -> grid tracking over the alignment patterns with
a least-squares homography (fallback: hill climbing on the known function modules) -> sampling against the
local mean -> correcting decoder, also transposed for mirrored images. decode_png uses it for photos.

seal(): AES-256-GCM with a key from PBKDF2-HMAC-SHA256 (600 000 iterations, random 16-byte salt, random
96-bit nonce; header bound as GCM additional data). The cipher is the OS one through security_vault
(Windows CNG / OpenSSL) - this module contains no home-made cipher; without a backend seal() refuses.
The same format is decrypted by the receiver page with WebCrypto.

Honest limits (measured with tests/test_qr_vision.py --measure on synthetic camera images)
  * One QR code per frame; byte mode only (what encode_frames produces); no lens-distortion model (a single
    homography per code, refined with every alignment pattern found).
  * Needs about 3+ camera pixels per module after blur; the three finder patterns must all be visible and
    the code must be inside the frame. Strong blur + noise + low contrast together still defeat it (the
    "harsh" synthetic level reads roughly half of its frames); hold the camera steadier / closer then.
  * Reed-Solomon beyond capacity is refused, not guessed; the rare RS miscorrection is caught by the frame
    CRC32 and the bundle SHA-256.
  * Throughput is roughly chunk_size * fps bytes/s (default 300 B x 5 fps = 1.5 kB/s). Big bundles take time.
  * CRC32 catches transmission errors, not an attacker: integrity against tampering comes from the SHA-256
    in the header frame (and, for sealed bundles, from the GCM tag). An attacker who can show the camera a
    whole fake transfer can replace an unsealed bundle - seal or sign what you send.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import itertools
import json
import math
import operator
import os
import re
import secrets
import struct
import sys
import unicodedata
import zlib

MAGIC = "GCAG1"
SEAL_MAGIC = b"GCAG-SEAL1"
SEAL_ITERATIONS = 600_000
DEFAULT_CHUNK = 300

__all__ = [
    "MAGIC", "encode_frames", "parse_frame", "Reassembler", "IntegrityError", "seal", "unseal",
    "qr_encode", "qr_decode_matrix", "png_from_matrix", "matrix_from_png", "decode_png", "write_frame_pngs",
    "sender_html", "receiver_html", "compress_ranges", "expand_ranges", "rs_correct", "rs_capacity",
    "bch_format_decode", "bch_version_decode", "qr_version_info", "scan_luminance", "luminance_from_png",
]


class IntegrityError(ValueError):
    pass


# ======================================================================================================
# 1. QR code: byte mode, ECC L / M, versions 1-40
# ======================================================================================================

_ECC_PER_BLOCK = {
    "L": [7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28, 28, 28, 30, 30, 26, 28, 30,
          30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
    "M": [10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28, 28,
          28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28],
}
_NUM_BLOCKS = {
    "L": [1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8, 8, 9, 9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18,
          19, 19, 20, 21, 22, 24, 25],
    "M": [1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31,
          33, 35, 37, 38, 40, 43, 45, 47, 49],
}
_ECC_PER_BLOCK["Q"] = [13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30, 28, 30, 30, 30,
                       30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30]
_ECC_PER_BLOCK["H"] = [17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28, 30, 24, 30, 30,
                       30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30]
_NUM_BLOCKS["Q"] = [1, 1, 2, 2, 4, 4, 6, 6, 8, 8, 8, 10, 12, 16, 12, 17, 16, 18, 21, 20, 23, 23, 25, 27, 29, 34, 34, 35,
                    38, 40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68]
_NUM_BLOCKS["H"] = [1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25, 25, 34, 30, 32, 35, 37, 40, 42,
                    45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81]
_ECL_BITS = {"L": 1, "M": 0, "Q": 3, "H": 2}
# ISO/IEC 18004 table 9 "p": misdecode-protection codewords that the decoder must NOT spend on correction
_MISDECODE_P = {(1, "L"): 3, (1, "M"): 2, (1, "Q"): 1, (1, "H"): 1, (2, "L"): 2, (3, "L"): 1}

_GF_EXP = [0] * 512
_GF_LOG = [0] * 256
_x = 1
for _i in range(255):
    _GF_EXP[_i] = _x
    _GF_LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _GF_EXP[_i] = _GF_EXP[_i - 255]


def _gf_mul(a, b):
    return 0 if a == 0 or b == 0 else _GF_EXP[_GF_LOG[a] + _GF_LOG[b]]


_GEN_CACHE = {}


def _rs_generator(n):
    g = _GEN_CACHE.get(n)
    if g is None:
        g = [1]
        for i in range(n):
            ng = [0] * (len(g) + 1)
            for j, c in enumerate(g):
                ng[j] ^= c
                ng[j + 1] ^= _gf_mul(c, _GF_EXP[i])
            g = ng
        _GEN_CACHE[n] = g
    return g


def rs_remainder(data, n):
    gen = _rs_generator(n)
    res = list(data) + [0] * n
    for i in range(len(data)):
        c = res[i]
        if c:
            lc = _GF_LOG[c]
            for j in range(1, len(gen)):
                gj = gen[j]
                if gj:
                    res[i + j] ^= _GF_EXP[lc + _GF_LOG[gj]]
    return res[len(data):]


def rs_syndromes_ok(block, n):
    for i in range(n):
        s = 0
        for byte in block:
            s = _gf_mul(s, _GF_EXP[i]) ^ byte
        if s:
            return False
    return True


def raw_codewords(v):
    r = (16 * v + 128) * v + 64
    if v >= 2:
        na = v // 7 + 2
        r -= (25 * na - 10) * na - 55
        if v >= 7:
            r -= 36
    return r // 8


def block_layout(v, ecl):
    """-> (ecc codewords per block, [(count, data codewords per block), ...]) as in ISO/IEC 18004 table 9."""
    ec = _ECC_PER_BLOCK[ecl][v - 1]
    nb = _NUM_BLOCKS[ecl][v - 1]
    total = raw_codewords(v)
    short = nb - total % nb
    short_len = total // nb
    groups = [(short, short_len - ec)]
    if nb - short:
        groups.append((nb - short, short_len + 1 - ec))
    return ec, groups


def data_capacity(v, ecl):
    ec, groups = block_layout(v, ecl)
    return sum(c * d for c, d in groups)


def alignment_positions(v):
    if v == 1:
        return []
    na = v // 7 + 2
    size = 17 + 4 * v
    step = 26 if v == 32 else (v * 4 + na * 2 + 1) // (na * 2 - 2) * 2
    out = [6]
    for k in range(na - 1):
        out.insert(1, size - 7 - k * step)
    return out


def _count_bits(v):
    return 8 if v < 10 else 16


def qr_version_for(nbytes, ecl="M", min_version=1):
    for v in range(min_version, 41):
        if 4 + _count_bits(v) + 8 * nbytes <= data_capacity(v, ecl) * 8:
            return v
    raise ValueError("data too long for a QR code at ECC %s (%d bytes)" % (ecl, nbytes))


def qr_format_bits(ecl, mask):
    data = (_ECL_BITS[ecl] << 3) | mask
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    return ((data << 10) | (rem & 0x3FF)) ^ 0x5412


def qr_version_bits(v):
    rem = v
    for _ in range(12):
        rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
    return (v << 12) | (rem & 0xFFF)


def _format_positions(size):
    c1 = [(8, i) for i in range(6)] + [(8, 7), (8, 8), (7, 8)] + [(14 - i, 8) for i in range(9, 15)]
    c2 = [(size - 1 - i, 8) for i in range(8)] + [(8, size - 15 + i) for i in range(8, 15)]
    return c1, c2


def _draw_format(mod, fn, ecl, mask):
    size = len(mod)
    bits = qr_format_bits(ecl, mask)
    c1, c2 = _format_positions(size)
    for i in range(15):
        dark = (bits >> i) & 1 == 1
        for (x, y) in (c1[i], c2[i]):
            mod[y][x] = dark
            fn[y][x] = True
    mod[size - 8][8] = True
    fn[size - 8][8] = True


_FN_CACHE = {}


def _function_map(v):
    if v in _FN_CACHE:
        m, f = _FN_CACHE[v]
        return [r[:] for r in m], f
    size = 17 + 4 * v
    mod = [[False] * size for _ in range(size)]
    fn = [[False] * size for _ in range(size)]

    def setf(x, y, dark):
        mod[y][x] = dark
        fn[y][x] = True

    for i in range(size):
        setf(6, i, i % 2 == 0)
        setf(i, 6, i % 2 == 0)
    for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                x, y = cx + dx, cy + dy
                if 0 <= x < size and 0 <= y < size:
                    setf(x, y, max(abs(dx), abs(dy)) not in (2, 4))
    al = alignment_positions(v)
    n = len(al)
    for a in range(n):
        for b in range(n):
            if (a == 0 and b == 0) or (a == 0 and b == n - 1) or (a == n - 1 and b == 0):
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    setf(al[a] + dx, al[b] + dy, max(abs(dx), abs(dy)) != 1)
    _draw_format(mod, fn, "M", 0)
    if v >= 7:
        bits = qr_version_bits(v)
        for i in range(18):
            dark = (bits >> i) & 1 == 1
            a, b = size - 11 + i % 3, i // 3
            setf(a, b, dark)
            setf(b, a, dark)
    _FN_CACHE[v] = ([r[:] for r in mod], fn)
    return mod, fn


def _zigzag(size, fn):
    right = size - 1
    while right >= 1:
        if right == 6:
            right = 5
        upward = ((right + 1) & 2) == 0
        for vert in range(size):
            y = size - 1 - vert if upward else vert
            for j in range(2):
                x = right - j
                if not fn[y][x]:
                    yield x, y
        right -= 2


def _mask_bit(m, x, y):
    if m == 0:
        return (x + y) % 2 == 0
    if m == 1:
        return y % 2 == 0
    if m == 2:
        return x % 3 == 0
    if m == 3:
        return (x + y) % 3 == 0
    if m == 4:
        return (x // 3 + y // 2) % 2 == 0
    if m == 5:
        return x * y % 2 + x * y % 3 == 0
    if m == 6:
        return (x * y % 2 + x * y % 3) % 2 == 0
    return ((x + y) % 2 + x * y % 3) % 2 == 0


_RUN = re.compile(r"0{5,}|1{5,}")
_FINDER = re.compile(r"(?=(10111010000|00001011101))")


def qr_penalty(mod):
    """ISO 18004 mask penalty (N1 runs, N2 2x2 blocks, N3 finder-like patterns, N4 dark balance)."""
    size = len(mod)
    rows = ["".join("1" if c else "0" for c in r) for r in mod]
    cols = ["".join(r[x] for r in rows) for x in range(size)]
    score = 0
    for line in rows + cols:
        for m in _RUN.finditer(line):
            score += len(m.group(0)) - 2
        score += 40 * len(_FINDER.findall(line))
    ints = [int(r, 2) for r in rows]
    mask = (1 << (size - 1)) - 1
    for a, b in zip(ints, ints[1:]):
        same_v = ~(a ^ b)
        h = ~(a ^ (a >> 1))
        block = same_v & (same_v >> 1) & h & mask
        score += 3 * bin(block).count("1")
    dark = sum(r.count("1") for r in rows)
    score += 10 * (abs(dark * 100 // (size * size) - 50) // 5)
    return score


def qr_encode(data, ecl="M", mask=None, min_version=1):
    """-> (matrix rows of bool, version, mask). data: str (UTF-8) or bytes."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    v = qr_version_for(len(data), ecl, min_version)
    ec, groups = block_layout(v, ecl)
    cap = data_capacity(v, ecl)
    bits = []

    def put(val, n):
        for i in range(n - 1, -1, -1):
            bits.append((val >> i) & 1)

    put(0b0100, 4)
    put(len(data), _count_bits(v))
    for byte in data:
        put(byte, 8)
    put(0, min(4, cap * 8 - len(bits)))
    put(0, (-len(bits)) % 8)
    cw = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(cw) < cap:
        cw.append(pad)
        pad ^= 0xEC ^ 0x11
    blocks, pos = [], 0
    for count, dlen in groups:
        for _ in range(count):
            blocks.append(cw[pos:pos + dlen])
            pos += dlen
    ecs = [rs_remainder(b, ec) for b in blocks]
    stream = []
    for i in range(max(len(b) for b in blocks)):
        for b in blocks:
            if i < len(b):
                stream.append(b[i])
    for i in range(ec):
        for e in ecs:
            stream.append(e[i])
    base, fn = _function_map(v)
    size = len(base)
    total = len(stream) * 8
    idx = 0
    cells = list(_zigzag(size, fn))
    for x, y in cells:
        if idx < total:
            base[y][x] = (stream[idx >> 3] >> (7 - (idx & 7))) & 1 == 1
        idx += 1
    best = None
    for m in ([mask] if mask is not None else range(8)):
        mod = [row[:] for row in base]
        for x, y in cells:
            if _mask_bit(m, x, y):
                mod[y][x] = not mod[y][x]
        _draw_format(mod, [r[:] for r in fn], ecl, m)
        pen = qr_penalty(mod) if mask is None else 0
        if best is None or pen < best[0]:
            best = (pen, mod, m)
    return best[1], v, best[2]


def _gf_div(a, b):
    if b == 0:
        raise ZeroDivisionError("GF(256) division by zero")
    return 0 if a == 0 else _GF_EXP[(_GF_LOG[a] - _GF_LOG[b]) % 255]


def _poly_eval_low(p, x):
    """p[0] + p[1] x + p[2] x^2 + ... over GF(256) (Horner from the top)."""
    y = 0
    for c in reversed(p):
        y = _gf_mul(y, x) ^ c
    return y


def rs_correct(block, n, max_errors=None):
    """Reed-Solomon decoder for one QR block (data codewords then n ECC codewords, first = highest power).
    Syndromes -> Berlekamp-Massey error locator -> Chien search -> Forney magnitudes (generator roots
    alpha^0..alpha^(n-1), as rs_remainder uses). Corrects up to max_errors symbol errors (default n // 2).
    -> (corrected codewords as list, number of errors fixed). Raises ValueError when the block is beyond
    repair (more errors than the code can locate, or a locator that does not split into in-range roots)."""
    if max_errors is None:
        max_errors = n // 2
    blk = list(block)
    ln = len(blk)
    if ln > 255 or ln <= n:
        raise ValueError("bad Reed-Solomon block length")
    synd = []
    for i in range(n):
        x = _GF_EXP[i]
        s = 0
        for byte in blk:
            s = _gf_mul(s, x) ^ byte
        synd.append(s)
    if not any(synd):
        return blk, 0
    # Berlekamp-Massey (Massey 1969): C = error locator, low coefficient first
    C, B = [1], [1]
    L, m, bb = 0, 1, 1
    for k in range(n):
        d = synd[k]
        for i in range(1, min(L, len(C) - 1) + 1):
            d ^= _gf_mul(C[i], synd[k - i])
        if d == 0:
            m += 1
            continue
        coef = _gf_div(d, bb)
        T = C[:]
        if len(C) < len(B) + m:
            C = C + [0] * (len(B) + m - len(C))
        for i, bi in enumerate(B):
            C[i + m] ^= _gf_mul(coef, bi)
        if 2 * L <= k:
            L, B, bb, m = k + 1 - L, T, d, 1
        else:
            m += 1
    C = (C + [0] * (L + 1))[:L + 1]
    if L == 0 or C[L] == 0 or 2 * L > n or L > max_errors:
        raise ValueError("Reed-Solomon: too many errors (%d located, %d correctable)" % (L, max_errors))
    # Chien search: codeword k sits at power e = ln-1-k, error locator X = alpha^e, root of C at X^-1
    pos = [k for k in range(ln) if _poly_eval_low(C, _GF_EXP[(255 - (ln - 1 - k)) % 255]) == 0]
    if len(pos) != L:
        raise ValueError("Reed-Solomon: locator has %d roots in the block, expected %d" % (len(pos), L))
    # Forney: Omega = S(x) C(x) mod x^n; first root alpha^0 -> magnitude = X * Omega(X^-1) / C'(X^-1)
    omega = [0] * n
    for i in range(n):
        acc = 0
        for j in range(min(i, L) + 1):
            acc ^= _gf_mul(synd[i - j], C[j])
        omega[i] = acc
    for k in pos:
        e = ln - 1 - k
        li = (255 - e) % 255                     # log of X^-1
        den = 0
        for i in range(1, L + 1, 2):             # formal derivative: only odd powers survive in GF(2^8)
            if C[i]:
                den ^= _gf_mul(C[i], _GF_EXP[(li * (i - 1)) % 255])
        if den == 0:
            raise ValueError("Reed-Solomon: Forney denominator is zero")
        blk[k] ^= _gf_mul(_GF_EXP[e], _gf_div(_poly_eval_low(omega, _GF_EXP[li]), den))
    if not rs_syndromes_ok(blk, n):
        raise ValueError("Reed-Solomon: correction did not produce a codeword")
    return blk, L


def rs_capacity(v, ecl):
    """Symbol errors per block the decoder corrects (ISO 18004 table 9: (ecc codewords - p) // 2)."""
    return (_ECC_PER_BLOCK[ecl][v - 1] - _MISDECODE_P.get((v, ecl), 0)) // 2


_FORMAT_TABLE = []


def bch_format_decode(word):
    """15-bit format word as read (mask 0x5412 still applied) -> ((ecl, mask), bit errors corrected), or None
    when more than 3 bits are wrong (BCH(15,5), minimum distance 7)."""
    if not _FORMAT_TABLE:
        _FORMAT_TABLE.extend((qr_format_bits(e, m), (e, m)) for e in ("L", "M", "Q", "H") for m in range(8))
    best, dist = None, 99
    for code, hit in _FORMAT_TABLE:
        d = bin(code ^ word).count("1")
        if d < dist:
            best, dist = hit, d
    return (best, dist) if dist <= 3 else None


def bch_version_decode(word):
    """18-bit version word -> (version 7..40, bit errors corrected), or None (BCH(18,6), distance 8)."""
    best, dist = None, 99
    for v in range(7, 41):
        d = bin(qr_version_bits(v) ^ word).count("1")
        if d < dist:
            best, dist = v, d
    return (best, dist) if dist <= 3 else None


def _read_version_words(mod):
    size = len(mod)
    a = b = 0
    for i in range(18):
        if mod[i // 3][size - 11 + i % 3]:
            a |= 1 << i
        if mod[size - 11 + i % 3][i // 3]:
            b |= 1 << i
    return a, b


def qr_version_info(mod):
    """Version from the two version-information blocks of a sampled matrix (v7+), BCH-corrected; None if
    neither block is readable. Lets a camera decoder fix a wrong grid-size estimate."""
    best = None
    for w in _read_version_words(mod):
        hit = bch_version_decode(w)
        if hit and (best is None or hit[1] < best[1]):
            best = hit
    return best[0] if best else None


def qr_decode_matrix(mod, correct=False):
    """Decode a QR module matrix (byte mode, ECC L/M/Q/H). -> {"version", "ecl", "mask", "data": bytes,
    "corrected": codewords repaired, "format_errors": format bits repaired}. Raises ValueError.

    correct=False (default): strict check of a clean render - finder and timing patterns intact, format and
    version words exact, and a Reed-Solomon syndrome error is reported, not repaired.
    correct=True (camera / photo input): finder and timing patterns are not required, format and version
    words are BCH-corrected (up to 3 bit errors each), and every block is Reed-Solomon corrected up to its
    capacity (Berlekamp-Massey, Chien search, Forney)."""
    size = len(mod)
    v = (size - 17) // 4
    if size != 17 + 4 * v or not 1 <= v <= 40 or any(len(r) != size for r in mod):
        raise ValueError("bad QR matrix size %d" % size)
    c1, c2 = _format_positions(size)
    f1 = sum(1 << i for i in range(15) if mod[c1[i][1]][c1[i][0]])
    f2 = sum(1 << i for i in range(15) if mod[c2[i][1]][c2[i][0]])
    ferr = 0
    if not correct:
        for ox, oy in ((0, 0), (size - 7, 0), (0, size - 7)):
            for dy in range(7):
                for dx in range(7):
                    if mod[oy + dy][ox + dx] != (max(abs(dx - 3), abs(dy - 3)) != 2):
                        raise ValueError("finder pattern broken")
        for i in range(8, size - 8):
            if mod[6][i] != (i % 2 == 0) or mod[i][6] != (i % 2 == 0):
                raise ValueError("timing pattern broken")
        valid = {qr_format_bits(e, m): (e, m) for e in ("L", "M", "Q", "H") for m in range(8)}
        hit = valid.get(f1) or valid.get(f2)
        if hit is None:
            raise ValueError("format information unreadable")
        ecl, mask = hit
        if v >= 7 and _read_version_words(mod)[0] != qr_version_bits(v):
            raise ValueError("version information mismatch")
    else:
        cands = [h for h in (bch_format_decode(f1), bch_format_decode(f2)) if h]
        if not cands:
            raise ValueError("format information unreadable (more than 3 bit errors in both copies)")
        (ecl, mask), ferr = min(cands, key=lambda h: h[1])
        if v >= 7:
            vi = qr_version_info(mod)
            if vi is not None and vi != v:
                raise ValueError("version information says %d, grid is %d" % (vi, v))
    _, fn = _function_map(v)
    ec, groups = block_layout(v, ecl)
    total = data_capacity(v, ecl) + ec * sum(c for c, _ in groups)
    bits = [mod[y][x] ^ _mask_bit(mask, x, y) for x, y in _zigzag(size, fn)]
    cws = [int("".join("1" if b else "0" for b in bits[i * 8:i * 8 + 8]), 2) for i in range(total)]
    lens = [d for c, d in groups for _ in range(c)]
    blocks = [[] for _ in lens]
    p = 0
    for i in range(max(lens)):
        for bi, ln in enumerate(lens):
            if i < ln:
                blocks[bi].append(cws[p])
                p += 1
    for i in range(ec):
        for bi in range(len(lens)):
            blocks[bi].append(cws[p])
            p += 1
    fixed = 0
    cap = rs_capacity(v, ecl)
    for bi, blk in enumerate(blocks):
        if not correct:
            if not rs_syndromes_ok(blk, ec):
                raise ValueError("Reed-Solomon check failed in block %d (damaged code; strict mode)" % bi)
            continue
        try:
            blocks[bi], nf = rs_correct(blk, ec, cap)
        except ValueError as e:
            raise ValueError("block %d of %d: %s" % (bi + 1, len(blocks), e))
        fixed += nf
    stream = [b for bi, blk in enumerate(blocks) for b in blk[:lens[bi]]]
    sbits = "".join(format(b, "08b") for b in stream)
    if sbits[:4] != "0100":
        raise ValueError("not a byte-mode QR code")
    cc = _count_bits(v)
    n = int(sbits[4:4 + cc], 2)
    start = 4 + cc
    if start + 8 * n > len(sbits):
        raise ValueError("byte count exceeds capacity")
    payload = bytes(int(sbits[start + 8 * i:start + 8 * i + 8], 2) for i in range(n))
    return {"version": v, "ecl": ecl, "mask": mask, "data": payload, "corrected": fixed, "format_errors": ferr}


# ======================================================================================================
# 2. PNG write / read (clean renders)
# ======================================================================================================

def _png_chunk(tag, data):
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def png_from_matrix(mod, scale=8, border=4):
    """1-bit grayscale PNG of a QR matrix (dark = black)."""
    n = len(mod)
    w = (n + 2 * border) * scale
    rows = bytearray()
    blank = [True] * (n + 2 * border)
    for y in range(-border, n + border):
        line = blank if y < 0 or y >= n else [True] * border + [not c for c in mod[y]] + [True] * border
        px = []
        for white in line:
            px += [1 if white else 0] * scale
        packed = bytearray()
        for k in range(0, w, 8):
            byte = 0
            for j in range(8):
                byte = (byte << 1) | (px[k + j] if k + j < w else 1)
            packed.append(byte)
        for _ in range(scale):
            rows += b"\x00" + packed
    ihdr = struct.pack(">IIBBBBB", w, w, 1, 0, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", zlib.compress(bytes(rows), 9)) \
        + _png_chunk(b"IEND", b"")


def _png_pixels(data):
    """-> (width, height, luminance rows as list of bytearray). Non-interlaced PNG, any colour type."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG file")
    p = 8
    ihdr = None
    idat = bytearray()
    palette = None
    while p < len(data):
        ln = struct.unpack(">I", data[p:p + 4])[0]
        tag = data[p + 4:p + 8]
        body = data[p + 8:p + 8 + ln]
        crc = struct.unpack(">I", data[p + 8 + ln:p + 12 + ln])[0]
        if zlib.crc32(tag + body) & 0xFFFFFFFF != crc:
            raise ValueError("PNG chunk %r CRC mismatch" % tag)
        if tag == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", body)
        elif tag == b"PLTE":
            palette = body
        elif tag == b"IDAT":
            idat += body
        elif tag == b"IEND":
            break
        p += 12 + ln
    if ihdr is None:
        raise ValueError("PNG without IHDR")
    w, h, depth, ctype, _comp, _filt, interlace = ihdr
    if interlace:
        raise ValueError("interlaced PNG not supported")
    chans = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    bits_pp = chans * depth
    stride = (w * bits_pp + 7) // 8
    bpp = max(1, bits_pp // 8)
    raw = zlib.decompress(bytes(idat))
    prev = bytearray(stride)
    out = []
    q = 0
    for _y in range(h):
        f = raw[q]
        line = bytearray(raw[q + 1:q + 1 + stride])
        q += 1 + stride
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if f == 1:
                line[i] = (line[i] + a) & 255
            elif f == 2:
                line[i] = (line[i] + b) & 255
            elif f == 3:
                line[i] = (line[i] + ((a + b) >> 1)) & 255
            elif f == 4:
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if pa <= pb and pa <= pc else b if pb <= pc else c
                line[i] = (line[i] + pr) & 255
        prev = line
        lum = bytearray(w)
        if depth < 8:
            maxv = (1 << depth) - 1
            for x in range(w):
                bit = x * depth
                val = (line[bit >> 3] >> (8 - depth - (bit & 7))) & maxv
                if ctype == 3:
                    r, g, bl = palette[3 * val:3 * val + 3]
                    lum[x] = (299 * r + 587 * g + 114 * bl) // 1000
                else:
                    lum[x] = val * 255 // maxv
        else:
            step = chans * (depth // 8)
            for x in range(w):
                o = x * step
                if ctype in (0, 4):
                    lum[x] = line[o]
                elif ctype == 3:
                    r, g, bl = palette[3 * line[o]:3 * line[o] + 3]
                    lum[x] = (299 * r + 587 * g + 114 * bl) // 1000
                else:
                    d = depth // 8
                    r, g, bl = line[o], line[o + d], line[o + 2 * d]
                    lum[x] = (299 * r + 587 * g + 114 * bl) // 1000
                if ctype in (4, 6):  # composite on white
                    alpha = line[o + (chans - 1) * (depth // 8)]
                    lum[x] = (lum[x] * alpha + 255 * (255 - alpha)) // 255
        out.append(lum)
    return w, h, out


def _matrix_from_pixels(w, h, px):
    lo = min(min(r) for r in px)
    hi = max(max(r) for r in px)
    if hi - lo < 64:
        raise ValueError("image has no contrast")
    thr = (lo + hi) // 2
    dark = [[v < thr for v in r] for r in px]
    top = next((y for y in range(h) if any(dark[y])), None)
    if top is None:
        raise ValueError("no dark pixels")
    bottom = max(y for y in range(h) if any(dark[y]))
    left = min(r.index(True) for r in dark if True in r)
    right = max(w - 1 - r[::-1].index(True) for r in dark if True in r)
    run = 0
    row = dark[top]
    x = left
    while x <= right and row[x]:
        run += 1
        x += 1
    if run < 7:
        raise ValueError("finder pattern not found at the top-left")
    mpx = run / 7.0
    n = int(round((right - left + 1) / mpx))
    v = int(round((n - 17) / 4.0))
    n = 17 + 4 * v
    if not 1 <= v <= 40:
        raise ValueError("cannot determine QR version from the image")
    sx = (right - left + 1) / float(n)
    sy = (bottom - top + 1) / float(n)
    mod = []
    for j in range(n):
        yy = int(top + (j + 0.5) * sy)
        mod.append([dark[yy][int(left + (i + 0.5) * sx)] for i in range(n)])
    return mod


def matrix_from_png(data):
    """QR module matrix from a clean, axis-aligned render (any scale, any quiet zone)."""
    w, h, px = _png_pixels(data)
    return _matrix_from_pixels(w, h, px)


def luminance_from_png(data):
    """-> (width, height, luminance bytes, row-major) for any non-interlaced PNG."""
    w, h, px = _png_pixels(data)
    return w, h, b"".join(bytes(r) for r in px)


def decode_png(data):
    """-> frame text from a PNG of one QR frame: a clean render (fast path) or a photo / camera still
    (computer-vision locator + Reed-Solomon correction, see scan_luminance)."""
    w, h, px = _png_pixels(data)
    try:
        return qr_decode_matrix(_matrix_from_pixels(w, h, px), correct=True)["data"].decode("ascii")
    except ValueError:
        pass
    hit = scan_luminance(w, h, b"".join(bytes(r) for r in px))
    if hit is None:
        raise ValueError("no readable QR code in the image")
    return hit["data"].decode("ascii")


# ======================================================================================================
# 2b. Computer vision: find and read a QR code in a camera frame or photo.
#     The receiver page runs a line-for-line JavaScript port of these functions (_QR_VISION_JS), so the two
#     decoders give the same answer on the same pixels (tests/test_qr_vision.py checks that).
#     adaptive threshold (integral-image local mean) -> finder patterns by 1:1:3:1:1 run ratios with
#     vertical / horizontal / diagonal cross-checks -> best finder triple -> module size along the finder
#     axes -> grid size (+ version-information correction) -> alignment-pattern search (v2+) -> homography
#     from 4 point pairs -> grid sampling against the local mean -> qr_decode_matrix(correct=True), and the
#     transposed grid for mirrored images.
# ======================================================================================================

_QV_OFFSETS = (6, 14, 0)   # binarizer passes: dark when the 3x3 mean is this many % below the local mean
_RUNS_RX = re.compile(rb"\x01+|\x00+")


def _qv_integral(lum, w, h):
    zero = [0] * (w + 1)
    table = [zero]
    prev = zero
    for y in range(h):
        acc = [0]
        acc.extend(itertools.accumulate(lum[y * w:(y + 1) * w]))
        prev = list(map(operator.add, prev, acc))
        table.append(prev)
    return table


def _qv_radius(w, h):
    return max(8, max(w, h) // 8)


def _qv_binarize(lum, w, h, table, r, offset):
    """Dark where the 3x3 mean is more than offset % below the (2r+1)^2 local mean (both from the integral
    image, so the smoothing that tames sensor noise costs nothing extra)."""
    xa = [max(0, x - r) for x in range(w)]
    xb = [min(w, x + r + 1) for x in range(w)]
    sa = [max(0, x - 1) for x in range(w)]
    sb = [min(w, x + 2) for x in range(w)]
    wx = [b - a for a, b in zip(xa, xb)]
    bx = [b - a for a, b in zip(sa, sb)]
    ga, gb = operator.itemgetter(*xa), operator.itemgetter(*xb)
    ha, hb = operator.itemgetter(*sa), operator.itemgetter(*sb)
    k = 100 - offset
    mul, sub, rep = operator.mul, operator.sub, itertools.repeat
    rows = []
    for y in range(h):
        ya, yb = max(0, y - r), min(h, y + r + 1)
        ta, tb = max(0, y - 1), min(h, y + 2)
        col = list(map(sub, table[yb], table[ya]))
        sml = list(map(sub, table[tb], table[ta]))
        win = map(sub, gb(col), ga(col))               # window sums
        box = map(sub, hb(sml), ha(sml))               # 3x3 sums
        # box * win_area * 100 < win * box_area * k
        lhs = map(mul, map(mul, box, wx), rep((yb - ya) * 100))
        rhs = map(mul, map(mul, win, bx), rep((tb - ta) * k))
        rows.append(bytearray(map(operator.lt, lhs, rhs)))
    return rows


def _qv_ratio(c0, c1, c2, c3, c4, tol):
    total = c0 + c1 + c2 + c3 + c4
    if total < 7:
        return False
    m = total / 7.0
    v = m * tol
    return (abs(m - c0) < v and abs(m - c1) < v and abs(3.0 * m - c2) < 3.0 * v and abs(m - c3) < v
            and abs(m - c4) < v)


def _qv_cross(B, w, h, x, y, dx, dy, max_count, orig_total):
    """Runs dark/light/dark both ways from pixel (x, y) along (dx, dy) -> (centre offset, total) or None."""
    c0 = c1 = c2 = c3 = c4 = 0
    i, j = x, y
    while 0 <= i < w and 0 <= j < h and B[j][i]:
        c2 += 1
        i -= dx
        j -= dy
    if not (0 <= i < w and 0 <= j < h):
        return None
    while 0 <= i < w and 0 <= j < h and not B[j][i] and c1 <= max_count:
        c1 += 1
        i -= dx
        j -= dy
    if not (0 <= i < w and 0 <= j < h) or c1 > max_count:
        return None
    while 0 <= i < w and 0 <= j < h and B[j][i] and c0 <= max_count:
        c0 += 1
        i -= dx
        j -= dy
    if c0 > max_count:
        return None
    i, j = x + dx, y + dy
    f2 = 0
    while 0 <= i < w and 0 <= j < h and B[j][i]:
        f2 += 1
        i += dx
        j += dy
    if not (0 <= i < w and 0 <= j < h):
        return None
    while 0 <= i < w and 0 <= j < h and not B[j][i] and c3 < max_count:
        c3 += 1
        i += dx
        j += dy
    if not (0 <= i < w and 0 <= j < h) or c3 >= max_count:
        return None
    while 0 <= i < w and 0 <= j < h and B[j][i] and c4 < max_count:
        c4 += 1
        i += dx
        j += dy
    if c4 >= max_count:
        return None
    c2 += f2
    total = c0 + c1 + c2 + c3 + c4
    if 5 * abs(total - orig_total) >= 2 * orig_total:
        return None
    if not _qv_ratio(c0, c1, c2, c3, c4, 0.5):
        return None
    return 1.0 + f2 - c2 / 2.0, total


def _qv_diagonal(B, w, h, x, y):
    c = [0, 0, 0, 0, 0]
    k = 0
    while x - k >= 0 and y - k >= 0 and B[y - k][x - k]:
        c[2] += 1
        k += 1
    if x - k < 0 or y - k < 0:
        return False
    while x - k >= 0 and y - k >= 0 and not B[y - k][x - k]:
        c[1] += 1
        k += 1
    if x - k < 0 or y - k < 0:
        return False
    while x - k >= 0 and y - k >= 0 and B[y - k][x - k]:
        c[0] += 1
        k += 1
    k = 1
    while x + k < w and y + k < h and B[y + k][x + k]:
        c[2] += 1
        k += 1
    if x + k >= w or y + k >= h:
        return False
    while x + k < w and y + k < h and not B[y + k][x + k]:
        c[3] += 1
        k += 1
    if x + k >= w or y + k >= h:
        return False
    while x + k < w and y + k < h and B[y + k][x + k]:
        c[4] += 1
        k += 1
    return _qv_ratio(c[0], c[1], c[2], c[3], c[4], 0.75)


def _qv_finders(B, w, h):
    """Finder-pattern candidates [x, y, module size, hits] (continuous pixel coordinates)."""
    cands = []
    step = 1 if h <= 1000 else 2
    for y in range(0, h, step):
        row = B[y]
        spans = [m.span() for m in _RUNS_RX.finditer(row)]
        n = len(spans)
        k = 0 if row[0] else 1
        while k + 4 < n:
            s0, e0 = spans[k]
            s2, e2 = spans[k + 2]
            c0 = e0 - s0
            c1 = spans[k + 1][1] - spans[k + 1][0]
            c2 = e2 - s2
            c3 = spans[k + 3][1] - spans[k + 3][0]
            c4 = spans[k + 4][1] - spans[k + 4][0]
            if _qv_ratio(c0, c1, c2, c3, c4, 0.5):
                total = c0 + c1 + c2 + c3 + c4
                ix = int(s2 + c2 / 2.0)
                vr = _qv_cross(B, w, h, ix, y, 0, 1, c2, total)
                if vr is not None:
                    cy = y + vr[0]
                    iy = int(cy)
                    hr = _qv_cross(B, w, h, ix, iy, 1, 0, c2, total)
                    if hr is not None and _qv_diagonal(B, w, h, int(ix + hr[0]), iy):
                        cx = ix + hr[0]
                        ms = (hr[1] + vr[1]) / 14.0
                        for c in cands:
                            if abs(cy - c[1]) <= ms and abs(cx - c[0]) <= ms and (
                                    abs(ms - c[2]) <= 1.0 or abs(ms - c[2]) <= c[2]):
                                cnt = c[3]
                                c[0] = (cnt * c[0] + cx) / (cnt + 1)
                                c[1] = (cnt * c[1] + cy) / (cnt + 1)
                                c[2] = (cnt * c[2] + ms) / (cnt + 1)
                                c[3] = cnt + 1
                                break
                        else:
                            cands.append([cx, cy, ms, 1])
            k += 2
    return cands


def _qv_triples(cands):
    """Finder triples ordered (top-left, top-right, bottom-left), best geometry first."""
    pool = sorted(cands, key=lambda c: -c[3])[:12]
    out = []
    n = len(pool)
    for a in range(n):
        for b in range(a + 1, n):
            for c in range(b + 1, n):
                p, q, r = pool[a], pool[b], pool[c]
                msmax = max(p[2], q[2], r[2])
                msmin = min(p[2], q[2], r[2])
                if msmax > 1.8 * msmin:
                    continue
                dpq = (p[0] - q[0]) * (p[0] - q[0]) + (p[1] - q[1]) * (p[1] - q[1])
                dqr = (q[0] - r[0]) * (q[0] - r[0]) + (q[1] - r[1]) * (q[1] - r[1])
                dpr = (p[0] - r[0]) * (p[0] - r[0]) + (p[1] - r[1]) * (p[1] - r[1])
                if dqr >= dpq and dqr >= dpr:
                    tl, o1, o2, hyp, l1, l2 = p, q, r, dqr, dpq, dpr
                elif dpr >= dpq:
                    tl, o1, o2, hyp, l1, l2 = q, p, r, dpr, dpq, dqr
                else:
                    tl, o1, o2, hyp, l1, l2 = r, p, q, dpq, dpr, dqr
                ms = (p[2] + q[2] + r[2]) / 3.0
                lo, hi = min(l1, l2), max(l1, l2)
                if lo < (10.0 * ms) * (10.0 * ms) or hi > 2.6 * lo or hi > (180.0 * ms) * (180.0 * ms):
                    continue
                pyth = abs(hyp - (l1 + l2)) / hyp
                if pyth > 0.45:
                    continue
                cross = (o1[0] - tl[0]) * (o2[1] - tl[1]) - (o1[1] - tl[1]) * (o2[0] - tl[0])
                tr, bl = (o1, o2) if cross > 0 else (o2, o1)
                score = (hi / lo - 1.0) + 2.0 * pyth + (msmax / msmin - 1.0)
                out.append((score, tl, tr, bl))
    out.sort(key=lambda t: t[0])
    return [t[1:] for t in out[:4]]


def _qv_run3(B, w, h, fx, fy, tx, ty):
    """Distance from a finder centre along (fx,fy)->(tx,ty) to the far edge of its dark outer ring
    (dark centre, light ring, dark ring = 3.5 modules), or -1."""
    dx, dy = tx - fx, ty - fy
    ln = math.sqrt(dx * dx + dy * dy)
    if ln < 1.0:
        return -1.0
    ux, uy = dx / ln, dy / ln
    state = 0
    i = 0
    while i < ln:
        x = int(math.floor(fx + ux * i))
        y = int(math.floor(fy + uy * i))
        if x < 0 or y < 0 or x >= w or y >= h:
            return -1.0
        d = B[y][x]
        if state == 0:
            if not d:
                state = 1
        elif state == 1:
            if d:
                state = 2
        elif not d:
            return float(i)
        i += 1
    return -1.0


def _qv_module_one_way(B, w, h, p, q):
    a = _qv_run3(B, w, h, p[0], p[1], q[0], q[1])
    b = _qv_run3(B, w, h, p[0], p[1], 2.0 * p[0] - q[0], 2.0 * p[1] - q[1])
    c = _qv_run3(B, w, h, q[0], q[1], p[0], p[1])
    d = _qv_run3(B, w, h, q[0], q[1], 2.0 * q[0] - p[0], 2.0 * q[1] - p[1])
    s1 = (a + b) / 7.0 if a > 0 and b > 0 else -1.0
    s2 = (c + d) / 7.0 if c > 0 and d > 0 else -1.0
    if s1 > 0 and s2 > 0:
        return (s1 + s2) / 2.0
    return s1 if s1 > 0 else s2


def _qv_dims(B, w, h, tl, tr, bl):
    m1 = _qv_module_one_way(B, w, h, tl, tr)
    m2 = _qv_module_one_way(B, w, h, tl, bl)
    if m1 > 0 and m2 > 0:
        ms = (m1 + m2) / 2.0
    elif m1 > 0 or m2 > 0:
        ms = m1 if m1 > 0 else m2
    else:
        ms = (tl[2] + tr[2] + bl[2]) / 3.0
    d1 = math.sqrt((tr[0] - tl[0]) * (tr[0] - tl[0]) + (tr[1] - tl[1]) * (tr[1] - tl[1]))
    d2 = math.sqrt((bl[0] - tl[0]) * (bl[0] - tl[0]) + (bl[1] - tl[1]) * (bl[1] - tl[1]))
    dim = (int(math.floor(d1 / ms + 0.5)) + int(math.floor(d2 / ms + 0.5))) // 2 + 7
    r = dim % 4
    if r == 0:
        base = [dim + 1, dim - 3]
    elif r == 2:
        base = [dim - 1, dim + 3]
    elif r == 3:
        base = [dim - 2, dim + 2]
    else:
        base = [dim, dim + 4, dim - 4]
    out = []
    for d in base + [base[0] + 4, base[0] - 4, base[0] + 8, base[0] - 8]:
        if 21 <= d <= 177 and d not in out:
            out.append(d)
    return ms, out[:5]


def _qv_alignment(B, w, h, ex, ey, ms, rad_modules):
    """Alignment-pattern candidates within rad_modules of (ex, ey), nearest first (at most 4):
    light/dark/light 1:1:1 along the row with the dark outer ring beyond, confirmed on both axes."""
    rad = rad_modules * ms
    x0, x1 = max(0, int(ex - rad)), min(w - 1, int(ex + rad))
    y0, y1 = max(0, int(ey - rad)), min(h - 1, int(ey + rad))
    if x1 - x0 < 3 * ms or y1 - y0 < 3 * ms:
        return []
    found = []
    tol = ms * 0.6
    for y in range(y0, y1 + 1):
        seg = B[y][x0:x1 + 1]
        spans = [m.span() for m in _RUNS_RX.finditer(seg)]
        k = 1 if seg[0] else 2          # spans[k] is a light run with a dark run before it
        while k + 3 < len(spans):
            c1 = spans[k][1] - spans[k][0]
            c2 = spans[k + 1][1] - spans[k + 1][0]
            c3 = spans[k + 2][1] - spans[k + 2][0]
            if abs(c1 - ms) < tol and abs(c2 - ms) < tol and abs(c3 - ms) < tol:
                ix = int(x0 + spans[k + 1][0] + c2 / 2.0)
                vr = _qv_align_runs(B, w, h, ix, y, 0, 1, ms)
                if vr is not None:
                    cy = y + vr
                    hr = _qv_align_runs(B, w, h, ix, int(cy), 1, 0, ms)
                    if hr is not None:
                        cx = ix + hr
                        for f in found:
                            if abs(f[0] - cx) <= ms and abs(f[1] - cy) <= ms:
                                n = f[2]
                                f[0] = (n * f[0] + cx) / (n + 1)
                                f[1] = (n * f[1] + cy) / (n + 1)
                                f[2] = n + 1
                                break
                        else:
                            found.append([cx, cy, 1])
            k += 2
    found.sort(key=lambda f: (f[0] - ex) * (f[0] - ex) + (f[1] - ey) * (f[1] - ey))
    return [(f[0], f[1]) for f in found[:4]]


def _qv_align_runs(B, w, h, x, y, dx, dy, ms):
    """From a dark centre pixel: centre run both ways, then a light ring and a dark ring on both sides.
    -> centre offset along (dx, dy), or None."""
    lim = int(ms * 2.5) + 2
    b = 0
    i, j = x, y
    while 0 <= i < w and 0 <= j < h and B[j][i] and b <= lim:
        b += 1
        i -= dx
        j -= dy
    lb = 0
    while 0 <= i < w and 0 <= j < h and not B[j][i] and lb <= lim:
        lb += 1
        i -= dx
        j -= dy
    if not (0 <= i < w and 0 <= j < h) or not B[j][i]:
        return None
    f = 0
    i, j = x + dx, y + dy
    while 0 <= i < w and 0 <= j < h and B[j][i] and f <= lim:
        f += 1
        i += dx
        j += dy
    lf = 0
    while 0 <= i < w and 0 <= j < h and not B[j][i] and lf <= lim:
        lf += 1
        i += dx
        j += dy
    if not (0 <= i < w and 0 <= j < h) or not B[j][i]:
        return None
    c = b + f
    tol = ms * 0.6
    if abs(c - ms) >= tol or abs(lb - ms) >= tol or abs(lf - ms) >= tol:
        return None
    return 1.0 + f - c / 2.0


def _qv_square_to_quad(x0, y0, x1, y1, x2, y2, x3, y3):
    """3x3 homography (row-major list) mapping the unit square (0,0),(1,0),(1,1),(0,1) to the quad."""
    dx3 = x0 - x1 + x2 - x3
    dy3 = y0 - y1 + y2 - y3
    if dx3 == 0.0 and dy3 == 0.0:
        return [x1 - x0, x2 - x1, x0, y1 - y0, y2 - y1, y0, 0.0, 0.0, 1.0]
    dx1, dx2, dy1, dy2 = x1 - x2, x3 - x2, y1 - y2, y3 - y2
    den = dx1 * dy2 - dx2 * dy1
    g = (dx3 * dy2 - dx2 * dy3) / den
    hh = (dx1 * dy3 - dx3 * dy1) / den
    return [x1 - x0 + g * x1, x3 - x0 + hh * x3, x0, y1 - y0 + g * y1, y3 - y0 + hh * y3, y0, g, hh, 1.0]


def _qv_adjugate(m):
    a, b, c, d, e, f, g, h, i = m
    return [e * i - f * h, c * h - b * i, b * f - c * e,
            f * g - d * i, a * i - c * g, c * d - a * f,
            d * h - e * g, b * g - a * h, a * e - b * d]


def _qv_matmul(p, q):
    return [p[0] * q[0] + p[1] * q[3] + p[2] * q[6], p[0] * q[1] + p[1] * q[4] + p[2] * q[7],
            p[0] * q[2] + p[1] * q[5] + p[2] * q[8],
            p[3] * q[0] + p[4] * q[3] + p[5] * q[6], p[3] * q[1] + p[4] * q[4] + p[5] * q[7],
            p[3] * q[2] + p[4] * q[5] + p[5] * q[8],
            p[6] * q[0] + p[7] * q[3] + p[8] * q[6], p[6] * q[1] + p[7] * q[4] + p[8] * q[7],
            p[6] * q[2] + p[7] * q[5] + p[8] * q[8]]


def _qv_quad_to_quad(src, dst):
    """Homography mapping the 4 src points onto the 4 dst points (order TL, TR, BR, BL)."""
    s = _qv_square_to_quad(*[c for p in src for c in p])
    t = _qv_square_to_quad(*[c for p in dst for c in p])
    return _qv_matmul(t, _qv_adjugate(s))


def _qv_sample(lum, w, h, table, r, H, dim, ms):
    """dim x dim module grid: mean of a small box at each module centre vs the local mean around it."""
    s = 1 if ms >= 4.5 else 0
    mod = []
    for j in range(dim):
        v = j + 0.5
        row = []
        for i in range(dim):
            u = i + 0.5
            z = H[6] * u + H[7] * v + H[8]
            x = int(math.floor((H[0] * u + H[1] * v + H[2]) / z))
            y = int(math.floor((H[3] * u + H[4] * v + H[5]) / z))
            if x < 0 or y < 0 or x >= w or y >= h:
                row.append(False)
                continue
            if s:
                xa, xb = max(0, x - s), min(w, x + s + 1)
                ya, yb = max(0, y - s), min(h, y + s + 1)
                box = table[yb][xb] - table[ya][xb] - table[yb][xa] + table[ya][xa]
                barea = (xb - xa) * (yb - ya)
            else:
                box = lum[y * w + x]
                barea = 1
            xa, xb = max(0, x - r), min(w, x + r + 1)
            ya, yb = max(0, y - r), min(h, y + r + 1)
            win = table[yb][xb] - table[ya][xb] - table[yb][xa] + table[ya][xa]
            row.append(box * (xb - xa) * (yb - ya) < win * barea)
        mod.append(row)
    return mod


def _qv_apply(H, u, v):
    z = H[6] * u + H[7] * v + H[8]
    return (H[0] * u + H[1] * v + H[2]) / z, (H[3] * u + H[4] * v + H[5]) / z


def _qv_solve(A, b):
    """Gaussian elimination with partial pivoting (n x n). -> list or None when singular."""
    n = len(b)
    M = [A[i][:] + [b[i]] for i in range(n)]
    for c in range(n):
        p = c
        for r in range(c + 1, n):
            if abs(M[r][c]) > abs(M[p][c]):
                p = r
        if abs(M[p][c]) < 1e-12:
            return None
        if p != c:
            M[c], M[p] = M[p], M[c]
        piv = M[c][c]
        for r in range(c + 1, n):
            f = M[r][c] / piv
            if f != 0.0:
                Mr, Mc = M[r], M[c]
                for k in range(c, n + 1):
                    Mr[k] -= f * Mc[k]
    x = [0.0] * n
    for c in range(n - 1, -1, -1):
        s = M[c][n]
        for k in range(c + 1, n):
            s -= M[c][k] * x[k]
        x[c] = s / M[c][c]
    return x


def _qv_fit_lsq(pairs, dim):
    """Least-squares homography (normalised DLT, h22 = 1) from >= 4 (u, v, x, y) pairs."""
    n = len(pairs)
    cx = sum(p[2] for p in pairs) / n
    cy = sum(p[3] for p in pairs) / n
    s = sum(math.sqrt((p[2] - cx) * (p[2] - cx) + (p[3] - cy) * (p[3] - cy)) for p in pairs) / n or 1.0
    A = [[0.0] * 8 for _ in range(8)]
    b = [0.0] * 8
    for (u, v, x, y) in pairs:
        u, v, x, y = u / dim, v / dim, (x - cx) / s, (y - cy) / s
        for row, rhs in (([u, v, 1.0, 0.0, 0.0, 0.0, -u * x, -v * x], x),
                         ([0.0, 0.0, 0.0, u, v, 1.0, -u * y, -v * y], y)):
            for i in range(8):
                ri = row[i]
                if ri != 0.0:
                    Ai = A[i]
                    for j in range(8):
                        Ai[j] += ri * row[j]
                    b[i] += ri * rhs
    h = _qv_solve(A, b)
    if h is None:
        return None
    Hn = [h[0], h[1], h[2], h[3], h[4], h[5], h[6], h[7], 1.0]
    Hn = [Hn[0] / dim, Hn[1] / dim, Hn[2], Hn[3] / dim, Hn[4] / dim, Hn[5], Hn[6] / dim, Hn[7] / dim, Hn[8]]
    return _qv_matmul([s, 0.0, cx, 0.0, s, cy, 0.0, 0.0, 1.0], Hn)


def _qv_track(B, w, h, tl, tr, bl, ms, dim):
    """Grid tracking: start from the 3-finder parallelogram, then visit the alignment patterns outward
    from the top-left, predict each with the current homography, look for it within a few local module
    sizes, and refit (least squares, worst point dropped while any residual exceeds 0.75 module).
    -> (H, alignment patterns found)."""
    t = dim - 3.5
    pairs = [(3.5, 3.5, tl[0], tl[1]), (t, 3.5, tr[0], tr[1]), (3.5, t, bl[0], bl[1])]
    bx, by = tr[0] - tl[0] + bl[0], tr[1] - tl[1] + bl[1]
    H = _qv_quad_to_quad([(3.5, 3.5), (t, 3.5), (t, t), (3.5, t)],
                         [(tl[0], tl[1]), (tr[0], tr[1]), (bx, by), (bl[0], bl[1])])
    al = alignment_positions((dim - 17) // 4)
    n = len(al)
    order = []
    for a in range(n):
        for b in range(n):
            if (a == 0 and b == 0) or (a == 0 and b == n - 1) or (a == n - 1 and b == 0):
                continue
            order.append((max(a, b), a + b, al[a] + 0.5, al[b] + 0.5))
    order.sort()
    found = 0
    for _r, _s, u, v in order:
        px, py = _qv_apply(H, u, v)
        qx, qy = _qv_apply(H, u + 1.0, v)
        rx, ry = _qv_apply(H, u, v + 1.0)
        lms = (math.sqrt((qx - px) * (qx - px) + (qy - py) * (qy - py))
               + math.sqrt((rx - px) * (rx - px) + (ry - py) * (ry - py))) / 2.0
        rad = 3.5 if (found or n > 2) else min(16.0, 4.0 + dim / 8.0)
        hits = _qv_alignment(B, w, h, px, py, lms, rad)
        if not hits:
            continue
        pairs.append((u, v, hits[0][0], hits[0][1]))
        found += 1
        while len(pairs) >= 4:
            H2 = _qv_fit_lsq(pairs, dim)
            if H2 is None:
                pairs.pop()
                found -= 1
                break
            worst, wi = 0.0, -1
            for k in range(3, len(pairs)):
                u2, v2, x2, y2 = pairs[k]
                ex, ey = _qv_apply(H2, u2, v2)
                d = math.sqrt((ex - x2) * (ex - x2) + (ey - y2) * (ey - y2))
                if d > worst:
                    worst, wi = d, k
            if len(pairs) > 4 and worst > 0.75 * ms:
                pairs.pop(wi)
                found -= 1
                continue
            H = H2
            break
    return H, found


_QV_FIXED = {}


def _qv_fixed(dim):
    """Modules every symbol of this size has: finders + separators, timing, alignment patterns.
    -> flat list u0, v0, dark0, u1, ... (module-centre coordinates)."""
    pts = _QV_FIXED.get(dim)
    if pts is not None:
        return pts
    v = (dim - 17) // 4
    pts = []
    for ox, oy in ((0, 0), (dim - 7, 0), (0, dim - 7)):
        for dy in range(-1, 8):
            for dx in range(-1, 8):
                x, y = ox + dx, oy + dy
                if 0 <= x < dim and 0 <= y < dim:
                    dark = 0 <= dx <= 6 and 0 <= dy <= 6 and max(abs(dx - 3), abs(dy - 3)) != 2
                    pts.extend((x + 0.5, y + 0.5, 1 if dark else 0))
    for i in range(8, dim - 8):
        pts.extend((i + 0.5, 6.5, 1 - i % 2, 6.5, i + 0.5, 1 - i % 2))
    al = alignment_positions(v)
    n = len(al)
    for a in range(n):
        for b in range(n):
            if (a == 0 and b == 0) or (a == 0 and b == n - 1) or (a == n - 1 and b == 0):
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    pts.extend((al[a] + dx + 0.5, al[b] + dy + 0.5, 0 if max(abs(dx), abs(dy)) == 1 else 1))
    _QV_FIXED[dim] = pts
    return pts


def _qv_score(B, w, h, H, pts):
    """How many of the fixed modules the binarized image agrees with under homography H."""
    a, b, c, d, e, f, g, hh, i = H
    ok = 0
    for k in range(0, len(pts), 3):
        u = pts[k]
        v = pts[k + 1]
        z = g * u + hh * v + i
        x = int(math.floor((a * u + b * v + c) / z))
        y = int(math.floor((d * u + e * v + f) / z))
        if 0 <= x < w and 0 <= y < h and B[y][x] == pts[k + 2]:
            ok += 1
    return ok


def _qv_homography(tl, tr, bl, px, py, dim):
    t = dim - 3.5
    q = dim - 6.5
    return _qv_quad_to_quad([(3.5, 3.5), (t, 3.5), (q, q), (3.5, t)],
                            [(tl[0], tl[1]), (tr[0], tr[1]), (px, py), (bl[0], bl[1])])


_QV_DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1))


def _qv_fit(B, w, h, tl, tr, bl, ms, dim):
    """Homography for a dim x dim grid. First grid tracking over the alignment patterns (_qv_track); if
    that matches the fixed modules poorly, the image point of module (dim-6.5, dim-6.5) is searched by
    pattern-search hill climbing on _qv_score from the tracked guess, the parallelogram estimate and each
    alignment candidate near it. -> (H, share of fixed modules matched, 4th point)."""
    pts = _qv_fixed(dim)
    npts = len(pts) / 3.0
    q = dim - 6.5
    best = None
    if dim >= 25:
        Ht, _found = _qv_track(B, w, h, tl, tr, bl, ms, dim)
        st = _qv_score(B, w, h, Ht, pts)
        best = (st, Ht)
        if st >= 0.85 * npts:
            return Ht, st / npts, _qv_apply(Ht, q, q)
    k = 1.0 - 3.0 / (dim - 7.0)
    ex = tl[0] + k * (tr[0] - tl[0] + bl[0] - tl[0])
    ey = tl[1] + k * (tr[1] - tl[1] + bl[1] - tl[1])
    starts = [(ex, ey)]
    if best is not None:
        starts.insert(0, _qv_apply(best[1], q, q))
    if dim >= 25:
        starts += _qv_alignment(B, w, h, ex, ey, ms, min(16.0, 4.0 + dim / 8.0))
    for sx, sy in starts:
        px, py = sx, sy
        s = _qv_score(B, w, h, _qv_homography(tl, tr, bl, px, py, dim), pts)
        step = 2.0 * ms
        evals = 0
        while step >= 0.24 * ms and evals < 160:
            bs, bx, by = s, px, py
            for dx, dy in _QV_DIRS:
                qx, qy = px + dx * step, py + dy * step
                sc = _qv_score(B, w, h, _qv_homography(tl, tr, bl, qx, qy, dim), pts)
                evals += 1
                if sc > bs:
                    bs, bx, by = sc, qx, qy
            if bs > s:
                s, px, py = bs, bx, by
            else:
                step /= 2.0
        if best is None or s > best[0]:
            best = (s, _qv_homography(tl, tr, bl, px, py, dim))
    s, H = best
    return H, s / npts, _qv_apply(H, q, q)


def _qv_quick_score(B, w, h, tl, tr, bl, dim):
    k = 1.0 - 3.0 / (dim - 7.0)
    ex = tl[0] + k * (tr[0] - tl[0] + bl[0] - tl[0])
    ey = tl[1] + k * (tr[1] - tl[1] + bl[1] - tl[1])
    pts = _qv_fixed(dim)
    return _qv_score(B, w, h, _qv_homography(tl, tr, bl, ex, ey, dim), pts) / (len(pts) / 3.0)


def _qv_try_grid(lum, w, h, table, r, B, tl, tr, bl, ms, dim):
    """-> (decode result or None, version read from the grid or None)."""
    H, frac, p4 = _qv_fit(B, w, h, tl, tr, bl, ms, dim)
    if frac < 0.6:
        return None, None
    mod = _qv_sample(lum, w, h, table, r, H, dim, ms)
    grids = [mod, [list(c) for c in zip(*mod)]]
    alt = []
    for j in range(dim):
        row = []
        for i in range(dim):
            x, y = _qv_apply(H, i + 0.5, j + 0.5)
            x, y = int(math.floor(x)), int(math.floor(y))
            row.append(0 <= x < w and 0 <= y < h and B[y][x] == 1)
        alt.append(row)
    grids += [alt, [list(c) for c in zip(*alt)]]
    seen = None
    for grid in grids:
        try:
            res = qr_decode_matrix(grid, correct=True)
            res["points"] = [(tl[0], tl[1]), (tr[0], tr[1]), (bl[0], bl[1]), p4]
            res["fit"] = frac
            return res, None
        except ValueError:
            if seen is None and dim >= 45:
                seen = qr_version_info(grid)
    return None, seen


def scan_luminance(w, h, lum, stats=None, offsets=_QV_OFFSETS):
    """Find and decode one QR code in a grayscale image (row-major bytes, w*h). Handles rotation, perspective,
    blur, noise, uneven lighting and mirrored images within limits (see module docstring). Returns the
    qr_decode_matrix(correct=True) dict plus "points" (finder centres TL, TR, BL and the fitted 4th point)
    and "fit" (share of fixed modules matched), or None. stats (optional dict) gets "finders", "attempts"."""
    if w < 21 or h < 21 or len(lum) < w * h:
        return None
    table = _qv_integral(lum, w, h)
    r = _qv_radius(w, h)
    tries = 0
    for offset in offsets:
        B = _qv_binarize(lum, w, h, table, r, offset)
        cands = _qv_finders(B, w, h)
        if stats is not None:
            stats["finders"] = stats.get("finders", 0) + len(cands)
        res, tries = _qv_scan_pass(lum, w, h, table, r, B, cands, tries)
        if res is not None:
            res["offset"] = offset
            if stats is not None:
                stats["attempts"] = tries
            return res
    if stats is not None:
        stats["attempts"] = tries
    return None


def _qv_scan_pass(lum, w, h, table, r, B, cands, tries):
    for tl, tr, bl in _qv_triples(cands):
        ms, dims = _qv_dims(B, w, h, tl, tr, bl)
        dims.sort(key=lambda d: -_qv_quick_score(B, w, h, tl, tr, bl, d))
        done = set()
        queue = list(dims)
        while queue:
            dim = queue.pop(0)
            if dim in done:
                continue
            done.add(dim)
            tries += 1
            res, ver = _qv_try_grid(lum, w, h, table, r, B, tl, tr, bl, ms, dim)
            if res is not None:
                return res, tries
            if ver is not None and 17 + 4 * ver not in done:
                queue.insert(0, 17 + 4 * ver)
    return None, tries


# ======================================================================================================
# 3. Framing and reassembly
# ======================================================================================================

def _crc(s):
    return "%08x" % (zlib.crc32(s.encode("ascii")) & 0xFFFFFFFF)


def _frame(sid, seq, total, payload):
    body = "%s|%s|%d|%d|%s" % (MAGIC, sid, seq, total, base64.b64encode(payload).decode("ascii"))
    return body + "|" + _crc(body)


def encode_frames(blob, name="bundle.gcag", chunk_size=DEFAULT_CHUNK, sid=None, encoding=None):
    """-> [header frame, data frame 1, ..., data frame N] (ASCII strings, one per QR code)."""
    if not isinstance(blob, (bytes, bytearray)):
        raise TypeError("blob must be bytes")
    if not 16 <= chunk_size <= 2000:
        raise ValueError("chunk_size must be 16..2000 bytes")
    blob = bytes(blob)
    digest = hashlib.sha256(blob).hexdigest()
    sid = sid or digest[:8]
    if not re.fullmatch(r"[0-9a-f]{8}", sid):
        raise ValueError("sid must be 8 lowercase hex digits")
    if encoding is None:
        encoding = "gcag-seal1" if blob.startswith(SEAL_MAGIC) else "opaque"
    chunks = [blob[i:i + chunk_size] for i in range(0, len(blob), chunk_size)] or [b""]
    total = len(chunks)
    header = json.dumps({"name": os.path.basename(name), "size": len(blob), "sha256": digest,
                         "chunk": chunk_size, "enc": encoding}, separators=(",", ":"), sort_keys=True)
    frames = [_frame(sid, 0, total, header.encode("utf-8"))]
    frames += [_frame(sid, k + 1, total, c) for k, c in enumerate(chunks)]
    return frames


_FRAME_RX = re.compile(r"^GCAG1\|([0-9a-f]{8})\|(\d{1,6})\|(\d{1,6})\|([A-Za-z0-9+/=]*)\|([0-9a-f]{8})$")


def parse_frame(text):
    """-> (sid, seq, total, payload bytes). Raises IntegrityError on a bad frame."""
    if isinstance(text, bytes):
        text = text.decode("ascii", "replace")
    text = text.strip()
    m = _FRAME_RX.match(text)
    if not m:
        raise IntegrityError("not a Stark Covenant airgap frame")
    sid, seq, total, b64, crc = m.groups()
    if _crc(text[:text.rfind("|")]) != crc:
        raise IntegrityError("CRC32 mismatch")
    seq, total = int(seq), int(total)
    if total < 1 or seq > total:
        raise IntegrityError("sequence number out of range")
    try:
        payload = base64.b64decode(b64, validate=True)
    except binascii.Error as e:
        raise IntegrityError("bad base64: %s" % e)
    return sid, seq, total, payload


def compress_ranges(nums):
    """[1, 2, 3, 7, 9, 10] -> '1-3,7,9-10'"""
    nums = sorted(set(nums))
    out, k = [], 0
    while k < len(nums):
        j = k
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(str(nums[k]) if j == k else "%d-%d" % (nums[k], nums[j]))
        k = j + 1
    return ",".join(out)


def expand_ranges(s):
    out = set()
    for part in (s or "").replace(" ", "").split(","):
        if not part:
            continue
        a, _, b = part.partition("-")
        lo, hi = int(a), int(b or a)
        if hi < lo or hi - lo > 100000:
            raise ValueError("bad range %r" % part)
        out.update(range(lo, hi + 1))
    return sorted(out)


class Reassembler:
    """Collects frames in any order, ignores duplicates, rejects corrupt or foreign frames, and verifies the
    SHA-256 of the whole bundle. add() returns one of: header, chunk, duplicate, conflict, bad-crc,
    foreign-session, malformed, complete."""

    def __init__(self, sid=None):
        self.sid = sid
        self.total = None
        self.header = None
        self.chunks = {}
        self.stats = {"header": 0, "chunk": 0, "duplicate": 0, "conflict": 0, "bad-crc": 0, "foreign-session": 0,
                      "malformed": 0}
        self.conflicts = []

    def add(self, text):
        try:
            sid, seq, total, payload = parse_frame(text)
        except IntegrityError as e:
            kind = "bad-crc" if "CRC" in str(e) else "malformed"
            self.stats[kind] += 1
            return kind
        if self.sid is None:
            self.sid = sid
        if sid != self.sid or (self.total is not None and total != self.total):
            self.stats["foreign-session"] += 1
            return "foreign-session"
        self.total = total
        if seq == 0:
            try:
                hdr = json.loads(payload.decode("utf-8"))
                if not re.fullmatch(r"[0-9a-f]{64}", hdr.get("sha256", "")) or not isinstance(hdr.get("size"), int):
                    raise ValueError
            except ValueError:
                self.stats["malformed"] += 1
                return "malformed"
            if self.header is not None:
                if hdr != self.header:
                    self.stats["conflict"] += 1
                    self.conflicts.append(0)
                    return "conflict"
                self.stats["duplicate"] += 1
                return "duplicate"
            self.header = hdr
            self.stats["header"] += 1
            return "complete" if self.complete() else "header"
        if seq in self.chunks:
            if self.chunks[seq] != payload:
                self.stats["conflict"] += 1
                self.conflicts.append(seq)
                return "conflict"
            self.stats["duplicate"] += 1
            return "duplicate"
        self.chunks[seq] = payload
        self.stats["chunk"] += 1
        return "complete" if self.complete() else "chunk"

    def missing(self):
        if self.total is None:
            return [0]
        miss = [k for k in range(1, self.total + 1) if k not in self.chunks]
        return ([0] if self.header is None else []) + miss

    def complete(self):
        return self.total is not None and not self.missing()

    def progress(self):
        got = len(self.chunks)
        return {"sid": self.sid, "received": got, "total": self.total, "header_ok": self.header is not None,
                "missing": compress_ranges(self.missing()), "counts": dict(self.stats)}

    def result(self):
        """-> (bundle bytes, header dict). Raises IntegrityError when incomplete or the SHA-256 differs."""
        if not self.complete():
            raise IntegrityError("incomplete: missing frames %s" % compress_ranges(self.missing()))
        blob = b"".join(self.chunks[k] for k in range(1, self.total + 1))
        if len(blob) != self.header["size"]:
            raise IntegrityError("size mismatch: got %d bytes, header says %d" % (len(blob), self.header["size"]))
        if hashlib.sha256(blob).hexdigest() != self.header["sha256"]:
            raise IntegrityError("SHA-256 mismatch: bundle corrupted or frames from different senders mixed"
                                 + (" (conflicting chunks: %s)" % compress_ranges(self.conflicts) if self.conflicts else ""))
        return blob, dict(self.header)


# ======================================================================================================
# 4. Sealing (AES-256-GCM via security_vault's OS backend, PBKDF2-HMAC-SHA256 key)
# ======================================================================================================

def _vault():
    try:
        import security_vault
    except ImportError as e:
        raise RuntimeError("seal/unseal need security_vault.py (OS AES-GCM backend): %s" % e)
    return security_vault


def _key(passphrase, salt, iterations):
    if not passphrase:
        raise ValueError("passphrase required")
    pw = unicodedata.normalize("NFC", passphrase).encode("utf-8")
    return hashlib.pbkdf2_hmac("sha256", pw, salt, iterations, 32)


def seal(plaintext, passphrase, iterations=SEAL_ITERATIONS):
    """-> GCAG-SEAL1 | iterations (4, BE) | salt (16) | nonce (12) | AES-256-GCM ciphertext | tag (16).
    The 42-byte header is the GCM additional data."""
    sv = _vault()
    if not 1000 <= iterations <= 10_000_000:
        raise ValueError("iterations out of range")
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    header = SEAL_MAGIC + struct.pack(">I", iterations) + salt + nonce
    return header + sv.aes_gcm_encrypt(_key(passphrase, salt, iterations), nonce, bytes(plaintext), header)


def unseal(blob, passphrase):
    sv = _vault()
    if not blob.startswith(SEAL_MAGIC) or len(blob) < len(SEAL_MAGIC) + 32 + 16:
        raise IntegrityError("not a sealed airgap bundle")
    p = len(SEAL_MAGIC)
    iterations = struct.unpack(">I", blob[p:p + 4])[0]
    salt, nonce = blob[p + 4:p + 20], blob[p + 20:p + 32]
    if not 1000 <= iterations <= 10_000_000:
        raise IntegrityError("bad iteration count")
    header = blob[:p + 32]
    try:
        return sv.aes_gcm_decrypt(_key(passphrase, salt, iterations), nonce, blob[p + 32:], header)
    except Exception as e:
        raise IntegrityError("wrong passphrase or bundle altered (%s)" % type(e).__name__)


# ======================================================================================================
# 5. Browser pages
# ======================================================================================================

def _packed(mod):
    n = len(mod)
    bits = bytearray((n * n + 7) // 8)
    for y in range(n):
        for x in range(n):
            if mod[y][x]:
                k = y * n + x
                bits[k >> 3] |= 128 >> (k & 7)
    return base64.b64encode(bytes(bits)).decode("ascii")


_STYLE = """
:root{--bg:#f4f2ee;--fg:#1d1b18;--muted:#5f5a52;--panel:#fff;--line:#d9d4cb;--ok:#2f7d4f;--bad:#b3261e;--accent:#4a5a8c}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#171614;--fg:#ece8e1;--muted:#a9a297;--panel:#211f1c;--line:#3a3631;--ok:#6cc08b;--bad:#f2867d;--accent:#9fb0e6}}
:root[data-theme="dark"]{--bg:#171614;--fg:#ece8e1;--muted:#a9a297;--panel:#211f1c;--line:#3a3631;--ok:#6cc08b;--bad:#f2867d;--accent:#9fb0e6}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,"Segoe UI",sans-serif}
main{max-width:980px;margin:0 auto;padding:16px}h1{font-size:1.25rem;margin:.2rem 0 .6rem}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:8px 0}
button,input,select{font:inherit;padding:6px 10px;border:1px solid var(--line);border-radius:6px;background:var(--panel);color:var(--fg)}
button{cursor:pointer}button.primary{background:var(--accent);color:#fff;border-color:var(--accent)}
.muted{color:var(--muted)}.ok{color:var(--ok)}.bad{color:var(--bad)}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px;margin:10px 0}
code{font-family:ui-monospace,Consolas,monospace;font-size:.9em;word-break:break-all}
"""

_SENDER_JS = r"""
const FR = __FRAMES__, META = __META__;
const cv = document.getElementById('qr'), ctx = cv.getContext('2d');
let list = [], pos = 0, playing = true, timer = null;
function bits(f){ if(!f.m){ const r = atob(f.b); f.m = new Uint8Array(r.length); for(let i=0;i<r.length;i++) f.m[i]=r.charCodeAt(i);} return f.m; }
function buildList(only){
  const data = only && only.length ? only.filter(k => k>=1 && k<FR.length) : FR.map((_,k)=>k).slice(1);
  list = [];
  data.forEach((k,i) => { if(i % 8 === 0) list.push(0); list.push(k); });
  if(!list.length) list = [0];
  pos = 0;
}
function draw(k){
  const f = FR[k], n = f.n, border = 4, total = n + 2*border;
  const fs = document.fullscreenElement != null;
  const avail = Math.min(window.innerWidth - 32, (fs ? window.innerHeight : window.innerHeight - 170)) * devicePixelRatio;
  const px = Math.max(1, Math.floor(avail / total));
  cv.width = cv.height = total * px;
  cv.style.width = cv.style.height = (total * px / devicePixelRatio) + 'px';
  ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, cv.width, cv.height); ctx.fillStyle = '#000';
  const m = bits(f);
  for(let y=0;y<n;y++) for(let x=0;x<n;x++){ const q=y*n+x; if(m[q>>3] & (128>>(q&7))) ctx.fillRect((x+border)*px,(y+border)*px,px,px); }
  document.getElementById('lab').textContent = (k===0 ? 'header' : ('chunk ' + k + ' / ' + (FR.length-1))) + '  (QR v' + ((n-17)/4) + ', ' + n + 'x' + n + ')';
}
function tick(){ draw(list[pos]); pos = (pos + 1) % list.length; if(playing) timer = setTimeout(tick, 1000 / +document.getElementById('fps').value); }
function play(on){ playing = on; clearTimeout(timer); document.getElementById('pp').textContent = on ? 'Pause' : 'Play'; if(on) tick(); }
function expand(s){ const out=[]; for(const p of s.replace(/\s/g,'').split(',')){ if(!p) continue; const [a,b]=p.split('-'); const lo=+a, hi=+(b||a); if(!(hi>=lo)) continue; for(let k=lo;k<=hi && k-lo<100000;k++) out.push(k);} return out; }
document.getElementById('pp').onclick = () => play(!playing);
document.getElementById('full').onclick = () => { const s=document.getElementById('stage'); (s.requestFullscreen ? s.requestFullscreen() : Promise.reject()).catch(()=>{}); };
document.getElementById('only').onchange = e => { buildList(expand(e.target.value)); play(true); };
document.getElementById('all').onclick = () => { document.getElementById('only').value=''; buildList(null); play(true); };
document.getElementById('png').onclick = () => { cv.toBlob(b => { const a=document.createElement('a'); a.href=URL.createObjectURL(b); a.download='gcag_' + META.sid + '_' + document.getElementById('lab').textContent.split(' ')[1] + '.png'; a.click(); }); };
document.addEventListener('keydown', e => { if(e.key===' '){ e.preventDefault(); play(!playing);} else if(e.key==='ArrowRight'){ play(false); pos=(pos+1)%list.length; draw(list[pos]);} else if(e.key==='ArrowLeft'){ play(false); pos=(pos-1+list.length)%list.length; draw(list[pos]);} else if(e.key==='f'){ document.getElementById('full').click(); } });
document.addEventListener('fullscreenchange', () => draw(list[(pos-1+list.length)%list.length]));
window.addEventListener('resize', () => draw(list[(pos-1+list.length)%list.length]));
buildList(null); play(true);
"""

_SENDER_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Airgap Sender</title><style>__STYLE__
#stage{display:flex;flex-direction:column;align-items:center;justify-content:center;background:#fff;padding:8px;border-radius:8px}
#stage:fullscreen{padding:0;border-radius:0}#qr{image-rendering:pixelated;display:block}#lab{color:#333;font:13px ui-monospace,Consolas,monospace;margin-top:6px}
</style></head><body><main>
<h1>Stark Covenant airgap sender</h1>
<div class="muted">__SUMMARY__</div>
<div class="bar"><button class="primary" id="pp">Pause</button><button id="full">Full screen (f)</button>
<label>Frames/s <input id="fps" type="number" min="1" max="20" value="__FPS__" style="width:4.5em"></label>
<label>Resend only <input id="only" placeholder="e.g. 3,7,12-15" style="width:11em"></label><button id="all">All</button>
<button id="png">Save this frame as PNG</button></div>
<div id="stage"><canvas id="qr" width="10" height="10" aria-label="QR frame"></canvas><div id="lab"></div></div>
<p class="muted">Space pauses, arrow keys step. Keep the receiver's camera square to the screen; raise brightness. The header frame repeats every 8 frames.</p>
</main><script>__JS__</script></body></html>"""


def sender_html(frames, title="bundle", fps=5, ecl="M"):
    """Self-contained sender page for the given frames (from encode_frames)."""
    out = []
    sid = parse_frame(frames[0])[0]
    for f in frames:
        mod, v, _m = qr_encode(f.encode("ascii"), ecl)
        out.append({"n": len(mod), "b": _packed(mod)})
    hdr = json.loads(parse_frame(frames[0])[3])
    summary = "%s &middot; %d bytes &middot; %d chunks of %d bytes &middot; SHA-256 <code>%s</code> &middot; session %s" % (
        _h(title), hdr["size"], len(frames) - 1, hdr["chunk"], hdr["sha256"], sid)
    js = _SENDER_JS.replace("__FRAMES__", json.dumps(out)).replace("__META__", json.dumps({"sid": sid}))
    return (_SENDER_HTML.replace("__STYLE__", _STYLE).replace("__SUMMARY__", summary)
            .replace("__FPS__", str(int(fps))).replace("__JS__", js))


def _h(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


_QR_VISION_JS = r"""
// ---------- qr_vision: QR detection + decoding for camera frames (line-for-line port of optical_airgap.py
// sections 1 and 2b: Reed-Solomon / BCH correction, adaptive threshold, finder / alignment patterns,
// homography, grid sampling). Self-contained: also runs inside a Web Worker built from its own source. ----------
function qrVisionModule(){
'use strict';
// ---- GF(256), Reed-Solomon (Berlekamp-Massey, Chien, Forney) ----
const EXP = new Uint8Array(512), LOG = new Uint8Array(256);
{ let x = 1; for (let i = 0; i < 255; i++){ EXP[i] = x; LOG[x] = i; x <<= 1; if (x & 0x100) x ^= 0x11D; } for (let i = 255; i < 512; i++) EXP[i] = EXP[i - 255]; }
function gmul(a, b){ return (a === 0 || b === 0) ? 0 : EXP[LOG[a] + LOG[b]]; }
function gdiv(a, b){ if (b === 0) throw new Error('GF(256) division by zero'); return a === 0 ? 0 : EXP[(LOG[a] - LOG[b] + 255) % 255]; }
function evalLow(p, x){ let y = 0; for (let i = p.length - 1; i >= 0; i--) y = gmul(y, x) ^ p[i]; return y; }
function syndOk(blk, n){ for (let i = 0; i < n; i++){ let s = 0; for (let k = 0; k < blk.length; k++) s = gmul(s, EXP[i]) ^ blk[k]; if (s) return false; } return true; }
function rsCorrect(block, n, maxErrors){
  if (maxErrors === undefined) maxErrors = n >> 1;
  const blk = Array.from(block), ln = blk.length;
  if (ln > 255 || ln <= n) throw new Error('bad Reed-Solomon block length');
  const synd = []; let any = false;
  for (let i = 0; i < n; i++){ const x = EXP[i]; let s = 0; for (let k = 0; k < ln; k++) s = gmul(s, x) ^ blk[k]; synd.push(s); if (s) any = true; }
  if (!any) return [blk, 0];
  let C = [1], B = [1], L = 0, m = 1, bb = 1;
  for (let k = 0; k < n; k++){
    let d = synd[k];
    const lim = Math.min(L, C.length - 1);
    for (let i = 1; i <= lim; i++) d ^= gmul(C[i], synd[k - i]);
    if (d === 0){ m++; continue; }
    const coef = gdiv(d, bb), T = C.slice();
    while (C.length < B.length + m) C.push(0);
    for (let i = 0; i < B.length; i++) C[i + m] ^= gmul(coef, B[i]);
    if (2 * L <= k){ L = k + 1 - L; B = T; bb = d; m = 1; } else m++;
  }
  while (C.length < L + 1) C.push(0);
  C = C.slice(0, L + 1);
  if (L === 0 || C[L] === 0 || 2 * L > n || L > maxErrors) throw new Error('Reed-Solomon: too many errors (' + L + ' located, ' + maxErrors + ' correctable)');
  const pos = [];
  for (let k = 0; k < ln; k++) if (evalLow(C, EXP[(255 - (ln - 1 - k)) % 255]) === 0) pos.push(k);
  if (pos.length !== L) throw new Error('Reed-Solomon: locator has ' + pos.length + ' roots in the block, expected ' + L);
  const omega = new Array(n).fill(0);
  for (let i = 0; i < n; i++){ let acc = 0; for (let j = 0; j <= Math.min(i, L); j++) acc ^= gmul(synd[i - j], C[j]); omega[i] = acc; }
  for (const k of pos){
    const e = ln - 1 - k, li = (255 - e) % 255;
    let den = 0;
    for (let i = 1; i <= L; i += 2) if (C[i]) den ^= gmul(C[i], EXP[(li * (i - 1)) % 255]);
    if (den === 0) throw new Error('Reed-Solomon: Forney denominator is zero');
    blk[k] ^= gmul(EXP[e], gdiv(evalLow(omega, EXP[li]), den));
  }
  if (!syndOk(blk, n)) throw new Error('Reed-Solomon: correction did not produce a codeword');
  return [blk, L];
}
// ---- QR tables (ISO/IEC 18004), ECC L / M / Q / H ----
const ECC = {L:[7,10,15,20,26,18,20,24,30,18,20,24,26,30,22,24,28,30,28,28,28,28,30,30,26,28,30,30,30,30,30,30,30,30,30,30,30,30,30,30],
             M:[10,16,26,18,24,16,18,22,22,26,30,22,22,24,24,28,28,26,26,26,26,28,28,28,28,28,28,28,28,28,28,28,28,28,28,28,28,28,28,28],
             Q:[13,22,18,26,18,24,18,22,20,24,28,26,24,20,30,24,28,28,26,30,28,30,30,30,30,28,30,30,30,30,30,30,30,30,30,30,30,30,30,30],
             H:[17,28,22,16,22,28,26,26,24,28,24,28,22,24,24,30,28,28,26,28,30,24,30,30,30,30,30,30,30,30,30,30,30,30,30,30,30,30,30,30]};
const NB = {L:[1,1,1,1,1,2,2,2,2,4,4,4,4,4,6,6,6,6,7,8,8,9,9,10,12,12,12,13,14,15,16,17,18,19,19,20,21,22,24,25],
            M:[1,1,1,2,2,4,4,4,5,5,5,8,9,9,10,10,11,13,14,16,17,17,18,20,21,23,25,26,28,29,31,33,35,37,38,40,43,45,47,49],
            Q:[1,1,2,2,4,4,6,6,8,8,8,10,12,16,12,17,16,18,21,20,23,23,25,27,29,34,34,35,38,40,43,45,48,51,53,56,59,62,65,68],
            H:[1,1,2,4,4,4,5,6,8,8,11,11,16,16,18,16,19,21,25,25,25,34,30,32,35,37,40,42,45,48,51,54,57,60,63,66,70,74,77,81]};
const ECLB = {L:1, M:0, Q:3, H:2}, MISDECODE = {'1L':3, '1M':2, '1Q':1, '1H':1, '2L':2, '3L':1};
function rawCw(v){ let r = (16 * v + 128) * v + 64; if (v >= 2){ const na = Math.floor(v / 7) + 2; r -= (25 * na - 10) * na - 55; if (v >= 7) r -= 36; } return Math.floor(r / 8); }
function layout(v, e){ const ec = ECC[e][v - 1], nb = NB[e][v - 1], t = rawCw(v), sh = nb - t % nb, sl = Math.floor(t / nb); const g = [[sh, sl - ec]]; if (nb - sh) g.push([nb - sh, sl + 1 - ec]); return [ec, g]; }
function rsCapacity(v, e){ return (ECC[e][v - 1] - (MISDECODE[v + e] || 0)) >> 1; }
function align(v){ if (v === 1) return []; const na = Math.floor(v / 7) + 2, size = 17 + 4 * v; const step = v === 32 ? 26 : Math.floor((v * 4 + na * 2 + 1) / (na * 2 - 2)) * 2; const out = [6]; for (let k = 0; k < na - 1; k++) out.splice(1, 0, size - 7 - k * step); return out; }
function fmtBits(e, m){ const d = (ECLB[e] << 3) | m; let r = d; for (let i = 0; i < 10; i++) r = (r << 1) ^ ((r >> 9) * 0x537); return ((d << 10) | (r & 0x3FF)) ^ 0x5412; }
function verBits(v){ let r = v; for (let i = 0; i < 12; i++) r = (r << 1) ^ ((r >> 11) * 0x1F25); return (v << 12) | (r & 0xFFF); }
function popcount(x){ let c = 0; while (x){ x &= x - 1; c++; } return c; }
const FMT = []; for (const e of ['L', 'M', 'Q', 'H']) for (let m = 0; m < 8; m++) FMT.push([fmtBits(e, m), e, m]);
function bchFormat(word){ let best = null, dist = 99; for (const f of FMT){ const d = popcount(f[0] ^ word); if (d < dist){ best = f; dist = d; } } return dist <= 3 ? [best[1], best[2], dist] : null; }
function bchVersion(word){ let best = null, dist = 99; for (let v = 7; v <= 40; v++){ const d = popcount(verBits(v) ^ word); if (d < dist){ best = v; dist = d; } } return dist <= 3 ? [best, dist] : null; }
function versionWords(mod){ const n = mod.length; let a = 0, b = 0; for (let i = 0; i < 18; i++){ if (mod[Math.floor(i / 3)][n - 11 + i % 3]) a |= 1 << i; if (mod[n - 11 + i % 3][Math.floor(i / 3)]) b |= 1 << i; } return [a, b]; }
function versionInfo(mod){ let best = null; for (const w of versionWords(mod)){ const h = bchVersion(w); if (h && (best === null || h[1] < best[1])) best = h; } return best ? best[0] : null; }
const FN = {};
function fnMap(v){
  if (FN[v]) return FN[v];
  const n = 17 + 4 * v, fn = []; for (let y = 0; y < n; y++) fn.push(new Uint8Array(n));
  for (let i = 0; i < n; i++){ fn[i][6] = 1; fn[6][i] = 1; }
  for (const [cx, cy] of [[3, 3], [n - 4, 3], [3, n - 4]]) for (let dy = -4; dy <= 4; dy++) for (let dx = -4; dx <= 4; dx++){ const x = cx + dx, y = cy + dy; if (x >= 0 && y >= 0 && x < n && y < n) fn[y][x] = 1; }
  const al = align(v), k = al.length;
  for (let a = 0; a < k; a++) for (let b = 0; b < k; b++){ if ((a === 0 && b === 0) || (a === 0 && b === k - 1) || (a === k - 1 && b === 0)) continue; for (let dy = -2; dy <= 2; dy++) for (let dx = -2; dx <= 2; dx++) fn[al[b] + dy][al[a] + dx] = 1; }
  for (let i = 0; i < 9; i++){ fn[8][i] = 1; fn[i][8] = 1; } for (let i = 0; i < 8; i++){ fn[8][n - 1 - i] = 1; fn[n - 1 - i][8] = 1; }
  if (v >= 7) for (let i = 0; i < 18; i++){ const a = n - 11 + i % 3, b = Math.floor(i / 3); fn[b][a] = 1; fn[a][b] = 1; }
  return (FN[v] = fn);
}
function maskBit(m, x, y){ switch (m){ case 0: return (x + y) % 2 === 0; case 1: return y % 2 === 0; case 2: return x % 3 === 0; case 3: return (x + y) % 3 === 0;
  case 4: return (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0; case 5: return x * y % 2 + x * y % 3 === 0; case 6: return (x * y % 2 + x * y % 3) % 2 === 0; default: return ((x + y) % 2 + x * y % 3) % 2 === 0; } }
// decodeGrid: matrix of booleans -> {text (one char per byte), bytes, version, ecl, mask, corrected, formatErrors}
// (= qr_decode_matrix(correct=True)). Throws Error.
function decodeGrid(mod){
  const n = mod.length, v = (n - 17) / 4;
  if (!(Number.isInteger(v) && v >= 1 && v <= 40) || mod.some(r => r.length !== n)) throw new Error('bad QR matrix size ' + n);
  const c1 = [], c2 = []; for (let i = 0; i < 6; i++) c1.push([8, i]); c1.push([8, 7], [8, 8], [7, 8]); for (let i = 9; i < 15; i++) c1.push([14 - i, 8]);
  for (let i = 0; i < 8; i++) c2.push([n - 1 - i, 8]); for (let i = 8; i < 15; i++) c2.push([8, n - 15 + i]);
  let f1 = 0, f2 = 0; for (let i = 0; i < 15; i++){ if (mod[c1[i][1]][c1[i][0]]) f1 |= 1 << i; if (mod[c2[i][1]][c2[i][0]]) f2 |= 1 << i; }
  const h1 = bchFormat(f1), h2 = bchFormat(f2);
  if (!h1 && !h2) throw new Error('format information unreadable (more than 3 bit errors in both copies)');
  const hf = (h1 && (!h2 || h1[2] <= h2[2])) ? h1 : h2;
  const ecl = hf[0], mask = hf[1];
  if (v >= 7){ const vi = versionInfo(mod); if (vi !== null && vi !== v) throw new Error('version information says ' + vi + ', grid is ' + v); }
  const fn = fnMap(v), [ec, groups] = layout(v, ecl); let nblk = 0, dataCw = 0; for (const [c, d] of groups){ nblk += c; dataCw += c * d; }
  const total = dataCw + ec * nblk, bits = [];
  for (let right = n - 1; right >= 1; right -= 2){ if (right === 6) right = 5; const up = ((right + 1) & 2) === 0;
    for (let vert = 0; vert < n; vert++){ const y = up ? n - 1 - vert : vert; for (let j = 0; j < 2; j++){ const x = right - j; if (!fn[y][x]) bits.push((mod[y][x] ? 1 : 0) ^ (maskBit(mask, x, y) ? 1 : 0)); } } }
  const cws = new Uint8Array(total); for (let i = 0; i < total; i++){ let b = 0; for (let j = 0; j < 8; j++) b = (b << 1) | bits[i * 8 + j]; cws[i] = b; }
  const lens = []; for (const [c, d] of groups) for (let i = 0; i < c; i++) lens.push(d);
  let blocks = lens.map(() => []), p = 0; const mx = Math.max(...lens);
  for (let i = 0; i < mx; i++) for (let b = 0; b < lens.length; b++) if (i < lens[b]) blocks[b].push(cws[p++]);
  for (let i = 0; i < ec; i++) for (let b = 0; b < lens.length; b++) blocks[b].push(cws[p++]);
  let fixed = 0; const cap = rsCapacity(v, ecl);
  for (let b = 0; b < blocks.length; b++){
    let r; try { r = rsCorrect(blocks[b], ec, cap); } catch (e){ throw new Error('block ' + (b + 1) + ' of ' + blocks.length + ': ' + e.message); }
    blocks[b] = r[0]; fixed += r[1];
  }
  const stream = []; for (let b = 0; b < blocks.length; b++) for (let i = 0; i < lens[b]; i++) stream.push(blocks[b][i]);
  const bit = i => (stream[i >> 3] >> (7 - (i & 7))) & 1;
  const rd = (o, k) => { let x = 0; for (let i = 0; i < k; i++) x = x * 2 + bit(o + i); return x; };
  if (rd(0, 4) !== 4) throw new Error('not a byte-mode QR code');
  const cc = v < 10 ? 8 : 16, len = rd(4, cc), st = 4 + cc;
  if (st + 8 * len > stream.length * 8) throw new Error('byte count exceeds capacity');
  const bytes = new Uint8Array(len); let text = '';
  for (let i = 0; i < len; i++){ bytes[i] = rd(st + 8 * i, 8); text += String.fromCharCode(bytes[i]); }
  return {text: text, bytes: bytes, version: v, ecl: ecl, mask: mask, corrected: fixed, formatErrors: hf[2]};
}
function decodeMatrix(mod){ return decodeGrid(mod).text; }
// ---- clean axis-aligned render (old fast path, any scale / quiet zone) ----
function matrixFromImageData(id){
  const w = id.width, h = id.height, lum = lumaFromImageData(id); let lo = 255, hi = 0;
  for (let i = 0; i < w * h; i++){ if (lum[i] < lo) lo = lum[i]; if (lum[i] > hi) hi = lum[i]; }
  if (hi - lo < 64) throw new Error('no contrast'); const thr = (lo + hi) / 2, dk = (x, y) => lum[y * w + x] < thr;
  let top = -1, bottom = -1, left = w, right = -1;
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) if (dk(x, y)){ if (top < 0) top = y; bottom = y; if (x < left) left = x; if (x > right) right = x; }
  if (top < 0) throw new Error('blank image');
  let run = 0; for (let x = left; x <= right && dk(x, top); x++) run++;
  if (run < 7) throw new Error('finder not found');
  let n = Math.round((right - left + 1) / (run / 7)); const v = Math.round((n - 17) / 4); n = 17 + 4 * v; if (v < 1 || v > 40) throw new Error('version?');
  const sx = (right - left + 1) / n, sy = (bottom - top + 1) / n, mod = [];
  for (let j = 0; j < n; j++){ const row = []; const yy = Math.floor(top + (j + 0.5) * sy); for (let i = 0; i < n; i++) row.push(dk(Math.floor(left + (i + 0.5) * sx), yy)); mod.push(row); }
  return mod;
}
function lumaFromImageData(id){
  const w = id.width, h = id.height, d = id.data, lum = new Uint8Array(w * h);
  for (let i = 0, j = 0; i < w * h; i++, j += 4){ const a = d[j + 3]; let l = Math.floor((299 * d[j] + 587 * d[j + 1] + 114 * d[j + 2]) / 1000); if (a !== 255) l = Math.floor((l * a + 255 * (255 - a)) / 255); lum[i] = l; }
  return lum;
}
// ---- computer vision (mirrors _qv_* in optical_airgap.py) ----
const OFFSETS = [6, 14, 0];
function integral(lum, w, h){
  const W = w + 1, T = new Int32Array(W * (h + 1));
  for (let y = 0; y < h; y++){ let acc = 0; const o = (y + 1) * W, p = y * W, q = y * w; for (let x = 0; x < w; x++){ acc += lum[q + x]; T[o + x + 1] = T[p + x + 1] + acc; } }
  return T;
}
function radius(w, h){ return Math.max(8, Math.floor(Math.max(w, h) / 8)); }
function* binarizeGen(lum, w, h, T, r, offset){
  const W = w + 1, B = new Uint8Array(w * h), k = 100 - offset;
  const xa = new Int32Array(w), xb = new Int32Array(w), sa = new Int32Array(w), sb = new Int32Array(w);
  for (let x = 0; x < w; x++){ xa[x] = Math.max(0, x - r); xb[x] = Math.min(w, x + r + 1); sa[x] = Math.max(0, x - 1); sb[x] = Math.min(w, x + 2); }
  for (let y = 0; y < h; y++){
    const ya = Math.max(0, y - r), yb = Math.min(h, y + r + 1), ta = Math.max(0, y - 1), tb = Math.min(h, y + 2);
    const A = ya * W, Bo = yb * W, C = ta * W, D = tb * W, wy = (yb - ya) * 100, by = (tb - ta) * k, o = y * w;
    for (let x = 0; x < w; x++){
      const win = (T[Bo + xb[x]] - T[A + xb[x]]) - (T[Bo + xa[x]] - T[A + xa[x]]);
      const box = (T[D + sb[x]] - T[C + sb[x]]) - (T[D + sa[x]] - T[C + sa[x]]);
      B[o + x] = box * (xb[x] - xa[x]) * wy < win * (sb[x] - sa[x]) * by ? 1 : 0;
    }
    if ((y & 63) === 63) yield 0;
  }
  return B;
}
function ratio(c0, c1, c2, c3, c4, tol){
  const total = c0 + c1 + c2 + c3 + c4; if (total < 7) return false;
  const m = total / 7.0, v = m * tol;
  return Math.abs(m - c0) < v && Math.abs(m - c1) < v && Math.abs(3.0 * m - c2) < 3.0 * v && Math.abs(m - c3) < v && Math.abs(m - c4) < v;
}
function cross(B, w, h, x, y, dx, dy, maxCount, origTotal){
  let c0 = 0, c1 = 0, c2 = 0, c3 = 0, c4 = 0, i = x, j = y;
  const inb = () => i >= 0 && i < w && j >= 0 && j < h;
  while (inb() && B[j * w + i]){ c2++; i -= dx; j -= dy; }
  if (!inb()) return null;
  while (inb() && !B[j * w + i] && c1 <= maxCount){ c1++; i -= dx; j -= dy; }
  if (!inb() || c1 > maxCount) return null;
  while (inb() && B[j * w + i] && c0 <= maxCount){ c0++; i -= dx; j -= dy; }
  if (c0 > maxCount) return null;
  i = x + dx; j = y + dy; let f2 = 0;
  while (inb() && B[j * w + i]){ f2++; i += dx; j += dy; }
  if (!inb()) return null;
  while (inb() && !B[j * w + i] && c3 < maxCount){ c3++; i += dx; j += dy; }
  if (!inb() || c3 >= maxCount) return null;
  while (inb() && B[j * w + i] && c4 < maxCount){ c4++; i += dx; j += dy; }
  if (c4 >= maxCount) return null;
  c2 += f2;
  const total = c0 + c1 + c2 + c3 + c4;
  if (5 * Math.abs(total - origTotal) >= 2 * origTotal) return null;
  if (!ratio(c0, c1, c2, c3, c4, 0.5)) return null;
  return [1.0 + f2 - c2 / 2.0, total];
}
function diagonal(B, w, h, x, y){
  const c = [0, 0, 0, 0, 0]; let k = 0;
  while (x - k >= 0 && y - k >= 0 && B[(y - k) * w + x - k]){ c[2]++; k++; }
  if (x - k < 0 || y - k < 0) return false;
  while (x - k >= 0 && y - k >= 0 && !B[(y - k) * w + x - k]){ c[1]++; k++; }
  if (x - k < 0 || y - k < 0) return false;
  while (x - k >= 0 && y - k >= 0 && B[(y - k) * w + x - k]){ c[0]++; k++; }
  k = 1;
  while (x + k < w && y + k < h && B[(y + k) * w + x + k]){ c[2]++; k++; }
  if (x + k >= w || y + k >= h) return false;
  while (x + k < w && y + k < h && !B[(y + k) * w + x + k]){ c[3]++; k++; }
  if (x + k >= w || y + k >= h) return false;
  while (x + k < w && y + k < h && B[(y + k) * w + x + k]){ c[4]++; k++; }
  return ratio(c[0], c[1], c[2], c[3], c[4], 0.75);
}
function runs(B, o, x0, x1){   // maximal runs of row segment [x0, x1] -> flat [start, end, start, end, ...] relative to x0
  const out = []; let s = x0;
  for (let x = x0 + 1; x <= x1 + 1; x++) if (x === x1 + 1 || B[o + x] !== B[o + x - 1]){ out.push(s - x0, x - x0); s = x; }
  return out;
}
function* findersGen(B, w, h){
  const cands = [], step = h <= 1000 ? 1 : 2;
  for (let y = 0; y < h; y += step){
    const o = y * w, sp = runs(B, o, 0, w - 1), n = sp.length / 2;
    let k = B[o] ? 0 : 1;
    while (k + 4 < n){
      const s2 = sp[2 * (k + 2)], c0 = sp[2 * k + 1] - sp[2 * k], c1 = sp[2 * k + 3] - sp[2 * k + 2], c2 = sp[2 * k + 5] - s2,
            c3 = sp[2 * k + 7] - sp[2 * k + 6], c4 = sp[2 * k + 9] - sp[2 * k + 8];
      if (ratio(c0, c1, c2, c3, c4, 0.5)){
        const total = c0 + c1 + c2 + c3 + c4, ix = Math.trunc(s2 + c2 / 2.0);
        const vr = cross(B, w, h, ix, y, 0, 1, c2, total);
        if (vr !== null){
          const cy = y + vr[0], iy = Math.trunc(cy);
          const hr = cross(B, w, h, ix, iy, 1, 0, c2, total);
          if (hr !== null && diagonal(B, w, h, Math.trunc(ix + hr[0]), iy)){
            const cx = ix + hr[0], ms = (hr[1] + vr[1]) / 14.0;
            let merged = false;
            for (const c of cands){
              if (Math.abs(cy - c[1]) <= ms && Math.abs(cx - c[0]) <= ms && (Math.abs(ms - c[2]) <= 1.0 || Math.abs(ms - c[2]) <= c[2])){
                const cnt = c[3]; c[0] = (cnt * c[0] + cx) / (cnt + 1); c[1] = (cnt * c[1] + cy) / (cnt + 1); c[2] = (cnt * c[2] + ms) / (cnt + 1); c[3] = cnt + 1;
                merged = true; break;
              }
            }
            if (!merged) cands.push([cx, cy, ms, 1]);
          }
        }
      }
      k += 2;
    }
    if ((y & 31) === 31) yield 0;
  }
  return cands;
}
function triples(cands){
  const pool = cands.slice().sort((a, b) => b[3] - a[3]).slice(0, 12), out = [], n = pool.length;
  for (let a = 0; a < n; a++) for (let b = a + 1; b < n; b++) for (let c = b + 1; c < n; c++){
    const p = pool[a], q = pool[b], r = pool[c];
    const msmax = Math.max(p[2], q[2], r[2]), msmin = Math.min(p[2], q[2], r[2]);
    if (msmax > 1.8 * msmin) continue;
    const dpq = (p[0] - q[0]) * (p[0] - q[0]) + (p[1] - q[1]) * (p[1] - q[1]);
    const dqr = (q[0] - r[0]) * (q[0] - r[0]) + (q[1] - r[1]) * (q[1] - r[1]);
    const dpr = (p[0] - r[0]) * (p[0] - r[0]) + (p[1] - r[1]) * (p[1] - r[1]);
    let tl, o1, o2, hyp, l1, l2;
    if (dqr >= dpq && dqr >= dpr){ tl = p; o1 = q; o2 = r; hyp = dqr; l1 = dpq; l2 = dpr; }
    else if (dpr >= dpq){ tl = q; o1 = p; o2 = r; hyp = dpr; l1 = dpq; l2 = dqr; }
    else { tl = r; o1 = p; o2 = q; hyp = dpq; l1 = dpr; l2 = dqr; }
    const ms = (p[2] + q[2] + r[2]) / 3.0, lo = Math.min(l1, l2), hi = Math.max(l1, l2);
    if (lo < (10.0 * ms) * (10.0 * ms) || hi > 2.6 * lo || hi > (180.0 * ms) * (180.0 * ms)) continue;
    const pyth = Math.abs(hyp - (l1 + l2)) / hyp;
    if (pyth > 0.45) continue;
    const cr = (o1[0] - tl[0]) * (o2[1] - tl[1]) - (o1[1] - tl[1]) * (o2[0] - tl[0]);
    const tr = cr > 0 ? o1 : o2, bl = cr > 0 ? o2 : o1;
    out.push([(hi / lo - 1.0) + 2.0 * pyth + (msmax / msmin - 1.0), tl, tr, bl]);
  }
  out.sort((x, y) => x[0] - y[0]);
  return out.slice(0, 4).map(t => [t[1], t[2], t[3]]);
}
function run3(B, w, h, fx, fy, tx, ty){
  const dx = tx - fx, dy = ty - fy, ln = Math.sqrt(dx * dx + dy * dy);
  if (ln < 1.0) return -1.0;
  const ux = dx / ln, uy = dy / ln; let state = 0;
  for (let i = 0; i < ln; i++){
    const x = Math.floor(fx + ux * i), y = Math.floor(fy + uy * i);
    if (x < 0 || y < 0 || x >= w || y >= h) return -1.0;
    const d = B[y * w + x];
    if (state === 0){ if (!d) state = 1; }
    else if (state === 1){ if (d) state = 2; }
    else if (!d) return i;
  }
  return -1.0;
}
function moduleOneWay(B, w, h, p, q){
  const a = run3(B, w, h, p[0], p[1], q[0], q[1]), b = run3(B, w, h, p[0], p[1], 2.0 * p[0] - q[0], 2.0 * p[1] - q[1]);
  const c = run3(B, w, h, q[0], q[1], p[0], p[1]), d = run3(B, w, h, q[0], q[1], 2.0 * q[0] - p[0], 2.0 * q[1] - p[1]);
  const s1 = (a > 0 && b > 0) ? (a + b) / 7.0 : -1.0, s2 = (c > 0 && d > 0) ? (c + d) / 7.0 : -1.0;
  if (s1 > 0 && s2 > 0) return (s1 + s2) / 2.0;
  return s1 > 0 ? s1 : s2;
}
function dims(B, w, h, tl, tr, bl){
  const m1 = moduleOneWay(B, w, h, tl, tr), m2 = moduleOneWay(B, w, h, tl, bl);
  let ms;
  if (m1 > 0 && m2 > 0) ms = (m1 + m2) / 2.0; else if (m1 > 0 || m2 > 0) ms = m1 > 0 ? m1 : m2; else ms = (tl[2] + tr[2] + bl[2]) / 3.0;
  const d1 = Math.sqrt((tr[0] - tl[0]) * (tr[0] - tl[0]) + (tr[1] - tl[1]) * (tr[1] - tl[1]));
  const d2 = Math.sqrt((bl[0] - tl[0]) * (bl[0] - tl[0]) + (bl[1] - tl[1]) * (bl[1] - tl[1]));
  const dim = Math.floor((Math.floor(d1 / ms + 0.5) + Math.floor(d2 / ms + 0.5)) / 2) + 7, r = dim % 4;
  const base = r === 0 ? [dim + 1, dim - 3] : r === 2 ? [dim - 1, dim + 3] : r === 3 ? [dim - 2, dim + 2] : [dim, dim + 4, dim - 4];
  const out = [];
  for (const d of base.concat([base[0] + 4, base[0] - 4, base[0] + 8, base[0] - 8])) if (d >= 21 && d <= 177 && !out.includes(d)) out.push(d);
  return [ms, out.slice(0, 5)];
}
function alignRuns(B, w, h, x, y, dx, dy, ms){
  const lim = Math.trunc(ms * 2.5) + 2; let b = 0, i = x, j = y;
  const inb = () => i >= 0 && i < w && j >= 0 && j < h;
  while (inb() && B[j * w + i] && b <= lim){ b++; i -= dx; j -= dy; }
  let lb = 0;
  while (inb() && !B[j * w + i] && lb <= lim){ lb++; i -= dx; j -= dy; }
  if (!inb() || !B[j * w + i]) return null;
  let f = 0; i = x + dx; j = y + dy;
  while (inb() && B[j * w + i] && f <= lim){ f++; i += dx; j += dy; }
  let lf = 0;
  while (inb() && !B[j * w + i] && lf <= lim){ lf++; i += dx; j += dy; }
  if (!inb() || !B[j * w + i]) return null;
  const c = b + f, tol = ms * 0.6;
  if (Math.abs(c - ms) >= tol || Math.abs(lb - ms) >= tol || Math.abs(lf - ms) >= tol) return null;
  return 1.0 + f - c / 2.0;
}
function alignment(B, w, h, ex, ey, ms, radModules){
  const rad = radModules * ms;
  const x0 = Math.max(0, Math.trunc(ex - rad)), x1 = Math.min(w - 1, Math.trunc(ex + rad));
  const y0 = Math.max(0, Math.trunc(ey - rad)), y1 = Math.min(h - 1, Math.trunc(ey + rad));
  if (x1 - x0 < 3 * ms || y1 - y0 < 3 * ms) return [];
  const found = [], tol = ms * 0.6;
  for (let y = y0; y <= y1; y++){
    const o = y * w, sp = runs(B, o, x0, x1), n = sp.length / 2;
    let k = B[o + x0] ? 1 : 2;
    while (k + 3 < n){
      const c1 = sp[2 * k + 1] - sp[2 * k], c2 = sp[2 * k + 3] - sp[2 * k + 2], c3 = sp[2 * k + 5] - sp[2 * k + 4];
      if (Math.abs(c1 - ms) < tol && Math.abs(c2 - ms) < tol && Math.abs(c3 - ms) < tol){
        const ix = Math.trunc(x0 + sp[2 * k + 2] + c2 / 2.0), vr = alignRuns(B, w, h, ix, y, 0, 1, ms);
        if (vr !== null){
          const cy = y + vr, hr = alignRuns(B, w, h, ix, Math.trunc(cy), 1, 0, ms);
          if (hr !== null){
            const cx = ix + hr; let merged = false;
            for (const f of found){ if (Math.abs(f[0] - cx) <= ms && Math.abs(f[1] - cy) <= ms){ const m = f[2]; f[0] = (m * f[0] + cx) / (m + 1); f[1] = (m * f[1] + cy) / (m + 1); f[2] = m + 1; merged = true; break; } }
            if (!merged) found.push([cx, cy, 1]);
          }
        }
      }
      k += 2;
    }
  }
  const d2 = f => (f[0] - ex) * (f[0] - ex) + (f[1] - ey) * (f[1] - ey);
  found.sort((a, b) => d2(a) - d2(b));
  return found.slice(0, 4).map(f => [f[0], f[1]]);
}
function squareToQuad(x0, y0, x1, y1, x2, y2, x3, y3){
  const dx3 = x0 - x1 + x2 - x3, dy3 = y0 - y1 + y2 - y3;
  if (dx3 === 0.0 && dy3 === 0.0) return [x1 - x0, x2 - x1, x0, y1 - y0, y2 - y1, y0, 0.0, 0.0, 1.0];
  const dx1 = x1 - x2, dx2 = x3 - x2, dy1 = y1 - y2, dy2 = y3 - y2, den = dx1 * dy2 - dx2 * dy1;
  const g = (dx3 * dy2 - dx2 * dy3) / den, hh = (dx1 * dy3 - dx3 * dy1) / den;
  return [x1 - x0 + g * x1, x3 - x0 + hh * x3, x0, y1 - y0 + g * y1, y3 - y0 + hh * y3, y0, g, hh, 1.0];
}
function adjugate(m){ const [a, b, c, d, e, f, g, h, i] = m;
  return [e * i - f * h, c * h - b * i, b * f - c * e, f * g - d * i, a * i - c * g, c * d - a * f, d * h - e * g, b * g - a * h, a * e - b * d]; }
function matmul(p, q){
  return [p[0] * q[0] + p[1] * q[3] + p[2] * q[6], p[0] * q[1] + p[1] * q[4] + p[2] * q[7], p[0] * q[2] + p[1] * q[5] + p[2] * q[8],
          p[3] * q[0] + p[4] * q[3] + p[5] * q[6], p[3] * q[1] + p[4] * q[4] + p[5] * q[7], p[3] * q[2] + p[4] * q[5] + p[5] * q[8],
          p[6] * q[0] + p[7] * q[3] + p[8] * q[6], p[6] * q[1] + p[7] * q[4] + p[8] * q[7], p[6] * q[2] + p[7] * q[5] + p[8] * q[8]];
}
function quadToQuad(src, dst){
  const s = squareToQuad(src[0][0], src[0][1], src[1][0], src[1][1], src[2][0], src[2][1], src[3][0], src[3][1]);
  const t = squareToQuad(dst[0][0], dst[0][1], dst[1][0], dst[1][1], dst[2][0], dst[2][1], dst[3][0], dst[3][1]);
  return matmul(t, adjugate(s));
}
function apply(H, u, v){ const z = H[6] * u + H[7] * v + H[8]; return [(H[0] * u + H[1] * v + H[2]) / z, (H[3] * u + H[4] * v + H[5]) / z]; }
function sample(lum, w, h, T, r, H, dim, ms){
  const s = ms >= 4.5 ? 1 : 0, W = w + 1, mod = [];
  for (let j = 0; j < dim; j++){
    const v = j + 0.5, row = [];
    for (let i = 0; i < dim; i++){
      const u = i + 0.5, z = H[6] * u + H[7] * v + H[8];
      const x = Math.floor((H[0] * u + H[1] * v + H[2]) / z), y = Math.floor((H[3] * u + H[4] * v + H[5]) / z);
      if (x < 0 || y < 0 || x >= w || y >= h){ row.push(false); continue; }
      let box, barea;
      if (s){ const xa = Math.max(0, x - s), xb = Math.min(w, x + s + 1), ya = Math.max(0, y - s), yb = Math.min(h, y + s + 1);
        box = T[yb * W + xb] - T[ya * W + xb] - T[yb * W + xa] + T[ya * W + xa]; barea = (xb - xa) * (yb - ya); }
      else { box = lum[y * w + x]; barea = 1; }
      const xa = Math.max(0, x - r), xb = Math.min(w, x + r + 1), ya = Math.max(0, y - r), yb = Math.min(h, y + r + 1);
      const win = T[yb * W + xb] - T[ya * W + xb] - T[yb * W + xa] + T[ya * W + xa];
      row.push(box * (xb - xa) * (yb - ya) < win * barea);
    }
    mod.push(row);
  }
  return mod;
}
function solve(A, b){
  const n = b.length, M = []; for (let i = 0; i < n; i++) M.push(A[i].concat([b[i]]));
  for (let c = 0; c < n; c++){
    let p = c; for (let r = c + 1; r < n; r++) if (Math.abs(M[r][c]) > Math.abs(M[p][c])) p = r;
    if (Math.abs(M[p][c]) < 1e-12) return null;
    if (p !== c){ const t = M[c]; M[c] = M[p]; M[p] = t; }
    const piv = M[c][c];
    for (let r = c + 1; r < n; r++){ const f = M[r][c] / piv; if (f !== 0.0){ const Mr = M[r], Mc = M[c]; for (let k = c; k <= n; k++) Mr[k] -= f * Mc[k]; } }
  }
  const x = new Array(n).fill(0.0);
  for (let c = n - 1; c >= 0; c--){ let s = M[c][n]; for (let k = c + 1; k < n; k++) s -= M[c][k] * x[k]; x[c] = s / M[c][c]; }
  return x;
}
function fitLsq(pairs, dim){
  const n = pairs.length; let cx = 0, cy = 0, s = 0;
  for (const p of pairs) cx += p[2]; cx /= n;
  for (const p of pairs) cy += p[3]; cy /= n;
  for (const p of pairs) s += Math.sqrt((p[2] - cx) * (p[2] - cx) + (p[3] - cy) * (p[3] - cy));
  s = s / n || 1.0;
  const A = []; for (let i = 0; i < 8; i++) A.push(new Array(8).fill(0.0)); const b = new Array(8).fill(0.0);
  for (const p of pairs){
    const u = p[0] / dim, v = p[1] / dim, x = (p[2] - cx) / s, y = (p[3] - cy) / s;
    for (const [row, rhs] of [[[u, v, 1.0, 0.0, 0.0, 0.0, -u * x, -v * x], x], [[0.0, 0.0, 0.0, u, v, 1.0, -u * y, -v * y], y]]){
      for (let i = 0; i < 8; i++){ const ri = row[i]; if (ri !== 0.0){ const Ai = A[i]; for (let j = 0; j < 8; j++) Ai[j] += ri * row[j]; b[i] += ri * rhs; } }
    }
  }
  const hv = solve(A, b); if (hv === null) return null;
  const Hn = [hv[0] / dim, hv[1] / dim, hv[2], hv[3] / dim, hv[4] / dim, hv[5], hv[6] / dim, hv[7] / dim, 1.0];
  return matmul([s, 0.0, cx, 0.0, s, cy, 0.0, 0.0, 1.0], Hn);
}
function track(B, w, h, tl, tr, bl, ms, dim){
  const t = dim - 3.5, pairs = [[3.5, 3.5, tl[0], tl[1]], [t, 3.5, tr[0], tr[1]], [3.5, t, bl[0], bl[1]]];
  const bx = tr[0] - tl[0] + bl[0], by = tr[1] - tl[1] + bl[1];
  let H = quadToQuad([[3.5, 3.5], [t, 3.5], [t, t], [3.5, t]], [[tl[0], tl[1]], [tr[0], tr[1]], [bx, by], [bl[0], bl[1]]]);
  const al = align((dim - 17) / 4), n = al.length, order = [];
  for (let a = 0; a < n; a++) for (let b = 0; b < n; b++){ if ((a === 0 && b === 0) || (a === 0 && b === n - 1) || (a === n - 1 && b === 0)) continue; order.push([Math.max(a, b), a + b, al[a] + 0.5, al[b] + 0.5]); }
  order.sort((p, q) => (p[0] - q[0]) || (p[1] - q[1]) || (p[2] - q[2]) || (p[3] - q[3]));
  let found = 0;
  for (const [, , u, v] of order){
    const [px, py] = apply(H, u, v), [qx, qy] = apply(H, u + 1.0, v), [rx, ry] = apply(H, u, v + 1.0);
    const lms = (Math.sqrt((qx - px) * (qx - px) + (qy - py) * (qy - py)) + Math.sqrt((rx - px) * (rx - px) + (ry - py) * (ry - py))) / 2.0;
    const rad = (found || n > 2) ? 3.5 : Math.min(16.0, 4.0 + dim / 8.0);
    const hits = alignment(B, w, h, px, py, lms, rad);
    if (!hits.length) continue;
    pairs.push([u, v, hits[0][0], hits[0][1]]); found++;
    while (pairs.length >= 4){
      const H2 = fitLsq(pairs, dim);
      if (H2 === null){ pairs.pop(); found--; break; }
      let worst = 0.0, wi = -1;
      for (let k = 3; k < pairs.length; k++){ const [u2, v2, x2, y2] = pairs[k], [ex, ey] = apply(H2, u2, v2); const d = Math.sqrt((ex - x2) * (ex - x2) + (ey - y2) * (ey - y2)); if (d > worst){ worst = d; wi = k; } }
      if (pairs.length > 4 && worst > 0.75 * ms){ pairs.splice(wi, 1); found--; continue; }
      H = H2; break;
    }
  }
  return [H, found];
}
const FIXED = {};
function fixedPts(dim){
  if (FIXED[dim]) return FIXED[dim];
  const v = (dim - 17) / 4, pts = [];
  for (const [ox, oy] of [[0, 0], [dim - 7, 0], [0, dim - 7]]) for (let dy = -1; dy < 8; dy++) for (let dx = -1; dx < 8; dx++){
    const x = ox + dx, y = oy + dy;
    if (x >= 0 && x < dim && y >= 0 && y < dim){ const dark = dx >= 0 && dx <= 6 && dy >= 0 && dy <= 6 && Math.max(Math.abs(dx - 3), Math.abs(dy - 3)) !== 2; pts.push(x + 0.5, y + 0.5, dark ? 1 : 0); }
  }
  for (let i = 8; i < dim - 8; i++) pts.push(i + 0.5, 6.5, 1 - i % 2, 6.5, i + 0.5, 1 - i % 2);
  const al = align(v), n = al.length;
  for (let a = 0; a < n; a++) for (let b = 0; b < n; b++){
    if ((a === 0 && b === 0) || (a === 0 && b === n - 1) || (a === n - 1 && b === 0)) continue;
    for (let dy = -2; dy < 3; dy++) for (let dx = -2; dx < 3; dx++) pts.push(al[a] + dx + 0.5, al[b] + dy + 0.5, Math.max(Math.abs(dx), Math.abs(dy)) === 1 ? 0 : 1);
  }
  return (FIXED[dim] = pts);
}
function score(B, w, h, H, pts){
  const [a, b, c, d, e, f, g, hh, i] = H; let ok = 0;
  for (let k = 0; k < pts.length; k += 3){
    const u = pts[k], v = pts[k + 1], z = g * u + hh * v + i;
    const x = Math.floor((a * u + b * v + c) / z), y = Math.floor((d * u + e * v + f) / z);
    if (x >= 0 && x < w && y >= 0 && y < h && B[y * w + x] === pts[k + 2]) ok++;
  }
  return ok;
}
function homography(tl, tr, bl, px, py, dim){ const t = dim - 3.5, q = dim - 6.5; return quadToQuad([[3.5, 3.5], [t, 3.5], [q, q], [3.5, t]], [[tl[0], tl[1]], [tr[0], tr[1]], [px, py], [bl[0], bl[1]]]); }
const DIRS = [[1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [1, -1], [-1, 1], [-1, -1]];
function fit(B, w, h, tl, tr, bl, ms, dim){
  const pts = fixedPts(dim), npts = pts.length / 3.0, q = dim - 6.5; let best = null;
  if (dim >= 25){
    const Ht = track(B, w, h, tl, tr, bl, ms, dim)[0], st = score(B, w, h, Ht, pts);
    best = [st, Ht];
    if (st >= 0.85 * npts) return [Ht, st / npts, apply(Ht, q, q)];
  }
  const k = 1.0 - 3.0 / (dim - 7.0), ex = tl[0] + k * (tr[0] - tl[0] + bl[0] - tl[0]), ey = tl[1] + k * (tr[1] - tl[1] + bl[1] - tl[1]);
  let starts = [[ex, ey]];
  if (best !== null) starts.unshift(apply(best[1], q, q));
  if (dim >= 25) starts = starts.concat(alignment(B, w, h, ex, ey, ms, Math.min(16.0, 4.0 + dim / 8.0)));
  for (const [sx, sy] of starts){
    let px = sx, py = sy, s = score(B, w, h, homography(tl, tr, bl, px, py, dim), pts), step = 2.0 * ms, evals = 0;
    while (step >= 0.24 * ms && evals < 160){
      let bs = s, bx = px, by = py;
      for (const [dx, dy] of DIRS){ const qx = px + dx * step, qy = py + dy * step; const sc = score(B, w, h, homography(tl, tr, bl, qx, qy, dim), pts); evals++; if (sc > bs){ bs = sc; bx = qx; by = qy; } }
      if (bs > s){ s = bs; px = bx; py = by; } else step /= 2.0;
    }
    if (best === null || s > best[0]) best = [s, homography(tl, tr, bl, px, py, dim)];
  }
  return [best[1], best[0] / npts, apply(best[1], q, q)];
}
function quickScore(B, w, h, tl, tr, bl, dim){
  const k = 1.0 - 3.0 / (dim - 7.0), ex = tl[0] + k * (tr[0] - tl[0] + bl[0] - tl[0]), ey = tl[1] + k * (tr[1] - tl[1] + bl[1] - tl[1]);
  const pts = fixedPts(dim); return score(B, w, h, homography(tl, tr, bl, ex, ey, dim), pts) / (pts.length / 3.0);
}
function transpose(m){ return m[0].map((_, i) => m.map(r => r[i])); }
function tryGrid(lum, w, h, T, r, B, tl, tr, bl, ms, dim){
  const [H, frac, p4] = fit(B, w, h, tl, tr, bl, ms, dim);
  if (frac < 0.6) return [null, null];
  const mod = sample(lum, w, h, T, r, H, dim, ms), alt = [];
  for (let j = 0; j < dim; j++){ const row = []; for (let i = 0; i < dim; i++){ const p = apply(H, i + 0.5, j + 0.5), x = Math.floor(p[0]), y = Math.floor(p[1]); row.push(x >= 0 && x < w && y >= 0 && y < h && B[y * w + x] === 1); } alt.push(row); }
  let seen = null;
  for (const grid of [mod, transpose(mod), alt, transpose(alt)]){
    try { const res = decodeGrid(grid); res.points = [[tl[0], tl[1]], [tr[0], tr[1]], [bl[0], bl[1]], p4]; res.fit = frac; return [res, null]; }
    catch (e){ if (seen === null && dim >= 45) seen = versionInfo(grid); }
  }
  return [null, seen];
}
function* scanPassGen(lum, w, h, T, r, B, cands, st){
  for (const [tl, tr, bl] of triples(cands)){
    const [ms, ds] = dims(B, w, h, tl, tr, bl);
    const sc = new Map(ds.map(d => [d, -quickScore(B, w, h, tl, tr, bl, d)]));
    ds.sort((a, b) => sc.get(a) - sc.get(b));
    const done = new Set(), queue = ds.slice();
    while (queue.length){
      const dim = queue.shift(); if (done.has(dim)) continue; done.add(dim); st.attempts++;
      const [res, ver] = tryGrid(lum, w, h, T, r, B, tl, tr, bl, ms, dim);
      if (res !== null) return res;
      if (ver !== null && !done.has(17 + 4 * ver)) queue.unshift(17 + 4 * ver);
      yield 0;
    }
  }
  return null;
}
// scanGen: generator form of scan_luminance; yields between row bands / attempts so a caller can spread one
// frame over several animation frames. Returns the decode dict (+points, fit, offset) or null.
function* scanGen(lum, w, h, offsets, stats){
  offsets = offsets || OFFSETS; stats = stats || {};
  stats.finders = 0; stats.attempts = 0;
  if (w < 21 || h < 21 || lum.length < w * h) return null;
  const T = integral(lum, w, h), r = radius(w, h);
  yield 0;
  for (const off of offsets){
    const B = yield* binarizeGen(lum, w, h, T, r, off);
    const cands = yield* findersGen(B, w, h);
    stats.finders += cands.length;
    const res = yield* scanPassGen(lum, w, h, T, r, B, cands, stats);
    if (res !== null){ res.offset = off; return res; }
  }
  return null;
}
function runGen(g){ let s; do { s = g.next(); } while (!s.done); return s.value; }
function scanLuma(lum, w, h, offsets, stats){ return runGen(scanGen(lum, w, h, offsets, stats)); }
function scanImageData(id, offsets, stats){ return scanLuma(lumaFromImageData(id), id.width, id.height, offsets, stats); }
return {scanGen, scanLuma, scanImageData, lumaFromImageData, decodeGrid, decodeMatrix, matrixFromImageData, rsCorrect, rsCapacity, bchFormat, bchVersion, OFFSETS};
}
const QRV = qrVisionModule();
"""

_RECEIVER_JS = "\n'use strict';\n" + _QR_VISION_JS + r"""
const decodeMatrix = QRV.decodeMatrix, matrixFromImageData = QRV.matrixFromImageData;
// ---------- frames ----------
const CRC_T=(()=>{ const t=new Uint32Array(256); for(let n=0;n<256;n++){ let c=n; for(let k=0;k<8;k++) c = c&1 ? 0xEDB88320^(c>>>1) : c>>>1; t[n]=c>>>0; } return t; })();
function crc32(s){ let c=0xFFFFFFFF; for(let i=0;i<s.length;i++) c=CRC_T[(c^s.charCodeAt(i))&255]^(c>>>8); return ((c^0xFFFFFFFF)>>>0).toString(16).padStart(8,'0'); }
const RX=/^GCAG1\|([0-9a-f]{8})\|(\d{1,6})\|(\d{1,6})\|([A-Za-z0-9+\/=]*)\|([0-9a-f]{8})$/;
const st={sid:null,total:null,header:null,chunks:new Map(),bad:0,dup:0,foreign:0,conflict:0,done:false,blob:null};
function b64(s){ const r=atob(s); const u=new Uint8Array(r.length); for(let i=0;i<r.length;i++) u[i]=r.charCodeAt(i); return u; }
function same(a,b){ if(a.length!==b.length) return false; for(let i=0;i<a.length;i++) if(a[i]!==b[i]) return false; return true; }
function addFrame(text){
  text=(text||'').trim(); const m=RX.exec(text); if(!m){ st.bad++; return 'malformed'; }
  if(crc32(text.slice(0,text.lastIndexOf('|')))!==m[5]){ st.bad++; return 'bad-crc'; }
  const sid=m[1], seq=+m[2], total=+m[3]; let payload; try{ payload=b64(m[4]); }catch(e){ st.bad++; return 'malformed'; }
  if(st.sid===null) st.sid=sid;
  if(sid!==st.sid || (st.total!==null && total!==st.total)){ st.foreign++; return 'foreign-session'; }
  st.total=total;
  if(seq===0){ if(st.header){ st.dup++; return 'duplicate'; } try{ st.header=JSON.parse(new TextDecoder().decode(payload)); }catch(e){ st.bad++; return 'malformed'; } }
  else if(st.chunks.has(seq)){ if(!same(st.chunks.get(seq),payload)){ st.conflict++; return 'conflict'; } st.dup++; return 'duplicate'; }
  else st.chunks.set(seq,payload);
  render(); maybeFinish(); return seq===0?'header':'chunk';
}
function missing(){ if(st.total===null) return [0]; const out=st.header?[]:[0]; for(let k=1;k<=st.total;k++) if(!st.chunks.has(k)) out.push(k); return out; }
function ranges(a){ const out=[]; for(let i=0;i<a.length;){ let j=i; while(j+1<a.length&&a[j+1]===a[j]+1) j++; out.push(i===j?''+a[i]:a[i]+'-'+a[j]); i=j+1; } return out.join(','); }
function hex(buf){ return [...new Uint8Array(buf)].map(b=>b.toString(16).padStart(2,'0')).join(''); }
async function maybeFinish(){
  if(st.done || missing().length) return; st.done=true;
  const parts=[]; let size=0; for(let k=1;k<=st.total;k++){ parts.push(st.chunks.get(k)); size+=st.chunks.get(k).length; }
  const blob=new Uint8Array(size); let o=0; for(const p of parts){ blob.set(p,o); o+=p.length; }
  const out=document.getElementById('result');
  if(!(window.crypto && crypto.subtle)){ out.innerHTML='<span class="bad">This page is not in a secure context (open it from a file:// path or https), so SHA-256 cannot be checked. Not offering the bundle.</span>'; return; }
  const h=hex(await crypto.subtle.digest('SHA-256', blob));
  if(size!==st.header.size || h!==st.header.sha256){ out.innerHTML='<span class="bad">SHA-256 MISMATCH - the bundle is corrupt or frames from two senders were mixed. Reload and scan again.</span>'; st.done=false; return; }
  st.blob=blob;
  out.innerHTML='<span class="ok">Complete and verified</span> - '+size+' bytes, SHA-256 <code>'+h+'</code>';
  document.getElementById('dl').disabled=false;
  if(st.header.enc==='gcag-seal1') document.getElementById('decbox').hidden=false;
}
function save(bytes,name){ const a=document.createElement('a'); a.href=URL.createObjectURL(new Blob([bytes])); a.download=name; a.click(); }
document.getElementById('dl').onclick=()=>save(st.blob, st.header.name||'bundle.gcag');
document.getElementById('dec').onclick=async()=>{
  const msg=document.getElementById('decmsg');
  try{
    const b=st.blob, magic=10, it=new DataView(b.buffer,magic,4).getUint32(0), salt=b.slice(magic+4,magic+20), iv=b.slice(magic+20,magic+32), hdr=b.slice(0,magic+32);
    const pw=new TextEncoder().encode(document.getElementById('pw').value.normalize('NFC'));
    const base=await crypto.subtle.importKey('raw',pw,'PBKDF2',false,['deriveKey']);
    const key=await crypto.subtle.deriveKey({name:'PBKDF2',salt:salt,iterations:it,hash:'SHA-256'},base,{name:'AES-GCM',length:256},false,['decrypt']);
    const pt=await crypto.subtle.decrypt({name:'AES-GCM',iv:iv,additionalData:hdr,tagLength:128},key,b.slice(magic+32));
    msg.innerHTML='<span class="ok">Decrypted ('+pt.byteLength+' bytes).</span>'; save(new Uint8Array(pt),(st.header.name||'bundle').replace(/\.gcag$/,'')+'.decrypted');
  }catch(e){ msg.innerHTML='<span class="bad">Wrong passphrase or altered bundle.</span>'; }
};
function render(){
  const g=document.getElementById('grid'); const t=st.total||0;
  if(g.childElementCount!==t+1){ g.innerHTML=''; for(let k=0;k<=t;k++){ const c=document.createElement('span'); c.title=k===0?'header':'chunk '+k; g.appendChild(c);} }
  [...g.children].forEach((c,k)=>{ c.className=(k===0?!!st.header:st.chunks.has(k))?'have':''; });
  const miss=missing();
  document.getElementById('stat').textContent=(st.total===null?'waiting for frames':(st.chunks.size+' / '+st.total+' chunks'+(st.header?', header ok':', header missing')))+
    '  |  rejected '+st.bad+', duplicates '+st.dup+', other session '+st.foreign+(st.conflict?', CONFLICTS '+st.conflict:'');
  document.getElementById('miss').value=miss.length?ranges(miss):'';
}
// ---------- inputs ----------
let detector = null;
const camStats = {engine: '-', mode: '-', size: '', frames: 0, scanned: 0, read: 0, newChunks: 0, corrected: 0, fps: 0, ms: 0, last: '', running: false};
async function initDetector(){
  if(!('BarcodeDetector' in window)) return false;
  try{ const f=await BarcodeDetector.getSupportedFormats(); if(!f.includes('qr_code')) return false; detector=new BarcodeDetector({formats:['qr_code']}); return true; }catch(e){ return false; }
}
function workerMain(){
  self.onmessage = e => {
    const d = e.data, t0 = performance.now(); let res = null, err = null;
    if (d.ping){ self.postMessage({pong: true}); return; }
    try { res = QRV.scanImageData({width: d.w, height: d.h, data: new Uint8ClampedArray(d.buf)}, d.offsets); }
    catch (x){ err = String((x && x.message) || x); }
    self.postMessage({id: d.id, ms: performance.now() - t0, err: err,
      res: res && {text: res.text, version: res.version, ecl: res.ecl, corrected: res.corrected, points: res.points}});
  };
}
// A worker built from this page's own decoder source (no file, no network). Resolves null where workers are
// unavailable (then frames are decoded on the main thread in time-sliced chunks).
function makeWorker(){
  return new Promise(resolve => {
    let w = null, done = false;
    const finish = v => { if (!done){ done = true; resolve(v); } };
    try {
      const src = '"use strict";\n' + qrVisionModule.toString() + '\nconst QRV = qrVisionModule();\n(' + workerMain.toString() + ')();\n';
      w = new Worker(URL.createObjectURL(new Blob([src], {type: 'text/javascript'})));
      w.onerror = () => finish(null);
      w.onmessage = e => { if (e.data && e.data.pong) finish(w); };
      w.postMessage({ping: true});
      setTimeout(() => finish(null), 2000);
    } catch (e){ finish(null); }
  });
}
function overlay(points, ok, fw, fh){
  const c = document.getElementById('ovl'), v = document.getElementById('video');
  c.width = v.clientWidth; c.height = v.clientHeight;
  const x = c.getContext('2d'); x.clearRect(0, 0, c.width, c.height);
  if (!points) return;
  const sx = c.width / fw, sy = c.height / fh, [tl, tr, bl, p4] = points;
  x.strokeStyle = ok ? '#2fbf6a' : '#e0a030'; x.lineWidth = 3; x.beginPath();
  x.moveTo(tl[0] * sx, tl[1] * sy); x.lineTo(tr[0] * sx, tr[1] * sy); x.lineTo(p4[0] * sx, p4[1] * sy); x.lineTo(bl[0] * sx, bl[1] * sy); x.closePath(); x.stroke();
}
function camLine(){
  const s = camStats, miss = missing();
  document.getElementById('camstat').textContent = s.engine + ' | ' + s.size + ' | ' + s.fps.toFixed(1) + ' frames/s scanned' +
    (s.ms ? ', ' + s.ms.toFixed(0) + ' ms/frame' : '') + ' | QR read ' + s.read + ' (new ' + s.newChunks + '), codewords corrected ' + s.corrected +
    ' | ' + (st.total === null ? 'waiting for the first frame' : (miss.length ? 'missing ' + ranges(miss) : 'all frames received'));
}
function onDecoded(text, corrected){
  camStats.read++; camStats.corrected += corrected || 0;
  const r = addFrame(text); camStats.last = r;
  if (r === 'chunk' || r === 'header') camStats.newChunks++;
}
async function startCamera(){
  const info = document.getElementById('caminfo'), pick = document.getElementById('engine').value;
  const useBD = !!detector && (pick === 'auto' || pick === 'bd');
  if (pick === 'bd' && !detector){ info.innerHTML = '<span class="bad">This browser has no BarcodeDetector</span> - using the built-in decoder.'; }
  let s;
  try { s = await navigator.mediaDevices.getUserMedia({video: {facingMode: 'environment', width: {ideal: 1280}, height: {ideal: 720}}}); }
  catch (e){ info.innerHTML = '<span class="bad">Camera not available: ' + e.message + '</span>'; return; }
  const v = document.getElementById('video'); v.srcObject = s; document.getElementById('vwrap').hidden = false; await v.play();
  const worker = (useBD || pick === 'main') ? null : await makeWorker();
  camStats.engine = useBD ? 'BarcodeDetector' : (worker ? 'built-in decoder (worker)' : 'built-in decoder (main thread, sliced)');
  camStats.running = true;
  info.textContent = 'Scanning... keep the whole code in view; it may be tilted.';
  const grab = document.createElement('canvas'), gx = grab.getContext('2d', {willReadFrequently: true});
  let busy = false, lastT = -1, id = 0, job = null, k = 0, winStart = performance.now(), winCount = 0;
  const stop = () => { camStats.running = false; s.getTracks().forEach(t => t.stop()); info.textContent = 'Done - camera stopped.'; overlay(null); camLine(); };
  const finished = (res, ms, fw, fh) => {
    busy = false; camStats.scanned++; winCount++; camStats.ms = camStats.ms ? camStats.ms * 0.8 + ms * 0.2 : ms;
    if (res){ onDecoded(res.text, res.corrected); overlay(res.points, true, fw, fh); } else overlay(null);
  };
  if (worker) worker.onmessage = e => { const d = e.data; if (d.pong) return; finished(d.res, d.ms, d.w || job.w, d.h || job.h); };
  const tick = async () => {
    if (st.done){ stop(); return; }
    const now = performance.now();
    if (now - winStart >= 1000){ camStats.fps = winCount * 1000 / (now - winStart); winStart = now; winCount = 0; camLine(); }
    if (job && job.gen){                      // main-thread fallback: at most ~8 ms of decoding per animation frame
      const t0 = performance.now(); let r;
      do { r = job.gen.next(); } while (!r.done && performance.now() - t0 < 8);
      job.spent += performance.now() - t0;
      if (r.done){ const j = job; job = null; finished(r.value, j.spent, j.w, j.h); }
    } else if (!busy && v.readyState >= 2 && v.currentTime !== lastT){
      lastT = v.currentTime; camStats.frames++;
      if (useBD){
        busy = true; const t0 = performance.now();
        try { const found = await detector.detect(v); busy = false; camStats.scanned++; winCount++; camStats.ms = performance.now() - t0; for (const c of found) onDecoded(c.rawValue, 0); }
        catch (e){ busy = false; }
      } else {
        const vw = v.videoWidth, vh = v.videoHeight, sc = Math.min(1, 1280 / Math.max(vw, vh));
        const fw = Math.round(vw * sc), fh = Math.round(vh * sc);
        if (grab.width !== fw || grab.height !== fh){ grab.width = fw; grab.height = fh; }
        camStats.size = vw + 'x' + vh + (sc < 1 ? ' -> ' + fw + 'x' + fh : '');
        gx.drawImage(v, 0, 0, fw, fh);
        const img = gx.getImageData(0, 0, fw, fh), offs = [QRV.OFFSETS[k++ % QRV.OFFSETS.length]];
        busy = true;
        if (worker){ job = {w: fw, h: fh}; worker.postMessage({id: ++id, w: fw, h: fh, buf: img.data.buffer, offsets: offs}, [img.data.buffer]); }
        else { busy = false; job = {gen: QRV.scanGen(QRV.lumaFromImageData(img), fw, fh, offs), spent: 0, w: fw, h: fh}; }
      }
    }
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}
async function fromImage(file){
  const bmp = await createImageBitmap(file); const found = [];
  if (detector){ try { for (const c of await detector.detect(bmp)) found.push([c.rawValue, 0]); } catch (e){} }
  if (!found.length){
    const c = document.createElement('canvas'); c.width = bmp.width; c.height = bmp.height; const x = c.getContext('2d'); x.drawImage(bmp, 0, 0);
    const id = x.getImageData(0, 0, c.width, c.height);
    const r = QRV.scanImageData(id);
    if (r) found.push([r.text, r.corrected]);
    else { try { found.push([decodeMatrix(matrixFromImageData(id)), 0]); } catch (e){ log(file.name + ': no readable QR code (' + e.message + ')'); } }
  }
  for (const [t, cor] of found) log(file.name + ': ' + addFrame(t) + (cor ? ' (' + cor + ' codewords corrected)' : ''));
}
function log(s){ const l=document.getElementById('log'); l.textContent=(s+'\n'+l.textContent).slice(0,4000); }
async function files(list){ for(const f of list) await fromImage(f); }
const dz=document.getElementById('drop');
dz.addEventListener('dragover',e=>{e.preventDefault(); dz.classList.add('hot');});
dz.addEventListener('dragleave',()=>dz.classList.remove('hot'));
dz.addEventListener('drop',e=>{e.preventDefault(); dz.classList.remove('hot'); files(e.dataTransfer.files);});
document.getElementById('pick').onchange=e=>files(e.target.files);
document.getElementById('cam').onclick=()=>{ if (!camStats.running) startCamera(); };
initDetector().then(ok=>{ document.getElementById('det').innerHTML = (ok ? '<span class="ok">BarcodeDetector available</span>' : '<span class="muted">No BarcodeDetector in this browser</span>') +
  ' &middot; built-in camera decoder: adaptive threshold, perspective correction, Reed-Solomon error correction'; });
render();
"""

_RECEIVER_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Airgap Receiver</title><style>__STYLE__
#drop{border:2px dashed var(--line);border-radius:8px;padding:22px;text-align:center}#drop.hot{border-color:var(--accent)}
#grid{display:flex;flex-wrap:wrap;gap:2px;margin:8px 0}#grid span{width:10px;height:10px;border-radius:2px;background:var(--line)}#grid span.have{background:var(--ok)}
#vwrap{position:relative;margin-top:8px}#video{display:block;width:100%;max-height:60vh;object-fit:fill;background:#000;border-radius:8px}
#ovl{position:absolute;left:0;top:0;width:100%;height:100%;pointer-events:none}#camstat{font:12px ui-monospace,Consolas,monospace;margin-top:6px}#log{white-space:pre-wrap;font:12px ui-monospace,Consolas,monospace;max-height:10em;overflow:auto;color:var(--muted)}
</style></head><body><main>
<h1>Stark Covenant airgap receiver</h1>
<div id="det" class="muted"></div>
<div class="panel"><div class="bar"><button class="primary" id="cam">Scan with camera</button>
<label>Decoder <select id="engine"><option value="auto">auto</option><option value="builtin">built-in</option><option value="main">built-in, main thread</option><option value="bd">BarcodeDetector</option></select></label>
<span id="caminfo" class="muted"></span></div>
<div id="vwrap" hidden><video id="video" playsinline muted></video><canvas id="ovl" aria-hidden="true"></canvas></div>
<div id="camstat" class="muted" aria-live="polite"></div></div>
<div class="panel" id="drop">Drop QR frame PNGs here, or <label style="text-decoration:underline;cursor:pointer">choose files<input id="pick" type="file" accept="image/png,image/*" multiple hidden></label></div>
<div class="panel"><div id="stat"></div><div id="grid" aria-label="received chunks"></div>
<div class="bar"><label>Missing (type these into the sender's "Resend only" box) <input id="miss" readonly style="width:16em"></label></div>
<div id="result" class="muted">Nothing verified yet.</div>
<div class="bar"><button id="dl" disabled>Download verified bundle</button></div>
<div id="decbox" hidden class="bar"><input id="pw" type="password" placeholder="passphrase" autocomplete="off"><button id="dec">Decrypt in this browser</button><span id="decmsg"></span></div></div>
<div id="log"></div>
</main><script>__JS__</script></body></html>"""


def receiver_html():
    return _RECEIVER_HTML.replace("__STYLE__", _STYLE).replace("__JS__", _RECEIVER_JS)


def write_frame_pngs(frames, outdir, scale=6, ecl="M"):
    os.makedirs(outdir, exist_ok=True)
    paths = []
    sid = parse_frame(frames[0])[0]
    for k, f in enumerate(frames):
        mod, _v, _m = qr_encode(f.encode("ascii"), ecl)
        p = os.path.join(outdir, "gcag_%s_%05d.png" % (sid, k))
        with open(p, "wb") as fh:
            fh.write(png_from_matrix(mod, scale))
        paths.append(p)
    return paths


# ======================================================================================================
# CLI
# ======================================================================================================

def _main(argv):
    import argparse
    import getpass
    ap = argparse.ArgumentParser(description="Stark Covenant optical airgap (QR frames)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("send", help="make the sender page (and optional PNG frames) for a file")
    s.add_argument("file")
    s.add_argument("--out", default="airgap_sender.html")
    s.add_argument("--seal", action="store_true", help="encrypt with a passphrase first (prompted)")
    s.add_argument("--chunk", type=int, default=DEFAULT_CHUNK)
    s.add_argument("--fps", type=int, default=5)
    s.add_argument("--pngs", help="also write one PNG per frame into this folder")
    r = sub.add_parser("receiver", help="write the receiver page")
    r.add_argument("--out", default="airgap_receiver.html")
    d = sub.add_parser("decode-pngs", help="reassemble a bundle from PNG frames")
    d.add_argument("folder")
    d.add_argument("--out", required=True)
    ns = ap.parse_args(argv)
    if ns.cmd == "send":
        with open(ns.file, "rb") as f:
            blob = f.read()
        name = os.path.basename(ns.file)
        if ns.seal:
            pw = getpass.getpass("Passphrase: ")
            if pw != getpass.getpass("Repeat: "):
                print("passphrases differ")
                return 2
            blob = seal(blob, pw)
            name += ".gcag"
        frames = encode_frames(blob, name, ns.chunk)
        with open(ns.out, "w", encoding="utf-8") as f:
            f.write(sender_html(frames, name, ns.fps))
        print("wrote %s: %d frames (%d bytes)" % (ns.out, len(frames), len(blob)))
        if ns.pngs:
            print("wrote %d PNGs to %s" % (len(write_frame_pngs(frames, ns.pngs)), ns.pngs))
        return 0
    if ns.cmd == "receiver":
        with open(ns.out, "w", encoding="utf-8") as f:
            f.write(receiver_html())
        print("wrote", ns.out)
        return 0
    ra = Reassembler()
    for nm in sorted(os.listdir(ns.folder)):
        if nm.lower().endswith(".png"):
            with open(os.path.join(ns.folder, nm), "rb") as f:
                try:
                    print(nm, ra.add(decode_png(f.read())))
                except ValueError as e:
                    print(nm, "unreadable:", e)
    try:
        blob, hdr = ra.result()
    except IntegrityError as e:
        print("FAILED:", e)
        return 1
    with open(ns.out, "wb") as f:
        f.write(blob)
    print("verified %s (%d bytes, sha256 %s) -> %s" % (hdr["name"], len(blob), hdr["sha256"], ns.out))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
