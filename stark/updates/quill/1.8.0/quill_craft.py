"""Stark Quill craft tools: progress figures, plot outlines, the submission tracker, the series bible and the
revision checks. Everything here is plain rules and plain data: no network, no AI, no clock of its own (callers
pass the date), no file access (writer_app.py reads and writes the files and calls these functions)."""
from __future__ import annotations

import math
import re
import statistics
import uuid
from datetime import date, datetime, timedelta

DAY_RX = re.compile(r"^\d{4}-\d\d-\d\d$")


def parse_day(s):
    if isinstance(s, date):
        return s
    if not isinstance(s, str) or not DAY_RX.match(s):
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _txt(v, n):
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(v if v is not None else "")).strip()[:n]


# ======================================================================================================
# 1. Progress: sessions, pace, heatmap, projected finish
# ======================================================================================================
SESSION_GAP = 30 * 60   # a pause of 30 minutes or more starts a new writing session


def note_session(rec, now):
    """Count a writing session on a day's record (a pause over SESSION_GAP starts the next one)."""
    last = rec.get("last")
    if not isinstance(last, (int, float)) or now - last >= SESSION_GAP or int(rec.get("sessions", 0)) < 1:
        rec["sessions"] = int(rec.get("sessions", 0)) + 1
    rec["last"] = int(now)


def heatmap(days, today, goal=0, weeks=26):
    """Columns of 7 days (Monday first) ending with the week of `today`. Level 0 = nothing written, 1 to 4 = how
    much of the day's goal (or, with no goal, of the busiest day shown) was written."""
    start = today - timedelta(days=today.weekday() + 7 * (weeks - 1))
    cells = []
    for i in range((today - start).days + 1):
        d = start + timedelta(days=i)
        cells.append((d, max(0, int((days.get(d.isoformat()) or {}).get("words", 0)))))
    top = goal if goal else max([w for _, w in cells] + [1])
    out = []
    for d, w in cells:
        lvl = 0 if w <= 0 else min(4, 1 + int(3.999 * min(1.0, w / top)))
        out.append({"date": d.isoformat(), "words": w, "level": lvl})
    return out


def pace(days, today, span=14):
    """Average net words a day over the last `span` days, today included (days with no record count as zero)."""
    total = sum(max(0, int((days.get((today - timedelta(days=i)).isoformat()) or {}).get("words", 0)))
                for i in range(span))
    return total / float(span)


def project_finish(target, words_now, days, today, span=14):
    """-> {'status': 'done'|'none'|'on', ...}. 'none' = no target, or no recent pace to work from."""
    target = int(target or 0)
    if target <= 0:
        return {"status": "none", "reason": "target"}
    remaining = target - int(words_now)
    if remaining <= 0:
        return {"status": "done", "remaining": 0}
    avg = pace(days, today, span)
    if avg < 1:
        return {"status": "none", "reason": "pace", "remaining": remaining}
    need = int(math.ceil(remaining / avg))
    return {"status": "on", "remaining": remaining, "pace": int(round(avg)), "days": need,
            "date": (today + timedelta(days=need)).isoformat()}


def session_stats(days, today):
    rec = days.get(today.isoformat()) or {}
    words, n = max(0, int(rec.get("words", 0))), int(rec.get("sessions", 0))
    tot_w = sum(max(0, int(r.get("words", 0))) for r in days.values())
    tot_s = sum(int(r.get("sessions", 0)) for r in days.values())
    return {"sessions_today": n, "per_session_today": int(words / n) if n else 0,
            "sessions_total": tot_s, "per_session_all": int(tot_w / tot_s) if tot_s else 0,
            "days_written": sum(1 for r in days.values() if int(r.get("words", 0)) > 0), "words_total": tot_w}


# ======================================================================================================
# 2. Plot structure templates
# ======================================================================================================
def _beats(rows):
    return [{"id": r[0], "title": r[1], "start": r[2], "end": r[3], "prompt": r[4]} for r in rows]


TEMPLATES = {
    "three_act": {"name": "Three acts", "beats": _beats([
        ("setup", "Setup", 0, 20, "Who is your lead, what is their ordinary life, and what is missing from it?"),
        ("plot1", "First plot point", 20, 25, "What event closes the old life and forces a choice?"),
        ("rising", "Rising action", 25, 50, "What does the lead try, and what does each attempt cost?"),
        ("midpoint", "Midpoint", 50, 55, "What turn changes what the lead knows or wants?"),
        ("complications", "Complications", 55, 75, "How do the odds get worse, and what does the lead lose?"),
        ("plot2", "Second plot point", 75, 80, "What is the lowest moment, and what makes the lead try once more?"),
        ("climax", "Climax", 80, 95, "How do the lead and the opposing force meet for the last time?"),
        ("resolution", "Resolution", 95, 100, "What has changed, and what does the new ordinary look like?")])},
    "save_the_cat": {"name": "Save the Cat beats", "beats": _beats([
        ("opening_image", "Opening image", 0, 1, "What single picture shows the lead's life before the story?"),
        ("setup", "Set-up", 1, 5, "Who and what matters in the lead's world, and what is wrong?"),
        ("theme", "Theme stated", 5, 10, "Who says the book's lesson aloud, and does the lead hear it?"),
        ("catalyst", "Catalyst", 10, 12, "What event knocks the lead's life off course?"),
        ("debate", "Debate", 12, 25, "What does the lead doubt, and why not act yet?"),
        ("break_two", "Break into two", 25, 26, "What choice takes the lead into the new world?"),
        ("b_story", "B story", 26, 30, "Who arrives to carry the theme: a friend, a love, a mentor?"),
        ("fun_games", "Fun and games", 30, 50, "What is the promise of the premise, shown in scenes?"),
        ("midpoint", "Midpoint", 50, 51, "What false win or false defeat raises the stakes?"),
        ("bad_guys", "Bad guys close in", 51, 75, "How do outside pressure and inner flaws tighten?"),
        ("all_is_lost", "All is lost", 75, 76, "What is taken away: a person, a hope, a plan?"),
        ("dark_night", "Dark night of the soul", 76, 80, "What does the lead feel and face alone?"),
        ("break_three", "Break into three", 80, 81, "What idea, from the B story, shows the way?"),
        ("finale", "Finale", 81, 99, "How does the lead use what they learned to win, step by step?"),
        ("final_image", "Final image", 99, 100, "What picture answers the opening image?")])},
    "heros_journey": {"name": "Hero's Journey", "beats": _beats([
        ("ordinary", "Ordinary world", 0, 8, "What is the hero's everyday life, and what do they lack?"),
        ("call", "Call to adventure", 8, 12, "What problem or invitation arrives?"),
        ("refusal", "Refusal of the call", 12, 17, "What fear or duty holds the hero back?"),
        ("mentor", "Meeting the mentor", 17, 22, "Who gives the hero a tool, a push or some wisdom?"),
        ("threshold", "Crossing the threshold", 22, 27, "What moment commits the hero to the unknown world?"),
        ("tests", "Tests, allies, enemies", 27, 45, "Who does the hero meet, and what do they learn about the rules?"),
        ("approach", "Approach to the inmost cave", 45, 52, "What preparation comes before the greatest danger?"),
        ("ordeal", "The ordeal", 52, 62, "What is the central crisis, where the hero risks everything?"),
        ("reward", "Reward", 62, 70, "What does the hero take from the ordeal, and at what price?"),
        ("road_back", "The road back", 70, 80, "What pursuit or choice sends the hero home?"),
        ("resurrection", "Resurrection", 80, 95, "What final test proves the hero is changed?"),
        ("elixir", "Return with the elixir", 95, 100, "What does the hero bring back, and who benefits?")])},
}


def template_list():
    return [{"id": k, "name": v["name"], "beats": len(v["beats"])} for k, v in TEMPLATES.items()]


def apply_template(outline, template_id, replace=False):
    """A new outline for the template. Chapters already placed in beats of the same ids are kept."""
    if template_id not in TEMPLATES:
        raise ValueError("Unknown template.")
    old = (outline or {}).get("beats") or {}
    beats = {}
    for b in TEMPLATES[template_id]["beats"]:
        keep = old.get(b["id"]) if not replace and (outline or {}).get("template") == template_id else None
        beats[b["id"]] = keep if isinstance(keep, dict) else {"chapters": [], "notes": ""}
    return {"template": template_id, "beats": beats}


def set_beat(outline, beat_id, chapters=None, notes=None, valid=()):
    if not outline or outline.get("template") not in TEMPLATES:
        raise ValueError("Pick a template first.")
    if beat_id not in outline["beats"]:
        raise ValueError("No such beat.")
    cur = outline["beats"][beat_id]
    if chapters is not None:
        if not isinstance(chapters, list):
            raise ValueError("chapters must be a list")
        valid = set(valid)
        cur["chapters"] = [c for i, c in enumerate(chapters) if isinstance(c, str) and c in valid
                           and c not in chapters[:i]]
    if notes is not None:
        cur["notes"] = _txt(notes, 2000)
    return outline


def outline_view(outline, chapters, target=0, resolve=lambda n: n):
    """The outline with a word count and a share per beat. `chapters` = [{'name','title','words'}]. A chapter may
    sit in more than one beat; its words count in each. `resolve` follows a chapter's rename trail."""
    if not outline or outline.get("template") not in TEMPLATES:
        return None
    by = {c["name"]: c for c in chapters}
    total = sum(c["words"] for c in chapters)
    base = int(target or 0) or total
    beats, placed = [], set()
    for b in TEMPLATES[outline["template"]]["beats"]:
        rec = outline["beats"].get(b["id"]) or {}
        names = []
        for c in rec.get("chapters") or []:
            c = resolve(c)
            if c in by and c not in names:
                names.append(c)
        placed.update(names)
        words = sum(by[c]["words"] for c in names)
        want = int(round(base * (b["end"] - b["start"]) / 100.0))
        status = "empty" if not names else ("on" if want and 0.6 * want <= words <= 1.4 * want else
                                            ("short" if words < want else "long"))
        beats.append(dict(b, chapters=names, titles=[by[c]["title"] for c in names], notes=rec.get("notes", ""),
                          words=words, share=round(100.0 * words / total, 1) if total else 0.0,
                          expected_words=want, expected_share=b["end"] - b["start"], status=status))
    return {"template": outline["template"], "name": TEMPLATES[outline["template"]]["name"], "beats": beats,
            "total": total, "base": base, "unplaced": [c["name"] for c in chapters if c["name"] not in placed]}


# ======================================================================================================
# 3. Submission tracker and letter templates
# ======================================================================================================
SUB_KINDS = ("agent", "publisher", "contest", "other")
SUB_STATUS = ("planned", "sent", "requested", "no_reply", "rejected", "offer", "accepted", "withdrawn")
SUB_LABEL = {"planned": "Planned", "sent": "Sent", "requested": "More requested", "no_reply": "No reply",
             "rejected": "Rejected", "offer": "Offer", "accepted": "Accepted", "withdrawn": "Withdrawn"}


def clean_submission(d, old=None):
    d = d if isinstance(d, dict) else {}
    out = dict(old or {})
    out["id"] = out.get("id") or re.sub(r"\W", "", _txt(d.get("id"), 20)) or uuid.uuid4().hex[:10]
    for k, n in (("name", 120), ("contact", 200), ("response", 2000), ("notes", 2000), ("what", 200)):
        if k in d or k not in out:
            out[k] = _txt(d.get(k, out.get(k, "")), n)
    if not out["name"]:
        raise ValueError("Give the agent, publisher or contest a name.")
    for k, allowed in (("kind", SUB_KINDS), ("status", SUB_STATUS)):
        v = d.get(k, out.get(k, allowed[0] if k == "kind" else "planned"))
        if v not in allowed:
            raise ValueError("%s must be one of: %s." % (k.capitalize(), ", ".join(allowed)))
        out[k] = v
    for k in ("sent", "response_date", "follow_up"):
        v = d.get(k, out.get(k, ""))
        v = _txt(v, 10)
        if v and parse_day(v) is None:
            raise ValueError("Dates look like 2026-11-20.")
        out[k] = v
    return out


def submissions_summary(items, today):
    counts = {s: 0 for s in SUB_STATUS}
    waits = []
    due = []
    for it in items:
        counts[it["status"]] = counts.get(it["status"], 0) + 1
        s, r = parse_day(it.get("sent")), parse_day(it.get("response_date"))
        if s and r and r >= s:
            waits.append((r - s).days)
        f = parse_day(it.get("follow_up"))
        if f and f <= today and it["status"] in ("sent", "requested", "no_reply"):
            due.append(it["id"])
    return {"total": len(items), "counts": counts, "due": due,
            "avg_wait_days": int(round(sum(waits) / len(waits))) if waits else None}


QUERY_FIELDS = [
    ("agent_name", "Agent or editor", "Ms. Rowan"),
    ("reason", "Why you are writing to them", "I chose you because you represent ..., and my book shares its ..."),
    ("hook", "The hook: who wants what, and what stands in the way", ""),
    ("stakes", "What is lost if they fail", ""),
    ("title", "Title", ""), ("genre", "Genre", ""), ("word_count", "Word count", ""),
    ("comps", "Comparable books (optional)", ""),
    ("bio", "About you (short)", ""), ("author", "Your name", ""), ("contact", "Your email and phone", ""),
]
SYNOPSIS_FIELDS = [
    ("title", "Title", ""), ("author", "Your name", ""), ("genre", "Genre", ""),
    ("setup", "The lead and their world", ""), ("inciting", "The event that starts the story", ""),
    ("rising", "What the lead tries, and what it costs", ""), ("midpoint", "The turn in the middle", ""),
    ("crisis", "The lowest point", ""), ("climax", "The final confrontation", ""),
    ("ending", "How it ends, including the ending (agents expect it)", ""),
]
_LETTER = {
    "query": ("Dear {agent_name},\n\n{reason}\n\n{hook}\n\n{stakes}\n\n{TITLE} is a {genre} novel, complete at "
              "{word_count} words. {comps}\n\n{bio}\n\nThank you for your time and consideration.\n\n"
              "Sincerely,\n{author}\n{contact}\n"),
    "synopsis": "SYNOPSIS: {TITLE}\nby {author}\n{genre}\n\n{setup}\n\n{inciting}\n\n{rising}\n\n{midpoint}\n\n"
                "{crisis}\n\n{climax}\n\n{ending}\n",
}
_OPTIONAL = {"comps", "bio", "stakes", "genre"}


def letter_fields(kind):
    rows = {"query": QUERY_FIELDS, "synopsis": SYNOPSIS_FIELDS}.get(kind)
    if rows is None:
        raise ValueError("Unknown template.")
    return [{"id": a, "label": b, "hint": c} for a, b, c in rows]


def render_letter(kind, fields):
    """Fill the template with the writer's words. A field left empty shows as [field name], except the optional
    ones, which are dropped. -> (text, missing field ids)."""
    if kind not in _LETTER:
        raise ValueError("Unknown template.")
    fields = fields if isinstance(fields, dict) else {}
    labels = {a: b for a, b, _ in (QUERY_FIELDS if kind == "query" else SYNOPSIS_FIELDS)}
    missing = []

    def val(key):
        k = key.lower()
        v = _txt(fields.get(k, ""), 4000)
        if k in ("title",) and v and key == "TITLE":
            v = v.upper() if kind == "synopsis" else v
        if v:
            return v
        if k in _OPTIONAL:
            return ""
        if k not in missing:
            missing.append(k)
        return "[%s]" % labels.get(k, k).lower()
    text = re.sub(r"\{(\w+)\}", lambda m: val(m.group(1)), _LETTER[kind])
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"(complete at [^\n]*?words\.) \n", r"\1\n", text)
    text = text.replace(" is a  novel,", " is a novel,")   # the optional genre left empty
    return text.strip() + "\n", missing


# ======================================================================================================
# 4. Series bible
# ======================================================================================================
def name_key(s):
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def clean_series(obj, books, old=None):
    """A series from the page: a name, the books in it (folder names that exist, in reading order) and the bible
    lists. Everything else is dropped."""
    obj = obj if isinstance(obj, dict) else {}
    name = _txt(obj.get("name"), 120)
    if not name:
        raise ValueError("Give the series a name.")
    known = set(books)
    out = {"id": (old or {}).get("id") or _txt(obj.get("id"), 20) or uuid.uuid4().hex[:10], "name": name,
           "books": [b for i, b in enumerate(obj.get("books") or []) if isinstance(b, str) and b in known
                     and b not in (obj.get("books") or [])[:i]]}
    for lst, extra in (("characters", ("aliases",)), ("places", ()), ("timeline", ())):
        rows = []
        for r in (obj.get(lst) or [])[:500]:
            if not isinstance(r, dict):
                continue
            if lst == "timeline":
                row = {"when": _txt(r.get("when"), 60), "text": _txt(r.get("text"), 600),
                       "book": r.get("book") if r.get("book") in known else ""}
                if row["text"] or row["when"]:
                    rows.append(row)
                continue
            row = {"name": _txt(r.get("name"), 120), "notes": _txt(r.get("notes"), 2000),
                   "books": [b for b in (r.get("books") or []) if b in known]}
            if "aliases" in extra:
                al = r.get("aliases")
                if isinstance(al, str):
                    al = al.split(",")
                row["aliases"] = [a for a in (_txt(x, 80) for x in (al or [])[:20]) if a]
            if row["name"]:
                rows.append(row)
        out[lst] = rows
    return out


def merge_lore(series, per_book):
    """Add the people, places and dated chapters that Lore found in each book to the bible; nothing already there
    is changed. per_book = [(folder, lore result)] -> number added."""
    added = 0
    for lst, src in (("characters", "characters"), ("places", "locations")):
        have = {}
        for r in series[lst]:
            for n in [r["name"]] + r.get("aliases", []):
                have[name_key(n)] = r
        for folder, lore in per_book:
            for e in lore.get(src) or []:
                row = have.get(name_key(e["name"])) or next((have[name_key(a)] for a in e.get("aliases", []) if name_key(a) in have), None)
                if row is None:
                    row = {"name": e["name"], "notes": "", "books": []}
                    if lst == "characters":
                        row["aliases"] = list(e.get("aliases", []))
                    series[lst].append(row)
                    for n in [row["name"]] + row.get("aliases", []):
                        have[name_key(n)] = row
                    added += 1
                if folder not in row["books"]:
                    row["books"].append(folder)
    seen = {(t["book"], t["when"], t["text"]) for t in series["timeline"]}
    for folder, lore in per_book:
        for t in lore.get("timeline") or []:
            row = {"when": t["date"], "text": t["chapter"] + (" (flashback)" if t.get("flashback") else ""), "book": folder}
            if (row["book"], row["when"], row["text"]) not in seen:
                seen.add((row["book"], row["when"], row["text"]))
                series["timeline"].append(row)
                added += 1
    return added


# ======================================================================================================
# 5. Revision checks (rules only; the writer switches each one off)
# ======================================================================================================
CHECKS = ("overused", "phrases", "sentences", "adverbs", "filler", "tags", "readability", "passive")
CHECK_LABEL = {"overused": "Overused words", "phrases": "Repeated phrases", "sentences": "Sentence length",
               "adverbs": "Adverbs", "filler": "Filler words", "tags": "Dialogue tags",
               "readability": "Readability", "passive": "Passive voice"}
WORD_RX = re.compile(r"[A-Za-z][A-Za-z'’-]*")
STOP = frozenset("""a about above after again against all also am an and any are as at be because been before being
below between both but by can could did do does doing down during each few for from further had has have having he her
here hers herself him himself his how i if in into is it its itself just me more most my myself no nor not of off on
once only or other our ours ourselves out over own same she should so some such than that the their theirs them
themselves then there these they this those through to too under until up us very was we were what when where which
while who whom why will with would you your yours yourself yourselves said say says one two""".split())
LY_NOT = frozenset("""family only early lonely silly friendly ugly belly holy lily bully jelly rely reply supply apply
imply comply july italy fly ally assembly anomaly butterfly daily likely lovely lively elderly deadly costly curly
orderly homely ghostly heavenly hilly wily oily smelly sly gully rally tally dolly folly holly molly sally emily
billy willy chilly jolly melancholy monopoly multiply""".split())
FILLERS = ["all of a sudden", "needless to say", "in order to", "kind of", "sort of", "a bit", "a little", "of course",
           "at all", "in fact", "began to", "started to", "seemed to", "managed to", "just", "really", "quite",
           "rather", "somewhat", "actually", "basically", "literally", "simply", "suddenly", "totally", "definitely"]
SPEECH = frozenset("""said asked replied answered whispered shouted yelled cried exclaimed murmured muttered snapped
growled hissed sighed laughed snarled barked declared demanded insisted retorted stammered stated remarked added
continued called breathed gasped sobbed groaned mumbled announced urged pleaded begged sneered scoffed chuckled
giggled bellowed thundered whined cooed offered suggested warned wondered inquired queried responded roared
screamed sang""".split())
PLAIN_TAGS = frozenset(("said", "asked"))
NOT_SPEECH = frozenset("smiled nodded grinned frowned shrugged winked grimaced smirked blinked stared glared "
                       "waved sneezed".split())
_PRON = r"(?:(?i:he|she|they|we|it|you)|I|[A-Z][a-z]+(?:\s[A-Z][a-z]+)?)"
_VERB = r"([a-z]+)"
TAG_RX = re.compile(r"""(?:^|(?<=[,.!?]))\s*(?:,\s*)?(?:%s\s+%s|%s\s+%s)(\s+[a-z]+ly\b)?""" % (_PRON, _VERB, _VERB, _PRON))
QUOTE_RX = re.compile(r"\"([^\"\n]{1,600})\"|“([^”\n]{1,600})”")
SENT_RX = re.compile(r"[^.!?…\n]+(?:[.!?…]+[\"”’')\]]*|(?=\n)|$)")
PRIORITY = {"tag": 0, "adverb": 1, "filler": 2, "passive": 3, "phrase": 4, "overused": 5, "long": 6, "run": 7}
BE = r"(?:am|is|are|was|were|be|been|being|is\s+being|are\s+being|was\s+being|were\s+being|has\s+been|have\s+been|had\s+been|will\s+be|got|gets|get|getting)"
IRREGULAR = frozenset("""arisen awoken beaten become begun bent bet bid bitten blown broken brought built burnt burned
bought caught chosen clung come cost crept cut dealt done drawn dreamt drunk driven eaten fallen fed felt fought found
fled flung forbidden forgotten forgiven frozen given gone ground grown hung heard hidden hit held hurt kept knelt known
laid led leant learnt left lent let lain lit lost made meant met paid put quit read ridden rung risen run said seen sought
sold sent set shaken shed shone shot shown shrunk shut sung sunk sat slain slept slid slung slit spoken sped spent spun
split spread sprung stood stolen stuck stung stunk struck strung sworn swept swollen swum swung taken taught torn told
thought thrown thrust trodden understood woken worn woven wept won wound written""".split())
NOT_PARTICIPLE = frozenset("""tired bored excited interested scared worried pleased surprised married used supposed
afraid ashamed aware tied red bed need seed feed bleed speed shed sled wed hundred sacred naked wicked rugged ragged
crooked beloved learned aged blessed""".split())
PASSIVE_RX = re.compile(r"\b(%s)\s+(?:(?:not|never|also|just|already|still|often|always|quickly|slowly|badly|\w+ly)\s+)?([A-Za-z]+)\b(\s+by\b)?" % BE, re.I)
def syllables(word):
    """English syllables by rule (good to about one in ten words; the grade figures are estimates anyway)."""
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return 0
    if len(w) <= 3:
        return 1
    n = len(re.findall(r"[aeiouy]+", w))
    if w.endswith("e") and not w.endswith(("le", "ee", "ye")):
        n -= 1                                              # silent e: cake, fire
    elif w.endswith("ed") and not w.endswith(("ted", "ded")):
        n -= 1                                              # walked, but wanted
    elif w.endswith("es") and not w.endswith(("ses", "zes", "ces", "ges", "xes", "shes", "ches")):
        n -= 1                                              # makes, but horses
    n += len(re.findall(r"ia|iu|io(?!n)|ua|eo|ii|uo", w))  # two sounds in one vowel run: piano, idea
    return max(1, n)


NOT_EN = frozenset("often seven eleven even open listen garden kitten golden wooden sudden children heaven happen "
                   "token oven siren linen women men ten then when".split())


def _participle(w):
    lw = w.lower()
    if lw in NOT_PARTICIPLE:
        return False
    return lw in IRREGULAR or (len(lw) > 3 and lw.endswith("ed")) or (len(lw) > 4 and lw.endswith("en")
                                                                     and lw not in NOT_EN)


def readability(text):
    """Flesch reading ease, Flesch-Kincaid grade, average sentence and word length, reading and speaking time."""
    sents = [m.group(0) for m in SENT_RX.finditer(text or "") if WORD_RX.search(m.group(0))]
    words = [w for _, _, w in _words(text or "")]
    if not words or not sents:
        return {"words": 0, "sentences": 0}
    syl = sum(syllables(w) for w in words)
    wps, spw = len(words) / float(len(sents)), syl / float(len(words))
    ease = 206.835 - 1.015 * wps - 84.6 * spw
    grade = 0.39 * wps + 11.8 * spw - 15.59
    hard = sum(1 for w in words if syllables(w) >= 3 and not w[0].isupper())
    band = ("very easy" if ease >= 90 else "easy" if ease >= 80 else "fairly easy" if ease >= 70 else
            "plain English" if ease >= 60 else "fairly difficult" if ease >= 50 else "difficult" if ease >= 30 else
            "very difficult")
    return {"words": len(words), "sentences": len(sents), "ease": round(max(0.0, min(100.0, ease)), 1), "grade": round(max(0.0, grade), 1),
            "band": band, "words_per_sentence": round(wps, 1), "syllables_per_word": round(spw, 2),
            "complex_share": round(100.0 * hard / len(words)), "reading_minutes": round(len(words) / 238.0, 1),
            "speaking_minutes": round(len(words) / 150.0, 1)}


def _words(text):
    return [(m.start(), m.end(), m.group(0)) for m in WORD_RX.finditer(text)]


def _lc(w):
    return w.lower().replace("’", "'")


def analyze_text(text, off=(), min_overuse=4):
    """Run every check not in `off`. -> {'words', 'checks': {id: {...}}, 'spans': [[start, end, kind, note]]}.
    Spans never overlap (the more specific kind wins) and are sorted by position."""
    off = set(off or ())
    text = text or ""
    words = _words(text)
    total = len(words)
    res, spans = {}, []
    if "overused" not in off:
        cnt, caps = {}, {}
        for s, e, w in words:
            lw = _lc(w).strip("'-")
            if len(lw) < 4 or lw in STOP:
                continue
            cnt.setdefault(lw, []).append((s, e))
            if w[0].isupper():
                caps[lw] = caps.get(lw, 0) + 1
        floor = max(min_overuse, int(math.ceil(total * 0.004)))
        rows = [(w, v) for w, v in cnt.items() if len(v) >= floor and caps.get(w, 0) < 0.8 * len(v)]
        rows.sort(key=lambda r: -len(r[1]))
        res["overused"] = {"items": [{"word": w, "count": len(v), "per_1000": round(1000.0 * len(v) / total, 1)}
                                     for w, v in rows[:25]]}
        for w, v in rows[:25]:
            spans += [(s, e, "overused", "%s: %d times" % (w, len(v))) for s, e in v]
    if "phrases" not in off:
        lows = [(s, e, _lc(w)) for s, e, w in words]
        found = {}
        for n, need in ((4, 2), (3, 3)):
            grams = {}
            for i in range(len(lows) - n + 1):
                g = tuple(x[2] for x in lows[i:i + n])
                if all(x in STOP for x in g):
                    continue
                grams.setdefault(g, []).append((lows[i][0], lows[i + n - 1][1]))
            for g, v in grams.items():
                if len(v) >= need:
                    found[g] = v
        keep = {}
        for g, v in sorted(found.items(), key=lambda kv: (-len(kv[0]), -len(kv[1]))):
            if any(len(h) > len(g) and len(found[h]) >= len(v) and " ".join(g) in " ".join(h) for h in keep):
                continue
            keep[g] = v
        top = sorted(keep.items(), key=lambda kv: (-len(kv[1]), -len(kv[0])))[:15]
        res["phrases"] = {"items": [{"phrase": " ".join(g), "count": len(v)} for g, v in top]}
        for g, v in top:
            spans += [(s, e, "phrase", "\"%s\": %d times" % (" ".join(g), len(v))) for s, e in v]
    if "sentences" not in off:
        sents = []
        for m in SENT_RX.finditer(text):
            n = len(_words(m.group(0)))
            if n:
                sents.append((m.start(), m.end(), n))
        lens = [x[2] for x in sents]
        info = {"count": len(lens)}
        if lens:
            mean = sum(lens) / float(len(lens))
            sd = statistics.pstdev(lens)
            buckets = [("1 to 5", 1, 5), ("6 to 10", 6, 10), ("11 to 20", 11, 20), ("21 to 30", 21, 30),
                       ("31 or more", 31, 10 ** 6)]
            info.update(mean=round(mean, 1), median=statistics.median(lens), longest=max(lens), shortest=min(lens),
                        variety=round(sd / mean, 2) if mean else 0,
                        buckets=[{"label": a, "count": sum(1 for x in lens if lo <= x <= hi)} for a, lo, hi in buckets])
            info["verdict"] = ("Sentence lengths vary little: try mixing short and long." if len(lens) >= 8
                               and info["variety"] < 0.45 else "Sentence lengths vary well." if len(lens) >= 8
                               else "Too short a text to judge variety.")
            runs, i = [], 0
            while i < len(sents):
                j = i
                while j + 1 < len(sents) and abs(sents[j + 1][2] - sents[i][2]) <= 2 and sents[i][2] >= 6:
                    j += 1
                if j - i + 1 >= 4:
                    runs.append((i, j))
                i = j + 1
            info["runs"] = [{"sentences": j - i + 1, "words_each": sents[i][2], "start": sents[i][0]} for i, j in runs]
            for i, j in runs:
                spans.append((sents[i][0], sents[j][1], "run", "%d sentences in a row of about %d words"
                              % (j - i + 1, sents[i][2])))
            longs = [x for x in sents if x[2] > 40]
            info["long"] = len(longs)
            spans += [(a, b, "long", "%d words in one sentence" % n) for a, b, n in longs]
        res["sentences"] = info
    if "adverbs" not in off:
        adv = {}
        for s, e, w in words:
            lw = _lc(w)
            if len(lw) > 4 and lw.endswith("ly") and lw not in LY_NOT and not (w[0].isupper() and s > 0
                                                                                and text[max(0, s - 2):s].strip() not in ("", ".", "!", "?", "\"", "“")):
                adv.setdefault(lw, []).append((s, e))
        n = sum(len(v) for v in adv.values())
        res["adverbs"] = {"count": n, "per_1000": round(1000.0 * n / total, 1) if total else 0,
                          "items": [{"word": w, "count": len(v)} for w, v in sorted(adv.items(), key=lambda kv: -len(kv[1]))[:20]]}
        for w, v in adv.items():
            spans += [(s, e, "adverb", "adverb") for s, e in v]
    if "filler" not in off:
        rows = []
        for f in FILLERS:
            hits = [(m.start(), m.end()) for m in re.finditer(r"(?i)(?<![A-Za-z'])%s(?![A-Za-z'])" % re.escape(f).replace(r"\ ", r"\s+"), text)]
            if hits:
                rows.append((f, hits))
        rows.sort(key=lambda r: -len(r[1]))
        res["filler"] = {"count": sum(len(h) for _, h in rows), "items": [{"word": f, "count": len(h)} for f, h in rows]}
        for f, hits in rows:
            spans += [(s, e, "filler", "filler") for s, e in hits]
    if "tags" not in off:
        said = asked = 0
        other, adv_tags, wrong = {}, [], []
        for qm in QUOTE_RX.finditer(text):
            tail = text[qm.end():qm.end() + 70].split("\n")[0]
            t = TAG_RX.match(tail)
            if not t:
                continue
            verb = (t.group(1) or t.group(2) or "").lower()
            if verb not in SPEECH and verb not in NOT_SPEECH:
                continue
            start, end = qm.end() + t.start(), qm.end() + t.end()
            while start < end and text[start] in " ,":
                start += 1
            if verb == "said":
                said += 1
            elif verb == "asked":
                asked += 1
            elif verb in SPEECH:
                other[verb] = other.get(verb, 0) + 1
            inner = (qm.group(1) or qm.group(2) or "").rstrip()
            if verb in NOT_SPEECH and (inner.endswith(",") or tail.lstrip().startswith(",")):
                wrong.append(verb)
                spans.append((start, end, "tag", "%s is an action, not speech: end the quote with a full stop" % verb))
            elif t.group(3):
                adv_tags.append(t.group(3).strip())
                spans.append((start, end, "tag", "adverb on a tag: let the words show it"))
            elif verb in SPEECH and verb not in PLAIN_TAGS:
                spans.append((start, end, "tag", "%s: said or asked reads quieter" % verb))
        n = said + asked + sum(other.values())
        res["tags"] = {"said": said, "asked": asked, "other": [{"word": w, "count": c} for w, c in sorted(other.items(), key=lambda kv: -kv[1])],
                       "plain_share": round(100.0 * (said + asked) / n) if n else None,
                       "adverb_tags": len(adv_tags), "action_as_tag": wrong}
    if "readability" not in off:
        res["readability"] = readability(text)
    if "passive" not in off:
        hits = []
        for m in PASSIVE_RX.finditer(text):
            if _participle(m.group(2)):
                hits.append((m.start(), m.end() if m.group(3) else m.start(2) + len(m.group(2)), m.group(3) is not None))
        res["passive"] = {"count": len(hits), "per_1000": round(1000.0 * len(hits) / total, 1) if total else 0,
                          "with_by": sum(1 for h in hits if h[2])}
        spans += [(a, b, "passive", "passive voice: who does it? Name them first if it matters") for a, b, _ in hits]
    pri = sorted(spans, key=lambda s: (PRIORITY[s[2]], s[0]))
    taken, out = [], []
    for s, e, kind, note in pri[:6000]:
        if any(s < b and a < e for a, b in taken):
            continue
        taken.append((s, e))
        out.append([s, e, kind, note])
    out.sort(key=lambda x: x[0])
    return {"words": total, "checks": res, "spans": out[:3000]}
