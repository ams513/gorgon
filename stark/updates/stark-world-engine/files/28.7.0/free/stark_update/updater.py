"""Stark World Engine in-app updater: opt-in, offline-safe, SIGNED feed, staged + verified download, transactional swap with
rollback. Modelled on Stark Quill's auto_updater.py and Stark Covenant's signed variant; standard library only.

The buyer decides (stark_update.json next to studio_app.py; created on first use):
    {"check_for_updates": false, "channel": "stable"}
Nothing touches the network unless the buyer turned "check for updates" on. Channels: stable (default) or preview (stored as "beta").
Feed: https://ams513.github.io/stark/updates/stark-world-engine/latest.json (beta: .../stark-world-engine/beta/latest.json),
then the pre-28.7 address (LEGACY_FEED_BASE) when the first gives no valid feed;
STARK_WORLD_ENGINE_UPDATE_URL or "url" in stark_update.json overrides both (mirrors, tests).

A feed is used only when ALL of these hold, otherwise it is ignored and nothing changes:
  * it carries a valid Ed25519 signature ("sig") from the Stark World Engine seller key - the PUBLIC_KEY_HEX in
    licensing.py that already verifies licence keys - over the canonical JSON of everything else, prefixed with
    GS1-FEED. (domain separation: a licence signature can never pass as a feed signature)
  * product is stark-world-engine, channel matches, the buyer's edition (tier.json: standard / pro / studio) is in it
  * its version is newer than the installed one, not a version this copy already rolled back, and the installed
    version is at least the feed's min_version
Flow:
  1. check: fetch latest.json (https, 5 s, 2 MB cap). Offline / garbled / unsigned -> "offline", nothing changes.
  2. stage: every changed file of the buyer's edition is downloaded to .stark_update/staging/<version>/ and its size and
     SHA-256 checked (all or nothing). Protected files are never written: key.txt, tier.json, config.json,
     .gs_watermark, PERSONAL_LICENSE.txt, stark_update.json, files_manifest.json (patched, not replaced) and anything
     under runtime/, My_Games/, .keys/, .claude/, .git/, .stark_update/.
  3. apply: a journal is written first (.stark_update/journal.json); each file is moved aside to <file>.old and the
     staged one moved in (os.replace); files_manifest.json gets the new hashes so the setup doctor stays happy; the
     journal becomes "pending health check". An error undoes every step at once.
  4. boot_check() runs in the launcher BEFORE the app: a swap that was cut off (power loss) is undone; a version that
     started once but never confirmed its health is rolled back (.old files return, the version is skipped later).
  5. confirm_healthy(): studio_app calls it once its local server is up -> the update is kept.
All decisions are plain functions on a folder, so tests run them against temp folders.
"""
from __future__ import annotations

import base64, hashlib, json, os, re, shutil, sys, time, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import ed25519  # noqa: E402

PRODUCT = 'stark-world-engine'
FEED_BASE = 'https://ams513.github.io/stark/updates/stark-world-engine'
PREFIX = b'GS1-FEED.'
ENV_URL = 'STARK_WORLD_ENGINE_UPDATE_URL'
SETTINGS = 'stark_update.json'
STATE = '.stark_update'
# Read-only compatibility with copies installed before 28.7 (they used the old names): their feed is polled after the
# Stark one, their settings and update state are still read, and a journal their updater wrote is still finished here.
LEGACY_PRODUCT = 'gorgon-studios'
LEGACY_FEED_BASE = 'https://ams513.github.io/gorgon/updates/gorgon-studios'
LEGACY_SETTINGS = 'gorgon_update.json'
LEGACY_STATE = '.gorgon_update'
HEALTH_DEADLINE_S = 180.0
MAX_FEED = 2 * 1024 * 1024
OLD, BAD = '.old', '.bad'
PROTECTED_NAMES = {'key.txt', 'tier.json', 'config.json', '.gs_watermark', 'personal_license.txt', SETTINGS,
                   LEGACY_SETTINGS, 'files_manifest.json', 'launcher.log', 'doctor.log'}
PROTECTED_DIRS = {'runtime', 'my_games', '.keys', '.claude', '.git', STATE, LEGACY_STATE}
CHANNELS = ('stable', 'beta')


class UpdateError(Exception):
    pass


# ---------------------------------------------------------------------------------------------- small helpers
def app_dir_default():
    return os.path.dirname(HERE)


def _state(app_dir, *parts):
    return os.path.join(app_dir, STATE, *parts)


def _found(app_dir, name):
    """A state file: the Stark one, else the one a pre-28.7 updater left (read-only fallback), else the Stark path."""
    new, old = _state(app_dir, name), os.path.join(app_dir, LEGACY_STATE, name)
    return old if not os.path.exists(new) and os.path.exists(old) else new


def _journal(app_dir):
    return _found(app_dir, 'journal.json')


def _log(app_dir, msg):
    try:
        os.makedirs(_state(app_dir), exist_ok=True)
        with open(_state(app_dir, 'update.log'), 'a', encoding='utf-8') as f:
            f.write(f'{time.strftime("%Y-%m-%d %H:%M:%S")} {msg}\n')
    except OSError:
        pass


def _read_json(path):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def parse_version(v):
    out = []
    for part in str(v).lstrip('vV').split('.')[:3]:
        num = ''.join(ch for ch in part if ch.isdigit())
        out.append(int(num) if num else 0)
    while len(out) < 3:
        out.append(0)
    return tuple(out)


def is_newer(candidate, current):
    return parse_version(candidate) > parse_version(current)


def b64d(s):
    return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))


def feed_body(feed):
    body = {k: v for k, v in feed.items() if k != 'sig'}
    return PREFIX + json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def verify_feed(feed, pub):
    try:
        return isinstance(feed, dict) and isinstance(feed.get('sig'), str) and \
            ed25519.verify(pub, feed_body(feed), b64d(feed['sig']))
    except Exception:  # noqa: BLE001
        return False


def public_key(app_dir):
    """The seller public key the shipped licensing.py trusts (read as text: nothing is imported or run)."""
    try:
        with open(os.path.join(app_dir, 'licensing.py'), encoding='utf-8') as f:
            m = re.search(r"PUBLIC_KEY_HEX\s*=\s*['\"]([0-9a-fA-F]{64})['\"]", f.read())
        return bytes.fromhex(m.group(1)) if m else None
    except OSError:
        return None


# ---------------------------------------------------------------------------------------------- buyer settings
def settings(app_dir):
    s = _read_json(os.path.join(app_dir, SETTINGS)) or _read_json(os.path.join(app_dir, LEGACY_SETTINGS)) or {}
    ch = s.get('channel') if s.get('channel') in CHANNELS else 'stable'
    return {'check_for_updates': s.get('check_for_updates') is True, 'channel': ch, 'url': s.get('url') or ''}


def set_settings(app_dir, **kw):
    s = settings(app_dir)
    if 'check_for_updates' in kw:
        s['check_for_updates'] = kw['check_for_updates'] is True
    if 'channel' in kw:
        if kw['channel'] not in CHANNELS:
            raise ValueError('channel must be stable or beta')
        s['channel'] = kw['channel']
    if 'url' in kw:
        s['url'] = kw['url'] or ''
    _write_json(os.path.join(app_dir, SETTINGS), s)
    return s


def feed_urls(app_dir, channel=None):
    """Feeds to poll in order: an override alone, else the Stark feed, then the pre-28.7 address as a fallback."""
    if os.environ.get(ENV_URL):
        return [os.environ[ENV_URL]]
    s = settings(app_dir)
    if s['url']:
        return [s['url']]
    tail = '/latest.json' if (channel or s['channel']) == 'stable' else '/beta/latest.json'
    return [FEED_BASE + tail, LEGACY_FEED_BASE + tail]


def feed_url(app_dir, channel=None):
    return feed_urls(app_dir, channel)[0]


def installed(app_dir):
    """-> (version, edition). The updater's own record wins; a fresh unzip uses tier.json from the build."""
    tier = _read_json(os.path.join(app_dir, 'tier.json')) or {}
    rec = _read_json(_found(app_dir, 'installed.json')) or {}
    return str(rec.get('version') or tier.get('version') or '0'), str(tier.get('tier') or 'standard')


# ---------------------------------------------------------------------------------------------- feed
def _open(src, timeout):
    low = src.lower()
    if '://' in src and not low.startswith('file://'):
        if not (low.startswith('https://') or low.startswith('http://127.0.0.1')):
            raise UpdateError('update feed must be https')
        req = urllib.request.Request(src, headers={'User-Agent': 'StarkWorldEngine-Updater/1'})
        return urllib.request.urlopen(req, timeout=timeout)
    if low.startswith('file://'):
        src = urllib.request.url2pathname(urllib.parse.urlparse(src).path)
    return open(src, 'rb')


def fetch_feed(url, pub, timeout=5.0):
    """-> a verified feed dict, or None (offline, too big, garbled, unsigned, wrong product). Never raises."""
    if not url or not pub:
        return None
    try:
        with _open(url, timeout) as r:
            data = r.read(MAX_FEED + 1)
        if len(data) > MAX_FEED:
            return None
        feed = json.loads(data.decode('utf-8'))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(feed, dict) or feed.get('product') not in (PRODUCT, LEGACY_PRODUCT) or not verify_feed(feed, pub):
        return None
    return feed


def skipped(app_dir):
    return {v for st in (STATE, LEGACY_STATE)
            for v in (_read_json(os.path.join(app_dir, st, 'skip.json')) or {}).get('bad_versions', [])}


def choose(app_dir, feed, channel=None):
    """-> (entries, base_url, reason). entries == None means: nothing to do (reason says why)."""
    version, edition = installed(app_dir)
    channel = channel or settings(app_dir)['channel']
    if feed.get('channel') not in (channel, None) and not (channel == 'beta' and feed.get('channel') == 'stable'):
        return None, '', f'feed is for the {feed.get("channel")} channel'
    ed = (feed.get('editions') or {}).get(edition)
    if not ed:
        return None, '', f'no {edition} edition in this update'
    new = str(feed.get('version') or '0')
    if not is_newer(new, version):
        return None, '', f'you have the latest version ({version})'
    if new in skipped(app_dir):
        return None, '', f'{new} was rolled back on this computer before; waiting for a newer version'
    if feed.get('min_version') and parse_version(version) < parse_version(feed['min_version']):
        return None, '', f'{new} needs at least {feed["min_version"]} first (download it from the store page)'
    return list(ed.get('app_files') or []), ed.get('base_url') or feed.get('base_url') or '', ''


def safe_target(app_dir, rel):
    """The file inside app_dir that a feed path names; anything outside it or protected raises UpdateError."""
    if not rel or os.path.isabs(rel) or ':' in rel or rel.startswith(('\\\\', '//')) or '\x00' in rel:
        raise UpdateError(f'unsafe path in update feed: {rel!r}')
    root = os.path.realpath(app_dir)
    full = os.path.realpath(os.path.join(root, rel))
    if os.path.commonpath([root, full]) != root or full == root:
        raise UpdateError(f'unsafe path in update feed: {rel!r}')
    inner = os.path.relpath(full, root).replace('\\', '/').split('/')
    parts = [p.lower() for p in inner]
    if parts[-1] in PROTECTED_NAMES or any(p in PROTECTED_DIRS for p in parts[:-1]) or parts[0] in PROTECTED_DIRS \
            or parts[-1].endswith(('.pem', '.seed', '.key', OLD, BAD)):
        raise UpdateError(f'update feed tried to replace a protected file: {rel!r}')
    return os.path.join(app_dir, *inner)       # under app_dir as given (8.3 short names stay consistent)


def pending_files(app_dir, entries):
    out = []
    for e in entries:
        full = safe_target(app_dir, e['path'])
        if not os.path.isfile(full) or os.path.getsize(full) != int(e['size']) or sha256_file(full) != e['sha256']:
            out.append(e)
    return out


# ---------------------------------------------------------------------------------------------- stage
def download(src, dest, size, sha256, timeout=30.0):
    h, n = hashlib.sha256(), 0
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    try:
        with _open(src, timeout) as r, open(dest, 'wb') as f:
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                n += len(chunk)
                if n > size:
                    raise UpdateError(f'{os.path.basename(dest)}: larger than announced')
                h.update(chunk)
                f.write(chunk)
            f.flush()
            os.fsync(f.fileno())
        if n != size:
            raise UpdateError(f'{os.path.basename(dest)}: size {n} != {size}')
        if h.hexdigest() != str(sha256).lower():
            raise UpdateError(f'{os.path.basename(dest)}: SHA-256 mismatch')
    except Exception:
        try:
            os.remove(dest)
        except OSError:
            pass
        raise
    return dest


def stage(app_dir, feed, entries, base_url):
    """Download + verify every entry into .stark_update/staging/<version>/ (all or nothing). -> [(target, staged, e)]"""
    root = _state(app_dir, 'staging', str(feed.get('version')))
    shutil.rmtree(root, ignore_errors=True)
    staged = []
    try:
        for e in entries:
            target = safe_target(app_dir, e['path'])
            src = e.get('url') or (base_url.rstrip('/') + '/' + urllib.parse.quote(e['path'].replace('\\', '/'))
                                   if '://' in base_url else os.path.join(base_url, e['path']))
            dest = os.path.join(root, *e['path'].replace('\\', '/').split('/'))
            staged.append((target, download(src, dest, int(e['size']), e['sha256']), e))
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise
    return staged


# ---------------------------------------------------------------------------------------------- apply / undo
def _manifest_patch(app_dir, staged):
    path = os.path.join(app_dir, 'files_manifest.json')
    doc = _read_json(path)
    if not isinstance(doc, dict) or not isinstance(doc.get('files'), dict):
        return False
    shutil.copy2(path, path + OLD)
    for target, _, e in staged:
        rel = os.path.relpath(target, app_dir).replace('\\', '/')
        doc['files'][rel] = {'sha256': e['sha256'], 'size': int(e['size'])}
    _write_json(path, doc)
    return True


def apply(app_dir, staged, new_version, old_version):
    """Swap the staged files in, journal first. Any error undoes everything. -> list of replaced relative paths."""
    journal = {'state': 'swapping', 'version': new_version, 'previous': old_version, 'files': [], 'boots': 0,
               'started': time.time(), 'manifest': False}
    jpath = _state(app_dir, 'journal.json')
    for target, _, _ in staged:               # leftovers of an earlier, confirmed update must never be "restored"
        for leftover in (target + OLD, target + BAD):
            if os.path.exists(leftover):
                os.remove(leftover)
    journal['files'] = [[os.path.relpath(t, app_dir).replace('\\', '/'), os.path.exists(t)] for t, _, _ in staged]
    _write_json(jpath, journal)
    done = []
    try:
        for target, src, _ in staged:
            if os.path.exists(target):
                os.replace(target, target + OLD)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            os.replace(src, target)
            done.append(target)
        journal['manifest'] = _manifest_patch(app_dir, staged)
        _write_json(_state(app_dir, 'installed.json'), {'version': new_version, 'previous': old_version,
                                                        'applied_at': time.time()})
        journal['state'] = 'pending'
        _write_json(jpath, journal)
    except Exception:
        undo(app_dir, 'apply failed', mark_bad=False)
        raise
    shutil.rmtree(_state(app_dir, 'staging'), ignore_errors=True)
    _log(app_dir, f'applied {old_version} -> {new_version} ({len(done)} file(s)); waiting for the health check')
    return [os.path.relpath(t, app_dir).replace('\\', '/') for t in done]


def undo(app_dir, reason, mark_bad=True):
    """Put every file of the journal back (.old returns; files that were new are removed). -> restored paths."""
    jpath = _journal(app_dir)
    j = _read_json(jpath)
    if not j:
        return []
    restored = []
    for rel, had in j.get('files', []):
        target = os.path.join(app_dir, *rel.split('/'))
        old = target + OLD
        if had:
            if os.path.exists(old):
                if os.path.exists(target):
                    os.replace(target, target + BAD)
                os.replace(old, target)
                restored.append(rel)
        elif os.path.exists(target):
            os.replace(target, target + BAD)
            restored.append(rel)
    mpath = os.path.join(app_dir, 'files_manifest.json')
    if j.get('manifest') and os.path.exists(mpath + OLD):
        os.replace(mpath + OLD, mpath)
    prev = j.get('previous')
    if prev:
        _write_json(_state(app_dir, 'installed.json'), {'version': prev, 'rolled_back_from': j.get('version'),
                                                        'at': time.time()})
    if mark_bad and j.get('version'):
        sk = _read_json(_state(app_dir, 'skip.json')) or {'bad_versions': []}
        if j['version'] not in sk['bad_versions']:
            sk['bad_versions'].append(j['version'])
        _write_json(_state(app_dir, 'skip.json'), sk)
    os.remove(jpath)
    shutil.rmtree(_state(app_dir, 'staging'), ignore_errors=True)
    _log(app_dir, f'ROLLED BACK {j.get("version")} -> {prev}: {reason} ({len(restored)} file(s))')
    return restored


def boot_check(app_dir=None, now=None):
    """Call at every start BEFORE the app. -> 'none' | 'pending' | 'rolled_back' | 'repaired'."""
    app_dir = app_dir or app_dir_default()
    jpath = _journal(app_dir)
    j = _read_json(jpath)
    if not j:
        return 'none'
    if j.get('state') == 'swapping':
        undo(app_dir, 'the update was interrupted while files were being replaced', mark_bad=False)
        return 'repaired'
    if j.get('boots', 0) >= 1:
        undo(app_dir, 'the new version started but never confirmed it was healthy (crash or boot failure)')
        return 'rolled_back'
    j['boots'] = 1
    j['boot_started'] = time.time() if now is None else now
    _write_json(jpath, j)
    return 'pending'


def confirm_healthy(app_dir=None, now=None, deadline=HEALTH_DEADLINE_S):
    """studio_app calls this once its server is up. True = the update is kept (or nothing was pending)."""
    app_dir = app_dir or app_dir_default()
    jpath = _journal(app_dir)
    j = _read_json(jpath)
    if not j:
        return True
    if j.get('state') != 'pending' or j.get('boot_started') is None:
        return False
    now = time.time() if now is None else now
    if now - j['boot_started'] > deadline:
        _log(app_dir, f'health confirmed too late ({now - j["boot_started"]:.1f} s > {deadline} s)')
        return False
    os.remove(jpath)
    _log(app_dir, f'version {j.get("version")} confirmed healthy')
    return True


def cleanup_old(app_dir=None):
    """Remove .old / .bad copies once no update is pending (housekeeping). Never touches My_Games or runtime."""
    app_dir = app_dir or app_dir_default()
    if os.path.exists(_journal(app_dir)):
        return 0
    n = 0
    for root, dirs, files in os.walk(app_dir):
        dirs[:] = [d for d in dirs if d.lower() not in PROTECTED_DIRS]
        for fn in files:
            if fn.endswith((OLD, BAD)):
                try:
                    os.remove(os.path.join(root, fn))
                    n += 1
                except OSError:
                    pass
    return n


# ---------------------------------------------------------------------------------------------- one call for the app
def check_for_updates(app_dir=None, url=None, pub=None, timeout=5.0, force=False):
    """The app's "Check for updates". -> (status, detail). Never raises. No network unless the buyer opted in."""
    app_dir = app_dir or app_dir_default()
    s = settings(app_dir)
    if not s['check_for_updates'] and not force:
        return 'disabled', 'Update checks are off. Turn on "Check for updates" in Settings to allow them.'
    if os.path.isdir(os.path.join(app_dir, '.git')):
        return 'disabled', 'This is a source checkout: updates come from the developer, not in place.'
    if os.path.exists(_journal(app_dir)):
        return 'pending', 'An update is waiting for a restart of Stark World Engine.'
    pub = pub or public_key(app_dir)
    if not pub:
        return 'failed', 'No seller key in licensing.py, so no update can be verified. Nothing was changed.'
    try:
        feed = None
        for src in ([url] if url else feed_urls(app_dir)):
            feed = fetch_feed(src, pub, timeout)
            if feed is not None:
                break
        _write_json(_state(app_dir, 'last_check.json'), {'when': time.time(), 'ok': feed is not None})
        if feed is None:
            return 'offline', 'Could not reach a correctly signed update feed; working offline. Nothing was changed.'
        entries, base, why = choose(app_dir, feed, s['channel'])
        if entries is None:
            return 'current', why
        version, _ = installed(app_dir)
        todo = pending_files(app_dir, entries)
        if not todo:
            _write_json(_state(app_dir, 'installed.json'), {'version': feed['version'], 'previous': version})
            return 'current', f'Files already match {feed["version"]}.'
        staged = stage(app_dir, feed, todo, base)
        apply(app_dir, staged, str(feed['version']), version)
        return 'updated', f'Updated to {feed["version"]}. Restart Stark World Engine to finish.' + (
            f' {feed["notes"]}' if feed.get('notes') else '')
    except Exception as e:  # noqa: BLE001
        _log(app_dir, f'update failed: {e}')
        return 'failed', f'Update not installed ({e}). Nothing was changed.'


def status(app_dir=None):
    """For the app's Settings panel."""
    app_dir = app_dir or app_dir_default()
    s = settings(app_dir)
    version, edition = installed(app_dir)
    last = _read_json(_state(app_dir, 'last_check.json')) or {}
    return {'enabled': s['check_for_updates'], 'channel': s['channel'], 'version': version, 'edition': edition,
            'feed': feed_url(app_dir) if s['check_for_updates'] else None, 'last_check': last.get('when'),
            'pending_restart': os.path.exists(_journal(app_dir)), 'skipped': sorted(skipped(app_dir))}


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description='Stark World Engine updater (opt-in, signed, offline-safe)')
    ap.add_argument('cmd', choices=['boot-check', 'confirm', 'check', 'status', 'enable', 'disable', 'channel', 'rollback', 'cleanup'])
    ap.add_argument('value', nargs='?')
    ap.add_argument('--app-dir', default=app_dir_default())
    a = ap.parse_args(argv)
    if a.cmd == 'boot-check':
        r = boot_check(a.app_dir)
        if r == 'rolled_back':
            print('Stark World Engine: the last update did not start correctly, so the previous version was restored.')
        elif r == 'repaired':
            print('Stark World Engine: an interrupted update was undone; your files are as before.')
        return 0
    if a.cmd == 'confirm':
        return 0 if confirm_healthy(a.app_dir) else 1
    if a.cmd == 'check':
        print(*check_for_updates(a.app_dir), sep=': ')
        return 0
    if a.cmd in ('enable', 'disable'):
        print(set_settings(a.app_dir, check_for_updates=a.cmd == 'enable'))
        return 0
    if a.cmd == 'channel':
        print(set_settings(a.app_dir, channel=a.value or 'stable'))
        return 0
    if a.cmd == 'rollback':
        print(len(undo(a.app_dir, 'manual')), 'file(s) restored')
        return 0
    if a.cmd == 'cleanup':
        print(cleanup_old(a.app_dir), 'old copies removed')
        return 0
    print(json.dumps(status(a.app_dir), indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
