# Dealer CRM and onboarding

Developer milestone: 14 November 2026. This implements the dealer lead/onboarding portion of Step 8, with manual evidence review. It does not complete the full SRS or certify a customer's statutory registrations.

## Daily workflow

1. Open **Sales → Dealer CRM** (`/crm/`). Create a prospect with its plant/depot, owner, customer type, contact, district, territory, opportunity amount and next follow-up. Indian mobile formats normalize to ten digits; a mobile cannot have duplicate leads at the same site. The same prospect can have a lead at another site.
2. Record calls, visits, meetings or notes with the outcome and next follow-up. Future activity dates and past new follow-up dates are refused. Stages are New, Contacted, Qualified, Proposal/review, Converted and Lost. Losing or reopening a lead requires a reason. Conversion is possible only through approved onboarding or an evidenced link to an existing active customer.
3. Prepare an application. Complete the customer profile and proposed credit/payment terms, then upload evidence. Files must match their PDF/PNG/JPG/WebP content and be at most 5 MB. Replacement creates a numbered version; withdrawal retains the original. A lead has at most one open application.
4. Submit with a note. The profile, owner, current document versions/content hashes and company policy revision are frozen. Default required evidence is PAN and a dealership agreement; administrators can change it at `/crm/policy/`. A supplied GSTIN additionally requires its certificate; a supplied bank account requires a cancelled cheque. Current expired evidence blocks submission/review. Required profile fields and GSTIN/PAN/state/bank formats are checked.
5. An independent KYC reviewer records findings and approves or returns/rejects the application. A different finance reviewer approves credit and days, capped by the request. A final approver applies the approved terms and creates the active customer master. The final approver may also have performed KYC, but cannot be the finance reviewer. Application contributors, including earlier editors, submitters and document uploaders, cannot approve any lane, even as superusers.
6. Changes requested unlock editing. Resubmission creates a new revision using the then-current policy; earlier review profiles, notes, evidence versions and decisions remain accessible. Rejected/withdrawn applications remain retained; a new application can be prepared. Repeating an approval cannot create another customer.

Nothing in an unapproved application is usable as a new sales customer. A declared security deposit is master information; it does not create a receipt or accounting entry. A separately authorized receipt must record actual money received.

## Existing customers and amendments

Existing partners are grandfathered: migration does not change active status, terms, invoices or historical stock/accounting. Link an existing active customer to a lead with a matching reason, then use **Prepare amendment**. Its current profile and KYC evidence are copied into a new review. Approval updates the same partner; existing invoice snapshots remain unchanged. Changed protected customer fields, active status or current master KYC evidence invalidate a stale amendment so it cannot overwrite a newer decision.

The ordinary master form creates suppliers/transporters. For customers, identity/address, bank and commercial fields require a CRM amendment; contact, territory, internal code and notes remain administrative edits. The audited status action can deactivate/reactivate an already established customer without changing approved terms. An amendment preserves the existing active/inactive status.

For a CRM-managed customer, the master KYC list is the current approved projection. Direct upload/delete is blocked. A new approval replaces that projection, while every original file remains in the CRM application history. Access to its projected files requires both KYC view permission and access to the approved application's lead; shared partner visibility alone does not grant evidence access. Legacy vendor/customer document behavior remains available until CRM onboarding is adopted.

## Access and upgrade

Non-superusers need site assignments. Salespeople see their own leads/applications; **CRM team & ownership → View** permits the team's records only within assigned sites. **Edit** additionally permits assigning owners; the owner must be active, have CRM access and be assigned to the selected site. Evidence downloads and CSV exports are separately permissioned and audited. CSV data is scoped/filtered and formula-like text is escaped.

The role matrix exposes Dealer CRM, CRM team & ownership, CRM follow-ups, Dealer onboarding, Dealer KYC evidence, Dealer credit review, Sales targets & performance, and onboarding policy. Fresh preset roles support:

| Role | CRM responsibility |
| --- | --- |
| Sales executive | Own pipeline, follow-ups, prepare/edit/submit applications, view own evidence and performance |
| Sales manager | Assigned-site team, owner assignment, KYC review, final activation, targets, exports |
| Finance controller | Assigned-site team review, KYC evidence, independent credit/terms review, performance |
| Internal auditor | Assigned-site team and evidence read/export; no decisions |

Use at least three people for a complete application: contributor, KYC/final reviewer, finance reviewer. A sales manager who creates or contributes to an application needs another independent manager to review/activate it.

After applying migration `0029`, run `python manage.py setup_roles`. It creates missing groups without replacing customized groups. **Existing Sales executive and Finance controller groups do not silently acquire new permissions.** A super administrator must explicitly grant the CRM rows in Roles & permissions and confirm site assignments. `setup_roles --reset` is only for a deliberate reset of all named presets; do not use it to preserve custom grants.

## Targets and reporting

Targets are monthly per salesperson/site, with a net taxable sales amount and new-dealer count. Re-entering the same month/site/person revises the existing target; audit retains before/after values. Ordinary salespeople see only their own performance. Target management additionally requires the target permission and owner/site authority.

Sales attribution uses the **sales order creator** of each invoice, with the invoice's site. It does not infer a salesperson from the current lead owner. Net taxable sales are invoices dated within the selected month less credit notes dated within that month, including notes against older invoices. Collections show **current invoice allocations** from posted receipts dated in that month, exclude unallocated advances and credit-note settlements, and can change after bounce/returns/reallocation. Overdue is the current outstanding amount on all overdue attributed invoices, not a historical month-end balance. New dealers count first-time approved applications by frozen submission owner, site and activation month; links to existing customers and amendments do not count.

Rows drill down to invoices, credit notes, allocations and activations, up to 100 source records per category; summary totals include all matching records. CSV export covers the scoped summary. This is an operational performance report, not a financial-close snapshot or a commission calculation.

## Verification and boundaries

`erp.test_crm` covers normalized duplicates, own/team/site access, unauthorized mutations/exports, follow-ups, maker-checker across all lanes, caps, evidence versioning/content/expiry/withdrawal, policy resubmission, retained snapshots, duplicate GSTINs, stale amendments, master bypasses, target revision, sales/credit/receipt attribution and HTML routes. `erp.test_crm_browser` checks 14 screens at 320/390/768/1440px and a collapsed desktop sidebar, local Poppins, overflow, JavaScript errors, real lead form submission and approval through three independent browser sessions. All fixtures and documents are fictional; screenshots stay ignored.

SQLite regressions verify sequential state changes and transaction rollback. PostgreSQL concurrency/load validation, business policy acceptance and real-data UAT remain rollout gates. Policy/application serialization uses a company policy row lock on databases supporting it. There are no external GST/PAN/bank checks, automatic email/WhatsApp reminders or real notifications. Territory is currently a text attribute, not a hierarchy/access boundary. Project/site-referral records remain in the influencer module; a unified project pipeline, duplicate-contact merging, multi-owner handover, dealer portals, schemes/commissions/payouts and complaints/CAPA are future work. No approved/onboarding evidence is publicly served.

### Verified commands

On 14 November 2026, `manage.py check` passed, `makemigrations --check --dry-run` reported no drift, and the full suite ran 276 tests successfully (272 passed; four opt-in browser tests skipped). The four browser tests were then run explicitly and all passed, including the three-role CRM activation. A SQLite backup outside the repository preceded local migration `0029`; `setup_roles` preserved existing customized groups. No real customer/test fixture data was added to the local business database.

```sh
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
RUN_BROWSER_TESTS=1 python manage.py test erp.test_crm_browser erp.test_business_browser erp.test_finance_browser accounts.test_browser
```
