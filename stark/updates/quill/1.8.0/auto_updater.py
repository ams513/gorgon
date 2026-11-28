"""Stark Quill auto-updater: offline-safe, verified, transactional, with automatic rollback (stdlib only).

Flow
  1. check(): fetch latest.json (URL configurable; any network error -> None, the app carries on offline).
  2. stage(): download each changed file to  <file>.update.tmp  and verify its size + SHA-256 from latest.json.
     A mismatch deletes the temp file and nothing is changed.
  3. apply(): for every file keep the current one as  <file>.old , swap the new one in with os.replace(),
     then write the "pending health check" flag  .quill_update_pending.json .
  4. Next start: the LAUNCHER runs  boot_check()  first (this file is separate from the app, so a broken
     app cannot stop it). First boot of the new version -> state 'pending'; the app must call
     confirm_healthy() within HEALTH_DEADLINE_S (3 s) of that boot. If the flag is still pending at the
     following start (crash, hang, boot failure) the .old files are restored automatically, the bad
     version is remembered and not offered again.

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
FLAG_NAME = '.quill_update_pending.json'
SKIP_NAME = '.quill_update_skip.json'
LOG_NAME = 'update.log'
CONFIG_NAME = 'quill_update.json'           # {"url": "https://.../latest.json", "fallback": "https://..."}
ENV_URL = 'STARK_QUILL_UPDATE_URL'
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
    """Semantic-version ordering: 1.0.9 < 1.0.10 < 1.1.0-beta.1 < 1.1.0-beta.2 < 1.1.0-rc.1 < 1.1.0."""
    s = str(v).strip().lstrip('vV')
    core, _, pre = s.partition('-')
    nums = []
    for part in core.split('.'):
        num = ''.join(ch for ch in part if ch.isdigit())
        nums.append(int(num) if num else 0)
    while len(nums) < 3:
        nums.append(0)
    if not pre:
        return tuple(nums[:3]) + (1,)               # a release sorts after every pre-release of it
    ids = tuple((0, int(p), '') if p.isdigit() else (1, 0, p.lower()) for p in pre.split('.'))
    return tuple(nums[:3]) + (0,) + ids


def is_newer(candidate, current):
    return parse_version(candidate) > parse_version(current)


def _safe_rel(app_dir, rel):
    """Resolve a latest.json path inside app_dir; refuse anything that escapes it."""
    if not rel or os.path.isabs(rel) or ':' in rel or rel.startswith(('\\\\', '//')):
        raise UpdateError(f'unsafe path in update feed: {rel!r}')
    root = os.path.realpath(app_dir)
    full = os.path.realpath(os.path.join(root, rel))
    if os.path.commonpath([root, full]) != root:
        raise UpdateError(f'unsafe path in update feed: {rel!r}')
    return full


# ---------------------------------------------------------------------------------------------- feed
def configured_urls(app_dir):
    """[url, fallback]: the feed address and the one tried when it cannot be reached (quill_update.json), or only
    the STARK_QUILL_UPDATE_URL override."""
    if os.environ.get(ENV_URL):
        return [os.environ[ENV_URL]]
    cfg = _read_json(os.path.join(app_dir, CONFIG_NAME)) or {}
    return [u for u in (cfg.get('url'), cfg.get('fallback')) if isinstance(u, str) and u]


def configured_url(app_dir):
    return (configured_urls(app_dir) or [''])[0]


def _feed_url_ok(src):
    """https anywhere, or plain http only to this PC itself (tests and local mirrors). Parsed, not prefix-matched:
    http://127.0.0.1.attacker.example/ is not loopback."""
    try:
        u = urllib.parse.urlsplit(src)
        host = (u.hostname or '').lower()
    except ValueError:
        return False
    if u.scheme.lower() == 'https':
        return bool(host)
    return u.scheme.lower() == 'http' and host in ('127.0.0.1', 'localhost', '::1')


def _open(src, timeout):
    if '://' in src and not src.lower().startswith('file://'):
        if not _feed_url_ok(src):
            raise UpdateError('update feed must be https')
        req = urllib.request.Request(src, headers={'User-Agent': 'StarkQuill-Updater/1'})
        return urllib.request.urlopen(req, timeout=timeout)
    if src.lower().startswith('file://'):
        src = urllib.request.url2pathname(urllib.parse.urlparse(src).path)
    return open(src, 'rb')


def fetch_latest(url, timeout=5.0, max_bytes=1 << 20):
    """-> parsed latest.json dict, or None when offline/unreachable/garbled (never raises). url may be a list:
    each address is tried in turn until one answers (the Stark feed, then the pre-1.5.1 address)."""
    if isinstance(url, (list, tuple)):
        for u in url:
            got = fetch_latest(u, timeout, max_bytes)
            if got is not None:
                return got
        return None
    if not url:
        return None
    try:
        with _open(url, timeout) as r:
            data = r.read(max_bytes + 1)
        if len(data) > max_bytes:
            return None
        obj = json.loads(data.decode('utf-8'))
        return obj if isinstance(obj, dict) and ('version' in obj or 'channels' in obj) else None
    except Exception:
        return None


# ---------------------------------------------------------------------------------------------- signed feed
# latest.json carries an Ed25519 signature made with the seller's key (the same key pair as the licences; the app
# holds only the public half in license_guard.PUBLIC_KEY_HEX). Nothing from an unsigned or altered feed is used:
# no file, no version, no revocation list.
FEED_PREFIX = b'GQFEED1.'
CHANNEL_NAME = 'CHANNEL'                    # "beta" or "stable", written by the build next to the app


def canonical_feed(feed):
    body = {k: v for k, v in feed.items() if k != 'signature'}
    return json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def sign_feed(feed, seed):
    """Seller side (keygen_quill.py sign-feed / deploy). -> a copy of feed with 'signature'."""
    import license_guard as L
    out = {k: v for k, v in feed.items() if k != 'signature'}
    out['signature'] = L._b64e(L.sign(seed, FEED_PREFIX + canonical_feed(out)))
    return out


STORE_FEED_PREFIX = b'GQ1-FEED.'          # the Storefront's release engine (release_policy.stage_feed) signs this way


def _store_body(feed):
    body = {k: v for k, v in feed.items() if k != 'sig'}
    return STORE_FEED_PREFIX + json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def verify_feed(feed, pub=None):
    """True only for a feed signed with the seller key, in either format: Quill's own ('signature', GQFEED1.) or the
    Storefront release engine's ('sig', GQ1-FEED.). Unsigned, tampered or foreign-key feeds -> False."""
    import license_guard as L
    if not isinstance(feed, dict):
        return False
    sig = feed.get('signature')
    try:
        if isinstance(sig, str) and sig:
            return L.verify(L._pub_bytes(pub), FEED_PREFIX + canonical_feed(feed), L._b64d(sig))
        sig = feed.get('sig')
        if isinstance(sig, str) and sig:
            if feed.get('product', 'stark-quill') not in ('stark-quill', 'gorgon-quill'):   # old catalogue id too
                return False
            return L.verify(L._pub_bytes(pub), _store_body(feed), L._b64d(sig))
    except Exception:
        return False
    return False


def installed_channel(app_dir):
    try:
        with open(os.path.join(app_dir, CHANNEL_NAME), encoding='ascii') as f:
            ch = f.read().strip().lower()
        return ch if ch in ('beta', 'stable') else 'stable'
    except OSError:
        return 'stable'


def feed_channels(feed):
    """{'beta': entry, 'stable': entry}; a v1.0 flat feed counts as its stable channel."""
    chans = feed.get('channels')
    if isinstance(chans, dict) and chans:
        return {k: v for k, v in chans.items() if isinstance(v, dict) and v.get('version')}
    if feed.get('version'):
        return {'stable': {k: feed[k] for k in ('version', 'base_url', 'app_files', 'released') if k in feed}}
    return {}


def choose_entry(feed, channel, current_version):
    """Which build an install should move to. A beta install follows the beta channel and is promoted to stable
    as soon as the stable release is at least as new as the beta (the full version has shipped); a stable install
    only ever follows stable. -> (entry or None, channel after the update)."""
    chans = feed_channels(feed)
    stable, beta = chans.get('stable'), chans.get('beta')
    if channel == 'beta':
        cands = [(e, ch) for e, ch in ((stable, 'stable'), (beta, 'beta')) if e]
        if not cands:
            return None, channel
        best = max(cands, key=lambda c: (parse_version(c[0]['version']), c[1] == 'stable'))
        return best
    return (stable, 'stable') if stable else (None, channel)


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
def _stage_text(app_dir, name, text):
    """A small file (VERSION, CHANNEL) staged like a download, so rollback restores it with the rest."""
    target = os.path.join(app_dir, name)
    with open(target + TMP_SUFFIX, 'w', encoding='ascii', newline='') as f:
        f.write(text)
    return target, target + TMP_SUFFIX


def apply(app_dir, staged, new_version, old_version, channel=None):
    """Swap staged files in (keeping .old) and write the pending-health flag. Undoes itself on error.
    The VERSION file (and CHANNEL, when given) move with the files, so a rollback puts them back too."""
    done = []
    staged = list(staged)
    have = {os.path.normcase(os.path.realpath(t)) for t, _ in staged}
    if new_version and staged:
        for name, text in (('VERSION', str(new_version)), (CHANNEL_NAME, channel)):
            if text and os.path.normcase(os.path.realpath(os.path.join(app_dir, name))) not in have:
                staged.append(_stage_text(app_dir, name, text))   # a build that ships the file itself wins
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
        print('Stark Quill: the last update did not start correctly, so the previous version was restored.')
    proc = subprocess.Popen(cmd, cwd=app_dir)
    try:
        rc = proc.wait(timeout=alive_s)
    except subprocess.TimeoutExpired:
        rc = None
    if state == 'pending' and rc in (None, 0):
        confirm_healthy(app_dir)
    return proc.wait() if rc is None else rc


def check_and_update(app_dir, current_version, url=None, timeout=5.0, pub=None, channel=None):
    """One call for the app's 'Check for updates': -> (status, detail). Never raises.
    The feed must be signed with the seller's key (pub, default license_guard.PUBLIC_KEY_HEX). The install's
    channel (CHANNEL file: beta / stable) decides which build it follows; see choose_entry()."""
    try:
        latest = fetch_latest(url or configured_urls(app_dir), timeout)
        if latest is None:
            return 'offline', 'Could not reach the update server; working offline.'
        if not verify_feed(latest, pub):                    # always: there is no way to switch this off
            _log(app_dir, 'update feed refused: missing or bad signature')
            return 'unsigned', 'The update feed is not signed by Stark, so nothing was installed.'
        channel = channel or installed_channel(app_dir)
        entry, new_channel = choose_entry(latest, channel, current_version)
        if not entry or not update_available(app_dir, entry, current_version):
            return 'current', f'You have the latest version ({current_version}).'
        staged = stage(app_dir, entry)
        if not staged:
            return 'current', 'Files already up to date.'
        apply(app_dir, staged, entry['version'], current_version,
              channel=new_channel if new_channel != channel else None)
        moved = ' (the full release: this copy now follows the stable channel)' if new_channel != channel else ''
        return 'updated', f'Updated to {entry["version"]}{moved}. Restart Stark Quill to finish.'
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
    for name in ('writer_app', '__main__'):
        v = getattr(sys.modules.get(name), 'VERSION', None)
        if isinstance(v, str):
            return v
    return '0'


def check_for_updates(app_dir=None, current_version=None, url=None, timeout=5.0, pub=None):
    """Entry point writer_app calls in a background thread, only after the writer said yes to automatic update
    checks. Does nothing unless an update URL is configured (quill_update.json or STARK_QUILL_UPDATE_URL). A
    fetched, correctly signed latest.json also refreshes the licence revocation cache. -> (status, detail)."""
    app_dir = app_dir or os.path.dirname(os.path.abspath(__file__))
    url = url or configured_urls(app_dir)
    if not url:
        return 'disabled', 'No update URL configured.'
    if os.path.isdir(os.path.join(app_dir, '.git')) or os.path.isfile(os.path.join(app_dir, 'keygen_quill.py')):
        return 'disabled', 'Source checkout: updates are applied by deploy, not in place.'
    latest = fetch_latest(url, timeout)
    if latest is not None and verify_feed(latest, pub):
        try:
            import license_guard
            license_guard.update_revocations(license_guard.default_state_dir(), latest)
        except Exception:
            pass
    return check_and_update(app_dir, current_version or _installed_version(app_dir), url, timeout, pub=pub)


check = check_for_updates


if __name__ == '__main__':
    import argparse
    import sys
    ap = argparse.ArgumentParser(description='Stark Quill updater')
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
            ap.error('launch needs: -- writer_app.py [args]')
        sys.exit(launch(a.app_dir, [sys.executable] + rest))
    elif a.cmd == 'rollback':
        print(len(rollback(a.app_dir, 'manual')), 'file(s) restored')
    else:
        print(*check_and_update(a.app_dir, a.version, a.url), sep=': ')
