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

Start the project-local replica set on port 27018 (the MongoDB Windows service on
27017 is standalone and cannot run transactions):

```bash
mkdir -p .mongo/data
"/c/Program Files/MongoDB/Server/9.0/bin/mongod.exe" \
  --replSet rs0 --port 27018 --bind_ip 127.0.0.1 \
  --dbpath "$(cygpath -w "$PWD/.mongo/data")" \
  --logpath "$(cygpath -w "$PWD/.mongo/mongod.log")" &
```

The first time only, initiate the set:

```bash
uv run python - <<'PY'
from pymongo import MongoClient

client = MongoClient("mongodb://127.0.0.1:27018/?directConnection=true")
client.admin.command(
    "replSetInitiate",
    {"_id": "rs0", "members": [{"_id": 0, "host": "127.0.0.1:27018"}]},
)
print(client.admin.command("hello")["isWritablePrimary"])
PY
```

The data directory is `.mongo/` (gitignored). Stop it with Ctrl-C in the terminal
that started it, or `taskkill //PID <pid> //F`.

## Run

```bash
uv run uvicorn app.main:app --reload          # API on http://127.0.0.1:8000
uv run celery -A app.worker worker -l info    # worker
uv run celery -A app.worker beat -l info      # scheduled queues
```
- The OpenAPI contract (`openapi.json` at the repo root) is generated from the
  installed routes, so commit it and regenerate it with
  `uv run python scripts/export_openapi.py` whenever a route changes.

## Seeding (local development)

Apply migrations and seed initial tenants for local development and login:

```bash
uv run mgm migrate               # applies database migrations
uv run python scripts/seed.py    # seeds acme and beta tenants with admin identities
```

The seed script is idempotent and safe to run repeatedly. It creates:
- Two tenants: `Acme Hospital` (slug: `acme`, America/New_York) and `Beta Clinic` (slug: `beta`, Europe/London)
- Default tenant configurations and five role templates per tenant
- Hospital Administrator identities: `admin@acme.example` and `admin@beta.example` (password: `Password123!`, MFA disabled)

Docs are at `/docs`, the schema at `/openapi.json`, and the probes at
`/api/v1/health/live` and `/api/v1/health/ready`.

## Migrations

Migrations are versioned, forward-only Python scripts in `migrations/`, applied by
the `mgm` runner against the database in `Settings`: no second env file, no
at-startup index build.

```bash
uv run mgm new add_patient_indexes    # creates migrations/0002_add_patient_indexes.py
uv run mgm migrate                    # applies every pending revision
```

A schema change ships as a migration plus the matching Pydantic model change in
the same pull request; a wrong migration is corrected by a new revision, not a
rollback. Every migration runs inside `run_as_system`, and the applied revision
and reason are logged and stored in the `_migrations` collection.

## Visit images (Cloudflare R2)

Visit images are uploaded directly from the client to a private R2 bucket. The API
only stores metadata and signs short-lived URLs, so image bytes never pass through
the API process.

1. Create a private R2 bucket (for example `hms-documents`).
2. Create an R2 API token with **Object Read & Write** for that bucket.
3. Set the values in `.env`:

```bash
STORAGE_ENDPOINT=https://<ACCOUNT_ID>.r2.cloudflarestorage.com
STORAGE_BUCKET=hms-documents
STORAGE_ACCESS_KEY=<R2_ACCESS_KEY_ID>
STORAGE_SECRET_KEY=<R2_SECRET_ACCESS_KEY>
STORAGE_REGION=auto
```

If your browser uploads directly, add a CORS policy on the bucket that allows
`PUT` and `GET` from the frontend origin and permits the `content-type` header.

The upload flow is:

1. `POST /api/v1/visits/{visit_id}/attachments` with the filename, MIME type and
   byte size. The response contains a presigned `PUT` URL and required headers.
2. `PUT` the image bytes to that URL.
3. `POST /api/v1/visits/{visit_id}/attachments/{attachment_id}/complete`. The API
   checks the object in R2 and marks the attachment `uploaded`.
4. `GET /api/v1/visits/{visit_id}/attachments` lists images with short-lived
   download URLs. Use `GET .../{attachment_id}/url` to refresh an expired one.

Accepted types are JPEG, PNG, WebP and HEIC, up to 10 MiB by default. The tenant
is part of every object key (`t/{tenant_id}/patients/...`) and the bucket must stay
private. Uploading and reading require `document:capture` and `document:read`;
images can only be added while the visit is open.

## Checks

```bash
uv run ruff format .
uv run ruff check .
uv run mypy
uv run lint-imports
uv run pytest
MONGODB_URI='mongodb://127.0.0.1:27018/?replicaSet=rs0' uv run pytest -m integration
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
docs/                    design, API reference and issue tickets
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

Work is broken into tickets in [docs/issues/](docs/issues/README.md), starting
with M0 foundations and M1 tenancy and identity. Authentication, permissions and
the tenant-config module are M1 in the plan;
until then the request context carries a request ID but no tenant, and
`TenantCollection` refuses to run.
