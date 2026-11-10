# 0017 — Permission catalogue and authentication dependencies

| | |
| :-- | :-- |
| Milestone | M1 Tenancy and identity |
| Depends on | 0011, 0012, 0013 |
| Plan | §5.2, §5.3, §18 `GET /permissions`, §13 "Security" |

## Context

Every route after this one declares `Depends(require_permission("..."))`. The
catalogue is code-owned and asserted in both directions by a test, so an
undeclared or unused permission fails the build. This ticket also provides the
dependency that turns a bearer token into a request context, which the whole API
depends on.

## Scope

- [ ] Add `app/core/permissions.py`: a `Permission` string enum for the §5.2
      catalogue with human descriptions, `ALL_PERMISSIONS`, and
      `DEFAULT_ROLE_TEMPLATES` for the five seeded roles including each role's
      `mfa_required` flag.
- [ ] Add to `app/api/deps.py`: `get_bearer_token`, `get_current_user` (resolve
      identity, membership, session; check identity, membership and tenant status;
      load permissions from the role cache; populate the request context with
      `tenant_id`, `user_id`, `session_id`, `permissions`, `request_id`),
      `require_mfa`, and `require_feature(flag)` reading the tenant config from
      0011.
- [ ] `require_permission(p)` must validate `p` against the catalogue at import
      time, return a dependency that raises the documented `403` (message names the
      permission, never the roles), and record the permission on the endpoint so the
      route-table test can read it without executing handlers.
- [ ] Add a permission cache in Redis keyed `t:{tenant_id}:perm:{user_id}:{perm_version}`
      with invalidation on a version bump; a cache miss computes from the roles and
      stores with a short TTL.
- [ ] Fail with `401` for a missing, malformed, expired or revoked token, and `403`
      with the documented code when authenticated but not permitted.
- [ ] Add `GET /permissions` returning the catalogue grouped by resource with
      descriptions, available to any authenticated user.
- [ ] Add `tests/test_permission_catalogue.py`: walk `app.routes`, read each
      route's declared permissions, assert every one exists in the catalogue, and
      assert every catalogue entry is used by at least one route or is on an
      explicit `PENDING_ROUTES` allowlist with a comment naming the milestone.
- [ ] Add tests: no token → `401`; valid token lacking the permission → `403`;
      suspended tenant → `403 tenant_suspended`; deactivated membership →
      `403 account_deactivated`.

## Acceptance criteria

- A new route that declares `require_permission("typo:read")` fails `pytest`.
- Adding a permission to the catalogue without using it fails `pytest` unless it is
  on the allowlist.
- The permission set for a seeded Doctor matches §5.2 exactly, asserted in a test.
- `get_current_user` is the only place that reads the `Authorization` header.
- No endpoint can be reached without a tenant context except the public ones
  (`/health/*`, login, reset, invitation accept), and those are enumerated in one
  constant used by 0022.

## Out of scope

- Patient care-team scoping (`PolicyService`), M2.
- Break-glass grants; the collection exists from 0012, the flow is M2.
- Platform realm authentication (separate issuer and audience); M5.
