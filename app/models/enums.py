"""Enumerations shared by models and schemas."""

from __future__ import annotations

from enum import StrEnum


class Language(StrEnum):
    RW = "rw"
    EN = "en"
    FR = "fr"


class UserStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class AdminRole(StrEnum):
    SUPER_ADMIN = "super_admin"
    CONTENT_EDITOR = "content_editor"
    DATA_ANALYST = "data_analyst"
    SUPPORT_AGENT = "support_agent"


class AdminStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class SubjectType(StrEnum):
    USER = "user"
    ADMIN = "admin"


class ActorType(StrEnum):
    USER = "user"
    ADMIN = "admin"
    SYSTEM = "system"
