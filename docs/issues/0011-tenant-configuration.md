# 0011 — Tenant configuration with cache and validation

| | |
| :-- | :-- |
| Milestone | M1 Tenancy and identity |
| Depends on | 0010 |
| Plan | §4.7, §6.2 `TenantConfig`, §18 `GET`/`PATCH /tenant/config` |

## Context

Session timeouts, password rules and the patient ID format are tenant settings, and
login (0013/0014) cannot be built without the security block. The plan is specific
about behaviour: one document per tenant, validated by a Pydantic model with
defaults, cached for 60 seconds, security fields never older than 5 seconds, and
every change audited.

## Scope

- [ ] Add `app/models/tenant_config.py` with the full §6.2 shape: branding,
      modules, `patient_id_format`, document types, visit types, `ai`,
      `security { idle_timeout_min, absolute_timeout_hours, password_min_length,
      break_glass_minutes }`, `retention`, `version`. Defaults must be valid with no
      document present.
- [ ] Validate the invariants that matter now: `patient_id_format.pattern` parses
      against the placeholder grammar (tokens `{PREFIX}`, `{YYYY}`, `{SEQ:n}`),
      timeouts are positive and bounded, enabled AI requires a provider and model,
      document and visit type keys are unique and stable.
- [ ] Add `app/db/tenant_configs.py` over a `TenantCollection` with a unique
      `tenant_id` index and `get_or_default(tenant_id)`, and
      `update(tenant_id, patch, expected_version)` that deep-merges and bumps
      `version`.
- [ ] Add `app/services/tenant_config.py` with the Redis cache:
      `t:{tenant_id}:config` for 60 seconds, a separate 5-second entry for the
      security block, deletion plus a Redis publish on write so other instances drop
      their copy.
- [ ] Add `GET /tenant/config` (`tenant:configure`) returning the effective config
      and `ETag`, and `PATCH /tenant/config` accepting a partial patch with
      `If-Match`, returning the updated config and auditing the change.
- [ ] Return `412 version_mismatch` for a stale `If-Match`, `422 validation_failed`
      with field errors for an invalid patch, and never echo unknown fields.
- [ ] Add tests: defaults when no document exists, cache hit and invalidation,
      security block expiring at 5 seconds, patch merge, stale version, audit row.

## Acceptance criteria

- `GET /tenant/config` works for a freshly seeded tenant with no config document.
- A `PATCH` is visible to the next read on the same instance immediately, and on
  another instance within the 5-second security ceiling.
- Changing a security field is never served stale for more than 5 seconds, which
  the test asserts by manipulating the clock or the TTL, not by sleeping 60.
- Every successful patch produces one audit entry naming the changed fields, with
  the previous and new values for security fields.

## Out of scope

- The settings UI and branding asset upload.
- Feature-flag evaluation middleware (`require_feature` lands with 0017 and reads
  this config).
- Migration of existing configs between versions.
