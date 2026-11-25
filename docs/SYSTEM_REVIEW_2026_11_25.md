# ERP system review — 25 November 2026

This review covers the repository's module implementation, access paths, source/workflow documentation,
Django checks, regression suite and selected real-browser workflows. It is developer verification with
fictional test data, not business acceptance, a full penetration test or production concurrency proof.
The original SRS is represented here by the existing `SRS_COMPLETION.md` register and module documents;
this review does not claim an independent line-by-line comparison with a newly supplied SRS file.

## Delivered in this milestone

- The five People groups remain in the requested order: **C&F**; **Sales Promoter / Handling Agent /
  Transporter**; **Dealer**; **Retailer**; **Mason**. Group pages show their own heading and creation path.
  New dealers/retailers use reviewed CRM onboarding; agents/C&F use partner masters; masons reuse the
  influencer register. Existing suppliers/distributors/institutional buyers are retained.
- Dealer self-service now has a linked-account dashboard, confirmed orders, open invoices, recent receipts,
  paginated invoice register and date-filtered CSV **and branded PDF** statements. Only the linked active
  Dealer is queried; submitted partner IDs do not choose the account. Existing internal ERP roles remain
  separately administered: a dealer link adds self-service access, not internal module permissions.
- Super administrators can manage the link from **Users → User details → Manage dealer access**. An
  activation needs an active non-staff login with an email and an active Dealer. A reason and recent
  identity confirmation are required; reassignment/disable signs out old sessions and records an audit event.
  This action sends no invitation/email and does not create passwords or silently assign ERP roles.
- Invalid/reversed ledger date ranges now return a validation response before any statement is generated.
  CSV text is formula-escaped, filenames are sanitized, exports are audited and statements are not cached.
- Dealer CRM and construction-project pipelines have stage boards and filtered list views. Board cards
  show the next follow-up/review step; the project view adds owner/site/expected-close filters, overdue
  and inactive-work filters, missed-close warnings, elapsed stage-review days and a monthly forecast.
  Stage links preserve filters. Board cards are capped at 12 per stage and link to the full matching list.
- Project detail shows a sample → current offer → independently approved order readiness checklist.
  Board navigation does not change stages. All existing evidence, maker-checker and source-history rules
  continue to apply when a user records a real stage transition.
- CRM → **Create project opportunity** pre-fills only a visible non-lost/non-merged lead; buyer/site/contact
  identity is preserved and the project must still be explicitly completed and saved.

## Required end-to-end sales flow

| Step | Current implementation | Advanced work still required |
| --- | --- | --- |
| 1. Capture and assign | Scoped CRM leads, duplicate mobile checks, activities, owner/site, due follow-ups, customer-type filter, board | Shared contacts, fuller identity matching, source integrations and deduplicated customer masters |
| 2. Qualify and onboard | Retained KYC, independent KYC/credit/activation lanes, reviewed amendments, linked existing customers | Company-approved policy acceptance; C&F/agent onboarding is currently a master process, not a dedicated commission contract workflow |
| 3. Project and quotation | CRM handoff, project/source attribution, sample evidence, versions, quote approval/send/acceptance | Product/phase delivery schedules, configurable calibrated forecasts, collaborative selling |
| 4. Confirm the order | Customer/credit/overdue checks, price exceptions, independent approval, firm order-line stock reservations | Lot/bin ATP, price/margin floors, PostgreSQL race proof |
| 5. Dispatch and deliver | Gate/weighments, review, invoice, partial dispatch, POD and reviewed corrections/recoveries | Actual weighbridge/GPS/mobile proof, refused-load redelivery to another buyer |
| 6. Collect and reconcile | Receipts, allocations, bounces, credit notes, ageing and linked dealer statements | Customer advance refund lifecycle, real banking feeds and combined/split bank matching |
| 7. Retain and grow | Territory/beat/project attribution, targets, schemes, recognition and employee commission | Dealer/retailer secondary sales, channel hierarchy and agent/distributor commission beneficiaries |

A won project requires independent approved-order evidence. CRM conversion means an onboarded/linked
customer, not collected revenue. Forecast values are taxable estimates with existing fixed stage weights;
held projects have zero weight, closed projects are excluded, and no quote/order totals are added twice.
The 14-day inactivity flag is a visible operational default based on recorded contact activity, not a
company-approved SLA. Monthly forecasts include explicitly shown unscheduled expected-close records.

## Whole-system pending register

Priority P0 = production/access acceptance; P1 = next operational milestone; P2 = later advanced work.

| Area | Implemented baseline reviewed | Pending / acceptance needed | Priority |
| --- | --- | --- | --- |
| Identity and access | Password + email OTP (including admin), roles/sites, expiry, reauthentication, delegated/routed approvals, work handoff, dealer link controls | Cross-module permission inventory, explicit external-portal role policy, production SMTP delivery/expiry/security acceptance, deployed cleanup scheduling | P0 |
| Production deployment | Local Django/SQLite server; production-oriented HTTPS settings exist | PostgreSQL configuration/migration and race/load tests; real host/TLS/static serving; monitoring; tested backup/restore and retention; deployment automation | P0 |
| Business readiness | Audited workflows and fictional-data regressions | Real opening-data mapping/import, reconciliation, role-policy sign-off, finance/plant UAT, training and cutover acceptance | P0 |
| Company / masters | Company identity, GST fields, sites, product grades, local assets/fonts, People groups, audited C&F → agent → dealer → retailer → mason upline network with tree and network sales | Multiple legal entities/GST hierarchy, versioned alternate units and deeper dimensions | P1 |
| CRM / sales pipeline | Leads/onboarding, territories, beats, projects, stage evidence, read-only boards, follow-up health and forecast | Master/contact deduplication, recurring/optimized visits, product/phase schedules, configurable forecasts and fuller collaboration | P1 |
| Dealer / retailer portal | Linked dealer dashboard/invoice register/ledger PDF+CSV | Dealer-originated order requests with price/credit checks, own document downloads, disputes and acknowledgements; retailer-specific account policy and secondary sales | P1 |
| C&F and agents | Distinct master classifications, contact/tax/bank/KYC fields | Company-approved contracts, handling/commission basis, beneficiaries and independent accrual/payout/reversal workflow; classification alone is not an implemented agent accounting engine | P1 |
| Inventory | One stock ledger, WAC, transfers/counts/damage, order-line reservations, bins/put-away/bin moves | Bin-directed picking, lot balances per bin, lot-level reservation/ATP, conversion versions and recall coverage | P1 |
| Procurement | PR/RFQ/PO, gate/GRN/QC, three-way match, AP, returns/payments/refunds/offsets | Demand/budget enforcement, deeper supplier verification, purchasing acceptance with actual evidence | P1 |
| Production / QC | Recipes/effectivity, issues, shifts/packing, cost variance, immutable QC/release, COA, calibration, CAPA, sampling and maintenance | Own-plant capacity/MRP, complete cost allocation, rework/write-off and automated recall; spares against maintenance jobs | P1 |
| Dispatch / freight | Gate/weights/loading, review, POD/correction, freight-bill review, recovery/vendor credits, return-to-origin | Hardware, GPS/ETA, driver mobile proof, partial redelivery and advanced freight | P1/P2 |
| Finance | GL/AR/AP, receipts/deposits, journals/reversals, offsets/refunds, assets, close/year-end, cost centres/budgets and advisor-set TDS deductions | Customer advance refunds, beneficiary approval, combined/split bank matching, intercompany/profit centres and signed finance reconciliation | P1 |
| Schemes / incentives | Versioned rules, retained eligibility, accrual/payouts, rewards and employee sales commission | Progressive/per-unit/margin schemes, stacking/budgets, channel-agent beneficiaries and complete automatic correction lifecycle | P1/P2 |
| Service / collections | Chase desk, promises/disputes, tickets, complaint/CAPA, inspected RMA/credit | Customer channel ingestion, mobile cash evidence and broader downstream correction coverage | P1/P2 |
| Reports | Role dashboards, source drill-down, CSV, document/report PDFs, scheduled Excel packs | Saved views/filters, dashboard PDF packs, fuller dimensional analytics and accepted KPI definitions | P2 |
| API / mobile | Responsive web | Versioned scoped API, token/device control, employee/dealer/driver apps, encrypted offline sync/conflict handling | P2 |
| External integration | Email code and scheduled-report/reminder commands | Actual IRN/EWB/GSP, bank/payment gateway, WhatsApp/SMS/push, bridge/GPS/scanner/PLC/LIMS and legacy connectors | P2 / external |

Existing code already contains firm order-line reservations, bins, budgets, PDF documents and scheduled
Excel packs. Older roadmap snapshots that list those entire areas as absent are stale; the remaining
work is the advanced subset listed above, not rebuilding their implemented baselines.

## Verification and practical limits

Verification completed locally:

- `python manage.py check`, `migrate --check`, migration-drift and `git diff --check`: passed.
- `python manage.py test --noinput`: **722 tests discovered; 700 passed; 22 opt-in browser tests skipped**.
- After the final pipeline-query/health refinement, 72 targeted CRM/project/People/dealer/access tests passed.
- Four selected real-browser workflows passed: project pipeline (including its board), Dealer CRM
  (including its board), authentication, and dealer dashboard/statement downloads/access revocation.
  Project/CRM layouts were checked at 320/390/768/1440px and a collapsed desktop sidebar;
  dealer/access pages at 320/390/768/1440px. Local Poppins, no document overflow, JavaScript errors,
  downloads and the applicable real form actions were checked. The other 18 opt-in browser tests
  were not executed in this review.
- Browser screenshots were visually inspected and remain under ignored `test-results/`.
- Login HTTP smoke returned 200 after restarting the local server with the updated code.

Reproduce the selected browser checks with:

```sh
RUN_BROWSER_TESTS=1 python manage.py test erp.test_projects_browser erp.test_crm_browser erp.test_dealer_browser accounts.test_browser --noinput
```

A production-settings simulation (DEBUG off, transient non-persisted secret and example host) removed
the development warnings but retained Django W005/W021 for deliberately disabled HSTS subdomain/preload
flags. Enabling those domain-wide commitments requires evidence that the actual domain/subdomains are
ready for exclusive HTTPS. This simulation is not an actual deployed-host or TLS test.
No external banking/GST/hardware call, real invitation, production deployment or business-data migration
is part of this milestone. No schema migration is needed for these changes. The local server remains a
development server, not a production hosting setup.

`check --deploy` in the local development configuration reports the expected DEBUG/HTTPS/secure-cookie/
HSTS warnings. These are deployment gates; turning HTTPS-only flags on the local HTTP server is not
production acceptance. Actual credentials, provider choices, policies, operating data and UAT results
must come from the company/environment and cannot be fabricated by code.

## Execution order

1. Finish this access + People + sales visibility milestone and retain test/browser evidence.
2. ✅ Channel hierarchy (upline network) done 25 Nov. Next: company-approved C&F/agent commercial workflows, then dealer order
   requests and secondary sales, with independent reviews and reconciliation tests.
3. Finish lot/bin operational controls and own-plant capacity/MRP in separate verified milestones.
4. Harden PostgreSQL/hosting/restore/monitoring and conduct load/race/UAT checks before rollout.
5. Implement provider-backed GST/bank/hardware/mobile work against real configuration and test evidence.

The product is not declared “fully advanced” or SRS-complete while these requirements remain open.
