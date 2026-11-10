# 0020 — Staff management endpoints

| | |
| :-- | :-- |
| Milestone | M1 Tenancy and identity |
| Depends on | 0018, 0019 |
| Plan | §18 `GET /users`, `/users/directory`, `/users/:id` and its actions, `/users/:id/sessions`, §5.1 "Sessions", §26.1, §26.2 |

## Context

Administrators need to see staff, change roles and professional details, deactivate
and remove people, and end their sessions. This is the administrative surface of
M1 and the last place where the role and session models are exercised together. It
also has to keep the "email is freed on removal" invariant from §2 item 11.

## Scope

- [ ] Add `GET /users` (`user:manage`): cursor paging, filters by `status`, `role_id`
      and a name/email search, returning `_id`, `display_name`, `email`, `roles`
      (id, key, name), `status` and `last_login_at`; never password or MFA material.
- [ ] Add `GET /users/directory` for any authenticated staff member: id, name and
      role names of active colleagues in the same tenant only, no email.
- [ ] Add `GET /users/:id` and `PATCH /users/:id` (`If-Match`) accepting `role_ids`,
      `professional`, `display_name`; role assignment validates the roles exist in
      the tenant and bumps `perm_version` of the affected user when permissions
      change.
- [ ] Add `POST /users/:id/deactivate`, `/reactivate`, `/mfa/reset` (clears the MFA
      secret and recovery codes, forcing re-enrolment, and revokes all sessions),
      and `POST /users/:id/remove` (status `removed`, identity disabled when it has
      no other membership, email freed, sessions revoked, `removed_at`/`removed_by`
      stamped).
- [ ] Add `GET /users/:id/sessions` and `DELETE /users/:id/sessions/:session_id` for
      administrators, which audit the revocation and record `revoked_reason="admin"`.
- [ ] Enforce the last-administrator guard: removing or deactivating the last user
      holding `role:manage` returns `409` with the documented code, so a tenant
      cannot lock itself out (add the code to §26 if it is missing).
- [ ] Protect system roles from being dropped by a PATCH (`403 system_role_protected`)
      and return `404` for a user of another tenant.
- [ ] Audit every action with the target user ID and the changed field names.

## Acceptance criteria

- Removing a user frees their email for a new invitation in that tenant and in
  another tenant, and their sessions stop working immediately.
- Removing the last administrator is refused and leaves the tenant usable.
- A role change updates the user's permissions on the next request without a
  re-login.
- `/users/directory` never returns a user from another tenant and never returns an
  email address, asserted by a two-tenant test.
- Every route in this ticket has a permission test for a user who lacks it (`403`)
  and for a user in another tenant (`404`).

## Out of scope

- Signature image upload (`PUT /users/:id/signature`) needs object storage and
  arrives with M3; the field and its schema stay reserved.
- Care-team reassignment on removal (`care_team_reassignment_required`), which
  needs patients (M2).
- Bulk operations, CSV import (M5) and staff export.
