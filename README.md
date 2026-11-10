# hms-be

Backend for the hospital management system: FastAPI modular monolith plus a Celery
worker over MongoDB. The design, data model and endpoint contract are in
[docs/Backend Plan and API Refrence.md](docs/Backend%20Plan%20and%20API%20Refrence.md).

## Stack

Python 3.12, FastAPI, Pydantic v2, Motor (async PyMongo), Celery + Redis, uv for
dependency management, ruff and `mypy --strict` for checks.

## Setup

```bash
uv sync                      # creates .venv from uv.lock
cp .env.example .env         # optional; every value has a local default
```

MongoDB must run as a single-node replica set because multi-document transactions
are required (patient merge, versioned entries, ID counters). Redis is needed for
the worker, the session cache and rate limits.

## Run

```bash
uv run uvicorn app.main:app --reload          # API on http://127.0.0.1:8000
uv run celery -A app.worker worker -l info    # worker
uv run celery -A app.worker beat -l info      # scheduled queues
```

Docs are at `/docs`, the schema at `/openapi.json`, and the probes at
`/api/v1/health/live` and `/api/v1/health/ready`.

## Checks

```bash
uv run ruff format .
uv run ruff check .
uv run mypy
uv run lint-imports
uv run pytest
```

`lint-imports` enforces that nothing outside `app/db/` imports the MongoDB driver:
the tenancy guard cannot be bypassed by grabbing a collection.

## Layout

```
app/
  main.py                FastAPI app and uvicorn bootstrap
  worker.py              Celery app and the TenantTask base class
  api/
    deps.py              request-scoped resources
    routing.py           the router that applies the envelope
    v1/
      router.py          mounts every route module under the prefix
      routes/            one module per resource
  core/
    config.py            pydantic-settings env schema
    context.py           contextvars tenant/user context
    errors.py            exception handlers that emit the envelope
    logging.py           JSON logs with request and tenant IDs
    middleware.py        request ID and context
  db/
    tenancy.py           tenant guard, document base, system context
    repository.py        TenantRepository CRUD primitives
    client.py            connection registry
    cache.py             Redis client factory
  models/                stored document shapes
  schemas/               request and response bodies
  services/              business logic
  utils/                 small helpers (cursors)
  templates/             email and document templates
docs/                    design and API reference
tests/
```

Every route returns the four-key envelope (`status`, `status_code`, `message`,
`data`) and takes its success message from the route declaration:

```python
@router.get("/live")
@response_message("API is live")
async def live() -> dict[str, LiveHealth]: ...
```

## Next

Authentication, permissions and the tenant-config module are M1 in the plan;
until then the request context carries a request ID but no tenant, and
`TenantCollection` refuses to run.
