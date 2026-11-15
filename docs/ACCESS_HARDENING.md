# Complaint, CAPA and quotation access hardening

15 November 2026. This milestone addresses the access gaps found during the SRS audit; it does not complete the SRS or production acceptance.

Quotation services now check site assignment when creating, editing, submitting, approving, sending, recording outcomes, revising and converting quotations. Editing locks and reloads the stored quotation before checking its original site and draft state. A caller cannot move another site's quotation into an assigned site to bypass authorization. CRM prospect site access is checked before saving.

Invoice-backed complaints follow the invoice site's access. The boundary applies to creation, investigation, decision, settlement, closure and linked CAPA actions, as well as lists, detail pages, KPI counts, CSV drill-downs and work queues. Replacement orders require site access. Complaint audit events retain the invoice site. Forged/inaccessible invoice form selections fail validation instead of silently creating an unlinked complaint.

Unlinked complaints have no reliable site on the current schema: visibility is restricted to their creator, assigned owner and active superusers. Standalone CAPAs use the same creator/owner restriction. Module permission or company-finance permission alone does not grant every site's operational records. A CAPA owner's identity does not bypass the linked complaint's site boundary. Web actions also require the CAPA to belong to the complaint in the URL. Existing module permissions and independent-approval rules remain required.

No schema migration, role grants, OTP change or business-data backfill is required. Records are retained. Where an unlinked case needs an independent reviewer outside its current visibility, an administrator must handle the review; a dedicated scoped case-team/reviewer model remains future work.

Regression coverage in `erp.test_access_hardening` includes denied direct service calls, denied HTTP reads/writes, no-write checks, parent substitution, unlinked-case privacy, scoped reports/queues and valid same-site operations. Verification uses fictional records and isolated test databases.

Remaining rollout work includes broader module-by-module authorization review, PostgreSQL concurrency/load testing, sensitive-action step-up authentication and company security/business UAT.

Verification: Django system checks and migration application/drift checks passed. The final full suite ran 404 tests: 396 passed and eight opt-in browser tests were skipped. Authentication, core business and project browser tests were also run explicitly: all three passed. No live records, emails or external financial calls were created by these tests.
