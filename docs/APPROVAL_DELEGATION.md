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

Other approval types (for example supplier-invoice exceptions, GRN/QC release, CRM onboarding, RFQ/PR, transfers, RMA and beat planning) remain direct-role actions until explicitly integrated and verified with this policy. Arbitrary permissions, financial read scope, payment-file export, master edits and login authority cannot be delegated through this feature.

Reassignment/escalation of pending work when people become unavailable remains separate work. A disabled principal cannot lend authority; an administrator must arrange independent direct authority or an eligible active principal. Broader dimensional access, API/mobile enforcement, PostgreSQL concurrency/load validation and company security/business UAT remain pending. Do not use SQLite sequential tests as evidence of production races.

Verification on 15 November 2026: final full suite 443 tests (435 passed; eight optional browser checks skipped). All eight browser checks passed explicitly; after the final button/authority refinements the three affected authentication/finance/scheme browser checks also passed. System and migration application/drift checks passed. The 19 delegation regressions are included in the full suite.
