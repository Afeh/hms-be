# API testing walkthrough

Every endpoint currently exposed in the OpenAPI docs, in a safe call order.
Use this with Swagger at `http://127.0.0.1:8000/docs` or with curl.

- Base URL: `http://127.0.0.1:8000`
- API prefix: `/api/v1`
- Demo account: `admin@acme.example` / `Password123!`
- Protected endpoints need: `Authorization: Bearer <access_token>`
- IDs in URLs are Mongo `_id` values, **not** `patient_number` values.

## Values to capture as you go

| Name | Where it comes from |
| :-- | :-- |
| `{access_token}` | `POST /auth/login` → `data.access_token` |
| `{admin_user_id}` | `GET /auth/me` → `data.user._id` |
| `{patient_id}` | `POST /patients` → `data.patient._id` |
| `{visit_id}` | `POST /patients/{patient_id}/visits` → `data.visit._id` |
| `{vitals_id}` | `POST /visits/{visit_id}/vitals` → `data.vitals._id` |
| `{patient_version}` | `ETag` header on patient create/get, e.g. `"1"` |
| `{visit_version}` | `ETag` header on visit create/get/patch |
| `{config_version}` | `ETag` header on `GET /tenant/config` |

`If-Match` values must include the quotes: `If-Match: "1"`.

---

## 1. Authentication

### `POST /api/v1/auth/login`

No auth required.

```json
{
  "email": "admin@acme.example",
  "password": "Password123!",
  "device_label": "API walkthrough"
}
```

Copy `data.access_token` into the Swagger **Authorize** dialog as a bearer token, or add it to every request below.

### `GET /api/v1/auth/me`

No body. Requires the bearer token.

Use `data.user._id` as `{admin_user_id}` for the visit body below.

---

## 2. Health

Both are unauthenticated and have no body.

- `GET /api/v1/health/live`
- `GET /api/v1/health/ready`

The readiness probe needs MongoDB and Redis to be up.

---

## 3. Tenant

### `GET /api/v1/tenant`

No body. Requires `tenant:configure`.

### `GET /api/v1/tenant/config`

No body. Capture the `ETag` response header as `{config_version}`.

### `PATCH /api/v1/tenant/config`

Requires `If-Match: {config_version}`.

```json
{
  "branding": {
    "display_name": "Acme Demo Hospital",
    "primary_color": "#0F766E",
    "support_email": "support@acme.example"
  },
  "modules": {
    "laboratory": true
  },
  "security": {
    "idle_timeout_min": 45
  }
}
```

Patches deep-merge into the stored configuration. Returned `ETag` becomes the next `{config_version}`.

---

## 4. Patients

### `POST /api/v1/patients`

Requires `patient:create`. Capture `data.patient._id` and the `ETag`.

```json
{
  "name": {
    "given": ["Demo", "Ada"],
    "family": "Okafor",
    "other": null
  },
  "date_of_birth": "1992-06-11",
  "dob_approximate": false,
  "sex": "female",
  "phones": ["+2348031112233", "+2348031112244"],
  "address": {
    "line1": "1 Demo Road",
    "line2": "Flat 2",
    "city": "Enugu",
    "state": "Enugu",
    "postal_code": "400001",
    "country": "NG"
  },
  "next_of_kin": [
    {
      "name": "Ada Okafor",
      "relationship": "Sister",
      "phone": "+2348031112255"
    }
  ],
  "payer": {
    "name": "Demo Health",
    "scheme": "Standard",
    "member_number": "DH-000123"
  },
  "legacy_number": "OLD-7788",
  "paper_file": false
}
```

Only `name` and `date_of_birth` are required; every other field is optional.

### `GET /api/v1/patients`

Query parameters, all optional:

| Parameter | Example | Notes |
| :-- | :-- | :-- |
| `status` | `active` | `active`, `inactive`, `deceased` |
| `registered_from` | `2026-01-01` | Date |
| `registered_to` | `2026-12-31` | Date |
| `sort` | `-registration_date` | `registration_date`, `-registration_date`, `name`, `-name` |
| `limit` | `25` | 1–100 |
| `cursor` | returned `next_cursor` | For the next page |

### `GET /api/v1/patients/{patient_id}`

No body. Capture the `ETag` as `{patient_version}`.

### `PATCH /api/v1/patients/{patient_id}`

Requires `patient:update` and `If-Match: {patient_version}`.

```json
{
  "phones": ["+2348039990000"],
  "address": {
    "line1": "2 Updated Road",
    "city": "Enugu",
    "state": "Enugu",
    "country": "NG"
  },
  "next_of_kin": [
    {
      "name": "Ngozi Okafor",
      "relationship": "Mother",
      "phone": "+2348039990001"
    }
  ],
  "paper_file": false
}
```

Send at least one field. The returned `ETag` becomes the next `{patient_version}`.

---

## 5. Visits

### `POST /api/v1/patients/{patient_id}/visits`

Requires `visit:create`. Uses the admin membership ID from `/auth/me`.

```json
{
  "type": "outpatient",
  "visit_date": "2026-11-14",
  "attending_clinician_id": "{admin_user_id}",
  "reason_for_visit": "Fever and headache for three days"
}
```

`type` must match an active seeded visit type: `outpatient`, `inpatient`, or `emergency`. `visit_date` defaults to today if omitted. Capture `data.visit._id` and the `ETag` as `{visit_version}`.

### `GET /api/v1/patients/{patient_id}/visits`

Query parameters:

- `limit` — 1–100, default 25
- `cursor` — returned `next_cursor`

Newest visit first.

### `GET /api/v1/visits/{visit_id}`

No body. Returns the visit with its active vitals nested under `data.visit.vitals`.

### `PATCH /api/v1/visits/{visit_id}`

Requires `visit:update` and `If-Match: {visit_version}`.

```json
{
  "reason_for_visit": "Fever, headache and mild rash",
  "visit_date": "2026-11-15"
}
```

Send at least one editable field: `type`, `visit_date`, `attending_clinician_id`, or `reason_for_visit`.

### `POST /api/v1/visits/{visit_id}/close`

Requires `visit:close` and `If-Match: {visit_version}`.

```json
{
  "force": false
}
```

There are no drafts in the MVP, so `force` has no effect. Closing an already closed visit returns `409 visit_not_open`.

---

## 6. Vitals

### `POST /api/v1/visits/{visit_id}/vitals`

Requires `vitals:write`. The visit must still be open.

Normal measurements:

```json
{
  "measured_at": "2026-11-15T09:30:00Z",
  "temperature_c": 38.4,
  "pulse_bpm": 98,
  "systolic_mmhg": 128,
  "diastolic_mmhg": 82,
  "weight_kg": 81.0,
  "height_cm": 175
}
```

- BMI is computed server-side (`26.4` here).
- At least one measurement is required.
- `measured_at` is optional and defaults to now.
- Capture `data.vitals._id` as `{vitals_id}`.

Weight-only is allowed; BMI falls back to the patient's latest recorded height when one exists, otherwise it is `null`:

```json
{
  "weight_kg": 82.0
}
```

Warning path — returns `409 vitals_confirmation_required` with `data.warnings`:

```json
{
  "temperature_c": 41.5
}
```

Repeat the same body with acknowledgement to save it:

```json
{
  "temperature_c": 41.5,
  "acknowledge_warnings": true
}
```

Impossible path — returns `422 vitals_invalid` and is not saved:

```json
{
  "pulse_bpm": 900
}
```

### `GET /api/v1/patients/{patient_id}/vitals`

Query parameters, all optional:

| Parameter | Example | Notes |
| :-- | :-- | :-- |
| `visit_id` | `{visit_id}` | Limit to one visit |
| `from` | `2026-11-01T00:00:00Z` | Inclusive lower bound |
| `to` | `2026-11-30T23:59:59Z` | Inclusive upper bound |
| `limit` | `25` | 1–100 |
| `cursor` | returned `next_cursor` | For the next page |

Newest measurement first.

### `PATCH /api/v1/vitals/{vitals_id}`

Requires `vitals:write`. Marks the row as entered in error without deleting it.

```json
{
  "status": "entered_in_error",
  "reason": "Temperature recorded in Fahrenheit by mistake"
}
```

Marked rows still appear in the patient vitals list but are excluded from the visit detail's `vitals` array.

---

## 7. Visit images (Cloudflare R2)

Requires R2 to be configured. Uploading needs `document:capture`; listing and
refreshing download URLs need `document:read`. The visit must be open.

### `POST /api/v1/visits/{visit_id}/attachments`

```json
{
  "filename": "wound-left-arm.jpg",
  "content_type": "image/jpeg",
  "size_bytes": 2483921,
  "caption": "Wound on left forearm"
}
```

Copy `data.attachment._id` as `{attachment_id}` and the `upload` object. Then PUT
the file directly to R2; do not send the bearer token to the presigned URL:

```bash
curl -X PUT \
  -H "Content-Type: image/jpeg" \
  --upload-file ./wound-left-arm.jpg \
  "<data.attachment.upload.url>"
```

The URL and content type must match the values from the create response. The URL
expires after five minutes by default.

### `POST /api/v1/visits/{visit_id}/attachments/{attachment_id}/complete`

No body. The API checks that the R2 object exists and that its size and content
type match the declaration. On success the attachment becomes `uploaded` and the
response includes `download_url`.

If nothing was uploaded, this returns `409 object_not_found`. A size mismatch
returns `422 size_mismatch` and removes the rejected pending record.

### `GET /api/v1/visits/{visit_id}/attachments`

Optional `limit` (1–100) and `cursor` query parameters. Each pending attachment
includes a fresh `upload` URL; each uploaded attachment includes a fresh
`download_url`.

### `GET /api/v1/visits/{visit_id}/attachments/{attachment_id}/url`

No body. Returns a fresh short-lived download URL for an uploaded attachment.

Allowed image types are `image/jpeg`, `image/png`, `image/webp` and `image/heic`;
the default size limit is 10 MiB. An unsupported type returns `415`
`unsupported_media_type`; an oversized declaration returns `413 image_too_large`.

---

## Full endpoint checklist

| # | Method and path | Body |
| :-- | :-- | :-- |
| 1 | `POST /api/v1/auth/login` | Login body above |
| 2 | `GET /api/v1/auth/me` | — |
| 3 | `GET /api/v1/health/live` | — |
| 4 | `GET /api/v1/health/ready` | — |
| 5 | `GET /api/v1/patients` | — |
| 6 | `POST /api/v1/patients` | Patient create body |
| 7 | `GET /api/v1/patients/{patient_id}` | — |
| 8 | `PATCH /api/v1/patients/{patient_id}` | Patient update body + `If-Match` |
| 9 | `GET /api/v1/patients/{patient_id}/visits` | — |
| 10 | `POST /api/v1/patients/{patient_id}/visits` | Visit create body |
| 11 | `GET /api/v1/patients/{patient_id}/vitals` | — |
| 12 | `GET /api/v1/tenant` | — |
| 13 | `GET /api/v1/tenant/config` | — |
| 14 | `PATCH /api/v1/tenant/config` | Config patch body + `If-Match` |
| 15 | `GET /api/v1/visits/{visit_id}` | — |
| 16 | `PATCH /api/v1/visits/{visit_id}` | Visit update body + `If-Match` |
| 17 | `POST /api/v1/visits/{visit_id}/close` | `{"force": false}` + `If-Match` |
| 18 | `POST /api/v1/visits/{visit_id}/vitals` | Vitals body |
| 19 | `PATCH /api/v1/vitals/{vitals_id}` | Entered-in-error body |
| 20 | `POST /api/v1/visits/{visit_id}/attachments` | Image declaration body |
| 21 | `POST /api/v1/visits/{visit_id}/attachments/{attachment_id}/complete` | — |
| 22 | `GET /api/v1/visits/{visit_id}/attachments` | — |
| 23 | `GET /api/v1/visits/{visit_id}/attachments/{attachment_id}/url` | — |

## Common test order

1. Login and copy the token.
2. Call `/auth/me` and copy the admin user ID.
3. Create a patient and copy the patient ID and ETag.
4. List/search patients.
5. Create a visit with the admin user ID.
6. Record normal vitals, then list them.
7. Fetch the visit and confirm the vitals are nested.
8. Optionally attach an image: create the upload URL, PUT the file to R2, then complete it.
9. Optionally mark a vitals row entered in error.
10. Patch the patient and visit with their current ETags.
11. Close the visit with its current ETag.
