"""Small in-process rate limiter for sign in and code requests.

The platform runs a single API instance, so an in-memory sliding window is
enough. Swap for Redis if the deployment ever scales out.
"""

from __future__ import annotations

import threading
import time
from collections import deque

from app.config import get_settings
from app.core.errors import rate_limited

_lock = threading.Lock()
_hits: dict[tuple[str, str], deque[float]] = {}


def check_rate_limit(bucket: str, identity: str) -> None:
    settings = get_settings()
    limit = settings.auth_rate_limit_attempts
    window = float(settings.auth_rate_limit_window_seconds)
    now = time.monotonic()
    key = (bucket, identity)

    with _lock:
        timestamps = _hits.setdefault(key, deque())
        while timestamps and now - timestamps[0] > window:
            timestamps.popleft()
        if len(timestamps) >= limit:
            retry_after = int(window - (now - timestamps[0])) + 1
            raise rate_limited(max(retry_after, 1))
        timestamps.append(now)


def reset_rate_limits() -> None:
    with _lock:
        _hits.clear()
