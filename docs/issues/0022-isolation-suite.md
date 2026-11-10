# 0022 — Isolation test suite and the M1 merge gate

| | |
| :-- | :-- |
| Milestone | M1 Tenancy and identity (exit gate) |
| Depends on | 0010 to 0021 |
| Plan | §4.6, §13 "Isolation suite", §14 M1 exit criteria |

## Context

Tenant isolation is the one bug class that cannot be forgiven, and the plan makes
this suite a merge gate. It does not test features; it tries to break the guard from
every direction: repositories that bypass it, IDs from another tenant, forged
`tenant_id` in a body, and jobs without a tenant. It is the last M1 ticket because
it needs every route and repository to exist.

## Scope

- [ ] Create `tests/isolation/` with a `conftest.py` that seeds two tenants and
      users through the real services, marks the suite `isolation`, and skips
      cleanly when no MongoDB is reachable.
- [ ] Repository enumeration test: reflect over `app/db/` and assert every
      collection accessor subclasses `TenantRepository` or is on the explicit
      exemption list (`identities`, `platform_users`, `tenants`,
      `platform_audit_logs`, plus documented infrastructure collections such as
      `counters` and `outbox` only if they are guarded). A new accessor that bypasses
      the guard fails without anyone remembering to add a test.
- [ ] Route enumeration test: walk the FastAPI route table, find every route with a
      path parameter ending in `id` or `_id`, call it as tenant A with tenant B's
      object ID, and require `404`. Routes that legitimately differ are listed in one
      constant with a reason.
- [ ] Forged-create test: POST bodies containing `tenant_id` of another tenant are
      ignored or rejected, and the stored document always carries the caller's
      tenant.
- [ ] Job test: a Celery task payload without `tenant_id` raises, and one with it
      restores the context and cannot read another tenant's documents.
- [ ] Cache and key test: permission, config and session cache keys are
      tenant-prefixed; a key built for tenant A cannot return tenant B's value.
- [ ] Add the CI job that runs this suite against the compose services, and make it
      required for merge in the repository settings (documented in the ticket's
      closing comment).
- [ ] Add a short `docs/testing.md` describing how to run the suite locally, what the
      markers mean, and how to add an exemption.

## Acceptance criteria

- Deliberately removing `TenantCollection` from one repository makes the suite fail
  with a message naming the repository.
- A deliberately unscoped query in a test repository fails the suite.
- Calling any ID route with the other tenant's ID returns `404`, never `403` and
  never the document.
- The suite passes against the compose MongoDB and is green for both tenants, and
  CI runs it on every pull request.
- Runtime is under about two minutes so it can stay a merge gate.

## Out of scope

- Presigned URL isolation, which needs the storage service (M3).
- Penetration testing and the authorization matrix across every role, which arrive
  with the full route set in M5.
- Load and concurrency testing beyond the isolation assertions.
