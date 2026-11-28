"""Stark Quill Schools mode (part of the Stark Education Suite): assignment files, the rubric self-check,
the checked submission package and the classroom profile. Pure standard library, no network, no account.

Nothing here grades anything. A rubric is a list of things the student ticks for themselves; the teacher reads
the work and decides. The glue that finds the book on disk lives in writer_app.py.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import zipfile

from quill_safezip import ZipTooBig, check_zip, read_member
from datetime import date, datetime

ASSIGN_EXT = ".quillassign"
ASSIGN_FORMAT = "stark-quill-assignment"
SUBMIT_FORMAT = "stark-quill-submission"
MAX_ASSIGN_BYTES = 256 * 1024
MAX_RUBRIC = 20
PROFILES = ("standard", "classroom")
# What the classroom profile sets: a larger page, spell check on, update checks off (and not switchable on).
CLASSROOM = {"ui_scale": 1.25, "spellcheck": True, "updates": False}
STANDARD = {"ui_scale": 1.0, "spellcheck": True}
MANIFEST = "SUBMISSION.json"
REFLECTION = "REFLECTION.txt"
CHECKSUMS = "CHECKSUMS.sha256"
_BAD_NAME = r'[\\/:*?"<>|\x00-\x1f]'


def _clean(s, n):
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(s if s is not None else "")).strip()
    return s[:n]


def _sha(b):
    return hashlib.sha256(b).hexdigest()


def _day(s):
    try:
        return date.fromisoformat(str(s)).isoformat()
    except ValueError:
        return None


# ---------------------------------------------------------------------------------------------- assignments
def assignment_id(a):
    core = {k: a[k] for k in ("title", "prompt", "word_target", "due", "rubric")}
    return _sha(json.dumps(core, sort_keys=True, ensure_ascii=False).encode("utf-8"))[:12]


def make_assignment(title, prompt, word_target=0, due="", rubric=(), teacher=""):
    """A validated assignment dict. Raises ValueError with a sentence a teacher can act on."""
    title, prompt = _clean(title, 200), _clean(prompt, 5000)
    if not title:
        raise ValueError("Give the assignment a title.")
    if not prompt:
        raise ValueError("Write the prompt the students will see.")
    try:
        target = int(word_target or 0)
    except (TypeError, ValueError):
        raise ValueError("The word target should be a whole number.")
    if not 0 <= target <= 1_000_000:
        raise ValueError("The word target should be between 0 and 1,000,000.")
    due = _clean(due, 10)
    if due and _day(due) is None:
        raise ValueError("The due date should look like 2027-03-01.")
    items = []
    for r in rubric or ():
        text = _clean(r.get("text") if isinstance(r, dict) else r, 300)
        if text:
            items.append({"id": "c%d" % (len(items) + 1), "text": text})
    if len(items) > MAX_RUBRIC:
        raise ValueError("A rubric can have up to %d criteria." % MAX_RUBRIC)
    a = {"format": ASSIGN_FORMAT, "version": 1, "title": title, "prompt": prompt, "word_target": target,
         "due": due, "rubric": items, "teacher": _clean(teacher, 100)}
    a["id"] = assignment_id(a)
    return a


def read_assignment(data):
    """Bytes (the contents) or a path of an assignment file -> validated dict. A bad file only ever raises ValueError."""
    if isinstance(data, (str, os.PathLike)):   # a path; file contents are passed as bytes
        try:
            if os.path.getsize(data) > MAX_ASSIGN_BYTES:
                raise ValueError("That file is too big to be an assignment.")
            with open(data, "rb") as f:
                data = f.read()
        except OSError:
            raise ValueError("Quill could not read that file.")
    if len(data) > MAX_ASSIGN_BYTES:
        raise ValueError("That file is too big to be an assignment.")
    try:
        d = json.loads(data.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError):
        raise ValueError("That is not a Quill assignment file.")
    if not isinstance(d, dict) or d.get("format") != ASSIGN_FORMAT:
        raise ValueError("That is not a Quill assignment file.")
    rub = d.get("rubric")
    if not isinstance(rub, list):
        raise ValueError("The assignment file has no rubric list.")
    return make_assignment(d.get("title"), d.get("prompt"), d.get("word_target"), d.get("due"),
                           [r.get("text") if isinstance(r, dict) else r for r in rub], d.get("teacher"))


def write_assignment(dest_dir, a):
    """Save '<title>.quillassign' in dest_dir (never over an existing one); returns the path."""
    os.makedirs(dest_dir, exist_ok=True)
    stem = re.sub(_BAD_NAME, "", a["title"]).strip(" .") or "Assignment"
    out, n = os.path.join(dest_dir, stem + ASSIGN_EXT), 1
    while os.path.exists(out):
        n += 1
        out = os.path.join(dest_dir, "%s (%d)%s" % (stem, n, ASSIGN_EXT))
    tmp = out + ".part"
    with open(tmp, "wb") as f:
        f.write(json.dumps(a, indent=1, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, out)
    read_assignment(out)  # read it back: a file the students cannot open is never left behind
    return out


# ---------------------------------------------------------------------------------------------- rubric
def rubric_status(assignment, ticked):
    """The student's own ticks. Ticks for criteria the assignment does not have are dropped."""
    rub = (assignment or {}).get("rubric", [])
    have = {i for i in (ticked or []) if i in {r["id"] for r in rub}}
    crit = [{"id": r["id"], "text": r["text"], "done": r["id"] in have} for r in rub]
    return {"criteria": crit, "done": len(have), "total": len(crit)}


# ---------------------------------------------------------------------------------------------- profile
def profile_name(settings, env=None, here=None):
    """'classroom' when the school set it: the QUILL_PROFILE variable, a PROFILE file beside the app, or the setting."""
    env = os.environ if env is None else env
    v = (env.get("QUILL_PROFILE") or "").strip().lower()
    if v in PROFILES:
        return v
    if here:
        try:
            with open(os.path.join(here, "PROFILE"), encoding="ascii") as f:
                v = f.read().strip().lower()
            if v in PROFILES:
                return v
        except OSError:
            pass
    v = str((settings or {}).get("profile") or "").lower()
    return v if v in PROFILES else "standard"


def profile_defaults(name):
    return dict(CLASSROOM if name == "classroom" else STANDARD)


# ---------------------------------------------------------------------------------------------- submission
def submission_name(title, student, now=None):
    stamp = datetime.fromtimestamp(time.time() if now is None else now).strftime("%Y-%m-%d")
    t = re.sub(_BAD_NAME, "", title).strip(" .")[:60] or "Book"
    s = re.sub(_BAD_NAME, "", student).strip(" .")[:40]
    return "%s - %s submission %s" % (t, s, stamp) if s else "%s submission %s" % (t, stamp)


def build_submission(dest_dir, name, members, info, reflection, now=None):
    """One zip: the book's files, SUBMISSION.json (word count, rubric ticks, revision summary), the reflection and
    CHECKSUMS.sha256. It is read back and every file is checked before it is kept.
    members = [(absolute path, relative path)]; info = the manifest fields; returns {path, file, files, bytes}."""
    reflection = str(reflection or "").strip()
    if not reflection:
        raise ValueError("Write a short reflection before handing in: what went well, and what you would change.")
    os.makedirs(dest_dir, exist_ok=True)
    out, n = os.path.join(dest_dir, name + ".zip"), 1
    while os.path.exists(out):
        n += 1
        out = os.path.join(dest_dir, "%s (%d).zip" % (name, n))
    files = {}
    for p, rel in members:
        with open(p, "rb") as f:
            files["book/" + rel] = f.read()
    manifest = dict(info)
    manifest.update({"format": SUBMIT_FORMAT, "version": 1, "created": datetime.fromtimestamp(
        time.time() if now is None else now).strftime("%Y-%m-%d %H:%M"), "reflection_words": len(reflection.split()),
        "book_files": sorted(files)})
    files[REFLECTION] = reflection.encode("utf-8") + b"\n"
    files[MANIFEST] = json.dumps(manifest, indent=1, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n"
    files[CHECKSUMS] = "".join("%s  %s\n" % (_sha(files[k]), k) for k in sorted(files)).encode("utf-8")
    when = max(time.localtime(time.time() if now is None else now)[:6], (1980, 1, 1, 0, 0, 0))
    tmp = out + ".part"
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for k in sorted(files):
                zi = zipfile.ZipInfo("%s/%s" % (name, k), date_time=when)
                zi.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(zi, files[k])
        res = verify_submission(tmp)
        if not res["ok"]:
            raise IOError("The package did not read back the same; nothing was kept. Try again. "
                          + "; ".join(res["problems"]))
        with open(tmp, "rb+") as f:
            os.fsync(f.fileno())
        os.replace(tmp, out)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return {"path": out, "file": os.path.basename(out), "files": len(files), "bytes": os.path.getsize(out)}


def _is_chapter(rel):
    """book/<chapter>.md|.txt at the top of the book folder; the dictionary, Research and the graveyard are not chapters."""
    parts = rel.split("/")
    return (len(parts) == 2 and parts[0] == "book" and not parts[1].startswith(".") and parts[1].lower().endswith((".md", ".txt"))
            and parts[1] not in ("dictionary.txt", "Research.md", "Deleted_Graveyard.md"))


def verify_submission(path, counter=None):
    """Teacher-side check: {ok, problems, info}. Every file must match CHECKSUMS.sha256, nothing may be extra or
    missing, and (with a word counter) the stated word count must match the chapters."""
    problems, info = [], {}
    try:
        with zipfile.ZipFile(path) as z:
            check_zip(z)
            if z.testzip() is not None:
                problems.append("the zip is damaged")
            names = [n for n in z.namelist() if not n.endswith("/")]
            tops = {n.split("/", 1)[0] for n in names}
            if len(tops) != 1:
                return {"ok": False, "problems": ["not a Quill submission (expected one folder)"], "info": {}}
            top = tops.pop() + "/"
            rel = {n[len(top):]: n for n in names}
            if CHECKSUMS not in rel or MANIFEST not in rel or REFLECTION not in rel:
                return {"ok": False, "problems": ["not a Quill submission (missing manifest, reflection or checksums)"],
                        "info": {}}
            listed = {}
            for line in read_member(z, rel[CHECKSUMS]).decode("utf-8").splitlines():
                m = re.match(r"^([0-9a-f]{64})  (.+)$", line)
                if m:
                    listed[m.group(2)] = m.group(1)
            for k in sorted(set(listed) | (set(rel) - {CHECKSUMS})):
                if k not in rel:
                    problems.append("missing: " + k)
                elif k not in listed:
                    problems.append("not in the checksum list: " + k)
                elif _sha(read_member(z, rel[k])) != listed[k]:
                    problems.append("changed after it was packed: " + k)
            try:
                info = json.loads(read_member(z, rel[MANIFEST]).decode("utf-8"))
            except ValueError:
                problems.append("the manifest cannot be read")
            if counter and info:
                got = sum(counter(read_member(z, n).decode("utf-8", "replace")) for k, n in rel.items()
                          if _is_chapter(k))
                if got != info.get("words"):
                    problems.append("word count %s does not match the chapters (%d)" % (info.get("words"), got))
    except ZipTooBig as e:
        return {"ok": False, "problems": ["too large to open safely: %s" % e], "info": {}}
    except (OSError, zipfile.BadZipFile, UnicodeDecodeError):
        return {"ok": False, "problems": ["not a readable zip"], "info": {}}
    return {"ok": not problems, "problems": problems, "info": info}


def main(argv=None):
    """python quill_schools.py verify <submission.zip>: for the teacher; prints the summary, exit 1 on a problem."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2 or argv[0] != "verify":
        print("usage: python quill_schools.py verify <submission.zip>")
        return 2
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from writer_app import count_words as counter
    except Exception:
        counter = None
    res = verify_submission(argv[1], counter)
    i = res["info"]
    if i:
        print("%s, %s words (target %s), assignment: %s" % (i.get("student") or "student", i.get("words"),
              i.get("word_target") or "none", (i.get("assignment") or {}).get("title", "none")))
    print("OK: every file matches its checksum." if res["ok"] else "PROBLEMS:\n  " + "\n  ".join(res["problems"]))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
