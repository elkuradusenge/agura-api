"""Standalone scheduler process.

    python -m app.scheduler.runner

The pass runs every minute. A PostgreSQL advisory lock keeps a second instance from
sending the same reminders twice, which matters during a deploy overlap.
"""

from __future__ import annotations

import argparse
import logging
import time

from sqlalchemy import text

from app.config import get_settings
from app.db import get_session_factory
from app.scheduler.reminders import run_once

logger = logging.getLogger("agura.scheduler")

LOCK_KEY = 990_001
INTERVAL_SECONDS = 60


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AGUra reminder scheduler")
    parser.add_argument("--once", action="store_true", help="Run one pass then exit")
    parser.add_argument("--interval", type=int, default=INTERVAL_SECONDS)
    args = parser.parse_args(argv)

    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())
    logger.info("Scheduler started, running every %s seconds", args.interval)

    while True:
        session = get_session_factory()()
        acquired = False
        try:
            acquired = bool(
                session.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK_KEY})
            )
            if not acquired:
                logger.debug("Another scheduler holds the lock, skipping this pass")
            else:
                result = run_once(session, settings)
                logger.info("Reminder pass: %s", result.as_dict())
        except Exception:  # noqa: BLE001 - the loop must survive a bad pass
            logger.exception("Reminder pass failed")
            session.rollback()
        finally:
            if acquired:
                try:
                    session.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_KEY})
                    session.commit()
                except Exception:  # noqa: BLE001
                    session.rollback()
            session.close()

        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
