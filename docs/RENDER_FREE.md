# Render Free: disposable, read-only fictional demo

The production failure occurs because Gunicorn imports `web_app` before a migrated
SQLite database exists at `TCCP_DB_PATH`. The production check remains intact.
The Render environment cannot be read from this repository: inspect the service's
Environment page for its current value. With no environment override, local code
defaults to the repository's `tccp.db`; production refuses a missing absolute path.
Do not upload that development database.

The merged application uses schema version **27**. Bootstrap checks the complete
ordered migration manifest rather than treating version 14 as current. `python manage.py init` backs up an existing database,
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
compatible file needs migration, it first makes a timestamped backup. Fully migrated files skip initialization. Supported older fictional demo schemas
are backed up and upgraded through the existing transactional migrations, preserving
products and sales. Every store-scoped business table is checked for non-demo data;
automatically created legacy store settings remain inaccessible to demo visitors. The existing fictional seeder is transactional and refuses
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

Verification results after merging the commercial UI and adapting the bootstrap
are recorded below. The earlier Day 14 bootstrap results do not establish correctness
of this merged application. No live Render service or public TLS endpoint is changed
or verified by local tests.

After resolving the merge from `feature/commercial-ui` into `main`, the complete
merged suite passed: **274 tests and 168 subtests**, zero failures/errors/skips,
in **454.52 seconds**. JUnit evidence is in the local temporary file
`/private/tmp/tccp-merge-results.xml`. The eight bootstrap tests include schema-14
demo upgrade to schema 27 without resetting existing sales or inventory, repeat
startup preservation, and rejection of legacy customer data. All **36 frontend
checks** passed. Ruff, compilation, dependency consistency and diff whitespace
checks passed; the tracked sensitive-file/key-pattern scan found zero matches.
The full test run permitted its temporary local Gunicorn Unix socket. No browser
visual review or live Render deployment is claimed by these merge checks.

## Slow/stuck startup diagnostics

The reported command ended with `web_app`. Gunicorn 23 interprets that as
`web_app:application`; this project defines `app`, not `application`. The bare target
fails the import check. Set the Render Start Command to the explicit target:

```sh
python -u manage.py prepare-render-demo && exec gunicorn --bind 0.0.0.0:$PORT --workers 1 --threads 2 --timeout 60 --error-logfile - web_app:app
```

Baseline local preparation returned exit 0 in 0.680 seconds for a fresh fictional
database and 0.411 seconds on restart. Existing initialization does transactional
migrations and seeding only when needed; it does not perform network calls or start
a background worker. WSGI import validates production schema/integrity and imports
the authentication module, which computes one supported scrypt dummy hash per worker.
No multi-minute initialization was reproduced on the small fictional database.

A real silent-wait defect was reproduced: the old blocking `flock` call could wait
indefinitely for another bootstrap, without printing any stage. Bootstrap now uses
nonblocking lock attempts with a 30-second deadline. Log messages are flushed at
configuration, lock, ownership/schema inspection, backup, migration, seed, integrity
and successful exit stages. No secret values, customer records, session tokens or
request data are logged. Lock timeout fails before modifying the database.
Do not delete `.bootstrap.lock`: the presence of that file is normal; an active
process holds the lock, and replacing the file can break mutual exclusion.

Startup's SQLite online backup copy also has a 30-second progress deadline, so its
automatic busy retry cannot wait indefinitely. Ordinary SQLite calls retain their
existing busy timeout. An incomplete failed backup is retained for diagnosis and
must not be restored without validation; the source database is never replaced.
No records are reset/deleted. WSGI logs on stderr identify production validation
and successful application setup; the setup timer starts after module imports.

Use **`/login`** for the Render HTTP health-check path. It is public and lightweight;
it is a liveness check after startup validation, not continuous storage monitoring.
`/health` does not exist and returns 404. Keep `TCCP_TRUSTED_HOSTS` set to the actual
public hostname (including a verified custom domain if Render uses it for probes),
without disabling host checks. Render considers 2xx/3xx responses successful and
requires a response within five seconds. See official
[health checks](https://render.com/docs/health-checks) and
[deployment troubleshooting](https://render.com/docs/troubleshooting-deploys).

Interpret the next deployment logs in order:

1. `validating demo configuration` — CLI entered.
2. `waiting for bootstrap lock` / `bootstrap lock acquired` — lock contention stage.
3. Migration/seed start and completion, or explicit skipped messages.
4. `ready; bootstrap lock released; exiting successfully` — preparation finished;
   `&&` can now launch Gunicorn.
5. Gunicorn `Listening at: http://0.0.0.0:<PORT>` and `Booting worker`.
6. WSGI `production database validated` and `application ready`.
7. Render HTTP readiness succeeds at `/login` with an allowed Host.

The single pasted log line cannot establish which stage is actually failing on
Render, nor reveal its configured health path, host allowlist, worker exits or CPU
metrics. The WSGI target error is confirmed; lock contention and health configuration
are possible additional causes, not claims about the live service. No Render
settings, resources or deployments are changed automatically by this investigation.

Final startup investigation verification: **61 passed, 32 subtests passed**, zero
failures/errors/skips, in **75.75 seconds**. The fresh Render-style shell command
completed bootstrap, bound to `0.0.0.0:$PORT`, and returned `/login` HTTP 200 in
**1.391 seconds** locally. An untrusted Host returned 400 and `/health` returned 404.
Tests reproduce the bare WSGI target failure, verify the explicit target, enforce
lock and backup deadlines, preserve source bytes on contention, preserve records
on restart, retain security checks, and exercise the isolated interactive demo.
Ruff, Python compilation and diff whitespace checks passed. JUnit evidence is the
local temporary file `/private/tmp/tccp-startup-diagnostics.xml`. These local timings
are not a performance guarantee for Render Free. No live deployment was performed.
