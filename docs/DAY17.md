# Day 17 — checkout, historical receipts and simulated refunds

## Checkout lifecycle and audit findings

Flask remains in `web_app.py`, with existing auth, inventory and analytics blueprints. The Tkinter entry point remains `main.py`; local desktop/CLI access stays fixed to legacy store 1. Flask requests resolve stores through verified memberships, never posted ownership IDs. Existing session rotation, persistent login throttling, default private-route protection, CSRF, no-store caching, production cookie/secret rules and public read-only demo restrictions are preserved.

Cart selection is session-local and bound to the user/store context. Product queries and cart mutations use authorized-store services. Checkout re-reads active product prices and stock, computes integer-cent subtotal and half-up transaction-level tax, and executes `BEGIN IMMEDIATE`. The sale, historical line snapshots, conditional stock deduction and stock audit records commit together. Persisted checkout tokens prevent repeated service calls from recording another sale. A stale completed browser form cannot start a second checkout; its session token has been removed.

Before Day 17, transaction browsing had a 200-row limit without pagination; receipts were available only through the session's last-sale reference. There was no financial refund ledger, item tax allocation or financial effect from manual returned-stock adjustments. Day 17 adds these capabilities without rewriting historical sales or inferring missing payment details.

## Cart and history

Cart supports add, remove, increase/decrease, exact quantities (zero removes), stock feedback, and clear confirmation. Search, categories and product pagination reuse the existing scoped inventory service, always restricted to active products. There are still at most 50 distinct cart products and 1,000,000 units per entry. Final checkout revalidates availability.

With JavaScript, quantity changes refresh authoritative Flask-rendered totals; no financial arithmetic runs in the browser. Forms work without JavaScript using their submit buttons. The browser suppresses concurrent submissions. Checkout's persisted key remains the durable duplicate-sale protection. Cart quantities are cookie-session state, so simultaneous edits in multiple tabs can overwrite one another; use one checkout tab. This cannot bypass final stock validation or create duplicate sales with the same checkout key.

Transaction history accepts ID/date search, date range, Cash/Card, completed/partial/refunded status, and newest/oldest/highest-total/lowest-total sorting. Sort SQL is allowlisted and values parameterized. Results are 20 per page; matching summaries are limited to 10,000 sales, returning 413 instead of silently truncating. Invalid filters return 400. Counts and totals use one read snapshot.

## Historical receipts

`/receipt` still resolves the session's last sale. `/transactions/<id>/receipt` provides durable authorized access from history. Both use one scoped snapshot of persisted sale/line/refund data. Product edits never change historical names, prices or amounts. Receipts show current store identity (the old schema never captured the historical store name), payment method, cash/change when recorded, original total and cumulative refund details. Missing historical tax details are explicitly unverified. Original totals never decrease after refunds.

Print controls use the browser's Print / Save as PDF. CSS hides application chrome. No private PDF files or receipt files are written under static assets. Jinja escapes store names, historical product names and refund reasons. Receipt/detail views are bounded to 1,000 sale lines, with 413 for larger records rather than an incomplete receipt. Latest 100 refunds are displayed; ledger CSV exports provide date-filtered history.

## Approved refund financial rules

Only sales with complete positive-price line records, a recorded Cash/Card method, a finite recorded tax rate from 0 through 100%, and a total that reconciles exactly to the original line subtotal plus half-up tax are eligible. Missing or inconsistent legacy details remain unchanged and cannot be refunded. No unsupported discounts, voids or payment details are invented.

The original tax is allocated proportionally across original purchased units using **largest remainder**. For each unit, its floor share is `original_tax_cents * unit_price_cents // original_subtotal_cents`. Remaining cents go to the largest fractional remainders, with original line ID and then unit ordinal breaking ties. The implementation groups identical units without allocating a list of millions of units. Each partial refund consumes the next unrefunded units on its original line. Rounding never restarts for a later refund. All quantities refunded together or separately therefore return exactly the original subtotal and tax, independent of the order in which different lines are selected.

Example: two five-cent units, original tax one cent, total eleven cents. The first original unit receives the tax cent: its refund is six cents; the next is five cents. Repeating partial refunds cannot create another tax cent.

## Refund workflow and inventory

From transaction details, select **Simulated refund / return**. Choose quantities or all remaining items, enter a reason, and optionally select restocking. **Restocking defaults off**. Damaged/discarded items should remain unchecked. Preview shows the authoritative item and tax amounts, reason and stock effect. Confirm the signed, user/store/sale-bound quote within one hour. Quotes are not credentials and grant no access without the current authorized session and CSRF token. Changes in prior refund state require a new preview.

Only completed simulated refunds are recorded. Original sales are never overwritten, deleted or marked as smaller sales. Refund status is derived: no refunds = completed; some amount = partial; original amount returned = refunded. There is no external payment or pending processor settlement.

The service checks authorization, membership, quantities, eligible originals and cumulative caps under `BEGIN IMMEDIATE`. It records the refund header and item amounts, optionally restores stock, and records the Day 15 `return` stock movement with a direct refund ID and original sale link in that same transaction. Failure rolls back everything. Inactive products can be restocked without becoming active. Stock above 1,000,000 is rejected atomically. No-restock refunds make no stock movement. Existing manual returned-stock adjustments remain stock-only; do not also manually restore units already restored by a refund.

A durable, per-store idempotency key and request fingerprint make retries return the original refund ID. Reusing that key for different data is rejected. Concurrent independent refunds serialize; they cannot exceed remaining quantities or amounts. Header amount caps, item quantity caps, composite ownership foreign keys, immutable refund records and protection of refunded sale records add database-level defenses. Trusted database administrators still control the file and can bypass application policy; do not grant users SQLite file access.

Refund preview/confirmation attempts share the existing persistent limiter: 10 requests per authenticated user per 15-minute window, across sessions and workers. A normal preview plus confirmation consumes two attempts. This is intentionally conservative for a portfolio application; no staff permissions, supervisor approvals or configurable refund windows exist yet.

## Analytics reconciliation

Original gross sale totals remain on sale dates, including tax. Refunds are reported separately on their persisted **refund business date**, alongside their UTC creation timestamp. Net sales including tax = gross sale totals in the selected period minus completed refunds in that period. A period can have negative net sales when older sales are refunded. This is a cash-movement reporting convention, not accrual accounting or profit.

Daily, Monday-based weekly and monthly tables/CSV include gross, refunds and net. Previous-period comparisons, average sale, product rankings, units sold and payment breakdown retain original gross-sale definitions. Transaction CSV includes cumulative refunds across all dates as a separate informational field; do not subtract that field again from period net. New refund-ledger CSV includes refund ID, original sale ID, UTC timestamp, business date, amount, tax and restock flag. Summary CSV reconciles gross minus refund ledger total to net. Dashboard and Reports display separate refund/net KPIs. All queries and exports are store-scoped. CSV retains formula neutralization and no-store attachment headers.

Reports are bounded to 366 days, 10,000 current/comparison transactions, 50,000 current sale lines and 10,000 current-period refunds. Keep `TCCP_TIMEZONE` stable: legacy sales have local wall-clock timestamps without original UTC offsets. Refund business dates are persisted so later timezone changes do not relabel them. PDF export remains browser printing; there is no server PDF dependency.

## Additive migration 17 and recovery

Stop all web/desktop writers before migration. Work with a backed-up, private local disk database, never an ephemeral or network-shared database. No working database is migrated as part of this code-editing task.

```bash
.venv/bin/python manage.py init
.venv/bin/python manage.py check
```

`init` backs up an existing file first, then runs all pending versioned migrations in one transaction. Version 17 creates `refunds` and `refund_items`, composite foreign keys and indexes, cap/immutability triggers, and nullable `stock_adjustments.refund_id`. Existing audit rows retain NULL refund IDs; no historical movements or refunds are fabricated. The quantity-audit trigger now records optional refund context, while desktop connections register the matching SQL function. Migration is idempotent. Production startup requires version 17 and refuses an unmigrated database; it never migrates automatically.

Recovery: stop writers, preserve the failed/current file, and restore a known-good backup **to a new path** with `manage.py restore --file /path/to/backup`, setting `TCCP_DB_PATH` to that new target. Run `manage.py check`, then point the appropriate code version at the checked database. Do not restore an old backup over a live database or discard refunds recorded after that backup. Never run older application code against the upgraded quantity-audit trigger: old connections lack the new SQL audit function and writes fail closed.

## Verification and residual limitations

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/ruff check .
.venv/bin/python -m pip check
.venv/bin/python -m pip_audit -r requirements.txt
.venv/bin/python manage.py check
```

New real Flask/SQLite tests exercise three accounts, checkout/cart ownership, receipt/refund ID guessing, foreign product/line injection, store-ID tampering, signed quote context, CSRF, public demo restrictions, partial/full refunds, tax allocation, immutable history, duplicate retries, concurrent refunds, checkout/refund races, audit rollback, stock bounds, inactive products, cash change, refund-date analytics and exports, migration preservation/idempotency and integrity checks. Existing desktop and Days 13–16 tests remain enabled.

Manual visual/mobile/print and keyboard checks remain necessary if no interactive browser is available. Passing tests and dependency scans do not guarantee zero vulnerabilities. Existing inline CSP allowances are retained; HTTPS/reverse-proxy limits and backups remain deployment responsibilities. This application is a simulated portfolio POS, not a certified payment processor or accounting system.

Security reference: [Flask security considerations](https://flask.palletsprojects.com/en/stable/web-security/). Transaction reference: [SQLite transaction semantics](https://www.sqlite.org/lang_transaction.html).

## Change manifest

Created:

- `database/refund_migration.py`: version 17 ledger, constraints, indexes and audit linkage.
- `services/refunds.py`: authorized sale/refund snapshots, fixed unit tax allocation, previews and atomic refund/restock service.
- `services/transaction_history.py`: scoped, validated history filters, summaries and pagination.
- `refund_routes.py`: signed preview/confirmation and private refund detail routes.
- `templates/refund.html`, `templates/refund_detail.html`: simulated refund workflow and printable result.
- `static/js/checkout.js`: authoritative cart refresh and submission guards.
- `tests/test_refunds.py`: Day 17 financial, concurrency, integration and isolation tests.
- `docs/DAY17.md`: design, rollout, recovery and verification notes.

Modified:

- `web_app.py`, `database/database.py`: route/service integration, production schema guard and refund audit SQL context.
- `services/analytics.py`, `services/sales_exports.py`: separate refund/net metrics and refund ledger CSV.
- `templates/sale.html`, `templates/transactions.html`, `templates/transaction_detail.html`, `templates/receipt.html`: cart/history/receipt integration.
- `templates/dashboard.html`, `templates/analytics.html`, `templates/reports.html`, `templates/stock_history.html`: metric definitions and refund/audit links.
- `static/css/style.css`: return form and print styling.
- `tests/test_auth_isolation.py`, `tests/test_analytics.py`: include the new routes in the anonymous guard sweep and require migration 17; all existing checks remain.
- `README.md`: Day 17 usage and migration instructions.

No dependency files, Tkinter source files, production secrets or working database contents were changed. No commit, push or deployment was performed.

## Final verification results

- Before changes: **106 tests passed, 34 subtests passed**.
- Final complete suite: **136 tests passed, 42 subtests passed in 145.42 seconds** using `.venv/bin/python -m pytest -q -p no:cacheprovider`. This includes 30 new Day 17 tests; no existing tests were removed or disabled.
- Ruff, Python compilation, JavaScript syntax parsing, `git diff --check`, Gunicorn configuration and `pip check` passed. JavaScript parsing is a syntax check, not browser interaction testing.
- `pip-audit -r requirements.txt`: **No known vulnerabilities found**. Advisory access required running outside the restricted sandbox.
- A disposable copy of the existing database migrated twice to versions 14/15/16/17, preserving all original column values across 11 existing tables. SQLite integrity passed with zero foreign-key violations. The original database hash remained unchanged.
- A separate production fixture at version 16 was rejected. After migration 17, production import succeeded with Secure/HttpOnly/SameSite cookies, read-only default and debug disabled. Missing-secret and desktop compatibility regression tests also passed in the full suite.
- Final diff and tracked-sensitive-file review found no introduced secrets or tracked database/private-key files. New SQL was reviewed for authorized-store predicates, parameterized values and allowlisted dynamic sort expressions.
- No interactive browser was available. Visual/mobile, focus behavior and actual browser Print / Save as PDF remain manual checks. No production migration, commit, push or deployment was performed.

**Day 17 implementation status: COMPLETE.** Automated critical financial, authorization, concurrency and regression checks pass. Apply the documented backup/migration procedure before running upgraded clients against an existing working database.
