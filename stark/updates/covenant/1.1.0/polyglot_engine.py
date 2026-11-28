"""Stark Covenant - polyglot_engine: store, display and print patient text in 100+ languages / scripts.

Standard library only, Python 3.10+.

What it does
  * store_text(): NFC-normalises patient text and records its scripts, writing direction and warnings;
    search_key() gives a casefolded NFKC key for cross-script search.
  * Script detection (detect_scripts / script_runs) from a Unicode block table (approximation of the
    Unicode Script property; Common / Inherited characters take the script of their neighbours).
  * Unicode Bidirectional Algorithm (UAX #9) over unicodedata.bidirectional(): P2-P3, X1-X10 (explicit
    embeddings, overrides and isolates), W1-W7, N0 (bracket pairs, common bracket table), N1-N2, I1-I2,
    L1, L2 and mirroring (L4, common mirror table). bidi_levels(), visual_indices(), get_display().
  * Arabic contextual shaping to presentation forms (isolated / initial / medial / final) incl. the four
    lam-alef ligatures. The form table is derived at import time from the Unicode decomposition data of the
    Arabic Presentation Forms blocks, so it covers Arabic, Persian, Urdu (Naskh forms), Pashto letters that
    have presentation forms, Uyghur and Sindhi letters in FB50-FDFF / FE70-FEFF.
  * display_html(): the right way to show patient text on SCREEN is to let the browser shape and reorder it:
    <bdi dir="auto" lang=..>, escaped. The engine above is used for PDF, where nothing else shapes text.
  * PolyglotPdf: hand-written PDF 1.4 with CID fonts (Type0 / CIDFontType2, Identity-H) embedded per script
    from installed system fonts (Segoe UI, Arial, Tahoma, Nirmala UI, Leelawadee UI, Microsoft YaHei,
    SimSun, Yu Gothic, Malgun Gothic, Ebrima, Gadugi, Myanmar Text, Microsoft Himalaya, MV Boli, ...),
    ToUnicode CMaps (text extractable; shaped Arabic maps back to nominal letters, lam-alef to two letters),
    /ActualText spans carrying the LOGICAL text of every line that was reordered or shaped, UTF-16BE text
    strings for /Info (Producer "Stark Covenant") and bookmarks. Substitution / missing-glyph / unshaped-
    script warnings are collected in .warnings - never silent.
  * i18n: UI string table. English is the source. Other languages appear only when a human reviewer has
    registered them as verified; AI/machine-authored strings are marked "unverified", are OFF by default and
    are NEVER used for clinical labels.

  * OpenType shaping (PolyglotPdf(shaping=True), the default): every run is shaped by ot_shaper with the
    font's own GSUB / GPOS tables (Indic model for Devanagari ... Malayalam and Sinhala, Khmer, Myanmar,
    a USE subset for Tibetan, Arabic joining via GSUB with GPOS harakat, generic kern / liga / mark for the
    rest) and written as glyph ids (Identity-H) with positions; clusters whose glyphs do not map 1:1 to
    their characters (conjuncts, reordered vowel signs, reph) get their own /ActualText so copy / search
    return the stored text. Checked against Windows' own shaping (Uniscribe), see tests/test_ot_shaper.py.

Honest limits
  * With shaping=False (and language_sample_pdf's default, kept for the original specimen) Indic scripts,
    Sinhala, Khmer, Myanmar, Tibetan, Mongolian, Syriac and N'Ko are rendered UNSHAPED: no conjuncts, no
    pre-base vowel reordering, no mark positioning; the PDF warns for every such script. With shaping on, a
    script is still reported UNSHAPED when its font has no OpenType tables for it. Known shaping limits
    (see ot_shaper): no USE cluster grammar, no Syriac fin2/fin3/med2, no vertical Mongolian, no Nastaliq
    (Urdu prints in Naskh style with the Arial / Segoe UI tables).
  * The unshaped Arabic path uses presentation-form code points: no optional ligatures beyond lam-alef,
    harakat are not positioned. Letters without presentation forms are left in nominal form and reported.
  * Mongolian is printed horizontally (no vertical layout). Han characters use the font chosen by the
    paragraph's language tag (ja / ko / zh-Hant / zh-Hans); without a tag Simplified Chinese glyph forms
    are used.
  * Line breaking is greedy on spaces, plus between CJK characters; scripts without spaces (Thai, Lao,
    Khmer, Myanmar) break only when a run is wider than the line (no dictionary word breaking).
  * Bidi: Bidi_Paired_Bracket / Bidi_Mirroring_Glyph are not exposed by unicodedata, so N0 and L4 use
    built-in tables of the common brackets / mirrored pairs. Unassigned code points default to L (R/AL in
    the RTL blocks). L3 is approximated by keeping combining marks after their base when a run is reversed.
  * Fonts whose OS/2 fsType forbids embedding (Restricted License) are skipped. CFF-outline (.otf) fonts
    are not supported by the embedder and are skipped with a warning.
"""
from __future__ import annotations

import bisect
import datetime
import hashlib
import html
import os
import re
import struct
import sys
import unicodedata
import zlib

def _load_ot_shaper():
    """OpenType shaper from this module's folder (works under python -I too); None if unavailable."""
    try:
        import ot_shaper
        return ot_shaper
    except ImportError:
        pass
    try:
        import importlib.util
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ot_shaper.py")
        spec = importlib.util.spec_from_file_location("ot_shaper", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules.setdefault("ot_shaper", mod)
        return mod
    except Exception:
        return None


_ots = _load_ot_shaper()  # the PDF writer falls back to the unshaped path (and says so) without it

APP_NAME = "Stark Covenant"

__all__ = [
    "APP_NAME", "LANGUAGES", "script_of", "detect_scripts", "dominant_script", "script_runs", "direction_of",
    "store_text", "search_key", "display_html", "bidi_levels", "visual_indices", "get_display",
    "shape_arabic", "isolate_ltr_tokens", "PolyglotPdf", "language_sample_pdf", "extract_pdf_text", "validate_pdf",
    "t", "t_info", "register_translation", "UI_STRINGS", "SHAPING_REQUIRED", "RTL_SCRIPTS",
]

# ======================================================================================================
# 1. Script detection
# ======================================================================================================

_SCRIPT_RANGES_SRC = [
    (0x0041, 0x005A, "Latin"), (0x0061, 0x007A, "Latin"), (0x00AA, 0x00AA, "Latin"), (0x00BA, 0x00BA, "Latin"),
    (0x00C0, 0x00D6, "Latin"), (0x00D8, 0x00F6, "Latin"), (0x00F8, 0x02AF, "Latin"), (0x1D00, 0x1D7F, "Latin"),
    (0x1E00, 0x1EFF, "Latin"), (0x2C60, 0x2C7F, "Latin"), (0xA720, 0xA7FF, "Latin"), (0xAB30, 0xAB6F, "Latin"),
    (0xFB00, 0xFB06, "Latin"), (0xFF21, 0xFF3A, "Latin"), (0xFF41, 0xFF5A, "Latin"),
    (0x0370, 0x03FF, "Greek"), (0x1F00, 0x1FFF, "Greek"),
    (0x0400, 0x052F, "Cyrillic"), (0x1C80, 0x1C8F, "Cyrillic"), (0x2DE0, 0x2DFF, "Cyrillic"), (0xA640, 0xA69F, "Cyrillic"),
    (0x0530, 0x058F, "Armenian"), (0xFB13, 0xFB17, "Armenian"),
    (0x0590, 0x05FF, "Hebrew"), (0xFB1D, 0xFB4F, "Hebrew"),
    (0x0600, 0x06FF, "Arabic"), (0x0750, 0x077F, "Arabic"), (0x0870, 0x08FF, "Arabic"),
    (0xFB50, 0xFDFF, "Arabic"), (0xFE70, 0xFEFF, "Arabic"),
    (0x0700, 0x074F, "Syriac"), (0x0780, 0x07BF, "Thaana"), (0x07C0, 0x07FF, "Nko"),
    (0x0900, 0x097F, "Devanagari"), (0xA8E0, 0xA8FF, "Devanagari"), (0x0980, 0x09FF, "Bengali"),
    (0x0A00, 0x0A7F, "Gurmukhi"), (0x0A80, 0x0AFF, "Gujarati"), (0x0B00, 0x0B7F, "Oriya"),
    (0x0B80, 0x0BFF, "Tamil"), (0x0C00, 0x0C7F, "Telugu"), (0x0C80, 0x0CFF, "Kannada"),
    (0x0D00, 0x0D7F, "Malayalam"), (0x0D80, 0x0DFF, "Sinhala"),
    (0x0E00, 0x0E7F, "Thai"), (0x0E80, 0x0EFF, "Lao"), (0x0F00, 0x0FFF, "Tibetan"), (0x1000, 0x109F, "Myanmar"),
    (0x10A0, 0x10FF, "Georgian"), (0x1C90, 0x1CBF, "Georgian"), (0x2D00, 0x2D2F, "Georgian"),
    (0x1100, 0x11FF, "Hangul"), (0x3130, 0x318F, "Hangul"), (0xA960, 0xA97F, "Hangul"), (0xAC00, 0xD7FF, "Hangul"),
    (0x1200, 0x139F, "Ethiopic"), (0x2D80, 0x2DDF, "Ethiopic"), (0xAB00, 0xAB2F, "Ethiopic"),
    (0x13A0, 0x13FF, "Cherokee"), (0xAB70, 0xABBF, "Cherokee"),
    (0x1400, 0x167F, "Canadian_Aboriginal"), (0x18B0, 0x18FF, "Canadian_Aboriginal"),
    (0x1680, 0x169F, "Ogham"), (0x16A0, 0x16FF, "Runic"), (0x1780, 0x17FF, "Khmer"), (0x19E0, 0x19FF, "Khmer"),
    (0x1800, 0x18AF, "Mongolian"), (0x1950, 0x197F, "Tai_Le"), (0x1C50, 0x1C7F, "Ol_Chiki"),
    (0x2D30, 0x2D7F, "Tifinagh"), (0xA000, 0xA4CF, "Yi"), (0xA500, 0xA63F, "Vai"), (0xA840, 0xA87F, "Phags_Pa"),
    (0x3040, 0x309F, "Hiragana"), (0x30A0, 0x30FF, "Katakana"), (0x31F0, 0x31FF, "Katakana"), (0xFF66, 0xFF9D, "Katakana"),
    (0x3100, 0x312F, "Bopomofo"), (0x31A0, 0x31BF, "Bopomofo"),
    (0x2E80, 0x2FDF, "Han"), (0x3005, 0x3005, "Han"), (0x3007, 0x3007, "Han"), (0x3021, 0x3029, "Han"),
    (0x3038, 0x303B, "Han"), (0x3400, 0x4DBF, "Han"), (0x4E00, 0x9FFF, "Han"), (0xF900, 0xFAFF, "Han"),
    (0x20000, 0x3134F, "Han"),
    (0x1F300, 0x1FAFF, "Emoji"),
]
_SCRIPT_RANGES = sorted(_SCRIPT_RANGES_SRC)
_SCRIPT_STARTS = [r[0] for r in _SCRIPT_RANGES]

RTL_SCRIPTS = frozenset({"Hebrew", "Arabic", "Syriac", "Thaana", "Nko"})
SHAPING_REQUIRED = frozenset({"Devanagari", "Bengali", "Gurmukhi", "Gujarati", "Oriya", "Tamil", "Telugu",
                              "Kannada", "Malayalam", "Sinhala", "Khmer", "Myanmar", "Tibetan", "Mongolian",
                              "Syriac", "Nko"})
MARK_POSITIONING = frozenset({"Thai", "Lao", "Thaana", "Hebrew", "Tai_Le"})  # marks drawn without GPOS


def script_of(ch):
    """Script name of one character; 'Inherited' for combining marks, 'Common' for digits/punctuation."""
    cat = unicodedata.category(ch)
    if cat in ("Mn", "Me"):
        return "Inherited"
    cp = ord(ch)
    k = bisect.bisect_right(_SCRIPT_STARTS, cp) - 1
    if k >= 0:
        a, b, s = _SCRIPT_RANGES[k]
        if a <= cp <= b and (cat[0] in "LM" or s in ("Han", "Emoji") or 0x0660 <= cp <= 0x06F9 or cat == "Nd"):
            return s
    return "Common"


def detect_scripts(text):
    """{script: count} of the letters in text (Common / Inherited excluded)."""
    out = {}
    for ch in text:
        s = script_of(ch)
        if s not in ("Common", "Inherited"):
            out[s] = out.get(s, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def dominant_script(text):
    d = detect_scripts(text)
    return next(iter(d), "Common")


def script_runs(text):
    """[(script, start, end)] with Common/Inherited characters attached to the neighbouring script run."""
    raw = [script_of(c) for c in text]
    res = list(raw)
    last = None
    for i, s in enumerate(res):
        if s in ("Common", "Inherited"):
            if last is not None:
                res[i] = last
        else:
            last = s
    nxt = None
    for i in range(len(res) - 1, -1, -1):
        if res[i] in ("Common", "Inherited"):
            if nxt is not None:
                res[i] = nxt
        else:
            nxt = res[i]
    runs = []
    for i, s in enumerate(res):
        if runs and runs[-1][0] == s:
            runs[-1][2] = i + 1
        else:
            runs.append([s, i, i + 1])
    return [tuple(r) for r in runs]


def direction_of(text):
    """'ltr', 'rtl', 'mixed' or 'neutral' from the strong bidi classes in text."""
    has_l = has_r = False
    for ch in text:
        c = unicodedata.bidirectional(ch)
        if c == "L":
            has_l = True
        elif c in ("R", "AL"):
            has_r = True
    return "mixed" if has_l and has_r else "rtl" if has_r else "ltr" if has_l else "neutral"


def store_text(text, lang=None):
    """Normalise patient text for storage. Returns a dict; 'text' is NFC. Never alters meaning:
    only canonical (NFC) normalisation, CRLF -> LF, lone surrogates rejected."""
    if not isinstance(text, str):
        raise TypeError("text must be str")
    warnings = []
    if any(0xD800 <= ord(c) <= 0xDFFF for c in text):
        raise ValueError("text contains lone surrogate code points (broken UTF-16)")
    norm = unicodedata.normalize("NFC", text.replace("\r\n", "\n"))
    scripts = detect_scripts(norm)
    if any(unicodedata.category(c) == "Co" for c in norm):
        warnings.append("private-use characters present: they only render with the font that defined them")
    if any(unicodedata.category(c) == "Cn" for c in norm):
        warnings.append("unassigned code points present (newer Unicode than this Python's %s?)"
                        % unicodedata.unidata_version)
    bidi_ctl = [c for c in norm if unicodedata.bidirectional(c) in ("LRE", "RLE", "LRO", "RLO", "PDF")]
    if bidi_ctl:
        warnings.append("legacy bidi embedding/override controls present (U+202A-202E); "
                        "prefer isolates U+2066-2069")
    if len(set(scripts) & {"Latin", "Cyrillic", "Greek"}) > 1:
        for word in re.findall(r"\w+", norm):
            ws = set(detect_scripts(word))
            if len(ws) > 1 and ws <= {"Latin", "Cyrillic", "Greek"}:
                warnings.append("word %r mixes Latin/Cyrillic/Greek letters (possible look-alike typo)" % word)
                break
    return {"text": norm, "lang": lang, "scripts": scripts, "direction": direction_of(norm),
            "needs_complex_shaping": sorted(set(scripts) & SHAPING_REQUIRED), "warnings": warnings}


def search_key(text):
    """Key for matching patient text across compatibility variants (NFKC + casefold, Arabic tatweel and
    harakat and Hebrew points removed)."""
    k = unicodedata.normalize("NFKC", text).casefold()
    k = "".join(c for c in k if not (c == "\u0640" or 0x064B <= ord(c) <= 0x065F or 0x0591 <= ord(c) <= 0x05C7
                                     and unicodedata.category(c) == "Mn"))
    return unicodedata.normalize("NFC", k)


_LANG_TAG = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$")


_LTR_TOKEN = re.compile(r"(?<![\w⁦-⁩])[0-9]+(?:[/.:,]?[0-9]+)*(?:[\-‐‑–+][0-9]+(?:[/.:,][0-9]+)*)+(?![\w])")


def isolate_ltr_tokens(text):
    """Clinical-safety helper. After Arabic letters, ASCII digits become Arabic Numbers (UAX #9 W2) and a
    hyphen / plus (ES) does not bind AN runs, so '1980-03-14' DISPLAYS as '14-03-1980' (browsers do the
    same). This wraps such hyphenated ASCII numbers (dates, ranges like 60-100) in LRI ... PDI when the last
    strong letter before them is Arabic-letter (AL), so they read exactly as stored. Numbers joined by / : . ,
    are already kept together by the algorithm and are not touched; neither is text without AL letters,
    nor Arabic-Indic digit dates (an LTR isolate would not keep their part order either)."""
    if not any(unicodedata.bidirectional(c) == "AL" for c in text):
        return text
    out, last = [], 0
    for m in _LTR_TOKEN.finditer(text):
        strong = None
        for k in range(m.start() - 1, -1, -1):
            c = unicodedata.bidirectional(text[k])
            if c in ("L", "R", "AL"):
                strong = c
                break
            if c == "B":
                break
        if strong == "AL":
            out.append(text[last:m.start()] + "⁦" + m.group(0) + "⁩")
            last = m.end()
    out.append(text[last:])
    return "".join(out)


def display_html(text, lang=None, block=False, protect_numbers=True):
    """Escaped HTML for screen display. The browser does bidi + shaping (incl. Indic) correctly when the
    text is isolated with dir=auto and tagged with its language. protect_numbers applies
    isolate_ltr_tokens() so dates like 1980-03-14 are not shown reordered next to Arabic text."""
    tag = "div" if block else "bdi"
    la = ' lang="%s"' % lang if lang and _LANG_TAG.match(lang) else ""
    if protect_numbers:
        text = isolate_ltr_tokens(text)
    body = html.escape(text, quote=False).replace("\n", "<br>")
    return '<%s dir="auto"%s>%s</%s>' % (tag, la, body, tag)


# ======================================================================================================
# 2. Unicode Bidirectional Algorithm (UAX #9)
# ======================================================================================================

_ISO_INIT = frozenset({"LRI", "RLI", "FSI"})
_REMOVED = frozenset({"RLE", "LRE", "RLO", "LRO", "PDF", "BN"})
_STRONG = frozenset({"L", "R", "AL"})
_NI = frozenset({"B", "S", "WS", "ON", "LRI", "RLI", "FSI", "PDI"})
_MAX_DEPTH = 125

_BRACKET_SRC = "()[]{}\u0f3a\u0f3b\u0f3c\u0f3d\u169b\u169c\u2045\u2046\u207d\u207e\u208d\u208e\u2308\u2309" \
               "\u230a\u230b\u2329\u232a\u2768\u2769\u276a\u276b\u276c\u276d\u276e\u276f\u2770\u2771\u2772\u2773" \
               "\u2774\u2775\u27c5\u27c6\u27e6\u27e7\u27e8\u27e9\u27ea\u27eb\u27ec\u27ed\u27ee\u27ef\u2983\u2984" \
               "\u2985\u2986\u2987\u2988\u2989\u298a\u298b\u298c\u2991\u2992\u2993\u2994\u2995\u2996\u2997\u2998" \
               "\u29fc\u29fd\u2e22\u2e23\u2e24\u2e25\u2e26\u2e27\u2e28\u2e29\u3008\u3009\u300a\u300b\u300c\u300d" \
               "\u300e\u300f\u3010\u3011\u3014\u3015\u3016\u3017\u3018\u3019\u301a\u301b\ufe59\ufe5a\ufe5b\ufe5c" \
               "\ufe5d\ufe5e\uff08\uff09\uff3b\uff3d\uff5b\uff5d\uff5f\uff60\uff62\uff63"
_OPEN = {_BRACKET_SRC[i]: _BRACKET_SRC[i + 1] for i in range(0, len(_BRACKET_SRC), 2)}
_CLOSE = set(_OPEN.values())
_CANON = {"\u2329": "\u3008", "\u232a": "\u3009"}
_OPEN = {_CANON.get(k, k): _CANON.get(v, v) for k, v in _OPEN.items()}
_CLOSE = {_CANON.get(c, c) for c in _CLOSE}

_MIRROR_SRC = "()<>[]{}\u00ab\u00bb\u2039\u203a\u2045\u2046\u207d\u207e\u208d\u208e\u2208\u220b\u2209\u220c" \
              "\u220a\u220d\u2264\u2265\u2266\u2267\u226a\u226b\u2282\u2283\u2286\u2287\u2308\u2309\u230a\u230b" \
              "\u2329\u232a\u27e6\u27e7\u27e8\u27e9\u27ea\u27eb\u2983\u2984\u2985\u2986\u3008\u3009\u300a\u300b" \
              "\u300c\u300d\u300e\u300f\u3010\u3011\u3014\u3015\u3016\u3017\u3018\u3019\u301a\u301b\ufe59\ufe5a" \
              "\ufe5b\ufe5c\ufe5d\ufe5e\ufe64\ufe65\uff08\uff09\uff1c\uff1e\uff3b\uff3d\uff5b\uff5d\uff5f\uff60" \
              "\uff62\uff63"
_MIRROR = {}
for _k in range(0, len(_MIRROR_SRC), 2):
    _MIRROR[_MIRROR_SRC[_k]] = _MIRROR_SRC[_k + 1]
    _MIRROR[_MIRROR_SRC[_k + 1]] = _MIRROR_SRC[_k]


def _bidi_class(ch):
    c = unicodedata.bidirectional(ch)
    if c:
        return c
    cp = ord(ch)
    if (cp & 0xFFFE) == 0xFFFE or 0xFDD0 <= cp <= 0xFDEF or 0xE0000 <= cp <= 0xE0FFF:
        return "BN"
    if 0x0600 <= cp <= 0x07BF or 0x0860 <= cp <= 0x08FF or 0xFB50 <= cp <= 0xFDCF or 0xFDF0 <= cp <= 0xFDFF \
            or 0xFE70 <= cp <= 0xFEFF or 0x1EC70 <= cp <= 0x1EEFF:
        return "AL"
    if 0x0590 <= cp <= 0x08FF or 0xFB1D <= cp <= 0xFB4F or 0x10800 <= cp <= 0x10FFF or 0x1E800 <= cp <= 0x1EFFF:
        return "R"
    return "L"


def _first_strong(cls, start, end):
    """P2: level of the first strong char in cls[start:end], skipping isolates; None if none."""
    depth = 0
    for k in range(start, end):
        c = cls[k]
        if c in _ISO_INIT:
            depth += 1
        elif c == "PDI":
            if depth:
                depth -= 1
        elif c == "B":
            break
        elif depth == 0 and c in _STRONG:
            return 0 if c == "L" else 1
    return None


def _match_isolates(cls):
    match, stack = {}, []
    for i, c in enumerate(cls):
        if c in _ISO_INIT:
            stack.append(i)
        elif c == "PDI" and stack:
            match[stack.pop()] = i
    return match


class BidiParagraph:
    """Resolved levels of one paragraph (no paragraph separators inside except possibly the last char)."""

    def __init__(self, text, base=None):
        self.text = text
        n = len(text)
        self.classes = cls = [_bidi_class(c) for c in text]
        match = _match_isolates(cls)
        if base in ("L", "ltr", 0):
            para = 0
        elif base in ("R", "rtl", 1):
            para = 1
        else:  # P2-P3
            fs = _first_strong(cls, 0, n)
            para = fs if fs is not None else 0
        self.para_level = para
        levels, types = self._explicit(cls, para, match)
        self._resolve(cls, types, levels, para, match)
        self.levels = levels
        self.types = types

    # X1-X8 --------------------------------------------------------------------------------------
    @staticmethod
    def _explicit(cls, para, match):
        n = len(cls)
        levels = [para] * n
        types = list(cls)
        stack = [(para, None, False)]
        ovf_iso = ovf_emb = valid_iso = 0
        for i, c in enumerate(cls):
            if c in ("RLE", "LRE", "RLO", "LRO"):
                lvl = stack[-1][0]
                new = (lvl + 1) | 1 if c in ("RLE", "RLO") else (lvl + 2) & ~1
                levels[i] = lvl
                if new <= _MAX_DEPTH and ovf_iso == 0 and ovf_emb == 0:
                    stack.append((new, {"RLO": "R", "LRO": "L"}.get(c), False))
                elif ovf_iso == 0:
                    ovf_emb += 1
            elif c in _ISO_INIT:
                lvl, ovr, _iso = stack[-1]
                levels[i] = lvl
                if ovr:
                    types[i] = ovr
                rtl = c == "RLI"
                if c == "FSI":
                    rtl = _first_strong(cls, i + 1, match.get(i, n)) == 1
                new = (lvl + 1) | 1 if rtl else (lvl + 2) & ~1
                if new <= _MAX_DEPTH and ovf_iso == 0 and ovf_emb == 0:
                    valid_iso += 1
                    stack.append((new, None, True))
                else:
                    ovf_iso += 1
            elif c == "PDI":
                if ovf_iso > 0:
                    ovf_iso -= 1
                elif valid_iso:
                    ovf_emb = 0
                    while not stack[-1][2]:
                        stack.pop()
                    stack.pop()
                    valid_iso -= 1
                lvl, ovr, _iso = stack[-1]
                levels[i] = lvl
                if ovr:
                    types[i] = ovr
            elif c == "PDF":
                if ovf_iso > 0:
                    pass
                elif ovf_emb > 0:
                    ovf_emb -= 1
                elif not stack[-1][2] and len(stack) >= 2:
                    stack.pop()
                levels[i] = stack[-1][0]
            elif c == "B":
                levels[i] = para
            elif c == "BN":
                levels[i] = stack[-1][0]
            else:
                lvl, ovr, _iso = stack[-1]
                levels[i] = lvl
                if ovr:
                    types[i] = ovr
        return levels, types

    # X9-X10, W, N, I --------------------------------------------------------------------------
    def _resolve(self, cls, types, levels, para, match):
        keep = [i for i, c in enumerate(cls) if c not in _REMOVED]
        pos = {i: k for k, i in enumerate(keep)}
        runs = []
        for i in keep:
            if runs and levels[runs[-1][-1]] == levels[i]:
                runs[-1].append(i)
            else:
                runs.append([i])
        run_of = {}
        for r, run in enumerate(runs):
            for i in run:
                run_of[i] = r
        matched_pdi = set(match.values())
        seqs = []
        for run in runs:
            if cls[run[0]] == "PDI" and run[0] in matched_pdi:
                continue
            seq = list(run)
            while cls[seq[-1]] in _ISO_INIT and seq[-1] in match and match[seq[-1]] in run_of:
                seq.extend(runs[run_of[match[seq[-1]]]])
            seqs.append(seq)
        orig = list(types)
        for seq in seqs:
            first, last = seq[0], seq[-1]
            lvl = levels[first]
            pk = pos[first]
            prev_lvl = levels[keep[pk - 1]] if pk > 0 else para
            sos = "R" if max(lvl, prev_lvl) % 2 else "L"
            if cls[last] in _ISO_INIT:
                next_lvl = para
            else:
                nk = pos[last] + 1
                next_lvl = levels[keep[nk]] if nk < len(keep) else para
            eos = "R" if max(lvl, next_lvl) % 2 else "L"
            t = [types[i] for i in seq]
            self._weak(t, sos)
            self._brackets(seq, t, [orig[i] for i in seq], lvl, sos)
            self._neutral(t, sos, eos, lvl)
            for k, i in enumerate(seq):  # I1-I2
                x = t[k]
                types[i] = x
                if lvl % 2 == 0:
                    if x == "R":
                        levels[i] = lvl + 1
                    elif x in ("AN", "EN"):
                        levels[i] = lvl + 2
                elif x in ("L", "EN", "AN"):
                    levels[i] = lvl + 1
        prev = para  # removed characters: level of the preceding character (they are not displayed)
        for i, c in enumerate(cls):
            if c in _REMOVED:
                levels[i] = prev
            else:
                prev = levels[i]

    @staticmethod
    def _weak(t, sos):
        n = len(t)
        prev = sos
        for k in range(n):  # W1
            if t[k] == "NSM":
                t[k] = "ON" if prev in _ISO_INIT or prev == "PDI" else prev
            prev = t[k]
        last = sos
        for k in range(n):  # W2
            if t[k] in _STRONG:
                last = t[k]
            elif t[k] == "EN" and last == "AL":
                t[k] = "AN"
        for k in range(n):  # W3
            if t[k] == "AL":
                t[k] = "R"
        for k in range(1, n - 1):  # W4
            if t[k] == "ES" and t[k - 1] == "EN" and t[k + 1] == "EN":
                t[k] = "EN"
            elif t[k] == "CS" and t[k - 1] == t[k + 1] and t[k - 1] in ("EN", "AN"):
                t[k] = t[k - 1]
        k = 0
        while k < n:  # W5
            if t[k] == "ET":
                j = k
                while j < n and t[j] == "ET":
                    j += 1
                if (k > 0 and t[k - 1] == "EN") or (j < n and t[j] == "EN"):
                    for m in range(k, j):
                        t[m] = "EN"
                k = j
            else:
                k += 1
        for k in range(n):  # W6
            if t[k] in ("ES", "ET", "CS"):
                t[k] = "ON"
        last = sos
        for k in range(n):  # W7
            if t[k] in ("L", "R"):
                last = t[k]
            elif t[k] == "EN" and last == "L":
                t[k] = "L"

    def _brackets(self, seq, t, orig, lvl, sos):
        """N0 with BD16 bracket-pair identification."""
        stack, pairs = [], []
        for k, i in enumerate(seq):
            if t[k] != "ON":
                continue
            ch = _CANON.get(self.text[i], self.text[i])
            if ch in _OPEN:
                if len(stack) == 63:
                    break
                stack.append((_OPEN[ch], k))
            elif ch in _CLOSE:
                for s in range(len(stack) - 1, -1, -1):
                    if stack[s][0] == ch:
                        pairs.append((stack[s][1], k))
                        del stack[s:]
                        break
        if not pairs:
            return
        pairs.sort()
        e = "R" if lvl % 2 else "L"

        def strong(x):
            return "R" if x in ("R", "EN", "AN") else "L" if x == "L" else None

        for o, c in pairs:
            found_e = found_o = False
            for m in range(o + 1, c):
                s = strong(t[m])
                if s == e:
                    found_e = True
                    break
                if s:
                    found_o = True
            if found_e:
                new = e
            elif found_o:
                new = sos
                for m in range(o - 1, -1, -1):
                    s = strong(t[m])
                    if s:
                        new = s
                        break
            else:
                continue
            for b in (o, c):
                t[b] = new
                m = b + 1
                while m < len(t) and orig[m] == "NSM":
                    t[m] = new
                    m += 1

    @staticmethod
    def _neutral(t, sos, eos, lvl):
        n = len(t)
        e = "R" if lvl % 2 else "L"

        def side(x):
            return "R" if x in ("R", "EN", "AN") else "L" if x == "L" else None

        k = 0
        while k < n:
            if t[k] in _NI:
                j = k
                while j < n and t[j] in _NI:
                    j += 1
                before = sos if k == 0 else side(t[k - 1])
                after = eos if j == n else side(t[j])
                fill = before if before is not None and before == after else e
                for m in range(k, j):
                    t[m] = fill
                k = j
            else:
                k += 1

    # L1-L2 ------------------------------------------------------------------------------------
    def line_levels(self, start, end):
        """Levels for text[start:end] as one display line, after rule L1."""
        lv = self.levels[start:end]
        cls = self.classes[start:end]
        para = self.para_level
        ws = ("WS", "LRI", "RLI", "FSI", "PDI") + tuple(_REMOVED)
        n = len(lv)
        for k in range(n):
            if cls[k] in ("S", "B"):
                lv[k] = para
                m = k - 1
                while m >= 0 and cls[m] in ws:
                    lv[m] = para
                    m -= 1
        m = n - 1
        while m >= 0 and cls[m] in ws:
            lv[m] = para
            m -= 1
        return lv

    def visual_order(self, start=0, end=None, keep_marks_after_base=True):
        """Logical indices of text[start:end] in visual (left-to-right) order (rule L2)."""
        if end is None:
            end = len(self.text)
        lv = self.line_levels(start, end)
        units = []  # clusters: (level, [indices])
        for k in range(end - start):
            i = start + k
            if keep_marks_after_base and units and unicodedata.category(self.text[i]) in ("Mn", "Me") \
                    and self.classes[i] not in _REMOVED:
                units[-1][1].append(i)
            else:
                units.append((lv[k], [i]))
        if not units:
            return []
        hi = max(u[0] for u in units)
        lo_odd = min((u[0] for u in units if u[0] % 2), default=None)
        if lo_odd is not None:
            for level in range(hi, lo_odd - 1, -1):
                k = 0
                while k < len(units):
                    if units[k][0] >= level:
                        j = k
                        while j < len(units) and units[j][0] >= level:
                            j += 1
                        units[k:j] = units[k:j][::-1]
                        k = j
                    else:
                        k += 1
        return [i for _lvl, idx in units for i in idx]


def _paragraphs(text):
    """Split into (start, end) paragraph ranges; the separator stays with its paragraph."""
    out, start = [], 0
    for i, ch in enumerate(text):
        if unicodedata.bidirectional(ch) == "B":
            if ch == "\r" and i + 1 < len(text) and text[i + 1] == "\n":
                continue
            out.append((start, i + 1))
            start = i + 1
    if start < len(text) or not out:
        out.append((start, len(text)))
    return out


def bidi_levels(text, base=None):
    """-> (levels, paragraph_levels) with one entry per character of text."""
    levels, paras = [], []
    for a, b in _paragraphs(text):
        p = BidiParagraph(text[a:b], base)
        levels += p.levels
        paras += [p.para_level] * (b - a)
    return levels, paras


def visual_indices(text, base=None):
    """Visual order of logical indices, line by line (paragraph separators end lines)."""
    out = []
    for a, b in _paragraphs(text):
        p = BidiParagraph(text[a:b], base)
        out += [a + i for i in p.visual_order()]
    return out


def get_display(text, base=None, shape=True, mirror=True, keep_controls=False):
    """Visual-order string (what a left-to-right renderer must draw) with Arabic shaping and mirroring."""
    out = []
    for a, b in _paragraphs(text):
        seg = text[a:b]
        brk = ""
        if seg and unicodedata.bidirectional(seg[-1]) == "B":
            brk = seg[-1]
            if seg.endswith("\r\n"):
                brk = "\r\n"
            seg = seg[:len(seg) - len(brk)]
        p = BidiParagraph(seg, base)
        shaped = shape_arabic(seg)[0] if shape else list(seg)
        for i in p.visual_order():
            ch = shaped[i]
            if not keep_controls and p.classes[i] in _REMOVED | _ISO_INIT | {"PDI"} \
                    and unicodedata.category(seg[i]) == "Cf":
                continue
            if mirror and p.levels[i] % 2 and len(ch) == 1 and ch in _MIRROR and unicodedata.mirrored(ch):
                ch = _MIRROR[ch]
            out.append(ch)
        out.append(brk)
    return "".join(out)


# ======================================================================================================
# 3. Arabic contextual shaping
# ======================================================================================================

_FORMS = {}   # nominal char -> {form: presentation char}
_LAM_ALEF = {}  # alef variant -> {form: ligature char}
for _cp in list(range(0xFB50, 0xFE00)) + list(range(0xFE70, 0xFF00)):
    _m = re.match(r"<(isolated|final|initial|medial)> ([0-9A-F ]+)$", unicodedata.decomposition(chr(_cp)))
    if not _m:
        continue
    _parts = [chr(int(_x, 16)) for _x in _m.group(2).split()]
    if len(_parts) == 1 and unicodedata.category(_parts[0]) == "Lo":
        _FORMS.setdefault(_parts[0], {}).setdefault(_m.group(1), chr(_cp))
    elif len(_parts) == 2 and _parts[0] == "\u0644" and _parts[1] in "\u0622\u0623\u0625\u0627":
        _LAM_ALEF.setdefault(_parts[1], {}).setdefault(_m.group(1), chr(_cp))


def joining_type(ch):
    """Approximate Unicode Joining_Type: D dual, R right, C join-causing, T transparent, U non-joining."""
    if ch in ("\u200d", "\u0640", "\u07fa"):
        return "C"
    if ch == "\u200c":
        return "U"
    cat = unicodedata.category(ch)
    if cat in ("Mn", "Me", "Cf"):
        return "T"
    f = _FORMS.get(ch)
    if f:
        if "initial" in f or "medial" in f:
            return "D"
        if "final" in f:
            return "R"
    return "U"


def shape_arabic(text):
    """Contextual shaping in LOGICAL order.
    -> (display, source, unshaped) where display[i] is the presentation form for text[i] ('' when text[i]
    was absorbed into a lam-alef ligature), source[i] the logical text that glyph stands for (for ToUnicode)
    and unshaped the set of joining-script letters that could not be shaped (no presentation forms)."""
    n = len(text)
    jt = [joining_type(c) for c in text]
    display, source = list(text), list(text)
    unshaped = set()
    i = 0
    while i < n:
        ch = text[i]
        if ch not in _FORMS:
            if jt[i] == "U" and script_of(ch) in ("Arabic", "Syriac", "Nko", "Mongolian") \
                    and unicodedata.category(ch) == "Lo":
                unshaped.add(ch)
            i += 1
            continue
        p = i - 1
        while p >= 0 and jt[p] == "T":
            p -= 1
        q = i + 1
        while q < n and jt[q] == "T":
            q += 1
        joins_prev = p >= 0 and jt[p] in ("D", "C") and jt[i] in ("D", "R", "C") and display[p] != ""
        if ch == "\u0644" and q < n and text[q] in _LAM_ALEF:
            forms = _LAM_ALEF[text[q]]
            lig = forms.get("final" if joins_prev else "isolated") or forms.get("isolated")
            if lig:
                display[i] = lig
                source[i] = ch + text[q]
                display[q] = ""
                source[q] = ""
                jt[q] = "R"
                i = q + 1
                continue
        joins_next = q < n and jt[i] in ("D", "C") and jt[q] in ("D", "R", "C")
        forms = _FORMS[ch]
        if joins_prev and joins_next:
            form = forms.get("medial") or forms.get("final")
        elif joins_prev:
            form = forms.get("final")
        elif joins_next:
            form = forms.get("initial") or forms.get("isolated")
        else:
            form = forms.get("isolated")
        display[i] = form or ch
        i += 1
    return display, source, unshaped


# ======================================================================================================
# 4. TrueType / TTC reading and subsetting (adapted from Gorgon Quill quill_export.py)
# ======================================================================================================

class TrueTypeFont:
    """Minimal TrueType reader: cmap (formats 4 and 12), hmtx, glyf subsetting, fsType."""

    def __init__(self, path, index=0):
        with open(path, "rb") as f:
            self.data = data = f.read()
        self.path, self.index = path, index
        base = 0
        if data[:4] == b"ttcf":
            n = struct.unpack(">I", data[8:12])[0]
            if index >= n:
                raise ValueError("TTC index out of range")
            base = struct.unpack(">I", data[12 + 4 * index:16 + 4 * index])[0]
        num = struct.unpack(">H", data[base + 4:base + 6])[0]
        self.tables = {}
        for k in range(num):
            rec = base + 12 + 16 * k
            tag, _cs, off, ln = struct.unpack(">4sIII", data[rec:rec + 16])
            self.tables[tag.decode("latin-1")] = (off, ln)
        if "glyf" not in self.tables or "loca" not in self.tables:
            raise ValueError("not a glyf-outline TrueType font (CFF/bitmap fonts unsupported)")
        h = self._t("head")
        self.units_per_em = struct.unpack(">H", h[18:20])[0]
        self.bbox = struct.unpack(">hhhh", h[36:44])
        self.loca_long = struct.unpack(">h", h[50:52])[0] == 1
        hh = self._t("hhea")
        self.ascent, self.descent = struct.unpack(">hh", hh[4:8])
        n_hm = struct.unpack(">H", hh[34:36])[0]
        self.num_glyphs = struct.unpack(">H", self._t("maxp")[4:6])[0]
        hm = self._t("hmtx")
        adv = list(struct.unpack(">" + "Hh" * n_hm, hm[:4 * n_hm])[0::2])
        if n_hm < self.num_glyphs:
            adv += [adv[-1]] * (self.num_glyphs - n_hm)
        self.advances = adv
        self.cap_height = self.ascent
        self.fs_type = 0
        if "OS/2" in self.tables:
            o = self._t("OS/2")
            self.fs_type = struct.unpack(">H", o[8:10])[0]
            if len(o) >= 90 and struct.unpack(">H", o[0:2])[0] >= 2:
                self.cap_height = struct.unpack(">h", o[88:90])[0]
        self.italic_angle = 0
        if "post" in self.tables:
            p = self._t("post")
            self.italic_angle = struct.unpack(">i", p[4:8])[0] / 65536.0
        self.ps_name = self._name(6) or os.path.splitext(os.path.basename(path))[0]
        self.ps_name = re.sub(r"[^A-Za-z0-9_.+-]", "", self.ps_name) or "Font"
        self.family = self._name(1) or self.ps_name
        self.cmap = self._parse_cmap()

    @property
    def embeddable(self):
        """False when OS/2 fsType is exactly Restricted License embedding (bit 1 only)."""
        return (self.fs_type & 0x000F) != 0x0002

    def _t(self, tag):
        off, ln = self.tables[tag]
        return self.data[off:off + ln]

    def _name(self, nid_want):
        if "name" not in self.tables:
            return None
        t = self._t("name")
        count, soff = struct.unpack(">HH", t[2:6])
        best = None
        for k in range(count):
            pid, eid, lid, nid, ln, off = struct.unpack(">6H", t[6 + 12 * k:18 + 12 * k])
            if nid != nid_want:
                continue
            raw = t[soff + off:soff + off + ln]
            try:
                s = raw.decode("utf-16-be") if pid in (0, 3) else raw.decode("latin-1")
            except UnicodeDecodeError:
                continue
            if pid == 3 and lid == 0x409:
                return s
            best = best or s
        return best

    def _parse_cmap(self):
        t = self._t("cmap")
        n = struct.unpack(">H", t[2:4])[0]
        subs = {}
        for k in range(n):
            pid, eid, off = struct.unpack(">HHI", t[4 + 8 * k:12 + 8 * k])
            fmt = struct.unpack(">H", t[off:off + 2])[0]
            subs[(pid, eid, fmt)] = off
        for key in ((3, 10, 12), (0, 6, 12), (0, 4, 12), (3, 1, 4), (0, 3, 4), (0, 1, 4), (0, 0, 4), (3, 0, 4)):
            if key in subs:
                off = subs[key]
                return self._cmap12(t, off) if key[2] == 12 else self._cmap4(t, off, key[1] == 0 and key[0] == 3)
        return {}

    @staticmethod
    def _cmap4(t, off, symbol):
        segx2 = struct.unpack(">H", t[off + 6:off + 8])[0]
        seg = segx2 // 2
        ends = struct.unpack(">%dH" % seg, t[off + 14:off + 14 + segx2])
        p = off + 16 + segx2
        starts = struct.unpack(">%dH" % seg, t[p:p + segx2])
        deltas = struct.unpack(">%dh" % seg, t[p + segx2:p + 2 * segx2])
        ro_pos = p + 2 * segx2
        ros = struct.unpack(">%dH" % seg, t[ro_pos:ro_pos + segx2])
        cmap = {}
        for i in range(seg):
            s, e, d, ro = starts[i], ends[i], deltas[i], ros[i]
            if s == 0xFFFF:
                continue
            for c in range(s, e + 1):
                if ro == 0:
                    g = (c + d) & 0xFFFF
                else:
                    gp = ro_pos + 2 * i + ro + 2 * (c - s)
                    g = struct.unpack(">H", t[gp:gp + 2])[0]
                    if g:
                        g = (g + d) & 0xFFFF
                if g:
                    cmap[c] = g
                    if symbol and 0xF000 <= c <= 0xF0FF:
                        cmap.setdefault(c - 0xF000, g)
        return cmap

    @staticmethod
    def _cmap12(t, off):
        n = struct.unpack(">I", t[off + 12:off + 16])[0]
        cmap = {}
        p = off + 16
        for _ in range(n):
            s, e, g = struct.unpack(">III", t[p:p + 12])
            p += 12
            if e - s > 0x30000:
                e = s + 0x30000
            for c in range(s, e + 1):
                if g + (c - s):
                    cmap[c] = g + (c - s)
        return cmap

    def gid(self, ch):
        g = self.cmap.get(ord(ch), 0)
        return g if g < self.num_glyphs else 0

    def width1000(self, gid):
        return self.advances[gid] * 1000.0 / self.units_per_em

    def _loca(self):
        raw = self._t("loca")
        n = self.num_glyphs + 1
        if self.loca_long:
            return struct.unpack(">%dI" % n, raw[:4 * n])
        return [x * 2 for x in struct.unpack(">%dH" % n, raw[:2 * n])]

    def subset(self, gids):
        """Standalone TrueType file keeping glyph ids, with unused glyph outlines emptied."""
        loca = self._loca()
        goff = self.tables["glyf"][0]
        data = self.data
        keep = set(gids) | {0}
        todo = list(keep)
        while todo:  # pull in composite components
            g = todo.pop()
            if g >= self.num_glyphs:
                continue
            a, b = goff + loca[g], goff + loca[g + 1]
            if b - a < 10 or struct.unpack(">h", data[a:a + 2])[0] >= 0:
                continue
            p = a + 10
            while True:
                flags, comp = struct.unpack(">HH", data[p:p + 4])
                p += 4 + (4 if flags & 1 else 2)
                p += 2 if flags & 8 else 4 if flags & 0x40 else 8 if flags & 0x80 else 0
                if comp not in keep:
                    keep.add(comp)
                    todo.append(comp)
                if not flags & 0x20:
                    break
        glyf = bytearray()
        new_loca = [0]
        for g in range(self.num_glyphs):
            if g in keep:
                glyf += data[goff + loca[g]:goff + loca[g + 1]]
                if len(glyf) % 4:
                    glyf += b"\0" * (4 - len(glyf) % 4)
            new_loca.append(len(glyf))
        head = bytearray(self._t("head"))
        head[8:12] = b"\0\0\0\0"
        head[50:52] = struct.pack(">h", 1)
        tables = {"head": bytes(head), "hhea": self._t("hhea"), "maxp": self._t("maxp"),
                  "hmtx": self._t("hmtx"), "loca": struct.pack(">%dI" % len(new_loca), *new_loca),
                  "glyf": bytes(glyf)}
        for tag in ("cvt ", "fpgm", "prep", "OS/2", "post"):
            if tag in self.tables:
                tables[tag] = self._t(tag)
        if "post" in tables:
            tables["post"] = struct.pack(">I", 0x00030000) + tables["post"][4:32]
        return _build_sfnt(tables)


def _sfnt_checksum(b):
    if len(b) % 4:
        b = b + b"\0" * (4 - len(b) % 4)
    return sum(struct.unpack(">%dI" % (len(b) // 4), b)) & 0xFFFFFFFF


def _build_sfnt(tables):
    tags = sorted(tables)
    n = len(tags)
    es = max(k for k in range(16) if 2 ** k <= n)
    sr = (2 ** es) * 16
    out = bytearray(struct.pack(">IHHHH", 0x00010000, n, sr, es, n * 16 - sr))
    offset = 12 + 16 * n
    body = bytearray()
    for tag in tags:
        t = tables[tag]
        out += struct.pack(">4sIII", tag.encode("latin-1"), _sfnt_checksum(t), offset + len(body), len(t))
        body += t
        if len(body) % 4:
            body += b"\0" * (4 - len(body) % 4)
    return bytes(out + body)


# ======================================================================================================
# 5. Font selection per script
# ======================================================================================================

_LATINISH = ["arial.ttf", "segoeui.ttf", "tahoma.ttf", "times.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf",
             "NotoSans-Regular.ttf", "Arial.ttf"]
_INDIC = ["Nirmala.ttc", "Nirmala.ttf", "NirmalaUI.ttf", "mangal.ttf", "NotoSansDevanagari-Regular.ttf"]
_SCRIPT_FONTS = {
    "Latin": _LATINISH, "Greek": _LATINISH, "Cyrillic": _LATINISH, "Common": _LATINISH + ["seguisym.ttf"],
    "Inherited": _LATINISH,
    "Armenian": ["segoeui.ttf", "sylfaen.ttf", "arial.ttf"], "Georgian": ["segoeui.ttf", "sylfaen.ttf"],
    "Hebrew": ["arial.ttf", "tahoma.ttf", "segoeui.ttf", "times.ttf", "DejaVuSans.ttf"],
    "Arabic": ["arial.ttf", "tahoma.ttf", "segoeui.ttf", "times.ttf", "DejaVuSans.ttf"],
    "Syriac": ["estre.ttf", "seguihis.ttf"], "Thaana": ["mvboli.ttf"], "Nko": ["ebrima.ttf"],
    "Devanagari": _INDIC, "Bengali": _INDIC + ["vrinda.ttf"], "Gurmukhi": _INDIC + ["raavi.ttf"],
    "Gujarati": _INDIC + ["shruti.ttf"], "Oriya": _INDIC + ["kalinga.ttf"], "Tamil": _INDIC + ["latha.ttf"],
    "Telugu": _INDIC + ["gautami.ttf"], "Kannada": _INDIC + ["tunga.ttf"], "Malayalam": _INDIC + ["kartika.ttf"],
    "Sinhala": _INDIC + ["iskpota.ttf"], "Ol_Chiki": _INDIC,
    "Thai": ["LeelawUI.ttf", "tahoma.ttf", "leelawad.ttf"], "Lao": ["LeelawUI.ttf", "laoui.ttf"],
    "Khmer": ["LeelawUI.ttf", "KhmerUI.ttf", "daunpenh.ttf"], "Myanmar": ["mmrtext.ttf"],
    "Tibetan": ["himalaya.ttf"], "Ethiopic": ["ebrima.ttf", "nyala.ttf"], "Tifinagh": ["ebrima.ttf"],
    "Vai": ["ebrima.ttf"], "Cherokee": ["gadugi.ttf", "plantc.ttf"], "Canadian_Aboriginal": ["gadugi.ttf", "euphemia.ttf"],
    "Mongolian": ["monbaiti.ttf"], "Tai_Le": ["taile.ttf"], "Phags_Pa": ["phagspa.ttf"], "Yi": ["msyi.ttf"],
    "Runic": ["seguisym.ttf", "seguihis.ttf"], "Ogham": ["seguisym.ttf", "seguihis.ttf"],
    "Hangul": ["malgun.ttf", "gulim.ttc", "batang.ttc"],
    "Hiragana": ["YuGothR.ttc", "msgothic.ttc", "meiryo.ttc", "msyh.ttc"],
    "Katakana": ["YuGothR.ttc", "msgothic.ttc", "meiryo.ttc", "msyh.ttc"],
    "Bopomofo": ["msjh.ttc", "msyh.ttc"],
    "Emoji": ["seguiemj.ttf", "seguisym.ttf"],
}
_HAN_FONTS = {
    "ja": ["YuGothR.ttc", "msgothic.ttc", "meiryo.ttc"],
    "ko": ["malgun.ttf", "batang.ttc", "gulim.ttc"],
    "zh-Hant": ["msjh.ttc", "mingliu.ttc"],
    "zh-Hans": ["msyh.ttc", "simsun.ttc"],
}
_FALLBACK = ["segoeui.ttf", "arial.ttf", "seguisym.ttf", "Nirmala.ttc", "msyh.ttc", "malgun.ttf", "ebrima.ttf",
             "gadugi.ttf", "LeelawUI.ttf", "tahoma.ttf", "sylfaen.ttf", "seguihis.ttf", "mmrtext.ttf", "himalaya.ttf",
             "mvboli.ttf", "monbaiti.ttf", "taile.ttf", "phagspa.ttf", "YuGothR.ttc", "simsun.ttc", "msjh.ttc",
             "SimsunExtG.ttf", "seguiemj.ttf", "micross.ttf", "DejaVuSans.ttf", "unifont.ttf"]


def _han_key(lang):
    lang = (lang or "").lower()
    if lang.startswith("ja"):
        return "ja"
    if lang.startswith("ko"):
        return "ko"
    if lang in ("zh-hant", "zh-tw", "zh-hk", "zh-mo", "yue") or lang.startswith(("zh-hant", "yue")):
        return "zh-Hant"
    return "zh-Hans"


_FONT_INDEX = None


def _font_dirs():
    dirs = []
    if os.name == "nt":
        dirs.append(os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))
        la = os.environ.get("LOCALAPPDATA")
        if la:
            dirs.append(os.path.join(la, "Microsoft", "Windows", "Fonts"))
    home = os.path.expanduser("~")
    dirs += ["/System/Library/Fonts", "/System/Library/Fonts/Supplemental", "/Library/Fonts",
             os.path.join(home, "Library/Fonts"), "/usr/share/fonts", "/usr/local/share/fonts",
             os.path.join(home, ".fonts"), os.path.join(home, ".local/share/fonts")]
    return [d for d in dirs if os.path.isdir(d)]


def _font_index():
    global _FONT_INDEX
    if _FONT_INDEX is None:
        idx = {}
        for d in _font_dirs():
            for root, sub, names in os.walk(d):
                for nm in names:
                    if nm.lower().endswith((".ttf", ".ttc")):
                        idx.setdefault(nm.lower(), os.path.join(root, nm))
                if root.count(os.sep) - d.count(os.sep) > 4:
                    sub[:] = []
        _FONT_INDEX = idx
    return _FONT_INDEX


class FontBook:
    """Chooses a font per (character, script, language) and records honest substitution warnings."""

    def __init__(self, overrides=None):
        self.overrides = {k: [os.fspath(p) for p in v] for k, v in (overrides or {}).items()}
        self.loaded = {}
        self.cache = {}
        self.problems = {}      # path -> reason the font could not be used
        self.substituted = {}   # (script, lang-key, font family) -> count
        self.missing = {}       # char -> count

    def _paths(self, names):
        idx = _font_index()
        out = []
        for nm in names:
            p = nm if os.path.isabs(nm) else idx.get(nm.lower())
            if p and os.path.isfile(p) and p not in out:
                out.append(p)
        return out

    def font(self, path):
        if path not in self.loaded:
            f = None
            try:
                f = TrueTypeFont(path, 0)
                if not f.embeddable:
                    self.problems[path] = "Restricted License embedding (OS/2 fsType); not embedded"
                    f = None
            except Exception as e:  # unreadable / CFF font
                self.problems[path] = "%s: %s" % (type(e).__name__, e)
            self.loaded[path] = f
        return self.loaded[path]

    def preferred(self, script, lang):
        if script in self.overrides:
            return self._paths(self.overrides[script])
        if script == "Han":
            return self._paths(_HAN_FONTS[_han_key(lang)])
        return self._paths(_SCRIPT_FONTS.get(script, _LATINISH))

    def resolve(self, ch, script, lang=None):
        """-> (TrueTypeFont, gid, status) status in preferred / substituted / missing."""
        key = (ch, script, _han_key(lang) if script == "Han" else None)
        hit = self.cache.get(key)
        if hit is not None:
            if hit[2] == "missing":
                self.missing[ch] = self.missing.get(ch, 0) + 1
            elif hit[2] == "substituted":
                k2 = (script, key[2], hit[0].family)
                self.substituted[k2] = self.substituted.get(k2, 0) + 1
            return hit
        pref = self.preferred(script, lang)
        res = None
        for p in pref:
            f = self.font(p)
            if f is not None:
                g = f.gid(ch)
                if g:
                    res = (f, g, "preferred")
                    break
        if res is None:
            names = _FALLBACK
            if script == "Han":
                names = sum(_HAN_FONTS.values(), []) + _FALLBACK
            for p in self._paths(names) + self._paths(_LATINISH):
                if p in pref:
                    continue
                f = self.font(p)
                if f is not None:
                    g = f.gid(ch)
                    if g:
                        res = (f, g, "substituted")
                        break
        if res is None:
            base = None
            for p in pref + self._paths(_LATINISH + _FALLBACK):
                base = self.font(p)
                if base is not None:
                    break
            if base is None:
                raise RuntimeError("no usable TrueType font found on this system")
            res = (base, 0, "missing")
        self.cache[key] = res
        return self.resolve(ch, script, lang)


# ======================================================================================================
# 6. PDF writer
# ======================================================================================================

def _pdf_utf16(s):
    """PDF text string as UTF-16BE with BOM (hex string)."""
    return b"<FEFF" + s.encode("utf-16-be").hex().upper().encode("ascii") + b">"


def _pdf_name_lang(lang):
    return re.sub(r"[^A-Za-z0-9-]", "", lang or "")


def _fmt(x):
    s = ("%.3f" % x).rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


_SKIP_CF = frozenset("\u200b\u200c\u200d\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2060\u2066\u2067\u2068\u2069"
                     "\ufeff\u00ad\ufe0e\ufe0f")
_CJK_BREAK = frozenset({"Han", "Hiragana", "Katakana", "Bopomofo", "Yi"})


class _Cluster(list):
    """Glyphs of one shaped cluster (on the cluster's first character); .text = its logical characters."""
    __slots__ = ("text",)

    def __init__(self, *a):
        super().__init__(*a)
        self.text = None


class PolyglotPdf:
    """Flowing multi-script PDF. Add headings / paragraphs, then build() -> bytes.

    pdf = PolyglotPdf("Discharge summary", author="Dr. X", lang="en")
    pdf.heading("Patient", bookmark="Patient")
    pdf.paragraph("مريم أحمد  —  DOB 1980-03-14", lang="ar")
    data = pdf.build(); pdf.warnings
    """

    def __init__(self, title, author="", subject="", keywords="", lang="en", page_size=(595.276, 841.89),
                 margin=56.0, font_size=11.0, fonts=None, shaping=True):
        """shaping: True = shape runs with ot_shaper (OpenType GSUB / GPOS); False = the original unshaped
        path (Arabic presentation forms, everything else one glyph per character)."""
        self.title, self.author, self.subject, self.keywords, self.lang = title, author, subject, keywords, lang
        self.shaping = bool(shaping) and _ots is not None
        self._shaper_missing = bool(shaping) and _ots is None
        self._shape_log = {}    # script -> {"ok": n, "no_tables": {family}, "errors": [..]}
        self.pw, self.ph = page_size
        self.margin = margin
        self.size = font_size
        self.book = FontBook(fonts)
        self.blocks = []
        self.warnings = []
        self._used = {}     # font path -> {"key", "font", "map": {gid: source text}}
        self._pages = []
        self._outline = []

    def heading(self, text, lang=None, bookmark=True, size=None, base=None):
        bm = text if bookmark is True else bookmark or None
        self.blocks.append(("h", text, lang, base, size or self.size * 1.45, bm))

    def paragraph(self, text, lang=None, base=None, size=None, bookmark=None, protect_numbers=True):
        """protect_numbers: isolate dates / ranges next to Arabic text (see isolate_ltr_tokens)."""
        if protect_numbers:
            text = isolate_ltr_tokens(text)
        self.blocks.append(("p", text, lang, base, size or self.size, bookmark))

    # -- layout --------------------------------------------------------------------------------
    def _fontkey(self, f):
        ent = self._used.get(f.path)
        if ent is None:
            ent = self._used[f.path] = {"key": "F%d" % (len(self._used) + 1), "font": f, "map": {}}
        return ent

    def _glyphs(self, text, lang):
        """Per logical index: list of (fontkey-entry, gid, width1000, source[, dx1000, dy1000]) (empty for
        skipped chars). Shaped text puts a cluster's glyphs on its first character (a _Cluster with .text)."""
        if self.shaping:
            try:
                return self._glyphs_shaped(text, lang)
            except Exception as e:  # never lose the document over a shaping bug: fall back, and say so
                self._shape_log.setdefault("*", {"ok": 0, "no_tables": set(), "errors": []})["errors"].append(
                    "%s: %s" % (type(e).__name__, e))
        return self._glyphs_plain(text, lang)

    def _glyphs_shaped(self, text, lang):
        runs = script_runs(text)
        script_at = [None] * len(text)
        for s, a, b in runs:
            for i in range(a, b):
                script_at[i] = s
        segs, cur = [], None
        for i, ch in enumerate(text):
            if ch in "\r\n\u2029\u2028":
                cur = None
                continue
            if ch in _SKIP_CF or unicodedata.category(ch) == "Cf":
                if cur is not None and ch in "\u200c\u200d":     # joiners matter to the shaper
                    cur[2].append(i)
                continue
            script = script_of(ch)
            if script in ("Common", "Inherited"):
                script = script_at[i] if script_at[i] not in ("Common", "Inherited", None) else "Common"
            f, _g, _status = self.book.resolve(" " if ch == "\t" else ch, script, lang)
            if cur is not None and cur[0] is f and cur[1] == script:
                cur[2].append(i)
            else:
                cur = [f, script, [i]]
                segs.append(cur)
        out = [_Cluster() for _ in text]
        for f, script, idxs in segs:
            seg = "".join(" " if text[i] == "\t" else text[i] for i in idxs)
            otf = _ots.load_font(f.path, f.index)
            res = _ots.shape_run(otf, seg, script=None if script == "Common" else script, lang=lang,
                                 direction="rtl" if script in RTL_SCRIPTS else "ltr")
            log = self._shape_log.setdefault(script, {"ok": 0, "no_tables": set(), "errors": []})
            tags = _ots.ot_script_tags(script)
            if tags and res.script_tag not in tags and script in SHAPING_REQUIRED | MARK_POSITIONING:
                log["no_tables"].add(f.family)
            else:
                log["ok"] += 1
            ent = self._fontkey(f)
            starts = sorted({g.cluster for g in res.glyphs})
            ends = dict(zip(starts, starts[1:] + [len(seg)]))
            scale = 1000.0 / otf.upem
            for g, src in zip(res.glyphs, res.texts):
                idx = idxs[g.cluster]
                if src and not g.x_advance and all(c in "\u200c\u200d" for c in src):
                    continue    # joiner kept by the shaper as a zero-width space: nothing to draw
                if g.glyph_id == 0:
                    src = "\ufffd"
                elif not src:
                    src = "\u25cc" if g.glyph_id == otf.glyph(0x25CC) else ""
                if not ent["map"].get(g.glyph_id):
                    ent["map"][g.glyph_id] = src
                cl = out[idx]
                if cl.text is None:
                    cl.text = "".join(text[idxs[k]] for k in range(g.cluster, ends[g.cluster])
                                      if text[idxs[k]] not in _SKIP_CF)
                cl.append((ent, g.glyph_id, g.x_advance * scale, src, g.x_offset * scale, g.y_offset * scale))
        return out, set()

    def _glyphs_plain(self, text, lang):
        display, source, unshaped = shape_arabic(text)
        runs = script_runs(text)
        script_at = [None] * len(text)
        for s, a, b in runs:
            for i in range(a, b):
                script_at[i] = s
        out = []
        for i, ch in enumerate(text):
            disp = display[i]
            if disp == "" or ch in _SKIP_CF or (unicodedata.category(ch) == "Cf") or ch in "\r\n\u2029\u2028":
                out.append([])
                continue
            if ch == "\t":
                disp = " "
            script = script_of(ch)
            if script in ("Common", "Inherited"):
                script = script_at[i] if script_at[i] not in ("Common", "Inherited", None) else "Common"
            f, g, status = self.book.resolve(disp, script, lang)
            if g == 0 and disp != ch:  # presentation form missing in this font: fall back to nominal
                f, g, status = self.book.resolve(ch, script, lang)
            ent = self._fontkey(f)
            src = source[i] if g else "\ufffd"
            ent["map"].setdefault(g, src)
            out.append([(ent, g, f.width1000(g), src)])
        return out, unshaped

    def _lines(self, text, glyphs, para, size, maxw):
        """Greedy line breaking in logical order -> list of (start, end)."""
        n = len(text)
        scripts = [script_of(c) for c in text]
        lines = []
        start = 0
        width = 0.0
        last_break = None
        i = 0
        while i < n:
            w = sum(x[2] for x in glyphs[i]) * size / 1000.0
            is_space = para.classes[i] in ("WS", "S")
            if width + w > maxw and i > start and not is_space:
                if last_break is not None and last_break > start:
                    cut = last_break
                else:
                    cut = i
                lines.append((start, cut))
                start = cut
                width = sum(sum(x[2] for x in glyphs[k]) for k in range(start, i)) * size / 1000.0
                last_break = None
            width += w
            if is_space:
                last_break = i + 1
            elif scripts[i] in _CJK_BREAK and i + 1 < n and unicodedata.category(text[i + 1])[0] != "P":
                last_break = i + 1
            i += 1
        if start < n or not lines:
            lines.append((start, n))
        return lines

    def _new_page(self):
        self._pages.append([])
        return self.ph - self.margin

    def build(self):
        self._pages, self._outline, self.warnings = [], [], []
        self._shape_log = {}
        unshaped_all, scripts_seen = set(), set()
        maxw = self.pw - 2 * self.margin
        y = self._new_page()
        for kind, text, lang, base, size, bm in self.blocks:
            text = unicodedata.normalize("NFC", text)
            for s in detect_scripts(text):
                scripts_seen.add(s)
            lead = size * 1.45
            if kind == "h":
                y -= size * 0.6
            pieces = text.split("\n") if text else [""]
            first_line = True
            for piece in pieces:
                para = BidiParagraph(piece, base)
                glyphs, unshaped = self._glyphs(piece, lang)
                unshaped_all |= unshaped
                for a, b in self._lines(piece, glyphs, para, size, maxw):
                    if y - lead < self.margin:
                        y = self._new_page()
                    y -= lead
                    if first_line and bm:
                        self._outline.append((bm, len(self._pages) - 1, y + lead))
                    first_line = False
                    self._emit_line(piece, para, glyphs, a, b, size, y, lang)
            if kind == "h":
                y -= size * 0.25
        shaped_ok = {s for s, v in self._shape_log.items() if v["ok"] and not v["no_tables"]}
        if self._shaper_missing:
            self.warnings.append("OpenType shaping requested but ot_shaper.py could not be loaded; text printed "
                                 "with the unshaped path.")
        for v in self._shape_log.values():
            for e in v["errors"][:3]:
                self.warnings.append("OpenType shaping failed, text printed unshaped: %s" % e)
        for s in sorted(scripts_seen & SHAPING_REQUIRED):
            if s in shaped_ok:
                continue
            fams = sorted(self._shape_log.get(s, {}).get("no_tables", ()))
            if fams:
                self.warnings.append("%s text is printed UNSHAPED: font %s has no OpenType tables for it (no "
                                     "conjuncts, vowel-sign reordering or mark positioning). Text stays correct "
                                     "for search/copy; verify clinical meaning on screen." % (s, ", ".join(fams)))
            else:
                self.warnings.append("%s text is printed UNSHAPED: no conjuncts, vowel-sign reordering or mark "
                                     "positioning (needs an OpenType shaper such as HarfBuzz, not available in "
                                     "the standard library). Text stays correct for search/copy; verify "
                                     "clinical meaning on screen." % s)
        for s in sorted(scripts_seen & {"Syriac"} & shaped_ok):
            self.warnings.append("Syriac is shaped without the fin2 / fin3 / med2 alaph forms.")
        for s in sorted(scripts_seen & MARK_POSITIONING):
            if s in shaped_ok:
                continue
            self.warnings.append("%s combining marks are drawn without GPOS mark positioning; stacked marks "
                                 "may overlap." % s)
        if "Mongolian" in scripts_seen:
            self.warnings.append("Mongolian is printed horizontally (vertical layout not supported).")
        if unshaped_all:
            self.warnings.append("joining letters without presentation forms left unjoined: %s"
                                 % " ".join("U+%04X" % ord(c) for c in sorted(unshaped_all)))
        for (script, hk, fam), cnt in sorted(self.book.substituted.items()):
            self.warnings.append("substitution: %d %s character%s%s printed with %s (preferred %s fonts lack "
                                 "them or are not installed)" % (cnt, script, "" if cnt == 1 else "s",
                                                                 " (%s)" % hk if hk else "", fam, script))
        if self.book.missing:
            miss = sorted(self.book.missing.items())
            self.warnings.append("MISSING glyphs (printed as empty boxes, still extractable as U+FFFD): %s"
                                 % ", ".join("U+%04X %s x%d" % (ord(c), script_of(c), n) for c, n in miss[:40])
                                 + (" ..." if len(miss) > 40 else ""))
        for p, why in sorted(self.book.problems.items()):
            self.warnings.append("font skipped: %s (%s)" % (os.path.basename(p), why))
        self._footers()
        return self._serialize()

    def _footers(self):
        """Page footers; glyphs registered before the fonts (and their ToUnicode maps) are written."""
        total = len(self._pages)
        for pidx, ops in enumerate(self._pages):
            footer = "%s  %d / %d" % (APP_NAME, pidx + 1, total)
            tail = [b"BT %s %s Td" % (_fmt(self.margin).encode(), _fmt(self.margin * 0.5).encode())]
            cur = None
            for ch in footer:
                f, g, _st = self.book.resolve(ch, "Latin")
                ent = self._fontkey(f)
                ent["map"].setdefault(g, ch if g else "�")
                if ent is not cur:
                    tail.append(b"/%s 8 Tf" % ent["key"].encode())
                    cur = ent
                tail.append(b"<%04X> Tj" % g)
            tail.append(b"ET")
            ops.append(b"\n".join(tail))

    def _emit_line(self, text, para, glyphs, a, b, size, y, lang):
        if any(isinstance(glyphs[i], _Cluster) and glyphs[i] for i in range(a, b)):
            return self._emit_shaped_line(text, para, glyphs, a, b, size, y, lang)
        order = para.visual_order(a, b)
        lv = para.levels
        items = []
        for i in order:
            for ent, g, w, src in glyphs[i]:
                if lv[i] % 2 and text[i] in _MIRROR and unicodedata.mirrored(text[i]):
                    f = ent["font"]
                    mg = f.gid(_MIRROR[text[i]])
                    if mg:
                        g, w = mg, f.width1000(mg)
                        ent["map"].setdefault(g, _MIRROR[text[i]])
                items.append((ent, g, w))
        width = sum(w for _e, _g, w in items) * size / 1000.0
        x = self.pw - self.margin - width if para.para_level % 2 else self.margin
        logical = "".join(c for c in text[a:b] if c not in _SKIP_CF).rstrip()
        visual_src = "".join(glyphs[i][0][3] for i in order if glyphs[i])
        needs_actual = logical != visual_src.rstrip()
        ops = []
        props = []
        if lang:
            props.append(b"/Lang " + _pdf_utf16(lang))
        if needs_actual:
            props.append(b"/ActualText " + _pdf_utf16(logical))
        if props:
            ops.append(b"/Span << " + b" ".join(props) + b" >> BDC")
        ops.append(b"BT %s %s Td" % (_fmt(x).encode(), _fmt(y).encode()))
        cur = None
        buf = []
        for ent, g, w in items:
            if ent is not cur:
                if buf:
                    ops.append(b"<" + b"".join(b"%04X" % q for q in buf) + b"> Tj")
                    buf = []
                ops.append(b"/%s %s Tf" % (ent["key"].encode(), _fmt(size).encode()))
                cur = ent
            buf.append(g)
        if buf:
            ops.append(b"<" + b"".join(b"%04X" % q for q in buf) + b"> Tj")
        ops.append(b"ET")
        if props:
            ops.append(b"EMC")
        self._pages[-1].append(b"\n".join(ops))

    def _emit_shaped_line(self, text, para, glyphs, a, b, size, y, lang):
        """Shaped clusters in visual order, positioned with Tm where GPOS moved a glyph off the pen."""
        order = para.visual_order(a, b)
        lv = para.levels
        k = size / 1000.0
        rtl_line = bool(para.para_level % 2) or any(lv[i] % 2 for i in range(a, b))
        clusters = []
        for i in order:
            cl = glyphs[i]
            if not cl:
                continue
            items = []
            for it in cl:
                ent, g, w = it[0], it[1], it[2]
                dx, dy = (it[4], it[5]) if len(it) > 5 else (0.0, 0.0)
                if len(cl) == 1 and lv[i] % 2 and text[i] in _MIRROR and unicodedata.mirrored(text[i]):
                    f = ent["font"]
                    mg = f.gid(_MIRROR[text[i]])
                    if mg and g == f.gid(text[i]):
                        g, w = mg, f.width1000(mg)
                        ent["map"].setdefault(g, _MIRROR[text[i]])
                items.append((ent, g, w, dx, dy))
            logical_c = getattr(cl, "text", None)
            if logical_c is None:
                logical_c = cl[0][3]
            mapped = "".join(ent["map"].get(g) or "\ufffd" for ent, g, _w, _dx, _dy in items)
            clusters.append((items, logical_c, mapped))
        width = sum(w for items, _l, _m in clusters for _e, _g, w, _dx, _dy in items) * k
        x = self.pw - self.margin - width if para.para_level % 2 else self.margin
        logical = "".join(c for c in text[a:b] if c not in _SKIP_CF).rstrip()
        visual_src = "".join(m for _i, _l, m in clusters)
        line_actual = rtl_line and logical != visual_src.rstrip()
        ops, props = [], []
        if lang:
            props.append(b"/Lang " + _pdf_utf16(lang))
        if line_actual:
            props.append(b"/ActualText " + _pdf_utf16(logical))
        if props:
            ops.append(b"/Span << " + b" ".join(props) + b" >> BDC")
        ops.append(b"BT %s %s Td" % (_fmt(x).encode(), _fmt(y).encode()))
        state = {"cur": None, "buf": [], "nat_x": 0.0, "nat_y": 0.0}

        def flush():
            if state["buf"]:
                ops.append(b"<" + b"".join(b"%04X" % q for q in state["buf"]) + b"> Tj")
                state["buf"] = []

        pen = 0.0
        for items, logical_c, mapped in clusters:
            span = (not rtl_line) and logical_c and logical_c != mapped
            if span:
                flush()
                ops.append(b"/Span << /ActualText " + _pdf_utf16(logical_c) + b" >> BDC")
            for ent, g, w, dx, dy in items:
                if ent is not state["cur"]:
                    flush()
                    ops.append(b"/%s %s Tf" % (ent["key"].encode(), _fmt(size).encode()))
                    state["cur"] = ent
                gx, gy = pen + dx * k, dy * k
                if abs(gx - state["nat_x"]) > 0.005 or abs(gy - state["nat_y"]) > 0.005:
                    flush()
                    ops.append(b"1 0 0 1 %s %s Tm" % (_fmt(x + gx).encode(), _fmt(y + gy).encode()))
                    state["nat_x"], state["nat_y"] = gx, gy
                state["buf"].append(g)
                state["nat_x"] += round(ent["font"].width1000(g)) * k     # what the viewer advances (/W)
                pen += w * k
            if span:
                flush()
                ops.append(b"EMC")
        flush()
        ops.append(b"ET")
        if props:
            ops.append(b"EMC")
        self._pages[-1].append(b"\n".join(ops))

    # -- serialisation -------------------------------------------------------------------------
    def _serialize(self):
        objs = []

        def add(b):
            objs.append(b)
            return len(objs)

        def reserve():
            objs.append(None)
            return len(objs)

        def stream(extra, raw, compress=True):
            if compress:
                raw = zlib.compress(raw, 6)
                extra += b" /Filter /FlateDecode"
            return add(b"<< /Length %d%s >>\nstream\n" % (len(raw), extra) + raw + b"\nendstream")

        catalog = reserve()
        pages_root = reserve()
        font_refs = [(ent["key"], self._cid_font(ent, add, stream)) for ent in self._used.values()]
        fonts = b" ".join(b"/%s %d 0 R" % (k.encode(), o) for k, o in font_refs)
        resources = add(b"<< /ProcSet [/PDF /Text] /Font << %s >> >>" % fonts)
        page_ids = []
        for ops in self._pages:
            content = stream(b"", b"\n".join(ops) + b"\n")
            page_ids.append(add(b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %s %s] /Resources %d 0 R "
                                b"/Contents %d 0 R >>" % (pages_root, _fmt(self.pw).encode(), _fmt(self.ph).encode(),
                                                          resources, content)))
        objs[pages_root - 1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
            b" ".join(b"%d 0 R" % p for p in page_ids), len(page_ids))
        outline_ref = b""
        if self._outline:
            root = reserve()
            items = [reserve() for _ in self._outline]
            for k, (title, pidx, yy) in enumerate(self._outline):
                d = b"<< /Title %s /Parent %d 0 R /Dest [%d 0 R /XYZ 0 %s null]" % (
                    _pdf_utf16(title), root, page_ids[pidx], _fmt(yy).encode())
                if k:
                    d += b" /Prev %d 0 R" % items[k - 1]
                if k + 1 < len(items):
                    d += b" /Next %d 0 R" % items[k + 1]
                objs[items[k] - 1] = d + b" >>"
            objs[root - 1] = b"<< /Type /Outlines /First %d 0 R /Last %d 0 R /Count %d >>" % (
                items[0], items[-1], len(items))
            outline_ref = b" /Outlines %d 0 R /PageMode /UseOutlines" % root
        objs[catalog - 1] = b"<< /Type /Catalog /Pages %d 0 R%s /Lang %s >>" % (
            pages_root, outline_ref, _pdf_utf16(_pdf_name_lang(self.lang) or "en"))
        now = datetime.datetime.now(datetime.timezone.utc).strftime("D:%Y%m%d%H%M%SZ")
        info = b"<< /Title %s /Producer %s /Creator %s /CreationDate (%s)" % (
            _pdf_utf16(self.title), _pdf_utf16(APP_NAME), _pdf_utf16(APP_NAME), now.encode())
        for key, val in ((b"Author", self.author), (b"Subject", self.subject), (b"Keywords", self.keywords)):
            if val:
                info += b" /%s %s" % (key, _pdf_utf16(val))
        info_ref = add(info + b" >>")
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for k, body in enumerate(objs, 1):
            offsets.append(len(out))
            out += b"%d 0 obj\n" % k + body + b"\nendobj\n"
        xref = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
        out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
        fid = hashlib.md5(bytes(out[:4096]) + str(len(out)).encode()).hexdigest().upper().encode()
        out += b"trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R /ID [<%s> <%s>] >>\nstartxref\n%d\n%%%%EOF\n" % (
            len(objs) + 1, catalog, info_ref, fid, fid, xref)
        return bytes(out)

    @staticmethod
    def _cid_font(ent, add, stream):
        f = ent["font"]
        used = ent["map"]
        gids = sorted(used)
        tag = "".join(chr(65 + (b % 26)) for b in hashlib.md5(("%s%r" % (f.ps_name, gids)).encode()).digest()[:6])
        base = ("%s+%s" % (tag, f.ps_name)).encode("ascii", "replace")
        sub = f.subset(gids)
        ff = stream(b" /Length1 %d" % len(sub), sub)
        scale = 1000.0 / f.units_per_em
        bbox = b" ".join(str(int(round(v * scale))).encode() for v in f.bbox)
        flags = 4 | (64 if f.italic_angle else 0)
        desc = add(b"<< /Type /FontDescriptor /FontName /%s /Flags %d /FontBBox [%s] /ItalicAngle %s /Ascent %d "
                   b"/Descent %d /CapHeight %d /StemV 80 /FontFile2 %d 0 R >>"
                   % (base, flags, bbox, _fmt(f.italic_angle).encode(), int(f.ascent * scale),
                      int(f.descent * scale), int(f.cap_height * scale), ff))
        w = []
        k = 0
        while k < len(gids):
            j = k
            while j + 1 < len(gids) and gids[j + 1] == gids[j] + 1:
                j += 1
            w.append(b"%d [%s]" % (gids[k], b" ".join(b"%d" % round(f.width1000(g)) for g in gids[k:j + 1])))
            k = j + 1
        cid = add(b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /%s /CIDSystemInfo << /Registry (Adobe) "
                  b"/Ordering (Identity) /Supplement 0 >> /FontDescriptor %d 0 R /DW 1000 /W [%s] "
                  b"/CIDToGIDMap /Identity >>" % (base, desc, b" ".join(w)))
        lines = []
        for k in range(0, len(gids), 100):
            chunk = gids[k:k + 100]
            lines.append("%d beginbfchar" % len(chunk))
            for g in chunk:
                lines.append("<%04X> <%s>" % (g, (used[g] or "\ufffd").encode("utf-16-be").hex().upper()))
            lines.append("endbfchar")
        cmap = ("/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
                "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
                "/CMapName /Adobe-Identity-UCS def\n/CMapType 2 def\n1 begincodespacerange\n<0000> <FFFF>\n"
                "endcodespacerange\n%s\nendcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n"
                % "\n".join(lines))
        tu = stream(b"", cmap.encode("ascii"))
        return add(b"<< /Type /Font /Subtype /Type0 /BaseFont /%s /Encoding /Identity-H /DescendantFonts [%d 0 R] "
                   b"/ToUnicode %d 0 R >>" % (base, cid, tu))


# ======================================================================================================
# 7. PDF reader / text extraction (validation of our own output)
# ======================================================================================================

class _PdfReader:
    def __init__(self, data):
        self.data = data
        self.problems = []
        self.offsets = {}
        self.trailer = b""
        self._read_xref()

    def _read_xref(self):
        d = self.data
        m = re.search(rb"startxref\s+(\d+)\s+%%EOF\s*$", d[-1024:])
        if not m:
            self.problems.append("missing startxref / %%EOF")
            return
        pos = int(m.group(1))
        if d[pos:pos + 4] != b"xref":
            self.problems.append("startxref does not point at an xref table")
            return
        tm = d.find(b"trailer", pos)
        lines = d[pos + 4:tm].split()
        k = 0
        while k + 1 < len(lines):
            start, count = int(lines[k]), int(lines[k + 1])
            k += 2
            for j in range(count):
                off, _gen, kind = int(lines[k]), int(lines[k + 1]), lines[k + 2]
                k += 3
                if kind == b"n":
                    self.offsets[start + j] = off
        tail = d[tm:]
        self.trailer = tail[:tail.find(b"startxref")]
        sm = re.search(rb"/Size (\d+)", self.trailer)
        if not sm or int(sm.group(1)) != max(self.offsets or [0]) + 1:
            self.problems.append("trailer /Size does not match xref")
        for num, off in self.offsets.items():
            if not d.startswith(b"%d 0 obj" % num, off):
                self.problems.append("xref offset for object %d is wrong" % num)

    def obj(self, num):
        off = self.offsets[num]
        d = self.data
        start = d.index(b"obj", off) + 3
        sp = d.find(b"stream", start)
        ep = d.find(b"endobj", start)
        if 0 <= sp < ep:
            head = d[start:sp]
            length = int(re.search(rb"/Length (\d+)", head).group(1))
            s0 = sp + 6 + (2 if d[sp + 6:sp + 8] == b"\r\n" else 1)
            raw = d[s0:s0 + length]
            if b"/FlateDecode" in head:
                raw = zlib.decompress(raw)
            return head, raw
        return d[start:ep], None

    def pages(self):
        root = int(re.search(rb"/Root (\d+) 0 R", self.trailer).group(1))
        pages = int(re.search(rb"/Pages (\d+) 0 R", self.obj(root)[0]).group(1))
        head = self.obj(pages)[0]
        kids = [int(k) for k in re.findall(rb"(\d+) 0 R", re.search(rb"/Kids\s*\[(.*?)\]", head, re.S).group(1))]
        cnt = int(re.search(rb"/Count (\d+)", head).group(1))
        if cnt != len(kids):
            self.problems.append("page tree /Count mismatch")
        return [(k, self.obj(k)[0]) for k in kids]

    def decoder(self, num, cache):
        if num in cache:
            return cache[num]
        head = self.obj(num)[0]
        table = {}
        tu = re.search(rb"/ToUnicode (\d+) 0 R", head)
        if tu:
            cm = self.obj(int(tu.group(1)))[1].decode("latin-1")
            for block in re.findall(r"beginbfchar(.*?)endbfchar", cm, re.S):
                for src, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
                    table[int(src, 16)] = bytes.fromhex(dst).decode("utf-16-be")

        def dec(bs):
            return "".join(table.get(int.from_bytes(bs[k:k + 2], "big"), "\ufffd") for k in range(0, len(bs) - 1, 2))
        cache[num] = dec
        return dec


def _pdf_text_string_value(tok):
    if tok.startswith(b"<"):
        raw = bytes.fromhex(re.sub(rb"[^0-9A-Fa-f]", b"", tok[1:-1]).decode())
        return raw[2:].decode("utf-16-be") if raw[:2] == b"\xfe\xff" else raw.decode("latin-1")
    return tok[1:-1].decode("latin-1")


_TOK = re.compile(rb"<<|>>|<[0-9A-Fa-f\s]*>|/[^\s/\[\]()<>]+|\[|\]|[-+]?\d*\.?\d+|[A-Za-z'\"*]+|\([^)]*\)")


def extract_pdf_text(data, actual_text=True):
    """Text of each page of a PDF written by this module. With actual_text=True a /Span /ActualText
    replaces the glyph text it covers (logical order for RTL lines, as Acrobat-style extractors do);
    with False the raw ToUnicode glyph text is returned (visual order)."""
    r = _PdfReader(data)
    cache = {}
    pages = []
    for _num, head in r.pages():
        res = r.obj(int(re.search(rb"/Resources (\d+) 0 R", head).group(1)))[0]
        fonts = {n.decode(): int(o) for n, o in re.findall(rb"/(F\d+)\s+(\d+) 0 R", res)}
        content = r.obj(int(re.search(rb"/Contents (\d+) 0 R", head).group(1)))[1]
        out, font, stack, actual = [], None, [], []
        for m in _TOK.finditer(content):
            tok = m.group(0)
            if tok in (b"Tf",):
                font = r.decoder(fonts[stack[-2][1:].decode()], cache)
                stack = []
            elif tok == b"Tj":
                s = stack[-1]
                txt = font(bytes.fromhex(s[1:-1].decode()))
                if actual and actual[-1] is not None:
                    pass
                else:
                    out.append(txt)
                stack = []
            elif tok == b"BDC":
                joined = b" ".join(stack)
                am = re.search(rb"/ActualText\s+(<[0-9A-Fa-f]+>)", joined)
                if am and actual_text:
                    t = _pdf_text_string_value(am.group(1))
                    out.append(t)
                    actual.append(t)
                else:
                    actual.append(None)
                stack = []
            elif tok == b"EMC":
                if actual:
                    actual.pop()
                stack = []
            elif tok == b"ET":
                out.append("\n")
                stack = []
            elif tok in (b"BT", b"Td", b"TJ", b"Tm"):
                stack = []
            else:
                stack.append(tok)
        pages.append("".join(out))
    return pages


def validate_pdf(data):
    """Structural problems of a PDF produced here ([] == fine)."""
    probs = []
    if not data.startswith(b"%PDF-1.4"):
        probs.append("header is not %PDF-1.4")
    if not data.rstrip().endswith(b"%%EOF"):
        probs.append("missing %%EOF")
    try:
        r = _PdfReader(data)
        probs += r.problems
        if not r.problems:
            if not r.pages():
                probs.append("no pages")
            probs += r.problems
            for num in r.offsets:
                r.obj(num)
            extract_pdf_text(data)
    except Exception as e:
        probs.append("parse error: %s: %s" % (type(e).__name__, e))
    return probs


# ======================================================================================================
# 8. Language catalogue (endonyms: each language's name for itself) and the sample document
# ======================================================================================================

LANGUAGES = [  # (BCP 47 tag, English name, endonym)
    ("en", "English", "English"), ("fr", "French", "Français"), ("de", "German", "Deutsch"),
    ("es", "Spanish", "Español"), ("pt", "Portuguese", "Português"), ("it", "Italian", "Italiano"),
    ("nl", "Dutch", "Nederlands"), ("sv", "Swedish", "Svenska"), ("nb", "Norwegian Bokmål", "Norsk bokmål"),
    ("da", "Danish", "Dansk"), ("fi", "Finnish", "Suomi"), ("is", "Icelandic", "Íslenska"),
    ("pl", "Polish", "Polski"), ("cs", "Czech", "Čeština"), ("sk", "Slovak", "Slovenčina"),
    ("sl", "Slovenian", "Slovenščina"), ("hr", "Croatian", "Hrvatski"), ("bs", "Bosnian", "Bosanski"),
    ("ro", "Romanian", "Română"), ("hu", "Hungarian", "Magyar"), ("et", "Estonian", "Eesti"),
    ("lv", "Latvian", "Latviešu"), ("lt", "Lithuanian", "Lietuvių"), ("tr", "Turkish", "Türkçe"),
    ("az", "Azerbaijani", "Azərbaycanca"), ("uz", "Uzbek", "Oʻzbekcha"), ("tk", "Turkmen", "Türkmençe"),
    ("sq", "Albanian", "Shqip"), ("mt", "Maltese", "Malti"), ("ga", "Irish", "Gaeilge"),
    ("cy", "Welsh", "Cymraeg"), ("gd", "Scottish Gaelic", "Gàidhlig"), ("eu", "Basque", "Euskara"),
    ("ca", "Catalan", "Català"), ("gl", "Galician", "Galego"), ("af", "Afrikaans", "Afrikaans"),
    ("sw", "Swahili", "Kiswahili"), ("yo", "Yoruba", "Yorùbá"), ("ig", "Igbo", "Igbo"), ("ha", "Hausa", "Hausa"),
    ("zu", "Zulu", "isiZulu"), ("xh", "Xhosa", "isiXhosa"), ("so", "Somali", "Soomaali"),
    ("rw", "Kinyarwanda", "Ikinyarwanda"), ("mg", "Malagasy", "Malagasy"), ("id", "Indonesian", "Bahasa Indonesia"),
    ("ms", "Malay", "Bahasa Melayu"), ("fil", "Filipino", "Filipino"), ("vi", "Vietnamese", "Tiếng Việt"),
    ("haw", "Hawaiian", "ʻŌlelo Hawaiʻi"), ("mi", "Māori", "Te reo Māori"), ("sm", "Samoan", "Gagana Sāmoa"),
    ("to", "Tongan", "Lea faka-Tonga"), ("eo", "Esperanto", "Esperanto"), ("la", "Latin", "Lingua Latina"),
    ("lb", "Luxembourgish", "Lëtzebuergesch"), ("fo", "Faroese", "Føroyskt"), ("qu", "Quechua", "Runa Simi"),
    ("gn", "Guarani", "Avañeʼẽ"), ("ht", "Haitian Creole", "Kreyòl ayisyen"), ("om", "Oromo", "Afaan Oromoo"),
    ("wo", "Wolof", "Wolof"), ("ku", "Kurdish (Kurmanji)", "Kurdî"), ("ny", "Chichewa", "Chichewa"),
    ("sn", "Shona", "chiShona"), ("st", "Sesotho", "Sesotho"), ("tn", "Setswana", "Setswana"),
    ("ln", "Lingala", "Lingála"), ("jv", "Javanese", "Basa Jawa"), ("su", "Sundanese", "Basa Sunda"),
    ("fy", "West Frisian", "Frysk"), ("br", "Breton", "Brezhoneg"), ("co", "Corsican", "Corsu"),
    ("rm", "Romansh", "Rumantsch"), ("se", "Northern Sami", "Davvisámegiella"), ("kl", "Greenlandic", "Kalaallisut"),
    ("ru", "Russian", "Русский"), ("uk", "Ukrainian", "Українська"), ("be", "Belarusian", "Беларуская"),
    ("bg", "Bulgarian", "Български"), ("sr", "Serbian", "Српски"), ("mk", "Macedonian", "Македонски"),
    ("kk", "Kazakh", "Қазақ тілі"), ("ky", "Kyrgyz", "Кыргызча"), ("tg", "Tajik", "Тоҷикӣ"),
    ("mn", "Mongolian (Cyrillic)", "Монгол"), ("tt", "Tatar", "Татарча"), ("ba", "Bashkir", "Башҡортса"),
    ("el", "Greek", "Ελληνικά"), ("hy", "Armenian", "Հայերեն"), ("ka", "Georgian", "ქართული"),
    ("he", "Hebrew", "עברית"), ("yi", "Yiddish", "ייִדיש"),
    ("ar", "Arabic", "العربية"), ("fa", "Persian", "فارسی"), ("ur", "Urdu", "اردو"), ("ps", "Pashto", "پښتو"),
    ("sd", "Sindhi", "سنڌي"), ("ug", "Uyghur", "ئۇيغۇرچە"), ("ckb", "Central Kurdish", "کوردی"),
    ("syr", "Syriac", "ܣܘܪܝܝܐ"), ("dv", "Dhivehi", "ދިވެހި"), ("nqo", "N'Ko", "ߒߞߏ"),
    ("hi", "Hindi", "हिन्दी"), ("mr", "Marathi", "मराठी"), ("ne", "Nepali", "नेपाली"), ("sa", "Sanskrit", "संस्कृतम्"),
    ("bn", "Bengali", "বাংলা"), ("as", "Assamese", "অসমীয়া"), ("pa", "Punjabi", "ਪੰਜਾਬੀ"),
    ("gu", "Gujarati", "ગુજરાતી"), ("or", "Odia", "ଓଡ଼ିଆ"), ("ta", "Tamil", "தமிழ்"), ("te", "Telugu", "తెలుగు"),
    ("kn", "Kannada", "ಕನ್ನಡ"), ("ml", "Malayalam", "മലയാളം"), ("si", "Sinhala", "සිංහල"),
    ("sat", "Santali", "ᱥᱟᱱᱛᱟᱲᱤ"),
    ("th", "Thai", "ไทย"), ("lo", "Lao", "ລາວ"), ("km", "Khmer", "ខ្មែរ"), ("my", "Burmese", "မြန်မာ"),
    ("bo", "Tibetan", "བོད་ཡིག"), ("dz", "Dzongkha", "རྫོང་ཁ"),
    ("am", "Amharic", "አማርኛ"), ("ti", "Tigrinya", "ትግርኛ"),
    ("zh-Hans", "Chinese (Simplified)", "简体中文"), ("zh-Hant", "Chinese (Traditional)", "繁體中文"),
    ("yue", "Cantonese", "粵語"), ("ja", "Japanese", "日本語"), ("ko", "Korean", "한국어"),
    ("chr", "Cherokee", "ᏣᎳᎩ"), ("iu", "Inuktitut", "ᐃᓄᒃᑎᑐᑦ"), ("mn-Mong", "Mongolian (traditional)", "ᠮᠣᠩᠭᠣᠯ"),
    ("zgh", "Standard Moroccan Tamazight", "ⵜⴰⵎⴰⵣⵉⵖⵜ"), ("vai", "Vai", "ꕙꔤ"), ("tdd", "Tai Nüa", "ᥖᥭᥰ ᥖᥬᥲ ᥑᥨᥒᥰ"),
]

# Hand-checked bidi / shaping specimens: (logical text, base, expected visual string WITHOUT shaping)
BIDI_SPECIMENS = [
    ("שלום", None, "םולש"),
    ("car is מכונית in Hebrew", None, "car is תינוכמ in Hebrew"),
    ("אבג 123 דהו", None, "והד 123 גבא"),
    ("مرحبا BP 120/80", None, "BP 120/80 ابحرم"),
    ("العمر 45 سنة", None, "ةنس 45 رمعلا"),
    ("אבג (abc) דהו", None, "והד (abc) גבא"),
    ("Patient: מרים כהן, age 34", None, "Patient: ןהכ םירמ, age 34"),
    ("x \u2067אב 12\u2069 y", None, "x 12 בא y"),
]


def language_sample_pdf(path=None, fonts=None, shaping=False):
    """Build the 100+ language specimen PDF -> (bytes, warnings). shaping=False keeps the original
    unshaped specimen (presentation-form Arabic, honest UNSHAPED warnings); shaping=True shapes every
    script with ot_shaper (OpenType GSUB / GPOS)."""
    pdf = PolyglotPdf("Stark Covenant — language specimen (%d languages)" % len(LANGUAGES),
                      author=APP_NAME, subject="Script coverage, bidi and shaping specimen",
                      keywords="multilingual, bidi, UAX 9, Arabic shaping, CID fonts", lang="en", fonts=fonts,
                      shaping=shaping)
    pdf.heading("Stark Covenant — %d languages, each written in its own name (endonym)" % len(LANGUAGES),
                bookmark="Languages")
    for code, english, endo in LANGUAGES:
        pdf.paragraph("\u2068%s\u2069  —  %s · %s · %s" % (endo, english, code, dominant_script(endo)),
                      lang=code, base="L", bookmark="%s — %s" % (endo, english))
    pdf.heading("Bidirectional text (UAX #9) specimens", bookmark="Bidi specimens")
    for text, base, _exp in BIDI_SPECIMENS:
        pdf.paragraph(text, base=base)
    pdf.heading("Arabic shaping: isolated / initial / medial / final, lam-alef", bookmark="Arabic shaping")
    for text in ("ب بب ببب", "لا للا سلام الإسلام لأن لآ", "مريم أحمد · 1980-03-14 · ضغط الدم 120/80",
                 "پاکستان · اردو · کراچی", "فارسی: بیمار ۴۵ ساله"):
        pdf.paragraph(text, lang="ar")
    data = pdf.build()
    if path:
        with open(path, "wb") as f:
            f.write(data)
    return data, pdf.warnings


# ======================================================================================================
# 9. UI strings (i18n) - no fake translations
# ======================================================================================================
# Every entry: key -> {lang: (text, status)}. status: "source" (English original), "verified" (registered by a
# named human reviewer), "unverified" (AI / machine authored - shown only on request, never for clinical keys).
# Keys starting with "clinical." are clinical labels: only "source" or "verified" text is ever returned.

UI_STRINGS = {
    "app.name": {"en": ("Stark Covenant", "source")},
    "action.save": {"en": ("Save", "source"), "fr": ("Enregistrer", "unverified"), "de": ("Speichern", "unverified"),
                    "es": ("Guardar", "unverified")},
    "action.print": {"en": ("Print", "source"), "fr": ("Imprimer", "unverified"), "de": ("Drucken", "unverified"),
                     "es": ("Imprimir", "unverified")},
    "action.cancel": {"en": ("Cancel", "source"), "fr": ("Annuler", "unverified"), "de": ("Abbrechen", "unverified"),
                      "es": ("Cancelar", "unverified")},
    "action.search": {"en": ("Search", "source"), "fr": ("Rechercher", "unverified"), "de": ("Suchen", "unverified"),
                      "es": ("Buscar", "unverified")},
    "ui.language": {"en": ("Language", "source"), "fr": ("Langue", "unverified"), "de": ("Sprache", "unverified"),
                    "es": ("Idioma", "unverified")},
    "ui.unverified_notice": {"en": ("Interface text in this language has not been reviewed by a qualified "
                                    "translator. Clinical labels stay in English.", "source")},
    "clinical.allergies": {"en": ("Allergies", "source")},
    "clinical.medications": {"en": ("Current medications", "source")},
    "clinical.diagnoses": {"en": ("Diagnoses", "source")},
    "clinical.blood_pressure": {"en": ("Blood pressure", "source")},
    "clinical.heart_rate": {"en": ("Heart rate", "source")},
    "clinical.temperature": {"en": ("Body temperature", "source")},
    "clinical.date_of_birth": {"en": ("Date of birth", "source")},
    "clinical.patient_name": {"en": ("Patient name", "source")},
    "clinical.dose": {"en": ("Dose", "source")},
}


def register_translation(key, lang, text, reviewer, qualification):
    """Record a human-reviewed translation. reviewer + qualification are mandatory and kept with the
    string so the provenance of every clinical label is auditable."""
    if not reviewer or not qualification:
        raise ValueError("a verified translation needs a named reviewer and their qualification")
    if key not in UI_STRINGS:
        raise KeyError(key)
    UI_STRINGS[key][lang] = (text, "verified", reviewer, qualification)


def t_info(key, lang="en", allow_unverified=False):
    """-> (text, lang_used, status). Falls back to English. Clinical keys never use unverified text."""
    entry = UI_STRINGS.get(key)
    if entry is None:
        raise KeyError(key)
    cands = [lang]
    if lang and "-" in lang:
        cands.append(lang.split("-")[0])
    for la in cands:
        val = entry.get(la)
        if not val:
            continue
        status = val[1]
        if status in ("source", "verified"):
            return val[0], la, status
        if status == "unverified" and allow_unverified and not key.startswith("clinical."):
            return val[0], la, status
    return entry["en"][0], "en", "source"


def t(key, lang="en", allow_unverified=False):
    return t_info(key, lang, allow_unverified)[0]


# ======================================================================================================
# CLI
# ======================================================================================================

def _main(argv):
    import argparse
    ap = argparse.ArgumentParser(description="Stark Covenant polyglot engine")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample", help="write the 100+ language specimen PDF")
    s.add_argument("out")
    s.add_argument("--shaped", action="store_true", help="shape with ot_shaper (OpenType GSUB/GPOS)")
    d = sub.add_parser("display", help="print the visual (bidi + shaped) form of text")
    d.add_argument("text")
    d.add_argument("--base", choices=["L", "R"])
    a = ap.parse_args(argv)
    if a.cmd == "sample":
        data, warns = language_sample_pdf(a.out, shaping=a.shaped)
        print("wrote %s (%d bytes, %d languages)" % (a.out, len(data), len(LANGUAGES)))
        for w in warns:
            print("WARNING:", w)
        probs = validate_pdf(data)
        print("validate:", probs or "ok")
        return 1 if probs else 0
    if a.cmd == "display":
        sys.stdout.reconfigure(encoding="utf-8")
        print(get_display(a.text, a.base))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
