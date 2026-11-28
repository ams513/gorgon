#!/usr/bin/env python3
"""Stark Covenant - care suite (care_suite.py). Standard library only, Python 3.10+.

Behavioral-health and small-practice additions on top of practice_suite, all on this computer:
  * measurement-based care: a measure schedule per client, the caseload's due list and outcome indicators
    (change from the first score; response and remission where a published definition exists);
  * psychotherapy notes kept apart from the record: author-only, stored outside the chart file, so they are never
    in the chart view, the full-record export, the subject-access export or the FHIR / HL7 export;
  * safety plans: clinician-written versions, PIN-signed and sealed, with a printable copy for the client;
  * releases of information (typed client signature witnessed by a staff PIN, an expiry, revocable) and a
    disclosure log with a printable accounting;
  * episodes of care: a PIN-signed discharge summary closes the episode; reopening starts a new one;
  * payer authorizations: visits authorized against visits attended and booked, with alerts;
  * Good Faith Estimates (US No Surprises Act) with a billed-against-estimate check;
  * client statements and accounts-receivable aging (with a CSV file);
  * a to-do list: measures due, sessions with no note or only a draft, sessions not billed, note timeliness,
    authorization alerts and self-pay clients without a current estimate.

Mounted by clinic_app after practice_suite (it builds on it): mount() adds /api/care/... routes to clinic_app's
ROUTES, so sign-in, CSRF, Origin, Host and LAN checks stay clinic_app's. Every write goes through the clinic's
journaled, encrypted, audited Store. Nothing here uses the network.

Where the data lives (sealed by the security vault in normal mode; plain only in DEMO mode):
  charts/P*.bin         measure schedule, safety plans, releases, disclosures and episodes (inside the chart)
  sys/psy-P*.bin        psychotherapy notes of one client (outside the chart on purpose)
  sys/auths.bin         payer authorizations
  sys/estimates.bin     Good Faith Estimates, their notice wording and the reminder setting
  sys/care-index.bin    clients with a measure schedule, and discharged clients

Honest limits
  * Not a compliance product. Psychotherapy-note separation, releases, the disclosure log and Good Faith Estimates
    are record-keeping features; the clinic's own compliance review decides how they are used.
  * Outcome indicators restate published definitions on the clinic's own scores; they are not a diagnosis.
  * Authorization usage counts attended appointments inside the authorization's dates (plus visits used before);
    it does not read payer records.
  * Aging applies payments linked to an invoice to that invoice and other payments to the oldest open invoices.
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import re
import traceback
from datetime import date, datetime, timedelta

import practice_suite as ps

PracticeError = ps.PracticeError
LOG_PREFIX = "care_suite"
CARE_INDEX = "sys/care-index.bin"
AUTHS = "sys/auths.bin"
ESTIMATES = "sys/estimates.bin"

# ======================================================================================================
# Measurement-based care
# ======================================================================================================
MBC_EVERY = (7, 14, 21, 28, 30, 42, 56, 60, 90, 180)
MBC_DUE_DAYS = 7
OUTCOMES_DEFAULT_DAYS = 365
# change: the smallest change counted as improved / worsened (points of the instrument's own total)
OUTCOME_RULES = {
    "phq9": {"better": "lower", "change": 5, "baseline_min": 10, "response_pct": 50, "remission_below": 5},
    "gad7": {"better": "lower", "change": 5, "baseline_min": 10, "response_pct": 50, "remission_below": 5},
    "pcl5": {"better": "lower", "change": 10},
    "who5": {"better": "higher", "change": 3},
    "auditc": {"better": "lower"},
}
OUTCOME_SOURCES = {
    "phq9": "Change of 5 points: Lowe B et al. Med Care. 2004;42(12):1194-1201. Response (a fall of 50% or more from "
            "the first score) and remission (a score below 5) are counted only when the first score was 10 or more, "
            "as in common measurement-based care and depression remission measures.",
    "gad7": "Change of 5 points, response (a fall of 50% or more) and remission (below 5) use the same conventions as "
            "the PHQ-9, counted only when the first score was 10 or more.",
    "pcl5": "National Center for PTSD: a change of 10 to 20 points is a clinically meaningful change; 10 is used here.",
    "who5": "Topp CW et al. Psychother Psychosom. 2015;84(3):167-176: a change of 10 percentage points is significant; "
            "3 raw points (12 percentage points) is the smallest whole-point step above that.",
    "auditc": "No change rule is applied to the AUDIT-C.",
}

# ======================================================================================================
# Psychotherapy notes, safety plans, releases, disclosures, episodes
# ======================================================================================================
PSY_SEALED = ("date", "text", "author", "signed_by", "signed_at")
SAFETY_SECTIONS = ("warning_signs", "coping", "distractions", "help_people", "professionals", "safe_environment",
                   "reasons")
SAFETY_SEALED = ("version", "sections", "client_name", "client_date", "author", "signed_by", "signed_at")
RELEASE_DIRECTIONS = ("release_to", "obtain_from", "exchange")
RELEASE_INFO = ("attendance", "assessment", "treatment_plan", "progress_summary", "measures", "medications",
                "discharge_summary", "billing", "full_record", "psychotherapy_notes")
RELEASE_SEALED = ("direction", "party", "party_contact", "information", "information_other", "purpose", "expires",
                  "expires_event", "signer_name", "signer_role", "signed_date", "signed_by", "signed_by_name",
                  "signed_at")
DISCLOSURE_BASES = ("authorization", "client_request", "treatment", "payment", "operations", "required_by_law",
                    "court_order", "public_health", "health_oversight", "abuse_report", "serious_threat", "research",
                    "other")
ACCOUNTING_LEFT_OUT = ("authorization", "client_request", "treatment", "payment", "operations")
DISCLOSURE_METHODS = ("mail", "fax", "email", "in_person", "phone", "secure_portal", "other")
DISCHARGE_REASONS = ("goals_met", "client_ended", "referred", "moved", "no_contact", "other")
DISCHARGE_SEALED = ("opened", "closed", "reason", "summary", "outcomes", "recommendations", "referrals", "author",
                    "signed_by", "signed_at")

# ======================================================================================================
# Authorizations, Good Faith Estimates, aging, to-do
# ======================================================================================================
AUTH_SOON_DAYS = 14
AUTH_LOW = 2
MAX_AUTH_VISITS = 999
GFE_MAX_DAYS = 366
GFE_DISPUTE_CENTS = 40000          # US$400: a bill this much above the estimate can be disputed (No Surprises Act)
GFE_LOOKAHEAD_DAYS = 30
GFE_SEALED = ("number", "patient", "provider", "period_start", "period_end", "lines", "diagnoses", "notes",
              "currency", "total", "notice", "issued_at", "issued_by")
GFE_NOTICE_DEFAULT = (
    "The Good Faith Estimate shows the costs of items and services that are reasonably expected for your health care "
    "needs for an item or service. The estimate is based on information known at the time the estimate was created. "
    "The Good Faith Estimate does not include any unknown or unexpected costs that may arise during treatment. You "
    "could be charged more if complications or special circumstances occur. If this happens, federal law allows you "
    "to dispute (appeal) the bill. If you are billed for more than this Good Faith Estimate, you have the right to "
    "dispute the bill. You may contact the health care provider or facility listed to let them know the billed "
    "charges are higher than the Good Faith Estimate. You can ask them to update the bill to match the Good Faith "
    "Estimate, ask to negotiate the bill, or ask if there is financial assistance available. You may also start a "
    "dispute resolution process with the U.S. Department of Health and Human Services (HHS). If you choose to use the "
    "dispute resolution process, you must start the dispute process within 120 calendar days (about 4 months) of the "
    "date on the original bill. For questions or more information about your right to a Good Faith Estimate or the "
    "dispute process, visit www.cms.gov/nosurprises. Keep a copy of this Good Faith Estimate in a safe place or take "
    "pictures of it. You may need it if you are billed a higher amount.")
AGING_BUCKETS = ("0_30", "31_60", "61_90", "91_plus")
GAP_DEFAULT_DAYS = 30
GAP_MAX_DAYS = 366
ATTENDED = ("completed", "arrived")


def local_day(ts):
    """A stored UTC time stamp as this computer's own calendar day (the day people see and type)."""
    s = str(ts or "")
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone().date().isoformat()
    except ValueError:
        return s[:10]


def outcome(instrument, measures):
    """First and latest active score of one instrument, the change, and the published indicators.
    direction: improved | worsened | no_reliable_change (None when the instrument has no change rule or one score).
    response / remission: True / False only for PHQ-9 and GAD-7 with a first score of 10 or more, else None."""
    rule = OUTCOME_RULES.get(instrument) or {}
    ms = sorted([m for m in measures if m.get("instrument") == instrument and m.get("status") == "active"],
                key=lambda m: (m.get("date") or "", m.get("at") or ""))
    if not ms:
        return None
    first, last = ms[0], ms[-1]
    out = {"instrument": instrument, "count": len(ms), "change": last["total"] - first["total"],
           "first": {"date": first["date"], "total": first["total"], "band": first.get("band")},
           "latest": {"date": last["date"], "total": last["total"], "band": last.get("band")},
           "direction": None, "response": None, "remission": None}
    if len(ms) < 2:
        return out
    if rule.get("change"):
        gain = out["change"] if rule["better"] == "higher" else -out["change"]
        out["direction"] = ("improved" if gain >= rule["change"] else "worsened" if -gain >= rule["change"]
                            else "no_reliable_change")
    if rule.get("response_pct") and first["total"] >= rule["baseline_min"]:
        out["response"] = (first["total"] - last["total"]) * 100 >= rule["response_pct"] * first["total"]
        out["remission"] = last["total"] < rule["remission_below"]
    return out


def open_items(invoices, payments, as_of=None):
    """Per-invoice open amounts after payments, for one client. Payments linked to an issued invoice (same currency)
    go to it; the rest (net of refunds) pays the oldest open invoices first. Entries dated after as_of are left out.
    -> (issued invoices oldest first, {invoice id: open cents}, {currency: unapplied cents, positive = credit})."""
    inv = sorted([i for i in invoices if i["status"] == "issued" and (not as_of or i["date"] <= as_of)],
                 key=lambda i: (i["date"], i.get("issued_at") or "", i["id"]))
    open_ = {i["id"]: i["total"] for i in inv}
    cur_of = {i["id"]: i["currency"] for i in inv}
    loose = {}
    for y in sorted(payments, key=lambda y: (y["date"], y.get("at") or "", y["id"])):
        if y["status"] != "active" or (as_of and y["date"] > as_of):
            continue
        amt = -y["amount"] if y["kind"] == "refund" else y["amount"]
        if y.get("invoice") in open_ and cur_of[y["invoice"]] == y["currency"]:
            open_[y["invoice"]] -= amt
        else:
            loose[y["currency"]] = loose.get(y["currency"], 0) + amt
    for cur in list(loose):
        for i in inv:
            if loose[cur] <= 0:
                break
            if i["currency"] == cur and open_[i["id"]] > 0:
                pay = min(loose[cur], open_[i["id"]])
                open_[i["id"]] -= pay
                loose[cur] -= pay
    return inv, open_, loose


def aging_rows(invoices, payments, as_of):
    """{currency: {"buckets": {0_30, 31_60, 61_90, 91_plus}, "credit", "total"}} for one client.
    total = sum(buckets) - credit = the client's balance in that currency on as_of."""
    inv, open_, loose = open_items(invoices, payments, as_of)
    out = {}
    end = date.fromisoformat(as_of)

    def row(cur):
        return out.setdefault(cur, {"buckets": {b: 0 for b in AGING_BUCKETS}, "credit": 0, "total": 0})
    for i in inv:
        v = open_[i["id"]]
        r = row(i["currency"])
        if v < 0:
            r["credit"] += -v
            continue
        if v == 0:
            continue
        age = (end - date.fromisoformat(i["date"])).days
        b = "0_30" if age <= 30 else "31_60" if age <= 60 else "61_90" if age <= 90 else "91_plus"
        r["buckets"][b] += v
    for cur, v in loose.items():
        if v:
            row(cur)["credit"] += v
    for r in out.values():
        if r["credit"] < 0:                 # refunds not linked to an invoice: owed now
            r["buckets"]["0_30"] += -r["credit"]
            r["credit"] = 0
        r["total"] = sum(r["buckets"].values()) - r["credit"]
    return {k: v for k, v in out.items() if v["total"] or v["credit"] or any(v["buckets"].values())}


class CareSuite:
    def __init__(self, clinic, suite):
        self.c = clinic
        self.ps = suite
        self.H = suite.H

    # --- helpers ------------------------------------------------------------------------------------------
    @property
    def store(self):
        return self.c.store

    def _req(self, user, *perms):
        self.ps._req(user, *perms)

    def _text(self, v, n, field, multiline=False):
        return self.ps._text(v, n, field, multiline)

    def _day(self, v, field="date", required=False, not_future=False):
        return self.ps._day(v, field, required, not_future)

    def _choice(self, v, allowed, field, default=None):
        return self.ps._choice(v, allowed, field, default)

    @staticmethod
    def _today():
        return date.today().isoformat()

    def _int(self, v, lo, hi, field):
        try:
            if isinstance(v, bool) or v in (None, ""):
                raise ValueError
            n = int(str(v).strip())
        except (TypeError, ValueError):
            raise PracticeError(400, "%s must be a whole number." % field)
        if not lo <= n <= hi:
            raise PracticeError(400, "%s must be %d to %d." % (field, lo, hi))
        return n

    def _names(self):
        return self.c._index()["patients"]

    def _pname(self, pid):
        return (self._names().get(pid) or {}).get("name", "")

    def _index(self):
        return self.store.read_obj(CARE_INDEX, {"mbc": {}, "discharged": {}})

    def _seal(self, kind, pid, x, fields):
        content = self.H.sha256_bytes(self.H.canon({k: x.get(k) for k in fields}))
        sig = hmac.new(self.c.sig_key, self.H.canon({"kind": kind, "patient": pid, "id": x["id"], "content": content,
                                                     "signer": x.get("signed_by"), "at": x.get("signed_at")}),
                       hashlib.sha256).hexdigest()
        return content, sig

    def _valid(self, kind, pid, x, fields):
        if not x.get("signature"):
            return None
        content, sig = self._seal(kind, pid, x, fields)
        return content == x.get("content_sha") and hmac.compare_digest(sig, x["signature"])

    def _find(self, items, xid, what):
        x = next((y for y in items if y.get("id") == xid), None)
        if x is None:
            raise PracticeError(404, "No such %s." % what)
        return x

    def _patient_appts(self, pid):
        idx = self.store.read_obj(ps.SCHED_INDEX, {"patients": {}})
        out = []
        for mk in idx["patients"].get(pid, []):
            out += [dict(a) for a in self.ps._month(mk + "-01")["appts"].values() if a["patient"] == pid]
        return sorted(out, key=lambda a: (a["date"], a["time"]))

    def _pdf(self, title, lines, loc, chart=None):
        if chart is not None and chart.get("synthetic"):
            lines.insert(0, ("DEMO - synthetic patient, not a real person", True))
        return ps._pdf(title, lines, loc)

    def _client_line(self, lab, chart):
        d = chart["demographics"]
        return "%s   %s: %s" % (d.get("name", ""), lab("app.field.dob"), d.get("dob", ""))

    # ==================================================================================================
    # Measurement-based care: schedule, due list, outcomes
    # ==================================================================================================
    def _schedule_view(self, chart, today):
        out = []
        ms = chart.get("measures", [])
        for s in chart.get("measure_schedule", []):
            s = dict(s)
            if s["status"] == "active":
                last = max((m["date"] for m in ms if m["instrument"] == s["instrument"] and m["status"] == "active"),
                           default=None)
                nxt = (date.fromisoformat(last) + timedelta(days=s["every_days"])).isoformat() if last else s["start"]
                s["last"] = last
                s["due"] = max(nxt, s["start"])
                s["overdue_days"] = max(0, (date.fromisoformat(today) - date.fromisoformat(s["due"])).days)
            out.append(s)
        return out

    def mbc_patient(self, user, pid):
        self.c.require(user, "clin.read")
        chart = self.c._load_chart(pid)
        today = self._today()
        outs = {}
        for k in ps.INSTRUMENTS:
            o = outcome(k, chart.get("measures", []))
            if o:
                outs[k] = o
        return {"schedule": self._schedule_view(chart, today), "outcomes": outs, "every": list(MBC_EVERY),
                "sources": OUTCOME_SOURCES, "today": today}

    def mbc_set(self, user, pid, instrument, every_days, start=None):
        self.c.require(user, "measure.write")
        if instrument not in ps.INSTRUMENTS:
            raise PracticeError(400, "Unknown measure.")
        every = self._int(every_days, MBC_EVERY[0], MBC_EVERY[-1], "Every (days)")
        start = self._day(start, "start date") or self._today()
        now = self.H.now_iso()
        with self.store.lock:
            chart = self.c._load_chart(pid)
            sch = chart.setdefault("measure_schedule", [])
            s = next((x for x in sch if x["instrument"] == instrument and x["status"] == "active"), None)
            if s:
                s["history"].append({k: s[k] for k in ("every_days", "start", "by", "by_name", "at")})
                s.update(every_days=every, start=start, by=user["id"], by_name=user.get("display", ""), at=now)
                action = "mbc.update"
            else:
                s = {"id": self.H.new_id("H"), "instrument": instrument, "every_days": every, "start": start,
                     "status": "active", "by": user["id"], "by_name": user.get("display", ""), "at": now,
                     "history": []}
                sch.append(s)
                action = "mbc.schedule"
            idx = self._index()
            idx["mbc"][pid] = True
            self.c._save_chart(chart, user["id"], action, {"schedule": s["id"], "instrument": instrument,
                                                           "every_days": every},
                               extra_files={CARE_INDEX: self.c.codec.dump(idx)}, index=False)
        return self._schedule_view({"measure_schedule": [s], "measures": chart.get("measures", [])},
                                   self._today())[0]

    def mbc_stop(self, user, pid, sid):
        self.c.require(user, "measure.write")
        with self.store.lock:
            chart = self.c._load_chart(pid)
            s = self._find(chart.get("measure_schedule", []), sid, "measure schedule")
            if s["status"] != "active":
                raise PracticeError(409, "This schedule is already stopped.")
            s.update(status="stopped", stopped_by=user["id"], stopped_at=self.H.now_iso())
            idx = self._index()
            if not any(x["status"] == "active" for x in chart["measure_schedule"]):
                idx["mbc"].pop(pid, None)
            self.c._save_chart(chart, user["id"], "mbc.stop", {"schedule": sid},
                               extra_files={CARE_INDEX: self.c.codec.dump(idx)}, index=False)
        return s

    def _next_appts(self, today):
        """{patient: the next still-scheduled appointment date from today}"""
        out = {}
        for a in self.ps._all_appts():
            if a["status"] == "scheduled" and a["date"] >= today:
                if a["patient"] not in out or a["date"] < out[a["patient"]]:
                    out[a["patient"]] = a["date"]
        return out

    def mbc_due(self, user, within=None):
        """Measures due within N days (default 7) or overdue, for clients with a schedule who are not discharged."""
        self.c.require(user, "clin.read")
        within = MBC_DUE_DAYS if within in (None, "") else self._int(within, 0, 90, "within")
        today = self._today()
        until = (date.today() + timedelta(days=within)).isoformat()
        idx = self._index()
        nxt = self._next_appts(today)
        rows = []
        for pid in idx["mbc"]:
            if pid in idx["discharged"]:
                continue
            try:
                chart = self.c._load_chart(pid)
            except Exception:
                continue
            for s in self._schedule_view(chart, today):
                if s["status"] == "active" and s["due"] <= until:
                    rows.append({"patient": pid, "patient_name": self._pname(pid), "instrument": s["instrument"],
                                 "due": s["due"], "overdue_days": s["overdue_days"], "last": s["last"],
                                 "every_days": s["every_days"], "next_appt": nxt.get(pid)})
        rows.sort(key=lambda r: (r["due"], r["patient_name"].lower()))
        return {"due": rows, "within": within, "today": today}

    def outcomes(self, user, start=None, end=None):
        """Caseload outcomes: per instrument, clients with two or more scores whose latest score is in the period."""
        self.c.require(user, "clin.read")
        end = self._day(end, "to") or self._today()
        start = self._day(start, "from") or (date.fromisoformat(end) - timedelta(days=OUTCOMES_DEFAULT_DAYS)).isoformat()
        if start > end:
            raise PracticeError(400, "The start date is after the end date.")
        rows, summary = [], {}
        for pid in self._names():
            try:
                chart = self.c._load_chart(pid)
            except Exception:
                continue
            for k in ps.INSTRUMENTS:
                o = outcome(k, chart.get("measures", []))
                if not o or o["count"] < 2 or not start <= o["latest"]["date"] <= end:
                    continue
                rows.append(dict(o, patient=pid, patient_name=chart["demographics"].get("name", "")))
                s = summary.setdefault(k, {"clients": 0, "improved": 0, "worsened": 0, "no_reliable_change": 0,
                                           "response_eligible": 0, "response": 0, "remission": 0,
                                           "change_rule": OUTCOME_RULES.get(k, {}).get("change")})
                s["clients"] += 1
                if o["direction"]:
                    s[o["direction"]] += 1
                if o["response"] is not None:
                    s["response_eligible"] += 1
                    s["response"] += int(o["response"])
                    s["remission"] += int(o["remission"])
        rows.sort(key=lambda r: (r["instrument"], r["patient_name"].lower()))
        self.c._commit({}, user["id"], "mbc.outcomes", "practice", {"from": start, "to": end, "rows": len(rows)})
        return {"from": start, "to": end, "summary": summary, "rows": rows[:1000], "sources": OUTCOME_SOURCES}

    # ==================================================================================================
    # Psychotherapy notes: author-only, outside the chart file
    # ==================================================================================================
    def _psy_rel(self, pid):
        self.c._chart_rel(pid)
        return "sys/psy-%s.bin" % pid

    def _psy(self, pid):
        return self.store.read_obj(self._psy_rel(pid), {"notes": []})

    def _psy_view(self, pid, n):
        return dict(n, signature_valid=self._valid("psynote", pid, n, PSY_SEALED))

    def _psy_note(self, user, db, nid):
        n = self._find(db["notes"], nid, "psychotherapy note")
        if n["author"] != user["id"]:
            raise PracticeError(404, "No such psychotherapy note.")      # other authors' notes do not exist here
        return n

    def psy_list(self, user, pid):
        self.c.require(user, "psynote.write")
        self.ps._patient(pid)
        notes = [self._psy_view(pid, n) for n in self._psy(pid)["notes"] if n["author"] == user["id"]]
        self.c._commit({}, user["id"], "psynote.view", pid, {"count": len(notes)})
        return {"notes": sorted(notes, key=lambda n: (n["date"], n["created"]), reverse=True)}

    def psy_save(self, user, pid, b, note_id=None):
        self.c.require(user, "psynote.write")
        self.ps._patient(pid)
        if not isinstance(b, dict):
            raise PracticeError(400, "note required")
        text = self._text(b.get("text"), 50000, "note", multiline=True)
        if not text:
            raise PracticeError(400, "The note is empty.")
        day = self._day(b.get("date"), "session date", not_future=True) or self._today()
        now = self.H.now_iso()
        with self.store.lock:
            db = self._psy(pid)
            if note_id:
                n = self._psy_note(user, db, note_id)
                if n["status"] != "draft":
                    raise PracticeError(423, "A signed psychotherapy note is locked. Write a new note.")
                n.update(text=text, date=day, updated=now)
                action = "psynote.update"
            else:
                n = {"id": self.H.new_id("J"), "date": day, "text": text, "author": user["id"],
                     "author_name": user.get("display", ""), "created": now, "updated": now, "status": "draft",
                     "signed_by": None, "signed_at": None, "content_sha": None, "signature": None}
                db["notes"].append(n)
                action = "psynote.create"
            self.c._commit({self._psy_rel(pid): self.c.codec.dump(db)}, user["id"], action, pid, {"note": n["id"]})
        return self._psy_view(pid, n)

    def psy_sign(self, user, pid, note_id, pin):
        self.c.require(user, "psynote.write")
        with self.store.lock:
            n = self._psy_note(user, self._psy(pid), note_id)
            if n["status"] != "draft":
                raise PracticeError(409, "This note is already signed.")
            self.c.verify_pin(user, pin)
            db = self._psy(pid)
            n = self._psy_note(user, db, note_id)
            n.update(status="signed", signed_by=user["id"], signed_at=self.H.now_iso())
            n["content_sha"], n["signature"] = self._seal("psynote", pid, n, PSY_SEALED)
            self.c._commit({self._psy_rel(pid): self.c.codec.dump(db)}, user["id"], "psynote.sign", pid,
                           {"note": note_id, "content_sha": n["content_sha"]})
        return self._psy_view(pid, n)

    def psy_error(self, user, pid, note_id, reason):
        self.c.require(user, "psynote.write")
        reason = self._text(reason, 300, "reason")
        if not reason:
            raise PracticeError(400, "A reason is required.")
        with self.store.lock:
            db = self._psy(pid)
            n = self._psy_note(user, db, note_id)
            if n["status"] == "entered_in_error":
                raise PracticeError(409, "Already marked entered in error.")
            n.update(status="entered_in_error", error_reason=reason, error_at=self.H.now_iso())
            self.c._commit({self._psy_rel(pid): self.c.codec.dump(db)}, user["id"], "psynote.entered_in_error", pid,
                           {"note": note_id})
        return self._psy_view(pid, n)

    def psy_pdf(self, user, pid):
        """The author's own psychotherapy notes for one client (for a disclosure the author decides to make)."""
        self.c.require(user, "psynote.write")
        chart = self.c._load_chart(pid)
        notes = sorted([n for n in self._psy(pid)["notes"] if n["author"] == user["id"]],
                       key=lambda n: (n["date"], n["created"]))
        lab, loc = self.ps._labels(user)
        L = [(lab("pm.care.psy.pdf_title"), True), (self._client_line(lab, chart), False),
             (lab("pm.care.psy.pdf_note"), False)]
        for n in notes:
            v = self._psy_view(pid, n)
            L.append(("%s - %s" % (n["date"], n.get("author_name") or n["author"]), True))
            L.append((n["text"], False))
            if n["status"] == "signed":
                L.append((lab("pm.care.signed_line", name=n.get("author_name") or n["author"], when=n["signed_at"],
                              code=n["content_sha"][:12]), False))
                if not v["signature_valid"]:
                    L.append((lab("app.chip.sig_invalid"), True))
            elif n["status"] == "draft":
                L.append((lab("app.chip.draft"), False))
            else:
                L.append((lab("pm.meas.in_error", reason=n.get("error_reason", "")), True))
        if not notes:
            L.append(("-", False))
        self.c._commit({}, user["id"], "psynote.print", pid, {"count": len(notes)})
        data, ext = self._pdf(lab("pm.care.psy.pdf_title"), L, loc, chart)
        return ("file", "psychotherapy-notes-%s.%s" % (pid, ext), data)

    # ==================================================================================================
    # Safety plans: clinician-written versions, PIN-signed and sealed, printable for the client
    # ==================================================================================================
    def _emergency_numbers(self):
        try:
            ec = ps._opt("emergency_comms")
            cc = self.c.locale_settings().get("region") or ""
            e = ec.country_entry(cc) if ec is not None and cc else None
            return {"country": cc.upper(), "numbers": ec.numbers_list(e)} if e else {"country": cc.upper(),
                                                                                      "numbers": []}
        except Exception:
            return {"country": "", "numbers": []}

    def _safety_view(self, pid, p):
        return dict(p, signature_valid=self._valid("safety", pid, p, SAFETY_SEALED))

    def safety(self, user, pid):
        self.c.require(user, "clin.read")
        chart = self.c._load_chart(pid)
        plans = [self._safety_view(pid, p) for p in chart.get("safety_plans", [])]
        return {"plans": sorted(plans, key=lambda p: p["version"], reverse=True), "sections": list(SAFETY_SECTIONS),
                "emergency": self._emergency_numbers()}

    def safety_save(self, user, pid, b, plan_id=None):
        self.c.require(user, "plan.write")
        if not isinstance(b, dict):
            raise PracticeError(400, "plan required")
        sec = b.get("sections") if isinstance(b.get("sections"), dict) else {}
        sections = {k: self._text(sec.get(k), 2000, k.replace("_", " "), multiline=True) for k in SAFETY_SECTIONS}
        if not any(sections.values()):
            raise PracticeError(400, "Write at least one part of the safety plan.")
        f = {"sections": sections, "client_name": self._text(b.get("client_name"), 120, "client name"),
             "client_date": self._day(b.get("client_date"), "client date", not_future=True)}
        now = self.H.now_iso()
        with self.store.lock:
            chart = self.c._load_chart(pid)
            plans = chart.setdefault("safety_plans", [])
            p = next((x for x in plans if x["id"] == plan_id), None) if plan_id else None
            if plan_id and p is None:
                raise PracticeError(404, "No such safety plan.")
            if p is not None and p["status"] == "draft":
                if p["author"] != user["id"]:
                    raise PracticeError(403, "Only the author can change this draft.")
                p.update(f, updated=now)
                action = "safety.update"
            else:
                p = dict(f, id=self.H.new_id("K"), version=max([x["version"] for x in plans] or [0]) + 1,
                         status="draft", author=user["id"], author_name=user.get("display", ""), created=now,
                         updated=now, signed_by=None, signed_by_name=None, signed_at=None, content_sha=None,
                         signature=None, based_on=plan_id or None)
                plans.append(p)
                action = "safety.create"
            self.c._save_chart(chart, user["id"], action, {"plan": p["id"], "version": p["version"]}, index=False)
        return self._safety_view(pid, p)

    def safety_sign(self, user, pid, plan_id, pin):
        self.c.require(user, "plan.write")
        with self.store.lock:
            p = self._find(self.c._load_chart(pid).get("safety_plans", []), plan_id, "safety plan")
            if p["status"] != "draft":
                raise PracticeError(409, "This safety plan is already signed.")
            if p["author"] != user["id"]:
                raise PracticeError(403, "Only the author can sign this safety plan.")
            self.c.verify_pin(user, pin)
            chart = self.c._load_chart(pid)
            p = self._find(chart["safety_plans"], plan_id, "safety plan")
            for x in chart["safety_plans"]:
                if x["status"] == "signed":
                    x["status"] = "superseded"
            p.update(status="signed", signed_by=user["id"], signed_by_name=user.get("display", ""),
                     signed_at=self.H.now_iso())
            p["content_sha"], p["signature"] = self._seal("safety", pid, p, SAFETY_SEALED)
            self.c._save_chart(chart, user["id"], "safety.sign", {"plan": plan_id, "content_sha": p["content_sha"]},
                               index=False)
        return self._safety_view(pid, p)

    def safety_error(self, user, pid, plan_id, reason):
        self.c.require(user, "plan.write")
        reason = self._text(reason, 300, "reason")
        if not reason:
            raise PracticeError(400, "A reason is required.")
        with self.store.lock:
            chart = self.c._load_chart(pid)
            p = self._find(chart.get("safety_plans", []), plan_id, "safety plan")
            if p["status"] == "entered_in_error":
                raise PracticeError(409, "Already marked entered in error.")
            p.update(status="entered_in_error", error_reason=reason, error_by=user["id"], error_at=self.H.now_iso())
            self.c._save_chart(chart, user["id"], "safety.entered_in_error", {"plan": plan_id}, index=False)
        return self._safety_view(pid, p)

    def safety_pdf(self, user, pid, plan_id):
        self.c.require(user, "clin.read")
        chart = self.c._load_chart(pid)
        p = self._find(chart.get("safety_plans", []), plan_id, "safety plan")
        if not p.get("signature"):
            raise PracticeError(409, "Sign the safety plan first.")
        lab, loc = self.ps._labels(user)
        prof = self.ps._ledger().get("profile") or {}
        L = [(prof.get("practice_name") or self.H.APP_NAME, False), (lab("pm.care.safety.title"), True),
             (self._client_line(lab, chart), False)]
        for k in SAFETY_SECTIONS:
            L.append((lab("pm.care.safety.s_" + k), True))
            L.append((p["sections"].get(k) or "-", False))
        em = self._emergency_numbers()
        L.append((lab("pm.care.safety.emergency_line", numbers=" / ".join(em["numbers"])) if em["numbers"]
                  else lab("pm.care.safety.emergency_plain"), True))
        if p.get("client_name"):
            L.append((lab("pm.care.safety.client_line", name=p["client_name"], date=p.get("client_date") or "-"),
                      False))
        L.append((lab("pm.care.signed_line", name=p.get("signed_by_name") or p["signed_by"], when=p["signed_at"],
                      code=p["content_sha"][:12]), False))
        if not self._valid("safety", pid, p, SAFETY_SEALED):
            L.append((lab("app.chip.sig_invalid"), True))
        if p["status"] == "entered_in_error":
            L.append((lab("pm.meas.in_error", reason=p.get("error_reason", "")), True))
        self.c._commit({}, user["id"], "safety.print", pid, {"plan": plan_id})
        data, ext = self._pdf(lab("pm.care.safety.title"), L, loc, chart)
        return ("file", "safety-plan-%s-v%d.%s" % (pid, p["version"], ext), data)

    # ==================================================================================================
    # Releases of information and the disclosure log
    # ==================================================================================================
    def _release_state(self, r, on=None):
        on = on or self._today()
        if r["status"] in ("revoked", "entered_in_error"):
            return r["status"]
        if r.get("expires") and r["expires"] < on:
            return "expired"
        return "active"

    def _release_view(self, pid, r):
        return dict(r, state=self._release_state(r), signature_valid=self._valid("release", pid, r, RELEASE_SEALED))

    def releases(self, user, pid):
        self.c.require(user, "release.write")
        chart = self.c._load_chart(pid)
        return {"releases": [self._release_view(pid, r) for r in reversed(chart.get("releases", []))],
                "disclosures": sorted(chart.get("disclosures", []), key=lambda d: (d["date"], d["at"]), reverse=True),
                "directions": list(RELEASE_DIRECTIONS), "information": list(RELEASE_INFO),
                "bases": list(DISCLOSURE_BASES), "methods": list(DISCLOSURE_METHODS),
                "accounting_left_out": list(ACCOUNTING_LEFT_OUT), "signer_roles": list(ps.SIGNER_ROLES)}

    def release_add(self, user, pid, b, pin):
        self.c.require(user, "release.write")
        if not isinstance(b, dict):
            raise PracticeError(400, "release required")
        info = b.get("information") or []
        if not isinstance(info, list) or any(x not in RELEASE_INFO for x in info):
            raise PracticeError(400, "Choose what information the release covers.")
        info = [x for x in RELEASE_INFO if x in info]
        other = self._text(b.get("information_other"), 300, "other information")
        if not info and not other:
            raise PracticeError(400, "Choose what information the release covers.")
        if "psychotherapy_notes" in info and (len(info) > 1 or other):
            raise PracticeError(400, "Psychotherapy notes need a release of their own, with nothing else on it.")
        r = {"direction": self._choice(b.get("direction"), RELEASE_DIRECTIONS, "direction", "release_to"),
             "party": self._text(b.get("party"), 200, "person or organisation"),
             "party_contact": self._text(b.get("party_contact"), 300, "contact", multiline=True),
             "information": info, "information_other": other,
             "purpose": self._text(b.get("purpose"), 300, "purpose"),
             "expires": self._day(b.get("expires"), "expiry date"),
             "expires_event": self._text(b.get("expires_event"), 200, "expiry event"),
             "signer_name": self._text(b.get("signer_name"), 120, "signer name"),
             "signer_role": self._choice(b.get("signer_role"), ps.SIGNER_ROLES, "signer role", "client"),
             "signed_date": self._day(b.get("signed_date"), "date signed", required=True, not_future=True)}
        if not r["party"]:
            raise PracticeError(400, "Name the person or organisation.")
        if not r["purpose"]:
            raise PracticeError(400, "State the purpose of the release.")
        if not r["signer_name"]:
            raise PracticeError(400, "The person signing types their full name.")
        if not r["expires"] and not r["expires_event"]:
            raise PracticeError(400, "Give an expiry date or an expiry event.")
        if r["expires"] and r["expires"] < r["signed_date"]:
            raise PracticeError(400, "The expiry date is before the date signed.")
        self.ps._patient(pid)
        self.c.verify_pin(user, pin)
        with self.store.lock:
            chart = self.c._load_chart(pid)
            r.update(id=self.H.new_id("O"), status="active", signed_by=user["id"],
                     signed_by_name=user.get("display", ""), signed_at=self.H.now_iso())
            r["content_sha"], r["signature"] = self._seal("release", pid, r, RELEASE_SEALED)
            chart.setdefault("releases", []).append(r)
            self.c._save_chart(chart, user["id"], "release.sign", {"release": r["id"], "direction": r["direction"],
                                                                   "content_sha": r["content_sha"]}, index=False)
        return self._release_view(pid, r)

    def release_revoke(self, user, pid, rid, reason, day=None):
        self.c.require(user, "release.write")
        reason = self._text(reason, 300, "reason")
        if not reason:
            raise PracticeError(400, "A reason is required.")
        day = self._day(day, "revocation date", not_future=True) or self._today()
        with self.store.lock:
            chart = self.c._load_chart(pid)
            r = self._find(chart.get("releases", []), rid, "release")
            if r["status"] != "active":
                raise PracticeError(409, "Only an active release can be revoked.")
            r.update(status="revoked", revoked_date=day, revoked_reason=reason, revoked_by=user["id"],
                     revoked_at=self.H.now_iso())
            self.c._save_chart(chart, user["id"], "release.revoke", {"release": rid}, index=False)
        return self._release_view(pid, r)

    def release_error(self, user, pid, rid, reason):
        self.c.require(user, "release.write")
        reason = self._text(reason, 300, "reason")
        if not reason:
            raise PracticeError(400, "A reason is required.")
        with self.store.lock:
            chart = self.c._load_chart(pid)
            r = self._find(chart.get("releases", []), rid, "release")
            if r["status"] == "entered_in_error":
                raise PracticeError(409, "Already marked entered in error.")
            r.update(status="entered_in_error", error_reason=reason, error_by=user["id"], error_at=self.H.now_iso())
            self.c._save_chart(chart, user["id"], "release.entered_in_error", {"release": rid}, index=False)
        return self._release_view(pid, r)

    def release_pdf(self, user, pid, rid):
        self.c.require(user, "release.write")
        chart = self.c._load_chart(pid)
        r = self._release_view(pid, self._find(chart.get("releases", []), rid, "release"))
        lab, loc = self.ps._labels(user)
        prof = self.ps._ledger().get("profile") or {}
        info = [lab("pm.care.rinfo." + x) for x in r["information"]] + ([r["information_other"]]
                                                                        if r.get("information_other") else [])
        L = [(prof.get("practice_name") or self.H.APP_NAME, False), (lab("pm.care.rel.pdf_title"), True),
             (self._client_line(lab, chart), False),
             (lab("pm.care.rdir." + r["direction"]) + ": " + r["party"], True)]
        if r.get("party_contact"):
            L.append((r["party_contact"], False))
        L += [(lab("pm.care.rel.information") + ": " + "; ".join(info), False),
              (lab("pm.care.rel.purpose") + ": " + r["purpose"], False),
              (lab("pm.care.rel.expires") + ": " + (r.get("expires") or r.get("expires_event") or "-"), False),
              (lab("pm.care.rel.revoke_note"), False),
              (lab("pm.care.rel.signed_line", name=r["signer_name"], role=lab("pm.srole." + r["signer_role"]),
                   date=r["signed_date"], witness=r.get("signed_by_name") or r["signed_by"],
                   code=r["content_sha"][:12]), False)]
        if not r["signature_valid"]:
            L.append((lab("app.chip.sig_invalid"), True))
        if r["state"] != "active":
            L.append((lab("pm.care.rstate." + r["state"]) + (" - " + r.get("revoked_reason", "")
                                                              if r["state"] == "revoked" else ""), True))
        self.c._commit({}, user["id"], "release.print", pid, {"release": rid})
        data, ext = self._pdf(lab("pm.care.rel.pdf_title"), L, loc, chart)
        return ("file", "release-%s.%s" % (rid, ext), data)

    def disclosure_add(self, user, pid, b):
        self.c.require(user, "release.write")
        if not isinstance(b, dict):
            raise PracticeError(400, "disclosure required")
        d = {"date": self._day(b.get("date"), "disclosure date", required=True, not_future=True),
             "recipient": self._text(b.get("recipient"), 200, "recipient"),
             "recipient_contact": self._text(b.get("recipient_contact"), 300, "recipient contact", multiline=True),
             "information": self._text(b.get("information"), 500, "information disclosed", multiline=True),
             "purpose": self._text(b.get("purpose"), 300, "purpose"),
             "basis": self._choice(b.get("basis"), DISCLOSURE_BASES, "basis"),
             "method": self._choice(b.get("method"), DISCLOSURE_METHODS, "method", "other"),
             "release": str(b.get("release") or "") or None}
        if not d["recipient"] or not d["information"]:
            raise PracticeError(400, "Name the recipient and what was disclosed.")
        with self.store.lock:
            chart = self.c._load_chart(pid)
            if d["basis"] == "authorization":
                r = next((x for x in chart.get("releases", []) if x["id"] == d["release"]), None)
                if r is None:
                    raise PracticeError(400, "Choose the signed release this disclosure was made under.")
                allowed = (r["direction"] != "obtain_from" and r["status"] in ("active", "revoked")
                           and r["signed_date"] <= d["date"] and (not r.get("expires") or d["date"] <= r["expires"])
                           and (r["status"] != "revoked" or d["date"] < r["revoked_date"]))
                if not allowed:                     # a disclosure dated before a revocation may still be logged
                    raise PracticeError(409, "That release did not allow a disclosure on this date.")
            else:
                d["release"] = None
            d.update(id=self.H.new_id("X"), status="active", by=user["id"], by_name=user.get("display", ""),
                     at=self.H.now_iso())
            chart.setdefault("disclosures", []).append(d)
            self.c._save_chart(chart, user["id"], "disclosure.add", {"disclosure": d["id"], "basis": d["basis"]},
                               index=False)
        return d

    def disclosure_error(self, user, pid, did, reason):
        self.c.require(user, "release.write")
        reason = self._text(reason, 300, "reason")
        if not reason:
            raise PracticeError(400, "A reason is required.")
        with self.store.lock:
            chart = self.c._load_chart(pid)
            d = self._find(chart.get("disclosures", []), did, "disclosure")
            if d["status"] == "entered_in_error":
                raise PracticeError(409, "Already marked entered in error.")
            d.update(status="entered_in_error", error_reason=reason, error_by=user["id"], error_at=self.H.now_iso())
            self.c._save_chart(chart, user["id"], "disclosure.entered_in_error", {"disclosure": did}, index=False)
        return d

    def accounting(self, user, pid, since=None, everything=False):
        """Disclosures on or after `since` (default 6 years back). By default the bases an accounting usually leaves
        out (authorization, the client's own request, treatment, payment, operations) are left out; everything=True
        lists every active entry."""
        self.c.require(user, "release.write")
        chart = self.c._load_chart(pid)
        since = self._day(since, "from") or (date.today() - timedelta(days=6 * 365 + 1)).isoformat()
        rows = [d for d in chart.get("disclosures", []) if d["status"] == "active" and d["date"] >= since
                and (everything or d["basis"] not in ACCOUNTING_LEFT_OUT)]
        rows.sort(key=lambda d: (d["date"], d["at"]))
        return chart, since, rows

    def accounting_pdf(self, user, pid, since=None, everything=False):
        chart, since, rows = self.accounting(user, pid, since, str(everything).lower() in ("1", "true", "yes"))
        lab, loc = self.ps._labels(user)
        prof = self.ps._ledger().get("profile") or {}
        L = [(prof.get("practice_name") or self.H.APP_NAME, False), (lab("pm.care.disc.pdf_title"), True),
             (self._client_line(lab, chart), False), (lab("pm.care.disc.pdf_period", since=since,
                                                          until=self._today()), False)]
        if not str(everything).lower() in ("1", "true", "yes"):
            L.append((lab("pm.care.disc.pdf_left_out"), False))
        for d in rows:
            L.append(("%s - %s" % (d["date"], d["recipient"]), True))
            for key, val in (("pm.care.disc.information", d["information"]), ("pm.care.disc.purpose", d["purpose"]),
                             ("pm.care.disc.basis", lab("pm.care.dbasis." + d["basis"])),
                             ("pm.care.disc.method", lab("pm.care.dmethod." + d["method"]))):
                if val:
                    L.append(("%s: %s" % (lab(key), val), False))
            if d.get("recipient_contact"):
                L.append((d["recipient_contact"], False))
        if not rows:
            L.append((lab("pm.care.disc.none"), False))
        self.c._commit({}, user["id"], "disclosure.accounting", pid, {"since": since, "count": len(rows)})
        data, ext = self._pdf(lab("pm.care.disc.pdf_title"), L, loc, chart)
        return ("file", "disclosures-%s.%s" % (pid, ext), data)

    # ==================================================================================================
    # Episodes of care: discharge summary (PIN-signed) and reopening
    # ==================================================================================================
    def _episodes(self, chart):
        eps = chart.setdefault("episodes", [])
        if not eps:
            eps.append({"id": self.H.new_id("Z"), "opened": local_day(chart.get("created") or self.H.now_iso()),
                        "opened_by": chart.get("created_by"), "status": "open", "discharge": None})
        return eps

    def _episode_view(self, pid, e, clinical=True):
        out = {k: e.get(k) for k in ("id", "opened", "status", "closed", "reopen_reason")}
        if clinical and e.get("discharge"):
            dsc = dict(e["discharge"])
            dsc["signature_valid"] = self._valid("discharge", pid, dict(dsc, id=e["id"]), DISCHARGE_SEALED)
            out["discharge"] = dsc
        return out

    def episodes(self, user, pid):
        self._req(user, "clin.read", "sched.read")
        clinical = "clin.read" in self.c.perms(user)
        chart = self.c._load_chart(pid)
        eps = chart.get("episodes") or [{"id": None, "opened": local_day(chart.get("created")), "status": "open"}]
        cur = eps[-1]["status"]
        return {"status": "discharged" if cur == "discharged" else "active",
                "episodes": [self._episode_view(pid, e, clinical) for e in reversed(eps)],
                "reasons": list(DISCHARGE_REASONS)}

    def discharge_save(self, user, pid, b):
        self.c.require(user, "plan.write")
        if not isinstance(b, dict):
            raise PracticeError(400, "discharge summary required")
        f = {"closed": self._day(b.get("closed"), "discharge date", not_future=True) or self._today(),
             "reason": self._choice(b.get("reason"), DISCHARGE_REASONS, "reason"),
             "summary": self._text(b.get("summary"), 10000, "summary", multiline=True),
             "recommendations": self._text(b.get("recommendations"), 3000, "recommendations", multiline=True),
             "referrals": self._text(b.get("referrals"), 2000, "referrals", multiline=True)}
        if not f["summary"]:
            raise PracticeError(400, "Write the discharge summary.")
        now = self.H.now_iso()
        with self.store.lock:
            chart = self.c._load_chart(pid)
            e = self._episodes(chart)[-1]
            if e["status"] != "open":
                raise PracticeError(409, "This client is already discharged. Reopen care first.")
            if f["closed"] < e["opened"]:
                raise PracticeError(400, "The discharge date is before this episode began.")
            dsc = e.get("discharge")
            if dsc and dsc["author"] != user["id"]:
                raise PracticeError(403, "Only the author can change this draft.")
            if dsc:
                dsc.update(f, updated=now)
            else:
                e["discharge"] = dict(f, author=user["id"], author_name=user.get("display", ""), created=now,
                                      updated=now, opened=e["opened"], outcomes={}, signed_by=None,
                                      signed_by_name=None, signed_at=None, content_sha=None, signature=None)
            self.c._save_chart(chart, user["id"], "discharge.draft", {"episode": e["id"]}, index=False)
        return self._episode_view(pid, e)

    def discharge_sign(self, user, pid, pin):
        self.c.require(user, "plan.write")
        with self.store.lock:
            chart = self.c._load_chart(pid)
            e = (chart.get("episodes") or [None])[-1]
            if not e or e["status"] != "open" or not e.get("discharge"):
                raise PracticeError(409, "Write the discharge summary first.")
            if e["discharge"]["author"] != user["id"]:
                raise PracticeError(403, "Only the author can sign this discharge summary.")
            self.c.verify_pin(user, pin)
            chart = self.c._load_chart(pid)
            e = chart["episodes"][-1]
            dsc = e["discharge"]
            outs = {}
            for k in ps.INSTRUMENTS:
                o = outcome(k, [m for m in chart.get("measures", []) if m["date"] >= e["opened"]])
                if o:
                    outs[k] = o
            dsc.update(outcomes=outs, opened=e["opened"], signed_by=user["id"],
                       signed_by_name=user.get("display", ""), signed_at=self.H.now_iso())
            dsc["content_sha"], dsc["signature"] = self._seal("discharge", pid, dict(dsc, id=e["id"]),
                                                              DISCHARGE_SEALED)
            e.update(status="discharged", closed=dsc["closed"])
            idx = self._index()
            idx["discharged"][pid] = {"date": dsc["closed"], "episode": e["id"]}
            self.c._save_chart(chart, user["id"], "discharge.sign", {"episode": e["id"],
                                                                     "content_sha": dsc["content_sha"]},
                               extra_files={CARE_INDEX: self.c.codec.dump(idx)}, index=False)
        return self._episode_view(pid, e)

    def reopen(self, user, pid, reason, day=None):
        self._req(user, "plan.write", "sched.write")
        reason = self._text(reason, 300, "reason")
        if not reason:
            raise PracticeError(400, "A reason is required.")
        day = self._day(day, "date", not_future=True) or self._today()
        with self.store.lock:
            chart = self.c._load_chart(pid)
            eps = self._episodes(chart)
            if eps[-1]["status"] != "discharged":
                raise PracticeError(409, "This client's care is already open.")
            if day < eps[-1]["closed"]:
                raise PracticeError(400, "Care cannot reopen before the last discharge date.")
            e = {"id": self.H.new_id("Z"), "opened": day, "opened_by": user["id"], "status": "open",
                 "discharge": None, "reopen_reason": reason}
            eps.append(e)
            idx = self._index()
            idx["discharged"].pop(pid, None)
            self.c._save_chart(chart, user["id"], "episode.reopen", {"episode": e["id"]},
                               extra_files={CARE_INDEX: self.c.codec.dump(idx)}, index=False)
        return self._episode_view(pid, e, "clin.read" in self.c.perms(user))

    def discharge_pdf(self, user, pid, eid):
        self.c.require(user, "clin.read")
        chart = self.c._load_chart(pid)
        e = self._find(chart.get("episodes", []), eid, "episode")
        dsc = e.get("discharge")
        if not dsc or not dsc.get("signature"):
            raise PracticeError(409, "Sign the discharge summary first.")
        lab, loc = self.ps._labels(user)
        prof = self.ps._ledger().get("profile") or {}
        L = [(prof.get("practice_name") or self.H.APP_NAME, False), (lab("pm.care.dis.title"), True),
             (self._client_line(lab, chart), False),
             (lab("pm.care.dis.period", opened=e["opened"], closed=dsc["closed"]), False),
             (lab("pm.care.dis.reason") + ": " + lab("pm.care.dreason." + dsc["reason"]), False),
             (lab("pm.care.dis.summary"), True), (dsc["summary"], False)]
        if dsc["outcomes"]:
            L.append((lab("pm.care.dis.outcomes"), True))
            for k, o in dsc["outcomes"].items():
                name = ps.INSTRUMENTS[k]["name"] if k in ps.INSTRUMENTS else k
                L.append((lab("pm.care.out.line", name=name, first=o["first"]["total"], fdate=o["first"]["date"],
                              last=o["latest"]["total"], ldate=o["latest"]["date"], n=o["count"]), False))
        for key in ("recommendations", "referrals"):
            if dsc.get(key):
                L += [(lab("pm.care.dis." + key), True), (dsc[key], False)]
        L.append((lab("pm.care.signed_line", name=dsc.get("signed_by_name") or dsc["signed_by"], when=dsc["signed_at"],
                      code=dsc["content_sha"][:12]), False))
        if not self._valid("discharge", pid, dict(dsc, id=e["id"]), DISCHARGE_SEALED):
            L.append((lab("app.chip.sig_invalid"), True))
        self.c._commit({}, user["id"], "discharge.print", pid, {"episode": eid})
        data, ext = self._pdf(lab("pm.care.dis.title"), L, loc, chart)
        return ("file", "discharge-%s.%s" % (pid, ext), data)

    # ==================================================================================================
    # Payer authorizations
    # ==================================================================================================
    def _auths(self):
        return self.store.read_obj(AUTHS, {"auths": {}})

    def _auth_usage(self, a, appts, today):
        win = [x for x in appts if a["start"] <= x["date"] <= a["end"]]
        attended = sum(1 for x in win if x["status"] in ATTENDED)
        booked = sum(1 for x in win if x["status"] == "scheduled")
        after = sum(1 for x in appts if x["status"] == "scheduled" and x["date"] > a["end"])
        remaining = a["visits"] - a["used_before"] - attended
        alerts = []
        if a["status"] == "active" and a["end"] < today:
            alerts.append("expired")                  # a new authorization is needed; the other alerts follow from it
        elif a["status"] == "active":
            if (date.fromisoformat(a["end"]) - date.fromisoformat(today)).days <= AUTH_SOON_DAYS:
                alerts.append("ends_soon")
            if remaining <= 0:
                alerts.append("used_up")
            elif remaining <= AUTH_LOW:
                alerts.append("low")
            if booked > max(remaining, 0):
                alerts.append("overbooked")
            if after:
                alerts.append("booked_after_end")
        return dict(a, attended=attended, booked=booked, booked_after_end=after, remaining=remaining, alerts=alerts)

    def _auth_reader(self, user):
        self._req(user, "bill.read", "sched.read")

    def auths_patient(self, user, pid):
        self._auth_reader(user)
        self.ps._patient(pid)
        appts, today = self._patient_appts(pid), self._today()
        rows = [self._auth_usage(a, appts, today) for a in self._auths()["auths"].values() if a["patient"] == pid]
        rows.sort(key=lambda a: (a["status"] != "active", a["end"]), reverse=False)
        return {"auths": rows}

    def auth_save(self, user, pid, b, auth_id=None):
        self.c.require(user, "bill.write")
        self.ps._patient(pid)
        if not isinstance(b, dict):
            raise PracticeError(400, "authorization required")
        codes = b.get("codes") or []
        if isinstance(codes, str):
            codes = [x for x in re.split(r"[\s,;]+", codes) if x]
        if not isinstance(codes, list) or len(codes) > 10:
            raise PracticeError(400, "At most 10 service codes.")
        f = {"payer": self._text(b.get("payer"), 120, "payer"), "number": self._text(b.get("number"), 40, "number"),
             "codes": [ps.norm_code(x) for x in codes],
             "visits": self._int(b.get("visits"), 1, MAX_AUTH_VISITS, "Visits authorized"),
             "used_before": self._int(b.get("used_before") or 0, 0, MAX_AUTH_VISITS, "Visits used before"),
             "start": self._day(b.get("start"), "start date", required=True),
             "end": self._day(b.get("end"), "end date", required=True),
             "notes": self._text(b.get("notes"), 500, "notes", multiline=True)}
        if not f["payer"] or not f["number"]:
            raise PracticeError(400, "The payer and the authorization number are required.")
        if f["end"] < f["start"]:
            raise PracticeError(400, "The end date is before the start date.")
        now = self.H.now_iso()
        with self.store.lock:
            db = self._auths()
            if auth_id:
                a = db["auths"].get(str(auth_id))
                if a is None or a["patient"] != pid:
                    raise PracticeError(404, "No such authorization.")
                a["history"].append({k: a[k] for k in f})
                a.update(f, updated=now, updated_by=user["id"])
                action = "auth.update"
            else:
                a = dict(f, id=self.H.new_id("C"), patient=pid, status="active", created=now, created_by=user["id"],
                         created_by_name=user.get("display", ""), updated=now, updated_by=user["id"], history=[])
                db["auths"][a["id"]] = a
                action = "auth.create"
            self.c._commit({AUTHS: self.c.codec.dump(db)}, user["id"], action, pid,
                           {"auth": a["id"], "visits": a["visits"]})
        return self._auth_usage(a, self._patient_appts(pid), self._today())

    def auth_close(self, user, auth_id, reason):
        self.c.require(user, "bill.write")
        reason = self._text(reason, 200, "reason")
        if not reason:
            raise PracticeError(400, "A reason is required.")
        with self.store.lock:
            db = self._auths()
            a = db["auths"].get(str(auth_id or ""))
            if a is None:
                raise PracticeError(404, "No such authorization.")
            if a["status"] != "active":
                raise PracticeError(409, "This authorization is already closed.")
            a.update(status="closed", closed_reason=reason, closed_by=user["id"], closed_at=self.H.now_iso())
            self.c._commit({AUTHS: self.c.codec.dump(db)}, user["id"], "auth.close", a["patient"], {"auth": a["id"]})
        return a

    def auth_alerts(self, user):
        self._auth_reader(user)
        today, out = self._today(), []
        gone = self._index()["discharged"]
        by = {}
        for a in self._auths()["auths"].values():
            if a["status"] == "active" and a["patient"] not in gone:
                by.setdefault(a["patient"], []).append(a)
        for pid, auths in by.items():
            appts = self._patient_appts(pid)
            for a in auths:
                u = self._auth_usage(a, appts, today)
                if u["alerts"]:
                    out.append(dict(u, patient_name=self._pname(pid)))
        out.sort(key=lambda a: (a["end"], a["patient_name"].lower()))
        return out

    # ==================================================================================================
    # Good Faith Estimates (US No Surprises Act)
    # ==================================================================================================
    def _estimates(self):
        return self.store.read_obj(ESTIMATES, {"seq": 0, "estimates": {}, "notice": "", "reminders": None})

    def _gfe_settings(self, db=None):
        db = db or self._estimates()
        rem = db.get("reminders")
        if rem is None:
            rem = (self.c.locale_settings().get("region") or "").upper() == "US"
        return {"notice": db.get("notice") or GFE_NOTICE_DEFAULT, "notice_is_default": not db.get("notice"),
                "reminders": bool(rem)}

    def gfe_settings(self, user):
        self.c.require(user, "bill.read")
        return self._gfe_settings()

    def gfe_settings_save(self, user, notice=None, reminders=None):
        self.c.require(user, "bill.write")
        with self.store.lock:
            db = self._estimates()
            if notice is not None:
                notice = self._text(notice, 4000, "notice", multiline=True)
                db["notice"] = "" if notice == GFE_NOTICE_DEFAULT else notice
            if reminders is not None:
                db["reminders"] = bool(reminders)
            self.c._commit({ESTIMATES: self.c.codec.dump(db)}, user["id"], "gfe.settings", "practice",
                           {"custom_notice": bool(db["notice"]), "reminders": db.get("reminders")})
        return self._gfe_settings(db)

    def _gfe_view(self, g, invoices=None):
        g = dict(g)
        g["signature_valid"] = self._valid("gfe", g["patient"], g, GFE_SEALED)
        if invoices is not None and g["status"] == "issued":
            billed = sum(i["total"] for i in invoices if i["status"] == "issued" and i["currency"] == g["currency"]
                         and g["period_start"] <= i["date"] <= g["period_end"])
            g["billed"] = billed
            g["over"] = billed - g["total"]
            g["dispute_risk"] = g["currency"] == "USD" and g["over"] >= GFE_DISPUTE_CENTS
        return g

    def gfe_list(self, user, pid):
        self.c.require(user, "bill.read")
        self.ps._patient(pid)
        inv = [i for i in self.ps._ledger()["invoices"].values() if i["patient"] == pid]
        rows = [self._gfe_view(g, inv) for g in self._estimates()["estimates"].values() if g["patient"] == pid]
        rows.sort(key=lambda g: g["created"], reverse=True)
        return {"estimates": rows, "settings": self._gfe_settings()}

    def gfe_save(self, user, pid, b, gfe_id=None):
        self.c.require(user, "bill.write")
        self.ps._patient(pid)
        if not isinstance(b, dict):
            raise PracticeError(400, "estimate required")
        start = self._day(b.get("period_start"), "period start", required=True)
        end = self._day(b.get("period_end"), "period end", required=True)
        if end < start or (date.fromisoformat(end) - date.fromisoformat(start)).days > GFE_MAX_DAYS:
            raise PracticeError(400, "The period must end after it starts and cover at most 12 months.")
        lines = []
        for ln in b.get("lines") or []:
            if not isinstance(ln, dict) or not str(ln.get("code") or "").strip():
                continue
            lines.append({"code": ps.norm_code(ln.get("code")),
                          "description": self._text(ln.get("description"), 120, "description"),
                          "quantity": self._int(1 if ln.get("quantity") in (None, "") else ln.get("quantity"), 1, 99,
                                                "Quantity"),
                          "fee": ps.cents(ln.get("fee"), "fee")})
        if not lines or len(lines) > ps.MAX_LINES:
            raise PracticeError(400, "Add 1 to %d expected services." % ps.MAX_LINES)
        dx = []
        for d in (b.get("diagnoses") or [])[:ps.MAX_DX + 1]:
            if str(d or "").strip():
                dx.append(ps.norm_icd10(d))
        if len(dx) > ps.MAX_DX:
            raise PracticeError(400, "At most %d diagnosis codes." % ps.MAX_DX)
        provider = str(b.get("provider") or "")
        if provider and provider not in self.c._users()["users"]:
            raise PracticeError(400, "Unknown provider.")
        L = self.ps._ledger()
        cur = str(b.get("currency") or (L.get("profile") or {}).get("currency") or "USD").upper()
        if not ps.CURRENCY_RE.match(cur):
            raise PracticeError(400, "Currency must be a 3-letter ISO code (for example USD).")
        f = {"period_start": start, "period_end": end, "lines": lines, "diagnoses": dx, "provider": provider,
             "currency": cur, "notes": self._text(b.get("notes"), 1000, "notes", multiline=True),
             "total": sum(ln["quantity"] * ln["fee"] for ln in lines)}
        now = self.H.now_iso()
        with self.store.lock:
            db = self._estimates()
            if gfe_id:
                g = db["estimates"].get(str(gfe_id))
                if g is None or g["patient"] != pid:
                    raise PracticeError(404, "No such estimate.")
                if g["status"] != "draft":
                    raise PracticeError(423, "An issued estimate cannot be changed. Void it and write a new one.")
                g.update(f, updated=now)
                action = "gfe.update"
            else:
                g = dict(f, id=self.H.new_id("F"), patient=pid, number=None, status="draft", created=now,
                         created_by=user["id"], created_by_name=user.get("display", ""), updated=now, notice=None,
                         issued_at=None, issued_by=None, signed_by=None, signed_at=None, content_sha=None,
                         signature=None)
                db["estimates"][g["id"]] = g
                action = "gfe.create"
            self.c._commit({ESTIMATES: self.c.codec.dump(db)}, user["id"], action, pid,
                           {"estimate": g["id"], "total": g["total"]})
        return self._gfe_view(g)

    def gfe_issue(self, user, gfe_id):
        self.c.require(user, "bill.write")
        with self.store.lock:
            db = self._estimates()
            g = db["estimates"].get(str(gfe_id or ""))
            if g is None:
                raise PracticeError(404, "No such estimate.")
            if g["status"] != "draft":
                raise PracticeError(409, "Only a draft can be issued.")
            db["seq"] = int(db.get("seq") or 0) + 1
            now = self.H.now_iso()
            g.update(status="issued", number="GFE-%05d" % db["seq"], issued_at=now, issued_by=user["id"],
                     issued_by_name=user.get("display", ""), notice=self._gfe_settings(db)["notice"],
                     signed_by=user["id"], signed_at=now)
            g["content_sha"], g["signature"] = self._seal("gfe", g["patient"], g, GFE_SEALED)
            self.c._commit({ESTIMATES: self.c.codec.dump(db)}, user["id"], "gfe.issue", g["patient"],
                           {"estimate": g["id"], "number": g["number"], "total": g["total"]})
        return self._gfe_view(g)

    def gfe_void(self, user, gfe_id, reason):
        self.c.require(user, "bill.write")
        reason = self._text(reason, 200, "reason")
        if not reason:
            raise PracticeError(400, "A reason is required.")
        with self.store.lock:
            db = self._estimates()
            g = db["estimates"].get(str(gfe_id or ""))
            if g is None:
                raise PracticeError(404, "No such estimate.")
            if g["status"] == "void":
                raise PracticeError(409, "Already void.")
            g.update(status="void", void_reason=reason, voided_at=self.H.now_iso(), voided_by=user["id"])
            self.c._commit({ESTIMATES: self.c.codec.dump(db)}, user["id"], "gfe.void", g["patient"],
                           {"estimate": g["id"]})
        return self._gfe_view(g)

    def gfe_pdf(self, user, gfe_id):
        self.c.require(user, "bill.read")
        g = self._estimates()["estimates"].get(str(gfe_id or ""))
        if g is None:
            raise PracticeError(404, "No such estimate.")
        if g["status"] == "draft":
            raise PracticeError(409, "Issue the estimate first.")
        chart = self.c._load_chart(g["patient"])
        d = chart["demographics"]
        lab, loc = self.ps._labels(user)
        L0 = self.ps._ledger()
        prof = L0.get("profile") or {}
        prov = (L0.get("providers") or {}).get(g["provider"]) or {}
        names = self.ps._names()
        L = [(lab("pm.care.gfe.pdf_title"), True),
             (lab("pm.care.gfe.pdf_number", number=g["number"], date=local_day(g["issued_at"])), False),
             (lab("pm.care.gfe.pdf_client"), True),
             ("%s   %s: %s" % (d.get("name", ""), lab("app.field.dob"), d.get("dob", "")), False)]
        if d.get("address"):
            L.append((d["address"], False))
        L.append((lab("pm.care.gfe.pdf_provider"), True))
        L.append((" | ".join(s for s in (prof.get("practice_name"), prof.get("address"), prof.get("phone"),
                                         prof.get("email")) if s) or "-", False))
        ids = ["NPI %s" % s for s in (prov.get("npi") or prof.get("npi"),) if s]
        if prof.get("tax_id"):
            ids.append("TIN %s" % prof["tax_id"])
        if g.get("provider"):
            ids.insert(0, names.get(g["provider"], g["provider"]))
        if ids:
            L.append((" | ".join(ids), False))
        L.append((lab("pm.care.gfe.pdf_period", start=g["period_start"], end=g["period_end"]), False))
        if g["diagnoses"]:
            L.append(("ICD-10: " + ", ".join(g["diagnoses"]), False))
        L.append((lab("pm.care.gfe.pdf_services"), True))
        for ln in g["lines"]:
            L.append(("%s %s - %d x %s = %s" % (ln["code"], ln.get("description") or "", ln["quantity"],
                                                ps.money_text(ln["fee"], g["currency"]),
                                                ps.money_text(ln["quantity"] * ln["fee"], g["currency"])), False))
        L.append((lab("pm.care.gfe.pdf_total", total=ps.money_text(g["total"], g["currency"])), True))
        if g.get("notes"):
            L.append((g["notes"], False))
        L.append((g.get("notice") or GFE_NOTICE_DEFAULT, False))
        L.append((lab("pm.care.gfe.pdf_seal", code=g["content_sha"][:12]), False))
        if not self._valid("gfe", g["patient"], g, GFE_SEALED):
            L.append((lab("app.chip.sig_invalid"), True))
        if g["status"] == "void":
            L.append((lab("pm.care.gfe.void_line", reason=g.get("void_reason", "")), True))
        self.c._commit({}, user["id"], "gfe.print", g["patient"], {"estimate": g["id"]})
        data, ext = self._pdf(lab("pm.care.gfe.pdf_title"), L, loc, chart)
        return ("file", "good-faith-estimate-%s.%s" % (g["number"], ext), data)

    def gfe_missing(self, user):
        """Self-pay clients (no coverage on file) with a session in the next 30 days that no issued estimate covers."""
        self.c.require(user, "bill.read")
        db = self._estimates()
        if not self._gfe_settings(db)["reminders"]:
            return []
        today = self._today()
        until = (date.today() + timedelta(days=GFE_LOOKAHEAD_DAYS)).isoformat()
        cov = self.ps._ledger().get("coverage") or {}
        gone = self._index()["discharged"]
        issued = {}
        for g in db["estimates"].values():
            if g["status"] == "issued":
                issued.setdefault(g["patient"], []).append((g["period_start"], g["period_end"]))
        out = {}
        for a in self.ps._all_appts():
            pid = a["patient"]
            if a["status"] != "scheduled" or not today <= a["date"] <= until or pid in gone:
                continue
            if (cov.get(pid) or {}).get("payer_name") or (cov.get(pid) or {}).get("member_id"):
                continue
            if any(s <= a["date"] <= e for s, e in issued.get(pid, [])):
                continue
            if pid not in out or a["date"] < out[pid]["date"]:
                out[pid] = {"patient": pid, "patient_name": self._pname(pid), "date": a["date"]}
        return sorted(out.values(), key=lambda r: (r["date"], r["patient_name"].lower()))

    # ==================================================================================================
    # Client statements and accounts-receivable aging
    # ==================================================================================================
    def aging(self, user, as_of=None):
        self.c.require(user, "bill.read")
        as_of = self._day(as_of, "as of") or self._today()
        L = self.ps._ledger()
        by = {}
        for i in L["invoices"].values():
            by.setdefault(i["patient"], ([], []))[0].append(i)
        for y in L["payments"].values():
            by.setdefault(y["patient"], ([], []))[1].append(y)
        rows, totals = [], {}
        for pid, (inv, pays) in by.items():
            for cur, r in aging_rows(inv, pays, as_of).items():
                rows.append(dict(r, patient=pid, patient_name=self._pname(pid), currency=cur))
                t = totals.setdefault(cur, {"buckets": {b: 0 for b in AGING_BUCKETS}, "credit": 0, "total": 0})
                for b in AGING_BUCKETS:
                    t["buckets"][b] += r["buckets"][b]
                t["credit"] += r["credit"]
                t["total"] += r["total"]
        rows.sort(key=lambda r: (-r["buckets"]["91_plus"], -r["total"], r["patient_name"].lower()))
        return {"as_of": as_of, "rows": rows, "totals": totals, "buckets": list(AGING_BUCKETS)}

    def aging_csv(self, user, as_of=None):
        a = self.aging(user, as_of)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["client", "client_id", "currency", "0-30", "31-60", "61-90", "91+", "credit", "balance"])
        money = lambda c: "%s%d.%02d" % ("-" if c < 0 else "", abs(c) // 100, abs(c) % 100)  # noqa: E731
        for r in a["rows"]:
            w.writerow([ps._csv_cell(r["patient_name"]), r["patient"], r["currency"]]
                       + [money(r["buckets"][b]) for b in AGING_BUCKETS] + [money(r["credit"]), money(r["total"])])
        self.c._commit({}, user["id"], "aging.export", "practice", {"as_of": a["as_of"], "rows": len(a["rows"])})
        return ("file", "ar-aging-%s.csv" % a["as_of"], ("﻿" + buf.getvalue()).encode("utf-8"))

    def statement(self, user, pid, as_of=None):
        self.c.require(user, "bill.read")
        as_of = self._day(as_of, "as of") or self._today()
        chart = self.c._load_chart(pid)
        d = chart["demographics"]
        L0 = self.ps._ledger()
        prof = L0.get("profile") or {}
        invoices = [i for i in L0["invoices"].values() if i["patient"] == pid]
        payments = [y for y in L0["payments"].values() if y["patient"] == pid]
        inv, open_, loose = open_items(invoices, payments, as_of)
        aging = aging_rows(invoices, payments, as_of)
        lab, loc = self.ps._labels(user)
        L = [(prof.get("practice_name") or self.H.APP_NAME, True),
             (" | ".join(s for s in (prof.get("address"), prof.get("phone"), prof.get("email")) if s), False),
             (lab("pm.care.st.title"), True), (lab("pm.care.st.as_of", date=as_of), False),
             (d.get("name", ""), False)]
        if d.get("address"):
            L.append((d["address"], False))
        L.append((lab("pm.care.st.invoices"), True))
        for i in inv:
            paid = i["total"] - open_[i["id"]]
            L.append(("%s  %s  %s: %s  %s: %s  %s: %s" % (
                i["date"], i.get("number") or i["id"], lab("pm.care.st.charged"), ps.money_text(i["total"], i["currency"]),
                lab("pm.care.st.paid"), ps.money_text(paid, i["currency"]), lab("pm.care.st.open"),
                ps.money_text(open_[i["id"]], i["currency"])), False))
        if not inv:
            L.append(("-", False))
        L.append((lab("pm.care.st.payments"), True))
        shown = 0
        for y in sorted(payments, key=lambda y: (y["date"], y["at"])):
            if y["status"] != "active" or y["date"] > as_of:
                continue
            shown += 1
            L.append(("%s  %s  %s %s" % (y["date"], lab("pm.care.st.k_" + y["kind"]),
                                         ps.money_text(y["amount"], y["currency"]), y.get("reference") or ""), False))
        if not shown:
            L.append(("-", False))
        for cur, r in sorted(aging.items()):
            L.append((lab("pm.care.st.due", total=ps.money_text(r["total"], cur)), True))
            L.append((" | ".join("%s: %s" % (lab("pm.care.aging.b_" + b), ps.money_text(r["buckets"][b], cur))
                                 for b in AGING_BUCKETS)
                      + (" | %s: %s" % (lab("pm.care.aging.credit"), ps.money_text(r["credit"], cur))
                         if r["credit"] else ""), False))
        if not aging:
            L.append((lab("pm.care.st.nothing_due"), True))
        if prof.get("footer"):
            L.append((prof["footer"], False))
        self.c._commit({}, user["id"], "statement.print", pid, {"as_of": as_of})
        data, ext = self._pdf(lab("pm.care.st.title"), L, loc, chart)
        return ("file", "statement-%s-%s.%s" % (pid, as_of, ext), data)

    # ==================================================================================================
    # To-do: documentation and billing gaps, measures due, authorization and estimate alerts
    # ==================================================================================================
    @staticmethod
    def _note_days(chart):
        """{date: [note statuses]} with a note's date = its encounter date (else the day it was written)."""
        enc = {e["id"]: e["date"] for e in chart.get("encounters", [])}
        out = {}
        for n in chart.get("notes", []):
            if n.get("status") == "entered_in_error":
                continue
            d = enc.get(n.get("encounter_id")) or local_day(n.get("created"))
            out.setdefault(d, []).append(n)
        return out

    def gaps(self, user, start=None, end=None, clinician=""):
        self.c.require(user, "sched.read")
        perms = self.c.perms(user)
        end = self._day(end, "to") or self._today()
        start = self._day(start, "from") or (date.fromisoformat(end) - timedelta(days=GAP_DEFAULT_DAYS)).isoformat()
        if start > end or (date.fromisoformat(end) - date.fromisoformat(start)).days > GAP_MAX_DAYS:
            raise PracticeError(400, "Choose a period of at most a year, start before end.")
        clinician = str(clinician or "")
        appts = [a for a in self.ps._with_names(self.ps._all_appts())
                 if start <= a["date"] <= end and a["status"] in ATTENDED and a["date"] <= self._today()
                 and (not clinician or a["clinician"] == clinician)]
        appts.sort(key=lambda a: (a["date"], a["time"]))
        out = {"from": start, "to": end, "attended": len(appts)}
        keep = ("id", "date", "time", "patient", "patient_name", "clinician", "clinician_name", "kind")
        if "clin.read" in perms:
            missing, unsigned, days = [], [], {}
            charts = {}
            for a in appts:
                if a["patient"] not in charts:
                    try:
                        charts[a["patient"]] = self._note_days(self.c._load_chart(a["patient"]))
                    except Exception:
                        charts[a["patient"]] = {}
                notes = charts[a["patient"]].get(a["date"], [])
                row = {k: a.get(k) for k in keep}
                if not notes:
                    missing.append(row)
                elif not any(n["status"] == "signed" for n in notes):
                    unsigned.append(row)
            within = {"1": 0, "3": 0, "later": 0}
            for nd in charts.values():
                for d0, notes in nd.items():
                    if not start <= d0 <= end:
                        continue
                    for n in notes:
                        if n["status"] != "signed" or not n.get("signed_at"):
                            continue
                        lag = (date.fromisoformat(local_day(n["signed_at"])) - date.fromisoformat(d0)).days
                        within["1" if lag <= 1 else "3" if lag <= 3 else "later"] += 1
                days = within
            out.update(missing_notes=missing, unsigned_notes=unsigned, timeliness=days)
        if "bill.read" in perms:
            billed = {}
            for i in self.ps._ledger()["invoices"].values():
                if i["status"] == "void":
                    continue
                for ln in i["lines"]:
                    billed.setdefault(i["patient"], set()).add(ln["dos"])
            out["unbilled"] = [{k: a.get(k) for k in keep} for a in appts
                               if a["date"] not in billed.get(a["patient"], ())]
        return out

    def todo(self, user):
        perms = self.c.perms(user)
        if not perms & {"sched.read", "clin.read", "bill.read"}:
            raise self.H.HttpError(403, "Your role does not allow this (sched.read).")
        out = {"today": self._today()}
        if "sched.read" in perms:
            out["gaps"] = self.gaps(user)
        if "clin.read" in perms:
            out["measures_due"] = self.mbc_due(user)["due"]
        if perms & {"bill.read", "sched.read"}:
            out["auth_alerts"] = self.auth_alerts(user)
        if "bill.read" in perms:
            out["gfe_missing"] = self.gfe_missing(user)
        out["discharged"] = len(self._index()["discharged"])
        self.c._commit({}, user["id"], "todo.view", "practice", {k: len(v) for k, v in out.items()
                                                                 if isinstance(v, list)})
        return out

    def subject_data(self, sid):
        """consent_center subject-access export: payer authorizations and Good Faith Estimates (kept outside the
        chart). Psychotherapy notes are not included: the author decides on their release separately."""
        if not isinstance(sid, str) or not sid.startswith("P"):
            return {}
        return {"authorizations": [a for a in self._auths()["auths"].values() if a["patient"] == sid],
                "good_faith_estimates": [g for g in self._estimates()["estimates"].values() if g["patient"] == sid]}

    # ==================================================================================================
    # Full-record export (practice_suite): seal checks and readable sections
    # ==================================================================================================
    def verification(self, pid, chart):
        return {"safety_plans": {p["id"]: self._valid("safety", pid, p, SAFETY_SEALED)
                                 for p in chart.get("safety_plans", []) if p.get("signature")},
                "releases": {r["id"]: self._valid("release", pid, r, RELEASE_SEALED)
                             for r in chart.get("releases", []) if r.get("signature")},
                "discharges": {e["id"]: self._valid("discharge", pid, dict(e["discharge"], id=e["id"]),
                                                    DISCHARGE_SEALED)
                               for e in chart.get("episodes", []) if (e.get("discharge") or {}).get("signature")}}

    def pdf_sections(self, pid, chart, lab):
        L = []
        sp = [p for p in chart.get("safety_plans", []) if p["status"] in ("signed", "superseded")]
        if sp:
            p = max(sp, key=lambda p: p["version"])
            L.append((lab("pm.care.safety.title") + " (v%d)" % p["version"], True))
            for k in SAFETY_SECTIONS:
                if p["sections"].get(k):
                    L.append(("%s: %s" % (lab("pm.care.safety.s_" + k), p["sections"][k]), False))
        rel = chart.get("releases", [])
        if rel:
            L.append((lab("pm.care.rel.title"), True))
            for r in rel:
                L.append(("%s %s - %s (%s)" % (r["signed_date"], lab("pm.care.rdir." + r["direction"]), r["party"],
                                               lab("pm.care.rstate." + self._release_state(r))), False))
        dis = [d for d in chart.get("disclosures", []) if d["status"] == "active"]
        if dis:
            L.append((lab("pm.care.disc.title"), True))
            for d in dis:
                L.append(("%s %s - %s (%s)" % (d["date"], d["recipient"], d["information"],
                                               lab("pm.care.dbasis." + d["basis"])), False))
        for e in chart.get("episodes", []):
            dsc = e.get("discharge") or {}
            if dsc.get("signature"):
                L.append((lab("pm.care.dis.title"), True))
                L.append((lab("pm.care.dis.period", opened=e["opened"], closed=dsc["closed"]) + " - "
                          + lab("pm.care.dreason." + dsc["reason"]), False))
                L.append((dsc["summary"], False))
        return L


# ======================================================================================================
# clinic_app mount
# ======================================================================================================
def _route_table():
    """(method, path) -> fn(care, user, query, body)."""
    def pid(q, b):
        return b.get("patient") or q.get("patient")

    return {
        ("GET", "/api/care/mbc"): lambda s, u, q, b: s.mbc_patient(u, pid(q, b)),
        ("POST", "/api/care/mbc"): lambda s, u, q, b: {"schedule": s.mbc_set(u, pid(q, b), b.get("instrument"),
                                                                            b.get("every_days"), b.get("start"))},
        ("POST", "/api/care/mbc/stop"): lambda s, u, q, b: {"schedule": s.mbc_stop(u, pid(q, b), b.get("schedule"))},
        ("GET", "/api/care/mbc/due"): lambda s, u, q, b: s.mbc_due(u, q.get("within")),
        ("GET", "/api/care/outcomes"): lambda s, u, q, b: s.outcomes(u, q.get("from"), q.get("to")),
        ("GET", "/api/care/psy"): lambda s, u, q, b: s.psy_list(u, pid(q, b)),
        ("POST", "/api/care/psy"): lambda s, u, q, b: {"note": s.psy_save(u, pid(q, b), b.get("note"),
                                                                         b.get("note_id"))},
        ("POST", "/api/care/psy/sign"): lambda s, u, q, b: {"note": s.psy_sign(u, pid(q, b), b.get("note_id"),
                                                                              b.get("pin"))},
        ("POST", "/api/care/psy/error"): lambda s, u, q, b: {"note": s.psy_error(u, pid(q, b), b.get("note_id"),
                                                                                b.get("reason"))},
        ("GET", "/api/care/psy/pdf"): lambda s, u, q, b: s.psy_pdf(u, pid(q, b)),
        ("GET", "/api/care/safety"): lambda s, u, q, b: s.safety(u, pid(q, b)),
        ("POST", "/api/care/safety"): lambda s, u, q, b: {"plan": s.safety_save(u, pid(q, b), b.get("plan"),
                                                                               b.get("plan_id"))},
        ("POST", "/api/care/safety/sign"): lambda s, u, q, b: {"plan": s.safety_sign(u, pid(q, b), b.get("plan_id"),
                                                                                    b.get("pin"))},
        ("POST", "/api/care/safety/error"): lambda s, u, q, b: {"plan": s.safety_error(u, pid(q, b), b.get("plan_id"),
                                                                                      b.get("reason"))},
        ("GET", "/api/care/safety/pdf"): lambda s, u, q, b: s.safety_pdf(u, pid(q, b), q.get("plan")),
        ("GET", "/api/care/releases"): lambda s, u, q, b: s.releases(u, pid(q, b)),
        ("POST", "/api/care/releases"): lambda s, u, q, b: {"release": s.release_add(u, pid(q, b), b.get("release"),
                                                                                    b.get("pin"))},
        ("POST", "/api/care/releases/revoke"): lambda s, u, q, b: {"release": s.release_revoke(
            u, pid(q, b), b.get("release_id"), b.get("reason"), b.get("date"))},
        ("POST", "/api/care/releases/error"): lambda s, u, q, b: {"release": s.release_error(
            u, pid(q, b), b.get("release_id"), b.get("reason"))},
        ("GET", "/api/care/releases/pdf"): lambda s, u, q, b: s.release_pdf(u, pid(q, b), q.get("release")),
        ("POST", "/api/care/disclosures"): lambda s, u, q, b: {"disclosure": s.disclosure_add(u, pid(q, b),
                                                                                             b.get("disclosure"))},
        ("POST", "/api/care/disclosures/error"): lambda s, u, q, b: {"disclosure": s.disclosure_error(
            u, pid(q, b), b.get("disclosure_id"), b.get("reason"))},
        ("GET", "/api/care/disclosures/pdf"): lambda s, u, q, b: s.accounting_pdf(u, pid(q, b), q.get("from"),
                                                                                q.get("all", "")),
        ("GET", "/api/care/episodes"): lambda s, u, q, b: s.episodes(u, pid(q, b)),
        ("POST", "/api/care/discharge"): lambda s, u, q, b: {"episode": s.discharge_save(u, pid(q, b),
                                                                                        b.get("discharge"))},
        ("POST", "/api/care/discharge/sign"): lambda s, u, q, b: {"episode": s.discharge_sign(u, pid(q, b),
                                                                                             b.get("pin"))},
        ("POST", "/api/care/reopen"): lambda s, u, q, b: {"episode": s.reopen(u, pid(q, b), b.get("reason"),
                                                                             b.get("date"))},
        ("GET", "/api/care/discharge/pdf"): lambda s, u, q, b: s.discharge_pdf(u, pid(q, b), q.get("episode")),
        ("GET", "/api/care/auths"): lambda s, u, q, b: s.auths_patient(u, pid(q, b)),
        ("POST", "/api/care/auths"): lambda s, u, q, b: {"auth": s.auth_save(u, pid(q, b), b.get("auth"),
                                                                            b.get("auth_id"))},
        ("POST", "/api/care/auths/close"): lambda s, u, q, b: {"auth": s.auth_close(u, b.get("auth_id"),
                                                                                   b.get("reason"))},
        ("GET", "/api/care/gfe"): lambda s, u, q, b: s.gfe_list(u, pid(q, b)),
        ("POST", "/api/care/gfe"): lambda s, u, q, b: {"estimate": s.gfe_save(u, pid(q, b), b.get("estimate"),
                                                                             b.get("estimate_id"))},
        ("POST", "/api/care/gfe/issue"): lambda s, u, q, b: {"estimate": s.gfe_issue(u, b.get("estimate_id"))},
        ("POST", "/api/care/gfe/void"): lambda s, u, q, b: {"estimate": s.gfe_void(u, b.get("estimate_id"),
                                                                                  b.get("reason"))},
        ("GET", "/api/care/gfe/pdf"): lambda s, u, q, b: s.gfe_pdf(u, q.get("estimate")),
        ("GET", "/api/care/gfe/settings"): lambda s, u, q, b: s.gfe_settings(u),
        ("POST", "/api/care/gfe/settings"): lambda s, u, q, b: s.gfe_settings_save(u, b.get("notice"),
                                                                                  b.get("reminders")),
        ("GET", "/api/care/aging"): lambda s, u, q, b: s.aging(u, q.get("as_of")),
        ("GET", "/api/care/aging.csv"): lambda s, u, q, b: s.aging_csv(u, q.get("as_of")),
        ("GET", "/api/care/statement"): lambda s, u, q, b: s.statement(u, pid(q, b), q.get("as_of")),
        ("GET", "/api/care/gaps"): lambda s, u, q, b: s.gaps(u, q.get("from"), q.get("to"), q.get("clinician", "")),
        ("GET", "/api/care/todo"): lambda s, u, q, b: s.todo(u),
    }


def mount(app, routes=None):
    """Attach the care suite to a CovenantApp (or a bare Clinic) that already has practice_suite: clinic.care_suite
    = the suite, and clinic_app's ROUTES gain /api/care/... Never raises: on a problem it logs and returns None."""
    clinic = getattr(app, "clinic", app)
    try:
        suite = getattr(clinic, "practice_suite", None)
        if not isinstance(suite, ps.PracticeSuite):
            return None
        care = getattr(clinic, "care_suite", None)
        if not isinstance(care, CareSuite):
            care = CareSuite(clinic, suite)
            clinic.care_suite = care
        host = suite.H
        routes = host.ROUTES if routes is None else routes

        def wrap(fn):
            def run(c, u, q, b):
                try:
                    return fn(c.care_suite, u, q, b)
                except PracticeError as e:
                    raise host.HttpError(e.status, e.msg) from None
            return run
        for key, fn in _route_table().items():
            routes[key] = wrap(fn)
        cc = getattr(clinic, "consent_center", None)
        if cc is not None and callable(getattr(cc, "register_holder", None)):
            try:
                cc.register_holder("care_suite", export=care.subject_data)
            except Exception:
                clinic.health("%s: consent_center holder registration failed\n%s" % (LOG_PREFIX,
                                                                                    traceback.format_exc()))
        return care
    except Exception:
        try:
            clinic.health("%s mount failed\n%s" % (LOG_PREFIX, traceback.format_exc()))
        except Exception:
            pass
        return None
