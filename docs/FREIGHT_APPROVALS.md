# Consolidated freight review and retained claim evidence

SRS §3.2 and FR-LOG-013/014: transporter bills retain contracted freight, capped allowed extras and shortage recovery, with independent review before becoming payable.

Freight bill detail, My Work and the approval-routing catalog now support the same independent review eligibility. A site-only direct checker must cover every trip in the consolidated bill. Named central routing and dated delegation require explicit existing company-finance and freight-bill read scope. A company-scoped grant covers a consolidated bill, rather than pretending that one site grant covers a multi-site claim. Both principal and deputy must remain enabled/unexpired and qualified. Grant creation does not add roles, read access, sites or finance scope.

The bill maker cannot decide directly, be routed as an independent reviewer or lend approval authority through a deputy. The actual deputy remains `checked_by`; retained delegation use references `freight:{bill_id}`. Contract caps, detention/unloading caps, shortage recovery, advance deductions and variance findings remain unchanged. Approval/rejection web writes now require recent identity confirmation as well as CSRF and ordinary access checks.

Approval posts once through the existing source-keyed balanced ledger. Failed variance validation or posting rolls back the decision, its snapshot and delegated use. A repeated decision cannot post/consume authority twice. Routing is accountable and nonexclusive: it does not itself decide, grant permission or post money. A qualified configured backup can receive a disabled reviewer's pending assignment; stage completion removes it from pending inboxes while retaining routing/history.

## Rejected-claim history

A decision now freezes the bill calculation and line-level trip identity/site, vehicle/customer display, contract/claim amounts, extra/recovery amounts, detention and reason. Rejection releases the live one-to-one trip links for a corrected bill, while retaining the original claim snapshot. Later corrected bills do not overwrite it. The rejected detail renders the retained line evidence. Approval likewise retains its calculation evidence independently of future rate/trip changes.

Rejected bills retain existing visibility rules: their creator and explicit central finance may view them; removing live trip links does not grant arbitrary site users access to the retained customer/claim data. Earlier rejected bills whose lines were already deleted have no trustworthy historical line data. Their snapshot stays empty and the screen states that evidence is unavailable; no backfill invents a past review.

Migration `erp.0047` adds the review snapshot field. A SQLite backup was saved outside Git before application. Counts for all 131 existing non-metadata business/account tables remained unchanged; no live review snapshot, approval, grant, staff assignment or ledger posting was created. Existing rows start with empty snapshots rather than fabricated decision evidence. Applied-migration and migration-drift checks passed.

## Verification and remaining work

Run `python manage.py test erp.test_freight_approvals erp.test_freight accounts.test_handoff accounts.test_delegation erp.test_insights`. Browser verification: `RUN_BROWSER_TESTS=1 python manage.py test erp.test_freight_approval_browser erp.test_business_browser erp.test_finance_browser`.

Developer checks use fictional data in isolated databases; no real payment, email, tax or hardware call is made. PostgreSQL concurrency/load validation, company freight/finance reconciliation and business/security acceptance remain required. Recognition and complaint/CAPA approval integrations, advanced freight/GPS/mobile scenarios and the remaining SRS are separate work.

Verification on 15 November 2026: full suite 521 tests, 509 passed and twelve opt-in browser checks skipped. The three affected freight/business/finance browser checks passed explicitly. The related workflow/delegation/handoff/report run passed 67 tests, including eight new freight regressions for actual delegated actors, caps, balanced posting, retained rejection/corrected claims, maker-principal denial, failure rollback, expiry/scope, multi-site visibility, qualified backup routing and recent identity confirmation. The new browser check covers assigned-inbox → delegated rejection → retained evidence and released trips at 320/390/768/1440px. Django, applied-migration/drift and diff checks passed.
