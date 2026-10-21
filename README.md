# Team Riders POS

A fast, self-contained point-of-sale and stock-control system for a motorbike spares business.
Built with **Python + FastAPI** and server-rendered pages. It runs on SQLite out of the box (fast, no
server to install) and switches to PostgreSQL automatically when `DATABASE_URL` is set — which is how it
runs in the cloud, so the shop keeps working when the shop PC is off.

**Live:** <https://www.teamriders.bid>

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

## 2. Where the shop runs

**Live now:** <https://www.teamriders.bid> runs in the cloud on **Render** (Frankfurt, free plan) with the
database on **Neon PostgreSQL**. Nothing on the shop PC needs to be switched on for the shop to work —
you can sell from a phone, a laptop or a café.

| Piece | Where | Notes |
|---|---|---|
| App | Render `teamriders-pos` (free) | `https://teamriders-pos.onrender.com`, custom domains `www.`, `shop.` and the apex |
| Database | Neon `neondb` (free, 0.5 GB) | Connection string lives only in Render's environment variables |
| Code | GitHub `earnestchitsora92-dev/teamriders-pos` | Auto-deploys on every commit to `main` |
| DNS | Cloudflare | `www`, `shop` and apex are CNAMEs to `teamriders-pos.onrender.com` (DNS only) |

Render sleeps a free service after ~15 minutes without traffic (the first visit then takes ~30-60 s).
The repository includes `.github/workflows/keepalive.yml`, which pings `/healthz` every 10 minutes to
prevent that. Any uptime pinger aimed at `https://www.teamriders.bid/healthz` works as well.

### Offline fallback on this PC (Cloudflare Tunnel)

The PC copy still runs on SQLite and can be published through the named Cloudflare tunnel
`teamriders` — useful if the internet at the shop dies, or as a spare till:

```powershell
.\start-online.bat
```

A shortcut named *Team Riders POS* in the Windows Startup folder does this automatically at sign-in
(hidden windows; logs in `logs\`). While DNS points at Render this tunnel is idle; to use it again,
point the Cloudflare CNAMEs back at `5bfc9160-5e58-4aa8-ae7a-4d19316b78a1.cfargotunnel.com` (proxied).

* Data in the fallback lives in `data/mxspares.db` and is **separate** from the cloud database —
  use one or the other for real sales, not both.
* `start.bat` runs it locally only; other devices on the same Wi-Fi can use `http://<this-pc-ip>:8077`.

### Azure App Service (if you ever get an account)

```powershell
az login
.\deploy-azure.ps1
```

`startup.sh` keeps a SQLite database on the persistent `/home/data` volume and switches it to
rollback-journal mode, because WAL is unreliable on Azure's network file share.

---

## 2b. Rebuilding or moving the cloud deployment

Everything below is already done once; keep it for the day you change host, restore a backup or start over.

### Step 1 — Push the code to GitHub

```powershell
$env:GH_TOKEN = "ghp_..."   # token with `repo` (add `workflow` to publish .github/workflows)
python scripts\push_to_github.py --repo teamriders-pos
```

No `git` install is needed: the script uploads the working tree through the GitHub API.

### Step 2 — Create the free PostgreSQL database (Neon)

1. Sign up at <https://neon.tech> (no card).
2. Create a project in the nearest region and copy the **connection string**
   (`postgresql://…@ep-….neon.tech/neondb?sslmode=require`).

### Step 3 — Copy the shop data into Neon

```powershell
$env:DATABASE_URL = "postgresql://…neon.tech/neondb?sslmode=require"
python scripts\migrate_to_postgres.py
```

Every table is copied with its original IDs and the ID sequences are reset, so the next receipt number
continues where the shop left off. Sign-in sessions are not copied.

### Step 4 — Create the Render service

Either use the dashboard (*New → Web Service → Public Git Repository*, Python 3, free plan, build
`pip install -r requirements.txt`, start command below, env var `DATABASE_URL`, health check `/healthz`):

```
uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1 --proxy-headers --forwarded-allow-ips '*'
```

…or with an API key (`RENDER_API_KEY`):

```powershell
python scripts\deploy_render.py --repo-url https://github.com/<you>/teamriders-pos `
    --database-url "postgresql://…neon.tech/neondb?sslmode=require" --domain www.teamriders.bid --wait
```

### Step 5 — Point the domain at Render

In Cloudflare DNS set these CNAMEs (Proxy status **DNS only** so Render can issue certificates):

| Name | Target |
|---|---|
| `www` | `teamriders-pos.onrender.com` |
| `@` | `teamriders-pos.onrender.com` |
| `shop` | `teamriders-pos.onrender.com` |

### Step 6 — Verify

```powershell
python scripts\pg_smoke_test.py            # 35 checks against DATABASE_URL
curl https://www.teamriders.bid/healthz    # {"status":"ok", ...}
```

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
