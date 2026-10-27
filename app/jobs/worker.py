"""Standalone job worker.

Run as its own Replit process so heavy uploads never share a process with the API:

    python -m app.jobs.worker

The API can also run jobs in a thread pool during development. Both paths take a
PostgreSQL advisory lock per job, so a job can never run twice.
"""

from __future__ import annotations

import argparse
import logging
import time

from sqlalchemy import select

from app.analysis.pipeline import run_job
from app.config import get_settings
from app.db import get_session_factory
from app.models.enums import JobStatus
from app.models.survey import SurveyJob

logger = logging.getLogger("agura.jobs.worker")


def claim_next_job_id() -> object | None:
    session = get_session_factory()()
    try:
        job_id = session.scalar(
            select(SurveyJob.id)
            .where(SurveyJob.status == JobStatus.QUEUED)
            .order_by(SurveyJob.created_at)
            .limit(1)
        )
        return job_id
    finally:
        session.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AGUra survey job worker")
    parser.add_argument("--once", action="store_true", help="Run at most one job then exit")
    args = parser.parse_args(argv)

    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())
    logger.info("Worker started, polling every %s seconds", settings.worker_poll_seconds)

    while True:
        job_id = claim_next_job_id()
        if job_id is None:
            if args.once:
                return 0
            time.sleep(settings.worker_poll_seconds)
            continue
        logger.info("Running job %s", job_id)
        run_job(job_id, settings)
        if args.once:
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
