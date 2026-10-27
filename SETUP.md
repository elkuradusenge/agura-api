# AGUra backend setup

FastAPI service for AGUra (aguka means to grow): it reads the Rwanda Population and
Housing Census 2022 microdata, aggregates it, and runs the crop adoption platform.
This repository is independent from `agura-frontend`. They talk only through the REST
API and the WebSocket described in the product specification.

Current milestone: **M1 Backend foundation**. Milestones M2 to M6 add the data
pipeline, aggregations, AI suggestions, WebSocket events, products and reminders.

## Requirements

- Python 3.11 or newer
- PostgreSQL 15 or newer 

## Environment variables and Secrets

Copy `.env.example` to `.env` for local work. 

**Use `cp -n`, or open the file and edit it. A bare `cp .env.example .env` overwrites
the `DATABASE_URL`, `JWT_SECRET` and `ADMIN_BOOTSTRAP_PASSWORD` you already had, and
the next command then fails against a database that does not exist.**

The port and database name in `DATABASE_URL` are properties of your cluster, not
constants. Confirm them before running anything:

```bash
pg_isready -h 127.0.0.1 -p 5432   # is anything listening, and on which port
psql -h 127.0.0.1 -p 5432 -U postgres -l   # list the databases that really exist
```

If your cluster listens on a non default port, every connection string and every
`createdb` needs `-p`. This project was developed against an isolated cluster on
**port 5433** holding `agura_dev` and `agura_test`, because the system cluster on
5432 required a password that was not available.

| Name | Required | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | yes | PostgreSQL connection string. Both `postgres://` and `postgresql://` are rewritten to the psycopg driver automatically. The port and database name must exist on your cluster, see above. |
| `JWT_SECRET` | yes | Signs access tokens. Use a long random value in production. |
| `ADMIN_BOOTSTRAP_PASSWORD` | first run | Creates the first super administrator (`ADMIN_BOOTSTRAP_EMAIL`) when no administrator exists. |
| `ALLOWED_ORIGINS` | yes | Comma separated list of frontend origins for CORS. |
| `SMS_ENABLED` | no | `false` in development. SMS is only sent in production. |
| `SMS_API_KEY`, `SMS_USERNAME`, `SMS_SENDER_ID` | production | Africa's Talking credentials. |
| `SMALL_CELL_THRESHOLD` | no | Aggregate cells built from fewer households are suppressed. Default 10. |
| `PHONE_VERIFICATION_ENABLED` | no | Feature flag for SMS phone verification, off in development. |
| `PORT` | Port the API binds to. |
| `TEST_DATABASE_URL` | tests | Separate database used by the test suite. |

Never commit `.env`, secrets or any `.dta` file. `.gitignore` already covers `*.dta`,
`uploads/` and `.env`.

### Validation happens at startup

`DATABASE_URL`, `JWT_SECRET` and `ALLOWED_ORIGINS` have **no defaults**. If any is
missing or malformed the process stops immediately and prints every problem at
once, the way envalid does it in Node:

```
Environment validation failed
==============================
  ALLOWED_ORIGINS  required, but it is not set
  DATABASE_URL     required, but it is not set
  JWT_SECRET       required, but it is not set
==============================
Set these in .env or in the process environment, then start again.
```

There is no silent fallback, so the API cannot come up pointed at a database that
does not exist. `JWT_SECRET` must be at least 32 characters and must not be a
placeholder from a template. `DATABASE_URL` must be a PostgreSQL URL, and
`ENVIRONMENT`, `LOG_LEVEL` and `SMS_PROVIDER` accept only their known values.

Check the environment without starting anything:

```bash
python -m app.config
```

Everything else (`ACCESS_TOKEN_MINUTES`, `SMALL_CELL_THRESHOLD`, `ANALYSIS_CHUNK_SIZE`
and the rest) keeps a documented operational default, because a wrong value there
is a tuning choice rather than a connection to the wrong place.

## Local run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

cp -n .env.example .env       # -n: never overwrite a .env that already works
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
createdb -h 127.0.0.1 -p 5433 agura_test
export TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:5433/agura_test
# The suite rewrites the public schema, so never point it at real data.
pytest

ruff check .
ruff format --check .
python scripts/check_no_em_dash.py
```

`pytest` includes `tests/test_no_em_dash.py`, which fails the build when the em dash
character appears anywhere in the source tree, as required by the product rules.

## Seeding

```bash
python -m scripts.seed              # settings, food items, message templates, draft product, admin
python -m scripts.seed_demo_product # one complete, trackable demo product
```

The real Oyster mushroom product is always created as a **draft** with empty benefits
and nutrient values, because health statements need RAB review. It never becomes
trackable on its own.

`seed_demo_product` adds a second product, "Demo: Orange-fleshed sweet potato", and
publishes it through the same `product_service.mark_trackable` rule the console uses.
Every health statement in it says "Demo placeholder, replace before any farmer sees
it", and the reviewer is "Demo seed". It exists so the farmer flow can be walked
locally: plant it, get reminders, record a harvest. Do not ship it.

## M2 data pipeline

`POST /api/v1/admin/surveys/upload` streams the `.dta` file to a private folder,
returns `202` with a job id, and runs the pipeline outside the request. Stages run in
order and every stage writes a `job_events` row, so nothing is lost:

1. `received`: the file is re-hashed and compared with the upload checksum.
2. `validated`: the file opens and every key variable is present. The whole
   questionnaire goes into `variable_dictionary` and `value_labels`.
3. `mapped`: field groups and the yes code for each flag come from the value labels.
   The job pauses with `awaiting_mapping` until an administrator confirms.
4. `cleaned`: chunked reading, trimming, declared missing codes to NULL, geography
   names standardised, impossible ages rejected, exact duplicates dropped.
5. `loaded`: `COPY` into unlogged staging tables, then SQL deduplication and one
   ordered insert per table. Secondary indexes are rebuilt once afterwards.

Stata files that are not UTF-8 are detected automatically: `cp1252` and `latin-1` are
tried in turn, and `DTA_ENCODING` pins one when you know it.

Other endpoints: `GET /admin/surveys`, `GET /admin/surveys/{id}`, `GET /admin/jobs/{id}`,
`GET|POST /admin/jobs/{id}/mapping`, `GET /admin/surveys/{id}/dictionary`,
`GET /admin/surveys/{id}/quality-report`, `DELETE /admin/surveys/{id}` (rolls back every
row and deletes the file).

Generate a test file with `python -m scripts.make_synthetic_dta --out /tmp/census.dta`.
It is person level with repeated household variables, variable labels and value labels,
plus injected duplicates, impossible ages and untidy geography names. It contains no
real data.

Run the worker as its own process so uploads never share a process with the API:

```bash
python -m app.jobs.worker
```

Set `RUN_JOBS_INLINE=true` to run jobs in the API process instead, which is what the
test suite does.

## M3 aggregation and mushroom analysis

When the job reaches `aggregated` it computes four summary tables from SQL, in one
scan of `households` per table:

- `province_food_summary`: per food item, per geography (national, province, district,
  sector), the households growing or rearing it, the weighted share, the unweighted
  share, the average number of heads or trees where the questionnaire has a count
  variable, and for livestock the district-of-rearing distribution.
- `mushroom_summary`: mushroom households with weighted and unweighted shares, the
  urban and rural split, the female headed share, average household size, and the
  share that already keeps a grain, fruit or livestock activity.
- `household_diversity_summary`: distinct field crops, vegetables and fruits per
  household, stored on `households.food_diversity_count`, then averaged per geography
  with the full distribution.
- `readiness_summary`: a 0 to 100 score per geography from weighted component shares
  and the documented weights in `readiness.weights`.

Household head attributes (sex, education, phone type, internet access) are copied
onto the household row during the `loaded` stage, so every aggregate stays a single
table scan. The head person is found from the `p02` value label, never assumed.

### Small cell suppression

Raw counts and weighted sums are stored unchanged. Suppression happens when a
response is built, using `privacy.small_cell_threshold` from `app_settings`, so
changing the threshold takes effect immediately without re-running a job. A withheld
value comes back as:

```json
{"value": null, "count": null, "suppressed": true,
 "translation_key": "analysis.fewer_than_threshold", "threshold": 10}
```

### Analysis endpoints

- `GET /admin/food-items` with `q` and `group` filters, `PATCH /admin/food-items/{id}`
- `GET /admin/analysis/province-food-summary` with `survey_id`, `level`, `province`, `district`, `food_item_id`
- `GET /admin/analysis/mushroom`, `/diversity`, `/readiness` with the same filters
- Every response carries `meta` with the survey name and year, the source name and
  year, the threshold and whether the figures are weighted

### Readiness definitions

`readiness.definitions` in `app_settings` says how each component is detected. Codes
come from the value labels in the uploaded file. Some files ship person variables
without value labels, in which case a component is skipped and the job summary warns
about it. An administrator can then add an explicit `codes` list to that definition,
and the response reports `definition_source` as `configured_codes` so the assumption
stays visible.

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

## Notification text

`GET /notifications` returns both the translation keys and the resolved `title` and
`body`. The body is rendered from the admin editable `message_templates` row in the
farmer's language, so an administrator editing copy sees it in the app without a
client release. Titles are short fixed labels held in `app/sms/templates.py`.

A non English template that has not had human review carries a trailing
`[TODO: human review of ...]` marker, so an unreviewed translation is visible in the
product rather than pretending to be finished.

## Administrator identity

`GET /auth/admin/me` returns the signed in administrator with their permission list.
The console needs it after a page reload, because permissions otherwise only arrive
once, on the login response, and permission gated navigation would disappear.

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
