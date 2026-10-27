"""Role based permissions for the administrator console."""

from __future__ import annotations

from enum import StrEnum

from app.models.enums import AdminRole


class Permission(StrEnum):
    USERS_READ = "users.read"
    USERS_WRITE = "users.write"
    AUDIT_READ = "audit.read"
    MONITORING_READ = "monitoring.read"

    SURVEYS_READ = "surveys.read"
    SURVEYS_WRITE = "surveys.write"
    ANALYSIS_READ = "analysis.read"
    AI_RUN = "ai.run"
    NUTRITION_WRITE = "nutrition.write"
    FOOD_ITEMS_WRITE = "food_items.write"

    PRODUCTS_READ = "products.read"
    PRODUCTS_WRITE = "products.write"
    TEMPLATES_WRITE = "templates.write"

    SUPPORT_READ = "support.read"
    SUPPORT_WRITE = "support.write"

    SETTINGS_WRITE = "settings.write"


ALL_PERMISSIONS: frozenset[Permission] = frozenset(Permission)

ROLE_PERMISSIONS: dict[AdminRole, frozenset[Permission]] = {
    AdminRole.SUPER_ADMIN: ALL_PERMISSIONS,
    AdminRole.CONTENT_EDITOR: frozenset(
        {
            Permission.PRODUCTS_READ,
            Permission.PRODUCTS_WRITE,
            Permission.TEMPLATES_WRITE,
            Permission.FOOD_ITEMS_WRITE,
            Permission.ANALYSIS_READ,
        }
    ),
    AdminRole.DATA_ANALYST: frozenset(
        {
            Permission.SURVEYS_READ,
            Permission.SURVEYS_WRITE,
            Permission.ANALYSIS_READ,
            Permission.AI_RUN,
            Permission.NUTRITION_WRITE,
            Permission.FOOD_ITEMS_WRITE,
            Permission.MONITORING_READ,
            Permission.PRODUCTS_READ,
        }
    ),
    AdminRole.SUPPORT_AGENT: frozenset(
        {
            Permission.USERS_READ,
            Permission.SUPPORT_READ,
            Permission.SUPPORT_WRITE,
            Permission.MONITORING_READ,
            Permission.PRODUCTS_READ,
        }
    ),
}


def permissions_for(role: AdminRole) -> frozenset[Permission]:
    return ROLE_PERMISSIONS.get(role, frozenset())


def has_permissions(role: AdminRole, required: tuple[Permission, ...] | list[Permission]) -> bool:
    granted = permissions_for(role)
    return all(permission in granted for permission in required)
