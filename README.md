# Sistech Cement ERP

A runnable first implementation of the supplied Cement Manufacturing ERP SRS using **HTML, CSS, JavaScript and Bootstrap 5** with a **Python Django** backend. Bootstrap assets are included locally, so the interface works without a CDN connection.

## Run locally on Windows

Python 3.12 or newer is recommended. Open PowerShell in this folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py setup_roles
.\.venv\Scripts\python.exe manage.py seed_demo
.\.venv\Scripts\python.exe manage.py set_login_email admin your-email@your-company.com
.\.venv\Scripts\python.exe manage.py runserver
```

Replace `your-email@your-company.com` with your real mailbox, configure SMTP in `.env`, then open **http://127.0.0.1:8000/**. The seed command prints a generated password for `admin`, `operator` and `approver`. It will refuse to overwrite existing demo users. Configure a unique real login email for each demo account before using OTP. For a clean installation without sample data, replace `seed_demo` with `createsuperuser` and provide your real email during account creation.

Use `admin` for master data and the **Access control** workspace at `/accounts/access/` to create team members, edit role matrices and assign sites. Use `operator` to create and submit an order, then sign in as `approver` to approve it. The creator cannot approve their own order, including when the creator is a superuser. Each non-superuser needs both role permissions and a SiteAccess assignment. Super administrator accounts are managed separately at `/admin/`.

## Authentication and access control

- Poppins fonts are hosted locally with their SIL Open Font License. Authentication pages share a responsive interface, keyboard focus states, field errors and password visibility toggles.
- Branding follows the supplied Sistech Cement & TMT reference: deep blue, golden-yellow and cement-grey, with readable high-contrast Poppins text. Local full/compact SVG logos and a roof-symbol favicon replace the temporary S badge. These assets are clean vector recreations of the supplied raster reference; the original raster is not bundled. `scripts/build_brand_assets.py` maintains the vector geometry. OTP email embeds a PNG logo as an inline CID attachment, with a text fallback, so it does not need a remote image URL.
- Sign in using a case-insensitive email address or username. Ambiguous legacy emails and disabled users are rejected. The team editor enforces case-insensitive email/username uniqueness for new accounts; existing duplicates must be corrected by an administrator.
- Sessions expire when the browser closes by default, with an optional 8-hour persistent session. Browser session restore can retain session cookies. Password change keeps the current session and invalidates other sessions. Password reset links expire after 30 minutes and cannot be reused.
- Login (including `/admin/login/`) has persistent hashed identifier/IP throttle windows: five identity attempts and thirty IP attempts in fifteen minutes. Successful login clears its identity counter. Password recovery uses equivalent windows and generic confirmation for unknown/disabled addresses. Current-password checks are also limited. Run `python manage.py purge_auth_limits` daily to clear expired counters. Production reverse-proxy rate limiting and concurrent PostgreSQL testing remain necessary.
- Forgot password, reset link sent, new password, invalid/expired link, reset complete, change password, signed out, access help and My account pages are implemented. There is no public registration; administrators create company accounts.
- **OTP is required by default for username and email sign-ins**, including admin. `EMAIL_OTP_SKIP_FOR_USERNAME=1` is an explicit password-only username override for local evaluation; leave it at `0` for normal use. Existing username-only sessions are signed out when the override is disabled. Existing ignored local configuration can override this default.
- **Two-step email OTP login** (email sign-ins, or all sign-ins when the username skip is off), including `/admin/login/`: validate password → send 6-digit code → verify code → create authenticated session. Codes expire after five minutes, permit five attempts, are bound to the initiating browser, and are stored only as password hashes. Resend requires 60 seconds, invalidates earlier codes and is rate limited; verification also has a shared ten-attempt window. Pre-authentication ends after fifteen minutes. Disabled users and changed passwords/emails invalidate pending challenges. Delivery failure never authenticates the user. Password-only sessions from before activation are signed out and must complete the new flow.
- The SMTP sender is separate from the user receiving the OTP. Each user needs a unique real email on their account. Empty and demo `.test` addresses are blocked without a password-only fallback. Bootstrap an existing account from a trusted administrator shell using `python manage.py set_login_email admin your-real-email@company.com`. The command records an audit event but sends no email. Then sign in normally to receive a code.
- On a fresh checkout, email defaults to the **console backend** for development. Copy `.env.example` to `.env` and configure real delivery. Settings now load ignored local `.env`; process environment variables take precedence. Implicit SMTP SSL uses `EMAIL_PORT=465`, `EMAIL_USE_SSL=1` and `EMAIL_USE_TLS=0`. Actual delivery depends on ignored local or deployment configuration; credentials are never committed. Never expose development terminals, OTPs or reset links publicly. In development only, `EMAIL_OTP_ENABLED=0` explicitly disables the second step; production deployments should keep it enabled.
- Role permissions are separate for Sales and Purchase: View, Create, Submit, Approve, Post and Cancel. Stock export is separately permissioned. Navigation, dashboard records, action buttons, views and posting services follow these permissions. Legacy generic order grants no longer grant Sales/Purchase access.
- Default roles: Operations maker, Operations approver, Sales executive, Purchase officer, Warehouse operator, Production operator, Quality manager and Internal auditor. `setup_roles` creates missing presets without replacing edits; `setup_roles --reset` explicitly restores all named presets. On upgrade from v0.1, review and migrate legacy groups with this command or the role matrix. New schema migration alone does not rewrite existing roles.
- **Administration** (super administrators): **Users** (list with search and role/location/status filters; add, view and edit; disable or enable instead of delete so history is kept; send a password reset link or set a new password; sign a user out everywhere; see their devices, access and activity), **Roles & permissions** (list, view matrix and members, create, edit, duplicate, delete when unassigned), **Organisation setup** and **System settings** (read-only health of email delivery, sign-in security, sessions, password policy and server, with a *Send test email* button). Every administrative action is audited.
- Messages after any action appear as toast notifications (success, error, warning, info) that close automatically after 3 seconds.
- Only a super administrator can manage roles, users and site assignments, including in Django admin. Ordinary team accounts cannot grant staff/superuser flags. Multiple roles and direct grants combine; separately managed permissions are disclosed and preserved in the role editor. Site assignments constrain operational documents, while products/partners are shared internal masters.
- **My account** (`/accounts/profile/`) has four tabs:
  - **Overview:** profile, security summary, emergency contacts, a per-module "what I can access" summary from the role matrix, and recent activity.
  - **Security:** change password with a strength meter; password age is tracked (reminder after 90 days); two-step status; every signed-in device with browser, IP and sign-in time, which can be signed out one at a time or all together. Older untracked sessions are grouped.
  - **Emergency contacts:** full add/edit/delete, up to 5, one primary, visible only to the owner.
  - **Activity:** the user's own audit history with filters and pagination.
- **Edit profile:** name, mobile, designation, department, and a WhatsApp-style profile photo (camera or upload, circular crop and zoom, view, remove with undo; resized to 512×512 in the browser and shown in the app header). Email, roles and sites remain administrator-managed.
- Authentication pages are mobile-first and tested from 320px phones to 2560px desktops; on desktop the sign-in page fits one screen without scrolling. The team directory stacks into cards on phones.
- Passwords must be at least 10 characters (plus Django similarity, common-password and numeric checks).
- Sign-in/out, password changes/resets, role edits and team access changes are audited. Security events without a site are visible to super administrators; ordinary audit views remain scoped to assigned sites. Disabling a user and changing their roles/sites takes effect on their next request.

## Organisation setup

Super administrators (and any role granted **Organisation setup → View/Edit** in the role matrix) manage the company profile at `/organisation/`:

- **Company identity:** legal and trade name, company type, date of incorporation, sidebar name/subtitle and tagline.
- **Branding:** full logo and square icon (PNG, JPG or WebP up to 1 MB; checked by file content, SVG rejected). Stored in the database and served from `/brand/logo/` and `/brand/icon/`, so every user sees them in the sidebar, phone header, sign-in page and browser tab with no media-server setup. The built-in Sistech logo is used until one is uploaded.
- **Registered office** with Indian state list (GST state codes) and PIN code.
- **Tax and statutory:** GSTIN (cross-checked against PAN and state code), PAN, TAN, CIN/LLPIN, Udyam and IEC, each format-validated.
- **Business and licences:** cement / TMT business lines, BIS licence numbers, financial-year start month.
- **Bank details** for invoices (IFSC and account number validated) and **contact and authorised signatory**.

- **GST settings** (registration type, e-invoicing, e-way bill), **standard cement bag weight** (pre-filled on new products), customer care / WhatsApp numbers, and default **terms & conditions** and document footer text.
- **Company documents** tab (`/organisation/documents/`): upload GST certificate, PAN, incorporation, Udyam, IEC, BIS licences (cement / TMT), ISO, factory licence, pollution consent (CTE/CTO), mining lease, explosives licence, fire NOC, legal metrology, cancelled cheque, insurance and other files (PDF, PNG, JPG or WebP up to 5 MB, checked by content). Each has number, plant (or company-wide), issue and expiry dates; expired and expiring-within-30-days counts and filters are shown. Files are served only to users with Organisation setup access; uploads and deletions are audited.

Changes are audited. Plant-level addresses, GST registrations and factory licences belong to Plants & depots. Invoice printing that uses these details arrives with the Finance module.

## Products & materials

`/products/` lists cement, **TMT bars**, raw materials, packaging, fuel and spares with category tabs, search, stock and GST. Each product has a detail page (specification, stock & tax, usage, stock by location, recent movements), and can be added, edited, duplicated (e.g. another TMT size), deactivated (hidden from new orders and production) or, if never used, deleted by a super administrator.

- **Cement:** grade (OPC 33/43/53, PPC, PSC, PCC), bag weight; IS standard and HSN 2523 filled automatically.
- **TMT:** grade (Fe 415–Fe 600, incl. D grades), bar size 8–32 mm, weight per metre (theoretical d²/162 when empty), bar length, bars per bundle and bundle weight; IS 1786 and HSN 7214 filled automatically.
- GST rate is stored per product (confirm rates with your accountant). Unit, bag weight and category lock once a product has transactions.
- **Add standard catalogue** creates OPC 43/53, PPC and PSC 50 kg bags and TMT Fe 500D/550D in 8–32 mm, skipping SKU codes that already exist.

## Plants & depots

`/sites/` shows each location as a card (type tabs, search, licence-expiry badges). A location records its facility (integrated cement plant, grinding unit, packing plant, TMT rolling mill, steel melting shop, depot or warehouse), address with state and PIN, **plant GSTIN** (checked against its state and the company PAN), manager, contacts and installed capacity. The detail page shows people with access, licences and documents (factory licence, CTO, BIS, NOC — reusing company documents with expiry tracking), stock at that location and recent activity. Locations can be deactivated (hidden from new orders, production and stock entries) or, if never used, deleted by a super administrator. Non-administrators see only their assigned locations and keep access to locations they create.

## Dealers & suppliers

`/partners/` covers customers (dealer, distributor, retailer, institutional / project buyer) and vendors (supplier, transporter) with group tabs, type filter and search. Each partner records contact person, phones, email, **GSTIN and PAN** (PAN taken from the GSTIN when empty; state checked), billing and delivery address, territory, **credit limit, payment terms and security deposit**, and bank details (account masked on screen). The detail page shows the **credit position** (approved + completed sales orders against the limit; invoices, receipts, credit notes and uninvoiced order commitments feed credit exposure), recent orders, KYC documents (GST, PAN, cancelled cheque, agreement, vehicle RC; PDF or image up to 5 MB) and a shortcut to a new order. Sales orders accept any customer type; purchase orders accept suppliers. The partner type locks once it has orders; partners with orders cannot be deleted.

## Masons & contractors (influencer network)

`/influencers/` (Sales → Masons & contractors) records masons/mistris, contractors, site engineers, architects and builders: mobile (normalised to 10 digits and **unique — duplicates are blocked with a link to the existing record**), district / tehsil / village (spelling normalised so reports group correctly), main dealer plus other dealers, specialisation, experience, monthly cement bags and TMT, current brand, and payout KYC (only the last 4 Aadhaar digits, PAN, UPI). Monthly bags set the tier (Bronze < 100 ≤ Silver < 400 ≤ Gold < 1,000 ≤ Platinum).

- **District-wise** (`/influencers/districts/`): masons, contractors, others, dealers located and linked, bags/month, Sistech share, open leads, and coverage gaps (dealer but no masons, masons but no dealer, people not linked, low share).
- **Dealer-wise** (`/influencers/dealers/`): the people behind each dealer, potential volume and Sistech share, dealers with nobody linked; each dealer's detail page also lists its masons and contractors.
- **Activity**: meets, site visits, calls, samples, gifts, and **site referrals / projects** with expected volume and an open → converted / lost status.
- **CSV import** with a downloadable template (type aliases like Mistri/Thekedar, state codes like MP, dealer codes), duplicate and per-row error report; **CSV export** behind its own permission (audited).
- Permissions: `view/add/change/export_influencer` in the role matrix; the Sales executive preset can view, add and edit. `manage.py seed_influencers` adds fictional demo records locally.

## Price list, numbering & GST on orders (Step 2a)

- **Price list** (`/prices/`, Sales → Price list): selling price per product before GST, optionally narrowed to a plant/depot, buyer district, buyer type or one dealer, with quantity slabs and validity dates. The most specific price wins (dealer > buyer type > district > plant > standard), then the highest slab reached. Overlapping prices for the same scope are refused. A price already used on orders is never edited — **Revise** creates a new version from a date and ends the old one the day before. Includes a price checker and a warning for finished products with no standard price.
- **Number series** per document type, plant and financial year (Organisation's FY start month): `SOPLT-/26-27/00001` for orders; tax-invoice series fit GST's 16-character limit and restart each FY. Allocation is row-locked.
- **Orders** now record place of supply, ship-to, freight terms and customer reference. Leaving the rate empty uses the list price; each line snapshots list price, price rule, GST rate and HSN. CGST + SGST or IGST follows plant state vs place of supply (supplier state vs plant state on purchases); totals round to the rupee with amount in words. Drafts can be edited. The detail page shows GST breakup, workflow steps and pre-approval checks: below-list-price lines, credit limit including GST, stock free after reservations, and B2C (no GSTIN) buyers. Credit exposure now includes GST.

## GST tax invoice (Step 2b)

A dispatched sales order gets **one tax invoice** (Finance → Tax invoices, or the order page). Posting checks the plant/company GSTIN, plant state and place of supply, numbers the invoice from the plant's FY series (≤ 16 characters), and **freezes** seller, buyer, ship-to, place of supply, every line (HSN, unit, rate, GST split) and totals — later master changes never alter it. Transport details (vehicle no. validated, transporter, LR, 12-digit e-way bill) print on it; a warning appears above ₹50,000 without an e-way bill. Due date = invoice date + the partner's payment terms. The invoice page is an **A4 print / save-as-PDF** layout (Original / Duplicate / Triplicate copy label, logo, GSTINs, HSN summary, amount and GST in words, bank details, terms, signatory). The register filters B2B/B2C, plant, dates and overdue, shows taxable/GST/value totals, lists orders ready to invoice and exports **one row per invoice × GST rate** for GSTR-1 (`export_invoice` permission, audited). Posted invoices cannot be edited; corrections come with credit notes (Step 2c). E-invoice IRN and e-way bill generation through a GSP are Step 11.

## Receipts, credit notes, ledger & ageing (Step 2c)

- **Receipts** (Finance → Receipts): NEFT/RTGS/IMPS, UPI, cheque, DD, cash; reference required for bank modes and checked for duplicates; cheque/DD date required; future dates refused; **cash of ₹2 lakh or more from one person in a day is blocked** (Income-tax s.269ST). Each receipt settles open invoices **oldest due date first**; the rest stays as an advance and settles the next invoice automatically. Cheques/DDs can be marked **bounced** (`bounce_receipt`), which reverses their settlements and shows a debit on the ledger.
- **Credit notes** from the invoice page: rate difference, goods returned, damage/short, scheme, other. GST reverses at the invoice's rates and supply type; value and quantity are capped by what is still creditable on each invoice line; "goods returned" posts the quantity back to stock (`Sales return`). Numbered per plant and FY within 16 characters, printable like the invoice, applied first to their own invoice.
- **Customer ledger** (from the dealer page): opening balance, invoices, receipts, bounces and credit notes with running balance, date filter and CSV; ageing (not due, 1–30, 31–60, 61–90, 90+) with open invoices. **Outstanding & ageing** lists every customer with buckets, advances held and CSV export.
- **Credit control** now uses the ledger: exposure = invoices − receipts − credit notes + approved/dispatched orders not yet invoiced (incl. GST). **Approval is blocked while any invoice is past due** or when the limit would be exceeded; the order page warns beforehand. The dealer page shows ledger balance, pipeline and overdue.

## Gate, weighbridge & dispatch (Step 3a)

Turn on **Gate & weighbridge dispatch** for a plant (Masters → Plants & depots → Dispatch settings) and set the stock-posting event (loading complete or gate-out), weight tolerance %, scale ID and loading bays. Sales orders at that plant then leave only through trucks (Logistics → Gate & dispatch); direct "dispatch & post stock" is blocked there.

- **Vehicles & drivers**: registration (validated, BH series supported), type, capacity, owner/transporter, RC / insurance / permit / fitness / PUC validity, blacklist; drivers with licence and validity. Expired or missing documents, blacklisted vehicles, a truck already on another trip, or an expired licence block planning and gate-in.
- **Truck flow** (SRS §4.4): plan truck against an approved order (partial quantities, capacity check, daily token) → gate-in with a 7-point security checklist → **tare** → loading at a bay → loading complete with actual bags/MT, seal, team, torn bags → **gross** → net vs loaded quantity (SKU bag weight) within the plant tolerance and vehicle capacity → **supervisor exception approval by a different person** → tax invoice for exactly what the truck carries (vehicle, transporter, LR, e-way bill printed) → gate-out → POD with receiver, shortage/damage and signed copy upload.
- **Weighments are immutable**: manual entry needs a reason; a reweigh adds a new linked reading with a reason and the original stays visible (struck through) on screen and slip. Gross must exceed tare; tare far from the vehicle's usual tare needs a reason.
- **Stock posts once** per dispatch line at the configured event (unique source id); cancelling a loaded truck puts stock back. Orders move Approved → Part dispatched → Completed automatically; **short-close** ends a part-dispatched order. One order can have many trucks and invoices.
- **Segregated roles**: Gate security, Weighbridge operator, Loading supervisor, Logistics manager presets; gate cannot weigh, weighbridge cannot run the gate, the weigher/loader cannot approve their own exception. Board by stage with exceptions and POD pending; printable **weighment slip** and **gate pass**.
- Weighbridge hardware integration is Step 11.

## Freight (Step 3b)

- **Freight rates** (Logistics → Freight rates): lane = plant → buyer district, optionally for one transporter and/or vehicle type; per MT (with minimum chargeable MT) or per trip; detention per day, unloading charge, distance, validity. Overlapping rates for the same lane are refused; a rate already used on trips can only be ended.
- At **gate-out** each trip freezes who pays (customer for ex-works orders or customer-owned trucks, otherwise Sistech) and the **contracted freight** for its net weight, so later rate changes never alter past trips.
- **Freight bills**: enter the transporter's bill against their unbilled trips. Per trip, payable = claimed freight capped at contract + extras allowed up to (detention days × lane detention + unloading) − **shortage/damage recovery** from the POD at invoice value; minus advance already paid. Claims above contract are shown as variance. Duplicate bill numbers are refused. A **different user** approves (a note is required when the claim exceeds contract) or rejects (which frees the trips for a corrected bill). Approved bills post to transporter payables and can be settled through Finance → Payments.

## Purchase requests, RFQ & PO approval (Step 4a)

- **Purchase requests** (Procurement → Purchase requests): plant, needed-by date, priority, source (manual, reorder level, production plan, maintenance) and purpose; items with optional estimated rate and specification. Stock at the time of request is recorded. Submit → a **different user** approves or rejects (reason required). Requests close automatically when fully ordered and reopen if a PO is cancelled.
- **Reorder suggestions**: items below their reorder level per plant after counting open POs and pending requests; suggested quantity is the product's reorder quantity or the shortfall. Tick and raise draft requests in one step.
- **RFQ & quote comparison**: from an approved request, invite suppliers for the pending quantity. Record each quote (rate and GST per item, freight, delivery days, credit days, validity). Quotes are ranked L1, L2… on **landed cost** (rate + supplier freight spread by value), then delivery time, then credit days, with each supplier's past acceptance % beside them. Expired quotes cannot be awarded; awarding anyone other than L1 needs a reason.
- **Award** raises a draft PO linked line by line to the request, with supplier freight added into the rate (part of the value of supply under GST), payment terms, over-receipt tolerance and delivery terms. **Direct PO** (single source / rate contract / breakdown) is allowed from an approved request with a mandatory reason shown to the approver.
- **PO controls**: quantities can never exceed what the request approved (drafts count too); zero rates are refused; the approver must differ from the creator; POs above the **PO approval limit** (Organisation setup → Purchase controls, default ₹5,00,000 incl. GST) need the *High-value PO approval* permission (new *Purchase head* role). Roles: *Purchase officer*, *Purchase head*, *Store requester*.

## Inbound gate, weighment & GRN (Step 4b)

- **Goods receipt** (Procurement → Goods receipt): board of trucks inside, material on quality hold, approved POs expected from suppliers and posted receipts.
- **Gate-in** against an approved PO: vehicle, driver, transporter, supplier invoice/challan number and date, e-way bill, optional photo of truck or challan (PDF/PNG/JPEG/WebP ≤ 5 MB), and the challan quantity per item. A challan number is accepted once per supplier; a vehicle cannot be inside twice.
- **Weighbridge** (plants with *Gate & weighbridge* on): gross when the loaded truck arrives, tare after unloading. Readings are immutable; manual entry and every reweigh need a reason and keep the original. Locations without a weighbridge count at unloading.
- **GRN**: per item received = accepted + rejected + damaged + quality hold; short / excess against the challan is worked out. Received MT/KG must match the weighbridge net within the plant tolerance (a single weight item is pre-filled from net). Accepted + hold cannot exceed PO quantity + its over-receipt tolerance — the excess is rejected. Rejected/damaged need a reason; a challan variance beyond tolerance needs a reason. Only accepted quantity enters stock (once, unique source per line). The PO moves to *Part received* / *Completed*; part-received POs can be short-closed but not cancelled. The printable **GRN** values accepted quantity at the PO rate.
- **Quality hold** (FR-PUR-007): held quantity stays out of stock until a quality user — not the GRN poster — accepts and/or rejects it with the lab result. The one-step "receive & post" for purchases is retired; all purchases go through GRN. Roles: *Gate security* (gate-in), *Weighbridge operator*, *Warehouse operator* (post GRN), *Quality manager* (release).

## Supplier invoices, three-way match & debit notes (Step 4c)

- **Supplier invoices** (Finance → Supplier invoices): enter the supplier's invoice number, date, GSTIN, taxable value, GST and total, and the billed quantity / rate / GST % against received GRN lines (one plant per invoice). Live totals help catch typing mistakes.
- **Three-way match** per line: billed quantity ≤ accepted GRN quantity not yet billed (+ invoice quantity tolerance), billed rate ≤ PO rate (+ rate tolerance), GST % = PO GST %. The header must add up to the lines (₹1 round-off) and the GSTIN must be the supplier's. Tolerances are in Organisation setup → Purchase controls (default 0%). Clean invoices are **Matched** and payable with a due date from the PO credit days; others go to the **exception queue**.
- **Duplicate check**: same supplier + invoice number (ignoring case, spaces and punctuation), or same supplier + GSTIN + date + amount, is refused.
- **Exception decision** by a different user (*Finance controller*), with a reason: pay as billed, pay the matched value (the difference becomes a **debit note** automatically), or reject (the quantity becomes billable again).
- **Debit notes** from a posted GRN: *return to supplier* takes accepted stock out (stock movement "Return to supplier") and is valued only for quantity already billed, so nothing is claimed twice; other reasons are value claims. Printable debit note.
- **Supplier balances**: approved invoices − debit notes, with past-due amounts. Payments and returned-payment reversals are available in Finance → Payments. Roles: *Accounts payable*, *Finance controller*.

## Inventory depth (Step 5)

- **Stock states**: available, damaged and blocked are real balances per location and item; reserved (open approved sales orders), quality hold (GRN hold and production awaiting release) and in transit (dispatched transfers) are derived. The **Inventory** page shows available, reserved, **free** (available − reserved), quality hold, damaged, in transit, MT equivalent for bags and value, with filters for below-reorder, over-committed and damaged.
- **Valuation**: weighted-average cost per location and item. GRNs bring stock in at the PO rate, opening stock can carry a rate, transfers move at the source cost, and every movement keeps its unit, rate and resulting balance (the **stock card**). Stock received before costing began takes the first real cost.
- **Transfers** between plants and depots: request → approval by a different user → dispatch (vehicle, LR, e-way bill; stock leaves the source and is in transit) → receipt by someone other than the dispatcher (good and damaged counted separately). Shortage or damage opens a **discrepancy** that a third person resolves; transit loss is shown at cost and transit damage creates a damage report.
- **Damaged stock**: reason (torn, wet, leaking, hardened, rusted…), location, responsible person and photo. The quantity leaves available at once; a different user decides how much is recovered (back to available) and how much is scrapped.
- **Stock counts**: cycle or annual, by location and optionally category, blind by default. System quantity is frozen at start; counters enter quantities (save as they go), submit, and a different user reviews the variance at cost, sends chosen items for recount, and approves — each variance is posted as a count adjustment against today's stock, so sales during the count are not lost.
- **Ageing & slow-moving**: stock value by age bucket (0–30, 31–60, 61–90, 90+ days since last receipt), days idle since the last issue, slow-moving after 30/60/90/180 days, and flags for above maximum / below reorder. Products now carry reorder quantity and maximum stock.

## Production & quality (Step 6)

- **Recipes & BOM**: material per unit of output with tolerance and expected yield, versioned; a different user approves and the earlier version retires; each batch keeps the version it used.
- **Process orders**: plant, product, planned output, shift, line/mill, automatic batch number. **Material issue** from available stock at average cost with source silo and optional receipt lot (GRN). **Completion** records good output and scrap, compares issued material with the recipe standard (outside tolerance needs an explanation) and fixes the batch's **actual cost per unit**. Output enters stock only on quality release, at that cost.
- **Quality lab**: IS-standard specifications created by `python manage.py seed_quality_specs` (IS 269 OPC 33/43/53, IS 1489-1 PPC, IS 455 PSC, IS 16415 PCC, IS 1786 Fe 415…600) — fineness, setting times, soundness, chemistry, 3/7/28-day strength; yield, UTS, UTS/YS, elongation, C/S/P, bend. Results are checked against limits automatically and never edited; a retest needs a reason and keeps the first value. Equipment with overdue calibration cannot be used. Release needs every release parameter passing and a quality user who neither created nor completed the batch; out-of-spec batches can be rejected or released under deviation with justification. A failing result after release (e.g. 28-day strength) raises a quality alert.
- **COA / mill test certificate** per released batch (optionally addressed to a customer); results still to come show their due date.
- **Batch traceability**: loading records the batch on each dispatch line; the trace page shows raw-material lots and issues → QC results → packing → trucks → invoices → customers.
- **Shift log**: running hours, downtime minutes with reason, meter readings, power kWh (kWh per unit), output, crew and supervisor — one entry per shift and line.
- **Packing**: bulk issued by silo meter vs bags × bag weight + spillage; a difference above 0.5 % needs a reason. Bulk and empty bags are consumed and bags enter stock at their combined cost. Roles: *Production head*, *Production operator*, *Quality manager*.

## Accounting & treasury (Step 7 — working implementation)

`/accounts-ledger/` provides journals, chart of accounts, manual vouchers with independent approval, trial balance, profit & loss, balance sheet, account ledger and cash flow. Document services automatically post balanced, source-keyed entries for stock movements, invoices, receipts, credit/debit notes, supplier invoices and freight bills. Retrying a source does not create a second voucher. Closed months reject posting; application corrections create linked reversals.

- **Payments:** proposals reserve approved supplier/freight bills or assets bought on credit; debit notes reduce the payment. Another user approves and the maker cannot release it. Release records the bank reference and posts the payment; a bank return reverses it and reopens the allocations. Beneficiary name/account/IFSC are frozen at proposal; a change requires cancellation and fresh approval. The bank CSV uses the snapshot. This records bank-portal transactions; it does not transfer money.
- **Bank reconciliation:** atomic CSV import, duplicate-row checks, permissioned matching/unmatching with audit history, unique exact-reference auto-matching and manual resolution. Historical reports keep entries matched after the report date outstanding.
- **Fixed assets:** register, supplier-credit or bank funding, SLM/WDV monthly depreciation, residual-value cap, one run per asset/month, chronological posting and disposal with gain/loss. Credit purchases appear in supplier payment proposals. Policy is a simplified full-month calculation, not a statutory depreciation calculator.
- **GST summary:** outward/inward tax, credit/debit notes and HSN export; quantities remain separate by HSN, tax rate and UOM. These are reconciliation reports, not filed returns or an eligibility decision for input tax credit.
- **Periods:** the close action blocks pending manual vouchers, current AR/inventory/AP control-account differences and outstanding imported bank rows. Reopening is audited. Full historical close, bank completeness, tax review and stock count sign-off remain business controls; no year-end earnings carry-forward is implemented.
- **Access:** this central workspace requires **Company-wide finance access** plus the specific view/create/approve/post permission. It is deliberately separate from operational site assignments. New Accountant, Finance controller and Internal auditor presets include the scope; existing customised groups must be explicitly granted it through Roles & permissions. `setup_roles` preserves existing role edits. Granting the scope alone grants no transaction action.

See [Finance operations and limitations](docs/FINANCE.md) for setup, reconciliation and verification. Finance and business owners must validate the chart/posting map and sign UAT before operational use.

## Implemented workflows

- Responsive internal web ERP with local Poppins/Bootstrap, Sistech blue/yellow branding and The Webfix credit.
- Authentication, CSRF-protected forms, module/action permissions and operational site scope; central accounting requires explicit company-wide finance access.
- Sales/purchase drafts, independent approvals, credit/overdue controls, partial dispatch and short-close. Draft orders can be edited; approved revisions remain pending.
- Atomic stock movements, source-keyed posting, inventory states, valuation, transfers and cycle counts.
- Versioned recipes, production consumption/output/costing, quality results, disposition, COA and batch traceability.
- GST documents, collections, supplier/transporter payments and accounting statements linked through the central posting engine.
- Workflow and stock history, audit events and permissioned exports. Posted documents are corrected through supported reversals/adjustments.

Quantities retain each SKU's base unit and pack weight. Bag/MT equivalents are available, while versioned alternate-UOM transaction entry remains pending. Order details show GST; the separate posted tax invoice is the frozen invoice record.

## SRS implementation boundaries

This is a working foundation, not completion of the full multi-phase SRS. The following are still required before an operational ERP rollout:

| SRS area | Current status / remaining work |
| --- | --- |
| Organisation and access | Single company; editable role matrices, user/site assignments and authentication workflows implemented. Multiple legal entities/GST registration hierarchy, territory/dealer portal isolation and sensitive-action step-up authentication remain. Monthly accounting periods are implemented; email OTP is enabled by default. |
| CRM and dealer network | Dealer/supplier master with GST, credit terms and KYC documents. Leads, KYC approval workflow, territory hierarchy, visits and project pipeline remain. |
| Sales | Price list with slabs and dealer prices, GST orders, credit/overdue blocking, partial dispatch and short-close. Quotations, stock reservations/ATP and order revisions remain. |
| Procurement | Purchase requests, reorder suggestions, RFQ with landed-cost comparison, award, PO approval by value. Inbound gate, weighment and GRN with accepted / rejected / damaged / quality-hold quantities and lab release. Supplier invoices with three-way match, duplicate check, exception queue and debit notes / returns. Supplier payments are implemented; budget checks remain. |
| Inventory | Stock states, weighted-average valuation, stock card, transfers with in-transit and discrepancy, damaged-stock disposition, blind cycle counts with recount and approval, ageing and slow-moving. Production lot traceability is implemented; bin locations and full inventory balances by lot remain. |
| Production and quality | Versioned recipes, process orders, material issue, variance and actual costing, shift log, packing reconciliation, IS-standard specifications, immutable results, calibration control, release / deviation / reject, COA and batch traceability. Production planning against demand, PLC/SCADA data and CAPA remain. |
| Logistics | Gate, immutable weighments with tolerance approval, loading, invoice, gate-out, POD, freight rates and transporter bill check. Weighbridge hardware, GPS and trip tracking remain (Step 11). |
| Finance and GST | GST tax invoices, receipts with allocation, credit notes, dealer ledger and ageing. GL, AP payments, journal approvals, bank reconciliation, assets/depreciation, statements and basic period-close controls are implemented. E-invoice/e-way bill connectors, statutory filing, budgets, full historical close/year-end carry-forward and finance UAT remain. |
| Schemes and claims | Not implemented. Versioned incentive rules, commissions, returns, complaints and settlements remain. |
| Android and integrations | Not implemented. Scoped APIs, employee/dealer/driver apps, offline drafts, notifications, GST/bank/GPS/hardware integrations remain. |
| Production operations | PostgreSQL migration, concurrent workload testing, audit retention hardening, monitoring, backups/restore, secure hosting and UAT remain. |

No external banking, GST, weighbridge or payment calls occur in this implementation. Hardware and tax rules require the actual business configuration. A failed quality test remains recorded; no application action silently deletes it. Direct database operators remain outside the application audit boundary.

## Verify

```powershell
python manage.py check
python manage.py test
```

For repeatable desktop/mobile visual checks, install the optional Playwright package and Chromium, then run `python scripts/browser_smoke.py`. It creates an isolated test database and local test server, uses fictional accounts and in-memory email, completes the two-step login, and captures authentication/access/ERP pages into ignored `test-results/`. No real email is sent and no production credentials are used.

Tests exercise authentication, throttling, password recovery and token expiry/single-use, session invalidation, role/permission injection, Sales/Purchase isolation, disabled users, site isolation, maker-checker, stock rollback, credit limits, quality release and HTTP pages.

## Project layout

`config/` contains Django settings and routes. `erp/models.py` defines persisted data, `erp/services.py` contains transactional workflow rules, `erp/views.py` enforces permissions and site scope, `erp/forms.py` validates input, `templates/` holds HTML, and `static/` holds Bootstrap, custom CSS and JavaScript. `erp/migrations/` contains the database schema history.

SQLite is for local development and evaluation. Choose PostgreSQL and test concurrent stock/credit/OTP workflows before multi-user production use. Production requires `DJANGO_DEBUG=0`, a unique `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS` (for example `https://erp.sistech.co.in`), HTTPS, a production WSGI/ASGI server and static file serving. With `DJANGO_DEBUG=0`: secure cookies, HTTPS redirect and HSTS (`DJANGO_HSTS_SECONDS`, default one year) are enabled, and OTP is required for every sign-in unless `EMAIL_OTP_SKIP_FOR_USERNAME=1` is set explicitly. Behind an HTTPS reverse proxy set `DJANGO_BEHIND_HTTPS_PROXY=1`. SMTP credentials belong in the deployment environment or an ignored local `.env`.

Reference documentation used for the implementation: [Django transactions](https://docs.djangoproject.com/en/5.2/topics/db/transactions/) and [Django authentication](https://docs.djangoproject.com/en/5.2/topics/auth/default/).
