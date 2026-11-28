"""quill_export.py - manuscript export for Stark Quill v1.0.

Python standard library only (3.10+), zero dependencies.

Public API
----------
export(text_or_chapters, meta, fmt, out_path, **opts) -> out_path
    text_or_chapters : str (Markdown-lite) or list of (title, body) / {"title","body"}
    meta             : {"title", "author", "lang", "date"} (all optional)
    fmt              : 'txt' | 'md' | 'html' | 'epub' | 'docx' | 'pdf'
    opts (optional)  : page='a5'|'trade' (pdf/docx), family='serif'|'sans' (pdf),
                       font_size=11.0 (pdf), justify=True (pdf),
                       fallback_fonts=[paths] (pdf, overrides system search),
                       warnings=list (problems such as missing glyphs are appended)
validate(path) -> list of problem strings (empty list == valid) for epub/docx/pdf
pdf_extract_text(path) -> list of page texts (decodes our ToUnicode / WinAnsi strings)
parse_manuscript(text_or_chapters) -> list of Chapter
LAST_WARNINGS : warnings of the most recent export() call

Markdown-lite: '# Title' starts a chapter, blank line separates paragraphs,
'***' / '* * *' / '---' alone on a line is a scene break, *italic*, **bold**,
***bold italic***, backslash-escaped \\* is a literal asterisk.
"""

import datetime
import hashlib
import html
import io
import os
import re
import struct
import uuid
import zipfile
from quill_safezip import open_capped
import zlib

__all__ = ["export", "validate", "pdf_extract_text", "parse_manuscript",
           "Chapter", "LAST_WARNINGS", "FORMATS"]

FORMATS = ("txt", "md", "html", "epub", "docx", "pdf")
LAST_WARNINGS = []
APP_NAME = "Stark Quill 1.3"

# ---------------------------------------------------------------------------
# Manuscript model
# ---------------------------------------------------------------------------


class Chapter:
    """title: str ('' when untitled); blocks: list of ('p', runs) | ('break', None).
    runs: list of (text, bold, italic)."""
    __slots__ = ("title", "blocks")

    def __init__(self, title, blocks):
        self.title = title
        self.blocks = blocks

    def __repr__(self):
        return "Chapter(%r, %d blocks)" % (self.title, len(self.blocks))


_BAD_XML = re.compile("[^\x09\x0A\x0D\x20-\uD7FF\uE000-\uFFFD\U00010000-\U0010FFFF]")
_SCENE = re.compile(r"^\s*(\*\s*\*\s*\*[\s*]*|-{3,}|_{3,}|#{1}\s*#\s*#\s*)\s*$")
_HEAD = re.compile(r"^#{1,2}\s+(.*?)\s*#*\s*$")
_INLINE = re.compile(r"\*\*\*(?=\S)(.+?)(?<=\S)\*\*\*|\*\*(?=\S)(.+?)(?<=\S)\*\*|\*(?=\S)(.+?)(?<=\S)\*", re.S)
_ESC = "\uE000"  # private-use sentinel for an escaped asterisk


def _clean(s):
    if not isinstance(s, str):
        s = "" if s is None else str(s)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    return _BAD_XML.sub("", s)


def _parse_inline(s, bold=False, italic=False, out=None):
    if out is None:
        out = []
    pos = 0
    for m in _INLINE.finditer(s):
        if m.start() > pos:
            out.append((s[pos:m.start()], bold, italic))
        if m.group(1) is not None:
            _parse_inline(m.group(1), True, True, out)
        elif m.group(2) is not None:
            _parse_inline(m.group(2), True, italic, out)
        else:
            _parse_inline(m.group(3), bold, True, out)
        pos = m.end()
    if pos < len(s):
        out.append((s[pos:], bold, italic))
    return out


def _runs(text):
    text = text.replace("\\*", _ESC)
    runs = []
    for t, b, i in _parse_inline(text):
        t = t.replace(_ESC, "*")
        if not t:
            continue
        if runs and runs[-1][1] == b and runs[-1][2] == i:
            runs[-1] = (runs[-1][0] + t, b, i)
        else:
            runs.append((t, b, i))
    return runs


_SUBHEAD = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")


def _parse_blocks(text, allow_headings, subheads=False):
    """Return list of (title_or_None, blocks). A None title = text before any heading.
    subheads (chapter-list mode): '# ...' lines inside a chapter become ('sub', runs) blocks."""
    sections = [[None, []]]
    para = []

    def flush():
        if para:
            joined = " ".join(x.strip() for x in para)
            joined = re.sub(r"[ \t]+", " ", joined).strip()
            if joined:
                sections[-1][1].append(("p", _runs(joined)))
            del para[:]

    for line in text.split("\n"):
        if not line.strip():
            flush()
            continue
        if _SCENE.match(line):
            flush()
            sections[-1][1].append(("break", None))
            continue
        m = _HEAD.match(line) if allow_headings else None
        if m:
            flush()
            sections.append([m.group(1).strip().replace("\\*", "*"), []])
            continue
        m = _SUBHEAD.match(line) if subheads else None
        if m and m.group(1).strip():
            flush()
            sections[-1][1].append(("sub", _runs(m.group(1).strip())))
            continue
        para.append(line)
    flush()
    return sections


def parse_manuscript(text_or_chapters, subheads=False):
    """Normalise a manuscript into a list of Chapter objects."""
    chapters = []
    if isinstance(text_or_chapters, (str, bytes)):
        text = text_or_chapters
        if isinstance(text, bytes):
            text = text.decode("utf-8", "replace")
        text = _clean(text).lstrip("\ufeff")
        for title, blocks in _parse_blocks(text, True):
            if title is None and not blocks:
                continue
            chapters.append(Chapter(_inline_plain(title or ""), blocks))
    else:
        for item in text_or_chapters or []:
            if isinstance(item, dict):
                title, body = item.get("title", ""), item.get("body", "")
            else:
                title, body = item[0], item[1]
            blocks = []
            for t, bl in _parse_blocks(_clean(body), False, subheads):
                blocks.extend(bl)
            chapters.append(Chapter(_inline_plain(_clean(title).strip()), blocks))
    # drop leading/trailing/double scene breaks inside a chapter
    for ch in chapters:
        bl = []
        for b in ch.blocks:
            if b[0] == "break" and (not bl or bl[-1][0] == "break"):
                continue
            bl.append(b)
        while bl and bl[-1][0] == "break":
            bl.pop()
        ch.blocks = bl
    if not chapters:
        chapters.append(Chapter("", []))
    return chapters


def _inline_plain(s):
    """Headings may carry *markup*; titles are plain text."""
    return "".join(t for t, b, i in _runs(s)) if s else ""


def _meta(meta):
    meta = dict(meta or {})
    out = {}
    out["title"] = _clean(meta.get("title") or "Untitled").strip() or "Untitled"
    out["author"] = _clean(meta.get("author") or "").strip()
    lang = _clean(meta.get("lang") or "en").strip()
    out["lang"] = lang if re.match(r"^[A-Za-z]{1,8}(-[A-Za-z0-9]{1,8})*$", lang) else "en"
    d = meta.get("date") or ""
    if isinstance(d, (datetime.date, datetime.datetime)):
        d = d.isoformat()
    out["date"] = _clean(d).strip()
    out["identifier"] = _clean(meta.get("identifier") or "").strip()
    out["subtitle"] = _clean(meta.get("subtitle") or "").strip()
    out["contact"] = [ln.strip()[:80] for ln in re.split(r"[\r\n]+", str(meta.get("contact") or "")) if ln.strip()][:6]
    y = str(meta.get("year") or "").strip()
    out["year"] = y if re.match(r"^\d{4}$", y) else str(datetime.date.today().year)
    return out


def _chapter_label(ch, n):
    return ch.title or ("Section %d" % n)


def _plain(runs):
    return "".join(t for t, b, i in runs)


# ---------------------------------------------------------------------------
# Book typography helpers (used when opts["smart"] / opts["book"] are set)
# ---------------------------------------------------------------------------

_ELISIONS = re.compile(r"(?:tis|twas|twere|twill|em|cause|til|n|round|bout|kay|nother)\b", re.I)
_OPENERS = set(" \t\n([{—–-/ “‘")


def smarten(text, prev=" "):
    """Typesetter's punctuation: straight quotes -> curly, -- -> em dash, ... -> ellipsis. `prev` is the
    character before `text` (runs are converted one after another with their context)."""
    if not text:
        return text
    text = text.replace("...", "…").replace(". . .", "…")
    text = re.sub(r"(?<=\S) ?--- ?(?=\S)", "—", text)
    text = re.sub(r"(?<=\w)--(?=\w)", "—", text)
    text = re.sub(r"(?<= )--(?= )", "–", text)          # spaced: an en dash, as British books set it
    text = re.sub(r"(?<=\w)--(?=[\s\"'.,;:!?”’])", "—", text)
    out = []
    for k, c in enumerate(text):
        before = out[-1] if out else prev
        after = text[k + 1] if k + 1 < len(text) else ""
        if c == '"':
            out.append("“" if before in _OPENERS or before == "" else "”")
        elif c == "'":
            if before.isalnum() and (after.isalpha() or after == ""):
                out.append("’")                      # don't, Mara's, o' (and a closing quote after a word)
            elif before in _OPENERS or before == "":
                rest = text[k + 1:k + 10]
                if re.match(r"\d\d(?:s\b|\b)", rest) or _ELISIONS.match(rest):
                    out.append("’")                  # '90s, 'tis, rock 'n' roll
                else:
                    out.append("‘")
            else:
                out.append("’")
        else:
            out.append(c)
    return "".join(out)


def smarten_runs(runs, prev=" "):
    out = []
    for r in runs:
        t = smarten(r[0], prev)
        if t:
            prev = t[-1]
        out.append((t,) + tuple(r[1:]))
    return out, prev


def smarten_chapters(chapters):
    for ch in chapters:
        ch.title = smarten(ch.title)
        new = []
        for kind, runs in ch.blocks:
            if runs:
                runs, _ = smarten_runs(runs)
            new.append((kind, runs))
        ch.blocks = new


_UNNUMBERED = re.compile(r"^(?:chapter|prologue|epilogue|interlude|part|book|act|afterword|foreword|preface|"
                         r"introduction|acknowledg|appendix|coda|author'?s note|dedication|contents)\b", re.I)
_ONES = ("Zero One Two Three Four Five Six Seven Eight Nine Ten Eleven Twelve Thirteen Fourteen Fifteen Sixteen "
         "Seventeen Eighteen Nineteen").split()
_TENS = "Twenty Thirty Forty Fifty Sixty Seventy Eighty Ninety".split()


def number_words(n):
    if 0 <= n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10 - 2] + ("-" + _ONES[n % 10] if n % 10 else "")
    return str(n)


def chapter_labels(chapters, enabled):
    """{index: 'Chapter One', ...} for chapters whose title is not already a label (Prologue, Chapter 3...)."""
    if not enabled:
        return {}
    out, k = {}, 0
    for i, ch in enumerate(chapters):
        if ch.title and _UNNUMBERED.match(ch.title):
            continue
        k += 1
        out[i] = "Chapter " + number_words(k)
    return out


def lead_split(runs, max_words=5):
    """First words of a chapter's opening paragraph, for a small-caps lead-in. -> (lead_runs, rest_runs)."""
    lead, rest, count, done = [], [], 0, False
    for r in runs:
        t = r[0]
        if done:
            rest.append(r)
            continue
        cut = None
        for m in re.finditer(r"\S+", t):
            count += 1
            word = m.group(0)
            if re.fullmatch("[—–\\-…*]+", word):
                count -= 1                                     # a lone dash is not a word: stop before it
                if count > 0 or lead:
                    cut = m.start()
                    break
                continue
            if count >= max_words or re.search(r"[,.;:!?—–…)][”’\"')\]]*$", word):
                cut = m.end()
                break
        if cut is None:
            lead.append(r)
            continue
        lead.append((t[:cut],) + tuple(r[1:]))
        if t[cut:]:
            rest.append((t[cut:],) + tuple(r[1:]))
        done = True
    return lead, rest


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def export(text_or_chapters, meta, fmt, out_path, **opts):
    """Export a manuscript. Returns out_path. Warnings -> LAST_WARNINGS (and opts['warnings']).

    Book options (all off by default, so plain calls keep the v1.0 output):
      smart=True            curly quotes, em dashes, ellipses (html, epub, docx, pdf)
      subheads=True         '# ...' lines inside a chapter (chapter-list input) become section headings
      book=True             typeset like a printed novel: chapter openings, small-caps lead-in, book CSS and,
                            for pdf, an embedded book face, justified lines, kerning, ligatures, widow/orphan
                            control, running heads, mirrored margins
      chapter_numbers=True  'Chapter One' labels above titles (not on Prologue, Epilogue, Part ...)
      copyright=True        a copyright page after the title page (needs meta author)
      layout='manuscript'   docx / pdf in standard manuscript format (US Letter, 12 pt, double spaced)
      page='trade'|'digest'|'a5', embed_fonts=True (pdf), font_size (pdf)
    """
    global LAST_WARNINGS
    fmt = (fmt or "").lower().lstrip(".")
    fmt = {"markdown": "md", "text": "txt", "htm": "html"}.get(fmt, fmt)
    if fmt not in FORMATS:
        raise ValueError("unknown format %r (expected one of %s)" % (fmt, ", ".join(FORMATS)))
    warnings = []
    chapters = parse_manuscript(text_or_chapters, subheads=bool(opts.get("subheads")))
    m = _meta(meta)
    book = bool(opts.get("book"))
    if opts.get("smart") and fmt not in ("txt", "md"):
        smarten_chapters(chapters)
        m["title"], m["subtitle"] = smarten(m["title"]), smarten(m["subtitle"])
    labels = chapter_labels(chapters, opts.get("chapter_numbers"))
    copyright_page = bool(opts.get("copyright")) and bool(m["author"])
    manuscript = (opts.get("layout") or "book") == "manuscript"
    if fmt == "txt":
        data = _to_txt(chapters, m, labels).encode("utf-8")
    elif fmt == "md":
        data = _to_md(chapters, m).encode("utf-8")
    elif fmt == "html":
        data = _to_html(chapters, m, labels, book).encode("utf-8")
    elif fmt == "epub":
        data = _to_epub(chapters, m, labels, book, copyright_page)
    elif fmt == "docx":
        if manuscript:
            data = _to_docx_manuscript(chapters, m, labels)
        else:
            data = _to_docx(chapters, m, opts, labels, book, copyright_page)
    elif manuscript or book:
        data = _BookPdf(chapters, m, opts, warnings, labels, copyright_page, manuscript).build()
    else:
        data = _PdfWriter(chapters, m, opts, warnings).build()
    out_path = os.fspath(out_path)
    d = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(d, exist_ok=True)
    tmp = out_path + ".tmp-quill"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, out_path)
    LAST_WARNINGS = warnings
    if isinstance(opts.get("warnings"), list):
        opts["warnings"].extend(warnings)
    return out_path


def _heading_text(ch, n, labels):
    """TOC / bookmark text: 'Chapter One: The Storm', 'The Storm', 'Chapter One' or 'Section n'."""
    lab = labels.get(n - 1)
    if lab and ch.title:
        return "%s: %s" % (lab, ch.title)
    return lab or _chapter_label(ch, n)


# ---------------------------------------------------------------------------
# TXT / MD / HTML
# ---------------------------------------------------------------------------

def _to_txt(chapters, m, labels=None):
    labels = labels or {}
    out = [m["title"]]
    if m["subtitle"]:
        out.append(m["subtitle"])
    if m["author"]:
        out.append("by " + m["author"])
    if m["date"]:
        out.append(m["date"])
    out.append("")
    for n, ch in enumerate(chapters, 1):
        out.append("")
        lab = labels.get(n - 1)
        if lab:
            out.append(lab)
        if ch.title or lab:
            head = ch.title or lab
            if ch.title:
                out.append(ch.title)
            out.append("=" * max(3, min(60, len(head))))
            out.append("")
        for kind, runs in ch.blocks:
            if kind == "break":
                out.append("* * *")
            else:
                out.append(_plain(runs))
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def _md_escape(s):
    return s.replace("\\", "\\\\").replace("*", "\\*")


def _md_runs(runs):
    parts = []
    for t, b, i in runs:
        lead = t[:len(t) - len(t.lstrip())]
        trail = t[len(t.rstrip()):]
        core = _md_escape(t.strip())
        if not core:
            parts.append(t)
            continue
        mk = "***" if (b and i) else "**" if b else "*" if i else ""
        parts.append(lead + mk + core + mk + trail)
    return "".join(parts)


def _yaml_str(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _to_md(chapters, m):
    out = ["---", "title: " + _yaml_str(m["title"])]
    if m["subtitle"]:
        out.append("subtitle: " + _yaml_str(m["subtitle"]))
    if m["author"]:
        out.append("author: " + _yaml_str(m["author"]))
    out.append("lang: " + m["lang"])
    if m["date"]:
        out.append("date: " + _yaml_str(m["date"]))
    out += ["---", ""]
    for n, ch in enumerate(chapters, 1):
        if ch.title or n > 1:  # an untitled opening section stays heading-less (round-trips)
            out.append("# " + _md_escape(ch.title or _chapter_label(ch, n)))
            out.append("")
        for kind, runs in ch.blocks:
            if kind == "break":
                out.append("***")
            elif kind == "sub":
                out.append("## " + _md_runs(runs))
            else:
                out.append(_md_runs(runs))
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def _esc(s):
    return html.escape(s, quote=True)


def _html_runs(runs):
    parts = []
    for t, b, i in runs:
        s = _esc(t)
        if i:
            s = "<em>" + s + "</em>"
        if b:
            s = "<strong>" + s + "</strong>"
        parts.append(s)
    return "".join(parts)


_BOOK_CSS = """
:root { --ink: #1d1b19; --paper: #fbf8f2; --muted: #6f675c; --rule: #cfc5b4; }
@media (prefers-color-scheme: dark) { :root { --ink: #e8e2d6; --paper: #1b1a18; --muted: #a49b8c; --rule: #4a443b; } }
html { background: var(--paper); color: var(--ink); }
body { font-family: "Iowan Old Style", "Palatino Linotype", Palatino, "Book Antiqua", Georgia, "Times New Roman", "Noto Serif", "Microsoft YaHei", serif;
  font-size: 1.125rem; line-height: 1.6; max-width: 33em; margin: 0 auto; padding: 3em 1.25em 6em;
  text-rendering: optimizeLegibility; font-kerning: normal; font-variant-ligatures: common-ligatures;
  hyphens: auto; -webkit-hyphens: auto; hanging-punctuation: first; }
header.titlepage { text-align: center; margin: 5em 0 7em; }
header.titlepage h1 { font-size: 2.4em; line-height: 1.15; margin: 0 0 .35em; font-weight: 500; letter-spacing: .01em; }
header.titlepage .subtitle { font-style: italic; font-size: 1.2em; margin: 0 0 1.6em; }
header.titlepage .author { font-variant: small-caps; letter-spacing: .08em; font-size: 1.2em; color: var(--muted); margin: 0; }
header.titlepage .date { color: var(--muted); font-size: .9em; margin-top: 1em; }
section.chapter { margin-top: 6em; page-break-before: always; break-before: page; }
section.chapter header { text-align: center; margin: 0 0 2.4em; }
section.chapter .label { font-variant: small-caps; letter-spacing: .14em; font-size: .9em; color: var(--muted); margin: 0 0 .7em; }
section.chapter h2 { font-weight: 500; font-size: 1.6em; line-height: 1.25; margin: 0; letter-spacing: .01em; }
h3.sub { text-align: center; font-weight: normal; font-variant: small-caps; letter-spacing: .1em; font-size: 1em; margin: 2em 0 1em; }
p { margin: 0; text-align: justify; orphans: 2; widows: 2; }
p + p { text-indent: 1.5em; }
p.scene { text-align: center; text-indent: 0; margin: 1.2em 0; color: var(--muted); letter-spacing: .5em; }
p.scene + p, h3.sub + p { text-indent: 0; }
.lead { font-variant: small-caps; letter-spacing: .05em; }
nav.toc { margin: 0 0 4em; } nav.toc ol { padding-left: 1.2em; } nav.toc a { color: inherit; }
@media print { :root { --ink: #000; --paper: #fff; --muted: #444; --rule: #999; }
  body { max-width: none; padding: 0; font-size: 11pt; } nav.toc { display: none; } }
"""


def _block_html(kind, runs, first, book, sub_tag="h3"):
    if kind == "break":
        return '<p class="scene" role="separator">* * *</p>'
    if kind == "sub":
        return '<%s class="sub">%s</%s>' % (sub_tag, _html_runs(runs), sub_tag)
    if first and book:
        lead, rest = lead_split(runs)
        if lead:
            return "<p>" + '<span class="lead">' + _html_runs(lead) + "</span>" + _html_runs(rest) + "</p>"
    return "<p>" + _html_runs(runs) + "</p>"


def _html_chapter_body(ch, book=False):
    out, first = [], True
    for kind, runs in ch.blocks:
        out.append(_block_html(kind, runs, first, book))
        if kind == "p":
            first = False
    return "\n".join(out)


def _to_html(chapters, m, labels=None, book=False):
    labels = labels or {}
    lang = _esc(m["lang"])
    out = ["<!DOCTYPE html>", '<html lang="%s">' % lang, "<head>", '<meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width, initial-scale=1">',
           "<title>%s</title>" % _esc(m["title"])]
    if m["author"]:
        out.append('<meta name="author" content="%s">' % _esc(m["author"]))
    out.append('<meta name="generator" content="%s">' % APP_NAME)
    out.append("<style>" + _BOOK_CSS + "</style>")
    out += ["</head>", "<body>", '<header class="titlepage">', "<h1>%s</h1>" % _esc(m["title"])]
    if m["subtitle"]:
        out.append('<p class="subtitle">%s</p>' % _esc(m["subtitle"]))
    if m["author"]:
        out.append('<p class="author">%s</p>' % _esc(m["author"]))
    if m["date"]:
        out.append('<p class="date">%s</p>' % _esc(m["date"]))
    out.append("</header>")
    titled = [(n, ch) for n, ch in enumerate(chapters, 1) if ch.title or labels.get(n - 1)]
    if len(titled) > 1:
        out.append('<nav class="toc" aria-label="Contents"><ol>')
        for n, ch in titled:
            out.append('<li><a href="#ch%d">%s</a></li>' % (n, _esc(_heading_text(ch, n, labels))))
        out.append("</ol></nav>")
    for n, ch in enumerate(chapters, 1):
        out.append('<section class="chapter" id="ch%d">' % n)
        lab = labels.get(n - 1)
        if ch.title or lab:
            out.append("<header>")
            if lab:
                out.append('<div class="label">%s</div>' % _esc(lab))
            if ch.title:
                out.append("<h2>%s</h2>" % _esc(ch.title))
            out.append("</header>")
        out.append(_html_chapter_body(ch, book))
        out.append("</section>")
    out += ["</body>", "</html>"]
    return "\n".join(out) + "\n"



def print_html(chapters, meta, chapter_numbers=False):
    """The book (or one chapter) as one page set for the browser's Print: title page, then each chapter on a new
    page, curly quotes, black ink on white paper whatever the screen theme. Nothing is written to disk."""
    parsed = parse_manuscript(chapters, subheads=True)
    m = _meta(meta)
    smarten_chapters(parsed)
    m["title"], m["subtitle"] = smarten(m["title"]), smarten(m["subtitle"])
    page = _to_html(parsed, m, chapter_numbers and chapter_labels(parsed, True) or {}, True)
    extra = ("<style>@page { margin: 2cm 2.2cm; } @media print { header.titlepage { margin: 30% 0 0; "
             "page-break-after: always; break-after: page; } section.chapter { margin-top: 0; } }</style>")
    return page.replace("</head>", extra + "</head>", 1)

# ---------------------------------------------------------------------------
# EPUB 3
# ---------------------------------------------------------------------------

_EPUB_CSS = """@charset "utf-8";
body { font-family: serif; line-height: 1.5; margin: 0 5%; }
h1, h2 { text-align: center; font-weight: bold; page-break-after: avoid; }
h1 { font-size: 1.5em; margin: 2em 0 1.5em; }
p { margin: 0; text-indent: 1.5em; text-align: justify; orphans: 2; widows: 2; }
p.first, p.scene + p { text-indent: 0; }
p.scene { text-align: center; text-indent: 0; margin: 1em 0; }
.titlepage { text-align: center; margin-top: 30%; }
.titlepage h1 { font-size: 2em; margin-bottom: .5em; }
.titlepage .author { font-style: italic; font-size: 1.2em; text-indent: 0; text-align: center; }
.titlepage .date { text-indent: 0; text-align: center; margin-top: 1em; }
nav ol { list-style: none; padding-left: 0; }
"""

_EPUB_BOOK_CSS = """@charset "utf-8";
body { font-family: serif; line-height: 1.5; margin: 0 5%; widows: 2; orphans: 2; }
section.chapter > header { text-align: center; margin: 3em 0 2.2em; page-break-after: avoid; break-after: avoid; }
.label { font-variant: small-caps; letter-spacing: .14em; font-size: .9em; margin: 0 0 .8em; text-align: center; }
h1 { font-size: 1.55em; font-weight: normal; line-height: 1.25; text-align: center; margin: 0;
     page-break-after: avoid; break-after: avoid; }
h2.sub { font-size: 1em; font-weight: normal; font-variant: small-caps; letter-spacing: .1em; text-align: center;
         margin: 1.8em 0 1em; page-break-after: avoid; break-after: avoid; }
p { margin: 0; text-indent: 1.5em; text-align: justify; orphans: 2; widows: 2;
    -webkit-hyphens: auto; -epub-hyphens: auto; hyphens: auto; }
p.first, p.scene + p, h2.sub + p { text-indent: 0; }
p.scene { text-align: center; text-indent: 0; margin: 1.1em 0; letter-spacing: .4em; }
.lead { font-variant: small-caps; letter-spacing: .05em; }
.titlepage { text-align: center; margin-top: 30%; }
.titlepage h1 { font-size: 2.1em; margin-bottom: .4em; }
.titlepage .subtitle { font-style: italic; font-size: 1.15em; text-indent: 0; text-align: center; margin: 0 0 2em; }
.titlepage .author { font-variant: small-caps; letter-spacing: .08em; font-size: 1.2em; text-indent: 0; text-align: center; }
.copyright { margin-top: 60%; font-size: .85em; text-align: center; }
.copyright p { text-indent: 0; text-align: center; margin: .3em 0; }
nav ol { list-style: none; padding-left: 0; }
nav li { margin: .35em 0; }
"""


def _xhtml(title, lang, body):
    return ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
            'lang="%s" xml:lang="%s">\n<head>\n<meta charset="utf-8"/>\n<title>%s</title>\n'
            '<link rel="stylesheet" type="text/css" href="style.css"/>\n</head>\n<body>\n%s\n</body>\n</html>\n'
            % (_esc(lang), _esc(lang), _esc(title), body))


def _book_uuid(m):
    if m["identifier"]:
        return m["identifier"]
    return "urn:uuid:" + str(uuid.uuid4())


def _w3c_date(d):
    return d if re.match(r"^\d{4}(-\d{2}(-\d{2})?)?$", d or "") else ""


def _to_epub(chapters, m, labels=None, book=False, copyright_page=False):
    labels = labels or {}
    lang = m["lang"]
    ident = _book_uuid(m)
    modified = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    files = []      # (file, label, doc, is_chapter)
    tp = ['<section class="titlepage" epub:type="titlepage">', "<h1>%s</h1>" % _esc(m["title"])]
    if m["subtitle"]:
        tp.append('<p class="subtitle">%s</p>' % _esc(m["subtitle"]))
    if m["author"]:
        tp.append('<p class="author">%s</p>' % _esc(m["author"]))
    if m["date"]:
        tp.append('<p class="date">%s</p>' % _esc(m["date"]))
    tp.append("</section>")
    files.append(("titlepage.xhtml", m["title"], _xhtml(m["title"], lang, "\n".join(tp)), False))
    if copyright_page:
        cp = ['<section class="copyright" epub:type="copyright-page">',
              "<p>Copyright © %s %s</p>" % (_esc(m["year"]), _esc(m["author"])), "<p>All rights reserved.</p>",
              "</section>"]
        files.append(("copyright.xhtml", "Copyright", _xhtml("Copyright", lang, "\n".join(cp)), False))
    for n, ch in enumerate(chapters, 1):
        label = _heading_text(ch, n, labels)
        lab = labels.get(n - 1)
        body = ['<section class="chapter" epub:type="chapter" id="ch%d">' % n]
        if book and (ch.title or lab):
            body.append("<header>")
            if lab:
                body.append('<div class="label">%s</div>' % _esc(lab))
            if ch.title:
                body.append("<h1>%s</h1>" % _esc(ch.title))
            body.append("</header>")
        elif ch.title:
            body.append("<h1>%s</h1>" % _esc(ch.title))
        first = True
        for kind, runs in ch.blocks:
            if kind == "break":
                body.append('<p class="scene">* * *</p>')
                first = False
            elif kind == "sub":
                body.append('<h2 class="sub">%s</h2>' % _html_runs(runs))
                first = False
            else:
                if first and book:
                    lead, rest = lead_split(runs)
                    body.append('<p class="first"><span class="lead">%s</span>%s</p>' % (_html_runs(lead), _html_runs(rest)))
                else:
                    body.append(('<p class="first">' if first else "<p>") + _html_runs(runs) + "</p>")
                first = False
        body.append("</section>")
        files.append(("chapter%04d.xhtml" % n, label, _xhtml(label, lang, "\n".join(body)), True))
    chap_files = [f for f in files if f[3]]
    nav = ['<nav epub:type="toc" id="toc">', "<h1>Contents</h1>", "<ol>"]
    for fname, label, _, _c in chap_files:
        nav.append('<li><a href="%s">%s</a></li>' % (fname, _esc(label)))
    nav += ["</ol>", "</nav>", '<nav epub:type="landmarks" hidden="hidden"><ol>',
            '<li><a epub:type="titlepage" href="titlepage.xhtml">Title Page</a></li>',
            '<li><a epub:type="bodymatter" href="%s">Start</a></li>' % chap_files[0][0] if chap_files else "",
            "</ol></nav>"]
    nav_doc = _xhtml("Contents", lang, "\n".join(x for x in nav if x))
    # NCX for EPUB 2 reading systems
    ncx = ['<?xml version="1.0" encoding="utf-8"?>',
           '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1" xml:lang="%s">' % _esc(lang),
           '<head><meta name="dtb:uid" content="%s"/><meta name="dtb:depth" content="1"/>'
           '<meta name="dtb:totalPageCount" content="0"/><meta name="dtb:maxPageNumber" content="0"/></head>' % _esc(ident),
           "<docTitle><text>%s</text></docTitle>" % _esc(m["title"]), "<navMap>"]
    for k, (fname, label, _, _c) in enumerate(chap_files, 1):
        ncx.append('<navPoint id="np%d" playOrder="%d"><navLabel><text>%s</text></navLabel>'
                   '<content src="%s"/></navPoint>' % (k, k, _esc(label), fname))
    ncx += ["</navMap>", "</ncx>"]
    md = ['<dc:identifier id="bookid">%s</dc:identifier>' % _esc(ident),
          "<dc:title>%s</dc:title>" % _esc(m["title"]),
          "<dc:language>%s</dc:language>" % _esc(lang),
          '<meta property="dcterms:modified">%s</meta>' % modified]
    if m["author"]:
        md.append("<dc:creator>%s</dc:creator>" % _esc(m["author"]))
    if _w3c_date(m["date"]):
        md.append("<dc:date>%s</dc:date>" % m["date"])
    md.append('<meta name="generator" content="%s"/>' % APP_NAME)
    manifest = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
                '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
                '<item id="css" href="style.css" media-type="text/css"/>']
    spine = []
    for k, (fname, label, _, _c) in enumerate(files):
        manifest.append('<item id="x%d" href="%s" media-type="application/xhtml+xml"/>' % (k, fname))
        spine.append('<itemref idref="x%d"/>' % k)
    opf = ('<?xml version="1.0" encoding="utf-8"?>\n'
           '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="%s">\n'
           '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n%s\n</metadata>\n'
           "<manifest>\n%s\n</manifest>\n"
           '<spine toc="ncx">\n%s\n</spine>\n</package>\n'
           % (_esc(lang), "\n".join(md), "\n".join(manifest), "\n".join(spine)))
    container = ('<?xml version="1.0" encoding="utf-8"?>\n'
                 '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
                 '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                 "</rootfiles>\n</container>\n")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        zi = zipfile.ZipInfo("mimetype", date_time=(2020, 1, 1, 0, 0, 0))
        zi.compress_type = zipfile.ZIP_STORED
        z.writestr(zi, b"application/epub+zip")
        dz = zipfile.ZIP_DEFLATED
        z.writestr("META-INF/container.xml", container, compress_type=dz)
        z.writestr("OEBPS/content.opf", opf, compress_type=dz)
        z.writestr("OEBPS/nav.xhtml", nav_doc, compress_type=dz)
        z.writestr("OEBPS/toc.ncx", "\n".join(ncx) + "\n", compress_type=dz)
        z.writestr("OEBPS/style.css", _EPUB_BOOK_CSS if book else _EPUB_CSS, compress_type=dz)
        for fname, label, doc, _c in files:
            z.writestr("OEBPS/" + fname, doc, compress_type=dz)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# DOCX (WordprocessingML)
# ---------------------------------------------------------------------------

_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
_R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
# page: (width, height, outer/side margin, top/bottom margin) in twips
_PAGES_TWIPS = {"a5": (8391, 11906, 1020, 1077), "trade": (8640, 12960, 1080, 1152), "digest": (7920, 12240, 1008, 1080)}


def _wt(text):
    return '<w:t xml:space="preserve">%s</w:t>' % _esc(text)


def _docx_runs(runs, extra_rpr=""):
    out = []
    for t, b, i in runs:
        rpr = extra_rpr
        if b or i or extra_rpr:
            rpr = "<w:rPr>%s%s%s</w:rPr>" % ("<w:b/><w:bCs/>" if b else "", "<w:i/><w:iCs/>" if i else "", extra_rpr)
        out.append("<w:r>%s%s</w:r>" % (rpr, _wt(t)))
    return "".join(out)


def _docx_styles(lang, book=False):
    def style(sid, name, ppr, rpr, based="Normal", nxt="Normal", extra=""):
        return ('<w:style w:type="paragraph" w:styleId="%s"><w:name w:val="%s"/><w:basedOn w:val="%s"/>'
                '<w:next w:val="%s"/>%s<w:qFormat/><w:pPr>%s</w:pPr><w:rPr>%s</w:rPr></w:style>'
                % (sid, name, based, nxt, extra, ppr, rpr))
    lang = _esc(lang)
    font = "Palatino Linotype" if book else "Times New Roman"
    extra_styles = ""
    if book:
        extra_styles = (
            style("ChapterLabel", "Chapter Label", '<w:keepNext/><w:pageBreakBefore/><w:spacing w:before="1800" w:after="200"/>'
                  '<w:ind w:firstLine="0"/><w:jc w:val="center"/>', '<w:smallCaps/><w:spacing w:val="40"/><w:sz w:val="22"/>',
                  nxt="Heading1")
            + style("Heading2", "heading 2", '<w:keepNext/><w:spacing w:before="360" w:after="240"/><w:ind w:firstLine="0"/>'
                    '<w:jc w:val="center"/><w:outlineLvl w:val="1"/>', '<w:smallCaps/><w:spacing w:val="20"/>',
                    nxt="FirstParagraph")
            + style("Copyright", "Copyright Notice", '<w:spacing w:before="0" w:after="120"/><w:ind w:firstLine="0"/>'
                    '<w:jc w:val="center"/>', '<w:sz w:val="18"/><w:szCs w:val="18"/>'))
    heading1_ppr = ('<w:keepNext/><w:pageBreakBefore/><w:spacing w:before="1800" w:after="600"/>'
                    '<w:ind w:firstLine="0"/><w:jc w:val="center"/><w:outlineLvl w:val="0"/>')
    heading1_rpr = '<w:b/><w:bCs/><w:sz w:val="36"/><w:szCs w:val="36"/>'
    if book:
        heading1_rpr = '<w:sz w:val="40"/><w:szCs w:val="40"/>'
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:styles %s>'
            '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="%s" w:hAnsi="%s" '
            'w:eastAsia="SimSun" w:cs="%s"/><w:sz w:val="22"/><w:szCs w:val="22"/>'
            '<w:lang w:val="%s" w:eastAsia="zh-CN" w:bidi="ar-SA"/></w:rPr></w:rPrDefault>'
            '<w:pPrDefault><w:pPr><w:spacing w:after="0" w:line="300" w:lineRule="auto"/></w:pPr></w:pPrDefault>'
            '</w:docDefaults>'
            '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/>'
            '<w:pPr><w:ind w:firstLine="360"/><w:jc w:val="both"/><w:widowControl/></w:pPr></w:style>'
            '%s%s%s%s%s%s'
            '<w:style w:type="character" w:default="1" w:styleId="DefaultParagraphFont">'
            '<w:name w:val="Default Paragraph Font"/><w:uiPriority w:val="1"/><w:semiHidden/></w:style>'
            '</w:styles>'
            % (_W, font, font, font, lang,
               style("FirstParagraph", "First Paragraph", '<w:ind w:firstLine="0"/>', ""),
               style("Title", "Title", '<w:spacing w:before="2400" w:after="480"/><w:ind w:firstLine="0"/><w:jc w:val="center"/>',
                     '<w:b/><w:bCs/><w:sz w:val="52"/><w:szCs w:val="52"/>' if not book else '<w:sz w:val="56"/><w:szCs w:val="56"/>'),
               style("Subtitle", "Subtitle", '<w:spacing w:after="240"/><w:ind w:firstLine="0"/><w:jc w:val="center"/>',
                     '<w:i/><w:iCs/><w:sz w:val="28"/><w:szCs w:val="28"/>'),
               style("Heading1", "heading 1", heading1_ppr, heading1_rpr, nxt="FirstParagraph"),
               style("SceneBreak", "Scene Break", '<w:spacing w:before="240" w:after="240"/><w:ind w:firstLine="0"/><w:jc w:val="center"/>',
                     "", nxt="FirstParagraph"),
               extra_styles))


_DOCX_CT = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
            '<Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>'
            '<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>'
            '%s'
            '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
            '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
            '</Types>')
_DOCX_RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
              '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
              '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
              '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
              '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
              '</Relationships>')


def _docx_package(document, styles, settings, footer, m, words, header=None):
    doc_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                '<Relationship Id="rIdSettings" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/>'
                '<Relationship Id="rIdFooter1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>'
                '%s</Relationships>' % (
                    '<Relationship Id="rIdHeader1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/>'
                    if header else ""))
    ct = _DOCX_CT % ('<Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>'
                     if header else "")
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            "<dc:title>%s</dc:title><dc:creator>%s</dc:creator><dc:language>%s</dc:language>"
            '<dcterms:created xsi:type="dcterms:W3CDTF">%s</dcterms:created>'
            '<dcterms:modified xsi:type="dcterms:W3CDTF">%s</dcterms:modified></cp:coreProperties>'
            % (_esc(m["title"]), _esc(m["author"]), _esc(m["lang"]), now, now))
    app = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
           '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
           'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
           "<Application>%s</Application><Words>%d</Words><DocSecurity>0</DocSecurity></Properties>" % (APP_NAME, words))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", _DOCX_RELS)
        z.writestr("word/document.xml", document)
        z.writestr("word/styles.xml", styles)
        z.writestr("word/settings.xml", settings)
        z.writestr("word/footer1.xml", footer)
        if header:
            z.writestr("word/header1.xml", header)
        z.writestr("word/_rels/document.xml.rels", doc_rels)
        z.writestr("docProps/core.xml", core)
        z.writestr("docProps/app.xml", app)
    return buf.getvalue()


def _docx_page_field_para(jc="center", prefix_runs=""):
    return ('<w:p><w:pPr><w:ind w:firstLine="0"/><w:jc w:val="%s"/></w:pPr>%s<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            '<w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r><w:r><w:fldChar w:fldCharType="separate"/></w:r>'
            '<w:r><w:t>1</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>' % (jc, prefix_runs))


def _words_in(chapters):
    return sum(len(_plain(r).split()) for ch in chapters for k, r in ch.blocks if k in ("p", "sub"))


def _to_docx(chapters, m, opts, labels=None, book=False, copyright_page=False):
    labels = labels or {}
    page = _PAGES_TWIPS.get((opts.get("page") or "a5").lower(), _PAGES_TWIPS["a5"])
    pw, ph, mx, my = page
    body = ['<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr>%s</w:p>' % _docx_runs([(m["title"], False, False)])]
    if m["subtitle"]:
        body.append('<w:p><w:pPr><w:pStyle w:val="Subtitle"/></w:pPr>%s</w:p>' % _docx_runs([(m["subtitle"], False, False)]))
    if m["author"]:
        sc = "<w:smallCaps/>" if book else ""
        body.append('<w:p><w:pPr><w:pStyle w:val="Subtitle"/></w:pPr>%s</w:p>'
                    % (_docx_runs([(m["author"], False, False)], sc) if book else _docx_runs([(m["author"], False, False)])))
    if m["date"]:
        body.append('<w:p><w:pPr><w:pStyle w:val="Subtitle"/></w:pPr>%s</w:p>' % _docx_runs([(m["date"], False, False)]))
    if copyright_page and book:
        body.append('<w:p><w:pPr><w:pStyle w:val="Copyright"/><w:pageBreakBefore/><w:spacing w:before="7200"/></w:pPr>%s</w:p>'
                    % _docx_runs([("Copyright © %s %s" % (m["year"], m["author"]), False, False)]))
        body.append('<w:p><w:pPr><w:pStyle w:val="Copyright"/></w:pPr>%s</w:p>' % _docx_runs([("All rights reserved.", False, False)]))
    for n, ch in enumerate(chapters, 1):
        lab = labels.get(n - 1) if book else None
        if lab:
            body.append('<w:p><w:pPr><w:pStyle w:val="ChapterLabel"/></w:pPr>%s</w:p>' % _docx_runs([(lab, False, False)]))
            if ch.title:
                body.append('<w:p><w:pPr><w:pStyle w:val="Heading1"/><w:pageBreakBefore w:val="0"/>'
                            '<w:spacing w:before="0" w:after="600"/></w:pPr>%s</w:p>' % _docx_runs([(ch.title, False, False)]))
        elif ch.title:
            body.append('<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>%s</w:p>' % _docx_runs([(ch.title, False, False)]))
        else:  # untitled chapter still starts a new page
            body.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
        first, lead_done = True, False
        for kind, runs in ch.blocks:
            if kind == "break":
                body.append('<w:p><w:pPr><w:pStyle w:val="SceneBreak"/></w:pPr>%s</w:p>' % _docx_runs([("* * *", False, False)]))
                first = True
            elif kind == "sub":
                body.append('<w:p><w:pPr><w:pStyle w:val="%s"/></w:pPr>%s</w:p>'
                            % ("Heading2" if book else "SceneBreak", _docx_runs(runs)))
                first = True
            else:
                st = '<w:pPr><w:pStyle w:val="FirstParagraph"/></w:pPr>' if first else ""
                if book and not lead_done:
                    lead, rest = lead_split(runs)
                    body.append("<w:p>%s%s%s</w:p>" % (st, _docx_runs(lead, "<w:smallCaps/>"), _docx_runs(rest)))
                    lead_done = True
                else:
                    body.append("<w:p>%s%s</w:p>" % (st, _docx_runs(runs)))
                first = False
    left, right = (mx + 216, mx - 144) if book else (mx, mx)   # book: a wider inside margin for the binding
    sect = ('<w:sectPr><w:footerReference w:type="default" r:id="rIdFooter1"/><w:pgSz w:w="%d" w:h="%d"/>'
            '<w:pgMar w:top="%d" w:right="%d" w:bottom="%d" w:left="%d" w:header="567" w:footer="567" w:gutter="0"/>'
            '<w:titlePg/></w:sectPr>' % (pw, ph, my, right, my, left))
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:document %s %s><w:body>%s%s</w:body></w:document>'
                % (_W, _R, "".join(body), sect))
    footer = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:ftr %s %s>%s</w:ftr>'
              % (_W, _R, _docx_page_field_para()))
    settings = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:settings %s>%s<w:defaultTabStop w:val="720"/>'
                '<w:characterSpacingControl w:val="doNotCompress"/><w:compat><w:compatSetting w:name="compatibilityMode" '
                'w:uri="http://schemas.microsoft.com/office/word" w:val="15"/></w:compat></w:settings>'
                % (_W, "<w:mirrorMargins/>" if book else ""))
    return _docx_package(document, _docx_styles(m["lang"], book), settings, footer, m, _words_in(chapters))


def _round_words(n):
    if n >= 10000:
        return int(round(n / 1000.0)) * 1000
    if n >= 1000:
        return int(round(n / 100.0)) * 100
    return max(10, int(round(n / 10.0)) * 10)


def _surname(author):
    parts = re.findall(r"[^\W\d_][\w'’-]*", author or "")
    return parts[-1] if parts else "Author"


def _to_docx_manuscript(chapters, m, labels=None):
    """Standard manuscript format (the layout agents and editors ask for): US Letter, 1 inch margins,
    Times New Roman 12 pt, double spaced, half-inch first-line indents, ragged right, a running header
    'Surname / TITLE / page', each chapter on a new page a third of the way down, '#' for scene breaks."""
    labels = labels or {}
    words = _words_in(chapters)
    author = m["author"] or "Author Name"
    key = " ".join(m["title"].upper().split()[:4])
    s = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:styles %s>'
         '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" '
         'w:eastAsia="SimSun" w:cs="Times New Roman"/><w:sz w:val="24"/><w:szCs w:val="24"/>'
         '<w:lang w:val="%s"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="0" w:line="480" '
         'w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>'
         '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/>'
         '<w:pPr><w:ind w:firstLine="720"/><w:widowControl/></w:pPr></w:style>'
         '<w:style w:type="paragraph" w:styleId="FirstParagraph"><w:name w:val="First Paragraph"/><w:basedOn w:val="Normal"/>'
         '<w:qFormat/><w:pPr><w:ind w:firstLine="0"/></w:pPr></w:style>'
         '<w:style w:type="paragraph" w:styleId="Plain"><w:name w:val="Contact"/><w:basedOn w:val="Normal"/>'
         '<w:pPr><w:spacing w:line="240" w:lineRule="auto"/><w:ind w:firstLine="0"/><w:tabs><w:tab w:val="right" w:pos="9360"/></w:tabs></w:pPr></w:style>'
         '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:qFormat/>'
         '<w:pPr><w:spacing w:before="4320" w:after="0"/><w:ind w:firstLine="0"/><w:jc w:val="center"/></w:pPr></w:style>'
         '<w:style w:type="paragraph" w:styleId="Byline"><w:name w:val="Byline"/><w:basedOn w:val="Normal"/>'
         '<w:pPr><w:ind w:firstLine="0"/><w:jc w:val="center"/></w:pPr></w:style>'
         '<w:style w:type="paragraph" w:styleId="ChapterLabel"><w:name w:val="Chapter Label"/><w:basedOn w:val="Normal"/>'
         '<w:pPr><w:keepNext/><w:pageBreakBefore/><w:spacing w:before="2880"/><w:ind w:firstLine="0"/><w:jc w:val="center"/></w:pPr></w:style>'
         '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/>'
         '<w:next w:val="FirstParagraph"/><w:qFormat/><w:pPr><w:keepNext/><w:pageBreakBefore/><w:spacing w:before="2880" '
         'w:after="480"/><w:ind w:firstLine="0"/><w:jc w:val="center"/><w:outlineLvl w:val="0"/></w:pPr></w:style>'
         '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/>'
         '<w:pPr><w:keepNext/><w:ind w:firstLine="0"/><w:jc w:val="center"/><w:outlineLvl w:val="1"/></w:pPr></w:style>'
         '<w:style w:type="paragraph" w:styleId="SceneBreak"><w:name w:val="Scene Break"/><w:basedOn w:val="Normal"/>'
         '<w:next w:val="FirstParagraph"/><w:pPr><w:ind w:firstLine="0"/><w:jc w:val="center"/></w:pPr></w:style>'
         '<w:style w:type="character" w:default="1" w:styleId="DefaultParagraphFont">'
         '<w:name w:val="Default Paragraph Font"/><w:uiPriority w:val="1"/><w:semiHidden/></w:style></w:styles>'
         % (_W, _esc(m["lang"])))
    body = ['<w:p><w:pPr><w:pStyle w:val="Plain"/></w:pPr>%s<w:r><w:tab/></w:r>%s</w:p>'
            % (_docx_runs([(author, False, False)]), _docx_runs([("about %s words" % format(_round_words(words), ","), False, False)])),
            "".join('<w:p><w:pPr><w:pStyle w:val="Plain"/></w:pPr>%s</w:p>' % _docx_runs([(ln, False, False)])
                    for ln in (m.get("contact") or ["[Your address, email and phone]"])),
            '<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr>%s</w:p>' % _docx_runs([(m["title"].upper(), False, False)])]
    if m["subtitle"]:
        body.append('<w:p><w:pPr><w:pStyle w:val="Byline"/></w:pPr>%s</w:p>' % _docx_runs([(m["subtitle"], False, False)]))
    body.append('<w:p><w:pPr><w:pStyle w:val="Byline"/></w:pPr>%s</w:p>' % _docx_runs([("by " + author, False, False)]))
    for n, ch in enumerate(chapters, 1):
        lab = labels.get(n - 1)
        if lab:
            body.append('<w:p><w:pPr><w:pStyle w:val="ChapterLabel"/></w:pPr>%s</w:p>' % _docx_runs([(lab, False, False)]))
            body.append('<w:p><w:pPr><w:pStyle w:val="Heading1"/><w:pageBreakBefore w:val="0"/><w:spacing w:before="0" w:after="480"/></w:pPr>%s</w:p>'
                        % _docx_runs([(ch.title or lab, False, False)]) if ch.title else "")
        else:
            body.append('<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>%s</w:p>'
                        % _docx_runs([(ch.title or "Chapter %d" % n, False, False)]))
        first = True
        for kind, runs in ch.blocks:
            if kind == "break":
                body.append('<w:p><w:pPr><w:pStyle w:val="SceneBreak"/></w:pPr>%s</w:p>' % _docx_runs([("#", False, False)]))
                first = True
            elif kind == "sub":
                body.append('<w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr>%s</w:p>' % _docx_runs(runs))
                first = True
            else:
                st = '<w:pPr><w:pStyle w:val="FirstParagraph"/></w:pPr>' if first else ""
                body.append("<w:p>%s%s</w:p>" % (st, _docx_runs(runs)))
                first = False
    body.append('<w:p><w:pPr><w:pStyle w:val="SceneBreak"/></w:pPr>%s</w:p>' % _docx_runs([("END", False, False)]))
    sect = ('<w:sectPr><w:headerReference w:type="default" r:id="rIdHeader1"/><w:footerReference w:type="default" r:id="rIdFooter1"/>'
            '<w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" '
            'w:header="720" w:footer="720" w:gutter="0"/><w:titlePg/></w:sectPr>')
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:document %s %s><w:body>%s%s</w:body></w:document>'
                % (_W, _R, "".join(body), sect))
    header = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:hdr %s %s>%s</w:hdr>'
              % (_W, _R, _docx_page_field_para("right", _docx_runs([("%s / %s / " % (_surname(author), key), False, False)]))))
    footer = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:ftr %s %s><w:p/></w:ftr>' % (_W, _R)
    settings = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:settings %s><w:defaultTabStop w:val="720"/>'
                '<w:compat><w:compatSetting w:name="compatibilityMode" w:uri="http://schemas.microsoft.com/office/word" '
                'w:val="15"/></w:compat></w:settings>' % _W)
    return _docx_package(document, s, settings, footer, m, words, header=header)


# ---------------------------------------------------------------------------
# TrueType / TTC parsing and subsetting (for PDF CID fonts)
# ---------------------------------------------------------------------------


class TrueTypeFont:
    """Minimal TrueType reader: cmap (formats 4 and 12), hmtx, glyf subsetting."""

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
        if "OS/2" in self.tables:
            o = self._t("OS/2")
            if len(o) >= 90 and struct.unpack(">H", o[0:2])[0] >= 2:
                self.cap_height = struct.unpack(">h", o[88:90])[0]
        self.italic_angle = 0
        if "post" in self.tables:
            p = self._t("post")
            self.italic_angle = struct.unpack(">i", p[4:8])[0] / 65536.0
        self.ps_name = self._ps_name() or os.path.splitext(os.path.basename(path))[0]
        self.cmap = self._parse_cmap()

    def _t(self, tag):
        off, ln = self.tables[tag]
        return self.data[off:off + ln]

    def _ps_name(self):
        if "name" not in self.tables:
            return None
        t = self._t("name")
        count, soff = struct.unpack(">HH", t[2:6])
        best = None
        for k in range(count):
            pid, eid, lid, nid, ln, off = struct.unpack(">6H", t[6 + 12 * k:18 + 12 * k])
            if nid != 6:
                continue
            raw = t[soff + off:soff + off + ln]
            try:
                s = raw.decode("utf-16-be") if pid in (0, 3) else raw.decode("latin-1")
            except UnicodeDecodeError:
                continue
            best = best or s
        if best:
            best = re.sub(r"[^A-Za-z0-9_.+-]", "", best)
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

    def glyph(self, ch):
        return self.cmap.get(ord(ch), 0)

    def width1000(self, gid):
        return int(round(self.advances[gid] * 1000.0 / self.units_per_em))

    def _loca(self):
        raw = self._t("loca")
        n = self.num_glyphs + 1
        if self.loca_long:
            return struct.unpack(">%dI" % n, raw[:4 * n])
        return [x * 2 for x in struct.unpack(">%dH" % n, raw[:2 * n])]

    def subset(self, gids):
        """Return a standalone TrueType file keeping glyph ids, with unused glyph outlines emptied."""
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
                chunk = data[goff + loca[g]:goff + loca[g + 1]]
                glyf += chunk
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
        if "post" in tables:  # version 3 post: no glyph names (keeps size small)
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


# ---------------------------------------------------------------------------
# Standard-14 font metrics (WinAnsiEncoding, bytes 32..255, 1/1000 em, 0 = undefined).
# Extracted from the metric-compatible system faces (Times New Roman == Times-Roman AFM
# widths, Arial == Helvetica AFM widths) and spot-checked against the Adobe AFM values.
# ---------------------------------------------------------------------------

_STD_WIDTHS_SRC = {
    'Times-Roman': "250 333 408 500 500 833 778 180 333 333 500 564 250 333 250 278 500 500 500 500 500 500 500 500 500 500 278 278 564 564 564 444 921 722 667 667 722 611 556 722 722 333 389 722 611 889 722 722 556 722 667 556 611 722 722 944 722 722 611 333 278 333 469 500 333 444 500 444 500 444 333 500 500 278 278 500 278 778 500 500 500 500 333 389 278 500 500 722 500 500 444 480 200 480 541 0 500 0 333 500 444 1000 500 500 333 1000 556 333 889 0 611 0 0 333 333 444 444 350 500 1000 333 980 389 333 722 0 444 722 250 333 500 500 500 500 200 500 333 760 276 500 564 333 760 500 400 549 300 300 333 576 453 333 333 300 310 500 750 750 750 444 722 722 722 722 722 722 889 667 611 611 611 611 333 333 333 333 722 722 722 722 722 722 722 564 722 722 722 722 722 722 556 500 444 444 444 444 444 444 667 444 444 444 444 444 278 278 278 278 500 500 500 500 500 500 500 549 500 500 500 500 500 500 500 500",
    'Times-Bold': "250 333 555 500 500 1000 833 278 333 333 500 570 250 333 250 278 500 500 500 500 500 500 500 500 500 500 333 333 570 570 570 500 930 722 667 722 722 667 611 778 778 389 500 778 667 944 722 778 611 778 722 556 667 722 722 1000 722 722 667 333 278 333 581 500 333 500 556 444 556 444 333 500 556 278 333 556 278 833 556 500 556 556 444 389 333 556 500 722 500 500 444 394 220 394 520 0 500 0 333 500 500 1000 500 500 333 1000 556 333 1000 0 667 0 0 333 333 500 500 350 500 1000 333 1000 389 333 722 0 444 722 250 333 500 500 500 500 220 500 333 747 300 500 570 333 747 500 400 549 300 300 333 576 540 333 333 300 330 500 750 750 750 500 722 722 722 722 722 722 1000 722 667 667 667 667 389 389 389 389 722 722 778 778 778 778 778 570 778 722 722 722 722 722 611 556 500 500 500 500 500 500 722 444 444 444 444 444 278 278 278 278 500 556 500 500 500 500 500 549 500 556 556 556 556 500 556 500",
    'Times-Italic': "250 333 420 500 500 833 778 214 333 333 500 675 250 333 250 278 500 500 500 500 500 500 500 500 500 500 333 333 675 675 675 500 920 611 611 667 722 611 611 722 722 333 444 667 556 833 667 722 611 722 611 500 556 722 611 833 611 556 556 389 278 389 422 500 333 500 500 444 500 444 278 500 500 278 278 444 278 722 500 500 500 500 389 389 278 500 444 667 444 444 389 400 275 400 541 0 500 0 333 500 556 889 500 500 333 1000 500 333 944 0 556 0 0 333 333 556 556 350 500 889 333 980 389 333 667 0 389 556 250 389 500 500 500 500 275 500 333 760 276 500 675 333 760 500 400 549 300 300 333 576 523 250 333 300 310 500 750 750 750 500 611 611 611 611 611 611 889 667 611 611 611 611 333 333 333 333 722 667 722 722 722 722 722 675 722 722 722 722 722 556 611 500 500 500 500 500 500 500 667 444 444 444 444 444 278 278 278 278 500 500 500 500 500 500 500 549 500 500 500 500 500 444 500 444",
    'Times-BoldItalic': "250 389 555 500 500 833 778 278 333 333 500 570 250 333 250 278 500 500 500 500 500 500 500 500 500 500 333 333 570 570 570 500 832 667 667 667 722 667 667 722 778 389 500 667 611 889 722 722 611 722 667 556 611 722 667 889 667 611 611 333 278 333 570 500 333 500 500 444 500 444 333 500 556 278 278 500 278 778 556 500 500 500 389 389 278 556 444 667 500 444 389 348 220 348 570 0 500 0 333 500 500 1000 500 500 333 1000 556 333 944 0 611 0 0 333 333 500 500 350 500 1000 333 1000 389 333 722 0 389 611 250 389 500 500 500 500 220 500 333 747 266 500 606 333 747 500 400 549 300 300 333 576 500 250 333 300 300 500 750 750 750 500 667 667 667 667 667 667 944 667 667 667 667 667 389 389 389 389 722 722 722 722 722 722 722 570 722 722 722 722 722 611 611 500 500 500 500 500 500 500 722 444 444 444 444 444 278 278 278 278 500 556 500 500 500 500 500 549 500 556 556 556 556 444 500 444",
    'Helvetica': "278 278 355 556 556 889 667 191 333 333 389 584 278 333 278 278 556 556 556 556 556 556 556 556 556 556 278 278 584 584 584 556 1015 667 667 722 722 667 611 778 722 278 500 667 556 833 722 778 667 778 722 667 611 722 667 944 667 667 611 278 278 278 469 556 333 556 556 500 556 556 278 556 556 222 222 500 222 833 556 556 556 556 333 500 278 556 500 722 500 500 500 334 260 334 584 0 556 0 222 556 333 1000 556 556 333 1000 667 333 1000 0 611 0 0 222 222 333 333 350 556 1000 333 1000 500 333 944 0 500 667 278 333 556 556 556 556 260 556 333 737 370 556 584 333 737 552 400 549 333 333 333 576 537 333 333 333 365 556 834 834 834 611 667 667 667 667 667 667 1000 722 667 667 667 667 278 278 278 278 722 722 778 778 778 778 778 584 778 722 722 722 722 667 667 611 556 556 556 556 556 556 889 500 556 556 556 556 278 278 278 278 556 556 556 556 556 556 556 549 611 556 556 556 556 500 556 500",
    'Helvetica-Bold': "278 333 474 556 556 889 722 238 333 333 389 584 278 333 278 278 556 556 556 556 556 556 556 556 556 556 333 333 584 584 584 611 975 722 722 722 722 667 611 778 722 278 556 722 611 833 722 778 667 778 722 667 611 722 667 944 667 667 611 333 278 333 584 556 333 556 611 556 611 556 333 611 611 278 278 556 278 889 611 611 611 611 389 556 333 611 556 778 556 556 500 389 280 389 584 0 556 0 278 556 500 1000 556 556 333 1000 667 333 1000 0 611 0 0 278 278 500 500 350 556 1000 333 1000 556 333 944 0 500 667 278 333 556 556 556 556 280 556 333 737 370 556 584 333 737 552 400 549 333 333 333 576 556 333 333 333 365 556 834 834 834 611 722 722 722 722 722 722 1000 722 667 667 667 667 278 278 278 278 722 722 778 778 778 778 778 584 778 722 722 722 722 667 667 611 556 556 556 556 556 556 889 556 556 556 556 556 278 278 278 278 611 611 611 611 611 611 611 549 611 611 611 611 611 556 611 556",
    'Helvetica-Oblique': "278 278 355 556 556 889 667 191 333 333 389 584 278 333 278 278 556 556 556 556 556 556 556 556 556 556 278 278 584 584 584 556 1015 667 667 722 722 667 611 778 722 278 500 667 556 833 722 778 667 778 722 667 611 722 667 944 667 667 611 278 278 278 469 556 333 556 556 500 556 556 278 556 556 222 222 500 222 833 556 556 556 556 333 500 278 556 500 722 500 500 500 334 260 334 584 0 556 0 222 556 333 1000 556 556 333 1000 667 333 1000 0 611 0 0 222 222 333 333 350 556 1000 333 1000 500 333 944 0 500 667 278 333 556 556 556 556 260 556 333 737 370 556 584 333 737 552 400 549 333 333 333 576 537 333 333 333 365 556 834 834 834 611 667 667 667 667 667 667 1000 722 667 667 667 667 278 278 278 278 722 722 778 778 778 778 778 584 778 722 722 722 722 667 667 611 556 556 556 556 556 556 889 500 556 556 556 556 278 278 278 278 556 556 556 556 556 556 556 549 611 556 556 556 556 500 556 500",
    'Helvetica-BoldOblique': "278 333 474 556 556 889 722 238 333 333 389 584 278 333 278 278 556 556 556 556 556 556 556 556 556 556 333 333 584 584 584 611 975 722 722 722 722 667 611 778 722 278 556 722 611 833 722 778 667 778 722 667 611 722 667 944 667 667 611 333 278 333 584 556 333 556 611 556 611 556 333 611 611 278 278 556 278 889 611 611 611 611 389 556 333 611 556 778 556 556 500 389 280 389 584 0 556 0 278 556 500 1000 556 556 333 1000 667 333 1000 0 611 0 0 278 278 500 500 350 556 1000 333 1000 556 333 944 0 500 667 278 333 556 556 556 556 280 556 333 737 370 556 584 333 737 552 400 549 333 333 333 576 556 333 333 333 365 556 834 834 834 611 722 722 722 722 722 722 1000 722 667 667 667 667 278 278 278 278 722 722 778 778 778 778 778 584 778 722 722 722 722 667 667 611 556 556 556 556 556 556 889 556 556 556 556 556 278 278 278 278 611 611 611 611 611 611 611 549 611 611 611 611 611 556 611 556",
}
_STD_WIDTHS = {k: [int(x) for x in v.split()] for k, v in _STD_WIDTHS_SRC.items()}
_FAMILIES = {
    "serif": {(False, False): "Times-Roman", (True, False): "Times-Bold",
              (False, True): "Times-Italic", (True, True): "Times-BoldItalic"},
    "sans": {(False, False): "Helvetica", (True, False): "Helvetica-Bold",
             (False, True): "Helvetica-Oblique", (True, True): "Helvetica-BoldOblique"},
}
_WINANSI = {}
for _b in range(32, 256):
    try:
        _WINANSI[bytes([_b]).decode("cp1252")] = _b
    except UnicodeDecodeError:
        pass

# ---------------------------------------------------------------------------
# System font discovery for non-WinAnsi characters
# ---------------------------------------------------------------------------

_STYLE_FIRST = {
    "serif": {(False, False): ["times.ttf", "Times New Roman.ttf", "LiberationSerif-Regular.ttf", "DejaVuSerif.ttf"],
              (True, False): ["timesbd.ttf", "Times New Roman Bold.ttf", "LiberationSerif-Bold.ttf", "DejaVuSerif-Bold.ttf"],
              (False, True): ["timesi.ttf", "Times New Roman Italic.ttf", "LiberationSerif-Italic.ttf", "DejaVuSerif-Italic.ttf"],
              (True, True): ["timesbi.ttf", "Times New Roman Bold Italic.ttf", "LiberationSerif-BoldItalic.ttf", "DejaVuSerif-BoldItalic.ttf"]},
    "sans": {(False, False): ["arial.ttf", "Arial.ttf", "LiberationSans-Regular.ttf", "DejaVuSans.ttf"],
             (True, False): ["arialbd.ttf", "Arial Bold.ttf", "LiberationSans-Bold.ttf", "DejaVuSans-Bold.ttf"],
             (False, True): ["ariali.ttf", "Arial Italic.ttf", "LiberationSans-Italic.ttf", "DejaVuSans-Oblique.ttf"],
             (True, True): ["arialbi.ttf", "Arial Bold Italic.ttf", "LiberationSans-BoldItalic.ttf", "DejaVuSans-BoldOblique.ttf"]},
}
_GENERIC_FALLBACKS = [
    "segoeui.ttf", "DejaVuSans.ttf", "arial.ttf", "NotoSans-Regular.ttf", "seguisym.ttf",
    "msyh.ttc", "simsun.ttc", "YuGothR.ttc", "msgothic.ttc", "malgun.ttf", "Nirmala.ttc", "Nirmala.ttf",
    "tahoma.ttf", "LeelawUI.ttf", "micross.ttf", "sylfaen.ttf", "ebrima.ttf", "gadugi.ttf", "seguihis.ttf", "himalaya.ttf", "monbaiti.ttf", "msjh.ttc", "taile.ttf", "phagspa.ttf", "mvboli.ttf", "arialuni.ttf", "Arial Unicode.ttf",
    "PingFang.ttc", "STHeiti Light.ttc", "AppleSDGothicNeo.ttc", "wqy-microhei.ttc", "wqy-zenhei.ttc",
    "DroidSansFallbackFull.ttf", "DroidSansFallback.ttf", "unifont.ttf",
    "seguiemj.ttf", "NotoEmoji-Regular.ttf", "Symbola.ttf", "DejaVuSans.ttf",
]
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
            for root, _sub, names in os.walk(d):
                for nm in names:
                    if nm.lower().endswith((".ttf", ".ttc")):
                        idx.setdefault(nm.lower(), os.path.join(root, nm))
                if root.count(os.sep) - d.count(os.sep) > 4:
                    _sub[:] = []
        _FONT_INDEX = idx
    return _FONT_INDEX


class _FontChain:
    """Finds a TrueType font for characters outside WinAnsi, per (bold, italic) style."""

    def __init__(self, family, explicit=None):
        self.family = family
        self.explicit = [os.fspath(p) for p in explicit] if explicit is not None else None
        self.loaded = {}      # path -> TrueTypeFont or None
        self.chains = {}      # style -> list of paths

    def _paths(self, style):
        if style not in self.chains:
            if self.explicit is not None:
                paths = [p for p in self.explicit if os.path.isfile(p)]
            else:
                idx = _font_index()
                names = _STYLE_FIRST[self.family][style] + _GENERIC_FALLBACKS
                paths = []
                for nm in names:
                    p = idx.get(nm.lower())
                    if p and p not in paths:
                        paths.append(p)
            self.chains[style] = paths
        return self.chains[style]

    def _font(self, path):
        if path not in self.loaded:
            try:
                self.loaded[path] = TrueTypeFont(path, 0)
            except Exception:
                self.loaded[path] = None
        return self.loaded[path]

    def find(self, ch, style):
        cp = ord(ch)
        for p in self._paths(style):
            f = self._font(p)
            if f is not None:
                g = f.cmap.get(cp, 0)
                if g and g < f.num_glyphs:
                    return f, g
        return None, 0


# ---------------------------------------------------------------------------
# PDF writer
# ---------------------------------------------------------------------------

_PAGE_SIZES = {"a5": (419.528, 595.276, 50.0, 56.0, 62.0), "trade": (432.0, 648.0, 58.0, 62.0, 70.0)}
_ZERO_WIDTH = set("\u200b\u200c\u200d\u2060\ufe0e\ufe0f\u00ad\ufeff")


def _is_cjk(cp):
    return (0x2E80 <= cp <= 0x9FFF or 0xAC00 <= cp <= 0xD7AF or 0xF900 <= cp <= 0xFAFF or
            0xFF00 <= cp <= 0xFFEF or 0x20000 <= cp <= 0x3FFFF or 0x1F300 <= cp <= 0x1FAFF or
            0x2600 <= cp <= 0x27BF)


def _pdf_escape(b):
    return b.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)").replace(b"\r", b"\\r")


def _pdf_text_string(s):
    try:
        b = s.encode("latin-1")
        if all(32 <= c < 127 for c in b):
            return b"(" + _pdf_escape(b) + b")"
    except UnicodeEncodeError:
        pass
    return b"<FEFF" + s.encode("utf-16-be").hex().upper().encode("ascii") + b">"


def _fmt(x):
    s = "%.3f" % x
    s = s.rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


class _Box:
    __slots__ = ("pieces", "width", "space", "space_font")

    def __init__(self, space, space_font):
        self.pieces = []      # list of [fontkey, text, width_units]
        self.width = 0.0      # in 1/1000 em (scaled later by size)
        self.space = space    # width units of the space glue before this box (0 = none)
        self.space_font = space_font


class _PdfWriter:
    def __init__(self, chapters, meta, opts, warnings):
        self.chapters, self.meta, self.warnings = chapters, meta, warnings
        page = (opts.get("page") or "a5").lower()
        self.pw, self.ph, self.mx, self.mt, self.mb = _PAGE_SIZES.get(page, _PAGE_SIZES["a5"])
        self.family = "sans" if (opts.get("family") or "serif").lower() in ("sans", "sans-serif", "helvetica") else "serif"
        self.size = float(opts.get("font_size") or 11.0)
        self.lead = self.size * 1.38
        self.justify = bool(opts.get("justify", True))
        self.chain = _FontChain(self.family, opts.get("fallback_fonts"))
        self.std_keys = {}    # base font name -> resource name
        self.cid = {}         # (path) -> {"name", "font", "used": {gid: char}}
        self.cache = {}       # (ch, style) -> (fontkey, code_text, width)
        self.missing = {}     # char -> count
        self.pages = []       # list of list of bytes (content ops)
        self.outline = []     # (title, page_index, y)

    # -- fonts ---------------------------------------------------------------
    def _std(self, style):
        base = _FAMILIES[self.family][style]
        if base not in self.std_keys:
            self.std_keys[base] = "F%d" % (len(self.std_keys) + 1)
        return self.std_keys[base], base

    def _resolve(self, ch, style):
        key = (ch, style)
        r = self.cache.get(key)
        if r is not None:
            return r
        b = _WINANSI.get(ch)
        if b is not None:
            name, base = self._std(style)
            w = _STD_WIDTHS[base][b - 32]
            if w:
                r = (name, ch, w)
        if r is None:
            f, g = self.chain.find(ch, style)
            if f is not None:
                ent = self.cid.get(f.path)
                if ent is None:
                    ent = self.cid[f.path] = {"name": "C%d" % (len(self.cid) + 1), "font": f, "used": {}}
                ent["used"].setdefault(g, ch)
                r = (ent["name"], "%04X" % g, f.width1000(g))
            else:
                self.missing[ch] = self.missing.get(ch, 0) + 1
                name, base = self._std(style)
                r = (name, "?", _STD_WIDTHS[base][ord("?") - 32])
        self.cache[key] = r
        if r[1] == "?" and ch != "?":
            # count every occurrence, not just the first lookup
            self.cache.pop(key, None)
        return r

    # -- layout --------------------------------------------------------------
    def _boxes(self, runs):
        boxes = []
        cur = None
        pending = 0
        pending_font = None
        for text, b, i in runs:
            style = (bool(b), bool(i))
            for ch in text:
                if ch in " \t\n\u3000" or ch == "\u00a0" and False:
                    if ch == "\u3000":
                        ch = " "
                    if cur is not None and cur.pieces:
                        boxes.append(cur)
                        cur = None
                    name, base = self._std(style)
                    pending = _STD_WIDTHS[base][0]
                    pending_font = name
                    continue
                if ch in _ZERO_WIDTH:
                    continue
                cp = ord(ch)
                if cp < 32:
                    continue
                fk, code, w = self._resolve(ch, style)
                cjk = _is_cjk(cp)
                if cur is None or cjk:
                    if cur is not None and cur.pieces:
                        boxes.append(cur)
                    cur = _Box(pending, pending_font)
                    pending, pending_font = 0, None
                elif cur.pieces and cur.pieces[-1][3]:  # previous char was CJK: allow a break
                    boxes.append(cur)
                    cur = _Box(0, None)
                p = cur.pieces
                if p and p[-1][0] == fk and not p[-1][3] and not cjk:
                    p[-1][1] += code
                    p[-1][2] += w
                else:
                    p.append([fk, code, w, cjk])
                cur.width += w
        if cur is not None and cur.pieces:
            boxes.append(cur)
        return boxes

    def _split_long(self, box, maxu):
        out = []
        cur = _Box(box.space, box.space_font)
        for fk, code, w, cjk in box.pieces:
            step = 4 if fk.startswith("C") else 1
            chunks = [code[k:k + step] for k in range(0, len(code), step)]
            # per-char widths: recompute from cache
            for c in chunks:
                cw = self._code_width(fk, c)
                if cur.pieces and cur.width + cw > maxu:
                    out.append(cur)
                    cur = _Box(0, None)
                if cur.pieces and cur.pieces[-1][0] == fk:
                    cur.pieces[-1][1] += c
                    cur.pieces[-1][2] += cw
                else:
                    cur.pieces.append([fk, c, cw, cjk])
                cur.width += cw
        if cur.pieces:
            out.append(cur)
        return out

    def _code_width(self, fk, c):
        if fk.startswith("C"):
            for ent in self.cid.values():
                if ent["name"] == fk:
                    return ent["font"].width1000(int(c, 16))
        for base, name in self.std_keys.items():
            if name == fk:
                return _STD_WIDTHS[base][_WINANSI.get(c, 63) - 32]
        return 500

    def _lines(self, runs, size, maxw, first_maxw=None):
        maxu = maxw * 1000.0 / size
        limit = (first_maxw if first_maxw is not None else maxw) * 1000.0 / size
        lines, line, width = [], [], 0.0
        for box in self._boxes(runs):
            parts = self._split_long(box, maxu) if box.width > maxu else [box]
            for bx in parts:
                add = bx.width + (bx.space if line else 0)
                if line and width + add > limit:
                    lines.append((line, width))
                    line, width = [], 0.0
                    limit = maxu
                    add = bx.width
                line.append(bx)
                width += add
        if line:
            lines.append((line, width))
        return lines

    # -- drawing -------------------------------------------------------------
    def _new_page(self):
        self.pages.append([])
        self.y = self.ph - self.mt

    def _emit_line(self, line, natural, size, x, y, align, maxw, last):
        ops = self.pages[-1]
        nat_pt = natural * size / 1000.0
        tw = 0.0
        nspace = sum(1 for k, b in enumerate(line) if k and b.space)
        if align == "center":
            x += (maxw - nat_pt) / 2.0
        elif align == "right":
            x += maxw - nat_pt
        elif align == "justify" and not last and nspace:
            tw = (maxw - nat_pt) / nspace
            if tw > size * 1.2:
                tw = 0.0
        parts = ["BT 1 0 0 1 %s %s Tm" % (_fmt(x), _fmt(y))]
        if tw:
            parts.append("%s Tw" % _fmt(tw))
        cur = None
        buf = []

        def flush():
            if buf:
                if cur.startswith("C"):
                    parts.append("<%s> Tj" % "".join(buf))
                else:
                    parts.append("(" + _pdf_escape("".join(buf).encode("cp1252")).decode("latin-1") + ") Tj")
                del buf[:]

        for k, bx in enumerate(line):
            if k and bx.space:
                sf = bx.space_font if bx.space_font else (cur if cur and not cur.startswith("C") else self._std((False, False))[0])
                if cur != sf:
                    flush()
                    parts.append("/%s %s Tf" % (sf, _fmt(size)))
                    cur = sf
                buf.append(" ")
            for fk, code, w, cjk in bx.pieces:
                if cur != fk:
                    flush()
                    parts.append("/%s %s Tf" % (fk, _fmt(size)))
                    cur = fk
                buf.append(code)
        flush()
        if tw:
            parts.append("0 Tw")
        parts.append("ET")
        ops.append(" ".join(parts))

    def _paragraph(self, runs, size, align, indent=0.0, lead=None):
        lead = lead or self.lead
        maxw = self.pw - 2 * self.mx
        lines = self._lines(runs, size, maxw, maxw - indent)
        last = len(lines) - 1
        for n, (line, w) in enumerate(lines):
            if self.y - lead < self.mb:
                self._new_page()
            self.y -= lead
            ind = indent if n == 0 else 0.0
            self._emit_line(line, w, size, self.mx + ind, self.y, align, maxw - ind, n == last)

    def _footer(self, idx, number):
        size = 9.0
        runs = [(str(number), False, False)]
        lines = self._lines(runs, size, self.pw)
        line, w = lines[0]
        x = (self.pw - w * size / 1000.0) / 2.0
        saved = self.pages
        self.pages = [saved[idx]]
        self._emit_line(line, w, size, x, self.mb / 2.0, "left", self.pw, True)
        self.pages = saved

    def build(self):
        m = self.meta
        body_align = "justify" if self.justify else "left"
        # title page
        self._new_page()
        self.y = self.ph * 0.66
        self._paragraph([(m["title"], True, False)], 24.0, "center", lead=30.0)
        if m["author"]:
            self.y -= 18
            self._paragraph([(m["author"], False, True)], 14.0, "center", lead=19.0)
        if m["date"]:
            self.y -= 8
            self._paragraph([(m["date"], False, False)], 10.5, "center", lead=14.0)
        first_body_page = len(self.pages)
        for n, ch in enumerate(self.chapters, 1):
            self._new_page()
            self.outline.append((_chapter_label(ch, n), len(self.pages) - 1, self.ph - self.mt))
            if ch.title:
                self.y = self.ph - self.mt - self.ph * 0.12
                self._paragraph([(ch.title, True, False)], 17.0, "center", lead=22.0)
                self.y -= self.lead * 2.2
            first = True
            for kind, runs in ch.blocks:
                if kind == "break":
                    if self.y - 3 * self.lead < self.mb:
                        self._new_page()
                    else:
                        self.y -= self.lead * 0.5
                        self._paragraph([("*  *  *", False, False)], self.size, "center")
                        self.y -= self.lead * 0.5
                    first = True
                    continue
                self._paragraph(runs, self.size, body_align, 0.0 if first else self.size * 1.5)
                first = False
        for k in range(first_body_page, len(self.pages)):
            self._footer(k, k - first_body_page + 1)
        if self.missing:
            for ch, cnt in sorted(self.missing.items(), key=lambda kv: -kv[1]):
                self.warnings.append("pdf: no installed font has U+%04X %r (%d occurrence%s); substituted '?'"
                                     % (ord(ch), ch, cnt, "" if cnt == 1 else "s"))
        return self._serialize()

    # -- serialisation -------------------------------------------------------
    def _serialize(self):
        objs = []   # list of bytes, object number = index + 1

        def add(b):
            objs.append(b)
            return len(objs)

        def reserve():
            objs.append(None)
            return len(objs)

        def stream(dict_extra, raw, compress=True):
            if compress:
                raw = zlib.compress(raw, 6)
                dict_extra += b" /Filter /FlateDecode"
            return add(b"<< /Length %d%s >>\nstream\n" % (len(raw), dict_extra) + raw + b"\nendstream")

        catalog = reserve()
        pages_root = reserve()
        font_refs = []
        for base, name in sorted(self.std_keys.items(), key=lambda kv: kv[1]):
            o = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /%s /Encoding /WinAnsiEncoding >>" % base.encode())
            font_refs.append((name, o))
        for path, ent in self.cid.items():
            font_refs.append((ent["name"], self._cid_font(ent, add, stream)))
        fonts = b" ".join(b"/%s %d 0 R" % (n.encode(), o) for n, o in font_refs)
        resources = add(b"<< /ProcSet [/PDF /Text] /Font << %s >> >>" % fonts)
        page_ids = []
        for ops in self.pages:
            content = stream(b"", ("\n".join(ops) + "\n").encode("latin-1"))
            page_ids.append(add(b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %s %s] /Resources %d 0 R /Contents %d 0 R >>"
                                % (pages_root, _fmt(self.pw).encode(), _fmt(self.ph).encode(), resources, content)))
        objs[pages_root - 1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
            b" ".join(b"%d 0 R" % p for p in page_ids), len(page_ids))
        outline_ref = b""
        if self.outline:
            root = reserve()
            items = [reserve() for _ in self.outline]
            for k, (title, pidx, y) in enumerate(self.outline):
                d = b"<< /Title %s /Parent %d 0 R /Dest [%d 0 R /XYZ 0 %s null]" % (
                    _pdf_text_string(title), root, page_ids[pidx], _fmt(y + 20).encode())
                if k:
                    d += b" /Prev %d 0 R" % items[k - 1]
                if k + 1 < len(items):
                    d += b" /Next %d 0 R" % items[k + 1]
                objs[items[k] - 1] = d + b" >>"
            objs[root - 1] = b"<< /Type /Outlines /First %d 0 R /Last %d 0 R /Count %d >>" % (items[0], items[-1], len(items))
            outline_ref = b" /Outlines %d 0 R /PageMode /UseOutlines" % root
        lang = b" /Lang %s" % _pdf_text_string(self.meta["lang"])
        objs[catalog - 1] = b"<< /Type /Catalog /Pages %d 0 R%s%s >>" % (pages_root, outline_ref, lang)
        now = datetime.datetime.now().strftime("D:%Y%m%d%H%M%S")
        info = b"<< /Title %s /Producer %s /Creator %s /CreationDate (%s)" % (
            _pdf_text_string(self.meta["title"]), _pdf_text_string(APP_NAME), _pdf_text_string(APP_NAME), now.encode())
        if self.meta["author"]:
            info += b" /Author %s" % _pdf_text_string(self.meta["author"])
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

    def _cid_font(self, ent, add, stream):
        f = ent["font"]
        used = ent["used"]
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
            w.append(b"%d [%s]" % (gids[k], b" ".join(b"%d" % f.width1000(g) for g in gids[k:j + 1])))
            k = j + 1
        cid = add(b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /%s /CIDSystemInfo << /Registry (Adobe) "
                  b"/Ordering (Identity) /Supplement 0 >> /FontDescriptor %d 0 R /DW 1000 /W [%s] /CIDToGIDMap /Identity >>"
                  % (base, desc, b" ".join(w)))
        lines = []
        for k in range(0, len(gids), 100):
            chunk = gids[k:k + 100]
            lines.append("%d beginbfchar" % len(chunk))
            for g in chunk:
                lines.append("<%04X> <%s>" % (g, used[g].encode("utf-16-be").hex().upper()))
            lines.append("endbfchar")
        cmap = ("/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
                "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
                "/CMapName /Adobe-Identity-UCS def\n/CMapType 2 def\n1 begincodespacerange\n<0000> <FFFF>\n"
                "endcodespacerange\n%s\nendcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n"
                % "\n".join(lines))
        tu = stream(b"", cmap.encode("ascii"))
        return add(b"<< /Type /Font /Subtype /Type0 /BaseFont /%s /Encoding /Identity-H /DescendantFonts [%d 0 R] "
                   b"/ToUnicode %d 0 R >>" % (base, cid, tu))


# ---------------------------------------------------------------------------
# Book-quality PDF (opts book=True, or layout='manuscript')
# ---------------------------------------------------------------------------
# width, height, inner margin, outer margin, top, bottom (points). Inner = the binding side (mirrored).
_BOOK_PAGES = {"trade": (432.0, 648.0, 58.0, 45.0, 56.0, 66.0), "digest": (396.0, 612.0, 54.0, 42.0, 52.0, 62.0),
               "a5": (419.528, 595.276, 56.0, 42.0, 52.0, 62.0), "letter": (612.0, 792.0, 72.0, 72.0, 72.0, 72.0)}
# (regular, bold, italic, bold italic): the first family that is installed and allows embedding wins
_BOOK_FACES = [
    ("pala.ttf", "palab.ttf", "palai.ttf", "palabi.ttf"),                                  # Palatino Linotype
    ("constan.ttf", "constanb.ttf", "constani.ttf", "constanz.ttf"),                       # Constantia
    ("georgia.ttf", "georgiab.ttf", "georgiai.ttf", "georgiaz.ttf"),                       # Georgia
    ("Georgia.ttf", "Georgia Bold.ttf", "Georgia Italic.ttf", "Georgia Bold Italic.ttf"),  # Georgia (macOS)
    ("cambria.ttc", "cambriab.ttf", "cambriai.ttf", "cambriaz.ttf"),                       # Cambria
    ("times.ttf", "timesbd.ttf", "timesi.ttf", "timesbi.ttf"),                             # Times New Roman
    ("LiberationSerif-Regular.ttf", "LiberationSerif-Bold.ttf", "LiberationSerif-Italic.ttf",
     "LiberationSerif-BoldItalic.ttf"),
    ("DejaVuSerif.ttf", "DejaVuSerif-Bold.ttf", "DejaVuSerif-Italic.ttf", "DejaVuSerif-BoldItalic.ttf"),
]
_MANUSCRIPT_FACES = [_BOOK_FACES[5], ("Times New Roman.ttf", "Times New Roman Bold.ttf", "Times New Roman Italic.ttf",
                                      "Times New Roman Bold Italic.ttf"), _BOOK_FACES[6], _BOOK_FACES[7]]
_STYLES = ((False, False), (True, False), (False, True), (True, True))
_LIGS = (("ffi", "ﬃ"), ("ffl", "ﬄ"), ("ff", "ﬀ"), ("fi", "ﬁ"), ("fl", "ﬂ"))
_SC = 0.78            # fake small caps: capitals at 78 % size


def _embeddable(f):
    """OS/2 fsType: installable (0), editable (8) or preview & print (4) may be embedded in a PDF;
    restricted licence (2 alone) and bitmap-only (0x200) may not."""
    if "OS/2" not in f.tables:
        return True
    fs = struct.unpack(">H", f._t("OS/2")[8:10])[0]
    if fs & 0x0200:
        return False
    return not (fs & 0x0002 and not fs & 0x000C)


def _kern_table(f):
    """TrueType 'kern' version 0, format 0 pairs -> {(left << 16) | right: value in font units}."""
    if "kern" not in f.tables:
        return {}
    t = f._t("kern")
    try:
        version, n = struct.unpack(">HH", t[:4])
        if version != 0:
            return {}
        off, pairs = 4, {}
        for _ in range(n):
            _v, length, cov = struct.unpack(">HHH", t[off:off + 6])
            if (cov >> 8) == 0 and cov & 1 and not cov & 6:
                npairs = struct.unpack(">H", t[off + 6:off + 8])[0]
                base = off + 14
                for k in range(npairs):
                    l, r, v = struct.unpack(">HHh", t[base + 6 * k:base + 6 * k + 6])
                    pairs[(l << 16) | r] = v
                length = 14 + 6 * npairs  # the 16-bit length field overflows on big tables
            off += length
        return pairs
    except struct.error:
        return {}


class _Word:
    __slots__ = ("pieces", "width", "space")

    def __init__(self, space):
        self.pieces = []   # [fontkey, scale, [items]]; items: code strings and kern numbers (TJ units)
        self.width = 0.0   # in 1/1000 em of the nominal size
        self.space = space


class _BookPdf(_PdfWriter):
    def __init__(self, chapters, meta, opts, warnings, labels=None, copyright_page=False, manuscript=False):
        _PdfWriter.__init__(self, chapters, meta, opts, warnings)
        self.labels = labels or {}
        self.copyright_page = copyright_page
        self.ms = manuscript
        page = "letter" if manuscript else (opts.get("page") or "trade").lower()
        self.pw, self.ph, self.inner, self.outer, self.mt, self.mb = _BOOK_PAGES.get(page, _BOOK_PAGES["trade"])
        self.size = float(opts.get("font_size") or (12.0 if manuscript else 10.8))
        self.lead = self.size * (2.0 if manuscript else 1.42)
        self.indent = 36.0 if manuscript else self.size * 1.5
        self.justify = not manuscript
        self.lw = self.pw - self.inner - self.outer
        self.face = self._pick_face(_MANUSCRIPT_FACES if manuscript else _BOOK_FACES) \
            if opts.get("embed_fonts", True) else None
        self.kerning = bool(self.face) and not manuscript
        self.ligatures = bool(self.face) and not manuscript
        self.kerns = {}
        self.gcache = {}
        self.kinds = []        # per page: title / copyright / opener / body
        self.heads = []        # per page: running head text or ""
        self.layout_log = []   # (page, paragraph index, lines here, first part, last part): widow/orphan proof
        self.cur_title = ""
        self._para_id = 0
        self.target = None     # page index that _line draws on (None = the newest page)

    # -- fonts ---------------------------------------------------------------
    def _pick_face(self, families):
        idx = _font_index() if self.chain.explicit is None else {}
        for fam in families:
            paths = [idx.get(nm.lower()) for nm in fam]
            if not all(paths):
                continue
            try:
                fonts = [TrueTypeFont(p, 0) for p in paths]
            except Exception:
                continue
            if all(_embeddable(f) for f in fonts):
                return dict(zip(_STYLES, fonts))
        return None

    def _ent(self, f):
        key = (f.path, f.index)
        ent = self.cid.get(key)
        if ent is None:
            ent = self.cid[key] = {"name": "C%d" % (len(self.cid) + 1), "font": f, "used": {}}
        return ent

    def _g(self, ch, style, uni=None):
        """-> (fontkey, code, width1000, font or None). uni: what the glyph stands for (ligatures)."""
        key = (ch, style)
        r = self.gcache.get(key)
        if r is not None:
            return r
        if self.face:
            f = self.face[style]
            g = f.cmap.get(ord(ch), 0)
            if g and g < f.num_glyphs:
                self._ent(f)["used"].setdefault(g, uni or ch)
                r = (self._ent(f)["name"], "%04X" % g, f.width1000(g), f)
        else:
            b = _WINANSI.get(ch)
            if b is not None:
                name, base = self._std(style)
                w = _STD_WIDTHS[base][b - 32]
                if w:
                    r = (name, ch, w, None)
        if r is None:
            f, g = self.chain.find(ch, style)
            if f is not None:
                self._ent(f)["used"].setdefault(g, ch)
                r = (self._ent(f)["name"], "%04X" % g, f.width1000(g), f)
        if r is None:
            self.missing[ch] = self.missing.get(ch, 0) + 1
            return self._g("?", style)
        self.gcache[key] = r
        return r

    def _kern(self, f, a, b):
        if not self.kerning or f is None:
            return 0
        t = self.kerns.get(f.path)
        if t is None:
            t = self.kerns[f.path] = _kern_table(f)
        v = t.get((int(a, 16) << 16) | int(b, 16))
        return int(round(v * 1000.0 / f.units_per_em)) if v else 0

    def _space(self):
        return self._g(" ", (False, False))

    # -- words -----------------------------------------------------------------
    def _words(self, runs):
        words, cur, space = [], None, False
        for r in runs:
            text, b, i = r[0], bool(r[1]), bool(r[2])
            sc = len(r) > 3 and r[3]
            style = (b, i)
            k = 0
            while k < len(text):
                ch = text[k]
                if ch in " \t\n　":
                    if cur is not None and cur.pieces:
                        words.append(cur)
                    cur, space = None, True
                    k += 1
                    continue
                if ch in _ZERO_WIDTH or ord(ch) < 32:
                    k += 1
                    continue
                cjk = _is_cjk(ord(ch))
                if cur is None:
                    cur, space = _Word(space), False
                elif cjk:  # each CJK character is its own box: a line may break between any two of them
                    if cur.pieces:
                        words.append(cur)
                    cur = _Word(False)
                scale, uni, glyph_ch = 1.0, None, ch
                if sc and ch.islower() and ch.upper() != ch and len(ch.upper()) == 1:
                    glyph_ch, scale = ch.upper(), _SC
                elif self.ligatures and not sc:
                    for seq, lig in _LIGS:
                        if text.startswith(seq, k) and self.face[style].cmap.get(ord(lig)):
                            glyph_ch, uni = lig, seq
                            break
                fk, code, w, f = self._g(glyph_ch, style, uni)
                p = cur.pieces[-1] if cur.pieces else None
                if p is not None and p[0] == fk and p[1] == scale:
                    kern = self._kern(f, p[3], code) if f is not None else 0
                    if kern:
                        p[2].append(-kern)
                        cur.width += kern * scale
                    p[2].append(code)
                    p[3] = code
                else:
                    cur.pieces.append([fk, scale, [code], code, f])
                cur.width += w * scale
                k += len(uni) if uni else 1
                if cjk:
                    words.append(cur)
                    cur = None
        if cur is not None and cur.pieces:
            words.append(cur)
        return words

    def _fit_long(self, words, maxu):
        out = []
        for w in words:
            if w.width <= maxu:
                out.append(w)
                continue
            cur = _Word(w.space)
            for fk, scale, items, last, f in w.pieces:
                for it in items:
                    if isinstance(it, (int, float)):
                        continue
                    cw = self._code_w(fk, it, f) * scale
                    if cur.pieces and cur.width + cw > maxu:
                        out.append(cur)
                        cur = _Word(False)
                    p = cur.pieces[-1] if cur.pieces else None
                    if p is not None and p[0] == fk and p[1] == scale:
                        p[2].append(it)
                    else:
                        cur.pieces.append([fk, scale, [it], it, f])
                    cur.width += cw
            if cur.pieces:
                out.append(cur)
        return out

    def _code_w(self, fk, code, f):
        if f is not None:
            return f.width1000(int(code, 16))
        return self._code_width(fk, code)

    # -- line breaking (total fit) --------------------------------------------------
    def _break(self, words, size, first_w, rest_w, justify):
        """Knuth-Plass style: choose the breaks that minimise the sum of squared badness over the whole
        paragraph (no hyphenation). -> list of (words, natural_width_pt, target_width_pt, glue_count)."""
        n = len(words)
        if not n:
            return []
        sw = self._space()[2] * size / 1000.0
        wid = [w.width * size / 1000.0 for w in words]
        INF = float("inf")
        best, prev = [INF] * (n + 1), [0] * (n + 1)
        best[0] = 0.0
        for i in range(n):
            if best[i] == INF:
                continue
            lw = first_w if i == 0 else rest_w
            nat, glue = 0.0, 0
            for j in range(i, n):
                if j > i and words[j].space:
                    nat += sw
                    glue += 1
                nat += wid[j]
                last = j == n - 1
                shrink = glue * sw * 0.26 if justify else 0.0
                if j > i and nat - shrink > lw + 0.01:
                    break
                if last:
                    bad = 0.0
                elif justify:
                    if nat <= lw:
                        st = glue * sw * 0.55
                        r = (lw - nat) / st if st else (0.0 if lw - nat < 1.0 else 12.0)
                    else:
                        r = (nat - lw) / shrink if shrink else 12.0
                    bad = min(10000.0, 100.0 * r ** 3)
                else:
                    bad = 100.0 * ((lw - nat) / lw) ** 2 * 3
                cost = best[i] + (1.0 + bad) ** 2
                if cost < best[j + 1]:
                    best[j + 1], prev[j + 1] = cost, i
        lines, j = [], n
        while j > 0:
            i = prev[j]
            lines.append(i)
            j = i
        lines.reverse()
        out = []
        for k, i in enumerate(lines):
            j = lines[k + 1] if k + 1 < len(lines) else n
            ws = words[i:j]
            glue = sum(1 for m, w in enumerate(ws) if m and w.space)
            nat = sum(wid[i:j]) + glue * sw
            out.append((ws, nat, first_w if i == 0 else rest_w, glue))
        return out

    # -- drawing -------------------------------------------------------------------
    def _page(self, kind, head=""):
        self.pages.append([])
        self.kinds.append(kind)
        self.heads.append(head)
        self.y = self.ph - self.mt

    def _x0(self):
        k = self.target if self.target is not None else len(self.pages) - 1
        return self.inner if k % 2 == 0 else self.outer   # page 1 (index 0) is a right-hand page

    def _tj(self, ops, fk, size, items, set_font=True):
        if not items:
            return
        parts, buf = [], []

        def flush():
            if buf:
                if fk.startswith("C"):
                    parts.append("<" + "".join(buf) + ">")
                else:
                    parts.append("(" + _pdf_escape("".join(buf).encode("cp1252", "replace")).decode("latin-1") + ")")
                del buf[:]
        for it in items:
            if isinstance(it, (int, float)):
                flush()
                if it:
                    parts.append(_fmt(it))
            else:
                buf.append(it)
        flush()
        ops.append(("/%s %s Tf [%s] TJ" % (fk, _fmt(size), " ".join(parts))) if set_font
                   else ("[%s] TJ" % " ".join(parts)))

    def _line(self, words, x, y, size, extra_per_glue, track=0.0):
        """One line as TJ arrays. extra_per_glue (pt) is added to each word space (negative = tighter)."""
        ops = ["BT 1 0 0 1 %s %s Tm" % (_fmt(x), _fmt(y))]
        if track:
            ops.append("%s Tc" % _fmt(track))
        sfk, scode, sw, sf = self._space()
        adj = round(-extra_per_glue * 1000.0 / size, 2) if extra_per_glue else 0
        segs = []   # merge neighbours that share font and size into one TJ array

        def add(fk, sz, items):
            if segs and segs[-1][0] == fk and abs(segs[-1][1] - sz) < 1e-6:
                segs[-1][2].extend(items)
            else:
                segs.append([fk, sz, list(items)])
        for k, w in enumerate(words):
            if k and w.space:
                add(sfk, size, [scode, adj] if adj else [scode])
            for fk, scale, items, last, f in w.pieces:
                add(fk, size * scale, items)
        cur = None
        for fk, sz, items in segs:
            self._tj(ops, fk, sz, items, set_font=(fk, sz) != cur)
            cur = (fk, sz)
        if track:
            ops.append("0 Tc")
        ops.append("ET")
        self.pages[self.target if self.target is not None else -1].append(" ".join(ops))

    def _centered(self, runs, size, lead, track=0.0, align="center"):
        """Short centred (or right-aligned) display lines (titles, labels, heads), wrapped if needed."""
        x0 = self._x0()
        ws = self._fit_long(self._words(runs), self.lw * 1000.0 / size)
        for line, nat, tw, glue in self._break(ws, size, self.lw, self.lw, False):
            self.y -= lead
            n_chars = sum(len([i for i in p[2] if not isinstance(i, (int, float))]) for w in line for p in w.pieces)
            width = nat + track * max(0, n_chars + glue - 1)
            x = x0 + ((self.lw - width) / 2.0 if align == "center" else self.lw - width if align == "right" else 0.0)
            self._line(line, x, self.y, size, 0.0, track)

    def _caps(self, text):
        """Letter-spaced small capitals for labels and running heads."""
        return [(text, False, False, True)]

    def _paragraph(self, runs, indent, align=None):
        """Lay out one paragraph with widow and orphan control: a paragraph that crosses a page leaves at least
        two lines at the foot of the page and carries at least two to the next, or moves over whole."""
        size = self.size
        ws = self._fit_long(self._words(runs), (self.lw - indent) * 1000.0 / size)
        justify = self.justify if align is None else False
        lines = self._break(ws, size, self.lw - indent, self.lw, justify)
        self._para_id += 1
        pid, i, total = self._para_id, 0, len(lines)
        while i < total:
            room = int((self.y - self.mb + 0.01) // self.lead)
            left = total - i
            if room <= 0:
                self._body_page()
                continue
            take = left if left <= room else min(room, left - 2)
            if take < left and take < 2:
                take = 0
            if take == 0:
                self._body_page()
                continue
            for k in range(take):
                line, nat, tw, glue = lines[i + k]
                self.y -= self.lead
                last = i + k == total - 1
                x = self._x0() + (indent if i + k == 0 else 0.0)
                if align == "center":
                    x, extra = self._x0() + (self.lw - nat) / 2.0, 0.0
                elif justify and glue and (not last or nat > tw):
                    extra = (tw - nat) / glue
                elif glue and nat > tw:
                    extra = (tw - nat) / glue
                else:
                    extra = 0.0
                self._line(line, x, self.y, size, extra)
            self.layout_log.append((len(self.pages) - 1, pid, take, i == 0, i + take == total))
            i += take

    def _body_page(self):
        self._page("body", self.cur_title)

    def _ornament(self, glyphs):
        size = self.size
        if self.y - self.lead * 3.5 < self.mb:
            self._body_page()
            self.y -= self.lead * 0.3
        else:
            self.y -= self.lead * 0.5
        self._centered([(glyphs, False, False)], size, self.lead)
        self.y -= self.lead * 0.5

    # -- pages ---------------------------------------------------------------------
    def _title_page(self):
        m = self.meta
        self._page("title")
        if self.ms:
            self.y = self.ph - self.mt + self.lead
            words = _round_words(_words_in(self.chapters))
            author = m["author"] or "Author Name"
            self._centered([(author, False, False)], self.size, self.size * 1.2, align="left")
            self.y += self.size * 1.2
            self._centered([("about %s words" % format(words, ","), False, False)], self.size, self.size * 1.2,
                           align="right")
            for ln in (m.get("contact") or ["[Your address, email and phone]"]):
                self._centered([(ln, False, False)], self.size, self.size * 1.2, align="left")
            self.y = self.ph * 0.55
            self._centered([(m["title"].upper(), False, False)], self.size, self.lead)
            if m["subtitle"]:
                self._centered([(m["subtitle"], False, False)], self.size, self.lead)
            self._centered([("by " + author, False, False)], self.size, self.lead)
            return
        self.y = self.ph * 0.70
        self._centered([(m["title"], False, False)], 25.0, 31.0)
        if m["subtitle"]:
            self.y -= 6
            self._centered([(m["subtitle"], False, True)], 13.0, 18.0)
        if m["author"]:
            self.y = min(self.y - 40, self.ph * 0.48)
            self._centered(self._caps(m["author"]), 13.0, 18.0, track=0.9)
        if self.copyright_page:
            self._page("copyright")
            self.y = self.mb + 70
            self._centered([("Copyright © %s %s" % (m["year"], m["author"]), False, False)], 8.6, 12.0)
            self._centered([("All rights reserved.", False, False)], 8.6, 12.0)

    def _chapter_opening(self, n, ch):
        lab = self.labels.get(n - 1)
        self.cur_title = ch.title or lab or self.meta["title"]
        self._page("opener")
        top = self.ph - self.mt
        self.outline.append((_heading_text(ch, n, self.labels), len(self.pages) - 1, top))
        if self.ms:
            self.y = self.ph - self.mt - (self.ph - self.mt - self.mb) * 0.33 + self.lead
            if lab:
                self._centered([(lab, False, False)], self.size, self.lead)
            self._centered([(ch.title or lab or "Chapter %d" % n, False, False)], self.size, self.lead)
            self.y -= self.lead
            return
        self.y = top - (self.ph * 0.17)
        if lab:
            self._centered(self._caps(lab), self.size * 0.95, self.size * 1.6, track=1.3)
            self.y -= 4
        if ch.title:
            self._centered([(ch.title, False, False)], self.size * 1.75, self.size * 2.25)
        self.y -= self.lead * 2.2

    def build(self):
        self._title_page()
        first_body = len(self.pages)
        ornament = "#" if self.ms else "*  *  *"
        for n, ch in enumerate(self.chapters, 1):
            self._chapter_opening(n, ch)
            first, lead_done = True, False
            for kind, runs in ch.blocks:
                if kind == "break":
                    self._ornament(ornament)
                    first = True
                    continue
                if kind == "sub":
                    if self.y - self.lead * 4 < self.mb:
                        self._body_page()
                    else:
                        self.y -= self.lead * 0.6
                    if self.ms:
                        self._paragraph(runs, 0.0, align="center")
                    else:
                        self._centered([(_plain(runs), False, False, True)], self.size * 0.95, self.lead, track=0.8)
                    self.y -= self.lead * 0.4
                    first = True
                    continue
                if first and not lead_done and not self.ms:
                    lead, rest = lead_split(runs)
                    runs = [tuple(r[:3]) + (True,) for r in lead] + list(rest)
                    lead_done = True
                self._paragraph(runs, 0.0 if first else self.indent)
                first = False
            if self.ms and n == len(self.chapters):
                self._ornament("END")
        self._decorate(first_body)
        if self.missing:
            for ch, cnt in sorted(self.missing.items(), key=lambda kv: -kv[1]):
                self.warnings.append("pdf: no installed font has U+%04X %r (%d occurrence%s); substituted '?'"
                                     % (ord(ch), ch, cnt, "" if cnt == 1 else "s"))
        return self._serialize()

    def _decorate(self, first_body):
        """Running heads and folios, drawn last; each page's text then reads head, body, folio."""
        m = self.meta
        saved_y = self.y
        key = " ".join(m["title"].upper().split()[:4])
        for k in range(first_body, len(self.pages)):
            body_ops, num = self.pages[k], k - first_body + 1
            self.pages[k] = []
            self.target = k
            if self.ms:
                self.y = self.ph - 36 + 12
                self._centered([("%s / %s / %d" % (_surname(m["author"]), key, num), False, False)], 10.0, 12.0,
                               align="right")
                self.pages[k] = self.pages[k] + body_ops
                continue
            if self.kinds[k] == "body":
                recto = k % 2 == 0
                if m["author"]:
                    head = m["title"] if recto else m["author"]
                else:
                    head = self.heads[k] if recto else m["title"]
                self.y = self.ph - self.mt + 30
                self._centered(self._caps(head), 8.2, 8.0, track=0.9)
            head_ops = self.pages[k]
            self.pages[k] = []
            self.y = self.mb * 0.55 + 9
            self._centered([(str(num), False, False)], 9.0, 9.0)
            self.pages[k] = head_ops + body_ops + self.pages[k]
        self.target = None
        self.y = saved_y


# ---------------------------------------------------------------------------
# PDF reader (for validation and round-trip tests of our own output)
# ---------------------------------------------------------------------------

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
            self.problems.append("pdf: missing startxref / %%EOF at end of file")
            return
        pos = int(m.group(1))
        if d[pos:pos + 4] != b"xref":
            self.problems.append("pdf: startxref %d does not point at an xref table" % pos)
            return
        p = pos + 4
        tm = d.find(b"trailer", p)
        if tm < 0:
            self.problems.append("pdf: no trailer")
            return
        lines = d[p:tm].split()
        k = 0
        while k + 1 < len(lines):
            start, count = int(lines[k]), int(lines[k + 1])
            k += 2
            for j in range(count):
                off, gen, kind = int(lines[k]), int(lines[k + 1]), lines[k + 2]
                k += 3
                if kind == b"n":
                    self.offsets[start + j] = off
        tail = d[tm:]
        self.trailer = tail[:tail.find(b"startxref")]
        sm = re.search(rb"/Size (\d+)", self.trailer)
        if not sm or int(sm.group(1)) != max(self.offsets or [0]) + 1:
            self.problems.append("pdf: trailer /Size does not match xref")
        for num, off in self.offsets.items():
            if not d.startswith(b"%d 0 obj" % num, off):
                self.problems.append("pdf: xref offset for object %d is wrong (%d)" % (num, off))
                if len(self.problems) > 20:
                    break

    def obj(self, num):
        off = self.offsets.get(num)
        if off is None:
            raise KeyError(num)
        d = self.data
        start = d.index(b"obj", off) + 3
        sp = d.find(b"stream", start)
        ep = d.find(b"endobj", start)
        if 0 <= sp < ep:
            head = d[start:sp]
            lm = re.search(rb"/Length (\d+)( 0 R)?", head)
            length = int(lm.group(1))
            if lm.group(2):
                length = int(self.obj(length)[0].strip())
            s0 = sp + 6
            if d[s0:s0 + 2] == b"\r\n":
                s0 += 2
            elif d[s0:s0 + 1] == b"\n":
                s0 += 1
            raw = d[s0:s0 + length]
            if b"/FlateDecode" in head:
                raw = zlib.decompress(raw)
            return head, raw
        return d[start:ep], None

    def pages(self):
        root = int(re.search(rb"/Root (\d+) 0 R", self.trailer).group(1))
        cat = self.obj(root)[0]
        pages = int(re.search(rb"/Pages (\d+) 0 R", cat).group(1))
        out = []

        def walk(n):
            head = self.obj(n)[0]
            if re.search(rb"/Type\s*/Pages", head):
                kids = re.search(rb"/Kids\s*\[(.*?)\]", head, re.S).group(1)
                for k in re.findall(rb"(\d+) 0 R", kids):
                    walk(int(k))
            else:
                out.append((n, head))
        walk(pages)
        cnt = re.search(rb"/Count (\d+)", self.obj(pages)[0])
        if not cnt or int(cnt.group(1)) != len(out):
            self.problems.append("pdf: page tree /Count mismatch")
        return out

    def fonts_for(self, page_head):
        rm = re.search(rb"/Resources (\d+) 0 R", page_head)
        res = self.obj(int(rm.group(1)))[0] if rm else page_head
        fm = re.search(rb"/Font\s*<<(.*?)>>", res, re.S)
        fonts = {}
        if fm:
            for name, num in re.findall(rb"/([\w.+-]+)\s+(\d+) 0 R", fm.group(1)):
                fonts[name.decode()] = int(num)
        return fonts

    def decoder(self, num, cache):
        if num in cache:
            return cache[num]
        head = self.obj(num)[0]
        tu = re.search(rb"/ToUnicode (\d+) 0 R", head)
        if tu:
            cm = self.obj(int(tu.group(1)))[1].decode("latin-1")
            table = {}
            for block in re.findall(r"beginbfchar(.*?)endbfchar", cm, re.S):
                for src, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
                    table[int(src, 16)] = bytes.fromhex(dst).decode("utf-16-be")
            for block in re.findall(r"beginbfrange(.*?)endbfrange", cm, re.S):
                for a, b, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
                    base = int(dst, 16)
                    for c in range(int(a, 16), int(b, 16) + 1):
                        table[c] = chr(base + c - int(a, 16))
            two = b"Type0" in head

            def dec(bs, table=table, two=two):
                if two:
                    return "".join(table.get(int.from_bytes(bs[k:k + 2], "big"), "\ufffd") for k in range(0, len(bs) - 1, 2))
                return "".join(table.get(c, chr(c)) for c in bs)
        else:
            def dec(bs):
                return bs.decode("cp1252", "replace")
        cache[num] = dec
        return dec


_TOK = re.compile(rb"\s*(?:(\()|<([0-9A-Fa-f\s]*)>|(/[^\s/\[\]()<>]+)|([-+]?\d*\.?\d+)|(\[|\])|([A-Za-z'\"*]+))")


def _lit_string(data, p):
    depth, out = 1, bytearray()
    while p < len(data):
        c = data[p]
        if c == 0x5C:
            p += 1
            e = data[p:p + 1]
            mp = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f"}
            if e in mp:
                out += mp[e]
            elif e.isdigit():
                oct_ = re.match(rb"[0-7]{1,3}", data[p:p + 3]).group(0)
                out.append(int(oct_, 8) & 255)
                p += len(oct_) - 1
            elif e == b"\n":
                pass
            else:
                out += e
        elif c == 0x28:
            depth += 1
            out.append(c)
        elif c == 0x29:
            depth -= 1
            if depth == 0:
                return bytes(out), p + 1
            out.append(c)
        else:
            out.append(c)
        p += 1
    return bytes(out), p


def _content_text(stream, fonts, reader, cache):
    out = []
    stack = []
    font = None
    p = 0
    n = len(stream)
    while p < n:
        m = _TOK.match(stream, p)
        if not m:
            p += 1
            continue
        p = m.end()
        if m.group(1):
            s, p = _lit_string(stream, p)
            stack.append(("s", s))
        elif m.group(2) is not None:
            stack.append(("s", bytes.fromhex(re.sub(rb"\s", b"", m.group(2)).decode() + ("0" if len(re.sub(rb"\s", b"", m.group(2))) % 2 else ""))))
        elif m.group(3):
            stack.append(("n", m.group(3)[1:].decode("latin-1")))
        elif m.group(4):
            stack.append(("num", m.group(4)))
        elif m.group(5):
            stack.append(("arr", m.group(5)))
        else:
            op = m.group(6)
            if op == b"Tf" and len(stack) >= 2 and stack[-2][0] == "n":
                fn = stack[-2][1]
                font = reader.decoder(fonts[fn], cache) if fn in fonts else None
            elif op in (b"Tj", b"'", b'"') and stack and stack[-1][0] == "s" and font:
                out.append(font(stack[-1][1]))
            elif op == b"TJ" and font:
                for kind, v in stack:
                    if kind == "s":
                        out.append(font(v))
            elif op in (b"Tm", b"Td", b"TD", b"T*", b"ET"):
                if out and not out[-1].endswith("\n"):
                    out.append("\n")
            stack = []
    return "".join(out)


def pdf_extract_text(path):
    """Return the text of each page (our ToUnicode / WinAnsi decoder)."""
    with open(path, "rb") as f:
        reader = _PdfReader(f.read())
    cache = {}
    texts = []
    for num, head in reader.pages():
        fonts = reader.fonts_for(head)
        cm = re.search(rb"/Contents (\d+) 0 R", head)
        raw = reader.obj(int(cm.group(1)))[1] if cm else b""
        texts.append(_content_text(raw, fonts, reader, cache))
    return texts


# ---------------------------------------------------------------------------
# validate()
# ---------------------------------------------------------------------------

def validate(path):
    """Return a list of problems (empty list == valid) for an .epub, .docx or .pdf file."""
    import xml.etree.ElementTree as ET
    path = os.fspath(path)
    if not os.path.isfile(path):
        return ["file not found: %s" % path]
    ext = os.path.splitext(path)[1].lower()
    probs = []
    if ext == ".pdf":
        with open(path, "rb") as f:
            data = f.read()
        if not data.startswith(b"%PDF-1.4"):
            probs.append("pdf: header is not %PDF-1.4")
        if not data.rstrip().endswith(b"%%EOF"):
            probs.append("pdf: does not end with %%EOF")
        try:
            r = _PdfReader(data)
            probs += r.problems
            if not r.problems:
                pages = r.pages()
                probs += r.problems
                if not pages:
                    probs.append("pdf: no pages")
                for num in r.offsets:
                    r.obj(num)  # every stream must decompress
                cache = {}
                for num, head in pages[:3] + pages[-3:]:
                    cm = re.search(rb"/Contents (\d+) 0 R", head)
                    _content_text(r.obj(int(cm.group(1)))[1], r.fonts_for(head), r, cache)
        except Exception as e:  # malformed structure
            probs.append("pdf: parse error: %s: %s" % (type(e).__name__, e))
        return probs
    if ext not in (".epub", ".docx"):
        return ["validate: unsupported file type %s (epub/docx/pdf only)" % ext]
    try:
        z = open_capped(path)
    except zipfile.BadZipFile as e:
        return ["%s: not a zip file (%s)" % (ext[1:], e)]
    with z:
        bad = z.testzip()
        if bad is not None:
            probs.append("zip: corrupt member %s" % bad)
        names = z.namelist()
        for n in names:
            if n.endswith((".xml", ".xhtml", ".opf", ".ncx", ".rels")):
                try:
                    ET.fromstring(z.read(n))
                except ET.ParseError as e:
                    probs.append("%s: XML not well-formed: %s" % (n, e))
        if ext == ".epub":
            first = z.infolist()[0] if names else None
            if not first or first.filename != "mimetype":
                probs.append("epub: mimetype is not the first entry")
            elif first.compress_type != zipfile.ZIP_STORED:
                probs.append("epub: mimetype is compressed")
            elif z.read("mimetype") != b"application/epub+zip":
                probs.append("epub: wrong mimetype content")
            if first is not None and first.extra:
                probs.append("epub: mimetype entry has an extra field")
            if "META-INF/container.xml" not in names:
                return probs + ["epub: META-INF/container.xml missing"]
            try:
                c = ET.fromstring(z.read("META-INF/container.xml"))
                rf = c.find(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile")
                opf_path = rf.get("full-path")
                opf = ET.fromstring(z.read(opf_path))
            except Exception as e:
                return probs + ["epub: cannot read package document: %s" % e]
            ns = {"o": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/"}
            if opf.get("version") != "3.0":
                probs.append("epub: package version is not 3.0")
            uid = opf.get("unique-identifier")
            ids = [e for e in opf.findall(".//dc:identifier", ns) if e.get("id") == uid]
            if not uid or not ids or not (ids[0].text or "").strip():
                probs.append("epub: unique-identifier does not reference a dc:identifier")
            for tag in ("title", "language"):
                if opf.find(".//dc:%s" % tag, ns) is None:
                    probs.append("epub: dc:%s missing" % tag)
            mod = [e for e in opf.findall(".//o:meta", ns) if e.get("property") == "dcterms:modified"]
            if len(mod) != 1 or not re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$", (mod[0].text or "")):
                probs.append("epub: dcterms:modified missing or malformed")
            base = opf_path.rsplit("/", 1)[0] + "/" if "/" in opf_path else ""
            items = {}
            nav = False
            for it in opf.findall(".//o:manifest/o:item", ns):
                items[it.get("id")] = it
                href = base + it.get("href", "")
                if href not in names:
                    probs.append("epub: manifest item %s missing from zip" % href)
                if "nav" in (it.get("properties") or "").split():
                    nav = True
                    try:
                        nd = ET.fromstring(z.read(href))
                        if not any(e.get("{http://www.idpf.org/2007/ops}type") == "toc" for e in nd.iter()):
                            probs.append("epub: nav document has no epub:type=toc")
                    except Exception as e:
                        probs.append("epub: nav unreadable: %s" % e)
            if not nav:
                probs.append("epub: no manifest item with properties=nav")
            refs = opf.findall(".//o:spine/o:itemref", ns)
            if not refs:
                probs.append("epub: empty spine")
            for r in refs:
                if r.get("idref") not in items:
                    probs.append("epub: spine idref %s not in manifest" % r.get("idref"))
        else:
            for req in ("[Content_Types].xml", "_rels/.rels", "word/document.xml", "word/styles.xml",
                        "word/_rels/document.xml.rels", "docProps/core.xml", "docProps/app.xml"):
                if req not in names:
                    probs.append("docx: %s missing" % req)
            if "[Content_Types].xml" in names:
                ct = ET.fromstring(z.read("[Content_Types].xml"))
                nsct = "{http://schemas.openxmlformats.org/package/2006/content-types}"
                over = {o.get("PartName") for o in ct.findall(nsct + "Override")}
                defaults = {d.get("Extension") for d in ct.findall(nsct + "Default")}
                for n in names:
                    if "/" + n not in over and n.rsplit(".", 1)[-1] not in defaults:
                        probs.append("docx: no content type for %s" % n)
                if "/word/document.xml" not in over:
                    probs.append("docx: main document content type missing")
            if "word/styles.xml" in names:
                st = z.read("word/styles.xml")
                for sid in (b'w:styleId="Normal"', b'w:styleId="Heading1"'):
                    if sid not in st:
                        probs.append("docx: styles.xml lacks %s" % sid.decode())
            if "word/_rels/document.xml.rels" in names:
                rels = ET.fromstring(z.read("word/_rels/document.xml.rels"))
                for r in rels:
                    t = "word/" + r.get("Target", "")
                    if r.get("TargetMode") != "External" and t not in names:
                        probs.append("docx: relationship target %s missing" % t)
    return probs
