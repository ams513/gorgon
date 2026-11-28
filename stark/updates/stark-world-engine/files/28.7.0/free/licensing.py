"""licensing.py: offline license keys for Stark World Engine (verify side only - safe to ship).

A key is ONE line:  GS1-<payload>.<signature>   (both base64url; payload = {"b": buyer id, "t": tier, "d": date})
signed with the seller's Ed25519 PRIVATE key (kept by keygen.py, never shipped). This file holds only the PUBLIC key.

Friendly by design:
  * No key, a wrong key or a damaged key never stops the product: it runs as "unregistered" (Free features).
  * A valid Pro/Studio key unlocks the Godot 4 / UE5 exporters. Nothing else is locked.
  * No server, no internet, no account, no phone-home: verification is pure math on this computer.
  * Activate by pasting the key into the studio's Activate box, or by saving it as key.txt next to studio_app.py.
This raises the effort needed to share a copy; it is not (and does not pretend to be) unbreakable copy protection.
"""
import base64, hashlib, json, os, re

PUBLIC_KEY_HEX = '032da19024c78dfc1319030d3b0096e4660e836c76104e4432580be14ac30750'
KEY_FILE = 'key.txt'
TIERS = ('standard', 'pro', 'studio')   # what a signed key can unlock
FREE = 'free'                           # no key / bad key: the Free edition (everything except the export limits)
KEY_RX = re.compile(r'GS1-([A-Za-z0-9_-]+)\.([A-Za-z0-9_-]+)')
# Store plan IDs a key may name ("p"). New keys carry the Stark IDs; keys issued before 28.7 may carry the old IDs,
# which stay valid (read-only aliases). Keys without "p" (every key issued so far) are unaffected.
PLAN_IDS = {'standard': 'stark-world-engine', 'pro': 'stark-world-engine/pro', 'studio': 'stark-world-engine/studio'}
LEGACY_PLAN_IDS = {'gorgon-studios': 'stark-world-engine', 'gorgon-studios/pro': 'stark-world-engine/pro',
                   'gorgon-studios/studio': 'stark-world-engine/studio'}


def plan_tier(plan_id):
    """'stark-world-engine/pro' (or its pre-28.7 alias 'gorgon-studios/pro') -> 'pro'; unknown -> None."""
    pid = str(plan_id or '').strip().lower()
    pid = LEGACY_PLAN_IDS.get(pid, pid)
    return next((t for t, p in PLAN_IDS.items() if p == pid), None)

# ---------------- Ed25519 verify (RFC 8032), pure Python; `cryptography` is used when installed ----------------
_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_I = pow(2, (_P - 1) // 4, _P)


def _recover_x(y, sign):
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * _I % _P
    if (x * x - x2) % _P:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_GY = 4 * pow(5, _P - 2, _P) % _P
_GX = _recover_x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % _P)


def _add(p, q):
    a = (p[1] - p[0]) * (q[1] - q[0]) % _P
    b = (p[1] + p[0]) * (q[1] + q[0]) % _P
    c = 2 * p[3] * q[3] * _D % _P
    d = 2 * p[2] * q[2] % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(s, p):
    q = (0, 1, 1, 0)
    while s:
        if s & 1:
            q = _add(q, p)
        p = _add(p, p)
        s >>= 1
    return q


def _decompress(b):
    if len(b) != 32:
        return None
    y = int.from_bytes(b, 'little')
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _P)


def _equal(p, q):
    return (p[0] * q[2] - q[0] * p[2]) % _P == 0 and (p[1] * q[2] - q[1] * p[2]) % _P == 0


def ed25519_verify_py(pub, msg, sig):
    if len(sig) != 64:
        return False
    a, r = _decompress(pub), _decompress(sig[:32])
    s = int.from_bytes(sig[32:], 'little')
    if a is None or r is None or s >= _L:
        return False
    h = int.from_bytes(hashlib.sha512(sig[:32] + pub + msg).digest(), 'little') % _L
    return _equal(_mul(s, _G), _add(r, _mul(h, a)))


def ed25519_verify(pub, msg, sig):
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.exceptions import InvalidSignature
    except ImportError:
        return ed25519_verify_py(pub, msg, sig)
    try:
        Ed25519PublicKey.from_public_bytes(pub).verify(sig, msg)
        return True
    except (InvalidSignature, ValueError):
        return False


# ---------------- keys ----------------
def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b'=').decode()


def unb64u(s):
    return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))


MAX_WRAP_PIECES = 8      # a key split over at most this many extra whitespace-separated pieces is rejoined


def find_key_token(text):
    """-> (token, None) or (None, reason). Pasted text may carry words, quotes, whitespace or line breaks around the
    key ("Your key: GS1-... thanks!"): the key is looked for in the text as written, so neighbouring words never glue
    onto it. Two or more DIFFERENT keys are refused (no guessing which one was meant)."""
    tokens = list(dict.fromkeys(m.group(0) for m in KEY_RX.finditer(str(text or ''))))
    if not tokens:
        return None, 'no Stark World Engine key found (it starts with GS1-)'
    if len(tokens) > 1:
        return None, 'more than one different key was pasted (paste just one GS1- key)'
    return tokens[0], None


def _wrapped_candidates(text):
    """Keys an e-mail client wrapped over lines: from every piece holding 'GS1-', join it with the next 1..8
    whitespace-separated pieces and yield each GS1- token that forms. Each candidate still has to pass the full
    signature check, so this only changes which text is checked, never how strictly."""
    pieces = str(text or '').split()
    for i, piece in enumerate(pieces):
        if 'GS1-' not in piece:
            continue
        acc = piece
        for nxt in pieces[i + 1:i + 1 + MAX_WRAP_PIECES]:
            acc += nxt
            m = KEY_RX.search(acc)
            if m:
                yield m.group(0)


def _verify_token(token, pub_hex):
    m = KEY_RX.fullmatch(token)
    if not m:
        return False, 'no Stark World Engine key found (it starts with GS1-)'
    try:
        payload_b, sig = unb64u(m.group(1)), unb64u(m.group(2))
        ok = ed25519_verify(bytes.fromhex(pub_hex), b'GS1|' + payload_b, sig)
        payload = json.loads(payload_b)
    except (ValueError, TypeError):
        return False, 'the key is damaged (copy it again in one piece)'
    if not ok:
        return False, 'the key signature does not match (typo or edited key)'
    if not isinstance(payload, dict) or payload.get('t') not in TIERS or not payload.get('b'):
        return False, 'the key is for an unknown edition'
    if 'p' in payload:
        if plan_tier(payload['p']) != payload['t']:
            return False, 'the key is for an unknown edition'
        payload = dict(payload, p=PLAN_IDS[payload['t']])    # an old plan ID reads as its Stark name
    return True, payload


def _verify(text, pub_hex=None):
    """-> (ok, payload_or_reason, token). The Ed25519 check is the same strict one for every path."""
    pub_hex = pub_hex or PUBLIC_KEY_HEX
    if set(pub_hex) == {'0'}:
        return False, 'this build has no public key yet (run keygen.py --init)', None
    token, why = find_key_token(text)
    if token is None and why != 'no Stark World Engine key found (it starts with GS1-)':
        return False, why, None                          # conflicting keys: refuse, never guess
    if token is not None:
        ok, info = _verify_token(token, pub_hex)
        if ok:
            return True, info, token
        why = info
    # Nothing valid as written: maybe the key was wrapped over lines. Only candidates that verify count, and two
    # different valid ones are refused just like two keys pasted side by side.
    good = {}
    for cand in dict.fromkeys(_wrapped_candidates(text)):
        if cand != token:
            ok, info = _verify_token(cand, pub_hex)
            if ok:
                good[cand] = info
    if len(good) == 1:
        cand, info = next(iter(good.items()))
        return True, info, cand
    if len(good) > 1:
        return False, 'more than one different key was pasted (paste just one GS1- key)', None
    return False, why, None


def verify_key(text, pub_hex=None):
    """-> (True, payload) or (False, reason). Never raises."""
    try:
        ok, info, _ = _verify(text, pub_hex)
    except Exception:  # noqa: BLE001  (contract: never raises)
        return False, 'the key is damaged (copy it again in one piece)'
    return ok, info


def _decode_key_file(raw):
    """key.txt bytes -> text. Notepad's "Unicode" (UTF-16 + BOM) and UTF-8 with or without BOM are read as such;
    anything else is decoded leniently (the key itself is pure ASCII), so a stray byte never raises."""
    if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
        return raw.decode('utf-16', 'replace')
    if len(raw) >= 4 and raw[1:4:2] == b'\0\0' and raw[0:1] != b'\0':      # UTF-16-LE without a BOM
        return raw.decode('utf-16-le', 'replace')
    return raw.decode('utf-8-sig', 'replace')


def load_license(app_dir):
    """-> {'registered', 'tier', 'buyer', 'issued', 'reason'} from key.txt next to the app (absent = unregistered)."""
    p = os.path.join(app_dir, KEY_FILE)
    if not os.path.isfile(p):
        return {'registered': False, 'tier': FREE, 'buyer': None, 'reason': 'Free edition (no key.txt)'}
    try:
        with open(p, 'rb') as f:
            text = _decode_key_file(f.read(8192))
    except OSError as e:
        return {'registered': False, 'tier': FREE, 'buyer': None, 'reason': f'key.txt unreadable: {e}'}
    ok, info = verify_key(text)
    if not ok:
        return {'registered': False, 'tier': FREE, 'buyer': None, 'reason': info}
    return {'registered': True, 'tier': info['t'], 'buyer': info['b'], 'issued': info.get('d'), 'reason': 'valid key'}


def save_key(app_dir, text):
    """Verify first; only a valid key (the bare GS1- token, nothing around it) is written to key.txt.
    -> license dict or raise ValueError."""
    ok, info, token = _verify(text)
    if not ok:
        raise ValueError(info)
    with open(os.path.join(app_dir, KEY_FILE), 'w', encoding='utf-8') as f:
        f.write(token + '\n')
    return load_license(app_dir)


def minify_js(js):
    """Conservative minifier: drops full-line // comments, indentation and blank lines (newlines kept, so automatic
    semicolon insertion behaves exactly as before)."""
    out = []
    for line in js.splitlines():
        s = line.strip()
        if not s or s.startswith('//'):
            continue
        out.append(s)
    return '\n'.join(out)
