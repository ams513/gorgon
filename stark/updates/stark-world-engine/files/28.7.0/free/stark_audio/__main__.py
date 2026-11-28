"""python -m stark_audio <command>

  demo [OUT]                 render the listening set (score, presets, SFX, ambience, voice chain, in-browser game
                             mix, a playable cartridge) + LISTEN_CHECK.md   (default OUT: _music_demo/stark_audio)
  build WORLD_DIR [--budget compact|standard|rich|BYTES] [--cartridge OUT.html]
                             build a game's audio pack (and optionally a cartridge with it)
  check FILE.wav [...]       loudness (ffmpeg ebur128), clipping, spectrum, plain-words description
  booth [--games DIR]        start the voice booth on 127.0.0.1
  sfx OUT [--cat C]          render the SFX library
"""
from __future__ import annotations

import json
import os
import sys
import time

import numpy as np

from . import ROOT, analyze, dsp


def _row(path, kind='', loop=False, extra=None):
    y, sr = dsp.read_wav(path)
    r = analyze.check(y, kind, loop=(0, y.shape[-1]) if loop else None)
    e = r.get('ebur128') or {}
    f = r['features']
    out = {'file': path, 'kind': kind, 'dur': f['dur_s'], 'I': e.get('I'), 'LRA': e.get('LRA'), 'TP': e.get('TP'),
           'peak': f.get('peak_dbfs'), 'clipped': f.get('clipped'), 'centroid': f.get('centroid_hz'),
           'description': r['description'], 'problems': r['problems']}
    if loop and r.get('loop'):
        out['seamless'] = r['loop']['seamless']
    out.update(extra or {})
    return out


def demo(out=None, quick=False):
    import music_engine as me
    from . import adaptive, browser_check, fixtures, lipsync, pack, sfx_library, subtitles, voice
    out = os.path.abspath(out or os.path.join(ROOT, '_music_demo', 'stark_audio'))
    t0 = time.time()
    rows, notes = [], {}
    os.makedirs(out, exist_ok=True)

    def say(*a):
        print('[demo %5.0fs]' % (time.time() - t0), *a, flush=True)
    sys.path.insert(0, ROOT)
    import studio_app
    world = studio_app.starter_worlds()[0]                                  # Firefly Fen (3 levels, 16 lines)
    # 1. the world's adaptive score (every state + stinger it needs), plus the full mix of each cue
    cfg = adaptive.score_config(world, {})
    say('score for', world['name'], cfg['preset'], cfg['key'], cfg['states'], [r['mood'] for r in cfg['regions']])
    score = adaptive.build_score(world, {}, None, lambda a, b: say('  render', b), cfg)
    md = os.path.join(out, 'music_firefly_fen')
    os.makedirs(md, exist_ok=True)
    for rid, r in score['regions'].items():
        for st, c in r['cues'].items():
            arr = [dsp.read_wav(p)[0] for p in c['files'].values()]
            full = sum(arr)
            loop = not c['oneShot'] if 'oneShot' in c else c['loop_samples'] is not None
            if loop:
                full = np.concatenate([full, full], axis=1)                 # twice round: hear the seam
            p = os.path.join(md, '%s_%s_full%s.wav' % (rid, st, '_x2' if loop else ''))
            dsp.write_wav(p, full)
            rows.append(_row(p, 'music %s %s' % (rid, st), extra={'progression': ' '.join(c['progression']),
                                                                  'wrap_db': c.get('loop_wrap_error_db'),
                                                                  'vl_breaks': sum(c['voice_leading'].values())}))
            for layer, src in c['files'].items():
                y, _ = dsp.read_wav(src)
                dsp.write_wav(os.path.join(md, '%s_%s_layer_%s.wav' % (rid, st, layer)), y, (0, y.shape[1]) if loop else None)
    for k, s in score['stingers'].items():
        y, _ = dsp.read_wav(s['path'])
        p = os.path.join(md, 'stinger_%s.wav' % k)
        dsp.write_wav(p, y)
        rows.append(_row(p, 'stinger ' + k))
    notes['motif'] = score['regions']['main']['motif']
    # 2. one explore + combat cue per orchestration preset (same key, same motif) for comparison
    pd = os.path.join(out, 'music_presets')
    os.makedirs(pd, exist_ok=True)
    motif = score['regions']['main']['motif']
    for pr in ([] if quick else me.PRESETS):
        for st in ('explore', 'combat'):
            info = me.compose_cue(st, pr, 'D', 'major', None, 8 if st == 'explore' else 8, 4, motif, 'Firefly Fen', write=False)
            full = sum(a.astype(np.float64) for a in info['_arrays'].values())
            p = os.path.join(pd, '%s_%s.wav' % (pr, st))
            dsp.write_wav(p, np.concatenate([full, full], axis=1))
            rows.append(_row(p, 'preset %s %s' % (pr, st), extra={'progression': ' '.join(info['progression']),
                                                                  'wrap_db': info['loop_wrap_error_db']}))
        say('  preset', pr)
    # 3. SFX: one of each + variation reels
    sd = os.path.join(out, 'sfx')
    os.makedirs(sd, exist_ok=True)
    names = sfx_library.list_sounds()
    ctx = {'tonic': pack.me_tonic(cfg['key'])}
    for n in names:
        e = sfx_library.CATALOG[n]
        if e['loop']:
            continue
        y = sfx_library.render(n, 0, ctx)
        p = os.path.join(sd, n.replace('.', '_') + '.wav')
        dsp.write_wav(p, y)
        rows.append(_row(p, 'sfx ' + n))
    say('  sfx one-shots', len(rows))
    reel = []
    for s in sfx_library.SURFACES:
        for v in range(4):
            reel.append(dsp.as_stereo(sfx_library.render('foot.' + s, v, ctx)))
            reel.append(np.zeros((2, int(0.18 * dsp.SR))))
        reel.append(np.zeros((2, int(0.6 * dsp.SR))))
    dsp.write_wav(os.path.join(sd, '_reel_footsteps_all_surfaces.wav'), np.concatenate(reel, axis=1))
    reel = []
    for v in range(5):
        reel += [dsp.as_stereo(sfx_library.render('combat.hit_flesh', v, ctx)), np.zeros((2, int(0.25 * dsp.SR)))]
    dsp.write_wav(os.path.join(sd, '_reel_hit_variations.wav'), np.concatenate(reel, axis=1))
    # 4. ambience + weather beds, twice round each
    ad = os.path.join(out, 'ambience')
    os.makedirs(ad, exist_ok=True)
    for n in names:
        if sfx_library.CATALOG[n]['loop']:
            y = sfx_library.render(n, 0, ctx)
            p = os.path.join(ad, n.replace('.', '_') + '_x2.wav')
            dsp.write_wav(p, np.concatenate([y, y], axis=-1))
            rows.append(_row(p, 'loop ' + n, extra={'seamless': analyze.loop_seam(y, 0, y.shape[-1])['seamless']}))
    say('  beds')
    # 5. voice chain on synthetic speech (no real recordings): before / after / profiles / lip-sync / subtitles
    vd = os.path.join(out, 'voice')
    os.makedirs(vd, exist_ok=True)
    x, spans, words = fixtures.synth_sentence(3, noise_db=-50, hum_db=-42)
    dsp.write_wav(os.path.join(vd, 'fixture_raw_noisy.wav'), x)
    y, rep = voice.process(x)
    dsp.write_wav(os.path.join(vd, 'fixture_processed.wav'), y)
    rows.append(_row(os.path.join(vd, 'fixture_raw_noisy.wav'), 'voice raw'))
    rows.append(_row(os.path.join(vd, 'fixture_processed.wav'), 'voice processed', extra={'chain': {k: v for k, v in rep.items() if k not in ('in', 'out')}}))
    text = 'Mass of the past. Fee pa. See you.'
    keys = lipsync.visemes(y, text)
    dsp.save_json(os.path.join(vd, 'fixture_visemes.json'), {'text': text, 'keys': keys, 'compact': lipsync.compact(keys)})
    with open(os.path.join(vd, 'fixture.srt'), 'w', encoding='utf-8') as fh:
        fh.write(subtitles.to_srt(subtitles.auto_time(text, y)))
    for prof in ('deeper', 'giant', 'goblin', 'child', 'elder', 'spirit', 'radio', 'monster'):
        z, _ = voice.process(x, profile=dict(voice.PROFILES[prof], name=prof))
        p = os.path.join(vd, 'profile_%s.wav' % prof)
        dsp.write_wav(p, z)
        rows.append(_row(p, 'voice profile ' + prof))
    say('  voice')
    # 6. playable cartridge with the full audio pack (standard budget) + the in-browser game mix
    cd = os.path.join(out, 'cartridge')
    os.makedirs(cd, exist_ok=True)
    pk = pack.build_pack(world, None, 'standard', progress=lambda a, b: None)
    html = pack.inject(studio_app.cartridge_html(world), pk)
    with open(os.path.join(cd, 'Firefly_Fen_with_audio.html'), 'w', encoding='utf-8') as fh:
        fh.write(html)
    notes['pack'] = pk['report']
    say('  cartridge', pk['report']['html_bytes'], 'bytes, level', pk['report']['level'])
    if browser_check.find_browser():
        demo_pk = pk
        res = browser_check.run_scenario(demo_pk, browser_check.DEMO_EVENTS[:-5] + [[30.0, 'menu_open', {}], [32.0, 'menu_close', {}]], 36.0)
        p = os.path.join(cd, 'game_mix_rendered_by_browser.wav')
        with open(p, 'wb') as fh:
            fh.write(res['wav'])
        rows.append(_row(p, 'in-browser game mix'))
        notes['browser_log'] = res['log']
        notes['browser_debug'] = res['debug']
        yy, sr = dsp.read_wav(p)
        notes['segments'] = {lab: round(dsp.lufs(yy[:, int(a * sr):int(b * sr)]), 1) for a, b, lab in
                             ((1, 9, 'explore 1-9 s'), (11.5, 18.5, 'combat 11.5-18.5 s'), (20, 27, 'victory 20-27 s'),
                              (30.3, 31.8, 'menu low-pass 30.3-31.8 s'))}
        say('  browser mix')
    rep_p = os.path.join(out, 'listen_check.json')
    dsp.save_json(rep_p, {'rows': rows, 'notes': notes, 'seconds': round(time.time() - t0, 1)})
    write_markdown(out, rows, notes)
    say('done ->', out)
    return out


def write_markdown(out, rows, notes):
    L = ['# Stark World Engine audio listen-check', '',
         'Every file below was measured with ffmpeg ebur128 (integrated loudness I, loudness range LRA, true peak TP) and '
         'stark_audio.analyze (sample peak, clipping, spectral centroid, band balance). The descriptions are generated '
         'from those measurements; they are not a human listening note.', '']
    if notes.get('pack'):
        r = notes['pack']
        L += ['## Cartridge pack (Firefly Fen, standard budget)', '',
              '- HTML added: %d bytes (budget %d, fits: %s, quality level %d, music %d kbps)' % (
                  r['html_bytes'], r['budget'], r['fits'], r['level'], r['music_kbps']),
              '- states: %s; stingers: %s' % (', '.join(r['states']), ', '.join(r['stingers'])), '']
    if notes.get('segments'):
        L += ['## In-browser game mix (Chrome, OfflineAudioContext, the shipped runtime)', '']
        L += ['- %s: %s LUFS' % (k, v) for k, v in notes['segments'].items()]
        L += ['']
    L += ['| file | I (LUFS) | TP (dBTP) | LRA | clip | notes | description |', '|---|---|---|---|---|---|---|']
    for r in rows:
        extra = []
        if r.get('progression'):
            extra.append(r['progression'])
        if r.get('wrap_db') is not None:
            extra.append('loop wrap %s dB' % r['wrap_db'])
        if 'seamless' in r:
            extra.append('seamless' if r['seamless'] else 'SEAM')
        if r.get('problems'):
            extra.append('PROBLEMS: ' + ', '.join(r['problems']))
        L.append('| %s | %s | %s | %s | %s | %s | %s |' % (os.path.relpath(r['file'], out).replace(os.sep, '/'), r['I'], r['TP'],
                                                         r['LRA'], r['clipped'], '; '.join(extra), r['description']))
    with open(os.path.join(out, 'LISTEN_CHECK.md'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(L) + '\n')


def main(argv):
    if not argv or argv[0] in ('-h', '--help'):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == 'demo':
        demo(rest[0] if rest and not rest[0].startswith('-') else None, quick='--quick' in rest)
        return 0
    if cmd == 'check':
        for p in rest:
            r = _row(p, os.path.basename(p))
            print(json.dumps(r, indent=1, default=str))
        return 0
    if cmd == 'booth':
        from . import booth
        return booth.main(rest)
    if cmd == 'sfx':
        from . import sfx_library
        return sfx_library._main(rest)
    if cmd == 'build':
        import argparse
        from . import pack
        ap = argparse.ArgumentParser(prog='stark_audio build')
        ap.add_argument('world_dir')
        ap.add_argument('--budget', default='standard')
        ap.add_argument('--cartridge', default=None)
        a = ap.parse_args(rest)
        with open(os.path.join(a.world_dir, 'world.json'), encoding='utf-8') as fh:
            world = json.load(fh)
        b = int(a.budget) if a.budget.isdigit() else a.budget
        pk = pack.build_pack(world, a.world_dir, b, progress=lambda k, n: print('  ', k, n))
        print(json.dumps(pk['report'], indent=1))
        if a.cartridge:
            sys.path.insert(0, ROOT)
            import studio_app
            with open(a.cartridge, 'w', encoding='utf-8') as fh:
                fh.write(pack.inject(studio_app.cartridge_html(world), pk))
            print('wrote', a.cartridge)
        return 0
    print(__doc__)
    return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
