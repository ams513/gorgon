"""music_engine.py - Procedural ORIGINAL music + SFX generator for Stark World Engine.

Everything you hear is synthesized from math at render time: no samples, no
loops, no sound fonts, no third-party audio. Every render is therefore 100%
original and royalty-free for the buyer (the seed makes it reproducible).

100% local: Python stdlib + numpy (when present; a slower pure-Python draft
renderer is used otherwise) + ffmpeg on PATH (loudness measurement only;
optional). No network, no cloud, no model.

Public API
----------
compose(style, mood=None, bpm=None, seconds=60, seed=0, key=None, *,
        stems=False, out=None, loop_file=False, target_lufs=-14.0) -> wav path
    Full track: intro -> seamless loop -> outro (+ ring-out). The WAV carries
    a RIFF 'smpl' loop chunk and a JSON sidecar (<name>.json) with the loop
    points (samples + seconds), key, progressions, sections, loudness, stems.
    style: 'adventure' 'ambient' 'chiptune' 'synthwave' 'battle' 'cozy', or a
    world mood name (see WORLD_MOODS) to use that world's defaults.
compose_track(...) -> dict    same as compose() but returns the full info dict
compose_world(world, seconds=60, seed=0, **kw) -> wav path
sfx(kind, seed=0, out=None) -> wav path   (kinds: SFX_KINDS)
background_bed(duration, bpm, style='synthwave', seed=0, mood=None,
               out=None, target_lufs=-14.0) -> wav path
    Beat-locked bed for the video editor: a steady quarter-note pulse at
    exactly `bpm` (beat_engine.analyze() reads that tempo back), lead tucked
    under, same intro/loop/outro structure, exact `duration`.
list_styles(), list_moods(), list_sfx(), WORLD_MOODS

Adaptive score (v1.1, used by the audio module for cartridges)
compose_cue(state, preset, key, mode, bpm, bars, seed, motif, region, out_dir) -> info dict
    One mood cue as layered seamless loop stems ('bed', 'perc', 'lead'; victory is a one-shot 'mix').
    state: explore tension combat victory town night. preset: orchestral synthwave folk ambient chiptune.
    Harmony: functional phrase templates per state and mode (cadences, secondary dominants, modal mixture),
    voiced by a dynamic-programming solver that scores the part-writing rules (no parallel fifths/octaves,
    leading tones and sevenths resolve, no crossing, spacing, common tones kept, contrary outer voices).
    Melody: the region's leitmotif (region_motif) transformed per state (augmented, diminished, inverted,
    fragmented, dotted). Every state of a score shares one tonic and tempo, so transitions stay musical.
compose_stinger(kind, preset, key, mode, bpm, seed, motif) -> info dict   (combat_start boss quest_complete
    discovery danger defeat level_up)
compose_score(preset, key, mode, bpm, seed, region, ...) -> manifest of every cue + stinger
region_motif(name, seed), motif_transform(motif, kind), voice_lead(chords), vl_report(...), progression(...)

CLI
---
python music_engine.py --demo                 demo set -> _music_demo/
python music_engine.py --style battle --seconds 60 --seed 3 --stems
python music_engine.py --world "Crimson Citadel" --seconds 90
python music_engine.py --sfx explosion --seed 2
python music_engine.py --bed 30 --bpm 120 --style cozy
python music_engine.py --verify [dir]         ffprobe/loudness/loop/BPM report
python music_engine.py --cue combat --preset folk --key "A minor" --bars 8 --out DIR
python music_engine.py --score --preset orchestral --key D --region "Firefly Fen" --out DIR

Output format: 48 kHz, stereo, 16-bit PCM WAV (TPDF dither), master at about
-14 LUFS integrated (BS.1770 measurement, confirmed with ffmpeg loudnorm's
analysis pass), sample peak <= -1 dBFS through a memoryless soft clipper
(memoryless so the loop region stays sample-identical on every pass).
"""
from __future__ import annotations

import json
import math
import os
import random
import re
import shutil
import struct
import subprocess
import sys
import time

if os.environ.get('GS_MUSIC_NO_NUMPY'):
    np = None
    HAVE_NUMPY = False
else:
    try:
        import numpy as np
        HAVE_NUMPY = True
    except Exception:  # pragma: no cover
        np = None
        HAVE_NUMPY = False

VERSION = '1.1.0'
SR = 48000
ROOT = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(ROOT, '_music_out')
DEMO_DIR = os.path.join(ROOT, '_music_demo')
TARGET_LUFS = -14.0
CEILING = 10 ** (-1.0 / 20)          # -1 dBFS sample-peak ceiling
TABLE_N = 2048
STEMS = ('drums', 'bass', 'pads', 'lead')
BUSES = ('drums', 'bass', 'pads', 'arp', 'lead')
BUS_TO_STEM = {'drums': 'drums', 'bass': 'bass', 'pads': 'pads', 'arp': 'pads', 'lead': 'lead'}


def mtof(m):
    return 440.0 * 2 ** ((m - 69) / 12.0)


# =============================================================================
# Music theory
# =============================================================================
NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
_NOTE_ALIASES = {'DB': 1, 'EB': 3, 'GB': 6, 'AB': 8, 'BB': 10, 'CB': 11, 'FB': 4, 'E#': 5, 'B#': 0}
MODES = {
    'major': [0, 2, 4, 5, 7, 9, 11], 'minor': [0, 2, 3, 5, 7, 8, 10],
    'dorian': [0, 2, 3, 5, 7, 9, 10], 'lydian': [0, 2, 4, 6, 7, 9, 11],
    'mixolydian': [0, 2, 4, 5, 7, 9, 10], 'phrygian': [0, 1, 3, 5, 7, 8, 10],
}
# mood -> (mode, [progressions]); roman case = chord quality, root = mode degree
MOODS = {
    'heroic': ('major', [['I', 'V', 'vi', 'IV'], ['I', 'IV', 'vi', 'V'], ['vi', 'IV', 'I', 'V'], ['I', 'iii', 'IV', 'V']]),
    'happy': ('major', [['I', 'vi', 'IV', 'V'], ['I', 'V', 'vi', 'IV'], ['IV', 'V', 'iii', 'vi']]),
    'playful': ('major', [['I', 'IV', 'V', 'I'], ['I', 'vi', 'ii', 'V'], ['I', 'iii', 'IV', 'V']]),
    'triumphant': ('mixolydian', [['I', 'VII', 'IV', 'I'], ['I', 'IV', 'VII', 'I'], ['IV', 'VII', 'I', 'I']]),
    'calm': ('major', [['I', 'iii', 'IV', 'I'], ['IV', 'I', 'V', 'vi'], ['I', 'V', 'IV', 'I']]),
    'dreamy': ('lydian', [['I', 'II', 'iii', 'II'], ['I', 'II', 'vi', 'V'], ['vi', 'II', 'I', 'V']]),
    'nostalgic': ('major', [['IV', 'iii', 'vi', 'V'], ['I', 'iii', 'vi', 'IV'], ['vi', 'IV', 'I', 'V']]),
    'mysterious': ('dorian', [['i', 'IV', 'i', 'VII'], ['i', 'III', 'IV', 'i'], ['i', 'VII', 'IV', 'i']]),
    'dark': ('minor', [['i', 'VI', 'III', 'VII'], ['i', 'iv', 'VI', 'V'], ['i', 'VII', 'VI', 'VII']]),
    'epic': ('minor', [['i', 'VI', 'III', 'VII'], ['i', 'VII', 'VI', 'VII'], ['VI', 'VII', 'i', 'i'], ['i', 'VI', 'VII', 'V']]),
    'tense': ('phrygian', [['i', 'II', 'i', 'VII'], ['i', 'VI', 'II', 'i'], ['i', 'II', 'VI', 'VII']]),
    'sad': ('minor', [['i', 'iv', 'VII', 'III'], ['VI', 'VII', 'i', 'i'], ['i', 'VI', 'iv', 'V']]),
}
MOOD_ALIASES = {'melancholy': 'sad', 'peaceful': 'calm', 'cheerful': 'happy', 'scary': 'tense',
                'spooky': 'mysterious', 'action': 'epic', 'warm': 'nostalgic', 'chill': 'calm'}
MODE_DEFAULT_MOOD = {'major': 'happy', 'minor': 'dark', 'dorian': 'mysterious', 'lydian': 'dreamy',
                     'mixolydian': 'triumphant', 'phrygian': 'tense'}
_ROMAN = {'i': 0, 'ii': 1, 'iii': 2, 'iv': 3, 'v': 4, 'vi': 5, 'vii': 6}

# The five world moods of the builder -> music defaults
WORLD_MOODS = {
    'Bioluminescent Twilight': {'style': 'synthwave', 'mood': 'dreamy', 'bpm': 92, 'key': 'D',
                                'sfx_tone': 'glassy', 'blurb': 'glowing lydian supersaws, slow arps, wide delay'},
    'Alpine Sunset': {'style': 'adventure', 'mood': 'heroic', 'bpm': 100, 'key': 'G',
                      'sfx_tone': 'warm', 'blurb': 'strings, harp, horn melody, timpani on the swells'},
    'Sub-Zero Aurora': {'style': 'ambient', 'mood': 'mysterious', 'bpm': 72, 'key': 'F#',
                        'sfx_tone': 'icy', 'blurb': 'dorian pads that breathe, crystal bells, huge reverb'},
    'Crimson Citadel': {'style': 'battle', 'mood': 'epic', 'bpm': 144, 'key': 'D',
                        'sfx_tone': 'heavy', 'blurb': 'taiko + toms, staccato strings, brass stabs'},
    'Sunken Ruins': {'style': 'cozy', 'mood': 'mysterious', 'bpm': 78, 'key': 'A',
                     'sfx_tone': 'muffled', 'blurb': 'lo-fi dorian keys, dusty swung drums, tape wow'},
}

STYLE_DEFAULTS = {
    'adventure': {'bpm': 100, 'mood': 'heroic'},
    'ambient': {'bpm': 70, 'mood': 'dreamy'},
    'chiptune': {'bpm': 140, 'mood': 'happy'},
    'synthwave': {'bpm': 100, 'mood': 'nostalgic'},
    'battle': {'bpm': 150, 'mood': 'epic'},
    'cozy': {'bpm': 80, 'mood': 'nostalgic'},
}


def list_styles():
    return list(STYLE_DEFAULTS)


def list_moods():
    return list(MOODS) + list(MOOD_ALIASES)


def _parse_key(key, rng):
    """'D', 'F#', 'Bb', 'A minor', 'D dorian' -> (tonic_pc, mode_or_None)."""
    if key is None or key == '':
        return rng.choice([0, 2, 3, 5, 7, 9, 10, 4]), None
    if isinstance(key, int):
        return key % 12, None
    parts = str(key).strip().split()
    name = parts[0].upper().replace('♭', 'B').replace('♯', '#')
    mode = None
    if name.endswith('M') and len(name) > 1 and name[:-1] in NOTE_NAMES + list(_NOTE_ALIASES):
        name, mode = name[:-1], 'minor'
    if name in NOTE_NAMES:
        pc = NOTE_NAMES.index(name)
    elif name in _NOTE_ALIASES:
        pc = _NOTE_ALIASES[name]
    else:
        raise ValueError('unknown key %r (use e.g. "D", "F#", "Bb", "A minor")' % key)
    if len(parts) > 1:
        m = parts[1].lower()
        m = {'maj': 'major', 'min': 'minor', 'aeolian': 'minor', 'ionian': 'major'}.get(m, m)
        if m not in MODES:
            raise ValueError('unknown mode %r (use %s)' % (parts[1], ', '.join(MODES)))
        mode = m
    return pc, mode


def _parse_chord(token, tonic, scale):
    acc = 0
    while token and token[0] in 'b#':
        acc += -1 if token[0] == 'b' else 1
        token = token[1:]
    m = re.match(r'([ivIV]+)(.*)$', token)
    rn, suf = m.group(1), m.group(2)
    deg = _ROMAN[rn.lower()]
    root = (tonic + scale[deg] + acc) % 12
    if suf == 'o':
        ints = [0, 3, 6]
    elif rn.isupper():
        ints = [0, 4, 7]
    else:
        ints = [0, 3, 7]
    return {'token': rn + suf, 'deg': deg, 'root': root, 'ints': ints, 'major': rn.isupper()}


def _extend_chord(ch, style, rng):
    ints = list(ch['ints'])
    if style == 'cozy':
        if ch['major']:
            ints.append(10 if ch['deg'] == 4 else 11)
        else:
            ints.append(10)
        if rng.random() < 0.6:
            ints.append(14)
    elif style == 'ambient':
        ints.append(14)
        if ch['major'] and rng.random() < 0.5:
            ints.append(11)
    elif style == 'synthwave':
        if rng.random() < 0.5:
            ints.append(14)
        elif not ch['major']:
            ints.append(10)
    ch = dict(ch)
    ch['ints'] = ints
    ch['pcs'] = [(ch['root'] + i) % 12 for i in ints]
    return ch


def _voice(pcs, prev, lo, hi, n=4):
    """Pick a voicing of the chord pitch classes in [lo, hi] closest to prev (voice leading)."""
    tones = list(pcs)
    while len(tones) < n:
        tones.append(tones[len(tones) % len(pcs)])
    tones = tones[:max(n, len(pcs))]
    center = (lo + hi) / 2.0
    cands = []
    for r in range(len(tones)):
        rot = tones[r:] + tones[:r]
        for start in range(lo, hi + 1):
            if start % 12 != rot[0]:
                continue
            notes = [start]
            for pc in rot[1:]:
                nx = notes[-1] + 1
                while nx % 12 != pc:
                    nx += 1
                notes.append(nx)
            if notes[-1] <= hi:
                cands.append(notes)
            if len(notes) >= 4:
                d2 = sorted(notes[:-2] + [notes[-2] - 12] + [notes[-1]])
                if d2[0] >= lo:
                    cands.append(d2)
    if not cands:
        base = lo + ((pcs[0] - lo) % 12)
        return sorted(base + ((p - pcs[0]) % 12) for p in pcs)

    def cost(c):
        s = 0.35 * abs(sum(c) / len(c) - center)
        if prev:
            a, b = sorted(c), sorted(prev)
            if len(a) == len(b):
                s += sum(abs(x - y) for x, y in zip(a, b))
            else:
                s += abs(sum(a) / len(a) - sum(b) / len(b)) * 2
        s += 0.5 * max(0, (c[1] - c[0]) < 3 and c[0] < 52)    # muddy low cluster
        return s
    return sorted(min(cands, key=cost))


# ----------------------------------------------------------------------------
# Melody generator: motifs, sequence, variation, cadence
# ----------------------------------------------------------------------------
CELLS = {
    'legato': [[(0, 4), (4, 2), (6, 2), (8, 8)], [(0, 6), (6, 2), (8, 4), (12, 4)], [(0, 8), (8, 4), (12, 4)],
               [(0, 2), (2, 2), (4, 4), (8, 6), (14, 2)], [(0, 4), (4, 4), (8, 8)], [(0, 6), (6, 2), (8, 8)]],
    'busy': [[(0, 2), (2, 2), (4, 2), (6, 2), (8, 4), (12, 2), (14, 2)], [(0, 3), (3, 3), (6, 2), (8, 2), (10, 2), (12, 4)],
             [(0, 2), (2, 1), (3, 1), (4, 4), (8, 2), (10, 2), (12, 4)], [(0, 4), (4, 2), (6, 2), (8, 2), (10, 2), (12, 2), (14, 2)]],
    'sparse': [[(0, 8), (8, 8)], [(0, 12), (12, 4)], [(2, 6), (8, 8)], [(0, 6), (6, 10)], [(4, 12)]],
    'syncop': [[(0, 3), (3, 3), (6, 4), (10, 6)], [(0, 2), (3, 3), (6, 2), (8, 6), (14, 2)],
               [(2, 2), (4, 3), (7, 5), (12, 4)], [(0, 4), (6, 2), (8, 3), (11, 5)]],
}
CADENCE_CELLS = [[(0, 4), (4, 12)], [(0, 16)], [(0, 2), (2, 2), (4, 12)], [(0, 8), (8, 8)], [(0, 6), (6, 10)]]
_STEP_W = [(-2, 0.10), (-1, 0.26), (1, 0.26), (2, 0.12), (0, 0.07), (3, 0.07), (-3, 0.07), (4, 0.025), (-4, 0.025)]


def _wchoice(rng, pairs):
    r = rng.random() * sum(w for _, w in pairs)
    for v, w in pairs:
        r -= w
        if r <= 0:
            return v
    return pairs[-1][0]


def _new_motif(rng, cells):
    cell = rng.choice(cells)
    ivs = [0]
    for _ in range(1, len(cell)):
        last = ivs[-1]
        if abs(last) >= 3:
            step = -(1 if last > 0 else -1) * rng.choice([1, 1, 2])   # leap -> step back
        else:
            step = _wchoice(rng, _STEP_W)
        ivs.append(step)
    return {'rhythm': list(cell), 'ivs': ivs}


def _vary(m, rng, kind=None):
    kind = kind or rng.choice(['invert', 'ornament', 'tail', 'shift'])
    r, iv = list(m['rhythm']), list(m['ivs'])
    if kind == 'invert':
        iv = [iv[0]] + [-x for x in iv[1:]]
    elif kind == 'ornament':
        j = max(range(len(r)), key=lambda k: r[k][1])
        st, ln = r[j]
        if ln >= 4:
            h = ln // 2
            r[j:j + 1] = [(st, h), (st + h, ln - h)]
            iv[j + 1:j + 1] = [rng.choice([1, -1])]
    elif kind == 'tail':
        for k in range(max(1, len(iv) - 2), len(iv)):
            iv[k] = _wchoice(rng, _STEP_W)
    elif kind == 'shift' and len(r) > 2:
        j = rng.randrange(1, len(r))
        st, ln = r[j]
        pst = r[j - 1][0]
        nst = st + rng.choice([-1, 1])
        if pst < nst < st + ln:
            r[j - 1] = (pst, nst - pst)
            r[j] = (nst, st + ln - nst)
    return {'rhythm': r, 'ivs': iv}


class _MelodyCtx(object):
    def __init__(self, tonic_pc, scale, lo, hi):
        self.scale = scale
        self.tonic = 60 + tonic_pc - (12 if tonic_pc > 6 else 0)
        self.lo, self.hi = lo, hi

    def midi(self, d):
        return self.tonic + 12 * (d // 7) + self.scale[d % 7]

    def deg_near(self, midi):
        best, bd = 0, 999
        for d in range(-21, 35):
            dd = abs(self.midi(d) - midi)
            if dd < bd:
                best, bd = d, dd
        return best

    def clamp(self, d):
        while self.midi(d) > self.hi:
            d -= 7
        while self.midi(d) < self.lo:
            d += 7
        return d

    def snap(self, d, pcs, prefer=None):
        targets = prefer if prefer is not None else pcs
        for off in (0, -1, 1, -2, 2, -3, 3):
            if self.midi(d + off) % 12 in targets:
                return d + off
        return d


def _phrase(ctx, chords, plan, rng, d, cadence):
    """Realize a 4-bar phrase. plan entries are motif dicts or 'cad'.
    Returns ([(bar, step, len, midi, vel)], last_degree)."""
    notes = []
    for bi, ch in enumerate(chords):
        p = plan[bi]
        pcs = set(ch['pcs'])
        if p == 'cad':
            cell = rng.choice(CADENCE_CELLS)
            tgt = {ch['root']} if cadence == 'full' else {ch['pcs'][1 % len(ch['pcs'])], ch['pcs'][2 % len(ch['pcs'])]}
            for j, (st, ln) in enumerate(cell):
                if j == len(cell) - 1:
                    d = ctx.clamp(ctx.snap(d, pcs, tgt))
                else:
                    d = ctx.clamp(ctx.snap(d + rng.choice([1, -1, 2]), pcs) if j == 0 else d + rng.choice([-1, 1]))
                notes.append((bi, st, ln, ctx.midi(d), 0.85 if j == len(cell) - 1 else 0.75))
            continue
        for j, (st, ln) in enumerate(p['rhythm']):
            d = d + p['ivs'][j]
            if ctx.midi(d) > ctx.hi or ctx.midi(d) < ctx.lo:
                d = ctx.clamp(d)
            strong = st % 8 == 0
            if strong or ln >= 6 or (st % 4 == 0 and rng.random() < 0.55):
                d = ctx.snap(d, pcs)
            vel = 0.9 if st % 8 == 0 else (0.78 if st % 4 == 0 else 0.68)
            notes.append((bi, st, ln, ctx.midi(d), vel))
    return notes, d


def _block_melodies(ctx, progA, progB, rng, cell_kind):
    cells = CELLS[cell_kind]
    m1 = _new_motif(rng, cells)
    m2 = _new_motif(rng, cells)
    while m2['rhythm'] == m1['rhythm'] and len(cells) > 1:
        m2 = _new_motif(rng, cells)
    d0 = ctx.deg_near((ctx.lo + ctx.hi) // 2 - 3)
    mel = {}
    mel['A'], d = _phrase(ctx, progA, [m1, m1, _vary(m1, rng), 'cad'], rng, d0, 'half')
    mel['A2'], _ = _phrase(ctx, progA, [m1, m1, _vary(m1, rng, 'ornament'), 'cad'], rng, d0, 'full')
    dB = d0 + rng.choice([2, 3, 4])
    mel['B'], _ = _phrase(ctx, progB, [m2, _vary(m2, rng, 'shift'), m2, 'cad'], rng, dB, 'half')
    mel['B2'], _ = _phrase(ctx, progB, [m2, _vary(m2, rng, 'tail'), _vary(m1, rng, 'invert'), 'cad'], rng, dB, 'full')
    # C (breakdown): augmentation of the main motif, long notes only
    aug = {'rhythm': [(st, min(16 - st, ln * 2)) for st, ln in m1['rhythm'] if st < 8], 'ivs': m1['ivs']}
    aug['ivs'] = aug['ivs'][:len(aug['rhythm'])]
    if not aug['rhythm']:
        aug = {'rhythm': [(0, 16)], 'ivs': [0]}
    mel['C'], _ = _phrase(ctx, progA, [aug, aug, aug, 'cad'], rng, d0, 'full')
    return mel


# =============================================================================
# Styles: arrangement data
# =============================================================================
def _P(*rows):
    return rows


DRUMS = {
    'adventure': {
        'lo': {'timp': 'X.......x.......'},
        'mid': {'timp': 'X.......x...x...', 'gran': 'X...............', 'snare': '....o.......o...'},
        'hi': {'timp': 'X..x..x.X.......', 'gran': 'X.......X.......', 'snare': '....X..o....X.oo', 'shaker': 'x.o.x.o.x.o.x.o.'},
        'fill': {'timp': 'x.x.x.x.xoxoXXXX', 'snare': '........oooxxxXX'},
        'bed': {'gran': 'X...x...X...x...', 'timp': 'X.......x.......', 'snare': '....o.......o...'},
    },
    'battle': {
        'lo': {'taiko': 'X.......X..x....', 'kick': 'X.......X.......', 'shaker': 'x.x.x.x.x.x.x.x.'},
        'mid': {'taiko': 'X..xX..xX..xX.x.', 'kick': 'X...X...X...X...', 'snare': '....X.......X...', 'hat': 'x.x.x.x.x.x.x.x.'},
        'hi': {'taiko': 'X..xX..xX..xX.xx', 'kick': 'X..xX...X..xX...', 'snare': '....X.......X..x',
               'hat': 'xoxoxoxoxoxoxoxo', 'tom_l': '..............xx'},
        'fill': {'tom_h': '........xx......', 'tom_m': '..........xx....', 'tom_l': '............xxXX', 'snare': '........x.x.xxxx'},
        'bed': {'taiko': 'X...x...X...x...', 'kick': 'X...X...X...X...', 'snare': '....X.......X...', 'hat': 'x.x.x.x.x.x.x.x.'},
    },
    'synthwave': {
        'lo': {'kick808': 'X...X...X...X...', 'ohat': '..x...x...x...x.'},
        'mid': {'kick808': 'X...X...X...X...', 'snare': '....X.......X...', 'ohat': '..x...x...x...x.', 'chh': 'x.o.x.o.x.o.x.o.'},
        'hi': {'kick808': 'X...X...X...X...', 'snare': '....X.......X...', 'clap': '....x.......x...',
               'ohat': '..x...x...x...x.', 'chh': 'xoxoxoxoxoxoxoxo'},
        'fill': {'tom_h': '........xx......', 'tom_m': '..........xx....', 'tom_l': '............xxXx'},
        'bed': {'kick808': 'X...X...X...X...', 'snare': '....X.......X...', 'ohat': '..x...x...x...x.', 'chh': 'o.o.o.o.o.o.o.o.'},
    },
    'chiptune': {
        'lo': {'ckick': 'X.......X.......', 'chat': '..x...x...x...x.'},
        'mid': {'ckick': 'X.......X.x.....', 'csnare': '....X.......X...', 'chat': 'x.x.x.x.x.x.x.x.'},
        'hi': {'ckick': 'X.....x.X.x.....', 'csnare': '....X.......X..x', 'chat': 'x.xxx.x.x.xxx.x.'},
        'fill': {'csnare': '........x.x.xxxx'},
        'bed': {'ckick': 'X...X...X...X...', 'csnare': '....X.......X...', 'chat': '..x...x...x...x.'},
    },
    'ambient': {
        'lo': {},
        'mid': {'pulse': 'X.......x.......'},
        'hi': {'pulse': 'X.......X.......', 'rim': '....o.......o...', 'shaker': '..o...o...o...o.'},
        'fill': {},
        'bed': {'dkick': 'X...x...X...x...', 'rim': '....x.......x...'},
    },
    'cozy': {
        'lo': {'dkick': 'X.........x.....', 'lhat': 'x.o.x.o.x.o.x.o.'},
        'mid': {'dkick': 'X......x..X.....', 'rim': '....X.......X...', 'lhat': 'x.o.x.oox.o.x.o.'},
        'hi': {'dkick': 'X......x..X..x..', 'snare_lofi': '....X.......X...', 'lhat': 'x.oxx.oxx.oxx.ox', 'shaker': '..o...o...o...o.'},
        'fill': {'snare_lofi': '............x.xo'},
        'bed': {'dkick': 'X.......X.x.....', 'snare_lofi': '....X.......X...', 'lhat': 'x...x...x...x...'},
    },
}
_VEL = {'X': 1.0, 'x': 0.72, 'o': 0.45, '-': 0.25}

# bass patterns: (step, len, role)  R root, O octave, 5 fifth, A approach to next root
BASS = {
    'adventure': {'lo': ('lowstr', [(0, 16, 'R')]), 'mid': ('pizz_bass', [(0, 8, 'R'), (8, 8, '5')]),
                  'hi': ('pizz_bass', [(0, 4, 'R'), (4, 4, 'R'), (8, 4, '5'), (12, 4, 'A')])},
    'battle': {'lo': ('lowstr', [(0, 16, 'R')]),
               'mid': ('str_stacc', [(i, 2, 'R' if i % 8 else 'O') for i in range(0, 16, 2)]),
               'hi': ('str_stacc', [(i, 1, 'R5RRR5RRRR5RR5R5'[i] if 'R5RRR5RRRR5RR5R5'[i] != 'R' else 'R') for i in range(16)])},
    'synthwave': {'lo': ('saw_bass', [(i, 2, 'R') for i in range(0, 16, 2)]),
                  'mid': ('saw_bass', [(i, 2, 'R' if (i // 2) % 2 == 0 else 'O') for i in range(0, 16, 2)]),
                  'hi': ('saw_bass', [(i, 1, 'R' if i % 4 != 2 else 'O') for i in range(16)])},
    'chiptune': {'lo': ('tri_nes', [(0, 4, 'R'), (4, 4, 'O'), (8, 4, 'R'), (12, 4, 'O')]),
                 'mid': ('tri_nes', [(i, 2, 'RORO5O5A'[i // 2]) for i in range(0, 16, 2)]),
                 'hi': ('tri_nes', [(i, 2, 'RORO5ORA'[i // 2]) for i in range(0, 16, 2)])},
    'ambient': {'lo': ('sub_drone', [(0, 16, 'R')]), 'mid': ('sub_drone', [(0, 16, 'R')]), 'hi': ('sub_drone', [(0, 16, 'R')])},
    'cozy': {'lo': ('round_bass', [(0, 8, 'R'), (10, 4, '5')]),
             'mid': ('round_bass', [(0, 6, 'R'), (7, 3, 'R'), (10, 4, '5'), (14, 2, 'A')]),
             'hi': ('round_bass', [(0, 6, 'R'), (7, 3, 'O'), (10, 4, '5'), (14, 2, 'A')])},
}

STYLE_CFG = {
    'adventure': dict(pad='strings', pad_rng=(52, 74), pad_min=0.0, comp=None,
                      arp=('harp', 2, 'up', 0.5), lead={'A': 'flute', 'A2': 'flute', 'B': 'horn', 'B2': 'horn', 'C': 'flute'},
                      lead_rng=(62, 84), cells='legato', harmony=('strings', 'B2'), reverb=(2.8, 0.02),
                      sends={'drums': 0.18, 'bass': 0.08, 'pads': 0.3, 'arp': 0.32, 'lead': 0.3},
                      delay=None, sidechain={}, swing=0.0, human=0.006, master_lp=None, crash='crash', swell=True),
    'battle': dict(pad='strings', pad_rng=(50, 70), pad_min=0.0, comp=('brass', 'X.....X.....X...', 0.6),
                   arp=None, lead={'B': 'horn', 'B2': 'horn', 'A2': 'horn'}, lead_rng=(60, 79), cells='busy',
                   harmony=('brass', 'B2'), reverb=(2.2, 0.015),
                   sends={'drums': 0.14, 'bass': 0.06, 'pads': 0.25, 'arp': 0.2, 'lead': 0.22},
                   delay=None, sidechain={'pads': 0.15, 'bass': 0.1}, swing=0.0, human=0.003, master_lp=None,
                   crash='crash', swell=True),
    'synthwave': dict(pad='supersaw', pad_rng=(52, 72), pad_min=0.0, comp=None,
                      arp=('arp_saw', 1, 'updown', 0.0), lead={'B': 'saw_lead', 'B2': 'saw_lead', 'A2': 'saw_lead'},
                      lead_rng=(64, 84), cells='syncop', harmony=None, reverb=(2.4, 0.03),
                      sends={'drums': 0.12, 'bass': 0.0, 'pads': 0.25, 'arp': 0.22, 'lead': 0.25},
                      delay=(0.75, 0.38, {'arp': 0.22, 'lead': 0.3}), sidechain={'pads': 0.5, 'bass': 0.35, 'arp': 0.25},
                      swing=0.0, human=0.0, master_lp=None, crash='crash', swell=True),
    'chiptune': dict(pad=None, pad_rng=(55, 72), pad_min=0.0, comp=None,
                     arp=('chip_arp', 1, 'chip', 0.5), lead={'A': 'pulse25', 'A2': 'pulse25', 'B': 'pulse25', 'B2': 'pulse25', 'C': 'pulse12'},
                     lead_rng=(67, 88), cells='busy', harmony=('pulse12', 'B2'), reverb=(0.9, 0.01),
                     sends={'drums': 0.04, 'bass': 0.0, 'pads': 0.08, 'arp': 0.08, 'lead': 0.1},
                     delay=(0.5, 0.25, {'lead': 0.18}), sidechain={}, swing=0.0, human=0.0, master_lp=None,
                     crash=None, swell=False),
    'ambient': dict(pad='warm_pad', pad_rng=(50, 76), pad_min=0.0, comp=None,
                    arp=('bell', 4, 'updown', 0.8), lead={'A2': 'bell', 'B': 'bell', 'B2': 'bell', 'C': 'bell'},
                    lead_rng=(69, 91), cells='sparse', harmony=None, reverb=(6.0, 0.04),
                    sends={'drums': 0.25, 'bass': 0.05, 'pads': 0.5, 'arp': 0.55, 'lead': 0.55},
                    delay=(1.5, 0.45, {'lead': 0.35, 'arp': 0.3}), sidechain={}, swing=0.0, human=0.0, master_lp=None,
                    crash=None, swell=True),
    'cozy': dict(pad=None, pad_rng=(52, 74), pad_min=0.0, comp=('keys', 'X.....x...x.....', 0.0),
                 arp=None, lead={'A2': 'vibes', 'B': 'vibes', 'B2': 'vibes'}, lead_rng=(67, 86), cells='syncop',
                 harmony=None, reverb=(1.6, 0.02),
                 sends={'drums': 0.1, 'bass': 0.0, 'pads': 0.2, 'arp': 0.2, 'lead': 0.25},
                 delay=(0.75, 0.3, {'lead': 0.2}), sidechain={'pads': 0.15, 'bass': 0.1}, swing=0.22, human=0.008,
                 master_lp=9500, crash=None, swell=False),
}

INST_GAIN = {
    'kick': 0.95, 'kick808': 0.95, 'dkick': 0.85, 'gran': 0.8, 'pulse': 0.75, 'snare': 0.55, 'snare_lofi': 0.5,
    'clap': 0.38, 'rim': 0.32, 'hat': 0.16, 'chh': 0.13, 'ohat': 0.15, 'lhat': 0.13, 'shaker': 0.13,
    'tom_h': 0.5, 'tom_m': 0.52, 'tom_l': 0.55, 'taiko': 0.75, 'timp': 0.6, 'crash': 0.22, 'swell': 0.2,
    'ckick': 0.5, 'csnare': 0.32, 'chat': 0.11,
    'strings': 0.14, 'warm_pad': 0.13, 'supersaw': 0.1, 'harp': 0.3, 'pizz_bass': 0.6, 'lowstr': 0.32,
    'str_stacc': 0.3, 'keys': 0.2, 'bell': 0.2, 'vibes': 0.24, 'horn': 0.3, 'flute': 0.28, 'brass': 0.13,
    'saw_lead': 0.2, 'arp_saw': 0.12, 'saw_bass': 0.42, 'sub_drone': 0.36, 'round_bass': 0.55, 'tri_nes': 0.5,
    'pulse25': 0.17, 'pulse12': 0.12, 'chip_arp': 0.09, 'wind': 0.12,
}
DRUM_INSTS = {'kick', 'kick808', 'dkick', 'gran', 'pulse', 'snare', 'snare_lofi', 'clap', 'rim', 'hat', 'chh', 'ohat',
              'lhat', 'shaker', 'tom_h', 'tom_m', 'tom_l', 'taiko', 'crash', 'swell', 'ckick', 'csnare', 'chat'}
KICKS = {'kick', 'kick808', 'dkick', 'gran', 'ckick', 'pulse'}


# =============================================================================
# Song plan (pure Python)
# =============================================================================
def _plan_bars(seconds, bar_s, tail_s):
    avail = seconds - tail_s
    nb = int(avail // bar_s)
    if nb >= 14:
        intro, outro = (4 if nb >= 30 else 2), 2
        loop = ((nb - intro - outro) // 4) * 4
        left = nb - intro - outro - loop          # leftover bars (<= 3): 1 to the intro, rest to the outro
        intro += min(1, left)
        outro += left - min(1, left)
    elif nb >= 7:
        intro, outro, loop = 1, 1, 4
        intro += nb - 6
    else:
        intro, outro = 0, 1
        loop = max(1, nb - 1)
    return intro, loop, outro


def _plan_song(style, mood, bpm, seconds, seed, key, bed=False):
    rng = random.Random('%s|%s|%s|%s|%s' % (style, mood, bpm, seed, key))
    tonic, kmode = _parse_key(key, rng)
    mood = MOOD_ALIASES.get(mood, mood)
    if mood not in MOODS:
        raise ValueError('unknown mood %r (use one of: %s)' % (mood, ', '.join(list_moods())))
    mode, progs = MOODS[mood]
    if kmode and kmode != mode:
        mood = MODE_DEFAULT_MOOD[kmode]
        mode, progs = MOODS[mood]
    scale = MODES[mode]
    pA = rng.choice(progs)
    pB = rng.choice([p for p in progs if p != pA] or progs)
    progA = [_extend_chord(_parse_chord(t, tonic, scale), style, rng) for t in pA]
    progB = [_extend_chord(_parse_chord(t, tonic, scale), style, rng) for t in pB]
    pre_tok = 'IV' if scale[2] == 4 else 'VI'
    tonic_tok = 'I' if scale[2] == 4 else 'i'
    outro_prog = [_extend_chord(_parse_chord(t, tonic, scale), style, rng) for t in (pre_tok, tonic_tok)]

    bar_s = 240.0 / bpm
    rev_t = STYLE_CFG[style]['reverb'][0]
    tail_s = max(1.5, min(4.0, rev_t * 0.6))
    intro_b, loop_b, outro_b = _plan_bars(seconds, bar_s, tail_s)

    blocks = []
    nblk = max(1, int(math.ceil(loop_b / 4.0)))
    tmpl = {1: ['A'], 2: ['A', 'B'], 3: ['A', 'B', 'B2'], 4: ['A', 'A2', 'B', 'B2']}
    if nblk in tmpl:
        blocks = tmpl[nblk]
    else:
        cyc = ['A', 'A2', 'B', 'B2', 'C', 'A2', 'B', 'B2']
        blocks = [cyc[i % len(cyc)] for i in range(nblk)]
    inten = {'A': 0.6, 'A2': 0.75, 'B': 0.95, 'B2': 1.0, 'C': 0.4}
    cfg = STYLE_CFG[style]
    ctx = _MelodyCtx(tonic, scale, *cfg['lead_rng'])
    mel = _block_melodies(ctx, progA, progB, rng, cfg['cells'])

    def mk(section, i, blk, bib, chord, inten_v, last):
        return {'section': section, 'i': i, 'block': blk, 'bib': bib, 'chord': chord, 'int': inten_v,
                'last_in_block': last, 'next': None}

    intro = [mk('intro', i, 'I', i, progA[(i - intro_b) % 4], 0.3, i == intro_b - 1) for i in range(intro_b)]
    loop = []
    for i in range(loop_b):
        blk = blocks[i // 4]
        bib = i % 4
        prog = progB if blk in ('B', 'B2') else progA
        last = (bib == 3) or (i == loop_b - 1)
        loop.append(mk('loop', i, blk, bib, prog[bib], inten[blk], last))
    outro = []
    for i in range(outro_b):
        ch = outro_prog[-outro_b + i] if outro_b <= 2 else outro_prog[min(i, 1)]
        outro.append(mk('outro', i, 'O', i, ch, 0.5, i == outro_b - 1))
    # next-chord links (loop wraps onto itself so the last bar approaches bar 0)
    for seq, nxt_first in ((intro, loop[0] if loop else None), (loop, loop[0] if loop else None), (outro, None)):
        for k, b in enumerate(seq):
            b['next'] = seq[k + 1]['chord'] if k + 1 < len(seq) else (nxt_first['chord'] if nxt_first else b['chord'])
    if loop and outro:
        loop[-1]['next_outro'] = outro[0]['chord']

    # voice leading: loop solved twice (steady state at the wrap), intro & outro chained
    def voice_seq(seq, lo, hi, prev):
        for b in seq:
            prev = _voice(b['chord']['pcs'], prev, lo, hi, 4 if len(b['chord']['pcs']) <= 4 else 5)
            b.setdefault('voicing', {})[(lo, hi)] = prev
        return prev
    lo, hi = cfg['pad_rng']
    prev = voice_seq(loop, lo, hi, None)
    voice_seq(loop, lo, hi, prev)
    voice_seq(intro, lo, hi, None)
    voice_seq(outro, lo, hi, loop[-1]['voicing'][(lo, hi)] if loop else None)

    return {
        'style': style, 'mood': mood, 'mode': mode, 'tonic': tonic, 'key_name': '%s %s' % (NOTE_NAMES[tonic], mode),
        'scale': scale, 'bpm': bpm, 'bar_s': bar_s, 's16': bar_s / 16.0, 'seconds': seconds, 'tail_s': tail_s,
        'progA': [c['token'] for c in progA], 'progB': [c['token'] for c in progB],
        'intro': intro, 'loop': loop, 'outro': outro, 'blocks': blocks, 'melody': mel, 'rng': rng, 'bed': bed,
        'cfg': cfg,
    }


def _bass_midi(pc):
    m = 36 + pc
    return m - 12 if m > 45 else m


def _arrange_bar(song, b, t0, evs, rng):
    """Append events for one bar starting at section-local time t0."""
    style, cfg = song['style'], song['cfg']
    s16, bar_s = song['s16'], song['bar_s']
    sw = cfg['swing']
    hum = cfg['human']
    ch = b['chord']
    inten = b['int']
    sec = b['section']
    bed = song['bed']
    lvl = 'lo' if inten < 0.5 else ('mid' if inten < 0.85 else 'hi')
    if sec == 'intro':
        lvl = 'lo'

    def at(step):
        t = t0 + step * s16
        if sw and step % 2 == 1:
            t += sw * s16
        if hum:
            t += rng.gauss(0, hum)
        return max(t0, t)        # never before the bar start: keeps every loop event inside the loop

    def ev(bus, inst, step, ln_steps, midi=None, vel=1.0, pan=0.0, t=None):
        tt = at(step) if t is None else t
        evs.append((tt, bus, inst, midi, max(0.03, ln_steps * s16), vel, pan))

    voicing = b['voicing'][cfg['pad_rng']]
    slow_bed = bed and song['bpm'] < 92        # slow beds: keep harmony on the beat so tempo reads unambiguously
    outro_last = sec == 'outro' and b['last_in_block']
    hold = (bar_s + song['tail_s'] * 0.8) / s16 if outro_last else 16

    # ---- pads / chords -------------------------------------------------------
    if cfg['pad']:
        pv = 0.55 + 0.35 * inten
        if style == 'ambient' or b['bib'] % 1 == 0:
            for k, m in enumerate(voicing):
                pan = (k / max(1, len(voicing) - 1) - 0.5) * 0.7
                ev('pads', cfg['pad'], 0, hold if outro_last else 16.0, m, pv, pan, t=t0)
    if cfg['comp']:
        inst, pat, cmin = cfg['comp']
        if inten >= cmin or style == 'cozy':
            if slow_bed:
                pat = 'X.......x.......'
            hits = [(i, c) for i, c in enumerate(pat) if c != '.']
            if outro_last or (sec == 'intro' and style != 'cozy'):
                hits = [(0, 'X')]
            for j, (st, c) in enumerate(hits):
                ln = (hits[j + 1][0] - st) if j + 1 < len(hits) else 16 - st
                if outro_last:
                    ln = hold
                if inst == 'brass':
                    ln = min(ln, 3)
                for k, m in enumerate(voicing):
                    pan = (k / max(1, len(voicing) - 1) - 0.5) * 0.5
                    ev('pads', inst, st, ln * 0.92, m, _VEL[c] * (0.7 + 0.3 * inten), pan)

    # ---- bass -----------------------------------------------------------------
    blvl = 'lo' if (sec == 'intro' or outro_last) else lvl
    if style == 'ambient' or sec != 'intro' or b['i'] >= 1:
        inst, pat = BASS[style][blvl]
        root = _bass_midi(ch['root'])
        nxt = _bass_midi(b['next']['root'])
        if slow_bed and style != 'ambient':
            pat = [(0, 8, 'R'), (8, 8, '5')]
        if outro_last:
            pat = [(0, hold, 'R')]
        for st, ln, role in pat:
            if role == 'R':
                m = root
            elif role == 'O':
                m = root + 12
            elif role == '5':
                m = root + 7 if root + 7 <= 50 else root - 5
            else:
                cands = [nxt - 1, nxt + 1, nxt - 2, nxt + 2]
                scale_pcs = [(song['tonic'] + s) % 12 for s in song['scale']]
                dia = [c for c in cands if c % 12 in scale_pcs]
                m = nxt - 1 if style == 'cozy' else (dia[0] if dia else nxt)
            ev('bass', inst, st, ln * (0.9 if ln <= 2 else 0.97), m, 0.85 if st % 4 == 0 else 0.7)

    # ---- arpeggio ----------------------------------------------------------------
    arp = cfg['arp']
    if slow_bed and style == 'ambient':
        arp = None
    if arp and inten >= arp[3] and not outro_last and (sec != 'intro' or style in ('synthwave', 'ambient')):
        inst, step, order, _ = arp
        tones = sorted(voicing) + [voicing[0] + 12, voicing[1] + 12]
        if style == 'ambient':
            tones = [m + 12 for m in sorted(voicing)]
        if order == 'up':
            seq = [0, 1, 2, 3, 4, 3, 2, 1]
        elif order == 'updown':
            seq = [0, 1, 2, 3, 4, 5, 4, 3, 2, 1, 2, 3, 4, 3, 2, 1]
        else:
            seq = [0, 1, 2]
        if inst == 'chip_arp':
            # classic chip arpeggio: chord tones cycled at 1/32-ish rate, 1 bar long sound
            nt = int(bar_s / 0.045)
            dt = bar_s / nt
            pts = sorted(voicing)[:3]
            for k in range(nt):
                if (k * dt) / s16 % 4 >= 3.2:     # breathing gap each beat
                    continue
                evs.append((t0 + k * dt, 'arp', inst, pts[k % 3] + 12, dt * 0.95, 0.8, 0.25))
        else:
            n = 16 // step
            for k in range(n):
                idx = seq[k % len(seq)] % len(tones)
                v = 0.85 if (k * step) % 4 == 0 else 0.62
                pan = 0.35 * math.sin(k * 0.9)
                ev('arp', inst, k * step, step * (0.9 if step > 1 else 0.8), tones[idx], v, pan)

    # ---- lead melody ------------------------------------------------------------------
    blk = b['block']
    lead_inst = cfg['lead'].get(blk) if sec == 'loop' else None
    if lead_inst:
        for (bi, st, ln, m, v) in song['melody'][blk]:
            if bi != b['bib']:
                continue
            vv = v * (0.55 if bed else 1.0)
            ev('lead', lead_inst, st, ln * (0.8 if style == 'chiptune' else 0.95), m, vv, 0.05)
            if cfg['harmony'] and blk == cfg['harmony'][1]:
                # diatonic third below (or a sixth) as a softer second voice
                scale_pcs = [(song['tonic'] + s) % 12 for s in song['scale']]
                h = m - 3 if (m - 3) % 12 in scale_pcs else m - 4
                ev('lead', cfg['harmony'][0], st, ln * 0.95, h, vv * 0.55, -0.3)
            if style == 'chiptune':
                pass
    if outro_last and cfg['lead']:
        # final melody note: the tonic, held into the ring-out
        mi = song['tonic'] + 72
        while mi > cfg['lead_rng'][1]:
            mi -= 12
        inst = list(cfg['lead'].values())[-1]
        ev('lead', inst, 0, min(hold, 16 + 8), mi, 0.6 * (0.55 if bed else 1.0), 0.05)

    # ---- drums ------------------------------------------------------------------------
    dk = DRUMS[style]
    lanes = dk['bed'] if (bed and sec == 'loop') else dk[lvl]
    if bed and sec == 'loop' and song['bpm'] < 92:
        lanes = {k: v for k, v in lanes.items() if k not in ('chh', 'lhat', 'shaker', 'hat')}
    if blk == 'C' and not bed:
        lanes = {k: v for k, v in dk['lo'].items()}
    fill = dict(dk.get('fill', {}))
    do_fill = fill and b['last_in_block'] and sec in ('loop', 'intro') and (
        sec == 'intro' or (b['i'] + 1) % (8 if bed else 4) == 0) and (inten >= 0.5 or sec == 'intro')
    if sec == 'outro':
        lanes = {}
    if sec == 'intro' and not do_fill and style not in ('ambient', 'cozy', 'synthwave'):
        lanes = {k: v for k, v in dk['lo'].items()}
    if sec == 'intro' and b['i'] == 0 and style in ('synthwave',):
        lanes = {}
    for inst, pat in lanes.items():
        for st, c in enumerate(pat):
            if c == '.' or (do_fill and st >= 8 and inst not in ('kick', 'kick808', 'ckick')):
                continue
            midi = None
            if inst == 'timp':
                midi = _timp_midi(ch['root'])
            evs.append((at(st) if inst not in KICKS else t0 + st * s16 + (sw * s16 if (sw and st % 2) else 0.0),
                        'drums', inst, midi, s16, _VEL[c] * (0.75 + 0.25 * inten), _DRUM_PAN.get(inst, 0.0)))
    if do_fill:
        for inst, pat in fill.items():
            for st, c in enumerate(pat):
                if c == '.' or st < 8:
                    continue
                midi = _timp_midi(ch['root'] if st < 12 else b['next']['root']) if inst == 'timp' else None
                evs.append((at(st), 'drums', inst, midi, s16, _VEL[c], _DRUM_PAN.get(inst, 0.0)))
    # crash on section downbeats, reverse swell into B
    if cfg['crash'] and sec == 'loop' and b['bib'] == 0 and blk in ('A', 'B', 'B2') and (b['i'] == 0 or blk != 'A'):
        evs.append((t0, 'drums', cfg['crash'], None, 2.0, 0.8, 0.2))
    if cfg['crash'] and sec == 'outro' and b['i'] == 0:
        evs.append((t0, 'drums', cfg['crash'], None, 2.5, 0.9, -0.2))
    if outro_last and style in DRUMS and style not in ('ambient', 'cozy'):
        big = {'adventure': 'timp', 'battle': 'taiko', 'synthwave': 'kick808', 'chiptune': 'ckick'}[style]
        evs.append((t0, 'drums', big, _timp_midi(ch['root']) if big == 'timp' else None, s16, 1.0, 0.0))
    if cfg['swell'] and sec == 'loop' and b['last_in_block'] and b['bib'] == 3:
        nxt_i = b['i'] + 1
        nxt_blk = song['blocks'][(nxt_i // 4) % len(song['blocks'])] if song['loop'] else 'A'
        if nxt_blk in ('B',) or (style == 'ambient' and nxt_blk in ('B', 'A')):
            evs.append((t0 + bar_s * 0.5, 'drums', 'swell', None, bar_s * 0.5, 0.7, 0.0))
    if style == 'ambient' and b['bib'] == 0 and sec != 'outro':
        evs.append((t0, 'pads', 'wind', None, bar_s * 4, 0.6 + 0.3 * inten, 0.0))


_DRUM_PAN = {'hat': 0.3, 'chh': 0.3, 'ohat': -0.25, 'lhat': 0.25, 'shaker': -0.35, 'tom_h': 0.35, 'tom_m': 0.0,
             'tom_l': -0.35, 'rim': 0.15, 'chat': 0.2, 'clap': -0.05}


def _timp_midi(pc):
    m = 41 + ((pc - 41) % 12)       # F2..E3
    return m


def _build_events(song):
    """Return (events, layout). Timeline: [intro | preroll | loop | outro | ring]."""
    bar_s = song['bar_s']
    I = len(song['intro']) * bar_s
    L = len(song['loop']) * bar_s
    O = len(song['outro']) * bar_s
    rev_t = song['cfg']['reverb'][0]
    need = max(8.0, rev_t + 2.0)
    P = min(math.ceil(need / bar_s), 10 ** 6) * bar_s
    rng = song['rng']
    intro_ev, loop_ev, outro_ev = [], [], []
    for b in song['intro']:
        _arrange_bar(song, b, b['i'] * bar_s, intro_ev, rng)
    for b in song['loop']:
        _arrange_bar(song, b, b['i'] * bar_s, loop_ev, rng)
    for b in song['outro']:
        _arrange_bar(song, b, b['i'] * bar_s, outro_ev, rng)
    def tag_rr(lst):
        # round-robin drum variant fixed per event at arrangement time, so the preroll copy == the loop
        cnt, out = {}, []
        for e in lst:
            k = cnt.get(e[2], 0)
            cnt[e[2]] = k + 1
            out.append(e + (k % 3,))
        return out
    intro_ev, loop_ev, outro_ev = tag_rr(intro_ev), tag_rr(loop_ev), tag_rr(outro_ev)
    # everything on the integer sample grid: preroll copy and loop land on identical sample offsets
    Is, Ps, Ls, Os = (int(round(v * SR)) for v in (I, P, L, O))
    Hs = int(0.5 * SR)                       # loop head after the loop in timeline A (filter look-ahead)

    def smp(e):
        return int(round(e[0] * SR))

    def shift(lst, off, lo=None, hi=None):
        out = []
        for e in lst:
            s_ = smp(e) + off
            if (lo is None or s_ >= lo) and (hi is None or s_ < hi):
                out.append((s_ / float(SR),) + e[1:])
        return out

    def preroll(end_s):
        out = []
        reps = int(math.ceil(Ps / float(Ls))) if Ls > 0 else 0
        for r in range(1, reps + 1):
            out += shift(loop_ev, end_s - r * Ls, lo=end_s - Ps)
        return out
    # Timeline A: intro | preroll | loop | loop head   (intro and loop region come from here)
    loop0s = Is + Ps
    ev_a = shift(intro_ev, 0) + preroll(loop0s) + shift(loop_ev, loop0s) + shift(loop_ev, loop0s + Ls, hi=loop0s + Ls + Hs)
    # Timeline B: preroll | outro | ring              (outro comes from here)
    ev_b = preroll(Ps) + shift(outro_ev, Ps)
    I, P, L, O = Is / float(SR), Ps / float(SR), Ls / float(SR), Os / float(SR)
    total = song['seconds']
    ring = total - (I + L + O)
    layout = {'I': I, 'P': P, 'L': L, 'O': O, 'H': Hs / float(SR), 'ring': ring,
              'WA': I + P + L + Hs / float(SR), 'WB': P + O + max(ring, 0.0) + 0.05}
    return (ev_a, ev_b), layout


# =============================================================================
# DSP (numpy)
# =============================================================================
_TABLES = {}
_NOTE_CACHE = {}


_FASTLEN = {}


def _pow2(n):
    """Smallest 5-smooth length >= n (fast for pocketfft, much less padding than 2**k)."""
    n = max(2, int(n))
    best = _FASTLEN.get(n)
    if best:
        return best
    best = 1 << int(math.ceil(math.log2(n)))
    p5 = 1
    while p5 < best:
        p35 = p5
        while p35 < best:
            q = p35
            while q < n:
                q *= 2
            if q < best:
                best = q
            p35 *= 3
        p5 *= 5
    _FASTLEN[n] = best
    return best


def _filt(x, lo=None, hi=None, order=2, peak=None):
    """Zero-phase Butterworth-magnitude filter via FFT (x: 1-D or 2-D, last axis time)."""
    n = x.shape[-1]
    N = _pow2(n + 64)
    X = np.fft.rfft(x, N)
    f = np.fft.rfftfreq(N, 1.0 / SR)
    H = np.ones_like(f)
    if lo:
        H *= 1.0 / np.sqrt(1.0 + (lo / np.maximum(f, 1e-3)) ** (2 * order))
    if hi:
        H *= 1.0 / np.sqrt(1.0 + (f / hi) ** (2 * order))
    if peak:
        for fc, gain_db, q in peak:
            g = 10 ** (gain_db / 20.0) - 1.0
            H *= 1.0 + g / (1.0 + ((f - fc) / (fc / q)) ** 2)
    return np.fft.irfft(X * H, N)[..., :n]


def _fir_hp(x, fc, taps=4095):
    """Linear-phase windowed-sinc high-pass, applied as a *local* convolution (loop-safe), delay compensated."""
    m = np.arange(taps) - (taps - 1) / 2.0
    lp = np.sinc(2 * fc / SR * m) * (2 * fc / SR) * np.blackman(taps)
    lp /= lp.sum()
    h = -lp
    h[(taps - 1) // 2] += 1.0
    n = x.shape[-1]
    N = _pow2(n + taps)
    y = np.fft.irfft(np.fft.rfft(x, N) * np.fft.rfft(h, N), N)
    d = (taps - 1) // 2
    return y[..., d:d + n]


def _table(shape, f0, fc):
    key = (shape, int(round(12 * math.log2(max(f0, 1.0) / 8.0) * 2)), int(fc / 60))
    t = _TABLES.get(key)
    if t is not None:
        return t
    nh = int(min(SR * 0.45 / max(f0, 1.0), TABLE_N // 2 - 1))
    nh = max(nh, 1)
    k = np.arange(1, nh + 1, dtype=np.float64)
    if shape == 'saw':
        a = 1.0 / k
    elif shape == 'square':
        a = np.where(k % 2 == 1, 1.0 / k, 0.0)
    elif shape == 'soft':
        a = 1.0 / k ** 2
    elif shape.startswith('pulse'):
        d = {'pulse12': 0.125, 'pulse25': 0.25, 'pulse50': 0.5}[shape]
        a = np.abs(np.sin(np.pi * k * d)) / k
    elif shape == 'tri':
        a = np.where(k % 2 == 1, 1.0 / k ** 2, 0.0) * np.where((k // 2) % 2 == 0, 1.0, -1.0)
    elif shape == 'organ':
        a = np.zeros_like(k)
        for h, g in ((1, 1.0), (2, 0.5), (3, 0.25), (4, 0.12), (6, 0.06)):
            if h <= nh:
                a[h - 1] = g
    else:
        a = np.zeros_like(k)
        a[0] = 1.0
    ref = math.sqrt(float(np.sum(a ** 2)) / 2.0) or 1.0
    lp = 1.0 / np.sqrt(1.0 + (k * f0 / max(fc, 1.0)) ** 4)
    spec = np.zeros(TABLE_N // 2 + 1, dtype=np.complex128)
    spec[1:nh + 1] = -1j * a * lp * (TABLE_N / 2.0)
    tab = np.fft.irfft(spec, TABLE_N) / ref * 0.5
    tab = np.append(tab, tab[0])
    if len(_TABLES) > 4000:
        _TABLES.clear()
    _TABLES[key] = tab
    return tab


def _osc(tab, freq, n, phase0=0.0):
    if np.isscalar(freq):
        ph = phase0 + freq * np.arange(n) / SR
    else:
        ph = phase0 + np.cumsum(freq) / SR
    ph -= np.floor(ph)
    x = ph * TABLE_N
    i = x.astype(np.int64)
    fr = x - i
    return tab[i] * (1.0 - fr) + tab[i + 1] * fr


def _osc_xf(dark, bright, freq, n, mix, phase0=0.0):
    """Two tables read with one shared phase, crossfaded by `mix` (0 = dark, 1 = bright)."""
    if np.isscalar(freq):
        ph = phase0 + freq * np.arange(n) / SR
    else:
        ph = phase0 + np.cumsum(freq) / SR
    ph -= np.floor(ph)
    x = ph * TABLE_N
    i = x.astype(np.int64)
    fr = x - i
    d0, d1 = dark[i], dark[i + 1]
    b0, b1 = bright[i], bright[i + 1]
    a = d0 + (b0 - d0) * mix
    b = d1 + (b1 - d1) * mix
    return a + (b - a) * fr


def _adsr(gate, a, d, s, r, curve=1.0):
    ng = max(1, int(gate * SR))
    nr = max(16, int(r * SR))
    n = ng + nr
    t = np.arange(n) / SR
    if a > 0:
        att = np.clip(t / a, 0, 1)
        att = np.sin(att * np.pi / 2) ** (2 * curve)
    else:
        att = np.ones(n)
    dec = s + (1.0 - s) * np.exp(-np.maximum(t - a, 0) / max(d, 1e-4))
    e = np.where(t < a, att, dec)
    lvl = e[ng - 1]
    tail = lvl * np.exp(-np.maximum(t - gate, 0) / max(r / 4.6, 1e-4))
    e = np.where(np.arange(n) < ng, e, tail)
    k = min(96, n)
    e[-k:] *= np.linspace(1, 0, k)
    return e


def _rng_for(*key):
    h = 2166136261
    for ch in repr(key):
        h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
    return np.random.default_rng(h)


def _vib(n, rate, depth, delay):
    t = np.arange(n) / SR
    ramp = np.clip((t - delay) / 0.4, 0, 1)
    return 1.0 + depth * ramp * np.sin(2 * np.pi * rate * t)


def _ks(f0, n, t60, bright, rng):
    p_exact = SR / f0
    P = max(2, int(p_exact - 0.5))
    ratio = f0 / (SR / (P + 0.5))
    m = int(n * ratio) + P + 4
    y = np.zeros(m)
    exc = rng.uniform(-1, 1, P)
    k = max(1, int((1.0 - bright) * 10))
    if k > 1:
        exc = np.convolve(exc, np.ones(k) / k, 'same')
    exc -= exc.mean()
    y[1:P + 1] = exc
    c = 10 ** (-3.0 / (t60 * f0))
    s = P + 1
    while s < m:
        e = min(s + P, m)
        L = e - s
        y[s:e] = c * 0.5 * (y[s - P:s - P + L] + y[s - P - 1:s - P - 1 + L])
        s = e
    y = y[1:]
    out = np.interp(np.arange(n) * ratio, np.arange(len(y)), y)
    return out / (np.max(np.abs(out[:min(n, 2000)])) + 1e-9)


def _stereo(mono, width_samples=0):
    if width_samples:
        r = np.concatenate([np.zeros(width_samples), mono[:-width_samples]])
        return np.vstack([mono, r])
    return np.vstack([mono, mono])


# ---- pitched instruments ------------------------------------------------------------
def _i_strings(midi, dur, vel, rng, short=False, dark=False):
    f0 = mtof(midi)
    if short:
        env = _adsr(dur, 0.008, 0.11, 0.35, 0.09)
        fc = 3800 + 1800 * vel
    else:
        env = _adsr(dur, 0.32, 0.6, 0.85, 0.55, curve=0.8)
        fc = (1100 if dark else 2000) + 1800 * vel
    n = len(env)
    tab = _table('saw', f0, fc)
    vib = _vib(n, 5.1, 0.0028, 0.35 if not short else 9)
    out = np.zeros((2, n))
    detunes = [-9, 4, -3, 10] if not short else [-5, 6]
    for j, c in enumerate(detunes):
        out[j % 2] += _osc(tab, f0 * 2 ** (c / 1200.0) * vib, n, rng.random())
    out *= env / (len(detunes) / 2)
    if not short:
        out = _filt(out, lo=70, peak=((350, 2.0, 1.5), (2600, 2.5, 1.2)))
    return out


def _i_warm_pad(midi, dur, vel, rng):
    # Band-limited (< 4 kHz) so it is rendered at half rate (2x pitch, n/2 samples) and stretched back.
    f0 = mtof(midi)
    env = _adsr(dur, 1.6, 1.0, 0.9, 2.6, curve=1.0)
    n = len(env)
    h = n // 2 + 2
    t = np.arange(h) * 2.0 / SR
    dark = _table('soft', 2 * f0, 2 * 1400)
    bright = _table('saw', 2 * f0, 2 * (2600 + 1200 * vel))
    lfo = 0.5 + 0.5 * np.sin(2 * np.pi * t / (7.0 + (midi % 5)) + midi)
    bright75 = bright * 0.75          # dark*(1-0.6*lfo) + 0.45*bright*lfo as one crossfade
    mixc = 0.6 * lfo
    half = np.zeros((2, h))
    for j, c in enumerate([-7, 6, -2, 3]):
        f = 2 * f0 * 2 ** (c / 1200.0)
        half[j % 2] += _osc_xf(dark, bright75, f, h, mixc, rng.random())
    xi = np.arange(n) / 2.0
    out = np.vstack([np.interp(xi, np.arange(h), half[0]), np.interp(xi, np.arange(h), half[1])])
    return out * env / 2.0


def _i_supersaw(midi, dur, vel, rng):
    f0 = mtof(midi)
    env = _adsr(dur, 0.06, 0.5, 0.88, 0.4)
    n = len(env)
    tab = _table('saw', f0, 3200 + 1500 * vel)
    out = np.zeros((2, n))
    cents = [-17, -11, -5, 0, 5, 11, 17]
    for j, c in enumerate(cents):
        g = 1.0 if c else 1.3
        o = _osc(tab, f0 * 2 ** (c / 1200.0), n, rng.random()) * g
        if c == 0:
            out += o * 0.7
        else:
            out[j % 2] += o
    return out * env / 3.6


def _i_harp(midi, dur, vel, rng):
    f0 = mtof(midi)
    t60 = max(0.9, 3.2 - (midi - 48) * 0.05)
    n = int(min(t60 * 0.9 + 0.05, max(dur, 0.2) + 1.6) * SR)
    y = _ks(f0, n, t60, 0.55 + 0.35 * vel, rng)
    t = np.arange(n) / SR
    y = 0.75 * y + 0.35 * np.sin(2 * np.pi * f0 * t) * np.exp(-t / (t60 * 0.5))
    y *= np.minimum(1, t / 0.002)
    y[-200:] *= np.linspace(1, 0, 200)
    return y


def _i_pizz_bass(midi, dur, vel, rng):
    f0 = mtof(midi)
    n = int((min(dur, 1.2) + 0.25) * SR)
    y = _ks(f0, n, 1.1, 0.35 + 0.2 * vel, rng)
    t = np.arange(n) / SR
    sub = np.sin(2 * np.pi * f0 * t) * np.exp(-t / 0.45)
    y = 0.6 * y + 0.6 * sub
    y *= np.minimum(1, t / 0.003)
    rel = np.clip((dur + 0.2 - t) / 0.2, 0, 1)
    return y * rel


def _i_round_bass(midi, dur, vel, rng):
    f0 = mtof(midi)
    env = _adsr(dur, 0.006, 0.35, 0.65, 0.09)
    n = len(env)
    tab = _table('tri', f0, 700)
    t = np.arange(n) / SR
    y = 0.75 * _osc(tab, f0, n) * 2.2 + 0.5 * np.sin(2 * np.pi * f0 * t)
    return np.tanh(1.3 * y * env) * 0.9


def _i_sub_drone(midi, dur, vel, rng):
    f0 = mtof(midi)
    env = _adsr(dur, 1.2, 1.0, 0.9, 2.0)
    n = len(env)
    t = np.arange(n) / SR
    y = np.sin(2 * np.pi * f0 * t) + 0.18 * np.sin(2 * np.pi * 2 * f0 * t + 0.5) + 0.08 * np.sin(2 * np.pi * 3 * f0 * t)
    return y * env * 0.8


def _i_saw_bass(midi, dur, vel, rng):
    f0 = mtof(midi)
    env = _adsr(dur, 0.004, 0.18, 0.7, 0.05)
    n = len(env)
    t = np.arange(n) / SR
    fe = np.exp(-t / 0.09)
    dark = _table('saw', f0, 380)
    bright = _table('saw', f0, 1900 + 800 * vel)
    ph = rng.random()
    y = _osc_xf(dark, bright, f0, n, fe, ph)
    y = 1.1 * y + 0.55 * np.sin(2 * np.pi * f0 * t)
    return np.tanh(1.4 * y) * env * 0.8


def _i_keys(midi, dur, vel, rng):
    f = mtof(midi)
    rel = 0.28
    n = int((dur + rel) * SR)
    t = np.arange(n) / SR
    I = (0.6 + 1.8 * vel) * np.exp(-t / 0.28) + 0.22
    car = np.sin(2 * np.pi * f * t + I * np.sin(2 * np.pi * f * t))
    if f * 14 < 18000:
        car += 0.1 * vel * np.sin(2 * np.pi * f * 13.98 * t) * np.exp(-t / 0.018)
    dec = 1.0 + 2.6 * min(1.0, max(0.0, (84 - midi) / 36.0))
    amp = np.exp(-t / dec) * (1 - np.exp(-t / 0.0025))
    gate = np.where(t < dur, 1.0, np.exp(-(t - dur) / (rel / 4.6)))
    y = np.tanh(1.2 * car * amp * gate)
    trem = 0.22 * np.sin(2 * np.pi * 4.2 * t)
    out = np.vstack([y * (1 + trem), y * (1 - trem)])
    out[:, -96:] *= np.linspace(1, 0, 96)
    return out


def _i_bell(midi, dur, vel, rng, ratio=3.5, idx=2.4, dec=2.6):
    f = mtof(midi)
    n = int(min(max(dur, 0.4) + dec, 6.0) * SR)
    t = np.arange(n) / SR
    I = idx * vel * np.exp(-t / 0.5)
    y = np.sin(2 * np.pi * f * t + I * np.sin(2 * np.pi * f * ratio * t))
    y += 0.25 * np.sin(2 * np.pi * f * 2.0 * t) * np.exp(-t / (dec * 0.4))
    amp = np.exp(-t / dec) * (1 - np.exp(-t / 0.0015))
    y = y * amp
    y[-200:] *= np.linspace(1, 0, 200)
    return y * 0.8


def _i_vibes(midi, dur, vel, rng):
    f = mtof(midi)
    n = int(min(max(dur, 0.3) + 1.4, 4.0) * SR)
    t = np.arange(n) / SR
    I = 1.1 * vel * np.exp(-t / 0.12)
    y = np.sin(2 * np.pi * f * t + I * np.sin(2 * np.pi * f * 4.0 * t))
    y += 0.12 * np.sin(2 * np.pi * f * 3.0 * t) * np.exp(-t / 0.3)
    amp = np.exp(-t / 1.1) * (1 - np.exp(-t / 0.002))
    trem = 1 + 0.28 * np.sin(2 * np.pi * 5.0 * t)
    y = y * amp
    y[-200:] *= np.linspace(1, 0, 200)
    return np.vstack([y * trem, y * (2 - trem)]) * 0.75


def _i_horn(midi, dur, vel, rng):
    f0 = mtof(midi)
    env = _adsr(dur, 0.09, 0.35, 0.85, 0.3)
    n = len(env)
    dark = _table('saw', f0, f0 * 2.0)
    bright = _table('saw', f0, min(6000, f0 * (4.5 + 2 * vel)))
    vib = _vib(n, 4.8, 0.0045, 0.35)
    br = env ** 1.6
    out = np.zeros((2, n))
    for j, c in enumerate([-4, 4]):
        ph = rng.random()
        f = f0 * 2 ** (c / 1200.0) * vib
        out[j] = _osc_xf(dark, bright, f, n, br, ph)
    out = _filt(out, lo=90, peak=((700, 3.0, 1.2),))
    return out * env * 1.1


def _i_flute(midi, dur, vel, rng):
    f0 = mtof(midi)
    env = _adsr(dur, 0.07, 0.3, 0.9, 0.18)
    n = len(env)
    tab = _table('soft', f0, 4200)
    vib = _vib(n, 5.3, 0.006, 0.25)
    y = _osc(tab, f0 * vib, n, rng.random()) * 1.4
    br = _filt(rng.standard_normal(n), lo=1800, hi=7500) * 0.05
    y = (y + br) * env
    return y


def _i_brass(midi, dur, vel, rng):
    f0 = mtof(midi)
    env = _adsr(dur, 0.025, 0.22, 0.55, 0.16)
    n = len(env)
    t = np.arange(n) / SR
    fe = np.exp(-t / 0.18)
    dark = _table('saw', f0, f0 * 2.5)
    bright = _table('saw', f0, min(7000, f0 * 9))
    out = np.zeros((2, n))
    for j, c in enumerate([-6, 0, 6]):
        ph = rng.random()
        f = f0 * 2 ** (c / 1200.0)
        o = _osc_xf(dark, bright, f, n, fe, ph)
        if c == 0:
            out += o * 0.6
        else:
            out[j // 2] += o
    return np.tanh(out * env * 1.4)


def _i_saw_lead(midi, dur, vel, rng):
    f0 = mtof(midi)
    env = _adsr(dur, 0.012, 0.3, 0.8, 0.22)
    n = len(env)
    tab = _table('saw', f0, 4200 + 1500 * vel)
    sq = _table('square', f0 / 2, 1600)
    vib = _vib(n, 5.5, 0.005, 0.28)
    out = np.zeros((2, n))
    for j, c in enumerate([-7, 7]):
        out[j] += _osc(tab, f0 * 2 ** (c / 1200.0) * vib, n, rng.random())
    out += 0.35 * _osc(sq, f0 / 2 * vib, n, 0.0)
    return out * env * 0.8


def _i_arp_saw(midi, dur, vel, rng):
    f0 = mtof(midi)
    env = _adsr(dur, 0.002, 0.12, 0.25, 0.08)
    n = len(env)
    t = np.arange(n) / SR
    fe = np.exp(-t / 0.07)
    dark = _table('saw', f0, 900)
    bright = _table('saw', f0, 6500)
    ph = rng.random()
    y = _osc_xf(dark, bright, f0, n, fe, ph)
    return y * env


def _i_chip(shape):
    def f(midi, dur, vel, rng):
        f0 = mtof(midi)
        env = _adsr(dur, 0.002, 0.12, 0.6, 0.03)
        n = len(env)
        env = np.round(env * 15) / 15.0                   # 4-bit volume steps
        vib = _vib(n, 6.0, 0.012, 0.18) if shape != 'pulse50' else 1.0
        tab = _table(shape, f0, 14000)
        return _osc(tab, f0 * vib, n, 0.0) * env
    return f


_NES_TRI = None


def _i_tri_nes(midi, dur, vel, rng):
    global _NES_TRI
    if _NES_TRI is None:
        steps = np.concatenate([np.arange(16), np.arange(15, -1, -1)]) / 7.5 - 1.0
        tab = np.repeat(steps, TABLE_N // 32)
        tab = np.append(tab, tab[0])
        _NES_TRI = tab * 0.8
    f0 = mtof(midi)
    n = int((dur + 0.01) * SR)
    y = _osc(_NES_TRI, f0, n, 0.0)
    k = min(64, n)
    y[:k] *= np.linspace(0, 1, k)
    y[-k:] *= np.linspace(1, 0, k)
    return y


def _i_wind(midi, dur, vel, rng):
    n = int(dur * SR)
    t = np.arange(n) / SR
    out = np.zeros((2, n))
    for c in range(2):
        nz = rng.standard_normal(n)
        out[c] = _filt(nz, lo=250, hi=2200, order=1)
    sw = np.sin(np.pi * t / dur) ** 2
    mod = 1 + 0.3 * np.sin(2 * np.pi * t / (dur / 2.0))
    return out * sw * mod * 0.6


# ---- drums ----------------------------------------------------------------------------------
def _d_kick(kind, vel, rng):
    p = {'kick': (165, 48, 0.034, 0.32, 0.35), 'kick808': (125, 43, 0.05, 0.75, 0.18),
         'dkick': (115, 50, 0.03, 0.22, 0.08), 'gran': (95, 40, 0.07, 0.65, 0.04),
         'pulse': (78, 46, 0.045, 0.32, 0.0)}[kind]
    fh, fl, tp, dec, click = p
    n = int(min(dec * 5, 2.5) * SR)
    t = np.arange(n) / SR
    f = fl + (fh - fl) * np.exp(-t / tp)
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / dec)
    nz = rng.standard_normal(n)
    if click:
        body += _filt(nz, lo=1500, hi=9000) * np.exp(-t / 0.004) * click
    if kind == 'gran':
        body += _filt(nz, hi=380) * np.exp(-t / 0.25) * 0.6
    y = np.tanh(1.8 * body) / math.tanh(1.8)
    y *= np.minimum(1, t / (0.0012 if kind != 'pulse' else 0.004))
    if kind in ('dkick', 'pulse'):
        y = _filt(y, hi=3000 if kind == 'dkick' else 700)
    y[-128:] *= np.linspace(1, 0, 128)
    return y


def _d_snare(kind, vel, rng):
    n = int(0.45 * SR)
    t = np.arange(n) / SR
    nz = rng.standard_normal(n)
    if kind == 'clap':
        env = np.zeros(n)
        for ti in (0.0, 0.011, 0.023, 0.036):
            env += np.where(t >= ti, np.exp(-(t - ti) / 0.007), 0)
        env += np.where(t >= 0.036, np.exp(-(t - 0.036) / 0.13), 0) * 0.6
        return _filt(nz, lo=900, hi=4200) * env * 0.7
    if kind == 'rim':
        n2 = int(0.09 * SR)
        t2 = t[:n2]
        y = np.sin(2 * np.pi * 1750 * t2) * np.exp(-t2 / 0.012) + 0.6 * np.sin(2 * np.pi * 820 * t2) * np.exp(-t2 / 0.02)
        y += _filt(nz[:n2], lo=2000, hi=7000) * np.exp(-t2 / 0.005)
        return y * 0.6
    tone = (np.sin(2 * np.pi * np.cumsum(185 * (1 + 0.3 * np.exp(-t / 0.01))) / SR) * 0.6 +
            np.sin(2 * np.pi * 330 * t) * 0.25) * np.exp(-t / 0.075)
    noise = _filt(nz, lo=1300, hi=9500) * np.exp(-t / (0.16 if kind == 'snare' else 0.11))
    y = tone * 0.6 + noise * 0.75
    y *= np.minimum(1, t / 0.001)
    if kind == 'snare_lofi':
        y = _filt(y, lo=150, hi=4800)
    y[-128:] *= np.linspace(1, 0, 128)
    return np.tanh(1.3 * y)


def _metal(n, t, rng):
    fs = np.array([205.3, 304.4, 369.6, 522.7, 540.0, 800.0]) * (1 + 0.01 * rng.standard_normal(6))
    y = np.zeros(n)
    for f in fs:
        y += np.sign(np.sin(2 * np.pi * f * t + rng.random() * 6.28))
    return y / 6.0


def _d_hat(kind, vel, rng):
    open_ = kind == 'ohat'
    n = int((0.6 if open_ else 0.16) * SR)
    t = np.arange(n) / SR
    nz = rng.standard_normal(n)
    y = 0.55 * _metal(n, t, rng) + 0.7 * nz
    y = _filt(y, lo=7000 if kind != 'lhat' else 5500, hi=None if kind != 'lhat' else 8500, order=3)
    dec = {'hat': 0.035, 'chh': 0.025, 'ohat': 0.22, 'lhat': 0.03, 'chat': 0.02}.get(kind, 0.04)
    y *= np.exp(-t / dec) * np.minimum(1, t / 0.0005)
    y[-64:] *= np.linspace(1, 0, 64)
    return y


def _d_shaker(kind, vel, rng):
    n = int(0.16 * SR)
    t = np.arange(n) / SR
    y = _filt(rng.standard_normal(n), lo=4500, hi=11000, order=2)
    y *= np.minimum(1, t / 0.018) * np.exp(-np.maximum(t - 0.018, 0) / 0.04)
    y[-64:] *= np.linspace(1, 0, 64)
    return y


def _d_tom(kind, vel, rng):
    base = {'tom_h': 210, 'tom_m': 150, 'tom_l': 108, 'taiko': 68}[kind]
    dec = 0.5 if kind == 'taiko' else 0.28
    n = int(dec * 4 * SR)
    t = np.arange(n) / SR
    f = base * (1 + (0.9 if kind == 'taiko' else 0.55) * np.exp(-t / 0.035))
    y = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / dec)
    nz = rng.standard_normal(n)
    y += _filt(nz, lo=80, hi=1800 if kind == 'taiko' else 2500) * np.exp(-t / (0.06 if kind == 'taiko' else 0.025)) * 0.45
    if kind == 'taiko':
        y += 0.35 * np.sin(2 * np.pi * np.cumsum(f * 1.58) / SR) * np.exp(-t / 0.18)
    y = np.tanh(1.6 * y) * np.minimum(1, t / 0.001)
    y[-128:] *= np.linspace(1, 0, 128)
    return y


def _d_timp(midi, vel, rng):
    f0 = mtof(midi)
    n = int(2.2 * SR)
    t = np.arange(n) / SR
    y = np.zeros(n)
    for r, a, d in ((1.0, 1.0, 1.5), (1.5, 0.5, 1.0), (1.99, 0.32, 0.75), (2.44, 0.18, 0.55), (2.9, 0.1, 0.4)):
        y += a * np.sin(2 * np.pi * f0 * r * t * (1 + 0.006 * np.exp(-t / 0.08))) * np.exp(-t / (d * (0.7 + 0.4 * vel)))
    y += _filt(rng.standard_normal(n), hi=650) * np.exp(-t / 0.03) * 0.8
    y *= np.minimum(1, t / 0.002)
    y[-256:] *= np.linspace(1, 0, 256)
    return np.tanh(1.2 * y) * 0.8


def _d_crash(kind, dur, rng):
    if kind == 'swell':
        n = max(64, int(dur * SR))
        t = np.arange(n) / SR
        out = np.zeros((2, n))
        for c in range(2):
            out[c] = _filt(rng.standard_normal(n) + 0.5 * _metal(n, t, rng), lo=2200, order=2)
        env = (t / max(dur, 1e-3)) ** 2.6
        env[-48:] *= np.linspace(1, 0, 48)
        return out * env * 0.6
    n = int(3.2 * SR)
    t = np.arange(n) / SR
    out = np.zeros((2, n))
    for c in range(2):
        y = 0.6 * _metal(n, t, rng) + 0.8 * rng.standard_normal(n)
        out[c] = _filt(y, lo=3200, order=2)
    out *= np.exp(-t / 1.25) * np.minimum(1, t / 0.0008)
    out[:, -256:] *= np.linspace(1, 0, 256)
    return out


def _d_chip(kind, vel, rng):
    if kind == 'ckick':
        n = int(0.13 * SR)
        t = np.arange(n) / SR
        f = 40 + 140 * np.exp(-t / 0.025)
        y = np.sign(np.sin(2 * np.pi * np.cumsum(f) / SR))
        env = np.round(np.exp(-t / 0.05) * 15) / 15
        return _filt(y * env, hi=6000) * 0.9
    if kind == 'csnare':
        n = int(0.2 * SR)
        t = np.arange(n) / SR
        nz = np.repeat(np.sign(rng.standard_normal(n // 3 + 1)), 3)[:n]
        env = np.round(np.exp(-t / 0.07) * 15) / 15
        y = nz * env * 0.7 + np.sign(np.sin(2 * np.pi * 210 * t)) * np.exp(-t / 0.02) * 0.4
        return _filt(y, lo=150, hi=9000)
    n = int(0.07 * SR)
    t = np.arange(n) / SR
    env = np.round(np.exp(-t / 0.018) * 15) / 15
    return _filt(np.sign(rng.standard_normal(n)), lo=6500) * env


PITCHED = {
    'strings': _i_strings, 'lowstr': lambda m, d, v, r: _i_strings(m, d, v, r, dark=True),
    'str_stacc': lambda m, d, v, r: _i_strings(m, d, v, r, short=True),
    'warm_pad': _i_warm_pad, 'supersaw': _i_supersaw, 'harp': _i_harp, 'pizz_bass': _i_pizz_bass,
    'round_bass': _i_round_bass, 'sub_drone': _i_sub_drone, 'saw_bass': _i_saw_bass, 'keys': _i_keys,
    'bell': _i_bell, 'vibes': _i_vibes, 'horn': _i_horn, 'flute': _i_flute, 'brass': _i_brass,
    'saw_lead': _i_saw_lead, 'arp_saw': _i_arp_saw, 'pulse25': _i_chip('pulse25'), 'pulse12': _i_chip('pulse12'),
    'chip_arp': _i_chip('pulse50'), 'tri_nes': _i_tri_nes, 'wind': _i_wind,
}


def _render_note(inst, midi, dur, vel, rr=0):
    vb = round(min(1.0, max(0.0, vel)) * 4) / 4.0
    key = (inst, midi, round(dur, 3), vb, rr)
    buf = _NOTE_CACHE.get(key)
    if buf is not None:
        return buf
    rng = _rng_for(inst, midi, round(dur, 3), vb, rr)
    if inst in PITCHED:
        buf = PITCHED[inst](midi, dur, vb, rng)
    elif inst in ('kick', 'kick808', 'dkick', 'gran', 'pulse'):
        buf = _d_kick(inst, vb, rng)
    elif inst in ('snare', 'snare_lofi', 'clap', 'rim'):
        buf = _d_snare(inst, vb, rng)
    elif inst in ('hat', 'chh', 'ohat', 'lhat'):
        buf = _d_hat(inst, vb, rng)
    elif inst == 'shaker':
        buf = _d_shaker(inst, vb, rng)
    elif inst in ('tom_h', 'tom_m', 'tom_l', 'taiko'):
        buf = _d_tom(inst, vb, rng)
    elif inst == 'timp':
        buf = _d_timp(midi, vb, rng)
    elif inst in ('crash', 'swell'):
        buf = _d_crash(inst, dur, rng)
    elif inst in ('ckick', 'csnare', 'chat'):
        buf = _d_chip(inst, vb, rng)
    else:
        raise ValueError('unknown instrument %r' % inst)
    buf = buf.astype(np.float32)
    if len(_NOTE_CACHE) > 6000:
        _NOTE_CACHE.clear()
    _NOTE_CACHE[key] = buf
    return buf


def _mix_events(events, n_total):
    buses = {b: np.zeros((2, n_total), np.float32) for b in BUSES}
    for ev in events:
        t, bus, inst, midi, dur, vel, pan = ev[:7]
        k = ev[7] if len(ev) > 7 else 0
        i0 = int(round(t * SR))
        if i0 >= n_total:
            continue
        buf = _render_note(inst, midi, dur, vel, k if inst in DRUM_INSTS and inst not in ('crash', 'swell') else 0)
        amp = (max(0.0, vel) ** 1.3) * INST_GAIN.get(inst, 0.3)
        m = buf.shape[-1]
        a, b0 = i0, 0
        if a < 0:
            b0 = -a
            a = 0
        e = min(n_total, i0 + m)
        if e <= a:
            continue
        seg = buf[..., b0:b0 + (e - a)]
        if buf.ndim == 1:
            th = (max(-1.0, min(1.0, pan)) + 1) * math.pi / 4
            gl, gr = math.cos(th) * math.sqrt(2), math.sin(th) * math.sqrt(2)
            buses[bus][0, a:e] += seg * (amp * gl)
            buses[bus][1, a:e] += seg * (amp * gr)
        else:
            gl, gr = min(1.0, 1.0 - pan), min(1.0, 1.0 + pan)
            buses[bus][0, a:e] += seg[0] * (amp * gl)
            buses[bus][1, a:e] += seg[1] * (amp * gr)
    return buses


def _make_ir(t60, predelay, seed):
    rng = np.random.default_rng(seed)
    n = int((t60 * 0.75 + predelay) * SR)
    t = np.arange(n) / SR
    ir = np.zeros((2, n))
    pd = int(predelay * SR)
    for c in range(2):
        nz = rng.standard_normal(n)
        lo = _filt(nz, hi=500, order=1)
        mid = _filt(nz, lo=500, hi=4000, order=1)
        hi = _filt(nz, lo=4000, order=1)
        tt = np.maximum(t - predelay, 0)
        y = (lo * np.exp(-6.9 * tt / (t60 * 1.1)) + mid * np.exp(-6.9 * tt / t60) + hi * np.exp(-6.9 * tt / (t60 * 0.45)))
        y *= np.minimum(1, tt / 0.012) * (t >= predelay)
        for k in range(6):
            d = pd + int(SR * (0.004 + 0.009 * k + 0.003 * rng.random()))
            if d < n:
                y[d] += (0.9 - 0.12 * k) * (1 if rng.random() > 0.5 else -1) * 3
        ir[c] = y
    ir /= np.sqrt(np.sum(ir ** 2) / 2)
    ir[:, -256:] *= np.linspace(1, 0, 256)
    return ir


def _convolve(x, ir):
    n = x.shape[-1]
    N = _pow2(n + ir.shape[-1])
    y = np.empty_like(x)
    for c in range(2):
        y[c] = np.fft.irfft(np.fft.rfft(x[c], N) * np.fft.rfft(ir[c], N), N)[:n]
    return y


def _delay(x, beats, fb, bpm, taps=6):
    d = int(round(beats * 60.0 / bpm * SR))
    y = np.zeros_like(x)
    n = x.shape[-1]
    g = 1.0
    for k in range(1, taps + 1):
        g *= fb
        s = d * k
        if s >= n:
            break
        if k % 2:   # ping-pong
            y[1, s:] += x[0, :n - s] * g
            y[0, s:] += x[1, :n - s] * g * 0.6
        else:
            y[0, s:] += x[0, :n - s] * g
            y[1, s:] += x[1, :n - s] * g * 0.6
    return _filt(y, lo=180, hi=5000, order=1)


def _sidechain(n, kick_times, depth, bar_s):
    if not kick_times or depth <= 0:
        return None
    idx = np.zeros(n, np.int64)
    pos = sorted(set(int(round(t * SR)) for t in kick_times if 0 <= t * SR < n))
    if not pos:
        return None
    marks = np.zeros(n, np.int64)
    marks[pos] = np.array(pos)
    idx = np.maximum.accumulate(marks)
    has = np.maximum.accumulate((marks > 0).astype(np.int8)) | (np.arange(n) == 0)
    dt = (np.arange(n) - idx) / SR
    tau = min(0.12, bar_s / 16.0)
    duck = np.exp(-dt / tau) * np.minimum(1, dt / 0.004 + 0.3)
    duck = np.where(has & (np.arange(n) >= pos[0]), duck, 0)
    return (1.0 - depth * duck).astype(np.float32)


def _moving(x, w):
    w = max(1, int(w))
    c = np.cumsum(np.concatenate([np.zeros(1), x]))
    out = (c[w:] - c[:-w]) / w
    pad_l = w // 2
    return np.concatenate([np.full(pad_l, out[0]), out, np.full(len(x) - len(out) - pad_l, out[-1])])


def _bus_comp(x, ratio=2.2, above_db=4.0):
    p = (x[0].astype(np.float64) ** 2 + x[1].astype(np.float64) ** 2) / 2
    rms = np.sqrt(_moving(p, 0.03 * SR) + 1e-12)
    ref = 10 * math.log10(float(np.mean(p)) + 1e-12)
    thr = ref + above_db
    lvl = 20 * np.log10(rms)
    gr = np.maximum(lvl - thr, 0) * (1 - 1.0 / ratio)
    gr = _moving(gr, 0.06 * SR)
    return x * (10 ** (-gr / 20.0)).astype(np.float32)


def _soft_clip(x, ceiling=CEILING, knee=0.72):
    k = ceiling * knee
    a = np.abs(x)
    over = a > k
    y = x.copy()
    y[over] = np.sign(x[over]) * (k + (ceiling - k) * np.tanh((a[over] - k) / (ceiling - k)))
    return y


# ---- loudness (BS.1770-4 K-weighting, gated) ---------------------------------------------
_K1 = ([1.53512485958697, -2.69169618940638, 1.19839281085285], [1.0, -1.69065929318241, 0.73248077421585])
_K2 = ([1.0, -2.0, 1.0], [1.0, -1.99004745483398, 0.99007225036621])


def lufs(x):
    """Integrated loudness (LUFS) of a (2, n) float array at 48 kHz."""
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 1:
        x = np.vstack([x, x])
    n = x.shape[-1]
    if n < int(0.4 * SR):
        x = np.concatenate([x, np.zeros((2, int(0.4 * SR) - n + 1))], axis=1)
        n = x.shape[-1]
    N = _pow2(n)
    f = np.fft.rfftfreq(N, 1.0 / SR)
    z = np.exp(-2j * np.pi * f / SR)
    H = np.ones_like(z)
    for b, a in (_K1, _K2):
        H *= (b[0] + b[1] * z + b[2] * z * z) / (a[0] + a[1] * z + a[2] * z * z)
    y = np.fft.irfft(np.fft.rfft(x, N) * np.abs(H), N)[:, :n]
    blk, hop = int(0.4 * SR), int(0.1 * SR)
    c = np.cumsum(np.concatenate([np.zeros((2, 1)), y ** 2], axis=1), axis=1)
    starts = np.arange(0, n - blk + 1, hop)
    ms = ((c[:, starts + blk] - c[:, starts]) / blk).sum(axis=0)
    lj = -0.691 + 10 * np.log10(ms + 1e-20)
    g1 = ms[lj > -70]
    if g1.size == 0:
        return -70.0
    rel = -0.691 + 10 * math.log10(float(np.mean(g1))) - 10
    g2 = ms[(lj > -70) & (lj > rel)]
    return -0.691 + 10 * math.log10(float(np.mean(g2)) + 1e-20)


def _ffmpeg():
    return shutil.which('ffmpeg')


def ffmpeg_loudness(x):
    """ffmpeg loudnorm analysis pass on a (2, n) float array -> (I LUFS, true peak dBTP) or None."""
    ff = _ffmpeg()
    if not ff:
        return None
    raw = np.ascontiguousarray(np.asarray(x, dtype='<f4').T).tobytes()
    cmd = [ff, '-hide_banner', '-nostats', '-f', 'f32le', '-ar', str(SR), '-ac', '2', '-i', 'pipe:0',
           '-af', 'loudnorm=I=-14:TP=-1:LRA=11:print_format=json', '-f', 'null', '-']
    try:
        p = subprocess.run(cmd, input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        txt = p.stderr.decode('utf-8', 'replace')
        j = json.loads(txt[txt.rfind('{'):txt.rfind('}') + 1])
        return float(j['input_i']), float(j['input_tp'])
    except Exception:
        return None


def _master_gain(x, target):
    """Static gain + memoryless soft clip to hit `target` LUFS (loop-safe: no time-varying gain)."""
    g = target - lufs(x)
    xg = x * (10 ** (g / 20.0))
    y = _soft_clip(xg)
    # if the clipper barely touched the signal the loudness is already exact: skip re-measuring
    if float(np.mean(np.abs(xg) > CEILING * 0.72)) < 0.0005:
        ff = ffmpeg_loudness(y) if os.environ.get('GS_MUSIC_FFMPEG_CONFIRM') else None
        return y, g, ff
    for _ in range(2):
        cur = lufs(y)
        if abs(cur - target) < 0.15:
            break
        g += (target - cur)
        y = _soft_clip(x * (10 ** (g / 20.0)))
    ff = ffmpeg_loudness(y) if os.environ.get('GS_MUSIC_FFMPEG_CONFIRM') else None
    return y, g, ff


# =============================================================================
# WAV writer (RIFF + smpl loop chunk + LIST/INFO)
# =============================================================================
def _write_wav(path, x, loop=None, title=None, seed=0):
    os.makedirs(os.path.dirname(os.path.abspath(path)) or '.', exist_ok=True)
    if HAVE_NUMPY:
        x = np.asarray(x, dtype=np.float64)
        r = np.random.default_rng(seed + 7)
        d = (r.random(x.shape) - r.random(x.shape))     # TPDF, +-1 LSB
        pcm = np.clip(np.round(x * 32767.0 + d), -32768, 32767).astype('<i2').T.tobytes()
    else:
        L, R = x
        r = random.Random(seed + 7)
        buf = bytearray()
        pk = struct.Struct('<hh').pack
        for a, b in zip(L, R):
            ia = int(round(a * 32767.0 + r.random() - r.random()))
            ib = int(round(b * 32767.0 + r.random() - r.random()))
            buf += pk(max(-32768, min(32767, ia)), max(-32768, min(32767, ib)))
        pcm = bytes(buf)
    chunks = struct.pack('<4sIHHIIHH', b'fmt ', 16, 1, 2, SR, SR * 4, 4, 16)
    chunks += b'data' + struct.pack('<I', len(pcm)) + pcm
    if loop:
        s, e = int(loop[0]), int(loop[1])
        chunks += struct.pack('<4sI9I', b'smpl', 36 + 24, 0, 0, int(1e9 / SR), 60, 0, 0, 0, 1, 0)
        chunks += struct.pack('<6I', 0, 0, s, e - 1, 0, 0)
    info = b''
    for tag, txt in ((b'ISFT', 'Stark World Engine music_engine %s (procedural, royalty-free)' % VERSION),
                     (b'INAM', title or '')):
        if not txt:
            continue
        bt = txt.encode('ascii', 'replace') + b'\0'
        if len(bt) % 2:
            bt += b'\0'
        info += tag + struct.pack('<I', len(bt)) + bt
    chunks += b'LIST' + struct.pack('<I', 4 + len(info)) + b'INFO' + info
    with open(path, 'wb') as fh:
        fh.write(b'RIFF' + struct.pack('<I', 4 + len(chunks)) + b'WAVE' + chunks)
    return path


def read_wav_loop(path):
    """Return (start, end_exclusive) from a WAV 'smpl' chunk, or None."""
    with open(path, 'rb') as fh:
        data = fh.read()
    i = 12
    while i + 8 <= len(data):
        cid, size = data[i:i + 4], struct.unpack('<I', data[i + 4:i + 8])[0]
        if cid == b'smpl' and size >= 60:
            s, e = struct.unpack('<II', data[i + 8 + 36 + 8:i + 8 + 36 + 16])
            return s, e + 1
        i += 8 + size + (size & 1)
    return None


# =============================================================================
# Rendering pipeline
# =============================================================================
# Mix balance: target level (dB, gated block RMS) per bus and style. Buses are trimmed toward these
# after mixing, so the arrangement always lands balanced whatever the voicing density / tempo.
BUS_TARGET = {
    'adventure': {'drums': -7, 'bass': -9, 'pads': -6, 'arp': -10, 'lead': -6},
    'battle': {'drums': -4, 'bass': -8, 'pads': -8, 'arp': -11, 'lead': -6},
    'synthwave': {'drums': -5, 'bass': -8, 'pads': -7, 'arp': -11, 'lead': -6},
    'chiptune': {'drums': -7, 'bass': -7, 'pads': -10, 'arp': -12, 'lead': -5},
    'ambient': {'drums': -14, 'bass': -11, 'pads': -4, 'arp': -10, 'lead': -8},
    'cozy': {'drums': -6, 'bass': -8, 'pads': -5, 'arp': -10, 'lead': -9},
}


def _bus_level(x):
    m = (x[0, ::4].astype(np.float64) + x[1, ::4]) * 0.5
    blk = int(0.4 * SR / 4)
    nb = len(m) // blk
    if nb < 1:
        return None
    ms = np.mean(m[:nb * blk].reshape(nb, blk) ** 2, axis=1)
    if not np.any(ms > 0):
        return None
    top = np.max(ms)
    g = ms[ms > top * 1e-3]          # ignore blocks > 30 dB below the loudest (silence / tails)
    return 10 * math.log10(float(np.mean(g)) + 1e-20)


def _render_numpy(song, events, w_seconds, want_stems, bus_gains=None, targets=None):
    cfg = song['cfg']
    bpm, bar_s = song['bpm'], song['bar_s']
    W = int(round(w_seconds * SR)) + 1
    buses = _mix_events(events, W)
    if bus_gains is None:
        bus_gains = {}
        tgt = targets or BUS_TARGET[song['style']]
        for b in BUSES:
            lv = _bus_level(buses[b])
            if lv is not None:
                t = tgt[b] - (4.0 if (song['bed'] and b == 'lead') else 0.0)
                if song['bed'] and b == 'drums':
                    t = max(t, -3.0 if song['style'] == 'ambient' else -4.0)   # beds carry an audible pulse
                    t += 2.0 * song.get('bed_boost', 0)
                elif song['bed']:
                    t -= 3.0 * song.get('bed_boost', 0)
                bus_gains[b] = 10 ** (max(-18.0, min(18.0, t - lv)) / 20.0)
    for b, g in bus_gains.items():
        buses[b] *= np.float32(g)
    # sidechain (deterministic, from kick events)
    kicks = [e[0] for e in events if e[2] in KICKS]
    for bus, depth in cfg['sidechain'].items():
        sc = _sidechain(W, kicks, depth, bar_s)
        if sc is not None:
            buses[bus] *= sc
    # lo-fi wow on keys (bar-synced LFO so the loop wraps in phase)
    if song['style'] == 'cozy':
        t = np.arange(W) / SR
        d = 0.0012 * (1 + np.sin(2 * np.pi * t * 2.0 / bar_s)) + 0.00025 * np.sin(2 * np.pi * t * 16.0 / bar_s)
        src = np.arange(W) - d * SR
        for c in range(2):
            buses['pads'][c] = np.interp(src, np.arange(W), buses['pads'][c]).astype(np.float32)
            buses['lead'][c] = np.interp(src, np.arange(W), buses['lead'][c]).astype(np.float32)
    # clean lows on everything that is not bass/kick
    for bus in ('pads', 'arp', 'lead'):
        buses[bus] = _filt(buses[bus], lo=110 if bus != 'pads' else 90, order=2).astype(np.float32)
    t60, pre = cfg['reverb']
    ir = _make_ir(t60, pre, 1234 + int(t60 * 10))
    stems_out = {}
    if want_stems:
        for stem in STEMS:
            members = [b for b in BUSES if BUS_TO_STEM[b] == stem]
            dry = sum(buses[b] for b in members)
            send = sum(buses[b] * cfg['sends'].get(b, 0.0) for b in members)
            wet = _convolve(send, ir).astype(np.float32) if np.any(send) else 0
            dl = 0
            if cfg['delay']:
                beats, fb, dsend = cfg['delay']
                if song['bed'] and bpm < 92:
                    beats = 1.0
                dsrc = sum(buses[b] * dsend.get(b, 0.0) for b in members)
                if np.any(dsrc):
                    dl = _delay(dsrc, beats, fb, bpm)
                    dl = dl + _convolve(dl * 0.3, ir)
            stems_out[stem] = dry + wet + dl
        mix = sum(stems_out.values())
    else:
        send = sum(buses[b] * cfg['sends'].get(b, 0.0) for b in BUSES)
        mix = sum(buses.values()) + _convolve(send, ir)
        if cfg['delay']:
            beats, fb, dsend = cfg['delay']
            if song['bed'] and bpm < 92:
                beats = 1.0
            dsrc = sum(buses[b] * dsend.get(b, 0.0) for b in BUSES)
            dl = _delay(dsrc, beats, fb, bpm)
            mix = mix + dl + _convolve(dl * 0.3, ir)
    # master bus: HP, style colour, glue compression
    mix = _fir_hp(mix, 25.0)
    if cfg['master_lp']:
        mix = _filt(mix, hi=cfg['master_lp'], order=2)
    if song['style'] == 'cozy':
        mix = np.tanh(mix * 1.6) / 1.6
        r = np.random.default_rng(99)
        hiss = _filt(r.standard_normal((2, W)), lo=2000, hi=9000, order=1) * 0.0025
        crack = (r.random((2, W)) > 0.99993) * r.standard_normal((2, W)) * 0.05
        mix = mix + hiss + _filt(crack, lo=800, hi=6000, order=1)
    mix = _bus_comp(mix.astype(np.float32))
    return mix, stems_out, bus_gains


def _assemble(xa, xb, layout):
    """Splice [intro | loop] from timeline A and [outro | ring] from timeline B (see _build_events)."""
    I = int(round(layout['I'] * SR))
    P = int(round(layout['P'] * SR))
    L = int(round(layout['L'] * SR))
    rest = int(round((layout['O'] + max(layout['ring'], 0)) * SR))
    parts = []
    if I > 0:
        xf = min(int(0.02 * SR), I)
        intro = xa[:, :I].copy()
        fo = np.cos(np.linspace(0, np.pi / 2, xf)) ** 2
        intro[:, I - xf:] = intro[:, I - xf:] * fo + xa[:, I + P - xf:I + P] * (1 - fo)
        parts.append(intro)
    parts.append(xa[:, I + P:I + P + L])
    outro = xb[:, P:P + rest].copy()
    xf = min(int(0.02 * SR), outro.shape[1], xa.shape[1] - (I + P + L))
    if xf > 0:
        fi = np.sin(np.linspace(0, np.pi / 2, xf)) ** 2
        outro[:, :xf] = xa[:, I + P + L:I + P + L + xf] * (1 - fi) + outro[:, :xf] * fi
    parts.append(outro)
    out = np.concatenate(parts, axis=1)
    k = int(0.003 * SR)
    out[:, :k] *= np.linspace(0, 1, k)
    fade = int(min(1.0, max(layout['ring'], 0.3)) * SR)
    out[:, -fade:] *= np.linspace(1, 0, fade) ** 2
    return out, (I, I + L)


def compose_track(style='adventure', mood=None, bpm=None, seconds=60, seed=0, key=None, *, stems=False, out=None,
                  loop_file=False, target_lufs=TARGET_LUFS, bed=False, world=None, _bed_boost=0):
    """Render a full track and return its info dict (see module doc)."""
    t_start = time.time()
    if style in WORLD_MOODS and world is None:
        world, style = style, None
    if world:
        w = WORLD_MOODS[world]
        style = style or w['style']
        mood = mood or w['mood']
        bpm = bpm or w['bpm']
        key = key or w['key']
    if style not in STYLE_DEFAULTS:
        raise ValueError('unknown style %r (use %s or a world: %s)' % (style, ', '.join(STYLE_DEFAULTS), ', '.join(WORLD_MOODS)))
    mood = mood or STYLE_DEFAULTS[style]['mood']
    bpm = float(bpm or STYLE_DEFAULTS[style]['bpm'])
    bpm = max(50.0, min(200.0, bpm))
    seconds = float(max(2.0, min(900.0, seconds)))
    song = _plan_song(style, mood, bpm, seconds, seed, key, bed=bed)
    song['bed_boost'] = _bed_boost
    events, layout = _build_events(song)
    base = out or os.path.join(OUT_DIR, '%s_%s_%dbpm_s%s.wav' % (style, song['mood'], int(round(bpm)), seed))
    base = os.path.abspath(base)
    title = '%s / %s / %s / %g bpm / seed %s' % (style, song['mood'], song['key_name'], bpm, seed)
    stems_paths = {}
    if HAVE_NUMPY:
        mix_a, stems_a, bg = _render_numpy(song, events[0], layout['WA'], stems)
        mix_b, stems_b, _ = _render_numpy(song, events[1], layout['WB'], stems, bus_gains=bg)
        final, loop = _assemble(mix_a, mix_b, layout)
        # proof of seamlessness: in timeline A the audio after the loop end (loop head) must equal the loop start
        _i, _p, _l = (int(round(layout[k] * SR)) for k in ('I', 'P', 'L'))
        _h = min(int(0.4 * SR), mix_a.shape[1] - (_i + _p + _l))
        _d = mix_a[:, _i + _p + _l:_i + _p + _l + _h] - mix_a[:, _i + _p:_i + _p + _h]
        _r = float(np.sqrt(np.mean(mix_a[:, _i + _p:_i + _p + _h].astype(np.float64) ** 2))) + 1e-12
        wrap_err_db = round(20 * math.log10(float(np.sqrt(np.mean(_d.astype(np.float64) ** 2))) / _r + 1e-12), 1)
        n_exact = int(round(seconds * SR))
        final = final[:, :n_exact]
        if final.shape[1] < n_exact:
            final = np.concatenate([final, np.zeros((2, n_exact - final.shape[1]))], axis=1)
        y, gain_db, ff = _master_gain(final, target_lufs)
        loop_ok = loop[1] <= n_exact and loop[1] > loop[0]
        _write_wav(base, y, loop if loop_ok else None, title, seed)
        lu = lufs(y)
        peak = float(np.max(np.abs(y)))
        if stems:
            g = 10 ** (gain_db / 20.0)
            for name in stems_a:
                s_fin, _ = _assemble(stems_a[name], stems_b[name], layout)
                s_fin = s_fin[:, :n_exact]
                p = base[:-4] + '_stem_%s.wav' % name
                _write_wav(p, _soft_clip(s_fin * g), loop if loop_ok else None, title + ' / ' + name, seed)
                stems_paths[name] = p
        loop_path = None
        if loop_file and loop_ok:
            loop_path = base[:-4] + '_loop.wav'
            _write_wav(loop_path, y[:, loop[0]:loop[1]], (0, loop[1] - loop[0]), title + ' / loop', seed)
    else:
        y, loop, lu, peak, ff, loop_path = _render_fallback(song, events, layout, base, title, seed, target_lufs)
        loop_ok = loop is not None
    info = {
        'path': base, 'engine': 'music_engine %s (%s)' % (VERSION, 'numpy' if HAVE_NUMPY else 'pure-python draft'),
        'style': style, 'mood': song['mood'], 'world': world, 'key': song['key_name'], 'bpm': bpm, 'seed': seed,
        'seconds': seconds, 'sample_rate': SR, 'channels': 2,
        'progressions': {'A': song['progA'], 'B': song['progB']},
        'structure': {'intro_bars': len(song['intro']), 'loop_bars': len(song['loop']), 'outro_bars': len(song['outro']),
                      'blocks': song['blocks'], 'bar_seconds': round(song['bar_s'], 6)},
        'loop': ({'start_sample': loop[0], 'end_sample': loop[1], 'start_s': round(loop[0] / SR, 6),
                  'end_s': round(loop[1] / SR, 6), 'note': 'end is exclusive; smpl chunk stores end-1'}
                 if loop_ok else None),
        'loop_wrap_error_db': (wrap_err_db if HAVE_NUMPY else None),
        'loop_file': loop_path, 'stems': stems_paths,
        'lufs': (round(lu, 2) if math.isfinite(lu) else None), 'ffmpeg_lufs': (round(ff[0], 2) if ff else None), 'true_peak_dbtp': (round(ff[1], 2) if ff else None),
        'peak_dbfs': round(20 * math.log10(max(peak, 1e-9)), 2), 'target_lufs': target_lufs,
        'render_s': round(time.time() - t_start, 2), 'bed': bed,
        'license': 'Procedurally synthesized, no samples: original and royalty-free for the Stark World Engine buyer.',
    }
    with open(base[:-4] + '.json', 'w', encoding='utf-8') as fh:
        json.dump(info, fh, indent=2)
    if out is None:
        _prune_cache(OUT_DIR, keep=OUT_KEEP, protect=base)
    return info


OUT_KEEP = 40          # renders kept in the default _music_out cache (the studio copies each one into the game)
_CACHE_RX = re.compile(r'^[a-z0-9]+_.+_\d+bpm_.*\.wav$', re.I)


def _prune_cache(folder, keep=OUT_KEEP, protect=None):
    """Bound the default output cache: every soundtrack click with a new world seed renders a new ~8 MB WAV there.
    Only our own renders (+ their .json, stems and loop files) are removed, oldest first; `protect` is kept."""
    try:
        names = os.listdir(folder)
    except OSError:
        return 0
    mains = [n for n in names if _CACHE_RX.match(n) and '_stem_' not in n and not n.endswith(('_loop.wav', '_tmp.wav'))]
    if len(mains) <= keep:
        return 0
    def mtime(n):
        try:
            return os.path.getmtime(os.path.join(folder, n))
        except OSError:
            return 0.0
    mains.sort(key=mtime, reverse=True)
    removed = 0
    for n in mains[keep:]:
        stem = n[:-4]
        if protect and os.path.normcase(os.path.join(folder, n)) == os.path.normcase(os.path.abspath(protect)):
            continue
        for m in names:
            if m == n or m == stem + '.json' or m == stem + '_loop.wav' or (m.startswith(stem + '_stem_') and m.endswith('.wav')):
                try:
                    os.remove(os.path.join(folder, m))
                    removed += 1
                except OSError:
                    pass
    return removed


def compose(style, mood=None, bpm=None, seconds=60, seed=0, key=None, **kw):
    """Render an original track; returns the WAV path (sidecar JSON next to it)."""
    return compose_track(style, mood, bpm, seconds, seed, key, **kw)['path']


def compose_world(world, seconds=60, seed=0, loop_only=True, **kw):
    """World soundtrack. loop_only=True (default) returns the seamless loop region only: bar-aligned start and end,
    no intro, no outro fade, sample-exact wrap, so a player looping the file never dips (QA-026)."""
    if world not in WORLD_MOODS:
        raise ValueError('unknown world mood %r (use %s)' % (world, ', '.join(WORLD_MOODS)))
    style = kw.pop('style', None)
    total = seconds
    if loop_only:                      # QA-029: the loop itself is at least `seconds` long (rounded UP to whole phrases)
        w = WORLD_MOODS[world]
        st = style or w['style']
        bar_s = 240.0 / float(kw.get('bpm') or w['bpm'])
        tail_s = max(1.5, min(4.0, STYLE_CFG[st]['reverb'][0] * 0.6))
        total = float(seconds)
        while _plan_bars(total, bar_s, tail_s)[1] * bar_s < seconds - 1e-6:
            total += bar_s
        total = min(900.0, total)
    info = compose_track(world=world, style=style, seconds=total, seed=seed,
                         loop_file=loop_only or kw.pop('loop_file', False), **kw)
    if loop_only and info.get('loop_file') and os.path.isfile(info['loop_file']):
        lp = info['loop']
        meta = {'seconds': round((lp['end_sample'] - lp['start_sample']) / float(SR), 3), 'requested': seconds,
                'bars': info['structure']['loop_bars'], 'bpm': info['bpm'], 'loop': True, 'source': info['path']}
        with open(info['loop_file'][:-4] + '.json', 'w', encoding='utf-8') as fh:
            json.dump(meta, fh, indent=1)
        return info['loop_file']
    return info['path']


def soundtrack_seconds(path):
    """Actual length of a rendered soundtrack (reads its sidecar, else the WAV header) for the studio UI."""
    side = dict()
    try:
        with open(path[:-4] + '.json', encoding='utf-8') as fh:
            side = json.load(fh)
    except (OSError, ValueError):
        pass
    if side.get('seconds'):
        return float(side['seconds'])
    import wave
    with wave.open(path, 'rb') as wv:
        return wv.getnframes() / float(wv.getframerate())


def background_bed(duration, bpm, style='synthwave', seed=0, mood=None, out=None, target_lufs=TARGET_LUFS,
                   check=True):
    """Beat-locked music bed of exactly `duration` seconds at exactly `bpm` for the video editor.

    The bed carries a steady quarter-note pulse. With check=True (default) the result is run through
    beat_engine.analyze(); if the detected tempo is off by > 2 % (and is not a pure analyzer octave
    ambiguity that a stronger pulse cannot fix) it is re-rendered with a stronger pulse (max 2 retries).
    The sidecar JSON gets 'beat_check': {requested, detected, attempts, match, octave}.
    """
    if style in WORLD_MOODS:
        w = WORLD_MOODS[style]
        style, mood = w['style'], mood or w['mood']
    if out is None:
        out = os.path.join(OUT_DIR, 'bed_%s_%dbpm_%ds_s%s.wav' % (style, int(round(bpm)), int(round(duration)), seed))
    be = None
    if check:
        try:
            sys.path.insert(0, ROOT)
            import beat_engine as be
        except Exception:
            be = None
    best = None
    for attempt in range(3 if be else 1):
        info = compose_track(style, mood, bpm, duration, seed, None, out=out, bed=True, target_lufs=target_lufs,
                             _bed_boost=attempt)
        if not be:
            break
        got = float(be.analyze(info['path'])['tempo_bpm'])
        err = abs(got - bpm) / float(bpm)
        octave = any(abs(got - bpm * r) / (bpm * r) < 0.02 for r in (0.5, 2.0))
        bc = {'requested': bpm, 'detected': round(got, 2), 'attempts': attempt + 1, 'match': err < 0.02,
              'octave': octave, 'analyzer': 'beat_engine.analyze'}
        if best is None or err < best[0]:
            best = (err, attempt, bc)
        if err < 0.02:
            break
    if be and best:
        if best[1] != attempt:      # re-render the best attempt (files are overwritten per attempt)
            info = compose_track(style, mood, bpm, duration, seed, None, out=out, bed=True, target_lufs=target_lufs,
                                 _bed_boost=best[1])
        side = info['path'][:-4] + '.json'
        info['beat_check'] = best[2]
        with open(side, 'w', encoding='utf-8') as fh:
            json.dump(info, fh, indent=2)
    return info['path']


# =============================================================================
# Pure-Python draft renderer (no numpy): same composition, simple voices
# =============================================================================
SR_FB = 24000


def _render_fallback(song, events, layout, path, title, seed, target):
    LA, RA = _fb_timeline(events[0], layout['WA'], seed)
    LB, RB = _fb_timeline(events[1], layout['WB'], seed + 1)
    I = int(layout['I'] * SR_FB)
    P = int(layout['P'] * SR_FB)
    Ln = int(layout['L'] * SR_FB)
    n_exact = int(song['seconds'] * SR_FB)
    rest = n_exact - I - Ln
    outL = LA[:I] + LA[I + P:I + P + Ln] + LB[P:P + rest]
    outR = RA[:I] + RA[I + P:I + P + Ln] + RB[P:P + rest]
    return _fb_finish(outL, outR, n_exact, I, Ln, path, title, seed, target)


def _fb_timeline(events, w_seconds, seed):
    n_total = int(w_seconds * SR_FB) + 1
    L = [0.0] * n_total
    R = [0.0] * n_total
    rnd = random.Random(seed)
    tw = 2 * math.pi
    for ev in events:
        t, bus, inst, midi, dur, vel, pan = ev[:7]
        i0 = int(t * SR_FB)
        amp = (max(0.0, vel) ** 1.3) * INST_GAIN.get(inst, 0.3) * 0.8
        th = (max(-1.0, min(1.0, pan)) + 1) * math.pi / 4
        gl, gr = math.cos(th) * 1.41, math.sin(th) * 1.41
        if midi is not None and inst not in DRUM_INSTS and inst != 'timp':
            f = mtof(midi)
            rel = 0.3 if inst not in ('warm_pad', 'sub_drone') else 1.5
            n = int((dur + rel) * SR_FB)
            att = 0.01 if inst not in ('warm_pad', 'strings', 'supersaw', 'sub_drone', 'lowstr') else 0.3
            hs = (1.0, 0.4, 0.2) if inst not in ('bell', 'vibes', 'harp', 'pizz_bass', 'round_bass', 'sub_drone') else (1.0, 0.15, 0.05)
            plucky = inst in ('harp', 'pizz_bass', 'bell', 'vibes', 'keys', 'arp_saw', 'chip_arp')
            for k in range(n):
                i = i0 + k
                if i < 0:
                    continue
                if i >= n_total:
                    break
                tt = k / SR_FB
                e = min(1.0, tt / att)
                e *= math.exp(-tt / 0.6) if plucky else (1.0 if tt < dur else math.exp(-(tt - dur) / (rel / 4)))
                s = (hs[0] * math.sin(tw * f * tt) + hs[1] * math.sin(tw * 2 * f * tt) + hs[2] * math.sin(tw * 3 * f * tt)) * e * amp
                L[i] += s * gl
                R[i] += s * gr
        else:
            ln = int(0.4 * SR_FB)
            for k in range(ln):
                i = i0 + k
                if i < 0:
                    continue
                if i >= n_total:
                    break
                tt = k / SR_FB
                if inst in KICKS or inst in ('taiko', 'tom_l', 'tom_m', 'tom_h', 'timp'):
                    f = 50 + 100 * math.exp(-tt / 0.035)
                    s = math.sin(tw * f * tt) * math.exp(-tt / 0.25)
                elif inst in ('snare', 'snare_lofi', 'clap', 'csnare', 'rim'):
                    s = (rnd.uniform(-1, 1) * 0.7 + 0.4 * math.sin(tw * 190 * tt)) * math.exp(-tt / 0.1)
                else:
                    s = rnd.uniform(-1, 1) * math.exp(-tt / (0.6 if inst in ('crash', 'swell') else 0.03)) * 0.6
                s *= amp
                L[i] += s * gl
                R[i] += s * gr
    return L, R


def _fb_finish(outL, outR, n_exact, I, Ln, path, title, seed, target):
    outL += [0.0] * (n_exact - len(outL))
    outR += [0.0] * (n_exact - len(outR))
    # 2x upsample (linear) to 48 kHz
    def up(v):
        o = []
        for a, b in zip(v, v[1:] + [v[-1]]):
            o.append(a)
            o.append((a + b) * 0.5)
        return o
    L2, R2 = up(outL), up(outR)
    pk = max(1e-9, max(max(abs(v) for v in L2), max(abs(v) for v in R2)))
    g = 0.5 / pk
    # loudness: ffmpeg measure (if present) on the peak-normalized draft
    ff = None
    try:
        tmp = path[:-4] + '_tmp.wav'
        _write_wav(tmp, ([v * g for v in L2], [v * g for v in R2]), None, title, seed)
        ffm = _ffmpeg()
        if ffm:
            p = subprocess.run([ffm, '-hide_banner', '-nostats', '-i', tmp, '-af', 'loudnorm=print_format=json', '-f', 'null', '-'],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            txt = p.stderr.decode('utf-8', 'replace')
            j = json.loads(txt[txt.rfind('{'):txt.rfind('}') + 1])
            g *= 10 ** ((target - float(j['input_i'])) / 20.0)
        os.remove(tmp)
    except Exception:
        pass

    def clip(v):
        k = CEILING * 0.72
        a = abs(v)
        if a <= k:
            return v
        return math.copysign(k + (CEILING - k) * math.tanh((a - k) / (CEILING - k)), v)
    L3 = [clip(v * g) for v in L2]
    R3 = [clip(v * g) for v in R2]
    loop = (2 * I, 2 * (I + Ln))
    _write_wav(path, (L3, R3), loop, title, seed)
    try:
        ffm = _ffmpeg()
        p = subprocess.run([ffm, '-hide_banner', '-nostats', '-i', path, '-af', 'loudnorm=print_format=json', '-f', 'null', '-'],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        txt = p.stderr.decode('utf-8', 'replace')
        j = json.loads(txt[txt.rfind('{'):txt.rfind('}') + 1])
        ff = (float(j['input_i']), float(j['input_tp']))
    except Exception:
        ff = None
    peak = max(max(abs(v) for v in L3), max(abs(v) for v in R3))
    return None, loop, (ff[0] if ff else float('nan')), peak, ff, None


# =============================================================================
# SFX
# =============================================================================
SFX_KINDS = ['jump', 'coin', 'pickup', 'hit', 'explosion', 'powerup', 'door', 'footstep', 'ui_click',
             'level_complete', 'boss_roar']
SFX_ALIASES = {'click': 'ui_click', 'ui': 'ui_click', 'jingle': 'level_complete', 'roar': 'boss_roar',
               'step': 'footstep', 'boom': 'explosion', 'power_up': 'powerup'}


def list_sfx():
    return list(SFX_KINDS)


def _env_exp(n, a, d):
    t = np.arange(n) / SR
    return np.minimum(1, t / max(a, 1e-5)) * np.exp(-np.maximum(t - a, 0) / d)


def _place(dst, src, at, gain=1.0, pan=0.0):
    i0 = int(at * SR)
    if src.ndim == 1:
        th = (pan + 1) * math.pi / 4
        src = np.vstack([src * math.cos(th) * 1.41, src * math.sin(th) * 1.41])
    e = min(dst.shape[1], i0 + src.shape[1])
    if e > i0:
        dst[:, i0:e] += src[:, :e - i0] * gain


def _sfx_render(kind, seed):
    rng = np.random.default_rng(seed * 7919 + SFX_KINDS.index(kind) * 104729 if kind in SFX_KINDS else seed)
    pr = random.Random('%s|%s' % (kind, seed))
    jit = 2 ** (pr.uniform(-1.5, 1.5) / 12.0)

    def T(sec):
        n = int(sec * SR)
        return n, np.arange(n) / SR

    if kind == 'jump':
        n, t = T(0.32)
        f = 180 * jit * np.exp(np.log(4.2) * np.minimum(t / 0.16, 1.0) ** 0.7) * (1 + 0.02 * np.sin(2 * np.pi * 28 * t))
        tab = _table('pulse25', 800, 9000)
        body = _osc(tab, f, n) * _env_exp(n, 0.004, 0.09)
        sub = np.sin(2 * np.pi * np.cumsum(f * 0.5) / SR) * _env_exp(n, 0.003, 0.06) * 0.6
        wh = _filt(rng.standard_normal(n), lo=1500, hi=6000) * _env_exp(n, 0.02, 0.05) * 0.25
        y = _stereo(body + sub + wh, 18)
        return y, 0.12
    if kind in ('coin', 'pickup'):
        n, t = T(0.6)
        notes = (83, 88) if kind == 'coin' else (79, 86)
        out = np.zeros((2, n))
        for j, m in enumerate(notes):
            f = mtof(m) * jit
            st = 0.0 if j == 0 else 0.065
            nn = n - int(st * SR)
            tt = np.arange(nn) / SR
            tab = _table('square', f, 12000)
            v = _osc(tab, f, nn) * 0.5 + np.sin(2 * np.pi * f * tt + 1.5 * np.exp(-tt / 0.08) * np.sin(2 * np.pi * f * 3 * tt)) * 0.6
            env = _env_exp(nn, 0.001, 0.07 if j == 0 else 0.22)
            _place(out, v * env, st, 1.0, -0.15 if j == 0 else 0.15)
        sp = _filt(rng.standard_normal(n), lo=7000) * _env_exp(n, 0.001, 0.03) * 0.15
        out += sp
        return out, 0.18
    if kind == 'hit':
        n, t = T(0.35)
        f = 40 + 110 * jit * np.exp(-t / 0.03)
        thump = np.sin(2 * np.pi * np.cumsum(f) / SR) * _env_exp(n, 0.001, 0.09)
        crack = _filt(rng.standard_normal(n), lo=900, hi=5000) * _env_exp(n, 0.0005, 0.018)
        crunch = _filt(rng.standard_normal(n), lo=200, hi=1800) * _env_exp(n, 0.001, 0.05) * 0.5
        y = np.tanh(2.5 * (thump + 0.8 * crack + crunch))
        return _stereo(y, 12), 0.1
    if kind == 'explosion':
        n, t = T(2.6)
        out = np.zeros((2, n))
        for c in range(2):
            nz = rng.standard_normal(n)
            brown = np.cumsum(nz)
            brown = _filt(brown - _moving(brown, 4800), lo=20, hi=400)
            brown /= np.max(np.abs(brown)) + 1e-9
            # body: noise with a falling cutoff, done as a crossfade of 4 filtered copies
            bands = [_filt(nz, hi=fc) for fc in (4000, 1600, 600, 220)]
            body = np.zeros(n)
            pos = np.clip(t / 1.4, 0, 1) * 3
            for k in range(4):
                w = np.clip(1 - np.abs(pos - k), 0, 1)
                body += bands[k] / (np.std(bands[k]) + 1e-9) * w
            crack = _filt(nz, lo=1000) * _env_exp(n, 0.0005, 0.03)
            boom = np.sin(2 * np.pi * np.cumsum(30 + 40 * jit * np.exp(-t / 0.25)) / SR) * _env_exp(n, 0.003, 0.6)
            deb = (rng.random(n) > 0.9993) * rng.standard_normal(n) * np.exp(-t / 0.8) * (t > 0.15)
            deb = _filt(deb, lo=1500, hi=8000) * 4
            y = (0.35 * body * _env_exp(n, 0.004, 0.55) + 0.5 * crack + 1.1 * boom + 0.6 * brown * _env_exp(n, 0.01, 0.9) + deb)
            out[c] = y
        out = np.tanh(1.6 * out)
        return out, 0.28
    if kind == 'powerup':
        n, t = T(0.95)
        out = np.zeros((2, n))
        base = 60 + pr.choice([0, 2, 5])
        seq = [0, 4, 7, 12, 16, 19, 24]
        for j, iv in enumerate(seq):
            st = j * 0.06
            f = mtof(base + iv)
            nn = int(0.3 * SR)
            tt = np.arange(nn) / SR
            v = _osc(_table('pulse25', f, 11000), f * (1 + 0.01 * np.sin(2 * np.pi * 9 * tt)), nn) * _env_exp(nn, 0.002, 0.09)
            _place(out, v, st, 0.8, 0.5 * math.sin(j))
        sweep = 300 * np.exp(np.log(8) * t / 0.95)
        out += _stereo(_osc(_table('saw', 300, 6000), sweep, n) * _env_exp(n, 0.3, 0.4) * 0.18 * (1 + 0.5 * np.sin(2 * np.pi * 14 * t)), 30)
        sh = _filt(rng.standard_normal(n), lo=6000) * np.clip(t / 0.9, 0, 1) * np.exp(-np.maximum(t - 0.7, 0) / 0.08) * 0.12
        out += sh
        return out, 0.25
    if kind == 'door':
        n, t = T(1.5)
        out = np.zeros((2, n))
        # creak: stick-slip friction = jittery pulse train through body resonances
        cr_n = int(0.85 * SR)
        tc = np.arange(cr_n) / SR
        f = (95 + 45 * np.sin(np.pi * tc / 0.85) + 12 * np.cumsum(rng.standard_normal(cr_n)) / np.sqrt(SR) * 8) * jit
        f = np.clip(f, 50, 260)
        pulses = _osc(_table('pulse12', 100, 5000), f, cr_n)
        am = np.clip(0.6 + 0.6 * _filt(rng.standard_normal(cr_n), hi=25), 0, 1.5)
        creak = _filt(pulses * am, lo=250, hi=4500, peak=((700, 9, 3), (1500, 6, 4), (2600, 5, 5)))
        creak *= np.sin(np.pi * np.clip(tc / 0.85, 0, 1)) ** 0.6
        _place(out, creak * 0.5, 0.0, 1.0, -0.2)
        # thud + latch
        tn, tt = T(0.5)
        thud = np.sin(2 * np.pi * np.cumsum(70 + 50 * np.exp(-tt / 0.02)) / SR) * _env_exp(tn, 0.001, 0.12)
        thud += _filt(rng.standard_normal(tn), hi=900) * _env_exp(tn, 0.001, 0.04) * 0.6
        _place(out, np.tanh(2 * thud), 0.9, 1.0, 0.0)
        ln, lt = T(0.1)
        latch = (np.sin(2 * np.pi * 2300 * lt) + 0.6 * np.sin(2 * np.pi * 3700 * lt)) * _env_exp(ln, 0.0003, 0.008)
        _place(out, latch * 0.5, 0.93, 1.0, 0.25)
        return out, 0.2
    if kind == 'footstep':
        n, t = T(0.28)
        heel = _filt(rng.standard_normal(n), lo=60, hi=900 * jit) * _env_exp(n, 0.001, 0.025)
        thump = np.sin(2 * np.pi * np.cumsum(60 + 60 * np.exp(-t / 0.01)) / SR) * _env_exp(n, 0.001, 0.03)
        sc_t = 0.035 + pr.uniform(0, 0.02)
        scuff = _filt(rng.standard_normal(n), lo=1200, hi=6000) * np.exp(-np.maximum(t - sc_t, 0) / 0.03) * (t >= sc_t) * 0.35
        grit = (rng.random(n) > 0.995) * rng.standard_normal(n) * np.exp(-t / 0.06) * 0.4
        y = heel * 1.2 + thump * 0.7 + scuff + _filt(grit, lo=2000)
        return _stereo(y, 6), 0.06
    if kind == 'ui_click':
        n, t = T(0.09)
        f = 1900 * jit
        y = np.sin(2 * np.pi * f * t) * _env_exp(n, 0.0005, 0.008) + 0.5 * np.sin(2 * np.pi * f * 0.5 * t) * _env_exp(n, 0.001, 0.015)
        y += _filt(rng.standard_normal(n), lo=4000) * _env_exp(n, 0.0002, 0.002) * 0.4
        return _stereo(y, 0), 0.0
    if kind == 'level_complete':
        bpm = 132.0
        q = 60.0 / bpm
        n, t = T(3.2)
        out = np.zeros((2, n))
        root = 60 + pr.choice([0, 2, 5, 7])
        mel = [(0, 0.5, 0), (0.5, 0.5, 4), (1.0, 0.5, 7), (1.5, 1.0, 12), (2.5, 0.5, 11), (3.0, 3.0, 12)]
        for st, ln, iv in mel:
            m = root + 12 + iv
            v = _i_saw_lead(m, ln * q * 0.9, 0.9, rng).astype(np.float64)
            _place(out, v * 0.45, st * q)
            b = _i_bell(m + 12, 0.3, 0.8, rng)
            _place(out, b * 0.25, st * q, 1.0, 0.3)
        for iv in (0, 4, 7, 12):
            p = _i_supersaw(root + iv, 3.0 * q, 0.8, rng)
            _place(out, p * 0.3, 3.0 * q)
            h = _i_harp(root + iv + 12, 0.4, 0.9, rng)
            _place(out, h * 0.35, 3.0 * q + 0.03 * iv / 4, 1.0, -0.3 + 0.2 * iv / 4)
        tim = _d_timp(root - 24 if root - 24 >= 36 else root - 12, 1.0, rng)
        _place(out, tim * 0.7, 3.0 * q)
        cr = _d_crash('crash', 2.0, rng)
        _place(out, cr * 0.25, 3.0 * q)
        return out, 0.3
    if kind == 'boss_roar':
        n, t = T(2.6)
        out = np.zeros((2, n))
        shape = np.clip(t / 0.35, 0, 1) ** 0.8 * np.exp(-np.maximum(t - 1.3, 0) / 0.45)
        f = (52 + 28 * np.sin(np.pi * np.clip(t / 1.8, 0, 1)) - 15 * np.clip((t - 1.4) / 1.0, 0, 1)) * jit
        for c in range(2):
            rough = 1 + 0.55 * _filt(rng.standard_normal(n), lo=15, hi=70)
            jitter = 1 + 0.02 * _filt(rng.standard_normal(n), hi=30)
            g = _osc(_table('saw', 60, 5000), f * jitter * (1 + 0.004 * c), n, rng.random()) * rough
            g += 0.5 * _osc(_table('square', 60, 3000), f * 0.5 * jitter, n)          # subharmonic growl
            breath = _filt(rng.standard_normal(n), lo=300, hi=5000) * 0.6
            v = (g + breath) * shape
            v = _filt(v, lo=40, peak=((420, 12, 2.5), (950, 9, 3), (2400, 6, 4)))
            v = np.tanh(2.2 * v / (np.max(np.abs(v)) + 1e-9))
            out[c] = v
        sub = np.sin(2 * np.pi * np.cumsum(f * 0.5) / SR) * shape * 0.5
        out += sub
        return out, 0.35
    raise ValueError('unknown sfx %r (use %s)' % (kind, ', '.join(SFX_KINDS)))


def sfx(kind, seed=0, out=None):
    """Render one layered, synthesized sound effect -> WAV path (48 kHz stereo, peak -1 dBFS)."""
    kind = SFX_ALIASES.get(kind, kind)
    if kind not in SFX_KINDS:
        raise ValueError('unknown sfx %r (use %s)' % (kind, ', '.join(SFX_KINDS)))
    out = os.path.abspath(out or os.path.join(OUT_DIR, 'sfx_%s_s%s.wav' % (kind, seed)))
    if not HAVE_NUMPY:
        return _sfx_fallback(kind, seed, out)
    y, verb = _sfx_render(kind, seed)
    y = np.asarray(y, dtype=np.float64)
    if verb > 0:
        ir = _make_ir(1.2 if kind not in ('explosion', 'boss_roar') else 2.2, 0.01, 77)
        tail = int(ir.shape[1] * 0.6)
        y = np.concatenate([y, np.zeros((2, tail))], axis=1)
        y = y + _convolve(y * verb, ir)
        # trim the inaudible end of the tail
        env = np.max(np.abs(y), axis=0)
        thr = np.max(env) * 10 ** (-60 / 20.0)
        last = int(np.nonzero(env > thr)[0][-1]) + 1
        y = y[:, :max(last, int(0.05 * SR))]
    y = _filt(y, lo=25)
    k = min(int(0.02 * SR), y.shape[1] // 4)
    y[:, -k:] *= np.linspace(1, 0, k)
    y[:, :32] *= np.linspace(0, 1, 32)
    y = y / (np.max(np.abs(y)) + 1e-12) * CEILING * 0.98
    _write_wav(out, y, None, 'sfx %s seed %s' % (kind, seed), seed)
    return out


def _sfx_fallback(kind, seed, out):
    r = random.Random('%s%s' % (kind, seed))
    dur = {'explosion': 1.8, 'boss_roar': 2.2, 'level_complete': 2.0, 'door': 1.2, 'powerup': 0.9}.get(kind, 0.35)
    n = int(dur * SR)
    L = []
    for i in range(n):
        t = i / SR
        if kind in ('explosion', 'hit', 'footstep', 'door'):
            v = r.uniform(-1, 1) * math.exp(-t / (dur * 0.3)) + math.sin(2 * math.pi * (50 + 80 * math.exp(-t / 0.05)) * t) * math.exp(-t / 0.3)
        elif kind == 'boss_roar':
            v = (((t * 55) % 1.0) * 2 - 1) * math.sin(math.pi * t / dur) * (0.7 + 0.3 * r.random())
        else:
            f = 600 * (1 + 2 * t / dur) if kind in ('jump', 'powerup') else 988 if t < 0.07 else 1319
            v = math.sin(2 * math.pi * f * t) * math.exp(-t / (dur * 0.3))
        L.append(v)
    pk = max(abs(v) for v in L) or 1.0
    L = [v / pk * CEILING * 0.98 for v in L]
    return _write_wav(out, (L, L), None, 'sfx %s' % kind, seed)


# =============================================================================
# Adaptive score (v1.1): mood cues with layered seamless stems, stingers, leitmotifs
# =============================================================================
# One score = one tonic + one tempo shared by every mood state, so the runtime can cross-fade on beats and bars
# with the groove intact. Each state re-harmonises and re-orchestrates the region's leitmotif.
SCORE_STATES = ('explore', 'tension', 'combat', 'victory', 'town', 'night')
STINGERS = ('combat_start', 'boss', 'quest_complete', 'discovery', 'danger', 'defeat', 'level_up')
PRESETS = ('orchestral', 'synthwave', 'folk', 'ambient', 'chiptune')
MAJORISH = ('major', 'lydian', 'mixolydian')


# ---- extra instruments ----------------------------------------------------------------------------------------
def _i_str_trem(midi, dur, vel, rng):
    y = _i_strings(midi, dur, vel, rng)
    t = np.arange(y.shape[1]) / SR
    rate = 12.5 + rng.random() * 1.5
    trem = 0.5 + 0.5 * np.abs(np.sin(np.pi * rate * t + rng.random() * 3))
    return y * trem


def _i_celesta(midi, dur, vel, rng):
    return _i_bell(midi, dur, vel, rng, ratio=4.0, idx=1.1, dec=1.6)


def _i_fiddle(midi, dur, vel, rng):
    f0 = mtof(midi)
    env = _adsr(dur, 0.035, 0.25, 0.85, 0.12)
    n = len(env)
    vib = _vib(n, 5.8, 0.0055, 0.18)
    y = _osc(_table('saw', f0, 7000), f0 * vib, n, rng.random())
    bow = _filt(rng.standard_normal(n), lo=2000, hi=8000) * 0.04
    y = _filt(y + bow, lo=180, peak=((280, 6, 2), (450, 4, 3), (1000, 3, 2), (2800, 5, 1.5), (5000, -8, 1)))
    return y * env * 0.9


def _i_whistle(midi, dur, vel, rng):
    f0 = mtof(midi)
    env = _adsr(dur, 0.02, 0.2, 0.9, 0.08)
    n = len(env)
    t = np.arange(n) / SR
    scoop = 1 - 0.06 * np.exp(-t / 0.03)
    vib = _vib(n, 5.5, 0.004, 0.3)
    y = _osc(_table('soft', f0, 9000), f0 * scoop * vib, n, rng.random()) * 1.3
    y += 0.1 * np.sin(2 * np.pi * 2 * f0 * t)
    br = _filt(rng.standard_normal(n), lo=2500, hi=9000) * 0.06
    return (y + br) * env


def _i_guitar(midi, dur, vel, rng):
    f0 = mtof(midi)
    t60 = max(0.8, 2.6 - (midi - 40) * 0.04)
    n = int(min(t60 * 0.8 + 0.05, max(dur, 0.2) + 1.0) * SR)
    y = _ks(f0, n, t60, 0.45 + 0.35 * vel, rng)
    y = _filt(y, lo=70, peak=((110, 4, 2), (220, 3, 2), (2500, 2, 1)))
    t = np.arange(n) / SR
    y *= np.minimum(1, t / 0.0015)
    rel = np.clip((dur + 0.25 - t) / 0.25, 0, 1)
    y = y * (0.25 + 0.75 * rel)
    y[-200:] *= np.linspace(1, 0, 200)
    return y


def _i_mandolin(midi, dur, vel, rng):
    f0 = mtof(midi)
    n = int((dur + 0.35) * SR)
    out = np.zeros(n)
    rate = 13.0
    k = max(1, int(dur * rate))
    m = int(0.35 * SR)
    for j in range(k):
        i0 = int(j / rate * SR)
        if i0 >= n:
            break
        pl = _ks(f0 * (1 + 0.002 * rng.standard_normal()), m, 0.5, 0.8, rng) * (1.0 if j % 2 == 0 else 0.7)
        e = min(n, i0 + m)
        out[i0:e] += pl[:e - i0] * np.minimum(1, np.arange(e - i0) / 40.0)
    return _filt(out, lo=150, peak=((900, 3, 2),)) * 0.5


def _i_reed(midi, dur, vel, rng):
    f0 = mtof(midi)
    env = _adsr(dur, 0.06, 0.3, 0.9, 0.15)
    n = len(env)
    t = np.arange(n) / SR
    bellows = 1 + 0.06 * np.sin(2 * np.pi * 0.7 * t + rng.random() * 6)
    L = 0.6 * _osc(_table('square', f0, 3500), f0 * 2 ** (-7 / 1200.0), n, rng.random())
    R = 0.6 * _osc(_table('saw', f0, 3000), f0 * 2 ** (7 / 1200.0), n, rng.random())
    out = np.vstack([L + 0.4 * R, R + 0.4 * L])
    return _filt(out, lo=150, hi=5000, peak=((1200, 3, 1.5),)) * env * bellows * 0.8


def _i_choir(midi, dur, vel, rng):
    f0 = mtof(midi)
    env = _adsr(dur, 0.45, 0.6, 0.9, 0.7, curve=0.8)
    n = len(env)
    vib = _vib(n, 4.6, 0.004, 0.4)
    tab = _table('saw', f0, 4500)
    out = np.zeros((2, n))
    for j, c in enumerate((-8, -3, 3, 8)):
        out[j % 2] += _osc(tab, f0 * 2 ** (c / 1200.0) * vib, n, rng.random())
    out += _filt(rng.standard_normal((2, n)), lo=800, hi=5000) * 0.05
    out = _filt(out, lo=120, hi=4800, peak=((700, 10, 4), (1150, 8, 5), (2800, 6, 6)))
    return out * env * 0.35


def _i_braam(midi, dur, vel, rng):
    f0 = mtof(midi)
    env = _adsr(dur, 0.1, 0.9, 0.75, 0.9)
    n = len(env)
    t = np.arange(n) / SR
    br = np.clip(t / 0.25, 0, 1) * np.exp(-np.maximum(t - 0.4, 0) / 1.2)
    out = np.zeros((2, n))
    for r, g in ((1.0, 1.0), (1.5, 0.6), (2.0, 0.5)):
        dark = _table('saw', f0 * r, f0 * r * 2.0)
        bright = _table('saw', f0 * r, min(6000, f0 * r * 10))
        for j, c in enumerate((-9, 9)):
            out[j] += g * _osc_xf(dark, bright, f0 * r * 2 ** (c / 1200.0), n, br, rng.random())
    return np.tanh(1.6 * out * env) * 0.6


def _d_frame(kind, vel, rng):
    hi = kind == 'frame_hi'
    n = int((0.35 if not hi else 0.15) * SR)
    t = np.arange(n) / SR
    f = (85 if not hi else 230) * (1 + 0.5 * np.exp(-t / 0.02))
    y = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / (0.16 if not hi else 0.05))
    y += _filt(rng.standard_normal(n), lo=200, hi=2600 if not hi else 4500) * np.exp(-t / 0.025) * (0.35 if not hi else 0.6)
    y = np.tanh(1.4 * y) * np.minimum(1, t / 0.001)
    y[-64:] *= np.linspace(1, 0, 64)
    return y


def _d_tamb(kind, vel, rng):
    n = int(0.2 * SR)
    t = np.arange(n) / SR
    y = np.zeros(n)
    for _ in range(14):
        i0 = int(rng.uniform(0, 0.03) * SR)
        m = n - i0
        f = rng.uniform(5000, 9500)
        y[i0:] += np.sin(2 * np.pi * f * t[:m] + rng.random() * 6) * np.exp(-t[:m] / rng.uniform(0.03, 0.08))
    y = _filt(y + 0.4 * rng.standard_normal(n) * np.exp(-t / 0.04), lo=4000) / 6.0
    y[-64:] *= np.linspace(1, 0, 64)
    return y


def _d_heart(kind, vel, rng):
    n = int(0.6 * SR)
    t = np.arange(n) / SR
    y = np.zeros(n)
    for t0, a in ((0.0, 1.0), (0.24, 0.7)):
        tt = np.maximum(t - t0, 0)
        y += a * np.sin(2 * np.pi * np.cumsum(48 + 30 * np.exp(-tt / 0.02)) / SR) * np.exp(-tt / 0.09) * (t >= t0)
    y = _filt(y, hi=180)
    y[-64:] *= np.linspace(1, 0, 64)
    return y


def _d_boom(kind, vel, rng):
    n = int(2.2 * SR)
    t = np.arange(n) / SR
    y = np.sin(2 * np.pi * np.cumsum(38 + 60 * np.exp(-t / 0.05)) / SR) * np.exp(-t / 0.6)
    y += _filt(rng.standard_normal(n), lo=60, hi=1500) * np.exp(-t / 0.08) * 0.5
    y = np.tanh(1.5 * y)
    y[-256:] *= np.linspace(1, 0, 256)
    return y


PITCHED.update({'str_trem': _i_str_trem, 'celesta': _i_celesta, 'fiddle': _i_fiddle, 'whistle': _i_whistle,
                'guitar': _i_guitar, 'mandolin': _i_mandolin, 'reed': _i_reed, 'choir': _i_choir, 'braam': _i_braam})
_EXTRA_DRUMS = {'frame': _d_frame, 'frame_hi': _d_frame, 'tamb': _d_tamb, 'heart': _d_heart, 'boom': _d_boom}
DRUM_INSTS.update(_EXTRA_DRUMS)
INST_GAIN.update({'str_trem': 0.13, 'celesta': 0.2, 'fiddle': 0.26, 'whistle': 0.22, 'guitar': 0.3, 'mandolin': 0.22,
                  'reed': 0.11, 'choir': 0.2, 'braam': 0.2, 'frame': 0.7, 'frame_hi': 0.4, 'tamb': 0.22,
                  'heart': 0.9, 'boom': 0.7})
_render_note_base = _render_note


def _render_note(inst, midi, dur, vel, rr=0):          # noqa: F811 - extends the dispatcher with the new drums
    fn = _EXTRA_DRUMS.get(inst)
    if fn is None:
        return _render_note_base(inst, midi, dur, vel, rr)
    vb = round(min(1.0, max(0.0, vel)) * 4) / 4.0
    key = (inst, midi, round(dur, 3), vb, rr)
    buf = _NOTE_CACHE.get(key)
    if buf is None:
        buf = fn(inst, vb, _rng_for(inst, midi, round(dur, 3), vb, rr)).astype(np.float32)
        _NOTE_CACHE[key] = buf
    return buf


# ---- functional harmony ---------------------------------------------------------------------------------------
# Phrase templates: each slot is [(token, weight), ...]. Tokens use the mode's degrees (case = quality,
# b/# alter the degree, suffix 7 adds the diatonic/dominant seventh, 'o' diminished). Every loop phrase ends on a
# dominant-function chord so the loop's last bar resolves into its own first bar.
HARMONY = {
    'explore': {
        'major': ([[('I', 1)], [('vi', 3), ('IV', 3), ('iii', 1)], [('IV', 3), ('ii', 3), ('vi', 1)], [('V', 4), ('V7', 2), ('IV', 1)]],
                  [[('vi', 2), ('IV', 2), ('I', 2)], [('IV', 2), ('ii', 2), ('bVII', 1), ('iii', 1)], [('ii', 2), ('IV', 2), ('bVI', 1)], [('V', 3), ('V7', 2), ('bVII', 1)]]),
        'lydian': ([[('I', 1)], [('II', 3), ('vi', 1)], [('iii', 2), ('vi', 2)], [('II', 3), ('V', 2)]],
                   [[('vi', 2), ('I', 2)], [('II', 3)], [('V', 2), ('vi', 2)], [('II', 3), ('V', 1)]]),
        'mixolydian': ([[('I', 1)], [('VII', 3), ('v', 1)], [('IV', 3)], [('I', 2), ('v', 2)]],
                       [[('I', 2), ('IV', 1)], [('v', 2), ('IV', 2)], [('VII', 3)], [('IV', 2), ('VII', 3)]]),
        'minor': ([[('i', 1)], [('VI', 3), ('iv', 2), ('III', 2)], [('VII', 3), ('iv', 2), ('VI', 1)], [('V', 3), ('VII', 2)]],
                  [[('VI', 2), ('i', 2)], [('III', 2), ('iv', 2)], [('VI', 2), ('iv', 2), ('iio', 1)], [('V', 3), ('V7', 1), ('VII', 1)]]),
        'dorian': ([[('i', 1)], [('IV', 4)], [('i', 2), ('III', 2)], [('VII', 2), ('IV', 2)]],
                   [[('i', 2), ('v', 1)], [('IV', 2), ('ii', 1)], [('VII', 2), ('III', 2)], [('IV', 2), ('VII', 2)]]),
        'phrygian': ([[('i', 1)], [('II', 4)], [('i', 2), ('VII', 2)], [('II', 3)]],
                     [[('i', 2)], [('VII', 2), ('iv', 2)], [('VI', 2), ('II', 2)], [('II', 2), ('VII', 2)]]),
    },
    'tension': {
        'minor': ([[('i', 1)], [('VI', 2), ('iv', 2)], [('i', 2), ('bII', 2)], [('V', 3), ('VII', 1)]],),
        'phrygian': ([[('i', 1)], [('II', 3)], [('i', 2), ('iv', 1)], [('II', 2), ('VII', 2)]],),
    },
    'combat': {
        'minor': ([[('i', 1)], [('VI', 3), ('VII', 2), ('iv', 2)], [('VII', 2), ('III', 2), ('VI', 2)], [('V', 3), ('VII', 2)]],
                  [[('VI', 2), ('i', 2)], [('iv', 2), ('VI', 2)], [('VII', 2), ('III', 2)], [('V', 4)]]),
    },
    'victory': {
        'major': ([[('I', 1)], [('IV', 3), ('vi', 1)], [('V', 3), ('V7', 2)], [('I', 1)]],),
        'mixolydian': ([[('I', 1)], [('VII', 3)], [('IV', 3)], [('I', 1)]],),
    },
    'town': {
        'major': ([[('I', 1)], [('IV', 3), ('vi', 2)], [('I', 2), ('ii', 2)], [('V', 4)]],
                  [[('IV', 2), ('vi', 1)], [('I', 3)], [('ii', 2), ('IV', 2)], [('V', 3), ('V7', 2)]]),
        'mixolydian': ([[('I', 1)], [('VII', 3)], [('IV', 3)], [('I', 2), ('v', 1)]],
                       [[('IV', 2)], [('I', 2)], [('v', 2), ('VII', 2)], [('IV', 2), ('VII', 2)]]),
        'dorian': ([[('i', 1)], [('IV', 4)], [('i', 2), ('VII', 2)], [('IV', 3)]],
                   [[('III', 2), ('i', 2)], [('IV', 3)], [('VII', 3)], [('IV', 2), ('v', 2)]]),
    },
    'night': {
        'lydian': ([[('I', 1)], [('II', 3)], [('I', 2), ('vi', 2)], [('II', 3), ('V', 1)]],),
        'dorian': ([[('i', 1)], [('IV', 3)], [('i', 2), ('VII', 2)], [('IV', 3)]],),
        'major': ([[('I', 1)], [('IV', 3)], [('vi', 2), ('I', 2)], [('IV', 3), ('V', 1)]],),
    },
}


def state_mode(state, base_mode):
    """The mode each mood state uses, all on the world's tonic (parallel modes keep every transition coherent)."""
    maj = base_mode in MAJORISH
    return {'explore': base_mode if base_mode in HARMONY['explore'] else ('major' if maj else 'minor'),
            'tension': 'minor' if maj else 'phrygian',
            'combat': 'minor',
            'victory': 'mixolydian' if base_mode == 'mixolydian' else 'major',
            'town': ('mixolydian' if base_mode == 'mixolydian' else 'major') if maj else 'dorian',
            'night': 'lydian' if maj else 'dorian'}[state]


def _chord2(token, tonic, mode):
    """Roman token -> chord dict with pitch classes and roles (root, third, fifth, seventh, leading tone)."""
    scale = MODES[mode]
    seven = token.endswith('7')
    base = token[:-1] if seven else token
    ch = _parse_chord(base, tonic, scale)
    ints = list(ch['ints'])
    if seven:
        if base.endswith('o'):
            ints.append(9)                                     # fully diminished seventh
        elif ch['major'] and ch['deg'] in (0, 3) and not base.startswith(('b', '#')):
            ints.append(11)                                    # Imaj7 / IVmaj7
        else:
            ints.append(10)                                    # dominant 7th (V7, II7 = V/V, bVII7) or minor 7th
    root = ch['root']
    pcs = [(root + i) % 12 for i in ints]
    roles = {'root': pcs[0], 'third': pcs[1], 'fifth': pcs[2]}
    if len(pcs) > 3:
        roles['seventh'] = pcs[3]
    dominantish = ch['major'] and ((root - tonic) % 12 == 7 or (seven and ch['deg'] not in (0, 3)))
    lt = (root + 4) % 12 if dominantish else None             # the (secondary) leading tone: a major third up
    if base.endswith('o') and (root - tonic) % 12 == 11:
        lt = root
    return {'token': token, 'root': root, 'pcs': pcs, 'roles': roles, 'major': ch['major'], 'deg': ch['deg'],
            'dom': lt is not None, 'lt': lt}


def progression(state, mode, n_chords, rng, tonic=0):
    """A functional progression for one state: phrase templates realised by weighted choice.
    Secondary dominants: a chord before V is sometimes replaced by V/V (the major II, with its seventh)."""
    tab = HARMONY[state].get(mode) or next(iter(HARMONY[state].values()))
    toks = []
    k = 0
    while len(toks) < n_chords:
        tmpl = tab[k % len(tab)]
        for j, slot in enumerate(tmpl):
            if len(toks) >= n_chords:
                break
            tok = _wchoice(rng, slot)
            for _ in range(6):                               # no immediate repeats when the slot has options
                if not toks or tok != toks[-1] or len(slot) == 1:
                    break
                tok = _wchoice(rng, slot)
            toks.append(tok)
        k += 1
    if state in ('explore', 'town', 'combat'):
        for i in range(1, len(toks)):
            if toks[i] in ('V', 'V7') and toks[i - 1] in ('ii', 'IV', 'iv', 'VI') and rng.random() < 0.3 \
                    and mode in ('major', 'minor', 'mixolydian'):
                toks[i - 1] = 'II7'
    return toks


# ---- voice leading (dynamic programming over voicings, with the classical rules as costs) -------------------------
VL_RANGES = {'B': (36, 55), 'T': (48, 67), 'A': (55, 72), 'S': (60, 79)}
_PAIRS = [(i, j) for i in range(4) for j in range(i + 1, 4)]


def _voicings(ch, ranges=VL_RANGES):
    """All 4-voice voicings [B, T, A, S] of a chord that respect range, spacing, completeness and doubling."""
    pcs = ch['pcs']
    roles = ch['roles']
    req = {roles['root'], roles['third']}
    if 'seventh' in roles:
        req.add(roles['seventh'])
    basses = [(roles['root'], 0.0), (roles['third'], 1.2)]
    out, stat = [], []
    tones = {v: [m for m in range(ranges[v][0], ranges[v][1] + 1) if m % 12 in pcs] for v in 'BTAS'}
    for bpc, bcost in basses:
        for b in tones['B']:
            if b % 12 != bpc:
                continue
            for t in tones['T']:
                if t <= b or t - b > 24:
                    continue
                for a in tones['A']:
                    if a <= t or a - t > 12:
                        continue
                    for s in tones['S']:
                        if s <= a or s - a > 12:
                            continue
                        v = (b, t, a, s)
                        got = {x % 12 for x in v}
                        if not req <= got:
                            continue
                        cnt = {}
                        for x in v:
                            cnt[x % 12] = cnt.get(x % 12, 0) + 1
                        c = bcost
                        if ch['lt'] is not None and cnt.get(ch['lt'], 0) > 1:
                            c += 20.0
                        if 'seventh' in roles and cnt.get(roles['seventh'], 0) > 1:
                            c += 20.0
                        if ch['major'] and cnt.get(roles['third'], 0) > 1:
                            c += 1.5
                        if roles['fifth'] not in got:
                            c += 0.8
                        if bpc == roles['third'] and cnt.get(roles['third'], 0) > 1:
                            c += 3.0
                        c += 0.04 * abs((t + a + s) / 3.0 - 64)
                        out.append(v)
                        stat.append(c)
    return np.array(out, dtype=np.int64).reshape(-1, 4), np.array(stat, dtype=np.float64)


def _vl_cost(V1, V2, ch1, ch2):
    """(C1, C2) transition cost between voicing sets with the part-writing rules."""
    d = V2[None, :, :] - V1[:, None, :]
    ad = np.abs(d)
    cost = (ad * np.array([0.3, 0.8, 0.8, 1.0])).sum(-1)
    cost += ((ad[..., 1:] > 4) * 1.5 + (ad[..., 1:] > 7) * 4.0).sum(-1) + (ad[..., 3] > 9) * 3.0
    sgn = np.sign(d)
    for i, j in _PAIRS:
        i1 = (V1[:, j] - V1[:, i]) % 12
        i2 = (V2[:, j] - V2[:, i]) % 12
        p1 = (i1 == 0) | (i1 == 7)
        p2 = (i2 == 0) | (i2 == 7)
        same = (i1[:, None] == i2[None, :]) & p1[:, None] & p2[None, :]
        moving = (ad[..., i] > 0) & (ad[..., j] > 0) & (sgn[..., i] == sgn[..., j])
        cost += (same & moving) * 25.0
    # direct (hidden) fifths/octaves in the outer voices with a soprano leap
    p2o = ((V2[:, 3] - V2[:, 0]) % 12)
    perf = (p2o == 0) | (p2o == 7)
    cost += (perf[None, :] & (sgn[..., 0] == sgn[..., 3]) & (sgn[..., 0] != 0) & (ad[..., 3] > 2)) * 5.0
    # overlap: a voice moves past the previous position of its neighbour
    cost += ((V2[None, :, 1] > V1[:, None, 2]) | (V2[None, :, 2] < V1[:, None, 1]) |
             (V2[None, :, 2] > V1[:, None, 3]) | (V2[None, :, 3] < V1[:, None, 2])) * 3.0
    # tendency tones: leading tone up to the tonic, chordal seventh down by step
    if ch1['lt'] is not None and (ch1['lt'] + 1) % 12 in ch2['pcs']:
        for k, w in ((3, 8.0), (2, 2.0), (1, 2.0)):
            has = (V1[:, k] % 12) == ch1['lt']
            ok = d[..., k] == 1
            cost += (has[:, None] & ~ok) * w
    sev = ch1['roles'].get('seventh')
    if sev is not None:
        held_ok = sev in ch2['pcs']                         # e.g. V7 -> IV: the seventh may stay as a common tone
        for k, w in ((3, 8.0), (2, 5.0), (1, 5.0), (0, 3.0)):
            has = (V1[:, k] % 12) == sev
            ok = (d[..., k] == -1) | (d[..., k] == -2) | ((d[..., k] == 0) & held_ok)
            cost += (has[:, None] & ~ok) * w
    # common tones held, contrary outer voices
    cost -= ((ad[..., 1:] == 0).sum(-1)) * 0.8
    cost -= ((sgn[..., 0] * sgn[..., 3]) < 0) * 0.6
    return cost


def voice_lead(chords, loop=True, ranges=VL_RANGES, k_start=10):
    """Best voicing per chord (Viterbi). loop=True also scores the wrap from the last chord to the first."""
    sets = [_voicings(c, ranges) for c in chords]
    for (V, s), c in zip(sets, chords):
        if len(V) == 0:
            raise ValueError('no voicing for chord %s' % c['token'])
    n = len(chords)
    trans = [_vl_cost(sets[i][0], sets[i + 1][0], chords[i], chords[i + 1]) for i in range(n - 1)]
    wrap = _vl_cost(sets[-1][0], sets[0][0], chords[-1], chords[0]) if (loop and n > 1) else None
    V0, s0 = sets[0]
    starts = np.argsort(s0)[:k_start] if loop else [None]
    best = (math.inf, None)
    for st in starts:
        cost = s0.copy()
        if st is not None:
            cost = np.full_like(s0, math.inf)
            cost[st] = s0[st]
        back = []
        for i in range(n - 1):
            tot = cost[:, None] + trans[i]
            arg = np.argmin(tot, axis=0)
            back.append(arg)
            cost = tot[arg, np.arange(tot.shape[1])] + sets[i + 1][1]
        if wrap is not None and st is not None:
            fin = cost + wrap[:, st]
        else:
            fin = cost
        e = int(np.argmin(fin))
        if fin[e] < best[0]:
            path = [e]
            for arg in reversed(back):
                path.append(int(arg[path[-1]]))
            path.reverse()
            best = (float(fin[e]), path)
    path = best[1]
    return [tuple(int(x) for x in sets[i][0][path[i]]) for i in range(n)], best[0]


def vl_report(voicings, chords, loop=True):
    """Count rule breaks (parallel 5ths/8ves, unresolved leading tones/sevenths, crossings) for tests and docs."""
    rep = {'parallels': 0, 'lt_unresolved_soprano': 0, 'seventh_unresolved': 0, 'crossings': 0, 'spacing': 0}
    n = len(voicings)
    pairs = [(i, i + 1) for i in range(n - 1)] + ([(n - 1, 0)] if loop and n > 1 else [])
    for v in voicings:
        if not (v[0] < v[1] < v[2] < v[3]):
            rep['crossings'] += 1
        if v[2] - v[1] > 12 or v[3] - v[2] > 12:
            rep['spacing'] += 1
    for a, b in pairs:
        A, B = voicings[a], voicings[b]
        for i, j in _PAIRS:
            ia, ib = (A[j] - A[i]) % 12, (B[j] - B[i]) % 12
            if ia == ib and ia in (0, 7) and A[i] != B[i] and A[j] != B[j] and \
                    (B[i] - A[i]) * (B[j] - A[j]) > 0:
                rep['parallels'] += 1
        c1, c2 = chords[a], chords[b]
        if c1['lt'] is not None and (c1['lt'] + 1) % 12 in c2['pcs'] and A[3] % 12 == c1['lt'] and B[3] - A[3] != 1:
            rep['lt_unresolved_soprano'] += 1
        sv = c1['roles'].get('seventh')
        if sv is not None:
            for k in range(4):
                held = B[k] == A[k] and sv in c2['pcs']
                if A[k] % 12 == sv and (B[k] - A[k]) not in (-1, -2) and not held:
                    rep['seventh_unresolved'] += 1
    return rep


# ---- leitmotifs ---------------------------------------------------------------------------------------------------
THEME_RHYTHMS = [[(0, 4), (4, 2), (6, 2), (8, 6), (14, 2)], [(0, 6), (6, 2), (8, 4), (12, 4)],
                 [(0, 2), (2, 2), (4, 4), (8, 8)], [(0, 4), (4, 4), (8, 2), (10, 2), (12, 4)],
                 [(0, 3), (3, 1), (4, 4), (8, 8)], [(0, 4), (4, 2), (6, 2), (8, 4), (12, 4)]]
THEME_CONTOURS = {3: [[0, 2, -1], [0, 4, -1], [0, -1, 3], [0, 1, 1]],
                  4: [[0, 2, 2, -1], [0, 4, -1, -1], [0, -1, -2, 4], [0, 1, 2, -3], [0, 2, -1, 2]],
                  5: [[0, 1, 2, -1, -2], [0, 4, -1, -1, 2], [0, -2, 1, 3, -1], [0, 2, 2, -3, 1], [0, 1, -2, 4, -1]],
                  6: [[0, 1, 2, 1, -2, -1], [0, 4, -1, -1, -1, 2], [0, -1, -1, 4, -1, -2]]}


def region_motif(region='world', seed=0):
    """The leitmotif of a region: a memorable 1-bar cell (rhythm + scale-degree contour) derived from its name."""
    rng = random.Random('motif|%s|%s' % (region, seed))
    r = list(rng.choice(THEME_RHYTHMS))
    ivs = list(rng.choice(THEME_CONTOURS[len(r)]))
    if rng.random() < 0.35:
        k = rng.randrange(1, len(ivs))
        ivs[k] += rng.choice([-1, 1])
    return {'region': region, 'rhythm': r, 'ivs': ivs}


def motif_transform(m, kind, rng=None):
    """Thematic transformation -> list of per-bar plan entries ({'rhythm', 'ivs'}, None = rest, 'cad')."""
    rng = rng or random.Random(0)
    r, iv = list(m['rhythm']), list(m['ivs'])
    if kind == 'state':
        return [{'rhythm': r, 'ivs': iv}]
    if kind == 'invert':
        return [{'rhythm': r, 'ivs': [iv[0]] + [-x for x in iv[1:]]}]
    if kind == 'retro':
        return [{'rhythm': r, 'ivs': [iv[0]] + [-x for x in reversed(iv[1:])]}]
    if kind == 'augment':                                   # 2 bars, durations doubled
        notes = [(st * 2, ln * 2, d) for (st, ln), d in zip(r, iv)]
        b1 = [(st, min(16 - st, ln), d) for st, ln, d in notes if st < 16]
        b2 = [(st - 16, ln, d) for st, ln, d in notes if st >= 16]
        out = [{'rhythm': [(s, l) for s, l, _ in b1], 'ivs': [d for _, _, d in b1]}]
        out.append({'rhythm': [(s, l) for s, l, _ in b2], 'ivs': [d for _, _, d in b2]} if b2 else None)
        return out
    if kind == 'diminish':                                  # half durations, stated twice in one bar
        half = [(st // 2, max(1, ln // 2)) for st, ln in r]
        rr = half + [(st + 8, ln) for st, ln in half]
        return [{'rhythm': rr, 'ivs': iv + [-(sum(iv[1:]))] + iv[1:]}]
    if kind == 'fragment':                                  # head of the motif, long final note
        k = min(3, len(r))
        rr = r[:k - 1] + [(r[k - 1][0], 16 - r[k - 1][0])]
        return [{'rhythm': rr, 'ivs': iv[:k]}]
    if kind == 'dotted':                                    # dance rhythm
        rr = []
        for st, ln in r:
            if ln >= 4:
                rr += [(st, ln * 3 // 4), (st + ln * 3 // 4, ln - ln * 3 // 4)]
            else:
                rr.append((st, ln))
        ivs2 = []
        j = 0
        for st, ln in rr:
            if any(st == a for a, _ in r):
                ivs2.append(iv[j])
                j += 1
            else:
                ivs2.append(rng.choice([1, -1]))
        return [{'rhythm': rr, 'ivs': ivs2}]
    raise ValueError(kind)


def _phrase2(ctx, chords, plan, rng, d, cadence):
    """Like _phrase, with rests (None) allowed in the plan."""
    notes = []
    for bi, ch in enumerate(chords):
        p = plan[bi] if bi < len(plan) else None
        pcs = set(ch['pcs'])
        if p is None:
            continue
        if p == 'cad':
            cell = rng.choice(CADENCE_CELLS)
            tgt = {ch['root']} if cadence == 'full' else {ch['pcs'][1 % len(ch['pcs'])], ch['pcs'][2 % len(ch['pcs'])]}
            for j, (st, ln) in enumerate(cell):
                if j == len(cell) - 1:
                    d = ctx.clamp(ctx.snap(d, pcs, tgt))
                else:
                    d = ctx.clamp(ctx.snap(d + rng.choice([1, -1, 2]), pcs) if j == 0 else d + rng.choice([-1, 1]))
                notes.append((bi, st, ln, ctx.midi(d), 0.85 if j == len(cell) - 1 else 0.75))
            continue
        for j, (st, ln) in enumerate(p['rhythm']):
            d = d + (p['ivs'][j] if j < len(p['ivs']) else 0)
            if ctx.midi(d) > ctx.hi or ctx.midi(d) < ctx.lo:
                d = ctx.clamp(d)
            if st % 8 == 0 or ln >= 6 or (st % 4 == 0 and rng.random() < 0.55):
                d = ctx.snap(d, pcs)
            vel = 0.9 if st % 8 == 0 else (0.78 if st % 4 == 0 else 0.68)
            notes.append((bi, st, ln, ctx.midi(d), vel))
    return notes, d


# Phrase plans per state (per 4-bar phrase): motif transforms and cadence types. A = first phrase, B = answer.
_PLANS = {
    'explore': (['state', 'state2', 'seq', 'cad'], ['invert', 'invert2', 'state', 'cad']),
    'tension': (['fragment', None, 'fragment_lo', None], ['augment', '+', 'fragment', None]),
    'combat': (['diminish', 'state', 'diminish', 'cad'], ['diminish', 'invert', 'state', 'cad']),
    'victory': (['state', 'dotted', 'state', 'cad'],),
    'town': (['dotted', 'dotted2', 'seq', 'cad'], ['state', 'dotted', 'invert', 'cad']),
    'night': (['augment', '+', 'fragment', None], ['augment', '+', None, 'cad']),
}


def _plan_entries(motif, state, rng):
    """Expand _PLANS into 4 bar entries per phrase ('augment' spans 2 bars; '+' marks its continuation)."""
    names = {'state': ('state', None), 'state2': ('state', 'vary'), 'seq': ('state', 'seq'),
             'invert': ('invert', None), 'invert2': ('invert', 'vary'), 'fragment': ('fragment', None),
             'fragment_lo': ('fragment', 'low'), 'dotted': ('dotted', None), 'dotted2': ('dotted', 'vary'),
             'diminish': ('diminish', None), 'retro': ('retro', None)}
    phrases = []
    for plan in _PLANS[state]:
        out = []
        for p in plan:
            if p == '+':
                continue
            if p is None or p == 'cad':
                out.append(p)
                continue
            if p == 'augment':
                out.extend(motif_transform(motif, 'augment', rng))
                continue
            base, how = names[p]
            e = motif_transform(motif, base, rng)[0]
            if how == 'vary':
                e = _vary(e, rng, rng.choice(['ornament', 'tail', 'shift']))
            elif how == 'seq':
                e = {'rhythm': e['rhythm'], 'ivs': [e['ivs'][0] + rng.choice([-2, 2, 3])] + e['ivs'][1:]}
            elif how == 'low':
                e = {'rhythm': e['rhythm'], 'ivs': [e['ivs'][0] - 3] + e['ivs'][1:]}
            out.append(e)
        phrases.append((out + [None] * 4)[:4])
    return phrases


# ---- presets: instruments, rhythm, mix per state -------------------------------------------------------------------
# drum patterns are 2-bar strings (32 sixteenths); 'fill' replaces the second half of a phrase's last bar
CUE = {
    'orchestral': {
        'bpm': 120, 'reverb': (2.8, 0.02), 'sends': {'drums': 0.16, 'bass': 0.08, 'pads': 0.3, 'arp': 0.32, 'lead': 0.3},
        'delay': None, 'sidechain': {}, 'lead_rng': {'flute': (67, 88), 'horn': (55, 77), 'brass': (57, 79),
                                                     'celesta': (72, 93), 'oboe': (64, 84)},
        'explore': dict(pad='strings', bass=('lowstr', 'half'), arp=('harp', 2, 0.0), lead=('flute', 'horn'),
                        drums={'timp': 'X...............' '........x.......', 'gran': '................' 'X...............',
                               'shaker': 'o...o...o...o...' 'o...o...o.o.o...'},
                        fill={'timp': '........x.x.xxXX'}, hr=2, ext=False),
        'tension': dict(pad='str_trem', bass=('lowstr', 'pedal'), arp=('str_stacc', 2, 0.0), lead=('horn',),
                        drums={'heart': 'X...............' '........X.......', 'taiko': '................' '..........x.....'},
                        fill={}, hr=2, ext=False, pad_dark=True),
        'combat': dict(pad='strings', comp=('brass', 'X.....X.....X...'), bass=('str_stacc', 'ostinato16'),
                       arp=None, lead=('brass', 'horn'),
                       drums={'taiko': 'X..xX..xX..xX.x.' 'X..xX..xX..xXxxx', 'snare': '....X.......X...' '....X.......X..x',
                              'timp': 'X.......X.......' 'X.......X.......', 'gran': 'X...............' 'X...............'},
                       fill={'tom_h': '........xx......', 'tom_m': '..........xx....', 'tom_l': '............xxXX'},
                       hr=1, ext=False, crash=True),
        'victory': dict(pad='strings', comp=('brass', 'X.......X.......'), bass=('lowstr', 'half'), arp=('harp', 1, 0.0),
                        lead=('brass', 'horn'), drums={'timp': 'X.......x.x.x.x.' 'X...............', 'gran': 'X...............' 'X...............'},
                        fill={}, hr=1, ext=False, crash=True),
        'town': dict(pad='strings', bass=('pizz_bass', 'walk'), arp=('harp', 2, 0.0), lead=('flute', 'oboe'),
                     drums={'frame': 'X.......X..x....' 'X.......X.x.x...', 'tamb': '....x.......x...' '....x.......x..x',
                            'shaker': 'x.o.x.o.x.o.x.o.' 'x.o.x.o.x.o.x.o.'},
                     fill={'frame': '........x.x.xxXx'}, hr=1, ext=False, swing=0.12),
        'night': dict(pad='strings', bass=('lowstr', 'whole'), arp=('harp', 4, 0.0), lead=('celesta',),
                      drums={'shaker': '................' '........o.......'}, fill={}, hr=2, ext=False, soft=True),
    },
    'synthwave': {
        'bpm': 104, 'reverb': (2.4, 0.03), 'sends': {'drums': 0.12, 'bass': 0.0, 'pads': 0.25, 'arp': 0.22, 'lead': 0.25},
        'delay': (0.75, 0.38, {'arp': 0.22, 'lead': 0.3}), 'sidechain': {'pads': 0.45, 'bass': 0.3, 'arp': 0.2},
        'lead_rng': {'saw_lead': (64, 86), 'bell': (69, 91), 'arp_saw': (60, 84)},
        'explore': dict(pad='supersaw', bass=('saw_bass', 'eighths'), arp=('arp_saw', 2, 0.0), lead=('saw_lead',),
                        drums={'kick808': 'X.......X.......' 'X.......X.....x.', 'snare': '........X.......' '........X.......',
                               'chh': 'x.o.x.o.x.o.x.o.' 'x.o.x.o.x.o.x.o.', 'ohat': '..x.......x.....' '..x.......x.....'},
                        fill={'tom_h': '........xx......', 'tom_m': '..........xx....', 'tom_l': '............xxXx'}, hr=2, ext=True),
        'tension': dict(pad='warm_pad', bass=('saw_bass', 'pulse8'), arp=('arp_saw', 2, 0.0), lead=('arp_saw',),
                        drums={'kick808': 'X.........X.....' 'X.........X.....', 'chh': 'o...o...o...o...' 'o...o...o...o...'},
                        fill={}, hr=2, ext=True, pad_dark=True),
        'combat': dict(pad='supersaw', comp=None, bass=('saw_bass', 'sixteenths'), arp=('arp_saw', 1, 0.0), lead=('saw_lead',),
                       drums={'kick808': 'X...X...X...X...' 'X...X...X...X...', 'snare': '....X.......X...' '....X.......X...',
                              'clap': '....x.......x...' '....x.......x..x', 'chh': 'xoxoxoxoxoxoxoxo' 'xoxoxoxoxoxoxoxo',
                              'ohat': '..x...x...x...x.' '..x...x...x...x.'},
                       fill={'tom_h': '........xx......', 'tom_m': '..........xx....', 'tom_l': '............xxXX'}, hr=1, ext=True, crash=True),
        'victory': dict(pad='supersaw', bass=('saw_bass', 'eighths'), arp=('arp_saw', 1, 0.0), lead=('saw_lead',),
                        drums={'kick808': 'X...X...X...X...' 'X...............', 'snare': '....X.......X...' '................'},
                        fill={}, hr=1, ext=True, crash=True),
        'town': dict(pad='warm_pad', bass=('saw_bass', 'groove'), arp=('arp_saw', 2, 0.0), lead=('bell',),
                     drums={'kick808': 'X..X..X...X..X..' 'X..X..X...X.....', 'snare': '....X.......X...' '....X.......X...',
                            'chh': 'x.xxx.xxx.xxx.xx' 'x.xxx.xxx.xxx.xx'}, fill={}, hr=1, ext=True),
        'night': dict(pad='warm_pad', bass=('sub_drone', 'whole'), arp=('bell', 4, 0.0), lead=('bell',),
                      drums={'kick808': 'X...............' '................', 'chh': '....o.......o...' '....o.......o...'},
                      fill={}, hr=2, ext=True, soft=True),
    },
    'folk': {
        'bpm': 112, 'reverb': (1.8, 0.015), 'sends': {'drums': 0.12, 'bass': 0.05, 'pads': 0.22, 'arp': 0.25, 'lead': 0.25},
        'delay': None, 'sidechain': {}, 'lead_rng': {'fiddle': (62, 86), 'whistle': (74, 96), 'guitar': (52, 76),
                                                     'mandolin': (64, 86)},
        'explore': dict(pad='reed', bass=('pizz_bass', 'half'), arp=('guitar', 2, 0.0), lead=('whistle', 'fiddle'),
                        drums={'frame': 'X.......x.......' 'X.......x...x...', 'shaker': 'o.o.o.o.o.o.o.o.' 'o.o.o.o.o.o.o.o.'},
                        fill={'frame_hi': '........x.x.xxxx'}, hr=2, ext=False, swing=0.1),
        'tension': dict(pad='reed', bass=('lowstr', 'pedal'), arp=('mandolin', 8, 0.0), lead=('fiddle',),
                        drums={'frame': 'X...............' '........X.......'}, fill={}, hr=2, ext=False, pad_dark=True),
        'combat': dict(pad='reed', comp=('guitar', 'X.x.X.x.X.x.X.x.'), bass=('pizz_bass', 'ostinato8'), arp=None,
                       lead=('fiddle',),
                       drums={'frame': 'X.xxX.xxX.xxX.xx' 'X.xxX.xxX.xxXxxx', 'frame_hi': '..x...x...x...x.' '..x...x...x...x.',
                              'tamb': 'x.x.x.x.x.x.x.x.' 'x.x.x.x.x.x.x.x.'}, fill={'frame_hi': '........xxxxXXXX'},
                       hr=1, ext=False, crash=False),
        'victory': dict(pad='reed', comp=('guitar', 'X...X...X...X...'), bass=('pizz_bass', 'half'), arp=None,
                        lead=('fiddle', 'whistle'), drums={'frame': 'X...X...X.x.X.x.' 'X...............', 'tamb': 'x.x.x.x.x.x.x.x.' 'x...............'},
                        fill={}, hr=1, ext=False),
        'town': dict(pad='reed', comp=('guitar', 'X..x..X.X..x..X.'), bass=('pizz_bass', 'walk'), arp=None,
                     lead=('fiddle', 'whistle'),
                     drums={'frame': 'X..xX..xX..xX.x.' 'X..xX..xX..xX.xx', 'tamb': '..x...x...x...x.' '..x...x...x...x.'},
                     fill={'frame_hi': '........x.x.xxXx'}, hr=1, ext=False, swing=0.33),
        'night': dict(pad='reed', bass=('pizz_bass', 'whole'), arp=('guitar', 4, 0.0), lead=('whistle',),
                      drums={}, fill={}, hr=2, ext=False, soft=True),
    },
    'ambient': {
        'bpm': 84, 'reverb': (5.5, 0.04), 'sends': {'drums': 0.22, 'bass': 0.05, 'pads': 0.5, 'arp': 0.5, 'lead': 0.5},
        'delay': (1.5, 0.42, {'lead': 0.32, 'arp': 0.28}), 'sidechain': {},
        'lead_rng': {'bell': (69, 91), 'vibes': (62, 86), 'celesta': (72, 93)},
        'explore': dict(pad='warm_pad', bass=('sub_drone', 'whole'), arp=('bell', 4, 0.0), lead=('bell', 'vibes'),
                        drums={'pulse': 'X...............' 'X...............', 'rim': '........o.......' '........o.....o.',
                               'shaker': '..o...o...o...o.' '..o...o...o...o.'}, fill={}, hr=2, ext=True),
        'tension': dict(pad='warm_pad', bass=('sub_drone', 'pedal'), arp=('bell', 8, 0.0), lead=('vibes',),
                        drums={'pulse': 'X.......X.......' 'X.......X.......', 'heart': 'X...............' '................'},
                        fill={}, hr=2, ext=True, pad_dark=True),
        'combat': dict(pad='warm_pad', comp=('supersaw', 'X.....X.....X...'), bass=('saw_bass', 'eighths'),
                       arp=('arp_saw', 2, 0.0), lead=('vibes',),
                       drums={'dkick': 'X..x..X...x..X..' 'X..x..X...x..X..', 'rim': '....X.......X...' '....X.......X...',
                              'shaker': 'xoxoxoxoxoxoxoxo' 'xoxoxoxoxoxoxoxo'}, fill={}, hr=1, ext=True),
        'victory': dict(pad='warm_pad', bass=('sub_drone', 'whole'), arp=('bell', 1, 0.0), lead=('vibes',),
                        drums={'pulse': 'X.......X.......' 'X...............'}, fill={}, hr=1, ext=True),
        'town': dict(pad='keys', bass=('round_bass', 'walk'), arp=('vibes', 4, 0.0), lead=('vibes',),
                     drums={'dkick': 'X.......X.x.....' 'X.......X.x.....', 'rim': '....x.......x...' '....x.......x...',
                            'lhat': 'x.x.x.x.x.x.x.x.' 'x.x.x.x.x.x.x.x.'}, fill={}, hr=1, ext=True, swing=0.18),
        'night': dict(pad='warm_pad', bass=('sub_drone', 'whole'), arp=('celesta', 8, 0.0), lead=('celesta',),
                      drums={}, fill={}, hr=2, ext=True, soft=True),
    },
    'chiptune': {
        'bpm': 140, 'reverb': (0.9, 0.01), 'sends': {'drums': 0.04, 'bass': 0.0, 'pads': 0.08, 'arp': 0.08, 'lead': 0.1},
        'delay': (0.5, 0.25, {'lead': 0.18}), 'sidechain': {}, 'lead_rng': {'pulse25': (67, 89), 'pulse12': (64, 86)},
        'explore': dict(pad=None, bass=('tri_nes', 'eighths'), arp=('chip_arp', 1, 0.0), lead=('pulse25',),
                        drums={'ckick': 'X.......X.......' 'X.......X.x.....', 'csnare': '....X.......X...' '....X.......X...',
                               'chat': 'x.x.x.x.x.x.x.x.' 'x.x.x.x.x.x.x.x.'}, fill={'csnare': '........x.x.xxxx'}, hr=1, ext=False),
        'tension': dict(pad=None, bass=('tri_nes', 'pulse8'), arp=('chip_arp', 1, 0.0), lead=('pulse12',),
                        drums={'ckick': 'X.......X.......' 'X.......X.......', 'chat': '..x...x...x...x.' '..x...x...x...x.'},
                        fill={}, hr=2, ext=False),
        'combat': dict(pad=None, bass=('tri_nes', 'sixteenths'), arp=('chip_arp', 1, 0.0), lead=('pulse25',),
                       drums={'ckick': 'X...X...X..XX...' 'X...X...X..XX.x.', 'csnare': '....X.......X..x' '....X.......X.xx',
                              'chat': 'xxxxxxxxxxxxxxxx' 'xxxxxxxxxxxxxxxx'}, fill={'csnare': '........xxxxxxxx'}, hr=1, ext=False),
        'victory': dict(pad=None, bass=('tri_nes', 'eighths'), arp=('chip_arp', 1, 0.0), lead=('pulse25',),
                        drums={'ckick': 'X...X...X...X...' 'X...............', 'csnare': '....X.......X...' '................'},
                        fill={}, hr=1, ext=False),
        'town': dict(pad=None, bass=('tri_nes', 'walk'), arp=('chip_arp', 1, 0.0), lead=('pulse25',),
                     drums={'ckick': 'X..X..X.X..X..X.' 'X..X..X.X..X....', 'csnare': '....X.......X...' '....X.......X...',
                            'chat': 'x.x.x.x.x.x.x.x.' 'x.x.x.x.x.x.x.x.'}, fill={}, hr=1, ext=False, swing=0.2),
        'night': dict(pad=None, bass=('tri_nes', 'whole'), arp=('chip_arp', 1, 0.0), lead=('pulse12',),
                      drums={'chat': '..o.......o.....' '..o.......o.....'}, fill={}, hr=2, ext=False, soft=True),
    },
}


def _i_oboe(midi, dur, vel, rng):
    f0 = mtof(midi)
    env = _adsr(dur, 0.03, 0.25, 0.85, 0.12)
    n = len(env)
    vib = _vib(n, 5.2, 0.005, 0.25)
    y = _osc(_table('pulse25', f0, 6500), f0 * vib, n, rng.random())
    y = _filt(y, lo=250, hi=7000, peak=((1100, 8, 2.5), (2800, 4, 2)))
    return y * env * 0.9


PITCHED['oboe'] = _i_oboe
INST_GAIN['oboe'] = 0.24

# layer (stem) membership by bus, per state; 'kick' events go to every layer as silent sidechain keys
CUE_LAYERS = {
    'explore': {'bed': ('pads', 'bass'), 'perc': ('drums',), 'lead': ('lead', 'arp')},
    'tension': {'bed': ('pads', 'bass'), 'perc': ('drums', 'arp'), 'lead': ('lead',)},
    'combat': {'bed': ('pads', 'bass'), 'perc': ('drums',), 'lead': ('lead', 'arp')},
    'town': {'bed': ('pads', 'bass'), 'perc': ('drums',), 'lead': ('lead', 'arp')},
    'night': {'bed': ('pads', 'bass'), 'perc': ('drums',), 'lead': ('lead', 'arp')},
    'victory': {'mix': ('pads', 'bass', 'drums', 'lead', 'arp')},
}
CUE_LUFS = {'explore': -19.0, 'tension': -21.0, 'combat': -17.0, 'victory': -17.0, 'town': -19.0, 'night': -22.0}
CUE_BARS = {'explore': 16, 'tension': 8, 'combat': 8, 'victory': 4, 'town': 16, 'night': 8}
CUE_TARGETS = {  # bus balance (dB, gated RMS) per state
    'explore': {'drums': -12, 'bass': -10, 'pads': -6, 'arp': -11, 'lead': -6},
    'tension': {'drums': -9, 'bass': -9, 'pads': -6, 'arp': -11, 'lead': -10},
    'combat': {'drums': -5, 'bass': -8, 'pads': -8, 'arp': -11, 'lead': -6},
    'victory': {'drums': -7, 'bass': -9, 'pads': -7, 'arp': -11, 'lead': -5},
    'town': {'drums': -8, 'bass': -9, 'pads': -7, 'arp': -11, 'lead': -6},
    'night': {'drums': -18, 'bass': -11, 'pads': -6, 'arp': -10, 'lead': -8},
}


def list_presets():
    return list(PRESETS)


def preset_for_style(style):
    return {'adventure': 'orchestral', 'battle': 'orchestral', 'cozy': 'folk'}.get(style, style if style in CUE else 'orchestral')


def world_score_defaults(world_mood):
    """Score defaults for one of the builder's world moods: preset, tonic key, base mode."""
    w = WORLD_MOODS.get(world_mood) or WORLD_MOODS['Alpine Sunset']
    mood = MOOD_ALIASES.get(w['mood'], w['mood'])
    mode = MOODS[mood][0]
    preset = {'Bioluminescent Twilight': 'synthwave', 'Alpine Sunset': 'orchestral', 'Sub-Zero Aurora': 'ambient',
              'Crimson Citadel': 'orchestral', 'Sunken Ruins': 'folk'}.get(world_mood, 'orchestral')
    return {'preset': preset, 'key': w['key'], 'mode': mode}


def _bass_role(role, ch_bass, nxt_bass, tonic, scale):
    if role == 'R':
        return ch_bass
    if role == 'O':
        return ch_bass + 12
    if role == '5':
        return ch_bass + 7 if ch_bass + 7 <= 55 else ch_bass - 5
    if role == 'P':
        m = 36 + tonic
        return m - 12 if m > 45 else m
    cands = [nxt_bass - 1, nxt_bass + 1, nxt_bass - 2, nxt_bass + 2]
    sp = [(tonic + s) % 12 for s in scale]
    dia = [c for c in cands if c % 12 in sp]
    return dia[0] if dia else nxt_bass


_BASS_PATS = {   # (step, length, role) over 32 steps (2 bars)
    'half': [(0, 16, 'R'), (16, 8, 'R'), (24, 8, '5')],
    'whole': [(0, 32, 'R')],
    'pedal': [(0, 32, 'P')],
    'walk': [(0, 4, 'R'), (4, 4, '5'), (8, 4, 'O'), (12, 4, '5'), (16, 4, 'R'), (20, 4, '5'), (24, 4, 'R'), (28, 4, 'A')],
    'eighths': [(i, 2, 'R' if (i // 2) % 4 != 3 else 'O') for i in range(0, 32, 2)],
    'pulse8': [(i, 2, 'P') for i in range(0, 32, 2)],
    'sixteenths': [(i, 1, 'R' if i % 4 != 2 else 'O') for i in range(32)],
    'ostinato16': [(i, 1, 'R' if i % 8 not in (3, 6) else ('O' if i % 8 == 3 else '5')) for i in range(32)],
    'ostinato8': [(i, 2, 'RROR5RRA'[(i // 2) % 8]) for i in range(0, 32, 2)],
    'groove': [(0, 3, 'R'), (3, 3, 'R'), (6, 2, 'O'), (10, 2, 'R'), (12, 4, '5'), (16, 3, 'R'), (19, 3, 'R'), (22, 2, 'O'), (26, 2, '5'), (28, 4, 'A')],
}


def _plan_cue(state, preset='orchestral', key=None, mode=None, bpm=None, bars=None, seed=0, motif=None,
              region='world'):
    if state not in SCORE_STATES:
        raise ValueError('unknown state %r (use %s)' % (state, ', '.join(SCORE_STATES)))
    preset = preset_for_style(preset)
    P = CUE[preset]
    S = P[state]
    rng = random.Random('cue|%s|%s|%s|%s|%s|%s' % (state, preset, key, mode, seed, region))
    tonic, kmode = _parse_key(key, random.Random(seed))
    base_mode = mode or kmode or 'major'
    smode = state_mode(state, base_mode)
    scale = MODES[smode]
    bpm = float(bpm or P['bpm'])
    bars = int(bars or CUE_BARS[state])
    hr = S['hr']                                         # bars per chord
    if bars < hr * 4:
        hr = max(1, bars // 4)
    nch = max(1, bars // hr)
    toks = progression(state, smode, nch, rng, tonic)
    if state == 'victory':
        toks = toks[:nch]
        if toks[-1] not in ('I',):
            toks[-1] = 'I'
    chords = [_chord2(t, tonic, smode) for t in toks]
    voicings, vl_cost = voice_lead(chords, loop=(state != 'victory'))
    motif = motif or region_motif(region, seed)
    lead_insts = S['lead']
    lrng = P['lead_rng'].get(lead_insts[0], (64, 86))
    ctx = _MelodyCtx(tonic, scale, *lrng)
    phrases = _plan_entries(motif, state, rng)
    bar_chords = []
    for b in range(bars):
        ci = min(len(chords) - 1, b // hr)
        bar_chords.append(ci)
    mel = []
    d = ctx.deg_near((ctx.lo + ctx.hi) // 2 - 2)
    for ph in range(int(math.ceil(bars / 4.0))):
        plan = phrases[ph % len(phrases)]
        chs = [dict(chords[bar_chords[min(bars - 1, ph * 4 + j)]]) for j in range(4)]
        last_phrase = ph == int(math.ceil(bars / 4.0)) - 1
        cad = 'full' if (state == 'victory' and last_phrase) else ('half' if ph % 2 == 0 else 'full')
        notes, d = _phrase2(ctx, chs, plan, rng, d, cad)
        for (bi, st, ln, m, v) in notes:
            if ph * 4 + bi < bars:
                mel.append((ph * 4 + bi, st, ln, m, v))
        d = ctx.deg_near((ctx.lo + ctx.hi) // 2 - 2) if rng.random() < 0.5 else d
    return {'state': state, 'preset': preset, 'tonic': tonic, 'mode': smode, 'base_mode': base_mode, 'scale': scale,
            'key_name': '%s %s' % (NOTE_NAMES[tonic], smode), 'bpm': bpm, 'bar_s': 240.0 / bpm, 's16': 15.0 / bpm,
            'bars': bars, 'hr': hr, 'tokens': toks, 'chords': chords, 'voicings': voicings, 'vl_cost': vl_cost,
            'bar_chords': bar_chords, 'melody': mel, 'motif': motif, 'cfg_state': S, 'P': P, 'rng': rng,
            'region': region, 'seed': seed}


def _arrange_cue(cue):
    """Events for every bar of the cue: (t, bus, inst, midi, dur, vel, pan). Section-local times from 0."""
    S, P = cue['cfg_state'], cue['P']
    rng = cue['rng']
    s16, bar_s = cue['s16'], cue['bar_s']
    sw = S.get('swing', 0.0)
    soft = 0.75 if S.get('soft') else 1.0
    evs = []

    def at(b, step):
        t = b * bar_s + step * s16
        if sw and step % 2 == 1:
            t += sw * s16
        return t

    def ev(b, bus, inst, step, ln, midi=None, vel=1.0, pan=0.0):
        evs.append((at(b, step), bus, inst, midi, max(0.03, ln * s16), vel * soft, pan))

    bars, hr = cue['bars'], cue['hr']
    chords, vo = cue['chords'], cue['voicings']
    for b in range(bars):
        ci = cue['bar_chords'][b]
        ch, v = chords[ci], vo[ci]
        nci = cue['bar_chords'][(b + 1) % bars]
        first_of_chord = b % hr == 0
        upper = list(v[1:])
        if S.get('pad_dark'):
            upper = [m - 12 if m > 66 else m for m in upper]
        # pads: one held chord per chord change (strings breathe), color tone for synth presets
        if S.get('pad') and first_of_chord:
            notes = list(upper)
            if S.get('ext') and len(ch['pcs']) == 3:
                ninth = (ch['root'] + 2) % 12
                cand = [m for m in range(60, 76) if m % 12 == ninth and m not in notes]
                if cand:
                    notes.append(cand[0])
            for k, m in enumerate(notes):
                ev(b, 'pads', S['pad'], 0, 16 * hr * 0.98, m, 0.6 + 0.2 * (k == len(notes) - 1), (k / max(1, len(notes) - 1) - 0.5) * 0.7)
        comp = S.get('comp')
        if comp:
            inst, pat = comp
            hits = [i for i, c in enumerate(pat) if c != '.']
            for j, st in enumerate(hits):
                ln = (hits[j + 1] - st) if j + 1 < len(hits) else 16 - st
                ln = min(ln, 3) if inst in ('brass', 'supersaw') else ln
                if inst == 'guitar':                       # strum: staggered strings, down/up
                    order = upper if j % 2 == 0 else list(reversed(upper))
                    for k, m in enumerate([v[0] + 12] + order if j % 2 == 0 else order):
                        evs.append((at(b, st) + k * 0.012, 'pads', 'guitar', m, ln * s16 * 0.9, (0.8 if j % 2 == 0 else 0.6) * soft, (k - 1.5) * 0.15))
                else:
                    for k, m in enumerate(upper):
                        ev(b, 'pads', inst, st, ln * 0.92, m, 0.75 if pat[st] == 'X' else 0.55, (k - 1) * 0.3)
        # bass
        bname, bpat = S['bass']
        half = 16 if (b % 2 == 1) else 0
        pat = [(st - half, ln, r) for st, ln, r in _BASS_PATS[bpat] if half <= st < half + 16]
        if bpat == 'pedal':
            pat = [(0, 16 * min(2, bars - b), 'P')] if b % 2 == 0 else []
        elif bpat == 'whole':
            pat = [(0, 16 * min(hr - b % hr, bars - b), 'R')] if b % hr == 0 else []
        nb = vo[nci][0]
        for st, ln, role in pat:
            m = _bass_role(role, v[0], nb, cue['tonic'], cue['scale'])
            while m > 55:
                m -= 12
            while m < 28:
                m += 12
            ev(b, 'bass', bname, st, ln * (0.9 if ln <= 2 else 0.97), m, 0.85 if st % 4 == 0 else 0.7)
        # arpeggio / ostinato
        arp = S.get('arp')
        if arp:
            inst, step, _ = arp
            tones = sorted(upper) + [upper[0] + 12, upper[1] + 12]
            if inst == 'chip_arp':
                nt = int(bar_s / 0.045)
                dt = bar_s / nt
                pts = sorted(upper)[:3]
                for k in range(nt):
                    if (k * dt) / s16 % 4 >= 3.2:
                        continue
                    evs.append((b * bar_s + k * dt, 'arp', inst, pts[k % 3] + 12, dt * 0.95, 0.75 * soft, 0.25))
            elif inst in ('str_stacc',) and cue['state'] == 'tension':
                root = v[0] + 12
                seqs = [root, root + 1, root, root + 7]
                for k in range(16 // step):
                    ev(b, 'arp', inst, k * step, step * 0.7, seqs[k % 4], 0.55 if k % 2 else 0.7, 0.2)
            else:
                seq = [0, 1, 2, 3, 4, 3, 2, 1] if step >= 2 else [0, 1, 2, 3, 4, 5, 4, 3, 2, 1, 2, 3, 4, 3, 2, 1]
                if cue['state'] in ('explore', 'town') and (b // 4) % 2 == 0 and step <= 2:
                    pass
                n_ = 16 // step
                for k in range(n_):
                    idx = seq[k % len(seq)] % len(tones)
                    vv = 0.8 if (k * step) % 4 == 0 else 0.6
                    ev(b, 'arp', inst, k * step, step * (0.9 if step > 1 else 0.8), tones[idx] + (12 if inst in ('bell', 'celesta') else 0),
                       vv, 0.35 * math.sin(k * 0.9 + b))
        # lead melody (instrument alternates between phrases A/B when two are given)
        insts = S['lead']
        li = insts[(b // 4) % len(insts)]
        lr = P['lead_rng'].get(li, (60, 88))
        for (bb, st, ln, m, vv) in cue['melody']:
            if bb != b:
                continue
            while m > lr[1]:
                m -= 12
            while m < lr[0]:
                m += 12
            ev(b, 'lead', li, st, ln * (0.8 if li.startswith('pulse') else 0.95), m, vv, 0.05)
            if cue['state'] in ('victory', 'town') and len(insts) > 1 and (b // 4) % 2 == 1:
                sp = [(cue['tonic'] + s) % 12 for s in cue['scale']]
                h = m - 3 if (m - 3) % 12 in sp else m - 4
                ev(b, 'lead', insts[0] if li != insts[0] else insts[1], st, ln * 0.95, h, vv * 0.5, -0.3)
        # drums: 2-bar patterns, fill on each phrase's last bar, crash on phrase starts
        lanes = S.get('drums', {})
        fill = S.get('fill', {})
        last_in_phrase = b % 4 == 3 and cue['state'] != 'victory'
        for inst, pat2 in lanes.items():
            pat = pat2[16 * (b % 2):16 * (b % 2) + 16] if len(pat2) >= 32 else pat2
            for st, c in enumerate(pat):
                if c == '.' or (last_in_phrase and fill and st >= 8 and inst not in KICKS):
                    continue
                midi = _timp_midi(ch['root']) if inst == 'timp' else None
                t = at(b, st) if inst not in KICKS else b * bar_s + st * s16 + (sw * s16 if (sw and st % 2) else 0.0)
                evs.append((t, 'drums', inst, midi, s16, _VEL[c] * soft, _DRUM_PAN.get(inst, 0.0)))
        if last_in_phrase and fill:
            for inst, pat in fill.items():
                for st, c in enumerate(pat):
                    if c == '.' or st < 8:
                        continue
                    midi = _timp_midi(chords[nci]['root']) if inst == 'timp' else None
                    evs.append((at(b, st), 'drums', inst, midi, s16, _VEL[c] * soft, _DRUM_PAN.get(inst, 0.0)))
        if S.get('crash') and b % 4 == 0 and (b > 0 or cue['state'] in ('victory',)):
            evs.append((b * bar_s, 'drums', 'crash', None, 2.0, 0.75, 0.2))
        if cue['state'] in ('explore', 'night') and b % 4 == 3 and cue['preset'] in ('orchestral', 'synthwave', 'ambient'):
            evs.append((b * bar_s + bar_s * 0.5, 'drums', 'swell', None, bar_s * 0.5, 0.5, 0.0))
    if cue['state'] == 'victory':                      # final chord held + big hit
        b = bars - 1
        v = vo[-1]
        ring = bar_s * 1.5
        for k, m in enumerate(v[1:]):
            evs.append((bars * bar_s, 'pads', S['pad'] or 'pulse12', m, ring, 0.7, (k - 1) * 0.3))
        evs.append((bars * bar_s, 'bass', S['bass'][0], v[0], ring, 0.8, 0.0))
        mi = cue['tonic'] + 72
        lr = P['lead_rng'].get(S['lead'][0], (60, 88))
        while mi > lr[1]:
            mi -= 12
        evs.append((bars * bar_s, 'lead', S['lead'][0], mi, ring, 0.8, 0.05))
        big = {'orchestral': 'timp', 'synthwave': 'kick808', 'folk': 'frame', 'ambient': 'boom', 'chiptune': 'ckick'}[cue['preset']]
        evs.append((bars * bar_s, 'drums', big, _timp_midi(cue['tonic']) if big == 'timp' else None, s16, 1.0, 0.0))
        if cue['preset'] != 'chiptune':
            evs.append((bars * bar_s, 'drums', 'crash', None, 2.5, 0.9, -0.2))
    return [e + (i % 3,) for i, e in enumerate(evs)]


def _cue_cfg(cue):
    P = cue['P']
    rev = P['reverb']
    if cue['state'] == 'night':
        rev = (rev[0] * 1.3, rev[1])
    return {'reverb': rev, 'sends': P['sends'], 'delay': P['delay'], 'sidechain': P['sidechain'], 'master_lp': None}


def _render_layers(cue, events, W, layers, kick_keys=True):
    """Render each layer (stem) separately with shared bus gains. Returns {layer: (2, n)}, full mix."""
    song = {'cfg': _cue_cfg(cue), 'bpm': cue['bpm'], 'bar_s': cue['bar_s'], 'style': cue['preset'], 'bed': False,
            'bed_boost': 0}
    full, _, bg = _render_numpy(song, events, W, False, targets=CUE_TARGETS[cue['state']])
    out = {}
    if len(layers) == 1:
        out[list(layers)[0]] = full
        return out, full
    for name, buses in layers.items():
        sel = [e for e in events if e[1] in buses]
        if kick_keys and 'drums' not in buses:
            sel += [(e[0], 'drums', e[2], e[3], e[4], 0.0, e[6]) + tuple(e[7:]) for e in events if e[2] in KICKS]
        if not sel:
            out[name] = np.zeros((2, int(round(W * SR)) + 1), np.float32)
            continue
        y, _, _ = _render_numpy(song, sel, W, False, bus_gains=bg, targets=CUE_TARGETS[cue['state']])
        out[name] = y
    return out, full


def render_cue(cue, target_lufs=None):
    """Seamless loop stems for a planned cue (victory: one-shot with ring-out). -> dict with arrays + metadata."""
    events = _arrange_cue(cue)
    bar_s = cue['bar_s']
    Ls = int(round(cue['bars'] * bar_s * SR))
    L = Ls / float(SR)
    layers = CUE_LAYERS[cue['state']]
    target = CUE_LUFS[cue['state']] if target_lufs is None else target_lufs
    if cue['state'] == 'victory':
        W = L + bar_s * 1.5 + 4.0
        stems, full = _render_layers(cue, events, W, layers)
        y = stems['mix']
        g = target - lufs(y)
        y = _soft_clip(y * (10 ** (g / 20.0)))
        env = np.max(np.abs(y), axis=0)
        thr = np.max(env) * 10 ** (-60 / 20.0)
        last = int(np.nonzero(env > thr)[0][-1]) + 1
        y = y[:, :last]
        k = min(int(0.5 * SR), y.shape[1] // 3)
        y[:, -k:] *= np.linspace(1, 0, k) ** 2
        return {'stems': {'mix': y.astype(np.float32)}, 'loop': None, 'len_s': y.shape[1] / float(SR),
                'gain_db': round(g, 2), 'wrap_err_db': None}
    rev_t = cue['P']['reverb'][0] * (1.3 if cue['state'] == 'night' else 1.0)
    dly = cue['P']['delay']
    dly_t = (6 * dly[0] * 60.0 / cue['bpm']) if dly else 0.0       # feedback taps ring this long, then reverb
    Ps = int(round(int(math.ceil(max(8.0, rev_t + 3.0 + dly_t) / bar_s)) * bar_s * SR))
    Hs = int(0.5 * SR)

    def shift(lst, off, lo=None, hi=None):
        o = []
        for e in lst:
            s_ = int(round(e[0] * SR)) + off
            if (lo is None or s_ >= lo) and (hi is None or s_ < hi):
                o.append((s_ / float(SR),) + e[1:])
        return o
    tl = []
    reps = int(math.ceil(Ps / float(Ls)))
    for r in range(1, reps + 1):
        tl += shift(events, Ps - r * Ls, lo=0)
    tl += shift(events, Ps) + shift(events, Ps + Ls, hi=Ps + Ls + Hs)
    W = (Ps + Ls + Hs) / float(SR)
    stems, full = _render_layers(cue, tl, W, layers)
    tot = sum(s[:, Ps:Ps + Ls].astype(np.float64) for s in stems.values())
    g = target - lufs(tot)
    gl = 10 ** (g / 20.0)
    out = {}
    worst = -200.0
    for name, s in stems.items():
        s = s.astype(np.float64)
        h = min(int(0.4 * SR), s.shape[1] - (Ps + Ls))
        dif = s[:, Ps + Ls:Ps + Ls + h] - s[:, Ps:Ps + h]
        ref = float(np.sqrt(np.mean(s[:, Ps:Ps + h] ** 2))) + 1e-12
        if ref > 1e-6:
            worst = max(worst, 20 * math.log10(float(np.sqrt(np.mean(dif ** 2))) / ref + 1e-12))
        out[name] = _soft_clip(s[:, Ps:Ps + Ls] * gl, knee=0.85).astype(np.float32)
    return {'stems': out, 'loop': (0, Ls), 'len_s': L, 'gain_db': round(g, 2), 'wrap_err_db': round(worst, 1)}


def compose_cue(state, preset='orchestral', key=None, mode=None, bpm=None, bars=None, seed=0, motif=None,
                region='world', out_dir=None, target_lufs=None, write=True):
    """Render one adaptive mood cue -> info dict (stem WAV paths with smpl loop chunks, harmony, motif, timing)."""
    t0 = time.time()
    cue = _plan_cue(state, preset, key, mode, bpm, bars, seed, motif, region)
    r = render_cue(cue, target_lufs)
    files = {}
    if write:
        out_dir = os.path.abspath(out_dir or os.path.join(OUT_DIR, 'score'))
        os.makedirs(out_dir, exist_ok=True)
        for name, y in r['stems'].items():
            p = os.path.join(out_dir, 'cue_%s_%s_%s.wav' % (_slug(region), state, name))
            _write_wav(p, y, r['loop'], '%s %s %s' % (cue['preset'], state, name), seed)
            files[name] = p
    info = {'state': state, 'preset': cue['preset'], 'region': region, 'key': cue['key_name'], 'tonic': cue['tonic'],
            'mode': cue['mode'], 'bpm': cue['bpm'], 'beats_per_bar': 4, 'bars': cue['bars'],
            'bar_s': round(cue['bar_s'], 6), 'len_s': round(r['len_s'], 6),
            'loop_samples': (r['loop'][1] - r['loop'][0]) if r['loop'] else None, 'sample_rate': SR,
            'progression': cue['tokens'], 'chords_per_bar': cue['bar_chords'], 'voicings': cue['voicings'],
            'voice_leading': vl_report(cue['voicings'], cue['chords'], loop=state != 'victory'),
            'motif': cue['motif'], 'layers': list(r['stems']), 'files': files, 'gain_db': r['gain_db'],
            'loop_wrap_error_db': r['wrap_err_db'], 'target_lufs': CUE_LUFS[state] if target_lufs is None else target_lufs,
            'render_s': round(time.time() - t0, 2), 'seed': seed}
    info['_arrays'] = r['stems']
    return info


def _slug(s):
    return re.sub(r'[^A-Za-z0-9]+', '_', str(s)).strip('_').lower()[:40] or 'x'


def compose_stinger(kind, preset='orchestral', key=None, mode=None, bpm=None, seed=0, motif=None, region='world',
                    out_dir=None, target_lufs=-17.0, write=True):
    """Short one-shot in the score's key and tempo (combat_start, boss, quest_complete, discovery, danger, defeat,
    level_up) -> info dict with 'array' and 'path'. Meant to be aligned to the next beat by the runtime."""
    if kind not in STINGERS:
        raise ValueError('unknown stinger %r (use %s)' % (kind, ', '.join(STINGERS)))
    preset = preset_for_style(preset)
    P = CUE[preset]
    tonic, kmode = _parse_key(key, random.Random(seed))
    base_mode = mode or kmode or 'major'
    bpm = float(bpm or P['bpm'])
    s16 = 15.0 / bpm
    rng = random.Random('st|%s|%s|%s|%s' % (kind, preset, key, seed))
    motif = motif or region_motif(region, seed)
    evs = []
    root = 36 + tonic
    root = root - 12 if root > 45 else root
    maj = kind in ('quest_complete', 'discovery', 'level_up')
    md = ('lydian' if kind == 'discovery' else 'major') if maj else ('phrygian' if kind == 'danger' else 'minor')
    scale = MODES[md]
    triad = [0, scale[2], scale[4]]
    lead_i = {'orchestral': 'brass', 'synthwave': 'saw_lead', 'folk': 'fiddle', 'ambient': 'vibes', 'chiptune': 'pulse25'}[preset]
    pad_i = {'orchestral': 'strings', 'synthwave': 'supersaw', 'folk': 'reed', 'ambient': 'warm_pad', 'chiptune': 'pulse12'}[preset]
    hit_i = {'orchestral': 'taiko', 'synthwave': 'kick808', 'folk': 'frame', 'ambient': 'boom', 'chiptune': 'ckick'}[preset]
    ctx = _MelodyCtx(tonic, scale, *P['lead_rng'].get(lead_i, (62, 86)))
    if kind in ('combat_start', 'boss', 'danger'):
        evs.append((0.0, 'drums', hit_i, None, s16, 1.0, 0.0))
        evs.append((0.0, 'drums', 'boom', None, s16, 0.9 if kind != 'danger' else 0.7, 0.0))
        if preset != 'chiptune':
            evs.append((0.0, 'drums', 'crash', None, 2.5, 0.8 if kind != 'danger' else 0.4, 0.15))
            evs.append((0.0, 'pads', 'braam' if preset in ('orchestral', 'ambient') else pad_i, root + 12, 8 * s16 * (2 if kind == 'boss' else 1),
                        0.9, 0.0))
        if kind == 'danger':
            for k, iv in enumerate((0, 1, 6)):
                evs.append((0.0, 'pads', 'str_trem' if preset == 'orchestral' else pad_i, root + 24 + iv, 16 * s16, 0.6, (k - 1) * 0.4))
        else:
            for k, iv in enumerate(triad):
                evs.append((0.0, 'pads', 'brass' if preset == 'orchestral' else pad_i, root + 24 + iv, 6 * s16, 0.8, (k - 1) * 0.4))
            if kind == 'boss':
                for j in range(4):
                    evs.append(((8 + 2 * j) * s16, 'drums', 'taiko' if preset == 'orchestral' else hit_i, None, s16, 0.7 + 0.1 * j, 0.0))
                evs.append((16 * s16, 'drums', hit_i, None, s16, 1.0, 0.0))
                evs.append((16 * s16, 'drums', 'boom', None, s16, 1.0, 0.0))
    elif kind in ('quest_complete', 'level_up', 'discovery'):
        tr = motif_transform(motif, 'state')[0] if kind == 'quest_complete' else None
        d = ctx.deg_near((ctx.lo + ctx.hi) // 2 - 2)
        if tr:
            for j, (st, ln) in enumerate(tr['rhythm']):
                d += tr['ivs'][j]
                d = ctx.clamp(d)
                evs.append((st * s16, 'lead', lead_i, ctx.midi(d), ln * s16 * 0.95, 0.85, 0.05))
            end = 16 * s16
            evs.append((end, 'lead', lead_i, ctx.midi(ctx.clamp(ctx.snap(d, {tonic}, {tonic}))), 10 * s16, 0.85, 0.05))
            for k, iv in enumerate(triad):
                evs.append((end, 'pads', pad_i, 60 + tonic % 12 + iv - (12 if tonic > 6 else 0), 10 * s16, 0.7, (k - 1) * 0.4))
            evs.append((end, 'drums', 'timp' if preset == 'orchestral' else hit_i, _timp_midi(tonic) if preset == 'orchestral' else None, s16, 0.9, 0.0))
        elif kind == 'level_up':
            for j, iv in enumerate((0, triad[1], triad[2], 12, 12 + triad[1], 12 + triad[2], 24)):
                evs.append((j * s16, 'arp', 'harp' if preset in ('orchestral', 'folk') else ('chip_arp' if preset == 'chiptune' else 'arp_saw'),
                            60 + tonic % 12 + iv, 2 * s16, 0.8, 0.4 * math.sin(j)))
            for k, iv in enumerate(triad):
                evs.append((8 * s16, 'pads', pad_i, 60 + tonic % 12 + iv, 8 * s16, 0.75, (k - 1) * 0.4))
            if preset != 'chiptune':
                evs.append((8 * s16, 'drums', 'crash', None, 2.0, 0.42, 0.2))
        else:
            for j in range(8):
                iv = (0, 4, 7, 11, 14, 18, 19, 23)[j]
                evs.append((j * s16, 'arp', 'celesta' if preset != 'chiptune' else 'chip_arp', 72 + tonic % 12 + (iv if md == 'lydian' else iv), 3 * s16, 0.6, 0.5 * math.sin(j * 1.3)))
            evs.append((0.0, 'drums', 'swell', None, 8 * s16, 0.6, 0.0))
            for k, iv in enumerate((0, 6, 11)):
                evs.append((8 * s16, 'pads', pad_i, 60 + tonic % 12 + iv, 12 * s16, 0.6, (k - 1) * 0.4))
    elif kind == 'defeat':
        d = ctx.deg_near(ctx.hi - 4)
        for j in range(5):
            d -= 1
            evs.append((j * 4 * s16, 'lead', lead_i if preset != 'orchestral' else 'horn', ctx.midi(ctx.clamp(d)), 4 * s16 * 0.95, 0.7, 0.05))
        for k, iv in enumerate(triad):
            evs.append((0.0, 'pads', pad_i, 57 + tonic % 12 + iv, 20 * s16, 0.55, (k - 1) * 0.4))
        evs.append((20 * s16, 'drums', 'boom', None, s16, 0.6, 0.0))
    evs = [e + (i % 3,) for i, e in enumerate(evs)]
    W = max(e[0] + e[4] for e in evs) + 4.5
    song = {'cfg': {'reverb': P['reverb'], 'sends': P['sends'], 'delay': P['delay'], 'sidechain': {}, 'master_lp': None},
            'bpm': bpm, 'bar_s': 240.0 / bpm, 'style': preset, 'bed': False, 'bed_boost': 0}
    y, _, _ = _render_numpy(song, evs, W, False, targets=CUE_TARGETS['victory'])
    y = y.astype(np.float64)
    env = np.max(np.abs(y), axis=0)
    last = int(np.nonzero(env > np.max(env) * 10 ** (-48 / 20.0))[0][-1]) + 1
    y = y[:, :last]
    k = min(int(0.6 * SR), y.shape[1] // 3)
    y[:, -k:] *= np.linspace(1, 0, k) ** 2
    from_active = _active_lufs(y)
    y = _soft_clip(y * 10 ** ((target_lufs - from_active) / 20.0))
    info = {'kind': kind, 'preset': preset, 'key': '%s %s' % (NOTE_NAMES[tonic], md), 'bpm': bpm,
            'len_s': round(y.shape[1] / float(SR), 4), 'sync': 'beat', 'array': y.astype(np.float32)}
    if write:
        out_dir = os.path.abspath(out_dir or os.path.join(OUT_DIR, 'score'))
        os.makedirs(out_dir, exist_ok=True)
        p = os.path.join(out_dir, 'stinger_%s.wav' % kind)
        _write_wav(p, y, None, 'stinger %s' % kind, seed)
        info['path'] = p
    return info


def _active_lufs(x):
    """Loudness of the audible part (one-shots are shorter than the gated BS.1770 window would like)."""
    x = np.asarray(x, dtype=np.float64)
    env = np.max(np.abs(x), axis=0)
    idx = np.nonzero(env > np.max(env) * 0.03)[0]
    if idx.size == 0:
        return -70.0
    seg = x[:, idx[0]:idx[-1] + 1]
    return lufs(seg)


def compose_score(preset='orchestral', key='D', mode='major', bpm=None, seed=0, region='world', motif=None,
                  states=SCORE_STATES, stingers=STINGERS, bars=None, out_dir=None, write=True, progress=None):
    """Every mood cue + stinger for one region -> manifest dict (arrays kept under '_arrays' for the packer)."""
    t0 = time.time()
    motif = motif or region_motif(region, seed)
    man = {'engine': 'music_engine %s' % VERSION, 'preset': preset_for_style(preset), 'key': key, 'mode': mode,
           'bpm': float(bpm or CUE[preset_for_style(preset)]['bpm']), 'region': region, 'motif': motif, 'seed': seed,
           'cues': {}, 'stingers': {}}
    for st in states:
        if progress:
            progress('cue', st)
        b = (bars or {}).get(st) if isinstance(bars, dict) else bars
        info = compose_cue(st, preset, key, mode, bpm, b if st != 'victory' else None, seed, motif, region, out_dir,
                           write=write)
        man['cues'][st] = info
    for k in stingers:
        if progress:
            progress('stinger', k)
        man['stingers'][k] = compose_stinger(k, preset, key, mode, bpm, seed, motif, region, out_dir, write=write)
    man['render_s'] = round(time.time() - t0, 2)
    return man


# =============================================================================
# Verification + demo
# =============================================================================
def _ffprobe(path):
    fp = shutil.which('ffprobe')
    if not fp:
        return None
    p = subprocess.run([fp, '-v', 'error', '-show_entries', 'stream=sample_rate,channels,codec_name:format=duration',
                        '-of', 'json', path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        j = json.loads(p.stdout.decode())
        s = j['streams'][0]
        return {'sr': int(s['sample_rate']), 'ch': int(s['channels']), 'codec': s['codec_name'],
                'dur': float(j['format']['duration'])}
    except Exception:
        return None


def _read_pcm(path):
    import wave
    with wave.open(path, 'rb') as w:
        n = w.getnframes()
        raw = w.readframes(n)
    a = np.frombuffer(raw, '<i2').reshape(-1, 2).T.astype(np.float64) / 32768.0
    return a


def check_file(path, expect_s=None, target=TARGET_LUFS):
    """Validate one WAV: format, duration, loudness, clipping, loop continuity."""
    r = {'file': os.path.basename(path)}
    pr = _ffprobe(path)
    r['probe'] = pr
    x = _read_pcm(path)
    r['dur'] = round(x.shape[1] / SR, 4)
    r['peak_dbfs'] = round(20 * math.log10(max(float(np.max(np.abs(x))), 1e-9)), 2)
    r['clipped_samples'] = int(np.sum(np.abs(x) >= 0.999))
    ff = ffmpeg_loudness(x)
    r['lufs_ffmpeg'] = round(ff[0], 2) if ff else None
    r['tp_dbtp'] = round(ff[1], 2) if ff else None
    ok = bool(pr and pr['sr'] == SR and pr['ch'] == 2 and r['clipped_samples'] == 0)
    if expect_s is not None:
        r['dur_err_ms'] = round((r['dur'] - expect_s) * 1000, 2)
        ok = ok and abs(r['dur_err_ms']) <= 1.0
    lp = read_wav_loop(path)
    if lp:
        s, e = lp
        # continuity across the wrap vs. typical sample-to-sample movement inside the loop
        seg = x[:, s:e]
        d_in = np.abs(np.diff(seg, axis=1))
        typical = float(np.percentile(d_in, 99.9))
        jump = float(np.max(np.abs(x[:, e - 1] - x[:, s])))
        # spectral/energy continuity: RMS of 50 ms before end vs after start
        w = int(0.05 * SR)
        rms_end = float(np.sqrt(np.mean(x[:, e - w:e] ** 2)))
        rms_start = float(np.sqrt(np.mean(x[:, s:s + w] ** 2)))
        entry = float(np.max(np.abs(x[:, s] - x[:, s - 1]))) if s > 0 else typical
        r['loop'] = {'start_s': round(s / SR, 4), 'end_s': round(e / SR, 4), 'wrap_jump': round(jump, 5),
                     'entry_step': round(entry, 5), 'p99.9_step': round(typical, 5),
                     'rms_end': round(rms_end, 4), 'rms_start': round(rms_start, 4),
                     'seamless': jump <= max(typical, 1.5 * entry + 0.01, 1e-4)}
        ok = ok and r['loop']['seamless']
    if target is not None and r['lufs_ffmpeg'] is not None:
        r['lufs_err'] = round(r['lufs_ffmpeg'] - target, 2)
        ok = ok and abs(r['lufs_err']) <= 1.0
    r['ok'] = ok
    return r


def demo(out_dir=DEMO_DIR, seconds=60, verify=True):
    """Write the demo set: every style, every world mood, every sfx, beat beds, one stem set."""
    os.makedirs(out_dir, exist_ok=True)
    report = {'tracks': [], 'worlds': [], 'sfx': [], 'beds': [], 'engine': VERSION, 'numpy': HAVE_NUMPY}
    for i, st in enumerate(STYLE_DEFAULTS):
        p = os.path.join(out_dir, 'style_%s.wav' % st)
        info = compose_track(st, seconds=seconds, seed=11 + i, out=p, stems=(st == 'synthwave'), loop_file=True)
        print('  %-10s %-11s %-12s %5.1f bpm  %5.2f LUFS  render %.2fs' % (st, info['mood'], info['key'], info['bpm'], info['lufs'], info['render_s']))
        report['tracks'].append(info)
    for i, w in enumerate(WORLD_MOODS):
        p = os.path.join(out_dir, 'world_%s.wav' % w.lower().replace(' ', '_').replace('-', ''))
        info = compose_track(world=w, style=None, seconds=45, seed=21 + i, out=p)
        print('  world %-24s -> %-9s %s  render %.2fs' % (w, info['style'], info['key'], info['render_s']))
        report['worlds'].append(info)
    for k in SFX_KINDS:
        t0 = time.time()
        p = sfx(k, seed=1, out=os.path.join(out_dir, 'sfx_%s.wav' % k))
        report['sfx'].append({'kind': k, 'path': p, 'render_s': round(time.time() - t0, 3)})
    for st, bpm in (('synthwave', 120), ('cozy', 84), ('battle', 150), ('chiptune', 140), ('adventure', 100), ('ambient', 72)):
        p = background_bed(30, bpm, st, seed=5, out=os.path.join(out_dir, 'bed_%s_%d.wav' % (st, bpm)))
        report['beds'].append({'style': st, 'bpm': bpm, 'path': p})
    if verify:
        report['verify'] = verify_dir(out_dir, report)
    with open(os.path.join(out_dir, 'demo_report.json'), 'w', encoding='utf-8') as fh:
        json.dump(report, fh, indent=2, default=str)
    return report


def verify_dir(out_dir, report=None):
    res = {'files': [], 'beats': []}
    for name in sorted(os.listdir(out_dir)):
        if not name.endswith('.wav'):
            continue
        p = os.path.join(out_dir, name)
        side = p[:-4] + '.json'
        exp = None
        if os.path.isfile(side):
            try:
                exp = json.load(open(side, encoding='utf-8')).get('seconds')
            except Exception:
                exp = None
        is_sfx = name.startswith('sfx_')
        r = check_file(p, exp, None if (is_sfx or '_stem_' in name) else TARGET_LUFS)
        res['files'].append(r)
        print('  %-44s %s %5.2fs peak %6.2f dBFS  LUFS %6s  %s%s' % (
            name, 'OK ' if r['ok'] else 'BAD', r['dur'], r['peak_dbfs'], r['lufs_ffmpeg'],
            ('wrap step %.4f (entry %.4f, p99.9 %.4f)' % (r['loop']['wrap_jump'], r['loop']['entry_step'], r['loop']['p99.9_step'])) if r.get('loop') else '',
            (' dur_err %sms' % r['dur_err_ms']) if 'dur_err_ms' in r else ''))
    try:
        sys.path.insert(0, ROOT)
        import beat_engine
        for name in sorted(os.listdir(out_dir)):
            if name.startswith('bed_') and name.endswith('.wav'):
                req = float(name.split('_')[2].split('.')[0])
                a = beat_engine.analyze(os.path.join(out_dir, name))
                got = a['tempo_bpm']
                res['beats'].append({'file': name, 'requested': req, 'detected': got, 'err_pct': round(100 * (got - req) / req, 2)})
                print('  BPM %-28s requested %6.1f detected %6.2f (%+.2f%%)' % (name, req, got, 100 * (got - req) / req))
    except Exception as e:  # pragma: no cover
        res['beats_error'] = repr(e)
    return res


def _main(argv):
    import argparse
    ap = argparse.ArgumentParser(description='Stark World Engine procedural music + SFX (100% synthesized, royalty-free)')
    ap.add_argument('--demo', action='store_true', help='write the demo set into _music_demo/')
    ap.add_argument('--verify', nargs='?', const=DEMO_DIR, help='check every WAV in a folder')
    ap.add_argument('--style', default=None, help=', '.join(STYLE_DEFAULTS))
    ap.add_argument('--world', default=None, help=', '.join(WORLD_MOODS))
    ap.add_argument('--mood', default=None)
    ap.add_argument('--bpm', type=float, default=None)
    ap.add_argument('--seconds', type=float, default=60)
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--key', default=None, help='e.g. D, F#, "A minor", "D dorian"')
    ap.add_argument('--stems', action='store_true')
    ap.add_argument('--loop-file', action='store_true')
    ap.add_argument('--sfx', default=None, help=', '.join(SFX_KINDS))
    ap.add_argument('--bed', type=float, default=None, help='background bed duration (s); uses --bpm, --style')
    ap.add_argument('--lufs', type=float, default=TARGET_LUFS)
    ap.add_argument('--out', default=None)
    ap.add_argument('--cue', default=None, help=', '.join(SCORE_STATES))
    ap.add_argument('--score', action='store_true', help='every cue + stinger for one region')
    ap.add_argument('--preset', default='orchestral', help=', '.join(PRESETS))
    ap.add_argument('--bars', type=int, default=None)
    ap.add_argument('--region', default='world')
    a = ap.parse_args(argv)
    if a.cue or a.score:
        key = a.key or 'D'
        mode = None
        if ' ' in key:
            key, mode = key.split(' ', 1)
        if a.score:
            man = compose_score(a.preset, key, mode or 'major', a.bpm, a.seed, a.region, out_dir=a.out)
            print(json.dumps({k: {s: v.get('files', v.get('path')) for s, v in man[k].items()} for k in ('cues', 'stingers')},
                             indent=1, default=str))
        else:
            info = compose_cue(a.cue, a.preset, key, mode or 'major', a.bpm, a.bars, a.seed, None, a.region, a.out)
            info.pop('_arrays', None)
            print(json.dumps({k: info[k] for k in ('files', 'key', 'bpm', 'bars', 'progression', 'voice_leading',
                                                   'loop_wrap_error_db', 'render_s')}, indent=1))
        return 0
    if a.demo:
        t0 = time.time()
        print('Stark World Engine music_engine %s demo -> %s (numpy=%s)' % (VERSION, DEMO_DIR, HAVE_NUMPY))
        demo()
        print('done in %.1fs' % (time.time() - t0))
        return 0
    if a.verify:
        verify_dir(a.verify)
        return 0
    if a.sfx:
        print(sfx(a.sfx, a.seed, a.out))
        return 0
    if a.bed:
        print(background_bed(a.bed, a.bpm or 120, a.style or 'synthwave', a.seed, a.mood, a.out, a.lufs))
        return 0
    if a.world or a.style:
        info = compose_track(a.style, a.mood, a.bpm, a.seconds, a.seed, a.key, stems=a.stems, out=a.out,
                             loop_file=a.loop_file, target_lufs=a.lufs, world=a.world)
        print(json.dumps({k: info[k] for k in ('path', 'style', 'mood', 'key', 'bpm', 'loop', 'lufs', 'render_s', 'stems')}, indent=2))
        return 0
    ap.print_help()
    return 1


if __name__ == '__main__':
    sys.exit(_main(sys.argv[1:]))
