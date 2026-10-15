# TCCP — The Credit Card Processor

A Flask and SQLite point-of-sale portfolio application. **All payments are simulated. Use fictional demonstration data only.** No card numbers or real payments are collected or processed.

The existing Tkinter desktop application remains available through `python main.py`. The web application runs through `web_app.py`.

## Features

- Dashboard with today's sales, seven days of revenue, recent transactions, and stock indicators.
- Product search, creation, atomic edits, restocking, and soft deactivation.
- Session carts, simulated cash/card checkout, cash change, printable receipts.
- Transaction history and date/payment filters; daily, payment, and product reports.
- Exact integer-cent totals, Decimal half-up tax rounding, transactional stock deductions.
- CSRF protection, durable checkout tokens, database-backed receipts, read-only public demo mode.
- Explicit migration, integrity checks, backups, restoration, and fictional demo seeding.
- Registration, login, revocable sessions, persistent login throttling, and isolated private stores.

## Local setup

Use Python 3.12 or newer with Tkinter available (desktop tests mock windows; they still import Tkinter).

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
export TCCP_DB_PATH="$PWD/demo.db"
python manage.py seed-demo
python web_app.py
```

Open http://127.0.0.1:5000. Register an account to get a new empty private store, or use **Explore the read-only demo** on the login page. Sign out with the POST button in the header. Use a 15–128 character password/passphrase; no real card details are needed. Demo seeding requires an empty database and creates fictional products and six sales over seven days, including a zero-sale day and low/out-of-stock examples. It never resets an existing database. Choose a new database path to start again.

To use an existing database, run `python manage.py init` instead of seeding. Existing databases are backed up before migration. `init` creates schema only; it does not insert demonstration data. Direct development startup retains the original two sample products for a new default database.

Configuration is read from environment variables; `.env.example` documents them. A local secret is generated when none is supplied, so restarting without a stable secret clears browser sessions. `TCCP_TIMEZONE` defaults to `America/Chicago`; timestamps use this business timezone. Existing naive historical timestamps retain their original meaning and are not rewritten.

## Automated validation

```bash
python -m pytest -q
python -m ruff check .
python -m pip_audit --cache-dir /tmp/tccp-pip-audit
# Standard-library alternative:
python -B -m unittest discover -s tests -v
python manage.py check
```

Tests use disposable databases, never your working database. Coverage includes web routes, invalid inputs, money rounding, forced rollback, concurrent buyers, duplicate requests, CSRF, read-only access, receipt snapshots, legacy migrations, backups, orphan repair, CLI and desktop behavior.

The database mirrors legacy REAL fields for compatibility; integer cents are authoritative. New writes must provide cent values. Validation triggers protect legacy and fresh schemas without deleting or rewriting historical sales.

## Backups and recovery

```bash
python manage.py backup --file backups/tccp-before-change.db
python manage.py check
```

Backup uses SQLite's online backup API and refuses to overwrite an existing file. Backups may contain data: keep them private and off the public web root.

Restore to a **new path**, validate it, and only then point the stopped application at it:

```bash
TCCP_DB_PATH="$PWD/restored.db" python manage.py restore --file backups/tccp-before-change.db
TCCP_DB_PATH="$PWD/restored.db" python manage.py check
```

`python manage.py repair-missing-products` creates a backup, migrates, and restores missing product references using inactive placeholders explicitly named `Unavailable historical product #ID`. A one-cent placeholder price is structural only; original transaction items, snapshot names, prices, quantities, and totals are untouched. It does not infer historical identity or repair unrelated corruption. Review the printed integrity result; a nonzero exit means more investigation is required.

## Safe public portfolio demo

Use a separate seeded SQLite database on a persistent writable disk. Run migration once before starting workers, never concurrently as a startup side effect.

```bash
export TCCP_ENV=production
export TCCP_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
export TCCP_DB_PATH="/absolute/persistent/path/tccp-demo.db"
export TCCP_READ_ONLY=1
python manage.py check
python -m gunicorn --workers 1 --threads 4 --bind 127.0.0.1:8000 web_app:app
```

Persist the secret securely across restarts. Production refuses a missing/short secret, uses Secure/HttpOnly/SameSite cookies, and defaults to read-only. Serve it behind HTTPS and a reverse proxy. Do not expose the Flask development server or debugger publicly. SQLite requires a persistent local filesystem; use one application instance rather than shared network storage or ephemeral serverless disks.

Registration and login now isolate each account’s store. Public demo sessions only read a separate fictional demo store and can never modify it. `TCCP_READ_ONLY=1` additionally blocks business writes for authenticated owners; set it to `0` to enable private-store writes. Authentication forms remain available in either mode. No real payment integration is included.

## Portfolio review checklist

- Run tests and both database integrity checks before deployment.
- Inspect dashboard at desktop and phone widths: seven separate columns and full-width blue bars; chart scrolls on narrow screens.
- Check keyboard navigation, long product names, zero-sale states, form feedback, cart cancellation, and receipt print preview.
- Walk through a simulated cash/card sale in the private demo; verify dashboard, history, receipt, and report totals agree.
- Add screenshots from fictional data and a hosted URL after deployment; hosting and external HTTPS configuration are not performed by this repository.

## Layout

`web_app.py` contains Flask routes; `services/` contains inventory, money, checkout and persistence logic; `database/database.py` manages SQLite schema and connections. `templates/` and `static/` hold the web UI. `ui/` and `main.py` retain the desktop application. `manage.py` provides explicit database lifecycle commands. `tests/` contains regression and integration checks.


## Day 14 migration and legacy ownership

Stop application writers, back up, migrate explicitly, and validate before starting workers:

```bash
python manage.py init
python manage.py check
```

The versioned additive migration preserves existing rows in an **unclaimed legacy store**. Registration never claims those records. A registered owner can be assigned deliberately by a trusted local administrator:

```bash
python manage.py assign-legacy-owner --email owner@example.com
```

This command creates a backup, refuses reassignment, and revokes existing logins for that user. Sign in again to access the legacy store. The original empty private store remains intact. Desktop/CLI access remains fixed to legacy store 1; it is a trusted local application with filesystem access, not a public authentication bypass. Do not share the database file with web users.

To add a fictional public demo to an existing migrated database:

```bash
python manage.py add-public-demo
```

Set `TCCP_DB_PATH` explicitly first. No real data is copied into the demo; existing demo data is never reset. `seed-demo` still requires an empty business database.

Authentication and throttling persist in SQLite across workers and restarts; keep the production secret stable, use HTTPS and a private persistent local disk, and do not log credentials or cookies. Local development uses the same limiter. Passwords use Werkzeug scrypt; new passphrases must contain 15–128 characters. Email validation checks format, not mailbox ownership. There is no email verification, recovery, MFA, staff invitation, or multi-store switcher yet.

See [Day 14 design and security notes](docs/DAY14.md) for the route audit, ownership constraints, migration details, limiter settings, demo behavior, security references, and deployment limitations.

## Day 15 inventory management

Inventory now supports store-private categories, optional unique SKUs, combined search/category/stock/visibility filters, allowlisted sorting and pagination. Use **Categories**, **Adjust / restock**, and **Stock history** from Inventory. Product forms include category, SKU and a configurable low-stock threshold (blank defaults to 5). Dashboard alerts use those same thresholds and link to restocking.

Every quantity change is audited atomically with its operation, including checkout and desktop setters. Existing quantities receive an explicit migration baseline; no earlier stock movements are fabricated. History is append-only and private to its store. Returned-stock adjustments do not issue financial refunds.

Apply migration **15** with `python manage.py init` while writers are stopped, then run `python manage.py check`. The command backs up an existing database. Production requires migration 15 before startup. No working database is migrated automatically by this implementation.

See [Day 15 usage, isolation and recovery notes](docs/DAY15.md) for normalization rules, filters, quantity semantics, history constraints, migration verification, recovery and remaining limitations.
