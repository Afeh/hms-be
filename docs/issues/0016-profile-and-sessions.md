# 0016 — Profile and own-session endpoints

| | |
| :-- | :-- |
| Milestone | M1 Tenancy and identity |
| Depends on | 0014, 0017 |
| Plan | §17 `GET /auth/me`, `PATCH /auth/me`, `GET /auth/sessions`, `DELETE /auth/sessions/:id`, §5.1 "Sessions", §26.1 |

## Context

After login the client needs to know who it is, what it may do, and how to end a
session on another device. These are small endpoints, but they are the first ones
that exercise the authentication dependency end to end and the first that expose
the permission set to the frontend.

## Scope

- [ ] Add `GET /auth/me` returning the identity fields the spec allows (name,
      email, `email_verified_at` as `is_verified`, phone), the membership (`_id`,
      `tenant_id`, `status`, `display_name`, `last_login_at`) and `roles[]` with
      `_id`, `key`, `name`, `mfa_required`, plus `permissions[]` and
      `perm_version`. Never return password or MFA material.
- [ ] Add `PATCH /auth/me` accepting `name`, `phone` and `display_name` only;
      `email`, `roles`, `status` and `tenant_id` are rejected. An `email` field
      returns `422 email_change_not_supported`; unknown fields are a `422` thanks to
      the strict base schema.
- [ ] Add `GET /auth/sessions` listing the caller's live sessions with `device_label`,
      `ip`, `created_at`, `last_seen_at` and `current: true` for the one presenting
      the request; revoked and expired sessions are excluded.
- [ ] Add `DELETE /auth/sessions/:id`: ends the caller's own session; ending the
      current session behaves like logout. Another user's session ID returns `404`,
      not `403`, so existence is not revealed.
- [ ] Emit an audit entry for a profile change and for a session revocation.
- [ ] Add tests: the response shape matches §17 exactly; `PATCH` with `email`
      returns the documented code; the current session is marked; revocation takes
      effect on the next request; a foreign session ID yields `404`.

## Acceptance criteria

- `GET /auth/me` after login returns permissions that match the user's roles and
  the catalogue (no extra, none missing).
- A `perm_version` bump (0018) is reflected on the next `GET /auth/me` without
  re-login.
- `DELETE /auth/sessions/:id` on another tenant's or another user's session returns
  `404`, and the test seeds such a session to prove it.
- The two new error paths use documented codes only; no ad-hoc strings.

## Out of scope

- Administrator session management (`GET/DELETE /users/:id/sessions`); 0020.
- Session labels like "Chrome on Windows" beyond storing the user agent string.
- Self-service email change.
