"""Stark Covenant licence guard: offline signed licence tokens (Ed25519 signatures, standard library only).

Adapted from Stark Quill's (formerly Gorgon Quill) proven licence_guard (same token mechanics), with Covenant's OWN signing key and
token prefix GC1, so a Quill key can never unlock Covenant and the reverse.

What it does
  * A licence is a token  GC1.<payload b64url>.<Ed25519 signature b64url>  signed by the seller's private key
    (kept in .keys/ on the seller's PC by keygen_chart.py, never shipped). The app only carries the PUBLIC
    key, so it can check a token but cannot make one.
  * Plans for sale (display name, price):  Stark Covenant Baby $0 | Solo $50 one-time | Practice $200 one-time (up to
    4 users; the Clinic edition, $500, is a Practice key for up to 10 users) | Humanitarian $50 one-time. The monthly and yearly plans are retired from sale (old keys still work).
    The storefront's Founding Beta editions issue the same keys: Founding Solo = Solo, Founding Practice = Practice x3.
  * Payload: buyer, plan, scale (Individual .. Continent, capped per plan), seats (devices), expiry date,
    offline grace days, optional seller-bound device list.
  * Seats: a local hardware fingerprint (salted SHA-256 of MachineGuid, volume serial, CPU/arch, hostname),
    computed locally, never sent anywhere. Seats are recorded in the licence file that travels with the
    token; one device more than the plan allows is refused with a plain message.
  * A per-install activation file is HMAC-signed with a random per-install key and remembers when the
    licence was last seen, so winding the PC clock back does not stretch a subscription.
  * Revocation: the signed update feed (latest.json) carries SHA-256 hashes of withdrawn tokens; the last
    copy is cached so the check also works offline.

Honest limits (say them plainly to buyers)
  * This makes a licence HARD TO SHARE, not impossible to copy. Anything that runs fully offline on the
    buyer's PC can be patched by someone determined enough. It keeps honest clinics honest.
  * A licence problem NEVER locks a clinic out of its patient records: an expired, revoked or invalid
    licence drops to the Baby plan, and existing records stay readable and exportable.
  * Ed25519 verification here is pure Python (milliseconds, see benchmark()); the verified result is cached
    per process. Signatures are not encryption: patient data is encrypted only by security_vault.py with the
    operating system's AES-GCM.
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
import socket
import sys
import time

# APP_ID, TOKEN_PREFIX, FEED_PREFIX and every salt below are embedded in keys already issued (and in seat files and
# device codes): they keep their exact pre-1.1.0 bytes for good, whatever the product is called.
APP_ID = 'GorgonCovenant'
APP_NAME = 'Stark Covenant'
TOKEN_PREFIX = 'GC1'
FREE_PLAN = 'BABY'

SCALES = ('Individual', 'Practice', 'Clinic', 'Hospital', 'Network', 'City', 'Region', 'Country', 'Continent')

# The plans for sale, in the storefront's words (the storefront catalog.json reads this literal and must match it):
# plan key -> display name, price, billing period, 'seats' = people covered (users; for Practice the most clinicians),
# 'devices' = device seats one key allows by default, largest scale it may be issued for.
PLAN_INFO = {
    'BABY':         {'name': 'Stark Covenant Baby', 'price_usd': 0, 'period': None, 'seats': 1, 'devices': 1,
                     'max_scale': 'Individual'},
    'SOLO':         {'name': 'Solo', 'price_usd': 50, 'period': None, 'perpetual': True, 'seats': 1, 'devices': 2,
                     'max_scale': 'Individual', 'users': 1},
    'PRACTICE':     {'name': 'Practice', 'price_usd': 200, 'period': None, 'perpetual': True, 'seats': 4,
                     'devices': 4, 'max_scale': 'Clinic', 'max_seats': 10},
    'HUMANITARIAN': {'name': 'Humanitarian', 'price_usd': 50, 'period': None, 'perpetual': True, 'seats': 10,
                     'devices': 10, 'max_scale': 'Continent', 'users': 10},
}
# Storefront edition id -> (plan key, seats (users) the key covers; None = the plan's default). Owner-approved
# 2026-11-20 (market_radar LP-c38b420dc7): practice-team $200 = 4 users, clinic-team $500 = 10 users (both PRACTICE
# keys, they differ only in seats), founding-solo $25 = a Solo key, founding-practice $99 = a 3-user Practice key.
# The old per-clinician Practice (2-25) is retired; a bare 'practice' still resolves to a 4-seat key.
CATALOG_EDITIONS = {'baby': ('BABY', None), 'solo': ('SOLO', None), 'practice-team': ('PRACTICE', 4),
                    'clinic-team': ('PRACTICE', 10), 'humanitarian': ('HUMANITARIAN', None),
                    'founding-solo': ('SOLO', None), 'founding-practice': ('PRACTICE', 3)}
# Plan IDs = storefront product / edition. 1.1.0+: the product is stark-covenant; the old gorgon-chart/... IDs (the
# storefront product id up to 1.1.0) are still accepted and mean exactly the same edition.
PRODUCT_ID = 'stark-covenant'
LEGACY_PRODUCT_IDS = ('gorgon-chart',)
PLAN_IDS = {PRODUCT_ID + '/' + e: v for e, v in CATALOG_EDITIONS.items()}
LEGACY_PLAN_IDS = {old + '/' + e: PRODUCT_ID + '/' + e for old in LEGACY_PRODUCT_IDS for e in CATALOG_EDITIONS}
# Keys issued under the old per-clinician Practice carry 'clinicians' (2-25, 2 devices each): they stay valid.
LEGACY_CLINICIAN_RANGE = (2, 25)
LEGACY_DEVICES_PER_CLINICIAN = 2

# Monthly plans retired from sale on 2026-11-19. Keys issued for them keep working until their own end date and
# renewal keys can still be issued, so they stay valid plan codes ('seats' = devices here). Never sold again.
LEGACY_PLAN_INFO = {
    # one practitioner, all single-user features (owner's call 2026-11-19: individuals pay $30, not $79)
    'INDIVIDUAL':    {'name': 'Individual',           'price_usd': 30,  'period': 'month', 'seats': 2,
                      'max_scale': 'Individual'},
    'CLINIC':        {'name': 'Clinic',               'price_usd': 79,  'period': 'month', 'seats': 5,
                      'max_scale': 'Clinic'},
    'HOSPITAL':      {'name': 'Hospital',             'price_usd': 249, 'period': 'month', 'seats': 25,
                      'max_scale': 'Hospital'},
    'HOSPITAL_PLUS': {'name': 'Hospital Plus',        'price_usd': 999, 'period': 'month', 'seats': 120,
                      'max_scale': 'Network'},
    # Large-scale operations (owner's call 2026-11-19: "larger prices for larger operations but make it fair"):
    # the price per seat FALLS as the operation grows (Clinic ~$15.80, Hospital ~$10, Network $7, Region $4.50,
    # National $2 per seat per month); extra seats on any large plan cost that plan's per-seat rate.
    'HEALTH_NETWORK': {'name': 'Health Network',      'price_usd': 3499, 'period': 'month', 'seats': 500,
                       'max_scale': 'City'},
    'REGIONAL':      {'name': 'Regional Health System', 'price_usd': 8999, 'period': 'month', 'seats': 2000,
                      'max_scale': 'Region'},
    'NATIONAL':      {'name': 'National', 'price_usd': 19999, 'period': 'month', 'seats': 10000,
                      'max_scale': 'Continent'},
}
# Humanitarian before the one-time switch ($49/year): yearly keys issued then keep working until their end date.
_HUMANITARIAN_YEARLY = {'name': 'Humanitarian', 'price_usd': 49, 'period': 'year', 'seats': 10,
                        'max_scale': 'Continent'}
# Fairness rules shown wherever prices are shown (the licence itself does not enforce them; they are applied when
# the seller issues the key). The storefront catalog prints these verbatim; i18n app.lic.fair1-5 translate them.
FAIRNESS_RULES = (
    'Each licence is paid once: no subscription and no setup fees.',
    'Public-sector and non-profit operators in World Bank low- and lower-middle-income countries pay 50% of the '
    'listed price on every plan.',
    'Registered humanitarian organisations use the Humanitarian plan ($50 one-time).',
    'Practice covers 2 to 4 users and Clinic 5 to 10 users, each at one flat price.',
    'If you stop using Stark Covenant, your records stay readable and exportable.',
)
LARGE_PLAN_EXTRA_SEAT_USD = {'HEALTH_NETWORK': 7.0, 'REGIONAL': 4.5, 'NATIONAL': 2.0}
PLANS = tuple(PLAN_INFO)

# ---- Pricing 2026-12: ONE-TIME perpetual licences. APPROVED by the owner on 2026-11-19 (signed decision
# LP-9d83b09182 in Gorgon_Storefront/approvals/price_approvals.jsonl). The plans for sale are:
#   Baby $0 (solo demo, 1 user) | Solo $50 one-time (1 clinician) | Practice $200 one-time (4 users) and
#   Clinic $500 one-time (10 users; a Practice key with 10 seats) | Humanitarian $50 one-time (10 users).
# Christmas sale (decision LP-2ad6059ee3): Solo 50% off ($25 at the $50 list price), 2026-12-19 to 2027-01-02; the checkout applies it.
# A one-time key is perpetual: it has no end date and carries 'major' (the major version it covers, e.g. 1 = every
# 1.x release). A newer major app (APP_MAJOR) treats an older key as "upgrade available" (Baby plan, records stay
# readable); the old version keeps working with it forever. Upgrades to a new major are sold at 50% off to owners.
# Every monthly or yearly plan stays a valid licence code: existing keys keep working until their own end date
# and existing subscribers can still be issued renewal keys. Nothing is ever revoked because of this change.
PRICING_PROPOSAL_APPROVED = True
PRICING_PROPOSAL_ENV = 'GORGON_COVENANT_PRICING_PROPOSAL'
APP_MAJOR = 1                   # the major version of THIS app; perpetual keys must cover it
UPGRADE_DISCOUNT = 0.5          # existing owners pay 50% for the next major version
PROPOSED_PLANS = {k: dict(PLAN_INFO[k]) for k in ('SOLO', 'PRACTICE')}
PROPOSED_CHANGES = {'HUMANITARIAN': dict(PLAN_INFO['HUMANITARIAN'])}
PROPOSED_RETIRED = tuple(LEGACY_PLAN_INFO)
RETIRED_FROM_SALE = ()          # plan codes still valid for keys but no longer sold (filled when the proposal is on)
SALES = ({'id': 'christmas-2026-covenant-solo', 'plan': 'SOLO', 'percent_off': 50, 'starts': '2026-12-19',
          'ends': '2027-01-02'},)
_CURRENT = json.dumps(PLAN_INFO)                     # the plans for sale
_ORIGINAL = json.dumps({'BABY': PLAN_INFO['BABY'], **LEGACY_PLAN_INFO, 'HUMANITARIAN': _HUMANITARIAN_YEARLY})


def sale_price_usd(plan, day=None):
    """List price on a given day, with any approved sale applied (the checkout charges it; this is for display)."""
    day = (day or _dt.date.today()).isoformat()
    price = PLAN_INFO[plan]['price_usd']
    for s in SALES:
        if s['plan'] == plan and s['starts'] <= day <= s['ends']:
            return round(price * (100 - s['percent_off']) / 100, 2)
    return price


def restore_original_catalogue():
    """Back to the catalogue before the pricing switch (tests of the switch-off state)."""
    global PLANS, RETIRED_FROM_SALE
    PLAN_INFO.clear()
    PLAN_INFO.update(json.loads(_ORIGINAL))
    RETIRED_FROM_SALE = ()
    PLANS = tuple(PLAN_INFO)
    return PLANS


def pricing_proposal_active():
    return bool(PRICING_PROPOSAL_APPROVED) or os.environ.get(PRICING_PROPOSAL_ENV) == '1'


def apply_pricing_proposal():
    """The plans for sale, then every retired plan code (still valid for existing keys, marked retired). Idempotent."""
    global PLANS, RETIRED_FROM_SALE
    PLAN_INFO.clear()
    PLAN_INFO.update(json.loads(_CURRENT))
    PLAN_INFO.update(json.loads(json.dumps(LEGACY_PLAN_INFO)))
    RETIRED_FROM_SALE = tuple(PROPOSED_RETIRED)
    PLANS = tuple(PLAN_INFO)
    return PLANS


def plan_devices(plan):
    """Device seats one key of this plan allows by default."""
    info = PLAN_INFO[plan]
    return info.get('devices', info['seats'])


def plan_id(name):
    """'solo', 'stark-covenant/solo' or the old 'gorgon-chart/solo' -> 'stark-covenant/solo'; None if unknown."""
    s = str(name or '').strip().lower()
    s = LEGACY_PLAN_IDS.get(s, s)
    if s in CATALOG_EDITIONS:
        s = PRODUCT_ID + '/' + s
    return s if s in PLAN_IDS else None


def edition_plan_id(plan, seats=None):
    """The stark-covenant/... ID a new key for this plan (and seat count) is sold as, or None (retired plans)."""
    for e, (p, n) in CATALOG_EDITIONS.items():
        if p == plan and n == (seats or None):
            return PRODUCT_ID + '/' + e
    for e, (p, n) in CATALOG_EDITIONS.items():
        if p == plan and n is None:
            return PRODUCT_ID + '/' + e
    return None


def resolve_plan(name):
    """'SOLO', 'solo', 'founding-practice', 'stark-covenant/solo', 'gorgon-chart/solo' -> (plan key, clinicians the
    key covers or None). LicenseError if unknown."""
    s = str(name or '').strip()
    if s.upper() in PLAN_INFO:
        return s.upper(), None
    if s.lower() in CATALOG_EDITIONS:
        return CATALOG_EDITIONS[s.lower()]
    if plan_id(s):
        return PLAN_IDS[plan_id(s)]
    raise LicenseError(f'unknown plan {name!r}; choose from {", ".join(PLANS)} or {", ".join(CATALOG_EDITIONS)}')


def for_sale(plan):
    return plan in PLAN_INFO and plan not in RETIRED_FROM_SALE


def clinician_range(plan):
    """Only keys issued under the retired per-clinician Practice carry 'clinicians'; no plan is per-clinician now."""
    return None


def plan_total_usd(plan, clinicians=None, upgrade=False):
    """Price of one purchase (flat per licence; `clinicians` is ignored, kept for old callers);
    upgrade=True is the price for an existing owner moving to the next major version."""
    info = PLAN_INFO[plan]
    total = info['price_usd']
    return round(total * (1 - UPGRADE_DISCOUNT), 2) if upgrade else total


def is_perpetual(plan):
    return bool(PLAN_INFO.get(plan, {}).get('perpetual'))


DEFAULT_GRACE_DAYS = 14      # a clinic offline for two weeks after renewal day keeps its plan
CLOCK_SKEW_DAYS = 1          # tolerated backwards clock movement before we call it a rollback
_FP_SALT = b'GorgonCovenant/device-fingerprint/v1'   # app-wide salt: raw identifiers never leave this module

# Seller public key (Ed25519, 32 bytes hex). keygen_chart.py init writes it here; the private key stays in .keys/.
PUBLIC_KEY_HEX = 'b1747ecf1f895dbcc4cc753b40746bf314c0cc2e912c7edce98a9f125bf0e791'


def plan_name(plan):
    return PLAN_INFO.get(plan, PLAN_INFO[FREE_PLAN])['name']


def plan_price(plan):
    """-> e.g. '$79/month', '$0'."""
    info = PLAN_INFO[plan]
    unit = ''
    if info.get('perpetual'):
        return f"${info['price_usd']}{unit} one-time"
    return f"${info['price_usd']}" + unit + (f"/{info['period']}" if info['period'] else '')


def scale_allowed(plan, scale):
    return scale in SCALES and SCALES.index(scale) <= SCALES.index(PLAN_INFO[plan]['max_scale'])


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
    """-> (seed 32 bytes, public key 32 bytes). Seller side only (keygen_chart.py)."""
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
    """Sign a payload into a token. Needs the PRIVATE seed: only keygen_chart.py and tests call this."""
    body = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')
    msg = TOKEN_PREFIX.encode() + b'.' + body
    return f'{TOKEN_PREFIX}.{_b64e(body)}.{_b64e(sign(seed, msg))}'


def token_hash(token: str) -> str:
    """The id used in revocation lists (latest.json 'revoked'): SHA-256 of the token text."""
    return hashlib.sha256(token.strip().encode('utf-8')).hexdigest()


FEED_PREFIX = b'GC1-FEED.'


def _feed_body(latest):
    body = {k: v for k, v in latest.items() if k != 'sig'}
    return FEED_PREFIX + json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def sign_feed(seed: bytes, latest: dict) -> dict:
    """Seller side: add an Ed25519 'sig' to latest.json (version, files, hashes, revocations)."""
    out = dict(latest)
    out['sig'] = _b64e(sign(seed, _feed_body(out)))
    return out


def verify_feed(latest, pub=None) -> bool:
    """True only if latest.json carries a valid seller signature over everything else in it."""
    try:
        pubb = _pub_bytes(pub)
        return (isinstance(latest, dict) and len(pubb) == 32 and isinstance(latest.get('sig'), str)
                and verify(pubb, _feed_body(latest), _b64d(latest['sig'])))
    except Exception:
        return False


_VERIFY_CACHE = {}   # (pub, token) -> payload ; per process


def _pub_bytes(pub):
    if pub is None:
        pub = PUBLIC_KEY_HEX
    if isinstance(pub, str):
        pub = bytes.fromhex(pub)
    return pub


def parse_token(token: str, pub=None) -> dict:
    """Verify the signature and return the payload; raises LicenseError. Cached per process."""
    pubb = _pub_bytes(pub)
    if len(pubb) != 32:
        raise LicenseError('this copy of Stark Covenant has no seller key yet (run keygen_chart.py init)')
    if not isinstance(token, str):
        raise LicenseError('licence must be text')
    token = token.strip()
    key = (pubb, token)
    hit = _VERIFY_CACHE.get(key)
    if hit is not None:
        return dict(hit)
    parts = token.split('.')
    if len(parts) != 3 or parts[0] != TOKEN_PREFIX:
        raise LicenseError('this is not a Stark Covenant licence key')
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
    if not isinstance(payload, dict) or payload.get('app') != APP_ID or payload.get('plan') not in PLANS:
        raise LicenseError('the licence key is for a different product')
    scale = payload.get('scale', 'Individual')
    if not scale_allowed(payload['plan'], scale):
        raise LicenseError(f'scale "{scale}" is not available on the {plan_name(payload["plan"])} plan')
    if payload['plan'] == 'PRACTICE':
        if payload.get('clinicians') is not None:        # old per-clinician key: keep its original rules
            rng, n = LEGACY_CLINICIAN_RANGE, payload['clinicians']
            if not isinstance(n, int) or not rng[0] <= n <= rng[1]:
                raise LicenseError(f'the {plan_name("PRACTICE")} key must cover {rng[0]}-{rng[1]} clinicians')
            if int(payload.get('seats', 0)) > n * LEGACY_DEVICES_PER_CLINICIAN:
                raise LicenseError('the key allows more devices than its clinicians')
        else:
            seats = payload.get('seats')
            top = PLAN_INFO['PRACTICE']['max_seats']
            if not isinstance(seats, int) or not 1 <= seats <= top:
                raise LicenseError(f'the {plan_name("PRACTICE")} key must cover 1-{top} users')
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
    """Short code a buyer can read out to support (XXXX-XXXX-XXXX-XXXX)."""
    fp = fp or device_fingerprint()
    return '-'.join(fp[i:i + 4].upper() for i in range(0, 16, 4))


# --------------------------------------------------------------------------------------------------------
# Local state: per-install key, activation file, seats in the licence file, revocation cache
# --------------------------------------------------------------------------------------------------------
def default_state_dir():
    base = os.environ.get('LOCALAPPDATA') or os.path.join(os.path.expanduser('~'), '.local', 'share')
    return os.path.join(base, APP_ID, 'licence')


def default_license_path():
    return os.environ.get('COVENANT_LICENSE_FILE') or os.path.join(default_state_dir(), 'license.json')


def _atomic_write_json(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f'{path}.{os.getpid()}.tmp'
    data = json.dumps(obj, sort_keys=True, separators=(',', ':'))   # one C-encoded write (big seat lists)
    with open(tmp, 'w', encoding='utf-8') as f:
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
    return hashlib.sha256(b'GorgonCovenant/seats/v1' + token.strip().encode('utf-8')).digest()


def read_seats(license_path, token):
    """-> list of device fingerprints recorded in the licence file ([] if none, None if tampered)."""
    data = _read_json(license_path) or {}
    seats = data.get('seats') or []
    if data.get('token', '').strip() != token.strip():
        return []
    if not hmac.compare_digest(str(data.get('mac', '')), _mac(_seat_key(token), {'seats': seats})):
        return None
    return list(seats)


def write_license_file(license_path, token, seats):
    _atomic_write_json(license_path, {'token': token.strip(), 'seats': list(seats),
                                      'mac': _mac(_seat_key(token), {'seats': list(seats)})})


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
    """Store latest.json's 'revoked' list (only call with a feed whose signature was verified). -> set."""
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
    """dict with attribute access: ok, plan, plan_name, code, message, buyer, scale, seats, expires,
    days_left, devices."""
    __getattr__ = dict.get


_RECORDS_SAFE = ('Your patient records are safe and stay readable and exportable; Stark Covenant runs on '
                 'the Baby plan until this is fixed.')


def _status(ok, plan, code, message, **kw):
    kw.setdefault('scale', 'Individual' if plan == FREE_PLAN else None)
    kw.setdefault('seats', plan_devices(plan))
    return LicenseStatus(ok=ok, plan=plan, plan_name=plan_name(plan), code=code, message=message, **kw)


def _today(now):
    return _dt.datetime.fromtimestamp(now, _dt.timezone.utc).date()


def evaluate(token, *, now=None, fingerprint=None, revoked=None, pub=None, seats=None, last_seen=None):
    """Pure decision (no file access). -> LicenseStatus. Never raises for a bad token: returns Baby."""
    now = time.time() if now is None else now
    if not token:
        return _status(True, FREE_PLAN, 'free', f'{plan_name(FREE_PLAN)} ($0). Upgrade any time.')
    try:
        payload = parse_token(token, pub)
    except LicenseError as e:
        return _status(False, FREE_PLAN, 'invalid', f'Licence not accepted: {e}. {_RECORDS_SAFE}')
    plan = payload['plan']
    maxd = int(payload.get('seats', plan_devices(plan)))
    base = dict(buyer=payload.get('buyer'), expires=payload.get('exp'), payload=payload,
                scale=payload.get('scale', 'Individual'))
    if payload.get('clinicians') is not None:
        base['clinicians'] = payload['clinicians']      # per-clinician plans: the number of clinicians paid for
    if revoked and token_hash(token) in revoked:
        return _status(False, FREE_PLAN, 'revoked', 'This licence key has been withdrawn (refund, or a key that '
                       f'was shared publicly). {_RECORDS_SAFE} Contact support if this is a mistake.', **base)
    if last_seen and now < last_seen - CLOCK_SKEW_DAYS * 86400:
        return _status(False, FREE_PLAN, 'clock', 'Your PC clock seems to have moved backwards. Set the correct '
                       f'date and time, then restart {APP_NAME}. {_RECORDS_SAFE}', **base)
    exp = payload.get('exp')
    grace_days = int(payload.get('grace_days', DEFAULT_GRACE_DAYS))
    days_left = None
    if exp:
        try:
            exp_date = _dt.date.fromisoformat(exp)
        except ValueError:
            return _status(False, FREE_PLAN, 'invalid', f'Licence not accepted: bad expiry date. {_RECORDS_SAFE}',
                           **base)
        days_left = (exp_date - _today(now)).days
        if days_left < -grace_days:
            return _status(False, FREE_PLAN, 'expired', f'Your {plan_name(plan)} plan ended on {exp}. Renew to '
                           f'get it back. {_RECORDS_SAFE}', days_left=days_left, **base)
    elif plan != FREE_PLAN and is_perpetual(plan):
        major = payload.get('major')
        if not isinstance(major, int) or major < 1:
            return _status(False, FREE_PLAN, 'invalid', f'Licence not accepted: a one-time licence must name the '
                           f'version it covers. {_RECORDS_SAFE}', **base)
        base['major'] = major
        if major < APP_MAJOR:
            return _status(False, FREE_PLAN, 'upgrade', f'Your {plan_name(plan)} licence covers Stark Covenant '
                           f'{major}.x, which keeps working with it for good. This is version {APP_MAJOR}: owners '
                           f'can upgrade at {int(UPGRADE_DISCOUNT * 100)}% off. {_RECORDS_SAFE}', **base)
    elif plan != FREE_PLAN:
        return _status(False, FREE_PLAN, 'invalid', f'Licence not accepted: a paid plan needs an expiry date. '
                       f'{_RECORDS_SAFE}', **base)
    bound = payload.get('devices')
    if fingerprint is not None:
        if bound and fingerprint not in bound:
            return _status(False, FREE_PLAN, 'device', 'This licence key was issued for other devices. Send your '
                           f'device code ({device_code(fingerprint)}) to support to move it.', **base)
        if seats is not None and fingerprint not in seats and len(seats) >= maxd:
            return _status(False, FREE_PLAN, 'seats', f'This licence is already active on {len(seats)} devices '
                           f'(the {plan_name(plan)} plan allows {maxd}). Deactivate it on one of them '
                           '(Licence screen > Deactivate this device), then try again.',
                           devices=len(seats), **base)
    if days_left is not None and days_left < 0:
        return _status(True, plan, 'grace', f'{plan_name(plan)} renewal is due (ended {exp}). The plan stays on '
                       f'for {grace_days + days_left} more day(s) while offline.', days_left=days_left,
                       seats=maxd, **base)
    when = f' until {exp}.' if exp else (f' (one-time licence, every {payload["major"]}.x update).'
                                         if payload.get('major') else '.')
    return _status(True, plan, 'ok', f'{plan_name(plan)} licence active' + when,
                   days_left=days_left, seats=maxd, **base)


def activate(token, license_path, *, state_dir=None, fingerprint=None, now=None, pub=None, revoked=None):
    """Check a token on this device and take a seat for it (up to the plan's seats). -> LicenseStatus."""
    state_dir = state_dir or default_state_dir()
    fp = fingerprint or device_fingerprint()
    now = time.time() if now is None else now
    if revoked is None:
        revoked = cached_revocations(state_dir)
    seats = read_seats(license_path, token) if token else []
    if seats is None:
        return _status(False, FREE_PLAN, 'tampered', 'The licence file was edited by hand and cannot be trusted. '
                       f'Paste your licence key again. {_RECORDS_SAFE}')
    act = load_activation(state_dir)
    last_seen = act.get('last_seen') if act and act.get('token_hash') == token_hash(token or '') else None
    st = evaluate(token, now=now, fingerprint=fp, revoked=revoked, pub=pub, seats=seats, last_seen=last_seen)
    if not st.ok or not token:
        return st
    if fp not in seats:
        seats.append(fp)
        write_license_file(license_path, token, seats)
    save_activation(state_dir, {'token_hash': token_hash(token), 'fingerprint': fp,
                                'activated_at': (act or {}).get('activated_at', int(now)),
                                'last_seen': max(int(now), int(last_seen or 0))})
    st['devices'] = len(seats)
    return st


def deactivate(token, license_path, *, state_dir=None, fingerprint=None):
    """Give this device's seat back. -> remaining seat count."""
    fp = fingerprint or device_fingerprint()
    seats = read_seats(license_path, token) or []
    seats = [s for s in seats if s != fp]
    write_license_file(license_path, token, seats)
    try:
        os.remove(os.path.join(state_dir or default_state_dir(), 'activation.json'))
    except OSError:
        pass
    return len(seats)


def check(license_path=None, *, state_dir=None, fingerprint=None, now=None, pub=None):
    """App start-up check: read the licence file and evaluate it. -> LicenseStatus (Baby if no file)."""
    license_path = license_path or default_license_path()
    data = _read_json(license_path) or {}
    token = data.get('token', '')
    if not token:
        return evaluate('')
    return activate(token, license_path, state_dir=state_dir, fingerprint=fingerprint, now=now, pub=pub)


def plans_table():
    """For the app's 'Plans' screen: list of dicts in display order."""
    return [{'plan': k, 'name': v['name'], 'price': plan_price(k), 'seats': v['seats'], 'devices': plan_devices(k),
             'max_scale': v['max_scale'], 'for_sale': for_sale(k)} for k, v in PLAN_INFO.items()]


def benchmark(n_cached=1000):
    """Honest timings on this machine: first (real) verification vs cached checks. -> dict of ms."""
    seed, pub = generate_keypair()
    tok = make_token(seed, {'app': APP_ID, 'plan': 'CLINIC', 'buyer': 'bench', 'exp': '2099-01-01',
                            'scale': 'Clinic'})
    clear_cache()
    t = time.perf_counter()
    parse_token(tok, pub)
    first = (time.perf_counter() - t) * 1000
    t = time.perf_counter()
    for _ in range(n_cached):
        parse_token(tok, pub)
    cached = (time.perf_counter() - t) * 1000 / n_cached
    clear_cache()
    return {'first_verify_ms': round(first, 3), 'cached_check_ms': round(cached, 5)}


if pricing_proposal_active():      # approved 2026-11-19 (PRICING_PROPOSAL_APPROVED); old plans stay valid
    apply_pricing_proposal()
else:
    restore_original_catalogue()


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='Stark Covenant licence tools (buyer side)')
    ap.add_argument('cmd', choices=['device-code', 'device-id', 'check', 'bench', 'activate', 'deactivate', 'plans'])
    ap.add_argument('token', nargs='?', help='activate: the licence key you received')
    ap.add_argument('--license', default=None)
    a = ap.parse_args()
    if a.cmd == 'device-code':
        print(device_code())
    elif a.cmd == 'device-id':
        print(device_fingerprint())
    elif a.cmd == 'bench':
        print(json.dumps(benchmark(), indent=1))
    elif a.cmd == 'plans':
        for p in plans_table():
            print(f"{p['name']:<22} {p['price']:<24} {p['devices']:>5} devices  up to {p['max_scale']}")
    elif a.cmd == 'activate':
        if not a.token:
            raise SystemExit('usage: license_guard.py activate GC1.xxxxx.yyyyy')
        st = activate(a.token, a.license or default_license_path())
        print(st.plan_name, '-', st.message)
    elif a.cmd == 'deactivate':
        data = _read_json(a.license or default_license_path()) or {}
        if data.get('token'):
            print(deactivate(data['token'], a.license or default_license_path()), 'device(s) still active')
    else:
        st = check(a.license)
        print(st.plan_name, '-', st.message)
