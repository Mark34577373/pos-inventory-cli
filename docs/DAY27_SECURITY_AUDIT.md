# Day 27 security audit

Scope: release candidate on `feature/commercial-ui`, starting commit
`fe77cd091e48bc56972f8cd205e68a015ee43a08`. Git was clean before this work.
This is a source review and regression assessment, not an independent penetration
test or a guarantee that no vulnerabilities exist.

## Actual architecture and boundaries

`web_app.py` owns the global Flask app, default authentication/CSRF guards, security
headers, generic errors and core routes. Ten blueprints cover identity/store selection,
inventory tools, analytics, refunds, settings, alerts, customers, purchasing, expenses/
financials and POS layouts/held sales. There are 78 non-static URL rules, 46 allowing
mutations. State-changing requests require the shared guard; endpoint-specific handlers
validate the supported fields and resolve owned IDs through services.

SQLite access uses `database/database.py`, with foreign keys, a 5s busy timeout,
private newly created files and transaction audit context. `database/tenancy.py`
resolves HTTP ownership from verified `g.store`; services use parameterized
`authorized_store_id()` filters and store-qualified joins. Outside HTTP, the desktop
and CLI deliberately use legacy store 1 as trusted local clients. Filesystem access
is therefore a privileged boundary; the desktop is not an internet authentication API.

`services/transaction_service.py` is the only sale writer. Refunds, receiving and
expenses have separate atomic ledgers; original price/discount/tax/cost/customer/
receipt snapshots remain authoritative. `database/*_migration.py`, `migrations.py`
and `validation.py` implement ordered transactional schema changes/guard checks.
`manage.py` handles explicit backup, migration, integrity and restoration.
Jinja templates and native JavaScript share UI components without a second money
engine. Tkinter remains in `main.py`, `ui/` and the existing services.

## Prioritized findings

| ID | Severity | Demonstrated issue | Resolution |
| --- | --- | --- | --- |
| S27-1 | Medium | Unknown `TCCP_READ_ONLY` values were interpreted as writable; a misspelled production environment selected development behavior. | Strict 0/1 security flags and explicit supported environment names fail at startup. Subprocess tests verify refusal before opening a DB. |
| S27-2 | Medium | Registration remained publicly available in production, creating durable accounts/stores despite a restricted demo's intended operating policy. Existing throttles bound individual windows, not lifetime/public resource allocation. | `TCCP_ALLOW_REGISTRATION` defaults off in production, on in development/testing. Closed GET/POST registration returns 403 before hashing, rate bucket or identity writes; UI links are hidden. Existing login/demo remain available. |
| S27-3 | Medium | Production accepted a DB path under Flask's unauthenticated static root. An operator placing a readable DB there could expose all stores. | Startup rejects the resolved path under the resolved static directory. No actual database was exposed or moved during verification. |
| R27-1 | Medium | Restore checked SQLite structure/FKs but not the application's schema guards; interruption could leave a newly created incomplete target. | Restore validates TCCP core tables, migration guards and integrity before/after SQLite backup. Failed output is removed only when its inode matches this operation's newly created file. Existing targets/source remain untouched. |
| O27-1 | Medium, deployment condition | TLS termination, public domain, persistent disk ownership, backups and proxy limits cannot be proven without a selected deployment. | Required Day 28 verification; not represented as configured by source-code cookie flags. |
| O27-2 | Low/operational | Without trusted-proxy configuration, IP throttles see the proxy address. Broadly trusting forwarded headers would allow spoofing. | Keep current untrusted-header behavior; configure edge limits and assess exact proxy chain before enabling any ProxyFix middleware. |
| A27-1 | Low, verification limit | Screen-reader, physical touch, Safari/Firefox and deployed HTTPS behavior have not been fully tested. | Manual acceptance remains conditional. Chrome checks and semantic/contrast regressions are narrower evidence. |

No confirmed critical/high defect remains in the reviewed paths. No authentication
bypass, cross-store data access or financial corruption was found by the executed
gates. These statements describe the evidence, not absence of all possible defects.

## Verified protections

- Werkzeug scrypt hashes, normalized unique email, atomic user/store creation,
  generic login failure and persistent SQLite/HMAC rate buckets. No password reset
  flow exists; no email delivery or recovery feature is implied.
- Login clears prior cookie state, revokes the previous opaque server session,
  checks current membership, and expires after eight hours. Logout is POST/CSRF and
  revokes replay. Production uses Secure, HttpOnly and SameSite=Lax cookies.
- Anonymous private browser routes redirect; private APIs return 401. Demo stores
  are loaded separately from private stores and all business writes are rejected.
  Service guards, owned-ID lookups, immutable ownership and composite database guards
  supplement route checks. Store IDs from forms never become authorization.
- Every unsafe method traverses CSRF validation, duplicate-field/file/content-type
  checks and request limits. No permissive CORS or forwarded-header trust was added.
- Reviewed SQL interpolation uses fixed table/sort/placeholder fragments, not raw
  user identifiers. Search values remain parameters. Jinja autoescaping, quoted
  attributes, textContent/DOM creation and a script CSP remain in place. Downloads
  neutralize user-text spreadsheet formulas, including whitespace/BOM prefixes.
- Application errors log exception classes, not credentials or exception payloads.
  No-store responses, frame denial, nosniff, same-origin referrers, resource limits,
  host validation and production HSTS remain. Strong stable production secrets,
  absolute existing DB path, schema guards and private DB permissions fail closed.
- Sale tokens and held claims are checked inside `BEGIN IMMEDIATE`; stock deductions,
  sale lines and held completion commit together. Receiving/refund/expense retries
  have durable keys and payload checks. Existing race/rollback tests are retained.

## Dependencies, secrets and data handling

Installed-environment `pip-audit` queried public advisories successfully for **43
packages**, with **zero reported advisories**. This does not guarantee future or
unreported vulnerabilities. The initial sandbox DNS failure was retried with network
approval. No dependency version or runtime dependency was changed.
No tracked database, `.env` or private-key file was found. Changed-source credential
patterns are checked separately; tests use explicitly fictional credentials. The
working DB was opened read-only for integrity and copied to a private temporary
location for repeat-initialization checks. Source bytes remained unchanged.

References for deployment review: [Flask security guidance](https://flask.palletsprojects.com/en/stable/web-security/)
and [Flask trusted-proxy guidance](https://flask.palletsprojects.com/en/stable/deploying/proxy_fix/).
These support the cookie/header/resource/host checklist and the requirement to trust
only a known proxy chain. They do not certify this application or hosting setup.
