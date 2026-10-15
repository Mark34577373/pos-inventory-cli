# Day 14: authentication and store isolation

## Audit and boundary map

The existing entry point is `web_app:app`; `main.py` remains a separate Tkinter application. No application factory or API/export/download endpoints existed. Before Day 14, Flask's signed cookies held carts and receipt references, CSRF protected POSTs, checkout used `BEGIN IMMEDIATE`, and all stores shared global queries. The Day 13 baseline had 47 passing tests and 12 subtests.

| Boundary | Data | Day 14 enforcement |
| --- | --- | --- |
| Dashboard `/` | Inventory, counts, recent sales, seven-day revenue | Authenticated context plus every SQL query scoped |
| `/inventory`, `/products/*` | Active products, search, editing, stock, deactivation | Scoped service reads and writes; client ownership fields ignored |
| `/sale`, `/sale/*` | Cart, current prices, stock, checkout | User/store cart context, scoped product lookup, atomic scoped checkout |
| `/transactions`, `/transactions/<id>` | History, filters, snapshots | Store predicate on transaction and item lookups |
| `/receipt` | Printable committed receipt | Session reference checked against current store on every read |
| `/reports` | Payment, date, daily, top-product aggregates | Both sides of joins scoped to current store |
| `/account` | Account email and store identity | Verified membership; demo cannot access it |
| `/login`, `/register` | Credentials | Public forms, CSRF, validation, persistent throttling |
| `/logout`, `/demo` | Authentication context changes | POST only, CSRF, revoke token and clear prior state |
| `services/inventory.py`, `services/transaction_service.py` | All inventory/sales access | `get_store_connection`, authorized store predicate/function, write guard |
| `ui/dashboard.py`, `ui/reports_view.py`, desktop/CLI | Existing local business data | Trusted local client fixed to legacy store 1 |
| `database/`, `manage.py`, `services/auth.py` | Schema, identities, maintenance | Privileged internal access; never expose raw connections to client-controlled SQL |

Product/transaction model classes are simple compatibility containers; they perform no queries. The process-global `session_cart` belongs only to the trusted local desktop/CLI. Flask never uses it. Web carts remain signed browser state and are bound to the server-verified user/store context; changing identity clears them.

There are no current JSON APIs, exports, or file-download routes. The default guard protects newly added non-public routes; future APIs must also use scoped services and return appropriate JSON errors. Static assets contain no account data.

## Data model and additive migration

Migration **14** is recorded in `schema_migrations`, and runs inside the existing initializer's `BEGIN IMMEDIATE` transaction. It is idempotent and does not rebuild, delete, or reset existing business tables.

- `users`: unique canonical email, Werkzeug scrypt hash, stable numeric ID.
- `stores`: stable ID, name, kind (`legacy`, `private`, `demo`).
- `store_memberships`: explicit owner relationship, foreign keys, composite primary key, one owner per store.
- `auth_sessions`: SHA-256 digest of a cryptographically random token, user/store membership foreign key, fixed expiry.
- `auth_limits`: persistent, atomic throttling buckets. Account/IP identifiers are HMAC digests, never raw email/IP strings.
- `products`, `transactions`, `transaction_items`: additive `store_id` foreign keys and indexes. Line-item triggers enforce matching transaction/product/store ownership; ownership cannot be reassigned through updates.

SQLite cannot add a non-null REFERENCES column with a non-null default to a populated table. The migration adds nullable FK columns, backfills store 1, rejects NULL on updates, and uses AFTER INSERT normalization for legacy inserts that omit ownership. Those inserts persist with store 1 within the same atomic statement. Web services always set ownership explicitly. Existing numeric guards remain in place. This preserves desktop fixtures and behavior while ensuring committed records have a store.

All legacy records stay in the unclaimed store **1**. Registration creates a new private empty store; it never grants legacy membership. An explicit local administrator command can assign the legacy store to an already registered account. It grants membership without moving any records, refuses reassignment, creates a backup, and revokes that user's sessions. The owner's original private store is retained; sign-in selects the lowest authorized store ID, so explicitly claiming legacy selects store 1. A multi-store switcher is not implemented.

## Authentication

- Email uses `email-validator` with delivery/DNS checks disabled, ASCII canonicalization and case folding. SQLite uniqueness arbitrates races.
- New passwords/passphrases accept **15–128 characters**, including spaces and Unicode, with no character-class rules. This exceeds the requested 12-character minimum and follows current OWASP advice for single-factor login.
- Werkzeug scrypt uses `scrypt:32768:8:3`; its implementation handles salts and hash encoding. Passwords and hashes never enter Flask cookies or templates.
- Successful login revokes the previous token and clears the entire cookie session before issuing a new token and CSRF state. Raw login tokens only exist in the signed HttpOnly cookie; their digests are stored server-side.
- Every request reloads identity through the session-to-membership join. Logout deletes the server-side session, so replaying an old signed cookie fails. Sessions have a fixed eight-hour lifetime and are invalid if membership is removed.
- Login checks a real or dummy hash, with generic invalid-credential errors. Registration validation and duplicate errors do not reveal hashes or internal details; duplicate registration may still be distinguishable from success.
- Only known local GET route destinations are accepted as `next`; external, protocol-relative, control-character, malformed, and auth-loop targets fall back to `/`.
- POST logout and demo entry both require CSRF. GETs do not change persistent authentication state.

## Throttling

SQLite `auth_limits` is persistent and shared across workers using the same disk. Requests reserve their attempt under `BEGIN IMMEDIATE` before password verification; a process restart or second worker does not reset limits. Login permits up to 10 attempts/account and 30/IP per 15 minutes. Registration permits 10/IP per 15 minutes. Successful login attempts also count. A 429 response asks the visitor to retry after the window; expired buckets are cleaned during subsequent attempts.

Local development uses exactly the same limiter on its local SQLite file: there is no in-memory fallback or production bypass. The production secret must remain stable so HMAC bucket keys remain stable. The limiter uses `REMOTE_ADDR`, never untrusted forwarding headers. Behind a proxy, all visitors may share its address; configure trusted proxy handling and infrastructure rate limits deliberately before scaling.

## Public demo

`seed-demo` still requires an empty business database. `add-public-demo` can add a separate fictional store to an existing migrated database without copying or changing private records. Both refuse to overwrite an existing demo store. The demo is identified by server-side `stores.kind='demo'`, never a client-supplied store ID.

Visitors enter it through a CSRF-protected POST button on login/registration. It revokes any prior account session and clears carts/receipts. Demo sessions can only read that dedicated store; all business mutations are rejected even when `TCCP_READ_ONLY=0`. Demo mode cannot grant account membership or access the account page. Login clears demo state.

`TCCP_READ_ONLY=1` additionally blocks business writes for account owners across the deployment. Authentication routes remain available. Set it to 0 to permit authenticated private-store writes while keeping the public demo read-only. Read-only forms remain visible so existing workflows are recognizable; attempted writes receive a clear 403.

## Deployment and remaining limitations

Production requires a private secret of at least 32 characters and an absolute persistent database path that already exists and has migration 14 applied. Startup validates the schema through a read-only connection and never creates or migrates a production database. Cookies are Secure/HttpOnly/SameSite=Lax; account/business responses are private/no-store. Security headers include frame denial, MIME protection, referrer policy, and HSTS in production. Debug is disabled by direct production startup. CSP allows existing inline styles and scripts to preserve current charts, confirmation handlers, and checkout behavior; removing `unsafe-inline` is future hardening.

Use HTTPS and a correctly configured reverse proxy, a stable secret, a private persistent local disk, OS file permissions and backups. SQLite coordinates workers on one host; distributed deployments require shared session/rate-limit storage and a database suitable for that topology. Filesystem administrators and the desktop application are trusted: their access to the database is not restricted by web login. Do not run the desktop on an untrusted public server or share database files with end users.

This milestone does not add email verification, password recovery/change, MFA, staff invitations, store switching, audit logging of account actions, or distributed rate limiting. Registration does not prove email ownership. Apply infrastructure abuse protections before opening unlimited registration to the public. A current dependency scan is useful evidence, not a guarantee of no vulnerabilities or a compliance certification.

Database backups now contain password hashes and session digests; protect them as sensitive data. Never log request bodies, cookies, passwords, or hash values. Database error logging records only the exception class. The existing tracked `.DS_Store` is repository metadata, not an application secret; databases, `.env`, and backups are ignored. No production data is migrated automatically during this implementation.

## References reviewed

- [Flask security guidance](https://flask.palletsprojects.com/en/stable/web-security/)
- [Werkzeug password hashing](https://werkzeug.palletsprojects.com/en/stable/utils/#werkzeug.security.generate_password_hash)
- [OWASP authentication guidance](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html)
- [OWASP password storage guidance](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)

## Verification performed (October 15, 2026)

- Complete pytest suite: **68 passed, 28 subtests passed**. All 47 existing Day 13 tests remain; private web tests now explicitly authenticate through real login with a deliberately assigned legacy test owner.
- Standard-library unittest run before the final production-schema test: **67 tests, OK**. The final additional production-schema test passed in the complete pytest run.
- Ruff checks: passed. `git diff --check`: passed. Gunicorn configuration check: passed. `pip check`: no broken requirements.
- `pip-audit` against installed dependencies: no known vulnerabilities reported at verification time.
- Disposable copy of the existing working database: migration ran twice, every preexisting business field matched, both SQLite integrity checks passed, and the source file's SHA-256 digest stayed unchanged.
- Reviewed all store-specific reads/writes, joins, receipt paths, and current route guards; tested another store's IDs in inventory, transactions, carts and receipts, plus reports/metrics and concurrent private-store checkout.
- Tracked-file checks found no databases, `.env` secret file, backups, key files, or private-key blocks. Existing tracked metadata was retained.
- No browser/app surface was available for visual testing. Mobile layout, keyboard interaction, and print preview still need manual review; no visual verification is claimed.
- No commit, push, deployment, working-database migration, or legacy ownership assignment was performed.
