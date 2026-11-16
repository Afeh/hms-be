# Sprint Plan — MVP Demo (Nov 13–20, 2026)

| | |
| :-- | :-- |
| Delivery | **Friday, Nov 20, 2026** (demo) |
| Available | 4.5 development days: Fri 13 (half), Mon 16, Tue 17, Wed 18, Thu 19. Fri 20 is delivery only. |
| Goal | Demoable end-to-end flow: **Login → Register patient → Create visit → Record vitals → Close visit** |
| Branch | `feat/sprint-mvp` |
| Type | **Demo MVP — not a pilot, no real patient data** |

## Reality Check

The previous version of this plan assumed 10 working days and ~17–18.5 days of ticket estimates across 10 tickets. Delivery is now **7 calendar days / 4.5 working days** away, so the plan is re-cut rather than compressed:

- Only tickets on the demo path are in scope (5 tickets + 2 partials).
- Every ticket is narrowed below its original acceptance criteria; the trimmed parts are listed under **Deferred**.
- Thursday 17:00 is a hard feature freeze. Friday is demo and demo-blocking fixes only.
- The weekend is not counted. Working it is a contingency, not a plan input.

If this scope cannot be met by Thursday, the fallback is a shorter demo (login + register + search), not a later date. The date is fixed; scope is the variable.

> This plan assumes agent-assisted execution. That is why 4.5 days is plausible; it is still compressed, and any estimate overrun comes directly out of the demo steps in the fallback table.

---

## Already Done

| # | Ticket | Milestone | Status |
| :-- | :-- | :-- | :-- |
| 0001 | CI pipeline | M0 | ✅ Done |
| 0002 | Local infrastructure | M0 | ✅ Done (Mongo must be switched to a replica set — see Day 0) |
| 0003 | Migrations and indexes | M0 | ✅ Done |
| 0004 | Repository primitives | M0 | ✅ Done |
| 0005 | Transactional outbox | M0 | ✅ Done (integration tests currently fail on standalone Mongo) |
| 0006 | HTTP hardening and rate limits | M0 | ✅ Done |
| 0010 | Tenant records and seeding | M1 | ✅ Done |
| 0011 | Tenant configuration | M1 | ✅ Done |

---

## In Scope (Demo Path Only)

| Ticket | Trimmed deliverable | Est. |
| :-- | :-- | :-- |
| **0012** | 5 collections only: identities, memberships, roles, sessions, refresh_tokens. Repos + indexes + one migration. No invitations, no service accounts. | 1 day |
| **0013** | argon2id hashing, JWT access tokens, opaque refresh tokens with rotation, session create/revoke, transaction on login. No Redis caches, no idle/absolute timeout matrix. | 1 day |
| **0017** | Permission enum + catalogue, `get_current_user`, `require_permission`. DB lookup per request; no Redis permission cache; no exhaustive route-table test. | 0.75 day |
| **0014** | `POST /auth/login` only. IP rate limit, generic invalid-credentials response. No MFA endpoints, no per-account lockout counters. | 0.5 day |
| **0016** | `GET /auth/me` only. No profile PATCH, no session list/revoke UI endpoints. | 0.25 day |
| **S001** | Patient model, ID generation, `POST /patients`, `GET /patients/:id`, `GET /patients` (status filter, sort, cursor page). Migration via `mgm new`. No summary endpoint, no duplicate detection, no merge. | 1 day |
| **S002** | Visit and vitals models, `POST /patients/:id/visits`, `GET /patients/:id/visits`, `POST /visits/:id/vitals` (BMI + range validation), `POST /visits/:id/close`. No trends, no clinical notes. | 1 day |
| **Seed** | argon2 password, roles aligned to the permission catalogue, demo patient/visit data optional. | 0.25 day |

**Total: ~5.75 estimated days against 4.5 available.** The difference is absorbed by agent-assisted execution and by the fact that 0012/0013/0017 are narrower than their tickets. This is the main schedule risk; the fallback table below is the response if it is not absorbed.

---

## Out of Scope (Cut From the Original Plan)

| Cut | Reason |
| :-- | :-- |
| **0015** password reset/change | Not in the demo; requires email abstraction + outbox wiring; 1.5 days. |
| **0018** roles CRUD | Demo uses seeded roles; `scripts/seed.py` already creates them. |
| **S003** invitations | Not in the demo; depends on 0018. |
| **MFA** (verify, enroll, disable, recovery) | Not demoed; seed accounts are MFA-disabled. Keep `mfa_required` on roles as data only. |
| **Redis permission cache** | Premature at demo scale; per-request DB lookup is fast enough. |
| **Per-account lockout** | Keep IP rate limiting from 0006; account lockout is post-demo. |
| **Patient summary endpoint** | Demo step 6 uses existing GETs instead (see demo script). |
| **Care-team scoping, merge, duplicates, clinical entries, audit log, isolation suite** | Unchanged from the original deferral list. |

---

## Fixed Contracts (Decide Now, Do Not Revisit Mid-Sprint)

These resolve contradictions found in the tickets. The plan is authoritative for the sprint; update the tickets to match.

1. **One active membership per identity.** Login resolves the identity's single active membership; no tenant field on the login request. 0012 enforces this for the sprint and its multi-membership test is deferred. This contradicts 0012's current wording and 0014's "same email in two tenants" acceptance; that acceptance is out of scope.
2. **Email is globally unique** on `identities.email_normalized` (already implemented). Do not add per-tenant email uniqueness this sprint.
3. **Demo account and permissions.** `hospital_admin` must include, at minimum: `patient:create`, `patient:read`, `patient:update`, `visit:create`, `visit:read`, `visit:close`, `vitals:write`, plus its existing admin permissions. The seed must import role templates from the permission catalogue rather than defining its own copy. Demo logs in as `admin@acme.example` with MFA disabled.
4. **Seed hashing is argon2id.** The current SHA-256 seed hash will fail against 0013. The seed uses the same password helper as 0013, and the dev database is re-seeded after 0013 lands. No legacy hash support.
5. **Audit is a no-op interface.** 0014/0016 call a single `audit.record(...)` that writes structured logs now and is backed by 0021 later. Do not block auth on 0021.
6. **Migration names are generated.** Use `uv run mgm new <name>`; no hardcoded `0005`/`0006` in tickets. Current tree ends at `0004`.
7. **No auth bypass.** Patient routes are protected by `require_permission`; during development, test them after 0014 lands or via direct service-level tests. Any debug bypass must be gated by `settings.debug` and fail closed, but none should be needed.
8. **Demo script is the acceptance test.** It lives in `tests/test_demo_flow.py` and runs against a freshly seeded database.

---

## Demo Scenario (Final)

```
1. Admin logs in          →  POST /auth/login (email + password)
2. Gets profile           →  GET /auth/me (permissions, roles, tenant)
3. Registers a patient    →  POST /patients (name, DOB, sex, phone, address)
                              ← Returns patient with auto-generated ID: ACH-48291-2026
4. Creates a visit        →  POST /patients/:id/visits (type, date, clinician, reason)
5. Records vitals         →  POST /visits/:id/vitals (temp, pulse, BP, weight, height)
                              ← Server computes BMI, validates ranges
6. Views patient chart    →  GET /patients/:id
                              GET /patients/:id/visits
                              (visit documents include their vitals — no summary endpoint)
7. Closes the visit       →  POST /visits/:id/close
8. Searches patients      →  GET /patients?status=active&sort=-registration_date
```

---

## Schedule

### Fri 13 (half day) — Preflight

- [ ] Switch local MongoDB to a replica set (`--replSet rs0`, `rs.initiate()`, `?replicaSet=rs0` in `.env`); confirm `tests/test_outbox_integration.py` passes.
- [ ] Ensure CI uses a replica set or explicitly skips those tests (prefer replica set).
- [ ] Create `feat/sprint-mvp`.
- [ ] Freeze route list, request/response shapes, permission strings, and the demo script above.
- [ ] Write `tests/test_demo_flow.py` as a skipped end-to-end test (fails until the routes exist).
- [ ] Update 0012/0014 acceptance criteria to the fixed contracts (single membership, login-only, MFA deferred).

**Exit:** outbox tests green on a replica set; demo test file exists and is collected.

### Mon 16 — 0012 + password helper

- [ ] 5 models/repos/indexes, migration via `mgm new`.
- [ ] argon2id password helper; seed switches to it and imports catalogue role templates; add demo permissions to `hospital_admin`; re-seed.
- [ ] Integration tests for the repos and for seeded login-hash verification.

**Exit:** `scripts/seed.py` runs; the stored admin hash verifies with the argon2 helper; repo tests pass.

### Tue 17 — 0013 + 0017

- [ ] JWT access tokens, opaque refresh token with rotation, session create/revoke.
- [ ] Login transaction (session + refresh token atomic) — requires the replica set from Friday.
- [ ] Permission catalogue, `get_current_user`, `require_permission` (DB lookup).
- [ ] Unit tests: token round-trip, expiry, rotation, reuse rejection, permission denial.

**Exit:** a token can be issued for the seeded admin and `get_current_user` resolves tenant + permissions.

### Wed 18 — 0014 + 0016 + S001

- [ ] `POST /auth/login` with IP rate limit and generic errors.
- [ ] `GET /auth/me`.
- [ ] Patient model/service/routes: create, get, list with filters/sort/cursor.
- [ ] Demo steps 1–3 and 8 pass.

**Exit:** login → register patient → search works end-to-end against a fresh seed.

### Thu 19 — S002 + full demo + freeze

- [ ] Visit model/service/routes: create, list by patient.
- [ ] Vitals recording with BMI and range validation.
- [ ] Visit close.
- [ ] Run the full demo script twice from a fresh seed; fix only what breaks it.
- [ ] **Feature freeze at 17:00.** No new tickets, no polish, no refactors.

**Exit:** `tests/test_demo_flow.py` green; OpenAPI export updated.

### Fri 20 — Delivery

- [ ] Re-run the demo script on a clean seed.
- [ ] Demo.
- [ ] Only demo-blocking fixes, with sign-off.

---

## Fallback Triggers

Check at these points; if the condition is true, cut immediately rather than pushing the date.

| Checkpoint | If not green | Fallback |
| :-- | :-- | :-- |
| Wed 12:00 | Login → `/auth/me` not working | Drop S001 read/list polish; demo auth only and show patient data as slides. Extreme case: reset to seeded read-only demo. |
| Thu 12:00 | Patient create + visit create not working end-to-end | Demo steps 1–3 + 8 only; visits/vitals become a walkthrough of code/tests, not live calls. |
| Thu 17:00 | Vitals or close failing | Demo steps 1–5 + 8; show close as a unit test result. Do not start new work. |

The demo path must never depend on a feature completed on Friday.

---

## Definition of Done (Nov 20)

- Fresh `uv run python scripts/seed.py` against a replica-set MongoDB.
- `uv run pytest tests/test_demo_flow.py` passes and covers the eight demo steps.
- `uv run pytest tests/unit tests/integration` passes (outbox included).
- `uv run ruff format --check`, `uv run ruff check`, `uv run mypy app`, `uv run lint-imports` pass.
- OpenAPI export regenerated and committed.
- README documents the replica-set requirement and demo credentials.

---

## Deferred (Post-Nov 20)

| Item | Why deferred | Priority |
| :-- | :-- | :-- |
| 0015 password reset/change | Needs email abstraction; not demoed | High |
| 0018 roles CRUD | Seeded roles are enough for demo | High |
| S003 invitations | Not demoed | Medium |
| MFA enrollment/disable/recovery + per-account lockout | Not demoed; security work needed before any real users | **High if pilot** |
| 0021 audit core | Structured logs only for demo | **High if pilot** |
| 0022 isolation suite | Existing guard + unit tests | Medium |
| Patient summary endpoint, trends, duplicates, merge, clinical entries, documents | Out of demo | Roadmap |

---

## Risks

| Risk | Impact | Response |
| :-- | :-- | :-- |
| 0012/0013 exceed their trimmed estimates | Everything downstream slips | Domain work for S001/S002 starts Tue in gaps; fallback triggers above. |
| MongoDB stays standalone | Login transaction fails at runtime, not just in tests | Day 0 preflight is mandatory; integration tests are the gate. |
| Seed hash/permissions not updated | Demo 401s or 403s on stage | Day 1 exit criteria covers both; demo script logs in as the seeded admin. |
| Multi-tenant login semantics drift | 0014 or 0012 tests contradict | Fixed contract #1; tickets updated Day 0. |
| "Deliver" turns out to mean real patient data | Deferred MFA/audit/isolation are compliance gaps | This plan explicitly targets a demo. A pilot needs a separate security milestone before real PHI. |
| Friday debugging | Demo fails live | Feature freeze Thursday 17:00; Friday changes require sign-off. |
