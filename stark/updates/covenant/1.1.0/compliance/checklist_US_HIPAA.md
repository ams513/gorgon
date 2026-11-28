# United States: HIPAA alignment checklist

> **Informational, not legal advice. Not a certification and not a compliance claim.** HIPAA compliance is a
> property of the covered entity's whole program (people, policies, premises, vendors), never of a software
> product. Have a qualified lawyer and a privacy/security professional review this with you. State laws (health
> privacy, minors' consent, biometric, consumer health data) may add stricter duties.

Jurisdiction profile: `us_hipaa` (consent age default 18; state law decides).

## Privacy Rule (45 CFR Part 164, Subpart E)

| Requirement | Feature that supports it | What the CLINIC must still do |
|---|---|---|
| Uses and disclosures; minimum necessary (164.502(b), 164.514(d)) | Roles with least privilege (front desk sees no clinical data); per-purpose field lists in `consent_center`; purpose-limited reads | Write minimum-necessary policies; assign roles correctly; review access regularly |
| Authorizations for uses outside treatment, payment, operations (164.508) | Optional purposes are opt-in with a signed receipt; research and partner sharing are separate, named consents | Use a HIPAA-compliant **authorization** form (core elements, expiry, signature) for marketing, sale of PHI, most research; the consent receipt alone is not a HIPAA authorization |
| Notice of Privacy Practices (164.520) | `templates/privacy_notice.md` starting point; plain-language purpose explanations | Adopt, post and give your NPP; obtain acknowledgement; keep versions |
| Right to request restrictions; confidential communications (164.522) | Per-purpose opt-in; channel and quiet-hours preferences under `reminders` | Process restriction requests (mandatory for self-paid services withheld from a health plan) |
| Right of access (164.524) | `export_subject` JSON + PDF (clinical record, consents, access log, assistant messages) | Verify identity; respond within 30 days (one 30-day extension); fees only as allowed |
| Right to amend (164.526) | Versioned allergy/medication/problem entries; signed addenda | Run the amendment process and its timelines; record denials |
| Accounting of disclosures (164.528) | Hash-chained audit trail records exports and views by ids | Track disclosures outside the system (paper, phone, partners); produce the accounting on request |
| Personal representatives (164.502(g)) | Guardian section with authority check; guardian consent lapses at the consent age | Apply state law on minors (including services minors can consent to alone) |
| Administrative requirements: privacy official, training, sanctions, complaints, documentation retained 6 years (164.530) | Consent proof retention default 6 years (`proof_retention_days` 2190); audit trail | Name a privacy official; train staff; keep policies; handle complaints; sanctions policy |

## Security Rule (45 CFR Part 164, Subpart C)

| Requirement | Feature that supports it | What the CLINIC must still do |
|---|---|---|
| Risk analysis and risk management (164.308(a)(1)) | `honest limits` sections in README / module docstrings; DPIA template usable as a risk analysis aid | Perform and document a risk analysis covering the PC, network, backups, staff, vendors; update it |
| Workforce security, information access management (164.308(a)(3)-(4)) | Unique accounts, roles, admin user management, disable accounts | Onboarding / offboarding procedure; access reviews |
| Security awareness and training (164.308(a)(5)) | Login lockout, PIN lockout | Train staff (phishing, passwords, locking the PC) |
| Security incident procedures (164.308(a)(6)) | Audit integrity check detects tampering; health log | Adopt `templates/breach_response_runbook.md`; practise it |
| Contingency plan: backup, disaster recovery (164.308(a)(7)) | Encrypted rolling backups; journaled writes; vault recovery code + USB key | Keep an off-site backup copy; test restores; store the recovery code safely |
| Business associate contracts (164.308(b), 164.314) | `templates/business_associate_agreement.md` | Sign BAAs with every vendor that creates, receives, keeps or sends PHI for you (IT support, cloud backup, message service). Stark does not receive PHI from a local install; if a support session would expose PHI, sign a BAA first |
| Facility and device / media controls (164.310) | Records encrypted at rest; vault keys split across PC, USB stick, recovery code | Lock rooms and screens; control the USB key; dispose of disks securely |
| Access control: unique user id, automatic logoff, encryption (164.312(a)) | Per-user logins; 10-minute idle lock; AES-256-GCM at rest | Keep the OS patched and encrypted (BitLocker); limit who can sign in to Windows |
| Audit controls (164.312(b)) | HMAC-sealed, hash-chained audit trail; consent, export, erasure, assistant events logged | Review audit logs on a schedule |
| Integrity, authentication (164.312(c)-(d)) | Signed notes and signed assistant replies; scrypt passwords; signing PIN | Password and PIN policy |
| Transmission security (164.312(e)) | Listens on 127.0.0.1 by default; no outside AI service | LAN mode is plain HTTP: use only on a trusted, segmented network, or add TLS in front |

## Breach Notification Rule (45 CFR 164.400-414)

| Requirement | Feature that supports it | What the CLINIC must still do |
|---|---|---|
| Notify individuals without unreasonable delay, no later than 60 days; HHS; media for 500+ residents of a state | Encryption at rest (a breach of properly encrypted data may fall under the safe harbor - ask counsel); audit trail to scope a breach | Run the 4-factor risk assessment; notify; keep the breach log; annual HHS report for breaches under 500 |

## Other U.S. points to check with counsel

- 42 CFR Part 2 (substance-use treatment records): stricter consent rules where it applies.
- State consumer-health-data laws (e.g. Washington My Health My Data Act) can require separate consent for
  data outside HIPAA.
- TCPA / state rules for automated reminder calls and texts: get the right consent for the channel.
- The assistant never makes clinical decisions; keep it that way for FDA software-as-a-medical-device purposes.
