"""Stark Covenant - coverage_navigator: help people USE health insurance (individuals, families, small businesses).

Standard library only, Python 3.10+.

Covenant is not an insurer, broker or licensed adviser. This module never sells, recommends or enrols anyone in
a plan, never submits an application, claim, appeal or authorisation request, and never moves money. Every
result carries NOTICE; every letter is a DRAFT for a person to check and send themselves.

What it does
  * Plan comparison worksheets: premium, deductible (embedded or aggregate), out-of-pocket maximum (always
    embedded per person, as the ACA requires), coinsurance, copays (before or after the deductible), network
    (out-of-network rules and balance-billing warning), formulary (tier copay / coinsurance / not covered),
    HSA employer contribution; total-cost-of-care scenarios computed service by service, in order.
  * Claim tracker: claims, status history, totals, and deadlines that follow a configurable jurisdiction
    profile (internal appeal / external review filing windows and expected plan decision times).
  * Explanation-of-Benefits reader: structured lines in, plain-language explanation per line out, with
    arithmetic checks and common X12 Claim Adjustment Reason Codes (CARC) explained.
  * Prior-authorisation request and appeal letter DRAFTS with required fields, [MISSING: ...] placeholders and
    the deadlines that apply; expedited (urgent) wording when asked.
  * Family and employee benefit organisers: who is covered by what, renewal dates, enrolment windows,
    special-enrolment windows after life events, dependants reaching 26, new-hire waiting periods.
  * Export of any worksheet / letter / summary to PDF via polyglot_engine (text fallback without it).

Jurisdiction profiles (JURISDICTIONS; every figure is cited and must be checked against the person's own plan
notice, which states the exact deadline; state law can give more time)
  us_group      US employer group health plan (ERISA claims rule 29 CFR 2560.503-1 + ACA 29 CFR 2590.715-2719 /
                45 CFR 147.136): internal appeal within 180 days of the adverse benefit determination notice;
                appeal decisions 72 h urgent / 30 days pre-service / 60 days post-service; initial decisions
                72 h urgent / 15 days pre-service / 30 days post-service; federal external review requested
                within 4 months of the final internal adverse determination, decided within 45 days (72 h
                expedited).
  us_individual US individual-market (ACA) plan, 45 CFR 147.136: same filing windows; one internal appeal level.
  us_cms_pa     CMS Interoperability and Prior Authorization final rule (CMS-0057-F; Medicare Advantage,
                Medicaid / CHIP, Marketplace QHPs on the federal exchange, from 1 Jan 2026): prior-authorisation
                decisions within 72 h expedited / 7 calendar days standard.
  us_medicare   Original Medicare: redetermination request within 120 days of the initial determination notice
                (42 CFR 405.942).
  us_medicare_advantage  Medicare Advantage reconsideration within 65 days (42 CFR 422.582).
  custom        empty: the person enters every window from their plan documents.

Integration with clinic_app (clinic_app.py is not edited by this module)
  mount(app) registers JSON routes in the ROUTES table of the module that defines `app`, so clinic_app's
  session, CSRF, Host/Origin and LAN checks apply; reads need demo.read, writes need demo.write:
      GET  /api/coverage/info          notice, jurisdiction profiles, service types, CARC table
      POST /api/coverage/compare       {plans: [...], scenarios: [...]}         -> worksheet
      POST /api/coverage/eob           {eob: {...}}                             -> explanation
      POST /api/coverage/letter        {kind, fields, jurisdiction}            -> draft letter
      POST /api/coverage/claims        {subject, action: list|add|update, ...} -> tracker (stored)
      POST /api/coverage/organiser     {subject, action: get|save|upcoming, ...} -> family / employee organiser
      POST /api/coverage/pdf           {title, text}                            -> PDF download (attachment)
  Enable it in CovenantApp.__init__ exactly like nutrition_planner (see that module's docstring). Stored data
  lives in <data_root>/coverage (vault-encrypted when the clinic has a vault) or <data_root>/coverage_no_vault.
  Claim trackers / organisers for a PATIENT id (P + 12 hex) are kept only while consent_center reports the
  patient's "coverage_help" consent active (consent_center installed but not attached: refused; not installed:
  allowed under the clinic's own policy). With consent_center mounted first, withdrawal of coverage_help
  deletes those records, and subject-access export / erasure include them (holder "coverage_navigator").
  Household / business subjects are the user's own records and need no patient consent. The stateless tools
  (compare, EOB reader, letter drafts, PDF) store nothing.

Honest limits
  * Cost scenarios are estimates from the numbers entered; real plans have rules (e.g. separate drug
    deductibles, visit limits, reference pricing) that are not modelled unless entered.
  * Jurisdiction figures are federal US rules as written in the cited regulations; plans may give more time,
    states may add rules, and other countries need a custom profile.
"""
from __future__ import annotations

import calendar
import copy
import datetime as _dt
import hashlib
import importlib
import json
import os
import re
import sys
import threading

APP_NAME = "Stark Covenant"
MODULE_VERSION = "1.0.0"
NOTICE = ("Covenant is not an insurer, broker or licensed adviser; confirm details with your plan or a licensed "
          "professional.")
NO_ACTION_NOTE = ("Nothing is submitted, applied for or paid by Covenant. You decide what to send, and you send "
                  "it yourself.")
DRAFT_BANNER = "DRAFT - check every detail, then send it yourself. Covenant does not send letters or move money."


class CoverageError(ValueError):
    """Input that cannot be used (bad numbers, unknown fields)."""


def _now():
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def _date(s, what="date"):
    if isinstance(s, _dt.date):
        return s
    try:
        return _dt.date.fromisoformat(str(s))
    except (TypeError, ValueError):
        raise CoverageError("%s must be YYYY-MM-DD" % what) from None


def add_months(d, months):
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    return _dt.date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _money(x, what):
    if x is None or x == "":
        return 0.0
    try:
        v = float(x)
    except (TypeError, ValueError):
        raise CoverageError("%s must be a number" % what) from None
    if v < 0 or v != v or v == float("inf"):
        raise CoverageError("%s must be zero or more" % what)
    return v


def _r(x):
    return round(x + 1e-9, 2)


# ======================================================================================================
# Jurisdiction profiles
# ======================================================================================================
JURISDICTIONS = {
    "us_group": {
        "label": "United States - employer group health plan (ERISA / ACA)",
        "internal_appeal_days": 180,
        "internal_appeal_cite": "29 CFR 2560.503-1(h)(3)(i); 29 CFR 2590.715-2719(b)",
        "appeal_decision": {"urgent_hours": 72, "pre_service_days": 30, "post_service_days": 60},
        "appeal_decision_cite": "29 CFR 2560.503-1(i)(2)",
        "initial_decision": {"urgent_hours": 72, "pre_service_days": 15, "post_service_days": 30},
        "initial_decision_cite": "29 CFR 2560.503-1(f)(2)",
        "external_review_months": 4,
        "external_review_cite": "29 CFR 2590.715-2719(d); 45 CFR 147.136(d)(2)",
        "external_decision": {"standard_days": 45, "expedited_hours": 72},
        "claim_file_right": "You may ask, free of charge, for copies of all documents relevant to your claim, "
                            "including the criteria used (29 CFR 2560.503-1(h)(2)(iii)).",
        "verify": "Your plan's denial notice states your exact deadline; follow it if it gives more time.",
    },
    "us_individual": {
        "label": "United States - individual-market (ACA) health plan",
        "internal_appeal_days": 180,
        "internal_appeal_cite": "45 CFR 147.136(b)(3)",
        "appeal_decision": {"urgent_hours": 72, "pre_service_days": 30, "post_service_days": 60},
        "appeal_decision_cite": "45 CFR 147.136(b)(3) (incorporating 29 CFR 2560.503-1(i))",
        "initial_decision": {"urgent_hours": 72, "pre_service_days": 15, "post_service_days": 30},
        "initial_decision_cite": "45 CFR 147.136(b)(3) (incorporating 29 CFR 2560.503-1(f))",
        "external_review_months": 4,
        "external_review_cite": "45 CFR 147.136(c)-(d)",
        "external_decision": {"standard_days": 45, "expedited_hours": 72},
        "claim_file_right": "You may ask, free of charge, for your claim file and the criteria used "
                            "(45 CFR 147.136(b)(3)).",
        "verify": "Individual-market plans have one level of internal appeal. Your notice states your deadline; "
                  "your state may run the external review.",
    },
    "us_cms_pa": {
        "label": "United States - CMS-0057-F impacted payer (Medicare Advantage, Medicaid/CHIP, federal-exchange "
                 "QHP) prior authorisation",
        "internal_appeal_days": None,
        "internal_appeal_cite": "",
        "appeal_decision": {},
        "initial_decision": {"urgent_hours": 72, "pre_service_days": 7},
        "initial_decision_cite": "CMS Interoperability and Prior Authorization Final Rule, CMS-0057-F "
                                 "(89 FR 8758, 2024), from 1 January 2026",
        "external_review_months": None,
        "external_decision": {},
        "claim_file_right": "A denial must include a specific reason (CMS-0057-F).",
        "verify": "Appeal windows for these programmes differ (Medicare Advantage, Medicaid fair hearings); "
                  "use the notice you received.",
    },
    "us_medicare": {
        "label": "United States - Original Medicare (Parts A/B) redetermination",
        "internal_appeal_days": 120,
        "internal_appeal_cite": "42 CFR 405.942(a)",
        "appeal_decision": {"post_service_days": 60},
        "appeal_decision_cite": "42 CFR 405.950",
        "initial_decision": {},
        "external_review_months": None,
        "external_decision": {},
        "claim_file_right": "",
        "verify": "The Medicare Summary Notice states your deadline.",
    },
    "us_medicare_advantage": {
        "label": "United States - Medicare Advantage reconsideration",
        "internal_appeal_days": 65,
        "internal_appeal_cite": "42 CFR 422.582(b)",
        "appeal_decision": {"urgent_hours": 72, "pre_service_days": 30, "post_service_days": 60},
        "appeal_decision_cite": "42 CFR 422.590",
        "initial_decision": {"urgent_hours": 72, "pre_service_days": 7},
        "initial_decision_cite": "CMS-0057-F (from 1 January 2026)",
        "external_review_months": None,
        "external_decision": {},
        "claim_file_right": "",
        "verify": "The plan's notice states your deadline and how to ask for a fast (expedited) decision.",
    },
    "custom": {
        "label": "Custom - enter the windows from your plan documents",
        "internal_appeal_days": None, "internal_appeal_cite": "", "appeal_decision": {}, "initial_decision": {},
        "external_review_months": None, "external_decision": {}, "claim_file_right": "",
        "verify": "Enter every deadline from your own plan documents or regulator.",
    },
}


def jurisdiction(name, overrides=None):
    if name not in JURISDICTIONS:
        raise CoverageError("Unknown jurisdiction profile %r (known: %s)" % (name, ", ".join(JURISDICTIONS)))
    j = copy.deepcopy(JURISDICTIONS[name])
    j["id"] = name
    for k, v in (overrides or {}).items():
        if k not in j:
            raise CoverageError("Unknown jurisdiction field %r" % k)
        j[k] = v
    return j


def appeal_deadlines(profile, notice_date=None, final_internal_date=None, appeal_filed_date=None,
                     service="post_service"):
    """Deadlines for a denial -> list of {what, date, rule, cite}. Dates the person has not given are skipped."""
    out = []
    if notice_date and profile.get("internal_appeal_days"):
        d = _date(notice_date, "denial notice date") + _dt.timedelta(days=int(profile["internal_appeal_days"]))
        out.append({"what": "Last day to file the internal appeal", "date": d.isoformat(),
                    "rule": "%d days after you received the denial notice" % profile["internal_appeal_days"],
                    "cite": profile.get("internal_appeal_cite", "")})
    dec = profile.get("appeal_decision") or {}
    if service == "urgent" and dec.get("urgent_hours"):
        out.append({"what": "Plan should decide the urgent appeal within",
                    "date": "%d hours of receiving it" % dec["urgent_hours"],
                    "rule": "urgent care", "cite": profile.get("appeal_decision_cite", "")})
    elif appeal_filed_date:
        days = dec.get("%s_days" % service)
        if days:
            d = _date(appeal_filed_date, "appeal filed date") + _dt.timedelta(days=int(days))
            out.append({"what": "Plan should decide the appeal by", "date": d.isoformat(),
                        "rule": "%d days after it receives the appeal (%s)" % (days, service.replace("_", "-")),
                        "cite": profile.get("appeal_decision_cite", "")})
    if final_internal_date and profile.get("external_review_months"):
        d = add_months(_date(final_internal_date, "final internal decision date"),
                       int(profile["external_review_months"]))
        ext = profile.get("external_decision") or {}
        out.append({"what": "Last day to request external review", "date": d.isoformat(),
                    "rule": "%d months after the final internal denial notice; decision within %s days "
                            "(%s hours if expedited)" % (profile["external_review_months"],
                                                         ext.get("standard_days", "?"),
                                                         ext.get("expedited_hours", "?")),
                    "cite": profile.get("external_review_cite", "")})
    return out


# ======================================================================================================
# Plan comparison and total cost of care
# ======================================================================================================
SERVICE_TYPES = ("primary_care", "specialist", "urgent_care", "er", "inpatient", "outpatient_surgery", "lab",
                 "imaging", "mental_health", "therapy", "maternity", "preventive", "drug", "other")
PLAN_FIELDS = ("name", "premium_monthly", "deductible_individual", "deductible_family", "oop_max_individual",
               "oop_max_family", "coinsurance", "copays", "copay_after_deductible", "deductible_type",
               "employer_hsa_annual", "out_of_network", "formulary", "rx_subject_to_deductible", "network",
               "notes")


def _plan(p):
    if not isinstance(p, dict):
        raise CoverageError("Each plan must be an object.")
    bad = set(p) - set(PLAN_FIELDS)
    if bad:
        raise CoverageError("Unknown plan field(s): %s" % ", ".join(sorted(bad)))
    q = dict(p)
    q["name"] = str(p.get("name") or "Plan")[:120]
    for k in ("premium_monthly", "deductible_individual", "oop_max_individual", "employer_hsa_annual"):
        q[k] = _money(p.get(k), "%s (%s)" % (k, q["name"]))
    q["deductible_family"] = _money(p.get("deductible_family", 2 * q["deductible_individual"]), "deductible_family")
    q["oop_max_family"] = _money(p.get("oop_max_family", 2 * q["oop_max_individual"]), "oop_max_family")
    c = float(p.get("coinsurance") or 0)
    if not 0 <= c <= 1:
        raise CoverageError("coinsurance is the share YOU pay, from 0 to 1 (e.g. 0.2 for 20%)")
    q["coinsurance"] = c
    q["copays"] = {k: _money(v, "copay %s" % k) for k, v in (p.get("copays") or {}).items()}
    for k in q["copays"]:
        if k not in SERVICE_TYPES:
            raise CoverageError("Unknown service type in copays: %r" % k)
    q["deductible_type"] = p.get("deductible_type") or "embedded"
    if q["deductible_type"] not in ("embedded", "aggregate"):
        raise CoverageError("deductible_type is embedded or aggregate")
    q["copay_after_deductible"] = bool(p.get("copay_after_deductible"))
    q["rx_subject_to_deductible"] = bool(p.get("rx_subject_to_deductible"))
    oon = p.get("out_of_network") or {"covered": False}
    q["out_of_network"] = {"covered": bool(oon.get("covered")),
                           "deductible_individual": _money(oon.get("deductible_individual"), "oon deductible"),
                           "oop_max_individual": _money(oon.get("oop_max_individual"), "oon oop max"),
                           "coinsurance": float(oon.get("coinsurance") or 0.4)}
    q["formulary"] = {str(k).lower(): v for k, v in (p.get("formulary") or {}).items()}
    q["network"] = [str(x).lower() for x in (p.get("network") or ())]
    return q


def _services(scenario):
    out = []
    for i, s in enumerate(scenario.get("services") or ()):
        t = s.get("type", "other")
        if t not in SERVICE_TYPES:
            raise CoverageError("Unknown service type %r" % t)
        n = int(s.get("count", 1))
        if not 0 <= n <= 1000:
            raise CoverageError("count must be 0-1000")
        for _ in range(n):
            out.append({"member": str(s.get("member") or "self"), "type": t,
                        "allowed": _money(s.get("allowed"), "allowed amount (service %d)" % (i + 1)),
                        "network": s.get("network") or "in", "provider": str(s.get("provider") or ""),
                        "drug": str(s.get("drug") or "").lower(), "preventive": bool(s.get("preventive")) or
                        t == "preventive", "label": str(s.get("label") or t)})
    return out


def scenario_cost(plan, scenario):
    """Member cost of one scenario under one plan, service by service, in order. -> dict with lines."""
    p = _plan(plan)
    svcs = _services(scenario)
    members = sorted({s["member"] for s in svcs} | set(scenario.get("members") or ()))
    family = len(members) > 1
    ded = {m: 0.0 for m in members}
    oop = {m: 0.0 for m in members}
    oon_ded = {m: 0.0 for m in members}
    oon_oop = {m: 0.0 for m in members}
    fam = {"ded": 0.0, "oop": 0.0}
    lines, warnings = [], []

    def ded_left(m):
        if not family:
            return max(0.0, p["deductible_individual"] - ded[m])
        fam_left = max(0.0, p["deductible_family"] - fam["ded"])
        if p["deductible_type"] == "aggregate":
            return fam_left
        return min(max(0.0, p["deductible_individual"] - ded[m]), fam_left)

    def oop_left(m):   # ACA: self-only OOP max is embedded in every family plan
        ind = max(0.0, p["oop_max_individual"] - oop[m])
        return min(ind, max(0.0, p["oop_max_family"] - fam["oop"])) if family else ind

    for s in svcs:
        m, a = s["member"], s["allowed"]
        how, d_used, counts = "", 0.0, True
        in_net = s["network"] == "in"
        if s["provider"] and p["network"] and in_net and s["provider"].lower() not in p["network"]:
            in_net = False
            warnings.append("%s is not in %s's network list you entered; treated as out-of-network."
                            % (s["provider"], p["name"]))
        if s["drug"]:
            f = p["formulary"].get(s["drug"])
            if f is None or f.get("covered") is False:
                pay, how, counts = a, "not on the formulary: full price, does not count toward the maximum", False
            elif p["rx_subject_to_deductible"] and ded_left(m) > 0:
                d_used = min(a, ded_left(m))
                rest = a - d_used
                pay = d_used + (min(_money(f.get("copay"), "copay"), rest) if "copay" in f
                                else rest * float(f.get("coinsurance", p["coinsurance"])))
                how = "deductible first (drugs count toward it on this plan), then tier %s" % f.get("tier", "?")
            elif "copay" in f:
                pay, how = min(_money(f["copay"], "copay"), a), "tier %s copay" % f.get("tier", "?")
            else:
                pay = a * float(f.get("coinsurance", p["coinsurance"]))
                how = "tier %s coinsurance" % f.get("tier", "?")
        elif s["preventive"] and in_net:
            pay, how = 0.0, "in-network preventive care: no cost to you (ACA, 45 CFR 147.130)"
        elif not in_net:
            o = p["out_of_network"]
            if not o["covered"]:
                pay, how, counts = a, "out-of-network and not covered: you pay it all", False
            else:
                left = max(0.0, o["deductible_individual"] - oon_ded[m])
                d = min(a, left)
                pay = d + (a - d) * o["coinsurance"]
                cap = max(0.0, o["oop_max_individual"] - oon_oop[m]) if o["oop_max_individual"] else pay
                pay = min(pay, cap)
                oon_ded[m] += min(d, pay)
                oon_oop[m] += pay
                how = "out-of-network deductible and coinsurance (separate limits)"
                counts = False
                warnings.append("Out-of-network providers may bill you above the allowed amount (balance "
                                "billing), except where the No Surprises Act protects you.")
        else:
            cp = p["copays"].get(s["type"])
            if cp is not None and not p["copay_after_deductible"]:
                pay, how = min(cp, a), "copay"
            else:
                d_used = min(a, ded_left(m))
                rest = a - d_used
                if cp is not None:
                    pay, how = d_used + min(cp, rest), "deductible, then copay"
                else:
                    pay, how = d_used + rest * p["coinsurance"], "deductible, then %d%% coinsurance" % round(
                        p["coinsurance"] * 100)
        if counts:
            cap = oop_left(m)
            if pay > cap:
                pay = cap
                how += "; capped by the out-of-pocket maximum"
            d_used = min(d_used, pay)
            ded[m] += d_used
            fam["ded"] += d_used
            oop[m] += pay
            fam["oop"] += pay
        lines.append({"member": m, "service": s["label"], "type": s["type"], "allowed": _r(a), "you_pay": _r(pay),
                      "plan_pays": _r(max(0.0, a - pay)), "how": how})
    member_total = sum(x["you_pay"] for x in lines)
    premiums = p["premium_monthly"] * 12
    return {"plan": p["name"], "scenario": str(scenario.get("name") or "Scenario"),
            "annual_premiums": _r(premiums), "your_care_costs": _r(member_total),
            "employer_hsa": _r(p["employer_hsa_annual"]),
            "total_cost": _r(premiums + member_total - p["employer_hsa_annual"]),
            "deductible_met": {m: _r(v) for m, v in ded.items()}, "oop_counted": {m: _r(v) for m, v in oop.items()},
            "lines": lines, "warnings": sorted(set(warnings))}


def compare_plans(plans, scenarios):
    """Worksheet: every plan x every scenario, lowest total cost marked per scenario (not a recommendation)."""
    if not plans or not scenarios:
        raise CoverageError("Give at least one plan and one scenario.")
    if len(plans) > 12 or len(scenarios) > 12:
        raise CoverageError("At most 12 plans and 12 scenarios.")
    rows = []
    for sc in scenarios:
        res = [scenario_cost(p, sc) for p in plans]
        low = min(r["total_cost"] for r in res)
        for r in res:
            r["lowest_for_scenario"] = r["total_cost"] == low
        rows.append({"scenario": res[0]["scenario"], "results": res})
    summary = [_plan(p) for p in plans]
    ws = {"notice": NOTICE, "no_action": NO_ACTION_NOTE, "module": "coverage_navigator", "created": _now(),
          "plans": [{k: s[k] for k in ("name", "premium_monthly", "deductible_individual", "deductible_family",
                                       "oop_max_individual", "oop_max_family", "coinsurance", "copays",
                                       "deductible_type", "employer_hsa_annual")} for s in summary],
          "scenarios": rows,
          "explanation": ["Total cost = 12 x monthly premium + what you pay for care - any employer HSA money.",
                          "The cheapest line in a scenario is only the cheapest for those numbers; it is not a "
                          "recommendation. Check networks (are your doctors in it?) and drug lists.",
                          "Deductible: what you pay before the plan shares costs. Coinsurance: your percentage "
                          "after that. Out-of-pocket maximum: the most you pay in a year for covered "
                          "in-network care (premiums not included)."]}
    ws["text"] = worksheet_text(ws)
    return ws


def worksheet_text(ws):
    lines = ["Stark Covenant - plan comparison worksheet", NOTICE, ""]
    for p in ws["plans"]:
        lines.append("%s: premium $%.2f/month, deductible $%.0f (family $%.0f, %s), out-of-pocket max $%.0f "
                     "(family $%.0f), coinsurance %d%%" % (
                         p["name"], p["premium_monthly"], p["deductible_individual"], p["deductible_family"],
                         p["deductible_type"], p["oop_max_individual"], p["oop_max_family"],
                         round(p["coinsurance"] * 100)))
    for sc in ws["scenarios"]:
        lines += ["", "Scenario: %s" % sc["scenario"]]
        for r in sc["results"]:
            lines.append("  %-30s premiums $%9.2f + care $%9.2f - HSA $%7.2f = $%9.2f%s" % (
                r["plan"][:30], r["annual_premiums"], r["your_care_costs"], r["employer_hsa"], r["total_cost"],
                "  (lowest for this scenario)" if r["lowest_for_scenario"] else ""))
            for w in r["warnings"]:
                lines.append("    ! " + w)
    lines += [""] + ws["explanation"] + ["", NO_ACTION_NOTE, NOTICE]
    return "\n".join(lines)


# ======================================================================================================
# EOB reader
# ======================================================================================================
CARC = {   # X12 Claim Adjustment Reason Codes (common ones), plain language
    "1": "Deductible: this amount counts toward your deductible, so you owe it.",
    "2": "Coinsurance: your percentage share of the allowed amount.",
    "3": "Copay: your fixed amount for this service.",
    "16": "The claim is missing information; the provider usually needs to resubmit it.",
    "18": "Duplicate claim: the plan thinks this was already billed.",
    "26": "The service was before your coverage started.",
    "27": "The service was after your coverage ended.",
    "29": "The claim was sent after the plan's filing deadline (usually the provider's problem).",
    "45": "The charge is above the plan's allowed amount; an in-network provider usually cannot bill you for "
          "the difference.",
    "50": "Not covered because the plan decided it was not medically necessary - you can appeal this.",
    "96": "Not covered under your plan.",
    "97": "Paid as part of another service on the same claim.",
    "197": "Prior authorisation (pre-certification) was required and not on file.",
    "204": "This service is not covered under your current benefit plan.",
    "242": "The provider is not in your network.",
}
EOB_LINE_FIELDS = ("service_date", "description", "code", "billed", "allowed", "plan_paid", "deductible", "copay",
                   "coinsurance", "not_covered", "remark_codes", "provider", "member")


def explain_eob(eob):
    """Structured EOB -> per-line plain-language explanation, arithmetic checks and totals."""
    if not isinstance(eob, dict) or not isinstance(eob.get("lines"), list) or not eob["lines"]:
        raise CoverageError("An EOB needs a list of lines.")
    out_lines, issues = [], []
    tot = dict.fromkeys(("billed", "allowed", "plan_paid", "you_owe"), 0.0)
    for n, ln in enumerate(eob["lines"], 1):
        bad = set(ln) - set(EOB_LINE_FIELDS)
        if bad:
            raise CoverageError("Unknown EOB line field(s): %s" % ", ".join(sorted(bad)))
        v = {k: _money(ln.get(k), "%s (line %d)" % (k, n)) for k in
             ("billed", "allowed", "plan_paid", "deductible", "copay", "coinsurance", "not_covered")}
        owe = v["deductible"] + v["copay"] + v["coinsurance"] + v["not_covered"]
        disc = v["billed"] - v["allowed"] if v["billed"] and v["allowed"] else 0.0
        say = ["%s%s on %s." % (ln.get("description") or "Service", " (code %s)" % ln["code"] if ln.get("code")
                                else "", ln.get("service_date") or "an unstated date")]
        if v["billed"]:
            say.append("The provider charged $%.2f." % v["billed"])
        if v["allowed"]:
            say.append("The plan's allowed amount (the price it agreed to) is $%.2f%s." % (
                v["allowed"], "; the $%.2f difference is a discount you should not be billed for if the provider "
                              "is in-network" % disc if disc > 0.005 else ""))
        say.append("The plan paid $%.2f." % v["plan_paid"])
        parts = [("deductible", "toward your deductible"), ("copay", "copay"), ("coinsurance", "coinsurance"),
                 ("not_covered", "not covered by the plan")]
        owed = ["$%.2f %s" % (v[k], label) for k, label in parts if v[k] > 0.005]
        say.append("You may owe $%.2f%s." % (owe, " (" + ", ".join(owed) + ")" if owed else ""))
        for code in ln.get("remark_codes") or ():
            c = str(code).upper().replace("CO-", "").replace("PR-", "").replace("OA-", "").replace("PI-", "")
            say.append("Code %s: %s" % (code, CARC.get(c, "not in Covenant's short list - ask your plan what it "
                                                          "means.")))
        checks = []
        if v["allowed"] and abs(v["plan_paid"] + v["deductible"] + v["copay"] + v["coinsurance"] - v["allowed"]) > 0.01:
            checks.append("Plan paid + deductible + copay + coinsurance ($%.2f) does not equal the allowed amount "
                          "($%.2f). Ask the plan to explain this line." % (
                              v["plan_paid"] + v["deductible"] + v["copay"] + v["coinsurance"], v["allowed"]))
        if v["billed"] and v["allowed"] > v["billed"] + 0.01:
            checks.append("Allowed amount is higher than the charge; this looks like an entry error.")
        if v["not_covered"] > 0.005:
            checks.append("Part of this line is not covered. If you think it should be, you can appeal; "
                          "keep this EOB.")
        issues += ["Line %d: %s" % (n, c) for c in checks]
        tot["billed"] += v["billed"]
        tot["allowed"] += v["allowed"]
        tot["plan_paid"] += v["plan_paid"]
        tot["you_owe"] += owe
        out_lines.append({"line": n, "explanation": " ".join(say), "you_may_owe": _r(owe), "checks": checks})
    res = {"notice": NOTICE, "not_a_bill": "An Explanation of Benefits is not a bill. Compare it with the "
                                           "provider's bill before you pay anything.",
           "claim_number": eob.get("claim_number", ""), "plan": eob.get("plan", ""),
           "lines": out_lines, "totals": {k: _r(x) for k, x in tot.items()}, "issues": issues}
    res["text"] = "\n".join(["Stark Covenant - Explanation of Benefits, in plain words", NOTICE, res["not_a_bill"],
                             ""] + ["%d. %s" % (x["line"], x["explanation"]) for x in out_lines] +
                            ([""] + issues if issues else []) +
                            ["", "Total you may owe: $%.2f" % res["totals"]["you_owe"], NOTICE])
    return res


# ======================================================================================================
# Letters (drafts only)
# ======================================================================================================
LETTER_FIELDS = {
    "appeal_internal": ("member_name", "member_id", "plan_name", "claim_number", "service", "service_date",
                        "provider", "denial_date", "denial_reason", "why_covered"),
    "appeal_external": ("member_name", "member_id", "plan_name", "claim_number", "service", "provider",
                        "final_denial_date", "denial_reason", "why_covered"),
    "prior_authorization": ("member_name", "member_id", "plan_name", "service", "provider", "diagnosis",
                            "requested_date", "medical_need"),
}
OPTIONAL_FIELDS = ("group_number", "address", "phone", "email", "supporting_documents", "urgent", "today",
                   "appeal_filed_date", "representative", "service_code")


def draft_letter(kind, fields, jurisdiction_id="us_group", overrides=None):
    """-> {draft: True, text, missing, deadlines, ready_to_review}. Missing required fields become
    [MISSING: field] placeholders; nothing is ever sent."""
    if kind not in LETTER_FIELDS:
        raise CoverageError("Unknown letter kind %r (known: %s)" % (kind, ", ".join(LETTER_FIELDS)))
    f = {k: str(v).strip() for k, v in (fields or {}).items() if v not in (None, "") and not isinstance(v, list)}
    docs = [str(x) for x in (fields or {}).get("supporting_documents") or ()]
    unknown = set(fields or {}) - set(LETTER_FIELDS[kind]) - set(OPTIONAL_FIELDS) - {
        "final_denial_date", "denial_date", "diagnosis", "requested_date", "medical_need", "service_date"}
    if unknown:
        raise CoverageError("Unknown letter field(s): %s" % ", ".join(sorted(unknown)))
    prof = jurisdiction(jurisdiction_id, overrides)
    urgent = str((fields or {}).get("urgent", "")).lower() in ("1", "true", "yes")
    missing = [k for k in LETTER_FIELDS[kind] if not f.get(k)]

    def g(k):
        return f.get(k) or "[MISSING: %s]" % k.replace("_", " ")

    today = f.get("today") or _dt.date.today().isoformat()
    if kind == "appeal_internal":
        deadlines = appeal_deadlines(prof, notice_date=f.get("denial_date") if f.get("denial_date") else None,
                                     appeal_filed_date=f.get("appeal_filed_date"),
                                     service="urgent" if urgent else "post_service")
        subject = "%sRequest for internal appeal - claim %s" % ("URGENT (expedited) " if urgent else "",
                                                                g("claim_number"))
        body = [
            "I am writing to appeal the decision dated %s to deny coverage for %s provided by %s on %s." % (
                g("denial_date"), g("service"), g("provider"), g("service_date")),
            "The reason given was: %s." % g("denial_reason"),
            "I believe this should be covered because: %s" % g("why_covered"),
            "Please send me, free of charge, copies of all documents, records and other information relevant to "
            "this claim, including any internal rule, guideline or criteria relied on.",
        ]
        if urgent:
            body.insert(0, "This is a request for an EXPEDITED (urgent) appeal: waiting the standard time could "
                           "seriously jeopardise my life, health or ability to regain maximum function, as my "
                           "treating clinician can confirm.")
    elif kind == "appeal_external":
        deadlines = appeal_deadlines(prof, final_internal_date=f.get("final_denial_date"))
        subject = "%sRequest for external review - claim %s" % ("URGENT (expedited) " if urgent else "",
                                                                g("claim_number"))
        body = [
            "I request an independent external review of the final internal adverse benefit determination dated "
            "%s about %s provided by %s." % (g("final_denial_date"), g("service"), g("provider")),
            "The plan's reason was: %s." % g("denial_reason"),
            "I believe the decision is wrong because: %s" % g("why_covered"),
        ]
        if urgent:
            body.insert(0, "I ask for EXPEDITED external review because of an urgent medical condition.")
    else:
        dec = prof.get("initial_decision") or {}
        deadlines = []
        if urgent and dec.get("urgent_hours"):
            deadlines.append({"what": "Plan should decide an urgent (expedited) request within",
                              "date": "%d hours" % dec["urgent_hours"], "rule": "urgent / expedited",
                              "cite": prof.get("initial_decision_cite", "")})
        elif dec.get("pre_service_days"):
            base = f.get("appeal_filed_date") or today
            d = _date(base, "request date") + _dt.timedelta(days=int(dec["pre_service_days"]))
            deadlines.append({"what": "Plan should decide a standard request by", "date": d.isoformat(),
                              "rule": "%d calendar days after it receives the request" % dec["pre_service_days"],
                              "cite": prof.get("initial_decision_cite", "")})
        subject = "%sPrior authorisation request - %s" % ("URGENT (expedited) " if urgent else "", g("service"))
        body = [
            "I am requesting prior authorisation for %s, to be provided by %s on or around %s." % (
                g("service"), g("provider"), g("requested_date")),
            "Diagnosis / reason: %s." % g("diagnosis"),
            "Why it is medically needed: %s" % g("medical_need"),
            "Please confirm receipt and tell me the decision date. If any information is missing, please tell me "
            "what is needed rather than denying the request.",
        ]
        if urgent:
            body.insert(0, "This is an EXPEDITED (urgent) request: the standard timeframe could seriously "
                           "jeopardise the patient's life, health or ability to regain maximum function.")
    if not deadlines:
        deadlines.append({"what": "Deadline", "date": "[enter the date from your plan notice]",
                          "rule": prof["verify"], "cite": ""})
    head = [DRAFT_BANNER, "", today, "", "To: %s" % g("plan_name"), "Member: %s" % g("member_name"),
            "Member ID: %s" % g("member_id")]
    if f.get("group_number"):
        head.append("Group number: %s" % f["group_number"])
    if kind != "prior_authorization":
        head.append("Claim / reference number: %s" % g("claim_number"))
    if f.get("representative"):
        head.append("Authorised representative: %s (written authorisation enclosed)" % f["representative"])
    text = head + ["", "Subject: %s" % subject, "", "Dear Sir or Madam,", ""] + body + [""]
    if docs:
        text += ["Enclosed:"] + ["  - %s" % d for d in docs] + [""]
    if prof.get("claim_file_right") and kind != "prior_authorization":
        text += [prof["claim_file_right"], ""]
    text += ["Sincerely,", g("member_name"),
             " / ".join(x for x in (f.get("address"), f.get("phone"), f.get("email")) if x) or "[contact details]",
             "", "--- Deadlines that apply (check against your plan notice) ---"]
    text += ["  %s: %s (%s)%s" % (d["what"], d["date"], d["rule"], " [%s]" % d["cite"] if d["cite"] else "")
             for d in deadlines]
    text += ["  " + prof["verify"], "", NO_ACTION_NOTE, NOTICE]
    return {"notice": NOTICE, "draft": True, "kind": kind, "jurisdiction": prof["id"],
            "jurisdiction_label": prof["label"], "missing": missing, "ready_to_review": not missing,
            "deadlines": deadlines, "urgent": urgent, "text": "\n".join(text), "no_action": NO_ACTION_NOTE}


# ======================================================================================================
# Storage
# ======================================================================================================
class JsonStore:
    """Small JSON documents, atomically written; encrypted with `vault` (encrypt/decrypt) when given. The
    document name is sealed inside so files cannot be swapped."""

    NAME = re.compile(r"[a-z][a-z0-9_]{0,40}")

    def __init__(self, root, vault=None):
        self.root, self.vault = root, vault
        self._lock = threading.Lock()

    def _path(self, name):
        if not self.NAME.fullmatch(name):
            raise ValueError("bad store name")
        return os.path.join(self.root, name + (".bin" if self.vault is not None else ".json"))

    def load(self, name, default):
        try:
            with open(self._path(name), "rb") as h:
                raw = h.read()
        except FileNotFoundError:
            return copy.deepcopy(default)
        if self.vault is not None:
            raw = self.vault.decrypt(raw)
        doc = json.loads(raw.decode("utf-8"))
        if doc.get("_name") != name:
            raise ValueError("store document does not belong here")
        return doc["data"]

    def save(self, name, data):
        os.makedirs(self.root, exist_ok=True)
        raw = json.dumps({"_name": name, "data": data}, sort_keys=True).encode("utf-8")
        if self.vault is not None:
            raw = self.vault.encrypt(raw)
        p = self._path(name)
        with self._lock:
            with open(p + ".tmp", "wb") as h:
                h.write(raw)
                h.flush()
                os.fsync(h.fileno())
            os.replace(p + ".tmp", p)

    def delete(self, name):
        try:
            os.remove(self._path(name))
            return True
        except FileNotFoundError:
            return False


class _MemStore:
    def __init__(self):
        self.d = {}

    def delete(self, name):
        return self.d.pop(name, None) is not None

    def load(self, name, default):
        return copy.deepcopy(self.d.get(name, default))

    def save(self, name, data):
        self.d[name] = copy.deepcopy(data)


def _subject_key(subject):
    s = str(subject or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", s):
        raise CoverageError("subject must be 1-40 letters, digits, - or _")
    return s.lower()


def _subject_names(subject):
    k = re.sub(r"[^a-z0-9_]", "_", _subject_key(subject))
    return ["claims_" + k, "org_family_" + k, "org_business_" + k]


PATIENT_ID = re.compile(r"^P[0-9a-f]{12}$")
CONSENT_PURPOSE = "coverage_help"   # consent_center purpose key


def forget_subject(store, subject):
    """Delete every claim tracker / organiser held for a subject -> number of documents removed."""
    return sum(1 for n in _subject_names(subject) if store.delete(n))


def export_subject(store, subject):
    return {n: store.load(n, None) for n in _subject_names(subject) if store.load(n, None) is not None}


# ======================================================================================================
# Claim tracker
# ======================================================================================================
CLAIM_STATUSES = ("submitted", "processing", "paid", "partially_paid", "denied", "appeal_filed",
                  "appeal_denied", "external_review", "resolved", "closed")
CLAIM_FIELDS = ("service_date", "provider", "member", "description", "billed", "allowed", "plan_paid", "you_owe",
                "claim_number", "plan_name", "jurisdiction", "notes", "urgent", "service_kind")


class ClaimTracker:
    """Claims per subject (a patient id, household or business). Records only; it never pays or submits."""

    def __init__(self, store=None, subject="household"):
        self.store = store or _MemStore()
        self.name = _subject_names(subject)[0]
        self._lock = threading.Lock()

    def _all(self):
        return self.store.load(self.name, {"claims": []})

    def list(self):
        return self._all()["claims"]

    def add(self, data):
        bad = set(data or {}) - set(CLAIM_FIELDS)
        if bad:
            raise CoverageError("Unknown claim field(s): %s" % ", ".join(sorted(bad)))
        c = {k: data.get(k) for k in CLAIM_FIELDS if data.get(k) not in (None, "")}
        for k in ("billed", "allowed", "plan_paid", "you_owe"):
            if k in c:
                c[k] = _money(c[k], k)
        if c.get("service_date"):
            c["service_date"] = _date(c["service_date"], "service date").isoformat()
        c.setdefault("jurisdiction", "us_group")
        jurisdiction(c["jurisdiction"])
        c.update(id="C" + hashlib.sha256(os.urandom(16)).hexdigest()[:12], status="submitted",
                 history=[{"status": "submitted", "at": _now(), "date": c.get("service_date")}], created=_now())
        with self._lock:
            d = self._all()
            d["claims"].append(c)
            self.store.save(self.name, d)
        return c

    def update(self, claim_id, status, date=None, note="", **amounts):
        if status not in CLAIM_STATUSES:
            raise CoverageError("status must be one of: %s" % ", ".join(CLAIM_STATUSES))
        date = _date(date, "date").isoformat() if date else _dt.date.today().isoformat()
        with self._lock:
            d = self._all()
            c = next((x for x in d["claims"] if x["id"] == claim_id), None)
            if c is None:
                raise CoverageError("No such claim.")
            for k, v in amounts.items():
                if k not in ("billed", "allowed", "plan_paid", "you_owe"):
                    raise CoverageError("Unknown amount %r" % k)
                c[k] = _money(v, k)
            c["status"] = status
            c["history"].append({"status": status, "at": _now(), "date": date, "note": str(note)[:500]})
            if status == "denied":
                c["denial_date"] = date
            elif status == "appeal_filed":
                c["appeal_filed_date"] = date
            elif status == "appeal_denied":
                c["final_denial_date"] = date
            self.store.save(self.name, d)
        return c

    def deadlines(self, claim, today=None):
        prof = jurisdiction(claim.get("jurisdiction", "us_group"))
        kind = "urgent" if claim.get("urgent") else claim.get("service_kind") or "post_service"
        dl = appeal_deadlines(prof, notice_date=claim.get("denial_date") if claim["status"] == "denied" else None,
                              appeal_filed_date=claim.get("appeal_filed_date") if claim["status"] == "appeal_filed"
                              else None,
                              final_internal_date=claim.get("final_denial_date") if claim["status"] ==
                              "appeal_denied" else None, service=kind)
        today = _date(today or _dt.date.today())
        for d in dl:
            try:
                d["days_left"] = (_date(d["date"]) - today).days
            except CoverageError:
                d["days_left"] = None
        return dl

    def summary(self, today=None):
        claims = self.list()
        dl = []
        for c in claims:
            for d in self.deadlines(c, today):
                dl.append(dict(d, claim=c["id"], claim_number=c.get("claim_number", ""),
                               provider=c.get("provider", "")))
        dl.sort(key=lambda d: (d["days_left"] is None, d["days_left"] if d["days_left"] is not None else 0))
        tot = {k: _r(sum(c.get(k, 0.0) for c in claims)) for k in ("billed", "allowed", "plan_paid", "you_owe")}
        return {"notice": NOTICE, "no_action": NO_ACTION_NOTE, "claims": claims, "totals": tot,
                "open": sum(1 for c in claims if c["status"] not in ("resolved", "closed", "paid")),
                "deadlines": dl, "overdue": [d for d in dl if d["days_left"] is not None and d["days_left"] < 0]}


# ======================================================================================================
# Benefit organisers (family / employees)
# ======================================================================================================
LIFE_EVENTS = {   # event -> (marketplace window days, group plan window days)
    "birth": (60, 30), "adoption": (60, 30), "marriage": (60, 30), "loss_of_coverage": (60, 30),
    "move": (60, None), "divorce": (60, None), "medicaid_chip_loss": (60, 60), "medicaid_chip_eligible": (60, 60),
}
LIFE_EVENT_CITE = ("Marketplace special enrolment: 60 days (45 CFR 155.420(c)); employer group plans: at least "
                   "30 days, 60 days for Medicaid/CHIP changes (HIPAA special enrolment, 29 CFR 2590.701-6). "
                   "Your plan may allow longer.")
DEPENDENT_AGE_LIMIT = 26   # ACA dependent coverage, 45 CFR 147.120
MAX_WAITING_DAYS = 90      # ACA waiting-period limit, 29 CFR 2590.715-2708


def _age_on(dob, day):
    return day.year - dob.year - ((day.month, day.day) < (dob.month, dob.day))


class BenefitsOrganiser:
    """Who is covered by what, renewals, enrolment windows. kind = family | business."""

    def __init__(self, store=None, subject="household", kind="family"):
        if kind not in ("family", "business"):
            raise CoverageError("kind is family or business")
        self.store = store or _MemStore()
        self.kind = kind
        self.name = _subject_names(subject)[1 if kind == "family" else 2]

    def get(self):
        return self.store.load(self.name, {"people": [], "coverages": [], "events": [], "renewal": None,
                                           "open_enrolment": None})

    def save(self, data):
        d = self.get()
        for k in ("people", "coverages", "events"):
            if k in data:
                if not isinstance(data[k], list) or len(data[k]) > 1000:
                    raise CoverageError("%s must be a list (max 1000)" % k)
                d[k] = data[k]
        for k in ("renewal", "open_enrolment"):
            if k in data:
                d[k] = data[k]
        for p in d["people"]:
            if not p.get("id"):
                raise CoverageError("every person needs an id")
            for k in ("dob", "hire_date"):
                if p.get(k):
                    _date(p[k], k)
        for c in d["coverages"]:
            for k in ("renewal_date",):
                if c.get(k):
                    _date(c[k], k)
        self.store.save(self.name, d)
        return d

    def coverage_matrix(self):
        d = self.get()
        rows = []
        for p in d["people"]:
            covs = [c for c in d["coverages"] if p["id"] in (c.get("covered") or ())]
            rows.append({"person": p["id"], "name": p.get("name", p["id"]),
                         "coverage": [{"plan": c.get("plan"), "type": c.get("type", "medical"),
                                       "holder": c.get("holder", "")} for c in covs],
                         "no_medical": not any(c.get("type", "medical") == "medical" for c in covs)})
        return rows

    def upcoming(self, today=None, within_days=60):
        d = self.get()
        today = _date(today or _dt.date.today())
        end = today + _dt.timedelta(days=within_days)
        out = []
        for c in d["coverages"]:
            if c.get("renewal_date"):
                r = _date(c["renewal_date"])
                if today <= r <= end:
                    out.append({"date": r.isoformat(), "what": "%s renews" % c.get("plan", "A plan"),
                                "action": "Check next year's premium, network and drug list before it renews."})
            oe = c.get("open_enrolment") or {}
            for key, label in (("start", "opens"), ("end", "closes")):
                if oe.get(key):
                    x = _date(oe[key])
                    if today <= x <= end:
                        out.append({"date": x.isoformat(), "what": "Open enrolment for %s %s" % (
                            c.get("plan", "a plan"), label), "action": "Dates come from your plan/employer; "
                                                                       "verify them."})
        for p in d["people"]:
            if p.get("dob") and p.get("relationship") in ("child", "dependent", "dependant"):
                dob = _date(p["dob"])
                try:
                    b26 = dob.replace(year=dob.year + DEPENDENT_AGE_LIMIT)
                except ValueError:
                    b26 = _dt.date(dob.year + DEPENDENT_AGE_LIMIT, 3, 1)
                if today <= b26 <= end + _dt.timedelta(days=60):
                    out.append({"date": b26.isoformat(), "what": "%s turns 26" % p.get("name", p["id"]),
                                "action": "Dependent coverage under a parent's plan generally ends at 26 (ACA, "
                                          "45 CFR 147.120); losing it opens a special enrolment window."})
            if self.kind == "business" and p.get("hire_date") and p.get("status", "pending") == "pending":
                wait = int(d.get("waiting_period_days") or p.get("waiting_period_days") or MAX_WAITING_DAYS)
                elig = _date(p["hire_date"]) + _dt.timedelta(days=min(wait, MAX_WAITING_DAYS))
                if elig <= end:
                    out.append({"date": elig.isoformat(), "what": "%s becomes eligible" % p.get("name", p["id"]),
                                "action": "Coverage must be able to start within 90 days of eligibility "
                                          "(29 CFR 2590.715-2708)."})
        for e in d["events"]:
            kind = e.get("type")
            if kind not in LIFE_EVENTS or not e.get("date"):
                continue
            mk, grp = LIFE_EVENTS[kind]
            base = _date(e["date"])
            for days, label in ((grp, "employer plan"), (mk, "Marketplace")):
                if days:
                    x = base + _dt.timedelta(days=days)
                    if x >= today:
                        out.append({"date": x.isoformat(), "what": "Last day of %s special enrolment after %s" % (
                            label, kind.replace("_", " ")), "action": LIFE_EVENT_CITE})
        out.sort(key=lambda x: x["date"])
        res = {"notice": NOTICE, "no_action": NO_ACTION_NOTE, "today": today.isoformat(), "upcoming": out,
               "coverage": self.coverage_matrix()}
        if self.kind == "business":
            people = d["people"]
            res["employees"] = {"total": len(people),
                                "enrolled": sum(1 for p in people if p.get("status") == "enrolled"),
                                "waived": sum(1 for p in people if p.get("status") == "waived"),
                                "pending": sum(1 for p in people if p.get("status", "pending") == "pending")}
            res["business_note"] = ("Employers with 50 or more full-time-equivalent employees have extra ACA "
                                    "duties (IRC 4980H) and reporting. Covenant does not give tax, ERISA or "
                                    "compliance advice; ask a licensed broker or benefits attorney.")
        return res


# ======================================================================================================
# Export
# ======================================================================================================
def export_pdf(title, text):
    """-> (bytes, mime, filename). PDF via polyglot_engine; plain text if it is not installed. The notice is
    always included."""
    if NOTICE not in text:
        text = text + "\n\n" + NOTICE
    safe = re.sub(r"[^A-Za-z0-9 _-]", "_", str(title or "Covenant coverage"))[:60].strip() or "coverage"
    try:
        pe = importlib.import_module("polyglot_engine")
        pdf = pe.PolyglotPdf(str(title or "Coverage"), author=APP_NAME, subject="Coverage navigator")
        pdf.heading(str(title or "Coverage"))
        for para in text.split("\n"):
            if para.strip():
                pdf.paragraph(para)
        return pdf.build(), "application/pdf", safe + ".pdf"
    except Exception:
        return text.encode("utf-8"), "text/plain", safe + ".txt"


# ======================================================================================================
# clinic_app mount
# ======================================================================================================
def _host_module(app):
    mod = sys.modules.get(type(app).__module__) if app is not None else None
    if mod is None or not hasattr(mod, "ROUTES"):
        mod = sys.modules.get("clinic_app") or importlib.import_module("clinic_app")
    return mod


def mount(app, routes=None):
    """Register /api/coverage/* in clinic_app's ROUTES (see module docstring). Returns the route table."""
    host = _host_module(app)
    routes = host.ROUTES if routes is None else routes
    HttpError = getattr(host, "HttpError", CoverageError)
    holder = {}

    def store():
        clinic = getattr(app, "clinic", app)
        vault = getattr(clinic, "vault", None)
        if holder.get("key") != id(vault) or "store" not in holder:
            holder["store"] = JsonStore(os.path.join(clinic.data_root, "coverage" if vault is not None
                                                     else "coverage_no_vault"), vault)
            holder["key"] = id(vault)
        return holder["store"]

    def guard(perm, fn):
        def run(c, u, q, b):
            c.require(u, perm)
            try:
                return fn(c, u, q, b)
            except CoverageError as e:
                raise HttpError(400, str(e)) from None
        return run

    def r_info(c, u, q, b):
        return {"notice": NOTICE, "no_action": NO_ACTION_NOTE,
                "jurisdictions": {k: v["label"] for k, v in JURISDICTIONS.items()},
                "service_types": list(SERVICE_TYPES), "letter_kinds": {k: list(v) for k, v in LETTER_FIELDS.items()},
                "carc": CARC, "life_events": list(LIFE_EVENTS)}

    def r_compare(c, u, q, b):
        return {"worksheet": compare_plans(b.get("plans") or [], b.get("scenarios") or [])}

    def r_eob(c, u, q, b):
        return {"eob": explain_eob(b.get("eob") or {})}

    def r_letter(c, u, q, b):
        return {"letter": draft_letter(b.get("kind"), b.get("fields") or {}, b.get("jurisdiction") or "us_group",
                                       b.get("overrides"))}

    def consent_gate(subject):
        """Patient-linked coverage records are kept only while consent_center says coverage_help is active.
        Installed-but-unattached consent_center means nothing can be confirmed: refused."""
        if not PATIENT_ID.match(str(subject or "")):
            return
        clinic = getattr(app, "clinic", app)
        cc = getattr(clinic, "consent_center", None)
        if cc is None:
            try:
                importlib.import_module("consent_center")
            except Exception:
                return          # no consent system installed: staff follow their own policy
            raise HttpError(403, "Coverage records for a patient need the patient's 'coverage_help' consent; "
                                 "consent_center is installed but not attached to this clinic.")
        try:
            ok = cc.is_active(subject, CONSENT_PURPOSE) is True
        except Exception:
            ok = False
        if not ok:
            raise HttpError(403, "Coverage records for this patient need their 'coverage_help' consent first.")

    def r_claims(c, u, q, b):
        act = b.get("action") or "list"
        consent_gate(b.get("subject"))
        t = ClaimTracker(store(), b.get("subject") or "household")
        if act == "list":
            return {"tracker": t.summary()}
        c.require(u, "demo.write")
        if act == "add":
            return {"claim": t.add(b.get("claim") or {}), "notice": NOTICE}
        if act == "update":
            return {"claim": t.update(b.get("id"), b.get("status"), b.get("date"), b.get("note", ""),
                                      **(b.get("amounts") or {})), "notice": NOTICE}
        raise HttpError(400, "action is list, add or update")

    def r_organiser(c, u, q, b):
        consent_gate(b.get("subject"))
        o = BenefitsOrganiser(store(), b.get("subject") or "household", b.get("kind") or "family")
        act = b.get("action") or "upcoming"
        if act == "get":
            return {"organiser": o.get(), "notice": NOTICE}
        if act == "upcoming":
            return {"organiser": o.upcoming(b.get("today"), int(b.get("within_days") or 60))}
        if act == "save":
            c.require(u, "demo.write")
            return {"organiser": o.save(b.get("data") or {}), "notice": NOTICE}
        raise HttpError(400, "action is get, upcoming or save")

    def r_pdf(c, u, q, b):
        data, _mime, name = export_pdf(b.get("title") or "Coverage", str(b.get("text") or ""))
        return ("file", name, data)

    routes[("GET", "/api/coverage/info")] = guard("demo.read", r_info)
    routes[("POST", "/api/coverage/compare")] = guard("demo.read", r_compare)
    routes[("POST", "/api/coverage/eob")] = guard("demo.read", r_eob)
    routes[("POST", "/api/coverage/letter")] = guard("demo.read", r_letter)
    routes[("POST", "/api/coverage/claims")] = guard("demo.read", r_claims)
    routes[("POST", "/api/coverage/organiser")] = guard("demo.read", r_organiser)
    routes[("POST", "/api/coverage/pdf")] = guard("demo.read", r_pdf)
    cc = getattr(getattr(app, "clinic", app), "consent_center", None)
    if cc is not None:
        try:   # withdrawal and erasure reach our records too; subject-access export includes them
            if callable(getattr(cc, "on_withdraw", None)):
                cc.on_withdraw(lambda sid, key, actor: forget_subject(store(), sid) if key == CONSENT_PURPOSE
                               else None)
            if callable(getattr(cc, "register_holder", None)):
                cc.register_holder("coverage_navigator", export=lambda sid: export_subject(store(), sid),
                                   erase=lambda sid, actor: forget_subject(store(), sid))
        except Exception:
            pass
    return routes


if __name__ == "__main__":
    print(draft_letter("appeal_internal", {"member_name": "Sam Example", "denial_date": "2026-11-01"})["text"])
