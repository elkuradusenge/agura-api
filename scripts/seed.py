"""Seed the platform: administrator account and application settings.

Run with: python -m scripts.seed
"""

from __future__ import annotations

import argparse
import sys

from app.config import get_settings
from app.db import get_session_factory
from app.services.auth_service import ensure_bootstrap_admin
from app.services.settings_service import seed_defaults


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed AGUra defaults")
    parser.add_argument(
        "--reset-settings",
        action="store_true",
        help="Overwrite existing application settings with the defaults",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    session = get_session_factory()()
    try:
        created_settings = seed_defaults(session, overwrite=args.reset_settings)
        admin = ensure_bootstrap_admin(session)
    finally:
        session.close()

    print(f"Application settings written: {created_settings}")
    if admin is not None:
        print(f"Super administrator created: {admin.email}")
    elif settings.admin_bootstrap_password:
        print("Super administrator already exists, nothing to do")
    else:
        print("ADMIN_BOOTSTRAP_PASSWORD is not set, no administrator created")
    return 0


if __name__ == "__main__":
    sys.exit(main())
