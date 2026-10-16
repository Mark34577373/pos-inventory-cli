# TCCP — Days 22–23 final QA report

## 1. Executive summary

Day 22 adds durable inventory condition episodes and a server-rendered notification
center, with database-trigger integration covering web, checkout, refunds, stock
adjustments and trusted desktop writes. Day 23 audits the existing application,
adds targeted reliability/security tests and reproducible local benchmarks, and
fixes confirmed presentation, query-efficiency and reporting defects. No real
payments, email/SMS, external predictions, deployments or production changes occur.

The Day 22 gate passed **122 tests and 72 subtests in 170.16 seconds**, zero
failures. The additional alert edge run passed **11 tests**. A disposable copy
migrated twice; original values across **14 existing tables** were preserved,
integrity was OK, foreign-key violations were zero and the source hash was unchanged.
Stage B began only after this gate. Final full-suite/coverage results appear below.

## 2. Scope and coverage matrix

| Area | Boundaries and actual test evidence |
|---|---|
| Registration/login | `auth_routes.py`, `services/auth.py`; `test_auth_isolation.py`: validation, scrypt, uniqueness races, atomic registration, generic failures, persistent throttling |
| Sessions/account | Same modules plus default guard in `web_app.py`; token rotation, logout/replay/expiration, cookie flags, next validation, account/cart switching |
| Settings/tax/receipts | `settings_routes.py`, `services/settings.py`, checkout service; `test_settings.py`: three stores, CSRF, invalid rates, revisions, exact half-up tax, original snapshots |
| Inventory/categories | `inventory_routes.py`, `services/inventory.py`, `inventory_management.py`; `test_inventory_management.py`, `test_inventory_view.py`: CRUD, metadata, thresholds, pagination/search, IDOR |
| Stock history | Inventory migration/audit triggers; `test_inventory_management.py`: actor/store, immutable chain, concurrent changes, rollback, desktop setters |
| Cart/checkout | `web_app.py`, `services/transaction_service.py`, desktop checkout; `test_reliability.py`, `test_sale_view.py`, `test_auth_isolation.py`: exact totals/change, stock caps, stale/duplicate/concurrent checkout |
| Transactions/receipts | `services/transaction_history.py`, receipt routes; reliability/auth/refund suites: historical names/prices/rates, filters, private IDs, print URLs |
| Refunds | `refund_routes.py`, `services/refunds.py`, immutable refund tables; `test_refunds.py`: partial/full allocation, caps, signed confirmations, retries, races, optional restock, atomic failure |
| Analytics/reports/exports | `analytics_routes.py`, analytics/detail/export services; `test_analytics.py`, `test_advanced_analytics.py`: gross/refund/net, periods/DST, cohorts, historic categories, CSV injection/reconciliation, limits |
| Alerts/notifications | `alert_routes.py`, `services/alerts.py`, migration 22; `test_alerts.py`: transitions, read/dismiss, three stores, bulk atomicity, CSRF/demo, default/override changes, rollback, recommendations |
| Error/config security | `test_security_hardening.py`, `test_final_qa.py`: redacted errors/logs, missing secrets/schema/guards, host/body/content-type/duplicate-input handling, status preservation |
| Database/recovery | All migrations, validation, `manage.py`; reliability/security/alerts suites: initialization, idempotency, FK/unique constraints, actual backup/restore, missing-product repair, rejected corruption |
| UI/accessibility | `test_ui.py`: all page families including notification list/detail, labels, IDs, headings, table scope, CSRF forms, read-only actions and contrast pairs. JS checks execute actual scripts against DOM doubles |
| Demo | Auth, inventory, settings, analytics, refund, UI and alert suites: separate fictional store, account switching, read-only writes, no private records |
| Desktop/CLI | `test_dashboard.py`, `test_inventory_view.py`, `test_sale_view.py`, `test_reports_view.py`, reliability tests: actual data helpers/checkout, mocked Tk windows |
| QA gaps addressed | `test_final_qa.py`: request-local threshold reuse/invalidation/store key, notification input/error paths, redacted lock errors, missing alert guard, large legacy totals and exact desktop average |

Fixtures use temporary SQLite paths and fictional accounts. Each fixture restores
DB_FILE and Flask configuration; desktop cart state is cleared where applicable.
Financial/authorization tests use real Flask sessions, SQL and service operations.
Mocks deliberately cover window creation, injected failures, clocks and browser
DOMs; they do not replace the critical store/ledger SQL. There is no external data
or real card information. No tests were removed, skipped or weakened. Exact schema
version assertions advance to 22, and the anonymous route sweep handles alert IDs.

## 3. Test environment

Local macOS 26.6.2 x86_64; Python 3.14.7; SQLite 3.50.4; Flask 3.1.3;
Werkzeug 3.1.9; pytest 9.1.1; Ruff 0.17.1; Gunicorn 23.0.0;
coverage.py 7.16.2 (development-only). CI remains Python 3.12 with Tkinter import
support and now runs coverage plus Node 20 frontend checks, with contents-only
read permission. CI configuration was reviewed; no remote CI run was triggered.

## 4. Commands and reproducibility

```
.venv/bin/python -m pytest -q -p no:cacheprovider
COVERAGE_FILE=/private/tmp/tccp23-final.coverage .venv/bin/python -m coverage run -m pytest -q -p no:cacheprovider
COVERAGE_FILE=/private/tmp/tccp23-final.coverage .venv/bin/python -m coverage report
COVERAGE_FILE=/private/tmp/tccp23-final.coverage .venv/bin/python -m coverage json -o /private/tmp/tccp23-coverage.json
.venv/bin/python -m ruff check .
node tests/frontend_checks.mjs
PYTHONPATH=. .venv/bin/python scripts/qa_benchmark.py
.venv/bin/python -m pip check
.venv/bin/python -m pip_audit --cache-dir /private/tmp/tccp23-audit
.venv/bin/python -m gunicorn --check-config web_app:app
```

The frontend entry point was imported and `runChecks()` invoked using the available
Node tool runtime; no shell Node executable/browser was exposed. The equivalent
local command is listed above. Coverage artifacts are ignored; the measured local
files live under /private/tmp. Benchmark code always creates disposable databases
and restores process configuration; it never opens the working database.

## 5. Financial reconciliation and confirmed fixes

Integer cents remain authoritative, including exact store-specific Decimal tax
and half-up cent rounding. Checkout still validates owned active prices/stock and
commits sale/items, stock/audit and alerts atomically. Refund allocation remains
the approved original-tax largest-remainder policy; no financial business rule was
changed. Repeated partial refunds reconcile exactly; restock is optional and
idempotent. Gross stays on sale date; refunds on persisted refund business date;
net is gross minus refunds, not profit. Category/product merchandise excludes tax;
filters select whole matching historical baskets, including co-sold items. Rate,
receipt and category changes never rewrite completed sale snapshots.

Confirmed and fixed during Stage B:

- **MEDIUM, resolved — report overflow:** a real HTTP regression fixture with two
  individually valid large legacy cent totals returned 503 from SQLite SUM.
  Dashboard/daily readers now fetch at most 10,000 rows through the store/time
  index and aggregate with Python integers; excessive windows return a clear
  limit response instead of truncating or overflowing.
- **MEDIUM, resolved — desktop average display:** bankers rounding could disagree
  with the web's half-up policy at half-cent averages. Shared sales totals now
  expose exact fractional-cent statistics (`Fraction`, replacing the old float)
  and a separate integer `average_sale_rounded_cents` for display. Existing numeric
  statistic assertions remain; money display uses the exact rounded field.
  Desktop views handle the bounded-reader limit with a helpful message.
- **MEDIUM, resolved — dashboard N+1:** defaults were reread per product. A
  request-local, store-keyed threshold value now refreshes after a settings save
  and on the next request. Authoritative financial settings are never cached.
- **LOW, resolved — unread index choice:** a covering partial unread index now
  supplies the count query; actual EXPLAIN results confirm its use.

Stage A also caught/fixed a macro import context error and current product names
in filtered/historical shared shells. Regression assertions were preserved. The
notification bell uses IDs/types on those views; the center and dashboard show
recorded names. Inactive products remain in history, not active-product previews.

## 6. Inventory and database integrity

One open episode per store/product is enforced by a partial unique index. Composite
FKs prevent foreign product/store pairs. Type/severity/quantity checks, immutable
identity/resolved snapshots and no-delete guards protect history. SQL triggers
resolve/recreate episodes inside the same inventory transaction; forced alert
failures roll back checkout, refund restock, settings revisions and audits.

Current unhealthy products receive initialization conditions in migration 22;
there is no historical reconstruction. Read/dismiss never mutates stock. Inactive
products resolve alerts; reactivation creates a new condition where appropriate.
Threshold changes preserve overrides and quantities. Tests verify zero threshold,
severity escalation, dismissal recurrence, simultaneous stock changes and retries.

Ordinary integrity checks remain strict. Explicit trusted missing-product repair
also recognizes missing inventory_alerts product references, but still rejects
cross-store FK mismatches. Recovery creates inactive, explicitly unknown historical
placeholders and retains original ledger/snapshot values. Backup/restore tests
refuse to overwrite existing destinations. Migration is forbidden inside HTTP.

The disposable source-copy migration check passed twice through versions
14,15,16,17,20,21,22, preserving all original values across 14 tables. All three
benchmark databases also returned integrity OK and zero FK violations after real
concurrent checkout/refund operations. Working/production data was not migrated.

## 7. Three-account isolation

A/B/C fixtures cover products/categories, adjustments, thresholds, alerts/history,
carts, transactions/receipts/refunds, dashboards, analytics, exports, account/store
settings, tax and receipt preferences. Real HTTP and service calls reject foreign
record IDs and mixed-owner bulk actions. Failed requests preserve database state.
Session switching revokes the old login/cart context; public demo loads a distinct
fictional store. No report or notification cache is shared across stores.

Existing inventory endpoints ignore supplied ownership fields and authorize using
server context; new settings/alert mutations explicitly reject those fields.
Neither behavior lets a client choose a store. Alert/query data boundaries always
use authorized_store_id(); composite FKs additionally enforce persisted ownership.
The trusted desktop/CLI remains fixed to legacy store 1 and requires local file
access; it is not a public authentication bypass.

## 8. Authentication, security and configuration findings

Scrypt hashes, generic login failures/dummy verification, atomic normalized-email
uniqueness and SQLite-backed account/IP throttles remain tested. Login clears old
session state and establishes a random server-revocable token; the database stores
its hash. Logout is POST+CSRF and revokes the token; fixed eight-hour expiry and
membership joins remain enforced. Flask's signed cookie is integrity protected,
not encrypted: it contains the opaque bearer token, CSRF/cart state, no credentials.
HTTPS, HttpOnly/Secure/SameSite and a private, stable production secret are required.

Production fails closed on weak/missing secrets, missing/mismatched schema or unsafe
POSIX file permissions; debug is disabled. Trusted hosts, body/query/form limits,
form-only mutations, duplicate/conflicting parameters, uploads, safe local next
URLs, CSRF, CSP, no-store, frame/nosniff/HSTS and permissions headers remain covered.
SQL values are parameterized, dynamic columns/orders are fixed allowlists, Jinja
escapes plain-text settings/names and CSV text neutralizes spreadsheet formulas.
Unknown/foreign IDs receive generic errors. Logs/responses redact exception details.

Review reference: [Flask security guidance](https://flask.palletsprojects.com/en/stable/web-security/).
Installed dependencies, including the development tools, were audited with
pip-audit after authorized network access: **no known vulnerabilities found**.
`pip check` found no broken requirements. No runtime dependency was added/upgraded;
coverage.py is development-only. The final tracked-file/ignore review checks
secrets, SQLite/backup variants and generated coverage files. This is not a
penetration-test certification or a guarantee of zero vulnerabilities.

## 9. UI/accessibility and manual limits

Notification list/detail join semantic/read-only regression sweeps: one main/h1,
unique IDs, visible/nested labels, scoped table headings, nonnested CSRF forms,
valid navigation and appropriate error codes. Severity is text as well as color.
Native details/summary supplies a keyboard-operable bell; timestamps and condition
status distinguish resolved history from current urgency. POST controls reuse
existing loading, confirmation and recovery behavior. Read-only views omit actions.
No aggressive polling, animation, external notification delivery or new chart
library was added. Existing actual-script DOM checks pass separately.

No browser or native display is enabled (`apps:[], browsers:[]`). Therefore
320/375/768/1024/1440px layout, actual focus/screen-reader behavior, native bell
interaction, mobile popover placement and print preview remain **unverified manual
checks**. CSS breakpoints and semantic tests are evidence, not browser visual QA or
WCAG certification. Real Tkinter window rendering remains manual.

## 10. Restock estimates

Read-only estimates use 30 complete business days with known opening history;
seven-/30-day gross sold units are shown. Net physical depletion subtracts only
units actually restocked by refunds on refund date, clamped at zero. Positive
covered demand yields daily depletion /30, stock/daily days remaining, and
max(threshold+1,ceil(14×daily)) target stock; suggested reorder is target minus
stock, floored at zero. Targets above the stock limit produce no suggestion.
Zero demand, inactive products or insufficient history yields N/A. These derived
estimates never write stock or generate repetitive daily alert events. Fixed
14-day coverage is illustrative, not supplier lead time or an AI prediction.
See DAY22.md for complete lifecycle/migration/formula definitions.

## 11. Operational risks and deployment gate

| Severity | Remaining item / release action |
|---|---|
| CRITICAL | No unresolved critical defect established by the completed checks; this is not proof that none exists |
| HIGH | No unresolved high authorization/financial/data-integrity failure established by the completed checks |
| MEDIUM | Browser/device/accessibility/print and actual desktop-window QA unavailable locally; perform before public launch |
| MEDIUM | Operator must migrate a verified backup while writers are stopped, configure real HTTPS/trusted hosts/stable secret/private file and directory permissions, and rehearse recovery on the host |
| LOW | Password change/recovery, email verification, MFA, staff roles and remote email/SMS are unavailable; keep portfolio/demo scope explicit |
| LOW | Historical categories/profiles/tax that were never recorded stay unknown/unverified; timezone stays fixed and currency is USD |
| LOW | SQLite needs one persistent local-disk application instance. No distributed, sustained hosted-load, external penetration or live TLS test was performed |
| LOW | Coverage excludes independently spawned subprocess execution and real browser/Tk painting; line percentages do not establish security |

No automatic commit, push or deployment was performed. Deployment readiness is a
review of the Day 24 entry conditions, not a claim that the current working database
or a hosted service is already upgraded. Production intentionally requires version
22. Back up, stop writers, explicitly initialize/check a verified database copy,
then perform the documented host and UI smoke checks before public traffic.

## 12. Performance measurements

Actual local median milliseconds across three real Flask GETs per endpoint; fixture setup is excluded from request timings. These are temporary fictional datasets, not a sustained hosted load test or SLA.

| Operation | 30 products / 100 sales | 300 / 1,000 | 1,500 / 3,000 |
|---|---:|---:|---:|
| dashboard | 39.809 | 48.99 | 62.545 |
| inventory search | 27.26 | 26.383 | 27.874 |
| transaction page | 18.235 | 20.593 | 27.669 |
| analytics | 26.936 | 51.902 | 124.182 |
| csv | 12.61 | 36.269 | 105.39 |
| notification filter | 18.735 | 19.323 | 22.3 |
| notification summary | 3.45 | 3.674 | 3.438 |
| four concurrent buyers | 44.176 | 65.584 | 44.941 |
| two duplicate refunds | 15.308 | 16.365 | 16.214 |

Every scenario admitted exactly two of four buyers competing for two units, and two identical concurrent refund requests created one refund/restock. Final stock was one, all integrity checks were OK and FK violations were zero. Concurrency timings cover the entire request group, not a median of individual writes.

The pre-fix 1,500-product dashboard median was 5,221.516 ms. The final indexed/bounded version above demonstrates the measured improvement. EXPLAIN uses `alerts_unread` as a covering index for unread counts and `alerts_store_kind` for kind pagination. No cross-store cache or aggressive polling was introduced.

## 13. Application inventory: routes

All business endpoints below use the default verified-store guard. Unsafe methods additionally require CSRF and reject demo/read-only writes. Auth forms have their own validation/throttle/session rules; static assets are public. Notification actions expose HTML forms, not a new JSON mutation API.

| Endpoint | Methods | Path |
|---|---|---|
| `static` | GET | `/static/<path:filename>` |
| `alerts.overview` | GET | `/notifications` |
| `alerts.detail` | GET | `/notifications/<int:alert_id>` |
| `alerts.action` | POST | `/notifications/<int:alert_id>/<action>` |
| `alerts.read_page` | POST | `/notifications/read-page` |
| `settings.overview` | GET | `/settings` |
| `settings.save` | POST | `/settings/<section>` |
| `auth.register` | GET, POST | `/register` |
| `auth.login` | GET, POST | `/login` |
| `auth.logout` | POST | `/logout` |
| `auth.demo` | POST | `/demo` |
| `auth.account` | GET | `/account` |
| `analytics.overview` | GET | `/analytics` |
| `analytics.download` | GET | `/analytics/export/<kind>.csv` |
| `inventory_tools.categories` | GET, POST | `/inventory/categories` |
| `inventory_tools.rename_category` | POST | `/inventory/categories/<int:category_id>/rename` |
| `inventory_tools.remove_category` | POST | `/inventory/categories/<int:category_id>/remove` |
| `inventory_tools.adjust` | GET, POST | `/inventory/adjust/<int:product_id>` |
| `inventory_tools.history` | GET | `/inventory/history` |
| `inventory_tools.history_detail` | GET | `/inventory/history/<int:adjustment_id>` |
| `refunds.refund_form` | GET, POST | `/transactions/<int:transaction_id>/refund` |
| `refunds.detail` | GET | `/refunds/<int:refund_id>` |
| `dashboard` | GET | `/` |
| `inventory` | GET | `/inventory` |
| `add_product` | GET, POST | `/products/new` |
| `edit_product` | GET, POST | `/products/<int:product_id>/edit` |
| `update_quantity` | POST | `/products/<int:product_id>/quantity` |
| `remove_product` | POST | `/products/<int:product_id>/deactivate` |
| `sale` | GET | `/sale` |
| `sale_add` | POST | `/sale/add` |
| `sale_update` | POST | `/sale/update/<int:product_id>` |
| `sale_remove` | POST | `/sale/remove/<int:product_id>` |
| `sale_cancel` | POST | `/sale/cancel` |
| `sale_checkout` | POST | `/sale/checkout` |
| `receipt` | GET | `/receipt` |
| `transaction_receipt` | GET | `/transactions/<int:transaction_id>/receipt` |
| `transactions` | GET | `/transactions` |
| `transaction_detail` | GET | `/transactions/<int:transaction_id>` |
| `reports` | GET | `/reports` |

39 registered route rules, including static. Service-function inventory follows. Plain models hold data and the desktop cart; business SQL remains in scoped services/migrations.

```text
services/alerts.py : filters_from, notification_summary, list_alerts, get_alert, update_alert, mark_page_read, recommendation
services/analytics.py : ReportLimitError, ReportFilters, strict_date, resolve_filters, cohort_sql, _refunds, _transactions, _average, comparison, _bucket, _series, _hourly_series, build_analytics
services/analytics_details.py : _percent, build_details
services/auth.py : normalize_email, register_user, authenticate, digest_token, revoke_session, create_login_session, load_identity, consume_rate_limit, assign_legacy_owner
services/checkout.py : add_to_cart, remove_from_cart, change_cart_quantity, view_cart, checkout, cancel_sale, start_sale
services/inventory.py : _product_from_row, list_active_products, search_active_products, find_product_by_id, find_products_by_ids, get_price, get_quantity, create_product, update_product_details, set_product_quantity, deactivate_product, remove_product_by_id, remove_product_by_name, display_product, view_inventory, add_product, remove_product, update_product, search_product, update_product_record
services/inventory_management.py : category_key, category_name, positive_id, whole_number, stock_status, list_categories, save_category, delete_category, clean_metadata, inventory_filters, query_inventory, adjust_stock, history_filters, stock_history
services/money.py : to_cents, from_cents, format_cents, calculate_tax_cents, parse_cash
services/refunds.py : valid_id, sale_state, get_sale, normalize_request, quote_refund, preview_refund, process_refund, get_refund
services/sales_exports.py : spreadsheet_text, amount, create_csv
services/sales_totals.py : read_sales, summarize_sales
services/settings.py : get_settings, tax_rate, default_threshold, receipt_snapshot, read_receipt_snapshot, save_settings
services/transaction_history.py : query_transactions
services/transaction_service.py : complete_transaction, view_sales_report
services/validation.py : visible_text
database/advanced_analytics_migration.py : migrate_advanced_analytics
database/alerts_migration.py : reconcile_sql, migrate_alerts
database/analytics_migration.py : migrate_analytics
database/database.py : InventoryConnection, get_db_connection, _ensure_column, initialize_database, _initialize_schema, get_store_connection
database/inventory_migration.py : migrate_inventory
database/migrations.py : migrate_identity
database/refund_migration.py : migrate_refunds
database/settings_migration.py : migrate_settings
database/tenancy.py : current_store_id, require_store_write
database/validation.py : validate_schema, validate_integrity
models/cart.py : Cart
models/product.py : Product
models/transaction.py : Transaction
ui/__init__.py :
ui/app.py : TCCPApplication
ui/dashboard.py : get_dashboard_data, create_dashboard_view
ui/dialogs.py : create_modal_window
ui/inventory_view.py : get_inventory_products, get_stock_display, _clear_inventory_view, create_inventory_view, open_stock_dialog, _submit_stock_quantity, open_edit_product_dialog, _submit_product_edit, open_add_product_dialog, open_remove_product_dialog, _submit_remove_product, _submit_new_product
ui/reports_view.py : get_today_sales_summary, get_today_best_seller, create_reports_view
ui/sale_view.py : create_sale_view, refresh_ui_product_display, handle_add_item, handle_increase_cart_item, handle_decrease_cart_item, refresh_ui_cart_display, handle_remove_cart_item, handle_clear_cart, render_totals_grid, handle_ui_checkout, show_receipt_window
```

## 14. Table and ownership inventory

| Table | Purpose / security boundary |
|---|---|
| schema_migrations | Trusted maintenance version ledger; no HTTP migration |
| users | Normalized unique identity and scrypt hash; auth-only lookup |
| stores | Stable business/demo/legacy identity; explicit membership access |
| store_memberships | Explicit user/store owner relationship; no client assignment |
| auth_sessions | Revocable hashed token, membership FK and fixed expiry |
| auth_limits | HMAC account/IP buckets, persistent shared SQLite throttling |
| products | Store-owned current stock/price, category/SKU/threshold; integer/numeric guards |
| categories | Store-private normalized unique names; deletion unassigns current products |
| transactions | Scoped original sale totals/rate/receipt snapshot/idempotency; read-only HTTP history |
| transaction_items | Composite sale/product/store consistency; immutable historical name/price/category attribution through app |
| inventory_baselines | Immutable opening snapshot, not invented earlier stock history |
| stock_adjustments | Immutable scoped stock chain, actor/sale/refund links and arithmetic guards |
| refunds | Immutable owned ledger, original-sale relationship, durable request key/amount cap |
| refund_items | Composite refund/original-item/store relationship, original-unit quantity cap |
| store_settings | One FK-constrained settings row per store; revision and value constraints |
| inventory_alerts | Composite owned product relationship, one active episode, history guards and read/dismiss timestamps |

All business SQL uses store-derived predicates or validated composite ownership;
identity/maintenance SQL has explicitly different trusted boundaries. Model classes
are plain data/cart holders. Desktop/CLI is trusted local legacy-store code.

## 15. Changed files

Created: `alert_routes.py`, `database/alerts_migration.py`, `services/alerts.py`,
`services/sales_totals.py`, `templates/notifications.html`,
`templates/notification_detail.html`, `templates/components/notifications.html`,
`tests/test_alerts.py`, `tests/test_final_qa.py`, `scripts/qa_benchmark.py`,
`docs/DAY22.md`, `docs/FINAL_QA_REPORT.md`.

Modified: `database/database.py`, `database/validation.py`, `web_app.py`,
`services/settings.py`, `ui/dashboard.py`, `ui/reports_view.py`,
`templates/base.html`, `dashboard.html`, `inventory.html`, `settings.html`,
`static/css/design-system.css`, `tests/test_ui.py`, `tests/test_auth_isolation.py`,
`tests/test_analytics.py`, `tests/test_refunds.py`, `tests/test_security_hardening.py`,
`.github/workflows/tests.yml`, `.gitignore`, `pyproject.toml`,
`requirements-dev.txt`, `README.md`. Runtime requirements/environment variables
are unchanged. No private database, credential, coverage output or export is added.

## 16. Actual final test and coverage results

- Full instrumented suite: **198 passed, 91 subtests passed, 0 failed, 0 skipped in 268.76 seconds** using the coverage/pytest command above. This retains all 180 earlier tests and adds 11 alert plus 7 final-QA tests.
- Final targeted branch/boundary rerun: **8 passed, 10 subtests passed in 16.80 seconds**, appended to coverage. These retained all prior assertions and added explicit row-limit, recommendation-limit and filter-state checks.
- Targeted reporting regression run: **38 passed, 22 subtests passed** after the reporting fixes.
- Day 22 gate: **122 passed, 72 subtests passed** before Stage B.
- Actual-script frontend checks: **20 passed, 0 failed** via the Node runtime. No browser visual execution was available.
- Ruff, Python compilation, Gunicorn configuration, pip consistency and final diff whitespace checks passed. Dependency advisory scan: **no known vulnerabilities found**.
- Temporary-copy initialization twice preserved every original column value in 14 tables, integrity OK, zero FK violations and unchanged original hash. The final migration version set is 14,15,16,17,20,21,22.

Coverage is measured over application code (tests, benchmark scripts and virtualenv excluded), including unexecuted desktop/CLI code. It includes threads, but not independently spawned production/CLI subprocesses. JavaScript, HTML/CSS/layout and real Tk window painting are separate from Python coverage. Functional subprocess tests still run; their source execution is simply absent from these percentages. One empty Python file was omitted by coverage reporting; no test was skipped.

Overall: **2,781/3,944 statements (70.51%)**, **778/1,124 branches (69.22%)**; combined coverage **70.22%**. No application source lines were manually excluded.

| Critical module | Statement coverage | Branch coverage | Combined |
|---|---:|---:|---:|
| `database/alerts_migration.py` | 100.00% | 100.00% | 100.00% |
| `database/database.py` | 97.98% | 92.86% | 96.85% |
| `database/tenancy.py` | 100.00% | 100.00% | 100.00% |
| `services/auth.py` | 97.27% | 87.50% | 95.52% |
| `services/alerts.py` | 100.00% | 100.00% | 100.00% |
| `services/transaction_service.py` | 92.73% | 78.12% | 89.44% |
| `services/refunds.py` | 94.31% | 85.42% | 91.81% |
| `services/money.py` | 93.33% | 100.00% | 94.44% |
| `services/sales_totals.py` | 100.00% | 100.00% | 100.00% |
| `services/inventory_management.py` | 97.52% | 93.75% | 96.45% |
| `services/settings.py` | 90.72% | 85.42% | 88.97% |
| `services/analytics.py` | 95.91% | 92.31% | 94.91% |
| `services/analytics_details.py` | 96.26% | 86.11% | 93.71% |
| `services/sales_exports.py` | 98.31% | 97.50% | 97.98% |
| `web_app.py` | 82.10% | 75.00% | 80.66% |

Residual coverage gaps are concentrated in interactive legacy CLI menus, unused
plain DTO models, actual Tk window construction and some defensive/error branches.
Production bootstrap branches execute in independent subprocess tests and therefore
also reduce the measured parent-process percentage. The new exact sales-reader
limit and recommendation limit have explicit tests. Real browser/Tk rendering,
hosted load/TLS and external penetration testing remain untested workflows, not
claims of completed coverage. No untested critical financial/authorization workflow
was identified by this audit; finite tests and line percentages cannot prove that
all defects or vulnerabilities have been found.

## 17. Deployment-readiness decision

**Day 22: COMPLETE. Day 23: COMPLETE. Overall milestone: COMPLETE.**

**Day 24 readiness: READY for controlled portfolio deployment preparation.**
The required application gates pass: no unresolved critical/high finding from this
review, no failing financial/data-integrity workflow, safe repeatable migrations,
three-account isolation, isolated read-only demo, fail-closed production settings,
private-file/secret guidance and tested backup/recovery behavior. The current
working database is deliberately not upgraded and no hosted deployment is claimed.

Before public traffic, the operator must migrate/check a verified backup with
writers stopped, configure the actual persistent disk/HTTPS/trusted hosts/stable
secret/private permissions, rehearse host recovery and complete the listed manual
browser/desktop smoke checks. If a critical/high defect is found in those checks,
block launch and resolve it before deployment. This decision does not certify tax
compliance, accessibility conformance, PCI compliance or zero vulnerabilities.
