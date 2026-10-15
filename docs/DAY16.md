# Day 16 — Advanced sales analytics

## Audit and financial sources

The existing entry point remains `web_app:app`. The application has revocable login sessions, server-verified store membership, a default private-route guard, CSRF, scoped services, atomic simulated checkout, and Day 15 stock audit triggers. Git was clean before this milestone. The starting regression suite passed 87 tests and 32 subtests.

| Data | Source of truth and limitation |
| --- | --- |
| Committed sale | A persisted `transactions` row; cart cancellation never creates one |
| Sale total | `transactions.total_cents`, including configured tax; REAL `total` is a compatibility mirror |
| Timestamp | Existing business-local wall-time text, normally `YYYY-MM-DD HH:MM:SS` |
| Payment method | Recorded Cash/Card; unsupported historical values display as not recorded |
| Product identity | Historical `transaction_items.product_id`, not product name/SKU |
| Sold units / item sales | Recorded line quantity and `price_cents`; never current inventory prices |
| Product name | Latest line-item snapshot within the selected period, ordered by timestamp, transaction ID, line ID |
| Ownership | Transaction and item store IDs, verified against the current authorized store |
| Tax | Inferred from total minus recorded line amounts, verified against stored rate where possible |
| Refunds / voids / discounts / profit | Not implemented in the financial model; never invented or reported as zero-valued supported metrics |

There is no transaction status column. Every existing committed transaction row is treated as a completed simulated sale. Stock returns do not refund or void a financial transaction. Historical amounts are read only and never recalculated from today's product price or today's tax rate.

## Architecture

`services/analytics.py` validates filters, reads one consistent SQLite snapshot, and produces reusable metrics/series/rankings. It obtains store context through `get_store_connection()` and `authorized_store_id()`; callers cannot supply an arbitrary store argument. Both sides of transaction-item joins are store scoped.

Four bounded SELECTs read current transactions, prior-period transactions, current historical items and store identity. Python integer aggregation avoids SQLite SUM overflow and floating financial averages; Decimal is used for half-up averages, comparison percentages and display/chart ratios. There are no per-product database queries or writes during aggregation.

Limits are explicit: at most 366 selected calendar days, 10,000 transactions in each current/comparison period and 50,000 current line items. Exceeding limits returns 413 and asks the user to narrow the range; reports never silently truncate. Product rankings and their export contain the selected Top 5 or Top 10, not an unbounded product list. Products with no recorded sales do not rank. Deterministic tie-breakers use units, item sales and stable product ID; name sorting uses normalized case ordering plus ID.

The existing `/reports` page now adapts the same service output, retaining its date-picker fallback behavior, payment breakdown, daily totals and product table. The new `/analytics` routes and downloads reject invalid filters with 400. Dashboard calculations and Tkinter behavior are preserved and checked against the new service. There is no separate server-side analytics context to survive account switching; each request resolves current membership again.

## Metric definitions and reconciliation

- **Recorded sale totals:** sum of authorized committed transaction `total_cents`; tax included. This is a collected/recorded sales metric, not net revenue or profit.
- **Item sales:** sum of recorded line-item `quantity × price_cents`, before tax. It includes all matching items even when a ranking displays only ten products.
- **Tax / reconciliation difference:** recorded sale totals minus item sales. For fully recorded checkouts this is inferred tax. Legacy rows with missing items/rates or inconsistent values remain unverified; their differences must not be described as verified tax.
- **Committed transactions:** number of included transaction rows, not item rows.
- **Average order value:** recorded sale totals divided by committed transaction count, rounded half up to an integer cent for display/export; zero for an empty period.
- **Units sold:** sum of recorded matching item quantities.
- **Previous comparison:** equal numbers of preceding calendar days. Percentage is `(current - prior) / prior × 100`, rounded to two decimals. No positive prior total means no percentage baseline; absolute change is still available.

Reconciliation: sum of period totals equals sale totals; payment-method totals equal sale totals; all item sales plus the reconciliation difference equals sale totals. Rankings may show fewer than all products, so Top 5/10 sums need not equal all item sales. CSV product names and store names may have an apostrophe added for spreadsheet safety; numeric values remain exact.

## Dates, timezones and daylight saving

Presets: Today, Yesterday, Last 7 days, Last 30 days, This month to date, Previous month, and Custom. Start/end dates shown to users are inclusive. SQL boundaries are half-open: `[start at 00:00:00, midnight after end)` in the existing business-local timestamp model. Date parsing requires exact `YYYY-MM-DD`; reversed and excessive ranges are rejected. The default is Last 30 days. Date input changes automatically select Custom; without JavaScript, select Custom explicitly.

All stores currently use the documented deployment fallback `TCCP_TIMEZONE` (default `America/Chicago`). There is no per-store timezone setting in the existing schema. Keep that setting stable for the lifetime of a database; changing it cannot convert offset-free historical records correctly. No historical timestamps were rewritten.

Calendar boundaries are constructed using ZoneInfo, not by adding fixed 24-hour UTC durations. Tests cover 23- and 25-hour Chicago days, local midnights and repeated fall-back times. Every transaction ID is counted once, including distinct transactions sharing a wall timestamp. A repeated-hour timestamp has no stored offset/fold, so its exact historical UTC instant cannot be recovered. The reports intentionally make no claim to order ambiguous same-time records by real UTC time. Generated export timestamps are actual UTC.

Daily/weekly/monthly series include zero-sale buckets. Weeks start Monday, months start on the first, and boundary buckets are clipped to the selected dates. Partial periods are labeled. Comparisons use equal calendar-day lengths rather than fixed seconds; current periods may still be in progress. The selected report range applies identically to summary, charts, ranking and downloads.

## UI and downloads

**Sales analytics** is a new navigation item. The page includes precise metric labels, validation messages, comparison indicators, payment breakdown, zero states, mouse/keyboard-selectable trend bars, an accessible trend data table, product bars and sortable product table. Filter/export links preserve dates, grouping, ranking and Top 5/10 selection. Inline styles contain only server-computed chart percentages; the external JavaScript uses local DOM state and `textContent`, with no private global JavaScript object or external chart service.

Four authenticated CSV routes:

- `/analytics/export/summary.csv`: metrics, previous-period totals and comparison.
- `/analytics/export/periods.csv`: daily/weekly/monthly zero-filled trend.
- `/analytics/export/products.csv`: the selected Top 5/10 historical products.
- `/analytics/export/transactions.csv`: bounded transaction history with recorded totals, item sales, tax/reconciliation difference and tax verification status.

Every file includes the authorized store ID/name, inclusive dates, timezone, generation timestamp and filter metadata. CSV is produced with Python's CSV library, UTF-8 BOM and CRLF newlines. Numeric money uses exact Decimal dollar formatting. Untrusted text beginning with spreadsheet formula characters, even after whitespace/BOM, is apostrophe-prefixed. Embedded quotes, commas, Unicode and newlines are escaped by the CSV writer.

Downloads are generated in bounded memory, never saved under `static/`, and use `text/csv`, safe fixed/date-based attachment filenames and private/no-store headers. They contain no emails, passwords, hashes, cookies or session tokens. A fresh download is a fresh read snapshot, so a concurrent committed sale can change results between page load and download; use its generation timestamp when comparing snapshots.

PDF is not implemented: the project has no PDF dependency or verified renderer. The requested conditional PDF extension was deferred to keep this milestone within the existing stack. Use CSV for spreadsheet analysis; browser print remains available for the page but is not advertised as a tested PDF generator.

## Migration and performance

Migration 16 adds only:

- `transactions_store_time(store_id, timestamp, id)`
- `items_store_transaction(store_id, transaction_id, id)`

No financial columns or amounts change. It is versioned, additive, transactional and idempotent. Production startup now requires migration 16 and validates it read only.

```bash
# Stop writers first; select the intended local database.
export TCCP_DB_PATH="/absolute/path/to/tccp.db"
python manage.py init
python manage.py check
```

`init` backs up existing databases before migration. Recovery follows the existing restore-to-a-new-path procedure with the matching older code. Never drop live indexes/schema or overwrite a database to roll back. No working/production database was migrated during implementation; tests use disposable data and a copied source database.

Query-plan tests check both new indexes. A 1,200-sale fictional workload exercises aggregate counts, totals and bounded performance; the ten-second test guard is a regression bound, not a latency guarantee or production benchmark. Larger workloads must use narrower periods. Concurrent report readers may briefly contend with writers under SQLite's existing journal mode; distributed/network-filesystem deployments remain unsupported.

## Security review and limitations

All analytics/download endpoints inherit the default private-route guard. Anonymous browser requests redirect to login, direct export links enforce membership independently of the UI, and demo mode can read only its dedicated fictional store. Client `store_id`, product IDs and transaction IDs do not override scope. No new JSON APIs or write endpoints were added, so GETs cannot mutate financial records. Existing CSRF protections remain unchanged.

Sort/group/top/preset inputs are allowlisted; dates and bounds are validated both at the route and service boundary. Financial and item queries use parameters and authorized store predicates. Chart/product strings are autoescaped. CSV formula neutralization and safe filenames prevent spreadsheet/file-path injection. The existing security headers prevent shared caching of sensitive pages/downloads.

Residual limits: no refund/void/discount model, no verified historical UTC offsets, no store-specific timezone settings, no large asynchronous exports, no PDF output, and no profitability metrics. Missing legacy item details remain visible as a reconciliation warning rather than invented tax. Existing CSP inline allowances remain as previously documented. No browser was available for final visual/mobile/print interaction checks, so those are not claimed. Dependency scans report known advisories only and are not a guarantee of no vulnerabilities.

## Verification commands

```bash
python -m pytest -q
python -m ruff check .
git diff --check
python -m pip_audit --progress-spinner off --cache-dir /tmp/tccp-pip-audit
python manage.py check
```

## Verification performed

- Complete final suite: **106 tests passed, 34 subtests passed** (87 existing tests plus 19 Day 16 tests). No tests were removed, skipped or weakened; the migration-version assertion includes version 16.
- All three fictional accounts used real login sessions and independently scoped page/CSV requests with overlapping names and dates. Foreign query IDs did not override ownership; switching accounts, logout and the read-only demo remained isolated.
- Reconciliation covered sale totals, item totals, payment totals, zero-filled daily/weekly/monthly buckets, half-up averages, missing legacy details, midnight and DST boundaries, historic prices/deactivation, and duplicate checkout retries.
- CSV checks covered all four report types, matching filters and totals, metadata, MIME/attachment headers, no shared caching, Unicode/quotes/newlines, formula payloads and absence of credential/session fields.
- Limits and index query plans passed; the 1,200-sale representative workload stayed within the ten-second regression guard.
- Ruff, `git diff --check`, Gunicorn configuration and dependency-consistency checks passed. The shell has no Node executable; the exact JavaScript file was parsed successfully by the available V8 runtime (syntax only, without DOM execution). `pip-audit` reported no known vulnerabilities at verification time.
- The migration ran twice on a disposable copy; existing data/ownership matched, both SQLite integrity checks passed and a source-file digest confirmed the working database was unchanged.
- Tracked-file checks found no databases, secret `.env`, backups, key files or private-key blocks. No commit, push, deployment or working-database migration was performed.
- No browser surface was available. Visual/mobile chart interaction and print layout remain manual checks. PDF output is not included.
