# 0005 — Transactional outbox and dispatcher

| | |
| :-- | :-- |
| Milestone | M0 Foundations |
| Depends on | 0002, 0003 |
| Plan | §8, §12 "Transactional outbox" |

## Context

Invitation emails, audit chaining and (later) extraction jobs must be triggered by
a committed state change, never by a request that might still fail to commit. The
plan requires an `outbox` collection written in the same transaction as the state
change, drained by the worker, with a sweep for anything left behind. M1 needs it
for invitation emails and session revocations; without it those become best-effort
calls from inside a request.

## Scope

- [ ] Add `app/models/outbox.py` and `app/db/outbox.py` over a `TenantCollection`:
      fields for `type`, `payload`, `created_at`, `available_at`, `attempts`,
      `sent_at`, `last_error`.
- [ ] Add `app/services/outbox.py` with `enqueue(collection_session, type, payload)`
      callable inside an existing transaction, plus the dispatch loop.
- [ ] Add a Celery task `outbox.dispatch` using the existing `TenantTask` base, and
      a beat schedule that runs every 30 seconds; a second sweep re-dispatches rows
      with `sent_at` null and `available_at` older than 30 seconds.
- [ ] Handle each registered type through a small handler registry so new event
      types do not edit the dispatcher; start with `email.send` (a log-only
      implementation until 0015 provides the provider) and `cache.invalidate`.
- [ ] Retry with exponential backoff, cap attempts, and record `last_error`; a row
      past the cap stays unsent and is logged at error level, never silently dropped.
- [ ] Add an idempotency guard so a redelivered row cannot send the same email
      twice (mark processing with a lease before dispatch).
- [ ] Add indexes for the dispatcher query in a migration (unsent and available).
- [ ] Add tests: enqueue inside a rolled-back transaction leaves no row; dispatch
      marks sent; a failing handler retries and then stops; the sweep picks up a
      stuck row.

## Acceptance criteria

- A request that enqueues and then fails commits no outbox row.
- A worker that dies mid-dispatch does not lose the row and does not send twice.
- The dispatcher runs without a request context and restores a tenant context per
  tenant payload.
- `uv run celery -A app.worker worker` and `beat` both start with the new task and
  schedule and pass `celery -A app.worker inspect registered` (documented check).

## Out of scope

- The email provider itself; 0015 owns it.
- Kafka or any external bus. This is the outbox the plan describes, over MongoDB.
- Priority queues and per-tenant fairness.
