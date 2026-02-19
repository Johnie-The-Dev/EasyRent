from __future__ import annotations

from io import BytesIO

from django.http import HttpResponse
from django.utils import timezone

from reportlab.lib.pagesizes import A4, landscape as rl_landscape
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas


def parse_range_days(request, default: int = 30) -> int:
    raw = request.GET.get("days")
    try:
        days = int(raw) if raw is not None else int(default)
    except (TypeError, ValueError):
        days = int(default)
    return max(1, min(days, 365))


def build_simple_table_pdf(
    *,
    title: str,
    subtitle: str,
    rows: list[list[str]],
    filename: str,
    landscape: bool = False,
) -> HttpResponse:
    """
    MVP-friendly PDF table renderer.
    - Fast
    - No external deps
    - Works well for ledgers + overview
    """
    buf = BytesIO()
    pagesize = rl_landscape(A4) if landscape else A4
    c = canvas.Canvas(buf, pagesize=pagesize)
    width, height = pagesize

    margin_x = 12 * mm
    margin_y = 14 * mm
    y = height - margin_y

    # Header
    c.setFont("Helvetica-Bold", 14)
    c.drawString(margin_x, y, title)
    y -= 7 * mm
    c.setFont("Helvetica", 9)
    c.drawString(margin_x, y, subtitle)
    y -= 10 * mm

    line_h = 4.6 * mm
    usable_h = y - margin_y
    max_lines = int(usable_h // line_h) if usable_h > 0 else 40

    col_count = max((len(r) for r in rows), default=1)
    col_max = [0] * col_count
    for r in rows:
        for i, val in enumerate(r):
            col_max[i] = max(col_max[i], len(str(val)))

    usable_w = width - (2 * margin_x)
    total_chars = sum(col_max) + (col_count - 1) * 3
    total_chars = max(total_chars, 1)
    col_w = [usable_w * (m / total_chars) for m in col_max]

    def footer():
        c.setFont("Helvetica", 7)
        c.drawRightString(width - margin_x, 8 * mm, f"Generated {timezone.now().strftime('%Y-%m-%d %H:%M')}")
        c.setFont("Helvetica", 8)

    def draw_row(vals, y_pos, bold=False):
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 8)
        x = margin_x
        for i in range(col_count):
            txt = str(vals[i]) if i < len(vals) else ""
            approx_chars = max(8, int((col_w[i] / usable_w) * total_chars) - 1)
            if len(txt) > approx_chars:
                txt = txt[: approx_chars - 1] + "…"
            c.drawString(x, y_pos, txt)
            x += col_w[i] + (3 * mm)

    line = 0
    for idx, r in enumerate(rows):
        if line >= max_lines:
            footer()
            c.showPage()
            y = height - margin_y
            c.setFont("Helvetica-Bold", 14)
            c.drawString(margin_x, y, title)
            y -= 7 * mm
            c.setFont("Helvetica", 9)
            c.drawString(margin_x, y, subtitle + " (continued)")
            y -= 10 * mm
            line = 0

        draw_row(r, y, bold=(idx == 0))
        y -= line_h
        line += 1

    footer()
    c.save()

    pdf = buf.getvalue()
    buf.close()

    resp = HttpResponse(content_type="application/pdf")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    resp.write(pdf)
    return resp
