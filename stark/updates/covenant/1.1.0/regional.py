#!/usr/bin/env python3
"""Stark Covenant - regional: region settings for display (standard library only, Python 3.10+).

Stored values never change with these settings. Records keep one canonical form and only the DISPLAY follows the
person's region settings:
  * dates      stored ISO 8601 (YYYY-MM-DD), timestamps UTC; shown as DD/MM/YYYY, MM/DD/YYYY, DD.MM.YYYY,
               YYYY-MM-DD ... in the chosen IANA time zone (the browser's Intl does the time-zone maths offline)
  * week       first day of the week (1 = Monday ... 7 = Sunday; CLDR weekData defaults per country)
  * units      vitals are stored metric (temp_c, weight_kg, height_cm); "us" shows degF / lb / in
  * glucose    stored mmol/L (glucose_mmol); shown as mmol/L or mg/dL (x 18.016, glucose molar mass 180.16 g/mol)
  * names      given + family name parts; shown family-first where that is the custom (CN, JP, KR, VN, HU, ...)
  * addresses  structured parts shown in the country's own order (postcode before the city in DE / FR / BR,
               prefecture first in JP, ...)
  * phones     stored E.164 (+<country code><national number>) next to what was typed; shown grouped
  * numbers    Latin digits by default (clinical readability); native digits (Arabic-Indic, Bengali, Devanagari...)
               on request

The country table holds DEFAULTS that a clinic or user can override; check them locally (for example, many labs
report glucose in both units, and some countries use a non-Gregorian civil calendar alongside the Gregorian one).
"""
import datetime as _dt
import math
import re

# ------------------------------------------------------------------------------------------------ country table
# cc | date pattern | first day (1 Mon .. 7 Sun) | units | glucose | name order | calling code | trunk prefix |
# default time zone | currency | address format | 12-hour clock (1/0)
_TABLE = """
US|MM/DD/YYYY|7|us|mg/dL|given|1|1|America/New_York|USD|us|1
CA|YYYY-MM-DD|7|metric|mmol/L|given|1|1|America/Toronto|CAD|us|1
MX|DD/MM/YYYY|7|metric|mg/dL|given|52||America/Mexico_City|MXN|latam|1
GT|DD/MM/YYYY|7|metric|mg/dL|given|502||America/Guatemala|GTQ|latam|1
HT|DD/MM/YYYY|1|metric|mg/dL|given|509||America/Port-au-Prince|HTG|generic|0
DO|DD/MM/YYYY|7|metric|mg/dL|given|1|1|America/Santo_Domingo|DOP|latam|1
CO|DD/MM/YYYY|7|metric|mg/dL|given|57||America/Bogota|COP|latam|1
VE|DD/MM/YYYY|7|metric|mg/dL|given|58|0|America/Caracas|VES|latam|1
PE|DD/MM/YYYY|7|metric|mg/dL|given|51||America/Lima|PEN|latam|1
BO|DD/MM/YYYY|1|metric|mg/dL|given|591||America/La_Paz|BOB|latam|1
CL|DD-MM-YYYY|1|metric|mg/dL|given|56||America/Santiago|CLP|latam|1
AR|DD/MM/YYYY|1|metric|mg/dL|given|54|0|America/Argentina/Buenos_Aires|ARS|latam|1
BR|DD/MM/YYYY|7|metric|mg/dL|given|55|0|America/Sao_Paulo|BRL|br|0
GB|DD/MM/YYYY|1|metric|mmol/L|given|44|0|Europe/London|GBP|gb|0
IE|DD/MM/YYYY|1|metric|mmol/L|given|353|0|Europe/Dublin|EUR|gb|0
FR|DD/MM/YYYY|1|metric|mg/dL|given|33|0|Europe/Paris|EUR|eu|0
BE|DD/MM/YYYY|1|metric|mg/dL|given|32|0|Europe/Brussels|EUR|eu|0
NL|DD-MM-YYYY|1|metric|mmol/L|given|31|0|Europe/Amsterdam|EUR|eu|0
DE|DD.MM.YYYY|1|metric|mg/dL|given|49|0|Europe/Berlin|EUR|eu|0
AT|DD.MM.YYYY|1|metric|mg/dL|given|43|0|Europe/Vienna|EUR|eu|0
CH|DD.MM.YYYY|1|metric|mmol/L|given|41|0|Europe/Zurich|CHF|eu|0
IT|DD/MM/YYYY|1|metric|mg/dL|given|39||Europe/Rome|EUR|eu|0
ES|DD/MM/YYYY|1|metric|mg/dL|given|34||Europe/Madrid|EUR|eu|0
PT|DD/MM/YYYY|7|metric|mg/dL|given|351||Europe/Lisbon|EUR|eu|0
PL|DD.MM.YYYY|1|metric|mg/dL|given|48||Europe/Warsaw|PLN|eu|0
SE|YYYY-MM-DD|1|metric|mmol/L|given|46|0|Europe/Stockholm|SEK|eu|0
NO|DD.MM.YYYY|1|metric|mmol/L|given|47||Europe/Oslo|NOK|eu|0
DK|DD.MM.YYYY|1|metric|mmol/L|given|45||Europe/Copenhagen|DKK|eu|0
FI|DD.MM.YYYY|1|metric|mmol/L|given|358|0|Europe/Helsinki|EUR|eu|0
HU|YYYY.MM.DD|1|metric|mmol/L|family|36|06|Europe/Budapest|HUF|eu|0
GR|DD/MM/YYYY|1|metric|mg/dL|given|30||Europe/Athens|EUR|eu|1
TR|DD.MM.YYYY|1|metric|mg/dL|given|90|0|Europe/Istanbul|TRY|eu|0
UA|DD.MM.YYYY|1|metric|mmol/L|given|380|0|Europe/Kyiv|UAH|ru|0
RU|DD.MM.YYYY|1|metric|mmol/L|given|7|8|Europe/Moscow|RUB|ru|0
KZ|DD.MM.YYYY|1|metric|mmol/L|given|7|8|Asia/Almaty|KZT|ru|0
SA|DD/MM/YYYY|7|metric|mg/dL|given|966|0|Asia/Riyadh|SAR|generic|1
AE|DD/MM/YYYY|1|metric|mg/dL|given|971|0|Asia/Dubai|AED|generic|1
YE|DD/MM/YYYY|7|metric|mg/dL|given|967|0|Asia/Aden|YER|generic|1
JO|DD/MM/YYYY|6|metric|mg/dL|given|962|0|Asia/Amman|JOD|generic|1
LB|DD/MM/YYYY|1|metric|mg/dL|given|961|0|Asia/Beirut|LBP|generic|1
SY|DD/MM/YYYY|6|metric|mg/dL|given|963|0|Asia/Damascus|SYP|generic|1
IQ|DD/MM/YYYY|6|metric|mg/dL|given|964|0|Asia/Baghdad|IQD|generic|1
EG|DD/MM/YYYY|6|metric|mg/dL|given|20|0|Africa/Cairo|EGP|generic|1
SD|DD/MM/YYYY|6|metric|mg/dL|given|249|0|Africa/Khartoum|SDG|generic|1
LY|DD/MM/YYYY|6|metric|mg/dL|given|218|0|Africa/Tripoli|LYD|generic|1
DZ|DD/MM/YYYY|6|metric|mg/dL|given|213|0|Africa/Algiers|DZD|eu|1
MA|DD/MM/YYYY|1|metric|mg/dL|given|212|0|Africa/Casablanca|MAD|eu|0
TN|DD/MM/YYYY|1|metric|mg/dL|given|216||Africa/Tunis|TND|eu|1
IL|DD.MM.YYYY|7|metric|mg/dL|given|972|0|Asia/Jerusalem|ILS|eu|0
IR|YYYY/MM/DD|6|metric|mg/dL|given|98|0|Asia/Tehran|IRR|generic|0
AF|YYYY/MM/DD|6|metric|mg/dL|given|93|0|Asia/Kabul|AFN|generic|0
PK|DD/MM/YYYY|7|metric|mg/dL|given|92|0|Asia/Karachi|PKR|in|1
IN|DD/MM/YYYY|7|metric|mg/dL|given|91|0|Asia/Kolkata|INR|in|1
NP|YYYY-MM-DD|7|metric|mg/dL|given|977|0|Asia/Kathmandu|NPR|in|0
BD|DD/MM/YYYY|7|metric|mmol/L|given|880|0|Asia/Dhaka|BDT|in|1
LK|YYYY-MM-DD|1|metric|mg/dL|given|94|0|Asia/Colombo|LKR|in|0
CN|YYYY-MM-DD|1|metric|mmol/L|family|86|0|Asia/Shanghai|CNY|cn|0
HK|DD/MM/YYYY|7|metric|mmol/L|family|852||Asia/Hong_Kong|HKD|gb|1
TW|YYYY/MM/DD|7|metric|mg/dL|family|886|0|Asia/Taipei|TWD|cn|1
JP|YYYY/MM/DD|7|metric|mg/dL|family|81|0|Asia/Tokyo|JPY|jp|0
KR|YYYY.MM.DD|7|metric|mg/dL|family|82|0|Asia/Seoul|KRW|kr|1
VN|DD/MM/YYYY|1|metric|mmol/L|family|84|0|Asia/Ho_Chi_Minh|VND|generic|0
TH|DD/MM/YYYY|7|metric|mg/dL|given|66|0|Asia/Bangkok|THB|generic|0
MM|DD/MM/YYYY|7|metric|mg/dL|given|95|0|Asia/Yangon|MMK|generic|0
KH|DD/MM/YYYY|7|metric|mg/dL|family|855|0|Asia/Phnom_Penh|KHR|generic|1
MY|DD/MM/YYYY|1|metric|mmol/L|given|60|0|Asia/Kuala_Lumpur|MYR|gb|1
SG|DD/MM/YYYY|7|metric|mmol/L|given|65||Asia/Singapore|SGD|gb|1
ID|DD/MM/YYYY|7|metric|mg/dL|given|62|0|Asia/Jakarta|IDR|id|0
PH|MM/DD/YYYY|7|metric|mg/dL|given|63|0|Asia/Manila|PHP|us|1
AU|DD/MM/YYYY|1|metric|mmol/L|given|61|0|Australia/Sydney|AUD|au|1
NZ|DD/MM/YYYY|1|metric|mmol/L|given|64|0|Pacific/Auckland|NZD|au|1
PG|DD/MM/YYYY|1|metric|mmol/L|given|675||Pacific/Port_Moresby|PGK|generic|1
KE|DD/MM/YYYY|7|metric|mmol/L|given|254|0|Africa/Nairobi|KES|ke|0
TZ|DD/MM/YYYY|1|metric|mmol/L|given|255|0|Africa/Dar_es_Salaam|TZS|ke|0
UG|DD/MM/YYYY|1|metric|mmol/L|given|256|0|Africa/Kampala|UGX|ke|0
RW|DD/MM/YYYY|1|metric|mmol/L|given|250||Africa/Kigali|RWF|ke|0
CD|DD/MM/YYYY|1|metric|mg/dL|given|243|0|Africa/Kinshasa|CDF|generic|0
ET|DD/MM/YYYY|7|metric|mg/dL|given|251|0|Africa/Addis_Ababa|ETB|ke|1
SO|DD/MM/YYYY|1|metric|mg/dL|given|252|0|Africa/Mogadishu|SOS|ke|1
SS|DD/MM/YYYY|1|metric|mg/dL|given|211|0|Africa/Juba|SSP|ke|1
NG|DD/MM/YYYY|1|metric|mg/dL|given|234|0|Africa/Lagos|NGN|ng|0
GH|DD/MM/YYYY|1|metric|mmol/L|given|233|0|Africa/Accra|GHS|ng|1
SN|DD/MM/YYYY|1|metric|mg/dL|given|221||Africa/Dakar|XOF|eu|0
CI|DD/MM/YYYY|1|metric|mg/dL|given|225||Africa/Abidjan|XOF|eu|0
ML|DD/MM/YYYY|1|metric|mg/dL|given|223||Africa/Bamako|XOF|eu|0
CM|DD/MM/YYYY|1|metric|mg/dL|given|237||Africa/Douala|XAF|eu|0
MZ|DD/MM/YYYY|7|metric|mg/dL|given|258||Africa/Maputo|MZN|br|0
AO|DD/MM/YYYY|1|metric|mg/dL|given|244||Africa/Luanda|AOA|br|0
MW|DD/MM/YYYY|1|metric|mmol/L|given|265|0|Africa/Blantyre|MWK|ke|1
ZM|DD/MM/YYYY|1|metric|mmol/L|given|260|0|Africa/Lusaka|ZMW|ke|1
ZW|DD/MM/YYYY|7|metric|mmol/L|given|263|0|Africa/Harare|USD|ke|0
ZA|YYYY/MM/DD|7|metric|mmol/L|given|27|0|Africa/Johannesburg|ZAR|za|0
"""
FIELDS = ("date", "first_day", "units", "glucose", "name_order", "calling_code", "trunk", "tz", "currency",
          "address", "hour12")
REGIONS = {}
for _ln in _TABLE.strip().splitlines():
    _p = _ln.split("|")
    REGIONS[_p[0]] = {"date": _p[1], "first_day": int(_p[2]), "units": _p[3], "glucose": _p[4],
                      "name_order": _p[5], "calling_code": _p[6], "trunk": _p[7], "tz": _p[8], "currency": _p[9],
                      "address": _p[10], "hour12": _p[11] == "1"}
del _ln, _p

DATE_PATTERNS = ("DD/MM/YYYY", "MM/DD/YYYY", "DD.MM.YYYY", "DD-MM-YYYY", "YYYY-MM-DD", "YYYY/MM/DD", "YYYY.MM.DD")
UNITS = ("metric", "us")
GLUCOSE = ("mmol/L", "mg/dL")
NAME_ORDERS = ("given", "family")
DIGITS = ("latn", "native")
GLUCOSE_FACTOR = 18.016            # mg/dL per mmol/L for glucose (180.16 g/mol / 10)

# ------------------------------------------------------------------------------------------------ addresses
# lines of field tokens; a token that is not a field name is literal text. region / postal give the label keys.
ADDRESS_FORMATS = {
    "us":      {"lines": [["line1"], ["line2"], ["city", ", ", "region", " ", "postal"]], "region": "state",
                "postal": "zip"},
    "gb":      {"lines": [["line1"], ["line2"], ["city"], ["region"], ["postal"]], "region": "county",
                "postal": "postcode"},
    "eu":      {"lines": [["line1"], ["line2"], ["postal", " ", "city"], ["region"]], "region": "region",
                "postal": "postcode"},
    "latam":   {"lines": [["line1"], ["line2"], ["postal", " ", "city"], ["region"]], "region": "state",
                "postal": "postcode"},
    "br":      {"lines": [["line1"], ["line2"], ["city", " - ", "region"], ["postal"]], "region": "state",
                "postal": "cep"},
    "ru":      {"lines": [["line1"], ["line2"], ["city"], ["region"], ["postal"]], "region": "region",
                "postal": "postcode"},
    "in":      {"lines": [["line1"], ["line2"], ["city", " ", "postal"], ["region"]], "region": "state",
                "postal": "pin"},
    "cn":      {"lines": [["region", " ", "city"], ["line1"], ["line2"], ["postal"]], "region": "province",
                "postal": "postcode"},
    "jp":      {"lines": [["postal"], ["region", " ", "city"], ["line1"], ["line2"]], "region": "prefecture",
                "postal": "postcode"},
    "kr":      {"lines": [["region", " ", "city"], ["line1"], ["line2"], ["postal"]], "region": "province",
                "postal": "postcode"},
    "id":      {"lines": [["line1"], ["line2"], ["city", " ", "postal"], ["region"]], "region": "province",
                "postal": "postcode"},
    "au":      {"lines": [["line1"], ["line2"], ["city", " ", "region", " ", "postal"]], "region": "state",
                "postal": "postcode"},
    "ke":      {"lines": [["line1"], ["line2"], ["city", " ", "postal"], ["region"]], "region": "county",
                "postal": "postcode"},
    "ng":      {"lines": [["line1"], ["line2"], ["city", " ", "postal"], ["region"]], "region": "state",
                "postal": "postcode"},
    "za":      {"lines": [["line1"], ["line2"], ["city"], ["region"], ["postal"]], "region": "province",
                "postal": "postcode"},
    "generic": {"lines": [["line1"], ["line2"], ["city", " ", "postal"], ["region"]], "region": "region",
                "postal": "postcode"},
}
ADDRESS_PARTS = ("line1", "line2", "city", "region", "postal")


def region_profile(cc):
    return REGIONS.get(str(cc or "").upper())


def default_region_for_locale(locale_region):
    return locale_region if locale_region in REGIONS else "US"


def clean_prefs(d):
    """Validate user / clinic region preferences. -> clean dict (unknown keys dropped). Raises ValueError."""
    if d is None:
        return {}
    if not isinstance(d, dict):
        raise ValueError("preferences must be an object")
    out = {}
    if d.get("region") not in (None, ""):
        cc = str(d["region"]).upper()
        if cc not in REGIONS:
            raise ValueError("unknown region %s" % cc[:4])
        out["region"] = cc
    for k, allowed in (("date", DATE_PATTERNS), ("units", UNITS), ("glucose", GLUCOSE),
                       ("name_order", NAME_ORDERS), ("digits", DIGITS)):
        v = d.get(k)
        if v not in (None, ""):
            if v not in allowed:
                raise ValueError("%s must be one of %s" % (k, ", ".join(allowed)))
            out[k] = v
    if d.get("first_day") not in (None, ""):
        try:
            fd = int(d["first_day"])
        except (TypeError, ValueError):
            raise ValueError("first_day must be 1-7")
        if not 1 <= fd <= 7:
            raise ValueError("first_day must be 1-7 (1 = Monday, 7 = Sunday)")
        out["first_day"] = fd
    if d.get("tz") not in (None, ""):
        if not valid_tz(d["tz"]):
            raise ValueError("unknown time zone")
        out["tz"] = d["tz"]
    if d.get("hour12") not in (None, ""):
        out["hour12"] = bool(d["hour12"]) if not isinstance(d["hour12"], str) else d["hour12"] in ("1", "true", "yes")
    return out


def effective(prefs=None, locale_region=None):
    """Defaults of the chosen region (or the locale's region), overridden by explicit preferences."""
    prefs = prefs or {}
    cc = prefs.get("region") or default_region_for_locale(locale_region)
    base = dict(REGIONS[cc])
    for k in ("date", "first_day", "units", "glucose", "name_order", "tz", "digits", "hour12"):
        if k in prefs:
            base[k] = prefs[k]
    base.setdefault("digits", "latn")
    base["region"] = cc
    fmt = ADDRESS_FORMATS.get(base["address"], ADDRESS_FORMATS["generic"])
    base["address_format"] = fmt
    base["regions"] = sorted(REGIONS)
    return base


# ------------------------------------------------------------------------------------------------ time zones
_TZ_RE = re.compile(r"^(UTC|Etc/(UTC|GMT([+-]\d{1,2})?)|[A-Z][A-Za-z_]+(/[A-Z][A-Za-z0-9_+\-]+){1,2})$")


def valid_tz(name):
    """IANA name check. Uses zoneinfo when the system has a tz database; otherwise the name shape and the
    known default zones (the browser's Intl does the real conversion)."""
    if not isinstance(name, str) or len(name) > 64 or not _TZ_RE.match(name):
        return False
    try:
        import zoneinfo
        try:
            zoneinfo.ZoneInfo(name)
            return True
        except zoneinfo.ZoneInfoNotFoundError:
            if zoneinfo.available_timezones():
                return False
        except (ValueError, OSError):
            return False
    except ImportError:
        pass
    return name in {r["tz"] for r in REGIONS.values()} or name.startswith(("UTC", "Etc/")) or bool(_TZ_RE.match(name))


def utc_offset_minutes(ts_iso, tz):
    """Offset of tz at an instant, when the system tz database exists (None otherwise)."""
    try:
        import zoneinfo
        z = zoneinfo.ZoneInfo(tz)
    except Exception:
        return None
    t = _dt.datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
    off = t.astimezone(z).utcoffset()
    return int(off.total_seconds() // 60) if off is not None else None


# ------------------------------------------------------------------------------------------------ dates
def format_date(iso, pattern="YYYY-MM-DD"):
    """'1980-03-14' -> per pattern. Anything that is not an ISO date is returned unchanged."""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(iso or ""))
    if not m:
        return str(iso or "")
    y, mo, d = m.groups()
    return pattern.replace("YYYY", y).replace("MM", mo).replace("DD", d)


def parse_date(text, pattern):
    """Inverse of format_date -> ISO, or None."""
    rx = re.escape(pattern).replace("YYYY", r"(?P<y>\d{4})").replace("MM", r"(?P<m>\d{1,2})").replace(
        "DD", r"(?P<d>\d{1,2})")
    m = re.fullmatch(rx, str(text or "").strip())
    if not m:
        return None
    try:
        return _dt.date(int(m["y"]), int(m["m"]), int(m["d"])).isoformat()
    except ValueError:
        return None


def start_of_week(iso, first_day):
    """ISO date of the first day of the week containing iso (first_day 1 = Monday .. 7 = Sunday)."""
    d = _dt.date.fromisoformat(iso[:10])
    back = (d.isoweekday() - first_day) % 7
    return (d - _dt.timedelta(days=back)).isoformat()


def week_days(first_day):
    """ISO weekday numbers in display order."""
    return [((first_day - 1 + i) % 7) + 1 for i in range(7)]


# ------------------------------------------------------------------------------------------------ units
def _r(x, nd):
    q = 10 ** nd
    return math.floor(x * q + 0.5) / q


def c_to_f(c):
    return c * 9.0 / 5.0 + 32.0


def f_to_c(f):
    return (f - 32.0) * 5.0 / 9.0


def kg_to_lb(kg):
    return kg / 0.45359237


def lb_to_kg(lb):
    return lb * 0.45359237


def cm_to_in(cm):
    return cm / 2.54


def in_to_cm(i):
    return i * 2.54


def mmol_to_mgdl(mmol):
    return mmol * GLUCOSE_FACTOR


def mgdl_to_mmol(mgdl):
    return mgdl / GLUCOSE_FACTOR


# canonical stored key -> (metric unit, us unit, to_us, from_us, decimals metric, decimals us)
VITAL_UNITS = {
    "temp_c": ("°C", "°F", c_to_f, f_to_c, 1, 1),
    "weight_kg": ("kg", "lb", kg_to_lb, lb_to_kg, 1, 1),
    "height_cm": ("cm", "in", cm_to_in, in_to_cm, 1, 1),
}


def display_vital(key, value, prefs):
    """Stored canonical value -> (shown number, unit) for these prefs. The stored value is not touched."""
    v = float(value)
    if key == "glucose_mmol":
        if (prefs or {}).get("glucose") == "mg/dL":
            return _r(mmol_to_mgdl(v), 0), "mg/dL"
        return _r(v, 1), "mmol/L"
    if key in VITAL_UNITS:
        mu, uu, to_us, _from, dm, du = VITAL_UNITS[key]
        if (prefs or {}).get("units") == "us":
            return _r(to_us(v), du), uu
        return _r(v, dm), mu
    return v, {"bp_sys": "mmHg", "bp_dia": "mmHg", "pulse": "/min", "resp": "/min", "spo2": "%"}.get(key, "")


def to_canonical(key, shown, prefs):
    """A number typed in the person's display unit -> the canonical stored value (4 decimals)."""
    v = float(shown)
    if key == "glucose_mmol":
        return _r(mgdl_to_mmol(v), 4) if (prefs or {}).get("glucose") == "mg/dL" else v
    if key in VITAL_UNITS and (prefs or {}).get("units") == "us":
        return _r(VITAL_UNITS[key][3](v), 4)
    return v


# ------------------------------------------------------------------------------------------------ names
_CJK = re.compile(r"^[぀-ヿ㐀-鿿가-힯豈-﫿]+$")


def display_name(d, order="given"):
    """Demographics -> shown name. given + family parts follow the order; otherwise the full 'name' as entered."""
    g = str((d or {}).get("given_name") or "").strip()
    f = str((d or {}).get("family_name") or "").strip()
    if not (g and f):
        return str((d or {}).get("name") or (f or g)).strip()
    first, second = (f, g) if order == "family" else (g, f)
    sep = "" if _CJK.match(first) and _CJK.match(second) else " "
    return first + sep + second


# ------------------------------------------------------------------------------------------------ addresses
def address_fields(fmt_id):
    """Address part names in the order the country writes them (for the form)."""
    fmt = ADDRESS_FORMATS.get(fmt_id, ADDRESS_FORMATS["generic"])
    seen = []
    for line in fmt["lines"]:
        for tok in line:
            if tok in ADDRESS_PARTS and tok not in seen:
                seen.append(tok)
    return seen


def format_address(parts, fmt_id):
    """Structured parts -> lines (empty parts and their separators dropped)."""
    fmt = ADDRESS_FORMATS.get(fmt_id, ADDRESS_FORMATS["generic"])
    lines = []
    for line in fmt["lines"]:
        acc, pend = [], ""
        for tok in line:
            if tok in ADDRESS_PARTS:
                v = str((parts or {}).get(tok) or "").strip()
                if v:
                    if acc:
                        acc.append(pend)
                    acc.append(v)
                pend = ""
            else:
                pend = tok
        s = "".join(acc).strip()
        if s:
            lines.append(s)
    return lines


# ------------------------------------------------------------------------------------------------ phones
def to_e164(raw, region="US"):
    """Typed phone number -> E.164 '+CCNSN', or None when it cannot be made valid.
    '+...' and '00...' (or '011...' in the NANP) are international; otherwise the region's calling code is added
    after removing its trunk prefix (0 in most countries, 1 in the NANP, 8 in RU / KZ, 06 in HU)."""
    s = str(raw or "").strip()
    if not s:
        return None
    s = re.sub(r"(?i)\s*(ext\.?|x|#)\s*\d+$", "", s)
    if re.search(r"[A-Za-z]", s):
        return None
    plus = s.startswith("+")
    digits = re.sub(r"\D", "", s)
    prof = REGIONS.get(str(region or "").upper()) or {}
    cc = prof.get("calling_code", "")
    if plus:
        full = digits
    elif digits.startswith("00"):
        full = digits[2:]
    elif cc == "1" and digits.startswith("011"):
        full = digits[3:]
    else:
        if not cc:
            return None
        nat = digits
        trunk = prof.get("trunk", "")
        if trunk and nat.startswith(trunk) and not (cc == "1" and len(nat) == 10):
            nat = nat[len(trunk):]
        if cc == "1" and len(nat) != 10:
            return None
        full = cc + nat
    if not 8 <= len(full) <= 15 or full[0] == "0":
        return None
    return "+" + full


def calling_code_of(e164):
    """Longest known calling code prefix of an E.164 number."""
    d = str(e164 or "").lstrip("+")
    codes = sorted({r["calling_code"] for r in REGIONS.values() if r["calling_code"]}, key=len, reverse=True)
    for c in codes:
        if d.startswith(c):
            return c
    return d[:1]


def format_phone(e164):
    """'+4930123456789' -> '+49 301 234 567 89' style grouping (NANP: '+1 555 123 4567'). Readability only."""
    if not re.fullmatch(r"\+\d{8,15}", str(e164 or "")):
        return str(e164 or "")
    cc = calling_code_of(e164)
    nsn = e164[1 + len(cc):]
    if cc == "1" and len(nsn) == 10:
        groups = [nsn[:3], nsn[3:6], nsn[6:]]
    else:
        groups = []
        rest = nsn
        while len(rest) > 4:
            groups.append(rest[:3])
            rest = rest[3:]
        groups.append(rest)
    return "+" + cc + " " + " ".join(groups)


# ------------------------------------------------------------------------------------------------ browser mirror
REGION_JS = r"""
const RG=(function(){
const UNITS={temp_c:['°C','°F',c=>c*9/5+32,f=>(f-32)*5/9,1,1],weight_kg:['kg','lb',k=>k/0.45359237,l=>l*0.45359237,1,1],height_cm:['cm','in',c=>c/2.54,i=>i*2.54,1,1]};
const GF=18.016;
function r(x,nd){const q=Math.pow(10,nd);return Math.floor(x*q+0.5)/q}
function P(){return (typeof I18N!=='undefined'&&I18N.region)||{}}
function date(iso,pat){const m=/^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso||''));if(!m)return String(iso||'');pat=pat||P().date||'YYYY-MM-DD';
 return pat.replace('YYYY',m[1]).replace('MM',m[2]).replace('DD',m[3])}
function ts(iso,p){p=p||P();if(!iso)return '';const d=new Date(iso);if(isNaN(d))return String(iso);let tz=p.tz||'UTC',utc=!p.tz;
 const o={timeZone:tz,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:!!p.hour12,hourCycle:p.hour12?'h12':'h23'};
 let parts;try{parts=new Intl.DateTimeFormat('en-US-u-nu-latn',o).formatToParts(d)}catch(e){o.timeZone='UTC';tz='UTC';utc=true;parts=new Intl.DateTimeFormat('en-US-u-nu-latn',o).formatToParts(d)}
 const g=t=>(parts.find(x=>x.type===t)||{}).value||'';
 const day=date(g('year')+'-'+g('month')+'-'+g('day'),p.date);
 let time=g('hour')+':'+g('minute');
 if(p.hour12){try{const loc=((typeof I18N!=='undefined'&&I18N.intl)||'en')+'-u-nu-latn';
  time=new Intl.DateTimeFormat(loc,{timeZone:tz,hour:'numeric',minute:'2-digit',hour12:true}).format(d)}catch(e){}}
 return day+' '+time+(utc?' UTC':'')}
function vital(k,v,p){p=p||P();v=Number(v);
 if(k==='glucose_mmol')return p.glucose==='mg/dL'?[r(v*GF,0),'mg/dL']:[r(v,1),'mmol/L'];
 if(UNITS[k]){const u=UNITS[k];return p.units==='us'?[r(u[2](v),u[5]),u[1]]:[r(v,u[4]),u[0]]}
 return [v,{bp_sys:'mmHg',bp_dia:'mmHg',pulse:'/min',resp:'/min',spo2:'%'}[k]||'']}
function unit(k,p){return vital(k,1,p)[1]}
function canon(k,shown,p){p=p||P();const v=Number(shown);
 if(k==='glucose_mmol')return p.glucose==='mg/dL'?r(v/GF,4):v;
 if(UNITS[k]&&p.units==='us')return r(UNITS[k][3](v),4);return v}
const CJK=/^[぀-ヿ㐀-鿿가-힯豈-﫿]+$/;
function name(d,order){order=order||P().name_order;const g=String((d||{}).given_name||'').trim(),f=String((d||{}).family_name||'').trim();
 if(!(g&&f))return String((d||{}).name||f||g).trim();const a=order==='family'?[f,g]:[g,f];return a[0]+(CJK.test(a[0])&&CJK.test(a[1])?'':' ')+a[1]}
const PARTS=['line1','line2','city','region','postal'];
function addrFields(fmt){fmt=fmt||P().address_format;const seen=[];if(!fmt)return PARTS.slice();for(const l of fmt.lines)for(const t of l)if(PARTS.includes(t)&&!seen.includes(t))seen.push(t);return seen}
function addr(parts,fmt){fmt=fmt||P().address_format;const out=[];if(!fmt)return out;
 for(const line of fmt.lines){const acc=[];let pend='';for(const t of line){if(PARTS.includes(t)){const v=String((parts||{})[t]||'').trim();if(v){if(acc.length)acc.push(pend);acc.push(v)}pend=''}else pend=t}
  const s=acc.join('').trim();if(s)out.push(s)}return out}
function phone(e,codes){e=String(e||'');if(!/^\+\d{8,15}$/.test(e))return e;codes=codes||P().calling_codes||[];let cc=e.slice(1,2);
 for(const c of codes)if(e.slice(1).startsWith(c)){cc=c;break}
 const nsn=e.slice(1+cc.length);let g=[];if(cc==='1'&&nsn.length===10)g=[nsn.slice(0,3),nsn.slice(3,6),nsn.slice(6)];
 else{let rest=nsn;while(rest.length>4){g.push(rest.slice(0,3));rest=rest.slice(3)}g.push(rest)}return '+'+cc+' '+g.join(' ')}
function weekDays(fd){fd=fd||P().first_day||1;const o=[];for(let i=0;i<7;i++)o.push(((fd-1+i)%7)+1);return o}
function startOfWeek(iso,fd){const d=new Date(iso.slice(0,10)+'T00:00:00Z');const wd=((d.getUTCDay()+6)%7)+1;const back=(wd-fd+7)%7;
 return new Date(d.getTime()-back*86400000).toISOString().slice(0,10)}
return {date,ts,vital,unit,canon,name,addrFields,addr,phone,weekDays,startOfWeek,GF}})();
"""


def calling_codes():
    return sorted({r["calling_code"] for r in REGIONS.values() if r["calling_code"]}, key=len, reverse=True)


def client_region(prefs=None, locale_region=None):
    """effective() plus what the browser mirror needs (calling codes for phone grouping)."""
    e = effective(prefs, locale_region)
    e["calling_codes"] = calling_codes()
    return e
