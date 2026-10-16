# Day 27 completion report

**Day 27 implementation/assessment: COMPLETE. Release readiness: CONDITIONAL.**
This means the requested audit, targeted hardening, tests and readiness assessment
were completed. It does not mean hosting/HTTPS is deployed or that TCCP is approved
for real commercial operation. All transactions remain simulated.

## Executive summary and actual fixes

The baseline was clean at `fe77cd091e48bc56972f8cd205e68a015ee43a08` on
`feature/commercial-ui`. The full baseline passed 253 tests and 155 subtests.
Source/route/service/schema review found configuration and restoration weaknesses,
not a demonstrated authentication bypass or ledger-math failure.

1. Security flags accept only 0/1; misspelled environment names fail startup instead
   of falling back to writable development settings.
2. Production registration defaults closed. The independent explicit setting is
   documented, routes refuse closed registration without identity/rate-bucket writes,
   and account-creation links disappear. Existing sign-in and read-only demo work.
3. Production rejects a database under the public static root, preventing a dangerous
   operator misconfiguration from publishing the file through Flask static serving.
4. Restore verifies the TCCP schema/guard manifest and integrity before and after
   SQLite backup. It refuses existing targets and removes only newly created failed
   output, preserving the source. Backup remains SQLite's online API.
5. Added production/restore and combined three-store business/financial release gates.
   No checkout engine, calculation, financial ledger, migration, database model,
   Tkinter module or runtime dependency was changed.

Risk severities, source boundaries and references are in
[DAY27_SECURITY_AUDIT.md](DAY27_SECURITY_AUDIT.md). No known critical/high issue
remains in the reviewed/tested paths. The remaining medium deployment conditions
are external HTTPS/domain/disk/backup/proxy verification; manual accessibility and
cross-browser work remain conditional.

## Files changed

- `web_app.py`, `auth_routes.py`: strict startup flags/environment, registration gate,
  production DB/static-root rejection.
- `manage.py`: validated new-target restore and failure cleanup.
- `templates/auth.html`, `templates/base.html`: reflect registration closure.
- `.env.example`, `README.md`: configuration/operational guidance.
- `tests/test_day27.py`: eight new release tests and eight subtests.
- Four Day 27 audit/test/readiness/completion documents, `DAY27_BROWSER_RESULTS.json`
  and five fictional-data screenshots under `docs/screenshots/day27/`.

All prior committed work is retained. No automatic staging, commit, push, migration
of working data or deployment was performed.

## Final verification

| Gate | Result |
| --- | --- |
| Full Python suite | **261 passed, 163 subtests passed, 455.78s** |
| JUnit including subtests | 424 cases; **0 failed, 0 errors, 0 skipped** |
| New Day 27 tests | 8 passed, 8 subtests passed |
| Three actual-source JavaScript suites | **36 passed, 0 failed** (24 + 7 + 5) |
| Chrome workflow/long-cart checks | **40 assertions passed** |
| Explicit search/header/toast/rail bounds | **30 assertions passed** |
| Explicit short/long-cart payment/receipt checks | **9 passed**; 43.9–44.1px button clearance |
| Ruff / compilation / Gunicorn configuration / pip consistency | Passed |
| Installed-package advisory audit | 43 packages; 0 reported advisories |
| Working DB read-only integrity / foreign keys | `ok` / 0 violations |
| Disposable source-copy repeat initialization | 25 tables' original values preserved twice; original bytes unchanged |
| Tracked sensitive files / credential-pattern source matches | 0 / 0 |

The final full run followed all code fixes. The intermediate 260-test run preceded
one added static-path production test; it is not presented as the final suite.
Overlapping focused test groups are not additive. Full commands, evidence,
exceptions and exact accounting values are in [DAY27_TEST_RESULTS.md](DAY27_TEST_RESULTS.md).

Financial verification covers discounted customer/held cash sales, durable duplicate
keys, partial/full receiving, restocked/non-restocked refunds, original cost snapshots,
expense reversal/correction, dashboard/analytics/financial/CSV reconciliation and
concurrent stock. The combined three-store golden workflow ends with each store at
25 units, a 271-cent sale fully refunded, 200 cents of received purchase cost and
20 cents of net expense, producing -20 cents estimated operating result. Purchase
costs are not silently expensed. Unknown costs/tax remain explicitly unavailable.

Migration/backup verification includes supported legacy fixtures, idempotence,
missing-guard/version-gap rejection, injected DDL rollback, source-copy comparison,
private backups/restores, unrelated/guardless-source refusal and controlled failed
restore cleanup. Actual disk-full/power-loss recovery and remote backups still need
host-specific operational rehearsal.

## Browser, accessibility and performance

Chrome 155 verified fictional-data checkout, customers/discounts/notes, hold/resume,
server cash change, confirmation/receipt, Designer persistence, chart date inspection,
short/long carts and responsive bounds. Five final screenshots at 1440, 1280, 1024,
768 and 390px were captured and inspected. Complete Sale was fully reachable at
both 768px and 900px heights with an unobstructed enabled hit target.

Semantic markup, labels, CSRF forms, contrast pairs, focus restoration and source
keyboard interactions pass existing tests. F2 browser testing used a DOM keyboard
event. Real screen readers, Safari/Firefox, actual touch/print, full high-zoom coverage
and the deployed HTTPS origin were not verified. These limitations are not counted
as passed tests or accessibility certification.

Local medians: dashboard 103.743ms over 1,000 sales, analytics 57.990ms, customer search
22.046ms, purchase list 31.856ms, expense report 29.464ms. Single authoritative checkout
median: **11.149ms**, three samples, with exact stock reconciliation. Query plans used
expected store-scoped indexes; no measured need justified speculative caching/index
changes. These are local samples, not hosting latency or sustained-load guarantees.

## Per-category release assessment

| Category | Status | Evidence / condition |
| --- | --- | --- |
| Security | CONDITIONAL | Reviewed protections/new fixes pass; not an independent penetration test; host controls outstanding |
| Store isolation | PASS | Three owners across business ledgers/layouts/holds; hostile HTTP/service/SQL regressions |
| Checkout integrity | PASS | Tokens/claims, atomic writes, races, rollback and browser cash receipt |
| Financial correctness | PASS | Exact cents, refunds/costs/tax/expenses and report/export reconciliation |
| Inventory consistency | PASS | Conditional updates, receiving/refund/stock audits and last-unit races |
| Purchasing | PASS | Partial/full receiving and duplicate receipt protections |
| Customers | PASS | Association/history, private queries, hostile IDs and protected exports |
| Migrations | PASS | Legacy fixtures, ordered guards, rollback and repeat source-copy initialization |
| Backup/restore | PASS | Online backup, verified separate restore, source preservation and failure cleanup |
| Performance | CONDITIONAL | Representative local benchmarks; sustained hosted load not tested |
| Browser usability | PASS | Tested Chrome widths/heights, screenshots, explicit final-button hit/clip checks |
| Accessibility | CONDITIONAL | Semantic/contrast/focus checks pass; screen-reader/device/cross-browser review outstanding |
| Public demo safety | PASS | Fictional separate store, read-only business guard, closed production registration by default |
| Production configuration | CONDITIONAL | Source/subprocess checks pass; real TLS/domain/proxy/storage/monitoring not configured |

**Ready for further deployment preparation: YES.**
**Restricted fictional-data public demo: CONDITIONAL on Day 28 hosting checks.**
**Real commercial operation / real payment processing: BLOCKED and outside this release's scope.**

Day 28 should follow [DAY27_PRODUCTION_READINESS.md](DAY27_PRODUCTION_READINESS.md):
select persistent hosting, configure actual HTTPS/hosts/secrets/edge limits, rehearse
private backup/restore and rollback, keep registration closed/read-only public access,
complete manual browser/accessibility checks, and rerun scans on the final build.
Current web checkout uses current server prices; it is not a fixed-price reservation
or a commercial quotation/settlement system. No commercial accounting/tax-filing or
real-payment capability is implied by these checks.
