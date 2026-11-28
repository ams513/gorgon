"""studio_api.py - the animation department's studio routes. studio_app.Handler forwards to these (see
INTEGRATION_PATCHES.md); every function returns (status, body, content_type, extra_headers) for Handler._send.

GET  /anim/<file>              stark_anim web files (runtime, webcam pages, models, library) - read-only, allow-listed
GET  /api/anim/status          models installed? ffmpeg / Chrome / FBX converter found? library size
GET  /api/anim/library         ?q=walk&category=locomotion&child=1  -> clips (built-in + My_Games/_animations)
GET  /api/anim/clip            ?id=quaternius_ual/Walk_Loop        -> one .gclip.json
POST /api/anim/take            mocap take JSON (landmarks only)     -> solved, cleaned clip saved in My_Games/_animations
POST /api/anim/import          {name, b64, ext: bvh|glb|gltf|fbx}  -> retargeted + cleaned clips saved
POST /api/anim/export          {ids: [...], target: godot|ue5}      -> AnimationLibrary .tres / UE5 GLB + import settings
POST /api/anim/preview         {id}                                 -> GIF + MP4 preview (headless Chrome)
User files live in My_Games/_animations/{takes,clips,exports,previews}; nothing is written anywhere else.
"""
import base64, json, os, re, tempfile, threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
USER_DIR = '_animations'
TYPES = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8',
         '.wasm': 'application/wasm', '.json': 'application/json', '.task': 'application/octet-stream',
         '.gif': 'image/gif', '.mp4': 'video/mp4', '.png': 'image/png', '.txt': 'text/plain; charset=utf-8',
         '.md': 'text/plain; charset=utf-8'}
SERVE_DIRS = ('runtime', 'webcam', 'preview', 'models', 'library', 'data', 'previews', 'fixtures')
# mocap pages compile MediaPipe's WebAssembly: they need 'wasm-unsafe-eval' (nothing else is relaxed)
CSP_PAGES = ("default-src 'self' 'unsafe-inline' data: blob:; script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval' blob:; "
             "connect-src 'self'; img-src 'self' data: blob:; media-src 'self' blob: mediastream:; worker-src 'self' blob:")
MAX_TAKE_FRAMES = 30 * 120
_LOCK = threading.Lock()


def _ok(body, code=200, ctype='application/json; charset=utf-8', extra=None):
    return code, body, ctype, extra or {}


def _err(code, msg):
    return code, {'error': msg}, 'application/json; charset=utf-8', {}


def _user_dir(games, sub):
    base = os.path.realpath(os.path.join(games, USER_DIR, sub))
    if not base.startswith(os.path.realpath(games) + os.sep):
        raise PermissionError('animation folder escapes My_Games')
    os.makedirs(base, exist_ok=True)
    return base


def _safe_name(name, default='clip'):
    n = re.sub(r'[^A-Za-z0-9_\-]+', '_', str(name or default)).strip('_')[:60]
    return n or default


def _static(rel):
    rel = rel.replace('\\', '/')
    parts = [p for p in rel.split('/') if p]
    if not parts or parts[0] not in SERVE_DIRS or any(p in ('..', '.') or ':' in p for p in parts):
        return _err(404, 'not found')
    p = os.path.realpath(os.path.join(HERE, *parts))
    ext = os.path.splitext(p)[1].lower()
    if not p.startswith(HERE + os.sep) or ext not in TYPES or not os.path.isfile(p):
        return _err(404, 'not found')
    with open(p, 'rb') as f:
        data = f.read()
    extra = {'Content-Security-Policy': CSP_PAGES} if ext == '.html' else {}
    return _ok(data, 200, TYPES[ext], extra)


def status():
    from . import model_store, preview
    from .fbx import find_converter
    st = model_store.status(verify=False)
    try:
        chrome = bool(preview._cdp().find_chrome())
    except Exception:  # noqa: BLE001
        chrome = False
    conv = find_converter()
    return {'models': st['roles'], 'models_installed': st['installed'], 'ffmpeg': bool(preview.find_ffmpeg()),
            'chrome': chrome, 'fbx_converter': conv['kind'] if conv else None,
            'library_clips': _index_count()}


def _index_count():
    p = os.path.join(HERE, 'library', 'index.json')
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f).get('count', 0)
    except (OSError, ValueError):
        return 0


def library(q, games):
    from . import library as L
    query = (q.get('q') or [''])[0]
    cat = (q.get('category') or [None])[0]
    child = (q.get('child') or ['0'])[0] in ('1', 'true')
    out = [dict(e, origin='built-in', id='lib:' + e['id']) for e in L.search(query, cat, child)]
    udir = os.path.join(games, USER_DIR, 'clips')
    if os.path.isdir(udir):
        for e in L.scan(udir):
            if 'error' in e:
                continue
            hay = (e['name'] + ' ' + ' '.join(e.get('tags', []))).lower()
            if query and not all(w in hay for w in query.lower().split()):
                continue
            if cat and e.get('category') != cat:
                continue
            if child and e.get('safety') in ('harm', 'intimacy'):
                continue
            out.append(dict(e, origin='mine', id='mine:' + e['id']))
    return {'clips': out, 'count': len(out)}


def _clip_path(cid, games):
    cid = str(cid or '')
    if cid.startswith('mine:'):
        base, rel = _user_dir(games, 'clips'), cid[5:]
    else:
        base, rel = os.path.join(HERE, 'library'), cid[4:] if cid.startswith('lib:') else cid
    p = os.path.realpath(os.path.join(base, rel + '.gclip.json'))
    if not p.startswith(os.path.realpath(base) + os.sep) or not os.path.isfile(p):
        raise FileNotFoundError('no such clip: ' + cid)
    return p


def save_take(body, games):
    """Mocap take (landmark numbers only) -> solve + clean -> clip in My_Games/_animations/clips."""
    from . import mocap, cleanup, child_safety
    if body.get('format') not in mocap.TAKE_FORMATS:
        raise ValueError('expected a stark-mocap-take')
    frames = body.get('frames') or []
    if not frames:
        raise ValueError('the take has no frames')
    if len(frames) > MAX_TAKE_FRAMES:
        raise ValueError('takes are limited to 2 minutes')
    raw = json.dumps(body)
    if 'data:image' in raw or 'data:video' in raw:
        raise ValueError('takes may only contain landmark numbers')
    name = _safe_name(body.get('name'), 'take')
    with _LOCK:
        tdir, cdir = _user_dir(games, 'takes'), _user_dir(games, 'clips')
        mocap.save_take(body, os.path.join(tdir, name + '.take.json'))
        clip, rep = mocap.take_to_clip(body, name=name)
        clip = cleanup.lock_feet(clip)
        clip = cleanup.add_footstep_events(clip)
        clip.tags = sorted(set(clip.tags + ['mocap', 'webcam']))
        clip.meta['category'] = 'mocap'
        child_safety.classify_clip(clip)
        out = os.path.join(cdir, name + '.gclip.json')
        clip.save(out)
    return {'clip': 'mine:' + name, 'file': os.path.relpath(out, games).replace('\\', '/'), 'report': rep,
            'summary': clip.summary()}


def import_file(body, games):
    """Uploaded motion file -> every clip retargeted to the Stark World Engine rig, cleaned, saved."""
    from .retarget import import_motion
    from . import cleanup, child_safety
    ext = str(body.get('ext') or '').lower().lstrip('.')
    if ext not in ('bvh', 'glb', 'gltf', 'fbx'):
        raise ValueError('supported: .bvh .glb .gltf .fbx')
    data = base64.b64decode(body.get('b64') or '', validate=True)
    if not data:
        raise ValueError('empty file')
    name = _safe_name(body.get('name'), 'import')
    work = tempfile.mkdtemp(prefix='gs_anim_imp_')
    src = os.path.join(work, name + '.' + ext)
    with open(src, 'wb') as f:
        f.write(data)
    out = []
    try:
        res = import_motion(src, root_motion=body.get('root_motion') or 'extract')
        cdir = _user_dir(games, 'clips')
        for clip, rep in res:
            c = cleanup.smooth(clip, 1.0) if body.get('smooth', True) else clip
            c = cleanup.lock_feet(c)
            c = cleanup.add_footstep_events(c)
            child_safety.classify_clip(c)
            cname = _safe_name(name if len(res) == 1 else f'{name}_{clip.name}')
            p = os.path.join(cdir, cname + '.gclip.json')
            c.save(p)
            out.append({'clip': 'mine:' + cname, 'report': {k: v for k, v in rep.items() if k not in ('map', 'target_map')},
                        'summary': c.summary()})
    finally:
        import shutil
        shutil.rmtree(work, ignore_errors=True)
    return {'imported': out}


def export_clips(body, games):
    from . import export_map
    from .clip import Clip
    ids = body.get('ids') or []
    if not ids:
        raise ValueError('no clips selected')
    clips = [Clip.load(_clip_path(i, games)) for i in ids]
    target = body.get('target')
    edir = _user_dir(games, 'exports')
    name = _safe_name(body.get('name'), 'animations')
    if target == 'godot':
        p = export_map.write_godot_library(clips, os.path.join(edir, name + '.tres'))
        return {'written': [os.path.relpath(p, games).replace('\\', '/')]}
    if target == 'ue5':
        files = export_map.write_ue5_package(clips, os.path.join(edir, name + '_ue5'))
        return {'written': [os.path.relpath(f, games).replace('\\', '/') for f in files]}
    raise ValueError('target must be godot or ue5')


def render_preview(body, games):
    from . import preview
    p = _clip_path(body.get('id'), games)
    pdir = _user_dir(games, 'previews')
    base = os.path.join(pdir, _safe_name(os.path.basename(p).replace('.gclip.json', '')))
    with preview.Previewer(360, 400) as pv:
        r = pv.render(p, base, fps=20, loops=2)
    return {k: (os.path.relpath(v, games).replace('\\', '/') if isinstance(v, str) and os.path.isabs(v) else v)
            for k, v in r.items()}


def embed_clips(world, games, limit_mb=8.0):
    """Clips a world asks for (world['anim_clips'] = ['mine:my_dance', 'lib:quaternius_ual/Wave', ...]) as gclip
    dicts for the cartridge (the runtime adds them to every character). Unknown ids are skipped; a size cap keeps
    cartridges small. Child-safety classes travel inside each clip's meta and are enforced by the runtime."""
    out, total = [], 0
    for cid in (world or {}).get('anim_clips') or []:
        try:
            p = _clip_path(cid, games)
        except FileNotFoundError:
            continue
        total += os.path.getsize(p)
        if total > limit_mb * 1024 * 1024:
            break
        with open(p, encoding='utf-8') as f:
            out.append(json.load(f))
    return out


def handle_get(path, q, games):
    """-> response tuple, or None when the path is not an animation route."""
    try:
        if path.startswith('/anim/'):
            from urllib.parse import unquote
            return _static(unquote(path[len('/anim/'):]))
        if path == '/api/anim/status':
            return _ok(status())
        if path == '/api/anim/library':
            return _ok(library(q, games))
        if path == '/api/anim/clip':
            with open(_clip_path((q.get('id') or [''])[0], games), encoding='utf-8') as f:
                return _ok(f.read())
    except FileNotFoundError as e:
        return _err(404, str(e))
    except (ValueError, PermissionError) as e:
        return _err(400, str(e))
    return None


def handle_post(path, body, games):
    try:
        if path == '/api/anim/take':
            return _ok(save_take(body, games))
        if path == '/api/anim/import':
            return _ok(import_file(body, games))
        if path == '/api/anim/export':
            return _ok(export_clips(body, games))
        if path == '/api/anim/preview':
            return _ok(render_preview(body, games))
    except FileNotFoundError as e:
        return _err(404, str(e))
    except (ValueError, PermissionError, KeyError) as e:
        return _err(400, str(e))
    return _err(404, 'not found')
