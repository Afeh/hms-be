# 0001 — CI pipeline for lint, types, imports, tests and the OpenAPI contract

| | |
| :-- | :-- |
| Milestone | M0 Foundations |
| Depends on | — |
| Plan | §12 "API documentation", §13, §14 |

## Context

Nothing runs the checks yet except a developer's shell. Until CI blocks a merge,
the checks do not exist: `mypy --strict` and the import-linter contract are the
two guards that keep the tenancy design intact, and the published OpenAPI file is
the contract the frontend builds against.

## Scope

- [ ] Add `.github/workflows/ci.yml`, triggered on `pull_request` and on pushes to
      `main`. One job, Python 3.12, `astral-sh/setup-uv` with caching enabled, then
      `uv sync --frozen`.
- [ ] Steps, in this order so the cheapest failure wins: `uv run ruff format --check .`,
      `uv run ruff check .`, `uv run mypy`, `uv run lint-imports`,
      `uv run pytest`.
- [ ] Add `scripts/export_openapi.py` that imports `create_app()`, calls
      `.openapi()` and writes `openapi.json` at the repo root, sorted keys and
      trailing newline, so the diff is stable.
- [ ] Commit the generated `openapi.json` and add a CI step that regenerates it and
      runs `git diff --exit-code openapi.json`.
- [ ] Add an `openapi` entry to `README.md` explaining that the file is generated
      and must be regenerated with the script when a route changes.
- [ ] Keep the workflow free of secrets: the current tests must pass without a
      MongoDB or Redis instance.

## Acceptance criteria

- A pull request with a lint error, a type error, a broken import contract, a
  failing test, or a stale `openapi.json` fails CI, each for the matching step.
- A clean branch passes end to end in under about three minutes.
- `uv run python scripts/export_openapi.py` on a clean tree leaves `openapi.json`
  unchanged.
- The workflow uses the same commands the README documents, so there is one
  definition of "the checks".

## Out of scope

- Deployment, image builds and environment promotion.
- Coverage thresholds and mutation testing.
- A job that runs the integration or isolation suites against MongoDB; 0022 adds
  it once those suites exist.
