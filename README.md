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

Open http://127.0.0.1:5000. Demo seeding requires an empty database and creates fictional products and six sales over seven days, including a zero-sale day and low/out-of-stock examples. It never resets an existing database. Choose a new database path to start again.

To use an existing database, run `python manage.py init` instead of seeding. Existing databases are backed up before migration. `init` creates schema only; it does not insert demonstration data. Direct development startup retains the original two sample products for a new default database.

Configuration is read from environment variables; `.env.example` documents them. A local secret is generated when none is supplied, so restarting without a stable secret clears browser sessions. `TCCP_TIMEZONE` defaults to `America/Chicago`; timestamps use this business timezone. Existing naive historical timestamps retain their original meaning and are not rewritten.

## Day 13 validation

```bash
python -m pytest -q
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

There is no user authentication or per-visitor database isolation. Public deployments should retain read-only mode. Demonstrate writes locally or in a restricted environment; never enable anonymous shared writes against meaningful inventory. Public mode keeps forms visible but rejects all POST actions. No real payment integration is included.

## Portfolio review checklist

- Run tests and both database integrity checks before deployment.
- Inspect dashboard at desktop and phone widths: seven separate columns and full-width blue bars; chart scrolls on narrow screens.
- Check keyboard navigation, long product names, zero-sale states, form feedback, cart cancellation, and receipt print preview.
- Walk through a simulated cash/card sale in the private demo; verify dashboard, history, receipt, and report totals agree.
- Add screenshots from fictional data and a hosted URL after deployment; hosting and external HTTPS configuration are not performed by this repository.

## Layout

`web_app.py` contains Flask routes; `services/` contains inventory, money, checkout and persistence logic; `database/database.py` manages SQLite schema and connections. `templates/` and `static/` hold the web UI. `ui/` and `main.py` retain the desktop application. `manage.py` provides explicit database lifecycle commands. `tests/` contains regression and integration checks.
