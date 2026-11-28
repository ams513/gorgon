# Stark Covenant: privacy and compliance support pack

> **Informational, not legal advice.** Stark Covenant is **not certified** under any law or standard, and it
> **makes no compliance claim**: no HIPAA, GDPR, PIPEDA, PHIPA, Privacy Act, DPDP or LGPD claim. Software alone
> cannot make a clinic compliant. These documents show which features **support** each requirement and what
> **the clinic must still do**. Have a qualified lawyer in your jurisdiction review your setup and every
> template before you rely on it.

## What is here

| File | What it is |
|---|---|
| `INTEGRATION.md` | How `clinic_app` mounts `consent_center` and `persona_ai` (the exact 3 lines), routes, data layout, operations |
| `checklist_US_HIPAA.md` | HIPAA Privacy, Security and Breach Notification Rules alignment checklist |
| `checklist_EU_UK_GDPR.md` | EU GDPR and UK GDPR / DPA 2018 alignment checklist |
| `checklist_CA_PIPEDA_PHIPA.md` | Canada: PIPEDA principles and Ontario PHIPA alignment checklist |
| `checklist_AU_Privacy_Act.md` | Australia: Privacy Act 1988, APPs and Notifiable Data Breaches alignment checklist |
| `checklist_IN_DPDP.md` | India: Digital Personal Data Protection Act 2023 alignment checklist |
| `checklist_BR_LGPD.md` | Brazil: LGPD alignment checklist |
| `templates/privacy_notice.md` | Privacy notice (patients and staff) |
| `templates/consent_form_patient.md` | Patient consent form for optional purposes, with a parent / guardian section |
| `templates/consent_form_staff.md` | Staff consent form (personalised assistant, writing helper) |
| `templates/business_associate_agreement.md` | HIPAA Business Associate Agreement |
| `templates/data_processing_agreement.md` | Data Processing Agreement (GDPR Art. 28 style; adaptable) |
| `templates/breach_response_runbook.md` | Breach response runbook with per-jurisdiction notification table |
| `templates/dpia_template.md` | Data Protection Impact Assessment template |

Every template starts with: **"Template - have a qualified lawyer in your jurisdiction review before use."**

## How the features map, in one table

| Need | Feature that supports it | Where |
|---|---|---|
| Opt-in only, per purpose, default off | 7 purposes (personalised wording, learning, reminders, nutrition, coverage help, research, each named sharing partner), all off until a recorded yes | `consent_center.PURPOSES`, `ConsentCenter.grant` |
| Plain-language explanation | What, which data, who sees it, how long, "saying no will not change your care", how to withdraw | `ConsentCenter.explain`, `GET /api/consent/explain` |
| Proof of consent | Kantara Consent Receipt v1.1 shape, JWS HS256 signed with a key derived from the per-install audit key (sealed by `security_vault`); staff PIN e-signature on every grant | `ConsentCenter._receipt`, `verify_receipt` |
| Withdrawal as easy as consent | One call, no PIN, immediate; purpose data deleted in the same journaled transaction | `ConsentCenter.withdraw` |
| Purpose limitation, minimisation | Each purpose lists the only fields it may hold; every read re-checks the live consent | `ConsentCenter.read/require/scoped`, `_clean_data` |
| Learning only with consent | `self_learner` learns from a signed note only when the clinician **and** (by default) the patient have an active `learning` consent; withdrawal shreds the profile | `consent_center._learner_gate` |
| Children | Guardian (name, relationship, authority checked by staff) required under the jurisdiction's consent age; guardian consent lapses at that age until re-confirmed | `ConsentCenter._guardian_check`, `_evaluate` |
| Retention and deletion | Per-purpose retention, consent expiry (research), proof-of-consent period, assistant outbox retention; hourly automatic purge | `ConsentCenter.purge_expired`, `start_scheduler` |
| Right of access / portability | JSON export (consents, receipts, purpose data, clinical record, access log, assistant messages) with a SHA-256 manifest, plus a readable PDF | `ConsentCenter.export_subject`, `export_pdf` |
| Erasure | Optional data and assistant data removed; tombstone with a keyed pseudonym only; clinical record kept for legal retention and flagged for the privacy officer; signed erasure register to re-apply after a backup restore | `ConsentCenter.erase_subject`, `export_erasure_register`, `reapply_erasures` |
| Security of processing | AES-256-GCM via OS crypto, 2-of-3 key split; unique logins (scrypt); roles; 10-minute idle lock; CSRF / Origin / Host checks; journaled writes | `security_vault`, `clinic_app` |
| Audit / accountability | Hash-chained, HMAC-sealed audit trail of ids and hashes (consent, export, erasure, assistant events included) | `clinic_app.AuditTrail` |
| Transparent AI, human path | Every assistant message says it is an AI assistant and how to reach a person; no diagnosis, dosing, medication changes or triage; clinical replies are drafts a clinician signs | `persona_ai` |
| No outside AI service | Optional model only on `127.0.0.1:11434`; otherwise templates | `persona_ai.OLLAMA_HOST` |

## Honest limits (read before relying on anything)

- Receipts use an HMAC key held by the clinic: the clinic can verify them; an outsider cannot without the clinic.
- Erasure clears the live store immediately. **Encrypted backups keep older copies until rotation** (newest 20).
  After any restore, run `reapply_erasures()` with the latest erasure register.
- "Forget me" does **not** delete clinical records. Most health-record laws require keeping them; the privacy
  officer decides with legal advice.
- The assistant's safety screen is keyword-based and deliberately broad. It will miss some phrasings and
  languages. Staff must still read incoming messages; it is not a crisis service.
- Built-in crisis numbers are a fallback for a few countries and can go out of date. Enter verified local
  resources in the consent settings (`crisis_resources`).
- DEMO mode stores synthetic data unencrypted, by design. Never enter real people in DEMO mode.
- Age defaults are conservative starting points, not legal determinations (e.g. HIPAA defers to state law;
  PHIPA is capacity-based).
