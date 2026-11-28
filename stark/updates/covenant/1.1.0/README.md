# Stark Covenant

Local-first clinic records: patients, encounters, notes, vitals, allergies, medications, problems and documents,
kept on your own computer. Python standard library only (3.10+); the whole interface is built into
`clinic_app.py` and opens in your browser at `http://127.0.0.1:86xx/`.

## What's new in 1.1.0 (care suite, 2026-11-20)

Version 1.1.0 is a full (stable) release. Everything below runs on this computer only, uses the same encrypted,
journaled, audited record store, follows the same roles, and works in all 14 languages (the 13 non-English
catalogs are machine drafts). Stark Covenant is not a certified EHR or medical device; your compliance review
is required before real patient data.

Also in 1.1.0 (Stark names):
- The data folder is now `Documents\Stark_Covenant_Data`. An existing `Documents\Gorgon_Covenant_Data` is copied
  there once at start-up, every file is checked (count and SHA-256), and only then does the app switch to the copy.
  The old folder is never changed or deleted; you can remove it yourself once you are happy.
- The launcher is `START_STARK_COVENANT.bat` and the Desktop shortcut is **Stark Covenant** (an old "Gorgon
  Covenant" shortcut is replaced; the old launcher name still works).
- Updates come from the Stark address first, with the earlier address as a fallback (see Updates).
- Plan IDs are `stark-covenant/<edition>`; keys sold under the old `gorgon-chart/<edition>` IDs keep working.
- Support: hello@starkforall.com (never put patient details in an email).

Also in 1.1.0 (easier from the first minute):
- **Try with an example clinic**: the first screen offers it next to **Set up my clinic**. One click opens a practice
  clinic with made-up patients (its own folder, nothing encrypted, never mixed with real records) and signs you in;
  its **Start here** card walks through opening a chart, writing and signing a note (example PIN 1234) and
  registering a patient.
- One main action per screen (the solid button); other actions look secondary. If a screen's main action starts
  below the screen, a copy of it waits at the bottom, one tap away.
- Every click shows at once that it is working. Leaving a half-filled form (menu, tab or refresh) asks first.
  Disabling a user asks first. Error messages say what happened and what to do next.
- Phones: the menu folds away so each screen's own content comes first; the chart's tabs scroll in one row; every
  control is at least 44 by 44 px.

- **Measurement-based care** (Measures tab, Practice > To-do, Practice > Outcomes): a measure schedule per client
  (which measure, every 7 to 180 days), the caseload list of measures due or overdue with the next session date,
  and outcome indicators: first and latest score, improved / worsened / no reliable change (PHQ-9 and GAD-7: 5
  points; PCL-5: 10 points; WHO-5: 3 raw points), and for PHQ-9 and GAD-7 with a first score of 10 or more,
  response (a fall of 50% or more) and remission (below 5). The rules and their sources are shown on screen.
  Indicators describe scores; they are not a diagnosis.
- **WHO-5 Well-Being Index** (new measure): the published wording and scoring, raw 0-25 and the 0-100 percentage,
  a raw score below 13 shown as low well-being. Free to use without permission (WHO Collaborating Centre,
  Frederiksborg).
- **Psychotherapy notes** (new chart tab, clinicians): the author's own process notes, visible only to the author,
  PIN-signed, sealed and locked, entered-in-error instead of deleted. They are stored outside the chart file, so
  they are never in the chart view, the full-record export, the subject-access export or the FHIR / HL7 export.
  The author can print their own notes for a disclosure they decide to make.
- **Safety plans** (new chart tab): seven parts written with the client (warning signs, own coping steps, people
  and places for distraction, people to ask for help, professionals and crisis lines, making surroundings safer,
  reasons for living), PIN-signed versions (a revision starts a new version, earlier ones are kept), and a printed
  copy for the client with the local emergency number from the clinic's region. Not a risk assessment.
- **Releases of information and the disclosure log** (new chart tab, clinicians, nurses and front desk): releases
  to, from or with a named person or organisation, signed by the client (typed name and date, as client,
  guardian or representative) and witnessed with a staff PIN, sealed, with an expiry date or event, revocable;
  psychotherapy notes only on a release of their own. Each disclosure is logged (date, recipient, what, purpose,
  basis, method); a disclosure under a release is refused if that release did not allow it on that date. The
  accounting PDF leaves out disclosures under a release, at the client's request and for treatment, payment or
  operations (an "everything" PDF lists all of them).
- **Episodes of care and discharge** (on the chart's care plan tab): a discharge summary (reason, summary, recommendations,
  referrals) signed with the author's PIN discharges the client and seals the episode's measure results with it;
  reopening care starts a new episode. Discharged clients leave the due list, authorization alerts and estimate
  reminders. Front desk sees the status only.
- **Payer authorizations** (Billing tab): payer, number, visits authorized, visits used before, dates and
  optional service codes; visits used = attended appointments in the dates. Alerts: expired, ends within 14
  days, all used, 2 or fewer left, more booked than left, booked after the end date.
- **Good Faith Estimates** (Billing tab, US No Surprises Act): expected services with codes, quantities and fees
  over up to 12 months, optional ICD-10 codes, issued as a sealed, numbered PDF with a notice the clinic can
  edit (the starting text follows the federal model form; check it is current). The billed amount in the period
  is compared with the estimate and $400 or more above it is flagged. Optional reminder: self-pay clients with a
  session in the next 30 days and no current estimate. No compliance claim.
- **Client statements and A/R aging** (Billing tab and Practice > Billing): a statement PDF (charges, payments,
  amount due by age) and practice-wide aging in 0-30 / 31-60 / 61-90 / over 90 day buckets with credits and a CSV.
  Linked payments go to their invoice, other payments to the oldest open invoices first.
- **Practice > To-do**: attended sessions with no note or only a draft note, note signing time (within 1 day,
  3 days, later), attended sessions not billed, measures due, authorization alerts and self-pay clients without a
  current estimate; each list follows the user's role.
- **Note templates**: intake assessment (biopsychosocial) and mental status examination headings, next to SOAP,
  DAP, BIRP and progress notes. Headings only; the clinician writes every word.
- The access report filters the new audit actions (`mbc.`, `psynote.`, `safety.`, `release.`, `disclosure.`,
  `discharge.`, `auth.`, `gfe.`, `statement.`, `aging.`, `todo.`); psychotherapy note entries carry ids only.
- Fixes: the access report's date filter uses this computer's calendar day (it used the UTC day, so evening
  entries fell on the next day), and the release drill reads the shortcut name the installer creates.

## Practice suite (2026-11-20)

Everything below runs on this computer only, is stored in the same encrypted, journaled, audited record store, and
works in all 14 languages (the 13 non-English catalogs are machine drafts, as before).

- **Letters tab** (new): school, work, referral and to-whom-it-may-concern letters with editable starting
  wording; signed with the author's PIN, sealed (verification code on the PDF), locked after signing, printable.
- **Waitlist** (new, Practice > Calendar): clients waiting for a slot, preferred clinician, days and times; mark
  booked or remove (history kept).
- **Groups** (new, Practice > Groups): members, one booking for the whole group, and one group note signed into
  each attending member's chart with an optional private line per member (other members are never named by the
  app; the shared text is the author's responsibility).
- **Sliding-scale fees** (new): tiers with a percent discount; a client's tier applies to new invoices (shown on
  the invoice and superbill); issued invoices keep their discount.
- **CMS-1500 (02/12) field sheet** (new, PDF from an issued invoice): every box with its value from the invoice,
  the client's insurance details and the practice profile, to copy onto the official red-ink form or into a payer
  portal. **Not validated with a clearinghouse or payer.** No 837P file and no claim submission.
- **Practice > Dashboard** (new): for a chosen period, billed / collected / written off / outstanding money and
  collected per attended visit (billing roles), attended visits, no-show and late-cancel rates and new vs returning
  clients (scheduling roles), and the average PHQ-9 / GAD-7 change across the caseload with how many clients
  improved or worsened by 5 or more points (clinical roles). Computed from this computer's records only.
- **Reminders** (new, Practice > Calendar): the next day's still-scheduled appointments with the client's phone and
  a reminder text to copy (clinic-editable wording with [name], [date], [time], [clinician], [practice], [phone]),
  and a record of what happened (called, texted, left a message, no answer, wrong number). Nothing is sent from
  Stark Covenant; e-mail or SMS sending is not included.
- The medications and allergies cards now say that no drug or allergy interaction checking is done.
- **Intake and consent forms** (new): Practice > Forms lets a clinician or admin design forms (short and long
  answers, dates, numbers, yes/no, one or several choices, text to read, required flags, a statement above the
  signature). Saving a change makes a new template version. In the chart's Forms tab, staff fill a form in with
  the client, who signs by typed full name and date (as client, parent/guardian or representative), witnessed
  with the staff member's PIN. The signed form is sealed in the chart, re-verified on every view, amended only as
  a new sealed version (earlier versions kept), marked entered-in-error rather than deleted, printable as PDF,
  included in the full-record export, and every step is in the audit trail (access report filter "form").
- **Calendar file (.ics)** (new): Practice > Calendar saves one clinician's or everyone's appointments (up to a year)
  as a standard calendar file on this computer, for import by hand. Nothing is sent. Client names: initials
  (default), full or none; appointment notes are never written. The file is not encrypted.
- **AUDIT-C and PCL-5** (new, Measures tab) with the published wording and scoring and the same trend chart.
  AUDIT-C (WHO AUDIT questions 1-3, 0-12): positive at 3 or more for women and 4 or more for men (the clinician
  picks the cut-point); question 1 "Never" scores the other two 0. PCL-5 (0-80): below 31, 31-32 and 33 or more
  (the National Center for PTSD cut-point range 31-33), cluster scores B/C/D/E and the provisional DSM-5 pattern
  (items scored 2 or higher: 1 B, 1 C, 2 D, 2 E). The PCL-5 is public domain (National Center for PTSD); the AUDIT-C is
  public domain (Bush et al. 1998, NIH/NLM) and the WHO AUDIT may be reproduced freely.
- **Practice > Today**: today's appointments, your unsigned notes, notes waiting for your co-signature, treatment
  plan reviews due within 14 days, and the largest open balances.
- **Practice > Calendar**: a week view per clinician, booking with repeats (every 1-4 weeks, up to 52 visits), an
  overlap check with "book anyway", arrived / completed / no-show / cancelled / late-cancellation statuses with
  reasons, "cancel the rest of this series", moving an appointment, and no-show and late-cancellation counts and
  rate per patient and per week. Times are the clinic's local wall-clock time.
- **Note templates**: SOAP, DAP, BIRP and progress note sections under New note. The sections become the note text,
  so the existing draft, PIN sign, lock and signed-addendum rules apply unchanged.
- **Supervision co-signature** (plans with roles): an administrator names a supervisor for a clinician under Users.
  That clinician's signed notes stay locked and wait for the supervisor's own PIN co-signature, which is sealed
  and re-verified every time the chart opens.
- **Measures tab: PHQ-9 and GAD-7** with the published English wording, the published scoring (sum of 0-3 items)
  and severity bands (PHQ-9: 0-4, 5-9, 10-14, 15-19, 20-27; GAD-7: 0-4, 5-9, 10-14, 15-21), a notice whenever
  PHQ-9 item 9 is answered above "Not at all", a trend chart and entered-in-error correction. Every item must be
  answered (no prorating). Scores support screening and monitoring; they are not a diagnosis. PHQ-9 and GAD-7
  were developed by Drs. Robert L. Spitzer, Janet B.W. Williams, Kurt Kroenke and colleagues, with an educational
  grant from Pfizer Inc.; no permission is required to reproduce, translate, display or distribute them.
- **Treatment plan tab**: problems, strengths, goals and objectives with target dates and statuses, a review date,
  PIN-signed versions (editing a signed plan starts a new version; every earlier version is kept).
- **Billing tab and Practice > Billing**: practice details, provider NPIs (check digit verified), a fee schedule,
  draft invoices with CPT/HCPCS service codes, modifiers, units, place of service and ICD-10 diagnosis pointers,
  issue (then locked) or void, payments, refunds and write-offs, balances per patient, and a **superbill PDF**.
  Codes are typed by you and checked for format only: no code set or code description is bundled. Stark Covenant
  does not send claims or check eligibility.
- **Full record export** (chart header): the complete chart as a self-checking JSON file (with appointments,
  billing and signature checks) and as a readable PDF.
- **Access report** (Audit trail screen, administrators): the sealed audit trail filtered by patient, user, action
  and date, with names, and a CSV download.
- Roles: front desk gains scheduling and billing (no notes, vitals or measures); nurses gain scheduling and
  measures; clinicians gain all of it plus treatment plans and co-signing.

## Read this first: patient data

- Stark Covenant is **not a certified EHR** and **not a medical device**.
- It makes **no HIPAA, GDPR or other regulatory compliance claim**.
- **You, the buyer, must complete your own legal, privacy, security and compliance review before you enter
  real patient data.** That review covers your jurisdiction, your hardware, backups and who can reach the PC.
- It makes **no automated diagnosis and no treatment decisions**. Each clinical entry is made by a person and
  signed by that person. The local helper (`self_learner`) only makes suggestions. A suggestion goes into a
  record only after the clinician signs it off with their PIN.

## Updates

- Version 1.1.0 is a full (stable) release. A stable install takes stable releases only; an install from the
  earlier test channel moves to the stable releases automatically.
- Update checks are **off until the clinic allows them**: a box on the first-run screen (unticked by default), and
  later the Updates card on the **Licence** screen, where an administrator turns them on or off.
- When allowed, the app fetches the signed update list at start-up from
  `https://ams513.github.io/stark/updates/covenant/latest.json`, and only if that cannot be reached, from the earlier
  address `https://ams513.github.io/gorgon/updates/covenant/latest.json` (the same signature check applies to
  both). An address set in `covenant_update.json` or `STARK_COVENANT_UPDATE_URL` is used on its own. Nothing about
  you or your patients is sent; like any web visit, the request shows
  your internet address to GitHub. An update never replaces the vault, records, licence, audit trail or update
  settings, and a version that fails its start-up health check is rolled back and not offered again.
- Some names are kept from earlier versions on purpose, so existing installs, vaults, licences and USB key shares
  keep working: the program folder `%LOCALAPPDATA%\GorgonCovenant` (and the folder inside the zip), the USB file
  `GORGON_COVENANT.KEYSHARE`, the `GORGON_COVENANT_*` environment variables (still read; `STARK_COVENANT_DATA` and
  `STARK_COVENANT_UPDATE_URL` are the new names), the identifiers inside licence keys, and the record and
  export formats (for example HL7 sender `GORGON_COVENANT`).

## Install (Windows, per user, no admin rights)

1. Unzip the package and double-click `install_chart.bat`.
2. The installer:
   - copies the app to `%LOCALAPPDATA%\GorgonCovenant`. Nothing already in that folder is deleted, and the
     security vault's own `vault` folder is left alone.
   - creates the data folder `Documents\Stark_Covenant_Data`. It uses your real Documents folder, including
     a redirected one. Existing data is never touched (an older `Gorgon_Covenant_Data` is left as it is and
     copied by the app, see "What's new").
   - creates a Desktop shortcut called **Stark Covenant** (pointing at `START_STARK_COVENANT.bat`) and starts
     the app.
3. On the first start you create the first account (username, password of 10+ characters, and a signing PIN of
   4-12 digits).

The installer works with paths that contain spaces, `& ( ) ^ ! % '`. It takes no arguments. If your Documents
folder is synced to a cloud service, think about that in your compliance review: records are encrypted, but
they still leave the PC.

Run without installing: `python clinic_app.py [--data DIR] [--no-browser] [--demo] [--lan] [--port 8600-8699]`.
Restore a backup into a new folder: `python clinic_app.py --restore BACKUP_FILE NEW_EMPTY_FOLDER`.

## Encryption and DEMO mode

Everything at rest is sealed with `security_vault` (OS AES-256-GCM; its keys are split between this PC, a USB
stick and a recovery code; see `security_vault.py`). This covers records, the user file, documents, the
audit key and backups.

**Opening the vault (what staff see).**
- **First run, no vault yet:** a "Create your secure vault" screen.
  1. Choose the USB stick from the list. "Look for USB sticks again" refreshes it.
  2. Optionally set an extra password for this computer.
  3. Press **Create secure vault**. This uses `Vault(root).create(usb, k=2, n=3)`.
  4. The recovery code is shown **once**, with **Print** and **Save as a text file** buttons. Records open only
     after the person ticks "I have printed or saved the code" and types the code back.
- **Every later start:** with the USB stick plugged in, the vault opens by itself (this computer's key + USB).
  Without it, an "Unlock your records" screen asks for the USB stick or the recovery code (this computer's key
  + code), plus the extra password if one was set. One key alone never opens it. Ten failed attempts pause
  unlocking for a minute.
- The vault folder is `security_vault`'s default (`%LOCALAPPDATA%\GorgonCovenant\vault`); `--vault-dir` changes
  it. The vault keys are wiped from memory when the app stops.

**DEMO mode** is used only when the person chooses it on those screens ("Use DEMO mode", which asks for
confirmation first). It is also used when `--demo` is given, when `security_vault` is not installed, or when
the computer has no OS AES-GCM. In DEMO mode:
- it uses a separate folder, `demo_synthetic`, with synthetic patients only, and nothing is encrypted;
- a red banner is shown on every screen, and every patient is marked "synthetic".

Encrypted records in `records\` are never opened or changed in DEMO mode. If the vault opens but cannot
decrypt the existing records (wrong vault), the app refuses to work and changes nothing.

## Emergency screen

An **Emergency** link sits at the top of every screen. A big red "Emergency - call for help (no sign-in
needed)" button appears on the sign-in, setup, unlock, create-vault and main screens. It opens `/comms/`,
which is the `emergency_comms` module (see `EMERGENCY_README.md`).
- The emergency page and the number list work without signing in, even while the vault is still locked.
- Family contacts, the partner directory, settings and call keys need a signed-in clinic session.
- The module checks its own requests. clinic_app's Host/DNS-rebinding and LAN checks still apply to `/comms`.
- Its data goes to `Stark_Covenant_Data\comms` (vault-encrypted), or to `comms_no_vault` when there is no
  vault. When the vault opens after start-up, the module is mounted again with the vault.
- It does not replace calling your local emergency number directly, and a connection cannot be guaranteed
  (no signal, no credit, no internet, network outages).

## Plans (from `license_guard`; missing or invalid licence = Baby)

| Plan | Price | Users | Devices (licence seats) | Scale up to | Roles | LAN opt-in |
|---|---|---|---|---|---|---|
| Baby | $0 | 1 (owner, all permissions) | 1 | Individual | no | no |
| Solo | $50 one-time | 1 | 2 | Individual | no | no |
| Practice | $200 one-time (4 users); Clinic edition $500 (10 users) | up to 4 (Clinic edition 10) | 1 per user | Clinic | yes | yes |
| Humanitarian | $50 one-time | 15 (same features as Practice) | 10 | Continent | yes | yes |

Fair pricing (`license_guard.FAIRNESS_RULES`, shown on the Licence screen in every language):

- Each licence is paid once: no subscription and no setup fees.
- Public-sector and non-profit operators in World Bank low- and lower-middle-income countries pay 50% of the
  listed price on every plan.
- Registered humanitarian organisations use the Humanitarian plan ($50 one-time).
- Practice covers 2 to 4 users and Clinic 5 to 10 users, each at one flat price.
- If you stop using Stark Covenant, your records stay readable and exportable.

A downgrade or an expired licence never locks anyone out of existing records. It only stops new users from
being added.

### Activating a licence

1. Get a licence key (it starts with `GC1.`) from Stark for the plan you chose. If the key must be tied to
   particular computers, send the device code first: it is on the **Licence** screen, or run
   `python license_guard.py device-code`.
2. Sign in as the administrator (or the owner on the Baby plan) and open **Licence** in the tools on the left.
3. Paste the key and press **Activate on this device**. The screen shows the plan, the result and, for a paid
   plan, the date it is paid until. Without the app: `python license_guard.py activate GC1.xxxxx.yyyyy`.
4. Moving to another computer: on the old one press **Deactivate this device** (this gives the seat back; the
   licence file is kept as `license.json.released`), then activate the key on the new one.

The licence file lives in `%LOCALAPPDATA%\GorgonCovenant\licence\license.json` (the environment variable
`COVENANT_LICENSE_FILE` points it elsewhere). Everything is checked on this computer; nothing is sent.
A key that is invalid, revoked or expired (after its offline grace days) only puts the app back on the Baby
plan: records stay readable and exportable.

## Languages and regions

- 14 languages: English (the source) plus Spanish, French, Portuguese (Brazil), Arabic, Hindi, Chinese
  (Simplified), Swahili, Indonesian, Russian, Bengali, Urdu, German and Japanese. Every language except English
  is a **machine draft that needs review by a qualified native speaker**; the app says so on every screen in a
  draft language and shows clinical words and safety wording with the English beside them.
- Choose the language with the picker at the top of every screen; it is saved per user. Arabic and Urdu (and
  Hebrew and Persian once catalogs exist) use a right-to-left layout.
- **My account > Language and region**: date format, first day of the week, 12/24-hour clock, time zone,
  units (metric or US), blood glucose (mmol/L or mg/dL), name order (given or family name first) and digits.
  The administrator sets the clinic default under **Users**. Stored values never change: dates are ISO, times
  UTC, vitals metric, glucose mmol/L, phones also stored in E.164.
- Reviewing a translation: `python i18n.py export-review fr fr_review.csv`, give the sheet to a native-speaking
  reviewer, then `python i18n.py import-review fr fr_reviewed.csv`. A string counts as reviewed only with a
  reviewer name and review date. See `WORLDWIDE.md` for what is ready, what is not, and what a distributor
  must do before deploying in a new country.

## Roles (least privilege)

| Role | Can |
|---|---|
| clinician | demographics, encounters, vitals, notes + sign + co-sign, allergies, medications, problems, documents, suggestions, measures, treatment plans, scheduling, billing, intake/consent forms (design and complete), psychotherapy notes (own only), safety plans, discharge summaries, releases and the disclosure log |
| nurse | like clinician except medications and problems (read only), treatment plans, safety plans and discharge (read only), billing, co-signing, form design and psychotherapy notes; completes intake/consent forms; releases and the disclosure log; measure schedules |
| front_desk | demographics, check-in encounters, document upload, scheduling, billing (invoices show diagnosis codes), authorizations, Good Faith Estimates, statements and aging, completing and reading intake/consent forms, releases and the disclosure log, a client's in-care / discharged status. No notes, vitals or measures; cannot open documents. |
| admin | users (create, roles, disable, clear a lockout after failed sign-in attempts, reset), audit trail + integrity check, backups, form templates (design only). No clinical data. |
| owner | Baby plan's single user: everything |

In Clinic and higher plans the first account is an **admin** only. Create clinician, nurse and front-desk
accounts from the Users screen. At least one active admin always remains.

## Sign-off lock

- A note is a **draft** until its author signs it with their PIN. The PIN is an e-signature, kept separate from
  the password and hashed with scrypt. Five wrong PINs lock signing for 15 minutes.
- Signing stores a content hash and an HMAC signature, and the note is then **locked**: an edit is answered with
  HTTP 423. Later information goes into **addenda**, and each addendum is signed with the PIN when it is
  added. The chart shows "signature valid" by checking the signature again each time it is opened.
- Vitals are never edited. A wrong entry is marked "entered in error" with a reason and stays visible.
  Allergy, medication and problem entries are versioned: each edit keeps the previous version, and nothing is
  deleted.
- **AI / self_learner:** suggestions are labelled "Suggestion - requires clinician sign-off". `POST /api/ai/accept`
  needs the clinician's PIN. It inserts only into that clinician's own *draft* and records provenance (who
  accepted it and when), and the draft still has to be signed. Signed notes cannot receive suggestions.

## Durability, audit trail, backups

- **Every write is one journaled transaction.** First, a record of the (encrypted) file bytes with their
  SHA-256 is appended to `.journal.log` and fsync'd. Then `.tmp` is written, fsync'd, read back and checked
  against the SHA-256, and moved into place with `os.replace`. After that the audit entry is added, then a
  commit record. `recover()` runs at every start: it replays unfinished transactions, discards torn journal
  lines and stray `.tmp` files, and cuts a torn last audit line (kept as `audit.torn-*.frag`).
- **Audit trail** (`records\audit\audit.log`): append-only, hash-chained, and each entry is sealed with
  HMAC-SHA256 under a per-install key held by the vault. It records ids, actions and SHA-256 values only, never
  names or note text. Chart views, document views, logins, failed logins, lockouts, user changes and backups
  are all logged. **Verify integrity** (admin) detects edited, inserted, deleted, reordered or cut-off entries.
  It also detects record files whose bytes no longer match what the audit trail sealed.
- **Backups:** an encrypted zip of the whole store, written at start-up, every 30 minutes after changes, and on
  demand. The newest 20 are kept in `Stark_Covenant_Data\backups\records\`. Keep a copy off this PC as well.

## Network and session security

- The app listens on 127.0.0.1 only, on the first free port 8600-8699. LAN access needs `--lan` **and** a plan
  that includes it. It is plain HTTP, so use it only on a trusted network.
- Each user has their own login (hashlib.scrypt). Five failed logins lock the account for 15 minutes, and the
  lock survives restarts. Unknown usernames get the same answer and take the same time.
- The session cookie is HttpOnly and SameSite=Strict, and every POST needs a CSRF token. The Host header is
  checked against DNS rebinding, and cross-origin or cross-site POSTs are refused. A strict CSP blocks
  framing.
- Idle auto-lock happens after 10 minutes, both on the server and in the browser (`GORGON_COVENANT_IDLE`
  changes the number of seconds). A **Lock** button is always visible.
- Each request has its own fault boundary: an error is logged with its traceback to
  `Stark_Covenant_Data\.covenant_health.log`, the browser gets a friendly message, and the server keeps
  running. A watchdog restarts a listener that has died.
- Paths are sandboxed: names are checked lexically (rejects `..`, absolute paths, drive letters, UNC and
  device paths, `:` alternate data streams, reserved names such as CON/NUL/COM1, control characters, and
  trailing dots or spaces), and `realpath` containment blocks junction and symlink escapes. Ids must match
  `^[A-Z][0-9a-f]{12}$`. Uploaded file names are reduced to a safe basename, and downloads are always sent as
  attachments.

## Data folder layout

```
Stark_Covenant_Data\
  records\              encrypted store (normal mode)
    charts\P*.bin       one encrypted chart per patient
    docs\D*.bin         encrypted documents
    sys\users.bin       users (scrypt hashes), sys\index.bin patient index, sys\audit.key (sealed)
    sys\sched-YYYY-MM.bin  appointments of one month (+ sys\sched-index.bin), sys\billing.bin billing,
                        sys\worklist.bin the Today index (rebuilt from the charts when missing)
    sys\psy-P*.bin      psychotherapy notes of one client (outside the chart on purpose)
    sys\auths.bin, sys\estimates.bin, sys\care-index.bin  authorizations, Good Faith Estimates, schedules and
                        discharged clients
    audit\audit.log     hash-chained audit trail (+ audit.head)
    .journal.log        write-ahead journal (normally empty)
  demo_synthetic\       DEMO mode store (synthetic only, unencrypted)
  backups\records\      rolling encrypted backups (*.gcbak)
  .covenant_health.log  technical errors (no request bodies)
```

## HTTP API (JSON; every route except status/setup/login needs the session cookie, and every POST needs `X-CSRF-Token`)

`GET /api/status` · `POST /api/setup` · `POST /api/login` · `POST /api/logout` · `GET /api/me` ·
`POST /api/me/secret` · `GET /api/patients?q=` · `POST /api/patients` · `GET /api/patient?id=` ·
`POST /api/patient/update` · `POST /api/encounters` · `POST /api/vitals` · `POST /api/vitals/error` ·
`POST /api/notes` · `POST /api/notes/update` · `POST /api/notes/sign` · `POST /api/notes/addendum` ·
`POST /api/items` · `POST /api/items/update` (kind = allergies | medications | problems) ·
`POST /api/documents` · `GET /api/document?patient=&doc=` · `GET /api/audit` · `GET /api/audit/verify` ·
`GET /api/users` · `POST /api/users` · `POST /api/users/update` · `POST /api/backup` · `GET /api/backups` ·
`GET /api/ai/suggest` · `POST /api/ai/accept` · `GET /api/interop/export?id=<patient>` (only when `interop_bridge` is installed: returns a FHIR R4 transaction Bundle and an HL7 v2.5.1 ADT^A08 message for one patient, checked structurally, not by the official HL7 validator).
`POST /api/notes/cosign` · practice suite (`practice_suite`, routes under `/api/pm/`): `info`, `staff`, `supervisor`,
`today`, `appts` (+ `/patient`, `/status`, `/move`), `measures` (+ `/error`), `plans` (+ `/sign`), `billing`
(+ `/profile`, `/summary`), `invoices` (+ `/issue`), `payments`, `void`, `superbill`, `export.json`, `export.pdf`,
`access`, `access.csv`. Care suite (`care_suite`, routes under `/api/care/`): `mbc` (+ `/stop`, `/due`), `outcomes`,
`psy` (+ `/sign`, `/error`, `/pdf`), `safety` (+ `/sign`, `/error`, `/pdf`), `releases` (+ `/revoke`, `/error`, `/pdf`),
`disclosures` (+ `/error`, `/pdf`), `episodes`, `discharge` (+ `/sign`, `/pdf`), `reopen`, `auths` (+ `/close`),
`gfe` (+ `/issue`, `/void`, `/pdf`, `/settings`), `aging`, `aging.csv`, `statement`, `gaps`, `todo`.
Before records open (this computer only, no login): `GET /api/vault/status` · `POST /api/vault/create` ·
`POST /api/vault/confirm` · `POST /api/vault/unlock` · `POST /api/vault/demo`. Every other `/api` route answers
503 until the vault is open. `/comms/...` belongs to `emergency_comms`.

Python API: `Clinic(data_root, vault="auto"|obj|None, plan=None, demo=False, vault_root=None)` (`.state`,
`vault_status/vault_create/vault_confirm/vault_unlock/choose_demo`) holds all record logic, with no
HTTP involved. `CovenantApp(...).start()` is the server. The module also provides `Store`, `AuditTrail`,
`safe_join`, `restore_backup`, `features_for_plan`, `resolve_plan` and `find_vault`.

## Optional modules (owned separately; each one missing or broken degrades safely)

`security_vault` (else DEMO), `license_guard` (else Baby), `auto_updater` (confirm_healthy + update check),
`self_learner` (else no suggestions), `interop_bridge` (else no export), `practice_suite` (else no Today, Calendar,
Measures, Treatment plan, Billing or full export screens), `care_suite` (needs `practice_suite`; else no To-do,
Outcomes, measure schedule, safety plan, psychotherapy note, release, discharge, authorization, estimate,
statement or aging screens), `polyglot_engine` (else PDF exports become text files),
`optical_airgap`.
The status screen shows which ones are present.

## Tests

`python -m unittest discover -s tests` runs the whole suite (825 tests on 2026-11-20, 1.1.0). Among them, `tests.test_clinic_app`
covers:
- unit tests;
- a real HTTP server in a child process: login, CSRF/Origin/Host, patient, note, sign-off lock, addendum,
  document, traversal, audit tamper, raising handler, crash plus restart recovery, concurrent writes, and the
  emergency module mount;
- power cuts at every byte of the journal commit marker, and across the journal transaction record and the
  audit entry (each one restarted twice: no old data may come back);
- malformed audit lines, which are reported and never crash the check;
- the watchdog;
- the vault create/confirm/unlock/DEMO flow with the real `security_vault` and a simulated USB stick;
- the installer run under hostile path characters, and the shortcut's Covenant icon and test-channel notice.

Worldwide release tests: `tests.test_i18n` (catalogs, plurals checked against ICU, the review workflow, no
hard-coded English in the pages, RTL, translated PDFs), `tests.test_regional` (dates, week start, units, glucose,
names, addresses, E.164 phones, time zones), `tests.test_worldwide_server` (per-user preferences, clinic
defaults, the Licence screen, product marks), `tests.test_beta_channel` (test-channel wording, update channels and
promotion to stable) and `tests.test_worldwide_ui` (headless Edge or Chrome: first-load size, emulated 2G, 1366x768 and
360x640 in eight languages, keyboard-only use and in-house accessibility checks; skipped when no browser is
installed or `GORGON_SKIP_BROWSER` is set).

Care suite: `tests.test_care_suite` (WHO-5 at every total; outcome rules at their boundaries; the measure schedule
and due list; psychotherapy notes kept out of every view and export; safety plan, release, discharge and estimate
seals, locks and tamper checks; the disclosure accounting; authorization alerts; a randomized check that aging
buckets always add up to the balance; gaps and the to-do list by role; HTTP, CSRF and roles; every catalog; and,
with Chrome or Edge, the new screens against the accessibility rules).

Practice suite: `tests.test_practice_suite` (PHQ-9 and GAD-7 scoring at every band boundary and every possible
total, the item 9 notice and refused answers; co-signature, treatment plan and invoice seals and locks; scheduling;
billing maths and code-format checks; full record export; access report; the Today worklist; encryption at rest;
HTTP, CSRF and roles; catalogs; and, with Chrome or Edge, the real screens against the accessibility rules).

The tests use a test-only stand-in vault where needed, so they also run without a vault on the machine.

## Honest limits

- fsync is only as honest as the drive. A disk that lies about its write cache can still lose the last write.
- An attacker with full write access **and** the decrypted audit key could forge a new chain. Off-site backups
  are the defence.
- LAN mode is plain HTTP (no TLS).
- The browser can keep copies of pages it has shown. Lock the PC as well as the app.

## About

Stark Covenant 1.1.0. Local-first clinic records. A Stark product.

*People and AI, working together, remember that God is the original Designer and Healer.*
