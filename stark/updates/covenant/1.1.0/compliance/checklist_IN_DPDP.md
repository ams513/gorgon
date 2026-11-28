# India: Digital Personal Data Protection Act 2023 alignment checklist

> **Informational, not legal advice. Not a certification and not a compliance claim.** The DPDP Rules are being
> brought into force in phases; check the currently notified Rules and their timelines. Sector rules (e.g.
> clinical establishment, telemedicine and medical-records guidance) also apply. Have a qualified lawyer review
> this.

Jurisdiction profile: `in_dpdp` (a child is under 18: verifiable parental or guardian consent).

| Section / requirement | Feature that supports it | What the CLINIC (data fiduciary) must still do |
|---|---|---|
| s.4 Grounds: consent or legitimate use | Optional purposes rely on consent only | Identify the legitimate-use basis (s.7) for care itself |
| s.5 Notice (itemised, plain language; available in English or a Schedule 8 language) | Plain-language explanations per purpose; privacy notice template | Give the notice in the language the person chooses; translations reviewed by a qualified person (the assistant only uses human-reviewed templates) |
| s.6 Consent: free, specific, informed, unconditional, unambiguous, clear affirmative action; withdrawal as easy as giving it | Separate opt-in per purpose, default off, signed receipts, one-step withdrawal with immediate effect and deletion | Do not bundle consent with care; keep the receipts |
| s.6 Consent Managers | Receipts in a standard (Kantara) shape | Integrate with a registered Consent Manager if you choose to |
| s.8 Obligations: accuracy, security safeguards, breach intimation, erasure when the purpose is served or consent withdrawn, grievance redressal, contact person | Encryption, roles, audit; deletion on withdrawal; retention purge; privacy officer field | Publish the contact details of the person who answers questions; run grievance redressal; breach intimation to the Board and each affected person |
| s.9 Children: verifiable parental consent; no tracking, behavioural monitoring or targeted advertising | Guardian required under 18 with staff authority check; no tracking, monitoring or advertising features exist; `learning` for minors only with guardian consent | Verify guardian identity as the Rules require; consider leaving `learning` off for children entirely |
| s.10 Significant data fiduciary (DPO, DPIA, audits) | DPIA template | If notified as significant, appoint a DPO in India, run DPIAs and audits |
| s.11 Right to access a summary of data and processing | JSON + PDF export | Respond within the time the Rules set |
| s.12 Right to correction and erasure | Editing; erasure with tombstone | Decide on clinical records against other laws requiring retention |
| s.13 Grievance redressal | Complaint path in wording | Respond within the prescribed period; inform about the Data Protection Board |
| s.14 Right to nominate | - | Record nominees (outside this software for now) |
| s.16 Cross-border transfer | Nothing leaves the PC by default | Check restrictions before using any foreign vendor |
