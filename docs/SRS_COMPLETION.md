# SRS completion work register

Source: supplied *Cement Manufacturing ERP — Customized SRS v2*, 19 October 2026.
Current objective: complete the remaining requirements module by module, retain test evidence, and push verified milestones. The document's text is a specification, not authorization to send messages, submit tax filings or invent company policies.

Implemented core workflows are recorded in [ROADMAP](ROADMAP.md). A checked implementation milestone is developer evidence, not company acceptance of the entire SRS. No overall completion percentage is asserted.

## Build order and remaining scope

| Area | Implemented baseline | Remaining scope / acceptance gate |
|---|---|---|
| Identity and access (§3) | Password/email OTP, throttling, roles/sites, sessions, temporary expiry, sensitive-action reauthentication, dated delegation, configured approval routing, eligible unavailable-staff handoff, reviewed CRM/beat ownership transitions | Remaining approval integrations/cross-module audit, batch staffing orchestration, deployment scheduling and security acceptance |
| Organisation / masters (§2) | Single company, products, sites, counterparties | Multi-entity/GST hierarchy, cost centres, versioned alternate units and dimensional access |
| CRM (§5.1) | Leads, onboarding, routed/delegated KYC/credit/activation reviews, targets, territories/beats, reviewed dated ownership transfer, project pipeline | Audited duplicate merge, parent channels, project pricing/schedules, advanced targets, secondary sales, field GPS/mobile |
| Sales (§5.2) | Prices, quotations, orders, overrides, backorders, revisions | Firm allocations, complete lot/bin ATP, margin floor, full concurrency proof, dealer self-service |
| Schemes (§5.3) | Versioned rules, retained eligibility, accrual, payouts, recognition with routed/delegated reviews and retained delivery actor | Quantity/per-unit/margin and progressive rules, budgets/stacking, additional beneficiary/tax handling, full automatic reversal lifecycle |
| Procurement (§5.4) | PR/RFQ/PO, inbound/GRN, matching, returns and AP | Demand/budget planning, deeper supplier verification, hardware/evidence acceptance |
| Inventory (§5.5) | Stock ledger, exact WAC value, transfers/counts/damage | Full warehouse/bin/lot balances, allocations/reservations, versioned conversions and recall coverage |
| Production / QC (§5.6–5.7) | Recipes with routed/delegated approval, issues, shifts/packing, results, release, certificates, calibration, CAPA | Demand/capacity/MRP, complete cost allocation, rework/write-off, sampling schedules, automated quarantine/recall |
| Dispatch (§5.8) | Gate/weighments/loading/dispatch with independently routed weight review, POD records, freight, routed/delegated bill review with retained rejection evidence | Hardware, GPS/ETA, mobile delivery proof, full failure/diversion/return states and advanced freight |
| Billing / finance (§5.9–5.10, §9) | GST billing, GL/AR/AP, payments, CSV bank match, assets, close | IRN/EWB, budgets/dimensions, tax scope, combined/split matching, beneficiary approval, signed finance reconciliation |
| Collections / service (§5.11–5.12) | Collections desk, tickets/complaints/CAPA with routed/delegated independent reviews, inspected RMA and credit | Mobile cash evidence, deposit/refund lifecycle, channel ingestion and all downstream corrections |
| API / Android (§6) | Responsive web only | Versioned API, employee/dealer/driver apps, token/device controls, encrypted offline sync and conflicts |
| Reports (§11) | Role dashboards, source drill-downs, CSV, KPI definitions | Scheduled PDF/Excel packs, saved filters, complete dimensional analytics |
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

- Complaint/CAPA and quotation access boundaries: commit d8d63bb.
- Scheme finance/site boundaries and preserved custom role assignments: commit 3271810.
- Temporary-access expiry: developer verified (405 full-suite tests passed; eight optional browser checks skipped; authentication browser and seven expiry checks passed explicitly); see [access hardening](ACCESS_HARDENING.md).

- Sensitive web reauthentication: implemented; see [controls and verification](SENSITIVE_AUTHENTICATION.md). Dated delegation for configured risk actions is now implemented; configured approval routing and unavailable-staff handoff are now implemented; remaining approval integrations and reviewed CRM/beat ownership handoff remain in the access workstream.

- Dated approval delegation: 25 configured action types, retained actual approver/use history, expiry/revocation, maker-checker and queue controls; see [scope and evidence](APPROVAL_DELEGATION.md).

- Extended site approvals include PR/RFQ, inbound/production QC, RMA, beats, supply contracts, incentive rules, AP exceptions and transfers. RFQ proxy makers remain excluded from later PO approval. Sourcing write/read site boundaries are enforced. CRM/freight/service/recognition approvals and unavailable-staff handoff remain pending.

- Approval routing and unavailable-staff handoff: explicit qualified reviewers/backups, bound previews, retained partial/attention receipts, automatic disable/expiry retry and historical maker protection. CRM/beat evidence-bound ownership changes and remaining approval types are still pending. See [work handoff](WORK_HANDOFF.md).
