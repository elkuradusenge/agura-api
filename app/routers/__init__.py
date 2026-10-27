"""HTTP routers."""

from app.routers import (
    admin_ai,
    admin_analysis,
    admin_audit,
    admin_surveys,
    admin_users,
    auth,
    health,
    me,
)

__all__ = [
    "admin_ai",
    "admin_analysis",
    "admin_audit",
    "admin_surveys",
    "admin_users",
    "auth",
    "health",
    "me",
]
