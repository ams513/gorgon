"""Stark Covenant auto-updater: offline-safe, SIGNED, transactional, with automatic rollback (stdlib only).

Adapted from Stark Quill's (formerly Gorgon Quill) proven updater. Covenant additions: latest.json must carry a valid Ed25519
signature from the Covenant seller key (license_guard.verify_feed) or it is ignored, and an update may never
write vault, key-share, licence or audit files.

Flow
  1. check(): fetch latest.json (URL configurable; any network error or a missing/bad signature -> None, the
     app carries on offline, unchanged).
  2. stage(): download each changed file to  <file>.update.tmp  and verify its size + SHA-256 from latest.json.
     A mismatch deletes the temp file and nothing is changed.
  3. apply(): for every file keep the current one as  <file>.old , swap the new one in with os.replace(),
     then write the "pending health check" flag  .covenant_update_pending.json .
  4. Next start: the LAUNCHER runs  boot_check()  first (this file is separate from the app, so a broken
     app cannot stop it). First boot of the new version -> state 'pending'; the app must call
     confirm_healthy() within HEALTH_DEADLINE_S (3 s) of that boot. If the flag is still pending at the
     following start (crash, hang, boot failure) the .old files are restored automatically, the bad
     version is remembered and not offered again.

Channels (Covenant ships as a beta first)
  * covenant_update.json in the app folder holds {"checks": bool, "channel": "beta"|"stable", "url": ...}.
    Update checks are OFF until the clinic allows them (first-run setup, or an administrator on the Licence
    screen); an administrator can turn them off again at any time. No feed can change this file (protected).
  * The feed: a "url" in covenant_update.json or STARK_COVENANT_UPDATE_URL (older name GORGON_COVENANT_UPDATE_URL)
    is the only address used. Otherwise DEFAULT_FEED_URL (the Stark address, 1.1.0+) is tried first and
    LEGACY_FEED_URL (the pre-1.1.0 /gorgon/ address) only when the first cannot be reached or gives no correctly
    signed feed; the signature check is the same for both. The feed may list releases per
    channel: {"channels": {"beta": {release}, "stable": {release}}, "signature": ...}; a flat feed (version,
    files, app_files at the top) is one release of channel feed["channel"] (default "stable").
  * A beta install follows both channels and takes the newest; a stable release at the same version as the
    installed beta counts as newer, so a beta is PROMOTED to the full version automatically when it ships
    (then its channel becomes "stable"). A stable install follows stable only.
  * The signature check, staging, rollback and the protected files (vault, keys, licence, audit, this config)
    are exactly as above for every channel.

All decisions are plain functions on a directory, so tests run them against temp folders.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import urllib.parse
import urllib.request

HEALTH_DEADLINE_S = 3.0
FLAG_NAME = '.covenant_update_pending.json'
SKIP_NAME = '.covenant_update_skip.json'
LOG_NAME = 'update.log'
CONFIG_NAME = 'covenant_update.json'        # {"checks": bool, "channel": "beta"|"stable", "url": "..."}
CHANNEL_NAME = 'CHANNEL'                    # written by deploy_chart.ps1: the channel this build shipped on
CHANNELS = ('beta', 'stable')
# Planned public location of the signed feed (published by the seller; nothing here ever uploads anything).
DEFAULT_FEED_URL = 'https://ams513.github.io/stark/updates/covenant/latest.json'
LEGACY_FEED_URL = 'https://ams513.github.io/gorgon/updates/covenant/latest.json'   # up to 1.1.0; kept as fallback
FEED_URLS = (DEFAULT_FEED_URL, LEGACY_FEED_URL)
ENV_URL = 'STARK_COVENANT_UPDATE_URL'
LEGACY_ENV_URL = 'GORGON_COVENANT_UPDATE_URL'                                     # still read (older installs)
# Never replaced by an update, whatever the feed says (patient-data keys, licence state, audit trail).
PROTECTED_NAMES = {'vault.json', 'keyring.bin', 'share1.dpapi', 'audit.jsonl', 'audit.jsonl.head',
                   'gorgon_covenant.keyshare', 'license.json', 'activation.json', 'install.key',
                   'revoked_cache.json', CONFIG_NAME.lower()}
PROTECTED_DIRS = {'.keys', '.git', 'vault', 'records', 'data'}
TMP_SUFFIX = '.update.tmp'
OLD_SUFFIX = '.old'
BAD_SUFFIX = '.bad'


class UpdateError(Exception):
    pass


# ---------------------------------------------------------------------------------------------- helpers
def _log(app_dir, msg):
    try:
        with open(os.path.join(app_dir, LOG_NAME), 'a', encoding='utf-8') as f:
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
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def sha256_file(path, bufsize=1 << 20):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(bufsize), b''):
            h.update(chunk)
    return h.hexdigest()


def parse_version(v):
    out = []
    for part in str(v).lstrip('vV').split('.'):
        num = ''.join(ch for ch in part if ch.isdigit())
        out.append(int(num) if num else 0)
    while len(out) < 3:
        out.append(0)
    return tuple(out)


def version_key(v):
    """SemVer-style order: 1.0.0-beta.2 < 1.0.0-beta.10 < 1.0.0 < 1.0.1."""
    v = str(v or '0').strip().lstrip('vV')
    core, _, pre = v.partition('-')
    nums = []
    for part in core.split('.')[:3]:
        digits = ''.join(ch for ch in part if ch.isdigit())
        nums.append(int(digits) if digits else 0)
    while len(nums) < 3:
        nums.append(0)
    if not pre:
        return tuple(nums) + (1, ())
    ids = tuple((0, int(x), '') if x.isdigit() else (1, 0, x) for x in pre.split('.'))
    return tuple(nums) + (0, ids)


def is_newer(candidate, current):
    return version_key(candidate) > version_key(current)


def _safe_rel(app_dir, rel):
    """Resolve a latest.json path inside app_dir; refuse anything that escapes it."""
    if not rel or os.path.isabs(rel) or ':' in rel or rel.startswith(('\\\\', '//')):
        raise UpdateError(f'unsafe path in update feed: {rel!r}')
    root = os.path.realpath(app_dir)
    full = os.path.realpath(os.path.join(root, rel))
    if os.path.commonpath([root, full]) != root:
        raise UpdateError(f'unsafe path in update feed: {rel!r}')
    parts = [p.lower() for p in os.path.relpath(full, root).replace('\\', '/').split('/')]
    if parts[-1] in PROTECTED_NAMES or any(p in PROTECTED_DIRS for p in parts[:-1]) \
            or parts[-1].endswith(('.keyshare', '.seed')):
        raise UpdateError(f'update feed tried to replace a protected file: {rel!r}')
    return full


# ---------------------------------------------------------------------------------------------- feed
def _env_url():
    return os.environ.get(ENV_URL) or os.environ.get(LEGACY_ENV_URL) or ''


def configured_url(app_dir):
    """The address a person set (environment or covenant_update.json), or '' for the built-in ones."""
    if _env_url():
        return _env_url()
    cfg = _read_json(os.path.join(app_dir, CONFIG_NAME)) or {}
    return cfg.get('url') or ''


def read_settings(app_dir):
    """-> {"checks": bool, "channel": str, "url": str, "allowed_at": int|None}. Checks are off until allowed."""
    cfg = _read_json(os.path.join(app_dir, CONFIG_NAME)) or {}
    channel = cfg.get('channel') if cfg.get('channel') in CHANNELS else installed_channel(app_dir, cfg)
    return {'checks': cfg.get('checks') is True, 'channel': channel,
            'url': _env_url() or cfg.get('url') or DEFAULT_FEED_URL, 'allowed_at': cfg.get('allowed_at')}


def write_settings(app_dir, checks=None, channel=None):
    """Change the clinic's update settings (only the app's administrator screens call this)."""
    path = os.path.join(app_dir, CONFIG_NAME)
    cfg = _read_json(path) or {}
    if checks is not None:
        cfg['checks'] = bool(checks)
        cfg['allowed_at' if checks else 'turned_off_at'] = int(time.time())
    if channel is not None:
        if channel not in CHANNELS:
            raise UpdateError('unknown channel %r' % channel)
        cfg['channel'] = channel
    _write_json(path, cfg)
    return read_settings(app_dir)


def installed_channel(app_dir, cfg=None):
    """The channel this install follows: covenant_update.json, else the CHANNEL file from the build, else stable."""
    cfg = cfg if cfg is not None else (_read_json(os.path.join(app_dir, CONFIG_NAME)) or {})
    if cfg.get('channel') in CHANNELS:
        return cfg['channel']
    try:
        with open(os.path.join(app_dir, CHANNEL_NAME), encoding='ascii') as f:
            ch = f.read().strip().lower()
        if ch in CHANNELS:
            return ch
    except OSError:
        pass
    return 'stable'


def releases(latest):
    """-> {channel: release dict} from a channel feed or a flat (single-release) feed."""
    if not isinstance(latest, dict):
        return {}
    if isinstance(latest.get('channels'), dict):
        return {c: r for c, r in latest['channels'].items() if c in CHANNELS and isinstance(r, dict) and r.get('version')}
    if latest.get('version'):
        ch = latest.get('channel') if latest.get('channel') in CHANNELS else 'stable'
        return {ch: latest}
    return {}


def select_release(latest, channel, current_version):
    """The release this install should move to, or None. -> (release, its channel)."""
    rel = releases(latest)
    follow = ('beta', 'stable') if channel == 'beta' else ('stable',)
    best, best_ch = None, None
    for ch in follow:
        r = rel.get(ch)
        if not r:
            continue
        newer = is_newer(r['version'], current_version) or (
            channel == 'beta' and ch == 'stable' and version_key(r['version'])[:3] >= version_key(current_version)[:3])
        if not newer:
            continue
        if best is None or version_key(r['version']) > version_key(best['version']) or (
                version_key(r['version'])[:3] == version_key(best['version'])[:3] and ch == 'stable'):
            best, best_ch = r, ch
    return best, best_ch


def _open(src, timeout):
    if '://' in src and not src.lower().startswith('file://'):
        if not src.lower().startswith('https://') and not src.lower().startswith('http://127.0.0.1'):
            raise UpdateError('update feed must be https')
        req = urllib.request.Request(src, headers={'User-Agent': 'StarkCovenant-Updater/1'})
        return urllib.request.urlopen(req, timeout=timeout)
    if src.lower().startswith('file://'):
        src = urllib.request.url2pathname(urllib.parse.urlparse(src).path)
    return open(src, 'rb')


def _verify(latest, pub):
    try:
        import license_guard
    except Exception:
        return False
    return license_guard.verify_feed(latest, pub)


def fetch_latest(url, timeout=5.0, max_bytes=1 << 20, pub=None, require_signature=True):
    """-> parsed latest.json dict, or None when offline/unreachable/garbled/unsigned (never raises).
    pub: seller public key (bytes/hex) for tests; default is license_guard.PUBLIC_KEY_HEX."""
    if not url:
        return None
    try:
        with _open(url, timeout) as r:
            data = r.read(max_bytes + 1)
        if len(data) > max_bytes:
            return None
        obj = json.loads(data.decode('utf-8'))
        if not (isinstance(obj, dict) and ('version' in obj or isinstance(obj.get('channels'), dict))):
            return None
        if require_signature and not _verify(obj, pub):
            return None
        return obj
    except Exception:
        return None


def feed_urls(app_dir):
    """Addresses to try, in order: only the one a person set, else the Stark feed then the old /gorgon/ one."""
    own = configured_url(app_dir)
    return [own] if own else list(FEED_URLS)


def fetch_first(urls, timeout=5.0, pub=None):
    """-> the first correctly signed latest.json from urls (each checked exactly like fetch_latest), or None."""
    for u in ([urls] if isinstance(urls, str) else urls):
        latest = fetch_latest(u, timeout, pub=pub)
        if latest is not None:
            return latest
    return None


def pending_files(app_dir, latest):
    """Entries of latest['app_files'] whose local copy differs (size or SHA-256)."""
    out = []
    for e in latest.get('app_files', []):
        full = _safe_rel(app_dir, e['path'])
        if not os.path.isfile(full) or os.path.getsize(full) != e['size'] or sha256_file(full) != e['sha256']:
            out.append(e)
    return out


def update_available(app_dir, latest, current_version):
    if not latest or not is_newer(latest.get('version', '0'), current_version):
        return False
    skip = _read_json(os.path.join(app_dir, SKIP_NAME)) or {}
    return latest['version'] not in skip.get('bad_versions', [])


# ---------------------------------------------------------------------------------------------- stage
def download(src, dest_tmp, size, sha256, timeout=30.0):
    """Stream src to dest_tmp, verify size + SHA-256. Mismatch -> temp removed, UpdateError."""
    h = hashlib.sha256()
    n = 0
    try:
        with _open(src, timeout) as r, open(dest_tmp, 'wb') as f:
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                n += len(chunk)
                if n > size:
                    raise UpdateError(f'{os.path.basename(dest_tmp)}: larger than announced')
                h.update(chunk)
                f.write(chunk)
            f.flush()
            os.fsync(f.fileno())
        if n != size:
            raise UpdateError(f'{os.path.basename(dest_tmp)}: size {n} != {size}')
        if h.hexdigest() != sha256.lower():
            raise UpdateError(f'{os.path.basename(dest_tmp)}: SHA-256 mismatch')
    except Exception:
        try:
            os.remove(dest_tmp)
        except OSError:
            pass
        raise
    return dest_tmp


def stage(app_dir, latest, entries=None, base_url=None):
    """Download + verify every entry to <file>.update.tmp. -> list of (target, tmp). All-or-nothing."""
    entries = pending_files(app_dir, latest) if entries is None else entries
    base_url = base_url or latest.get('base_url', '')
    staged = []
    try:
        for e in entries:
            target = _safe_rel(app_dir, e['path'])
            src = e.get('url') or (base_url.rstrip('/') + '/' + e['path'].replace('\\', '/') if '://' in base_url
                                   else os.path.join(base_url, e['path']))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            staged.append((target, download(src, target + TMP_SUFFIX, int(e['size']), e['sha256'])))
    except Exception:
        for _, tmp in staged:
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise
    return staged


# ---------------------------------------------------------------------------------------------- apply / rollback
def apply(app_dir, staged, new_version, old_version):
    """Swap staged files in (keeping .old) and write the pending-health flag. Undoes itself on error."""
    done = []
    try:
        for target, tmp in staged:
            had = os.path.exists(target)
            if had:
                os.replace(target, target + OLD_SUFFIX)
            os.replace(tmp, target)
            done.append((target, had))
    except Exception:
        for target, had in reversed(done):
            if had:
                os.replace(target + OLD_SUFFIX, target)
            else:
                os.remove(target)
        raise
    _write_json(os.path.join(app_dir, FLAG_NAME), {
        'state': 'pending', 'version': new_version, 'previous': old_version, 'boots': 0,
        'files': [[os.path.relpath(t, app_dir), had] for t, had in done], 'applied_at': time.time()})
    _log(app_dir, f'applied {old_version} -> {new_version} ({len(done)} files); waiting for health check')
    return done


def rollback(app_dir, reason='health check missed'):
    """Restore every .old file listed in the flag; remember the bad version. -> list of restored paths."""
    flag = _read_json(os.path.join(app_dir, FLAG_NAME))
    if not flag:
        return []
    restored = []
    for rel, had in flag.get('files', []):
        target = os.path.join(app_dir, rel)
        if os.path.exists(target):
            os.replace(target, target + BAD_SUFFIX)
        if had and os.path.exists(target + OLD_SUFFIX):
            os.replace(target + OLD_SUFFIX, target)
            restored.append(target)
    skip = _read_json(os.path.join(app_dir, SKIP_NAME)) or {'bad_versions': []}
    if flag.get('version') not in skip['bad_versions']:
        skip['bad_versions'].append(flag.get('version'))
    _write_json(os.path.join(app_dir, SKIP_NAME), skip)
    os.remove(os.path.join(app_dir, FLAG_NAME))
    _log(app_dir, f'ROLLED BACK {flag.get("version")} -> {flag.get("previous")}: {reason}')
    return restored


def boot_check(app_dir, now=None):
    """Call at every start BEFORE the app runs. -> 'none' | 'pending' | 'rolled_back'."""
    path = os.path.join(app_dir, FLAG_NAME)
    flag = _read_json(path)
    if not flag:
        return 'none'
    if flag.get('boots', 0) >= 1:
        rollback(app_dir, 'previous start never confirmed healthy (crash or boot failure)')
        return 'rolled_back'
    flag['boots'] = 1
    flag['boot_started'] = time.time() if now is None else now
    _write_json(path, flag)
    return 'pending'


def confirm_healthy(app_dir, now=None, deadline=HEALTH_DEADLINE_S):
    """The app calls this once it has booted. True = update accepted (or nothing pending).
    Called later than `deadline` seconds after boot_check -> False, flag stays, next start rolls back."""
    path = os.path.join(app_dir, FLAG_NAME)
    flag = _read_json(path)
    if not flag:
        return True
    now = time.time() if now is None else now
    started = flag.get('boot_started')
    if started is None:
        return False
    if now - started > deadline:
        _log(app_dir, f'health confirm too late ({now - started:.2f}s > {deadline}s)')
        return False
    os.remove(path)
    _log(app_dir, f'version {flag.get("version")} confirmed healthy in {now - started:.2f}s')
    return True


def cleanup_old(app_dir):
    """Remove .old/.bad files once a version has been healthy (optional housekeeping)."""
    if os.path.exists(os.path.join(app_dir, FLAG_NAME)):
        return 0
    n = 0
    for root, _, files in os.walk(app_dir):
        for fn in files:
            if fn.endswith((OLD_SUFFIX, BAD_SUFFIX)):
                os.remove(os.path.join(root, fn))
                n += 1
    return n


def launch(app_dir, cmd, alive_s=2.5):
    """Launcher helper: boot_check, start the app, and confirm health for it if it is still running (or exited
    cleanly) after alive_s seconds (< HEALTH_DEADLINE_S). A crash inside that window leaves the flag pending,
    so the next start rolls back. The app may also call confirm_healthy() itself; doing both is harmless.
    -> the app's exit code."""
    import subprocess
    state = boot_check(app_dir)
    if state == 'rolled_back':
        print('Stark Covenant: the last update did not start correctly, so the previous version was restored.')
    proc = subprocess.Popen(cmd, cwd=app_dir)
    try:
        rc = proc.wait(timeout=alive_s)
    except subprocess.TimeoutExpired:
        rc = None
    if state == 'pending' and rc in (None, 0):
        confirm_healthy(app_dir)
    return proc.wait() if rc is None else rc


def check_and_update(app_dir, current_version, url=None, timeout=5.0, pub=None, latest=None, channel=None):
    """One call for the app's 'Check for updates': -> (status, detail). Never raises."""
    try:
        if latest is None:
            latest = fetch_first(url or feed_urls(app_dir), timeout, pub=pub)
        if latest is None:
            return 'offline', 'Could not reach the update server; working offline.'
        channel = channel or installed_channel(app_dir)
        release, rel_channel = select_release(latest, channel, current_version)
        if release is None:
            return 'current', f'You have the latest version ({current_version}).'
        skip = _read_json(os.path.join(app_dir, SKIP_NAME)) or {}
        if release['version'] in skip.get('bad_versions', []):
            return 'current', f'Version {release["version"]} was rolled back here before; it is not offered again.'
        if 'base_url' not in release and latest.get('base_url'):
            release = dict(release, base_url=latest['base_url'])
        staged = stage(app_dir, release)
        promoted = channel == 'beta' and rel_channel == 'stable'
        if not staged:
            if promoted:                       # the full version ships the same files as this beta
                write_settings(app_dir, channel='stable')
                return 'updated', f'This beta is now the full version {release["version"]}.'
            return 'current', 'Files already up to date.'
        apply(app_dir, staged, release['version'], current_version)
        if promoted:
            write_settings(app_dir, channel='stable')
            return 'updated', (f'The full version {release["version"]} replaces this beta. '
                               'Restart Stark Covenant to finish.')
        return 'updated', f'Updated to {release["version"]}. Restart Stark Covenant to finish.'
    except Exception as e:
        _log(app_dir, f'update failed: {e}')
        return 'failed', f'Update not installed ({e}). Nothing was changed.'


def _installed_version(app_dir):
    try:
        with open(os.path.join(app_dir, 'VERSION'), encoding='ascii') as f:
            return f.read().strip()
    except OSError:
        pass
    import sys
    for name in ('covenant_app', 'gorgon_covenant', '__main__'):
        v = getattr(sys.modules.get(name), 'VERSION', None)
        if isinstance(v, str):
            return v
    return '0'


def check_for_updates(app_dir=None, current_version=None, url=None, timeout=5.0, pub=None):
    """Entry point the app calls (no arguments) in a background thread at start. Does nothing unless the
    clinic allowed update checks (covenant_update.json "checks": true). A fetched, correctly signed
    latest.json also refreshes the licence revocation cache. -> (status, detail)."""
    app_dir = app_dir or os.path.dirname(os.path.abspath(__file__))
    settings = read_settings(app_dir)
    if not settings['checks']:
        return 'disabled', 'Update checks are off (an administrator can turn them on).'
    url = url or feed_urls(app_dir)
    if os.path.isdir(os.path.join(app_dir, '.git')):
        return 'disabled', 'Source checkout: updates are applied by deploy, not in place.'
    latest = fetch_first(url, timeout, pub=pub)
    if latest is None:
        return 'offline', 'Could not reach the update server (or its feed was not signed); working offline.'
    try:
        import license_guard
        license_guard.update_revocations(license_guard.default_state_dir(), latest)
    except Exception:
        pass
    return check_and_update(app_dir, current_version or _installed_version(app_dir), url, timeout, pub=pub,
                            latest=latest, channel=settings['channel'])


check = check_for_updates


if __name__ == '__main__':
    import argparse
    import sys
    ap = argparse.ArgumentParser(description='Stark Covenant updater')
    ap.add_argument('cmd', choices=['boot-check', 'confirm', 'check', 'rollback', 'launch'])
    ap.add_argument('--app-dir', default=os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument('--version', default='0')
    ap.add_argument('--url')
    argv = sys.argv[1:]
    rest = argv[argv.index('--') + 1:] if '--' in argv else []     # launch: everything after -- is the program
    a = ap.parse_args(argv[:argv.index('--')] if '--' in argv else argv)
    if a.cmd == 'boot-check':
        print(boot_check(a.app_dir))
    elif a.cmd == 'confirm':
        sys.exit(0 if confirm_healthy(a.app_dir) else 1)
    elif a.cmd == 'launch':
        if not rest:
            ap.error('launch needs: -- covenant_app.py [args]')
        sys.exit(launch(a.app_dir, [sys.executable] + rest))
    elif a.cmd == 'rollback':
        print(len(rollback(a.app_dir, 'manual')), 'file(s) restored')
    else:
        print(*check_and_update(a.app_dir, a.version, a.url), sep=': ')
