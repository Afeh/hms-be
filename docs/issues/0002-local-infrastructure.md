# 0002 — Local infrastructure: MongoDB replica set, Redis and object storage

| | |
| :-- | :-- |
| Milestone | M0 Foundations |
| Depends on | — |
| Plan | §3.1, §4.5, §12 "Migrations", §14 M0 |

## Context

Every integration decision in the plan assumes a MongoDB replica set (transactions
for merge, signing and ID counters), Redis for the broker and caches, and
S3-compatible storage for documents. Today a developer has none of the three, so
`/health/ready` is the only thing that can be tested and no repository test can
run against a real database.

## Scope

- [ ] Add `docker-compose.yml` with:
      - `mongo:7` started with `--replSet rs0 --bind_ip_all`, a named volume and a
        healthcheck that reports healthy only once the set has a primary.
      - A one-shot `mongo-init` service that runs `rs.initiate` and waits for the
        primary, so a fresh `docker compose up` needs no manual step.
      - `redis:7-alpine` with `--appendonly yes` and a healthcheck.
      - `minio` plus a one-shot `minio-init` service that creates the documents
        bucket.
- [ ] Update `.env.example` so the defaults work with the compose file
      (`MONGODB_URI=mongodb://localhost:27017/?replicaSet=rs0`, the Redis URL and
      the storage endpoint, bucket, access key and secret).
- [ ] Add the storage settings to `app/core/config.py` (`storage_endpoint`,
      `storage_bucket`, `storage_access_key`, `storage_secret_key`,
      `storage_region`) with local defaults; nothing may require them to boot.
- [ ] Add a "Local dependencies" section to `README.md` with the commands to start,
      stop and reset the stack, and note that the database volume must be dropped
      to re-initiate the replica set.
- [ ] Document the seeded local ports so the API, worker and tests all point at the
      same instances.

## Acceptance criteria

- On a clean checkout, `docker compose up -d` followed by
  `uv run uvicorn app.main:app` makes `GET /api/v1/health/ready` return `200` with
  both dependencies `ok` (no code change beyond configuration).
- `docker compose exec mongo mongosh --eval "rs.status().myState"` reports `1`.
- The MinIO bucket exists and is reachable with the credentials in `.env.example`.
- Stopping Redis alone makes `/health/ready` return `503 dependency_unavailable`
  and nothing crashes.
- The stack is memory-reasonable for a laptop (no Atlas Search, no replica set
  members beyond one).

## Out of scope

- A production compose/Kubernetes manifest and TLS.
- Authentication on the local MongoDB or Redis; this is a laptop stack.
- Any CI container wiring; that arrives with 0022.
