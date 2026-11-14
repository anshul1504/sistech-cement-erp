# Sistech ERP — Implementation roadmap

Reference: *Cement Manufacturing ERP — Customized SRS v2.0 (19 Oct 2026)*. This roadmap maps that SRS to the
current code and fixes the build order. Each step ends with tests, a device check, README update and a push.

Control points from SRS §19 that every step must respect: one stock ledger, one finance posting engine,
versioned price/scheme rules, immutable weighbridge readings, batch-quality traceability, role-scoped access,
idempotent external integrations, month-end reconciliation, maker-checker, no edits to posted documents.

## Done (foundation)

| SRS area | Status |
|---|---|
| Identity & security (§3, NFR-001/002/003) | Login + email OTP, lockouts, sessions/devices, roles × module × action matrix, site scope, maker-checker on orders, append-only audit |
| Organisation (§2.2 company) | Single company profile, GSTIN/PAN/CIN/BIS, bank, branding, compliance documents with expiry |
| Masters (§2.2) | Products (cement + TMT specs, HSN, GST rate), plants/depots (facility, GSTIN, capacity, licences), dealers/suppliers/transporters (GSTIN, PAN, credit terms, KYC) |
| Basic transactions | Sales/purchase order draft → submit → approve → post; credit-limit block; stock ledger with source document and non-negative stock; production → quality hold → release |
| Masons & contractors (Step 1d) | Influencer master with district/dealer views, coverage gaps, activities and referral leads, CSV import/export |
| Price list & numbering (Step 2a) | Versioned price rules with slabs and scopes, number series per site and FY, GST snapshot and CGST/SGST/IGST on orders |
| GST tax invoice (Step 2b) | Invoice from dispatched order, frozen snapshot, FY series ≤16 chars, A4 print/PDF, register with GSTR-1 style export |
| Receivables (Step 2c) | Receipts with FIFO settlement and advances, s.269ST cash limit, cheque bounce, GST credit notes with stock return, customer ledger, ageing, overdue-aware credit control |
| Gate, weighbridge & dispatch (Step 3a) | Vehicles/drivers with document validity, truck flow with immutable weighments, tolerance/overload exceptions, invoice per truck, stock at configured event, POD, slip & gate pass |
| Freight (Step 3b) | Lane rate cards, contracted freight frozen at gate-out, transporter bill check with caps, recovery and maker-checker |
| Web shell | Responsive app shell, toasts, Indian number formats, My account, Administration, System settings |

## Build sequence

### Step 1d — Masons & contractors (influencer network) — requested by Sistech ✅ done
1. Influencer master: type (mason/mistri, contractor, engineer, architect, builder), name, mobile, district/tehsil/village,
   linked **dealer(s)**, specialisation, experience, typical monthly cement/TMT volume, KYC, active status.
2. **District-wise** view: count of masons/contractors per district, linked dealers, coverage gaps.
3. **Dealer-wise** view: which masons/contractors buy through each dealer; dealer detail page shows them.
4. Activity: meets/visits, site referrals and projects (feeds Step 8 loyalty/schemes and lead pipeline).
5. Duplicate check by mobile number; import from Excel/CSV.

### Step 2 — Order-to-cash core (SRS Phase 1, §4.1, §5.2, §5.9, §5.11) — mostly done (remaining: partial supply & revisions move into Step 3 dispatch)
1. ✅ Number series per document type and financial year. (Financial period open/close moves to Step 7.)
2. ✅ Price lists: product × plant × district × buyer type × dealer, quantity slab, effective dates; price snapshot on orders.
3. Sales order upgrade: bill-to/ship-to, freight terms, partial supply, full status set (Approved → Allocated → Part dispatched → Dispatched → Invoiced → Closed), revisions after approval.
4. ✅ Validations: dealer active, credit **and overdue** (block), price below list (flag), free stock after reservations (warn). KYC-document gating is configurable later.
5. ✅ Dispatch advice → **GST tax invoice**: CGST+SGST vs IGST from place of supply, HSN, invoice series per GSTIN, PDF print with company/bank/terms, immutable after posting.
6. ✅ Credit notes and sales returns linked to the original invoice (debit notes with procurement, Step 4).
7. ✅ Dealer ledger and receipts (allocation to invoices), so credit exposure uses real outstanding.

### Step 3 — Gate, weighbridge & dispatch (SRS Phase 2, §4.4, §5.8)
1. ✅ Vehicles and drivers master (permit/insurance/fitness expiry); transporter link.
2. ✅ Gate-in with token, tare, loading (bags/MT, bay, seal), gross, net = gross − tare with tolerance; **immutable readings + reweigh**.
3. ✅ Gate pass / weighment slip printing; stock posts at one configured event only.
4. ✅ Trips, POD, freight rate cards and freight bill check.
5. Inbound flow for purchases: gate → weighment → unload → GRN — built with Step 4 (GRN).

### Step 4 — Procure-to-pay (SRS Phase 1–3, §4.2, §5.4)
1. ✅ Purchase requests (manual + reorder level), RFQ and comparison, PO approval by value.
2. GRN with accepted / rejected / short / quality-hold quantities; incoming inspection.
3. Supplier invoice with PO–GRN–invoice three-way match and duplicate check; supplier returns / debit notes.

### Step 5 — Inventory depth (§5.5)
Stock states (available, reserved, quality hold, damaged, in transit), lot/batch, inter-site transfers with
in-transit stock, cycle counts, adjustments with approval, damaged/wet bag reports, UOM conversions (bag ↔ MT).

### Step 6 — Production & quality for cement and TMT (SRS Phase 2, §4.3, §5.6, §5.7)
1. Versioned recipes/BOM (clinker + gypsum + fly ash/slag; billet → TMT), process orders, material issue, shift log, downtime, packing line.
2. Test specifications with limits (cement IS 269/1489/455: fineness, setting time, 3/7/28-day strength; TMT IS 1786: YS, UTS, elongation, bend), out-of-spec hold, release, **COA / mill test certificate**, calibration.
3. Batch traceability from raw material to dispatched invoice.

### Step 7 — Finance (SRS Phase 3, §5.10, §9)
Chart of accounts, double-entry posting engine (invoices, receipts, GRN, payments post automatically), AR/AP ageing,
payments with maker-checker, bank reconciliation, trial balance, P&L, balance sheet, cash flow, fixed assets,
period close checklist, GST summaries for reconciliation.

### Step 8 — Dealer network, schemes & service (§5.1, §5.3, §5.12)
Leads and project pipeline, dealer onboarding approval, territories/targets, schemes and commissions with
versioned rules and payout batches, complaints/returns with CAPA.

### Step 9 — Dashboards & reports (§11)
Executive, sales, stock, dispatch, plant, quality, finance and credit dashboards with drill-down to source documents.

### Step 10 — APIs & Android apps (SRS Phase 4, §6)
Versioned REST API with token auth and idempotency keys, then employee, dealer and transporter/driver apps.

### Step 11 — Integrations & go-live (SRS Phase 5, §12, §14)
E-invoice / e-way bill via the company's GSP, weighbridge hardware, payment gateway, GPS, WhatsApp/SMS; PostgreSQL,
backups/restore drill, monitoring, data migration and UAT (§15).

## Open decisions needed from Sistech (SRS §17)
GST registrations and plants/depots list; product grades and bag sizes actually sold; current accounting software and
stock valuation method; stock posting event (loading complete vs gate-out); credit and price-approval policy;
e-invoice/e-way bill provider; weighbridge make/model; freight basis; user count and Android devices; hosting.
