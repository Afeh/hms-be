# 0014 — Login, MFA challenge, enrollment and recovery codes

| | |
| :-- | :-- |
| Milestone | M1 Tenancy and identity |
| Depends on | 0011, 0013, 0017 |
| Plan | §5.1 "Login", "MFA", "Platform administrators", §17 `/auth/login`, `/auth/mfa/verify`, `/auth/mfa/enroll`, `/auth/mfa/enroll/confirm`, `/auth/mfa/disable`, `/auth/mfa/recovery-codes`, §26.1 |

## Context

Login is where tenancy, identity, membership and MFA meet: the email resolves the
identity, the identity's single active membership resolves the tenant, and a held
role with `mfa_required` forces a TOTP step before tokens are issued. Every branch
in §5.1 has a documented error code, and the error messages must not reveal whether
an email exists.

## Scope

- [ ] Add `app/services/auth.py` with `login(email, password)`:
      normalise the email, load the identity, verify the password, resolve the
      membership, and check status in this order — identity disabled or no active
      membership → `403 account_deactivated`; tenant `suspended` →
      `403 tenant_suspended`; any held role with `mfa_required` →
      `200` with a challenge token, `challenge_expires_in`, `methods`.
- [ ] Uniform failure: unknown email, wrong password and disabled identity all
      return `401 invalid_credentials` with the same message, and the unknown-email
      path still performs a dummy hash comparison so timing does not leak existence.
- [ ] Lockout: increment `failed_login_count`, lock with exponential backoff after
      the configured threshold, store `locked_until`, return `423 account_locked`
      with `data.locked_until`, and clear the counter on success. Reset the counter
      on a successful password reset (0015).
- [ ] Add the MFA challenge token (short-lived JWT, `scope="mfa"`,
      `mfa_challenge_ttl`) and `POST /auth/mfa/verify`, accepting a TOTP code or a
      recovery code; wrong codes return `401 invalid_code` and count toward the
      rate limit, an expired or reused challenge returns `401 challenge_expired`.
- [ ] Add TOTP enrollment: `POST /auth/mfa/enroll` returns the otpauth URI and a
      secret (shown once), `POST /auth/mfa/enroll/confirm` verifies the first code
      and flips `mfa.enabled`, `enrolled_at`; secrets are encrypted with the
      `mfa_secret_key` (Fernet), never stored in plaintext.
- [ ] Add `POST /auth/mfa/disable` (password plus a valid TOTP or recovery code;
      refused with `403 mfa_required_by_role` when any held role requires MFA) and
      `POST /auth/mfa/recovery-codes` (password plus code, returns ten fresh codes
      once, stores only hashes).
- [ ] Return the session and token pair from the successful login and MFA verify
      responses exactly as §17 defines them, including `user.permissions`,
      `user.roles`, `mfa_required` and `must_change_password` if that field is in
      the spec.
- [ ] Emit an audit entry for every login success, failure, MFA enrollment and
      disable (0021 provides the writer; until then call the service interface).
- [ ] Add tests for every error code in §26.1 that these routes can return,
      including two-tenant disambiguation and a recovery code that works once only.

## Acceptance criteria

- A user in `acme` with the same email as a user in `beta` logs into the right
  tenant; there is no request field that can pick the tenant.
- An MFA-required user cannot obtain tokens without a valid code, and the challenge
  token cannot be used as an access token or reused after success.
- A recovery code is consumed exactly once, and disabling MFA with a recovery code
  also invalidates the remaining codes.
- Ten failed logins for a real and for a non-existent email both look the same from
  the client except for the documented `423` on the real account.
- Login, MFA verify and enroll are all rate limited by 0006.

## Out of scope

- Invitation acceptance and first-password setting; 0019.
- Platform administrator login (`/platform/auth/*`); M5, with its own realm.
- WebAuthn and SMS second factors.
