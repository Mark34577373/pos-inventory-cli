# Day 15 — Complete inventory management

## Features and usage

Open **Inventory** after signing in. The existing inventory endpoint now provides:

- Product name, SKU, or exact ID search; literal `%` and `_` are escaped rather than treated as wildcards.
- Category, Uncategorized, stock status, and active/inactive/all filters, combined with search.
- Allowlisted name, integer-cent price, quantity, or last-update sorting in either direction.
- Twenty products per page with filters preserved in previous/next links; result totals cover every matching page.
- Category labels, SKU, UTC update timestamp, thresholds, status text, and links to adjustments/history.

Use **Categories** to create, rename or remove a store-owned category. Names use Unicode NFKC normalization, collapsed whitespace and Unicode case folding for uniqueness. For example, `Office Supplies`, ` office  supplies ` and compatibility-width variants conflict within a store. Different stores may use the same name. Names contain 1–80 visible characters. A store supports up to 500 categories; removal makes its products Uncategorized without changing quantities or deleting products.

Product forms accept an optional category, optional SKU and optional low-stock threshold. SKU is trimmed, converted to uppercase ASCII, and limited to 1–64 letters/digits/`.`/`_`/`-`; it is unique per store. Legacy products keep NULL SKUs and categories. A blank threshold uses **5**; explicit zero is supported. Zero stock is always Out of Stock, positive stock at/below the threshold is Low Stock, and higher stock is In Stock. Inactive products are excluded from dashboard alerts and checkout.

The dashboard provides separate low-stock and out-of-stock lists (up to ten products each), full-group inventory links and restock links. Web inventory, sale selection, dashboard and desktop inventory/sale badges use the same threshold rule.

## Stock adjustments and history

**Adjust / restock** offers:

| Type | Quantity meaning |
| --- | --- |
| Restock | Positive units added to current stock |
| Correction | New absolute quantity; the form rejects a stale stock snapshot |
| Damaged | Positive units removed from current stock |
| Return | Positive units added to current stock; no financial refund/reversal |

Each adjustment requires a 1–500 character reason. Stock remains between zero and one million. Delta adjustments acquire `BEGIN IMMEDIATE`, read the current quantity and apply the delta while holding the write lock. Product edits also validate their displayed stock snapshot before saving metadata and quantity atomically. Existing trusted desktop/CLI setters remain available and record corrections.

History records product/store IDs, a product-name snapshot, acting user ID where applicable, type, before/change/after quantities, reason, an actual UTC timestamp and checkout transaction reference where applicable. Creation of a new product records opening stock (including zero); unchanged quantity does not create a movement. Product deactivation and category changes do not invent quantity movements.

Quantity triggers insert history in the same transaction as every product stock change, including desktop and maintenance changes through the configured database connection. If an audit insert fails, the quantity update and any checkout rows roll back. Checkout creates its transaction record under the existing write lock, then performs conditional deductions and line-item inserts; audit rows reference that transaction. Retrying a checkout token returns the original sale without adding stock movements.

Audit metadata belongs to each connection, not a process-global variable. It defaults to a local/system correction for trusted maintenance and uses authenticated user IDs for web operations. Direct SQLite writers must use `database.get_db_connection()` to register required audit functions; unconfigured external quantity writes fail rather than silently bypass history. Database administrators remain trusted and can alter schema; append-only triggers are not a tamper-proof external audit service.

History is paginated and filterable by product ID, type and inclusive UTC date range. Baselines show up to twenty products; filter by product ID to locate its baseline. Every query remains store-scoped, including adjustment detail IDs and baselines. There are no history edit/delete HTTP endpoints. UPDATE/DELETE on history and baseline tables is rejected by database triggers. Physical deletion of referenced products is restricted by foreign keys; existing soft deactivation preserves their identity and snapshots.

## Migration 15 and recovery

The additive versioned migration runs after Day 14, within the existing initializer's transaction:

- Adds store-owned `categories`, unique normalized names and store-aware indexes.
- Adds nullable category/SKU/threshold fields and a UTC last-update field to products.
- Adds `inventory_baselines`: one truthful snapshot for each product already present at migration time.
- Adds `stock_adjustments`, composite product/store and transaction/store foreign keys, actor membership checks, quantity consistency checks, and append-only triggers.
- Adds validation for cross-store category assignment, thresholds and SKU normalization.
- Preserves all existing product quantities, IDs, prices, transaction records and store ownership.

The baseline is **not** a reconstructed historical adjustment. There is no stock history before this migration, and old transactions are not replayed. `updated_at` starts at migration time because previous update times are unknown.

Stop writers and select the intended local database before migration:

```bash
export TCCP_DB_PATH="/absolute/path/to/your/database.db"
python manage.py init
python manage.py check
```

`init` backs up an existing database before its transactional migration. Production startup now requires version 15 and checks it read-only; it never auto-migrates production data. No working/production database was changed during implementation: verification used disposable databases and a copied source database.

For rollback, stop the app and restore the pre-migration backup to a **new** path, validate it, and run the matching pre-Day-15 code against that restored path. Never delete columns/tables in place or point older code at a live newer database. A restored pre-migration backup necessarily excludes later changes; retain both files and plan recovery deliberately.

```bash
TCCP_DB_PATH="/absolute/new/restored.db" python manage.py restore --file /absolute/path/pre-day15.backup
TCCP_DB_PATH="/absolute/new/restored.db" python manage.py check
```

Backups now include identity hashes and history; keep them private.

## Authorization and security review

Day 14 was verified against the implementation and existing regression suite before extending it. The new `inventory_tools` blueprint is private by default under the same membership/session loader and CSRF guard. All new service queries resolve the authorized store at the data-access boundary. No store/user IDs submitted in forms are assigned to records.

Product category ownership is rechecked in the write transaction and enforced by SQLite triggers. Category/SKU uniqueness is per store. History uses composite foreign keys, immutable store ownership inherited from Day 14, and an actor-to-store membership check. Sort identifiers come only from a fixed map; values are SQL parameters. Form fields and reasons use Jinja autoescaping. Mutations use POST and CSRF tokens. Public demo sessions cannot mutate categories, stock, thresholds, or history; a deployment-wide read-only setting also blocks service-layer writes.

No new API or export endpoints were added. Unknown API paths fail without side effects; current reports, receipts and transaction routes remain protected and scoped. Local desktop/CLI access remains fixed to the trusted legacy store; filesystem access is privileged, as documented in Day 14.

Remaining limitations: substring searches scan the selected store's candidates; distributed writers/network filesystems are unsupported; page navigation uses offsets rather than a historical snapshot; no email/push alerts, financial refunds or desktop category-management screens were added. No visual browser surface was available for mobile/keyboard/print QA, so those checks remain manual. Existing CSP still permits inline code as documented in Day 14.

## Verification commands

```bash
python -m pytest -q
python -m ruff check .
git diff --check
python manage.py check
```

The new suite registers/logs in three independent accounts through real HTTP forms, creates independent products/categories, and attacks cross-store product/category/adjustment IDs. It covers normalization, filtering, pagination, SKU, stock movements, UTC history, thresholds, dashboard isolation, CSRF, demo restrictions, switching accounts, checkout retries, forced audit failure, concurrent adjustments, and migration baselines. Existing Day 14 tests retain their behavior assertions; their expected migration list and route-ID substitutions include Day 15.

## Verification performed

- Complete suite: **87 tests passed, 32 subtests passed** (68 existing tests plus 19 Day 15 tests).
- Three-account tests use fictional Store A/B/C, real registration/login and actual form/service/database paths. Store-owned category names and SKUs can repeat across stores; foreign category/product/adjustment IDs cannot read or change their records.
- Eight concurrent authenticated HTTP restocks preserved all quantity transitions. Concurrent last-unit checkout allowed one sale, without negative stock; duplicate checkout retries produced one deduction/audit event.
- Forced audit failure rolled back stock and checkout. Existing desktop, authentication, money, receipt and report regressions passed.
- Migration ran twice on a copy of the working database; all prior fields were preserved, baselines matched existing products, no historical adjustments were fabricated, and both SQLite integrity checks passed. A source-file digest confirmed the working database was unchanged.
- Ruff, `git diff --check`, Gunicorn configuration and dependency-consistency checks passed. Tracked-file inspection found no database, secret `.env`, backup or key files.
- SQL review confirmed store predicates on every new application query and parameterized values; dynamic ordering is validated again at the service boundary.
- No browser/app surface was available: responsive layout, keyboard interaction and print preview were not visually verified.
- No commit, push, deployment or working-database migration was performed.
