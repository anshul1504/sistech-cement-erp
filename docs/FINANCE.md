# Finance operations

This is the internal Sistech accounting workspace. It is a working implementation of the central ledger and treasury workflows, with the limits below. It is not completion or business sign-off of the SRS.

## Installation and upgrade

1. Back up the database before migrating; do not commit the backup.
2. Run `python manage.py migrate` and `python manage.py seed_chart`.
3. Run `python manage.py setup_roles` to create missing presets. Existing groups are preserved. An administrator must grant **Company-wide finance access** to existing finance/auditor groups that need central books, in addition to individual module actions. Do not reset customised role matrices just to grant this scope.
4. For documents created before the ledger existed, review the chart/posting map with finance, then run `python manage.py post_ledger` on a restored copy first. It is atomic and source-keyed; any failure rolls back the backfill. It needs an existing super administrator and cannot post to closed months. It does not rewrite existing entries or infer missing opening balances.
5. Reconcile opening stock, receivables, supplier/transporter payables, bank and the trial balance before using the ledger operationally.

An upgrade snapshots current beneficiary master data on existing payment proposals; it cannot reconstruct historical bank details that were never stored. Confirm those payments with finance before release. Existing credit-funded assets are identified from their posted capitalisation journals.

## Daily workflows

- **Manual vouchers:** Accountant drafts a balanced voucher using active non-control accounts. An independent user with journal approval permission approves or rejects it. Control accounts are posted through source documents. The journal screen permits reversal only for original manual entries, with both create and approval permissions and a reason. System corrections must use the originating receipt/payment/credit/debit workflow.
- **Payments:** Choose a supplier/transporter, approved open bills and credit-funded assets; optionally apply standalone debit notes. Proposed/approved allocations reserve items against another proposal. Another user approves; a releaser other than the maker records the UTR/cheque reference after confirmation in the bank portal. Proposed/approved payments can be cancelled by a checker with a reason. Changed beneficiary details require cancellation and a fresh proposal. Failed/returned payments reverse the ledger and free allocations.
- **Bank:** Import UTF-8 CSV columns `Date,Description,Reference,Withdrawal,Deposit,Balance`. Dates accept DD/MM/YYYY or YYYY-MM-DD. Exactly one positive withdrawal/deposit is required; balance is optional. A malformed row rolls back the entire import. Re-imported identical rows are skipped. Auto-match needs one exact reference and matching amount within five days; ambiguous/no-reference entries stay for manual review. Matching/unmatching requires edit permission and is audited.
- **Assets:** Record cost, residual value, site, put-to-use date and SLM life or WDV annual rate. Credit assets become payable; bank-funded assets post directly. Depreciation uses full months, SLM `(cost − residual) / years / 12` or WDV `book value × annual rate / 100 / 12`, capped at residual value. Runs are idempotent by asset/month and cannot insert a month before already posted depreciation. Disposal cannot predate acquisition or later posted depreciation.
- **Close:** Review source documents, control-account differences, bank reconciliation, GST and stock counts. Close refuses pending manual vouchers in the month, current control differences and unmatched imported bank rows through the period end. Posting into a closed month fails atomically; a privileged user can reopen it with an audit event.

## Report meaning and limits

- Trial balance and account ledger show opening, selected-period movements and closing balance. Default dates follow the company's configured financial-year start month.
- P&L uses selected-period income/expenses. Balance sheet includes cumulative profit under equity; no automatic year-end retained-earnings journal exists.
- Supplier balances include credit-funded assets and actual released payments, with returns restoring the liability. Proposed payments reserve capacity but do not reduce the accounting liability.
- Cash flow is a direct-method summary classified by counterpart account keys. Mixed-purpose journals use the first matching category; it is a management report requiring finance review.
- GST reports summarize recorded documents only. They do not validate tax eligibility, reconcile government statements, file returns, calculate all statutory adjustments or generate an external IRN/e-way bill. HSN quantities are separated by unit and tax rate.
- Site filters show journals attributed to that site. Central receipts, payments and freight bills have no site attribution, so a plant-filtered statement is not a complete plant financial statement. The default company-wide report includes them.
- Control checks compare current AR, inventory valuation, supplier AP and transporter AP. They are not historical subledger snapshots. Close does not prove all bank statement pages were imported, every bank-ledger line cleared, or every source document posted. Full close sign-off, bank completeness, stock/tax review and year-end remain pending.
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
