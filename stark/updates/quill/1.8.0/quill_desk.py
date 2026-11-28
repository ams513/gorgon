"""Stark Quill desk tools (1.8.0): the submission check, collections, the writing week and the rescue sheet.

Everything here is plain rules over text and numbers the caller passes in: no network, no files read or written.
writer_app.py owns the files (book.json, the stats file) and calls these functions with what it has loaded.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

# ---------------------------------------------------------------------------------------------- submission check
MARKER_RX = re.compile(r"\b(TODO|TK|TKTK|XXX|FIXME|INSERT|\?\?\?)\b")
SENTENCE_GAP_RX = re.compile(r"[.!?][\"')]?  +[A-Z\"(]")
INDENT_RX = re.compile(r"^(\t+| {2,})\S", re.M)
COMMENT_RX = re.compile(r"\{>>.*?<<\}", re.S)
LEVELS = ("stop", "fix", "note")
MIN_CHAPTER_WORDS = 100


def _words(text):
    text = COMMENT_RX.sub(" ", text or "")
    return sum(1 for w in text.split() if any(c.isalnum() for c in w))


def round_words(n):
    """The figure a manuscript's first page gives: nearest 100 under 10,000, nearest 1,000 above."""
    n = int(n)
    if n < 10000:
        return max(100, int(round(n / 100.0)) * 100) if n else 0
    return int(round(n / 1000.0)) * 1000


def running_header(author, title):
    """'Surname / TITLE / page', the way the standard manuscript layout prints it on every page after the first."""
    parts = (author or "").split()
    surname = parts[-1] if parts else "Author"
    key = " ".join((title or "").upper().split()[:4])
    return "%s / %s / 1" % (surname, key)


def _item(level, key, title, detail, where=None):
    return {"level": level, "id": key, "title": title, "detail": detail, "where": where or []}


def preflight(chapters, meta, script=False):
    """chapters: [(title, text)] in reading order; meta: title, author, contact. -> a report with the items to fix
    before a manuscript goes to an agent or an editor. It checks what the manuscript export prints and what
    editors commonly send back; it does not judge the writing."""
    meta = meta or {}
    items = []
    author = (meta.get("author") or "").strip()
    contact = [ln for ln in str(meta.get("contact") or "").splitlines() if ln.strip()]
    title = (meta.get("title") or "").strip()
    body = [(t, x) for t, x in chapters if _words(x) > 0]
    total = sum(_words(x) for _, x in chapters)
    if not body:
        items.append(_item("stop", "empty", "There is nothing to send yet", "Every chapter is empty."))
    if not author:
        items.append(_item("stop", "author", "No author name",
                           "The title page and the running header would say \"Author Name\". Add your name in Book details."))
    if not contact:
        items.append(_item("fix", "contact", "No contact details",
                           "The top left of the first page would show \"[Your address, email and phone]\". Add them in Book details."))
    elif not any("@" in ln or re.search(r"\d{5,}", ln.replace(" ", "")) for ln in contact):
        items.append(_item("fix", "contact2", "Contact details have no email or phone",
                           "An agent or editor needs a way to answer you."))
    if not title:
        items.append(_item("stop", "title", "No title", "Give the book a title in Book details."))
    marks = [(t, sorted(set(MARKER_RX.findall(x)))) for t, x in body if MARKER_RX.search(COMMENT_RX.sub(" ", x))]
    if marks:
        items.append(_item("fix", "markers", "Placeholders left in the text",
                           "Words such as TODO, TK or XXX are still in the chapters. Search for them before you send.",
                           ["%s (%s)" % (t, ", ".join(m)) for t, m in marks]))
    notes = [t for t, x in body if "{>>" in x]
    if notes:
        items.append(_item("note", "comments", "Comments to yourself are in the text",
                           "The manuscript export leaves them out, so nothing leaks. Your own files keep them.", notes))
    indents = [] if script else [t for t, x in body if INDENT_RX.search(x)]
    if indents:
        items.append(_item("fix", "indent", "Paragraphs indented with spaces or tabs",
                           "The manuscript layout indents every paragraph itself. Spaces typed by hand can double the indent.", indents))
    gaps = [] if script else [t for t, x in body if SENTENCE_GAP_RX.search(x)]
    if gaps:
        items.append(_item("note", "spaces", "Two spaces after a full stop",
                           "The standard layout uses one space. Search for two spaces and replace them with one.", gaps))
    short = [t for t, x in body if _words(x) < MIN_CHAPTER_WORDS]
    if short:
        items.append(_item("note", "short", "Very short chapters",
                           "Under %d words. Fine if intended; an editor may ask whether it is a chapter." % MIN_CHAPTER_WORDS, short))
    empty = [t for t, x in chapters if _words(x) == 0]
    if empty and body:
        items.append(_item("note", "emptych", "Empty chapters are left out", "They are skipped by every export.", empty))
    counts = {k: sum(1 for i in items if i["level"] == k) for k in LEVELS}
    return {"ready": counts["stop"] == 0 and counts["fix"] == 0, "can_export": counts["stop"] == 0, "counts": counts,
            "items": items, "words": total, "words_rounded": round_words(total), "header": running_header(author, title),
            "chapters": len(body)}


# ---------------------------------------------------------------------------------------------- collections
MAX_COLLECTIONS = 30
MAX_MEMBERS = 500
STATUSES = ("idea", "draft", "revised", "final")


def clean_collection(raw, new_id, statuses=STATUSES):
    """One collection from user input. manual: a list of chapter file names. smart: a rule that picks chapters
    by status, keyword, word target or a word in the title or note. Raises ValueError with a plain sentence."""
    if not isinstance(raw, dict):
        raise ValueError("A collection is a name and a list or a rule.")
    name = re.sub(r"\s+", " ", str(raw.get("name") or "")).strip()[:40]
    if not name:
        raise ValueError("Give the collection a name.")
    kind = raw.get("kind", "manual")
    if kind not in ("manual", "smart"):
        raise ValueError("A collection is either a hand-picked list or a rule.")
    out = {"id": str(raw.get("id") or new_id)[:12], "name": name, "kind": kind}
    if kind == "manual":
        mem = raw.get("chapters") or []
        if not isinstance(mem, list):
            raise ValueError("The chapters of a collection are a list.")
        out["chapters"] = list(dict.fromkeys(str(x)[:200] for x in mem if str(x).strip()))[:MAX_MEMBERS]
    else:
        rule = raw.get("rule") or {}
        if not isinstance(rule, dict):
            raise ValueError("The rule should be a few choices.")
        status = str(rule.get("status") or "")
        if status and status not in statuses:
            raise ValueError("Status must be one of: %s." % ", ".join(statuses))
        r = {"status": status, "keyword": re.sub(r"\s+", " ", str(rule.get("keyword") or "")).strip().strip("#")[:24],
             "text": re.sub(r"\s+", " ", str(rule.get("text") or "")).strip()[:60],
             "target": str(rule.get("target") or "") if str(rule.get("target") or "") in ("under", "met", "none") else ""}
        if not any(r.values()):
            raise ValueError("A rule needs at least one choice: a status, a keyword, a word or a target.")
        out["rule"] = r
    return out


def add_collection(existing, raw, new_id, statuses=STATUSES):
    """Add or (same id) replace. -> the new list."""
    c = clean_collection(raw, new_id, statuses)
    lst = [x for x in (existing or []) if isinstance(x, dict)]
    for i, x in enumerate(lst):
        if x.get("id") == c["id"]:
            lst[i] = c
            return lst
    if len(lst) >= MAX_COLLECTIONS:
        raise ValueError("A book holds up to %d collections. Delete one first." % MAX_COLLECTIONS)
    if any(x.get("name", "").lower() == c["name"].lower() for x in lst):
        raise ValueError("There is already a collection called %s." % c["name"])
    return lst + [c]


def remove_collection(existing, cid):
    lst = [x for x in (existing or []) if isinstance(x, dict)]
    out = [x for x in lst if x.get("id") != cid]
    if len(out) == len(lst):
        raise KeyError(cid)
    return out


def members(coll, rows):
    """The chapter rows (dicts with name, title, status, words, target, keywords, synopsis) a collection holds, in
    book order. A hand-picked chapter that no longer exists is simply not there."""
    if coll.get("kind") == "manual":
        pick = set(coll.get("chapters") or [])
        return [r for r in rows if r["name"] in pick]
    rule = coll.get("rule") or {}
    out = []
    for r in rows:
        if rule.get("status") and r.get("status") != rule["status"]:
            continue
        kw = rule.get("keyword", "").lower()
        if kw and kw not in [k.lower() for k in r.get("keywords") or []]:
            continue
        txt = rule.get("text", "").lower()
        if txt and txt not in ("%s %s" % (r.get("title", ""), r.get("synopsis", ""))).lower():
            continue
        tgt, words = int(r.get("target") or 0), int(r.get("words") or 0)
        t = rule.get("target")
        if t == "under" and not (tgt and words < tgt):
            continue
        if t == "met" and not (tgt and words >= tgt):
            continue
        if t == "none" and tgt:
            continue
        out.append(r)
    return out


def describe_rule(rule):
    bits = []
    if rule.get("status"):
        bits.append("status is %s" % rule["status"])
    if rule.get("keyword"):
        bits.append("tagged %s" % rule["keyword"])
    if rule.get("text"):
        bits.append("title or note has \"%s\"" % rule["text"])
    if rule.get("target"):
        bits.append({"under": "under its word target", "met": "at or over its word target",
                     "none": "has no word target"}[rule["target"]])
    return ", ".join(b for b in bits if b)


def collections_view(colls, rows):
    out = []
    for c in colls or []:
        m = members(c, rows)
        out.append({"id": c["id"], "name": c["name"], "kind": c["kind"],
                    "rule": c.get("rule"), "rule_text": describe_rule(c["rule"]) if c.get("rule") else "",
                    "chapters": [{"name": r["name"], "title": r["title"], "words": r["words"], "status": r["status"]} for r in m],
                    "words": sum(r["words"] for r in m), "picked": c.get("chapters")})
    return out


# ---------------------------------------------------------------------------------------------- the writing week
def week_card(days, goal, rest, today, streak, best_streak):
    """days: {iso date: words}. rest: weekdays (Monday is 0) that never break a run. -> the figures for this week
    (Monday to today), what keeps today's run alive, and a plain sentence for the top of the screen."""
    rest = sorted({int(x) for x in rest or [] if str(x).isdigit() and 0 <= int(x) <= 6})
    start = today - timedelta(days=today.weekday())
    week, met_days, total = [], 0, 0
    for i in range(7):
        d = start + timedelta(days=i)
        w = max(0, int((days.get(d.isoformat()) or 0)))
        met = (w >= goal) if goal else (w > 0)
        future = d > today
        resting = d.weekday() in rest
        if not future:
            total += w
            met_days += 1 if met else 0
        week.append({"date": d.isoformat(), "weekday": d.weekday(), "words": w, "met": met and not future,
                     "rest": resting, "future": future, "today": d == today})
    done = max(0, int(days.get(today.isoformat()) or 0))
    left = max(0, goal - done) if goal else (0 if done else 1)
    today_rest = today.weekday() in rest
    at_risk = bool(streak) and left > 0 and not today_rest
    if goal and left == 0:
        line = "Today's goal is done. %s in a row." % _days(streak) if streak else "Today's goal is done."
    elif at_risk:
        line = ("%s words keep your %d-day run going today." % (format(left, ","), streak)) if goal else \
            "Write a few words today to keep your %d-day run going." % streak
    elif today_rest:
        line = "A rest day: your run is safe whatever you write."
    elif goal:
        line = "%s words to today's goal." % format(left, ",")
    else:
        line = "Set a daily goal and Quill will count your run against it."
    return {"week": week, "words": total, "days_met": met_days, "goal": goal, "left_today": left, "at_risk": at_risk,
            "today_rest": today_rest, "rest": rest, "streak": streak, "best_streak": best_streak, "line": line,
            "goal_week": goal * (7 - len(rest)) if goal else 0}


def _days(n):
    return "%d day" % n if n == 1 else "%d days" % n


# ---------------------------------------------------------------------------------------------- the rescue sheet
def rescue_sheet(title, author, folder, chapters, words, snapshots, last_backup, milestones, plan, streak, today):
    """The one page for the fridge or the USB stick: what the book is, where it lives, what a loss would cost, and
    how to get it back. Plain text figures only; the page lays it out and prints it."""
    chs = [{"title": c["title"], "words": c["words"], "status": c["status"]} for c in chapters]
    steps = [
        "Open Stark Quill and choose the book \"%s\" from the list of books." % title,
        "If a chapter is missing or wrong, open Backups: every chapter you deleted is in the graveyard, and every automatic copy can be opened.",
        "If this PC is lost, take the backup zip (Back up the whole book) to a new PC, unzip it, and copy the book's folder into Quill's books folder, or bring the chapter files in with Import a manuscript.",
        "Your chapters are plain text files in the folder above. Any text editor opens them.",
    ]
    return {"title": title, "author": author, "folder": folder, "date": today.isoformat(), "words": words,
            "chapters": chs, "snapshots": snapshots, "last_backup": last_backup or "none yet", "milestones": milestones,
            "plan": plan, "streak": streak, "steps": steps,
            "risk": ("No automatic copy yet: make a backup zip today." if not snapshots else
                     "%s, the latest on %s. Keep one backup zip off this PC as well." % (
                         "1 automatic copy" if snapshots == 1 else "%d automatic copies" % snapshots, last_backup))}
