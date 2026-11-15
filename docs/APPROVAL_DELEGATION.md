# Dated approval delegation

SRS §3.2: approval delegation must have start/end dates and retained audit history. This milestone adds explicit authority windows for the currently configured risk actions; it does not finish all access-control or SRS acceptance work.

## Administration and use

**Administration → Approval delegations** lets a super administrator record or revoke a grant. **My account → Approval authority** lets principals and deputies see their own authority history. Creation/revocation requires recent identity confirmation on the web, in addition to normal login/email OTP. A reason, distinct people and an end time are required; blank start means now. Times are entered/displayed in Asia/Kolkata. Windows are inclusive at start and exclusive at end.

A grant identifies one principal, deputy, approval action and site (or company-finance scope). Overlapping windows for the same deputy/action/scope are rejected. It adds no role, module read permission, site assignment or company-finance scope. The principal must already hold the direct action authority. A deputy cannot pass delegated authority on to another person.

Every use rechecks current dates, revocation, enabled/unexpired accounts, the principal's direct authority and the required scopes. A principal's removed permission/site, or either person's expired access, stops the delegation without a scheduler. Finance actions require existing company-finance access for both people. Site actions require existing assignment to that site for both people. Direct authority remains governed by each business workflow's existing controls.

The actual deputy remains the document's approver/releaser; the application never signs in or posts as the principal. Both people are checked against the workflow's maker identities for delegated approval. Delegation cannot approve a transaction made by its principal or deputy. Ordinary maker-checker, credit/price limits, source reconciliation, stock, closed-period and status checks still apply.

A successful delegated operation retains an ApprovalDelegationUse and an audit event with grant, principal, actual deputy, action and source reference. Failed business transactions roll back the use/audit together with the business writes. Revocation retains all earlier uses. Active windows are not edited in place; revoke and record a new window if cover changes.

## Configured actions

| Site scope | Extra company-finance scope |
|---|---|
| Sales and purchase order approval; high-value purchase approval | No |
| Sales price exception; quotation discount approval | No |
| Sales credit override | Yes |
| Damage disposition; stock count adjustment approval | No |
| Scheme entitlement accrual approval | Yes |

Company-wide grants (both people must already have company-finance scope): supplier payment approval/release, scheme payout approval, manual journal approval, month close/reopening and year close/reopening.

Base sales approval does not automatically include below-list or credit-override authority. Each exception needs its own direct permission or valid separate delegation. High-value purchase authority remains separate too.

Scoped detail buttons, payout controls and My Work approval queues use the live authority policy. Source-document visibility is still required. Participation in a grant does not reveal financial/business references from its use history: those references are shown on the delegation detail only to super administrators. A grant's **In window** label describes dates, not a promise that its people and transaction still qualify.

## Verification and upgrade

Migration accounts.0006 adds retained grants and uses; existing users, roles, site assignments and business documents are not backfilled or reassigned. A SQLite backup was saved outside Git before migration. Counts for 119 existing account/business tables remained unchanged. No live delegation, financial transaction or role grant was created during verification.

Regression coverage includes exact date boundaries, overlapping windows, unauthorized creation, principal/deputy scope loss, disabled/expired principals, non-transitive authority, separate price/credit/high-value grants, maker-checker denial, revoked windows, queues, actual approving user, closed-period rollback, and successful quotation/stock/payment/journal/scheme/month/year workflows. The real browser flow creates/revokes a grant after password/email-code login and checks list/form/detail at 320/390/768/1440px.

## Remaining acceptance / scope

The original milestone covered the fifteen risk actions above; the subsequent site integrations are described below. CRM onboarding and freight were integrated subsequently below; complaint/CAPA were integrated subsequently below; recognition approval integration is described below. Arbitrary permissions, financial read scope, payment-file export, master edits and login authority cannot be delegated through this feature.

Reassignment/escalation of pending work when people become unavailable remains separate work. A disabled principal cannot lend authority; an administrator must arrange independent direct authority or an eligible active principal. Broader dimensional access, API/mobile enforcement, PostgreSQL concurrency/load validation and company security/business UAT remain pending. Do not use SQLite sequential tests as evidence of production races.

Verification on 15 November 2026: final full suite 443 tests (435 passed; eight optional browser checks skipped). All eight browser checks passed explicitly; after the final button/authority refinements the three affected authentication/finance/scheme browser checks also passed. System and migration application/drift checks passed. The 19 delegation regressions are included in the full suite.

## Additional site workflows

The configured inventory now includes 25 action types. Added site-based actions are purchase request review, RFQ award, inbound quality release, production quality release, customer-return approval, beat-plan approval, manufacturing supply-rate approval, incentive-rule approval, supplier-invoice exception review and stock-transfer approval/discrepancy resolution.

Each integration retains its own independence/evidence checks. Scheme and beat contributors cannot lend authority to their own reviewer. Beat delegation additionally requires existing team-beat visibility for both principal and deputy; it does not grant team access. Transfer dispatch approval uses the source site; discrepancy resolution uses the destination site, so one site's grant does not cover both.

RFQ award creates a new PO rather than merely deciding an existing document. The retained award use identifies its principal as a maker of that PO. Both direct and delegated PO approval, its detail button and its approval queue exclude that principal. Revoking or expiring the award grant does not erase this historical maker relationship. This prevents delegated creation from disguising a maker as an independent checker.

Manufacturing-partner supply-rate writes now enforce site access in the service. Candidate plants, pending contracts, scorecards and sourcing-advisor rows are also scoped to assigned sites; an inaccessible contract action returns 404 on the web. Trusted internal read helpers retain optional site filters, which every current sourcing web entry point supplies.

CRM onboarding, freight, complaint/CAPA and recognition are integrated below. Unavailable-staff work reassignment remains separate. These outstanding items must not be inferred complete from the expanded configured list.

The transfer's original creator is also excluded from shortage resolution, alongside dispatch/receipt participants, both directly and through a deputy. Final verification for this extension: full suite 452 tests (444 passed; eight opt-in browser checks skipped), all eight browser checks passed explicitly during the extension, and 61 affected workflow/queue/maker regressions passed after the last stock-loss rule. The suite includes 28 delegation tests. Django and migration application/drift checks pass; this extension needs no new schema migration or role changes.


## Onboarding review integration

The configured inventory now includes 28 action types: dealer KYC, dealer credit and final activation add three site-scoped actions. Both principal and deputy need existing dealer-application read and team-CRM visibility, existing source-site access and enabled/unexpired accounts. No read, site, document, financial or role access is granted by delegation.

Current/former owners, profile/evidence contributors, submitters and ownership-change actors remain makers. Credit review excludes both the preceding KYC reviewer and any retained KYC delegation principal; final activation excludes the preceding credit reviewer and retained credit delegation principal. Revoking/expiring the earlier grant does not erase those identities. Each use references the application number, submission revision and lane, so returned/resubmitted evidence starts a new review revision without rewriting old history.

The actual deputy remains the reviewer and customer activation actor. Business snapshot checks, requested credit/term bounds, prior stage, documents and maker-checker remain enforced. Failed business validation rolls back the delegated use together with the decision. Buttons, My Work and routing use the same eligibility checks without consuming authority. Review/return/rejection web writes require recent identity confirmation.

Stage-specific accountable routing now supports all three lanes. A stage transition removes the completed assignment from pending inboxes without deleting its routing/history. Disabled staff can hand off an explicitly routed review only to an existing qualified independent backup; a deputy whose principal became unavailable is ineligible. Unassigned shared-role work is not fabricated into historical named assignments.

This extension needs no schema migration or local policy/role changes. Remaining approval inventory, batch staffing orchestration, deployment scheduling, API/mobile and production/security acceptance remain separate work.


Onboarding verification on 15 November 2026: full regression discovery ran 509 tests (498 passed; eleven opt-in browser checks skipped). The final eleven onboarding regressions passed separately, including three added after full-suite discovery and retained-principal exclusion after revocation. Both affected CRM browser checks passed explicitly; the new check completes assigned-inbox → delegated KYC → stage completion at 320/390/768/1440px without overflow or browser errors. Django check, applied-migration/drift checks and diff checks passed. Reproduce with `python manage.py test erp.test_crm_approvals erp.test_crm accounts.test_handoff accounts.test_delegation erp.test_insights` and `RUN_BROWSER_TESTS=1 python manage.py test erp.test_crm_approval_browser erp.test_crm_browser`. No live grant, routing, customer activation, role or schema change was made. PostgreSQL concurrency, company UAT and the remaining SRS are not certified by these developer checks.


## Consolidated freight review

The configured inventory now includes 29 action types. Freight-bill approval/rejection adds a company-scoped action because one bill may contain trips from several sites. Both principal and deputy require existing freight-bill read and explicit company-finance scope; no site/role/financial scope is granted. Existing direct site checkers retain the rule that every trip must be in their site scope; named central routing/delegation uses explicit company-finance coverage. Bill makers cannot approve directly or through a deputy. Uses reference the freight bill identity, retain the actual checker and roll back on claim/ledger failures.

See [freight approval evidence](FREIGHT_APPROVALS.md) for retained rejected claims, migration and verification. Remaining approval inventory, cross-module auditing, production scheduling/acceptance and the rest of the SRS remain pending.


## Complaint decisions, closure and CAPA effectiveness

The inventory now includes 31 configured action types. Complaint decision/closure and CAPA verification add two site-scoped permissions. Both parties require existing complaint read access; CAPA verification additionally requires CAPA read. The grant's site is the complaint invoice site, including a CAPA linked to that complaint. Neither a grant nor routing broadens case visibility. No site-scoped grant is invented for unlinked cases/standalone actions; authorized direct reviewers retain the existing personal/super-administrator scope, subject to independence.

Complaint creator, current/former owners and actual investigators are excluded from deciding or approving closure, directly and through deputies. Investigation-form owner changes now retain the old owner identity, supplementing retained staffing-handoff evidence. CAPA creator, current/former owners and actual implementers are excluded from verification and proxy verification. Existing completed reviews remain retained; the stronger maker check applies to new decisions.

A routed closure is pending only after required credit/replacement links and CAPA effectiveness are ready; a documented no-CAPA waiver remains a closure decision where permitted. Decisions, closure and effectiveness/reopening writes require recent web identity confirmation. Failed evidence validation rolls back delegated use and state together. Standalone CAPA now has a scoped detail/history/implementation/review screen rather than a dead register link. Remaining approval inventory, batch staffing orchestration, broader dimensional permissions, API/mobile and production/security acceptance remain work. See [complaint/CAPA verification](COMPLAINT_APPROVALS.md).


## Recognition review integration

The inventory now includes 33 configured action types. Verified influencer usage and recognition winner publication add company-recognition actions with their existing module read boundaries, rather than an invented site or finance grant. Both principal and deputy need the relevant existing read permission. Usage creators cannot verify directly or by proxy. Programme creator, current ranker and all retained rank contributors cannot publish or send back their own ranking directly or by proxy. Read-only buttons/queues do not consume authority; actual deputy identity and canonical usage/programme references are retained on a successful decision.

Delegated publication authority does not grant reward-delivery action authority. Delivery keeps the existing direct role, records the actual user/time and preserves its evidence on retries; a conflicting note is rejected. Usage verification, publication/sendback and reward-delivery web writes require recent identity confirmation. See [recognition control verification](RECOGNITION_APPROVALS.md) for migration, retained historical identity boundaries and remaining scope.


## Recipe and dispatch weight-exception integration

The inventory now includes 35 configured action types. Recipe/BOM approval is company-scoped within existing recipe read access. Weight-exception approval is site-scoped within existing dispatch read access. Both principal and deputy must retain the required read/action/scope and enabled/unexpired accounts; no module/site grants are added.

Recipe creators cannot approve directly or lend approval authority through a deputy. Weight review excludes dispatch creator, loader and every retained tare/gross operator, including superseded readings. Reweighing invalidates the old approval. A submitted web approval must match the current tare/gross reading IDs; missing/stale evidence fails before consuming delegated authority. Each use references the actual reading pair. A decided exception cannot be approved again to overwrite its finding. Read-only source buttons and queues share the maker checks.

Recipe approval requires active output/materials, valid basis/yield and finite positive material quantities/non-negative tolerances. Product-first locking serializes recipe version allocation and approval/retirement. Process orders retain their original recipe version. Both new risk approvals require recent web identity confirmation. See [operational approval verification](OPERATIONAL_APPROVALS.md). Other approval inventory, broader dimensional access, API/mobile and production acceptance remain work.


## Manual journal reversal requests

Original posted manual journals now use a request → independent review → linked reversal workflow. The existing company-finance `approve_journalentry` policy covers approval/rejection of a reversal request; no new general permission or role grant is introduced. Requester and original voucher maker are retained as makers for direct/delegated review. The actual deputy is request reviewer and reversal approver; use references identify `REVREQ-{id}`. A reversal creator remains the requester. Closed-period or evidence failures roll back use and posting together. Routing targets the pending request and links back to its original journal, not an unrelated request ID interpreted as a journal ID. See [manual reversal verification](JOURNAL_REVERSALS.md).


## POD evidence correction review

The current inventory contains **36 configured action types**. `approve_pod_correction` is a dedicated site-scoped permission with existing dispatch read access for both principal and deputy. It does not reuse freight finance authority or grant plant access. The role matrix exposes approval alongside POD recording; the default Logistics manager template includes it for future template application. This milestone does not change existing staff/group grants.

Requesters, original POD recorder, dispatch creator and creators of previously approved POD corrections are makers. Legacy original POD audit actors are included where retained; unknown original attribution stays unknown. Neither direct authority nor a principal/deputy grant can override these exclusions. Request creation changes no operational or financial source amounts. A successful independent approval retains the actual actor/time and the current evidence digest; any audit/source failure rolls back both the correction and delegated-use receipt. Source routing, eligible unavailable-staff handoff and My Work use the same stage and maker rules.


Non-cash offsets use the existing payment approval/release stages, retaining the 36-action inventory. Current payment read and company-finance scope are required for payment approval/release principals and deputies; zero-cash execution/reversal also require current module read access. Maker exclusions, actual deputy receipts, atomic rollback and existing routing/handoff apply. No bank or UTR authority is invented for the offset mode. See [non-cash settlement](NON_CASH_SETTLEMENT.md).


CRM duplicate prospect consolidation adds `approve_crmmerge`, bringing the configured inventory to **37** action types. Principals/deputies need existing CRM read, team visibility and source-site access. Requester and retained/current identity/conversation/change contributors remain makers; routing does not grant visibility. Actual reviewer and atomic delegated-use receipts are retained. Existing staff roles are not automatically changed. See [CRM consolidation](CRM_DUPLICATE_MERGE.md).
