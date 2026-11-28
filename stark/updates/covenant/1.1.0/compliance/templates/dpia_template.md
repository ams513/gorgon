# Data Protection Impact Assessment (DPIA)

> **Template - have a qualified lawyer in your jurisdiction review before use.** Structured on GDPR Art. 35 and
> regulator guidance; usable as a starting point for a HIPAA risk analysis, an LGPD impact report (RIPD), a
> PIPEDA/PHIPA privacy impact assessment, an Australian PIA or a DPDP DPIA. Pre-filled facts describe Stark
> Covenant's design; verify each against your own installation.

Organisation: [CLINIC]   Assessor: [NAME]   DPO / privacy officer consulted: [NAME, DATE]   Version: [N]

## 1. Describe the processing

| Question | Answer |
|---|---|
| What is processed | Health records; contact details; optional-purpose data (preferences, reminders, nutrition, coverage, research, partner sharing); staff accounts; audit trail; assistant messages |
| Whose data | Patients (including children via guardians), guardians, staff |
| Where | On the clinic PC ([MODEL, LOCATION]); encrypted backups at [LOCATION]; nothing sent to outside AI services |
| Who has access | Staff by role (clinician, nurse, front desk, admin); [VENDORS] |
| Retention | Health record: [LAW]; optional data: per-purpose schedule (consent_center), deleted automatically; assistant outbox [90] days |
| Technology | Stark Covenant (local web app, AES-256-GCM vault, hash-chained audit), optional local model via Ollama on 127.0.0.1 |

## 2. Necessity and proportionality

- Lawful basis for care: [ ]. Lawful basis for optional purposes: explicit opt-in consent, default off.
- Minimisation: each optional purpose has a fixed list of fields; others are refused by the software.
- Transparency: plain-language explanations; privacy notice; AI disclosure in every assistant message.
- Rights: export (JSON + PDF), correction, withdrawal (immediate), erasure of optional data with tombstone.
- Processors and transfers: [ ].

## 3. Risks

| # | Risk to people | Likelihood | Severity | Existing measure | Extra measure needed | Residual risk |
|---|---|---|---|---|---|---|
| 1 | Unauthorised access to records (stolen PC) | [ ] | High | Encryption at rest; keys split PC / USB / code | Full-disk encryption; keep USB key apart from PC | [ ] |
| 2 | Staff looking at records they do not need | [ ] | High | Roles; every chart view audited | Regular audit review; sanctions policy | [ ] |
| 3 | Assistant gives harmful advice | Low | High | No diagnosis/dosing/medication changes in code; clinician sign-off; template fallback; output checks | Staff read incoming messages; clinic-verified sources only | [ ] |
| 4 | Assistant misses a crisis message | [ ] | Very high | Broad crisis screen; alerts to care team; emergency numbers | Daily alert review; clear policy; staff read all messages; not advertised as a crisis service | [ ] |
| 5 | Optional data used without consent | Low | Medium | Purpose-limited reads re-check live consent; learner gate | Train staff to record consent correctly | [ ] |
| 6 | Erased data reappears after restoring a backup | [ ] | Medium | Signed erasure register; `reapply_erasures()` | Add to restore procedure | [ ] |
| 7 | Consent of children not valid | [ ] | Medium | Guardian required under configured age; lapse at age | Set the right age; verify authority | [ ] |
| 8 | LAN mode traffic intercepted | [ ] | High | Off by default; needs plan + flag | Use only on a trusted, segmented network or add TLS | [ ] |
| 9 | Ransomware / data loss | [ ] | High | Journaled writes; encrypted rolling backups | Off-site, offline copy; restore tests | [ ] |
| 10 | [OTHER] | | | | | |

## 4. Sign-off

| Item | Name / date | Notes |
|---|---|---|
| Measures approved by | | |
| Residual risks approved by | | If high residual risk remains, consult the regulator before processing (GDPR Art. 36) |
| DPO / privacy officer advice | | |
| Review date | | Review at least yearly and after any significant change |
