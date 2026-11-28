"""Subtitles: chunk a line's text into readable captions and time them to the recorded audio.

chunk(text)                    -> caption chunks (<= 2 lines of 42 characters, broken at sentence/clause/word)
speech_segments(x)             -> [(t0, t1)] voiced regions (energy VAD with hangover)
auto_time(text, x=None, dur=None) -> [[t0, t1, text], ...] seconds; no audio -> timed at a natural reading pace
to_srt(entries) / to_vtt(entries)

Timing rules (broadcast practice): at least 0.8 s on screen, at most ~20 characters per second, a small gap
between consecutive chunks, chunk boundaries snapped to pauses in the speech when one is close.
"""
from __future__ import annotations

import re

import numpy as np

from . import dsp

SR = dsp.SR
MAX_LINE = 42
MAX_CPS = 20.0
MIN_DUR = 0.8
GAP = 0.08


def _wrap2(words, width=MAX_LINE):
    """Wrap words to lines; when two lines suffice, balance them (broadcast style) instead of filling greedily."""
    lines = _greedy(words, width)
    if len(lines) == 2:
        best = None
        for i in range(1, len(words)):
            a, b = ' '.join(words[:i]), ' '.join(words[i:])
            if len(a) <= width and len(b) <= width:
                d = abs(len(a) - len(b)) + (0 if len(b) >= len(a) else 2)      # slightly prefer a longer bottom line
                if best is None or d < best[0]:
                    best = (d, [a, b])
        if best:
            return best[1]
    return lines


def _greedy(words, width):
    lines, cur = [], ''
    for w in words:
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= width:
            cur += ' ' + w
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def chunk(text, width=MAX_LINE, lines=2):
    """Split text into chunks that wrap to at most `lines` lines of `width` characters. Sentences never share a
    chunk unless both are short; inside a sentence the break prefers a clause mark (, ; : -) past 40 %."""
    text = re.sub(r'\s+', ' ', str(text or '')).strip()
    if not text:
        return []
    fits = lambda ws: len(_wrap2(ws, width)) <= lines           # noqa: E731
    out = []
    for s in re.split(r'(?<=[.!?…])\s+', text):
        cur = []
        for w in s.split(' '):
            if fits(cur + [w]):
                cur.append(w)
                continue
            if not cur:                                        # one enormous word: hard-wrap it
                out.append(w)
                continue
            k = max([i for i, x in enumerate(cur) if x[-1:] in ',;:—-' and i >= len(cur) * 0.4] or [-1])
            if k >= 0:
                out.append(' '.join(cur[:k + 1]))
                cur = cur[k + 1:] + [w]
                if not fits(cur):
                    out.append(' '.join(cur[:-1]))
                    cur = [w]
            else:
                out.append(' '.join(cur))
                cur = [w]
        if cur:
            out.append(' '.join(cur))
            if len(out) >= 2 and len(out[-1]) < 15 and out[-2] and out[-2][-1:] not in '.!?':
                ws = (out[-2] + ' ' + out[-1]).split(' ')   # rebalance an orphaned tail with its neighbour
                best = None
                for i in range(1, len(ws)):
                    a, b = ws[:i], ws[i:]
                    if fits(a) and fits(b):
                        d = abs(len(' '.join(a)) - len(' '.join(b)))
                        if best is None or d < best[0]:
                            best = (d, i)
                if best:
                    out[-2:] = [' '.join(ws[:best[1]]), ' '.join(ws[best[1]:])]
    merged = []
    for c in out:
        if merged and len(c) < 12 and fits((merged[-1] + ' ' + c).split(' ')):
            merged[-1] += ' ' + c
        else:
            merged.append(c)
    return ['\n'.join(_wrap2(c.split(' '), width)) for c in merged]


def speech_segments(x, floor_db=None, hang=0.18, min_len=0.06):
    """Voiced regions of a mono line: 10 ms RMS above an adaptive threshold, with hangover."""
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 2:
        x = x.mean(axis=0)
    hop = int(0.01 * SR)
    n = len(x) // hop
    if n < 2:
        return []
    fr = x[:n * hop].reshape(n, hop)
    rms = 20 * np.log10(np.sqrt((fr ** 2).mean(axis=1)) + 1e-9)
    top = np.percentile(rms, 95)
    noise = np.percentile(rms, 10)
    thr = max(noise + 9.0, top - 38.0) if floor_db is None else floor_db
    act = rms > thr
    h = int(hang / 0.01)
    on = np.zeros(n, bool)
    last = -10 ** 9
    for i in range(n):
        if act[i]:
            last = i
        on[i] = i - last <= h
    segs = []
    i = 0
    while i < n:
        if on[i]:
            j = i
            while j < n and on[j]:
                j += 1
            t0, t1 = i * 0.01, min(n, j - h + 1) * 0.01
            if t1 - t0 >= min_len:
                segs.append((round(t0, 3), round(max(t1, t0 + min_len), 3)))
            i = j
        else:
            i += 1
    return segs


def _pauses(segs):
    return [((a[1] + b[0]) / 2.0, b[0] - a[1]) for a, b in zip(segs, segs[1:]) if b[0] - a[1] >= 0.12]


def auto_time(text, x=None, dur=None, width=MAX_LINE):
    """Time caption chunks to the audio: characters map onto voiced time; boundaries snap to nearby pauses."""
    chunks = chunk(text, width)
    if not chunks:
        return []
    flat = [c.replace('\n', ' ') for c in chunks]
    if x is None:
        t, out = 0.1, []
        for c in flat:
            d = max(MIN_DUR, len(c) / 15.0)
            out.append([round(t, 3), round(t + d, 3), c])
            t += d + GAP
        return out
    segs = speech_segments(x)
    total = (len(x) if np.asarray(x).ndim == 1 else np.asarray(x).shape[-1]) / float(SR)
    if dur:
        total = dur
    if not segs:
        segs = [(0.0, total)]
    voiced = sum(b - a for a, b in segs)
    chars = [max(1, len(c)) for c in flat]
    csum = np.cumsum([0] + chars) / float(sum(chars))

    def v2t(frac):                    # fraction of voiced time -> absolute time
        target = frac * voiced
        acc = 0.0
        for a, b in segs:
            if acc + (b - a) >= target:
                return a + (target - acc)
            acc += b - a
        return segs[-1][1]
    bounds = [v2t(f) for f in csum]
    pauses = _pauses(segs)
    for k in range(1, len(bounds) - 1):
        near = [p for p in pauses if abs(p[0] - bounds[k]) < 0.35]
        if near:
            bounds[k] = min(near, key=lambda p: abs(p[0] - bounds[k]))[0]
    out = []
    for k, c in enumerate(flat):
        t0, t1 = bounds[k], bounds[k + 1]
        need = max(MIN_DUR, len(c) / MAX_CPS)
        if t1 - t0 < need:
            nxt = bounds[k + 2] - GAP if k + 2 < len(bounds) else total + 0.6
            t1 = min(max(t1, t0 + need), max(t1, nxt))
        if out and t0 < out[-1][1] + GAP:
            out[-1][1] = round(max(out[-1][0] + 0.3, t0 - GAP), 3)
        out.append([round(max(0.0, t0 - 0.05), 3), round(t1 + 0.15, 3), c])
    for k in range(len(out) - 1):
        if out[k][1] > out[k + 1][0] - GAP:
            out[k][1] = round(out[k + 1][0] - GAP, 3)
    return out


def _ts(t, sep=','):
    t = max(0.0, t)
    h, r = divmod(t, 3600)
    m, s = divmod(r, 60)
    return '%02d:%02d:%02d%s%03d' % (h, m, int(s), sep, int(round((s - int(s)) * 1000)) % 1000)


def to_srt(entries):
    return ''.join('%d\n%s --> %s\n%s\n\n' % (i + 1, _ts(a), _ts(b), t) for i, (a, b, t) in enumerate(entries))


def to_vtt(entries):
    return 'WEBVTT\n\n' + ''.join('%s --> %s\n%s\n\n' % (_ts(a, '.'), _ts(b, '.'), t) for a, b, t in entries)
