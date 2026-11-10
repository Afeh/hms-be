# 0004 — Repository primitives: versioned update, soft delete, cursor paging

| | |
| :-- | :-- |
| Milestone | M0 Foundations |
| Depends on | 0002 |
| Plan | §4.3, §6.1, §16.3, §16.4 |

## Context

`app/db/repository.py` today has `get`, `list` and `create` only. Every resource in
M1 needs optimistic concurrency (`If-Match` / `ETag`), soft delete for non-clinical
records, and consistent cursor paging. Building those here once keeps the resource
repositories thin and keeps the tenancy guard in a single code path.

## Scope

- [ ] Add `find_one(query)` and let `list` take a filter, a sort and a projection
      while keeping `_id`-ascending cursor paging as the default.
- [ ] Exclude soft-deleted documents from `get`, `find_one` and `list` by default,
      with an explicit `include_deleted=True` for administrative reads.
- [ ] Add `update_versioned(entity_id, changes, expected_version)` that compares and
      increments `version` in one atomic `find_one_and_update`, returns the updated
      model, and raises a dedicated `VersionConflictError` when nothing matched.
- [ ] Ensure the mutation never accepts `tenant_id`, `_id`, `created_at` or
      `created_by` in `changes`, and stamps `updated_at` / `updated_by` from the
      context.
- [ ] Add `soft_delete(entity_id)` stamping `deleted_at` / `deleted_by`, and make
      it idempotent.
- [ ] Map `VersionConflictError` to `412 version_mismatch` and duplicate-key errors
      to `409` in the route layer through `ApiError`, not by catching `pymongo`
      errors outside `app/db/`.
- [ ] Add helpers for reading and writing `If-Match` / `ETag` (parse the quoted
      version, set `ETag` on single-resource reads, and reject a missing `If-Match`
      on PATCH; `428 precondition_required` is not in the §26 catalogue yet, so add
      it there in the same change or fall back to the documented
      `400 version_mismatch`).
- [ ] Add integration tests against the compose MongoDB: two concurrent updates
      produce one success and one `412`; soft-deleted rows disappear from lists;
      cursor paging returns every row exactly once.

## Acceptance criteria

- No repository, service or route imports `pymongo`, `bson` or `motor`; the
  import-linter contract stays green.
- A stale version never overwrites a newer document, including under concurrent
  updates in the test.
- `create` still refuses a model whose `tenant_id` disagrees with the context, and
  the stored `tenant_id` always comes from the context.
- Cursor paging on a collection of 250 documents visits each document once and
  returns `next_cursor=null` on the last page.

## Out of scope

- Care-team filters and other per-resource queries; those live in the resource
  repositories in M2.
- Field-level history and audit; 0021 handles audit, versioned clinical content is M2.
