# Day 27 test evidence

Starting point: clean `feature/commercial-ui` at
`fe77cd091e48bc56972f8cd205e68a015ee43a08`. Python 3.14.7 / SQLite 3.50.4 locally;
CI remains configured for Python 3.12 and Node 20. Tests use private temporary SQLite
files. No test was removed or weakened. No new dependency or migration was added.

## Executed gates

| Command / gate | Actual final result |
| --- | --- |
| Baseline `.venv/bin/python -m pytest -q` | 253 passed, 155 subtests passed, 401.64s |
| `.venv/bin/python -m pytest -q tests/test_day27.py` | 8 passed, 8 subtests passed, 27.49s |
| `.venv/bin/python -m pytest -q --junitxml=/private/tmp/day27-final.xml` | **261 passed, 163 subtests passed, 455.78s** |
| JUnit result for that final full run | **424 cases including subtests; 0 failures, 0 errors, 0 skipped** |
| `node tests/frontend_checks.mjs` | 24 passed, 0 failed |
| `node tests/pos_frontend_checks.mjs` | 7 passed, 0 failed |
| `node tests/workspace_frontend_checks.mjs` | 5 passed, 0 failed |
| `.venv/bin/python -m ruff check .` | Passed |
| `.venv/bin/python -m compileall -q database services auth_routes.py pos_routes.py web_app.py manage.py` | Passed |
| `.venv/bin/python -m gunicorn --check-config web_app:app` | Passed |
| `.venv/bin/python -m pip check` | No broken requirements |
| `.venv/bin/python -m pip_audit --format=json --output=/private/tmp/day27-audit.json --cache-dir=/private/tmp/tccp27-pip-audit` | 43 installed packages, 0 reported advisories |
| `git diff --check` | Passed |
| Working DB read-only integrity/FK | Integrity `ok`, 0 FK violations, versions 14/15/16/17/20/21/22/24/25/26/27 |
| Disposable copy: repeat initialization and every existing table value | 25 tables preserved after two initializations; schema/integrity passed; original source bytes unchanged |

Python groups overlap and must not be added. JavaScript commands were executed via
the installed Node runtime tool because shell `node` is unavailable here; all three
actual-source suites pass. They use DOM doubles, not a rendering engine. Network
advisories initially failed under sandbox DNS and succeeded after approved network
access. The advisory result is a point-in-time installed-environment scan, not a
promise about future dependency versions. Pip emitted a harmless cache-directory
permission warning; dependency consistency and the approved advisory scan completed.

The new combined end-to-end test initially exposed incorrect test assumptions about
fixture returns and existing transaction redirects. Assertions now verify the actual
nested PO state and exact safe redirect targets plus absence of foreign products.
Existing response contracts and original tests were preserved. No assertion failure
remains in the final full run.

## Workflows and financial reconciliation

`tests/test_day27.py` adds closed-registration/no-write checks, production default/
explicit-override/invalid-flag/environment/static-path subprocess checks, guardless
and unrelated restore-source rejection, target-failure cleanup, and a combined
three-store ledger workflow.

Each of the three fictional owners has separate products/customers/suppliers/POs,
expense records, transactions, held sales and layouts. The workflow:

1. Receives a five-unit PO in two receipts, retries the first receipt without a
   second stock increase, and verifies partial/full order states.
2. Saves Custom layout, holds/resumes a discounted customer-linked sale, completes
   cash checkout, retries its original token and verifies one sale/held completion.
3. Records two restocked partial refunds, retries the first key and reconciles the
   full original sale exactly. Corrects an expense through reversal/replacement.
4. Reconciles dashboard projection, financial overview and CSV. Attempts foreign
   supplier/PO/expense/transaction/receipt/held access between all three stores.
5. Backs up/restores the combined ledger into a new private file and verifies the
   transaction/refund counts and exact stock quantities.

Per-store golden values: original merchandise 300 cents, discount 50, tax 21, sale
271; complete refunds 271; net revenue/tax/COGS zero after full restocking; corrected
net operating expense 20; estimated operating result -20; received purchase cost 200.
Stock starts at 20, receives five, sells three and restocks three, ending at 25.
No historical financial record is changed. Existing tests separately exercise unknown
cost/tax disclosures, rounding, maximum discounts, million-unit allocation, refunds
without restocking, customer history, CSV formula safety, concurrent last-unit buyers,
receiving/refund races, stale held claims, injected write failures and exact receipts.
The web flow always recalculates current server prices; it is not a fixed-price quote
reservation system. Atomic-writer price changes are rejected by existing tests.

## Database / failure coverage

Existing regressions exercise legacy migration fixtures, version gaps/missing guards,
DDL rollback, FK enforcement, business ownership constraints, idempotent initialization,
5s busy timeout, private file creation, lock errors and sanitized exception paths.
New restore checks reject unrelated SQLite databases and missing ownership guards;
a controlled post-copy disk failure removes only the newly created output, preserves
the source, and allows a subsequent valid restore. Existing-target refusal is verified
byte-for-byte. SQLite-supported online backup remains the copy mechanism. These are
controlled failure tests, not physical disk-full/power-loss certification.

## Browser and visual evidence

Installed Chrome 155 used a disposable profile and fictional Flask/SQLite store.
No private/working inventory was used. The listed browser connector skill was absent;
Chrome's local debugging protocol and an already available temporary helper supplied
the browser checks. No helper was added to project dependencies.

- **40 workflow/layout assertions** passed: SKU search, tap add, customer/guest
  context, saved notes, discounts, hold/resume, cash preview, confirmation, receipt,
  print-media navigation hiding, saved Designer preset, chart inspection and long
  carts. No JavaScript/resource errors were captured in the final run.
- Checkout horizontal bounds/Charge accessibility were checked at 1440, 1280, 1024,
  768 and 390px widths. Long carts scroll independently; mobile has its cart shortcut.
- **30 additional bounds assertions** passed for search icon/input alignment, header controls, multiple feedback messages, rail height, viewport overflow and Charge access at 1440/1280/1024/390px, plus F2 and SKU search. No captured browser errors.
- **Nine explicit payment-scroll/receipt checks** passed: short and long carts at
  widths 1440/1024 and heights 768/900. At maximum dock scroll, the entire Complete
  Sale rectangle was inside all clipping ancestors and the viewport, hit testing
  reached the enabled button, and bottom clearance was **43.9–44.1px**. Confirmed cash
  checkout then navigated to the finalized receipt.
- Final screenshots were captured at all five widths in `screenshots/day27/` and
  inspected. Header/search alignment, feedback placement, category rail, cart and
  Charge remained usable in those captures. `DAY27_BROWSER_RESULTS.json` records
  the workflow assertions and bounds. Earlier transient browser resource noise did
  not recur in the final run; the final capture pass reported no errors.

Manual/unexecuted: Firefox/Safari rendering, real touch hardware, screen-reader
announcements, physical function-key injection, complete high-zoom/200% acceptance,
real print output and the eventual public HTTPS origin. F2 was checked through a
browser DOM keyboard event; source tests also cover Alt+C/focus and dialog behavior.
Semantic HTML/label/CSRF/contrast checks pass, but this is not WCAG certification.
No remote penetration test, sustained host load test or deployment was performed.

## Local performance evidence

Each listed page uses the median of three requests, measured during concurrent local
QA activity. No before/after speedup is claimed; no speculative index/cache was added.

| Representative operation | Median ms |
| --- | ---: |
| Dashboard, 300 products / 1,000 sales | 103.743 |
| Inventory search | 35.341 |
| Transaction page | 30.976 |
| Sales analytics | 57.990 |
| Customer search, 300 customers | 22.046 |
| Purchase list, 100 orders | 31.856 |
| Expenses, 300 records | 29.464 |
| Financial report | 46.447 |
| Financial CSV | 32.693 |

Single-checkout median was 11.149ms over three authoritative checkout samples, with exact stock reconciliation. Receiving took 10.968ms
in the retail fixture. Four concurrent buyers took 69.810ms with exactly two available
units sold; two duplicate refund requests took 18.933ms and created one refund. SQL
plans used `alerts_unread`, `alerts_store_kind` and `expenses_store_category` with
store-qualified conditions. All fixture integrity checks passed. Larger analytics
are bounded and refuse oversized reports rather than silently truncating totals.
The dashboard retains some duplicated legacy overview reads; at this measured scale
there was no demonstrated need for an architectural cache or new index.

Reproduce the existing disposable benchmarks:

```bash
PYTHONPATH=. .venv/bin/python -c 'from scripts.qa_benchmark import scenario; import json; print(json.dumps(scenario(300,1000),indent=2))'
PYTHONPATH=. .venv/bin/python scripts/retail_benchmark.py
```
