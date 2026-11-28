# Integrating consent_center and persona_ai into clinic_app

Owner of this integration: the clinic_app integrator. `consent_center.py` and `persona_ai.py` do not edit
`clinic_app.py`; they expose `mount(app)`.

## The exact 3 lines

In `clinic_app.py`, class `CovenantApp`, method `__init__`, directly **after** this existing line:

```python
        self.clinic.on_ready.append(self._remount_comms)
```

add:

```python
        for _name in ("consent_center", "persona_ai"):  # optional modules; each one degrades safely when missing
            _m = optional(_name)
            setattr(self, _name, _m.mount(self) if _m is not None and callable(getattr(_m, "mount", None)) else None)
```

That is all that is required. The test `tests/test_consent_persona.py::TestHttpIntegration` runs exactly these
three lines against a live `CovenantApp` and drives the routes over HTTP.

Optional, for the status screen only (not required): add `"consent_center", "persona_ai"` to the
`OPTIONAL_MODULES` tuple so `GET /api/status` lists them under `modules`.

## What `mount()` does

`consent_center.mount(app)` (never raises; returns `None` and writes to `.covenant_health.log` on any problem):

1. creates `ConsentCenter(clinic)` and sets `clinic.consent_center`;
2. adds the `/api/consent/...` routes to `clinic_app.ROUTES` (so login, CSRF, Origin, Host and LAN checks stay
   clinic_app's own; errors map to `HttpError` with the right status);
3. wraps `Clinic._learn` on that instance so `self_learner` learns only with consent, and shreds a clinician's
   learner profile when they withdraw `learning`;
4. starts an hourly retention purge in a daemon thread (not with `--test-hooks`).

`persona_ai.mount(app)` mounts `consent_center` first if needed, sets `clinic.persona_ai`, adds the
`/api/persona/...` routes, and registers the assistant's outbox with the consent center (export, erasure,
retention). The local model is used unless `--test-hooks` is on.

Both modules read `clinic.store` / `clinic.codec` at call time, so they work when the vault opens after start-up
(before that, their routes answer 503 like the rest of the API).

## Routes (JSON; all need the session cookie; every POST needs `X-CSRF-Token`)

| Method | Path | Body / query | Who |
|---|---|---|---|
| GET | `/api/consent/explain` | `?subject=&kind=patient|staff` | signed in |
| GET | `/api/consent/status` | `?subject=` | `demo.read` (patients), self or admin (staff) |
| POST | `/api/consent/grant` | `subject, purposes[], pin, method, language, guardian{name, relationship, authority_checked, child_assent}, details{purpose: fields}, explanation_version, jurisdiction` | `demo.write` (patients), the staff member (staff) |
| POST | `/api/consent/withdraw` | `subject, purposes[] or "all", reason` | same as grant; an admin may withdraw for staff |
| POST | `/api/consent/data` | `subject, purpose, data{}` | same as grant; only while consent is active |
| GET | `/api/consent/receipts` | `?subject=` | as status |
| GET | `/api/consent/export` | `?subject=` | `clin.read` (patients), self or admin (staff) |
| GET | `/api/consent/export.pdf` | `?subject=` | as export (file download) |
| POST | `/api/consent/erase` | `subject, pin, reason` | as grant |
| GET/POST | `/api/consent/settings` | `settings{}` | `users.manage` (admin / owner) |
| POST | `/api/consent/purge` | - | `users.manage` |
| POST | `/api/persona/respond` | `subject, text` | `demo.read` (patients), self (staff) |
| POST | `/api/persona/compose` | `subject, kind (welcome, reminder, coverage_followup, nutrition_info), fields{}` | as respond; needs that purpose's consent |
| GET/POST | `/api/persona/profile` | `subject, preferences{}` | as respond; POST only with `personalisation` consent |
| GET | `/api/persona/drafts` | `?subject=` | `clin.read` |
| POST | `/api/persona/draft/update` | `subject, draft, text` | `note.write`; 423 once signed |
| POST | `/api/persona/draft/approve` | `subject, draft, pin` | `note.sign` + signing PIN |
| GET | `/api/persona/alerts` | `?status=open|closed|all` | `clin.read` |
| POST | `/api/persona/alerts/ack` | `alert` | `clin.read` |
| GET | `/api/persona/style` | - | signed in |

## Settings the clinic should fill in first (`POST /api/consent/settings`)

`jurisdiction` (`us_hipaa`, `eu_gdpr`, `uk_gdpr`, `ca_pipeda`, `au_privacy`, `in_dpdp`, `br_lgpd`, `generic`),
`country` (ISO code for the local emergency number), `clinic_name`, `clinic_phone`, `clinic_email`,
`clinic_address`, `privacy_officer`, `policy_url`, `partners[]` (id, name, purpose, data, contact, country),
`consent_age` (override), `retention_days{}`, `research_expiry_days`, `proof_retention_days`,
`learner_requires_patient_consent` (default true), `crisis_resources[]` (verified local lines),
`crisis_alert_policy` (`notify_care_team` or `record_only`), `knowledge_sources[]` (the only facts the assistant
may cite: id, title, url, text, keywords, reviewed_by), `outbox_retention_days`.

## Data layout (inside the clinic store, so vault-encrypted, journaled, audited and backed up)

```
records\consent\settings.bin            clinic settings
records\consent\r\<P|U id>.bin          consent record: states, history, signed receipts
records\consent\d\<id>\<purpose>.bin    data held for one purpose (only its allowed fields)
records\consent\t\<keyed ref>.bin       erasure / closure tombstones (no personal data)
records\persona\<id>.bin                assistant messages, drafts, alerts for one person
records\persona\alerts.bin              open / closed alert index (ids only)
```

Deleted items are overwritten in place with an `{"erased": true}` placeholder through the normal journaled
commit, so `Verify integrity` keeps passing and the audit trail records the deletion.

## Operations

- After restoring a backup: sign in as admin, call `ConsentCenter.export_erasure_register()` **before** the
  restore (keep it off the PC) and `reapply_erasures()` after it.
- Review open assistant alerts every working day (`GET /api/persona/alerts`).
- Re-confirm consents shown as `needs_reconfirmation` (a child who has reached the consent age) or `expired`.
