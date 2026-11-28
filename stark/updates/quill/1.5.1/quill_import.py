"""Stark Quill import: bring a manuscript you already have in as a new book, one plain chapter file per chapter.

Reads Word (.docx), OpenDocument (.odt), Markdown (.md), plain text (.txt) and Fountain screenplays (.fountain).
Italics and bold come across as *italics* and **bold**; Word and OpenDocument footnotes come across as [^1]
notes at the end of their chapter; tracked deletions are left out. The text is split into chapters at headings,
at lines such as "Chapter 12" or "Prologue", or at page breaks, in that order of preference. Nothing is ever
dropped: text before the first chapter becomes a chapter of its own. Standard library only, no network; this
module reads bytes and returns chapters, writer_app.py writes the files.
"""
from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET

MAX_BYTES = 5 * 1024 * 1024
MAX_CHAPTERS = 400
EXTS = (".docx", ".odt", ".md", ".markdown", ".txt", ".fountain")
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
NUMBER_WORDS = ("one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
                "seventeen eighteen nineteen twenty thirty forty fifty").split()
CHAPTER_RX = re.compile(r"^(?:chapter|ch\.|part|book|prologue|epilogue|interlude|afterword|foreword|preface|"
                        r"introduction|conclusion|acknowledg(?:e)?ments|appendix)\b.{0,80}$", re.I)
BARE_RX = re.compile(r"^(?:\d{1,3}|[IVXLC]{1,7}|%s)[.:]?$" % "|".join(NUMBER_WORDS), re.I)
SCENE_BREAK_RX = re.compile(r"^\s*(?:\*\s*){3,}$|^\s*(?:#\s*){1,3}$|^\s*(?:~\s*){1,3}$|^\s*(?:-\s*){3,}$|^\s*§\s*$")


class ImportProblem(ValueError):
    pass


def _words(s):
    return sum(1 for w in (s or "").split() if any(c.isalnum() for c in w))


def _md(runs):
    """[(text, bold, italic)] -> one line with *italics* and **bold**; spaces stay outside the markers."""
    merged = []
    for t, b, i in runs:
        if merged and merged[-1][1] == b and merged[-1][2] == i:
            merged[-1] = (merged[-1][0] + t, b, i)
        elif t:
            merged.append((t, b, i))
    out = []
    for t, b, i in merged:
        if not t.strip() or not (b or i):
            out.append(t)
            continue
        lead = t[:len(t) - len(t.lstrip())]
        trail = t[len(t.rstrip()):]
        mk = "***" if b and i else "**" if b else "*"
        out.append(lead + mk + t.strip() + mk + trail)
    return re.sub(r"[ \t]+", " ", "".join(out)).strip()


# ------------------------------------------------------------------------------------------------ Word
def _on(el, tag):
    x = el.find(W + tag) if el is not None else None
    if x is None:
        return False
    v = x.get(W + "val")
    return v not in ("0", "false", "none")


def _docx_styles(z):
    names, levels = {}, {}
    try:
        root = ET.fromstring(z.read("word/styles.xml"))
    except (KeyError, ET.ParseError):
        return names, levels
    for st in root.iter(W + "style"):
        sid = st.get(W + "styleId") or ""
        nm = st.find(W + "name")
        names[sid] = (nm.get(W + "val") if nm is not None else sid).lower()
        ol = st.find(W + "pPr/" + W + "outlineLvl")
        if ol is not None and (ol.get(W + "val") or "").isdigit():
            levels[sid] = int(ol.get(W + "val")) + 1
        st_rpr = st.find(W + "rPr")
        names[sid + "#b"] = _on(st_rpr, "b")
        names[sid + "#i"] = _on(st_rpr, "i")
    return names, levels


def _docx_notes(z, part, tag):
    out = {}
    try:
        root = ET.fromstring(z.read(part))
    except (KeyError, ET.ParseError):
        return out
    for n in root.iter(W + tag):
        nid = n.get(W + "id")
        if nid is None or (n.get(W + "type") or "normal") != "normal":
            continue
        text = " ".join(_md(_docx_runs(p, {})[0]) for p in n.iter(W + "p")).strip()
        if text:
            out[nid] = text
    return out


def _docx_runs(p, styles, notes=None, refs=None):
    """Runs of one paragraph -> ([(text, bold, italic)], page_break_before_or_inside)."""
    runs, brk = [], False
    pstyle = p.find(W + "pPr/" + W + "pStyle")
    sid = pstyle.get(W + "val") if pstyle is not None else ""
    base_b, base_i = styles.get(sid + "#b", False), styles.get(sid + "#i", False)

    def walk(el):
        nonlocal brk
        for ch in el:
            tag = ch.tag
            if tag in (W + "del", W + "moveFrom", W + "pPr", W + "rPr"):
                continue
            if tag == W + "r":
                rpr = ch.find(W + "rPr")
                b = _on(rpr, "b") if rpr is not None and rpr.find(W + "b") is not None else base_b
                i = _on(rpr, "i") if rpr is not None and rpr.find(W + "i") is not None else base_i
                for x in ch:
                    if x.tag == W + "t":
                        runs.append((x.text or "", b, i))
                    elif x.tag == W + "tab":
                        runs.append((" ", b, i))
                    elif x.tag == W + "br":
                        if x.get(W + "type") == "page":
                            brk = True
                        else:
                            runs.append((" ", b, i))
                    elif x.tag == W + "footnoteReference" and notes is not None:
                        nid = x.get(W + "id")
                        if nid in notes:
                            refs.append(notes[nid])
                            runs.append(("[^%d]" % len(refs), False, False))
            else:
                walk(ch)
    walk(p)
    ppr = p.find(W + "pPr")
    if ppr is not None and _on(ppr, "pageBreakBefore"):
        brk = True
    return runs, brk


def read_docx(data):
    """-> (paragraphs [(kind, text)], info {title, author})."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        doc = ET.fromstring(z.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError):
        raise ImportProblem("That does not look like a Word .docx file. In Word, use Save As and choose Word Document.")
    names, levels = _docx_styles(z)
    notes = _docx_notes(z, "word/footnotes.xml", "footnote")
    notes.update({"e" + k: v for k, v in _docx_notes(z, "word/endnotes.xml", "endnote").items()})
    info = {}
    try:
        core = ET.fromstring(z.read("docProps/core.xml"))
        for el in core:
            if el.tag.endswith("}title") and (el.text or "").strip():
                info["title"] = el.text.strip()
            if el.tag.endswith("}creator") and (el.text or "").strip():
                info["author"] = el.text.strip()
    except (KeyError, ET.ParseError):
        pass
    body = doc.find(W + "body")
    paras = []
    refs = []

    def para(p):
        pstyle = p.find(W + "pPr/" + W + "pStyle")
        sid = pstyle.get(W + "val") if pstyle is not None else ""
        name = names.get(sid, sid.lower())
        ol = p.find(W + "pPr/" + W + "outlineLvl")
        level = int(ol.get(W + "val")) + 1 if ol is not None and (ol.get(W + "val") or "").isdigit() else levels.get(sid)
        m = re.match(r"heading\s*(\d)", name)
        if m:
            level = int(m.group(1))
        runs, brk = _docx_runs(p, names, notes, refs)
        for x in p.iter(W + "endnoteReference"):
            nid = "e" + (x.get(W + "id") or "")
            if nid in notes:
                refs.append(notes[nid])
                runs.append(("[^%d]" % len(refs), False, False))
        if brk:
            paras.append(("pagebreak", ""))
        text = _md(runs)
        if name == "title":
            paras.append(("title", re.sub(r"\*", "", text)))
        elif level and level <= 6 and text:
            paras.append(("h%d" % level, re.sub(r"\*", "", text)))
        else:
            paras.append(("p", text))

    def walk(el):
        for ch in el:
            if ch.tag == W + "p":
                para(ch)
            elif ch.tag in (W + "tbl", W + "tr", W + "tc", W + "sdt", W + "sdtContent", W + "customXml"):
                walk(ch)
    if body is not None:
        walk(body)
    return paras, info, refs


# ------------------------------------------------------------------------------------------------ OpenDocument
TX = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
ST = "{urn:oasis:names:tc:opendocument:xmlns:style:1.0}"
FO = "{urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0}"
OF = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"


def read_odt(data):
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        root = ET.fromstring(z.read("content.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError):
        raise ImportProblem("That does not look like an OpenDocument .odt file.")
    fmt, brk = {}, set()
    for st in root.iter(ST + "style"):
        nm = st.get(ST + "name")
        tp = st.find(ST + "text-properties")
        if tp is not None:
            fmt[nm] = (tp.get(FO + "font-weight") == "bold", tp.get(FO + "font-style") == "italic")
        pp = st.find(ST + "paragraph-properties")
        if pp is not None and pp.get(FO + "break-before") == "page":
            brk.add(nm)
    paras, refs = [], []

    def runs_of(el, b=False, i=False):
        out = [(el.text or "", b, i)]
        for ch in el:
            if ch.tag == TX + "span":
                bb, ii = fmt.get(ch.get(TX + "style-name"), (False, False))
                out += runs_of(ch, b or bb, i or ii)
            elif ch.tag == TX + "s":
                out.append((" " * int(ch.get(TX + "c") or 1), b, i))
            elif ch.tag in (TX + "tab", TX + "line-break"):
                out.append((" ", b, i))
            elif ch.tag == TX + "note":
                body = ch.find(TX + "note-body")
                if body is not None:
                    refs.append(" ".join(_md(runs_of(p)) for p in body.iter(TX + "p")).strip())
                    out.append(("[^%d]" % len(refs), False, False))
            elif ch.tag == OF + "annotation":
                pass
            else:
                out += runs_of(ch, b, i)
            out.append((ch.tail or "", b, i))
        return out

    body = root.find(OF + "body/" + OF + "text")

    def walk(el):
        for ch in el:
            if ch.tag in (TX + "p", TX + "h"):
                sn = ch.get(TX + "style-name")
                if sn in brk:
                    paras.append(("pagebreak", ""))
                b, i = fmt.get(sn, (False, False))
                text = _md(runs_of(ch, b, i))
                if ch.tag == TX + "h":
                    lvl = int(ch.get(TX + "outline-level") or 1)
                    paras.append(("h%d" % min(6, lvl), re.sub(r"\*", "", text)))
                elif sn and sn.lower() == "title":
                    paras.append(("title", re.sub(r"\*", "", text)))
                else:
                    paras.append(("p", text))
            elif ch.tag in (TX + "list", TX + "list-item", TX + "section", "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}table",
                            "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}table-row",
                            "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}table-cell"):
                walk(ch)
    if body is not None:
        walk(body)
    return paras, {}, refs


# ------------------------------------------------------------------------------------------------ text
def decode_text(data):
    for enc in ("utf-8-sig", "utf-16") if data[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def read_text(text, markdown=True):
    """Markdown or plain text -> paragraphs. '# Heading' lines are headings (Markdown); blank lines end paragraphs;
    lines inside a paragraph are kept on their own lines (poetry, letters)."""
    info = {}
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    fm = re.match(r"^---\n(.*?)\n---\n", text, re.S) if markdown else None
    if fm:
        for ln in fm.group(1).split("\n"):
            m = re.match(r"^(title|author)\s*:\s*[\"']?(.*?)[\"']?\s*$", ln, re.I)
            if m and m.group(2):
                info[m.group(1).lower()] = m.group(2)
        text = text[fm.end():]
    paras, buf = [], []

    def flush():
        if buf:
            paras.append(("p", "\n".join(x.rstrip() for x in buf).strip()))
            del buf[:]
    for ln in text.split("\n"):
        m = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", ln) if markdown else None
        if m:
            flush()
            paras.append(("h%d" % len(m.group(1)), m.group(2).strip()))
        elif not ln.strip():
            flush()
        elif ln.strip() == "\f":
            flush()
            paras.append(("pagebreak", ""))
        else:
            buf.append(ln)
    flush()
    return paras, info, []


# ------------------------------------------------------------------------------------------------ chapters
def _is_chapter_line(t):
    s = re.sub(r"[*_]", "", t).strip()
    return bool(s) and _words(s) <= 12 and (CHAPTER_RX.match(s) is not None or BARE_RX.match(s) is not None)


def split(paras, refs=()):
    """paragraphs -> (chapters [{title, text}], how). Headings first, then chapter lines, then page breaks."""
    heads = {}
    for k, t in paras:
        if k.startswith("h"):
            heads[int(k[1:])] = heads.get(int(k[1:]), 0) + 1
    level = next((lv for lv in sorted(heads) if heads[lv] >= 2), None)
    marks = []
    how = "one"
    if level is not None:
        marks = [i for i, (k, _) in enumerate(paras) if k == "h%d" % level]
        how = "headings"
    else:
        cand = [i for i, (k, t) in enumerate(paras) if k in ("p",) + tuple("h%d" % x for x in heads) and _is_chapter_line(t)]
        if len(cand) >= 2:
            marks, how = cand, "chapter lines"
        else:
            pb = [i for i, (k, _) in enumerate(paras) if k == "pagebreak"]
            if len(pb) >= 2:
                marks, how = pb, "page breaks"
    chapters = []

    def body_of(items):
        out = []
        for k, t in items:
            if k == "pagebreak" or (k == "p" and not t):
                continue
            if SCENE_BREAK_RX.match(t):
                out.append("***")
            elif k.startswith("h") or k == "title":
                out.append("## " + t)
            else:
                out.append(t)
        return "\n\n".join(out)

    edges = marks + [len(paras)]
    head_items = paras[:marks[0]] if marks else paras
    lead = body_of([p for p in head_items if p[0] != "title"])
    if marks and lead.strip():
        chapters.append({"title": "Title page" if _words(lead) <= 40 else "Opening", "text": lead})
    if not marks:
        chapters.append({"title": "Chapter One", "text": lead})
    for n, start in enumerate(marks):
        items = paras[start:edges[n + 1]]
        k, t = items[0]
        if how == "page breaks":
            items = items[1:]
            first = next(((kk, tt) for kk, tt in items if tt.strip()), None)
            if first and _words(first[1]) <= 10 and not re.search(r"[.!?\"”]$", first[1].strip()):
                title = re.sub(r"[*_]", "", first[1]).strip()
                items = items[items.index(first) + 1:]
            else:
                title = "Chapter %d" % (n + 1)
        else:
            title = re.sub(r"[*_]", "", t).strip()
            items = items[1:]
            nxt = next(((kk, tt) for kk, tt in items if tt.strip()), None)
            if how == "chapter lines" and nxt and nxt[0] == "p" and _words(nxt[1]) <= 8 and \
                    not re.search(r"[.!?\"”,;]$", nxt[1].strip()) and not _is_chapter_line(nxt[1]):
                title = "%s: %s" % (title.rstrip(".:"), re.sub(r"[*_]", "", nxt[1]).strip())
                items = items[items.index(nxt) + 1:]
        chapters.append({"title": title[:120] or "Chapter %d" % (n + 1), "text": body_of(items)})
    # footnotes go to the end of the chapter that cites them, as [^n]: definitions
    if refs:
        for ch in chapters:
            used = sorted({int(x) for x in re.findall(r"\[\^(\d+)\]", ch["text"])})
            if used:
                ch["text"] += "\n\n" + "\n".join("[^%d]: %s" % (u, refs[u - 1]) for u in used if 0 < u <= len(refs))
    return chapters, how


def looks_like_fountain(text):
    return len(re.findall(r"(?m)^(?:INT|EXT|INT\./EXT|I/E)[. ].+$", text)) >= 3


def read(filename, data):
    """-> {'title', 'author', 'kind', 'chapters': [{title, text, words}], 'how', 'words', 'notes'}."""
    if len(data) > MAX_BYTES:
        raise ImportProblem("That file is over 5 MB. Save the manuscript without pictures and try again.")
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    if ext not in EXTS:
        raise ImportProblem("Quill can import Word (.docx), OpenDocument (.odt), Markdown (.md), text (.txt) "
                           "and Fountain (.fountain) files. For other formats, save as Word first.")
    stem = re.sub(r"\.[^.]+$", "", filename.replace("\\", "/").split("/")[-1]).strip() or "Imported book"
    kind, notes, refs = "novel", [], []
    if ext == ".docx":
        paras, info, refs = read_docx(data)
    elif ext == ".odt":
        paras, info, refs = read_odt(data)
    else:
        text = decode_text(data)
        if ext == ".fountain" or (ext == ".txt" and looks_like_fountain(text)):
            return _read_fountain(text, stem)
        paras, info, _ = read_text(text, markdown=ext in (".md", ".markdown", ".txt"))
    title = info.get("title") or next((t for k, t in paras if k == "title" and t), "") or stem
    chapters, how = split(paras, refs)
    chapters = [c for c in chapters if c["text"].strip() or c["title"]]
    if len(chapters) > MAX_CHAPTERS:
        raise ImportProblem("That would make %d chapters; Quill takes up to %d. Check the file's headings."
                           % (len(chapters), MAX_CHAPTERS))
    for c in chapters:
        c["words"] = _words(c["text"])
    if refs:
        notes.append("%d footnotes kept as [^n] notes at the end of their chapters." % len(refs))
    notes.append({"headings": "Split at the headings.", "chapter lines": "Split at lines such as \"Chapter 3\".",
                  "page breaks": "Split at page breaks.",
                  "one": "No chapter headings found, so it comes in as one chapter. Split it later with "
                         "New chapter after this one."}[how])
    return {"title": title[:200], "author": (info.get("author") or "")[:200], "kind": kind, "chapters": chapters,
            "how": how, "words": sum(c["words"] for c in chapters), "notes": notes}


def _read_fountain(text, stem):
    import quill_script as S
    keys, body = S.title_page(text)
    body = body.replace("\r\n", "\n")
    parts = re.split(r"(?m)^#(?!#)\s*(.+)$", body)
    chapters = []
    if len(parts) >= 3:
        if parts[0].strip():
            chapters.append({"title": "Opening", "text": parts[0].strip("\n")})
        for i in range(1, len(parts) - 1, 2):
            chapters.append({"title": parts[i].strip()[:120] or "Sequence", "text": parts[i + 1].strip("\n")})
        how = "sections"
    else:
        chapters.append({"title": "Screenplay", "text": body.strip("\n")})
        how = "one"
    for c in chapters:
        c["words"] = _words(c["text"])
    title = S.plain(keys.get("title", "").replace("\n", " ")) or stem
    return {"title": title[:200], "author": S.plain(keys.get("author") or keys.get("authors") or "")[:200],
            "kind": "screenplay", "chapters": chapters, "how": how, "words": sum(c["words"] for c in chapters),
            "notes": ["Fountain screenplay: the book opens in screenplay mode."
                      + (" Split at its # sections." if how == "sections" else "")]}
