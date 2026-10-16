s# Day 26A architecture and interfaces

## Verified starting point

The existing application is a global Flask application in `web_app.py`, with
blueprints and SQLite service modules; there is no application factory. Baseline
verification ran before implementation: **67 tests and 36 subtests passed** across
customers, discounts, purchasing, business financials, UI isolation, exports,
desktop sales and refunds. Git was clean at the start.

Existing features verified/reused: opaque revocable login sessions, authenticated
store context, CSRF, read-only demo mode, paginated SKU/name/category inventory,
customer selection, before-tax fixed/percentage discounts, integer-cent tax and
cash validation, atomic stock deduction, durable checkout tokens, original sale
snapshots, partial refunds, purchasing/receiving, expense corrections and financial
reporting. Product image uploads, variants and modifiers were not present.

## One checkout engine

`services/transaction_service.complete_transaction` remains the only sale writer.
Every preset and resumed cart calls it. New keyword arguments are optional, keeping
desktop callers compatible. Existing settings, discounts, refund allocation,
acquisition-cost snapshots and inventory audit/alert triggers remain authoritative.

`services/pos_cart.price_cart` is the shared cart-read/quote interface extracted from
`get_sale_data`. It loads up to 50 owned active products in one query, reads settings
and customer eligibility in the same SQLite snapshot, and uses existing money and
discount utilities. The web wrapper adds session notes. Checkout independently
revalidates the final quote inside its existing `BEGIN IMMEDIATE` transaction.

No JavaScript computes money. JavaScript replaces cart HTML returned by Flask.
The cash-change preview also uses a server quote and returns formatted total/change
strings without recording payment, changing stock or creating a transaction.
There are no card-detail fields, payment SDKs or external payment services.

## Presentation configuration

`services/pos_layout` stores one validated configuration per store. It contains only:

- preset: `hybrid`, `quick`, `custom`;
- density: `compact`, `comfortable`, `large`;
- image-placeholder visibility and category-navigation visibility (booleans);
- ordered pinned product IDs (100 maximum);
- ordered pinned category IDs (50 maximum);
- shared favorite product IDs (100 maximum);
- allowlisted tile colors for pinned products: blue, navy, green, amber, purple.

Unknown fields, duplicate IDs, foreign/inactive product references, unavailable
categories and arbitrary CSS values are rejected. JSON is bounded at 16,000
characters. Saves require the authenticated store owner, use a revision check and
commit atomically. Defaults are implicit for existing stores; no business data is
backfilled. Reads prune stale/inactive references safely. Reset writes defaults via
the same validated, revision-controlled interface.

`query_inventory` retains its existing paginated store-scoped query and adds an
optional validated presentation order/favorites filter. Ordering happens before
pagination. Hybrid uses the standard grid, Quick Sale uses large tiles/favorites
first and compact category shortcuts, Custom honors pinned order/colors. All share
one template and checkout backend. Favorites never change product/customer records.

`pos.js` provides native drag/drop, keyboard/touch move buttons, safe DOM/textContent
preview rendering, pin/unpin, favorites and dirty-state warnings. Preview contains no
sale forms and performs no business-data writes. No frontend framework or dependency
was introduced. Existing product-initial placeholders remain; no uploads were added.

## Held-sale lifecycle and concurrency

`services/held_sales` persists bounded product IDs/quantities, customer ID, discount
instructions and notes. It never persists trusted prices, tax or stock quantities.
Each sale retains a stable, unique checkout token across holds/resumes/releases.

```
held -> resumed -> completed
  |        |
  |        +-> held       (hold again or explicitly release interrupted claim)
  |        +-> discarded (clear/discard)
  +-> discarded
```

Resume uses `BEGIN IMMEDIATE` and creates an opaque claim; only its SHA-256 digest is
stored. Exactly one resume can claim a held cart. The current session keeps the
claim. An existing active cart is never silently overwritten. Lost sessions can
release a claim from Held sales; release revokes every stale copy. Closed records
are retained to preserve idempotency, rather than physically deleted.

The checkout engine discovers held rows by checkout token independently of client
held IDs. It verifies the active claim before any stock change. Stock deduction,
sale creation, item snapshots and marking the held row completed share one SQLite
transaction. Concurrent attempts with the same token return the original completed
transaction. Releasing/holding/discarding serializes with checkout, so an old claim
cannot complete after revocation. Database guards protect ownership, transitions,
completion references and closed records.

Holds do not reserve stock or create payments/transactions. Current prices, product
activity, tax, discounts, customer eligibility and stock are revalidated on use.
There are at most 200 open held sales per store; lists contain 20 per page. Estimates
that require review are labeled accordingly. Notes are limited to 500 characters,
escaped in the UI and snapshotted immutably on completed transactions.

## Uncertain checkout recovery

Before a checkout request, the browser retains its idempotency token in per-account,
per-store sessionStorage when available. On a timeout, repeat sale actions are
blocked and an explicit Check checkout status action appears. Reloading the checkout
also restores pending verification. No credentials or card information are stored.

The status endpoint serializes with in-flight writers using `BEGIN IMMEDIATE`. It
returns either the authorized original receipt or `not_found` with an instruction to
retry only the original token. Database-busy/network errors keep the result uncertain.
A timeout is never presented as a definitive failed transaction. SessionStorage is a
recovery aid, not an authorization source; durable server tokens protect retries.

## Authorized store selection

Registration continues to create one private store. `/stores` lists only existing
owner memberships. Switching verifies membership, replaces/revokes the login token,
rotates session state and clears current cart/options. It grants no memberships and
never exposes another store through a submitted ID. Public demo cannot select stores.
Store selection is an authenticated session action, allowed in read-only deployments;
business mutations remain read-only. Held claims remain recoverable in their store.

## Server interface contract

All paths except login/registration/static require authorized store context. Browser
pages redirect anonymous users to login; the new API returns 401. Every POST requires
existing CSRF validation. Demo/read-only business writes return 403. Unknown fields
are rejected on new mutation forms. New forms use URL-encoded/multipart form data,
not arbitrary JSON request bodies. JSON configuration is a bounded form field.

| Interface | Method and inputs | Authorization / response |
| --- | --- | --- |
| `/sale` | GET: existing q/category/page; `favorites=1`; customer_q | Owned active product grid and server cart HTML; invalid filters 400 |
| `/sale/add` | POST: product_id, quantity | Existing authorized/stock-validated cart mutation; redirect |
| `/sale/update/<product_id>` | POST: quantity, optional adjust=plus/minus | Existing current-cart mutation; redirect |
| `/sale/remove/<product_id>` | POST | Existing cart removal; redirect |
| `/sale/cancel` | POST | Existing clear; additionally revokes/discards its resumed claim |
| `/sale/customer` | POST: customer_id or empty for guest | Existing owned active-customer selection |
| `/sale/discount` | POST: kind=none/fixed/percent, value | Existing discount engine and validation |
| `/sale/notes` | POST: notes | 500-character validated optional note; 400 invalid |
| `/sale/hold` | POST: optional label | Durable owned hold of server session cart; 400 conflict/invalid |
| `/pos/held` | GET: page | Owned open holds with current estimates, 20/page |
| `/pos/held/<held_id>/resume` | POST | Atomic claim; 404 foreign/absent, 400 in-use/active-cart conflict |
| `/pos/held/<held_id>/manage` | POST: action=rename/release/discard; label for rename | Owned lifecycle mutation; 400 closed/conflict, 404 foreign |
| `/pos/designer` | GET: q/page | Owned settings and bounded catalog; demo preview read-only |
| `/pos/designer` | POST: configuration JSON string, revision, preset, density, optional show_images/show_categories=1, action=save/reset | Owner only; revision conflict/invalid input 400; 403 demo/read-only |
| `/sale/checkout` | POST: original checkout_token, payment_method; cash_received for cash | Existing atomic engine, now including claim/notes; redirects to receipt |
| `/api/pos/cash-quote` | POST: cash_received | CSRF/auth/store scoped current cart quote; server-formatted total/change, 400 invalid/insufficient cash or unavailable cart; no business writes |
| `/api/pos/checkout-status` | POST: checkout_token | CSRF/auth/store scoped; JSON state, account/store context, receipt_url only if completed; 503 stays uncertain |
| `/transactions/<transaction_id>/receipt` | GET | Existing owned historical receipt; no client-calculated totals |
| `/stores` | GET/POST: store_id on POST | Existing verified owner memberships only; rotates session |

General SQLite failures remain sanitized 503 responses. Authentication/session expiry
never authorizes an old cart for a new store. No prices, ownership fields, financial
totals or claim hashes are accepted as trusted client data.

## Migration and compatibility

Schema version **27** is Day 26A. It adds `pos_layouts`, `held_sales`, their indexes
and guards, and `transactions.order_notes` with an empty historical default. The
existing ordered initializer runs it transactionally/idempotently. Production schema
validation now requires 27; startup does not silently repair outdated production data.

Stop writers, use the existing explicit backup/init/check procedure, retain a verified
backup, and restart only after integrity checks. Development verification uses temporary
SQLite databases and a disposable read-only-source copy. Desktop entry points and
existing product stock ownership are preserved.

## Explicit advanced-product deferrals

Formal variants and modifiers are **not implemented** in this milestone. The current
single-product inventory/receiving/refund/cost model has one authoritative product
stock row per sale line. Adding options requires coordinated stock ownership,
immutable per-unit price/cost snapshots, purchasing, partial refund and profitability
semantics. There are no fake variant/modifier selectors or unsupported receipt lines.
Separate ordinary products remain ordinary stocked products, not a claimed variant
system. Image uploads and item-level promotions also remain deferred. Supported
order discounts and original refund rules are unchanged.
