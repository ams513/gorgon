"""Offline lip-sync: viseme timing per voice line from the audio (energy + spectral heuristics), refined by the
line's text when it is known (English letter-to-sound rules + DTW alignment to the audio frames).

Mouth set (Preston Blair / Rhubarb style, 9 shapes):
  A closed (M B P)   B teeth (most consonants, EE IH)   C open (EH AE)   D wide open (AA)   E rounded (AO ER)
  F pucker (OO OW W)  G lip-teeth (F V)  H tongue (L TH)  X rest
Output: keyframes [(t_seconds, shape, open 0..1)] and a compact track {'d': [ms deltas], 'v': 'XDC..', 'o': '0..9'}
that the runtime maps to jaw/mouth blendshapes (3D) or a mouth sprite index (2D). No model, no network.
"""
from __future__ import annotations

import math
import re

import numpy as np

from . import dsp

SR = dsp.SR
SHAPES = 'ABCDEFGHX'
OPEN = {'X': 0.0, 'A': 0.0, 'B': 0.15, 'C': 0.45, 'D': 0.8, 'E': 0.4, 'F': 0.2, 'G': 0.12, 'H': 0.3}
SPRITE_INDEX = {s: i for i, s in enumerate(SHAPES)}
BLENDSHAPES = {
    'X': {}, 'A': {'mouthClose': 1.0, 'mouthPressLeft': 0.5, 'mouthPressRight': 0.5},
    'B': {'jawOpen': 0.15, 'mouthStretchLeft': 0.3, 'mouthStretchRight': 0.3},
    'C': {'jawOpen': 0.4, 'mouthStretchLeft': 0.2, 'mouthStretchRight': 0.2}, 'D': {'jawOpen': 0.75},
    'E': {'jawOpen': 0.35, 'mouthFunnel': 0.5}, 'F': {'jawOpen': 0.15, 'mouthPucker': 0.85, 'mouthFunnel': 0.3},
    'G': {'jawOpen': 0.1, 'mouthRollLower': 0.6, 'mouthUpperUpLeft': 0.3, 'mouthUpperUpRight': 0.3},
    'H': {'jawOpen': 0.3, 'tongueOut': 0.15},
}
HOP = 0.01

# ---------------------------------------------------------------------------------------------------------------
# text -> expected shapes (letter-to-sound heuristics, English)
# ---------------------------------------------------------------------------------------------------------------
_DIGRAPHS = [('tch', ['B'], 0.8), ('igh', ['D', 'B'], 1.4), ('ough', ['E', 'F'], 1.4), ('th', ['H'], 0.7),
             ('sh', ['B'], 0.8), ('ch', ['B'], 0.8), ('ph', ['G'], 0.7), ('wh', ['F'], 0.6), ('ng', ['B'], 0.6),
             ('ck', ['B'], 0.6), ('qu', ['B', 'F'], 0.9), ('oo', ['F'], 1.3), ('ou', ['E', 'F'], 1.4),
             ('ow', ['E', 'F'], 1.4), ('ee', ['B'], 1.3), ('ea', ['B'], 1.3), ('ai', ['C'], 1.3), ('ay', ['C'], 1.3),
             ('oa', ['E'], 1.3), ('au', ['E'], 1.2), ('aw', ['E'], 1.2), ('er', ['E'], 1.0), ('ur', ['E'], 1.0),
             ('ir', ['E'], 1.0), ('ar', ['D'], 1.2), ('or', ['E'], 1.2), ('oy', ['E', 'B'], 1.3), ('oi', ['E', 'B'], 1.3)]
_LETTER = {'a': ('C', 1.0), 'e': ('C', 0.9), 'i': ('B', 0.8), 'o': ('E', 1.0), 'u': ('C', 0.9), 'y': ('B', 0.7),
           'b': ('A', 0.5), 'm': ('A', 0.7), 'p': ('A', 0.5), 'f': ('G', 0.7), 'v': ('G', 0.6), 'l': ('H', 0.6),
           'w': ('F', 0.6), 'r': ('B', 0.5)}


def text_shapes(text):
    """-> list of (shape, weight, optional) units with optional 'X' pauses between words."""
    units = [('X', 0.0, True)]
    words = re.findall(r"[A-Za-z']+|[.!?,;:]", str(text or ''))
    for w in words:
        if w in '.!?,;:':
            units.append(('X', 0.0, True))
            continue
        s = w.lower().replace("'", '')
        if len(s) > 2 and s.endswith('e') and s[-2] not in 'aeiouy':
            s = s[:-1]                                   # silent final e
        i = 0
        while i < len(s):
            for dg, shp, wt in _DIGRAPHS:
                if s.startswith(dg, i):
                    for k, sh in enumerate(shp):
                        units.append((sh, wt / len(shp), False))
                    i += len(dg)
                    break
            else:
                c = s[i]
                sh, wt = _LETTER.get(c, ('B', 0.55))
                if units and units[-1][0] == sh and not units[-1][2] and sh != 'A':
                    units[-1] = (sh, units[-1][1] + wt * 0.6, False)
                else:
                    units.append((sh, wt, False))
                i += 1
        units.append(('X', 0.0, True))
    return units


# ---------------------------------------------------------------------------------------------------------------
# audio -> frame classes
# ---------------------------------------------------------------------------------------------------------------
CLASSES = ('sil', 'closure', 'nasal', 'fric', 'front', 'mid', 'open', 'round')
_COST = {  # expected shape -> cost per frame class (0 good .. 1 bad)
    'A': {'closure': 0.0, 'nasal': 0.2, 'sil': 0.35, 'round': 0.6},
    'B': {'fric': 0.0, 'front': 0.1, 'nasal': 0.4, 'mid': 0.5, 'closure': 0.5},
    'C': {'mid': 0.0, 'open': 0.2, 'front': 0.4, 'round': 0.6, 'nasal': 0.6},
    'D': {'open': 0.0, 'mid': 0.3, 'round': 0.6, 'front': 0.7},
    'E': {'round': 0.1, 'mid': 0.2, 'open': 0.4, 'nasal': 0.5, 'front': 0.6},
    'F': {'round': 0.0, 'nasal': 0.4, 'mid': 0.5, 'closure': 0.6, 'front': 0.7},
    'G': {'fric': 0.1, 'closure': 0.4, 'front': 0.5, 'sil': 0.6},
    'H': {'nasal': 0.2, 'mid': 0.3, 'front': 0.4, 'fric': 0.5, 'open': 0.5, 'round': 0.5},
    'X': {'sil': 0.0, 'closure': 0.3},
}
_DEFAULT_SHAPE = {'sil': 'X', 'closure': 'A', 'nasal': 'A', 'fric': 'B', 'front': 'B', 'mid': 'C', 'open': 'D', 'round': 'F'}


def frames(x):
    """Per-10 ms features: level (dB), voiced flag, F1/F2 estimates from a smoothed spectrum, high-band ratio."""
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 2:
        x = x.mean(axis=0)
    hop = int(HOP * SR)
    win = int(0.03 * SR)
    n = max(0, (len(x) - win) // hop + 1)
    if n == 0:
        return None
    idx = np.arange(n)[:, None] * hop + np.arange(win)[None, :]
    fr = x[idx] * np.hanning(win)
    pre = np.concatenate([x[:1], x[1:] - 0.95 * x[:-1]])      # +6 dB/oct pre-emphasis for formant picking
    frp = pre[idx] * np.hanning(win)
    N = 2048
    spec = np.abs(np.fft.rfft(fr, N, axis=1)) + 1e-9
    f = np.fft.rfftfreq(N, 1.0 / SR)
    pw = spec ** 2
    lvl = 10 * np.log10(pw[:, (f > 80) & (f < 8000)].sum(axis=1) + 1e-12)
    hi = pw[:, (f >= 3500) & (f < 9000)].sum(axis=1)
    lo = pw[:, (f >= 80) & (f < 3500)].sum(axis=1)
    hr = hi / (hi + lo + 1e-12)
    # smoothed, pre-emphasised log spectrum (cepstral lifter); F1/F2 = first two envelope peaks
    ls = np.log(np.abs(np.fft.rfft(frp, N, axis=1)) + 1e-9)
    cep = np.fft.irfft(ls, N, axis=1)
    lift = 100                                                # ~2 ms: resolves formants, drops harmonics (f0 < 480 Hz)
    cep[:, lift:N - lift] = 0
    env = np.fft.rfft(cep, N, axis=1).real
    band = (f >= 200) & (f <= 3200)
    fb = f[band]
    eb = env[:, band]
    peak = (eb[:, 1:-1] > eb[:, :-2]) & (eb[:, 1:-1] >= eb[:, 2:])
    f1 = np.full(n, 500.0)
    f2 = np.full(n, 1500.0)
    for i in range(n):
        pk = np.nonzero(peak[i])[0] + 1
        if not pk.size:
            continue
        pf = fb[pk]
        lo = pk[pf < 1000]
        if lo.size:
            j1 = lo[np.argmax(eb[i, lo])]
            f1[i] = fb[j1]
        hi = pk[(pf > f1[i] + 250) & (pf < 3000)]
        if hi.size:
            f2[i] = fb[hi[np.argmax(eb[i, hi])]]
    # voicing: normalised autocorrelation peak in 70..400 Hz
    seg = fr - fr.mean(axis=1, keepdims=True)
    S = np.fft.rfft(seg, N, axis=1)
    ac = np.fft.irfft(np.abs(S) ** 2, N, axis=1)[:, :int(SR / 60)]
    ac /= ac[:, :1] + 1e-12
    lag0, lag1 = int(SR / 400), int(SR / 70)
    voiced = ac[:, lag0:lag1].max(axis=1)
    return {'lvl': lvl, 'hr': hr, 'f1': f1, 'f2': f2, 'voiced': voiced, 'n': n}


def classify(F):
    lvl = F['lvl']
    top = np.percentile(lvl, 95)
    floor = np.percentile(lvl, 10)
    thr = max(floor + 9, top - 40)
    speech = lvl > thr
    out = []
    n = F['n']
    for i in range(n):
        if not speech[i]:
            out.append('sil')
            continue
        v = F['voiced'][i] > 0.42
        if not v and F['hr'][i] > 0.35:
            out.append('fric')
            continue
        if lvl[i] < top - 22:
            out.append('nasal' if v else 'closure')
            continue
        f1, f2 = F['f1'][i], F['f2'][i]
        if f1 > 640:
            out.append('open')
        elif f1 < 420 and f2 > 1750:
            out.append('front')
        elif f2 < 1150:
            out.append('round')
        else:
            out.append('mid')
    # short dips inside speech are lip closures (plosives / m)
    for i in range(1, n - 1):
        if out[i] == 'sil':
            j = i
            while j < n and out[j] == 'sil':
                j += 1
            if j < n and i > 0 and out[i - 1] != 'sil' and (j - i) * HOP <= 0.09:
                for k in range(i, j):
                    out[k] = 'closure'
    return out, speech


def _align(cls, units):
    """DTW of expected units onto frame classes; optional pause units may be skipped."""
    n, m = len(cls), len(units)
    INF = 1e18
    cost = np.array([[_COST[u[0]].get(c, 1.0) for u in units] for c in cls])
    D = np.full((n + 1, m + 1), INF)
    B = np.zeros((n + 1, m + 1), np.int8)
    D[0, 0] = 0.0
    opt = np.array([u[2] for u in units])
    for i in range(1, n + 1):
        ci = cost[i - 1]
        for j in range(1, m + 1):
            stay = D[i - 1, j] + 0.01
            adv = D[i - 1, j - 1]
            skip = D[i - 1, j - 2] if (j >= 2 and opt[j - 2]) else INF
            best = stay
            bk = 0
            if adv < best:
                best, bk = adv, 1
            if skip < best:
                best, bk = skip, 2
            D[i, j] = best + ci[j - 1]
            B[i, j] = bk
    # end: last unit, or the one before an optional final pause
    j = m if D[n, m] <= (D[n, m - 1] if m > 1 and opt[m - 1] else INF) else m - 1
    path = [0] * n
    i = n
    while i > 0:
        path[i - 1] = j - 1
        bk = B[i, j]
        i -= 1
        if bk == 1:
            j -= 1
        elif bk == 2:
            j -= 2
        j = max(1, j)
    return path


def visemes(x, text=None, lead=0.04, min_dur=0.04):
    """Keyframes [(t, shape, open)] for a mono voice line. text (optional) guides the shapes; audio sets timing."""
    F = frames(x)
    if F is None:
        return [(0.0, 'X', 0.0)]
    cls, speech = classify(F)
    n = F['n']
    if text and re.search(r'[A-Za-z]', text):
        units = text_shapes(text)
        path = _align(cls, units)
        shapes = []
        for i in range(n):
            u = units[path[i]][0]
            if cls[i] == 'sil' and u != 'A':
                u = 'X'
            if cls[i] == 'closure' and u in 'CDE':
                u = 'A' if (i > 0 and shapes and shapes[-1] == 'A') else u
            shapes.append(u)
    else:
        shapes = [_DEFAULT_SHAPE[c] for c in cls]
    lvl = F['lvl']
    top = np.percentile(lvl, 95)
    opn = np.clip((lvl - (top - 20)) / 20.0, 0, 1)
    keys = []
    for i, s in enumerate(shapes):
        t = max(0.0, i * HOP + 0.015 - lead)
        o = float(opn[i]) if s not in 'AX' else 0.0
        if keys and keys[-1][1] == s:
            keys[-1] = (keys[-1][0], s, max(keys[-1][2], o))
            continue
        keys.append((round(t, 3), s, round(o, 2)))
    # merge keys shorter than min_dur into their predecessor
    out = []
    for k, key in enumerate(keys):
        nxt = keys[k + 1][0] if k + 1 < len(keys) else key[0] + 1.0
        if out and nxt - key[0] < min_dur and key[1] != 'A':
            continue
        out.append(key)
    if not out or out[0][0] > 0:
        out.insert(0, (0.0, 'X', 0.0))
    if out[-1][1] != 'X':
        out.append((round(n * HOP + 0.06, 3), 'X', 0.0))
    return out


def compact(keys):
    d, v, o, prev = [], [], [], 0
    for t, s, op in keys:
        ms = int(round(t * 1000))
        d.append(ms - prev)
        prev = ms
        v.append(s)
        o.append(str(int(round(min(1.0, max(0.0, op)) * 9))))
    return {'d': d, 'v': ''.join(v), 'o': ''.join(o)}


def expand(track):
    keys, t = [], 0
    for dd, s, o in zip(track['d'], track['v'], track['o']):
        t += dd
        keys.append((t / 1000.0, s, int(o) / 9.0))
    return keys


def shape_at(keys, t):
    cur = keys[0]
    for k in keys:
        if k[0] <= t:
            cur = k
        else:
            break
    return cur


def text_only(text, dur=None, cps=14.0):
    """Viseme keys from text alone (placeholder voices / no recording): evenly paced by weight."""
    units = [u for u in text_shapes(text)]
    total = sum(u[1] for u in units) or 1.0
    dur = dur or max(0.8, len(str(text or '')) / cps)
    t, keys = 0.0, [(0.0, 'X', 0.0)]
    for s, w, opt in units:
        if w <= 0:
            continue
        keys.append((round(t, 3), s, OPEN[s] and 0.7))
        t += dur * w / total
    keys.append((round(t + 0.05, 3), 'X', 0.0))
    return keys
