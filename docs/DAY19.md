# Day 19 — database and security hardening

## Scope and evidence

The starting branch was `feature/security`, with a clean working tree. The audit covered Flask configuration/entry points and blueprints, every store-specific service/SQL path, the four existing versioned migrations, database lifecycle commands, auth/session/CSRF handling, templates/scripts, dependencies, ignored/tracked files, demo mode, and desktop compatibility. All payment and refund behavior remains simulated. No working database was migrated or reset, and no financial business rule was changed.

## Prioritized findings and remediation

| Priority / severity | Finding and affected files | Remediation / evidence |
|---|---|---|
| 1 / Medium, integrity | Migration version markers were trusted without checking required tables, columns, indexes or protection triggers (`database/database.py`, existing migrations). An incomplete/damaged schema could be accepted or partly recreated. | `database/validation.py` checks ordered known versions and version-specific schema/protection manifests before DDL, then validates current schema after migration. SQLite quick/integrity and foreign-key checks detect inconsistent data. Missing protections, unknown/gapped markers and injected migration failure are tested without automatic repair. |
| 2 / Medium, local confidentiality | SQLite/backup/restore files could initially inherit permissive process umasks; backups were only chmodded after copying (`database/database.py`, `manage.py`). | New files are created exclusively with mode 0600 before SQLite opens them. Restore/backup never overwrite an existing target. Existing files are not silently chmodded. POSIX production startup refuses group/world permissions. Private creation and backup/restore-to-new-path are tested. |
| 3 / Medium, defense in depth | CSP allowed inline JavaScript to support two print handlers (`web_app.py`, receipt/refund templates). | Print controls use `data-print` and the shared external script. Script CSP is self-only with inline handlers explicitly prohibited. Header and escaping tests plus the frontend print interaction check pass. Inline **styles** remain for bounded server-generated chart heights. |
| 4 / Medium, logging | Checkout exception logging and Flask's default handling of unexpected exceptions could emit raw exception messages (`web_app.py`). | SQLite and unexpected errors log endpoint/operation and exception class only, without exception text, cookies, query values, bodies or tracebacks outside explicit local debug. Injected sensitive-message tests verify redaction. |
| 5 / Low, future-route protection | CSRF/read-only enforcement only tested POST; form endpoints did not explicitly reject unsupported JSON/types or ambiguous duplicate parameters. | All unsafe methods are covered; form content types, duplicate fields/query parameters, conflicting CSRF tokens, uploads and request/query size are validated. Header CSRF is supported for form requests. Existing endpoints are forms, not JSON mutation APIs. |
| 6 / Low, configuration | Host headers were unrestricted (`web_app.py`). | Production defaults to loopback-only trusted hosts. Deployments explicitly configure `TCCP_TRUSTED_HOSTS`. Rejected hosts return a safe plain 400; tests found and fixed the otherwise missing URL-adapter error path. Existing secure cookies/HSTS/no-store settings remain. |
| 7 / Low, input consistency | Integer parsing accepted Python-specific forms such as underscores; visible-text fields could contain control bytes (`services/`, form routes). | Shared bounded integer/identifier and visible-text validation rejects invalid types/ranges/control characters. Markup-like text remains legitimate text and is escaped, rather than altered by homemade HTML sanitization. |

No confirmed critical/high remote authorization vulnerability was found in the inspected routes. This statement is bounded by the performed tests and review, not a guarantee of no vulnerabilities.

## Database architecture and migration safety

Existing versions **14, 15, 16 and 17 are retained**. Day 19 introduces no new schema version or destructive/table-rebuild migration. Monetary values, original sale snapshots, immutable refund ledgers, stock baselines and adjustment history remain unchanged. The existing migration sequence executes inside one `BEGIN IMMEDIATE` transaction; failure rolls back DDL and version records as well as data.

The new validation requires known consecutive version markers and expected columns, named indexes and protection triggers for each applied version. It checks ownership/numeric guards, desktop legacy-store normalization, audit immutability and refund caps. This is structural validation of expected protections, **not cryptographic tamper detection** or a claim that a privileged database administrator cannot rewrite SQL definitions. No missing protection is silently repaired in a supposedly migrated database.

`PRAGMA quick_check` and `foreign_key_check` run around initialization, and production startup validates the current schema/data before serving requests. SQLite data connections enable and verify foreign keys and use a five-second busy timeout. Maintenance/startup connections also enable foreign keys; backup/restore use SQLite's backup API rather than copying a live file piecemeal. `manage.py check` retains full `integrity_check` plus foreign-key reporting.

Ownership validation detects mismatched product/sale/item stores and cross-store category assignments. Legacy identity migration still places original data in the explicitly unclaimed legacy store. Registration never claims it. No records are silently moved between stores.

The existing explicit `repair-missing-products` workflow is preserved. Its migration call may tolerate **only genuine missing product references** in transaction items/baselines/stock history; other foreign-key or ownership failures remain blocked. The subsequent repair creates inactive, clearly named placeholders, preserving historical item values. A final full integrity check is required. This is a trusted local maintenance operation, never exposed through HTTP. Migration calls are explicitly denied inside Flask request contexts.

### Backup, migration, recovery

Stop all web and desktop writers first. Use one OS service identity and a private local disk directory; do not expose the SQLite file or backups through static hosting, shared writable directories or network storage.

```bash
# Explicit maintenance; init backs up an existing database before migration.
.venv/bin/python manage.py init
.venv/bin/python manage.py check

# Optional additional backup; destination must not already exist.
.venv/bin/python manage.py backup --file /private/path/before-maintenance.backup

# Recovery always targets a NEW path; never overwrite the current database.
TCCP_DB_PATH=/private/path/restored.db .venv/bin/python manage.py restore --file /private/path/verified.backup
TCCP_DB_PATH=/private/path/restored.db .venv/bin/python manage.py check
```

If validation fails, preserve the original file and backup, stop writers and investigate. Unknown/newer versions, missing schema protections and ownership mismatches are not repaired by guessing. Restore only a verified backup to a new path, validate it, and use the matching application version. Never discard sales/refunds recorded after the backup without an explicit reconciliation decision. A failed backup may leave its newly reserved destination incomplete; preserve/inspect it and choose a new destination for retry. `restore` rejects a backup that fails integrity checks.

New files are created as 0600, avoiding an initial world-readable window and rejecting a racing existing destination. Existing file permissions remain unchanged during development. Before POSIX production startup, an administrator must set the chosen, already verified database to private permissions:

```bash
chmod 600 /private/path/tccp.db
```

Protect the parent directory (typically 0700), backups and disk encryption separately. File permissions cannot protect data from the OS account running the application or a privileged administrator.

## Input, SQL and authorization boundaries

Values in SQL remain parameterized. Dynamic sort expressions, schema identifiers and repair-table names come exclusively from source-code allowlists. Inventory, category, stock, sale/item, receipt, refund, chart, report and export access uses the server-authorized store. Composite foreign keys and store/actor consistency triggers add database defenses.

Current mutations accept URL-encoded or multipart HTML forms; JSON bodies, including malformed JSON, and other unsupported content types return **415**, rather than inventing a JSON API. Files and duplicate form/query fields return 400. Total request size remains 64 KiB; multipart part count is bounded at 1,100 to accommodate the existing 1,000-line refund ceiling. Query strings are limited to 8 KiB. Existing per-field limits, report limits and sort/date allowlists remain.

CSRF applies to POST/PUT/PATCH/DELETE and other unsafe methods. A valid form token or `X-CSRF-Token` is required; conflicting header/body tokens are rejected. Current routes do not implement JSON mutations; any future JSON endpoint needs an explicit security/design review, content-type policy and equivalent CSRF/auth handling. Missing authentication still blocks private routes before side effects; no hidden button is treated as authorization.

Identifiers and quantities use bounded whole-number parsers. Product/store names reject control bytes and invalid lengths; stock/refund reasons permit ordinary multiline whitespace but reject unsafe control bytes. Passwords keep the existing passphrase policy and are not normalized or silently changed. Invalid monetary values still use the exact-cent/Decimal validators; rounding, taxes and refund allocation rules are unchanged.

**Ownership-field compatibility:** existing form contracts discard posted `store_id`/`user_id` rather than binding them; authorization always comes from the verified session. The preexisting regression test deliberately posts foreign ownership fields and verifies that a newly created product belongs only to the authorized store. That test is retained. This is rejection of the ownership assignment, not a new HTTP-400 policy for every unused form field. Duplicate/ambiguous fields are explicitly rejected. Services do not expose mass-assignment ownership arguments.

## Authentication, sessions and demo mode

Werkzeug scrypt password hashes, normalized unique email addresses, atomic registration and persistent HMAC login/rate-limit buckets are preserved. Login creates a random opaque token; only its digest is stored in `auth_sessions`. The signed HttpOnly Flask cookie carries the token and small cart/CSRF references, not passwords or payment card data. Membership and expiry are rechecked on requests. Fixed server-side token expiry is eight hours; login/account switching revokes the previous current token and clears cart/receipt/CSRF state. Logout revokes the server-side row before clearing the browser state, so copying a cookie does not bypass revocation. Other independent devices remain signed in unless their own token is revoked/expired; there is no all-devices logout or password recovery workflow.

Production requires a secret of at least 32 nonblank-trimmed characters and an absolute existing, verified database path. Generate a high-entropy secret rather than relying on the length check to measure entropy. Development may generate an ephemeral secret, invalidating sessions after restart. Secure/HttpOnly/SameSite=Lax cookies, no-refresh session behavior, safe local return URLs and disabled production debug remain intact. Registration/login error messages and established throttling are preserved; no password/token/body logging was added.

Production `TCCP_TRUSTED_HOSTS` is comma-separated hostnames without scheme or port, for example `pos.example.com`. Missing/blank values default to `localhost,127.0.0.1,[::1]`, so a public hostname must be configured deliberately. Flask receives the actual Host from a trusted HTTPS reverse proxy. The application does **not** blindly trust `X-Forwarded-*` or arbitrary proxy hops. Serve HTTPS at the edge, keep Gunicorn private, limit request/header sizes and apply suitable edge rate limits. The app does not provision HTTPS or certify a proxy configuration.

Demo entry still selects only the fictional demo store on the server. Demo sessions are separate from authenticated accounts, and business writes remain forbidden even with valid CSRF. Trusted local desktop/CLI access remains fixed to legacy store 1; it is not an HTTP authentication bypass. Do not grant web users filesystem access to the application/database.

## XSS, headers and logging

Jinja autoescaping, quoted HTML attributes, `textContent` for messages, server-rendered historical snapshots and CSV formula neutralization remain. Representative script/SQL payloads were tested only against local fictional data. Cart HTML fragments originate from same-origin Flask rendering and verify account context before replacement; no arbitrary external HTML is fetched.

Headers include self-only scripts, `script-src-attr 'none'`, self-only connections, no workers/objects, self-only form actions/base URI, CSP frame-ancestors none, X-Frame-Options DENY, nosniff, same-origin referrer policy, restricted camera/microphone/geolocation/payment permissions, and no-store/private responses. Production adds one-year HSTS. Inline chart styles remain supported; CSP does not replace escaping, CSRF or authorization.

HTTP 400/401/403/404/405/409/413/414/415/429/500 use safe responses while preserving status codes and protocol headers. Invalid hosts intentionally return a small plain 400 without reflecting the Host. Expected database failures return safe 503 messages. Outside explicit local debug, unexpected exceptions are handled before Flask's default exception-text logging and record only endpoint plus exception class. Local debug can retain useful tracebacks and must never be exposed publicly. Reverse-proxy/WSGI access logging is an operator responsibility: avoid bodies, cookies, credentials and sensitive query strings.

## Dependency and tracked-file audit

Runtime/dev requirements remain unchanged; no unnecessary major upgrade or new package was introduced. `pip-audit -r requirements.txt` completed with **no known vulnerabilities found** at verification time. The sandbox attempt could not prepare the audit environment; the approved network-enabled retry succeeded. `pip check` reports no broken requirements. Advisory scans cover known published issues and do not prove absence of vulnerabilities.

`.gitignore` now includes `.env.*` except the safe `.env.example`, SQLite filename variants/journals, additional backup suffixes and private-key formats. Existing databases, virtual environments and backups remain ignored. No tracked file was removed. The final tracked-sensitive-file and diff review found no introduced secrets or tracked private databases/keys. `.env.example` contains placeholders only.

## Verification commands

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/python -m pytest -q tests/test_security_hardening.py
.venv/bin/ruff check .
.venv/bin/python -m pip check
.venv/bin/python -m pip_audit --cache-dir /tmp/tccp-audit -r requirements.txt
.venv/bin/python manage.py check
# Optional, Node 20+; no npm packages:
node tests/frontend_checks.mjs
```

The new security tests use real Flask requests and disposable SQLite databases. Fault injection is limited to rollback/logging tests. Coverage includes schema/marker mismatch, lost protection triggers, unknown versions, migration rollback/idempotency, FK/busy configuration, orphan preservation and explicit repair, private creation/restore refusal, malformed types/JSON, duplicate fields, all unsafe-method CSRF, header-token conflicts, request limits, hostile text, SQL strings, strict script CSP, production host/secret/cookie/file-mode checks, redacted error logging, and three independent accounts plus demo restrictions.

Existing regression tests continue to cover registration, hashes, session expiry/revocation, throttling, unsafe redirects, IDOR, all route protection, financial cents/tax, duplicate/concurrent checkouts and refunds, stock/audit rollback, analytics/CSV reconciliation, UI and Tkinter. A copy of the existing pre-Day-19 database was validated and initialized twice, retaining all original values across **13 existing tables**, with integrity OK, zero foreign-key violations and unchanged original-file hash. No production or working database changes were made.

## Remaining limitations and deployment responsibilities

- No interactive browser or real assistive-technology QA was available. JavaScript checks use DOM doubles; CSP/print behavior still needs browser review.
- SQLite and its persistent rate limiter assume a private local disk and one application deployment. Backups, monitoring, disk encryption, HTTPS, trusted proxy configuration and recovery drills remain operational responsibilities.
- Inline chart styles, legacy signed-cookie cart edits across tabs, generic registration failure inference, absent email verification/recovery/MFA/staff roles and no all-device session revocation remain documented limitations.
- Schema checks validate a known structural manifest, not arbitrary malicious database-admin edits. Existing bad/unsupported legacy data may cause migration to stop safely; it is never silently rewritten.
- No ownership-claim endpoint or new financial rule was added. Posted ownership fields remain ignored by the established form contract and cannot affect store authorization.
- No certification or zero-vulnerability guarantee is claimed.

References: [Flask security guidance](https://flask.palletsprojects.com/en/stable/web-security/), [SQLite PRAGMA/integrity guidance](https://www.sqlite.org/pragma.html), [OWASP CSRF prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html).

## Route/data-access map

| Surface | Authorization/data boundary |
|---|---|
| Registration/login/logout/demo | Explicit public form endpoints; global CSRF, persistent throttling, revocable identity tokens; demo lookup uses only server-selected `kind='demo'` |
| Dashboard and stock alerts | Authorized-store inventory/transaction queries; no client-selected store; gross/refund/net calculations unchanged |
| Products/inventory/categories | Explicit scoped services, allowlisted filters/sorts, category-owner validation, stock audit triggers |
| Stock adjustments/history | Scoped product/history IDs and actor/store checks; atomic updates and immutable audit records |
| Cart/checkout | User/store-bound signed session context; active scoped products; authoritative integer cents; immediate transaction, conditional stock deduction and persisted checkout token |
| Transactions/receipts | Scoped history/detail services and historical snapshots; foreign and absent IDs have the same missing-record behavior |
| Refunds | Scoped original sale/items, signed user/store-bound quote, durable per-store request key, cumulative quantity/amount caps, atomic ledger/restock/audit |
| Analytics/reports/exports | Store-scoped read snapshots, historical item joins, bounded date/record limits, fixed export kinds/filenames and formula protection |
| Account | Verified current identity/store only; no unsupported settings mutation route introduced |
| Desktop/CLI/models | Trusted local legacy-store context; no public route uses the desktop global cart; plain models perform no independent unscoped SQL |
| Migration/backup/repair | Trusted local maintenance only, explicit backup/recovery path; initialization denied inside HTTP contexts |

## Changed files

Created `database/validation.py`, `services/validation.py`, `tests/test_security_hardening.py` and `docs/DAY19.md`.

Modified `database/database.py` and `manage.py` for validation/private file creation/maintenance checks; `web_app.py` for request/CSRF/content-type/host/header/error/logging hardening; `services/auth.py`, `services/inventory.py`, `services/inventory_management.py` and `services/refunds.py` for visible-text/identifier validation; `static/js/ui.js`, `templates/receipt.html` and `templates/refund_detail.html` for external print handling; `tests/frontend_checks.mjs` for the actual print interaction check; `.env.example`, `.gitignore` and `README.md` for configuration and documentation. Dependency, existing migration, model and Tkinter source files are unchanged.

## Final verification results

- Existing regression suite with hardening changes: **144 tests passed, 58 subtests passed**.
- Final complete suite: **165 tests passed, 58 subtests passed in 135.95 seconds**, with `.venv/bin/python -m pytest -q -p no:cacheprovider`. This includes 21 new security tests. No existing test was removed, skipped or weakened.
- Frontend actual-script interaction checks: **18 passed, zero failed**, through the available JavaScript runtime using `tests/frontend_checks.mjs`. This includes external print handling compatible with the stricter script policy. These are DOM-double checks, not browser/CSP execution.
- Ruff, Python compilation, Gunicorn configuration, `pip check` and `git diff --check` passed.
- Both requirements-based and installed-environment `pip-audit` scans completed with **no known vulnerabilities found**. The installed scan includes development packages.
- A disposable copy of the pre-Day-19 working database was initialized twice; every original column value across 13 existing tables was preserved. Integrity was OK, foreign-key violations were zero, and the original-file hash was unchanged.
- Production tests verified strong/nonblank-secret requirements, trusted/invalid hosts, secure cookies, HSTS, debug disabled, private database file mode, and schema checks. Existing auth/session expiration/replay/limiting and financial/concurrency suites passed.
- Three-account hostile HTTP/service requests and independent demo restrictions passed. New validation-recovery tests restore a backup only to a new path and verify that an existing target cannot be overwritten.
- Final tracked-file/ignore/diff review found no introduced secrets or tracked private databases/keys; representative secret/SQLite/backup filename variants are ignored.

**Day 19 status: COMPLETE.** Critical authorization, financial/database integrity, migration and regression checks pass. Browser QA, HTTPS/proxy operations, backup retention and documented residual limitations remain the operator's responsibility. No working/production database modification, commit, push or deployment was performed.
