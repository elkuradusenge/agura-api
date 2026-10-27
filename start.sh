#!/usr/bin/env bash
# Replit run command for the API. Start the worker from the run configuration as well.
set -euo pipefail

alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
