# 0006 — HTTP hardening: headers, body limits, injection guard, rate limits

| | |
| :-- | :-- |
| Milestone | M0 Foundations |
| Depends on | — |
| Plan | §5.1 "Passwords" and "lockout", §12 "Security headers and input hygiene", §16.5 |

## Context

The scaffold has CORS and request IDs but nothing else the plan lists under input
hygiene. Login, password reset and invitation acceptance are the first endpoints
that take untrusted input, so the protections have to exist before them: a request
size cap, security headers, an operator-injection guard on query helpers, a strict
base schema, and a Redis-backed rate limiter for anonymous endpoints.

## Scope

- [ ] Add a strict base schema in `app/schemas/base.py`: `extra="forbid"`,
      `str_strip_whitespace=True`, and use it for request bodies from 0010 onwards.
      Response models stay permissive.
- [ ] Add security headers to every response in the existing middleware (or a
      sibling middleware): `X-Content-Type-Options: nosniff`,
      `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
      `Cache-Control: no-store` on authenticated responses, plus HSTS when
      `environment != "local"`.
- [ ] Add a maximum request body size (settings-driven, default 1 MiB for JSON) that
      returns the envelope with `413 payload_too_large` before the body is buffered
      in full.
- [ ] Add a query sanitizer in `app/db/tenancy.py` that rejects `$`-prefixed keys
      and dotted keys coming from user input, applied by `TenantCollection` and the
      repository helpers, raising an error the route layer maps to
      `422 validation_failed`.
- [ ] Add `app/core/rate_limit.py`: a Redis counter (fixed window is enough) and a
      FastAPI dependency factory `rate_limit(name, limit, window_seconds)` keyed by
      IP for unauthenticated routes and by user ID once 0017 lands. Raise
      `429 rate_limited` with a `Retry-After` header.
- [ ] Apply limits to the anonymous routes as they land: login and MFA verify
      (per IP and per email), password forgot/reset, invitation accept, signup.
      Put the numbers in settings, not in route files.
- [ ] Add tests: an oversized body yields `413`; a filter containing `$where` or
      `a.b` is rejected; the fourth request in a window yields `429` with
      `Retry-After`; headers are present on success and error responses.

## Acceptance criteria

- Every response, including errors and 404s, carries the security headers.
- The rate limiter is a no-op when Redis is unavailable (fail open, logged at
  warning), so a Redis blip cannot take down authentication; the choice is
  documented in the module docstring.
- Rate-limit keys include the tenant ID once a context exists, so one tenant cannot
  exhaust another's budget.
- No route reads `request.client` directly for limiting; the dependency is the only
  place that does.

## Out of scope

- WAF rules, bot detection, CAPTCHA.
- Per-user quotas that are not rate limits (metering is M4).
- Account lockout logic, which belongs to 0014 because it needs the identity record.
