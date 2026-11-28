"""Real-browser listen-check: render a scripted gameplay scenario through the cartridge runtime in headless Chrome
(OfflineAudioContext, Opus decoded by the browser itself) and bring the rendered mix back as a WAV.

run_scenario(pack, events, seconds) -> {'wav': bytes, 'log': [...], 'debug': {...}}
A minimal Chrome DevTools Protocol client (stdlib sockets, no packages). Local only: Chrome is started headless with
a throwaway profile and a page from a temp folder; nothing is fetched from the network.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import urllib.request

from . import pack as packmod

CHROMES = [r'C:\Program Files\Google\Chrome\Application\chrome.exe',
           r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
           r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
           r'C:\Program Files\Microsoft\Edge\Application\msedge.exe']


def find_browser():
    for p in CHROMES:
        if os.path.isfile(p):
            return p
    for name in ('chrome', 'google-chrome', 'chromium', 'msedge'):
        p = shutil.which(name)
        if p:
            return p
    return None


class WS:
    """Just enough RFC 6455 for CDP on localhost (text frames, fragmentation, ping)."""

    def __init__(self, url, timeout=120):
        assert url.startswith('ws://')
        hostport, path = url[5:].split('/', 1)
        host, port = hostport.split(':')
        self.s = socket.create_connection((host, int(port)), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = ('GET /%s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
               'Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n' % (path, hostport, key))
        self.s.sendall(req.encode())
        buf = b''
        while b'\r\n\r\n' not in buf:
            chunk = self.s.recv(4096)
            if not chunk:
                raise ConnectionError('websocket handshake failed')
            buf += chunk
        if b' 101 ' not in buf.split(b'\r\n', 1)[0]:
            raise ConnectionError('websocket upgrade refused')
        self.rest = buf.split(b'\r\n\r\n', 1)[1]
        self.n = 0

    def _read(self, k):
        while len(self.rest) < k:
            chunk = self.s.recv(1 << 20)
            if not chunk:
                raise ConnectionError('websocket closed')
            self.rest += chunk
        out, self.rest = self.rest[:k], self.rest[k:]
        return out

    def send(self, text):
        data = text.encode('utf-8')
        hdr = bytearray([0x81])
        n = len(data)
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr.append(0x80 | 126)
            hdr += struct.pack('>H', n)
        else:
            hdr.append(0x80 | 127)
            hdr += struct.pack('>Q', n)
        mask = os.urandom(4)
        hdr += mask
        body = bytes(b ^ mask[i % 4] for i, b in enumerate(data)) if n < 4096 else _mask_fast(data, mask)
        self.s.sendall(bytes(hdr) + body)

    def recv(self):
        parts = []
        while True:
            b0, b1 = self._read(2)
            op = b0 & 0x0F
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack('>H', self._read(2))[0]
            elif n == 127:
                n = struct.unpack('>Q', self._read(8))[0]
            mask = self._read(4) if b1 & 0x80 else None
            payload = self._read(n)
            if mask:
                payload = _mask_fast(payload, mask)
            if op == 9:
                self.s.sendall(bytes([0x8A, 0]))
                continue
            if op == 8:
                raise ConnectionError('websocket closed by browser')
            parts.append(payload)
            if b0 & 0x80:
                return b''.join(parts).decode('utf-8', 'replace')

    def call(self, method, params=None, timeout=300):
        self.n += 1
        mid = self.n
        self.send(json.dumps({'id': mid, 'method': method, 'params': params or {}}))
        t0 = time.time()
        while time.time() - t0 < timeout:
            msg = json.loads(self.recv())
            if msg.get('id') == mid:
                if 'error' in msg:
                    raise RuntimeError('CDP %s: %s' % (method, msg['error']))
                return msg.get('result', {})
        raise TimeoutError(method)

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass


def _mask_fast(data, mask):
    import numpy as np
    a = np.frombuffer(data, np.uint8)
    m = np.frombuffer((mask * (len(a) // 4 + 1))[:len(a)], np.uint8)
    return (a ^ m).tobytes()


HARNESS = r'''
window.__gaRun = function (spec) {
  var sr = 48000, n = Math.ceil(spec.seconds * sr), off = new OfflineAudioContext(2, n, sr), log = [];
  ['cue', 'stinger', 'caption', 'line_start', 'line_end', 'zone', 'subtitle'].forEach(function (k) {
    StarkAudio.on(k, function (d) { log.push([+off.currentTime.toFixed(3), k, d && (d.state || d.name || d.text || d.lineId || d.type || '')]); });
  });
  var vis = 0; StarkAudio.on('viseme', function (d) { if (d.v !== 'X') vis++; });
  StarkAudio.init({context: off, offline: true, pack: window.STARK_AUDIO_PACK});
  StarkAudio.unlock();
  return StarkAudio.whenReady().then(function () {
    var evs = spec.events.slice().sort(function (a, b) { return a[0] - b[0]; }), k = 0, step = 0.05;
    function at(t) {
      off.suspend(t).then(function () {
        while (k < evs.length && evs[k][0] <= t + 1e-6) { try { StarkAudio.emit(evs[k][1], evs[k][2] || {}); } catch (e) { log.push([t, 'error', String(e)]); } k++; }
        StarkAudio._tick();
        off.resume();
      });
    }
    for (var t = step; t < spec.seconds - step; t += step) at(Math.round(t * 1000) / 1000);
    return off.startRendering();
  }).then(function (buf) {
    var L = buf.getChannelData(0), R = buf.getChannelData(1), m = L.length, ab = new ArrayBuffer(44 + m * 4), d = new DataView(ab);
    function W(o, s) { for (var i = 0; i < s.length; i++) d.setUint8(o + i, s.charCodeAt(i)); }
    W(0, 'RIFF'); d.setUint32(4, 36 + m * 4, true); W(8, 'WAVE'); W(12, 'fmt '); d.setUint32(16, 16, true); d.setUint16(20, 1, true); d.setUint16(22, 2, true);
    d.setUint32(24, sr, true); d.setUint32(28, sr * 4, true); d.setUint16(32, 4, true); d.setUint16(34, 16, true); W(36, 'data'); d.setUint32(40, m * 4, true);
    var peak = 0;
    for (var i = 0; i < m; i++) { var a = L[i], b = R[i]; if (Math.abs(a) > peak) peak = Math.abs(a); if (Math.abs(b) > peak) peak = Math.abs(b);
      d.setInt16(44 + i * 4, Math.max(-32768, Math.min(32767, Math.round(a * 32767))), true); d.setInt16(46 + i * 4, Math.max(-32768, Math.min(32767, Math.round(b * 32767))), true); }
    var u = new Uint8Array(ab), s = '', CH = 0x8000;
    for (var j = 0; j < u.length; j += CH) s += String.fromCharCode.apply(null, u.subarray(j, j + CH));
    return JSON.stringify({wav: btoa(s), log: log, visemes: vis, peak: peak, debug: StarkAudio.debug()});
  });
};
'''


def page_for(pack, extra_js=''):
    html = '<!doctype html><html><head><meta charset="utf-8"><title>ga test</title></head><body><div id="x"></div></body></html>'
    html = packmod.inject(html, pack, boot=False)
    return html.replace('</body>', '<script>' + HARNESS + extra_js + '</script></body>')


def run_scenario(pack, events, seconds=30.0, browser=None, timeout=300):
    exe = browser or find_browser()
    if not exe:
        raise FileNotFoundError('no Chrome or Edge found')
    tmp = tempfile.mkdtemp(prefix='ga_browser_')
    prof = os.path.join(tmp, 'profile')
    os.makedirs(prof)
    page = os.path.join(tmp, 'page.html')
    with open(page, 'w', encoding='utf-8') as fh:
        fh.write(page_for(pack))
    url = 'file:///' + page.replace('\\', '/')
    proc = subprocess.Popen([exe, '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
                             '--disable-extensions', '--disable-background-networking', '--mute-audio',
                             '--autoplay-policy=no-user-gesture-required', '--user-data-dir=' + prof,
                             '--remote-debugging-port=0', url],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ws = None
    try:
        port = None
        t0 = time.time()
        while time.time() - t0 < 30:
            p = os.path.join(prof, 'DevToolsActivePort')
            if os.path.isfile(p):
                with open(p) as fh:
                    first = fh.readline().strip()
                if first.isdigit():
                    port = int(first)
                    break
            time.sleep(0.1)
        if not port:
            raise TimeoutError('browser did not open a debugging port')
        target = None
        t0 = time.time()
        while time.time() - t0 < 20 and not target:
            with urllib.request.urlopen('http://127.0.0.1:%d/json/list' % port, timeout=5) as r:
                lst = json.loads(r.read().decode())
            target = next((x for x in lst if x.get('type') == 'page' and x.get('url', '').startswith('file:')), None)
            if not target:
                time.sleep(0.2)
        if not target:
            raise RuntimeError('page target not found')
        ws = WS(target['webSocketDebuggerUrl'], timeout=timeout)
        t0 = time.time()
        while time.time() - t0 < 30:
            r = ws.call('Runtime.evaluate', {'expression': 'document.readyState + "|" + (typeof window.__gaRun)', 'returnByValue': True})
            if r.get('result', {}).get('value') == 'complete|function':
                break
            time.sleep(0.1)
        spec = json.dumps({'seconds': float(seconds), 'events': events})
        r = ws.call('Runtime.evaluate', {'expression': 'window.__gaRun(%s)' % spec, 'awaitPromise': True,
                                         'returnByValue': True, 'timeout': timeout * 1000}, timeout=timeout)
        if 'exceptionDetails' in r:
            raise RuntimeError('page error: %s' % json.dumps(r['exceptionDetails'])[:600])
        out = json.loads(r['result']['value'])
        out['wav'] = base64.b64decode(out['wav'])
        return out
    finally:
        if ws:
            ws.close()
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)


DEMO_EVENTS = (
    [[0.0, 'enter_region', {'level': 0, 'discovery': False}], [0.1, 'intensity', {'value': 1.0}]]
    + [[4.0 + 0.45 * i, 'footstep', {'player': True}] for i in range(10)]
    + [[9.0, 'enemy_alert', {'kind': 'grunt', 'x': 8, 'z': -6, 'level': 1.0}], [11.0, 'combat_start', {}]]
    + [[11.6 + 0.7 * i, 'attack', {}] for i in range(10)]
    + [[11.9 + 0.7 * i, 'hit', {'material': 'flesh' if i % 3 else 'metal', 'x': 2, 'z': -3}] for i in range(10)]
    + [[18.5, 'enemy_die', {'x': 2, 'z': -3}], [19.0, 'tension_end', {}], [19.2, 'combat_end', {'victory': True}],
       [27.0, 'quest_complete', {'id': 'q'}], [30.0, 'dialogue_start', {}], [30.2, 'dialogue_line', {'lineId': 'demo/voice'}],
       [34.5, 'dialogue_end', {}], [36.0, 'menu_open', {}], [38.0, 'menu_close', {}]]
)
