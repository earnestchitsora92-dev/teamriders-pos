# Team Riders POS

A fast, self-contained point-of-sale and stock-control system for a motorbike spares business.
Built with **Python + FastAPI** and server-rendered pages. It runs on SQLite out of the box (fast, no
server to install) and switches to PostgreSQL automatically when `DATABASE_URL` is set, so the same code
can run in the cloud when the shop PC is off.

Money is stored as integer cents, stock is tracked through an append-only ledger, and every discount,
cash movement and month close is recorded permanently.

---

## 1. Quick start

```bash
cd mx-spares
pip install -r requirements.txt
python run.py
```

Open **http://127.0.0.1:8077** (on Windows you can also double-click `start.bat`).

On the very first launch the app shows a **setup screen**:

1. Enter the business name, your name, an admin username and a password.
2. That account is the owner/administrator.
3. The demo spare-parts catalogue (27 realistic parts) is loaded automatically — edit or archive it freely.

Then add cashiers from **Team & access → Add a user** (role: *Cashier*).

Requirements: Python 3.10+ (tested on 3.13). No Node.js, no cloud services, no external database server.

---

## 2. Publish it online from this PC (Cloudflare Tunnel)

The shop is published at **https://www.teamriders.bid** (also `https://shop.teamriders.bid`) through a
named Cloudflare Tunnel. The domain is registered at Cloudflare; HTTPS certificates are managed for you
and no ports are opened on the router.

> **This PC must stay on.** For a version that keeps working when the PC is switched off, see
> [section 2b](#2b-run-in-the-cloud-works-when-the-shop-pc-is-off) (free Render + Neon).

Start the app and the tunnel together:

```powershell
.\start-online.bat
```

**Auto-start:** a shortcut named *Team Riders POS* is installed in the Windows Startup folder, so the shop
comes online automatically when this PC signs in (the window is hidden; logs go to `logs\`). A second
shortcut on the Desktop starts it manually with a visible window.

* Keep the PC switched on and connected — the site is served from this machine.
* Data stays in `data/mxspares.db`; back that folder up regularly.
* The launcher retries the tunnel every 30 seconds if the connection drops.
* Tunnel settings: `%USERPROFILE%\.cloudflared\config.yml` (tunnel name `teamriders`, hostnames, local port).
* The apex `teamriders.bid` can take a while to resolve everywhere after a new registration — use `www`.
* The address is public, but every page requires a username and password.

For access without internet exposure, `start.bat` runs it locally and other devices on the same
Wi-Fi can use `http://<this-pc-ip>:8077`.

### Azure App Service (if you get an account later)

The repository is already prepared for Azure App Service (Linux, free F1):

```powershell
az login
.\deploy-azure.ps1
```

`startup.sh` keeps the SQLite database on the persistent `/home/data` volume and switches it to
rollback-journal mode, because WAL is unreliable on Azure's network file share.

---

## 2b. Run in the cloud (works when the shop PC is off)

The tunnel above needs this PC switched on. To make the shop independent of the PC, the app runs on
**Render** (free web hosting) with the database on **Neon** (free PostgreSQL). Both are free, need no
card, and the app already supports PostgreSQL — `DATABASE_URL` switches the backend.

| Piece | Service | Free tier |
|---|---|---|
| Web app | Render *Web Service* (Python) | 512 MB, sleeps after 15 min idle (kept awake by the GitHub Action) |
| Database | Neon PostgreSQL | 0.5 GB, autosuspend |

### Step 1 — Put the code in a GitHub repository

```powershell
$env:GH_TOKEN = "ghp_..."   # GitHub → Settings → Developer settings → token with `repo` scope
python scripts\push_to_github.py --repo teamriders-pos
```

The script creates the repository (private by default) and uploads the source with no `git` install needed.

### Step 2 — Create the free PostgreSQL database (Neon)

1. Sign up at <https://neon.tech> with GitHub or e-mail (no card).
2. Create a project in the region closest to you (e.g. *AWS eu-central-1*).
3. Copy the **connection string** — it looks like
   `postgresql://user:password@ep-xxx.eu-central-1.aws.neon.tech/neondb?sslmode=require`.

### Step 3 — Copy the shop data into Neon

Run this once from this PC (it replaces whatever is in the empty Neon database):

```powershell
$env:DATABASE_URL = "postgresql://...neon.tech/neondb?sslmode=require"
python scripts\migrate_to_postgres.py
```

Every table is copied with its original IDs and the ID sequences are reset, so the next receipt number
continues where the shop left off. Sign-in sessions are not copied — everyone signs in again.

### Step 4 — Deploy to Render

1. Sign up at <https://render.com> with GitHub (no card).
2. Create an API key: *Account settings → API keys*.
3. Run:

```powershell
$env:RENDER_API_KEY = "rnd_..."
python scripts\deploy_render.py --repo-url https://github.com/<you>/teamriders-pos `
    --database-url "postgresql://...neon.tech/neondb?sslmode=require" `
    --domain www.teamriders.bid --wait
```

The script creates the free web service, sets the environment variables (`DATABASE_URL`, Python version),
attaches the domain, and waits for the first deploy. You can also create the service by hand from
`render.yaml` in the Render dashboard (*New → Blueprint*).

### Step 5 — Point the domain at Render

In Cloudflare DNS, replace the tunnel CNAMEs for `www` and `shop` with the hostname Render shows for the
service (e.g. `teamriders-pos.onrender.com`). Keep the Cloudflare proxy on *DNS only* until Render has
issued its certificate, then switch it back to *Proxied* if you want Cloudflare caching.

### Step 6 — Keep it awake

Render's free tier sleeps after ~15 minutes idle. The repository ships
`.github/workflows/keepalive.yml`, which pings `/healthz` every 10 minutes — enable Actions on the
repository and the shop stays instantly available. (Any uptime pinger such as cron-job.org works too.)

### Day-to-day with the cloud version

* Everything (stock, sales, discounts, cash, closings, alerts) lives in Neon — nothing depends on this PC.
* The PC copy keeps working offline on SQLite; both use exactly the same code and screens.
* Test the database path any time with: `python scripts\pg_smoke_test.py` (35 checks over sales, stock,
  discounts, quotations, cash, closings and reports).
* Neon free databases pause when idle; the first query after a pause takes a second or two.

## 3. Roles

| Capability | Cashier | Administrator |
|---|:--:|:--:|
| Search prices, sell, print receipts | ✅ | ✅ |
| Create quotations | ✅ | ✅ |
| Capture customer email when out of stock | ✅ | ✅ |
| View own sales receipts | ✅ | ✅ |
| View every sale, profit and cost | — | ✅ |
| Apply discounts (with admin authorisation) | ✅ (needs approval) | ✅ |
| Products: view | ✅ | ✅ |
| Products: create / edit / archive | — | ✅ |
| Restock, adjust stock, stock ledger | — | ✅ |
| Business cash ledger | — | ✅ |
| Monthly close-off, reports, users, activity log, settings | — | ✅ |

Passwords are hashed with scrypt; each user has an independent session (12 h, sliding). Every
state-changing form is protected by a per-session CSRF token, and login attempts are throttled.

---

## 4. Daily workflow

### Selling
1. **Point of Sale** → search by part name, SKU or bike model (`brake`, `CG125`, `SPK-001`).
2. A dropdown suggests matches **with live price and stock**; ↑/↓ then Enter (or click) adds the part.
3. Adjust quantity, pick payment method, enter cash received — change is calculated live.
4. **Complete sale** writes the sale, reduces stock, records the money and offers **Print / PDF / View**.

Receipts are available in three forms:
* 80 mm thermal receipt page with automatic print dialog (`Print`),
* 80 mm PDF (`PDF`),
* A4 PDF (from the sale page).

Windows sends the browser print dialog to the receipt printer; choose your 80 mm printer and set margins to none.

### Discounts (authorised and tracked)
1. In the cart, **Apply discount** → choose percentage or fixed amount and **type a reason**.
2. The cashier either enters the administrator's username + password at the counter (instant approval),
   or leaves them blank to send a pending request.
3. Pending requests are approved/denied on **Discounts**.
4. Each authorisation gets a code such as `DSC-2B226A`, expires after 30 minutes and can be used once.
   Codes can also be typed into the *Discount code* box.
5. Records keep cashier, approving admin, reason, value, status and the resulting receipt. The
   **maximum discount %** policy is enforced server-side.

### Quotations
**Point of Sale → ＋ Quotation** (or `/pos?mode=quote`). Build the cart, add customer details, save.
Quotations get their own number (`QTN-YYYYMM-0001`), validity date and A4 PDF with terms.
Use **Sell** on a quotation to load it back into the cart and convert it to a sale.

### Out of stock → email capture
If a part has zero stock in sale mode, adding it opens a dialog to capture the customer's email
(and name/phone). When the part is restocked the customer becomes “ready to notify”:
* with SMTP configured, emails are queued automatically at the moment of restocking;
* otherwise, press **Notify** (or *Send all ready notifications*) on **Back-in-stock**.

### Stock & purchasing
* **Stock levels** shows quantity, reorder point, cost value and retail value per part.
* **Restock** adds quantity, recalculates a weighted-average unit cost, logs the movement and can
  **deduct the purchase from business cash** (with supplier, invoice note and payment method).
* **Adjust stock** handles damages, stock counts and corrections with a mandatory reason.
* **Stock ledger** is the full audit trail: opening, restock, sale, adjustment, void.

### Money in the business
**Business cash** shows the running balance (all methods, plus per-method balances) built from:
sale income, restock payments, refunds, operating expenses, owner withdrawals, capital injections
and manual adjustments. System entries (sales/restock/refunds) cannot be deleted; manual ones can.

### Monthly profit close-off
**Monthly close** previews the selected month and locks a snapshot containing:
revenue, discounts, cost of goods, gross profit, operating expenses, net profit, opening/closing
stock value, purchases, cash in/out and cash balance, plus the top products of the month.

Once a month is closed, its sales can no longer be voided. Close at month end for a complete picture —
sales made after closing are not added to that snapshot.

### Reports
Date-range reporting with daily revenue bars, top parts by profit, sales by category, sales by cashier,
largest discounts, cash flow by type and stock valuation. CSV export for sales, products, stock and discounts.

---

## 5. Data, backup and reset

All data lives in one SQLite file:

```
data/mxspares.db      (plus -wal / -shm files while running)
```

* **Backup:** stop the server and copy the `data` folder (or use `VACUUM INTO`).
* **Reset / fresh start:** stop the server, delete `data/mxspares.db*`, start again → setup screen
  appears and the demo catalogue is re-seeded.
* **Move to another PC:** copy the whole project folder, including `data`.

SQLite runs in WAL mode with indexes on every hot query, so searches and receipts stay instant with
tens of thousands of rows.

---

## 6. Settings worth knowing

| Setting | Purpose |
|---|---|
| Business name / address / phone / email | Printed on receipts and quotations |
| Currency symbol | Used everywhere (`$`, `KSh`, `£`, `€`, …) |
| Tax rate + label | 0 hides tax; otherwise added after discount |
| Maximum discount % | Hard ceiling for any discount (default 60%) |
| Low-stock level | Default reorder level for new products |
| Quotation validity / terms | Printed on quotations |
| SMTP host/port/user/password/from | Enables automatic back-in-stock emails (e.g. Gmail app password) |

Use **Send a test email** to verify SMTP before relying on it.

---

## 7. Project layout

```
mx-spares/
├─ app/
│  ├─ main.py           app setup, session middleware, login/setup, dashboard
│  ├─ routes_sales.py   POS, sales, receipts, quotations, discount approvals
│  ├─ routes_admin.py   products, stock, alerts, cash, closings, reports, settings, users
│  ├─ services.py       business rules (sales, stock ledger, discounts, closing maths)
│  ├─ db.py             schema, SQLite/PostgreSQL switch, migrations, seed catalogue, money helpers
│  ├─ security.py       scrypt hashing, sessions, login throttling
│  ├─ pdf.py            ReportLab thermal/A4 receipts and quotations
│  ├─ templates/        Jinja2 pages
│  └─ static/           app.css, app.js, pos.js
├─ data/                SQLite database (created at runtime)
├─ scripts/
│  ├─ migrate_to_postgres.py  copy the SQLite shop data into Neon/Render PostgreSQL
│  ├─ push_to_github.py       upload the source to GitHub (no git install needed)
│  ├─ deploy_render.py        create/update the Render free web service
│  └─ pg_smoke_test.py        35-check end-to-end test for either database backend
├─ .github/workflows/keepalive.yml  pings the free Render app so it never sleeps
├─ render.yaml          Render blueprint (free plan, env vars, start command)
├─ run.py               launcher
├─ start.bat            local Windows launcher
├─ start-online.bat     starts the app + cloudflared tunnel
├─ start-online.ps1     tunnel launcher (domain configurable)
├─ deploy-azure.ps1     Azure App Service deployment (optional)
├─ startup.sh           App Service startup command
└─ requirements.txt
```

---

## 8. Troubleshooting

* **Port already in use** — `python run.py` automatically picks a free port and prints the URL.
  To force one: `python run.py --port 8080`.
* **Receipt printer** — print the 80 mm receipt page (not A4) and disable browser headers/footers.
  The thermal PDF is exactly 80 mm wide.
* **Currency symbol missing in PDF** — the app registers a Unicode Windows/Linux font automatically;
  if none is found it falls back to Helvetica (Latin-1 only).
* **Locked out** — passwords can be reset by an administrator on **Team & access**; if the only admin
  is locked out, stop the server and remove `data/mxspares.db` (this deletes all data).
* **Running for other computers on the shop LAN** — start with `python run.py --host 0.0.0.0` and put
  it behind HTTPS (reverse proxy) before exposing passwords over the network.
