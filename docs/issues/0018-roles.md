# 0018 — Roles endpoints and tenant seeding of role templates

| | |
| :-- | :-- |
| Milestone | M1 Tenancy and identity |
| Depends on | 0012, 0017 |
| Plan | §5.2, §6.3 `roles`, §18 `GET`/`POST /roles`, `GET`/`PATCH`/`DELETE /roles/:id`, §26.2 |

## Context

Roles are per-tenant bundles of permissions, and `mfa_required` on a role is the
single source of MFA policy. Changing a role's permissions must invalidate the
cached permission sets of everyone who holds it, which is why this ticket depends on
the catalogue and the cache from 0017.

## Scope

- [ ] Add `app/services/roles.py`: create with a slug `key`, validate every
      permission against the catalogue (`422 unknown_permission`), reject a
      duplicate key (`409 role_key_exists`), and seed the five templates on tenant
      creation from `DEFAULT_ROLE_TEMPLATES`.
- [ ] Add `app/db/roles.py` over a `TenantCollection` with `user_count(role_id)`.
- [ ] Add `GET /roles` (list with `user_count`, ordered by name, paged),
      `POST /roles` (`role:manage`), `GET /roles/:id`, `PATCH /roles/:id`,
      `DELETE /roles/:id`.
- [ ] PATCH accepts `name`, `permissions` and `mfa_required` with `If-Match`;
      changing `key` on a system role returns `403 system_role_protected`; a stale
      version returns `412 version_mismatch`.
- [ ] On a permission or `mfa_required` change, bump `perm_version` for every user
      holding the role in one `update_many` and delete their cache entries; the test
      asserts an affected user's next `GET /auth/me` reflects the new set.
- [ ] DELETE refuses a system role (`403 system_role_protected`) and a role still
      assigned (`409 role_in_use` with `data.user_count`); otherwise marks it
      deleted and removes it from the list.
- [ ] Return the §18 `data.role` shape, including `is_system` and `user_count`, and
      audit create, update and delete with the changed permission names.
- [ ] Add tests: each error code above; MFA-required toggle taking effect at next
      login; permission removal taking effect without re-login; system role
      protection.

## Acceptance criteria

- Deleting a role in use never orphans a user; the test asserts the `409` and then
  that the role and the user's roles are unchanged.
- A permission removed from a role stops a user from reaching a route guarded by it
  immediately after the update, proven by a request in the same test.
- `user_count` is accurate for users in `invited` and `active` status and excludes
  removed users.
- A tenant that edits a seeded role keeps `is_system: true` and `key` unchanged.

## Out of scope

- Assigning roles to users (part of 0019/0020).
- Custom permission creation by tenants; the catalogue is code-owned.
- Role cloning and permission templates beyond the five seeded roles.
