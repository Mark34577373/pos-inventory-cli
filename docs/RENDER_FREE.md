# Render Free: disposable, read-only fictional demo

The production failure occurs because Gunicorn imports `web_app` before a migrated
SQLite database exists at `TCCP_DB_PATH`. The production check remains intact.
The Render environment cannot be read from this repository: inspect the service's
Environment page for its current value. With no environment override, local code
defaults to the repository's `tccp.db`; production refuses a missing absolute path.
Do not upload that development database.

This checkout currently uses schema version **14**, not the later version mentioned
in earlier conversation. `python manage.py init` backs up an existing database,
runs the transactional schema initializer and identity migration with `seed=False`,
and checks SQLite integrity/foreign keys. It creates the unclaimed legacy store,
not an owner account or fictional sales. Plain `init` never seeds sample products.
The initializer's optional default seed is used by other callers; it is disabled here.

## Exact Render settings

Build Command:

```sh
pip install -r requirements.txt
```

Start Command (one line):

```sh
python manage.py prepare-render-demo && exec gunicorn --bind 0.0.0.0:$PORT --workers 1 --threads 2 --timeout 60 --error-logfile - web_app:app
```

Environment variables (each is a separate key/value):

| Key | Value |
| --- | --- |
| `TCCP_ENV` | `production` |
| `TCCP_DB_PATH` | `/tmp/tccp-public-demo/demo.db` |
| `TCCP_EPHEMERAL_DEMO` | `1` |
| `TCCP_READ_ONLY` | `1` |
| `TCCP_ALLOW_REGISTRATION` | `0` |
| `TCCP_SECRET_KEY` | Your stable private random secret, at least 32 characters |
| `TCCP_TRUSTED_HOSTS` | Your actual Render hostname, without scheme/path |
| `TCCP_DEBUG` | `0` |
| `TCCP_TIMEZONE` | `America/Chicago` |

Render supplies `PORT`; do not replace it with a database setting. `.env` is not
automatically loaded. Never commit or paste your real secret into this document.

## Bootstrap safeguards

`prepare-render-demo` is explicit opt-in; it refuses other security profiles and
requires a dedicated `demo.db` under a `/tmp` subdirectory. It never uses the default
development DB. A bootstrap lock serializes startup, then releases before Gunicorn.
Existing unrelated/corrupt databases, private accounts/stores and non-demo business
records cause failure without being overwritten or erased.

On a new file, it initializes/migrates without generic product seeding. If an existing
compatible file needs migration, it first makes a timestamped backup. Already migrated
files skip initialization. The existing fictional seeder is transactional and refuses
duplicate demo stores; the wrapper checks for the demo first and skips seeding on
subsequent starts. An interrupted bootstrap can resume an empty initialized database.
Existing demo rows remain unchanged; the result is checked and permissioned 0600.
Failure stops the command chain before Gunicorn. No initialization was added to Flask
imports or request handlers. Desktop behavior and financial calculations are unchanged.

Public demo mutations remain forbidden, CSRF remains required, registration is closed
and host validation uses the configured allowlist. All cash/card data is fictional and
simulated; no real card information is collected. This is not durable business hosting.

## Render Free limitations

Render Free web services cannot attach persistent disks. Their filesystem changes,
including SQLite, disappear on redeploy, restart or spin-down. A subsequent fresh
instance recreates fictional data. A process restart that retains the file preserves
it; the application never deliberately resets it. Auth sessions and rate-limit buckets
stored in SQLite can also disappear when the instance is replaced. Do not enable public
registration or business writes on this profile. Durable data requires a separately
approved hosting/storage arrangement. See the official
[Render Free limitations](https://render.com/docs/free) and
[persistent disk documentation](https://render.com/docs/disks).

## Local checks

```sh
.venv/bin/python -m pytest -q tests/test_render_startup.py tests/test_auth_isolation.py tests/test_reliability.py
.venv/bin/python -m ruff check manage.py web_app.py auth_routes.py tests/test_render_startup.py
```

Tests use new temporary demo databases, verify a fresh production import initially
fails, bootstrap twice without changing rows, then verify production import, host
validation, CSRF, closed registration, demo pages and rejected mutations. Other tests
cover initialized-but-unseeded recovery, private-data refusal, bad configuration and
corrupt-file preservation. No development data is migrated or seeded by these checks.
No commit, push, Render setting change or public deployment is performed automatically.

Verified locally: combined startup/authentication/reliability run **53 passed,
33 subtests passed** (47.04 seconds); final bootstrap file, including the subsequently
added empty-file migration-backup case, **6 passed, 5 subtests passed** (5.98 seconds).
These runs overlap. Ruff, Python compilation and diff whitespace checks passed.
Production Flask imports and HTTPS test-client requests were exercised; no live
Render service or public TLS endpoint was changed or verified.
