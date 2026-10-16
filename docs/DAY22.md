# Day 22 — Inventory notifications

## Workflows and condition policy

Open Notifications from navigation or the bell. Filters cover low/out/restored,
severity, read status, active/resolved/dismissed state and oldest/newest order.
Twenty records appear per page; links preserve filters. GET never marks a record
read. POST actions mark a notification or the current page read, or dismiss a
record. Every action is authorized and CSRF protected. Read/dismiss state belongs
to the store workspace (currently one owner), not a separate per-user inbox.

An active product is out of stock at zero (critical), low at positive stock at or
below its effective threshold (warning), and healthy above it. Blank product
thresholds inherit the store default; explicit overrides, including zero, win.
Inactive products create no conditions. Reactivating an unhealthy product creates
a new episode. Threshold changes never change stock or stock audit history.

There is one open condition episode per product/store. Unchanged conditions update
quantity/threshold/name snapshots only when they change, retaining read/dismiss
state. Low→out or out→low resolves the previous episode and creates a fresh unread
condition. Becoming healthy records an informational restored/condition-cleared
event and resolves the old episode. This can follow physical restocking OR a
threshold change; the UI explicitly distinguishes condition clearance from proof
of physical stock receipt. Deactivation resolves conditions without claiming a
restock. Read, dismissed and resolved history is retained, never deleted.

Dismissal hides that episode from unread counts and the recent panel; it does not
change stock or suppress a later critical episode. The full center retains it.
The bell count includes all unread, undismissed history; recent entries are the
latest five undismissed active-product records. Product names appear in the bell
on Dashboard/Notifications; other pages show notification IDs/types so the shared
shell does not reintroduce current or filtered-out names into historical/filtered
views. Full notification details retain recorded names and current product links.
There is no polling or external delivery integration.

## Atomic database integration

Migration 22 adds inventory_alerts with composite product/store foreign keys,
severity/type/quantity constraints, a partial unique active-product index, unread,
store/date, kind and product-history indexes, plus history retention guards.
It initializes current unhealthy conditions only; it never invents earlier events.
Initialization is versioned, additive, transactional and idempotent.

Product insert/update and inherited-default changes reconcile alerts through
SQLite triggers, within the initiating transaction. This covers all existing web,
checkout, refund, adjustment, maintenance and Tkinter paths, including legacy
inserts omitting store_id after store 1 normalization. Trigger failure rolls back
stock, sale/refund/audit and alert changes together. Original financial ledgers
are unchanged. Existing checkout/refund idempotency prevents duplicate transitions.
Per-product mutations target their store/product's indexed episode; default
threshold changes scan only the affected store's inherited products.

Existing missing-product repair now recognizes a genuinely missing alert product
reference in its explicit trusted repair mode. It still rejects mismatched stores
or other FK corruption. Inactive historical placeholders resolve the old condition
without rewriting its snapshot. Ordinary integrity checks remain strict.

## Read-only restock estimates

Recommendations are derived on the detail page, not durable notification events,
because reporting windows change with time. They never write inventory, create
purchase orders, or invent suppliers/lead times. Queries use the authorized store
and at most 50,000 sale and refund lines per window; excessive history fails 413.

Use the last 30 complete business days, plus gross units sold in the last seven
complete days. Physical depletion = max(0, units sold − units physically restored
by refunds on refund date). Refunds without restocking do not replenish stock.
Average daily depletion = depletion / 30. Estimates require a recorded opening
baseline/initial audit covering the entire window, an active product and positive
depletion. Missing history, zero demand or inactivity displays N/A.

Estimated days remaining = current quantity / average depletion, half-up rounded
to one decimal. Target stock = max(effective threshold + 1,
ceiling(14 × average daily depletion)). Suggested reorder = max(0,target−stock).
If the target exceeds the supported 1,000,000-unit stock limit, no reorder quantity
is suggested. Fourteen days is illustrative coverage, not supplier lead time.
These are transparent estimates, not guaranteed demand forecasts. They do not
change sale/refund accounting definitions or historical quantities.

## Migration, recovery and verification

Stop writers. Test a verified SQLite backup copy first, then use the existing
`python manage.py init` (backs up existing files) and `python manage.py check`.
Production requires migration 22. Do not reset or alter a live production file.
Restore a pre-upgrade backup to a new path with the matching application version
if recovery is necessary. Keep backups private. This implementation does not
migrate the working database automatically.

Day 22 gate command:

```
.venv/bin/python -m pytest tests/test_alerts.py tests/test_inventory_management.py tests/test_refunds.py tests/test_reliability.py tests/test_settings.py tests/test_ui.py tests/test_auth_isolation.py -q -p no:cacheprovider
```

Tests use temporary fictional databases. Responsive/print layout still needs
browser QA when no interactive browser is exposed; semantic HTML checks do not
certify accessibility. Gate and final results are recorded in FINAL_QA_REPORT.md.
