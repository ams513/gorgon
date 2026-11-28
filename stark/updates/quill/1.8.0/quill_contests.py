"""Stark Quill writing contests (Stark Challenge): event pack, signed entries, anonymous judging, signed score
sheets, ranked results with tie-breaks, printable certificates. Pure standard library, fully offline.

Signatures are HMAC-SHA256 keyed from the event code. They make any edit after signing visible (an entry whose
text, name, division or word count was changed, a score sheet with a changed score). The code is shared with every
entrant, so this is tamper-evidence for a friendly contest, not protection against a determined forger.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import re
import sys
import zipfile
from datetime import date
from fractions import Fraction

import quill_schools as S
from quill_safezip import ZipTooBig, check_zip, read_member

CONTEST_EXT = ".quillcontest"
CONTEST_FORMAT = "stark-quill-contest"
ENTRY_FORMAT = "stark-quill-entry"
PACK_FORMAT = "stark-quill-judging-pack"
SHEET_FORMAT = "stark-quill-score-sheet"
PACK_EXT, SHEET_EXT = ".quillpack", ".quillscores"
MAX_FILE = 16 * 1024 * 1024
DIVISIONS = {"kids": "Kids", "teens": "Teens", "couples": "Couples", "families": "Families",
             "business": "Business teams", "schools": "Schools", "colleges": "Colleges and universities",
             "open": "Open"}
MEMBERS_REQUIRED = {"couples": (2, 2), "families": (2, 12), "business": (2, 50)}   # division -> (min, max) members
MAX_MEMBERS = 50
DEFAULT_RUBRIC = (("Voice and originality", 10), ("Structure and pacing", 10), ("Language and craft", 10),
                  ("Answers the prompt", 10))
TIEBREAK = ("higher mean total", "higher mean on the first rubric criterion, then the second, and so on",
            "earlier entry", "still level: the rank is shared")
_NAME = re.compile(r"^[^\W\d_]+(?:['’-][^\W\d_]+)*$", re.U)


def norm_code(code):
    c = re.sub(r"[^A-Z0-9]+", "-", str(code or "").upper()).strip("-")
    return c[:40]


def _key(code):
    return hashlib.sha256(b"stark-quill-contest-v1|" + norm_code(code).encode("utf-8")).digest()


def _sign(code, obj):
    return hmac.new(_key(code), json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8"),
                    hashlib.sha256).hexdigest()


def event_id(code):
    """The same code is always the same event, whoever types it and however they space or case it."""
    return hashlib.sha256(("stark-quill-event|" + norm_code(code)).encode()).hexdigest()[:12]


def _day(s):
    try:
        return date.fromisoformat(str(s)).isoformat()
    except ValueError:
        return None


def _load(data, what, limit=MAX_FILE):
    if isinstance(data, (str, os.PathLike)):
        try:
            if os.path.getsize(data) > limit:
                raise ValueError("That file is too big to be a %s." % what)
            with open(data, "rb") as f:
                data = f.read()
        except OSError:
            raise ValueError("Quill could not read that file.")
    if len(data) > limit:
        raise ValueError("That file is too big to be a %s." % what)
    try:
        d = json.loads(data.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError):
        raise ValueError("That is not a Quill %s file." % what)
    if not isinstance(d, dict):
        raise ValueError("That is not a Quill %s file." % what)
    return d


def _write_json(out, obj):
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    tmp = out + ".part"
    with open(tmp, "wb") as f:
        f.write(json.dumps(obj, indent=1, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, out)


def _unique(dest_dir, stem, ext):
    os.makedirs(dest_dir, exist_ok=True)
    stem = re.sub(S._BAD_NAME, "", stem).strip(" .")[:80] or "file"
    out, n = os.path.join(dest_dir, stem + ext), 1
    while os.path.exists(out):
        n += 1
        out = os.path.join(dest_dir, "%s (%d)%s" % (stem, n, ext))
    return out


# ---------------------------------------------------------------------------------------------- event pack
def make_contest(code, title, prompt, word_limit=0, deadline="", divisions=None, rubric=None, organiser=""):
    """A validated contest dict. Raises ValueError with a sentence an organiser can act on."""
    code = norm_code(code)
    if len(code) < 4:
        raise ValueError("The event code needs at least 4 letters or digits, for example SPRING-2027.")
    title, prompt = S._clean(title, 200), S._clean(prompt, 5000)
    if not title:
        raise ValueError("Give the contest a title.")
    if not prompt:
        raise ValueError("Write the prompt the entrants will see.")
    try:
        limit = int(word_limit or 0)
    except (TypeError, ValueError):
        raise ValueError("The word limit should be a whole number.")
    if not 0 <= limit <= 1_000_000:
        raise ValueError("The word limit should be between 0 and 1,000,000.")
    deadline = S._clean(deadline, 10)
    if deadline and _day(deadline) is None:
        raise ValueError("The deadline should look like 2027-03-01.")
    divs = []
    for d in (divisions if divisions else DIVISIONS):
        d = str(d).strip().lower()
        if d not in DIVISIONS:
            raise ValueError("Unknown division '%s'. Choose from: %s." % (d, ", ".join(DIVISIONS)))
        if d not in divs:
            divs.append(d)
    crit = []
    for r in (rubric if rubric else [{"text": t, "max": m} for t, m in DEFAULT_RUBRIC]):
        text = S._clean(r.get("text") if isinstance(r, dict) else r, 200)
        mx = r.get("max", 10) if isinstance(r, dict) else 10
        if not text:
            continue
        try:
            mx = int(mx)
        except (TypeError, ValueError):
            raise ValueError("A criterion's top score should be a whole number.")
        if not 1 <= mx <= 100:
            raise ValueError("A criterion's top score should be between 1 and 100.")
        crit.append({"id": "c%d" % (len(crit) + 1), "text": text, "max": mx})
    if not 1 <= len(crit) <= S.MAX_RUBRIC:
        raise ValueError("A judging rubric needs 1 to %d criteria." % S.MAX_RUBRIC)
    return {"format": CONTEST_FORMAT, "version": 1, "code": code, "event": event_id(code), "title": title,
            "prompt": prompt, "word_limit": limit, "deadline": deadline, "divisions": divs, "rubric": crit,
            "organiser": S._clean(organiser, 100)}


def read_contest(data):
    d = _load(data, "contest", S.MAX_ASSIGN_BYTES)
    if d.get("format") != CONTEST_FORMAT or not isinstance(d.get("rubric"), list):
        raise ValueError("That is not a Quill contest file.")
    return make_contest(d.get("code"), d.get("title"), d.get("prompt"), d.get("word_limit"), d.get("deadline"),
                        d.get("divisions") or None, d["rubric"], d.get("organiser"))


def write_contest(dest_dir, c):
    """Save '<code>.quillcontest' in dest_dir (never over an existing one); returns the path."""
    out = _unique(dest_dir, c["code"], CONTEST_EXT)
    _write_json(out, c)
    read_contest(out)
    return out


# ---------------------------------------------------------------------------------------------- entries
def clean_members(division, members):
    """First names only. Couples need 2, families 2 to 12, business teams 2 or more; others may be one writer."""
    out = []
    for m in members or ():
        m = S._clean(m, 40)
        if not m:
            continue
        if not _NAME.match(m):
            raise ValueError("List members by first name only: '%s' has a space, a number or a symbol." % m)
        out.append(m)
    if division in MEMBERS_REQUIRED:
        lo, hi = MEMBERS_REQUIRED[division]
        if not lo <= len(out) <= hi:
            raise ValueError("A %s entry lists %s by first name." % (DIVISIONS[division].lower(),
                             "exactly %d members" % lo if lo == hi else "%d to %d members" % (lo, hi)))
    elif len(out) == 1 or len(out) > MAX_MEMBERS:
        raise ValueError("A team lists 2 to %d members by first name (leave the list empty for a single writer)." % MAX_MEMBERS)
    if len({m.lower() for m in out}) != len(out):
        raise ValueError("Two members share a first name. Write them apart, for example SamK and SamR.")
    return out


def _content_sha(files):
    return S._sha("".join("%s %s\n" % (k, S._sha(v)) for k, v in sorted(files.items())).encode("utf-8"))


def entry_info(contest, division, entrant, members, words, title, book_files):
    """The signed manifest fields of an entry. book_files = {relative path: bytes}."""
    division = str(division or "").lower()
    if division not in contest["divisions"]:
        raise ValueError("This contest has no '%s' division. Choose from: %s." % (division, ", ".join(contest["divisions"])))
    entrant = S._clean(entrant, 60)
    if not entrant:
        raise ValueError("Enter your first name, or your team or family name.")
    info = {"entry_format": ENTRY_FORMAT, "code": contest["code"], "event": contest["event"], "division": division,
            "entrant": entrant, "members": clean_members(division, members), "words": int(words),
            "book_title": S._clean(title, 200), "content": _content_sha(book_files)}
    info["signature"] = _sign(contest["code"], info)
    info["entry_id"] = info["signature"][:10]
    return info


def build_entry(dest_dir, contest, members_files, division, entrant, members, words, title, statement="", now=None):
    """Pack an entry zip with the hand-in packaging (checksums, read-back) plus the contest signature.
    members_files = [(absolute path, relative path)]; returns build_submission's result plus the entry id."""
    data = {}
    for p, rel in members_files:
        with open(p, "rb") as f:
            data[rel] = f.read()
    info = entry_info(contest, division, entrant, members, words, title, data)
    if info["words"] <= 0:
        raise ValueError("The entry is empty. Write something first.")
    if contest["word_limit"] and info["words"] > contest["word_limit"]:
        raise ValueError("The entry has %d words and the limit is %d. Trim it, then enter." % (info["words"], contest["word_limit"]))
    stem = "%s - %s entry %s" % (contest["code"], re.sub(S._BAD_NAME, "", info["entrant"]).strip(" .")[:40] or "entrant",
                                 info["entry_id"])
    res = S.build_submission(dest_dir, stem, members_files, info,
                             statement or "Entry to %s (%s)." % (contest["title"], contest["code"]), now=now)
    res["entry_id"] = info["entry_id"]
    return res


_SIGNED = ("entry_format", "code", "event", "division", "entrant", "members", "words", "book_title", "content")


def verify_entry(path, contest=None, counter=None):
    """{ok, problems, info}: checksums (as for a hand-in), the signature, the content digest, and (with a contest)
    the event, division, word limit and deadline."""
    res = S.verify_submission(path, counter)
    problems, info = list(res["problems"]), res["info"]
    if info.get("entry_format") != ENTRY_FORMAT:
        return {"ok": False, "problems": problems + ["not a contest entry"], "info": info}
    try:
        with zipfile.ZipFile(path) as z:
            check_zip(z)
            top = z.namelist()[0].split("/", 1)[0] + "/"
            files = {n[len(top) + len("book/"):]: read_member(z, n) for n in z.namelist()
                     if n.startswith(top + "book/") and not n.endswith("/")}
    except (OSError, zipfile.BadZipFile, IndexError, ZipTooBig):
        return {"ok": False, "problems": problems + ["not a readable zip"], "info": info}
    core = {k: info.get(k) for k in _SIGNED}
    sig = info.get("signature")
    if not isinstance(sig, str) or not hmac.compare_digest(sig, _sign(info.get("code"), core)):
        problems.append("the entry signature does not match: the entry was changed after it was signed")
    elif info.get("entry_id") != sig[:10]:
        problems.append("the entry id was changed")
    if info.get("content") != _content_sha(files):
        problems.append("the book text does not match what was signed")
    if contest:
        if info.get("event") != contest["event"] or norm_code(info.get("code")) != contest["code"]:
            problems.append("this entry belongs to another event (%s)" % info.get("code"))
        if info.get("division") not in contest["divisions"]:
            problems.append("division '%s' is not in this contest" % info.get("division"))
        if contest["word_limit"] and int(info.get("words") or 0) > contest["word_limit"]:
            problems.append("over the word limit (%s of %d)" % (info.get("words"), contest["word_limit"]))
        if contest["deadline"] and str(info.get("created", ""))[:10] > contest["deadline"]:
            problems.append("late: made %s, deadline %s" % (str(info.get("created"))[:10], contest["deadline"]))
    return {"ok": not problems, "problems": problems, "info": info}


def _entry_text(path):
    with zipfile.ZipFile(path) as z:
        check_zip(z)
        top = z.namelist()[0].split("/", 1)[0] + "/"
        names = sorted(n for n in z.namelist() if S._is_chapter(n[len(top):]))
        return "\n\n".join(read_member(z, n).decode("utf-8", "replace").strip() for n in names)


# ---------------------------------------------------------------------------------------------- judging pack
_STOP = {"the", "and", "family", "team", "club", "our", "mrs", "mr", "ms", "dr", "inc", "ltd", "llc", "school"}


def _name_re(names):
    parts = set()
    for n in names:
        n = (n or "").strip()
        if len(n) >= 2:
            parts.add(n)
            parts.update(w for w in re.split(r"[\s\-]+", n) if len(w) >= 3 and w.lower() not in _STOP)
    if not parts:
        return None
    return re.compile(r"(?<!\w)(?:%s)(?!\w)" % "|".join(re.escape(p) for p in sorted(parts, key=len, reverse=True)),
                      re.I | re.U)


def scrub(text, names):
    """Replace every name (whole word, any case, possessives too) with [name]."""
    pat = _name_re(names)
    return pat.sub("[name]", text) if pat else text


def anon_id(code, info):
    return "E" + hashlib.sha256(("%s|%s" % (norm_code(code), info["signature"])).encode()).hexdigest()[:6].upper()


def scan_entries(contest, paths, counter=None, allow_late=False):
    """Verify every entry zip: ({anon id: {path, info}}, [rejects]). A repeated entry is kept once."""
    good, rejects = {}, []
    for p in sorted(paths):
        res = verify_entry(p, contest, counter)
        probs = [x for x in res["problems"] if not (allow_late and x.startswith("late:"))]
        if probs:
            rejects.append({"file": os.path.basename(p), "problems": probs})
            continue
        good.setdefault(anon_id(contest["code"], res["info"]), {"path": p, "info": res["info"]})
    return good, rejects


def build_pack(dest_dir, contest, entries):
    """The anonymous judging pack. Every entrant, team and member name in the contest is scrubbed from titles and text; the
    reflection, manifest and file names are left out. Entries come in a repeatable shuffled order."""
    items = []
    names = [n for e in entries.values() for n in [e["info"]["entrant"]] + list(e["info"]["members"])]
    for aid, e in entries.items():
        i = e["info"]
        items.append({"id": aid, "division": i["division"], "words": i["words"],
                      "title": scrub(i.get("book_title") or "", names) or "Untitled",
                      "text": scrub(_entry_text(e["path"]), names)})
    items.sort(key=lambda x: S._sha((contest["code"] + x["id"]).encode()))
    core = {"format": PACK_FORMAT, "version": 1, "code": contest["code"], "event": contest["event"],
            "title": contest["title"], "prompt": contest["prompt"], "rubric": contest["rubric"], "items": items,
            "pack_id": S._sha(json.dumps([contest["code"]] + sorted(entries)).encode())[:10]}
    core["signature"] = _sign(contest["code"], core)
    out = _unique(dest_dir, "%s judging pack" % contest["code"], PACK_EXT)
    _write_json(out, core)
    read_pack(out)
    return {"path": out, "file": os.path.basename(out), "entries": len(items), "pack_id": core["pack_id"]}


def read_pack(data):
    d = _load(data, "judging pack")
    if d.get("format") != PACK_FORMAT or not isinstance(d.get("items"), list) or not isinstance(d.get("rubric"), list):
        raise ValueError("That is not a Quill judging pack.")
    core = {k: v for k, v in d.items() if k != "signature"}
    sig = d.get("signature")
    if not isinstance(sig, str) or not hmac.compare_digest(sig, _sign(d.get("code"), core)):
        raise ValueError("This judging pack was changed after the organiser made it.")
    return d


def anon_leaks(pack, names):
    """The names that still appear anywhere in the pack (empty when nothing leaks)."""
    pat = _name_re(names)
    if not pat:
        return []
    found = {m.group(0).lower() for m in pat.finditer(json.dumps(pack, ensure_ascii=False))}
    return sorted(found)


# ---------------------------------------------------------------------------------------------- score sheets
def make_sheet(dest_dir, pack, judge, scores, comments=None):
    """A judge's signed score sheet. scores = {entry id: {criterion id: points}}: every entry, every criterion,
    whole points from 0 to the criterion's top score."""
    judge = S._clean(judge, 60)
    if not judge:
        raise ValueError("Enter your name so the organiser can tell the judges apart.")
    rows, comments = {}, comments or {}
    for it in pack["items"]:
        got = (scores or {}).get(it["id"])
        if not isinstance(got, dict):
            raise ValueError("Score every entry: %s has no scores." % it["id"])
        row = {}
        for c in pack["rubric"]:
            v = got.get(c["id"])
            if isinstance(v, bool) or isinstance(v, str) and not re.fullmatch(r"\d{1,3}", v.strip()) \
                    or isinstance(v, float) and v != int(v) or v is None:
                raise ValueError("Entry %s needs a whole number for '%s'." % (it["id"], c["text"]))
            v = int(v)
            if not 0 <= v <= c["max"]:
                raise ValueError("Entry %s: '%s' must be between 0 and %d." % (it["id"], c["text"], c["max"]))
            row[c["id"]] = v
        rows[it["id"]] = {"scores": row, "comment": S._clean(comments.get(it["id"], ""), 1000)}
    core = {"format": SHEET_FORMAT, "version": 1, "code": pack["code"], "event": pack["event"],
            "pack_id": pack["pack_id"], "judge": judge, "entries": rows}
    core["signature"] = _sign(pack["code"], core)
    out = _unique(dest_dir, "%s scores %s" % (pack["code"], judge), SHEET_EXT)
    _write_json(out, core)
    return {"path": out, "file": os.path.basename(out), "entries": len(rows)}


def read_sheet(data, contest=None):
    d = _load(data, "score sheet")
    if d.get("format") != SHEET_FORMAT or not isinstance(d.get("entries"), dict):
        raise ValueError("That is not a Quill score sheet.")
    core = {k: v for k, v in d.items() if k != "signature"}
    sig = d.get("signature")
    if not isinstance(sig, str) or not hmac.compare_digest(sig, _sign(d.get("code"), core)):
        raise ValueError("A score sheet was changed after the judge signed it (%s)." % d.get("judge", "unknown judge"))
    if contest and d.get("event") != contest["event"]:
        raise ValueError("A score sheet belongs to another event (%s)." % d.get("code"))
    return d


# ---------------------------------------------------------------------------------------------- results
def rank(contest, entries, sheets):
    """Ranked results per division. entries = {anon id: {"info": ...}}; sheets = verified sheet dicts.
    One sheet per judge. Order: higher mean total; then higher mean on criterion 1, 2, ...; then the earlier
    entry; anything still level shares its rank. Sheet scores are re-checked against the rubric."""
    judges = [s["judge"].strip().lower() for s in sheets]
    if len(set(judges)) != len(judges):
        raise ValueError("Two score sheets come from the same judge. Keep one per judge.")
    mx = {c["id"]: c["max"] for c in contest["rubric"]}
    crit = list(mx)
    rows = []
    for aid, e in entries.items():
        i = e["info"]
        got = [s["entries"][aid]["scores"] for s in sheets if aid in s["entries"]]
        for g in got:
            if set(g) != set(mx) or any(not isinstance(g[c], int) or not 0 <= g[c] <= mx[c] for c in mx):
                raise ValueError("A score for entry %s is outside the rubric." % aid)
        mean = {c: Fraction(sum(g[c] for g in got), len(got)) for c in crit} if got else None
        rows.append({"id": aid, "entry_id": i["entry_id"], "division": i["division"], "entrant": i["entrant"],
                     "members": list(i["members"]), "title": i.get("book_title", ""), "words": i["words"],
                     "created": i.get("created", ""), "judges": len(got), "_mean": mean,
                     "total": sum(mean.values()) if mean else None})
    out = {}
    for d in contest["divisions"]:
        part = [r for r in rows if r["division"] == d]
        if not part:
            continue
        scored = sorted((r for r in part if r["_mean"]), key=lambda r: (
            -r["total"], tuple(-r["_mean"][c] for c in crit), r["created"], r["id"]))

        def level(a, b):
            return (a["total"], tuple(a["_mean"][c] for c in crit), a["created"]) == \
                   (b["total"], tuple(b["_mean"][c] for c in crit), b["created"])
        for n, r in enumerate(scored):
            r["rank"] = 1 if n == 0 else (scored[n - 1]["rank"] if level(r, scored[n - 1]) else n + 1)
        for n, r in enumerate(scored):
            r["tied"] = (n > 0 and level(r, scored[n - 1])) or (n + 1 < len(scored) and level(r, scored[n + 1]))
        unscored = [r for r in part if not r["_mean"]]
        for r in unscored:
            r["rank"], r["tied"] = None, False
        out[d] = scored + unscored
    for lst in out.values():
        for r in lst:
            m = r.pop("_mean")
            r["total"] = float(round(r["total"], 2)) if r["total"] is not None else None
            r["mean"] = {c: float(round(m[c], 2)) for c in crit} if m else {}
    return {"event": contest["event"], "code": contest["code"], "title": contest["title"], "divisions": out,
            "judges": sorted(s["judge"] for s in sheets), "max_total": sum(mx.values()), "tiebreak": list(TIEBREAK)}


def results_text(res):
    L = ["%s (%s)" % (res["title"], res["code"]), "Judges: %s" % (", ".join(res["judges"]) or "none yet"),
         "Out of %d. Tie-breaks: %s." % (res["max_total"], "; ".join(res["tiebreak"])), ""]
    for d, rows in res["divisions"].items():
        L.append(DIVISIONS[d])
        for r in rows:
            who = r["entrant"] + (" (%s)" % ", ".join(r["members"]) if r["members"] else "")
            place = ("%s%d" % ("=" if r["tied"] else "", r["rank"])) if r["rank"] else "-"
            pts = ("%g points, %d judge%s" % (r["total"], r["judges"], "" if r["judges"] == 1 else "s")) if r["total"] is not None else "not scored"
            L.append("  %s  %s, \"%s\", %s" % (place, who, r["title"], pts))
        L.append("")
    return "\n".join(L)


PLACES = {1: "First place", 2: "Second place", 3: "Third place"}


def certificates_html(res, places=3, participation=False):
    """One printable page set (landscape, one certificate per sheet) for each placed entry, or for everyone scored."""
    e = html.escape
    pages = []
    for d, rows in res["divisions"].items():
        for r in rows:
            if r["rank"] is None or not (r["rank"] <= places or participation):
                continue
            head = PLACES.get(r["rank"], "Place %d" % r["rank"]) if r["rank"] <= places else "Certificate of participation"
            mem = "<p class='mem'>%s</p>" % e(", ".join(r["members"])) if r["members"] else ""
            pages.append(
                "<section><p class='org'>Stark Challenge</p><h1>%s</h1><p class='sub'>%s division</p>"
                "<p>presented to</p><h2>%s</h2>%s<p>for <em>%s</em></p><p class='ev'>%s, event %s</p>"
                "<p class='sc'>%g points of %d</p></section>"
                % (e(head), e(DIVISIONS[d]), e(r["entrant"]), mem, e(r["title"] or "their entry"), e(res["title"]),
                   e(res["code"]), r["total"], res["max_total"]))
    css = ("@page{size:landscape;margin:0}body{margin:0;font-family:Georgia,serif;color:#222}"
           "section{box-sizing:border-box;width:100vw;height:100vh;page-break-after:always;border:12px double #8a6d3b;"
           "padding:6vh 8vw;text-align:center}h1{font-size:44px;margin:.3em 0}h2{font-size:38px;margin:.2em 0;color:#5a3d12}"
           ".org{letter-spacing:.3em;text-transform:uppercase;color:#8a6d3b}.sub,.ev,.sc,.mem{color:#555}")
    return ("<!doctype html><meta charset=utf-8><title>%s certificates</title><style>%s</style>%s"
            % (e(res["code"]), css, "".join(pages) or "<p>No placed entries yet.</p>"))


def write_results(dest_dir, contest, res, places=3, participation=False):
    paths = {"results": _unique(dest_dir, contest["code"] + " results", ".json"),
             "text": _unique(dest_dir, contest["code"] + " results", ".txt"),
             "certificates": _unique(dest_dir, contest["code"] + " certificates", ".html")}
    _write_json(paths["results"], res)
    with open(paths["text"], "w", encoding="utf-8") as f:
        f.write(results_text(res))
    with open(paths["certificates"], "w", encoding="utf-8") as f:
        f.write(certificates_html(res, places, participation))
    return paths


def main(argv=None):
    """python quill_contests.py verify <entry.zip>: prints the summary, exit 1 on a problem."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2 or argv[0] != "verify":
        print("usage: python quill_contests.py verify <entry.zip>")
        return 2
    res = verify_entry(argv[1])
    i = res["info"]
    if i:
        print("%s, %s division, %s words, event %s" % (i.get("entrant"), i.get("division"), i.get("words"), i.get("code")))
    print("OK: signed and unchanged." if res["ok"] else "PROBLEMS:\n  " + "\n  ".join(res["problems"]))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
