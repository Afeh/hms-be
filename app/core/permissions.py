"""Code-owned permission catalogue and the seeded role templates (plan §5.2).

Permissions are ``resource:action`` strings. The catalogue is the single source of
truth: routes declare one of these values, and tenant roles receive their default
permissions from ``DEFAULT_ROLE_TEMPLATES``. A route that names a permission not in
``Permission`` fails at import time in ``require_permission``.

The sprint keeps the catalogue explicit rather than storing wildcards, so a role's
permission set can be asserted exactly in tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Permission(StrEnum):
    """Every permission the API recognises."""

    PATIENT_CREATE = "patient:create"
    PATIENT_READ = "patient:read"
    PATIENT_UPDATE = "patient:update"
    PATIENT_MERGE = "patient:merge"
    PATIENT_READ_ALL = "patient:read:all"

    VISIT_CREATE = "visit:create"
    VISIT_READ = "visit:read"
    VISIT_UPDATE = "visit:update"
    VISIT_CLOSE = "visit:close"
    VITALS_WRITE = "vitals:write"

    CLINICAL_READ = "clinical:read"
    CLINICAL_WRITE = "clinical:write"
    CLINICAL_SIGN = "clinical:sign"

    DOCUMENT_CAPTURE = "document:capture"
    DOCUMENT_READ = "document:read"
    DOCUMENT_TAG = "document:tag"
    DOCUMENT_DELETE = "document:delete"

    EXTRACTION_REQUEST = "extraction:request"
    EXTRACTION_REVIEW = "extraction:review"

    AUDIT_READ = "audit:read"
    AUDIT_EXPORT = "audit:export"

    USER_MANAGE = "user:manage"
    ROLE_MANAGE = "role:manage"
    TENANT_CONFIGURE = "tenant:configure"
    TENANT_EXPORT = "tenant:export"

    ACCESS_BREAKGLASS = "access:breakglass"


ALL_PERMISSIONS: frozenset[str] = frozenset(permission.value for permission in Permission)

PERMISSION_DESCRIPTIONS: dict[Permission, str] = {
    Permission.PATIENT_CREATE: "Register a patient",
    Permission.PATIENT_READ: "View a patient in the user's care team",
    Permission.PATIENT_UPDATE: "Update patient demographics",
    Permission.PATIENT_MERGE: "Merge duplicate patient records",
    Permission.PATIENT_READ_ALL: "View any patient in the tenant",
    Permission.VISIT_CREATE: "Create a visit",
    Permission.VISIT_READ: "View visits",
    Permission.VISIT_UPDATE: "Update a visit",
    Permission.VISIT_CLOSE: "Close a visit",
    Permission.VITALS_WRITE: "Record vital signs",
    Permission.CLINICAL_READ: "View clinical entries and prescriptions",
    Permission.CLINICAL_WRITE: "Write clinical entries and prescriptions",
    Permission.CLINICAL_SIGN: "Sign a clinical entry",
    Permission.DOCUMENT_CAPTURE: "Upload a document",
    Permission.DOCUMENT_READ: "View documents",
    Permission.DOCUMENT_TAG: "Tag documents",
    Permission.DOCUMENT_DELETE: "Delete documents",
    Permission.EXTRACTION_REQUEST: "Request AI extraction",
    Permission.EXTRACTION_REVIEW: "Review an AI extraction",
    Permission.AUDIT_READ: "Read the audit log",
    Permission.AUDIT_EXPORT: "Export the audit log",
    Permission.USER_MANAGE: "Invite, update and remove staff",
    Permission.ROLE_MANAGE: "Create and change roles",
    Permission.TENANT_CONFIGURE: "Change hospital configuration",
    Permission.TENANT_EXPORT: "Export hospital data",
    Permission.ACCESS_BREAKGLASS: "Invoke emergency access",
}


@dataclass(frozen=True, slots=True)
class RoleTemplate:
    """The default shape of one seeded role."""

    key: str
    name: str
    permissions: frozenset[Permission]
    mfa_required: bool


#: The five default tenant roles. ``hospital_admin`` carries the demo permissions
#: fixed by the sprint plan: patient/visit/vitals work plus tenant administration.
DEFAULT_ROLE_TEMPLATES: tuple[RoleTemplate, ...] = (
    RoleTemplate(
        key="hospital_admin",
        name="Hospital Administrator",
        permissions=frozenset(
            {
                Permission.PATIENT_CREATE,
                Permission.PATIENT_READ,
                Permission.PATIENT_UPDATE,
                Permission.PATIENT_READ_ALL,
                Permission.VISIT_CREATE,
                Permission.VISIT_READ,
                Permission.VISIT_UPDATE,
                Permission.VISIT_CLOSE,
                Permission.VITALS_WRITE,
                Permission.USER_MANAGE,
                Permission.ROLE_MANAGE,
                Permission.TENANT_CONFIGURE,
                Permission.TENANT_EXPORT,
                Permission.AUDIT_READ,
                Permission.AUDIT_EXPORT,
            }
        ),
        mfa_required=False,
    ),
    RoleTemplate(
        key="medical_director",
        name="Medical Director",
        permissions=frozenset(
            {
                Permission.PATIENT_READ,
                Permission.PATIENT_READ_ALL,
                Permission.VISIT_READ,
                Permission.CLINICAL_READ,
                Permission.DOCUMENT_READ,
            }
        ),
        mfa_required=True,
    ),
    RoleTemplate(
        key="doctor",
        name="Doctor",
        permissions=frozenset(
            {
                Permission.PATIENT_READ,
                Permission.PATIENT_UPDATE,
                Permission.VISIT_CREATE,
                Permission.VISIT_READ,
                Permission.VISIT_UPDATE,
                Permission.VISIT_CLOSE,
                Permission.VITALS_WRITE,
                Permission.CLINICAL_READ,
                Permission.CLINICAL_WRITE,
                Permission.CLINICAL_SIGN,
                Permission.DOCUMENT_READ,
                Permission.DOCUMENT_CAPTURE,
                Permission.DOCUMENT_TAG,
                Permission.EXTRACTION_REQUEST,
                Permission.EXTRACTION_REVIEW,
                Permission.ACCESS_BREAKGLASS,
            }
        ),
        mfa_required=True,
    ),
    RoleTemplate(
        key="nurse",
        name="Nurse",
        permissions=frozenset(
            {
                Permission.PATIENT_READ,
                Permission.VISIT_CREATE,
                Permission.VISIT_READ,
                Permission.VITALS_WRITE,
                Permission.DOCUMENT_CAPTURE,
                Permission.DOCUMENT_READ,
                Permission.DOCUMENT_TAG,
                Permission.CLINICAL_READ,
            }
        ),
        mfa_required=True,
    ),
    RoleTemplate(
        key="records_clerk",
        name="Records Clerk",
        permissions=frozenset(
            {
                Permission.PATIENT_CREATE,
                Permission.PATIENT_READ,
                Permission.PATIENT_UPDATE,
                Permission.PATIENT_MERGE,
                Permission.PATIENT_READ_ALL,
                Permission.DOCUMENT_CAPTURE,
                Permission.DOCUMENT_READ,
                Permission.DOCUMENT_TAG,
                Permission.EXTRACTION_REQUEST,
                Permission.EXTRACTION_REVIEW,
            }
        ),
        mfa_required=False,
    ),
)


def validate_permission(permission: str) -> str:
    """Return ``permission`` if it is in the catalogue, otherwise raise ``ValueError``."""
    if permission not in ALL_PERMISSIONS:
        raise ValueError(f"unknown permission: {permission!r}")
    return permission


__all__ = [
    "ALL_PERMISSIONS",
    "DEFAULT_ROLE_TEMPLATES",
    "PERMISSION_DESCRIPTIONS",
    "Permission",
    "RoleTemplate",
    "validate_permission",
]
