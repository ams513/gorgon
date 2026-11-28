"""Listen-check tools: measure what a render really sounds like, and say it in plain words.

ebur128(x)            ffmpeg's EBU R128 meter (integrated LUFS, loudness range, true peak) on an array or a file
features(x)           peak, RMS, crest, DC, clipped samples, spectral centroid/rolloff/flatness, band balance,
                      attack/decay, stereo width, onset rate, pitch estimate
loop_seam(x, s, e)    continuity of a loop wrap compared with ordinary sample-to-sample movement
describe(f, kind)     a short plain-words description built from the features
check(x, ...)         one dict: features + ebur128 + sanity verdicts (no clipping, no DC, audible band, finite)
"""
from __future__ import annotations

import math
import re
import subprocess

import numpy as np

from . import dsp

SR = dsp.SR
BANDS = (('sub', 20, 60), ('low', 60, 250), ('lowmid', 250, 1000), ('mid', 1000, 4000), ('high', 4000, 10000),
         ('air', 10000, 20000))


def ebur128(x_or_path, timeout=180):
    """-> {'I': LUFS, 'LRA': LU, 'TP': dBTP, 'thresh': LUFS} from ffmpeg's ebur128 filter (None without ffmpeg)."""
    ff = dsp.ffmpeg_bin()
    if not ff:
        return None
    if isinstance(x_or_path, str):
        cmd = [ff, '-hide_banner', '-nostats', '-nostdin', '-i', x_or_path,
               '-af', 'ebur128=peak=true:framelog=quiet', '-f', 'null', '-']
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    else:
        x = np.asarray(x_or_path, dtype=np.float64)
        ch = 1 if x.ndim == 1 else x.shape[0]
        n = x.shape[-1]
        if n < int(0.45 * SR):                            # the meter needs one full 400 ms block
            pad = np.zeros((ch, int(0.45 * SR) - n)) if ch > 1 else np.zeros(int(0.45 * SR) - n)
            x = np.concatenate([x, pad], axis=-1)
        raw = np.ascontiguousarray((x if ch == 1 else x.T).astype('<f4')).tobytes()
        cmd = [ff, '-hide_banner', '-nostats', '-nostdin', '-f', 'f32le', '-ar', str(SR), '-ac', str(ch),
               '-i', 'pipe:0', '-af', 'ebur128=peak=true:framelog=quiet', '-f', 'null', '-']
        p = subprocess.run(cmd, input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    txt = p.stderr.decode('utf-8', 'replace')
    k = txt.rfind('Summary:')
    if k < 0:
        return None
    s = txt[k:]

    def grab(label, unit):
        m = re.search(label + r':\s+(-?[\d.]+|-inf)\s+' + unit, s)
        if not m:
            return None
        v = m.group(1)
        return -math.inf if v == '-inf' else float(v)
    out = {'I': grab('I', 'LUFS'), 'LRA': grab('LRA', 'LU'), 'TP': grab('Peak', 'dBFS'),
           'thresh': grab('Threshold', 'LUFS')}
    return out


def _mono(x):
    x = np.asarray(x, dtype=np.float64)
    return x.mean(axis=0) if x.ndim == 2 else x


def pitch_estimate(x, fmin=50, fmax=1500, thresh=0.12, lowpass=None):
    """YIN pitch (Hz) of the loudest 100 ms, or None when the sound is not periodic (noise, clusters).
    lowpass (Hz) helps on voices whose formants boost one harmonic above the fundamental."""
    m = _mono(x)
    if lowpass:
        m = dsp.filt(m, hi=lowpass, order=4)
    n = len(m)
    w = min(n, int(0.1 * SR))
    tmax = int(SR / fmin)
    tmin = max(2, int(SR / fmax))
    if w < 2 * tmax + 16:
        tmax = (w - 16) // 2
        if tmax <= tmin + 2:
            return None
    c2 = np.concatenate([[0.0], np.cumsum(m ** 2)])
    e = c2[w:] - c2[:-w]
    i = int(np.argmax(e))
    seg = m[i:i + w]
    W = w - tmax
    a = seg[:W]
    N = dsp.me._pow2(w + W)
    r = np.fft.irfft(np.fft.rfft(seg, N) * np.conj(np.fft.rfft(a, N)), N)[:tmax + 1]
    c = np.concatenate([[0.0], np.cumsum(seg ** 2)])
    e0 = c[W] - c[0]
    et = c[np.arange(tmax + 1) + W] - c[np.arange(tmax + 1)]
    d = np.maximum(e0 + et - 2 * r, 0.0)
    cm = np.cumsum(d[1:])
    dn = np.ones(tmax + 1)
    dn[1:] = d[1:] * np.arange(1, tmax + 1) / np.maximum(cm, 1e-20)
    k = None
    for t in range(tmin, tmax):
        if dn[t] < thresh:
            while t + 1 < tmax and dn[t + 1] < dn[t]:
                t += 1
            k = t
            break
    if k is None:
        return None
    kk = float(k)
    if 1 <= k < tmax:
        p0, p1, p2 = dn[k - 1], dn[k], dn[k + 1]
        den = p0 - 2 * p1 + p2
        if den > 1e-12:
            off = 0.5 * (p0 - p2) / den
            if -1 < off < 1:
                kk = k + off
    return SR / kk if kk > 0 else None


def features(x):
    x = np.asarray(x, dtype=np.float64)
    st = x.ndim == 2
    m = _mono(x)
    n = len(m)
    f = {'dur_s': round(n / SR, 3), 'channels': x.shape[0] if st else 1}
    finite = bool(np.all(np.isfinite(x)))
    f['finite'] = finite
    if not finite or n == 0:
        return f
    pk = float(np.max(np.abs(x)))
    rms = float(np.sqrt(np.mean(x ** 2)))
    f['peak_dbfs'] = round(dsp.db(pk), 2)
    f['rms_dbfs'] = round(dsp.db(rms), 2)
    f['crest_db'] = round(dsp.db(pk) - dsp.db(rms), 2)
    f['dc'] = round(float(np.mean(m)), 6)
    f['clipped'] = int(np.sum(np.abs(x) >= 0.999))
    N = dsp.me._pow2(n)
    S = np.abs(np.fft.rfft(m, N)) ** 2
    fr = np.fft.rfftfreq(N, 1.0 / SR)
    tot = float(S[fr >= 20].sum()) + 1e-20
    f['centroid_hz'] = round(float((S * fr).sum() / (S.sum() + 1e-20)), 1)
    c = np.cumsum(S)
    f['rolloff85_hz'] = round(float(fr[min(len(fr) - 1, int(np.searchsorted(c, 0.85 * c[-1])))]), 1)
    band = (fr >= 50) & (fr <= 12000)
    sb = S[band] + 1e-20
    f['flatness'] = round(float(np.exp(np.mean(np.log(sb))) / np.mean(sb)), 4)
    f['bands'] = {name: round(float(S[(fr >= lo) & (fr < hi)].sum()) / tot, 4) for name, lo, hi in BANDS}
    f['below20_ratio'] = round(float(S[(fr > 0) & (fr < 20)].sum()) / (float(S[1:].sum()) + 1e-20), 5)
    # envelope: 5 ms RMS
    w = max(1, int(0.005 * SR))
    env = np.sqrt(np.convolve(m ** 2, np.ones(w) / w, 'same') + 1e-20)
    ep = float(env.max())
    ipk = int(np.argmax(env))
    above10 = np.nonzero(env[:ipk + 1] >= 0.1 * ep)[0]
    f['attack_ms'] = round((ipk - (above10[0] if above10.size else 0)) / SR * 1000, 1)
    after = np.nonzero(env[ipk:] < ep * dsp.undb(-40))[0]
    f['decay40_ms'] = round((after[0] if after.size else n - ipk) / SR * 1000, 1)
    # onsets: positive spectral-energy flux peaks per second (texture density)
    hop = int(0.023 * SR)
    if n > 12 * hop:
        hp = dsp.filt(m, lo=200)
        nf = n // hop
        e2 = (hp[:nf * hop].reshape(nf, hop) ** 2).sum(axis=1)
        le = 10 * np.log10(e2 + 1e-12)
        base = np.array([np.median(le[max(0, j - 10):j]) if j else le[0] for j in range(nf)])
        rise = le - base
        on = np.sum((rise[1:-1] > 6.0) & (rise[1:-1] >= rise[:-2]) & (rise[1:-1] >= rise[2:]) & (le[1:-1] > le.max() - 45))
        f['onsets_per_s'] = round(float(on) / (n / SR), 2)
    if st:
        mid = (x[0] + x[1]) / 2
        side = (x[0] - x[1]) / 2
        f['width'] = round(float(np.sqrt(np.mean(side ** 2)) / (np.sqrt(np.mean(mid ** 2)) + 1e-12)), 3)
        f['lr_corr'] = round(float(np.corrcoef(x[0], x[1])[0, 1]) if np.std(x[0]) > 0 and np.std(x[1]) > 0 else 1.0, 3)
    p = pitch_estimate(m)
    f['pitch_hz'] = round(p, 1) if p else None
    f['active_lufs'] = round(dsp.active_loudness(x), 2)
    return f


def loop_seam(x, start, end):
    """Continuity across a loop wrap: the step end-1 -> start vs. the 99.9th percentile step inside the loop."""
    x = dsp.as_stereo(x)
    seg = x[:, start:end]
    d_in = np.abs(np.diff(seg, axis=1))
    typical = float(np.percentile(d_in, 99.9)) if d_in.size else 0.0
    jump = float(np.max(np.abs(seg[:, -1] - seg[:, 0])))
    w = max(1, min(int(0.05 * SR), seg.shape[1] // 4))
    r_end = float(np.sqrt(np.mean(seg[:, -w:] ** 2)))
    r_start = float(np.sqrt(np.mean(seg[:, :w] ** 2)))
    ratio_db = abs(dsp.db(r_end + 1e-9) - dsp.db(r_start + 1e-9))
    # the verdict is the sample step across the wrap; an RMS change is normal when a loop starts on a downbeat
    return {'wrap_step': round(jump, 6), 'p999_step': round(typical, 6), 'rms_jump_db': round(ratio_db, 2),
            'seamless': bool(jump <= max(typical, 1e-4))}


def _hz(v):
    return '%.1f kHz' % (v / 1000.0) if v >= 1000 else '%d Hz' % int(round(v))


def describe(f, kind=''):
    """Plain words from features (what a listener would notice first)."""
    if not f.get('finite', True):
        return 'BROKEN: non-finite samples'
    words = []
    d = f['dur_s']
    words.append('%.2f s' % d if d < 10 else '%.0f s' % d)
    att = f.get('attack_ms', 0)
    if d < 3:
        words.append('sharp attack' if att < 6 else ('soft attack' if att > 40 else 'quick attack'))
    b = f.get('bands', {})
    order = sorted(b.items(), key=lambda kv: -kv[1])
    names = {'sub': 'sub-bass rumble', 'low': 'low body', 'lowmid': 'warm low-mids', 'mid': 'mid presence',
             'high': 'bright highs', 'air': 'airy top'}
    main = [names[k] for k, v in order[:2] if v > 0.12]
    if main:
        words.append('mostly ' + ' + '.join(main))
    c = f.get('centroid_hz', 0)
    tone = 'dark' if c < 900 else ('warm' if c < 2000 else ('bright' if c < 4500 else 'very bright/hissy'))
    words.append('%s (centroid %s)' % (tone, _hz(c)))
    fl = f.get('flatness', 0)
    if f.get('pitch_hz') and fl < 0.2:
        words.append('pitched ~%s' % _hz(f['pitch_hz']))
    elif fl > 0.35:
        words.append('noisy texture')
    elif fl < 0.05:
        words.append('tonal')
    on = f.get('onsets_per_s')
    if on is not None and d > 1.5:
        words.append('busy (%.0f hits/s)' % on if on > 6 else ('pulsing (%.1f hits/s)' % on if on > 1 else 'smooth/sustained'))
    if f.get('channels') == 2:
        w = f.get('width', 0)
        words.append('wide stereo' if w > 0.45 else ('some width' if w > 0.12 else 'near-mono'))
    if f.get('clipped'):
        words.append('CLIPS (%d samples)' % f['clipped'])
    if abs(f.get('dc', 0)) > 0.002:
        words.append('DC offset %.3f' % f['dc'])
    return ', '.join(words)


def check(x, kind='', loop=None, target_lufs=None, tol=1.0, measure=True):
    """Features + ebur128 + verdicts. loop=(start, end) samples checks the seam."""
    x = np.asarray(x, dtype=np.float64)
    f = features(x)
    r = {'features': f, 'description': describe(f, kind)}
    if measure:
        r['ebur128'] = ebur128(x)
    problems = []
    if not f.get('finite', True):
        problems.append('non-finite samples')
    else:
        if f.get('clipped'):
            problems.append('clipping')
        if abs(f.get('dc', 0)) > 0.002:
            problems.append('DC offset')
        if f.get('peak_dbfs', -120) < -60:
            problems.append('silent')
        if f.get('below20_ratio', 0) > 0.05:
            problems.append('infrasonic energy')
    if loop:
        r['loop'] = loop_seam(x, int(loop[0]), int(loop[1]))
        if not r['loop']['seamless']:
            problems.append('loop seam')
    if target_lufs is not None and r.get('ebur128') and r['ebur128'].get('I') is not None:
        err = r['ebur128']['I'] - target_lufs
        r['lufs_err'] = round(err, 2)
        if abs(err) > tol:
            problems.append('loudness off target by %.1f LU' % err)
    r['problems'] = problems
    r['ok'] = not problems
    return r
