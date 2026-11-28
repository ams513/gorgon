"""Stark Quill licence guard: offline signed licence tokens (pure-Python Ed25519, standard library only).

What it does
  * A licence is a token  GQ1.<payload b64url>.<Ed25519 signature b64url>  signed by the seller's private key
    (kept in .keys/ on the seller's PC by keygen_quill.py, never shipped). The app only has the PUBLIC key,
    so it can check a token but cannot make one.
  * Payload: buyer id, plan ID (stark-quill/pro, ...; older keys' IDs are mapped), expiry date (PRO is a
    $10/month subscription), offline grace days, max devices (2), optional seller-bound device list.
  * Device binding: a local hardware fingerprint (salted SHA-256 of Windows MachineGuid, volume serial,
    CPU/arch, hostname). It is computed locally and never sent anywhere. Seats are recorded in the licence
    file that travels with the token; a third device is refused with a friendly message.
  * A local activation file (per install) is HMAC-signed with a random per-install key and remembers the
    last time the licence was seen, so winding the clock back does not extend a subscription.
  * Revocation: latest.json (update feed) carries a list of SHA-256 hashes of revoked tokens; the last
    downloaded copy is cached so the check also works offline.

Honest limits (said plainly, also in README.md)
  * This makes a licence HARD TO SHARE, not unstealable. Anything that runs fully offline on the buyer's
    PC can be patched by someone determined enough. The goal is to keep honest people honest.
  * An expired or invalid licence never locks a writer out of their own books: the app drops to FREE.
  * Pure-Python Ed25519 verification costs milliseconds, not microseconds (see benchmark()); the verified
    result is cached per process so every check after the first is far below 1 ms.
"""
from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import hmac
import json
import os
import platform
import secrets
import shutil
import socket
import sys
import time

APP_ID = 'StarkQuill'
LEGACY_APP_IDS = ('GorgonQuill',)    # keys issued up to 1.5.0 carry the old app id; they stay valid
TOKEN_PREFIX = 'GQ1'
PLANS = ('FREE', 'PRO')
# Plan IDs (the store catalogue's product/edition) and the plan each one turns on. New keys carry a stark-quill/...
# ID; keys issued before 1.5.1 carry 'FREE' / 'PRO' or the old catalogue ID, which map to the same plans.
PLAN_IDS = {'stark-quill/free': 'FREE', 'stark-quill/pro': 'PRO', 'stark-quill/early-access': 'PRO'}
LEGACY_PLAN_IDS = {'gorgon-quill/free': 'stark-quill/free', 'gorgon-quill/pro': 'stark-quill/pro',
                   'gorgon-quill/early-access': 'stark-quill/early-access',
                   'FREE': 'stark-quill/free', 'PRO': 'stark-quill/pro'}
DATA_DIR = 'StarkQuill'              # per-user folder in %LOCALAPPDATA%: settings, licence, the installed app
LEGACY_DATA_DIR = 'GorgonQuill'      # its name up to 1.5.0: copied to DATA_DIR once (move_dir), never deleted
DEFAULT_MAX_DEVICES = 2
DEFAULT_GRACE_DAYS = 7
CLOCK_SKEW_DAYS = 1          # tolerated backwards clock movement before we call it a rollback
# App-wide salts, fixed since 1.0: changing a byte would unbind every activated device and seat list.
_FP_SALT = b'GorgonQuill/device-fingerprint/v1'   # the raw identifiers never leave device_fingerprint()
_SEAT_SALT = b'GorgonQuill/seats/v1'

# Seller public key (Ed25519, 32 bytes hex). keygen_quill.py init writes it here; the private key stays in .keys/.
PUBLIC_KEY_HEX = '9152a9d803de08ee79ef23692281fd18a2760692311865d8026922cf8ea13d81'


class LicenseError(Exception):
    """A token that cannot be trusted (bad format, bad signature, wrong product)."""


# --------------------------------------------------------------------------------------------------------
# Ed25519 (RFC 8032), pure Python. Extended twisted-Edwards coordinates; fixed-base table for B.
# --------------------------------------------------------------------------------------------------------
_P = 2 ** 255 - 19
_Q = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, -1, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _recover_x(y, sign):
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, -1, _P) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_GY = 4 * pow(5, -1, _P) % _P
_GX = _recover_x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % _P)
_ZERO = (0, 1, 1, 0)


def _add(P, Q):
    a = (P[1] - P[0]) * (Q[1] - Q[0]) % _P
    b = (P[1] + P[0]) * (Q[1] + Q[0]) % _P
    c = 2 * P[3] * Q[3] * _D % _P
    d = 2 * P[2] * Q[2] % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _double(P):
    return _add(P, P)


def _mul(s, P):
    """Variable-base scalar multiplication, 4-bit fixed window."""
    table = [_ZERO, P]
    for _ in range(14):
        table.append(_add(table[-1], P))
    R = _ZERO
    for shift in range((s.bit_length() + 3) // 4 * 4 - 4, -4, -4):
        R = _double(_double(_double(_double(R))))
        nib = (s >> shift) & 15
        if nib:
            R = _add(R, table[nib])
    return R


def _build_base_table():
    t, P = [], _G
    for _ in range(256):
        t.append(P)
        P = _double(P)
    return t


_GTAB = None


def _mul_base(s):
    global _GTAB
    if _GTAB is None:
        _GTAB = _build_base_table()
    R = _ZERO
    i = 0
    while s:
        if s & 1:
            R = _add(R, _GTAB[i])
        s >>= 1
        i += 1
    return R


def _equal(P, Q):
    return (P[0] * Q[2] - Q[0] * P[2]) % _P == 0 and (P[1] * Q[2] - Q[1] * P[2]) % _P == 0


def _compress(P):
    zi = pow(P[2], -1, _P)
    x, y = P[0] * zi % _P, P[1] * zi % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, 'little')


def _decompress(b):
    if len(b) != 32:
        return None
    y = int.from_bytes(b, 'little')
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


def _h_modq(data):
    return int.from_bytes(hashlib.sha512(data).digest(), 'little') % _Q


def _expand(seed):
    if len(seed) != 32:
        raise ValueError('Ed25519 seed must be 32 bytes')
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], 'little')
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def public_key(seed: bytes) -> bytes:
    a, _ = _expand(seed)
    return _compress(_mul_base(a))


def sign(seed: bytes, msg: bytes) -> bytes:
    a, prefix = _expand(seed)
    A = _compress(_mul_base(a))
    r = _h_modq(prefix + msg)
    Rs = _compress(_mul_base(r))
    h = _h_modq(Rs + A + msg)
    s = (r + h * a) % _Q
    return Rs + int.to_bytes(s, 32, 'little')


def verify(pub: bytes, msg: bytes, sig: bytes) -> bool:
    if len(pub) != 32 or len(sig) != 64:
        return False
    A = _decompress(pub)
    R = _decompress(sig[:32])
    if A is None or R is None:
        return False
    s = int.from_bytes(sig[32:], 'little')
    if s >= _Q:
        return False
    h = _h_modq(sig[:32] + pub + msg)
    return _equal(_mul_base(s), _add(R, _mul(h, A)))


def generate_keypair():
    """-> (seed 32 bytes, public key 32 bytes). Seller side only (keygen_quill.py)."""
    seed = secrets.token_bytes(32)
    return seed, public_key(seed)


# --------------------------------------------------------------------------------------------------------
# Tokens
# --------------------------------------------------------------------------------------------------------
def _b64e(b):
    return base64.urlsafe_b64encode(b).rstrip(b'=').decode('ascii')


def _b64d(s):
    s = s.strip()
    return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))


def make_token(seed: bytes, payload: dict) -> str:
    """Sign a payload into a token. Needs the PRIVATE seed: only keygen_quill.py and tests call this."""
    body = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')
    msg = TOKEN_PREFIX.encode() + b'.' + body
    return f'{TOKEN_PREFIX}.{_b64e(body)}.{_b64e(sign(seed, msg))}'


def token_hash(token: str) -> str:
    """The id used in revocation lists (latest.json 'revoked'): SHA-256 of the token text."""
    return hashlib.sha256(token.strip().encode('utf-8')).hexdigest()


_VERIFY_CACHE = {}   # (pubhex, token) -> payload ; per process


def _pub_bytes(pub):
    if pub is None:
        pub = PUBLIC_KEY_HEX
    if isinstance(pub, str):
        pub = bytes.fromhex(pub)
    return pub


def plan_id(plan):
    """A key's 'plan' field -> its stark-quill/... plan ID (old IDs and FREE / PRO mapped), or None if unknown."""
    if not isinstance(plan, str):
        return None
    plan = LEGACY_PLAN_IDS.get(plan, plan)
    return plan if plan in PLAN_IDS else None


def parse_token(token: str, pub=None) -> dict:
    """Verify the signature and return the payload; raises LicenseError. Cached per process.
    The payload's 'plan' comes back as FREE / PRO and 'plan_id' as the stark-quill/... ID, whatever the key carries."""
    pubb = _pub_bytes(pub)
    if not isinstance(token, str):
        raise LicenseError('licence must be text')
    token = token.strip()
    key = (pubb, token)
    hit = _VERIFY_CACHE.get(key)
    if hit is not None:
        return dict(hit)
    parts = token.split('.')
    if len(parts) != 3 or parts[0] != TOKEN_PREFIX:
        raise LicenseError('this is not a Stark Quill licence key')
    try:
        body, sig = _b64d(parts[1]), _b64d(parts[2])
    except Exception:
        raise LicenseError('the licence key is damaged (copy it again in full)')
    if not verify(pubb, TOKEN_PREFIX.encode() + b'.' + body, sig):
        raise LicenseError('the licence key signature does not match (it was altered or mistyped)')
    try:
        payload = json.loads(body.decode('ascii'))
    except Exception:
        raise LicenseError('the licence key content is unreadable')
    pid = plan_id(payload.get('plan')) if isinstance(payload, dict) else None
    if pid is None or payload.get('app') not in (APP_ID,) + LEGACY_APP_IDS:
        raise LicenseError('the licence key is for a different product')
    payload.update(plan=PLAN_IDS[pid], plan_id=pid)
    _VERIFY_CACHE[key] = dict(payload)
    return payload


def clear_cache():
    _VERIFY_CACHE.clear()


# --------------------------------------------------------------------------------------------------------
# Device fingerprint (local only)
# --------------------------------------------------------------------------------------------------------
def _machine_guid():
    if sys.platform != 'win32':
        for p in ('/etc/machine-id', '/var/lib/dbus/machine-id'):
            try:
                with open(p, encoding='ascii') as f:
                    return f.read().strip()
            except OSError:
                pass
        return ''
    try:
        import winreg
        flags = winreg.KEY_READ | getattr(winreg, 'KEY_WOW64_64KEY', 0)
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Cryptography', 0, flags) as k:
            return str(winreg.QueryValueEx(k, 'MachineGuid')[0])
    except Exception:
        return ''


def _volume_serial():
    if sys.platform != 'win32':
        try:
            return str(os.stat('/').st_dev)
        except OSError:
            return ''
    try:
        import ctypes
        serial = ctypes.c_uint32(0)
        root = os.environ.get('SystemDrive', 'C:') + '\\'
        ok = ctypes.windll.kernel32.GetVolumeInformationW(ctypes.c_wchar_p(root), None, 0,
                                                          ctypes.byref(serial), None, None, None, 0)
        return format(serial.value, '08X') if ok else ''
    except Exception:
        return ''


def fingerprint_sources():
    """The stable local identifiers (for transparency; nothing here is sent anywhere)."""
    return {
        'machine_guid': _machine_guid(),
        'volume_serial': _volume_serial(),
        'cpu': platform.processor() or os.environ.get('PROCESSOR_IDENTIFIER', ''),
        'arch': platform.machine(),
        'hostname': socket.gethostname().lower(),
    }


def device_fingerprint(sources=None) -> str:
    """Salted SHA-256 of the local identifiers -> 32 hex chars. Same PC -> same value; never transmitted."""
    src = sources if sources is not None else fingerprint_sources()
    blob = '\x1f'.join(f'{k}={src.get(k, "")}' for k in sorted(src)).encode('utf-8')
    return hashlib.sha256(_FP_SALT + blob).hexdigest()[:32]


def device_code(fp=None) -> str:
    """Short code a buyer can send the seller to get a device-bound key (XXXX-XXXX-XXXX-XXXX)."""
    fp = fp or device_fingerprint()
    return '-'.join(fp[i:i + 4].upper() for i in range(0, 16, 4))


# --------------------------------------------------------------------------------------------------------
# Local state: per-install key, activation file, seats in the licence file, revocation cache
# --------------------------------------------------------------------------------------------------------
def _appdata():
    return os.environ.get('LOCALAPPDATA') or os.path.join(os.path.expanduser('~'), '.local', 'share')


def data_dir():
    """The per-user folder: DATA_DIR, or the pre-1.5.1 one while DATA_DIR is missing (not moved yet, or the move
    failed and nothing changed). Never creates or moves anything; writer_app calls move_dir() at start."""
    new, old = os.path.join(_appdata(), DATA_DIR), os.path.join(_appdata(), LEGACY_DATA_DIR)
    return old if not os.path.isdir(new) and os.path.isdir(old) else new


def _tree(root):
    """{relative path: SHA-256} of every file under root."""
    out = {}
    for d, _dirs, files in os.walk(root):
        for fn in files:
            p = os.path.join(d, fn)
            h = hashlib.sha256()
            with open(p, 'rb') as f:
                for chunk in iter(lambda: f.read(1 << 20), b''):
                    h.update(chunk)
            out[os.path.relpath(p, root)] = h.hexdigest()
    return out


MOVED_MARK = '.moved_from'


def move_dir(old, new):
    """Copy the folder old to new once: only when new is missing and old exists. The copy goes to new + '.moving',
    must match old in file count and every SHA-256, and is then renamed to new (MOVED_MARK inside names old).
    old is only read, never changed or deleted. -> 'moved', None (nothing to do) or 'failed: <reason>' (new is
    still missing, so data_dir() keeps using old)."""
    if os.path.isdir(new) or not os.path.isdir(old):
        return None
    tmp = new + '.moving'
    try:
        if os.path.isdir(tmp):
            shutil.rmtree(tmp)              # an earlier attempt that never finished: only ever our own copy
        shutil.copytree(old, tmp, ignore=shutil.ignore_patterns('__pycache__'))
        src = {k: v for k, v in _tree(old).items() if '__pycache__' not in k.split(os.sep)}
        dst = _tree(tmp)
        if len(src) != len(dst) or src != dst:
            raise OSError('the copy does not match (%d files, %d copied)' % (len(src), len(dst)))
        with open(os.path.join(tmp, MOVED_MARK), 'w', encoding='utf-8') as f:
            f.write(old)
        os.rename(tmp, new)
        return 'moved'
    except OSError as e:
        shutil.rmtree(tmp, ignore_errors=True)
        return 'failed: %s' % e


def default_state_dir():
    return os.path.join(data_dir(), 'licence')


def default_license_path():
    """Where the app keeps the licence file (writer_app uses the same: QUILL_LICENSE_FILE or license.json)."""
    return os.environ.get('QUILL_LICENSE_FILE') or os.path.join(default_state_dir(), 'license.json')


def _atomic_write_json(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, indent=1, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _read_json(path):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _install_key(state_dir):
    path = os.path.join(state_dir, 'install.key')
    try:
        with open(path, 'rb') as f:
            k = f.read()
        if len(k) == 32:
            return k
    except OSError:
        pass
    os.makedirs(state_dir, exist_ok=True)
    k = secrets.token_bytes(32)
    with open(path + '.tmp', 'wb') as f:
        f.write(k)
    os.replace(path + '.tmp', path)
    return k


def _mac(key, obj):
    body = json.dumps(obj, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hmac.new(key, body, hashlib.sha256).hexdigest()


def _seat_key(token):
    # Seats travel with the licence file between the buyer's devices, so they are keyed by the token itself.
    return hashlib.sha256(_SEAT_SALT + token.strip().encode('utf-8')).digest()


def read_seats(license_path, token):
    """-> list of device fingerprints recorded in the licence file (empty if missing or tampered)."""
    data = _read_json(license_path) or {}
    seats = data.get('seats') or []
    if data.get('token', '').strip() != token.strip():
        return []
    if not hmac.compare_digest(str(data.get('mac', '')), _mac(_seat_key(token), {'seats': seats})):
        return None          # tampered seat list
    return list(seats)


def write_license_file(license_path, token, seats, released=None):
    """released: devices where the buyer turned Pro off; check() leaves them on Free until the key is pasted
    again (activate). Not MAC-protected on purpose: editing it can only switch Pro off for yourself."""
    if released is None:
        released = (_read_json(license_path) or {}).get('released') or []
    data = {'token': token.strip(), 'seats': list(seats), 'mac': _mac(_seat_key(token), {'seats': list(seats)})}
    if released:
        data['released'] = sorted(set(released))
    _atomic_write_json(license_path, data)


def released_here(license_path, fingerprint=None):
    data = _read_json(license_path) or {}
    return (fingerprint or device_fingerprint()) in (data.get('released') or [])


def load_activation(state_dir):
    data = _read_json(os.path.join(state_dir, 'activation.json'))
    if not data:
        return None
    rec = {k: v for k, v in data.items() if k != 'mac'}
    if not hmac.compare_digest(str(data.get('mac', '')), _mac(_install_key(state_dir), rec)):
        return None
    return rec


def save_activation(state_dir, rec):
    rec = dict(rec)
    rec['mac'] = _mac(_install_key(state_dir), {k: v for k, v in rec.items() if k != 'mac'})
    _atomic_write_json(os.path.join(state_dir, 'activation.json'), rec)


def update_revocations(state_dir, latest):
    """Store latest.json's 'revoked' list (call after a successful update check). -> set of hashes."""
    revoked = sorted({str(h).lower() for h in (latest or {}).get('revoked', []) if h})
    _atomic_write_json(os.path.join(state_dir, 'revoked_cache.json'),
                       {'revoked': revoked, 'fetched': int(time.time())})
    return set(revoked)


def cached_revocations(state_dir):
    data = _read_json(os.path.join(state_dir, 'revoked_cache.json')) or {}
    return {str(h).lower() for h in data.get('revoked', [])}


# --------------------------------------------------------------------------------------------------------
# The check
# --------------------------------------------------------------------------------------------------------
class LicenseStatus(dict):
    """dict with attribute access: ok, plan, code, message, buyer, expires, days_left, devices."""
    __getattr__ = dict.get


def _status(ok, plan, code, message, **kw):
    return LicenseStatus(ok=ok, plan=plan, code=code, message=message, **kw)


def _today(now):
    return _dt.datetime.fromtimestamp(now, _dt.timezone.utc).date()


def _term_words(days):
    return '12 months' if days == 365 else ('%d months' % (days // 30) if days % 30 == 0 else '%d days' % days)


def evaluate(token, *, now=None, fingerprint=None, revoked=None, pub=None, seats=None, last_seen=None,
             term_start=None):
    """Pure decision (no file access). -> LicenseStatus. Never raises for a bad token: returns FREE.

    Two kinds of Pro key: dated keys carry 'exp' (a subscription period); TERM keys carry 'term_days' (for
    example 365 for 'Pro, 12 months' sold as one download) and run from term_start, the first time this install
    saw the key. Clock: a PC clock that moved backwards is not punished, and it cannot stretch Pro either: time
    is counted from the latest moment this install has seen (last_seen)."""
    now = time.time() if now is None else now
    if not token:
        return _status(True, 'FREE', 'free', 'Free plan. Upgrade to Pro any time.')
    try:
        payload = parse_token(token, pub)
    except LicenseError as e:
        return _status(False, 'FREE', 'invalid', f'Licence not accepted: {e}. Your books are safe; '
                                                 'the app runs on the Free plan.')
    base = dict(buyer=payload.get('buyer'), expires=payload.get('exp'), payload=payload)
    if revoked and token_hash(token) in revoked:
        return _status(False, 'FREE', 'revoked', 'This licence key has been withdrawn (refund or a key that '
                       'was shared publicly). Your books are safe. Contact support if this is a mistake.', **base)
    eff = max(float(now), float(last_seen or 0))
    if last_seen and now < last_seen - CLOCK_SKEW_DAYS * 86400:
        base['clock_behind'] = True
    plan = payload['plan']
    exp = payload.get('exp')
    term = payload.get('term_days')
    grace_days = int(payload.get('grace_days', 0 if term else DEFAULT_GRACE_DAYS))
    days_left = None
    if term is not None:
        try:
            term = int(term)
        except (TypeError, ValueError):
            term = 0
        if not 0 < term <= 3660:
            return _status(False, 'FREE', 'invalid', 'Licence not accepted: bad term.', **base)
        start = float(term_start) if term_start is not None else eff
        start_date = _today(start)
        exp = (start_date + _dt.timedelta(days=term)).isoformat()
        base.update(expires=exp, term_days=term, term_start=start_date.isoformat(), term_start_ts=int(start))
    if exp:
        try:
            exp_date = _dt.date.fromisoformat(exp)
        except ValueError:
            return _status(False, 'FREE', 'invalid', 'Licence not accepted: bad expiry date.', **base)
        days_left = (exp_date - _today(eff)).days
        if term is not None and days_left <= -grace_days:
            return _status(False, 'FREE', 'expired', f'Your {_term_words(term)} of Pro on this computer ended on '
                           f'{exp}. Stark Quill is on the Free plan now: every book stays yours, readable, '
                           'editable and exportable as text.', days_left=days_left, **base)
        if days_left < -grace_days:
            return _status(False, 'FREE', 'expired', f'Your Pro subscription ended on {exp}. Renew to get Pro '
                           'back; until then the Free plan is active and every book stays yours.',
                           days_left=days_left, **base)
    maxd = int(payload.get('max_devices', DEFAULT_MAX_DEVICES))
    bound = payload.get('devices')
    if fingerprint is not None:
        if bound and fingerprint not in bound:
            return _status(False, 'FREE', 'device', 'This licence key was issued for other devices. Send your '
                           f'device code ({device_code(fingerprint)}) to support to move it.', **base)
        if seats is not None and fingerprint not in seats and len(seats) >= maxd:
            return _status(False, 'FREE', 'device', f'This licence is already active on {len(seats)} devices '
                           f'(the limit is {maxd}). Deactivate it on one of them (in Stark Quill: Licence > '
                           'Turn Pro off on this PC), then try again.', devices=len(seats), **base)
    if days_left is not None and days_left < 0:
        return _status(True, plan, 'grace', f'Pro renewal is due (ended {exp}). Pro stays on for '
                       f'{grace_days + days_left} more day(s) while offline.', days_left=days_left, **base)
    if term is not None:
        return _status(True, plan, 'ok', f'Pro on this computer until {exp} ({_term_words(term)} from '
                       f'{base["term_start"]}, the day it was first opened here).', days_left=days_left, **base)
    return _status(True, plan, 'ok', f'{plan} licence active' + (f' until {exp}.' if exp else '.'),
                   days_left=days_left, **base)


def activate(token, license_path, *, state_dir=None, fingerprint=None, now=None, pub=None, revoked=None):
    """Check a token on this device and take a seat for it (up to max_devices). -> LicenseStatus."""
    state_dir = state_dir or default_state_dir()
    fp = fingerprint or device_fingerprint()
    now = time.time() if now is None else now
    if revoked is None:
        revoked = cached_revocations(state_dir)
    seats = read_seats(license_path, token) if token else []
    if seats is None:
        return _status(False, 'FREE', 'tampered', 'The licence file was edited by hand and cannot be trusted. '
                       'Paste your licence key again.')
    act = load_activation(state_dir) or {}
    th = token_hash(token or '')
    terms = dict(act.get('terms') or {})      # per key: first use on this install + latest time seen
    rec = dict(terms.get(th) or {})
    last_seen = act.get('last_seen') if act.get('token_hash') == th else None
    last_seen = max(int(last_seen or 0), int(rec.get('seen') or 0)) or None
    st = evaluate(token, now=now, fingerprint=fp, revoked=revoked, pub=pub, seats=seats, last_seen=last_seen,
                  term_start=rec.get('start'))
    if st.code in ('invalid', 'revoked', 'tampered'):
        return st
    # Remember the latest time seen (even for an expired key: winding the clock back must not revive it) and,
    # for a term key, the day the term started on this install.
    rec['seen'] = max(int(now), int(last_seen or 0))
    if st.get('term_start_ts') is not None and rec.get('start') is None:
        rec['start'] = int(st['term_start_ts'])
    terms[th] = rec
    out = {k: v for k, v in act.items() if k != 'mac'}
    out['terms'] = terms
    if not st.ok or st.plan == 'FREE':
        save_activation(state_dir, out)
        return st
    released = (_read_json(license_path) or {}).get('released') or []
    if fp not in seats or fp in released:
        if fp not in seats:
            seats.append(fp)
        write_license_file(license_path, token, seats, [r for r in released if r != fp])
    out.update({'token_hash': th, 'fingerprint': fp,
                'activated_at': act.get('activated_at', int(now)) if act.get('token_hash') == th else int(now),
                'last_seen': rec['seen']})
    save_activation(state_dir, out)
    st['devices'] = len(seats)
    return st


def deactivate(token, license_path, *, state_dir=None, fingerprint=None):
    """Give this device's seat back. -> remaining seat count."""
    fp = fingerprint or device_fingerprint()
    seats = read_seats(license_path, token) or []
    seats = [s for s in seats if s != fp]
    released = (_read_json(license_path) or {}).get('released') or []
    write_license_file(license_path, token, seats, list(released) + [fp])
    # activation.json stays: it remembers when a term started and the latest time seen, so turning Pro off and
    # on again never restarts a 12-month term.
    return len(seats)


def read_key_file(path):
    """A store download may ship its key as key.txt next to the app: one line starting with GQ1."""
    try:
        with open(path, encoding='utf-8-sig') as f:
            for line in f:
                line = line.strip()
                if line.startswith(TOKEN_PREFIX + '.'):
                    return line
    except OSError:
        pass
    return ''


def check(license_path, *, state_dir=None, fingerprint=None, now=None, pub=None, key_file=None):
    """App start-up check: read the licence file and evaluate it. -> LicenseStatus (FREE if no file).
    key_file: a pre-activated download's key.txt, used when no key has been entered on this install yet."""
    data = _read_json(license_path) or {}
    token = data.get('token', '')
    if not token and key_file:
        stored = read_key_file(key_file)
        if stored:
            st = activate(stored, license_path, state_dir=state_dir, fingerprint=fingerprint, now=now, pub=pub)
            st['from_key_file'] = True
            return st
    if not token:
        return evaluate('')
    if (fingerprint or device_fingerprint()) in (data.get('released') or []):
        return _status(True, 'FREE', 'released', 'Pro is turned off on this device. Paste your key again '
                       '(Licence) to turn it back on.')
    return activate(token, license_path, state_dir=state_dir, fingerprint=fingerprint, now=now, pub=pub)


def benchmark(n_cached=1000):
    """Honest timings on this machine: first (real) verification vs cached checks. -> dict of ms."""
    seed, pub = generate_keypair()
    tok = make_token(seed, {'app': APP_ID, 'plan': 'stark-quill/pro', 'buyer': 'bench', 'exp': '2099-01-01'})
    clear_cache()
    t = time.perf_counter()
    parse_token(tok, pub)
    first = (time.perf_counter() - t) * 1000
    t = time.perf_counter()
    for _ in range(n_cached):
        parse_token(tok, pub)
    cached = (time.perf_counter() - t) * 1000 / n_cached
    clear_cache()
    raw = []
    for _ in range(5):
        t = time.perf_counter()
        verify(pub, b'x' * 64, sign(seed, b'x' * 64))
        raw.append((time.perf_counter() - t) * 1000)
    return {'first_verify_ms': round(first, 3), 'cached_check_ms': round(cached, 5),
            'sign_plus_verify_ms_min': round(min(raw), 3)}


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='Stark Quill licence tools (buyer side)')
    ap.add_argument('cmd', choices=['device-code', 'device-id', 'check', 'bench', 'activate', 'deactivate'])
    ap.add_argument('token', nargs='?', help='activate: the licence key you received')
    ap.add_argument('--license', default=None)
    a = ap.parse_args()
    if a.cmd == 'device-code':
        print(device_code())
    elif a.cmd == 'device-id':        # full fingerprint, for a seller-bound key (still only a hash)
        print(device_fingerprint())
    elif a.cmd == 'bench':
        print(json.dumps(benchmark(), indent=1))
    elif a.cmd == 'activate':
        if not a.token:
            raise SystemExit('usage: license_guard.py activate GQ1.xxxxx.yyyyy  (most people use Quill itself: '
                             'click Free plan at the bottom left, paste the key, press Activate)')
        st = activate(a.token, a.license or default_license_path())
        print(st.plan, '-', st.message)
        sys.exit(0 if st.get('ok') and st.plan == 'PRO' else 1)   # ACTIVATE_PRO.bat reads this
    elif a.cmd == 'deactivate':
        data = _read_json(a.license or default_license_path()) or {}
        if data.get('token'):
            print(deactivate(data['token'], a.license or default_license_path()), 'device(s) still active')
    else:
        st = check(a.license or default_license_path())
        print(st.plan, '-', st.message)
