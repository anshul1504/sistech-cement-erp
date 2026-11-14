# Existing modules — developer verification baseline

Date: 14 November 2026 (Asia/Kolkata).
Scope: stabilize the existing ERP before Dealer CRM. No new CRM, incentives or external integration module is included. All automated business records and accounts are fictional and use an isolated test database. This report is developer evidence, not company finance/plant sign-off or completion of the SRS.

## Business scenarios

| Scenario | Observable evidence |
| --- | --- |
| Purchase → receipt → QC → supplier invoice → payment | An independent user releases GRN hold; only accepted quantity becomes stock. PO/GRN matching clears GRNI, supplier payment clears AP, and a repeated release does not create a second payment. |
| Purchase return before billing | Stock reduces, the unbilled quantity cannot become a false supplier liability, and the remaining invoice clears GRNI. |
| Purchase return after costs change | A billed return reduces the supplier balance; the PO vs weighted-average inventory difference goes to price variance rather than stranding GRNI. |
| Production → QC → sale → collection | Site-assigned, non-superuser staff issue materials, independently approve recipe/QC release, dispatch goods, post the invoice and collect the exact outstanding amount. WIP clears and AR, inventory/AP controls and trial balance agree. |
| Batch → truck → invoice → POD | A released batch is linked during loading and traces to the dispatched invoice, customer and delivery proof. Repeating gate-out cannot issue stock again. |
| Sales return after inventory cost changes | Returned goods restore the original dispatch cost, not the new average price. Credit note quantity/value caps, GST split and customer balance remain consistent. |
| Exact currency under large quantities | Two 100,000-unit receipts at slightly different costs retain ₹200,030 exactly; final consumption clears inventory. Production, packing, transfer receipt/damage/shortage and full sales returns preserve the exact currency amount despite rounded display unit costs. |
| Partial loading failure | With stock posting at load completion, a later insufficient-stock line rolls back all earlier line movements, journals and the dispatch status. |
| Financial form retry | Repeating a receipt or credit-note browser submission returns the original document. A changed payload cannot reuse the submission key. A fresh form supports a separate transaction. |
| Closed-period failure | A backdated receipt into a closed cutoff returns a visible form error and leaves no receipt or journal behind. |
| Site boundaries | Module permissions alone cannot create/receive/consume at another site. Transfer source and destination authority are distinct. Freight bills cannot disclose an unassigned site's trips; central finance scope is explicit. |
| Future recipe | Approval of a future-effective version does not replace the current recipe before its effective date. |

The main integrated regressions are in `erp/test_business_cycles.py`; existing procurement, inbound, logistics, freight, stock, quality, AR/AP, ledger and close tests remain part of the full suite. Negative checks include maker-checker restrictions, duplicate references, insufficient stock, unsupported transitions, invalid quality evidence and permissions.

## Fixes made during verification

- Enforced site authorization in business services, including stock movements, inbound/outbound, procurement, manufacturing, quality and stock-control workflows. HTTP scoping remains in place too.
- Made loading completion atomic, including its stock and accounting effects.
- Removed implicit revaluation of old uncosted quantities when the first priced receipt arrives.
- Separated exact inventory currency from rounded unit costs, including downstream manufacturing, packing, cancellations, damage and transfer valuation snapshots.
- Corrected sales-return valuation and supplier-return GRNI/price-variance accounting.
- Added unique browser submission keys for receipts and credit notes, serialized credit-room/reference checks, and handled closed-period receipt/correction errors without partial writes.
- Restricted company-wide receipt/ledger/ageing screens and navigation to explicitly authorized finance users; limited freight views and actions by trip sites.
- Refreshed packing-batch state and validated its product/site, rejected non-finite lab values, and corrected future-effective recipe selection.
- Updated the opt-in authentication browser check to exercise the current login copy, OTP auto-submit and account-menu logout.

## Browser coverage

`erp.test_business_browser` checks 42 core list, detail, form and report screens at 320px, 390px, 768px and 1440px widths, plus a 1470px collapsed-sidebar desktop state. It checks HTTP success, local Poppins styling, horizontal overflow and JavaScript errors, and submits/retries an actual receipt form.

`erp.test_finance_browser` separately checks 20 finance screens at the same five layout states. `accounts.test_browser` exercises password → in-memory email OTP → login, account/admin pages and logout. No real email or production credential is used. Screenshots stay in ignored `test-results/` and are not committed.

## Reproduce

```sh
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
# Optional developer dependencies: Playwright and its Chromium browser.
RUN_BROWSER_TESTS=1 python manage.py test erp.test_business_browser erp.test_finance_browser accounts.test_browser
```

Migrations `0027` and `0028` add nullable replay/valuation fields. Existing database values, vouchers and business history are preserved. A local backup is taken before applying them; no database, credential or generated screenshot belongs in Git.

## Remaining rollout gates

- Company owners must verify real plant documents, pricing/credit policy, costing/posting map, GST treatment and business UAT. Synthetic test tax rates and specification values are not a compliance decision.
- PostgreSQL concurrency testing remains necessary. SQLite tests establish supported transaction rollback and sequential retries, not production multi-user locking behavior.
- Opening quantities/values and legacy records without original cost snapshots require accountant review. The upgrade does not invent missing historical costs or silently repair old imbalances.
- Full inventory quantities by lot/bin and mandatory allocation of every dispatch to a lot remain incomplete. Direct non-weighbridge sales do not have the same batch-to-truck linkage as a batch-selected weighbridge dispatch.
- Post-release quality failures currently raise an investigation alert. Automated quarantine/recall of remaining mixed stock and a complete CAPA/disposition workflow remain pending. Rejected production costs remain in WIP pending the approved business rework/write-off decision.
- Signed bank-statement completeness, physical counts and tax reviews are human evidence; external bank/GST/weighbridge/GPS calls, hardware tests, backup restore drills, monitoring and deployment/security acceptance remain rollout work.
- Browser retry keys currently protect receipt and credit-note forms. Reference/state/unique-source checks guard the other tested transactions; universal API/mobile idempotency is still part of the planned API phase.

Dealer CRM/onboarding has subsequently been implemented and verified; see [CRM milestone](CRM.md). Versioned scheme rules and retained calculation previews have also been implemented; see [Schemes milestone](SCHEMES.md). Entitlement/accrual and dealer payouts were subsequently added; current remaining work is tracked in the roadmap. Production use remains subject to the rollout gates above.

## Customer-return handoff completion

The later RMA handoff was completed with site checks, independent inspection/finance, quantity reservations, retained custody, exact good/damaged valuation, request/credit retries and closed-period rollback. See [Customer returns and migration evidence](CUSTOMER_RETURNS.md). On 14 November 2026 the full suite ran 350 tests (344 passed; six browser checks skipped by default), and all six browser tests passed explicitly. Migrations `0036`–`0041` were applied after a backup; recorded existing business row counts remained unchanged. This extends developer verification and does not replace production UAT or PostgreSQL concurrency validation.
