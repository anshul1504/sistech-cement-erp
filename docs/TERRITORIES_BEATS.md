# Territories, dealer ownership and beat plans

This milestone implements the territory/field-planning portion of Step 8. It joins existing dealer, CRM, order, complaint and collections records through dated assignments and reported visits. It does not complete the SRS or introduce GPS attendance verification.

## Territory and ownership setup

Open **Sales → Territories & visits → Territories & ownership** (`/territories/`). An authorized manager creates a state root, districts under that root, then areas/beats under each district. Every branch belongs to one site; district/area state codes inherit from the parent. Codes are unique per site, and a site has at most one root for each state. The hierarchy is fixed after creation: archive/reactivate branches with a reason rather than silently moving historical customers. An inactive site/ancestor prevents new assignment/planning. Historical routes and review evidence remain retained.

Assign an existing active customer to an area, salesperson and inclusive date window. Owners must be active, assigned to the site, and have field-plan view and outcome-recording permissions. There can be only one overlapping assignment for a customer/site; the same customer can have separate ownership at another site. New assignments cannot be backdated. End an ongoing assignment with a handover note, through today/start or a later date, then begin the next assignment **after** that inclusive end day. An already recorded end date cannot be rewritten. Current/future and ended-history filters preserve the complete allocation trail.

This hierarchy does not overwrite shared `Partner.district`/`Partner.territory` text, existing scheme classifications or CRM lead owners, and it does not grant SiteAccess. Territory ownership scopes this field module; it is not a replacement for existing finance, CRM, stock or company-wide authorization policies. No automatic text-to-territory backfill guesses existing dealer geography.

## Plan, review and visit

1. Create a daily route (`/beats/new/`) with a site, owner, planned day and title. Salespeople plan their own visits; managers with territory-management and team visibility can prepare plans for others. One live plan is allowed per site/owner/day. Site, owner and day stay fixed; cancel an unused route and prepare another route to change them.
2. Add up to 50 customer stops from assignments valid on the planned day, with unique sequence numbers, purpose and instructions. Duplicate assignment stops are refused. Stops are editable/removable only in draft, with audit events. Purposes include dealer visits, order enquiries, collection follow-ups and complaint follow-ups.
3. Submit with a note. The route revision retains dealer code/name/contact/address, dated assignment and hierarchy snapshots. A separate reviewer approves or returns it for changes with a finding. The owner, creator, submitter and all draft contributors cannot approve their own route, including superusers. Changed assignment/customer evidence invalidates approval until refreshed through resubmission; old revisions remain readable. A past unapproved route must be cancelled/replanned rather than falsely approved as a completed visit.
4. On/after the planned day, the owner or an authorized managing user records the actual visit date, outcome, notes and optional next action. Dates must be between the planned day and today. The actor and recording time are retained, including when a manager records on someone else's behalf. A missed visit needs its own reason. Stops cannot be recorded twice; a route finishes when every stop has a completed or missed outcome. Once any outcome exists, cancellation cannot discard the route history.
5. Related CRM leads, sales orders and complaints must match the dealer/site and the actor's source-view access. These links do not create/edit an order, convert a CRM lead or settle a complaint. A complaint outcome requires a matching complaint link. Follow-up completion records another result without changing the original visit outcome.

Visited and missed are **reported field outcomes**. There is no GPS, check-in photo, geofence, automatic distance/travel calculation or attendance certification. A late entry retains its actual visit date separately from its recording timestamp.

## Field payment promises

A reported promise requires a positive amount and a promised payment date on/after the visit. Payment fields are refused for other outcomes. The report is initially visible only as a field claim; it creates no receipt, settlement, journal or finance follow-up automatically.

An independent finance reviewer needs `publish_beatpromise`, `add_receipt`, explicit company-finance scope, field-plan view/team access and the site's assignment. The reviewer must differ from both the route owner and outcome recorder. **Accept promise into collections** creates one `CollectionFollowUp` with the original visit/payment dates and owner. A late/expired promise remains late/expired; the deadline is not moved forward to make it look current. The original field report and reviewer finding remain in the route/audit trail. Retrying acceptance returns the same linked follow-up. The existing collections desk derives kept/broken status from actual posted receipts. This is not cash collection, a payment posting or a receipt acknowledgement.

Coverage and My Work show independent promises awaiting review. Current due field follow-ups and daily routes with outstanding visits are also available from My Work. Queue links match their pending review/due filters, including records outside the default date window.

## Coverage definitions

`/beats/coverage/` groups by site, route owner and area for up to 367 inclusive days. Salespeople see their own rows; explicit team viewers see only assigned sites. The date filter uses **planned route day**, while retained outcomes separately show actual visited/recorded dates.

- Assigned dealers: distinct customers whose dated ownership overlaps any part of the period.
- Planned stops: stops on approved/finished routes, including future approved plans within the selected range. Draft, submitted and cancelled plans are excluded.
- Completed/missed: counts of explicit recorded stop outcomes. Repeat visits remain separate completed stops.
- Visited dealers: distinct customers with a completed stop; repeated visits do not inflate coverage.
- Overdue pending: approved stops not recorded after their planned day. They do not silently become missed.
- Gaps: assigned dealers without a completed visit in that owner/area row during the period.

Current follow-ups and pending finance promises are separate current queues, independent of the chosen report period (up to 100 shown). The private CSV export includes the complete scoped coverage summary and gap codes, escapes formula-like text and records an audit event. No bank/receipt balances are exposed to salespeople by this report.

## Permissions and upgrade

The role matrix includes Sales territories & ownership, Beat & visit plans, Team field coverage and Field visit outcomes. Fresh presets:

| Role | Field responsibility |
| --- | --- |
| Sales executive | Own territory assignments, draft/submit daily plans, record visits and follow-ups |
| Sales manager | Assigned-site hierarchy/ownership, own/team planning, independent review, outcomes and coverage export |
| Accountant | Assigned-site team plan view and independent field-promise acceptance, with existing receipt/company-finance grants |
| Finance controller | Assigned-site team field visibility; additional posting/review grants are explicit |
| Internal auditor / Management | Assigned-site team read access; auditors can export coverage |

`setup_roles` preserves existing customized groups. Existing staff need the new rows granted explicitly and valid site assignments. Team visibility alone does not grant editing/approval/recording; managing another person's route additionally requires territory-management authority. A manager who creates/edits a route needs another independent reviewer. Application/model permissions never let users grant themselves sites or roles.

Migrations `0042` (new field module tables/constraints) and `0043` (BEAT numbering option) add no live territories, assignments or routes. Before local application, a database backup was saved outside Git. Existing user/partner/order/invoice/stock-movement/journal/CRM/collection/return record counts were recorded and remained unchanged after application. Migrations applied successfully and schema checks report no pending operations/drift. Existing source master values, role customizations and OTP defaults were preserved. Test data/screenshots/backups and local configuration are not committed.

## Verification and remaining work

The 23 workflow tests cover hierarchy/site validation, inherited state, non-overlap and inclusive handovers, active owner/site authority, own/team visibility, one daily route, stop uniqueness, independent contributors, frozen review revisions, future/late visit dates, immutable outcomes, missed visits, follow-ups, source-link scope, finance promise independence/retry/expired deadlines, no receipt/journal side effects, coverage gaps, escaped CSV, actual forms and queues.

The opt-in browser check covers eight plan/master/report screens and the outcome form across 320/390/768/1440px and a collapsed desktop sidebar. It checks Poppins, overflow/JavaScript errors and performs submission → independent approval → visit report → independent collections acceptance → retry using fictional records and isolated sessions.

```sh
python manage.py check
python manage.py migrate --check
python manage.py makemigrations --check --dry-run
python manage.py test
RUN_BROWSER_TESTS=1 python manage.py test erp.test_beats_browser erp.test_returns_browser erp.test_schemes_browser erp.test_crm_browser erp.test_business_browser erp.test_finance_browser accounts.test_browser
```

The unified project pipeline is now implemented separately. Remaining: bulk ownership transfers/import, recurring/optimized beats, mobile/offline synchronization and GPS/proof capture, cash pickup/receipting routes, external notifications and dealer portals. Territory classifications are currently independent of shared legacy text fields. Direct database edits remain outside the application audit boundary. PostgreSQL concurrency/load testing and actual sales/finance UAT remain rollout requirements.

Verification on 14 November 2026: the full suite ran 374 tests (367 passed; seven opt-in browser checks skipped by default). All seven browser tests were then run explicitly and passed. `check`, `migrate --check` and `makemigrations --check --dry-run` passed. Local migration `0042–0043` preserved the recorded business row counts after an external backup. No real route, customer assignment, payment, email or notification was created during verification.


## Temporary execution cover

For an approved route with pending visits, or a finished route with open field follow-ups, a site manager with team-beat visibility can propose an existing qualified executor and a finite expiry. Use **Route detail → Propose cover**. The route owner, dated dealer assignment, approved revision, customer snapshots and previous outcomes stay fixed. No role, site or finance grant is added.

A different checker with direct or valid delegated beat approval authority reviews the proposal. Route owner, original preparer/submitter/contributors, proposer and recipient are excluded; delegated principals must also be independent. Approval compares the retained outstanding-work evidence to the current route. If work changed or the window expired, reject and prepare a fresh proposal. Pending/approved unexpired cover blocks another overlapping proposal; rejected/revoked/expired records remain retained.

Only visits and follow-up execution are added. Draft editing, stop changes, cancellation, submission and route approval retain their original permissions and ownership checks. Each outcome/follow-up records the actual executor. Existing independently reviewed payment-promise acceptance remains separate. Cover can handle a partially visited route after the original owner's account is disabled; recorded visits are never reassigned.

Every request checks existing read/record roles, account availability, site access, the cover window and matching approved route revision. Expiry/revocation/access loss removes covered access immediately. Manager revocation needs a reason; proposal/review/revocation require recent identity confirmation and CSRF. My Work includes covered visits/follow-ups and eligible independent cover reviews. The plan register offers a cover-review filter; the approval-routing catalog can route the proposal to an eligible independent checker. Routing does not approve it.

Unavailable-staff attention omits a route while a current qualified approved cover executes it, and returns it on cover expiry/revocation/access loss if work remains. Finished routes with pending follow-ups are included. Draft/submitted route ownership and dated commercial dealer transfers still require their own reviewed transition; this execution workflow does not silently rewrite assignments.

Migration `erp.0045` adds the retained execution-cover table. Before local application, a SQLite backup and row-count inventory were saved outside Git. All 129 pre-existing non-metadata business/account table counts remained unchanged. Expected metadata additions were one content type, four default model permissions and the migration entry. No live execution cover was created.

PostgreSQL race/load verification, real backup staffing, company acceptance and mobile/GPS execution remain rollout work.

Reproduce execution-cover verification with `python manage.py test erp.test_beat_cover erp.test_beats accounts.test_handoff erp.test_insights` and `RUN_BROWSER_TESTS=1 python manage.py test erp.test_beat_cover_browser erp.test_beats_browser`. Browser checks exercise proposal → independent approval → actual cover execution and narrow/mobile layouts, in isolated fictional test databases.

Execution-cover verification on 15 November 2026: final full suite 488 tests, 479 passed and nine opt-in browser checks skipped. Both affected beat/cover browser checks passed explicitly. Eleven cover regressions cover partial routes, retained actual actors, stale evidence, replay/overlap denial, delegated review, independent makers, identity confirmation, expiry/revocation/site loss and unavailable-work attention. Django, applied-migration, migration-drift and diff checks passed. SQLite verification does not establish PostgreSQL concurrency acceptance.
