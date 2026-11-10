# HMS Backend Implementation Plan and API Reference

Prepared from Hospital Management System Requirements, Draft v0.1 (November 4, 2026). Scope: backend only (FastAPI, MongoDB, S3-compatible storage, Celery/Redis). Status: working draft, revision v0.3 (November 10, 2026). Sections 1 to 15 are the implementation plan; sections 16 to 28 are the API reference, the error code catalogue, the endpoint index and the revision history.

**How to read this revision.** Decisions taken while reviewing the draft are marked **Decision (proposed)** in section 2. Each carries a default so implementation can start, and each names the alternative that was rejected. Corrections that follow from those decisions are applied throughout the document instead of being listed separately. The four-key response envelope of section 16 is unchanged, and every endpoint keeps it.

**Revision v0.3 changes the stack, not the design.** The plan is now written for FastAPI on Python instead of NestJS on TypeScript: Pydantic replaces `class-validator` and `zod`, Motor replaces Mongoose, Celery replaces BullMQ, and Python runs both processes. Tenancy, authentication, the data model, the endpoints and the delivery sequence are unchanged. The one knock-on effect is that field names are snake\_case in the database as well as on the wire, so the camelCase-to-snake\_case transformation layer disappears (§6.1, §7.1, §16.6, §28).

## 1. Summary and Key Decisions

This plan turns the requirements document into a buildable backend: architecture, tenancy enforcement, security model, data schemas, API endpoints, background jobs, the AI extraction pipeline, testing, and a delivery sequence. It assumes a modular monolith (one codebase, two processes: API and worker), since the product is early and the module boundaries below make a later split cheap.

| Area | Decision | Reason |
| :-- | :-- | :-- |
| Architecture | FastAPI modular monolith on Python 3.12+, type-checked with `mypy --strict` and linted with `ruff`. Two entry points in one codebase: the API (uvicorn) and the worker (Celery). | One deployable to operate, module boundaries enforced by package layout and an import-linter rule, workers scale independently. |
| API style | REST, URI versioning (`/api/v1`), OpenAPI generated from Pydantic models. | Matches the requirement for a documented, versioned REST API. Every response uses the existing envelope: status, status\_code, message, data. |
| Database | MongoDB 7+ as a replica set (multi-document transactions are needed), accessed with Motor (async PyMongo) and Pydantic models. | Needed for patient merge, versioned entries, and atomic ID counters. |
| Tenancy | Shared database, `tenant_id` on every document, enforced by a tenant-scoped repository layer plus request-scoped context. | Matches requirement 5.2 and keeps a path to dedicated databases. |
| Identity | Global `identities` (email, password, MFA) plus per-tenant `users` memberships. | Email is unique across hospitals and freed on removal, without revealing which hospitals a person belongs to. |
| Files | S3-compatible storage (Cloudflare R2), presigned uploads and downloads, keys prefixed by tenant. | Images never public; the API never proxies large files. |
| Jobs | Celery + Redis, separate worker process, same codebase. | Image processing and extraction must not block requests. |
| Authentication | Email + password (argon2id), TOTP MFA, short-lived JWT access token plus rotating refresh token bound to a server-side session, with a token history for reuse detection. | Gives immediate revocation and session listing (6.8). |
| Authorization | Permission-based RBAC (roles are bundles of permissions), plus care-team scope and break-glass, evaluated in one `PolicyService`. | Roles are per-tenant configurable, so code checks permissions, never role names. |
| Validation | Pydantic v2 models for HTTP input, LLM output schemas and tenant config; `pydantic-settings` for environment config. | One library produces the OpenAPI schema, the JSON Schema sent to the LLM and the validated tenant config, so those three cannot drift apart. |
| Search | MongoDB Atlas Search if hosting on Atlas; otherwise normalized and phonetic keys on the patient document. | Spelling-tolerant name search is required. This needs a hosting decision (see section 15). |
| Clinical data | Append-only versions, sign-to-commit, no hard deletes of clinical content. | Requirement 6.4 and medico-legal needs. |
| AI | Provider-agnostic `ExtractionProvider` interface, per-tenant provider and model, off by default. | Requirement 9 and 5.3. |

## 2. Issues in the Requirements to Settle First

These affect schema or API design, so they should be decided before the matching code is written. Each has a recommended default that the rest of this plan assumes.

1. **Email uniqueness conflicts with automatic tenant resolution (5.5).** If emails are unique only per tenant, the same email can exist in two hospitals and login cannot know which one the person means. Recommendation: an email is unique across all active memberships. An administrator removing a user frees the email, which is what 5.5 already describes for moving hospitals. **Resolved in item 11:** the unique index is on `identities.email_normalized`, and an identity holds at most one membership not in `removed`, which is checked when an invitation is created.
2. **Handwriting extraction appears in both Release 1 and Release 2 (section 11).** Sections 2, 6.6 and 7 (Phase 1) imply Release 1. This plan builds extraction with review in Release 1, and keeps in Release 2 only the usage dashboard, batch capture and backfill dashboard. A basic per-tenant page counter ships in Release 1, because unmetered LLM spend is a cost risk.
3. **"No routine access to patient data" for the Platform Administrator.** This needs a hard boundary, not a convention. Plan: a separate `platform_users` collection, separate JWT issuer and audience, and a `/platform` route prefix whose routers cannot resolve a tenant data context. Support access to a tenant would be a later, time-boxed, tenant-approved feature.
4. **Care-team assignment is undefined.** "Clinicians see patients assigned to them" needs a source of truth. Plan: `patients.care_team[]` plus `visits.attending_clinician_id`. A clinician can access a patient if they appear in either, or hold `patient:read:all`. Records Clerks and Administrators get the all-patient permission by default.
5. **Section 6.7's last bullet is garbled.** Assumed meaning: full-text search over confirmed extracted notes arrives in Release 2 or later. Plan stores a searchable text field now so no backfill is needed.
6. **Retention versus "full deletion on exit".** Medical record retention law may require keeping records. Plan: soft delete with a purge job that is gated by tenant config, a legal-hold flag, and an audited admin action. Legal to confirm the periods.
7. **Patient ID format needs a grammar.** Plan defines tokens such as `{PREFIX}`, `{YYYY}`, `{SEQ:6}` with an atomic per-tenant counter (section 6).
8. **Performance targets exist only for uploads (8.2).** Proposed targets: patient search p95 under 500 ms, chart load p95 under 800 ms, upload initiation p95 under 300 ms, extraction job completion median under 60 seconds.
9. **AI consent and disclosure (8.1).** Plan adds a per-tenant `ai_notice_version` and records on each extraction job which notice version applied. Whether patient-level consent is required is a legal question.
10. **Per-tenant encryption keys are only "desirable".** Plan stores `encryption_key_id` on the tenant and defers field-level encryption. Storage-level encryption at rest covers Release 1.
11. **Identity is global; membership is per tenant.** Item 1 above and section 18 require an email that is unique across hospitals and is freed when a user is removed. One `users` collection cannot satisfy both: the tenant guard forces `tenant_id` on every document, and a uniqueness constraint across tenants would reveal which hospitals a person belongs to. **Decision (proposed):** split `identities` (global: email, password, MFA, name, `email_verified_at`, status) from `users` (tenant membership: roles, professional details, status). Login resolves the identity, then its active membership, which also fixes the tenant. Rejected alternative: emails unique per tenant with a hospital picker on the login form, which contradicts login identifying the hospital from the email alone. Consequences are applied in sections 5, 6, 17 and 18.
12. **Refresh-token reuse detection needs a token history.** Section 5.1 stores one `refresh_token_hash` on the session and rotates it on every use, which destroys the only evidence that an old token was replayed. **Decision (proposed):** a `refresh_tokens` collection keyed by token hash, with `used_at`, `replaced_by_hash` and a TTL, so reuse revokes the session family as promised. The session keeps the family identifier only.
13. **The audit hash chain cannot be both synchronous and horizontally scaled.** A per-tenant `prev_hash` chain is only well defined when inserts are serialized, which conflicts with the synchronous write requirement in section 5.5 and with running more than one API instance. **Decision (proposed):** audit writes append with an atomically allocated `seq`; the chain is computed by a single-writer chaining job that stamps `chained_at` and `hash`. `GET /audit-logs/verify` reports `chain_lag_seconds` and treats the unchained tail as unverified rather than broken. Rejected alternatives: a distributed lock per tenant on every write, and per-hour sub-chains. Both add operational complexity for no gain at this scale.
14. **Clinical entry versions need atomic allocation and one draft per entry.** Versions are allocated outside any stated transaction, so two simultaneous amendments to a signed entry both produce `version + 1`, and the unique index on `is_current` does not catch it because drafts are not current. **Decision (proposed):** allocate the version inside the transaction with a unique `{tenant_id, entry_id, version}` index and retry on duplicate key; allow at most one `draft` per `entry_id` with a unique partial index; marking the current version `entered_in_error` leaves `is_current` false so the next signing promotes its replacement.
15. **Patient merge needs a record and a number-resolution rule.** The API returns `merge._id` and `GET /patients/:id/merge-history`, but section 6 defines no merge document, and nothing says whether a retired `patient_number` still resolves. **Decision (proposed):** an append-only `patient_merges` collection, and `GET /patients/:id` plus search resolve a retired number to the survivor with a `merged_into_id` hint. This matters because the backfill use case has clerks typing the number written on an old paper folder.
16. **Care-team filtering needs one indexed field, and cards need denormalized flags.** Section 11 relies on a `clinician_ids` array that section 6.4 does not define, and the `PatientCard` shape requires `has_allergies` and `last_visit_date`, which nothing maintains. **Decision (proposed):** `clinician_ids`, `has_allergies`, `last_visit_date` and `paper_file.pages_captured` are denormalized onto the patient, written in the same transaction or update as their source, with the nightly `patient-denormalize` reconciliation job from section 8 as the safety net.

### 2.1 Corrections applied in this revision

These were internal inconsistencies rather than open questions, so they are settled:

- `Role.mfa_required` is the single source of truth for MFA enforcement; `tenant_config.security.mfa_required_role_keys` is removed. Two writable copies of a security policy is a bug waiting to happen.
- Permissions `visit:update` and `visit:close` are added, because `PATCH /visits/:id` and `POST /visits/:id/close` had no permission. The catalogue is code-owned (`app/common/permissions.py`) and a test fails when a route declares a permission that is not in it.
- `GET /users/directory` is added for care-team and attending-clinician pickers. `GET /users` requires `user:manage`, which Doctors and Records Clerks do not hold, so neither picker could be built.
- Fields referenced by prose or by responses but missing from the schema are added: `patients.legacy_number`, `patients.clinician_ids`, `ClinicalEntry.content_text`, `ExtractionJob.result.raw_output`, `ExtractionJob.warnings`, `ExtractionJob.draft`, `fields[].requires_confirmation`, `documents.pages[].processing_status`, `documents.request_hash`.
- `enhancement_failed` is replaced by `pages[].processing_status: 'enhancement_failed'`; `documents.extraction.status` gains `processing` and `cancelled`; `refresh_token_hash` leaves `sessions` for `refresh_tokens`.
- `is_verified` in responses is backed by `identities.email_verified_at`, which was missing from the schema.
- `data.user.role` (a single string) becomes `data.user.roles[]` everywhere, matching every other endpoint.
- Sections 26 (error code catalogue) and 27 (endpoint index), promised by section 7.1 but absent, are written. Section 28 records this revision.
- Sections 17 to 25 are authoritative for request and response bodies. Section 27 indexes every route and its permission.

## 3. Architecture

### 3.1 Runtime components

- **API process:** FastAPI application served by uvicorn (one ASGI app, stateless, horizontally scalable).
- **Worker process:** same codebase, runs Celery tasks only (image processing, extraction, exports, imports, purge, notifications).
- **MongoDB:** replica set, separate database users for the application and an insert-only user for audit writes.
- **Redis:** Celery broker and result backend, rate limiting, short-lived caches (tenant config, permission sets), SSE fan-out through pub/sub, and a capped replay stream for `Last-Event-ID`.
- **Object storage:** one bucket, keys prefixed by tenant, no public access, server-side encryption.
- **LLM provider:** reached only through the extraction provider interface.
- **Email provider:** invitations, password reset, notifications (SMTP or an API provider).
- **Observability:** structured JSON logs with request ID and tenant ID (never patient data), metrics, error tracking, health checks.

### 3.2 Repository layout

```
app/
  main_api.py            # FastAPI app and uvicorn bootstrap
  main_worker.py         # Celery app bootstrap (celery -A app.main_worker worker)
  common/
    context.py           # contextvars tenant/user context, set by middleware and by tasks
    database/            # tenant collection guard, base repository, client registry, outbox dispatcher
    deps/                # get_current_user, require_mfa, require_permission, require_feature
    middleware/          # audit, response envelope, idempotency, security headers
    errors.py            # exception handlers that emit the envelope
    schemas/ utils/      # shared Pydantic models, helpers
  config/                # pydantic-settings env schema, typed config
  modules/
    auth/ identities/ users/ roles/ tenants/ tenant_config/
    patients/ visits/ vitals/ clinical_entries/ prescriptions/
    documents/ storage/ image_processing/
    extraction/ ai_providers/
    search/ audit/ access_control/ notifications/
    exports/ imports/ metering/ platform/ platform_audit/ health/
    # every module package contains:
    #   router.py      APIRouter, one file (or a routers/ package) per resource
    #   schemas.py     request/response Pydantic models
    #   service.py     business logic
    #   repository.py  collection access, always through TenantCollection
migrations/              # versioned MongoDB migration scripts
tests/
  isolation/             # cross-tenant test suite
  e2e/ fixtures/
```

### 3.3 Module responsibilities

| Module | Responsibility |
| :-- | :-- |
| auth | Login, MFA, tokens, sessions, password reset, invitation acceptance |
| identities | Global person records: email, password hash, MFA enrollment. Never carries `tenant_id`. |
| users, roles | Tenant membership, staff lifecycle, role definitions, permission assignment |
| tenants, tenant_config | Tenant lifecycle, typed configuration, feature flags, onboarding |
| patients | Registration, ID generation, duplicate detection, merge, care team |
| visits, vitals | Encounters, vital signs, BMI and range checks, trends |
| clinical_entries, prescriptions | Versioned clinical notes with draft/sign workflow, prescriptions, allergies and conditions |
| documents, storage, image_processing | Capture sessions, uploads, enhancement jobs, signed URLs |
| extraction, ai_providers | Handwriting extraction jobs, provider adapters, review and approval |
| search | Patient search and document filters |
| audit, access_control | Audit writing and querying, break-glass grants, policy evaluation |
| exports, imports | Tenant data export, CSV patient import |
| metering | Page and storage counters per tenant |
| notifications | Email delivery, in-app notifications, and SSE event fan-out |
| platform, platform_audit | Platform admin API (separate auth realm) and the platform audit trail |

Each module is a Python package with the same four files (`router.py`, `schemas.py`, `service.py`, `repository.py`), so a module is a plain directory that nothing has to register. Cross-module calls go through the service, never through another module's repository.

### 3.4 Request pipeline

Starlette middleware (security headers, CORS, request ID) and rate limiting run first, then the authentication dependency (verifies the JWT and session and populates the context), the MFA-satisfied dependency, the permission dependency, Pydantic request validation, the router function, the service, and the tenant-scoped repository on the way in. The response then passes back out through the same layer: the envelope wrapper adds `status`, `status_code` and `message`, and the audit dependency records the event from route metadata. Both live once in `app/common/`, not in each route. Errors go through exception handlers that return the same four-key envelope as successful calls (with status set to error) and never leak internals or patient data.

## 4. Multi-Tenancy Implementation

### 4.1 Request context

Use a `contextvars.ContextVar` to hold a context object for each request or job. A Starlette middleware sets it for HTTP; the Celery task base class sets it before a task runs. The context holds: `tenant_id`, `user_id`, `session_id`, `permissions`, `ip`, `device_info`, `request_id`. The tenant ID comes only from the verified access token (or, for unauthenticated flows, from a server-side lookup such as the invitation token or the user record found at login). It is never read from a header, path or body.

### 4.2 Tenant-scoped collection guard

PyMongo has no schema middleware, so tenancy is enforced at the one place that can reach a collection. `TenantCollection` is a thin async wrapper around a Motor collection, and it is the only way database code may obtain one. It does the following:

- Models a required, indexed `tenant_id` field (a `TenantDocument` Pydantic base class; the index itself is created by migration).
- On `find`, `find_one`, `find_one_and_update`, `count_documents`, `update_one`, `update_many`, `delete_one`, `delete_many`: injects `tenant_id` from context into the filter. If the caller supplied a different `tenant_id`, it raises.
- On `aggregate`: prepends a `$match` on `tenant_id`. Pipelines whose first stage must be special (`$search`, `$geoNear`) get the filter inside that stage, and a unit test guards this.
- On `insert_one` and `insert_many`: stamps `tenant_id` from context and refuses a document that already carries a different one.
- Raises if there is no context, unless the call runs inside `run_as_system(reason, fn)`, which is logged and reserved for migrations and platform tasks.
- Collections exempt from the guard are limited to `identities`, `platform_users`, `tenants` and `platform_audit_logs`. The exemption list lives in `app/common/database/` and the isolation suite asserts it (§4.6).
- An `import-linter` contract forbids importing `pymongo` or `motor` outside `app/common/database/`, so the guard cannot be bypassed by grabbing a collection directly.

### 4.3 Base repository

`TenantRepository` is a generic base class over one `TenantCollection` (Python `Generic[Model]`) and exposes only safe operations: `find_by_id`, `find_one`, `list` (cursor pagination), `create`, `update_versioned`, `soft_delete`. Domain repositories subclass it. Routers and services never touch a collection directly.

### 4.4 Connection registry

A `TenantConnectionRegistry` maps `tenant_id` to a Motor client and database through `tenants.database = { mode: 'shared' | 'dedicated', secret_ref }`. All repositories obtain their database from it. In Release 1 every tenant resolves to the shared connection. Moving a tenant to a dedicated database later is then a data migration plus a config change, with no application changes (requirement 5.2).

### 4.5 Storage, search, cache and queues

- **Object keys:** `t/{tenant_id}/patients/{patient_id}/documents/{document_id}/{page_no}/{original|enhanced|thumb}.{ext}`. The storage service refuses any key that does not start with the context tenant prefix.
- **Search:** every Atlas Search query includes `tenant_id` as a mandatory `filter` clause, built by one helper.
- **Redis:** keys are prefixed `t:{tenant_id}:`, including the SSE replay stream (`t:{tenant_id}:events`).
- **Jobs:** every payload carries `tenant_id`; the worker re-establishes the context before running, and rejects payloads without it.

### 4.6 Isolation test suite

The suite seeds two tenants and then:

- Enumerates every registered repository and fails the build if it is not built on `TenantCollection` and is not on an explicit exemption list (`identities`, `platform_users`, `tenants`, `platform_audit_logs`).
- Enumerates every route that takes an ID from the FastAPI route table, calls it with tenant B's ID while authenticated as tenant A, and expects 404 (not 403, so existence is not revealed).
- Attempts cross-tenant creates with a forged `tenant_id` in the body and expects it to be ignored or rejected.
- Verifies presigned URLs cannot be issued for another tenant's keys, and that jobs without `tenant_id` are refused.

### 4.7 Tenant configuration

One `tenant_configs` document per tenant, validated by a Pydantic model with defaults, cached in Redis for 60 seconds and invalidated on write. It covers branding, enabled modules and feature flags, document types, visit types, patient ID format, AI settings (enabled, provider, model, notice version), and the security policy (idle timeout, absolute timeout, password rules, break-glass window). MFA enforcement is deliberately not tenant config: it is the `mfa_required` flag on each role (§6.3). Because a stale security policy is unacceptable, a write also publishes an invalidation event, and security fields have a 5-second cache ceiling regardless of the 60-second TTL. Every config change is audited.

## 5. Authentication, Authorization and Audit

### 5.1 Authentication flows

- **Login:** email and password. The server finds the global `identities` record by normalized email, verifies the password, then resolves that identity's active membership in a tenant (§2 item 11). An identity has at most one active membership, which is what makes the tenant unambiguous; a second concurrent membership is rejected at invitation time with `email_unavailable`. If MFA is required, login returns a short-lived MFA challenge token and a TOTP code completes it.
- **Tokens:** access token is a JWT valid for 10 minutes, with `sub` (identity), `uid` (membership), `tid`, `sid`, `perm_version`, `mfa`. Refresh token is an opaque random 256-bit value. Its hash is stored in `refresh_tokens` with the session and family IDs, and it rotates on every use by marking the presented row `used_at` and inserting its replacement. Presenting a row that already has `used_at` revokes the whole family and returns `401 refresh_reuse_detected`.
- **Sessions:** each login creates a session for the membership, with device label, IP, last-seen time, expiry, and the identity ID so a password change can revoke every session across devices. Idle and absolute timeouts come from tenant config. Users can list and end their sessions; administrators can end any session in their tenant. The authentication dependency checks the session on each request using a short Redis cache, so revocation takes effect within seconds.
- **Passwords:** argon2id, minimum length 12, optional breached-password check, lockout with exponential backoff after repeated failures, rate limits on login and reset endpoints, uniform error messages to avoid account enumeration.
- **MFA:** TOTP with encrypted secret storage and single-use hashed recovery codes. Enforcement is the `mfa_required` flag on the user's roles, which is the single source of truth (§2.1); a user must satisfy MFA if any assigned role requires it. Seeded defaults: Hospital Administrator, Medical Director, Doctor and Nurse are `true`; Records Clerk is `false`. Tenants can change the flag per role, and the change is audited.
- **Invitations:** signed, single-use, expiring token. An invitation reserves the email for seven days, so two hospitals cannot invite the same person concurrently; expiry or revocation releases it. Acceptance creates the `identities` record, sets the password, and enrolls MFA in one flow (§18).
- **Email ownership:** the invitation token is proof of ownership, so acceptance sets `identities.email_verified_at`; that is what the API reports as `is_verified`. Password reset proves ownership again.
- **Email change:** not self-service in Release 1. It is `POST /users/:id/remove` followed by a new invitation, which keeps the global uniqueness invariant intact. `PATCH /auth/me` rejects `email`.
- **Platform administrators:** separate collection, issuer and audience, mandatory MFA, `/platform` routes only.

### 5.2 Permission model

Permissions are strings in `resource:action` form. Roles store a set of permissions. A router declares its permission as a dependency, `Depends(require_permission('clinical:write'))`, which registers the permission in the route table used by the tests and by `GET /permissions`. Role changes bump a per-user `perm_version`, which invalidates cached permission sets.

The catalogue is code-owned in `app/common/permissions.py`, seeded at tenant creation, exposed by `GET /permissions`, and checked by a test that walks the FastAPI route table and the dependency closure of each route: a route that declares a permission missing from the catalogue fails the build, as does a catalogue entry no route or policy uses. When a role changes, the tenant module bumps `perm_version` for every user holding it in one update, which invalidates their cached permission sets.

| Permission | Meaning |
| :-- | :-- |
| `patient:create`, `patient:read`, `patient:update`, `patient:merge` | Patient demographics |
| `patient:read:all` | Bypass care-team restriction |
| `visit:create`, `visit:read`, `visit:update`, `visit:close`, `vitals:write` | Encounters and vitals |
| `clinical:read`, `clinical:write`, `clinical:sign` | Clinical entries and prescriptions |
| `document:capture`, `document:read`, `document:tag`, `document:delete` | Document handling |
| `extraction:request`, `extraction:review` | AI extraction |
| `audit:read`, `audit:export` | Audit log |
| `user:manage`, `role:manage`, `tenant:configure`, `tenant:export` | Administration |
| `access:breakglass` | May invoke emergency access |

Default role templates seeded at tenant creation:

| Role | Permissions (summary) |
| :-- | :-- |
| Hospital Administrator | `user:*`, `role:*`, `tenant:*`, `audit:*`, `patient:read:all`; no `clinical:write` or `clinical:sign` |
| Medical Director | Read-only on patients, visits, clinical entries, documents; `extraction:review` is not granted by default; `patient:read:all` |
| Doctor | `patient:read/update`, `visit:create/read/update/close`, `vitals:write`, `clinical:read/write/sign`, `document:read/capture/tag`, `extraction:request/review`, `access:breakglass` |
| Nurse | `patient:read`, `visit:create/read`, `vitals:write`, `document:capture/read/tag`, `clinical:read` |
| Records Clerk | `patient:create/read/update/merge`, `patient:read:all`, `document:capture/read/tag`, `extraction:request/review` |

The Medical Director's "read only" rule is enforced by the absence of write permissions, so a tenant that wants a different arrangement changes configuration, not code.

Two access rules do not need a permission of their own. `GET /users/directory` (name, role and id of colleagues in the same tenant) is open to any authenticated staff member, because care-team and attending-clinician pickers would otherwise be unusable for Doctors and Clerks. `PATCH /patients/:id/paper-file` and the care-team routes use `patient:update`, since they change the patient record.

### 5.3 Policy evaluation

`PolicyService.can_access_patient(ctx, patient_id)` is the single place that decides patient access, in this order:

1. The user holds the required permission (the route's dependency).
2. The tenant has the relevant module or feature enabled.
3. Care-team scope: the user holds `patient:read:all`, or is in `patient.care_team`, or is the attending clinician on a visit for the patient, or holds an active break-glass grant.
4. Resource-level rules: for example only the author can sign a draft clinical entry, and only drafts can be edited in place.

Repositories expose `find_by_id_for_user` and list methods that apply the care-team filter at query level, so patient lists and search results never contain patients the user may not see (requirement 6.7).

### 5.4 Break-glass

`POST /patients/:id/break-glass` with a mandatory reason (minimum length enforced) creates an `access_grants` document that expires after 60 minutes (TTL index). The grant is audited with a `break_glass: true` flag, and the tenant administrators are notified. The grant widens only care-team scope for that one patient, never permissions.

### 5.5 Audit

- **What:** every view, create, update, export, delete, login event, permission change, break-glass use and config change, with user, tenant, time, IP, device, request ID, and the resource type and ID. Audit entries store identifiers and field names, not clinical content.
- **How:** an audit dependency records reads and writes from route metadata (an `audit('patient.view')` decorator on the router function, read by the middleware). Writes, exports, deletes and break-glass events are written synchronously before the response returns. High-volume view events are written asynchronously with a retry queue so a slow audit write does not block clinical work, and the plan treats a lost view event as an incident to be monitored.
- **Integrity:** an insert-only database user for the `audit_logs` collection; the application role has no update or delete grant. Writes allocate `seq` atomically and are never rewritten. A single-writer chaining job appends `prev_hash` and `hash` per tenant in `seq` order, so concurrent API instances cannot fork the chain (§2 item 13). A daily `audit-hash-anchor` job stores the latest chain hash outside the database, and `GET /audit-logs/verify` reports the verified range plus `chain_lag_seconds` for the unchained tail.
- **Platform audit:** platform staff actions (tenant create, suspend, resume, offboard, plan, module and legal-hold changes, and platform logins) are written to `platform_audit_logs`, which is outside the tenant guard and readable only through the platform realm. Tenant audit logs never contain platform actions, and platform audit logs never contain patient data.
- **Access:** administrators can filter and export the log; exporting is itself audited.

## 6. Data Model

### 6.1 Conventions

- Every tenant-owned collection carries `tenant_id`, `created_at`, `updated_at`, `created_by`, `updated_by`, `schema_version`. `tenant_id` is the first field of every compound index.
- IDs are MongoDB ObjectIds internally and strings in the API. Mutable documents carry a `version` integer used for optimistic concurrency (`If-Match` header).
- Dates of birth are stored as date-only strings (`YYYY-MM-DD`) to avoid timezone shifts. Timestamps are UTC.
- Non-clinical records use soft delete (`deleted_at`, `deleted_by`). Clinical content is never deleted: a wrong entry is marked `entered_in_error` and stays visible in history.
- Naming follows FHIR concepts where practical: Patient, Encounter (visits), Observation (vitals), Condition, AllergyIntolerance, MedicationRequest (prescriptions), DocumentReference (documents). Each module keeps a mapper file so a FHIR export can be added without schema changes.
- Derived fields are denormalized and reconciled. `patients.clinician_ids`, `patients.has_allergies`, `patients.last_visit_date` and `patients.paper_file.pages_captured` are written by the service that changes their source, in the same transaction or update, and the nightly `patient-denormalize` job recomputes them to repair drift (§8). `primary_phone`, `display_name` and `age_years` are computed per request and never stored.
- Collection names below are suggestions; the blocks are schematic and show shape, not exact Motor or Pydantic syntax.

Field names in this section are the internal storage names, and they are snake\_case, exactly as the API exposes them (section 16.6). One Pydantic model therefore describes the stored document, the request body and the published schema, and no key transformation exists between storage, service and wire. Documents keep `_id` for identifiers.

### 6.2 Platform and Tenant Collections

```ts
// tenants  (exempt from the tenant guard; accessed by platform and bootstrap code only)
Tenant {
  _id, name, slug (unique), status: 'active'|'suspended'|'offboarding'|'deleted',
  plan: 'trial'|'standard'|'enterprise', country, timezone,
  database: { mode: 'shared'|'dedicated', secret_ref?: string },
  encryption_key_id?: string,           // reserved for per-tenant keys
  legal_hold: boolean,
  created_at, suspended_at?, deletion_scheduled_for?
}

// tenant_configs  (one per tenant)
TenantConfig {
  tenant_id (unique),
  branding: { display_name, logo_key?, primary_color? },
  modules: { ai_extraction: boolean, icd_coding: boolean, insurance: boolean },
  patient_id_format: { pattern: 'HMS-{YYYY}-{SEQ:6}', reset_sequence: 'never'|'yearly' },   // grammar in §6.4
  document_types: [{ key, label, default_handwritten: boolean, active }],
  visit_types:    [{ key, label, active }],
  ai: { provider, model, notice_version, max_pages_per_document, monthly_page_cap? },
  security: { idle_timeout_min, absolute_timeout_hours, password_min_length, break_glass_minutes },
  // MFA enforcement is Role.mfa_required, not tenant config (§2.1)
  retention: { clinical_years, audit_years, document_years },
  version
}

// platform_users  (separate realm, never has tenant_id)
PlatformUser { _id, email (unique), password_hash, mfa_secret_enc, status, roles: ['platform_admin'|'platform_support'] }
```

### 6.3 Identity and Access Collections

```ts
// identities  (global person record; exempt from the tenant guard)
Identity {
  _id, email, email_normalized,        // unique across the platform
  name: { given, family }, phone?,
  password_hash?, password_changed_at?, failed_login_count, locked_until?,
  email_verified_at?,                   // set by invitation acceptance or a completed password reset
  mfa: { enabled, secret_enc?, recovery_code_hashes: string[], enrolled_at? },
  status: 'active'|'disabled',
  last_login_at?, created_at, updated_at
}

// users  (tenant membership; created at invitation time, identity attached on acceptance)
User {
  tenant_id, identity_id?,              // null while status is 'invited'
  invited_email_normalized?,            // present while status is 'invited'; what the invite reserves
  role_ids: ObjectId[], perm_version: number,
  status: 'invited'|'active'|'deactivated'|'removed',
  professional?: { license_no, cadre, signature_image_key? },
  display_name?,                       // optional per-tenant override for the staff list
  last_login_at?, removed_at?, removed_by?, version
}
// unique {tenant_id, identity_id}; an identity has at most one membership not in 'removed' (checked at invitation time)

// roles  (per tenant; seeded from templates; mfa_required is the only MFA policy source)
Role { tenant_id, key, name, permissions: string[], is_system: boolean, mfa_required: boolean }

// sessions  (one per login; token hashes live in refresh_tokens)
Session {
  tenant_id, user_id, identity_id, family_id, device_label, user_agent, ip,
  created_at, last_seen_at, idle_expires_at, absolute_expires_at,
  revoked_at?, revoked_reason?: 'logout'|'admin'|'reuse_detected'|'password_change'
}  // TTL on absolute_expires_at + retention buffer

// refresh_tokens  (rotation history, so replay is detectable)
RefreshToken {
  tenant_id, session_id, family_id, token_hash,   // unique
  created_at, used_at?, replaced_by_hash?, expires_at
}  // TTL past the family's absolute expiry

// invitations  (reserves the email while pending)
Invitation {
  tenant_id, user_id, email_normalized, token_hash,
  status: 'pending'|'accepted'|'revoked'|'expired',
  invited_by, expires_at, accepted_at?, revoked_at?
}  // unique partial on {tenant_id, email_normalized} where status is 'pending'

// access_grants  (break-glass)
AccessGrant { tenant_id, user_id, patient_id, reason, granted_at, expires_at /* TTL */, notified_admin_ids: ObjectId[] }
```

### 6.4 Clinical Collections

```ts
// patients
Patient {
  tenant_id, patient_number,            // formatted per tenant config; unique per tenant
  status: 'active'|'inactive'|'deceased'|'merged', merged_into_id?,
  name: { given: string[], family, other? },
  name_search: { normalized, tokens: string[], phonetic: string[] },  // derived, for duplicate and fuzzy search
  date_of_birth: 'YYYY-MM-DD', dob_approximate: boolean,
  sex: 'male'|'female'|'other'|'unknown',
  phones: [{ value, normalized /* E.164 */, is_primary }],
  address: { line1, line2?, city, state, country, lga? },
  next_of_kin: [{ name, relationship, phone, address? }],
  registration_date,
  payer?: { type: 'self'|'insurance'|'corporate'|'hmo', provider?, member_id?, group_no? },
  care_team: [{ user_id, role, assigned_at, assigned_by }],
  clinician_ids: ObjectId[],           // derived: care_team.user_id ∪ attending clinicians; the care-team filter (§11)
  legacy_number?,                      // file number used before migration; searchable, not unique
  has_allergies: boolean,              // derived: any active allergy condition
  last_visit_date?: 'YYYY-MM-DD',       // derived from visits
  paper_file: { status: 'none'|'partial'|'complete'|'archived', physical_location?, pages_captured, last_captured_at? },
  duplicate_override?: { by, reason, matched_patient_ids: ObjectId[] },
  version
}

// patient_merges  (append-only; one document per merge)
PatientMerge {
  tenant_id, survivor_id, merged_id,
  reason,
  field_choices,                       // { phones: 'union'|'survivor'|'merged', address: ..., next_of_kin: ..., payer: ... }
  moved: { visits, vitals, clinical_entries, prescriptions, documents, conditions },
  retired_patient_number,               // kept so old paper files still resolve to the survivor
  merged_by, merged_at
}
// The merged patient document is retained with status 'merged' and merged_into_id, so both numbers stay resolvable.

// counters  (atomic sequences)
Counter { tenant_id, key: 'patient:2026', value }   // find_one_and_update with $inc, upsert

// patient_conditions  (allergies and chronic conditions; the prominent summary)
PatientCondition {
  tenant_id, patient_id, kind: 'allergy'|'chronic_condition'|'other_alert',
  substance_or_condition, code?: { system, code, display },
  severity?: 'mild'|'moderate'|'severe', reaction?, onset_date?,
  status: 'active'|'resolved'|'entered_in_error',
  source: 'typed'|'ai_extracted'|'ai_extracted_edited',
  source_document_id?, recorded_by, recorded_at, version
}

// visits
Visit {
  tenant_id, patient_id, type (from tenant config), visit_date, attending_clinician_id,
  status: 'open'|'closed'|'cancelled', reason_for_visit?,
  closed_at?, closed_by?, version
}

// vital_signs
VitalSigns {
  tenant_id, patient_id, visit_id, measured_at, recorded_by,
  temperature_c?, pulse_bpm?, systolic_mmhg?, diastolic_mmhg?,
  weight_kg?, height_cm?, bmi?,         // bmi computed server-side
  warnings: [{ field, message }],     // out-of-range acknowledgements
  status: 'active'|'entered_in_error', entered_in_error_reason?, version
}

// clinical_entries  (one document per version)
ClinicalEntry {
  tenant_id, entry_id /* stable across versions */, version, is_current,
  patient_id, visit_id, kind: 'encounter_note'|'progress_note'|'referral_note',
  status: 'draft'|'signed'|'entered_in_error',
  content: {
    presenting_complaint, history, examination, observations,
    diagnoses: [{ text, code?: { system: 'ICD-10', code, display }, type: 'primary'|'secondary' }],
    plan, follow_up
  },
  provenance: { [field_path]: 'typed'|'ai_extracted'|'ai_extracted_edited' },
  content_text,                              // plain text of content, built on signing (§11)
  source_document_id?, extraction_job_id?,      // link to the source image
  author, authored_at, signed_by?, signed_at?, supersedes_version?, change_reason?
}

// prescriptions  (same versioning rules as clinical entries)
Prescription {
  tenant_id, prescription_id /* stable across versions */, version, is_current,
  patient_id, visit_id, clinical_entry_id?,
  items: [{ drug_name, drug_code?, dose, dose_unit, route, frequency, duration, instructions?,
            source: 'typed'|'ai_extracted'|'ai_extracted_edited', confirmed_by?, confirmed_at? }],
  status: 'draft'|'signed'|'entered_in_error', prescriber_id, signed_at?,
  supersedes_version?, change_reason?, entered_in_error_reason?
}
```

Clinical entries and prescriptions follow one rule: a draft is editable in place because it is not yet part of the record. Once signed, the document is immutable. An edit creates a new draft with `version + 1` and `supersedes_version`; signing it flips the previous version's `is_current` to false inside a transaction. Prior versions stay readable through the history endpoint.

Two invariants make that safe under concurrency (§2 item 14). There is at most one `draft` per `entry_id` (and per `prescription_id`) enforced by a unique partial index, so a second editor receives the existing draft instead of forking. The next version number is allocated inside the signing transaction using a unique `{tenant_id, entry_id, version}` index, with one retry on duplicate key; allocating it beforehand is what allowed two simultaneous amendments to both produce `version + 1`. Marking the current version `entered_in_error` sets `is_current` to false and leaves no current version until a corrected one is signed, which is visible on the chart rather than silently papered over.

**Patient number grammar.** `patient_id_format.pattern` accepts literals, `{TENANT}` (the tenant slug, upper-cased), `{PREFIX}` (a configured prefix literal, kept for readability), `{YYYY}` and `{YY}` (the current year in the tenant timezone), and `{SEQ:n}` (a zero-padded counter, width 1 to 10). The counter key is `patient:{year}` when `reset_sequence` is `yearly` and `patient` when it is `never`, so an annual reset never collides with earlier numbers. The pattern is validated by a regex derived from this grammar at config write time (`422 invalid_patient_id_pattern`), the formatted number is unique per tenant, and numbers are never reused, including after a merge.

### 6.5 Documents and Extraction

```ts
// documents
Document {
  tenant_id, client_capture_id,          // unique per tenant: idempotency for retried captures
  request_hash,                        // distinguishes an idempotent replay from a conflicting payload
  patient_id, visit_id?, visit_date, type, is_handwritten: boolean,
  status: 'initiated'|'uploading'|'uploaded'|'processing'|'ready'|'failed',
  page_count,                          // derived from pages.length
  pages: [{
    page_no,
    processing_status: 'pending'|'uploaded'|'processing'|'ready'|'enhancement_failed',
    original:  { key, mime, bytes, sha256, width, height },   // never modified
    enhanced?: { key, mime, bytes, width, height },
    thumb?:    { key, mime, bytes },
    quality:   { blur_score, mean_brightness, flags: ('blurry'|'dark'|'low_resolution')[] },
    enhancement_error?: { code, message },
    upload:    { upload_id?, parts?: number, completed_at? }
  }],
  captured_by, captured_at, device: { user_agent, ip },
  extraction: { latest_job_id?, status?: 'none'|'queued'|'processing'|'needs_review'|'approved'|'rejected'|'failed'|'cancelled' },
  notes?, deleted_at?, deleted_by?, version
}

// extraction_jobs
ExtractionJob {
  tenant_id, document_id, patient_id, visit_id?,
  status: 'queued'|'processing'|'needs_review'|'approved'|'rejected'|'failed'|'cancelled',
  provider, model, prompt_version, schema_version, ai_notice_version,
  attempts, retry_of_job_id?, requested_by, queued_at, started_at?, completed_at?,
  pages: number[],                    // page numbers sent to the provider
  pages_processed,
  result?: {
    transcription: string,
    fields: [{ key, value, confidence /* 0..1 */, level: 'high'|'medium'|'low',
               unreadable: boolean, flagged: boolean, requires_confirmation: boolean }],
    raw_output,                        // raw provider JSON; patient data, never logged (§10.4)
  },
  warnings: [{ code, message }],      // e.g. note_date_mismatch
  validation?: { valid: boolean, errors: string[] },
  draft?: { edited_fields: Record<string, unknown>, saved_at },
  review?: { reviewer_id, outcome: 'approved'|'rejected', edited_field_keys: string[],
            medications_confirmed: boolean, reviewed_at, notes? },
  resulting_entry_id?, resulting_prescription_id?, resulting_condition_ids?: ObjectId[],
  usage: { input_tokens, output_tokens },
  error?: { code, message, retryable }
}
```

### 6.6 Operational Collections

```ts
// audit_logs  (insert-only DB user; sequence arrives with the insert, chain is stamped later)
AuditLog {
  tenant_id, seq, at, actor_id, identity_id?, actor_role, session_id, request_id, ip, device_label,
  action: 'patient.view'|'patient.create'|'document.view'|'export.create'|'breakglass.use'|...,
  resource_type, resource_id, patient_id?, outcome: 'success'|'denied'|'error',
  break_glass: boolean, changed_fields?: string[], reason?,
  prev_hash?, hash?, chained_at?, hash_version: 1    // written by the chaining job, never by the API (§2 item 13)
}

// platform_audit_logs  (platform realm only; no tenant_id, never patient data)
PlatformAuditLog {
  _id, seq, at, actor_id, actor_role, action, resource_type, resource_id,
  outcome: 'success'|'denied'|'error', changed_fields?: string[], reason?, ip, request_id
}

// usage_counters  (metering, one doc per tenant per month)
UsageCounter { tenant_id, period: '2026-11', ai_pages_processed, ai_tokens_in, ai_tokens_out, storage_bytes, documents_captured }

// data_jobs  (imports, exports, purges)
DataJob {
  tenant_id, kind: 'patient_import'|'tenant_export'|'audit_export'|'tenant_purge',
  status: 'awaiting_upload'|'queued'|'running'|'completed'|'failed'|'cancelled',
  requested_by, params, progress: { total, processed },
  result_key?, error_report_key?, row_report?: { total, created, skipped, errors: [{row, message}] },
  expires_at?
}

// outbox  (written in the same transaction as the change; drained by the worker)
OutboxEvent {
  tenant_id, type: 'extraction.approved'|'document.ready'|'notification.email'|...,
  payload, created_at, dispatched_at?, attempts, last_error?
}  // TTL on dispatched events; a dispatcher sweep covers a crashed drain

// idempotency_keys  (for non-document POSTs that need retry safety)
IdempotencyKey {
  tenant_id, user_id, key, request_hash, status: 'in_progress'|'completed',
  response_status?, response_body?, expires_at /* TTL 24h */
}  // unique {tenant_id, user_id, key}; a second request with status 'in_progress' gets 409 idempotency_in_progress
```

### 6.7 Index Plan

| Collection | Indexes |
| :-- | :-- |
| identities | unique `email_normalized` |
| users | unique `{tenant_id, identity_id}` (partial: `identity_id` exists); `{tenant_id, status}`; partial unique `{tenant_id, invited_email_normalized}` where `status` is `invited` |
| invitations | partial unique `{tenant_id, email_normalized}` where `status` is `pending`; `{tenant_id, status, expires_at}` |
| patients | unique `{tenant_id, patient_number}`; `{tenant_id, phones.normalized}`; `{tenant_id, date_of_birth, name_search.normalized}`; `{tenant_id, care_team.user_id}`; `{tenant_id, clinician_ids}`; `{tenant_id, legacy_number}`; `{tenant_id, status, registration_date}`; Atlas Search index on name fields |
| patient\_merges | `{tenant_id, survivor_id, merged_at: -1}`; `{tenant_id, merged_id}` |
| visits | `{tenant_id, patient_id, visit_date: -1}`; `{tenant_id, attending_clinician_id, visit_date: -1}` |
| vital\_signs | `{tenant_id, patient_id, measured_at: -1}` |
| clinical\_entries | unique `{tenant_id, entry_id, version}`; unique partial `{tenant_id, entry_id}` where `is_current` is true; unique partial `{tenant_id, entry_id}` where `status` is `draft`; `{tenant_id, patient_id, visit_id}` |
| prescriptions | unique `{tenant_id, prescription_id, version}`; unique partial `{tenant_id, prescription_id}` where `is_current` is true; unique partial `{tenant_id, prescription_id}` where `status` is `draft`; `{tenant_id, patient_id, visit_id}` |
| documents | unique `{tenant_id, client_capture_id}`; `{tenant_id, patient_id, visit_date: -1}`; `{tenant_id, type, captured_at: -1}` |
| extraction\_jobs | `{tenant_id, status, queued_at}`; `{tenant_id, document_id}`; `{tenant_id, patient_id}` |
| audit\_logs | unique `{tenant_id, seq}`; partial `{tenant_id, seq}` where `hash` does not exist; `{tenant_id, patient_id, at: -1}`; `{tenant_id, actor_id, at: -1}` |
| platform\_audit\_logs | unique `seq`; `{actor_id, at: -1}` |
| sessions | `{tenant_id, user_id}`; `{tenant_id, family_id}`; TTL on `absolute_expires_at` |
| refresh\_tokens | unique `token_hash`; `{tenant_id, session_id}`; `{family_id}`; TTL on `expires_at` |
| access\_grants | `{tenant_id, user_id, patient_id}`; TTL on `expires_at` |
| outbox | `{tenant_id, dispatched_at, created_at}`; TTL on dispatched events |

## 7. API Design

### 7.1 Conventions

- **Envelope:** every response, success or error, is `{ status, status_code, message, data }`, the shape the existing API already uses. `data` is always an object that holds named keys (`patient`, `patients`, `pagination`), never a bare array.
- **Naming:** field names are snake\_case on the wire and resource identifiers are `_id`. Services, Pydantic models and MongoDB documents use the same snake\_case names, so one model definition drives storage, serialization and the OpenAPI schema (§12) and there is nothing to map or keep in sync.
- **Pagination:** cursor-based, returned as `data.pagination`.
- **Errors:** `status` is `error`, with a machine-readable `data.code`, field errors in `data.errors`, and `data.request_id`.
- **Concurrency and idempotency:** `If-Match` and `ETag` on versioned resources, `Idempotency-Key` on retry-sensitive POSTs, and `client_capture_id` for document capture.
- **Cross-tenant access** returns 404, never 403, so existence is not revealed.
- **Rate limits:** stricter on `/auth/*`, search, and signed-URL endpoints.

Section 16 gives the full conventions. Sections 17 to 25 list every endpoint with its request and response bodies, section 26 is the error code catalogue, and section 27 is the endpoint index. Permission names and the care-team rules are defined in section 5. This section holds conventions only; section 27 is the single index of routes and permissions, generated from the same route metadata as the OpenAPI document, so a route cannot exist in one and not the other.

## 8. Background Jobs

All queues run in the worker process on Celery, with Redis as broker and result backend; scheduled work (the `maintenance` and `metering` queues) runs on Celery beat. Every task's first argument is a context envelope containing `tenant_id` and the IDs it needs, never patient content; the task base class restores the tenant context before the body runs. Tasks are idempotent, use exponential backoff, and move to a dead-letter queue after the retry limit, with an alert.

| Queue | Job | Trigger | Notes |
| :-- | :-- | :-- | :-- |
| `image-processing` | `process-page` | Page upload completed | Magic-byte check, HEIC conversion, quality scoring, crop, deskew, contrast, thumbnail |
| `extraction` | `extract-document` | Extraction requested | Concurrency limit per tenant and global; rate-limit aware |
| `notifications` | `send-email`, `notify-user` | Invitations, resets, job completion, break-glass | Also pushes to the SSE stream |
| `audit` | `write-audit-retry` | Failed async audit write | Retries until stored |
| `data-jobs` | `import-patients`, `export-tenant`, `export-audit` | Admin request | Streams to and from object storage; progress saved on the `data_jobs` document |
| `maintenance` | `audit-hash-anchor`, `expire-sessions`, `purge-tenant`, `retention-sweep`, `orphan-upload-cleanup` | Scheduled | Purge and retention sweeps honor `legal_hold` and tenant retention config |
| `metering` | `rollup-usage` | Scheduled | Storage bytes and monthly counters |

Fairness: each queue caps concurrent work per tenant with a Redis-backed semaphore, so one tenant running a large import or backfill cannot starve the others.

## 9. Document Capture and Storage Pipeline

1. **Initiate.** The client calls `POST /documents` with its `client_capture_id`, page list, checksums and tags. The server creates the document in `initiated` state (or returns the existing one if the ID was seen) and returns per-page upload instructions.
2. **Upload.** The client uploads each page directly to object storage using presigned URLs. Large files use multipart upload, and the client can ask which parts are done and resume. The key is `t/{tenant_id}/pending/{document_id}/{page_no}/original`.
3. **Complete.** The server verifies the object (size, checksum, magic bytes), moves it to its final key, records `original`, and queues `process-page`. A page can be added after initiation while the document is `initiated` or `uploading` (`POST /documents/:id/pages`); `page_count` is recalculated, existing upload URLs stay valid, and add-page is refused once the document is `processing`.
4. **Process.** The worker generates the enhanced copy and thumbnail with Pillow (rotation from EXIF, resize, contrast normalization) and `pillow-heif` for HEIC input. Deskew and perspective crop need an edge-detection step, so the plan isolates it behind an `ImageEnhancer` protocol: start with Pillow plus OpenCV (`opencv-python-headless`), and move that step to a separate service only if its CPU cost starts to distort queue latency. Quality scores (blur via Laplacian variance, brightness histogram) are stored per page, and the response includes flags so the client can prompt a retake before saving.
5. **Ready.** When every page is processed the document becomes `ready` and the client is notified. A failure on enhancement does not block the document: the original is still usable, the page's `processing_status` becomes `enhancement_failed`, and `enhancement_error` carries the reason.
6. **Read.** Image access always goes through `GET .../url`, which checks permission and care-team scope, writes an audit event, and returns a signed URL valid for about 60 to 120 seconds. Responses carry no-store cache headers. Buckets have public access disabled, and CORS permits only the application origins.

Originals are write-once: the bucket uses object versioning, and the application role has no delete permission on original keys (deletion happens only through the purge job). An orphan cleanup job removes `pending/` objects older than 24 hours whose documents never completed.

## 10. AI Extraction Pipeline

### 10.1 Provider interface

```py
class ExtractionProvider(Protocol):
    name: str

    async def extract(
        self,
        *,
        images: list[ImageInput],      # original or enhanced, per config
        schema: dict[str, Any],        # target JSON Schema
        prompt_version: str,
        options: ExtractOptions,       # model, timeout_ms
    ) -> ExtractionResult: ...         # raw_json, transcription, usage
```

`ImageInput`, `ExtractOptions` and `ExtractionResult` are Pydantic models in `app/modules/extraction/providers/base.py`; the protocol is what adapters implement, so no adapter can leak a provider SDK type into the rest of the service.

Adapters are registered by name (for example `anthropic`, `openai`, `google`). The tenant config selects provider and model, and a platform-level default and fallback exist. Provider API keys live in the secret manager, never in tenant config. Each adapter is contract-tested against recorded fixtures.

### 10.2 Job flow

1. `POST /documents/:id/extractions` checks: tenant AI enabled, document marked handwritten, user permitted, monthly cap not exceeded, page count within limit. It creates an `ExtractionJob` in `queued` state, records `ai_notice_version`, and returns 202.
2. The worker loads page images from storage (server-side, never exposing URLs), calls the provider, and receives raw JSON plus a transcription.
3. **Schema validation.** The output is validated with Pydantic against the extraction schema, using the `model_json_schema()` rendering of the same models that were sent to the provider as the prompt schema, so the prompt and the validator cannot diverge. Fields that fail validation are dropped and flagged, not repaired by guessing. One automatic retry is allowed with a repair instruction when the whole output is malformed.
4. **Confidence handling.** The prompt asks the model to return a confidence for each field and to return `null` with `unreadable: true` instead of guessing. The server maps confidence to `high`, `medium` and `low` using configurable thresholds, and flags anything below the threshold. Self-reported confidence is a weak signal, so the plan treats all handwriting output as unverified, and the review screen requires a deliberate action on flagged fields.
5. **Medication safety.** Any field in the medications group is always marked as requiring confirmation, regardless of confidence. Approval is rejected by the API unless `medications_confirmed` is true when medications are present.
6. The job becomes `needs_review`, usage is metered, and the requester is notified.

### 10.3 Extraction target schema (initial)

| Field | Type | Notes |
| :-- | :-- | :-- |
| `presenting_complaint` | string |  |
| `history` | string |  |
| `examination` | string |  |
| `observations` | string |  |
| `diagnoses` | array of `{ text, icd10? }` | ICD-10 only if clearly written; otherwise null |
| `plan` | string |  |
| `medications` | array of `{ drug_name, dose, dose_unit, route, frequency, duration }` | Always confirmation-gated |
| `follow_up` | string |  |
| `note_date` | date | Compared with the document's visit date; mismatch is flagged |
| `transcription` | string | Full text as read |

The schema is versioned (`schema_version`) so older jobs remain interpretable, and tenants can later add fields.

### 10.4 Review and approval

- The review endpoint returns each field with its confidence level, a link to fetch page images, and any validation flags. Reviewer edits are auto-saved as a draft on the job.
- On approval, the server compares submitted values to the AI values to decide each field's provenance: unchanged is `ai_extracted`, changed is `ai_extracted_edited`, and fields the reviewer typed from nothing are `typed`. These go into `provenance` on the created draft entry.
- Approval creates a draft `ClinicalEntry` (and `Prescription` and `PatientCondition` records where applicable) linked to `source_document_id` and `extraction_job_id`, inside one transaction. The job moves to `approved` and stores `resulting_entry_id`.
- Rejection or failure creates nothing structured; the image stays attached to the patient and visit.
- Raw model output is stored in `result.raw_output` on the job for audit and debugging for a configurable period. It is treated as patient data: same tenant isolation, encryption and retention rules, and it is excluded from logs.
- The notification and any follow-on job are written to the `outbox` in the same transaction as the created records, then dispatched after commit, so a crash cannot commit draft records without queuing their delivery.

### 10.5 Privacy and cost controls

- Disabled by default per tenant; enabling requires an administrator action that records acceptance of the AI notice version.
- Provider requirements from section 8.1 of the requirements (data processing agreement, no training on customer data, known region) are checked in a deployment checklist before any provider is enabled for production.
- Only the page images needed are sent; no patient demographics are included in the prompt.
- Monthly page cap per tenant, request timeouts, concurrency limits, and a circuit breaker that pauses extraction and alerts if the provider's error rate spikes.

## 11. Search Implementation

- **Normalization:** names are lowercased, accent-stripped, and tokenized; common name particles and honorifics are removed; phone numbers are normalized to E.164 (default country from tenant settings, Nigeria for the initial market).
- **Duplicate detection (registration):** candidates are found through exact normalized phone, DOB plus name token overlap, and fuzzy name similarity. Each candidate gets a score and the reasons for it (for example, same DOB and phone, similar name). The registration endpoint returns the candidates and demands an explicit override with a reason to continue.
- **Fuzzy search, option A (preferred):** MongoDB Atlas Search with an analyzer for names, `fuzzy` matching (maxEdits 1 to 2), and an autocomplete field, always filtered by `tenant_id` and care-team scope.
- **Fuzzy search, option B (self-hosted MongoDB):** a derived `name_search.phonetic` field using Double Metaphone (with attention to common Nigerian and Arabic-derived name variants) plus trigram tokens, queried with indexed lookups and re-ranked in the application using Jaro-Winkler distance.
- **Care-team filter at query time:** for users without `patient:read:all`, the query adds the filter on `care_team.user_id` or an attending-visit lookup (maintained as a denormalized `clinician_ids` array on the patient to keep this a single indexed filter).
- **Release 2 and later:** index the text of approved clinical entries for full-text search, subject to the same filters. The `content_text` field is built on signing now, so no backfill is needed.

## 12. Cross-Cutting Concerns

- **Configuration:** environment variables validated at boot by a `pydantic-settings` model (the app refuses to start when a required variable is missing or malformed); secrets from a secret manager (not committed, not in tenant config).
- **Security headers and input hygiene:** Starlette's security-headers middleware, strict CORS, request size limits, NoSQL operator injection protection (sanitize `$`-prefixed keys), and Pydantic models with `extra="forbid"` so an unknown field is a 422 rather than something silently ignored.
- **Event delivery:** `GET /events` is served by whichever API instance the client is connected to. The worker publishes to Redis, every instance relays to its own subscribers, and each tenant has a capped stream (`t:{tenant_id}:events`, the last 15 minutes) backing `Last-Event-ID` replays. Stream entries carry identifiers only. Browser clients authenticate the stream with an `HttpOnly` session cookie or a one-time ticket from `POST /events/ticket`, because `EventSource` cannot set an `Authorization` header.
- **Transactional outbox:** a state change that must trigger a job, an email or an SSE event writes an `outbox` document in the same transaction. The worker drains it with retries and a sweep re-dispatches anything undelivered after 30 seconds. Nothing is enqueued from a request after the transaction commits.
- **Time:** every stored timestamp is UTC and every business date (`visit_date`, `registration_date`, date of birth) is a date-only string resolved in the tenant timezone. `patient_number` year tokens and `age_years` use the tenant timezone, not the server's.
- **Logging:** structured logs with `request_id`, `tenant_id`, `user_id`. A redaction layer strips names, phone numbers and clinical text. Patient content never appears in logs, error reports or metrics labels.
- **Observability:** metrics for latency by route, queue depth and age, extraction success and review outcomes, storage errors, and auth failures; traces across API and worker through the request ID.
- **Health:** `/health/live`, `/health/ready` (Mongo, Redis, storage reachability).
- **Error handling and time:** one clock service for tests; all timestamps UTC.
- **Migrations:** versioned Python migration scripts (`mgm`) run with `run_as_system`; indexes are created by migration, never by an automatic index build in production. Collection access is untyped at the driver level, so a migration that renames a field also updates the Pydantic model in the same change.
- **Backups and restore:** daily snapshots plus point-in-time recovery where the host supports it; per-tenant restore implemented as export of one tenant's documents from a restored snapshot into the live database after verification (this is only possible because every document has `tenant_id`). Restore drills are scheduled and documented. Object storage uses versioning and, where available, replication.
- **API documentation:** OpenAPI generated by FastAPI from the Pydantic models and route signatures, published per version, with examples for the capture and extraction flows. Because the models are snake_case and are used verbatim on the wire, the published spec is the serialization itself; there is no field-name map to keep in step. A CI step regenerates `openapi.json` and fails when it differs from the committed file, so the published contract cannot silently drift.
- **Data protection:** encryption at rest for database and storage, TLS everywhere, signed URLs short-lived, no public buckets, retention and deletion jobs configurable per tenant and gated by legal hold.

## 13. Testing Strategy

| Level | Scope |
| :-- | :-- |
| Unit | Policy service, ID generator, duplicate scoring, BMI and range rules, provenance logic, config validation |
| Integration (real MongoDB replica set and Redis in containers) | Repositories with the tenant collection guard, transactions (merge, sign, approve), identity and membership resolution, auth flows with token rotation and reuse detection, queue processors, outbox dispatch |
| Concurrency | Two simultaneous amendments of a signed entry produce exactly one `version + 1`; the one-draft invariant holds; a duplicate in-flight `Idempotency-Key` gets `409 idempotency_in_progress`; `patient_number` allocations never collide under parallel registration; a repeated merge is idempotent |
| Isolation suite | Section 4.6; runs on every pull request and blocks merge |
| Contract | Provider adapters against recorded responses; S3 operations against a local S3-compatible server |
| End-to-end | Full flows: onboard tenant, invite staff, register patient, capture and process a document, extract, review, sign, search, export |
| Security | Authorization matrix test covering every route in section 27 against every default role, with the permission catalogue asserted both ways (no route uses an unknown permission, no permission is unused); break-glass flow, session revocation latency, dependency scanning, secret scanning, periodic penetration test before launch |
| Load | Patient search under concurrent users, upload bursts, extraction queue under a backfill batch |
| Extraction quality | A de-identified or synthetic set of handwritten notes to track field accuracy and the unreadable-field rate across prompt and model changes |

CI runs `ruff` (lint and format check), `mypy --strict`, `pytest` (unit, integration and the isolation suite) and the OpenAPI diff, and blocks merge on any of them.

## 14. Delivery Plan

Estimates assume two to three backend engineers and are rough. They should be revisited once the open questions in section 15 are answered.

| Milestone | Scope | Exit criteria | Est. |
| :-- | :-- | :-- | :-- |
| M0 Foundations | Repo, CI (`ruff`, `mypy`, `pytest`, OpenAPI drift check), Docker Compose (Mongo replica set, Redis, local S3), `pydantic-settings` config, logging, exception handlers, response envelope, outbox skeleton, health checks | Hello-world API and worker deploy to a staging environment | 1 to 2 wks |
| M1 Tenancy and identity | Tenant context, tenant collection guard, connection registry, tenants and config, identities and memberships, roles, invitations, login, MFA, sessions and refresh tokens, permission catalogue and dependencies, audit core, isolation suite | Two seeded tenants cannot see each other; MFA login works; sessions revocable; token reuse revokes the family | 3 to 4 wks |
| M2 Patients and clinical | Patient registration, ID generator, duplicate detection, merge, care team, visits, vitals and trends, clinical entries with versions and signing, prescriptions, conditions, break-glass | Full chart can be created and read; versions and audit visible | 4 to 5 wks |
| M3 Documents | Capture endpoints, multipart and resume, quality checks, image worker, signed reads, tagging, document search | Phone capture to tagged, viewable document with retries safe | 3 to 4 wks |
| M4 Extraction | Provider interface and first adapter, job flow, validation, review and approval endpoints, medication gating, provenance, basic metering and caps, SSE notifications | Handwritten sample set extracts, reviewed and signed end to end | 3 to 4 wks |
| M5 Search, admin, data ops | Fuzzy search, audit export and verification, CSV import, tenant export, self-service signup, onboarding polish | Pilot tenant onboarded without developer help | 3 wks |
| M6 Hardening (Release 1) | Security review, load tests, backup and restore drill, retention and purge, legal and DPA checklist, pen test fixes | Launch checklist signed off | 2 to 3 wks |
| R2 | Usage dashboard, batch capture, backfill dashboard, full-text search of confirmed notes | Backfill progress visible to administrators | 3 to 4 wks |

Suggested build order inside a milestone is data layer, service, endpoint, tests, then docs, and the frontend team can begin against the OpenAPI spec as soon as M1 publishes it. Mock endpoints for M2 and M3 can be generated from the spec.

## 15. Risks and Open Questions

### 15.1 Risks

| Risk | Impact | Mitigation |
| :-- | :-- | :-- |
| Tenant isolation bug leaks records | Critical | Central collection guard, import-linter rule, isolation suite as a merge gate, penetration test |
| Handwriting extraction errors reach the record | Critical | Mandatory review, medication confirmation, unreadable-not-guessed rule, sign-off by clinician, provenance on every field |
| Legal and data protection delay (Nigeria Data Protection Act, AI provider terms, cross-border processing) | High | Start legal review in M1; keep AI off by default; provider-agnostic design lets the provider change |
| Image enhancement quality is weak on phone photos | Medium | Keep originals untouched; enhancer behind an interface; quality flags and retake prompt |
| Fuzzy search quality for local names | Medium | Test with real name samples; choose Atlas Search or tuned phonetic keys after a spike in M2 |
| Cost of AI extraction during backfill | Medium | Monthly caps, metering from Release 1, queue limits |
| Flaky mobile connections | Medium | Presigned multipart with resume, idempotent captures, status endpoint |
| Audit log volume | Medium | Index plan, retention config, archive to cold storage |

### 15.2 Questions for the product owner

1. Where will MongoDB be hosted (Atlas or self-managed)? This decides the search approach and backup tooling.
2. Which AI provider and region are acceptable under the data protection review? Is patient-level consent required in addition to a hospital-level notice?
3. Confirm the email uniqueness rule from section 2, item 1. **Answered in §2 item 11:** one global identity, one active membership at a time.
4. Who may assign patients to a clinician's care team, and may Records Clerks see clinical content or only demographics and documents?
5. Should the Medical Director be able to approve extractions of his own notes, or only view them?
6. What retention periods apply, and may a tenant request deletion of records that fall under a legal retention duty?
7. Is ICD-10 coding needed in Release 1, and is a licensed drug formulary expected?
8. Is SMS needed for MFA or notifications, or is email plus authenticator app enough?
9. Do hospitals want to configure patient ID formats themselves in the admin UI, or only through onboarding?
10. Target data volume for the first tenant (patients, pages per month) so storage and queue capacity can be sized.
11. Is Atlas available for the first deployment, or must search work on self-managed MongoDB? This decides §11 option A versus option B and the backup tooling. **Blocks M2.**
12. What are the hosting, secret-manager, transactional-email and error-tracking choices? M0's staging target and M1's invitations depend on them. **Blocks M0.**
13. Is the client a browser PWA, a native app, or both? This decides refresh tokens in an `HttpOnly` cookie versus the body, and whether SSE or push is used. **Blocks M1.**
14. Who may add pages to a document after initiation, and may any holder of `document:tag` retag a document to another patient? The current defaults are the capturer for pages and any `document:tag` holder for retagging, both audited.
15. Must `legacy_number` be unique per tenant? The plan leaves it non-unique because duplicate file numbers are common in paper registers, and the import reports them as warnings.

## 16. API Conventions

Every endpoint in sections 17 to 25 returns the same four-key envelope the existing API already uses: `status`, `status_code`, `message`, `data`. Base path is `/api/v1`. Examples are illustrative; the field names and enums are the proposed contract and should be confirmed with the frontend team.

### 16.1 Response envelope

```json
{
  "status": "success",
  "status_code": 200,
  "message": "Patient retrieved successfully",
  "data": {
    "patient": { "_id": "66e7c0a2e4b0a1b2c3d4e5f6", "patient_number": "SMH-2026-000731" }
  }
}
```

- `status` is `success` for 2xx responses and `error` for everything else.
- `status_code` repeats the HTTP status code.
- `message` is a short sentence that is safe to show in the interface. Clients branch on `status_code` and, for errors, on `data.code`, never on `message`.
- `data` is always an object. One resource sits under a singular key (`patient`), a list sits under a plural key (`patients`) next to `pagination`. Actions with nothing to return send `"data": {}`.
- There are no empty response bodies: actions that would otherwise return 204 return 200 with the envelope and an empty `data`. The only exception is the event stream in section 22.

List response, with pagination inside `data`:

```json
{
  "status": "success",
  "status_code": 200,
  "message": "Patients retrieved successfully",
  "data": {
    "patients": [ { "_id": "66e7...", "patient_number": "SMH-2026-000731" } ],
    "pagination": { "limit": 25, "next_cursor": "eyJpZCI6Ij...", "has_more": true }
  }
}
```

Action response with nothing to return:

```json
{ "status": "success", "status_code": 200, "message": "Session ended successfully", "data": {} }
```

### 16.2 Error envelope

Errors keep the same four keys. `data` carries the machine-readable `code`, optional field errors, and the request ID to quote when reporting a problem.

```json
{
  "status": "error",
  "status_code": 422,
  "message": "Validation failed",
  "data": {
    "code": "validation_failed",
    "errors": [ { "field": "date_of_birth", "message": "must be a valid date (YYYY-MM-DD)" } ],
    "request_id": "req_8f2c1a"
  }
}
```

Each endpoint below lists its errors as `status code`. Where an error carries extra data (for example the duplicate candidates on patient registration), the example shows the full body. The complete code list is in section 26.

### 16.3 Pagination

List endpoints accept `limit` (default 25, maximum 100) and `cursor` (opaque, taken from the previous `data.pagination.next_cursor`). Offset pagination is not used on patient or audit data.

### 16.4 Headers

| Header | Direction | Purpose |
| :-- | :-- | :-- |
| `Authorization: Bearer <access_token>` | Request | Required on every route except those marked Public |
| `Content-Type: application/json` | Request | All bodies are JSON unless stated |
| `Idempotency-Key: <uuid>` | Request | Optional on retry-sensitive POSTs (visit, clinical entry, prescription, extraction request, merge) |
| `If-Match: "<version>"` | Request | Required on PATCH of versioned resources; a stale value returns 412 |
| `X-Request-Id` | Both | Echoed back, and the same value appears as `data.request_id` on errors |
| `ETag: "<version>"` | Response | Current version of a single resource |

### 16.5 Status codes

| Status | Meaning |
| :-- | :-- |
| 200, 201, 202 | OK, created, accepted for asynchronous processing |
| 400 | Malformed request (bad JSON, bad cursor) |
| 401 | Missing, expired or invalid token, or session revoked |
| 403 | Authenticated but lacks the permission or care-team access |
| 404 | Not found, or belongs to another hospital (indistinguishable by design) |
| 409 | Conflict: duplicate candidates, email unavailable, state conflict |
| 412 | `If-Match` version is stale |
| 413, 415 | Payload too large, unsupported media type |
| 422 | Validation failed |
| 423 | Account locked |
| 429 | Rate limited (`Retry-After` header set) |
| 503 | Dependency unavailable (for example AI paused by the circuit breaker) |

### 16.6 Naming and common types

- Field names and query parameters are snake\_case. Resource identifiers are `_id` on the resource itself, and `<thing>_id` when one resource points to another (`patient_id`, `visit_id`).
- Timestamps are ISO 8601 UTC (`created_at`, `updated_at`). Dates without a time are `YYYY-MM-DD`.
- Every stored resource also returns `created_at`, `updated_at`, `created_by` (a `UserRef`) and `version` unless noted. These are left out of most examples to keep them short.
- The storage models in section 6 are snake\_case, and so are the Pydantic models that serialize them, so a field keeps one name from MongoDB to the response body. There is no key-transform layer and no field-name map to maintain.

```py
Id = str                        # 24-character hex ObjectId
Timestamp = str                 # '2026-11-09T09:30:00Z'
DateOnly = str                  # 'YYYY-MM-DD'

class Name(TypedDict):
    given: list[str]
    family: str
    other: NotRequired[str]

class Phone(TypedDict):
    value: str
    is_primary: NotRequired[bool]   # responses add `normalized` (E.164)

class Address(TypedDict):
    line1: str
    line2: NotRequired[str]
    city: str
    state: str
    country: str
    lga: NotRequired[str]

class UserRef(TypedDict):       # lightweight reference
    _id: Id
    name: str
    role: NotRequired[str]

Sex = Literal['male', 'female', 'other', 'unknown']
```

## 17. Authentication and Sessions

### POST /auth/login

Public. Rate limited. The tenant is resolved from the email.

```json
// request
{ "email": "ada.okafor@stmarys.example", "password": "correct horse battery staple", "device_label": "Pixel 8, Chrome" }
```

```json
// 200: MFA not required (or already satisfied)
{
  "status": "success",
  "status_code": 200,
  "message": "Login successful",
  "data": {
    "mfa_required": false,
    "access_token": "eyJhbGciOi...",
    "expires_in": 600,
    "refresh_token": "rt_3b1f...",
    "refresh_expires_at": "2026-11-09T17:30:00Z",
    "user": {
      "_id": "66f1c0a2e4b0a1b2c3d4e5f6", "is_verified": true, "email": "ada.okafor@stmarys.example",
      "name": { "given": ["Ada"], "family": "Okafor" },
      "roles": [ { "_id": "66b2...", "key": "doctor", "name": "Doctor" } ], "tenant_id": "66a0...",
      "created_at": "2026-09-01T08:00:00Z"
    }
  }
}
```

```json
// 200: MFA required
{ "status": "success", "status_code": 200, "message": "MFA verification required",
  "data": { "mfa_required": true, "challenge_token": "ch_91ac...", "challenge_expires_in": 300, "methods": ["totp", "recovery"] } }
```

```json
// 200: role requires MFA but the user has not enrolled
{ "status": "success", "status_code": 200, "message": "MFA enrollment required",
  "data": { "mfa_enrollment_required": true, "enrollment_token": "en_77de...", "enrollment_expires_in": 900 } }
```

Errors: `401 invalid_credentials` (same message for unknown email and wrong password), `423 account_locked` (with `locked_until` in `data`), `403 account_deactivated`, `403 tenant_suspended`, `429 rate_limited`.

### POST /auth/mfa/verify

```json
// request
{ "challenge_token": "ch_91ac...", "code": "482913", "type": "totp" }     // type: 'totp' or 'recovery'
```

The 200 response is the same as a successful login, with the message `MFA verified successfully`. Errors: `401 invalid_code`, `401 challenge_expired`, `429 rate_limited`.

### POST /auth/refresh

```json
// request
{ "refresh_token": "rt_3b1f..." }
// 200: a new access token AND a new refresh token (the old one is now invalid)
{ "status": "success", "status_code": 200, "message": "Token refreshed successfully",
  "data": { "access_token": "eyJ...", "expires_in": 600, "refresh_token": "rt_c90d...", "refresh_expires_at": "2026-11-09T17:45:00Z" } }
```

Presenting an already-used refresh token returns `401 refresh_reuse_detected` and revokes the whole session. For browser clients the refresh token may be delivered as an `http_only; Secure; SameSite=Strict` cookie instead of in the body.

### POST /auth/logout

Request body empty.

```json
{ "status": "success", "status_code": 200, "message": "Logged out successfully", "data": {} }
```

### POST /auth/password/forgot

```json
// request
{ "email": "ada.okafor@stmarys.example" }
// 202: always the same response, whether or not the account exists
{ "status": "success", "status_code": 202, "message": "If the account exists, an email has been sent", "data": {} }
```

### POST /auth/password/reset

```json
// request
{ "token": "pr_1a2b...", "new_password": "a-new-long-passphrase-2026" }
// 200
{ "status": "success", "status_code": 200, "message": "Password reset successfully", "data": {} }
```

All sessions for the user are revoked. Errors: `400 invalid_or_expired_token`, `422 weak_password` (with `data.errors` describing the rule).

### POST /auth/password/change

```json
// request
{ "current_password": "old...", "new_password": "new..." }
// 200
{ "status": "success", "status_code": 200, "message": "Password changed successfully", "data": {} }
```

Other sessions are revoked; the current one continues. Errors: `401 invalid_credentials`, `422 weak_password`.

### POST /auth/invitations/accept

```json
// request
{ "token": "inv_5c7e...", "password": "a-long-passphrase-2026", "name": { "given": ["Ngozi"], "family": "Eze" } }
// 200: the user is active; MFA enrollment is next if required
{ "status": "success", "status_code": 200, "message": "Invitation accepted successfully",
  "data": { "mfa_enrollment_required": true, "enrollment_token": "en_3300..." } }
```

Errors: `400 invalid_or_expired_token`, `409 invitation_already_used`.

### POST /auth/mfa/enroll

Authenticated with an access token or an enrollment token. Request body empty.

```json
{ "status": "success", "status_code": 200, "message": "MFA enrollment started",
  "data": { "secret": "JBSWY3DPEHPK3PXP", "otpauth_uri": "otpauth://totp/HMS:ada.okafor@stmarys.example?secret=JBSWY3DPEHPK3PXP&issuer=HMS" } }
```

### POST /auth/mfa/enroll/confirm

```json
// request
{ "code": "482913" }
// 200: recovery codes are shown only once
{ "status": "success", "status_code": 200, "message": "MFA enabled successfully",
  "data": { "recovery_codes": ["7f3k-91xa", "b2m8-04qz", "..."], "access_token": "eyJ...", "expires_in": 600, "refresh_token": "rt_...", "refresh_expires_at": "2026-11-09T17:30:00Z" } }
```

### GET /auth/me

```json
{
  "status": "success",
  "status_code": 200,
  "message": "Profile retrieved successfully",
  "data": {
    "user": {
      "_id": "66f1...", "is_verified": true, "email": "ada.okafor@stmarys.example",
      "name": { "given": ["Ada"], "family": "Okafor" }, "mfa_enabled": true, "created_at": "2026-09-01T08:00:00Z"
    },
    "roles": [ { "_id": "66b2...", "key": "doctor", "name": "Doctor" } ],
    "permissions": [ "patient:read", "patient:update", "clinical:write", "clinical:sign", "document:capture" ],
    "tenant": {
      "_id": "66a0...", "name": "St Mary's Hospital", "logo_url": "https://signed.example/logo?...",
      "modules": { "ai_extraction": true, "icd_coding": true, "insurance": false },
      "document_types": [ { "key": "case_note", "label": "Case note", "default_handwritten": true } ],
      "visit_types": [ { "key": "outpatient", "label": "Outpatient" } ],
      "security": { "idle_timeout_min": 15 }
    }
  }
}
```

### PATCH /auth/me

Updates the caller's own name and phone. `email` is rejected with `422 email_change_not_supported`; changing it is an administrator action (§5.1). Returns `data.user`.

```json
{ "name": { "given": ["Ada"], "family": "Okafor" }, "phone": "+2348012345678" }
```

### POST /auth/mfa/disable, POST /auth/mfa/recovery-codes

Disabling MFA requires the current password and a valid TOTP or recovery code, and is refused with `403 mfa_required_by_role` when any role the user holds has `mfa_required` set. Regenerating recovery codes requires the password and returns a fresh set once; the set is never returned again.

```json
// disable request
{ "password": "correct horse battery staple", "code": "482913" }
// 200
{ "status": "success", "status_code": 200, "message": "MFA disabled successfully", "data": {} }

// recovery codes request
{ "password": "correct horse battery staple" }
// 200: shown only once
{ "status": "success", "status_code": 200, "message": "Recovery codes regenerated successfully",
  "data": { "recovery_codes": ["7f3k-91xa", "b2m8-04qz", "..."] } }
```

### GET /auth/sessions

```json
{ "status": "success", "status_code": 200, "message": "Sessions retrieved successfully",
  "data": { "sessions": [ { "_id": "66c9...", "device_label": "Pixel 8, Chrome", "ip": "102.89.x.x", "created_at": "2026-11-09T09:30:00Z", "last_seen_at": "2026-11-09T10:02:00Z", "current": true } ] } }
```

### DELETE /auth/sessions/:id

Ends one of your own sessions (ending the current one is the same as logout).

```json
{ "status": "success", "status_code": 200, "message": "Session ended successfully", "data": {} }
```

## 18. Staff, Roles and Tenant Administration

### GET /users

Permission `user:manage`. Query: `status` (`active`, `invited`, `deactivated`), `role_id`, `q`, `limit`, `cursor`. Each item joins the identity, so `email` and `is_verified` come from `identities` and are read-only here; the rest of the object is the membership.

```json
{
  "status": "success",
  "status_code": 200,
  "message": "Users retrieved successfully",
  "data": {
    "users": [
      {
        "_id": "66f1...", "is_verified": true, "email": "ada.okafor@stmarys.example",
        "name": { "given": ["Ada"], "family": "Okafor" }, "status": "active",
        "roles": [ { "_id": "66b2...", "key": "doctor", "name": "Doctor" } ],
        "mfa_enabled": true, "last_login_at": "2026-11-09T09:30:00Z", "created_at": "2026-09-01T08:00:00Z"
      }
    ],
    "pagination": { "limit": 25, "next_cursor": null, "has_more": false }
  }
}
```

### POST /users/invitations

```json
// request
{ "email": "ngozi.eze@stmarys.example", "role_ids": ["66b2..."], "note": "Welcome to the team." }
// 201
{ "status": "success", "status_code": 201, "message": "Invitation sent successfully",
  "data": { "invitation": { "_id": "66d1...", "email": "ngozi.eze@stmarys.example", "role_ids": ["66b2..."], "status": "pending", "expires_at": "2026-11-16T09:30:00Z" } } }
```

Errors: `409 email_unavailable` (the email belongs to an active user; the message does not say which hospital), `422 unknown_role`.

### GET /users/invitations

Permission `user:manage`. Pending invitations only, including ones whose membership is still `invited`. Each item is `_id`, `email`, `role_ids`, `status`, `expires_at` and `invited_by`. Returns `data.invitations`.

### GET /users/directory

Any authenticated user; no permission is required, because care-team and attending-clinician pickers would otherwise be unusable for Doctors and Records Clerks. Query `q`, `role_key`, `limit`. Returns `data.users` with `_id`, `name`, `display_name`, `roles[]` and `professional.cadre` only: no email, phone, status or session data.

### POST /users/invitations/:id/resend, DELETE /users/invitations/:id

Resend issues a fresh token. Delete revokes the invitation. Both return `200` with the message `Invitation resent successfully` or `Invitation revoked successfully` and `"data": {}`.

### GET /users/:id

Same object as a list item, plus `professional` and `perm_version`.

```json
{ "status": "success", "status_code": 200, "message": "User retrieved successfully",
  "data": { "user": { "_id": "66f1...", "email": "ada.okafor@stmarys.example", "status": "active", "roles": [ { "_id": "66b2...", "key": "doctor", "name": "Doctor" } ], "professional": { "license_no": "MDCN-12345", "cadre": "Consultant" }, "version": 4 } } }
```

### PATCH /users/:id

Header `If-Match: "4"`. Any subset of fields:

```json
{ "name": { "given": ["Ada"], "family": "Okafor" }, "role_ids": ["66b2...", "66b3..."], "professional": { "license_no": "MDCN-12345", "cadre": "Consultant" } }
```

Returns `data.user` with the message `User updated successfully`. Role changes take effect on the user's next request.

### POST /users/:id/deactivate, /reactivate, /mfa/reset

```json
// deactivate and mfa/reset request
{ "reason": "Left the hospital pending HR confirmation" }
// 200
{ "status": "success", "status_code": 200, "message": "User deactivated successfully", "data": {} }
```

Deactivation ends all of the user's sessions immediately. MFA reset forces re-enrollment on the next login.

### POST /users/:id/remove

Removes the user from the hospital so another hospital can invite them. The membership becomes `removed` and the identity is retained, so audit entries and `created_by` references keep resolving; the email is released because the identity no longer has an active membership, and the next invitation creates a fresh membership.

```json
// request
{ "reason": "Transferred to another hospital", "reassign_care_team_to": "66f9..." }
// 200
{ "status": "success", "status_code": 200, "message": "User removed successfully", "data": {} }
```

If the user is on patient care teams and `reassign_care_team_to` is omitted, the response is `409 care_team_reassignment_required` with `data.patient_count`.

### GET /users/:id/sessions, DELETE /users/:id/sessions/:session\_id

Same shape as `/auth/sessions` (`data.sessions`). Delete returns `200` with `"data": {}` and takes effect within seconds.

### PUT /users/:id/signature

Permission `user:manage`, or self-service for the caller's own record. Same presigned-upload shape as `PUT /tenant/logo`; the uploaded key is stored as `professional.signature_image_key`. Request body: `{ "mime": "image/png", "bytes": 18211 }`.

### GET /roles, POST /roles

```json
// POST request
{ "key": "senior_nurse", "name": "Senior Nurse", "permissions": ["patient:read", "visit:create", "vitals:write", "document:capture", "clinical:read"], "mfa_required": true }
// 201
{ "status": "success", "status_code": 201, "message": "Role created successfully",
  "data": { "role": { "_id": "66b9...", "key": "senior_nurse", "name": "Senior Nurse", "permissions": ["patient:read", "..."], "mfa_required": true, "is_system": false, "user_count": 0 } } }
```

GET returns `data.roles` (an array of the same objects). Errors: `409 role_key_exists`, `422 unknown_permission`.

### GET /roles/:id, PATCH /roles/:id, DELETE /roles/:id

GET and PATCH return `data.role`. PATCH accepts `name`, `permissions`, `mfa_required` (with `If-Match`). Delete returns `200` with `"data": {}`, or `409 role_in_use` with user counts in `data`. System roles reject changes to `key` and deletion with `403 system_role_protected`.

### GET /permissions

Permission `role:manage`. The catalogue is code-owned, and every entry names its group and description.

```json
{ "status": "success", "status_code": 200, "message": "Permissions retrieved successfully",
  "data": { "permissions": [ { "key": "patient:read", "group": "Patients", "description": "View patient demographics" }, { "key": "clinical:sign", "group": "Clinical", "description": "Sign clinical entries" } ] } }
```

### GET /tenant

Any authenticated user.

```json
{ "status": "success", "status_code": 200, "message": "Hospital retrieved successfully",
  "data": { "tenant": { "_id": "66a0...", "name": "St Mary's Hospital", "slug": "st-marys", "logo_url": "https://signed.example/logo?...", "timezone": "Africa/Lagos", "modules": { "ai_extraction": true, "icd_coding": true, "insurance": false } } } }
```

### GET /tenant/config, PATCH /tenant/config

Permission `tenant:configure`. PATCH takes any subset and is validated as a whole. Both return `data.config`.

```json
// GET 200 (abridged)
{
  "status": "success",
  "status_code": 200,
  "message": "Configuration retrieved successfully",
  "data": {
    "config": {
      "branding": { "display_name": "St Mary's Hospital", "logo_key": "t/66a0.../branding/logo.png" },
      "modules": { "ai_extraction": true, "icd_coding": true, "insurance": false },
      "patient_id_format": { "pattern": "SMH-{YYYY}-{SEQ:6}", "reset_sequence": "yearly" },
      "document_types": [ { "key": "case_note", "label": "Case note", "default_handwritten": true, "active": true } ],
      "visit_types": [ { "key": "outpatient", "label": "Outpatient", "active": true } ],
      "ai": { "provider": "anthropic", "model": "configured-model-id", "notice_version": "2026-11", "max_pages_per_document": 10, "monthly_page_cap": 2000 },
      "security": { "idle_timeout_min": 15, "absolute_timeout_hours": 12, "password_min_length": 12, "break_glass_minutes": 60 },
      "retention": { "clinical_years": 15, "audit_years": 7, "document_years": 15 },
      "version": 7
    }
  }
}
```

```json
// PATCH request (If-Match: "7"): enable AI and acknowledge the notice
{ "modules": { "ai_extraction": true }, "ai": { "notice_version": "2026-11", "accept_notice": true } }
```

Errors: `422 invalid_patient_id_pattern`, `422 ai_notice_not_accepted` (enabling AI without `accept_notice`), `412 version_mismatch`.

### PUT /tenant/logo

```json
// request
{ "mime": "image/png", "bytes": 48211 }
// 200: upload the file with an HTTP PUT to upload_url, then PATCH /tenant/config with branding.logo_key
{ "status": "success", "status_code": 200, "message": "Upload URL generated successfully",
  "data": { "upload": { "upload_url": "https://storage.example/...", "headers": { "Content-Type": "image/png" }, "key": "t/66a0.../branding/logo.png", "expires_at": "2026-11-09T09:40:00Z" } } }
```

### GET /tenant/usage

```json
{ "status": "success", "status_code": 200, "message": "Usage retrieved successfully",
  "data": { "usage": { "period": "2026-11", "ai_pages_processed": 312, "ai_page_cap": 2000, "documents_captured": 540, "storage_bytes": 8421934080, "active_users": 23 } } }
```

## 19. Patients

The lightweight card used in lists, search results and candidates:

```ts
type PatientCard = {
  _id: Id; patient_number: string; name: Name; display_name: string;
  date_of_birth: DateOnly; age_years: number; sex: Sex;
  primary_phone?: string; status: 'active' | 'inactive' | 'deceased' | 'merged';
  last_visit_date?: DateOnly; has_allergies: boolean;
  paper_file_status: 'none' | 'partial' | 'complete' | 'archived';
};
```

### POST /patients/duplicate-check

Permission `patient:create`. Called by the registration form before submit. Candidates carry minimal, masked details even for callers outside the care team, because the purpose is to prevent duplicates.

```json
// request
{ "name": { "given": ["Chinedu"], "family": "Obi" }, "date_of_birth": "1984-03-12", "phone": "+2348012345678" }
```

```json
// 200
{
  "status": "success",
  "status_code": 200,
  "message": "Duplicate check completed",
  "data": {
    "candidates": [
      {
        "_id": "66e0...", "patient_number": "SMH-2025-000412", "display_name": "Chinedu Obi",
        "date_of_birth": "1984-03-12", "sex": "male", "masked_phone": "+234801***5678",
        "score": 0.94, "reasons": ["same_dob", "same_phone", "similar_name"], "last_visit_date": "2026-08-02"
      }
    ]
  }
}
```

### POST /patients

Permission `patient:create`. Supports `Idempotency-Key`.

```json
// request
{
  "name": { "given": ["Chinedu", "Emeka"], "family": "Obi" },
  "date_of_birth": "1984-03-12", "dob_approximate": false,
  "sex": "male",
  "phones": [ { "value": "08012345678", "is_primary": true } ],
  "address": { "line1": "12 Aminu Kano Crescent", "city": "Abuja", "state": "FCT", "country": "NG", "lga": "Municipal Area Council" },
  "next_of_kin": [ { "name": "Ifeoma Obi", "relationship": "spouse", "phone": "08098765432" } ],
  "registration_date": "2026-11-09",
  "payer": { "type": "insurance", "provider": "Example HMO", "member_id": "EH-778123" },
  "duplicate_override": null
}
```

```json
// 201
{
  "status": "success",
  "status_code": 201,
  "message": "Patient registered successfully",
  "data": {
    "patient": {
      "_id": "66e7...", "patient_number": "SMH-2026-000731", "status": "active",
      "name": { "given": ["Chinedu", "Emeka"], "family": "Obi" }, "display_name": "Chinedu Emeka Obi",
      "date_of_birth": "1984-03-12", "dob_approximate": false, "age_years": 42, "sex": "male",
      "phones": [ { "value": "08012345678", "normalized": "+2348012345678", "is_primary": true } ],
      "address": { "line1": "12 Aminu Kano Crescent", "city": "Abuja", "state": "FCT", "country": "NG", "lga": "Municipal Area Council" },
      "next_of_kin": [ { "name": "Ifeoma Obi", "relationship": "spouse", "phone": "08098765432" } ],
      "registration_date": "2026-11-09",
      "payer": { "type": "insurance", "provider": "Example HMO", "member_id": "EH-778123" },
      "care_team": [],
      "paper_file": { "status": "none", "pages_captured": 0 },
      "created_at": "2026-11-09T09:40:00Z", "version": 1
    }
  }
}
```

If likely duplicates exist and no override is sent, the server responds `409`:

```json
{
  "status": "error",
  "status_code": 409,
  "message": "Possible duplicate patients found",
  "data": {
    "code": "duplicates_found",
    "candidates": [ { "_id": "66e0...", "display_name": "Chinedu Obi", "score": 0.94, "reasons": ["same_dob", "same_phone"] } ],
    "request_id": "req_..."
  }
}
```

To proceed anyway, resend with `"duplicate_override": { "confirmed": true, "reason": "Different person: father and son share phone", "matched_patient_ids": ["66e0..."] }`. The override is stored on the patient and audited. `legacy_number` is accepted on create and by the CSV import, is searchable, and cannot be changed by `PATCH`.

### GET /patients

Permission `patient:read`. Care-team scope is applied. Query: `status`, `registered_from`, `registered_to`, `paper_file_status`, `sort` (`registration_date` or `name`, `-` prefix for descending), `limit`, `cursor`. For name or phone search use `/search/patients`.

```json
{
  "status": "success",
  "status_code": 200,
  "message": "Patients retrieved successfully",
  "data": {
    "patients": [ { "_id": "66e7...", "patient_number": "SMH-2026-000731", "display_name": "Chinedu Emeka Obi", "date_of_birth": "1984-03-12", "age_years": 42, "sex": "male", "primary_phone": "+2348012345678", "status": "active", "last_visit_date": null, "has_allergies": false, "paper_file_status": "none" } ],
    "pagination": { "limit": 25, "next_cursor": null, "has_more": false }
  }
}
```

### GET /patients/:id

Permission `patient:read` plus care-team. Returns `data.patient`, the full object shown in the 201 above, with an `ETag`. A `403 care_team_required` includes `"break_glass_available": true` in `data` when the caller holds `access:breakglass`. A merged patient returns `"patient": { "status": "merged", "merged_into_id": "66e7..." }`. Looking up a `patient_number` that belongs to a merged record resolves to the survivor and adds `"merged_from_number": "SMH-2025-000412"`, so a clerk working from a paper folder sees which file was matched (§2 item 15).

### PATCH /patients/:id

Header `If-Match`. Any subset of the creatable fields (not `patient_number`). Arrays are replaced as a whole.

```json
{ "phones": [ { "value": "08055550000", "is_primary": true } ], "address": { "line1": "4 Gwarinpa Avenue", "city": "Abuja", "state": "FCT", "country": "NG" } }
```

Returns `data.patient` with the message `Patient updated successfully`. The audit entry records the names of the changed fields, not their values.

### GET /patients/:id/summary

Permission `patient:read`, care-team. The prominent clinical header shown on every chart.

```json
{
  "status": "success",
  "status_code": 200,
  "message": "Patient summary retrieved successfully",
  "data": {
    "summary": {
      "patient": { "_id": "66e7...", "patient_number": "SMH-2026-000731", "display_name": "Chinedu Emeka Obi", "age_years": 42, "sex": "male" },
      "allergies": [ { "_id": "66f4...", "substance_or_condition": "Penicillin", "severity": "severe", "reaction": "Anaphylaxis", "status": "active" } ],
      "chronic_conditions": [ { "_id": "66f5...", "substance_or_condition": "Hypertension", "code": { "system": "ICD-10", "code": "I10", "display": "Essential (primary) hypertension" } } ],
      "current_medications": [ { "drug_name": "Amlodipine", "dose": "5", "dose_unit": "mg", "frequency": "once daily", "prescribed_at": "2026-08-02" } ],
      "last_vitals": { "measured_at": "2026-08-02T10:12:00Z", "systolic_mmhg": 142, "diastolic_mmhg": 90, "weight_kg": 81.5, "bmi": 27.1 },
      "last_visit": { "_id": "66d8...", "visit_date": "2026-08-02", "type": "outpatient" },
      "open_drafts": 1,
      "paper_file": { "status": "partial", "pages_captured": 14, "physical_location": "Shelf C4, folder 211" }
    }
  }
}
```

### GET /patients/:id/timeline

Permission `visit:read`. Reverse chronological. Query: `limit`, `cursor`.

```json
{
  "status": "success",
  "status_code": 200,
  "message": "Timeline retrieved successfully",
  "data": {
    "timeline": [
      {
        "visit_id": "66d8...", "visit_date": "2026-08-02", "type": "outpatient", "status": "closed",
        "attending_clinician": { "_id": "66f1...", "name": "Dr Ada Okafor" },
        "diagnoses": [ "Essential hypertension" ],
        "counts": { "documents": 2, "clinical_entries": 1, "prescriptions": 1, "vitals": 1 }
      }
    ],
    "pagination": { "limit": 25, "next_cursor": null, "has_more": false }
  }
}
```

### POST /patients/:id/care-team, DELETE /patients/:id/care-team/:user\_id

Permission `patient:update`, care-team. Adding or removing a member recomputes `clinician_ids` in the same update.

```json
// POST request
{ "user_id": "66f1...", "role": "attending" }     // role: 'attending', 'consulting' or 'nurse'
// 201
{ "status": "success", "status_code": 201, "message": "Care team member added successfully",
  "data": { "care_team_member": { "user_id": "66f1...", "name": "Dr Ada Okafor", "role": "attending", "assigned_at": "2026-11-09T09:30:00Z", "assigned_by": { "_id": "66f2...", "name": "Records Clerk" } } } }
```

Delete returns `200` with `"data": {}`. Errors: `422 user_not_clinician`, `409 already_assigned`.

### POST /patients/:id/break-glass

Permission `access:breakglass`. Grants access to this one patient only, for `break_glass_minutes`.

```json
// request
{ "reason": "Patient unconscious in emergency; need allergy history before treatment." }   // minimum 20 characters
// 201
{ "status": "success", "status_code": 201, "message": "Emergency access granted",
  "data": { "grant": { "_id": "66aa...", "patient_id": "66e7...", "expires_at": "2026-11-09T10:30:00Z" } } }
```

The use is audited with a `break_glass` flag and the hospital administrators are notified.

### POST /patients/merge

Permission `patient:merge`. Runs in one transaction. Supports `Idempotency-Key`.

```json
// request
{
  "survivor_id": "66e7...", "merged_id": "66e9...",
  "reason": "Same person registered twice at two desks",
  "field_choices": { "phones": "union", "address": "survivor", "next_of_kin": "union", "payer": "survivor" }
}
```

```json
// 200
{ "status": "success", "status_code": 200, "message": "Patients merged successfully",
  "data": { "merge": { "_id": "66b1...", "survivor_id": "66e7...", "merged_id": "66e9...", "moved": { "visits": 3, "vitals": 3, "clinical_entries": 2, "prescriptions": 1, "documents": 5, "conditions": 1 }, "merged_at": "2026-11-09T09:45:00Z" } } }
```

Errors: `422 same_patient`, `409 patient_already_merged`, `403 care_team_required`.

Inside the transaction, the merged patient's visits, vitals, clinical entries, prescriptions, conditions and documents are re-pointed to the survivor and counted in `moved`; care-team members are unioned and `clinician_ids` recomputed; the survivor's `version` is bumped once. The merged record keeps `status: 'merged'` and `merged_into_id`, its `patient_number` stays resolvable, and `patient_merges` records the retired number, the field choices and the moved counts. Both patients receive audit entries, and the override reason is stored as given. Un-merge is not supported in Release 1; because `patient_merges` stores the moved object IDs, it can be built later.

### GET /patients/:id/merge-history

```json
{ "status": "success", "status_code": 200, "message": "Merge history retrieved successfully",
  "data": { "merges": [ { "_id": "66b1...", "role": "survivor", "other_patient_number": "SMH-2026-000745", "merged_at": "2026-11-09T09:45:00Z", "merged_by": { "_id": "66f2...", "name": "Records Clerk" }, "reason": "Same person registered twice at two desks" } ] } }
```

### PATCH /patients/:id/paper-file

Permission `patient:update`. Tracks capture progress of the physical file. `pages_captured` is recomputed from documents on every write and cannot be set by the client.

```json
// request     status: 'none', 'partial', 'complete' or 'archived'
{ "status": "partial", "physical_location": "Shelf C4, folder 211" }
// 200
{ "status": "success", "status_code": 200, "message": "Paper file updated successfully",
  "data": { "paper_file": { "status": "partial", "physical_location": "Shelf C4, folder 211", "pages_captured": 14, "last_captured_at": "2026-11-09T09:20:00Z" } } }
```

`pages_captured` is computed from documents and cannot be set.

### GET /patients/:id/conditions, POST /patients/:id/conditions

Permission `clinical:read` for GET and `clinical:write` for POST. Query for GET: `kind` (`allergy`, `chronic_condition`, `other_alert`), `status`. GET returns `data.conditions`.

```json
// POST request
{ "kind": "allergy", "substance_or_condition": "Penicillin", "severity": "severe", "reaction": "Anaphylaxis", "onset_date": "2015-06-01" }
// 201
{ "status": "success", "status_code": 201, "message": "Condition recorded successfully",
  "data": { "condition": { "_id": "66f4...", "kind": "allergy", "substance_or_condition": "Penicillin", "severity": "severe", "reaction": "Anaphylaxis", "status": "active", "source": "typed", "recorded_by": { "_id": "66f1...", "name": "Dr Ada Okafor" }, "recorded_at": "2026-11-09T09:50:00Z" } } }
```

### PATCH /patients/:id/conditions/:condition\_id

```json
{ "status": "resolved" }                                         // 'active' or 'resolved'
{ "status": "entered_in_error", "reason": "Recorded for the wrong patient" }
```

Returns `data.condition`. The record remains visible in history with its new status.

## 20. Visits, Vitals, Clinical Entries, Prescriptions

### POST /patients/:id/visits

Permission `visit:create`, care-team. Supports `Idempotency-Key`.

```json
// request
{ "type": "outpatient", "visit_date": "2026-11-09", "attending_clinician_id": "66f1...", "reason_for_visit": "Headache and fever for 3 days" }
// 201
{
  "status": "success",
  "status_code": 201,
  "message": "Visit created successfully",
  "data": {
    "visit": {
      "_id": "66d9...", "patient_id": "66e7...", "type": "outpatient", "visit_date": "2026-11-09", "status": "open",
      "attending_clinician": { "_id": "66f1...", "name": "Dr Ada Okafor" }, "reason_for_visit": "Headache and fever for 3 days"
    }
  }
}
```

Errors: `422 unknown_visit_type`, `422 attending_not_clinician`. Creating a visit adds the attending clinician to the patient's visible clinicians for care-team purposes.

### GET /patients/:id/visits, GET /visits/:id

The list returns `data.visits` (objects like the 201 above) with `data.pagination`. The single-visit response includes everything attached to the visit:

```json
{
  "status": "success",
  "status_code": 200,
  "message": "Visit retrieved successfully",
  "data": {
    "visit": {
      "_id": "66d9...", "patient_id": "66e7...", "type": "outpatient", "visit_date": "2026-11-09", "status": "open",
      "attending_clinician": { "_id": "66f1...", "name": "Dr Ada Okafor" },
      "vitals": [ { "_id": "66v1...", "measured_at": "2026-11-09T09:55:00Z", "temperature_c": 38.4, "pulse_bpm": 98, "systolic_mmhg": 128, "diastolic_mmhg": 82, "weight_kg": 81.0, "height_cm": 175, "bmi": 26.5 } ],
      "clinical_entries": [ { "entry_id": "66ce...", "version": 1, "status": "draft", "kind": "encounter_note", "author": { "_id": "66f1...", "name": "Dr Ada Okafor" } } ],
      "prescriptions": [ { "_id": "66pr...", "status": "draft", "item_count": 2 } ],
      "documents": [ { "_id": "66dc...", "type": "case_note", "page_count": 2, "status": "ready", "extraction_status": "needs_review" } ]
    }
  }
}
```

### PATCH /visits/:id

Permission `visit:update`, care-team. Header `If-Match`. Changing the attending clinician recomputes `clinician_ids` and is audited on the patient.

```json
// request
{ "type": "emergency", "reason_for_visit": "Updated reason", "attending_clinician_id": "66f3..." }
```

Returns `data.visit` with the message `Visit updated successfully`.

### POST /visits/:id/close

Permission `visit:close`, care-team. Requires `If-Match`. With `force: true` the visit closes despite unsigned drafts, and the audit entry lists the drafts that were open.

```json
// request
{ "force": false }
// 200
{ "status": "success", "status_code": 200, "message": "Visit closed successfully",
  "data": { "visit": { "_id": "66d9...", "status": "closed", "closed_at": "2026-11-09T12:10:00Z" } } }
// 409 when drafts exist and force is false
{ "status": "error", "status_code": 409, "message": "Visit has unsigned drafts",
  "data": { "code": "unsigned_drafts_exist", "drafts": [ { "type": "clinical_entry", "_id": "66ce..." }, { "type": "prescription", "_id": "66pr..." } ], "request_id": "req_..." } }
```

### POST /visits/:id/vitals

Permission `vitals:write`. All measurements are optional, but at least one is required. BMI is computed by the server when weight and height are both present (and from the patient's latest height if only weight is sent).

```json
// request
{ "measured_at": "2026-11-09T09:55:00Z", "temperature_c": 38.4, "pulse_bpm": 98, "systolic_mmhg": 128, "diastolic_mmhg": 82, "weight_kg": 81.0, "height_cm": 175, "acknowledge_warnings": false }
```

```json
// 201
{ "status": "success", "status_code": 201, "message": "Vitals recorded successfully",
  "data": { "vitals": { "_id": "66v1...", "measured_at": "2026-11-09T09:55:00Z", "temperature_c": 38.4, "pulse_bpm": 98, "systolic_mmhg": 128, "diastolic_mmhg": 82, "weight_kg": 81.0, "height_cm": 175, "bmi": 26.5, "warnings": [] } } }
```

Out-of-range values (plausible but unusual, for example a temperature of 41.5) are not saved on the first attempt:

```json
// 409
{
  "status": "error",
  "status_code": 409,
  "message": "Please confirm unusual values",
  "data": {
    "code": "vitals_confirmation_required",
    "warnings": [ { "field": "temperature_c", "value": 41.5, "message": "Above expected range (34.0 to 41.0)." } ],
    "request_id": "req_..."
  }
}
```

Resend the same body with `"acknowledge_warnings": true` to save; the warnings are stored on the record. Physically impossible values (for example a pulse of 900) return `422 vitals_invalid`.

### GET /patients/:id/vitals

Permission `visit:read`, care-team. Query: `from`, `to` (timestamps), `visit_id`, `limit`, `cursor`. Returns `data.vitals` (objects as above, newest first) and `data.pagination`.

### GET /patients/:id/vitals/trends

Query: `metric` (`temperature_c`, `pulse_bpm`, `systolic_mmhg`, `diastolic_mmhg`, `weight_kg`, `height_cm`, `bmi`), `from`, `to`.

```json
{
  "status": "success",
  "status_code": 200,
  "message": "Vitals trend retrieved successfully",
  "data": {
    "trend": {
      "metric": "weight_kg", "unit": "kg",
      "points": [ { "measured_at": "2026-02-10T09:00:00Z", "value": 84.2, "visit_id": "66d1..." }, { "measured_at": "2026-08-02T10:12:00Z", "value": 81.5, "visit_id": "66d8..." }, { "measured_at": "2026-11-09T09:55:00Z", "value": 81.0, "visit_id": "66d9..." } ],
      "reference_range": null
    }
  }
}
```

### PATCH /vitals/:id

Vitals are corrected by marking the record in error and posting a new one:

```json
{ "status": "entered_in_error", "reason": "Weight recorded in pounds by mistake" }
```

Returns `data.vitals` with the message `Vitals marked as entered in error`.

### POST /visits/:id/clinical-entries

Permission `clinical:write`. Creates a draft. Supports `Idempotency-Key`. In clinical entries `entry_id` is the identifier that stays the same across versions and is the one used in paths.

```json
// request
{
  "kind": "encounter_note",
  "content": {
    "presenting_complaint": "Headache and fever for 3 days",
    "history": "Intermittent fever, no recent travel. No known sick contacts.",
    "examination": "Febrile, no neck stiffness. Chest clear.",
    "observations": "Temp 38.4 C, pulse 98.",
    "diagnoses": [ { "text": "Uncomplicated malaria", "code": { "system": "ICD-10", "code": "B50.9", "display": "Plasmodium falciparum malaria, unspecified" }, "type": "primary" } ],
    "plan": "Antimalarial course, hydration, return if worse.",
    "follow_up": "Review in 1 week"
  }
}
```

```json
// 201
{
  "status": "success",
  "status_code": 201,
  "message": "Clinical entry saved as draft",
  "data": {
    "clinical_entry": {
      "entry_id": "66ce...", "version": 1, "is_current": false, "status": "draft",
      "patient_id": "66e7...", "visit_id": "66d9...", "kind": "encounter_note",
      "content": { "presenting_complaint": "Headache and fever for 3 days", "...": "..." },
      "provenance": { "content.presenting_complaint": "typed", "content.history": "typed", "content.plan": "typed" },
      "author": { "_id": "66f1...", "name": "Dr Ada Okafor" }, "authored_at": "2026-11-09T10:05:00Z"
    }
  }
}
```

### GET /patients/:id/clinical-entries

Permission `clinical:read`, care-team. Query `visit_id`, `kind`, `status`, `limit`, `cursor`. Returns `data.clinical_entries`, each with `entry_id`, `version`, `status`, `is_current`, `kind`, `author`, `authored_at` and `signed_at`, newest first. Chart rendering needs this list; without it every entry would have to be fetched by id.

### GET /clinical-entries/:entry\_id

Returns `data.clinical_entry`: the current signed version, or the draft if none is signed yet. Query `version=2` returns a specific version. Includes `source_document_id` and `extraction_job_id` when the entry originated from AI extraction.

### PATCH /clinical-entries/:entry\_id

Header `If-Match`. Partial update of `content`. On a draft, edits happen in place. On a signed entry, the call creates the next draft version and returns it.

```json
{ "content": { "plan": "Antimalarial course, hydration, return if worse. Paracetamol for fever." }, "change_reason": "Added symptomatic treatment" }
```

`change_reason` is required when amending a signed entry. The response is `data.clinical_entry` (the new or updated version) with `status: "draft"`.

### POST /clinical-entries/:entry\_id/sign

Permission `clinical:sign`. Only the author may sign.

```json
// request
{}
// 200
{ "status": "success", "status_code": 200, "message": "Clinical entry signed successfully",
  "data": { "clinical_entry": { "entry_id": "66ce...", "version": 1, "status": "signed", "is_current": true, "signed_by": { "_id": "66f1...", "name": "Dr Ada Okafor" }, "signed_at": "2026-11-09T10:20:00Z" } } }
```

Errors: `403 author_only`, `409 already_signed`, `422 empty_entry`.

### GET /clinical-entries/:entry\_id/versions

```json
{
  "status": "success",
  "status_code": 200,
  "message": "Entry history retrieved successfully",
  "data": {
    "versions": [
      { "version": 2, "status": "signed", "is_current": true, "signed_at": "2026-11-09T14:00:00Z", "change_reason": "Added symptomatic treatment", "changed_fields": ["content.plan"] },
      { "version": 1, "status": "signed", "is_current": false, "signed_at": "2026-11-09T10:20:00Z", "change_reason": null, "changed_fields": [] }
    ]
  }
}
```

Fetch a version's full content with `GET /clinical-entries/:entry_id?version=1`.

### POST /clinical-entries/:entry\_id/mark-error

Permission `clinical:sign`.

```json
// request
{ "reason": "Entered on the wrong patient" }
```

Response `200` with `data.clinical_entry` at `status: "entered_in_error"` and the message `Clinical entry marked as entered in error`. It stays visible in history and is hidden from default chart views.

### GET /patients/:id/prescriptions

Permission `clinical:read`, care-team. Query `visit_id`, `status`, `from`, `to`, `limit`, `cursor`. Returns `data.prescriptions` with `prescription_id`, `version`, `status`, `is_current`, `item_count`, `prescriber` and `signed_at`, newest first.

### POST /visits/:id/prescriptions, PATCH /prescriptions/:id, POST /prescriptions/:id/sign

Permission `clinical:write` (sign needs `clinical:sign`). Same draft and sign rules as clinical entries. Allergy conflicts are checked on save and returned as warnings.

```json
// POST request
{
  "clinical_entry_id": "66ce...",
  "items": [
    { "drug_name": "Artemether/Lumefantrine", "dose": "80/480", "dose_unit": "mg", "route": "oral", "frequency": "twice daily", "duration": "3 days", "instructions": "Take with food" },
    { "drug_name": "Paracetamol", "dose": "1000", "dose_unit": "mg", "route": "oral", "frequency": "every 8 hours as needed", "duration": "3 days" }
  ],
  "acknowledge_warnings": false
}
```

```json
// 201
{
  "status": "success",
  "status_code": 201,
  "message": "Prescription saved as draft",
  "data": {
    "prescription": {
      "_id": "66pr...", "status": "draft", "visit_id": "66d9...", "prescriber": { "_id": "66f1...", "name": "Dr Ada Okafor" },
      "items": [ { "drug_name": "Artemether/Lumefantrine", "dose": "80/480", "dose_unit": "mg", "route": "oral", "frequency": "twice daily", "duration": "3 days", "instructions": "Take with food", "source": "typed" } ],
      "warnings": [ { "type": "allergy_match", "item": 0, "message": "Patient has a recorded allergy that may match this drug." } ]
    }
  }
}
```

If warnings exist and `acknowledge_warnings` is false, the save returns `409 prescription_warnings` with the same `warnings` array in `data`; the clinician resends with `true` to proceed. Signing returns `data.prescription` with `status: "signed"` and `signed_at`, and the message `Prescription signed successfully`.

### GET /prescriptions/:id, POST /prescriptions/:id/mark-error

Permission `clinical:read` for GET and `clinical:sign` for mark-error. GET accepts `version` and returns `data.prescription` for that version (the current one by default). Mark-error takes `{ "reason": "..." }`, sets `status: "entered_in_error"`, clears `is_current`, and the record stays visible in history.

### GET /terminology/icd10

Query: `q` (code or text), `limit` (default 10).

```json
{ "status": "success", "status_code": 200, "message": "Codes retrieved successfully",
  "data": { "codes": [ { "code": "B50.9", "display": "Plasmodium falciparum malaria, unspecified" }, { "code": "I10", "display": "Essential (primary) hypertension" } ] } }
```

## 21. Documents

The capture flow is designed for unreliable connections. The client generates a `client_capture_id` (UUID v4) once per capture, and every call below can be repeated safely.

Typical sequence: `POST /documents`, then for each page upload to the returned URL, then `POST .../complete` per page, then `POST /documents/:id/finalize`, then wait for `document.ready` on the event stream (or poll `GET /documents/:id`).

### POST /documents

Permission `document:capture`, care-team. Allowed MIME types: `image/jpeg`, `image/png`, `image/webp`, `image/heic`.

```json
// request
{
  "client_capture_id": "b6f0f2d4-7a77-4c5e-9d1f-0a7a3f1c2e11",
  "patient_id": "66e7...",
  "visit_id": "66d9...",
  "visit_date": "2026-11-09",
  "type": "case_note",
  "is_handwritten": true,
  "notes": "MD's note from ward round",
  "pages": [
    { "page_no": 1, "mime": "image/jpeg", "bytes": 2483921, "sha256": "e3b0c44298fc1c149afbf4c8996fb924..." },
    { "page_no": 2, "mime": "image/jpeg", "bytes": 2210044, "sha256": "9a271f2a916b0b6ee6cecb2426f0b320..." }
  ]
}
```

```json
// 201 (200 with header Idempotent-Replayed: true if client_capture_id already exists)
{
  "status": "success",
  "status_code": 201,
  "message": "Document capture started",
  "data": {
    "document": {
      "_id": "66dc...", "client_capture_id": "b6f0f2d4-7a77-4c5e-9d1f-0a7a3f1c2e11", "status": "initiated",
      "patient_id": "66e7...", "visit_id": "66d9...", "visit_date": "2026-11-09", "type": "case_note", "is_handwritten": true, "page_count": 2,
      "pages": [
        { "page_no": 1, "uploaded": false, "upload_mode": "single",
          "upload": { "url": "https://storage.example/t/66a0.../pending/66dc.../1/original?X-Sig=...", "method": "PUT", "headers": { "Content-Type": "image/jpeg" }, "expires_at": "2026-11-09T10:20:00Z" } },
        { "page_no": 2, "uploaded": false, "upload_mode": "multipart",
          "multipart": { "upload_id": "mp_8d21...", "part_size": 5242880, "part_count": 1 } }
      ]
    }
  }
}
```

Files up to the configured single-upload limit (about 5 MB) use a single presigned `PUT`. Larger files use multipart mode, with parts requested through the endpoint below.

Errors: `404 patient_not_found`, `422 unknown_document_type`, `422 too_many_pages`, `413 page_too_large`, `415 unsupported_media_type`, `409 client_capture_id_conflict` (the ID exists with a different payload).

### POST /documents/:id/pages

Adds a page to a document that is still `initiated` or `uploading`, for the common case of a missed shot after the capture was created. Body `{ "page_no": 3, "mime": "image/jpeg", "bytes": 1902233, "sha256": "..." }`; returns `data.document` with the new `page_count` and that page's upload instructions. Errors: `409 document_processing` once processing has begun, `422 page_no_exists`.

### POST /documents/:id/pages/:page\_no/upload-url

Issues a fresh upload URL if the first one expired. Request body is empty.

```json
{ "status": "success", "status_code": 200, "message": "Upload URL generated successfully",
  "data": { "upload": { "upload_mode": "single", "url": "https://storage.example/...", "method": "PUT", "headers": { "Content-Type": "image/jpeg" }, "expires_at": "2026-11-09T10:35:00Z" } } }
```

### POST /documents/:id/pages/:page\_no/multipart/parts

Returns presigned URLs for the parts the client wants to send or resend.

```json
// request
{ "part_numbers": [1, 2, 3] }
// 200
{ "status": "success", "status_code": 200, "message": "Part URLs generated successfully",
  "data": { "upload_id": "mp_8d21...", "parts": [ { "part_number": 1, "url": "https://storage.example/...", "expires_at": "2026-11-09T10:35:00Z" }, { "part_number": 2, "url": "https://storage.example/...", "expires_at": "2026-11-09T10:35:00Z" } ] } }
```

### POST /documents/:id/pages/:page\_no/complete

Verifies the uploaded object (size, checksum, magic bytes) and queues processing for that page.

```json
// request: single upload
{}
// request: multipart upload
{ "parts": [ { "part_number": 1, "etag": "\"a54357aff0632cce46d942af68356b38\"" }, { "part_number": 2, "etag": "\"0c78aef83f66abc1fa1e8477f296d394\"" } ] }
```

```json
// 200
{ "status": "success", "status_code": 200, "message": "Page uploaded successfully",
  "data": { "page": { "page_no": 1, "status": "uploaded", "bytes": 2483921 } } }
```

Errors: `409 object_not_found` (nothing uploaded yet), `422 checksum_mismatch`, `422 invalid_image` (content is not a valid image).

### POST /documents/:id/finalize

```json
// request
{}
// 200
{ "status": "success", "status_code": 200, "message": "Document is being processed",
  "data": { "document": { "_id": "66dc...", "status": "processing", "page_count": 2, "pages_ready": 0 } } }
// 409 when pages are missing
{ "status": "error", "status_code": 409, "message": "Not all pages are uploaded",
  "data": { "code": "pages_missing", "missing_pages": [2], "request_id": "req_..." } }
```

### GET /documents/:id/upload-status

Used by the client to resume after a dropped connection.

```json
{ "status": "success", "status_code": 200, "message": "Upload status retrieved successfully",
  "data": { "upload_status": { "status": "uploading", "pages": [ { "page_no": 1, "uploaded": true }, { "page_no": 2, "uploaded": false, "upload_mode": "multipart", "multipart": { "upload_id": "mp_8d21...", "completed_parts": [1, 2] } } ] } } }
```

### GET /documents/:id

Permission `document:read`, care-team.

```json
{
  "status": "success",
  "status_code": 200,
  "message": "Document retrieved successfully",
  "data": {
    "document": {
      "_id": "66dc...", "status": "ready", "patient_id": "66e7...", "visit_id": "66d9...", "visit_date": "2026-11-09",
      "type": "case_note", "is_handwritten": true, "notes": "MD's note from ward round", "page_count": 2,
      "pages": [
        { "page_no": 1, "width": 3024, "height": 4032, "bytes": 2483921, "processing_status": "ready", "quality": { "blur_score": 182.4, "mean_brightness": 141, "flags": [] } },
        { "page_no": 2, "width": 3024, "height": 4032, "bytes": 2210044, "processing_status": "ready", "quality": { "blur_score": 41.7, "mean_brightness": 118, "flags": ["blurry"] } }
      ],
      "extraction": { "latest_job_id": "66ex...", "status": "needs_review" },
      "captured_by": { "_id": "66f2...", "name": "Records Clerk" }, "captured_at": "2026-11-09T10:10:00Z"
    }
  }
}
```

`quality.flags` can contain `blurry`, `dark`, `low_resolution`. The client uses them to offer a retake before the capture is finalized, and the server returns them as soon as a page is `complete`. A page whose enhancement failed returns `"processing_status": "enhancement_failed"` with an `enhancement_error` object, and the original is still readable.

### GET /documents/:id/pages/:page\_no/url

Query: `variant` (`original`, `enhanced`, `thumb`; default `enhanced`, falling back to `original` if no enhanced copy exists). Each call is audited.

```json
{ "status": "success", "status_code": 200, "message": "Image URL generated successfully",
  "data": { "image": { "url": "https://storage.example/t/66a0.../patients/66e7.../documents/66dc.../1/enhanced.jpg?X-Sig=...", "variant": "enhanced", "mime": "image/jpeg", "expires_at": "2026-11-09T10:12:30Z", "fallback": false } } }
```

The URL is valid for 60 to 120 seconds. Fetch a new one when it expires; do not cache it.

### PATCH /documents/:id

Permission `document:tag`. Retagging to another patient is allowed with `document:tag` and is audited on both patients.

```json
{ "patient_id": "66e8...", "visit_id": null, "visit_date": "2026-11-08", "type": "referral", "is_handwritten": false, "notes": "Corrected patient" }
```

Returns `data.document` with the message `Document updated successfully`. Error: `409 extraction_in_progress` if retagging the patient while an extraction is awaiting review (resolve it first).

### DELETE /documents/:id

Permission `document:delete`. Query `reason` is required. Soft delete; originals stay in storage until the retention policy allows purging.

```json
{ "status": "success", "status_code": 200, "message": "Document deleted successfully", "data": {} }
```

### GET /patients/:id/documents

Query: `type`, `from`, `to`, `visit_id`, `handwritten`, `limit`, `cursor`.

```json
{
  "status": "success",
  "status_code": 200,
  "message": "Documents retrieved successfully",
  "data": {
    "documents": [ { "_id": "66dc...", "type": "case_note", "visit_date": "2026-11-09", "visit_id": "66d9...", "page_count": 2, "status": "ready", "is_handwritten": true, "extraction_status": "needs_review", "thumb_url_endpoint": "/documents/66dc.../pages/1/url?variant=thumb" } ],
    "pagination": { "limit": 25, "next_cursor": null, "has_more": false }
  }
}
```

### POST /documents/batch (Release 2)

For a records clerk photographing many files. Creates many documents in one call; each upload then follows the normal per-page flow. The response is `207` with `status: "success"`, and each item reports its own outcome.

```json
// request
{ "items": [ { "client_capture_id": "...", "patient_id": "66e7...", "visit_date": "2026-02-10", "type": "case_note", "is_handwritten": true, "pages": [ { "page_no": 1, "mime": "image/jpeg", "bytes": 2100000, "sha256": "..." } ] } ] }
// 207
{ "status": "success", "status_code": 207, "message": "Batch processed",
  "data": { "results": [ { "client_capture_id": "...", "outcome": "created", "document": { "_id": "66dd...", "pages": [ { "page_no": 1, "upload": { "url": "...", "method": "PUT", "headers": {}, "expires_at": "..." } } ] } }, { "client_capture_id": "...", "outcome": "error", "error": { "code": "patient_not_found" } } ] } }
```

## 22. AI Extraction

Field keys in extraction results are the snake\_case form of the target schema in section 10.3 (for example `presenting_complaint`, `follow_up`).

### POST /documents/:id/extractions

Permission `extraction:request`. Supports `Idempotency-Key`.

```json
// request     both fields optional; defaults to all pages and the enhanced copy
{ "pages": [1, 2], "use_enhanced": true }
// 202
{ "status": "success", "status_code": 202, "message": "Extraction queued",
  "data": { "extraction": { "_id": "66ex...", "document_id": "66dc...", "status": "queued", "page_count": 2, "queued_at": "2026-11-09T10:15:00Z" } } }
```

Errors: `403 ai_not_enabled`, `422 document_not_handwritten`, `422 document_not_ready`, `409 extraction_in_progress`, `429 ai_monthly_cap_reached`, `503 ai_unavailable` (circuit breaker open), `422 too_many_pages`.

### GET /extractions/:id

Permission `extraction:review`. Statuses: `queued`, `processing`, `needs_review`, `approved`, `rejected`, `failed`, `cancelled`. Images are fetched separately through the document URL endpoint; no image URLs are embedded here.

```json
{
  "status": "success",
  "status_code": 200,
  "message": "Extraction retrieved successfully",
  "data": {
    "extraction": {
      "_id": "66ex...", "document_id": "66dc...", "patient_id": "66e7...", "visit_id": "66d9...", "status": "needs_review",
      "schema_version": "1", "pages": [1, 2], "queued_at": "2026-11-09T10:15:00Z", "completed_at": "2026-11-09T10:15:42Z",
      "transcription": "c/o headache + fever x 3/7. No neck stiffness. T 38.4. ? malaria. Rx ACT x 3 days...",
      "fields": [
        { "key": "presenting_complaint", "value": "Headache and fever for 3 days", "confidence": 0.93, "level": "high", "flagged": false, "unreadable": false, "requires_confirmation": false },
        { "key": "examination", "value": "Febrile. No neck stiffness.", "confidence": 0.88, "level": "high", "flagged": false, "unreadable": false, "requires_confirmation": false },
        { "key": "diagnoses", "value": [ { "text": "? malaria", "icd10": null } ], "confidence": 0.61, "level": "low", "flagged": true, "unreadable": false, "requires_confirmation": false },
        { "key": "medications", "value": [ { "drug_name": "ACT", "dose": null, "dose_unit": null, "route": "oral", "frequency": null, "duration": "3 days" } ], "confidence": 0.58, "level": "low", "flagged": true, "unreadable": false, "requires_confirmation": true },
        { "key": "follow_up", "value": null, "confidence": null, "level": null, "flagged": true, "unreadable": true, "requires_confirmation": false }
      ],
      "validation": { "valid": true, "errors": [] },
      "warnings": [ { "code": "note_date_mismatch", "message": "Date written on the note (2026-11-07) differs from the visit date." } ],
      "draft": { "edited_fields": {}, "saved_at": null },
      "review": null,
      "usage": { "input_tokens": 4120, "output_tokens": 560 }
    }
  }
}
```

While `queued` or `processing` the extraction carries no `fields`. For `failed`, an `error` object is present: `{ "code": "provider_timeout", "message": "...", "retryable": true }`.

### GET /extractions

Query: `status` (default `needs_review`), `document_id`, `patient_id`, `requested_by=me`, `limit`, `cursor`.

```json
{
  "status": "success",
  "status_code": 200,
  "message": "Extractions retrieved successfully",
  "data": {
    "extractions": [ { "_id": "66ex...", "status": "needs_review", "document_id": "66dc...", "patient": { "_id": "66e7...", "display_name": "Chinedu Emeka Obi", "patient_number": "SMH-2026-000731" }, "flagged_field_count": 3, "queued_at": "2026-11-09T10:15:00Z" } ],
    "pagination": { "limit": 25, "next_cursor": null, "has_more": false }
  }
}
```

### PATCH /extractions/:id/draft

Auto-saves reviewer edits without approving. Header `If-Match`. Only allowed in `needs_review`.

```json
// request
{ "fields": { "presenting_complaint": { "value": "Headache and fever for 3 days" }, "follow_up": { "value": "Review in 1 week" } } }
// 200
{ "status": "success", "status_code": 200, "message": "Draft saved",
  "data": { "extraction": { "_id": "66ex...", "draft": { "edited_fields": { "presenting_complaint": "Headache and fever for 3 days", "follow_up": "Review in 1 week" }, "saved_at": "2026-11-09T10:25:00Z" } } } }
```

### POST /extractions/:id/approve

Permission `extraction:review` and `clinical:write`. Creates draft records in one transaction; nothing is signed. Supports `Idempotency-Key`.

```json
// request
{
  "fields": {
    "presenting_complaint": "Headache and fever for 3 days",
    "history": null,
    "examination": "Febrile. No neck stiffness.",
    "observations": "Temp 38.4 C",
    "diagnoses": [ { "text": "Uncomplicated malaria", "code": { "system": "ICD-10", "code": "B50.9", "display": "Plasmodium falciparum malaria, unspecified" }, "type": "primary" } ],
    "plan": "Antimalarial course, hydration",
    "follow_up": "Review in 1 week",
    "medications": [ { "drug_name": "Artemether/Lumefantrine", "dose": "80/480", "dose_unit": "mg", "route": "oral", "frequency": "twice daily", "duration": "3 days" } ]
  },
  "medications_confirmed": true,
  "conditions": [ { "kind": "allergy", "substance_or_condition": "Penicillin" } ],
  "notes": "Checked against the original page 2"
}
```

```json
// 200
{
  "status": "success",
  "status_code": 200,
  "message": "Extraction approved. Draft records created for signing",
  "data": {
    "extraction": {
      "_id": "66ex...", "status": "approved",
      "resulting_entry_id": "66ce...", "resulting_prescription_id": "66pr...", "resulting_condition_ids": ["66f4..."],
      "provenance": {
        "content.presenting_complaint": "ai_extracted", "content.examination": "ai_extracted",
        "content.diagnoses": "ai_extracted_edited", "content.follow_up": "typed", "prescription.items[0]": "ai_extracted_edited"
      },
      "reviewed_by": { "_id": "66f2...", "name": "Records Clerk" }, "reviewed_at": "2026-11-09T10:30:00Z"
    }
  }
}
```

The server computes provenance by comparing each submitted value to the extracted value. The created clinical entry and prescription are drafts; the clinician still signs them (section 20).

Errors: `422 medications_not_confirmed` (medications present and `medications_confirmed` is not true), `409 not_reviewable` (the job is not in `needs_review`), `422 validation_failed`, `403 forbidden`.

### POST /extractions/:id/reject

```json
// request     reason: 'illegible', 'wrong_document', 'poor_quality' or 'other'
{ "reason": "illegible", "notes": "Page 2 too blurry; retake photo" }
// 200
{ "status": "success", "status_code": 200, "message": "Extraction rejected",
  "data": { "extraction": { "_id": "66ex...", "status": "rejected" } } }
```

No structured data is created; the image stays attached to the patient and visit.

### POST /extractions/:id/retry

Permission `extraction:request`. Allowed for `failed`, `rejected` or `needs_review` jobs. Request body is empty. Response `202` with `data.extraction` for a new job, in the same shape as when requesting an extraction, and the message `Extraction queued`.

### POST /extractions/:id/cancel

Permission `extraction:request` for one's own job, or `extraction:review` for any. Allowed in `queued` and `processing`; a job that has reached `needs_review` must be rejected instead. Returns `202` with `data.extraction` at `status: "cancelled"`. The worker re-checks the job status before calling the provider and drops a cancelled job, so cancelling during processing cannot create records.

### POST /events/ticket

Exchanges a normal access token for a single-use stream ticket: `{ "ticket": "ev_3f2a...", "expires_in": 60 }`. Needed because `EventSource` cannot send an `Authorization` header; browser clients may instead rely on the `HttpOnly` session cookie.

### GET /events (server-sent events)

A long-lived stream for the signed-in user, authenticated by the `HttpOnly` session cookie or a ticket from `POST /events/ticket`. It is the one response that does not use the envelope, because it is an event stream. The worker publishes to Redis and every API instance relays to its own subscribers, so the client does not need to know which instance processed the job (§12). Each event's `data` is a JSON object in snake\_case:

```
event: document.page_ready
data: { "document_id": "66dc...", "page_no": 1, "quality": { "flags": [] } }

event: document.ready
data: { "document_id": "66dc..." }

event: extraction.needs_review
data: { "extraction_id": "66ex...", "document_id": "66dc...", "patient_id": "66e7..." }

event: extraction.failed
data: { "extraction_id": "66ex...", "code": "provider_timeout", "retryable": true }

event: session.revoked
data: { "reason": "admin" }
```

Events carry identifiers only, never clinical content. The client sends `Last-Event-ID` on reconnect to replay from the tenant's capped stream, which holds the last 15 minutes; older gaps are not replayed, so the client must also re-fetch state on reconnect. A `session.revoked` event means the stream will close and the client must re-authenticate.

## 23. Search

### GET /search/patients

Permission `patient:read`. At least one of `q`, `dob`, `phone`, `patient_number` is required. Results are limited to patients the caller may see. Name matching tolerates spelling variations.

Query: `q` (minimum 2 characters), `dob` (`YYYY-MM-DD`), `phone`, `patient_number`, `limit` (maximum 25).

```json
// GET /search/patients?q=chinedu%20obi&dob=1984-03-12
{
  "status": "success",
  "status_code": 200,
  "message": "Search completed successfully",
  "data": {
    "results": [
      {
        "patient": { "_id": "66e7...", "patient_number": "SMH-2026-000731", "display_name": "Chinedu Emeka Obi", "date_of_birth": "1984-03-12", "age_years": 42, "sex": "male", "primary_phone": "+2348012345678", "has_allergies": true, "paper_file_status": "partial" },
        "score": 0.97, "matched_on": ["name", "dob"]
      },
      {
        "patient": { "_id": "66e0...", "patient_number": "SMH-2025-000412", "display_name": "Chinedu Obi", "date_of_birth": "1984-03-12", "age_years": 42, "sex": "male", "primary_phone": "+2348012345678", "has_allergies": false, "paper_file_status": "none" },
        "score": 0.91, "matched_on": ["name", "dob"]
      }
    ],
    "pagination": { "limit": 25, "next_cursor": null, "has_more": false }
  }
}
```

Errors: `422 query_required`, `422 query_too_short`, `429 rate_limited`.

### GET /search/documents

Permission `document:read`. Query: `patient_id`, `type`, `visit_id`, `from`, `to`, `handwritten`, `extraction_status`, `limit`, `cursor`. Returns `data.documents` (the same items as `GET /patients/:id/documents`, plus `patient` with `_id` and `display_name`) and `data.pagination`.

### GET /search/suggest

Query `q` (minimum 2 characters). Returns at most 8 quick matches for type-ahead.

```json
{ "status": "success", "status_code": 200, "message": "Suggestions retrieved successfully",
  "data": { "suggestions": [ { "_id": "66e7...", "display_name": "Chinedu Emeka Obi", "patient_number": "SMH-2026-000731", "date_of_birth": "1984-03-12" } ] } }
```

## 24. Audit, Imports and Exports

### GET /audit-logs

Permission `audit:read`. Query: `actor_id`, `patient_id`, `action`, `outcome` (`success`, `denied`, `error`), `break_glass=true`, `from`, `to`, `limit`, `cursor`.

```json
{
  "status": "success",
  "status_code": 200,
  "message": "Audit logs retrieved successfully",
  "data": {
    "audit_logs": [
      {
        "_id": "66au...", "seq": 128431, "at": "2026-11-09T09:58:12Z",
        "actor": { "_id": "66f1...", "name": "Dr Ada Okafor", "role": "Doctor" },
        "action": "patient.view", "resource_type": "patient", "resource_id": "66e7...", "patient_id": "66e7...",
        "outcome": "success", "break_glass": false,
        "ip": "102.89.x.x", "device_label": "Pixel 8, Chrome", "request_id": "req_8f2c1a",
        "changed_fields": [], "reason": null
      },
      {
        "_id": "66av...", "seq": 128440, "at": "2026-11-09T10:03:40Z",
        "actor": { "_id": "66f1...", "name": "Dr Ada Okafor", "role": "Doctor" },
        "action": "breakglass.use", "resource_type": "patient", "resource_id": "66e9...", "patient_id": "66e9...",
        "outcome": "success", "break_glass": true, "changed_fields": [], "reason": "Patient unconscious in emergency; need allergy history before treatment."
      }
    ],
    "pagination": { "limit": 50, "next_cursor": "eyJzZXEiOjEyODQ0MH0=", "has_more": true }
  }
}
```

Common `action` values: `auth.login`, `auth.login_failed`, `patient.view`, `patient.create`, `patient.update`, `patient.merge`, `document.view`, `document.create`, `clinical_entry.sign`, `extraction.approve`, `export.create`, `breakglass.use`, `user.role_change`, `tenant.config_change`.

### POST /audit-logs/exports

Permission `audit:export`. Asynchronous; the export itself is audited. Poll `GET /exports/:id` for completion.

```json
// request
{ "from": "2026-10-01", "to": "2026-10-31", "filters": { "break_glass": true }, "format": "csv" }
// 202
{ "status": "success", "status_code": 202, "message": "Export queued",
  "data": { "export": { "_id": "66dj...", "kind": "audit_export", "status": "queued" } } }
```

### GET /audit-logs/verify

Permission `audit:read`. Query: `from`, `to`. Verifies the hash chain for the range.

```json
{ "status": "success", "status_code": 200, "message": "Audit log verified",
  "data": { "verification": { "from": "2026-10-01", "to": "2026-10-31", "entries_checked": 412880, "chain_lag_seconds": 3, "valid": true, "first_seq": 100001, "last_seq": 512880, "broken_at_seq": null, "external_anchor_matched": true } } }
```

### POST /imports/patients

Permission `user:manage` (or a dedicated `patient:import` permission if the tenant prefers). Imports are a three-step flow: create the job, upload the CSV, start.

```json
// request
{
  "filename": "patients.csv", "bytes": 482113, "dry_run": true,
  "mapping": { "Surname": "name.family", "First name": "name.given[0]", "DOB": "date_of_birth", "Sex": "sex", "Mobile": "phones[0].value", "File No": "legacy_number" },
  "options": { "date_format": "DD/MM/YYYY", "default_country": "NG", "duplicate_policy": "flag" }     // duplicate_policy: 'skip', 'flag' or 'create'
}
// 201
{ "status": "success", "status_code": 201, "message": "Import created. Upload the file to continue",
  "data": { "import": { "_id": "66im...", "status": "awaiting_upload", "upload": { "url": "https://storage.example/...", "method": "PUT", "headers": { "Content-Type": "text/csv" }, "expires_at": "2026-11-09T10:40:00Z" } } } }
```

After uploading the file, start the job:

```json
// POST /imports/66im.../start     request: {}
// 202
{ "status": "success", "status_code": 202, "message": "Import started",
  "data": { "import": { "_id": "66im...", "status": "queued" } } }
```

### GET /imports, GET /exports

Permission `user:manage` for imports and `tenant:export` for exports. Cursor-paginated `data.imports` / `data.exports`, each with `_id`, `kind`, `status`, `requested_by`, `created_at`, `progress` and `expires_at`. Without these lists an administrator cannot find a job started in an earlier session.

### GET /imports/:id

```json
{
  "status": "success",
  "status_code": 200,
  "message": "Import retrieved successfully",
  "data": {
    "import": {
      "_id": "66im...", "status": "completed", "dry_run": true,
      "progress": { "total": 1200, "processed": 1200 },
      "report": {
        "would_create": 1150, "skipped": 30, "flagged_as_possible_duplicate": 20,
        "errors": [ { "row": 14, "field": "date_of_birth", "message": "Invalid date '31/02/1980'" }, { "row": 87, "field": "sex", "message": "Unrecognized value 'M/F'" } ],
        "error_report_download_endpoint": "/imports/66im.../errors"
      }
    }
  }
}
```

Statuses: `awaiting_upload`, `queued`, `running`, `completed`, `failed`. A dry run changes nothing; run again with `dry_run: false` after reviewing the report. Real runs report `created` instead of `would_create`, and imported patients get `paper_file.status: "none"` and a `legacy_number`.

### GET /imports/:id/errors

Permission `user:manage`. Returns a short-lived signed download URL for the row-error CSV (`error_report_key` on the job) rather than the file itself, because error reports are large and contain patient data. The link generation is audited.

### POST /exports/tenant

Permission `tenant:export`. Requires step-up authentication.

```json
// request
{ "include": ["patients", "visits", "vitals", "clinical_entries", "prescriptions", "documents", "audit_logs"], "include_files": true, "format": "jsonl", "step_up": { "mfa_code": "482913" } }
// 202
{ "status": "success", "status_code": 202, "message": "Export queued",
  "data": { "export": { "_id": "66ex...", "kind": "tenant_export", "status": "queued" } } }
```

Errors: `401 step_up_failed`, `409 export_in_progress`.

### GET /exports/:id

Works for tenant exports and audit exports.

```json
{ "status": "success", "status_code": 200, "message": "Export retrieved successfully",
  "data": { "export": { "_id": "66ex...", "kind": "tenant_export", "status": "completed", "result": { "download_url": "https://storage.example/...", "expires_at": "2026-11-09T14:00:00Z", "bytes": 7421993102, "manifest": { "patients": 8420, "visits": 21377, "documents": 15990, "files": 41233 } } } } }
```

For large exports with files the result contains a manifest and several time-limited download links. Each link generation is audited.

### GET /metering/dashboard (Release 2)

Permission `tenant:configure`. Query: `period=2026-11`.

```json
{ "status": "success", "status_code": 200, "message": "Dashboard retrieved successfully",
  "data": { "dashboard": { "period": "2026-11", "ai_pages_processed": 312, "ai_page_cap": 2000, "extraction_outcomes": { "approved": 120, "rejected": 14, "failed": 5 }, "avg_flagged_fields_per_extraction": 2.3, "storage_bytes": 8421934080, "documents_captured": 540, "daily": [ { "date": "2026-11-08", "ai_pages": 41, "documents": 72 } ] } } }
```

### GET /backfill/progress (Release 2)

Permission `patient:read:all`.

```json
{ "status": "success", "status_code": 200, "message": "Backfill progress retrieved successfully",
  "data": { "progress": { "total_patients": 8420, "by_paper_file_status": { "none": 5100, "partial": 2300, "complete": 900, "archived": 120 }, "captured_this_week": 310, "top_capturers": [ { "user": { "_id": "66f2...", "name": "Records Clerk" }, "documents": 210 } ] } } }
```

## 25. Platform Administration

Separate realm under `/api/v1/platform`. Tokens use a different issuer and audience and cannot call tenant routes. Platform staff cannot read patient data through any of these endpoints. The response envelope is the same as everywhere else.

### POST /platform/auth/login, POST /platform/auth/mfa/verify

Same request and response shapes as `/auth/login` and `/auth/mfa/verify`. MFA is mandatory.

### POST /platform/tenants

```json
// request
{
  "name": "St Mary's Hospital", "slug": "st-marys", "country": "NG", "timezone": "Africa/Lagos", "plan": "trial",
  "admin": { "email": "admin@stmarys.example", "name": { "given": ["Tunde"], "family": "Bello" } },
  "patient_id_format": { "pattern": "SMH-{YYYY}-{SEQ:6}", "reset_sequence": "yearly" }
}
```

```json
// 201
{
  "status": "success",
  "status_code": 201,
  "message": "Hospital created successfully",
  "data": {
    "tenant": { "_id": "66a0...", "name": "St Mary's Hospital", "slug": "st-marys", "status": "active", "plan": "trial", "modules": { "ai_extraction": false } },
    "admin_invitation": { "_id": "66d1...", "email": "admin@stmarys.example", "expires_at": "2026-11-16T09:30:00Z" }
  }
}
```

Provisioning seeds the tenant config, the default role templates, document types and visit types. Errors: `409 slug_taken`, `422 invalid_patient_id_pattern`.

### GET /platform/tenants, GET /platform/tenants/:id, PATCH /platform/tenants/:id

GET returns `data.tenants` (with `data.pagination`) or `data.tenant`, with `status`, `plan`, `created_at`, counts of users and patients, and storage size (counts only). PATCH accepts `name`, `plan`, `modules` (feature flags), and `legal_hold`, and returns `data.tenant`.

```json
{ "plan": "standard", "modules": { "ai_extraction": true }, "legal_hold": false }
```

### POST /platform/tenants/:id/suspend, /resume

```json
// request
{ "reason": "Unpaid invoice, 30 days overdue" }
// 200
{ "status": "success", "status_code": 200, "message": "Hospital suspended successfully", "data": {} }
```

Suspension blocks logins and API calls for the tenant with `403 tenant_suspended`; data is untouched.

### POST /platform/tenants/:id/offboard

```json
// request
{ "confirm_slug": "st-marys", "export_first": true, "delete_after_days": 30 }
// 202
{ "status": "success", "status_code": 202, "message": "Offboarding started",
  "data": { "offboarding": { "tenant_id": "66a0...", "status": "offboarding", "export_job_id": "66ex...", "deletion_scheduled_for": "2026-12-09T00:00:00Z" } } }
```

Errors: `422 slug_mismatch`, `409 legal_hold_active`. The purge runs only after the export is downloaded or the grace period ends, and never while a legal hold is set.

### GET /platform/tenants/:id/usage

```json
{ "status": "success", "status_code": 200, "message": "Usage retrieved successfully",
  "data": { "usage": { "period": "2026-11", "active_users": 23, "patients": 8420, "documents_captured": 540, "ai_pages_processed": 312, "storage_bytes": 8421934080 } } }
```

### GET /platform/health

```json
{ "status": "success", "status_code": 200, "message": "Platform health retrieved successfully",
  "data": { "health": { "api": "ok", "mongo": "ok", "redis": "ok", "storage": "ok", "queues": { "extraction": { "waiting": 4, "active": 2, "failed": 0, "oldest_waiting_seconds": 12 }, "image_processing": { "waiting": 0, "active": 1, "failed": 0, "oldest_waiting_seconds": 0 } }, "ai": { "circuit": "closed", "error_rate_5m": 0.01 } } } }
```

### GET /platform/audit-logs

Platform audit trail: tenant create, suspend, resume, offboard, plan, module and legal-hold changes, and platform logins. Filters `actor_id`, `action`, `resource_id`, `from`, `to`, `limit`, `cursor`; returns `data.audit_logs` in the same shape as the tenant log. Tenant audit logs never contain these actions, and this log never contains patient data.

### POST /signup, POST /signup/verify

Public self-service tenant creation. Protected by rate limits and a captcha. It calls the same provisioning service as `POST /platform/tenants`; new tenants start on the trial plan with AI extraction off.

```json
// POST /signup request
{ "hospital_name": "St Mary's Hospital", "slug": "st-marys", "admin_email": "admin@stmarys.example", "admin_name": { "given": ["Tunde"], "family": "Bello" }, "country": "NG", "timezone": "Africa/Lagos", "terms_version": "2026-11", "captcha_token": "cf-turnstile-..." }
// 202 (a verification email is sent)
{ "status": "success", "status_code": 202, "message": "Check your email to confirm your hospital's sign-up", "data": {} }
```

```json
// POST /signup/verify request
{ "token": "su_4f3a..." }
// 201: the tenant is provisioned and the administrator receives the invitation email
{ "status": "success", "status_code": 201, "message": "Hospital created successfully",
  "data": { "tenant": { "_id": "66a0...", "slug": "st-marys" }, "next_step": "accept_invitation" } }
```

## 26. Error Code Catalogue

Every error response carries `status: "error"`, the HTTP status in `status_code`, a human-readable `message`, and a stable machine code in `data.code`. Clients branch on `status_code` and `data.code`, never on `message`. `data.errors[]` is used only for field validation, and `data.request_id` is present on every error.

Rules for codes: snake\_case; one code per condition; a code always maps to exactly one HTTP status; codes are never renamed or reused, because renaming one is a breaking API change. A route may reuse a generic code (`not_found`, `forbidden`) or define a specific one where the client must react differently.

### 26.1 Authentication and sessions

| Code | HTTP | Returned when |
| :-- | :-- | :-- |
| `invalid_credentials` | 401 | Wrong password, unknown email, or a disabled identity. The message is identical in all three cases. |
| `invalid_code` | 401 | Wrong TOTP or recovery code. |
| `challenge_expired` | 401 | The MFA challenge or enrollment token has expired or was already used. |
| `refresh_reuse_detected` | 401 | A refresh token whose row already has `used_at` was presented. The whole family is revoked. |
| `invalid_or_expired_token` | 400 | Reset, invitation or verification token invalid, used or expired. |
| `weak_password` | 422 | Password fails the tenant policy. `data.errors` names the failed rules. |
| `account_locked` | 423 | Too many failed logins. `data.locked_until` is present. |
| `account_deactivated` | 403 | The membership is `deactivated` or `removed`, or has no active membership. |
| `tenant_suspended` | 403 | The tenant is suspended. Returned for every route, including login. |
| `mfa_required_by_role` | 403 | MFA cannot be disabled because a held role has `mfa_required`. |
| `invitation_already_used` | 409 | The invitation was already accepted. |
| `email_change_not_supported` | 422 | `email` was sent to `PATCH /auth/me`. |
| `email_unavailable` | 409 | The email belongs to an active membership elsewhere. The message never names the hospital. |
| `step_up_failed` | 401 | The step-up MFA code for a sensitive action was wrong or expired. |
| `rate_limited` | 429 | A rate limit was hit. `Retry-After` is set. |

### 26.2 Staff, roles and tenant configuration

| Code | HTTP | Returned when |
| :-- | :-- | :-- |
| `unknown_role` | 422 | A `role_id` does not exist in this tenant. |
| `unknown_permission` | 422 | A permission string is not in the catalogue. |
| `role_key_exists` | 409 | A role with that key already exists in the tenant. |
| `role_in_use` | 409 | Deleting a role that users still hold. `data` carries the user count. |
| `system_role_protected` | 403 | Changing the key of, or deleting, a seeded role. |
| `care_team_reassignment_required` | 409 | Removing a user who is on care teams without `reassign_care_team_to`. `data.patient_count` is present. |
| `invalid_patient_id_pattern` | 422 | The pattern fails the grammar in §6.4, or produces a number longer than the configured maximum. |
| `ai_notice_not_accepted` | 422 | Enabling AI without accepting the current notice version. |
| `version_mismatch` | 412 | `If-Match` does not match the current `version`. Also reported as `precondition_failed` for non-versioned resources. |

### 26.3 Patients

| Code | HTTP | Returned when |
| :-- | :-- | :-- |
| `duplicates_found` | 409 | Registration without an override while candidates exist. `data.candidates` is present. |
| `patient_not_found` | 404 | No such patient in this tenant. Also used for another tenant's patient. |
| `care_team_required` | 403 | The caller has the permission but no care-team relationship and no break-glass grant. `data.break_glass_available` is present when the caller holds `access:breakglass`. |
| `same_patient` | 422 | Merge with `survivor_id` equal to `merged_id`. |
| `patient_already_merged` | 409 | Either patient is already merged. |
| `user_not_clinician` | 422 | A care-team member or attending clinician is not a clinician. |
| `already_assigned` | 409 | The user is already on the care team. |
| `idempotency_in_progress` | 409 | A request with the same `Idempotency-Key` is still being processed. |

### 26.4 Visits, vitals and clinical records

| Code | HTTP | Returned when |
| :-- | :-- | :-- |
| `unknown_visit_type` | 422 | The visit type is not active in tenant config. |
| `attending_not_clinician` | 422 | The attending clinician is not a clinician in this tenant. |
| `unsigned_drafts_exist` | 409 | Closing a visit with drafts and `force` false. `data.drafts` lists them. |
| `vitals_confirmation_required` | 409 | Plausible but unusual values without `acknowledge_warnings`. `data.warnings` lists them. |
| `vitals_invalid` | 422 | Physically impossible values. Nothing is saved. |
| `author_only` | 403 | Signing someone else's draft. |
| `already_signed` | 409 | Signing a version that is already signed. |
| `empty_entry` | 422 | Signing an entry with no content. |
| `prescription_warnings` | 409 | Possible allergy conflict without `acknowledge_warnings`. `data.warnings` is present. |
| `entry_not_found` | 404 | No such clinical entry or version. |
| `prescription_not_found` | 404 | No such prescription or version. |

### 26.5 Documents

| Code | HTTP | Returned when |
| :-- | :-- | :-- |
| `unknown_document_type` | 422 | The document type is not active in tenant config. |
| `too_many_pages` | 422 | Page count exceeds `max_pages_per_document`. |
| `page_no_exists` | 422 | Adding a page number that already exists. |
| `page_too_large` | 413 | A page exceeds the configured size limit. |
| `unsupported_media_type` | 415 | MIME type not in the allowed list, or the bytes do not match it. |
| `client_capture_id_conflict` | 409 | The `client_capture_id` exists with a different `request_hash`. A replay of the same payload returns 200 instead. |
| `object_not_found` | 409 | `complete` was called before the object was uploaded. |
| `checksum_mismatch` | 422 | The stored object's SHA-256 differs from the declared one. |
| `invalid_image` | 422 | Magic bytes or decode show the content is not an image. |
| `pages_missing` | 409 | `finalize` with unuploaded pages. `data.missing_pages` lists them. |
| `document_processing` | 409 | A page cannot be added or the document retagged because processing has begun. |
| `extraction_in_progress` | 409 | Retagging a document whose extraction is queued, processing or awaiting review. |
| `document_not_found` | 404 | No such document. |

### 26.6 AI extraction

| Code | HTTP | Returned when |
| :-- | :-- | :-- |
| `ai_not_enabled` | 403 | The tenant has AI extraction off. |
| `document_not_handwritten` | 422 | The document is not marked handwritten. |
| `document_not_ready` | 422 | Pages are still uploading or processing. |
| `ai_monthly_cap_reached` | 429 | `monthly_page_cap` would be exceeded. `data.cap` and `data.used` are present. |
| `ai_unavailable` | 503 | The circuit breaker is open or the provider is unreachable. `Retry-After` is set. |
| `medications_not_confirmed` | 422 | Approval with medications present and `medications_confirmed` not true. |
| `not_reviewable` | 409 | Approve, reject or draft on a job that is not `needs_review`. |
| `extraction_not_found` | 404 | No such extraction job. |

### 26.7 Search, audit, imports and exports

| Code | HTTP | Returned when |
| :-- | :-- | :-- |
| `query_required` | 422 | `/search/patients` with none of `q`, `dob`, `phone`, `patient_number`. |
| `query_too_short` | 422 | `q` shorter than two characters. |
| `cursor_invalid` | 400 | The cursor is malformed, expired or from another sort order. |
| `export_in_progress` | 409 | A second tenant export is requested while one is running. |
| `import_not_found` | 404 | No such import job. |
| `export_not_found` | 404 | No such export job. |

### 26.8 Platform and signup

| Code | HTTP | Returned when |
| :-- | :-- | :-- |
| `slug_taken` | 409 | The tenant slug is in use. |
| `slug_mismatch` | 422 | `confirm_slug` does not match the tenant. |
| `legal_hold_active` | 409 | Offboarding or purging a tenant under legal hold. |
| `tenant_not_found` | 404 | No such tenant. |
| `captcha_failed` | 422 | The signup captcha did not verify. |

### 26.9 Generic

| Code | HTTP | Returned when |
| :-- | :-- | :-- |
| `validation_failed` | 422 | Any request-model validation failure. `data.errors[]` lists fields. |
| `unauthenticated` | 401 | No token, or an invalid or revoked session. |
| `forbidden` | 403 | Authenticated but without the required permission. |
| `not_found` | 404 | Route or resource not found. Also the answer for another tenant's resource. |
| `precondition_failed` | 412 | `If-Match` required but missing, or stale. |
| `payload_too_large` | 413 | Body exceeds the request size limit. |
| `unsupported_media_type` | 415 | Wrong `Content-Type`. |
| `conflict` | 409 | A state conflict with no more specific code. |
| `internal_error` | 500 | Unhandled failure. The response never includes internals. |
| `service_unavailable` | 503 | A dependency (Mongo, Redis, storage) is unreachable. |

### 26.10 Extraction job codes (separate namespace)

These appear as `extraction.error.code` on a job and in `extraction.failed` events. They are not HTTP error codes, except where the request itself was rejected (see 26.6).

`provider_timeout` (retryable), `provider_rate_limited` (retryable), `provider_error` (retryable), `invalid_output` (retried once with a repair instruction, then failed), `image_unreadable` (not retryable without a new capture), `schema_violation` (fields dropped and flagged), `cancelled`.

Warning codes are a third namespace, surfaced in a `warnings[]` array rather than in `data.code`: `note_date_mismatch` on an extraction job (the date written on the note differs from the visit date) and `allergy_match` on a prescription. A warning never fails a request by itself; it is informational or gated by an explicit acknowledgement (`acknowledge_warnings`, `medications_confirmed`). Page quality flags (`blurry`, `dark`, `low_resolution`) live on the page, not in `warnings[]`.

## 27. Endpoint Index

Every route in sections 17 to 25, with the permission it requires. `care-team` means the patient policy in §5.3 also applies. `(R2)` marks Release 2 routes. Platform routes use the platform token and never the tenant token. This table is generated from route metadata; a route that is missing here fails the build.

### 27.1 Authentication and sessions

| Method | Path | Access |
| :-- | :-- | :-- |
| POST | `/auth/login` | Public |
| POST | `/auth/mfa/verify` | Challenge token |
| POST | `/auth/refresh` | Refresh token |
| POST | `/auth/logout` | Authenticated |
| POST | `/auth/password/forgot` | Public |
| POST | `/auth/password/reset` | Reset token |
| POST | `/auth/password/change` | Authenticated |
| POST | `/auth/invitations/accept` | Invite token |
| POST | `/auth/mfa/enroll` | Authenticated or enrollment token |
| POST | `/auth/mfa/enroll/confirm` | Authenticated or enrollment token |
| POST | `/auth/mfa/disable` | Authenticated |
| POST | `/auth/mfa/recovery-codes` | Authenticated |
| GET, PATCH | `/auth/me` | Authenticated |
| GET | `/auth/sessions` | Authenticated |
| DELETE | `/auth/sessions/:id` | Authenticated |

### 27.2 Staff, roles and tenant

| Method | Path | Permission |
| :-- | :-- | :-- |
| GET | `/users` | `user:manage` |
| GET | `/users/directory` | Authenticated |
| GET | `/users/invitations` | `user:manage` |
| POST | `/users/invitations` | `user:manage` |
| POST | `/users/invitations/:id/resend` | `user:manage` |
| DELETE | `/users/invitations/:id` | `user:manage` |
| GET, PATCH | `/users/:id` | `user:manage` |
| PUT | `/users/:id/signature` | `user:manage`, or self |
| POST | `/users/:id/deactivate` | `user:manage` |
| POST | `/users/:id/reactivate` | `user:manage` |
| POST | `/users/:id/mfa/reset` | `user:manage` |
| POST | `/users/:id/remove` | `user:manage` |
| GET | `/users/:id/sessions` | `user:manage` |
| DELETE | `/users/:id/sessions/:session_id` | `user:manage` |
| GET, POST | `/roles` | `role:manage` |
| GET, PATCH, DELETE | `/roles/:id` | `role:manage` |
| GET | `/permissions` | `role:manage` |
| GET | `/tenant` | Authenticated |
| GET, PATCH | `/tenant/config` | `tenant:configure` |
| PUT | `/tenant/logo` | `tenant:configure` |
| GET | `/tenant/usage` | `tenant:configure` |

### 27.3 Patients

| Method | Path | Permission |
| :-- | :-- | :-- |
| POST | `/patients/duplicate-check` | `patient:create` |
| POST | `/patients` | `patient:create` |
| GET | `/patients` | `patient:read`, care-team |
| GET, PATCH | `/patients/:id` | `patient:read` / `patient:update`, care-team |
| GET | `/patients/:id/summary` | `patient:read`, care-team |
| GET | `/patients/:id/timeline` | `visit:read`, care-team |
| GET | `/patients/:id/documents` | `document:read`, care-team |
| POST | `/patients/:id/care-team` | `patient:update` |
| DELETE | `/patients/:id/care-team/:user_id` | `patient:update` |
| POST | `/patients/:id/break-glass` | `access:breakglass` |
| POST | `/patients/merge` | `patient:merge` |
| GET | `/patients/:id/merge-history` | `patient:merge` |
| PATCH | `/patients/:id/paper-file` | `patient:update` |
| GET | `/patients/:id/conditions` | `clinical:read` |
| POST | `/patients/:id/conditions` | `clinical:write` |
| PATCH | `/patients/:id/conditions/:condition_id` | `clinical:write` |

### 27.4 Visits, vitals, clinical entries and prescriptions

| Method | Path | Permission |
| :-- | :-- | :-- |
| POST | `/patients/:id/visits` | `visit:create` |
| GET | `/patients/:id/visits` | `visit:read` |
| GET, PATCH | `/visits/:id` | `visit:read` / `visit:update` |
| POST | `/visits/:id/close` | `visit:close` |
| POST | `/visits/:id/vitals` | `vitals:write` |
| GET | `/patients/:id/vitals` | `visit:read` |
| GET | `/patients/:id/vitals/trends` | `visit:read` |
| PATCH | `/vitals/:id` | `vitals:write` |
| POST | `/visits/:id/clinical-entries` | `clinical:write` |
| GET | `/patients/:id/clinical-entries` | `clinical:read` |
| GET, PATCH | `/clinical-entries/:entry_id` | `clinical:read` / `clinical:write` |
| POST | `/clinical-entries/:entry_id/sign` | `clinical:sign` |
| GET | `/clinical-entries/:entry_id/versions` | `clinical:read` |
| POST | `/clinical-entries/:entry_id/mark-error` | `clinical:sign` |
| GET | `/patients/:id/prescriptions` | `clinical:read` |
| POST | `/visits/:id/prescriptions` | `clinical:write` |
| GET, PATCH | `/prescriptions/:id` | `clinical:read` / `clinical:write` |
| POST | `/prescriptions/:id/sign` | `clinical:sign` |
| POST | `/prescriptions/:id/mark-error` | `clinical:sign` |
| GET | `/terminology/icd10` | `clinical:read` |

### 27.5 Documents

| Method | Path | Permission |
| :-- | :-- | :-- |
| POST | `/documents` | `document:capture` |
| POST | `/documents/:id/pages` | `document:capture` |
| POST | `/documents/:id/pages/:page_no/upload-url` | `document:capture` |
| POST | `/documents/:id/pages/:page_no/multipart/parts` | `document:capture` |
| POST | `/documents/:id/pages/:page_no/complete` | `document:capture` |
| POST | `/documents/:id/finalize` | `document:capture` |
| GET | `/documents/:id/upload-status` | `document:capture` |
| GET | `/documents/:id` | `document:read`, care-team |
| GET | `/documents/:id/pages/:page_no/url` | `document:read`, care-team |
| PATCH | `/documents/:id` | `document:tag` |
| DELETE | `/documents/:id` | `document:delete` |
| POST | `/documents/batch` | `document:capture` (R2) |

### 27.6 AI extraction and events

| Method | Path | Permission |
| :-- | :-- | :-- |
| POST | `/documents/:id/extractions` | `extraction:request` |
| GET | `/extractions` | `extraction:review` |
| GET | `/extractions/:id` | `extraction:review` |
| PATCH | `/extractions/:id/draft` | `extraction:review` |
| POST | `/extractions/:id/approve` | `extraction:review` and `clinical:write` |
| POST | `/extractions/:id/reject` | `extraction:review` |
| POST | `/extractions/:id/retry` | `extraction:request` |
| POST | `/extractions/:id/cancel` | `extraction:request`, or `extraction:review` |
| POST | `/events/ticket` | Authenticated |
| GET | `/events` | Authenticated |

### 27.7 Search, audit and data operations

| Method | Path | Permission |
| :-- | :-- | :-- |
| GET | `/search/patients` | `patient:read` |
| GET | `/search/documents` | `document:read` |
| GET | `/search/suggest` | `patient:read` |
| GET | `/audit-logs` | `audit:read` |
| POST | `/audit-logs/exports` | `audit:export` |
| GET | `/audit-logs/verify` | `audit:read` |
| GET | `/imports` | `user:manage` |
| POST | `/imports/patients` | `user:manage` |
| GET | `/imports/:id` | `user:manage` |
| GET | `/imports/:id/errors` | `user:manage` |
| POST | `/imports/:id/start` | `user:manage` |
| GET | `/exports` | `tenant:export` |
| POST | `/exports/tenant` | `tenant:export`, step-up |
| GET | `/exports/:id` | `tenant:export` |
| GET | `/metering/dashboard` | `tenant:configure` (R2) |
| GET | `/backfill/progress` | `patient:read:all` (R2) |

### 27.8 Platform, signup and health

| Method | Path | Access |
| :-- | :-- | :-- |
| POST | `/platform/auth/login` | Public |
| POST | `/platform/auth/mfa/verify` | Challenge token |
| GET, POST | `/platform/tenants` | `platform_admin` |
| GET, PATCH | `/platform/tenants/:id` | `platform_admin` |
| POST | `/platform/tenants/:id/suspend` | `platform_admin` |
| POST | `/platform/tenants/:id/resume` | `platform_admin` |
| POST | `/platform/tenants/:id/offboard` | `platform_admin` |
| GET | `/platform/tenants/:id/usage` | `platform_admin` |
| GET | `/platform/audit-logs` | `platform_admin` |
| GET | `/platform/health` | `platform_admin` |
| POST | `/signup` | Public |
| POST | `/signup/verify` | Public |
| GET | `/health/live`, `/health/ready` | Public, unauthenticated |

`platform_support` may call only the GET routes above and never a state-changing route. `/health/live` and `/health/ready` are the unauthenticated probes used by the load balancer; `/platform/health` requires a platform token because it exposes queue and provider detail.

## 28. Revision History

| Revision | Date | Change |
| :-- | :-- | :-- |
| v0.1 | 2026-11-04 | First draft: requirements turned into architecture, schemas, endpoints and a delivery plan. |
| v0.2 | 2026-11-09 | Review pass. Six schema decisions recorded in §2 items 11 to 16 (identity/membership split, refresh-token history, asynchronous audit chain, atomic version allocation, merge records, denormalized patient fields). Corrections in §2.1. Data model updated in §6 and its index plan. New endpoints: `PATCH /auth/me`, MFA disable and recovery codes, `/users/directory`, `/users/invitations` (list), `/users/:id/signature`, `POST /documents/:id/pages`, `GET /patients/:id/clinical-entries`, `GET /patients/:id/prescriptions`, `GET /prescriptions/:id`, `POST /prescriptions/:id/mark-error`, `POST /extractions/:id/cancel`, `POST /events/ticket`, `GET /imports`, `GET /imports/:id/errors`, `GET /exports`, `GET /platform/audit-logs`. Sections 26 and 27, promised since v0.1 but missing, are written. |
| v0.3 | 2026-11-10 | Framework change: FastAPI on Python replaces NestJS on TypeScript. Updated: §1 stack table, §3 runtime components and repository layout, §3.4 request pipeline, §4.1–§4.4 tenancy plumbing, §5 permission and audit plumbing, §8 Celery, §9 Pillow/OpenCV, §10.1 provider protocol, §12 cross-cutting concerns, §13–§14 tooling and CI, §15.1 risk mitigation. Pydantic now covers HTTP input, LLM output and tenant config (§1, §10.2, §12), so `class-validator` and zod are gone. Field names in §6 are snake\_case throughout, matching the wire format, so the camelCase mapping layer and `common/dto/field-names.ts` are gone (§6.1, §7.1, §16.6). Tenancy, security, schema shape, endpoints and the delivery sequence are unchanged. |

### 28.1 Deferred to Release 2 or later

Un-merge, patient-level data export, full-text search over signed notes, batch capture, the metering dashboard, backfill progress, per-tenant encryption keys, platform support access into a tenant (time-boxed and tenant-approved), and email change as a self-service flow.
