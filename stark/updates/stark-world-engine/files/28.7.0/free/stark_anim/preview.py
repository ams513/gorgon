"""preview.py - render GIF / MP4 previews of clips on a real skinned character in a private headless Chrome.

  python -m stark_anim preview CLIP.gclip.json [--out DIR] [--fps 20] [--size 360x400] [--mp4]
The page (preview/preview.html) runs the same runtime as cartridges (runtime/stark_anim.js) on a WebGL2 view of
the character, so a preview shows exactly what a game will play. Frames go to ffmpeg when it is installed (GIF with
a generated palette + H.264 MP4); without ffmpeg a pure-Python GIF encoder is used (MP4 then needs ffmpeg).
Nothing leaves 127.0.0.1.
"""
import base64, functools, http.server, json, os, shutil, socketserver, struct, subprocess, sys, tempfile, threading, zlib

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STAGED_CHAR = os.path.join(ROOT, 'third_party_assets', '_staging', 'quaternius_universal-base-characters', 'x',
                           'Universal Base Characters[Standard]', 'Base Characters', 'Godot - UE')


def find_character_model(prefer='male_adventurer'):
    """Character for previews: a 'character' entry in assets/library.json (v28.5 asset library) when it exists
    (prefer = an id such as 'female_guard', or just 'male' / 'female'), else the staged Quaternius base character."""
    lib = os.path.join(ROOT, 'assets', 'library.json')
    if os.path.isfile(lib):
        try:
            with open(lib, encoding='utf-8') as f:
                d = json.load(f)
            chars = [a for a in d.get('assets', []) if a.get('kind') == 'character' or a.get('category') == 'character']
            def rank(a):
                i = str(a.get('id', '')).lower().split('/')[-1]
                return (0 if i == prefer else 1 if i.startswith(prefer + '_') else 2, i)
            chars.sort(key=rank)
            for a in chars:
                p = os.path.join(ROOT, 'assets', a['file'])
                if os.path.isfile(p):
                    return p
        except (OSError, ValueError, KeyError):
            pass
    name = 'Superhero_Female_FullBody.gltf' if prefer.startswith('female') else 'Superhero_Male_FullBody.gltf'
    p = os.path.join(STAGED_CHAR, name)
    if os.path.isfile(p):
        return p
    raise FileNotFoundError('no rigged character found (asset library has none and the Quaternius base characters '
                            'are not staged)')


def find_ffmpeg():
    for cand in (os.environ.get('GS_FFMPEG'), shutil.which('ffmpeg')):
        if cand and os.path.isfile(cand):
            return cand
    try:
        with open(os.path.join(ROOT, 'config.json'), encoding='utf-8') as f:
            c = json.load(f).get('ffmpeg')
        if c and os.path.isfile(c):
            return c
    except (OSError, ValueError):
        pass
    return None


def _cdp():
    sys.path.insert(0, os.path.join(ROOT, 'marketing'))
    try:
        import cdp  # the studio's stdlib-only Chrome DevTools client
    except ImportError as e:
        raise RuntimeError('marketing/cdp.py (headless Chrome client) is missing') from e
    return cdp


ALLOWED = ('stark_anim/', 'assets/', 'third_party_assets/_staging/quaternius_universal-base-characters/',
           'third_party_assets/_staging/quaternius_universal-animation-library/')


class _Quiet(http.server.SimpleHTTPRequestHandler):
    allowed = ALLOWED

    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def send_head(self):  # only the folders a preview needs, never the rest of the workspace (keys, configs)
        from urllib.parse import unquote, urlsplit
        rel = unquote(urlsplit(self.path).path).lstrip('/')
        if '..' in rel.split('/') or not rel.startswith(self.allowed):
            self.send_error(404)
            return None
        return super().send_head()


class LocalServer:
    """Serve one folder on 127.0.0.1 (random port) for the headless page."""

    def __init__(self, root=ROOT):
        self.root = os.path.abspath(root)
        handler = functools.partial(_Quiet, directory=self.root)
        socketserver.TCPServer.allow_reuse_address = True
        self.httpd = socketserver.ThreadingTCPServer(('127.0.0.1', 0), handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def url(self, path):
        from urllib.parse import quote
        rel = os.path.relpath(os.path.abspath(path), self.root).replace('\\', '/')
        if rel.startswith('..'):
            raise ValueError(f'{path} is outside the served folder {self.root}')
        return f'http://127.0.0.1:{self.port}/' + quote(rel)

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _clip_json(c):
    from .clip import Clip
    if isinstance(c, Clip):
        return c.to_dict()
    if isinstance(c, dict):
        return c
    with open(c, encoding='utf-8') as f:
        return json.load(f)


class Previewer:
    """with Previewer() as pv: pv.render(clip, 'out/walk', play='Walk_Loop')"""

    def __init__(self, width=360, height=400, model=None):
        self.width, self.height = width, height
        self.model = model or find_character_model()
        self.srv = LocalServer(ROOT if os.path.commonpath([os.path.abspath(self.model), ROOT]) == ROOT
                               else os.path.commonpath([os.path.abspath(self.model), ROOT]))
        self.browser = _cdp().Browser(width, height)
        self.browser.goto(self.srv.url(os.path.join(HERE, 'preview', 'preview.html')), settle=0.3)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def close(self):
        try:
            self.browser.close()
        finally:
            self.srv.close()

    def setup(self, clips, play=None, machine=None, params=None, root_motion=True, cam=None):
        o = {'model': self.srv.url(self.model), 'clips': [_clip_json(c) for c in clips], 'play': play,
             'machine': machine, 'params': params or {}, 'rootMotion': root_motion, 'capture': True,
             'width': self.width, 'height': self.height}
        o.update(cam or {})
        return self.browser.js(f'GS_PREVIEW.init({json.dumps(o)})', await_promise=True)

    def frames(self, n, fps, fmt='png', every=None):
        out = []
        for k in range(n):
            self.browser.js(f'GS_PREVIEW.step({1.0 / fps})')
            if every:
                every(k)
            out.append(base64.b64decode(self.browser.js(f'GS_PREVIEW.capture({json.dumps(fmt)})')))
        return out

    def render(self, clip, out_base, play=None, seconds=None, fps=20, mp4=True, gif=True, loops=1, **kw):
        """Render `clip` (Clip, dict or path) to out_base.gif / out_base.mp4. Returns paths + frame count."""
        cj = _clip_json(clip)
        play = play or cj.get('name')
        dur = seconds or max(1.0, float(cj.get('duration', 1.0)) * loops)
        n = max(2, int(round(dur * fps)))
        self.setup([cj], play=play, **kw)
        pngs = self.frames(n, fps)
        return encode(pngs, out_base, fps, self.width, self.height, mp4=mp4, gif=gif)


def encode(pngs, out_base, fps, width, height, mp4=True, gif=True):
    os.makedirs(os.path.dirname(os.path.abspath(out_base)), exist_ok=True)
    res = {'frames': len(pngs)}
    ff = find_ffmpeg()
    if ff:
        tmp = tempfile.mkdtemp(prefix='gs_anim_prev_')
        try:
            for i, b in enumerate(pngs):
                with open(os.path.join(tmp, f'f{i:05d}.png'), 'wb') as f:
                    f.write(b)
            src = ['-framerate', str(fps), '-i', os.path.join(tmp, 'f%05d.png')]
            if gif:
                g = out_base + '.gif'
                subprocess.run([ff, '-y', '-loglevel', 'error'] + src + [
                    '-vf', 'split[a][b];[a]palettegen=max_colors=192:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:'
                           'bayer_scale=3:diff_mode=rectangle', '-loop', '0', g], check=True)
                res['gif'] = g
            if mp4:
                m = out_base + '.mp4'
                subprocess.run([ff, '-y', '-loglevel', 'error'] + src + [
                    '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '20',
                    '-movflags', '+faststart', m], check=True)
                res['mp4'] = m
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return res
    if gif:
        frames = [png_decode(b) for b in pngs]
        g = out_base + '.gif'
        write_gif(g, frames, fps)
        res['gif'] = g
    res['note'] = 'ffmpeg not found: GIF written by the built-in encoder, no MP4'
    return res


# ---------------------------------------------------------------- PNG decode (only what Chrome writes)
def png_decode(data):
    """8-bit RGB/RGBA non-interlaced PNG -> uint8 [h, w, 3]."""
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('not a PNG')
    pos, idat, w = 8, b'', 0
    while pos < len(data):
        n, typ = struct.unpack('>I4s', data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        if typ == b'IHDR':
            w, h, depth, ctype = struct.unpack('>IIBB', body[:10])
            if depth != 8 or ctype not in (2, 6) or body[12] != 0:
                raise ValueError('unsupported PNG layout')
            bpp = 4 if ctype == 6 else 3
        elif typ == b'IDAT':
            idat += body
        pos += 12 + n
    raw = np.frombuffer(zlib.decompress(idat), np.uint8).reshape(h, 1 + w * bpp)
    out = np.zeros((h, w * bpp), np.int32)
    prev = np.zeros(w * bpp, np.int32)
    for y in range(h):
        f, line = raw[y, 0], raw[y, 1:].astype(np.int32)
        if f == 0:
            cur = line
        elif f == 2:
            cur = (line + prev) & 255
        else:
            cur = np.zeros_like(line)
            for x in range(w * bpp):
                a = cur[x - bpp] if x >= bpp else 0
                b = prev[x]
                c = prev[x - bpp] if x >= bpp else 0
                if f == 1:
                    pr = a
                elif f == 3:
                    pr = (a + b) >> 1
                else:
                    p = a + b - c
                    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                    pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                cur[x] = (line[x] + pr) & 255
        out[y] = cur
        prev = cur
    return out.reshape(h, w, bpp)[:, :, :3].astype(np.uint8)


# ---------------------------------------------------------------- GIF (pure Python fallback)
def _palette(frames, n=255, iters=8):
    px = np.concatenate([f.reshape(-1, 3)[::max(1, f.size // 3 // 20000)] for f in frames]).astype(np.float64)
    rng = np.random.default_rng(1)
    cent = px[rng.choice(len(px), size=min(n, len(px)), replace=False)]
    for _ in range(iters):
        d = ((px[:, None, :] - cent[None]) ** 2).sum(-1)
        lab = d.argmin(1)
        for k in range(len(cent)):
            m = lab == k
            if m.any():
                cent[k] = px[m].mean(0)
    pal = np.zeros((256, 3), np.uint8)
    pal[:len(cent)] = np.clip(np.round(cent), 0, 255)
    return pal, len(cent)


def _index(frame, pal, n):
    flat = frame.reshape(-1, 3).astype(np.int32)
    out = np.empty(len(flat), np.uint8)
    P = pal[:n].astype(np.int32)
    for s in range(0, len(flat), 8192):
        d = ((flat[s:s + 8192, None, :] - P[None]) ** 2).sum(-1)
        out[s:s + 8192] = d.argmin(1)
    return out


def _lzw(indices):
    clear, eoi, size = 256, 257, 9
    table = {(i,): i for i in range(256)}
    nxt = 258
    out, bits, nbits = bytearray(), 0, 0

    def emit(code):
        nonlocal bits, nbits
        bits |= code << nbits
        nbits += size
        while nbits >= 8:
            out.append(bits & 255)
            bits >>= 8
            nbits -= 8
    emit(clear)
    w = (int(indices[0]),)
    for k in indices[1:]:
        wk = w + (int(k),)
        if wk in table:
            w = wk
            continue
        emit(table[w])
        if nxt < 4096:
            table[wk] = nxt
            nxt += 1
            if nxt > (1 << size) and size < 12:
                size += 1
        else:
            emit(clear)
            table = {(i,): i for i in range(256)}
            nxt, size = 258, 9
        w = (int(k),)
    emit(table[w])
    emit(eoi)
    if nbits:
        out.append(bits & 255)
    return bytes(out)


def write_gif(path, frames, fps):
    h, w = frames[0].shape[:2]
    pal, n = _palette(frames)
    delay = max(2, int(round(100 / fps)))
    out = bytearray(b'GIF89a' + struct.pack('<HHBBB', w, h, 0xF7, 0, 0) + pal.tobytes())
    out += b'\x21\xFF\x0BNETSCAPE2.0\x03\x01\x00\x00\x00'
    for f in frames:
        idx = _index(f, pal, n)
        out += b'\x21\xF9\x04\x04' + struct.pack('<H', delay) + b'\x00\x00'
        out += b'\x2C' + struct.pack('<HHHHB', 0, 0, w, h, 0) + b'\x08'
        data = _lzw(idx)
        for i in range(0, len(data), 255):
            blk = data[i:i + 255]
            out += bytes([len(blk)]) + blk
        out += b'\x00'
    out += b'\x3B'
    with open(path, 'wb') as fo:
        fo.write(out)
    return path
