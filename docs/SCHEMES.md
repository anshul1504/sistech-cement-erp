# Dealer incentive rules and calculation previews

Step 8b, stage 1: versioned commercial rules, independent rule approval and retained eligibility calculations. This stage does not approve entitlements, accrue a liability, create GST documents, issue payments or post accounting entries. Payout batches and post-payout adjustments are the next stage.

## Rules and approval

Open **Sales → Schemes & incentives** (`/schemes/`). A scheme has one fixed code, name and plant/depot. Each version defines an inclusive validity period, optional product/customer/type/district/territory scope, calculation basis, sales slabs and optional maximum incentive per dealer.

All specified scopes must match. Product scope uses invoice-line product IDs. Customer type, district and territory use the **current customer master at calculation time**, because historical invoice records do not snapshot these classifications. District/territory matching ignores case and repeated spaces. A selected customer must match the selected type/district/territory when rules are saved/submitted/approved. Existing inactive customers and products remain available for retrospective commercial review; historical invoice sales are not silently excluded because a master was later deactivated.

Provide 1–20 distinct nonnegative sales thresholds with percentages from 0 to 100. Slabs are ordered by threshold. The highest achieved threshold selects a percentage on the **whole eligible base per dealer**; these are not progressive/marginal bands. No matching threshold means zero incentive. Amounts round half-up to paise. An empty dealer cap means no cap; zero means no incentive can accrue in the preview.

Save a draft, then submit with supporting notes. A different authorized person reviews and approves or rejects with a finding. Every contributor, earlier editor and submitter is excluded from either decision, including superusers. Submitted and decided rules cannot be edited. Rejected rules remain available and can be copied into a new draft.

Only one draft/submitted version may exist in a scheme family. **Prepare next version** copies the latest version and slabs; after an approved period it proposes the next period. Dates must be later than and non-overlapping with the family's approved periods. Approved rules are not silently shortened or replaced. First-time approval may cover a past commercial period, so an approver must assess retroactive terms. The approval is a manual commercial decision.

Different scheme families are **additive**. This stage has no mutual-exclusion, priority or automatic de-duplication between families. A rule reviewer must assess overlapping campaigns. Multiple calculations are alternative retained estimates, not multiple earnings.

## Calculation definitions

Choose invoice dates within an approved version, with an end date no later than today. The chosen range is the threshold measurement period. A partial/custom range evaluates thresholds over that range only; it is not prorated to the full scheme period. Use the full intended commercial period for a final estimate.

For each matching customer and invoice:

1. Select invoices at the scheme's site dated inside the chosen range. All-product rules use invoice taxable totals; product-specific rules use only matching invoice-line taxable values.
2. Subtract credit notes against those invoices dated through **today**, including returns/discounts issued after the invoice measurement period. Product-specific rules subtract matching credit-note lines. A credit note with nonzero taxable value and no line allocation blocks a product-specific calculation; the engine does not guess an allocation. Net taxable base per invoice cannot go below zero. GST, invoice round-off and freight outside the selected taxable goods base are not incentive base.
3. **Net taxable sales** mode includes this net amount regardless of collection status. **Fully settled net taxable sales** mode includes it only when current posted-receipt and credit-note allocations settle the **whole invoice**. This is a full-settlement gate, not proportional collection commission. A mixed-product invoice must be fully settled before any selected product qualifies under this mode. Current unallocated advances do not qualify; bounced receipts do not qualify. No payment is inferred merely from a credit master limit or declared security deposit.
4. Pool eligible amounts per customer, choose the highest achieved slab, apply its percentage and then cap the amount if configured. Customers do not share threshold volume; sites and independent versions do not share threshold volume.

Example: ₹1,000 taxable sale, ₹100 taxable return, 1% slab from ₹0 and 2% from ₹1,000 gives ₹900 eligible base and a ₹9 preview. GST does not increase it. Fully settled mode shows zero until the invoice balance has been settled.

This is a **current-source calculation**, not a reconstruction of a historical settlement position. Receipt allocations can later change after returns, bounce or reallocation. Returns after a retained preview do not mutate the saved amount. Prepare another preview to see their effect. Prior-period invoices outside the selected range are not added simply because they were collected during it.

## Evidence, exports and retries

Each successful calculation stores its engine version, commercial-rule snapshot/slabs, period, observation date, customer classification, invoice/product evidence, credit-note deductions, current allocation evidence, chosen rate, cap, per-dealer amount, total and SHA-256 source fingerprint. It records no bank accounts/UTRs in the snapshot. **Check current sources** compares current evidence against the saved fingerprint and reports whether a fresh calculation is needed; it never rewrites the saved snapshot.

The dealer breakdown is paginated at 20 customers; each on-screen breakdown shows up to 50 invoices. Totals cover every matching invoice. CSV exports the full dealer summary with formula-like text escaped. JSON downloads the complete retained evidence, including all source rows. Both require export permission, are audited and served as private attachments. A UUID submission key makes a repeated form POST return the same retained calculation; a different request/user cannot reuse the key. A fresh form intentionally creates a new preview.

The source fingerprint can also change because descriptive customer/product data or allocation evidence changed, even where the final monetary amount is the same. It proves source equality, not external correctness or authorization of a payout.

## Permissions and upgrades

Non-superusers need assigned sites for all rule/evidence reads and writes, including service calls, exports and retries. Scheme visibility is site-scoped, not salesperson-owner-scoped. Calculation previews contain dealer commercial amounts and have a separate explicit view permission; they are not included in generic operational view presets.

The role matrix adds:

| Module | Actions |
| --- | --- |
| Incentive scheme rules | View, Create, Edit draft, Submit, Approve/reject |
| Incentive calculation previews | View, Create calculation/check sources, Export |

Fresh **Sales manager** presets can prepare/submit rules and calculate/export estimates at assigned sites. Fresh **Finance controller** presets can independently review rules and calculate/export. **Internal auditor** can read/export but cannot change rules or calculate. Existing customized groups are preserved by `setup_roles`; a super administrator must explicitly grant these new rows and confirm site assignments. `setup_roles --reset` deliberately overwrites named presets and is not required for this upgrade.

Migration `0030` adds scheme tables only. It does not seed live campaigns, backfill rewards, change customer credit or affect existing invoices/journals. Take a database backup, run `migrate`, then `setup_roles` to create any missing preset groups. Never commit the database, credentials or browser screenshots.

## Verification and remaining work

`erp.test_schemes` covers draft atomicity, slab validation/rounding/caps, contributor independence, immutability/revisions, date/site/module restrictions, product/customer/district/territory matching, line-specific credits, post-period returns, whole-invoice settlement, advances/bounce, per-dealer isolation, zero results, retries, snapshot drift, HTTP forms, private JSON/escaped CSV and absence of financial side effects. The opt-in browser check covers six screens at 320/390/768/1440px and a collapsed desktop sidebar, local Poppins, overflow/JavaScript errors, slab addition/save, independent approval, calculation, retry and source check.

```sh
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
RUN_BROWSER_TESTS=1 python manage.py test erp.test_schemes_browser erp.test_crm_browser erp.test_business_browser erp.test_finance_browser accounts.test_browser
```

Pending: independently reviewed accrual/entitlement and payout batches; unique earning-period allocation across previews; beneficiary/tax decisions; journal posting and approved post-payout reversals/recoveries; overlapping-campaign exclusion; quantity/bag/MT and marginal slab schemes; salesperson/influencer commissions; immutable historical territory assignments; cancellation/early termination of approved campaigns; notifications and dealer portals. No scheme liability should be recognized from this preview alone.

PostgreSQL concurrency/load validation and business policy/UAT remain necessary. SQLite regressions establish sequential retries/rollback and source calculations, not production multi-user snapshot isolation. Preview snapshots use current transactional sources; a source check or fresh calculation is required before the future payout stage. There are no external banking, tax or messaging calls.

Developer verification (14 November 2026): `manage.py check` passed and migration drift check reported no changes. The full suite ran 302 tests (297 passed; five opt-in browser tests skipped); all five browser tests also passed explicitly. A backup outside the repository preceded local migration `0030`; existing customized roles were preserved and no live campaign or sample business data was inserted.
