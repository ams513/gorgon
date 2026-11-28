"""Stark Quill Voice and rhythm (standard library only, no network, nothing leaves the PC).

Two plain-rules readings of a manuscript, drawn as pictures:
  * rhythm - the sentence-length "heartbeat" of a chapter: one bar per sentence, runs of sentences that all sit in
             the same length band (a drone), the share of very short and very long sentences, paragraph lengths.
  * voices - for each character Lore found, the lines of dialogue that sit beside their name in a paragraph: how long
             their lines run, how often they ask or shout, how contracted they speak, their own favourite words, and
             pairs whose lines measure alike.

Both are HEURISTICS over counts. Speech is matched to a character only when exactly one character name stands in the
same paragraph outside the quotation marks; everything else is left unmatched and counted. They are questions for the
writer, never verdicts, and nothing here edits the manuscript.

What is read: comments ({>>a note<<}) are left out, as they are from word counts and exports. A sentence is not cut at
"Mr.", "Dr." and the like, at an initial or inside a number (3.5), and a speech tag stays with its line ("Stop!" she
said. is one sentence). Speech is read from double quotation marks, straight or curly; single-quoted dialogue is not.
"""
from __future__ import annotations

import math
import re

from quill_xray import QUOTE_RX, SENT_RX, STOP, WORD_RX, _name_rx

try:
    from quill_craft import SPEECH          # the dialogue-tag verbs Revise already knows
except Exception:                           # pragma: no cover - quill_craft ships beside this file
    SPEECH = frozenset("said asked replied answered whispered shouted yelled cried called murmured muttered "
                       "snapped added".split())

SHORT_MAX = 8          # words: a short sentence
LONG_MIN = 25          # words: a long sentence
RUN_MIN = 6            # sentences in a row in one band before it is called a drone
MAX_BARS = 600
MIN_LINES_SHOWN = 3
MIN_LINES_COMPARE = 8
MIN_LINES_WORDS = 8
BAND_NAMES = ("short", "middling", "long")
FIRST_PERSON = frozenset(("i", "i'm", "i'd", "i'll", "i've", "me", "my"))


COMMENT_RX = re.compile(r"\{>>.*?<<\}", re.S)
ABBREV_RX = re.compile(r"(?:^|[\s(\"“‘'])(?:Mr|Mrs|Ms|Mx|Dr|Prof|St|Sr|Jr|Capt|Sgt|Lt|Col|Gen|Rev|Hon|Mme|Mlle|vs"
                       r"|[A-HJ-Z])\.$")
CLOSED_QUOTE_RX = re.compile(r"[.!?…][\"”]\s*$")


def _words(s):
    return WORD_RX.findall(s or "")


def _clean(text):
    """The chapter as the reader gets it: notes to self ({>>...<<}) taken out."""
    text = text or ""
    return COMMENT_RX.sub(" ", text) if "{>>" in text else text


TAIL = 24          # characters of a sentence's end that decide whether the next piece belongs to it
TAG_LOOK = 160     # a speech tag is short: a longer piece is never one


def _tail(s):
    """The end of a sentence so far. A cut-off tail is marked so that its first character is not taken for the
    start of the sentence (keeps the whole check linear on a paragraph of any length)."""
    return s if len(s) <= TAIL else "\x00" + s[-TAIL:]


def _is_tag(frag):
    """True for a short speech tag that opens with a name: 'Anna said.', 'Old Mrs Lee asked quietly.'"""
    if len(frag) > TAG_LOOK:
        return False
    w = _words(frag)
    return 2 <= len(w) <= 6 and any(x.lower() in SPEECH for x in w[1:4])


def sentences(para):
    """One paragraph -> its sentences. SENT_RX cuts at every full stop; this puts back the cuts that are not ends of
    sentences: a title or initial (Mr. Lee, J. R. Hale), a decimal (3.5), a trailing-off that carries on in lower case
    (He left... and came back.) and a speech tag after a closed quotation."""
    out, tail = [], ""                      # each sentence as its pieces; tail = the last few characters of the newest
    for m in SENT_RX.finditer(para or ""):
        frag = m.group(0)
        if out:
            prev, nxt = tail.rstrip(), frag[:TAG_LOOK].lstrip()
            if (ABBREV_RX.search(prev)
                    or (prev[-1:] == "." and prev[-2:-1].isdigit() and frag[:1].isdigit())
                    or (prev.endswith(("...", "…")) and nxt[:1].islower())
                    or (CLOSED_QUOTE_RX.search(prev) and nxt and (nxt[0].islower() or _is_tag(frag)))):
                out[-1].append(frag)
                tail = _tail(tail + frag)
                continue
        out.append([frag])
        tail = _tail(frag)
    return [x for x in ("".join(pieces).strip() for pieces in out) if x]


def _band(n):
    return 0 if n <= SHORT_MAX else (2 if n >= LONG_MIN else 1)


def _stats(vals):
    if not vals:
        return 0.0, 0.0
    m = sum(vals) / len(vals)
    return m, math.sqrt(sum((v - m) ** 2 for v in vals) / len(vals))


# ======================================================================================================
# 1. Rhythm
# ======================================================================================================
def rhythm(text):
    """One chapter -> sentences (words each), paragraphs, mean, spread, variation, short/long share, runs and notes."""
    sents, src, paras = [], [], []
    for para in re.split(r"\n\s*\n|\n", _clean(text)):
        if not para.strip():
            continue
        pw = len(_words(para))
        if pw:
            paras.append(pw)
        for s in sentences(para):
            n = len(_words(s))
            if n:
                sents.append(n)
                src.append(s)
    mean, spread = _stats(sents)
    total = len(sents)
    short = sum(1 for n in sents if n <= SHORT_MAX)
    long_ = sum(1 for n in sents if n >= LONG_MIN)
    runs, i = [], 0
    while i < total:
        j = i
        while j + 1 < total and _band(sents[j + 1]) == _band(sents[i]):
            j += 1
        if j - i + 1 >= RUN_MIN:
            runs.append({"start": i + 1, "length": j - i + 1, "band": BAND_NAMES[_band(sents[i])],
                         "excerpt": src[i][:90]})
        i = j + 1
    variation = (spread / mean) if mean else 0.0
    notes = []
    if total >= 12:
        if variation < 0.35:
            notes.append("Sentence lengths are very even (variation %.2f); a mix of long and short usually reads more alive."
                         % variation)
        if runs:
            r = max(runs, key=lambda x: x["length"])
            notes.append("%d sentences in a row are all %s, starting at sentence %d." % (r["length"], r["band"], r["start"]))
        if long_ / total > 0.25:
            notes.append("%d%% of the sentences run 25 words or more." % round(100 * long_ / total))
        if short / total > 0.6:
            notes.append("%d%% of the sentences are 8 words or fewer; fine for tension, tiring as a habit."
                         % round(100 * short / total))
    return {"sentences": sents[:MAX_BARS], "sentence_count": total, "paragraphs": paras[:MAX_BARS],
            "mean": round(mean, 1), "spread": round(spread, 1), "variation": round(variation, 2),
            "short": round(short / total, 2) if total else 0.0, "long": round(long_ / total, 2) if total else 0.0,
            "longest": max(sents) if sents else 0, "runs": runs[:12], "notes": notes,
            "note": "Bars show sentences in order, taller is longer. A speech tag counts with its line, and comments "
                    "are left out. Counts only; a drone can be a choice."}


def book_rhythm(chapters):
    """chapters: [(title, text)] -> one summary row per chapter."""
    rows = []
    for title, text in chapters:
        r = rhythm(text)
        rows.append({"title": title, "sentences": r["sentence_count"], "mean": r["mean"], "variation": r["variation"],
                     "longest": r["longest"], "runs": len(r["runs"])})
    return rows


# ======================================================================================================
# 2. Voices
# ======================================================================================================
def _lines_of(paragraph):
    """-> (spoken lines, narration). Narration is the paragraph with the quoted speech taken out."""
    spoken = [m.group(0)[1:-1].strip() for m in QUOTE_RX.finditer(paragraph)]
    return [s for s in spoken if _words(s)], QUOTE_RX.sub(" ", paragraph)


def collect_lines(chapters, characters, max_chars=16):
    """-> ({name: [line]}, matched, unmatched). Matched only when exactly one character is named outside the quotes."""
    chars = [c for c in characters if c.get("name")][:max_chars]
    rx = [_name_rx(c) for c in chars]
    out = {c["name"]: [] for c in chars}
    matched = unmatched = 0
    for _, text in chapters:
        for para in re.split(r"\n\s*\n|\n", _clean(text)):
            spoken, narr = _lines_of(para)
            if not spoken:
                continue
            here = [i for i in range(len(chars)) if rx[i] and rx[i].search(narr)]
            if len(here) == 1:
                out[chars[here[0]]["name"]].extend(spoken)
                matched += len(spoken)
            else:
                unmatched += len(spoken)
    return out, matched, unmatched


def _norm(w):
    return w.lower().replace("’", "'")


def _profile(lines):
    words = [_norm(w) for ln in lines for w in _words(ln)]
    n = len(lines)
    nw = len(words) or 1
    ends = [ln.rstrip("\"”’' ") for ln in lines]
    return {"lines": n, "words": len(words), "avg_words": round(len(words) / n, 1) if n else 0.0,
            "questions": round(sum(1 for e in ends if e.endswith("?")) / n, 2) if n else 0.0,
            "exclaims": round(sum(1 for e in ends if e.endswith("!")) / n, 2) if n else 0.0,
            "contractions": round(sum(1 for w in words if "'" in w) / nw, 3),
            "first_person": round(sum(1 for w in words if w in FIRST_PERSON) / nw, 3)}


def voices(chapters, characters):
    lines, matched, unmatched = collect_lines(chapters, characters)
    allw = {}
    for ls in lines.values():
        for ln in ls:
            for w in _words(ln):
                w = _norm(w)
                allw[w] = allw.get(w, 0) + 1
    total_all = sum(allw.values()) or 1
    rows = []
    for name, ls in lines.items():
        if len(ls) < MIN_LINES_SHOWN:
            continue
        p = _profile(ls)
        p["name"] = name
        own = {}
        for ln in ls:
            for w in _words(ln):
                w = _norm(w)
                own[w] = own.get(w, 0) + 1
        fav = []
        if len(ls) >= MIN_LINES_WORDS:
            n_own = sum(own.values()) or 1
            for w, c in own.items():
                if c < 3 or len(w) < 3 or w in STOP or w in FIRST_PERSON:   # "I" and "me" have their own figure
                    continue
                base = (allw[w] - c + 0.5) / (total_all - n_own + 1)
                fav.append(((c / n_own) / base * math.log(1 + c), w, c))
            fav.sort(key=lambda t: (-t[0], t[1]))
        p["favourites"] = [{"word": w, "count": c} for _, w, c in fav[:5]]
        p["sample"] = max(ls, key=lambda s: min(len(s), 140))[:140]
        rows.append(p)
    rows.sort(key=lambda r: (-r["lines"], r["name"]))
    alike = []
    full = [r for r in rows if r["lines"] >= MIN_LINES_COMPARE]
    for ai, a in enumerate(full):
        for b in full[ai + 1:]:
            if (abs(a["avg_words"] - b["avg_words"]) <= 1.5 and abs(a["questions"] - b["questions"]) <= 0.08
                    and abs(a["exclaims"] - b["exclaims"]) <= 0.05
                    and abs(a["contractions"] - b["contractions"]) <= 0.03):
                alike.append([a["name"], b["name"]])
    notes = []
    if unmatched:
        notes.append("%d of %d spoken lines could not be tied to one character (none or several named beside the quote)."
                     % (unmatched, matched + unmatched))
    return {"characters": rows, "alike": alike[:8], "matched": matched, "unmatched": unmatched, "notes": notes,
            "note": "A line counts for a character when exactly one character name stands beside the quotation marks. "
                    "Speech is read from double quotation marks, straight or curly. "
                    "Measures are rough; pairs that read alike are a question to ask, not a fault."}
