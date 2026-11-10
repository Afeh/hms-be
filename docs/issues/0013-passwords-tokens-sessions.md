# 0013 — Passwords, access tokens, refresh rotation and sessions

| | |
| :-- | :-- |
| Milestone | M1 Tenancy and identity |
| Depends on | 0012 |
| Plan | §5.1 "Tokens", "Sessions", "Passwords", §2 item 12, §16.4 |

## Context

This is the security core: argon2id hashing, a ten-minute JWT carrying the tenant
and permission version, an opaque rotating refresh token whose hash is stored, and
a session record that makes revocation immediate. It is also where the plan's most
specific invariant lives: presenting an already-used refresh token revokes the
whole family.

## Scope

- [ ] Add dependencies: `argon2-cffi`, `pyjwt`, `cryptography` (for MFA secret
      encryption in 0014, added here so the settings exist once) and
      `email-validator` for `EmailStr`.
- [ ] Add `app/core/security.py`: argon2id hash and verify (tuned cost, rehash on
      verify when parameters changed), password policy checking against the tenant
      config (`password_min_length`, default 12, and a list of the failing rules for
      `422 weak_password`), JWT encode and decode with `sub`, `uid`, `tid`, `sid`,
      `perm_version`, `mfa`, plus `iss`, `aud`, `iat`, `exp`, and an opaque token
      generator with a SHA-256 hash helper.
- [ ] Add settings: `jwt_secret`, `jwt_issuer`, `jwt_audience`, `access_token_ttl`
      (600 s), `refresh_token_ttl`, `mfa_secret_key`, `mfa_challenge_ttl` (300 s),
      with a validator that refuses to start in staging or production with a default
      or short secret.
- [ ] Add `app/services/sessions.py`: create a session and its first refresh token
      in one transaction, rotate on refresh (mark `used_at`, insert the replacement
      with the same `family_id`), detect reuse and revoke the family with
      `revoked_reason="reuse_detected"`, revoke one session, revoke all sessions for
      an identity, and throttle `last_seen_at` writes (at most once per minute).
- [ ] Enforce idle and absolute timeouts from the tenant config at refresh time and
      on every authenticated request; an expired session yields
      `401 session_expired`.
- [ ] Add a Redis session cache (`t:{sid}:session`) with a short TTL so a revoked
      session stops working within seconds without a database read per request;
      invalidation on revoke must be explicit.
- [ ] Map failures to the documented codes: `401 invalid_credentials`,
      `401 session_expired`, `401 refresh_reuse_detected`,
      `401 invalid_or_expired_token`, `422 weak_password`.
- [ ] Add unit tests for hashing, policy rules, JWT claim validation and every
      rotation path, plus an integration test that revokes a session and sees the
      next request fail.

## Acceptance criteria

- Refresh rotation is atomic: two concurrent refreshes with the same token produce
  one success and one reuse detection, with the family revoked.
- A revoked session fails the next authenticated request, and the test asserts it
  does so without waiting for a cache TTL.
- A token signed with the wrong audience, issuer, or an expired `exp` is rejected
  with `401`, never with a `500`.
- A password shorter than the tenant's minimum returns `422 weak_password` with the
  failed rules, and the message never includes the password itself.
- The JWT contains no email, name or role names — identifiers only.

## Out of scope

- The login, MFA and refresh endpoints themselves; 0014.
- Password reset tokens; 0015.
- Step-up authentication for sensitive actions (`step_up_failed`), which arrives
  with the first sensitive action in M2.
