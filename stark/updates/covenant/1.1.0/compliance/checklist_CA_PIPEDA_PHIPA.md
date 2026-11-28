# Canada: PIPEDA and provincial health laws (Ontario PHIPA as the example) alignment checklist

> **Informational, not legal advice. Not a certification and not a compliance claim.** Which law applies depends
> on the province and on who you are (PHIPA "health information custodian", other provincial health acts, or
> PIPEDA for commercial activity). Quebec Law 25 has its own rules. Have a qualified lawyer review this.

Jurisdiction profile: `ca_pipeda` (consent age default 13; PIPEDA sets none; PHIPA is capacity-based).

## PIPEDA fair information principles (Schedule 1)

| Principle | Feature that supports it | What the CLINIC must still do |
|---|---|---|
| 1 Accountability | Audit trail; named privacy officer field in settings and receipts | Designate a privacy officer; written policies; vendor contracts |
| 2 Identifying purposes | Each optional purpose has a stated, plain-language purpose | State purposes at or before collection |
| 3 Consent (meaningful; express for sensitive data) | Granular opt-in, default off, signed receipts, guardians for minors | Follow the OPC Guidelines for obtaining meaningful consent |
| 4 Limiting collection | Field lists per purpose; other fields refused | Collect nothing else for these purposes |
| 5 Limiting use, disclosure, retention | Purpose-limited reads; named partners only; retention schedules and automatic deletion | Set retention periods; destroy securely |
| 6 Accuracy | Editable preferences; versioned clinical entries | Correct on request |
| 7 Safeguards | Encryption, roles, idle lock, audit, backups | Physical and organisational safeguards; training |
| 8 Openness | Privacy notice template; explanations | Publish your policies |
| 9 Individual access | JSON + PDF export | Respond within 30 days (extensions as allowed) |
| 10 Challenging compliance | Complaint path stated in jurisdiction wording | Handle complaints; tell people about the OPC |

## Breach of security safeguards (PIPEDA s.10.1 and Regulations)

| Requirement | Feature | Clinic must still do |
|---|---|---|
| Report breaches with a real risk of significant harm to the OPC; notify individuals as soon as feasible; keep records of all breaches for 24 months | Audit integrity check; breach runbook template | Assess RROSH; notify; keep the breach record |

## Ontario PHIPA (health information custodians)

| Requirement | Feature | Clinic must still do |
|---|---|---|
| Consent: implied within the circle of care, express for other uses | Optional purposes are express, opt-in | Document the circle-of-care basis for care itself |
| Capacity and substitute decision-makers (ss.21-26) | Guardian section; configurable age; child assent flag | Assess capacity; identify the right substitute decision-maker |
| "Lockbox" (withholding or withdrawing consent to disclosure) | Per-purpose and per-partner consent, withdrawal | Handle lockbox requests across all records, not only these purposes |
| Access and correction (Part V; 30 days) | Export; addenda | Process requests within the time limits |
| Agents and electronic service providers | DPA / BAA templates adaptable | Agreements with agents and providers |
| Privacy breach: notify the individual and the IPC where thresholds are met; annual statistics to the IPC | Runbook template | Notify and report |
