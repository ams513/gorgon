#!/usr/bin/env python3
"""Stark Covenant - i18n: the locale layer for every user-facing UI string. Standard library only, Python 3.10+.

What it does
  * One string catalog per locale in i18n/<locale>.json. English (en.json) is the source and is complete; every
    other catalog is a MACHINE DRAFT until a named human reviewer signs each string off (see "Review" below).
  * Fallback: pt-BR -> pt -> en, zh-Hans -> zh -> en, ... A key missing in a catalog is shown in English.
  * ICU MessageFormat subset (the part UI text needs), the same in Python (here) and in the browser (JS_RUNTIME):
      {name}                      a value
      {n, number}                 a locale-formatted number
      {n, plural, =0 {..} one {# item} other {# items}}   CLDR plural categories, '#' = the number
      {x, select, a {..} other {..}}
      ''  is a literal apostrophe;  '{ ... }'  quotes braces.   A lone ' (French l'aide) stays literal.
  * CLDR plural rules for every shipped locale (+ he, fa), written out by hand so Python and JS agree exactly.
  * Text direction: ar, he, fa and ur are right-to-left (dir="rtl" on the page; PDFs use polyglot_engine's
    bidi algorithm and ot_shaper).
  * Safety wording that must never depend on an unreviewed translation ("paired" keys: the disclaimer, the
    no-compliance-claim line, the emergency safety sentence) is shown in the draft language AND in English until
    that string is reviewed. Clinical labels ("clinical" keys: allergies, medicines, vital signs ...) are shown
    as "draft (English)" until reviewed.

Review (the only way a translation becomes "reviewed")
  python i18n.py export-review fr fr_review.csv      CSV: key, English, translation, status, reviewer,
                                                       review_date, qualification, comment (UTF-8 with BOM)
  python i18n.py import-review fr fr_reviewed.csv    a row counts as reviewed ONLY with a reviewer name and an
                                                       ISO review date (not in the future); its translation must
                                                       pass the same checks as every catalog (placeholders, plural
                                                       branches, syntax). The record keeps a hash of the English
                                                       source and of the translation: if either changes later,
                                                       that string goes back to "needs native review".
  python i18n.py status | check                       per-locale status / full validation (exit 1 on a problem)
  The catalog-level status is COMPUTED from these records on every load; a hand-edited "status" is ignored.

Honest limits
  * Machine drafts can be wrong, including in ways that matter clinically. The UI says so on every screen in a
    draft language. Nothing here replaces a qualified native-speaking reviewer.
  * Content written by other modules (consent explanations, assistant replies, nutrition plans, coverage letters,
    research briefs, server error details with values in them) is English unless that module localises it.
"""
import csv
import datetime as _dt
import hashlib
import io
import json
import os
import re
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
I18N_DIR = os.path.join(HERE, "i18n")
SOURCE = "en"
SOURCE_STATUS, DRAFT, PARTIAL, REVIEWED = "source", "needs_native_review", "partially_reviewed", "reviewed"
DRAFT_LABEL = "needs native review"

# code: English name, endonym, text direction, Intl / BCP 47 tag, default region (ISO 3166-1)
LOCALES = {
    "en":      {"name": "English", "native": "English", "dir": "ltr", "intl": "en", "region": "US"},
    "es":      {"name": "Spanish", "native": "Español", "dir": "ltr", "intl": "es", "region": "ES"},
    "fr":      {"name": "French", "native": "Français", "dir": "ltr", "intl": "fr", "region": "FR"},
    "pt-BR":   {"name": "Portuguese (Brazil)", "native": "Português (Brasil)", "dir": "ltr", "intl": "pt-BR",
                "region": "BR"},
    "ar":      {"name": "Arabic", "native": "العربية", "dir": "rtl", "intl": "ar", "region": "EG"},
    "hi":      {"name": "Hindi", "native": "हिन्दी", "dir": "ltr", "intl": "hi", "region": "IN"},
    "zh-Hans": {"name": "Chinese (Simplified)", "native": "简体中文", "dir": "ltr", "intl": "zh-Hans", "region": "CN"},
    "sw":      {"name": "Swahili", "native": "Kiswahili", "dir": "ltr", "intl": "sw", "region": "KE"},
    "id":      {"name": "Indonesian", "native": "Bahasa Indonesia", "dir": "ltr", "intl": "id", "region": "ID"},
    "ru":      {"name": "Russian", "native": "Русский", "dir": "ltr", "intl": "ru", "region": "RU"},
    "bn":      {"name": "Bengali", "native": "বাংলা", "dir": "ltr", "intl": "bn", "region": "BD"},
    "ur":      {"name": "Urdu", "native": "اردو", "dir": "rtl", "intl": "ur", "region": "PK"},
    "de":      {"name": "German", "native": "Deutsch", "dir": "ltr", "intl": "de", "region": "DE"},
    "ja":      {"name": "Japanese", "native": "日本語", "dir": "ltr", "intl": "ja", "region": "JP"},
    # right-to-left locales the layout supports; no catalog is shipped yet (they fall back to English text)
    "he":      {"name": "Hebrew", "native": "עברית", "dir": "rtl", "intl": "he", "region": "IL"},
    "fa":      {"name": "Persian", "native": "فارسی", "dir": "rtl", "intl": "fa", "region": "IR"},
}
SHIPPED = ("en", "es", "fr", "pt-BR", "ar", "hi", "zh-Hans", "sw", "id", "ru", "bn", "ur", "de", "ja")
RTL = frozenset(k for k, v in LOCALES.items() if v["dir"] == "rtl")
_ALIASES = {"pt": "pt-BR", "zh": "zh-Hans", "zh-cn": "zh-Hans", "zh-sg": "zh-Hans", "zh-hans-cn": "zh-Hans",
            "iw": "he", "in": "id", "pt-pt": "pt-BR"}
# Most important script per locale: a draft whose strings do not use it was not really translated.
LOCALE_SCRIPT = {"ar": "\u0600-\u06ff", "ur": "\u0600-\u06ff", "fa": "\u0600-\u06ff", "he": "\u0590-\u05ff",
                 "hi": "\u0900-\u097f", "bn": "\u0980-\u09ff", "ru": "\u0400-\u04ff",
                 "zh-Hans": "\u4e00-\u9fff", "ja": "\u3040-\u30ff\u4e00-\u9fff"}


def canonical(code):
    """'pt_br' / 'PT-br' / 'zh-CN' -> a LOCALES code, or None."""
    if not isinstance(code, str):
        return None
    c = code.strip().replace("_", "-")
    if not re.match(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8}){0,3}$", c):
        return None
    low = c.lower()
    if low in _ALIASES:
        return _ALIASES[low]
    for k in LOCALES:
        if k.lower() == low:
            return k
    base = low.split("-")[0]
    if base in _ALIASES:
        return _ALIASES[base]
    return base if base in LOCALES else None


def fallback_chain(code):
    c = canonical(code) or SOURCE
    chain = [c]
    base = c.split("-")[0]
    if base != c and base in LOCALES:
        chain.append(base)
    if SOURCE not in chain:
        chain.append(SOURCE)
    return chain


def negotiate(accept_language, available=SHIPPED, default=SOURCE):
    """Accept-Language header -> best available locale (q-values honoured; region variants map to the base)."""
    best = []
    for i, part in enumerate(str(accept_language or "").split(",")[:20]):
        bits = part.strip().split(";")
        tag = bits[0].strip()
        q = 1.0
        for b in bits[1:]:
            b = b.strip()
            if b.startswith("q="):
                try:
                    q = float(b[2:])
                except ValueError:
                    q = 0.0
        c = canonical(tag)
        if c and c in available and q > 0:
            best.append((-q, i, c))
    return sorted(best)[0][2] if best else default


# ======================================================================================================
# CLDR plural rules (cldr-common 44, cardinal), by hand so Python and the browser runtime agree exactly
# ======================================================================================================
PLURAL_CATEGORIES = {
    "en": ("one", "other"), "de": ("one", "other"), "sw": ("one", "other"), "ur": ("one", "other"),
    "es": ("one", "many", "other"), "fr": ("one", "many", "other"), "pt": ("one", "many", "other"),
    "hi": ("one", "other"), "bn": ("one", "other"), "fa": ("one", "other"),
    "ar": ("zero", "one", "two", "few", "many", "other"), "he": ("one", "two", "other"),
    "ru": ("one", "few", "many", "other"), "zh": ("other",), "ja": ("other",), "id": ("other",),
}


def _operands(n):
    """CLDR plural operands (n, i, v, f) from an int, float or decimal string ('1.0' keeps v=1)."""
    s = str(n).strip().lstrip("-") if not isinstance(n, float) else repr(abs(n))
    if "e" in s.lower():
        s = "%f" % float(s)
        s = s.rstrip("0").rstrip(".") if "." in s else s
    if isinstance(n, float) and s.endswith(".0"):
        s = s[:-2]
    ip, _, fp = s.partition(".")
    i = int(ip or "0")
    v = len(fp)
    f = int(fp) if fp else 0
    nv = float(s) if s else 0.0
    return nv, i, v, f


def plural_category(locale, n):
    base = (canonical(locale) or SOURCE).split("-")[0]
    nv, i, v, f = _operands(n)
    is_int = v == 0
    if base in ("en", "de", "sw", "ur", "it", "nl"):
        return "one" if i == 1 and v == 0 else "other"
    if base in ("es", "fr", "pt"):
        if base == "es":
            if nv == 1:
                return "one"
        elif i in (0, 1):
            return "one"
        if v == 0 and i != 0 and i % 1000000 == 0:
            return "many"
        return "other"
    if base in ("hi", "bn", "fa"):
        return "one" if i == 0 or nv == 1 else "other"
    if base == "ar":
        if is_int:
            if i == 0:
                return "zero"
            if i == 1:
                return "one"
            if i == 2:
                return "two"
            if 3 <= i % 100 <= 10:
                return "few"
            if 11 <= i % 100 <= 99:
                return "many"
        return "other"
    if base == "he":
        if (i == 1 and v == 0) or (i == 0 and v != 0):
            return "one"
        if i == 2 and v == 0:
            return "two"
        return "other"
    if base == "ru":
        if v != 0:
            return "other"
        if i % 10 == 1 and i % 100 != 11:
            return "one"
        if 2 <= i % 10 <= 4 and not 12 <= i % 100 <= 14:
            return "few"
        return "many"
    return "other"


def plural_categories(locale):
    base = (canonical(locale) or SOURCE).split("-")[0]
    return PLURAL_CATEGORIES.get(base, ("other",))


# ======================================================================================================
# ICU MessageFormat subset: parse + format
# ======================================================================================================
class MessageError(ValueError):
    pass


_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def parse(msg):
    """-> list of nodes: str | {"t": "arg"|"num"|"plural"|"select"|"pound", ...}. Raises MessageError."""
    if not isinstance(msg, str):
        raise MessageError("message is not text")
    nodes, pos = _parse_seq(msg, 0, False)
    if pos != len(msg):
        raise MessageError("unexpected '}' at %d" % pos)
    return nodes


def _parse_seq(s, pos, in_plural):
    out, buf = [], []
    n = len(s)
    while pos < n:
        ch = s[pos]
        if ch == "'":
            if pos + 1 < n and s[pos + 1] == "'":
                buf.append("'")
                pos += 2
                continue
            if pos + 1 < n and s[pos + 1] in "{}#|":
                end = pos + 1
                lit = []
                while end < n:
                    if s[end] == "'":
                        if end + 1 < n and s[end + 1] == "'":
                            lit.append("'")
                            end += 2
                            continue
                        break
                    lit.append(s[end])
                    end += 1
                if end >= n:
                    raise MessageError("unterminated quote at %d" % pos)
                buf.append("".join(lit))
                pos = end + 1
                continue
            buf.append("'")
            pos += 1
            continue
        if ch == "{":
            if buf:
                out.append("".join(buf))
                buf = []
            node, pos = _parse_arg(s, pos + 1)
            out.append(node)
            continue
        if ch == "}":
            break
        if ch == "#" and in_plural:
            if buf:
                out.append("".join(buf))
                buf = []
            out.append({"t": "pound"})
            pos += 1
            continue
        buf.append(ch)
        pos += 1
    if buf:
        out.append("".join(buf))
    return out, pos


def _ws(s, pos):
    while pos < len(s) and s[pos] in " \t\r\n":
        pos += 1
    return pos


def _parse_arg(s, pos):
    pos = _ws(s, pos)
    m = _NAME.match(s, pos)
    if not m:
        raise MessageError("argument name expected at %d" % pos)
    name = m.group(0)
    pos = _ws(s, m.end())
    if pos < len(s) and s[pos] == "}":
        return {"t": "arg", "name": name}, pos + 1
    if pos >= len(s) or s[pos] != ",":
        raise MessageError("',' or '}' expected after {%s" % name)
    pos = _ws(s, pos + 1)
    m = re.compile(r"[a-z]+").match(s, pos)
    if not m:
        raise MessageError("argument type expected at %d" % pos)
    typ = m.group(0)
    pos = _ws(s, m.end())
    if typ == "number":
        if pos < len(s) and s[pos] == "}":
            return {"t": "num", "name": name}, pos + 1
        raise MessageError("number styles are not supported ({%s, number})" % name)
    if typ not in ("plural", "select"):
        raise MessageError("unsupported argument type '%s'" % typ)
    if pos >= len(s) or s[pos] != ",":
        raise MessageError("',' expected after %s" % typ)
    pos = _ws(s, pos + 1)
    offset = 0
    if typ == "plural" and s.startswith("offset:", pos):
        m = re.compile(r"offset:\s*(\d+)").match(s, pos)
        if not m:
            raise MessageError("bad offset")
        offset = int(m.group(1))
        pos = _ws(s, m.end())
    opts = {}
    while True:
        pos = _ws(s, pos)
        if pos < len(s) and s[pos] == "}":
            pos += 1
            break
        m = re.compile(r"=\d+|[A-Za-z_][A-Za-z0-9_]*").match(s, pos)
        if not m:
            raise MessageError("option selector expected at %d in {%s}" % (pos, name))
        sel = m.group(0)
        pos = _ws(s, m.end())
        if pos >= len(s) or s[pos] != "{":
            raise MessageError("'{' expected after selector '%s'" % sel)
        body, pos = _parse_seq(s, pos + 1, typ == "plural")
        if pos >= len(s) or s[pos] != "}":
            raise MessageError("unterminated option '%s' in {%s}" % (sel, name))
        pos += 1
        if sel in opts:
            raise MessageError("duplicate selector '%s' in {%s}" % (sel, name))
        opts[sel] = body
    if "other" not in opts:
        raise MessageError("{%s, %s} needs an 'other' branch" % (name, typ))
    return {"t": typ, "name": name, "offset": offset, "opts": opts}, pos


def arguments(nodes, out=None):
    """{name: kind} for every argument a parsed message uses (kind: arg / num / plural / select)."""
    out = {} if out is None else out
    for nd in nodes:
        if isinstance(nd, dict) and nd["t"] in ("arg", "num", "plural", "select"):
            prev = out.get(nd["name"])
            kind = nd["t"]
            if prev in ("plural", "num") and kind == "arg":
                kind = prev
            out[nd["name"]] = kind if prev is None or prev == "arg" or kind != "arg" else prev
            for body in nd.get("opts", {}).values():
                arguments(body, out)
    return out


def selectors(nodes, out=None):
    """[(argument name, type, set of selectors)] for every plural / select in a message."""
    out = [] if out is None else out
    for nd in nodes:
        if isinstance(nd, dict) and nd["t"] in ("plural", "select"):
            out.append((nd["name"], nd["t"], set(nd["opts"])))
            for body in nd["opts"].values():
                selectors(body, out)
    return out


# number formatting (server side: PDFs, CSV; the browser uses Intl.NumberFormat)
_SEPARATORS = {"en": (",", "."), "de": (".", ","), "es": (".", ","), "fr": ("\u202f", ","), "pt": (".", ","),
               "ru": ("\u00a0", ","), "id": (".", ","), "sw": (",", "."), "ur": (",", "."), "ar": (",", "."),
               "hi": (",", "."), "bn": (",", "."), "zh": (",", "."), "ja": (",", "."), "he": (",", "."),
               "fa": (",", ".")}
_MIN_GROUP = {"es": 2, "pt": 1}   # CLDR minimumGroupingDigits (es: 1234 is not grouped, 12 345 is)


def format_number(n, locale="en", decimals=None):
    base = (canonical(locale) or SOURCE).split("-")[0]
    group, dec = _SEPARATORS.get(base, (",", "."))
    if isinstance(n, str):
        n = float(n) if "." in n else int(n)
    neg = n < 0
    n = abs(n)
    if decimals is None:
        txt = ("%d" % n) if float(n) == int(n) and not (isinstance(n, float) and n > 2 ** 53) else repr(float(n))
        if "e" in txt:
            txt = "%.6f" % n
            txt = txt.rstrip("0").rstrip(".")
    else:
        txt = "%.*f" % (decimals, n)
    ip, _, fp = txt.partition(".")
    if base in ("hi", "bn") and len(ip) > 3:       # Indian grouping: 12,34,567
        head, tail = ip[:-3], ip[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        ip = group.join(parts + [tail])
    elif len(ip) > 3 and len(ip) >= 4 + _MIN_GROUP.get(base, 1) - 1:
        rev = ip[::-1]
        ip = group.join(rev[i:i + 3] for i in range(0, len(rev), 3))[::-1]
    return ("-" if neg else "") + ip + (dec + fp if fp else "")


def format_nodes(nodes, params, locale, pound=None, numfmt=None):
    nf = numfmt or (lambda x: format_number(x, locale))
    out = []
    for nd in nodes:
        if isinstance(nd, str):
            out.append(nd)
            continue
        t = nd["t"]
        if t == "pound":
            out.append(nf(pound) if pound is not None else "#")
            continue
        name = nd["name"]
        val = params.get(name) if params else None
        if t == "arg":
            out.append("" if val is None else str(val))
        elif t == "num":
            out.append("" if val is None else nf(val))
        elif t == "plural":
            x = 0 if val is None else val
            body = nd["opts"].get("=%s" % _exact(x))
            if body is None:
                xo = _num(x) - nd["offset"]
                body = nd["opts"].get(plural_category(locale, xo if nd["offset"] else x)) or nd["opts"]["other"]
                out.append(format_nodes(body, params, locale, xo, numfmt))
            else:
                out.append(format_nodes(body, params, locale, _num(x) - nd["offset"], numfmt))
        elif t == "select":
            body = nd["opts"].get(str(val)) or nd["opts"]["other"]
            out.append(format_nodes(body, params, locale, pound, numfmt))
    return "".join(out)


def _num(x):
    if isinstance(x, (int, float)):
        return x
    try:
        return int(x)
    except (TypeError, ValueError):
        try:
            return float(x)
        except (TypeError, ValueError):
            return 0


def _exact(x):
    v = _num(x)
    return str(int(v)) if float(v) == int(v) else str(v)


def format_message(msg, params=None, locale="en", numfmt=None):
    return format_nodes(parse(msg), params or {}, locale, numfmt=numfmt)


# ======================================================================================================
# Catalogs
# ======================================================================================================
_lock = threading.RLock()
_cache = {}


def sha(text):
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:16]


def catalog_path(locale, root=None):
    return os.path.join(root or I18N_DIR, "%s.json" % locale)


def load_raw(locale, root=None):
    """The catalog file as stored ({} when missing). Cached by mtime."""
    p = catalog_path(locale, root)
    try:
        st = os.stat(p)
    except OSError:
        return {}
    key = (p, st.st_mtime_ns, st.st_size)
    with _lock:
        hit = _cache.get(p)
        if hit and hit[0] == key:
            return hit[1]
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("messages"), dict):
            raise ValueError("%s: not a catalog" % p)
        _cache[p] = (key, data)
        return data


def source(root=None):
    return load_raw(SOURCE, root)


def available(root=None):
    return [c for c in SHIPPED if os.path.exists(catalog_path(c, root))]


def key_reviewed(locale, key, cat, src_msgs):
    """True only with a reviewer record that matches the current English source AND the current translation."""
    if locale == SOURCE:
        return True
    rec = (cat.get("review") or {}).get(key)
    if not isinstance(rec, dict) or key not in src_msgs or key not in cat.get("messages", {}):
        return False
    return bool(rec.get("status") == REVIEWED and _valid_reviewer(rec.get("reviewer")) is None
                and _valid_date(rec.get("date")) is None and rec.get("source_sha") == sha(src_msgs[key])
                and rec.get("text_sha") == sha(cat["messages"][key]))


def status(locale, root=None):
    """Computed: source | reviewed | partially_reviewed | needs_native_review | missing (+ counts)."""
    locale = canonical(locale) or locale
    src = source(root).get("messages", {})
    if locale == SOURCE:
        return {"locale": locale, "status": SOURCE_STATUS, "keys": len(src), "reviewed": len(src), "missing": 0}
    cat = load_raw(locale, root)
    if not cat:
        return {"locale": locale, "status": "missing", "keys": len(src), "reviewed": 0, "missing": len(src)}
    msgs = cat.get("messages", {})
    reviewed = sum(1 for k in src if key_reviewed(locale, k, cat, src))
    missing = sum(1 for k in src if k not in msgs)
    st = REVIEWED if reviewed == len(src) and src else (PARTIAL if reviewed else DRAFT)
    return {"locale": locale, "status": st, "keys": len(src), "reviewed": reviewed, "missing": missing}


def messages(locale, root=None):
    """-> (resolved {key: text} with fallback, {key: locale actually used})."""
    src = source(root).get("messages", {})
    chain = fallback_chain(locale)
    cats = [(c, load_raw(c, root).get("messages", {})) for c in chain]
    out, used = {}, {}
    for k, en in src.items():
        for c, m in cats:
            v = m.get(k)
            if isinstance(v, str) and v:
                out[k], used[k] = v, c
                break
        else:
            out[k], used[k] = en, SOURCE
    return out, used


def t(key, locale="en", params=None, root=None):
    """Server-side lookup + format (PDF headings, CSV, CLI). Unknown key -> KeyError."""
    msgs, used = messages(locale, root)
    if key not in msgs:
        raise KeyError(key)
    return format_message(msgs[key], params, used[key])


SCOPES = {
    "app": ("app.", "legal.", "err."),                       # the main clinic UI
    "comms": ("comms.",),                                     # the emergency page (English is built into it)
    "rd": ("rd.", "legal.", "err.", "app.lang.draft_banner"),  # the Research Desk
    "pm": ("pm.",),                                           # practice screens, fetched after sign-in
    # screens that only exist after sign-in (licence, consent, coverage): fetched right after sign-in, never part of
    # the first load, which stays small on 2G
    "later": ("app.lic.", "app.consent.", "app.cov."),
    "all": ("",),
}


def bundle(locale, root=None, region=None, scope="app"):
    """Everything the browser runtime needs for one locale and one page (inlined into the page; also served as
    GET /i18n/<loc>.json?scope=app|comms|rd). Only the keys that page uses are sent (first-load size)."""
    loc = canonical(locale) or SOURCE
    if loc not in LOCALES:
        loc = SOURCE
    prefixes = SCOPES.get(scope or "app", SCOPES["app"])
    skip = SCOPES["later"] if prefixes == SCOPES["app"] else ()
    src_cat = source(root)
    src = {k: v for k, v in src_cat.get("messages", {}).items() if k.startswith(prefixes) and not (skip and k.startswith(skip))}
    meta = src_cat.get("_meta", {})
    msgs, used = messages(loc, root)
    msgs = {k: v for k, v in msgs.items() if k in src}
    used = {k: v for k, v in used.items() if k in src}
    cat = load_raw(loc, root) if loc != SOURCE else src_cat
    reviewed = [k for k in src if key_reviewed(loc, k, cat, src)] if loc != SOURCE else None
    rev = set(reviewed or ())
    special = set(meta.get("clinical", ())) | set(meta.get("paired", ()))
    en = {k: src[k] for k in special if k in src and loc != SOURCE and k not in rev and used.get(k) != SOURCE}
    st = status(loc, root)
    return {"locale": loc, "dir": LOCALES[loc]["dir"], "intl": LOCALES[loc]["intl"],
            "name": LOCALES[loc]["native"], "status": st["status"], "m": msgs,
            "fallback": sorted(k for k, c in used.items() if c == SOURCE and loc != SOURCE),
            # only the clinical and safety ("paired") keys change how a string is shown when unreviewed, so only
            # they are listed (keeps the first load small); every machine-drafted string stays unreviewed
            "unreviewed": sorted(k for k in special if k in src and loc != SOURCE and k not in rev),
            "reviewed": len(rev), "total": len(src),
            "clinical": sorted(k for k in meta.get("clinical", ()) if k in src),
            "paired": sorted(k for k in meta.get("paired", ()) if k in src), "en": en, "scope": scope,
            # English server messages -> keys, so errors show translated; not needed when the page is English
            "errs": {k: v for k, v in src.items() if k.startswith("err.")} if loc != SOURCE else {},
            "plural": list(plural_categories(loc)), "locales": locale_list(root), "region": region}


def locale_list(root=None):
    out = []
    for c in available(root):
        st = status(c, root)["status"]
        out.append({"code": c, "native": LOCALES[c]["native"], "name": LOCALES[c]["name"], "dir": LOCALES[c]["dir"],
                    "status": st})
    return out


def html_attrs(locale):
    loc = canonical(locale) or SOURCE
    info = LOCALES.get(loc, LOCALES[SOURCE])
    return 'lang="%s" dir="%s"' % (info["intl"], info["dir"])


def script_json(obj):
    """JSON safe to put inside <script type="application/json">: every "<" becomes \\u003c, which is still
    valid JSON, so the HTML parser can never see </script> or <!-- (and no U+2028/9 surprises)."""
    return (json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


# ======================================================================================================
# Validation (every catalog: all keys, same placeholders, valid plural branches, really translated)
# ======================================================================================================
def validate(locale, root=None, src_msgs=None):
    """-> list of problems (empty = OK)."""
    loc = canonical(locale) or locale
    src = src_msgs if src_msgs is not None else source(root).get("messages", {})
    probs = []
    if loc == SOURCE:
        cat = source(root)
        for k, v in src.items():
            if not re.match(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$", k):
                probs.append("%s: bad key name" % k)
            probs += ["%s: %s" % (k, p) for p in check_message(v, None, SOURCE)]
        meta = cat.get("_meta", {})
        for lst in ("clinical", "paired"):
            for k in meta.get(lst, ()):
                if k not in src:
                    probs.append("_meta.%s lists unknown key %s" % (lst, k))
        return probs
    cat = load_raw(loc, root)
    if not cat:
        return ["%s: catalog missing" % loc]
    msgs = cat.get("messages", {})
    meta = cat.get("_meta", {})
    if meta.get("machine_draft") is not True and status(loc, root)["status"] != REVIEWED:
        probs.append("%s: _meta.machine_draft must be true until every string is reviewed" % loc)
    for k in src:
        if k not in msgs:
            probs.append("%s: missing key %s" % (loc, k))
    for k in msgs:
        if k not in src:
            probs.append("%s: extra key %s (not in en.json)" % (loc, k))
    for k, v in msgs.items():
        if k in src:
            probs += ["%s: %s: %s" % (loc, k, p) for p in check_message(v, src[k], loc)]
    for k, rec in (cat.get("review") or {}).items():
        if k not in src:
            probs.append("%s: review record for unknown key %s" % (loc, k))
        elif isinstance(rec, dict) and rec.get("status") == REVIEWED:
            for why in (_valid_reviewer(rec.get("reviewer")), _valid_date(rec.get("date"))):
                if why:
                    probs.append("%s: review of %s: %s" % (loc, k, why))
    probs += _translated_check(loc, msgs, src)
    return probs


def check_message(text, english, locale):
    """Problems with one message (english=None: checking the source itself)."""
    out = []
    if not isinstance(text, str) or not text.strip():
        return ["empty"]
    if re.search(r"<\s*/?\s*[a-zA-Z!]", text):
        out.append("contains markup (UI text is set with textContent: write plain text)")
    if re.search(r"[A-Za-z]:\\\\|[A-Za-z]:\\[A-Za-z]|/home/|/Users/|\\Users\\", text):
        out.append("contains a local path")
    if re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text):
        out.append("contains an email address")
    try:
        nodes = parse(text)
    except MessageError as e:
        return out + ["syntax: %s" % e]
    cats = set(plural_categories(locale)) | {"other"}
    for name, typ, sels in selectors(nodes):
        if typ == "plural":
            bad = [s for s in sels if not s.startswith("=") and s not in cats]
            if bad:
                out.append("plural {%s} has categories %s that %s does not use (valid: %s)"
                           % (name, sorted(bad), locale, sorted(cats)))
    if english is not None:
        try:
            en_nodes = parse(english)
        except MessageError:
            return out
        a, b = arguments(nodes), arguments(en_nodes)
        if set(a) != set(b):
            out.append("placeholders %s differ from English %s" % (sorted(a), sorted(b)))
        else:
            for n in a:
                if (a[n] == "plural") != (b[n] == "plural") or (a[n] == "select") != (b[n] == "select"):
                    out.append("{%s} is %s here but %s in English" % (n, a[n], b[n]))
        en_sel = {(n, t): s for n, t, s in selectors(en_nodes) if t == "select"}
        for n, t_, s in selectors(nodes):
            if t_ == "select" and (n, t_) in en_sel and s != en_sel[(n, t_)]:
                out.append("select {%s} options %s differ from English %s" % (n, sorted(s), sorted(en_sel[(n, t_)])))
    return out


# Strings that may legitimately stay identical to English in a draft (brand, codes, units, technical words).
_SAME_OK = re.compile(r"^(Stark Covenant|[A-Z0-9 /%#().:+\-]+|[A-Za-z]{1,4}[0-9]*|SpO2.*|PIN|Email|E-?mail|"
                      r"OK|PDF|JSON|CSV|HL7.*|FHIR.*|STUN.*|TURN.*|USB.*|ID|MRN.*)$")


def _plain_letters(text):
    try:
        nodes = parse(text)
    except MessageError:
        return text
    acc = []

    def walk(ns):
        for nd in ns:
            if isinstance(nd, str):
                acc.append(nd)
            elif nd.get("opts"):
                for body in nd["opts"].values():
                    walk(body)
    walk(nodes)
    return "".join(acc)


def _translated_check(loc, msgs, src):
    """A draft must really be a translation: target script present / not mostly identical to English."""
    probs = []
    rng = LOCALE_SCRIPT.get(loc)
    letters = [(k, _plain_letters(v)) for k, v in msgs.items() if k in src and re.search(r"[A-Za-z]{3}", src[k])]
    if not letters:
        return probs
    if rng:
        rx = re.compile("[%s]" % rng)
        miss = [k for k, v in letters if not rx.search(v) and not _SAME_OK.match(_plain_letters(src[k]).strip())]
        if len(miss) > max(3, len(letters) // 25):
            probs.append("%s: %d strings do not use the %s script (e.g. %s)" % (loc, len(miss), loc, miss[:5]))
    else:
        same = [k for k, v in letters if v.strip() == _plain_letters(src[k]).strip()
                and not _SAME_OK.match(v.strip())]
        if len(same) > max(5, len(letters) // 12):
            probs.append("%s: %d strings are identical to English (e.g. %s)" % (loc, len(same), same[:5]))
    return probs


# ======================================================================================================
# Template scanner: no user-facing English outside the catalogs
# ======================================================================================================
_KEYLIKE = re.compile(r"^(?:[a-z][a-z0-9_]*\.)+[a-z0-9_]*$")
_TWO_WORDS = re.compile(r"[A-Za-z][a-z]+[  ]+[a-z]{2,}")
_CAP_WORD = re.compile(r"^[A-Z][a-z]{2,}[.!?:]?$")
# Literal text that is not UI language: brand names, file names, protocol words, data values sent to the server.
SCAN_ALLOW = {
    "Stark Covenant",                       # product name (never translated)
    "withdrawn in app", "withdraw all in app",   # consent withdrawal reasons stored in the audit record (data)
    "Recorded in person by",                 # nutrition consent statement stored with the record (data)
    "same-origin", "use strict",             # fetch credentials mode, JS directive
    "noopener noreferrer",                   # link rel tokens
    "English",                               # the endonym of English in a language list (endonyms stay native)
    "Enter",                                 # KeyboardEvent.key value
}


def looks_english(s, css_classes=()):
    t = s.strip()
    if "<" in t and ">" in t:
        t = re.sub(r"<[^>]*>", " ", t).strip()          # HTML fragment: only its text counts
    if not t or t in SCAN_ALLOW or _KEYLIKE.match(t):
        return False
    words = t.split()
    if css_classes and all(w in css_classes for w in words):
        return False                                     # a class list such as 'card ai'
    if t.endswith('="') or t.startswith('"'):
        return False
    if re.match(r"^[\w.+-]+/[\w.+-]+$", t) or re.match(r"^[A-Z][A-Za-z]*(-[A-Z][A-Za-z]*)+$", t):
        return False                                     # MIME types, header names
    if re.search(r"[;{}]|^[a-z-]+:[^ ]", t) or t.startswith(("#", ".", "[", "/", "http", "tel:", "sms:")):
        return False                                     # CSS, selectors, paths, URLs
    return bool(_TWO_WORDS.search(t) or _CAP_WORD.match(t))


def _js_literals(js):
    """String literals of a JS source (single, double, template) with their offsets; comments and regexes skipped."""
    out, i, n = [], 0, len(js)
    prev = ""
    while i < n:
        c = js[i]
        if c == "/" and js.startswith("//", i):
            j = js.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and js.startswith("/*", i):
            j = js.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c == "/" and prev in "(,=:[!&|?{};+" + "\n":
            j = i + 1
            cls = False
            while j < n:
                if js[j] == "\\":
                    j += 2
                    continue
                if js[j] == "[":
                    cls = True
                elif js[j] == "]":
                    cls = False
                elif js[j] == "/" and not cls:
                    break
                elif js[j] == "\n":
                    break
                j += 1
            i = j + 1
            prev = "x"
            continue
        if c in "'\"`":
            j = i + 1
            buf = []
            while j < n and js[j] != c:
                if js[j] == "\\" and j + 1 < n:
                    buf.append(js[j + 1])
                    j += 2
                    continue
                buf.append(js[j])
                j += 1
            out.append((i, "".join(buf)))
            i = j + 1
            prev = "x"
            continue
        if not c.isspace():
            prev = c
        i += 1
    return out


def scan_template(html_text, hybrid_defaults=None):
    """-> list of (where, text) for user-facing English found outside the catalog mechanism.

    strict (hybrid_defaults=None): every UI string must come from T()/legal()/TK(); markup holds no text.
    hybrid (comms page): English may appear only as (a) the text of an element with data-t="k" or an attribute
    named in data-ta, or (b) the default argument of tr('k', 'English'), and in both cases it must be exactly
    the catalog's English for comms.k (hybrid_defaults is that {k: English} map)."""
    import html.parser
    found = []
    css = " ".join(re.findall(r"(?s)<style>(.*?)</style>", html_text))
    classes = set(re.findall(r"\.([A-Za-z_][\w-]*)", css)) | {"btn", "half", "big", "row", "card", "go", "sec", "eobline", "diet", "flags"}
    text = re.sub(r"(?s)<style>.*?</style>", "", html_text)
    scripts = []

    class P(html.parser.HTMLParser):
        VOID = ("input", "meta", "link", "br", "img", "hr", "source", "track", "wbr")

        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.stack = []
            self.in_script = False

        def handle_starttag(self, tag, attrs):
            d = dict(attrs)
            if tag == "script":
                self.in_script = True
                return
            ta = {}
            if d.get("data-ta"):
                for pair in d["data-ta"].split(";"):
                    a, _, k = pair.partition(":")
                    ta[a] = k
            for a in ("aria-label", "placeholder", "title", "alt", "value", "label"):
                v = d.get(a)
                if v and looks_english(v, classes):
                    if hybrid_defaults is not None and a in ta and hybrid_defaults.get(ta[a]) == v:
                        continue
                    found.append(("attribute %s" % a, v))
            if tag not in self.VOID:
                self.stack.append(d.get("data-t"))

        def handle_endtag(self, tag):
            if tag == "script":
                self.in_script = False
                return
            if tag not in self.VOID and self.stack:
                self.stack.pop()

        def handle_data(self, data):
            if self.in_script:
                scripts.append(data)
                return
            t = data.strip()
            if not t or not re.search(r"[A-Za-z]{2}", t) or t in SCAN_ALLOW:
                return
            keyed = [k for k in self.stack if k]
            if hybrid_defaults is not None and keyed and keyed[-1] in hybrid_defaults and \
                    t in " ".join(hybrid_defaults[keyed[-1]].split()):
                return
            if t.startswith("__") and t.endswith("__"):
                return
            if hybrid_defaults is not None and t == hybrid_defaults.get("safety"):
                return                                   # the safety sentence is always shown in English too
            found.append(("text", t))

    P().feed(text)
    for js in scripts:
        if js.lstrip().startswith("{") or "__I18N_DATA__" in js:
            continue                                     # the inlined catalog itself
        lits = _js_literals(js)
        for off, lit in lits:
            if not looks_english(lit, classes):
                continue
            if hybrid_defaults is not None:
                head = js[max(0, off - 80):off]
                m = re.search(r"\btr\(\s*'([a-z0-9_]+)'\s*,\s*$", head)
                if m and hybrid_defaults.get(m.group(1)) == lit:
                    continue
                if re.search(r"const SAFETY = $", head) and hybrid_defaults.get("safety") == lit:
                    continue
            found.append(("script", lit))
    return found


# ======================================================================================================
# Review workflow (CSV out, reviewed CSV in)
# ======================================================================================================
REVIEW_COLUMNS = ("key", "english", "translation", "status", "reviewer", "review_date", "qualification", "comment")


def _valid_reviewer(name):
    if not isinstance(name, str) or not name.strip():
        return "reviewer name required"
    n = name.strip()
    if len(n) < 2 or len(n) > 80:
        return "reviewer name must be 2-80 characters"
    if "@" in n or "/" in n or "\\" in n or re.search(r"https?:", n):
        return "reviewer must be a person's name (no email address, path or link)"
    if re.fullmatch(r"(?i)(ai|machine|mt|auto(matic)?|bot|gpt|claude|llm|google translate|deepl)( .*)?", n):
        return "a reviewer is a person, not a machine translation"
    return None


def _valid_date(d):
    if not isinstance(d, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d.strip()):
        return "review date must be YYYY-MM-DD"
    try:
        day = _dt.date.fromisoformat(d.strip())
    except ValueError:
        return "review date is not a real date"
    if day > _dt.date.today() + _dt.timedelta(days=1):
        return "review date is in the future"
    if day.year < 2020:
        return "review date is implausibly old"
    return None


def export_review_csv(locale, out=None, root=None):
    """Write (or return) the review sheet for one locale."""
    loc = canonical(locale)
    if not loc or loc == SOURCE:
        raise ValueError("choose a translated locale")
    src = source(root).get("messages", {})
    cat = load_raw(loc, root)
    msgs = cat.get("messages", {})
    rev = cat.get("review") or {}
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(REVIEW_COLUMNS)
    for k, en in src.items():
        r = rev.get(k) if key_reviewed(loc, k, cat, src) else None
        w.writerow([k, en, msgs.get(k, ""), REVIEWED if r else DRAFT, (r or {}).get("reviewer", ""),
                    (r or {}).get("date", ""), (r or {}).get("qualification", ""), ""])
    text = "\ufeff" + buf.getvalue()
    if out:
        with open(out, "w", encoding="utf-8", newline="") as f:
            f.write(text)
    return text


def import_review_csv(locale, path_or_text, root=None, write=True):
    """Apply a reviewed sheet. -> report {"reviewed", "updated_drafts", "rejected": [(key, why)], "status"}."""
    loc = canonical(locale)
    if not loc or loc == SOURCE:
        raise ValueError("choose a translated locale")
    if isinstance(path_or_text, str) and os.path.exists(path_or_text):
        with open(path_or_text, encoding="utf-8-sig", newline="") as f:
            text = f.read()
    else:
        text = str(path_or_text).lstrip("\ufeff")
    rows = list(csv.DictReader(io.StringIO(text)))
    src = source(root).get("messages", {})
    with _lock:
        cat = json.loads(json.dumps(load_raw(loc, root)))
        if not cat:
            raise ValueError("no catalog for %s" % loc)
        msgs = cat.setdefault("messages", {})
        rev = cat.setdefault("review", {})
        rep = {"reviewed": 0, "updated_drafts": 0, "rejected": [], "unchanged": 0}
        for row in rows:
            k = (row.get("key") or "").strip()
            if k not in src:
                rep["rejected"].append((k, "unknown key"))
                continue
            tr = row.get("translation")
            tr = tr if tr is not None else msgs.get(k, "")
            probs = check_message(tr, src[k], loc)
            if probs:
                rep["rejected"].append((k, "; ".join(probs)))
                continue
            reviewer = (row.get("reviewer") or "").strip()
            date = (row.get("review_date") or "").strip()
            if reviewer or date:
                why = _valid_reviewer(reviewer) or _valid_date(date)
                if why:
                    rep["rejected"].append((k, why))
                    continue
                msgs[k] = tr
                rev[k] = {"status": REVIEWED, "reviewer": reviewer, "date": date,
                          "qualification": (row.get("qualification") or "").strip()[:120],
                          "source_sha": sha(src[k]), "text_sha": sha(tr)}
                rep["reviewed"] += 1
            elif tr != msgs.get(k):
                msgs[k] = tr
                rev.pop(k, None)
                rep["updated_drafts"] += 1
            else:
                rep["unchanged"] += 1
        meta = cat.setdefault("_meta", {})
        meta.pop("status", None)
        if write:
            p = catalog_path(loc, root)
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                json.dump(cat, f, ensure_ascii=False, indent=1, sort_keys=False)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, p)
            _cache.pop(p, None)
            done = status(loc, root)
            meta["machine_draft"] = done["status"] != REVIEWED
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                json.dump(cat, f, ensure_ascii=False, indent=1)
                f.write("\n")
            os.replace(tmp, p)
            _cache.pop(p, None)
        rep["status"] = status(loc, root)["status"] if write else None
        return rep


# ======================================================================================================
# Browser runtime (inlined into every page that uses the catalogs; mirrors parse/format/plural_category)
# ======================================================================================================
JS_RUNTIME = r"""
let I18N=JSON.parse(document.getElementById('i18n-data').textContent);
const I18NX=(function(){
function pc(loc,n){const base=String(loc).split('-')[0];let s=String(n).replace(/^-/,'');if(/e/i.test(s))s=Number(s).toFixed(6).replace(/\.?0+$/,'');
 const p=s.split('.'),i=parseInt(p[0]||'0',10),v=p[1]?p[1].length:0,nv=Number(s);
 if(['en','de','sw','ur','it','nl'].includes(base))return i===1&&v===0?'one':'other';
 if(['es','fr','pt'].includes(base)){if(base==='es'){if(nv===1)return 'one'}else if(i===0||i===1)return 'one';
  if(v===0&&i!==0&&i%1000000===0)return 'many';return 'other'}
 if(['hi','bn','fa'].includes(base))return i===0||nv===1?'one':'other';
 if(base==='ar'){if(v===0){if(i===0)return 'zero';if(i===1)return 'one';if(i===2)return 'two';const m=i%100;if(m>=3&&m<=10)return 'few';if(m>=11&&m<=99)return 'many'}return 'other'}
 if(base==='he'){if((i===1&&v===0)||(i===0&&v!==0))return 'one';if(i===2&&v===0)return 'two';return 'other'}
 if(base==='ru'){if(v!==0)return 'other';const a=i%10,b=i%100;if(a===1&&b!==11)return 'one';if(a>=2&&a<=4&&!(b>=12&&b<=14))return 'few';return 'many'}
 return 'other'}
function parse(s){let pos=0;const n=s.length;
 function seq(inPl){const out=[];let buf='';
  while(pos<n){const ch=s[pos];
   if(ch==="'"){if(s[pos+1]==="'"){buf+="'";pos+=2;continue}
    if(pos+1<n&&'{}#|'.includes(s[pos+1])){let e=pos+1,lit='';while(e<n){if(s[e]==="'"){if(s[e+1]==="'"){lit+="'";e+=2;continue}break}lit+=s[e];e++}
     if(e>=n)throw new Error('unterminated quote');buf+=lit;pos=e+1;continue}
    buf+="'";pos++;continue}
   if(ch==='{'){if(buf){out.push(buf);buf=''}pos++;out.push(arg());continue}
   if(ch==='}')break;
   if(ch==='#'&&inPl){if(buf){out.push(buf);buf=''}out.push({t:'pound'});pos++;continue}
   buf+=ch;pos++}
  if(buf)out.push(buf);return out}
 function ws(){while(pos<n&&' \t\r\n'.includes(s[pos]))pos++}
 function word(re){re.lastIndex=pos;const m=re.exec(s);if(!m||m.index!==pos)return null;pos+=m[0].length;return m[0]}
 function arg(){ws();const name=word(/[A-Za-z_][A-Za-z0-9_]*/y);if(!name)throw new Error('name');ws();
  if(s[pos]==='}'){pos++;return {t:'arg',name}}if(s[pos]!==',')throw new Error(',');pos++;ws();
  const typ=word(/[a-z]+/y);ws();
  if(typ==='number'){if(s[pos]!=='}')throw new Error('number');pos++;return {t:'num',name}}
  if(typ!=='plural'&&typ!=='select')throw new Error('type');if(s[pos]!==',')throw new Error(',');pos++;ws();
  let offset=0;if(typ==='plural'&&s.startsWith('offset:',pos)){pos+=7;ws();offset=parseInt(word(/\d+/y),10);ws()}
  const opts={};
  for(;;){ws();if(s[pos]==='}'){pos++;break}const sel=word(/=\d+|[A-Za-z_][A-Za-z0-9_]*/y);if(!sel)throw new Error('sel');ws();
   if(s[pos]!=='{')throw new Error('{');pos++;const body=seq(typ==='plural');if(s[pos]!=='}')throw new Error('}');pos++;opts[sel]=body}
  if(!opts.other)throw new Error('other');return {t:typ,name,offset,opts}}
 const r=seq(false);if(pos!==n)throw new Error('extra }');return r}
const cache={};
function num(x){return typeof x==='number'?x:(x==null||x===''?0:Number(x))}
function exact(x){const v=num(x);return Number.isInteger(v)?String(v):String(v)}
function fnodes(ns,p,loc,pound,nf){let o='';
 for(const nd of ns){if(typeof nd==='string'){o+=nd;continue}
  if(nd.t==='pound'){o+=pound==null?'#':nf(pound);continue}
  const v=p?p[nd.name]:undefined;
  if(nd.t==='arg')o+=v==null?'':String(v);
  else if(nd.t==='num')o+=v==null?'':nf(v);
  else if(nd.t==='plural'){const x=v==null?0:v;let b=nd.opts['='+exact(x)];const xo=num(x)-nd.offset;
   if(!b)b=nd.opts[pc(loc,nd.offset?xo:x)]||nd.opts.other;o+=fnodes(b,p,loc,xo,nf)}
  else if(nd.t==='select'){const b=nd.opts[String(v)]||nd.opts.other;o+=fnodes(b,p,loc,pound,nf)}}
 return o}
function format(msg,p,loc,nf){let ns=cache[msg];if(!ns){try{ns=cache[msg]=parse(msg)}catch(e){return msg}}return fnodes(ns,p||{},loc,null,nf||(x=>String(x)))}
return {pc,parse,format}})();
function nfmt(x,o){const r=I18N.region||{};try{return new Intl.NumberFormat(I18N.intl+(r.digits==='native'?'':'-u-nu-latn'),o||{}).format(Number(x))}catch(e){return String(x)}}
let _clin,_pair,_unrev,_errs;
function i18nUse(b){I18N=b;_clin=new Set(b.clinical);_pair=new Set(b.paired);_unrev=new Set(b.unreviewed);_errs=null;
 const h=document.documentElement;h.lang=b.intl;h.dir=b.dir}
i18nUse(I18N);
function TK(prefix,v){const k=prefix+'.'+v;return I18N.m[k]!=null?T(k):String(v==null?'':v).replaceAll('_',' ')}
function trErr(msg){msg=String(msg||'');if(!_errs){_errs=[];for(const[k,en]of Object.entries(I18N.errs||{})){const names=[];
  const rx=en.replace(/[.*+?^$()|[\]\\]/g,'\\$&').replace(/\\?\{([A-Za-z_][A-Za-z0-9_]*)\\?\}/g,(m,n)=>{names.push(n);return '(.+?)'});
  _errs.push([new RegExp('^'+rx+'$'),k,names])}}
 for(const[rx,k,names]of _errs){const m=rx.exec(msg);if(m){const p={};names.forEach((n,i)=>p[n]=m[i+1]);return T(k,p)}}
 return msg}
function T(k,p){const m=I18N.m[k];if(m==null){console.warn('i18n: missing key '+k);return k}
 let s=I18NX.format(m,p,I18N.locale,nfmt);
 if(_clin.has(k)&&_unrev.has(k)&&I18N.en[k]!=null){const e=I18NX.format(I18N.en[k],p,'en',nfmt);if(e!==s)s=s+' ('+e+')'}
 return s}
function TE(k,p){return I18N.en[k]!=null?I18NX.format(I18N.en[k],p,'en',nfmt):null}
function legal(tag,k,p,attrs){const box=document.createElement(tag||'div');if(attrs)for(const[a,v]of Object.entries(attrs))box.setAttribute(a,v);
 const main=document.createElement('span');main.textContent=T(k,p);box.append(main);
 if(_pair.has(k)&&_unrev.has(k)&&I18N.en[k]!=null){const e=document.createElement('span');e.lang='en';e.dir='ltr';e.className='en-orig';
  e.textContent=TE(k,p);box.append(document.createElement('br'),e)}
 return box}
"""


def js_runtime():
    return JS_RUNTIME


# ======================================================================================================
# CLI
# ======================================================================================================
def _main(argv):
    import argparse
    ap = argparse.ArgumentParser(description="Stark Covenant i18n catalogs")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("check")
    e = sub.add_parser("export-review")
    e.add_argument("locale")
    e.add_argument("out")
    i = sub.add_parser("import-review")
    i.add_argument("locale")
    i.add_argument("csv")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if a.cmd == "status":
        for c in available():
            s = status(c)
            print("%-8s %-22s %4d/%d reviewed  %s" % (c, s["status"], s["reviewed"], s["keys"], LOCALES[c]["native"]))
        return 0
    if a.cmd == "check":
        bad = 0
        for c in available():
            p = validate(c)
            print("%-8s %s" % (c, "ok" if not p else "%d problem(s)" % len(p)))
            for x in p[:40]:
                print("   ", x)
            bad += bool(p)
        return 1 if bad else 0
    if a.cmd == "export-review":
        export_review_csv(a.locale, a.out)
        print("wrote %s" % a.out)
        return 0
    if a.cmd == "import-review":
        rep = import_review_csv(a.locale, a.csv)
        print("reviewed %d, drafts updated %d, unchanged %d, rejected %d -> status %s"
              % (rep["reviewed"], rep["updated_drafts"], rep["unchanged"], len(rep["rejected"]), rep["status"]))
        for k, why in rep["rejected"][:50]:
            print("  rejected %s: %s" % (k, why))
        return 1 if rep["rejected"] else 0
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
