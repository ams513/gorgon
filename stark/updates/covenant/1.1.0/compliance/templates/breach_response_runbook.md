# Breach response runbook

> **Template - have a qualified lawyer in your jurisdiction review before use.** Deadlines below are summaries for
> orientation only; laws change and have exceptions. Confirm each one with counsel and write your final
> numbers into this runbook before you need it.

Owner: [PRIVACY OFFICER]   Deputy: [NAME]   Legal counsel: [NAME, PHONE]   IT contact: [NAME, PHONE]
Insurance (cyber) hotline: [NUMBER]   Last rehearsal: [DATE]

## 0. Anyone who suspects a problem

Lost USB key, lost or stolen laptop, wrong patient sent a document, strange logins, "Verify integrity" fails,
ransomware note, a vendor tells you about an incident: **tell the privacy officer now**. Do not delete anything.
Do not try to "fix" it quietly.

## 1. Contain (first hour)

- [ ] Note the time you became aware (the notification clocks may start here).
- [ ] Disconnect affected PCs from the network (do not switch them off if forensic work may be needed).
- [ ] In Stark Covenant: Lock; as admin, disable affected user accounts; end their sessions.
- [ ] If the vault USB key or recovery code is lost: as soon as possible run a key rotation with re-sharing
      (`security_vault` `rotate_key(reshare=True)`), which makes the old USB key and code useless.
- [ ] Preserve evidence: copy `records\audit\audit.log`, `audit.head`, `.covenant_health.log`; export the audit
      verification result.

## 2. Assess (first 24-72 hours)

- [ ] What data, how many people, which jurisdictions do they live in?
- [ ] Was the data encrypted and were the keys safe? (Encrypted data with uncompromised keys may change the
      outcome of the assessment - ask counsel.)
- [ ] Use the audit trail to scope it: who viewed or exported which records and when (`/api/audit`).
- [ ] Assess the risk of harm using your jurisdiction's test (table below). Record the reasoning.

## 3. Notify (summary - confirm with counsel)

| Jurisdiction | Regulator | Individuals | Other |
|---|---|---|---|
| US HIPAA | HHS OCR: within 60 days if 500+; annual log for under 500 | Without unreasonable delay, no later than 60 days after discovery | Media if 500+ residents of a state; state laws may be shorter |
| EU GDPR / UK GDPR | Supervisory authority / ICO within 72 hours unless unlikely to result in a risk | Without undue delay if high risk | Keep an internal breach register for every breach |
| Canada PIPEDA / PHIPA | OPC if real risk of significant harm; IPC Ontario where thresholds met | As soon as feasible / at the first reasonable opportunity | Keep breach records 24 months (PIPEDA) |
| Australia | OAIC for eligible data breaches | As soon as practicable | Assessment within 30 days of suspicion |
| India DPDP | Data Protection Board (check current Rules for the timeline and content) | Each affected Data Principal | - |
| Brazil LGPD | ANPD within the deadline in current ANPD rules | Affected data subjects | - |
| Other | [LOCAL LAW] | [LOCAL LAW] | - |

## 4. Recover and learn

- [ ] Restore from a clean backup if needed; then run `reapply_erasures()` with the latest erasure register.
- [ ] Rotate passwords, PINs and vault keys as needed.
- [ ] Root-cause review within [14] days; update the risk analysis / DPIA, training and this runbook.

## Log

| Date/time | Who | What was done / decided |
|---|---|---|
| | | |
