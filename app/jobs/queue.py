"""Submission of survey jobs to a worker thread pool."""

from __future__ import annotations

import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from app.analysis.pipeline import run_job
from app.config import get_settings

logger = logging.getLogger("agura.jobs.queue")

_executor: ThreadPoolExecutor | None = None
_lock = threading.Lock()


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    with _lock:
        if _executor is None:
            settings = get_settings()
            _executor = ThreadPoolExecutor(
                max_workers=max(1, settings.job_workers), thread_name_prefix="agura-job"
            )
        return _executor


def submit_job(job_id: uuid.UUID) -> str:
    """Queue a job. Returns the mode used, which the API echoes in logs and tests."""
    settings = get_settings()
    if settings.run_jobs_inline:
        run_job(job_id, settings)
        return "inline"
    _get_executor().submit(run_job, job_id)
    return "thread"


def shutdown(wait: bool = False) -> None:
    global _executor
    with _lock:
        if _executor is not None:
            _executor.shutdown(wait=wait)
            _executor = None
