# Brazil: LGPD (Lei 13.709/2018) alignment checklist

> **Informational, not legal advice. Not a certification and not a compliance claim.** ANPD regulations and
> health-sector rules (e.g. CFM resolutions on medical records and telemedicine) also apply. Have a qualified
> lawyer in Brazil review this.

Jurisdiction profile: `br_lgpd` (Art. 14 requires a parent's specific consent for children; this profile
conservatively asks a guardian for everyone under 18 - change `consent_age` if your counsel advises otherwise).

| Article / requirement | Feature that supports it | What the CLINIC (controlador) must still do |
|---|---|---|
| Art. 6 Principles (purpose, adequacy, necessity, free access, quality, transparency, security, prevention, non-discrimination, accountability) | Purposes with fixed fields; purpose-limited reads; export; audit trail | Document your processing and its principles |
| Art. 7 / Art. 11 Legal bases; sensitive data needs specific, highlighted consent or another Art. 11 basis | Separate, highlighted opt-in per purpose with signed receipts | Identify the Art. 11 II basis (e.g. health protection by professionals) for care itself |
| Art. 8 Consent: written or demonstrable, highlighted clause, burden of proof on the controller, revocable at any time | Signed receipts; staff PIN e-signature; withdrawal at once | Keep proof; use the consent form template in Portuguese, reviewed |
| Art. 9 Information to the data subject | Plain-language explanations; privacy notice template | Publish the full information (controller, encarregado, sharing, rights) |
| Art. 14 Children and adolescents | Guardian required under the configured age; lapse at that age | Verify the parent's consent with reasonable effort |
| Art. 15-16 End of processing and retention | Retention schedules, automatic deletion, deletion on withdrawal | Keep data only as other laws require (medical records retention) |
| Art. 18 Rights (confirmation, access, correction, anonymisation, portability, deletion, information about sharing, revocation) | Export (JSON + PDF), partner list in consents, erasure, withdrawal | Answer within the ANPD's deadlines |
| Art. 20 Review of automated decisions | No automated decisions about people; clinician sign-off | Explain the assistant |
| Art. 33 International transfer | Nothing leaves the PC by default | Assess any foreign vendor |
| Art. 37-38 Records of processing; impact report (RIPD) | DPIA template usable as a RIPD starting point | Keep records; prepare the RIPD when required |
| Art. 41 Encarregado (DPO) | Privacy officer field | Appoint and publish the encarregado (small agents may be exempt under ANPD rules) |
| Art. 46-49 Security and incident communication | Encryption, roles, audit; breach runbook | Communicate incidents to the ANPD and data subjects within the ANPD's deadline (check Resolution CD/ANPD 15/2024) |
