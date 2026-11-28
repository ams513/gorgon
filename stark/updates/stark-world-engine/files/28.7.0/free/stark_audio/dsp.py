"""Shared numpy DSP for stark_audio (48 kHz float64 arrays; mono = 1-D, stereo = (2, n)).

Loop-safe ("periodic") helpers filter and modulate circularly, so a bed built from them wraps sample-exactly.
"""
from __future__ import annotations

import io
import json
import math
import os
import shutil
import struct
import subprocess
import wave

import numpy as np

from . import ROOT  # noqa: F401  (puts music_engine on sys.path)
import music_engine as me

SR = me.SR
CEILING = me.CEILING


def t_axis(n):
    return np.arange(int(n)) / SR


def secs(s):
    return max(1, int(round(s * SR)))


def db(x):
    return 20.0 * math.log10(max(float(x), 1e-12))


def undb(d):
    return 10.0 ** (d / 20.0)


# ---------------------------------------------------------------------------------------------------------------
# filters
# ---------------------------------------------------------------------------------------------------------------
def filt(x, lo=None, hi=None, order=2, peak=None):
    """Zero-phase Butterworth-magnitude band filter (linear, zero padded): music_engine._filt."""
    return me._filt(np.asarray(x, dtype=np.float64), lo=lo, hi=hi, order=order, peak=peak)


def _mag_response(f, lo, hi, order, peak, shelf=None):
    H = np.ones_like(f)
    if lo:
        H *= 1.0 / np.sqrt(1.0 + (lo / np.maximum(f, 1e-3)) ** (2 * order))
    if hi:
        H *= 1.0 / np.sqrt(1.0 + (f / hi) ** (2 * order))
    if peak:
        for fc, gain_db, q in peak:
            g = undb(gain_db) - 1.0
            H *= 1.0 + g / (1.0 + ((f - fc) / (fc / q)) ** 2)
    if shelf:
        for fc, gain_db, kind in shelf:                    # kind 'low' | 'high', smooth 1st-order shelves
            g = undb(gain_db)
            r = (f / fc) ** 2 if kind == 'high' else (fc / np.maximum(f, 1e-3)) ** 2
            H *= np.sqrt((1 + g * g * r) / (1 + r))
    return H


def cfilt(x, lo=None, hi=None, order=2, peak=None, shelf=None):
    """Circular (periodic) zero-phase filter: the output of a loop stays a seamless loop."""
    x = np.asarray(x, dtype=np.float64)
    n = x.shape[-1]
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1.0 / SR)
    return np.fft.irfft(X * _mag_response(f, lo, hi, order, peak, shelf), n)


def eq(x, lo=None, hi=None, order=2, peak=None, shelf=None):
    """Linear (zero padded) zero-phase EQ with shelves and peaks."""
    x = np.asarray(x, dtype=np.float64)
    n = x.shape[-1]
    N = me._pow2(n + 256)
    X = np.fft.rfft(x, N)
    f = np.fft.rfftfreq(N, 1.0 / SR)
    return np.fft.irfft(X * _mag_response(f, lo, hi, order, peak, shelf), N)[..., :n]


def biquad_hp(x, fc, q=0.707):
    """Causal 2nd-order high-pass (RBJ). Used for the dialogue chain (no pre-ringing on plosives)."""
    w0 = 2 * math.pi * fc / SR
    al = math.sin(w0) / (2 * q)
    c = math.cos(w0)
    b0, b1, b2 = (1 + c) / 2, -(1 + c), (1 + c) / 2
    a0, a1, a2 = 1 + al, -2 * c, 1 - al
    return _iir2(x, b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0)


def _iir2(x, b0, b1, b2, a1, a2):
    """Direct-form II transposed biquad, vectorised with scipy-free block recursion (pure numpy fallback loop)."""
    x = np.asarray(x, dtype=np.float64)
    y = np.empty_like(x)
    z1 = z2 = 0.0
    xs = x.tolist()
    out = y
    for i, v in enumerate(xs):
        o = b0 * v + z1
        z1 = b1 * v - a1 * o + z2
        z2 = b2 * v - a2 * o
        out[i] = o
    return y


def stft_shape(x, gain_fn, nfft=1024, hop=256, circular=False):
    """Time-varying spectral shaping: gain_fn(frame_centres_s (F,), freqs (K,)) -> gains (F, K).
    Hann analysis/synthesis with WOLA normalisation; circular=True wraps frames so loops stay seamless."""
    x = np.asarray(x, dtype=np.float64)
    n = x.shape[-1]
    win = np.hanning(nfft + 1)[:-1]
    if circular:
        nf = int(math.ceil(n / hop))
        idx = (np.arange(nf)[:, None] * hop + np.arange(nfft)[None, :]) % n
        frames = x[idx] * win
        centres = ((np.arange(nf) * hop + nfft / 2) % n) / SR
    else:
        pad = nfft
        xp = np.concatenate([np.zeros(pad), x, np.zeros(pad + nfft)])
        nf = int(math.ceil((n + pad) / hop)) + 1
        idx = np.arange(nf)[:, None] * hop + np.arange(nfft)[None, :]
        frames = xp[idx] * win
        centres = (np.arange(nf) * hop + nfft / 2 - pad) / SR
    S = np.fft.rfft(frames, axis=1)
    freqs = np.fft.rfftfreq(nfft, 1.0 / SR)
    S *= gain_fn(centres, freqs)
    fr = np.fft.irfft(S, nfft, axis=1) * win
    if circular:
        y = np.zeros(n)
        wsum = np.zeros(n)
        np.add.at(y, idx.ravel(), fr.ravel())
        np.add.at(wsum, idx.ravel(), np.tile(win ** 2, nf))
        return y / np.maximum(wsum, 1e-8)
    y = np.zeros(len(xp))
    wsum = np.zeros(len(xp))
    np.add.at(y, idx.ravel(), fr.ravel())
    np.add.at(wsum, idx.ravel(), np.tile(win ** 2, nf))
    y = y / np.maximum(wsum, 1e-8)
    return y[pad:pad + n]


def bell(freqs, fc, width_oct=0.6):
    """Gaussian band (in octaves) centred on fc: rows of frame centres (F, 1) x freqs (K,)."""
    lf = np.log2(np.maximum(freqs, 1.0))[None, :]
    return np.exp(-0.5 * ((lf - np.log2(np.maximum(fc, 1.0))) / width_oct) ** 2)


# ---------------------------------------------------------------------------------------------------------------
# sources, envelopes, placement
# ---------------------------------------------------------------------------------------------------------------
def env_perc(n, a, d, curve=1.0):
    t = t_axis(n)
    att = np.clip(t / max(a, 1e-5), 0, 1) ** curve
    return att * np.exp(-np.maximum(t - a, 0) / max(d, 1e-5))


def env_bell(n, peak_at, width):
    t = t_axis(n)
    return np.exp(-0.5 * ((t - peak_at) / max(width, 1e-4)) ** 2)


def env_adr(n, a, hold, r):
    t = t_axis(n)
    e = np.clip(t / max(a, 1e-5), 0, 1)
    e = np.where(t > a + hold, np.exp(-(t - a - hold) / max(r, 1e-5)), e)
    return e


def chirp(f0, f1, n, tau=None, phase=0.0):
    """Sine whose frequency glides f0 -> f1 (exponential with time constant tau, or linear when tau is None)."""
    t = t_axis(n)
    if tau:
        f = f1 + (f0 - f1) * np.exp(-t / tau)
    else:
        f = f0 + (f1 - f0) * t / max(t[-1], 1e-9)
    return np.sin(2 * np.pi * np.cumsum(f) / SR + phase)


def modal(n, modes, rng=None, jitter=0.0):
    """Sum of exponentially decaying partials: modes = [(freq, decay_s, amp), ...] (struck bars, plates, wood)."""
    t = t_axis(n)
    y = np.zeros(n)
    for f, d, a in modes:
        if f >= SR * 0.45:
            continue
        ff = f * (1 + jitter * (rng.standard_normal() if rng is not None else 0.0))
        ph = rng.random() * 6.283 if rng is not None else 0.0
        y += a * np.sin(2 * np.pi * ff * t + ph) * np.exp(-t / max(d, 1e-4))
    return y * np.minimum(1, t / 0.0004)


def grains(n, rate, rng, gmin=0.0005, gmax=0.003, lo=None, hi=None, env=None, amp_sigma=0.6):
    """Granular crackle: Poisson-timed micro noise bursts (rate per second), optionally band-limited and enveloped."""
    k = rng.poisson(rate * n / SR)
    y = np.zeros(n)
    if k <= 0:
        return y
    pos = rng.integers(0, n, k)
    amps = np.exp(rng.normal(0, amp_sigma, k)) * rng.choice([-1.0, 1.0], k)
    imp = np.zeros(n)
    np.add.at(imp, pos, amps)
    L = max(4, int(gmax * SR))
    kt = t_axis(L)
    kern = rng.standard_normal(L) * np.exp(-kt / max((gmin + gmax) / 4, 1e-5))
    y = np.convolve(imp, kern)[:n]
    if lo or hi:
        y = filt(y, lo=lo, hi=hi)
    if env is not None:
        y = y * env
    return y


def place(dst, src, at, gain=1.0, pan=0.0, wrap=False):
    """Mix src (mono or stereo) into dst (2, n) at time `at` seconds; wrap=True folds overflow to the start."""
    src = np.asarray(src, dtype=np.float64)
    if src.ndim == 1:
        th = (max(-1.0, min(1.0, pan)) + 1) * math.pi / 4
        src = np.vstack([src * math.cos(th) * math.sqrt(2), src * math.sin(th) * math.sqrt(2)])
    n = dst.shape[1]
    i0 = int(round(at * SR))
    m = src.shape[1]
    if wrap:
        i0 %= n
        first = min(m, n - i0)
        dst[:, i0:i0 + first] += src[:, :first] * gain
        rest = m - first
        off = first
        while rest > 0:
            k = min(rest, n)
            dst[:, :k] += src[:, off:off + k] * gain
            rest -= k
            off += k
        return dst
    if i0 >= n:
        return dst
    a = max(0, i0)
    b0 = a - i0
    e = min(n, i0 + m)
    if e > a:
        dst[:, a:e] += src[:, b0:b0 + (e - a)] * gain
    return dst


def stereo(mono, width=0.0, delay_ms=0.0, rng=None):
    """Mono -> stereo with an optional Haas delay and decorrelated side noise-free widening."""
    mono = np.asarray(mono, dtype=np.float64)
    if delay_ms <= 0:
        return np.vstack([mono, mono])
    d = int(delay_ms * SR / 1000.0)
    r = np.concatenate([np.zeros(d), mono[:len(mono) - d]]) if d else mono
    m = (1 - width) * mono + width * r
    return np.vstack([mono, m])


def pan(mono, p):
    th = (max(-1.0, min(1.0, p)) + 1) * math.pi / 4
    return np.vstack([mono * math.cos(th) * math.sqrt(2), mono * math.sin(th) * math.sqrt(2)])


def periodic_curve(n, rng, cycles=(1, 2, 3, 5, 8), weights=None, floor=0.0):
    """Smooth random curve in 0..1 that is exactly periodic over n samples (integer cycle counts)."""
    t = np.arange(n) / float(n)
    y = np.zeros(n)
    weights = weights or [1.0 / (1 + i) for i in range(len(cycles))]
    for c, w in zip(cycles, weights):
        y += w * np.sin(2 * np.pi * c * t + rng.random() * 6.283)
    y = (y - y.min()) / max(1e-9, (y.max() - y.min()))
    return floor + (1 - floor) * y


def periodic_noise(n, rng, ch=1):
    x = rng.standard_normal((ch, n)) if ch > 1 else rng.standard_normal(n)
    return x


def brown(n, rng, lo=40.0):
    """Brownish (1/f^2) noise, circular, unit RMS."""
    w = rng.standard_normal(n)
    X = np.fft.rfft(w)
    f = np.fft.rfftfreq(n, 1.0 / SR)
    X *= 1.0 / np.maximum(f, lo)
    X[0] = 0
    y = np.fft.irfft(X, n)
    return y / (np.std(y) + 1e-12)


def pink(n, rng):
    w = rng.standard_normal(n)
    X = np.fft.rfft(w)
    f = np.fft.rfftfreq(n, 1.0 / SR)
    X *= 1.0 / np.sqrt(np.maximum(f, 20.0))
    X[0] = 0
    y = np.fft.irfft(X, n)
    return y / (np.std(y) + 1e-12)


def fade(x, fin=0.002, fout=0.01):
    x = np.array(x, dtype=np.float64, copy=True)
    n = x.shape[-1]
    a = min(n // 2, int(fin * SR))
    b = min(n // 2, int(fout * SR))
    if a > 0:
        x[..., :a] *= np.linspace(0, 1, a)
    if b > 0:
        x[..., -b:] *= np.linspace(1, 0, b) ** 2
    return x


def trim_tail(x, floor_db=-60.0, keep_s=0.02):
    x = np.asarray(x)
    env = np.max(np.abs(x), axis=0) if x.ndim == 2 else np.abs(x)
    pk = float(np.max(env)) if env.size else 0.0
    if pk <= 0:
        return x[..., :secs(keep_s)]
    idx = np.nonzero(env > pk * undb(floor_db))[0]
    last = int(idx[-1]) + 1 if idx.size else secs(keep_s)
    return x[..., :max(last + secs(0.005), secs(keep_s))]


def reverb(x, t60=1.2, mix=0.2, predelay=0.01, seed=77, tail=True):
    """Convolution reverb with music_engine's synthetic IR (stereo out)."""
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 1:
        x = np.vstack([x, x])
    ir = me._make_ir(t60, predelay, seed)
    if tail:
        x = np.concatenate([x, np.zeros((2, int(ir.shape[1] * 0.7)))], axis=1)
    return x + me._convolve(x * mix, ir)


def creverb(x, t60=1.5, mix=0.2, seed=78):
    """Circular reverb for loops: the tail of the loop end rings into its start."""
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 1:
        x = np.vstack([x, x])
    n = x.shape[1]
    ir = me._make_ir(t60, 0.012, seed)
    L = ir.shape[1]
    if L >= n:
        ir = ir[:, :n - 1] * np.linspace(1, 0, n - 1)
        L = ir.shape[1]
    y = np.empty_like(x)
    for c in range(2):
        H = np.fft.rfft(np.concatenate([ir[c], np.zeros(n - L)]))
        y[c] = np.fft.irfft(np.fft.rfft(x[c]) * H, n)
    return x + y * mix


# ---------------------------------------------------------------------------------------------------------------
# loudness / levels
# ---------------------------------------------------------------------------------------------------------------
def as_stereo(x):
    x = np.asarray(x, dtype=np.float64)
    return np.vstack([x, x]) if x.ndim == 1 else x


def lufs(x):
    """BS.1770 integrated loudness. Mono input is measured as a single channel (dialogue convention)."""
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 1:
        # a mono signal measured as one channel: music_engine.lufs sums 2 channels, so halve the power (-3.01 dB)
        return me.lufs(np.vstack([x, np.zeros_like(x)]))
    return me.lufs(x)


def active_loudness(x, rel_db=-30.0):
    """K-weighted RMS loudness of the audible part only (short SFX are shorter than BS.1770's 400 ms blocks)."""
    x = as_stereo(x)
    n = x.shape[1]
    N = me._pow2(n + 64)
    f = np.fft.rfftfreq(N, 1.0 / SR)
    z = np.exp(-2j * np.pi * f / SR)
    H = np.ones_like(z)
    for b, a in (me._K1, me._K2):
        H *= (b[0] + b[1] * z + b[2] * z * z) / (a[0] + a[1] * z + a[2] * z * z)
    y = np.fft.irfft(np.fft.rfft(x, N) * np.abs(H), N)[:, :n]
    p = (y ** 2).sum(axis=0)
    w = max(1, int(0.01 * SR))
    c = np.cumsum(np.concatenate([[0.0], p]))
    ms = (c[w:] - c[:-w]) / w if n > w else np.array([p.mean()])
    if not np.any(ms > 0):
        return -120.0
    top = float(ms.max())
    sel = ms[ms > top * undb(rel_db) ** 2]
    return -0.691 + 10 * math.log10(float(np.mean(sel)) + 1e-20)


def limit(x, ceiling=CEILING, knee=0.8):
    return me._soft_clip(np.asarray(x, dtype=np.float64), ceiling=ceiling, knee=knee)


def upsample(x, factor=4):
    """Band-limited FFT interpolation (last axis)."""
    x = np.asarray(x, dtype=np.float64)
    n = x.shape[-1]
    X = np.fft.rfft(x)
    Z = np.zeros(x.shape[:-1] + (n * factor // 2 + 1,), complex)
    Z[..., :X.shape[-1]] = X
    return np.fft.irfft(Z, n * factor) * factor


def true_peak(x, factor=4):
    """Oversampled (inter-sample) peak, linear."""
    x = np.asarray(x, dtype=np.float64)
    if x.shape[-1] < 8:
        return float(np.max(np.abs(x))) if x.size else 0.0
    return float(np.max(np.abs(upsample(x, factor))))


def limit_tp(x, ceiling_db=-1.0, lookahead=0.0015, release=0.06, iters=4):
    """True-peak limiter: finds inter-sample overs at 4x, then applies a smooth gain dip around each one
    (lookahead ramp in, exponential release out). Untouched where the signal is already below the ceiling."""
    x = np.array(x, dtype=np.float64, copy=True)
    ceil = undb(ceiling_db)
    n = x.shape[-1]
    la = max(1, int(lookahead * SR))
    rl = max(1, int(release * SR))
    kt = np.arange(-la, 5 * rl)
    kern = np.where(kt < 0, 1.0 + kt / float(la), np.exp(-kt / float(rl)))
    for _ in range(iters):
        up = upsample(x, 4)
        env = np.abs(up) if up.ndim == 1 else np.max(np.abs(up), axis=0)
        env = env[:n * 4].reshape(n, 4).max(axis=1)
        over = env / ceil
        if over.max() <= 1.0005:
            break
        depth = np.where(over > 1.0, 1.0 - 1.0 / over, 0.0) * 1.02
        N = me._pow2(n + len(kern))
        red = np.fft.irfft(np.fft.rfft(depth, N) * np.fft.rfft(kern, N), N)[la:la + n]
        g = np.clip(1.0 - np.maximum(red, depth), 0.05, 1.0)
        x = x * g
    return x


def norm_active(x, target, ceiling_db=-1.0):
    """Gain to an active-loudness target, then a soft ceiling (SFX levelling)."""
    g = target - active_loudness(x)
    y = np.asarray(x, dtype=np.float64) * undb(g)
    return limit_tp(limit(y, undb(ceiling_db), 0.85), ceiling_db)


def norm_integrated(x, target, ceiling_db=-1.0):
    g = target - lufs(x)
    y = np.asarray(x, dtype=np.float64) * undb(g)
    return limit(y, undb(ceiling_db), 0.85)


# ---------------------------------------------------------------------------------------------------------------
# file i/o
# ---------------------------------------------------------------------------------------------------------------
def write_wav(path, x, loop=None, title=None, seed=0):
    """16-bit PCM WAV (TPDF dither), mono or stereo, optional RIFF smpl loop [start, end)."""
    x = np.asarray(x, dtype=np.float64)
    ch = 1 if x.ndim == 1 else x.shape[0]
    os.makedirs(os.path.dirname(os.path.abspath(path)) or '.', exist_ok=True)
    r = np.random.default_rng(seed + 7)
    d = r.random(x.shape) - r.random(x.shape)
    pcm = np.clip(np.round(x * 32767.0 + d), -32768, 32767).astype('<i2')
    data = (pcm.T if ch > 1 else pcm).tobytes()
    chunks = struct.pack('<4sIHHIIHH', b'fmt ', 16, 1, ch, SR, SR * 2 * ch, 2 * ch, 16)
    chunks += b'data' + struct.pack('<I', len(data)) + data
    if len(data) % 2:
        chunks += b'\0'
    if loop:
        s, e = int(loop[0]), int(loop[1])
        chunks += struct.pack('<4sI9I', b'smpl', 60, 0, 0, int(1e9 / SR), 60, 0, 0, 0, 1, 0)
        chunks += struct.pack('<6I', 0, 0, s, e - 1, 0, 0)
    txt = ('Stark World Engine stark_audio (procedural, original) ' + (title or '')).strip()
    bt = txt.encode('ascii', 'replace') + b'\0'
    if len(bt) % 2:
        bt += b'\0'
    info = b'ISFT' + struct.pack('<I', len(bt)) + bt
    chunks += b'LIST' + struct.pack('<I', 4 + len(info)) + b'INFO' + info
    with open(path, 'wb') as fh:
        fh.write(b'RIFF' + struct.pack('<I', 4 + len(chunks)) + b'WAVE' + chunks)
    return path


def read_wav(path):
    """-> (float64 array mono (n,) or stereo (2, n), sample rate). 16/24/32-bit PCM via the wave module."""
    with wave.open(path, 'rb') as w:
        ch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if sw == 2:
        a = np.frombuffer(raw, '<i2').astype(np.float64) / 32768.0
    elif sw == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3)
        v = (b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16))
        v = np.where(v >= 1 << 23, v - (1 << 24), v)
        a = v.astype(np.float64) / float(1 << 23)
    elif sw == 4:
        a = np.frombuffer(raw, '<i4').astype(np.float64) / float(1 << 31)
    else:
        a = (np.frombuffer(raw, np.uint8).astype(np.float64) - 128) / 128.0
    if ch > 1:
        a = a.reshape(-1, ch).T
    return a, sr


_FF = {}


def ffmpeg_bin(name='ffmpeg'):
    """ffmpeg/ffprobe: PATH, then the owner's config.json 'ffmpeg' entry (read only), then a WinGet install."""
    if name in _FF:
        return _FF[name]
    found = shutil.which(name)
    if not found:
        exe = name + ('.exe' if os.name == 'nt' else '')
        cands = []
        try:
            with open(os.path.join(ROOT, 'config.json'), encoding='utf-8-sig') as fh:
                p = (json.load(fh) or {}).get('ffmpeg') or ''
            if p:
                cands.append(os.path.join(os.path.dirname(p), exe))
        except (OSError, ValueError):
            pass
        import glob
        local = os.environ.get('LOCALAPPDATA', '')
        cands += sorted(glob.glob(os.path.join(local, 'Microsoft', 'WinGet', 'Packages', 'Gyan.FFmpeg*', '*', 'bin', exe)),
                        reverse=True)
        found = next((c for c in cands if os.path.isfile(c)), None)
    _FF[name] = found
    return found


def have_ffmpeg():
    return bool(ffmpeg_bin())


def decode(path_or_bytes, sr=SR, mono=True, timeout=120):
    """Decode any audio ffmpeg reads (wav/flac/mp3/ogg/opus/webm/m4a) -> float64 (mono 1-D or (2, n))."""
    ff = ffmpeg_bin()
    ch = 1 if mono else 2
    if isinstance(path_or_bytes, (bytes, bytearray)):
        data = bytes(path_or_bytes)
        if not ff:
            if data[:4] == b'RIFF':
                return _wav_bytes(data, sr, mono)
            raise RuntimeError('ffmpeg is needed to read this audio format')
        cmd = [ff, '-hide_banner', '-nostdin', '-loglevel', 'error', '-i', 'pipe:0', '-f', 'f32le', '-ac', str(ch),
               '-ar', str(sr), 'pipe:1']
        p = subprocess.run(cmd, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    else:
        if not ff:
            if str(path_or_bytes).lower().endswith('.wav'):
                with open(path_or_bytes, 'rb') as fh:
                    return _wav_bytes(fh.read(), sr, mono)
            raise RuntimeError('ffmpeg is needed to read this audio format')
        cmd = [ff, '-hide_banner', '-nostdin', '-loglevel', 'error', '-i', str(path_or_bytes), '-f', 'f32le',
               '-ac', str(ch), '-ar', str(sr), 'pipe:1']
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if p.returncode != 0:
        raise ValueError('could not decode audio: ' + p.stderr.decode('utf-8', 'replace').strip()[-200:])
    a = np.frombuffer(p.stdout, '<f4').astype(np.float64)
    if ch == 2:
        a = a.reshape(-1, 2).T
    return a


def _wav_bytes(data, sr, mono):
    a, r = read_wav(io.BytesIO(data))
    if a.ndim == 2 and mono:
        a = a.mean(axis=0)
    if r != sr:
        n = int(round(a.shape[-1] * sr / float(r)))
        xi = np.linspace(0, a.shape[-1] - 1, n)
        a = np.interp(xi, np.arange(a.shape[-1]), a) if a.ndim == 1 else np.vstack(
            [np.interp(xi, np.arange(a.shape[-1]), c) for c in a])
    return a


def encode(x, codec='opus', bitrate=48, application='audio', timeout=180, vbr='on'):
    """float array -> encoded bytes (Ogg Opus / Ogg Vorbis / MP3) through ffmpeg, deterministic (bitexact)."""
    ff = ffmpeg_bin()
    if not ff:
        raise RuntimeError('ffmpeg is required to encode audio')
    x = np.asarray(x, dtype=np.float64)
    ch = 1 if x.ndim == 1 else x.shape[0]
    raw = np.ascontiguousarray((x if ch == 1 else x.T).astype('<f4')).tobytes()
    base = [ff, '-hide_banner', '-nostdin', '-loglevel', 'error', '-f', 'f32le', '-ar', str(SR), '-ac', str(ch),
            '-i', 'pipe:0', '-map_metadata', '-1', '-fflags', '+bitexact', '-flags:a', '+bitexact']
    if codec == 'opus':
        args = ['-c:a', 'libopus', '-b:a', '%dk' % bitrate, '-vbr', vbr, '-compression_level', '10',
                '-application', application, '-frame_duration', '20', '-f', 'ogg']
    elif codec == 'vorbis':
        args = ['-c:a', 'libvorbis', '-b:a', '%dk' % bitrate, '-f', 'ogg']
    elif codec == 'mp3':
        args = ['-c:a', 'libmp3lame', '-b:a', '%dk' % bitrate, '-f', 'mp3']
    else:
        raise ValueError('codec must be opus, vorbis or mp3')
    p = subprocess.run(base + args + ['pipe:1'], input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=timeout)
    if p.returncode != 0 or not p.stdout:
        raise RuntimeError('ffmpeg encode failed: ' + p.stderr.decode('utf-8', 'replace')[-300:])
    return p.stdout


MIME = {'opus': 'audio/ogg; codecs=opus', 'vorbis': 'audio/ogg; codecs=vorbis', 'mp3': 'audio/mpeg'}


def save_json(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)) or '.', exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(obj, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, path)
    return path


def load_json(path, default=None):
    try:
        with open(path, encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default
