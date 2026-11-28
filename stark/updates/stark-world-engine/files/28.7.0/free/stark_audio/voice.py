"""Voice lines: script extraction, take storage, the dialogue processing chain, character voice profiles,
batch import, and the entries the cartridge packer embeds.

Line ids match the cartridge runtime: '<dialog id>/<node id>' (dialogs merge across levels exactly like the game
does), and 'npc/<npc id>' for an NPC's fallback line. Files live in My_Games/<game>/voice/:
  voice.json          takes, chosen take, trims, character profiles, chain settings (the studio never writes it)
  takes/<line>__<n>.<ext>   recordings exactly as received (never modified)
  lines/<line>.wav    the processed chosen take (48 kHz mono 16-bit) + <line>.json (visemes, subtitles, report)

Chain (each step optional): decode -> manual trim -> DC + high-pass 80 Hz -> noise gate -> light de-ess ->
auto silence trim -> voice profile (pitch / formant / EQ / colour) -> loudness to the dialogue target (-18 LUFS)
-> true-peak-safe limiter (-1 dBFS).
Consent: recordings only ever come from the creator's own microphone or files they pick; nothing leaves the PC.
"""
from __future__ import annotations

import base64
import hashlib
import math
import os
import re
import time

import numpy as np

from . import dsp, lipsync, subtitles

SR = dsp.SR
DIALOGUE_LUFS = -18.0
AUDIO_EXT = ('.wav', '.flac', '.mp3', '.ogg', '.oga', '.opus', '.webm', '.m4a', '.aac')
MAX_TAKES = 12
CHAIN_DEFAULT = {'hpf': True, 'gate': True, 'deess': True, 'trim': True, 'normalize': True, 'target': DIALOGUE_LUFS}

# Character voice profiles: one person can voice a whole cast. pitch in semitones, formant as a ratio
# (>1 smaller/younger vocal tract, <1 bigger), eq: low/high shelves + presence (dB), colour effects, TTS hints.
PROFILES = {
    'natural': {'pitch': 0.0, 'formant': 1.0, 'eq': {}, 'fx': None, 'tts': (1.0, 1.0), 'label': 'Natural (as recorded)'},
    'deeper': {'pitch': -2.0, 'formant': 0.94, 'eq': {'low': 2.0}, 'fx': None, 'tts': (0.75, 0.95), 'label': 'Deeper'},
    'higher': {'pitch': 2.5, 'formant': 1.06, 'eq': {'high': 1.0}, 'fx': None, 'tts': (1.25, 1.02), 'label': 'Higher'},
    'giant': {'pitch': -5.0, 'formant': 0.84, 'eq': {'low': 4.0, 'high': -3.0}, 'fx': 'room', 'tts': (0.45, 0.85), 'label': 'Giant / ogre'},
    'goblin': {'pitch': 5.0, 'formant': 1.18, 'eq': {'presence': 3.0, 'low': -3.0}, 'fx': None, 'tts': (1.7, 1.15), 'label': 'Goblin / imp'},
    'child': {'pitch': 4.0, 'formant': 1.15, 'eq': {'low': -2.0}, 'fx': None, 'tts': (1.5, 1.05), 'label': 'Child'},
    'elder': {'pitch': -1.5, 'formant': 0.96, 'eq': {'high': -3.0, 'presence': 1.0}, 'fx': 'tremble', 'tts': (0.8, 0.85), 'label': 'Elder'},
    'spirit': {'pitch': 1.0, 'formant': 1.03, 'eq': {'low': -4.0, 'high': 2.0}, 'fx': 'spirit', 'tts': (1.1, 0.9), 'label': 'Spirit / ghost'},
    'radio': {'pitch': 0.0, 'formant': 1.0, 'eq': {}, 'fx': 'radio', 'tts': (1.0, 1.0), 'label': 'Radio / far-speaker'},
    'monster': {'pitch': -7.0, 'formant': 0.8, 'eq': {'low': 5.0}, 'fx': 'growl', 'tts': (0.3, 0.8), 'label': 'Monster'},
}


# ---------------------------------------------------------------------------------------------------------------
# script
# ---------------------------------------------------------------------------------------------------------------
def lines(world):
    """Every spoken line of a world in play order-ish: [{id, speaker, text, dialog, node, level, npc}]."""
    levels = [world] + list(world.get('levels') or [])
    merged, where = {}, {}
    for i, L in enumerate(levels):
        for did, d in (L.get('dialogs') or {}).items():
            merged[did] = d
            where[did] = i
    npc_names = {}
    out, seen = [], set()
    for i, L in enumerate(levels):
        for o in L.get('objects') or []:
            if o.get('t') != 'npc':
                continue
            nid = o.get('id') or o.get('name')
            npc_names[o.get('dialog') or ''] = o.get('name') or ''
            if (not o.get('dialog') or o.get('dialog') not in merged) and nid and o.get('text'):
                lid = 'npc/%s' % nid
                if lid not in seen:
                    seen.add(lid)
                    out.append({'id': lid, 'speaker': o.get('name') or nid, 'text': o['text'], 'dialog': None,
                                'node': None, 'level': i, 'npc': nid})
    for did, d in merged.items():
        nodes = d.get('nodes') or {}
        order = [d.get('start')] + [b.get('node') for b in (d.get('branches') or [])] + list(nodes)
        for nid in order:
            if not nid or nid not in nodes:
                continue
            lid = '%s/%s' % (did, nid)
            if lid in seen:
                continue
            n = nodes[nid]
            text = (n.get('text') or '').strip()
            if not text:
                continue
            seen.add(lid)
            out.append({'id': lid, 'speaker': n.get('speaker') or npc_names.get(did) or '', 'text': text,
                        'dialog': did, 'node': nid, 'level': where[did], 'npc': None})
    return out


def line_file(line_id):
    """Line id -> safe file stem ('moss/hi' -> 'moss--hi'); a hash suffix keeps odd ids unique."""
    s = re.sub(r'[^A-Za-z0-9_.-]+', '-', line_id.replace('/', '--')).strip('-.')[:80] or 'line'
    if s != line_id.replace('/', '--'):
        s += '-' + hashlib.sha1(line_id.encode()).hexdigest()[:6]
    return s


def script_text(world, fmt='txt'):
    """A recording script for voice actors: id, file name to deliver, speaker, text."""
    ls = lines(world)
    if fmt == 'csv':
        rows = ['index,line_id,file_name,speaker,text']
        for i, l in enumerate(ls):
            rows.append('%d,%s,%s.wav,"%s","%s"' % (i + 1, l['id'], line_file(l['id']), l['speaker'].replace('"', "'"),
                                                    l['text'].replace('"', "'")))
        return '\n'.join(rows) + '\n'
    out = ['Recording script - %s' % (world.get('name') or 'game'), '',
           'Deliver one file per line, named exactly as shown (any of WAV, FLAC, MP3, OGG).', '']
    for i, l in enumerate(ls):
        out.append('%03d  %s.wav   [%s]' % (i + 1, line_file(l['id']), l['speaker'] or 'narrator'))
        out.append('     %s' % l['text'])
        out.append('')
    return '\n'.join(out)


# ---------------------------------------------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------------------------------------------
def _vdir(world_dir):
    return os.path.join(world_dir, 'voice')


def load(world_dir):
    m = dsp.load_json(os.path.join(_vdir(world_dir), 'voice.json'), None) if world_dir else None
    if not isinstance(m, dict):
        m = {}
    m.setdefault('version', 1)
    m.setdefault('lines', {})
    m.setdefault('characters', {})
    m.setdefault('chain', dict(CHAIN_DEFAULT))
    return m


def save(world_dir, m):
    return dsp.save_json(os.path.join(_vdir(world_dir), 'voice.json'), m)


def _ext_for(mime, name):
    e = os.path.splitext(name or '')[1].lower()
    if e in AUDIO_EXT:
        return e
    mm = (mime or '').lower()
    for key, ext in (('webm', '.webm'), ('ogg', '.ogg'), ('opus', '.ogg'), ('wav', '.wav'), ('mpeg', '.mp3'),
                     ('mp3', '.mp3'), ('flac', '.flac'), ('mp4', '.m4a'), ('aac', '.m4a')):
        if key in mm:
            return ext
    return '.webm'


def add_take(world_dir, line_id, data, mime='audio/webm', source='mic', name=None):
    """Store a recording as received; returns (take index, quick analysis)."""
    if not data or len(data) < 64:
        raise ValueError('empty recording')
    if len(data) > 40 * 1024 * 1024:
        raise ValueError('recording too large (40 MB max)')
    x = dsp.decode(bytes(data))                       # validates that it is real audio
    if len(x) < int(0.05 * SR):
        raise ValueError('recording is shorter than 50 ms')
    m = load(world_dir)
    ent = m['lines'].setdefault(line_id, {'takes': [], 'best': None})
    if len(ent['takes']) >= MAX_TAKES:
        raise ValueError('at most %d takes per line: delete one first' % MAX_TAKES)
    os.makedirs(os.path.join(_vdir(world_dir), 'takes'), exist_ok=True)
    k = 1 + max([t.get('n', 0) for t in ent['takes']] + [0])
    fn = '%s__%d%s' % (line_file(line_id), k, _ext_for(mime, name))
    with open(os.path.join(_vdir(world_dir), 'takes', fn), 'wb') as fh:
        fh.write(bytes(data))
    info = quick_info(x)
    ent['takes'].append({'n': k, 'file': fn, 'created': int(time.time()), 'source': source, 'name': name or '',
                         'dur': info['dur'], 'peak_db': info['peak_db'], 'clipped': info['clipped'],
                         'noise_db': info['noise_db'], 'trim': None})
    if ent.get('best') is None:
        ent['best'] = len(ent['takes']) - 1
    save(world_dir, m)
    return len(ent['takes']) - 1, info


def quick_info(x):
    x = np.asarray(x, dtype=np.float64)
    hop = int(0.02 * SR)
    n = max(1, len(x) // hop)
    rms = 20 * np.log10(np.sqrt((x[:n * hop].reshape(n, hop) ** 2).mean(axis=1)) + 1e-9) if len(x) >= hop else np.array([-90.0])
    pk = float(np.max(np.abs(x))) if len(x) else 0.0
    return {'dur': round(len(x) / SR, 3), 'peak_db': round(dsp.db(pk), 1), 'clipped': int(np.sum(np.abs(x) >= 0.995)),
            'noise_db': round(float(np.percentile(rms, 10)), 1), 'speech_db': round(float(np.percentile(rms, 90)), 1)}


def take_path(world_dir, line_id, take=None):
    m = load(world_dir)
    ent = m['lines'].get(line_id)
    if not ent or not ent['takes']:
        return None
    i = ent.get('best') if take is None else int(take)
    if i is None or not (0 <= i < len(ent['takes'])):
        return None
    return os.path.join(_vdir(world_dir), 'takes', ent['takes'][i]['file'])


def set_best(world_dir, line_id, take):
    m = load(world_dir)
    ent = m['lines'].get(line_id)
    if not ent or not (0 <= int(take) < len(ent['takes'])):
        raise ValueError('no such take')
    ent['best'] = int(take)
    save(world_dir, m)


def set_trim(world_dir, line_id, take, start, end):
    m = load(world_dir)
    ent = m['lines'].get(line_id)
    if not ent or not (0 <= int(take) < len(ent['takes'])):
        raise ValueError('no such take')
    ent['takes'][int(take)]['trim'] = None if start is None else [max(0.0, float(start)), float(end)]
    save(world_dir, m)


def delete_take(world_dir, line_id, take):
    m = load(world_dir)
    ent = m['lines'].get(line_id)
    if not ent or not (0 <= int(take) < len(ent['takes'])):
        raise ValueError('no such take')
    t = ent['takes'].pop(int(take))
    try:
        os.remove(os.path.join(_vdir(world_dir), 'takes', t['file']))
    except OSError:
        pass
    b = ent.get('best')
    if b is not None:
        ent['best'] = None if not ent['takes'] else (b - 1 if b > int(take) else (min(b, len(ent['takes']) - 1) if b == int(take) else b))
    save(world_dir, m)


def set_profile(world_dir, speaker, profile='natural', custom=None):
    if profile not in PROFILES:
        raise ValueError('unknown voice profile %r (use %s)' % (profile, ', '.join(PROFILES)))
    m = load(world_dir)
    m['characters'][speaker] = {'profile': profile, 'custom': _clean_custom(custom)}
    save(world_dir, m)


def _clean_custom(c):
    if not c:
        return None
    out = {}
    for k, lo, hi in (('pitch', -12, 12), ('formant', 0.7, 1.4), ('low', -12, 12), ('high', -12, 12), ('presence', -12, 12)):
        if c.get(k) is not None:
            out[k] = max(lo, min(hi, float(c[k])))
    return out or None


def profile_params(m, speaker):
    ch = (m.get('characters') or {}).get(speaker) or {}
    p = dict(PROFILES.get(ch.get('profile') or 'natural', PROFILES['natural']))
    p['eq'] = dict(p['eq'])
    for k, v in (ch.get('custom') or {}).items():
        if k in ('pitch', 'formant'):
            p[k] = v
        else:
            p['eq'][k] = v
    p['name'] = ch.get('profile') or 'natural'
    return p


# ---------------------------------------------------------------------------------------------------------------
# processing chain
# ---------------------------------------------------------------------------------------------------------------
def _frames_db(x, hop):
    n = max(1, len(x) // hop)
    fr = x[:n * hop].reshape(n, hop) if len(x) >= hop else np.pad(x, (0, hop - len(x)))[None, :]
    return 20 * np.log10(np.sqrt((fr ** 2).mean(axis=1)) + 1e-9)


def _smooth_gain(gf, hop, n, attack, release):
    """Per-frame target gains -> per-sample gain with asymmetric one-pole smoothing (attack opens fast)."""
    g = np.empty(len(gf))
    cur = gf[0]
    aa = math.exp(-hop / (attack * SR))
    ar = math.exp(-hop / (release * SR))
    for i, v in enumerate(gf):
        c = aa if v > cur else ar
        cur = c * cur + (1 - c) * v
        g[i] = cur
    xs = (np.arange(len(g)) + 0.5) * hop
    return np.interp(np.arange(n), xs, g)


def noise_gate(x, open_db=None, range_db=-24.0, hold=0.08, attack=0.002, release=0.12):
    """Downward expander/gate: frames below the threshold are reduced by range_db (natural, never chopped)."""
    hop = int(0.005 * SR)
    lv = _frames_db(x, hop)
    noise = float(np.percentile(lv, 10))
    speech = float(np.percentile(lv, 95))
    thr = open_db if open_db is not None else max(noise + 8.0, min(speech - 30.0, -38.0))
    if speech - noise < 12:                            # no clear speech/noise split: leave it alone
        return x.copy(), {'gate_threshold_db': round(thr, 1), 'gated_pct': 0.0, 'noise_db': round(noise, 1)}
    opened = lv > thr
    h = int(hold / 0.005)
    state = np.zeros(len(lv), bool)
    last = -10 ** 9
    for i in range(len(lv)):
        stay = i > 0 and state[i - 1] and lv[i] > thr - 4.0          # 4 dB hysteresis keeps word endings
        if opened[i] or stay:
            last = i
        state[i] = i - last <= h
    gf = np.where(state, 1.0, dsp.undb(range_db))
    g = _smooth_gain(gf, hop, len(x), attack, release)
    return x * g, {'gate_threshold_db': round(thr, 1), 'gated_pct': round(100.0 * float(np.mean(~state)), 1),
                   'noise_db': round(noise, 1)}


def deess(x, freq=5200.0, ratio_db=-8.0, max_cut=6.0):
    """Light split-band de-esser: when the >5 kHz band dominates (sibilance), turn that band down (max 6 dB)."""
    hi = dsp.filt(x, lo=freq, order=4)
    lo = x - hi
    hop = int(0.005 * SR)
    eh = _frames_db(hi, hop)
    ef = _frames_db(x, hop)
    over = (eh - ef) - ratio_db                          # how far sibilance exceeds the allowed balance
    active = (ef > np.percentile(ef, 95) - 35)
    cut = np.clip(over, 0, max_cut) * active
    g = _smooth_gain(dsp.undb(-cut), hop, len(x), 0.001, 0.04)
    return lo + hi * g, {'deess_max_db': round(float(cut.max()) if len(cut) else 0.0, 1),
                         'deess_pct': round(100.0 * float(np.mean(cut > 0.5)), 1)}


def auto_trim(x, pre=0.1, post=0.18):
    """Cut leading/trailing silence (keeps a breath of air either side) -> (x, (start_s, end_s))."""
    hop = int(0.01 * SR)
    lv = _frames_db(x, hop)
    if not len(lv):
        return x, (0.0, len(x) / SR)
    top = np.percentile(lv, 95)
    noise = np.percentile(lv, 10)
    thr = max(noise + 10.0, top - 40.0)
    idx = np.nonzero(lv > thr)[0]
    if not idx.size:
        return x, (0.0, len(x) / SR)
    a = max(0, idx[0] * hop - int(pre * SR))
    b = min(len(x), (idx[-1] + 1) * hop + int(post * SR))
    y = x[a:b].copy()
    k = min(len(y) // 4, int(0.005 * SR))
    if k:
        y[:k] *= np.linspace(0, 1, k)
    k2 = min(len(y) // 4, int(0.03 * SR))
    if k2:
        y[-k2:] *= np.linspace(1, 0, k2)
    return y, (round(float(a) / SR, 3), round(float(b) / SR, 3))


def _envelope(mag, lifter=90):            # ~1.9 ms: holds the formants, drops harmonics for f0 below ~530 Hz
    N = (mag.shape[-1] - 1) * 2
    cep = np.fft.irfft(np.log(mag + 1e-9), N, axis=-1)
    cep[..., lifter:N - lifter] = 0
    return np.exp(np.fft.rfft(cep, N, axis=-1).real)


def pitch_formant(x, semitones=0.0, formant=1.0, nfft=2048, hop=256):
    """Phase-vocoder pitch shift with independent formant (spectral envelope) shift. Duration is kept."""
    if abs(semitones) < 0.01 and abs(formant - 1.0) < 0.005:
        return x.copy()
    r = 2.0 ** (semitones / 12.0)
    x = np.asarray(x, dtype=np.float64)
    n = len(x)
    from . import analyze
    f0 = analyze.pitch_estimate(x, fmin=60, fmax=500, lowpass=600) or 140.0
    lifter = int(max(60, min(400, 0.7 * SR / f0)))         # envelope keeps formants, never the harmonics
    win = np.hanning(nfft + 1)[:-1]
    pad = np.concatenate([np.zeros(nfft), x, np.zeros(nfft + hop)])
    nf = (len(pad) - nfft) // hop + 1
    K = nfft // 2 + 1
    k = np.arange(K)
    expected = 2 * np.pi * hop * k / nfft
    prev_ph = np.zeros(K)
    out_ph = np.zeros(K)
    y = np.zeros(len(pad) + nfft)
    wsum = np.zeros(len(pad) + nfft)
    src_bins = k / r
    for f in range(nf):
        fr = pad[f * hop:f * hop + nfft] * win
        X = np.fft.rfft(fr)
        mag, ph = np.abs(X), np.angle(X)
        dphi = ph - prev_ph - expected
        prev_ph = ph
        dphi -= 2 * np.pi * np.round(dphi / (2 * np.pi))
        true_bin = k + dphi * nfft / (2 * np.pi * hop)
        env = _envelope(mag, lifter)
        fine = mag / env
        # new spectrum: fine structure read from k/r, envelope from k/formant
        fine2 = np.interp(src_bins, k, fine, right=0.0)
        tb2 = np.interp(src_bins, k, true_bin, right=0.0) * r
        env2 = np.interp(k / formant, k, env, right=env[-1] * 0.01)
        mag2 = fine2 * env2
        out_ph += 2 * np.pi * hop * tb2 / nfft
        Y = mag2 * np.exp(1j * out_ph)
        yf = np.fft.irfft(Y, nfft) * win
        y[f * hop:f * hop + nfft] += yf
        wsum[f * hop:f * hop + nfft] += win ** 2
    y = y / np.maximum(wsum, 1e-6)
    return y[nfft:nfft + n]


def apply_profile(x, p):
    y = pitch_formant(x, p.get('pitch', 0.0), p.get('formant', 1.0))
    eq = p.get('eq') or {}
    shelf, peak = [], []
    if eq.get('low'):
        shelf.append((220.0, eq['low'], 'low'))
    if eq.get('high'):
        shelf.append((5000.0, eq['high'], 'high'))
    if eq.get('presence'):
        peak.append((3000.0, eq['presence'], 1.2))
    if shelf or peak:
        y = dsp.eq(y, shelf=shelf or None, peak=peak or None)
    fx = p.get('fx')
    if fx == 'radio':
        y = np.tanh(2.0 * dsp.filt(y, lo=320, hi=3300, order=3) / (np.max(np.abs(y)) + 1e-9)) * 0.5
    elif fx == 'spirit':
        d = int(0.018 * SR)
        t = np.arange(len(y)) / SR
        mod = (d + 0.003 * SR * np.sin(2 * np.pi * 0.6 * t)).astype(int)
        idx = np.clip(np.arange(len(y)) - mod, 0, len(y) - 1)
        y = 0.7 * y + 0.5 * y[idx]
        y = dsp.reverb(y, 2.6, 0.45, seed=91).mean(axis=0)
    elif fx == 'room':
        y = dsp.reverb(y, 0.9, 0.18, seed=92).mean(axis=0)
    elif fx == 'tremble':
        t = np.arange(len(y)) / SR
        y = y * (1 + 0.06 * np.sin(2 * np.pi * 5.5 * t))
    elif fx == 'growl':
        sub = pitch_formant(y, -12.0, 1.0)
        y = np.tanh(1.8 * (y + 0.4 * sub) / (np.max(np.abs(y)) + 1e-9)) * 0.6
    return y


def process(x, chain=None, profile=None, trim=None):
    """The full dialogue chain on a mono float array -> (processed, report)."""
    c = dict(CHAIN_DEFAULT)
    c.update(chain or {})
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 2:
        x = x.mean(axis=0)
    rep = {'in': quick_info(x)}
    if trim:
        a, b = int(max(0.0, trim[0]) * SR), int(min(len(x) / SR, trim[1]) * SR)
        if b - a > int(0.05 * SR):
            x = x[a:b]
    x = x - np.mean(x)
    if c.get('hpf'):
        x = dsp.filt(x, lo=80.0, order=2)
    if c.get('gate'):
        x, r = noise_gate(x)
        rep.update(r)
    if c.get('deess'):
        x, r = deess(x)
        rep.update(r)
    if c.get('trim'):
        x, span = auto_trim(x)
        rep['trimmed'] = span
    if profile and profile.get('name', 'natural') != 'natural':
        x = apply_profile(x, profile)
        x = x - np.mean(x)
    if c.get('normalize'):
        target = float(c.get('target', DIALOGUE_LUFS))
        lu = dsp.lufs(x)
        if math.isfinite(lu) and lu > -70:
            x = x * dsp.undb(target - lu)
        x = dsp.limit_tp(dsp.limit(x, dsp.undb(-1.5), 0.8), -1.0)
        rep['lufs'] = round(float(dsp.lufs(x)), 2)
        rep['true_peak_db'] = round(dsp.db(dsp.true_peak(x)), 2)
    rep['out'] = quick_info(x)
    return x, rep


def process_line(world_dir, line_id, text=None, speaker=None, world=None):
    """Process the chosen take of a line -> writes lines/<file>.wav + .json (visemes + subtitles)."""
    m = load(world_dir)
    ent = m['lines'].get(line_id)
    if not ent or ent.get('best') is None or not ent['takes']:
        raise ValueError('no take recorded for %s' % line_id)
    tk = ent['takes'][ent['best']]
    src = os.path.join(_vdir(world_dir), 'takes', tk['file'])
    x = dsp.decode(src)
    if world is not None and (text is None or speaker is None):
        for l in lines(world):
            if l['id'] == line_id:
                text = l['text'] if text is None else text
                speaker = l['speaker'] if speaker is None else speaker
    prof = profile_params(m, speaker or '')
    y, rep = process(x, m.get('chain'), prof, tk.get('trim'))
    keys = lipsync.visemes(y, text)
    subs = subtitles.auto_time(text or '', y)
    d = os.path.join(_vdir(world_dir), 'lines')
    os.makedirs(d, exist_ok=True)
    stem = line_file(line_id)
    dsp.write_wav(os.path.join(d, stem + '.wav'), y, title=line_id)
    meta = {'line': line_id, 'take': tk['file'], 'speaker': speaker or '', 'text': text or '', 'profile': prof['name'],
            'dur': round(len(y) / SR, 3), 'visemes': lipsync.compact(keys), 'subs': subs, 'report': rep,
            'made': int(time.time())}
    dsp.save_json(os.path.join(d, stem + '.json'), meta)
    m = load(world_dir)
    m['lines'].setdefault(line_id, ent)['processed'] = {'file': stem + '.wav', 'take': tk['file'], 'dur': meta['dur'],
                                                         'lufs': rep.get('lufs'), 'profile': prof['name']}
    save(world_dir, m)
    return meta


# ---------------------------------------------------------------------------------------------------------------
# batch import
# ---------------------------------------------------------------------------------------------------------------
def _norm(s):
    return re.sub(r'[^a-z0-9]', '', s.lower())


def match_file(name, world_lines):
    """File name -> line id: exact delivered name, normalised line id, script index (001_...), or text prefix."""
    stem = os.path.splitext(os.path.basename(name))[0]
    ns = _norm(stem)
    by_file = {line_file(l['id']).lower(): l['id'] for l in world_lines}
    if stem.lower() in by_file:
        return by_file[stem.lower()], 'file name'
    for l in world_lines:
        for v in (l['id'], l['id'].replace('/', '_'), l['id'].replace('/', '.'), l['id'].replace('/', '-')):
            if _norm(v) == ns:
                return l['id'], 'line id'
    m = re.match(r'^(\d{1,4})(?:[_\-. ]|$)', stem)
    if m:
        k = int(m.group(1)) - 1
        if 0 <= k < len(world_lines):
            return world_lines[k]['id'], 'script number'
    words = re.findall(r'[a-z0-9]+', stem.lower())
    if len(words) >= 3:
        cands = [l for l in world_lines if ' '.join(re.findall(r'[a-z0-9]+', l['text'].lower())).startswith(' '.join(words[:6]))]
        if len(cands) == 1:
            return cands[0]['id'], 'text'
    return None, None


def import_files(world_dir, world, files):
    """files: [(name, bytes)] -> report {'matched': [...], 'unmatched': [...]} (each match becomes a new take)."""
    wl = lines(world)
    rep = {'matched': [], 'unmatched': [], 'errors': []}
    for name, data in files:
        if os.path.splitext(name)[1].lower() not in AUDIO_EXT:
            rep['unmatched'].append({'file': name, 'why': 'not an audio file'})
            continue
        lid, how = match_file(name, wl)
        if not lid:
            rep['unmatched'].append({'file': name, 'why': 'no line matches this name'})
            continue
        try:
            idx, info = add_take(world_dir, lid, data, mime='', source='import', name=name)
            rep['matched'].append({'file': name, 'line': lid, 'take': idx, 'by': how, 'dur': info['dur']})
        except (ValueError, RuntimeError) as e:
            rep['errors'].append({'file': name, 'line': lid, 'error': str(e)[:200]})
    return rep


# ---------------------------------------------------------------------------------------------------------------
# packer entries
# ---------------------------------------------------------------------------------------------------------------
def pack_entries(world, world_dir=None):
    """Every line for the cartridge: recorded (processed audio + visemes + subs) or a placeholder (text only)."""
    m = load(world_dir) if world_dir else {'lines': {}, 'characters': {}}
    out = []
    for l in lines(world):
        prof = profile_params(m, l['speaker'])
        ent = {'id': l['id'], 'speaker': l['speaker'], 'text': l['text'], 'profile': prof['name'],
               'profile_info': {'tts_pitch': prof['tts'][0], 'tts_rate': prof['tts'][1], 'profile': prof['name']}}
        proc = (m['lines'].get(l['id']) or {}).get('processed') if world_dir else None
        wav = os.path.join(_vdir(world_dir), 'lines', proc['file']) if proc else None
        meta = dsp.load_json(wav[:-4] + '.json') if wav else None
        if wav and os.path.isfile(wav) and meta:
            a, _ = dsp.read_wav(wav)
            ent['audio'] = a if a.ndim == 1 else a.mean(axis=0)
            ent['subs'] = meta.get('subs')
            ent['vis'] = meta.get('visemes')
        else:
            ent['placeholder'] = True
            ent['subs'] = subtitles.auto_time(l['text'])
            ent['vis'] = lipsync.compact(lipsync.text_only(l['text']))
        out.append(ent)
    return out


def status(world, world_dir):
    """Per-line recording status for the booth: takes, chosen take, processed."""
    m = load(world_dir)
    out = []
    for l in lines(world):
        e = m['lines'].get(l['id']) or {}
        out.append(dict(l, takes=e.get('takes') or [], best=e.get('best'), processed=e.get('processed'),
                        file=line_file(l['id'])))
    return {'lines': out, 'characters': m.get('characters') or {}, 'chain': m.get('chain') or dict(CHAIN_DEFAULT),
            'profiles': {k: v['label'] for k, v in PROFILES.items()}}


def b64decode(s):
    return base64.b64decode(s, validate=True)
