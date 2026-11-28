"""Synthetic test audio with known structure (no recordings of anyone): formant-synthesised 'speech'.

synth_speech(units, f0=130, noise_db=-58, hum_db=None, seed=0) -> (mono float64, [(kind, t0, t1)])
  units: (kind, seconds) with kind in vowels 'a e i o u', 'm' (nasal), 's' (sibilant), 'f' (lip-teeth),
         'p' (closure + burst) or 'sil'. A pink noise floor and optional 50 Hz hum are added under everything.
synth_sentence(seed) -> a three-word line with pauses, plus the word spans (for subtitle timing tests)
"""
from __future__ import annotations

import numpy as np

from . import dsp

SR = dsp.SR
VOWEL_F = {'a': (750, 1220, 2600), 'e': (520, 1850, 2600), 'i': (300, 2250, 3000), 'o': (480, 850, 2500),
           'u': (330, 800, 2350), 'm': (260, 1200, 2500)}


def _formant(src, fs, bws=(90, 110, 160)):
    n = len(src)
    N = dsp.me._pow2(n + 256)
    f = np.fft.rfftfreq(N, 1.0 / SR)
    H = np.full_like(f, 0.02)
    for F, B, g in zip(fs, bws, (1.0, 0.6, 0.3)):
        H += g / (1.0 + ((f - F) / (B / 2.0)) ** 2)
    return np.fft.irfft(np.fft.rfft(src, N) * H, N)[:n]


def synth_speech(units, f0=130.0, noise_db=-58.0, hum_db=None, seed=0, level_db=-16.0):
    rng = np.random.default_rng(seed)
    parts, spans, t = [], [], 0.0
    for kind, dur in units:
        n = int(dur * SR)
        tt = np.arange(n) / SR
        if kind in VOWEL_F:
            ff = f0 * (1 + 0.015 * np.sin(np.pi * tt / max(dur, 1e-3))) * (1 + 0.002 * dsp.filt(rng.standard_normal(n), hi=40))
            src = dsp.me._osc(dsp.me._table('saw', f0, 6000), ff, n, rng.random())
            y = _formant(src, VOWEL_F[kind])
            if kind == 'm':
                y = dsp.filt(y, hi=1500) * 0.35
            env = np.minimum(1, tt / 0.02) * np.minimum(1, (dur - tt) / 0.02)
            y = y * env
        elif kind == 's':
            y = dsp.filt(rng.standard_normal(n), lo=4500, hi=10000, order=3) * 0.5
            y *= np.minimum(1, tt / 0.015) * np.minimum(1, (dur - tt) / 0.015)
        elif kind == 'f':
            y = dsp.filt(rng.standard_normal(n), lo=1500, hi=8000) * 0.12
            y *= np.minimum(1, tt / 0.015) * np.minimum(1, (dur - tt) / 0.015)
        elif kind == 'p':
            y = np.zeros(n)
            b = min(n, int(0.012 * SR))
            y[n - b:] = rng.standard_normal(b) * np.exp(-np.arange(b) / (0.003 * SR)) * 0.6
        else:
            y = np.zeros(n)
        parts.append(y)
        spans.append((kind, round(t, 4), round(t + dur, 4)))
        t += dur
    x = np.concatenate(parts) if parts else np.zeros(1)
    voiced = np.abs(x) > 0
    if np.any(voiced):
        x = x / (np.sqrt(np.mean(x[voiced] ** 2)) + 1e-12) * dsp.undb(level_db)
    x = x + dsp.pink(len(x), rng) * dsp.undb(noise_db)
    if hum_db is not None:
        x = x + np.sin(2 * np.pi * 50 * np.arange(len(x)) / SR) * dsp.undb(hum_db)
    return x, spans


def synth_sentence(seed=0, noise_db=-58.0, hum_db=None):
    units = [('sil', 0.35), ('m', 0.08), ('a', 0.22), ('s', 0.12), ('o', 0.2), ('sil', 0.3),
             ('f', 0.1), ('e', 0.25), ('p', 0.07), ('a', 0.2), ('sil', 0.32), ('s', 0.14), ('i', 0.3),
             ('u', 0.18), ('sil', 0.5)]
    x, spans = synth_speech(units, seed=seed, noise_db=noise_db, hum_db=hum_db)
    words, cur = [], None
    for kind, a, b in spans:
        if kind == 'sil':
            if cur:
                words.append(cur)
                cur = None
        else:
            cur = [a, b] if cur is None else [cur[0], b]
    if cur:
        words.append(cur)
    return x, spans, words
