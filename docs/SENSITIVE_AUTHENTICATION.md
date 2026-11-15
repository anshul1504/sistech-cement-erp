# Sensitive web action reauthentication

SRS §3.2 requires step-up authentication or reauthentication for bank changes, payment release, commission payout, price/credit override, stock write-off, journal posting and period reopening.

## Behaviour

A completed password/email-code sign-in establishes a five-minute recent-confirmation window. The explicitly configured local password-only sign-in also establishes it after successful password verification. Email OTP remains required by default, including admin sign-in; this feature does not weaken that policy.

After the window expires, sensitive requests redirect to **Confirm your identity** before their view can execute. The user enters the current password, reviews the original document and explicitly resubmits the action. Only a safe local return URL is retained. Posted bank data, passwords, reasons, transaction payloads and uploaded files are never queued or automatically replayed.

The proof belongs to the authenticated user, credential hash, normalized email and current session key. It expires exactly at five minutes and rejects future/malformed timestamps. Successful confirmation rotates the session key and audits the event. Password failures are audited without the password; IP/identity throttling uses the existing 15-minute counters. CSRF and normal login/OTP/session checks apply. Confirmation grants no business permission, site scope, finance scope or maker-checker exception.

## Covered current web entry points

| Category | Guarded operations |
|---|---|
| Beneficiary / bank details | Partner create/edit, company profile changes, dealer application edits and credit/final approval |
| Payments / incentives | Supplier payment approval/release/failure reversal; scheme payout proposal/decision; bank-payment CSV download |
| Price / credit exceptions | Sales-order approval, quotation discount approval, price-rule create/edit/revise/end |
| Stock loss | Damage disposition, cycle-count adjustment approval, transfer discrepancy resolution |
| Journals / close | Manual voucher posting/reversal, year-close approval, year-reopening request/approval, month reopening |
| Access administration | Member creation/access/password/status changes; role creation/edit/copy/delete; Django-admin POST actions (login/logout excluded) |

The route inventory is centralized in `accounts.reauth.sensitive`; added endpoints must be reviewed against it. Login/logout and ordinary read screens remain available without an extra prompt. Sensitive data downloads are guarded separately from ordinary reads.

The HTTP boundary is enforced by middleware before the selected views run. Business services retain their own role, finance/site and maker-checker checks and remain usable for trusted in-process workflows. There is no public API/token stack yet: any future API or background approval entry point must validate appropriate transaction authorization; it must not assume a web-session proof exists.

## Verification / limits

Dedicated tests cover wrong password/throttling, exact expiry, user/email/password/session binding, malformed/future proofs, CSRF, unsafe return URLs, no auto-replay and unchanged business state on denial. Actual voucher and supplier-payment cases prove that draft/approved state is retained before confirmation and that explicit confirmed resubmission posts/releases.

Existing workflow tests now use an explicit confirmed-session fixture (analogous to Django force_login); dedicated reauthentication tests exercise real passwords and denial without that fixture. The real browser flow signs in using password/email OTP, expires its server proof, encounters the confirmation page, checks four viewport widths/local Poppins, confirms, reviews and explicitly resubmits. No real email/payment is sent.

Reauthentication is not a payment-provider authorization or evidence that a real transfer occurred. Dated approval delegation, unavailable-approver reassignment, API/mobile authorization, PostgreSQL concurrency validation and independent security/business UAT remain separate work.

Verification on 15 November 2026: full suite 424 tests (416 passed; eight opt-in browser checks skipped). All eight browser checks passed explicitly. The final eleven reauthentication tests also passed after malformed timestamp/encoded return-path hardening. Django system, applied-migration and migration-drift checks passed. No schema migration or live payment/email was required.

Manual reversal request, independent approval/rejection and cancellation now require recent confirmation on the journal action route. Requester evidence stays in the retained request; the original POST is never replayed after confirmation. Draft voucher rejection is protected alongside approval.
