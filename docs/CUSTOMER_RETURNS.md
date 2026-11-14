# Customer returns / RMA

The unfinished customer-return work from the handoff is completed as a site-scoped, tested workflow. RMA posting uses the existing credit-note, settlement, exact inventory value and GL engines. It does not create another stock ledger.

## Workflow

1. **Sales → Customer returns** (`/returns/`), or Create → Customer return. Find an invoice from an assigned site and enter the requested quantities, reason and description. Optionally link a complaint that is already linked to the same invoice/customer; complaint view permission is required. Invalid IDs/quantities return visible errors.
2. A different authorized user approves or rejects with a finding. Pending returns reserve their quantities against other RMAs and manual return credit notes. Invoice quantities already credited cannot be requested again. UUID submission keys make a repeated browser request return the original RMA; changed details cannot reuse the key.
3. Warehouse/quality records pickup date, registration and LR/challan, or inspects a direct arrival after approval. Pickup cannot be future-dated or precede the request.
4. Record every line's received, good, damaged and unaccepted quantities. No more than the approved quantity may be received; good plus damaged cannot exceed received. Unaccepted goods require a note. Inspection is recorded once. An all-unaccepted receipt closes without credit or owned stock. Accepted quantities stay in the derived quarantine/hold position until finance posts credit; unreceived/unaccepted quantities are released from the commercial reservation.
5. A finance user with credit-note and company-finance permissions, assigned to the return's site, issues credit. This user must differ from both requester and inspector; the return approver can be the finance issuer. Good stock returns to available; damaged stock returns to a separate damaged balance. Credit first settles its own invoice, then uses the normal customer allocation/advance rules. A repeated credit action returns the original linked note and cannot add stock or post again.

Credit taxable value is accepted quantity × the original invoice rate, **capped at the remaining taxable credit room** where earlier discounts/corrections exist. It uses invoice GST rates and current open-period posting. A prior full financial discount may leave no refund while physical return quantity remains; no second refund is invented. Returned inventory uses the original dispatch carrying-value snapshot. Good/damaged fragments and later partial returns share the same remaining value budget, including the final paisa. Legacy invoices without a dispatch-cost snapshot retain the existing valuation fallback and require accountant review.

The existing manual finance credit-note workflow remains available to explicitly authorized finance users; it cannot consume quantities reserved by an open RMA. All shared credit-note calls now validate module/company/site access, line ownership, finite/precision-limited values, remaining credit room, duplicate lines and damaged-inspection evidence. No posted invoice or prior credit note is rewritten.

## Custody and access

Cancellation releases a request only before pickup/arrival (requested or approved), with a reason. Goods already picked up or inspected cannot disappear through cancellation; finish the inspection/credit flow. An all-unaccepted receipt requires the plant to retain the rejection/disposition evidence; rejected goods are not booked as company-owned inventory. This stage does not automate return-to-customer transport, goods lost during pickup, lab retesting, quarantine-lot allocation, write-off of damaged stock or CAPA closure. Use the existing approved stock/complaint workflows for subsequent decisions.

Non-superusers require site assignments for all reads and service writes, including cancel and finance credit. Separate permissions cover view, request, approve/reject and inspect. Finance additionally needs credit-note creation and explicit company finance access. Sales executives can request; sales managers can independently approve; warehouse/quality users inspect; finance issues credit. Existing customized role groups are preserved: explicitly grant the new Customer returns permissions in the role matrix. `setup_roles` creates missing presets without resetting existing ones.

My work queues match the approval/arrival/credit filters and exclude users who cannot independently issue the credit. The return list is paginated; forms stack into labelled cards on phones, including inspection notes. Evidence and action links follow the corresponding module permissions. All supported transitions retain audit events.

## Migration verification

On 14 November 2026 the active local database had migrations `0036`–`0040` pending. The handoff supplied uncommitted `0040_customer_returns`; the completion adds `0041` for request replay metadata, no-acceptance closure and one-line-per-invoice-line uniqueness. The existing `0040` history was preserved.

Before applying `0036`–`0041`, a SQLite backup was saved outside the repository. The migration plan was reviewed, all six applied successfully, and user/partner/order/invoice/stock-movement/journal-entry counts matched before and after. `migrate --check` reports no pending operations; `makemigrations --check --dry-run` reports no model drift. Existing customized roles and OTP defaults were preserved; no demo or real customer records were inserted. Database/backups, local environment secrets and generated screenshots are not part of the commit.

## Verification

`erp.test_returns` covers exact good/damaged valuation, reconciliation, partial receipts, reservations against manual credits, non-finite/overprecision values, duplicate requests/credits, independent finance, cross-site and missing permissions, complaint matching, cancellation/custody rules, pickup dates, all-unaccepted closure, prior discounts, rollback in a closed financial period, queues and actual HTTP forms.

`erp.test_returns_browser` checks five list/form/state screens at 320, 390, 768 and 1440px plus a collapsed desktop sidebar, local Poppins, horizontal overflow and JavaScript errors. It completes independent approval → pickup → inspection → finance credit and retries the credit POST using fictional records in an isolated database. Existing authentication, CRM, schemes, core-business and finance browser checks remain part of verification.

```sh
python manage.py check
python manage.py migrate --check
python manage.py makemigrations --check --dry-run
python manage.py test
RUN_BROWSER_TESTS=1 python manage.py test erp.test_returns_browser erp.test_schemes_browser erp.test_crm_browser erp.test_business_browser erp.test_finance_browser accounts.test_browser
```

SQLite tests verify supported sequential operations and atomic rollback. PostgreSQL concurrency/load testing and actual business/finance UAT remain rollout gates. This milestone completes the pending RMA module, not the entire SRS.

Final verification: 350 tests discovered, 344 passed and six opt-in browser tests skipped in the default run. All six browser tests were run explicitly and passed. Local migration and schema checks passed; the migrated database retains the recorded pre-upgrade business row counts. No test data, real mail or external financial call was used in the business database.
