"""model_store.py - the ONLY code in Stark World Engine that may download the webcam-mocap models.

Owner-approved set (2026-11-19, "body + face"; relayed by the lead):
  1a  MediaPipe Pose Landmarker LITE   pose_landmarker_lite.task      5,777,746 B   Apache-2.0
  2   @mediapipe/tasks-vision 1.2.3    3 files from the npm tarball   ~15.2 MB      Apache-2.0
  3   MediaPipe Face Landmarker        face_landmarker.task           3,758,596 B   Apache-2.0
NOT approved: pose_landmarker_full / pose_landmarker_heavy (they are not listed here, so they can never be fetched).

  python -m stark_anim models status            what is installed, verified against the pins below
  python -m stark_anim models fetch --approved  download + verify + install (refuses without --approved)
  python -I stark_anim/model_store.py fetch --approved   same, isolated interpreter (stdlib only on purpose)

Safety rules this file follows:
  * fixed HTTPS URLs and pinned sizes + hashes; anything else is refused;
  * every download lands in its own new, empty temp folder, is size-capped while streaming and verified
    (md5 for the Google-storage models, npm sha512 integrity + sha1 for the tarball) before it is used;
  * the npm tarball is untrusted data: it is never extracted with tarfile's extract helpers and nothing in it is
    executed. Exactly three members are read as bytes (regular files only; absolute paths, '..', links and
    devices are rejected) and written to paths chosen here, then each is checked against its pinned sha256;
  * the runtime never touches the network: the studio and cartridges load these files from disk.
"""
import base64, datetime, hashlib, json, os, shutil, sys, tarfile, tempfile, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(HERE, 'models')
LICENSE_SRC = os.path.join(HERE, 'data', 'LICENSE-Apache-2.0.txt')
APPROVAL = 'owner approved "body + face" (items 1a, 2, 3) on 2026-11-19, relayed by the lead'
UA = 'StarkWorldEngine-anim-model-store/1.0'

APPROVED = [
    {'id': 'pose_landmarker_lite', 'role': 'body', 'kind': 'file',
     'url': 'https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/latest/'
            'pose_landmarker_lite.task',
     'dest': 'pose_landmarker_lite.task', 'bytes': 5777746, 'md5': '04a75ddf7c811ac7a1a4523266dd7d88',
     'licence': 'Apache-2.0', 'credit': 'Google LLC / The MediaPipe Authors',
     'about': 'MediaPipe Pose Landmarker (BlazePose GHUM 3D, lite, float16): 33 body landmarks + 3D world landmarks'},
    {'id': 'face_landmarker', 'role': 'face', 'kind': 'file',
     'url': 'https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/'
            'face_landmarker.task',
     'dest': 'face_landmarker.task', 'bytes': 3758596, 'md5': 'b0e7274907a1644404fef66b28dd6d85',
     'licence': 'Apache-2.0', 'credit': 'Google LLC / The MediaPipe Authors',
     'about': 'MediaPipe Face Landmarker (float16): 478 face landmarks + 52 ARKit-style blendshape scores'},
    {'id': 'tasks_vision', 'role': 'runtime', 'kind': 'npm',
     'url': 'https://registry.npmjs.org/@mediapipe/tasks-vision/-/tasks-vision-1.2.3.tgz',
     'package': '@mediapipe/tasks-vision', 'version': '1.2.3',
     'integrity': 'sha512-sxJyfhbN+EamexhaaCmUlw7v8vWWMum7VDsORe6d4c8Dk/xlI4qiIgoSw9cE9hpvYhVRRVLIA8y605FbqWkNeQ==',
     'shasum': '269f616515f3e244c9812e7738aa71551cbd9822', 'tgz_max_bytes': 12 * 1024 * 1024,
     'licence': 'Apache-2.0', 'credit': 'Google LLC / The MediaPipe Authors',
     'about': 'MediaPipe Tasks Vision web runtime (ES module + WebAssembly) that runs the .task models in the browser',
     # member -> (destination under models/, bytes, sha256 base64 as published by jsDelivr's file listing)
     'members': {
         'package/vision_bundle.mjs': ('tasks-vision/vision_bundle.mjs', 155990,
                                       '+wwCwpb0TODMMkuha9ZG2Hc+tKeEfbQBnMTQQw23qbU='),
         'package/wasm/vision_wasm_internal.js': ('tasks-vision/wasm/vision_wasm_internal.js', 336624,
                                                  'KqX9P909ggjNdYffC7GftpJcTqFqdt0CWu3B9k1qYwk='),
         'package/wasm/vision_wasm_internal.wasm': ('tasks-vision/wasm/vision_wasm_internal.wasm', 14665716,
                                                    'IlvAbA9Rqc8x/qDp4LAkPp4B+17zMswaf6Zlbb39qr8='),
     }},
]


class FetchError(RuntimeError):
    pass


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _download(url, dest, max_bytes):
    """Stream url -> dest with a hard size cap. Returns (bytes, md5hex, sha1hex, sha256hex, sha512 raw)."""
    if not url.startswith('https://'):
        raise FetchError('refusing a non-HTTPS URL: ' + url)
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    h_md5, h_sha1, h_sha256, h_sha512 = hashlib.md5(), hashlib.sha1(), hashlib.sha256(), hashlib.sha512()
    n = 0
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, 'wb') as out:
        if r.status != 200:
            raise FetchError(f'{url}: HTTP {r.status}')
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            n += len(chunk)
            if n > max_bytes:
                raise FetchError(f'{url}: larger than the pinned size ({max_bytes} B); refusing')
            for h in (h_md5, h_sha1, h_sha256, h_sha512):
                h.update(chunk)
            out.write(chunk)
    return n, h_md5.hexdigest(), h_sha1.hexdigest(), h_sha256.hexdigest(), h_sha512.digest()


def _safe_member(m):
    name = m.name
    if not m.isreg():
        raise FetchError(f'archive member {name!r} is not a regular file (link/device/dir); refusing')
    if name.startswith(('/', '\\')) or (len(name) > 1 and name[1] == ':') or '..' in name.replace('\\', '/').split('/'):
        raise FetchError(f'archive member {name!r} has an unsafe path; refusing')
    return name


def _write_bytes(rel, data):
    dst = os.path.join(MODELS, *rel.split('/'))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + '.part'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, dst)
    return dst


def _fetch_file(item, work):
    tmp = os.path.join(work, 'download.bin')
    n, md5, _sha1, sha256, _ = _download(item['url'], tmp, item['bytes'])
    if n != item['bytes']:
        raise FetchError(f"{item['id']}: got {n} B, pinned {item['bytes']} B")
    if md5 != item['md5']:
        raise FetchError(f"{item['id']}: md5 {md5} does not match the pinned {item['md5']}")
    dst = os.path.join(MODELS, item['dest'])
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.move(tmp, dst + '.part')
    os.replace(dst + '.part', dst)
    return [{'file': item['dest'], 'bytes': n, 'sha256': sha256, 'md5': md5}]


def _fetch_npm(item, work):
    tgz = os.path.join(work, 'package.tgz')
    n, _md5, sha1, _sha256, sha512 = _download(item['url'], tgz, item['tgz_max_bytes'])
    algo, _, want = item['integrity'].partition('-')
    if algo != 'sha512' or base64.b64encode(sha512).decode() != want:
        raise FetchError(f"{item['id']}: npm integrity (sha512) does not match the pinned value")
    if sha1 != item['shasum']:
        raise FetchError(f"{item['id']}: sha1 {sha1} does not match the pinned {item['shasum']}")
    want_members = item['members']
    found = {}
    with tarfile.open(tgz, mode='r:gz') as tf:
        for m in tf.getmembers():
            if m.name not in want_members:
                continue
            _safe_member(m)
            rel, size, sha_b64 = want_members[m.name]
            if m.size != size:
                raise FetchError(f'{m.name}: {m.size} B in the archive, pinned {size} B')
            f = tf.extractfile(m)
            data = f.read(size + 1)
            if len(data) != size:
                raise FetchError(f'{m.name}: read {len(data)} B, pinned {size} B')
            digest = hashlib.sha256(data).digest()
            if base64.b64encode(digest).decode() != sha_b64:
                raise FetchError(f'{m.name}: sha256 does not match the pinned value')
            found[m.name] = (rel, data, digest.hex())
    missing = sorted(set(want_members) - set(found))
    if missing:
        raise FetchError(f"{item['id']}: archive is missing {missing}")
    out = []
    for name in sorted(found):
        rel, data, hexd = found[name]
        _write_bytes(rel, data)
        out.append({'file': rel, 'bytes': len(data), 'sha256': hexd, 'from_member': name})
    return out, {'tarball_bytes': n, 'tarball_sha1': sha1, 'tarball_integrity': item['integrity']}


def _notice_text(manifest):
    lines = ['Third-party files in this folder (stark_anim/models)', '=' * 54, '',
             'These files are unmodified copies of Google MediaPipe artefacts.',
             'Copyright 2023 The MediaPipe Authors / Google LLC.',
             'Licensed under the Apache License, Version 2.0 (see LICENSE-Apache-2.0.txt in this folder);',
             'you may not use these files except in compliance with the License. Distributed on an "AS IS" BASIS,',
             'WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND. Stark World Engine did not change them.', '',
             'Neither the npm package nor the .task model files ship a NOTICE file of their own; this file is the',
             'attribution notice that travels with them.', '']
    for e in manifest['files']:
        lines.append(f"- {e['file']}  ({e['bytes']:,} B, sha256 {e['sha256']})")
        lines.append(f"    source: {e['source_url']}" + (f"  [{e['from_member']}]" if e.get('from_member') else ''))
    lines.append('')
    return '\n'.join(lines)


def _credits_text(manifest):
    out = ['# Credits: webcam motion-capture models', '',
           'Used by the Stark World Engine webcam mocap (body pose + optional face blendshapes). Everything runs on the',
           "creator's own PC; video frames never leave the browser tab.", '',
           '| File | What | Licence | Credit | Bytes | SHA-256 | Source |', '|---|---|---|---|---|---|---|']
    for e in manifest['files']:
        out.append(f"| `{e['file']}` | {e['about']} | {e['licence']} | {e['credit']} | {e['bytes']:,} | "
                   f"`{e['sha256']}` | {e['source_url']} |")
    out += ['', 'Licence text: `LICENSE-Apache-2.0.txt` (same folder). Attribution notice: `NOTICE.txt`.', '']
    return '\n'.join(out)


def _write_docs(manifest):
    os.makedirs(MODELS, exist_ok=True)
    shutil.copyfile(LICENSE_SRC, os.path.join(MODELS, 'LICENSE-Apache-2.0.txt'))
    with open(os.path.join(MODELS, 'NOTICE.txt'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(_notice_text(manifest))
    with open(os.path.join(MODELS, 'CREDITS.md'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(_credits_text(manifest))
    with open(os.path.join(MODELS, 'manifest.json'), 'w', encoding='utf-8', newline='\n') as f:
        json.dump(manifest, f, indent=1)


def load_manifest():
    p = os.path.join(MODELS, 'manifest.json')
    if not os.path.isfile(p):
        return None
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def status(verify=True):
    """-> {'installed': bool, 'files': [...{file, ok, reason}], 'roles': {'body': bool, 'face': bool, 'runtime': bool}}"""
    man = load_manifest()
    res = {'installed': False, 'files': [], 'roles': {'body': False, 'face': False, 'runtime': False},
           'manifest': bool(man)}
    if not man:
        return res
    role_ok = {}
    for e in man.get('files', []):
        p = os.path.join(MODELS, *e['file'].split('/'))
        ok, why = True, ''
        if not os.path.isfile(p):
            ok, why = False, 'missing'
        elif os.path.getsize(p) != e['bytes']:
            ok, why = False, 'size changed'
        elif verify and _sha256_file(p) != e['sha256']:
            ok, why = False, 'sha256 changed'
        res['files'].append({'file': e['file'], 'role': e['role'], 'ok': ok, 'reason': why})
        role_ok.setdefault(e['role'], True)
        role_ok[e['role']] = role_ok[e['role']] and ok
    for r in res['roles']:
        res['roles'][r] = bool(role_ok.get(r))
    res['installed'] = res['roles']['runtime'] and res['roles']['body']
    return res


def fetch(approved=False, only=None, log=print):
    if not approved:
        raise FetchError('refusing to download without --approved (the owner must approve model downloads)')
    os.makedirs(MODELS, exist_ok=True)
    old = load_manifest() or {}
    keep = {e['file']: e for e in old.get('files', [])}
    files = []
    for item in APPROVED:
        if only and item['role'] not in only and item['id'] not in only:
            files += [e for e in keep.values() if e.get('id') == item['id']]
            continue
        common = {'id': item['id'], 'role': item['role'], 'source_url': item['url'], 'licence': item['licence'],
                  'licence_file': 'LICENSE-Apache-2.0.txt', 'credit': item['credit'], 'about': item['about']}
        # already installed and intact? keep it (no network)
        prev = [e for e in keep.values() if e.get('id') == item['id']]
        if prev and all(os.path.isfile(os.path.join(MODELS, *e['file'].split('/'))) and
                        _sha256_file(os.path.join(MODELS, *e['file'].split('/'))) == e['sha256'] for e in prev):
            log(f"  {item['id']}: already installed and verified")
            files += prev
            continue
        work = tempfile.mkdtemp(prefix='gs_anim_dl_')  # its own new, empty folder
        try:
            log(f"  {item['id']}: downloading {item['url']}")
            if item['kind'] == 'file':
                got, extra = _fetch_file(item, work), {}
            else:
                got, extra = _fetch_npm(item, work)
            stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
            for g in got:
                e = dict(common)
                e.update(g)
                e.update(extra)
                e['fetched_utc'] = stamp
                if item['kind'] == 'npm':
                    e['package'] = f"{item['package']}@{item['version']}"
                files.append(e)
                log(f"    ok {g['file']}  {g['bytes']:,} B  sha256 {g['sha256'][:16]}...")
        finally:
            shutil.rmtree(work, ignore_errors=True)
    manifest = {'about': 'Webcam mocap models for stark_anim (MediaPipe, Apache-2.0). Written by model_store.py.',
                'approval': APPROVAL, 'files': files}
    _write_docs(manifest)
    return manifest


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else 'status'
    if cmd == 'fetch':
        only = None
        if '--only' in argv:
            only = set(argv[argv.index('--only') + 1].split(','))
        try:
            man = fetch(approved='--approved' in argv, only=only)
        except FetchError as e:
            print('REFUSED:', e)
            return 2
        print(f"installed {len(man['files'])} files into {MODELS}")
        return 0
    if cmd == 'status':
        st = status(verify='--fast' not in argv)
        print(json.dumps(st, indent=1))
        return 0 if st['installed'] else 1
    print(__doc__)
    return 2


if __name__ == '__main__':
    sys.exit(main())
