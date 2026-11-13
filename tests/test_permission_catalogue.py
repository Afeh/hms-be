"""The permission catalogue and role templates (issue 0017)."""

from __future__ import annotations

import pytest

from app.api.deps import require_permission
from app.core.permissions import (
    ALL_PERMISSIONS,
    DEFAULT_ROLE_TEMPLATES,
    PERMISSION_DESCRIPTIONS,
    Permission,
    validate_permission,
)


def test_every_catalogue_entry_is_described() -> None:
    assert set(PERMISSION_DESCRIPTIONS) == set(Permission)
    assert {permission.value for permission in Permission} == ALL_PERMISSIONS


def test_seeded_doctor_matches_the_plan_exactly() -> None:
    doctor = next(template for template in DEFAULT_ROLE_TEMPLATES if template.key == "doctor")

    assert {permission.value for permission in doctor.permissions} == {
        "patient:read",
        "patient:update",
        "visit:create",
        "visit:read",
        "visit:update",
        "visit:close",
        "vitals:write",
        "clinical:read",
        "clinical:write",
        "clinical:sign",
        "document:read",
        "document:capture",
        "document:tag",
        "extraction:request",
        "extraction:review",
        "access:breakglass",
    }


def test_hospital_admin_has_the_demo_permissions() -> None:
    admin = next(
        template for template in DEFAULT_ROLE_TEMPLATES if template.key == "hospital_admin"
    )
    permissions = {permission.value for permission in admin.permissions}

    assert {
        "patient:create",
        "patient:read",
        "patient:update",
        "visit:create",
        "visit:read",
        "visit:close",
        "vitals:write",
    } <= permissions
    assert admin.mfa_required is False


def test_role_templates_only_use_catalogue_permissions() -> None:
    for template in DEFAULT_ROLE_TEMPLATES:
        assert {permission.value for permission in template.permissions} <= ALL_PERMISSIONS


def test_unknown_permission_fails_when_the_route_is_declared() -> None:
    with pytest.raises(ValueError, match="typo:read"):
        require_permission("typo:read")
    with pytest.raises(ValueError, match="typo:read"):
        validate_permission("typo:read")
