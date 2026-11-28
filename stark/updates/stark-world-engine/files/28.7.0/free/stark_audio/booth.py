"""Voice booth: record, pick, trim and process a game's voice lines in the browser, fully offline.

handle(method, path, query, body, games) -> (status, content_type, bytes)   pure request handler
    GET  /voice                         the booth page (teleprompter, takes, trim, profiles, import)
    GET  /voice/api/worlds              games in My_Games
    GET  /voice/api/script?world=       lines + recording status + characters + profiles + chain
    GET  /voice/api/audio?world=&line=&take=N | &processed=1     a take as recorded / the processed line (WAV)
    GET  /voice/api/line?world=&line=                            processed line metadata (visemes, subtitles, report)
    GET  /voice/api/preview?world=&line=&profile=                the chosen take through a voice profile (WAV)
    GET  /voice/api/export_script?world=&fmt=txt|csv             recording script for actors
    POST /voice/api/take      {world, line, b64, mime}           new take from the microphone
    POST /voice/api/import    {world, name, b64}                 one file of a batch import (matched by name)
    POST /voice/api/best      {world, line, take}                choose a take (it is processed at once)
    POST /voice/api/trim      {world, line, take, start, end}    manual trim (null start clears it)
    POST /voice/api/delete    {world, line, take}
    POST /voice/api/process   {world, line}                      re-run the chain on the chosen take
    POST /voice/api/profile   {world, speaker, profile, custom}  a character's voice profile
    POST /voice/api/chain     {world, chain}                     gate / hpf / de-ess / trim / normalize / target
Standalone: python -m stark_audio.booth [--games DIR] [--port 8531] [--no-browser]   (binds 127.0.0.1 only)

Privacy: the microphone is opened by the browser only after the creator's own permission prompt; recordings are
posted to this local server and saved under My_Games/<game>/voice/. Nothing is sent anywhere else.
"""
from __future__ import annotations

import base64
import http.server
import io
import json
import os
import re
import socketserver
import sys
import threading
import urllib.parse
import webbrowser

from . import BOOTH_JS, ROOT, dsp, voice

MAX_BODY = 48 * 1024 * 1024
PORTS = list(range(8531, 8541))
_LOCK = threading.Lock()


class BoothError(ValueError):
    pass


def _games_default():
    return os.path.join(ROOT, 'My_Games')


def _slug(name):
    s = re.sub(r'[^A-Za-z0-9_-]+', '_', str(name or '')).strip('_')[:48]
    return s or 'world'


def _world_dir(games, world):
    games = os.path.realpath(games)
    d = os.path.realpath(os.path.join(games, _slug(world)))
    if not d.startswith(games + os.sep):
        raise BoothError('bad world name')
    if not os.path.isfile(os.path.join(d, 'world.json')):
        raise FileNotFoundError('no such game: %s' % world)
    return d


def _load_world(wd):
    with open(os.path.join(wd, 'world.json'), encoding='utf-8') as fh:
        return json.load(fh)


def _line_ok(world, line):
    if not isinstance(line, str) or line not in {l['id'] for l in voice.lines(world)}:
        raise BoothError('unknown line')
    return line


def list_worlds(games):
    out = []
    try:
        names = sorted(os.listdir(games))
    except OSError:
        return out
    for n in names:
        p = os.path.join(games, n, 'world.json')
        if os.path.isfile(p):
            try:
                with open(p, encoding='utf-8') as fh:
                    w = json.load(fh)
                out.append({'slug': n, 'name': w.get('name') or n, 'lines': len(voice.lines(w))})
            except (OSError, ValueError):
                continue
    return out


def _wav_bytes(x):
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix='.wav')
    os.close(fd)
    try:
        dsp.write_wav(tmp, x)
        with open(tmp, 'rb') as fh:
            return fh.read()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def page_html():
    with open(BOOTH_JS, encoding='utf-8') as fh:
        js = fh.read()
    return PAGE.replace('%%JS%%', js)


def _json(code, obj):
    return code, 'application/json; charset=utf-8', json.dumps(obj).encode('utf-8')


def handle(method, path, query, body, games=None):
    """-> (status, content type, payload bytes). Raises nothing: errors become JSON {'error': ...}."""
    games = games or _games_default()
    q = {k: (v[0] if isinstance(v, list) else v) for k, v in (query or {}).items()}
    try:
        if method == 'GET':
            if path in ('/voice', '/voice/'):
                return 200, 'text/html; charset=utf-8', page_html().encode('utf-8')
            if path == '/voice/api/worlds':
                return _json(200, {'worlds': list_worlds(games)})
            wd = _world_dir(games, q.get('world'))
            world = _load_world(wd)
            if path == '/voice/api/script':
                st = voice.status(world, wd)
                st['world'] = world.get('name')
                return _json(200, st)
            if path == '/voice/api/export_script':
                fmt = 'csv' if q.get('fmt') == 'csv' else 'txt'
                return 200, ('text/csv' if fmt == 'csv' else 'text/plain') + '; charset=utf-8', \
                    voice.script_text(world, fmt).encode('utf-8')
            line = _line_ok(world, q.get('line'))
            if path == '/voice/api/line':
                m = voice.load(wd)
                pr = (m['lines'].get(line) or {}).get('processed')
                meta = dsp.load_json(os.path.join(wd, 'voice', 'lines', pr['file'][:-4] + '.json')) if pr else None
                if not meta:
                    raise FileNotFoundError('not processed yet')
                return _json(200, meta)
            if path == '/voice/api/audio':
                if q.get('processed'):
                    m = voice.load(wd)
                    pr = (m['lines'].get(line) or {}).get('processed')
                    if not pr:
                        raise FileNotFoundError('not processed yet')
                    with open(os.path.join(wd, 'voice', 'lines', pr['file']), 'rb') as fh:
                        return 200, 'audio/wav', fh.read()
                p = voice.take_path(wd, line, q.get('take'))
                if not p or not os.path.isfile(p):
                    raise FileNotFoundError('no such take')
                ext = os.path.splitext(p)[1].lower()
                ctype = {'.wav': 'audio/wav', '.webm': 'audio/webm', '.ogg': 'audio/ogg', '.opus': 'audio/ogg',
                         '.mp3': 'audio/mpeg', '.flac': 'audio/flac', '.m4a': 'audio/mp4'}.get(ext, 'application/octet-stream')
                with open(p, 'rb') as fh:
                    return 200, ctype, fh.read()
            if path == '/voice/api/preview':
                p = voice.take_path(wd, line)
                if not p:
                    raise FileNotFoundError('record a take first')
                m = voice.load(wd)
                prof = dict(voice.PROFILES.get(q.get('profile') or 'natural', voice.PROFILES['natural']))
                prof['name'] = q.get('profile') or 'natural'
                ent = m['lines'][line]
                y, _ = voice.process(dsp.decode(p), m.get('chain'), prof, ent['takes'][ent['best']].get('trim'))
                return 200, 'audio/wav', _wav_bytes(y)
            return _json(404, {'error': 'not found'})
        if method == 'POST':
            body = body if isinstance(body, dict) else {}
            if path == '/voice/api/chain':
                wd = _world_dir(games, body.get('world'))
                ch = body.get('chain') or {}
                with _LOCK:
                    m = voice.load(wd)
                    for k in ('hpf', 'gate', 'deess', 'trim', 'normalize'):
                        if k in ch:
                            m['chain'][k] = bool(ch[k])
                    if 'target' in ch:
                        m['chain']['target'] = max(-30.0, min(-12.0, float(ch['target'])))
                    voice.save(wd, m)
                return _json(200, {'chain': m['chain']})
            wd = _world_dir(games, body.get('world'))
            world = _load_world(wd)
            if path == '/voice/api/profile':
                sp = str(body.get('speaker') or '')[:80]
                with _LOCK:
                    voice.set_profile(wd, sp, body.get('profile') or 'natural', body.get('custom'))
                return _json(200, {'ok': True})
            if path == '/voice/api/import':
                name = os.path.basename(str(body.get('name') or 'take.wav'))[:120]
                data = base64.b64decode(body.get('b64') or '', validate=True)
                with _LOCK:
                    rep = voice.import_files(wd, world, [(name, data)])
                for mm in rep['matched']:
                    try:
                        with _LOCK:
                            m = voice.load(wd)
                            if m['lines'][mm['line']]['best'] == mm['take']:
                                voice.process_line(wd, mm['line'], world=world)
                    except (ValueError, RuntimeError, OSError):
                        pass
                return _json(200, rep)
            line = _line_ok(world, body.get('line'))
            if path == '/voice/api/take':
                data = base64.b64decode(body.get('b64') or '', validate=True)
                with _LOCK:
                    idx, info = voice.add_take(wd, line, data, str(body.get('mime') or 'audio/webm')[:60], 'mic')
                    m = voice.load(wd)
                    meta = voice.process_line(wd, line, world=world) if m['lines'][line]['best'] == idx else None
                return _json(200, {'take': idx, 'info': info, 'processed': _brief(meta)})
            take = body.get('take')
            if path == '/voice/api/best':
                with _LOCK:
                    voice.set_best(wd, line, int(take))
                    meta = voice.process_line(wd, line, world=world)
                return _json(200, {'processed': _brief(meta)})
            if path == '/voice/api/trim':
                with _LOCK:
                    voice.set_trim(wd, line, int(take), body.get('start'), body.get('end'))
                    m = voice.load(wd)
                    meta = voice.process_line(wd, line, world=world) if m['lines'][line]['best'] == int(take) else None
                return _json(200, {'processed': _brief(meta)})
            if path == '/voice/api/delete':
                with _LOCK:
                    voice.delete_take(wd, line, int(take))
                    m = voice.load(wd)
                    meta = voice.process_line(wd, line, world=world) if m['lines'][line].get('best') is not None else None
                return _json(200, {'processed': _brief(meta)})
            if path == '/voice/api/process':
                with _LOCK:
                    meta = voice.process_line(wd, line, world=world)
                return _json(200, {'processed': _brief(meta)})
            return _json(404, {'error': 'not found'})
        return _json(405, {'error': 'method not allowed'})
    except FileNotFoundError as e:
        return _json(404, {'error': str(e)[:200]})
    except (ValueError, KeyError, TypeError, RuntimeError, base64.binascii.Error) as e:
        return _json(400, {'error': str(e)[:300] or e.__class__.__name__})


def _brief(meta):
    if not meta:
        return None
    r = meta.get('report') or {}
    return {'dur': meta['dur'], 'lufs': r.get('lufs'), 'profile': meta['profile'], 'subs': meta['subs'],
            'visemes': meta['visemes'], 'gated_pct': r.get('gated_pct'), 'deess_max_db': r.get('deess_max_db'),
            'trimmed': r.get('trimmed'), 'in': r.get('in'), 'out': r.get('out')}


CSP = ("default-src 'self' 'unsafe-inline' data: blob:; media-src 'self' data: blob:; connect-src 'self'; "
       "img-src 'self' data: blob:")


class Handler(http.server.BaseHTTPRequestHandler):
    games = None
    server_version = 'StarkVoiceBooth/1'

    def log_message(self, fmt, *a):
        pass

    def _host_ok(self):
        return (self.headers.get('Host') or '').split(':')[0].lower() in ('127.0.0.1', 'localhost')

    def _origin_ok(self):
        o = self.headers.get('Origin')
        if o is None:
            return True
        u = urllib.parse.urlparse(o)
        return u.scheme == 'http' and (u.hostname or '') in ('127.0.0.1', 'localhost') and u.port == self.server.server_address[1]

    def _send(self, code, ctype, data):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', CSP)
        self.send_header('Permissions-Policy', 'microphone=(self), camera=(), geolocation=()')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, 'application/json', b'{"error":"bad host"}')
        u = urllib.parse.urlparse(self.path)
        if u.path == '/':
            self.send_response(302)
            self.send_header('Location', '/voice')
            self.end_headers()
            return
        code, ctype, data = handle('GET', u.path, urllib.parse.parse_qs(u.query), None, self.games)
        self._send(code, ctype, data)

    def do_POST(self):
        if not self._host_ok() or not self._origin_ok():
            return self._send(403, 'application/json', b'{"error":"bad host or origin"}')
        if (self.headers.get('Content-Type') or '').split(';')[0] != 'application/json':
            return self._send(415, 'application/json', b'{"error":"json only"}')
        try:
            n = int(self.headers.get('Content-Length') or '0')
        except ValueError:
            n = -1
        if n < 0 or n > MAX_BODY:
            self.close_connection = True
            return self._send(413, 'application/json', b'{"error":"request too large"}')
        try:
            body = json.loads(self.rfile.read(n) or b'{}')
        except ValueError:
            return self._send(400, 'application/json', b'{"error":"bad json"}')
        u = urllib.parse.urlparse(self.path)
        code, ctype, data = handle('POST', u.path, {}, body, self.games)
        self._send(code, ctype, data)


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = False


def make_server(games=None, port=None):
    h = type('H', (Handler,), {'games': games or _games_default()})
    for p in ([port] if port else PORTS):
        try:
            return Server(('127.0.0.1', p), h)
        except OSError:
            continue
    raise OSError('no free port for the voice booth')


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description='Stark World Engine voice booth (local only)')
    ap.add_argument('--games', default=None, help='My_Games folder (default: next to studio_app.py)')
    ap.add_argument('--port', type=int, default=None)
    ap.add_argument('--world', default=None)
    ap.add_argument('--no-browser', action='store_true')
    a = ap.parse_args(argv)
    srv = make_server(a.games, a.port)
    url = 'http://127.0.0.1:%d/voice%s' % (srv.server_address[1], ('?world=' + urllib.parse.quote(a.world)) if a.world else '')
    print('Stark World Engine voice booth at %s  (local only, Ctrl+C to stop)' % url)
    if not a.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


PAGE = r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Voice Booth - Stark World Engine</title>
<meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="data:,">
<style>
:root{--bg:#0f1418;--panel:#172027;--panel2:#1d2933;--ink:#e9eef2;--mute:#93a3b1;--acc:#5fd3b5;--acc2:#ffd166;--line:#2a3742;--bad:#ff6b81;--rec:#ff4d6d}
*{box-sizing:border-box}html,body{margin:0;height:100%;background:var(--bg);color:var(--ink);font:14px system-ui,Segoe UI,sans-serif}
header{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:8px 12px;border-bottom:1px solid var(--line)}
header b{color:var(--acc)}button,select,input{background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:7px;padding:6px 10px;font:inherit}
button{cursor:pointer}button:hover{border-color:var(--acc)}button:disabled{opacity:.4;cursor:default}button.on{background:var(--acc);color:#04201a}
#wrap{display:grid;grid-template-columns:330px 1fr;height:calc(100% - 50px)}#list{overflow:auto;border-right:1px solid var(--line);padding:8px}
.ln{padding:6px 8px;border-radius:7px;cursor:pointer;margin:2px 0;display:flex;gap:8px;align-items:flex-start}.ln:hover{background:var(--panel)}.ln.cur{background:var(--panel2);outline:1px solid var(--acc)}
.dot{width:9px;height:9px;border-radius:5px;margin-top:5px;flex:none;background:#3a4753}.dot.t{background:var(--acc2)}.dot.p{background:var(--acc)}
.sp{color:var(--acc2);font-size:12px}.tx{font-size:13px;color:#cfd8df}.grp{font-size:11px;color:var(--mute);text-transform:uppercase;letter-spacing:.6px;margin:12px 4px 4px}
main{overflow:auto;padding:14px 18px}#prompt{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:18px 22px;margin-bottom:12px}
#pSpeaker{color:var(--acc2);font-weight:700}#pText{font-size:30px;line-height:1.35;margin:8px 0}#pNext{color:var(--mute);font-size:16px;border-top:1px dashed var(--line);padding-top:8px}
.row{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:8px 0}#rec{background:var(--rec);color:#fff;border:0;font-weight:700;padding:10px 18px;font-size:16px}#rec.live{animation:p 1s infinite}
@keyframes p{50%{opacity:.6}}#meter{width:220px;height:12px;background:#0b1013;border:1px solid var(--line);border-radius:6px;overflow:hidden;position:relative}#meterBar{height:100%;width:0;background:linear-gradient(90deg,#2a9d8f,#ffd166 75%,#ff4d6d)}
#clip{color:var(--bad);font-weight:700;visibility:hidden}.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin:10px 0}.card h4{margin:0 0 6px;color:var(--acc2)}
.take{display:flex;gap:8px;align-items:center;padding:4px 0;border-bottom:1px solid #22303a}.take .best{color:var(--acc2)}canvas{background:#0b1013;border:1px solid var(--line);border-radius:6px;width:100%;height:90px}
.mute{color:var(--mute);font-size:12px}.warn{color:var(--bad)}#drop{border:2px dashed var(--line);border-radius:10px;padding:14px;text-align:center;color:var(--mute)}#drop.over{border-color:var(--acc);color:var(--ink)}
kbd{background:#0b1013;border:1px solid var(--line);border-radius:4px;padding:0 5px;font-size:11px}
@media(max-width:860px){#wrap{grid-template-columns:1fr;height:auto}#list{max-height:40vh}}
</style></head><body>
<header><b>Voice Booth</b><select id="world" aria-label="Game"></select><select id="who" aria-label="Character"><option value="">All characters</option></select>
<span class="mute" id="stat"></span><span style="flex:1"></span><a id="script" class="mute" href="#" target="_blank">Recording script</a></header>
<div id="wrap"><nav id="list" aria-label="Lines"></nav><main>
<div class="mute" id="consent">Your microphone is used only after your browser asks you. Recordings are saved on this PC (My_Games/&lt;game&gt;/voice) and are never uploaded anywhere.</div>
<div id="prompt"><div><span id="pSpeaker"></span> <span class="mute" id="pId"></span><canvas id="mouth" width="64" height="36" style="width:64px;height:36px;float:right;background:rgba(255,220,200,.12)" title="Lip-sync preview"></canvas></div><div id="pText">Pick a game to start.</div><div id="subNow" style="color:var(--acc);min-height:20px"></div><div id="pNext"></div></div>
<div class="row"><button id="rec" title="Record / stop (Space)">Record</button><select id="countin" aria-label="Count-in"><option value="0">No count-in</option><option value="1" selected>1 s count-in</option><option value="3">3 s count-in</option></select>
<label class="mute"><input type="checkbox" id="auto" checked> Next line after each take</label><div id="meter" aria-hidden="true"><div id="meterBar"></div></div><span id="clip">CLIP</span><span class="mute" id="timer"></span></div>
<div class="row"><button id="prev">&larr; Prev</button><button id="next">Next &rarr;</button><button id="playBest" title="Play processed line (P)">Play processed</button><button id="tts" title="Placeholder computer voice">Hear placeholder voice</button>
<span class="mute"><kbd>Space</kbd> record/stop <kbd>&larr;</kbd><kbd>&rarr;</kbd> lines <kbd>P</kbd> play <kbd>1</kbd>-<kbd>9</kbd> pick take</span></div>
<div class="card"><h4>Takes</h4><div id="takes" class="mute">No takes yet.</div><canvas id="wave" width="900" height="90"></canvas>
<div class="row"><span class="mute">Trim</span><input type="range" id="trimA" min="0" max="1000" value="0" aria-label="Trim start"><input type="range" id="trimB" min="0" max="1000" value="1000" aria-label="Trim end"><button id="trimSave">Apply trim</button><button id="trimClear">Clear trim</button><span class="mute" id="trimInfo"></span></div>
<div class="mute" id="procInfo"></div></div>
<div class="card"><h4>Character voice</h4><div class="row"><span id="chName" class="sp"></span><select id="profile" aria-label="Voice profile"></select><button id="preview">Preview on this line</button></div>
<div class="mute">One person can voice the whole cast: each character gets a pitch / formant / EQ profile applied after recording.</div></div>
<div class="card"><h4>Clean-up chain</h4><div class="row"><label><input type="checkbox" data-ch="hpf"> High-pass 80 Hz</label><label><input type="checkbox" data-ch="gate"> Noise gate</label><label><input type="checkbox" data-ch="deess"> De-ess</label><label><input type="checkbox" data-ch="trim"> Auto trim silence</label><label><input type="checkbox" data-ch="normalize"> Loudness</label>
<select id="target" aria-label="Dialogue loudness"><option value="-16">-16 LUFS (loud)</option><option value="-18">-18 LUFS (game dialogue)</option><option value="-20">-20 LUFS</option><option value="-23">-23 LUFS (broadcast)</option></select></div></div>
<div class="card"><h4>Import lines recorded elsewhere</h4><div id="drop">Drop WAV / FLAC / MP3 / OGG files here, or <input type="file" id="files" multiple accept=".wav,.flac,.mp3,.ogg,.opus,.webm,.m4a,audio/*"><br>Files are matched by the name in the recording script, the line id, or the script number.</div><div id="impRep" class="mute"></div></div>
</main></div>
<script>%%JS%%</script></body></html>
'''


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
