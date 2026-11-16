"""Patient registration and lookup (plan §6.4, sprint S001)."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.core.errors import ApiError, DuplicateEntityError
from app.db.client import TenantConnectionRegistry
from app.db.counters import COUNTERS_COLLECTION, next_value
from app.db.patients import PATIENTS_COLLECTION, PatientRepository, PatientSort
from app.db.tenant_configs import TenantConfigStore
from app.db.tenants import TenantStore
from app.models.patient import (
    NextOfKin,
    Patient,
    PatientAddress,
    PatientName,
    PatientStatus,
    Payer,
)
from app.models.tenant_config import PatientIdFormat, generate_patient_id
from app.schemas.patient import (
    PatientCreate,
    PatientListItem,
    PatientResponse,
    PatientUpdate,
)
from app.utils.pagination import InvalidCursorError

MAX_PATIENT_NUMBER_ATTEMPTS = 5

_DIAL_CODES = {"GB": "+44", "NG": "+234", "US": "+1"}


def _repository(registry: TenantConnectionRegistry, tenant_id: str) -> PatientRepository:
    return PatientRepository(registry.collection(tenant_id, PATIENTS_COLLECTION))


def _date_to_datetime(value: date) -> datetime:
    return datetime(value.year, value.month, value.day, tzinfo=UTC)


def _date_bounds(
    registered_from: date | None,
    registered_to: date | None,
) -> tuple[datetime | None, datetime | None]:
    start = None if registered_from is None else _date_to_datetime(registered_from)
    end = None if registered_to is None else _date_to_datetime(registered_to) + timedelta(days=1)
    return start, end


def patient_age(date_of_birth: datetime, *, today: date | None = None) -> int:
    """Whole years between *date_of_birth* and today."""
    born = date_of_birth.date()
    current = today or datetime.now(UTC).date()
    return max(
        current.year - born.year - ((current.month, current.day) < (born.month, born.day)),
        0,
    )


def patient_display_name(name: PatientName) -> str:
    """Join the given names and family name for list and search displays."""
    given = " ".join(part for part in name.given if part)
    return f"{given} {name.family}".strip()


def normalize_phone(phone: str, country: str | None) -> str:
    """Strip formatting and expand a local leading zero to the tenant country code."""
    cleaned = re.sub(r"[^\d+]", "", phone.strip())
    if not cleaned:
        return cleaned
    if cleaned.startswith("+"):
        return "+" + cleaned[1:].replace("+", "")
    if cleaned.startswith("00"):
        return "+" + cleaned[2:]
    dial_code = _DIAL_CODES.get((country or "").upper())
    if dial_code and cleaned.startswith("0"):
        return dial_code + cleaned[1:]
    return cleaned


def _normalize_contact(contact: NextOfKin, country: str | None) -> NextOfKin:
    if contact.phone:
        return contact.model_copy(update={"phone": normalize_phone(contact.phone, country)})
    return contact


async def _allocate_number(
    repo: PatientRepository,
    *,
    format_config: PatientIdFormat,
    tenant_slug: str,
    sequence: int | None,
) -> str:
    for _ in range(MAX_PATIENT_NUMBER_ATTEMPTS):
        candidate = generate_patient_id(
            format_config,
            tenant_slug=tenant_slug,
            sequence=sequence,
        )
        if await repo.find_by_number(candidate) is None:
            return candidate
    raise ApiError(
        409,
        "patient_number_conflict",
        "Could not allocate a unique patient number; try again",
    )


async def register_patient(
    payload: PatientCreate,
    registry: TenantConnectionRegistry,
    tenant_id: str,
) -> Patient:
    """Create a patient with a tenant-formatted, unique patient number."""
    tenant = await TenantStore(registry).get_by_id(tenant_id)
    if tenant is None:
        raise ApiError(404, "not_found", "Tenant not found")

    config = await TenantConfigStore(registry).get_or_default(tenant_id)
    format_config = config.patient_id_format
    counter_collection = registry.collection(tenant_id, COUNTERS_COLLECTION)
    sequence = (
        await next_value(counter_collection, "patient_number")
        if "{SEQ:" in format_config.pattern
        else None
    )

    repo = _repository(registry, tenant_id)
    country = tenant.country
    phone_numbers = [normalize_phone(phone, country) for phone in payload.phones]
    contacts = [
        _normalize_contact(NextOfKin(**contact.model_dump()), country)
        for contact in payload.next_of_kin
    ]
    name = PatientName(**payload.name.model_dump())

    for _ in range(MAX_PATIENT_NUMBER_ATTEMPTS):
        patient_number = await _allocate_number(
            repo,
            format_config=format_config,
            tenant_slug=tenant.slug,
            sequence=sequence,
        )
        patient = Patient(
            tenant_id=tenant_id,
            patient_number=patient_number,
            name=name,
            date_of_birth=_date_to_datetime(payload.date_of_birth),
            dob_approximate=payload.dob_approximate,
            sex=payload.sex,
            phones=phone_numbers,
            address=(
                None if payload.address is None else PatientAddress(**payload.address.model_dump())
            ),
            next_of_kin=contacts,
            payer=None if payload.payer is None else Payer(**payload.payer.model_dump()),
            legacy_number=payload.legacy_number,
            paper_file=payload.paper_file,
            display_name=patient_display_name(name),
        )
        try:
            return await repo.create(patient)
        except DuplicateEntityError:
            continue
    raise ApiError(
        409,
        "patient_number_conflict",
        "Could not allocate a unique patient number; try again",
    )


async def get_patient(
    registry: TenantConnectionRegistry,
    tenant_id: str,
    patient_id: str,
) -> Patient:
    patient = await _repository(registry, tenant_id).get(patient_id)
    if patient is None:
        raise ApiError(404, "not_found", "Patient not found")
    return patient


async def list_patients(
    registry: TenantConnectionRegistry,
    tenant_id: str,
    *,
    status: PatientStatus | None,
    registered_from: date | None,
    registered_to: date | None,
    sort: PatientSort,
    limit: int,
    cursor: str | None,
) -> tuple[list[Patient], str | None]:
    start, end = _date_bounds(registered_from, registered_to)
    try:
        return await _repository(registry, tenant_id).list_patients(
            status=status,
            registered_from=start,
            registered_to=end,
            sort=sort,
            limit=limit,
            cursor=cursor,
        )
    except InvalidCursorError as exc:
        raise ApiError(400, "invalid_cursor", "The pagination cursor is invalid") from exc


async def update_patient(
    registry: TenantConnectionRegistry,
    tenant_id: str,
    patient_id: str,
    payload: PatientUpdate,
    expected_version: int,
) -> Patient:
    repo = _repository(registry, tenant_id)
    current = await repo.get(patient_id)
    if current is None:
        raise ApiError(404, "not_found", "Patient not found")

    tenant = await TenantStore(registry).get_by_id(tenant_id)
    country = tenant.country if tenant else None
    changes: dict[str, Any] = {}

    if payload.name is not None:
        name = PatientName(**payload.name.model_dump())
        changes["name"] = name.model_dump()
        changes["display_name"] = patient_display_name(name)
    if payload.phones is not None:
        changes["phones"] = [normalize_phone(phone, country) for phone in payload.phones]
    if payload.address is not None:
        changes["address"] = PatientAddress(**payload.address.model_dump()).model_dump()
    if payload.next_of_kin is not None:
        contacts = [
            _normalize_contact(NextOfKin(**contact.model_dump()), country)
            for contact in payload.next_of_kin
        ]
        changes["next_of_kin"] = [contact.model_dump() for contact in contacts]
    if payload.payer is not None:
        changes["payer"] = Payer(**payload.payer.model_dump()).model_dump()
    if payload.paper_file is not None:
        changes["paper_file"] = payload.paper_file

    return await repo.update_versioned(patient_id, changes, expected_version)


def patient_response(patient: Patient) -> PatientResponse:
    return PatientResponse.from_patient(patient, patient_age(patient.date_of_birth))


def patient_list_item(patient: Patient) -> PatientListItem:
    return PatientListItem.from_patient(patient)


__all__ = [
    "get_patient",
    "list_patients",
    "normalize_phone",
    "patient_age",
    "patient_display_name",
    "patient_list_item",
    "patient_response",
    "register_patient",
    "update_patient",
]
