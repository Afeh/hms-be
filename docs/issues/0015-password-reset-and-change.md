# 0015 — Password reset, change and the email provider interface

| | |
| :-- | :-- |
| Milestone | M1 Tenancy and identity |
| Depends on | 0013, 0014 |
| Plan | §5.1 "Passwords", "Email ownership", §17 `/auth/password/forgot`, `/auth/password/reset`, `/auth/password/change`, §26.1 |

## Context

Password reset is the second proof of email ownership (the invitation is the
first), so it must revoke every session and record `email_verified_at`. It is also
the first feature that has to send an email, which means the provider abstraction
and the outbox wiring for it land here.

## Scope

- [ ] Add `app/services/email.py` with a `EmailSender` protocol and a log-only
      implementation used locally and in tests; select it by a
      `email_provider: Literal["log", "smtp"]` setting, with the SMTP adapter
      implemented but disabled until credentials exist. No route imports a mail
      library directly.
- [ ] Add `app/core/tokens.py` (or extend `security.py`) for single-use tokens:
      random 256-bit value, stored as a SHA-256 hash with an expiry, purpose and the
      identity ID, consumed atomically (`find_one_and_update` setting `used_at`).
      Expiry in settings (`password_reset_ttl`, default 30 minutes).
- [ ] Add `POST /auth/password/forgot`: always `200` with the same message and
      timing class whether or not the email exists; rate limited per IP and per
      email; enqueues the email through the outbox (0005) so a mail failure cannot
      fail the request.
- [ ] Add `POST /auth/password/reset`: validates the token, enforces the tenant
      password policy, stores the new hash, sets `email_verified_at`, clears the
      lockout counters, revokes every session and refresh token for the identity,
      and returns the same `200` message as a successful change.
- [ ] Add `POST /auth/password/change`: requires the current password and the new
      one, enforces the policy, revokes all *other* sessions and keeps the current
      one alive, and returns `401 invalid_credentials` on a wrong current password.
- [ ] Ensure a password change or reset cannot race a refresh: the revocation is
      by `family_id` for all live sessions of the identity, in one update.
- [ ] Add tests: enumeration resistance, single use, expiry, weak password,
      revocation of other sessions, current session surviving a change, and an
      email enqueued exactly once.

## Acceptance criteria

- Requesting a reset for an unknown email returns the same status, envelope
  message and body as for a known one, and no email is sent.
- A reset token works once; a second attempt returns
  `400 invalid_or_expired_token`.
- After a reset, the previous refresh tokens all fail with `401`, and the old
  password no longer logs in.
- A failed send is retried by the outbox dispatcher and never surfaces as a `500`
  on `/auth/password/forgot`.
- The reset email contains a link built from a configured `frontend_url`, never an
  API URL, and the ticket documents the exact link shape for the frontend team.

## Out of scope

- The SMTP credentials and deliverability work (SPF/DKIM), owned by ops in M5.
- Changing an email address, which is not supported in Release 1
  (`email_change_not_supported`).
- Account recovery without access to the email.
