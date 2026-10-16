# Day 28A deployment preparation

Prepared locally; **not deployed**. Session 28B requires explicit approval of
provider, price, deployment target, and public launch. No provider is selected,
cloud resources provisioned, or hosting price represented as approved. This is a
fictional portfolio demonstration with simulated payments only.

## Audit and changes

The existing WSGI entry point is `web_app:app`. Import validates production secrets,
schema version 27, schema guards, integrity, absolute DB path and private file
permissions. It never migrates or seeds. Existing transactional migrations, SQLite
backup/restore, authentication, store isolation, CSRF, HTTPS cookie settings,
security headers and sanitized application error handlers remain in place.
No new schema, checkout engine, frontend redesign or Tkinter changes are required.

- `manage.py`: new fictional demo seeds include a customer and supplier, with
  reserved `.invalid` email addresses. New sample sales reference that customer.
  All inserts retain the existing atomic demo-store transaction. No owner account
  or password is seeded. Existing stores are never reset or silently extended.
- `tests/test_deployment.py`: disposable production WSGI page/asset, authentication,
  demo isolation, read-only, safe-error, real Gunicorn startup, missing-storage,
  migration idempotence, restart and verified restore rehearsals.
- `.env.example`, `README.md`, this report: operator guidance.

Existing populated demo stores are left unchanged. To obtain the expanded fictional
catalog, initialize a **new dedicated demo database**, never delete existing data.
The current health probe is `GET /login`; no duplicate health route was necessary.
It returns no private records. Startup validates DB readiness; `manage.py check`
provides explicit integrity/FK verification. A 200 liveness response alone cannot
detect every storage failure; monitor private disk capacity and application 503s.

## Hosting assumptions and launch blockers

Use a compatible Linux host with Python 3.12+, HTTPS termination, a private local
persistent volume, and one application instance. The DB **directory**, including
SQLite rollback journals and any future WAL/SHM files, must persist across deploys.
Do not use ephemeral build directories, object storage or a network-shared DB.
Start with one Gunicorn worker/two threads; SQLite serializes writers and uses the
existing five-second busy timeout. Do not enable WAL or add replicas casually.

Provider selection remains a launch blocker. No Render/Docker blueprint was added
without a chosen host. If Render is selected, verify its current official disk,
build/predeploy/runtime mount and health-check documentation before preparing its
configuration. Approve its persistent-disk plan and price before provisioning.
Do not run disk-dependent migrations in a build environment without that disk.

Before launch, verify actual service-user disk permissions, HTTPS redirects, exact
Host forwarding, edge request limits, backup destination/retention and operational
rollback. Do not add `ProxyFix` without establishing the exact proxy topology.
The application currently ignores forwarded client IPs; proxy requests may share
an authentication throttle bucket. No public deployment or live TLS has been tested.

## Environment and commands

`.env` is not automatically loaded. Configure these values in the approved host's
private environment/secret manager; never commit or print the secret.

| Variable | Restricted public demo setting |
| --- | --- |
| `TCCP_ENV` | `production` |
| `TCCP_SECRET_KEY` | Stable securely generated private value, at least 32 characters |
| `TCCP_DB_PATH` | Absolute persistent private path, e.g. `/var/lib/tccp/demo.db` |
| `TCCP_TRUSTED_HOSTS` | Exact approved hostnames, comma-separated, no URL scheme |
| `TCCP_READ_ONLY` | `1` |
| `TCCP_ALLOW_REGISTRATION` | `0` |
| `TCCP_DEBUG` | `0` |
| `TCCP_TIMEZONE` | `America/Chicago`, or the approved business zone |

No real customer, supplier, owner credentials or development DB may be uploaded.
The operator must create a private directory owned by the runtime user (0700)
on the selected durable volume. Database/backups must remain 0600, outside static
roots. Persistent secrets must survive process restarts for cookie/session validity.

Build/install in the selected Linux environment:

```bash
python -m pip install -r requirements.txt
python -m pip check
```

First-time initialization is an explicit maintenance action **before** WSGI startup,
with `TCCP_DB_PATH` pointing at a new dedicated fictional DB:

```bash
python manage.py init
python manage.py add-public-demo
python manage.py check
python -m gunicorn --check-config web_app:app
```

The seed command refuses a second public demo instead of resetting it. Do not put
init or seeding in the web start command. Do not launch the `__main__` development
server. Start behind an approved private HTTPS reverse proxy:

```bash
python -m gunicorn --bind 127.0.0.1:8000 --workers 1 --threads 2 --timeout 60 --error-logfile - web_app:app
```

The approved host may require a different private binding/port; validate that when
selecting it. Default Gunicorn access logging stays disabled to avoid query/PII
logging. Application failures log exception classes, not submitted credentials or
DB contents. Configure provider log retention and disk/startup alerts separately.

Public visitors enter through the CSRF-protected read-only demo action on `/login`.
They cannot register, administer private stores or complete persistent checkout.
Existing authenticated owners also have business writes blocked in this profile.
Cash/card checkout remains simulated; writable checkout testing uses isolated test
databases, never the hosted readonly demo.

## Migration, backup and recovery

Stop writers and schedule maintenance before changing schema or switching revisions.
With the intended DB environment loaded, create a new private snapshot outside the
web root and preferably outside the primary volume:

```bash
python manage.py backup --file /private-backups/tccp-before-release.db
python manage.py check
python manage.py init
python manage.py check
```

Choose a unique backup name each time. `init` also backs up an existing DB before
transactional/idempotent migrations. Nonzero exit blocks startup; preserve evidence
and source snapshots rather than resetting. `check` must report integrity `ok` and
zero FK violations. Store encrypted/private off-host backups with a documented
retention policy; same-volume backups do not protect against volume loss. Use the
SQLite online backup API via the CLI, not raw copying of an active database file.

Restore and validate a snapshot at a **new path**, without overwriting active data:

```bash
TCCP_DB_PATH=/private-recovery/restored.db python manage.py restore --file /private-backups/tccp-before-release.db
TCCP_DB_PATH=/private-recovery/restored.db python manage.py check
```

Restore checks source/output schema guards and integrity and refuses existing targets.
For a supported older backup, run explicit `init` on the restored copy with its
matching migration-capable revision. Retain the original snapshot. Only after checks
pass, stop the service, switch its DB environment to the restored path and restart
the matching application revision. Never reverse-migrate a live financial ledger.

## Local verification

Run from the repository with development dependencies installed:

```bash
.venv/bin/python -m pytest -q tests/test_deployment.py tests/test_day27.py tests/test_reliability.py
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
.venv/bin/python -m compileall -q -x '/(\.venv|\.git)/' .
.venv/bin/python -m pip check
```

The real Gunicorn test uses a temporary Unix socket; a restricted execution sandbox
may require permission for local socket creation. It starts/stops only its own
process and never binds a public listener. Tests use separate fictional DBs and
production-mode HTTPS Flask requests. Re-imports across separate processes verify
persistence; restored copies retain exact financial rows. The final suite results
are recorded below after execution.

Browser/live-provider acceptance remains for Session 28B: desktop/mobile dashboard,
charts, assets, POS presets/designer, long cart and final payment-button clearance,
customer/supplier pages, receipts, actual HTTPS cookies and rejected writes. No new
browser screenshots or visual pass are claimed by Session 28A. Existing Day 27 visual
evidence is historical; no checkout CSS/DOM was changed here. Safari/Firefox, physical
touch, screen-reader, zoom/printing and live restart/redeploy remain manual gates.

## Executed results — 2026-10-16

- Existing Day 27/reliability focused checks: **35 passed, 20 subtests passed**
  in 51.93 seconds.
- Final full suite: **266 passed, 163 subtests passed**, no failures/errors/skips,
  in **432.78 seconds**. Includes all five deployment tests and the successful real
  Gunicorn boot/login request. JUnit evidence: `/private/tmp/day28-results.xml`
  (local temporary artifact, not committed).
- Frontend harnesses: **24 + 7 + 5 = 36 passed**, zero failed.
- Ruff: passed. Python compilation: passed. `pip check`: no broken requirements
  (only an unavailable local pip-cache warning).
- Fresh `pip_audit`: **43 installed dependencies, zero known advisories**;
  local evidence `/private/tmp/day28-audit.json`. This is a point-in-time advisory
  check, not a guarantee of absence of vulnerabilities.
- Diff whitespace check: passed. Tracked sensitive-file/key-pattern scan: zero
  matches. No working database migration, reset or seeding was performed.

The initial Gunicorn socket attempt was denied by the execution sandbox; the
approved final run allowed its temporary Unix listener and passed. Production
simulation ran locally on macOS, not on the eventual Linux hosting environment.
The new WSGI test requests the listed pages, static references and persisted receipt,
checks cookies/headers/host rejection, logs in privately, switches to the isolated
demo, rejects writes and verifies a generic storage-error response. Separate-process
reads, repeated migrations and isolated restore preserve exact stored rows.

**Session 28A local preparation: complete. Public launch: not performed.**
Provider-specific configuration, price approval, durable-volume rehearsal and live
HTTPS/browser acceptance remain explicit Session 28B gates. Wait for the owner's
approval of provider, pricing, target and public launch before proceeding.
