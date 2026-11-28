#!/usr/bin/env python3
"""Stark Covenant - practice suite (practice_suite.py). Standard library only, Python 3.10+.

Scheduling with a calendar-file (.ics) export, outcome measures (PHQ-9, GAD-7, AUDIT-C, PCL-5, WHO-5), intake and consent
forms, treatment plans, billing with superbills, full-chart export, the access report, the Today worklist, the
practice dashboard and appointment reminders (a text to copy and a daily call list; nothing is sent from here). Note templates and supervision co-signature live in clinic_app (they
are part of the note sign-off lock); this module serves their settings.

Mounted by clinic_app.CovenantApp like the other optional modules: mount() adds /api/pm/... routes to clinic_app's
ROUTES, so sign-in, CSRF, Origin, Host and LAN checks stay clinic_app's. Every write goes through the clinic's
journaled, encrypted, audited Store. Nothing here uses the network.

Where the data lives (sealed by the security vault in normal mode; plain only in DEMO mode):
  charts/P*.bin          measures and treatment plans, inside the patient's chart
  sys/sched-YYYY-MM.bin  the appointments of one calendar month; sys/sched-index.bin: months per patient
  sys/billing.bin        billing profile, fee schedule, invoices and payments
  sys/reminders.bin      the clinic's reminder wording
  sys/forms.bin          the clinic's form templates (versioned); completed forms live in the chart (charts/P*.bin)
  sys/worklist.bin       the Today index (draft notes, co-signatures waiting, plan reviews); rebuilt from the
                         charts when it is missing

Honest limits
  * Not a claims system: no clearinghouse, eligibility check or remittance. Superbills are printed for the client
    or the clinic to submit by hand.
  * CPT, HCPCS and ICD-10 codes are typed by the clinic and checked for FORMAT only. No code set and no code
    description is bundled (their owners license them); descriptions are the clinic's own words.
  * PHQ-9 and GAD-7 results are screening and monitoring aids, not a diagnosis. Every item must be answered (no
    prorating). Item text is the published English wording; use an officially validated translation elsewhere.
  * Appointment times are the clinic's local wall-clock time (no time-zone conversion); .ics files use floating
    local times and are written unencrypted, so client names default to initials.
  * A form signature is a typed name + date witnessed by a staff PIN, sealed by the clinic key: an internal record
    of consent, not a qualified electronic signature.
  * PDF exports need polyglot_engine; without it they are plain text files.
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import importlib
import io
import json
import re
import sys
import traceback
from datetime import date, datetime, timedelta

LOG_PREFIX = "practice_suite"
EXPORT_FORMAT = "stark-covenant-chart"
EXPORT_VERSION = 1

# ======================================================================================================
# Outcome measures: published wording and scoring, nothing added
# ======================================================================================================
FREQ_OPTIONS = ("Not at all", "Several days", "More than half the days", "Nearly every day")
DIFFICULTY_Q = ("If you checked off any problems, how difficult have these problems made it for you to do your "
                "work, take care of things at home, or get along with other people?")
DIFFICULTY_OPTIONS = ("Not difficult at all", "Somewhat difficult", "Very difficult", "Extremely difficult")
PHQ_PERMISSION = ("Developed by Drs. Robert L. Spitzer, Janet B.W. Williams, Kurt Kroenke and colleagues, with an "
                  "educational grant from Pfizer Inc. No permission required to reproduce, translate, display or "
                  "distribute.")
INSTRUMENTS = {
    "phq9": {
        "name": "PHQ-9", "title": "Patient Health Questionnaire (PHQ-9)",
        "stem": "Over the last 2 weeks, how often have you been bothered by any of the following problems?",
        "items": (
            "Little interest or pleasure in doing things",
            "Feeling down, depressed, or hopeless",
            "Trouble falling or staying asleep, or sleeping too much",
            "Feeling tired or having little energy",
            "Poor appetite or overeating",
            "Feeling bad about yourself — or that you are a failure or have let yourself or your family down",
            "Trouble concentrating on things, such as reading the newspaper or watching television",
            "Moving or speaking so slowly that other people could have noticed? Or the opposite — being so "
            "fidgety or restless that you have been moving around a lot more than usual",
            "Thoughts that you would be better off dead or of hurting yourself in some way"),
        "options": FREQ_OPTIONS, "difficulty": DIFFICULTY_Q, "difficulty_options": DIFFICULTY_OPTIONS,
        # Kroenke, Spitzer & Williams 2001: 5, 10, 15 and 20 are the cut-points of mild, moderate, moderately
        # severe and severe depression severity
        "bands": ((0, 4, "none_minimal"), (5, 9, "mild"), (10, 14, "moderate"), (15, 19, "moderately_severe"),
                  (20, 27, "severe")),
        "flag_item": 9,
        "source": "Kroenke K, Spitzer RL, Williams JBW. The PHQ-9: validity of a brief depression severity "
                  "measure. J Gen Intern Med. 2001;16(9):606-613.",
        "permission": PHQ_PERMISSION},
    "gad7": {
        "name": "GAD-7", "title": "Generalized Anxiety Disorder 7-item (GAD-7)",
        "stem": "Over the last 2 weeks, how often have you been bothered by the following problems?",
        "items": (
            "Feeling nervous, anxious, or on edge",
            "Not being able to stop or control worrying",
            "Worrying too much about different things",
            "Trouble relaxing",
            "Being so restless that it is hard to sit still",
            "Becoming easily annoyed or irritable",
            "Feeling afraid, as if something awful might happen"),
        "options": FREQ_OPTIONS, "difficulty": DIFFICULTY_Q, "difficulty_options": DIFFICULTY_OPTIONS,
        # Spitzer, Kroenke, Williams & Loewe 2006: 5, 10 and 15 are the cut-points of mild, moderate and severe
        "bands": ((0, 4, "minimal"), (5, 9, "mild"), (10, 14, "moderate"), (15, 21, "severe")),
        "flag_item": None,
        "source": "Spitzer RL, Kroenke K, Williams JBW, Löwe B. A brief measure for assessing generalized "
                  "anxiety disorder: the GAD-7. Arch Intern Med. 2006;166(10):1092-1097.",
        "permission": PHQ_PERMISSION},
}


class PracticeError(Exception):
    def __init__(self, status, msg):
        Exception.__init__(self, msg)
        self.status, self.msg = status, msg


AUDIT_PERMISSION = ("AUDIT: World Health Organization (Babor TF, Higgins-Biddle JC, Saunders JB, Monteiro MG. The "
                    "Alcohol Use Disorders Identification Test, 2nd ed. WHO/MSD/MSB/01.6a, 2001), which may be "
                    "reproduced freely. AUDIT-C (questions 1-3): Bush K, Kivlahan DR, McDonell MB, Fihn SD, "
                    "Bradley KA. Arch Intern Med. 1998;158(16):1789-1795; available for use in the public domain "
                    "(NIH/NLM Common Data Elements).")
PCL_PERMISSION = ("Weathers FW, Litz BT, Keane TM, Palmieri PA, Marx BP, Schnurr PP. The PTSD Checklist for DSM-5 "
                  "(PCL-5). Scale available from the National Center for PTSD at www.ptsd.va.gov. Developed by "
                  "staff at VA's National Center for PTSD; in the public domain and not copyrighted.")
INSTRUMENTS["auditc"] = {
    "name": "AUDIT-C", "title": "AUDIT-C (Alcohol Use Disorders Identification Test, questions 1-3)",
    "stem": "Please choose the answer that is correct for you.",
    "items": ("How often do you have a drink containing alcohol?",
              "How many drinks containing alcohol do you have on a typical day when you are drinking?",
              "How often do you have six or more drinks on one occasion?"),
    "options": None,
    "item_options": (("Never", "Monthly or less", "2 to 4 times a month", "2 to 3 times a week",
                      "4 or more times a week"),
                     ("1 or 2", "3 or 4", "5 or 6", "7, 8, or 9", "10 or more"),
                     ("Never", "Less than monthly", "Monthly", "Weekly", "Daily or almost daily")),
    "difficulty": None, "difficulty_options": None,
    # question 1 answered Never: questions 2 and 3 are skipped and score 0 (WHO AUDIT manual)
    "skip_if_first_zero": True,
    # Bradley KA et al. Alcohol Clin Exp Res. 2007;31(7):1208-1217: positive at 4 or more for men and 3 or more
    # for women (the US VA cut-points). The clinician chooses which applies; "bands" lists the men's cut-point.
    "groups": ("women", "men"),
    "bands_by_group": {"women": ((0, 2, "screen_negative"), (3, 12, "screen_positive")),
                       "men": ((0, 3, "screen_negative"), (4, 12, "screen_positive"))},
    "bands": ((0, 3, "screen_negative"), (4, 12, "screen_positive")),
    "flag_item": None,
    "source": "Bush K et al. The AUDIT alcohol consumption questions (AUDIT-C). Arch Intern Med. "
              "1998;158(16):1789-1795. Bradley KA et al. Alcohol Clin Exp Res. 2007;31(7):1208-1217.",
    "permission": AUDIT_PERMISSION}
INSTRUMENTS["pcl5"] = {
    "name": "PCL-5", "title": "PTSD Checklist for DSM-5 (PCL-5)",
    "stem": ("Below is a list of problems that people sometimes have in response to a very stressful experience. "
             "Please read each problem carefully and then choose one of the answers to indicate how much you have "
             "been bothered by that problem in the past month. In the past month, how much were you bothered by:"),
    "items": (
        "Repeated, disturbing, and unwanted memories of the stressful experience?",
        "Repeated, disturbing dreams of the stressful experience?",
        "Suddenly feeling or acting as if the stressful experience were actually happening again (as if you were "
        "actually back there reliving it)?",
        "Feeling very upset when something reminded you of the stressful experience?",
        "Having strong physical reactions when something reminded you of the stressful experience (for example, "
        "heart pounding, trouble breathing, sweating)?",
        "Avoiding memories, thoughts, or feelings related to the stressful experience?",
        "Avoiding external reminders of the stressful experience (for example, people, places, conversations, "
        "activities, objects, or situations)?",
        "Trouble remembering important parts of the stressful experience?",
        "Having strong negative beliefs about yourself, other people, or the world (for example, having thoughts "
        "such as: I am bad, there is something seriously wrong with me, no one can be trusted, the world is "
        "completely dangerous)?",
        "Blaming yourself or someone else for the stressful experience or what happened after it?",
        "Having strong negative feelings such as fear, horror, anger, guilt, or shame?",
        "Loss of interest in activities that you used to enjoy?",
        "Feeling distant or cut off from other people?",
        "Trouble experiencing positive feelings (for example, being unable to feel happiness or have loving "
        "feelings for people close to you)?",
        "Irritable behavior, angry outbursts, or acting aggressively?",
        "Taking too many risks or doing things that could cause you harm?",
        "Being \"superalert\" or watchful or on guard?",
        "Feeling jumpy or easily startled?",
        "Having difficulty concentrating?",
        "Trouble falling or staying asleep?"),
    "options": ("Not at all", "A little bit", "Moderately", "Quite a bit", "Extremely"),
    "difficulty": None, "difficulty_options": None,
    # National Center for PTSD: "a PCL-5 cutoff score between 31-33 is indicative of probable PTSD". 31-32 is its
    # own band, so the clinic sees when a result depends on the cut-point it uses.
    "bands": ((0, 30, "below_cutoff"), (31, 32, "near_cutoff"), (33, 80, "probable_ptsd")),
    # DSM-5 clusters (cluster, first item, last item, items needed); an item rated 2 (Moderately) or higher counts
    # as endorsed. Provisional DSM-5 pattern: at least 1 B, 1 C, 2 D and 2 E items.
    "clusters": (("B", 1, 5, 1), ("C", 6, 7, 1), ("D", 8, 14, 2), ("E", 15, 20, 2)),
    "flag_item": None,
    "source": "Weathers FW et al. The PTSD Checklist for DSM-5 (PCL-5). National Center for PTSD, 2013. Bovin MJ "
              "et al. Psychol Assess. 2016;28(11):1379-1391.",
    "permission": PCL_PERMISSION}
WHO5_PERMISSION = ("WHO-5 Well-Being Index (1998 version), Psychiatric Research Unit, WHO Collaborating Centre in Mental "
                   "Health, Frederiksborg General Hospital. The WHO-5 is free of charge and may be used without "
                   "permission.")
INSTRUMENTS["who5"] = {
    "name": "WHO-5", "title": "WHO-5 Well-Being Index",
    "stem": ("Please indicate for each of the five statements which is closest to how you have been feeling over the "
             "last two weeks. Notice that higher numbers mean better well-being. Over the last two weeks:"),
    "items": ("I have felt cheerful and in good spirits",
              "I have felt calm and relaxed",
              "I have felt active and vigorous",
              "I woke up feeling fresh and rested",
              "My daily life has been filled with things that interest me"),
    # an answer's index is its score: At no time 0 ... All of the time 5
    "options": ("At no time", "Some of the time", "Less than half of the time", "More than half of the time",
                "Most of the time", "All of the time"),
    "difficulty": None, "difficulty_options": None,
    # WHO-5 scoring: raw score 0-25 (x4 = percentage); a raw score below 13 indicates poor well-being and is an
    # indication for testing for depression. Higher is better (the other measures here are the other way round).
    "bands": ((0, 12, "who5_low"), (13, 25, "who5_not_low")),
    "percent": 4,
    "flag_item": None,
    "source": "Topp CW, Ostergaard SD, Sondergaard S, Bech P. The WHO-5 Well-Being Index: a systematic review of "
              "the literature. Psychother Psychosom. 2015;84(3):167-176.",
    "permission": WHO5_PERMISSION}


def item_options(spec):
    """The answer choices of every item (an answer's index is its score)."""
    return list(spec.get("item_options") or [spec["options"]] * len(spec["items"]))


def score(instrument, answers, group=None):
    """Exact published scoring: the sum of the item scores, mapped to the published band.
    -> {"total", "band", "max", "flag"}, plus "group" (sex-specific AUDIT-C cut-point) and "clusters" /
    "criteria_met" (PCL-5). flag = PHQ-9 item 9 answered above 0 (needs the clinician's follow-up)."""
    spec = INSTRUMENTS.get(instrument)
    if spec is None:
        raise PracticeError(400, "Unknown measure.")
    opts = item_options(spec)
    n = len(opts)
    if not isinstance(answers, (list, tuple)) or len(answers) != n:
        raise PracticeError(400, "Answer all %d items of the %s (scores are not prorated)." % (n, spec["name"]))
    answers = list(answers)
    if spec.get("skip_if_first_zero") and answers[0] == 0 and not isinstance(answers[0], bool):
        if any(isinstance(a, bool) or a not in (None, 0) for a in answers[1:]):
            raise PracticeError(400, "When question 1 is answered Never, the other questions score 0.")
        answers = [0] * n
    vals = []
    for a, o in zip(answers, opts):
        k = len(o) - 1
        if isinstance(a, bool) or not isinstance(a, int) or not 0 <= a <= k:
            raise PracticeError(400, "Each answer must be a whole number from 0 to %d." % k)
        vals.append(a)
    total = sum(vals)
    out = {"total": total, "max": sum(len(o) - 1 for o in opts)}
    bands = spec["bands"]
    if spec.get("bands_by_group"):
        if group not in spec["groups"]:
            raise PracticeError(400, "Choose which cut-point applies (%s)." % ", ".join(spec["groups"]))
        bands = spec["bands_by_group"][group]
        out["group"] = group
    out["band"] = next(b for lo, hi, b in bands if lo <= total <= hi)
    flag_item = spec.get("flag_item")
    out["flag"] = bool(flag_item and vals[flag_item - 1] > 0)
    if spec.get("clusters"):
        out["clusters"] = {c: sum(vals[lo - 1:hi]) for c, lo, hi, _need in spec["clusters"]}
        out["criteria_met"] = all(sum(1 for v in vals[lo - 1:hi] if v >= 2) >= need
                                  for _c, lo, hi, need in spec["clusters"])
    if spec.get("percent"):
        out["percent"] = total * spec["percent"]              # WHO-5: raw score x 4 = percentage score
    return out


def public_instruments():
    out = {}
    for k, v in INSTRUMENTS.items():
        opts = item_options(v)
        out[k] = {"name": v["name"], "title": v["title"], "stem": v["stem"], "items": list(v["items"]),
                  "options": list(v["options"] or opts[0]), "item_options": [list(o) for o in opts],
                  "difficulty": v["difficulty"], "difficulty_options": list(v["difficulty_options"] or ()),
                  "bands": [list(b) for b in v["bands"]], "flag_item": v["flag_item"], "source": v["source"],
                  "permission": v["permission"], "max": sum(len(o) - 1 for o in opts),
                  "groups": list(v.get("groups") or ()), "skip_if_first_zero": bool(v.get("skip_if_first_zero")),
                  "bands_by_group": {g: [list(b) for b in bs] for g, bs in (v.get("bands_by_group") or {}).items()},
                  "clusters": [list(c) for c in v.get("clusters") or ()], "percent": v.get("percent") or 0}
    return out


# ======================================================================================================
# Note templates (sections the browser composes into the note text; the signature covers that text)
# ======================================================================================================
NOTE_SECTIONS = {"soap": ("subjective", "objective", "assessment", "plan"),
                 "dap": ("data", "assessment", "plan"),
                 "birp": ("behavior", "intervention", "response", "plan"),
                 "progress": ("summary", "interventions", "progress", "plan"),
                 # biopsychosocial intake assessment and mental status examination: headings only, written by the
                 # clinician (no wording, scoring or conclusion is suggested)
                 "intake": ("presenting", "history", "mh_history", "substance", "medical", "family", "social",
                            "mental_status", "risk", "strengths", "impressions", "plan"),
                 "mse": ("appearance", "behavior", "speech", "mood", "affect", "thought_process", "thought_content",
                         "perception", "cognition", "insight", "judgment")}

# ======================================================================================================
# Scheduling
# ======================================================================================================
APPT_STATUSES = ("scheduled", "arrived", "completed", "no_show", "cancelled", "late_cancel")
APPT_BLOCKING = ("scheduled", "arrived", "completed")         # a booking that occupies the clinician's time
APPT_KINDS = ("intake", "session", "follow_up", "assessment", "review", "other")
MAX_SERIES = 52
MAX_RANGE_DAYS = 62
TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
SCHED_INDEX = "sys/sched-index.bin"
WORKLIST = "sys/worklist.bin"
BILLING = "sys/billing.bin"

ICS_NAMES = ("initials", "full", "none")
ICS_MAX_DAYS = 366


def ics_text(s):
    """RFC 5545 TEXT value: backslash, semicolon, comma and newlines escaped."""
    s = str(s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    return s.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "")


def ics_fold(line):
    """RFC 5545 3.1: content lines longer than 75 octets continue on the next line after CRLF + one space,
    never splitting a UTF-8 character."""
    out, cur, size = [], "", 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        if size + n > (75 if not out else 74):
            out.append(cur)
            cur, size = "", 0
        cur += ch
        size += n
    out.append(cur)
    return "\r\n ".join(out)


# ======================================================================================================
# Dashboard and reminders
# ======================================================================================================
DASH_DEFAULT_DAYS = 90
DASH_MAX_DAYS = 365
DASH_MEASURES = ("phq9", "gad7")
DASH_CHANGE = 5                  # PHQ-9 / GAD-7: a change of 5 points is the usual clinically meaningful step
REMINDERS = "sys/reminders.bin"
REMINDER_OUTCOMES = ("called", "texted", "left_message", "no_answer", "wrong_number")
REMINDER_DEFAULT = ("Hello [name], this is a reminder of your appointment with [clinician] on [date] at [time]. "
                    "To change it, please call [practice] on [phone].")

# ======================================================================================================
# Letters, waitlist, groups, sliding scale, coverage (CMS-1500 field sheet)
# ======================================================================================================
LETTER_KINDS = ("school", "work", "referral", "twimc")
LETTER_SEALED = ("kind", "recipient", "subject", "body", "date", "author", "signed_by", "signed_by_name", "signed_at")
WAITLIST = "sys/waitlist.bin"
WAIT_STATUSES = ("waiting", "booked", "removed")
MAX_WAITING = 500
GROUPS = "sys/groups.bin"
MAX_GROUP = 30
MAX_TIERS = 10
COVERAGE_FIELDS = {"payer_name": 120, "payer_address": 300, "payer_id": 20, "member_id": 40, "group": 40,
                   "insured_name": 120, "insured_address": 300}
PLAN_TYPES = ("medicare", "medicaid", "tricare", "champva", "group", "feca", "other")
RELATIONSHIPS = ("self", "spouse", "child", "other")

# ======================================================================================================
# Intake and consent forms
# ======================================================================================================
FORMS = "sys/forms.bin"
FORM_KINDS = ("intake", "consent", "other")
FIELD_TYPES = ("text", "textarea", "date", "number", "yesno", "choice", "multi", "statement")
SIGNER_ROLES = ("client", "guardian", "representative")
FIELD_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
MAX_FORM_FIELDS = 80
MAX_FIELD_OPTIONS = 20
MAX_TEMPLATES = 200
FORM_SEALED = ("template", "template_version", "name", "kind", "intro", "statement", "fields", "answers",
               "signer_name", "signer_role", "signed_date", "witness", "witness_name", "at", "version")

# ======================================================================================================
# Treatment plans
# ======================================================================================================
PLAN_STATUSES = ("active", "completed", "discontinued")
GOAL_STATUSES = ("not_started", "in_progress", "met", "not_met", "discontinued")
MAX_GOALS = 20
MAX_OBJECTIVES = 20

# ======================================================================================================
# Billing: codes are typed by the clinic and checked for format only
# ======================================================================================================
CODE_RE = re.compile(r"^(?:\d{4}[0-9FTU]|[A-V]\d{4})$")          # CPT cat. I/II/III/PLA, HCPCS level II
MODIFIER_RE = re.compile(r"^[0-9A-Z]{2}$")
ICD10_RE = re.compile(r"^[A-Z][0-9][0-9A-Z](?:\.[0-9A-Z]{1,4})?$")
POS_RE = re.compile(r"^\d{2}$")
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
PAY_KINDS = ("payment", "refund", "write_off")
PAY_METHODS = ("cash", "card", "check", "transfer", "insurance", "other")
MAX_CENTS = 100000000                                              # 1,000,000.00 per amount
MAX_LINES = 30
MAX_DX = 12
PROFILE_FIELDS = {"practice_name": 120, "address": 300, "phone": 40, "email": 120, "tax_id": 30, "npi": 10,
                  "currency": 3, "invoice_prefix": 8, "footer": 500}


def npi_valid(npi):
    """CMS NPI check digit: Luhn over '80840' + the 10 digits (the 80840 prefix is the card-issuer constant)."""
    s = str(npi or "")
    if not re.fullmatch(r"\d{10}", s):
        return False
    total = 0
    for i, ch in enumerate(reversed("80840" + s)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def norm_icd10(code):
    """'f411' / 'F41.1' -> 'F41.1'. Format check only (letter, digit, alphanumeric, optional dot + 1-4)."""
    s = re.sub(r"\s+", "", str(code or "")).upper()
    if "." not in s and len(s) > 3:
        s = s[:3] + "." + s[3:]
    if not ICD10_RE.match(s):
        raise PracticeError(400, "Diagnosis code %s is not in ICD-10 format (for example F41.1)." % (s[:12] or "?"))
    return s


def norm_code(code):
    s = re.sub(r"\s+", "", str(code or "")).upper()
    if not CODE_RE.match(s):
        raise PracticeError(400, "Service code %s is not a 5-character CPT or HCPCS code (for example 90837)."
                            % (s[:12] or "?"))
    return s


def cents(v, field="amount", allow_zero=True):
    """'150', '150.5', 150.5 -> 15050. Up to 2 decimals, no negatives, at most 1,000,000.00."""
    if isinstance(v, bool) or v is None:
        raise PracticeError(400, "%s is required." % field)
    s = str(v).strip().replace(" ", "")
    if isinstance(v, float):
        s = ("%.2f" % v) if abs(v * 100 - round(v * 100)) < 1e-6 else s
    if not re.fullmatch(r"\d{1,7}(?:\.\d{1,2})?", s):
        raise PracticeError(400, "%s must be a number with at most 2 decimals." % field)
    whole, _, frac = s.partition(".")
    c = int(whole) * 100 + int((frac + "00")[:2])
    if c > MAX_CENTS or (c == 0 and not allow_zero):
        raise PracticeError(400, "%s is out of range." % field)
    return c


def money_text(c, cur):
    sign = "-" if c < 0 else ""
    c = abs(int(c))
    return "%s%s %d.%02d" % (sign, cur or "", c // 100, c % 100)


def _csv_cell(v):
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s       # no spreadsheet formulas


# ======================================================================================================
# The suite
# ======================================================================================================
class PracticeSuite:
    def __init__(self, clinic, host=None):
        self.c = clinic
        self.H = host or _host_module(clinic)

    # --- small helpers ------------------------------------------------------------------------------
    @property
    def store(self):
        return self.c.store

    def _req(self, user, *perms):
        have = self.c.perms(user)
        if not any(p in have for p in perms):
            raise self.H.HttpError(403, "Your role does not allow this (%s)." % perms[0])

    def _text(self, v, n, field, multiline=False):
        return self.H._clean(v, n, multiline=multiline, field=field)

    def _day(self, v, field="date", required=False, not_future=False):
        return self.H._date(v, field, required=required, not_future=not_future)

    def _choice(self, v, allowed, field, default=None):
        if v in (None, "") and default is not None:
            return default
        if v not in allowed:
            raise PracticeError(400, "%s must be one of: %s" % (field, ", ".join(allowed)))
        return v

    def _patient(self, pid):
        self.c._chart_rel(pid)                                    # id format check
        p = self.c._index()["patients"].get(pid)
        if p is None:
            raise PracticeError(404, "No such patient.")
        return p

    def _names(self):
        return {uid: u.get("display") or u["username"] for uid, u in self.c._users()["users"].items()}

    @staticmethod
    def _today():
        return date.today().isoformat()

    # --- staff + settings -----------------------------------------------------------------------------
    def staff(self, user):
        """Active people who can hold appointments, sign notes or co-sign (id + display name only)."""
        self._req(user, "sched.read", "bill.read", "users.manage", "note.cosign", "clin.read")
        out = []
        for uid, u in self.c._users()["users"].items():
            roles = set(u.get("roles", ()))
            if u.get("active", True) and roles & {"clinician", "nurse", "owner"}:
                out.append({"id": uid, "display": u.get("display") or u["username"], "roles": sorted(roles),
                            "supervisor": u.get("supervisor") or "",
                            "can_cosign": bool(roles & {"clinician", "owner"})})
        out.sort(key=lambda x: x["display"].lower())
        return out

    def set_supervisor(self, actor, uid, supervisor):
        """Admin: notes this person signs then wait for the supervisor's PIN co-signature ("" = none)."""
        self.c.require(actor, "users.manage")
        if not self.c.plan.get("roles"):
            raise PracticeError(402, "Supervision needs a plan with roles (Practice or Humanitarian).")
        with self.store.lock:
            users = self.c._users()
            u = users["users"].get(uid)
            if u is None:
                raise PracticeError(404, "No such user.")
            sup = str(supervisor or "")
            if sup:
                s = users["users"].get(sup)
                if s is None or not s.get("active", True) or not ({"clinician", "owner"} & set(s.get("roles", ()))):
                    raise PracticeError(400, "The supervisor must be an active clinician.")
                if sup == uid:
                    raise PracticeError(400, "Nobody can supervise themselves.")
                u["supervisor"] = sup
            else:
                u.pop("supervisor", None)
            self.c._commit({self.c.USERS: self.c.codec.dump(users)}, actor["id"], "user.supervisor", uid,
                           {"supervisor": sup})
        return {"user": uid, "supervisor": sup}

    def info(self, user):
        return {"instruments": public_instruments(), "templates": {k: list(v) for k, v in NOTE_SECTIONS.items()},
                "appt_statuses": list(APPT_STATUSES), "appt_kinds": list(APPT_KINDS), "max_series": MAX_SERIES,
                "plan_statuses": list(PLAN_STATUSES), "goal_statuses": list(GOAL_STATUSES),
                "pay_kinds": list(PAY_KINDS), "pay_methods": list(PAY_METHODS), "today": self._today(),
                "notice": ("Codes are typed by the clinic and checked for format only. Measure scores are "
                           "screening and monitoring aids, not a diagnosis.")}

    # ==================================================================================================
    # Scheduling
    # ==================================================================================================
    @staticmethod
    def _month_rel(day):
        return "sys/sched-%s.bin" % day[:7]

    def _month(self, day):
        return self.store.read_obj(self._month_rel(day), {"appts": {}})

    @staticmethod
    def _mins(t):
        h, m = t.split(":")
        return int(h) * 60 + int(m)

    def _clinician(self, uid):
        u = self.c._users()["users"].get(str(uid or ""))
        if u is None or not u.get("active", True) or not ({"clinician", "nurse", "owner"} & set(u.get("roles", ()))):
            raise PracticeError(400, "Choose an active clinician for the appointment.")
        return u

    def _appt_fields(self, b, base=None):
        a = dict(base or {})
        if "date" in b or not base:
            a["date"] = self._day(b.get("date"), "date", required=True)
        if "time" in b or not base:
            t = str(b.get("time") or "").strip()
            if not TIME_RE.match(t):
                raise PracticeError(400, "Time must be HH:MM (24-hour).")
            a["time"] = t
        if "minutes" in b or not base:
            try:
                m = int(b.get("minutes") or 50)
            except (TypeError, ValueError):
                raise PracticeError(400, "Length must be a number of minutes.")
            if not 5 <= m <= 480:
                raise PracticeError(400, "Length must be 5 to 480 minutes.")
            a["minutes"] = m
        if "kind" in b or not base:
            a["kind"] = self._choice(b.get("kind"), APPT_KINDS, "kind", "session")
        if "notes" in b or not base:
            a["notes"] = self._text(b.get("notes"), 300, "notes")
        if "clinician" in b or not base:
            u = self._clinician(b.get("clinician"))
            a["clinician"], a["clinician_name"] = u["id"], u.get("display") or u["username"]
        if self._mins(a["time"]) + a["minutes"] > 24 * 60:
            raise PracticeError(400, "The appointment must end on the same day.")
        return a

    def _conflicts(self, months, a, skip=()):
        s, e = self._mins(a["time"]), self._mins(a["time"]) + a["minutes"]
        out = []
        for x in months.get(a["date"][:7], {"appts": {}})["appts"].values():
            if x["id"] in skip or x["date"] != a["date"] or x["clinician"] != a["clinician"] \
                    or x["status"] not in APPT_BLOCKING:
                continue
            xs = self._mins(x["time"])
            if s < xs + x["minutes"] and xs < e:
                out.append({"id": x["id"], "date": x["date"], "time": x["time"], "minutes": x["minutes"]})
        return out

    def book(self, user, b):
        """One appointment, or a weekly series (repeat: {"every_weeks": 1-4, "count": 2-52})."""
        self.c.require(user, "sched.write")
        pid = b.get("patient")
        self._patient(pid)
        first = self._appt_fields(b)
        rep = b.get("repeat") or {}
        count, every = 1, 1
        if rep:
            try:
                count, every = int(rep.get("count") or 1), int(rep.get("every_weeks") or 1)
            except (TypeError, ValueError):
                raise PracticeError(400, "Repeat needs whole numbers.")
            if not 1 <= count <= MAX_SERIES or not 1 <= every <= 4:
                raise PracticeError(400, "A series is 1 to %d appointments, every 1 to 4 weeks." % MAX_SERIES)
        d0 = date.fromisoformat(first["date"])
        series = self.H.new_id("R") if count > 1 else None
        now = self.H.now_iso()
        with self.store.lock:
            months, created, conflicts = {}, [], []
            for i in range(count):
                day = (d0 + timedelta(weeks=every * i)).isoformat()
                a = dict(first, id=self.H.new_id("S"), patient=pid, date=day, status="scheduled", series=series,
                         seq=(i + 1) if series else None, created=now, created_by=user["id"],
                         created_by_name=user.get("display", ""), history=[])
                mk = day[:7]
                if mk not in months:
                    months[mk] = self._month(day)
                conflicts += self._conflicts(months, a)
                months[mk]["appts"][a["id"]] = a
                created.append(a)
            if conflicts and not b.get("allow_overlap"):
                raise PracticeError(409, "Overlaps %d other appointment(s) of this clinician. Choose another time "
                                         "or book anyway." % len(conflicts))
            idx = self.store.read_obj(SCHED_INDEX, {"patients": {}})
            have = set(idx["patients"].get(pid, []))
            idx["patients"][pid] = sorted(have | set(months))
            files = {self._month_rel(mk + "-01"): self.c.codec.dump(m) for mk, m in months.items()}
            files[SCHED_INDEX] = self.c.codec.dump(idx)
            self.c._commit(files, user["id"], "appt.create", pid, {"appts": [a["id"] for a in created],
                                                                     "series": series})
        return {"created": created, "conflicts": conflicts}

    def _find(self, aid, day):
        if not isinstance(aid, str) or not self.H.ID_RE.match(aid) or aid[0] != "S":
            raise PracticeError(400, "Bad appointment id.")
        day = self._day(day, "date", required=True)
        m = self._month(day)
        a = m["appts"].get(aid)
        if a is None:
            raise PracticeError(404, "No such appointment.")
        return m, a

    def set_status(self, user, aid, day, status, reason="", scope="one"):
        """scheduled | arrived | completed | no_show | cancelled | late_cancel. scope "following": this and every
        later still-scheduled appointment of the same series get the same status (cancel the rest)."""
        self.c.require(user, "sched.write")
        status = self._choice(status, APPT_STATUSES, "status")
        reason = self._text(reason, 200, "reason")
        if status in ("cancelled", "late_cancel") and not reason:
            raise PracticeError(400, "A reason is required to cancel.")
        with self.store.lock:
            m, a = self._find(aid, day)
            targets = {a["date"][:7]: m}
            todo = [a]
            if scope == "following" and a.get("series"):
                idx = self.store.read_obj(SCHED_INDEX, {"patients": {}})
                for mk in idx["patients"].get(a["patient"], []):
                    if mk < a["date"][:7]:
                        continue
                    mm = targets.get(mk) or self._month(mk + "-01")
                    targets[mk] = mm
                    for x in mm["appts"].values():
                        if x["id"] != a["id"] and x.get("series") == a["series"] and x["status"] == "scheduled" \
                                and (x["date"], x["time"]) > (a["date"], a["time"]):
                            todo.append(x)
            now = self.H.now_iso()
            for x in todo:
                x.setdefault("history", []).append({"at": now, "by": user["id"], "from": x["status"], "to": status,
                                                    "reason": reason})
                x["status"] = status
                x["status_at"], x["status_by"] = now, user["id"]
                if reason:
                    x["reason"] = reason
            touched = {x["date"][:7] for x in todo}
            files = {self._month_rel(mk + "-01"): self.c.codec.dump(targets[mk]) for mk in touched}
            self.c._commit(files, user["id"], "appt.status", a["patient"],
                           {"appts": [x["id"] for x in todo], "status": status})
        return {"updated": todo}

    def reschedule(self, user, aid, day, b):
        """Change date, time, length, clinician, kind or notes of a still-scheduled appointment."""
        self.c.require(user, "sched.write")
        with self.store.lock:
            m, a = self._find(aid, day)
            if a["status"] != "scheduled":
                raise PracticeError(409, "Only a scheduled appointment can be moved.")
            new = self._appt_fields(b, base=a)
            months = {a["date"][:7]: m}
            if new["date"][:7] not in months:
                months[new["date"][:7]] = self._month(new["date"])
            conflicts = self._conflicts(months, new, skip=(aid,))
            if conflicts and not b.get("allow_overlap"):
                raise PracticeError(409, "Overlaps %d other appointment(s) of this clinician. Choose another time "
                                         "or book anyway." % len(conflicts))
            changed = sorted(k for k in ("date", "time", "minutes", "clinician", "kind", "notes")
                             if new.get(k) != a.get(k))
            new.setdefault("history", []).append({"at": self.H.now_iso(), "by": user["id"], "moved": changed,
                                                  "old": {k: a.get(k) for k in changed}})
            del months[a["date"][:7]]["appts"][aid]
            months[new["date"][:7]]["appts"][aid] = new
            idx = self.store.read_obj(SCHED_INDEX, {"patients": {}})
            idx["patients"][a["patient"]] = sorted(set(idx["patients"].get(a["patient"], [])) | set(months))
            files = {self._month_rel(mk + "-01"): self.c.codec.dump(mm) for mk, mm in months.items()}
            files[SCHED_INDEX] = self.c.codec.dump(idx)
            self.c._commit(files, user["id"], "appt.move", a["patient"], {"appt": aid, "changed": changed})
        return {"appt": new, "conflicts": conflicts}

    def _with_names(self, appts):
        pts = self.c._index()["patients"]
        for a in appts:
            p = pts.get(a["patient"]) or {}
            a["patient_name"] = p.get("name", "")
            a["synthetic"] = bool(p.get("synthetic"))
        return appts

    def appointments(self, user, start, end=None, clinician=""):
        self.c.require(user, "sched.read")
        start = self._day(start, "from", required=True)
        end = self._day(end, "to") or start
        d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
        if d1 < d0 or (d1 - d0).days > MAX_RANGE_DAYS:
            raise PracticeError(400, "Choose a range of 0 to %d days." % MAX_RANGE_DAYS)
        out, mk, d = [], set(), d0
        while d <= d1:
            mk.add(d.isoformat()[:7])
            d += timedelta(days=1)
        for k in sorted(mk):
            for a in self._month(k + "-01")["appts"].values():
                if start <= a["date"] <= end and (not clinician or a["clinician"] == clinician):
                    out.append(dict(a))
        out.sort(key=lambda a: (a["date"], a["time"], a.get("clinician_name", "")))
        return {"appts": self._with_names(out), "stats": self._stats(out, past_only=False)}

    def _stats(self, appts, past_only=True):
        today = self._today()
        st = {s: 0 for s in APPT_STATUSES}
        for a in appts:
            if past_only and a["date"] >= today and a["status"] == "scheduled":
                continue
            st[a["status"]] += 1
        attended = st["completed"] + st["arrived"]
        base = attended + st["no_show"] + st["late_cancel"]
        st["kept_base"] = base
        st["no_show_rate"] = round(100.0 * st["no_show"] / base, 1) if base else None
        st["late_cancel_rate"] = round(100.0 * st["late_cancel"] / base, 1) if base else None
        return st

    def patient_appointments(self, user, pid):
        self.c.require(user, "sched.read")
        self._patient(pid)
        idx = self.store.read_obj(SCHED_INDEX, {"patients": {}})
        out = []
        for mk in idx["patients"].get(pid, []):
            out += [dict(a) for a in self._month(mk + "-01")["appts"].values() if a["patient"] == pid]
        out.sort(key=lambda a: (a["date"], a["time"]))
        today = self._today()
        past = [a for a in out if a["date"] < today or a["status"] != "scheduled"]
        return {"appts": self._with_names(out), "stats": self._stats(past),
                "upcoming": [a for a in out if a["date"] >= today and a["status"] == "scheduled"]}

    def export_ics(self, user, start, end=None, clinician="", names="initials"):
        """The appointments of one clinician (or all) as an iCalendar file (RFC 5545) the clinic imports into a
        calendar by hand. Served as a download from this computer; nothing is sent anywhere. Times are floating
        local times (the clinic's wall clock, like the calendar). Client names: initials (default), full or none;
        appointment notes are never written to the file."""
        self.c.require(user, "sched.read")
        names = self._choice(names, ICS_NAMES, "names", "initials")
        start = self._day(start, "from", required=True)
        end = self._day(end, "to") or start
        d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
        if d1 < d0 or (d1 - d0).days > ICS_MAX_DAYS:
            raise PracticeError(400, "Choose a range of 0 to %d days." % ICS_MAX_DAYS)
        if clinician and clinician not in self.c._users()["users"]:
            raise PracticeError(404, "No such user.")
        out, d = [], date(d0.year, d0.month, 1)
        while d <= d1:
            out += [dict(a) for a in self._month(d.isoformat())["appts"].values()
                    if start <= a["date"] <= end and a["status"] in APPT_BLOCKING
                    and (not clinician or a["clinician"] == clinician)]
            d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
        out.sort(key=lambda a: (a["date"], a["time"], a["id"]))
        self._with_names(out)
        lab, _loc = self._labels(user)
        stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Stark//Stark Covenant %s//EN" % self.H.VERSION,
                 "CALSCALE:GREGORIAN", "METHOD:PUBLISH"]
        for a in out:
            t0 = datetime.fromisoformat("%sT%s" % (a["date"], a["time"]))
            t1 = t0 + timedelta(minutes=a["minutes"])
            who = {"full": a.get("patient_name", ""), "none": "",
                   "initials": "".join(w[0] for w in a.get("patient_name", "").split() if w)[:4].upper()}[names]
            kind = lab("pm.akind." + a["kind"])
            lines += ["BEGIN:VEVENT", "UID:%s@stark-covenant.local" % a["id"], "DTSTAMP:" + stamp,
                      "DTSTART:" + t0.strftime("%Y%m%dT%H%M%S"), "DTEND:" + t1.strftime("%Y%m%dT%H%M%S"),
                      "SUMMARY:" + ics_text("%s - %s" % (kind, who) if who else kind),
                      "DESCRIPTION:" + ics_text(a.get("clinician_name", "")), "STATUS:CONFIRMED",
                      "TRANSP:OPAQUE", "END:VEVENT"]
        lines.append("END:VCALENDAR")
        data = "".join(ics_fold(x) + "\r\n" for x in lines).encode("utf-8")
        self.c._commit({}, user["id"], "appt.export_ics", clinician or "all",
                       {"from": start, "to": end, "count": len(out), "names": names})
        return ("file", "appointments-%s-%s.ics" % (start, end), data)

    # ==================================================================================================
    # Intake and consent forms: the clinic designs templates (sys/forms.bin, versioned); a completed form is
    # signed by typed name + date, witnessed with a staff PIN, sealed and versioned inside the patient's chart
    # ==================================================================================================
    def _forms(self):
        return self.store.read_obj(FORMS, {"templates": {}})

    def _form_fields(self, fields):
        if not isinstance(fields, list) or not fields or len(fields) > MAX_FORM_FIELDS:
            raise PracticeError(400, "A form has 1 to %d fields." % MAX_FORM_FIELDS)
        out, seen = [], set()
        for i, f in enumerate(fields):
            if not isinstance(f, dict):
                raise PracticeError(400, "field required")
            typ = self._choice(f.get("type"), FIELD_TYPES, "field type", "text")
            label = self._text(f.get("label"), 2000 if typ == "statement" else 300, "label",
                               multiline=typ == "statement")
            if not label:
                raise PracticeError(400, "Every field needs a label (field %d)." % (i + 1))
            key = str(f.get("key") or "")
            if not FIELD_KEY_RE.match(key) or key in seen:
                key = "f%d" % (i + 1)
                while key in seen:
                    key += "x"
            seen.add(key)
            x = {"key": key, "type": typ, "label": label, "required": bool(f.get("required")) and typ != "statement"}
            if typ in ("choice", "multi"):
                opts = f.get("options")
                if isinstance(opts, str):
                    opts = opts.split("\n")
                opts = [self._text(o, 120, "choice") for o in (opts or []) if str(o or "").strip()]
                if not 2 <= len(opts) <= MAX_FIELD_OPTIONS or len(set(opts)) != len(opts):
                    raise PracticeError(400, "\"%s\" needs 2 to %d different choices." % (label[:40], MAX_FIELD_OPTIONS))
                x["options"] = opts
            out.append(x)
        return out

    def form_templates(self, user):
        if not (self.c.perms(user) & {"form.design", "form.write", "clin.read"}):
            raise self.H.HttpError(403, "Your role does not allow this (form.design).")
        ts = sorted(self._forms()["templates"].values(), key=lambda t: (t["status"] != "active", t["name"].lower()))
        return {"templates": ts, "field_types": list(FIELD_TYPES), "kinds": list(FORM_KINDS),
                "signer_roles": list(SIGNER_ROLES)}

    def save_form_template(self, user, t, template_id=None):
        """Create a template, or save a new version of one (the earlier version is kept in its history; forms
        already completed keep the exact fields they were signed with)."""
        self.c.require(user, "form.design")
        if not isinstance(t, dict):
            raise PracticeError(400, "template required")
        fields = {"name": self._text(t.get("name"), 120, "name"),
                  "kind": self._choice(t.get("kind"), FORM_KINDS, "kind", "intake"),
                  "intro": self._text(t.get("intro"), 10000, "introduction", multiline=True),
                  "statement": self._text(t.get("statement"), 1000, "signature statement", multiline=True),
                  "fields": self._form_fields(t.get("fields"))}
        if not fields["name"]:
            raise PracticeError(400, "The form needs a name.")
        now = self.H.now_iso()
        with self.store.lock:
            db = self._forms()
            if template_id:
                cur_t = db["templates"].get(template_id)
                if cur_t is None:
                    raise PracticeError(404, "No such form.")
                cur_t.setdefault("history", []).append({k: v for k, v in cur_t.items() if k != "history"})
                cur_t.update(fields, version=cur_t["version"] + 1, updated=now, updated_by=user["id"],
                             updated_by_name=user.get("display", ""))
                tpl, action = cur_t, "form.template_update"
            else:
                if len(db["templates"]) >= MAX_TEMPLATES:
                    raise PracticeError(400, "At most %d form templates." % MAX_TEMPLATES)
                tpl = dict(fields, id=self.H.new_id("Q"), version=1, status="active", created=now,
                           created_by=user["id"], updated=now, updated_by=user["id"],
                           updated_by_name=user.get("display", ""), history=[])
                db["templates"][tpl["id"]] = tpl
                action = "form.template_create"
            self.c._commit({FORMS: self.c.codec.dump(db)}, user["id"], action, tpl["id"],
                           {"version": tpl["version"], "fields": len(tpl["fields"])})
        return tpl

    def form_template_status(self, user, template_id, status):
        self.c.require(user, "form.design")
        status = self._choice(status, ("active", "retired"), "status")
        with self.store.lock:
            db = self._forms()
            tpl = db["templates"].get(template_id)
            if tpl is None:
                raise PracticeError(404, "No such form.")
            tpl["status"] = status
            self.c._commit({FORMS: self.c.codec.dump(db)}, user["id"], "form.template_" + status, template_id,
                           {"version": tpl["version"]})
        return tpl

    def _answers(self, fields, answers):
        if not isinstance(answers, dict):
            raise PracticeError(400, "answers required")
        out = {}
        for f in fields:
            k, typ, v = f["key"], f["type"], answers.get(f["key"])
            if typ == "statement":
                continue
            if typ == "multi":
                v = [x for x in (v if isinstance(v, list) else []) if x in f["options"]]
                v = [o for o in f["options"] if o in v]
                empty = not v
            elif typ == "choice":
                v = v if v in f["options"] else ""
                empty = not v
            elif typ == "yesno":
                v = v if v in ("yes", "no") else ""
                empty = not v
            elif typ == "date":
                v = self._day(v, f["label"][:40]) or ""
                empty = not v
            elif typ == "number":
                s = str(v if v is not None else "").strip()
                if s and not re.fullmatch(r"-?\d{1,12}(?:\.\d{1,6})?", s):
                    raise PracticeError(400, "\"%s\" must be a number." % f["label"][:40])
                v, empty = s, not s
            else:
                v = self._text(v, 5000 if typ == "textarea" else 500, f["label"][:40], multiline=typ == "textarea")
                empty = not v
            if empty and f["required"]:
                raise PracticeError(400, "\"%s\" is required." % f["label"][:60])
            out[k] = v
        return out

    def _form_seal(self, pid, f):
        content = self.H.sha256_bytes(self.H.canon({k: f.get(k) for k in FORM_SEALED}))
        sig = hmac.new(self.c.sig_key, self.H.canon({"patient": pid, "form": f["id"], "content": content,
                                                     "witness": f.get("witness"), "at": f.get("at")}),
                       hashlib.sha256).hexdigest()
        return content, sig

    def _form_valid(self, pid, f):
        content, sig = self._form_seal(pid, f)
        return content == f.get("content_sha") and hmac.compare_digest(sig, f.get("signature") or "")

    def complete_form(self, user, pid, b):
        """Sign a form into the chart: b = {template | form (amend), answers, signer_name, signer_role,
        signed_date, pin}. The staff member witnessing it confirms with their PIN. Amending a completed form
        keeps every earlier sealed version in its history."""
        self.c.require(user, "form.write")
        if not isinstance(b, dict):
            raise PracticeError(400, "form required")
        self._patient(pid)
        signer = self._text(b.get("signer_name"), 120, "signer name")
        if len(signer) < 2:
            raise PracticeError(400, "Type the signer's full name.")
        role = self._choice(b.get("signer_role"), SIGNER_ROLES, "signer", "client")
        signed = self._day(b.get("signed_date"), "signature date", required=True, not_future=True)
        fid = b.get("form")
        if not fid:
            tpl = self._forms()["templates"].get(str(b.get("template") or ""))
            if tpl is None or tpl["status"] != "active":
                raise PracticeError(404, "No such form in use.")
            base = {k: tpl[k] for k in ("name", "kind", "intro", "statement", "fields")}
            base.update(template=tpl["id"], template_version=tpl["version"])
        answers = None
        with self.store.lock:
            chart = self.c._load_chart(pid)
            forms = chart.setdefault("forms", [])
            if fid:
                f = next((x for x in forms if x["id"] == fid), None)
                if f is None:
                    raise PracticeError(404, "No such form.")
                if f["status"] != "active":
                    raise PracticeError(409, "This form was marked entered in error.")
                fields = f["fields"]
            else:
                fields = base["fields"]
            answers = self._answers(fields, b.get("answers"))
            self.c.verify_pin(user, b.get("pin"))
            chart = self.c._load_chart(pid)
            forms = chart.setdefault("forms", [])
            now = self.H.now_iso()
            sig = {"answers": answers, "signer_name": signer, "signer_role": role, "signed_date": signed,
                   "witness": user["id"], "witness_name": user.get("display", ""), "at": now}
            if fid:
                f = next(x for x in forms if x["id"] == fid)
                f.setdefault("history", []).append({k: v for k, v in f.items() if k != "history"})
                f.update(sig, version=f["version"] + 1)
                action = "form.amend"
            else:
                f = dict(base, id=self.H.new_id("F"), version=1, status="active", history=[], **sig)
                forms.append(f)
                action = "form.complete"
            f["content_sha"], f["signature"] = self._form_seal(pid, f)
            self.c._save_chart(chart, user["id"], action, {"form": f["id"], "version": f["version"],
                                                            "template": f.get("template"),
                                                            "content_sha": f["content_sha"]}, index=False)
        return self._form_view(pid, f)

    def _form_view(self, pid, f):
        out = dict(f)
        out["signature_valid"] = self._form_valid(pid, f)
        out["history"] = [dict(h, signature_valid=self._form_valid(pid, h)) for h in f.get("history", [])]
        return out

    def _form_reader(self, user):
        if not (self.c.perms(user) & {"form.write", "clin.read"}):
            raise self.H.HttpError(403, "Your role does not allow this (form.write).")

    def patient_forms(self, user, pid):
        self._form_reader(user)
        self._patient(pid)
        chart = self.c._load_chart(pid)
        out = [self._form_view(pid, f) for f in chart.get("forms", [])]
        self.c._commit({}, user["id"], "form.view", pid, {"forms": len(out)})
        return {"forms": out}

    def form_error(self, user, pid, fid, reason):
        """Completed forms are never deleted: a wrong one is marked entered in error with a reason."""
        self.c.require(user, "form.write")
        reason = self._text(reason, 300, "reason")
        if not reason:
            raise PracticeError(400, "A reason is required.")
        with self.store.lock:
            chart = self.c._load_chart(pid)
            f = next((x for x in chart.get("forms", []) if x["id"] == fid), None)
            if f is None:
                raise PracticeError(404, "No such form.")
            if f["status"] != "active":
                raise PracticeError(409, "Already marked entered in error.")
            f.update(status="entered_in_error", error_reason=reason, error_by=user["id"], error_at=self.H.now_iso())
            self.c._save_chart(chart, user["id"], "form.entered_in_error", {"form": fid}, index=False)
        return self._form_view(pid, f)

    def _form_lines(self, lab, f):
        L = [(f["name"], True), (lab("pm.form.version", v=f["version"]), False)]
        if f.get("intro"):
            L.append((f["intro"], False))
        for x in f["fields"]:
            if x["type"] == "statement":
                L.append((x["label"], False))
                continue
            v = (f.get("answers") or {}).get(x["key"], "")
            if x["type"] == "multi":
                v = ", ".join(v or [])
            elif x["type"] == "yesno" and v:
                v = lab("pm.form." + v)
            L.append(("%s: %s" % (x["label"], v if v not in (None, "") else "-"), False))
        if f.get("statement"):
            L.append((f["statement"], False))
        L.append((lab("pm.form.signed_line", name=f["signer_name"], role=lab("pm.srole." + f["signer_role"]),
                      date=f["signed_date"], staff=f.get("witness_name") or f["witness"], when=f["at"]), False))
        if f.get("status") != "active":
            L.append((lab("pm.meas.in_error", reason=f.get("error_reason", "")), False))
        return L

    def form_pdf(self, user, pid, fid):
        self._form_reader(user)
        chart = self.c._load_chart(pid)
        f = next((x for x in chart.get("forms", []) if x["id"] == fid), None)
        if f is None:
            raise PracticeError(404, "No such form.")
        lab, loc = self._labels(user)
        d = chart["demographics"]
        lines = [(d.get("name", ""), True), (lab("app.field.dob") + ": " + str(d.get("dob", "")), False)]
        lines += self._form_lines(lab, f)
        lines.append((lab("pm.form.seal_ok" if self._form_valid(pid, f) else "app.chip.sig_invalid"), False))
        data, ext = _pdf(f["name"], lines, loc)
        self.c._commit({}, user["id"], "form.print", pid, {"form": fid, "version": f["version"]})
        return ("file", "form-%s-v%d.%s" % (fid, f["version"], ext), data)

    # ==================================================================================================
    # Outcome measures (stored in the chart; scores computed here, never taken from the browser)
    # ==================================================================================================
    def add_measure(self, user, pid, instrument, answers, day=None, difficulty=None, group=None):
        self.c.require(user, "measure.write")
        res = score(instrument, answers, group)
        when = self._day(day, "date", not_future=True) or self._today()
        if difficulty not in (None, "") and not INSTRUMENTS[instrument].get("difficulty"):
            raise PracticeError(400, "The %s has no difficulty question." % INSTRUMENTS[instrument]["name"])
        if difficulty not in (None, ""):
            if isinstance(difficulty, bool) or not isinstance(difficulty, int) or not 0 <= difficulty <= 3:
                raise PracticeError(400, "The difficulty answer must be 0, 1, 2 or 3.")
        else:
            difficulty = None
        with self.store.lock:
            chart = self.c._load_chart(pid)
            m = {"id": self.H.new_id("M"), "instrument": instrument, "date": when,
                 "answers": [a if a is not None else 0 for a in answers],
                 "difficulty": difficulty, "total": res["total"], "band": res["band"], "max": res["max"],
                 "flag": res["flag"], "by": user["id"], "by_name": user.get("display", ""), "at": self.H.now_iso(),
                 "status": "active"}
            for k in ("group", "clusters", "criteria_met", "percent"):
                if k in res:
                    m[k] = res[k]
            chart.setdefault("measures", []).append(m)
            self.c._save_chart(chart, user["id"], "measure.add", {"measure": m["id"], "instrument": instrument},
                               index=False)
        return m

    def measure_error(self, user, pid, mid, reason):
        """Measures are never edited: a wrong entry is marked entered-in-error with a reason."""
        self.c.require(user, "measure.write")
        reason = self._text(reason, 300, "reason")
        if not reason:
            raise PracticeError(400, "A reason is required.")
        with self.store.lock:
            chart = self.c._load_chart(pid)
            m = next((x for x in chart.get("measures", []) if x["id"] == mid), None)
            if m is None:
                raise PracticeError(404, "No such measure.")
            if m["status"] != "active":
                raise PracticeError(409, "Already marked entered-in-error.")
            m.update(status="entered_in_error", error_reason=reason, error_by=user["id"], error_at=self.H.now_iso())
            self.c._save_chart(chart, user["id"], "measure.entered_in_error", {"measure": mid}, index=False)
        return m

    # ==================================================================================================
    # Treatment plans (versioned; a PIN signature seals one version; editing a signed plan starts a new one)
    # ==================================================================================================
    def _plan_fields(self, p):
        if not isinstance(p, dict):
            raise PracticeError(400, "plan required")
        out = {"title": self._text(p.get("title"), 120, "title"),
               "problems": self._text(p.get("problems"), 1000, "problems", multiline=True),
               "start": self._day(p.get("start"), "start date"), "review": self._day(p.get("review"), "review date"),
               "status": self._choice(p.get("status"), PLAN_STATUSES, "status", "active"),
               "strengths": self._text(p.get("strengths"), 1000, "strengths", multiline=True), "goals": []}
        if not out["title"]:
            raise PracticeError(400, "The plan needs a title.")
        goals = p.get("goals") or []
        if not isinstance(goals, list) or len(goals) > MAX_GOALS:
            raise PracticeError(400, "A plan has at most %d goals." % MAX_GOALS)
        for g in goals:
            if not isinstance(g, dict):
                raise PracticeError(400, "goal required")
            gt = self._text(g.get("text"), 500, "goal", multiline=True)
            objs = g.get("objectives") or []
            if not isinstance(objs, list) or len(objs) > MAX_OBJECTIVES:
                raise PracticeError(400, "A goal has at most %d objectives." % MAX_OBJECTIVES)
            ol = []
            for o in objs:
                if not isinstance(o, dict):
                    raise PracticeError(400, "objective required")
                ot = self._text(o.get("text"), 500, "objective", multiline=True)
                if not ot:
                    continue
                ol.append({"text": ot, "target": self._day(o.get("target"), "target date"),
                           "status": self._choice(o.get("status"), GOAL_STATUSES, "objective status", "not_started")})
            if not gt and not ol:
                continue
            if not gt:
                raise PracticeError(400, "Every goal needs its text.")
            out["goals"].append({"text": gt, "status": self._choice(g.get("status"), GOAL_STATUSES, "goal status",
                                                                     "not_started"), "objectives": ol})
        return out

    def save_plan(self, user, pid, plan, plan_id=None):
        self.c.require(user, "plan.write")
        fields = self._plan_fields(plan)
        now = self.H.now_iso()
        with self.store.lock:
            chart = self.c._load_chart(pid)
            plans = chart.setdefault("treatment_plans", [])
            if plan_id:
                p = next((x for x in plans if x["id"] == plan_id), None)
                if p is None:
                    raise PracticeError(404, "No such plan.")
                snap = {k: v for k, v in p.items() if k != "history"}
                p.setdefault("history", []).append(snap)
                p.update(fields)
                p["version"] = p.get("version", 1) + 1
                p.update(updated=now, updated_by=user["id"], updated_by_name=user.get("display", ""),
                         signed_by=None, signed_by_name=None, signed_at=None, content_sha=None, signature=None)
                action = "plan.update"
            else:
                p = dict(fields, id=self.H.new_id("T"), version=1, created=now, created_by=user["id"],
                         created_by_name=user.get("display", ""), updated=now, updated_by=user["id"],
                         updated_by_name=user.get("display", ""), signed_by=None, signed_by_name=None,
                         signed_at=None, content_sha=None, signature=None, history=[])
                plans.append(p)
                action = "plan.create"
            extra = self._wl_files(lambda w: self._wl_plan(w, pid, p))
            self.c._save_chart(chart, user["id"], action, {"plan": p["id"], "version": p["version"]},
                               extra_files=extra, index=False)
        return self._plan_view(pid, p)

    def sign_plan(self, user, pid, plan_id, pin):
        self.c.require(user, "plan.write")
        with self.store.lock:
            chart = self.c._load_chart(pid)
            p = next((x for x in chart.get("treatment_plans", []) if x["id"] == plan_id), None)
            if p is None:
                raise PracticeError(404, "No such plan.")
            if p.get("signature"):
                raise PracticeError(409, "This version is already signed. Edit the plan to start a new version.")
            self.c.verify_pin(user, pin)
            chart = self.c._load_chart(pid)
            p = next(x for x in chart["treatment_plans"] if x["id"] == plan_id)
            p.update(signed_by=user["id"], signed_by_name=user.get("display", ""), signed_at=self.H.now_iso())
            p["content_sha"], p["signature"] = self.c.plan_signature(pid, p)
            self.c._save_chart(chart, user["id"], "plan.sign", {"plan": plan_id, "version": p["version"],
                                                                 "content_sha": p["content_sha"]}, index=False)
        return self._plan_view(pid, p)

    def _plan_view(self, pid, p):
        out = dict(p)
        if p.get("signature"):
            content, sig = self.c.plan_signature(pid, p)
            out["signature_valid"] = content == p.get("content_sha") and hmac.compare_digest(sig, p["signature"])
        return out

    # ==================================================================================================
    # Today worklist (an index; the charts stay the source of truth and rebuild it when it is missing)
    # ==================================================================================================
    @staticmethod
    def _wl_plan(w, pid, p):
        r = w.setdefault("reviews", {})
        if p.get("status") == "active" and p.get("review"):
            r[p["id"]] = {"patient": pid, "review": p["review"], "title": p.get("title", "")}
        else:
            r.pop(p["id"], None)

    def _wl_files(self, change):
        if not self.store.exists(WORKLIST):
            return {}
        w = self.store.read_obj(WORKLIST, {})
        change(w)
        return {WORKLIST: self.c.codec.dump(w)}

    def worklist(self):
        with self.store.lock:
            w = self.store.read_obj(WORKLIST, None)
            if w is not None:
                return w
            w = {"v": 1, "drafts": {}, "cosign": {}, "reviews": {}}
            for pid in self.c._index()["patients"]:
                try:
                    chart = self.c._load_chart(pid)
                except Exception:
                    continue
                for n in chart.get("notes", []):
                    if n.get("status") == "draft":
                        w["drafts"][n["id"]] = {"patient": pid, "author": n["author"], "title": n.get("title", ""),
                                                "created": n.get("created", "")}
                    cs = n.get("cosign") or {}
                    if n.get("status") == "signed" and cs.get("status") == "pending":
                        w["cosign"][n["id"]] = {"patient": pid, "author": n["author"], "supervisor": cs.get("required"),
                                                "title": n.get("title", ""), "signed_at": n.get("signed_at")}
                for p in chart.get("treatment_plans", []):
                    self._wl_plan(w, pid, p)
            self.c._commit({WORKLIST: self.c.codec.dump(w)}, "system", "worklist.rebuild", "",
                           {"drafts": len(w["drafts"]), "cosign": len(w["cosign"]), "reviews": len(w["reviews"])})
            return w

    def today(self, user, day=None):
        day = self._day(day, "date") or self._today()
        perms = self.c.perms(user)
        out = {"date": day}
        if "sched.read" in perms:
            out["appts"] = self.appointments(user, day, day)["appts"]
        w = self.worklist()
        pts = self.c._index()["patients"]
        name = lambda pid: (pts.get(pid) or {}).get("name", "")  # noqa: E731
        if "note.write" in perms:
            out["drafts"] = sorted([dict(v, note=k, patient_name=name(v["patient"]))
                                    for k, v in w.get("drafts", {}).items() if v.get("author") == user["id"]],
                                   key=lambda x: x.get("created", ""))
        if "note.cosign" in perms:
            out["cosign"] = sorted([dict(v, note=k, patient_name=name(v["patient"]))
                                    for k, v in w.get("cosign", {}).items() if v.get("supervisor") == user["id"]],
                                   key=lambda x: x.get("signed_at") or "")
        if "clin.read" in perms:
            soon = (date.fromisoformat(day) + timedelta(days=14)).isoformat()
            out["reviews"] = sorted([dict(v, plan=k, patient_name=name(v["patient"]), overdue=v["review"] < day)
                                     for k, v in w.get("reviews", {}).items() if v["review"] <= soon],
                                    key=lambda x: x["review"])
        if "bill.read" in perms:
            out["balances"] = self.billing_summary(user)["balances"][:10]
        return out

    # ==================================================================================================
    # Billing
    # ==================================================================================================
    def _ledger(self):
        return self.store.read_obj(BILLING, {"seq": 0, "profile": {}, "providers": {}, "fees": [], "invoices": {},
                                             "payments": {}})

    def profile(self, user):
        self._req(user, "bill.read")
        L = self._ledger()
        prof = dict(L.get("profile") or {})
        prof.setdefault("currency", "")
        prof.setdefault("invoice_prefix", "INV")
        return {"profile": prof, "providers": L.get("providers") or {}, "fees": L.get("fees") or [],
                "staff": self.staff(user), "tiers": (L.get("sliding") or {}).get("tiers") or [],
                "plan_types": list(PLAN_TYPES), "relationships": list(RELATIONSHIPS)}

    def save_profile(self, user, profile=None, providers=None, fees=None):
        self.c.require(user, "bill.write")
        with self.store.lock:
            L = self._ledger()
            changed = []
            if profile is not None:
                if not isinstance(profile, dict):
                    raise PracticeError(400, "profile must be an object")
                prof = dict(L.get("profile") or {})
                for k, n in PROFILE_FIELDS.items():
                    if k in profile:
                        prof[k] = self._text(profile[k], n, k, multiline=(k in ("address", "footer")))
                if prof.get("currency"):
                    prof["currency"] = prof["currency"].upper()
                    if not CURRENCY_RE.match(prof["currency"]):
                        raise PracticeError(400, "Currency must be a 3-letter ISO code (for example USD).")
                if prof.get("npi") and not npi_valid(prof["npi"]):
                    raise PracticeError(400, "That NPI fails its check digit.")
                if prof.get("invoice_prefix") and not re.fullmatch(r"[A-Z0-9-]{1,8}", prof["invoice_prefix"]):
                    raise PracticeError(400, "Invoice prefix: 1-8 capital letters, digits or dashes.")
                L["profile"] = prof
                changed.append("profile")
            if providers is not None:
                if not isinstance(providers, dict):
                    raise PracticeError(400, "providers must be an object")
                users = self.c._users()["users"]
                out = {}
                for uid, v in providers.items():
                    if uid not in users or not isinstance(v, dict):
                        raise PracticeError(400, "Unknown provider.")
                    rec = {"npi": self._text(v.get("npi"), 10, "npi"), "credentials": self._text(v.get("credentials"), 60,
                                                                                             "credentials"),
                           "license": self._text(v.get("license"), 60, "license"),
                           "taxonomy": self._text(v.get("taxonomy"), 10, "taxonomy").upper()}
                    if rec["npi"] and not npi_valid(rec["npi"]):
                        raise PracticeError(400, "That NPI fails its check digit.")
                    if any(rec.values()):
                        out[uid] = rec
                L["providers"] = out
                changed.append("providers")
            if fees is not None:
                if not isinstance(fees, list) or len(fees) > 300:
                    raise PracticeError(400, "The fee schedule holds at most 300 codes.")
                out, seen = [], set()
                for f in fees:
                    if not isinstance(f, dict) or not str(f.get("code") or "").strip():
                        continue
                    code = norm_code(f.get("code"))
                    if code in seen:
                        raise PracticeError(400, "Code %s is listed twice." % code)
                    seen.add(code)
                    out.append({"code": code, "description": self._text(f.get("description"), 120, "description"),
                                "fee": cents(f.get("fee"), "fee")})
                L["fees"] = out
                changed.append("fees")
            self.c._commit({BILLING: self.c.codec.dump(L)}, user["id"], "billing.settings", "billing",
                           {"changed": changed})
        return self.profile(user)

    def _invoice_fields(self, inv, L, pid=None):
        if not isinstance(inv, dict):
            raise PracticeError(400, "invoice required")
        dx = []
        for d in (inv.get("diagnoses") or [])[:MAX_DX + 1]:
            if not isinstance(d, dict) or not str(d.get("code") or "").strip():
                continue
            dx.append({"code": norm_icd10(d.get("code")), "description": self._text(d.get("description"), 120,
                                                                                  "description")})
        if len(dx) > MAX_DX:
            raise PracticeError(400, "At most %d diagnosis codes." % MAX_DX)
        lines = []
        for ln in inv.get("lines") or []:
            if not isinstance(ln, dict) or not str(ln.get("code") or "").strip():
                continue
            mods = ln.get("modifiers") or []
            if isinstance(mods, str):
                mods = [m for m in re.split(r"[\s,;]+", mods) if m]
            mods = [str(m).upper() for m in mods]
            if len(mods) > 4 or any(not MODIFIER_RE.match(m) for m in mods):
                raise PracticeError(400, "Up to 4 modifiers of 2 letters or digits each.")
            try:
                units = int(ln.get("units") or 1)
            except (TypeError, ValueError):
                raise PracticeError(400, "Units must be a whole number.")
            if not 1 <= units <= 99:
                raise PracticeError(400, "Units must be 1 to 99.")
            pos = str(ln.get("pos") or "").strip()
            if pos and not POS_RE.match(pos):
                raise PracticeError(400, "Place of service is a 2-digit code.")
            ptr = ln.get("dx") or []
            if isinstance(ptr, str):
                ptr = [p for p in re.split(r"[\s,;]+", ptr) if p]
            try:
                ptr = [int(p) for p in ptr]
            except (TypeError, ValueError):
                raise PracticeError(400, "Diagnosis pointers are numbers (1 = the first diagnosis).")
            if len(ptr) > 4 or any(not 1 <= p <= len(dx) for p in ptr):
                raise PracticeError(400, "Each diagnosis pointer must name a listed diagnosis (1-%d), at most 4."
                                    % max(len(dx), 1))
            lines.append({"dos": self._day(ln.get("dos"), "date of service", required=True, not_future=True),
                          "code": norm_code(ln.get("code")), "modifiers": mods,
                          "description": self._text(ln.get("description"), 120, "description"),
                          "units": units, "fee": cents(ln.get("fee"), "fee"), "pos": pos, "dx": ptr})
        if not lines:
            raise PracticeError(400, "Add at least one service line.")
        if len(lines) > MAX_LINES:
            raise PracticeError(400, "At most %d service lines." % MAX_LINES)
        provider = str(inv.get("provider") or "")
        if provider and provider not in self.c._users()["users"]:
            raise PracticeError(400, "Unknown provider.")
        cur = str(inv.get("currency") or (L.get("profile") or {}).get("currency") or "USD").upper()
        if not CURRENCY_RE.match(cur):
            raise PracticeError(400, "Currency must be a 3-letter ISO code (for example USD).")
        return {"date": self._day(inv.get("date"), "invoice date") or self._today(), "diagnoses": dx, "lines": lines,
                "provider": provider, "currency": cur, "notes": self._text(inv.get("notes"), 500, "notes", True),
                **self._totals(lines, L, pid)}

    def _totals(self, lines, L, pid):
        """subtotal = sum of units x fee; a patient on a sliding-scale tier gets that tier's percent off (rounded to
        the cent, half up), recorded on the invoice so later tier changes never alter it."""
        sub = sum(ln["units"] * ln["fee"] for ln in lines)
        s = L.get("sliding") or {}
        tid = (s.get("patients") or {}).get(pid) if pid else None
        tier = next((t for t in s.get("tiers") or [] if t["id"] == tid), None)
        if not tier or not tier["percent"]:
            return {"subtotal": sub, "discount": 0, "sliding": None, "total": sub}
        disc = (sub * tier["percent"] + 50) // 100
        return {"subtotal": sub, "discount": disc, "sliding": {"tier": tier["name"], "percent": tier["percent"]},
                "total": sub - disc}

    def save_invoice(self, user, pid, inv, invoice_id=None):
        """A draft invoice (create or edit). Drafts can change; issued invoices only be voided."""
        self.c.require(user, "bill.write")
        self._patient(pid)
        with self.store.lock:
            L = self._ledger()
            fields = self._invoice_fields(inv, L, pid)
            now = self.H.now_iso()
            if invoice_id:
                i = L["invoices"].get(invoice_id)
                if i is None or i["patient"] != pid:
                    raise PracticeError(404, "No such invoice.")
                if i["status"] != "draft":
                    raise PracticeError(423, "An issued invoice cannot be changed. Void it and write a new one.")
                i.update(fields, updated=now, updated_by=user["id"])
                action = "invoice.update"
            else:
                i = dict(fields, id=self.H.new_id("B"), patient=pid, number=None, status="draft", created=now,
                         created_by=user["id"], created_by_name=user.get("display", ""), updated=now,
                         updated_by=user["id"])
                L["invoices"][i["id"]] = i
                action = "invoice.create"
            self.c._commit({BILLING: self.c.codec.dump(L)}, user["id"], action, pid,
                           {"invoice": i["id"], "total": i["total"]})
        return i

    def issue_invoice(self, user, invoice_id):
        self.c.require(user, "bill.write")
        with self.store.lock:
            L = self._ledger()
            i = L["invoices"].get(invoice_id)
            if i is None:
                raise PracticeError(404, "No such invoice.")
            if i["status"] != "draft":
                raise PracticeError(409, "Only a draft can be issued.")
            L["seq"] = int(L.get("seq") or 0) + 1
            prefix = (L.get("profile") or {}).get("invoice_prefix") or "INV"
            i.update(status="issued", number="%s-%05d" % (prefix, L["seq"]), issued_at=self.H.now_iso(),
                     issued_by=user["id"])
            self.c._commit({BILLING: self.c.codec.dump(L)}, user["id"], "invoice.issue", i["patient"],
                           {"invoice": invoice_id, "number": i["number"], "total": i["total"]})
        return i

    def void(self, user, kind, item_id, reason):
        """Invoices and payments are never deleted: void keeps them, with who, when and why."""
        self.c.require(user, "bill.write")
        reason = self._text(reason, 200, "reason")
        if not reason:
            raise PracticeError(400, "A reason is required.")
        coll = {"invoice": "invoices", "payment": "payments"}.get(kind)
        if coll is None:
            raise PracticeError(400, "kind is invoice or payment")
        with self.store.lock:
            L = self._ledger()
            x = L[coll].get(item_id)
            if x is None:
                raise PracticeError(404, "No such %s." % kind)
            if x["status"] == "void":
                raise PracticeError(409, "Already void.")
            x.update(status="void", void_reason=reason, voided_at=self.H.now_iso(), voided_by=user["id"])
            self.c._commit({BILLING: self.c.codec.dump(L)}, user["id"], "%s.void" % kind, x["patient"],
                           {kind: item_id})
        return x

    def add_payment(self, user, pid, p):
        self.c.require(user, "bill.write")
        self._patient(pid)
        if not isinstance(p, dict):
            raise PracticeError(400, "payment required")
        with self.store.lock:
            L = self._ledger()
            inv = str(p.get("invoice") or "")
            if inv:
                i = L["invoices"].get(inv)
                if i is None or i["patient"] != pid or i["status"] != "issued":
                    raise PracticeError(400, "Payments can be linked only to an issued invoice of this patient.")
            cur = str(p.get("currency") or (L["invoices"][inv]["currency"] if inv else "")
                      or (L.get("profile") or {}).get("currency") or "USD").upper()
            if not CURRENCY_RE.match(cur):
                raise PracticeError(400, "Currency must be a 3-letter ISO code (for example USD).")
            y = {"id": self.H.new_id("Y"), "patient": pid, "date": self._day(p.get("date"), "payment date",
                                                                             not_future=True) or self._today(),
                 "amount": cents(p.get("amount"), "amount", allow_zero=False),
                 "kind": self._choice(p.get("kind"), PAY_KINDS, "kind", "payment"),
                 "method": self._choice(p.get("method"), PAY_METHODS, "method", "other"),
                 "reference": self._text(p.get("reference"), 80, "reference"), "invoice": inv or None,
                 "currency": cur, "status": "active", "at": self.H.now_iso(), "by": user["id"],
                 "by_name": user.get("display", "")}
            L["payments"][y["id"]] = y
            self.c._commit({BILLING: self.c.codec.dump(L)}, user["id"], "payment.add", pid,
                           {"payment": y["id"], "amount": y["amount"], "kind": y["kind"]})
        return y

    @staticmethod
    def _balance(invoices, payments):
        """Per currency: issued charges - payments - write-offs + refunds (void entries never count)."""
        out = {}
        for i in invoices:
            if i["status"] == "issued":
                out[i["currency"]] = out.get(i["currency"], 0) + i["total"]
        for y in payments:
            if y["status"] != "active":
                continue
            sign = 1 if y["kind"] == "refund" else -1
            out[y["currency"]] = out.get(y["currency"], 0) + sign * y["amount"]
        return out

    @staticmethod
    def _paid(invoice_id, payments):
        n = 0
        for y in payments:
            if y["status"] == "active" and y.get("invoice") == invoice_id:
                n += -y["amount"] if y["kind"] == "refund" else y["amount"]
        return n

    def patient_billing(self, user, pid):
        self.c.require(user, "bill.read")
        self._patient(pid)
        L = self._ledger()
        inv = sorted([dict(i) for i in L["invoices"].values() if i["patient"] == pid],
                     key=lambda i: (i["date"], i["created"]))
        pays = sorted([dict(y) for y in L["payments"].values() if y["patient"] == pid],
                      key=lambda y: (y["date"], y["at"]))
        for i in inv:
            i["paid"] = self._paid(i["id"], pays)
        return {"invoices": inv, "payments": pays, "balance": self._balance(inv, pays),
                "fees": L.get("fees") or [], "currency": (L.get("profile") or {}).get("currency") or "",
                "tier": ((L.get("sliding") or {}).get("patients") or {}).get(pid, ""),
                "tiers": (L.get("sliding") or {}).get("tiers") or [],
                "coverage": (L.get("coverage") or {}).get(pid) or {}}

    def billing_summary(self, user):
        self.c.require(user, "bill.read")
        L = self._ledger()
        by = {}
        for i in L["invoices"].values():
            by.setdefault(i["patient"], ([], []))[0].append(i)
        for y in L["payments"].values():
            by.setdefault(y["patient"], ([], []))[1].append(y)
        pts = self.c._index()["patients"]
        rows, totals = [], {}
        for pid, (inv, pays) in by.items():
            bal = self._balance(inv, pays)
            for cur, v in bal.items():
                totals[cur] = totals.get(cur, 0) + v
            if any(bal.values()):
                rows.append({"patient": pid, "patient_name": (pts.get(pid) or {}).get("name", ""), "balance": bal,
                             "drafts": sum(1 for i in inv if i["status"] == "draft")})
        rows.sort(key=lambda r: -max(r["balance"].values() or [0]))
        recent = sorted(L["payments"].values(), key=lambda y: (y["date"], y["at"]), reverse=True)[:20]
        for y in recent:
            y["patient_name"] = (pts.get(y["patient"]) or {}).get("name", "")
        drafts = sum(1 for i in L["invoices"].values() if i["status"] == "draft")
        return {"balances": rows, "totals": totals, "recent": recent, "draft_invoices": drafts}

    # ==================================================================================================
    # Practice dashboard: the clinic's own money, attendance, client and outcome numbers (local data only)
    # ==================================================================================================
    def _all_appts(self):
        idx = self.store.read_obj(SCHED_INDEX, {"patients": {}})
        months = sorted({mk for ms in idx["patients"].values() for mk in ms})
        out = []
        for mk in months:
            out += [dict(a) for a in self._month(mk + "-01")["appts"].values()]
        return out

    def dashboard(self, user, start=None, end=None):
        """Per period: billed / collected / written off (bill.read), attendance and new vs returning clients
        (sched.read), and the average PHQ-9 / GAD-7 change across the caseload (clin.read). Each section only for
        the roles that may see its data. Nothing is sent anywhere."""
        perms = self.c.perms(user)
        if not perms & {"bill.read", "sched.read", "clin.read"}:
            raise self.H.HttpError(403, "Your role does not allow this (dashboard).")
        today = self._today()
        end = self._day(end, "to") or today
        start = self._day(start, "from") or (date.fromisoformat(end) - timedelta(days=DASH_DEFAULT_DAYS - 1)).isoformat()
        if start > end or (date.fromisoformat(end) - date.fromisoformat(start)).days > DASH_MAX_DAYS:
            raise PracticeError(400, "Choose a range of 1 to %d days." % (DASH_MAX_DAYS + 1))
        out = {"from": start, "to": end}
        visits = None
        if "sched.read" in perms:
            appts = self._all_appts()
            past_end = min(end, today)
            span = [a for a in appts if start <= a["date"] <= past_end]
            st = self._stats(span, past_only=False)
            first = {}
            for a in appts:
                if a["status"] in ("arrived", "completed"):
                    first[a["patient"]] = min(first.get(a["patient"], a["date"]), a["date"])
            seen = {a["patient"] for a in span if a["status"] in ("arrived", "completed")}
            new = {p for p in seen if start <= first[p]}
            visits = st["completed"] + st["arrived"]
            out["attendance"] = {"attended": visits, "no_show": st["no_show"], "late_cancel": st["late_cancel"],
                                 "cancelled": st["cancelled"], "no_show_rate": st["no_show_rate"],
                                 "late_cancel_rate": st["late_cancel_rate"],
                                 "upcoming": sum(1 for a in appts if today < a["date"] <= end
                                                 and a["status"] == "scheduled")}
            out["clients"] = {"seen": len(seen), "new": len(new), "returning": len(seen - new)}
        if "bill.read" in perms:
            L = self._ledger()
            money = {}

            def acc(cur):
                return money.setdefault(cur, {"billed": 0, "collected": 0, "written_off": 0, "outstanding": 0})
            for i in L["invoices"].values():
                if i["status"] == "issued" and start <= i["date"] <= end:
                    acc(i["currency"])["billed"] += i["total"]
            for y in L["payments"].values():
                if y["status"] != "active" or not start <= y["date"] <= end:
                    continue
                m = acc(y["currency"])
                if y["kind"] == "write_off":
                    m["written_off"] += y["amount"]
                else:
                    m["collected"] += -y["amount"] if y["kind"] == "refund" else y["amount"]
            for cur, v in self._balance(L["invoices"].values(), L["payments"].values()).items():
                acc(cur)["outstanding"] = v
            for m in money.values():
                m["per_visit"] = (int(round(m["collected"] / float(visits))) if visits else None)
            out["money"] = money
        if "clin.read" in perms:
            series = {k: {} for k in DASH_MEASURES}
            for pid in self.c._index()["patients"]:
                try:
                    chart = self.c._load_chart(pid)
                except Exception:
                    continue
                for m in chart.get("measures", []):
                    if m.get("status") == "active" and m["instrument"] in series and m["date"] <= end:
                        series[m["instrument"]].setdefault(pid, []).append(m)
            outcomes = {}
            for k, per in series.items():
                changes = []
                for ms in per.values():
                    ms.sort(key=lambda m: (m["date"], m["at"]))
                    if len(ms) >= 2 and ms[-1]["date"] >= start:
                        changes.append(ms[-1]["total"] - ms[0]["total"])
                outcomes[k] = {"name": INSTRUMENTS[k]["name"], "clients": len(changes),
                               "mean_change": round(sum(changes) / float(len(changes)), 1) if changes else None,
                               "improved": sum(1 for c in changes if c <= -DASH_CHANGE),
                               "worse": sum(1 for c in changes if c >= DASH_CHANGE)}
            out["outcomes"] = outcomes
        self.c._commit({}, user["id"], "dashboard.view", "practice", {"from": start, "to": end,
                                                                      "sections": sorted(k for k in out if k not in ("from", "to"))})
        return out

    # ==================================================================================================
    # Appointment reminders: a text to copy and a daily list to call or text. Nothing is sent from here.
    # ==================================================================================================
    def _reminder_template(self, user):
        t = self.store.read_obj(REMINDERS, {}).get("template")
        if t:
            return t
        i18n = _opt("i18n")
        loc = self.c.locale_settings().get("locale") or "en"
        if i18n is not None:
            for lc in (loc, "en"):
                try:
                    return i18n.t("pm.rem.default_text", lc)
                except Exception:
                    continue
        return REMINDER_DEFAULT

    def save_reminder_template(self, user, text):
        self.c.require(user, "sched.write")
        text = self._text(text, 500, "reminder text", multiline=True)
        with self.store.lock:
            self.c._commit({REMINDERS: self.c.codec.dump({"template": text})}, user["id"], "appt.reminder_text",
                           "practice", {"length": len(text)})
        return {"template": text or self._reminder_template(user)}

    @staticmethod
    def reminder_text(template, a, practice="", phone=""):
        vals = {"name": (a.get("patient_name") or "").split(" ")[0] if a.get("patient_name") else "",
                "date": a["date"], "time": a["time"], "clinician": a.get("clinician_name", ""),
                "practice": practice, "phone": phone}
        return re.sub(r"\[(name|date|time|clinician|practice|phone)\]", lambda m: vals[m.group(1)], template)

    def reminders(self, user, day=None, clinician=""):
        """Still-scheduled appointments of one day (default tomorrow) with the client's phone and the reminder
        text, plus what was recorded so far. Sending is done by people, from their own phone or e-mail."""
        self.c.require(user, "sched.read")
        day = self._day(day, "date") or (date.today() + timedelta(days=1)).isoformat()
        appts = [a for a in self.appointments(user, day, day, clinician)["appts"] if a["status"] == "scheduled"]
        prof = self._ledger().get("profile") or {}
        tpl = self._reminder_template(user)
        for a in appts:
            try:
                d = self.c._load_chart(a["patient"])["demographics"]
                a["phone"] = d.get("phone") or ""
            except Exception:
                a["phone"] = ""
            a["text"] = self.reminder_text(tpl, a, prof.get("practice_name", ""), prof.get("phone", ""))
        self.c._commit({}, user["id"], "appt.reminder_list", "practice", {"date": day, "count": len(appts)})
        return {"date": day, "template": tpl, "appts": appts, "outcomes": list(REMINDER_OUTCOMES)}

    def record_reminder(self, user, aid, day, outcome, note=""):
        self.c.require(user, "sched.write")
        outcome = self._choice(outcome, REMINDER_OUTCOMES, "outcome")
        note = self._text(note, 200, "note")
        with self.store.lock:
            m, a = self._find(aid, day)
            r = {"at": self.H.now_iso(), "by": user["id"], "by_name": user.get("display", ""), "outcome": outcome,
                 "note": note}
            a.setdefault("reminders", []).append(r)
            self.c._commit({self._month_rel(a["date"]): self.c.codec.dump(m)}, user["id"], "appt.reminder",
                           a["patient"], {"appt": a["id"], "outcome": outcome})
        return {"appt": a}

    # ==================================================================================================
    # Letters (school, work, referral, to whom it may concern): drafted, PIN-signed, sealed in the chart
    # ==================================================================================================
    def _letter_seal(self, pid, x):
        content = self.H.sha256_bytes(self.H.canon({k: x.get(k) for k in LETTER_SEALED}))
        sig = hmac.new(self.c.sig_key, self.H.canon({"patient": pid, "letter": x["id"], "content": content,
                                                     "signer": x.get("signed_by"), "at": x.get("signed_at")}),
                       hashlib.sha256).hexdigest()
        return content, sig

    def _letter_view(self, pid, x):
        out = dict(x)
        if x.get("signature"):
            content, sig = self._letter_seal(pid, x)
            out["signature_valid"] = content == x.get("content_sha") and hmac.compare_digest(sig, x["signature"])
        return out

    def letters(self, user, pid):
        self.c.require(user, "clin.read")
        chart = self.c._load_chart(pid)
        return {"letters": [self._letter_view(pid, x) for x in chart.get("letters", [])], "kinds": list(LETTER_KINDS)}

    def save_letter(self, user, pid, b, letter_id=None):
        self.c.require(user, "note.sign")
        if not isinstance(b, dict):
            raise PracticeError(400, "letter required")
        f = {"kind": self._choice(b.get("kind"), LETTER_KINDS, "kind", "twimc"),
             "recipient": self._text(b.get("recipient"), 500, "recipient", multiline=True),
             "subject": self._text(b.get("subject"), 200, "subject"),
             "body": self._text(b.get("body"), 20000, "letter", multiline=True),
             "date": self._day(b.get("date"), "letter date", not_future=True) or self._today()}
        if not f["body"].strip():
            raise PracticeError(400, "The letter is empty.")
        now = self.H.now_iso()
        with self.store.lock:
            chart = self.c._load_chart(pid)
            ls = chart.setdefault("letters", [])
            if letter_id:
                x = next((y for y in ls if y["id"] == letter_id), None)
                if x is None:
                    raise PracticeError(404, "No such letter.")
                if x.get("signature") or x["status"] != "draft":
                    raise PracticeError(423, "A signed letter cannot be changed. Write a new one.")
                x.update(f, updated=now)
                action = "letter.update"
            else:
                x = dict(f, id=self.H.new_id("L"), status="draft", author=user["id"],
                         author_name=user.get("display", ""), created=now, updated=now, signed_by=None,
                         signed_by_name=None, signed_at=None, content_sha=None, signature=None)
                ls.append(x)
                action = "letter.create"
            self.c._save_chart(chart, user["id"], action, {"letter": x["id"], "kind": x["kind"]}, index=False)
        return self._letter_view(pid, x)

    def sign_letter(self, user, pid, lid, pin):
        self.c.require(user, "note.sign")
        with self.store.lock:
            chart = self.c._load_chart(pid)
            x = next((y for y in chart.get("letters", []) if y["id"] == lid), None)
            if x is None:
                raise PracticeError(404, "No such letter.")
            if x["status"] != "draft":
                raise PracticeError(409, "This letter is already signed.")
            if x["author"] != user["id"]:
                raise PracticeError(403, "Only the author can sign this letter.")
            self.c.verify_pin(user, pin)
            chart = self.c._load_chart(pid)
            x = next(y for y in chart["letters"] if y["id"] == lid)
            x.update(status="signed", signed_by=user["id"], signed_by_name=user.get("display", ""),
                     signed_at=self.H.now_iso())
            x["content_sha"], x["signature"] = self._letter_seal(pid, x)
            self.c._save_chart(chart, user["id"], "letter.sign", {"letter": lid, "content_sha": x["content_sha"]},
                               index=False)
        return self._letter_view(pid, x)

    def letter_error(self, user, pid, lid, reason):
        self.c.require(user, "note.sign")
        reason = self._text(reason, 300, "reason")
        if not reason:
            raise PracticeError(400, "A reason is required.")
        with self.store.lock:
            chart = self.c._load_chart(pid)
            x = next((y for y in chart.get("letters", []) if y["id"] == lid), None)
            if x is None:
                raise PracticeError(404, "No such letter.")
            if x["status"] == "entered_in_error":
                raise PracticeError(409, "Already marked entered in error.")
            x.update(status="entered_in_error", error_reason=reason, error_by=user["id"], error_at=self.H.now_iso())
            self.c._save_chart(chart, user["id"], "letter.entered_in_error", {"letter": lid}, index=False)
        return self._letter_view(pid, x)

    def letter_pdf(self, user, pid, lid):
        self.c.require(user, "clin.read")
        chart = self.c._load_chart(pid)
        x = next((y for y in chart.get("letters", []) if y["id"] == lid), None)
        if x is None:
            raise PracticeError(404, "No such letter.")
        if not x.get("signature"):
            raise PracticeError(409, "Sign the letter first.")
        lab, loc = self._labels(user)
        prof = self._ledger().get("profile") or {}
        v = self._letter_view(pid, x)
        L = [(prof.get("practice_name") or self.H.APP_NAME, True),
             (" | ".join(s for s in (prof.get("address"), prof.get("phone"), prof.get("email")) if s), False),
             (x["date"], False)]
        if x.get("recipient"):
            L.append((x["recipient"], False))
        if x.get("subject"):
            L.append((x["subject"], True))
        L.append((x["body"], False))
        L.append((lab("pm.letter.signed_line", name=x.get("signed_by_name") or x["signed_by"], when=x["signed_at"],
                      code=x["content_sha"][:12]), False))
        if not v.get("signature_valid"):
            L.append((lab("app.chip.sig_invalid"), True))
        if x["status"] == "entered_in_error":
            L.append((lab("pm.meas.in_error", reason=x.get("error_reason", "")), True))
        if chart.get("synthetic"):
            L.insert(0, ("DEMO - synthetic patient, not a real person", True))
        self.c._commit({}, user["id"], "letter.print", pid, {"letter": lid})
        data, ext = _pdf(x.get("subject") or lab("pm.lkind." + x["kind"]), L, loc)
        return ("file", "letter-%s.%s" % (lid, ext), data)

    # ==================================================================================================
    # Waitlist
    # ==================================================================================================
    def waitlist(self, user):
        self.c.require(user, "sched.read")
        db = self.store.read_obj(WAITLIST, {"entries": {}})
        pts = self.c._index()["patients"]
        out = []
        for e in db["entries"].values():
            e = dict(e, patient_name=(pts.get(e["patient"]) or {}).get("name", ""))
            out.append(e)
        out.sort(key=lambda e: (e["status"] != "waiting", e["added"]))
        return {"entries": out, "statuses": list(WAIT_STATUSES)}

    def waitlist_add(self, user, b):
        self.c.require(user, "sched.write")
        if not isinstance(b, dict):
            raise PracticeError(400, "entry required")
        pid = b.get("patient")
        self._patient(pid)
        clin = str(b.get("clinician") or "")
        if clin:
            self._clinician(clin)
        e = {"id": self.H.new_id("W"), "patient": pid, "clinician": clin,
             "preferences": self._text(b.get("preferences"), 300, "preferences"),
             "note": self._text(b.get("note"), 300, "note"), "status": "waiting", "added": self.H.now_iso(),
             "added_by": user["id"], "added_by_name": user.get("display", ""), "history": []}
        with self.store.lock:
            db = self.store.read_obj(WAITLIST, {"entries": {}})
            if sum(1 for x in db["entries"].values() if x["status"] == "waiting") >= MAX_WAITING:
                raise PracticeError(400, "The waitlist holds at most %d waiting clients." % MAX_WAITING)
            db["entries"][e["id"]] = e
            self.c._commit({WAITLIST: self.c.codec.dump(db)}, user["id"], "waitlist.add", pid, {"entry": e["id"]})
        return e

    def waitlist_status(self, user, wid, status, reason=""):
        self.c.require(user, "sched.write")
        status = self._choice(status, WAIT_STATUSES, "status")
        reason = self._text(reason, 200, "reason")
        with self.store.lock:
            db = self.store.read_obj(WAITLIST, {"entries": {}})
            e = db["entries"].get(str(wid or ""))
            if e is None:
                raise PracticeError(404, "No such waitlist entry.")
            e["history"].append({"at": self.H.now_iso(), "by": user["id"], "from": e["status"], "to": status,
                                 "reason": reason})
            e["status"] = status
            self.c._commit({WAITLIST: self.c.codec.dump(db)}, user["id"], "waitlist.status", e["patient"],
                           {"entry": e["id"], "status": status})
        return e

    # ==================================================================================================
    # Group sessions: one booking for every member; one note fanned out to each member's chart
    # ==================================================================================================
    def _groups(self):
        return self.store.read_obj(GROUPS, {"groups": {}})

    def groups(self, user):
        self._req(user, "sched.read", "clin.read")
        pts = self.c._index()["patients"]
        out = []
        for g in self._groups()["groups"].values():
            out.append(dict(g, member_names={p: (pts.get(p) or {}).get("name", "") for p in g["members"]}))
        out.sort(key=lambda g: (g["status"] != "active", g["name"].lower()))
        return {"groups": out}

    def save_group(self, user, b, gid=None):
        self.c.require(user, "sched.write")
        if not isinstance(b, dict):
            raise PracticeError(400, "group required")
        name = self._text(b.get("name"), 120, "name")
        if not name:
            raise PracticeError(400, "The group needs a name.")
        u = self._clinician(b.get("clinician"))
        members = b.get("members") or []
        if not isinstance(members, list):
            raise PracticeError(400, "members must be a list")
        members = list(dict.fromkeys(str(m) for m in members))
        if not 2 <= len(members) <= MAX_GROUP:
            raise PracticeError(400, "A group has 2 to %d members." % MAX_GROUP)
        for m in members:
            self._patient(m)
        status = self._choice(b.get("status"), ("active", "closed"), "status", "active")
        now = self.H.now_iso()
        with self.store.lock:
            db = self._groups()
            if gid:
                g = db["groups"].get(gid)
                if g is None:
                    raise PracticeError(404, "No such group.")
                g.update(name=name, clinician=u["id"], clinician_name=u.get("display") or u["username"],
                         members=members, status=status, updated=now)
            else:
                g = {"id": self.H.new_id("G"), "name": name, "clinician": u["id"],
                     "clinician_name": u.get("display") or u["username"], "members": members, "status": status,
                     "created": now, "updated": now, "created_by": user["id"]}
                db["groups"][g["id"]] = g
            self.c._commit({GROUPS: self.c.codec.dump(db)}, user["id"], "group.save", g["id"],
                           {"members": len(members), "status": status})
        return g

    def _group(self, gid):
        g = self._groups()["groups"].get(str(gid or ""))
        if g is None:
            raise PracticeError(404, "No such group.")
        return g

    def book_group(self, user, gid, b):
        """One appointment per member at the same time (a group session). Overlaps with the clinician's other
        appointments need allow_overlap, as for single bookings; members never conflict with each other."""
        self.c.require(user, "sched.write")
        g = self._group(gid)
        if g["status"] != "active":
            raise PracticeError(409, "This group is closed.")
        first = self._appt_fields(dict(b or {}, clinician=g["clinician"], kind=(b or {}).get("kind") or "session"))
        sess = self.H.new_id("R")
        now = self.H.now_iso()
        with self.store.lock:
            m = self._month(first["date"])
            months = {first["date"][:7]: m}
            conflicts = self._conflicts(months, first)
            if conflicts and not (b or {}).get("allow_overlap"):
                raise PracticeError(409, "Overlaps %d other appointment(s) of this clinician. Choose another time "
                                         "or book anyway." % len(conflicts))
            created = []
            for pid in g["members"]:
                a = dict(first, id=self.H.new_id("S"), patient=pid, status="scheduled", series=None, seq=None,
                         group=g["id"], group_name=g["name"], group_session=sess, created=now,
                         created_by=user["id"], created_by_name=user.get("display", ""), history=[])
                m["appts"][a["id"]] = a
                created.append(a)
            idx = self.store.read_obj(SCHED_INDEX, {"patients": {}})
            for pid in g["members"]:
                idx["patients"][pid] = sorted(set(idx["patients"].get(pid, [])) | {first["date"][:7]})
            self.c._commit({self._month_rel(first["date"]): self.c.codec.dump(m), SCHED_INDEX: self.c.codec.dump(idx)},
                           user["id"], "group.book", g["id"], {"appts": [a["id"] for a in created], "session": sess})
        return {"created": created, "conflicts": conflicts}

    def group_note(self, user, gid, b):
        """One group note, signed into each chosen member's chart as that member's own signed note (shared text +
        an optional line for that member). Other members are never named by the app; the text is the author's."""
        self.c.require(user, "note.sign")
        g = self._group(gid)
        b = b or {}
        text = self._text(b.get("text"), 50000, "note", multiline=True)
        if not text.strip():
            raise PracticeError(400, "The group note is empty.")
        members = b.get("members") or g["members"]
        if not isinstance(members, list) or not members or any(m not in g["members"] for m in members):
            raise PracticeError(400, "Choose members of this group.")
        individual = b.get("individual") or {}
        if not isinstance(individual, dict):
            raise PracticeError(400, "individual must be an object")
        when = self._day(b.get("date"), "date", not_future=True) or self._today()
        title = self._text(b.get("title"), 120, "title") or g["name"]
        self.c.verify_pin(user, b.get("pin"))
        out = []
        for pid in members:
            own = self._text(individual.get(pid), 5000, "member note", multiline=True)
            full = text + ("\n\n" + own if own.strip() else "")
            e = self.c.add_encounter(user, pid, "visit", g["name"][:200], when)
            n = self.c.add_note(user, pid, e["id"], full, title)
            n = self.c.sign_note(user, pid, n["id"], b.get("pin"))
            out.append({"patient": pid, "encounter": e["id"], "note": n["id"], "status": n["status"]})
        self.c._commit({}, user["id"], "group.note", g["id"], {"notes": [x["note"] for x in out], "date": when})
        return {"notes": out}

    # ==================================================================================================
    # Sliding-scale fees and coverage (for the CMS-1500 field sheet)
    # ==================================================================================================
    def _sliding(self, L):
        return L.setdefault("sliding", {"tiers": [], "patients": {}})

    def save_sliding(self, user, tiers):
        self.c.require(user, "bill.write")
        if not isinstance(tiers, list) or len(tiers) > MAX_TIERS:
            raise PracticeError(400, "At most %d sliding-scale tiers." % MAX_TIERS)
        out, ids = [], set()
        for t in tiers:
            if not isinstance(t, dict):
                raise PracticeError(400, "tier required")
            name = self._text(t.get("name"), 60, "tier name")
            if not name:
                continue
            try:
                pct = int(t.get("percent"))
            except (TypeError, ValueError):
                raise PracticeError(400, "A tier discount is a whole percent from 0 to 100.")
            if not 0 <= pct <= 100:
                raise PracticeError(400, "A tier discount is a whole percent from 0 to 100.")
            tid = str(t.get("id") or "")
            if not re.fullmatch(r"t[0-9a-f]{1,12}", tid) or tid in ids:
                tid = "t" + hashlib.sha256((name + str(len(out)) + self.H.now_iso()).encode()).hexdigest()[:8]
            ids.add(tid)
            out.append({"id": tid, "name": name, "percent": pct})
        with self.store.lock:
            L = self._ledger()
            s = self._sliding(L)
            s["tiers"] = out
            s["patients"] = {p: t for p, t in s["patients"].items() if t in ids}
            self.c._commit({BILLING: self.c.codec.dump(L)}, user["id"], "billing.sliding", "billing",
                           {"tiers": len(out)})
        return {"tiers": out}

    def set_patient_tier(self, user, pid, tier):
        self.c.require(user, "bill.write")
        self._patient(pid)
        tier = str(tier or "")
        with self.store.lock:
            L = self._ledger()
            s = self._sliding(L)
            if tier and tier not in {t["id"] for t in s["tiers"]}:
                raise PracticeError(400, "Unknown sliding-scale tier.")
            if tier:
                s["patients"][pid] = tier
            else:
                s["patients"].pop(pid, None)
            self.c._commit({BILLING: self.c.codec.dump(L)}, user["id"], "billing.sliding_patient", pid,
                           {"tier": tier})
        return {"patient": pid, "tier": tier}

    def save_coverage(self, user, pid, cov):
        self.c.require(user, "bill.write")
        self._patient(pid)
        if not isinstance(cov, dict):
            raise PracticeError(400, "coverage required")
        out = {}
        for k, n in COVERAGE_FIELDS.items():
            out[k] = self._text(cov.get(k), n, k, multiline=(k in ("payer_address", "insured_address")))
        out["signature_on_file"] = bool(cov.get("signature_on_file"))
        out["plan_type"] = self._choice(cov.get("plan_type"), PLAN_TYPES, "plan type", "group")
        out["relationship"] = self._choice(cov.get("relationship"), RELATIONSHIPS, "relationship", "self")
        out["insured_dob"] = self._day(cov.get("insured_dob"), "insured date of birth") or ""
        with self.store.lock:
            L = self._ledger()
            L.setdefault("coverage", {})[pid] = out
            self.c._commit({BILLING: self.c.codec.dump(L)}, user["id"], "billing.coverage", pid,
                           {"payer": out.get("payer_name", "")[:40]})
        return {"coverage": out}

    def cms1500(self, user, invoice_id):
        """CMS-1500 (02/12) field sheet: every box number with the value from the issued invoice, the patient's
        coverage and the practice profile, to copy onto the official red-ink form or into a payer portal. NOT
        validated with a clearinghouse or payer; codes are the clinic's own typing, checked for format only."""
        self.c.require(user, "bill.read")
        L = self._ledger()
        i = L["invoices"].get(str(invoice_id or ""))
        if i is None:
            raise PracticeError(404, "No such invoice.")
        if i["status"] != "issued":
            raise PracticeError(409, "Issue the invoice first; the CMS-1500 sheet is made from an issued invoice.")
        chart = self.c._load_chart(i["patient"])
        d = chart["demographics"]
        prof = L.get("profile") or {}
        cov = (L.get("coverage") or {}).get(i["patient"]) or {}
        pinfo = (L.get("providers") or {}).get(i.get("provider")) or {}
        prov = self.c._users()["users"].get(i.get("provider")) or {}
        paid = self._paid(i["id"], [y for y in L["payments"].values() if y["patient"] == i["patient"]])
        cur = i["currency"]
        letters = "ABCDEFGHIJKL"

        def mmddyy(s):
            return "%s %s %s" % (s[5:7], s[8:10], s[0:4]) if s else ""
        rows = [("1", "Insurance type", cov.get("plan_type", "")),
                ("1a", "Insured's ID number", cov.get("member_id", "")),
                ("2", "Patient's name", d.get("name", "")),
                ("3", "Patient's birth date, sex", "%s  %s" % (mmddyy(d.get("dob", "")), d.get("sex", ""))),
                ("4", "Insured's name", cov.get("insured_name") or (d.get("name", "") if cov.get("relationship", "self") == "self" else "")),
                ("5", "Patient's address, telephone", "%s  %s" % (d.get("address", ""), d.get("phone", ""))),
                ("6", "Patient relationship to insured", cov.get("relationship", "")),
                ("7", "Insured's address", cov.get("insured_address", "")),
                ("11", "Insured's policy group or FECA number", cov.get("group", "")),
                ("11a", "Insured's date of birth", mmddyy(cov.get("insured_dob", ""))),
                ("11c", "Insurance plan name", cov.get("payer_name", "")),
                ("12", "Patient's signature", "Signature on file" if cov.get("signature_on_file") else ""),
                ("21", "ICD indicator", "0 (ICD-10-CM)")]
        for n, dx in enumerate(i["diagnoses"]):
            rows.append(("21" + letters[n], "Diagnosis", dx["code"].replace(".", "")))
        for n, ln in enumerate(i["lines"], 1):
            rows.append(("24 line %d" % n, "A date / B place / D code+modifiers / E pointer / F charge / G units / J NPI",
                         "%s | %s | %s %s | %s | %s | %d | %s" % (
                             mmddyy(ln["dos"]), ln["pos"] or "", ln["code"], " ".join(ln["modifiers"]),
                             "".join(letters[p - 1] for p in ln["dx"]), money_text(ln["fee"] * ln["units"], cur).strip(),
                             ln["units"], pinfo.get("npi", ""))))
        rows += [("25", "Federal tax ID number", prof.get("tax_id", "")),
                 ("26", "Patient's account number", d.get("mrn") or i["patient"]),
                 ("28", "Total charge", money_text(i.get("subtotal", i["total"]), cur)),
                 ("29", "Amount paid", money_text(paid, cur)),
                 ("31", "Signature of physician or supplier", "%s %s  %s" % (
                     prov.get("display", ""), pinfo.get("credentials", ""), mmddyy(i["date"]))),
                 ("32", "Service facility location", "%s  %s" % (prof.get("practice_name", ""), prof.get("address", ""))),
                 ("32a", "Service facility NPI", prof.get("npi", "")),
                 ("33", "Billing provider info and phone", "%s  %s  %s" % (
                     prof.get("practice_name", ""), prof.get("address", ""), prof.get("phone", ""))),
                 ("33a", "Billing provider NPI", prof.get("npi", ""))]
        lab, loc = self._labels(user)
        lines = [("CMS-1500 (02/12) - %s" % lab("pm.cms.sheet"), True), (lab("pm.cms.not_validated"), True),
                 ("%s %s   %s %s   %s: %s" % (lab("pm.bill.col_number"), i["number"], lab("app.field.date"), i["date"],
                                             lab("pm.cms.payer"), cov.get("payer_name") or "-"), False),
                 (cov.get("payer_address", ""), False)]
        if len(i["lines"]) > 6:
            lines.append((lab("pm.cms.two_forms"), True))
        if i.get("discount"):
            lines.append((lab("pm.cms.sliding_note"), False))
        for box, label, value in rows:
            lines.append(("Box %s - %s: %s" % (box, label, value or "-"), False))
        if chart.get("synthetic"):
            lines.insert(0, ("DEMO - synthetic patient, not a real person", True))
        self.c._commit({}, user["id"], "claim.cms1500", i["patient"], {"invoice": i["id"]})
        data, ext = _pdf("CMS-1500 " + i["number"], lines, loc)
        return ("file", "cms1500-%s.%s" % (i["number"], ext), data)

    # ==================================================================================================
    # Documents: superbill, full chart (PDF + JSON), access report (CSV)
    # ==================================================================================================
    def _labels(self, user):
        i18n = _opt("i18n")
        loc = (user.get("prefs") or {}).get("locale") or self.c.locale_settings().get("locale") or "en"

        def lab(key, **p):
            if i18n is None:
                return key
            try:
                en = i18n.t(key, "en", p)
                tr = i18n.t(key, loc, p) if loc != "en" else en
            except KeyError:
                return key
            return tr if tr == en else "%s (%s)" % (tr, en)
        return lab, loc

    def superbill(self, user, invoice_id):
        self.c.require(user, "bill.read")
        L = self._ledger()
        i = L["invoices"].get(str(invoice_id or ""))
        if i is None:
            raise PracticeError(404, "No such invoice.")
        if i["status"] != "issued":
            raise PracticeError(409, "Issue the invoice first; a superbill is made from an issued invoice.")
        chart = self.c._load_chart(i["patient"])
        d = chart["demographics"]
        lab, loc = self._labels(user)
        prof = L.get("profile") or {}
        pays = [y for y in L["payments"].values() if y["patient"] == i["patient"]]
        paid = self._paid(i["id"], pays)
        users = self.c._users()["users"]
        prov = users.get(i.get("provider")) or {}
        pinfo = (L.get("providers") or {}).get(i.get("provider")) or {}
        cur = i["currency"]
        lines = [(lab("pm.sb.title"), True),
                 (" | ".join(x for x in (prof.get("practice_name"), prof.get("address"), prof.get("phone")) if x), False),
                 ("%s: %s   %s: %s" % (lab("pm.prof.tax_id"), prof.get("tax_id") or "-", lab("pm.bill.npi"),
                                       prof.get("npi") or "-"), False),
                 (lab("pm.f.patient"), True),
                 ("%s   %s: %s" % (d.get("name", ""), lab("app.field.dob"), d.get("dob", "")), False),
                 (d.get("address") or "", False),
                 (lab("pm.bill.provider"), True),
                 ("%s %s   %s: %s   %s: %s" % (prov.get("display", ""), pinfo.get("credentials", ""), lab("pm.bill.npi"),
                                             pinfo.get("npi") or "-", lab("pm.bill.license"), pinfo.get("license") or "-"),
                  False),
                 ("%s %s   %s %s" % (lab("pm.bill.col_number"), i["number"], lab("app.field.date"), i["date"]), False),
                 (lab("pm.bill.diagnoses"), True)]
        for n, dx in enumerate(i["diagnoses"], 1):
            lines.append(("%d. %s %s" % (n, dx["code"], dx["description"]), False))
        lines.append((lab("pm.bill.services"), True))
        for ln in i["lines"]:
            lines.append(("%s  %s%s  x%d  %s  %s %s  %s %s  %s" % (
                ln["dos"], ln["code"], ("-" + "-".join(ln["modifiers"])) if ln["modifiers"] else "", ln["units"],
                money_text(ln["fee"] * ln["units"], cur), lab("pm.bill.pos"), ln["pos"] or "-", lab("pm.bill.dx_ptr"),
                ",".join(str(p) for p in ln["dx"]) or "-", ln["description"]), False))
        if i.get("discount"):
            lines.append((lab("pm.slide.line", tier=i["sliding"]["tier"], percent=i["sliding"]["percent"],
                              amount=money_text(i["discount"], cur), subtotal=money_text(i["subtotal"], cur)), False))
        lines += [("%s: %s   %s: %s   %s: %s" % (lab("pm.bill.col_total"), money_text(i["total"], cur), lab("pm.bill.col_paid"),
                                                 money_text(paid, cur), lab("pm.bill.col_balance"),
                                                 money_text(i["total"] - paid, cur)), True),
                  (lab("pm.bill.codes_note"), False)]
        if prof.get("footer"):
            lines.append((prof["footer"], False))
        if chart.get("synthetic"):
            lines.insert(0, ("DEMO - synthetic patient, not a real person", True))
        self.c._commit({}, user["id"], "superbill.export", i["patient"], {"invoice": i["id"]})
        data, ext = _pdf(lab("pm.sb.title"), lines, loc)
        return ("file", "superbill-%s.%s" % (i["number"], ext), data)

    def subject_data(self, sid):
        """consent_center subject-access export: this person's appointments and billing (the chart itself, with
        measures and plans, is exported by consent_center). Not erased on request: like the clinical record they
        fall under retention duties, which consent_center hands to the privacy officer."""
        if not isinstance(sid, str) or not sid.startswith("P"):
            return {}
        idx = self.store.read_obj(SCHED_INDEX, {"patients": {}})
        appts = []
        for mk in idx["patients"].get(sid, []):
            appts += [a for a in self._month(mk + "-01")["appts"].values() if a["patient"] == sid]
        L = self._ledger()
        return {"appointments": sorted(appts, key=lambda a: (a["date"], a["time"])),
                "invoices": [i for i in L["invoices"].values() if i["patient"] == sid],
                "payments": [y for y in L["payments"].values() if y["patient"] == sid]}

    def _full(self, user, pid):
        self.c.require(user, "clin.read")
        perms = self.c.perms(user)
        chart = self.c._load_chart(pid)
        view = self.c.view(user, pid, audit=False)
        out = {"format": EXPORT_FORMAT, "format_version": EXPORT_VERSION, "app": self.H.APP_NAME,
               "app_version": self.H.VERSION, "exported_at": self.H.now_iso(), "exported_by": user["id"],
               "exported_by_name": user.get("display", ""), "synthetic": bool(chart.get("synthetic")),
               "chart": chart,
               "verification": {"notes": {n["id"]: {"signature_valid": n.get("signature_valid"),
                                                    "cosign_valid": (n.get("cosign") or {}).get("signature_valid")}
                                          for n in view.get("notes", []) if n.get("status") == "signed"},
                                "plans": {p["id"]: self._plan_view(pid, p).get("signature_valid")
                                          for p in chart.get("treatment_plans", []) if p.get("signature")},
                                "forms": {f["id"]: self._form_valid(pid, f) for f in chart.get("forms", [])},
                                "letters": {x["id"]: self._letter_view(pid, x).get("signature_valid")
                                            for x in chart.get("letters", []) if x.get("signature")}}}
        care = getattr(self.c, "care_suite", None)
        if care is not None:          # safety plans, releases and discharge summaries (care_suite seals)
            try:
                out["verification"].update(care.verification(pid, chart))
            except Exception:
                self.c.health("%s: care_suite verification failed\n%s" % (LOG_PREFIX, traceback.format_exc()))
        if "sched.read" in perms:
            out["appointments"] = self.patient_appointments(user, pid)["appts"]
        if "bill.read" in perms:
            b = self.patient_billing(user, pid)
            out["billing"] = {"invoices": b["invoices"], "payments": b["payments"], "balance": b["balance"]}
        out["content_sha256"] = self.H.sha256_bytes(self.H.canon(out))
        return out, chart, view

    def export_json(self, user, pid):
        out, chart, _ = self._full(user, pid)
        self.c._commit({}, user["id"], "chart.export_full", pid, {"format": "json", "sha256": out["content_sha256"]})
        data = json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True).encode("utf-8")
        return ("file", "chart-%s-%s.json" % (pid, self._today()), data)

    def export_pdf(self, user, pid):
        out, chart, view = self._full(user, pid)
        lab, loc = self._labels(user)
        d = chart["demographics"]
        L = []
        add = L.append
        if chart.get("synthetic"):
            add(("DEMO - synthetic patient, not a real person", True))
        add((lab("pm.pdf.title"), True))
        add(("%s   %s: %s   %s: %s   MRN: %s" % (d.get("name", ""), lab("app.field.dob"), d.get("dob", ""),
                                              lab("app.field.sex"), d.get("sex", "") or "-", d.get("mrn", "") or "-"), False))
        for k in ("address", "phone", "email", "emergency_contact"):
            if d.get(k):
                add(("%s: %s" % (k.replace("_", " "), d[k]), False))
        for kind, main in (("allergies", "substance"), ("problems", "description"), ("medications", "name")):
            add((lab("app.items." + kind), True))
            items = chart.get(kind, [])
            if not items:
                add(("-", False))
            for it in items:
                extra = ", ".join("%s %s" % (f, it[f]) for f in sorted(it) if f not in (
                    main, "id", "history", "recorded_by", "recorded_by_name", "recorded_at") and it.get(f))
                add(("%s (%s) - %s %s" % (it.get(main, ""), extra, it.get("recorded_by_name", ""),
                                          it.get("recorded_at", "")), False))
        add((lab("pm.pdf.vitals"), True))
        for v in chart.get("vitals", []):
            vals = ", ".join("%s %s" % (k, x) for k, x in sorted(v.get("values", {}).items()))
            st = "" if v.get("status") == "active" else " [entered in error: %s]" % v.get("error_reason", "")
            add(("%s  %s  (%s)%s" % (v.get("at", ""), vals, v.get("by_name", ""), st), False))
        add((lab("app.chart.tab_enc"), True))
        notes = {n["id"]: n for n in view.get("notes", [])}
        for e in sorted(chart.get("encounters", []), key=lambda e: (e.get("date", ""), e.get("created", ""))):
            add(("%s  %s  %s  (%s)" % (e.get("date", ""), e.get("type", ""), e.get("reason", ""),
                                       e.get("created_by_name", "")), True))
            for n in chart.get("notes", []):
                if n.get("encounter_id") != e["id"]:
                    continue
                nv = notes.get(n["id"], n)
                if n["status"] == "signed":
                    st = "%s %s %s (%s)" % (lab("app.chip.signed"), n.get("signed_by_name", ""), n.get("signed_at", ""),
                                            lab("pm.pdf.sig_ok") if nv.get("signature_valid") else
                                            lab("app.chip.sig_invalid"))
                else:
                    st = lab("app.chip.draft")
                add(("%s - %s - %s" % (n.get("title", ""), n.get("author_name", ""), st), True))
                add((n.get("text", ""), False))
                cs = nv.get("cosign") or {}
                if cs.get("status") == "done":
                    add(("%s %s %s (%s)" % (lab("pm.pdf.cosigned"), cs.get("by_name", ""), cs.get("at", ""),
                                            lab("pm.pdf.sig_ok") if cs.get("signature_valid") else lab("app.chip.sig_invalid")),
                         False))
                elif cs.get("status") == "pending":
                    add(("%s %s" % (lab("pm.pdf.cosign_pending"), cs.get("required_name", "")), False))
                for a in nv.get("addenda", []):
                    add(("%s %s %s (%s)" % (lab("app.note.addendum"), a.get("author_name", ""), a.get("at", ""),
                                            lab("pm.pdf.sig_ok") if a.get("signature_valid") else lab("app.chip.sig_invalid")),
                         False))
                    add((a.get("text", ""), False))
        add((lab("app.pm.tab_meas"), True))
        for m in chart.get("measures", []):
            spec = INSTRUMENTS.get(m["instrument"], {})
            st = "" if m.get("status") == "active" else " [entered in error: %s]" % m.get("error_reason", "")
            add(("%s  %s  %d/%d  %s%s%s" % (m["date"], spec.get("name", m["instrument"]), m["total"], m.get("max", 0),
                                          lab("pm.band." + m["band"]), "  " + lab("pm.meas.flag_short") if m.get("flag")
                                          else "", st), False))
        add((lab("app.pm.tab_plan"), True))
        for p in chart.get("treatment_plans", []):
            pv = self._plan_view(chart["id"], p)
            sig = (" - %s %s %s (%s)" % (lab("app.chip.signed"), p.get("signed_by_name", ""), p.get("signed_at", ""),
                                         lab("pm.pdf.sig_ok") if pv.get("signature_valid") else lab("app.chip.sig_invalid"))
                   if p.get("signature") else " - " + lab("app.chip.draft"))
            add(("%s (v%d, %s)%s" % (p["title"], p.get("version", 1), p.get("status", ""), sig), True))
            if p.get("problems"):
                add((p["problems"], False))
            for gi, g in enumerate(p.get("goals", []), 1):
                add(("%d. %s [%s]" % (gi, g["text"], g["status"]), False))
                for oi, o in enumerate(g.get("objectives", []), 1):
                    add(("   %d.%d %s [%s]%s" % (gi, oi, o["text"], o["status"],
                                                  (" -> " + o["target"]) if o.get("target") else ""), False))
        if chart.get("forms"):
            add((lab("app.pm.forms"), True))
            for f in chart["forms"]:
                fl = self._form_lines(lab, f)
                add(fl[0])
                L.extend(fl[1:])
                add((lab("pm.form.seal_ok") if self._form_valid(chart["id"], f) else lab("app.chip.sig_invalid"), False))
        care = getattr(self.c, "care_suite", None)
        if care is not None:
            try:
                L.extend(care.pdf_sections(pid, chart, lab))
            except Exception:
                self.c.health("%s: care_suite export sections failed\n%s" % (LOG_PREFIX, traceback.format_exc()))
        if "appointments" in out:
            add((lab("app.pm.tab_appt"), True))
            for a in out["appointments"]:
                add(("%s %s  %d min  %s  %s  %s" % (a["date"], a["time"], a["minutes"], a["kind"], a["status"],
                                                   a.get("clinician_name", "")), False))
        if "billing" in out:
            add((lab("app.pm.tab_bill"), True))
            for i in out["billing"]["invoices"]:
                add(("%s %s %s %s" % (i["date"], i.get("number") or "-", i["status"], money_text(i["total"],
                                                                                              i["currency"])), False))
            for y in out["billing"]["payments"]:
                add(("%s %s %s %s %s" % (y["date"], y["kind"], y["method"], money_text(y["amount"], y["currency"]),
                                         y["status"]), False))
            for cur, v in out["billing"]["balance"].items():
                add(("%s: %s" % (lab("pm.bill.col_balance"), money_text(v, cur)), False))
        add(("%s %s - %s %s - SHA-256 %s" % (lab("pm.pdf.exported"), out["exported_at"], self.H.APP_NAME,
                                             self.H.VERSION, out["content_sha256"]), False))
        self.c._commit({}, user["id"], "chart.export_full", pid, {"format": "pdf", "sha256": out["content_sha256"]})
        data, ext = _pdf(lab("pm.pdf.title"), L, loc)
        return ("file", "chart-%s-%s.%s" % (pid, self._today(), ext), data)

    def access(self, user, patient="", actor="", action="", since="", until="", limit=500):
        """The audit trail filtered by patient, user, action prefix and date range, with people's names."""
        self.c.require(user, "audit.read")
        since = self._day(since, "from")
        until = self._day(until, "to")
        try:
            limit = max(1, min(int(limit or 500), 5000))
        except (TypeError, ValueError):
            limit = 500
        entries, _total = self.store.audit.entries(10 ** 9, 0)
        out = []
        for e in entries:
            if e.get("unreadable"):
                continue
            if patient and e.get("target") != patient:
                continue
            if actor and e.get("actor") != actor:
                continue
            if action and not str(e.get("action", "")).startswith(action):
                continue
            day = self._local_day(e.get("ts"))
            if (since and day < since) or (until and day > until):
                continue
            out.append({k: e.get(k) for k in ("seq", "ts", "actor", "action", "target", "detail")})
        names = self._names()
        pts = self.c._index()["patients"]
        return {"entries": out[:limit], "matched": len(out), "users": names,
                "patients": {e["target"]: pts[e["target"]].get("name", "") for e in out[:limit]
                             if e.get("target") in pts}}

    @staticmethod
    def _local_day(ts):
        """An audit time stamp (UTC) as the clinic computer's own calendar day, the day people filter by."""
        s = str(ts or "")
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone().date().isoformat()
        except ValueError:
            return s[:10]

    def access_csv(self, user, **kw):
        r = self.access(user, limit=5000, **kw)
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\r\n")
        w.writerow(["seq", "time_utc", "user_id", "user_name", "action", "target", "target_name", "detail"])
        for e in r["entries"]:
            w.writerow([_csv_cell(x) for x in (e["seq"], e["ts"], e["actor"], r["users"].get(e["actor"], ""),
                                               e["action"], e["target"], r["patients"].get(e["target"], ""),
                                               json.dumps(e.get("detail") or {}, sort_keys=True))])
        self.c._commit({}, user["id"], "audit.export", "audit", {"rows": len(r["entries"]),
                                                                  "filters": sorted(k for k, v in kw.items() if v)})
        return ("file", "access-report-%s.csv" % self._today(), ("﻿" + buf.getvalue()).encode("utf-8"))


# ======================================================================================================
# PDF (polyglot_engine; plain text when it is missing)
# ======================================================================================================
def _opt(name):
    try:
        return importlib.import_module(name)
    except Exception:
        return None


def _pdf(title, lines, lang="en"):
    """lines: [(text, is_heading)] -> (bytes, "pdf" | "txt")."""
    pe = _opt("polyglot_engine")
    if pe is not None:
        try:
            pdf = pe.PolyglotPdf(str(title), author="Stark Covenant", subject=str(title), lang=lang.split("-")[0])
            for text, head in lines:
                for part in str(text or "").split("\n"):
                    if not part.strip():
                        continue
                    (pdf.heading(part, bookmark=False) if head else pdf.paragraph(part))
            return pdf.build(), "pdf"
        except Exception:
            pass
    body = "\n".join(("== %s ==" % t) if h else str(t) for t, h in lines)
    return body.encode("utf-8"), "txt"


# ======================================================================================================
# clinic_app mount
# ======================================================================================================
def _host_module(app):
    clinic = getattr(app, "clinic", app)
    mod = sys.modules.get(type(clinic).__module__) if clinic is not None else None
    if mod is None or not hasattr(mod, "ROUTES"):
        mod = sys.modules.get("clinic_app") or importlib.import_module("clinic_app")
    return mod


def _route_table(suite_of):
    """(method, path) -> fn(clinic, user, query, body); suite_of(clinic) gives the PracticeSuite."""
    def pid(q, b):
        return b.get("patient") or q.get("patient")

    return {
        ("GET", "/api/pm/info"): lambda s, u, q, b: s.info(u),
        ("GET", "/api/pm/staff"): lambda s, u, q, b: {"staff": s.staff(u)},
        ("POST", "/api/pm/supervisor"): lambda s, u, q, b: s.set_supervisor(u, b.get("user"), b.get("supervisor")),
        ("GET", "/api/pm/today"): lambda s, u, q, b: {"today": s.today(u, q.get("date"))},
        ("GET", "/api/pm/appts"): lambda s, u, q, b: s.appointments(u, q.get("from"), q.get("to"), q.get("clinician", "")),
        ("GET", "/api/pm/appts/patient"): lambda s, u, q, b: s.patient_appointments(u, pid(q, b)),
        ("POST", "/api/pm/appts"): lambda s, u, q, b: s.book(u, b),
        ("POST", "/api/pm/appts/status"): lambda s, u, q, b: s.set_status(u, b.get("id"), b.get("date"), b.get("status"),
                                                                         b.get("reason", ""), b.get("scope", "one")),
        ("GET", "/api/pm/appts.ics"): lambda s, u, q, b: s.export_ics(u, q.get("from"), q.get("to"),
                                                                    q.get("clinician", ""), q.get("names", "")),
        ("GET", "/api/pm/dashboard"): lambda s, u, q, b: s.dashboard(u, q.get("from"), q.get("to")),
        ("GET", "/api/pm/reminders"): lambda s, u, q, b: s.reminders(u, q.get("date"), q.get("clinician", "")),
        ("POST", "/api/pm/reminders/text"): lambda s, u, q, b: s.save_reminder_template(u, b.get("template")),
        ("POST", "/api/pm/reminders"): lambda s, u, q, b: s.record_reminder(u, b.get("id"), b.get("date"),
                                                                           b.get("outcome"), b.get("note", "")),
        ("GET", "/api/pm/letters"): lambda s, u, q, b: s.letters(u, pid(q, b)),
        ("POST", "/api/pm/letters"): lambda s, u, q, b: {"letter": s.save_letter(u, pid(q, b), b.get("letter"),
                                                                                 b.get("letter_id"))},
        ("POST", "/api/pm/letters/sign"): lambda s, u, q, b: {"letter": s.sign_letter(u, pid(q, b), b.get("letter_id"),
                                                                                      b.get("pin"))},
        ("POST", "/api/pm/letters/error"): lambda s, u, q, b: {"letter": s.letter_error(u, pid(q, b), b.get("letter_id"),
                                                                                       b.get("reason"))},
        ("GET", "/api/pm/letters/pdf"): lambda s, u, q, b: s.letter_pdf(u, pid(q, b), q.get("letter")),
        ("GET", "/api/pm/waitlist"): lambda s, u, q, b: s.waitlist(u),
        ("POST", "/api/pm/waitlist"): lambda s, u, q, b: {"entry": s.waitlist_add(u, b)},
        ("POST", "/api/pm/waitlist/status"): lambda s, u, q, b: {"entry": s.waitlist_status(u, b.get("id"), b.get("status"),
                                                                                            b.get("reason", ""))},
        ("GET", "/api/pm/groups"): lambda s, u, q, b: s.groups(u),
        ("POST", "/api/pm/groups"): lambda s, u, q, b: {"group": s.save_group(u, b.get("group"), b.get("group_id"))},
        ("POST", "/api/pm/groups/book"): lambda s, u, q, b: s.book_group(u, b.get("group_id"), b.get("appt")),
        ("POST", "/api/pm/groups/note"): lambda s, u, q, b: s.group_note(u, b.get("group_id"), b),
        ("POST", "/api/pm/billing/sliding"): lambda s, u, q, b: s.save_sliding(u, b.get("tiers")),
        ("POST", "/api/pm/billing/tier"): lambda s, u, q, b: s.set_patient_tier(u, pid(q, b), b.get("tier")),
        ("POST", "/api/pm/billing/coverage"): lambda s, u, q, b: s.save_coverage(u, pid(q, b), b.get("coverage")),
        ("GET", "/api/pm/cms1500"): lambda s, u, q, b: s.cms1500(u, q.get("invoice")),
        ("GET", "/api/pm/forms/templates"): lambda s, u, q, b: s.form_templates(u),
        ("POST", "/api/pm/forms/templates"): lambda s, u, q, b: {"template": s.save_form_template(
            u, b.get("template"), b.get("template_id"))},
        ("POST", "/api/pm/forms/templates/status"): lambda s, u, q, b: {"template": s.form_template_status(
            u, b.get("template_id"), b.get("status"))},
        ("GET", "/api/pm/forms"): lambda s, u, q, b: s.patient_forms(u, pid(q, b)),
        ("POST", "/api/pm/forms"): lambda s, u, q, b: {"form": s.complete_form(u, pid(q, b), b)},
        ("POST", "/api/pm/forms/error"): lambda s, u, q, b: {"form": s.form_error(u, pid(q, b), b.get("form"),
                                                                                 b.get("reason"))},
        ("GET", "/api/pm/forms/pdf"): lambda s, u, q, b: s.form_pdf(u, pid(q, b), q.get("form")),
        ("POST", "/api/pm/appts/move"):lambda s, u, q, b: s.reschedule(u, b.get("id"), b.get("date"), b.get("appt") or {}),
        ("POST", "/api/pm/measures"): lambda s, u, q, b: {"measure": s.add_measure(u, pid(q, b), b.get("instrument"),
                                                                                  b.get("answers"), b.get("date"),
                                                                                  b.get("difficulty"), b.get("group"))},
        ("POST", "/api/pm/measures/error"): lambda s, u, q, b: {"measure": s.measure_error(u, pid(q, b), b.get("measure"),
                                                                                          b.get("reason"))},
        ("POST", "/api/pm/plans"): lambda s, u, q, b: {"plan": s.save_plan(u, pid(q, b), b.get("plan"), b.get("plan_id"))},
        ("POST", "/api/pm/plans/sign"): lambda s, u, q, b: {"plan": s.sign_plan(u, pid(q, b), b.get("plan_id"),
                                                                               b.get("pin"))},
        ("GET", "/api/pm/billing/profile"): lambda s, u, q, b: s.profile(u),
        ("POST", "/api/pm/billing/profile"): lambda s, u, q, b: s.save_profile(u, b.get("profile"), b.get("providers"),
                                                                              b.get("fees")),
        ("GET", "/api/pm/billing"): lambda s, u, q, b: s.patient_billing(u, pid(q, b)),
        ("GET", "/api/pm/billing/summary"): lambda s, u, q, b: s.billing_summary(u),
        ("POST", "/api/pm/invoices"): lambda s, u, q, b: {"invoice": s.save_invoice(u, pid(q, b), b.get("invoice"),
                                                                                   b.get("invoice_id"))},
        ("POST", "/api/pm/invoices/issue"): lambda s, u, q, b: {"invoice": s.issue_invoice(u, b.get("invoice_id"))},
        ("POST", "/api/pm/void"): lambda s, u, q, b: {"item": s.void(u, b.get("kind"), b.get("id"), b.get("reason"))},
        ("POST", "/api/pm/payments"): lambda s, u, q, b: {"payment": s.add_payment(u, pid(q, b), b.get("payment"))},
        ("GET", "/api/pm/superbill"): lambda s, u, q, b: s.superbill(u, q.get("invoice")),
        ("GET", "/api/pm/export.json"): lambda s, u, q, b: s.export_json(u, pid(q, b)),
        ("GET", "/api/pm/export.pdf"): lambda s, u, q, b: s.export_pdf(u, pid(q, b)),
        ("GET", "/api/pm/access"): lambda s, u, q, b: s.access(u, q.get("patient", ""), q.get("user", ""),
                                                              q.get("action", ""), q.get("from", ""), q.get("to", ""),
                                                              q.get("limit", 500)),
        ("GET", "/api/pm/access.csv"): lambda s, u, q, b: s.access_csv(u, patient=q.get("patient", ""),
                                                                      actor=q.get("user", ""),
                                                                      action=q.get("action", ""),
                                                                      since=q.get("from", ""), until=q.get("to", "")),
    }


def mount(app, routes=None):
    """Attach the practice suite to a CovenantApp (or a bare Clinic): clinic.practice_suite = the suite, and
    clinic_app's ROUTES gain /api/pm/... Never raises: on a problem it logs to the clinic health log, returns None."""
    clinic = getattr(app, "clinic", app)
    try:
        host = _host_module(app)
        suite = getattr(clinic, "practice_suite", None)
        if not isinstance(suite, PracticeSuite):
            suite = PracticeSuite(clinic, host)
            clinic.practice_suite = suite
        routes = host.ROUTES if routes is None else routes

        def wrap(fn):
            def run(c, u, q, b):
                try:
                    return fn(c.practice_suite, u, q, b)
                except PracticeError as e:
                    raise host.HttpError(e.status, e.msg) from None
            return run
        for key, fn in _route_table(None).items():
            routes[key] = wrap(fn)
        cc = getattr(clinic, "consent_center", None)
        if cc is not None and callable(getattr(cc, "register_holder", None)):
            try:                    # subject-access export includes appointments and billing
                cc.register_holder("practice_suite", export=suite.subject_data)
            except Exception:
                clinic.health("%s: consent_center holder registration failed\n%s" % (LOG_PREFIX,
                                                                                    traceback.format_exc()))
        return suite
    except Exception:
        try:
            clinic.health("%s mount failed\n%s" % (LOG_PREFIX, traceback.format_exc()))
        except Exception:
            pass
        return None
