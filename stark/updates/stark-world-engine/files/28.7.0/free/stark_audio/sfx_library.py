"""Procedural sound-effect library: every sound is synthesised from math (no samples), seeded, reproducible.

Categories: foot (per surface), move, combat, magic, ui (tuned to the world's key), creature, spot (spatial ambience
one-shots), weather (seamless loops + thunder), amb (seamless ambience beds), world (doors, portals, chests).

Each catalog entry carries the runtime metadata the cartridge needs to keep repetition unnoticeable:
variants (different synthesis seeds), pitch/gain jitter ranges, cooldown, voice limit, bus, spatial flag, caption.

API
---
CATALOG                         name -> entry dict
render(name, variant=0, ctx=None, dur=None) -> float64 array (mono 1-D or stereo (2, n)), level-normalised
render_set(names, out_dir, variants=None, ctx=None) -> manifest dict (+ WAVs and sfx_manifest.json in out_dir)
sounds_for_world(world)         the subset a world needs (surfaces, beds, weather, creatures by its moods/objects)
LEGACY_MAP                      the studio engine's sfx keys (jump, coin, hit, ...) -> library names
"""
from __future__ import annotations

import math
import os
import random
import zlib

import numpy as np

from . import dsp
from .dsp import (SR, secs, t_axis, filt, cfilt, env_perc, env_bell, chirp, modal, grains, place, pan, fade,
                  stft_shape, bell, periodic_curve, brown, pink, undb)

me = dsp.me
VERSION = 1


def _rng(name, variant, seed=0):
    return np.random.default_rng(zlib.crc32(('%s|%d|%d' % (name, variant, seed)).encode()) & 0xFFFFFFFF)


def _noise(n, rng):
    return rng.standard_normal(n)


def _burst(n, rng, lo, hi, a, d, order=2):
    return filt(rng.standard_normal(n), lo=lo, hi=hi, order=order) * env_perc(n, a, d)


def _thump(n, f0, f1, tau, d, a=0.0008):
    return chirp(f0, f1, n, tau) * env_perc(n, a, d)


def _at(n, sig, t0, gain=1.0):
    """Mono place: sig into a zero array of length n at t0 seconds."""
    y = np.zeros(n)
    i = int(round(t0 * SR))
    if i >= n:
        return y
    m = min(len(sig), n - i)
    y[i:i + m] = sig[:m] * gain
    return y


def _bubble(n, rng, f0, rise=1.8, d=0.02):
    """Minnaert-style bubble: a decaying sine whose pitch rises as the bubble shrinks."""
    t = t_axis(n)
    f = f0 * (1 + (rise - 1) * (1 - np.exp(-t / max(d * 1.5, 1e-4))))
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * env_perc(n, 0.0005, d)


def _formants(x, formants, floor_db=-28.0):
    """Static vocal-tract style filter: sum of resonances [(F, bandwidth, gain_db)] over a small floor."""
    n = x.shape[-1]
    N = me._pow2(n + 256)
    f = np.fft.rfftfreq(N, 1.0 / SR)
    H = np.full_like(f, undb(floor_db))
    for F, B, g in formants:
        H += undb(g) / (1.0 + ((f - F) / (B / 2.0)) ** 2)
    return np.fft.irfft(np.fft.rfft(x, N) * H, N)[..., :n]


def _glottal(n, f0, rng, jitter=0.01, rough=0.0):
    """Band-limited saw source with pitch jitter and optional amplitude roughness (growls)."""
    f0 = np.broadcast_to(np.asarray(f0, dtype=np.float64), (n,)).copy()
    if jitter:
        f0 *= 1 + jitter * filt(rng.standard_normal(n), hi=30)
    tab = me._table('saw', float(np.mean(f0)), 5200)
    y = me._osc(tab, f0, n, rng.random())
    if rough:
        y *= 1 + rough * filt(rng.standard_normal(n), lo=15, hi=60) * 3
    return y


VOWELS = {  # (F1, F2, F3) Hz, bandwidths
    'a': ((750, 110), (1220, 120), (2600, 160)), 'e': ((520, 90), (1850, 120), (2600, 160)),
    'i': ((300, 70), (2250, 110), (3000, 180)), 'o': ((480, 90), (850, 100), (2500, 160)),
    'u': ((330, 80), (800, 100), (2350, 160)), 'uh': ((620, 100), (1150, 110), (2450, 160)),
}


def _vocal(n, f0, vowel, rng, breath=0.25, rough=0.0, jitter=0.012):
    src = _glottal(n, f0, rng, jitter, rough)
    src += breath * filt(rng.standard_normal(n), lo=400, hi=6000) * 2.5
    fm = [(F, B, 0.0 - 3 * k) for k, (F, B) in enumerate(VOWELS[vowel])]
    return _formants(src, fm)


def _whoosh(n, rng, f_lo, f_hi, peak_t, width_t, width_oct=0.7, tonal=0.0):
    """Swept band of noise (a passing blade/arm/gust): centre lo -> hi -> lo around peak_t, bell amplitude."""
    x = rng.standard_normal(n)
    dur = n / SR

    def g(tc, fr):
        shape = np.exp(-0.5 * ((tc - peak_t) / max(width_t, 1e-3)) ** 2)[:, None]
        fc = f_lo + (f_hi - f_lo) * shape
        out = bell(fr, fc, width_oct) * shape
        if tonal:
            out = out + tonal * bell(fr, fc * 0.62, 0.08) * shape
        return out
    y = stft_shape(x, g, nfft=512, hop=128)
    return y * np.minimum(1, t_axis(n) / 0.004) * np.clip((dur - t_axis(n)) / 0.01, 0, 1)


def _sweep_pan(y, p0, p1):
    n = len(y)
    th = (np.clip(np.linspace(p0, p1, n), -1, 1) + 1) * math.pi / 4
    return np.vstack([y * np.cos(th) * math.sqrt(2), y * np.sin(th) * math.sqrt(2)])


def _tonic_midi(ctx, octave=5):
    pc = int((ctx or {}).get('tonic', 0)) % 12
    return 12 * (octave + 1) + pc


def _mallet(midi, n, rng, ratio=4.0, idx=1.2, dec=0.25):
    f = me.mtof(midi)
    t = t_axis(n)
    I = idx * np.exp(-t / 0.05)
    y = np.sin(2 * np.pi * f * t + I * np.sin(2 * np.pi * f * ratio * t)) * np.exp(-t / dec)
    return y * np.minimum(1, t / 0.0008)


def _bellnote(midi, n, rng, dec=1.2, ratio=3.5, idx=2.0):
    f = me.mtof(midi)
    t = t_axis(n)
    I = idx * np.exp(-t / 0.4)
    y = np.sin(2 * np.pi * f * t + I * np.sin(2 * np.pi * f * ratio * t))
    y += 0.3 * np.sin(2 * np.pi * f * 2.0 * t) * np.exp(-t / (dec * 0.5))
    return y * np.exp(-t / dec) * np.minimum(1, t / 0.001)


# =================================================================================================================
# footsteps
# =================================================================================================================
SURFACES = {
    #          thump f0/f1/tau/d/amp          body lo/hi/d/amp      click amp  grains rate/lo/hi/a/d/amp       toe_t len
    'grass':  dict(th=(130, 55, 0.012, 0.030, 0.55), body=(60, 500, 0.025, 0.35), click=0.04,
                   gr=(1800, 2500, 9000, 0.012, 0.070, 0.55), swish=0.25, toe=0.10, len=0.30),
    'leaves': dict(th=(140, 60, 0.010, 0.025, 0.40), body=(60, 600, 0.020, 0.25), click=0.05,
                   gr=(2600, 1500, 8000, 0.006, 0.090, 0.95), gmax=0.0015, swish=0.15, toe=0.11, len=0.32),
    'dirt':   dict(th=(150, 60, 0.012, 0.035, 0.90), body=(60, 1500, 0.030, 0.60), click=0.10,
                   gr=(420, 1000, 5000, 0.004, 0.050, 0.25), scuff=0.35, toe=0.085, len=0.28),
    'gravel': dict(th=(120, 55, 0.010, 0.025, 0.60), body=(60, 900, 0.020, 0.30), click=0.12,
                   gr=(3000, 1200, 7000, 0.006, 0.060, 1.00), pings=10, toe=0.10, len=0.30),
    'stone':  dict(th=(110, 60, 0.008, 0.018, 0.50), body=(80, 900, 0.012, 0.25), click=0.60,
                   modes=((230, 0.014, 0.5), (560, 0.009, 0.35), (1150, 0.006, 0.25), (2300, 0.004, 0.15)),
                   refl=0.25, scuff=0.08, toe=0.085, len=0.26),
    'wood':   dict(th=(120, 55, 0.010, 0.030, 0.60), body=(60, 700, 0.025, 0.30), click=0.30, wood=True,
                   creak=0.35, toe=0.09, len=0.36),
    'snow':   dict(th=(90, 45, 0.015, 0.045, 0.45), body=(40, 400, 0.040, 0.30), click=0.0,
                   gr=(1600, 400, 4500, 0.030, 0.080, 0.90), gmax=0.004, squeak=0.10, toe=0.13, len=0.34),
    'sand':   dict(th=(100, 50, 0.012, 0.035, 0.35), body=(40, 300, 0.030, 0.30), click=0.0,
                   gr=(5000, 3000, 10000, 0.020, 0.100, 0.20), hiss=0.35, toe=0.12, len=0.34),
    'water':  dict(th=(90, 45, 0.012, 0.030, 0.25), body=(40, 500, 0.030, 0.20), click=0.0,
                   splash=0.85, bubbles=(3, 6), drips=(1, 3), toe=0.14, len=0.48),
    'mud':    dict(th=(100, 45, 0.015, 0.050, 0.60), body=(40, 900, 0.080, 0.70), click=0.0,
                   pops=(2, 3), suction=0.40, toe=0.15, len=0.42),
    'metal':  dict(th=(110, 55, 0.010, 0.025, 0.50), body=(80, 900, 0.015, 0.20), click=0.50, metal=True,
                   toe=0.09, len=0.50),
}


def _foot_strike(n, rng, s, k=1.0):
    y = np.zeros(n)
    f0, f1, tau, d, a = s['th']
    j = 2 ** (rng.uniform(-2, 2) / 12)
    y += _thump(n, f0 * j, f1, tau, d) * a * k
    lo, hi, bd, ba = s['body']
    y += _burst(n, rng, lo, hi * j, 0.0006, bd) * ba * k
    if s.get('click'):
        y += _burst(n, rng, 2000, 11000, 0.0002, 0.0016) * s['click'] * k
    if s.get('modes'):
        mj = 2 ** (rng.uniform(-1.5, 1.5) / 12)
        y += modal(n, [(f * mj, dd, aa) for f, dd, aa in s['modes']], rng, 0.01) * 0.6 * k
    if s.get('wood'):
        fw = rng.uniform(150, 215)
        y += modal(n, [(fw, 0.07, 1.0), (fw * 2.32, 0.045, 0.5), (fw * 3.91, 0.03, 0.35), (fw * 5.6, 0.018, 0.2)],
                   rng, 0.01) * 0.55 * k
    if s.get('metal'):
        fm = rng.uniform(380, 650)
        rat = (1, 1.59, 2.14, 2.65, 3.6, 4.3, 5.8)
        dec = (0.25, 0.2, 0.15, 0.12, 0.09, 0.07, 0.05)
        amp = (1, 0.7, 0.6, 0.5, 0.35, 0.25, 0.18)
        y += modal(n, [(fm * r, dd, aa) for r, dd, aa in zip(rat, dec, amp)], rng, 0.004) * 0.3 * k
    if s.get('gr'):
        rate, glo, ghi, ga, gd, gamp = s['gr']
        env = env_perc(n, ga, gd)
        y += grains(n, rate, rng, 0.0004, s.get('gmax', 0.003), glo, ghi, env) * gamp * 0.35 * k
    if s.get('swish'):
        sw = filt(rng.standard_normal(n), lo=1500, hi=6000) * env_bell(n, 0.05, 0.03)
        y += sw * s['swish'] * 0.3 * k
    if s.get('pings'):
        for _ in range(int(s['pings'] * rng.uniform(0.6, 1.3))):
            tp = rng.uniform(0.003, 0.09)
            m = secs(0.03)
            ping = modal(m, [(rng.uniform(2000, 5200), rng.uniform(0.004, 0.01), 1.0)], rng)
            y += _at(n, ping, tp, rng.uniform(0.04, 0.16) * k)
    if s.get('squeak'):
        m = secs(0.06)
        tt = t_axis(m)
        fq = rng.uniform(950, 1500) * (1 + 0.04 * np.sin(2 * np.pi * rng.uniform(30, 60) * tt))
        sq = np.sin(2 * np.pi * np.cumsum(fq) / SR) * env_bell(m, 0.03, 0.012)
        sq *= 1 + 0.5 * filt(rng.standard_normal(m), hi=200)
        y += _at(n, sq, rng.uniform(0.02, 0.05), s['squeak'] * k)
    if s.get('hiss'):
        y += filt(rng.standard_normal(n), lo=2500, hi=10000) * env_perc(n, 0.02, 0.09) * s['hiss'] * 0.4 * k
    if s.get('splash'):
        y += _burst(n, rng, 600, 7000, 0.002, 0.055) * s['splash'] * 0.6 * k
        for _ in range(rng.integers(s['bubbles'][0], s['bubbles'][1] + 1)):
            m = secs(0.06)
            y += _at(n, _bubble(m, rng, rng.uniform(500, 1400), rng.uniform(1.5, 2.3), rng.uniform(0.01, 0.03)),
                     rng.uniform(0.005, 0.14), rng.uniform(0.08, 0.25) * k)
    if s.get('pops'):
        for _ in range(rng.integers(s['pops'][0], s['pops'][1] + 1)):
            m = secs(0.08)
            y += _at(n, _bubble(m, rng, rng.uniform(180, 450), 1.7, rng.uniform(0.02, 0.04)),
                     rng.uniform(0.01, 0.12), rng.uniform(0.2, 0.4) * k)
    if s.get('scuff'):
        sc_t = rng.uniform(0.03, 0.06)
        sc = filt(rng.standard_normal(n), lo=800, hi=4200) * env_perc(n, 0.01, 0.035)
        y += _at(n, sc, sc_t, s['scuff'] * 0.5 * k)
    return y


def r_foot(name, rng, v, ctx, dur=None):
    surf = name.split('.', 1)[1]
    s = SURFACES[surf]
    n = secs(s['len'])
    y = _foot_strike(n, rng, s, 1.0)
    toe_t = s['toe'] * rng.uniform(0.85, 1.15)
    toe = _foot_strike(n, rng, s, 0.45)
    y += _at(n, toe, toe_t)
    if s.get('drips'):
        for _ in range(rng.integers(s['drips'][0], s['drips'][1] + 1)):
            m = secs(0.05)
            y += _at(n, _bubble(m, rng, rng.uniform(1500, 2800), 1.4, 0.012), rng.uniform(0.18, 0.4),
                     rng.uniform(0.05, 0.12))
    if s.get('suction'):
        m = secs(0.12)
        x = rng.standard_normal(m)
        sucks = stft_shape(x, lambda tc, fr: bell(fr, (300 + 1800 * np.clip(tc / 0.1, 0, 1))[:, None], 0.8)
                           * np.exp(-0.5 * ((tc - 0.06) / 0.03) ** 2)[:, None], nfft=256, hop=64)
        y += _at(n, sucks, toe_t + 0.01, s['suction'])
    if s.get('creak') and rng.random() < s['creak']:
        m = secs(0.16)
        tt = t_axis(m)
        f = rng.uniform(80, 150) * (1 + 0.25 * tt / 0.16) * (1 + 0.03 * filt(rng.standard_normal(m), hi=40))
        cr = me._osc(me._table('pulse12', 100, 5000), f, m) * np.sin(np.pi * np.clip(tt / 0.16, 0, 1))
        cr = filt(cr, lo=300, hi=4000, peak=((800, 8, 3), (1700, 6, 4)))
        y += _at(n, cr, rng.uniform(0.04, 0.12), 0.06)
    if s.get('refl'):
        d = rng.uniform(0.009, 0.017)
        y += _at(n, filt(y, hi=3500), d, s['refl'])
    y = np.tanh(y * 1.3) / 1.3
    return dsp.stereo(fade(y, 0.0005, 0.02), 0.25, 0.25)


# =================================================================================================================
# movement, combat
# =================================================================================================================
def r_move(name, rng, v, ctx, dur=None):
    k = name.split('.', 1)[1]
    if k == 'jump':
        n = secs(0.3)
        cloth = _whoosh(n, rng, 500, 1800, 0.08, 0.04, 0.9)
        breath = filt(rng.standard_normal(n), lo=600, hi=3500) * env_bell(n, 0.05, 0.03) * 0.3
        return dsp.stereo(fade(cloth + breath, 0.002, 0.03), 0.3, 0.4)
    if k == 'land':
        n = secs(0.4)
        y = _thump(n, 120, 40, 0.015, 0.08) * 1.0 + _burst(n, rng, 60, 900, 0.001, 0.05) * 0.6
        y += filt(rng.standard_normal(n), lo=500, hi=3000) * env_perc(n, 0.004, 0.07) * 0.25
        return dsp.stereo(fade(np.tanh(1.5 * y), 0.0005, 0.03), 0.2, 0.3)
    if k == 'dash':
        n = secs(0.42)
        y = _whoosh(n, rng, 350, 2400, 0.16, 0.07, 0.8, tonal=0.2)
        y += filt(rng.standard_normal(n), lo=400, hi=2500) * env_bell(n, 0.12, 0.08) * 0.15
        return _sweep_pan(fade(y, 0.002, 0.04), -0.5, 0.5)
    raise KeyError(name)


def r_combat(name, rng, v, ctx, dur=None):
    k = name.split('.', 1)[1]
    if k in ('swing', 'swing_heavy'):
        heavy = k == 'swing_heavy'
        n = secs(0.46 if heavy else 0.32)
        y = _whoosh(n, rng, 300 if heavy else 600, rng.uniform(1300, 1700) if heavy else rng.uniform(2400, 3600),
                    0.17 if heavy else 0.12, 0.06 if heavy else 0.042, 0.75, tonal=0.35)
        p = rng.choice([-1, 1]) * 0.45
        return _sweep_pan(fade(y, 0.002, 0.03), -p, p)
    if k == 'hit_flesh':
        n = secs(0.36)
        y = _thump(n, rng.uniform(150, 200), 50, 0.015, 0.06) * 1.0
        y += _burst(n, rng, 300, 3500, 0.0005, 0.012) * 0.7
        y += grains(n, 1500, rng, 0.0004, 0.002, 800, 5000, env_perc(n, 0.001, 0.03)) * 0.25
        y += _burst(n, rng, None, 700, 0.002, 0.05) * 0.35
        return dsp.stereo(fade(np.tanh(1.8 * y), 0.0003, 0.02), 0.2, 0.3)
    if k == 'clang':
        n = secs(1.4)
        f0 = rng.uniform(750, 1050)
        rat, dec, amp = (1, 2.756, 5.404, 8.933), (0.9, 0.55, 0.32, 0.2), (1, 0.6, 0.4, 0.25)
        sc = rng.uniform(0.7, 1.2)
        L = modal(n, [(f0 * r, d * sc, a) for r, d, a in zip(rat, dec, amp)], rng, 0.002)
        R = modal(n, [(f0 * r * 1.006, d * sc, a) for r, d, a in zip(rat, dec, amp)], rng, 0.002)
        tr = _burst(n, rng, 2500, 12000, 0.0002, 0.002) * 0.9
        scr = _burst(n, rng, 3000, 8000, 0.003, 0.05) * 0.15
        return fade(np.vstack([L * 0.5 + tr + scr, R * 0.5 + tr + scr]), 0.0002, 0.05)
    if k == 'block':
        n = secs(0.6)
        fw = rng.uniform(170, 260)
        y = modal(n, [(fw, 0.08, 1.0), (fw * 2.1, 0.05, 0.6), (fw * 3.4, 0.03, 0.35)], rng, 0.01) * 0.8
        y += modal(n, [(rng.uniform(1200, 1600), 0.25, 0.3), (rng.uniform(2600, 3300), 0.15, 0.2)], rng) * 0.4
        y += _thump(n, 130, 55, 0.012, 0.05) * 0.8 + _burst(n, rng, 1500, 9000, 0.0002, 0.002) * 0.5
        return dsp.stereo(fade(np.tanh(1.4 * y), 0.0003, 0.04), 0.2, 0.3)
    if k == 'blunt':
        n = secs(0.45)
        y = _thump(n, rng.uniform(120, 160), 40, 0.02, 0.09) * 1.2
        y += _burst(n, rng, 200, 2500, 0.001, 0.04) * 0.6
        y += grains(n, 900, rng, 0.0005, 0.003, 400, 3000, env_perc(n, 0.002, 0.05)) * 0.3
        return dsp.stereo(fade(np.tanh(2.2 * y), 0.0003, 0.03), 0.15, 0.3)
    if k == 'bow_draw':
        n = secs(0.62)
        tt = t_axis(n)
        f = rng.uniform(60, 90) * (1 + 0.8 * tt / 0.62) * (1 + 0.04 * filt(rng.standard_normal(n), hi=30))
        cr = me._osc(me._table('pulse12', 80, 4000), f, n) * np.clip(tt / 0.05, 0, 1) * np.clip((0.62 - tt) / 0.05, 0, 1)
        cr = filt(cr, lo=200, hi=3800, peak=((650, 8, 3), (1400, 6, 4)))
        ten = np.sin(2 * np.pi * np.cumsum(200 + 140 * tt / 0.62) / SR) * 0.08 * np.clip(tt / 0.3, 0, 1)
        return dsp.stereo(fade(cr * 0.35 + ten, 0.003, 0.03), 0.3, 0.4)
    if k == 'bow_release':
        n = secs(0.55)
        y = me._ks(rng.uniform(140, 200), n, 0.25, 0.85, rng) * 0.8
        y += _whoosh(n, rng, 2000, 5200, 0.05, 0.03, 0.6) * 0.5
        y += _thump(n, 140, 60, 0.008, 0.02) * 0.4
        return dsp.stereo(fade(y, 0.0003, 0.05), 0.3, 0.4)
    if k == 'arrow_hit':
        n = secs(0.5)
        fw = rng.uniform(380, 520)
        y = modal(n, [(fw, 0.04, 1.0), (fw * 2.3, 0.025, 0.5)], rng) * 0.8 + _thump(n, 150, 60, 0.01, 0.03) * 0.6
        tt = t_axis(n)
        quiv = np.sin(2 * np.pi * rng.uniform(90, 140) * tt) * (0.5 + 0.5 * np.sin(2 * np.pi * rng.uniform(25, 35) * tt))
        y += quiv * np.exp(-tt / 0.12) * 0.25 * (tt > 0.01)
        return dsp.stereo(fade(y + _burst(n, rng, 2000, 9000, 0.0002, 0.002) * 0.4, 0.0003, 0.04), 0.2, 0.3)
    if k == 'crit':
        n = secs(0.95)
        pre = secs(0.09)
        x = rng.standard_normal(pre)
        rev = stft_shape(x, lambda tc, fr: bell(fr, (800 + 4000 * np.clip(tc / 0.09, 0, 1))[:, None], 0.8)
                         * (np.clip(tc / 0.09, 0, 1) ** 2)[:, None], nfft=256, hop=64)
        y = np.zeros(n)
        y[:pre] += rev * 0.5
        hit = _thump(n, 130, 35, 0.03, 0.16) * 1.3 + _burst(n, rng, 1500, 10000, 0.0002, 0.006) * 0.9
        hit += chirp(60, 30, n, 0.15) * env_perc(n, 0.002, 0.25) * 0.8
        hit += modal(n, [(rng.uniform(900, 1300), 0.3, 0.3), (rng.uniform(2400, 3000), 0.18, 0.2)], rng) * 0.5
        y += _at(n, hit, 0.09)
        y = np.tanh(1.6 * y)
        return fade(dsp.reverb(dsp.stereo(y, 0.3, 0.4), 1.1, 0.12, seed=31)[:, :n], 0.002, 0.08)
    if k == 'body_fall':
        n = secs(0.62)
        y = _thump(n, 90, 35, 0.02, 0.12) * 1.1 + filt(rng.standard_normal(n), lo=400, hi=3000) * env_perc(n, 0.01, 0.12) * 0.35
        for _ in range(rng.integers(6, 11)):
            m = secs(0.03)
            y += _at(n, modal(m, [(rng.uniform(2000, 4200), 0.01, 1.0)], rng), rng.uniform(0.05, 0.26),
                     rng.uniform(0.03, 0.1))
        return dsp.stereo(fade(np.tanh(1.5 * y), 0.0005, 0.05), 0.25, 0.3)
    if k == 'parry':
        n = secs(0.9)
        f0 = rng.uniform(1200, 1500)
        y = modal(n, [(f0, 0.35, 1.0), (f0 * 2.756, 0.2, 0.5), (f0 * 5.4, 0.1, 0.3)], rng, 0.003) * 0.5
        x = rng.standard_normal(n)
        scr = stft_shape(x, lambda tc, fr: bell(fr, (6000 - 3500 * np.clip(tc / 0.22, 0, 1))[:, None], 0.5)
                         * (np.exp(-tc / 0.12) * (tc < 0.3))[:, None], nfft=512, hop=128)
        y += scr * 0.4 + _burst(n, rng, 2500, 12000, 0.0002, 0.002) * 0.8
        return _sweep_pan(fade(y, 0.0003, 0.05), -0.2, 0.3)
    if k == 'player_hurt':
        n = secs(0.42)
        y = _thump(n, 130, 50, 0.015, 0.05) * 0.8 + _burst(n, rng, 300, 3000, 0.0005, 0.01) * 0.4
        m = secs(0.2)
        tt = t_axis(m)
        f0 = rng.uniform(115, 150) * (1 - 0.18 * tt / 0.2)
        voc = _vocal(m, f0, 'uh', rng, breath=0.5) * np.sin(np.pi * np.clip(tt / 0.2, 0, 1)) ** 0.7
        y += _at(n, voc / (np.max(np.abs(voc)) + 1e-9), 0.012, 0.35)
        return dsp.stereo(fade(np.tanh(1.5 * y), 0.0005, 0.04), 0.1, 0.2)
    if k == 'enemy_die':
        n = secs(0.85)
        x = rng.standard_normal(n)
        y = stft_shape(x, lambda tc, fr: bell(fr, (3200 * np.exp(-tc / 0.25) + 250)[:, None], 0.9)
                       * (np.clip(tc / 0.01, 0, 1) * np.exp(-tc / 0.3))[:, None], nfft=512, hop=128)
        y += _thump(n, 110, 40, 0.02, 0.1) * 0.6
        y += grains(n, 300, rng, 0.0004, 0.002, 4000, 11000, env_perc(n, 0.05, 0.25)) * 0.25
        return dsp.stereo(fade(y, 0.001, 0.08), 0.35, 0.5)
    raise KeyError(name)


# =================================================================================================================
# magic
# =================================================================================================================
def r_magic(name, rng, v, ctx, dur=None):
    k = name.split('.', 1)[1]
    if k == 'fire_cast':
        n = secs(0.95)
        y = _whoosh(n, rng, 400, 1800, 0.25, 0.12, 1.0) * 0.7
        roar = brown(n, rng) * env_perc(n, 0.08, 0.35)
        y += filt(roar, hi=600) * 0.5
        y += grains(n, rng.uniform(60, 120), rng, 0.0005, 0.004, 1500, 7000, env_perc(n, 0.05, 0.5), amp_sigma=1.0) * 0.6
        return dsp.stereo(fade(np.tanh(1.3 * y), 0.003, 0.08), 0.4, 0.5)
    if k == 'fire_hit':
        n = secs(1.2)
        y = chirp(75, 32, n, 0.2) * env_perc(n, 0.002, 0.3) * 1.0
        x = rng.standard_normal(n)
        y += stft_shape(x, lambda tc, fr: bell(fr, (5000 * np.exp(-tc / 0.15) + 300)[:, None], 1.2)
                        * np.exp(-tc / 0.25)[:, None], nfft=512, hop=128) * 0.8
        y += grains(n, 90, rng, 0.0005, 0.004, 1500, 7000, env_perc(n, 0.1, 0.5), amp_sigma=1.0) * 0.5
        return dsp.reverb(dsp.stereo(fade(np.tanh(1.6 * y), 0.001, 0.1), 0.4, 0.5), 1.2, 0.12, seed=33)[:, :n]
    if k == 'ice_cast':
        n = secs(1.15)
        y = np.zeros(n)
        for _ in range(rng.integers(8, 15)):
            m = secs(0.7)
            f = rng.uniform(2000, 6000)
            tt = t_axis(m)
            b = np.sin(2 * np.pi * f * tt + 1.2 * np.exp(-tt / 0.2) * np.sin(2 * np.pi * f * 1.41 * tt))
            y += _at(n, b * np.exp(-tt / rng.uniform(0.15, 0.5)) * np.minimum(1, tt / 0.001), rng.uniform(0, 0.45),
                     rng.uniform(0.05, 0.15))
        y += grains(n, 60, rng, 0.0003, 0.0012, 3000, 11000, env_perc(n, 0.02, 0.4)) * 0.3
        y += filt(rng.standard_normal(n), lo=4000, hi=12000) * env_bell(n, 0.3, 0.15) * 0.12
        return dsp.reverb(dsp.stereo(fade(y, 0.002, 0.1), 0.6, 0.7), 1.6, 0.2, seed=35)[:, :n]
    if k == 'ice_hit':
        n = secs(0.95)
        y = _thump(n, 140, 60, 0.012, 0.04) * 0.6 + _burst(n, rng, 2500, 12000, 0.0002, 0.003) * 0.8
        for _ in range(rng.integers(20, 40)):
            m = secs(0.12)
            y += _at(n, modal(m, [(rng.uniform(2000, 7000), rng.uniform(0.01, 0.05), 1.0),
                                  (rng.uniform(5000, 9000), 0.01, 0.4)], rng), rng.uniform(0.0, 0.16) ** 1.5 * 2,
                     rng.uniform(0.03, 0.12))
        return dsp.reverb(dsp.stereo(fade(y, 0.0003, 0.08), 0.6, 0.7), 1.3, 0.15, seed=36)[:, :n]
    if k == 'lightning':
        n = secs(1.35)
        tt = t_axis(n)
        flick = (filt(rng.standard_normal(n), hi=rng.uniform(80, 200)) > 0).astype(float)
        zap = filt(rng.standard_normal(n), lo=1500, hi=12000) * flick * env_perc(n, 0.001, 0.12)
        buzz = me._osc(me._table('saw', 300, 7000), 2000 * np.exp(-tt / 0.05) + 180, n) * env_perc(n, 0.001, 0.15) * 0.4
        rum = filt(brown(n, rng), hi=220) * env_perc(n, 0.05, 0.5) * 0.7
        y = np.tanh(1.5 * (zap + buzz + rum))
        return dsp.reverb(dsp.stereo(fade(y, 0.0005, 0.1), 0.5, 0.6), 1.8, 0.18, seed=37)[:, :n]
    if k == 'heal':
        n = secs(1.45)
        base = _tonic_midi(ctx, 5)
        y = np.zeros(n)
        for j, iv in enumerate((0, 4, 7, 12, 16)):
            m = secs(1.0)
            y += _at(n, _bellnote(base + iv, m, rng, dec=0.6, ratio=2.0, idx=0.8), 0.06 * j, 0.35)
        tt = t_axis(n)
        pad = sum(np.sin(2 * np.pi * me.mtof(base - 12 + iv) * tt) for iv in (0, 4, 7)) * env_bell(n, 0.55, 0.3) * 0.12
        y += pad + filt(rng.standard_normal(n), lo=5000, hi=12000) * env_bell(n, 0.5, 0.25) * 0.05
        return dsp.reverb(dsp.stereo(fade(y, 0.002, 0.15), 0.6, 0.6), 1.8, 0.22, seed=38)[:, :n]
    if k == 'arcane':
        n = secs(0.85)
        x = rng.standard_normal(n)
        rate = rng.uniform(4, 7)

        def g(tc, fr):
            out = 0
            for j, c in enumerate((700, 1500, 3100)):
                fc = c * (1 + 0.45 * np.sin(2 * np.pi * rate * tc + j * 2.1))
                out = out + bell(fr, fc[:, None], 0.18)
            return out * (np.clip(tc / 0.05, 0, 1) * np.exp(-tc / 0.35))[:, None]
        y = stft_shape(x, g, nfft=1024, hop=256) * 0.9
        base = _tonic_midi(ctx, 6)
        y += _bellnote(base + 7, n, rng, dec=0.4) * 0.15
        return dsp.reverb(_sweep_pan(fade(y, 0.002, 0.08), -0.4, 0.4), 1.4, 0.2, seed=39)[:, :n]
    if k == 'earth':
        n = secs(1.25)
        y = filt(brown(n, rng), hi=160) * env_perc(n, 0.06, 0.5) * 1.0
        for _ in range(rng.integers(6, 11)):
            m = secs(0.12)
            f = rng.uniform(200, 900)
            y += _at(n, modal(m, [(f, 0.03, 1.0), (f * 2.2, 0.02, 0.5)], rng) + _burst(m, rng, 500, 4000, 0.0003, 0.005),
                     rng.uniform(0.02, 0.7), rng.uniform(0.1, 0.35))
        y += grains(n, 400, rng, 0.0005, 0.003, 600, 4000, env_perc(n, 0.05, 0.4)) * 0.3
        return dsp.reverb(dsp.stereo(fade(np.tanh(1.4 * y), 0.004, 0.1), 0.4, 0.5), 1.4, 0.15, seed=40)[:, :n]
    if k == 'wind':
        n = secs(1.0)
        y = _whoosh(n, rng, 300, 1200, 0.4, 0.18, 1.0, tonal=0.25)
        return _sweep_pan(fade(y, 0.004, 0.08), -0.6, 0.6)
    if k == 'teleport':
        n = secs(0.95)
        pre = secs(0.5)
        x = rng.standard_normal(pre)
        sw = stft_shape(x, lambda tc, fr: bell(fr, (400 + 5000 * np.clip(tc / 0.5, 0, 1) ** 2)[:, None], 0.5)
                        * (np.clip(tc / 0.5, 0, 1) ** 2)[:, None], nfft=512, hop=128)
        y = np.zeros(n)
        y[:pre] += sw * 0.6
        y += _at(n, chirp(300, 1600, secs(0.45), None) * env_perc(secs(0.45), 0.4, 0.02) * 0.2, 0.05)
        y += _at(n, _thump(secs(0.2), 220, 70, 0.01, 0.04) + _burst(secs(0.2), rng, 1500, 9000, 0.0003, 0.01), 0.5, 0.8)
        y += _at(n, grains(secs(0.4), 300, rng, 0.0003, 0.0015, 4000, 11000, env_perc(secs(0.4), 0.005, 0.15)), 0.5, 0.4)
        return dsp.reverb(dsp.stereo(fade(y, 0.002, 0.06), 0.5, 0.6), 1.2, 0.15, seed=41)[:, :n]
    raise KeyError(name)


# =================================================================================================================
# UI (tuned to the world's tonic so menus sit in the score's key)
# =================================================================================================================
def r_ui(name, rng, v, ctx, dur=None):
    k = name.split('.', 1)[1]
    base = _tonic_midi(ctx, 5)
    if k == 'click':
        n = secs(0.07)
        y = np.sin(2 * np.pi * rng.uniform(1700, 1950) * t_axis(n)) * env_perc(n, 0.0004, 0.007)
        y += _burst(n, rng, 3500, 12000, 0.0001, 0.0015) * 0.4
        return dsp.stereo(fade(y, 0.0002, 0.01))
    if k == 'hover':
        n = secs(0.05)
        return dsp.stereo(fade(np.sin(2 * np.pi * rng.uniform(2300, 2600) * t_axis(n)) * env_perc(n, 0.002, 0.01), 0.001, 0.01))
    if k in ('confirm', 'back'):
        n = secs(0.42)
        seq = (0, 7) if k == 'confirm' else (7, 0)
        y = np.zeros(n)
        for j, iv in enumerate(seq):
            y += _at(n, _mallet(base + iv, secs(0.3), rng, 4.0, 1.0, 0.12), 0.075 * j, 0.6 if j == 0 else 0.75)
        return dsp.stereo(fade(y, 0.0003, 0.05), 0.2, 0.3)
    if k in ('open', 'close'):
        n = secs(0.45)
        x = rng.standard_normal(n)
        up = k == 'open'
        wh = stft_shape(x, lambda tc, fr: bell(fr, ((1800 + 4000 * np.clip(tc / 0.15, 0, 1)) if up else
                                                    (5800 - 4000 * np.clip(tc / 0.15, 0, 1)))[:, None], 0.6)
                        * np.exp(-0.5 * ((tc - 0.08) / 0.04) ** 2)[:, None], nfft=256, hop=64) * 0.4
        ch = _mallet(base + (12 if up else 0), n, rng, 3.0, 0.8, 0.18) * 0.4
        return dsp.stereo(fade(wh + _at(n, ch, 0.06), 0.001, 0.05), 0.3, 0.4)
    if k == 'error':
        n = secs(0.32)
        f = me.mtof(base - 24)
        y = np.zeros(n)
        for j in range(2):
            m = secs(0.09)
            sq = me._osc(me._table('square', f, 2200), f, m) * env_adsr_box(m)
            y += _at(n, sq, 0.12 * j, 0.5)
        return dsp.stereo(fade(filt(y, hi=2500), 0.001, 0.03))
    if k == 'equip':
        n = secs(0.36)
        y = modal(n, [(rng.uniform(2200, 3200), 0.06, 1.0), (rng.uniform(4500, 6000), 0.03, 0.4)], rng) * 0.4
        y += filt(rng.standard_normal(n), lo=600, hi=4000) * env_perc(n, 0.01, 0.06) * 0.3
        y += _thump(n, 160, 80, 0.01, 0.02) * 0.3
        return dsp.stereo(fade(y, 0.0003, 0.04), 0.2, 0.3)
    if k in ('coin', 'pickup'):
        y, _ = me._sfx_render(k, int(v) + 3)
        return np.asarray(y, dtype=np.float64)
    if k == 'level_up':
        y, _ = me._sfx_render('level_complete', int(v) + 1)
        return dsp.reverb(np.asarray(y, dtype=np.float64), 1.5, 0.15, seed=42)
    if k in ('quest_accept', 'quest_complete'):
        n = secs(0.9 if k == 'quest_accept' else 1.5)
        seq = (0, 4, 7) if k == 'quest_accept' else (0, 4, 7, 12, 16, 19)
        y = np.zeros(n)
        for j, iv in enumerate(seq):
            y += _at(n, _mallet(base + iv, secs(0.6), rng, 4.0, 0.9, 0.25), 0.085 * j, 0.5)
        if k == 'quest_complete':
            y += filt(rng.standard_normal(n), lo=5000, hi=12000) * env_bell(n, 0.6, 0.3) * 0.04
        return dsp.reverb(dsp.stereo(fade(y, 0.0005, 0.08), 0.3, 0.4), 1.4, 0.18, seed=43)[:, :n]
    if k == 'notify':
        n = secs(0.7)
        y = _bellnote(base + 12, n, rng, dec=0.35, ratio=2.0, idx=0.6) * 0.5 + _bellnote(base + 19, n, rng, 0.3, 2.0, 0.5) * 0.2
        return dsp.stereo(fade(y, 0.0005, 0.05), 0.2, 0.3)
    if k == 'talk_blip':
        n = secs(0.06)
        pent = (0, 2, 4, 7, 9, 12)
        f = me.mtof(base + pent[v % len(pent)])
        y = me._osc(me._table('soft', f, 6000), f, n) * env_perc(n, 0.002, 0.02)
        return dsp.stereo(fade(y, 0.001, 0.01))
    if k == 'page':
        n = secs(0.32)
        y = filt(rng.standard_normal(n), lo=1000, hi=6500) * env_bell(n, 0.12, 0.06) * 0.4
        y += grains(n, 500, rng, 0.0003, 0.0015, 2000, 9000, env_bell(n, 0.12, 0.08)) * 0.3
        return dsp.stereo(fade(y, 0.002, 0.04), 0.3, 0.4)
    if k == 'slider':
        n = secs(0.03)
        return dsp.stereo(fade(np.sin(2 * np.pi * rng.uniform(2900, 3300) * t_axis(n)) * env_perc(n, 0.0003, 0.004), 0.0002, 0.005))
    raise KeyError(name)


def env_adsr_box(m):
    e = np.ones(m)
    k = min(m // 4, secs(0.004))
    e[:k] = np.linspace(0, 1, k)
    e[-k:] = np.linspace(1, 0, k)
    return e


# =================================================================================================================
# world objects
# =================================================================================================================
def r_world(name, rng, v, ctx, dur=None):
    k = name.split('.', 1)[1]
    if k == 'door':
        y, _ = me._sfx_render('door', int(v) + 1)
        return np.asarray(y, dtype=np.float64)
    if k == 'explosion':
        y, _ = me._sfx_render('explosion', int(v) + 1)
        return dsp.reverb(np.asarray(y, dtype=np.float64), 2.0, 0.15, seed=44)
    if k == 'chest':
        n = secs(0.9)
        tt = t_axis(n)
        f = rng.uniform(90, 140) * (1 + 0.3 * tt)
        cr = filt(me._osc(me._table('pulse12', 100, 4000), f, n) * np.clip(tt / 0.05, 0, 1) * np.clip((0.5 - tt) / 0.05, 0, 1),
                  lo=250, hi=3500, peak=((700, 8, 3),)) * 0.25
        thud = _at(n, _thump(secs(0.3), 120, 50, 0.01, 0.05) + modal(secs(0.3), [(rng.uniform(180, 260), 0.06, 1.0)], rng) * 0.5, 0.48, 0.7)
        base = _tonic_midi(ctx, 6)
        sparkle = sum(_at(n, _bellnote(base + iv, secs(0.4), rng, 0.25, 2.0, 0.5), 0.55 + 0.05 * j, 0.12)
                      for j, iv in enumerate((0, 7, 12)))
        return dsp.reverb(dsp.stereo(fade(cr + thud + sparkle, 0.002, 0.08), 0.3, 0.4), 1.0, 0.12, seed=45)[:, :n]
    if k == 'portal_loop':
        n = secs(dur or 6.0)
        tt = np.arange(n) / SR
        L = n / SR
        f1 = round(57 * L) / L
        f2 = round(58.5 * L) / L
        dr = np.sin(2 * np.pi * f1 * tt) + 0.8 * np.sin(2 * np.pi * f2 * tt) + 0.3 * np.sin(2 * np.pi * 2 * f1 * tt)
        x = rng.standard_normal(n)
        cyc = 3

        def g(tc, fr):
            fc = 900 * (1 + 0.6 * np.sin(2 * np.pi * cyc * tc / L))
            return bell(fr, fc[:, None], 0.35)
        sw = stft_shape(x, g, nfft=1024, hop=256, circular=True)
        y = np.vstack([dr * 0.4 + sw * 0.6, dr * 0.4 + np.roll(sw, n // 3) * 0.6])
        return dsp.creverb(y, 2.0, 0.2, seed=46)
    raise KeyError(name)


# =================================================================================================================
# creatures + spot ambience one-shots
# =================================================================================================================
def r_creature(name, rng, v, ctx, dur=None):
    k = name.split('.', 1)[1]
    if k == 'wolf_growl':
        n = secs(1.05)
        tt = t_axis(n)
        f0 = rng.uniform(70, 95) * (1 + 0.08 * np.sin(np.pi * tt / 1.05))
        y = _vocal(n, f0, 'o', rng, breath=0.6, rough=0.5, jitter=0.03)
        y *= np.clip(tt / 0.12, 0, 1) * np.clip((1.05 - tt) / 0.25, 0, 1)
        return dsp.stereo(fade(np.tanh(2.0 * y / (np.max(np.abs(y)) + 1e-9)), 0.003, 0.05), 0.3, 0.4)
    if k == 'wolf_howl':
        n = secs(2.3)
        tt = t_axis(n)
        a, b, c = rng.uniform(360, 420), rng.uniform(580, 660), rng.uniform(480, 540)
        f0 = np.where(tt < 0.8, a + (b - a) * np.sin(np.pi / 2 * tt / 0.8), b + (c - b) * np.clip((tt - 0.8) / 1.3, 0, 1))
        f0 *= 1 + 0.012 * np.sin(2 * np.pi * 5.2 * tt) * np.clip((tt - 0.3) / 0.4, 0, 1)
        src = np.sin(2 * np.pi * np.cumsum(f0) / SR) + 0.25 * np.sin(4 * np.pi * np.cumsum(f0) / SR)
        src += 0.08 * filt(rng.standard_normal(n), lo=500, hi=4000)
        y = _formants(src, [(450, 200, 0), (950, 250, -6), (2400, 400, -14)]) * np.clip(tt / 0.25, 0, 1) * np.clip((2.3 - tt) / 0.6, 0, 1)
        return dsp.reverb(dsp.stereo(fade(y, 0.01, 0.1), 0.4, 0.5), 2.5, 0.35, seed=47)[:, :n]
    if k == 'slime':
        n = secs(0.5)
        y = filt(rng.standard_normal(n), hi=800) * env_perc(n, 0.01, 0.12) * 0.6
        for _ in range(rng.integers(3, 6)):
            m = secs(0.1)
            y += _at(n, _bubble(m, rng, rng.uniform(150, 500), 1.9, rng.uniform(0.02, 0.05)), rng.uniform(0, 0.3), rng.uniform(0.3, 0.6))
        return dsp.stereo(fade(y, 0.002, 0.05), 0.3, 0.4)
    if k == 'wisp':
        n = secs(0.75)
        tt = t_axis(n)
        y = np.zeros(n)
        for j in range(rng.integers(2, 4)):
            m = secs(0.25)
            t2 = t_axis(m)
            f = rng.uniform(2000, 4000) * (1 + 0.1 * np.sin(2 * np.pi * rng.uniform(6, 10) * t2))
            tr = np.sin(2 * np.pi * np.cumsum(f) / SR) * (0.5 + 0.5 * np.sin(2 * np.pi * rng.uniform(18, 30) * t2))
            y += _at(n, tr * env_bell(m, 0.12, 0.05), rng.uniform(0, 0.45), 0.4)
        y += filt(rng.standard_normal(n), lo=5000, hi=11000) * env_bell(n, 0.35, 0.2) * 0.05
        return dsp.reverb(dsp.stereo(fade(y, 0.003, 0.05), 0.5, 0.5), 1.5, 0.25, seed=48)[:, :n]
    if k == 'bat':
        n = secs(0.6)
        y = np.zeros(n)
        for _ in range(rng.integers(3, 6)):
            m = secs(0.04)
            y += _at(n, chirp(rng.uniform(4500, 6500), rng.uniform(2800, 3500), m) * env_bell(m, 0.02, 0.008),
                     rng.uniform(0, 0.5), rng.uniform(0.2, 0.4))
        tt = t_axis(n)
        fl = filt(rng.standard_normal(n), lo=300, hi=2000) * (0.5 + 0.5 * np.sin(2 * np.pi * rng.uniform(14, 20) * tt)) ** 3
        y += fl * env_bell(n, 0.3, 0.15) * 0.3
        return _sweep_pan(fade(y, 0.002, 0.04), -0.5, 0.5)
    if k in ('brute_grunt', 'alert'):
        n = secs(0.7 if k == 'brute_grunt' else 0.3)
        tt = t_axis(n)
        L = n / SR
        f0 = rng.uniform(75, 95) * (1.15 - 0.3 * tt / L) if k == 'brute_grunt' else rng.uniform(140, 180) * (1.2 - 0.4 * tt / L)
        y = _vocal(n, f0, 'uh' if k == 'brute_grunt' else 'a', rng, breath=0.7, rough=0.35, jitter=0.025)
        y *= np.clip(tt / 0.03, 0, 1) * np.clip((L - tt) / (0.25 * L), 0, 1)
        y = np.tanh(2.2 * y / (np.max(np.abs(y)) + 1e-9))
        return dsp.stereo(fade(y, 0.003, 0.04), 0.2, 0.3)
    if k == 'goblin':
        n = secs(0.62)
        y = np.zeros(n)
        f = rng.uniform(280, 350)
        for j in range(rng.integers(3, 5)):
            m = secs(0.09)
            t2 = t_axis(m)
            s = _vocal(m, f * (1.05 - 0.15 * t2 / 0.09) * (1 + 0.03 * j), 'e', rng, breath=0.6, jitter=0.02)
            y += _at(n, s * np.sin(np.pi * np.clip(t2 / 0.09, 0, 1)), 0.11 * j, 1.0)
        return dsp.stereo(fade(np.tanh(1.5 * y / (np.max(np.abs(y)) + 1e-9)), 0.002, 0.04), 0.3, 0.3)
    if k == 'boss_roar':
        y, _ = me._sfx_render('boss_roar', int(v) + 1)
        return dsp.reverb(np.asarray(y, dtype=np.float64), 2.2, 0.2, seed=49)
    raise KeyError(name)


def r_spot(name, rng, v, ctx, dur=None):
    k = name.split('.', 1)[1]
    if k == 'bird':
        kind = v % 4
        if kind == 0:                                     # warbler
            n = secs(rng.uniform(0.35, 0.6))
            tt = t_axis(n)
            f = rng.uniform(2600, 3800) * (1 + 0.12 * np.sin(2 * np.pi * rng.uniform(30, 60) * tt) + 0.05 * np.sin(2 * np.pi * 7 * tt))
            y = np.sin(2 * np.pi * np.cumsum(f) / SR) * env_bell(n, n / SR * 0.5, n / SR * 0.28)
        elif kind == 1:                                   # two-tone call
            n = secs(0.45)
            y = np.zeros(n)
            for j, f in enumerate((rng.uniform(3000, 3500), rng.uniform(2400, 2800))):
                m = secs(0.14)
                y += _at(n, np.sin(2 * np.pi * f * t_axis(m)) * env_bell(m, 0.07, 0.03), 0.18 * j)
        elif kind == 2:                                   # trill
            n = secs(0.55)
            tt = t_axis(n)
            rate = rng.uniform(16, 24)
            f = rng.uniform(3500, 4800) * (1 + 0.04 * np.sin(2 * np.pi * rate * tt))
            y = np.sin(2 * np.pi * np.cumsum(f) / SR) * (0.5 + 0.5 * np.sin(2 * np.pi * rate * tt)) ** 2 * env_bell(n, 0.27, 0.15)
        else:                                             # rising chirps
            n = secs(0.5)
            y = np.zeros(n)
            for j in range(3):
                m = secs(0.06)
                y += _at(n, chirp(rng.uniform(1900, 2300), rng.uniform(4000, 4800), m) * env_bell(m, 0.03, 0.012), 0.13 * j + rng.uniform(0, 0.02))
        return fade(dsp.reverb(dsp.stereo(y, 0.2, 0.3), 1.4, 0.18, seed=50), 0.001, 0.1)
    if k == 'owl':
        n = secs(1.9)
        y = np.zeros(n)
        f = rng.uniform(370, 420)
        for j, (t0, L) in enumerate(((0.0, 0.25), (0.35, 0.25), (0.75, 0.6))):
            m = secs(L + 0.1)
            t2 = t_axis(m)
            s = np.sin(2 * np.pi * f * (1 - 0.03 * t2 / L) * t2) + 0.15 * filt(rng.standard_normal(m), lo=300, hi=1500)
            y += _at(n, s * env_bell(m, L * 0.45, L * 0.3), t0, 0.8 if j < 2 else 1.0)
        return fade(dsp.reverb(dsp.stereo(y, 0.2, 0.3), 2.2, 0.3, seed=51)[:, :secs(2.4)], 0.005, 0.2)
    if k == 'frog':
        n = secs(0.45)
        tt = t_axis(n)
        rate = rng.uniform(22, 38)
        pulses = (0.5 + 0.5 * np.sin(2 * np.pi * rate * tt)) ** 6
        src = _glottal(n, rng.uniform(130, 200), rng, 0.02) * pulses
        y = _formants(src, [(rng.uniform(450, 900), 180, 0), (rng.uniform(1300, 1800), 300, -8)]) * env_bell(n, 0.2, 0.1)
        return fade(dsp.reverb(dsp.stereo(y, 0.2, 0.3), 1.2, 0.2, seed=52), 0.002, 0.05)
    if k == 'cricket':
        n = secs(1.0)
        tt = t_axis(n)
        f = rng.uniform(4200, 4900)
        y = np.zeros(n)
        for g in range(2):
            for p in range(rng.integers(3, 5)):
                m = secs(0.018)
                y += _at(n, np.sin(2 * np.pi * f * t_axis(m)) * env_bell(m, 0.009, 0.004), 0.1 + 0.45 * g + 0.028 * p, 1.0)
        return fade(dsp.stereo(y * 0.6, 0.2, 0.3), 0.001, 0.05)
    if k == 'drip':
        n = secs(0.15)
        y = _bubble(n, rng, rng.uniform(1200, 2600), rng.uniform(1.3, 1.8), rng.uniform(0.02, 0.05))
        y += _burst(n, rng, 2000, 9000, 0.0002, 0.003) * 0.15
        return fade(dsp.reverb(dsp.stereo(y, 0.2, 0.3), 2.6, 0.45, seed=53)[:, :secs(1.6)], 0.0005, 0.25)
    if k == 'twig':
        n = secs(0.25)
        y = _burst(n, rng, 1000, 8000, 0.0002, 0.003) * 0.9 + modal(n, [(rng.uniform(900, 1600), 0.02, 0.6)], rng)
        y += _at(n, _burst(secs(0.05), rng, 1500, 7000, 0.0002, 0.002) * 0.5, rng.uniform(0.02, 0.06))
        return fade(dsp.reverb(dsp.stereo(y, 0.2, 0.3), 1.0, 0.15, seed=54), 0.0003, 0.05)
    if k == 'rock':
        n = secs(1.3)
        y = filt(brown(n, rng), hi=180) * env_perc(n, 0.08, 0.4) * 0.6
        for _ in range(rng.integers(4, 9)):
            m = secs(0.1)
            f = rng.uniform(250, 1100)
            y += _at(n, modal(m, [(f, 0.025, 1.0), (f * 2.3, 0.015, 0.4)], rng), rng.uniform(0.05, 0.9), rng.uniform(0.1, 0.3))
        return fade(dsp.reverb(dsp.stereo(y, 0.3, 0.4), 2.4, 0.3, seed=55)[:, :n], 0.003, 0.15)
    if k == 'gull':
        n = secs(1.2)
        y = np.zeros(n)
        for j in range(2):
            m = secs(0.45)
            t2 = t_axis(m)
            f0 = rng.uniform(1600, 1900) - 600 * np.clip(t2 / 0.4, 0, 1) ** 1.5
            s = _glottal(m, f0 / 2, rng, 0.03) * (1 + 0.3 * np.sin(2 * np.pi * 40 * t2))
            s = _formants(s, [(1800, 400, 0), (3000, 600, -6)])
            y += _at(n, s * env_bell(m, 0.15, 0.1), 0.5 * j, 1.0)
        return fade(dsp.reverb(dsp.stereo(y, 0.3, 0.3), 1.6, 0.25, seed=56)[:, :n], 0.003, 0.1)
    if k == 'bell':
        n = secs(4.5)
        m = _tonic_midi(ctx, 3)
        f = me.mtof(m)
        tt = t_axis(n)
        y = np.zeros(n)
        for r, a, d in ((0.5, 0.6, 4.0), (1.0, 1.0, 3.0), (1.2, 0.5, 2.0), (1.5, 0.35, 1.6), (2.0, 0.6, 1.4), (2.5, 0.25, 1.0), (3.0, 0.2, 0.8)):
            y += a * np.sin(2 * np.pi * f * r * tt + rng.random()) * np.exp(-tt / d)
        y *= np.minimum(1, tt / 0.002)
        y += _burst(n, rng, 1000, 6000, 0.0003, 0.004) * 0.3
        return fade(dsp.reverb(dsp.stereo(y, 0.2, 0.3), 2.8, 0.3, seed=57)[:, :n], 0.002, 0.4)
    if k == 'hammer':
        n = secs(1.5)
        y = np.zeros(n)
        f = rng.uniform(1300, 2300)
        for j, t0 in enumerate((0.0, 0.42, 0.62)):
            m = secs(0.6)
            hit = modal(m, [(f, 0.4, 1.0), (f * 2.4, 0.2, 0.5), (f * 3.9, 0.1, 0.3)], rng, 0.002) + _thump(m, 200, 90, 0.008, 0.02) * 0.5
            y += _at(n, hit, t0 + rng.uniform(-0.01, 0.01), 1.0 if j == 0 else 0.7)
        return fade(dsp.reverb(dsp.stereo(y, 0.2, 0.3), 1.6, 0.25, seed=58)[:, :n], 0.0005, 0.15)
    if k == 'hooves':
        n = secs(1.25)
        y = np.zeros(n)
        f = rng.uniform(400, 700)
        for j, t0 in enumerate((0.0, 0.11, 0.3, 0.41, 0.6, 0.71, 0.9, 1.01)):
            m = secs(0.08)
            hit = modal(m, [(f * rng.uniform(0.95, 1.05), 0.02, 1.0), (f * 2.2, 0.012, 0.4)], rng) + _burst(m, rng, 1000, 6000, 0.0002, 0.003) * 0.5
            y += _at(n, hit, t0, 0.8 if j % 2 else 1.0)
        return fade(dsp.reverb(dsp.stereo(y, 0.2, 0.3), 1.3, 0.2, seed=59)[:, :n], 0.0005, 0.1)
    if k == 'chains':
        n = secs(1.0)
        y = np.zeros(n)
        for _ in range(rng.integers(10, 20)):
            m = secs(0.08)
            y += _at(n, modal(m, [(rng.uniform(1800, 4500), rng.uniform(0.02, 0.05), 1.0), (rng.uniform(4000, 7000), 0.01, 0.5)], rng),
                     rng.uniform(0, 0.7), rng.uniform(0.1, 0.35))
        return fade(dsp.reverb(dsp.stereo(y, 0.3, 0.4), 2.0, 0.3, seed=60)[:, :n], 0.0005, 0.1)
    if k == 'wind_gust':
        n = secs(2.6)
        y = _whoosh(n, rng, 250, 900, 1.1, 0.5, 1.2, tonal=0.15)
        return _sweep_pan(fade(y, 0.05, 0.3), rng.uniform(-0.8, 0), rng.uniform(0, 0.8))
    if k == 'fire_pop':
        n = secs(0.2)
        y = _burst(n, rng, 800, 7000, 0.0002, 0.004) * 1.0 + grains(n, 200, rng, 0.0003, 0.002, 1500, 6000, env_perc(n, 0.002, 0.05)) * 0.4
        return fade(dsp.stereo(y, 0.2, 0.3), 0.0003, 0.03)
    if k == 'ice_creak':
        n = secs(1.4)
        tt = t_axis(n)
        f = rng.uniform(70, 110) * (1 + 0.5 * tt / 1.4)
        cr = me._osc(me._table('pulse12', 80, 3000), f * (1 + 0.05 * filt(rng.standard_normal(n), hi=20)), n)
        cr = filt(cr, lo=120, hi=2500, peak=((400, 9, 4), (900, 6, 5))) * np.sin(np.pi * np.clip(tt / 1.4, 0, 1)) ** 1.5
        return fade(dsp.reverb(dsp.stereo(cr * 0.5, 0.3, 0.4), 2.5, 0.35, seed=61)[:, :n], 0.01, 0.2)
    if k == 'bubbles':
        n = secs(1.0)
        y = np.zeros(n)
        for _ in range(rng.integers(5, 12)):
            m = secs(0.08)
            y += _at(n, _bubble(m, rng, rng.uniform(300, 1200), rng.uniform(1.5, 2.2), rng.uniform(0.015, 0.04)),
                     rng.uniform(0, 0.8), rng.uniform(0.2, 0.6))
        return fade(dsp.reverb(dsp.stereo(filt(y, hi=3500), 0.3, 0.4), 1.5, 0.3, seed=62)[:, :n], 0.001, 0.1)
    if k == 'wave_crash':
        n = secs(3.6)
        x = rng.standard_normal((2, n))

        def g(tc, fr):
            sw = np.clip(tc / 1.2, 0, 1)
            amp = np.where(tc < 1.2, sw ** 2, np.exp(-(tc - 1.2) / 0.9))
            fc = np.where(tc < 1.2, 250 + 900 * sw, 2500 * np.exp(-(tc - 1.2) / 1.2) + 600)
            return bell(fr, fc[:, None], 1.6) * amp[:, None]
        y = np.vstack([stft_shape(x[c], g, nfft=2048, hop=512) for c in range(2)])
        return fade(y * 0.6, 0.05, 0.4)
    raise KeyError(name)


# =================================================================================================================
# seamless loops: weather + ambience beds (periodic by construction: circular filters, wrapped events)
# =================================================================================================================
def _drops(n, rng, rate, lo, hi, dmin, dmax, amp_sigma=0.8):
    """Rain drops scattered over a circular stereo buffer."""
    out = np.zeros((2, n))
    k = rng.poisson(rate * n / SR)
    m = secs(0.012)
    kt = t_axis(m)
    for _ in range(int(k)):
        d = rng.uniform(dmin, dmax)
        kern = rng.standard_normal(m) * np.exp(-kt / d)
        place(out, kern * math.exp(rng.normal(0, amp_sigma)) * 0.2, rng.uniform(0, n / SR), 1.0, rng.uniform(-1, 1), wrap=True)
    return cfilt(out, lo=lo, hi=hi)


def _bed_wind(n, rng, lo_fc, hi_fc, gust_cycles, width=1.2, whistle=0.0):
    L = n / SR
    out = np.zeros((2, n))
    gust = periodic_curve(n, rng, gust_cycles, floor=0.15)
    for c in range(2):
        x = pink(n, rng)
        gc = np.roll(gust, int(c * 0.35 * SR))

        def g(tc, fr, gc=gc):
            gi = gc[(np.asarray(tc) * SR).astype(np.int64) % n]
            fc = lo_fc + (hi_fc - lo_fc) * gi
            out_ = bell(fr, fc[:, None], width) * (0.3 + 0.7 * gi)[:, None]
            if whistle:
                out_ = out_ + whistle * bell(fr, (fc * 1.8)[:, None], 0.06) * (gi ** 3)[:, None]
            return out_
        out[c] = stft_shape(x, g, nfft=2048, hop=512, circular=True)
    return out


def _cricket_loop(n, rng, voices=5):
    out = np.zeros((2, n))
    L = n / SR
    for _ in range(voices):
        f = rng.uniform(3800, 5200)
        per = rng.uniform(0.45, 0.9)
        cnt = max(1, int(round(L / per)))
        per = L / cnt
        p = rng.uniform(-0.9, 0.9)
        a = rng.uniform(0.3, 1.0)
        pulses = int(rng.integers(3, 5))
        m = secs(0.02)
        chirp_ = np.sin(2 * np.pi * f * t_axis(m)) * env_bell(m, 0.01, 0.004)
        off = rng.uniform(0, per)
        for i in range(cnt):
            for q in range(pulses):
                place(out, chirp_ * a, off + i * per + q * 0.026, 1.0, p, wrap=True)
    return out


def _frog_loop(n, rng, voices=4):
    out = np.zeros((2, n))
    L = n / SR
    for _ in range(voices):
        per = rng.uniform(1.6, 3.5)
        cnt = max(1, int(round(L / per)))
        per = L / cnt
        croak = r_spot('spot.frog', rng, int(rng.integers(0, 4)), {})
        croak = croak[:, :secs(0.6)] * rng.uniform(0.2, 0.6)
        p = rng.uniform(-0.8, 0.8)
        off = rng.uniform(0, per)
        for i in range(cnt):
            if rng.random() < 0.75:
                place(out, croak.mean(axis=0), off + i * per + rng.uniform(-0.1, 0.1), 1.0, p, wrap=True)
    return out


def _babble(n, rng, voices=12):
    """Distant crowd murmur: many formant 'voices' with syllable-rate gating, blurred and lowpassed."""
    out = np.zeros((2, n))
    L = n / SR
    vow = list(VOWELS)
    for _ in range(voices):
        f0 = rng.uniform(95, 230) * (1 + 0.08 * (periodic_curve(n, rng, (1, 2, 3)) - 0.5))
        src = _glottal(n, f0, rng, 0.02) + 0.4 * rng.standard_normal(n)
        syl = rng.uniform(3.0, 5.5)
        cyc = max(1, int(round(syl * L)))
        tt = np.arange(n) / float(n)
        gate = (0.5 + 0.5 * np.sin(2 * np.pi * cyc * tt + rng.random() * 6.28)) ** 2
        talk = periodic_curve(n, rng, (1, 2), floor=0.0) > 0.35
        seq = [vow[int(rng.integers(0, len(vow)))] for _ in range(4)]
        seg = n // 4
        y = np.zeros(n)
        for j, vname in enumerate(seq):
            part = src[j * seg:(j + 1) * seg if j < 3 else n]
            fm = [(F, B, -3 * kk) for kk, (F, B) in enumerate(VOWELS[vname])]
            y[j * seg:(j + 1) * seg if j < 3 else n] = _formants(part, fm)
        y = y * gate * talk
        out += pan(y, rng.uniform(-0.9, 0.9)) * rng.uniform(0.4, 1.0)
    out = cfilt(out, lo=150, hi=2600)
    return dsp.creverb(out, 1.6, 0.5, seed=63)


def r_loop(name, rng, v, ctx, dur=None):
    k = name.split('.', 1)[1]
    n = secs(dur or LOOP_DUR.get(name, 16.0))
    if k == 'rain_light':
        y = _drops(n, rng, 130, 900, 9000, 0.0008, 0.003)
        y += cfilt(np.vstack([pink(n, rng), pink(n, rng)]), lo=500, hi=9000) * 0.035
        bub = np.zeros((2, n))
        for _ in range(int(rng.poisson(12 * n / SR))):
            m = secs(0.04)
            place(bub, _bubble(m, rng, rng.uniform(1500, 4000), 1.4, 0.01), rng.uniform(0, n / SR), 0.05, rng.uniform(-1, 1), wrap=True)
        return y + bub
    if k == 'rain_heavy':
        y = _drops(n, rng, 650, 600, 10000, 0.0006, 0.004)
        y += cfilt(np.vstack([pink(n, rng), pink(n, rng)]), lo=250, hi=10000) * 0.08
        y += cfilt(np.vstack([brown(n, rng), brown(n, rng)]), hi=300) * 0.05
        return y
    if k == 'wind':
        return _bed_wind(n, rng, 220, 900, (1, 2, 3, 5), 1.2, whistle=0.08)
    if k == 'snow_wind':
        return _bed_wind(n, rng, 800, 2600, (1, 2, 4, 6), 0.9, whistle=0.04) * 0.7
    if k in ('embers', 'campfire'):
        out = np.zeros((2, n))
        for _ in range(int(rng.poisson(22 * n / SR))):
            m = secs(0.02)
            kern = rng.standard_normal(m) * np.exp(-t_axis(m) / rng.uniform(0.0006, 0.003))
            place(out, kern * math.exp(rng.normal(0, 0.9)) * 0.3, rng.uniform(0, n / SR), 1.0, rng.uniform(-0.6, 0.6), wrap=True)
        out = cfilt(out, lo=900, hi=7000)
        roar = cfilt(np.vstack([brown(n, rng), brown(n, rng)]), hi=350) * 0.06 * (0.7 + 0.3 * periodic_curve(n, rng, (2, 3, 5)))
        hiss = cfilt(np.vstack([pink(n, rng), pink(n, rng)]), lo=3000, hi=9000) * 0.01
        return out + roar + hiss
    if k == 'forest_day':
        leaves = _bed_wind(n, rng, 600, 2400, (1, 2, 3), 1.4) * 0.5
        hum = cfilt(np.vstack([pink(n, rng), pink(n, rng)]), lo=5500, hi=9000) * 0.006
        return leaves + hum
    if k == 'forest_night':
        cr = _cricket_loop(n, rng, 6) * 0.25
        wind = _bed_wind(n, rng, 300, 1200, (1, 2), 1.3) * 0.25
        frogs = _frog_loop(n, rng, 2) * 0.3
        return dsp.creverb(cr + frogs, 1.4, 0.25, seed=64) + wind
    if k == 'cave':
        tone = cfilt(np.vstack([brown(n, rng), brown(n, rng)]), hi=130) * 0.12
        air = cfilt(np.vstack([pink(n, rng), pink(n, rng)]), lo=200, hi=2000) * 0.012 * (0.6 + 0.4 * periodic_curve(n, rng, (1, 2)))
        L = n / SR
        f = round(46 * L) / L
        tt = np.arange(n) / SR
        drone = np.sin(2 * np.pi * f * tt) * 0.02 * (0.6 + 0.4 * periodic_curve(n, rng, (1, 3)))
        return dsp.creverb(tone + air + drone, 3.0, 0.35, seed=65)
    if k == 'town':
        murmur = _babble(n, rng, 12) * 0.6
        out = np.zeros((2, n))
        for _ in range(int(rng.poisson(0.6 * n / SR))):
            m = secs(0.1)
            f = rng.uniform(250, 900)
            place(out, modal(m, [(f, 0.03, 1.0), (f * 2.3, 0.02, 0.4)], rng) * 0.08, rng.uniform(0, n / SR), 1.0,
                  rng.uniform(-0.8, 0.8), wrap=True)
        return murmur + dsp.creverb(out, 1.5, 0.4, seed=66) + _bed_wind(n, rng, 300, 900, (1, 2), 1.3) * 0.08
    if k == 'plains':
        return _bed_wind(n, rng, 200, 700, (1, 2, 3), 1.3, whistle=0.03) * 0.8
    if k == 'mountain':
        return _bed_wind(n, rng, 400, 1800, (1, 2, 3, 5), 1.0, whistle=0.12)
    if k == 'sea':
        L = n / SR
        waves = max(1, int(round(L / 8.0)))
        out = np.zeros((2, n))
        for c in range(2):
            x = pink(n, rng)
            ph = rng.uniform(0, 0.2)

            def g(tc, fr, ph=ph):
                u = ((np.asarray(tc) / L) * waves + ph) % 1.0
                swell = np.where(u < 0.55, (u / 0.55) ** 2, np.exp(-(u - 0.55) / 0.18))
                fc = np.where(u < 0.55, 250 + 1100 * (u / 0.55), 600 + 2800 * np.exp(-(u - 0.55) / 0.2))
                return bell(fr, fc[:, None], 1.5) * (0.15 + 0.85 * swell)[:, None]
            out[c] = stft_shape(x, g, nfft=2048, hop=512, circular=True)
        rumble = cfilt(np.vstack([brown(n, rng), brown(n, rng)]), hi=200) * 0.15
        return out + rumble
    if k == 'swamp':
        frogs = _frog_loop(n, rng, 5) * 0.5
        L = n / SR
        tt = np.arange(n) / SR
        f = round(220 * L) / L
        buzz = me._osc(me._table('saw', 220, 3000), f, n) * 0.008 * (0.5 + 0.5 * periodic_curve(n, rng, (2, 3, 5)))
        buzz = np.vstack([buzz, np.roll(buzz, secs(0.013))])
        return dsp.creverb(frogs, 1.5, 0.25, seed=67) + buzz + _bed_wind(n, rng, 250, 800, (1, 2), 1.3) * 0.12
    if k == 'dungeon':
        L = n / SR
        tt = np.arange(n) / SR
        f1, f2 = round(41 * L) / L, round(41.7 * L) / L
        drone = (np.sin(2 * np.pi * f1 * tt) + np.sin(2 * np.pi * f2 * tt)) * 0.03
        air = cfilt(np.vstack([pink(n, rng), pink(n, rng)]), lo=150, hi=1500) * 0.01
        out = np.vstack([drone, drone]) + air
        for _ in range(max(1, int(L / 5))):
            place(out, r_spot('spot.drip', rng, 0, {})[:, :secs(1.4)] * 0.25, rng.uniform(0, L), 1.0, wrap=True)
        return dsp.creverb(out, 3.2, 0.3, seed=68)
    if k == 'underwater':
        rum = cfilt(np.vstack([brown(n, rng), brown(n, rng)]), hi=350) * 0.12 * (0.7 + 0.3 * periodic_curve(n, rng, (1, 2, 3)))
        out = np.zeros((2, n))
        for _ in range(int(rng.poisson(1.2 * n / SR))):
            m = secs(0.08)
            place(out, _bubble(m, rng, rng.uniform(250, 900), rng.uniform(1.5, 2.2), rng.uniform(0.02, 0.05)) * 0.15,
                  rng.uniform(0, n / SR), 1.0, rng.uniform(-0.8, 0.8), wrap=True)
        return dsp.creverb(cfilt(rum + out, hi=1800), 2.5, 0.35, seed=69)
    if k == 'aurora':
        wind = _bed_wind(n, rng, 900, 3000, (1, 2, 3), 0.8, whistle=0.05) * 0.5
        out = np.zeros((2, n))
        base = _tonic_midi(ctx, 6)
        L = n / SR
        for _ in range(max(2, int(L / 3))):
            m = secs(2.5)
            iv = (0, 2, 4, 7, 9, 12)[int(rng.integers(0, 6))]
            place(out, _bellnote(base + iv, m, rng, dec=1.2, ratio=2.0, idx=0.4) * 0.04, rng.uniform(0, L), 1.0,
                  rng.uniform(-0.8, 0.8), wrap=True)
        return wind + dsp.creverb(out, 3.5, 0.6, seed=70)
    raise KeyError(name)


def r_thunder(name, rng, v, ctx, dur=None):
    n = secs(7.0)
    tt = t_axis(n)
    close = v % 2 == 0
    out = np.zeros((2, n))
    for c in range(2):
        rum = filt(brown(n, rng), hi=rng.uniform(140, 260))
        env = np.zeros(n)
        for _ in range(rng.integers(3, 7)):
            t0 = rng.uniform(0.1 if close else 0.4, 3.0)
            env += np.exp(-0.5 * ((tt - t0) / rng.uniform(0.2, 0.7)) ** 2) * rng.uniform(0.4, 1.0)
        env = np.maximum(env, 0) * np.exp(-tt / 3.5) + np.exp(-tt / 1.8) * 0.3 * (tt > 0.05)
        out[c] = rum * env
    if close:
        crack = _burst(n, rng, 800, 8000, 0.001, 0.05) * 1.2 + chirp(90, 35, n, 0.2) * env_perc(n, 0.003, 0.35)
        out += np.vstack([crack, crack * 0.9])
    out = np.tanh(1.2 * out / (np.max(np.abs(out)) + 1e-9))
    return fade(dsp.reverb(out, 3.0, 0.3, seed=71)[:, :n], 0.01, 1.0)


# =================================================================================================================
# catalog
# =================================================================================================================
LOOP_DUR = {'weather.rain_light': 12.0, 'weather.rain_heavy': 12.0, 'weather.wind': 16.0, 'weather.snow_wind': 16.0,
            'weather.embers': 10.0, 'amb.forest_day': 20.0, 'amb.forest_night': 20.0, 'amb.cave': 16.0,
            'amb.town': 20.0, 'amb.plains': 16.0, 'amb.mountain': 16.0, 'amb.sea': 24.0, 'amb.swamp': 20.0,
            'amb.dungeon': 20.0, 'amb.underwater': 16.0, 'amb.aurora': 20.0, 'amb.campfire': 10.0,
            'world.portal_loop': 6.0}


def _e(fn, cat, target, variants=4, bus='sfx', spatial=True, caption=None, pitch=(-80, 80), gain=(-1.5, 1.5),
       cooldown=0.03, voices=6, loop=False, priority=5, lowpass=None):
    return dict(fn=fn, cat=cat, target=target, variants=variants, bus=bus, spatial=spatial, caption=caption,
                pitch=pitch, gain=gain, cooldown=cooldown, voices=voices, loop=loop, priority=priority, lowpass=lowpass)


CATALOG = {}
for _s in SURFACES:
    CATALOG['foot.' + _s] = _e(r_foot, 'foot', -26, 6, pitch=(-120, 120), gain=(-2.5, 1.5), cooldown=0.06, voices=4,
                               priority=3)
CATALOG.update({
    'move.jump': _e(r_move, 'move', -27, 3, cooldown=0.1, voices=2),
    'move.land': _e(r_move, 'move', -23, 3, cooldown=0.1, voices=2),
    'move.dash': _e(r_move, 'move', -22, 3, cooldown=0.1, voices=2),
    'combat.swing': _e(r_combat, 'combat', -21, 5, pitch=(-150, 150), cooldown=0.05),
    'combat.swing_heavy': _e(r_combat, 'combat', -19, 4, pitch=(-120, 120), cooldown=0.08),
    'combat.hit_flesh': _e(r_combat, 'combat', -15, 5, cooldown=0.04, priority=7),
    'combat.clang': _e(r_combat, 'combat', -16, 4, pitch=(-60, 60), cooldown=0.05, priority=7),
    'combat.block': _e(r_combat, 'combat', -16, 4, cooldown=0.05, priority=7),
    'combat.blunt': _e(r_combat, 'combat', -15, 4, cooldown=0.05, priority=7),
    'combat.bow_draw': _e(r_combat, 'combat', -25, 2, cooldown=0.2, voices=2),
    'combat.bow_release': _e(r_combat, 'combat', -19, 3, cooldown=0.08),
    'combat.arrow_hit': _e(r_combat, 'combat', -18, 3, cooldown=0.05),
    'combat.crit': _e(r_combat, 'combat', -13, 3, pitch=(-60, 60), cooldown=0.1, priority=8),
    'combat.body_fall': _e(r_combat, 'combat', -20, 3, cooldown=0.1),
    'combat.parry': _e(r_combat, 'combat', -16, 3, cooldown=0.08, priority=7),
    'combat.player_hurt': _e(r_combat, 'combat', -17, 4, spatial=False, cooldown=0.15, voices=2, priority=9),
    'combat.enemy_die': _e(r_combat, 'combat', -18, 3, cooldown=0.08, priority=6),
    'magic.fire_cast': _e(r_magic, 'magic', -17, 3, cooldown=0.1),
    'magic.fire_hit': _e(r_magic, 'magic', -15, 3, cooldown=0.1, caption='[Fire bursts]'),
    'magic.ice_cast': _e(r_magic, 'magic', -18, 3, cooldown=0.1),
    'magic.ice_hit': _e(r_magic, 'magic', -16, 3, cooldown=0.1),
    'magic.lightning': _e(r_magic, 'magic', -14, 3, cooldown=0.15, caption='[Lightning crackles]'),
    'magic.heal': _e(r_magic, 'magic', -19, 2, pitch=(0, 0), spatial=False, cooldown=0.3, caption='[Healing chime]'),
    'magic.arcane': _e(r_magic, 'magic', -18, 4, cooldown=0.08),
    'magic.earth': _e(r_magic, 'magic', -15, 3, cooldown=0.15, caption='[The ground rumbles]'),
    'magic.wind': _e(r_magic, 'magic', -19, 3, cooldown=0.1),
    'magic.teleport': _e(r_magic, 'magic', -18, 2, cooldown=0.3),
    'ui.click': _e(r_ui, 'ui', -27, 3, bus='ui', spatial=False, pitch=(-30, 30), gain=(-1, 1), cooldown=0.02, voices=3),
    'ui.hover': _e(r_ui, 'ui', -33, 3, bus='ui', spatial=False, pitch=(-30, 30), gain=(-1, 1), cooldown=0.03, voices=2),
    'ui.confirm': _e(r_ui, 'ui', -24, 1, bus='ui', spatial=False, pitch=(0, 0), gain=(0, 0), cooldown=0.05, voices=2),
    'ui.back': _e(r_ui, 'ui', -25, 1, bus='ui', spatial=False, pitch=(0, 0), gain=(0, 0), cooldown=0.05, voices=2),
    'ui.open': _e(r_ui, 'ui', -26, 2, bus='ui', spatial=False, pitch=(0, 0), cooldown=0.1, voices=2),
    'ui.close': _e(r_ui, 'ui', -27, 2, bus='ui', spatial=False, pitch=(0, 0), cooldown=0.1, voices=2),
    'ui.error': _e(r_ui, 'ui', -25, 1, bus='ui', spatial=False, pitch=(0, 0), gain=(0, 0), cooldown=0.15, voices=1),
    'ui.equip': _e(r_ui, 'ui', -24, 3, bus='ui', spatial=False, cooldown=0.08, voices=2),
    'ui.coin': _e(r_ui, 'ui', -22, 3, bus='ui', spatial=False, pitch=(-40, 40), cooldown=0.03, voices=4),
    'ui.pickup': _e(r_ui, 'ui', -22, 3, bus='ui', spatial=False, pitch=(-40, 40), cooldown=0.05, voices=3),
    'ui.level_up': _e(r_ui, 'ui', -18, 1, bus='ui', spatial=False, pitch=(0, 0), gain=(0, 0), cooldown=1.0, voices=1,
                      caption='[Level up fanfare]', priority=9),
    'ui.quest_accept': _e(r_ui, 'ui', -21, 1, bus='ui', spatial=False, pitch=(0, 0), gain=(0, 0), cooldown=0.5,
                          voices=1, caption='[New quest]', priority=8),
    'ui.quest_complete': _e(r_ui, 'ui', -20, 1, bus='ui', spatial=False, pitch=(0, 0), gain=(0, 0), cooldown=0.5,
                            voices=1, caption='[Quest complete]', priority=9),
    'ui.notify': _e(r_ui, 'ui', -25, 1, bus='ui', spatial=False, pitch=(0, 0), cooldown=0.2, voices=2),
    'ui.talk_blip': _e(r_ui, 'ui', -29, 6, bus='ui', spatial=False, pitch=(-20, 20), cooldown=0.03, voices=2),
    'ui.page': _e(r_ui, 'ui', -28, 3, bus='ui', spatial=False, cooldown=0.1, voices=2),
    'ui.slider': _e(r_ui, 'ui', -30, 2, bus='ui', spatial=False, pitch=(-50, 50), cooldown=0.02, voices=2),
    'world.door': _e(r_world, 'world', -20, 3, cooldown=0.3, caption='[Door creaks]'),
    'world.chest': _e(r_world, 'world', -20, 2, cooldown=0.3, caption='[Chest opens]'),
    'world.explosion': _e(r_world, 'world', -12, 3, pitch=(-100, 60), cooldown=0.1, caption='[Explosion]', priority=9),
    'world.portal_loop': _e(r_world, 'world', -26, 1, loop=True, caption='[Portal hums]', pitch=(0, 0), gain=(0, 0)),
    'creature.wolf_growl': _e(r_creature, 'creature', -18, 3, cooldown=0.6, caption='[Wolf growls]', priority=7),
    'creature.wolf_howl': _e(r_creature, 'creature', -20, 2, cooldown=4.0, caption='[Distant howl]'),
    'creature.slime': _e(r_creature, 'creature', -20, 3, cooldown=0.3),
    'creature.wisp': _e(r_creature, 'creature', -21, 3, cooldown=0.6, caption='[Wisp chitters]'),
    'creature.bat': _e(r_creature, 'creature', -22, 3, cooldown=0.6),
    'creature.brute_grunt': _e(r_creature, 'creature', -18, 3, cooldown=0.6, caption='[Brute grunts]'),
    'creature.alert': _e(r_creature, 'creature', -17, 3, cooldown=1.0, caption='[Enemy alerted]', priority=8),
    'creature.goblin': _e(r_creature, 'creature', -19, 3, cooldown=0.8, caption='[Goblin cackles]'),
    'creature.boss_roar': _e(r_creature, 'creature', -12, 2, pitch=(-80, 40), cooldown=3.0, caption='[Monstrous roar]',
                             priority=10),
    'spot.bird': _e(r_spot, 'spot', -27, 8, bus='amb', pitch=(-150, 150), gain=(-4, 1), cooldown=0.5),
    'spot.owl': _e(r_spot, 'spot', -27, 2, bus='amb', cooldown=6.0, caption='[Owl hoots]'),
    'spot.frog': _e(r_spot, 'spot', -27, 4, bus='amb', pitch=(-200, 200), cooldown=0.4),
    'spot.cricket': _e(r_spot, 'spot', -31, 3, bus='amb', cooldown=0.4),
    'spot.drip': _e(r_spot, 'spot', -28, 4, bus='amb', pitch=(-200, 200), cooldown=0.3),
    'spot.twig': _e(r_spot, 'spot', -26, 3, bus='amb', cooldown=2.0),
    'spot.rock': _e(r_spot, 'spot', -25, 3, bus='amb', cooldown=5.0, caption='[Rocks shift]'),
    'spot.gull': _e(r_spot, 'spot', -25, 3, bus='amb', cooldown=3.0),
    'spot.bell': _e(r_spot, 'spot', -21, 1, bus='amb', pitch=(0, 0), cooldown=30.0, caption='[Bell tolls]'),
    'spot.hammer': _e(r_spot, 'spot', -25, 2, bus='amb', cooldown=4.0, caption='[Hammer on anvil]'),
    'spot.hooves': _e(r_spot, 'spot', -26, 2, bus='amb', cooldown=8.0),
    'spot.chains': _e(r_spot, 'spot', -26, 3, bus='amb', cooldown=6.0, caption='[Chains rattle]'),
    'spot.wind_gust': _e(r_spot, 'spot', -25, 3, bus='amb', cooldown=4.0),
    'spot.fire_pop': _e(r_spot, 'spot', -27, 4, bus='amb', cooldown=0.2),
    'spot.ice_creak': _e(r_spot, 'spot', -26, 2, bus='amb', cooldown=8.0, caption='[Ice creaks]'),
    'spot.bubbles': _e(r_spot, 'spot', -27, 3, bus='amb', cooldown=1.0),
    'spot.wave_crash': _e(r_spot, 'spot', -23, 2, bus='amb', cooldown=6.0),
    'weather.thunder': _e(r_thunder, 'weather', -14, 4, bus='amb', pitch=(-150, 100), gain=(-4, 0), cooldown=6.0,
                          caption='[Thunder rumbles]', priority=8),
})
for _k, _d in LOOP_DUR.items():
    if _k.startswith(('weather.', 'amb.')):
        tgt = {'weather.rain_light': -27, 'weather.rain_heavy': -22, 'weather.wind': -26, 'weather.snow_wind': -28,
               'weather.embers': -28, 'amb.cave': -32, 'amb.dungeon': -31, 'amb.sea': -25, 'amb.town': -29}.get(_k, -28)
        CATALOG[_k] = _e(r_loop, _k.split('.')[0], tgt, 1, bus='amb', spatial=False, loop=True, pitch=(0, 0),
                         gain=(0, 0), caption={'weather.rain_heavy': '[Heavy rain]', 'weather.rain_light': '[Rain]'}.get(_k))

# The studio engine's built-in sound keys -> library sounds (the cartridge adapter routes them).
LEGACY_MAP = {'jump': 'move.jump', 'coin': 'ui.coin', 'hit': 'combat.hit_flesh', 'laser': 'combat.swing',
              'boom': 'combat.enemy_die', 'power': 'ui.quest_complete', 'quest': 'ui.quest_accept',
              'levelup': 'ui.level_up', 'door': 'world.door', 'portal': 'magic.teleport', 'talk': 'ui.talk_blip',
              'magic': 'magic.arcane'}

# Ambience per world mood (bed, extra loop layers, spatial spot sounds with average interval seconds)
MOOD_AMBIENCE = {
    'Bioluminescent Twilight': {'bed': 'amb.forest_night', 'spots': [('spot.frog', 7), ('spot.owl', 45), ('spot.cricket', 9), ('spot.twig', 40)]},
    'Alpine Sunset': {'bed': 'amb.mountain', 'spots': [('spot.bird', 6), ('spot.wind_gust', 25), ('spot.bell', 150)]},
    'Sub-Zero Aurora': {'bed': 'amb.aurora', 'layers': ['weather.snow_wind'], 'spots': [('spot.ice_creak', 35), ('spot.wind_gust', 20)]},
    'Crimson Citadel': {'bed': 'amb.campfire', 'layers': ['amb.plains'], 'spots': [('spot.fire_pop', 3), ('spot.rock', 40), ('spot.chains', 50)]},
    'Sunken Ruins': {'bed': 'amb.underwater', 'spots': [('spot.drip', 5), ('spot.bubbles', 9)]},
}
WEATHER_AMBIENCE = {'rain': ['weather.rain_light'], 'storm': ['weather.rain_heavy', 'weather.wind'],
                    'snow': ['weather.snow_wind'], 'embers': ['weather.embers'], 'pollen': ['amb.forest_day'],
                    'bubbles': ['amb.underwater'], 'fireflies': ['amb.forest_night']}
MOOD_SURFACES = {'Bioluminescent Twilight': ['grass', 'leaves', 'water', 'wood'],
                 'Alpine Sunset': ['grass', 'dirt', 'stone', 'wood', 'snow'],
                 'Sub-Zero Aurora': ['snow', 'stone', 'wood'],
                 'Crimson Citadel': ['stone', 'gravel', 'metal', 'dirt'],
                 'Sunken Ruins': ['sand', 'stone', 'water', 'mud']}
ENEMY_SOUNDS = {'grunt': ['creature.goblin', 'creature.alert'], 'archer': ['combat.bow_draw', 'combat.bow_release', 'combat.arrow_hit'],
                'brute': ['creature.brute_grunt', 'combat.blunt'], 'wisp': ['creature.wisp', 'magic.arcane'],
                'boss': ['creature.boss_roar', 'world.explosion']}
CORE = ['move.jump', 'move.land', 'move.dash', 'combat.swing', 'combat.swing_heavy', 'combat.hit_flesh', 'combat.clang',
        'combat.block', 'combat.crit', 'combat.body_fall', 'combat.player_hurt', 'combat.enemy_die', 'magic.arcane',
        'magic.heal', 'ui.click', 'ui.hover', 'ui.confirm', 'ui.back', 'ui.open', 'ui.close', 'ui.error', 'ui.equip',
        'ui.coin', 'ui.pickup', 'ui.level_up', 'ui.quest_accept', 'ui.quest_complete', 'ui.notify', 'ui.talk_blip',
        'ui.page', 'world.door', 'world.chest', 'magic.teleport', 'world.portal_loop']


def list_sounds(cat=None):
    return sorted(k for k, e in CATALOG.items() if cat is None or e['cat'] == cat)


def sounds_for_world(world):
    """The sounds a world needs: core + its moods' surfaces/beds/spots + weather + enemy kinds present."""
    names = set(CORE)
    levels = [world] + list(world.get('levels') or [])
    for L in levels:
        mood = L.get('mood') or world.get('mood')
        for s in MOOD_SURFACES.get(mood, ['grass', 'stone']):
            names.add('foot.' + s)
        amb = MOOD_AMBIENCE.get(mood)
        if amb:
            names.add(amb['bed'])
            names.update(amb.get('layers', []))
            names.update(s for s, _ in amb['spots'])
        w = L.get('weather')
        if w in WEATHER_AMBIENCE:
            names.update(WEATHER_AMBIENCE[w])
        if w == 'storm':
            names.add('weather.thunder')
        objs = L.get('objects') or []
        for o in objs:
            if o.get('t') == 'enemy':
                names.update(ENEMY_SOUNDS.get(o.get('kind') or 'grunt', []))
            if o.get('t') == 'house':
                names.add('amb.town')
                names.update(['spot.hammer', 'spot.bell'])
        if L.get('water') is not None:
            names.add('foot.water')
        tiles = ''.join(L.get('tiles') or [])
        if 'B' in tiles:
            names.update(ENEMY_SOUNDS['boss'])
        if 'a' in tiles:
            names.update(ENEMY_SOUNDS['archer'])
        if 'e' in tiles:
            names.update(ENEMY_SOUNDS['grunt'])
    names.update(['creature.alert', 'creature.wolf_growl'])
    return sorted(n for n in names if n in CATALOG)


def render(name, variant=0, ctx=None, dur=None, normalise=True):
    """One variant of one sound -> float64 array at 48 kHz, levelled to its category target."""
    e = CATALOG[name]
    rng = _rng(name, variant, int((ctx or {}).get('seed', 0)))
    y = e['fn'](name, rng, int(variant), ctx or {}, dur)
    y = np.asarray(y, dtype=np.float64)
    if not e['loop']:
        y = dsp.trim_tail(y, -62.0)
        y = fade(y, 0.0, 0.004)
        y = filt(y, lo=32, order=4)
        y[..., :16] *= np.linspace(0, 1, 16)
    else:
        y = cfilt(y, lo=32, order=4)
        y = y - y.mean(axis=-1, keepdims=True)
    if normalise:
        if e['loop']:
            y = dsp.norm_integrated(y, e['target'], -3.0)
        else:
            y = dsp.norm_active(y, e['target'], -1.0)
    return y


def meta(name):
    """Runtime metadata for the manifest (no function objects)."""
    e = CATALOG[name]
    return {k: e[k] for k in ('cat', 'bus', 'spatial', 'caption', 'pitch', 'gain', 'cooldown', 'voices', 'loop',
                              'priority', 'target')}


def render_set(names, out_dir, variants=None, ctx=None, write=True, dur=None):
    """Render many sounds -> manifest {name: meta + files}. variants: int (cap) or None (catalog default)."""
    os.makedirs(out_dir, exist_ok=True)
    man = {'version': VERSION, 'sr': SR, 'ctx': {k: v for k, v in (ctx or {}).items() if k in ('tonic', 'seed')},
           'sounds': {}}
    for name in names:
        e = CATALOG[name]
        nv = e['variants'] if variants is None else max(1, min(int(variants), e['variants']))
        files = []
        for v in range(nv):
            y = render(name, v, ctx, dur=dur if e['loop'] else None)
            fn = '%s_%d.wav' % (name.replace('.', '_'), v)
            if write:
                dsp.write_wav(os.path.join(out_dir, fn), y, loop=(0, y.shape[-1]) if e['loop'] else None, title=name)
            files.append({'file': fn, 'dur': round(y.shape[-1] / SR, 4), 'ch': 1 if y.ndim == 1 else 2})
        m = meta(name)
        m['files'] = files
        man['sounds'][name] = m
    if write:
        dsp.save_json(os.path.join(out_dir, 'sfx_manifest.json'), man)
    return man


def _main(argv=None):
    import argparse
    import time
    from . import analyze
    ap = argparse.ArgumentParser(description='Render the Stark World Engine procedural SFX library to WAV + a listen-check report')
    ap.add_argument('out', help='output folder')
    ap.add_argument('--cat', default=None)
    ap.add_argument('--variants', type=int, default=2)
    ap.add_argument('--tonic', type=int, default=2)
    a = ap.parse_args(argv)
    names = list_sounds(a.cat)
    t0 = time.time()
    man = render_set(names, a.out, a.variants, {'tonic': a.tonic})
    rows = []
    for name, m in man['sounds'].items():
        y, _ = dsp.read_wav(os.path.join(a.out, m['files'][0]['file']))
        r = analyze.check(y, name, loop=(0, y.shape[-1]) if m['loop'] else None)
        rows.append((name, r))
        print('%-24s %s %s' % (name, 'OK ' if r['ok'] else 'BAD', r['description']))
    print('%d sounds in %.1fs' % (len(names), time.time() - t0))
    return 0 if all(r['ok'] for _, r in rows) else 1


if __name__ == '__main__':
    import sys
    sys.exit(_main(sys.argv[1:]))
