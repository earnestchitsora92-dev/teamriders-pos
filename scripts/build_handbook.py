"""Build the Team Riders POS handbook (PDF) for shop users and the technical team.

    python scripts/build_handbook.py [output-path]

Everything in the document is read from the shipped source where practical, so the
manual stays truthful when prices, settings or screens change.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "Team-Riders-Handbook.pdf"

BRAND = colors.HexColor("#1d4ed8")
BRAND_DARK = colors.HexColor("#1e3a8a")
ACCENT = colors.HexColor("#f59e0b")
INK = colors.HexColor("#111827")
MUTED = colors.HexColor("#4b5563")
LINE = colors.HexColor("#d6dbe6")
PANEL = colors.HexColor("#eef2ff")
WARN = colors.HexColor("#fff7ed")

LIVE = "https://www.teamriders.bid"
RENDER = "https://teamriders-pos.onrender.com"
REPO = "https://github.com/earnestchitsora92-dev/teamriders-pos"
TODAY = date.today().strftime("%d %B %Y")

# ---------------------------------------------------------------- styles
SS = getSampleStyleSheet()


def style(name: str, **kwargs) -> ParagraphStyle:
    base = dict(fontName="Helvetica", fontSize=9.6, leading=14, textColor=INK, spaceAfter=6, alignment=TA_LEFT)
    base.update(kwargs)
    return ParagraphStyle(name, **base)


BODY = style("Body")
LEAD = style("Lead", fontSize=11, leading=16.5, textColor=MUTED)
H1 = style("H1", fontName="Helvetica-Bold", fontSize=19, leading=23, textColor=BRAND_DARK, spaceBefore=14, spaceAfter=10, keepWithNext=1)
H2 = style("H2", fontName="Helvetica-Bold", fontSize=13, leading=17, textColor=BRAND, spaceBefore=14, spaceAfter=6, keepWithNext=1)
H3 = style("H3", fontName="Helvetica-Bold", fontSize=10.6, leading=14, textColor=INK, spaceBefore=9, spaceAfter=4, keepWithNext=1)
H1_PLAIN = style("H1Plain", fontName="Helvetica-Bold", fontSize=19, leading=23, textColor=BRAND_DARK, spaceBefore=2, spaceAfter=10)
BULLET = style("Bullet", leftIndent=11, bulletIndent=2, spaceAfter=3.5)
SMALL = style("Small", fontSize=8.3, leading=11.6, textColor=MUTED)
MONO = style("Mono", fontName="Courier", fontSize=8.4, leading=11.4, textColor=INK)
CODE = style("Code", fontName="Courier", fontSize=8.2, leading=11.2, textColor=INK, backColor=colors.HexColor("#f3f4f6"),
             borderPadding=6, leftIndent=0, spaceBefore=4, spaceAfter=8)
COVER_T = style("CoverT", fontName="Helvetica-Bold", fontSize=34, leading=38, textColor=colors.white, alignment=TA_CENTER)
COVER_S = style("CoverS", fontSize=13, leading=19, textColor=colors.HexColor("#bfdbfe"), alignment=TA_CENTER)
COVER_M = style("CoverM", fontName="Courier", fontSize=11, leading=16, textColor=colors.white, alignment=TA_CENTER)


def bullet(text: str, mark: str = "•") -> Paragraph:
    return Paragraph(text, BULLET, bulletText=mark)


def numbered(index: int, text: str) -> Paragraph:
    return Paragraph(text, BULLET, bulletText=f"{index}.")


def para(text: str, st: ParagraphStyle = BODY) -> Paragraph:
    return Paragraph(text, st)


def h1(text: str, anchor: str) -> list:
    return [Paragraph(f'<a name="{anchor}"/>{text}', H1), _rule()]


def h2(text: str) -> Paragraph:
    return Paragraph(text, H2)


def h3(text: str) -> Paragraph:
    return Paragraph(text, H3)


def _rule() -> Table:
    tbl = Table([[""]], colWidths=[174 * mm], rowHeights=[1.6])
    tbl.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), ACCENT), ("TOPPADDING", (0, 0), (-1, -1), 0),
                             ("BOTTOMPADDING", (0, 0), (-1, -1), 0), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    return tbl


def gap(pts: float = 6) -> Spacer:
    return Spacer(1, pts)


def table(rows: list[list[str]], widths: list[float], header: bool = True, zebra: bool = True,
          font: float = 8.4, align_right: tuple[int, ...] = ()) -> Table:
    data = [[Paragraph(str(cell), style(f"cell{i}{j}", fontSize=font, leading=font * 1.32, spaceAfter=0,
                                        fontName="Helvetica-Bold" if (header and i == 0) else "Helvetica",
                                        textColor=colors.white if (header and i == 0) else INK))
             for j, cell in enumerate(row)] for i, row in enumerate(rows)]
    tbl = Table(data, colWidths=[w * mm for w in widths], repeatRows=1 if header else 0, hAlign="LEFT")
    cmds = [
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if header:
        cmds.append(("BACKGROUND", (0, 0), (-1, 0), BRAND))
    if zebra:
        start = 1 if header else 0
        for row_index in range(start, len(rows)):
            if (row_index - start) % 2 == 1:
                cmds.append(("BACKGROUND", (0, row_index), (-1, row_index), colors.HexColor("#f8fafc")))
    for col in align_right:
        cmds.append(("ALIGN", (col, 0), (col, -1), "RIGHT"))
    tbl.setStyle(TableStyle(cmds))
    return tbl


def callout(title: str, text: str, kind: str = "info") -> Table:
    bg = PANEL if kind == "info" else WARN
    edge = BRAND if kind == "info" else ACCENT
    content = Paragraph(f"<b>{title}</b><br/>{text}", style("callout", fontSize=8.8, leading=12.6, spaceAfter=0))
    tbl = Table([[content]], colWidths=[174 * mm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("LINEBEFORE", (0, 0), (0, -1), 3, edge),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return tbl


def code(text: str) -> Table:
    lines = text.strip("\n").splitlines()
    body = Paragraph("<br/>".join(line.replace("&", "&amp;").replace("<", "&lt;") for line in lines), MONO)
    tbl = Table([[body]], colWidths=[174 * mm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#0f172a")),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    for i, _ in enumerate(tbl._cellvalues):
        pass
    body.fontName = "Courier"
    body.textColor = colors.HexColor("#e5e7eb")
    return tbl


# ---------------------------------------------------------------- document
class Handbook(BaseDocTemplate):
    def __init__(self, filename: Path, **kwargs):
        super().__init__(str(filename), **kwargs)
        frame = Frame(18 * mm, 21 * mm, 174 * mm, 241 * mm, id="body", showBoundary=0)
        self.addPageTemplates([
            PageTemplate(id="cover", frames=[Frame(0, 0, A4[0], A4[1], id="cover", showBoundary=0)],
                         onPage=self.cover_page),
            PageTemplate(id="content", frames=[frame], onPage=self.content_page),
        ])

    def cover_page(self, canv, doc):
        canv.saveState()
        canv.setFillColor(BRAND_DARK)
        canv.rect(0, 0, A4[0], A4[1], stroke=0, fill=1)
        canv.setFillColor(BRAND)
        canv.rect(0, A4[1] - 120 * mm, A4[0], 120 * mm, stroke=0, fill=1)
        canv.setFillColor(ACCENT)
        canv.rect(0, A4[1] - 124 * mm, A4[0], 4 * mm, stroke=0, fill=1)
        canv.setFillColor(colors.HexColor("#93c5fd"))
        canv.setFont("Helvetica", 9)
        canv.drawCentredString(A4[0] / 2, 20 * mm, "Point of sale · Stock control · Profit tracking")
        canv.restoreState()

    def content_page(self, canv, doc):
        canv.saveState()
        canv.setStrokeColor(LINE)
        canv.setLineWidth(0.5)
        canv.line(18 * mm, 266 * mm, 192 * mm, 266 * mm)
        canv.setFont("Helvetica-Bold", 7.6)
        canv.setFillColor(BRAND_DARK)
        canv.drawString(18 * mm, 269 * mm, "TEAM RIDERS POS — HANDBOOK")
        canv.setFont("Helvetica", 7.6)
        canv.setFillColor(MUTED)
        canv.drawRightString(192 * mm, 269 * mm, TODAY)
        canv.line(18 * mm, 15 * mm, 192 * mm, 15 * mm)
        canv.setFont("Helvetica", 7.6)
        canv.drawString(18 * mm, 10 * mm, f"{LIVE}  ·  prepared {TODAY}")
        canv.drawRightString(192 * mm, 10 * mm, f"Page {doc.page}")
        canv.restoreState()

    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph):
            name = flowable.style.name
            if name in ("H1", "H2"):
                text = flowable.getPlainText()
                level = 0 if name == "H1" else 1
                key = f"toc-{id(flowable)}"
                self.canv.bookmarkPage(key)
                self.canv.addOutlineEntry(text, key, level=level, closed=level > 0)
                self.notify("TOCEntry", (level, text, self.page, key))


def build() -> list:
    story: list = []

    # ------------------------------------------------------------ cover
    story.append(Spacer(1, 46 * mm))
    story.append(Paragraph("Team Riders", COVER_T))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph("Point of Sale &amp; Stock Control", COVER_S))
    story.append(Spacer(1, 26 * mm))
    story.append(Paragraph(LIVE, COVER_M))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("Handbook for the shop team and the technical team", COVER_S))
    story.append(Spacer(1, 62 * mm))
    story.append(Paragraph(f"Issued {TODAY}", COVER_S))
    story.append(NextPageTemplate("content"))
    story.append(PageBreak())

    # ------------------------------------------------------------ contents
    story.append(Paragraph("Contents", H1_PLAIN))
    toc = TableOfContents()
    toc.levelStyles = [
        ParagraphStyle("TOC1", fontName="Helvetica-Bold", fontSize=10.2, leading=15.5, textColor=BRAND_DARK,
                       spaceBefore=6, leftIndent=0, firstLineIndent=0),
        ParagraphStyle("TOC2", fontName="Helvetica", fontSize=8.9, leading=12.4, textColor=INK,
                       leftIndent=14, firstLineIndent=0),
    ]
    toc.dotsMinLevel = 0
    story.append(toc)
    story.append(Spacer(1, 10 * mm))
    story.append(callout(
        "How to use this handbook",
        "Part 1 is for the people behind the counter and in the office — it explains every screen in plain "
        "language. Part 2 is for whoever maintains the system (the shop's IT person or an outside developer) — "
        "it covers hosting, database, deployment, backups and fault-finding. Section 1 is the page to print and "
        "pin next to the till.",
    ))
    story.append(PageBreak())

    # ------------------------------------------------------------ part 1
    story += h1("1. The five-minute version", "s1")
    story.append(para(
        "Team Riders runs on the web at <b>www.teamriders.bid</b>. Any device with a browser — the shop laptop, a "
        "phone, a tablet — can use it, and it keeps working when the shop computer is switched off, because the "
        "application and the database live on the internet, not on the PC."))
    story.append(gap(2))
    story.append(table([
        ["Question", "Answer"],
        ["Where do I open it?", f"{LIVE} — bookmark it. <font face='Courier' size='7.6'>teamriders.bid</font> and "
                                f"<font face='Courier' size='7.6'>shop.teamriders.bid</font> also work."],
        ["How do I sign in?", "Username and password issued by the owner. Cashiers only see the selling screens; "
                              "the owner (admin) sees everything."],
        ["Who approves discounts?", "The admin. A cashier can request one, but the sale cannot use it until it is "
                                    "approved."],
        ["Do I need to install anything?", "No. No software on the PC, no card machine integration, no licence "
                                           "fees. A browser and internet are enough."],
        ["Where does the data live?", "In a hosted PostgreSQL database (Neon), encrypted in transit. The shop PC "
                                      "holds no live copy unless you deliberately use the offline fallback."],
        ["What happens if the internet dies?", "The shop PC can still run the offline copy (see 1.11 and 2.13) — "
                                               "but only one of the two may be used for real sales at a time."],
    ], widths=[42, 132]))
    story.append(gap(8))
    story.append(callout(
        "Accounts for this handover",
        "Administrator: username <b>LOYD</b>, temporary password <b>TR-Cloud-8842</b> — change it on first sign-in "
        "(Account -> Change password). A cashier account named <b>Brandon</b> exists; its password is not known to "
        "the technical team, so reset it from <i>Team &amp; access</i>. Add or remove staff there, never by sharing "
        "one login.",
        kind="warn"))

    story += h1("2. Signing in, roles and safety", "s2")
    story.append(h2("2.1 Sign in and out"))
    story.append(numbered(1, "Open <b>www.teamriders.bid</b> and enter your username and password."))
    story.append(numbered(2, "You land on the dashboard. Cashiers see today's figures for their own selling; the "
                             "admin sees the whole business."))
    story.append(numbered(3, "Sign out with <i>Sign out</i> in the sidebar when you leave the counter — a session "
                             "lasts 12 hours, sliding, so it renews while you work."))
    story.append(numbered(4, "Forgotten password? Only the admin can reset it (Team &amp; access -> user -> Reset "
                             "password)."))
    story.append(gap(3))
    story.append(para("Five wrong passwords in a row lock that username for a short cooling-off period. This is "
                      "deliberate: it stops someone guessing their way into the till. Wait and try again, or ask "
                      "the admin to reset the password.", SMALL))
    story.append(h2("2.2 What each role can do"))
    story.append(table([
        ["Capability", "Cashier", "Admin"],
        ["Search prices, sell, print or save receipts", "Yes", "Yes"],
        ["Create quotations", "Yes", "Yes"],
        ["Capture a customer's e-mail when an item is out of stock", "Yes", "Yes"],
        ["See their own receipts and reprint them (no cost, no profit)", "Yes", "Yes"],
        ["Report a breakage, a customer return or a stock correction", "Request only", "Approve or refuse"],
        ["See their own salary payments", "Yes", "Yes"],
        ["See takings, profit, stock value, cash balance, cost prices", "No", "Yes"],
        ["Give a discount", "Request only", "Approve or refuse"],
        ["Add, edit, delete or archive products", "No", "Yes"],
        ["Restock, adjust stock, read the stock ledger", "No", "Yes"],
        ["Business cash ledger and paying salaries", "No", "Yes"],
        ["Monthly close-off, reports, CSV exports", "No", "Yes"],
        ["Team &amp; access (create, disable, delete users)", "No", "Yes"],
        ["Activity log and settings", "No", "Yes"],
    ], widths=[104, 33, 37]))
    story.append(gap(6))
    story.append(callout(
        "Why cashiers cannot see profit",
        "A cashier's screens deliberately show no takings, no profit, no stock value and no cash balance — not even as "
        "totals. They see the till, their own receipts, their own discount requests and the status of the breakages or "
        "returns they reported. Cost prices, margins and every business figure stay with the admin, and the server "
        "refuses those pages even if the address is typed by hand.",
    ))

    story += h1("3. Selling at the counter", "s3")
    story.append(h2("3.1 The Point of Sale screen"))
    story.append(para("The till is <b>Point of Sale</b> in the sidebar. Everything below happens without leaving "
                      "that page."))
    story.append(numbered(1, "<b>Search</b> — start typing a name, part number, brand or bike model in the box at "
                             "the top. A suggestion list opens as you type, showing SKU, name, <b>price</b> and how "
                             "many units are in stock, so you can answer “how much is this?” without touching the "
                             "till."))
    story.append(numbered(2, "<b>Add to cart</b> — click a suggestion (or press Enter on the highlighted row). "
                             "Repeat for each item; the cart on the right totals itself."))
    story.append(numbered(3, "<b>Quantities</b> — change the quantity in the cart, or remove a line with the "
                             "cross. Up to 999 units per line."))
    story.append(numbered(4, "<b>Customer details (optional)</b> — name, phone and e-mail are stored on the sale "
                             "and printed on the receipt; they also let the shop contact the customer later."))
    story.append(numbered(5, "<b>Discount</b> — see 3.2. Leave it empty for a normal sale."))
    story.append(numbered(6, "<b>Payment</b> — choose cash, mobile money, card or bank transfer. For cash, type "
                             "what the customer handed over and the change is worked out for you."))
    story.append(numbered(7, "<b>Complete sale</b> — stock is reduced, the receipt is numbered and you are taken to "
                             "the receipt, ready to print."))
    story.append(gap(3))
    story.append(callout(
        "What the system refuses to do",
        "You cannot sell more units than the shelf holds — the till stops with “Not enough stock for …”. You cannot "
        "take less cash than the total. Nothing is ever sold at a negative price. These rules protect the stock "
        "count, so do not fight them: adjust the stock or ask the admin.",
        kind="warn"))
    story.append(h2("3.2 Discounts: proposed by the cashier, authorised by the admin"))
    story.append(table([
        ["Step", "What happens"],
        ["1", "On the sale, choose <b>percentage</b> or <b>fixed amount</b>, type the value and — mandatory — a "
              "<b>reason</b> (" + "“loyalty”" + ", “damaged box”, “price match”)."],
        ["2", "A cashier's request is created as <b>pending</b> with its own code (e.g. <font face='Courier' "
              "size='7.6'>DSC-15FFFC</font>). The sale cannot use it yet."],
        ["3", "The admin opens <i>Discounts</i>, reads the reason and the amount, and approves or refuses with an "
              "optional note."],
        ["4", "Once approved, the code is valid for <b>30 minutes</b> and is applied to the next sale that uses it."],
        ["5", "After the sale the discount becomes <b>used</b> and is linked to that receipt. It cannot be reused."],
    ], widths=[13, 161]))
    story.append(gap(5))
    story.append(para("<b>House rules the system enforces:</b> a reason is always required; a percentage discount "
                      "may not exceed the limit in Settings (60% by default); an approved discount expires after 30 "
                      "minutes; an unapproved request lapses after 24 hours. Unused, refused and expired attempts "
                      "stay in the log, so discounting can always be audited.", SMALL))
    story.append(para("<b>When the admin is at the till:</b> the admin can approve inline while selling, which "
                      "issues an already-approved code valid for 30 minutes.", SMALL))

    story.append(h2("3.3 Quotations"))
    story.append(numbered(1, "Switch the till to <b>Quote</b> mode (the payment block disappears)."))
    story.append(numbered(2, "Search and add the parts exactly as for a sale, include customer details, apply an "
                             "approved discount if the customer has one."))
    story.append(numbered(3, "Save the quotation — it is numbered <font face='Courier' size='7.6'>QTN-YYYYMM-0001"
                             "</font> and marked as a draft."))
    story.append(numbered(4, "Open it to download the A4 PDF, print it, or e-mail it to the customer yourself."))
    story.append(numbered(5, "Move it along as the conversation develops: <b>draft -> sent -> accepted / rejected / "
                             "cancelled</b>. Quotations past their validity window are marked <b>expired</b>."))
    story.append(gap(2))
    story.append(para("A quotation never touches stock and never takes money — it is a priced offer. Two settings "
                      "control it: validity in days (14 by default) and the terms paragraph printed at the foot of "
                      "the PDF.", SMALL))
    story.append(h2("3.4 Receipts: paper, PDF, or both"))
    story.append(bullet("<b>Thermal (default)</b> — laid out for an 80 mm receipt printer. Use the browser's print "
                        "dialogue and choose the receipt printer."))
    story.append(bullet("<b>A4</b> — add <font face='Courier' size='7.6'>?size=a4</font> to the receipt address, or "
                        "pick A4 in the receipt page, for an office printer or a PDF by e-mail."))
    story.append(bullet("<b>Save as PDF</b> — every receipt and quotation is a real PDF generated by the system, so "
                        "“Save as PDF” in the print dialogue always produces a clean file."))
    story.append(bullet("<b>Reprint</b> — <i>Sales</i> lists every sale; open one to reprint or download it. A "
                        "cashier sees only their own sales, the admin sees all."))
    story.append(gap(2))
    story.append(para("Receipt numbers are sequential per month (<font face='Courier' size='7.6'>RCP-202610-0007"
                      "</font>) and are never reused, even when a sale is voided — a voided sale keeps its number "
                      "and is marked <b>voided</b> in the sales list.", SMALL))
    story.append(h2("3.5 Wrong sale? Void it — don't delete it"))
    story.append(para("The admin can void a completed sale from the receipt page, with a reason. Voiding puts the "
                      "stock back on the shelf, records the refund in the cash ledger and keeps the audit trail. "
                      "A sale in a month that has already been closed cannot be voided — that month's figures are "
                      "locked."))
    story.append(h2("3.6 Out of stock: capture the customer, then tell them when it lands"))
    story.append(numbered(1, "When an item shows <b>0 in stock</b>, the till offers <i>Not in stock? Take a "
                             "customer's e-mail</i>."))
    story.append(numbered(2, "Enter the e-mail (name, phone and a note are optional). The customer joins that "
                             "part's waiting list."))
    story.append(numbered(3, "The admin sees every waiting customer under <i>Back-in-stock</i>, and is also warned "
                             "on the restock screen."))
    story.append(numbered(4, "When stock arrives the system can e-mail the waiting customers automatically — that "
                             "requires the shop's e-mail account to be configured once in Settings (SMTP). "
                             "Without it, notify each customer with one click from that page."))
    story.append(gap(4))
    story.append(callout(
        "Set up e-mail once and it runs itself",
        "Settings -> Outgoing e-mail: server, port (587), username, password, from-address, TLS on. Use <i>Send test "
        "e-mail</i> to prove it works. With that in place, restocking a part e-mails everyone on its waiting list; "
        "without it you simply press <i>Notify</i> for each customer when you are ready.",
    ))

    story += h1("4. Stock, purchasing and what the shop is worth", "s4")
    story.append(h2("4.1 Products"))
    story.append(para("Every part has a SKU, name, category, brand, compatible bike models, <b>selling price</b>, "
                      "<b>cost price</b> (admin only), stock on hand, reorder level, an optional note and an "
                      "active/archived flag. Archive rather than delete: archived parts vanish from the till but keep "
                      "their history, so old receipts still make sense."))
    story.append(h2("4.2 Stock levels and reorder warnings"))
    story.append(bullet("<b>Stock levels</b> shows every part with its quantity, reorder level and a flag when the "
                        "quantity is at or below the reorder level, plus total units and the value of the shelf."))
    story.append(bullet("<b>Low stock</b> appears on the dashboard, so the admin sees what to buy without hunting."))
    story.append(bullet("<b>Stock ledger</b> is the complete, append-only history: opening stock, sales, restocks, "
                        "adjustments, voids — each line with quantity, cost, resulting quantity, reference, user and "
                        "timestamp. Nothing is edited or deleted in the ledger; that is what makes stock disputes "
                        "answerable."))
    story.append(h2("4.3 Restocking, and paying for it out of the business"))
    story.append(numbered(1, "Stock -> <b>Restock</b>: choose the part, quantity, the <b>unit cost you actually "
                             "paid</b>, supplier and a note."))
    story.append(numbered(2, "Tick <b>Pay from business cash</b> (and the method: cash, bank or mobile money) if "
                             "the money came out of the till or the shop account. The expense is logged automatically "
                             "so the cash balance stays honest."))
    story.append(numbered(3, "Save. On-hand quantity rises, the average cost is recalculated, the ledger records "
                             "the movement, and any waiting customers are queued for an e-mail."))
    story.append(gap(2))
    story.append(para("The system keeps a <b>moving average cost</b>: buy 10 units at 200.00 and 5 at 240.00 and the "
                      "cost used for profit becomes 213.33 per unit. That is why a restock at a new price quietly "
                      "corrects the profit figures of the next sale — and why the unit cost must be typed honestly "
                      "rather than guessed.", SMALL))
    story.append(h2("4.4 Adjustments: breakage, theft, stock-take differences"))
    story.append(para("Stock -> <b>Adjust</b> takes a signed quantity and a reason (“damaged in store”, “count "
                      "correction”). It never silently changes a price and it always appears in the ledger, so a "
                      "shrinkage pattern is visible instead of hidden."))
    story.append(h2("4.5 Breakages, returns and stock requests"))
    story.append(para("A cashier cannot change stock, but they are the person who sees the damage or takes the return. "
                      "So the shop runs on <b>requests</b>: anyone raises the change, the admin approves it, and only "
                      "then does the shelf move."))
    story.append(table([
        ["Request type", "What it does on approval", "Typical use"],
        ["Breakage / write-off", "Takes the quantity out of stock and records the loss at cost",
         "A part dropped while fitting, a damaged tyre, a cracked mirror"],
        ["Customer return", "Puts the quantity back into stock and, if you enter an amount, pays that refund out of "
                            "business cash", "Wrong size bought yesterday, warranty replacement"],
        ["Stock correction", "Adds or removes a quantity", "Stock-take found three more or two fewer than the system"],
    ], widths=[34, 74, 66]))
    story.append(gap(4))
    story.append(numbered(1, "Open <b>Stock requests</b> in the sidebar (cashiers and admins both see it)."))
    story.append(numbered(2, "Choose the part, what happened, the quantity and a reason — the reason is mandatory and "
                             "is copied onto the stock ledger."))
    story.append(numbered(3, "Press <b>Send for approval</b>. The request waits as <b>pending</b>; stock is untouched."))
    story.append(numbered(4, "The admin opens the same page and presses <b>Approve</b> (optionally adding a refund and a "
                             "note) or <b>Refuse</b>. The pending count also appears on the dashboard and in the "
                             "sidebar badge."))
    story.append(numbered(5, "Approval writes the ledger line, updates the quantity and, for a return with a refund, "
                             "posts the money out of the cash ledger."))
    story.append(gap(3))
    story.append(callout(
        "Why ask instead of allowing it",
        "Every approved breakage is a cost the owner can see in the monthly report, and a refused request keeps its "
        "reason and the admin's note on file. That is what stops stock quietly disappearing between counts.",
    ))
    story.append(h2("4.6 What the stock is worth"))
    story.append(table([
        ["Figure", "Where to find it", "What it means"],
        ["Stock at cost", "Dashboard / Stock levels", "Money tied up in the shelf, valued at cost price."],
        ["Stock at retail", "Stock levels", "What the shelf would bring in at today's prices."],
        ["Potential margin", "Stock levels", "Retail value minus cost value."],
        ["Units and SKUs", "Stock levels", "How many pieces, how many different parts."],
    ], widths=[34, 47, 93]))

    story += h1("5. Money, profit and the monthly close", "s5")
    story.append(h2("5.1 Business cash"))
    story.append(para("<b>Business cash</b> is the running record of money moving in and out of the business that "
                      "is not a sale. Every entry needs a kind, an amount, a method (cash, bank, mobile money) and a "
                      "note; entries can be corrected by an adjustment with a reason."))
    story.append(table([
        ["Kind", "Direction", "Use it for"],
        ["Operating expense", "Money out", "Rent, fuel, workshop consumables, airtime."],
        ["Salary paid", "Money out", "Wages paid to a team member from the Salaries page (automatic)."],
        ["Owner withdrawal", "Money out", "Money the owner takes out of the business."],
        ["Capital injection", "Money in", "Money the owner puts in."],
        ["Adjustment", "Money in", "Corrections after a physical cash count."],
        ["Sale income", "Money in", "Created automatically by every completed sale."],
        ["Refund (voided sale)", "Money out", "Created automatically when a sale is voided."],
        ["Customer return refund", "Money out", "Created automatically when a return with a refund is approved."],
    ], widths=[34, 22, 118]))
    story.append(gap(4))
    story.append(para("The balance shown is money in minus money out, overall and per method. That single number is "
                      "the answer to “how much money is in the business?” — and because sales and restock payments "
                      "post automatically, it only stays true if every manual payment is logged as it happens.", SMALL))
    story.append(h2("5.2 How profit is calculated"))
    story.append(bullet("<b>Revenue</b> — what the customer paid, after discounts, including tax."))
    story.append(bullet("<b>Cost of goods sold (COGS)</b> — the average cost of the units on that receipt, captured "
                        "at the moment of sale."))
    story.append(bullet("<b>Gross profit</b> — revenue (excluding tax) minus COGS. Shown per sale, per part, per "
                        "category, per cashier and for any date range."))
    story.append(bullet("<b>Net profit</b> — gross profit minus the operating expenses logged against the period, "
                        "<b>salaries included</b>."))
    story.append(bullet("<b>Discounts</b> are reported separately, so the owner can see exactly what discounting "
                        "cost, who approved it and why."))
    story.append(bullet("<b>Breakages</b> are shown at cost as their own figure, and <b>returns</b> show how many "
                        "units came back — both kept out of the profit arithmetic so the numbers stay easy to explain."))
    story.append(h2("5.3 Paying salaries"))
    story.append(numbered(1, "Open <b>Salaries</b>. The page shows the month's total paid, the current business cash "
                             "balance and every payment already made."))
    story.append(numbered(2, "Choose the person, the amount, the month it is for, whether it was paid by cash, bank or "
                             "mobile money, and an optional note."))
    story.append(numbered(3, "Press <b>Pay from business cash</b>. The money leaves the cash ledger immediately as "
                             "<i>Salary paid</i> (reference such as <font face='Courier' size='7.6'>SAL-202610-002"
                             "</font>), counts as an expense in the monthly close, and appears on that person's own "
                             "Account page."))
    story.append(numbered(4, "A salary payment cannot be deleted from the cash ledger; if one is wrong, pay a "
                             "correcting adjustment and note why."))
    story.append(h2("5.4 The monthly close"))
    story.append(numbered(1, "<b>Monthly close -> Close this month.</b> The system freezes that month's figures into "
                             "a closing record: sales count, revenue, discounts, tax, COGS, gross profit, expenses, "
                             "net profit, purchases paid from cash, voided sales and the best-selling parts."))
    story.append(numbered(2, "The month becomes read-only for sales corrections: receipts can still be printed, "
                             "but sales in that month can no longer be voided."))
    story.append(numbered(3, "Every closing stays on file as a PDF-ready report (open it from <i>Monthly close</i>) "
                             "for the accountant or the bank."))
    story.append(gap(3))
    story.append(callout(
        "Close the month before you file the cash away",
        "Closing is what turns daily numbers into a monthly record that cannot drift. Do it on the first working day "
        "after month end: open <i>Monthly close</i>, check revenue, COGS, gross and net against the drawer and the "
        "bank, then close and print the report.",
        kind="warn"))
    story.append(h2("5.5 Reports and exports"))
    story.append(table([
        ["Report", "Answers"],
        ["Sales in a date range", "Every receipt: number, date, cashier, customer, subtotal, discount, tax, total, "
                                  "profit, status."],
        ["Top performing parts", "Which parts earn the most profit and the most revenue in the period."],
        ["Category performance", "Where the money comes from by part category."],
        ["By cashier", "Who sold how much, and how much they discounted."],
        ["Discount report", "Every request: who asked, who approved, amount, reason, status."],
        ["Stock valuation", "Units and value at cost and at retail, with low-stock flags."],
        ["Cash movement", "Money in and out by kind and method, with the running balance."],
    ], widths=[46, 128]))
    story.append(gap(4))
    story.append(para("<b>CSV exports</b> (sales, products, stock, discounts) open directly in Excel or Google "
                      "Sheets — useful for the accountant, an insurer or a stock-take reconciliation.", SMALL))

    story += h1("6. Admin settings and the audit trail", "s6")
    story.append(h2("6.1 Settings worth reviewing once"))
    story.append(table([
        ["Setting", "Why it matters"],
        ["Business name, tagline, address, phone, e-mail", "Printed on every receipt and quotation — it is the "
                                                           "shop's stationery."],
        ["Currency symbol", "Used throughout the app and on PDFs."],
        ["Tax rate and label (VAT / none)", "Set to 0 until the business is VAT-registered; then the tax line "
                                            "appears on receipts and in the profit reports."],
        ["Maximum discount %", "The ceiling a cashier or admin may request (default 60%)."],
        ["Low-stock threshold", "The default reorder level used for new parts."],
        ["Receipt footer", "The thank-you line at the bottom of the paper receipt."],
        ["Quotation validity (days) and terms", "Printed on every quotation PDF."],
        ["Outgoing e-mail (SMTP)", "Powers the automatic back-in-stock e-mails."],
    ], widths=[52, 122]))
    story.append(h2("6.2 Team &amp; access"))
    story.append(bullet("Create a user with a role (<b>cashier</b> or <b>admin</b>), a full name and a starting "
                        "password — one login per person, never a shared one."))
    story.append(bullet("Reset a forgotten password, or deactivate a user who has left. Deactivated users cannot "
                        "sign in but their sales stay in the records."))
    story.append(bullet("<b>Delete</b> removes the account completely. If the person already has sales, quotations, "
                        "discount decisions or salary payments, those rows are kept and the account is anonymised "
                        "instead, so old receipts and payroll still make sense. A deleted user cannot sign in and "
                        "disappears from the team list."))
    story.append(bullet("Passwords are stored only as scrypt hashes. Nobody — including the technical team — can read "
                        "an existing password; the only options are reset or deactivate."))
    story.append(h2("6.3 Activity log"))
    story.append(para("The audit trail records sign-ins and failed sign-ins, sales and voids, restocks and "
                      "adjustments, discount requests and decisions, cash entries, settings changes and user "
                      "changes — with the user, the timestamp, the affected record and the IP address. When a figure "
                      "looks wrong, this is the first place to look."))
    story.append(h2("6.4 Daily, weekly, monthly"))
    story.append(table([
        ["When", "Do this"],
        ["Start of day", "Sign in, check the dashboard for low stock and pending discounts, confirm the drawer float."],
        ["Every sale", "Search, add, discount (if authorised), take payment, hand over the receipt."],
        ["When stock lands", "Restock with the real unit cost; tick <i>pay from business cash</i> if you paid from "
                             "the business; notify waiting customers."],
        ["End of day", "Compare the drawer with <i>Business cash</i> and log any expense or withdrawal you made."],
        ["Weekly", "Review low stock, send quotations that are going cold, look at the discount report."],
        ["Monthly", "Close the month, print the closing report, export the CSVs for the accountant, back up the "
                    "database (see 2.9)."],
    ], widths=[30, 144]))
    story.append(PageBreak())

    # ------------------------------------------------------------ part 2
    story += h1("7. Technical overview", "s7")
    story.append(para("The application is a single Python web service with server-rendered HTML. There is no "
                      "JavaScript framework, no build step and no separate API tier, which is why it starts in a "
                      "second and stays cheap to run."))
    story.append(table([
        ["Layer", "Technology"],
        ["Web framework", "FastAPI (Python 3.12 on the server) with Uvicorn"],
        ["Pages", "Jinja2 templates, one CSS file and two small JavaScript files (no bundler)"],
        ["Business logic", "services.py — sales, stock ledger, discounts, cash, closings, analytics"],
        ["PDF", "ReportLab (80 mm thermal + A4 receipts, A4 quotations)"],
        ["Database (cloud)", "PostgreSQL 18 on Neon, accessed with psycopg 3 and a psycopg_pool connection pool"],
        ["Database (local)", "SQLite in WAL mode — the offline fallback on the shop PC"],
        ["Hosting", "Render free web service, Frankfurt (EU Central)"],
        ["DNS / TLS", "Cloudflare DNS, certificates issued and renewed by Render"],
        ["CI / ops", "GitHub repository with auto-deploy on <font face='Courier' size='7.6'>main</font> and a "
                     "10-minute keep-alive Action"],
    ], widths=[34, 140]))
    story.append(gap(6))
    story.append(h2("7.1 Where things live"))
    story.append(table([
        ["Environment", "Address", "Notes"],
        ["Production", LIVE, "Primary address. Apex and <font face='Courier' size='7.6'>shop.</font> redirect to "
                             "<font face='Courier' size='7.6'>www</font>."],
        ["Render origin", RENDER, "The service itself; works even if DNS is changed."],
        ["Source code", REPO, "Public repository, <font face='Courier' size='7.6'>main</font> branch is production."],
        ["Cloud database", "Neon project <b>teamriders</b>, database <b>neondb</b>",
         "Connection string exists only in Render's environment variables."],
        ["Offline fallback", "<font face='Courier' size='7.6'>http://127.0.0.1:8077</font> on the shop PC",
         "SQLite file <font face='Courier' size='7.6'>data/mxspares.db</font>; Cloudflare tunnel "
         "<font face='Courier' size='7.6'>teamriders</font> can publish it again."],
    ], widths=[26, 62, 86]))
    story.append(gap(4))
    story.append(para("At handover (21 October 2026) the cloud database held the shop catalogue of 27 parts, two "
                      "existing sales, one closed month and two user accounts (one admin, one cashier). Anything higher "
                      "than that means the shop has been trading — which is the point.", SMALL))
    story.append(h2("7.2 How a request flows"))
    story.append(code(
        "browser  ->  Cloudflare DNS (www.teamriders.bid, DNS-only CNAME)\n"
        "         ->  Render edge (TLS terminated, X-Forwarded-Proto: https)\n"
        "         ->  uvicorn  app.main:app   (1 worker, $PORT)\n"
        "                 middleware: session cookie -> DB lookup, CSRF token, role gate\n"
        "                 route      -> services.py -> db.py\n"
        "                 db.py      -> psycopg_pool -> Neon PostgreSQL (TLS)\n"
        "         <-  HTML / JSON / application/pdf"))
    story.append(h2("7.3 Repository layout"))
    story.append(table([
        ["Path", "Purpose"],
        ["app/main.py", "App creation, session + CSRF middleware, login, setup, dashboard, health check"],
        ["app/routes_sales.py", "POS, sales, receipts and PDFs, quotations, discount requests and decisions"],
        ["app/routes_admin.py", "Products, stock, alerts, cash, closings, reports/CSV, settings, users, audit"],
        ["app/services.py", "All business rules and the SQL that implements them"],
        ["app/db.py", "Schema, backend switch (SQLite / PostgreSQL), settings cache, seed catalogue, money helpers"],
        ["app/security.py", "scrypt hashing, session tokens, login throttling, admin re-authentication"],
        ["app/pdf.py", "ReportLab receipts (thermal/A4) and quotation PDFs"],
        ["app/templates, app/static", "Jinja2 pages, app.css, app.js, pos.js"],
        ["scripts/", "migrate_to_postgres.py, push_to_github.py, deploy_render.py, pg_smoke_test.py, "
                     "build_handbook.py"],
        [".github/workflows/keepalive.yml", "Pings /healthz every 10 minutes so the free instance never sleeps"],
        ["render.yaml", "Blueprint equivalent of the live service (build, start, env vars, health check)"],
        ["data/", "SQLite database for the offline fallback (git-ignored)"],
    ], widths=[52, 122]))

    story += h1("8. Database, data model and integrity rules", "s8")
    story.append(para("The same schema is created for both backends; only the primary-key syntax differs "
                      "(<font face='Courier' size='7.6'>SERIAL</font> in PostgreSQL, "
                      "<font face='Courier' size='7.6'>INTEGER PRIMARY KEY AUTOINCREMENT</font> in SQLite)."))
    story.append(h2("8.1 Tables"))
    story.append(table([
        ["Table", "Holds"],
        ["users", "Accounts: username, full name, role (admin/cashier), scrypt hash, active flag, last sign-in"],
        ["sessions", "One row per signed-in device: token hash, CSRF secret, expiry, IP, user agent"],
        ["products", "Part master data: SKU, name, category, brand, compatible models, price, cost, stock, reorder "
                     "level, active flag"],
        ["stock_movements", "Append-only ledger: kind (initial/sale/restock/adjustment/void), quantity delta, unit "
                            "cost, resulting quantity, reference, user"],
        ["discounts", "Every request: code, kind, value, subtotal, amount, reason, requested_by, approved_by, "
                      "status, expiry, sale_id"],
        ["sales", "Receipt header: number, cashier, customer, subtotal, discount, tax, total, COGS, profit, payment "
                  "method, tendered, change, status, void details"],
        ["sale_items", "Receipt lines: product snapshot (SKU, name), quantity, unit price, unit cost, line total"],
        ["quotations", "Quote header: number, customer, totals, validity, status, created_by"],
        ["quotation_items", "Quote lines (same shape as sale items)"],
        ["stock_alerts", "Back-in-stock waiting list: product, customer e-mail/name/phone, note, status, notified_at"],
        ["cash_transactions", "Money in/out: kind, amount, method, reference, note, user, timestamp"],
        ["payroll", "Salary payments: person, month, amount, method, reference (SAL-YYYYMM-NNN), who paid it"],
        ["stock_requests", "Breakage / return / correction requests awaiting or carrying an admin decision, with the "
                           "reason, direction, refund and decision note"],
        ["closings", "One row per closed month: the frozen figures plus a JSON snapshot and notes"],
        ["sequences", "Counters behind receipt and quotation numbers (per month)"],
        ["settings", "Key/value business configuration (see Appendix B)"],
        ["audit_log", "Who did what, when, from which IP"],
    ], widths=[34, 140], font=8.1))
    story.append(h2("8.2 Non-negotiable rules in the code"))
    story.append(bullet("<b>Money is integer cents</b> everywhere (<font face='Courier' size='7.6'>price_cents"
                        "</font>, <font face='Courier' size='7.6'>total_cents</font>, …). No floats, no rounding "
                        "drift; percentages are rounded half-up."))
    story.append(bullet("<b>Stock only moves through the ledger.</b> Selling, voiding, restocking and adjusting all "
                        "write a <font face='Courier' size='7.6'>stock_movements</font> row and update the product "
                        "quantity in the same transaction."))
    story.append(bullet("<b>Costs use a moving average</b> — a restock blends the new unit cost into the existing "
                        "average, which is what makes per-sale profit honest."))
    story.append(bullet("<b>Receipt and quotation numbers</b> come from the <font face='Courier' size='7.6'>"
                        "sequences</font> table inside the same transaction, so two cashiers can never take the same "
                        "number."))
    story.append(bullet("<b>A closed month is frozen</b>: <font face='Courier' size='7.6'>void_sale</font> refuses "
                        "any receipt whose month has a closing record."))
    story.append(bullet("<b>Stock changes need a decision.</b> A request only touches the shelf when an admin approves it; "
                        "the ledger line, the new quantity and any refund are written in one transaction."))
    story.append(bullet("<b>Salary payments are cash events.</b> Inserting a payroll row and its "
                        "<font face='Courier' size='7.6'>cash_transactions</font> entry happens together, so the "
                        "balance can never drift from the payment list."))
    story.append(bullet("<b>Users with history are anonymised, not erased.</b> Deleting keeps the row (and every "
                        "receipt it authorises) while scrambling the password and hiding the account."))
    story.append(bullet("<b>Deletes are avoided.</b> Products are archived, sales are voided, cash entries are "
                        "corrected by an adjustment — the history remains complete."))
    story.append(h2("8.3 How one backend flag changes everything"))
    story.append(para("At import time <font face='Courier' size='7.6'>app/db.py</font> reads "
                      "<font face='Courier' size='7.6'>DATABASE_URL</font>. If it is set (and looks like a PostgreSQL "
                      "URL) the app talks to PostgreSQL; otherwise it opens the SQLite file. Because the entire "
                      "codebase is written in SQLite's dialect, the PostgreSQL path is provided by a thin adapter:"))
    story.append(table([
        ["Difference", "How it is handled"],
        ["Placeholders", "<font face='Courier' size='7.6'>?</font> is translated to <font face='Courier' size='7.6'>"
                         "%s</font> on the way in."],
        ["Insert IDs", "<font face='Courier' size='7.6'>RETURNING id</font> is appended to inserts into ID tables so "
                       "<font face='Courier' size='7.6'>cursor.lastrowid</font> keeps working."],
        ["Row access", "Rows are dicts that also allow sqlite3-style positional access (<font face='Courier' "
                       "size='7.6'>row[0]</font>)."],
        ["Numerics", "PostgreSQL returns <font face='Courier' size='7.6'>Decimal</font> for aggregates; these are "
                     "normalised to int/float so JSON and templates behave identically."],
        ["Dialect", "Grouped columns are fully qualified and alias-based ORDER BY expressions were rewritten as "
                    "sub-queries."],
        ["Connections", "A psycopg pool (min 1, max 6, 15 s timeout) with per-request checkout/return and rollback."],
    ], widths=[34, 140]))

    story += h1("9. Deployment, configuration and operations", "s9")
    story.append(h2("9.1 Configuration (environment variables)"))
    story.append(table([
        ["Variable", "Value in production", "Purpose"],
        ["DATABASE_URL", "<font face='Courier' size='7.4'>postgresql://…neon.tech/neondb?sslmode=require</font>",
         "Switches the app to PostgreSQL. Set only in Render (Secret)."],
        ["PYTHON_VERSION", "3.12.7", "Interpreter Render builds with."],
        ["PYTHONUNBUFFERED", "1", "Live logs."],
        ["MX_SQLITE_JOURNAL", "delete", "Ignored when PostgreSQL is used; kept for the SQLite fallback."],
        ["PORT", "injected by Render", "Uvicorn must bind <font face='Courier' size='7.6'>0.0.0.0:$PORT</font>."],
    ], widths=[36, 62, 76], font=8.1))
    story.append(gap(4))
    story.append(para("Build command <font face='Courier' size='7.6'>pip install --upgrade pip &amp;&amp; pip install -r "
                      "requirements.txt</font>; start command <font face='Courier' size='7.6'>uvicorn app.main:app "
                      "--host 0.0.0.0 --port $PORT --workers 1 --proxy-headers --forwarded-allow-ips '*'</font>; "
                      "health check <font face='Courier' size='7.6'>/healthz</font>.", SMALL))
    story.append(h2("9.2 The change process"))
    story.append(numbered(1, "Edit the code and test locally (<font face='Courier' size='7.6'>python run.py</font>, "
                             "then <font face='Courier' size='7.6'>python scripts/pg_smoke_test.py</font>)."))
    story.append(numbered(2, "Publish with <font face='Courier' size='7.6'>python scripts/push_to_github.py --repo "
                             "teamriders-pos --message \"…\"</font> (no git install needed; the script uploads the "
                             "tree through the GitHub API)."))
    story.append(numbered(3, "Render pulls the new commit and rebuilds. Because the service reads a <i>public "
                             "repository URL</i> rather than a connected Git provider, the push alone does not start a "
                             "build: trigger it with <font face='Courier' size='7.6'>python scripts/deploy_render.py "
                             "--repo-url &lt;repo&gt;</font> (it merges the existing environment variables, so "
                             "DATABASE_URL is kept) or press <i>Manual Deploy</i> in the dashboard."))
    story.append(numbered(4, "Verify: <font face='Courier' size='7.6'>curl https://www.teamriders.bid/healthz</font> "
                             "and sign in."))
    story.append(numbered(5, "Roll back by choosing the previous successful deploy in Render and clicking "
                             "<i>Redeploy</i>."))
    story.append(h2("9.3 Handy scripts"))
    story.append(table([
        ["Script", "What it does"],
        ["scripts/push_to_github.py", "Creates/updates the GitHub repository from the working tree via the API."],
        ["scripts/deploy_render.py", "Creates or updates the Render service, merges env vars, attaches domains and "
                                     "triggers a deploy (needs RENDER_API_KEY)."],
        ["scripts/migrate_to_postgres.py", "Copies a SQLite database into PostgreSQL, preserving IDs and resetting "
                                           "sequences; skips sessions."],
        ["scripts/pg_smoke_test.py", "55 service-layer checks (auth, products, stock, discounts, sales, quotations, "
                                     "cash, closings, reports, alerts, stock requests, salaries, deletions) against "
                                     "whichever backend DATABASE_URL selects."],
        ["scripts/http_smoke_test.py", "38 HTTP checks against a running instance: setup, logins, cashier restrictions, "
                                       "stock approval, salary payment, user and product deletion."],
        ["scripts/build_handbook.py", "Regenerates this PDF from the current source tree."],
    ], widths=[52, 122]))

    story.append(h2("9.4 Backups and restore"))
    story.append(table([
        ["What", "How", "How often"],
        ["Cloud database", "Neon console -> <i>Backups / Restore</i> (point-in-time per branch), or "
                           "<font face='Courier' size='7.6'>pg_dump \"$DATABASE_URL\" -Fc &gt; teamriders.dump</font>",
         "Weekly dump; Neon's own history covers the rest"],
        ["Offline SQLite copy", "Copy <font face='Courier' size='7.6'>data/mxspares.db</font> while the PC app is "
                                "stopped", "Whenever it is used in earnest"],
        ["Accountant's figures", "Reports -> CSV exports (sales, products, stock, discounts) and the monthly closing "
                                 "PDF", "Monthly, with the close"],
        ["Source code", "GitHub repository (public) — every commit is a restorable version", "Automatic"],
    ], widths=[30, 108, 36], font=8.1))
    story.append(gap(4))
    story.append(callout(
        "Restoring PostgreSQL",
        "Create an empty database, run <font face='Courier' size='7.6'>pg_restore -d \"$DATABASE_URL\" "
        "--no-owner teamriders.dump</font>, then point Render's DATABASE_URL at it. The schema is created "
        "automatically by the app on first start, so restoring data alone is enough if you have a plain SQL dump.",
    ))
    story.append(h2("9.5 Testing before and after a change"))
    story.append(code(
        "# service layer on the cloud database\n"
        "$env:DATABASE_URL = \"postgresql://…neon.tech/neondb?sslmode=require\"\n"
        "python scripts/pg_smoke_test.py          # expect: 35/35 checks passed\n"
        "\n"
        "# same suite on the offline SQLite copy\n"
        "$env:DATABASE_URL = \"\"\n"
        "python scripts/pg_smoke_test.py          # expect: 35/35 checks passed"))
    story.append(para("The suite creates its own users, products, discounts, sales, quotation, cash entry and "
                      "closing, so run it against a scratch database (or accept the extra test rows). Never point it at "
                      "the live database in business hours without telling the shop.", SMALL))
    story.append(h2("9.6 Keeping the free instance awake"))
    story.append(para("Render's free plan stops an idle service after about 15 minutes; the next visitor then waits "
                      "30–60 seconds. <font face='Courier' size='7.6'>.github/workflows/keepalive.yml</font> requests "
                      "<font face='Courier' size='7.6'>https://www.teamriders.bid/healthz</font> every 10 minutes, "
                      "which keeps it warm. Check it under the repository's <i>Actions</i> tab; it can also be run "
                      "manually (<i>Run workflow</i>). Any uptime monitor pointed at the same URL is an equivalent "
                      "safety net."))

    story += h1("10. Security, secrets and limits", "s10")
    story.append(h2("10.1 What protects the shop"))
    story.append(bullet("<b>Passwords</b> are hashed with scrypt and a per-user salt; the plain text is never stored "
                        "and cannot be recovered."))
    story.append(bullet("<b>Sessions</b> are database-backed: the cookie carries a random token, only its hash is "
                        "stored, it expires after 12 hours of inactivity, and it is invalidated on sign-out or "
                        "password reset."))
    story.append(bullet("<b>CSRF</b> — every form and JSON call carries a per-session token; a mismatch is rejected "
                        "with 403."))
    story.append(bullet("<b>Roles</b> are enforced on the server for every admin route, not hidden in the interface."))
    story.append(bullet("<b>Transport</b> — HTTPS everywhere; cookies are marked Secure and HttpOnly behind the "
                        "proxy, plus HSTS."))
    story.append(bullet("<b>Throttling</b> — repeated failed sign-ins lock the username briefly and are written to "
                        "the activity log."))
    story.append(bullet("<b>Audit</b> — every state change is attributed to a user, a time and an IP address."))
    story.append(bullet("<b>No card data</b> is captured or stored; card payments are taken on the bank's own device."))
    story.append(h2("10.2 Secrets inventory"))
    story.append(table([
        ["Secret", "Where it lives", "Rotation"],
        ["Neon database URL", "Render -> service -> Environment (Secret). Never in the repository",
         "Neon -> Reset password, then update DATABASE_URL and redeploy"],
        ["Render API key", "Developer's password manager", "Render -> Account settings -> API keys -> revoke and create"],
        ["Cloudflare API token (DNS edit)", "Developer's password manager",
         "Cloudflare -> My Profile -> API Tokens -> Roll"],
        ["GitHub token (repo + workflow)", "Developer's password manager",
         "GitHub -> Settings -> Developer settings -> regenerate"],
        ["Shop SMTP password", "Settings screen in the app (database)",
         "Change at the mail provider, then re-enter in Settings"],
        ["User passwords", "Not recoverable (scrypt hashes)", "Admin resets them from Team &amp; access"],
    ], widths=[36, 74, 64], font=8.1))
    story.append(gap(5))
    story.append(callout(
        "Housekeeping note",
        "During the handover the connection strings and API keys were placed in plain text files in the owner's "
        "profile folder (<font face='Courier' size='7.6'>neon.txt, render.txt, cloudflare.txt, ghtoken.txt</font>). "
        "Move them into a password manager and delete the files; anyone with those files controls the database, the "
        "hosting account and DNS.",
        kind="warn"))
    story.append(h2("10.3 Free-tier ceilings"))
    story.append(table([
        ["Service", "Free allowance", "What happens at the limit"],
        ["Render web service", "512 MB RAM, 0.1 CPU, sleeps after ~15 min idle",
         "Slow first request (the keep-alive Action prevents it); upgrade to 2 GB for $7/month if the shop grows"],
        ["Neon PostgreSQL", "0.5 GB storage, autosuspend when idle",
         "Writes fail once full — export CSVs and prune old audit rows, or upgrade the plan"],
        ["GitHub Actions", "Unlimited minutes for public repositories", "Scheduled runs may be delayed a few minutes"],
        ["Cloudflare DNS", "Free plan", "No practical limit for this use"],
    ], widths=[34, 62, 78], font=8.1))
    story.append(h2("10.4 Known limitations"))
    story.append(bullet("Single currency and single shop; the same install cannot serve two branches."))
    story.append(bullet("No barcode scanner input — search by typing SKU, name, brand or bike model."))
    story.append(bullet("No offline sync: the PC fallback and the cloud database are separate data sets."))
    story.append(bullet("No purchase orders or supplier invoices — restocking records quantity and unit cost only."))
    story.append(bullet("Receipts are commercial documents, not fiscalised tax invoices; add the shop's VAT number in "
                        "Settings if the business registers."))
    story.append(bullet("E-mail notifications need an SMTP account; without one the waiting list is notify-by-hand."))

    story += h1("11. Troubleshooting", "s11")
    story.append(table([
        ["Symptom", "Likely cause", "Fix"],
        ["First page load takes ~40 s", "Render free instance was asleep", "Confirm the keep-alive Action is enabled "
                                                                          "and green under the repo's Actions tab"],
        ["Site shows “Application failed to respond”", "Service crashed or is rebuilding", "Render -> Logs; check the "
                                                                                          "latest deploy; redeploy the "
                                                                                          "previous commit if the build is "
                                                                                          "broken"],
        ["Sign-in page loads but login fails", "Wrong password, or username locked after failed attempts",
         "Wait for the cool-off, or admin resets the password"],
        ["Everything redirects to /setup", "The app can see no users — usually a wrong DATABASE_URL",
         "Check DATABASE_URL on Render; a fresh database will show the setup screen"],
        ["“Forbidden” on a page a cashier used before", "Role is cashier", "Sign in as an admin for that screen"],
        ["“Invalid or missing security token”", "Page was left open too long; session expired",
         "Reload the page and try again"],
        ["“Not enough stock for …”", "Quantity on hand is lower than the cart", "Restock or adjust, or sell fewer "
                                                                                "units"],
        ["“Discount … is pending and cannot be used”", "It has not been approved yet", "Admin approves it in "
                                                                                       "Discounts, then sell again"],
        ["“Discount approval has expired”", "Approved discount is older than 30 minutes", "Request a new one"],
        ["“Period YYYY-MM is closed”", "The month has been closed", "Void is impossible by design; correct it as an "
                                                                   "adjustment"],
        ["Receipt prints on two pages", "Wrong paper size in the print dialogue", "Choose the 80 mm receipt printer, or "
                                                                                  "use the A4 version"],
        ["Back-in-stock e-mails not arriving", "SMTP not configured, or wrong credentials", "Settings -> Outgoing "
                                                                                            "e-mail -> Send test "
                                                                                            "e-mail"],
        ["Apex domain does not resolve", "Stale negative cache from an earlier move", "Prefer "
                                                                                        "<font face='Courier' "
                                                                                        "size='7.6'>www.</font>; Flush "
                                                                                        "the local DNS cache or use what "
                                                                                        "the domain shows"],
        ["Site works on the Render URL but not the domain", "Cloudflare record points at the old tunnel",
         "Set the three CNAMEs to <font face='Courier' size='7.6'>teamriders-pos.onrender.com</font>, proxy off"],
    ], widths=[40, 55, 79], font=8))

    story += h1("12. The offline fallback on the shop PC", "s12")
    story.append(para("The PC copy exists for one reason: to keep selling when the internet or the cloud is "
                      "unavailable. It runs the same code on SQLite."))
    story.append(numbered(1, "Double-click <font face='Courier' size='7.6'>start.bat</font> in the project folder "
                             "(or run <font face='Courier' size='7.6'>python run.py</font>)."))
    story.append(numbered(2, "Open <font face='Courier' size='7.6'>http://127.0.0.1:8077</font> on the PC, or "
                             "<font face='Courier' size='7.6'>http://&lt;pc-ip&gt;:8077</font> from another device on "
                             "the shop Wi-Fi."))
    story.append(numbered(3, "Sign in with the local accounts — these are separate from the cloud accounts."))
    story.append(numbered(4, "To publish the fallback through the old tunnel, run "
                             "<font face='Courier' size='7.6'>start-online.bat</font> and point the Cloudflare CNAMEs "
                             "back at <font face='Courier' size='7.6'>5bfc9160-5e58-4aa8-ae7a-4d19316b78a1."
                             "cfargotunnel.com</font> (proxy on)."))
    story.append(numbered(5, "When the cloud is back, either keep using one of them consistently, or copy the "
                             "offline sales across deliberately (export the CSVs and re-enter them) — there is no "
                             "automatic merge."))
    story.append(gap(4))
    story.append(callout(
        "Rule of one till",
        "Never sell from the cloud and the offline copy in the same period. Stock, receipts and profit are kept "
        "separately in each, and reconciling two sets of receipts by hand is exactly the work this system exists to "
        "avoid.",
        kind="warn"))
    story.append(h2("12.1 The Windows startup shortcut"))
    story.append(para("A shortcut named <i>Team Riders POS</i> in the Startup folder launches the local app (and "
                      "previously the tunnel) when the PC signs in. While the shop runs in the cloud this is "
                      "unnecessary; remove it from <font face='Courier' size='7.6'>shell:startup</font> if you want "
                      "the PC to stay clean, and start the fallback by hand only when needed."))
    story.append(PageBreak())

    # ------------------------------------------------------------ appendices
    story += h1("Appendix A — Route reference", "appa")
    story.append(table([
        ["Path", "Role", "Purpose"],
        ["/healthz", "public", "Liveness probe used by Render and the keep-alive Action"],
        ["/login, /logout, /setup", "public", "Authentication; setup only exists while there are no users"],
        ["/dashboard", "any", "Today/this month figures, low stock, attention list, latest receipts"],
        ["/pos", "any", "The till: search, cart, discount, payment, quote mode"],
        ["/api/products/search", "any", "Type-ahead suggestions with price and stock"],
        ["/api/discounts, /api/discounts/{code}", "any", "Create a discount request / look one up"],
        ["/api/sales, /api/quotations, /api/alerts", "any", "Create a sale, a quotation, or a back-in-stock alert"],
        ["/sales, /sales/{id}", "own / all", "Sales list and receipt detail"],
        ["/sales/{id}/receipt, /sales/{id}/receipt.pdf?size=a4", "own / all", "Printable receipt, thermal or A4"],
        ["/sales/{id}/void", "admin", "Void a completed sale (blocked for closed months)"],
        ["/quotations, /quotations/{id}, /quotations/{id}.pdf", "any", "Quotation list, detail, PDF"],
        ["/discounts, /discounts/{id}/decide", "admin", "Approve or refuse discount requests"],
        ["/products, /products/new, /products/{id}/edit, /products/{id}/toggle", "admin", "Catalogue maintenance"],
        ["/products/{id}/delete", "admin", "Delete a part (archived instead when it has sales history)"],
        ["/stock, /stock/movements, /stock/restock, /stock/adjust", "admin", "Levels, ledger, restock, adjustment"],
        ["/stock/requests", "any", "Raise a breakage, return or correction request; admin approves or refuses"],
        ["/salaries, /salaries/pay", "admin", "Pay a salary from business cash and see the payment history"],
        ["/alerts, /alerts/{id}/notify, /alerts/notify-ready", "admin", "Back-in-stock waiting list"],
        ["/cash, /cash/add, /cash/{id}/delete", "admin", "Business cash ledger"],
        ["/closings, /closings/close, /closings/{id}", "admin", "Monthly close-off and its report"],
        ["/reports, /reports/export/{sales|products|stock|discounts}.csv", "admin", "Period reporting and exports"],
        ["/settings, /settings/test-email", "admin", "Business configuration and SMTP test"],
        ["/users, /users/new, /users/{id}/password, /users/{id}/toggle, /users/{id}/delete", "admin", "Team &amp; access, including deleting an account"],
        ["/audit", "admin", "Activity log"],
        ["/account, /account/password", "any", "Own profile and password"],
    ], widths=[62, 22, 90], font=7.9))

    story += h1("Appendix B — Settings keys", "appb")
    story.append(table([
        ["Key", "Default", "Meaning"],
        ["business_name", "Team Riders", "Shop name on screens and documents"],
        ["business_tagline / address / phone / email", "—", "Stationery details on receipts and quotations"],
        ["currency", "$", "Symbol used everywhere"],
        ["tax_rate_percent / tax_label", "0 / VAT", "Tax rate and the wording of the tax line"],
        ["max_discount_percent", "60", "Ceiling for any discount request"],
        ["low_stock_threshold", "5", "Default reorder level for new products"],
        ["receipt_footer", "Thank you for your business…", "Line printed at the foot of receipts"],
        ["quotation_valid_days / quotation_terms", "14 / —", "Validity window and terms on quotations"],
        ["smtp_host / port / user / password / from / tls", "blank / 587 / …", "Outgoing e-mail for back-in-stock "
                                                                             "notices"],
        ["business_initialised", "0 -> 1", "Set once the owner account exists; hides the setup screen"],
    ], widths=[48, 30, 96], font=8))

    story += h1("Appendix C — Enumerations used by the data", "appc")
    story.append(table([
        ["Field", "Allowed values"],
        ["users.role", "admin, cashier"],
        ["sales.status", "completed, voided"],
        ["sales.payment_method", "cash, mobile, card, bank"],
        ["stock_movements.kind", "initial, sale, restock, adjustment, void, breakage, return"],
        ["discounts.kind", "percent, amount"],
        ["discounts.status", "pending, approved, denied, expired, used"],
        ["quotations.status", "draft, sent, accepted, rejected, cancelled, expired"],
        ["stock_alerts.status", "waiting, notified, cancelled"],
        ["cash_transactions.kind", "expense, owner_withdrawal, capital_injection, adjustment, sale_income, refund, salary, customer_refund"],
        ["stock_requests.kind", "breakage, return, correction"],
        ["stock_requests.status", "pending, approved, denied"],
        ["users.is_deleted", "0 active account, 1 deleted (anonymised, cannot sign in)"],
        ["cash_transactions.method", "cash, bank, mobile"],
    ], widths=[48, 126], font=8.2))
    story.append(gap(6))
    story.append(para("Document generated from the source tree by <font face='Courier' size='7.6'>scripts/"
                      "build_handbook.py</font>. Regenerate it whenever a screen, setting or deployment detail "
                      "changes, so the handbook never drifts from the system.", SMALL))
    return story


def main() -> int:
    doc = Handbook(
        OUTPUT,
        pagesize=A4,
        title="Team Riders POS — Handbook",
        author="Team Riders",
        subject="Point of sale, stock control and operations handbook",
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
    )
    story = build()
    doc.multiBuild(story)
    print(f"written: {OUTPUT} ({OUTPUT.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
