# AGUra backend setup

FastAPI service for AGUra (aguka means to grow): it reads the Rwanda Population and
Housing Census 2022 microdata, aggregates it, and runs the crop adoption platform.
This repository is independent from `agura-frontend`. They talk only through the REST
API and the WebSocket described in the product specification.

Current milestone: **M1 Backend foundation**. Milestones M2 to M6 add the data
pipeline, aggregations, AI suggestions, WebSocket events, products and reminders.

## Requirements

- Python 3.11 or newer
- PostgreSQL 15 or newer (Replit provides one, the connection string arrives as `DATABASE_URL`)

## Environment variables and Secrets

Copy `.env.example` to `.env` for local work. On Replit set the same names under Secrets.

| Name | Required | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | yes | PostgreSQL connection string. `postgres://` and `postgresql://` are rewritten to the psycopg driver automatically. |
| `JWT_SECRET` | yes | Signs access tokens. Use a long random value in production. |
| `ADMIN_BOOTSTRAP_PASSWORD` | first run | Creates the first super administrator (`ADMIN_BOOTSTRAP_EMAIL`) when no administrator exists. |
| `ALLOWED_ORIGINS` | yes | Comma separated list of frontend origins for CORS. |
| `SMS_ENABLED` | no | `false` in development. SMS is only sent in production. |
| `SMS_API_KEY`, `SMS_USERNAME`, `SMS_SENDER_ID` | production | Africa's Talking credentials. |
| `SMALL_CELL_THRESHOLD` | no | Aggregate cells built from fewer households are suppressed. Default 10. |
| `PHONE_VERIFICATION_ENABLED` | no | Feature flag for SMS phone verification, off in development. |
| `PORT` | Replit | Port the API binds to. |
| `TEST_DATABASE_URL` | tests | Separate database used by the test suite. |

Never commit `.env`, secrets or any `.dta` file. `.gitignore` already covers `*.dta`,
`uploads/` and `.env`.

## Local run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

cp .env.example .env          # then edit DATABASE_URL and JWT_SECRET
alembic upgrade head          # create the schema
python -m scripts.seed        # application settings and the first administrator
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

OpenAPI is published at `/openapi.json`, Swagger UI at `/docs`, ReDoc at `/redoc`.
All application routes live under `/api/v1`.

## Tests and checks

The suite runs against a real PostgreSQL database, not SQLite, because the models use
UUID and JSONB columns. Point `TEST_DATABASE_URL` at a scratch database.

```bash
createdb agura_test
export TEST_DATABASE_URL=postgresql+psycopg://user:password@127.0.0.1:5432/agura_test
# The suite rewrites the public schema, so never point it at real data.
pytest

ruff check .
ruff format --check .
python scripts/check_no_em_dash.py
```

`pytest` includes `tests/test_no_em_dash.py`, which fails the build when the em dash
character appears anywhere in the source tree, as required by the product rules.

## What M1 provides

- `POST /api/v1/auth/register`, `/auth/login`, `/auth/refresh`, `/auth/logout`,
  `/auth/password-reset/request`, `/auth/password-reset/confirm`
- `POST /api/v1/auth/admin/login`, administrator sessions carry the role
- `GET|PATCH|DELETE /api/v1/me`, `POST /api/v1/me/password`
- `GET /api/v1/admin/users`, `/admin/users/{id}` and the suspend, reactivate and
  reset password actions, all written to the audit log
- `GET /api/v1/admin/audit-log`
- `GET /api/v1/health` and `GET /api/v1/health/ready`
- Argon2 password hashing, JWT access tokens with 30 minute lifetime, refresh tokens
  stored hashed and rotated on every use, rate limits on sign in and code requests
- Consistent error envelope: `{"error": {"code", "translation_key", "message", "details"}}`
- Roles: `super_admin`, `content_editor`, `data_analyst`, `support_agent`, with a
  permission check on every administrator route

## Replit notes

- Bind to `0.0.0.0` and read the port from `PORT`.
- Use a deployment type that keeps one instance running (Reserved VM or equivalent).
  WebSocket connections and background jobs do not survive scale to zero.
- When M2 and M6 land, the API, the job worker and the scheduler run as separate
  processes. The `Procfile` in this repository lists them; start them from the Replit
  run configuration.
- Uploaded census files are streamed to a private folder and never served back. No API
  response returns an individual household or person record.

## Layout

```
app/main.py          application factory, CORS, error handlers
app/config.py        settings from environment and Secrets
app/db.py            engine and request scoped session
app/core/            errors, security, permissions, dependencies, pagination, rate limit
app/models/          SQLAlchemy 2 models
app/schemas/         Pydantic request and response models
app/routers/         HTTP routers
app/services/        auth, audit, settings, sms seam
alembic/             migrations
scripts/             seed command, em dash check, synthetic data generator (M2)
tests/               pytest suite
```
