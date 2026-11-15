# Reviewed duplicate prospect consolidation

SRS FR-CRM-002 requires authorized, audited duplicate handling. This milestone implements reviewed **lead/prospect consolidation**. Partner/customer-master consolidation, GSTIN/address matching across entities and automated duplicate resolution remain separate requirements.

## Preview and authority

From an unconverted lead, choose **Review duplicate merge**, select the canonical record at the same originating site and preview both identities, owners, opportunity values and resulting follow-up. A reason records the matching evidence. Selecting a target is a manual identity decision, not registry verification or automatic proof that two businesses are identical.

A requester needs existing CRM read, team visibility, lead-change and site access. The dedicated `approve_crmmerge` action is site-scoped and requires CRM read/team scope for principals and deputies. The role editor exposes it; dated delegation/routing use the existing framework. No staff grants are added automatically. The configured delegation inventory is now 37 action types.

Independent review excludes the requester and retained/current creators, owners and conversation/change contributors of both records. Proposal preserves a snapshot and fingerprint of both identities, activities and related work. Review rechecks the persisted records under the CRM policy mutex and ordered lead locks; changed identity, ownership, follow-up or dependencies require rejection and a fresh preview. Exact request retries return the retained request; conflicting retries and duplicate pending requests fail. Cancellation/rejection retain evidence without consolidating anything. Audit failure rolls back source changes and delegated-use receipts.

Converted/customer-linked sources require a separate master-consolidation workflow. Resolve active source onboarding, draft/pending/sent/accepted quotations, open projects and planned active route work before merging. Closed historical documents stay attached to their original lead IDs. Cross-site consolidation is refused; it must not bypass originating-site or ownership controls.

## Result and retained history

Approval sets the source's canonical link and **Merged duplicate** stage. The original mobile, name, notes, activities, applications, commercial references, creator and owner remain retained. The source becomes read-only for CRM mutation, onboarding and follow-up services. Canonical target identity/opportunity value are not overwritten or summed. If the target remains an open prospect, its follow-up becomes the earlier due date of the two leads; the duplicate's active follow-up is cleared. A converted target receives no new open-prospect follow-up.

Pipeline opportunity/due-work totals exclude merged duplicates. Source and canonical pages link retained duplicates only within existing scope. Cross-owner consolidation does not grant personal-view users the other owner's lead or conversation visibility. Canonical chains resolve to the current root without deleting prior links. No merge is a new customer activation, credit approval, price authorization or posted-document rewrite.

New quotations/projects/route work cannot select a merged duplicate; source services re-read locked leads to reject stale caller instances. Historical quotation revisions tied to an archived alias require a new quotation against the canonical record. Earlier closed records remain readable through their existing source permissions. An approved consolidation has no generic delete/undo endpoint; a reviewed identity correction would be separate work.

## Migration and remaining scope

Migration **0054** adds retained merge requests, nullable canonical links, the merged stage and constraints enforcing the stage/link relationship and prohibiting self-links. A pre-upgrade SQLite backup was created outside Git. All **135** existing nonmetadata table row counts were preserved; no live merge requests, alias links or staff grants were created. Downgrades after consolidation require a reviewed data/rollback plan rather than deleting audit history or inventing original identities.

Full cross-entity/customer-master deduplication, GSTIN/legal-name/address matching, richer field-resolution rules, approved merge correction, PostgreSQL race/deadlock/load validation and business acceptance remain pending. This milestone does not complete all of CRM or the SRS.


## Developer verification — 15 November 2026

Final full suite: **665 tests, 644 passed and 21 optional browser checks skipped**. All three affected CRM-merge/CRM/business browser checks passed explicitly. The new browser scenario previews, requests and independently approves a merge and checks read-only source presentation at 320/390/768/1440px without page overflow or JavaScript errors. Twelve new regressions cover retained original identities/activities, earlier due follow-up, maker independence, stale/retried/cancelled/rejected requests, onboarding/commercial holds, canonical chains and constraints, stale quotation references, scoped read-only sources, delegated source routing/rollback/actual actor and cross-owner personal-visibility protection. The related CRM/quotation/project/beat/delegation run passed 115 tests before the additional personal-visibility regression, which is included in the final full suite. Django, applied-migration/drift and diff checks passed. No live consolidation or staff grant was created. PostgreSQL concurrency, broader master deduplication and company acceptance remain pending.
