# Phase 1 issue tickets

Tickets for **M0 Foundations** and **M1 Tenancy and identity** from §14 of the
[plan](../Backend%20Plan%20and%20API%20Refrence.md). When every ticket here is
closed, two seeded tenants cannot see each other's data, staff log in with MFA,
sessions can be revoked, token reuse kills the family, and the permission checks
are enforced and tested.

M2 onwards (patients, documents, extraction, search, hardening) are not ticketed
yet; they come after M1 publishes its OpenAPI contract.

## Conventions

- One ticket per file, `NNNN-short-title.md`. The number is stable; do not reuse it.
- `Depends on` lists tickets that must be closed first. Everything in M1 depends on
  the M0 infrastructure tickets unless stated otherwise.
- Each ticket lists its plan sections. The plan is authoritative for request and
  response bodies, error codes and schema shape; tickets add the build order and
  the acceptance criteria.
- A ticket is done when its acceptance criteria are all true, `ruff`, `mypy`,
  `lint-imports` and `pytest` pass, and new behaviour is covered by a test.
- Status lives in the index below, not in the ticket file, so a ticket can be
  discussed without editing it.

## Index

| # | Ticket | Milestone | Depends on | Status |
| :-- | :-- | :-- | :-- | :-- |
| 0001 | [CI pipeline](0001-ci-pipeline.md) | M0 | — | Completed |
| 0002 | [Local infrastructure](0002-local-infrastructure.md) | M0 | — | Completed |
| 0003 | [Migrations and indexes](0003-migrations-and-indexes.md) | M0 | 0002 | Completed |
| 0004 | [Repository primitives](0004-repository-primitives.md) | M0 | 0002 | Completed |
| 0005 | [Transactional outbox](0005-transactional-outbox.md) | M0 | 0002, 0003 | Completed |
| 0006 | [HTTP hardening and rate limits](0006-http-hardening.md) | M0 | — | Completed |
| 0010 | [Tenant records and seeding](0010-tenant-records-and-seeding.md) | M1 | 0003, 0004 | Completed |
| 0011 | [Tenant configuration](0011-tenant-configuration.md) | M1 | 0010 | Completed |
| 0012 | [Identity and access models](0012-identity-and-access-models.md) | M1 | 0003, 0004, 0010 | Open |
| 0013 | [Passwords, tokens and sessions](0013-passwords-tokens-sessions.md) | M1 | 0012 | Open |
| 0014 | [Login and MFA](0014-login-and-mfa.md) | M1 | 0011, 0013, 0017 | Open |
| 0015 | [Password reset and change](0015-password-reset-and-change.md) | M1 | 0013, 0014 | Open |
| 0016 | [Profile and session endpoints](0016-profile-and-sessions.md) | M1 | 0014, 0017 | Open |
| 0017 | [Permission catalogue and auth dependencies](0017-permission-catalogue.md) | M1 | 0011, 0012, 0013 | Open |
| 0018 | [Roles endpoints](0018-roles.md) | M1 | 0012, 0017 | Open |
| 0019 | [Invitations and acceptance](0019-invitations.md) | M1 | 0015, 0017, 0018 | Open |
| 0020 | [Staff management](0020-staff-management.md) | M1 | 0018, 0019 | Open |
| 0021 | [Audit core](0021-audit-core.md) | M1 | 0012, 0017 | Open |
| 0022 | [Isolation test suite](0022-isolation-suite.md) | M1 | 0010 to 0021 | Open |

Suggested order: 0001 and 0002 first, then 0003, 0004, 0006, 0005. Inside M1:
0010, 0011, 0012, 0013, 0017, 0018, then 0014, 0015, 0019, 0020, 0016, 0021,
and 0022 last as the gate.

## Already in place

The scaffold covers a few foundations already, so they are not ticketed: project
layout and tooling (`pyproject.toml`, ruff, `mypy --strict`, `pytest`), settings,
JSON logging, the four-key envelope, error handlers, request-ID middleware, the
tenant context, the `TenantCollection` guard with its exemption list and
`run_as_system`, and the health probes.
