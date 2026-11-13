# S001 — Patient registration (MVP)

| | |
| :-- | :-- |
| Sprint | 2-week MVP |
| Depends on | 0017 (permission catalogue and auth deps) |
| Plan | §6.4 `patients`, §19 `POST /patients`, `GET /patients`, `GET /patients/:id`, `PATCH /patients/:id` |

## Context

The demo requires registering a patient and seeing their record. This is a
minimal slice of the full M2 patient module: registration with auto-generated
patient IDs, basic CRUD, and a list endpoint. Duplicate detection, merge,
care-team scoping, and search are deferred.

## Scope

- [ ] Add `app/models/patient.py` with the `Patient` shape from §6.4:
      `patient_number`, `status`, `name { given[], family }`, `date_of_birth`,
      `dob_approximate`, `sex`, `phones[]`, `address`, `next_of_kin[]`,
      `registration_date`, `payer`, `care_team[]`, `legacy_number`,
      `paper_file`, `version`, timestamps.
- [ ] Add `app/models/counter.py` with a simple atomic counter model for the
      `counters` collection, used to generate sequential patient numbers.
- [ ] Add `app/db/patients.py` over a `TenantCollection`: `create`,
      `get_by_id`, `list` (cursor paging, filter by status), `update`.
- [ ] Add `app/db/counters.py`: `next_value(tenant_id, key)` using
      `find_one_and_update` with `$inc` and `upsert=True` on the raw Motor
      collection under `run_as_system`.
- [ ] Add `app/services/patients.py`: generate the patient number from the
      tenant config's `patient_id_format` using `generate_patient_id`,
      compute `display_name` from the name parts, normalise phone numbers
      (strip spaces, leading zero → country code), and compute `age_years`
      from `date_of_birth`.
- [ ] Add `app/schemas/patient.py`: `PatientCreate` (strict, `extra=forbid`),
      `PatientUpdate`, `PatientResponse`, `PatientListItem`.
- [ ] Add migration `0005_patient_indexes.py`: unique compound
      `{tenant_id, patient_number}`, and index on `{tenant_id, status}`.
- [ ] Add endpoints in `app/api/v1/routes/patients.py`:
  - `POST /patients` — `patient:create`, generates the patient number,
    returns the full patient with `201`.
  - `GET /patients` — `patient:read`, cursor paging, filter by `status`,
    `registered_from`, `registered_to`, sort by `registration_date` or `name`.
  - `GET /patients/:id` — `patient:read`, returns full patient.
  - `PATCH /patients/:id` — `patient:update`, `If-Match` version check,
    accepts `name`, `phones`, `address`, `next_of_kin`, `payer`,
    `paper_file` only.
- [ ] Add tests in `tests/test_patients.py`: model validation, patient
      number generation, CRUD through the HTTP client, version conflict on
      PATCH, and tenant isolation (patient from tenant A invisible to B).
- [ ] Update the seed script to create 2–3 sample patients per tenant with
      realistic Nigerian names and generated patient numbers.

## Acceptance criteria

- `POST /patients` returns a patient with a correctly formatted
  `patient_number` matching the tenant's config (e.g. `ACH-48291-2026`).
- Running the creation twice with different data produces different,
  non-colliding patient numbers.
- `GET /patients` returns only patients from the caller's tenant.
- `PATCH /patients/:id` with a stale `If-Match` returns `412`.
- The seed script creates sample patients and prints their IDs.

## Out of scope (deferred)

- Duplicate detection and `POST /patients/duplicate-check`.
- Patient merge, `POST /patients/merge`, merge history.
- Care-team scoping (`PolicyService`); all staff with `patient:read` can see
  all patients in their tenant.
- Fuzzy name search and `/search/patients`.
- Conditions, allergies.
- Patient summary and timeline endpoints.

