# Sistech Cement ERP

A runnable first implementation of the supplied Cement Manufacturing ERP SRS using **HTML, CSS, JavaScript and Bootstrap 5** with a **Python Django** backend. Bootstrap assets are included locally, so the interface works without a CDN connection.

## Run locally on Windows

Python 3.12 or newer is recommended. Open PowerShell in this folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py seed_demo
.\.venv\Scripts\python.exe manage.py runserver
```

Open **http://127.0.0.1:8000/**. The seed command prints a generated password for `admin`, `operator` and `approver`. It will refuse to overwrite existing demo users. For a clean installation without sample data, replace `seed_demo` with `createsuperuser`.

Use `admin` for master data, users, groups and site assignments. Use `operator` to create and submit an order, then sign in as `approver` to approve it. The creator cannot approve their own order, including when the creator is a superuser. Each non-superuser needs both Django permissions and a SiteAccess assignment. User management is at `/admin/`.

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
| Organisation and access | Single company; basic sites and roles. Legal entities, GST registrations, financial periods, territory/record restrictions and MFA remain. |
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

Tests exercise permission and site isolation, maker-checker, insufficient stock rollback, duplicate posting, credit limits, quality release and HTTP pages.

## Project layout

`config/` contains Django settings and routes. `erp/models.py` defines persisted data, `erp/services.py` contains transactional workflow rules, `erp/views.py` enforces permissions and site scope, `erp/forms.py` validates input, `templates/` holds HTML, and `static/` holds Bootstrap, custom CSS and JavaScript. `erp/migrations/` contains the database schema history.

SQLite is for local development and evaluation. Choose PostgreSQL and test concurrent stock/credit workflows before multi-user production use. Production requires `DJANGO_DEBUG=0`, an environment-provided `DJANGO_SECRET_KEY`, allowed hosts, HTTPS, a production WSGI/ASGI server and static file serving. `.env.example` documents variables but is not auto-loaded.

Reference documentation used for the implementation: [Django transactions](https://docs.djangoproject.com/en/5.2/topics/db/transactions/) and [Django authentication](https://docs.djangoproject.com/en/5.2/topics/auth/default/).
