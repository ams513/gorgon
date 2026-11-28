"""Stark Covenant security vault: patient-data encryption with OS crypto only (standard library + ctypes).

Cipher (never home-made)
  * Windows: CNG bcrypt.dll AES-256-GCM (BCryptOpenAlgorithmProvider + BCRYPT_CHAIN_MODE_GCM,
    BCryptGenerateSymmetricKey, BCryptEncrypt/BCryptDecrypt with BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO),
    96-bit nonces from BCryptGenRandom (system-preferred RNG).
  * Other systems: OpenSSL libcrypto EVP_aes_256_gcm through ctypes when a libcrypto can be found.
  * Neither available -> status() says "encryption unavailable" and every encrypt/decrypt refuses. There is no
    fallback cipher in this file, on purpose.

Keys
  * A random 256-bit VAULT KEY is never stored whole. It is split with Shamir secret sharing over GF(256)
    (default 2-of-3):
      share 1  this PC, wrapped with Windows DPAPI (current user) [+ optional passphrase, scrypt]
      share 2  GORGON_COVENANT.KEYSHARE on a removable USB drive (GetDriveTypeW == DRIVE_REMOVABLE)
               [+ optional passphrase, scrypt]
      share 3  a printable recovery code (base32, grouped, with a 40-bit checksum that catches typos)
    Any 2 shares open the vault; one share alone reveals nothing about the key (information-theoretic).
  * The vault key wraps a small KEYRING of 256-bit data keys (AES-GCM, keyring.bin). Blobs name the data key
    they used, so rotate_key() adds a fresh data key without breaking old records; rotate_key(reshare=True)
    also replaces the vault key itself and issues new shares (old USB stick / recovery code become useless).
    The wrapped keyring is also copied onto the USB stick so a dead PC can be recovered from USB + code.
  * scrypt (hashlib.scrypt, N=2^15 r=8 p=1) derives the key for any passphrase-protected share.

Blob format (encrypt -> bytes)
  b'GCV1' | key_id (4, big endian) | nonce (12) | ciphertext | tag (16)
  GCM additional data = b'GCV1' + key_id + vault_id + caller aad, so a blob cannot be moved between vaults,
  between records (when the caller passes the record id as aad), or have its header edited.

Audit
  audit.jsonl is hash-chained (each line carries the SHA-256 of the previous one); audit.head remembers the
  last hash so a cut-off tail is also noticed. It records vault events only (create, unlock, failed unlock,
  lock, rotation, share writes); never patient data, never key material.

Honest limits
  * Python cannot guarantee that every copy of a key in memory is wiped; lock() overwrites the bytearrays we
    own and destroys the CNG key handles, which is the best a pure-Python program can do.
  * Random 96-bit nonces are safe for about 2^32 encryptions per data key (NIST SP 800-38D); rotate_key()
    long before that.
  * The audit chain detects edits; it cannot stop someone with full disk access from deleting the whole
    log and its head file together.
  * Some USB "external hard disks" report DRIVE_FIXED, not DRIVE_REMOVABLE; use a USB stick.
"""
from __future__ import annotations

import base64
import ctypes
import ctypes.util
import datetime as _dt
import hashlib
import hmac
import itertools
import json
import os
import secrets
import struct
import sys
import threading
import time

APP_NAME = 'Stark Covenant'
MAGIC = b'GCV1'
NONCE_LEN = 12
TAG_LEN = 16
KEY_LEN = 32
KEYSHARE_NAME = 'GORGON_COVENANT.KEYSHARE'
DEFAULT_K, DEFAULT_N = 2, 3
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 15, 8, 1
STATUS_UNAVAILABLE = 'encryption unavailable'
DRIVE_REMOVABLE = 2
_SHARE_ENTROPY = b'GorgonCovenant/share/v1'


class VaultError(Exception):
    """Vault cannot do what was asked (locked, missing shares, wrong shares, bad file)."""


class AuthenticationError(VaultError):
    """GCM authentication failed: ciphertext, tag, header or aad was altered (or wrong key)."""


class EncryptionUnavailable(VaultError):
    """No OS cipher on this machine: the vault refuses to touch real data."""


class RecoveryCodeError(VaultError):
    """Recovery code mistyped (checksum mismatch) or malformed."""


# ======================================================================================================
# Backends: CNG (Windows) and OpenSSL libcrypto (elsewhere). Both expose the same tiny interface.
# ======================================================================================================
class _CngKey:
    def __init__(self, backend, key):
        self._b = backend
        self.handle = ctypes.c_void_p()
        kb = (ctypes.c_ubyte * len(key)).from_buffer_copy(bytes(key))
        st = backend.bc.BCryptGenerateSymmetricKey(backend.alg, ctypes.byref(self.handle), None, 0,
                                                   kb, len(key), 0)
        ctypes.memset(kb, 0, len(key))
        backend._check(st, 'BCryptGenerateSymmetricKey')
        self._lock = threading.Lock()

    @staticmethod
    def _info(nonce_buf, aad_buf, aad_len, tag_buf):
        info = _AuthInfo()
        info.cbSize = ctypes.sizeof(_AuthInfo)
        info.dwInfoVersion = 1                       # BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO_VERSION
        info.pbNonce = ctypes.cast(nonce_buf, ctypes.c_void_p)
        info.cbNonce = NONCE_LEN
        info.pbAuthData = ctypes.cast(aad_buf, ctypes.c_void_p) if aad_len else None
        info.cbAuthData = aad_len
        info.pbTag = ctypes.cast(tag_buf, ctypes.c_void_p)
        info.cbTag = TAG_LEN
        return info

    def encrypt(self, nonce, pt, aad):
        if len(nonce) != NONCE_LEN:
            raise VaultError('GCM nonce must be 96 bits')
        n = ctypes.create_string_buffer(bytes(nonce), NONCE_LEN)
        a = ctypes.create_string_buffer(bytes(aad), max(1, len(aad)))
        tag = ctypes.create_string_buffer(TAG_LEN)
        src = ctypes.create_string_buffer(bytes(pt), max(1, len(pt)))
        out = ctypes.create_string_buffer(max(1, len(pt)))
        got = ctypes.c_ulong(0)
        info = self._info(n, a, len(aad), tag)
        with self._lock:
            st = self._b.bc.BCryptEncrypt(self.handle, src, len(pt), ctypes.byref(info), None, 0,
                                          out, len(pt), ctypes.byref(got), 0)
        self._b._check(st, 'BCryptEncrypt')
        return out.raw[:got.value] + tag.raw

    def decrypt(self, nonce, ct_tag, aad):
        if len(ct_tag) < TAG_LEN:
            raise AuthenticationError('blob too short')
        ct, tagv = bytes(ct_tag[:-TAG_LEN]), bytes(ct_tag[-TAG_LEN:])
        n = ctypes.create_string_buffer(bytes(nonce), NONCE_LEN)
        a = ctypes.create_string_buffer(bytes(aad), max(1, len(aad)))
        tag = ctypes.create_string_buffer(tagv, TAG_LEN)
        src = ctypes.create_string_buffer(ct, max(1, len(ct)))
        out = ctypes.create_string_buffer(max(1, len(ct)))
        got = ctypes.c_ulong(0)
        info = self._info(n, a, len(aad), tag)
        with self._lock:
            st = self._b.bc.BCryptDecrypt(self.handle, src, len(ct), ctypes.byref(info), None, 0,
                                          out, len(ct), ctypes.byref(got), 0)
        if st & 0xFFFFFFFF == 0xC000A002:          # STATUS_AUTH_TAG_MISMATCH
            ctypes.memset(out, 0, max(1, len(ct)))
            raise AuthenticationError('authentication failed: data, tag or aad was altered')
        self._b._check(st, 'BCryptDecrypt')
        return out.raw[:got.value]

    def destroy(self):
        if self.handle:
            self._b.bc.BCryptDestroyKey(self.handle)
            self.handle = ctypes.c_void_p()


class _AuthInfo(ctypes.Structure):
    # BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO (bcrypt.h)
    _fields_ = [('cbSize', ctypes.c_ulong), ('dwInfoVersion', ctypes.c_ulong),
                ('pbNonce', ctypes.c_void_p), ('cbNonce', ctypes.c_ulong),
                ('pbAuthData', ctypes.c_void_p), ('cbAuthData', ctypes.c_ulong),
                ('pbTag', ctypes.c_void_p), ('cbTag', ctypes.c_ulong),
                ('pbMacContext', ctypes.c_void_p), ('cbMacContext', ctypes.c_ulong),
                ('cbAAD', ctypes.c_ulong), ('cbData', ctypes.c_ulonglong), ('dwFlags', ctypes.c_ulong)]


class CngBackend:
    """Windows CNG AES-256-GCM."""
    name = 'cng'

    def __init__(self):
        if sys.platform != 'win32':
            raise OSError('CNG is Windows only')
        bc = ctypes.WinDLL('bcrypt')
        vp, ul = ctypes.c_void_p, ctypes.c_ulong
        bc.BCryptOpenAlgorithmProvider.argtypes = [ctypes.POINTER(vp), ctypes.c_wchar_p, ctypes.c_wchar_p, ul]
        bc.BCryptSetProperty.argtypes = [vp, ctypes.c_wchar_p, vp, ul, ul]
        bc.BCryptGenerateSymmetricKey.argtypes = [vp, ctypes.POINTER(vp), vp, ul, vp, ul, ul]
        for f in (bc.BCryptEncrypt, bc.BCryptDecrypt):
            f.argtypes = [vp, vp, ul, vp, vp, ul, vp, ul, ctypes.POINTER(ul), ul]
        bc.BCryptDestroyKey.argtypes = [vp]
        bc.BCryptGenRandom.argtypes = [vp, vp, ul, ul]
        for f in (bc.BCryptOpenAlgorithmProvider, bc.BCryptSetProperty, bc.BCryptGenerateSymmetricKey,
                  bc.BCryptEncrypt, bc.BCryptDecrypt, bc.BCryptDestroyKey, bc.BCryptGenRandom):
            f.restype = ctypes.c_long
        self.bc = bc
        self.alg = vp()
        self._check(bc.BCryptOpenAlgorithmProvider(ctypes.byref(self.alg), 'AES', None, 0),
                    'BCryptOpenAlgorithmProvider')
        mode = ctypes.create_unicode_buffer('ChainingModeGCM')       # BCRYPT_CHAIN_MODE_GCM
        self._check(bc.BCryptSetProperty(self.alg, 'ChainingMode', ctypes.cast(mode, vp),
                                         ctypes.sizeof(mode), 0), 'BCryptSetProperty(ChainingModeGCM)')

    @staticmethod
    def _check(status, what):
        if status != 0:
            raise VaultError(f'{what} failed (NTSTATUS 0x{status & 0xFFFFFFFF:08X})')

    def random(self, n):
        buf = ctypes.create_string_buffer(max(1, n))
        self._check(self.bc.BCryptGenRandom(None, buf, n, 0x2), 'BCryptGenRandom')   # SYSTEM_PREFERRED_RNG
        return buf.raw[:n]

    def new_key(self, key):
        if len(key) != KEY_LEN:
            raise VaultError('AES-256 needs a 32-byte key')
        return _CngKey(self, key)


class _SslKey:
    def __init__(self, backend, key):
        self._b = backend
        self.key = bytearray(key)

    def _run(self, enc, nonce, data, aad, tag=None):
        L, b = self._b.lib, self._b
        ctx = L.EVP_CIPHER_CTX_new()
        if not ctx:
            raise VaultError('EVP_CIPHER_CTX_new failed')
        try:
            init = L.EVP_EncryptInit_ex if enc else L.EVP_DecryptInit_ex
            upd = L.EVP_EncryptUpdate if enc else L.EVP_DecryptUpdate
            fin = L.EVP_EncryptFinal_ex if enc else L.EVP_DecryptFinal_ex
            if len(nonce) != NONCE_LEN:
                raise VaultError('GCM nonce must be 96 bits')
            outl = ctypes.c_int(0)
            b._ok(init(ctx, L.EVP_aes_256_gcm(), None, None, None), 'init')
            b._ok(L.EVP_CIPHER_CTX_ctrl(ctx, 0x9, NONCE_LEN, None), 'set ivlen')        # EVP_CTRL_GCM_SET_IVLEN
            kb = ctypes.create_string_buffer(bytes(self.key), KEY_LEN)
            nb = ctypes.create_string_buffer(bytes(nonce), NONCE_LEN)
            b._ok(init(ctx, None, None, kb, nb), 'key')
            ctypes.memset(kb, 0, KEY_LEN)
            if aad:
                ab = ctypes.create_string_buffer(bytes(aad), len(aad))
                b._ok(upd(ctx, None, ctypes.byref(outl), ab, len(aad)), 'aad')
            out = ctypes.create_string_buffer(len(data) + 32)
            src = ctypes.create_string_buffer(bytes(data), max(1, len(data)))
            b._ok(upd(ctx, out, ctypes.byref(outl), src, len(data)), 'update')
            n = outl.value
            if not enc:
                tb = ctypes.create_string_buffer(bytes(tag), TAG_LEN)
                b._ok(L.EVP_CIPHER_CTX_ctrl(ctx, 0x11, TAG_LEN, tb), 'set tag')            # EVP_CTRL_GCM_SET_TAG
            tail = ctypes.c_int(0)
            if fin(ctx, ctypes.c_void_p(ctypes.addressof(out) + n), ctypes.byref(tail)) <= 0:
                if enc:
                    raise VaultError('EVP final failed')
                ctypes.memset(out, 0, len(data) + 32)
                raise AuthenticationError('authentication failed: data, tag or aad was altered')
            n += tail.value
            if enc:
                tb = ctypes.create_string_buffer(TAG_LEN)
                b._ok(L.EVP_CIPHER_CTX_ctrl(ctx, 0x10, TAG_LEN, tb), 'get tag')            # EVP_CTRL_GCM_GET_TAG
                return out.raw[:n] + tb.raw
            return out.raw[:n]
        finally:
            L.EVP_CIPHER_CTX_free(ctx)

    def encrypt(self, nonce, pt, aad):
        return self._run(True, nonce, pt, aad)

    def decrypt(self, nonce, ct_tag, aad):
        if len(ct_tag) < TAG_LEN:
            raise AuthenticationError('blob too short')
        return self._run(False, nonce, ct_tag[:-TAG_LEN], aad, ct_tag[-TAG_LEN:])

    def destroy(self):
        for i in range(len(self.key)):
            self.key[i] = 0


def _libcrypto_candidates():
    if sys.platform == 'win32':
        d = os.path.join(sys.base_prefix, 'DLLs')
        try:
            return [os.path.join(d, f) for f in sorted(os.listdir(d), reverse=True)
                    if f.lower().startswith('libcrypto') and f.lower().endswith('.dll')]
        except OSError:
            return []
    out = []
    found = ctypes.util.find_library('crypto')
    if found and not (sys.platform == 'darwin' and found.startswith('/usr/lib/')):
        out.append(found)            # macOS /usr/lib/libcrypto.dylib is a stub that aborts the process
    out += ['libcrypto.so.3', 'libcrypto.so.1.1', 'libcrypto.so',
            '/opt/homebrew/opt/openssl@3/lib/libcrypto.3.dylib', '/usr/local/opt/openssl@3/lib/libcrypto.3.dylib',
            '/opt/homebrew/opt/openssl@1.1/lib/libcrypto.1.1.dylib']
    return out


class OpenSslBackend:
    """OpenSSL libcrypto EVP AES-256-GCM via ctypes (non-Windows; on Windows only for cross-checks)."""
    name = 'openssl'

    def __init__(self, path=None):
        lib, err = None, None
        for cand in ([path] if path else _libcrypto_candidates()):
            try:
                lib = ctypes.CDLL(cand)
                lib.EVP_aes_256_gcm
                break
            except (OSError, AttributeError) as e:
                lib, err = None, e
        if lib is None:
            raise OSError(f'libcrypto not found ({err})')
        vp, ci = ctypes.c_void_p, ctypes.c_int
        lib.EVP_CIPHER_CTX_new.restype = vp
        lib.EVP_CIPHER_CTX_free.argtypes = [vp]
        lib.EVP_aes_256_gcm.restype = vp
        for f in (lib.EVP_EncryptInit_ex, lib.EVP_DecryptInit_ex):
            f.argtypes, f.restype = [vp, vp, vp, vp, vp], ci
        for f in (lib.EVP_EncryptUpdate, lib.EVP_DecryptUpdate):
            f.argtypes, f.restype = [vp, vp, ctypes.POINTER(ci), vp, ci], ci
        for f in (lib.EVP_EncryptFinal_ex, lib.EVP_DecryptFinal_ex):
            f.argtypes, f.restype = [vp, vp, ctypes.POINTER(ci)], ci
        lib.EVP_CIPHER_CTX_ctrl.argtypes, lib.EVP_CIPHER_CTX_ctrl.restype = [vp, ci, ci, vp], ci
        self.lib = lib
        k = self.new_key(b'\0' * KEY_LEN)     # self-test: a real libcrypto, not a stub
        if k.encrypt(b'\0' * NONCE_LEN, b'', b'') != bytes.fromhex('530f8afbc74536b9a963b4f1c4cb738b'):
            raise OSError('libcrypto AES-GCM self-test failed')

    @staticmethod
    def _ok(r, what):
        if r != 1:
            raise VaultError(f'OpenSSL {what} failed')

    def random(self, n):
        return os.urandom(n)          # OS CSPRNG (getrandom / /dev/urandom)

    def new_key(self, key):
        if len(key) != KEY_LEN:
            raise VaultError('AES-256 needs a 32-byte key')
        return _SslKey(self, key)


_BACKEND_CACHE = {}


def get_backend(prefer=None):
    """-> CngBackend | OpenSslBackend | None (None = encryption unavailable). prefer: 'cng' | 'openssl'."""
    prefer = prefer or os.environ.get('GORGON_COVENANT_CRYPTO') or ('cng' if sys.platform == 'win32' else 'openssl')
    if prefer in _BACKEND_CACHE:
        return _BACKEND_CACHE[prefer]
    order = [prefer] + [b for b in ('cng', 'openssl') if b != prefer]
    if sys.platform == 'win32' and prefer == 'cng':
        order = ['cng']               # Windows always uses CNG unless OpenSSL was asked for explicitly
    be = None
    for name in order:
        try:
            be = CngBackend() if name == 'cng' else OpenSslBackend()
            break
        except Exception:
            be = None
    _BACKEND_CACHE[prefer] = be
    return be


def aes_gcm_encrypt(key, nonce, plaintext, aad=b'', backend=None):
    """Raw AES-256-GCM through the OS backend (used by the NIST vector tests). -> ciphertext + 16-byte tag."""
    be = backend or get_backend()
    if be is None:
        raise EncryptionUnavailable(STATUS_UNAVAILABLE)
    k = be.new_key(key)
    try:
        return k.encrypt(nonce, plaintext, aad)
    finally:
        k.destroy()


def aes_gcm_decrypt(key, nonce, ct_tag, aad=b'', backend=None):
    be = backend or get_backend()
    if be is None:
        raise EncryptionUnavailable(STATUS_UNAVAILABLE)
    k = be.new_key(key)
    try:
        return k.decrypt(nonce, ct_tag, aad)
    finally:
        k.destroy()


# ======================================================================================================
# Shamir secret sharing over GF(256) (AES polynomial x^8+x^4+x^3+x+1, generator 3)
# ======================================================================================================
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x ^= (_x << 1) ^ (0x11B if _x & 0x80 else 0)     # multiply by 3
    _x &= 0xFF
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]
del _x, _i


def _gmul(a, b):
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _gdiv(a, b):
    if b == 0:
        raise ZeroDivisionError('GF(256) division by zero')
    if a == 0:
        return 0
    return _EXP[(_LOG[a] - _LOG[b]) % 255]


def shamir_split(secret, k, n, rand=None):
    """Split bytes into n shares, any k of which rebuild it. -> list of (x, bytes) with x = 1..n."""
    if not 1 <= k <= n <= 255:
        raise ValueError('need 1 <= k <= n <= 255')
    rand = rand or secrets.token_bytes
    secret = bytes(secret)
    coeffs = [rand(len(secret)) for _ in range(k - 1)]       # one random polynomial per secret byte
    shares = []
    for x in range(1, n + 1):
        out = bytearray(len(secret))
        for i, s in enumerate(secret):
            y = 0
            for c in reversed(coeffs):                       # Horner: ((c_{k-1} x + ...) x + c_1) x + s
                y = _gmul(y, x) ^ c[i]
            out[i] = _gmul(y, x) ^ s
        shares.append((x, bytes(out)))
    return shares


def shamir_interpolate(shares, at=0):
    """Lagrange-interpolate the share polynomials at x=`at` (0 = the secret; 1..n = rebuild a lost share)."""
    shares = [(int(x), bytes(y)) for x, y in shares]
    xs = [x for x, _ in shares]
    if not shares:
        raise ValueError('no shares')
    if len(set(xs)) != len(xs) or any(not 1 <= x <= 255 for x in xs):
        raise ValueError('share indexes must be distinct and 1..255')
    if len({len(y) for _, y in shares}) != 1:
        raise ValueError('shares have different lengths')
    weights = []
    for j, xj in enumerate(xs):
        num, den = 1, 1
        for m, xm in enumerate(xs):
            if m != j:
                num = _gmul(num, xm ^ at)
                den = _gmul(den, xm ^ xj)
        weights.append(_gdiv(num, den))
    size = len(shares[0][1])
    out = bytearray(size)
    for w, (_, y) in zip(weights, shares):
        if w:
            lw = _LOG[w]
            for i, b in enumerate(y):
                if b:
                    out[i] ^= _EXP[lw + _LOG[b]]
    return bytes(out)


def shamir_combine(shares):
    return shamir_interpolate(shares, 0)


# ======================================================================================================
# Recovery code: base32 (RFC 4648 alphabet), grouped by 4, 40-bit SHA-256 checksum
# ======================================================================================================
_RC_VERSION = 1
_B32 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'


def make_recovery_code(index, share, vault_id):
    if len(share) != KEY_LEN:
        raise ValueError('share must be 32 bytes')
    body = bytes([_RC_VERSION, index]) + bytes(vault_id)[:6] + bytes(share)       # 40 bytes
    raw = body + hashlib.sha256(b'GorgonCovenant/recovery/v1' + body).digest()[:5]  # 45 bytes -> 72 chars
    s = base64.b32encode(raw).decode('ascii')
    return '-'.join(s[i:i + 4] for i in range(0, len(s), 4))


def parse_recovery_code(code):
    """-> (index, share bytes, vault tag 6 bytes). Raises RecoveryCodeError on any typo."""
    s = ''.join(ch for ch in str(code).upper() if not ch.isspace() and ch != '-')
    s = s.replace('0', 'O').replace('1', 'I').replace('8', 'B')     # digits never used by base32
    if len(s) != 72 or any(ch not in _B32 for ch in s):
        raise RecoveryCodeError(f'a recovery code has 72 letters/digits in 18 groups (got {len(s)}); '
                                'check that nothing is missing')
    raw = base64.b32decode(s)
    body, chk = raw[:-5], raw[-5:]
    if not hmac.compare_digest(chk, hashlib.sha256(b'GorgonCovenant/recovery/v1' + body).digest()[:5]):
        raise RecoveryCodeError('the recovery code has a typo (checksum does not match); compare each '
                                'group with the printed sheet')
    if body[0] != _RC_VERSION:
        raise RecoveryCodeError('recovery code from an unknown version')
    return body[1], body[8:40], body[2:8]


# ======================================================================================================
# Passphrase wrapping (scrypt -> AES-256-GCM through the OS backend)
# ======================================================================================================
def _scrypt(passphrase, salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P):
    pw = passphrase.encode('utf-8') if isinstance(passphrase, str) else bytes(passphrase)
    return hashlib.scrypt(pw, salt=salt, n=n, r=r, p=p, maxmem=256 * 1024 * 1024, dklen=KEY_LEN)


def passphrase_wrap(backend, data, passphrase, aad, n=SCRYPT_N):
    salt, nonce = backend.random(16), backend.random(NONCE_LEN)
    key = _scrypt(passphrase, salt, n)
    return {'kdf': 'scrypt', 'n': n, 'r': SCRYPT_R, 'p': SCRYPT_P, 'salt': salt.hex(), 'nonce': nonce.hex(),
            'ct': aes_gcm_encrypt(key, nonce, data, aad, backend).hex()}


def passphrase_unwrap(backend, obj, passphrase, aad):
    key = _scrypt(passphrase, bytes.fromhex(obj['salt']), int(obj['n']), int(obj['r']), int(obj['p']))
    try:
        return aes_gcm_decrypt(key, bytes.fromhex(obj['nonce']), bytes.fromhex(obj['ct']), aad, backend)
    except AuthenticationError:
        raise VaultError('wrong passphrase for this key share') from None


# ======================================================================================================
# DPAPI (Windows, current user) for the local share
# ======================================================================================================
class _Blob(ctypes.Structure):
    _fields_ = [('cbData', ctypes.c_ulong), ('pbData', ctypes.c_void_p)]


def _dpapi(data, entropy, protect):
    if sys.platform != 'win32':
        raise OSError('DPAPI is Windows only')
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    k32 = ctypes.WinDLL('kernel32')
    k32.LocalFree.argtypes, k32.LocalFree.restype = [ctypes.c_void_p], ctypes.c_void_p
    fn = crypt.CryptProtectData if protect else crypt.CryptUnprotectData
    fn.argtypes = [ctypes.POINTER(_Blob), ctypes.c_wchar_p, ctypes.POINTER(_Blob), ctypes.c_void_p,
                   ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(_Blob)]
    fn.restype = ctypes.c_int
    dbuf = ctypes.create_string_buffer(bytes(data), max(1, len(data)))
    ebuf = ctypes.create_string_buffer(bytes(entropy), max(1, len(entropy)))
    src = _Blob(len(data), ctypes.cast(dbuf, ctypes.c_void_p))
    ent = _Blob(len(entropy), ctypes.cast(ebuf, ctypes.c_void_p))
    out = _Blob()
    ok = fn(ctypes.byref(src), 'Stark Covenant key share' if protect else None, ctypes.byref(ent),
            None, None, 0x1, ctypes.byref(out))                         # CRYPTPROTECT_UI_FORBIDDEN
    ctypes.memset(dbuf, 0, max(1, len(data)))
    if not ok:
        raise VaultError(f'DPAPI {"protect" if protect else "unprotect"} failed '
                         f'(error {ctypes.get_last_error()}); was the share made by another Windows user?')
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        k32.LocalFree(out.pbData)


def dpapi_protect(data, entropy=b''):
    return _dpapi(data, entropy, True)


def dpapi_unprotect(data, entropy=b''):
    return _dpapi(data, entropy, False)


# ======================================================================================================
# Removable drives
# ======================================================================================================
def _drive_type(root):
    """GetDriveTypeW(root) on Windows. Tests monkeypatch this to simulate a USB stick."""
    if sys.platform != 'win32':
        mounts = ('/media/', '/run/media/', '/Volumes/', '/mnt/')
        return DRIVE_REMOVABLE if any(os.path.abspath(root).startswith(m) for m in mounts) else 3
    k32 = ctypes.WinDLL('kernel32')
    k32.GetDriveTypeW.argtypes, k32.GetDriveTypeW.restype = [ctypes.c_wchar_p], ctypes.c_uint
    return k32.GetDriveTypeW(root)


def _drive_root(path):
    if sys.platform == 'win32':
        drive = os.path.splitdrive(os.path.abspath(path))[0]
        return drive + '\\' if drive else path
    return os.path.abspath(path)


def is_removable(path):
    return _drive_type(_drive_root(path)) == DRIVE_REMOVABLE


def removable_drives():
    """-> list of roots of removable drives currently plugged in."""
    roots = []
    if sys.platform == 'win32':
        mask = ctypes.WinDLL('kernel32').GetLogicalDrives()
        for i in range(26):
            if mask >> i & 1:
                r = f'{chr(65 + i)}:\\'
                if _drive_type(r) == DRIVE_REMOVABLE:
                    roots.append(r)
    else:
        user = os.environ.get('USER', '')
        for base in (f'/media/{user}', f'/run/media/{user}', '/Volumes', '/media'):
            try:
                roots += [os.path.join(base, d) for d in sorted(os.listdir(base))
                          if os.path.isdir(os.path.join(base, d)) and os.path.join(base, d) not in roots]
            except OSError:
                pass
    return roots


# ======================================================================================================
# Hash-chained audit log
# ======================================================================================================
_GENESIS = '0' * 64


def _canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


class AuditLog:
    def __init__(self, path):
        self.path = path
        self.head_path = path + '.head'
        self._lock = threading.Lock()

    def _last(self):
        last = None
        try:
            with open(self.path, 'rb') as f:
                for line in f:
                    if line.strip():
                        last = line
        except OSError:
            return 0, _GENESIS
        if last is None:
            return 0, _GENESIS
        rec = json.loads(last)
        return rec['seq'], rec['hash']

    def append(self, event, **detail):
        with self._lock:
            seq, prev = self._last()
            rec = {'seq': seq + 1, 'ts': _dt.datetime.now(_dt.timezone.utc).isoformat(timespec='seconds'),
                   'event': event, 'detail': detail, 'prev': prev}
            rec['hash'] = hashlib.sha256(prev.encode('ascii') + _canon(rec)).hexdigest()
            with open(self.path, 'ab') as f:
                f.write(_canon(rec) + b'\n')
                f.flush()
                os.fsync(f.fileno())
            _atomic_write(self.head_path, _canon({'seq': rec['seq'], 'hash': rec['hash']}))
            return rec

    def verify(self):
        """-> (ok, entries, problem or None)."""
        prev, n = _GENESIS, 0
        try:
            with open(self.path, 'rb') as f:
                lines = [ln for ln in f if ln.strip()]
        except OSError:
            lines = []
        for ln in lines:
            try:
                rec = json.loads(ln)
                h = rec.pop('hash')
            except Exception:
                return False, n, f'entry {n + 1} unreadable'
            if rec.get('prev') != prev or rec.get('seq') != n + 1:
                return False, n, f'chain broken at entry {n + 1}'
            if hashlib.sha256(prev.encode('ascii') + _canon(rec)).hexdigest() != h:
                return False, n, f'entry {n + 1} was edited'
            prev, n = h, n + 1
        head = _read_json(self.head_path)
        if head and (head.get('seq') != n or head.get('hash') != prev):
            return False, n, f'log ends at entry {n} but the head says {head.get("seq")} (entries removed)'
        if head is None and n:
            return False, n, 'head file missing'
        return True, n, None

    def entries(self):
        try:
            with open(self.path, 'rb') as f:
                return [json.loads(ln) for ln in f if ln.strip()]
        except OSError:
            return []


# ======================================================================================================
# Files
# ======================================================================================================
def _atomic_write(path, data, mode=0o600):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f'{path}.{os.getpid()}.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_BINARY', 0), mode)
    with os.fdopen(fd, 'wb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _read_json(path):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def default_root():
    base = os.environ.get('LOCALAPPDATA') or os.path.join(os.path.expanduser('~'), '.local', 'share')
    return os.path.join(base, 'GorgonCovenant', 'vault')


def _wipe(buf):
    if isinstance(buf, bytearray):
        for i in range(len(buf)):
            buf[i] = 0


# ======================================================================================================
# The vault
# ======================================================================================================
class Vault:
    """Vault(root).create(usb=...) once; then unlock() / encrypt() / decrypt() / lock() / status().

    Files in root: vault.json (public metadata), keyring.bin (data keys, AES-GCM under the vault key),
    share1.dpapi (this PC's share), audit.jsonl (+ .head). None of them alone opens the vault."""

    META = 'vault.json'
    KEYRING = 'keyring.bin'
    LOCAL_SHARE = 'share1.dpapi'

    def __init__(self, root=None, backend=None):
        self.root = os.path.abspath(root or default_root())
        self._backend = backend if backend is not None else get_backend()
        self._keys = {}            # key_id -> (bytearray, backend key object)
        self._current = None
        self._vault_key = None     # bytearray while unlocked
        self._lock = threading.Lock()
        self.audit = AuditLog(os.path.join(self.root, 'audit.jsonl'))

    # ------------------------------------------------------------------ helpers
    def _p(self, name):
        return os.path.join(self.root, name)

    def _meta(self):
        return _read_json(self._p(self.META))

    def _require_backend(self):
        if self._backend is None:
            raise EncryptionUnavailable(f'{STATUS_UNAVAILABLE}: no OS AES-GCM (CNG or OpenSSL libcrypto) on '
                                        'this machine; patient data will not be stored')
        return self._backend

    def _event(self, event, **detail):
        if os.path.isdir(self.root) and os.path.exists(self._p(self.META)):
            try:
                self.audit.append(event, **detail)
            except OSError:
                pass

    def _keyring_aad(self, vid):
        return b'GorgonCovenant/keyring/v1' + vid

    def _kcv(self, vault_key):
        return hmac.new(bytes(vault_key), b'GorgonCovenant/kcv/v1', hashlib.sha256).hexdigest()[:32]

    def _seal_keyring(self, vault_key, vid, ring):
        be = self._backend
        nonce = be.random(NONCE_LEN)
        body = _canon(ring)
        return MAGIC + nonce + aes_gcm_encrypt(bytes(vault_key), nonce, body, self._keyring_aad(vid), be)

    def _open_keyring(self, vault_key, vid, blob):
        if blob[:4] != MAGIC:
            raise VaultError('keyring file damaged')
        try:
            body = aes_gcm_decrypt(bytes(vault_key), blob[4:16], blob[16:], self._keyring_aad(vid), self._backend)
        except AuthenticationError:
            raise VaultError('these key shares do not open this vault (wrong or mixed shares)') from None
        return json.loads(body)

    def _share_entropy(self, vid):
        return _SHARE_ENTROPY + vid

    def _share_aad(self, vid, x):
        return _SHARE_ENTROPY + vid + bytes([x])

    def _write_local_share(self, vid, x, share, passphrase=None):
        # inner payload = the share, or the share sealed by a passphrase (scrypt + AES-GCM);
        # on Windows the whole payload is then wrapped by DPAPI (current Windows user).
        payload = {'share': bytes(share).hex()}
        if passphrase:
            payload = {'passphrase': passphrase_wrap(self._backend, bytes(share), passphrase,
                                                     self._share_aad(vid, x))}
        rec = {'v': 1, 'vault_id': vid.hex(), 'x': x}
        if sys.platform == 'win32':
            rec['dpapi'] = base64.b64encode(dpapi_protect(_canon(payload), self._share_entropy(vid))).decode('ascii')
            rec['protection'] = 'dpapi' + ('+scrypt' if passphrase else '')
        else:
            rec['payload'] = payload
            rec['protection'] = 'scrypt' if passphrase else 'file-permissions-only'
        return _canon(rec)

    def _read_local_share(self, vid, passphrase=None):
        rec = _read_json(self._p(self.LOCAL_SHARE))
        if not rec or rec.get('vault_id') != vid.hex():
            return None
        x = int(rec['x'])
        if 'dpapi' in rec:
            payload = json.loads(dpapi_unprotect(base64.b64decode(rec['dpapi']), self._share_entropy(vid)))
        else:
            payload = rec.get('payload') or {}
        if 'passphrase' in payload:
            if not passphrase:
                raise VaultError('this PC\'s key share needs its passphrase')
            return x, passphrase_unwrap(self._backend, payload['passphrase'], passphrase, self._share_aad(vid, x))
        return x, bytes.fromhex(payload['share'])

    def _usb_record(self, vid, x, share, passphrase=None):
        rec = {'v': 1, 'app': APP_NAME, 'vault_id': vid.hex(), 'x': x,
               'note': 'Stark Covenant key share 2. Keep this USB stick apart from the clinic PC. '
                       'One share alone cannot open the records.'}
        if passphrase:
            rec['passphrase'] = passphrase_wrap(self._backend, bytes(share), passphrase, self._share_aad(vid, x))
        else:
            rec['share'] = bytes(share).hex()
        return rec

    @staticmethod
    def _check_usb(usb):
        if not usb or not os.path.isdir(usb):
            raise VaultError(f'USB drive {usb} is not available; plug in the USB stick')
        if not is_removable(usb):
            raise VaultError(f'{usb} is not a removable drive; the second share must live on a USB stick')
        return os.path.join(usb, KEYSHARE_NAME)

    def find_usb_share(self, usb=None):
        """-> path of this vault's GORGON_COVENANT.KEYSHARE on a removable drive, or None."""
        meta = self._meta() or {}
        roots = [usb] if usb else removable_drives()
        for r in roots:
            path = r if r.endswith(KEYSHARE_NAME) else os.path.join(r, KEYSHARE_NAME)
            rec = _read_json(path)
            if rec and (not meta or rec.get('vault_id') == meta.get('vault_id')) and is_removable(path):
                return path
        return None

    def _read_usb_share(self, path, vid, passphrase=None):
        rec = _read_json(path)
        if not rec or rec.get('vault_id') != vid.hex():
            raise VaultError('the USB key share belongs to a different vault')
        x = int(rec['x'])
        if 'passphrase' in rec:
            if not passphrase:
                raise VaultError('the USB key share needs its passphrase')
            return x, passphrase_unwrap(self._backend, rec['passphrase'], passphrase, self._share_aad(vid, x))
        return x, bytes.fromhex(rec['share'])

    def _install_keys(self, vault_key, ring):
        be = self._backend
        self._drop_keys()
        self._vault_key = bytearray(vault_key)
        for kid, hexkey in ring['keys'].items():
            raw = bytearray(bytes.fromhex(hexkey))
            self._keys[int(kid)] = (raw, be.new_key(bytes(raw)))
        self._current = int(ring['current'])

    def _drop_keys(self):
        for raw, obj in self._keys.values():
            obj.destroy()
            _wipe(raw)
        self._keys.clear()
        if self._vault_key is not None:
            _wipe(self._vault_key)
        self._vault_key = None
        self._current = None

    def _ring(self):
        return {'current': self._current, 'keys': {str(k): bytes(v[0]).hex() for k, v in self._keys.items()}}

    def _commit(self, writes):
        """Write several files as one step: on any failure every file gets its previous content back.
        writes = [(path, bytes)]."""
        before = []
        try:
            for path, data in writes:
                try:
                    with open(path, 'rb') as f:
                        before.append((path, f.read()))
                except OSError:
                    before.append((path, None))
                _atomic_write(path, data)
        except Exception:
            for path, old in reversed(before):
                try:
                    if old is None:
                        os.remove(path)
                    else:
                        _atomic_write(path, old)
                except OSError:
                    pass
            raise

    def _new_key_files(self, meta, vault_key, ring, usb, passphrase, usb_passphrase):
        """Everything a (re)issued key needs, as [(path, bytes)], plus the recovery codes. Writes nothing."""
        vid = bytes.fromhex(meta['vault_id'])
        usb_path = self._check_usb(usb)
        shares = shamir_split(bytes(vault_key), int(meta['k']), int(meta['n']), self._backend.random)
        keyring = self._seal_keyring(vault_key, vid, ring)
        usb_rec = self._usb_record(vid, shares[1][0], shares[1][1], usb_passphrase)
        usb_rec['vault_meta'] = meta
        usb_rec['keyring'] = base64.b64encode(keyring).decode('ascii')
        writes = [(self._p(self.META), _canon(meta)), (self._p(self.KEYRING), keyring),
                  (self._p(self.LOCAL_SHARE), self._write_local_share(vid, shares[0][0], shares[0][1], passphrase)),
                  (usb_path, _canon(usb_rec))]
        codes = [make_recovery_code(x, s, vid) for x, s in shares[2:]]
        return writes, codes, usb_path

    # ------------------------------------------------------------------ public API
    def exists(self):
        return os.path.exists(self._p(self.META))

    def create(self, usb, k=DEFAULT_K, n=DEFAULT_N, passphrase=None, usb_passphrase=None):
        """Make a new vault. usb = root of a removable drive for share 2. -> list of recovery codes (shares
        3..n; print them, they are shown only now). The vault is left unlocked."""
        be = self._require_backend()
        if self.exists():
            raise VaultError(f'a vault already exists in {self.root}')
        if not 2 <= k <= n <= 16 or n < 3:
            raise VaultError('need 2 <= k <= n, n >= 3 (this PC, USB stick, recovery code)')
        self._check_usb(usb)
        vid = be.random(16)
        vault_key = bytearray(be.random(KEY_LEN))
        ring = {'current': 1, 'keys': {'1': be.random(KEY_LEN).hex()}}
        meta = {'v': 1, 'app': APP_NAME, 'vault_id': vid.hex(), 'k': k, 'n': n, 'kcv': self._kcv(vault_key),
                'created': _dt.datetime.now(_dt.timezone.utc).isoformat(timespec='seconds'),
                'cipher': 'AES-256-GCM', 'backend': be.name, 'epoch': 1}
        writes, codes, usb_path = self._new_key_files(meta, vault_key, ring, usb, passphrase, usb_passphrase)
        os.makedirs(self.root, exist_ok=True)
        self._commit(writes[1:] + writes[:1])        # vault.json last: a half-made vault never "exists"
        self._vid_cache = vid
        self._install_keys(vault_key, ring)
        _wipe(vault_key)
        self.audit.append('create', k=k, n=n, backend=be.name, usb=bool(usb_path), recovery_codes=len(codes))
        return codes

    def unlock(self, usb=None, recovery_codes=(), passphrase=None, usb_passphrase=None, use_local=True):
        """Open the vault with any k shares: this PC's share, the USB stick (found automatically or given as
        usb=), and/or recovery codes. -> self. Raises VaultError if fewer than k valid shares."""
        be = self._require_backend()
        meta = self._meta()
        if not meta:
            raise VaultError(f'no vault in {self.root}')
        vid, k = bytes.fromhex(meta['vault_id']), int(meta['k'])
        got, sources, problems = {}, [], []
        if use_local:
            try:
                s = self._read_local_share(vid, passphrase)
                if s:
                    got[s[0]] = s[1]
                    sources.append('pc')
            except Exception as e:
                problems.append(f'this PC: {e}')
        if len(got) < k:
            path = self.find_usb_share(usb)
            if path:
                try:
                    x, s = self._read_usb_share(path, vid, usb_passphrase)
                    got[x] = s
                    sources.append('usb')
                except Exception as e:
                    problems.append(f'USB: {e}')
            elif usb:
                problems.append(f'USB: no key share on a removable drive at {usb}')
        if isinstance(recovery_codes, str):
            recovery_codes = [recovery_codes]
        for code in recovery_codes:
            try:
                x, s, tag = parse_recovery_code(code)
            except RecoveryCodeError as e:
                problems.append(f'recovery code: {e}')
                continue
            if tag != vid[:6]:
                problems.append('recovery code: belongs to a different vault')
                continue
            got[x] = s
            sources.append('code')
        if len(got) < k:
            self._event('unlock_failed', reason='not enough shares', have=len(got), need=k)
            raise VaultError(f'need {k} key shares, found {len(got)}' + (f' ({"; ".join(problems)})' if problems
                                                                         else ''))
        vault_key = bytearray(shamir_combine(sorted(got.items())[:max(k, len(got))]))
        if not hmac.compare_digest(self._kcv(vault_key), meta['kcv']):
            # more than k shares and one is bad: try every k-subset before giving up
            for combo in itertools.combinations(sorted(got.items()), k):
                cand = bytearray(shamir_combine(combo))
                if hmac.compare_digest(self._kcv(cand), meta['kcv']):
                    vault_key = cand
                    break
            else:
                _wipe(vault_key)
                self._event('unlock_failed', reason='shares do not match', sources=sources)
                raise VaultError('these key shares do not open this vault (wrong, damaged or mixed shares)')
        with open(self._p(self.KEYRING), 'rb') as f:
            ring = self._open_keyring(vault_key, vid, f.read())
        with self._lock:
            self._install_keys(vault_key, ring)
        _wipe(vault_key)
        self._event('unlock', sources=sources)
        return self

    def lock(self):
        with self._lock:
            was = self._vault_key is not None
            self._drop_keys()
        if was:
            self._event('lock')

    @property
    def unlocked(self):
        return self._vault_key is not None

    def status(self):
        be = self._backend
        meta = self._meta()
        st = {'app': APP_NAME, 'root': self.root, 'backend': be.name if be else None,
              'cipher': 'AES-256-GCM' if be else None}
        if be is None:
            st['state'] = STATUS_UNAVAILABLE
            st['message'] = ('No operating-system AES-GCM found (Windows CNG or OpenSSL libcrypto). '
                             'Stark Covenant will not store patient data on this machine.')
            return st
        if not meta:
            st['state'] = 'uninitialized'
            return st
        local = _read_json(self._p(self.LOCAL_SHARE)) or {}
        ok, n, problem = self.audit.verify()
        st.update(state='unlocked' if self.unlocked else 'locked', threshold=meta['k'], shares=meta['n'],
                  vault_id=meta['vault_id'][:8], local_share=local.get('protection'),
                  usb_share=self.find_usb_share(), key_id=self._current, keys=len(self._keys) or None,
                  epoch=meta.get('epoch', 1), audit_ok=ok, audit_entries=n, audit_problem=problem)
        return st

    def _key_aad(self, kid, aad):
        meta_vid = getattr(self, '_vid_cache', None)
        if meta_vid is None:
            meta_vid = self._vid_cache = bytes.fromhex(self._meta()['vault_id'])
        return MAGIC + struct.pack('>I', kid) + meta_vid + bytes(aad or b'')

    def encrypt(self, plaintext, aad=b''):
        """-> blob bytes (header + nonce + ciphertext + tag). Pass the record id as aad to bind the blob to it."""
        be = self._require_backend()
        if isinstance(plaintext, str):
            raise TypeError('encrypt takes bytes (encode text first)')
        with self._lock:
            if self._vault_key is None:
                raise VaultError('vault is locked')
            kid = self._current
            keyobj = self._keys[kid][1]
        nonce = be.random(NONCE_LEN)
        hdr = MAGIC + struct.pack('>I', kid) + nonce
        return hdr + keyobj.encrypt(nonce, bytes(plaintext), self._key_aad(kid, aad))

    def decrypt(self, blob, aad=b''):
        """-> plaintext. Raises AuthenticationError if anything (data, tag, header, aad) was altered."""
        self._require_backend()
        blob = bytes(blob)
        if len(blob) < 4 + 4 + NONCE_LEN + TAG_LEN or blob[:4] != MAGIC:
            raise AuthenticationError('not a Stark Covenant blob (or truncated)')
        kid = struct.unpack('>I', blob[4:8])[0]
        with self._lock:
            if self._vault_key is None:
                raise VaultError('vault is locked')
            ent = self._keys.get(kid)
        if ent is None:
            raise AuthenticationError(f'blob uses unknown key {kid}')
        try:
            return ent[1].decrypt(blob[8:8 + NONCE_LEN], blob[8 + NONCE_LEN:], self._key_aad(kid, aad))
        except AuthenticationError:
            self._event('decrypt_auth_failed', key_id=kid)
            raise

    def reencrypt(self, blob, aad=b''):
        """Decrypt with whatever key made the blob and encrypt again under the current key."""
        return self.encrypt(self.decrypt(blob, aad), aad)

    def rotate_key(self, reshare=False, usb=None, passphrase=None, usb_passphrase=None):
        """Add a fresh data key (new blobs use it; old blobs still open). With reshare=True also replace the
        vault key and issue new shares (needs the USB stick; old USB share and recovery codes stop working).
        -> {'key_id': n, 'recovery_codes': [...] (reshare only), 'usb': path}"""
        be = self._require_backend()
        meta = self._meta()
        vid = bytes.fromhex(meta['vault_id'])
        with self._lock:
            if self._vault_key is None:
                raise VaultError('unlock the vault before rotating keys')
            new_id = max(self._keys) + 1
            raw = bytearray(be.random(KEY_LEN))
            ring = {'current': new_id,
                    'keys': {**{str(k): bytes(v[0]).hex() for k, v in self._keys.items()}, str(new_id): raw.hex()}}
            out = {'key_id': new_id}
            if reshare:
                found = self.find_usb_share(usb)
                usb_root = usb or (os.path.dirname(found) if found else None)
                if not usb_root:
                    raise VaultError('plug in the USB stick to re-issue key shares')
                new_vk = bytearray(be.random(KEY_LEN))
                meta = dict(meta, kcv=self._kcv(new_vk), epoch=int(meta.get('epoch', 1)) + 1)
                writes, codes, usb_path = self._new_key_files(meta, new_vk, ring, usb_root, passphrase,
                                                              usb_passphrase)
                self._commit(writes)               # all four files, or none
                _wipe(self._vault_key)
                self._vault_key = new_vk
                out.update(recovery_codes=codes, usb=usb_path)
            else:
                writes = [(self._p(self.KEYRING), self._seal_keyring(self._vault_key, vid, ring))]
                usb_path = self.find_usb_share(usb)
                if usb_path:                         # keep the USB copy of the keyring current
                    rec = _read_json(usb_path)
                    rec['keyring'] = base64.b64encode(writes[0][1]).decode('ascii')
                    rec['vault_meta'] = meta
                    writes.append((usb_path, _canon(rec)))
                self._commit(writes)
                out['usb'] = usb_path
            self._keys[new_id] = (raw, be.new_key(bytes(raw)))   # only once the new key is safely on disk
            self._current = new_id
        self._event('rotate_key', key_id=new_id, reshare=bool(reshare), epoch=meta.get('epoch', 1))
        return out

    @classmethod
    def restore_from_usb(cls, root, usb, backend=None):
        """New/replacement PC: copy vault.json + keyring from the USB key share file into root (no secrets
        are copied; the keyring is still sealed). Then unlock(usb=..., recovery_codes=[code]) and
        rotate_key(reshare=True) to give this PC its own share."""
        v = cls(root, backend)
        path = v.find_usb_share(usb)
        rec = _read_json(path) if path else None
        if not rec or 'vault_meta' not in rec or 'keyring' not in rec:
            raise VaultError('no restorable Stark Covenant key share on that USB drive')
        if v.exists():
            raise VaultError(f'a vault already exists in {v.root}')
        os.makedirs(v.root, exist_ok=True)
        _atomic_write(v._p(cls.META), _canon(rec['vault_meta']))
        _atomic_write(v._p(cls.KEYRING), base64.b64decode(rec['keyring']))
        v.audit.append('restore_from_usb')
        return v

    def verify_audit(self):
        return self.audit.verify()


def default_vault():
    """The app's vault in the default location, unlocked with this PC's share + a plugged-in USB stick.
    Raises VaultError if it cannot be opened (callers then fall back or ask for the recovery code)."""
    v = Vault()
    if not v.exists():
        raise VaultError('no Stark Covenant vault on this PC yet')
    return v.unlock()


def benchmark(n=200, size=4096):
    """Honest timings on this machine (ms). Uses a throwaway in-memory key, writes nothing."""
    be = get_backend()
    if be is None:
        return {'state': STATUS_UNAVAILABLE}
    key = be.new_key(be.random(KEY_LEN))
    data = be.random(size)
    t = time.perf_counter()
    blobs = [key.encrypt(be.random(NONCE_LEN), data, b'aad') for _ in range(n)]
    enc = (time.perf_counter() - t) * 1000 / n
    key.destroy()
    shares = shamir_split(be.random(KEY_LEN), 2, 3)
    t = time.perf_counter()
    for _ in range(100):
        shamir_combine(shares[:2])
    comb = (time.perf_counter() - t) * 1000 / 100
    t = time.perf_counter()
    _scrypt('bench passphrase', b'0' * 16)
    scr = (time.perf_counter() - t) * 1000
    return {'backend': be.name, f'encrypt_{size}B_ms': round(enc, 4), 'shamir_combine_ms': round(comb, 4),
            'scrypt_ms': round(scr, 1), 'blobs': len(blobs)}


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='Stark Covenant vault tools')
    ap.add_argument('cmd', choices=['status', 'drives', 'bench', 'verify-audit'])
    ap.add_argument('--root', default=None)
    a = ap.parse_args()
    if a.cmd == 'status':
        print(json.dumps(Vault(a.root).status(), indent=1))
    elif a.cmd == 'drives':
        print(removable_drives())
    elif a.cmd == 'bench':
        print(json.dumps(benchmark(), indent=1))
    else:
        print(Vault(a.root).verify_audit())
