#!/usr/bin/env python3
"""Stark Covenant - Research Desk (research_desk.py). Standard library only, Python 3.10+.

WHAT IT IS
  A research assistant for clinicians, nurses, clinics, hospitals, triage teams and relief workers. It searches
  official, free, documented medical sources (research_sources.json), and turns what it finds into drafts that a
  clinician reads and judges.

WHAT IT NEVER DOES (enforced in code, repeated in the UI)
  * No diagnosis, no treatment decision, no triage of a patient. There is no function here that takes a patient's
    signs and returns a category, a dose or a plan. Triage mode DESCRIBES published systems and links to them.
  * It never changes care and never writes into a chart. It has no access to the record write API at all.
  * Everything it produces is labelled "DRAFT for clinician review - not a recommendation".
  * Every statement in a brief is either a citation list or a verbatim quotation of the cited source.
  * No patient data leaves this computer. Searches are built only from terms a clinician types. Those terms are
    scrubbed (emails, phone numbers, dates, ids, record numbers, postcodes, and the names / dates of birth / record
    numbers of this clinic's own patients). Then EVERY outgoing address passes OutboundGuard, which refuses to
    send it if anything identifying is left, and which only allows the hosts listed in research_sources.json.
    Trial Finder sends only condition + region: a patient's age and sex are compared on this computer, and only
    after consent (consent_center when installed, otherwise the clinician's explicit attestation).

FEATURES
  1. Living Evidence Briefs   PICO question -> PubMed (esearch / esummary / efetch) + Europe PMC -> de-duplicated,
                              ranked by study design (systematic review / meta-analysis, guideline, RCT, cohort /
                              observational, case series, other), abstracts, retraction flags ('Retracted
                              Publication'), recency, and a GRADE-lite certainty label with every step shown.
                              WHO IRIS guidance documents are listed beside it (not graded).
     Watch-lists              saved questions (+ drugs) refreshed daily: by the in-app scheduler while the app runs,
                              and by `python research_desk.py refresh-watchlists` (the autopilot hook). Alerts on new
                              high-quality evidence, new retractions and new FDA recalls.
  2. Trial Finder             ClinicalTrials.gov v2, open trials by condition / region, age and sex checked locally.
  3. Triage & Relief          START / JumpSTART / SALT / WHO ETAT described and linked (never applied by software);
                              WHO Disease Outbreak News, CDC travel notices and ReliefWeb reports for a configured
                              region; a printable field kit PDF (polyglot_engine) and an offline cache.
  4. Research Log             every fetch: query, source, URL (keys redacted), timestamp, HTTP status, SHA-256 of
                              the response. Hash-chained; response bodies are kept content-addressed so a brief can
                              be rebuilt and its result hash compared (replay).
  Drug reference              RxNorm names (candidates shown, never auto-picked), FDA label sections quoted, recalls,
                              FAERS reaction counts (not causation). The RxNav interaction API is retired (see
                              research_sources.json) - the desk says so and does not compute interactions.

STORAGE
  <data>/research (when the security vault is open: every document, log line and cached body is encrypted with it)
  or <data>/research_no_vault (DEMO / vault locked: plain files; only clinician search terms and public literature
  are kept there, never patient data).

MOUNTING IN clinic_app.py (owned by the core team; this file does not edit it). Three small pieces:

  1. At the end of CovenantApp.__init__ (next to `self.comms = make_comms(self)`):

        rd = optional("research_desk")
        self.research = None
        if rd is not None:
            try:
                self.research = rd.mount(self)      # also re-mounts itself when the vault opens later
            except Exception:
                self.clinic.health("research_desk could not start\\n" + traceback.format_exc())

  2. In CovenantHandler._dispatch, right after the /comms block and before `if not path.startswith("/api/")`:

        if path == "/research" or path.startswith("/research/"):
            research = getattr(self.app, "research", None)
            if research is None:
                raise HttpError(404, "Research Desk not installed.")
            if research.handle(self):
                return

  3. Optional: add "research_desk" to OPTIONAL_MODULES and a nav link to /research/ in the page.

  handle() writes its own responses. It checks the clinic session cookie (clinic.session), the role permission
  "clin.read" (clinician, nurse, owner), the CSRF token of that session on every POST, Origin and Content-Type.
  clinic_app's Host / DNS-rebinding and LAN checks run before it.

CLI
  python research_desk.py refresh-watchlists [--data DIR] [--force]   autopilot hook; prints a JSON summary
  python research_desk.py check-sources                              one live request per source (network)
  python research_desk.py record-fixtures OUT_DIR                    record test fixtures (network)
  python research_desk.py verify-log [--data DIR]
  python research_desk.py field-kit OUT.pdf [--data DIR] [--lang en]
  python research_desk.py mount-help

HONEST LIMITS
  * Study design comes from publication-type tags and title words; sample sizes and "direction" cues come from
    regular expressions over abstracts. GRADE-lite does not assess risk of bias, indirectness or publication bias;
    it is a reading aid, not a GRADE assessment.
  * The identifier scrubber is pattern based. It cannot recognise every possible name; it does block the names,
    dates of birth and record numbers of this clinic's own patients. Clinicians should still type research terms,
    not patient details.
  * Abstract text is the publisher's; the desk quotes it and never paraphrases it into advice.
  * ReliefWeb needs an approved appname (v1 is decommissioned). Europe PMC can be slow; the cache covers outages.
"""
from __future__ import annotations

import argparse
import base64
import datetime as _dt
import hashlib
import html
import importlib
import json
import os
import re
import secrets
import sys
import threading
import time
import traceback
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

VERSION = "1.0.0"
PREFIX = "/research"
LOG_PREFIX = "[ResearchDesk]"
SOURCES_PATH = os.path.join(HERE, "research_sources.json")
DEFAULT_TOOL = "StarkCovenantResearchDesk"
MAX_BODY_BYTES = 8 * 1024 * 1024
MAX_POST = 256 * 1024
WATCH_EVERY_S = 20 * 3600          # a watch-list is "due" after 20 h (daily refresh with slack)
SCHEDULER_TICK_S = 15 * 60
KEEP_BRIEFS = 50
KEEP_ALERTS = 500

DRAFT_LABEL = "DRAFT for clinician review - not a recommendation"
RULES = (
    "The Research Desk supports clinicians. It never diagnoses, never triages a patient and never changes care.",
    "Everything here is a draft for clinician judgement. Nothing from the desk enters a chart by itself.",
    "Every statement in a brief is a citation or a verbatim quotation of the cited source.",
    "No patient data leaves this computer: searches use only the terms you type, identifiers are stripped, and "
    "every outgoing address is checked before it is sent.",
    "The certainty label is a GRADE-lite heuristic computed from abstracts, not a GRADE assessment.",
)
TRIAGE_NOTICE = ("These are published triage systems, described and linked for training and reference. The "
                 "Research Desk never triages anyone: triage is done by trained people following their local "
                 "protocol.")
TRIALS_NOTICE = ("For discussion between clinician and patient. Eligibility is decided by each trial's team. Age and "
                 "sex were compared on this computer only; they were not sent to ClinicalTrials.gov.")
FAERS_NOTICE = ("FAERS counts are reports, not proof that the drug caused the reaction, and cannot be used to "
                "compare drugs or estimate risk. openFDA: 'Do not rely on openFDA to make decisions regarding "
                "medical care.'")
INTERACTION_NOTICE = ("Drug-drug interactions are not computed here. NLM retired the RxNav Drug Interaction API "
                      "(January 2024; a live check on 2026-11-19 returned HTTP 404). The 'Drug interactions' "
                      "section of the FDA label is quoted below when the label has one; use a maintained "
                      "interaction reference and a pharmacist.")
GRADE_NOT_ASSESSED = ("risk of bias", "indirectness", "publication bias", "effect size and confidence intervals")


def now_iso():
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def today():
    return _dt.datetime.now(_dt.timezone.utc).date().isoformat()


def sha256_hex(b):
    return hashlib.sha256(b).hexdigest()


def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def log(msg):
    try:
        sys.stderr.write("%s %s\n" % (LOG_PREFIX, msg))
    except Exception:
        pass


def optional(name):
    try:
        return importlib.import_module(name)
    except Exception:
        return None


class DeskError(Exception):
    def __init__(self, status, msg):
        Exception.__init__(self, msg)
        self.status, self.msg = status, msg


class PrivacyViolation(DeskError):
    def __init__(self, msg):
        DeskError.__init__(self, 422, msg)


class StorageUnavailable(DeskError):
    def __init__(self, msg):
        DeskError.__init__(self, 503, msg)


# ======================================================================================================
# 1. Privacy: identifier scrubber + outbound guard
# ======================================================================================================
_UNIT_AFTER = r"(?!\s*(?:mg|mcg|ug|µg|g|kg|iu|units?|ml|l|mmol|mol|cells|copies|years?|yrs?|days?)\b)"
_PATTERNS = (
    ("labelled_identifier", re.compile(
        r"\b(?:mrn|nhs\s*(?:no|number)|ssn|dob|d\.o\.b\.?|date\s+of\s+birth|patient\s*(?:id|no|number)|"
        r"medicare\s*(?:no|number)|medicaid\s*(?:no|number)|passport\s*(?:no|number)|record\s*(?:no|number)|"
        r"chart\s*(?:no|number))\b\s*[:#]?\s*(?:[\w./-]*\d[\w./-]*(?:\s+\d[\d-]*)*|\S+)?", re.I)),
    ("email", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    ("url", re.compile(r"\bhttps?://\S+|\bwww\.\S+", re.I)),
    ("record_id", re.compile(r"\b[A-Z][0-9a-f]{12}\b")),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("date", re.compile(r"\b(?:\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4})\b")),
    ("date", re.compile(r"\b\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?,?\s+\d{4}\b|"
                        r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}\b",
                        re.I)),
    ("phone", re.compile(r"(?<![\w])\+?\(?\d[\d ()\-.]{7,}\d")),
    ("record_number", re.compile(r"\b(?=[A-Za-z0-9-]*\d)[A-Za-z]{1,6}-?\d{4,}[A-Za-z0-9]*\b")),
    ("long_number", re.compile(r"\b\d{5,}\b" + _UNIT_AFTER, re.I)),
    ("postcode", re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b")),
)
_STRONG = ("labelled_identifier", "email", "record_id", "ssn")   # also checked on whole outbound URLs
_STREET = re.compile(r"\b(?:street|st|road|rd|avenue|ave|lane|ln|drive|dr|boulevard|blvd|way|close|court|ct|place|"
                     r"pl|square|sq|terrace|apartment|apt|suite|flat|floor|house|rue|strasse|stra\u00dfe|str|calle|"
                     r"avenida|via|chemin|po box|p\.o\. box)\b\.?", re.I)
_SAFE_CHARS = re.compile(r"[^\w\s\-',/.+]", re.UNICODE)


def _phone_ok(m):
    return sum(ch.isdigit() for ch in m.group(0)) >= 9


_FOLD_MAP = str.maketrans({"ø": "o", "Ø": "o", "æ": "ae", "Æ": "ae", "œ": "oe", "Œ": "oe",
                           "đ": "d", "Đ": "d", "ł": "l", "Ł": "l", "ı": "i", "ß": "ss",
                           "þ": "th", "Þ": "th", "ð": "d", "Ð": "d", "’": "'", "‘": "'",
                           "ʼ": "'", "‐": "-", "‑": "-", "–": "-", "—": "-"})
_WS = re.compile(r"[\s  -​  　﻿]+")
_TOKEN = re.compile(r"[^\W_]+(?:['-][^\W_]+)*", re.UNICODE)
_NAME_PARTICLES = {"de", "del", "la", "las", "los", "da", "das", "do", "dos", "di", "du", "van", "von", "der", "den",
                   "le", "el", "al", "bin", "ibn", "y", "e", "mc", "mac", "st", "ben", "abu", "bint"}
_RELATION_WORDS = {"mother", "father", "mum", "mom", "dad", "wife", "husband", "son", "daughter", "brother",
                   "sister", "partner", "friend", "aunt", "uncle", "grandmother", "grandfather", "guardian",
                   "carer", "caregiver", "neighbour", "neighbor", "spouse", "child", "parent", "synthetic"}


def _name_token_ok(tok):
    return (len(tok) >= 3 and tok not in _NAME_PARTICLES and tok not in _RELATION_WORDS
            and not any(c.isdigit() for c in tok))


_STREET_WORDS = {"street", "road", "avenue", "lane", "drive", "boulevard", "close", "court", "place", "square",
                 "terrace", "apartment", "suite", "flat", "floor", "house", "calle", "avenida", "carrera", "camino",
                 "plaza", "paseo", "rue", "chemin", "allee", "strasse", "gasse", "weg", "platz", "rua", "travessa",
                 "via", "viale", "piazza", "corso", "north", "south", "east", "west", "unit", "block", "building"}
# common given-name nicknames (both directions are checked); enough to catch the usual short forms
_NICKNAMES = {
    "william": ["bill", "billy", "will", "willy", "liam"], "robert": ["bob", "bobby", "rob", "robbie", "bert"],
    "richard": ["rick", "ricky", "dick", "rich"], "james": ["jim", "jimmy", "jamie"], "john": ["jack", "johnny"],
    "joseph": ["joe", "joey"], "thomas": ["tom", "tommy"], "michael": ["mike", "mikey", "mick"],
    "charles": ["charlie", "chuck"], "christopher": ["chris", "kit"], "daniel": ["dan", "danny"],
    "edward": ["ed", "eddie", "ted", "ned"], "anthony": ["tony"], "alexander": ["alex", "sasha", "xander"],
    "nicholas": ["nick", "nicky"], "benjamin": ["ben", "benny"], "samuel": ["sam", "sammy"],
    "matthew": ["matt"], "andrew": ["andy", "drew"], "jonathan": ["jon", "jonny"], "stephen": ["steve"],
    "steven": ["steve"], "timothy": ["tim", "timmy"], "patrick": ["pat", "paddy"], "peter": ["pete"],
    "elizabeth": ["liz", "lizzie", "beth", "betty", "eliza", "libby", "betsy"], "margaret": ["maggie", "meg", "peggy", "marge"],
    "katherine": ["kate", "katie", "kathy", "kat"], "catherine": ["cathy", "kate", "katie", "cat"],
    "jennifer": ["jen", "jenny"], "rebecca": ["becky", "becca"], "susan": ["sue", "susie"],
    "patricia": ["pat", "patty", "trish"], "deborah": ["deb", "debbie"], "victoria": ["vicky", "tori"],
    "alexandra": ["alex", "sasha", "lexi"], "abigail": ["abby"], "dorothy": ["dot", "dottie"],
    "jose": ["pepe", "chepe"], "francisco": ["paco", "pancho", "curro"], "ignacio": ["nacho"],
    "guadalupe": ["lupe", "lupita"], "dolores": ["lola"], "concepcion": ["concha", "conchita"],
    "manuel": ["manolo", "manu"], "antonio": ["toni", "tono"], "enrique": ["kike", "quique"],
    "eduardo": ["lalo"], "alejandro": ["alex", "ale"], "rosario": ["charo"], "mercedes": ["mercha", "meche"],
    "jesus": ["chucho", "chuy"], "isabel": ["isa", "chabela"], "giuseppe": ["beppe", "peppe"],
    "johannes": ["hans", "jan"], "mohammed": ["mo"], "muhammad": ["mo"], "aleksandr": ["sasha"],
}
_MONTHS = {
    1: ("january", "jan", "enero", "ene", "janvier", "janv", "januar", "janeiro", "gennaio", "genn"),
    2: ("february", "feb", "febrero", "fevrier", "février", "fevr", "févr", "februar", "fevereiro", "febbraio"),
    3: ("march", "mar", "marzo", "mars", "märz", "marz", "maerz", "marco", "março"),
    4: ("april", "apr", "abril", "abr", "avril", "avr", "aprile"),
    5: ("may", "mayo", "mai", "maio", "maggio", "mag"),
    6: ("june", "jun", "junio", "juin", "juni", "junho", "giugno", "giu"),
    7: ("july", "jul", "julio", "juillet", "juil", "juli", "julho", "luglio", "lug"),
    8: ("august", "aug", "agosto", "ago", "août", "aout"),
    9: ("september", "sep", "sept", "septiembre", "setiembre", "set", "septembre", "setembro", "settembre"),
    10: ("october", "oct", "octubre", "octobre", "oktober", "okt", "outubro", "out", "ottobre", "ott"),
    11: ("november", "nov", "noviembre", "novembre", "novembro"),
    12: ("december", "dec", "diciembre", "dic", "décembre", "decembre", "déc", "dezember", "dez",
         "dezembro", "dicembre"),
}
_DSEP = r"[\s./\-,]{0,3}"


def fold(text):
    """Accent-, case- and width-insensitive form used for every identifier comparison."""
    s = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", str(text or "")).translate(_FOLD_MAP))
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold()
    return _WS.sub(" ", s.translate(_FOLD_MAP)).strip()


def _near(a, b):
    """True when a and b differ by one edit (substitute, insert, delete or swap two neighbours)."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        return len(diff) == 1 or (len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]]
                                  and a[diff[1]] == b[diff[0]])
    if len(a) > len(b):
        a, b = b, a
    for i in range(len(b)):
        if b[:i] + b[i + 1:] == a:
            return True
    return False


def _sep_regex(chars, sep=r"[\s\-./_]?"):
    return sep.join(re.escape(c) for c in chars)


class Scrubber:
    """Removes identifiers from clinician-typed search text. `identifiers` is a callable returning, for this
    clinic's own patients (read locally, never sent):
        {"names": [...], "nicknames": [...], "dobs": [...], "mrns": [...], "phones": [...], "emails": [...],
         "addresses": [...], "postcodes": [...]}
    Every comparison is accent-, case-, width- and whitespace-insensitive ("Jose Pena" matches "José Peña").
    It fails closed: a word one typing error away from a patient's name, or a patient list that cannot be
    read, blocks the whole query (PrivacyViolation) instead of letting it through."""

    def __init__(self, identifiers=None):
        self.identifiers = identifiers
        self._cache = (0.0, None)

    def known(self):
        empty = {"names": set(), "joined": set(), "long_names": set(), "dobs": set(), "mrns": set(), "phones": set(),
                 "emails": set(), "addr": set(), "postcodes": set(), "error": False}
        if self.identifiers is None:
            return empty
        t, val = self._cache
        if val is not None and time.time() - t < 60:
            return val
        try:
            raw = self.identifiers() or {}
        except Exception:
            val = dict(empty, error=True)       # fail closed: nothing may be sent until the list can be read
            self._cache = (time.time(), val)
            return val
        names, joined = set(), set()
        for n in list(raw.get("names", ())) + list(raw.get("nicknames", ())):
            toks = [x for x in re.split(r"[^\w'-]+", fold(n)) if x]
            for tok in toks:
                for part in [tok] + re.split(r"['-]", tok):
                    part = part.strip("'-")
                    if _name_token_ok(part):
                        names.add(part)
                bare = re.sub(r"['-]", "", tok)
                if _name_token_ok(bare):
                    names.add(bare)
            full = [x for x in (re.sub(r"['-]", "", t) for t in toks) if x and x != "synthetic"]
            if len(full) >= 2:
                joined.add("".join(full))
        for n in list(names):
            for long_form, shorts in _NICKNAMES.items():
                if n == long_form:
                    names.update(s for s in shorts if len(s) >= 3)
                elif n in shorts:
                    names.add(long_form)
        emails, addr, postcodes = set(), set(), set()
        for e in raw.get("emails", ()):
            e = fold(e)
            if "@" in e:
                emails.add(e)
                local = re.sub(r"[^a-z0-9]", "", e.split("@")[0])
                if len(local) >= 4:
                    names.add(local)
        for a in raw.get("addresses", ()):
            for tok in re.split(r"[^\w'-]+", fold(a)):
                tok = tok.strip("'-")
                if len(tok) >= 4 and not tok.isdigit() and tok not in _STREET_WORDS and tok not in _NAME_PARTICLES:
                    addr.add(tok)
        for p in raw.get("postcodes", ()):
            p = re.sub(r"[^0-9a-z]", "", fold(p))
            if len(p) >= 3:
                postcodes.add(p)
        val = {"names": names, "joined": joined, "long_names": {n for n in names | addr if len(n) >= 6},
               "dobs": {str(d).strip() for d in raw.get("dobs", ()) if str(d).strip()},
               "mrns": {re.sub(r"[^0-9a-z]", "", fold(m)) for m in raw.get("mrns", ())
                        if len(re.sub(r"[^0-9a-z]", "", fold(m))) >= 3},
               "phones": {re.sub(r"\D", "", str(p)) for p in raw.get("phones", ()) if len(re.sub(r"\D", "", str(p))) >= 7},
               "emails": emails, "addr": addr, "postcodes": postcodes, "error": False}
        self._cache = (time.time(), val)
        return val

    def _known_patterns(self):
        """Structured identifiers (dates of birth, record numbers, phones, e-mails, postcodes) as regexes that run on
        the NFKC-normalised text, case-insensitively."""
        k = self.known()
        out = []
        for d in k["dobs"]:
            m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", d)
            if not m:
                out.append(("patient_dob", re.compile(r"(?<![\d])" + re.escape(d) + r"(?![\d])")))
                continue
            y, mo, da = (int(x) for x in m.groups())
            Y = r"(?:%04d|%02d)" % (y, y % 100)
            M, D = r"0?%d" % mo, r"0?%d" % da
            for order in ((Y, M, D), (D, M, Y), (M, D, Y)):
                out.append(("patient_dob", re.compile(r"(?<![\d])" + _DSEP.join(order) + r"(?![\d])")))
            mon = r"(?:%s)\.?" % "|".join(re.escape(x) for x in sorted(_MONTHS[mo], key=len, reverse=True))
            day = r"0?%d(?:st|nd|rd|th|er|o|º|\.)?" % da
            part = r"(?:\s*(?:de|of|del)\s*|[\s,./-]{0,3})"
            out.append(("patient_dob", re.compile(r"(?<![\w])" + day + part + mon + part + Y + r"(?![\d])", re.I)))
            out.append(("patient_dob", re.compile(r"(?<![\w])" + mon + part + day + part + Y + r"(?![\d])", re.I)))
        for m in k["mrns"]:
            out.append(("patient_record_number", re.compile(r"(?<![0-9a-z])" + _sep_regex(m) + r"(?![0-9a-z])", re.I)))
        for p in k["phones"]:
            tail = p[-7:]
            out.append(("patient_phone", re.compile(r"(?<![\d+])\+?(?:\d[\s().\-/]{0,3}){0,8}" +
                                                    _sep_regex(tail, r"[\s().\-/]{0,3}") + r"(?!\d)")))
        for e in k["emails"]:
            out.append(("patient_email", re.compile(re.escape(e), re.I)))
        for pc in k["postcodes"]:
            out.append(("patient_postcode", re.compile(r"(?<![0-9a-z])" + _sep_regex(pc, r"[\s-]?") + r"(?![0-9a-z])", re.I)))
        return out

    def _tokens(self, text, k):
        """-> (spans to remove [(start, end, kind)], uncertain) from a token pass over the text."""
        spans, uncertain = [], False
        toks = list(_TOKEN.finditer(text))
        folded = [fold(t.group(0)) for t in toks]
        for i, (t, f) in enumerate(zip(toks, folded)):
            bare = re.sub(r"['-]", "", f)
            parts = [p for p in re.split(r"['-]", f) if p]
            kind = None
            if bare in k["names"] or bare in k["joined"] or any(p in k["names"] for p in parts if len(p) >= 3):
                kind = "patient_name"
            elif bare in k["addr"] or any(p in k["addr"] for p in parts):
                kind = "patient_address"
            elif i + 1 < len(toks) and (bare + re.sub(r"['-]", "", folded[i + 1])) in k["joined"]:
                spans.append((toks[i + 1].start(), toks[i + 1].end(), "patient_name"))
                kind = "patient_name"
            if kind:
                spans.append((t.start(), t.end(), kind))
            elif len(bare) >= 6 and any(_near(bare, n) for n in k["long_names"]):
                uncertain = True
        return spans, uncertain

    def _analyse(self, text, strong_only=False):
        """-> (text with every identifier blanked, removed kinds in order, uncertain). Generic patterns first, then
        this clinic's own patients; each pass sees the text the previous pass left. strong_only keeps only the
        strong generic patterns (used on whole outbound URLs); patient identifiers are always checked."""
        text = _WS.sub(" ", unicodedata.normalize("NFKC", str(text or "")))
        k = self.known()
        removed = []

        def blank(t, s, e):
            return t[:s] + " " * (e - s) + t[e:]
        for kind, rx in _PATTERNS:
            if strong_only and kind not in _STRONG:
                continue
            for m in list(rx.finditer(text)):
                if kind == "phone" and not _phone_ok(m):
                    continue
                removed.append(kind)
                text = blank(text, m.start(), m.end())
        for kind, rx in self._known_patterns():
            for m in list(rx.finditer(text)):
                removed.append(kind)
                text = blank(text, m.start(), m.end())
        spans, uncertain = self._tokens(text, k)
        for s, e, kind in sorted(set(spans)):
            removed.append(kind)
            text = blank(text, s, e)
        return text, removed, uncertain or k.get("error", False)

    def find(self, text, strong_only=False):
        """-> list of identifier kinds found (never the values)."""
        _t, kinds, uncertain = self._analyse(text, strong_only)
        if uncertain:
            kinds.append("possible_patient_identifier")
        return kinds

    def scrub(self, text, kind="term", maxlen=200):
        """-> (clean text, removed kinds). kind='location' also drops any token with a digit (house numbers).
        Raises PrivacyViolation when it cannot be sure the text is free of this clinic's patient identifiers."""
        text, removed, uncertain = self._analyse(str(text or "")[:2000])
        if uncertain:
            if self.known().get("error"):
                raise PrivacyViolation("blocked: this clinic's patient list could not be read, so the search cannot "
                                       "be checked for patient identifiers")
            raise PrivacyViolation("blocked: a word looks like a misspelling of a patient's name. Remove it and try "
                                   "again")
        if kind == "location":   # region only: drop street-address segments and any token with a digit
            segs = []
            for seg in text.split(","):
                if _STREET.search(seg):
                    removed.append("street_address")
                    continue
                toks = seg.split()
                keep_t = [t for t in toks if not any(c.isdigit() for c in t)]
                if len(keep_t) != len(toks):
                    removed.append("street_number")
                if keep_t:
                    segs.append(" ".join(keep_t))
            text = ", ".join(segs)
        text = _SAFE_CHARS.sub(" ", text)
        text = re.sub(r"\s+", " ", text).strip(" ,/.-'")
        return text[:maxlen], removed


class OutboundGuard:
    """Last check before any byte leaves the machine. Refuses non-https, hosts outside the allow-list, user terms
    that still contain an identifier, and URLs that contain a strong identifier or a known patient identifier."""

    EXEMPT = ("email", "api_key", "tool", "appname")

    def __init__(self, scrubber, allowed_hosts, contact_email=lambda: ""):
        self.scrubber = scrubber
        self.allowed_hosts = set(allowed_hosts)
        self.contact_email = contact_email

    def check(self, url, user_terms=()):
        u = urllib.parse.urlsplit(url)
        if u.scheme != "https":
            raise PrivacyViolation("only https requests are allowed")
        if (u.hostname or "") not in self.allowed_hosts:
            raise PrivacyViolation("host %s is not in research_sources.json" % (u.hostname or "?"))
        if u.username or u.password or u.fragment:
            raise PrivacyViolation("credentials or fragments are not allowed in outbound URLs")
        for t in user_terms:
            kinds = self.scrubber.find(t)
            if kinds:
                raise PrivacyViolation("blocked before sending: a search term still contains %s" % kinds[0])
        parts = [urllib.parse.unquote_plus(u.path)]
        for k, v in urllib.parse.parse_qsl(u.query, keep_blank_values=True):
            if k in self.EXEMPT:
                if k == "email" and v and v != (self.contact_email() or ""):
                    raise PrivacyViolation("email parameter is not the configured contact email")
                continue
            parts.append(k + " " + v)
        kinds = self.scrubber.find(" ".join(parts), strong_only=True)
        if kinds:
            raise PrivacyViolation("blocked before sending: the request contained %s" % kinds[0])
        return True


# ======================================================================================================
# 2. Source catalogue
# ======================================================================================================
def load_sources(path=None):
    with open(path or SOURCES_PATH, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data.get("sources"), dict):
        raise ValueError("research_sources.json: 'sources' missing")
    return data


def allowed_hosts(catalogue):
    out = set()
    for s in catalogue["sources"].values():
        if s.get("enabled", True):
            out |= set(s.get("hosts", ()))
    return out


# ======================================================================================================
# 3. Storage (vault-encrypted when the vault is open)
# ======================================================================================================
class DeskStore:
    """JSON documents, content-addressed blobs and the log, under one folder. With a vault (clinic_app's
    VaultAdapter or security_vault.Vault) everything is encrypted; the document name is bound inside the plaintext
    header so a blob cannot be swapped for another document."""

    def __init__(self, root, vault=None):
        self.root = os.path.abspath(root)
        os.makedirs(os.path.join(self.root, "blobs"), exist_ok=True)
        self.vault = vault
        self._lock = threading.RLock()

    @property
    def encrypted(self):
        if self.vault is None:
            return False
        try:
            return bool(getattr(self.vault, "unlocked", True))
        except Exception:
            return False

    def seal(self, name, raw):
        if not self.encrypted:
            return raw
        return bytes(self.vault.encrypt(b"GCR1:" + name.encode() + b"\n" + raw))

    def unseal(self, name, blob):
        pt = bytes(self.vault.decrypt(blob))
        head = b"GCR1:" + name.encode() + b"\n"
        if not pt.startswith(head):
            raise StorageUnavailable("%s: encrypted file belongs to another document" % name)
        return pt[len(head):]

    @staticmethod
    def _atomic(path, data):
        tmp = "%s.%d.%d.tmp" % (path, os.getpid(), threading.get_ident())
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def _doc_paths(self, name):
        if not re.match(r"^[a-z_]{1,40}$", name):
            raise ValueError("bad document name")
        base = os.path.join(self.root, name + ".json")
        return base + ".gcv", base

    def load(self, name, default):
        pe, pp = self._doc_paths(name)
        with self._lock:
            if os.path.exists(pe):
                if not self.encrypted:
                    raise StorageUnavailable("%s is encrypted and the security vault is locked" % name)
                with open(pe, "rb") as f:
                    return json.loads(self.unseal(name, f.read()).decode("utf-8"))
            if os.path.exists(pp):
                with open(pp, encoding="utf-8") as f:
                    return json.load(f)
        return default

    def save(self, name, obj):
        pe, pp = self._doc_paths(name)
        raw = json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")
        with self._lock:
            if self.encrypted:
                self._atomic(pe, self.seal(name, raw))
                if os.path.exists(pp):
                    os.remove(pp)
            else:
                if os.path.exists(pe):
                    raise StorageUnavailable("%s is encrypted and the security vault is locked" % name)
                self._atomic(pp, raw)

    def put_blob(self, data):
        h = sha256_hex(data)
        p = os.path.join(self.root, "blobs", h + (".gcv" if self.encrypted else ".bin"))
        if not os.path.exists(p):
            self._atomic(p, self.seal("blob_" + h, data))
        return h

    def get_blob(self, h):
        if not re.match(r"^[0-9a-f]{64}$", h or ""):
            return None
        pe = os.path.join(self.root, "blobs", h + ".gcv")
        pp = os.path.join(self.root, "blobs", h + ".bin")
        if os.path.exists(pe) and self.encrypted:
            with open(pe, "rb") as f:
                data = self.unseal("blob_" + h, f.read())
        elif os.path.exists(pp):
            with open(pp, "rb") as f:
                data = f.read()
        else:
            return None
        return data if sha256_hex(data) == h else None

    def has_encrypted_files(self):
        try:
            return any(n.endswith(".gcv") for n in os.listdir(self.root))
        except OSError:
            return False


# ======================================================================================================
# 4. Research Log (hash-chained, reproducible)
# ======================================================================================================
GENESIS = "0" * 64


class ResearchLog:
    """research_log.jsonl: one line per desk run. Plain fields: seq, at, kind, prev, hash. The body (query,
    sources, every request with its URL, status, timestamp and SHA-256) is encrypted when the vault is open."""

    def __init__(self, store):
        self.store = store
        self.path = os.path.join(store.root, "research_log.jsonl")
        self._lock = threading.Lock()

    def _lines(self):
        if not os.path.exists(self.path):
            return []
        with open(self.path, "rb") as f:
            return [ln for ln in f.read().split(b"\n") if ln.strip()]

    def append(self, kind, body):
        with self._lock:
            lines = self._lines()
            prev, seq = GENESIS, 0
            if lines:
                last = json.loads(lines[-1].decode("utf-8"))
                prev, seq = last["hash"], last["seq"]
            entry = {"seq": seq + 1, "at": now_iso(), "kind": kind, "prev": prev}
            raw = canon(body)
            if self.store.encrypted:
                entry["enc"] = base64.b64encode(self.store.seal("log", raw)).decode("ascii")
            else:
                entry["body"] = body
            entry["hash"] = sha256_hex(canon(entry))
            with open(self.path, "ab") as f:
                f.write(canon(entry) + b"\n")
                f.flush()
                os.fsync(f.fileno())
            return entry["seq"]

    def _body(self, e):
        if "enc" in e:
            if not self.store.encrypted:
                raise StorageUnavailable("the research log is encrypted and the security vault is locked")
            return json.loads(self.store.unseal("log", base64.b64decode(e["enc"])).decode("utf-8"))
        return e.get("body")

    def entries(self, limit=100, offset=0, kind=None):
        out = []
        for ln in reversed(self._lines()):
            try:
                e = json.loads(ln.decode("utf-8"))
            except ValueError:
                continue
            if kind and e.get("kind") != kind:
                continue
            out.append(e)
        out = out[offset:offset + limit]
        res = []
        for e in out:
            item = {k: e[k] for k in ("seq", "at", "kind", "hash")}
            try:
                item["body"] = self._body(e)
            except Exception as ex:
                item["body"] = {"error": str(ex)}
            res.append(item)
        return res

    def find(self, run_id):
        for ln in reversed(self._lines()):
            try:
                e = json.loads(ln.decode("utf-8"))
            except ValueError:
                continue
            b = self._body(e) if ("enc" in e or "body" in e) else None
            if b and b.get("run_id") == run_id:
                return e, b
        return None, None

    def verify(self):
        prev, problems, n = GENESIS, [], 0
        for i, ln in enumerate(self._lines(), 1):
            try:
                e = json.loads(ln.decode("utf-8"))
            except ValueError:
                problems.append("line %d: not JSON" % i)
                prev = None
                continue
            n += 1
            h = e.pop("hash", None)
            if sha256_hex(canon(e)) != h:
                problems.append("line %d: hash mismatch (edited)" % i)
            if prev is not None and e.get("prev") != prev:
                problems.append("line %d: chain broken (inserted, deleted or reordered)" % i)
            if e.get("seq") != i:
                problems.append("line %d: sequence %s" % (i, e.get("seq")))
            prev = h
        return {"ok": not problems, "entries": n, "problems": problems}


# ======================================================================================================
# 5. Transport, rate limits, cache
# ======================================================================================================
class RateLimiter:
    def __init__(self, clock=time.monotonic, sleep=time.sleep):
        self.clock, self.sleep = clock, sleep
        self._next = {}
        self._lock = threading.Lock()

    def wait(self, host, interval):
        with self._lock:
            now = self.clock()
            at = max(now, self._next.get(host, 0.0))
            self._next[host] = at + interval
        if at > now:
            self.sleep(at - now)
        return at - now


class HttpTransport:
    """urllib over https. Returns (status, content_type, body). Reads at most MAX_BODY_BYTES."""

    def get(self, url, headers, timeout=30):
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read(MAX_BODY_BYTES + 1)
                if len(body) > MAX_BODY_BYTES:
                    raise DeskError(502, "response too large")
                return r.status, r.headers.get("Content-Type") or "", body
        except urllib.error.HTTPError as e:
            try:
                body = e.read(65536)
            except Exception:
                body = b""
            return e.code, (e.headers.get("Content-Type") if e.headers else "") or "", body


class Fetched:
    __slots__ = ("name", "source", "url", "status", "body", "sha256", "fetched_at", "from_cache", "stale", "error")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    @property
    def ok(self):
        return self.status == 200 and self.body is not None


def redact_url(url):
    u = urllib.parse.urlsplit(url)
    q = []
    for k, v in urllib.parse.parse_qsl(u.query, keep_blank_values=True):
        if k == "api_key":
            v = "REDACTED"
        elif k == "email":
            v = "CONTACT"
        q.append((k, v))
    return urllib.parse.urlunsplit((u.scheme, u.netloc, u.path, urllib.parse.urlencode(q), ""))


def cache_key(url):
    return sha256_hex(redact_url(url).encode("utf-8"))


class Run:
    """Collects every request of one desk action for the Research Log."""

    def __init__(self, desk, kind, actor, query):
        self.desk, self.kind, self.actor, self.query = desk, kind, actor, query
        self.run_id = "R" + secrets.token_hex(8)
        self.started = now_iso()
        self.requests = []
        self.responses = {}
        self.meta = {}

    def fetch(self, name, source, url, user_terms=(), accept="application/json"):
        f = self.desk.fetcher.get(source, url, user_terms=user_terms, accept=accept)
        f.name = name
        self.requests.append({"name": name, "source": source, "url": redact_url(url), "status": f.status,
                              "sha256": f.sha256, "fetched_at": f.fetched_at, "from_cache": bool(f.from_cache),
                              "stale": bool(f.stale), "error": f.error})
        if f.ok:
            self.responses[name] = f.sha256
        return f

    def finish(self, result=None, extra=None):
        body = {"run_id": self.run_id, "kind": self.kind, "actor": self.actor, "started": self.started,
                "finished": now_iso(), "query": self.query, "requests": self.requests,
                "sources": sorted({r["source"] for r in self.requests}), "responses": self.responses,
                "meta": self.meta}
        if result is not None:
            body["result_sha256"] = result_hash(result)
        body.update(extra or {})
        self.desk.log.append(self.kind, body)
        return self.run_id


def result_hash(result):
    r = dict(result)
    for k in ("generated_at", "run_id"):
        r.pop(k, None)
    return sha256_hex(canon(r))


class Fetcher:
    def __init__(self, store, guard, catalogue, settings, transport=None, limiter=None, sleep=time.sleep):
        self.store, self.guard, self.catalogue, self.settings = store, guard, catalogue, settings
        self.transport = transport or HttpTransport()
        self.limiter = limiter or RateLimiter()
        self.sleep = sleep
        self._idx_lock = threading.Lock()

    def _interval(self, source):
        s = self.catalogue["sources"].get(source, {})
        if source == "pubmed" and self.settings().get("ncbi_api_key"):
            return float(s.get("min_interval_with_key_s", 0.12))
        return float(s.get("min_interval_s", 1.0))

    def user_agent(self):
        st = self.settings()
        ua = "%s/%s (Stark Covenant; Python urllib" % (st.get("tool_name") or DEFAULT_TOOL, VERSION)
        if st.get("contact_email"):
            ua += "; mailto:" + st["contact_email"]
        return ua + ")"

    def _index(self):
        return self.store.load("cache_index", {"entries": {}})

    def cached(self, url):
        ent = self._index()["entries"].get(cache_key(url))
        if not ent:
            return None, None
        body = self.store.get_blob(ent["sha256"])
        return (ent, body) if body is not None else (None, None)

    def get(self, source, url, user_terms=(), accept="application/json"):
        st = self.settings()
        self.guard.check(url, user_terms)
        ttl = float(st.get("cache_ttl_hours", 24)) * 3600
        ent, body = self.cached(url)
        if ent is not None:
            age = time.time() - ent.get("t", 0)
            if st.get("offline") or age < ttl:
                return Fetched(source=source, url=url, status=200, body=body, sha256=ent["sha256"],
                               fetched_at=ent["fetched_at"], from_cache=True, stale=age >= ttl)
        if st.get("offline"):
            return Fetched(source=source, url=url, status=None, body=None, fetched_at=now_iso(),
                           error="offline mode: not in the offline cache")
        host = urllib.parse.urlsplit(url).hostname
        headers = {"User-Agent": self.user_agent(), "Accept": accept}
        status, err, data = None, None, None
        for attempt in range(2):
            self.limiter.wait(host, self._interval(source))
            try:
                status, _ctype, data = self.transport.get(url, headers, timeout=30)
                err = None
            except DeskError:
                raise
            except Exception as e:
                status, data, err = None, None, "%s: %s" % (type(e).__name__, str(e)[:200])
            if status == 200 or (status is not None and status < 500 and status != 429):
                break
            if attempt == 0:
                self.sleep(1.5)
        fetched_at = now_iso()
        if status == 200 and data is not None:
            h = self.store.put_blob(data)
            with self._idx_lock:
                idx = self._index()
                idx["entries"][cache_key(url)] = {"sha256": h, "fetched_at": fetched_at, "t": time.time(),
                                                  "source": source}
                if len(idx["entries"]) > 5000:
                    for k in sorted(idx["entries"], key=lambda k: idx["entries"][k]["t"])[:1000]:
                        del idx["entries"][k]
                self.store.save("cache_index", idx)
            return Fetched(source=source, url=url, status=200, body=data, sha256=h, fetched_at=fetched_at,
                           from_cache=False, stale=False)
        if ent is not None:   # network trouble: serve the stale cached copy, marked as stale
            return Fetched(source=source, url=url, status=200, body=body, sha256=ent["sha256"],
                           fetched_at=ent["fetched_at"], from_cache=True, stale=True,
                           error="live request failed (%s); cached copy used" % (err or status))
        snippet = ""
        if data:
            snippet = data[:300].decode("utf-8", "replace")
        return Fetched(source=source, url=url, status=status, body=None, sha256=sha256_hex(data) if data else None,
                       fetched_at=fetched_at, from_cache=False, error=err or ("HTTP %s %s" % (status, snippet)))


# ======================================================================================================
# 6. Source URL builders and parsers
# ======================================================================================================
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
CTGOV = "https://clinicaltrials.gov/api/v2/studies"
WHO_DON = "https://www.who.int/api/news/diseaseoutbreaknews"
WHO_DON_ITEM = "https://www.who.int/emergencies/disease-outbreak-news/item/"
IRIS = "https://iris.who.int/server/api/discover/search/objects"
CDC_RSS = "https://wwwnc.cdc.gov/travel/rss/notices.xml"
OPENFDA = "https://api.fda.gov/drug/"
RXNAV = "https://rxnav.nlm.nih.gov/REST/"
RELIEFWEB = "https://api.reliefweb.int/v2/reports"

HQ_FILTER = ("(systematic[sb] OR meta-analysis[pt] OR randomized controlled trial[pt] OR practice guideline[pt] OR "
             "guideline[pt])")


def _q(params):
    return urllib.parse.urlencode(params, quote_via=urllib.parse.quote)


def eutils_url(tool, settings, params):
    p = dict(params)
    p["tool"] = settings.get("tool_name") or DEFAULT_TOOL
    if settings.get("contact_email"):
        p["email"] = settings["contact_email"]
    if settings.get("ncbi_api_key"):
        p["api_key"] = settings["ncbi_api_key"]
    return EUTILS + tool + "?" + _q(p)


def pubmed_term(q, drop=()):
    parts = [q[k] for k in ("p", "i", "c", "o") if q.get(k) and k not in drop]
    return " AND ".join("(%s)" % x for x in parts)


def parse_esearch(body):
    d = json.loads(body.decode("utf-8"))
    r = d.get("esearchresult", {})
    return {"count": int(r.get("count") or 0), "ids": [str(i) for i in r.get("idlist", []) if str(i).isdigit()],
            "translation": r.get("querytranslation", "")}


def parse_esummary(body):
    d = json.loads(body.decode("utf-8")).get("result", {})
    out = {}
    for uid in d.get("uids", []):
        r = d.get(uid) or {}
        doi = ""
        for a in r.get("articleids", []):
            if a.get("idtype") == "doi":
                doi = a.get("value", "")
        year = None
        m = re.match(r"(\d{4})", r.get("sortpubdate") or r.get("pubdate") or "")
        if m:
            year = int(m.group(1))
        out[str(uid)] = {"pmid": str(uid), "title": r.get("title", ""), "journal": r.get("fulljournalname") or
                         r.get("source", ""), "year": year, "pubtypes": list(r.get("pubtype", [])),
                         "authors": [a.get("name", "") for a in r.get("authors", []) if a.get("authtype") == "Author"],
                         "doi": doi}
    return out


def _text(el):
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip() if el is not None else ""


def parse_efetch(body):
    root = ET.fromstring(body)
    out = {}
    for art in root.iter("PubmedArticle"):
        mc = art.find("MedlineCitation")
        if mc is None:
            continue
        pmid = _text(mc.find("PMID"))
        a = mc.find("Article")
        if a is None or not pmid:
            continue
        sections = []
        for t in a.findall("Abstract/AbstractText"):
            sections.append({"label": (t.get("Label") or "").strip(), "text": _text(t)})
        year = None
        y = a.find("Journal/JournalIssue/PubDate/Year")
        if y is not None and (y.text or "").strip().isdigit():
            year = int(y.text.strip())
        else:
            md = _text(a.find("Journal/JournalIssue/PubDate/MedlineDate"))
            m = re.match(r"(\d{4})", md)
            year = int(m.group(1)) if m else None
        doi = ""
        for e in a.findall("ELocationID"):
            if e.get("EIdType") == "doi":
                doi = _text(e)
        if not doi:
            for e in art.findall("PubmedData/ArticleIdList/ArticleId"):
                if e.get("IdType") == "doi":
                    doi = _text(e)
        authors = []
        for au in a.findall("AuthorList/Author"):
            ln, ini = _text(au.find("LastName")), _text(au.find("Initials"))
            if ln:
                authors.append((ln + " " + ini).strip())
            elif au.find("CollectiveName") is not None:
                authors.append(_text(au.find("CollectiveName")))
        retraction_in = []
        for cc in mc.findall("CommentsCorrectionsList/CommentsCorrections"):
            if cc.get("RefType") == "RetractionIn":
                retraction_in.append({"ref": _text(cc.find("RefSource")), "pmid": _text(cc.find("PMID"))})
        out[pmid] = {"pmid": pmid, "title": _text(a.find("ArticleTitle")), "journal": _text(a.find("Journal/Title")),
                     "year": year, "doi": doi, "authors": authors, "abstract_sections": sections,
                     "pubtypes": [_text(p) for p in a.findall("PublicationTypeList/PublicationType")],
                     "mesh": [_text(m.find("DescriptorName")) for m in mc.findall("MeshHeadingList/MeshHeading")],
                     "retraction_in": retraction_in}
    return out


def epmc_query(q, years=None):
    parts = [q[k] for k in ("p", "i", "c", "o") if q.get(k)]
    s = " AND ".join("(%s)" % x for x in parts)
    if years:
        s += " AND PUB_YEAR:[%d TO %d]" % (years[0], years[1])
    return s


def epmc_url(query, page_size=15):
    return EPMC + "?" + _q({"query": query, "format": "json", "resultType": "core", "pageSize": page_size})


def _strip_tags(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def parse_epmc(body):
    d = json.loads(body.decode("utf-8"))
    out = []
    for r in d.get("resultList", {}).get("result", []):
        pts = r.get("pubTypeList", {}).get("pubType", []) or []
        if isinstance(pts, str):
            pts = [pts]
        year = int(r["pubYear"]) if str(r.get("pubYear", "")).isdigit() else None
        journal = ((r.get("journalInfo") or {}).get("journal") or {}).get("title", "") or r.get("bookOrReportDetails",
                                                                                               {}).get("publisher", "")
        out.append({"epmc_id": "%s:%s" % (r.get("source", ""), r.get("id", "")), "source": r.get("source", ""),
                    "pmid": str(r.get("pmid") or ""), "doi": r.get("doi", "") or "", "title": _strip_tags(r.get("title")),
                    "journal": journal, "year": year, "authors_string": r.get("authorString", ""),
                    "abstract": _strip_tags(r.get("abstractText")), "pubtypes": list(pts),
                    "preprint": r.get("source") == "PPR" or any("preprint" in str(p).lower() for p in pts),
                    "retracted": any("retracted publication" in str(p).lower() for p in pts)})
    return out


def iris_url(text, size=5):
    return IRIS + "?" + _q({"query": text, "size": size})


def parse_iris(body):
    d = json.loads(body.decode("utf-8"))
    out = []
    objs = (((d.get("_embedded") or {}).get("searchResult") or {}).get("_embedded") or {}).get("objects", [])
    for o in objs:
        io = (o.get("_embedded") or {}).get("indexableObject") or {}
        md = io.get("metadata") or {}

        def first(k):
            v = md.get(k) or []
            return v[0].get("value", "") if v else ""
        uri = first("dc.identifier.uri")
        if not uri.startswith("https://iris.who.int/"):
            uri = "https://iris.who.int/handle/" + io.get("handle", "") if io.get("handle") else ""
        out.append({"title": first("dc.title") or io.get("name", ""), "date": first("dc.date.issued"),
                    "type": first("dc.type"), "url": uri, "abstract": first("dc.description.abstract")[:600],
                    "language": first("dc.language.iso")})
    return out


# ======================================================================================================
# 7. Evidence: design, sample size, ranking, GRADE-lite, brief
# ======================================================================================================
DESIGNS = (  # tier, key, label, pubtype words, title words
    (1, "systematic_review", "Systematic review / meta-analysis",
     ("systematic review", "meta-analysis", "meta analysis", "systematic-review", "network meta-analysis"),
     (r"systematic review", r"meta-analys", r"meta analys")),
    (2, "guideline", "Practice guideline",
     ("practice guideline", "guideline", "consensus development conference"), (r"\bguideline",)),
    (3, "rct", "Randomised controlled trial",
     ("randomized controlled trial", "pragmatic clinical trial", "equivalence trial", "clinical trial, phase iii",
      "clinical trial, phase iv"), (r"randomi[sz]ed", r"\brct\b")),
    (4, "cohort", "Cohort / observational study",
     ("observational study", "comparative study", "multicenter study", "clinical trial", "clinical trial, phase ii",
      "clinical trial, phase i", "controlled clinical trial"),
     (r"\bcohort\b", r"case-control", r"cross-sectional", r"\bprospective\b", r"\bretrospective\b",
      r"\bregistry\b", r"observational")),
    (5, "case_series", "Case series / case report", ("case reports", "case-report", "case report"),
     (r"case series", r"case report")),
    (6, "other", "Narrative review, editorial or other",
     ("review", "editorial", "comment", "letter", "news", "journal article", "research-article"), ()),
)
DESIGN_LABEL = {d[1]: d[2] for d in DESIGNS}
DESIGN_TIER = {d[1]: d[0] for d in DESIGNS}


def classify_design(pubtypes, title=""):
    pts = {str(p).lower().replace("-", " ").strip() for p in pubtypes or ()}
    pts |= {str(p).lower().strip() for p in pubtypes or ()}
    t = (title or "").lower()
    for tier, key, _label, words, _tw in DESIGNS[:5]:
        if any(w in pts for w in words):
            # "clinical trial" alone (no randomisation tag) is treated as non-randomised / observational
            if key == "cohort" and re.search(r"randomi[sz]ed", t):
                return "rct"
            return key
    for tier, key, _label, _w, tws in DESIGNS[:5]:
        if any(re.search(x, t) for x in tws):
            return key
    return "other"


_NUMBER = r"(\d{1,3}(?:,\d{3})+|\d+)"
_N_PATTERNS = (
    re.compile(r"\bn\s*=\s*" + _NUMBER, re.I),
    re.compile(r"\b" + _NUMBER + r"\s+(?:\w+\s+){0,3}?(?:participants|patients|women|men|subjects|adults|children|"
               r"infants|individuals|pregnancies|people|persons|volunteers|neonates|newborns|mothers|pregnant)\b", re.I),
)
_K_PATTERN = re.compile(r"\b" + _NUMBER + r"\s+(?:\w+\s+){0,2}?(?:trials|studies|rcts)\b", re.I)
_POSITIVE = re.compile(r"\b(?:significantly (?:reduced|improved|lower(?:ed)?|decreased|better)|(?:was|were|is|are) "
                       r"associated with (?:a |an )?(?:lower|reduced|improved|decreased|better)|(?:probably |may |likely )?"
                       r"(?:reduces?|reduced|improves?|improved) (?:the )?(?:risk|mortality|death|survival|outcomes?|"
                       r"symptoms|recovery)|reduction (?:of|in) (?:mortality|risk|death)|survival benefits?|(?:is|was|"
                       r"were|are) effective|superior to|beneficial effect)", re.I)
_NULL = re.compile(r"\b(?:no (?:statistically )?significant (?:difference|effect|benefit|association|reduction|"
                   r"improvement|change)s?|did not (?:significantly )?(?:reduce|improve|differ|lower|decrease|change|"
                   r"prevent)|(?:does|do) not (?:improve|reduce|prevent)|neither improves?|not associated with|no "
                   r"(?:clear |clinical |robust |meaningful )?(?:benefit|difference|effect|evidence)|little or no "
                   r"(?:effect|difference|benefit)|(?:was|were) not effective|failed to (?:reduce|improve|show)|not "
                   r"(?:significantly )?(?:different|superior))", re.I)
_HARM = re.compile(r"\b(?:(?:significant(?:ly)? )?increased? (?:the )?(?:mortality|risk of death|rate of adverse|"
                   r"adverse events|risk of harm)|increase in (?:mortality|deaths?|adverse)|associated with (?:an )?"
                   r"increased (?:risk|mortality|rate)|more adverse events|(?:higher|greater) mortality|harmful)", re.I)
CUE_BENEFIT, CUE_NULL, CUE_HARM, CUE_MIXED, CUE_UNCLEAR = ("benefit or association", "no clear difference",
                                                           "harm or increased risk", "mixed", "unclear")
_CONCLUSION_LABELS = ("CONCLUSION", "CONCLUSIONS", "AUTHORS' CONCLUSIONS", "INTERPRETATION",
                      "CONCLUSIONS AND RELEVANCE", "CONCLUSION AND RELEVANCE", "IMPLICATIONS")


def _to_int(s):
    try:
        return int(str(s).replace(",", ""))
    except ValueError:
        return None


def sample_size(text):
    best = None
    for rx in _N_PATTERNS:
        for m in rx.finditer(text or ""):
            n = _to_int(m.group(1))
            if n and 1 <= n <= 50_000_000 and not (1900 <= n <= 2100 and "," not in m.group(1)):
                best = max(best or 0, n)
    return best


def study_count(text):
    best = None
    for m in _K_PATTERN.finditer(text or ""):
        n = _to_int(m.group(1))
        if n and n < 5000:
            best = max(best or 0, n)
    return best


def conclusion_of(sections, abstract=""):
    for s in sections or ():
        if s.get("label", "").upper() in _CONCLUSION_LABELS and s.get("text"):
            return s["text"]
    text = abstract or " ".join(s.get("text", "") for s in sections or ())
    sents = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text.strip())
    return " ".join(sents[-2:]).strip() if text.strip() else ""


def direction_cue(text):
    """Keyword heuristic over the authors' conclusion (a reading aid, shown as such)."""
    t = text or ""
    pos, nul, harm = bool(_POSITIVE.search(t)), bool(_NULL.search(t)), bool(_HARM.search(t))
    if pos and (nul or harm):
        return CUE_MIXED
    if harm:
        return CUE_HARM
    if nul:
        return CUE_NULL
    if pos:
        return CUE_BENEFIT
    return CUE_UNCLEAR


def _norm_title(t):
    return re.sub(r"[^a-z0-9]+", "", (t or "").lower())[:120]


def merge_records(summaries, fetched, epmc):
    """De-duplicate PubMed (esummary + efetch) and Europe PMC records by PMID, then DOI, then title."""
    items, by_key = [], {}

    def keys(r):
        ks = []
        if r.get("pmid"):
            ks.append("pmid:" + r["pmid"])
        if r.get("doi"):
            ks.append("doi:" + r["doi"].lower())
        if r.get("title"):
            ks.append("title:" + _norm_title(r["title"]))
        return ks

    def add(r, src):
        for k in keys(r):
            if k in by_key:
                it = by_key[k]
                if src not in it["sources"]:
                    it["sources"].append(src)
                for f in ("pmid", "doi", "title", "journal", "year", "abstract"):
                    if not it.get(f) and r.get(f):
                        it[f] = r[f]
                it["pubtypes"] = sorted(set(it["pubtypes"]) | set(r.get("pubtypes", [])))
                it["retracted"] = it["retracted"] or r.get("retracted", False)
                for k2 in keys(it):
                    by_key[k2] = it
                return
        it = {"pmid": r.get("pmid", ""), "doi": r.get("doi", ""), "title": r.get("title", ""),
              "journal": r.get("journal", ""), "year": r.get("year"), "authors": r.get("authors") or
              ([r["authors_string"]] if r.get("authors_string") else []), "pubtypes": sorted(set(r.get("pubtypes", []))),
              "abstract_sections": r.get("abstract_sections", []), "abstract": r.get("abstract", ""),
              "retracted": bool(r.get("retracted")), "retraction_in": r.get("retraction_in", []),
              "preprint": bool(r.get("preprint")), "epmc_id": r.get("epmc_id", ""), "sources": [src]}
        items.append(it)
        for k in keys(it):
            by_key[k] = it

    for pmid, s in summaries.items():
        r = dict(s)
        f = fetched.get(pmid)
        if f:
            r.update({k: v for k, v in f.items() if v})
            r["pubtypes"] = sorted(set(s.get("pubtypes", [])) | set(f.get("pubtypes", [])))
        add(r, "pubmed")
    for pmid, f in fetched.items():
        if pmid not in summaries:
            add(f, "pubmed")
    for r in epmc:
        add(r, "europepmc")
    for it in items:
        if it["abstract_sections"]:
            it["abstract"] = " ".join(("%s: %s" % (s["label"], s["text"])) if s["label"] else s["text"]
                                      for s in it["abstract_sections"])
        pts = [p.lower() for p in it["pubtypes"]]
        it["retracted"] = it["retracted"] or "retracted publication" in pts or bool(it["retraction_in"])
        it["retraction_notice"] = "retraction of publication" in pts
        it["design"] = classify_design(it["pubtypes"], it["title"])
        it["tier"] = DESIGN_TIER[it["design"]]
        it["design_label"] = DESIGN_LABEL[it["design"]]
        it["sample_size"] = sample_size(it["abstract"])
        it["n_studies"] = study_count(it["abstract"]) if it["design"] == "systematic_review" else None
        it["conclusion"] = conclusion_of(it["abstract_sections"], it["abstract"])[:700]
        it["direction_cue"] = direction_cue(it["conclusion"] or it["abstract"])
        if it["preprint"] and it["pmid"]:
            it["preprint"] = False
    return items


def rank_items(items):
    return sorted(items, key=lambda it: (it["retracted"] or it["retraction_notice"], it["tier"],
                                         -(it["sample_size"] or 0), -(it["year"] or 0), it["title"]))


CERTAINTY = {4: "High", 3: "Moderate", 2: "Low", 1: "Very low", 0: "Insufficient"}


def grade_lite(items):
    """GRADE-lite: a transparent heuristic. Start level from the best non-retracted, peer-reviewed study design,
    then downgrade for inconsistency and imprecision. Returns every step so a clinician can disagree with it."""
    basis_all = [it for it in items if not it["retracted"] and not it["retraction_notice"] and not it["preprint"]
                 and it["design"] in ("systematic_review", "rct", "cohort", "case_series")]
    sr = [it for it in basis_all if it["design"] == "systematic_review"]
    rct = [it for it in basis_all if it["design"] == "rct"]
    obs = [it for it in basis_all if it["design"] == "cohort"]
    cs = [it for it in basis_all if it["design"] == "case_series"]
    steps = []
    if sr or len(rct) >= 2:
        level, basis = 4, (sr + rct)
        steps.append("Start High: %d systematic review(s)/meta-analysis(es) and %d randomised trial(s)."
                     % (len(sr), len(rct)))
    elif len(rct) == 1:
        level, basis = 3, rct + obs
        steps.append("Start Moderate: a single randomised trial (one trial is rarely enough for High).")
    elif obs:
        level, basis = 2, obs
        steps.append("Start Low: observational studies only.")
    elif cs:
        level, basis = 1, cs
        steps.append("Start Very low: case series / case reports only.")
    else:
        return {"label": CERTAINTY[0], "level": 0, "heuristic": True, "basis_refs": [], "steps": [
            "No peer-reviewed systematic review, trial or observational study was found by this search."],
            "not_assessed": list(GRADE_NOT_ASSESSED)}
    cues = {it["direction_cue"] for it in basis}
    if CUE_BENEFIT in cues and (CUE_NULL in cues or CUE_HARM in cues):
        level -= 1
        steps.append("Down 1 for inconsistency: some included conclusions report benefit, others no clear difference "
                     "or harm (keyword heuristic on the authors' conclusions).")
    elif CUE_MIXED in cues:
        steps.append("Not downgraded, but note: at least one conclusion mixes benefit and no-difference wording.")
    sizes = [it["sample_size"] for it in basis if it["sample_size"]]
    if not sizes:
        steps.append("Imprecision not assessed: no sample size could be read from the abstracts.")
    elif sum(sizes) < 400 and not sr:
        level -= 1
        steps.append("Down 1 for imprecision: about %d participants in total across the trials/studies used "
                     "(under ~400)." % sum(sizes))
    else:
        steps.append("Sample sizes read from abstracts: largest %d, total about %d." % (max(sizes), sum(sizes)))
    level = max(level, 1)
    retracted = [it for it in items if it["retracted"]]
    if retracted:
        steps.append("%d retracted publication(s) were found and excluded." % len(retracted))
    pre = [it for it in items if it["preprint"]]
    if pre:
        steps.append("%d preprint(s) (not peer reviewed) were excluded." % len(pre))
    return {"label": CERTAINTY[level], "level": level, "heuristic": True, "basis_refs": [it["ref"] for it in basis],
            "steps": steps, "not_assessed": list(GRADE_NOT_ASSESSED)}


def recency(items, as_of):
    y0 = int(as_of[:4])
    years = sorted(it["year"] for it in items if it["year"] and not it["retracted"])
    hq = [it["year"] for it in items if it["year"] and it["tier"] <= 3 and not it["retracted"] and not it["preprint"]]
    if not years:
        return {"newest_year": None, "median_year": None, "share_last_5_years": None,
                "newest_high_quality_year": None, "stale": None}
    newest_hq = max(hq) if hq else None
    return {"newest_year": years[-1], "median_year": years[len(years) // 2],
            "share_last_5_years": round(sum(1 for y in years if y >= y0 - 5) / len(years), 2),
            "newest_high_quality_year": newest_hq,
            "stale": (newest_hq is None) or (newest_hq < y0 - 10)}


def _links(it):
    out = {}
    if it.get("pmid"):
        out["pubmed"] = "https://pubmed.ncbi.nlm.nih.gov/%s/" % it["pmid"]
    if it.get("doi"):
        out["doi"] = "https://doi.org/" + it["doi"]
    if it.get("epmc_id") and not it.get("pmid"):
        src, _, i = it["epmc_id"].partition(":")
        out["europepmc"] = "https://europepmc.org/article/%s/%s" % (src, i)
    return out


def _authors_short(a):
    a = [x for x in a if x]
    if not a:
        return ""
    if len(a) == 1 and "," in a[0]:
        parts = [p.strip() for p in a[0].rstrip(".").split(",")]
        return parts[0] + (" et al." if len(parts) > 1 else "")
    return a[0] + (" et al." if len(a) > 1 else "")


def build_brief(question, meta, bodies, as_of, max_items=25):
    """PURE: question + meta + raw response bodies -> brief dict. Used live and by replay (same bytes -> same
    result hash)."""
    summaries, fetched, epmc, iris = {}, {}, [], []
    errors = []
    for name, parser in (("pubmed_esummary", parse_esummary), ("pubmed_efetch", parse_efetch),
                         ("europepmc", parse_epmc), ("who_iris", parse_iris)):
        b = bodies.get(name)
        if not b:
            continue
        try:
            val = parser(b)
        except Exception as e:
            errors.append("%s could not be read (%s)" % (name, type(e).__name__))
            continue
        if name == "pubmed_esummary":
            summaries = val
        elif name == "pubmed_efetch":
            fetched = val
        elif name == "europepmc":
            epmc = val
        else:
            iris = val
    items = rank_items(merge_records(summaries, fetched, epmc))
    kept = [it for it in items if not (it["retracted"] or it["retraction_notice"])][:max_items]
    flagged = [it for it in items if it["retracted"] or it["retraction_notice"]][:10]
    shown = kept + flagged
    for n, it in enumerate(shown, 1):
        it["ref"] = n
        it["links"] = _links(it)
        it["authors_short"] = _authors_short(it["authors"])
        it["citation"] = "%s %s. %s%s.%s" % (it["authors_short"], it["title"].rstrip("."), it["journal"],
                                            (" " + str(it["year"])) if it["year"] else "",
                                            (" PMID " + it["pmid"]) if it["pmid"] else
                                            ((" doi:" + it["doi"]) if it["doi"] else ""))
        it["abstract_excerpt"] = it["abstract"][:1200]
        for k in ("abstract_sections",):
            it.pop(k, None)
    certainty = grade_lite(kept)
    counts = {}
    for it in kept:
        counts[it["design"]] = counts.get(it["design"], 0) + 1
    statements = []
    if not shown:
        statements.append({"text": "No records were found for this question in PubMed or Europe PMC.",
                           "cites": [], "source": "search (see the Research Log for the exact queries)"})
    else:
        statements.append({"text": "%d record(s) after de-duplication (PubMed and Europe PMC)." % len(kept),
                           "cites": [it["ref"] for it in kept], "source": "search"})
        for d in DESIGNS:
            refs = [it["ref"] for it in kept if it["design"] == d[1]]
            if refs:
                statements.append({"text": "%s: %d." % (d[2], len(refs)), "cites": refs, "source": "search"})
        for it in [x for x in kept if x["tier"] <= 4 and x["conclusion"] and not x["preprint"]][:6]:
            bits = [it["design_label"]]
            if it["year"]:
                bits.append(str(it["year"]))
            if it["sample_size"]:
                bits.append("n=%d" % it["sample_size"])
            statements.append({"text": "Authors' conclusion (%s), quoted: \"%s\"" % (", ".join(bits), it["conclusion"]),
                               "cites": [it["ref"]], "source": "quotation"})
        if flagged:
            statements.append({"text": "Retracted or retraction notice - excluded from the certainty label.",
                               "cites": [it["ref"] for it in flagged], "source": "PubMed publication type"})
        if certainty["basis_refs"]:
            statements.append({"text": "Certainty (GRADE-lite heuristic, for clinician review): %s."
                                       % certainty["label"], "cites": certainty["basis_refs"],
                               "source": "computed from the cited records"})
    return {"kind": "evidence_brief", "label": DRAFT_LABEL, "as_of": as_of, "generated_at": now_iso(),
            "question": question, "query": meta.get("query", {}), "relaxed": meta.get("relaxed", []),
            "counts": counts, "certainty": certainty, "recency": recency(kept, as_of), "items": shown,
            "who_publications": iris, "statements": statements, "errors": errors, "rules": list(RULES)}


# ======================================================================================================
# 8. Trials
# ======================================================================================================
TRIAL_STATUSES = ("RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION", "ACTIVE_NOT_RECRUITING")


def age_years(s):
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*(year|month|week|day|hour|minute)s?\s*$", str(s or ""), re.I)
    if not m:
        return None
    v, unit = float(m.group(1)), m.group(2).lower()
    return v / {"year": 1, "month": 12, "week": 52.1775, "day": 365.25, "hour": 8766, "minute": 525960}[unit]


def parse_trials(body):
    d = json.loads(body.decode("utf-8"))
    out = []
    for s in d.get("studies", []):
        ps = s.get("protocolSection", {})
        idm, st, el = ps.get("identificationModule", {}), ps.get("statusModule", {}), ps.get("eligibilityModule", {})
        de, cl = ps.get("designModule", {}), ps.get("contactsLocationsModule", {})
        nct = idm.get("nctId", "")
        if not re.match(r"^NCT\d{8}$", nct):
            continue
        out.append({
            "nct_id": nct, "title": idm.get("briefTitle", ""), "official_title": idm.get("officialTitle", ""),
            "status": st.get("overallStatus", ""), "start": (st.get("startDateStruct") or {}).get("date", ""),
            "last_update": (st.get("lastUpdatePostDateStruct") or {}).get("date", ""),
            "conditions": ps.get("conditionsModule", {}).get("conditions", []),
            "phases": de.get("phases", []), "study_type": de.get("studyType", ""),
            "enrollment": (de.get("enrollmentInfo") or {}).get("count"),
            "interventions": [{"type": i.get("type", ""), "name": i.get("name", "")}
                              for i in ps.get("armsInterventionsModule", {}).get("interventions", [])],
            "sponsor": (ps.get("sponsorCollaboratorsModule", {}).get("leadSponsor") or {}).get("name", ""),
            "sex": el.get("sex", "ALL"), "minimum_age": el.get("minimumAge", ""), "maximum_age": el.get("maximumAge", ""),
            "healthy_volunteers": el.get("healthyVolunteers"),
            "criteria_excerpt": (el.get("eligibilityCriteria") or "")[:1500],
            "central_contacts": [{k: c.get(k, "") for k in ("name", "phone", "email")}
                                 for c in cl.get("centralContacts", [])][:3],
            "locations": [{k: l.get(k, "") for k in ("facility", "city", "state", "country", "status")}
                          for l in cl.get("locations", [])],
            "url": "https://clinicaltrials.gov/study/" + nct})
    return out, d.get("nextPageToken")


def filter_trials(trials, age=None, sex=None, location_terms=()):
    out = []
    for t in trials:
        why = []
        if age is not None:
            lo, hi = age_years(t["minimum_age"]), age_years(t["maximum_age"])
            if lo is not None and age < lo:
                continue
            if hi is not None and age > hi:
                continue
            why.append("age within the listed range")
        if sex and t["sex"] not in ("ALL", "", None) and t["sex"] != sex:
            continue
        if sex:
            why.append("sex criterion %s" % (t["sex"] or "ALL"))
        t = dict(t)
        terms = [x.lower() for x in location_terms if x]
        near = [l for l in t["locations"] if terms and any(
            x in " ".join([l.get("city", ""), l.get("state", ""), l.get("country", "")]).lower() for x in terms)]
        t["matching_sites"] = near[:10]
        t["site_count"] = len(t["locations"])
        t.pop("locations", None)
        t["local_checks"] = why
        out.append(t)
    return out


# ======================================================================================================
# 9. Feeds (outbreaks, travel notices, situation reports)
# ======================================================================================================
def who_don_url(top=30):
    return WHO_DON + "?" + _q({"$orderby": "PublicationDate desc", "$top": top,
                               "$select": "Title,PublicationDate,UrlName,Summary,DonId"})


def parse_who_don(body):
    d = json.loads(body.decode("utf-8"))
    out = []
    for v in d.get("value", []):
        slug = v.get("UrlName") or v.get("DonId") or ""
        out.append({"title": v.get("Title", ""), "date": (v.get("PublicationDate") or "")[:10],
                    "summary": _strip_tags(v.get("Summary", ""))[:700], "id": v.get("DonId", ""),
                    "url": WHO_DON_ITEM + urllib.parse.quote(slug) if slug else "https://www.who.int/emergencies/"
                    "disease-outbreak-news", "source": "WHO Disease Outbreak News"})
    return out


def parse_cdc_rss(body):
    root = ET.fromstring(body)
    out = []
    for it in root.iter("item"):
        link = _text(it.find("link"))
        if link and not link.startswith("https://"):
            link = ""
        out.append({"title": _text(it.find("title")), "date": _text(it.find("pubDate")),
                    "summary": _strip_tags(_text(it.find("description")))[:500], "url": link,
                    "source": "CDC Travelers' Health"})
    return out


def reliefweb_url(appname, country, limit=15):
    params = [("appname", appname), ("query[value]", country), ("query[fields][]", "country"),
              ("sort[]", "date.created:desc"), ("limit", str(limit))]
    for f in ("title", "url", "date.created", "source.shortname", "country.name"):
        params.append(("fields[include][]", f))
    return RELIEFWEB + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)


def parse_reliefweb(body):
    d = json.loads(body.decode("utf-8"))
    out = []
    for r in d.get("data", []):
        f = r.get("fields", {})
        out.append({"title": f.get("title", ""), "date": ((f.get("date") or {}).get("created") or "")[:10],
                    "url": f.get("url", "") if str(f.get("url", "")).startswith("https://") else "",
                    "summary": ", ".join(s.get("shortname", "") for s in f.get("source", []) if isinstance(s, dict)),
                    "source": "ReliefWeb"})
    return out


def region_filter(items, terms):
    terms = [t.lower() for t in terms if t]
    if not terms:
        return items
    return [i for i in items if any(t in (i.get("title", "") + " " + i.get("summary", "")).lower() for t in terms)]


# ======================================================================================================
# 10. The desk
# ======================================================================================================
DEFAULT_SETTINGS = {"contact_email": "", "ncbi_api_key": "", "reliefweb_appname": "", "tool_name": DEFAULT_TOOL,
                    "region": {"name": "", "iso2": "", "terms": []}, "offline": False, "auto_refresh": True,
                    "cache_ttl_hours": 24, "kit_lang": "en"}


def validate_settings(doc, base=None):
    out = json.loads(json.dumps(base or DEFAULT_SETTINGS))
    if not isinstance(doc, dict):
        raise ValueError("settings must be an object")
    if "contact_email" in doc:
        e = str(doc["contact_email"] or "").strip()
        if e and not re.match(r"^[^@\s]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$", e):
            raise ValueError("contact email is not valid")
        out["contact_email"] = e
    if "ncbi_api_key" in doc:
        k = str(doc["ncbi_api_key"] or "").strip()
        if k and not re.match(r"^[A-Za-z0-9]{8,64}$", k):
            raise ValueError("NCBI API key must be 8-64 letters/digits")
        out["ncbi_api_key"] = k
    if "reliefweb_appname" in doc:
        a = str(doc["reliefweb_appname"] or "").strip()
        if a and not re.match(r"^[A-Za-z0-9._-]{3,80}$", a):
            raise ValueError("ReliefWeb appname: 3-80 letters, digits, dot, dash or underscore")
        out["reliefweb_appname"] = a
    if "region" in doc:
        r = doc["region"] or {}
        if not isinstance(r, dict):
            raise ValueError("region must be an object")
        name = re.sub(r"[^\w\s'-]", "", str(r.get("name", "")), flags=re.UNICODE).strip()[:80]
        iso2 = str(r.get("iso2", "")).strip().upper()
        if iso2 and not re.match(r"^[A-Z]{2}$", iso2):
            raise ValueError("region ISO code must be two letters")
        terms = r.get("terms") or []
        if isinstance(terms, str):
            terms = terms.split(",")
        terms = [re.sub(r"[^\w\s'-]", "", str(t), flags=re.UNICODE).strip()[:60] for t in terms][:10]
        terms = [t for t in terms if t]
        if name and name not in terms:
            terms.insert(0, name)
        out["region"] = {"name": name, "iso2": iso2, "terms": terms}
    for k in ("offline", "auto_refresh"):
        if k in doc:
            out[k] = bool(doc[k])
    if "cache_ttl_hours" in doc:
        out["cache_ttl_hours"] = max(1, min(720, int(doc["cache_ttl_hours"])))
    if "kit_lang" in doc:
        lang = str(doc["kit_lang"] or "en")
        if not re.match(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$", lang):
            raise ValueError("bad language tag")
        out["kit_lang"] = lang
    return out


CONSENT_PURPOSE = "research"   # consent_center purpose "Taking part in research"


def _consent_is_active(cc, patient_id):
    """consent_center (optional, owned elsewhere): ConsentCenter.is_active(sid, purpose). Fails closed."""
    try:
        return bool(cc.is_active(patient_id, CONSENT_PURPOSE))
    except Exception:
        return False


class Desk:
    """All research logic, no HTTP. Never touches clinical records."""

    def __init__(self, data_dir, vault=None, identifiers=None, transport=None, catalogue=None, health=None,
                 limiter=None, sleep=time.sleep, consent_center=None):
        """consent_center: zero-argument callable returning clinic_app's ConsentCenter (or None)."""
        self.consent_center = consent_center or (lambda: None)
        self.catalogue = catalogue or load_sources()
        self.store = DeskStore(data_dir, vault)
        self.log = ResearchLog(self.store)
        self.scrubber = Scrubber(identifiers)
        self.guard = OutboundGuard(self.scrubber, allowed_hosts(self.catalogue),
                                   lambda: self.settings().get("contact_email", ""))
        self.fetcher = Fetcher(self.store, self.guard, self.catalogue, self.settings, transport, limiter, sleep)
        self.health = health or (lambda m: log(m))
        self._lock = threading.RLock()

    # --- settings ----------------------------------------------------------------------------
    def settings(self):
        return validate_settings(self.store.load("settings", {}), DEFAULT_SETTINGS)

    def update_settings(self, doc, actor):
        with self._lock:
            cur = self.settings()
            new = validate_settings(doc, cur)
            self.store.save("settings", new)
        self.log.append("settings", {"run_id": "R" + secrets.token_hex(8), "kind": "settings", "actor": actor,
                                     "changed": sorted(k for k in new if new[k] != cur.get(k)), "at": now_iso()})
        return self.public_settings()

    def public_settings(self):
        s = dict(self.settings())
        s["ncbi_api_key"] = "set" if s.get("ncbi_api_key") else ""
        return s

    def config(self):
        st = self.settings()
        srcs = {}
        for k, v in self.catalogue["sources"].items():
            srcs[k] = {f: v.get(f) for f in ("name", "docs", "terms", "rate_limit", "used_for", "live_check",
                                             "interaction_api", "requires", "enabled") if v.get(f) is not None}
        return {"label": DRAFT_LABEL, "rules": list(RULES), "triage_notice": TRIAGE_NOTICE,
                "trials_notice": TRIALS_NOTICE, "interaction_notice": INTERACTION_NOTICE, "version": VERSION,
                "sources": srcs, "settings": self.public_settings(), "storage": {
                    "encrypted": self.store.encrypted, "root": self.store.root},
                "consent_center": self._cc() is not None,
                "polyglot_engine": optional("polyglot_engine") is not None,
                "contact_email_missing": not st.get("contact_email"),
                "reliefweb_ready": bool(st.get("reliefweb_appname"))}

    def _cc(self):
        try:
            cc = self.consent_center()
        except Exception:
            return None
        return cc if callable(getattr(cc, "is_active", None)) else None

    # --- question handling -------------------------------------------------------------------
    def make_question(self, pico):
        if not isinstance(pico, dict):
            raise ValueError("pico must be an object with p, i, c, o")
        q, removed = {}, []
        for k in ("p", "i", "c", "o"):
            clean, rem = self.scrubber.scrub(pico.get(k, ""))
            q[k] = clean
            removed += rem
        if not (q["p"] or q["i"]):
            raise ValueError("enter at least a Population/problem or an Intervention")
        summary = {}
        for r in removed:
            summary[r] = summary.get(r, 0) + 1
        return q, summary

    @staticmethod
    def _years(years):
        if not years:
            return None
        y0, y1 = int(years[0]), int(years[1])
        cur = _dt.date.today().year
        if not (1900 <= y0 <= y1 <= cur + 1):
            raise ValueError("years must be a range like [2015, %d]" % cur)
        return (y0, y1)

    # --- 1. Living Evidence Brief ------------------------------------------------------------
    def evidence_brief(self, pico, actor, years=None, kind="brief", max_items=25):
        q, removed = self.make_question(pico)
        years = self._years(years)
        st = self.settings()
        run = Run(self, kind, actor, {"pico": q, "years": list(years) if years else None, "removed": removed})
        terms = [v for v in q.values() if v]
        base = {"db": "pubmed", "retmode": "json", "sort": "relevance"}
        if years:
            base.update({"datetype": "pdat", "mindate": str(years[0]), "maxdate": str(years[1])})
        relaxed, term = [], pubmed_term(q)
        f = run.fetch("pubmed_esearch", "pubmed", eutils_url("esearch.fcgi", st, dict(base, term=term, retmax=20)),
                      terms)
        found = parse_esearch(f.body) if f.ok else {"count": 0, "ids": []}
        for drop in (("o",), ("o", "c")):
            if found["count"] >= 5:
                break
            new = [k for k in drop if q.get(k)]
            if not new or new == relaxed:
                continue
            term = pubmed_term(q, drop)
            relaxed = new
            f = run.fetch("pubmed_esearch", "pubmed", eutils_url("esearch.fcgi", st, dict(base, term=term,
                                                                                         retmax=20)), terms)
            found = parse_esearch(f.body) if f.ok else {"count": 0, "ids": []}
        hq = run.fetch("pubmed_esearch_hq", "pubmed", eutils_url("esearch.fcgi", st, dict(
            base, term="%s AND %s" % (term, HQ_FILTER), retmax=15)), terms)
        rt = run.fetch("pubmed_esearch_retracted", "pubmed", eutils_url("esearch.fcgi", st, dict(
            base, term="%s AND retracted publication[pt]" % term, retmax=5)), terms)
        ids = []
        for res in ((parse_esearch(hq.body) if hq.ok else {"ids": []}), found,
                    (parse_esearch(rt.body) if rt.ok else {"ids": []})):
            for i in res["ids"]:
                if i not in ids:
                    ids.append(i)
        ids = ids[:40]
        if ids:
            run.fetch("pubmed_esummary", "pubmed", eutils_url("esummary.fcgi", st, {
                "db": "pubmed", "id": ",".join(ids), "retmode": "json"}), terms)
            run.fetch("pubmed_efetch", "pubmed", eutils_url("efetch.fcgi", st, {
                "db": "pubmed", "id": ",".join(ids), "retmode": "xml", "rettype": "abstract"}), terms,
                accept="application/xml")
        eq = epmc_query({k: v for k, v in q.items() if k not in relaxed}, years)
        run.fetch("europepmc", "europepmc", epmc_url(eq), terms)
        run.fetch("who_iris", "who_iris", iris_url(" ".join(x for x in (q["p"], q["i"]) if x)), terms)
        run.meta = {"query": {"pubmed": term, "europepmc": eq}, "relaxed": relaxed, "ids": ids}
        bodies = {n: self.store.get_blob(h) for n, h in run.responses.items()}
        as_of = today()
        brief = build_brief(q, run.meta, bodies, as_of, max_items)
        brief["run_id"] = run.run_id
        brief["removed_identifiers"] = removed
        brief["fetch_problems"] = [{"source": r["source"], "name": r["name"], "error": r["error"]}
                                   for r in run.requests if r["error"]]
        brief["sources_used"] = sorted({r["source"] for r in run.requests if r["status"] == 200})
        run.meta["as_of"] = as_of
        run.meta["max_items"] = max_items
        run.finish(brief)
        self._keep_brief(brief)
        return brief

    def _keep_brief(self, brief):
        with self._lock:
            doc = self.store.load("briefs", {"briefs": []})
            doc["briefs"] = ([brief] + [b for b in doc["briefs"] if b.get("run_id") != brief["run_id"]])[:KEEP_BRIEFS]
            self.store.save("briefs", doc)

    def get_brief(self, run_id):
        for b in self.store.load("briefs", {"briefs": []})["briefs"]:
            if b.get("run_id") == run_id:
                return b
        return None

    def replay(self, run_id):
        """Rebuild a logged brief from the stored response bytes and compare the result hash."""
        _e, body = self.log.find(run_id)
        if not body or body.get("kind") not in ("brief", "watch"):
            raise DeskError(404, "no evidence brief with that run id in the Research Log")
        bodies, missing = {}, []
        for name, h in body.get("responses", {}).items():
            b = self.store.get_blob(h)
            if b is None:
                missing.append(name)
            bodies[name] = b
        meta = body.get("meta", {})
        brief = build_brief(body["query"]["pico"], meta, bodies, meta.get("as_of", today()), meta.get("max_items", 25))
        brief["run_id"] = run_id
        brief["removed_identifiers"] = body["query"].get("removed", {})
        brief["fetch_problems"] = [{"source": r["source"], "name": r["name"], "error": r["error"]}
                                   for r in body.get("requests", []) if r.get("error")]
        brief["sources_used"] = sorted({r["source"] for r in body.get("requests", []) if r.get("status") == 200})
        h = result_hash(brief)
        return {"run_id": run_id, "logged_result_sha256": body.get("result_sha256"), "replayed_result_sha256": h,
                "identical": h == body.get("result_sha256"), "missing_bodies": missing}

    # --- watch-lists ---------------------------------------------------------------------------
    def watchlists(self):
        return self.store.load("watchlists", {"lists": []})["lists"]

    def alerts(self, include_ack=False):
        al = self.store.load("alerts", {"alerts": []})["alerts"]
        return [a for a in al if include_ack or not a.get("acknowledged_at")]

    def add_watchlist(self, name, pico, drugs, actor):
        q, _rem = self.make_question(pico)
        name = re.sub(r"[^\w\s'(),.-]", "", str(name or ""), flags=re.UNICODE).strip()[:80] or "Watch-list"
        dl = []
        for d in (drugs or [])[:10]:
            c, _r = self.scrubber.scrub(d, maxlen=60)
            c = re.sub(r"[^A-Za-z0-9 -]", "", c).strip()
            if c:
                dl.append(c.lower())
        w = {"id": "W" + secrets.token_hex(6), "name": name, "pico": q, "drugs": dl, "created_by": actor,
             "created_at": now_iso(), "last_run": None, "seen": [], "seen_retracted": [], "seen_recalls": []}
        with self._lock:
            doc = self.store.load("watchlists", {"lists": []})
            if len(doc["lists"]) >= 50:
                raise ValueError("at most 50 watch-lists")
            doc["lists"].append(w)
            self.store.save("watchlists", doc)
        self.log.append("watchlist", {"run_id": "R" + secrets.token_hex(8), "kind": "watchlist.add",
                                      "actor": actor, "watchlist": w["id"], "query": {"pico": q, "drugs": dl}})
        return w

    def delete_watchlist(self, wid, actor):
        with self._lock:
            doc = self.store.load("watchlists", {"lists": []})
            n = len(doc["lists"])
            doc["lists"] = [w for w in doc["lists"] if w["id"] != wid]
            if len(doc["lists"]) == n:
                raise DeskError(404, "no such watch-list")
            self.store.save("watchlists", doc)
        self.log.append("watchlist", {"run_id": "R" + secrets.token_hex(8), "kind": "watchlist.delete",
                                      "actor": actor, "watchlist": wid})
        return True

    @staticmethod
    def _item_key(it):
        return ("pmid:" + it["pmid"]) if it.get("pmid") else ("doi:" + it["doi"].lower()) if it.get("doi") else \
            "title:" + _norm_title(it.get("title"))

    def refresh_watchlist(self, wid, actor):
        w = next((x for x in self.watchlists() if x["id"] == wid), None)
        if w is None:
            raise DeskError(404, "no such watch-list")
        brief = self.evidence_brief(w["pico"], actor, kind="watch")
        new_alerts = []
        baseline = w["last_run"] is None
        seen, seen_r, seen_rc = set(w["seen"]), set(w["seen_retracted"]), set(w["seen_recalls"])
        for it in brief["items"]:
            k = self._item_key(it)
            if it["retracted"]:
                if k not in seen_r and not baseline:
                    new_alerts.append(("new_retraction", "Retraction found: %s" % it["citation"], it["links"]))
                seen_r.add(k)
            elif it["tier"] <= 3 and not it["preprint"]:
                if k not in seen and not baseline:
                    new_alerts.append(("new_high_quality_evidence", "New %s: %s" % (it["design_label"].lower(),
                                                                                   it["citation"]), it["links"]))
            seen.add(k)
        recalls_seen_now = []
        for drug in w.get("drugs", []):
            rec = self.drug_recalls(drug, actor, run_kind="watch_recalls")
            for r in rec["recalls"]:
                key = r["recall_number"] or sha256_hex(canon(r))[:16]
                recalls_seen_now.append(key)
                if key not in seen_rc and not baseline:
                    new_alerts.append(("new_recall", "FDA recall (%s, class %s, %s): %s" % (
                        drug, r["classification"], r["report_date"], r["reason"][:300]),
                        {"openfda": "https://open.fda.gov/apis/drug/enforcement/"}))
                seen_rc.add(key)
        with self._lock:
            doc = self.store.load("watchlists", {"lists": []})
            for x in doc["lists"]:
                if x["id"] == wid:
                    x.update({"last_run": now_iso(), "last_run_t": time.time(), "last_brief": brief["run_id"],
                              "last_certainty": brief["certainty"]["label"], "seen": sorted(seen)[-2000:],
                              "seen_retracted": sorted(seen_r)[-500:], "seen_recalls": sorted(seen_rc)[-1000:]})
            self.store.save("watchlists", doc)
            al = self.store.load("alerts", {"alerts": []})
            made = []
            for kind, text, links in new_alerts:
                a = {"id": "A" + secrets.token_hex(6), "watchlist": wid, "watchlist_name": w["name"], "kind": kind,
                     "text": text, "links": links, "at": now_iso(), "run_id": brief["run_id"], "label": DRAFT_LABEL}
                made.append(a)
            al["alerts"] = (made + al["alerts"])[:KEEP_ALERTS]
            self.store.save("alerts", al)
        return {"watchlist": wid, "baseline": baseline, "alerts": made, "brief_run": brief["run_id"],
                "certainty": brief["certainty"]["label"]}

    def refresh_due(self, actor="scheduler", force=False):
        out = []
        for w in self.watchlists():
            if force or not w.get("last_run_t") or time.time() - w["last_run_t"] >= WATCH_EVERY_S:
                try:
                    out.append(self.refresh_watchlist(w["id"], actor))
                except Exception as e:
                    self.health("watch-list %s refresh failed\n%s" % (w["id"], traceback.format_exc()))
                    out.append({"watchlist": w["id"], "error": "%s: %s" % (type(e).__name__, str(e)[:200])})
        return out

    def ack_alert(self, aid, actor):
        with self._lock:
            al = self.store.load("alerts", {"alerts": []})
            for a in al["alerts"]:
                if a["id"] == aid:
                    a["acknowledged_by"], a["acknowledged_at"] = actor, now_iso()
                    self.store.save("alerts", al)
                    return a
        raise DeskError(404, "no such alert")

    # --- 2. Trial Finder -------------------------------------------------------------------------
    def trials(self, condition, actor, location="", age=None, sex=None, statuses=None, consent=None):
        cond, rem = self.scrubber.scrub(condition, maxlen=120)
        loc, rem2 = self.scrubber.scrub(location, kind="location", maxlen=80)
        if not cond:
            raise ValueError("enter a condition")
        consent_basis = None
        uses_patient = age not in (None, "") or sex not in (None, "")
        if uses_patient:
            consent = consent or {}
            pid = consent.get("patient_id") or ""
            cc = self._cc()
            if cc is not None:
                if not re.match(r"^P[0-9a-f]{12}$", str(pid)):
                    raise DeskError(403, "consent_center is installed: enter the patient id so their recorded consent "
                                         "can be checked, or search without age and sex.")
                if not _consent_is_active(cc, pid):
                    raise DeskError(403, "No recorded consent for trial matching. Search without age and sex, or "
                                         "record the patient's consent first.")
                consent_basis = "consent_center"
            else:
                if consent.get("attested") is not True:
                    raise DeskError(403, "Using a patient's age or sex needs their consent. Tick the box to confirm "
                                         "the patient agreed, or search without age and sex.")
                consent_basis = "clinician_attestation"
            if age not in (None, ""):
                age = float(age)
                if not 0 <= age <= 120:
                    raise ValueError("age must be 0-120 years")
            else:
                age = None
            sex = (str(sex).upper() if sex else None)
            if sex not in (None, "FEMALE", "MALE"):
                raise ValueError("sex must be FEMALE, MALE or empty")
        statuses = [s for s in (statuses or ["RECRUITING", "NOT_YET_RECRUITING"]) if s in TRIAL_STATUSES] or \
            ["RECRUITING"]
        params = {"query.cond": cond, "filter.overallStatus": ",".join(statuses), "pageSize": 30, "format": "json"}
        if loc:
            params["query.locn"] = loc
        run = Run(self, "trials", actor, {"condition": cond, "location": loc, "statuses": statuses,
                                          "removed": rem + rem2, "patient_details_used_locally": uses_patient,
                                          "consent_basis": consent_basis})
        f = run.fetch("clinicaltrials", "clinicaltrials", CTGOV + "?" + _q(params), [cond, loc])
        trials, total = [], 0
        if f.ok:
            raw, _nxt = parse_trials(f.body)
            total = len(raw)
            trials = filter_trials(raw, age, sex, [x.strip() for x in loc.split(",") if x.strip()] if loc else [])
        out = {"kind": "trials", "label": DRAFT_LABEL, "notice": TRIALS_NOTICE, "condition": cond, "location": loc,
               "statuses": statuses, "retrieved": total, "trials": trials, "removed_identifiers": rem + rem2,
               "consent_basis": consent_basis, "as_of": today(), "error": f.error if not f.ok else None,
               "from_cache": bool(f.from_cache), "generated_at": now_iso()}
        out["run_id"] = run.finish(out)
        return out

    # --- 3. Triage & Relief ----------------------------------------------------------------------
    def triage_references(self):
        return {"notice": TRIAGE_NOTICE, "label": DRAFT_LABEL, "systems": self.catalogue.get("triage_systems", [])}

    def feeds(self, actor, refresh=False):
        st = self.settings()
        region = st["region"]
        run = Run(self, "feeds", actor, {"region": region})
        out = {"kind": "feeds", "region": region, "label": DRAFT_LABEL, "feeds": {}, "status": {}}
        f = run.fetch("who_don", "who_don", who_don_url())
        if f.ok:
            try:
                items = parse_who_don(f.body)
                out["feeds"]["who_don"] = {"items": region_filter(items, region["terms"])[:15],
                                           "latest_global": items[:5], "fetched_at": f.fetched_at,
                                           "from_cache": bool(f.from_cache), "stale": bool(f.stale)}
            except Exception as e:
                out["status"]["who_don"] = "could not read (%s)" % type(e).__name__
        else:
            out["status"]["who_don"] = f.error
        f = run.fetch("cdc_travel", "cdc_travel", CDC_RSS, accept="application/rss+xml, application/xml")
        if f.ok:
            try:
                items = parse_cdc_rss(f.body)
                out["feeds"]["cdc_travel"] = {"items": region_filter(items, region["terms"])[:15],
                                              "fetched_at": f.fetched_at, "from_cache": bool(f.from_cache),
                                              "stale": bool(f.stale)}
            except Exception as e:
                out["status"]["cdc_travel"] = "could not read (%s)" % type(e).__name__
        else:
            out["status"]["cdc_travel"] = f.error
        if not st.get("reliefweb_appname"):
            out["status"]["reliefweb"] = ("not configured: ReliefWeb requires an approved appname (request one at "
                                          "https://apidoc.reliefweb.int/parameters#appname, then enter it in "
                                          "Settings). API v1 is decommissioned.")
        elif not region["name"]:
            out["status"]["reliefweb"] = "set a region (country) in Settings"
        else:
            f = run.fetch("reliefweb", "reliefweb", reliefweb_url(st["reliefweb_appname"], region["name"]),
                          [region["name"]])
            if f.ok:
                try:
                    out["feeds"]["reliefweb"] = {"items": parse_reliefweb(f.body), "fetched_at": f.fetched_at,
                                                 "from_cache": bool(f.from_cache), "stale": bool(f.stale)}
                except Exception as e:
                    out["status"]["reliefweb"] = "could not read (%s)" % type(e).__name__
            else:
                out["status"]["reliefweb"] = f.error
        out["generated_at"] = now_iso()
        out["run_id"] = run.finish(out)
        with self._lock:
            self.store.save("last_feeds", out)
        return out

    def last_feeds(self):
        return self.store.load("last_feeds", None)

    def prepare_offline(self, actor):
        """Fill the offline cache: feeds, and a fresh brief for every watch-list."""
        res = {"feeds": None, "watchlists": []}
        res["feeds"] = self.feeds(actor, refresh=True)["status"]
        for w in self.watchlists():
            try:
                b = self.evidence_brief(w["pico"], actor, kind="watch")
                res["watchlists"].append({"id": w["id"], "run_id": b["run_id"], "certainty": b["certainty"]["label"]})
            except Exception as e:
                res["watchlists"].append({"id": w["id"], "error": str(e)[:200]})
        return res

    # --- drug reference ----------------------------------------------------------------------------
    def _drug_name(self, name):
        c, rem = self.scrubber.scrub(name, maxlen=60)
        c = re.sub(r"[^A-Za-z0-9 -]", "", c).strip().lower()
        if not c:
            raise ValueError("enter a drug name")
        return c, rem

    def drug_recalls(self, name, actor, run_kind="drug_recalls"):
        d, rem = self._drug_name(name)
        run = Run(self, run_kind, actor, {"drug": d, "removed": rem})
        search = 'openfda.generic_name:"%s" openfda.brand_name:"%s" product_description:"%s"' % (d, d, d)
        f = run.fetch("openfda_enforcement", "openfda", OPENFDA + "enforcement.json?" + urllib.parse.urlencode(
            {"search": search, "sort": "report_date:desc", "limit": 10}), [d])
        recalls = []
        if f.ok:
            for r in json.loads(f.body.decode("utf-8")).get("results", []):
                recalls.append({k: r.get(k, "") for k in ("recall_number", "classification", "status", "report_date",
                                                          "recalling_firm", "product_description", "reason_for_recall")})
                recalls[-1]["reason"] = recalls[-1].pop("reason_for_recall")
                recalls[-1]["product_description"] = recalls[-1]["product_description"][:400]
        out = {"drug": d, "recalls": recalls, "error": None if f.ok or f.status == 404 else f.error}
        run.finish(out)
        return out

    def drug_reference(self, name, actor):
        d, rem = self._drug_name(name)
        run = Run(self, "drug", actor, {"drug": d, "removed": rem})
        out = {"kind": "drug", "label": DRAFT_LABEL, "drug": d, "removed_identifiers": rem,
               "interaction_notice": INTERACTION_NOTICE, "faers_notice": FAERS_NOTICE, "as_of": today()}
        f = run.fetch("rxnav_rxcui", "rxnav", RXNAV + "rxcui.json?" + _q({"name": d, "search": 2}), [d])
        out["rxcui"] = (json.loads(f.body.decode("utf-8")).get("idGroup", {}).get("rxnormId") or []) if f.ok else []
        f = run.fetch("rxnav_approximate", "rxnav", RXNAV + "approximateTerm.json?" + _q({"term": d, "maxEntries": 5}),
                      [d])
        cands = []
        if f.ok:
            for c in (json.loads(f.body.decode("utf-8")).get("approximateGroup", {}).get("candidate") or [])[:8]:
                if c.get("name"):
                    cands.append({"rxcui": c.get("rxcui", ""), "name": c.get("name", ""), "rank": c.get("rank", ""),
                                  "source": c.get("source", "")})
        out["candidates"] = cands
        out["candidates_note"] = ("Spelling candidates from RxNorm, for you to choose from. The desk never picks one "
                                  "for you.")
        search = 'openfda.generic_name:"%s" openfda.brand_name:"%s"' % (d, d)
        f = run.fetch("openfda_label", "openfda", OPENFDA + "label.json?" + urllib.parse.urlencode(
            {"search": search, "limit": 1}), [d])
        out["label_sections"] = []
        if f.ok:
            res = json.loads(f.body.decode("utf-8")).get("results", [])
            if res:
                lab = res[0]
                of = lab.get("openfda", {})
                out["label_source"] = {"set_id": lab.get("set_id", ""), "effective_time": lab.get("effective_time", ""),
                                       "brand_name": of.get("brand_name", [])[:3],
                                       "generic_name": of.get("generic_name", [])[:3],
                                       "url": ("https://dailymed.nlm.nih.gov/dailymed/lookup.cfm?setid=" +
                                               urllib.parse.quote(lab.get("set_id", ""))) if lab.get("set_id") else ""}
                for key, title in (("boxed_warning", "Boxed warning"), ("indications_and_usage", "Indications and usage"),
                                   ("contraindications", "Contraindications"),
                                   ("warnings_and_cautions", "Warnings and precautions"), ("warnings", "Warnings"),
                                   ("drug_interactions", "Drug interactions"),
                                   ("use_in_specific_populations", "Use in specific populations"),
                                   ("pregnancy", "Pregnancy")):
                    v = lab.get(key)
                    if v:
                        txt = " ".join(v) if isinstance(v, list) else str(v)
                        trunc = len(txt) > 1500
                        out["label_sections"].append({"section": title, "quote": txt[:1500], "truncated": trunc,
                                                      "source": "FDA label via openFDA (set id %s)" %
                                                                lab.get("set_id", "?")})
        elif f.status != 404:
            out["label_error"] = f.error
        rec = self.drug_recalls(d, actor)
        out["recalls"] = rec["recalls"]
        f = run.fetch("openfda_event_count", "openfda", OPENFDA + "event.json?" + urllib.parse.urlencode(
            {"search": 'patient.drug.openfda.generic_name:"%s"' % d,
             "count": "patient.reaction.reactionmeddrapt.exact", "limit": 10}), [d])
        out["faers_top_reactions"] = []
        if f.ok:
            out["faers_top_reactions"] = [{"reaction": r.get("term", ""), "reports": r.get("count", 0)}
                                          for r in json.loads(f.body.decode("utf-8")).get("results", [])]
        out["generated_at"] = now_iso()
        out["run_id"] = run.finish(out)
        return out

    # --- field kit ---------------------------------------------------------------------------------
    def field_kit_pdf(self, actor, lang=None):
        pe = optional("polyglot_engine")
        if pe is None:
            raise DeskError(501, "polyglot_engine is not installed, so the field kit PDF cannot be made.")
        st = self.settings()
        lang = lang or st.get("kit_lang", "en")
        region = st["region"]
        pdf = pe.PolyglotPdf("Stark Covenant field kit - research and triage references",
                             author="Stark Covenant Research Desk", subject="Offline reference for relief teams",
                             lang=lang)
        pdf.heading("Field kit (offline reference)")
        pdf.paragraph("Made %s. Region: %s. %s" % (now_iso(), region.get("name") or "not set", DRAFT_LABEL))
        for r in RULES:
            pdf.paragraph("- " + r)
        ec = optional("emergency_comms")
        if ec is not None and region.get("iso2"):
            try:
                plan = ec.dial_plan(region["iso2"])
            except Exception:
                plan = None
            if plan:
                pdf.heading("Emergency numbers - %s" % plan["country"])
                for b in plan["buttons"]:
                    pdf.paragraph("%s: %s%s" % (b["service"], b["number"], "" if b["verified"] else " (NOT verified)"))
                for w in plan["warnings"]:
                    pdf.paragraph(w)
        pdf.heading("Mass-casualty and emergency triage systems (reference only)")
        pdf.paragraph(TRIAGE_NOTICE)
        for s in self.catalogue.get("triage_systems", []):
            pdf.heading(s["name"], size=13)
            pdf.paragraph("Origin: " + s["origin"])
            if s.get("reference"):
                pdf.paragraph("Reference: " + s["reference"])
            pdf.paragraph(s["summary"])
            for ln in s["links"]:
                pdf.paragraph("Source: " + ln)
        feeds = self.last_feeds()
        if feeds:
            pdf.heading("Outbreak and situation reports (as of %s)" % feeds.get("generated_at", "?"))
            for key, title in (("who_don", "WHO Disease Outbreak News"), ("cdc_travel", "CDC travel notices"),
                               ("reliefweb", "ReliefWeb")):
                fd = feeds.get("feeds", {}).get(key)
                if not fd:
                    continue
                items = fd["items"] or fd.get("latest_global", [])
                pdf.heading(title + ("" if fd["items"] else " (latest worldwide; nothing matched the region)"), size=12)
                for it in items[:10]:
                    pdf.paragraph("%s - %s. %s %s" % (it.get("date", ""), it.get("title", ""),
                                                      it.get("summary", "")[:300], it.get("url", "")))
        else:
            pdf.paragraph("No outbreak feeds cached yet: press 'Refresh feeds' while online.")
        briefs = {b["run_id"]: b for b in self.store.load("briefs", {"briefs": []})["briefs"]}
        wl = self.watchlists()
        if wl:
            pdf.heading("Watch-list evidence briefs")
            for w in wl:
                b = briefs.get(w.get("last_brief"))
                pdf.heading(w["name"], size=12)
                if not b:
                    pdf.paragraph("Not refreshed yet.")
                    continue
                pdf.paragraph("Certainty (GRADE-lite, for clinician review): %s. As of %s." %
                              (b["certainty"]["label"], b["as_of"]))
                for it in b["items"][:8]:
                    pdf.paragraph("[%d] %s%s %s" % (it["ref"], "RETRACTED - " if it["retracted"] else "",
                                                    it["citation"], it["links"].get("pubmed", it["links"].get("doi", ""))))
        data = pdf.build()
        self.log.append("fieldkit", {"run_id": "R" + secrets.token_hex(8), "kind": "fieldkit", "actor": actor,
                                     "sha256": sha256_hex(data), "bytes": len(data), "warnings": pdf.warnings[:20]})
        kit_dir = os.path.join(self.store.root, "fieldkit")
        os.makedirs(kit_dir, exist_ok=True)
        if not self.store.encrypted:
            DeskStore._atomic(os.path.join(kit_dir, "field_kit_latest.pdf"), data)
        return data

    def brief_pdf(self, run_id):
        pe = optional("polyglot_engine")
        if pe is None:
            raise DeskError(501, "polyglot_engine is not installed.")
        b = self.get_brief(run_id)
        if b is None:
            raise DeskError(404, "no stored brief with that run id")
        q = b["question"]
        pdf = pe.PolyglotPdf("Evidence brief (draft for clinician review)", author="Stark Covenant Research Desk")
        pdf.heading("Evidence brief - " + DRAFT_LABEL)
        pdf.paragraph("Question: P: %s | I: %s | C: %s | O: %s" % (q.get("p"), q.get("i"), q.get("c"), q.get("o")))
        pdf.paragraph("As of %s. Run %s (Research Log)." % (b["as_of"], b["run_id"]))
        c = b["certainty"]
        pdf.heading("Certainty (GRADE-lite heuristic): " + c["label"], size=12)
        for s in c["steps"]:
            pdf.paragraph("- " + s)
        pdf.paragraph("Not assessed: " + ", ".join(c["not_assessed"]))
        pdf.heading("Statements (each cited)", size=12)
        for s in b["statements"]:
            pdf.paragraph("%s %s" % (s["text"], "".join("[%d]" % n for n in s["cites"][:30])))
        pdf.heading("References", size=12)
        for it in b["items"]:
            pdf.paragraph("[%d] %s%s (%s) %s" % (it["ref"], "RETRACTED - " if it["retracted"] else "", it["citation"],
                                                it["design_label"], " ".join(it["links"].values())))
        return pdf.build()


# ======================================================================================================
# 11. HTTP module (mounted into clinic_app) and mount()
# ======================================================================================================
class ResearchDeskModule:
    """`if research.handle(self): return` inside the clinic handler. session_lookup(handler) -> (session, user) or
    raises; perm_check(user) -> bool."""

    CSP = ("default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; "
           "connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'")

    def __init__(self, desk, session_lookup, perm_check=None, health=None, prefix=PREFIX):
        self.desk = desk
        self.session_lookup = session_lookup
        self.perm_check = perm_check or (lambda u: bool(set(u.get("roles", ())) & {"clinician", "nurse", "owner"}))
        self.health = health or log
        self.prefix = prefix
        self._stop = threading.Event()
        self._thread = None

    # --- scheduler (daily watch-list refresh while the app runs) ----------------------------------
    def start_scheduler(self, tick=SCHEDULER_TICK_S):
        if self._thread is not None:
            return
        def loop():
            while not self._stop.wait(tick):
                try:
                    st = self.desk.settings()
                    if st.get("auto_refresh") and not st.get("offline") and self.desk.watchlists():
                        self.desk.refresh_due("scheduler")
                except Exception:
                    self.health("research scheduler\n" + traceback.format_exc())
        self._thread = threading.Thread(target=loop, name="research-desk-scheduler", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    # --- HTTP ---------------------------------------------------------------------------------------
    def _send(self, h, code, body, ctype="application/json; charset=utf-8", filename=None):
        if not isinstance(body, (bytes, bytearray)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        h.send_response(code)
        h.send_header("Content-Type", ctype)
        h.send_header("Content-Length", str(len(body)))
        h.send_header("Cache-Control", "no-store")
        h.send_header("X-Content-Type-Options", "nosniff")
        h.send_header("X-Frame-Options", "DENY")
        h.send_header("Referrer-Policy", "no-referrer")
        if ctype.startswith("text/html"):
            h.send_header("Content-Security-Policy", self.CSP)
        if filename:
            h.send_header("Content-Disposition", 'attachment; filename="%s"' % re.sub(r"[^A-Za-z0-9._-]", "_", filename))
            h.send_header("Content-Security-Policy", "sandbox")
        h.end_headers()
        if h.command != "HEAD":
            h.wfile.write(body)

    def _err(self, h, code, msg):
        # an early rejection (origin, CSRF, auth) used to answer before reading the POST body, so the client saw a
        # reset connection (WinError 10053) instead of the error; read and discard it first (bounded by MAX_POST)
        if h.command == "POST" and not getattr(h, "_gc_body_read", False):
            try:
                n = int(h.headers.get("Content-Length") or "0")
            except ValueError:
                n = -1
            if 0 < n <= MAX_POST:
                h.rfile.read(n)
            elif n != 0:
                h.close_connection = True
            h._gc_body_read = True
        self._send(h, code, {"ok": False, "error": msg})

    def _body(self, h):
        if (h.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
            raise DeskError(415, "JSON only.")
        try:
            n = int(h.headers.get("Content-Length") or "-1")
        except ValueError:
            n = -1
        if n < 0 or n > MAX_POST:
            raise DeskError(413 if n > MAX_POST else 411, "body missing or too large")
        raw = h.rfile.read(n)
        h._gc_body_read = True
        try:
            obj = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            raise DeskError(400, "Malformed JSON.")
        if not isinstance(obj, dict):
            raise DeskError(400, "JSON object expected.")
        return obj

    @staticmethod
    def _same_origin(h):
        origin = h.headers.get("Origin")
        if origin and urllib.parse.urlsplit(origin).netloc.lower() != (h.headers.get("Host") or "").lower():
            return False
        return (h.headers.get("Sec-Fetch-Site") or "").lower() != "cross-site"

    def _auth(self, h):
        try:
            res = self.session_lookup(h)
        except Exception:
            res = None
        if not res:
            raise DeskError(401, "Please sign in to Stark Covenant first.")
        sess, user = res
        if not self.perm_check(user):
            raise DeskError(403, "The Research Desk is for clinical roles (clinician, nurse).")
        return sess, user

    def handle(self, h):
        u = urllib.parse.urlsplit(h.path)
        path = u.path
        if not (path == self.prefix or path.startswith(self.prefix + "/")):
            return False
        sub = path[len(self.prefix):] or "/"
        try:
            if h.command not in ("GET", "POST", "HEAD"):
                raise DeskError(405, "Method not allowed.")
            if sub in ("/", "/index.html"):
                if h.command == "POST":
                    raise DeskError(405, "Method not allowed.")
                ck = re.search(r"(?:^|;\s*)gc_lang=([^;]+)", h.headers.get("Cookie") or "")
                page = render_page(urllib.parse.unquote(ck.group(1))[:20] if ck else "",
                                   h.headers.get("Accept-Language") or "")
                self._send(h, 200, page.encode("utf-8"), "text/html; charset=utf-8")
                return True
            if not sub.startswith("/api/"):
                raise DeskError(404, "Not found.")
            sess, user = self._auth(h)
            body, q = {}, {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
            if h.command == "POST":
                if not self._same_origin(h):
                    raise DeskError(403, "Cross-origin request refused.")
                tok = h.headers.get("X-CSRF-Token", "")
                if not tok or not secrets.compare_digest(tok, str(sess.get("csrf", ""))):
                    raise DeskError(403, "Missing or wrong CSRF token. Reload the page.")
                body = self._body(h)
            fn = ROUTES.get((h.command if h.command != "HEAD" else "GET", sub[4:]))
            if fn is None:
                raise DeskError(404, "Unknown Research Desk API.")
            res = fn(self, sess, user, q, body)
            if isinstance(res, tuple) and res[0] == "pdf":
                self._send(h, 200, res[2], "application/pdf", filename=res[1])
            else:
                out = {"ok": True}
                out.update(res)
                self._send(h, 200, out)
        except DeskError as e:
            self._err(h, e.status, e.msg)
        except (ValueError, TypeError, KeyError) as e:
            self._err(h, 400, str(e)[:300] or "bad request")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self.health("research_desk %s %s\n%s" % (h.command, path, traceback.format_exc()))
            try:
                self._err(h, 500, "Something went wrong in the Research Desk. Nothing in the records was changed.")
            except Exception:
                pass
        return True


def _uid(user):
    return str(user.get("id", "?"))


ROUTES = {
    ("GET", "/session"): lambda m, s, u, q, b: {"csrf": s.get("csrf", ""), "user": {
        "id": _uid(u), "display": u.get("display") or u.get("username", ""), "roles": list(u.get("roles", []))}},
    ("GET", "/config"): lambda m, s, u, q, b: m.desk.config(),
    ("POST", "/settings"): lambda m, s, u, q, b: {"settings": m.desk.update_settings(b, _uid(u))},
    ("POST", "/brief"): lambda m, s, u, q, b: {"brief": m.desk.evidence_brief(b.get("pico") or {}, _uid(u),
                                                                             b.get("years"),
                                                                             max_items=min(int(b.get("max", 25)), 40))},
    ("POST", "/brief/pdf"): lambda m, s, u, q, b: ("pdf", "evidence_brief.pdf", m.desk.brief_pdf(str(b.get("run_id")))),
    ("GET", "/watchlists"): lambda m, s, u, q, b: {"watchlists": m.desk.watchlists(), "alerts": m.desk.alerts()},
    ("POST", "/watchlists"): lambda m, s, u, q, b: {"watchlist": m.desk.add_watchlist(
        b.get("name"), b.get("pico") or {}, b.get("drugs") or [], _uid(u))},
    ("POST", "/watchlists/delete"): lambda m, s, u, q, b: {"deleted": m.desk.delete_watchlist(str(b.get("id")),
                                                                                             _uid(u))},
    ("POST", "/watchlists/refresh"): lambda m, s, u, q, b: {"results": (
        [m.desk.refresh_watchlist(str(b["id"]), _uid(u))] if b.get("id") else m.desk.refresh_due(_uid(u), force=True))},
    ("POST", "/alerts/ack"): lambda m, s, u, q, b: {"alert": m.desk.ack_alert(str(b.get("id")), _uid(u))},
    ("POST", "/trials"): lambda m, s, u, q, b: {"trials": m.desk.trials(
        b.get("condition", ""), _uid(u), b.get("location", ""), b.get("age"), b.get("sex"), b.get("statuses"),
        b.get("consent"))},
    ("GET", "/triage"): lambda m, s, u, q, b: m.desk.triage_references(),
    ("GET", "/feeds"): lambda m, s, u, q, b: {"feeds": m.desk.feeds(_uid(u)) if q.get("refresh") == "1"
                                              else (m.desk.last_feeds() or m.desk.feeds(_uid(u)))},
    ("POST", "/fieldkit"): lambda m, s, u, q, b: ("pdf", "stark_field_kit.pdf", m.desk.field_kit_pdf(_uid(u),
                                                                                                    b.get("lang"))),
    ("POST", "/offline/prepare"): lambda m, s, u, q, b: {"prepared": m.desk.prepare_offline(_uid(u))},
    ("POST", "/drug"): lambda m, s, u, q, b: {"drug": m.desk.drug_reference(b.get("name", ""), _uid(u))},
    ("GET", "/log"): lambda m, s, u, q, b: {"entries": m.desk.log.entries(min(int(q.get("limit", 100)), 500),
                                                                          int(q.get("offset", 0)))},
    ("GET", "/log/verify"): lambda m, s, u, q, b: {"verify": m.desk.log.verify()},
    ("POST", "/log/replay"): lambda m, s, u, q, b: {"replay": m.desk.replay(str(b.get("run_id")))},
}


def clinic_identifiers(clinic):
    """Every identifier of this clinic's patients, read locally so the scrubber and the outbound guard can block
    them: names, given and family names, the emergency contact, dates of birth, record numbers, phones, e-mails,
    street lines and postcodes (city, region and country are left out: they are needed for region searches).
    The patient index gives names, DOBs and record numbers; the rest comes from each chart, decrypted once and
    cached until the index changes. Never sent, never stored by the desk. A chart that cannot be read raises, so
    the scrubber fails closed."""
    cache = {"key": None, "val": None}

    def provider():
        if getattr(clinic, "state", "") != "ready":
            return {}
        idx = clinic._index().get("patients", {})
        key = json.dumps(idx, sort_keys=True, default=str)
        if cache["key"] == key:
            return cache["val"]
        out = {"names": [], "nicknames": [], "dobs": [], "mrns": [], "phones": [], "emails": [], "addresses": [],
               "postcodes": []}
        for pid, p in idx.items():
            out["names"].append(p.get("name", ""))
            out["dobs"].append(p.get("dob", ""))
            out["mrns"].append(p.get("mrn", ""))
            d = (clinic._load_chart(pid) or {}).get("demographics", {})
            for f in ("name", "given_name", "family_name", "emergency_contact"):
                if d.get(f):
                    out["names"].append(str(d[f]))
            for f, dest in (("dob", "dobs"), ("mrn", "mrns"), ("phone", "phones"), ("email", "emails"),
                            ("address", "addresses"), ("addr_line1", "addresses"), ("addr_line2", "addresses"),
                            ("addr_postal", "postcodes")):
                if d.get(f):
                    out[dest].append(str(d[f]))
            for m in re.finditer(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", str(d.get("emergency_contact", ""))):
                out["emails"].append(m.group(0))
            for m in re.finditer(r"\+?\d[\d ()\-.]{6,}\d", str(d.get("emergency_contact", ""))):
                out["phones"].append(m.group(0))
        cache["key"], cache["val"] = key, out
        return out
    return provider


def _make_module(app, transport=None, start_scheduler=True):
    clinic = app.clinic
    vault = clinic.vault
    data_dir = os.path.join(clinic.data_root, "research" if vault is not None else "research_no_vault")

    def lookup(h):
        return clinic.session(h._cookie())

    def perm(user):
        try:
            return "clin.read" in clinic.perms(user)
        except Exception:
            return False
    desk = Desk(data_dir, vault=vault, identifiers=clinic_identifiers(clinic), transport=transport,
                health=clinic.health, consent_center=lambda: getattr(clinic, "consent_center", None))
    mod = ResearchDeskModule(desk, lookup, perm, clinic.health)
    if start_scheduler:
        mod.start_scheduler()
    return mod


def mount(app, transport=None, start_scheduler=True):
    """Mount the Research Desk on a clinic_app.CovenantApp. Sets app.research and returns the module. When the
    vault opens after start-up (clinic.on_ready), the desk is mounted again so its data is stored encrypted."""
    mod = _make_module(app, transport, start_scheduler)
    app.research = mod
    if not getattr(app, "_research_remount_hooked", False):
        def remount():
            if app.clinic.vault is not None:
                old = getattr(app, "research", None)
                try:
                    app.research = _make_module(app, transport, start_scheduler)
                    if old is not None:
                        old.stop()
                except Exception:
                    app.clinic.health("research_desk re-mount failed\n" + traceback.format_exc())
        app.clinic.on_ready.append(remount)
        app._research_remount_hooked = True
    return mod


# ======================================================================================================
# 12. UI page (inline; DOM built with textContent only - no HTML from data)
# ======================================================================================================
PAGE = r"""<!doctype html><html __HTML_ATTRS__><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Stark Covenant</title><link rel="icon" href="__FAVICON__"><style>
:root{--bg:#f6f7f9;--fg:#15202b;--mut:#56616d;--card:#fff;--line:#d9dee4;--acc:#1f5f8b;--warn:#7a4200;--warnbg:#fff4e0;--bad:#9b1c1c;--badbg:#fde8e8;--ok:#1d6b3a}
@media (prefers-color-scheme:dark){:root{--bg:#11161c;--fg:#e6ebf0;--mut:#9aa7b3;--card:#1a2129;--line:#2c3742;--acc:#7cb7e0;--warn:#ffcf8a;--warnbg:#3a2a10;--bad:#ffb4b4;--badbg:#3d1818;--ok:#8fd6a8}}
*{box-sizing:border-box}body{margin:0;font:15px/1.45 system-ui,"Segoe UI","Nirmala UI","Microsoft YaHei","Yu Gothic UI",Tahoma,Arial,sans-serif;background:var(--bg);color:var(--fg);overflow-wrap:anywhere}
header{display:flex;flex-wrap:wrap;gap:12px;align-items:center;padding:12px 16px;background:var(--card);border-bottom:1px solid var(--line)}
header b{font-size:18px}.draft{background:var(--warnbg);color:var(--warn);padding:3px 8px;border-radius:4px;font-weight:600}
a{color:var(--acc)}nav{display:flex;flex-wrap:wrap;gap:6px;padding:10px 16px}nav button{border:1px solid var(--line);background:var(--card);color:var(--fg);padding:6px 12px;border-radius:16px;cursor:pointer;min-height:36px}
nav button[aria-selected=true]{background:var(--acc);color:#fff;border-color:var(--acc)}main{padding:0 16px 40px;max-width:1100px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:14px;margin:12px 0;min-width:0}
.rules{font-size:13px;color:var(--mut);margin:0;padding-inline-start:18px}label{display:block;margin:8px 0 2px;font-weight:600;font-size:13px}
input,select,textarea{width:100%;padding:7px;border:1px solid var(--line);border-radius:5px;background:var(--bg);color:var(--fg);font:inherit}
input[type=checkbox]{width:auto}
.row{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(200px,100%),1fr));gap:10px}button.go{margin-top:10px;background:var(--acc);color:#fff;border:0;padding:8px 16px;border-radius:5px;cursor:pointer;font-weight:600;min-height:40px}
button.sec{margin-block:4px 0;margin-inline:0 4px;background:transparent;color:var(--acc);border:1px solid var(--acc);padding:5px 10px;border-radius:5px;cursor:pointer;min-height:36px}
.flag{display:inline-block;font-size:12px;font-weight:700;padding:1px 6px;border-radius:3px;margin-inline-end:4px}.bad{background:var(--badbg);color:var(--bad)}.warn{background:var(--warnbg);color:var(--warn)}
.mut{color:var(--mut);font-size:13px}.cert{font-size:18px;font-weight:700}.tw{overflow-x:auto;max-width:100%}table{border-collapse:collapse;width:100%;font-size:13px}td,th{border-bottom:1px solid var(--line);padding:5px;text-align:start;vertical-align:top}
blockquote{margin:6px 0;padding:4px 10px;border-inline-start:3px solid var(--line);color:var(--fg)}#msg{position:sticky;top:0;z-index:2}.err{background:var(--badbg);color:var(--bad);padding:8px 12px;border-radius:5px;margin:8px 16px}
.info{background:var(--warnbg);color:var(--warn);padding:8px 12px;border-radius:5px;margin:8px 16px}
.draftlang{background:#fff4d6;color:#5c4000;padding:6px 16px;font-size:13px}.en-orig{color:var(--mut);font-size:.9em}
</style></head><body>
<header><b id="title"></b><span class="draft" id="lbl"></span><a href="/" id="back"></a><span class="mut" id="who"></span></header>
<div id="draftLang" class="draftlang" role="note" hidden></div>
<div id="msg"></div><div class="card" style="margin:12px 16px"><ul class="rules" id="rules"></ul></div>
<nav id="tabs" aria-label="-" role="tablist"></nav><main id="main"></main>
<script type="application/json" id="i18n-data">__I18N_DATA__</script>
<script>
"use strict";
__I18N_JS__
const P='/research/api';let CSRF='',CFG=null;const $=s=>document.querySelector(s);
function el(t,a,...k){const e=document.createElement(t);if(a)for(const[x,v]of Object.entries(a)){if(x==='on')for(const[ev,f]of Object.entries(v))e.addEventListener(ev,f);else if(v!==false&&v!=null)e.setAttribute(x,v===true?'':v)}for(const c of k.flat(9))if(c!=null&&c!==false)e.append(c.nodeType?c:String(c));return e}
function say(t,cls){const m=$('#msg');m.replaceChildren();if(t)m.append(el('div',{class:cls||'err'},t))}
async function api(path,body){const o={credentials:'same-origin',headers:{}};if(body!==undefined){o.method='POST';o.headers['Content-Type']='application/json';o.headers['X-CSRF-Token']=CSRF;o.body=JSON.stringify(body)}
 const r=await fetch(P+path,o);const ct=r.headers.get('Content-Type')||'';if(r.ok&&ct.includes('application/pdf'))return await r.blob();const j=await r.json().catch(()=>({error:'HTTP '+r.status}));if(!r.ok||j.ok===false)throw new Error(trErr(j.error||('HTTP '+r.status)));return j}
function save(blob,name){const a=el('a',{href:URL.createObjectURL(blob),download:name});document.body.append(a);a.click();setTimeout(()=>{URL.revokeObjectURL(a.href);a.remove()},2000)}
function busy(btn,fn){return async()=>{btn.disabled=true;const t=btn.textContent;btn.textContent=T('rd.working');say('');try{await fn()}catch(e){say(e.message)}finally{btn.disabled=false;btn.textContent=t}}}
function field(id,label,ph,type){return el('div',null,el('label',{for:id},label),el(type==='area'?'textarea':'input',{id,placeholder:ph||'',type:type&&type!=='area'?type:'text',rows:type==='area'?2:false,dir:'auto'}))}
function v(id){const e=document.getElementById(id);return e?(e.type==='checkbox'?e.checked:e.value.trim()):''}
function links(o){return Object.entries(o||{}).map(([k,u])=>[el('a',{href:u,target:'_blank',rel:'noopener noreferrer'},k),' '])}
function tw(t){return el('div',{class:'tw'},t)}
function num(x){return nfmt(x)}
function removedNote(r){const ks=Array.isArray(r)?r:Object.keys(r||{});return ks.length?el('div',{class:'info',style:'margin:8px 0'},T('rd.removed',{list:Array.isArray(r)?r.join(', '):Object.entries(r).map(([k,n])=>n+' x '+k.replace(/_/g,' ')).join(', ')})):null}
const TABS=['brief','watch','trials','triage','drug','log','settings'];
function tabs(cur){$('#tabs').setAttribute('aria-label',T('rd.tabs_aria'));$('#tabs').replaceChildren(...TABS.map(k=>el('button',{role:'tab','aria-selected':String(k===cur),on:{click:()=>show(k)}},T('rd.tab_'+k))))}
function pico(prefix){return el('div',{class:'row'},field(prefix+'p',T('rd.pico_p'),T('rd.pico_p_ph')),field(prefix+'i',T('rd.pico_i'),T('rd.pico_i_ph')),field(prefix+'c',T('rd.pico_c'),T('rd.pico_c_ph')),field(prefix+'o',T('rd.pico_o'),T('rd.pico_o_ph')))}
function getPico(prefix){return{p:v(prefix+'p'),i:v(prefix+'i'),c:v(prefix+'c'),o:v(prefix+'o')}}
function draftLbl(){return legal('div','rd.draft_label',null,{class:'draft'})}
function renderBrief(b){const box=el('div',{class:'card'});const c=b.certainty,rc=b.recency;
 const rec=[T('rd.rec_newest',{y:rc.newest_year||'-'}),T('rd.rec_newest_hq',{y:rc.newest_high_quality_year||'-'}),T('rd.rec_median',{y:rc.median_year||'-'})];
 if(rc.share_last_5_years!=null)rec.push(T('rd.rec_share',{pct:Math.round(rc.share_last_5_years*100)}));
 box.append(draftLbl(),removedNote(b.removed_identifiers),
  el('p',{class:'cert'},T('rd.certainty',{label:c.label})),el('ul',null,c.steps.map(s=>el('li',null,s))),el('p',{class:'mut'},T('rd.not_assessed',{list:c.not_assessed.join(', ')})),
  el('p',{class:'mut'},T('rd.recency',{list:rec.join(', ')})+(rc.stale?' - '+T('rd.rec_stale'):'')+'. '+T('rd.as_of',{date:b.as_of})),
  el('h3',null,T('rd.statements')),el('ul',null,b.statements.map(s=>el('li',null,s.text,' ',s.cites.slice(0,40).map(n=>el('a',{href:'#ref-'+n},'['+n+']')),s.cites.length?'':el('span',{class:'mut'},' ('+s.source+')')))),
  el('h3',null,T('rd.references')),tw(el('table',null,el('tr',null,['rd.col_ref','rd.col_design','rd.col_year','rd.col_n','rd.col_citation','rd.col_flags'].map(h=>el('th',null,T(h)))),b.items.map(it=>el('tr',{id:'ref-'+it.ref},el('td',null,it.ref),el('td',null,it.design_label),el('td',null,it.year||''),el('td',null,it.sample_size||''),
   el('td',{lang:'en',dir:'ltr'},it.citation,' ',links(it.links),it.abstract_excerpt?el('details',null,el('summary',null,T('rd.abstract')),el('blockquote',null,it.abstract_excerpt)):null),
   el('td',null,it.retracted?el('span',{class:'flag bad'},T('rd.retracted')):null,it.retraction_notice?el('span',{class:'flag bad'},T('rd.retraction_notice')):null,it.preprint?el('span',{class:'flag warn'},T('rd.preprint')):null,it.sources.join(' + ')))))));
 if(b.who_publications&&b.who_publications.length)box.append(el('h3',null,T('rd.who_pubs')),el('ul',null,b.who_publications.map(w=>el('li',null,w.title+' ('+(w.date||'')+', '+(w.type||'')+') ',w.url?el('a',{href:w.url,target:'_blank',rel:'noopener noreferrer'},'IRIS'):null))));
 if(b.fetch_problems&&b.fetch_problems.length)box.append(el('p',{class:'mut'},T('rd.source_problems',{list:b.fetch_problems.map(p=>p.source+': '+p.error).join('; ')})));
 const pb=el('button',{class:'sec'},T('rd.download_pdf'));pb.onclick=busy(pb,async()=>save(await api('/brief/pdf',{run_id:b.run_id}),'evidence_brief.pdf'));
 box.append(el('p',{class:'mut'},T('rd.query_sent',{q:(b.query||{}).pubmed||''})+(b.relaxed&&b.relaxed.length?' '+T('rd.relaxed',{list:b.relaxed.join(', ')}):'')+'. '+T('rd.run_in_log',{id:b.run_id})),pb);return box}
const VIEWS={
brief(m){const out=el('div');const go=el('button',{class:'go'},T('rd.brief_go'));
 m.append(el('div',{class:'card'},el('h2',null,T('rd.brief_title')),el('p',{class:'mut'},T('rd.brief_hint')),pico('b'),el('div',{class:'row'},field('by0',T('rd.from_year'),'2015','number'),field('by1',T('rd.to_year'),'','number')),go),out);
 go.onclick=busy(go,async()=>{const y0=v('by0'),y1=v('by1');const r=await api('/brief',{pico:getPico('b'),years:y0?[+y0,+(y1||new Date().getFullYear())]:null});out.replaceChildren(renderBrief(r.brief))})},
async watch(m){const r=await api('/watchlists');const add=el('button',{class:'go'},T('rd.wl_create'));const all=el('button',{class:'sec'},T('rd.wl_refresh_all'));
 m.append(el('div',{class:'card'},el('h2',null,T('rd.alerts')),r.alerts.length?el('ul',null,r.alerts.map(a=>{const b=el('button',{class:'sec'},T('rd.mark_read'));b.onclick=busy(b,async()=>{await api('/alerts/ack',{id:a.id});show('watch')});return el('li',null,el('span',{class:'flag '+(a.kind==='new_high_quality_evidence'?'warn':'bad')},TK('rd.alert',a.kind)),' '+a.watchlist_name+': '+a.text+' ',links(a.links),b)})):el('p',{class:'mut'},T('rd.no_alerts'))),
  el('div',{class:'card'},el('h2',null,T('rd.tab_watch')),el('p',{class:'mut'},T('rd.wl_hint')),all,
   tw(el('table',null,el('tr',null,['rd.col_name','rd.col_question','rd.col_drugs','rd.col_last','rd.col_certainty','rd.col_actions'].map(h=>el('th',null,T(h)))),r.watchlists.map(w=>{const rf=el('button',{class:'sec'},T('rd.refresh'));rf.onclick=busy(rf,async()=>{await api('/watchlists/refresh',{id:w.id});show('watch')});const del=el('button',{class:'sec'},T('rd.delete'));del.onclick=busy(del,async()=>{if(confirm(T('rd.delete_confirm'))){await api('/watchlists/delete',{id:w.id});show('watch')}});
    return el('tr',null,el('td',{dir:'auto'},w.name),el('td',{dir:'auto'},['p','i','c','o'].map(k=>w.pico[k]).filter(Boolean).join(' / ')),el('td',null,(w.drugs||[]).join(', ')),el('td',null,w.last_run||T('rd.never')),el('td',null,w.last_certainty||''),el('td',null,rf,del))})))),
  el('div',{class:'card'},el('h3',null,T('rd.wl_new')),field('wn',T('rd.col_name'),T('rd.wl_name_ph')),pico('w'),field('wd',T('rd.wl_drugs'),'metformin'),add));
 all.onclick=busy(all,async()=>{await api('/watchlists/refresh',{});show('watch')});
 add.onclick=busy(add,async()=>{await api('/watchlists',{name:v('wn'),pico:getPico('w'),drugs:v('wd').split(/[,،、，]/).map(s=>s.trim()).filter(Boolean)});show('watch')})},
trials(m){const out=el('div');const go=el('button',{class:'go'},T('rd.trials_go'));
 m.append(el('div',{class:'card'},el('h2',null,T('rd.tab_trials')),el('p',{class:'mut'},CFG.trials_notice),el('div',{class:'row'},field('tc',T('rd.condition'),T('rd.condition_ph')),field('tl',T('rd.region_field'),T('rd.region_ph'))),
  el('h3',null,T('rd.patient_details')),el('div',{class:'row'},field('ta',T('rd.age'),'','number'),el('div',null,el('label',{for:'ts'},T('rd.sex')),el('select',{id:'ts'},el('option',{value:''},T('rd.sex_any')),el('option',{value:'FEMALE'},T('rd.sex_female')),el('option',{value:'MALE'},T('rd.sex_male'))))),
  CFG.consent_center?field('tp',T('rd.patient_id'),'P...'):null,
  el('label',null,el('input',{type:'checkbox',id:'tk'}),' '+T('rd.trial_consent')),go),out);
 go.onclick=busy(go,async()=>{const r=(await api('/trials',{condition:v('tc'),location:v('tl'),age:v('ta')||null,sex:v('ts')||null,consent:{attested:v('tk'),patient_id:v('tp')}})).trials;
  out.replaceChildren(el('div',{class:'card'},draftLbl(),removedNote(r.removed_identifiers),el('p',{class:'mut'},r.notice+' '+T('rd.trials_counts',{got:r.retrieved,shown:r.trials.length,date:r.as_of})+(r.from_cache?' '+T('rd.cached'):'')),r.error?el('p',{class:'err'},r.error):null,
   r.trials.map(t=>el('div',{class:'card',lang:'en',dir:'ltr'},el('b',null,t.title),' ',el('a',{href:t.url,target:'_blank',rel:'noopener noreferrer'},t.nct_id),el('div',{class:'mut'},[t.status,(t.phases||[]).join('/'),T('rd.ages',{min:t.minimum_age||T('rd.sex_any'),max:t.maximum_age||T('rd.sex_any')}),T('rd.sex_is',{sex:t.sex}),t.sponsor,T('rd.sites',{n:t.site_count})].filter(Boolean).join(' | ')),
    t.matching_sites.length?el('div',null,T('rd.sites_in_region',{list:t.matching_sites.map(s=>[s.facility,s.city,s.country].filter(Boolean).join(', ')).join('; ')})):null,el('details',null,el('summary',null,T('rd.eligibility')),el('blockquote',{style:'white-space:pre-wrap'},t.criteria_excerpt))))))})},
async triage(m){const r=await api('/triage');const fe=el('div');const rf=el('button',{class:'sec'},T('rd.refresh_feeds'));const kit=el('button',{class:'go'},T('rd.field_kit'));const pre=el('button',{class:'sec'},T('rd.offline_cache'));
 m.append(el('div',{class:'card'},el('h2',null,T('rd.triage_title')),legal('p','rd.triage_notice',null,{class:'draft'}),r.systems.map(s=>el('div',{class:'card',lang:'en',dir:'ltr'},el('b',null,s.name),el('p',{class:'mut'},s.origin),el('p',null,s.summary),s.reference?el('p',{class:'mut'},s.reference):null,s.links.map(u=>[el('a',{href:u,target:'_blank',rel:'noopener noreferrer'},u),' '])))),
  el('div',{class:'card'},el('h2',null,T('rd.outbreaks')),el('p',{class:'mut'},T('rd.region_line',{name:CFG.settings.region.name||T('rd.region_unset')})),rf,pre,kit,fe));
 const draw=f=>{fe.replaceChildren(el('p',{class:'mut'},T('rd.as_of',{date:f.generated_at})),Object.entries(f.status||{}).map(([k,s])=>el('p',{class:'info'},k+': '+s)),Object.entries(f.feeds||{}).map(([k,d])=>el('div',{lang:'en',dir:'ltr'},el('h3',null,k.replace('_',' ').toUpperCase()+(d.from_cache?' ('+T(d.stale?'rd.cached_stale':'rd.cached_word')+')':'')),(d.items.length?d.items:(d.latest_global||[])).map(i=>el('p',null,el('b',null,i.date+' '),i.url?el('a',{href:i.url,target:'_blank',rel:'noopener noreferrer'},i.title):i.title,el('br'),el('span',{class:'mut'},i.summary))),d.items.length?null:el('p',{class:'mut'},T('rd.nothing_matched')))))};
 rf.onclick=busy(rf,async()=>draw((await api('/feeds?refresh=1')).feeds));pre.onclick=busy(pre,async()=>{await api('/offline/prepare',{});say(T('rd.cache_ready'),'info')});kit.onclick=busy(kit,async()=>save(await api('/fieldkit',{}),'gorgon_field_kit.pdf'));
 try{draw((await api('/feeds')).feeds)}catch(e){fe.append(el('p',{class:'err'},e.message))}},
drug(m){const out=el('div');const go=el('button',{class:'go'},T('rd.look_up'));
 m.append(el('div',{class:'card'},el('h2',null,T('rd.tab_drug')),el('p',{class:'mut'},T('rd.drug_hint')+' '+CFG.interaction_notice),field('dn',T('rd.drug_name'),T('rd.drug_ph')),go),out);
 go.onclick=busy(go,async()=>{const d=(await api('/drug',{name:v('dn')})).drug;
  out.replaceChildren(el('div',{class:'card'},draftLbl(),removedNote(d.removed_identifiers),el('p',null,T('rd.rxcui',{list:d.rxcui.join(', ')||T('rd.none_found')})),
   d.candidates.length?el('p',{class:'mut'},d.candidates_note+' '+d.candidates.map(c=>c.name+' ('+c.rxcui+')').join('; ')):null,
   d.label_source?el('p',{class:'mut'},T('rd.label_line',{names:(d.label_source.brand_name||[]).join(', ')+' / '+(d.label_source.generic_name||[]).join(', '),date:d.label_source.effective_time})+' ',d.label_source.url?el('a',{href:d.label_source.url,target:'_blank',rel:'noopener noreferrer'},'DailyMed'):null):el('p',{class:'mut'},T('rd.no_label')),
   d.label_sections.map(s=>el('div',{lang:'en',dir:'ltr'},el('h3',null,s.section),el('blockquote',null,s.quote+(s.truncated?' ... ('+T('rd.truncated')+')':'')),el('p',{class:'mut'},s.source))),
   el('h3',null,T('rd.recalls')),d.recalls.length?el('ul',{lang:'en',dir:'ltr'},d.recalls.map(r=>el('li',null,r.report_date+' class '+r.classification+' ('+r.status+'): '+r.reason))):el('p',{class:'mut'},T('rd.none_found')),
   el('h3',null,T('rd.faers')),el('p',{class:'mut'},d.faers_notice),el('ul',{lang:'en',dir:'ltr'},d.faers_top_reactions.map(x=>el('li',null,x.reaction+': '+num(x.reports))))))})},
async log(m){const r=await api('/log?limit=100');const vb=el('button',{class:'sec'},T('rd.verify_log'));const res=el('div');
 vb.onclick=busy(vb,async()=>{const x=(await api('/log/verify')).verify;res.replaceChildren(el('p',{class:x.ok?'info':'err'},x.ok?T('rd.log_intact',{n:x.entries}):T('rd.log_problems',{list:x.problems.join('; ')})))});
 m.append(el('div',{class:'card'},el('h2',null,T('rd.tab_log')),el('p',{class:'mut'},T('rd.log_hint')),vb,res,
  tw(el('table',null,el('tr',null,['rd.col_ref','rd.col_when','rd.col_kind','rd.col_query','rd.col_requests','rd.col_actions'].map(h=>el('th',null,T(h)))),r.entries.map(e=>{const b=e.body||{};let rp=null;if(b.kind==='brief'||b.kind==='watch'){rp=el('button',{class:'sec'},T('rd.replay'));rp.onclick=busy(rp,async()=>{const x=(await api('/log/replay',{run_id:b.run_id})).replay;say(x.identical?T('rd.replay_same',{sha:x.replayed_result_sha256}):T('rd.replay_diff',{a:x.logged_result_sha256,b:x.replayed_result_sha256}),'info')})}
   return el('tr',null,el('td',null,e.seq),el('td',null,e.at),el('td',null,e.kind),el('td',{dir:'ltr'},JSON.stringify(b.query||b.changed||b.watchlist||'').slice(0,160)),el('td',null,(b.requests||[]).map(q=>el('div',{class:'mut'},q.source+' '+(q.status||'-')+(q.from_cache?' '+T('rd.cache_word'):'')+' '+(q.sha256||'').slice(0,12)))),el('td',null,rp))})))))},
settings(m){const s=CFG.settings;const go=el('button',{class:'go'},T('rd.save_settings'));
 m.append(el('div',{class:'card'},el('h2',null,T('rd.tab_settings')),field('se',T('rd.set_email'),''),field('sk',T('rd.set_key'),s.ncbi_api_key?T('rd.set_key_ph'):''),field('sr',T('rd.set_reliefweb'),''),
  el('div',{class:'row'},field('sn',T('rd.set_region'),T('rd.set_region_ph')),field('si',T('rd.set_iso'),'HT'),field('st',T('rd.set_terms'),T('rd.set_terms_ph'))),
  el('label',null,el('input',{type:'checkbox',id:'so'}),' '+T('rd.set_offline')),el('label',null,el('input',{type:'checkbox',id:'sa'}),' '+T('rd.set_auto')),go,
  el('h3',null,T('rd.sources')),tw(el('table',null,Object.entries(CFG.sources).map(([k,x])=>el('tr',null,el('td',null,el('a',{href:x.docs,target:'_blank',rel:'noopener noreferrer'},x.name)),el('td',{class:'mut'},(x.rate_limit||'')+' '+(x.interaction_api||'')),el('td',{class:'mut'},x.live_check?x.live_check.date+': '+x.live_check.result:''))))),
  el('p',{class:'mut'},T(CFG.storage.encrypted?'rd.storage_enc':'rd.storage_plain'))));
 $('#se').value=s.contact_email||'';$('#sr').value=s.reliefweb_appname||'';$('#sn').value=s.region.name||'';$('#si').value=s.region.iso2||'';$('#st').value=(s.region.terms||[]).filter(t=>t!==s.region.name).join(', ');$('#so').checked=!!s.offline;$('#sa').checked=!!s.auto_refresh;
 go.onclick=busy(go,async()=>{const b={contact_email:v('se'),reliefweb_appname:v('sr'),region:{name:v('sn'),iso2:v('si'),terms:v('st')},offline:v('so'),auto_refresh:v('sa')};if(v('sk'))b.ncbi_api_key=v('sk');await api('/settings',b);CFG=await api('/config');say(T('rd.saved'),'info')})}};
async function show(k){tabs(k);const m=$('#main');m.replaceChildren();try{await VIEWS[k](m)}catch(e){say(e.message)}}
function chromeRd(){document.title=T('rd.page_title');$('#title').textContent=T('rd.title');$('#lbl').replaceChildren(legal('span','rd.draft_label'));$('#back').textContent=T('rd.back');
 const dl=$('#draftLang');dl.hidden=I18N.status==='source'||I18N.status==='reviewed';dl.replaceChildren(...(dl.hidden?[]:[legal('span','app.lang.draft_banner')]));
 $('#rules').replaceChildren(...[1,2,3,4,5].map(n=>legal('li','rd.rule'+n)))}
(async()=>{chromeRd();try{const s=await api('/session');CSRF=s.csrf;$('#who').textContent=T('rd.signed_in',{name:s.user.display});CFG=await api('/config');
 if(CFG.contact_email_missing)say(T('rd.set_email_hint'),'info');show('brief')}
 catch(e){$('#main').replaceChildren(el('div',{class:'card'},el('p',null,e.message),el('a',{href:'/'},T('rd.open_sign_in'))))}})();
</script></body></html>"""


def render_page(cookie_lang="", accept_language=""):
    """The Research Desk page in the person's language (catalog i18n/<locale>.json; English when i18n is missing)."""
    try:
        i18n = importlib.import_module("i18n")
    except Exception:
        i18n = None
    if i18n is None:
        return PAGE.replace("__HTML_ATTRS__", 'lang="en" dir="ltr"').replace("__I18N_DATA__", "{}").replace(
            "__I18N_JS__", "").replace("__FAVICON__", "data:,")
    avail = i18n.available()
    loc = i18n.canonical(cookie_lang or "")
    if not loc or loc not in avail:
        loc = i18n.negotiate(accept_language, avail)
    b = i18n.bundle(loc, scope="rd")
    fav = "data:,"
    try:
        ca = sys.modules.get("clinic_app") or importlib.import_module("clinic_app")
        fav = ca.favicon_uri()
    except Exception:
        pass
    return (PAGE.replace("__FAVICON__", fav).replace("__HTML_ATTRS__", i18n.html_attrs(b["locale"])).replace("__I18N_DATA__", i18n.script_json(b))
            .replace("__I18N_JS__", i18n.js_runtime()))


# ======================================================================================================
# 13. CLI (autopilot hook, live checks, fixture recording)
# ======================================================================================================
def _data_root(arg):
    if arg:
        return os.path.realpath(arg)
    ca = optional("clinic_app")
    if ca is not None and hasattr(ca, "default_data_root"):
        return os.path.realpath(ca.default_data_root())
    env = os.environ.get("STARK_COVENANT_DATA") or os.environ.get("GORGON_COVENANT_DATA")
    if env:
        return os.path.realpath(env)
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    new, old = os.path.join(docs, "Stark_Covenant_Data"), os.path.join(docs, "Gorgon_Covenant_Data")   # old: pre-1.1.0
    return old if not os.path.isdir(new) and os.path.isdir(old) else new


def _cli_vault():
    """security_vault auto-unlock (this PC's share + USB stick), like clinic_app. None when it cannot open."""
    sv = optional("security_vault")
    if sv is None or not isinstance(getattr(sv, "Vault", None), type):
        return None, "security_vault not installed"
    try:
        root = os.environ.get("GORGON_COVENANT_VAULT") or None
        v = sv.Vault(root) if root else sv.Vault()
        if not v.exists():
            return None, "no vault on this computer"
        v.unlock()
        return v, "unlocked"
    except Exception as e:
        return None, "vault locked (%s)" % str(e)[:160]


def cli_refresh(args):
    root = _data_root(args.data)
    summary = {"tool": "research_desk", "at": now_iso(), "data_root": root, "stores": []}
    for sub, needs_vault in (("research", True), ("research_no_vault", False)):
        d = os.path.join(root, sub)
        if not os.path.isdir(d):
            continue
        vault, why = (None, "not needed")
        if needs_vault:
            vault, why = _cli_vault()
            if vault is None:
                summary["stores"].append({"store": sub, "status": "skip", "reason": why + "; the app's own daily "
                                          "refresh runs while Stark Covenant is open"})
                continue
        try:
            desk = Desk(d, vault=vault)
            if desk.settings().get("offline"):
                summary["stores"].append({"store": sub, "status": "skip", "reason": "offline mode"})
                continue
            res = desk.refresh_due("autopilot", force=args.force)
            summary["stores"].append({"store": sub, "status": "ok", "refreshed": len(res),
                                      "new_alerts": sum(len(r.get("alerts", [])) for r in res),
                                      "errors": [r["error"] for r in res if r.get("error")]})
        except Exception as e:
            summary["stores"].append({"store": sub, "status": "error", "reason": "%s: %s" % (type(e).__name__, e)})
        finally:
            if vault is not None:
                try:
                    vault.lock()
                except Exception:
                    pass
    if not summary["stores"]:
        summary["stores"].append({"status": "skip", "reason": "no Research Desk data yet"})
    print(json.dumps(summary, indent=2))
    return 2 if any(s.get("status") == "error" for s in summary["stores"]) else 0


class RecordingTransport(HttpTransport):
    """Wraps the real transport and writes every response to OUT_DIR (fixtures for the unit tests)."""

    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.entries = []
        os.makedirs(out_dir, exist_ok=True)

    def get(self, url, headers, timeout=30):
        status, ctype, body = HttpTransport.get(self, url, headers, timeout)
        name = "%03d_%s" % (len(self.entries) + 1, urllib.parse.urlsplit(url).hostname.split(".")[-2])
        ext = ".xml" if body[:5] in (b"<?xml", b"<rss ") or "xml" in ctype else ".json"
        with open(os.path.join(self.out_dir, name + ext), "wb") as f:
            f.write(body)
        self.entries.append({"url": redact_url(url), "file": name + ext, "status": status, "content_type": ctype,
                             "sha256": sha256_hex(body), "bytes": len(body), "recorded_at": now_iso(),
                             "live": True})
        return status, ctype, body


def cli_record(args):
    """Record one real response per feature into tests/fixtures (network; run by a person, not by tests)."""
    import tempfile
    rec = RecordingTransport(args.out)
    tmp = tempfile.mkdtemp(prefix="research_record_")
    desk = Desk(tmp, transport=rec)
    desk.update_settings({"region": {"name": "Switzerland", "iso2": "CH"}, "cache_ttl_hours": 1}, "recorder")
    scenarios = []

    def step(name, fn):
        try:
            fn()
            scenarios.append({"scenario": name, "ok": True})
        except Exception as e:
            scenarios.append({"scenario": name, "ok": False, "error": "%s: %s" % (type(e).__name__, e)})
    step("brief_hcq", lambda: desk.evidence_brief({"p": "COVID-19", "i": "hydroxychloroquine", "o": "mortality"},
                                                  "recorder"))
    step("brief_gdm", lambda: desk.evidence_brief({"p": "gestational diabetes", "i": "metformin", "c": "insulin"},
                                                  "recorder"))
    step("trials", lambda: desk.trials("gestational diabetes", "recorder", location="United States"))
    step("feeds", lambda: desk.feeds("recorder"))
    step("drug", lambda: desk.drug_reference("metformin", "recorder"))
    for s in desk.catalogue.get("triage_systems", []):
        for ln in s["links"]:
            try:
                req = urllib.request.Request(ln, headers={"User-Agent": desk.fetcher.user_agent()}, method="GET")
                with urllib.request.urlopen(req, timeout=20) as r:
                    st = r.status
            except urllib.error.HTTPError as e:
                st = e.code
            except Exception as e:
                st = "error %s" % type(e).__name__
            scenarios.append({"link_check": ln, "status": st})
    man = {"recorded_at": now_iso(), "note": "Real responses recorded by `python research_desk.py record-fixtures`. "
           "Tests serve them by URL (email/api_key redacted). Re-record when URL building changes.",
           "entries": rec.entries, "scenarios": scenarios}
    with open(os.path.join(args.out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(man, f, indent=1)
    print(json.dumps(scenarios, indent=1))
    return 0


def cli_check(args):
    import tempfile
    desk = Desk(tempfile.mkdtemp(prefix="research_check_"))
    rows = []
    checks = [("pubmed", eutils_url("esearch.fcgi", desk.settings(), {"db": "pubmed", "term": "cholera",
                                                                       "retmode": "json", "retmax": 1})),
              ("europepmc", epmc_url("cholera", 1)), ("clinicaltrials", CTGOV + "?" + _q({"query.cond": "cholera",
                                                                                        "pageSize": 1})),
              ("who_don", who_don_url(1)), ("who_iris", iris_url("cholera", 1)), ("cdc_travel", CDC_RSS),
              ("openfda", OPENFDA + "label.json?limit=1"), ("rxnav", RXNAV + "rxcui.json?name=metformin"),
              ("rxnav_interaction(retired)", RXNAV + "interaction/list.json?rxcuis=6809+11289"),
              ("reliefweb", reliefweb_url(desk.settings().get("reliefweb_appname") or "gorgon-covenant", "Haiti", 1))]
    for name, url in checks:
        t = time.time()
        try:
            st, _ct, body = HttpTransport().get(url, {"User-Agent": desk.fetcher.user_agent()}, 30)
            rows.append({"source": name, "status": st, "seconds": round(time.time() - t, 1),
                         "bytes": len(body), "snippet": body[:120].decode("utf-8", "replace")})
        except Exception as e:
            rows.append({"source": name, "status": "error", "error": "%s: %s" % (type(e).__name__, e)})
    print(json.dumps(rows, indent=1))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="research_desk.py", description="Stark Covenant Research Desk")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("refresh-watchlists", help="autopilot hook: refresh due watch-lists")
    p.add_argument("--data")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("record-fixtures")
    p.add_argument("out")
    sub.add_parser("check-sources")
    p = sub.add_parser("verify-log")
    p.add_argument("--data")
    p = sub.add_parser("field-kit")
    p.add_argument("out")
    p.add_argument("--data")
    p.add_argument("--lang")
    sub.add_parser("mount-help")
    a = ap.parse_args(argv)
    if a.cmd == "refresh-watchlists":
        return cli_refresh(a)
    if a.cmd == "record-fixtures":
        return cli_record(a)
    if a.cmd == "check-sources":
        return cli_check(a)
    if a.cmd in ("verify-log", "field-kit"):
        root = _data_root(a.data)
        d = os.path.join(root, "research_no_vault")
        vault = None
        if os.path.isdir(os.path.join(root, "research")):
            vault, why = _cli_vault()
            if vault is not None:
                d = os.path.join(root, "research")
            else:
                print("encrypted research store is locked (%s); using research_no_vault" % why, file=sys.stderr)
        desk = Desk(d, vault=vault)
        if a.cmd == "verify-log":
            res = desk.log.verify()
            print(json.dumps(res, indent=1))
            return 0 if res["ok"] else 1
        data = desk.field_kit_pdf("cli", a.lang)
        with open(a.out, "wb") as f:
            f.write(data)
        print("wrote %s (%d bytes)" % (a.out, len(data)))
        return 0
    if a.cmd == "mount-help":
        doc = __doc__ or ""
        print(doc[doc.index("MOUNTING"):doc.index("CLI\n")])
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
