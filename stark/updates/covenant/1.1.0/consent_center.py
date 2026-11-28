"""Stark Covenant - consent_center: privacy-by-design consent for patients AND staff users.

Standard library only, Python 3.10+. Informational, not legal advice: the jurisdiction profiles below set
conservative defaults and plain-language wording; the clinic must confirm them with a qualified lawyer.

What it does
  * Granular opt-in per PURPOSE (personalised wording/tone, learning preferences, reminders, nutrition help,
    coverage help, research participation, data sharing with each NAMED partner). Every purpose is OFF until
    the person says yes. Saying no never changes anyone's care.
  * Plain-language explanation of every purpose (what, which data, who sees it, how long, how to stop).
  * Age / guardian handling: a patient under the jurisdiction's consent age needs a parent or legal guardian,
    checked by a staff member. Guardian-given consent lapses (needs re-confirmation) when the patient comes of
    age. The age per jurisdiction is configurable.
  * Consent receipts in the Kantara Consent Receipt v1.1 shape, signed (JWS compact, HS256) with a key derived
    from the clinic's per-install audit key (held sealed by security_vault).
  * Withdraw any time, with immediate effect: every read checks the live consent state (no cache), and the
    data held for that purpose is deleted in the same transaction.
  * Data minimisation + purpose limitation IN CODE: each purpose declares the only fields it may hold; a
    reader (a learner, the persona assistant) can only read data through require()/read()/scoped(), which
    refuse unless that exact purpose is active for that exact person.
  * Retention schedules with automatic deletion (purge_expired(), run hourly by mount()).
  * Subject-access export (machine-readable JSON + readable PDF through polyglot_engine) and erasure
    ("forget me") that removes the data and leaves an audit tombstone without personal data.
  * Everything at rest goes through clinic_app's Store: vault-encrypted (security_vault), journaled, recorded
    in the hash-chained audit trail by ids only. In DEMO mode the clinic store is unencrypted and synthetic.

Honest limits
  * Receipts are signed with an HMAC key the clinic holds: the clinic can verify them, a third party cannot
    without the clinic's help (no public-key signature in the standard library).
  * Erasure replaces the data in the live store at once. Older encrypted backups still hold it until the
    backup rotation drops them (newest 20 kept). After restoring an old backup, call reapply_erasures()
    with the erasure register (export_erasure_register()), so erased people stay erased.
  * Clinical records are NOT deleted by "forget me": health-record retention laws usually require keeping
    them. The request is recorded for the clinic's privacy officer, who decides with legal advice.
  * Phrases a clinician's self_learner profile already counted cannot be traced back to one patient (they are
    masked and aggregated); withdrawing a clinician's learning consent shreds that clinician's whole profile.

Integration (clinic_app.CovenantApp.__init__, after the emergency_comms mount; see compliance/INTEGRATION.md):
    for _name in ("consent_center", "persona_ai"):  # optional modules; each one degrades safely when missing
        _m = optional(_name)
        setattr(self, _name, _m.mount(self) if _m is not None and callable(getattr(_m, "mount", None)) else None)
"""
from __future__ import annotations

import base64
import copy
import datetime as _dt
import hashlib
import hmac
import importlib
import json
import os
import re
import secrets
import sys
import threading
import time
import traceback

__all__ = ["ConsentCenter", "ConsentError", "ConsentRequired", "GuardianRequired", "PURPOSES", "JURISDICTIONS",
           "DISCLAIMER", "mount", "verify_receipt", "age_on"]

APP_NAME = "Stark Covenant"
SCHEMA = "gorgon-covenant/consent/1"
RECEIPT_VERSION = "KI-CR-v1.1.0"
RECEIPT_KID = "covenant-consent-receipt-v1"
DISCLAIMER = ("Informational, not legal advice. These settings and wordings are conservative defaults. Have a "
              "qualified lawyer in your jurisdiction review them before you rely on them. Stark Covenant makes no "
              "claim of certification or legal compliance.")
NO_EFFECT_ON_CARE = "Saying no, or changing your mind later, will not change your care in any way."
WITHDRAW_LINE = ("You can change your mind at any time. Tell any member of staff, and it stops straight away. "
                 "What we kept for it is deleted at the same time.")
SUBJECT_RE = re.compile(r"^[PU][0-9a-f]{12}$")
PARTNER_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
DAY = 86400.0
PURGE_INTERVAL = 3600.0
LOG_PREFIX = "[consent_center]"


# ======================================================================================================
# Errors (each carries the HTTP status clinic_app should answer with)
# ======================================================================================================
class ConsentError(Exception):
    status = 400

    def __init__(self, msg, status=None):
        super().__init__(msg)
        self.msg = msg
        if status is not None:
            self.status = status


class ConsentRequired(ConsentError):
    """A read or write was attempted for a purpose the person has not (or no longer) agreed to."""
    status = 403


class GuardianRequired(ConsentError):
    status = 400


# ======================================================================================================
# Purposes: what each one is for, the ONLY fields it may hold (data minimisation), plain language
# ======================================================================================================
_CHOICES = {
    "tone": ("warm", "formal", "brief"),
    "reading_level": ("plain", "standard"),
    "accessibility": ("large_text", "screen_reader", "plain_language", "interpreter", "deaf_or_hard_of_hearing",
                      "low_vision", "cognitive_support", "no_phone_calls"),
    "channel": ("sms", "email", "phone", "letter"),
}

PURPOSES = {
    "personalisation": {
        "title": "Personalised wording and tone",
        "applies_to": ("patient", "staff"),
        "fields": {"preferred_name": 60, "tone": "tone", "language": 20, "reading_level": "reading_level",
                   "accessibility": "accessibility[]", "pronouns": 30},
        "what": "Messages from us use the name you like to be called, the language you prefer, the tone you like "
                "(warm, formal or brief), simpler wording if you want it, and your accessibility needs.",
        "data": "Only what you tell us for this: preferred name, language, tone, reading level, accessibility "
                "needs, pronouns.",
        "who": "Staff at this clinic and the clinic's own assistant on this computer. Nobody outside the clinic.",
        "retention_days": 730,
        "sensitive": False,
    },
    "learning": {
        "title": "Learning your preferences",
        "applies_to": ("patient", "staff"),
        "fields": {"preferred_contact_time": 40, "communication_notes": 300, "topics_of_interest": "topics[]"},
        "what": "The system may remember what works well for you (for example the best time to contact you) "
                "so it can adjust over time. For staff: the writing helper learns your own phrases and templates "
                "from notes you have signed.",
        "data": "Patterns of how you like to communicate. For staff: masked phrases from your own signed notes.",
        "who": "Stays on this clinic's computer. Nothing is sent to any outside AI service.",
        "retention_days": 730,
        "sensitive": False,
    },
    "reminders": {
        "title": "Reminders",
        "applies_to": ("patient", "staff"),
        "fields": {"channel": "channel", "quiet_hours": 20, "advance_notice_hours": "int", "reminder_types":
                   "topics[]"},
        "what": "We send you reminders, for example for appointments or for repeat prescriptions to collect.",
        "data": "How and when you want reminders.",
        "who": "Staff at this clinic. A message service only if the clinic names one in its privacy notice.",
        "retention_days": 730,
        "sensitive": False,
    },
    "nutrition": {
        "title": "Nutrition information",
        "applies_to": ("patient",),
        "fields": {"dietary_pattern": 80, "food_preferences": 300, "cultural_or_religious_practices": 200,
                   "food_dislikes": 200},
        "what": "We share nutrition information that suits your food choices, culture and faith. A clinician "
                "checks anything health-related before it reaches you.",
        "data": "Food choices and practices you tell us about. Food allergies stay in your medical record, not "
                "here.",
        "who": "Your care team at this clinic.",
        "retention_days": 365,
        "sensitive": True,
    },
    "coverage_help": {
        "title": "Help with insurance or coverage",
        "applies_to": ("patient",),
        "fields": {"insurer_name": 120, "plan_type": 80, "help_needed": 300},
        "what": "Staff help you with insurance, benefits or coverage paperwork.",
        "data": "The name of your insurer or programme, the plan type and what help you need.",
        "who": "Staff at this clinic. Your insurer only when you ask us to contact them.",
        "retention_days": 365,
        "sensitive": False,
    },
    "research": {
        "title": "Taking part in research",
        "applies_to": ("patient",),
        "fields": {"study_name": 160, "study_contact": 160, "contact_about_new_studies": "bool"},
        "what": "Information from your record may be used for a research study the clinic names, after names and "
                "other identifying details are removed. Each study is explained to you before you agree.",
        "data": "The study name and whether we may contact you about future studies.",
        "who": "The clinic and the named study team. Research ethics rules apply.",
        "retention_days": 1825,
        "expires_days": 1825,
        "sensitive": True,
    },
    "data_sharing": {
        "title": "Sharing with a named partner",
        "applies_to": ("patient",),
        "fields": {},
        "per_partner": True,
        "what": "We share the information described for that partner, only with that partner, only for the "
                "reason given.",
        "data": "Only what the partner's description lists.",
        "who": "Only the partner named on this consent.",
        "retention_days": 0,
        "sensitive": True,
    },
}

# ======================================================================================================
# Jurisdiction profiles (informational defaults - not legal advice)
# ======================================================================================================
JURISDICTIONS = {
    "us_hipaa": {
        "name": "United States (HIPAA, plus state law)",
        "laws": ["HIPAA Privacy Rule (45 CFR Part 164, Subpart E)", "HIPAA Security Rule (Subpart C)",
                 "HIPAA Breach Notification Rule (Subpart D)", "State privacy, minor-consent and health-data laws",
                 "42 CFR Part 2 for substance-use treatment records where it applies"],
        "consent_age": 18,
        "age_note": "State law decides when a minor can consent; in many states it is 18, and minors can consent to "
                    "some services themselves. A parent is usually the minor's personal representative.",
        "sensitive_term": "protected health information (PHI)",
        "rights": "You can see and get a copy of your health records, ask us to correct them, ask for a list of "
                  "certain disclosures, ask us to limit some uses, ask to be contacted a certain way, and complain "
                  "to us or to the U.S. Department of Health and Human Services Office for Civil Rights.",
        "regulator": "U.S. Department of Health and Human Services, Office for Civil Rights",
        "lawful_basis_note": "HIPAA allows treatment, payment and health-care operations without this consent. "
                             "These optional purposes are kept opt-in anyway; marketing, sale of data and most "
                             "research need a written HIPAA authorization.",
        "breach_note": "Notify affected people without unreasonable delay and no later than 60 days after "
                       "discovery; also HHS (and the media for 500+ in a state).",
        "proof_retention_days": 2190,
        "erasure_note": "HIPAA has no general right to erasure; records are kept as state retention law requires.",
    },
    "eu_gdpr": {
        "name": "European Union (GDPR)",
        "laws": ["GDPR (Regulation (EU) 2016/679), Articles 5-9, 12-22, 25, 28, 30, 32-35",
                 "National health-data and record-retention laws of the Member State"],
        "consent_age": 16,
        "age_note": "Article 8: 16 by default; each Member State may set 13 to 16. Set the age of your state.",
        "sensitive_term": "special category data (health data, Article 9)",
        "rights": "You can access, correct, delete or move your data, restrict or object to its use, withdraw "
                  "consent at any time, and complain to your data protection authority.",
        "regulator": "Your national data protection supervisory authority",
        "lawful_basis_note": "These purposes rely on explicit consent (Articles 6(1)(a) and 9(2)(a)). Care itself "
                             "rests on other bases (Article 9(2)(h)).",
        "breach_note": "Tell the supervisory authority within 72 hours of becoming aware (Article 33), and the "
                       "people affected without undue delay when the risk is high (Article 34).",
        "proof_retention_days": 2190,
        "erasure_note": "Article 17(3) lets health records be kept where law requires; optional data is erased.",
    },
    "uk_gdpr": {
        "name": "United Kingdom (UK GDPR, Data Protection Act 2018)",
        "laws": ["UK GDPR", "Data Protection Act 2018", "Common law duty of confidentiality",
                 "NHS / professional record-retention codes where they apply"],
        "consent_age": 13,
        "age_note": "13 for online services (DPA 2018 s.9). For care, Gillick competence applies.",
        "sensitive_term": "special category data (health data)",
        "rights": "You can access, correct, delete or move your data, restrict or object to its use, withdraw "
                  "consent at any time, and complain to the Information Commissioner's Office (ICO).",
        "regulator": "Information Commissioner's Office (ICO)",
        "lawful_basis_note": "These purposes rely on explicit consent; care itself rests on other bases.",
        "breach_note": "Report notifiable breaches to the ICO within 72 hours; tell people without undue delay "
                       "when the risk is high.",
        "proof_retention_days": 2190,
        "erasure_note": "Health records may be kept where law or professional codes require.",
    },
    "ca_pipeda": {
        "name": "Canada (PIPEDA; provincial health laws such as Ontario PHIPA)",
        "laws": ["PIPEDA (Schedule 1 fair information principles)", "Ontario PHIPA 2004 and other provincial "
                 "health-information laws", "Breach of Security Safeguards Regulations"],
        "consent_age": 13,
        "age_note": "PIPEDA sets no age; the Privacy Commissioner expects a parent for children under 13. PHIPA is "
                    "capacity-based (no minimum age). Set what your province and legal advice require.",
        "sensitive_term": "personal health information",
        "rights": "You can access and correct your information, withdraw consent, and complain to the clinic or "
                  "to the Privacy Commissioner (federal or your province's).",
        "regulator": "Office of the Privacy Commissioner of Canada / provincial commissioner (e.g. IPC Ontario)",
        "lawful_basis_note": "Meaningful, express consent for these optional, sensitive uses.",
        "breach_note": "Report breaches with a real risk of significant harm to the Commissioner and notify people "
                       "as soon as feasible; keep a record of every breach for 24 months.",
        "proof_retention_days": 2190,
        "erasure_note": "Information is kept only as long as needed for its purpose or as law requires.",
    },
    "au_privacy": {
        "name": "Australia (Privacy Act 1988, Australian Privacy Principles)",
        "laws": ["Privacy Act 1988 (Cth) and the APPs", "Notifiable Data Breaches scheme",
                 "State and territory health-records laws"],
        "consent_age": 15,
        "age_note": "No fixed age; the OAIC generally presumes capacity from 15. Assess capacity case by case.",
        "sensitive_term": "sensitive information (health information)",
        "rights": "You can access and correct your information (APP 12 and 13), withdraw consent, and complain to "
                  "the clinic or to the Office of the Australian Information Commissioner.",
        "regulator": "Office of the Australian Information Commissioner (OAIC)",
        "lawful_basis_note": "Health information needs consent unless an exception applies (APP 3, APP 6).",
        "breach_note": "Assess a suspected eligible breach within 30 days; notify the OAIC and the people affected "
                       "as soon as practicable.",
        "proof_retention_days": 2190,
        "erasure_note": "Destroy or de-identify information no longer needed (APP 11.2) unless law requires it.",
    },
    "in_dpdp": {
        "name": "India (Digital Personal Data Protection Act 2023)",
        "laws": ["Digital Personal Data Protection Act 2023", "DPDP Rules (check the current notified version)"],
        "consent_age": 18,
        "age_note": "Under 18 is a child: verifiable consent of a parent or lawful guardian is required (s.9).",
        "sensitive_term": "personal data (health data)",
        "rights": "You can get a summary of your data, correct or erase it, withdraw consent as easily as you gave "
                  "it, nominate someone to act for you, and raise a grievance with us and then the Data "
                  "Protection Board of India.",
        "regulator": "Data Protection Board of India",
        "lawful_basis_note": "Free, specific, informed, unconditional and unambiguous consent with a clear notice.",
        "breach_note": "Inform the Data Protection Board and each affected person; check the current DPDP Rules "
                       "for timelines and report content.",
        "proof_retention_days": 2190,
        "erasure_note": "Erase when consent is withdrawn or the purpose ends, unless law requires keeping it.",
    },
    "br_lgpd": {
        "name": "Brazil (LGPD, Lei 13.709/2018)",
        "laws": ["LGPD (Lei 13.709/2018), Articles 7, 11, 14, 18, 37-41, 46-49", "ANPD regulations",
                 "Professional and health-record retention rules (e.g. CFM)"],
        "consent_age": 18,
        "age_note": "Article 14 requires a parent's specific consent for children (under 12) and the best interest "
                    "of adolescents. This profile conservatively asks a guardian for everyone under 18.",
        "sensitive_term": "dados pessoais sensíveis (health data)",
        "rights": "You can confirm, access, correct, anonymise, port or delete your data, learn who it is shared "
                  "with, withdraw consent, and complain to the ANPD.",
        "regulator": "Autoridade Nacional de Proteção de Dados (ANPD)",
        "lawful_basis_note": "Specific and highlighted consent for sensitive data (Article 11, I).",
        "breach_note": "Tell the ANPD and the people affected within a reasonable time; check current ANPD "
                       "rules (e.g. Resolution CD/ANPD 15/2024) for the deadline.",
        "proof_retention_days": 2190,
        "erasure_note": "Article 16 allows keeping data to meet a legal or regulatory duty.",
    },
    "generic": {
        "name": "Generic (no specific law selected)",
        "laws": ["Local privacy, health-information and record-retention laws (to be identified by the clinic)"],
        "consent_age": 18,
        "age_note": "Conservative default: a guardian for everyone under 18. Change it to what your law requires.",
        "sensitive_term": "health information",
        "rights": "You can ask to see your information, correct it, delete optional information, withdraw consent "
                  "and complain to the clinic or your local privacy authority.",
        "regulator": "Your local privacy authority",
        "lawful_basis_note": "Opt-in consent for every optional purpose.",
        "breach_note": "Follow your local breach-notification law; act quickly and keep a written record.",
        "proof_retention_days": 2190,
        "erasure_note": "Clinical records are kept as local law requires.",
    },
}

DEFAULT_SETTINGS = {
    "jurisdiction": "generic",
    "consent_age": None,            # None = the jurisdiction profile's age
    "country": "",                  # ISO 3166-1 alpha-2 for local emergency numbers (emergency_comms)
    "clinic_name": "the clinic",
    "clinic_phone": "",
    "clinic_email": "",
    "clinic_address": "",
    "privacy_officer": "",
    "policy_url": "",
    "partners": [],                 # [{id, name, contact, purpose, data, country}]
    "retention_days": {},           # {purpose: days} overrides
    "research_expiry_days": None,
    "proof_retention_days": None,
    "learner_requires_patient_consent": True,
    "crisis_resources": [],         # clinic-verified [{name, number, text, hours}], used by persona_ai
    "crisis_alert_policy": "notify_care_team",   # or "record_only"
    "knowledge_sources": [],        # clinic-configured facts persona_ai may cite: [{id, title, url, text, keywords}]
    "outbox_retention_days": 90,
}


# ======================================================================================================
# Small helpers
# ======================================================================================================
def _b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64u_dec(s):
    s = str(s)
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _iso(ts):
    return _dt.datetime.fromtimestamp(float(ts), _dt.timezone.utc).isoformat(timespec="seconds")


def _date(v):
    if isinstance(v, _dt.date):
        return v
    try:
        return _dt.date.fromisoformat(str(v)[:10])
    except Exception:
        return None


def age_on(birth_date, on_date):
    """Whole years of age on on_date, or None when the birth date is unknown."""
    b, d = _date(birth_date), _date(on_date)
    if b is None or d is None:
        return None
    return d.year - b.year - ((d.month, d.day) < (b.month, b.day))


def _text(v, n, field):
    if v is None:
        return ""
    if not isinstance(v, (str, int, float)):
        raise ConsentError("%s must be text." % field)
    s = "".join(ch for ch in str(v) if ch in "\n\t" or ord(ch) >= 32).strip()
    if len(s) > n:
        raise ConsentError("%s is too long (at most %d characters)." % (field, n))
    return s


def _ckey(purpose, partner=None):
    return "%s:%s" % (purpose, partner) if partner else purpose


def _split_key(key):
    p, _, partner = str(key).partition(":")
    return p, (partner or None)


def _host_module(clinic):
    """The module that defines the Clinic class (clinic_app) - for HttpError, ROUTES and helpers."""
    return sys.modules.get(type(clinic).__module__)


# ======================================================================================================
# Receipts (Kantara Consent Receipt v1.1 shape, JWS compact HS256)
# ======================================================================================================
def _jws(payload, key):
    head = _b64u(_canon({"alg": "HS256", "typ": "JWT", "kid": RECEIPT_KID}))
    body = _b64u(_canon(payload))
    sig = hmac.new(key, ("%s.%s" % (head, body)).encode("ascii"), hashlib.sha256).digest()
    return "%s.%s.%s" % (head, body, _b64u(sig))


def verify_receipt(jws, key):
    """-> the decoded receipt when the signature is valid, else None."""
    try:
        head, body, sig = str(jws).split(".")
        want = hmac.new(key, ("%s.%s" % (head, body)).encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(want, _b64u_dec(sig)):
            return None
        if json.loads(_b64u_dec(head)).get("alg") != "HS256":
            return None
        return json.loads(_b64u_dec(body))
    except Exception:
        return None


# ======================================================================================================
# Storage: clinic_app's Store (vault-encrypted, journaled, audit-trailed)
# ======================================================================================================
class _ClinicStorage:
    ROOT = "consent"

    def __init__(self, clinic):
        self.clinic = clinic

    @property
    def store(self):
        c = self.clinic
        if getattr(c, "state", "ready") != "ready" or not getattr(c, "store", None):
            raise ConsentError("Open the secure vault first.", 503)
        if getattr(c, "fatal", None):
            raise ConsentError(c.fatal, 503)
        return c.store

    @property
    def lock(self):
        return self.store.lock

    @property
    def encrypted(self):
        return not getattr(self.clinic, "demo", True)

    def audit_key(self):
        return self.store.audit.key

    def ensure_dirs(self, *rels):
        for rel in rels:
            os.makedirs(self.store.path(rel), exist_ok=True)

    def read(self, rel):
        obj = self.store.read_obj(rel)
        if isinstance(obj, dict) and obj.get("erased"):
            return None
        return obj

    def seal(self, obj):
        return self.clinic.codec.dump(obj)

    def commit(self, files, actor, action, target="", detail=None):
        """files: {rel: obj | None}. None = replace with an 'erased' placeholder (the bytes are overwritten, and
        the audit trail's file hash stays consistent, so integrity checks keep passing)."""
        out = {}
        for rel, obj in files.items():
            if obj is None:
                obj = {"erased": True, "at": _iso(time.time())}
            out[rel] = self.seal(obj)
        return self.clinic._commit(out, actor, action, target, detail or {})

    def audit_only(self, actor, action, target="", detail=None):
        return self.clinic._commit({}, actor, action, target, detail or {})

    def listdir(self, rel):
        try:
            return sorted(os.listdir(self.store.path(rel)))
        except (FileNotFoundError, NotADirectoryError):
            return []

    def audit_entries(self):
        out, _ = self.store.audit.entries(limit=10 ** 9)
        return out


# ======================================================================================================
# The consent center
# ======================================================================================================
class ConsentCenter:
    """One per clinic. Every public method that changes something takes the acting user (clinic_app user dict)."""

    def __init__(self, clinic, clock=None):
        self.clinic = clinic
        self.storage = _ClinicStorage(clinic)
        self.clock = clock or time.time
        self._holders = {}          # name -> {"export": fn(sid), "erase": fn(sid, actor), "purge": fn(now)}
        self._on_withdraw = []      # fn(subject, purpose_key, actor)
        self._stop = threading.Event()
        self._thread = None
        self.last_purge = None

    # --------------------------------------------------------------- paths
    @staticmethod
    def _check_subject(sid):
        if not isinstance(sid, str) or not SUBJECT_RE.match(sid):
            raise ConsentError("Bad person id.")
        return sid

    @staticmethod
    def _rec_rel(sid):
        return "consent/r/%s.bin" % sid

    @staticmethod
    def _data_rel(sid, purpose):
        return "consent/d/%s/%s.bin" % (sid, purpose)

    def _tomb_rel(self, sid):
        return "consent/t/%s.bin" % self.subject_ref(sid)[:40]

    def subject_ref(self, sid):
        """Keyed pseudonym of a person id (used in tombstones: no personal data, still checkable)."""
        return hmac.new(self._key(b"subject-ref"), sid.encode("ascii"), hashlib.sha256).hexdigest()

    def _key(self, label):
        return hmac.new(self.storage.audit_key(), b"gorgon-covenant-consent-" + label + b"-v1",
                        hashlib.sha256).digest()

    @property
    def receipt_key(self):
        return self._key(b"receipt")

    # --------------------------------------------------------------- settings / jurisdiction
    def settings(self):
        s = copy.deepcopy(DEFAULT_SETTINGS)
        stored = self.storage.read("consent/settings.bin") or {}
        s.update({k: v for k, v in stored.items() if k in DEFAULT_SETTINGS})
        return s

    def update_settings(self, actor, changes):
        self._require_perm(actor, "users.manage")
        if not isinstance(changes, dict):
            raise ConsentError("settings must be an object")
        with self.storage.lock:
            s = self.settings()
            for k, v in changes.items():
                if k not in DEFAULT_SETTINGS:
                    raise ConsentError("Unknown setting: %s" % _text(k, 40, "setting"))
                s[k] = self._clean_setting(k, v)
            self.storage.ensure_dirs("consent")
            self.storage.commit({"consent/settings.bin": s}, actor["id"], "consent.settings", "",
                                {"changed": sorted(changes)})
        return s

    def _clean_setting(self, k, v):
        if k == "jurisdiction":
            if v not in JURISDICTIONS:
                raise ConsentError("Unknown jurisdiction profile.")
            return v
        if k in ("consent_age", "research_expiry_days", "proof_retention_days"):
            if v in (None, ""):
                return None
            v = int(v)
            if not (0 <= v <= (25 if k == "consent_age" else 36500)):
                raise ConsentError("%s is out of range." % k)
            return v
        if k == "outbox_retention_days":
            v = int(v)
            if not 1 <= v <= 3650:
                raise ConsentError("outbox_retention_days is out of range.")
            return v
        if k == "country":
            v = _text(v, 2, k).upper()
            if v and not re.match(r"^[A-Z]{2}$", v):
                raise ConsentError("country must be a two-letter ISO code.")
            return v
        if k == "learner_requires_patient_consent":
            return bool(v)
        if k == "crisis_alert_policy":
            if v not in ("notify_care_team", "record_only"):
                raise ConsentError("crisis_alert_policy must be notify_care_team or record_only.")
            return v
        if k == "retention_days":
            if not isinstance(v, dict):
                raise ConsentError("retention_days must be an object.")
            out = {}
            for p, d in v.items():
                if p not in PURPOSES:
                    raise ConsentError("Unknown purpose in retention_days.")
                d = int(d)
                if not 1 <= d <= 36500:
                    raise ConsentError("retention days out of range.")
                out[p] = d
            return out
        if k == "partners":
            if not isinstance(v, list) or len(v) > 200:
                raise ConsentError("partners must be a list.")
            out, seen = [], set()
            for p in v:
                if not isinstance(p, dict):
                    raise ConsentError("each partner must be an object.")
                pid = _text(p.get("id"), 40, "partner id").lower()
                if not PARTNER_RE.match(pid) or pid in seen:
                    raise ConsentError("partner id must be unique, lower-case letters, digits, - or _.")
                seen.add(pid)
                name = _text(p.get("name"), 160, "partner name")
                purpose = _text(p.get("purpose"), 400, "partner purpose")
                if not name or not purpose:
                    raise ConsentError("A partner needs a name and a stated purpose.")
                out.append({"id": pid, "name": name, "purpose": purpose,
                            "data": _text(p.get("data"), 400, "partner data description"),
                            "contact": _text(p.get("contact"), 200, "partner contact"),
                            "country": _text(p.get("country"), 2, "partner country").upper()})
            return out
        if k == "crisis_resources":
            if not isinstance(v, list) or len(v) > 20:
                raise ConsentError("crisis_resources must be a list.")
            return [{"name": _text(r.get("name"), 120, "name"), "number": _text(r.get("number"), 40, "number"),
                     "text": _text(r.get("text"), 200, "text"), "hours": _text(r.get("hours"), 80, "hours")}
                    for r in v if isinstance(r, dict) and r.get("name")]
        if k == "knowledge_sources":
            if not isinstance(v, list) or len(v) > 500:
                raise ConsentError("knowledge_sources must be a list.")
            out = []
            for r in v:
                if not isinstance(r, dict):
                    continue
                title, text = _text(r.get("title"), 200, "source title"), _text(r.get("text"), 1500, "source text")
                if not title or not text:
                    raise ConsentError("Every knowledge source needs a title (the citation) and its text.")
                kw = r.get("keywords") or []
                out.append({"id": _text(r.get("id") or secrets.token_hex(4), 40, "id"), "title": title,
                            "url": _text(r.get("url"), 300, "url"), "text": text,
                            "keywords": [_text(x, 40, "keyword").lower() for x in kw][:30],
                            "reviewed_by": _text(r.get("reviewed_by"), 80, "reviewed_by")})
            return out
        return _text(v, 300, k)

    def jurisdiction(self, settings=None, sid=None):
        s = settings or self.settings()
        key = s["jurisdiction"]
        if sid:
            rec = self._record(sid)
            if rec and rec.get("jurisdiction") in JURISDICTIONS:
                key = rec["jurisdiction"]
        prof = dict(JURISDICTIONS[key], key=key)
        if s.get("consent_age") is not None:
            prof["consent_age"] = int(s["consent_age"])
        if s.get("proof_retention_days"):
            prof["proof_retention_days"] = int(s["proof_retention_days"])
        prof["disclaimer"] = DISCLAIMER
        return prof

    def retention_days(self, purpose, settings=None):
        s = settings or self.settings()
        return int(s.get("retention_days", {}).get(purpose) or PURPOSES[purpose]["retention_days"])

    # --------------------------------------------------------------- explanations (plain language)
    def explain(self, sid=None, kind=None):
        s = self.settings()
        prof = self.jurisdiction(s, sid if sid and self._exists_record(sid) else None)
        kind = kind or (self._kind(sid) if sid else "patient")
        out = []
        for key, p in PURPOSES.items():
            if kind not in p["applies_to"]:
                continue
            days = self.retention_days(key, s)
            how_long = ("Until you withdraw. If unused for %d days it is deleted automatically." % days
                        if days else "Nothing is stored for this; it lasts until you withdraw.")
            if p.get("expires_days"):
                ex = int(s.get("research_expiry_days") or p["expires_days"])
                how_long += " This consent ends by itself after %d days unless you renew it." % ex
            base = {"purpose": key, "title": p["title"], "what": p["what"], "data": p["data"], "who": p["who"],
                    "how_long": how_long, "saying_no": NO_EFFECT_ON_CARE, "withdraw": WITHDRAW_LINE,
                    "default": "off", "sensitive": bool(p.get("sensitive")),
                    "sensitive_note": ("This includes %s." % prof["sensitive_term"]) if p.get("sensitive") else ""}
            if p.get("per_partner"):
                for partner in s["partners"]:
                    e = dict(base, key=_ckey(key, partner["id"]), partner=partner,
                             title="Sharing with %s" % partner["name"],
                             what="We share information with %s for this reason: %s" % (partner["name"],
                                                                                         partner["purpose"]),
                             data=partner["data"] or base["data"], who="Only %s." % partner["name"])
                    out.append(e)
            else:
                out.append(dict(base, key=key))
        return {"purposes": out, "jurisdiction": {"key": prof["key"], "name": prof["name"], "rights":
                prof["rights"], "consent_age": prof["consent_age"], "age_note": prof["age_note"],
                "lawful_basis_note": prof["lawful_basis_note"], "regulator": prof["regulator"]},
                "controller": {"name": s["clinic_name"], "contact": s["clinic_email"] or s["clinic_phone"],
                               "privacy_officer": s["privacy_officer"], "policy_url": s["policy_url"]},
                "disclaimer": DISCLAIMER, "version": self._explanation_version(out)}

    @staticmethod
    def _explanation_version(items):
        return hashlib.sha256(_canon(items)).hexdigest()[:16]

    # --------------------------------------------------------------- subjects
    def _kind(self, sid):
        return "staff" if sid[0] == "U" else "patient"

    def _exists_record(self, sid):
        try:
            return self._record(sid) is not None
        except Exception:
            return False

    def _record(self, sid):
        return self.storage.read(self._rec_rel(self._check_subject(sid)))

    def _empty_record(self, sid):
        return {"schema": SCHEMA, "subject": sid, "kind": self._kind(sid), "consents": {}, "history": [],
                "receipts": [], "jurisdiction": None, "created": _iso(self.clock()), "updated": _iso(self.clock())}

    def _birth_date(self, sid):
        if sid[0] != "P":
            return None
        try:
            chart = self.clinic._load_chart(sid)
        except Exception:
            raise ConsentError("No such patient.", 404)
        return chart.get("demographics", {}).get("dob") or None

    def _require_perm(self, actor, perm):
        if not isinstance(actor, dict) or "id" not in actor:
            raise ConsentError("Sign in first.", 401)
        if perm not in self.clinic.perms(actor):
            raise ConsentError("Your role does not allow this (%s)." % perm, 403)

    def _may_manage(self, actor, sid, withdraw=False):
        """Patients: staff who may edit demographics record their decisions. Staff: only the person themselves
        (an administrator may also WITHDRAW for a staff member on their request)."""
        if sid[0] == "P":
            self._require_perm(actor, "demo.write")
            return
        if actor.get("id") == sid:
            return
        if withdraw and "users.manage" in self.clinic.perms(actor):
            return
        raise ConsentError("Only that staff member can give their own consent.", 403)

    # --------------------------------------------------------------- the live state (no caching anywhere)
    def status(self, sid, now=None):
        """-> {purpose_key: {"active": bool, "state": ..., ...}} for every purpose that applies to this person."""
        sid = self._check_subject(sid)
        now = self.clock() if now is None else now
        rec = self._record(sid) or self._empty_record(sid)
        s = self.settings()
        out = {}
        for key, p in PURPOSES.items():
            if rec["kind"] not in p["applies_to"]:
                continue
            keys = [_ckey(key, x["id"]) for x in s["partners"]] if p.get("per_partner") else [key]
            for k in keys:
                c = rec["consents"].get(k)
                out[k] = self._evaluate(sid, k, c, now) if c else {"active": False, "state": "not_given"}
        return out

    def _evaluate(self, sid, key, c, now):
        st = c.get("state")
        res = {"active": False, "state": st, "granted_at": c.get("granted_at"), "receipt": c.get("receipt"),
               "by_guardian": bool(c.get("guardian"))}
        if st != "granted":
            return res
        if c.get("expires") and now >= float(c["expires"]):
            res["state"] = "expired"
            return res
        if c.get("guardian"):
            age = age_on(self._birth_date(sid), _dt.datetime.fromtimestamp(now, _dt.timezone.utc).date())
            if age is not None and age >= int(c.get("age_threshold", 18)):
                res["state"] = "needs_reconfirmation"
                return res
        purpose, partner = _split_key(key)
        if partner and partner not in {p["id"] for p in self.settings()["partners"]}:
            res["state"] = "partner_removed"
            return res
        res["active"] = True
        return res

    def is_active(self, sid, purpose, partner=None):
        try:
            return bool(self.status(sid).get(_ckey(purpose, partner), {}).get("active"))
        except ConsentError:
            return False

    def require(self, sid, purpose, partner=None, reader="unknown"):
        """Purpose-limitation gate. Raises ConsentRequired unless the purpose is active for this person NOW."""
        if purpose not in PURPOSES:
            raise ConsentRequired("Unknown purpose: no processing allowed.")
        if not self.is_active(sid, purpose, partner):
            raise ConsentRequired("%s has no active consent for %s; %s may not use their data for it."
                                  % (sid, _ckey(purpose, partner), reader))
        return True

    # --------------------------------------------------------------- grant / withdraw
    def grant(self, actor, sid, purposes, pin=None, method="in_person", language="en", guardian=None,
              details=None, explanation_version=None, jurisdiction=None):
        """Record an explicit YES for each listed purpose key. The acting staff member confirms with their
        signing PIN (an e-signature that the person agreed). Minors need a verified parent / guardian."""
        sid = self._check_subject(sid)
        self._may_manage(actor, sid)
        if not isinstance(purposes, (list, tuple)) or not purposes:
            raise ConsentError("Choose at least one purpose.")
        if method not in ("in_person", "paper_form", "phone", "video", "portal", "self_service"):
            raise ConsentError("Unknown collection method.")
        language = _text(language, 20, "language") or "en"
        if jurisdiction is not None and jurisdiction not in JURISDICTIONS:
            raise ConsentError("Unknown jurisdiction profile.")
        s = self.settings()
        kind = self._kind(sid)
        keys = []
        for k in purposes:
            purpose, partner = _split_key(k)
            p = PURPOSES.get(purpose)
            if p is None or kind not in p["applies_to"]:
                raise ConsentError("That purpose does not apply here: %s" % _text(k, 60, "purpose"))
            if p.get("per_partner"):
                if not partner or partner not in {x["id"] for x in s["partners"]}:
                    raise ConsentError("Data sharing needs a partner the clinic has named in its settings.")
            elif partner:
                raise ConsentError("Only data sharing is per partner.")
            keys.append(_ckey(purpose, partner))
        self.clinic.verify_pin(actor, pin)
        now = self.clock()
        with self.storage.lock:
            rec = self._record(sid) or self._empty_record(sid)
            if jurisdiction:
                rec["jurisdiction"] = jurisdiction
            prof = self.jurisdiction(s)
            if rec.get("jurisdiction"):
                prof = dict(prof, **{k: v for k, v in JURISDICTIONS[rec["jurisdiction"]].items()})
                if s.get("consent_age") is not None:
                    prof["consent_age"] = int(s["consent_age"])
            g = self._guardian_check(sid, kind, prof, guardian, now)
            receipt_id = "CR-" + secrets.token_hex(8)
            for key in keys:
                purpose, partner = _split_key(key)
                c = {"state": "granted", "granted_at": now, "by": actor["id"], "method": method,
                     "language": language, "receipt": receipt_id,
                     "explanation_version": _text(explanation_version, 32, "explanation version")}
                if g:
                    c["guardian"] = g
                    c["age_threshold"] = prof["consent_age"]
                if PURPOSES[purpose].get("expires_days"):
                    c["expires"] = now + DAY * int(s.get("research_expiry_days") or PURPOSES[purpose]["expires_days"])
                rec["consents"][key] = c
                rec["history"].append({"at": now, "event": "granted", "purpose": key, "by": actor["id"],
                                       "receipt": receipt_id, "guardian": bool(g)})
            receipt = self._receipt(sid, rec, keys, receipt_id, now, method, language, s, prof, g)
            rec["receipts"].append({"id": receipt_id, "at": now, "jws": receipt, "purposes": keys,
                                    "event": "granted"})
            rec["updated"] = _iso(now)
            files = {self._rec_rel(sid): rec}
            if details:
                files.update(self._data_files(sid, keys, details, now, rec))
            self.storage.ensure_dirs("consent/r", "consent/d/%s" % sid)
            self.storage.commit(files, actor["id"], "consent.grant", sid,
                                {"purposes": keys, "receipt": receipt_id, "guardian": bool(g), "method": method})
        return {"receipt_id": receipt_id, "receipt": receipt, "status": self.status(sid)}

    def _guardian_check(self, sid, kind, prof, guardian, now):
        if kind != "patient":
            return None
        today = _dt.datetime.fromtimestamp(now, _dt.timezone.utc).date()
        age = age_on(self._birth_date(sid), today)
        if age is None:
            raise GuardianRequired("The date of birth is needed to check whether a guardian must consent.")
        if age >= int(prof["consent_age"]):
            return None
        if not isinstance(guardian, dict):
            raise GuardianRequired("This patient is under %d, the consent age set for %s. A parent or legal "
                                   "guardian must give this consent." % (prof["consent_age"], prof["name"]))
        name = _text(guardian.get("name"), 120, "guardian name")
        rel = _text(guardian.get("relationship"), 60, "guardian relationship")
        if not name or not rel or guardian.get("authority_checked") is not True:
            raise GuardianRequired("Record the guardian's name and relationship, and confirm that you checked "
                                   "their authority to consent for this child.")
        return {"name": name, "relationship": rel, "authority_checked": True,
                "child_assent": bool(guardian.get("child_assent")), "patient_age_at_consent": age}

    def withdraw(self, actor, sid, purposes="all", reason=""):
        """Immediate effect: the consent is off before this returns, and its data is deleted in the same
        transaction. No PIN needed - withdrawing must be as easy as giving consent."""
        sid = self._check_subject(sid)
        self._may_manage(actor, sid, withdraw=True)
        now = self.clock()
        with self.storage.lock:
            rec = self._record(sid)
            if rec is None:
                return {"withdrawn": [], "status": self.status(sid)}
            if isinstance(purposes, str) and purposes != "all":
                purposes = [purposes]
            keys = list(rec["consents"]) if purposes == "all" else [str(k) for k in (purposes or [])]
            done = []
            files = {}
            receipt_id = "CR-" + secrets.token_hex(8)
            for key in keys:
                c = rec["consents"].get(key)
                if not c or c.get("state") != "granted":
                    continue
                c.update(state="withdrawn", withdrawn_at=now, withdrawn_by=actor["id"])
                rec["history"].append({"at": now, "event": "withdrawn", "purpose": key, "by": actor["id"],
                                       "receipt": receipt_id, "reason": _text(reason, 200, "reason")})
                done.append(key)
                purpose, partner = _split_key(key)
                if not partner and PURPOSES.get(purpose, {}).get("fields") is not None:
                    if self.storage.read(self._data_rel(sid, purpose)) is not None:
                        files[self._data_rel(sid, purpose)] = None
            if not done:
                return {"withdrawn": [], "status": self.status(sid)}
            s = self.settings()
            receipt = self._receipt(sid, rec, done, receipt_id, now, "withdrawal", "en", s, self.jurisdiction(s),
                                    None, withdrawn=True)
            rec["receipts"].append({"id": receipt_id, "at": now, "jws": receipt, "purposes": done,
                                    "event": "withdrawn"})
            rec["updated"] = _iso(now)
            files[self._rec_rel(sid)] = rec
            self.storage.commit(files, actor["id"], "consent.withdraw", sid, {"purposes": done,
                                                                              "receipt": receipt_id})
        for key in done:
            for cb in list(self._on_withdraw):
                try:
                    cb(sid, key, actor)
                except Exception:
                    self._health("withdraw callback failed\n" + traceback.format_exc())
        return {"withdrawn": done, "receipt_id": receipt_id, "status": self.status(sid)}

    def on_withdraw(self, fn):
        self._on_withdraw.append(fn)

    # --------------------------------------------------------------- receipts
    def _receipt(self, sid, rec, keys, rid, now, method, language, s, prof, guardian, withdrawn=False):
        services = []
        for key in keys:
            purpose, partner = _split_key(key)
            p = PURPOSES[purpose]
            pname = next((x["name"] for x in s["partners"] if x["id"] == partner), None) if partner else None
            services.append({"service": p["title"] + (" - " + pname if pname else ""), "purposes": [{
                "purpose": p["what"], "purposeCategory": [purpose], "consentType": "EXPLICIT",
                "piiCategory": list(p["fields"]) or ["as described for the partner"],
                "primaryPurpose": False, "termination": WITHDRAW_LINE,
                "thirdPartyDisclosure": bool(partner), "thirdPartyName": pname or ""}]})
        payload = {"version": RECEIPT_VERSION, "jurisdiction": prof["name"], "consentTimestamp": int(now),
                   "collectionMethod": method, "consentReceiptID": rid, "publicKey": "", "language": language,
                   "piiPrincipalId": sid,
                   "piiControllers": [{"piiController": s["clinic_name"], "contact": s["privacy_officer"],
                                       "address": s["clinic_address"], "email": s["clinic_email"],
                                       "phone": s["clinic_phone"], "piiControllerUrl": s["policy_url"]}],
                   "policyUrl": s["policy_url"], "services": services,
                   "sensitive": any(PURPOSES[_split_key(k)[0]].get("sensitive") for k in keys),
                   "spiCat": [prof["sensitive_term"]],
                   "x-gorgon-covenant": {"event": "withdrawn" if withdrawn else "granted",
                                         "guardian": bool(guardian), "kid": RECEIPT_KID,
                                         "notice": DISCLAIMER}}
        return _jws(payload, self.receipt_key)

    def receipts(self, actor, sid):
        sid = self._check_subject(sid)
        self._may_read(actor, sid)
        rec = self._record(sid) or self._empty_record(sid)
        return [dict(r, decoded=verify_receipt(r["jws"], self.receipt_key)) for r in rec["receipts"]]

    def _may_read(self, actor, sid):
        if sid[0] == "P":
            self._require_perm(actor, "demo.read")
        elif actor.get("id") != sid:
            self._require_perm(actor, "users.manage")

    # --------------------------------------------------------------- purpose-limited data
    def _clean_data(self, purpose, data):
        if not isinstance(data, dict):
            raise ConsentError("data must be an object")
        spec = PURPOSES[purpose]["fields"]
        out = {}
        for k, v in data.items():
            if k not in spec:   # data minimisation: anything the purpose does not need is refused
                raise ConsentError("'%s' is not collected for %s (data minimisation)."
                                   % (_text(k, 40, "field"), purpose))
            rule = spec[k]
            if isinstance(rule, int):
                out[k] = _text(v, rule, k)
            elif rule == "int":
                out[k] = max(0, min(int(v), 10000))
            elif rule == "bool":
                out[k] = bool(v)
            elif rule == "topics[]":
                if not isinstance(v, list):
                    raise ConsentError("%s must be a list" % k)
                out[k] = [_text(x, 60, k) for x in v][:20]
            elif rule.endswith("[]"):
                choices = _CHOICES[rule[:-2]]
                if not isinstance(v, list) or any(x not in choices for x in v):
                    raise ConsentError("%s must be a list of: %s" % (k, ", ".join(choices)))
                out[k] = sorted(set(v))
            else:
                if v not in _CHOICES[rule]:
                    raise ConsentError("%s must be one of: %s" % (k, ", ".join(_CHOICES[rule])))
                out[k] = v
        return out

    def _data_files(self, sid, keys, details, now, rec):
        files = {}
        for purpose, data in (details or {}).items():
            if purpose not in keys or PURPOSES.get(purpose, {}).get("per_partner"):
                raise ConsentError("Details can only be stored for a purpose being consented to.")
            files[self._data_rel(sid, purpose)] = {"subject": sid, "purpose": purpose, "updated": now,
                                                   "data": self._clean_data(purpose, data)}
        return files

    def write(self, actor, sid, purpose, data, merge=True):
        """Store data for ONE purpose. Refused unless that purpose is active for this person."""
        sid = self._check_subject(sid)
        self._may_manage(actor, sid)
        if PURPOSES.get(purpose, {}).get("per_partner") or purpose not in PURPOSES:
            raise ConsentError("Nothing is stored for that purpose.")
        clean = self._clean_data(purpose, data)
        with self.storage.lock:
            self.require(sid, purpose, reader="write by %s" % actor["id"])
            old = self.storage.read(self._data_rel(sid, purpose)) or {}
            merged = dict(old.get("data") or {}, **clean) if merge else clean
            self.storage.ensure_dirs("consent/d/%s" % sid)
            self.storage.commit({self._data_rel(sid, purpose): {"subject": sid, "purpose": purpose,
                                                               "updated": self.clock(), "data": merged}},
                                actor["id"], "consent.data_write", sid, {"purpose": purpose,
                                                                        "fields": sorted(clean)})
        return merged

    def read(self, sid, purpose, reader="unknown"):
        """Purpose-limited read: the data held for this purpose, only while its consent is active. Never cached."""
        sid = self._check_subject(sid)
        self.require(sid, purpose, reader=reader)
        obj = self.storage.read(self._data_rel(sid, purpose)) or {}
        return copy.deepcopy(obj.get("data") or {})

    def scoped(self, sid, purpose, reader):
        """A handle for a learner: it can read this person's data for this purpose and nothing else, and every
        call re-checks the live consent state (so a withdrawal stops it at once)."""
        return ScopedAccess(self, self._check_subject(sid), purpose, reader)

    # --------------------------------------------------------------- data holders (persona outbox, learner, ...)
    def register_holder(self, name, export=None, erase=None, purge=None):
        self._holders[name] = {"export": export, "erase": erase, "purge": purge}

    # --------------------------------------------------------------- retention
    def purge_expired(self, now=None, actor_id="system"):
        """Automatic deletion on schedule. Deletes: data of purposes no longer active; data unused longer than
        its retention period; expired time-limited consents' data; withdrawn-only records past the proof-of-
        consent retention period (replaced by a tombstone). -> counts."""
        now = self.clock() if now is None else now
        rep = {"data_deleted": 0, "consents_expired": 0, "records_closed": 0, "holders": {}}
        s = self.settings()
        prof = self.jurisdiction(s)
        with self.storage.lock:
            for fn in self.storage.listdir("consent/r"):
                if not fn.endswith(".bin") or not SUBJECT_RE.match(fn[:-4]):
                    continue
                sid = fn[:-4]
                try:
                    self._purge_one(sid, now, s, prof, rep, actor_id)
                except Exception:
                    self._health("retention purge of %s failed\n%s" % (sid, traceback.format_exc()))
        for name, h in list(self._holders.items()):
            if h.get("purge"):
                try:
                    rep["holders"][name] = h["purge"](now)
                except Exception:
                    self._health("retention purge of holder %s failed\n%s" % (name, traceback.format_exc()))
        self.last_purge = {"at": _iso(now), "report": rep}
        return rep

    def _purge_one(self, sid, now, s, prof, rep, actor_id):
        rec = self._record(sid)
        if rec is None:
            return
        files, changed, deleted = {}, False, []
        st = {k: self._evaluate(sid, k, c, now) for k, c in rec["consents"].items()}
        for key, ev in st.items():
            if ev["state"] == "expired" and rec["consents"][key].get("state") == "granted":
                rec["consents"][key]["state"] = "expired"
                rec["history"].append({"at": now, "event": "expired", "purpose": key, "by": actor_id})
                rep["consents_expired"] += 1
                changed = True
        for purpose, p in PURPOSES.items():
            if p.get("per_partner"):
                continue
            rel = self._data_rel(sid, purpose)
            obj = self.storage.read(rel)
            if obj is None:
                continue
            active = st.get(purpose, {}).get("active", False)
            stale = now - float(obj.get("updated", 0)) > DAY * self.retention_days(purpose, s)
            if not active or stale:
                files[rel] = None
                deleted.append(purpose)
                rep["data_deleted"] += 1
        if changed:
            rec["updated"] = _iso(now)
            files[self._rec_rel(sid)] = rec
        last = max([float(h.get("at", 0)) for h in rec["history"]] or [0])
        if rec["consents"] and not any(c.get("state") == "granted" for c in rec["consents"].values()) and \
                now - last > DAY * int(prof["proof_retention_days"]):
            files[self._rec_rel(sid)] = None
            files[self._tomb_rel(sid)] = self._tombstone(sid, now, actor_id, "retention_period_ended",
                                                         ["consent record"], {})
            rep["records_closed"] += 1
            self.storage.ensure_dirs("consent/t")
        if files:
            self.storage.commit(files, actor_id, "consent.retention_purge", sid,
                                {"data_deleted": deleted, "expired": changed,
                                 "record_closed": self._tomb_rel(sid) in files})

    def start_scheduler(self, interval=PURGE_INTERVAL):
        if self._thread is not None:
            return

        def loop():
            while not self._stop.wait(interval if self.last_purge else min(5.0, interval)):
                try:
                    if getattr(self.clinic, "state", "") == "ready" and not getattr(self.clinic, "fatal", None):
                        self.purge_expired()
                except Exception:
                    self._health("scheduled retention purge failed\n" + traceback.format_exc())
        self._thread = threading.Thread(target=loop, name="consent-retention", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    # --------------------------------------------------------------- subject access export
    def export_subject(self, actor, sid):
        """Everything Stark Covenant holds about one person, machine-readable. Patients: needs clinical read
        permission (the export contains the clinical record). Staff: the person themselves or an administrator."""
        sid = self._check_subject(sid)
        if sid[0] == "P":
            self._require_perm(actor, "clin.read")
        elif actor.get("id") != sid:
            self._require_perm(actor, "users.manage")
        s = self.settings()
        rec = self._record(sid) or self._empty_record(sid)
        purpose_data = {}
        for purpose, p in PURPOSES.items():
            if p.get("per_partner"):
                continue
            obj = self.storage.read(self._data_rel(sid, purpose))
            if obj is not None:     # export is a right of access: it includes data even if consent lapsed
                purpose_data[purpose] = {"updated": _iso(obj.get("updated", 0)), "data": obj.get("data") or {}}
        sections = {
            "consents": {"status": self.status(sid), "records": rec["consents"], "history": rec["history"]},
            "receipts": [dict(r, decoded=verify_receipt(r["jws"], self.receipt_key)) for r in rec["receipts"]],
            "purpose_data": purpose_data,
        }
        if sid[0] == "P":
            chart = self.clinic._load_chart(sid)
            sections["clinical_record"] = chart
        else:
            u = self.clinic._users()["users"].get(sid)
            if u is None:
                raise ConsentError("No such user.", 404)
            sections["account"] = {k: v for k, v in u.items() if k not in ("pw", "pin")}
        sections["access_log"] = [e for e in self.storage.audit_entries()
                                  if e.get("target") == sid or e.get("actor") == sid]
        held = {}
        for name, h in self._holders.items():
            if h.get("export"):
                try:
                    held[name] = h["export"](sid)
                except Exception as e:
                    held[name] = {"error": "could not be exported: %s" % type(e).__name__}
        sections["other_data"] = held
        prof = self.jurisdiction(s, sid)
        doc = {"format": "gorgon-covenant/subject-access/1", "generated": _iso(self.clock()), "subject": sid,
               "kind": self._kind(sid), "controller": s["clinic_name"], "jurisdiction": prof["name"],
               "your_rights": prof["rights"], "notice": DISCLAIMER, "sections": sections,
               "manifest": {k: hashlib.sha256(_canon(v)).hexdigest() for k, v in sections.items()}}
        self.storage.audit_only(actor["id"], "consent.subject_export", sid, {"sections": sorted(sections)})
        return doc

    def export_pdf(self, doc):
        """Readable PDF of an export (polyglot_engine). -> (bytes | None, warnings)."""
        try:
            pe = importlib.import_module("polyglot_engine")
        except Exception:
            return None, ["polyglot_engine is not installed; only the JSON export is available."]
        try:
            pdf = pe.PolyglotPdf("Your information held by %s" % doc.get("controller", "the clinic"),
                                 author=str(doc.get("controller", "")), subject="Subject access export")
            pdf.heading("Your information")
            pdf.paragraph("Prepared %s for %s. %s" % (doc["generated"], doc["subject"], doc["your_rights"]))
            pdf.paragraph(doc["notice"])
            sec = doc["sections"]
            pdf.heading("Your choices (consents)")
            for k, v in sec["consents"]["status"].items():
                pdf.paragraph("%s: %s" % (PURPOSES[_split_key(k)[0]]["title"] + (
                    " (" + _split_key(k)[1] + ")" if _split_key(k)[1] else ""), v["state"].replace("_", " ")))
            pdf.heading("Consent history")
            for h in sec["consents"]["history"]:
                pdf.paragraph("%s  %s  %s" % (_iso(h["at"]), h["event"], h["purpose"]))
            pdf.heading("Information kept for your choices")
            if not sec["purpose_data"]:
                pdf.paragraph("None.")
            for purpose, v in sec["purpose_data"].items():
                pdf.paragraph("%s (updated %s): %s" % (PURPOSES[purpose]["title"], v["updated"], json.dumps(
                    v["data"], ensure_ascii=False)))
            if "clinical_record" in sec:
                ch = sec["clinical_record"]
                pdf.heading("Clinical record")
                d = ch.get("demographics", {})
                pdf.paragraph("Name: %s   Date of birth: %s" % (d.get("name", ""), d.get("dob", "")))
                for label, items, f in (("Allergies", ch.get("allergies", []), "substance"),
                                        ("Medicines", ch.get("medications", []), "name"),
                                        ("Problems", ch.get("problems", []), "description")):
                    pdf.paragraph("%s: %s" % (label, "; ".join(str(i.get(f, "")) for i in items) or "none"))
                for n in ch.get("notes", []):
                    pdf.paragraph("Note %s (%s, %s): %s" % (n.get("title", ""), n.get("status", ""),
                                                            n.get("created", ""), n.get("text", "")))
                for v in ch.get("vitals", []):
                    pdf.paragraph("Vitals %s: %s" % (v.get("taken", v.get("created", "")), json.dumps(
                        v.get("values", {}))))
            pdf.heading("Who looked at or changed your information")
            for e in sec["access_log"][:500]:
                pdf.paragraph("%s  %s  by %s" % (e.get("ts", ""), e.get("action", ""), e.get("actor", "")))
            if sec.get("other_data"):
                pdf.heading("Other information")
                pdf.paragraph(json.dumps(sec["other_data"], ensure_ascii=False)[:20000])
            return pdf.build(), list(getattr(pdf, "warnings", []))
        except Exception as e:
            self._health("export pdf failed\n" + traceback.format_exc())
            return None, ["The PDF could not be made (%s); the JSON export is complete." % type(e).__name__]

    # --------------------------------------------------------------- erasure ("forget me")
    def erase_subject(self, actor, sid, pin=None, reason="subject_request"):
        """Remove every optional-purpose record about this person, and the data other holders keep for them, at
        once. Keeps an audit tombstone (keyed pseudonym, dates, what was removed; no personal data). The clinical
        record is NOT deleted: the request is recorded for the privacy officer (legal retention duties)."""
        sid = self._check_subject(sid)
        self._may_manage(actor, sid)
        self.clinic.verify_pin(actor, pin)
        now = self.clock()
        removed = {}
        with self.storage.lock:
            rec = self._record(sid)
            files = {}
            for purpose, p in PURPOSES.items():
                rel = self._data_rel(sid, purpose)
                if not p.get("per_partner") and self.storage.read(rel) is not None:
                    files[rel] = None
                    removed[purpose] = 1
            if rec is not None:
                files[self._rec_rel(sid)] = None
                removed["consent_record"] = len(rec.get("history", []))
        for name, h in list(self._holders.items()):
            if h.get("erase"):
                try:
                    removed[name] = h["erase"](sid, actor)
                except Exception:
                    self._health("erase by holder %s failed\n%s" % (name, traceback.format_exc()))
                    removed[name] = "FAILED - retry erasure"
        prof = self.jurisdiction(self.settings())
        clinical = None
        if sid[0] == "P":
            clinical = ("Not deleted automatically. Clinical records are usually covered by legal retention "
                        "duties (%s). This request is recorded for the privacy officer to review." %
                        prof["erasure_note"])
        tomb = self._tombstone(sid, now, actor["id"], _text(reason, 80, "reason") or "subject_request",
                               sorted(removed), {"clinical_record": clinical})
        with self.storage.lock:
            self.storage.ensure_dirs("consent/t")
            files[self._tomb_rel(sid)] = tomb
            self.storage.commit(files, actor["id"], "consent.erase", sid,
                                {"removed": sorted(removed), "tombstone": tomb["id"],
                                 "clinical_review_requested": clinical is not None})
        return {"tombstone": tomb, "removed": removed, "clinical_record": clinical}

    def _tombstone(self, sid, now, by, reason, scope, extra):
        return {"id": "TS-" + secrets.token_hex(8), "subject_ref": self.subject_ref(sid), "kind": self._kind(sid),
                "erased_at": _iso(now), "by": by, "reason": reason, "removed": scope,
                "retained": ["audit trail entries (ids and hashes only)"] + (
                    ["clinical record (legal retention)"] if sid[0] == "P" else []),
                **{k: v for k, v in extra.items() if v}}

    def tombstone(self, sid):
        return self.storage.read(self._tomb_rel(self._check_subject(sid)))

    def export_erasure_register(self, actor):
        self._require_perm(actor, "users.manage")
        out = []
        for fn in self.storage.listdir("consent/t"):
            t = self.storage.read("consent/t/" + fn) if fn.endswith(".bin") else None
            if t:
                out.append(t)
        payload = {"format": "gorgon-covenant/erasure-register/1", "at": _iso(self.clock()), "tombstones": out}
        return {"register": payload, "signature": _jws(payload, self.receipt_key)}

    def reapply_erasures(self, actor, register):
        """After restoring an old backup: erase again every person in a (signed) erasure register."""
        self._require_perm(actor, "users.manage")
        body = verify_receipt((register or {}).get("signature", ""), self.receipt_key)
        if not body:
            raise ConsentError("The erasure register's signature does not match this installation.")
        refs = {t["subject_ref"] for t in body.get("tombstones", [])}
        done = []
        for fn in self.storage.listdir("consent/r"):
            sid = fn[:-4]
            if fn.endswith(".bin") and SUBJECT_RE.match(sid) and self.subject_ref(sid) in refs:
                with self.storage.lock:
                    files = {self._rec_rel(sid): None}
                    for purpose in PURPOSES:
                        if self.storage.read(self._data_rel(sid, purpose)) is not None:
                            files[self._data_rel(sid, purpose)] = None
                    self.storage.commit(files, actor["id"], "consent.erase_reapplied", sid, {})
                for h in self._holders.values():
                    if h.get("erase"):
                        try:
                            h["erase"](sid, actor)
                        except Exception:
                            self._health("re-erase failed\n" + traceback.format_exc())
                done.append(sid)
        return {"reapplied": done}

    # --------------------------------------------------------------- misc
    def summary(self):
        return {"encrypted": self.storage.encrypted, "purposes": list(PURPOSES), "jurisdictions":
                {k: v["name"] for k, v in JURISDICTIONS.items()}, "last_purge": self.last_purge,
                "disclaimer": DISCLAIMER}

    def _health(self, msg):
        try:
            self.clinic.health("%s %s" % (LOG_PREFIX, msg))
        except Exception:
            pass


class ScopedAccess:
    """Read-only, single-person, single-purpose handle. Holds no data itself; every call re-checks consent."""
    __slots__ = ("_cc", "subject", "purpose", "reader")

    def __init__(self, cc, subject, purpose, reader):
        self._cc, self.subject, self.purpose, self.reader = cc, subject, purpose, reader

    @property
    def allowed(self):
        return self._cc.is_active(self.subject, self.purpose)

    def get(self, field=None, default=None):
        data = self._cc.read(self.subject, self.purpose, reader=self.reader)
        return data if field is None else data.get(field, default)


# ======================================================================================================
# clinic_app integration
# ======================================================================================================
def _learner_gate(clinic, cc):
    """Wrap Clinic._learn (called after a note is signed) so self_learner only learns when the clinician has
    an active 'learning' consent and - unless the clinic switched it off - the patient has one as well.
    Withdrawing a clinician's learning consent shreds that clinician's learner profile."""
    original = clinic._learn

    def gated_learn(user, chart, note):
        try:
            if not cc.is_active(user["id"], "learning"):
                return None
            if cc.settings().get("learner_requires_patient_consent", True) and \
                    not cc.is_active(chart["id"], "learning"):
                return None
        except Exception:
            cc._health("learner gate refused (error)\n" + traceback.format_exc())
            return None
        return original(user, chart, note)

    gated_learn.__wrapped__ = original
    gated_learn.consent_gated = True
    clinic._learn = gated_learn

    def on_withdraw(sid, key, actor):
        if key == "learning" and sid[0] == "U":
            lr = clinic._learners.pop(sid, None)
            if lr is None:
                try:
                    lr = clinic._learner({"id": sid})
                    clinic._learners.pop(sid, None)
                except Exception:
                    lr = None
            if lr is not None:
                lr.reset()
    cc.on_withdraw(on_withdraw)

    def export(sid):
        if sid[0] != "U":
            return {"note": "Patients' notes may contribute masked, aggregated phrases to a clinician's writing "
                            "helper only with both consents; they cannot be traced back to one patient."}
        lr = clinic._learners.get(sid)
        st = lr.status() if lr is not None else {}
        return {"self_learner": {k: st.get(k) for k in ("enabled", "phrases", "templates", "order_sets",
                                                        "notes_learned")}}

    def erase(sid, actor):
        if sid[0] != "U":
            return 0
        lr = clinic._learners.pop(sid, None)
        if lr is None:
            try:
                lr = clinic._learner({"id": sid})
                clinic._learners.pop(sid, None)
            except Exception:
                lr = None
        if lr is not None:
            lr.reset()
            return 1
        return 0
    cc.register_holder("self_learner", export=export, erase=erase)


def _wrap(fn):
    """Route adapter: ConsentError -> clinic_app.HttpError with the right status."""
    def route(c, u, q, b):
        cc = getattr(c, "consent_center", None)
        mod = _host_module(c)
        Http = getattr(mod, "HttpError", None)
        if cc is None:
            if Http:
                raise Http(501, "The consent center is not installed.")
            raise ConsentError("The consent center is not installed.", 501)
        try:
            return fn(cc, u, q, b)
        except ConsentError as e:
            if Http:
                raise Http(e.status, e.msg)
            raise
    return route


def _pdf_route(cc, u, q, b):
    doc = cc.export_subject(u, q.get("subject"))
    data, warnings = cc.export_pdf(doc)
    if data is None:
        raise ConsentError("; ".join(warnings), 501)
    return ("file", "subject-access-%s.pdf" % doc["subject"], data)


ROUTE_TABLE = {
    ("GET", "/api/consent/explain"): lambda cc, u, q, b: cc.explain(q.get("subject") or None, q.get("kind") or None),
    ("GET", "/api/consent/status"): lambda cc, u, q, b: (cc._may_read(u, cc._check_subject(q.get("subject")))
                                                       or {"status": cc.status(q.get("subject"))}),
    ("POST", "/api/consent/grant"): lambda cc, u, q, b: cc.grant(u, b.get("subject"), b.get("purposes"),
                                                               b.get("pin"), b.get("method", "in_person"),
                                                               b.get("language", "en"), b.get("guardian"),
                                                               b.get("details"), b.get("explanation_version"),
                                                               b.get("jurisdiction")),
    ("POST", "/api/consent/withdraw"): lambda cc, u, q, b: cc.withdraw(u, b.get("subject"), b.get("purposes", "all"),
                                                                     b.get("reason", "")),
    ("POST", "/api/consent/data"): lambda cc, u, q, b: {"data": cc.write(u, b.get("subject"), b.get("purpose"),
                                                                         b.get("data"))},
    ("GET", "/api/consent/receipts"): lambda cc, u, q, b: {"receipts": cc.receipts(u, q.get("subject"))},
    ("GET", "/api/consent/export"): lambda cc, u, q, b: {"export": cc.export_subject(u, q.get("subject"))},
    ("GET", "/api/consent/export.pdf"): _pdf_route,
    ("POST", "/api/consent/erase"): lambda cc, u, q, b: cc.erase_subject(u, b.get("subject"), b.get("pin"),
                                                                       b.get("reason", "subject_request")),
    ("GET", "/api/consent/settings"): lambda cc, u, q, b: (cc._require_perm(u, "users.manage") or
                                                         {"settings": cc.settings(), "summary": cc.summary()}),
    ("POST", "/api/consent/settings"): lambda cc, u, q, b: {"settings": cc.update_settings(u, b.get("settings"))},
    ("POST", "/api/consent/purge"): lambda cc, u, q, b: (cc._require_perm(u, "users.manage") or
                                                       {"report": cc.purge_expired(actor_id=u["id"])}),
}


def mount(app, scheduler=True):
    """Attach the consent center to a clinic_app CovenantApp (or a bare Clinic). Never raises: on any problem it
    logs to the clinic health log and returns None, and the app runs on without it.
      * clinic.consent_center = the ConsentCenter
      * clinic_app.ROUTES gains /api/consent/... (login, CSRF, Origin and Host checks stay clinic_app's)
      * Clinic._learn is gated by consent (self_learner learns only with consent)
      * an hourly retention purge runs in a daemon thread (scheduler=False to skip)"""
    clinic = getattr(app, "clinic", app)
    try:
        existing = getattr(clinic, "consent_center", None)
        if isinstance(existing, ConsentCenter):
            return existing
        cc = ConsentCenter(clinic)
        clinic.consent_center = cc
        if not getattr(getattr(clinic, "_learn", None), "consent_gated", False):
            _learner_gate(clinic, cc)
        mod = _host_module(clinic)
        routes = getattr(mod, "ROUTES", None)
        if isinstance(routes, dict):
            for key, fn in ROUTE_TABLE.items():
                routes[key] = _wrap(fn)
        if scheduler and not getattr(clinic, "test_hooks", False):
            cc.start_scheduler()
        return cc
    except Exception:
        try:
            clinic.health("%s mount failed\n%s" % (LOG_PREFIX, traceback.format_exc()))
        except Exception:
            pass
        return None
