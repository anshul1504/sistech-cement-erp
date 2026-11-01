# Sistech Cement ERP

A runnable first implementation of the supplied Cement Manufacturing ERP SRS using **HTML, CSS, JavaScript and Bootstrap 5** with a **Python Django** backend. Bootstrap assets are included locally, so the interface works without a CDN connection.

## Run locally on Windows

Python 3.12 or newer is recommended. Open PowerShell in this folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py setup_roles
.\.venv\Scripts\python.exe manage.py seed_demo
.\.venv\Scripts\python.exe manage.py runserver
```

Open **http://127.0.0.1:8000/**. The seed command prints a generated password for `admin`, `operator` and `approver`. It will refuse to overwrite existing demo users. For a clean installation without sample data, replace `seed_demo` with `createsuperuser`.

Use `admin` for master data and the **Access control** workspace at `/accounts/access/` to create team members, edit role matrices and assign sites. Use `operator` to create and submit an order, then sign in as `approver` to approve it. The creator cannot approve their own order, including when the creator is a superuser. Each non-superuser needs both role permissions and a SiteAccess assignment. Super administrator accounts are managed separately at `/admin/`.

## Authentication and access control

- Poppins fonts are hosted locally with their SIL Open Font License. Authentication pages share a responsive interface, keyboard focus states, field errors and password visibility toggles.
- Sign in using a case-insensitive email address or username. Ambiguous legacy emails and disabled users are rejected. The team editor enforces case-insensitive email/username uniqueness for new accounts; existing duplicates must be corrected by an administrator.
- Sessions expire when the browser closes by default, with an optional 8-hour persistent session. Browser session restore can retain session cookies. Password change keeps the current session and invalidates other sessions. Password reset links expire after 30 minutes and cannot be reused.
- Login (including `/admin/login/`) has persistent hashed identifier/IP throttle windows: five identity attempts and thirty IP attempts in fifteen minutes. Successful login clears its identity counter. Password recovery uses equivalent windows and generic confirmation for unknown/disabled addresses. Current-password checks are also limited. Run `python manage.py purge_auth_limits` daily to clear expired counters. Production reverse-proxy rate limiting and concurrent PostgreSQL testing remain necessary.
- Forgot password, reset link sent, new password, invalid/expired link, reset complete, change password, signed out, access help and My account pages are implemented. There is no public registration; administrators create company accounts.
- Recovery emails default to the **console backend**. Development reset links appear only in the local server terminal. No real email is delivered until SMTP environment variables are configured. Never expose the development console or reset links publicly.
- **Email OTP remains inactive and commented out** in `accounts/otp.py` and settings. The scaffold describes hashed codes, expiry, attempt limits, session binding, resend cooldown and single use. Challenge persistence, delivery, routes, verification UI and two-step authentication tests must be implemented before enabling it. Password login does not send an OTP or claim an email is verified.
- Role permissions are separate for Sales and Purchase: View, Create, Submit, Approve, Post and Cancel. Stock export is separately permissioned. Navigation, dashboard records, action buttons, views and posting services follow these permissions. Legacy generic order grants no longer grant Sales/Purchase access.
- Default roles: Operations maker, Operations approver, Sales executive, Purchase officer, Warehouse operator, Production operator, Quality manager and Internal auditor. `setup_roles` creates missing presets without replacing edits; `setup_roles --reset` explicitly restores all named presets. On upgrade from v0.1, review and migrate legacy groups with this command or the role matrix. New schema migration alone does not rewrite existing roles.
- Only a super administrator can manage roles, users and site assignments, including in Django admin. Ordinary team accounts cannot grant staff/superuser flags. Multiple roles and direct grants combine; separately managed permissions are disclosed and preserved in the role editor. Site assignments constrain operational documents, while products/partners are shared internal masters.
- Sign-in/out, password changes/resets, role edits and team access changes are audited. Security events without a site are visible to super administrators; ordinary audit views remain scoped to assigned sites. Disabling a user and changing their roles/sites takes effect on their next request.

## Implemented workflows

- Login, CSRF-protected forms, logout and Django group permissions.
- Site-scoped orders, production, inventory, tests, reports and audit views. Products and partners are shared internal masters; this is a single-company internal deployment, not a dealer portal.
- Plants/depots, products/materials and dealer/supplier/transporter master creation and editing.
- Multi-line sales/purchase orders with decimal quantities and INR rates. Submit → independent approval → full dispatch/receipt → completed. Draft/submitted/approved orders may be cancelled. Orders are immutable after creation in this first version; cancel and recreate an unposted order to correct it.
- Dealer credit blocking at approval. The provisional exposure calculation sums approved/completed sales orders; it does not subtract collections because AR/payment accounting is not implemented.
- Atomic stock posting with a source document per movement, duplicate-source prevention, conditional balance updates and nonnegative stock enforcement. Insufficient stock rolls the entire posting back.
- Production output records → quality hold → passing test evidence → independent release to stock. Held output is excluded from available stock. Any failing test blocks release; retesting/disposition approval is a future workflow.
- Immutable workflow/stock/test history through the application and read-only admin; audit events for creates, transitions, opening stock and CSV exports. UOM and pack weight lock once used.
- Dashboard, searchable lists, pagination, stock ledger and CSV download with site-scoped data and formula injection protection.

All quantities use each SKU's explicit base unit; mixed units are never summed on the dashboard. Bag weight is captured per SKU. Automatic bag/MT conversion and versioned alternate UOM transactions are not yet implemented. All order values exclude GST, freight and discounts and are **not tax invoices**.

## SRS implementation boundaries

This is a working foundation, not completion of the full multi-phase SRS. The following are still required before an operational ERP rollout:

| SRS area | Current status / remaining work |
| --- | --- |
| Organisation and access | Single company; editable role matrices, user/site assignments and authentication workflows implemented. Legal entities, GST registrations, financial periods, territory/dealer isolation and MFA remain. |
| CRM and dealer network | Partner master only. Leads, KYC attachments/approvals, territory hierarchy, visits and project pipeline remain. |
| Sales | Basic net-value orders. Versioned price lists, quotations, credit ageing, stock reservations/ATP, partial delivery and revision workflow remain. Approval does not reserve stock; availability is enforced at posting. |
| Procurement | Basic order and full receipt. PR/RFQ, GRN inspection/holds and three-way matching remain. Purchase receipts become available immediately. |
| Inventory | Quantity ledger and opening stock. Lots, reservations, locations, transfers, returns, valuation and reversals remain. |
| Production and quality | Output tracking and quality release only. BOM versions, material consumption, test specifications, COA, energy and actual costing remain. |
| Logistics | Stock dispatch only. Gate tokens, immutable weighments, tare/gross tolerance, loading, trip, freight and POD remain. |
| Finance and GST | Not implemented. GL, AP/AR, journals, tax invoices, GST configuration/connectors, banking, assets, financial statements and period close remain. |
| Schemes and claims | Not implemented. Versioned incentive rules, commissions, returns, complaints and settlements remain. |
| Android and integrations | Not implemented. Scoped APIs, employee/dealer/driver apps, offline drafts, notifications, GST/bank/GPS/hardware integrations remain. |
| Production operations | PostgreSQL migration, concurrent workload testing, audit retention hardening, monitoring, backups/restore, secure hosting and UAT remain. |

No external banking, GST, weighbridge or payment calls occur in this implementation. Hardware and tax rules require the actual business configuration. A failed quality test remains recorded; no application action silently deletes it. Direct database operators remain outside the application audit boundary.

## Verify

```powershell
python manage.py check
python manage.py test
```

For repeatable desktop/mobile visual checks, install the optional Playwright package and Chromium, start the local server, set `ERP_DEMO_PASSWORD` to the local demo password, then run `python scripts/browser_smoke.py`. The script captures 14 authentication/access pages at both sizes into ignored `test-results/` and checks Poppins, page overflow, password visibility and logout. Do not use production credentials or run it against a production server; it generates a local reset token for the demo operator without submitting a new password.

Tests exercise authentication, throttling, password recovery and token expiry/single-use, session invalidation, role/permission injection, Sales/Purchase isolation, disabled users, site isolation, maker-checker, stock rollback, credit limits, quality release and HTTP pages.

## Project layout

`config/` contains Django settings and routes. `erp/models.py` defines persisted data, `erp/services.py` contains transactional workflow rules, `erp/views.py` enforces permissions and site scope, `erp/forms.py` validates input, `templates/` holds HTML, and `static/` holds Bootstrap, custom CSS and JavaScript. `erp/migrations/` contains the database schema history.

SQLite is for local development and evaluation. Choose PostgreSQL and test concurrent stock/credit workflows before multi-user production use. Production requires `DJANGO_DEBUG=0`, an environment-provided `DJANGO_SECRET_KEY`, allowed hosts, HTTPS, a production WSGI/ASGI server and static file serving. `.env.example` documents variables but is not auto-loaded.

Reference documentation used for the implementation: [Django transactions](https://docs.djangoproject.com/en/5.2/topics/db/transactions/) and [Django authentication](https://docs.djangoproject.com/en/5.2/topics/auth/default/).
