# S002 — Visits and vitals (MVP)

| | |
| :-- | :-- |
| Sprint | 2-week MVP |
| Depends on | S001 (patient registration) |
| Plan | §6.4 `visits`, `vital_signs`, §20 endpoints |

## Context

A demo of the hospital flow requires creating a visit for a registered patient,
recording vital signs, and closing the visit. This is a minimal slice of the
full M2 clinical module. Clinical entries, prescriptions, and documents are
deferred.

## Scope

- [ ] Add `app/models/visit.py` with the `Visit` shape from §6.4:
      `patient_id`, `type` (validated against `tenant_config.visit_types`),
      `visit_date`, `attending_clinician_id`, `status` (`open` | `closed` |
      `cancelled`), `reason_for_visit`, `closed_at`, `closed_by`, `version`,
      timestamps.
- [ ] Add `app/models/vital_signs.py` with the `VitalSigns` shape from §6.4:
      `patient_id`, `visit_id`, `measured_at`, `recorded_by`,
      `temperature_c`, `pulse_bpm`, `systolic_mmhg`, `diastolic_mmhg`,
      `weight_kg`, `height_cm`, `bmi` (server-computed), `warnings[]`,
      `status`, `version`.
- [ ] Add `app/db/visits.py` over a `TenantCollection`: `create`,
      `get_by_id`, `list_for_patient` (cursor paging), `update`, `close`.
- [ ] Add `app/db/vital_signs.py` over a `TenantCollection`: `create`,
      `get_by_id`, `list_for_visit`, `list_for_patient`.
- [ ] Add `app/services/visits.py`: validate `type` against tenant config
      visit types, validate `attending_clinician_id` exists as a user in the
      tenant, update `patient.last_visit_date` on create.
- [ ] Add `app/services/vitals.py`: compute BMI when weight and height are
      present, validate vital sign ranges (reject physically impossible
      values with `422 vitals_invalid`, flag unusual values with
      `409 vitals_confirmation_required` unless `acknowledge_warnings` is
      true), store warnings on the record.
- [ ] Add schemas: `VisitCreate`, `VisitUpdate`, `VisitResponse`,
      `VitalsCreate`, `VitalsResponse`.
- [ ] Add migration `0006_visit_indexes.py`: compound index
      `{tenant_id, patient_id, status}` on visits, and
      `{tenant_id, visit_id}` on vital_signs.
- [ ] Add endpoints in `app/api/v1/routes/visits.py`:
  - `POST /patients/:id/visits` — `visit:create`, returns 201.
  - `GET /patients/:id/visits` — `visit:read`, cursor paging.
  - `GET /visits/:id` — `visit:read`, returns visit with nested vitals.
  - `PATCH /visits/:id` — `visit:update`, `If-Match`.
  - `POST /visits/:id/close` — `visit:close`, `If-Match`, checks for
    unsigned drafts (returns `409 unsigned_drafts_exist` with `force: false`;
    for MVP, no drafts exist so close always succeeds).
- [ ] Add endpoints in `app/api/v1/routes/vitals.py`:
  - `POST /visits/:id/vitals` — `vitals:write`, returns 201.
  - `GET /patients/:id/vitals` — `visit:read`, filter by `visit_id`,
    `from`, `to`, cursor paging.
  - `PATCH /vitals/:id` — `vitals:write`, mark as `entered_in_error`
    with reason.
- [ ] Add tests in `tests/test_visits.py` and `tests/test_vitals.py`:
      model validation, visit lifecycle (open → close), vitals recording,
      BMI computation, out-of-range warning flow, tenant isolation.
- [ ] Update the seed script to create 1–2 sample visits with vitals per
      seeded patient.

## Vital sign validation ranges

| Metric | Impossible (→ 422) | Unusual (→ 409 unless acknowledged) |
| :-- | :-- | :-- |
| `temperature_c` | < 25 or > 48 | < 34 or > 41 |
| `pulse_bpm` | < 10 or > 300 | < 40 or > 180 |
| `systolic_mmhg` | < 40 or > 300 | < 70 or > 200 |
| `diastolic_mmhg` | < 20 or > 200 | < 40 or > 130 |
| `weight_kg` | ≤ 0 or > 500 | < 2 or > 300 |
| `height_cm` | ≤ 0 or > 300 | < 30 or > 250 |

## Acceptance criteria

- `POST /patients/:id/visits` creates a visit and returns it with status
  `open`.
- `POST /visits/:id/vitals` records vitals and returns the computed BMI.
- Submitting a temperature of 41.5 returns `409` with a warning; resending
  with `acknowledge_warnings: true` saves it.
- Submitting a pulse of 900 returns `422 vitals_invalid`.
- `POST /visits/:id/close` transitions the visit to `closed` with
  `closed_at` and `closed_by` populated.
- `GET /patients/:id/visits` returns only the visits for the given patient
  in the caller's tenant.
- The seed script creates sample visits and prints a summary.

## Out of scope (deferred)

- Vitals trends endpoint (`GET /patients/:id/vitals/trends`).
- Clinical entries and the draft/sign workflow.
- Prescriptions.
- Denormalized fields (`has_allergies`, `clinician_ids`).
- Care-team scoping for visits.
- Visit type validation against `tenant_config.visit_types` is enforced but
  the seed script ensures the types exist.

