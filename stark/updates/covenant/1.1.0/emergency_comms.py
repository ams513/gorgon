"""Stark Covenant emergency & communications module (standard library only, Python 3.10+).

SAFETY (shown in the UI and the README wherever it matters):
    This does not replace calling your local emergency number directly. A connection cannot be guaranteed
    (no signal, no credit, no internet, network outages).
    Nothing here summons an ambulance automatically. Every call and every SMS is placed by a person pressing
    a button on their own phone (tel: / sms: links). Partner clinics are never an emergency route: an emergency
    always goes to the LOCAL emergency number of the country the user selected.

What lives here
  1. Emergency numbers  emergency_numbers.json (ISO 3166-1, per-number 'verified' flags, sources cited);
                        validate_numbers(), reconcile_with_itu(), fetch_itu_e129() to refresh against ITU-T E.129.
  2. Family alert       contact + group lists (encrypted with security_vault when it is present and unlocked;
                        otherwise the server refuses to store them and the page keeps them on that device only),
                        build_alert_message() / sms_uri() - the page opens the phone's SMS app, never sends itself.
  3. Clinic calls       a WebSocket (RFC 6455) signaling hub written with the standard library, HMAC
                        challenge-response per clinic, relaying WebRTC offer/answer/candidates between Covenant
                        installations; a partner directory the clinic fills in itself (no built-in directory).
  4. HTTP routes        CommsModule.handle(handler) serves /comms/... from any http.server handler (see
                        EMERGENCY_README.md for the exact mount code); `python emergency_comms.py serve` runs it
                        alone on 127.0.0.1.

CLI
  python emergency_comms.py serve [--host 127.0.0.1] [--port 8765] [--data DIR]
  python emergency_comms.py add-clinic ID "Display name"      -> prints the key to give that clinic (once)
  python emergency_comms.py list-clinics | remove-clinic ID
  python emergency_comms.py validate-numbers
  python emergency_comms.py fetch-itu OUT.json                -> downloads the ITU-T E.129 table (network)
  python emergency_comms.py reconcile-itu ROWS.json           -> re-derives every 'verified' flag from it
"""
from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import hmac
import html as _html
import http.server
import importlib
import json
import os
import re
import secrets
import socket
import ssl
import struct
import sys
import threading
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
NUMBERS_PATH = os.path.join(HERE, 'emergency_numbers.json')
UI_PATH = os.path.join(HERE, 'comms_ui.html')
PREFIX = '/comms'
LOG_PREFIX = '[EmergencyComms]'

SAFETY_NOTICE = ('This does not replace calling your local emergency number directly. A connection cannot be '
                 'guaranteed (no signal, no credit, no internet, network outages).')
NO_AUTO_SUMMON = ('Nothing here calls an ambulance by itself. You press the button, your phone places the call.')
PARTNER_NOT_EMERGENCY = ('Partner clinic calls are for referrals and advice. They are not an emergency line. '
                         'In an emergency call your local emergency number.')

DEFAULT_STUN = 'stun:stun.l.google.com:19302'
STUN_NOTE = ('stun:stun.l.google.com:19302 is a free public STUN server run by Google (a third party). It only '
             'tells each browser its public address; no audio or video passes through it. Replace or remove it '
             'in Settings if your clinic policy requires.')
TURN_NOTE = ('Behind strict NATs, carrier-grade NAT or firewalls that block UDP, two browsers often cannot reach '
             'each other directly. A TURN server relays the (still DTLS-SRTP encrypted) media. Run your own (for '
             'example coturn) and enter turn:/turns: URL, username and credential here.')

SERVICES = ('general', 'ambulance', 'police', 'fire')
NUMBER_RE = re.compile(r'^[0-9]{2,6}$')

# 112 must reach emergency services free of charge in every EU Member State (Directive (EU) 2018/1972 art. 109).
# EU outermost regions and Aland are EU territory. (Overseas countries/territories such as NC/PF/WF/BL/PM are not.)
EU_112 = frozenset('AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE '
                   'GF GP MQ RE YT MF AX'.split())
# Territories inside the parent's national numbering plan: checked against the parent's ITU-T E.129 entry.
ITU_PARENT = {'AX': 'FI', 'SJ': 'NO', 'GG': 'GB', 'JE': 'GB', 'IM': 'GB', 'CC': 'AU', 'CX': 'AU',
              'GF': 'FR', 'GP': 'FR', 'MQ': 'FR', 'RE': 'FR', 'YT': 'FR', 'BL': 'FR', 'MF': 'FR', 'PM': 'FR',
              'PR': 'US', 'VI': 'US', 'GU': 'US', 'AS': 'US', 'MP': 'US'}
NO_SERVICE = frozenset('AQ BV HM GS TF UM'.split())     # uninhabited / stations only: no public network

ITU_E129_URL = 'https://www.itu.int/net/itu-t/inrdb/e129_important_numbers.aspx'
ITU_CORE_CATEGORIES = ('Emergency', 'Medical', 'Police', 'Fire')


def log(msg):
    if not os.environ.get('GORGON_COMMS_QUIET'):
        sys.stderr.write(f'{LOG_PREFIX} {msg}\n')


# ====================================================================================== emergency numbers
_numbers_cache = {}


def load_numbers(path=None):
    path = path or NUMBERS_PATH
    st = os.stat(path)
    hit = _numbers_cache.get(path)
    if hit and hit[0] == st.st_mtime_ns:
        return hit[1]
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    _numbers_cache[path] = (st.st_mtime_ns, data)
    return data


def country_entry(cc, data=None):
    data = data or load_numbers()
    return data['countries'].get((cc or '').upper())


def numbers_list(entry):
    """Every dialable number of one country entry, in display order, without repeats."""
    out = []
    for svc in SERVICES:
        for n in entry.get(svc, []):
            if n['number'] not in out:
                out.append(n['number'])
    return out


def dial_plan(cc, data=None):
    """What the emergency screen shows for one country: buttons, fallback and the warnings that go with them."""
    e = country_entry(cc, data)
    if e is None:
        return None
    buttons = []
    for svc in SERVICES:
        for n in e.get(svc, []):
            buttons.append({'service': svc, 'number': n['number'], 'verified': bool(n.get('verified')),
                            'tel': 'tel:' + n['number']})
    warnings = [SAFETY_NOTICE]
    if not e['verified']:
        warnings.append('These numbers are NOT verified. Check the local emergency number now (ask someone '
                        'nearby, a hotel, a pharmacy, or your mobile operator).')
    fallback = None
    if e['gsm_112'] != 'none' and '112' not in numbers_list(e):
        fallback = {'number': '112', 'tel': 'tel:112',
                    'note': 'Only from a mobile phone. Most mobile phones treat 112 as an emergency number, but '
                            'it may not connect to local services in this country. Try the local number first.'}
    if e['gsm_112'] == 'none':
        warnings.append('There is no public emergency service here. Use your station or expedition emergency plan.')
    return {'country': e['name'], 'cc': cc.upper(), 'verified': e['verified'], 'status': e['status'],
            'source': e['source'], 'buttons': buttons, 'fallback_112': fallback, 'warnings': warnings,
            'note': e.get('note', '')}


def validate_numbers(data, iso_codes=None):
    """-> list of problems (empty = good). Checks schema, garbage, duplicates, flags and ISO coverage."""
    problems = []
    if data.get('schema') != 'gorgon-covenant/emergency-numbers/1':
        problems.append('schema id missing or wrong')
    srcs = data.get('sources', {})
    for key in ('itu_e129', 'eu_eecc_art109', '3gpp_ts_22_101'):
        if not srcs.get(key, {}).get('url', '').startswith('https://'):
            problems.append(f'source {key} has no https url')
    countries = data.get('countries', {})
    if iso_codes is not None:
        missing = sorted(set(iso_codes) - set(countries))
        if missing:
            problems.append(f'missing ISO 3166-1 codes: {missing}')
    for cc, e in countries.items():
        where = f'{cc}:'
        if not re.fullmatch(r'[A-Z]{2}', cc):
            problems.append(f'{where} bad code')
        for k in ('name', 'verified', 'status', 'source', 'gsm_112'):
            if k not in e:
                problems.append(f'{where} missing {k}')
        if not isinstance(e.get('verified'), bool):
            problems.append(f'{where} verified must be true/false')
        if e.get('gsm_112') not in ('national', 'mobile_handset', 'none'):
            problems.append(f'{where} bad gsm_112')
        all_ok = True
        count = 0
        for svc in SERVICES:
            seen = set()
            lst = e.get(svc, [])
            if not isinstance(lst, list):
                problems.append(f'{where} {svc} not a list')
                continue
            for n in lst:
                num = n.get('number', '')
                count += 1
                if not NUMBER_RE.match(num):
                    problems.append(f'{where} garbage number {num!r} in {svc}')
                if num in seen:
                    problems.append(f'{where} duplicate {num} in {svc}')
                seen.add(num)
                if not isinstance(n.get('verified'), bool):
                    problems.append(f'{where} {num} verified flag missing')
                if n.get('verified') and n.get('source') not in srcs:
                    problems.append(f'{where} {num} verified without a known source')
                all_ok = all_ok and bool(n.get('verified'))
        if e.get('verified') and not (all_ok and count):
            problems.append(f'{where} marked verified but has unverified or no numbers')
        want = 'verified' if (all_ok and count) else ('unverified' if not any(
            n.get('verified') for s in SERVICES for n in e.get(s, [])) else 'partially_verified')
        if e.get('status') != want:
            problems.append(f'{where} status {e.get("status")} should be {want}')
        has112 = any(n['number'] == '112' and n.get('verified') for s in SERVICES for n in e.get(s, []))
        if e.get('gsm_112') == 'national' and not has112:
            problems.append(f'{where} gsm_112 national without a verified 112')
    return problems


# Which ITU-T E.129 service categories may confirm a number shown under each of our services.
_RESCUE_RE = re.compile(r'(?i)civil|protec|rescue|emergenc|secours|urgenc|ambulan|fire|pompier|samu')
_SVC_CATS = {'general': ('Emergency', 'Medical', 'Police', 'Fire'), 'ambulance': ('Medical', 'Emergency'),
             'police': ('Police', 'Emergency'), 'fire': ('Fire', 'Emergency')}


def _itu_tokens(rows, svc):
    """Digit strings the ITU rows list for one of our services ('Other' rows count only for ambulance/fire
    when their description is about rescue / civil protection)."""
    toks = set()
    for r in rows:
        ok = r[2] in _SVC_CATS[svc] or (svc in ('ambulance', 'fire') and r[2] == 'Other'
                                         and _RESCUE_RE.search(r[3] or ''))
        if ok:
            toks.update(re.findall(r'\d+', r[1]))
    return toks


def reconcile_with_itu(data, itu_rows, retrieved=None):
    """Re-derive every per-number 'verified' flag from ITU-T E.129 rows ([country, number, category, info,
    allocated, note, last_update]) plus EU law for 112. Mutates and returns data. Never adds numbers."""
    by_name = {}
    for r in itu_rows:
        by_name.setdefault(r[0], []).append(r)
    name_of = {cc: e.get('itu_name') for cc, e in data['countries'].items()}
    for cc, e in data['countries'].items():
        own = by_name.get(name_of.get(cc) or '', [])
        parent = ITU_PARENT.get(cc)
        prow = by_name.get(name_of.get(parent) or '', []) if parent and not own else []
        rows = own or prow
        confirmed = {c['number'] for c in e.get('confirmations', []) if c.get('url', '').startswith('https://')}
        all_ok, count, any_ok = True, 0, False
        for svc in SERVICES:
            toks = _itu_tokens(rows, svc)
            for n in e.get(svc, []):
                count += 1
                if n['number'] in toks:
                    n['verified'], n['source'] = True, 'itu_e129'
                elif n['number'] in confirmed:
                    n['verified'], n['source'] = True, 'national_confirmation'
                elif n['number'] == '112' and cc in EU_112:
                    n['verified'], n['source'] = True, 'eu_eecc_art109'
                else:
                    n['verified'], n['source'] = False, 'curated'
                all_ok = all_ok and n['verified']
                any_ok = any_ok or n['verified']
        e['verified'] = bool(all_ok and count)
        e['status'] = 'verified' if e['verified'] else ('partially_verified' if any_ok else 'unverified')
        core = [[r[1], r[2], r[3]] for r in rows if r[2] in ITU_CORE_CATEGORIES
                or (r[2] == 'Other' and _RESCUE_RE.search(r[3] or ''))]
        e['itu_e129'] = core
        dates = sorted(r[6] for r in rows if re.fullmatch(r'\d{4}-\d{2}-\d{2}', r[6]))
        e['itu_last_update'] = dates[-1] if dates else None
        e['itu_via_parent'] = parent if (prow and not own) else None
        if rows:
            label = name_of.get(parent) if e['itu_via_parent'] else name_of.get(cc)
            e['source'] = (f'ITU-T E.129 entry for {label} (last update {e["itu_last_update"]})'
                           + (', parent numbering plan' if e['itu_via_parent'] else ''))
            if cc in EU_112:
                e['source'] += '; 112 under Directive (EU) 2018/1972 art. 109'
        elif cc in EU_112:
            e['source'] = 'Directive (EU) 2018/1972 art. 109 (112 only); other numbers curated, not cross-checked'
        elif confirmed:
            e['source'] = 'national government page: ' + ', '.join(
                sorted({c['url'] for c in e.get('confirmations', [])}))
        else:
            e['source'] = 'curated by the module authors; NOT cross-checked against an ITU or regulator list'
        has112 = any(n['number'] == '112' and n['verified'] for s in SERVICES for n in e.get(s, []))
        e['gsm_112'] = 'none' if cc in NO_SERVICE else ('national' if has112 else 'mobile_handset')
    if retrieved:
        data['sources']['itu_e129']['retrieved'] = retrieved
        data['sources']['itu_e129']['rows'] = len(itu_rows)
    return data


def fetch_itu_e129(timeout=60):
    """Download the whole ITU-T E.129 table (ASP.NET GridView, 50 rows per page). Network; stdlib only."""
    import http.cookiejar
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def get(form=None):
        body = urllib.parse.urlencode(form).encode() if form else None
        req = urllib.request.Request(ITU_E129_URL, data=body, headers={
            'User-Agent': 'StarkCovenant-emergency_comms/1', 'Content-Type': 'application/x-www-form-urlencoded'})
        return opener.open(req, timeout=timeout).read().decode('utf-8', 'replace')

    def form(s):
        f = {'__LASTFOCUS': '', 'ctl00$ContentPlaceHolder1$DDL_countries': '0',
             'ctl00$ContentPlaceHolder1$ddl_page_size': '50'}
        for n in ('__VIEWSTATE', '__VIEWSTATEGENERATOR', '__EVENTVALIDATION'):
            m = re.search(r'name="%s" id="%s" value="([^"]*)"' % (n, n), s)
            f[n] = _html.unescape(m.group(1)) if m else ''
        return f

    def rows(s):
        g = s[s.find('id="ContentPlaceHolder1_GridView1"'):]
        out = []
        for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', g, re.S):
            c = [_html.unescape(re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', x))).strip()
                 for x in re.findall(r'<td[^>]*>(.*?)</td>', tr, re.S)]
            if len(c) == 7:
                out.append(c)
        return out

    s = get()
    total = int((re.search(r'Total found:\s*(?:<[^>]+>\s*)*(\d+)', s) or [0, 0])[1])
    allr, page = rows(s), 2
    while len(allr) < total and page < 200:
        f = form(s)
        f.update({'__EVENTTARGET': 'ctl00$ContentPlaceHolder1$GridView1', '__EVENTARGUMENT': f'Page${page}'})
        s = get(f)
        got = rows(s)
        if not got:
            break
        allr += got
        page += 1
        time.sleep(0.3)
    return allr


# ====================================================================================== family alert helpers
PHONE_RE = re.compile(r'^\+?[0-9]{3,15}$')


def clean_phone(p):
    p = re.sub(r'[\s\-().]', '', str(p or ''))
    return p if PHONE_RE.match(p) else None


def build_alert_message(sender='', lat=None, lon=None, accuracy_m=None, note=''):
    """Plain SMS text. Location only when the user granted it (lat/lon given)."""
    who = (sender or '').strip()[:60]
    parts = [f'HELP NEEDED{(" - " + who) if who else ""}.']
    if note:
        parts.append(str(note).strip()[:200])
    if lat is not None and lon is not None:
        lat, lon = float(lat), float(lon)
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError('coordinates out of range')
        acc = f' (within about {int(round(float(accuracy_m)))} m)' if accuracy_m is not None else ''
        parts.append(f'My location: {lat:.5f}, {lon:.5f}{acc} https://www.openstreetmap.org/?mlat={lat:.5f}'
                     f'&mlon={lon:.5f}#map=17/{lat:.5f}/{lon:.5f}')
    else:
        parts.append('Location not shared.')
    parts.append('Please call me. If I do not answer, call the emergency number where I am.')
    return ' '.join(parts)


def sms_uri(numbers, body):
    """sms: link with several recipients. '?&body=' is understood by both Android and iOS Messages."""
    nums = [n for n in (clean_phone(x) for x in numbers) if n]
    if not nums:
        raise ValueError('no valid phone numbers')
    return 'sms:' + ','.join(nums) + '?&body=' + urllib.parse.quote(body, safe='')


# ====================================================================================== storage
class StorageUnavailable(Exception):
    pass


class CommsStore:
    """Small JSON documents. With an unlocked security_vault.Vault the files are AES-256-GCM blobs (aad binds
    each blob to its document name); without one, documents marked sensitive are refused and the rest are
    written as plain JSON (status() says so)."""
    SENSITIVE = {'contacts': True, 'directory': True, 'clinics': False, 'settings': False}

    def __init__(self, root, vault=None):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)
        self.vault = vault
        self._lock = threading.Lock()

    def encrypted(self):
        """Works with a security_vault.Vault (has .unlocked) and with clinic_app's VaultAdapter (no .unlocked:
        it only exists while the vault is open)."""
        if self.vault is None:
            return False
        try:
            return bool(getattr(self.vault, 'unlocked', True))
        except Exception:
            return False

    # The document name is bound to every blob twice: as GCM additional data when the vault's encrypt() takes
    # aad (security_vault.Vault), and always inside the plaintext header (clinic_app's VaultAdapter has no aad).
    def _seal(self, name, raw):
        aad = b'gorgon-comms:' + name.encode()
        pt = b'GCC1:' + name.encode() + b'\n' + raw
        try:
            return self.vault.encrypt(pt, aad=aad)
        except TypeError:
            return self.vault.encrypt(pt)

    def _open(self, name, blob):
        aad = b'gorgon-comms:' + name.encode()
        try:
            pt = self.vault.decrypt(blob, aad=aad)
        except TypeError:
            pt = self.vault.decrypt(blob)
        head = b'GCC1:' + name.encode() + b'\n'
        if not bytes(pt).startswith(head):
            raise StorageUnavailable(f'{name}: encrypted file belongs to another document')
        return bytes(pt)[len(head):]

    def status(self):
        return {'encrypted': self.encrypted(), 'vault_present': self.vault is not None,
                'contacts_storage': 'server-encrypted' if self.encrypted() else 'browser-only',
                'root': self.root}

    def _path(self, name, enc):
        return os.path.join(self.root, f'{name}.json' + ('.gcv' if enc else ''))

    def load(self, name, default):
        with self._lock:
            pe, pp = self._path(name, True), self._path(name, False)
            if os.path.exists(pe):
                if not self.encrypted():
                    raise StorageUnavailable(f'{name} is encrypted and the vault is locked')
                with open(pe, 'rb') as f:
                    return json.loads(self._open(name, f.read()))
            if os.path.exists(pp):   # plain file (written while no vault); re-saved encrypted on next save
                with open(pp, encoding='utf-8') as f:
                    return json.load(f)
            return default

    def save(self, name, obj):
        raw = json.dumps(obj, ensure_ascii=False, sort_keys=True).encode('utf-8')
        with self._lock:
            if self.encrypted():
                blob = self._seal(name, raw)
                self._atomic(self._path(name, True), blob)
                pp = self._path(name, False)
                if os.path.exists(pp):
                    os.remove(pp)
                return 'encrypted'
            if self.SENSITIVE.get(name):
                raise StorageUnavailable(f'{name} needs the security vault (locked or not installed)')
            self._atomic(self._path(name, False), raw)
            return 'plain'

    @staticmethod
    def _atomic(path, data):
        tmp = f'{path}.{os.getpid()}.{threading.get_ident()}.tmp'
        with open(tmp, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)


def _short_text(v, n, field):
    v = '' if v is None else str(v).strip()
    if len(v) > n:
        raise ValueError(f'{field} too long (max {n})')
    if any(ord(c) < 32 for c in v):
        raise ValueError(f'{field} has control characters')
    return v


def validate_contacts(doc):
    """{'contacts': [{id,name,phone,relation}], 'groups': [{id,name,members:[contact id]}]} -> cleaned copy."""
    if not isinstance(doc, dict):
        raise ValueError('expected an object')
    contacts, ids = [], set()
    for c in doc.get('contacts', [])[:200]:
        cid = _short_text(c.get('id') or secrets.token_hex(6), 40, 'id')
        phone = clean_phone(c.get('phone'))
        if not phone:
            raise ValueError(f'bad phone number for {c.get("name")!r}')
        if cid in ids:
            raise ValueError(f'duplicate contact id {cid}')
        ids.add(cid)
        contacts.append({'id': cid, 'name': _short_text(c.get('name'), 80, 'name') or phone, 'phone': phone,
                         'relation': _short_text(c.get('relation'), 40, 'relation')})
    groups, gids = [], set()
    for g in doc.get('groups', [])[:50]:
        gid = _short_text(g.get('id') or secrets.token_hex(6), 40, 'id')
        if gid in gids:
            raise ValueError(f'duplicate group id {gid}')
        gids.add(gid)
        members = [m for m in g.get('members', []) if m in ids]
        groups.append({'id': gid, 'name': _short_text(g.get('name'), 60, 'group name') or 'Group',
                       'members': list(dict.fromkeys(members))})
    return {'contacts': contacts, 'groups': groups}


def validate_directory(doc):
    """Partner clinics/hospitals the clinic adds itself. No entry is ever an emergency route."""
    if not isinstance(doc, dict):
        raise ValueError('expected an object')
    out = []
    for p in doc.get('partners', [])[:500]:
        url = _short_text(p.get('signal_url'), 300, 'signal_url')
        if url:
            u = urllib.parse.urlparse(url)
            local = u.hostname in ('localhost', '127.0.0.1', '::1')
            if u.scheme not in ('wss', 'ws') or (u.scheme == 'ws' and not local) or not u.hostname:
                raise ValueError('signal_url must be wss://... (ws:// only for localhost)')
        kind = p.get('kind') or 'clinic'
        if kind not in ('clinic', 'hospital', 'telehealth', 'lab', 'other'):
            raise ValueError('bad kind')
        cc = _short_text(p.get('country'), 2, 'country').upper()
        if cc and not re.fullmatch(r'[A-Z]{2}', cc):
            raise ValueError('country must be an ISO 3166-1 alpha-2 code')
        key = _short_text(p.get('my_key'), 128, 'my_key')
        if key and not re.fullmatch(r'[0-9a-f]{64}', key):
            raise ValueError('my_key must be the 64-hex key the partner issued')
        phone = p.get('phone')
        out.append({'id': _short_text(p.get('id') or secrets.token_hex(6), 40, 'id'),
                    'name': _short_text(p.get('name'), 120, 'name') or 'Partner',
                    'kind': kind, 'country': cc,
                    'phone': (clean_phone(phone) or '') if phone else '',
                    'signal_url': url,
                    'peer_id': _short_text(p.get('peer_id'), 64, 'peer_id'),
                    'my_id': _short_text(p.get('my_id'), 64, 'my_id'),
                    'my_key': key,
                    'notes': _short_text(p.get('notes'), 500, 'notes')})
    return {'partners': out}


def validate_settings(doc):
    out = {}
    cc = (doc.get('clinic_country') or '').upper()
    if cc and not re.fullmatch(r'[A-Z]{2}', cc):
        raise ValueError('clinic_country must be ISO 3166-1 alpha-2')
    out['clinic_country'] = cc
    ice = []
    for s in doc.get('ice_servers', [])[:8]:
        urls = s.get('urls')
        urls = [urls] if isinstance(urls, str) else list(urls or [])
        for u in urls:
            if not re.match(r'^(stun|stuns|turn|turns):[^\s]{3,250}$', str(u)):
                raise ValueError(f'bad ICE server url {u!r}')
        if not urls:
            continue
        item = {'urls': urls}
        if s.get('username'):
            item['username'] = _short_text(s['username'], 128, 'username')
        if s.get('credential'):
            item['credential'] = _short_text(s['credential'], 256, 'credential')
        ice.append(item)
    out['ice_servers'] = ice
    out['speech_enabled'] = bool(doc.get('speech_enabled', True))
    return out


# ====================================================================================== WebSocket (RFC 6455)
WS_GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
OP_CONT, OP_TEXT, OP_BIN, OP_CLOSE, OP_PING, OP_PONG = 0, 1, 2, 8, 9, 10


class WSClosed(Exception):
    pass


def ws_accept(key):
    return base64.b64encode(hashlib.sha1((key + WS_GUID).encode('ascii')).digest()).decode('ascii')


class WSConn:
    """One WebSocket over a connected socket. Server side expects masked frames; client side masks."""

    def __init__(self, sock, client=False, max_size=128 * 1024, idle=20.0, initial=b''):
        self.sock = sock
        self.client = client
        self.max_size = max_size
        self.idle = idle
        self._buf = bytearray(initial)
        self._wlock = threading.Lock()
        self.closed = False
        self.close_code = None
        sock.settimeout(idle)

    def _recv_exact(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(max(65536, n - len(self._buf)))
            if not chunk:
                raise WSClosed('peer closed the socket')
            self._buf += chunk
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    def _send_frame(self, opcode, payload=b''):
        hdr = bytearray([0x80 | opcode])
        n = len(payload)
        mbit = 0x80 if self.client else 0
        if n < 126:
            hdr.append(mbit | n)
        elif n < 65536:
            hdr.append(mbit | 126)
            hdr += struct.pack('>H', n)
        else:
            hdr.append(mbit | 127)
            hdr += struct.pack('>Q', n)
        if self.client:
            mask = os.urandom(4)
            hdr += mask
            payload = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))
        with self._wlock:
            if self.closed and opcode != OP_CLOSE:
                raise WSClosed('closed')
            self.sock.sendall(bytes(hdr) + payload)

    def send_text(self, text):
        self._send_frame(OP_TEXT, text.encode('utf-8'))

    def send_json(self, obj):
        self.send_text(json.dumps(obj, separators=(',', ':')))

    def ping(self, data=b''):
        self._send_frame(OP_PING, data)

    def close(self, code=1000, reason=''):
        if self.closed:
            return
        try:
            self._send_frame(OP_CLOSE, struct.pack('>H', code) + reason.encode('utf-8')[:120])
        except OSError:
            pass
        self.closed = True
        self.close_code = code
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass

    def _read_frame(self):
        b1, b2 = self._recv_exact(2)
        fin, rsv, opcode = b1 & 0x80, b1 & 0x70, b1 & 0x0F
        masked, n = b2 & 0x80, b2 & 0x7F
        if rsv:
            raise WSClosed('reserved bits set (no extensions negotiated)')
        if self.client and masked:
            raise WSClosed('server frames must not be masked')
        if not self.client and not masked:
            raise WSClosed('client frames must be masked')
        if n == 126:
            n = struct.unpack('>H', self._recv_exact(2))[0]
        elif n == 127:
            n = struct.unpack('>Q', self._recv_exact(8))[0]
        if opcode >= 8 and (n > 125 or not fin):
            raise WSClosed('bad control frame')
        if n > self.max_size:
            raise WSClosed('frame too large')
        mask = self._recv_exact(4) if masked else None
        data = self._recv_exact(n)
        if mask:
            data = bytes(b ^ mask[i & 3] for i, b in enumerate(data))
        return bool(fin), opcode, data

    def recv(self):
        """-> str (text) or bytes (binary). Answers pings, raises WSClosed on close. socket.timeout bubbles up."""
        parts, first = [], None
        while True:
            fin, op, data = self._read_frame()
            if op == OP_PING:
                self._send_frame(OP_PONG, data)
                continue
            if op == OP_PONG:
                continue
            if op == OP_CLOSE:
                code = struct.unpack('>H', data[:2])[0] if len(data) >= 2 else 1005
                self.close_code = code
                if not self.closed:
                    try:
                        self._send_frame(OP_CLOSE, data[:2])
                    except OSError:
                        pass
                    self.closed = True
                raise WSClosed(f'close {code}')
            if op in (OP_TEXT, OP_BIN):
                if first is not None:
                    raise WSClosed('new message inside a fragmented one')
                first = op
            elif op == OP_CONT:
                if first is None:
                    raise WSClosed('continuation without start')
            else:
                raise WSClosed(f'unknown opcode {op}')
            parts.append(data)
            if sum(map(len, parts)) > self.max_size:
                raise WSClosed('message too large')
            if fin:
                raw = b''.join(parts)
                if first == OP_TEXT:
                    return raw.decode('utf-8')   # UnicodeDecodeError -> caller closes 1007
                return raw

    def recv_json(self):
        m = self.recv()
        if isinstance(m, bytes):
            raise ValueError('binary message where JSON was expected')
        return json.loads(m)


def ws_connect(url, timeout=10.0, headers=None, origin=None, ssl_context=None):
    """Minimal client (tests, CLI probes, Chrome DevTools). -> WSConn(client=True)."""
    u = urllib.parse.urlparse(url)
    if u.scheme not in ('ws', 'wss'):
        raise ValueError('ws:// or wss:// only')
    port = u.port or (443 if u.scheme == 'wss' else 80)
    sock = socket.create_connection((u.hostname, port), timeout=timeout)
    if u.scheme == 'wss':
        sock = (ssl_context or ssl.create_default_context()).wrap_socket(sock, server_hostname=u.hostname)
    key = base64.b64encode(os.urandom(16)).decode('ascii')
    path = (u.path or '/') + (('?' + u.query) if u.query else '')
    host = u.hostname + (f':{u.port}' if u.port else '')
    lines = [f'GET {path} HTTP/1.1', f'Host: {host}', 'Upgrade: websocket', 'Connection: Upgrade',
             f'Sec-WebSocket-Key: {key}', 'Sec-WebSocket-Version: 13']
    if origin:
        lines.append(f'Origin: {origin}')
    for k, v in (headers or {}).items():
        lines.append(f'{k}: {v}')
    sock.sendall(('\r\n'.join(lines) + '\r\n\r\n').encode('ascii'))
    buf = b''
    while b'\r\n\r\n' not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise WSClosed('handshake: connection closed')
        buf += chunk
        if len(buf) > 65536:
            raise WSClosed('handshake too large')
    head, rest = buf.split(b'\r\n\r\n', 1)
    hl = head.decode('latin-1').split('\r\n')
    if ' 101 ' not in hl[0] + ' ':
        raise WSClosed(f'handshake refused: {hl[0]}')
    hdrs = {k.strip().lower(): v.strip() for k, _, v in (h.partition(':') for h in hl[1:])}
    if hdrs.get('sec-websocket-accept') != ws_accept(key):
        raise WSClosed('bad Sec-WebSocket-Accept')
    return WSConn(sock, client=True, idle=timeout, initial=rest)


# ====================================================================================== signaling hub
PROTO = 'gcv-signal-1'
RELAY_TYPES = frozenset({'ring', 'accept', 'decline', 'busy', 'offer', 'answer', 'candidate', 'bye'})
CLINIC_ID_RE = re.compile(r'^[a-z0-9][a-z0-9._-]{1,63}$')


def auth_mac(key_hex, clinic_id, nonce):
    return hmac.new(bytes.fromhex(key_hex), f'{PROTO}|{clinic_id}|{nonce}'.encode('utf-8'),
                    hashlib.sha256).hexdigest()


class SignalingHub:
    """Relays call-setup messages between authenticated clinics. It never sees media (that is peer to peer,
    DTLS-SRTP) and never stores messages. Registry: {clinic_id: {'name', 'key' (64 hex), 'added'}}."""

    AUTH_TIMEOUT = 10.0
    RATE = (200, 10.0)          # messages per window
    FAIL_LIMIT = (10, 300.0)    # failed auths per IP per window

    def __init__(self, store=None, registry=None):
        self.store = store
        self._reg = registry if registry is not None else (store.load('clinics', {}) if store else {})
        self._lock = threading.Lock()
        self._conns = {}            # clinic_id -> {session: WSConn}
        self._fails = {}            # ip -> [timestamps]

    # ---- registry
    def add_clinic(self, clinic_id, name=''):
        if not CLINIC_ID_RE.match(clinic_id or ''):
            raise ValueError('clinic id: 2-64 chars, lowercase letters, digits, . _ -')
        key = secrets.token_hex(32)
        with self._lock:
            self._reg[clinic_id] = {'name': _short_text(name, 120, 'name'), 'key': key,
                                    'added': _dt.date.today().isoformat()}
            self._persist()
        return key

    def remove_clinic(self, clinic_id):
        with self._lock:
            found = self._reg.pop(clinic_id, None) is not None
            self._persist()
            conns = list(self._conns.pop(clinic_id, {}).values())
        for c in conns:
            c.close(4003, 'removed')
        return found

    def clinics(self):
        with self._lock:
            return {k: {'name': v.get('name', ''), 'added': v.get('added')} for k, v in self._reg.items()}

    def key_of(self, clinic_id):
        with self._lock:
            v = self._reg.get(clinic_id)
            return v['key'] if v else None

    def _persist(self):
        if self.store:
            self.store.save('clinics', self._reg)

    def online(self, ids):
        with self._lock:
            return [i for i in ids if self._conns.get(i)]

    # ---- connection
    def _too_many_fails(self, ip):
        now = time.monotonic()
        with self._lock:
            lst = [t for t in self._fails.get(ip, []) if now - t < self.FAIL_LIMIT[1]]
            self._fails[ip] = lst
            return len(lst) >= self.FAIL_LIMIT[0]

    def _fail(self, ip):
        with self._lock:
            self._fails.setdefault(ip, []).append(time.monotonic())

    def serve(self, ws, peer_ip='?'):
        """Run one connection to the end (blocking, in the caller's thread)."""
        if self._too_many_fails(peer_ip):
            ws.send_json({'type': 'error', 'code': 'rate_limited'})
            ws.close(4029, 'too many failed logins')
            return
        nonce = secrets.token_hex(16)
        ws.send_json({'type': 'hello', 'proto': PROTO, 'nonce': nonce})
        ws.sock.settimeout(self.AUTH_TIMEOUT)
        try:
            msg = ws.recv_json()
        except (WSClosed, OSError, ValueError, UnicodeDecodeError):
            ws.close(4001, 'auth expected')
            return
        cid = msg.get('clinic_id') if isinstance(msg, dict) else None
        key = self.key_of(cid) if isinstance(cid, str) else None
        mac = msg.get('mac') if isinstance(msg, dict) else None
        if (not isinstance(msg, dict) or msg.get('type') != 'auth' or key is None or not isinstance(mac, str)
                or not hmac.compare_digest(mac, auth_mac(key, cid, nonce))):
            self._fail(peer_ip)
            log(f'auth failed from {peer_ip} for {cid!r}')
            try:
                ws.send_json({'type': 'error', 'code': 'auth_failed'})
            except OSError:
                pass
            ws.close(4001, 'auth failed')
            return
        session = secrets.token_hex(8)
        with self._lock:
            self._conns.setdefault(cid, {})[session] = ws
        log(f'{cid} connected ({peer_ip}) session {session}')
        ws.send_json({'type': 'welcome', 'clinic_id': cid, 'session': session,
                      'name': self.clinics().get(cid, {}).get('name', '')})
        ws.sock.settimeout(ws.idle)
        window, count, missed = time.monotonic(), 0, 0
        try:
            while True:
                try:
                    raw = ws.recv()
                    missed = 0
                except socket.timeout:
                    missed += 1
                    if missed >= 3:
                        break
                    ws.ping(b'k')
                    continue
                now = time.monotonic()
                if now - window > self.RATE[1]:
                    window, count = now, 0
                count += 1
                if count > self.RATE[0]:
                    ws.send_json({'type': 'error', 'code': 'rate_limited'})
                    break
                try:
                    msg = json.loads(raw) if isinstance(raw, str) else None
                except ValueError:
                    msg = None
                if not isinstance(msg, dict):
                    ws.send_json({'type': 'error', 'code': 'bad_message'})
                    continue
                self._dispatch(cid, session, ws, msg)
        except UnicodeDecodeError:
            ws.close(1007, 'invalid utf-8')
        except (WSClosed, OSError):
            pass
        finally:
            with self._lock:
                d = self._conns.get(cid, {})
                d.pop(session, None)
                if not d:
                    self._conns.pop(cid, None)
            ws.close()
            log(f'{cid} disconnected session {session}')

    def _dispatch(self, cid, session, ws, msg):
        t = msg.get('type')
        if t == 'ping':
            ws.send_json({'type': 'pong'})
            return
        if t == 'presence':
            ids = [i for i in msg.get('ids', []) if isinstance(i, str)][:200]
            ws.send_json({'type': 'presence', 'online': self.online(ids)})
            return
        if t not in RELAY_TYPES:
            ws.send_json({'type': 'error', 'code': 'unknown_type', 'got': str(t)[:32]})
            return
        to = msg.get('to')
        if not isinstance(to, str) or to == cid and not msg.get('to_session'):
            ws.send_json({'type': 'error', 'code': 'bad_target'})
            return
        out = {k: v for k, v in msg.items() if k in ('type', 'call_id', 'sdp', 'candidate', 'media', 'reason',
                                                     'name', 'to_session')}
        out.update({'from': cid, 'from_session': session})   # identity comes from the hub, never the sender
        with self._lock:
            targets = dict(self._conns.get(to, {}))
        want = msg.get('to_session')
        if want:
            targets = {k: v for k, v in targets.items() if k == want}
        if not targets:
            ws.send_json({'type': 'error', 'code': 'offline', 'to': to, 'call_id': msg.get('call_id')})
            return
        for c in targets.values():
            try:
                c.send_json(out)
            except OSError:
                pass

    def close_all(self):
        with self._lock:
            conns = [c for d in self._conns.values() for c in d.values()]
            self._conns.clear()
        for c in conns:
            c.close(1001, 'server shutting down')


# ====================================================================================== HTTP mounting
# Keeps the emergency screen and the number list available when the network is down (secure contexts only).
SERVICE_WORKER = r'''
const CACHE = 'gcv-comms-v1';
const KEEP = ['./', './api/numbers'];
self.addEventListener('install', (e) => { e.waitUntil(caches.open(CACHE).then((c) => c.addAll(KEEP)).catch(() => {})); self.skipWaiting(); });
self.addEventListener('activate', (e) => { e.waitUntil(self.clients.claim()); });
self.addEventListener('fetch', (e) => {
  const u = new URL(e.request.url);
  if (e.request.method !== 'GET' || u.origin !== location.origin) return;
  const rel = './' + u.pathname.split('/comms/')[1];
  if (!KEEP.includes(rel === './' ? './' : rel)) return;
  e.respondWith(fetch(e.request).then((r) => { const copy = r.clone(); caches.open(CACHE).then((c) => c.put(e.request, copy)); return r; })
    .catch(() => caches.match(e.request)));
});
'''


def _default_data_dir():
    return os.environ.get('GORGON_COMMS_DIR') or os.path.join(os.path.expanduser('~'), '.gorgon_covenant', 'comms')


def _loopback_only(h):
    ip = (h.client_address or ('',))[0]
    return ip in ('127.0.0.1', '::1') or ip.startswith('127.') or ip == '::ffff:127.0.0.1'


def _try_vault():
    try:
        import security_vault  # noqa: WPS433 (optional, owned by another module)
        return security_vault.default_vault()
    except Exception as e:  # missing module, no vault yet, USB absent, ...
        log(f'security vault not available ({type(e).__name__}): contacts stay on each device')
        return None


def _interop_available():
    try:
        import interop_bridge  # noqa: F401
        return True
    except Exception:
        return False


class CommsModule:
    """Mount with: comms = CommsModule(); then in do_GET/do_POST: `if comms.handle(self): return`.
    The handler must come from a ThreadingHTTPServer (a WebSocket keeps its thread until it closes).
    clinic_app must run its own login check BEFORE calling handle() for /comms/api/* routes; the WebSocket
    route authenticates itself (HMAC per clinic) and may be reachable by partner clinics."""

    PUBLIC = ('/', '/index.html', '/sw.js', '/api/numbers', '/api/i18n', '/ws')   # usable without a clinic login

    def __init__(self, data_dir=None, vault='auto', numbers_path=None, ui_path=None, prefix=PREFIX,
                 self_clinic_id=None, authorize=None):
        """authorize(handler) -> bool decides who may use the private routes (config with this clinic's call
        key, contacts, directory, settings, issuing partner keys). clinic_app passes its own login check.
        Default: only requests from this computer (loopback) are allowed."""
        self.authorize = authorize or _loopback_only
        self.prefix = prefix.rstrip('/')
        self.vault = _try_vault() if vault == 'auto' else vault
        self.store = CommsStore(data_dir or _default_data_dir(), self.vault)
        self.numbers_path = numbers_path or NUMBERS_PATH
        self.ui_path = ui_path or UI_PATH
        self.hub = SignalingHub(self.store)
        self.settings = validate_settings(self.store.load('settings', {}))
        if not self.settings['ice_servers']:
            self.settings['ice_servers'] = [{'urls': [DEFAULT_STUN]}]
        self.self_id = self_clinic_id or self.settings.get('self_id') or 'this-clinic'
        if self.hub.key_of(self.self_id) is None:
            self.hub.add_clinic(self.self_id, 'This clinic')

    # ---- helpers
    def _send(self, h, code, body, ctype='application/json; charset=utf-8', extra=None):
        if not isinstance(body, (bytes, bytearray)):
            body = json.dumps(body, ensure_ascii=False).encode('utf-8')
        h.send_response(code)
        h.send_header('Content-Type', ctype)
        h.send_header('Content-Length', str(len(body)))
        h.send_header('Cache-Control', 'no-store')
        h.send_header('X-Content-Type-Options', 'nosniff')
        h.send_header('Referrer-Policy', 'no-referrer')
        if ctype.startswith('text/html'):
            h.send_header('Content-Security-Policy',
                          "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                          "connect-src 'self' ws: wss:; img-src 'self' data:; media-src 'self' blob:; "
                          "frame-ancestors 'none'")
            h.send_header('Permissions-Policy', 'camera=(self), microphone=(self), geolocation=(self)')
        for k, v in (extra or {}).items():
            h.send_header(k, v)
        h.end_headers()
        if h.command != 'HEAD':
            h.wfile.write(body)

    def _json_body(self, h, limit=256 * 1024):
        n = int(h.headers.get('Content-Length') or 0)
        if n <= 0 or n > limit:
            raise ValueError('body missing or too large')
        if 'application/json' not in (h.headers.get('Content-Type') or ''):
            raise ValueError('Content-Type must be application/json')
        return json.loads(h.rfile.read(n).decode('utf-8'))

    @staticmethod
    def _drain(h, limit=256 * 1024):
        """Read and discard an unread request body before an early rejection, so the client sees the 401 instead of
        a reset connection (Windows WinError 10053). Bodies over the limit are not read; the connection is closed."""
        try:
            n = int(h.headers.get('Content-Length') or 0)
        except ValueError:
            n = -1
        if 0 < n <= limit:
            h.rfile.read(n)
        elif n != 0:
            h.close_connection = True

    @staticmethod
    def _same_origin(h):
        """Write requests: custom header (forces a CORS preflight we never answer) + Origin == Host when sent."""
        if h.headers.get('X-Covenant-Comms') != '1':
            return False
        origin = h.headers.get('Origin')
        if origin:
            hosts = {h.headers.get('Host') or '', h.headers.get('X-Forwarded-Host') or ''} - {''}
            return urllib.parse.urlparse(origin).netloc in hosts
        return True

    def config(self):
        return {'safety_notice': SAFETY_NOTICE, 'no_auto_summon': NO_AUTO_SUMMON,
                'partner_not_emergency': PARTNER_NOT_EMERGENCY,
                'stun_note': STUN_NOTE, 'turn_note': TURN_NOTE, 'default_stun': DEFAULT_STUN,
                'ice_servers': self.settings['ice_servers'], 'clinic_country': self.settings['clinic_country'],
                'speech_enabled': self.settings['speech_enabled'], 'storage': self.store.status(),
                'interop_bridge': _interop_available(),
                'self_hub': {'path': self.prefix + '/ws', 'clinic_id': self.self_id,
                             'key': self.hub.key_of(self.self_id)}}

    # ---- entry point
    def handle(self, h):
        path = urllib.parse.urlparse(h.path).path
        if not (path == self.prefix or path.startswith(self.prefix + '/')):
            return False
        sub = path[len(self.prefix):] or '/'
        try:
            if sub not in self.PUBLIC and not self.authorize(h):
                self._drain(h)
                self._send(h, 401, {'error': 'clinic login required'})
                return True
            if sub == '/ws':
                self._websocket(h)
                return True
            if h.command in ('GET', 'HEAD'):
                self._get(h, sub)
            elif h.command == 'POST':
                if not self._same_origin(h):
                    self._drain(h)
                    self._send(h, 403, {'error': 'cross-origin or missing X-Covenant-Comms header'})
                else:
                    self._post(h, sub)
            else:
                self._send(h, 405, {'error': 'method not allowed'})
        except StorageUnavailable as e:
            self._send(h, 503, {'error': str(e), 'storage': 'browser-only'})
        except ValueError as e:
            self._send(h, 400, {'error': str(e)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        return True

    def _get(self, h, sub):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(h.path).query)
        if sub in ('/', '/index.html'):
            with open(self.ui_path, 'rb') as f:
                self._send(h, 200, f.read(), 'text/html; charset=utf-8')
        elif sub == '/sw.js':
            self._send(h, 200, SERVICE_WORKER.encode('utf-8'), 'text/javascript; charset=utf-8',
                       extra={'Service-Worker-Allowed': self.prefix + '/'})
        elif sub == '/api/numbers':
            data = load_numbers(self.numbers_path)
            cc = (q.get('cc') or [''])[0]
            if cc:
                plan = dial_plan(cc, data)
                self._send(h, 200 if plan else 404, plan or {'error': 'unknown country code'})
            else:
                self._send(h, 200, data)
        elif sub == '/api/i18n':
            # UI text only (shared Covenant catalogs, emergency-page keys); the page has English built in
            try:
                i18n = importlib.import_module('i18n')
            except Exception:
                i18n = None
            if i18n is None:
                self._send(h, 404, {'error': 'no translations installed'})
            elif (q.get('list') or [''])[0] == '1':
                self._send(h, 200, {'locales': i18n.locale_list()})
            else:
                code = i18n.canonical((q.get('l') or ['en'])[0])
                if not code or code not in i18n.available():
                    self._send(h, 404, {'error': 'unknown language'})
                else:
                    self._send(h, 200, i18n.bundle(code, scope='comms'))
        elif sub == '/api/config':
            self._send(h, 200, self.config())
        elif sub == '/api/contacts':
            self._send(h, 200, self.store.load('contacts', {'contacts': [], 'groups': []}))
        elif sub == '/api/directory':
            self._send(h, 200, self.store.load('directory', {'partners': []}))
        elif sub == '/api/hub/clinics':
            self._send(h, 200, {'clinics': self.hub.clinics(), 'self_id': self.self_id})
        else:
            self._send(h, 404, {'error': 'not found'})

    def _post(self, h, sub):
        body = self._json_body(h)
        if sub == '/api/contacts':
            doc = validate_contacts(body)
            self._send(h, 200, {'saved': self.store.save('contacts', doc), **doc})
        elif sub == '/api/directory':
            doc = validate_directory(body)
            self._send(h, 200, {'saved': self.store.save('directory', doc), **doc})
        elif sub == '/api/settings':
            doc = validate_settings(body)
            doc['self_id'] = self.self_id
            self.store.save('settings', doc)
            self.settings = doc
            if not doc['ice_servers']:
                self.settings['ice_servers'] = [{'urls': [DEFAULT_STUN]}]
            self._send(h, 200, self.config())
        elif sub == '/api/alert-text':
            text = build_alert_message(body.get('sender', ''), body.get('lat'), body.get('lon'),
                                       body.get('accuracy'), body.get('note', ''))
            nums = body.get('numbers') or []
            self._send(h, 200, {'text': text, 'sms': sms_uri(nums, text) if nums else None})
        elif sub == '/api/hub/add':
            key = self.hub.add_clinic(str(body.get('clinic_id', '')), body.get('name', ''))
            self._send(h, 200, {'clinic_id': body.get('clinic_id'), 'key': key,
                                'note': 'Give this key to the partner over a trusted channel. Shown once.'})
        elif sub == '/api/hub/remove':
            cid = str(body.get('clinic_id', ''))
            if cid == self.self_id:
                raise ValueError('cannot remove this clinic itself')
            self._send(h, 200, {'removed': self.hub.remove_clinic(cid)})
        else:
            self._send(h, 404, {'error': 'not found'})

    def _websocket(self, h):
        hd = h.headers
        if (hd.get('Upgrade', '').lower() != 'websocket' or 'upgrade' not in hd.get('Connection', '').lower()
                or hd.get('Sec-WebSocket-Version') != '13' or not hd.get('Sec-WebSocket-Key')):
            self._send(h, 426, {'error': 'WebSocket upgrade required'}, extra={'Sec-WebSocket-Version': '13'})
            return
        try:
            if len(base64.b64decode(hd['Sec-WebSocket-Key'], validate=True)) != 16:
                raise ValueError
        except Exception:
            self._send(h, 400, {'error': 'bad Sec-WebSocket-Key'})
            return
        h.send_response(101, 'Switching Protocols')
        h.send_header('Upgrade', 'websocket')
        h.send_header('Connection', 'Upgrade')
        h.send_header('Sec-WebSocket-Accept', ws_accept(hd['Sec-WebSocket-Key']))
        h.end_headers()
        h.wfile.flush()
        h.close_connection = True
        ws = WSConn(h.connection, client=False)
        self.hub.serve(ws, peer_ip=h.client_address[0] if h.client_address else '?')


class _StandaloneHandler(http.server.BaseHTTPRequestHandler):
    comms = None
    protocol_version = 'HTTP/1.1'
    server_version = 'StarkComms/1'

    def do_GET(self):
        if not self.comms.handle(self):
            if self.path in ('/', ''):
                self.send_response(302)
                self.send_header('Location', PREFIX + '/')
                self.send_header('Content-Length', '0')
                self.end_headers()
            else:
                self.send_error(404)

    do_HEAD = do_GET

    def do_POST(self):
        if not self.comms.handle(self):
            self.send_error(404)

    def log_message(self, fmt, *args):
        if os.environ.get('GORGON_COMMS_HTTPLOG'):
            super().log_message(fmt, *args)


class _QuietServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (ConnectionError, ssl.SSLError, socket.timeout)):
            return   # browsers drop connections all the time; not worth a traceback
        super().handle_error(request, client_address)


def make_server(host='127.0.0.1', port=8765, comms=None, certfile=None, keyfile=None):
    """Standalone server. With certfile/keyfile it serves HTTPS/WSS (needed for camera/mic off localhost)."""
    comms = comms or CommsModule()
    handler = type('Handler', (_StandaloneHandler,), {'comms': comms})
    srv = _QuietServer((host, port), handler)
    srv.daemon_threads = True
    if certfile:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(certfile, keyfile)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    srv.comms = comms
    return srv


# ====================================================================================== CLI
def _main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description='Stark Covenant emergency & communications')
    ap.add_argument('--data', default=None, help='data directory (default ~/.gorgon_covenant/comms)')
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('serve')
    s.add_argument('--host', default='127.0.0.1')
    s.add_argument('--port', type=int, default=8765)
    s.add_argument('--cert')
    s.add_argument('--key')
    s.add_argument('--no-vault', action='store_true')
    a = sub.add_parser('add-clinic')
    a.add_argument('clinic_id')
    a.add_argument('name', nargs='?', default='')
    r = sub.add_parser('remove-clinic')
    r.add_argument('clinic_id')
    sub.add_parser('list-clinics')
    sub.add_parser('validate-numbers')
    f = sub.add_parser('fetch-itu')
    f.add_argument('out')
    c = sub.add_parser('reconcile-itu')
    c.add_argument('rows')
    args = ap.parse_args(argv)

    if args.cmd == 'validate-numbers':
        probs = validate_numbers(load_numbers())
        print('\n'.join(probs) if probs else 'emergency_numbers.json: OK')
        return 1 if probs else 0
    if args.cmd == 'fetch-itu':
        rows = fetch_itu_e129()
        with open(args.out, 'w', encoding='utf-8') as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=0)
        print(f'{len(rows)} rows -> {args.out}')
        return 0
    if args.cmd == 'reconcile-itu':
        with open(args.rows, encoding='utf-8') as fh:
            rows = json.load(fh)
        data = load_numbers()
        reconcile_with_itu(data, rows, retrieved=_dt.date.today().isoformat())
        probs = validate_numbers(data)
        if probs:
            print('\n'.join(probs))
            return 1
        tmp = NUMBERS_PATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, NUMBERS_PATH)
        v = sum(e['verified'] for e in data['countries'].values())
        print(f'reconciled: {v}/{len(data["countries"])} countries fully verified')
        return 0

    vault = None if getattr(args, 'no_vault', False) else 'auto'
    if args.cmd in ('add-clinic', 'remove-clinic', 'list-clinics'):
        store = CommsStore(args.data or _default_data_dir(), _try_vault())
        hub = SignalingHub(store)
        if args.cmd == 'add-clinic':
            print(json.dumps({'clinic_id': args.clinic_id, 'key': hub.add_clinic(args.clinic_id, args.name),
                              'note': 'Give the key to that clinic over a trusted channel; it is shown once.'}))
        elif args.cmd == 'remove-clinic':
            print('removed' if hub.remove_clinic(args.clinic_id) else 'not found')
        else:
            print(json.dumps(hub.clinics(), indent=1))
        return 0
    if args.cmd == 'serve':
        comms = CommsModule(data_dir=args.data, vault=vault)
        srv = make_server(args.host, args.port, comms, args.cert, args.key)
        scheme = 'https' if args.cert else 'http'
        log(f'serving {scheme}://{args.host}:{args.port}{PREFIX}/  (Ctrl+C stops)')
        log(SAFETY_NOTICE)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            comms.hub.close_all()
            srv.server_close()
        return 0
    return 2


if __name__ == '__main__':
    sys.exit(_main())
