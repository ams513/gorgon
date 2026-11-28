"""Stark Covenant - interop_bridge: HL7 v2.x <-> internal record <-> FHIR R4 JSON.

Standard library only, Python 3.10+.

HL7 v2 (pipe-and-hat, default version 2.5.1, MSH-3 sending application "GORGON_COVENANT", MSH-18 UNICODE UTF-8)
  * parse_hl7(text|bytes) -> Hl7Message (any message; segments / fields / repetitions / components /
    subcomponents, unescaped on access). Delimiters are read from MSH-1 / MSH-2.
  * escaping per HL7 v2 2.7 "escape sequences": \\F\\ field, \\S\\ component, \\T\\ subcomponent, \\R\\ repetition,
    \\E\\ escape; \\Xhh..\\ hex (decoded as UTF-8, or Latin-1 if not valid UTF-8), \\.br\\ -> newline on parse;
    CR / LF in data are written as \\X0D\\ / \\X0A\\. Unknown escape sequences are kept literally.
  * build_adt(record, "A01" | "A03" | "A08") and build_oru_r01(record) -> message text (segments end with CR).
  * hl7_to_record(msg) -> internal record; build_ack(msg) -> ACK; mllp_wrap / mllp_unwrap.

FHIR R4 JSON
  * record_to_fhir_bundle(record) -> transaction Bundle (urn:uuid fullUrls, POST + ifNoneExist for Patient and
    Encounter) holding Patient, Encounter, Observation (vital signs with LOINC codes + UCUM units, blood pressure
    as panel 85354-9 with 8480-6 / 8462-4 components), Condition, MedicationStatement, AllergyIntolerance.
  * fhir_bundle_to_record(bundle) -> internal record.
  * validate_fhir(resource_or_bundle) -> list of issues. STRUCTURAL checks only: JSON shape, cardinality,
    unknown elements, primitive formats, required-binding codes, choice-type exclusivity, a set of R4 invariants
    (ele-1, obs-6, obs-7, con-4, con-5, ait-1, ait-2, bdl-1/2/3/4/7), in-bundle reference resolution and a subset of
    the vital-signs profile (LOINC code, UCUM system + allowed unit, effective[x], subject). This is NOT the
    official HL7 FHIR validator: no StructureDefinition snapshot, no terminology server, no full FHIRPath.

Internal record (plain dict, every key optional except where noted):
  {"event": "A01", "message_id": str, "sent": iso,
   "patient": {"id"*, "id_system", "id_authority", "family", "given": [..], "birth_date", "sex",
               "phone", "address": {"line": [..], "city", "state", "postal_code", "country"}, "language"},
   "encounter": {"id", "class": inpatient|outpatient|emergency, "status", "start", "end",
                 "location": {"ward", "room", "bed"}, "attending": {"id", "family", "given"}},
   "observations": [{"id", "code", "system", "display", "value", "unit", "value_type": NM|ST|TX, "time",
                     "status", "interpretation", "reference_range", "components": [{"code", "display", "value",
                     "unit"}]}],
   "conditions": [{"id", "code", "system", "display", "clinical_status", "verification_status", "onset"}],
   "medications": [{"id", "code", "system", "display", "status", "dosage", "start"}],
   "allergies": [{"id", "code", "system", "display", "category", "criticality", "clinical_status",
                  "verification_status", "reaction", "severity"}],
   "notes": [mapping notes]}
Times are ISO 8601 strings (date or dateTime with offset).

Mapping losses (reported by hl7_losses()): HL7 ADT/ORU carries no MedicationStatement and no allergy criticality /
clinical status; FHIR-only fields are dropped when going to HL7. HL7 AL1 / DG1 carry no status, so HL7 -> record
assumes clinical_status "active" and says so in record["notes"].
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
import uuid

APP_ID = "GORGON_COVENANT"
DEFAULT_VERSION = "2.5.1"

__all__ = [
    "APP_ID", "Hl7Message", "parse_hl7", "hl7_escape", "hl7_unescape", "build_adt", "build_oru_r01",
    "hl7_to_record", "build_ack", "mllp_wrap", "mllp_unwrap", "hl7_losses", "hl7_ts_to_iso", "iso_to_hl7_ts",
    "record_to_fhir_bundle", "fhir_bundle_to_record", "validate_fhir", "VITALS", "SYSTEMS",
]

# ======================================================================================================
# Code systems and vital signs
# ======================================================================================================

LOINC = "http://loinc.org"
UCUM = "http://unitsofmeasure.org"
SYSTEMS = {  # FHIR URI <-> HL7 table 0396 coding-system mnemonic
    LOINC: "LN", "http://hl7.org/fhir/sid/icd-10": "I10", "http://hl7.org/fhir/sid/icd-10-cm": "I10C",
    "http://snomed.info/sct": "SCT", "http://www.nlm.nih.gov/research/umls/rxnorm": "RXNORM", UCUM: "UCUM",
}
SYSTEMS_BY_HL7 = {v: k for k, v in SYSTEMS.items()}

# LOINC code -> (display, allowed UCUM codes) - the FHIR R4 vital signs profile set
VITALS = {
    "85353-1": ("Vital signs panel", ()),
    "9279-1": ("Respiratory rate", ("/min",)),
    "8867-4": ("Heart rate", ("/min",)),
    "2708-6": ("Oxygen saturation in Arterial blood", ("%",)),
    "59408-5": ("Oxygen saturation in Arterial blood by Pulse oximetry", ("%",)),
    "8310-5": ("Body temperature", ("Cel", "[degF]")),
    "8302-2": ("Body height", ("cm", "[in_i]")),
    "9843-4": ("Head Occipital-frontal circumference", ("cm", "[in_i]")),
    "29463-7": ("Body weight", ("kg", "g", "[lb_av]")),
    "39156-5": ("Body mass index (BMI) [Ratio]", ("kg/m2",)),
    "85354-9": ("Blood pressure panel with all children optional", ()),
    "8480-6": ("Systolic blood pressure", ("mm[Hg]",)),
    "8462-4": ("Diastolic blood pressure", ("mm[Hg]",)),
}
BP_PANEL, BP_SYS, BP_DIA = "85354-9", "8480-6", "8462-4"
UCUM_DISPLAY = {"/min": "/min", "%": "%", "Cel": "Cel", "[degF]": "[degF]", "cm": "cm", "[in_i]": "[in_i]",
                "kg": "kg", "g": "g", "[lb_av]": "[lb_av]", "kg/m2": "kg/m2", "mm[Hg]": "mm[Hg]"}

_V3_ACT = "http://terminology.hl7.org/CodeSystem/v3-ActCode"
_CLASS = {"inpatient": ("I", "IMP", "inpatient encounter"), "outpatient": ("O", "AMB", "ambulatory"),
          "emergency": ("E", "EMER", "emergency")}
_CLASS_BY_HL7 = {v[0]: k for k, v in _CLASS.items()}
_CLASS_BY_V3 = {v[1]: k for k, v in _CLASS.items()}
_SEX = {"F": "female", "M": "male", "O": "other", "U": "unknown", "A": "other", "N": "unknown"}
_SEX_HL7 = {"female": "F", "male": "M", "other": "O", "unknown": "U"}
_OBS_STATUS = {"F": "final", "P": "preliminary", "C": "corrected", "W": "entered-in-error", "X": "cancelled",
               "I": "registered", "R": "preliminary"}
_OBS_STATUS_HL7 = {"final": "F", "preliminary": "P", "corrected": "C", "amended": "C", "entered-in-error": "W",
                   "cancelled": "X", "registered": "I"}
_ALG_CAT = {"DA": "medication", "FA": "food", "EA": "environment", "LA": "environment", "PA": "environment",
            "AA": "environment"}
_ALG_CAT_HL7 = {"medication": "DA", "food": "FA", "environment": "EA", "biologic": "MA"}
_ALG_SEV = {"SV": "severe", "MO": "moderate", "MI": "mild"}
_ALG_SEV_HL7 = {v: k for k, v in _ALG_SEV.items()}
_INTERP = "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation"
_COND_CLIN = "http://terminology.hl7.org/CodeSystem/condition-clinical"
_COND_VER = "http://terminology.hl7.org/CodeSystem/condition-ver-status"
_AIT_CLIN = "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical"
_AIT_VER = "http://terminology.hl7.org/CodeSystem/allergyintolerance-verification"
_OBS_CAT = "http://terminology.hl7.org/CodeSystem/observation-category"
_DG_TYPE_VER = {"A": "provisional", "W": "provisional", "F": "confirmed"}

# ======================================================================================================
# HL7 v2: escaping, parsing, building
# ======================================================================================================


class Encoding:
    def __init__(self, field="|", comp="^", rep="~", esc="\\", sub="&", trunc=""):
        self.field, self.comp, self.rep, self.esc, self.sub, self.trunc = field, comp, rep, esc, sub, trunc

    @property
    def msh2(self):
        return self.comp + self.rep + self.esc + self.sub + self.trunc


DEFAULT_ENC = Encoding()


def hl7_escape(s, enc=DEFAULT_ENC):
    """Escape data text for one leaf (component / subcomponent) value."""
    if s is None:
        return ""
    s = str(s)
    e = enc.esc
    if s == '""':
        # a literal "" would read back as the HL7 explicit-null value; hex-escape it
        return e + "X2222" + e
    out = []
    for ch in s:
        if ch == e:
            out.append(e + "E" + e)
        elif ch == enc.field:
            out.append(e + "F" + e)
        elif ch == enc.comp:
            out.append(e + "S" + e)
        elif ch == enc.sub:
            out.append(e + "T" + e)
        elif ch == enc.rep:
            out.append(e + "R" + e)
        elif enc.trunc and ch == enc.trunc:
            out.append(e + "P" + e)
        elif ch == "\r":
            out.append(e + "X0D" + e)
        elif ch == "\n":
            out.append(e + "X0A" + e)
        else:
            out.append(ch)
    return "".join(out)


def hl7_unescape(s, enc=DEFAULT_ENC):
    e = enc.esc
    if e not in s:
        return s
    out = []
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        if ch == e:
            j = s.find(e, i + 1)
            if j < 0:
                out.append(s[i:])
                break
            seq = s[i + 1:j]
            rep = {"F": enc.field, "S": enc.comp, "T": enc.sub, "R": enc.rep, "E": e, "P": enc.trunc or None,
                   ".br": "\n"}.get(seq)
            if rep is not None:
                out.append(rep)
            elif seq[:1] == "X" and len(seq) > 1 and len(seq) % 2 == 1 and re.fullmatch(r"[0-9A-Fa-f]+", seq[1:]):
                raw = bytes.fromhex(seq[1:])
                try:
                    out.append(raw.decode("utf-8"))
                except UnicodeDecodeError:
                    out.append(raw.decode("latin-1"))
            else:
                out.append(s[i:j + 1])  # unknown (\H\, \N\, \Cxxyy\ ...) kept literally
            i = j + 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


class Rep(list):
    """Marks a field value as repeating: Rep([value1, value2])."""


def _enc_leaf(v, enc):
    return hl7_escape(v, enc)


def _enc_component(c, enc):
    if isinstance(c, (list, tuple)):
        return enc.sub.join(_enc_leaf(x, enc) for x in c).rstrip(enc.sub)
    return _enc_leaf(c, enc)


def _enc_value(v, enc):
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return enc.comp.join(_enc_component(c, enc) for c in v).rstrip(enc.comp)
    return _enc_leaf(v, enc)


def _enc_field(v, enc):
    if isinstance(v, Rep):
        return enc.rep.join(_enc_value(x, enc) for x in v)
    return _enc_value(v, enc)


def build_segment(name, fields, enc=DEFAULT_ENC):
    """fields: {field_number: value}; value is str, list of components (a component may be a list of
    subcomponents) or Rep([...]). MSH numbering is handled (MSH-1 / MSH-2 are written automatically)."""
    top = max(fields) if fields else 0
    if name == "MSH":
        parts = ["MSH", enc.msh2] + [_enc_field(fields.get(k), enc) for k in range(3, top + 1)]
        return parts[0] + enc.field + enc.field.join(parts[1:])
    parts = [name] + [_enc_field(fields.get(k), enc) for k in range(1, top + 1)]
    return enc.field.join(parts).rstrip(enc.field)


class Segment:
    def __init__(self, name, raw_fields, enc):
        self.name, self.raw, self.enc = name, raw_fields, enc

    def field_raw(self, n):
        """Raw (escaped) field n, HL7 1-based numbering (MSH-1 is the field separator)."""
        if self.name == "MSH":
            if n == 1:
                return self.enc.field
            idx = n - 1
        else:
            idx = n
        return self.raw[idx] if 0 < idx < len(self.raw) else ""

    def get(self, n, comp=1, sub=1, rep=1):
        """Unescaped value of field n, repetition rep, component comp, subcomponent sub ('' when absent;
        None for the HL7 explicit null "")."""
        raw = self.field_raw(n)
        if self.name == "MSH" and n in (1, 2):
            return raw
        reps = raw.split(self.enc.rep)
        if rep > len(reps):
            return ""
        comps = reps[rep - 1].split(self.enc.comp)
        if comp > len(comps):
            return ""
        subs = comps[comp - 1].split(self.enc.sub)
        if sub > len(subs):
            return ""
        val = subs[sub - 1]
        if val == '""':
            return None
        return hl7_unescape(val, self.enc)

    def reps(self, n):
        raw = self.field_raw(n)
        return len(raw.split(self.enc.rep)) if raw else 0


class Hl7Message:
    def __init__(self, segments, enc):
        self.segments, self.enc = segments, enc

    def all(self, name):
        return [s for s in self.segments if s.name == name]

    def first(self, name):
        for s in self.segments:
            if s.name == name:
                return s
        return None

    @property
    def msh(self):
        return self.segments[0]

    @property
    def message_type(self):
        m = self.msh
        return m.get(9, 1), m.get(9, 2), m.get(9, 3)


def parse_hl7(data):
    """Parse an HL7 v2 message (str or bytes; bytes decoded per MSH-18, UTF-8 by default)."""
    if isinstance(data, (bytes, bytearray)):
        head = bytes(data[:2048])
        charset = "utf-8"
        m = re.match(rb"MSH(.)", head.lstrip(b"\x0b"))
        if m:
            fs = m.group(1)
            first = head.split(b"\r")[0].split(b"\n")[0]
            parts = first.split(fs)
            if len(parts) > 17:
                cs = parts[17].decode("ascii", "replace").strip().upper()
                charset = {"UNICODE UTF-8": "utf-8", "8859/1": "latin-1", "ASCII": "ascii", "": "utf-8"}.get(cs, "utf-8")
        data = bytes(data).decode(charset)
    text = mllp_unwrap(data) if data.startswith("\x0b") else data
    text = text.replace("\r\n", "\r").replace("\n", "\r")
    lines = [ln for ln in text.split("\r") if ln.strip()]
    if not lines or not lines[0].startswith("MSH") or len(lines[0]) < 8:
        raise ValueError("not an HL7 v2 message: first segment must be MSH")
    fs = lines[0][3]
    msh2 = lines[0][4:].split(fs, 1)[0]
    if len(msh2) < 4:
        raise ValueError("MSH-2 encoding characters must have at least 4 characters")
    enc = Encoding(fs, msh2[0], msh2[1], msh2[2], msh2[3], msh2[4] if len(msh2) > 4 else "")
    segs = []
    for ln in lines:
        name = ln[:3]
        if not re.fullmatch(r"[A-Z][A-Z0-9]{2}", name):
            raise ValueError("bad segment name %r" % name)
        segs.append(Segment(name, ln.split(fs), enc))
    return Hl7Message(segs, enc)


def mllp_wrap(text):
    return "\x0b" + text + "\x1c\r"


def mllp_unwrap(text):
    t = text
    if t.startswith("\x0b"):
        t = t[1:]
    end = t.find("\x1c")
    return t[:end] if end >= 0 else t


# ---- time ---------------------------------------------------------------------------------------------

_HL7_TS = re.compile(r"^(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\.\d{1,4})?([+-]\d{4})?$")


def _local_offset():
    off = dt.datetime.now().astimezone().utcoffset() or dt.timedelta(0)
    mins = int(off.total_seconds() // 60)
    return "%s%02d:%02d" % ("+" if mins >= 0 else "-", abs(mins) // 60, abs(mins) % 60)


def hl7_ts_to_iso(ts, default_tz=None):
    """HL7 DTM/TS -> ISO 8601 (FHIR date or dateTime). A time without offset gets default_tz (default: this
    machine's current offset) because FHIR dateTime with a time requires one."""
    if not ts:
        return None
    m = _HL7_TS.match(ts.strip())
    if not m:
        raise ValueError("bad HL7 timestamp %r" % ts)
    y, mo, d, h, mi, s, frac, tz = m.groups()
    if h is None:
        return "-".join(x for x in (y, mo, d) if x)
    if mo is None or d is None:
        raise ValueError("HL7 timestamp with time but no full date: %r" % ts)
    out = "%s-%s-%sT%s:%s:%s%s" % (y, mo, d, h, mi or "00", s or "00", frac or "")
    if tz:
        out += "%s:%s" % (tz[:3], tz[3:])
    else:
        out += default_tz or _local_offset()
    return out


def iso_to_hl7_ts(iso):
    if not iso:
        return ""
    m = re.match(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?(?:T(\d{2}):(\d{2})(?::(\d{2})(\.\d+)?)?(Z|[+-]\d{2}:\d{2})?)?$",
                 iso.strip())
    if not m:
        raise ValueError("bad ISO date/time %r" % iso)
    y, mo, d, h, mi, s, frac, tz = m.groups()
    out = y + (mo or "") + (d or "")
    if h:
        out += h + mi + (s or "00") + ((frac or "")[:5])
        if tz:
            out += "+0000" if tz == "Z" else tz.replace(":", "")
    return out


# ---- record -> HL7 ------------------------------------------------------------------------------------

def _now_iso():
    return dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


def _cwe(code, display, system):
    if not code and not display:
        return None
    return [code or "", display or "", SYSTEMS.get(system, system or "")]


def _msh(record, msg_type, enc, facility, recv_app, recv_fac, version, message_id, sent):
    return build_segment("MSH", {
        3: APP_ID, 4: facility, 5: recv_app, 6: recv_fac, 7: iso_to_hl7_ts(sent), 9: msg_type,
        10: message_id, 11: "P", 12: version, 18: "UNICODE UTF-8"}, enc)


def _pid(p, enc):
    if not p or not p.get("id"):
        raise ValueError("record['patient']['id'] is required")
    sysuri = p.get("id_system") or ""
    if sysuri.startswith("urn:oid:"):
        hd = [p.get("id_authority") or "GORGON", sysuri[8:], "ISO"]
    elif sysuri:
        hd = [p.get("id_authority") or "GORGON", sysuri, "URI"]
    else:
        hd = [p.get("id_authority") or "GORGON"]
    given = list(p.get("given") or [])
    name = [p.get("family") or "", given[0] if given else "", " ".join(given[1:])]
    addr = p.get("address") or {}
    lines = list(addr.get("line") or [])
    xad = None
    if addr:
        xad = [lines[0] if lines else "", " ".join(lines[1:]), addr.get("city") or "", addr.get("state") or "",
               addr.get("postal_code") or "", addr.get("country") or ""]
    return build_segment("PID", {
        1: "1", 3: [p["id"], "", "", hd, "MR"], 5: name, 7: iso_to_hl7_ts(p.get("birth_date")),
        8: _SEX_HL7.get(p.get("sex") or "", ""), 11: xad, 13: p.get("phone"),
        15: [p["language"]] if p.get("language") else None}, enc)


def _pv1(e, enc):
    e = e or {}
    loc = e.get("location") or {}
    att = e.get("attending") or {}
    cls = _CLASS.get(e.get("class") or "", ("", "", ""))[0] or "U"
    return build_segment("PV1", {
        1: "1", 2: cls, 3: [loc.get("ward") or "", loc.get("room") or "", loc.get("bed") or ""] if loc else None,
        7: [att.get("id") or "", att.get("family") or "", att.get("given") or ""] if att else None,
        19: e.get("id"), 44: iso_to_hl7_ts(e.get("start")), 45: iso_to_hl7_ts(e.get("end"))}, enc)


def _fmt_num(v):
    if v is None:
        return ""
    return repr(v) if isinstance(v, float) else str(v)


def _obx_rows(observations, enc):
    rows = []
    n = 0
    sub = 0
    for o in observations or []:
        comps = o.get("components") or []
        if comps:
            sub += 1
            for c in comps:
                n += 1
                rows.append(build_segment("OBX", {
                    1: str(n), 2: "NM", 3: _cwe(c["code"], c.get("display") or VITALS.get(c["code"], ("",))[0], LOINC),
                    4: str(sub), 5: _fmt_num(c.get("value")), 6: [c.get("unit") or "", c.get("unit") or "", "UCUM"],
                    11: _OBS_STATUS_HL7.get(o.get("status") or "final", "F"), 14: iso_to_hl7_ts(o.get("time"))}, enc))
            continue
        n += 1
        vt = o.get("value_type") or ("NM" if isinstance(o.get("value"), (int, float)) else "ST")
        unit = o.get("unit")
        rows.append(build_segment("OBX", {
            1: str(n), 2: vt, 3: _cwe(o["code"], o.get("display") or VITALS.get(o["code"], ("",))[0],
                                     o.get("system") or LOINC),
            5: _fmt_num(o.get("value")) if vt == "NM" else o.get("value"),
            6: [unit, unit, "UCUM"] if unit else None, 7: o.get("reference_range"), 8: o.get("interpretation"),
            11: _OBS_STATUS_HL7.get(o.get("status") or "final", "F"), 14: iso_to_hl7_ts(o.get("time"))}, enc))
    return rows


def _al1_rows(allergies, enc):
    rows = []
    for k, a in enumerate(allergies or [], 1):
        cat = _ALG_CAT_HL7.get(a.get("category") or "", "MA")
        rows.append(build_segment("AL1", {
            1: str(k), 2: [cat, "", "HL70127"], 3: _cwe(a.get("code"), a.get("display"), a.get("system")),
            4: _ALG_SEV_HL7.get(a.get("severity") or ""), 5: a.get("reaction")}, enc))
    return rows


def _dg1_rows(conditions, enc):
    rows = []
    for k, c in enumerate(conditions or [], 1):
        rows.append(build_segment("DG1", {
            1: str(k), 3: _cwe(c.get("code"), c.get("display"), c.get("system")), 5: iso_to_hl7_ts(c.get("onset")),
            6: "F" if c.get("verification_status") == "confirmed" else "W"}, enc))
    return rows


def build_adt(record, event="A01", facility="CLINIC", receiving_app="", receiving_facility="",
              message_id=None, sent=None, version=DEFAULT_VERSION, enc=DEFAULT_ENC):
    """ADT^A01 (admit), ADT^A03 (discharge) or ADT^A08 (update patient information)."""
    event = event.upper()
    if event not in ("A01", "A03", "A08"):
        raise ValueError("supported ADT events: A01, A03, A08")
    sent = sent or record.get("sent") or _now_iso()
    message_id = message_id or record.get("message_id") or uuid.uuid4().hex[:20].upper()
    enc_rec = dict(record.get("encounter") or {})
    if event == "A03" and not enc_rec.get("end"):
        raise ValueError("A03 discharge needs encounter.end")
    structure = "ADT_A01" if event in ("A01", "A08") else "ADT_A03"
    segs = [_msh(record, ["ADT", event, structure], enc, facility, receiving_app, receiving_facility, version,
                 message_id, sent),
            build_segment("EVN", {1: event, 2: iso_to_hl7_ts(sent)}, enc),
            _pid(record.get("patient"), enc), _pv1(enc_rec, enc)]
    if event in ("A01", "A08"):
        segs += _obx_rows(record.get("observations"), enc) + _al1_rows(record.get("allergies"), enc) \
            + _dg1_rows(record.get("conditions"), enc)
    else:
        segs += _al1_rows(record.get("allergies"), enc) + _dg1_rows(record.get("conditions"), enc) \
            + _obx_rows(record.get("observations"), enc)
    return "\r".join(segs) + "\r"


def build_oru_r01(record, facility="CLINIC", receiving_app="", receiving_facility="", message_id=None, sent=None,
                  version=DEFAULT_VERSION, enc=DEFAULT_ENC, panel=("85353-1", "Vital signs panel")):
    sent = sent or record.get("sent") or _now_iso()
    message_id = message_id or record.get("message_id") or uuid.uuid4().hex[:20].upper()
    obs = record.get("observations") or []
    if not obs:
        raise ValueError("ORU^R01 needs at least one observation")
    first_time = next((o.get("time") for o in obs if o.get("time")), None)
    segs = [_msh(record, ["ORU", "R01", "ORU_R01"], enc, facility, receiving_app, receiving_facility, version,
                 message_id, sent),
            _pid(record.get("patient"), enc)]
    if record.get("encounter"):
        segs.append(_pv1(record["encounter"], enc))
    segs.append(build_segment("OBR", {1: "1", 3: message_id, 4: [panel[0], panel[1], "LN"],
                                      7: iso_to_hl7_ts(first_time)}, enc))
    segs += _obx_rows(obs, enc)
    return "\r".join(segs) + "\r"


def build_ack(msg, code="AA", text="", message_id=None, sent=None):
    if isinstance(msg, (str, bytes)):
        msg = parse_hl7(msg)
    m = msg.msh
    enc = msg.enc
    sent = sent or _now_iso()
    trig = m.get(9, 2)
    segs = [build_segment("MSH", {3: APP_ID, 4: m.get(6), 5: m.get(3), 6: m.get(4), 7: iso_to_hl7_ts(sent),
                                  9: ["ACK", trig, "ACK"], 10: message_id or uuid.uuid4().hex[:20].upper(),
                                  11: m.get(11) or "P", 12: m.get(12) or DEFAULT_VERSION, 18: "UNICODE UTF-8"}, enc),
            build_segment("MSA", {1: code, 2: m.get(10), 3: text or None}, enc)]
    return "\r".join(segs) + "\r"


def hl7_losses(record):
    """Fields of the record that HL7 ADT / ORU cannot carry (would be lost on the way to HL7)."""
    out = []
    if record.get("medications"):
        out.append("medications: no MedicationStatement equivalent in ADT/ORU (%d dropped)" % len(record["medications"]))
    for a in record.get("allergies") or []:
        for k in ("criticality", "clinical_status", "verification_status"):
            if a.get(k) and not (k == "clinical_status" and a[k] == "active"):
                out.append("allergy %s: %s=%s not representable in AL1" % (a.get("code"), k, a[k]))
    for c in record.get("conditions") or []:
        if c.get("clinical_status") and c["clinical_status"] != "active":
            out.append("condition %s: clinical_status=%s not representable in DG1" % (c.get("code"), c["clinical_status"]))
    return out


# ---- HL7 -> record ------------------------------------------------------------------------------------

def _sys_from_hl7(s):
    return SYSTEMS_BY_HL7.get(s or "", s or None)


def _num(s):
    if s is None or s == "":
        return None
    try:
        return int(s) if re.fullmatch(r"[+-]?\d+", s) else float(s)
    except ValueError:
        return s


def _clean(d):
    """Drop empty values recursively (keeps 0 / False)."""
    if isinstance(d, dict):
        out = {}
        for k, v in d.items():
            v = _clean(v)
            if v is None or v == "" or v == [] or v == {}:
                continue
            out[k] = v
        return out
    if isinstance(d, list):
        return [x for x in (_clean(v) for v in d) if not (x is None or x == "" or x == [] or x == {})]
    return d


def hl7_to_record(msg, default_tz=None):
    if isinstance(msg, (str, bytes, bytearray)):
        msg = parse_hl7(msg)
    m = msg.msh
    mtype, trig, _struct = msg.message_type
    notes = []
    rec = {"event": trig if mtype == "ADT" else "R01" if (mtype, trig) == ("ORU", "R01") else trig,
           "message_id": m.get(10), "sent": hl7_ts_to_iso(m.get(7), default_tz) if m.get(7) else None}
    pid = msg.first("PID")
    if pid is None:
        raise ValueError("message has no PID segment")
    auth, uid, typ = pid.get(3, 4, 1), pid.get(3, 4, 2), pid.get(3, 4, 3)
    id_system = ("urn:oid:" + uid) if (uid and typ == "ISO") else (uid or None)
    middle = pid.get(5, 3)
    given = [g for g in [pid.get(5, 2)] + (middle.split(" ") if middle else []) if g]
    addr = None
    if pid.get(11, 1) or pid.get(11, 3):
        line = [x for x in (pid.get(11, 1), pid.get(11, 2)) if x]
        addr = {"line": line, "city": pid.get(11, 3), "state": pid.get(11, 4), "postal_code": pid.get(11, 5),
                "country": pid.get(11, 6)}
    rec["patient"] = {"id": pid.get(3, 1), "id_system": id_system, "id_authority": auth or None,
                      "family": pid.get(5, 1), "given": given,
                      "birth_date": hl7_ts_to_iso(pid.get(7), default_tz) if pid.get(7) else None,
                      "sex": _SEX.get(pid.get(8) or ""), "address": addr, "phone": pid.get(13, 1),
                      "language": pid.get(15, 1)}
    pv1 = msg.first("PV1")
    if pv1 is not None:
        start = pv1.get(44)
        end = pv1.get(45)
        status = "finished" if trig == "A03" or end else "in-progress"
        loc = {"ward": pv1.get(3, 1), "room": pv1.get(3, 2), "bed": pv1.get(3, 3)}
        att = {"id": pv1.get(7, 1), "family": pv1.get(7, 2), "given": pv1.get(7, 3)}
        rec["encounter"] = {"id": pv1.get(19, 1), "class": _CLASS_BY_HL7.get(pv1.get(2) or ""), "status": status,
                            "start": hl7_ts_to_iso(start, default_tz) if start else None,
                            "end": hl7_ts_to_iso(end, default_tz) if end else None,
                            "location": loc, "attending": att}
    obs, groups = [], {}
    for s in msg.all("OBX"):
        code = s.get(3, 1)
        vt = s.get(2) or "ST"
        raw_v = s.get(5)
        value = _num(raw_v) if vt in ("NM", "SN") else raw_v
        unit = s.get(6, 1) or None
        status = _OBS_STATUS.get(s.get(11) or "F", "final")
        time = hl7_ts_to_iso(s.get(14), default_tz) if s.get(14) else None
        subid = s.get(4)
        if code in (BP_SYS, BP_DIA) and subid:
            g = groups.get(subid)
            if g is None:
                g = groups[subid] = {"code": BP_PANEL, "system": LOINC, "display": VITALS[BP_PANEL][0],
                                     "status": status, "time": time, "components": []}
                obs.append(g)
            g["components"].append({"code": code, "display": s.get(3, 2) or VITALS[code][0], "value": value,
                                    "unit": unit})
            continue
        obs.append({"code": code, "system": _sys_from_hl7(s.get(3, 3)) or LOINC, "display": s.get(3, 2),
                    "value": value, "unit": unit, "value_type": vt if vt not in ("NM",) else None,
                    "time": time, "status": status, "interpretation": s.get(8) or None,
                    "reference_range": s.get(7) or None})
    rec["observations"] = obs
    allergies = []
    for s in msg.all("AL1"):
        allergies.append({"code": s.get(3, 1), "display": s.get(3, 2), "system": _sys_from_hl7(s.get(3, 3)),
                          "category": _ALG_CAT.get(s.get(2, 1) or ""), "severity": _ALG_SEV.get(s.get(4) or ""),
                          "reaction": s.get(5), "clinical_status": "active"})
    if allergies:
        notes.append("AL1 carries no clinical status: allergies recorded as clinical_status=active")
    rec["allergies"] = allergies
    conds = []
    for s in msg.all("DG1"):
        dtyp = s.get(6) or "W"  # A admitting / W working -> provisional, F final -> confirmed (A is not kept)
        conds.append({"code": s.get(3, 1), "display": s.get(3, 2), "system": _sys_from_hl7(s.get(3, 3)),
                      "onset": hl7_ts_to_iso(s.get(5), default_tz) if s.get(5) else None, "clinical_status": "active",
                      "verification_status": _DG_TYPE_VER.get(dtyp, "provisional")})
    if conds:
        notes.append("DG1 carries no clinical status: diagnoses recorded as clinical_status=active; verification "
                     "from DG1-6 (A/W -> provisional, F -> confirmed)")
    rec["conditions"] = conds
    rec["notes"] = notes
    return _clean(rec)


# ======================================================================================================
# FHIR R4: build / parse
# ======================================================================================================

_NS = uuid.UUID("5f0b7d1e-3c55-4c3e-9a51-6f1f6b0c0d7e")


def _urn(*parts):
    return "urn:uuid:%s" % uuid.uuid5(_NS, "|".join(str(p) for p in parts))


def _cc(code, display, system, text=None):
    c = {"system": system, "code": code, "display": display}
    out = {"coding": [{k: v for k, v in c.items() if v}]}
    if text:
        out["text"] = text
    return out


def _qty(value, unit):
    return {"value": value, "unit": UCUM_DISPLAY.get(unit, unit), "system": UCUM, "code": unit}


def _fhir_patient(p):
    res = {"resourceType": "Patient",
           "identifier": [{"use": "usual", "system": p.get("id_system") or "urn:gorgon-covenant:mrn", "value": p["id"]}]}
    if p.get("id_authority") and p.get("id_authority") != "GORGON":
        res["identifier"][0]["assigner"] = {"display": p["id_authority"]}
    name = {}
    if p.get("family"):
        name["family"] = p["family"]
    if p.get("given"):
        name["given"] = list(p["given"])
    if name:
        name["use"] = "official"
        res["name"] = [name]
    if p.get("phone"):
        res["telecom"] = [{"system": "phone", "value": p["phone"]}]
    if p.get("sex"):
        res["gender"] = p["sex"]
    if p.get("birth_date"):
        res["birthDate"] = p["birth_date"]
    a = p.get("address")
    if a:
        addr = {"line": a.get("line"), "city": a.get("city"), "state": a.get("state"),
                "postalCode": a.get("postal_code"), "country": a.get("country")}
        res["address"] = [_clean(addr)]
    if p.get("language"):
        res["communication"] = [{"language": _cc(p["language"], None, "urn:ietf:bcp:47"), "preferred": True}]
    return _clean(res)


def _fhir_encounter(e, pat_ref):
    cls = _CLASS.get(e.get("class") or "")
    res = {"resourceType": "Encounter", "status": e.get("status") or "unknown",
           "class": {"system": _V3_ACT, "code": cls[1], "display": cls[2]} if cls else
           {"system": "http://terminology.hl7.org/CodeSystem/v3-NullFlavor", "code": "UNK", "display": "unknown"},
           "subject": {"reference": pat_ref}}
    if e.get("id"):
        res["identifier"] = [{"system": "urn:gorgon-covenant:visit", "value": e["id"]}]
    per = _clean({"start": e.get("start"), "end": e.get("end")})
    if per:
        res["period"] = per
    loc = e.get("location") or {}
    if any(loc.values()):
        res["location"] = [{"location": {"display": " / ".join(loc.get(k) or "" for k in ("ward", "room", "bed"))}}]
    att = e.get("attending") or {}
    if any(att.values()):
        ind = {"display": ", ".join(x for x in (att.get("family"), att.get("given")) if x)}
        if att.get("id"):
            ind["identifier"] = {"system": "urn:gorgon-covenant:staff", "value": att["id"]}
        res["participant"] = [{"type": [_cc("ATND", "attender", "http://terminology.hl7.org/CodeSystem/v3-ParticipationType")],
                               "individual": _clean(ind)}]
    return _clean(res)


def _fhir_observation(o, pat_ref, enc_ref):
    code = o["code"]
    system = o.get("system") or LOINC
    vital = system == LOINC and code in VITALS
    res = {"resourceType": "Observation", "status": o.get("status") or "final",
           "code": _cc(code, o.get("display") or (VITALS[code][0] if vital else None), system),
           "subject": {"reference": pat_ref}}
    if vital:
        res["category"] = [_cc("vital-signs", "Vital Signs", _OBS_CAT)]
    if enc_ref:
        res["encounter"] = {"reference": enc_ref}
    if o.get("time"):
        res["effectiveDateTime"] = o["time"]
    if o.get("components"):
        res["component"] = [{"code": _cc(c["code"], c.get("display") or VITALS.get(c["code"], (None,))[0], LOINC),
                             "valueQuantity": _qty(c["value"], c["unit"])} for c in o["components"]]
    elif o.get("value") is not None:
        if isinstance(o["value"], (int, float)) and not isinstance(o["value"], bool):
            res["valueQuantity"] = _qty(o["value"], o.get("unit")) if o.get("unit") else {"value": o["value"]}
        else:
            res["valueString"] = str(o["value"])
    if o.get("interpretation"):
        res["interpretation"] = [_cc(o["interpretation"], None, _INTERP)]
    if o.get("reference_range"):
        res["referenceRange"] = [{"text": o["reference_range"]}]
    return _clean(res)


def _fhir_condition(c, pat_ref, enc_ref):
    res = {"resourceType": "Condition", "subject": {"reference": pat_ref},
           "code": _cc(c.get("code"), c.get("display"), c.get("system"))}
    vs = c.get("verification_status")
    if vs:
        res["verificationStatus"] = _cc(vs, None, _COND_VER)
    if c.get("clinical_status") and vs != "entered-in-error":
        res["clinicalStatus"] = _cc(c["clinical_status"], None, _COND_CLIN)
    if c.get("onset"):
        res["onsetDateTime"] = c["onset"]
    if enc_ref:
        res["encounter"] = {"reference": enc_ref}
    return _clean(res)


def _fhir_medication(m, pat_ref, enc_ref):
    res = {"resourceType": "MedicationStatement", "status": m.get("status") or "active",
           "medicationCodeableConcept": _cc(m.get("code"), m.get("display"), m.get("system")),
           "subject": {"reference": pat_ref}}
    if enc_ref:
        res["context"] = {"reference": enc_ref}
    if m.get("start"):
        res["effectivePeriod"] = {"start": m["start"]}
    if m.get("dosage"):
        res["dosage"] = [{"text": m["dosage"]}]
    return _clean(res)


def _fhir_allergy(a, pat_ref, enc_ref):
    res = {"resourceType": "AllergyIntolerance", "patient": {"reference": pat_ref},
           "code": _cc(a.get("code"), a.get("display"), a.get("system"))}
    vs = a.get("verification_status")
    if vs:
        res["verificationStatus"] = _cc(vs, None, _AIT_VER)
    if vs != "entered-in-error":
        res["clinicalStatus"] = _cc(a.get("clinical_status") or "active", None, _AIT_CLIN)
    if a.get("category"):
        res["category"] = [a["category"]]
    if a.get("criticality"):
        res["criticality"] = a["criticality"]
    if a.get("reaction") or a.get("severity"):
        rx = {"manifestation": [{"text": a.get("reaction") or "unspecified reaction"}]}
        if a.get("severity"):
            rx["severity"] = a["severity"]
        res["reaction"] = [rx]
    if enc_ref:
        res["encounter"] = {"reference": enc_ref}
    return _clean(res)


def record_to_fhir_bundle(record, timestamp=None):
    """Transaction Bundle for one record. fullUrls are deterministic urn:uuid values derived from the ids."""
    p = record.get("patient") or {}
    if not p.get("id"):
        raise ValueError("record['patient']['id'] is required")
    psys = p.get("id_system") or "urn:gorgon-covenant:mrn"
    pat_url = _urn("Patient", psys, p["id"])
    entries = [{"fullUrl": pat_url, "resource": _fhir_patient(p),
                "request": {"method": "POST", "url": "Patient", "ifNoneExist": "identifier=%s|%s" % (psys, p["id"])}}]
    enc_url = None
    e = record.get("encounter")
    if e:
        enc_url = _urn("Encounter", p["id"], e.get("id") or e.get("start") or "")
        req = {"method": "POST", "url": "Encounter"}
        if e.get("id"):
            req["ifNoneExist"] = "identifier=urn:gorgon-covenant:visit|%s" % e["id"]
        entries.append({"fullUrl": enc_url, "resource": _fhir_encounter(e, pat_url), "request": req})
    for kind, items, fn in (("Observation", record.get("observations"), _fhir_observation),
                            ("Condition", record.get("conditions"), _fhir_condition),
                            ("MedicationStatement", record.get("medications"), _fhir_medication),
                            ("AllergyIntolerance", record.get("allergies"), _fhir_allergy)):
        for k, it in enumerate(items or []):
            url = _urn(kind, p["id"], it.get("id") or k, it.get("code"), it.get("time") or it.get("onset") or "")
            entries.append({"fullUrl": url, "resource": fn(it, pat_url, enc_url),
                            "request": {"method": "POST", "url": kind}})
    b = {"resourceType": "Bundle", "type": "transaction", "entry": entries}
    if timestamp:
        b["timestamp"] = timestamp
    return b


def _coding(cc, system=None):
    for c in (cc or {}).get("coding") or []:
        if system is None or c.get("system") == system:
            return c
    return {}


def fhir_bundle_to_record(bundle):
    """Internal record from a Bundle (transaction, batch, collection or searchset)."""
    by_url, rec = {}, {"observations": [], "conditions": [], "medications": [], "allergies": []}
    for en in bundle.get("entry") or []:
        r = en.get("resource") or {}
        if en.get("fullUrl"):
            by_url[en["fullUrl"]] = r
        if r.get("resourceType") and r.get("id"):
            by_url["%s/%s" % (r["resourceType"], r["id"])] = r
    for en in bundle.get("entry") or []:
        r = en.get("resource") or {}
        t = r.get("resourceType")
        if t == "Patient":
            ident = (r.get("identifier") or [{}])[0]
            name = (r.get("name") or [{}])[0]
            a = (r.get("address") or [None])[0]
            lang = None
            for c in r.get("communication") or []:
                lang = _coding(c.get("language")).get("code") or lang
            phone = next((tp.get("value") for tp in r.get("telecom") or [] if tp.get("system") == "phone"), None)
            sysuri = ident.get("system")
            rec["patient"] = {"id": ident.get("value"),
                              "id_system": None if sysuri == "urn:gorgon-covenant:mrn" else sysuri,
                              "id_authority": (ident.get("assigner") or {}).get("display") or "GORGON",
                              "family": name.get("family"), "given": name.get("given"),
                              "birth_date": r.get("birthDate"), "sex": r.get("gender"), "phone": phone,
                              "address": {"line": a.get("line"), "city": a.get("city"), "state": a.get("state"),
                                          "postal_code": a.get("postalCode"), "country": a.get("country")} if a else None,
                              "language": lang}
        elif t == "Encounter":
            cls = _CLASS_BY_V3.get((r.get("class") or {}).get("code"))
            loc_disp = ((r.get("location") or [{}])[0].get("location") or {}).get("display") or ""
            parts = (loc_disp.split(" / ", 2) + ["", "", ""])[:3]
            part = (r.get("participant") or [{}])[0].get("individual") or {}
            fam, _sep, giv = (part.get("display") or "").partition(", ")
            att = {"id": (part.get("identifier") or {}).get("value"), "family": fam or None, "given": giv or None}
            rec["encounter"] = {"id": ((r.get("identifier") or [{}])[0]).get("value"), "class": cls,
                                "status": r.get("status"), "start": (r.get("period") or {}).get("start"),
                                "end": (r.get("period") or {}).get("end"),
                                "location": {"ward": parts[0], "room": parts[1], "bed": parts[2]}, "attending": att}
        elif t == "Observation":
            c = _coding(r.get("code"))
            o = {"code": c.get("code"), "system": c.get("system"), "display": c.get("display"),
                 "status": r.get("status"), "time": r.get("effectiveDateTime")}
            if r.get("component"):
                o["components"] = [{"code": _coding(x.get("code")).get("code"),
                                    "display": _coding(x.get("code")).get("display"),
                                    "value": (x.get("valueQuantity") or {}).get("value"),
                                    "unit": (x.get("valueQuantity") or {}).get("code")} for x in r["component"]]
            elif "valueQuantity" in r:
                o["value"] = r["valueQuantity"].get("value")
                o["unit"] = r["valueQuantity"].get("code")
            elif "valueString" in r:
                o["value"] = r["valueString"]
                o["value_type"] = "ST"
            if r.get("interpretation"):
                o["interpretation"] = _coding(r["interpretation"][0]).get("code")
            if r.get("referenceRange"):
                o["reference_range"] = r["referenceRange"][0].get("text")
            rec["observations"].append(o)
        elif t == "Condition":
            c = _coding(r.get("code"))
            rec["conditions"].append({"code": c.get("code"), "system": c.get("system"), "display": c.get("display"),
                                      "clinical_status": _coding(r.get("clinicalStatus")).get("code"),
                                      "verification_status": _coding(r.get("verificationStatus")).get("code"),
                                      "onset": r.get("onsetDateTime")})
        elif t == "MedicationStatement":
            c = _coding(r.get("medicationCodeableConcept"))
            rec["medications"].append({"code": c.get("code"), "system": c.get("system"), "display": c.get("display"),
                                       "status": r.get("status"), "start": (r.get("effectivePeriod") or {}).get("start")
                                       or r.get("effectiveDateTime"),
                                       "dosage": ((r.get("dosage") or [{}])[0]).get("text")})
        elif t == "AllergyIntolerance":
            c = _coding(r.get("code"))
            rx = (r.get("reaction") or [{}])[0]
            man = ((rx.get("manifestation") or [{}])[0]).get("text")
            rec["allergies"].append({"code": c.get("code"), "system": c.get("system"), "display": c.get("display"),
                                     "category": (r.get("category") or [None])[0], "criticality": r.get("criticality"),
                                     "clinical_status": _coding(r.get("clinicalStatus")).get("code"),
                                     "verification_status": _coding(r.get("verificationStatus")).get("code"),
                                     "severity": rx.get("severity"),
                                     "reaction": None if man == "unspecified reaction" else man})
    return _clean(rec)


# ======================================================================================================
# FHIR R4 structural validation
# ======================================================================================================

_YEAR = r"([0-9]([0-9]([0-9][1-9]|[1-9]0)|[1-9]00)|[1-9]000)"
_TZ = r"(Z|(\+|-)((0[0-9]|1[0-3]):[0-5][0-9]|14:00))"
_PRIM = {
    "id": re.compile(r"^[A-Za-z0-9\-\.]{1,64}$"),
    "code": re.compile(r"^[^\s]+( [^\s]+)*$"),
    "uri": re.compile(r"^\S*$"), "canonical": re.compile(r"^\S*$"),
    "date": re.compile(r"^%s(-(0[1-9]|1[0-2])(-(0[1-9]|[1-2][0-9]|3[0-1]))?)?$" % _YEAR),
    "dateTime": re.compile(r"^%s(-(0[1-9]|1[0-2])(-(0[1-9]|[1-2][0-9]|3[0-1])(T([01][0-9]|2[0-3]):[0-5][0-9]:"
                           r"([0-5][0-9]|60)(\.[0-9]{1,9})?%s)?)?)?$" % (_YEAR, _TZ)),
    "instant": re.compile(r"^%s-(0[1-9]|1[0-2])-(0[1-9]|[1-2][0-9]|3[0-1])T([01][0-9]|2[0-3]):[0-5][0-9]:"
                          r"([0-5][0-9]|60)(\.[0-9]{1,9})?%s$" % (_YEAR, _TZ)),
    "time": re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]:([0-5][0-9]|60)(\.[0-9]{1,9})?$"),
    "string": re.compile(r"^[ \r\n\t\S]+$"), "markdown": re.compile(r"^[ \r\n\t\S]+$"),
}

_VS = {
    "administrative-gender": {"male", "female", "other", "unknown"},
    "encounter-status": {"planned", "arrived", "triaged", "in-progress", "onleave", "finished", "cancelled",
                         "entered-in-error", "unknown"},
    "encounter-location-status": {"planned", "active", "reserved", "completed"},
    "observation-status": {"registered", "preliminary", "final", "amended", "corrected", "cancelled",
                           "entered-in-error", "unknown"},
    "medication-statement-status": {"active", "completed", "entered-in-error", "intended", "stopped", "on-hold",
                                    "unknown", "not-taken"},
    "allergy-intolerance-type": {"allergy", "intolerance"},
    "allergy-intolerance-category": {"food", "medication", "environment", "biologic"},
    "allergy-intolerance-criticality": {"low", "high", "unable-to-assess"},
    "reaction-event-severity": {"mild", "moderate", "severe"},
    "bundle-type": {"document", "message", "transaction", "transaction-response", "batch", "batch-response",
                    "history", "searchset", "collection"},
    "http-verb": {"GET", "HEAD", "POST", "PUT", "DELETE", "PATCH"},
    "identifier-use": {"usual", "official", "temp", "secondary", "old"},
    "name-use": {"usual", "official", "temp", "nickname", "anonymous", "old", "maiden"},
    "contact-point-system": {"phone", "fax", "email", "pager", "url", "sms", "other"},
    "contact-point-use": {"home", "work", "temp", "old", "mobile"},
    "address-use": {"home", "work", "temp", "old", "billing"}, "address-type": {"postal", "physical", "both"},
    "quantity-comparator": {"<", "<=", ">=", ">"},
    "narrative-status": {"generated", "extensions", "additional", "empty"},
}
_CODED_STATUS = {  # CodeableConcept elements with required bindings: (system, allowed codes)
    ("Condition", "clinicalStatus"): (_COND_CLIN, {"active", "recurrence", "relapse", "inactive", "remission", "resolved"}),
    ("Condition", "verificationStatus"): (_COND_VER, {"unconfirmed", "provisional", "differential", "confirmed",
                                                      "refuted", "entered-in-error"}),
    ("AllergyIntolerance", "clinicalStatus"): (_AIT_CLIN, {"active", "inactive", "resolved"}),
    ("AllergyIntolerance", "verificationStatus"): (_AIT_VER, {"unconfirmed", "confirmed", "refuted", "entered-in-error"}),
}

# element: (type, min, max[, value set]) ; max is 1 or "*" ; "Any" = allowed, not inspected
_DT = {
    "Coding": {"system": ("uri", 0, 1), "version": ("string", 0, 1), "code": ("code", 0, 1),
               "display": ("string", 0, 1), "userSelected": ("boolean", 0, 1)},
    "CodeableConcept": {"coding": ("Coding", 0, "*"), "text": ("string", 0, 1)},
    "Identifier": {"use": ("code", 0, 1, "identifier-use"), "type": ("CodeableConcept", 0, 1), "system": ("uri", 0, 1),
                   "value": ("string", 0, 1), "period": ("Period", 0, 1), "assigner": ("Reference", 0, 1)},
    "Reference": {"reference": ("string", 0, 1), "type": ("uri", 0, 1), "identifier": ("Identifier", 0, 1),
                  "display": ("string", 0, 1)},
    "Period": {"start": ("dateTime", 0, 1), "end": ("dateTime", 0, 1)},
    "Quantity": {"value": ("decimal", 0, 1), "comparator": ("code", 0, 1, "quantity-comparator"),
                 "unit": ("string", 0, 1), "system": ("uri", 0, 1), "code": ("code", 0, 1)},
    "Range": {"low": ("Quantity", 0, 1), "high": ("Quantity", 0, 1)},
    "HumanName": {"use": ("code", 0, 1, "name-use"), "text": ("string", 0, 1), "family": ("string", 0, 1),
                  "given": ("string", 0, "*"), "prefix": ("string", 0, "*"), "suffix": ("string", 0, "*"),
                  "period": ("Period", 0, 1)},
    "ContactPoint": {"system": ("code", 0, 1, "contact-point-system"), "value": ("string", 0, 1),
                     "use": ("code", 0, 1, "contact-point-use"), "rank": ("positiveInt", 0, 1),
                     "period": ("Period", 0, 1)},
    "Address": {"use": ("code", 0, 1, "address-use"), "type": ("code", 0, 1, "address-type"), "text": ("string", 0, 1),
                "line": ("string", 0, "*"), "city": ("string", 0, 1), "district": ("string", 0, 1),
                "state": ("string", 0, 1), "postalCode": ("string", 0, 1), "country": ("string", 0, 1),
                "period": ("Period", 0, 1)},
    "Annotation": {"authorString": ("string", 0, 1), "authorReference": ("Reference", 0, 1), "time": ("dateTime", 0, 1),
                   "text": ("markdown", 1, 1)},
    "Dosage": {"sequence": ("integer", 0, 1), "text": ("string", 0, 1), "additionalInstruction": ("CodeableConcept", 0, "*"),
               "patientInstruction": ("string", 0, 1), "timing": ("Any", 0, 1), "asNeededBoolean": ("boolean", 0, 1),
               "asNeededCodeableConcept": ("CodeableConcept", 0, 1), "site": ("CodeableConcept", 0, 1),
               "route": ("CodeableConcept", 0, 1), "method": ("CodeableConcept", 0, 1), "doseAndRate": ("Any", 0, "*"),
               "maxDosePerPeriod": ("Any", 0, 1), "maxDosePerAdministration": ("Quantity", 0, 1),
               "maxDosePerLifetime": ("Quantity", 0, 1)},
    "Ratio": {"numerator": ("Quantity", 0, 1), "denominator": ("Quantity", 0, 1)},
    "Meta": {"versionId": ("id", 0, 1), "lastUpdated": ("instant", 0, 1), "source": ("uri", 0, 1),
             "profile": ("canonical", 0, "*"), "security": ("Coding", 0, "*"), "tag": ("Coding", 0, "*")},
    "Narrative": {"status": ("code", 1, 1, "narrative-status"), "div": ("xhtml", 1, 1)},
}
_DT["Age"] = _DT["Duration"] = _DT["Quantity"]
_COMMON = {"id": ("id", 0, 1), "meta": ("Meta", 0, 1), "implicitRules": ("uri", 0, 1), "language": ("code", 0, 1),
           "text": ("Narrative", 0, 1), "contained": ("Any", 0, "*"), "extension": ("Any", 0, "*"),
           "modifierExtension": ("Any", 0, "*")}
_BB = {"id": ("string", 0, 1), "extension": ("Any", 0, "*"), "modifierExtension": ("Any", 0, "*")}

_RES = {
    "Patient": {"identifier": ("Identifier", 0, "*"), "active": ("boolean", 0, 1), "name": ("HumanName", 0, "*"),
                "telecom": ("ContactPoint", 0, "*"), "gender": ("code", 0, 1, "administrative-gender"),
                "birthDate": ("date", 0, 1), "deceased[x]": (("boolean", "dateTime"), 0, 1),
                "address": ("Address", 0, "*"), "maritalStatus": ("CodeableConcept", 0, 1),
                "multipleBirth[x]": (("boolean", "integer"), 0, 1), "photo": ("Any", 0, "*"), "contact": ("Any", 0, "*"),
                "communication": ({"language": ("CodeableConcept", 1, 1), "preferred": ("boolean", 0, 1)}, 0, "*"),
                "generalPractitioner": ("Reference", 0, "*"), "managingOrganization": ("Reference", 0, 1),
                "link": ("Any", 0, "*")},
    "Encounter": {"identifier": ("Identifier", 0, "*"), "status": ("code", 1, 1, "encounter-status"),
                  "statusHistory": ("Any", 0, "*"), "class": ("Coding", 1, 1), "classHistory": ("Any", 0, "*"),
                  "type": ("CodeableConcept", 0, "*"), "serviceType": ("CodeableConcept", 0, 1),
                  "priority": ("CodeableConcept", 0, 1), "subject": ("Reference", 0, 1),
                  "episodeOfCare": ("Reference", 0, "*"), "basedOn": ("Reference", 0, "*"),
                  "participant": ({"type": ("CodeableConcept", 0, "*"), "period": ("Period", 0, 1),
                                   "individual": ("Reference", 0, 1)}, 0, "*"),
                  "appointment": ("Reference", 0, "*"), "period": ("Period", 0, 1), "length": ("Any", 0, 1),
                  "reasonCode": ("CodeableConcept", 0, "*"), "reasonReference": ("Reference", 0, "*"),
                  "diagnosis": ({"condition": ("Reference", 1, 1), "use": ("CodeableConcept", 0, 1),
                                 "rank": ("positiveInt", 0, 1)}, 0, "*"),
                  "account": ("Reference", 0, "*"), "hospitalization": ("Any", 0, 1),
                  "location": ({"location": ("Reference", 1, 1), "status": ("code", 0, 1, "encounter-location-status"),
                                "physicalType": ("CodeableConcept", 0, 1), "period": ("Period", 0, 1)}, 0, "*"),
                  "serviceProvider": ("Reference", 0, 1), "partOf": ("Reference", 0, 1)},
    "Observation": {"identifier": ("Identifier", 0, "*"), "basedOn": ("Reference", 0, "*"), "partOf": ("Reference", 0, "*"),
                    "status": ("code", 1, 1, "observation-status"), "category": ("CodeableConcept", 0, "*"),
                    "code": ("CodeableConcept", 1, 1), "subject": ("Reference", 0, 1), "focus": ("Reference", 0, "*"),
                    "encounter": ("Reference", 0, 1),
                    "effective[x]": (("dateTime", "Period", "instant", "Timing"), 0, 1), "issued": ("instant", 0, 1),
                    "performer": ("Reference", 0, "*"),
                    "value[x]": (("Quantity", "CodeableConcept", "string", "boolean", "integer", "Range", "Ratio",
                                  "SampledData", "time", "dateTime", "Period"), 0, 1),
                    "dataAbsentReason": ("CodeableConcept", 0, 1), "interpretation": ("CodeableConcept", 0, "*"),
                    "note": ("Annotation", 0, "*"), "bodySite": ("CodeableConcept", 0, 1),
                    "method": ("CodeableConcept", 0, 1), "specimen": ("Reference", 0, 1), "device": ("Reference", 0, 1),
                    "referenceRange": ({"low": ("Quantity", 0, 1), "high": ("Quantity", 0, 1),
                                        "type": ("CodeableConcept", 0, 1), "appliesTo": ("CodeableConcept", 0, "*"),
                                        "age": ("Range", 0, 1), "text": ("string", 0, 1)}, 0, "*"),
                    "hasMember": ("Reference", 0, "*"), "derivedFrom": ("Reference", 0, "*"),
                    "component": ({"code": ("CodeableConcept", 1, 1),
                                   "value[x]": (("Quantity", "CodeableConcept", "string", "boolean", "integer", "Range",
                                                 "Ratio", "SampledData", "time", "dateTime", "Period"), 0, 1),
                                   "dataAbsentReason": ("CodeableConcept", 0, 1),
                                   "interpretation": ("CodeableConcept", 0, "*"), "referenceRange": ("Any", 0, "*")}, 0, "*")},
    "Condition": {"identifier": ("Identifier", 0, "*"), "clinicalStatus": ("CodeableConcept", 0, 1),
                  "verificationStatus": ("CodeableConcept", 0, 1), "category": ("CodeableConcept", 0, "*"),
                  "severity": ("CodeableConcept", 0, 1), "code": ("CodeableConcept", 0, 1),
                  "bodySite": ("CodeableConcept", 0, "*"), "subject": ("Reference", 1, 1),
                  "encounter": ("Reference", 0, 1),
                  "onset[x]": (("dateTime", "Age", "Period", "Range", "string"), 0, 1),
                  "abatement[x]": (("dateTime", "Age", "Period", "Range", "string"), 0, 1),
                  "recordedDate": ("dateTime", 0, 1), "recorder": ("Reference", 0, 1), "asserter": ("Reference", 0, 1),
                  "stage": ("Any", 0, "*"), "evidence": ("Any", 0, "*"), "note": ("Annotation", 0, "*")},
    "MedicationStatement": {"identifier": ("Identifier", 0, "*"), "basedOn": ("Reference", 0, "*"),
                            "partOf": ("Reference", 0, "*"), "status": ("code", 1, 1, "medication-statement-status"),
                            "statusReason": ("CodeableConcept", 0, "*"), "category": ("CodeableConcept", 0, 1),
                            "medication[x]": (("CodeableConcept", "Reference"), 1, 1), "subject": ("Reference", 1, 1),
                            "context": ("Reference", 0, 1), "effective[x]": (("dateTime", "Period"), 0, 1),
                            "dateAsserted": ("dateTime", 0, 1), "informationSource": ("Reference", 0, 1),
                            "derivedFrom": ("Reference", 0, "*"), "reasonCode": ("CodeableConcept", 0, "*"),
                            "reasonReference": ("Reference", 0, "*"), "note": ("Annotation", 0, "*"),
                            "dosage": ("Dosage", 0, "*")},
    "AllergyIntolerance": {"identifier": ("Identifier", 0, "*"), "clinicalStatus": ("CodeableConcept", 0, 1),
                           "verificationStatus": ("CodeableConcept", 0, 1),
                           "type": ("code", 0, 1, "allergy-intolerance-type"),
                           "category": ("code", 0, "*", "allergy-intolerance-category"),
                           "criticality": ("code", 0, 1, "allergy-intolerance-criticality"),
                           "code": ("CodeableConcept", 0, 1), "patient": ("Reference", 1, 1),
                           "encounter": ("Reference", 0, 1),
                           "onset[x]": (("dateTime", "Age", "Period", "Range", "string"), 0, 1),
                           "recordedDate": ("dateTime", 0, 1), "recorder": ("Reference", 0, 1),
                           "asserter": ("Reference", 0, 1), "lastOccurrence": ("dateTime", 0, 1),
                           "note": ("Annotation", 0, "*"),
                           "reaction": ({"substance": ("CodeableConcept", 0, 1),
                                         "manifestation": ("CodeableConcept", 1, "*"), "description": ("string", 0, 1),
                                         "onset": ("dateTime", 0, 1),
                                         "severity": ("code", 0, 1, "reaction-event-severity"),
                                         "exposureRoute": ("CodeableConcept", 0, 1), "note": ("Annotation", 0, "*")},
                                        0, "*")},
    "Bundle": {"identifier": ("Identifier", 0, 1), "type": ("code", 1, 1, "bundle-type"), "timestamp": ("instant", 0, 1),
               "total": ("unsignedInt", 0, 1), "link": ("Any", 0, "*"),
               "entry": ({"link": ("Any", 0, "*"), "fullUrl": ("uri", 0, 1), "resource": ("Resource", 0, 1),
                          "search": ("Any", 0, 1),
                          "request": ({"method": ("code", 1, 1, "http-verb"), "url": ("uri", 1, 1),
                                       "ifNoneMatch": ("string", 0, 1), "ifModifiedSince": ("instant", 0, 1),
                                       "ifMatch": ("string", 0, 1), "ifNoneExist": ("string", 0, 1)}, 0, 1),
                          "response": ("Any", 0, 1)}, 0, "*"),
               "signature": ("Any", 0, 1)},
}
_RES_COMMON_SKIP = {"Bundle": {"text", "contained", "extension", "modifierExtension", "implicitRules"}}


def _cap(s):
    return s[0].upper() + s[1:]


class _V:
    def __init__(self):
        self.issues = []

    def err(self, path, msg, severity="error"):
        self.issues.append({"severity": severity, "path": path, "message": msg})

    def prim(self, typ, val, path, vs=None):
        if typ in ("boolean",):
            if not isinstance(val, bool):
                self.err(path, "must be a JSON boolean")
            return
        if typ in ("decimal",):
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                self.err(path, "must be a JSON number")
            return
        if typ in ("integer", "positiveInt", "unsignedInt"):
            if isinstance(val, bool) or not isinstance(val, int):
                self.err(path, "must be a JSON integer")
            elif typ == "positiveInt" and val < 1 or typ == "unsignedInt" and val < 0:
                self.err(path, "%s out of range" % typ)
            return
        if not isinstance(val, str):
            self.err(path, "must be a JSON string")
            return
        if val == "" or (val.strip() == "" and typ != "string"):
            self.err(path, "empty string not allowed (ele-1)")
            return
        if typ == "xhtml":
            if not val.lstrip().startswith("<div"):
                self.err(path, "narrative div must be an XHTML <div>")
            return
        rx = _PRIM.get(typ)
        if rx and not rx.match(val):
            self.err(path, "invalid %s %r" % (typ, val))
        if vs and val not in _VS[vs]:
            self.err(path, "code %r not in required value set %s" % (val, vs))

    def element(self, spec, val, path):
        typ = spec[0]
        vs = spec[3] if len(spec) > 3 else None
        if typ in ("Any", "Timing", "SampledData"):
            return
        if typ == "Resource":
            self.resource(val, path)
            return
        if isinstance(typ, dict):
            self.obj(dict(_BB, **typ), val, path)
            return
        if typ in _DT:
            self.obj(_DT[typ], val, path)
            if typ == "Reference" and isinstance(val, dict):
                ref = val.get("reference")
                if isinstance(ref, str) and not (re.match(r"^[A-Z][A-Za-z]+/[A-Za-z0-9\-\.]{1,64}(/_history/[A-Za-z0-9\-\.]{1,64})?$", ref)
                                                 or ref.startswith(("urn:uuid:", "urn:oid:", "http://", "https://", "#"))):
                    self.err(path + ".reference", "reference %r is not relative, absolute or urn:uuid" % ref)
            return
        self.prim(typ, val, path, vs)

    def obj(self, schema, val, path):
        if not isinstance(val, dict):
            self.err(path, "must be a JSON object")
            return
        if not val:
            self.err(path, "empty object not allowed (ele-1)")
            return
        choices = {k[:-3]: v for k, v in schema.items() if k.endswith("[x]")}
        seen_choice = {}
        for key, v in val.items():
            if key == "resourceType":
                continue
            p = "%s.%s" % (path, key)
            spec = schema.get(key)
            if spec is None:
                for base, cspec in choices.items():
                    if key.startswith(base) and key[len(base):] in [_cap(t) for t in cspec[0]]:
                        tname = key[len(base):]
                        tname = tname[0].lower() + tname[1:] if tname in ("String", "Boolean", "Integer",
                                                                          "DateTime", "Time", "Instant") else tname
                        spec = (tname,) + cspec[1:]
                        seen_choice.setdefault(base, []).append(key)
                        break
            if spec is None:
                if key.startswith("_"):
                    continue
                self.err(p, "unknown element")
                continue
            if v is None:
                self.err(p, "null is not allowed in FHIR JSON")
                continue
            many = spec[2] == "*"
            if many:
                if not isinstance(v, list):
                    self.err(p, "must be an array (0..*)")
                    continue
                if not v:
                    self.err(p, "empty array not allowed (ele-1)")
                for k, item in enumerate(v):
                    if item is None:
                        self.err("%s[%d]" % (p, k), "null array item")
                    else:
                        self.element(spec, item, "%s[%d]" % (p, k))
            else:
                if isinstance(v, list):
                    self.err(p, "must not be an array (max 1)")
                    continue
                self.element(spec, v, p)
        for base, keys in seen_choice.items():
            if len(keys) > 1:
                self.err("%s.%s[x]" % (path, base), "only one of %s allowed" % ", ".join(keys))
        for key, spec in schema.items():
            if spec[1] >= 1:
                if key.endswith("[x]"):
                    if key[:-3] not in seen_choice:
                        self.err("%s.%s" % (path, key), "required element missing (min 1)")
                elif key not in val:
                    self.err("%s.%s" % (path, key), "required element missing (min 1)")

    def resource(self, res, path):
        if not isinstance(res, dict):
            self.err(path, "resource must be a JSON object")
            return
        rt = res.get("resourceType")
        if rt not in _RES:
            self.err(path + ".resourceType", "unsupported or missing resourceType %r" % rt)
            return
        schema = dict(_COMMON, **_RES[rt])
        for k in _RES_COMMON_SKIP.get(rt, ()):
            schema.pop(k, None)
        p = path or rt
        self.obj(schema, res, p)
        getattr(self, "inv_" + rt, lambda r, p: None)(res, p)
        for (rtype, el), (system, codes) in _CODED_STATUS.items():
            if rtype == rt and isinstance(res.get(el), dict):
                for c in res[el].get("coding") or []:
                    if c.get("system") == system and c.get("code") not in codes:
                        self.err("%s.%s" % (p, el), "code %r not in required value set" % c.get("code"))
                    if c.get("system") != system:
                        self.err("%s.%s" % (p, el), "coding system must be %s" % system)

    # invariants -----------------------------------------------------------------------------------
    def inv_Observation(self, r, p):
        values = [k for k in r if k.startswith("value")]
        if values and "dataAbsentReason" in r:
            self.err(p, "obs-6: dataAbsentReason SHALL only be present if value[x] is not present")
        code = _coding(r.get("code"))
        for k, comp in enumerate(r.get("component") or []):
            cc = _coding(comp.get("code") if isinstance(comp, dict) else None)
            if cc and code and cc.get("code") == code.get("code") and cc.get("system") == code.get("system") and values:
                self.err("%s.component[%d]" % (p, k), "obs-7: value[x] SHALL NOT be present if a component has the "
                                                      "same code as the Observation")
            if isinstance(comp, dict) and any(x.startswith("value") for x in comp) and "dataAbsentReason" in comp:
                self.err("%s.component[%d]" % (p, k), "obs-6 (component): value and dataAbsentReason both present")
        cats = [c for cat in r.get("category") or [] if isinstance(cat, dict) for c in cat.get("coding") or []]
        if any(c.get("system") == _OBS_CAT and c.get("code") == "vital-signs" for c in cats):
            self.vitals(r, p)

    def vitals(self, r, p):
        """Subset of the FHIR R4 vital signs profile (vitalsigns)."""
        loinc = [c for c in (r.get("code") or {}).get("coding") or [] if c.get("system") == LOINC]
        if not loinc:
            self.err(p + ".code", "vital-signs: needs a LOINC coding")
            return
        if "subject" not in r:
            self.err(p + ".subject", "vital-signs: subject is required")
        if not any(k.startswith("effective") for k in r):
            self.err(p + ".effective[x]", "vital-signs: effective[x] is required (vs-1)")
        code = loinc[0].get("code")
        if code not in VITALS:
            self.err(p + ".code", "LOINC %s is not one of the vital signs profile codes" % code, "warning")
            return
        if code == BP_PANEL:
            comps = {_coding(c.get("code"), LOINC).get("code"): c for c in r.get("component") or []}
            for need in (BP_SYS, BP_DIA):
                if need not in comps:
                    self.err(p + ".component", "blood pressure panel needs component %s" % need)
                else:
                    self.quantity(comps[need].get("valueQuantity"), need, p + ".component(%s)" % need,
                                  absent="dataAbsentReason" in comps[need])
            return
        if VITALS[code][1]:
            self.quantity(r.get("valueQuantity"), code, p + ".valueQuantity", absent="dataAbsentReason" in r
                          or "component" in r)

    def quantity(self, q, code, path, absent=False):
        if q is None:
            if not absent:
                self.err(path, "vital-signs: valueQuantity or dataAbsentReason required (vs-2)")
            return
        for k in ("value", "unit", "system", "code"):
            if k not in q:
                self.err(path, "vital-signs: Quantity.%s is required" % k)
        if q.get("system") and q["system"] != UCUM:
            self.err(path + ".system", "vital-signs: units must be UCUM (%s)" % UCUM)
        if q.get("code") and q["code"] not in VITALS[code][1]:
            self.err(path + ".code", "UCUM unit %r not allowed for LOINC %s (allowed: %s)"
                     % (q["code"], code, ", ".join(VITALS[code][1])))

    def inv_Condition(self, r, p):
        ver = _coding(r.get("verificationStatus")).get("code")
        clin = _coding(r.get("clinicalStatus")).get("code")
        if ver == "entered-in-error" and "clinicalStatus" in r:
            self.err(p, "con-5: clinicalStatus SHALL NOT be present if verificationStatus is entered-in-error")
        if any(k.startswith("abatement") for k in r) and clin and clin not in ("inactive", "resolved", "remission"):
            self.err(p, "con-4: abatement present but clinicalStatus is not inactive/resolved/remission")
        cats = [c.get("code") for cat in r.get("category") or [] if isinstance(cat, dict) for c in cat.get("coding") or []]
        if "problem-list-item" in cats and ver != "entered-in-error" and "clinicalStatus" not in r:
            self.err(p, "con-3: clinicalStatus should be present for problem-list items", "warning")

    def inv_AllergyIntolerance(self, r, p):
        ver = _coding(r.get("verificationStatus")).get("code")
        if ver != "entered-in-error" and "clinicalStatus" not in r:
            self.err(p, "ait-1: clinicalStatus SHALL be present if verificationStatus is not entered-in-error")
        if ver == "entered-in-error" and "clinicalStatus" in r:
            self.err(p, "ait-2: clinicalStatus SHALL NOT be present if verificationStatus is entered-in-error")

    def inv_Bundle(self, b, p):
        btype = b.get("type")
        if "total" in b and btype not in ("searchset", "history"):
            self.err(p, "bdl-1: total only when a search or history")
        urls = {}
        entries = b.get("entry") or []
        for k, en in enumerate(entries):
            if not isinstance(en, dict):
                continue
            ep = "%s.entry[%d]" % (p, k)
            if "search" in en and btype != "searchset":
                self.err(ep, "bdl-2: entry.search only when a search")
            if btype in ("batch", "transaction", "history") and "request" not in en:
                self.err(ep, "bdl-3: entry.request mandatory for batch/transaction/history")
            if btype not in ("batch-response", "transaction-response", "history") and "response" in en:
                self.err(ep, "bdl-4: entry.response only for batch-response/transaction-response/history")
            if "resource" not in en and "request" not in en and "response" not in en:
                self.err(ep, "bdl-5: must have a resource unless there's a request or response")
            fu = en.get("fullUrl")
            if fu:
                vid = ((en.get("resource") or {}).get("meta") or {}).get("versionId")
                key = (fu, vid)
                if key in urls:
                    self.err(ep + ".fullUrl", "bdl-7: fullUrl %s duplicated (also entry %d)" % (fu, urls[key]))
                urls[key] = k
                if fu.startswith("urn:uuid:") and not re.match(r"^urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                                                                r"[0-9a-f]{4}-[0-9a-f]{12}$", fu):
                    self.err(ep + ".fullUrl", "malformed urn:uuid")
        known = {u for u, _v in urls}
        types = {en.get("fullUrl"): (en.get("resource") or {}).get("resourceType")
                 for en in entries if isinstance(en, dict)}
        expect = {"subject": ("Patient", "Group"), "patient": ("Patient",), "encounter": ("Encounter",),
                  "context": ("Encounter", "EpisodeOfCare")}
        for k, en in enumerate(entries):
            if not isinstance(en, dict):
                continue
            for path2, ref in _refs(en.get("resource"), "%s.entry[%d].resource" % (p, k)):
                if ref.startswith("urn:uuid:"):
                    if ref not in known:
                        self.err(path2, "reference %s does not resolve inside the bundle" % ref)
                    else:
                        el = path2.rsplit(".", 2)[-2]
                        el = re.sub(r"\[\d+\]$", "", el)
                        if el in expect and types.get(ref) not in expect[el]:
                            self.err(path2, "reference to %s where %s expected" % (types.get(ref), "/".join(expect[el])))


def _refs(obj, path):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "reference" and isinstance(v, str):
                yield path + ".reference", v
            else:
                yield from _refs(v, "%s.%s" % (path, k))
    elif isinstance(obj, list):
        for k, v in enumerate(obj):
            yield from _refs(v, "%s[%d]" % (path, k))


def validate_fhir(resource):
    """Structural validation of a FHIR R4 JSON resource or Bundle (dict or JSON text).
    -> list of {"severity", "path", "message"}; errors make it invalid, warnings do not.
    NOT the official HL7 validator (see module docstring)."""
    if isinstance(resource, (str, bytes)):
        try:
            resource = json.loads(resource)
        except ValueError as e:
            return [{"severity": "error", "path": "", "message": "not JSON: %s" % e}]
    v = _V()
    v.resource(resource, resource.get("resourceType", "") if isinstance(resource, dict) else "")
    return v.issues


def errors_only(issues):
    return [i for i in issues if i["severity"] == "error"]


# ======================================================================================================
# CLI
# ======================================================================================================

def _main(argv):
    import argparse
    ap = argparse.ArgumentParser(description="Stark Covenant HL7 v2 / FHIR R4 bridge")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("hl7-to-fhir")
    a.add_argument("file")
    b = sub.add_parser("validate")
    b.add_argument("file")
    ns = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    if ns.cmd == "hl7-to-fhir":
        with open(ns.file, "rb") as f:
            rec = hl7_to_record(f.read())
        print(json.dumps(record_to_fhir_bundle(rec), indent=2, ensure_ascii=False))
        return 0
    with open(ns.file, encoding="utf-8") as f:
        issues = validate_fhir(f.read())
    for i in issues:
        print("%-7s %s: %s" % (i["severity"], i["path"], i["message"]))
    print("structurally valid" if not errors_only(issues) else "INVALID")
    return 1 if errors_only(issues) else 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
