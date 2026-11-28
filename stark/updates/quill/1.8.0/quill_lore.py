"""Stark Quill - Living Lore engine (standard library only).

analyze(chapters) takes [(chapter_name, text), ...] in reading order and returns
  {"characters": [...], "locations": [...], "timeline": [...], "anomalies": [...]}.

Everything here is a lightweight HEURISTIC, not understanding:
  * Characters: capitalised names with person evidence (a title such as Captain/Lady, a speech/action verb
    right after the name, '"..." said Name', a possessive, or an age statement), or names seen >= 3 times
    that have no place evidence.
  * Locations: capitalised names after in/at/to/from/into/towards/near/across/through, or ending in a
    place word (Keep, River, Forest, ...).
  * Dates: ISO dates, '12 May 1271', 'May 12, 1271', and years after in/year/since/by ('in 1271',
    'the year 1271'). Sentences about the past (born, ago, earlier, remembered, founded...) do not move
    the story clock. Chapters containing flashback markers (flashback, years earlier, prologue,
    interlude, remembered, ...) are exempt from order checks.
  * Anomalies: 'appears_after_death' (a character who died acts/speaks in a later sentence that is not
    about ghosts, memories or dreams), 'dates_out_of_order' (a chapter's first story date is earlier
    than the latest date of the chapters before it), 'age_inconsistency' (two age statements that do
    not fit the elapsed story time, an age that goes down, or birth year vs age vs story date).
Expect false positives and misses; the panel shows them as hints, never edits text.

ai_status() asks a local Ollama (127.0.0.1:11434, 1.5 s probe) which models it has. ai_continuity() sends the
lore index (and the open chapter, cut to 6,000 characters) to that LOCAL model and returns up to 8 short
continuity questions. The prompt forbids writing or rewriting story text; the panel shows the answer as
suggestions with the model's name and never puts anything into the manuscript. Generation may take a minute
the first time (the model loads), so the timeout is 180 s. ai_summary() is the v1.0 call, kept for old clients.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request

MONTHS = {m: i + 1 for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july",
                                          "august", "september", "october", "november", "december"])}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})
TITLES = {"Mr", "Mrs", "Ms", "Miss", "Dr", "Sir", "Dame", "Lady", "Lord", "King", "Queen", "Prince", "Princess",
          "Captain", "General", "Master", "Mistress", "Father", "Mother", "Brother", "Sister", "Uncle", "Aunt",
          "Duke", "Duchess", "Count", "Countess", "Baron", "Professor", "Inspector", "Sergeant", "Lieutenant"}
STOP = {"The", "A", "An", "And", "But", "Or", "If", "When", "Then", "He", "She", "It", "They", "We", "I", "You",
        "His", "Her", "Their", "Our", "My", "Your", "Its", "This", "That", "These", "Those", "There", "Here",
        "What", "Who", "Why", "How", "Where", "Which", "Yes", "No", "Not", "In", "On", "At", "To", "From", "With",
        "Of", "For", "By", "As", "After", "Before", "Chapter", "Oh", "Well", "So", "Now", "Just", "Still", "Even",
        "All", "One", "Two", "Three", "Some", "Every", "Each", "Once", "Later", "Soon", "Perhaps", "Maybe",
        "Today", "Tomorrow", "Yesterday", "Tonight", "Morning", "Night", "Day", "Year", "Years", "God", "Gods",
        "Dear", "Please", "Thank", "Thanks", "Sorry", "Good", "Hello", "Hey", "Into", "Toward", "Towards",
        "Near", "Across", "Through", "Behind", "Under", "Over", "Upon", "Without", "Within", "While", "Though",
        "Although", "Because", "Since", "Until", "Do", "Did", "Does", "Is", "Was", "Are", "Were", "Be", "Let",
        "Don't", "I'm", "It's", "Mine", "Nothing", "Everything", "Someone", "Something", "Nobody", "Everyone",
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday", "Spring", "Summer",
        "Autumn", "Winter", "Prologue", "Epilogue", "Part", "Book", "Act", "Scene", "End", "Ah", "Ha", "Hmm",
        "Very", "Too", "Only", "Also", "Never", "Always", "Again", "Back", "Up", "Down", "Out", "Away", "Like"}
STOP |= {m.capitalize() for m in MONTHS}
STOP |= {"Battle", "Siege", "Beyond", "Tell", "Nobody", "Everyone", "Somewhere", "Meanwhile", "Inside", "Outside"}
EPITHETS = {"Old", "Young", "Little", "Big", "Poor", "Mad", "Saint", "St", "Good", "Dear"}  # "Old Tamsin Vey" -> "Tamsin Vey"
PLACE_WORDS = {"City", "Keep", "Castle", "River", "Forest", "Wood", "Woods", "Mountains", "Mountain", "Valley",
               "Town", "Village", "Street", "Hall", "Tower", "Lake", "Sea", "Isle", "Island", "Port", "Harbor",
               "Harbour", "Bridge", "Gate", "Fort", "Abbey", "Inn", "Market", "Square", "Road", "Hills", "Marsh",
               "Swamp", "Bay", "Palace", "Temple", "Kingdom", "Empire", "Coast", "Desert", "Pass", "Cove",
               "Manor", "Tavern", "Citadel", "Vale", "Moor", "Peak", "Falls", "Springs"}
ACTION = (r"said|says|asked|replied|whispered|shouted|muttered|answered|cried|called|nodded|smiled|laughed|"
          r"frowned|sighed|grinned|shrugged|walked|ran|turned|looked|stood|sat|entered|drew|took|gave|went|came|"
          r"stepped|reached|spoke|wrote|knocked|waved|fought|rode|arrived|returned|opened|laughs|smiles|walks")
SPEECH_BEFORE = r"said|asked|replied|whispered|shouted|muttered|answered|cried|called"
LOC_PREP = r"in|at|to|from|into|toward|towards|near|across|through|reached|left|visited|outside|inside"
GHOST = re.compile(r"\b(ghost|spirit|memory|memories|remembered|remember|dream|dreamed|dreamt|vision|"
                   r"flashback|haunt\w*|portrait|corpse|body|grave|funeral|buried|mourn\w*|before (?:he|she|they) "
                   r"died|statue|letter|diary|journal)\b", re.I)
FLASHBACK = re.compile(r"\b(flashback|years earlier|years before|years ago|months earlier|long ago|prologue|"
                       r"interlude|in (?:his|her|their) memory)\b", re.I)
FLASH_TITLE = re.compile(r"\b(flashback|prologue|interlude|memory|memories|before)\b", re.I)
FLASH_OPENING = 300           # characters at the start of a chapter that may announce a flashback
PAST_SENTENCE = re.compile(r"\b(born|ago|earlier|remembered|founded|built|since|last seen|died in|history|"
                           r"centuries|anniversary)\b", re.I)
NUMWORDS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve "
                                       "thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split())}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
NAME_RE = re.compile(r"\b[A-Z][a-z]+(?:['’][A-Za-z]+)?(?:[ \t]+[A-Z][a-z]+(?:['’][A-Za-z]+)?){0,2}\b")  # old, kept for callers
WORD_RE = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)?")
CONNECT = {"the", "of", "de", "da", "van", "von", "al", "le", "la", "du"}   # "Ångrim the Tall", "Anna de Vries"
EVENT_RE = re.compile(r"\b((?:[Ss]iege|[Bb]attle|[Ff]all|[Ss]ack|[Bb]urning|[Ff]ounding|[Ww]edding|[Cc]oronation|"
                      r"[Tt]reaty|[Ww]ar|[Ff]lood|[Ff]ire|[Rr]evolt|[Rr]ebellion)\s+of\s+(?:the\s+)?"
                      r"[^\W\d_][\w’']*(?:\s+[A-Z][\w’']*)*)[^.!?\n]{0,60}?\b(\d{3,4})\b")


def _is_cap(tok):
    return tok[:1].isupper() and (len(tok) == 1 or not tok[1:2].isupper())


def name_spans(text):
    """Capitalised runs ("Zoë Marchetti", "Ångrim the Tall") as (start, end, raw, sentence_initial). Letters from
    any alphabet; up to 3 words, joined by single spaces or by one small connector word."""
    toks = list(WORD_RE.finditer(text))
    i, out = 0, []
    while i < len(toks):
        t = toks[i]
        if not _is_cap(t.group(0)):
            i += 1
            continue
        j, words = i, 1
        while words < 3 and j + 1 < len(toks):
            gap = text[toks[j].end():toks[j + 1].start()]
            nxt = toks[j + 1].group(0)
            if gap.strip(" \t") == "" and gap and "\n" not in gap and _is_cap(nxt):
                j, words = j + 1, words + 1
            elif (gap == " " and nxt in CONNECT and j + 2 < len(toks) and _is_cap(toks[j + 2].group(0))
                  and text[toks[j + 1].end():toks[j + 2].start()] == " " and nxt == "the"):
                j, words = j + 2, words + 1
            else:
                break
        before = text[max(0, t.start() - 3):t.start()].rstrip(" \t\u201c\u2018\"'(\u2014-")
        initial = t.start() == 0 or not before or before[-1] in ".!?\n:\u2026" or \
            text[max(0, t.start() - 1):t.start()] in ("\u201c", "\u2018", '"', "\n")
        out.append((t.start(), toks[j].end(), text[t.start():toks[j].end()], initial))
        i = j + 1
    return out
AGE_NUM = r"(\d{1,3}|(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)(?:[- ](?:one|two|three|four|five|six|seven|eight|nine))?|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen)"


def parse_num(s):
    s = s.lower()
    if s.isdigit():
        return int(s)
    if s in NUMWORDS:
        return NUMWORDS[s]
    parts = re.split(r"[- ]", s)
    if parts[0] in TENS:
        return TENS[parts[0]] + (NUMWORDS.get(parts[1], 0) if len(parts) > 1 else 0)
    return None


def sentences(text):
    """[(start, end, sentence)] - simple splitter on . ! ? and line breaks."""
    out = []
    for m in re.finditer(r"[^.!?\n]+[.!?\"'”’]*", text):
        s = m.group(0).strip()
        if s:
            out.append((m.start(), m.end(), s))
    return out


class _Span:
    def __init__(self, start, end, raw):
        self._s, self._e, self._r = start, end, raw

    def start(self):
        return self._s

    def end(self):
        return self._e

    def group(self, _=0):
        return self._r


def _clean_name(raw):
    toks = re.split(r"[ \t]+", raw)
    titled = False
    while toks and (toks[0] in STOP or toks[0] in TITLES or (toks[0] in EPITHETS and len(toks) > 1)):
        titled = titled or toks[0] in TITLES
        toks = toks[1:]
    while toks and toks[-1] in STOP:
        toks = toks[:-1]
    toks = [re.sub(r"['’]s$", "", t) for t in toks]
    return " ".join(toks), titled


def _month(word):
    w = word.lower().rstrip(".")
    if w in MONTHS:
        return MONTHS[w]
    if w == "sept":
        return 9
    return None


def _dmy(m):
    mo = _month(m[2])
    return (int(m[3]), mo, int(m[1])) if mo else None


def _mdy(m):
    mo = _month(m[1])
    return (int(m[3]), mo, int(m[2])) if mo else None


DATE_PATTERNS = [
    (re.compile(r"\b(\d{3,4})-(\d{1,2})-(\d{1,2})\b"), lambda m: (int(m[1]), int(m[2]), int(m[3]))),
    (re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([A-Z][a-z]+\.?),?\s+(\d{1,4})\b"), _dmy),
    (re.compile(r"\b([A-Z][a-z]+\.?)\s+(\d{1,2})(?:st|nd|rd|th)?,\s*(\d{1,4})\b"), _mdy),
    (re.compile(r"\b(?:[Ii]n|[Yy]ear|[Ss]ince|[Bb]y|[Uu]ntil|of)\s+(?:the\s+year\s+)?(\d{3,4})\b(?!\s*[a-z%])"),
     lambda m: (int(m[1]), 0, 0)),
]


def find_dates(text):
    found = []
    taken = []
    for rx, conv in DATE_PATTERNS:
        for m in rx.finditer(text):
            if any(a <= m.start() < b for a, b in taken):
                continue
            try:
                d = conv(m)
            except (KeyError, ValueError, TypeError):
                d = None
            if not d or not d[0] or d[1] is None or not (0 <= d[1] <= 12) or not (0 <= d[2] <= 31):
                continue
            taken.append((m.start(), m.end()))
            found.append((m.start(), d))
    found.sort()
    return found


def fmt_date(d):
    y, mo, da = d
    if mo and da:
        return "%04d-%02d-%02d" % (y, mo, da)
    if mo:
        return "%04d-%02d" % (y, mo)
    return str(y)


def _sentence_at(sents, pos):
    for a, b, s in sents:
        if a <= pos < b:
            return s
    return ""


def _clip(s, n=160):
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def analyze(chapters):
    chapters = list(chapters)
    mentions = {}   # name -> count
    first = {}
    in_chapters = {}
    person = {}
    place = {}
    spans = [name_spans(text) for _, text in chapters]
    lower, mid = set(), {}
    for _, text in chapters:
        lower.update(w for w in WORD_RE.findall(text) if w[:1].islower())
    for sp in spans:
        for _, _, raw, initial in sp:
            if not initial:
                first_tok = raw.split()[0]
                mid[first_tok] = mid.get(first_tok, 0) + 1
    seen_mid = set()
    for sp in spans:
        for _, _, raw, initial in sp:
            if not initial:
                seen_mid.update(raw.split())

    def common_start(tok):
        """A sentence-initial word that is an ordinary word ("Beyond", "Tell"): it shows up in lower case
        somewhere else in the book (or it is a common opener: STOP)."""
        return tok in STOP or tok.lower() in lower

    for ci, (cname, text) in enumerate(chapters):
        for start, end, raw, initial in spans[ci]:
            toks = raw.split()
            if initial and common_start(toks[0]):
                if len(toks) == 1:
                    continue
                cut = len(toks[0]) + 1
                start, raw = start + cut, raw[cut:]
                if raw.startswith("the "):
                    continue
            m = _Span(start, end, raw)
            name, titled = _clean_name(m.group(0))
            if not name or name in STOP or len(name) < 2:
                continue
            mentions[name] = mentions.get(name, 0) + 1
            first.setdefault(name, cname)
            in_chapters.setdefault(name, [])
            if cname not in in_chapters[name]:
                in_chapters[name].append(cname)
            before = text[max(0, m.start() - 24):m.start()]
            after = text[m.end():m.end() + 24]
            if titled or re.match(r"^['’]s\b", after) or re.match(r"^\s+(?:%s)\b" % ACTION, after) \
                    or re.search(r"\b(?:%s)\s+$" % SPEECH_BEFORE, before) or re.match(r"^,\s+(?:aged\s+)?\d", after):
                person[name] = person.get(name, 0) + 1
            if re.search(r"\b(?:%s)\s+(?:the\s+)?$" % LOC_PREP, before, re.I) or name.split()[-1] in PLACE_WORDS \
                    or re.search(r"\b(?:village|town|city|port|isle|island|county|kingdom|valley|parish)\s+of\s+$",
                                 before, re.I):
                place[name] = place.get(name, 0) + 1
    characters, locations = [], []
    for name, n in mentions.items():
        p, l = person.get(name, 0), place.get(name, 0)
        entry = {"name": name, "mentions": n, "first": first[name], "chapters": in_chapters[name]}
        if name.split()[-1] in PLACE_WORDS or (l > p and l >= 1):
            locations.append(entry)
        elif p >= 1 or (n >= 3 and l == 0 and name.split()[0] in seen_mid):
            characters.append(entry)
    # Fold single-word names into the full names that start with them ("Anna" -> "Anna Kovac").
    full = {c["name"]: c for c in characters}
    for c in list(characters):
        if " " not in c["name"]:
            longer = [k for k in full if k.startswith(c["name"] + " ")]
            if len(longer) == 1:
                full[longer[0]]["mentions"] += c["mentions"]
                full[longer[0]].setdefault("aliases", []).append(c["name"])
                characters.remove(c)
    # A first name used on its own ("Wren") is the person behind the one full name that starts with it
    # ("Wren Halloway"), even when that full name appeared only once, without a verb after it.
    place_names = {l["name"] for l in locations}
    for c in list(characters):
        if " " in c["name"]:
            continue
        longer = [k for k in mentions if k.startswith(c["name"] + " ") and k not in place_names]
        if len(longer) == 1 and longer[0] not in full:
            fullname = longer[0]
            c.setdefault("aliases", []).append(c["name"])
            full.pop(c["name"], None)
            c["name"] = fullname
            c["mentions"] += mentions[fullname]
            for ch in in_chapters.get(fullname, []):
                if ch not in c["chapters"]:
                    c["chapters"].append(ch)
            full[fullname] = c
    characters.sort(key=lambda c: -c["mentions"])
    locations.sort(key=lambda c: -c["mentions"])
    char_names = [c["name"] for c in characters]
    alias = {}
    for c in characters:
        alias[c["name"]] = c["name"]
        for a in c.get("aliases", []):
            alias[a] = c["name"]
    anomalies, timeline = _timeline(chapters, alias)
    return {"characters": characters[:60], "locations": locations[:60], "timeline": timeline,
            "anomalies": anomalies, "character_names": char_names}


def _timeline(chapters, alias):
    anomalies, timeline = [], []
    names_rx = "|".join(re.escape(n) for n in sorted(alias, key=len, reverse=True)) or r"(?!x)x"
    N = r"(%s)" % names_rx
    death_rx = [
        re.compile(r"\b%s\s+(?:had\s+)?(?:died|was\s+killed|was\s+slain|was\s+murdered|was\s+executed|passed\s+away|"
                   r"perished|drowned|was\s+dead)\b" % N),
        re.compile(r"\b(?:killed|slew|murdered|executed|beheaded)\s+%s\b" % N),
        re.compile(r"\b(?:death|funeral|burial)\s+of\s+%s\b" % N),
        re.compile(r"\b%s['’]s\s+(?:funeral|corpse|burial)\b" % N),
    ]
    act_rx = re.compile(r"\b%s\s+(?:%s)\b|\b(?:%s)\s+%s\b" % (N, ACTION, SPEECH_BEFORE, N))
    age_rx = [
        (re.compile(r"\b%s,\s+(?:now\s+|aged\s+|just\s+)?%s(?:\s+years\s+old)?," % (N, AGE_NUM), re.I), 1, 2),
        (re.compile(r"\b%s\s+(?:was|is|had\s+turned|turned|was\s+now|is\s+now)\s+%s(?:\s+years?\s+old|(?=[.,;!?]))" % (N, AGE_NUM)), 1, 2),
        (re.compile(r"\b%s-year-old\s+%s\b" % (AGE_NUM, N)), 2, 1),
    ]
    born_rx = re.compile(r"\b%s\s+(?:was|had\s+been)\s+born\s+in\s+(?:the\s+year\s+)?(\d{3,4})\b" % N)
    deaths = {}       # canonical -> (chapter_index, date, sentence)
    ages = {}         # canonical -> [(chapter_index, chapter, age, date_or_None, sentence)]
    born = {}
    latest = None     # latest story date so far (non-flashback)
    latest_ch = None
    for ci, (cname, text) in enumerate(chapters):
        sents = sentences(text)
        flash = bool(FLASH_TITLE.search(cname) or FLASHBACK.search(text[:FLASH_OPENING]))
        dates = [(pos, d) for pos, d in find_dates(text) if not PAST_SENTENCE.search(_sentence_at(sents, pos))]
        if dates:
            timeline.append({"chapter": cname, "date": fmt_date(dates[0][1]), "flashback": flash})
            if not flash and latest is not None and dates[0][1] < latest:
                anomalies.append({"kind": "dates_out_of_order", "chapter": cname,
                                  "message": "Story date %s is earlier than %s, already reached in %s - is this a flashback?"
                                             % (fmt_date(dates[0][1]), fmt_date(latest), latest_ch),
                                  "excerpt": _clip(_sentence_at(sents, dates[0][0]))})
        events = []
        for rx in death_rx:
            for m in rx.finditer(text):
                events.append((m.start(), "death", alias[m.group(1)]))
        for m in act_rx.finditer(text):
            nm = m.group(1) or m.group(2)
            events.append((m.start(), "act", alias[nm]))
        for rx, ni, ai in age_rx:
            for m in rx.finditer(text):
                age = parse_num(m.group(ai))
                if age is not None and 0 <= age <= 150:
                    events.append((m.start(), "age", (alias[m.group(ni)], age)))
        for m in born_rx.finditer(text):
            events.append((m.start(), "born", (alias[m.group(1)], int(m.group(2)))))
        for pos, d in dates:
            events.append((pos, "date", d))
        events.sort(key=lambda e: (e[0], e[1] != "date"))
        cur = latest
        reported = set()
        for pos, kind, val in events:
            sent = _sentence_at(sents, pos)
            if kind == "date":
                if not flash and (cur is None or val >= cur):
                    cur = val
            elif kind == "death":
                deaths.setdefault(val, (ci, cur, sent))
            elif kind == "act":
                d = deaths.get(val)
                if d and (ci > d[0] or (ci == d[0] and sent != d[2])) and not flash and not GHOST.search(sent) \
                        and not PAST_SENTENCE.search(sent) \
                        and val not in reported:
                    reported.add(val)
                    anomalies.append({"kind": "appears_after_death", "chapter": cname,
                                      "message": "%s acts here, but died earlier (in %s)." % (val, chapters[d[0]][0]),
                                      "excerpt": _clip(sent)})
            elif kind == "born":
                born[val[0]] = val[1]
            elif kind == "age":
                who, age = val
                if PAST_SENTENCE.search(sent) or GHOST.search(sent):
                    continue                                  # a memory, not the story's present
                story = None if flash else cur
                n_before = len(anomalies)
                if who in born and story:
                    exp = story[0] - born[who]
                    if abs(exp - age) > 1:
                        anomalies.append({"kind": "age_inconsistency", "chapter": cname,
                                          "message": "%s is %d here, but born in %d makes them about %d in %d."
                                                     % (who, age, born[who], exp, story[0]), "excerpt": _clip(sent)})
                for pci, pch, page, pdate, _ in ages.get(who, []):
                    if flash:
                        break
                    if pdate and story:
                        exp = page + (story[0] - pdate[0])
                        if abs(exp - age) > 1:
                            anomalies.append({"kind": "age_inconsistency", "chapter": cname,
                                              "message": "%s is %d here (%s) but was %d in %s (%s): expected about %d."
                                                         % (who, age, fmt_date(story), page, pch, fmt_date(pdate), exp),
                                              "excerpt": _clip(sent)})
                            break
                    elif age < page:
                        anomalies.append({"kind": "age_inconsistency", "chapter": cname,
                                          "message": "%s is %d here but was already %d in %s." % (who, age, page, pch),
                                          "excerpt": _clip(sent)})
                        break
                    elif pci == ci and age != page and not story:
                        anomalies.append({"kind": "age_inconsistency", "chapter": cname,
                                          "message": "%s is given two ages in one chapter (%d and %d)." % (who, page, age),
                                          "excerpt": _clip(sent)})
                        break
                if not flash and len(anomalies) == n_before:  # a flagged statement is not a reference
                    ages.setdefault(who, []).append((ci, cname, age, story, sent))
        if not flash and cur is not None:
            if latest is None or cur > latest:
                latest, latest_ch = cur, cname
    events_seen = {}
    for cname, text in chapters:
        sents = sentences(text)
        for m in EVENT_RE.finditer(text):
            ev = re.sub(r"\s+", " ", m.group(1)).strip()
            key, year = ev.lower(), int(m.group(2))
            prev = events_seen.get(key)
            if prev is None:
                events_seen[key] = (year, cname)
            elif prev[0] != year:
                anomalies.append({"kind": "event_date_conflict", "chapter": cname,
                                  "message": "%s is dated %d here, but %d in %s." % (ev[0].upper() + ev[1:], year,
                                                                                    prev[0], prev[1]),
                                  "excerpt": _clip(_sentence_at(sents, m.start()))})
                events_seen[key] = (prev[0], prev[1])
    return anomalies, timeline


PREFERRED_MODELS = ("llama3.2", "llama3.1", "llama3", "qwen2.5", "qwen3", "mistral", "gemma3", "gemma2", "gemma",
                    "phi4", "phi3", "phi")
NO_MODEL = ("No local AI model is running. Install Ollama from ollama.com, run \"ollama pull llama3.2\" once, "
            "and continuity notes will work on this PC without the internet.")


def _local_opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never route localhost via a proxy


def pick_model(models, wanted=None):
    """models: [(name, size_bytes)]. The writer's choice if installed, else a general chat model, else the
    smallest non-embedding model."""
    names = [m[0] for m in models]
    if wanted:
        if wanted in names:
            return wanted
        same = [n for n in names if n.split(":")[0] == wanted.split(":")[0]]
        if same:
            return same[0]
    for pref in PREFERRED_MODELS:
        for n in names:
            if n.split(":")[0] == pref:
                return n
    cands = [m for m in models if not re.search(r"embed|coder|code", m[0], re.I)] or \
            [m for m in models if not re.search(r"embed", m[0], re.I)]
    return min(cands, key=lambda m: m[1] or 0)[0] if cands else None


def ai_status(host="127.0.0.1", port=11434, timeout=1.5):
    """Is a local Ollama answering, and which models does it have? Never raises."""
    try:
        with _local_opener().open("http://%s:%d/api/tags" % (host, port), timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        return {"running": False, "models": [], "default": None, "message": NO_MODEL}
    models = [(m.get("name"), m.get("size") or 0) for m in data.get("models", []) if m.get("name")]
    default = pick_model(models, os.environ.get("QUILL_OLLAMA_MODEL"))
    msg = "" if models else "Ollama is running but has no models yet. Run \"ollama pull llama3.2\" once."
    return {"running": True, "models": [m[0] for m in models], "default": default, "message": msg}


CONTINUITY_PROMPT = (
    "You are a careful continuity editor helping a novelist check their own manuscript. You never write, "
    "rewrite or continue story text, you never suggest sentences or changes of style, and you never praise. "
    "Using only the facts and the excerpt below, list up to 6 short questions about continuity: names, places, "
    "dates, ages, who knows what, and where people are. Start each item with \"- \" and name the chapter it "
    "concerns. If nothing looks inconsistent, answer with one line saying so.\n\n")


def _clean_notes(text, max_items=8, max_len=320):
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    items = []
    for ln in lines:
        ln = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "- ", ln)
        ln = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), ln)   # the panel shows plain text
        ln = re.sub(r"^#+\s*", "", ln)
        if len(ln) > max_len:
            ln = ln[: max_len - 1].rstrip() + "…"
        items.append(ln)
        if len(items) >= max_items:
            break
    return "\n".join(items)


def ai_continuity(lore, chapter_text=None, chapter_title=None, model=None, host="127.0.0.1", port=11434,
                  timeout=180.0):
    """Ask a LOCAL model for continuity questions about the writer's own lore index (and the open chapter).
    -> {"ok", "notes", "model", "seconds"} or {"ok": False, "error"}. The text never leaves this PC."""
    st = ai_status(host, port)
    if not st["running"] or not st["models"]:
        return {"ok": False, "error": st["message"] or NO_MODEL, "model": ""}
    model = model if model in st["models"] else st["default"]
    facts = {"characters": [{"name": c["name"], "first_chapter": c.get("first"), "mentions": c.get("mentions")}
                            for c in lore.get("characters", [])][:30],
             "places": [c["name"] for c in lore.get("locations", [])][:30],
             "dates": lore.get("timeline", [])[:30],
             "checks_already_found": [a.get("chapter", "") + ": " + a.get("message", "")
                                      for a in lore.get("anomalies", [])][:10]}
    prompt = CONTINUITY_PROMPT + "Facts: " + json.dumps(facts, ensure_ascii=False)
    if chapter_text:
        excerpt = chapter_text.strip()
        if len(excerpt) > 6000:
            excerpt = excerpt[:6000] + " [...]"
        prompt += "\n\nOpen chapter (%s):\n%s" % (chapter_title or "current", excerpt)
    body = json.dumps({"model": model, "prompt": prompt, "stream": False,
                       "options": {"temperature": 0.2, "num_predict": 450}}).encode("utf-8")
    req = urllib.request.Request("http://%s:%d/api/generate" % (host, port), data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with _local_opener().open(req, timeout=timeout) as r:
            out = json.loads(r.read().decode("utf-8")).get("response", "")
    except Exception as e:
        return {"ok": False, "model": model, "error": "The model %s did not answer (%s). Try again, or pick a "
                                                      "smaller model." % (model, type(e).__name__)}
    notes = _clean_notes(out)
    if not notes:
        return {"ok": False, "model": model, "error": "The model %s gave an empty answer." % model}
    return {"ok": True, "notes": notes, "model": model, "seconds": round(time.time() - t0, 1)}


def ai_summary(lore, model=None, timeout=1.5, host="127.0.0.1", port=11434):
    """Short AI lore summary from a LOCAL Ollama, or None (silently) when unavailable/slow."""
    model = model or os.environ.get("QUILL_OLLAMA_MODEL", "llama3.2")
    facts = {"characters": [c["name"] for c in lore.get("characters", [])][:25],
             "locations": [c["name"] for c in lore.get("locations", [])][:25],
             "timeline": lore.get("timeline", [])[:25],
             "warnings": [a["message"] for a in lore.get("anomalies", [])][:10]}
    prompt = ("You help a novelist keep their story bible. In at most 5 short sentences, summarise this lore "
              "index and point out the most important continuity risk. Facts: " + json.dumps(facts))
    body = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode("utf-8")
    req = urllib.request.Request("http://%s:%d/api/generate" % (host, port), data=body,
                                 headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never route localhost via a proxy
    try:
        with opener.open(req, timeout=timeout) as r:
            out = json.loads(r.read().decode("utf-8")).get("response", "")
            return out.strip() or None
    except Exception:
        return None
