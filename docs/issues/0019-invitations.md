# 0019 — Invitations, acceptance and the email reservation

| | |
| :-- | :-- |
| Milestone | M1 Tenancy and identity |
| Depends on | 0015, 0017, 0018 |
| Plan | §5.1 "Invitations", "Email ownership", §6.3 `invitations`, §18 `POST`/`GET /users/invitations`, resend and revoke, §17 `POST /auth/invitations/accept`, §26.1 |

## Context

An invitation reserves the email for seven days so two hospitals cannot invite the
same person, and its token is proof of email ownership. Acceptance creates the
global identity, attaches the membership, sets the password and enrols MFA in one
flow. The reservation is enforced by a partial unique index, so expiry and
revocation have to release it deliberately.

## Scope

- [ ] Add `app/services/invitations.py`: create an invitation and its `invited`
      user in one transaction, hashing the token and storing the expiry; reject with
      `409 email_unavailable` when the email already has an active membership
      anywhere, and also when a pending invitation exists in another tenant (the
      message never names the hospital).
- [ ] Enforce the seven-day reservation: a pending invitation expires (status
      `expired`) and releases the email; resend issues a new token and restarts the
      window; revoke ends the reservation immediately.
- [ ] Add `POST /users/invitations` (`user:manage`) with `email`, `role_ids`,
      `professional`; validate roles belong to the tenant (`422 unknown_role`) and
      are not deleted.
- [ ] Add `GET /users/invitations` (paged, filter by status) and
      `POST /users/invitations/:id/resend`, `DELETE /users/invitations/:id`.
- [ ] Send the invitation email through the outbox, with the acceptance link built
      from `frontend_url`; the token appears in the link only, never in logs.
- [ ] Add `POST /auth/invitations/accept` (public, rate limited): validate the token
      (single use, unexpired, invitation still pending), enforce the password policy,
      create or reuse the `Identity`, set `email_verified_at`, attach `identity_id`
      to the user, set status `active`, and return the same token pair as login —
      or an enrollment-required response when a held role has `mfa_required`.
- [ ] Handle the edge cases explicitly: acceptance races (two accepts, one wins;
      the other gets `409 invitation_already_used`), a revoked or expired token
      (`400 invalid_or_expired_token`), and an identity created by a concurrent
      password reset.
- [ ] Audit invite, resend, revoke and accept.

## Acceptance criteria

- Two hospitals cannot both hold a pending invitation for the same email; the
  second attempt returns `409 email_unavailable` with a message that names no
  hospital.
- After expiry or revocation, a new invitation for that email succeeds.
- An accepted invitation logs the person in, and a second acceptance attempt fails
  with the documented code.
- An MFA-required role produces an enrolment step before tokens are usable.
- The invitation token is single use, hashed at rest, and absent from logs and
  audit records.

## Out of scope

- Bulk invitation import (M5 with CSV import).
- Email change for an existing user.
- Invitation templates and branding beyond the tenant display name.
