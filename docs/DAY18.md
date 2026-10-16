# Day 18 — UI/UX polish

## Scope and repository audit

The clean starting branch was `feature/ui`. Flask remains in `web_app.py`, with auth, inventory, analytics and refund blueprints. All templates, shared styles/scripts, route rendering/error paths, dependencies, existing tests, schema/migrations and preceding milestone documentation were inspected using the existing Day 14–17 architecture audit as context. The visual work preserves the SQLite schema, authorized services, exact-cent money operations and atomic checkout/refund/stock transactions. Tkinter, database lifecycle and payment simulation behavior remain intact.

The audit found a working UI with accumulated, repeated CSS across milestones; inconsistent panel/button/form treatment; a sidebar without a mobile disclosure; some unwrapped refund tables; inconsistent table header semantics; category/error pages with limited empty/error treatment; no shared pending-feedback mechanism; and business write controls visible in read-only demo screens. Authentication, CSRF, store isolation and idempotency were already implemented and remain the security boundary.

## Shared presentation system

`static/css/design-system.css` loads after the existing structural stylesheet. It centralizes navy/blue/light-surface colors, text colors, typography, spacing, radii and shadows, plus shared buttons, form controls, panels, KPI cards, tables, badges, alerts, empty states, dialogs, loading notices and print styles. Existing structural classes are retained to avoid destabilizing every historical layout. Legacy CSS still contains duplicated rules; the shared presentation sheet is the final source for component appearance. A full deletion/rewrite of the legacy stylesheet was deliberately avoided during a functional milestone.

The added CSS is roughly 20 KB, without fonts, image assets, icon packages, chart libraries or a frontend framework. System fonts and existing lightweight glyphs remain. There are no new runtime or Python dependencies.

`templates/components/ui.html` provides reusable active-navigation, empty-state and error-summary macros. `base.html` provides a single main landmark, skip link, store/account identity, secure POST logout, a simulation/read-only banner, a shared native confirmation dialog and accessible operation status/notification regions. No credentials, tokens or account objects are embedded in executable JavaScript. A DOM context attribute contains only the current authorized user/store identifiers so an account change forces a full cart-page navigation rather than mixing fragments from different sessions.

## Page-by-page changes

| Pages | Improvements |
|---|---|
| Dashboard | Consistent KPI surfaces, restrained chart color, responsive alert grids, table headers, keyboard-scrollable chart region, write actions omitted in read-only mode |
| Inventory | Unified filters/table/badges/pagination, touch-friendly actions, keyboard-scrollable table, read-only mutation controls omitted |
| Product create/edit | Consistent form grid, labels/focus, preserved correction reason, read-only explanation instead of mutation form |
| Categories | Shared empty/error states, consistent create/rename/remove layout, retained invalid create input, read-only category list |
| Stock adjustment/history | Preserved invalid amount/reason/type, error summary, consistent filters, responsive audit table and source links |
| Checkout/cart | Catalog and cart share the component system; active stock feedback; authoritative totals; preserved tender/method during cart refresh; focused quantity field restored where it still exists; shared simulated-checkout confirmation |
| Transactions/details | Status badges distinguish completed/partial/full refunds using text; consistent filters, historical item table, actions and refund history |
| Receipts/refund result | Responsive, wrapped historical text and tables, existing browser print/save-PDF retained, application chrome omitted when printing |
| Refund preview/confirmation | Scrollable item tables, preserved selected quantities/reason/restock on validation errors, consistent confirmation and pending states; server-calculated financial rules unchanged |
| Analytics/reports | Consistent KPI/filter/export components, accessible text/table alternatives retained, scrollable charts, real download feedback and errors |
| Login/registration | Consistent private-workspace presentation, preserved safe email/store input, password visibility controls with accessible names and pressed state; passwords are never re-rendered |
| Account | Store/email/access definition list and consistent dashboard/logout controls |
| Errors/404 | Safe common presentation, accessible focusable summary where applicable, clear recovery/history links, appropriate original HTTP statuses |
| Public demo | Clear read-only identity; products/categories/analytics remain browsable; no business POST forms or write-action links; logout and authorized exports remain available |

Read-only presentation is determined by `ui_can_write`, a context helper. It is **not authorization**. Existing route guards and service checks still reject direct unauthorized requests. Login, registration, demo entry and logout keep their existing public/authenticated rules.

## Navigation, responsive behavior and accessibility

- Desktop uses a 240px fixed navy sidebar with active-page indicators and `aria-current`. Inventory tools stay in the Inventory group; receipts/refunds stay in Transactions.
- Below 1024px the sidebar becomes an in-flow header, preventing content overlap. The native `<details>` menu supports keyboard use without custom focus trapping. JavaScript initially collapses it on mobile; without JavaScript it remains open and usable.
- Below 1200px checkout stacks catalog/cart. Below 768px forms and report/alert grids stack, while most KPI cards use two columns. Below 375px KPI cards become one column. Content, long identifiers and controls can wrap; table/chart overflow is confined to keyboard-focusable regions.
- Visible focus outlines, a skip link, semantic labels, decorative-icon hiding, column header scopes, live status regions and focusable error summaries are shared across pages. Text accompanies stock/refund status colors.
- Shared controls target 44px height; mobile inputs use 16px text. Reduced-motion preferences disable animation/transitions and smooth scrolling. Essential pending text remains when the spinner does not animate.
- The native `<dialog>` confirmation supplies accessible heading/description, browser-managed modal focus containment/Escape behavior, cancel-first autofocus and explicit focus return. Older browsers fall back to native `confirm`. With JavaScript disabled, forms still submit normally; custom confirmation/loading enhancements are unavailable. Server-side validation, CSRF, idempotency and authorization are unchanged.
- Automated contrast checks calculate actual token-pair luminance and require at least 4.5:1 for primary/secondary/muted body text, primary button text and success/warning/error badge text. This is a targeted check, **not a complete WCAG conformance claim**. References: [WCAG contrast minimum](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html) and [target size minimum](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html).

No interactive browser or automated browser/accessibility engine was available. Therefore **320px, 375px, 768px, 1024px and 1440px rendering have not been visually verified**. The implementation and static layout review cover those breakpoints, but overflow, zoom, screen-reader behavior, actual keyboard focus containment, touch use and print preview need manual browser QA. No screenshots were created or claimed.

## Pending, confirmation, empty and error states

`static/js/ui.js` owns common native-form pending states, confirmation, menu enhancement, password visibility and CSV downloads. `checkout.js` exclusively owns asynchronous cart requests; `analytics.js` retains chart inspection and preset behavior. This avoids competing submit handlers.

Pending feedback begins only after a valid, uncanceled submit or an actual export fetch. Live status text and a dismissible notice explain the operation; submit controls are disabled while pending. Native forms preserve successful submitter values by delaying disabling until the next frame. A slow native navigation restores controls after 30 seconds and explains that history must be checked before retrying. This is an uncertain-result notice, not a claim that the server canceled an operation. Back/forward restoration resets controls; cached private pages reload instead of showing a previous account's DOM.

Cart requests have a 30-second network timeout and restore controls on failure, showing an inline error that advises verifying history before retrying. Exact prices/tax/totals always come from Flask. No local/session storage caches financial or account data. A changed response account context triggers a full navigation. Server checkout/refund idempotency remains the durable duplicate-financial-operation protection; generic UI guards are an additional convenience.

CSV links work as ordinary downloads without JavaScript. Enhanced downloads announce pending/completed/error states, honor modified clicks, reject non-CSV/error responses, time out after 45 seconds, abort on page navigation and release object URLs. Failed downloads remain visible until dismissed; completion notices disappear after a short delay. Existing export resource limits, formula protection, authorization and no-store headers remain.

Existing empty states are retained and styled consistently. Categories use the shared component. Read-only empties do not offer prohibited creation/sale actions. Error summaries retain safe entered text for product, stock, auth and refund forms where supported. Passwords are never echoed. Shared HTTP error handling now includes 401/405/429 as well as existing 400/403/413 and safe database/server errors; original status codes are preserved. Errors do not reveal traces or SQL details.

## Testing and local verification

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/python -m pytest -q tests/test_ui.py
.venv/bin/ruff check .
.venv/bin/python -m pip check
# Optional frontend interaction checks; Node 20+ with no npm packages:
node tests/frontend_checks.mjs
```

`tests/test_ui.py` exercises real Flask pages/databases: markup structure, duplicate IDs, landmarks, input labels, CSRF fields, table wrappers/header scope, navigation resolution, active groups, safe error input retention, three independent fictional stores, read-only owners/demo controls and direct unauthorized writes. Existing auth/inventory/checkout/refund/analytics/desktop tests remain enabled.

`tests/frontend_checks.mjs` executes the actual frontend scripts in isolated module copies with DOM doubles. It checks menu breakpoints, password visibility/reset, pending announcements, duplicate-submit guards, cancellation, slow-request recovery, cached-page reload, CSV success/error/modified-click behavior, cart network recovery, authoritative totals, focus/tender retention, account-context changes and shared confirmation cancel/approve/fallback behavior. These are JavaScript interaction checks, **not browser or layout tests**. In this environment they ran through the available JavaScript runtime because the shell has no Node executable.

## Manual browser checklist

At each requested viewport width (320/375/768/1024/1440):

1. Tab from the skip link through navigation, filters and primary actions. Open/close the mobile menu with the keyboard.
2. Verify no page-level horizontal overflow. Scroll long tables/charts with keyboard and touch; test long fictional product/store names.
3. Register/login with fictional accounts A/B/C, navigate every page and switch accounts. Confirm no stale prior-store state after Back or a cart refresh.
4. Exercise inventory/category edits and errors; cancel and approve deactivation/category removal. Verify dialog Escape, focus containment and return.
5. Add/update/remove/clear a cart, switch Cash/Card, retain tender after quantity updates, cancel/approve checkout and inspect the committed receipt.
6. Preview and confirm full/partial simulated refunds, with/without restocking. Test validation, double clicks and disconnected/reconnected networking; verify stock/history before retrying uncertain outcomes.
7. Inspect zero-data analytics, negative net periods, CSV downloads/errors and print/save-as-PDF output. Confirm no app chrome on receipts.
8. Run a browser accessibility scanner and screen reader at normal/200% zoom and reduced motion. Confirm live messages and error focus are useful rather than noisy.

No schema migration, working database modification, dependency installation, real payment integration, commit, push or deployment is part of Day 18. Existing Day 17 migration requirements still apply to any unmigrated deployment. Known limitations include legacy CSS duplication, cookie-session cart edits in multiple tabs, unavailable browser/assistive-technology QA and the existing CSP inline allowances; passing tests cannot prove absence of vulnerabilities.

## Change manifest

Created:

- `static/css/design-system.css`: shared tokens, components, responsive and print rules.
- `static/js/ui.js`: shared navigation, dialogs, pending states, downloads and password controls.
- `templates/components/ui.html`: navigation, empty-state and error-summary macros.
- `tests/test_ui.py`: HTML/HTTP/accessibility/three-account integration checks.
- `tests/frontend_checks.mjs`: dependency-free frontend interaction checks using DOM doubles.
- `docs/DAY18.md`: design, verification, limitations and manual QA instructions.

Modified:

- `web_app.py`: presentation-only permission context and consistent HTTP error pages, preserving original HTTP error response headers such as `Allow`.
- `static/js/checkout.js`: shared pending integration, timeout recovery, tender/focus preservation and account-context validation.
- `static/js/analytics.js`: remove the competing one-off loading handler in favor of shared UI handling.
- `templates/base.html`, `404.html`, `error.html`, `auth.html`, `account.html`: application shell, authentication and safe recovery states.
- `templates/dashboard.html`, `inventory.html`, `categories.html`, `product_form.html`, `stock_adjustment.html`, `stock_history.html`: inventory/dashboard layout, form/empty/error states and read-only actions.
- `templates/sale.html`, `transactions.html`, `transaction_detail.html`, `receipt.html`, `refund.html`, `refund_detail.html`: checkout/financial workflow presentation and responsive tables.
- `templates/analytics.html`, `reports.html`: report/chart/export presentation and semantics.
- `README.md`: Day 18 features, commands and limitations.

No services, models, migration/schema, dependency, Tkinter or production configuration files were changed. No existing automated test was removed, disabled or weakened.

## Final verification results

- Existing regression suite during implementation: **136 tests passed, 42 subtests passed**.
- Final combined Python suite: **144 tests passed, 58 subtests passed in 121.98 seconds**, using `.venv/bin/python -m pytest -q -p no:cacheprovider`.
- Final targeted UI rerun after the final markup/contrast and strengthened isolation assertions: **8 tests passed, 16 subtests passed in 7.24 seconds**.
- Final actual-script JavaScript interaction run: **17 checks passed, zero failed**, using the available JavaScript runtime and the repository's `runChecks()` entry point. This includes confirmation validation recovery; no browser/layout execution is claimed.
- Ruff, all-template compilation, Python compilation, JavaScript syntax, `git diff --check`, `pip check` and Gunicorn configuration checks passed.
- Three fictional account/store flows passed with scoped dashboards, inventory/categories, carts, transactions, receipts, refund access, analytics/reports/exports and account switching. Direct unauthorized/CSRF/demo writes remain rejected. Database integrity checks in the real SQLite fixtures passed.
- HTTP error rendering preserves original status codes and headers; the 405 `Allow: POST` behavior has an explicit regression assertion.
- Diff and tracked-sensitive-file review found no introduced credentials/secrets or tracked working database/private-key files. Service-layer authorization and financial/database code are unchanged. Dependencies are unchanged; no new dependency vulnerability scan is claimed for Day 18.
- No enabled browser was available; no screenshot, visual viewport, real focus-trap, screen-reader or print-preview verification is claimed. Perform the manual checklist before presenting or deploying the UI.

**Day 18 implementation status: COMPLETE.** Critical functional, authorization and regression tests pass. Browser and assistive-technology QA remain explicitly outstanding. No working database migration, commit, push or deployment was performed.
