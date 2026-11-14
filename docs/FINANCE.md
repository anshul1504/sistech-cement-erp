# Finance operations

This is the internal Sistech accounting workspace. It is a working implementation of the central ledger and treasury workflows, with the limits below. It is not completion or business sign-off of the SRS.

## Installation and upgrade

1. Back up the database before migrating; do not commit the backup.
2. Run `python manage.py migrate` and `python manage.py seed_chart`.
3. New close permissions are **Prepare month-end close review**, **Prepare year-end carry-forward**, **View financial year close**, and **Approve year-end close and reopen**. Grant preparation to accountants and approval to controllers; the maker cannot approve their own submission. Run `python manage.py setup_roles` to create missing presets. Existing groups are preserved. An administrator must grant **Company-wide finance access** to existing finance/auditor groups that need central books, in addition to individual module actions. Do not reset customised role matrices just to grant this scope.
4. For documents created before the ledger existed, review the chart/posting map with finance, then run `python manage.py post_ledger` on a restored copy first. It is atomic and source-keyed; any failure rolls back the backfill. It needs an existing super administrator and cannot post to closed months. It does not rewrite existing entries or infer missing opening balances.
5. Reconcile opening stock, receivables, supplier/transporter payables, bank and the trial balance before using the ledger operationally.

An upgrade snapshots current beneficiary master data on existing payment proposals; it cannot reconstruct historical bank details that were never stored. Confirm those payments with finance before release. Existing credit-funded assets are identified from their posted capitalisation journals.

## Daily workflows

- **Manual vouchers:** Accountant drafts a balanced voucher using active non-control accounts. An independent user with journal approval permission approves or rejects it. Control accounts are posted through source documents. The journal screen permits reversal only for original manual entries, with both create and approval permissions and a reason. System corrections must use the originating receipt/payment/credit/debit workflow.
- **Payments:** Choose a supplier/transporter, approved open bills and credit-funded assets; optionally apply standalone debit notes. Proposed/approved allocations reserve items against another proposal. Another user approves; a releaser other than the maker records the UTR/cheque reference after confirmation in the bank portal. Proposed/approved payments can be cancelled by a checker with a reason. Changed beneficiary details require cancellation and a fresh proposal. Failed/returned payments reverse the ledger and free allocations.
- **Bank:** Import UTF-8 CSV columns `Date,Description,Reference,Withdrawal,Deposit,Balance`. Dates accept DD/MM/YYYY or YYYY-MM-DD. Exactly one positive withdrawal/deposit is required; balance is optional. A malformed row rolls back the entire import. Re-imported identical rows are skipped. Auto-match needs one exact reference and matching amount within five days; ambiguous/no-reference entries stay for manual review. Matching/unmatching requires edit permission and is audited.
- **Assets:** Record cost, residual value, site, put-to-use date and SLM life or WDV annual rate. Credit assets become payable; bank-funded assets post directly. Depreciation uses full months, SLM `(cost − residual) / years / 12` or WDV `book value × annual rate / 100 / 12`, capped at residual value. Runs are idempotent by asset/month and cannot insert a month before already posted depreciation. Disposal cannot predate acquisition or later posted depreciation.
- **Month close:** open the period review, reconcile historical control balances and resolve missing source postings, invoice exceptions and all draft journals through the cutoff. For each used bank, enter the statement closing balance and a reference covering the month end. The confirmed bank balance must equal books less outstanding book entries; statement rows must be matched. The accountant attests tax, stock and bank review and records evidence references. Another authorized user approves the exact snapshot. Any journal or bank-evidence change invalidates the submission. A month cannot close before it finishes.
- **Month reopening:** requires close permission and a reason. Reopen later closed months first. Posting or bank-match changes before a closed cutoff are blocked, including dates in earlier months; an older open month does not bypass that cutoff. Review submissions and approvals remain available after reopening; a new independent review is required to close again.
- **Year close:** after all twelve months are independently closed, the accountant prepares the year-end proposal. A different year-end approver posts income/expense balances to retained earnings, separately by the journal’s site (and HO). The fiscal year follows Organisation setup. P&L excludes these carry-forward entries. Zero-profit years may close without a journal. Closed years reject duplicate approval and ordinary journal reversal.
- **Year reopening:** the accountant requests reopening with a reason; a different year-end approver posts linked reversals. Later closed years must be reopened first. Monthly periods remain closed until separately reopened. Reclosing creates a new revision and keeps all previous review snapshots and reversing journals. A financial-year configuration change cannot create an overlapping close.

## Report meaning and limits

- Trial balance and account ledger show opening, selected-period movements and closing balance. Default dates follow the company's configured financial-year start month.
- P&L uses selected-period income/expenses and excludes year-end transfer/reversal entries. The balance sheet shows untransferred profit separately; an approved year close moves that amount to retained earnings without changing total equity.
- Supplier balances include credit-funded assets and actual released payments, with returns restoring the liability. Proposed payments reserve capacity but do not reduce the accounting liability.
- Cash flow is a direct-method summary classified by counterpart account keys. Mixed-purpose journals use the first matching category; it is a management report requiring finance review.
- GST reports summarize recorded documents only. They do not validate tax eligibility, reconcile government statements, file returns, calculate all statutory adjustments or generate an external IRN/e-way bill. HSN quantities are separated by unit and tax rate.
- Site filters show journals attributed to that site. Central receipts, payments and freight bills have no site attribution, so a plant-filtered statement is not a complete plant financial statement. The default company-wide report includes them.
- Cutoff checks reconstruct AR, inventory movement value, supplier AP and transporter AP from dated source records. Later cheque bounces/payment returns do not alter an earlier cutoff balance. Inventory history uses recorded movement costs; it is not a historical physical-count report. Current valuation checks remain available on the periods overview. Source coverage detects missing postings for the supported document types; direct database edits remain outside the application audit boundary.
- Bank closing balances, completeness references and tax/physical-stock sign-offs are human evidence. The application checks reconciliation and independent approval, but does not fetch or authenticate bank statements or independently verify tax/physical counts. Outstanding book entries may legitimately remain when the bank balance reconciles and the reviewer attests them.
- The posting engine and close/reconciliation mutations share a transactional finance lock, and a close retains a fingerprint of the reviewed journal lines. This prevents changes from silently slipping through a reviewed cutoff in the supported workflow; concurrency must still be validated on PostgreSQL before rollout.
- Identical same-day bank rows with the same description/reference/amount are treated as duplicates. If a bank contains genuinely distinct identical rows, resolve their external identifiers before import; the current importer has no bank-provided transaction-ID column.
- No direct banking calls occur. Bank exports contain beneficiary data and are restricted/audited. Accountants must verify their bank portal's required CSV format before upload.
- SQLite is for development. Payment proposal locking, posting/close races and bank matching need concurrent PostgreSQL testing before production. Multi-entity books, cost centres, budgets, statutory depreciation/tax filing, bank-change approval workflow and sensitive-action reauthentication remain pending.

## Verification

```sh
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
# Optional: install Playwright and Chromium in the development environment first.
RUN_BROWSER_TESTS=1 python manage.py test erp.test_finance_browser
```

The browser check creates disposable accounts and documents in an isolated test database. Screenshots go to ignored `test-results/finance/`; no production account or OTP is used.
