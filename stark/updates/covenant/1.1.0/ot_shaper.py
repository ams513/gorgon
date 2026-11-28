"""Stark Covenant - ot_shaper: an OpenType text shaper (GSUB / GPOS) in pure standard-library Python 3.10+.

Why: polyglot_engine writes PDFs itself, and nothing in the standard library shapes complex scripts, so
Indic, Sinhala, Khmer, Myanmar and Tibetan text used to be printed UNSHAPED. This module is a small
HarfBuzz-style engine that reads the font's own OpenType tables and applies them the way Windows does.

What it reads
  * sfnt / TTC directories; cmap (formats 4 and 12), maxp, hhea/hmtx, head, GDEF (glyph classes, mark
    attachment classes, mark glyph sets), GSUB and GPOS: script / language-system / feature / lookup lists,
    coverage tables (formats 1, 2) and class definitions (formats 1, 2).
  * GSUB lookup types 1 single, 2 multiple, 3 alternate, 4 ligature, 5 context, 6 chaining context
    (formats 1, 2, 3 each), 7 extension, 8 reverse chaining single.
  * GPOS lookup types 1 single, 2 pair (formats 1, 2), 3 cursive, 4 mark-to-base, 5 mark-to-ligature,
    6 mark-to-mark, 7 context, 8 chaining context, 9 extension. Lookup flags (ignore base / ligature /
    marks, mark attachment type, mark filtering sets), ZWJ / ZWNJ handling and per-syllable matching follow
    HarfBuzz's semantics (which match Uniscribe / DirectWrite).

Shaping models (chosen from the script)
  * "indic": the Microsoft "Developing OpenType Fonts for <script>" model, v2 ('dev2', 'bng2', 'gur2',
    'gjr2', 'ory2', 'tml2', 'tel2', 'knd2', 'mlm2') and old-spec ('deva' ...) tags, plus Sinhala:
    syllable clustering, split-matra decomposition, base consonant finding (consonant positions from the
    font's blwf / pstf / pref / vatu lookups), reph detection (implicit, explicit with ZWJ, logical repha),
    pre-base matra reordering, ZWJ / ZWNJ effects, the basic features one stage each in the order
    locl, ccmp, nukt, akhn, rphf, rkrf, pref, blwf, abvf, half, pstf, vatu, cjct, the final reordering
    (matra / reph / pre-base-reordering consonant placement) and then init, pres, abvs, blws, psts, haln
    (+ calt, clig, liga, rclt) and GPOS kern, dist, abvm, blwm, mark, mkmk.
  * "khmer", "myanmar": the dedicated Khmer / Myanmar reordering (coeng-ro and pre-base vowels, kinzi,
    medial ra, pre-base vowel e) with pref / blwf / abvf / pstf / cfar / rphf stages.
  * "use": a Universal Shaping Engine subset (Tibetan, and other scripts that need no reordering): the USE
    feature stages without its cluster grammar.
  * "arabic": joining analysis (Arabic, Syriac, N'Ko, Mongolian) -> isol / fina / medi / init applied with
    the font's GSUB (not presentation-form code points), then rlig, calt, liga and GPOS mark / mkmk for
    harakat.
  * "default": ccmp, locl, rlig, calt, clig, liga, rclt + kern, mark, mkmk, curs, dist (Latin, Greek,
    Cyrillic, Hebrew, Thai, Lao, ...). Thai / Lao SARA AM is decomposed as Windows does.

Output: shape(font, text, ...) -> list of ShapedGlyph(glyph_id, x_advance, x_offset, y_offset, cluster)
in font units, in visual (left-to-right drawing) order; cluster is the index in `text` of the first
character of the cluster. shape_run() also returns, per glyph, the characters it was formed from.

Verified against Windows (tests/test_ot_shaper.py, Uniscribe through ctypes, glyph ids + advances + offsets):
  real words in all 9 Indic scripts + Sinhala (Nirmala UI), Thai / Lao / Khmer (Leelawadee UI), Myanmar
  (Myanmar Text), Tibetan (Microsoft Himalaya), Arabic / Hebrew / Latin (Arial, Segoe UI) are identical,
  except Telugu SHRII (U+0C36 0C4D 0C30 0C40) in Nirmala UI. Random syllable strings (300 per script, many
  not real words) match 94-98 %; differences involve nukta on post-base consonants, bindu / accents
  directly after a virama, Assamese ra-phala and Gurmukhi nukta+post-base forms.

Honest limits
  * Windows quirks are followed where measured (Hebrew presentation-form composition, Gurmukhi IRI / URA as
    non-bases, below-base search past unformed forms in every Indic script); untested fonts may differ.
  * No hinting / device tables / variable fonts (FeatureVariations, 'rvrn' are ignored); AAT / Graphite
    tables are not read. Vertical layout is not supported (Mongolian is shaped horizontally).
  * The USE subset does not implement the USE cluster grammar or its reordering; Tibetan does not need
    it, other USE scripts (Balinese, Javanese, ...) are only approximated.
  * Syriac fin2 / fin3 / med2 and Arabic 'stch' are not applied; Arabic mark reordering (UTR #53, the
    "modifier combining marks") is not done.
  * Emoji sequences, variation selectors (cmap format 14) and the Unicode normaliser's font-dependent
    recomposition are not implemented beyond Indic split matras.
"""
from __future__ import annotations

import os
import re
import struct
import sys
import unicodedata
from collections import namedtuple

__all__ = ["Font", "load_font", "shape", "shape_run", "ShapedGlyph", "ShapeResult", "shaping_model",
           "ot_script_tags", "MODEL_OF_SCRIPT"]

ShapedGlyph = namedtuple("ShapedGlyph", "glyph_id x_advance x_offset y_offset cluster")
ShapeResult = namedtuple("ShapeResult", "glyphs texts model script_tag")


# ======================================================================================================
# 1. Binary helpers and table parsing
# ======================================================================================================

def _u16(d, o):
    return (d[o] << 8) | d[o + 1]


def _s16(d, o):
    v = (d[o] << 8) | d[o + 1]
    return v - 0x10000 if v & 0x8000 else v


def _u32(d, o):
    return (d[o] << 24) | (d[o + 1] << 16) | (d[o + 2] << 8) | d[o + 3]


def _tag(d, o):
    return d[o:o + 4].decode("latin-1")


class Font:
    """One face of a TrueType / OpenType file (TTC index supported)."""

    def __init__(self, path=None, index=0, data=None):
        if data is None:
            with open(path, "rb") as f:
                data = f.read()
        self.path, self.index, self.data = path, index, data
        d = data
        base = 0
        if d[:4] == b"ttcf":
            n = _u32(d, 8)
            if index >= n:
                raise ValueError("TTC index out of range")
            base = _u32(d, 12 + 4 * index)
        num = _u16(d, base + 4)
        self.tables = {}
        for k in range(num):
            rec = base + 12 + 16 * k
            self.tables[_tag(d, rec)] = (_u32(d, rec + 8), _u32(d, rec + 12))
        self.upem = _u16(d, self.tables["head"][0] + 18)
        self.num_glyphs = _u16(d, self.tables["maxp"][0] + 4)
        n_hm = _u16(d, self.tables["hhea"][0] + 34)
        hm = self.tables["hmtx"][0]
        adv = [_u16(d, hm + 4 * k) for k in range(n_hm)]
        if n_hm < self.num_glyphs:
            adv += [adv[-1] if adv else 0] * (self.num_glyphs - n_hm)
        self.advances = adv
        self.cmap = self._parse_cmap()
        self._cov_cache, self._cd_cache = {}, {}
        self.glyph_class, self.mark_class, self.mark_sets, self.has_glyph_classes = {}, {}, [], False
        self._parse_gdef()
        self.gsub = _Layout(self, "GSUB") if "GSUB" in self.tables else None
        self.gpos = _Layout(self, "GPOS") if "GPOS" in self.tables else None

    # -- cmap ------------------------------------------------------------------------------------
    def _parse_cmap(self):
        if "cmap" not in self.tables:
            return {}
        d = self.data
        t = self.tables["cmap"][0]
        n = _u16(d, t + 2)
        subs = {}
        for k in range(n):
            pid, eid, off = _u16(d, t + 4 + 8 * k), _u16(d, t + 6 + 8 * k), _u32(d, t + 8 + 8 * k)
            subs[(pid, eid, _u16(d, t + off))] = t + off
        for key in ((3, 10, 12), (0, 6, 12), (0, 4, 12), (3, 1, 4), (0, 3, 4), (0, 1, 4), (0, 0, 4), (3, 0, 4)):
            if key in subs:
                o = subs[key]
                return self._cmap12(d, o) if key[2] == 12 else self._cmap4(d, o, key[:2] == (3, 0))
        return {}

    @staticmethod
    def _cmap4(d, o, symbol):
        seg = _u16(d, o + 6) // 2
        cmap = {}
        ends, starts, deltas, ro_pos = o + 14, o + 16 + 2 * seg, o + 16 + 4 * seg, o + 16 + 6 * seg
        for i in range(seg):
            e, s, dl, ro = _u16(d, ends + 2 * i), _u16(d, starts + 2 * i), _u16(d, deltas + 2 * i), _u16(d, ro_pos + 2 * i)
            if s == 0xFFFF:
                continue
            for c in range(s, e + 1):
                if ro == 0:
                    g = (c + dl) & 0xFFFF
                else:
                    gp = ro_pos + 2 * i + ro + 2 * (c - s)
                    g = _u16(d, gp) if gp + 1 < len(d) else 0
                    if g:
                        g = (g + dl) & 0xFFFF
                if g:
                    cmap[c] = g
                    if symbol and 0xF000 <= c <= 0xF0FF:
                        cmap.setdefault(c - 0xF000, g)
        return cmap

    @staticmethod
    def _cmap12(d, o):
        n = _u32(d, o + 12)
        cmap = {}
        p = o + 16
        for _ in range(n):
            s, e, g = _u32(d, p), _u32(d, p + 4), _u32(d, p + 8)
            p += 12
            e = min(e, s + 0x30000)
            for c in range(s, e + 1):
                if g + (c - s):
                    cmap[c] = g + (c - s)
        return cmap

    def glyph(self, cp):
        g = self.cmap.get(cp, 0)
        return g if g < self.num_glyphs else 0

    def advance(self, g):
        return self.advances[g] if 0 <= g < len(self.advances) else 0

    # -- shared sub-tables -------------------------------------------------------------------------
    def coverage(self, o):
        """Coverage table at absolute offset -> {glyph: coverage index}."""
        hit = self._cov_cache.get(o)
        if hit is not None:
            return hit
        d = self.data
        fmt = _u16(d, o)
        r = {}
        if fmt == 1:
            for k in range(_u16(d, o + 2)):
                r[_u16(d, o + 4 + 2 * k)] = k
        elif fmt == 2:
            for k in range(_u16(d, o + 2)):
                p = o + 4 + 6 * k
                s, e, si = _u16(d, p), _u16(d, p + 2), _u16(d, p + 4)
                for g in range(s, e + 1):
                    r[g] = si + g - s
        self._cov_cache[o] = r
        return r

    def classdef(self, o):
        """Class definition table at absolute offset -> {glyph: class} (class 0 omitted)."""
        hit = self._cd_cache.get(o)
        if hit is not None:
            return hit
        d = self.data
        fmt = _u16(d, o)
        r = {}
        if fmt == 1:
            start = _u16(d, o + 2)
            for k in range(_u16(d, o + 4)):
                c = _u16(d, o + 6 + 2 * k)
                if c:
                    r[start + k] = c
        elif fmt == 2:
            for k in range(_u16(d, o + 2)):
                p = o + 4 + 6 * k
                s, e, c = _u16(d, p), _u16(d, p + 2), _u16(d, p + 4)
                if c:
                    for g in range(s, e + 1):
                        r[g] = c
        self._cd_cache[o] = r
        return r

    def _parse_gdef(self):
        if "GDEF" not in self.tables:
            return
        d = self.data
        o = self.tables["GDEF"][0]
        minor = _u16(d, o + 2)
        gc, mac = _u16(d, o + 4), _u16(d, o + 10)
        if gc:
            self.glyph_class = self.classdef(o + gc)
            self.has_glyph_classes = True
        if mac:
            self.mark_class = self.classdef(o + mac)
        if minor >= 2 and _u16(d, o + 12):
            ms = o + _u16(d, o + 12)
            self.mark_sets = [frozenset(self.coverage(ms + _u32(d, ms + 4 + 4 * k))) for k in range(_u16(d, ms + 2))]


class _Lookup:
    __slots__ = ("type", "flag", "subs", "mset", "index")


def _anchor(d, o):
    return (_s16(d, o + 2), _s16(d, o + 4))


def _value(d, p, vf):
    """ValueRecord -> (xPlacement, yPlacement, xAdvance, yAdvance), device tables ignored."""
    v = [0, 0, 0, 0]
    for bit in range(8):
        if vf & (1 << bit):
            if bit < 4:
                v[bit] = _s16(d, p)
            p += 2
    return tuple(v)


def _vsize(vf):
    return 2 * bin(vf & 0xFF).count("1")


class _Layout:
    """GSUB or GPOS: script / language / feature / lookup lists with lazily parsed lookups."""

    def __init__(self, font, tag):
        self.font, self.tag = font, tag
        self.is_gpos = tag == "GPOS"
        d = font.data
        o = font.tables[tag][0]
        sl, fl, ll = _u16(d, o + 4), _u16(d, o + 6), _u16(d, o + 8)
        self.scripts = {}
        if sl:
            p = o + sl
            for k in range(_u16(d, p)):
                stag, so = _tag(d, p + 2 + 6 * k), p + _u16(d, p + 6 + 6 * k)
                langs = {}
                if _u16(d, so):
                    langs[None] = self._langsys(so + _u16(d, so))
                for j in range(_u16(d, so + 2)):
                    langs[_tag(d, so + 4 + 6 * j)] = self._langsys(so + _u16(d, so + 8 + 6 * j))
                self.scripts[stag] = langs
        self.features = []
        if fl:
            p = o + fl
            for k in range(_u16(d, p)):
                ftag, fo = _tag(d, p + 2 + 6 * k), p + _u16(d, p + 6 + 6 * k)
                self.features.append((ftag, [_u16(d, fo + 4 + 2 * j) for j in range(_u16(d, fo + 2))]))
        self.lookup_offsets = []
        if ll:
            p = o + ll
            self.lookup_offsets = [p + _u16(d, p + 2 + 2 * k) for k in range(_u16(d, p))]
        self._lookups = {}

    def _langsys(self, lo):
        d = self.font.data
        req = _u16(d, lo + 2)
        return (None if req == 0xFFFF else req, [_u16(d, lo + 6 + 2 * k) for k in range(_u16(d, lo + 4))])

    def select(self, script_tags, lang_tag=None):
        """-> (chosen script tag or None, {feature tag: sorted lookup indices})."""
        chosen = None
        for t in list(script_tags) + ["DFLT", "dflt", "latn"]:
            if t in self.scripts:
                chosen = t
                break
        if chosen is None:
            return None, {}
        langs = self.scripts[chosen]
        ls = langs.get(lang_tag) if lang_tag else None
        if ls is None:
            ls = langs.get(None)
        if ls is None:
            return chosen, {}
        req, idx = ls
        fmap = {}
        for fi in ([req] if req is not None else []) + idx:
            if fi < len(self.features):
                ftag, lookups = self.features[fi]
                if fi == req:
                    ftag = ftag  # required feature: applied like any other listed feature
                fmap.setdefault(ftag, set()).update(lookups)
        return chosen, {k: sorted(v) for k, v in fmap.items()}

    def lookup(self, idx):
        L = self._lookups.get(idx)
        if L is not None:
            return L
        L = _Lookup()
        L.index = idx
        d = self.font.data
        if idx >= len(self.lookup_offsets):
            L.type, L.flag, L.subs, L.mset = 0, 0, [], None
            self._lookups[idx] = L
            return L
        o = self.lookup_offsets[idx]
        lt, flag, n = _u16(d, o), _u16(d, o + 2), _u16(d, o + 4)
        L.flag = flag
        L.mset = None
        if flag & 0x10:
            msi = _u16(d, o + 6 + 2 * n)
            L.mset = self.font.mark_sets[msi] if msi < len(self.font.mark_sets) else frozenset()
        ext = 9 if self.is_gpos else 7
        subs = []
        real = lt
        for k in range(n):
            so = o + _u16(d, o + 6 + 2 * k)
            t = lt
            if lt == ext:
                t = _u16(d, so + 2)
                so = so + _u32(d, so + 4)
            real = t
            try:
                sub = self._gpos_sub(t, so) if self.is_gpos else self._gsub_sub(t, so)
            except (IndexError, struct.error):
                sub = None
            if sub is not None:
                subs.append(sub)
        L.type, L.subs = real, subs
        self._lookups[idx] = L
        return L

    # -- GSUB subtables ----------------------------------------------------------------------------
    def _gsub_sub(self, t, o):
        d, C = self.font.data, self.font.coverage
        fmt = _u16(d, o)
        if t == 1:
            cov = C(o + _u16(d, o + 2))
            if fmt == 1:
                delta = _u16(d, o + 4)
                return (1, {g: (g + delta) & 0xFFFF for g in cov})
            n = _u16(d, o + 4)
            return (1, {g: _u16(d, o + 6 + 2 * i) for g, i in cov.items() if i < n})
        if t in (2, 3):
            cov = C(o + _u16(d, o + 2))
            n = _u16(d, o + 4)
            seqs = []
            for k in range(n):
                so = o + _u16(d, o + 6 + 2 * k)
                seqs.append(tuple(_u16(d, so + 2 + 2 * j) for j in range(_u16(d, so))))
            return (t, {g: seqs[i] for g, i in cov.items() if i < n})
        if t == 4:
            cov = C(o + _u16(d, o + 2))
            n = _u16(d, o + 4)
            sets = []
            for k in range(n):
                so = o + _u16(d, o + 6 + 2 * k)
                ligs = []
                for j in range(_u16(d, so)):
                    lo = so + _u16(d, so + 2 + 2 * j)
                    cc = _u16(d, lo + 2)
                    ligs.append((_u16(d, lo), tuple(_u16(d, lo + 4 + 2 * q) for q in range(max(cc - 1, 0)))))
                sets.append(ligs)
            return (4, {g: sets[i] for g, i in cov.items() if i < n})
        if t == 5:
            return self._context(o, False)
        if t == 6:
            return self._context(o, True)
        if t == 8:
            cov = C(o + _u16(d, o + 2))
            nb = _u16(d, o + 4)
            back = [C(o + _u16(d, o + 6 + 2 * k)) for k in range(nb)]
            p = o + 6 + 2 * nb
            nl = _u16(d, p)
            look = [C(o + _u16(d, p + 2 + 2 * k)) for k in range(nl)]
            p += 2 + 2 * nl
            subst = [_u16(d, p + 2 + 2 * k) for k in range(_u16(d, p))]
            return (8, cov, back, look, subst)
        return None

    # -- (chained) context, shared ---------------------------------------------------------------
    def _rule(self, ro, chain):
        d = self.font.data
        if chain:
            p = ro
            nb = _u16(d, p)
            back = tuple(_u16(d, p + 2 + 2 * k) for k in range(nb))
            p += 2 + 2 * nb
            ni = max(_u16(d, p), 1)
            inp = tuple(_u16(d, p + 2 + 2 * k) for k in range(ni - 1))
            p += 2 + 2 * (ni - 1)
            nl = _u16(d, p)
            look = tuple(_u16(d, p + 2 + 2 * k) for k in range(nl))
            p += 2 + 2 * nl
            recs = tuple((_u16(d, p + 2 + 4 * k), _u16(d, p + 4 + 4 * k)) for k in range(_u16(d, p)))
            return (back, inp, look, recs)
        ni, nr = max(_u16(d, ro), 1), _u16(d, ro + 2)
        inp = tuple(_u16(d, ro + 4 + 2 * k) for k in range(ni - 1))
        p = ro + 4 + 2 * (ni - 1)
        return ((), inp, (), tuple((_u16(d, p + 4 * k), _u16(d, p + 2 + 4 * k)) for k in range(nr)))

    def _rulesets(self, o, base, n, chain):
        d = self.font.data
        sets = []
        for k in range(n):
            off = _u16(d, base + 2 * k)
            rules = []
            if off:
                so = o + off
                for j in range(_u16(d, so)):
                    rules.append(self._rule(so + _u16(d, so + 2 + 2 * j), chain))
            sets.append(rules)
        return sets

    def _context(self, o, chain):
        d, C, CD = self.font.data, self.font.coverage, self.font.classdef
        fmt = _u16(d, o)
        if fmt == 1:
            cov = C(o + _u16(d, o + 2))
            return ("c1", cov, self._rulesets(o, o + 6, _u16(d, o + 4), chain))
        if fmt == 2:
            cov = C(o + _u16(d, o + 2))
            if chain:
                bo, io, lo = _u16(d, o + 4), _u16(d, o + 6), _u16(d, o + 8)
                bcd = CD(o + bo) if bo else {}
                icd = CD(o + io) if io else {}
                lcd = CD(o + lo) if lo else {}
                n, base = _u16(d, o + 10), o + 12
            else:
                io = _u16(d, o + 4)
                icd = CD(o + io) if io else {}
                bcd = lcd = {}
                n, base = _u16(d, o + 6), o + 8
            return ("c2", cov, bcd, icd, lcd, self._rulesets(o, base, n, chain))
        if fmt == 3:
            if chain:
                p = o + 2
                nb = _u16(d, p)
                back = [C(o + _u16(d, p + 2 + 2 * k)) for k in range(nb)]
                p += 2 + 2 * nb
                ni = _u16(d, p)
                inp = [C(o + _u16(d, p + 2 + 2 * k)) for k in range(ni)]
                p += 2 + 2 * ni
                nl = _u16(d, p)
                look = [C(o + _u16(d, p + 2 + 2 * k)) for k in range(nl)]
                p += 2 + 2 * nl
                recs = tuple((_u16(d, p + 2 + 4 * k), _u16(d, p + 4 + 4 * k)) for k in range(_u16(d, p)))
            else:
                ni, nr = _u16(d, o + 2), _u16(d, o + 4)
                inp = [C(o + _u16(d, o + 6 + 2 * k)) for k in range(ni)]
                p = o + 6 + 2 * ni
                recs = tuple((_u16(d, p + 4 * k), _u16(d, p + 2 + 4 * k)) for k in range(nr))
                back, look = [], []
            if not inp:
                return None
            return ("c3", back, inp, look, recs)
        return None

    # -- GPOS subtables ----------------------------------------------------------------------------
    def _gpos_sub(self, t, o):
        d, C, CD = self.font.data, self.font.coverage, self.font.classdef
        fmt = _u16(d, o)
        if t == 1:
            cov = C(o + _u16(d, o + 2))
            vf = _u16(d, o + 4)
            if fmt == 1:
                v = _value(d, o + 6, vf)
                return (1, {g: v for g in cov})
            n, sz = _u16(d, o + 6), _vsize(vf)
            return (1, {g: _value(d, o + 8 + i * sz, vf) for g, i in cov.items() if i < n})
        if t == 2:
            cov = C(o + _u16(d, o + 2))
            vf1, vf2 = _u16(d, o + 4), _u16(d, o + 6)
            s1, s2 = _vsize(vf1), _vsize(vf2)
            if fmt == 1:
                n = _u16(d, o + 8)
                sets = []
                for k in range(n):
                    so = o + _u16(d, o + 10 + 2 * k)
                    pairs = {}
                    rs = 2 + s1 + s2
                    for j in range(_u16(d, so)):
                        r = so + 2 + j * rs
                        pairs[_u16(d, r)] = (_value(d, r + 2, vf1), _value(d, r + 2 + s1, vf2))
                    sets.append(pairs)
                return (2, 1, {g: sets[i] for g, i in cov.items() if i < n}, vf2)
            cd1, cd2 = CD(o + _u16(d, o + 8)), CD(o + _u16(d, o + 10))
            c1n, c2n = _u16(d, o + 12), _u16(d, o + 14)
            return (2, 2, (cov, cd1, cd2, c1n, c2n, o + 16, vf1, vf2, s1, s2, {}), vf2)
        if t == 3:
            cov = C(o + _u16(d, o + 2))
            n = _u16(d, o + 4)
            recs = []
            for k in range(n):
                en, ex = _u16(d, o + 6 + 4 * k), _u16(d, o + 8 + 4 * k)
                recs.append((_anchor(d, o + en) if en else None, _anchor(d, o + ex) if ex else None))
            return (3, {g: recs[i] for g, i in cov.items() if i < n})
        if t in (4, 6):
            mcov, bcov = C(o + _u16(d, o + 2)), C(o + _u16(d, o + 4))
            cc = _u16(d, o + 6)
            ma, ba = o + _u16(d, o + 8), o + _u16(d, o + 10)
            marks = {}
            nm = _u16(d, ma)
            for g, i in mcov.items():
                if i < nm:
                    cls, ao = _u16(d, ma + 2 + 4 * i), _u16(d, ma + 4 + 4 * i)
                    marks[g] = (cls, _anchor(d, ma + ao) if ao else None)
            bases = {}
            nb = _u16(d, ba)
            for g, i in bcov.items():
                if i < nb:
                    row = []
                    for c in range(cc):
                        ao = _u16(d, ba + 2 + 2 * (i * cc + c))
                        row.append(_anchor(d, ba + ao) if ao else None)
                    bases[g] = row
            return (t, marks, bases)
        if t == 5:
            mcov, lcov = C(o + _u16(d, o + 2)), C(o + _u16(d, o + 4))
            cc = _u16(d, o + 6)
            ma, la = o + _u16(d, o + 8), o + _u16(d, o + 10)
            marks = {}
            nm = _u16(d, ma)
            for g, i in mcov.items():
                if i < nm:
                    cls, ao = _u16(d, ma + 2 + 4 * i), _u16(d, ma + 4 + 4 * i)
                    marks[g] = (cls, _anchor(d, ma + ao) if ao else None)
            ligs = {}
            nl = _u16(d, la)
            for g, i in lcov.items():
                if i < nl:
                    at = la + _u16(d, la + 2 + 2 * i)
                    comps = []
                    for c in range(_u16(d, at)):
                        row = []
                        for k in range(cc):
                            ao = _u16(d, at + 2 + 2 * (c * cc + k))
                            row.append(_anchor(d, at + ao) if ao else None)
                        comps.append(row)
                    ligs[g] = comps
            return (5, marks, ligs)
        if t == 7:
            return self._context(o, False)
        if t == 8:
            return self._context(o, True)
        return None


_FONTS = {}


def load_font(path, index=0):
    """Cached Font loader (fonts are immutable; lookups parse lazily)."""
    key = (os.path.abspath(path), index)
    f = _FONTS.get(key)
    if f is None:
        f = _FONTS[key] = Font(path, index)
    return f


# ======================================================================================================
# 2. Glyph buffer
# ======================================================================================================

_SUBST, _LIG, _MULT, _DECOMP = 1, 2, 4, 8
_BASE, _LIGC, _MARK, _COMP = 1, 2, 3, 4
_ZWNJ, _ZWJ = 0x200C, 0x200D
_GLOBAL = 1


class _GI:
    __slots__ = ("g", "cp", "cl", "src", "mask", "props", "lig_id", "lig_comp", "cat", "pos", "syl", "fl",
                 "ign", "xa", "ya", "xo", "yo", "att", "att_t")

    def copy(self):
        n = _GI.__new__(_GI)
        for s in _GI.__slots__:
            setattr(n, s, getattr(self, s))
        return n


def _default_ignorable(cp):
    if cp == _ZWNJ:
        return 1
    if cp == _ZWJ:
        return 2
    if (cp in (0x00AD, 0x034F, 0x061C, 0x180E, 0x3164, 0xFEFF, 0xFFA0) or 0x115F <= cp <= 0x1160
            or 0x17B4 <= cp <= 0x17B5 or 0x180B <= cp <= 0x180D or cp == 0x180F or 0x200B <= cp <= 0x200F
            or 0x202A <= cp <= 0x202E or 0x2060 <= cp <= 0x206F or 0xFE00 <= cp <= 0xFE0F
            or 0xFFF0 <= cp <= 0xFFF8 or 0x1BCA0 <= cp <= 0x1BCA3 or 0x1D173 <= cp <= 0x1D17A
            or 0xE0000 <= cp <= 0xE0FFF):
        return 3
    return 0


def _merge_clusters(buf, start, end):
    if end - start < 2:
        return
    cl = min(buf[k].cl for k in range(start, end))
    n = len(buf)
    if cl != buf[end - 1].cl:
        while end < n and buf[end - 1].cl == buf[end].cl:
            end += 1
    if cl != buf[start].cl:
        while start > 0 and buf[start - 1].cl == buf[start].cl:
            start -= 1
    for k in range(start, end):
        buf[k].cl = cl


# match predicates: (kind, value)
_EQ, _CLS, _COV = 0, 1, 2


def _test(g, pr):
    k, v = pr
    if k == _EQ:
        return g == v
    if k == _COV:
        return g in v
    return v[0].get(g, 0) == v[1]


class _Ctx:
    __slots__ = ("gpos", "mask", "flag", "mset", "auto_zwj", "auto_zwnj", "per_syl", "syl", "depth")


# ======================================================================================================
# 3. Script tables
# ======================================================================================================

_OT_TAGS = {
    "Devanagari": ("dev2", "deva"), "Bengali": ("bng2", "beng"), "Gurmukhi": ("gur2", "guru"),
    "Gujarati": ("gjr2", "gujr"), "Oriya": ("ory2", "orya"), "Tamil": ("tml2", "taml"),
    "Telugu": ("tel2", "telu"), "Kannada": ("knd2", "knda"), "Malayalam": ("mlm2", "mlym"),
    "Sinhala": ("sinh",), "Khmer": ("khmr",), "Myanmar": ("mym2", "mymr"), "Tibetan": ("tibt",),
    "Arabic": ("arab",), "Syriac": ("syrc",), "Nko": ("nko ",), "Mongolian": ("mong",), "Hebrew": ("hebr",),
    "Thai": ("thai",), "Lao": ("lao ",), "Latin": ("latn",), "Greek": ("grek",), "Cyrillic": ("cyrl",),
    "Armenian": ("armn",), "Georgian": ("geor",), "Thaana": ("thaa",), "Ethiopic": ("ethi",),
    "Hangul": ("hang",), "Han": ("hani",), "Hiragana": ("kana",), "Katakana": ("kana",), "Bopomofo": ("bopo",),
    "Cherokee": ("cher",), "Canadian_Aboriginal": ("cans",), "Tai_Le": ("tale",), "Ol_Chiki": ("olck",),
    "Tifinagh": ("tfng",), "Yi": ("yi  ",), "Vai": ("vai ",), "Phags_Pa": ("phag",), "Runic": ("runr",),
    "Ogham": ("ogam",),
}
MODEL_OF_SCRIPT = {s: "indic" for s in ("Devanagari", "Bengali", "Gurmukhi", "Gujarati", "Oriya", "Tamil",
                                         "Telugu", "Kannada", "Malayalam", "Sinhala")}
MODEL_OF_SCRIPT.update({"Khmer": "khmer", "Myanmar": "myanmar", "Tibetan": "use", "Arabic": "arabic",
                        "Syriac": "arabic", "Nko": "arabic", "Mongolian": "arabic"})
_RTL = frozenset({"Arabic", "Syriac", "Nko", "Hebrew", "Thaana"})

_LANG_TAGS = {
    "hi": "HIN ", "mr": "MAR ", "ne": "NEP ", "sa": "SAN ", "bn": "BEN ", "as": "ASM ", "pa": "PAN ",
    "gu": "GUJ ", "or": "ORI ", "ta": "TAM ", "te": "TEL ", "kn": "KAN ", "ml": "MAL ", "si": "SNH ",
    "ar": "ARA ", "fa": "FAR ", "ur": "URD ", "ps": "PAS ", "sd": "SND ", "ug": "UYG ", "ckb": "KUR ",
    "ku": "KUR ", "he": "IWR ", "yi": "JII ", "th": "THA ", "lo": "LAO ", "km": "KHM ", "my": "BRM ",
    "bo": "TIB ", "dz": "DZN ", "sat": "SAT ", "mn": "MNG ", "tr": "TRK ", "az": "AZE ", "ro": "ROM ",
    "ru": "RUS ", "sr": "SRB ", "mk": "MKD ", "bg": "BGR ", "pl": "PLK ", "nl": "NLD ", "ca": "CAT ",
    "kk": "KAZ ", "tt": "TAT ", "ba": "BSH ", "cv": "CHU ", "uk": "UKR ", "be": "BEL ", "el": "ELL ",
    "syr": "SYR ", "dv": "DIV ", "am": "AMH ", "ti": "TGY ", "ja": "JAN ", "ko": "KOR ", "vi": "VIT ",
}


def ot_script_tags(script):
    return _OT_TAGS.get(script, ())


def shaping_model(script):
    if script in ("Thai", "Lao"):
        return "default"
    return MODEL_OF_SCRIPT.get(script, "default")


def _guess_script(text):
    for ch in text:
        cp = ord(ch)
        for lo, hi, s in _BLOCKS:
            if lo <= cp <= hi:
                return s
    return "Latin"


_BLOCKS = [(0x0900, 0x097F, "Devanagari"), (0xA8E0, 0xA8FF, "Devanagari"), (0x0980, 0x09FF, "Bengali"),
           (0x0A00, 0x0A7F, "Gurmukhi"), (0x0A80, 0x0AFF, "Gujarati"), (0x0B00, 0x0B7F, "Oriya"),
           (0x0B80, 0x0BFF, "Tamil"), (0x0C00, 0x0C7F, "Telugu"), (0x0C80, 0x0CFF, "Kannada"),
           (0x0D00, 0x0D7F, "Malayalam"), (0x0D80, 0x0DFF, "Sinhala"), (0x0E00, 0x0E7F, "Thai"),
           (0x0E80, 0x0EFF, "Lao"), (0x0F00, 0x0FFF, "Tibetan"), (0x1000, 0x109F, "Myanmar"),
           (0x1780, 0x17FF, "Khmer"), (0x19E0, 0x19FF, "Khmer"), (0x1800, 0x18AF, "Mongolian"),
           (0x0600, 0x06FF, "Arabic"), (0x0750, 0x077F, "Arabic"), (0x0870, 0x08FF, "Arabic"),
           (0x0700, 0x074F, "Syriac"), (0x07C0, 0x07FF, "Nko"), (0x0590, 0x05FF, "Hebrew"),
           (0x0780, 0x07BF, "Thaana"), (0x0370, 0x03FF, "Greek"), (0x0400, 0x052F, "Cyrillic"),
           (0x0041, 0x024F, "Latin")]

# ---- Indic character categories (letters are HarfBuzz's category names folded to one character) ----
# C consonant, R ra, V independent vowel, N nukta, H halant/virama, n ZWNJ, j ZWJ, M matra, S syllable
# modifier (bindu / visarga), A vedic / stress accent, P placeholder, D dotted circle, r logical repha,
# Y symbol (avagraha), X other.
_CONS_FLAGS = frozenset("CRVPD")

(POS_START, POS_RA_TO_BECOME_REPH, POS_PRE_M, POS_PRE_C, POS_BASE_C, POS_AFTER_MAIN, POS_ABOVE_C,
 POS_BEFORE_SUB, POS_BELOW_C, POS_AFTER_SUB, POS_BEFORE_POST, POS_POST_C, POS_AFTER_POST, POS_FINAL_C,
 POS_SMVD, POS_END) = range(16)

_INDIC_BASES = {"Devanagari": 0x0900, "Bengali": 0x0980, "Gurmukhi": 0x0A00, "Gujarati": 0x0A80,
                "Oriya": 0x0B00, "Tamil": 0x0B80, "Telugu": 0x0C00, "Kannada": 0x0C80, "Malayalam": 0x0D00,
                "Sinhala": 0x0D80}
_RA = {0x0930, 0x09B0, 0x09F0, 0x0A30, 0x0AB0, 0x0B30, 0x0BB0, 0x0C30, 0x0CB0, 0x0D30, 0x0DBB}

# matra side after decomposition: L(eft) R(ight) T(op) B(ottom)  (Unicode IndicPositionalCategory)
_MATRA_SIDE = {}


def _sides(spec):
    for part in spec.split():
        rng, side = part.split(":")
        a, _, b = rng.partition("-")
        for cp in range(int(a, 16), int(b or a, 16) + 1):
            _MATRA_SIDE[cp] = side


_sides("093A:T 093B:R 093E:R 093F:L 0940:R 0941-0944:B 0945-0948:T 0949-094C:R 094E:L 094F:R 0955:T "
       "0956-0957:B 0962-0963:B "
       "09BE:R 09BF:L 09C0:R 09C1-09C4:B 09C7-09C8:L 09CB-09CC:R 09D7:R 09E2-09E3:B "
       "0A3E:R 0A3F:L 0A40:R 0A41-0A42:B 0A47-0A48:T 0A4B-0A4C:T 0A75:B "
       "0ABE:R 0ABF:L 0AC0:R 0AC1-0AC4:B 0AC5:T 0AC7-0AC8:T 0AC9:R 0ACB-0ACC:R 0AE2-0AE3:B "
       "0B3E:R 0B3F:T 0B40:R 0B41-0B44:B 0B47:L 0B48:T 0B4B-0B4C:R 0B55-0B56:T 0B57:R 0B62-0B63:B "
       "0BBE-0BBF:R 0BC0:T 0BC1-0BC2:R 0BC6-0BC8:L 0BCA-0BCC:R 0BD7:R "
       "0C3E-0C40:T 0C41-0C44:R 0C46-0C47:T 0C48:T 0C4A-0C4C:T 0C55:T 0C56:B 0C62-0C63:B "
       "0CBE:R 0CBF:T 0CC0:T 0CC1-0CC4:R 0CC6:T 0CC7-0CC8:T 0CCA-0CCB:T 0CCC:T 0CD5-0CD6:R 0CE2-0CE3:B "
       "0D3E-0D40:R 0D41-0D44:B 0D46-0D48:L 0D4A-0D4C:R 0D57:R 0D62-0D63:B "
       "0DCF-0DD1:R 0DD2-0DD3:T 0DD4:B 0DD6:B 0DD8:R 0DD9:L 0DDA:T 0DDB:L 0DDC-0DDE:R 0DDF:R 0DF2-0DF3:R")

_VOWEL_NAMES = {"A", "AA", "I", "II", "U", "UU", "E", "EE", "AI", "O", "OO", "AU", "SHORT A", "CANDRA A",
                "CANDRA E", "SHORT E", "CANDRA O", "SHORT O", "OE", "OOE", "AW", "UE", "UUE", "ARCHAIC II"}


def _indic_script_of(cp):
    for s, b in _INDIC_BASES.items():
        if b <= cp < b + 0x80:
            return s
    return None


def _indic_category(cp):
    """-> (category char, position)."""
    if cp == _ZWNJ:
        return "n", POS_END
    if cp == _ZWJ:
        return "j", POS_END
    if cp in (0x00A0, 0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2022, 0x25FB, 0x25FC, 0x25FD,
              0x25FE, 0x0980, 0x09FC, 0x0C80):
        return "P", POS_BASE_C
    if cp == 0x25CC:
        return "D", POS_BASE_C
    ch = chr(cp)
    gc = unicodedata.category(ch)
    name = unicodedata.name(ch, "")
    script = _indic_script_of(cp)
    if 0x1CD0 <= cp <= 0x1CFF or 0xA8E0 <= cp <= 0xA8FF:
        if 0xA8F2 <= cp <= 0xA8F7 or 0x1CE9 <= cp <= 0x1CEC or 0x1CEE <= cp <= 0x1CF1:
            return "Y", POS_END
        if cp in (0x1CF5, 0x1CF6):
            return "C", POS_BASE_C
        if gc in ("Mn", "Mc"):
            return "A", POS_SMVD
        return "X", POS_END
    if script is None:
        return "X", POS_END
    if cp == 0x0D04:                    # Malayalam VEDIC ANUSVARA is a bindu although named LETTER
        return "S", POS_SMVD
    if gc in ("Mn", "Mc"):
        if "VIRAMA" in name or "AL-LAKUNA" in name:
            return "H", POS_END
        if "NUKTA" in name or cp == 0x0AFB:
            return "N", POS_END
        if "VOWEL SIGN" in name or "LENGTH MARK" in name or cp == 0x0A75:
            side = _MATRA_SIDE.get(cp, "R")
            return "M", _matra_pos(cp, script, side)
        if any(k in name for k in ("BINDU", "ANUSVARA", "VISARGA", "TIPPI", "ADDAK", "ADAK", "CANDRABINDU")):
            return "S", (POS_BEFORE_SUB if cp == 0x0B01 else POS_SMVD)
        if 0x0953 <= cp <= 0x0954:
            return "S", POS_SMVD
        return "A", POS_SMVD
    if cp == 0x0D4E:
        return "r", POS_END
    if gc == "Lo":
        if script == "Sinhala":
            if 0x0D85 <= cp <= 0x0D96:
                return "V", POS_BASE_C
            if 0x0D9A <= cp <= 0x0DC6:
                return ("R" if cp in _RA else "C"), POS_BASE_C
            return "X", POS_END
        if cp in (0x0A72, 0x0A73):      # Gurmukhi IRI / URA: Windows gives following signs a dotted circle
            return "X", POS_END
        if "AVAGRAHA" in name:
            return "Y", POS_END
        if " LETTER " in name:
            tail = name.split(" LETTER ", 1)[1]
            if "VOCALIC" in tail or tail in _VOWEL_NAMES:
                return "V", POS_BASE_C
            return ("R" if cp in _RA else "C"), POS_BASE_C
    return "X", POS_END


def _matra_pos(cp, script, side):
    if side == "L":
        return POS_PRE_M
    if side == "R":
        if script in ("Devanagari", "Sinhala"):
            return POS_AFTER_SUB
        if script == "Telugu":
            return POS_BEFORE_SUB if cp <= 0x0C42 else POS_AFTER_SUB
        if script == "Kannada":
            return POS_BEFORE_SUB if (cp < 0x0CC3 or cp > 0x0CD6) else POS_AFTER_SUB
        return POS_AFTER_POST
    if side == "T":
        return {"Gurmukhi": POS_AFTER_POST, "Oriya": POS_AFTER_MAIN, "Telugu": POS_BEFORE_SUB,
                "Kannada": POS_BEFORE_SUB}.get(script, POS_AFTER_SUB)
    return {"Gurmukhi": POS_AFTER_POST, "Gujarati": POS_AFTER_POST, "Tamil": POS_AFTER_POST,
            "Telugu": POS_BEFORE_SUB, "Kannada": POS_BEFORE_SUB, "Malayalam": POS_AFTER_POST}.get(script, POS_AFTER_SUB)


# script -> (has_old_spec, virama, base_pos, reph_pos, reph_mode, blwf_mode)
_INDIC_CONFIG = {
    "Devanagari": (True, 0x094D, "last", POS_BEFORE_POST, "implicit", "pre_and_post"),
    "Bengali": (True, 0x09CD, "last", POS_AFTER_SUB, "implicit", "pre_and_post"),
    "Gurmukhi": (True, 0x0A4D, "last", POS_BEFORE_SUB, "implicit", "pre_and_post"),
    "Gujarati": (True, 0x0ACD, "last", POS_BEFORE_POST, "implicit", "pre_and_post"),
    "Oriya": (True, 0x0B4D, "last", POS_AFTER_MAIN, "implicit", "pre_and_post"),
    "Tamil": (True, 0x0BCD, "last", POS_AFTER_POST, "implicit", "pre_and_post"),
    "Telugu": (True, 0x0C4D, "last", POS_AFTER_POST, "explicit", "post_only"),
    "Kannada": (True, 0x0CCD, "last", POS_AFTER_POST, "implicit", "post_only"),
    "Malayalam": (True, 0x0D4D, "last", POS_AFTER_MAIN, "log_repha", "pre_and_post"),
    "Sinhala": (False, 0x0DCA, "last_sinhala", POS_AFTER_POST, "explicit", "pre_and_post"),
}

_INDIC_BASIC = ("nukt", "akhn", "rphf", "rkrf", "pref", "blwf", "abvf", "half", "pstf", "vatu", "cjct")
_INDIC_MASKED = {"rphf", "pref", "blwf", "abvf", "half", "pstf", "init"}
_INDIC_OTHER = ("init", "pres", "abvs", "blws", "psts", "haln")

# Indic syllable grammar (HarfBuzz indic machine) over the category string.
_c = "[CR]"
_n = "(?:NN?)?"
_z = "[jn]"
_reph = "(?:RH|r)"
_cn = _c + "j?" + _n
_matra_group = _z + "*MN?H?"
_syl_tail = "(?:" + _z + "?SS?n?)?A*"
_halant_group = _z + "?H(?:jN?)?"
_final_halant = "(?:Hn|" + _halant_group + ")"     # longest first: Python alternation is first-match
_hm_group = "(?:" + _final_halant + "|(?:" + _matra_group + ")*)"
_ctail = "(?:" + _halant_group + _cn + ")*" + _hm_group + _syl_tail
_INDIC_SYLLABLES = [
    ("consonant", re.compile("r?" + _cn + _ctail)),
    ("vowel", re.compile(_reph + "?V" + _n + "j")),
    ("vowel", re.compile(_reph + "?V" + _n + _ctail)),
    ("standalone", re.compile("(?:r?P|" + _reph + "?D)" + _n + _ctail)),
    ("symbol", re.compile("YN?" + _syl_tail)),
    ("broken", re.compile(_reph + "?" + _n + _ctail)),
]

# ---- Khmer / Myanmar categories -------------------------------------------------------------------


def _khmer_category(cp):
    if cp == _ZWNJ:
        return "n"
    if cp == _ZWJ:
        return "j"
    if cp in (0x00A0, 0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015):
        return "P"
    if cp == 0x25CC:
        return "D"
    if cp == 0x179A:
        return "R"
    if 0x1780 <= cp <= 0x17A2:
        return "C"
    if 0x17A3 <= cp <= 0x17B3:
        return "V"
    if cp == 0x17D2:
        return "H"
    if cp in (0x17C1, 0x17C2, 0x17C3):
        return "L"          # VPre
    if cp == 0x17CC:
        return "o"          # Robat
    if cp in (0x17C9, 0x17CA):
        return "s"          # register shifter
    if 0x17B6 <= cp <= 0x17D3 or cp == 0x17DD:
        return "M"
    return "X"


def _myanmar_category(cp):
    if cp == _ZWNJ:
        return "n"
    if cp == _ZWJ:
        return "j"
    if cp in (0x00A0, 0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015):
        return "P"
    if cp == 0x25CC:
        return "D"
    if cp in (0x1004, 0x101B, 0x105A):
        return "R"
    if (0x1000 <= cp <= 0x1020 or cp in (0x103F, 0x104E, 0x1050, 0x1051, 0x1061, 0x1065, 0x1066, 0x108E)
            or 0x105A <= cp <= 0x105D or 0x106E <= cp <= 0x1070 or 0x1075 <= cp <= 0x1081
            or 0xAA60 <= cp <= 0xAA6F or 0xA9E0 <= cp <= 0xA9E4 or 0xA9E7 <= cp <= 0xA9EF):
        return "C"
    if 0x1021 <= cp <= 0x102A or 0x1052 <= cp <= 0x1055:
        return "V"
    if cp == 0x103A:
        return "a"          # asat
    if cp == 0x1039:
        return "H"
    if cp in (0x103B, 0x105E, 0x105F):
        return "y"          # medial ya
    if cp == 0x103C:
        return "q"          # medial ra (pre-base)
    if cp in (0x103D, 0x1082):
        return "w"
    if cp in (0x103E, 0x1060):
        return "h"
    if cp in (0x1031, 0x1084):
        return "L"          # VPre
    if cp in (0x102D, 0x102E, 0x1032, 0x1033, 0x1034, 0x1035, 0x1071, 0x1072, 0x1073, 0x1074, 0x1085,
              0x1086, 0x109D):
        return "T"          # VAbv
    if cp in (0x102F, 0x1030, 0x1058, 0x1059):
        return "B"          # VBlw
    if cp in (0x102B, 0x102C, 0x1056, 0x1057, 0x1062, 0x1067, 0x1068, 0x1083, 0x108F, 0x109A, 0x109B,
              0x109C) or 0x1087 <= cp <= 0x108C:
        return "Q"          # VPst
    if cp == 0x1036:
        return "A"
    if cp == 0x1037:
        return "d"          # dot below
    if cp == 0x1038 or 0x1063 <= cp <= 0x1064 or 0x1069 <= cp <= 0x106D or cp == 0x108D:
        return "S"
    if 0xFE00 <= cp <= 0xFE0F:
        return "v"
    return "X"


# ---- Arabic joining ------------------------------------------------------------------------------

def _build_joining():
    jt = {}
    for cp in range(0xFB50, 0xFF00):
        dec = unicodedata.decomposition(chr(cp))
        if not dec.startswith("<"):
            continue
        kind, _, rest = dec.partition(" ")
        parts = rest.split()
        if len(parts) != 1:
            continue
        base = int(parts[0], 16)
        if kind in ("<initial>", "<medial>"):
            jt[base] = "D"
        elif kind == "<final>" and jt.get(base) != "D":
            jt[base] = "R"
    return jt


_JT = _build_joining()
_HEBREW_COMP = {}
for _cp in range(0xFB1D, 0xFB50):
    _d = unicodedata.decomposition(chr(_cp))
    if _d and not _d.startswith("<") and len(_d.split()) == 2:
        _a, _b = (int(x, 16) for x in _d.split())
        if _b in (0x05BC, 0x05C1, 0x05C2) or (_a, _b) == (0x05D5, 0x05B9):   # what Windows composes
            _HEBREW_COMP[(_a, _b)] = _cp
_R_BASES = frozenset({"ALEF", "DAL", "THAL", "REH", "ZAIN", "WAW", "AE", "OE", "U", "YU", "VE", "KIRGHIZ OE",
                      "KIRGHIZ YU", "JEH", "DDAL", "RREH", "DUL", "DAHAL", "DDAHAL", "ALEF WASLA", "ALEF MADDA"})
_SYRIAC_R = ("ALAPH", "DALATH", "DOTLESS DALATH RISH", "SYRIAC LETTER HE", "WAW", "ZAIN", "YUDH HE",
             "SADHE", "RISH", "TAW", "PERSIAN DHALATH")


def joining_type(cp):
    """Unicode joining type approximation: U, R, D, C or T."""
    if cp in (_ZWJ, 0x0640, 0x07FA, 0x180A):
        return "C"
    if cp == _ZWNJ:
        return "U"
    t = _JT.get(cp)
    if t:
        return t
    ch = chr(cp)
    gc = unicodedata.category(ch)
    if gc in ("Mn", "Me", "Cf"):
        return "T"
    if gc != "Lo":
        return "U"
    name = unicodedata.name(ch, "")
    if name.startswith("ARABIC LETTER "):
        base = name[len("ARABIC LETTER "):].split(" WITH ")[0]
        if base == "HAMZA" or base == "HIGH HAMZA":
            return "U"
        if base.startswith("HIGH HAMZA "):
            return "D" if base.endswith("YEH") else "R"
        if base in _R_BASES or name.endswith("YEH WITH TAIL") or base.startswith(("TEH MARBUTA", "YEH BARREE")):
            return "R"
        return "D"
    if name.startswith("SYRIAC LETTER"):
        return "R" if any(w in name for w in _SYRIAC_R) else "D"
    if name.startswith("NKO LETTER"):
        return "D"
    if name.startswith("MONGOLIAN LETTER"):
        return "D"
    return "U"


# ======================================================================================================
# 4. The shaper
# ======================================================================================================

class _Shaper:
    def __init__(self, font, script, lang, direction, features):
        self.font = font
        self.script = script
        self.model = shaping_model(script)
        self.rtl = direction == "rtl"
        tags = _OT_TAGS.get(script, ())
        ltag = _LANG_TAGS.get((lang or "").split("-")[0].lower()) if lang else None
        self.lang_tag = ltag
        self.gsub_tag, self.gsub_map = font.gsub.select(tags, ltag) if font.gsub else (None, {})
        self.gpos_tag, self.gpos_map = font.gpos.select(tags, ltag) if font.gpos else (None, {})
        if self.model == "indic" and self.gsub_tag not in tags:
            self.model = "default"      # font has no Indic tables for this script (HarfBuzz does the same)
        if self.model == "myanmar" and self.gsub_tag != "mym2":
            self.model = "default"
        self.user_features = features or {}
        self.any_context_positions = True
        self.skip_unformed_below = set(_INDIC_CONFIG)
        self._would = {}
        self._lig_id = 0

    # -- feature plan ------------------------------------------------------------------------------
    def _plan(self):
        """-> (gsub stages, gpos stages); a stage is ([(tag, mask, manual_joiners, per_syllable)], pause)."""
        self.bits = {}
        nb = [2]

        def bit(tag):
            if tag not in self.bits:
                self.bits[tag] = nb[0]
                nb[0] <<= 1
            return self.bits[tag]

        G = _GLOBAL
        m = self.model
        common_gsub = [("calt", G, False, False), ("clig", G, False, False), ("liga", G, False, False),
                       ("rclt", G, False, False), ("rlig", G, False, False)]
        gpos = [([(t, G, False, False) for t in ("abvm", "blwm", "curs", "dist", "kern", "mark", "mkmk")], None)]
        if m == "indic":
            st = [([("locl", G, True, True), ("ccmp", G, True, True)], self._indic_initial)]
            for t in _INDIC_BASIC:
                st.append(([(t, bit(t) if t in _INDIC_MASKED else G, True, True)], None))
            st[-1] = (st[-1][0], self._indic_final)
            st.append(([(t, bit(t) if t in _INDIC_MASKED else G, True, True) for t in _INDIC_OTHER]
                       + [(t, G, False, False) for t in ("calt", "clig", "liga", "rclt")], None))
            for t in _INDIC_MASKED:
                bit(t)
            return st, gpos
        if m == "khmer":
            st = [([("locl", G, True, True), ("ccmp", G, True, True)], self._khmer_reorder)]
            for t in ("pref", "blwf", "abvf", "pstf", "cfar"):
                st.append(([(t, bit(t), True, True)], None))
            st.append(([(t, G, True, True) for t in ("pres", "abvs", "blws", "psts")]
                       + [(t, G, False, False) for t in ("calt", "clig", "liga", "rclt")], None))
            return st, gpos
        if m == "myanmar":
            st = [([("locl", G, True, True), ("ccmp", G, True, True)], self._myanmar_reorder)]
            for t in ("rphf", "pref", "blwf", "pstf"):
                st.append(([(t, G, True, True)], None))
            st.append(([(t, G, True, True) for t in ("pres", "abvs", "blws", "psts")]
                       + [(t, G, False, False) for t in ("calt", "clig", "liga", "rclt")], None))
            return st, gpos
        if m == "use":
            st = [([(t, G, False, False) for t in ("locl", "ccmp", "nukt", "akhn")], None),
                  ([("rphf", G, False, False)], None), ([("pref", G, False, False)], None),
                  ([(t, G, False, False) for t in ("rkrf", "abvf", "blwf", "half", "pstf", "vatu", "cjct")], None),
                  ([(t, G, False, False) for t in ("abvs", "blws", "haln", "pres", "psts")] + common_gsub, None)]
            return st, gpos
        if m == "arabic":
            st = [([("ccmp", G, False, False), ("locl", G, False, False)], None)]
            for t in ("isol", "fina", "fin2", "fin3", "medi", "med2", "init"):
                st.append(([(t, bit(t), False, False)], None))
            st.append(([("rlig", G, False, False)], None))
            st.append(([("rclt", G, False, False), ("calt", G, False, False)], None))
            st.append(([("liga", G, False, False), ("clig", G, False, False), ("mset", G, False, False)], None))
            return st, gpos
        st = [([("ccmp", G, False, False), ("locl", G, False, False)] + common_gsub, None)]
        return st, gpos

    # -- buffer ------------------------------------------------------------------------------------
    def _new_gi(self, cp, cl, src):
        gi = _GI()
        gi.cp, gi.cl, gi.src = cp, cl, src
        gi.g = self.font.glyph(cp)
        gi.mask, gi.lig_id, gi.lig_comp, gi.cat, gi.pos, gi.syl, gi.fl = _GLOBAL, 0, 0, "X", POS_END, 0, 0
        gi.ign = _default_ignorable(cp)
        gi.xa = gi.ya = gi.xo = gi.yo = 0
        gi.att, gi.att_t = -1, 0
        self._init_props(gi)
        return gi

    def _init_props(self, gi):
        f = self.font
        if f.has_glyph_classes:
            gi.props = f.glyph_class.get(gi.g, 0)
        else:
            gi.props = _MARK if unicodedata.category(chr(gi.cp)) == "Mn" else _BASE

    def _set_glyph(self, gi, g, lig=False, mult=False):
        gi.g = g
        gi.fl |= _SUBST | (_LIG if lig else 0) | (_MULT if mult else 0)
        if self.font.has_glyph_classes:
            gi.props = self.font.glyph_class.get(g, 0)
        elif lig:
            gi.props = _LIGC

    def _decompose(self, cp):
        """Characters to feed for one input code point (split matras, missing precomposed letters)."""
        f = self.font
        m = self.model
        if m == "khmer" and cp in (0x17BE, 0x17BF, 0x17C0, 0x17C4, 0x17C5):
            return [0x17C1, cp]
        if m == "default" and self.script in ("Thai", "Lao") and cp in (0x0E33, 0x0EB3):
            return [0x0E4D, 0x0E32] if cp == 0x0E33 else [0x0ECD, 0x0EB2]
        ch = chr(cp)
        dec = unicodedata.decomposition(ch)
        if not dec or dec.startswith("<"):
            return [cp]
        parts = [int(x, 16) for x in dec.split()]
        if m == "indic" and unicodedata.category(ch) in ("Mn", "Mc"):
            out = []
            for p in parts:
                out += self._decompose(p)
            if all(f.glyph(p) for p in out):
                return out
            return [cp]
        if not f.glyph(cp):
            out = []
            for p in parts:
                out += self._decompose(p)
            if all(f.glyph(p) for p in out):
                return out
        return [cp]

    def _buffer(self, text):
        buf = []
        for k, ch in enumerate(text):
            cp = ord(ch)
            parts = self._decompose(cp)
            for p in parts:
                gi = self._new_gi(p, k, chr(p) if len(parts) > 1 else ch)
                if len(parts) > 1:
                    gi.fl |= _DECOMP
                buf.append(gi)
        # graphemes: marks and ZWJ continue the previous cluster (HarfBuzz cluster level 0)
        for i in range(1, len(buf)):
            gc = unicodedata.category(chr(buf[i].cp))
            if gc in ("Mn", "Mc", "Me") or buf[i].cp == _ZWJ or 0xFE00 <= buf[i].cp <= 0xFE0F:
                start = i - 1
                while start > 0 and buf[start - 1].cl == buf[start].cl:
                    start -= 1
                _merge_clusters(buf, start, i + 1)
        if self.model == "default" and self.script in ("Thai", "Lao"):
            self._thai_sara_am(buf)
        if self.script == "Hebrew":
            buf = self._hebrew_compose(buf)
        return buf

    def _hebrew_compose(self, buf):
        """Compose letter + point into the Hebrew presentation forms (U+FB1D-FB4F) the font has, as
        Windows does (they are composition exclusions, so NFC text never contains them)."""
        f = self.font
        out = []
        i = 0
        while i < len(buf):
            gi = buf[i]
            out.append(gi)
            i += 1
            if unicodedata.combining(chr(gi.cp)):
                continue
            j = i
            last_ccc = 0
            while j < len(buf) and unicodedata.combining(chr(buf[j].cp)):
                m = buf[j]
                ccc = unicodedata.combining(chr(m.cp))
                comp = _HEBREW_COMP.get((gi.cp, m.cp))
                if comp and f.glyph(comp) and (last_ccc < ccc or j == i):
                    gi.cp, gi.g, gi.src = comp, f.glyph(comp), gi.src + m.src
                    self._init_props(gi)
                    del buf[j]
                    continue
                last_ccc = ccc
                out.append(m)
                j += 1
            i = j
        return out

    def _thai_sara_am(self, buf):
        """Move the decomposed NIKHAHIT before preceding tone marks (Windows / HarfBuzz behaviour)."""
        i = 0
        while i < len(buf):
            if buf[i].cp in (0x0E4D, 0x0ECD) and i + 1 < len(buf) and buf[i + 1].cp in (0x0E32, 0x0EB2) \
                    and buf[i].cl == buf[i + 1].cl and buf[i].fl & _DECOMP:
                j = i
                while j > 0 and (0x0E48 <= buf[j - 1].cp <= 0x0E4B or 0x0EC8 <= buf[j - 1].cp <= 0x0ECB):
                    j -= 1
                if j < i:
                    nk = buf.pop(i)
                    buf.insert(j, nk)
                    _merge_clusters(buf, j, i + 2)
            i += 1

    # -- matching ------------------------------------------------------------------------------------
    def _ignored(self, gi, flag, mset):
        p = gi.props
        if p == _MARK:
            if flag & 8:
                return True
            if flag & 0x10:
                return mset is not None and gi.g not in mset
            if flag & 0xFF00:
                return self.font.mark_class.get(gi.g, 0) != (flag >> 8)
            return False
        if p == _BASE:
            return bool(flag & 2)
        if p == _LIGC:
            return bool(flag & 4)
        return False

    def _skip(self, gi, c, flag, mset, context):
        if self._ignored(gi, flag, mset):
            return 2
        ig = gi.ign
        if ig:
            if c.gpos:
                return 1
            if ig == 1:
                return 1 if (context and c.auto_zwnj) else 0
            if ig == 2:
                return 1 if (context or c.auto_zwj) else 0
            return 1
        return 0

    def _step(self, buf, j, c, pr, context, flag, mset, d):
        n = len(buf)
        j += d
        while 0 <= j < n:
            gi = buf[j]
            sk = self._skip(gi, c, flag, mset, context)
            if sk == 2:
                j += d
                continue
            if (not context and not (gi.mask & c.mask)) or (c.syl is not None and gi.syl != c.syl):
                m = 0
            elif pr is None:
                m = 1
            else:
                m = 2 if _test(gi.g, pr) else 0
            if m == 2 or (m == 1 and sk == 0):
                return j
            if sk == 0:
                return -1
            j += d
        return -1

    def _match_input(self, buf, i, preds, c):
        pos = [i]
        j = i
        for pr in preds:
            j = self._step(buf, j, c, pr, False, c.flag, c.mset, 1)
            if j < 0:
                return None
            pos.append(j)
        return pos

    def _match_back(self, buf, i, preds, c):
        j = i
        for pr in preds:
            j = self._step(buf, j, c, pr, True, c.flag, c.mset, -1)
            if j < 0:
                return False
        return True

    def _match_look(self, buf, last, preds, c):
        j = last
        for pr in preds:
            j = self._step(buf, j, c, pr, True, c.flag, c.mset, 1)
            if j < 0:
                return False
        return True

    # -- lookup application ------------------------------------------------------------------------
    def _apply_stage(self, buf, table, fmap, feats, gpos):
        lk = {}
        for tag, mask, manual, per_syl in feats:
            for li in fmap.get(tag, ()):
                cur = lk.get(li)
                if cur is None:
                    lk[li] = [mask, manual, per_syl]
                else:
                    cur[0] |= mask
        for li in sorted(lk):
            mask, manual, per_syl = lk[li]
            L = table.lookup(li)
            c = _Ctx()
            c.gpos, c.mask, c.auto_zwj, c.auto_zwnj, c.per_syl, c.syl, c.depth = gpos, mask, not manual, not manual, per_syl, None, 0
            self._apply_lookup(L, buf, c, table)

    def _apply_lookup(self, L, buf, c, table):
        c.flag, c.mset = L.flag, L.mset
        if not L.subs:
            return
        if not c.gpos and L.type == 8:
            i = len(buf) - 1
            while i >= 0:
                gi = buf[i]
                if (gi.mask & c.mask) and not self._ignored(gi, L.flag, L.mset):
                    c.syl = gi.syl if c.per_syl else None
                    for sub in L.subs:
                        _, cov, back, look, subst = sub
                        k = cov.get(gi.g)
                        if k is None or k >= len(subst):
                            continue
                        if self._match_back(buf, i, [(_COV, x) for x in back], c) and \
                                self._match_look(buf, i, [(_COV, x) for x in look], c):
                            self._set_glyph(gi, subst[k])
                            break
                i -= 1
            return
        i = 0
        while i < len(buf):
            gi = buf[i]
            if (gi.mask & c.mask) and not self._ignored(gi, L.flag, L.mset):
                c.syl = gi.syl if c.per_syl else None
                r = None
                for sub in L.subs:
                    r = self._apply_sub(L.type, sub, buf, i, c, table)
                    if r is not None:
                        break
                if r is not None:
                    i = max(r, i + 1) if c.gpos else r
                    continue
            i += 1

    def _apply_nested(self, li, buf, idx, c, table):
        if c.depth > 6 or idx >= len(buf):
            return None
        L = table.lookup(li)
        saved = (c.flag, c.mset)
        c.flag, c.mset = L.flag, L.mset
        c.depth += 1
        r = None
        try:
            if not c.gpos and L.type == 8:
                gi = buf[idx]
                for sub in L.subs:
                    _, cov, back, look, subst = sub
                    k = cov.get(gi.g)
                    if k is not None and k < len(subst) and \
                            self._match_back(buf, idx, [(_COV, x) for x in back], c) and \
                            self._match_look(buf, idx, [(_COV, x) for x in look], c):
                        self._set_glyph(gi, subst[k])
                        r = idx + 1
                        break
            elif not self._ignored(buf[idx], L.flag, L.mset):
                for sub in L.subs:
                    r = self._apply_sub(L.type, sub, buf, idx, c, table)
                    if r is not None:
                        break
        finally:
            c.depth -= 1
            c.flag, c.mset = saved
        return r

    def _apply_sub(self, t, sub, buf, i, c, table):
        gi = buf[i]
        g = gi.g
        if not c.gpos:
            if t == 1:
                ng = sub[1].get(g)
                if ng is None:
                    return None
                self._set_glyph(gi, ng)
                return i + 1
            if t == 2:
                seq = sub[1].get(g)
                if seq is None:
                    return None
                if not seq:
                    del buf[i]
                    return i
                out = []
                for k, ng in enumerate(seq):
                    n = gi.copy()
                    self._set_glyph(n, ng, mult=len(seq) > 1)
                    n.lig_id, n.lig_comp = 0, k + 1 if len(seq) > 1 else 0
                    if k:
                        n.src = ""
                    out.append(n)
                buf[i:i + 1] = out
                return i + len(out)
            if t == 3:
                alts = sub[1].get(g)
                if not alts:
                    return None
                self._set_glyph(gi, alts[0])
                return i + 1
            if t == 4:
                ligset = sub[1].get(g)
                if ligset is None:
                    return None
                for lg, comps in ligset:
                    pos = self._match_input(buf, i, [(_EQ, x) for x in comps], c)
                    if pos is None:
                        continue
                    return self._ligate(buf, pos, lg)
                return None
            if t in (5, 6):
                return self._apply_context(sub, buf, i, c, table)
            return None
        # ---- GPOS
        if t == 1:
            v = sub[1].get(g)
            if v is None:
                return None
            self._add_value(gi, v)
            return i + 1
        if t == 2:
            j = None
            if sub[1] == 1:
                pairs = sub[2].get(g)
                if pairs is None:
                    return None
                j = self._step(buf, i, c, None, False, c.flag, c.mset, 1)
                if j < 0:
                    return None
                vv = pairs.get(buf[j].g)
                if vv is None:
                    return None
            else:
                cov, cd1, cd2, c1n, c2n, mo, vf1, vf2, s1, s2, cache = sub[2]
                if g not in cov:
                    return None
                j = self._step(buf, i, c, None, False, c.flag, c.mset, 1)
                if j < 0:
                    return None
                k1, k2 = cd1.get(g, 0), cd2.get(buf[j].g, 0)
                if k1 >= c1n or k2 >= c2n:
                    return None
                vv = cache.get((k1, k2))
                if vv is None:
                    p = mo + (k1 * c2n + k2) * (s1 + s2)
                    d = self.font.data
                    vv = cache[(k1, k2)] = (_value(d, p, vf1), _value(d, p + s1, vf2))
            self._add_value(gi, vv[0])
            self._add_value(buf[j], vv[1])
            return j + 1 if sub[3] else j
        if t == 3:
            rec = sub[1].get(g)
            if rec is None or rec[1] is None:
                return None
            j = self._step(buf, i, c, None, False, c.flag, c.mset, 1)
            if j < 0:
                return None
            rec2 = sub[1].get(buf[j].g)
            if rec2 is None or rec2[0] is None:
                return None
            ex, en = rec[1], rec2[0]
            # horizontal cursive attachment: advance of the exiting glyph ends at its exit anchor and the
            # entering glyph starts at its entry anchor; the y difference is carried as an attachment chain.
            if not self.rtl:
                gi.xa = ex[0] + gi.xo
                d = en[0] + buf[j].xo
                buf[j].xa -= d
                buf[j].xo -= d
            else:
                d = ex[0] + gi.xo
                gi.xa -= d
                gi.xo -= d
                buf[j].xa = en[0] + buf[j].xo
            if c.flag & 1:      # RightToLeft lookup flag: the earlier glyph hangs off the later one
                child, parent, y = i, j, en[1] - ex[1]
            else:
                child, parent, y = j, i, ex[1] - en[1]
            buf[child].yo = y
            buf[child].att, buf[child].att_t = parent, 2
            return j
        if t == 4:
            mk = sub[1].get(g)
            if mk is None or mk[1] is None:
                return None
            j = i - 1
            while j >= 0 and (buf[j].props == _MARK or buf[j].ign):
                j -= 1
            if j < 0:
                return None
            row = sub[2].get(buf[j].g)
            if row is None or mk[0] >= len(row) or row[mk[0]] is None:
                return None
            self._attach(buf, i, j, mk[1], row[mk[0]])
            return i + 1
        if t == 5:
            mk = sub[1].get(g)
            if mk is None or mk[1] is None:
                return None
            j = i - 1
            while j >= 0 and (buf[j].props == _MARK or buf[j].ign):
                j -= 1
            if j < 0:
                return None
            comps = sub[2].get(buf[j].g)
            if not comps:
                return None
            lig = buf[j]
            n = len(comps)
            if lig.lig_id and lig.lig_id == gi.lig_id and gi.lig_comp > 0:
                ci = min(n, gi.lig_comp) - 1
            else:
                ci = n - 1
            row = comps[ci]
            if mk[0] >= len(row) or row[mk[0]] is None:
                return None
            self._attach(buf, i, j, mk[1], row[mk[0]])
            return i + 1
        if t == 6:
            mk = sub[1].get(g)
            if mk is None or mk[1] is None:
                return None
            j = self._step(buf, i, c, None, False, c.flag & ~0x0E, c.mset, -1)
            if j < 0 or buf[j].props != _MARK:
                return None
            a, b = gi, buf[j]
            if not (a.lig_id == b.lig_id and (a.lig_id == 0 or a.lig_comp == b.lig_comp)) and \
                    not ((a.lig_id > 0 and not a.lig_comp) or (b.lig_id > 0 and not b.lig_comp)):
                return None
            row = sub[2].get(b.g)
            if row is None or mk[0] >= len(row) or row[mk[0]] is None:
                return None
            self._attach(buf, i, j, mk[1], row[mk[0]])
            return i + 1
        if t in (7, 8):
            return self._apply_context(sub, buf, i, c, table)
        return None

    @staticmethod
    def _add_value(gi, v):
        gi.xo += v[0]
        gi.yo += v[1]
        gi.xa += v[2]
        gi.ya += v[3]

    @staticmethod
    def _attach(buf, i, j, mark_anchor, base_anchor):
        gi = buf[i]
        gi.xo = base_anchor[0] - mark_anchor[0]
        gi.yo = base_anchor[1] - mark_anchor[1]
        gi.att, gi.att_t = j, 1

    def _ligate(self, buf, pos, lg):
        first = buf[pos[0]]
        comps = [buf[p] for p in pos]
        all_marks = all(x.props == _MARK for x in comps)
        lig = first.copy()
        lig.src = "".join(x.src for x in comps)
        self._set_glyph(lig, lg, lig=True)
        if not self.font.has_glyph_classes:
            lig.props = _MARK if all_marks else _LIGC
        if all_marks or len(pos) == 1:
            lig_id = 0
        else:
            self._lig_id = (self._lig_id % 7) + 1
            lig_id = self._lig_id
        lig.lig_id, lig.lig_comp = lig_id, 0
        between = []
        comp_no = 0
        span = range(pos[0], pos[-1] + 1)
        cset = set(pos)
        for k in span:
            if k in cset:
                comp_no += 1
                continue
            m = buf[k]
            if lig_id:
                m.lig_id, m.lig_comp = lig_id, comp_no
            between.append(m)
        _merge_clusters(buf, pos[0], pos[-1] + 1)
        lig.cl = buf[pos[0]].cl
        for m in between:
            m.cl = lig.cl
        buf[pos[0]:pos[-1] + 1] = [lig] + between
        return pos[0] + 1 + len(between)

    def _apply_context(self, sub, buf, i, c, table):
        kind = sub[0]
        g = buf[i].g
        if kind == "c1":
            k = sub[1].get(g)
            if k is None or k >= len(sub[2]):
                return None
            for back, inp, look, recs in sub[2][k]:
                pos = self._match_input(buf, i, [(_EQ, x) for x in inp], c)
                if pos is None:
                    continue
                if not self._match_back(buf, i, [(_EQ, x) for x in back], c):
                    continue
                if not self._match_look(buf, pos[-1], [(_EQ, x) for x in look], c):
                    continue
                return self._run_records(buf, pos, recs, c, table)
            return None
        if kind == "c2":
            _, cov, bcd, icd, lcd, sets = sub
            if g not in cov:
                return None
            k = icd.get(g, 0)
            if k >= len(sets):
                return None
            for back, inp, look, recs in sets[k]:
                pos = self._match_input(buf, i, [(_CLS, (icd, x)) for x in inp], c)
                if pos is None:
                    continue
                if not self._match_back(buf, i, [(_CLS, (bcd, x)) for x in back], c):
                    continue
                if not self._match_look(buf, pos[-1], [(_CLS, (lcd, x)) for x in look], c):
                    continue
                return self._run_records(buf, pos, recs, c, table)
            return None
        if kind == "c3":
            _, back, inp, look, recs = sub
            if g not in inp[0]:
                return None
            pos = self._match_input(buf, i, [(_COV, x) for x in inp[1:]], c)
            if pos is None:
                return None
            if not self._match_back(buf, i, [(_COV, x) for x in back], c):
                return None
            if not self._match_look(buf, pos[-1], [(_COV, x) for x in look], c):
                return None
            return self._run_records(buf, pos, recs, c, table)
        return None

    def _run_records(self, buf, mp, recs, c, table):
        mp = list(mp)
        count = len(mp)
        end = mp[-1] + 1
        for seq, li in recs:
            if seq >= count:
                continue
            orig = len(buf)
            idx = mp[seq]
            if self._apply_nested(li, buf, idx, c, table) is None:
                continue
            delta = len(buf) - orig
            if not delta:
                continue
            end += delta
            if end < mp[seq]:
                delta += mp[seq] - end
                end = mp[seq]
            nxt = seq + 1
            if delta > 0:
                mp = mp[:nxt] + [0] * delta + mp[nxt:]
            else:
                delta = max(delta, nxt - count)
                nxt -= delta
                mp = mp[:nxt + delta] + mp[nxt:]
            nxt += delta
            count += delta
            for j in range(seq + 1, min(nxt, count)):
                mp[j] = mp[j - 1] + 1
            for j in range(nxt, count):
                mp[j] += delta
        return max(end, mp[0] + 1) if not c.gpos else end

    def _would_sub(self, tag, gids, any_context=False):
        """Would a lookup of GSUB feature `tag` substitute exactly this glyph sequence? Context rules count
        only without backtrack / lookahead, unless any_context (then a rule whose input matches and which
        applies a lookup counts whatever its context - what Windows does for consonant positions)."""
        key = (tag, gids, any_context)
        hit = self._would.get(key)
        if hit is not None:
            return hit
        res = False
        tab = self.font.gsub
        for li in self.gsub_map.get(tag, ()) if tab else ():
            L = tab.lookup(li)
            for sub in L.subs:
                t = L.type
                if t in (1, 2, 3, 8):
                    m = sub[1]
                    if len(gids) == 1 and gids[0] in m:
                        res = True
                elif t == 4:
                    for lg, comps in sub[1].get(gids[0], ()):
                        if comps == tuple(gids[1:]):
                            res = True
                            break
                elif t in (5, 6):
                    kind = sub[0]
                    if kind == "c1":
                        k = sub[1].get(gids[0])
                        if k is not None and k < len(sub[2]):
                            res = any(((not b and not lk) or (any_context and r)) and inp == tuple(gids[1:])
                                      for b, inp, lk, r in sub[2][k])
                    elif kind == "c2":
                        _, cov, bcd, icd, lcd, sets = sub
                        if gids[0] in cov:
                            k = icd.get(gids[0], 0)
                            if k < len(sets):
                                cls = tuple(icd.get(x, 0) for x in gids[1:])
                                res = any(((not b and not lk) or (any_context and r)) and inp == cls
                                          for b, inp, lk, r in sets[k])
                    elif kind == "c3":
                        _, back, inp, look, r = sub
                        res = (((not back and not look) or (any_context and r)) and len(inp) == len(gids)
                               and all(gg in cv for gg, cv in zip(gids, inp)))
                if res:
                    break
            if res:
                break
        self._would[key] = res
        return res

    # -- Indic -------------------------------------------------------------------------------------
    def _indic_setup(self, buf):
        cfg = _INDIC_CONFIG.get(self.script, _INDIC_CONFIG["Devanagari"])
        self.cfg = cfg
        self.old_spec = cfg[0] and not (self.gsub_tag or "").endswith("2")
        for gi in buf:
            gi.cat, gi.pos = _indic_category(gi.cp)
        self._syllables(buf, _INDIC_SYLLABLES)

    def _syllables(self, buf, grammar):
        cats = "".join(gi.cat for gi in buf)
        self.syl_type = {}
        i, serial = 0, 0
        n = len(cats)
        while i < n:
            best, btype = 0, "other"
            for typ, rx in grammar:
                m = rx.match(cats, i)
                if m and m.end() - i > best:
                    best, btype = m.end() - i, typ
            if best == 0:
                best, btype = 1, "other"
            serial += 1
            for k in range(i, i + best):
                buf[k].syl = serial
            self.syl_type[serial] = btype
            i += best

    def _syllable_ranges(self, buf):
        out = []
        i, n = 0, len(buf)
        while i < n:
            j = i + 1
            while j < n and buf[j].syl == buf[i].syl:
                j += 1
            out.append((i, j))
            i = j
        return out

    def _insert_dotted_circles(self, buf, repha_cat):
        """Broken clusters (a mark with no base) get U+25CC as base, as Windows and HarfBuzz do."""
        if not self.font.glyph(0x25CC):
            return buf
        out = []
        last = None
        for gi in buf:
            if gi.syl != last:
                last = gi.syl
                if self.syl_type.get(gi.syl) == "broken":
                    d = self._new_gi(0x25CC, gi.cl, "")
                    d.cat, d.pos, d.syl = "D", POS_BASE_C, gi.syl
                    if repha_cat and gi.cat == repha_cat:
                        out += [gi, d]
                        continue
                    out.append(d)
            out.append(gi)
        return out

    @staticmethod
    def _ligated(gi):
        return bool(gi.fl & _LIG)

    def _is(self, gi, cats):
        return not (gi.fl & _LIG) and gi.cat in cats

    def _indic_initial(self, buf):
        cfg = self.cfg
        virama = self.font.glyph(cfg[1])
        if virama:
            for gi in buf:
                if gi.pos == POS_BASE_C:
                    gi.pos = self._consonant_position(gi.g, virama)
        for start, end in self._syllable_ranges(buf):
            if self.syl_type.get(buf[start].syl) in ("consonant", "vowel", "standalone", "broken"):
                self._indic_initial_syllable(buf, start, end)

    def _consonant_position(self, g, virama):
        ctx = self.any_context_positions

        def W(tag, gids):
            return self._would_sub(tag, gids, ctx)
        if W("blwf", (virama, g)) or W("blwf", (g, virama)) or W("vatu", (virama, g)) or W("vatu", (g, virama)):
            return POS_BELOW_C
        if W("pstf", (virama, g)) or W("pstf", (g, virama)):
            return POS_POST_C
        if W("pref", (virama, g)) or W("pref", (g, virama)):
            return POS_POST_C
        return POS_BASE_C

    def _indic_initial_syllable(self, buf, start, end):
        cfg = self.cfg
        old_spec = self.old_spec
        base_pos, reph_mode, blwf_mode = cfg[2], cfg[4], cfg[5]
        bits = self.bits
        is_cons = lambda gi: self._is(gi, _CONS_FLAGS)
        is_joiner = lambda gi: self._is(gi, "jn")
        base = end
        has_reph = False
        limit = start
        if "rphf" in self.gsub_map and start + 3 <= end and (
                (reph_mode == "implicit" and not is_joiner(buf[start + 2])) or
                (reph_mode == "explicit" and buf[start + 2].cat == "j")):
            g3 = (buf[start].g, buf[start + 1].g)
            if self._would_sub("rphf", g3) or (reph_mode == "explicit" and
                                                self._would_sub("rphf", g3 + (buf[start + 2].g,))):
                limit += 2
                while limit < end and is_joiner(buf[limit]):
                    limit += 1
                base = start
                has_reph = True
        elif reph_mode == "log_repha" and buf[start].cat == "r":
            limit += 1
            while limit < end and is_joiner(buf[limit]):
                limit += 1
            base = start
            has_reph = True
        if base_pos == "last":
            i = end
            seen_below = False
            while True:
                i -= 1
                gi = buf[i]
                if is_cons(gi):
                    if gi.pos != POS_BELOW_C and (gi.pos != POS_POST_C or seen_below):
                        base = i
                        break
                    if gi.pos == POS_BELOW_C:
                        seen_below = True
                    base = i
                else:
                    if start < i and gi.cat == "j" and buf[i - 1].cat == "H":
                        break
                if i <= limit:
                    break
        else:  # Sinhala
            if not has_reph:
                base = limit
            for i in range(limit, end):
                if is_cons(buf[i]):
                    if limit < i and buf[i - 1].cat == "j":
                        break
                    base = i
            for i in range(base + 1, end):
                if is_cons(buf[i]):
                    buf[i].pos = POS_BELOW_C
        if has_reph and base == start and limit - base <= 2:
            has_reph = False

        for i in range(start, base):
            buf[i].pos = min(POS_PRE_C, buf[i].pos)
        if base < end:
            buf[base].pos = POS_BASE_C
        for i in range(base + 1, end):
            if buf[i].cat == "M":
                for j in range(i + 1, end):
                    if is_cons(buf[j]):
                        buf[j].pos = POS_FINAL_C
                        break
                break
        if has_reph:
            buf[start].pos = POS_RA_TO_BECOME_REPH
        if old_spec:
            disallow_double = self.script == "Kannada"
            for i in range(base + 1, end):
                if buf[i].cat == "H":
                    j = end - 1
                    while j > i:
                        if is_cons(buf[j]) or (disallow_double and buf[j].cat == "H"):
                            break
                        j -= 1
                    if buf[j].cat != "H" and j > i:
                        t = buf.pop(i)
                        buf.insert(j, t)
                    break
        last_pos = POS_START
        for i in range(start, end):
            gi = buf[i]
            if gi.cat in "jnNH":
                gi.pos = last_pos
                if gi.cat == "H" and gi.pos == POS_PRE_M:
                    for j in range(i, start, -1):
                        if buf[j - 1].pos != POS_PRE_M:
                            gi.pos = buf[j - 1].pos
                            break
            elif gi.pos != POS_SMVD:
                last_pos = gi.pos
        last = base
        for i in range(base + 1, end):
            if is_cons(buf[i]):
                for j in range(last + 1, i):
                    if buf[j].pos < POS_SMVD:
                        buf[j].pos = buf[i].pos
                last = i
            elif buf[i].cat == "M":
                last = i
        # stable sort by position
        seg = buf[start:end]
        order = sorted(range(len(seg)), key=lambda k: seg[k].pos)
        seg2 = [seg[k] for k in order]
        buf[start:end] = seg2
        base = end
        first_lm = last_lm = end
        for i in range(start, end):
            if buf[i].pos == POS_BASE_C:
                base = i
                break
            if buf[i].pos == POS_PRE_M:
                if first_lm == end:
                    first_lm = i
                last_lm = i
        if first_lm < last_lm:
            buf[first_lm:last_lm + 1] = buf[first_lm:last_lm + 1][::-1]
            i = first_lm
            for j in range(i, last_lm + 1):
                if buf[j].cat == "M":
                    if j > i:
                        buf[i:j + 1] = buf[i:j + 1][::-1]
                    i = j + 1
        if old_spec:
            _merge_clusters(buf, base, end)
        else:
            seen = [False] * len(order)
            for k in range(max(base, start) - start, end - start):
                if seen[k]:
                    continue
                seen[k] = True
                mn = mx = k
                j = order[k]
                while j != k and not seen[j]:
                    seen[j] = True
                    mn, mx = min(mn, j), max(mx, j)
                    j = order[j]
                if mn != mx:
                    _merge_clusters(buf, max(base, start + mn), start + mx + 1)
        # masks
        for i in range(start, end):
            if buf[i].pos == POS_RA_TO_BECOME_REPH:
                buf[i].mask |= bits["rphf"]
            else:
                break
        pre = bits["half"]
        if not old_spec and blwf_mode == "pre_and_post":
            pre |= bits["blwf"]
        for i in range(start, base):
            buf[i].mask |= pre
        post = bits["blwf"] | bits["abvf"] | bits["pstf"]
        for i in range(base + 1, end):
            buf[i].mask |= post
        if "pref" in self.gsub_map and base + 2 < end:
            for i in range(base + 1, end - 1):
                if self._would_sub("pref", (buf[i].g, buf[i + 1].g)):
                    buf[i].mask |= bits["pref"]
                    buf[i + 1].mask |= bits["pref"]
                    break
        for i in range(base + 1, end):
            if is_joiner(buf[i]):
                non_joiner = buf[i].cat == "n"
                j = i
                while True:
                    j -= 1
                    if non_joiner:
                        buf[j].mask &= ~bits["half"]
                    if not (j > start and not is_cons(buf[j])):
                        break

    def _indic_final(self, buf):
        for start, end in self._syllable_ranges(buf):
            if self.syl_type.get(buf[start].syl) in ("consonant", "vowel", "standalone", "broken"):
                self._indic_final_syllable(buf, start, end)

    def _indic_final_syllable(self, buf, start, end):
        bits = self.bits
        script = self.script
        reph_pos = self.cfg[3]
        virama = self.font.glyph(self.cfg[1])
        is_h = lambda gi: self._is(gi, "H")
        is_cons = lambda gi: self._is(gi, _CONS_FLAGS)
        is_joiner = lambda gi: self._is(gi, "jn")
        if virama:
            for i in range(start, end):
                gi = buf[i]
                if gi.g == virama and gi.fl & _LIG and gi.fl & _MULT:
                    gi.cat = "H"
                    gi.fl &= ~(_LIG | _MULT)
        try_pref = "pref" in self.gsub_map
        base = start
        while base < end:
            if buf[base].pos >= POS_BASE_C:
                if try_pref and base + 1 < end:
                    for i in range(base + 1, end):
                        if buf[i].mask & bits["pref"]:
                            if not (buf[i].fl & _SUBST and buf[i].fl & _LIG and not buf[i].fl & _MULT):
                                base = i
                                while base < end and is_h(buf[base]):
                                    base += 1
                                if base < end:
                                    buf[base].pos = POS_BASE_C
                                try_pref = False
                            break
                if script in self.skip_unformed_below:
                    i = base + 1
                    while i < end:
                        while i < end and is_joiner(buf[i]):
                            i += 1
                        if i == end or not is_h(buf[i]):
                            break
                        i += 1
                        while i < end and is_joiner(buf[i]):
                            i += 1
                        if i < end and is_cons(buf[i]) and buf[i].pos == POS_BELOW_C:
                            base = i
                            buf[base].pos = POS_BASE_C
                        i += 1
                if start < base < end and buf[base].pos > POS_BASE_C:
                    base -= 1
                break
            base += 1
        if base == end and start < base and self._is(buf[base - 1], "j"):
            base -= 1
        if base < end:
            while start < base and self._is(buf[base], "NH"):
                base -= 1
        # pre-base matra
        if start + 1 < end and start < base:
            new_pos = base - 2 if base == end else base - 1
            if script not in ("Malayalam", "Tamil"):
                while True:
                    while new_pos > start and not self._is(buf[new_pos], "MH"):
                        new_pos -= 1
                    if is_h(buf[new_pos]) and buf[new_pos].pos != POS_PRE_M:
                        if new_pos + 1 < end and buf[new_pos + 1].cat == "j" and new_pos > start:
                            new_pos -= 1
                            continue
                    else:
                        new_pos = start
                    break
            if start < new_pos and buf[new_pos].pos != POS_PRE_M:
                i = new_pos
                while i > start:
                    if buf[i - 1].pos == POS_PRE_M:
                        old = i - 1
                        if old < base <= new_pos:
                            base -= 1
                        t = buf.pop(old)
                        buf.insert(new_pos, t)
                        _merge_clusters(buf, new_pos, min(end, base + 1))
                        new_pos -= 1
                    i -= 1
            else:
                for i in range(start, base):
                    if buf[i].pos == POS_PRE_M:
                        _merge_clusters(buf, i, min(end, base + 1))
                        break
        # reph
        if start + 1 < end and buf[start].pos == POS_RA_TO_BECOME_REPH and \
                ((buf[start].cat == "r") ^ bool(buf[start].fl & _LIG and not buf[start].fl & _MULT)):
            new_reph = None
            if reph_pos != POS_AFTER_POST:
                p = start + 1
                while p < base and not is_h(buf[p]):
                    p += 1
                if p < base and is_h(buf[p]):
                    if p + 1 < base and is_joiner(buf[p + 1]):
                        p += 1
                    new_reph = p
                if new_reph is None and reph_pos == POS_AFTER_MAIN:
                    p = base
                    while p + 1 < end and buf[p + 1].pos <= POS_AFTER_MAIN:
                        p += 1
                    if p < end:
                        new_reph = p
                if new_reph is None and reph_pos == POS_AFTER_SUB:
                    p = base
                    while p + 1 < end and buf[p + 1].pos not in (POS_POST_C, POS_AFTER_POST, POS_SMVD):
                        p += 1
                    if p < end:
                        new_reph = p
            if new_reph is None:
                p = start + 1
                while p < base and not is_h(buf[p]):
                    p += 1
                if p < base and is_h(buf[p]):
                    if p + 1 < base and is_joiner(buf[p + 1]):
                        p += 1
                    new_reph = p
            if new_reph is None:
                p = end - 1
                while p > start and buf[p].pos == POS_SMVD:
                    p -= 1
                if is_h(buf[p]):
                    for i in range(base + 1, p):
                        if buf[i].cat == "M":
                            p -= 1
                new_reph = p
            _merge_clusters(buf, start, new_reph + 1)
            t = buf.pop(start)
            buf.insert(new_reph, t)
            if start < base <= new_reph:
                base -= 1
        # pre-base-reordering consonant
        if try_pref and base + 1 < end:
            for i in range(base + 1, end):
                if buf[i].mask & bits["pref"]:
                    gi = buf[i]
                    if gi.fl & _LIG and not gi.fl & _MULT:
                        new_pos = base
                        if script not in ("Malayalam", "Tamil"):
                            while new_pos > start and not self._is(buf[new_pos - 1], "MH"):
                                new_pos -= 1
                        if new_pos > start and is_h(buf[new_pos - 1]):
                            if new_pos < end and is_joiner(buf[new_pos]):
                                new_pos += 1
                        old = i
                        _merge_clusters(buf, new_pos, old + 1)
                        t = buf.pop(old)
                        buf.insert(new_pos, t)
                        if new_pos <= base < old:
                            base += 1
                    break
        if buf[start].pos == POS_PRE_M:
            if start == 0 or unicodedata.category(chr(buf[start - 1].cp)) not in ("Cf", "Cn", "Co", "Cs", "Ll", "Lm", "Lo", "Lt", "Lu", "Mc", "Me", "Mn"):
                buf[start].mask |= bits["init"]

    # -- Khmer ---------------------------------------------------------------------------------------
    def _khmer_setup(self, buf):
        for gi in buf:
            gi.cat = _khmer_category(gi.cp)
        # syllable: a base (C R V P D) not preceded by Coeng starts one; marks, Coeng + consonant,
        # register shifters, Robat and joiners continue it.
        serial = 0
        self.syl_type = {}
        prev = None
        for gi in buf:
            c = gi.cat
            if prev is None or c == "X" or prev.cat == "X" or (c in "CRVPD" and prev.cat != "H"):
                serial += 1
                self.syl_type[serial] = "consonant" if c in "CRVPD" else ("other" if c == "X" else "broken")
            gi.syl = serial
            prev = gi

    def _khmer_reorder(self, buf):
        bits = self.bits
        for start, end in self._syllable_ranges(buf):
            if self.syl_type.get(buf[start].syl) != "consonant":
                continue
            post = bits["blwf"] | bits["abvf"] | bits["pstf"]
            for i in range(start + 1, end):
                buf[i].mask |= post
            num_coengs = 0
            i = start + 1
            while i < end:
                gi = buf[i]
                if gi.cat == "H" and num_coengs <= 2 and i + 1 < end:
                    num_coengs += 1
                    if buf[i + 1].cat == "R":
                        buf[i].mask |= bits["pref"]
                        buf[i + 1].mask |= bits["pref"]
                        _merge_clusters(buf, start, i + 2)
                        t0, t1 = buf[i], buf[i + 1]
                        del buf[i:i + 2]
                        buf[start:start] = [t0, t1]
                        for j in range(i + 2, end):
                            buf[j].mask |= bits["cfar"]
                        num_coengs = 2
                elif gi.cat == "L":
                    _merge_clusters(buf, start, i + 1)
                    t = buf.pop(i)
                    buf.insert(start, t)
                i += 1

    # -- Myanmar -------------------------------------------------------------------------------------
    def _myanmar_setup(self, buf):
        for gi in buf:
            gi.cat = _myanmar_category(gi.cp)
        serial = 0
        self.syl_type = {}
        n = len(buf)
        i = 0
        while i < n:
            c = buf[i].cat
            serial += 1
            j = i + 1
            if c in "CRVPD":
                while j < n:
                    cj = buf[j].cat
                    if cj in "CRVPD":
                        if buf[j - 1].cat == "H":       # stacked consonant (virama 1039)
                            j += 1
                            continue
                        break
                    if cj == "X":
                        break
                    j += 1
                typ = "consonant"
            elif c == "X":
                typ = "other"
            else:
                while j < n and buf[j].cat not in "CRVPDX":
                    j += 1
                typ = "broken"
            self.syl_type[serial] = typ
            for k in range(i, j):
                buf[k].syl = serial
            i = j

    def _myanmar_reorder(self, buf):
        for start, end in self._syllable_ranges(buf):
            if self.syl_type.get(buf[start].syl) != "consonant":
                continue
            base = end
            has_reph = False
            limit = start
            if start + 3 <= end and buf[start].cat == "R" and buf[start + 1].cat == "a" and buf[start + 2].cat == "H":
                limit += 3
                base = start
                has_reph = True
            if not has_reph:
                base = limit
            for i in range(limit, end):
                if buf[i].cat in _CONS_FLAGS:
                    base = i
                    break
            i = start
            while i < start + (3 if has_reph else 0):
                buf[i].pos = POS_AFTER_MAIN
                i += 1
            while i < base:
                buf[i].pos = POS_PRE_C
                i += 1
            if i < end:
                buf[i].pos = POS_BASE_C
                i += 1
            pos = POS_AFTER_MAIN
            while i < end:
                c = buf[i].cat
                if c == "q":
                    buf[i].pos = POS_PRE_C
                elif c == "L":
                    buf[i].pos = POS_PRE_M
                elif c == "v":
                    buf[i].pos = buf[i - 1].pos
                elif pos == POS_AFTER_MAIN and c == "B":
                    pos = POS_BELOW_C
                    buf[i].pos = pos
                elif pos == POS_BELOW_C and c == "A":
                    buf[i].pos = POS_BEFORE_SUB
                elif pos == POS_BELOW_C and c == "B":
                    buf[i].pos = pos
                elif pos == POS_BELOW_C and c != "A":
                    pos = POS_AFTER_SUB
                    buf[i].pos = pos
                else:
                    buf[i].pos = pos
                i += 1
            seg = buf[start:end]
            order = sorted(range(len(seg)), key=lambda k: seg[k].pos)
            if order != list(range(len(seg))):
                buf[start:end] = [seg[k] for k in order]
                _merge_clusters(buf, start, end)

    # -- Arabic --------------------------------------------------------------------------------------
    def _arabic_masks(self, buf):
        bits = self.bits
        types = [joining_type(gi.cp) for gi in buf]
        n = len(buf)
        res = [None] * n
        prev = -1
        for i in range(n):
            t = types[i]
            if t == "T":
                continue
            jp = prev >= 0 and types[prev] in ("D", "C") and t in ("D", "R", "C")
            if jp and res[prev] is not None:
                res[prev] = {"isol": "init", "fina": "medi"}.get(res[prev], res[prev])
            res[i] = None if t in ("U", "C") else ("fina" if jp else "isol")
            prev = i
        for i, f in enumerate(res):
            if f and f in bits:
                buf[i].mask |= bits[f]

    # -- driver --------------------------------------------------------------------------------------
    def run(self, text):
        gsub_st, gpos_st = self._plan()
        for t in ("rphf", "half", "pref", "blwf", "abvf", "pstf", "init", "cfar"):
            self.bits.setdefault(t, 0)
        buf = self._buffer(text)
        m = self.model
        if m == "indic":
            self._indic_setup(buf)
            buf = self._insert_dotted_circles(buf, "r")
        elif m == "khmer":
            self._khmer_setup(buf)
            buf = self._insert_dotted_circles(buf, None)
        elif m == "myanmar":
            self._myanmar_setup(buf)
            buf = self._insert_dotted_circles(buf, None)
        elif m == "arabic":
            self._arabic_masks(buf)
        for tag, val in self.user_features.items():
            if not val:
                for st in gsub_st + gpos_st:
                    st[0][:] = [f for f in st[0] if f[0] != tag]
        if self.font.gsub:
            for feats, pause in gsub_st:
                self._apply_stage(buf, self.font.gsub, self.gsub_map, feats, False)
                if pause is not None:
                    pause(buf)
        for gi in buf:
            gi.xa = self.font.advance(gi.g)
            gi.ya = gi.xo = gi.yo = 0
            gi.att = -1
        if m in ("myanmar", "use"):
            for gi in buf:
                if gi.props == _MARK:
                    gi.xa = 0
        if self.font.gpos:
            for feats, pause in gpos_st:
                self._apply_stage(buf, self.font.gpos, self.gpos_map, feats, True)
        if m in ("default", "arabic"):
            for gi in buf:
                if gi.props == _MARK:
                    gi.xa = 0
        fwd = not self.rtl
        for i in range(len(buf)):
            self._propagate(buf, i, fwd, 0)
        # default ignorables (ZWJ, ZWNJ, bidi controls ...) become a zero-width space glyph, as Windows
        # and HarfBuzz do; without a space glyph they are removed.
        space = self.font.glyph(0x20)
        out = []
        for gi in buf:
            if gi.ign:
                if not space:
                    continue
                gi.g, gi.xa, gi.ya, gi.xo, gi.yo = space, 0, 0, 0, 0
            out.append(gi)
        buf = out
        if self.rtl:
            buf.reverse()
        return buf

    def _propagate(self, buf, i, fwd, depth):
        gi = buf[i]
        j = gi.att
        if j < 0 or j >= len(buf):
            gi.att = -1
            return
        gi.att = -1
        if depth < 32:
            self._propagate(buf, j, fwd, depth + 1)
        gj = buf[j]
        if gi.att_t == 2:   # cursive: y offsets chain
            gi.yo += gj.yo
            return
        gi.xo += gj.xo
        gi.yo += gj.yo
        if fwd:
            for k in range(j, i):
                gi.xo -= buf[k].xa
                gi.yo -= buf[k].ya
        else:
            for k in range(j + 1, i + 1):
                gi.xo += buf[k].xa
                gi.yo += buf[k].ya


def shape_run(font, text, script=None, lang=None, direction=None, features=None):
    """Shape one run of a single script with one font -> ShapeResult(glyphs, texts, model, script_tag).

    font: Font or a path. script: Unicode script name as used by polyglot_engine ("Devanagari", ...);
    guessed from the text when None. direction: "ltr" / "rtl" (default from the script).
    features: {tag: False} switches features off. texts[k] are the characters glyph k was formed from
    ("" for the 2nd.. glyph of a multiple substitution or an inserted dotted circle)."""
    if not isinstance(font, Font):
        font = load_font(os.fspath(font))
    if script is None:
        script = _guess_script(text)
    if direction is None:
        direction = "rtl" if script in _RTL else "ltr"
    sh = _Shaper(font, script, lang, direction, features)
    buf = sh.run(text)
    glyphs = [ShapedGlyph(gi.g, gi.xa, gi.xo, gi.yo, gi.cl) for gi in buf]
    return ShapeResult(glyphs, [gi.src for gi in buf], sh.model, sh.gsub_tag)


def shape(font, text, script=None, lang=None, direction=None, features=None):
    """-> list of ShapedGlyph(glyph_id, x_advance, x_offset, y_offset, cluster), font units, visual order."""
    return shape_run(font, text, script, lang, direction, features).glyphs


def _main(argv):
    import argparse
    ap = argparse.ArgumentParser(description="Stark Covenant OpenType shaper")
    ap.add_argument("font")
    ap.add_argument("text")
    ap.add_argument("--script")
    ap.add_argument("--lang")
    ap.add_argument("--index", type=int, default=0)
    a = ap.parse_args(argv)
    f = Font(a.font, a.index)
    r = shape_run(f, a.text, a.script, a.lang)
    sys.stdout.reconfigure(encoding="utf-8")
    print("model %s, script tag %s" % (r.model, r.script_tag))
    for g, src in zip(r.glyphs, r.texts):
        print("gid %5d adv %5d off (%5d,%5d) cluster %d  %s" % (g.glyph_id, g.x_advance, g.x_offset, g.y_offset,
                                                                g.cluster, " ".join("U+%04X" % ord(ch) for ch in src)))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
