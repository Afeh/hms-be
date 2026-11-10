# 0012 — Identity and access collections, models, schemas and repositories

| | |
| :-- | :-- |
| Milestone | M1 Tenancy and identity |
| Depends on | 0003, 0004, 0010 |
| Plan | §2 items 11 and 12, §6.3, §6.7, §5.1 |

## Context

The identity/membership split is what makes login unambiguous and tenancy honest:
`identities` is global and exempt from the guard, `users` is the per-tenant
membership, and the rest of the auth data (`roles`, `sessions`, `refresh_tokens`,
`invitations`, `access_grants`) is tenant-owned. No endpoint work starts until the
shapes and indexes exist, because the unique indexes are the invariants.

## Scope

- [ ] Add documents in `app/models/`: `identity.py`, `user.py`, `role.py`,
      `session.py`, `refresh_token.py`, `invitation.py`, `access_grant.py`, exactly
      as §6.3 defines them (snake_case, `_id` as `id`).
- [ ] Reuse `TenantDocument` for every guarded document; give `Identity` its own
      base because it never carries `tenant_id`.
- [ ] Add request and response schemas in `app/schemas/` for each resource, with
      the field sets §17 and §18 define (`auth.py`, `user.py`, `role.py`,
      `invitation.py`, `session.py`). Request bodies derive from the strict base
      schema (0006); never expose `password_hash`, `mfa_secret_enc`,
      `recovery_code_hashes` or token hashes.
- [ ] Add repositories in `app/db/`: `identities.py` (exempt, explicit email
      lookups), `users.py`, `roles.py`, `sessions.py`, `refresh_tokens.py`,
      `invitations.py`, `access_grants.py`. Each subclasses `TenantRepository` where
      the collection is guarded, and each is registered so 0022 can enumerate them.
- [ ] Add a migration creating the indexes from §6.7, including at minimum:
      `identities.email_normalized` unique; `users` unique `{tenant_id,
      identity_id}` (sparse while `invited`) and `{tenant_id, status}`;
      `roles` unique `{tenant_id, key}`; `sessions` TTL on
      `absolute_expires_at` plus `{tenant_id, user_id}`; `refresh_tokens` unique
      `token_hash` and `{tenant_id, session_id}`; `invitations` partial unique
      `{tenant_id, email_normalized}` where `status = "pending"`;
      `access_grants` TTL on `expires_at`.
- [ ] Provide explicit failure signals the services can translate: duplicate key
      on email, on role key, on identity membership and on pending invitation, plus
      a helper that resolves an identity by normalized email with a single query.
- [ ] Add model round-trip and index tests (integration, against the compose
      database): each unique index actually rejects the duplicate, the TTL index
      has the expected `expireAfterSeconds`, and no guarded repository can read
      another tenant's documents.

## Acceptance criteria

- Every collection in §6.3 exists in a fresh database after migrations, with the
  indexes named in the migration and asserted by a test.
- A second pending invitation for the same normalised email in one tenant is
  rejected by the database, not only by application code.
- Two `identities` with the same normalised email cannot both exist, while two
  `users` rows in different tenants may point at one identity (test asserts both).
- The deadline indexes expire documents in a short TTL test (set a 1-second
  `expires_at` in the test rather than waiting for the real window).

## Out of scope

- Login, token issuance and session behaviour; 0013 and 0014.
- Role templates and seeding; 0018 owns the templates, 0010 the seeding call.
- Patient-facing collections.
