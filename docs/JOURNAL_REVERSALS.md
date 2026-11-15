# Independently reviewed manual journal corrections

SRS §3.2 maker-checker and FR-FIN-017 posted-document corrections now apply to the manual reversal workflow. Posted originals and their lines remain immutable. A reversing entry is created only after a retained request is independently approved.

## Request and review

On an original posted manual voucher, **Request reversal of this voucher** records a date, reason, requester and browser retry key. Existing company-finance, journal read and journal-create permission are required; requesting does not require approval authority and creates no posting. The date cannot precede the original voucher and must be open at request time. Matching retry keys return the same request; changed details with the same key fail. Only one pending/approved request can exist per original. The source row lock and database constraint preserve that rule.

A qualified independent reviewer can approve/reject using existing direct or valid delegated journal approval. Requester and original voucher maker cannot decide directly or lend authority through a deputy. The source/date/reason are fixed; review records a finding and actual reviewer/time. Approval uses the existing balanced, source-keyed ledger engine, retaining requester as reversal creator and actual reviewer as its approver. The request links the original and the exact posted reversal. Repeated/concurrent approval cannot create a second reversal.

The posting period is checked again during approval. A period closed after proposal, invalid evidence or posting failure rolls back the decision, reversing entry and delegated-use audit together; the original request remains pending for legitimate review/cancellation. Rejection creates no reversal and retains the reason. The requester, or an administrator within company-finance scope, can cancel a pending request with a reason. Rejected/cancelled history is retained and a new request may be prepared with a new key.

## Source and authority boundaries

Requests apply only to original posted manual vouchers without a system source or reversal parent. System postings must be corrected through the source business document; carry-forward entries retain the year-end reopening workflow. An already reversed voucher or a reversing entry cannot create another manual reversal request. The web request identifier must belong to the journal in the route.

The former direct manual `gl.reverse` call now refuses manual vouchers and directs callers to the independent request service. Automatic source-document corrections still use their controlled source services. The private ledger primitive is called by the approved manual workflow after authorization; it is not a general web/API action. Future APIs must call the same request/decision services, not expose ledger primitives.

Recent identity confirmation and CSRF protect request, approval/rejection and cancellation. No posted form is replayed after reauthentication. Action/view/company-finance permissions remain separate. Named approval routing and My Work include pending reversal requests under the existing company-finance journal policy. A qualified configured backup may receive unavailable staff's assigned review; reassignment never posts a reversal. Inbox work completes when its request leaves pending, while assignment/history remain retained.

## Migration and verification boundaries

`erp.0049` adds reversal requests with retained source/actors/reason, retry identity, review and reversal link. Before local application, a SQLite backup and row-count inventory were saved outside Git. All 132 existing non-metadata business/account table counts remained unchanged. No live request, source edit, reversal, staff assignment, grant or financial posting was created. Earlier reversing entries remain unchanged; historical requests/approvers are not fabricated. Expected metadata changes are the new model content type/default permissions and migration entry; no role reset was run.

Run `python manage.py test erp.test_journal_reversals erp.test_gl erp.test_finance_controls erp.test_closing accounts.test_handoff accounts.test_delegation erp.test_insights` and `RUN_BROWSER_TESTS=1 python manage.py test erp.test_journal_reversal_browser erp.test_finance_browser`. Fixtures are fictional and isolated. PostgreSQL race/load validation, company finance reconciliation/UAT, remaining approval inventory, API/mobile and the wider SRS remain work.

Verification on 15 November 2026: full suite 565 tests, 549 passed and sixteen opt-in browser checks skipped. All 99 related ledger/close/finance/delegation/handoff/report tests passed, including ten new reversal regressions for no-post requests, key retries, balanced single posting, original/request maker and proxy denial, actual deputies, closed-period rollback, rejected/cancelled history, blocked direct manual reversal, source/date/finance/account scope, backup routing and source-bound protected HTTP decisions. Both affected reversal/finance browser checks passed explicitly. The new browser flow completes requester proposal → independently assigned deputy approval → one linked reversal at 320/390/768/1440px without overflow/browser errors. Django, applied-migration/drift and diff checks passed. Developer evidence does not certify PostgreSQL concurrency or company finance acceptance of the whole SRS.
