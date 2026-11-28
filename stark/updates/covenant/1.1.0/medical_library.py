#!/usr/bin/env python3
"""Stark Covenant - Medical Library (medical_library.py). Standard library only, Python 3.10+.

A clinician REFERENCE library. Off until an admin turns it on. It looks things up in public sources whose terms
allow it, keeps an offline cache, and shows on every view where each item came from, under which licence,
when it was retrieved, and this line:

    Reference only: not medical advice; clinical decisions need clinician judgement.

WHAT IT NEVER DOES
  * No diagnosis, no treatment suggestion, no triage. No function here takes a patient's signs, symptoms or
    record. It only answers lookups a clinician types (a topic, a drug name, a code) and shows what the source
    says, with a link to it. It has no access to the record API and never writes into a chart.
  * No patient data leaves the computer: search words pass research_desk's Scrubber (identifiers, and this
    clinic's own patient names / dates of birth / record numbers, are removed) and every request passes its
    OutboundGuard (https only, hosts listed below only, refused if anything identifying is left).

SOURCES (terms checked 2026-11-19)
  medlineplus          MedlinePlus health topics, NLM web service wsearch.nlm.nih.gov (85 requests/min).
                       Health topic summaries are US government works, not copyrighted; credit "Courtesy of
                       MedlinePlus from the National Library of Medicine". medlineplus.gov/about/using/usingcontent
  medlineplus_connect  MedlinePlus Connect, connect.medlineplus.gov (100 requests/min), by ICD-10-CM, RxNorm or
                       LOINC code. Only titles and links are kept: drug pages behind those links carry ASHP
                       monographs, which are copyrighted and must not be ingested.
  openfda              FDA drug labels, api.fda.gov/drug/label.json. Public domain, CC0 (open.fda.gov/terms);
                       credit "Data provided by the U.S. Food and Drug Administration (https://open.fda.gov)".
  rxnav                RxNorm names and RxCUIs from NLM RxNav, rxnav.nlm.nih.gov (20 requests/s). RxNav serves
                       "non-proprietary content from RxNorm"; only candidates whose source is RXNORM are shown.
                       Statement: "This product uses publicly available data from the U.S. National Library of
                       Medicine (NLM). NLM is not responsible for the product and does not endorse or recommend
                       this or any other product." (lhncbc.nlm.nih.gov/RxNav/TermsofService.html)
  pubmed               PubMed citations via NCBI E-utilities (3 requests/s, tool + email sent). NLM does not
                       claim copyright on abstracts, but publishers or authors may: the library stores only the
                       citation, a 300-character excerpt and the link. The full abstract response is never cached.
  icd10cm              ICD-10-CM code titles from the CDC/NCHS files the clinic downloads and imports
                       (ftp.cdc.gov/pub/Health_Statistics/NCHS/Publications/ICD10CM/). Offline lookup only.
  cdc_links            A short list of CDC clinical guidance pages, linked (content stays on cdc.gov).

EXCLUDED: SNOMED CT (needs an affiliate licence outside member-country use), UpToDate, DynaMed and other
subscription references, the A.D.A.M. encyclopedia and ASHP drug monographs shown on MedlinePlus (licensed
content), and WHO material under non-commercial licences (linked only, never copied).

STORAGE: <data>/library (vault-encrypted when the vault is open) or <data>/library_no_vault.
MOUNTING: see release/MEDICAL_LIBRARY_PATCH.md (three small pieces in clinic_app, owned by the UI team).
"""
from __future__ import annotations

import html
import io
import json
import os
import re
import threading
import time
import traceback
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
import research_desk as rd  # noqa: E402  (reused: store, scrubber, outbound guard, fetcher, rate limiter, log)

VERSION = "1.0.0"
PREFIX = "/library"
DISCLAIMER = "Reference only: not medical advice; clinical decisions need clinician judgement."
EXCERPT_CHARS = 300
DEFAULT_TOOL = "StarkCovenantLibrary"
WSEARCH = "https://wsearch.nlm.nih.gov/ws/query"
CONNECT = "https://connect.medlineplus.gov/service"
OPENFDA_LABEL = "https://api.fda.gov/drug/label.json"
RXNAV = "https://rxnav.nlm.nih.gov/REST/"
CODE_SYSTEMS = {"icd10cm": ("2.16.840.1.113883.6.90", r"^[A-Za-z][0-9][0-9A-Za-z](?:\.?[0-9A-Za-z]{1,4})?$"),
                "rxnorm": ("2.16.840.1.113883.6.88", r"^[0-9]{1,10}$"),
                "loinc": ("2.16.840.1.113883.6.1", r"^[0-9]{1,7}-[0-9]$")}

SOURCES = {
    "medlineplus": {
        "name": "MedlinePlus health topics (U.S. National Library of Medicine)", "hosts": ["wsearch.nlm.nih.gov"],
        "min_interval_s": 0.75, "home": "https://medlineplus.gov/",
        "licence": "Public domain (U.S. government work): MedlinePlus health topic summaries",
        "licence_url": "https://medlineplus.gov/about/using/usingcontent/",
        "attribution": "Courtesy of MedlinePlus from the National Library of Medicine"},
    "medlineplus_connect": {
        "name": "MedlinePlus Connect (U.S. National Library of Medicine)", "hosts": ["connect.medlineplus.gov"],
        "min_interval_s": 0.65, "home": "https://medlineplus.gov/medlineplus-connect/",
        "licence": "Links only. Linked pages may hold licensed content (A.D.A.M., ASHP) that is not copied here",
        "licence_url": "https://medlineplus.gov/about/using/usingcontent/",
        "attribution": "Source: MedlinePlus, National Library of Medicine"},
    "openfda": {
        "name": "FDA drug labels via openFDA", "hosts": ["api.fda.gov"], "min_interval_s": 0.25,
        "home": "https://open.fda.gov/apis/drug/label/",
        "licence": "Public domain, CC0 1.0 (unless openFDA marks an item otherwise)",
        "licence_url": "https://open.fda.gov/terms/",
        "attribution": "Data provided by the U.S. Food and Drug Administration (https://open.fda.gov)"},
    "rxnav": {
        "name": "RxNorm via NLM RxNav", "hosts": ["rxnav.nlm.nih.gov"], "min_interval_s": 0.1,
        "home": "https://lhncbc.nlm.nih.gov/RxNav/",
        "licence": "Non-proprietary RxNorm content (source RXNORM only); no licence needed for the RxNav APIs",
        "licence_url": "https://lhncbc.nlm.nih.gov/RxNav/TermsofService.html",
        "attribution": ("This product uses publicly available data from the U.S. National Library of Medicine (NLM). "
                        "NLM is not responsible for the product and does not endorse or recommend this or any other "
                        "product.")},
    "pubmed": {
        "name": "PubMed citations (NCBI E-utilities)", "hosts": ["eutils.ncbi.nlm.nih.gov"], "min_interval_s": 0.4,
        "min_interval_with_key_s": 0.12, "home": "https://pubmed.ncbi.nlm.nih.gov/",
        "licence": ("Citation data from NLM. Abstracts may be copyrighted by publishers or authors: only a short "
                    "excerpt is shown; read the full abstract at the link"),
        "licence_url": "https://www.ncbi.nlm.nih.gov/home/about/policies/",
        "attribution": "Source: PubMed, National Library of Medicine"},
    "icd10cm": {
        "name": "ICD-10-CM (CDC National Center for Health Statistics)", "hosts": [], "home":
        "https://www.cdc.gov/nchs/icd/icd-10-cm/files.html",
        "licence": ("ICD-10-CM files published free of charge by CDC/NCHS (U.S. government); ICD-10 itself is a WHO "
                    "classification adapted by NCHS. Imported by the clinic, looked up offline"),
        "licence_url": "https://www.cdc.gov/nchs/icd/icd-10-cm/files.html",
        "attribution": "Source: ICD-10-CM, CDC National Center for Health Statistics"},
    "cdc_links": {
        "name": "CDC clinical guidance pages (links)", "hosts": [], "home": "https://www.cdc.gov/",
        "licence": "Linked only; the pages stay on cdc.gov under CDC's own terms",
        "licence_url": "https://www.cdc.gov/other/agencymaterials.html",
        "attribution": "Source: U.S. Centers for Disease Control and Prevention"},
}
CDC_LINKS = (   # each URL opened and its title checked on 2026-11-19
    {"title": "Healthcare Professionals: Immunization Schedules", "topic": "immunization vaccines schedules",
     "url": "https://www.cdc.gov/vaccines/hcp/imz-schedules/index.html", "checked": "2026-11-19"},
    {"title": "Sexually Transmitted Infections Treatment Guidelines, 2021", "topic": "sti std sexually transmitted",
     "url": "https://www.cdc.gov/std/treatment-guidelines/default.htm", "checked": "2026-11-19"},
    {"title": "2022 CDC Clinical Practice Guideline at a Glance (opioid prescribing)", "topic": "opioids pain overdose",
     "url": "https://www.cdc.gov/overdose-prevention/hcp/clinical-guidance/index.html", "checked": "2026-11-19"},
)
EXCLUDED = (
    {"source": "SNOMED CT", "why": "Needs a SNOMED International affiliate licence outside member-country use."},
    {"source": "UpToDate, DynaMed and other subscription references", "why": "Commercial licence we do not hold."},
    {"source": "A.D.A.M. Medical Encyclopedia and ASHP drug monographs (shown on MedlinePlus)",
     "why": "Copyrighted; MedlinePlus says they may not be ingested into health IT systems without a licence."},
    {"source": "WHO content under non-commercial (CC BY-NC-SA) licences", "why": "Linked only, never copied or cached."},
)
LABEL_SECTIONS = (("boxed_warning", "Boxed warning"), ("indications_and_usage", "Indications and usage"),
                  ("dosage_and_administration", "Dosage and administration"), ("contraindications", "Contraindications"),
                  ("warnings_and_cautions", "Warnings and precautions"), ("warnings", "Warnings"),
                  ("adverse_reactions", "Adverse reactions"), ("drug_interactions", "Drug interactions"),
                  ("use_in_specific_populations", "Use in specific populations"), ("pregnancy", "Pregnancy"))
SECTION_CHARS = 1500
DEFAULT_SETTINGS = {"enabled": False, "offline": False, "cache_ttl_hours": 24, "contact_email": "",
                    "tool_name": DEFAULT_TOOL, "ncbi_api_key": ""}
CATALOGUE = {"sources": {k: {"hosts": v["hosts"], "min_interval_s": v.get("min_interval_s", 1.0),
                             **({"min_interval_with_key_s": v["min_interval_with_key_s"]}
                                if "min_interval_with_key_s" in v else {})}
                         for k, v in SOURCES.items()}}


class LibraryError(rd.DeskError):
    pass


def _plain(s, limit=None):
    s = html.unescape(re.sub(r"<[^>]+>", " ", str(s or "")))
    s = re.sub(r"\s+", " ", s).strip()
    if limit and len(s) > limit:
        return s[:limit].rsplit(" ", 1)[0] + "…"
    return s


def validate_settings(doc, base=None):
    out = dict(base or DEFAULT_SETTINGS)
    if not isinstance(doc, dict):
        raise ValueError("settings must be an object")
    for k in ("enabled", "offline"):
        if k in doc:
            out[k] = bool(doc[k])
    if "cache_ttl_hours" in doc:
        out["cache_ttl_hours"] = max(1, min(720, int(doc["cache_ttl_hours"])))
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
    return out


# ======================================================================================================
# ICD-10-CM files (CDC/NCHS): "icd10cm-codes-YYYY.txt" (code, space, title) or the order file
# "icd10cm-order-YYYY.txt" (order 5, code 7, billable flag 1, short title 60, long title), plain or zipped.
# ======================================================================================================
def parse_icd10cm(text):
    codes = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        m = re.match(r"^(\d{5}) ([A-Z0-9]{3,7})\s* ([01]) (.{60}) ?(.*)$", line)
        if m:
            codes[m.group(2)] = {"title": (m.group(5) or m.group(4)).strip(), "billable": m.group(3) == "1"}
            continue
        m = re.match(r"^([A-Z][0-9][0-9A-Z]{1,5})\s+(\S.*)$", line)
        if m:
            codes[m.group(1)] = {"title": m.group(2).strip(), "billable": None}
    return codes


def format_code(c):
    return c if len(c) <= 3 else c[:3] + "." + c[3:]


# ======================================================================================================
# The library
# ======================================================================================================
class Library:
    """All library logic, no HTTP, no access to clinical records."""

    def __init__(self, data_dir, vault=None, identifiers=None, transport=None, limiter=None, sleep=time.sleep,
                 health=None):
        self.store = rd.DeskStore(data_dir, vault)
        self.log = rd.ResearchLog(self.store)
        self.catalogue = CATALOGUE
        self.scrubber = rd.Scrubber(identifiers)
        self.guard = rd.OutboundGuard(self.scrubber, {h for s in SOURCES.values() for h in s["hosts"]},
                                      lambda: self.settings().get("contact_email", ""))
        self.fetcher = rd.Fetcher(self.store, self.guard, self.catalogue, self.settings, transport, limiter, sleep)
        self.fetcher.user_agent = lambda: "%s/%s (Stark Covenant medical library; Python urllib%s)" % (
            self.settings().get("tool_name") or DEFAULT_TOOL, VERSION,
            ("; mailto:" + self.settings()["contact_email"]) if self.settings().get("contact_email") else "")
        self.health = health or rd.log
        self._icd = None
        self._lock = threading.RLock()

    # --- settings -----------------------------------------------------------------------------------
    def settings(self):
        return validate_settings(self.store.load("settings", {}), DEFAULT_SETTINGS)

    def update_settings(self, doc, actor):
        with self._lock:
            new = validate_settings(doc, self.settings())
            self.store.save("settings", new)
            self.log.append("settings", {"actor": actor, "at": rd.now_iso(),
                                         "changed": sorted(k for k in doc if k in DEFAULT_SETTINGS)})
            return self.public_settings()

    def public_settings(self):
        s = self.settings()
        return {"enabled": s["enabled"], "offline": s["offline"], "cache_ttl_hours": s["cache_ttl_hours"],
                "contact_email": s["contact_email"], "ncbi_api_key_set": bool(s["ncbi_api_key"])}

    def about(self):
        """The source and licence list the page shows before anything is searched."""
        icd = self._icd_doc(meta_only=True)
        return {"disclaimer": DISCLAIMER, "settings": self.public_settings(), "excluded": list(EXCLUDED),
                "sources": {k: {x: v[x] for x in ("name", "home", "licence", "licence_url", "attribution")}
                            for k, v in SOURCES.items()},
                "icd10cm": {"imported": bool(icd), "version": (icd or {}).get("version"),
                            "imported_at": (icd or {}).get("imported_at"), "codes": (icd or {}).get("count", 0)}}

    # --- helpers --------------------------------------------------------------------------------------
    def _need_enabled(self):
        if not self.settings()["enabled"]:
            raise LibraryError(403, "The reference library is off. An administrator can turn it on in its settings "
                                    "(it then sends typed search words, without identifiers, to the listed sources).")

    def _term(self, text, maxlen=100):
        clean, removed = self.scrubber.scrub(text, maxlen=maxlen)
        if not clean:
            raise ValueError("type a search word" + (" (identifiers were removed)" if removed else ""))
        return clean, sorted(set(removed))

    def _view(self, source, query, items, retrieved, removed=(), from_cache=False, stale=False, error=None, **extra):
        s = SOURCES[source]
        out = {"kind": source, "disclaimer": DISCLAIMER, "query": query, "removed_identifiers": list(removed),
               "source": {"key": source, "name": s["name"], "home": s["home"], "licence": s["licence"],
                          "licence_url": s["licence_url"], "attribution": s["attribution"]},
               "retrieved": retrieved, "from_cache": bool(from_cache), "stale": bool(stale), "items": items,
               "error": error}
        out.update(extra)
        return out

    def _fetch_uncached(self, source, url, user_terms=()):
        """For responses that must not be stored (full PubMed abstracts): guard, rate limit, fetch, keep nothing."""
        self.guard.check(url, user_terms)
        if self.settings().get("offline"):
            return None, "offline mode"
        self.fetcher.limiter.wait(urllib.parse.urlsplit(url).hostname, self.fetcher._interval(source))
        try:
            status, _ct, body = self.fetcher.transport.get(url, {"User-Agent": self.fetcher.user_agent(),
                                                                 "Accept": "application/xml"}, timeout=30)
        except rd.DeskError:
            raise
        except Exception as e:
            return None, "%s: %s" % (type(e).__name__, str(e)[:200])
        return (body, None) if status == 200 else (None, "HTTP %s" % status)

    # --- MedlinePlus ----------------------------------------------------------------------------------
    def health_topics(self, term, actor, lang="en"):
        self._need_enabled()
        q, removed = self._term(term)
        db = "healthTopicsSpanish" if lang == "es" else "healthTopics"
        run = rd.Run(self, "library_medlineplus", actor, {"term": q, "removed": removed})
        f = run.fetch("wsearch", "medlineplus", WSEARCH + "?" + urllib.parse.urlencode(
            {"db": db, "term": q, "retmax": 10, "rettype": "brief"}), [q])
        items = []
        if f.ok:
            root = ET.fromstring(f.body)
            for doc in root.iter("document"):
                c = {e.get("name"): "".join(e.itertext()) for e in doc.findall("content")}
                items.append({"title": _plain(c.get("title")), "url": doc.get("url", ""),
                              "summary": _plain(c.get("FullSummary"), 2000), "snippet": _plain(c.get("snippet"), 300)})
        out = self._view("medlineplus", q, items, f.fetched_at, removed, f.from_cache, f.stale,
                         None if f.ok else f.error)
        run.finish(out)
        return out

    def connect(self, system, code, actor, lang="en"):
        self._need_enabled()
        if system not in CODE_SYSTEMS:
            raise ValueError("code system must be icd10cm, rxnorm or loinc")
        oid, rx = CODE_SYSTEMS[system]
        code = str(code or "").strip().upper()
        if not re.match(rx, code):
            raise ValueError("that is not a valid %s code" % system)
        run = rd.Run(self, "library_connect", actor, {"system": system, "code": code})
        f = run.fetch("connect", "medlineplus_connect", CONNECT + "?" + urllib.parse.urlencode(
            {"mainSearchCriteria.v.cs": oid, "mainSearchCriteria.v.c": code, "knowledgeResponseType": "application/json",
             "informationRecipient.languageCode.c": "es" if lang == "es" else "en"}))
        items = []
        if f.ok:
            feed = json.loads(f.body.decode("utf-8")).get("feed", {})
            for e in feed.get("entry", []) or []:
                links = e.get("link") or []
                links = links if isinstance(links, list) else [links]
                href = next((l.get("href") for l in links if isinstance(l, dict) and l.get("href")), "")
                title = e.get("title")
                items.append({"title": _plain(title.get("_value") if isinstance(title, dict) else title), "url": href})
        out = self._view("medlineplus_connect", "%s %s" % (system, code), items, f.fetched_at, (), f.from_cache,
                         f.stale, None if f.ok else f.error,
                         note="Titles and links only. Open the link to read the page on medlineplus.gov.")
        run.finish(out)
        return out

    # --- openFDA drug labels -------------------------------------------------------------------------
    def drug_label(self, name, actor):
        self._need_enabled()
        d, removed = self._term(name, 60)
        d = re.sub(r"[^A-Za-z0-9 -]", "", d).strip().lower()
        if not d:
            raise ValueError("type a drug name")
        run = rd.Run(self, "library_label", actor, {"drug": d, "removed": removed})
        f = run.fetch("label", "openfda", OPENFDA_LABEL + "?" + urllib.parse.urlencode(
            {"search": 'openfda.generic_name:"%s" openfda.brand_name:"%s"' % (d, d), "limit": 3}), [d])
        items = []
        if f.ok:
            for lab in json.loads(f.body.decode("utf-8")).get("results", []):
                of = lab.get("openfda", {})
                secs = []
                for key, title in LABEL_SECTIONS:
                    v = lab.get(key)
                    if v:
                        txt = " ".join(v) if isinstance(v, list) else str(v)
                        secs.append({"section": title, "text": txt[:SECTION_CHARS], "truncated": len(txt) > SECTION_CHARS})
                sid = lab.get("set_id", "")
                items.append({"brand_name": of.get("brand_name", [])[:3], "generic_name": of.get("generic_name", [])[:3],
                              "manufacturer": of.get("manufacturer_name", [])[:2], "set_id": sid,
                              "effective_time": lab.get("effective_time", ""), "sections": secs,
                              "url": ("https://dailymed.nlm.nih.gov/dailymed/lookup.cfm?setid=" + urllib.parse.quote(sid))
                              if sid else ""})
        error = None if f.ok or f.status == 404 else f.error
        out = self._view("openfda", d, items, f.fetched_at, removed, f.from_cache, f.stale, error,
                         note="Label text as published by the manufacturer; sections are cut at %d characters. Read "
                              "the full label at DailyMed." % SECTION_CHARS)
        run.finish(out)
        return out

    # --- RxNorm (RxNav) -------------------------------------------------------------------------------
    def rxnorm(self, name, actor):
        self._need_enabled()
        d, removed = self._term(name, 60)
        run = rd.Run(self, "library_rxnorm", actor, {"drug": d, "removed": removed})
        f = run.fetch("approximate", "rxnav", RXNAV + "approximateTerm.json?" + urllib.parse.urlencode(
            {"term": d, "maxEntries": 10}), [d])
        items, seen = [], set()
        if f.ok:
            for c in json.loads(f.body.decode("utf-8")).get("approximateGroup", {}).get("candidate") or []:
                if c.get("source") != "RXNORM" or not c.get("rxcui") or c["rxcui"] in seen:
                    continue            # only RxNorm's own (non-proprietary) names
                seen.add(c["rxcui"])
                items.append({"rxcui": c["rxcui"], "name": c.get("name", ""), "rank": c.get("rank", ""),
                              "url": "https://mor.nlm.nih.gov/RxNav/search?searchBy=RXCUI&searchTerm=" + c["rxcui"]})
        out = self._view("rxnav", d, items, f.fetched_at, removed, f.from_cache, f.stale, None if f.ok else f.error,
                         note="Spelling candidates for you to choose from; the library never picks one.")
        run.finish(out)
        return out

    # --- PubMed -----------------------------------------------------------------------------------------
    def pubmed(self, term, actor, retmax=10):
        self._need_enabled()
        q, removed = self._term(term, 150)
        st = self.settings()
        run = rd.Run(self, "library_pubmed", actor, {"term": q, "removed": removed})
        f = run.fetch("esearch", "pubmed", rd.eutils_url("esearch.fcgi", st, {"db": "pubmed", "term": q, "retmode": "json",
                                                                              "retmax": max(1, min(20, int(retmax))),
                                                                              "sort": "relevance"}), [q])
        if not f.ok:
            out = self._view("pubmed", q, [], f.fetched_at, removed, error=f.error)
            run.finish(out)
            return out
        ids = rd.parse_esearch(f.body)["ids"]
        items, err, cached_any = [], None, f.from_cache
        if ids:
            s = run.fetch("esummary", "pubmed", rd.eutils_url("esummary.fcgi", st, {"db": "pubmed", "id": ",".join(ids),
                                                                                    "retmode": "json"}))
            meta = rd.parse_esummary(s.body) if s.ok else {}
            excerpts = self.store.load("pubmed_excerpts", {})
            missing = [i for i in ids if i not in excerpts]
            if missing:
                body, err = self._fetch_uncached("pubmed", rd.eutils_url("efetch.fcgi", st, {
                    "db": "pubmed", "id": ",".join(missing), "retmode": "xml"}))
                if body:
                    for pmid, rec in rd.parse_efetch(body).items():
                        text = " ".join(x["text"] for x in rec.get("abstract_sections", []))
                        excerpts[pmid] = _plain(text, EXCERPT_CHARS)      # only the excerpt is kept
                    self.store.save("pubmed_excerpts", excerpts)
                    del body
            for i in ids:
                m = meta.get(i, {"pmid": i})
                items.append({"pmid": i, "title": m.get("title", ""), "journal": m.get("journal", ""),
                              "year": m.get("year"), "authors": (m.get("authors") or [])[:3], "doi": m.get("doi", ""),
                              "pubtypes": m.get("pubtypes", []), "excerpt": excerpts.get(i, ""),
                              "url": "https://pubmed.ncbi.nlm.nih.gov/%s/" % i})
            cached_any = cached_any and s.from_cache
        out = self._view("pubmed", q, items, f.fetched_at, removed, cached_any, f.stale, err,
                         note="Excerpt of at most %d characters. Abstracts may be copyrighted by their publishers; "
                              "read the full abstract at PubMed." % EXCERPT_CHARS)
        run.finish(out)
        return out

    # --- ICD-10-CM (offline) ------------------------------------------------------------------------------
    def import_icd10cm(self, path_or_bytes, version, actor):
        """Import a CDC/NCHS ICD-10-CM codes or order file (.txt, or the .zip that holds it)."""
        raw = path_or_bytes if isinstance(path_or_bytes, (bytes, bytearray)) else open(path_or_bytes, "rb").read()
        text = None
        if raw[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                names = [n for n in z.namelist() if n.lower().endswith(".txt") and
                         re.search(r"order|codes", os.path.basename(n), re.I) and "addenda" not in n.lower()]
                names.sort(key=lambda n: (0 if "order" in n.lower() else 1, n))
                if not names:
                    raise ValueError("no ICD-10-CM codes or order .txt file in this zip")
                text = z.read(names[0]).decode("latin-1")
        else:
            text = bytes(raw).decode("latin-1")
        codes = parse_icd10cm(text)
        if len(codes) < 1:
            raise ValueError("no ICD-10-CM codes found in this file")
        doc = {"version": re.sub(r"[^\w .-]", "", str(version or ""))[:40] or "unknown", "imported_at": rd.now_iso(),
               "count": len(codes), "codes": codes}
        with self._lock:
            self.store.save("icd_codes", doc)
            self._icd = doc
        self.log.append("library_icd_import", {"actor": actor, "at": doc["imported_at"], "codes": len(codes),
                                               "version": doc["version"]})
        return {"imported": len(codes), "version": doc["version"]}

    def _icd_doc(self, meta_only=False):
        with self._lock:
            if self._icd is None:
                self._icd = self.store.load("icd_codes", None)
            return self._icd

    def icd10cm(self, query, actor, limit=25):
        self._need_enabled()
        doc = self._icd_doc()
        if not doc:
            raise LibraryError(409, "No ICD-10-CM file imported yet. Download the code file from CDC and import it.")
        q = str(query or "").strip()
        if not q:
            raise ValueError("type a code or words from a code title")
        codes, items = doc["codes"], []
        key = q.upper().replace(".", "")
        if re.match(r"^[A-Z][0-9][0-9A-Z]{0,5}$", key):
            for c in sorted(codes):
                if c.startswith(key):
                    items.append(c)
                    if len(items) >= limit:
                        break
        else:
            words = [w for w in re.findall(r"[a-z0-9]+", q.lower()) if len(w) > 1][:8]
            for c in sorted(codes):
                t = codes[c]["title"].lower()
                if words and all(w in t for w in words):
                    items.append(c)
                    if len(items) >= limit:
                        break
        out = self._view("icd10cm", q, [{"code": format_code(c), "title": codes[c]["title"],
                                         "billable": codes[c]["billable"]} for c in items],
                         doc["imported_at"], version=doc["version"],
                         note="Code lookup in the imported file. Choosing a code is the clinician's decision.")
        self.log.append("library_icd", {"actor": actor, "at": rd.now_iso(), "results": len(items)})
        return out

    # --- CDC links ---------------------------------------------------------------------------------------
    def cdc_links(self, topic=""):
        self._need_enabled()
        words = re.findall(r"[a-z]+", str(topic or "").lower())
        items = [dict(l) for l in CDC_LINKS if not words or any(w in (l["title"] + " " + l["topic"]).lower() for w in words)]
        return self._view("cdc_links", topic, items, CDC_LINKS[0]["checked"],
                          note="Links open cdc.gov. The library copies nothing from these pages.")


# ======================================================================================================
# HTTP (mounted by clinic_app; same session, CSRF and origin checks as the Research Desk)
# ======================================================================================================
def _admin(user):
    return bool(set(user.get("roles", ())) & {"admin", "owner"})


ROUTES = {
    ("GET", "/session"): lambda m, s, u, q, b: {"csrf": s.get("csrf", ""), "admin": _admin(u)},
    ("GET", "/about"): lambda m, s, u, q, b: m.lib.about(),
    ("GET", "/topics"): lambda m, s, u, q, b: m.lib.health_topics(q.get("q", ""), rd._uid(u), q.get("lang", "en")),
    ("GET", "/connect"): lambda m, s, u, q, b: m.lib.connect(q.get("system", ""), q.get("code", ""), rd._uid(u),
                                                             q.get("lang", "en")),
    ("GET", "/label"): lambda m, s, u, q, b: m.lib.drug_label(q.get("q", ""), rd._uid(u)),
    ("GET", "/rxnorm"): lambda m, s, u, q, b: m.lib.rxnorm(q.get("q", ""), rd._uid(u)),
    ("GET", "/pubmed"): lambda m, s, u, q, b: m.lib.pubmed(q.get("q", ""), rd._uid(u)),
    ("GET", "/icd10cm"): lambda m, s, u, q, b: m.lib.icd10cm(q.get("q", ""), rd._uid(u)),
    ("GET", "/cdc"): lambda m, s, u, q, b: m.lib.cdc_links(q.get("q", "")),
}
ADMIN_ROUTES = {
    ("POST", "/settings"): lambda m, s, u, q, b: m.lib.update_settings(b, rd._uid(u)),
}


class LibraryModule(rd.ResearchDeskModule):
    """`if library.handle(self): return` inside the clinic handler."""

    def __init__(self, lib, session_lookup, perm_check=None, health=None, prefix=PREFIX):
        rd.ResearchDeskModule.__init__(self, None, session_lookup, perm_check, health, prefix)
        self.lib = lib

    def _auth_any(self, h):
        try:
            res = self.session_lookup(h)
        except Exception:
            res = None
        if not res:
            raise LibraryError(401, "Please sign in to Stark Covenant first.")
        return res

    def handle(self, h):
        u = urllib.parse.urlsplit(h.path)
        path = u.path
        if not (path == self.prefix or path.startswith(self.prefix + "/")):
            return False
        sub = path[len(self.prefix):] or "/"
        try:
            if h.command not in ("GET", "POST", "HEAD"):
                raise LibraryError(405, "Method not allowed.")
            if sub in ("/", "/index.html"):
                if h.command == "POST":
                    raise LibraryError(405, "Method not allowed.")
                self._send(h, 200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
                return True
            if not sub.startswith("/api/"):
                raise LibraryError(404, "Not found.")
            key = (h.command if h.command != "HEAD" else "GET", sub[4:])
            if key in ADMIN_ROUTES:
                sess, user = self._auth_any(h)
                if not _admin(user):
                    raise LibraryError(403, "Only an administrator can change the library settings.")
            elif key == ("GET", "/session"):
                sess, user = self._auth_any(h)
            else:
                sess, user = self._auth(h)
            body, q = {}, {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
            if h.command == "POST":
                if not self._same_origin(h):
                    raise LibraryError(403, "Cross-origin request refused.")
                tok = h.headers.get("X-CSRF-Token", "")
                import secrets as _s
                if not tok or not _s.compare_digest(tok, str(sess.get("csrf", ""))):
                    raise LibraryError(403, "Missing or wrong CSRF token. Reload the page.")
                body = self._body(h)
            fn = ROUTES.get(key) or ADMIN_ROUTES.get(key)
            if fn is None:
                raise LibraryError(404, "Unknown library API.")
            out = {"ok": True}
            out.update(fn(self, sess, user, q, body))
            self._send(h, 200, out)
        except rd.DeskError as e:
            self._err(h, e.status, e.msg)
        except (ValueError, TypeError, KeyError) as e:
            self._err(h, 400, str(e)[:300] or "bad request")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self.health("medical_library %s %s\n%s" % (h.command, path, traceback.format_exc()))
            try:
                self._err(h, 500, "Something went wrong in the library. Nothing in the records was changed.")
            except Exception:
                pass
        return True


def mount(app, transport=None):
    """Mount on a clinic_app.CovenantApp: sets app.library and returns the module (see MEDICAL_LIBRARY_PATCH.md).
    When the vault opens after start-up (clinic.on_ready), the library is mounted again so its data is encrypted."""
    mod = _make_module(app, transport)
    if not getattr(app, "_library_remount_hooked", False) and hasattr(app.clinic, "on_ready"):
        def remount():
            if app.clinic.vault is not None:
                try:
                    _make_module(app, transport)
                except Exception:
                    app.clinic.health("medical_library re-mount failed\n" + traceback.format_exc())
        app.clinic.on_ready.append(remount)
        app._library_remount_hooked = True
    return mod


def _make_module(app, transport=None):
    clinic = app.clinic
    vault = clinic.vault
    data_dir = os.path.join(clinic.data_root, "library" if vault is not None else "library_no_vault")
    lib = Library(data_dir, vault=vault, identifiers=rd.clinic_identifiers(clinic), transport=transport,
                  health=clinic.health)

    def lookup(h):
        return clinic.session(h._cookie())

    def perm(user):
        try:
            return "clin.read" in clinic.perms(user)
        except Exception:
            return False
    mod = LibraryModule(lib, lookup, perm, clinic.health)
    app.library = mod
    return mod


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Stark Covenant - Reference library</title>
<style>
body{font:16px/1.5 system-ui,sans-serif;margin:0;background:#f6f4ee;color:#18211d}main{max-width:960px;margin:0 auto;padding:16px}
h1{font-size:1.4rem}.notice{background:#fff3cd;border:2px solid #8a6d00;padding:10px 12px;border-radius:6px;font-weight:600}
form{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}label{display:block;font-weight:600}input,select,button{font:inherit;padding:8px;min-height:44px}
button{background:#1f7a5a;color:#fff;border:0;border-radius:6px;padding:8px 16px}:focus{outline:3px solid #c9963b;outline-offset:2px}
.card{background:#fff;border:1px solid #ccd;border-radius:8px;padding:12px;margin:12px 0}.src{font-size:.9rem;color:#334}
.item{border-top:1px solid #dde;padding:8px 0}.muted{color:#556}
</style></head><body><main>
<h1>Stark Covenant - Reference library</h1>
<p class="notice" id="disc">Reference only: not medical advice; clinical decisions need clinician judgement.</p>
<p id="status" class="muted" aria-live="polite"></p>
<form id="f"><div><label for="kind">Look up</label><select id="kind">
<option value="topics">MedlinePlus health topic</option><option value="label">FDA drug label (openFDA)</option>
<option value="rxnorm">RxNorm drug name</option><option value="pubmed">PubMed citations</option>
<option value="icd10cm">ICD-10-CM code or title</option><option value="connect">MedlinePlus Connect by ICD-10-CM code</option>
<option value="cdc">CDC guidance links</option></select></div>
<div><label for="q">Search words or code</label><input id="q" autocomplete="off" maxlength="150"></div>
<div><label>&nbsp;</label><button type="submit">Look up</button></div></form>
<div id="out"></div><div id="about" class="card"></div>
</main><script>
'use strict';
const $=s=>document.querySelector(s);let CSRF='';
function el(t,a,...k){const e=document.createElement(t);for(const[x,v]of Object.entries(a||{}))e.setAttribute(x,v);for(const c of k.flat())if(c!=null)e.append(c.nodeType?c:document.createTextNode(String(c)));return e}
async function api(p){const r=await fetch('/library/api'+p,{credentials:'same-origin'});const j=await r.json();if(!j.ok)throw new Error(j.error||('HTTP '+r.status));return j}
function link(u,t){return u?el('a',{href:u,target:'_blank',rel:'noopener noreferrer'},t||u):''}
function sourceBox(v){const s=v.source;return el('p',{class:'src'},'Source: ',link(s.home,s.name),'. Licence: ',link(s.licence_url,s.licence),
 '. Retrieved: '+(v.retrieved||'?')+(v.from_cache?' (offline copy'+(v.stale?', may be out of date':'')+')':'')+'. ',s.attribution)}
function render(v){const c=el('div',{class:'card'},el('p',{class:'notice'},v.disclaimer),sourceBox(v));
 if(v.removed_identifiers&&v.removed_identifiers.length)c.append(el('p',{class:'muted'},'Removed before sending: '+v.removed_identifiers.join(', ')));
 if(v.note)c.append(el('p',{class:'muted'},v.note));if(v.error)c.append(el('p',null,'Problem: '+v.error));
 if(!v.items.length)c.append(el('p',null,'Nothing found.'));
 for(const it of v.items){const d=el('div',{class:'item'});
  if(v.kind==='icd10cm')d.append(el('strong',null,it.code+' '),it.title,it.billable===false?' (header, not billable)':'');
  else if(v.kind==='openfda'){d.append(el('strong',null,(it.brand_name.join(', ')||it.generic_name.join(', '))),' ',link(it.url,'Full label at DailyMed'),' effective '+(it.effective_time||'?'));
   for(const s of it.sections)d.append(el('details',null,el('summary',null,s.section),el('p',null,s.text+(s.truncated?' …':''))))}
  else if(v.kind==='pubmed')d.append(link(it.url,it.title||('PMID '+it.pmid)),el('div',{class:'muted'},[it.journal,it.year,it.authors.join(', ')].filter(Boolean).join(' · ')),it.excerpt?el('p',null,it.excerpt):'');
  else if(v.kind==='rxnav')d.append(el('strong',null,it.name),' RxCUI '+it.rxcui+' ',link(it.url,'RxNav'));
  else d.append(link(it.url,it.title),it.summary?el('p',null,it.summary):'');
  c.append(d)}
 $('#out').replaceChildren(c)}
$('#f').addEventListener('submit',async ev=>{ev.preventDefault();const k=$('#kind').value,q=$('#q').value;$('#status').textContent='Looking up…';
 try{const p=k==='connect'?'/connect?system=icd10cm&code='+encodeURIComponent(q):'/'+k+'?q='+encodeURIComponent(q);render(await api(p));$('#status').textContent=''}
 catch(e){$('#status').textContent=e.message}});
(async()=>{try{const a=await api('/about');const box=$('#about');box.append(el('h2',null,'Sources and licences'));
 for(const s of Object.values(a.sources))box.append(el('p',{class:'src'},link(s.home,s.name),': ',link(s.licence_url,s.licence)));
 box.append(el('h2',null,'Not included'));for(const x of a.excluded)box.append(el('p',{class:'src'},x.source+': '+x.why));
 if(!a.settings.enabled)$('#status').textContent='The library is off. An administrator can turn it on.'}catch(e){$('#status').textContent=e.message}})();
</script></body></html>"""
