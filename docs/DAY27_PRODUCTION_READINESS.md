# Day 27 production readiness

**Release-candidate assessment: CONDITIONAL.** Ready for further deployment
preparation and a restricted fictional-data demo after the Day 28 conditions below.
**Not approved for real commercial operation or real payments.** All cash/card
transactions remain simulated; operating results are estimates, not audited profit.
The final evidence and per-category assessment are in `DAY27_TEST_RESULTS.md` and
`DAY27_COMPLETION_REPORT.md`.

## Required environment and hosting

Use the current `web_app:app` WSGI entry point, Python 3.12+, installed pinned/runtime
requirements and a persistent local SQLite disk. Do not use Flask's development
server publicly. Start conservatively with one Gunicorn worker and two threads:

```bash
.venv/bin/python -m gunicorn --check-config web_app:app
.venv/bin/python -m gunicorn --bind 127.0.0.1:8000 --workers 1 --threads 2 --timeout 60 --error-logfile - web_app:app
```

The reverse proxy must provide HTTPS, preserve an allowlisted Host, and prevent
public access to Gunicorn's private listener. These are deployment requirements,
not work performed by this milestone. Do not expose source, DB, backup files, debug
consoles or arbitrary filesystem mounts as static content. Do not use ephemeral or
network-shared SQLite storage or multiple application replicas against copied files.
Gunicorn support is checked locally; Linux hosting behavior still needs a rehearsal.

| Variable | Restricted demo value / rule |
| --- | --- |
| `TCCP_ENV` | `production`; only development/testing/production are accepted |
| `TCCP_SECRET_KEY` | Stable private secret, at least 32 non-whitespace characters; use a secrets manager/private environment file, never Git |
| `TCCP_DB_PATH` | Absolute existing persistent path, owner-only 0600, outside public static root |
| `TCCP_READ_ONLY` | `1`; values must be exactly 0 or 1 |
| `TCCP_ALLOW_REGISTRATION` | `0`; defaults to 0 in production, 1 otherwise; 0/1 only |
| `TCCP_DEBUG` | `0`; never launch a public debug server |
| `TCCP_TRUSTED_HOSTS` | Exact intended hostnames, comma separated, without scheme/port; default production hosts are loopback only |
| `TCCP_TIMEZONE` | Existing business zone, normally America/Chicago; do not reinterpret historical timestamps |

Existing owners may log in to a read-only deployment; public demo visitors still see
only the fictional demo store. Registration closure is independent of business-write
mode. Explicitly enabling registration is an operator choice and requires abuse
controls beyond short-window login/registration throttles. `.env` is not auto-loaded.
Production source checks do not set up a TLS certificate or trusted reverse proxy.
Do not add ProxyFix based solely on a deployment guide's example counts. Without it,
login IP buckets see the proxy's address; use edge request limits and verify the exact
forwarding topology. Keep query strings/customer searches out of public access logs.

## Safe initialization, migration and backup

Choose a new, private fictional-data database for the portfolio deployment; do not
copy local/private business records into the demo. Run maintenance with writers stopped:

```bash
# TCCP_DB_PATH must already point to the intended private database location.
.venv/bin/python manage.py init
.venv/bin/python manage.py add-public-demo
.venv/bin/python manage.py check
.venv/bin/python manage.py backup --file /private/backup/location/tccp-before-release.db
```

Replace the example backup path with an operator-controlled private directory; choose
a new filename every time. `init` backs up existing files before explicit migration.
Schema remains **27**; Day 27 adds no migration and rewrites no historical money.
The initializer is transactional/idempotent and refuses missing guards/version gaps.
Production imports never initialize/migrate an absent or old database automatically.

Backups use SQLite's online backup API, not raw copying of a busy file. Keep private
versioned off-host backups, monitor free space and test restoration periodically.
Backups may contain all store records and credentials; never put them under static
or download routes. Preserve damaged sources for diagnosis rather than resetting them.

## Recovery, rollback and failure handling

Stop writers. Restore into a **new** path with the existing command, validate, and
only then change the service's DB path/restart:

```bash
TCCP_DB_PATH=/private/new-location/restored.db .venv/bin/python manage.py restore --file /private/backup/location/verified.db
TCCP_DB_PATH=/private/new-location/restored.db .venv/bin/python manage.py check
```

Restore validates core tables, known schema guards and data integrity on both sides
of the copy. It refuses existing targets and removes its own incomplete output on
failure. It does not alter the backup. A supported older backup may require explicit
`init` after restoration; retain the original backup before that migration. Never
point an older application at a newer schema without a proven compatibility path.
Rollback requires the matching application revision and verified pre-change snapshot,
not a reverse migration against a live ledger.

SQLite lock failures return a generic retryable 503; financial operations roll back.
After a checkout response is lost, use the original-token status recovery. Never
start a replacement sale just because the browser timed out. A receipt rendering
failure does not imply the committed transaction failed: inspect history/recover the
original token. Monitor sanitized application errors and repeated 503s/429s; reduce
write contention before increasing workers. No email, crash-reporting SaaS or external
monitoring service was configured by this task.

`GET /login` is a lightweight liveness probe (200, without private data). It does
not prove DB readiness. Production startup performs schema/integrity validation;
`manage.py check` performs a read-only SQLite/FK check. Add host/HTTPS/proxy smoke
checks and private-disk/backup alerts in the selected host's operational setup.

## Day 28 deployment conditions

1. Select a host with a persistent private local disk; verify SQLite/Gunicorn and
   backup filesystem permissions with the actual service user.
2. Configure HTTPS, exact trusted hosts, stable secrets, proxy/header policy and edge
   limits. Confirm cookies/HSTS/redirects with the actual public HTTPS origin.
3. Rehearse init/backup/restore and application rollback using fictional data only;
   retain a known-good source snapshot and check disk capacity.
4. Keep registration closed and all business writes read-only for public review.
   Confirm the demo contains no private customer/local legacy data.
5. Perform Safari/Firefox, screen-reader, real touch, zoom and print review; exercise
   payment scrolling at short heights and verify graceful network recovery.
6. Re-run the documented tests/advisory scan on the final deployment build. No commit,
   push or deployment was performed by Day 27.
