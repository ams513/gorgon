# Stark Covenant worldwide

This page says which languages and regions are ready, which still need work, and what a distributor, NGO or
health ministry must do before deploying Stark Covenant in a new country. It also covers fair pricing.

Stark Covenant 1.1.0 is the full (stable) release. It is not a certified EHR and not a medical device. **Stark
Covenant makes no compliance claim** under any law listed here, or any other law. Each operator must complete
their own legal, privacy, security and clinical review before real patient data goes into it.

## At a glance

| Area | Ready now | Needs work before real use in a country |
|---|---|---|
| English interface | Complete; it is the source text | Local clinical review of wording |
| 13 other languages | Every screen translated as a machine draft, clearly marked | Review by a qualified native speaker (workflow below) |
| Right-to-left layout | Arabic and Urdu (Hebrew and Persian are supported once catalogs exist) | Native review of the layout and wording |
| Region settings | Defaults for 92 countries; every setting can be changed per clinic and per user | Local check of the defaults (see "Regions") |
| PDF letters and exports | Arabic, Hindi, Chinese, Urdu and Bengali tested, with fonts already on Windows | Check the printed result with local staff |
| Emergency numbers | 250 countries and territories: 178 verified, 19 partly verified, 53 unverified | Local confirmation of the numbers you rely on |
| Offline use | Works with no internet at all; no page asks for anything outside the computer | Nothing |
| Low bandwidth, small screens | Tested on emulated 2G, at 1366x768 and 360x640 | Real-device check on the phones and PCs you use |
| Keyboard and screen readers | Keyboard-only use and in-house accessibility checks pass | A session with people who use screen readers |
| Compliance | Checklists and templates as a starting point (`compliance/`) | Your own legal review; no claim is made |

## Languages

Shipped: English (source), Spanish, French, Portuguese (Brazil), Arabic, Hindi, Chinese (Simplified), Swahili,
Indonesian, Russian, Bengali, Urdu, German and Japanese. All 1,141 interface strings exist in all 14 languages.

| Language | Code | Direction | Status |
|---|---|---|---|
| English | en | left to right | source text |
| Spanish | es | left to right | machine draft, needs native review |
| French | fr | left to right | machine draft, needs native review |
| Portuguese (Brazil) | pt-BR | left to right | machine draft, needs native review |
| Arabic | ar | right to left | machine draft, needs native review |
| Hindi | hi | left to right | machine draft, needs native review |
| Chinese (Simplified) | zh-Hans | left to right | machine draft, needs native review |
| Swahili | sw | left to right | machine draft, needs native review |
| Indonesian | id | left to right | machine draft, needs native review |
| Russian | ru | left to right | machine draft, needs native review |
| Bengali | bn | left to right | machine draft, needs native review |
| Urdu | ur | right to left | machine draft, needs native review |
| German | de | left to right | machine draft, needs native review |
| Japanese | ja | left to right | machine draft, needs native review |

`python i18n.py status` prints the live status of every catalog.

How the app protects people while a language is still a draft:

- A banner on every screen says the language has not been checked by a native speaker yet.
- Clinical words (allergies, vital signs, medicines, problems) show the English next to the translation.
- Safety and legal wording (the disclaimer, the safety-plan emergency line, the emergency-page warnings, AI labels, insurance
  navigation notices) always shows the English original as well.
- The emergency page keeps its safety sentence in English and adds the translation below it.

Reviewing a language (only a named human reviewer can mark a string as reviewed):

1. `python i18n.py export-review fr fr_review.csv` writes a sheet with the English, the draft and empty
   reviewer columns.
2. A qualified native speaker, ideally with clinical experience, corrects the `translation` column and fills
   `reviewer`, `review_date` (YYYY-MM-DD) and, if they like, `qualification` and `comment`.
3. `python i18n.py import-review fr fr_reviewed.csv` checks every row. Rows without a reviewer name and date
   are refused, as are rows signed by a machine, rows with an e-mail address or link as the reviewer, rows
   dated in the future, and rows whose placeholders or plural forms do not match the English.
4. A string counts as reviewed only while its English and its translation are both unchanged since the
   review. Editing either one puts it back to "needs native review". A hand-edited status has no effect.

What stays in English in every language, for now:

- Voice commands on the emergency page (the spoken trigger words are English only).
- Text produced by optional modules: research summaries and their sources, FHIR and X12 exports, the food
  database, and coded vocabularies (LOINC, UCUM, ICD and similar).
- Server log files and the support diagnostics bundle.
- PHQ-9 and GAD-7 questions and answers (the published English wording; scores from another wording are not
  comparable, so use an officially validated translation of the questionnaire on paper where needed).
- Error messages from the practice screens (calendar, billing, measures, plans), and appointment statuses and
  billing codes inside the full-record JSON export. PDF exports (full record, superbill) print each heading in
  the chosen language with the English beside it while the language is a machine draft.
- Superbill fields follow US conventions (CPT/HCPCS, ICD-10, NPI, place of service); other countries can use the
  invoice and payment records and ignore those fields.

Plural forms follow the Unicode CLDR rules for each language (including Arabic's six forms and Russian's four),
checked against the ICU library in Node.js. Numbers, currency and weekday names come from the browser's
built-in Intl data, with Latin digits by default and native digits on request.

## Regions

**My account > Language and region** lets each user choose: date format, first day of the week, 12- or 24-hour
clock, time zone, units (metric or US), blood glucose unit (mmol/L or mg/dL), name order (given or family name
first) and digits. An administrator sets the clinic default on the **Users** screen.

What is stored never changes with these settings:

| Setting | Stored as | Shown as |
|---|---|---|
| Dates | ISO 8601 (1984-02-29) | DD/MM/YYYY, MM/DD/YYYY, DD.MM.YYYY, DD-MM-YYYY, YYYY-MM-DD, YYYY/MM/DD or YYYY.MM.DD |
| Times | UTC | the chosen IANA time zone, daylight saving included |
| Temperature, weight, height | degrees C, kg, cm | the same, or degrees F, lb, in |
| Blood glucose | mmol/L | mmol/L or mg/dL (x 18.016) |
| Names | given name and family name separately | given-first or family-first (no space between CJK names) |
| Addresses | street, city, region, postcode, country | in the country's own order and labels |
| Phone numbers | what was typed, plus E.164 (+254712345678) | grouped for reading |

The country table holds defaults for 92 countries. The first day of the week and the 12/24-hour clock follow
Unicode CLDR, and a test checks them against ICU. Please check these locally before go-live:

- the date format your clinic uses on paper (many countries use more than one);
- the glucose unit your laboratory reports (some report both);
- the trunk prefix used to turn local phone numbers into E.164 (0 in most countries, 8 in Russia and
  Kazakhstan, 06 in Hungary, none in some). A number is only converted when the country is known; otherwise
  only the typed text is kept;
- the address format and the names for "region" and "postcode";
- any non-Gregorian civil calendar used alongside the Gregorian one (the app shows Gregorian dates only).

## Offline and low-resource use

- Everything runs on the clinic's own computer. There are no web fonts, CDNs, analytics or cloud calls; a
  browser test records every request on every main screen and fails if one leaves the computer.
- First load (the whole app shell, compressed, with one language inlined; measured 2026-11-20): English 44.4 KB,
  Chinese 49.0 KB, Arabic 50.1 KB, Bengali 50.8 KB, Hindi 51.2 KB. Only the strings a page uses are sent with it, and the
  tests fail if English goes over 48 KiB or another language over 52 KiB. The practice screens (Today,
  Calendar, Billing, Measures, Treatment plan) are not part of that first load: their script (about 12 KB
  compressed) and strings (about 14 KB) are fetched once, after sign-in, the first time one of them opens.
- On emulated 2G (800 ms latency, 280 kbit/s down, 256 kbit/s up) the sign-in screen appears in about
  3 seconds, and signing in and opening a chart takes about 3 seconds more.
- Screens were tested at 1366x768 and 360x640 in English, Arabic, Urdu, Hindi, Chinese, Russian, German and
  Swahili: no sideways scrolling on sign-in, the patient list, the chart tabs or the region settings.
- Keyboard only: a skip link, a visible focus ring on every control, Tab order through the header and forms,
  and Enter to sign in and open a chart.
- Screen readers: in-house checks (no internet needed) for page language and direction, labels on every field,
  names on every button, no duplicate ids, live regions for messages, headings, text contrast of at least
  4.5:1 (3:1 for large text) and touch targets of at least 24x24 px, in light and dark mode.

## Data-protection laws (information only)

**Stark Covenant makes no compliance claim.** The list below is a starting point for your own legal
review. It is not legal advice, it is not complete, and laws change. Many countries have health-records laws
on top of their general data-protection law, so ask a lawyer who practises where you deploy.

| Law | Where | Who it covers, in short | Regulator | Starting point here |
|---|---|---|---|---|
| HIPAA (Privacy, Security and Breach Notification Rules) | United States | Health-care providers that bill electronically, health plans, clearinghouses and their business associates | HHS Office for Civil Rights | `compliance/checklist_US_HIPAA.md`, BAA template |
| GDPR (Regulation (EU) 2016/679) | EU and EEA | Anyone processing personal data of people in the EU/EEA; health data is a special category (art. 9) | National data protection authorities | `compliance/checklist_EU_UK_GDPR.md`, DPIA and DPA templates |
| UK GDPR and Data Protection Act 2018 | United Kingdom | As GDPR, under UK law | Information Commissioner's Office (ICO) | `compliance/checklist_EU_UK_GDPR.md` |
| PIPEDA, plus provincial health laws (for example Ontario's PHIPA) | Canada | Private-sector organisations; health custodians under provincial laws | Office of the Privacy Commissioner of Canada and provincial commissioners | `compliance/checklist_CA_PIPEDA_PHIPA.md` |
| LGPD (Law 13.709/2018) | Brazil | Anyone processing personal data in Brazil; health data is sensitive | ANPD | `compliance/checklist_BR_LGPD.md` |
| POPIA (Act 4 of 2013) | South Africa | Responsible parties processing personal information; health information is special personal information | Information Regulator | none yet |
| DPDP Act 2023 | India | Data fiduciaries processing digital personal data; its rules are being phased in | Data Protection Board of India | `compliance/checklist_IN_DPDP.md` |
| PIPL (2021) | China | Anyone processing personal information in China; medical and health information is sensitive; strict rules on transfers abroad | Cyberspace Administration of China and sector regulators | none yet |
| APPI | Japan | Business operators handling personal information; medical history is special care-required information | Personal Information Protection Commission | none yet |
| Data Protection Act 2019 | Kenya | Data controllers and processors; health data is sensitive; many health-care providers must register | Office of the Data Protection Commissioner (ODPC) | none yet |

`compliance/templates/` holds drafts of a patient consent form, a staff consent form, a privacy notice, a
data processing agreement, a business associate agreement, a DPIA and a breach response runbook. They are
drafts for your lawyer to adapt, not finished legal documents.

## Deploying in a new country (distributors, NGOs, ministries)

1. **Legal review.** Have local counsel decide which laws apply (data protection, health records, medical
   software, retention periods, consent, transfers abroad) and adapt the templates. Record the outcome.
2. **Evaluate with DEMO data first.** Start in DEMO mode with synthetic patients and keep it there until your
   review is finished.
3. **Language review.** Export the review sheet for your language, have a qualified native speaker (ideally a
   clinician) review it, import it, and check `python i18n.py status`. Until then the app keeps showing the
   draft banner and the English next to clinical and safety wording.
4. **Region settings.** Set the clinic's default language and region on the **Users** screen, then check the
   date format, glucose unit, name order, address labels and phone conversion against local practice.
5. **Emergency numbers.** Check the numbers the emergency page shows for your country with the local
   authorities. The page always says that it does not replace calling your local emergency number, and that a
   connection cannot be guaranteed (no signal, no credit, no internet, network outages).
6. **Computers and backups.** Use full-disk encryption, a supported operating system with updates, a screen
   lock, the secure vault (not DEMO mode) for real data, and off-site encrypted backups that you test by
   restoring. Decide who may physically reach each computer.
7. **People.** Train staff on sign-off (every clinical entry is made and signed by a person; AI suggestions
   need a clinician's sign-off), roles, locking the screen and the breach runbook.
8. **Updates.** Decide whether each site allows update checks (below). Sites with no internet can stay fully
   offline and receive new versions by USB.
9. **Licences and pricing.** Choose the plan; if you qualify for the reduced price (below), say so when you
   buy. Keep the device codes of the computers you license.
10. **Support.** Name a local contact who collects the diagnostics bundle (it holds no patient data) when
    something goes wrong.

## Fair pricing

Plans and their limits are in `license_guard.py` and on the **Licence** screen; the rules below are shown on
that screen in every language (`license_guard.FAIRNESS_RULES`):

- Each licence is paid once: no subscription and no setup fees.
- Public-sector and non-profit operators in World Bank low- and lower-middle-income countries pay 50% of the
  listed price on every plan.
- Registered humanitarian organisations use the Humanitarian plan ($50 one-time).
- Practice is priced per clinician, from 2 to 25 clinicians.
- If you stop using Stark Covenant, your records stay readable and exportable.

| Plan | Price (USD) | Devices | Up to |
|---|---|---|---|
| Baby | free | 1 | Individual |
| Solo | 50, one-time | 2 | Individual |
| Practice | 200 per clinician, one-time (2-25 clinicians) | 2 per clinician | Clinic |
| Humanitarian | 50, one-time | 10 | Continent |

The 50% price follows the World Bank's country income classification, which is updated every year on 1 July.
A lapsed or downgraded licence never locks anyone out of existing records.

## Updates

- Stark Covenant is not a certified EHR or medical device; your compliance review is required before real
  patient data.
- Update checks are off until the clinic allows them: a box on the first-run screen (unticked by default), or later by an administrator
  on the **Licence** screen (Updates card), where they can also be turned off again.
- When allowed, the app fetches a signed list of updates at start-up from
  `https://ams513.github.io/stark/updates/covenant/latest.json` (the earlier
  `https://ams513.github.io/gorgon/updates/covenant/latest.json` only if that cannot be reached). Nothing about you or your patients is sent;
  like any web visit, the request shows the clinic's internet address to GitHub. An unsigned
  or altered list is ignored.
- An install from the earlier test channel is moved to the full ("stable") releases automatically. A stable
  install takes stable releases only.
- An update never replaces the vault, the records, the licence, the audit trail or the update settings. A new
  version that fails its start-up health check is rolled back and not offered again.

## Honest limits

- Machine-drafted translations can be wrong in ways that matter clinically. That is why they are labelled
  and why clinical and safety wording keeps the English beside them.
- Region defaults are a starting point for each country, not an authority; clinics should set their own.
- Emergency numbers marked unverified come from secondary sources.
- The app has been tested on Windows with Microsoft Edge (Chromium). Other browsers should work but were not
  part of the automated tests.
- Nothing here is certified by any authority, and no part of this page is legal advice.
