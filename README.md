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

Inventory now supports store-private categories, optional unique SKUs, combined search/category/stock/visibility filters, allowlisted sorting and pagination. Use **Categories**, **Adjust / restock**, and **Stock history** from Inventory. Product forms include category, SKU and a configurable low-stock threshold (blank inherits the store default, initially 5). Dashboard alerts use those same thresholds and link to restocking.

Every quantity change is audited atomically with its operation, including checkout and desktop setters. Existing quantities receive an explicit migration baseline; no earlier stock movements are fabricated. History is append-only and private to its store. Returned-stock adjustments do not issue financial refunds.

Apply migration **15** with `python manage.py init` while writers are stopped, then run `python manage.py check`. The command backs up an existing database. Production requires migration 15 before startup. No working database is migrated automatically by this implementation.

See [Day 15 usage, isolation and recovery notes](docs/DAY15.md) for normalization rules, filters, quantity semantics, history constraints, migration verification, recovery and remaining limitations.

## Day 16 sales analytics

Open **Sales analytics** for daily, Monday-based weekly or monthly trends, Top 5/10 historical products, recorded sale totals, item sales, transaction counts, average order values and previous-period comparisons. Choose a date preset or a custom range (maximum 366 days). Charts include zero-sales periods and support mouse/keyboard inspection.

Download four CSV reports from the page: summary, period revenue, selected top products and transaction history. Each download is independently authorized and uses the same filters. Files include store/period/timezone metadata and spreadsheet formula protection. PDF is not included because no PDF infrastructure is installed.

Money totals remain integer cents. Recorded sale totals include tax; item sales use historical recorded prices before tax. The difference is labeled tax/reconciliation difference, with warnings for unverified legacy details. Refunds, voids, discounts and profit are not invented. Reporting respects the existing business-local timestamps and deployment timezone (`TCCP_TIMEZONE`); keep that setting stable for historical data.

Migration **16** adds two store-aware indexes only. Stop writers, run `python manage.py init` (which backs up an existing database), then `python manage.py check`. Production requires migration 16 before startup. Reports are bounded to 10,000 transactions per current/comparison period and 50,000 current line items; narrow the range if a report exceeds the limits.

See [Day 16 metric definitions, dates, exports and security notes](docs/DAY16.md) for reconciliation rules, DST limitations, store isolation, query limits, performance and recovery.

## Day 17 checkout, receipts and simulated refunds

Checkout now includes quantity increase/decrease, automatic authoritative total updates, category filters and product pagination. Clear-cart confirmation and normal form submission remain available. Transaction history supports status filters, date/total sorting and 20-record pages. Open a historical transaction to print its persisted receipt or save it as PDF through your browser.

**Simulated refund / return** offers selected quantities or all remaining items, a required reason, an authoritative preview and confirmation. Restocking defaults **off**; leave it off for damaged/discarded goods. Refund and optional stock restoration/audit commit atomically. Duplicate confirmations cannot refund or restock twice. Deactivated returned products stay inactive. Missing or unreconciled legacy sale details cannot be refunded.

The approved tax policy allocates original tax cents to original units using proportional largest remainders, with stable line/unit tie-breaking. Repeated partial refunds exactly reconcile to the original sale. Original gross sales stay on sale dates; refunds appear on their refund dates, and net sales including tax = gross minus refunds. Rankings, average sale and payment breakdown retain gross-sale definitions. Summary/period CSVs include refund/net metrics, with a separate refund-ledger CSV. All records, receipts and exports remain private to their authorized store. Public demo remains read-only.

Migration **17** is additive and preserves existing sale/stock history. Stop writers, run `.venv/bin/python manage.py init` (creates a backup), then `.venv/bin/python manage.py check` before starting upgraded web or desktop clients. At Day 17, production required migration 17; Days 20–21 below supersede this requirement. No working database is automatically migrated during this development task. No new dependencies are required.

See [Day 17 financial rules, workflows, security, migration and recovery](docs/DAY17.md). Earlier milestone documentation describes those milestones at completion; Day 17 supersedes their statements that refunds are unsupported or that production only requires migration 15/16.

## Day 18 UI/UX polish

The Flask interface now shares a light navy-and-blue design system: consistent cards, forms, tables, badges, alerts and responsive spacing. Navigation shows the active section and becomes a keyboard-accessible mobile menu. Read-only demo/owner pages omit business write controls while retaining server-side authorization checks.

Checkout and destructive actions use a shared confirmation dialog; cart totals remain server-calculated. Loading feedback covers forms, cart updates and CSV downloads, with duplicate-submit guards, network recovery and accessible announcements. Error summaries preserve safe input, authentication supports password visibility controls, tables/charts offer keyboard scrolling, and receipt printing remains supported.

Run `.venv/bin/python -m pytest -q` and `.venv/bin/ruff check .`. Optional dependency-free frontend interaction checks run with `node tests/frontend_checks.mjs` (Node 20+). No new Python/npm dependency or database migration is required.

See [Day 18 design system, page changes, accessibility and manual QA](docs/DAY18.md). No interactive browser was available during implementation; visual checks at 320/375/768/1024/1440px, actual screen-reader/modal behavior and print-preview QA remain manual. Automated contrast/HTML/interaction checks do not establish full WCAG compliance.

## Day 19 database and security hardening

Initialization now checks migration history against required schema/ownership/audit protections and validates SQLite integrity/foreign keys before and after changes. Missing protections, unknown versions and inconsistent ownership stop safely. Existing migration versions 14–17 are retained; **no new schema migration or financial rule is introduced**. New databases, backups and restore targets are created privately (0600); existing files are never overwritten or silently chmodded.

Production also validates the database and requires private file permissions on POSIX systems. Set `TCCP_TRUSTED_HOSTS` to the actual HTTPS hostname(s), comma separated without scheme/port; omitted/blank production settings allow only localhost/loopback. Keep the application behind a trusted HTTPS proxy and its SQLite directory private. For an existing verified production file, set `chmod 600 /private/path/tccp.db` before startup.

All unsafe browser mutations require CSRF. Current mutation endpoints accept HTML forms, not JSON: unsupported content types return 415; duplicate parameters, conflicting tokens and uploads are rejected. Inputs remain validated server-side, store IDs remain server-authorized, and exact cents/atomic checkout/refunds remain unchanged. Script CSP now rejects inline scripts/handlers; receipt/refund print buttons use external JavaScript. Error logging records operation/endpoint and exception class without raw exception messages outside explicit local debug.

Run `.venv/bin/python -m pytest -q`, `.venv/bin/ruff check .`, `.venv/bin/python -m pip_audit -r requirements.txt`, and `.venv/bin/python manage.py check`. See [Day 19 findings, security architecture, backup/recovery and verification](docs/DAY19.md) for the evidence, ownership-field compatibility, new configuration, deployment assumptions and remaining limitations. Tests and scans do not guarantee production security.

## Days 20–21 business settings and advanced analytics

Open **Settings** for store-private business/contact details, future-sale tax,
plain-text receipt customization and preview, default stock thresholds, reporting
preferences and dashboard panel visibility. New receipts and category attribution
are snapshotted at checkout. Earlier transactions are never rewritten. Blank
product thresholds now use the store default (initially 5); explicit overrides win.
Only USD and the deployment's fixed business timezone are supported.

**Sales analytics** adds hourly/daily/weekly/monthly gross–refund–net charts,
payment charts, historical category performance, product comparisons and trends,
refund quantities/cohort rates, current-stock intelligence and conservative demand
estimates. Product/category filters select **whole baskets containing matching
historical items**, including co-sold items; all accounting widgets and CSVs use
that same basket cohort. Inventory is explicitly a separate current-stock view.
New category/product/trend/comparison CSVs supplement every existing export.

Migration **20** adds settings and future-only snapshots; **21** adds analytics
indexes. These supersede older production schema requirements below. Back up,
stop writers, run `python manage.py init`, then `python manage.py check` before
starting upgraded clients. No new dependencies or environment variables are
needed. Production requires schema versions 14, 15, 16, 17, 20 and 21. This task
never migrates your working database automatically.

See the [Days 20–21 implementation report](docs/DAYS20-21.md) for exact metric,
filter, comparison and forecasting definitions, migration/recovery instructions,
file manifest, verification results and manual QA limitations.
