"""Writing bench: chapter targets, keywords, milestones (named copies of the whole book, kept until you delete
them) and a plain-text outline. Pure standard library; writer_app.py wires it to the book folder."""
import json
import os
import re
import time
import zipfile
from datetime import datetime

MILESTONE_DIR = os.path.join(".backups", "milestones")
MAX_MILESTONES = 100
MAX_KEYWORDS = 12
KEYWORD_LEN = 24
_ID = re.compile(r"^ms-\d{8}-\d{6}(-\d+)?$")


def clean_target(v):
    try:
        return max(0, min(1_000_000, int(v)))
    except (TypeError, ValueError):
        raise ValueError("A chapter target is a whole number of words.")


def clean_keywords(v):
    if isinstance(v, str):
        v = re.split(r"[,\n;]", v)
    if not isinstance(v, (list, tuple)):
        raise ValueError("Keywords are a list of short words.")
    out, seen = [], set()
    for k in v:
        k = re.sub(r"\s+", " ", str(k)).strip().strip("#")[:KEYWORD_LEN].strip()
        if k and k.lower() not in seen:
            seen.add(k.lower())
            out.append(k)
    return out[:MAX_KEYWORDS]


def progress(words, target):
    """0..100 share of a chapter target written (100 at or over target); None when no target is set."""
    if not target:
        return None
    return min(100, int(100 * words / target))


# ---------------------------------------------------------------- milestones
def _dir(book_dir):
    return os.path.join(book_dir, MILESTONE_DIR)


def _meta_path(book_dir, mid):
    return os.path.join(_dir(book_dir), mid + ".json")


def _check_id(mid):
    if not isinstance(mid, str) or not _ID.match(mid):
        raise KeyError(mid)
    return mid


def list_milestones(book_dir):
    out = []
    try:
        names = os.listdir(_dir(book_dir))
    except FileNotFoundError:
        return out
    for n in names:
        if not n.endswith(".json") or not _ID.match(n[:-5]):
            continue
        try:
            with open(os.path.join(_dir(book_dir), n), "rb") as f:
                m = json.loads(f.read().decode("utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(m, dict) and os.path.isfile(os.path.join(_dir(book_dir), n[:-5] + ".zip")):
            out.append(m)
    out.sort(key=lambda m: m.get("id", ""), reverse=True)
    return out


def make_milestone(book_dir, label, note, chapters, now=None, count_words=None):
    """chapters: {file name: text}. Returns the milestone record. Atomic: zip and record are renamed into place."""
    label = re.sub(r"\s+", " ", str(label or "")).strip()[:80]
    note = str(note or "").strip()[:2000]
    if not label:
        raise ValueError("Give the milestone a name, such as First draft done.")
    if len(list_milestones(book_dir)) >= MAX_MILESTONES:
        raise ValueError("There are %d milestones; delete one you no longer need first." % MAX_MILESTONES)
    now = now or time.time()
    os.makedirs(_dir(book_dir), exist_ok=True)
    stamp = datetime.fromtimestamp(now).strftime("%Y%m%d-%H%M%S")
    mid, k = "ms-" + stamp, 1
    while os.path.exists(os.path.join(_dir(book_dir), mid + ".zip")):
        k += 1
        mid = "ms-%s-%d" % (stamp, k)
    words = sum((count_words or (lambda t: len(t.split())))(t) for t in chapters.values())
    zpath = os.path.join(_dir(book_dir), mid + ".zip")
    with zipfile.ZipFile(zpath + ".tmp", "w", zipfile.ZIP_DEFLATED) as z:
        for n, t in chapters.items():
            z.writestr(n, t.encode("utf-8"))
    os.replace(zpath + ".tmp", zpath)
    rec = {"id": mid, "label": label, "note": note, "created": datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M"),
           "chapters": len(chapters), "words": words}
    with open(_meta_path(book_dir, mid) + ".tmp", "wb") as f:
        f.write(json.dumps(rec, ensure_ascii=False).encode("utf-8"))
    os.replace(_meta_path(book_dir, mid) + ".tmp", _meta_path(book_dir, mid))
    return rec


def delete_milestone(book_dir, mid):
    _check_id(mid)
    found = False
    for ext in (".json", ".zip"):
        p = os.path.join(_dir(book_dir), mid + ext)
        if os.path.isfile(p):
            os.remove(p)
            found = True
    if not found:
        raise KeyError(mid)


def milestone_texts(book_dir, mid):
    _check_id(mid)
    p = os.path.join(_dir(book_dir), mid + ".zip")
    if not os.path.isfile(p):
        raise KeyError(mid)
    with zipfile.ZipFile(p) as z:
        return {n: z.read(n).decode("utf-8", "replace") for n in z.namelist() if n.lower().endswith((".md", ".txt", ".fountain"))}


# ---------------------------------------------------------------- outline
def outline_markdown(title, rows):
    """rows: dicts with title, status, words, target, keywords, pov, synopsis. A plain Markdown outline."""
    lines = ["# " + (title or "Outline"), ""]
    for i, r in enumerate(rows, 1):
        t = "%d. %s" % (i, r["title"])
        bits = [r.get("status") or "draft", "%d words" % r.get("words", 0)]
        if r.get("target"):
            bits[-1] += " of %d" % r["target"]
        if r.get("pov"):
            bits.append("POV " + r["pov"])
        if r.get("keywords"):
            bits.append(", ".join("#" + k.replace(" ", "-") for k in r["keywords"]))
        lines.append("## " + t)
        lines.append("*" + " · ".join(bits) + "*")
        if r.get("synopsis"):
            lines += ["", r["synopsis"].strip()]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
