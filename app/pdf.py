"""PDF generation (thermal + A4 receipts, quotations) using ReportLab."""
from __future__ import annotations

import io
import os
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app import db

_FONTS: tuple[str, str] | None = None


def _fonts() -> tuple[str, str]:
    """Use a Unicode TTF when available so any currency symbol renders."""
    global _FONTS
    if _FONTS is not None:
        return _FONTS
    candidates = [
        ("C:\\Windows\\Fonts\\segoeui.ttf", "C:\\Windows\\Fonts\\segoeuib.ttf"),
        ("C:\\Windows\\Fonts\\arial.ttf", "C:\\Windows\\Fonts\\arialbd.ttf"),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ]
    for regular, bold in candidates:
        if os.path.exists(regular) and os.path.exists(bold):
            try:
                pdfmetrics.registerFont(TTFont("AppSans", regular))
                pdfmetrics.registerFont(TTFont("AppSans-Bold", bold))
                pdfmetrics.registerFontFamily("AppSans", normal="AppSans", bold="AppSans-Bold", italic="AppSans", boldItalic="AppSans-Bold")
                _FONTS = ("AppSans", "AppSans-Bold")
                return _FONTS
            except Exception:
                continue
    _FONTS = ("Helvetica", "Helvetica-Bold")
    return _FONTS


def _styles(width: float) -> dict:
    regular, bold = _fonts()
    base = 8 if width < 300 else 9.5
    return {
        "title": ParagraphStyle("title", fontName=bold, fontSize=base + 4, leading=base + 6, alignment=1),
        "small": ParagraphStyle("small", fontName=regular, fontSize=base - 1, leading=base + 1, alignment=1),
        "label": ParagraphStyle("label", fontName=bold, fontSize=base, leading=base + 2),
        "text": ParagraphStyle("text", fontName=regular, fontSize=base, leading=base + 2),
        "cell": ParagraphStyle("cell", fontName=regular, fontSize=base - 0.5 if base > 8 else base - 1, leading=base + 0.5),
        "cellright": ParagraphStyle(
            "cellright", fontName=regular, fontSize=base - 0.5 if base > 8 else base - 1, leading=base + 0.5, alignment=2
        ),
        "h2": ParagraphStyle("h2", fontName=bold, fontSize=base + 3, leading=base + 5),
        "muted": ParagraphStyle("muted", fontName=regular, fontSize=base - 1, leading=base + 1, textColor=colors.HexColor("#555555")),
    }


def _money(cents: int) -> str:
    return f"{db.get_settings().get('currency', '$')}{(cents or 0) / 100:,.2f}"


def _header_flowables(settings: dict, st: dict, width: float) -> list:
    items = [
        Paragraph(escape(settings.get("business_name", "Team Riders")), st["title"]),
        Paragraph(escape(settings.get("business_tagline", "")), st["small"]),
        Paragraph(escape(settings.get("business_address", "")), st["small"]),
        Paragraph(
            escape(f"{settings.get('business_phone', '')}  {settings.get('business_email', '')}".strip()), st["small"]
        ),
        Spacer(1, 6),
    ]
    return items


def _sale_pdf(sale, items, settings: dict, width: float, height: float | None) -> tuple[bytes, int]:
    sale = dict(sale)
    st = _styles(width)
    buf = io.BytesIO()
    if width < 300:
        left = right = 7 * mm
    else:
        left = right = 18 * mm
    doc = SimpleDocTemplate(
        buf,
        pagesize=(width, height or A4[1]),
        leftMargin=left,
        rightMargin=right,
        topMargin=8 * mm,
        bottomMargin=8 * mm,
        title=f"Receipt {sale['receipt_no']}",
        author=settings.get("business_name", ""),
    )
    content_width = width - left - right
    story = _header_flowables(settings, st, width)
    story.append(Paragraph(f"<b>SALES RECEIPT</b>", st["h2"]))
    story.append(Spacer(1, 3))
    meta = [
        ["Receipt", sale["receipt_no"]],
        ["Date", sale["created_at"]],
        ["Cashier", sale["cashier_name"]],
    ]
    if sale["customer_name"]:
        meta.append(["Customer", sale["customer_name"]])
    meta_tbl = Table(meta, colWidths=[content_width * 0.3, content_width * 0.7])
    meta_tbl.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), st["text"].fontName),
                ("FONTSIZE", (0, 0), (-1, -1), st["text"].fontSize),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#666666")),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                ("TOPPADDING", (0, 0), (-1, -1), 1),
            ]
        )
    )
    story.append(meta_tbl)
    story.append(Spacer(1, 5))

    rows = [[Paragraph("<b>Item</b>", st["cell"]), Paragraph("<b>Qty</b>", st["cellright"]),
             Paragraph("<b>Price</b>", st["cellright"]), Paragraph("<b>Amount</b>", st["cellright"])]]
    for it in items:
        rows.append(
            [
                Paragraph(escape(it["name"]), st["cell"]),
                Paragraph(str(it["qty"]), st["cellright"]),
                Paragraph(f"{it['unit_price_cents'] / 100:,.2f}", st["cellright"]),
                Paragraph(f"{it['line_total_cents'] / 100:,.2f}", st["cellright"]),
            ]
        )
    items_tbl = Table(
        rows,
        colWidths=[content_width * 0.44, content_width * 0.13, content_width * 0.2, content_width * 0.23],
        repeatRows=1,
    )
    items_tbl.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), st["cell"].fontName),
                ("FONTSIZE", (0, 0), (-1, -1), st["cell"].fontSize),
                ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#333333")),
                ("LINEBELOW", (0, 1), (-1, -2), 0.25, colors.HexColor("#dddddd")),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    story.append(items_tbl)
    story.append(Spacer(1, 6))

    totals = [["Subtotal", _money(sale["subtotal_cents"])]]
    if sale["discount_cents"]:
        totals.append(["Discount" + (f" ({sale['discount_code']})" if sale.get("discount_code") else ""),
                       f"-{_money(sale['discount_cents'])}"])
    if sale["tax_cents"]:
        totals.append([f"{settings.get('tax_label', 'Tax')} ({settings.get('tax_rate_percent', '0')}%)", _money(sale["tax_cents"])])
    totals.append(["TOTAL", _money(sale["total_cents"])])
    if sale["payment_method"] == "cash":
        totals.append(["Tendered", _money(sale["tendered_cents"])])
        totals.append(["Change", _money(sale["change_cents"])])
    totals_tbl = Table(totals, colWidths=[content_width * 0.6, content_width * 0.4])
    totals_tbl.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -2), st["text"].fontName),
                ("FONTNAME", (0, -1), (-1, -1), st["label"].fontName),
                ("FONTSIZE", (0, 0), (-1, -1), st["text"].fontSize),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("LINEABOVE", (0, -1), (-1, -1), 0.6, colors.HexColor("#333333")),
                ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
            ]
        )
    )
    story.append(totals_tbl)
    story.append(Spacer(1, 6))
    story.append(Paragraph(f"Payment: {escape(str(sale['payment_method']).upper())}", st["text"]))
    if sale["status"] == "voided":
        story.append(Paragraph("<b>*** VOIDED SALE ***</b>", st["text"]))
    story.append(Spacer(1, 10))
    if sale["discount_reason"]:
        story.append(Paragraph(f"Discount reason: {escape(sale['discount_reason'])}", st["muted"]))
    story.append(Paragraph(escape(settings.get("receipt_footer", "")), st["small"]))
    story.append(Spacer(1, 4))
    story.append(Paragraph(escape(f"Powered by {settings.get('business_name', 'Team Riders')} POS"), st["muted"]))

    doc.build(story)
    return buf.getvalue(), doc.page


def receipt_pdf(sale, items, size: str = "thermal") -> bytes:
    settings = db.get_settings()
    if size == "a4":
        return _sale_pdf(sale, items, settings, A4[0], A4[1])[0]
    width = 80 * mm
    height = (70 + len(items) * 12 + (14 if sale["discount_cents"] else 0)) * mm
    height = max(140 * mm, min(height, 600 * mm))
    content = b""
    for _ in range(4):
        content, pages = _sale_pdf(sale, items, settings, width, height)
        if pages == 1:
            break
        height = min(height + 50 * mm, 700 * mm)
    return content


def quotation_pdf(quote, items) -> bytes:
    settings = db.get_settings()
    width, height = A4
    st = _styles(width)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title=f"Quotation {quote['quote_no']}",
        author=settings.get("business_name", ""),
    )
    content_width = width - 36 * mm
    story = _header_flowables(settings, st, width)
    story.append(Spacer(1, 8))
    story.append(Paragraph("QUOTATION", ParagraphStyle("qt", fontName=st["h2"].fontName, fontSize=18, leading=22)))
    story.append(Spacer(1, 6))

    left_block = [
        Paragraph("<b>Prepared for</b>", st["text"]),
        Paragraph(escape(quote["customer_name"]), st["text"]),
    ]
    if quote["customer_phone"]:
        left_block.append(Paragraph(escape(quote["customer_phone"]), st["muted"]))
    if quote["customer_email"]:
        left_block.append(Paragraph(escape(quote["customer_email"]), st["muted"]))
    right_block = [
        Paragraph(f"<b>{escape(quote['quote_no'])}</b>", st["text"]),
        Paragraph(f"Date: {escape(quote['created_at'][:10])}", st["text"]),
        Paragraph(f"Valid until: {escape(quote['valid_until'] or '—')}", st["text"]),
        Paragraph(f"Prepared by: {escape(quote['created_by_name'])}", st["muted"]),
    ]
    head = Table([[left_block, right_block]], colWidths=[content_width * 0.55, content_width * 0.45])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story.append(head)
    story.append(Spacer(1, 10))

    rows = [[
        Paragraph("<b>#</b>", st["cell"]), Paragraph("<b>SKU</b>", st["cell"]), Paragraph("<b>Description</b>", st["cell"]),
        Paragraph("<b>Qty</b>", st["cellright"]), Paragraph("<b>Unit price</b>", st["cellright"]),
        Paragraph("<b>Amount</b>", st["cellright"]),
    ]]
    for idx, it in enumerate(items, start=1):
        rows.append([
            Paragraph(str(idx), st["cell"]),
            Paragraph(escape(it["sku"]), st["cell"]),
            Paragraph(escape(it["name"]), st["cell"]),
            Paragraph(str(it["qty"]), st["cellright"]),
            Paragraph(f"{it['unit_price_cents'] / 100:,.2f}", st["cellright"]),
            Paragraph(f"{it['line_total_cents'] / 100:,.2f}", st["cellright"]),
        ])
    tbl = Table(
        rows,
        colWidths=[content_width * 0.05, content_width * 0.13, content_width * 0.4, content_width * 0.08,
                   content_width * 0.17, content_width * 0.17],
        repeatRows=1,
    )
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef1f7")),
                ("LINEBELOW", (0, 0), (-1, 0), 0.7, colors.HexColor("#8892a6")),
                ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#dfe3ea")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(tbl)
    story.append(Spacer(1, 8))

    totals = [["Subtotal", _money(quote["subtotal_cents"])]]
    if quote["discount_cents"]:
        totals.append(["Discount", f"-{_money(quote['discount_cents'])}"])
    if quote["tax_cents"]:
        totals.append([f"{settings.get('tax_label', 'Tax')} ({settings.get('tax_rate_percent', '0')}%)", _money(quote["tax_cents"])])
    totals.append(["TOTAL", _money(quote["total_cents"])])
    totals_tbl = Table(totals, colWidths=[content_width * 0.7, content_width * 0.3])
    totals_tbl.setStyle(
        TableStyle(
            [
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("FONTNAME", (0, -1), (-1, -1), st["label"].fontName),
                ("LINEABOVE", (0, -1), (-1, -1), 0.7, colors.HexColor("#8892a6")),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(totals_tbl)
    story.append(Spacer(1, 14))
    story.append(Paragraph("<b>Terms &amp; conditions</b>", st["text"]))
    story.append(Paragraph(escape(settings.get("quotation_terms", "")), st["muted"]))
    if quote["note"]:
        story.append(Spacer(1, 6))
        story.append(Paragraph(escape(quote["note"]), st["muted"]))
    story.append(Spacer(1, 24))
    story.append(Paragraph("Authorised signature: ______________________________", st["text"]))
    doc.build(story)
    return buf.getvalue()
