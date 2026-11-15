# Complaint/CAPA independent review integration

SRS §3.2 and FR-QC-007/008 require controlled complaint/CAPA decisions, retained evidence and maker-checker. The approval catalog now includes complaint decision, evidence-ready complaint closure and CAPA effectiveness verification. Dated grants use the existing complaint decision and CAPA verification permissions at the complaint invoice's site.

## Authority, independence and scope

Delegation requires both people to have existing enabled/unexpired accounts, the source site and complaint read access; CAPA also requires existing CAPA read. The principal must retain direct review authority. The actual deputy remains the case decision/closure or CAPA verification actor; uses reference the complaint, complaint closure or canonical CAPA identity. No role, site or case access is added.

Complaint decisions and closure exclude creator, current/former owners and actual investigators. Investigation-form owner changes retain the previous owner in a structured audit event; unavailable-staff handoff receipts also retain former owners. CAPA verification excludes creator, current/former owners and actual implementers. Delegation principals and deputies must both be independent. Existing completed cases/verifications are not rewritten or backfilled.

Invoice-backed cases retain site scope. Unlinked complaints and standalone CAPAs retain the existing personal/super-administrator privacy boundary. A site grant cannot open an unlinked private case or manufacture a site for it. A direct reviewer still needs existing record access and independence; for a standalone action where the creator/owner are the only ordinary readers, independent administrator review is required under the existing scope. This milestone does not invent a broader quality-team access policy.

## Review and closure behavior

My Work, source buttons, routing eligibility and the decision service use the same historical-maker checks. Complaint closure work appears in the routing catalog only after any required credit note/replacement link and linked CAPA effectiveness are ready. Where an accepted quality complaint has no CAPA, its reviewer must still record an allowed waiver before actual closure. Closure never creates a financial credit or replacement order; the existing evidence must be linked through normal business services.

Accepted quantities reject negative and non-finite values and retain the claimed-quantity upper bound. A rejected complaint remains a zero-accepted/no-settlement decision. Verification can mark an implemented action effective or reopen it for more work; implementation/review audits remain retained. Decision, closure, effectiveness and reopening web requests require recent identity confirmation and CSRF. Failed quantity, settlement, waiver or effectiveness validation rolls back delegated use and state.

Accountable assignments are nonexclusive and do not grant authority or decide anything. A currently qualified independent configured backup can receive a disabled user's assigned review. Stage completion removes pending inbox work while retaining its routing/history. An ineligible or unavailable backup leaves explicit attention rather than arbitrary reassignment.

## CAPA detail and verification

`/capa/{id}/` now provides scoped action evidence, implementation, effectiveness, actual reviewer and retained history for linked and standalone actions. The open-CAPA register links to this screen. Linked complaint screens retain their existing forms with the same delegated/maker checks. The actual action owner or someone with edit permission can implement within existing scope; independent approval authority is not editing authority.

No schema migration, local role/grant/assignment, live case decision or external message was needed. The implementation uses existing audit/delegation/routing structures. PostgreSQL concurrency/load tests, company quality/customer-service acceptance, broader resource access dimensions, recognition integration, APIs/mobile and the remaining SRS remain pending.

Verification commands: `python manage.py test erp.test_complaint_approvals erp.test_complaints accounts.test_handoff accounts.test_delegation erp.test_insights erp.test_access_hardening` and `RUN_BROWSER_TESTS=1 python manage.py test erp.test_complaint_approval_browser erp.test_business_browser`. All fixtures are fictional and isolated from the local business database.

Verification on 15 November 2026: final full suite 533 tests, 520 passed and thirteen opt-in browser checks skipped. Both affected complaint/CAPA and business browser checks passed explicitly. The related workflow/access/delegation/handoff/report run passed 73 tests; after the final scope-label refinement, all 30 complaint-approval/handoff regressions and both browser checks passed again. The eleven new complaint/CAPA regressions cover actual deputies, creator/principal/investigator/implementer and retained-owner denial, negative/non-finite quantities, rollback, ready closure evidence, site/expiry/private scope, backup routing and recent identity confirmation. Browser execution completes assigned CAPA verification → delegated complaint decision → closure at 320/390/768/1440px without overflow/browser errors. Private records display their actual record scope rather than a finance label. Django, applied-migration/drift and diff checks passed; no migration was needed. These checks do not certify PostgreSQL concurrency or company acceptance of the SRS.
