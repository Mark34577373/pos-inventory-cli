# Day 28C — isolated interactive public demo

Implementation is local and not deployed by this task. No real purchase or payment
occurs. The primary new entry is `/demo/pos`, also linked from login and the existing
read-only checkout. The shared dashboard/catalog demonstration remains read-only.

## Architecture and security boundary

`demo_routes.py` is a separate public blueprint. `services/interactive_demo.py`
provides an immutable fictional catalog, exact quotes, and a bounded in-memory
visitor store. Neither module accesses the business database or calls checkout
persistence, stock deduction, customers, refunds, or inventory services.

The global request guard recognizes only this blueprint as public simulation.
It skips loading private identities and the SQL demo-store context on these routes,
while retaining the same CSRF, duplicate-field, file-upload, query-length, content
type and 64 KiB request-body checks. Existing business endpoints remain protected
and `TCCP_READ_ONLY=1` remains unchanged. A visitor's demo token never grants business
store authorization. Authenticated visitors may try the simulation without clearing
their private login or cart; no private identity is rendered in the demo shell.

The production schema/startup checks and `prepare-render-demo` are unchanged. The
dedicated static fictional catalog is not read from the shared seeded SQL store.
The product-card single-column/full-width fix remains intact. New pages reuse the
Hybrid Retail categories, tile styling, icons, mobile cart access, and existing
scrollable payment dock, including its bottom clearance rules.

## Routes

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/demo/pos` | Fictional catalog, search/demo SKU, category navigation, cart and totals |
| POST | `/demo/cart` | Add/set/increase/decrease/remove bounded quantities |
| POST | `/demo/discount` | Apply the existing fixed/percentage discount rules |
| POST | `/demo/cash-preview` | JSON server-formatted cash change or validation error |
| POST | `/demo/checkout` | Complete session-local simulated Cash/Card checkout |
| GET | `/demo/receipt` | Only the current visitor's latest fictional receipt; no identifier lookup |
| POST | `/demo/clear` | Clear cart and discount; retain the latest receipt for recovery |
| POST | `/demo/reset` | Clear this visitor's cart, discount and receipt |

Every POST requires CSRF. Unknown fields, including prices, totals, ownership and
card credentials, are rejected. Submitted product IDs must belong to the immutable
catalog. Search is limited to 100 characters and categories are explicitly allowed.

## Sessions, bounds and expiry

The signed, HttpOnly Flask cookie carries an opaque random demo token, not prices,
cart lines or a receipt. Server memory holds each cart and at most one receipt and
one completed checkout fingerprint. Server-controlled quote nonces change whenever
the cart/discount changes. Expired, reset or restarted sessions cannot use old nonces.

- 256 visitor states maximum; idle expiry after 30 minutes.
- Six distinct lines, 20 units per line, further limited by fictional availability.
- $1,000.00 maximum gross subtotal before discounts/tax; cash tender capped at $2,000.
- No inventory deduction. Another visitor always sees the same fictional availability.
- At capacity, new visitors receive a retryable error rather than evicting active users.
- Expired entries and limiter buckets are pruned under the same thread lock.

There is no permanent history, account requirement or persistent anonymous record.
Reset affects only the simulation. Returning after expiry/restart creates a new empty
demo and displays a restart notice; an old receipt returns 404. Receipts cannot be
looked up by guessing identifiers. Cookies remain credentials: anyone possessing a
stolen visitor cookie can act as that visitor until expiry; standard HTTPS/session
protections must remain enabled.

## Money, receipts and idempotency

Quotes reuse `discount_amount`, `calculate_tax_cents`, `parse_cash` and money
formatting. Prices, line totals, discounts, tax, totals and change use integer cents
and Decimal half-up rounding. Demo tax is explicitly 8.25%; no client/store field can
change it. Discounts must leave at least one cent per unit, matching supported rules.
Invalid edits leave the preceding cart intact; clear removes the cart and discount.

Cash rejects insufficient or inexact tender; Card accepts no card credentials.
A lock makes quote validation and completion atomic for a visitor. Repeating the
last completed nonce with the same payment details returns the same receipt.
Conflicting payment details and stale quote nonces are rejected. Starting a newer
checkout replaces the single saved receipt; older completion tokens then expire.

Receipts show `TCCP Demo Store`, a random `DEMO-` identifier, fictional line items,
discount, tax, total, simulated payment, optional tender/change and business-local
timestamp. They explicitly disclaim proof of purchase. Existing shared print handling
and receipt styling are reused. After a lost checkout response, visit `/demo/receipt`
before trying again; resetting discards that recovery information.

## Abuse controls and Render compatibility

Thread-safe process-local limits:

| Limit | Scope/window |
| --- | --- |
| 300 demo requests | Per connection IP / 60 seconds |
| 20 new demo sessions | Per connection IP / 10 minutes |
| 10 checkout attempts, including retries | Per visitor / 10 minutes |
| 60 checkout attempts | Per connection IP / 10 minutes |
| 1,024 limiter buckets | Total process capacity |

IP keys are HMAC-derived; raw addresses are not stored by this feature. Forwarded
IP headers are not trusted. Visitors behind Render's proxy may share IP buckets;
limits can therefore affect legitimate visitors collectively. This is bounded local
abuse protection, not distributed DDoS protection. Reset does not reset rate limits.

Keep exactly the existing **one Gunicorn worker/two threads** configuration. Memory
is shared between those threads under an RLock, not between separate workers or
replicas. Restarts/redeploys clear all demo state and counters. This is predictable
and acceptable for disposable fiction; it is not durable business hosting. No paid
infrastructure, new package or persistent disk is required. Never scale this design
to multiple workers without first adding a suitable shared ephemeral-state mechanism.

## Files changed

Created: `demo_routes.py`, `services/interactive_demo.py`, `templates/demo_sale.html`,
`templates/demo_receipt.html`, `static/js/demo.js`, `tests/test_interactive_demo.py`,
`tests/demo_frontend_checks.mjs`, and this document.

Modified: `web_app.py` (register the blueprint and retain common request protections),
`templates/base.html` (demo shell/disclaimer), `templates/auth.html` and
`templates/sale.html` (entry links), `static/css/pos.css` (small scoped additions),
`tests/test_auth_isolation.py` (explicit public-route CSRF assertions while preserving
all private-route checks), `tests/test_render_startup.py` (production-mode simulation
and byte-for-byte database immutability), and `README.md` (feature instructions).

## Local verification and manual acceptance

```sh
.venv/bin/python -m pytest -q tests/test_interactive_demo.py tests/test_auth_isolation.py tests/test_pos.py tests/test_ui.py tests/test_render_startup.py tests/test_security_hardening.py tests/test_retail_security_ui.py tests/test_deployment.py
.venv/bin/python -m ruff check .
.venv/bin/python -m compileall -q -x '/(\.venv|\.git)/' .
```

The existing frontend harnesses and `tests/demo_frontend_checks.mjs` execute actual
scripts against DOM models; they are not browser-rendering checks. Final actual
counts are recorded below after execution. Tests use disposable databases and prove
no business SQL access during simulation, exact financial rounding, isolated carts
and receipts, concurrency/idempotency, CSRF, resource/rate limits, and production-mode
checkout without a single change to the SQLite file.

Browser inventory reports no available apps or browsers. **No browser screenshots,
visual pass or live deployment verification are claimed.** Manual acceptance remains
at 1440, 1280, 1024, 768 and 390px: search/categories; short/long titles; add/set/+/-/remove;
fixed/percent discounts; cash preview and insufficient cash; Card without credentials;
receipt/print; reset; separate private windows; expired/restarted sessions. At viewport
heights 768 and 900px, use a six-line cart, open the payment panel, scroll its dock to
the bottom and verify the final button has at least 16px clearance and is clickable.
Check no horizontal overflow and mobile cart access. Real printing, screen readers,
physical touch and cross-browser behavior remain manual verification items.

## Deployment and rollback

No environment variable changes and no database migrations are required. Keep
`TCCP_ENV=production`, `TCCP_READ_ONLY=1`, `TCCP_ALLOW_REGISTRATION=0`,
`TCCP_EPHEMERAL_DEMO=1`, the stable private secret, explicit trusted host and the
existing `/tmp/tccp-public-demo/demo.db` path. Build remains `pip install -r requirements.txt`.
Start command remains:

```sh
python manage.py prepare-render-demo && exec gunicorn --bind 0.0.0.0:$PORT --workers 1 --threads 2 --timeout 60 --error-logfile - web_app:app
```

After review and manual acceptance, the owner may commit/push the tested revision and
deploy it from Render's configured branch. Then verify `/demo/pos` on the real HTTPS
origin, read-only business protections, styles/JS loading and a fictional receipt.
Local changes alone do not update the public site. No commit, push or deploy occurs
automatically. For rollback, redeploy the prior `21b8f18` revision (`Demo UI Fix`) using
the same safe environment/start command. No reverse migration or business-data reset
is needed; transient demo carts/receipts will be lost on restart in either direction.

## Final executed results

- Final combined relevant Python suite: **90 passed, 101 subtests passed**, no
  failures/errors/skips, in **133.63 seconds**. Includes the actual Gunicorn Unix
  socket startup check with its required local socket permission. JUnit evidence:
  `/private/tmp/day28c-focused.xml` (temporary local artifact).
- Frontend harnesses: **24 shared UI + 7 POS + 5 workspace + 4 demo = 40 passed**,
  zero failed. New checks verify tender-field behavior, server-formatted cash change,
  CSRF submission, stale reply rejection and visible network/validation errors.
- Ruff, Python compilation, dependency consistency and diff whitespace checks passed.
- No new dependencies, schema migrations or changes to business financial services.
- This is focused regression coverage, not a claim that the full repository suite
  was rerun. Earlier intermediate failures were corrected before these final runs.
- Browser inventory was empty. All five viewport checks and real print acceptance
  remain unverified; no live Render changes or screenshots were produced.

Implementation and local security/functional gates passed. Visual/live acceptance
remains pending the manual checks above; the public site still runs its previous
revision until the owner deliberately publishes this change.
