# Vendor-credit bank refunds

This workflow records money returned by a supplier/transporter against recognized vendor credits. It extends existing company-finance payment controls; customer refunds, deposits, contractual damages and external banking remain separate SRS scope.

## Request, independent review and bank receipt

Finance → Payments → Pay / offset / refund now includes vendors with credits only. Choose **Vendor refund (incoming)**, the company receiving bank, recognized credits and a reason. Enter positive selection amounts; the service stores each credit allocation and the incoming cash amount with a negative sign. Outgoing bank amounts remain positive; non-cash offsets remain zero. The database enforces each mode's sign and bank requirement. Refunds cannot include positive bills or combine an offset with incoming cash in one document.

Proposal reserves credit against other bank payments, non-cash offsets and refunds. Existing company-finance, payment read/create, independent approval and vendor bank-movement permissions govern the stages. The proposer cannot approve, record receipt or reverse their receipt directly/by proxy. Current principal/deputy read, finance scope and account policy continue to apply. The existing payment stages support routing/inbox/handoff; no new staff grants or delegated action types are created.

After independent approval, choose **Record refund received**, enter the actual incoming bank reference, bank value date and finding. The service rechecks the receiving bank, source ownership, source/cutoff dates, current recovery/POD evidence, credit capacity and original paid credit backing. Bank value date drives the receipt journal and cutoff balance; the proposal date remains in its original audit receipt. Closed/future dates and dates before the underlying credits/backing are refused. The operator records an observed bank credit; this feature neither requests/transmits funds nor obtains a provider acknowledgement. Actual bank-statement reconciliation remains required.

The same incoming reference cannot be recorded twice on the receiving bank, including across vendors or after reversal. Comparison ignores case; a bank-row mutex serializes the check. Failed reference/source/posting checks preserve the approved request without consuming a delegated-use receipt or writing a financial journal. Repeated receipt attempts do not post twice.

## Ledger and reversal

Receipt posts **Dr receiving bank / Cr supplier payable and/or transporter payable** through the shared GL engine. Signed received allocations consume the recognized credits; original bills, debit notes, outgoing bank allocations, recovery adjustments and their approval evidence stay unchanged. Existing current and historical subledger arithmetic includes these signed allocations. Supplier debit-note credits and paid freight-recovery credits use their respective control accounts.

For example, ₹9,000 paid freight followed by ₹1,600 approved recovery creates a −₹1,600 open credit. A ₹1,600 incoming refund consumes that credit and debits the bank by ₹1,600. It is never represented as an outgoing transfer. If the receipt is later reversed, a linked GL reversal reopens the vendor credit. Earlier cutoff reports retain the receipt until the recorded reversal date.

**Reverse refund receipt** requires an independent authorized actor and a reason. It retains the original incoming reference, proposal/approval/receipt findings, actual receipt actor/time and reversal actor/time. A missing/already-reversed original journal blocks reversal; period rules are rechecked by the shared engine. Repeated reversal fails without posting twice.

Outgoing bank release/failure and non-cash execution/reversal reject refund-mode documents. Bank payment-file exports exclude incoming refunds, including direct calls to the CSV service. Cash-flow and bank reconciliation use the actual debit/credit journal lines. A refund is not a legal tax/credit-note correction and does not issue government documents.

## Migration and acceptance

Migration **0053** adds the incoming refund mode and extends the existing payment bank/sign constraint. A pre-upgrade SQLite backup was created outside Git; all **135** existing nonmetadata table counts were preserved and no live refund/approval/posting or staff grant was created. Earlier migration/data history remains intact. After refund documents exist, a downgrade requires a reviewed data/rollback plan; never turn incoming receipts into outgoing payments to satisfy the previous constraint.

PostgreSQL concurrency/deadlock/load validation, bank delivery/provider validation, company finance sign-off, customer/deposit refunds, multi-party netting and excess contractual damages remain pending. Developer tests do not certify those gates.


## Developer verification — 15 November 2026

Full suite: **652 tests, 632 passed and 20 optional browser checks skipped**. All three affected vendor-refund/non-cash/finance browser checks passed explicitly. After the final header-capacity and direction-wording refinement, all **44** related refund/non-cash/finance regressions and the refund browser scenario passed again. Thirteen new refund regressions cover incoming signs/journals, source reservation, independent/delegated actor and rollback, returned outgoing backing, closed periods, receiving-bank validity, bank-file/action separation, sign/bank constraints, duplicate reference retention, supplier debit-note refunds, original proposal-date evidence, actual bank value-date cutoffs and later reversal. The browser completes proposal → independent approval → incoming receipt → reversal at 320/390/768/1440px without page overflow or JavaScript errors. Django, applied-migration/drift and diff checks passed. All verification used fictional isolated data; PostgreSQL concurrency, external banking and company acceptance remain pending.
