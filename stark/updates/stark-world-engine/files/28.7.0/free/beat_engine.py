"""beat_engine.py - Local beat / tempo / energy analysis for Stark World Engine.

Gives the auto-editor a beat grid so it can cut and accent ON THE BEAT,
for a professional music-video rhythm. 100% local: the only external
process is ffmpeg/ffprobe (on PATH). No cloud APIs, no network calls, no
music libraries (librosa/aubio). The DSP is implemented here.

numpy is used when available for speed; a pure-Python fallback keeps the
module working (just slower) if numpy is not installed. Importing this
module has no side effects.

Public API
----------
analyze(path) -> dict
    keys: tempo_bpm, beats, downbeats, onsets, energy_envelope, duration
nearest_beat(beats, t) -> float
snap_cuts(cut_times, beats, max_shift=0.12) -> list[float]
accents(beats, downbeats, energy) -> list[(t, strength)]

Run directly for a self-test against a synthesized click track:
    python beat_engine.py
"""

from __future__ import annotations

import math
import os
import struct
import subprocess
import sys
import tempfile
import wave

try:
    import numpy as _np
    HAVE_NUMPY = True
except Exception:  # pragma: no cover - exercised only without numpy
    _np = None
    HAVE_NUMPY = False

# --- Analysis constants -----------------------------------------------------
SAMPLE_RATE = 22050      # PCM decode rate (mono); plenty for onset/tempo work
FRAME = 1024             # STFT window size (samples)
HOP = 512                # STFT hop size (samples) -> ~43 fps envelope
MIN_BPM = 60.0
MAX_BPM = 200.0


# --- ffmpeg helpers ---------------------------------------------------------
def _run(cmd):
    """Run a subprocess, returning (returncode, stdout_bytes, stderr_bytes)."""
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, p.stdout, p.stderr


def _decode_pcm(path, sr=SAMPLE_RATE):
    """Decode any audio/video file to mono float samples in [-1, 1] via ffmpeg.

    Reads raw s16le PCM over stdout (no temp file needed for decode).
    Returns (samples, sr). samples is a numpy array or a Python list.
    """
    cmd = [
        "ffmpeg", "-v", "error", "-nostdin",
        "-i", path,
        "-vn", "-ac", "1", "-ar", str(sr),
        "-f", "s16le", "-acodec", "pcm_s16le", "-",
    ]
    rc, out, err = _run(cmd)
    if rc != 0 or not out:
        raise RuntimeError(
            "ffmpeg failed to decode audio from %r: %s"
            % (path, err.decode("utf-8", "replace").strip())
        )
    n = len(out) // 2
    if HAVE_NUMPY:
        data = _np.frombuffer(out[: n * 2], dtype="<i2").astype(_np.float32)
        data /= 32768.0
        return data, sr
    ints = struct.unpack("<%dh" % n, out[: n * 2])
    return [v / 32768.0 for v in ints], sr


# --- Onset-strength envelope (spectral flux) --------------------------------
def _hann(n):
    if HAVE_NUMPY:
        return 0.5 - 0.5 * _np.cos(2.0 * _np.pi * _np.arange(n) / (n - 1))
    return [0.5 - 0.5 * math.cos(2.0 * math.pi * i / (n - 1)) for i in range(n)]


def _dft_mag(frame):
    """Pure-Python magnitude spectrum (real input). O(n^2) - fallback only.

    Downsampled frame size keeps this tractable without numpy.
    """
    n = len(frame)
    half = n // 2
    mags = [0.0] * half
    for k in range(half):
        wr = 2.0 * math.pi * k / n
        re = im = 0.0
        for t in range(n):
            a = wr * t
            re += frame[t] * math.cos(a)
            im -= frame[t] * math.sin(a)
        mags[k] = math.sqrt(re * re + im * im)
    return mags


def _onset_envelope(samples, sr):
    """Return (env, fps) where env is the spectral-flux onset strength.

    Spectral flux = sum of positive bin-to-bin magnitude increases, i.e.
    energy appearing (a transient), not energy leaving.
    """
    fps = sr / float(HOP)
    if HAVE_NUMPY:
        win = _hann(FRAME).astype(_np.float32)
        n = len(samples)
        if n < FRAME:
            return _np.zeros(1, dtype=_np.float32), fps
        n_frames = 1 + (n - FRAME) // HOP
        idx = _np.arange(FRAME)[None, :] + HOP * _np.arange(n_frames)[:, None]
        frames = samples[idx] * win
        spec = _np.abs(_np.fft.rfft(frames, axis=1))
        flux = spec[1:] - spec[:-1]
        flux[flux < 0] = 0.0
        env = flux.sum(axis=1)
        env = _np.concatenate([[0.0], env]).astype(_np.float32)
        mx = float(env.max()) if env.size else 0.0
        if mx > 0:
            env = env / mx
        return env, fps

    # ---- pure-Python path: downsample each frame so the DFT stays cheap ----
    ds = 4
    small = FRAME // ds
    win = _hann(small)
    n = len(samples)
    if n < FRAME:
        return [0.0], fps
    n_frames = 1 + (n - FRAME) // HOP
    env = [0.0]
    prev = None
    for f in range(n_frames):
        base = f * HOP
        frame = [0.0] * small
        for i in range(small):
            frame[i] = samples[base + i * ds] * win[i]
        mag = _dft_mag(frame)
        if prev is not None:
            flux = 0.0
            for k in range(len(mag)):
                d = mag[k] - prev[k]
                if d > 0:
                    flux += d
            env.append(flux)
        prev = mag
    mx = max(env) if env else 0.0
    if mx > 0:
        env = [v / mx for v in env]
    return env, fps


# --- Tempo estimation (autocorrelation of the onset envelope) ---------------
def _autocorr_tempo(env, fps):
    """Estimate tempo (BPM) by autocorrelating the onset envelope."""
    min_lag = int(round(fps * 60.0 / MAX_BPM))
    max_lag = int(round(fps * 60.0 / MIN_BPM))
    max_lag = min(max_lag, len(env) - 1)
    if max_lag <= min_lag:
        return 120.0, min_lag or 1

    # Perceptual weight: a Gaussian on log-tempo centered at ~120 BPM steadies
    # octave errors (picking half/double tempo), the classic autocorr failure.
    def _weight(lag):
        bpm = 60.0 * fps / lag
        return math.exp(-0.5 * (math.log2(bpm / 120.0) / 0.7) ** 2)

    if HAVE_NUMPY:
        e = env - env.mean()
        raw = {}
        for lag in range(min_lag, max_lag + 1):
            raw[lag] = float(_np.dot(e[:-lag], e[lag:]))
    else:
        mean = sum(env) / len(env)
        e = [v - mean for v in env]
        raw = {}
        for lag in range(min_lag, max_lag + 1):
            s = 0.0
            for i in range(len(e) - lag):
                s += e[i] * e[i + lag]
            raw[lag] = s

    best_lag, best_val = min_lag, -1e30
    for lag, r in raw.items():
        v = r * _weight(lag)
        if v > best_val:
            best_val, best_lag = v, lag

    # Parabolic interpolation around the peak for sub-frame period accuracy,
    # so the beat grid does not drift over a long clip.
    lag = float(best_lag)
    lo = raw.get(best_lag - 1)
    hi = raw.get(best_lag + 1)
    mid = raw.get(best_lag)
    if lo is not None and hi is not None:
        denom = (lo - 2.0 * mid + hi)
        if denom != 0:
            lag += 0.5 * (lo - hi) / denom
    return 60.0 * fps / lag, lag


# --- Peak picking for onsets ------------------------------------------------
def _pick_onsets(env, fps, min_gap_s=0.07):
    """Pick local maxima above an adaptive threshold -> onset times (s)."""
    n = len(env)
    if n == 0:
        return []
    if HAVE_NUMPY:
        mean = float(env.mean())
        std = float(env.std())
    else:
        mean = sum(env) / n
        std = (sum((v - mean) ** 2 for v in env) / n) ** 0.5
    thr = mean + 0.6 * std
    min_gap = max(1, int(round(min_gap_s * fps)))
    onsets = []
    last = -10 ** 9
    for i in range(1, n - 1):
        v = env[i]
        if v >= thr and v >= env[i - 1] and v > env[i + 1] and i - last >= min_gap:
            onsets.append(i / fps)
            last = i
    return onsets


# --- Beat grid (phase-locked to the onset envelope) -------------------------
def _beat_grid(env, fps, lag, duration, onsets=None):
    """Build an evenly spaced beat grid, phase-locked then onset-refined.

    1. Pick the sub-frame phase whose grid captures the most onset energy.
    2. If onsets are available, least-squares fit period and phase to them
       (beats = phase + k*period), which removes long-clip drift.
    """
    period_s = lag / fps  # seconds per beat (fractional)
    n = len(env)
    if n == 0 or period_s <= 0:
        return []

    # Phase search over one beat period (sampled finely for sub-frame phase).
    best_phase, best_score = 0.0, -1e30
    steps = 48
    for s in range(steps):
        phase = (s / steps) * period_s
        score = 0.0
        t = phase
        while t < duration:
            i = int(round(t * fps))
            if 0 <= i < n:
                score += env[i]
            t += period_s
        if score > best_score:
            best_score, best_phase = score, phase

    period = period_s
    phase = best_phase

    # Least-squares refine against onsets near the predicted grid.
    if onsets:
        xs, ys = [], []
        for ot in onsets:
            k = round((ot - phase) / period)
            if abs((phase + k * period) - ot) <= 0.25 * period:
                xs.append(k)
                ys.append(ot)
        if len(xs) >= 2:
            m = len(xs)
            sx = sum(xs)
            sy = sum(ys)
            sxx = sum(x * x for x in xs)
            sxy = sum(x * y for x, y in zip(xs, ys))
            denom = m * sxx - sx * sx
            if denom != 0:
                period = (m * sxy - sx * sy) / denom
                phase = (sy - period * sx) / m

    beats = []
    k = 0
    t = phase
    # Walk backward to the start if phase landed mid-clip.
    while t - period >= 0:
        t -= period
    while t <= duration + 1e-6:
        if t >= -1e-6:
            beats.append(round(t, 4))
        t += period
    return beats


def _downbeats(beats, env, fps, group=4):
    """Pick the strongest phase within each bar of `group` beats."""
    if not beats:
        return []
    n = len(env)

    def strength(t):
        i = int(round(t * fps))
        return env[i] if 0 <= i < n else 0.0

    best_off = 0
    best_score = -1e30
    for off in range(group):
        s = sum(strength(beats[j]) for j in range(off, len(beats), group))
        if s > best_score:
            best_score, best_off = s, off
    return [beats[j] for j in range(best_off, len(beats), group)]


# --- Energy envelope (RMS over ~50 ms windows, normalized 0..1) -------------
def _energy_envelope(samples, sr, win_s=0.05):
    win = max(1, int(sr * win_s))
    if HAVE_NUMPY:
        n = len(samples)
        if n < win:
            return [(0.0, 0.0)]
        n_win = n // win
        trimmed = samples[: n_win * win].reshape(n_win, win)
        rms = _np.sqrt((trimmed * trimmed).mean(axis=1))
        mx = float(rms.max()) if rms.size else 0.0
        if mx > 0:
            rms = rms / mx
        ts = (_np.arange(n_win) * win + win / 2) / sr
        return [(round(float(t), 4), round(float(r), 4)) for t, r in zip(ts, rms)]
    out = []
    i = 0
    n = len(samples)
    while i + win <= n:
        s = 0.0
        for j in range(i, i + win):
            s += samples[j] * samples[j]
        out.append(((i + win / 2) / sr, math.sqrt(s / win)))
        i += win
    mx = max((v for _, v in out), default=0.0)
    if mx > 0:
        out = [(round(t, 4), round(v / mx, 4)) for t, v in out]
    return out or [(0.0, 0.0)]


# --- Public API -------------------------------------------------------------
def analyze(audio_or_video_path):
    """Analyze an audio or video file and return its rhythmic structure.

    Parameters
    ----------
    audio_or_video_path : str
        Path to mp3/wav/m4a, or a video with an audio track. ffmpeg
        decodes it to mono PCM; video is handled transparently.

    Returns
    -------
    dict with keys:
        tempo_bpm       : float
        beats           : list[float]   beat-grid times (seconds)
        downbeats       : list[float]   every strong (bar) beat
        onsets          : list[float]   transient hit times
        energy_envelope : list[(float t, float 0..1)]
        duration        : float         seconds
    """
    if not os.path.isfile(audio_or_video_path):
        raise FileNotFoundError(audio_or_video_path)

    samples, sr = _decode_pcm(audio_or_video_path, SAMPLE_RATE)
    n = len(samples)
    duration = n / float(sr)

    env, fps = _onset_envelope(samples, sr)
    tempo, lag = _autocorr_tempo(env, fps)
    onsets = _pick_onsets(env, fps)
    beats = _beat_grid(env, fps, lag, duration, onsets)
    downbeats = _downbeats(beats, env, fps)
    energy = _energy_envelope(samples, sr)

    return {
        "tempo_bpm": round(float(tempo), 2),
        "beats": beats,
        "downbeats": downbeats,
        "onsets": [round(t, 4) for t in onsets],
        "energy_envelope": energy,
        "duration": round(duration, 4),
    }


def nearest_beat(beats, t):
    """Return the beat time in `beats` closest to `t` (seconds)."""
    if not beats:
        return t
    best = beats[0]
    best_d = abs(best - t)
    for b in beats:
        d = abs(b - t)
        if d < best_d:
            best_d, best = d, b
    return best


def snap_cuts(cut_times, beats, max_shift=0.12):
    """Nudge each cut onto the nearest beat, but only within max_shift seconds.

    Cuts further than max_shift from any beat are left untouched, so a cut in
    a beatless passage is not dragged somewhere musically wrong. Output is
    sorted and de-duplicated.
    """
    if not beats:
        return list(cut_times)
    snapped = []
    for c in cut_times:
        b = nearest_beat(beats, c)
        snapped.append(round(b, 4) if abs(b - c) <= max_shift else round(c, 4))
    out = sorted(set(snapped))
    return out


def accents(beats, downbeats, energy):
    """Return (t, strength 0..1) marks for visual hits (zoom/flash/shake).

    Downbeats accent strongest; other beats scale with local audio energy so
    punches land hardest where the music is loudest.
    """
    if not beats:
        return []
    down = set(round(d, 4) for d in downbeats)

    def energy_at(t):
        if not energy:
            return 0.0
        lo, hi = 0, len(energy) - 1
        while lo < hi:  # binary search nearest sample
            mid = (lo + hi) // 2
            if energy[mid][0] < t:
                lo = mid + 1
            else:
                hi = mid
        return energy[lo][1]

    out = []
    for b in beats:
        e = energy_at(b)
        if round(b, 4) in down:
            strength = min(1.0, 0.7 + 0.3 * e)
        else:
            strength = min(1.0, 0.35 + 0.45 * e)
        out.append((round(b, 4), round(strength, 3)))
    return out


# --- Self-test --------------------------------------------------------------
def _synth_click(path, bpm, seconds, sr=44100):
    """Render a click track at a known BPM with ffmpeg (metronome clicks)."""
    period = 60.0 / bpm
    # Short decaying 1 kHz sine burst at each beat, built as an expression.
    # 'mod(t,period)' resets each beat; gate the first ~25 ms, decay it.
    expr = (
        "sin(2*PI*1000*mod(t\\,{p}))"
        "*exp(-30*mod(t\\,{p}))"
        "*lt(mod(t\\,{p})\\,0.03)"
    ).format(p=period)
    cmd = [
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi",
        "-i", "aevalsrc=%s:s=%d:d=%g" % (expr, sr, seconds),
        "-ac", "1", path,
    ]
    rc, out, err = _run(cmd)
    if rc != 0:
        raise RuntimeError("click synth failed: " + err.decode("utf-8", "replace"))


def _selftest():
    tmpdir = tempfile.mkdtemp(prefix="beat_selftest_")
    wav = os.path.join(tmpdir, "click_120.wav")
    try:
        import time
        target_bpm = 120.0
        _synth_click(wav, target_bpm, 20.0)
        t0 = time.time()
        res = analyze(wav)
        elapsed = time.time() - t0

        det_bpm = res["tempo_bpm"]
        beats = res["beats"]
        # Expected beats: multiples of 0.5 s for 120 BPM.
        period = 60.0 / target_bpm
        errs = []
        for b in beats:
            ideal = round(b / period) * period
            errs.append(abs(b - ideal))
        max_err_ms = (max(errs) * 1000.0) if errs else 999.0
        mean_err_ms = (sum(errs) / len(errs) * 1000.0) if errs else 999.0

        bpm_ok = abs(det_bpm - target_bpm) <= 3.0
        align_ok = max_err_ms <= 40.0
        ok = bpm_ok and align_ok

        print("numpy path       :", "yes" if HAVE_NUMPY else "pure-Python")
        print("target BPM       :", target_bpm)
        print("detected BPM     :", det_bpm, "(ok)" if bpm_ok else "(FAIL)")
        print("beats detected   :", len(beats))
        print("downbeats        :", len(res["downbeats"]))
        print("onsets detected  :", len(res["onsets"]))
        print("beat align max   : %.1f ms %s" % (max_err_ms, "(ok)" if align_ok else "(FAIL)"))
        print("beat align mean  : %.1f ms" % mean_err_ms)
        print("analyze runtime  : %.3f s for %.0fs audio" % (elapsed, res["duration"]))
        print("RESULT           :", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        try:
            for f in os.listdir(tmpdir):
                os.remove(os.path.join(tmpdir, f))
            os.rmdir(tmpdir)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(_selftest())
