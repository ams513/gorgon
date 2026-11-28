"""Stark Quill studio tools: comments and footnotes kept inside the chapter text, comparing a chapter with one of
its backups, project templates (novel, short story, screenplay, essay, thesis, nonfiction, 30-day novel), writing
sprints, and scene details (point of view, setting, story time, plotlines) with a plot grid.

Comments use the plain-text CriticMarkup form {>>a note<<}, or {==these words==}{>>a note<<} to pin a note to a
phrase; footnotes use the Markdown form [^1] with a line "[^1]: the note" anywhere in the chapter. Both stay in the
chapter file, readable in any text editor. Exports never carry comments; footnotes become numbered notes at the
end of their chapter. Plain rules on plain data: no network, no file access, no clock of its own.
"""
from __future__ import annotations

import difflib
import re
import uuid
from datetime import date, timedelta

# ======================================================================================================
# 1. Comments and footnotes
# ======================================================================================================
COMMENT_RX = re.compile(r"(?P<lead>[ \t]*)(?:\{==(?P<hl>.*?)==\}[ \t]*)?\{>>(?P<note>.*?)<<\}", re.S)
FOOTDEF_RX = re.compile(r"(?m)^\[\^([^\]\s]{1,24})\]:[ \t]*(.*(?:\n(?: {2,}|\t)\S.*)*)\n?")
FOOTREF_RX = re.compile(r"\[\^([^\]\s]{1,24})\](?!:)")
SUP = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")


def sup(n):
    return str(n).translate(SUP)


def comments(text):
    """Every comment in a chapter: [{start, end, note, quote, pinned, line}] (positions in JS string units are the
    caller's job; Quill's texts keep to the Basic Multilingual Plane in practice, and the page re-finds by text)."""
    out = []
    for m in COMMENT_RX.finditer(text or ""):
        start = m.start() + len(m.group("lead"))
        if m.group("hl") is not None:
            quote, pinned = m.group("hl"), True
        else:
            para = text[:start].rsplit("\n\n", 1)[-1]
            quote, pinned = para.strip()[-70:], False
        out.append({"start": start, "end": m.end(), "note": m.group("note").strip(), "quote": re.sub(r"\s+", " ", quote).strip(),
                    "pinned": pinned, "line": text.count("\n", 0, start), "raw": text[start:m.end()]})
    return out


def strip_comments(text):
    """The text as readers see it: comments gone, pinned words kept, no doubled spaces left behind."""
    def repl(m):
        if m.group("hl") is not None:
            return m.group("lead") + m.group("hl")
        nxt = text[m.end():m.end() + 1]
        return "" if (not nxt or nxt in " \t\n.,;:!?)]}”’\"'") else m.group("lead")
    return COMMENT_RX.sub(repl, text or "")


def footnotes(text):
    """-> (body without the definition lines, {label: note}, [labels in order of first reference])."""
    defs = {}

    def take(m):
        defs[m.group(1)] = re.sub(r"\s*\n\s*", " ", m.group(2)).strip()
        return ""
    body = FOOTDEF_RX.sub(take, text or "")
    order = []
    for m in FOOTREF_RX.finditer(body):
        if m.group(1) in defs and m.group(1) not in order:
            order.append(m.group(1))
    return body, defs, order


def for_export(text, notes_title="Notes"):
    """Chapter text as it goes into an export: no comments; footnote marks become superscript numbers (in order of
    use, from 1 in each chapter) and their notes are listed at the end under a small heading."""
    body, defs, order = footnotes(strip_comments(text))
    if not order:
        return body
    num = {k: i + 1 for i, k in enumerate(order)}
    body = FOOTREF_RX.sub(lambda m: sup(num[m.group(1)]) if m.group(1) in num else m.group(0), body)
    notes = "\n\n".join("%d. %s" % (num[k], defs[k]) for k in order)
    return body.rstrip() + "\n\n## " + notes_title + "\n\n" + notes + "\n"


def next_footnote(text):
    """The next free numeric label in a chapter."""
    used = [int(x) for x in re.findall(r"\[\^(\d{1,6})\]", text or "")]
    return (max(used) + 1) if used else 1


def comment_words(text):
    return sum(len(m.group("note").split()) for m in COMMENT_RX.finditer(text or ""))


# ======================================================================================================
# 2. Compare a chapter with a backup
# ======================================================================================================
TOKEN_RX = re.compile(r"\s+|[\w'’-]+|[^\w\s]", re.U)


def compare(old, new, max_cells=6_000_000):
    """Word-level differences between two texts, found paragraph by paragraph first so long chapters stay quick.
    -> {'ops': [[op, text]], 'added': words, 'removed': words, 'changed': paragraphs touched}.
    op is '=' (same), '-' (only in the old text) or '+' (only in the new). Joining the '=' and '-' parts gives
    the old text exactly; '=' and '+' give the new."""
    old, new = old or "", new or ""
    a, b = old.splitlines(True), new.splitlines(True)
    ops = []

    def emit(op, s):
        if not s:
            return
        if ops and ops[-1][0] == op:
            ops[-1][1] += s
        else:
            ops.append([op, s])
    changed = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        A, B = "".join(a[i1:i2]), "".join(b[j1:j2])
        if tag == "equal":
            emit("=", A)
            continue
        changed += max(i2 - i1, j2 - j1)
        if tag == "delete":
            emit("-", A)
        elif tag == "insert":
            emit("+", B)
        else:
            ta, tb = TOKEN_RX.findall(A), TOKEN_RX.findall(B)
            if len(ta) * len(tb) > max_cells:
                emit("-", A)
                emit("+", B)
                continue
            for t2, x1, x2, y1, y2 in difflib.SequenceMatcher(None, ta, tb, autojunk=False).get_opcodes():
                if t2 == "equal":
                    emit("=", "".join(ta[x1:x2]))
                else:
                    emit("-", "".join(ta[x1:x2]))
                    emit("+", "".join(tb[y1:y2]))

    def words(op):
        return sum(1 for t, s in ops if t == op for w in s.split() if any(c.isalnum() for c in w))
    return {"ops": ops, "added": words("+"), "removed": words("-"), "changed": changed}


# ======================================================================================================
# 3. Project templates
# ======================================================================================================
KINDS = {"novel": "Novel", "short": "Short story", "screenplay": "Screenplay", "essay": "Essay or paper",
         "thesis": "Thesis", "nonfiction": "Nonfiction"}


def _c(note):
    return "{>>" + note + "<<}\n\n"


PROJECTS = [
    {"id": "novel", "name": "Novel", "kind": "novel", "target": 80000,
     "note": "Chapters, a word target of 80,000 and room for a plot outline.",
     "chapters": [("Chapter One", "")]},
    {"id": "novel_outline", "name": "Novel with a three-act outline", "kind": "novel", "target": 80000,
     "outline": "three_act", "note": "Six chapters already placed on a three-act outline.",
     "chapters": [("Chapter One", _c("Setup: who is your lead, and what is missing from their life?")),
                  ("Chapter Two", _c("First plot point: what forces a choice?")),
                  ("Chapter Three", _c("Rising action: what does the lead try, and what does it cost?")),
                  ("Chapter Four", _c("Midpoint: what turn changes what the lead wants?")),
                  ("Chapter Five", _c("Second plot point: the lowest moment.")),
                  ("Chapter Six", _c("Climax and resolution."))]},
    {"id": "short", "name": "Short story", "kind": "short", "target": 5000,
     "note": "One file, about 5,000 words.", "chapters": [("Story", "")]},
    {"id": "screenplay", "name": "Screenplay", "kind": "screenplay", "target": 0,
     "note": "Fountain screenplay in three acts: scene headings, characters and dialogue, set on script pages.",
     "chapters": [("Act One", None), ("Act Two", ""), ("Act Three", "")]},
    {"id": "essay", "name": "Essay or research paper", "kind": "essay", "target": 2500, "cite": "mla",
     "note": "Introduction, body and conclusion, with sources and a Works Cited list (MLA, APA or Chicago).",
     "chapters": [("Introduction", _c("Hook, background, and your thesis in one sentence.")),
                  ("Body", _c("One point per paragraph: claim, evidence with a citation, what it shows.")),
                  ("Conclusion", _c("Answer the thesis. What follows from it? No new evidence here."))]},
    {"id": "thesis", "name": "Thesis or dissertation", "kind": "thesis", "target": 40000, "cite": "apa",
     "note": "The usual chapters of a research thesis, with sources and references in APA.",
     "chapters": [("Abstract", _c("150 to 300 words: question, method, main finding, why it matters.")),
                  ("Introduction", ""), ("Literature Review", ""), ("Method", ""), ("Results", ""),
                  ("Discussion", ""), ("Conclusion", "")]},
    {"id": "nonfiction", "name": "Nonfiction book", "kind": "nonfiction", "target": 60000, "cite": "chicago",
     "note": "Chapters with Chicago-style notes and a bibliography.",
     "chapters": [("Introduction", ""), ("Chapter One", "")]},
    {"id": "sprint30", "name": "30-day novel (50,000 words)", "kind": "novel", "target": 50000, "days": 30,
     "note": "A 50,000-word target with a finish date 30 days from today: about 1,667 words a day.",
     "chapters": [("Chapter One", "")]},
]


def project(pid):
    p = next((p for p in PROJECTS if p["id"] == pid), None)
    if p is None:
        raise ValueError("Unknown kind of project.")
    return p


def project_list():
    return [{"id": p["id"], "name": p["name"], "note": p["note"], "kind": p["kind"],
             "chapters": len(p["chapters"])} for p in PROJECTS]


def project_meta(p, today):
    """book.json settings a project starts with."""
    meta = {"kind": p["kind"]}
    if p.get("target"):
        meta["target_words"] = p["target"]
    if p.get("days") and today:
        meta["deadline"] = (today + timedelta(days=p["days"] - 1)).isoformat()
    if p.get("cite"):
        meta["cite_style"] = p["cite"]
        meta["bibliography"] = True
    return meta


# ======================================================================================================
# 4. Writing sprints
# ======================================================================================================
DAY_RX = re.compile(r"^\d{4}-\d\d-\d\d$")


def clean_sprint(d):
    d = d if isinstance(d, dict) else {}
    try:
        minutes = float(d.get("minutes", 0))
        words = int(d.get("words", 0))
        goal = int(d.get("goal", 0) or 0)
    except (TypeError, ValueError):
        raise ValueError("A sprint needs minutes and words as numbers.")
    if not 0.25 <= minutes <= 240:
        raise ValueError("A sprint lasts from 15 seconds to 4 hours.")
    day = str(d.get("day") or "")
    if not DAY_RX.match(day):
        raise ValueError("A sprint needs the day it was written.")
    return {"id": uuid.uuid4().hex[:10], "day": day, "minutes": round(minutes, 2),
            "words": max(-1_000_000, min(1_000_000, words)), "goal": max(0, min(1_000_000, goal)),
            "book": re.sub(r"[\x00-\x1f]", "", str(d.get("book") or ""))[:200],
            "finished": bool(d.get("finished", True))}


def sprint_summary(items, today_s):
    done = [s for s in items if s.get("minutes", 0) > 0]
    pos = [s for s in done if s["words"] > 0]
    wpm = [s["words"] / s["minutes"] for s in pos]
    return {"count": len(done), "words": sum(max(0, s["words"]) for s in done),
            "best": max([s["words"] for s in done] + [0]), "wpm": round(sum(wpm) / len(wpm), 1) if wpm else 0,
            "best_wpm": round(max(wpm), 1) if wpm else 0, "today": sum(1 for s in done if s["day"] == today_s),
            "goals_met": sum(1 for s in done if s["goal"] and s["words"] >= s["goal"]),
            "recent": list(reversed(done[-8:]))}


# ======================================================================================================
# 5. Scene details and plotlines
# ======================================================================================================
PLOT_COLORS = ("#b5482c", "#2f6f8f", "#5f8a35", "#8a5a9e", "#b07d16", "#2f7d70", "#a43f6b", "#5b6770",
               "#c0622f", "#4459a8", "#7a6a3a", "#3f8f4f")
MAX_PLOTLINES = 12


def _t(v, n):
    return re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", str(v if v is not None else ""))).strip()[:n]


def clean_plotlines(lst, old=()):
    if not isinstance(lst, list):
        raise ValueError("Plotlines must be a list.")
    if len(lst) > MAX_PLOTLINES:
        raise ValueError("Up to %d plotlines." % MAX_PLOTLINES)
    known = {p["id"] for p in old if isinstance(p, dict) and "id" in p}
    out, names = [], set()
    for k, p in enumerate(lst):
        p = p if isinstance(p, dict) else {"name": p}
        name = _t(p.get("name"), 60)
        if not name:
            continue
        if name.lower() in names:
            raise ValueError("Two plotlines are called %s." % name)
        names.add(name.lower())
        pid = p.get("id") if p.get("id") in known else uuid.uuid4().hex[:8]
        color = p.get("color") if p.get("color") in PLOT_COLORS else PLOT_COLORS[k % len(PLOT_COLORS)]
        out.append({"id": pid, "name": name, "color": color})
    return out


def clean_details(d, plot_ids):
    d = d if isinstance(d, dict) else {}
    out = {}
    for k, n in (("pov", 80), ("setting", 120), ("when", 60)):
        if k in d:
            out[k] = _t(d[k], n)
    if "plotlines" in d:
        pl = d["plotlines"] if isinstance(d["plotlines"], list) else []
        out["plotlines"] = [p for p in dict.fromkeys(pl) if p in plot_ids]
    return out


def plot_grid(chapters, plotlines):
    """Rows of the grid: one per plotline, with the chapters that carry it, plus point-of-view word counts."""
    rows = [{"id": p["id"], "name": p["name"], "color": p["color"],
             "chapters": [c["name"] for c in chapters if p["id"] in (c.get("plotlines") or [])]} for p in plotlines]
    pov = {}
    for c in chapters:
        if c.get("pov"):
            e = pov.setdefault(c["pov"], {"pov": c["pov"], "chapters": 0, "words": 0})
            e["chapters"] += 1
            e["words"] += c.get("words", 0)
    total = sum(c.get("words", 0) for c in chapters) or 1
    povs = sorted(pov.values(), key=lambda e: -e["words"])
    for e in povs:
        e["share"] = round(100.0 * e["words"] / total)
    gaps = [c["name"] for c in chapters if not (c.get("plotlines") or [])] if plotlines else []
    return {"rows": rows, "pov": povs, "untagged": gaps}


def today_from(s):
    try:
        return date.fromisoformat(s) if isinstance(s, str) and DAY_RX.match(s) else None
    except ValueError:
        return None
