# Unified construction/project pipeline

Sales → **Project pipeline** (`/projects/`) brings project/site context, buyer/CRM identity, imported influencer referrals, activities, quotations and sales-order evidence into one owner/site-scoped workspace. It is a planning module; it does not post sales, stock, payments, accounting or rewards.

## Project and source identity

Create a project with site, responsible salesperson, construction name/location/district, contact/mobile, buyer context, estimated taxable opportunity, expected cement bags/TMT MT, expected close and next action. Mobile formats normalize to ten Indian digits. Estimates and demand must be finite/nonnegative; bags and MT remain separate units. New expected-close/next-action dates cannot be in the past and open projects require a next action. An exact normalized buyer/name/location combination cannot be duplicated at the same site, including after closure; name distinct phases explicitly rather than counting one opportunity twice.

Owners must be active, assigned to the site and have project view/edit permission. Salespeople see/edit their own projects; explicit team visibility extends reads only within assigned sites. Managing another person's project or reassigning ownership additionally requires management authority. Site is fixed after creation. Project ownership does not grant SiteAccess or change CRM lead ownership, dealer credit or territory assignments. A handover may require separate source-CRM access changes before the new owner can select/edit a private lead reference.

Use an approved customer or a named prospect, optionally with a visible same-site CRM lead. A CRM lead must be onboarded/linked to the chosen customer before combining both identities. When a linked prospect is later onboarded, explicitly bind that customer on the project; this preserves existing lead quotations. The project does not invent an active customer or bypass onboarding.

Legacy influencer **referral/project activities** can be imported once per source record. The source dealer must match the selected project customer. A retained snapshot records the influencer/title/location/date/status/expected volumes; later legacy edits do not rewrite that snapshot. Imported activities are protected from deletion. Source statuses remain unchanged: legacy referral and unified project reports are separate views, not additive totals or automatic bulk migration. No site/owner is guessed for historical referrals.

## Activities and stages

Calls, site visits, sample/technical reviews and notes retain their actor, actual activity day, content and next action. Activities cannot precede project creation or be future-dated, and the latest activity must retain a current/future next action. A sample activity is commercial/technical follow-up evidence, **not** a QC lab approval or production release.

| Stage | Evidence / planning weight |
| --- | --- |
| New enquiry | 10% |
| Qualified | 30% |
| Sample / technical review | At least one sample activity; 50% |
| Quoted | Current sent, accepted or converted quotation; 65% |
| Negotiation | Same quotation evidence; 80% |
| On hold | Reason and a future/current review action; 0% |
| Won | Linked sales order with independent approval evidence, matching buyer/site and not Draft/Submitted/Cancelled/Revised; 100% |
| Lost | Closing reason; 0% |

All stage changes require evidence/reason. Open/held stages require a next action. Won/lost clear follow-up and lock edits/activities/links. A manager can reopen to Qualified with a reason and next action; prior closed snapshots remain retained. An accepted quotation converted only to a **draft** order does not make a project won. Source document approval still follows its own normal workflow.

Stage weights are fixed planning defaults, not measured win probabilities or finance forecasts. Pipeline cards/export follow current search/stage/due filters. Open estimated value includes held opportunities but excludes won/lost. Weighted open value sums each open estimate × its stage weight, rounded to paise per project. The report does not add quotation/order values again or sum bags and MT together.

## Quotations, orders and actual invoicing

**Prepare quotation** opens the existing quote form with project buyer/site context. Saving a new project-context quote creates the quote and project link atomically: a mismatched buyer/site or unavailable project fails without leaving an orphan quote. Quote preparation, price exceptions, sending, acceptance and conversion remain the existing quotation workflow. Users need project edit plus quote/source access. A manual prospect must first be represented by a CRM lead or customer before quoting. Onboarded leads must be explicitly bound to their approved project customer.

A visible quotation detail includes a scoped return link to its project; users without access to that project do not get the link. The internal breadcrumb is excluded from print. Existing quotes/orders can also be linked with a note. A converted quotation provides **Link converted sales order** when an approved customer is bound; this attaches the existing order and never creates or approves it. Linking/retrying an existing source within the same project is idempotent.

Sources must match site and buyer/CRM identity. A quotation revision family cannot be actively split across projects; a converted order cannot be attributed away from its actively linked source quotation. A quote/order has at most one active project link. Unlinking requires a reason and retains the old snapshot/actor/time. A project's last qualifying quotation cannot be removed while it is Quoted/Negotiation; first review its stage. Closed projects require manager reopening before attribution changes.

Events retain the project forecast/context and source **current status/approval evidence at that event**, in addition to each link's original snapshot. Later expiry, revision, cancellation or identity changes do not silently rewrite a historical stage. Detail pages warn where a recorded win or quoted/negotiation stage no longer has qualifying live evidence, so the owner/manager can review it.

Where invoice-view permission exists, project detail separately shows lifetime taxable invoicing from active linked orders for the same customer/site, less all related credit notes. Invoice references are limited to 50 on screen; totals include all matches. This is not estimated pipeline, booked cash or a promise to pay. No receipt/bank information is exposed merely by project access. Unlinking/reassigning a source changes operational attribution, not the underlying books; it must not be treated as an authorized commission/accrual adjustment.

## Roles, retention and upgrade

The permission matrix adds Project pipeline, Project ownership & team and Project follow-ups. Fresh Sales executive presets can manage own projects/activities; Sales managers get assigned-site team management/reopening/export. Management and auditors get team reads; auditors can export. Source permissions (CRM, influencer, quotation, sales order, invoice) remain separate. CSV exports are scoped/filtered, formula-escaped, private and audited. My Work includes current/overdue project next actions and links to the matching filter.

Project/event/source references are retained. Shared customer deletion is blocked where projects/territory ownership reference it; imported referral deletion is blocked with a visible explanation. No project action deletes or edits a posted transaction. Existing customized roles are preserved by `setup_roles` and must receive new grants explicitly. Existing OTP defaults and site assignments are unchanged.

Migration `0044` adds project/activity/link/event tables, constraints and PRJ numbering. Before local application a SQLite backup was saved outside Git. Existing user/partner/order/invoice/stock/journal/CRM/collection/return/beat/territory-assignment/quotation counts were recorded and remained unchanged afterward. No real projects, source mappings or example transactions were inserted. Schema/pending-migration checks pass; databases, backups, screenshots and secrets are not committed.

## Verification and boundaries

`erp.test_projects` exercises normalized duplicates, ownership/site isolation, prospect onboarding, one-time referrals, activity dates, stage gates, source expiry/identity drift, quotation families/conversions, retained unlink and closure/reopening evidence, real quotation-form atomicity, protected sources, invoice/credit attribution, forecast isolation, CSV permissions/escaping and queues. `erp.test_projects_browser` checks six page types at 320/390/768/1440px and a collapsed desktop sidebar, local Poppins, overflow/duplicate IDs/JavaScript errors, source linking, sample activity, evidence-based stage change and project-context quote creation.

```sh
python manage.py check
python manage.py migrate --check
python manage.py makemigrations --check --dry-run
python manage.py test
RUN_BROWSER_TESTS=1 python manage.py test erp.test_projects_browser erp.test_beats_browser erp.test_returns_browser erp.test_schemes_browser erp.test_crm_browser erp.test_business_browser erp.test_finance_browser accounts.test_browser
```

Remaining: bulk CRM/referral migration and fuzzy duplicate merging, contacts/shared collaborators, independent calibration/configuration of forecast weights, detailed phase/product schedules, recurring plans, GPS/offline apps, APIs, notifications and dealer portals. Existing standalone quote forms keep their own workflow; only the explicit project context creates an automatic project link. Direct database edits remain outside the application audit boundary. PostgreSQL concurrency/load checks and real sales/finance UAT remain necessary; this does not complete the full SRS.

Developer verification on 14 November 2026: the full suite ran 399 tests (391 passed; eight opt-in browser checks skipped by default). All eight browser tests also passed explicitly, including the project handoff. `check`, `migrate --check` and migration-drift checks passed. The migrated local database retained the recorded pre-upgrade source-business row counts; no live campaign, customer mapping, financial posting or external message was created.
