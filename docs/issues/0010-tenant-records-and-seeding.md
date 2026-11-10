# 0010 — Tenant records, tenant profile endpoint and seed script

| | |
| :-- | :-- |
| Milestone | M1 Tenancy and identity |
| Depends on | 0003, 0004 |
| Plan | §6.2, §18 `GET /tenant`, §4.6, §14 M1 |

## Context

Everything scoped starts from a tenant. The guard knows how to stamp `tenant_id`
but there is no `tenants` collection, no way to create a tenant, and no way to
produce two tenants to test isolation against. The platform administration API that
will create tenants properly is M5; M1 needs records and a seed script.

## Scope

- [ ] Add `app/models/tenant.py` with the `Tenant` shape from §6.2: `name`, unique
      `slug`, `status` (`active` | `suspended` | `offboarding` | `deleted`), `plan`,
      `country`, `timezone`, `database { mode: shared | dedicated, secret_ref }`,
      `legal_hold`, timestamps.
- [ ] Add `app/db/tenants.py` for the exempt `tenants` collection: create, find by
      id, find by slug, list, and a status update. No generic repository, because
      this collection is deliberately outside the tenant guard; every method takes
      an explicit tenant ID and one of them is the only lookup that ignores status.
- [ ] Add `app/schemas/tenant.py` and `app/services/tenants.py` for the profile
      response, including the resolved timezone and plan.
- [ ] Add `GET /tenant` returning the current tenant's profile (`_id`, `name`,
      `slug`, `status`, `plan`, `country`, `timezone`, `legal_hold`), permission
      `tenant:configure`, envelope message per §18. A suspended tenant cannot have
      reached the handler (see 0022), so no special case here.
- [ ] Enforce a unique index on `tenants.slug` and on `_id` (implicit) in a migration.
- [ ] Add `scripts/seed.py`, idempotent and safe to run repeatedly: two tenants
      (`acme`, `beta`) with distinct timezones, a config document each (0011), the
      five default roles each (0018 templates), and one Hospital Administrator
      identity with a known local password and MFA disabled, both memberships
      active. Print tenant IDs and login emails at the end.
- [ ] Make the seed script refuse to run when `environment == "production"`.

## Acceptance criteria

- `uv run python scripts/seed.py` twice leaves exactly two tenants, two configs,
  ten roles and two admin identities — no duplicates.
- `GET /tenant` with a token for `acme` returns `acme`; a token for `beta` returns
  `beta`; neither can be made to return the other by passing an ID.
- A duplicate slug is rejected with a `409`, and the error is not a raw
  `DuplicateKeyError`.
- The seed script is the documented way to get a working local login and is
  mentioned in the README.

## Out of scope

- The platform admin API (`/platform/tenants`): M5.
- Tenant deletion, offboarding and dedicated databases; the fields exist, the
  behaviour does not.
- Logo upload, which needs storage (M3).
