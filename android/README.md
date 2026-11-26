# Sistech ERP — Android app

One app for everyone (staff, sales team, dealers, drivers). After sign-in it shows what that login's roles allow:

- **Home** — navy hero with greeting and work waiting, role-wise numbers and quick actions
  (dealer: balance, credit, overdue, sold on, request order, ledger, sales to retailers, statement PDF;
  sales: my sales vs target, open orders, follow-ups, new order, dealer requests, customers, my orders;
  driver: trips on the road / at the plant, delivered, licence, the trip in transit as a big card, next trips),
  *Waiting for you* and the first modules.
- **ERP** — every web ERP module this login may open (same tiles and colours as the web launcher). A module opens the full
  web ERP inside the app, already signed in (one-time web session from the app token), with downloads (PDF/CSV/Excel) and
  file uploads working.
- **Work** — everything waiting for this user (same list as the web bell).
- **Account** — profile & security, help & support, sign out.

- **Trips** (driver logins) — active / delivered trips; trip page with progress, call the customer, navigate (opens the
  phone's maps app — the app itself never collects location), load & papers, POD and exceptions; *Record delivery* takes a
  photo of the signed copy (camera or gallery, downscaled before upload); *Report a problem* sends breakdown / delay /
  diversion / refused load to the office.

Native screens: sign-in (password, then email code when it applies), dealer account, order request, ledger & invoices
(invoice PDF opens in the phone's PDF viewer), sales to retailers, my sales, customers, dealer requests, my orders, driver
trips, trip, POD, road problem. Downloads from the web ERP are saved to the phone's Downloads with an *Open* button.
Design: navy hero with rounded edge, cards with soft shadows floating over it, shimmer placeholders while loading,
pull-to-refresh, status pills, empty states with icons — Poppins and the Sistech navy / gold throughout. API: `../docs/API.md`.

## Build
- Android Studio (bundled JDK) or `JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"`.
- `./gradlew assembleDebug` — debug talks to `http://10.0.2.2:8000` (the computer running `manage.py runserver`, seen from
  the emulator). Override: `-PsistechDebugUrl=http://10.0.2.2:8001`. Start Django with
  `DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,10.0.2.2` so it accepts the emulator's host name.
- `./gradlew assembleRelease -PsistechBaseUrl=https://<your ERP domain>` — release builds use HTTPS only and need a
  signing key (keep the keystore out of git).

Security: the token is stored encrypted with an Android Keystore AES-GCM key and is excluded from backups; plain HTTP is
allowed only in debug builds and only to the emulator/localhost.
