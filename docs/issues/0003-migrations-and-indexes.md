# 0003 — Versioned migrations and index creation

| | |
| :-- | :-- |
| Milestone | M0 Foundations |
| Depends on | 0002 |
| Plan | §12 "Migrations", §6.1, §6.7 |

## Context

Indexes are part of the tenancy design: `tenant_id` must be the first field of
every compound index, several collections need unique or partial-unique indexes,
and a missing one is a correctness bug (duplicate patient numbers, two drafts,
replayed invitations). The plan forbids building indexes automatically at runtime,
so there has to be a migration runner before the first collection exists.

## Scope

- [ ] Add the migration runner the plan names (`mgm`) as a project dependency and
      create `migrations/` with its configuration, taking the URI, database name
      and options from `app.core.config.Settings` rather than a duplicated env file.
- [ ] Ensure every migration runs inside `run_as_system(reason=...)` from
      `app.db.tenancy`, and logs the applied revision and reason.
- [ ] Add `uv run mgm new <name>` and `uv run mgm migrate` to the README, plus the
      rule that a schema change ships as a migration and a Pydantic model change in
      the same pull request.
- [ ] Add `migrations` to the `mypy` `files` list so migrations are type-checked
      like the rest of the code.
- [ ] Add a first migration, empty apart from a marker comment, so the runner has a
      baseline to apply; the real indexes come from 0010 and 0012.
- [ ] Add a `tests/test_migrations.py` check that applies all migrations to a fresh
      test database and reports zero pending afterwards (skipped when no MongoDB is
      running yet, marked `integration`).

## Acceptance criteria

- `uv run mgm migrate` on an empty database creates the tracking collection and
  applies the baseline; a second run is a no-op.
- A failing migration leaves no partially applied revision recorded and exits
  non-zero.
- Migrations do not import `motor` outside `app/db/` (the import-linter contract
  still passes).
- CI does not need MongoDB for the unit suite; the migration test is opt-in by
  marker and by `MONGODB_URI`.

## Out of scope

- Data backfills and renames; those come with the collection they touch.
- Automatic index creation at application start.
- Rollbacks; forward-only migrations, documented as such.
