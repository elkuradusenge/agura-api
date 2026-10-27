#!/usr/bin/env bash
# Replit run command. Milestone 2 adds the worker, milestone 6 adds the scheduler.
set -euo pipefail

alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
