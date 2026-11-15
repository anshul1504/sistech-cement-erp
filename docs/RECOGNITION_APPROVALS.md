# Recognition independent approvals and retained reward delivery

SRS §3.2 maker-checker and dated approval delegation now extend to company-recognition winner publication/sendback and influencer-usage verification. The existing role-authorized recognition workflow remains distinct from cash/bank commission payouts and scheme settlements.

## Review and routing

Both principal and deputy must retain enabled/unexpired accounts, the direct principal's review authority and existing module read access. These existing recognition records are company-wide within their module permissions; the grant does not invent site ownership or add finance, site, read or editing privileges. Usage verification and publication are separate grant types. A usage creator cannot verify directly or through a deputy. Programme creator, current ranker and every recorded rank contributor remain makers, including after a rerank or sendback. A maker cannot lend publication authority to a deputy.

The actual deputy remains `verified_by` or `published_by`. Delegation uses retain canonical `usage:{id}` / `STAR-{id}` source references; new recognition audit references use the stable programme identity rather than potentially long/nonunique names. Failed sendback/review validation rolls back use and state together. Read-only source buttons and routing do not consume a grant.

My Work, the personal assigned inbox, administration routing and source screens support both review types. Routing remains accountable/nonexclusive and adds no permission. A currently qualified independent configured backup can receive a disabled reviewer's assigned work. Completion removes it from pending inboxes while retaining assignment/history. Source scope labels explicitly identify company recognition rather than company finance.

## Reward delivery and source binding

A publication grant covers review/sendback only. It does not grant delivery/fulfilment writes. Delivery keeps the existing direct publication role plus programme read access, and the web award identifier must belong to the programme in the route. A forged cross-programme award does not update another award.

A new delivery retains actual actor, timestamp and evidence. A matching retry returns the existing receipt without creating a new audit or changing its actor/time; conflicting evidence cannot overwrite it. Delivery takes the parent programme lock before the award lock, matching the programme-first ranking/publication order. Published winners cannot be reranked. This reports delivery of an existing recognition reward; it does not transfer money, book a trip or execute an external payout.

Verification, publication/sendback and delivery require recent web identity confirmation and CSRF. Ranking, programme edits, normal source-evidence checks, highest-level-only awards and the existing volume/eligibility calculation are unchanged by delegation. Full versioned ranking/source snapshots, richer reward reversal and business acceptance remain scope to verify; this integration does not certify those as finished.

## Migration and historical boundaries

`erp.0048` adds programme contributors and nullable reward-delivery actor/time. Its data step retains only the existing creator/current ranked-by identities; future reranks retain each previous/current rank contributor. Earlier unknown ranking actors are not invented. Existing fulfilled awards retain their original note/flag and unknown actor/time remain empty; no fabricated delivery receipt is added. Existing published winners and awards are not changed.

A local SQLite backup was saved outside Git before application. All 131 existing non-metadata business/account table counts stayed unchanged. No live recognition programme, usage, winner, grant, routing, publication or delivery was created. The new contributor table had zero live links at application because no live programme required seeding. Applied-migration and drift checks passed. No role reset or default grant was run.

Verification commands: `python manage.py test erp.test_recognition_approvals erp.test_recognition accounts.test_handoff accounts.test_delegation erp.test_insights` and `RUN_BROWSER_TESTS=1 python manage.py test erp.test_recognition_approval_browser erp.test_business_browser`. Fixtures are fictional and isolated. PostgreSQL concurrency/load, company acceptance, remaining approval inventory, batch staffing orchestration, wider dimensional access, APIs/mobile and the remaining SRS remain work.

Verification on 15 November 2026: full suite 543 tests, 529 passed and fourteen opt-in browser checks skipped. After the final delivery lock-order refinement, all 67 related recognition/delegation/handoff/report tests and both affected recognition/business browser checks passed. Nine new regressions cover actual deputies, separate usage/publication authority, maker principals and retained prior rankers, failed sendback rollback, expiry/read-scope loss, qualified backup routing, immutable delivery retries, source-bound award actions, identity confirmation and known-identity migration without fabricated delivery actors. Browser execution completes assigned usage verification → winner publication → empty pending inbox at 320/390/768/1440px; a publication-only deputy has no reward-delivery button. Django, applied-migration/drift and diff checks passed. SQLite verification is not PostgreSQL concurrency or company acceptance certification.
