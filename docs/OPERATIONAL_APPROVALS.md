# Recipe and dispatch weight-exception approvals

The SRS §3.2 approval inventory audit identified two remaining configured direct-role decisions: recipe/BOM version approval and dispatch weighbridge exception approval. Both now support retained dated delegation and accountable routing through the same source-stage catalog, personal inbox and My Work.

## Recipe versions

Recipes are company-level product standards in the existing model. A grant requires the principal's direct recipe approval and both people's existing recipe read access; it does not add site or finance access. The draft creator cannot approve directly or through a deputy. The actual deputy is retained as `approved_by`; a use references `recipe:{id}`. Approval, version allocation and retirement acquire the output product lock before recipe rows. Source business services still create new drafts rather than editing an approved formula, and process orders retain their original referenced version.

Before approval, output and materials must remain active, at least one material must exist, basis/yield must be valid, material quantities must be finite and positive, tolerances finite/non-negative and a product cannot consume itself. Failed validation rolls back use and leaves earlier approval/retirement state unchanged. Future-effective recipe selection remains the existing workflow; PostgreSQL concurrent version/effective-date testing and company production acceptance remain required.

## Weight exceptions

A grant identifies the dispatch site and requires existing dispatch read for both parties, current site access and the principal's direct weight approval. Makers include dispatch creator, loader and every retained tare/gross reading operator, including superseded reweighs. Changing the last operator cannot make an earlier operator independent. Current and delegated reviewers are both checked against that maker set.

Only a weighed trip with a real undecided exception is pending review. An already approved exception cannot be approved again to overwrite its checker/finding. Reweighing retains its earlier readings and clears the approval through the existing assessment workflow. Each use references the dispatch and current tare/gross reading IDs. The web form submits that pair; stale or missing evidence fails before any delegated use or approval write. Readings themselves are not changed by approval. Required findings, plant tolerance/vehicle checks, invoice/gate guards and stock behavior remain the existing business workflow. Future API clients must also supply/review current evidence; no API is certified by this web implementation.

## Access, routing and verification boundaries

Detail/list buttons, routing and queues use the same independence/authority checks without consuming a grant. A qualified explicitly configured backup can receive a disabled reviewer's assigned review; routing never grants authority, approves a recipe or changes stock/readings. Stage completion disappears from the pending inbox but retains routing history. Company recipe scope has its own label rather than being described as finance scope.

Recipe and weight approvals require recent web identity confirmation and CSRF. A confirmation returns to the GET source screen; the original POST is not retained/replayed. This extension needs no schema migration, live grant/role/site/routing change, recipe approval, weighing or external hardware call. All verification fixtures are fictional and isolated.

Run `python manage.py test erp.test_operational_approvals erp.test_production erp.test_logistics accounts.test_handoff accounts.test_delegation erp.test_insights` and `RUN_BROWSER_TESTS=1 python manage.py test erp.test_operational_approval_browser erp.test_business_browser`. Broader approval inventory/cross-module audit, batch staffing orchestration, legal-entity/warehouse dimensions, production planning/costing/recall, APIs/mobile, hardware and business/security acceptance remain SRS work.

Verification on 15 November 2026: full suite 554 tests, 539 passed and fifteen opt-in browser checks skipped. After the final current-reading form/reference refinement, all 78 related operational/delegation/handoff/report regressions and both affected operational/business browser checks passed. Ten new regressions verify actual deputies, recipe version retention/retirement, maker principals, invalid formula rollback, authority expiry/read/site loss, all historical weighing makers, repeated/stale approval denial, reweigh invalidation, qualified backup routing and recent identity confirmation. Browser execution completes assigned weight approval → BOM approval → empty pending inbox at 320/390/768/1440px with no overflow/browser errors. Django, applied-migration/drift and diff checks passed. These developer checks do not establish PostgreSQL concurrency or company acceptance of the whole SRS.
