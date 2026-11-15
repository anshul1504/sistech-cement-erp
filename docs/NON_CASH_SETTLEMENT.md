# Non-cash vendor offsets

SRS §5.10 / FR-FIN-007, source accounting and retained correction controls. This completes the zero-cash settlement path for existing supplier/transporter sources; it does not complete the SRS or certify company acceptance.

## Workflow

Finance → Payments → Pay / offset opens approved bills and recognized credits for one payee. Vendors with positive bills remain visible even when their net balance is zero or negative. Select **Non-cash offset**, choose positive bill amounts and credit amounts, and record the vendor offset reason. The selected amounts must net to exactly zero and include both signs. Enter positive selection amounts; the service stores credits as negative allocations. Paise precision and field capacity are checked before reservation. Cash payments retain their existing strictly positive bank workflow.

An offset has **no bank account**, zero cash amount and no bank reference. A database constraint enforces that combination; other payment modes require a bank and positive cash. The form disables bank selection for offsets, and forged bank selections are refused by the service. Existing records retain their mode, bank, amounts and allocations.

The maker proposes; an independent direct/delegated reviewer approves; an authorized independent executor chooses **Settle without cash** with a required finding. Existing company-finance, payment read, approval and release permissions govern the stages. The maker cannot approve, execute or reverse directly or by proxy. Routing/inbox/handoff use the existing payment stages; the delegation inventory remains 36 action types. Both principals and deputies now require current payment read access for payment approval/release authority. No staff grants are created by this implementation.

The original proposal reason, approval finding, settlement finding and reversal reason are retained separately. Execution records the actual actor/time; reversal records its actual actor/time too. Old records are not attributed to invented actors or backfilled with inferred findings. Repeated execution/reversal fails without duplicate posting or consuming a delegated use. Audit/posting failure rolls the whole business action and delegated receipt back.

## Source and ledger controls

One payee mutex serializes reservations. Each stage rechecks source ownership, approved invoice/asset/debit-note eligibility, available reservations, current freight/POD evidence and backing of freight credits. Proposed/approved allocations reserve both selected bills and credits. A pending POD correction, changed recovery or returned original bank payment can hold execution for explicit cancellation/reallocation. Reversal releases the settlement allocations; original bill, credit, paid bank allocation and recovery histories remain retained.

Settlement cannot precede selected source dates, recognized freight recovery or its paid credit backing, and cannot use a future or closed accounting date. Execution checks the effective date again. Reversals check today's open period. Offset changes never manufacture cash movements or bypass the GL engine.

For a ₹1,600 freight credit against a ₹1,600 freight payable, signed source allocations cancel within the same control account: there is no empty/zero journal. A mixed supplier-payable/freight-credit offset posts **Dr supplier payable / Cr transporter payable**, with opposite signs supported as appropriate. It never touches bank/cash. Reversal creates the existing linked reversal journal when a cross-control journal exists; same-control reversal retains its receipt without an empty journal.

Current and cutoff-date subledger checks include signed allocations. Supplier controls now include counterparties with actual AP sources, including a transporter supplying a credit-purchased asset, rather than relying on the partner's single kind label. Close source checks require cross-control settlement journals and their reversals; they do not falsely report missing same-control empty journals. A later reversal preserves the earlier effective cutoff balance.

## Bank boundaries and remaining work

Bank CSV export excludes non-cash offsets, including when directly supplied to the export service. Offsets cannot be released with a fabricated UTR or marked as a bank-return failure; explicit settlement and reversal actions are required. Ordinary bank payments cannot use those offset actions. Cash flow and bank reconciliation remain based on actual bank/cash journal lines.

This flow supports existing approved invoices, credit-purchased assets, standalone supplier debit notes and recognized freight recovery credits for one payee. It does not implement cash refunds, multi-payee netting, withholding/tax policy changes, extra contractual damages, external bank transmission, provider acknowledgements or company finance sign-off. PostgreSQL locking/deadlock/load validation remains a rollout gate.

## Migration

Migration **0052** adds retained finding/return-attribution fields, the non-cash mode, nullable bank for that mode and the cash/offset constraint. The local upgrade checked existing payment eligibility, created a backup outside Git, preserved row counts in all **135** existing nonmetadata tables and created no live offset, approval, posting or staff grant. Nullable historical return identities and empty new finding fields intentionally retain uncertainty. SQLite upgrade tests are not PostgreSQL migration acceptance.

Once offsets exist, restoring the older mandatory-bank schema requires a reviewed rollback/data plan or the pre-upgrade backup; do not invent bank accounts or delete settlement history to satisfy a downgrade. The forward implementation keeps all original cash documents intact.


## Developer verification — 15 November 2026

Final full suite: **638 tests, 619 passed and 19 optional browser checks skipped**. Three affected non-cash/finance/freight-recovery browser checks passed explicitly; after the final bank-disable/button-label refinement, the non-cash browser check passed again. The new browser scenario creates, independently approves, settles and reverses an offset, checks source/finding receipts and verifies no overflow or JavaScript errors at 320/390/768/1440px.

Sixteen new regressions cover same-control/no-journal offsets; cross-control posting/reversal; normal bank release/failure/export isolation; exact paise, balanced signs and source dates; period closure; returned bank backing; pending POD corrections; module/finance/principal scope; delegated routing/rollback/actual actors; database bank/amount constraints; web actions; retained findings; earlier cutoff before later reversal; and standalone supplier debit-note/invoice offsets. Related finance/close/delegation/handoff tests also passed. Django, applied-migration/drift and diff checks passed. These checks use isolated fictional data; no live bank, payment, offset or staff grant was created. PostgreSQL concurrency and company acceptance remain pending.
