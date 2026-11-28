"""Stark Covenant (formerly Stark Covenant) - self_learner: local-only adaptive helpers. Stdlib only (3.10+).

What it does
  * learns ONE clinician's frequently used phrases, note templates (section skeletons) and order sets from that
    clinician's SIGNED notes on this machine -> autocomplete / template / order-set suggestions;
  * flags data-quality issues: vitals without units, unknown units, implausible values, diastolic >= systolic,
    impossible dates of birth, likely duplicate patients (fuzzy name + DOB);
  * optional wording suggestions from a LOCAL Ollama (127.0.0.1:11434 only, 1.5 s timeout), silently off
    when Ollama is not running.

What it never does
  * it never writes, edits or signs a record: every public function takes records as read-only input and
    returns Suggestion objects (frozen, labelled "suggestion - requires clinician sign-off");
  * no diagnosis and no treatment advice: suggestions only echo the clinician's OWN signed wording/habits or
    point at data-entry problems; wording output that adds numbers or advice terms is discarded;
  * no data leaves the machine: the only network call is to the loopback Ollama address, hard-coded;
  * a phrase is offered only after it appeared in >= MIN_SEEN signed notes, digits are masked ("___") and the
    patient's own name tokens are dropped before anything is stored.

Profile storage: <profile_dir>/<sha256(clinician)>.gcsl, written atomically. Sealed with the vault object the
app passes in (security_vault.Vault: encrypt(plaintext, aad) / decrypt(blob, aad)), or module-level
security_vault encrypt/decrypt functions when they work without any setup, else Windows DPAPI (current user),
else stored with owner-only permissions and reported as unprotected by status(). A profile sealed by another
codec on this machine is migrated on load. self_learner never constructs or unlocks a vault itself. If saving
fails (vault locked, disk full) learning stays in memory and status()['save_error'] says why. reset() shreds it.
"""
from __future__ import annotations

import base64
import collections
import contextlib
import copy
import dataclasses
import datetime as dt
import difflib
import hashlib
import http.client
import importlib
import json
import logging
import os
import re
import sys
import threading
import time
import unicodedata

__all__ = ['PRODUCT', 'SUGGESTION_LABEL', 'Suggestion', 'SignOff', 'SignOffRequired', 'SelfLearner',
           'accept_suggestion', 'check_data_quality', 'find_duplicate_patients', 'is_signed', 'note_text']

PRODUCT = 'Stark Covenant'
SUGGESTION_LABEL = 'suggestion \u2014 requires clinician sign-off'
PROFILE_AAD = b'GorgonCovenant.self_learner.profile.v1'
MIN_SEEN = 2                 # a phrase must appear in this many signed notes before it is ever offered
MAX_PHRASES = 20000          # profile cap; lowest counts are pruned first
OLLAMA_HOST, OLLAMA_PORT = '127.0.0.1', 11434
OLLAMA_TIMEOUT = 1.5
OLLAMA_RETRY_S = 60.0        # after a failure, do not try again for this long (keeps the UI snappy)
MAGIC = b'GCSL1\n'
LOG_PREFIX = '[self_learner]'
_log = logging.getLogger('gorgon_covenant.self_learner')


# ------------------------------------------------------------------------------------------------ model
@dataclasses.dataclass(frozen=True)
class Suggestion:
    """A read-only proposal. Holding one changes nothing; only a clinician acting through clinic_app can."""
    kind: str                     # autocomplete | template | order_set | data_quality | duplicate_patient | wording
    text: str
    detail: str = ''
    score: float = 0.0
    field: str = ''
    code: str = ''
    refs: tuple = ()
    label: str = SUGGESTION_LABEL
    requires_signoff: bool = True
    source: str = 'self_learner'

    def __post_init__(self):
        # label and sign-off requirement cannot be weakened by a caller
        object.__setattr__(self, 'label', SUGGESTION_LABEL)
        object.__setattr__(self, 'requires_signoff', True)
        object.__setattr__(self, 'refs', tuple(self.refs))

    @property
    def id(self):
        h = hashlib.sha256(f'{self.kind}\0{self.code}\0{self.field}\0{self.text}\0{self.refs}'.encode('utf-8'))
        return h.hexdigest()[:16]

    def to_dict(self):
        d = dataclasses.asdict(self)
        d['refs'] = list(self.refs)
        d['id'] = self.id
        return d


@dataclasses.dataclass(frozen=True)
class SignOff:
    clinician_id: str
    attested: bool
    at: str = ''


class SignOffRequired(PermissionError):
    pass


def accept_suggestion(suggestion, signoff):
    """Turn an accepted suggestion into a draft fragment for clinic_app to insert under the clinician's own
    signature. Without an attested SignOff this raises; it never touches a record either way."""
    if not isinstance(suggestion, Suggestion):
        raise TypeError('not a Suggestion')
    if not isinstance(signoff, SignOff) or not signoff.attested or not str(signoff.clinician_id).strip():
        raise SignOffRequired('a clinician must review and sign off before a suggestion enters a record')
    return {'text': suggestion.text, 'provenance': {
        'source': suggestion.source, 'kind': suggestion.kind, 'suggestion_id': suggestion.id,
        'accepted_by': signoff.clinician_id,
        'accepted_at': signoff.at or dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}}


# ------------------------------------------------------------------------------------------------ note access
def _get(obj, *names, default=None):
    for n in names:
        if isinstance(obj, dict):
            if n in obj and obj[n] not in (None, ''):
                return obj[n]
        else:
            v = getattr(obj, n, None)
            if v not in (None, ''):
                return v
    return default


def is_signed(note):
    st = str(_get(note, 'status', 'state', default='')).strip().lower()
    if st in ('signed', 'final', 'locked', 'signed_off', 'amended_signed'):
        return True
    if _get(note, 'signed', 'locked', 'is_signed', default=False) is True:
        return True
    return bool(_get(note, 'signed_by', 'signer') and _get(note, 'signed_at', 'signature_time', 'signed_on'))


def note_text(note):
    t = _get(note, 'text', 'body', 'content', 'note', default='')
    if isinstance(t, (list, tuple)):
        t = '\n'.join(str(x) for x in t)
    return str(t)


def note_author(note):
    return str(_get(note, 'signed_by', 'author', 'clinician_id', 'clinician', 'author_id', default='') or '')


def _name_tokens(note):
    out = set()
    for k in ('patient_name', 'patient', 'name', 'given', 'family', 'given_name', 'family_name'):
        v = _get(note, k)
        if isinstance(v, dict):
            v = ' '.join(str(x) for x in v.values() if isinstance(x, str))
        if isinstance(v, str):
            out.update(t for t in _norm_name(v).split() if len(t) > 1)
    return out


# ------------------------------------------------------------------------------------------------ text utils
_WS = re.compile(r'\s+')
_NUM = re.compile(r'\d+(?:[.,]\d+)?')
_EMAIL = re.compile(r'\S+@\S+')
_TOKEN = re.compile(r"[\w'\-/%.:+]+|[^\w\s]", re.UNICODE)
_HEADER = re.compile(r'^\s*(?:#{1,6}\s*)?([A-Za-z\u00C0-\uFFFF][^:\n]{0,40}):\s*$|^\s*#{1,6}\s+(.{1,40})$')
_INLINE_HEADER = re.compile(r'^\s*([A-Za-z\u00C0-\uFFFF][\w /&\-]{0,30}):\s')


def _strip_accents(s):
    return ''.join(c for c in unicodedata.normalize('NFKD', s) if not unicodedata.combining(c))


def _norm_name(s):
    s = _strip_accents(str(s)).casefold()
    s = re.sub(r"[^\w\s]", ' ', s)
    return _WS.sub(' ', s).strip()


def _mask(line):
    line = _EMAIL.sub('___', line)
    return _NUM.sub('___', line)


def _clean_line(line):
    return _WS.sub(' ', line).strip()


# ------------------------------------------------------------------------------------------------ profile crypto
class _Codec:
    name = 'none'

    def seal(self, b):
        return b

    def open(self, b):
        return b


class _VaultCodec(_Codec):
    PAIRS = (('encrypt_blob', 'decrypt_blob'), ('seal', 'unseal'), ('encrypt_bytes', 'decrypt_bytes'),
             ('protect', 'unprotect'), ('encrypt', 'decrypt'))

    def __init__(self, target):
        self.name = 'vault'
        for e, d in self.PAIRS:
            fe, fd = getattr(target, e, None), getattr(target, d, None)
            if not (callable(fe) and callable(fd)):
                continue
            for with_aad in (True, False):        # security_vault.Vault: encrypt(plaintext, aad)
                with contextlib.suppress(Exception):
                    probe = os.urandom(24)
                    out = fe(probe, PROFILE_AAD) if with_aad else fe(probe)
                    if not isinstance(out, (bytes, bytearray)) or bytes(out) == probe:
                        continue
                    back = fd(bytes(out), PROFILE_AAD) if with_aad else fd(bytes(out))
                    if back == probe:
                        self._e, self._d, self._aad = fe, fd, with_aad
                        return
        raise LookupError('no usable encrypt/decrypt pair')

    def seal(self, b):
        return bytes(self._e(b, PROFILE_AAD) if self._aad else self._e(b))

    def open(self, b):
        return bytes(self._d(b, PROFILE_AAD) if self._aad else self._d(b))


class _DpapiCodec(_Codec):
    """Windows DPAPI, current-user scope (ctypes, stdlib)."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.name = 'dpapi'

        class BLOB(ctypes.Structure):
            _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]
        self._c, self._BLOB = ctypes, BLOB
        self._crypt = ctypes.WinDLL('crypt32', use_last_error=True)
        self._k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        self._k32.LocalFree.argtypes = [ctypes.c_void_p]
        self._entropy = b'GorgonChart.self_learner.v1'
        if self.open(self.seal(b'probe')) != b'probe':
            raise OSError('DPAPI probe failed')

    def _blob(self, b):
        buf = self._c.create_string_buffer(b, len(b))
        return self._BLOB(len(b), self._c.cast(buf, self._c.POINTER(self._c.c_char))), buf

    def _call(self, fn, data):
        c = self._c
        src, keep1 = self._blob(data)
        ent, keep2 = self._blob(self._entropy)
        out = self._BLOB()
        if not fn(c.byref(src), None, c.byref(ent), None, None, 0x1, c.byref(out)):   # UI_FORBIDDEN
            raise OSError(c.get_last_error(), 'DPAPI call failed')
        try:
            return c.string_at(out.pbData, out.cbData)
        finally:
            self._k32.LocalFree(c.cast(out.pbData, c.c_void_p))

    def seal(self, b):
        c = self._c
        f = self._crypt.CryptProtectData
        return self._call(lambda s, _d, e, r, p, fl, o: f(s, c.c_wchar_p('gcsl'), e, r, p, fl, o), b)

    def open(self, b):
        c = self._c
        f = self._crypt.CryptUnprotectData
        return self._call(lambda s, _d, e, r, p, fl, o: f(s, None, e, r, p, fl, o), b)


def _pick_codec(vault=None, allow_dpapi=True):
    # Only an app-supplied vault object or ready-to-use module-level functions: constructing or unlocking a
    # Vault (USB share, recovery code, files under a data root) is the app's job, never a side effect here.
    targets = []
    if vault is not None:
        targets.append(vault)
    else:
        with contextlib.suppress(Exception):
            targets.append(importlib.import_module('security_vault'))
    for t in targets:
        with contextlib.suppress(Exception):
            return _VaultCodec(t)
    if allow_dpapi and sys.platform == 'win32':
        with contextlib.suppress(Exception):
            return _DpapiCodec()
    return _Codec()


def _codec_named(name, vault=None, allow_dpapi=True):
    """The codec that can open a profile sealed as `name` on this machine, or None."""
    with contextlib.suppress(Exception):
        if name == 'none':
            return _Codec()
        if name == 'dpapi' and allow_dpapi and sys.platform == 'win32':
            return _DpapiCodec()
        if name == 'vault':
            c = _pick_codec(vault, allow_dpapi=False)
            return c if c.name == 'vault' else None
    return None


def _atomic_write(path, data):
    tmp = path + '.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_BINARY', 0), 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def _shred(path):
    if not os.path.exists(path):
        return
    with contextlib.suppress(OSError):
        n = os.path.getsize(path)
        with open(path, 'r+b') as f:
            f.write(os.urandom(n))
            f.flush()
            os.fsync(f.fileno())
    os.remove(path)


# ------------------------------------------------------------------------------------------------ data quality
# canonical vital -> {unit: (low, high)} plausible (not normal) ranges, all ages incl. neonates
VITALS = {
    'temperature': {'c': (25.0, 45.0), 'f': (77.0, 113.0)},
    'heart_rate': {'bpm': (20, 300), '/min': (20, 300)},
    'resp_rate': {'/min': (2, 120), 'bpm': (2, 120), 'breaths/min': (2, 120)},
    'systolic': {'mmhg': (40, 300)},
    'diastolic': {'mmhg': (15, 200)},
    'spo2': {'%': (40, 100)},
    'weight': {'kg': (0.2, 400.0), 'g': (200, 400000), 'lb': (0.44, 880.0)},
    'height': {'cm': (20, 260), 'm': (0.2, 2.6), 'in': (8, 103)},
    'glucose': {'mg/dl': (10, 1500), 'mmol/l': (0.5, 85.0)},
    'head_circumference': {'cm': (15, 70)},
    'pain_score': {'/10': (0, 10)},
}
_VITAL_ALIASES = {
    'temp': 'temperature', 't': 'temperature', 'temperature': 'temperature', 'body_temperature': 'temperature',
    'hr': 'heart_rate', 'pulse': 'heart_rate', 'heart_rate': 'heart_rate', 'heartrate': 'heart_rate',
    'rr': 'resp_rate', 'resp': 'resp_rate', 'respiratory_rate': 'resp_rate', 'resp_rate': 'resp_rate',
    'sbp': 'systolic', 'systolic': 'systolic', 'dbp': 'diastolic', 'diastolic': 'diastolic',
    'spo2': 'spo2', 'sao2': 'spo2', 'o2_sat': 'spo2', 'oxygen_saturation': 'spo2', 'sat': 'spo2',
    'weight': 'weight', 'wt': 'weight', 'height': 'height', 'ht': 'height', 'length': 'height',
    'glucose': 'glucose', 'bg': 'glucose', 'blood_glucose': 'glucose', 'bgl': 'glucose',
    'hc': 'head_circumference', 'head_circumference': 'head_circumference', 'ofc': 'head_circumference',
    'pain': 'pain_score', 'pain_score': 'pain_score', 'bp': 'bp', 'blood_pressure': 'bp',
}
_UNIT_ALIASES = {'°c': 'c', 'degc': 'c', 'celsius': 'c', '℃': 'c', '°f': 'f', 'degf': 'f', 'fahrenheit': 'f',
                 '℉': 'f', 'beats/min': 'bpm', 'b/min': 'bpm', '/minute': '/min', 'per min': '/min',
                 'mm hg': 'mmhg', 'kgs': 'kg', 'lbs': 'lb', 'pound': 'lb', 'pounds': 'lb', 'grams': 'g',
                 'inch': 'in', 'inches': 'in', '"': 'in', 'mg/100ml': 'mg/dl', 'mmol': 'mmol/l', 'percent': '%',
                 'rpm': '/min', 'br/min': 'breaths/min'}
_VAL_UNIT = re.compile(r'^\s*([-+]?\d+(?:[.,]\d+)?)\s*(?:/\s*(\d+(?:[.,]\d+)?))?\s*(.*?)\s*$')


def _num(s):
    return float(str(s).replace(',', '.'))


def _norm_unit(u):
    u = str(u or '').strip().lower()
    return _UNIT_ALIASES.get(u, u)


# clinic_app stores vitals as entries {"values": {"bp_sys": 120, "temp_c": 37.1, ...}, "status": ...}: the unit
# is part of the key
_KEY_UNITS = {'temp_c': ('temperature', 'c'), 'temp_f': ('temperature', 'f'), 'pulse': ('heart_rate', 'bpm'),
              'resp': ('resp_rate', '/min'), 'bp_sys': ('systolic', 'mmhg'), 'bp_dia': ('diastolic', 'mmhg'),
              'spo2': ('spo2', '%'), 'weight_kg': ('weight', 'kg'), 'weight_lb': ('weight', 'lb'),
              'height_cm': ('height', 'cm'), 'height_in': ('height', 'in'), 'glucose_mgdl': ('glucose', 'mg/dl'),
              'glucose_mmol': ('glucose', 'mmol/l'), 'hc_cm': ('head_circumference', 'cm')}
_INACTIVE = ('entered_in_error', 'deleted', 'cancelled', 'canceled')


def _iter_vitals(record):
    """-> (name, value, unit or None, entry index or None)."""
    v = _get(record, 'vitals', 'observations', 'vital_signs', default=None)
    if v is None:
        return
    if isinstance(v, dict):
        for k, val in v.items():
            if isinstance(val, dict):
                yield str(k), _get(val, 'value', 'val', 'v'), _get(val, 'unit', 'units', 'uom'), None
            else:
                yield str(k), val, None, None
    elif isinstance(v, (list, tuple)):
        for i, item in enumerate(v):
            if not isinstance(item, dict):
                continue
            if str(item.get('status', '')).lower() in _INACTIVE:
                continue
            vals = item.get('values')
            if isinstance(vals, dict):
                for k, val in vals.items():
                    ku = _KEY_UNITS.get(str(k).lower())
                    yield str(k), val, (ku[1] if ku else None), i
                continue
            yield (str(_get(item, 'name', 'code', 'type', 'vital', default='?')),
                   _get(item, 'value', 'val', 'v'), _get(item, 'unit', 'units', 'uom'), i)


def _dq(code, field, msg, refs=()):
    return Suggestion(kind='data_quality', text=msg, code=code, field=field, refs=refs,
                      detail='data entry check only - no clinical interpretation')


def check_data_quality(record, today=None):
    """Read-only. -> list[Suggestion] for data-entry problems. Never interprets values clinically."""
    out = []
    today = today or dt.date.today()
    rid = str(_get(record, 'id', 'patient_id', 'mrn', default=''))
    refs = (rid,) if rid else ()
    pairs = collections.defaultdict(dict)          # entry index -> {'systolic': x, 'diastolic': y}
    for raw_name, val, unit, entry in _iter_vitals(record):
        norm = re.sub(r'[\s\-]+', '_', raw_name.strip().lower())
        key = _KEY_UNITS[norm][0] if norm in _KEY_UNITS else _VITAL_ALIASES.get(norm)
        field = f'vitals.{raw_name}' if entry is None else f'vitals[{entry}].{raw_name}'
        if val is None or str(val).strip() == '':
            continue
        m = _VAL_UNIT.match(str(val))
        if not m:
            out.append(_dq('vital_not_numeric', field, f'{raw_name}: value "{val}" is not a number', refs))
            continue
        a, b, trailing = m.group(1), m.group(2), m.group(3)
        u = _norm_unit(unit if unit not in (None, '') else trailing)
        if key is None:
            continue
        if key == 'bp' or b is not None:
            if b is None:
                out.append(_dq('bp_incomplete', field, f'{raw_name}: blood pressure needs systolic/diastolic', refs))
                continue
            if not u:
                out.append(_dq('vital_missing_unit', field, f'{raw_name}: unit missing (expected mmHg)', refs))
            elif u != 'mmhg':
                out.append(_dq('vital_unknown_unit', field, f'{raw_name}: unit "{u}" not recognised (mmHg)', refs))
            s, d = _num(a), _num(b)
            for nm, x in (('systolic', s), ('diastolic', d)):
                lo, hi = VITALS[nm]['mmhg']
                if not lo <= x <= hi:
                    out.append(_dq('vital_implausible', field, f'{raw_name}: {nm} {x:g} mmHg outside the '
                                   f'plausible range {lo}-{hi} (check for a typing error)', refs))
            if d >= s:
                out.append(_dq('bp_order', field, f'{raw_name}: diastolic {d:g} >= systolic {s:g} '
                               '(values swapped?)', refs))
            continue
        x = _num(a)
        ranges = VITALS[key]
        if not u:
            out.append(_dq('vital_missing_unit', field, f'{raw_name}: unit missing (expected one of '
                           f'{", ".join(ranges)})', refs))
            if not any(lo <= x <= hi for lo, hi in ranges.values()):
                out.append(_dq('vital_implausible', field, f'{raw_name}: {x:g} is implausible in every '
                               'accepted unit (check for a typing error)', refs))
            continue
        if u not in ranges:
            out.append(_dq('vital_unknown_unit', field, f'{raw_name}: unit "{u}" not recognised (expected one '
                           f'of {", ".join(ranges)})', refs))
            continue
        lo, hi = ranges[u]
        if not lo <= x <= hi:
            out.append(_dq('vital_implausible', field, f'{raw_name}: {x:g} {u} outside the plausible range '
                           f'{lo:g}-{hi:g} (check for a typing error or unit)', refs))
        if key in ('systolic', 'diastolic') and entry is not None:
            pairs[entry][key] = x
    for entry, p in sorted(pairs.items()):
        if 'systolic' in p and 'diastolic' in p and p['diastolic'] >= p['systolic']:
            out.append(_dq('bp_order', f'vitals[{entry}]', f'blood pressure: diastolic {p["diastolic"]:g} >= '
                           f'systolic {p["systolic"]:g} (values swapped?)', refs))
    dob = _parse_date(_get(record, 'dob', 'birth_date', 'birthDate', 'date_of_birth'))
    if dob is not None:
        if dob > today:
            out.append(_dq('dob_future', 'dob', f'date of birth {dob.isoformat()} is in the future', refs))
        elif (today - dob).days > 130 * 366:
            out.append(_dq('dob_implausible', 'dob', f'date of birth {dob.isoformat()} gives an age over 130',
                           refs))
    return out


def _parse_date(v):
    if v is None or v == '':
        return None
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    s = str(v).strip()[:10]
    for fmt in ('%Y-%m-%d', '%Y%m%d', '%d/%m/%Y', '%d.%m.%Y'):
        with contextlib.suppress(ValueError):
            return dt.datetime.strptime(s, fmt).date()
    return None


# ------------------------------------------------------------------------------------------------ duplicates
def _patient_name(p):
    n = _get(p, 'name', 'full_name', 'patient_name')
    if isinstance(n, dict):
        n = ' '.join(str(x) for x in (n.get('given'), n.get('family')) if x)
    if not n:
        n = ' '.join(str(x) for x in (_get(p, 'given', 'given_name', 'first_name'),
                                       _get(p, 'family', 'family_name', 'last_name', 'surname')) if x)
    return str(n or '')


def _dob_relation(a, b):
    if a is None or b is None:
        return None
    if a == b:
        return 'same'
    if a.year == b.year and a.month == b.day and a.day == b.month:
        return 'day/month swapped'
    sa, sb = a.strftime('%Y%m%d'), b.strftime('%Y%m%d')
    if sum(x != y for x, y in zip(sa, sb)) == 1:
        return 'one digit differs'
    return None


def find_duplicate_patients(patients, threshold=0.86):
    """Read-only. Fuzzy name (accents, case, order, punctuation ignored) + DOB (equal, d/m swapped, one-digit
    typo). -> list[Suggestion] kind duplicate_patient, refs = (id_a, id_b). Never merges anything."""
    rows = []
    for i, p in enumerate(patients):
        nm = _norm_name(_patient_name(p))
        if not nm:
            continue
        rows.append((str(_get(p, 'id', 'patient_id', 'mrn', default=i)), nm, ' '.join(sorted(nm.split())),
                     _parse_date(_get(p, 'dob', 'birth_date', 'birthDate', 'date_of_birth'))))
    blocks = collections.defaultdict(list)
    for idx, r in enumerate(rows):
        d = r[3]
        if d is None:
            continue
        blocks[('y', d.year)].append(idx)
        blocks[('md', min(d.month, d.day), max(d.month, d.day))].append(idx)
    seen, out = set(), []
    for members in blocks.values():
        for x in range(len(members)):
            for y in range(x + 1, len(members)):
                i, j = members[x], members[y]
                if (i, j) in seen:
                    continue
                seen.add((i, j))
                a, b = rows[i], rows[j]
                rel = _dob_relation(a[3], b[3])
                if rel is None:
                    continue
                sim = max(difflib.SequenceMatcher(None, a[2], b[2]).ratio(),
                          difflib.SequenceMatcher(None, a[1], b[1]).ratio())
                need = threshold if rel == 'same' else max(threshold, 0.93)
                if sim >= need:
                    out.append(Suggestion(kind='duplicate_patient', code='possible_duplicate', refs=(a[0], b[0]),
                                          score=round(sim, 3),
                                          text=f'records {a[0]} and {b[0]} may be the same patient',
                                          detail=f'name similarity {sim:.2f}; date of birth {rel}. Review '
                                                 'both records before any merge.'))
    out.sort(key=lambda s: -s.score)
    return out


# ------------------------------------------------------------------------------------------------ wording guard
_ADVICE = re.compile(r'\b(diagnos\w*|prescrib\w*|dos(e|age|ing)|mg|mcg|ml|tablet\w*|recommend\w*|treat\w*|'
                     r'therap\w*|should take|start(ing)? on|administer\w*|increase|decrease|differential)\b', re.I)


_NEGATION = re.compile(r"\b(no|not|non|none|never|without|denies|denied|negative|absent|nil|neither|nor|"
                       r"ruled out|r/o|n't)\b|n't\b", re.I)


def _wording_ok(original, proposal):
    if not proposal or len(proposal) > max(80, 2 * len(original)):
        return False
    if collections.Counter(re.findall(r'\d+(?:[.,]\d+)?', proposal)) != \
            collections.Counter(re.findall(r'\d+(?:[.,]\d+)?', original)):
        return False                      # a number added, dropped or changed = clinical content changed
    if len(_NEGATION.findall(proposal)) != len(_NEGATION.findall(original)):
        return False                      # "no fever" -> "fever" (or the reverse) flips a finding
    new_advice = {m.group(0).lower() for m in _ADVICE.finditer(proposal)} - \
                 {m.group(0).lower() for m in _ADVICE.finditer(original)}
    return not new_advice


# ------------------------------------------------------------------------------------------------ learner
class SelfLearner:
    """Per-clinician adaptive helper. Thread-safe. Read-only towards records."""

    def __init__(self, profile_dir, clinician_id, vault=None, use_ollama=True, ollama_model=None,
                 min_seen=MIN_SEEN, allow_dpapi=True):
        cid = str(clinician_id or '').strip()
        if not cid:
            raise ValueError('clinician_id required (profiles are per clinician)')
        self.clinician_id = cid
        self.profile_dir = os.path.abspath(profile_dir)
        os.makedirs(self.profile_dir, exist_ok=True)
        safe = hashlib.sha256(cid.encode('utf-8')).hexdigest()[:24]
        self.path = os.path.join(self.profile_dir, f'{safe}.gcsl')
        self.min_seen = max(1, int(min_seen))
        self.use_ollama = bool(use_ollama)
        self.ollama_model = (ollama_model or os.environ.get('GORGON_COVENANT_OLLAMA_MODEL')
                             or os.environ.get('GORGON_CHART_OLLAMA_MODEL') or 'llama3.2')
        self._ollama_down_until = 0.0
        self._lock = threading.RLock()
        self._vault, self._allow_dpapi = vault, allow_dpapi
        self._codec = _pick_codec(vault, allow_dpapi)
        self.save_error = self.load_error = ''
        self._empty()
        self.load()

    # -------------------------------------------------------------- state
    def _empty(self):
        self.enabled = True
        self.phrases = collections.Counter()      # masked phrase -> number of signed notes containing it
        self.templates = collections.defaultdict(collections.Counter)   # note_type -> skeleton -> count
        self.order_sets = collections.Counter()   # tuple(sorted orders) -> count
        self.forgotten = set()                    # sha256 of forgotten phrases (never relearned)
        self.learned_notes = set()                # sha256 of note ids/texts already learned (idempotent)

    def status(self):
        return {'enabled': self.enabled, 'clinician': self.clinician_id, 'phrases': len(self.phrases),
                'templates': sum(len(c) for c in self.templates.values()), 'order_sets': len(self.order_sets),
                'notes_learned': len(self.learned_notes), 'profile': self.path,
                'encryption': self._codec.name, 'protected': self._codec.name != 'none',
                'ollama': self.ollama_available(probe=False), 'save_error': self.save_error,
                'load_error': self.load_error, 'local_only': True, 'label': SUGGESTION_LABEL}

    def set_enabled(self, on):
        with self._lock:
            self.enabled = bool(on)
            self.save()

    # -------------------------------------------------------------- learning
    def learn_from_note(self, note):
        """Learn from ONE signed note by this clinician. Unsigned notes, other clinicians' notes and repeats are
        ignored. The note is never modified. -> number of new items counted."""
        if not self.enabled or self.load_error or not is_signed(note):
            return 0
        author = note_author(note)
        if author and author != self.clinician_id:
            return 0
        text = note_text(note)
        nid = _get(note, 'id', 'note_id', 'uuid')
        key = hashlib.sha256(f'id:{nid}'.encode('utf-8') if nid is not None else text.encode('utf-8')).hexdigest()
        with self._lock:
            if key in self.learned_notes:
                return 0
            self.learned_notes.add(key)
            names = _name_tokens(note)
            n = 0
            phrases = set()
            skeleton = []
            for raw in text.splitlines():
                line = _clean_line(raw)
                if not line:
                    continue
                hm = _HEADER.match(raw)
                if hm:
                    skeleton.append((hm.group(1) or hm.group(2)).strip() + ':')
                    continue
                im = _INLINE_HEADER.match(raw)
                if im and len(skeleton) < 40:
                    skeleton.append(im.group(1).strip() + ':')
                for seg in re.split(r'(?<=[.;!?])\s+', _mask(line)):
                    phrases.update(self._phrases_of(seg, names))
            for p in phrases:
                if hashlib.sha256(p.casefold().encode('utf-8')).hexdigest() in self.forgotten:
                    continue
                self.phrases[p] += 1
                n += 1
            if len(skeleton) >= 2:
                ntype = str(_get(note, 'note_type', 'type', 'kind', default='note')).lower()
                self.templates[ntype]['\n'.join(skeleton)] += 1
                n += 1
            orders = _get(note, 'orders', 'order_set', 'orders_placed', default=None)
            if isinstance(orders, (list, tuple)) and orders:
                names_o = sorted({self._order_name(o) for o in orders} - {''})
                if names_o:
                    self.order_sets[tuple(names_o)] += 1
                    n += 1
            self._prune()
            self.save()
            return n

    def learn_from_notes(self, notes):
        return sum(self.learn_from_note(n) for n in notes)

    @staticmethod
    def _order_name(o):
        if isinstance(o, dict):
            o = _get(o, 'name', 'display', 'code', 'text', default='')
        return _clean_line(str(o))[:120]

    @staticmethod
    def _phrases_of(seg, names):
        toks = seg.split()
        if names:
            if any(_norm_name(t) in names for t in toks):
                # drop the whole segment rather than learn text around a patient's name
                return set()
        out = set()
        if 3 <= len(toks) <= 25 and len(seg) <= 160:
            out.add(seg.strip())
        for size in (3, 4, 5, 6):
            for i in range(0, max(0, len(toks) - size + 1)):
                out.add(' '.join(toks[i:i + size]))
        return {p for p in out if not set(p.replace('___', '').strip(' .,;:/')) <= set(' _/.-,')}

    def _prune(self):
        if len(self.phrases) > MAX_PHRASES:
            keep = self.phrases.most_common(int(MAX_PHRASES * 0.8))
            self.phrases = collections.Counter(dict(keep))

    # -------------------------------------------------------------- suggestions (read-only)
    def autocomplete(self, prefix, limit=5):
        if not self.enabled:
            return []
        pre = _WS.sub(' ', str(prefix)).lstrip()
        if len(pre) < 2:
            return []
        cf = pre.casefold()
        with self._lock:
            cands = [(c, p) for p, c in self.phrases.items()
                     if c >= self.min_seen and len(p) > len(pre) and p.casefold().startswith(cf)]
        cands.sort(key=lambda cp: (-cp[0], -len(cp[1]), cp[1]))
        out, seen = [], set()
        for c, p in cands:
            if p.casefold() in seen:
                continue
            seen.add(p.casefold())
            out.append(Suggestion(kind='autocomplete', text=p, score=float(c), code='your_phrase',
                                  detail=f'from your own signed notes (used {c}x)'))
            if len(out) >= limit:
                break
        return out

    def suggest_templates(self, note_type='note', limit=3):
        if not self.enabled:
            return []
        with self._lock:
            c = self.templates.get(str(note_type).lower()) or collections.Counter()
            items = [(sk, n) for sk, n in c.most_common() if n >= self.min_seen][:limit]
        return [Suggestion(kind='template', text=sk, score=float(n), code='your_template',
                           detail=f'section layout you used in {n} signed {note_type} notes') for sk, n in items]

    def suggest_order_sets(self, current_orders=(), limit=3):
        if not self.enabled:
            return []
        cur = {self._order_name(o) for o in current_orders} - {''}
        with self._lock:
            sets = [(s, n) for s, n in self.order_sets.items() if n >= self.min_seen]
        out = []
        for s, n in sorted(sets, key=lambda x: (-x[1], x[0])):
            if cur and not cur <= set(s):
                continue
            missing = [o for o in s if o not in cur]
            if not missing:
                continue
            out.append(Suggestion(kind='order_set', text='\n'.join(s), score=float(n), code='your_order_set',
                                  refs=tuple(missing),
                                  detail=f'order set you placed together {n}x; adds: {", ".join(missing)}'))
            if len(out) >= limit:
                break
        return out

    def check_record(self, record):
        return check_data_quality(record) if self.enabled else []

    def find_duplicates(self, patients):
        return find_duplicate_patients(patients) if self.enabled else []

    # -------------------------------------------------------------- local Ollama (optional)
    def ollama_available(self, probe=True):
        if not self.use_ollama or time.monotonic() < self._ollama_down_until:
            return False
        if not probe:
            return True
        return self._ollama('GET', '/api/tags', None) is not None

    def _ollama(self, method, path, body):
        if not self.use_ollama or time.monotonic() < self._ollama_down_until:
            return None
        conn = None
        try:
            conn = http.client.HTTPConnection(OLLAMA_HOST, OLLAMA_PORT, timeout=OLLAMA_TIMEOUT)
            data = json.dumps(body).encode('utf-8') if body is not None else None
            conn.request(method, path, body=data, headers={'Content-Type': 'application/json'})
            r = conn.getresponse()
            if r.status != 200:
                raise OSError(f'ollama http {r.status}')
            return json.loads(r.read(2_000_000).decode('utf-8'))
        except Exception:
            self._ollama_down_until = time.monotonic() + OLLAMA_RETRY_S
            return None
        finally:
            if conn is not None:
                with contextlib.suppress(Exception):
                    conn.close()

    def wording_suggestion(self, text):
        """Clearer wording for the clinician's own sentence, or None (Ollama absent, slow, or the proposal adds
        numbers / advice terms). Never interprets the content."""
        if not self.enabled or not str(text).strip() or len(str(text)) > 2000:
            return None
        prompt = ('Rewrite the following clinical note sentence for clarity and grammar only. Keep every fact, '
                  'number and term exactly; do not add, remove or interpret anything; no advice, no diagnosis. '
                  'Reply with the rewritten sentence only.\n\n' + str(text))
        r = self._ollama('POST', '/api/generate', {'model': self.ollama_model, 'prompt': prompt, 'stream': False,
                                                    'options': {'temperature': 0.1, 'num_predict': 200}})
        if not isinstance(r, dict):
            return None
        prop = _clean_line(str(r.get('response', ''))).strip('"\u201c\u201d')
        if prop == _clean_line(str(text)) or not _wording_ok(str(text), prop):
            return None
        return Suggestion(kind='wording', text=prop, code='local_wording',
                          detail='wording only, from a local model on this machine')

    # -------------------------------------------------------------- forget / reset
    def forget_phrase(self, phrase):
        """Remove a phrase (and every learned phrase containing it) and never learn it again."""
        p = _clean_line(_mask(str(phrase)))
        cf = p.casefold()
        with self._lock:
            gone = [k for k in self.phrases if cf and cf in k.casefold()]
            for k in gone:
                del self.phrases[k]
                self.forgotten.add(hashlib.sha256(k.casefold().encode('utf-8')).hexdigest())
            self.forgotten.add(hashlib.sha256(cf.encode('utf-8')).hexdigest())
            self.save()
            return len(gone)

    def forget_order_set(self, orders):
        key = tuple(sorted({self._order_name(o) for o in orders} - {''}))
        with self._lock:
            n = self.order_sets.pop(key, 0)
            self.save()
            return bool(n)

    def forget_template(self, note_type, skeleton):
        with self._lock:
            n = self.templates.get(str(note_type).lower(), {}).pop(skeleton, 0)
            self.save()
            return bool(n)

    def reset(self):
        """Forget everything for this clinician: profile shredded, memory cleared."""
        with self._lock:
            _shred(self.path)
            _shred(self.path + '.tmp')
            self._empty()
            self.load_error = self.save_error = ''

    # -------------------------------------------------------------- persistence
    def _to_json(self):
        return {'v': 1, 'clinician': self.clinician_id, 'enabled': self.enabled,
                'phrases': dict(self.phrases),
                'templates': {k: dict(v) for k, v in self.templates.items()},
                'order_sets': [[list(k), n] for k, n in self.order_sets.items()],
                'forgotten': sorted(self.forgotten), 'learned_notes': sorted(self.learned_notes)}

    def save(self):
        """Persist the profile. Never raises: a locked vault or a full disk leaves learning in memory only and
        is reported in status()['save_error']. While the stored profile cannot be read (load_error) nothing is
        written over it until reset()."""
        with self._lock:
            if self.load_error:
                return False
            try:
                raw = json.dumps(self._to_json(), ensure_ascii=False, separators=(',', ':')).encode('utf-8')
                sealed = self._codec.seal(raw)
                blob = MAGIC + self._codec.name.encode('ascii') + b'\n' + base64.b64encode(sealed)
                _atomic_write(self.path, blob)
                self.save_error = ''
                return True
            except Exception as e:                       # noqa: BLE001 - helper must never break the app
                self.save_error = f'{e.__class__.__name__}: {e}'
                _log.warning('%s profile not saved (%s)', LOG_PREFIX, self.save_error)
                return False

    def load(self):
        """-> True when a profile was loaded. A profile this machine cannot open (other user, missing vault,
        damaged file) is left untouched, learning is paused for it and status()['load_error'] says why;
        reset() starts a fresh profile. A profile sealed with another available codec is re-sealed."""
        with self._lock:
            self.load_error = ''
            if not os.path.exists(self.path):
                return False
            try:
                with open(self.path, 'rb') as f:
                    blob = f.read()
                if not blob.startswith(MAGIC):
                    raise ValueError('not a Stark Covenant learner profile')
                codec_name, _, body = blob[len(MAGIC):].partition(b'\n')
                codec_name = codec_name.decode('ascii', 'replace')
                codec = self._codec if codec_name == self._codec.name else \
                    _codec_named(codec_name, self._vault, self._allow_dpapi)
                if codec is None:
                    raise ValueError(f'profile sealed with {codec_name!r}, which this machine cannot open now')
                d = json.loads(codec.open(base64.b64decode(body, validate=True)).decode('utf-8'))
                if not isinstance(d, dict) or d.get('clinician') != self.clinician_id:
                    raise ValueError('profile belongs to another clinician')
            except Exception as e:                       # noqa: BLE001
                self.load_error = f'{e.__class__.__name__}: {e}'
                _log.warning('%s profile not loaded (%s); learning paused until reset()', LOG_PREFIX,
                             self.load_error)
                return False
            migrate = codec is not self._codec
            self._empty()
            self.enabled = bool(d.get('enabled', True))
            self.phrases.update(d.get('phrases', {}))
            for k, v in d.get('templates', {}).items():
                self.templates[k].update(v)
            for k, n in d.get('order_sets', []):
                self.order_sets[tuple(k)] = n
            self.forgotten = set(d.get('forgotten', []))
            self.learned_notes = set(d.get('learned_notes', []))
            if migrate:
                self.save()
            return True


def _demo():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        sl = SelfLearner(d, 'dr.demo', use_ollama=False)
        for i in range(3):
            sl.learn_from_note({'id': i, 'status': 'signed', 'signed_by': 'dr.demo', 'note_type': 'soap',
                                'text': f'S:\nCough for {i + 2} days.\nO:\nChest clear to auscultation bilaterally.'
                                        '\nA:\nViral upper respiratory infection.\nP:\nReturn if worse.',
                                'orders': ['CBC', 'CRP']})
        print(json.dumps(sl.status(), indent=1))
        for s in sl.autocomplete('Chest cl') + sl.suggest_templates('soap'):
            print(s.kind, '|', s.text.replace('\n', ' / '), '|', s.label)
        for s in check_data_quality({'id': 'p1', 'vitals': {'temp': '38.5', 'hr': '900 bpm', 'bp': '80/120'}}):
            print(s.code, '|', s.text)


if __name__ == '__main__':
    _demo()
