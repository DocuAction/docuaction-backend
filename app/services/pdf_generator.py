"""
AGT Premium PDF Generator — World-Class Federal Procurement Documents
Quotes & Invoices with gold accent bars, certification badges, embedded logo,
navy/blue/gold design system, signature blocks, GSA MAS contract display.
NEVER shows: cost, margin, profit %, supplier information.
"""
import io
import os
from datetime import datetime
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, Image, KeepTogether
)
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT

# ═══════════════════════════════════════════════════════════════
# AGT PREMIUM DESIGN SYSTEM
# ═══════════════════════════════════════════════════════════════

NAVY = colors.HexColor("#0A1628")
DARK_BLUE = colors.HexColor("#002D5E")
BLUE = colors.HexColor("#0066B3")
LIGHT_BLUE = colors.HexColor("#E8F4FD")
GOLD = colors.HexColor("#C8A951")
LIGHT_GOLD = colors.HexColor("#FBF5E6")
GRAY_BG = colors.HexColor("#F8F9FA")
GRAY_BD = colors.HexColor("#DEE2E6")
GRAY_TEXT = colors.HexColor("#6C757D")
WHITE = colors.white
BLACK = colors.HexColor("#212529")
GREEN = colors.HexColor("#107C10")
RED = colors.HexColor("#D13438")

LOGO_PATH = os.path.join(os.path.dirname(__file__), "agt_logo.png")


def _fmt(v):
    try:
        return f"${float(v):,.2f}"
    except:
        return "$0.00"


def _clean(s):
    if s and '.' in str(s):
        return str(s).split('.')[-1]
    return str(s) if s else "Draft"


# ─── Styles ───────────────────────────────────────────────────

def _styles():
    return {
        'brand': ParagraphStyle(name='Brand', fontSize=18, fontName='Helvetica-Bold', textColor=NAVY, leading=22),
        'brand_sub': ParagraphStyle(name='BrandSub', fontSize=8, textColor=GRAY_TEXT, leading=10),
        'doc_title': ParagraphStyle(name='DocTitle', fontSize=22, fontName='Helvetica-Bold', textColor=BLUE, alignment=TA_RIGHT, leading=26),
        'doc_num': ParagraphStyle(name='DocNum', fontSize=12, fontName='Helvetica-Bold', textColor=BLACK, alignment=TA_RIGHT),
        'section': ParagraphStyle(name='Section', fontSize=11, fontName='Helvetica-Bold', textColor=NAVY, spaceBefore=6, spaceAfter=6),
        'label': ParagraphStyle(name='Label', fontSize=7, fontName='Helvetica-Bold', textColor=GRAY_TEXT, leading=9),
        'value': ParagraphStyle(name='Value', fontSize=9, textColor=BLACK, leading=12),
        'cell': ParagraphStyle(name='Cell', fontSize=8, textColor=BLACK, leading=10),
        'cell_r': ParagraphStyle(name='CellR', fontSize=8, textColor=BLACK, alignment=TA_RIGHT, leading=10),
        'cell_h': ParagraphStyle(name='CellH', fontSize=7.5, fontName='Helvetica-Bold', textColor=WHITE, leading=10),
        'term': ParagraphStyle(name='Term', fontSize=7, textColor=GRAY_TEXT, spaceBefore=1, spaceAfter=1, leading=9),
        'footer': ParagraphStyle(name='Footer', fontSize=6.5, textColor=GRAY_TEXT, alignment=TA_CENTER, leading=9),
        'badge': ParagraphStyle(name='Badge', fontSize=6.5, fontName='Helvetica-Bold', textColor=NAVY, alignment=TA_CENTER, leading=8),
        'total_l': ParagraphStyle(name='TotalL', fontSize=9, textColor=GRAY_TEXT, alignment=TA_RIGHT, fontName='Helvetica-Bold'),
        'total_v': ParagraphStyle(name='TotalV', fontSize=9, textColor=BLACK, alignment=TA_RIGHT, fontName='Helvetica-Bold'),
        'grand_l': ParagraphStyle(name='GrandL', fontSize=13, textColor=NAVY, alignment=TA_RIGHT, fontName='Helvetica-Bold'),
        'grand_v': ParagraphStyle(name='GrandV', fontSize=15, textColor=BLUE, alignment=TA_RIGHT, fontName='Helvetica-Bold'),
        'sig_l': ParagraphStyle(name='SigL', fontSize=8, fontName='Helvetica-Bold', textColor=BLACK),
        'sig_n': ParagraphStyle(name='SigN', fontSize=7, textColor=GRAY_TEXT),
    }


# ─── Shared Components ────────────────────────────────────────

def _gold_bar():
    t = Table([[""]],  colWidths=[7.5 * inch], rowHeights=[4])
    t.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), GOLD), ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0)]))
    return t


def _navy_bar():
    t = Table([[""]],  colWidths=[7.5 * inch], rowHeights=[2])
    t.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), NAVY), ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0)]))
    return t


def _credential_badges(s):
    badges = ["SBA 8(a)", "ISO 9001:2015", "ISO 27001:2013", "CMMI Level 3", "MBE Certified"]
    cells = [Table([[Paragraph(b, s['badge'])]], colWidths=[1.5 * inch]) for b in badges]
    for c in cells:
        c.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), LIGHT_BLUE),
            ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor("#B8DAFF")),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ]))
    row = Table([cells], colWidths=[1.5 * inch] * 5)
    row.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE')]))
    return row


def _header(s, doc_title, doc_number):
    el = []

    # Logo + Title
    if os.path.exists(LOGO_PATH):
        logo = Image(LOGO_PATH, width=1.8 * inch, height=0.55 * inch)
    else:
        logo = Paragraph("<font size='16' color='#0066B3'><b>AGT</b></font><br/><font size='7' color='#6C757D'>Alliance Global Tech</font>", s['brand_sub'])

    header_t = Table([
        [logo, Paragraph(doc_title, s['doc_title'])],
        [Paragraph("Advanced Government Technologies", s['brand_sub']),
         Paragraph(doc_number, s['doc_num'])],
    ], colWidths=[3.8 * inch, 3.7 * inch])
    header_t.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    el.append(header_t)

    # Gold bar
    el.append(_gold_bar())
    el.append(Spacer(1, 6))

    # Company info
    info = Table([
        [Paragraph("5457 Twin Knolls Rd, Suite 300, Columbia, MD 21045 | Phone: 301-792-3371 | Toll-Free: 1-800-696-1973", s['brand_sub']),
         Paragraph("imran@agtbi.com | www.agtbi.com", ParagraphStyle(name='Web', fontSize=8, textColor=BLUE, alignment=TA_RIGHT))],
    ], colWidths=[5.0 * inch, 2.5 * inch])
    info.setStyle(TableStyle([('TOPPADDING', (0, 0), (-1, -1), 1), ('BOTTOMPADDING', (0, 0), (-1, -1), 1)]))
    el.append(info)
    el.append(Spacer(1, 6))

    # Credential badges
    el.append(_credential_badges(s))
    el.append(Spacer(1, 4))

    # Contract vehicles row
    cv = Table([
        [Paragraph("<b>GSA MAS:</b> 47QTCA21D003M", s['badge']),
         Paragraph("<b>CAGE:</b> 8ERE8", s['badge']),
         Paragraph("<b>UEI:</b> MP2FLV1MAW93", s['badge']),
         Paragraph("<b>US Navy Contract</b>", s['badge']),
         Paragraph("<b>DoD Cleared</b>", s['badge'])],
    ], colWidths=[1.8 * inch, 1.2 * inch, 1.6 * inch, 1.4 * inch, 1.5 * inch])
    cv.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), LIGHT_GOLD),
        ('BOX', (0, 0), (-1, -1), 0.5, GOLD),
        ('INNERGRID', (0, 0), (-1, -1), 0.25, GOLD),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
    ]))
    el.append(cv)
    el.append(Spacer(1, 10))

    return el


def _footer(s, doc_number):
    el = []
    el.append(Spacer(1, 10))
    el.append(_navy_bar())
    el.append(Spacer(1, 4))
    el.append(Paragraph(
        "Alliance Global Tech, Inc. | SBA 8(a) Certified | MBE | CMMI Level 3 Dev & Serv | ISO 27001:2013 | ISO 9001:2015<br/>"
        "GSA MAS: 47QTCA21D003M | SeaPort NxG | US Navy Contract | CAGE: 8ERE8 | UEI: MP2FLV1MAW93 | DoD Facility Clearance<br/>"
        f"5457 Twin Knolls Rd, Suite 300, Columbia, MD 21045 | {doc_number}", s['footer']))
    return el


def _signature_block(s):
    sig = Table([
        [Paragraph("Prepared by:", s['sig_l']), "", "", Paragraph("Accepted by:", s['sig_l']), "", ""],
        ["", "", "", "", "", ""],
        ["", "", "", "", "", ""],
        [HRFlowable(width="100%", thickness=0.5, color=BLACK), "",
         Paragraph("Date", s['sig_n']),
         HRFlowable(width="100%", thickness=0.5, color=BLACK), "",
         Paragraph("Date", s['sig_n'])],
        [Paragraph("Imran Siddiqui", s['sig_n']), "", "",
         Paragraph("Name / Title", s['sig_n']), "", ""],
        [Paragraph("President & CEO", s['sig_n']), "", "",
         Paragraph("Organization", s['sig_n']), "", ""],
    ], colWidths=[2.2 * inch, 0.3 * inch, 1.0 * inch, 2.2 * inch, 0.3 * inch, 1.0 * inch])
    sig.setStyle(TableStyle([('TOPPADDING', (0, 0), (-1, -1), 2), ('BOTTOMPADDING', (0, 0), (-1, -1), 2)]))
    return sig


# ═══════════════════════════════════════════════════════════════
# QUOTE PDF
# ═══════════════════════════════════════════════════════════════

def generate_quote_pdf(quote_data: dict, rfq_data: dict, line_items: list) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter,
        leftMargin=0.5 * inch, rightMargin=0.5 * inch,
        topMargin=0.4 * inch, bottomMargin=0.5 * inch)

    s = _styles()
    el = []

    quote_num = quote_data.get('quote_number', 'AGT-Q-DRAFT')
    version = quote_data.get('version', 1)
    status = _clean(quote_data.get('status', 'Draft'))
    created = quote_data.get('created_at', '')
    if isinstance(created, str):
        try:
            created = datetime.fromisoformat(created.replace('Z', '+00:00')).strftime('%B %d, %Y')
        except:
            created = str(created)[:10] if created else datetime.now().strftime('%B %d, %Y')

    # Header
    el.extend(_header(s, "QUOTATION", quote_num))

    # Quote details — two column
    co_name = rfq_data.get('contract_officer_name') or 'N/A'
    co_email = rfq_data.get('contract_officer_email') or 'N/A'
    co_phone = rfq_data.get('contract_officer_phone') or 'N/A'

    detail_t = Table([
        [Paragraph("QUOTE TO", s['label']), "", Paragraph("DOCUMENT DETAILS", s['label']), ""],
        [Paragraph(rfq_data.get('title', 'N/A'), s['value']), "", Paragraph(f"Date: {created}", s['value']), ""],
        [Paragraph(f"Agency: {rfq_data.get('agency') or 'N/A'}", s['value']), "", Paragraph(f"Version: v{version}", s['value']), ""],
        [Paragraph(f"Solicitation: {rfq_data.get('solicitation_number') or 'N/A'}", s['value']), "", Paragraph(f"Status: {status}", s['value']), ""],
        [Paragraph(f"Officer: {co_name}", s['value']), "", Paragraph(f"Valid: 30 Days", s['value']), ""],
        [Paragraph(f"Email: {co_email}", s['value']), "", Paragraph(f"Terms: Net 30", s['value']), ""],
        [Paragraph(f"Phone: {co_phone}", s['value']), "", Paragraph(f"Delivery: FOB Destination", s['value']), ""],
    ], colWidths=[3.4 * inch, 0.3 * inch, 3.0 * inch, 0.8 * inch])
    detail_t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), GRAY_BG),
        ('BOX', (0, 0), (-1, -1), 0.5, GRAY_BD),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('LINEBELOW', (0, 0), (0, 0), 0.75, GOLD),
        ('LINEBELOW', (2, 0), (2, 0), 0.75, GOLD),
    ]))
    el.append(detail_t)

    # Shipping
    sa = rfq_data.get('ship_to_address') or ''
    sc = rfq_data.get('ship_to_city') or ''
    if any([sa, sc]):
        el.append(Spacer(1, 4))
        city_line = ", ".join(filter(None, [sc, rfq_data.get('ship_to_state', '')])) + (" " + (rfq_data.get('ship_to_zip') or '') if rfq_data.get('ship_to_zip') else "")
        ship_t = Table([
            [Paragraph("SHIP TO", s['label']), Paragraph(f"{sa or 'N/A'} | {city_line.strip() or 'N/A'} | {rfq_data.get('shipping_method') or 'FOB Destination'}", s['value'])]
        ], colWidths=[0.7 * inch, 6.8 * inch])
        ship_t.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), GRAY_BG), ('BOX', (0, 0), (-1, -1), 0.5, GRAY_BD), ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3), ('LEFTPADDING', (0, 0), (-1, -1), 6)]))
        el.append(ship_t)

    el.append(Spacer(1, 12))

    # Line items
    el.append(Paragraph("LINE ITEMS", s['section']))
    cw = [0.42 * inch, 1.1 * inch, 0.85 * inch, 1.4 * inch, 0.32 * inch, 0.45 * inch, 0.32 * inch, 0.75 * inch, 0.89 * inch]

    thdr = [Paragraph(h, s['cell_h']) for h in ["CLIN", "PART NUMBER", "MFR", "DESCRIPTION", "TAA", "TYPE", "QTY", "UNIT $", "TOTAL"]]
    tdata = [thdr]

    for i, li in enumerate(line_items):
        qty = int(li.get('quantity', 1))
        sell = float(li.get('sell_price', 0))
        tdata.append([
            Paragraph(str(li.get('clin') or str(i + 1).zfill(4)), s['cell']),
            Paragraph(str(li.get('part_number', ''))[:28], s['cell']),
            Paragraph(str(li.get('manufacturer', ''))[:22], s['cell']),
            Paragraph(str(li.get('description', li.get('part_number', 'Item')))[:35], s['cell']),
            Paragraph(li.get('taa_compliant', 'Yes'), s['cell']),
            Paragraph(li.get('product_type', 'HW')[:8], s['cell']),
            Paragraph(str(qty), s['cell_r']),
            Paragraph(_fmt(sell), s['cell_r']),
            Paragraph(_fmt(sell * qty), s['cell_r']),
        ])

    if not line_items:
        tdata.append([Paragraph("No line items", s['cell'])] + [""] * 8)

    lt = Table(tdata, colWidths=cw, repeatRows=1)
    tst = [
        ('BACKGROUND', (0, 0), (-1, 0), NAVY),
        ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
        ('BOX', (0, 0), (-1, -1), 0.75, NAVY),
        ('INNERGRID', (0, 1), (-1, -1), 0.25, GRAY_BD),
        ('LINEBELOW', (0, 0), (-1, 0), 2, GOLD),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]
    for r in range(2, len(tdata), 2):
        tst.append(('BACKGROUND', (0, r), (-1, r), GRAY_BG))
    lt.setStyle(TableStyle(tst))
    el.append(lt)
    el.append(Spacer(1, 12))

    # Totals
    total_sell = float(quote_data.get('total_sell_price', 0) or 0)
    total_tax = float(quote_data.get('total_tax', 0) or 0)
    shipping = float(quote_data.get('shipping_cost', 0) or 0)
    grand = total_sell + total_tax + shipping

    tots = Table([
        ["", Paragraph("Subtotal:", s['total_l']), Paragraph(_fmt(total_sell), s['total_v'])],
        ["", Paragraph("Shipping:", s['total_l']), Paragraph(_fmt(shipping), s['total_v'])],
        ["", Paragraph("Tax:", s['total_l']), Paragraph(_fmt(total_tax), s['total_v'])],
        ["", "", ""],
        ["", Paragraph("TOTAL:", s['grand_l']), Paragraph(_fmt(grand), s['grand_v'])],
    ], colWidths=[4.0 * inch, 1.5 * inch, 2.0 * inch])
    tots.setStyle(TableStyle([
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LINEABOVE', (1, 4), (-1, 4), 2, NAVY),
        ('BACKGROUND', (1, 4), (-1, 4), LIGHT_GOLD),
        ('BOX', (1, 4), (-1, 4), 0.5, GOLD),
    ]))
    el.append(tots)
    el.append(Spacer(1, 14))

    # Terms
    el.append(Paragraph("TERMS AND CONDITIONS", s['section']))
    for t in [
        "1. Quotation valid for 30 calendar days from date of issue.",
        "2. Payment terms: Net 30 unless otherwise agreed in writing.",
        "3. All prices quoted in USD. FOB Destination unless specified.",
        "4. Delivery timeline confirmed upon receipt of purchase order.",
        "5. All items subject to manufacturer availability at time of order.",
        "6. TAA (Trade Agreements Act) compliance indicated per line item.",
        "7. GSA pricing per MAS Contract 47QTCA21D003M, SIN 54151S.",
        "8. Returns subject to manufacturer restocking fee and approval.",
        f"9. Reference: {quote_num} v{version}",
    ]:
        el.append(Paragraph(t, s['term']))

    el.append(Spacer(1, 12))
    el.append(HRFlowable(width="100%", thickness=0.5, color=GRAY_BD, spaceAfter=8))
    el.append(_signature_block(s))
    el.extend(_footer(s, quote_num))

    doc.build(el)
    buffer.seek(0)
    return buffer.read()


# ═══════════════════════════════════════════════════════════════
# INVOICE PDF
# ═══════════════════════════════════════════════════════════════

def generate_invoice_pdf(invoice_data: dict, line_items: list) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter,
        leftMargin=0.5 * inch, rightMargin=0.5 * inch,
        topMargin=0.4 * inch, bottomMargin=0.5 * inch)

    s = _styles()
    el = []

    inv_num = invoice_data.get('invoice_number', 'AGT-INV-DRAFT')

    # Header
    el.extend(_header(s, "INVOICE", inv_num))

    # Bill To + Invoice Details
    detail_t = Table([
        [Paragraph("BILL TO", s['label']), "", Paragraph("INVOICE DETAILS", s['label']), ""],
        [Paragraph(invoice_data.get('client_name', '[Client]'), s['value']), "", Paragraph(f"Invoice #: {inv_num}", s['value']), ""],
        [Paragraph(f"Address: {invoice_data.get('client_address') or 'N/A'}", s['value']), "", Paragraph(f"Date: {invoice_data.get('invoice_date') or 'N/A'}", s['value']), ""],
        [Paragraph(f"Email: {invoice_data.get('client_email') or 'N/A'}", s['value']), "", Paragraph(f"Due: {invoice_data.get('due_date') or 'Net 30'}", s['value']), ""],
        [Paragraph(f"Phone: {invoice_data.get('client_phone') or 'N/A'}", s['value']), "", Paragraph(f"Terms: {invoice_data.get('payment_terms') or 'Net 30'}", s['value']), ""],
        [Paragraph(f"PO #: {invoice_data.get('po_number') or 'N/A'}", s['value']), "", Paragraph(f"Contract: 47QTCA21D003M", s['value']), ""],
    ], colWidths=[3.4 * inch, 0.3 * inch, 3.0 * inch, 0.8 * inch])
    detail_t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), GRAY_BG),
        ('BOX', (0, 0), (-1, -1), 0.5, GRAY_BD),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('LINEBELOW', (0, 0), (0, 0), 0.75, GOLD),
        ('LINEBELOW', (2, 0), (2, 0), 0.75, GOLD),
    ]))
    el.append(detail_t)
    el.append(Spacer(1, 12))

    # Line items
    el.append(Paragraph("SERVICES / ITEMS", s['section']))
    cw = [0.4 * inch, 3.3 * inch, 0.7 * inch, 0.7 * inch, 1.0 * inch, 1.4 * inch]
    thdr = [Paragraph(h, s['cell_h']) for h in ["#", "DESCRIPTION", "QTY", "UNIT", "RATE", "AMOUNT"]]
    tdata = [thdr]

    for i, li in enumerate(line_items):
        tdata.append([
            Paragraph(str(i + 1), s['cell']),
            Paragraph(str(li.get('description', 'Service'))[:50], s['cell']),
            Paragraph(f"{float(li.get('quantity', 1)):.1f}", s['cell_r']),
            Paragraph(str(li.get('unit', 'EA')), s['cell']),
            Paragraph(_fmt(li.get('rate', 0)), s['cell_r']),
            Paragraph(_fmt(li.get('amount', 0)), s['cell_r']),
        ])

    lt = Table(tdata, colWidths=cw, repeatRows=1)
    tst = [
        ('BACKGROUND', (0, 0), (-1, 0), NAVY),
        ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
        ('BOX', (0, 0), (-1, -1), 0.75, NAVY),
        ('INNERGRID', (0, 1), (-1, -1), 0.25, GRAY_BD),
        ('LINEBELOW', (0, 0), (-1, 0), 2, GOLD),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
    ]
    for r in range(2, len(tdata), 2):
        tst.append(('BACKGROUND', (0, r), (-1, r), GRAY_BG))
    lt.setStyle(TableStyle(tst))
    el.append(lt)
    el.append(Spacer(1, 12))

    # Totals
    subtotal = float(invoice_data.get('subtotal', 0) or 0)
    tax = float(invoice_data.get('tax_amount', 0) or 0)
    total = float(invoice_data.get('total', subtotal + tax) or 0)

    tots = Table([
        ["", Paragraph("Subtotal:", s['total_l']), Paragraph(_fmt(subtotal), s['total_v'])],
        ["", Paragraph("Tax:", s['total_l']), Paragraph(_fmt(tax), s['total_v'])],
        ["", "", ""],
        ["", Paragraph("AMOUNT DUE:", s['grand_l']), Paragraph(_fmt(total), s['grand_v'])],
    ], colWidths=[4.0 * inch, 1.5 * inch, 2.0 * inch])
    tots.setStyle(TableStyle([
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LINEABOVE', (1, 3), (-1, 3), 2, NAVY),
        ('BACKGROUND', (1, 3), (-1, 3), LIGHT_GOLD),
        ('BOX', (1, 3), (-1, 3), 0.5, GOLD),
    ]))
    el.append(tots)
    el.append(Spacer(1, 14))

    # Payment info
    pay_t = Table([
        [
            Table([[Paragraph("PAYMENT INFORMATION", s['label']),
                     Paragraph("Bank: [Bank Name]", s['value']),
                     Paragraph("Account: [Account #]", s['value']),
                     Paragraph("Routing: [Routing #]", s['value']),
                     Paragraph("DUNS: 080654701", s['value']),
                   ]], colWidths=[3.5 * inch]),
            Table([[Paragraph("REMIT TO", s['label']),
                     Paragraph("Alliance Global Tech, Inc.", ParagraphStyle(name='RemitB', fontSize=9, fontName='Helvetica-Bold', textColor=BLACK)),
                     Paragraph("5457 Twin Knolls Rd, Suite 300", s['value']),
                     Paragraph("Columbia, MD 21045", s['value']),
                     Paragraph("EIN: [EIN Number]", s['value']),
                   ]], colWidths=[3.5 * inch]),
        ]
    ], colWidths=[3.75 * inch, 3.75 * inch])
    pay_t.setStyle(TableStyle([
        ('BOX', (0, 0), (0, 0), 0.5, GRAY_BD),
        ('BOX', (1, 0), (1, 0), 0.5, GRAY_BD),
        ('BACKGROUND', (0, 0), (0, 0), GRAY_BG),
        ('BACKGROUND', (1, 0), (1, 0), LIGHT_BLUE),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
    ]))
    el.append(pay_t)
    el.append(Spacer(1, 8))
    el.append(Paragraph(f"Payment due within {invoice_data.get('payment_terms') or 'Net 30'}. Reference invoice {inv_num} on all correspondence.", ParagraphStyle(name='PayNote', fontSize=7, textColor=GRAY_TEXT, alignment=TA_CENTER)))

    el.extend(_footer(s, inv_num))

    doc.build(el)
    buffer.seek(0)
    return buffer.read()
