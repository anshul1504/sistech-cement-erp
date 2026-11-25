# Sistech ERP — mobile API v1

Base path `/api/v1/`. JSON in and out. Every response is `{"ok": true, ...}` or `{"ok": false, "error": "..."}` with an
HTTP status (400 invalid, 401 sign in again, 403 no access, 405 wrong method, 429 too many attempts). Money and
quantities are **strings** (exact decimals — parse as BigDecimal); dates are `yyyy-mm-dd`.

## Sign-in
1. `POST auth/login/` `{"username", "password", "device"}` — same password rules and rate limit as the web.
   - If email OTP applies: `{"otp_required": true, "email": "ab•••@domain"}`. Keep the session cookie from this response.
   - Otherwise: `{"token": "sst_…", "expires_in_days": 30}`.
2. `POST auth/verify/` `{"code": "123456"}` (same cookie) → `{"token": …}`.
3. Send `Authorization: Bearer <token>` on every other call. `POST auth/logout/` revokes it. A token also stops working
   after 30 days, when the password changes, or when the account is disabled/expired. Only a SHA-256 hash is stored.

## Common
- `GET work/` → everything waiting for this user (same as the web bell), each with a web `path`.
- `GET me/` → user, roles, `apps` (`dealer`, `sales`), linked dealer, `can` flags.

## Dealer app (login linked to a dealer account)
- `GET dealer/summary/` balance, overdue, not yet due, credit limit/available/used, this month bought vs sold MT.
- `GET dealer/invoices/` last 100 invoices with unpaid amount.
- `GET dealer/ledger/?from=&to=` opening, closing and statement rows (latest 300).
- `GET dealer/products/` orderable products with today's list rate and GST %.
- `GET|POST dealer/orders/` list / create order request `{"required_by", "lines": [{"product", "quantity"}], "ship_to", "note"}`.
- `POST dealer/orders/<id>/cancel/` cancel own waiting request.
- `GET|POST dealer/secondary/` own retailers + entries / record `{"date", "product", "quantity", "retailer" | "retailer_name", "rate"}`.

## Sales app (staff who may view or create sales orders)
- `GET sales/summary/` this month's sales vs target, open orders, quotations, leads/follow-ups, *My work* items.
- `GET sales/customers/?q=` customers with credit limit, available and overdue.
- `GET sales/orders/?mine=0|1` recent sales orders (plant-scoped).
- `GET sales/dealer-requests/` dealer order requests waiting (for users who may create sales orders).

## Full ERP inside the app
- `GET modules/` modules (and their pages) this user may open — same rules as the web launcher.
- `POST auth/web-session/` `{"next": "/m/finance/"}` → `{"url": "/api/v1/auth/bridge/…?next=…"}`. Open that URL in the app's
  web view within 60 seconds: it signs the user into the web ERP once (single use) and redirects to `next` (same-site paths
  only). Inside the app the web footer and web sign-out are hidden; the app signs out with `auth/logout/`.
