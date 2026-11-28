"""Event -> asset mapping for the engine bridges (UE5 MetaSounds / Sound Cues, Godot AudioStreamPlayer).

mapping(pack) -> dict that the bridge agent turns into engine assets:
  events     every game event the runtime understands, with what it triggers (music state, stinger, sounds)
  music      per region and state: stem files, loop points (samples @ 48 kHz), bpm, bar length, layer thresholds,
             transition rules (sync on beat/bar/end, fade times), stingers with their beat sync
  sounds     per sound: variant files (segment of a sprite or own file), pitch/gain jitter, cooldown, voice limit, bus,
             spatial flag, caption
  buses      mixer buses, default gains, ducking rules, limiter; loudness anchors
  voice      per line: wav + subtitles + visemes (+ ARKit-style blendshape weights per mouth shape)
write(pack, out_dir) -> folder with WAVs (sprites cut back into one file per variant), mapping.json,
                        per-line voice WAV + .srt + .vtt + visemes JSON
"""
from __future__ import annotations

import json
import os

import numpy as np

from . import adaptive, dsp, lipsync, subtitles

SR = dsp.SR

EVENTS = {
    'combat_start': {'music': 'combat', 'stinger': 'combat_start'}, 'combat_end': {'music': 'victory|base'},
    'enemy_alert': {'music': 'tension', 'sound': 'creature.alert', 'caption': True}, 'tension_start': {'music': 'tension'},
    'tension_end': {'music': 'base'}, 'boss_start': {'music': 'combat', 'stinger': 'boss', 'sound': 'creature.boss_roar'},
    'boss_defeated': {'music': 'victory'}, 'quest_start': {'sound': 'ui.quest_accept'},
    'quest_complete': {'stinger': 'quest_complete', 'fallback_sound': 'ui.quest_complete'},
    'level_up': {'stinger': 'level_up', 'fallback_sound': 'ui.level_up'},
    'enter_region': {'music': 'region of the level', 'ambience': 'level beds + weather', 'stinger': 'discovery'},
    'enter_town': {'music': 'town', 'ambience': 'amb.town'}, 'leave_town': {'music': 'base'},
    'time_of_day': {'music': 'night when t < 0.22 or t > 0.8'}, 'weather': {'ambience': 'weather layers'},
    'player_hurt': {'sound': 'combat.player_hurt', 'filter': 'music low-pass 900 Hz under 30 % HP'},
    'player_death': {'stinger': 'defeat', 'music': 'stop'}, 'player_respawn': {'music': 'base'},
    'item_pickup': {'sound': 'ui.pickup'}, 'chest_open': {'sound': 'world.chest'}, 'door_open': {'sound': 'world.door'},
    'portal_enter': {'sound': 'magic.teleport'}, 'dialogue_start': {'music': 'calm (bed layer only)'},
    'dialogue_line': {'voice': 'line id', 'duck': 'voice'}, 'dialogue_end': {'music': 'all layers'},
    'menu_open': {'filter': 'music low-pass 1.2 kHz, -5 dB'}, 'menu_close': {'filter': 'off'},
    'footstep': {'sound': 'foot.<surface>'}, 'jump': {'sound': 'move.jump'}, 'land': {'sound': 'move.land'},
    'attack': {'sound': 'combat.swing | combat.swing_heavy | combat.bow_release'},
    'hit': {'sound': 'combat.hit_flesh | clang | block | blunt | parry | arrow_hit | crit'},
    'cast': {'sound': 'magic.<spell>'}, 'spell_hit': {'sound': 'magic.fire_hit | magic.ice_hit'},
    'enemy_die': {'sound': 'combat.enemy_die + combat.body_fall (+0.18 s)'},
    'game_won': {'music': 'victory'}, 'reverb_zone': {'reverb': 'outdoor forest town room hall cave underwater snow'},
}
REVERB_ZONES = {'outdoor': 0.45, 'forest': 1.1, 'town': 1.2, 'room': 0.6, 'hall': 2.2, 'cave': 2.9, 'underwater': 1.6,
                'snow': 0.3}


def mapping(pack, files=None):
    m = pack['manifest']
    files = files or {}
    music = {'bpm': m['music']['bpm'], 'beats_per_bar': m['music']['bpb'], 'key': m['music']['key'],
             'transitions': m['music']['transitions'], 'rest': m['music']['rest'], 'breathe': m['music']['breathe'],
             'priority': m['music']['priority'], 'level_region': m['music']['levelRegion'], 'regions': {}, 'stingers': {}}
    for rid, r in m['music']['regions'].items():
        out = {}
        for st, c in r['cues'].items():
            layers = {}
            for name, L in c['layers'].items():
                key = 'music/%s_%s_%s.wav' % (rid, st, name)
                ent = {'file': files.get(key, key), 'loop': not c['oneShot']}
                if not c['oneShot']:
                    ent['loop_start_sample'] = 0
                    ent['loop_end_sample'] = int(round((L['loopEnd'] - L['loopStart']) * SR))
                layers[name] = ent
            out[st] = {'layers': layers, 'bar_seconds': c['bar'], 'bars': c['bars'], 'thresholds': c['thresholds'],
                       'one_shot': c['oneShot'], 'music_end_s': c.get('musicEnd')}
        music['regions'][rid] = out
    for k, s in m['music']['stingers'].items():
        key = 'music/stinger_%s.wav' % k
        music['stingers'][k] = {'file': files.get(key, key), 'sync': s['sync'], 'duck_db': s['duck_db'], 'len_s': s['len']}
    sounds = {}
    for n, e in m['sfx'].items():
        sounds[n] = {'variants': [files.get('sfx/%s_%d.wav' % (n, i), 'sfx/%s_%d.wav' % (n, i)) for i in range(len(e['segs']))],
                     'pitch_cents': e['pitch'], 'gain_db': e['gain'], 'cooldown_s': e['cooldown'], 'max_voices': e['voices'],
                     'bus': e['bus'], 'spatial': e['spatial'], 'caption': e['caption'], 'priority': e['priority']}
    for n, e in m['loops'].items():
        key = 'loops/%s.wav' % n
        sounds[n] = {'variants': [files.get(key, key)], 'loop': True, 'bus': e['bus'], 'spatial': False,
                     'caption': e['caption'], 'loop_start_sample': 0,
                     'loop_end_sample': int(round((e['loopEnd'] - e['loopStart']) * SR))}
    voice = {}
    for lid, ln in m['voice']['lines'].items():
        base = 'voice/%s' % lid.replace('/', '--')
        voice[lid] = {'speaker': ln.get('speaker'), 'text': ln.get('text'), 'recorded': 'a' in ln,
                      'wav': files.get(base + '.wav', base + '.wav') if 'a' in ln else None,
                      'subtitles': ln.get('subs'), 'srt': base + '.srt', 'visemes': ln.get('vis'),
                      'placeholder_tts': ln.get('placeholder', False)}
    return {'format': 'stark_audio export mapping v1', 'sample_rate': SR, 'events': EVENTS, 'music': music,
            'sounds': sounds, 'ambience': m['ambience'], 'buses': m['mix'], 'reverb_zones_t60': REVERB_ZONES,
            'voice': voice, 'mouth_shapes': {s: {'sprite_index': lipsync.SPRITE_INDEX[s], 'blendshapes': lipsync.BLENDSHAPES[s],
                                                 'open': lipsync.OPEN[s]} for s in lipsync.SHAPES},
            'legacy_engine_keys': m['legacy']}


def write(pack, out_dir):
    """Decode the pack back into engine-friendly files + mapping.json (sprites cut into one WAV per variant)."""
    m = pack['manifest']
    os.makedirs(out_dir, exist_ok=True)
    dec = {}

    def asset(aid):
        if aid not in dec:
            dec[aid] = dsp.decode(pack['data'][aid], mono=False)
        return dec[aid]

    def cut(aid, a, d):
        y = asset(aid)
        i, j = int(round(a * SR)), int(round((a + d) * SR))
        return y[:, i:j]
    files = {}

    def put(rel, y, loop=False):
        p = os.path.join(out_dir, rel)
        dsp.write_wav(p, y, (0, y.shape[-1]) if loop else None)
        files[rel] = rel
    for rid, r in m['music']['regions'].items():
        for st, c in r['cues'].items():
            for name, L in c['layers'].items():
                if c['oneShot']:
                    put('music/%s_%s_%s.wav' % (rid, st, name), cut(L['a'], L['start'], L['dur']))
                else:
                    put('music/%s_%s_%s.wav' % (rid, st, name), cut(L['a'], L['loopStart'], L['loopEnd'] - L['loopStart']), True)
    for k, s in m['music']['stingers'].items():
        put('music/stinger_%s.wav' % k, cut(s['a'], s['start'], s['dur']))
    for n, e in m['sfx'].items():
        for i, (a, d) in enumerate(e['segs']):
            put('sfx/%s_%d.wav' % (n, i), cut(e['a'], a, d))
    for n, e in m['loops'].items():
        put('loops/%s.wav' % n, cut(e['a'], e['loopStart'], e['loopEnd'] - e['loopStart']), True)
    for lid, ln in m['voice']['lines'].items():
        base = 'voice/%s' % lid.replace('/', '--')
        if 'a' in ln:
            y = cut(ln['a'], ln['start'], ln['dur']).mean(axis=0)
            put(base + '.wav', y)
        os.makedirs(os.path.join(out_dir, 'voice'), exist_ok=True)
        subs = ln.get('subs') or []
        with open(os.path.join(out_dir, base + '.srt'), 'w', encoding='utf-8') as fh:
            fh.write(subtitles.to_srt(subs))
        with open(os.path.join(out_dir, base + '.vtt'), 'w', encoding='utf-8') as fh:
            fh.write(subtitles.to_vtt(subs))
        if ln.get('vis'):
            dsp.save_json(os.path.join(out_dir, base + '.visemes.json'),
                          {'keys': [[round(t, 3), s, round(o, 2)] for t, s, o in lipsync.expand(ln['vis'])],
                           'shapes': {s: lipsync.BLENDSHAPES[s] for s in lipsync.SHAPES}})
    mp = mapping(pack, files)
    dsp.save_json(os.path.join(out_dir, 'mapping.json'), mp)
    return mp
