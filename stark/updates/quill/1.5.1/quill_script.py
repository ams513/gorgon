"""Stark Quill screenplays: Fountain plain-text scripts.

A screenplay book keeps its chapters as ordinary text files written in Fountain (the open plain-text screenplay
syntax: a line starting INT. or EXT. is a scene heading, a line in capitals over a line of speech is a character
cue, and so on). This module reads that text into script elements, sets it on script pages (Courier 12 pt,
1.5 inch left margin, 55 lines a page, so about one minute of screen time a page), and writes three files:
a Fountain file (free), a Final Draft .fdx file and an industry-layout PDF (both Pro; writer_app.py does the
gating). Rules only, standard library only, no network; the only file written is the out path a caller passes.
"""
from __future__ import annotations

import html
import re
import unicodedata
import zlib
from datetime import date

# ------------------------------------------------------------------------------------------------ parsing
SCENE_RX = re.compile(r"^(?:INT\.?/EXT|INT/EXT|I/E|INT|EXT|EST)[.\s]", re.I)
SCENE_NO_RX = re.compile(r"\s*#([A-Za-z0-9][A-Za-z0-9.\-]*)#\s*$")
EXT_RX = re.compile(r"\s*\((?:V\.O\.|O\.S\.|O\.C\.|CONT'D|CONT’D|CONT\.|[^()]*)\)\s*$", re.I)
TRANSITION_WORDS = ("FADE OUT.", "FADE TO BLACK.", "CUT TO BLACK.", "THE END.", "FADE OUT")
TITLE_KEYS = ("title", "credit", "author", "authors", "source", "draft date", "date", "contact", "notes",
              "copyright", "revision")
PRINTED = ("scene", "action", "character", "paren", "dialogue", "transition", "centered", "lyric")
NL = "\n"


def _blank_out(text, rx):
    """Remove matches but keep their line breaks, so line numbers still point at the right place."""
    return rx.sub(lambda m: "\n" * m.group(0).count("\n"), text)


_BONEYARD = re.compile(r"/\*.*?\*/", re.S)
_NOTE = re.compile(r"\[\[.*?\]\]", re.S)
_COMMENT = re.compile(r"\{>>.*?<<\}", re.S)
_HILITE = re.compile(r"\{==(.*?)==\}", re.S)


def clean_source(text):
    """Text as printed: no boneyard, no [[notes]], no Quill comments; line breaks kept."""
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ").lstrip("﻿")
    t = _blank_out(t, _BONEYARD)
    t = _blank_out(t, _NOTE)
    t = _blank_out(t, _COMMENT)
    return _HILITE.sub(lambda m: m.group(1), t)


def is_character(s):
    core = EXT_RX.sub("", s).rstrip(" ^").strip()
    if not core or core.endswith(":") or not any(c.isalpha() for c in core):
        return False
    return core == core.upper() and any(c.isupper() for c in core)


def is_transition(s):
    u = s.strip()
    return u == u.upper() and (u.endswith("TO:") or u in TRANSITION_WORDS)


def title_page(text):
    """Fountain title page keys at the very top ("Title: ...") -> ({key: value}, the text after them)."""
    lines = (text or "").replace("\r\n", "\n").lstrip("﻿").split("\n")
    if not lines or not re.match(r"^\s*(%s)\s*:" % "|".join(re.escape(k) for k in TITLE_KEYS), lines[0], re.I):
        return {}, text or ""
    keys, cur, i = {}, None, 0
    while i < len(lines) and lines[i].strip():
        m = re.match(r"^\s*([A-Za-z][A-Za-z ]{0,20}):\s*(.*)$", lines[i])
        if m and not lines[i].startswith((" ", "\t")):
            cur = m.group(1).strip().lower()
            keys[cur] = m.group(2).strip()
        elif cur:
            keys[cur] = (keys[cur] + "\n" + lines[i].strip()).strip()
        i += 1
    return keys, "\n".join(lines[i:])


def parse(text):
    """-> list of elements {type, text, line, scene_no?, dual?, level?}. `line` is the 0-based line in `text`."""
    src = clean_source(text)
    lines = src.split("\n")
    out, in_dialogue, action = [], False, None
    n = len(lines)

    def blank(i):
        return i < 0 or i >= n or not lines[i].strip()

    for i, raw in enumerate(lines):
        s = raw.strip()
        if not s:
            in_dialogue = False
            action = None
            continue
        if in_dialogue:
            if s.startswith("(") and s.endswith(")"):
                out.append({"type": "paren", "text": s, "line": i})
            elif out and out[-1]["type"] == "dialogue":
                out[-1]["text"] += "\n" + s
            else:
                out.append({"type": "dialogue", "text": s, "line": i})
            continue
        prev_blank = blank(i - 1)
        el = None
        if re.fullmatch(r"={3,}", s):
            el = {"type": "pagebreak", "text": ""}
        elif s.startswith("#"):
            el = {"type": "section", "text": s.lstrip("#").strip(), "level": len(s) - len(s.lstrip("#"))}
        elif s.startswith("=") and not s.startswith("=="):
            el = {"type": "synopsis", "text": s[1:].strip()}
        elif s.startswith("!"):
            el = {"type": "action", "text": s[1:]}
        elif s.startswith("@") and len(s) > 1:
            el = {"type": "character", "text": s[1:].strip()}
        elif s.startswith(".") and not s.startswith("..") and len(s) > 1:
            el = {"type": "scene", "text": s[1:].strip()}
        elif s.startswith(">") and s.endswith("<") and len(s) > 2:
            el = {"type": "centered", "text": s[1:-1].strip()}
        elif s.startswith(">"):
            el = {"type": "transition", "text": s[1:].strip()}
        elif s.startswith("~"):
            el = {"type": "lyric", "text": s[1:].strip()}
        elif prev_blank and SCENE_RX.match(s):
            el = {"type": "scene", "text": s}
        elif prev_blank and blank(i + 1) and is_transition(s):
            el = {"type": "transition", "text": s}
        elif prev_blank and not blank(i + 1) and is_character(s):
            el = {"type": "character", "text": s}
        if el is None:
            if action is not None:
                action["text"] += "\n" + raw.rstrip()
                continue
            el = {"type": "action", "text": raw.rstrip().lstrip() if not raw.startswith("    ") else raw.rstrip()}
        el["line"] = i
        if el["type"] == "scene":
            m = SCENE_NO_RX.search(el["text"])
            if m:
                el["scene_no"] = m.group(1)
                el["text"] = el["text"][:m.start()].rstrip()
            el["text"] = el["text"].upper() if SCENE_RX.match(el["text"]) else el["text"]
        if el["type"] == "character":
            el["dual"] = el["text"].endswith("^")
            el["text"] = el["text"].rstrip(" ^").strip()
            in_dialogue = True
        out.append(el)
        action = el if el["type"] == "action" else None
    return out


def character_name(cue):
    """'MARA (V.O.)' -> 'MARA'."""
    prev = None
    s = cue.strip()
    while prev != s:
        prev = s
        s = EXT_RX.sub("", s).strip()
    return re.sub(r"\s+", " ", s).upper()


# ------------------------------------------------------------------------------------------------ emphasis
_ESC = {"\\*": "\x01", "\\_": "\x02"}
_EMPH = [(re.compile(r"\*\*\*(?=\S)(.+?)(?<=\S)\*\*\*"), "bi"), (re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*"), "b"),
         (re.compile(r"\*(?=\S)(.+?)(?<=\S)\*"), "i"),
         (re.compile(r"(?<![A-Za-z0-9])_(?=\S)(.+?)(?<=\S)_(?![A-Za-z0-9])"), "u")]


def _style(a, b):
    return "".join(c for c in "biu" if c in a or c in b)


def _runs(s, style):
    best = None
    for rx, st in _EMPH:
        m = rx.search(s)
        if m and (best is None or m.start() < best[0].start()):
            best = (m, st)
    if best is None:
        return [(s, style)] if s else []
    m, st = best
    return _runs(s[:m.start()], style) + _runs(m.group(1), _style(style, st)) + _runs(s[m.end():], style)


def runs(s, style=""):
    """Inline Fountain emphasis -> [(text, style)] with style made of b, i and u."""
    for k, v in _ESC.items():
        s = s.replace(k, v)
    out = []
    for t, st in _runs(s, style):
        t = t.replace("\x01", "*").replace("\x02", "_")
        if out and out[-1][1] == st:
            out[-1] = (out[-1][0] + t, st)
        elif t:
            out.append((t, st))
    return out


def plain(s):
    return "".join(t for t, _ in runs(s))


# ------------------------------------------------------------------------------------------------ pages
LINES = 55                      # body lines on a page (Courier 12 pt = 6 lines an inch)
CPI = 10.0                      # Courier 12 pt: 10 characters an inch
# kind: (left edge in inches from the page edge, characters a line, blank lines before)
LAYOUT = {"scene": (1.5, 61, 1), "action": (1.5, 61, 1), "character": (3.7, 38, 1), "paren": (3.1, 26, 0),
          "dialogue": (2.5, 35, 0), "transition": (1.5, 61, 1), "centered": (1.5, 61, 1), "lyric": (2.5, 35, 1)}


def _breaks(p, width, hang=0):
    """Slices of one paragraph, broken at spaces (monospace, so characters are the measure). A word longer
    than a line is cut. Continuation lines are `hang` characters narrower."""
    out, i, n = [], 0, len(p)
    if n == 0:
        return [(0, 0)]
    while i < n:
        w = width - (hang if out else 0)
        if n - i <= w:
            out.append((i, n))
            break
        j = p.rfind(" ", i, i + w + 1)
        if j <= i:
            out.append((i, i + w))
            i += w
        else:
            out.append((i, j))
            i = j + 1
        while i < n and p[i] == " ":
            i += 1
    return out


def wrap(s, width):
    return [p[a:b].rstrip() for p in s.split(NL) for a, b in _breaks(p, width)]


def _wrap_runs(text, width, hang=0):
    """[[(text, style)], ...] one list per printed line. `hang` indents continuation lines (parentheticals)."""
    chars = [(c, st) for t, st in runs(text) for c in t]
    lines, para = [], []
    for item in chars + [(NL, "")]:
        if item[0] != NL:
            para.append(item)
            continue
        p = "".join(c for c, _ in para)
        for k, (a, b) in enumerate(_breaks(p, width, hang)):
            seg = []
            for c, st in para[a:b]:
                if seg and seg[-1][1] == st:
                    seg[-1] = (seg[-1][0] + c, st)
                else:
                    seg.append((c, st))
            if seg:
                seg[-1] = (seg[-1][0].rstrip(), seg[-1][1])
            lines.append(([(" " * hang, "")] if hang and k else []) + seg)
        para = []
    return lines


def _ends_sentence(line):
    t = "".join(x for x, _ in line).rstrip()
    return t.endswith((".", "?", "!", "...", "…", ".\"", "?\"", "!\""))


def _blocks(elements):
    """Group elements into blocks that are placed together: a dialogue block is the cue with its speech."""
    blocks, i = [], 0
    printable = [e for e in elements if e["type"] in PRINTED or e["type"] == "pagebreak"]
    while i < len(printable):
        e = printable[i]
        if e["type"] == "character":
            j = i + 1
            while j < len(printable) and printable[j]["type"] in ("paren", "dialogue"):
                j += 1
            blocks.append(("dialogue", printable[i:j]))
            i = j
        else:
            blocks.append((e["type"], [e]))
            i += 1
    return blocks


def _lines_of(e):
    """Printed lines of one element: [(kind, [(text, style)])]."""
    kind = e["type"]
    left, width, _ = LAYOUT[kind]
    text = e["text"]
    if kind in ("scene", "character", "transition"):
        text = text.upper() if kind != "transition" or text == text.upper() else text
    if kind == "lyric":
        text = "*" + text + "*" if text and not text.startswith("*") else text
    return [(kind, ln) for ln in _wrap_runs(text, width, hang=1 if kind == "paren" else 0)]


def paginate(elements):
    """Set the elements on pages. -> {'pages': [[(line_no, kind, runs, extra)]], 'starts': {element index: page},
    'lines_at': {element index: absolute line}}. Pages count from 1 (the title page is separate)."""
    pages, cur, starts, at = [], [], {}, {}
    index = {id(e): k for k, e in enumerate(elements)}

    def new_page():
        nonlocal cur
        pages.append(cur)
        cur = []

    def used():
        return cur[-1][0] + 1 if cur else 0

    def put(kind, ln, extra=None):
        cur.append((used(), kind, ln, extra))

    def gap(n):
        for _ in range(n if cur else 0):
            cur.append((used(), "blank", [], None))

    for kind, els in _blocks(elements):
        if kind == "pagebreak":
            if cur:
                new_page()
            continue
        if kind == "dialogue":
            cue = els[0]
            body = []
            for e in els:
                body += [(e, ln) for ln in _lines_of(e)]
            before = 1 if cur else 0
            room = LINES - used() - before
            if len(body) <= room:
                gap(before)
                for e, (k, ln) in body:
                    if id(e) not in at:
                        at[id(e)] = (len(pages), used())
                    put(k, ln)
                continue
            # split the speech across the page: keep 2+ lines each side, end on a sentence when possible
            cue_lines = [x for x in body if x[0] is cue]
            speech = body[len(cue_lines):]
            fit = room - len(cue_lines) - 1          # one line kept for (MORE)
            cut = None
            if fit >= 2 and len(speech) - fit >= 1:
                options = [k for k in range(2, min(fit, len(speech) - 2) + 1) if speech[k - 1][1][0] == "dialogue"]
                good = [k for k in options if _ends_sentence(speech[k - 1][1][1])]
                cut = (good or options or [None])[-1]
            if cut is None:
                if cur:
                    new_page()
                for e, (k, ln) in body:
                    if id(e) not in at:
                        at[id(e)] = (len(pages), used())
                    put(k, ln)
                continue
            gap(before)
            for e, (k, ln) in cue_lines + speech[:cut]:
                if id(e) not in at:
                    at[id(e)] = (len(pages), used())
                put(k, ln)
            put("more", [("(MORE)", "")])
            new_page()
            name = "".join(t for t, _ in cue_lines[0][1][1])
            put("character", [(character_name(name) + " (CONT'D)", "")])
            for e, (k, ln) in speech[cut:]:
                if id(e) not in at:
                    at[id(e)] = (len(pages), used())
                put(k, ln)
            continue
        e = els[0]
        lines = _lines_of(e)
        before = LAYOUT[kind][2] if cur else 0
        room = LINES - used() - before
        need = len(lines) + (2 if kind == "scene" else 0)   # a scene heading never ends a page on its own
        num = e.get("scene_no") if kind == "scene" else None
        if need <= room:
            gap(before)
            at[id(e)] = (len(pages), used())
            for j, (k, ln) in enumerate(lines):
                put(k, ln, num if j == 0 else None)
            continue
        if kind == "action" and room >= 2 and len(lines) - room >= 2:
            cut = room
            good = [k for k in range(2, room + 1) if _ends_sentence(lines[k - 1][1])]
            cut = good[-1] if good else room
            gap(before)
            at[id(e)] = (len(pages), used())
            for k, ln in lines[:cut]:
                put(k, ln)
            new_page()
            for k, ln in lines[cut:]:
                put(k, ln)
            continue
        if cur:
            new_page()
        at[id(e)] = (len(pages), used())
        for j, (k, ln) in enumerate(lines):
            if used() >= LINES:
                new_page()
            put(k, ln, num if j == 0 else None)
    if cur or not pages:
        pages.append(cur)
    for e in elements:
        if id(e) in at:
            starts[index[id(e)]] = at[id(e)][0] + 1
    lines_at = {index[k]: v[0] * LINES + v[1] for k, v in at.items() if k in index}
    return {"pages": pages, "starts": starts, "lines_at": lines_at}


def eighths(lines):
    """Script length in pages and eighths, the way schedules count it: 70 lines -> '1 2/8'."""
    e = max(1, int(round(lines * 8.0 / LINES)))
    whole, part = divmod(e, 8)
    return ("%d" % whole if whole else "") + (" " if whole and part else "") + ("%d/8" % part if part else "")


# ------------------------------------------------------------------------------------------------ statistics
TIME_RX = re.compile(r"[-–—]\s*([A-Z][A-Z ']+?)\s*$")


def analyze(chapters):
    """chapters: [(chapter name, title, text)] in reading order.
    -> {'pages', 'minutes', 'scenes': [...], 'characters': [...], 'int', 'ext', 'times', 'dialogue_share',
        'elements': count}."""
    all_els, owner = [], []
    for name, title, text in chapters:
        keys, body = title_page(text) if not all_els else ({}, text)
        offset = (text or "").replace("\r\n", "\n").count("\n") - body.replace("\r\n", "\n").count("\n")
        for e in parse(body):
            e["line"] = e.get("line", 0) + offset
            all_els.append(e)
            owner.append((name, title))
    lay = paginate(all_els)
    scenes, chars, cur_scene = [], {}, None
    words_dialogue = words_action = 0
    int_n = ext_n = 0
    times = {}
    scene_idx = [k for k, e in enumerate(all_els) if e["type"] == "scene"]
    for pos, k in enumerate(scene_idx):
        e = all_els[k]
        start = lay["lines_at"].get(k, 0)
        nxt = scene_idx[pos + 1] if pos + 1 < len(scene_idx) else None
        end = lay["lines_at"].get(nxt, start) if nxt is not None else len(lay["pages"]) * LINES
        if nxt is None and lay["pages"]:
            end = (len(lay["pages"]) - 1) * LINES + (lay["pages"][-1][-1][0] + 1 if lay["pages"][-1] else 0)
        head = plain(e["text"])
        up = head.upper()
        ie = "INT/EXT" if re.match(r"^(INT\.?/EXT|INT/EXT|I/E)", up) else "INT" if up.startswith("INT") else \
            "EXT" if up.startswith(("EXT", "EST")) else ""
        int_n += ie in ("INT", "INT/EXT")
        ext_n += ie in ("EXT", "INT/EXT")
        tm = TIME_RX.search(up)
        t = tm.group(1).strip() if tm else ""
        if t:
            times[t] = times.get(t, 0) + 1
        scenes.append({"n": len(scenes) + 1, "number": e.get("scene_no") or str(len(scenes) + 1), "heading": head,
                       "chapter": owner[k][0], "chapter_title": owner[k][1], "line": e["line"],
                       "page": lay["starts"].get(k, 1), "length": eighths(max(1, end - start)), "lines": max(1, end - start),
                       "characters": []})
    sc_of = {}
    pos = -1
    for k, e in enumerate(all_els):
        if e["type"] == "scene":
            pos += 1
        sc_of[k] = pos
    for k, e in enumerate(all_els):
        if e["type"] == "character":
            nm = character_name(plain(e["text"]))
            c = chars.setdefault(nm, {"name": nm, "speeches": 0, "words": 0, "scenes": set(), "first_page": lay["starts"].get(k, 1)})
            c["speeches"] += 1
            j = k + 1
            while j < len(all_els) and all_els[j]["type"] in ("paren", "dialogue"):
                if all_els[j]["type"] == "dialogue":
                    w = len(plain(all_els[j]["text"]).split())
                    c["words"] += w
                    words_dialogue += w
                j += 1
            if sc_of[k] >= 0:
                c["scenes"].add(sc_of[k])
                if nm not in scenes[sc_of[k]]["characters"]:
                    scenes[sc_of[k]]["characters"].append(nm)
        elif e["type"] in ("action", "centered", "lyric"):
            words_action += len(plain(e["text"]).split())
    clist = sorted(({"name": c["name"], "speeches": c["speeches"], "words": c["words"], "scenes": len(c["scenes"]),
                     "first_page": c["first_page"]} for c in chars.values()), key=lambda c: (-c["speeches"], c["name"]))
    pages = len(lay["pages"]) if any(lay["pages"]) else 0
    total = words_dialogue + words_action
    return {"pages": pages, "minutes": pages, "scenes": scenes, "characters": clist, "int": int_n, "ext": ext_n,
            "times": [{"time": k, "count": v} for k, v in sorted(times.items(), key=lambda kv: -kv[1])],
            "dialogue_share": round(100.0 * words_dialogue / total) if total else 0,
            "elements": sum(1 for e in all_els if e["type"] in PRINTED)}


# ------------------------------------------------------------------------------------------------ preview
def preview_html(text):
    """One chapter as script-shaped HTML (classes sc-*); every piece of text is escaped."""
    out = []
    for e in parse(text):
        t = e["type"]
        if t == "pagebreak":
            out.append('<div class="sc-break"></div>')
            continue
        if t in ("section", "synopsis"):
            out.append('<div class="sc-%s">%s</div>' % (t, html.escape(e["text"])))
            continue
        body = e["text"].upper() if t in ("scene", "character") else e["text"]
        parts = []
        for line in body.split("\n"):
            seg = "".join(_tag(tx, st) for tx, st in runs(line))
            parts.append(seg)
        extra = ' data-line="%d"' % e["line"]
        num = ('<span class="sc-no">%s</span>' % html.escape(e["scene_no"])) if e.get("scene_no") else ""
        out.append('<div class="sc-%s"%s>%s%s</div>' % (t, extra, num, "<br>".join(parts)))
    return "".join(out)


def _tag(t, st):
    s = html.escape(t)
    if "u" in st:
        s = "<u>%s</u>" % s
    if "i" in st:
        s = "<em>%s</em>" % s
    if "b" in st:
        s = "<strong>%s</strong>" % s
    return s


# ------------------------------------------------------------------------------------------------ files
def _title_info(meta, chapters):
    keys = {}
    if chapters:
        keys, _ = title_page(chapters[0][1])
    title = keys.get("title") or meta.get("title") or "Untitled"
    author = keys.get("author") or keys.get("authors") or meta.get("author") or ""
    credit = keys.get("credit") or "Written by"
    return {"title": plain(title.replace("\n", " ")), "credit": credit, "author": plain(author),
            "source": keys.get("source", ""), "draft": keys.get("draft date") or keys.get("date") or "",
            "contact": keys.get("contact", "")}


def _bodies(chapters):
    """[(title, text)] -> the elements of the whole script, title page keys left out of the first chapter."""
    els = []
    for k, (title, text) in enumerate(chapters):
        body = title_page(text)[1] if k == 0 else text
        els += parse(body)
    return els


def to_fountain(chapters, meta):
    """[(chapter title, text)] -> one Fountain file. Chapters become sections (# Act One) that other screenplay
    apps show in their outline and never print; Quill comments become Fountain [[notes]]."""
    info = _title_info(meta, chapters)
    head = ["Title: " + info["title"], "Credit: " + info["credit"]]
    if info["author"]:
        head.append("Author: " + info["author"])
    for k in ("source", "contact"):
        if info[k]:
            head.append("%s: %s" % (k.capitalize(), info[k].replace("\n", "\n    ")))
    head.append("Draft date: " + (info["draft"] or date.today().isoformat()))
    parts = ["\n".join(head), ""]
    for k, (title, text) in enumerate(chapters):
        body = title_page(text)[1] if k == 0 else text
        body = _COMMENT.sub(lambda m: "[[" + m.group(0)[3:-3].strip() + "]]", body)
        body = _HILITE.sub(lambda m: m.group(1), body)
        parts += ["# " + title, "", body.strip("\n"), ""]
    return "\n".join(parts).rstrip("\n") + "\n"


_FDX_TYPE = {"scene": "Scene Heading", "action": "Action", "character": "Character", "paren": "Parenthetical",
             "dialogue": "Dialogue", "transition": "Transition", "centered": "Action", "lyric": "Dialogue"}


def _xml(s):
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", s)
    return html.escape(s, quote=True)


def _fdx_text(s, upper=False):
    out = []
    for t, st in runs(s):
        t = t.upper() if upper else t
        style = "+".join(n for c, n in (("b", "Bold"), ("i", "Italic"), ("u", "Underline")) if c in st)
        out.append('<Text%s>%s</Text>' % (' Style="%s"' % style if style else "", _xml(t)))
    return "".join(out) or "<Text></Text>"


def to_fdx(chapters, meta):
    """Final Draft XML (.fdx): scene headings, action, character, parenthetical, dialogue, transition, a title page."""
    info = _title_info(meta, chapters)
    paras, new_page = [], False
    for e in _bodies(chapters):
        t = e["type"]
        if t == "pagebreak":
            new_page = True
            continue
        if t not in _FDX_TYPE:
            continue
        lines = e["text"].split("\n") if t in ("action", "dialogue") else [e["text"]]
        for ln in lines:
            attrs = ' Type="%s"' % _FDX_TYPE[t]
            if t == "scene" and e.get("scene_no"):
                attrs += ' Number="%s"' % _xml(e["scene_no"])
            if t == "centered":
                attrs += ' Alignment="Center"'
            if new_page:
                attrs += ' StartsNewPage="Yes"'
                new_page = False
            paras.append("    <Paragraph%s>%s</Paragraph>" % (attrs, _fdx_text(ln, upper=t in ("scene", "character"))))
    tp = ['      <Paragraph Alignment="Center"><Text>%s</Text></Paragraph>' % _xml(info["title"]),
          '      <Paragraph Alignment="Center"><Text></Text></Paragraph>',
          '      <Paragraph Alignment="Center"><Text>%s</Text></Paragraph>' % _xml(info["credit"])]
    if info["author"]:
        tp.append('      <Paragraph Alignment="Center"><Text>%s</Text></Paragraph>' % _xml(info["author"]))
    if info["contact"]:
        tp += ['      <Paragraph Alignment="Left"><Text>%s</Text></Paragraph>' % _xml(c) for c in info["contact"].split("\n")]
    return ('<?xml version="1.0" encoding="UTF-8" standalone="no" ?>\n'
            '<FinalDraft DocumentType="Script" Template="No" Version="5">\n  <Content>\n%s\n  </Content>\n'
            '  <TitlePage>\n    <Content>\n%s\n    </Content>\n  </TitlePage>\n</FinalDraft>\n'
            % ("\n".join(paras), "\n".join(tp)))


# ------------------------------------------------------------------------------------------------ PDF
PT = 72.0
PAGE_W, PAGE_H = 8.5 * PT, 11.0 * PT
TOP = 1.0 * PT
FONT = {"": "F1", "b": "F2", "i": "F3", "bi": "F4", "u": "F1", "bu": "F2", "iu": "F3", "biu": "F4"}
FACES = (("F1", "Courier"), ("F2", "Courier-Bold"), ("F3", "Courier-Oblique"), ("F4", "Courier-BoldOblique"))


class _Enc:
    """Text for the PDF's built-in Courier (WinAnsi): accents kept, anything else becomes '?' and is counted."""

    def __init__(self):
        self.missing = 0

    def __call__(self, s):
        out = bytearray()
        for ch in s:
            try:
                b = ch.encode("cp1252")
            except UnicodeEncodeError:
                base = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore")
                b = base[:1] if base else b"?"
                if not base:
                    self.missing += 1
            for x in b:
                if x in (0x28, 0x29, 0x5C):
                    out += b"\\" + bytes([x])
                elif x < 32 or x > 126:
                    out += b"\\%03o" % x
                else:
                    out.append(x)
        return bytes(out)


def _x_for(kind, text_len):
    left, width, _ = LAYOUT.get(kind, LAYOUT["action"])
    if kind == "transition":
        return 7.5 * PT - text_len * PT / CPI
    if kind == "centered":
        return 1.5 * PT + (6.0 * PT - text_len * PT / CPI) / 2
    if kind == "more":
        return 3.7 * PT
    return left * PT


def to_pdf(chapters, meta, out_path):
    """Write an industry-layout screenplay PDF. -> {'pages': body pages, 'missing': characters set as '?'}."""
    info = _title_info(meta, chapters)
    els = _bodies(chapters)
    lay = paginate(els)
    enc = _Enc()
    streams = []

    def line_ops(x, y, segs):
        ops = []
        for t, st in segs:
            if not t:
                continue
            ops.append(b"BT /%s 12 Tf %.2f %.2f Td (%s) Tj ET" % (FONT.get(st, "F1").encode(), x, y, enc(t)))
            if "u" in st and t.strip():
                ops.append(b"0.6 w %.2f %.2f m %.2f %.2f l S" % (x, y - 1.6, x + len(t) * PT / CPI, y - 1.6))
            x += len(t) * PT / CPI
        return ops

    # title page
    tops = []
    y = PAGE_H - 3.5 * PT
    for text, gap_after in ((info["title"].upper(), 4), (info["credit"], 2), (info["author"], 0)):
        if text:
            for ln in wrap(text, 58):
                tops += line_ops(PAGE_W / 2 - len(ln) * PT / CPI / 2, y, [(ln, "u" if text == info["title"].upper() else "")])
                y -= 12
            y -= 12 * gap_after
    if info["source"]:
        for ln in wrap(info["source"], 58):
            tops += line_ops(PAGE_W / 2 - len(ln) * PT / CPI / 2, y, [(ln, "")])
            y -= 12
    yb = 1.0 * PT + 12 * 4
    for ln in [x for x in (info["contact"].split("\n") if info["contact"] else [])][:6]:
        tops += line_ops(1.5 * PT, yb, [(ln, "")])
        yb -= 12
    draft = info["draft"] or date.today().isoformat()
    tops += line_ops(7.5 * PT - len(draft) * PT / CPI, 1.0 * PT + 12 * 4, [(draft, "")])
    streams.append(b"\n".join(tops))
    for p, page in enumerate(lay["pages"]):
        ops = []
        if p > 0:
            num = "%d." % (p + 1)
            ops += line_ops(7.5 * PT - len(num) * PT / CPI, PAGE_H - 0.5 * PT - 12, [(num, "")])
        for line_no, kind, segs, num in page:
            if kind == "blank" or not segs:
                continue
            y = PAGE_H - TOP - 12 - line_no * 12
            text_len = sum(len(t) for t, _ in segs)
            ops += line_ops(_x_for(kind, text_len), y, segs)
            if num:   # scene numbers in both margins, as shooting scripts carry them
                ops += line_ops(1.5 * PT - (len(num) + 3) * PT / CPI, y, [(num, "")])
                ops += line_ops(7.6 * PT, y, [(num, "")])
        streams.append(b"\n".join(ops))
    objs = []

    def add(body):
        objs.append(body)
        return len(objs)

    cat = add(None)
    pages_id = add(None)
    font_ids = [add(b"<< /Type /Font /Subtype /Type1 /BaseFont /%s /Encoding /WinAnsiEncoding >>" % f.encode())
                for _, f in FACES]
    res = b"<< /Font << " + b" ".join(b"/%s %d 0 R" % (n.encode(), i) for (n, _), i in zip(FACES, font_ids)) + b" >> >>"
    kids = []
    for s in streams:
        data = zlib.compress(s, 9)
        cid = add(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(data) + data + b"\nendstream")
        kids.append(add(b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792] /Resources %s /Contents %d 0 R >>"
                        % (pages_id, res, cid)))
    objs[cat - 1] = b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id
    objs[pages_id - 1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(b"%d 0 R" % k for k in kids), len(kids))
    info_id = add(b"<< /Title (%s) /Author (%s) /Creator (Stark Quill) /Producer (Stark Quill) >>"
                  % (enc(info["title"]), enc(info["author"])))
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offs = []
    for i, body in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for o in offs:
        out += b"%010d 00000 n \n" % o
    out += b"trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, cat, info_id, xref)
    with open(out_path, "wb") as f:
        f.write(bytes(out))
    return {"pages": len(lay["pages"]), "missing": enc.missing}


def pdf_text(path):
    """The text drawn on each page of a PDF written by to_pdf (for tests and the export check)."""
    with open(path, "rb") as f:
        data = f.read()
    pages = []
    for m in re.finditer(rb"stream\n(.*?)\nendstream", data, re.S):
        try:
            s = zlib.decompress(m.group(1))
        except zlib.error:
            continue
        lines = []
        for t in re.finditer(rb"\(((?:\\.|[^\\)])*)\) Tj", s):
            raw = re.sub(rb"\\([0-7]{3})", lambda x: bytes([int(x.group(1), 8)]), t.group(1))
            raw = re.sub(rb"\\(.)", rb"\1", raw)
            lines.append(raw.decode("cp1252", "replace"))
        pages.append(lines)
    return pages


def check_pdf(path):
    """Structural check of a script PDF: header, every object the xref names, page count. -> [problems]."""
    probs = []
    with open(path, "rb") as f:
        data = f.read()
    if not data.startswith(b"%PDF-1.") or not data.rstrip().endswith(b"%%EOF"):
        probs.append("not a complete PDF file")
        return probs
    m = re.search(rb"startxref\s+(\d+)", data[-200:])
    if not m:
        return probs + ["no cross-reference table"]
    x = int(m.group(1))
    if data[x:x + 4] != b"xref":
        return probs + ["the cross-reference table is not where the file says"]
    head = re.match(rb"xref\n0 (\d+)\n", data[x:])
    n = int(head.group(1)) if head else 0
    entries = re.findall(rb"(\d{10}) 00000 n ", data[x:x + 40 + n * 20])
    for i, off in enumerate(entries, 1):
        if not data[int(off):].startswith(b"%d 0 obj" % i):
            probs.append("object %d is not where the table says" % i)
    count = re.search(rb"/Type /Pages /Kids \[[^\]]*\] /Count (\d+)", data)
    if not count or int(count.group(1)) < 1:
        probs.append("no pages")
    return probs


def check_fdx(text):
    """Is this well-formed Final Draft XML with content? -> [problems]."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(text.encode("utf-8"))
    except ET.ParseError as e:
        return ["not well-formed XML (%s)" % e]
    if root.tag != "FinalDraft" or root.get("DocumentType") != "Script":
        return ["not a Final Draft script"]
    if root.find("Content") is None or not root.findall("Content/Paragraph"):
        return ["the script has no paragraphs"]
    bad = [p.get("Type") for p in root.findall("Content/Paragraph") if p.get("Type") not in set(_FDX_TYPE.values())]
    return ["unknown paragraph type %s" % bad[0]] if bad else []


STARTER = ("FADE IN:\n\nINT. KITCHEN - NIGHT\n\nA single bulb. Rain against the window.\n\nMARA (40s, tired, sharp) "
           "counts coins on the table.\n\nMARA\n(under her breath)\nNot enough. Never enough.\n\nThe back door rattles.\n")

CHEATSHEET = [
    ["Scene heading", "Start the line with INT. or EXT. (or a full stop to force one): INT. KITCHEN - NIGHT"],
    ["Action", "Plain lines. Start with ! to keep a line in capitals as action."],
    ["Character", "A name in capitals on its own line, with the speech on the next line. Start with @ to force one."],
    ["Parenthetical", "(quietly) on its own line under the name."],
    ["Dialogue", "The lines under the name, up to the next empty line."],
    ["Transition", "CUT TO: in capitals with empty lines around it, or start the line with >."],
    ["Centred", "> THE END <"],
    ["Notes and sections", "[[a note]] and # Act One are never printed. === starts a new page."],
    ["Emphasis", "*italic*, **bold**, _underline_"],
]
