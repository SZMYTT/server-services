"""
PDF generator MCP — HTML to PDF via WeasyPrint (falls back to ReportLab).

from systemOS.mcp.pdf import generate_pdf, render_html_pdf, invoice_pdf

    pdf_bytes = await generate_pdf("<h1>Hello</h1>")
    pdf_bytes = await render_html_pdf("templates/pdf/invoice.html", data={"name": "Jane"})
    pdf_bytes = await invoice_pdf({
        "invoice_number": "INV-001", "date": "3 May 2026",
        "customer_name": "Jane Doe",
        "items": [{"name": "Sourdough", "qty": 2, "unit_price": 6.50}],
        "business_name": "My Bakery",
    })
    Path("invoice.pdf").write_bytes(pdf_bytes)
"""
import io, logging, re, asyncio
from pathlib import Path
logger = logging.getLogger(__name__)

def _has_weasyprint():
    try:
        import weasyprint; return True
    except ImportError:
        return False

async def generate_pdf(html: str, base_url: str | None = None) -> bytes:
    """Convert HTML string → PDF bytes. Uses WeasyPrint if installed, else ReportLab."""
    if _has_weasyprint():
        import weasyprint
        def _sync():
            return weasyprint.HTML(string=html, base_url=base_url).write_pdf()
        return await asyncio.to_thread(_sync)
    logger.warning("[PDF] WeasyPrint not installed, using plain-text fallback")
    return await _reportlab(html)

async def render_html_pdf(template_path: str | Path, data: dict) -> bytes:
    """Render a PDF from an HTML template with {{placeholder}} substitution."""
    path = Path(template_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF template not found: {path}")
    html = path.read_text(encoding="utf-8")
    for k, v in data.items():
        html = html.replace("{{" + k + "}}", str(v))
    return await generate_pdf(html, base_url=path.parent.as_uri())

async def invoice_pdf(inv: dict) -> bytes:
    """Generate a styled invoice PDF from a dict. Keys: invoice_number, date, customer_name,
    items=[{name,qty,unit_price}], business_name, notes, currency (default £)."""
    cur = inv.get("currency", "£")
    items = inv.get("items", [])
    rows, subtotal = "", 0.0
    for it in items:
        qty, price = float(it.get("qty", 1)), float(it.get("unit_price", 0))
        line = qty * price; subtotal += line
        rows += (f"<tr><td style='padding:8px 12px;border-bottom:1px solid #E2D9C4'>{it.get('name','')}</td>"
                 f"<td style='padding:8px 12px;border-bottom:1px solid #E2D9C4;text-align:center'>{qty:.0f}</td>"
                 f"<td style='padding:8px 12px;border-bottom:1px solid #E2D9C4;text-align:right'>{cur}{price:.2f}</td>"
                 f"<td style='padding:8px 12px;border-bottom:1px solid #E2D9C4;text-align:right'>{cur}{line:.2f}</td></tr>")
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:Arial,sans-serif;font-size:13px;color:#162920;padding:48px}}
.hdr{{display:flex;justify-content:space-between;margin-bottom:40px}}
.biz{{font-size:22px;font-weight:700}}.sub{{color:#6B8578;font-size:12px;margin-top:3px}}
table{{width:100%;border-collapse:collapse}}
thead tr{{background:#F2EBD9}}thead th{{padding:8px 12px;text-align:left;font-size:11px;font-weight:600;text-transform:uppercase;color:#6B8578}}
.tot{{font-weight:700;font-size:15px}}</style></head><body>
<div class="hdr"><div><div class="biz">{inv.get("business_name","")}</div>
<div class="sub">{inv.get("business_email","")}</div></div>
<div style="text-align:right"><div style="font-size:20px;font-weight:700;color:#8C7455">INVOICE</div>
<div style="font-weight:600">#{inv.get("invoice_number","001")}</div>
<div style="color:#6B8578">Date: {inv.get("date","")}</div></div></div>
<div style="margin-bottom:24px"><div style="font-size:10px;font-weight:600;text-transform:uppercase;color:#6B8578;margin-bottom:4px">Bill To</div>
<div style="font-weight:600">{inv.get("customer_name","")}</div>
<div>{inv.get("customer_email","")}</div></div>
<table><thead><tr><th>Item</th><th style="text-align:center">Qty</th>
<th style="text-align:right">Unit Price</th><th style="text-align:right">Total</th></tr></thead>
<tbody>{rows}<tr class="tot"><td colspan="3" style="padding:12px;text-align:right">Total</td>
<td style="padding:12px;text-align:right">{cur}{subtotal:.2f}</td></tr></tbody></table>
{"<div style='margin-top:24px;padding:14px;background:#F2EBD9;border-radius:6px'><strong>Notes:</strong> " + inv.get("notes","") + "</div>" if inv.get("notes") else ""}
<div style="margin-top:48px;text-align:center;font-size:11px;color:#6B8578">Thank you for your business.</div>
</body></html>"""
    return await generate_pdf(html)

async def _reportlab(html: str) -> bytes:
    def _sync():
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.platypus import SimpleDocTemplate, Paragraph
            from reportlab.lib.styles import getSampleStyleSheet
            buf = io.BytesIO()
            doc = SimpleDocTemplate(buf, pagesize=A4)
            plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()
            doc.build([Paragraph(plain, getSampleStyleSheet()["Normal"])])
            return buf.getvalue()
        except ImportError:
            logger.error("[PDF] Neither weasyprint nor reportlab installed — pip install weasyprint")
            return b""
    return await asyncio.to_thread(_sync)
