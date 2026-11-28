# stark_audio export mapping (for the UE5 and Godot bridges)

`stark_audio.studio_api.export_files(world, games, target)` returns `{relative path: bytes}` under `audio/`:

```
audio/mapping.json                      events, music, sounds, ambience, buses, voice, mouth shapes
audio/music/<region>_<state>_<layer>.wav      loop stems (RIFF smpl loop chunk = whole file), 48 kHz stereo
audio/music/stinger_<name>.wav                one-shots, meant to start on the next beat
audio/sfx/<sound>_<variant>.wav               one WAV per variation
audio/loops/<bed>.wav                         seamless ambience / weather loops (whole file loops)
audio/voice/<dialog>--<node>.wav|.srt|.vtt|.visemes.json   recorded lines, subtitles, lip-sync
```

All files are 48 kHz 16-bit PCM WAV. Every loop is sample-exact: the file's first sample follows its last.

## Music (adaptive score)

`mapping.music`:

- `bpm`, `beats_per_bar`, `key`: one tempo and tonic shared by every state, so cross-fades can land on beats.
- `regions.<region>.<state>.layers.<bed|perc|lead|mix>`: stem file, `loop`, `loop_start_sample`, `loop_end_sample`.
- `thresholds`: the intensity (0..1) at which each layer sounds. `bed` is always on.
- `one_shot` + `music_end_s` (victory): hand back to the calm state at `music_end_s` and let the tail ring under
  the next cue.
- `level_region[level]`: which region a level uses.
- `transitions`: the first rule matching `(from, to)` wins. `sync`: `beat` | `bar` | `end` | `now`. `fade_in` and
  `fade_out` in seconds (equal-power). With `resume`, return to the bar where that state was left.
- `stingers.<name>`: `{file, sync, duck_db, len_s}`. Play on the next beat; duck the music cue by `duck_db` for
  about 55 % of the stinger, then recover over 1.6 s.
- `rest`: `{play: [min, max], rest: [min, max]}` seconds. Ambient states play a while, then rest (ambience
  carries the world), then come back on a new phrase.
- `breathe`: the chance per loop pass that the `lead` layer sits out, so the theme never wears thin.
- `priority`: combat 5 > victory 4 > tension 3 > town 2 > night 1 > explore 0.

Desired state, in order: dead -> silence; a queued victory; combat; tension (level >= 0.5); town; night; explore.

**UE5 (MetaSounds).** Make one MetaSound Source per state with a Wave Player per layer, all started together and
looping. Expose a float `Intensity` input; each layer's gain is `Intensity >= threshold ? 1 : 0`, smoothed with an
Interp To over 1.5 s. Use Quartz for the clock: a Quartz clock at `bpm`, with transitions queued with
`PlayQuantized` on Beat or Bar boundaries, fade-out on the old source and fade-in on the new one as in
`transitions`. Stingers are a one-shot Wave Player scheduled on the next Beat, with a Submix ducking send. Rest and
breathe are a Blueprint timer on the music manager.

**Godot 4.** Use `AudioStreamPlayer` nodes on a `Music` bus, one per layer, with `AudioStreamWAV` loop mode Forward
and `loop_end` = `loop_end_sample`. For the beat/bar grid, use `AudioServer.get_time_since_last_mix()` plus the
playback position, or a Timer at 60/bpm. Cross-fade with Tweens on `volume_db` (equal-power: tween linear gain and
convert). Stingers go on a separate player; duck through a bus Compressor sidechain, or tween the music bus.
`AudioStreamInteractive` (4.3+) maps directly: clips = states, transitions = `transitions`,
`TRANSITION_FROM_TIME_NEXT_BEAT` / `NEXT_BAR` for `beat` / `bar`.

## Sounds

`mapping.sounds.<name>`: `variants` (files), `pitch_cents` [min, max], `gain_db` [min, max], `cooldown_s`,
`max_voices`, `bus` (`sfx` | `ui` | `amb`), `spatial`, `caption`, `priority`.

Every trigger picks a variant from a shuffle bag, so no variant repeats until all have played and the bag never
starts with the one that just ended it. It adds a random pitch and gain inside the ranges, skips triggers inside
`cooldown_s`, and steals the oldest voice above `max_voices`. With 40 voices playing, the lowest priority yields.

- **UE5:** Sound Cue (or a MetaSound) with Random (no repeats) over the variants, Modulator for pitch and volume,
  Concurrency asset = `max_voices` / Stop Oldest, Attenuation from `buses.spatial`.
- **Godot:** `AudioStreamRandomizer` with `random_pitch` (cents -> semitone ratio) and `random_volume_offset_db`,
  `max_polyphony` = `max_voices`. Use `AudioStreamPlayer3D` when `spatial`.

The engine's old sound keys map through `legacy_engine_keys` (`jump`, `coin`, `hit`, `laser`, ...).

## Ambience

`mapping.ambience.levels[i]`: `beds` (loops, cross-faded over 3-4 s), `weather` (extra loops), `spots`
(`{sfx, every}`: Poisson-timed one-shots placed 6-28 m around the listener at random directions, between 0.3 and
3 times `every` apart), `surfaces` (footstep surfaces for that level). `town` + `townSpots` replace the beds while
the player is in a village (at least two houses within 22 m).

## Mixer

`mapping.buses`:

- `buses`: default bus gains in dB.
- `duck.voice`: music -9 dB, ambience -6 dB, sfx -3 dB while a line plays; attack 0.12 s, release 0.7 s.
- `limiter`: master limiter at -3 dB (ratio 20, 1 ms) then a 4x-oversampled soft clip at -1.3 dBFS.
- `night_mode`: compressor for "reduce sudden loud sounds".
- `spatial`: inverse distance, ref 2 m, max 60 m, rolloff 1. Air absorption low-passes from 18 kHz down to
  4.5 kHz at max distance; occlusion pulls the low-pass toward 250 Hz.

Loudness anchors (measured with ffmpeg ebur128):

- music cues: explore -19, tension -21, combat -17, town -19, night -22, victory -17 LUFS (all layers on)
- dialogue: -18 LUFS (single-channel measure), true peak -1 dBTP
- SFX: levelled by "active loudness" per category; footsteps -26, impacts -15, UI -24 to -33, beds -25 to -32
- program target about -18 LUFS integrated with true peak at or below -1 dBTP

Reverb zones (`reverb_zones_t60`): outdoor 0.45 s, forest 1.1, town 1.2, room 0.6, hall 2.2, cave 2.9,
underwater 1.6, snow 0.3.

## Voice

`mapping.voice.<line id>`:

- `speaker`, `text`, `recorded`, `wav`.
- `subtitles`: `[[start_s, end_s, text], ...]`, timed to the recording; `\n` marks the two-line break.
- `visemes`: compact track `{d: ms deltas, v: shapes, o: openness 0-9}`. The `.visemes.json` next to the WAV has
  it expanded to `[[t, shape, open]]`.
- `placeholder_tts`: no recording yet. Use the engine's text-to-speech, or show subtitles only.

`mapping.mouth_shapes.<A..H, X>` gives `sprite_index` for a 9-frame 2D mouth sheet, ARKit-style `blendshapes`
weights, and `open` (jaw).

- **UE5:** drive a Pose Asset or morph targets from the shape keys with a 60 ms blend. Subtitles: a Dialogue Wave
  with SubtitleCue entries from `subtitles`.
- **Godot:** an `AnimationPlayer` track on the mouth `frame` (2D) or blend shapes (3D) built from the keys. The
  `.srt` file is ready for any subtitle label.

Line ids are `<dialog id>/<node id>` (and `npc/<npc id>` for an NPC's single fallback line), exactly the keys the
game's dialogue system uses.
