# 0021 — Audit core: append-only writes and the audit query endpoint

| | |
| :-- | :-- |
| Milestone | M1 Tenancy and identity |
| Depends on | 0012, 0017 |
| Plan | §5.5, §6.6 `audit_logs`, §8, §18 `GET /audit-logs` |

## Context

Every security-relevant action in M1 must leave a record: logins, permission
changes, invitations, session revocations, config changes. The plan requires
synchronous writes for writes, deletes and exports, asynchronous writes for
high-volume views, identifiers only (never clinical content), and an insert-only
path. Hash chaining and verification are listed as M5 work in §14, so they are out
of scope here, but the `seq` that the chain will later use is allocated now.

## Scope

- [ ] Add `app/models/audit.py` and `app/db/audit_logs.py` over a `TenantCollection`
      with: `seq`, `action`, `resource_type`, `resource_id`, `user_id`,
      `identity_id`, `ip`, `device_info`, `request_id`, `changes` (field names and
      old/new values for non-clinical fields), `break_glass`, `created_at`.
- [ ] Add an atomic per-tenant `seq` counter (a `counters` collection updated with
      `$inc` inside the same transaction) so no two entries share a sequence number.
- [ ] Add `app/services/audit.py`: `record(...)` writing synchronously inside the
      caller's transaction, and `record_async(...)` pushing view events onto a Redis
      list drained by a Celery task. Both share one payload builder.
- [ ] Add an `audit(action)` route decorator that stores metadata on the endpoint,
      read by a dependency after the handler succeeds, so a route declares its
      action once and the handler does not call the audit service for views.
- [ ] Wire the existing M1 call sites: login success/failure, MFA changes,
      invitation lifecycle, role changes, user changes, session revocation, config
      changes. Nothing in `app/` may update or delete an `audit_logs` document.
- [ ] Add `GET /audit-logs` (`audit:read`) with filters `action`, `resource_type`,
      `resource_id`, `user_id`, `from`, `to`, cursor paging, newest first.
- [ ] Document the database-user setup: the application role holds `insert` and
      `find` on `audit_logs` only (no `update`, no `delete`), with the commands in
      `docs/` or the README and a note that local dev runs as a privileged user.
- [ ] Add tests: a failed transaction writes no audit row; `seq` is gapless and
      unique under concurrent writes; the async view queue drains; a route without
      `audit()` writes no view event; filters and paging behave.

## Acceptance criteria

- Every M1 audit call site produces exactly one row per action, with the request ID
  and tenant ID populated from the context, and no patient-identifying content.
- Concurrent writes produce unique, increasing `seq` per tenant; the test runs
  parallel inserts and asserts it.
- `GET /audit-logs` for tenant A never returns tenant B's rows, including when a
  `resource_id` from B is passed.
- Revoking a session shows up in the audit log with the acting administrator when
  an admin did it, and with the user when they did it themselves.
- The audit writer is callable from the worker with `run_as_system`, and a job can
  be attributed with `user_id` from its payload.

## Out of scope

- Hash chaining, the anchor job and `GET /audit-logs/verify`; §14 puts these in M5.
- Audit export (CSV/JSON) and retention policies; M5.
- Platform audit (`platform_audit_logs`); M5.
