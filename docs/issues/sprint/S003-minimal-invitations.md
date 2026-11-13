# S003 — Minimal invitation accept (no email)

| | |
| :-- | :-- |
| Sprint | 2-week MVP |
| Depends on | 0013, 0017, 0018 |
| Plan | §5.1 "Invitations", §17 `POST /auth/invitations/accept`, §18 `POST /users/invitations` |

## Context

The full invitation flow (0019) involves email delivery, a seven-day
reservation, resend/revoke, and edge-case handling. For the sprint MVP,
we need a way for an admin to create a new user account without building
the full email pipeline. The admin creates an invitation, receives a token
in the response, and shares it out-of-band (e.g. copies the link). The
invitee uses the token to set their password and activate their account.

## Scope

- [ ] Add `app/services/invitations.py` (minimal):
  - `create_invitation(tenant_id, email, role_ids, invited_by)` — creates
    an `Invitation` document with a hashed token and expiry (24 hours),
    creates the `User` document with status `invited`, validates `role_ids`
    exist in the tenant, and returns the **raw token** in the response
    (shown once; the hash is stored).
  - Reject with `409 email_unavailable` if the email already has an active
    identity.
- [ ] Add `POST /users/invitations` — `user:manage`, returns the invitation
      with the raw `token` and a constructed `accept_url` using the
      `frontend_url` setting (or a placeholder).
- [ ] Add `GET /users/invitations` — `user:manage`, list pending/accepted
      invitations with status filter and cursor paging.
- [ ] Add `POST /auth/invitations/accept` (public, rate limited):
  - Validate the token (hashed lookup, unexpired, status `pending`).
  - Accept `password` in the body, enforce the tenant password policy.
  - Create or reuse the `Identity` (by normalised email).
  - Attach `identity_id` to the `User`, set status `active`.
  - Set `email_verified_at` (the token is proof of the invitation).
  - Return the same token pair as login (access + refresh tokens).
- [ ] Add schemas: `InvitationCreate`, `InvitationResponse`,
      `InvitationAccept`.
- [ ] Add tests: create invitation, accept with valid token, reject expired
      token, reject duplicate email, accept returns working access token.

## Acceptance criteria

- `POST /users/invitations` returns a response containing the `token` that
  can be used exactly once to accept.
- `POST /auth/invitations/accept` with the token and a valid password
  creates an active user who can then `POST /auth/login` successfully.
- A second accept attempt returns `409 invitation_already_used`.
- An expired token returns `400 invalid_or_expired_token`.
- The invitation for an email that already has an active membership is
  rejected with `409 email_unavailable`.

## Out of scope (deferred to full 0019)

- Email delivery via the outbox.
- Email reservation with partial unique index.
- Resend and revoke endpoints.
- Seven-day reservation window.
- MFA enrollment during acceptance.
- Cross-tenant email conflict checks.

