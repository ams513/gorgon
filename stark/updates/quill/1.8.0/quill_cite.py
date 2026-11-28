"""Stark Quill sources and citations: a list of the books, articles, websites and films a piece of writing draws
on, written out in MLA (9th edition), APA (7th edition) or Chicago (17th edition, notes and bibliography).

The list lives in Sources.json in the book's folder (plain JSON). This module only cleans records and formats
text: in-text citations, Chicago notes and the reference list. Titles come back wrapped in *asterisks* so Quill's
exports set them in italics. Rules only: no network, no AI, no file access. Formats cover the common cases of
each style; a style guide or a teacher has the last word on edge cases.
"""
from __future__ import annotations

import re
import uuid

STYLES = {"mla": "MLA 9", "apa": "APA 7", "chicago": "Chicago 17 (notes and bibliography)"}
LIST_TITLE = {"mla": "Works Cited", "apa": "References", "chicago": "Bibliography"}
KINDS = {"book": "Book", "article": "Journal article", "chapter": "Chapter in an edited book",
         "web": "Web page", "news": "Newspaper or magazine article", "film": "Film or video"}
FIELDS = ("authors", "title", "container", "editors", "edition", "publisher", "place", "year", "month", "day",
          "volume", "issue", "pages", "url", "doi", "accessed", "note")
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
MLA_MONTHS = ["Jan.", "Feb.", "Mar.", "Apr.", "May", "June", "July", "Aug.", "Sept.", "Oct.", "Nov.", "Dec."]
MAX_SOURCES = 1000


def _txt(v, n=600):
    return re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", str(v if v is not None else ""))).strip()[:n]


def clean_source(d, old=None):
    """A source record with known fields only. Raises ValueError when it has neither a title nor an author."""
    d = d if isinstance(d, dict) else {}
    out = dict(old or {})
    out["id"] = (old or {}).get("id") or _txt(d.get("id"), 40) or uuid.uuid4().hex[:12]
    kind = d.get("kind", out.get("kind", "book"))
    if kind not in KINDS:
        raise ValueError("Kind must be one of: %s." % ", ".join(KINDS))
    out["kind"] = kind
    for f in FIELDS:
        if f in d:
            out[f] = _txt(d[f], 2000 if f == "note" else 600)
    if not out.get("title") and not out.get("authors"):
        raise ValueError("Give the source a title or an author.")
    if out.get("year") and not re.fullmatch(r"\d{1,4}|n\.d\.|forthcoming", out["year"], re.I):
        raise ValueError("The year should be a number such as 2021.")
    if out.get("month"):
        m = out["month"].strip().lower()
        idx = next((i for i, name in enumerate(MONTHS) if name.lower().startswith(m[:3]) and len(m) >= 3), None)
        if idx is None and m.isdigit() and 1 <= int(m) <= 12:
            idx = int(m) - 1
        if idx is None:
            raise ValueError("The month should be a month name or 1 to 12.")
        out["month"] = MONTHS[idx]
    if out.get("day") and not (out["day"].isdigit() and 1 <= int(out["day"]) <= 31):
        raise ValueError("The day should be 1 to 31.")
    if out.get("doi"):
        out["doi"] = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", out["doi"], flags=re.I)
    return out


# ------------------------------------------------------------------------------------------------ names
def people(s):
    """'Le Guin, Ursula K.; Smith, Zadie' or 'Ursula K. Le Guin and Zadie Smith' -> [(last, first)].
    A name with no comma and no space (or an organisation in {braces}) stays whole: [('NASA', '')]."""
    s = _txt(s)
    if not s:
        return []
    parts = [p.strip() for p in re.split(r"\s*;\s*|\s+and\s+|\s*&\s*", s) if p.strip()]
    if len(parts) == 1 and s.count(",") >= 3:
        bits = [b.strip() for b in s.split(",")]
        parts = [bits[i] + ", " + bits[i + 1] for i in range(0, len(bits) - 1, 2)]
    out = []
    for p in parts:
        if p.startswith("{") and p.endswith("}"):
            out.append((p[1:-1].strip(), ""))
        elif "," in p:
            last, first = p.split(",", 1)
            out.append((last.strip(), first.strip()))
        elif " " in p:
            bits = p.split()
            k = len(bits) - 1
            while k > 1 and bits[k - 1].lower() in ("van", "von", "de", "der", "da", "di", "le", "la", "du", "del"):
                k -= 1
            out.append((" ".join(bits[k:]), " ".join(bits[:k])))
        else:
            out.append((p, ""))
    return out


def initials(first):
    bits = [b for b in re.split(r"[\s.]+", first) if b]
    out = []
    for b in bits:
        if "-" in b:
            out.append("-".join(x[0].upper() + "." for x in b.split("-") if x))
        else:
            out.append(b[0].upper() + ".")
    return " ".join(out)


def _full(p):
    return (p[1] + " " + p[0]).strip()


def _inv(p):
    return p[0] + (", " + p[1] if p[1] else "")


def _apa(p):
    return p[0] + (", " + initials(p[1]) if p[1] else "")


def _join(items, conj="and", oxford=True):
    if len(items) <= 1:
        return "".join(items)
    if len(items) == 2:
        return "%s %s %s" % (items[0], conj, items[1])
    return ", ".join(items[:-1]) + ("," if oxford else "") + " %s %s" % (conj, items[-1])


def _end(s, mark="."):
    s = s.rstrip()
    if not s:
        return s
    if s.endswith(("*", "\"", "”")):
        core = s.rstrip("*\"”")
        if core.endswith((".", "?", "!")):
            return s
    return s if s.endswith((".", "?", "!")) else s + mark


def _it(s):
    return "*%s*" % s.strip() if s and s.strip() else ""


def _q(s, inner=""):
    """A title in quotation marks, with the full stop or comma inside them (US style)."""
    s = s.strip()
    if not s:
        return ""
    if s.endswith(("?", "!")):
        return "“%s”" % s
    return "“%s%s”" % (s, inner)


def _sentence_case(s):
    """APA titles: first word, the first word after a colon and proper-looking words keep their capitals."""
    words = s.split(" ")
    out = []
    cap_next = True
    for w in words:
        if cap_next:
            out.append(w[:1].upper() + w[1:])
        elif w.isupper() and len(w) > 1 or re.search(r"[A-Z].*[A-Z]", w[1:]):
            out.append(w)
        else:
            out.append(w.lower() if w[:1].isupper() and w[1:].islower() else w)
        cap_next = w.endswith((":", "?", "!"))
    return " ".join(out)


def _pages(s, prefix=True):
    s = (s or "").replace("--", "-").replace("–", "-").strip()
    if not s:
        return ""
    if not prefix:
        return s.replace("-", "–")
    return ("pp. " if "-" in s or "," in s else "p. ") + s.replace("-", "–")


def _url(src):
    if src.get("doi"):
        return "https://doi.org/" + src["doi"]
    return src.get("url", "")


def _mla_date(src):
    bits = []
    if src.get("day"):
        bits.append(src["day"])
    if src.get("month"):
        bits.append(MLA_MONTHS[MONTHS.index(src["month"])])
    if src.get("year"):
        bits.append(src["year"])
    return " ".join(bits)


# ------------------------------------------------------------------------------------------------ entries
def entry(src, style):
    """One reference-list entry in the given style, as plain text with *italic* titles."""
    if style not in STYLES:
        raise ValueError("Unknown citation style.")
    return {"mla": _mla, "apa": _apa_entry, "chicago": _chicago}[style](src)


def _mla_authors(ps):
    if not ps:
        return ""
    if len(ps) == 1:
        return _inv(ps[0])
    if len(ps) == 2:
        return "%s, and %s" % (_inv(ps[0]), _full(ps[1]))
    return _inv(ps[0]) + ", et al."


def _mla(src):
    k = src["kind"]
    ps = people(src.get("authors"))
    eds = people(src.get("editors"))
    title = src.get("title", "")
    parts = []
    a = _mla_authors(ps)
    if a:
        parts.append(_end(a))
    if k in ("book", "film"):
        parts.append(_end(_it(title)))
    elif title:
        parts.append(_q(title, "."))
    cont = []
    if k == "film" and eds:
        cont.append("directed by " + _join([_full(p) for p in eds]))
    if k != "book" and k != "film" and src.get("container"):
        cont.append(_it(src["container"]))
    if k == "chapter" and eds:
        cont.append("edited by " + _join([_full(p) for p in eds]))
    if k == "book" and eds:
        cont.append("edited by " + _join([_full(p) for p in eds]))
    if src.get("edition"):
        ed = src["edition"]
        cont.append(ed if re.search(r"ed\.?$", ed) else ed + " ed.")
    if src.get("volume"):
        cont.append("vol. " + src["volume"])
    if src.get("issue"):
        cont.append("no. " + src["issue"])
    if src.get("publisher") and not (k == "web" and src.get("publisher") == src.get("container")):
        cont.append(src["publisher"])
    d = _mla_date(src)
    if d:
        cont.append(d)
    if src.get("pages"):
        cont.append(_pages(src["pages"]))
    u = _url(src)
    if u:
        cont.append(re.sub(r"^https?://", "", u) if not src.get("doi") else u)
    if cont:
        c = ", ".join(cont)
        parts.append(_end(c[:1].upper() + c[1:] if c.startswith(("directed", "edited")) else c))
    if k == "web" and src.get("accessed"):
        parts.append("Accessed %s." % _day_text(src["accessed"], mla=True))
    return " ".join(p for p in parts if p).strip()


def _day_text(s, mla=False):
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s or "")
    if not m:
        return s
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not 1 <= mo <= 12:
        return s
    return "%d %s %d" % (d, MLA_MONTHS[mo - 1], y) if mla else "%s %d, %d" % (MONTHS[mo - 1], d, y)


def _apa_authors(ps):
    if not ps:
        return ""
    names = [_apa(p) for p in ps]
    if len(names) == 1:
        return names[0]
    if len(names) <= 20:
        return ", ".join(names[:-1]) + ", & " + names[-1]
    return ", ".join(names[:19]) + ", . . . " + names[-1]


def _apa_entry(src):
    k = src["kind"]
    ps = people(src.get("authors"))
    eds = people(src.get("editors"))
    year = src.get("year") or "n.d."
    if k in ("web", "news") and src.get("month"):
        year += ", " + src["month"] + (" " + src["day"] if src.get("day") else "")
    title = _sentence_case(src.get("title", ""))
    who = _apa_authors(ps)
    if k == "film" and eds:
        who = _join(["%s (Director)" % _apa(p) for p in eds], "&") if not ps else who
    parts = []
    if who:
        parts.append(_end(who) + " (%s)." % year)
    else:
        parts.append("")
    if k in ("book", "film"):
        t = _it(title + (" (%s)" % src["edition"] if src.get("edition") else ""))
        if src.get("edition"):
            t = _it(title) + " (%s)" % src["edition"]
        if k == "film":
            t += " [Film]"
        parts.append(_end(t))
    elif k == "chapter":
        parts.append(_end(title))
        ed_txt = ""
        if eds:
            ed_txt = "In %s (%s), " % (_join(["%s %s" % (initials(p[1]), p[0]) if p[1] else p[0] for p in eds], "&"),
                                       "Ed." if len(eds) == 1 else "Eds.")
        else:
            ed_txt = "In "
        pg = " (%s)" % _pages(src["pages"]) if src.get("pages") else ""
        parts.append(_end(ed_txt + _it(src.get("container", "")) + pg))
    elif k == "article":
        parts.append(_end(title))
        c = _it(src.get("container", ""))
        if src.get("volume"):
            c += ", " + _it(src["volume"]) + ("(%s)" % src["issue"] if src.get("issue") else "")
        if src.get("pages"):
            c += ", " + _pages(src["pages"], prefix=False)
        parts.append(_end(c))
    else:  # web, news
        parts.append(_end(_it(title) if k == "web" else title))
        if src.get("container") and k == "news":
            parts.append(_end(_it(src["container"])))
        elif src.get("container"):
            parts.append(_end(src["container"]))
    if not who:   # no author: the title moves to the front
        t = parts.pop(1)
        parts[0] = t.rstrip(".") + " (%s)." % year
    if src.get("publisher") and k in ("book", "chapter", "film"):
        parts.append(_end(src["publisher"]))
    u = _url(src)
    if u:
        parts.append(u)
    return " ".join(p for p in parts if p).strip()


def _chicago_authors(ps):
    if not ps:
        return ""
    if len(ps) == 1:
        return _inv(ps[0])
    if len(ps) == 2:
        return "%s, and %s" % (_inv(ps[0]), _full(ps[1]))
    if len(ps) <= 10:
        return _inv(ps[0]) + ", " + ", ".join(_full(p) for p in ps[1:-1]) + ", and " + _full(ps[-1])
    return ", ".join([_inv(ps[0])] + [_full(p) for p in ps[1:7]]) + ", et al."


def _chicago(src):
    k = src["kind"]
    ps = people(src.get("authors"))
    eds = people(src.get("editors"))
    title = src.get("title", "")
    parts = []
    a = _chicago_authors(ps)
    if k == "film" and eds and not ps:
        a = _inv(eds[0]) + ", dir."
    if a:
        parts.append(_end(a))
    pub = ""
    if src.get("place") and src.get("publisher"):
        pub = "%s: %s" % (src["place"], src["publisher"])
    elif src.get("publisher"):
        pub = src["publisher"]
    if k in ("book", "film"):
        parts.append(_end(_it(title)))
        if src.get("edition"):
            parts.append(_end(src["edition"] if re.search(r"ed\.?$", src["edition"]) else src["edition"] + " ed."))
        if eds and k == "book":
            parts.append("Edited by %s." % _join([_full(p) for p in eds]))
        tail = ", ".join(x for x in (pub, src.get("year", "")) if x)
        if tail:
            parts.append(_end(tail))
    elif k == "chapter":
        parts.append(_q(title, "."))
        inb = "In " + _it(src.get("container", ""))
        if eds:
            inb += ", edited by " + _join([_full(p) for p in eds])
        if src.get("pages"):
            inb += ", " + _pages(src["pages"], prefix=False)
        parts.append(_end(inb))
        tail = ", ".join(x for x in (pub, src.get("year", "")) if x)
        if tail:
            parts.append(_end(tail))
    elif k == "article":
        parts.append(_q(title, "."))
        c = _it(src.get("container", ""))
        if src.get("volume"):
            c += " " + src["volume"]
        if src.get("issue"):
            c += ", no. " + src["issue"]
        when = " ".join(x for x in (src.get("month", ""), src.get("year", "")) if x)
        if when:
            c += " (%s)" % when
        if src.get("pages"):
            c += ": " + _pages(src["pages"], prefix=False)
        parts.append(_end(c))
    else:  # web, news
        parts.append(_q(title, "."))
        if src.get("container"):
            parts.append(_end(_it(src["container"]) if k == "news" else src["container"]))
        when = (src.get("month", "") + (" " + src["day"] if src.get("day") else "")).strip()
        when = (when + ", " + src["year"]) if when and src.get("year") else (src.get("year") or when)
        if when:
            parts.append(_end(when))
        elif src.get("accessed"):
            parts.append("Accessed %s." % _day_text(src["accessed"]))
    u = _url(src)
    if u:
        parts.append(_end(u))
    return " ".join(p for p in parts if p).strip()


# ------------------------------------------------------------------------------------------------ in-text
_SMALL = frozenset("a an the of to and in on for at by with from or".split())


def _short_title(src):
    t = re.split(r"[:.?!]", src.get("title", ""))[0].strip()
    words = t.split()[:4]
    while len(words) > 1 and words[-1].lower() in _SMALL:
        words.pop()
    return " ".join(words) if words else "Untitled"


def in_text(src, style, locator=""):
    """MLA (Le Guin 23), APA (Le Guin, 1969, p. 23); Chicago gives a footnote instead (see note())."""
    ps = people(src.get("authors"))
    loc = _txt(locator, 40)
    if style == "mla":
        if not ps:
            who = _q(_short_title(src)) if src["kind"] not in ("book", "film") else _it(_short_title(src))
        elif len(ps) == 1:
            who = ps[0][0]
        elif len(ps) == 2:
            who = "%s and %s" % (ps[0][0], ps[1][0])
        else:
            who = ps[0][0] + " et al."
        return "(%s%s)" % (who, " " + loc if loc else "")
    if style == "apa":
        if not ps:
            who = _it(_short_title(src)) if src["kind"] in ("book", "film", "web") else _q(_short_title(src))
        elif len(ps) == 1:
            who = ps[0][0]
        elif len(ps) == 2:
            who = "%s & %s" % (ps[0][0], ps[1][0])
        else:
            who = ps[0][0] + " et al."
        pl = (", " + ("pp. " if "-" in loc else "p. ") + loc.replace("-", "–")) if loc and loc[:1].isdigit() else \
            (", " + loc if loc else "")
        return "(%s, %s%s)" % (who, src.get("year") or "n.d.", pl)
    if style == "chicago":
        return note(src, loc)
    raise ValueError("Unknown citation style.")


def note(src, locator="", short=False):
    """A Chicago footnote: full the first time, short (Le Guin, Left Hand, 23.) after that."""
    ps = people(src.get("authors"))
    loc = _txt(locator, 40)
    k = src["kind"]
    if short:
        who = ps[0][0] + (" et al." if len(ps) > 3 else " and " + ps[1][0] if len(ps) == 2 else "") if ps else ""
        t = _it(_short_title(src)) if k in ("book", "film") else _q(_short_title(src))
        return _end(", ".join(x for x in (who, t, loc) if x))
    if not ps:
        who = _full(people(src.get("editors"))[0]) + ", dir." if k == "film" and src.get("editors") else ""
    elif len(ps) == 1:
        who = _full(ps[0])
    elif len(ps) <= 3:
        who = _join([_full(p) for p in ps])
    else:
        who = _full(ps[0]) + " et al."
    title = src.get("title", "")
    if k in ("book", "film"):
        pub = ", ".join(x for x in ("%s: %s" % (src["place"], src["publisher"]) if src.get("place") and src.get("publisher")
                                    else src.get("publisher", ""), src.get("year", "")) if x)
        body = ", ".join(x for x in (who, _it(title)) if x) + (" (%s)" % pub if pub else "")
    elif k == "article":
        c = _it(src.get("container", "")) + (" " + src["volume"] if src.get("volume") else "") + \
            (", no. " + src["issue"] if src.get("issue") else "") + (" (%s)" % src["year"] if src.get("year") else "")
        body = ", ".join(x for x in (who, _q(title, ","), c) if x).replace("”,", "”")
    elif k == "chapter":
        body = ", ".join(x for x in (who, _q(title, ","), "in " + _it(src.get("container", ""))) if x).replace("”,", "”")
        pub = ", ".join(x for x in (src.get("publisher", ""), src.get("year", "")) if x)
        if pub:
            body += " (%s)" % pub
    else:
        body = ", ".join(x for x in (who, _q(title, ","), src.get("container", ""), src.get("year", "")) if x)
        body = body.replace("”,", "”")
    if loc:
        body += ", " + loc
    u = _url(src)
    if u and k in ("web", "news", "article"):
        body += ", " + u
    return _end(body)


# ------------------------------------------------------------------------------------------------ the list
def _sort_key(src):
    ps = people(src.get("authors"))
    head = ps[0][0] if ps else re.sub(r"^(the|a|an)\s+", "", src.get("title", ""), flags=re.I)
    return (head.lower(), src.get("year", ""), src.get("title", "").lower())


def bibliography(sources, style):
    """Sorted reference list: [entry text]. APA and Chicago repeat the author; MLA too (no dashes, plain text)."""
    if style not in STYLES:
        raise ValueError("Unknown citation style.")
    return [entry(s, style) for s in sorted(sources, key=_sort_key)]


def chapter(sources, style):
    """(title, markdown text) of the reference list, ready to add after the last chapter of an export."""
    items = bibliography(sources, style)
    return LIST_TITLE[style], "\n\n".join(items) + "\n"


def from_clips(clips):
    """Sources from the Library's research clips (research.json): one web source per page or book clipped."""
    out, seen = [], set()
    for c in clips or []:
        if not isinstance(c, dict):
            continue
        title = _txt(c.get("title") or c.get("source_name"))
        url = _txt(c.get("url"))
        if not title or (title, url) in seen:
            continue
        seen.add((title, url))
        src = {"kind": "web", "title": title, "container": _txt(c.get("source_name")), "url": url,
               "accessed": _txt(c.get("date"), 10)}
        try:
            out.append(clean_source(src))
        except ValueError:
            continue
    return out
