"""stark_audio - the Stark World Engine audio department (offline, original, procedural).

Modules
-------
dsp         shared numpy DSP: periodic (loop-safe) filters, envelopes, grains, modal resonators, WAV i/o, ffmpeg i/o
analyze     listen-check: ffmpeg ebur128 loudness, clipping, DC, spectrum, loop seams, plain-words descriptions
sfx_library procedural sound-effect library (footsteps per surface, combat, magic, UI, creatures, weather, beds)
adaptive    adaptive score builder on top of music_engine (mood cues with layered stems, stingers, transitions)
pack        cartridge packer: Opus/OGG encode inside a size budget, audio sprites, manifest, HTML injection
voice       voice-line pipeline: decode, trim, gate, high-pass, de-ess, loudness, voice profiles (pitch/formant/EQ)
lipsync     offline viseme timing (energy + spectral heuristics + text-guided alignment) -> compact viseme tracks
subtitles   subtitle chunking and auto-timing to recorded audio (JSON / SRT / VTT)
booth       in-studio voice booth: page + JSON API (pure functions) + a standalone 127.0.0.1 server
export_map  event -> asset mapping for the UE5 (MetaSounds) and Godot (AudioStreamPlayer) bridges
runtime/    stark_audio.js, the WebAudio runtime embedded in cartridges

Everything runs locally: Python 3.10 + numpy + ffmpeg (encode/decode/measure). No network, no cloud, no model.
"""
import os
import sys

VERSION = '1.0.0'
PKG_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(PKG_DIR)
RUNTIME_JS = os.path.join(PKG_DIR, 'runtime', 'stark_audio.js')
BOOTH_JS = os.path.join(PKG_DIR, 'runtime', 'voice_booth.js')

if ROOT not in sys.path:          # music_engine.py lives next to the package
    sys.path.insert(0, ROOT)
