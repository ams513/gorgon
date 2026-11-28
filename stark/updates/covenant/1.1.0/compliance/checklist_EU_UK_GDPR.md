# European Union GDPR and United Kingdom GDPR / DPA 2018: alignment checklist

> **Informational, not legal advice. Not a certification and not a compliance claim.** The clinic is the
> controller and remains responsible. National health-data laws, professional secrecy rules and record-retention
> laws apply on top of the GDPR. Have a qualified lawyer (and your DPO, if you have one) review this.

Jurisdiction profiles: `eu_gdpr` (consent age default 16; set your Member State's age, 13-16), `uk_gdpr`
(13 for online services; Gillick competence for care).

| Requirement | Feature that supports it | What the CLINIC must still do |
|---|---|---|
| Principles: lawfulness, purpose limitation, minimisation, accuracy, storage limitation, integrity, accountability (Art. 5) | Purposes with fixed field lists; purpose-limited reads; retention schedules with automatic deletion; audit trail | Keep a record of processing (Art. 30); document your retention schedule |
| Lawful basis (Art. 6) and special category data (Art. 9) | Optional purposes use explicit, granular, recorded consent (Art. 9(2)(a)); care itself is not made conditional on it | Identify the basis for care (Art. 9(2)(h) + national law) separately; do not rely on consent for care records |
| Conditions for consent: demonstrable, distinguishable, withdrawable as easily as given (Art. 7) | Signed consent receipts; one purpose per tick box; withdrawal without PIN, immediate, data deleted | Use the consent form template; keep paper originals if used |
| Children (Art. 8) | Guardian required under the configured age; staff confirm guardian authority; lapse at the age of consent | Set the Member State age; verify parental responsibility reasonably |
| Transparency (Art. 12-14) | Plain-language explanations; privacy notice template; receipts state controller and purpose | Publish a full privacy notice (DPO contact, recipients, transfers, retention, rights, complaint right) |
| Access (Art. 15), portability (Art. 20) | `export_subject` JSON (machine-readable) + PDF; access log included | Verify identity; answer within one month (extendable by two months); free of charge as a rule |
| Rectification (Art. 16) | Editing demographics; versioned clinical entries; addenda | Process requests; tell recipients (Art. 19) |
| Erasure (Art. 17), restriction (Art. 18), objection (Art. 21) | `erase_subject` removes optional data at once with a tombstone; withdrawal stops each purpose | Decide on clinical-record erasure requests using Art. 17(3) exemptions and national retention law; handle restriction manually where needed |
| Automated decision-making (Art. 22) | The assistant makes no decisions about a person; clinical content is a clinician-signed draft; safety routing only adds a human | Explain the assistant in your notice |
| Data protection by design and by default (Art. 25) | Every optional purpose is off by default; local-only processing; pseudonymous ids in audit and tombstones | Keep defaults off; review new features against Art. 25 |
| Processors (Art. 28) | `templates/data_processing_agreement.md` | Sign DPAs with IT support, backup and messaging vendors |
| Security (Art. 32) | AES-256-GCM at rest, 2-of-3 key split, roles, idle lock, CSRF/Origin/Host checks, journaled writes, encrypted backups | OS hardening, device encryption, physical security, staff training, tested restores |
| Breach notification (Art. 33-34) | Audit integrity check; breach runbook template | Notify the supervisory authority within 72 hours where required; tell people without undue delay when the risk is high; keep a breach register |
| DPIA (Art. 35) | `templates/dpia_template.md` | Health data processing usually needs a DPIA; complete and review it |
| DPO (Art. 37) | - | Appoint a DPO if your core activities are large-scale health data processing or national law requires it |
| International transfers (Ch. V) | Nothing leaves the PC by default; no outside AI service | Assess any cloud backup or messaging vendor for transfers |

## UK specifics

- Regulator: ICO. Register and pay the data protection fee if required.
- Common law duty of confidentiality applies to health information alongside UK GDPR.
- NHS bodies and contractors: follow the NHS Records Management Code of Practice and Data Security and
  Protection Toolkit where they apply.
