"""Stark Quill Story X-ray (standard library only, no network, nothing leaves the PC).

Four plain-rules readings of a manuscript, shown as pictures:
  * mood_arc      - how warm or dark each stretch of the book reads and how tense its pace is, from a small word list,
                    sentence length and punctuation; plus the share of each chapter that is dialogue.
  * echoes        - the same distinctive word used again within a few lines, the repetition a tired eye misses.
  * character_web - which characters share scenes, how strongly, where each pair first meets, and strong characters
                    who never share one.
  * fuzzy_find    - search that survives typos, accents and word order, with a hit in a sentence, not a file.

Every reading is a HEURISTIC over word lists and counts. It does not understand the story; treat the pictures as a
second pair of eyes and the notes as questions, never as verdicts. Nothing here edits the manuscript.
"""
from __future__ import annotations

import math
import re
import unicodedata

WORD_RX = re.compile(r"[A-Za-zÀ-ɏ]+(?:['’][A-Za-z]+)?")
SENT_RX = re.compile(r"[^.!?…\n]+[.!?…]*[\"')\]”’]*")
QUOTE_RX = re.compile(r"“[^”]*”|\"[^\"\n]*\"")
WINDOW_WORDS = 250

POS = frozenset("""love loved loving joy joyful happy happily glad delight delighted laugh laughed laughing smile smiled
smiling warm warmth kind kindly gentle gently safe safety hope hoped hopeful peace peaceful calm bright beautiful lovely
sweet proud pride brave courage trust trusted friend friends friendship home welcome welcomed bless blessed light golden
gold sun sunlit sunny dawn tender embrace embraced cheer cheerful cheered laughter win won victory triumph free freedom
saved rescue rescued relief relieved wonder wonderful delightful gift treasure feast festival dance danced song sang
alive heal healed healing mend mended grace noble honest honour honor fair fortune lucky luck thrilled eager
comfort comforted cozy flourish bloom bloomed thrive""".split())
NEG = frozenset("""death dead die died dying kill killed killing murder murdered blood bloody bleeding wound wounded
pain painful hurt fear feared afraid terror terrified scream screamed screaming cry cried crying weep wept grief
sorrow sad sadly despair dark darkness shadow shadows cold fury furious rage angry anger hate hated hatred cruel
cruelty betray betrayed betrayal lie lied lies lost loss alone lonely empty ruin ruined destroy destroyed doom
doomed danger dangerous threat threatened trap trapped panic fled flee fleeing run ran chase chased attack attacked
fight fought battle war sword knife gun shot shoot scar scarred curse cursed poison poisoned drown drowned burn
burned burning fire flames storm thunder brutal bitter grim dread horror horrible nightmare monster demon
prison chains chained captive broken break broke shatter shattered collapse crash crashed
sick plague famine starving starve trembled trembling shaking shiver gasp gasped choke choked suffer suffered""".split())
NEGATORS = frozenset("not no never n't cannot without hardly barely neither nor".split())

STOP = frozenset("""about above after again against almost along also always among and another any anyone anything are around
because been before behind being below between both but came can could did does doing done down during each either else
even ever every few for from gave get gets give goes going gone got had has have having her here hers herself him himself
his how however into its itself just keep kept know known let like made make many may might more most much must myself
never next none nothing now off once only onto other others ought our ours ourselves out over own perhaps quite rather
said same saw say says see seem seemed seems shall she should since some someone something still such take taken than
that the their theirs them themselves then there these they thing things think this those though thought through thus
till too took toward towards under until upon very was way well went were what when where whether which while who whom
whose why will with within without would yet you your yours yourself yourselves""".split())


def fold(s):
    return unicodedata.normalize("NFD", str(s)).encode("ascii", "ignore").decode("ascii").lower()


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _tokens(text):
    return [(m.start(), m.end(), m.group(0)) for m in WORD_RX.finditer(text or "")]


# ======================================================================================================
# 1. Mood arc
# ======================================================================================================
def _valence(words):
    pos = neg = 0
    for i, w in enumerate(words):
        lw = w.lower().replace("’", "'")
        hit = 1 if lw in POS else -1 if lw in NEG else 0
        if not hit:
            continue
        if any(x in NEGATORS or x.endswith("n't") for x in (v.lower() for v in words[max(0, i - 3):i])):
            hit = -hit
        if hit > 0:
            pos += 1
        else:
            neg += 1
    return pos, neg


def _window_scores(text):
    """Split a chapter into windows of about 250 words (at most 12); -> [{'valence', 'tension'}] and the word count."""
    toks = _tokens(text)
    n = len(toks)
    if not n:
        return [], 0
    k = _clamp(int(round(n / float(WINDOW_WORDS))), 1, 12)
    out = []
    for i in range(k):
        a, b = i * n // k, (i + 1) * n // k
        seg = toks[a:b]
        if not seg:
            continue
        words = [t[2] for t in seg]
        pos, neg = _valence(words)
        v = _clamp((pos - neg) / math.sqrt(len(words)), -1.0, 1.0)
        chunk = text[seg[0][0]:seg[-1][1]]
        sents = [s for s in (m.group(0) for m in SENT_RX.finditer(chunk)) if WORD_RX.search(s)]
        ns = max(len(sents), 1)
        short = sum(1 for s in sents if len(WORD_RX.findall(s)) <= 5) / float(ns)
        punct = _clamp((chunk.count("!") + chunk.count("?")) / float(ns) * 2.0, 0.0, 1.0)
        dark = _clamp(neg / float(len(words)) * 12.0, 0.0, 1.0)
        out.append({"valence": round(v, 3), "tension": round(0.4 * short + 0.2 * punct + 0.4 * dark, 3)})
    return out, n


def dialogue_share(text):
    """Share of the words inside \"quotation marks\"."""
    total = len(_tokens(text))
    if not total:
        return 0.0
    quoted = sum(len(_tokens(m.group(0))) for m in QUOTE_RX.finditer(text))
    return round(quoted / float(total), 3)


def mood_arc(chapters):
    """chapters: [(title, text)] in reading order -> per-chapter figures, the whole-book curve and plain-words notes."""
    rows, curve = [], []
    for i, (title, text) in enumerate(chapters):
        wins, n = _window_scores(text)
        if not n:
            rows.append({"title": title, "words": 0, "valence": 0.0, "tension": 0.0, "dialogue": 0.0, "windows": []})
            continue
        v = round(sum(w["valence"] for w in wins) / len(wins), 3)
        t = round(sum(w["tension"] for w in wins) / len(wins), 3)
        rows.append({"title": title, "words": n, "valence": v, "tension": t, "dialogue": dialogue_share(text), "windows": wins})
        for j, w in enumerate(wins):
            curve.append({"chapter": i, "valence": w["valence"], "tension": w["tension"]})
    notes = []
    live = [(i, r) for i, r in enumerate(rows) if r["words"]]
    if len(live) >= 4:
        run = []
        for i, r in live + [(-1, {"tension": 9})]:
            if r["tension"] < 0.18 and r["words"]:
                run.append(i)
                continue
            if len(run) >= 3:
                notes.append("Chapters %d to %d all read at a low, even tension; check that the pace there is on purpose."
                             % (run[0] + 1, run[-1] + 1))
            run = []
        top = max(live, key=lambda x: x[1]["tension"])
        late = len(live) >= 5 and live.index(top) < len(live) * 0.5
        notes.append("The tensest chapter is %d (%s)%s." % (top[0] + 1, top[1]["title"],
                     ", in the first half of the book" if late else ""))
        lo = min(live, key=lambda x: x[1]["valence"])
        hi = max(live, key=lambda x: x[1]["valence"])
        if hi[1]["valence"] - lo[1]["valence"] >= 0.1:
            notes.append("The darkest-reading chapter is %d and the warmest is %d." % (lo[0] + 1, hi[0] + 1))
    total = sum(r["words"] for r in rows) or 1
    return {"chapters": rows, "curve": curve, "notes": notes,
            "dialogue": round(sum(r["dialogue"] * r["words"] for r in rows) / float(total), 3),
            "note": "Word lists and sentence counts, not understanding. Read the shape, not the numbers."}


# ======================================================================================================
# 2. Echoes
# ======================================================================================================
def echoes(text, window=40, limit=40):
    """Distinctive words (4+ letters, not names, not common words) repeated within `window` words of each other.
    -> [{'word', 'count', 'closest', 'spans': [[start, end], ...]}], tightest clusters first."""
    toks = _tokens(text)
    last, groups = {}, {}
    for idx, (s, e, w) in enumerate(toks):
        lw = w.lower()
        if len(lw) < 4 or lw in STOP or w[:1].isupper():
            continue
        if lw in last and idx - last[lw] <= window:
            g = groups.setdefault(lw, {"gaps": [], "idx": {last[lw]}})
            g["gaps"].append(idx - last[lw])
            g["idx"].add(idx)
        last[lw] = idx
    out = []
    for lw, g in groups.items():
        spans = [[toks[i][0], toks[i][1]] for i in sorted(g["idx"])]
        out.append({"word": lw, "count": len(spans), "closest": min(g["gaps"]), "spans": spans})
    out.sort(key=lambda x: (x["closest"], -x["count"], x["word"]))
    return out[:limit]


# ======================================================================================================
# 3. Character web
# ======================================================================================================
def _name_rx(c):
    names = [c["name"]] + [a for a in c.get("aliases", []) if a]
    names = sorted({n for n in names if len(n) >= 2}, key=len, reverse=True)
    return re.compile(r"(?<![\w])(?:%s)(?![\w])" % "|".join(re.escape(n) for n in names)) if names else None


def character_web(chapters, characters, max_nodes=14):
    """chapters: [(title, text)]; characters: Lore's list [{'name', 'aliases'?, 'mentions'?}].
    Two characters are linked when they appear in the same paragraph."""
    chars = [c for c in characters if c.get("name")][:max_nodes]
    rx = [_name_rx(c) for c in chars]
    n = len(chars)
    para = [0] * n
    link = {}
    first = {}
    for ci, (_, text) in enumerate(chapters):
        for p in re.split(r"\n\s*\n|\n", text or ""):
            if not p.strip():
                continue
            here = [i for i in range(n) if rx[i] and rx[i].search(p)]
            for i in here:
                para[i] += 1
            for a in range(len(here)):
                for b in range(a + 1, len(here)):
                    k = (here[a], here[b])
                    link[k] = link.get(k, 0) + 1
                    first.setdefault(k, ci)
    nodes = [{"name": chars[i]["name"], "paragraphs": para[i]} for i in range(n) if para[i]]
    keep = {i for i in range(n) if para[i]}
    edges = [{"a": chars[a]["name"], "b": chars[b]["name"], "weight": w, "first": first[(a, b)],
              "first_title": chapters[first[(a, b)]][0]} for (a, b), w in link.items() if a in keep and b in keep]
    edges.sort(key=lambda e: (-e["weight"], e["a"], e["b"]))
    top = sorted(keep, key=lambda i: -para[i])[:8]
    apart = [[chars[a]["name"], chars[b]["name"]] for ai, a in enumerate(top) for b in top[ai + 1:]
             if para[a] >= 3 and para[b] >= 3 and (min(a, b), max(a, b)) not in link]
    return {"nodes": nodes, "edges": edges, "never_together": apart[:10],
            "note": "Linked when two names share a paragraph. Pronouns are not followed, so quiet scenes can read as apart."}


# ======================================================================================================
# 4. Fuzzy find
# ======================================================================================================
def edit_distance(a, b, limit=2):
    """Optimal string alignment distance (insert, delete, replace, swap two letters); limit+1 when it is more than limit."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2, prev = None, list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > limit:
            return limit + 1
        prev2, prev = prev, cur
    return min(prev[-1], limit + 1)


def _allowed(n):
    return 0 if n <= 3 else 1 if n <= 7 else 2


def fuzzy_find(chapters, query, limit=40):
    """Sentences holding every word of the query, each allowed a typo (one letter off up to 7 letters, two beyond) or
    to be the start of a longer word. Accents and capitals are ignored; word order is not. -> hits, best first."""
    q = [fold(w) for w in WORD_RX.findall(query or "")]
    q = [w for w in q if len(w) >= 2]
    if not q or len(q) > 8:
        return []
    hits = []
    for ci, (title, text) in enumerate(chapters):
        for m in SENT_RX.finditer(text or ""):
            sent = m.group(0)
            toks = [(t.start(), t.end(), fold(t.group(0))) for t in WORD_RX.finditer(sent)]
            if len(toks) < len(q):
                continue
            cost, marks, ok = 0.0, [], True
            for w in q:
                best = None
                for s, e, t in toks:
                    d = 0.0 if t == w else (0.5 if len(w) >= 4 and t.startswith(w) else
                                            float(edit_distance(w, t, _allowed(len(w)))) if _allowed(len(w)) else 9.0)
                    if d <= _allowed(len(w)) and (best is None or d < best[0]):
                        best = (d, s, e)
                if best is None:
                    ok = False
                    break
                cost += best[0]
                marks.append([best[1], best[2]])
            if not ok:
                continue
            marks.sort()
            ordered = all(marks[i][0] < marks[i + 1][0] for i in range(len(marks) - 1)) and len(marks) > 1
            tight = (marks[-1][1] - marks[0][0]) if marks else 0
            score = 100.0 - 15.0 * cost + (6 if ordered else 0) - min(tight / 40.0, 10.0)
            lead = len(sent) - len(sent.lstrip())
            hits.append({"chapter": ci, "title": title, "score": round(score, 1), "text": sent.strip()[:300],
                         "marks": [[a - lead, b - lead] for a, b in marks if a - lead < 300], "at": m.start() + lead})
    hits.sort(key=lambda h: (-h["score"], h["chapter"], h["at"]))
    return hits[:limit]
