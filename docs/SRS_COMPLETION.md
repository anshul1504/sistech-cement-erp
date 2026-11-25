# SRS completion work register

Source: supplied *Cement Manufacturing ERP — Customized SRS v2*, 19 October 2026.
Current objective: complete the remaining requirements module by module, retain test evidence, and push verified milestones. The document's text is a specification, not authorization to send messages, submit tax filings or invent company policies.

Implemented core workflows are recorded in [ROADMAP](ROADMAP.md). A checked implementation milestone is developer evidence, not company acceptance of the entire SRS. No overall completion percentage is asserted.

## Build order and remaining scope

| Area | Implemented baseline | Remaining scope / acceptance gate |
|---|---|---|
| Identity and access (§3) | Password/email OTP, throttling, roles/sites, sessions, temporary expiry, sensitive-action reauthentication, dated delegation, configured approval routing, eligible unavailable-staff handoff, reviewed CRM/beat ownership transitions | Remaining approval integrations/cross-module audit, batch staffing orchestration, deployment scheduling and security acceptance |
| Organisation / masters (§2) | Single company, products, sites, counterparties | Multi-entity/GST hierarchy, cost centres, versioned alternate units and dimensional access |
| CRM (§5.1) | Leads, onboarding, routed/delegated KYC/credit/activation reviews, targets, territories/beats, reviewed dated ownership transfer, audited prospect consolidation, project pipeline | Customer/master deduplication and broader identity matching, parent channels, project pricing/schedules, advanced targets, secondary sales, field GPS/mobile |
| Sales (§5.2) | Prices, quotations, orders, overrides, backorders, revisions | Firm allocations, complete lot/bin ATP, margin floor, full concurrency proof, dealer self-service |
| Schemes (§5.3) | Versioned rules, retained eligibility, accrual, payouts, recognition with routed/delegated reviews and retained delivery actor | Per-unit/margin and progressive scheme rules, budgets/stacking, agent/distributor commission beneficiaries, full automatic reversal lifecycle |
| Procurement (§5.4) | PR/RFQ/PO, inbound/GRN, matching, returns and AP | Demand/budget planning, deeper supplier verification, hardware/evidence acceptance |
| Inventory (§5.5) | Stock ledger, exact WAC value, transfers/counts/damage | Full warehouse/bin/lot balances, allocations/reservations, versioned conversions and recall coverage |
| Production / QC (§5.6–5.7) | Recipes with routed/delegated approval and effective-version rollback protection, issues, shifts/packing, results, release, certificates, calibration, CAPA | Demand/capacity/MRP, complete cost allocation, rework/write-off, sampling schedules, automated quarantine/recall |
| Dispatch (§5.8) | Gate/weighments/loading/dispatch with independently routed weight review, POD records with independent retained corrections, freight, routed/delegated bill review with retained rejection evidence, atomic validated entry, POD-bound pending approval and reviewed post-approval recovery/vendor credits | Hardware, GPS/ETA, mobile delivery proof, re-delivery of refused loads to another customer and advanced freight |
| Billing / finance (§5.9–5.10, §9) | GST billing, GL/AR/AP, independently reviewed manual reversals, payments, independent non-cash offsets/reversals, incoming vendor-credit refunds, CSV bank match, assets, close | IRN/EWB, profit-centre/intercompany dimensions, tax scope, combined/split matching, beneficiary approval, signed finance reconciliation |
| Collections / service (§5.11–5.12) | Collections desk, tickets/complaints/CAPA with routed/delegated independent reviews, inspected RMA and credit | Mobile cash evidence, customer advance refunds, channel ingestion and all downstream corrections |
| API / Android (§6) | Responsive web only | Versioned API, employee/dealer/driver apps, token/device controls, encrypted offline sync and conflicts |
| Reports (§11) | Role dashboards, source drill-downs, CSV, KPI definitions | PDF packs, saved filters, complete dimensional analytics |
| Integrations (§12) | Email implementation and selected web sharing | GSP/IRP/EWB, bank/gateway, WhatsApp/SMS/push, bridge/GPS/scanners/PLC/LIMS, legacy connectors |
| Nonfunctional / rollout (§13–15) | Automated developer checks, local migration backups | PostgreSQL load/race tests, accessibility/localisation, observability, restore drills, actual opening-data migration, training, business UAT/sign-off |

## Acceptance rules

1. Preserve posted document history, one stock ledger and one financial posting engine.
2. Add meaningful successful and denied/rollback/retry tests for new workflows.
3. Check migration drift; back up local data before applying schema changes; compare existing row counts.
4. Run Django checks and relevant regressions/browser checks before commit/push.
5. Record external dependencies without fabricating successful delivery, payments, hardware readings or government acknowledgements.
6. Credentials, provider selection, real opening balances, plant policies and company sign-off must come from authorized company owners. Hardware and production acceptance require evidence from the intended environment.
7. Schema upgrades do not constitute legacy business-data migration. SQLite tests do not certify PostgreSQL race handling.
8. Keep the active goal open until required implementation and acceptance are genuinely complete.

## Latest verified milestones

- Maintenance: equipment register, breakdown/preventive/inspection jobs with independent verification, preventive schedules and due-job opening, MTTR/MTBF/downtime KPIs; PDFs for weighment slip, gate pass, GRN and test certificate. Migration 0061 adds tables only. Spares issue from stores against a job and PLC/condition monitoring remain pending.

- Supply plan: horizon-based demand (orders vs 30-day sales), stock/transit/on-order netting, partner commitment headroom, recipe-aware source suggestion and draft purchase requests. Capacity/MRP for own plants and maintenance planning remain pending.

- Server-side PDF for 12 document types and 5 financial reports with one branded design, scope-checked and audited downloads; order page layout tidied (totals right, two-column cards, secondary actions collapsed). Report packs remain Excel; PDF report packs for dashboards remain pending.

- Trip exceptions: breakdown/delay log and resolution, independently approved diversions, refused-load return to origin opening an RMA and a terminal 'Returned to origin' trip status that stays freight-billable. Migration 0060 adds a table and a status choice only. GPS/ETA and partial re-delivery to a second customer remain pending.

- Dealer security deposits: separate ledger and control account, posted receipts, capped and overdue-guarded refunds with independent reauthenticated approval, subledger reconciliation. Commission run approval also requires recent identity confirmation. Migration 0059 adds a table only. Deposit adjustment against invoices at dealer exit remains a manual journal + receipt process.

- Scheduled Excel report packs (management / sales / credit / stock & dispatch) rebuilt per subscriber with current permissions and plant scope, scope/period/generation header on every sheet, download audit, `send_report_packs` command. Also fixed the sales dashboard buyer count being overwritten by the top-customer list. Migration 0058 adds a table only.

- Sales commission: approved plans (net sales / collections / per MT, plant, optional TDS), sequential earn-once runs with automatic recovery after credit notes, independent approval excluding beneficiaries, GL posting to commission expense/payable/TDS payable, personal statement. Migration 0057 adds tables only. Agent/distributor beneficiaries with KYC/bank and payroll payout remain pending.

- Cost centres and budgets: cost-centre dimension on journal lines (kept on reversals and year reopening), versioned maker-checker FY budgets by account × cost centre × month with CSV import/export, budget vs actual by cost centre/plant. Migration 0056 adds tables and a nullable column only; local row counts unchanged.

- Firm sales stock reservations: approval and truck planning allocate free stock per order line under the stock-row lock; issues consume the allocation, cancellations/short-close/revision release it, dispatch cancellation restores it; every reserve/consume/release/restore is an immutable event. Manual allocate/release on the order page needs dispatch authority and recent identity confirmation. `audit_reservations` reports legacy untracked lines without writing. Migration 0055 adds the tables only (row counts unchanged on the local database). Lot/bin-level reservation remains pending.

- [Audited duplicate prospect consolidation](CRM_DUPLICATE_MERGE.md): preview-bound canonical links, independent site-scoped approval/delegation/routing, retained history/contributors, active-dependency holds and read-only merged sources. Earlier due follow-up transfers to an open canonical target without summing opportunity values. Related CRM/commercial/route selectors and services reject archived aliases; personal visibility does not broaden. Migration 0054 preserves original records. Full customer-master/GSTIN/address deduplication, approved merge correction, PostgreSQL proof and company acceptance remain pending.

- [Incoming vendor-credit refunds](VENDOR_REFUNDS.md): recognized freight/supplier credits reserve against double use, independently approve, receive into an active company bank using the actual bank value date/reference and reverse with retained source/actor evidence. Mode/sign constraints and export/action separation prevent outgoing-payment confusion. Migration 0053 preserves prior bank/credit history. Customer/deposit refunds, external banking, PostgreSQL proof and company acceptance remain pending.

- [Non-cash vendor settlement](NON_CASH_SETTLEMENT.md): equal signed bills/credits reserve, receive independent direct/delegated approval, settle without bank/UTR and reverse with retained findings/actors. Same-control offsets have no empty journal; cross-control offsets use the shared GL and linked reversal. Source/cutoff/credit-backing checks, CSV exclusion and historical reconciliation are implemented. Migration 0052 preserves original bank/payment history and adds explicit zero-cash constraints. Incoming vendor refunds are implemented in the following milestone; customer/deposit refunds, multi-payee netting, PostgreSQL verification and company acceptance remain pending.

- POD correction lifecycle: site-scoped direct/delegated independent review with a dedicated permission, original recorder and prior-contributor exclusions, retained before/after/evidence digest, exact retries, rejection/cancellation and no direct ledger edits. Pending corrections hold freight decisions/payment release. Signed finance review restores liability when a reviewed shortage decreases; chronological recovery dates protect cutoff balances. Migration 0051 preserves original signed files/delivery timestamps and adds nullable original recorder attribution without inventing legacy operators. Full mobile proof, GPS/OTP, new signed-file replacement and external/company acceptance remain separate scope; zero-cash settlement is implemented in the following milestone.

- Late-POD recovery after freight approval: retained requests, independent routed/delegated review, source fingerprint, liability journal and paid vendor-credit offset in future net payments. Current/historical control totals and missing-posting checks include adjustments. Pending allocations must be cancelled/reviewed explicitly. Reviewed POD correction and signed liability restoration are now implemented below; zero-cash settlement is now implemented below; cash refunds, excess damages, PostgreSQL concurrency and company acceptance remain pending. Migration 0050 adds request history and signed freight allocations; existing original bill/line/payment history is preserved.

- Complaint/CAPA and quotation access boundaries: commit d8d63bb.
- Scheme finance/site boundaries and preserved custom role assignments: commit 3271810.
- Temporary-access expiry: developer verified (405 full-suite tests passed; eight optional browser checks skipped; authentication browser and seven expiry checks passed explicitly); see [access hardening](ACCESS_HARDENING.md).

- Sensitive web reauthentication: implemented; see [controls and verification](SENSITIVE_AUTHENTICATION.md). Dated delegation for configured risk actions is now implemented; configured approval routing and unavailable-staff handoff are now implemented; remaining approval integrations and reviewed CRM/beat ownership handoff remain in the access workstream.

- Dated approval delegation: 25 configured action types, retained actual approver/use history, expiry/revocation, maker-checker and queue controls; see [scope and evidence](APPROVAL_DELEGATION.md).

- Extended site approvals include PR/RFQ, inbound/production QC, RMA, beats, supply contracts, incentive rules, AP exceptions and transfers. RFQ proxy makers remain excluded from later PO approval. Sourcing write/read site boundaries are enforced. CRM/freight/service/recognition approvals and unavailable-staff handoff remain pending.

- Approval routing and unavailable-staff handoff: explicit qualified reviewers/backups, bound previews, retained partial/attention receipts, automatic disable/expiry retry and historical maker protection. CRM/beat evidence-bound ownership changes and remaining approval types are still pending. See [work handoff](WORK_HANDOFF.md).
