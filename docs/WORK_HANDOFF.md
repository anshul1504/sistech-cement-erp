# Approval routing and unavailable-staff work handoff

SRS §3.2 requires disabled users to lose access and their pending approvals to be reassigned. This milestone adds accountable approval routing, explicit backups, retained work handoff and a current exception view. It grants no role, site, finance access or approval result.

## Review and routing

**Administration → Approval routing** lists pending source stages from the configured approval catalog. A super administrator can assign an accountable reviewer, with a reason, only if that person currently has:

- an enabled, unexpired account and existing module read access;
- the necessary source site and company-finance scope;
- direct or valid delegated action authority;
- independence from source makers, contributors and retained delegated RFQ makers;
- required price/credit/high-value exception authority where relevant.

Routing is nonexclusive: another qualified independent checker can still decide the source. An assignment never submits, approves, releases stock, pays money or changes a transaction value. There is no invented backfill of historical assignees: existing unassigned approvals remain shared-role work until explicitly routed.

**My account → Assigned approvals** and My Work show only pending assignments the user is still eligible to review. Sources that leave the pending stage disappear from that inbox; routing/history is retained. Authority is rechecked by the normal business service when an actual decision is made.

The catalog covers the currently configured order/quotation, PR/RFQ, stock transfer/count/damage, inbound/production QC, AP/payment, journal/close, scheme, supply-rate, RMA and beat approval stages. Open months without a prepared review and RFQs without current quotes are not presented as pending approval work. Onboarding, freight, recognition and complaint/CAPA decision integrations still require their resource-specific approval work.

## Previewed handoff

**Transfer pending work** previews current work for a source owner and selected recipient. The preview is bound to both people, pending state and eligibility. Changed work or a different recipient requires a fresh preview. Replay keys prevent duplicate mutations and reject reuse for a different payload. Receipts retain what moved and what needed attention.

Eligible open work can transfer for:

| Work | Preserved evidence |
|---|---|
| Accountable approval routing | Original document, maker, approval status, amounts and source evidence |
| Open/in-progress/waiting support tickets | Raiser, messages/authors, response/due dates, status and resolution evidence |
| Open/investigating/decided complaints | Original creator, findings/investigators, decision and settlement links |
| Open/reopened CAPA | Existing action/evidence and due dates; implemented/effective records are not reassigned |
| Open sales projects | Original creator, value, source links and prior project-event snapshots |
| Unresolved collections work | Original contact author, promise amount/date, dispute and receipt-derived status |

A recipient needs the existing module/action permissions and site/finance scope. Changing ownership does not copy permissions. Former complaint owners/investigators and CAPA owners/implementers remain disqualified from deciding/verifying their own work after a transfer. New CAPA audit references use a bounded stable ID; legacy references are still checked when determining historical implementers.

Closed/resolved/effective/won/lost records and posted stock/financial documents are not rewritten. Submitted CRM ownership is part of KYC evidence, and beat ownership is tied to dated dealer assignments and approved routes. Those items are explicitly retained as **Needs attention** for a module-specific reviewed ownership change; this feature does not silently invalidate their evidence or claim that automatic CRM/beat handoff is complete.

## Disable, expiry and backups

A super administrator can select **Work backup** on the member form. It must be a different active user with unexpired access; qualification is checked again per item at the time of handoff.

After an account disable or expiry commits, the configured eligible backup receives pending assigned work. Account revocation never waits for a successful handoff. No backup or an ineligible backup produces a retained attention receipt; it does not grant authority or pick an arbitrary administrator. The board shows current unavailable owners with pending work separately from historical receipts.

A user-save hook covers the main administration and Django-admin save paths; bulk database updates can bypass signals. Schedule these commands in the deployment environment:

```sh
python manage.py expire_temporary_access
python manage.py reassign_unavailable_work
```

The second command retries using current explicitly configured backups. Unchanged failed work/eligibility reuses its receipt; a repaired backup/scope yields a new attempt. No pending work produces no automatic empty receipt. These commands are supplied; a host scheduler has not been installed automatically.

Automatic events are attributed to **System**, not the recipient or disabled user's identity. Source-event references and source/recipient IDs are retained. Exceptions remain visible for administrator action; the software cannot supply missing business authority or choose real staff on the company's behalf.

## Data, authorization and locking

Handoff records contain source identity/state, ownership and a content digest; they do not duplicate passwords, bank details, uploaded files or long descriptions. Full receipts and cross-company routing are super-administrator-only. Participation/assignment alone does not grant access to the administration history or a source module.

Writes require recent web identity confirmation and CSRF. Business sources lock before routing rows; a separate per-source handoff guard avoids coupling work moves to credential/profile revocation locks. Recipient availability is checked under a user lock. Ownership changes, retained item receipts and audit events are one transaction; stale previews or unexpected failures roll back the batch. Partial results concern explicit eligibility exceptions, which are retained rather than silently skipped. PostgreSQL race/load certification remains a rollout gate, not something proven by SQLite.

Migration accounts.0007 adds an optional backup and routing/handoff tables. A local SQLite backup was created outside Git before application. Counts for 121 existing account/business tables remained unchanged afterward. No live staff backup, assignment, handoff or financial posting was created during developer verification.

## Remaining SRS work

- CRM lead edits now support reasoned ownership changes with retained onboarding evidence and mandatory resubmission. Automatic CRM handoff remains deliberately blocked pending explicit module review. Approved beat execution cover now handles outstanding visits/follow-ups with independent review and expiry; active qualified cover suppresses unavailable-owner attention until it expires or loses access. Explicit preview-bound dated dealer ownership transfer now creates replacement assignment/route lineage and requires renewed route review. Automatic backup handoff does not silently perform either ownership workflow. Complete remaining approval-type routing and batch staffing orchestration.
- Configure the company's real backup coverage and operational escalation rules.
- Verify PostgreSQL concurrency, deployment scheduling/monitoring and business/security UAT.
- Apply the same authorization/availability contract to the future API/mobile/background approval stack.

This milestone does not complete the whole SRS or certify production acceptance.

Verification on 15 November 2026: the final full suite ran 471 tests (463 passed; eight opt-in browser tests skipped). All eight browser checks passed explicitly during this milestone; after the final preview/catalog refinements the three affected authentication/finance/beat browser checks passed again. The suite includes 19 handoff tests, including actual disable/expiry callbacks, replay/stale-preview denial, private inboxes, retained investigator independence, equal-authority recipient changes and source-stage screens. Django and migration application/drift checks passed.
