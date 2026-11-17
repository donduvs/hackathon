"""Аркуш одноразових кодів доступу з QR-кодами (PDF, А4, 10 карток на сторінці).

Картки розрізають, перемішують і роздають навмання в межах категорії.
QR веде на адресу /s/<код>: телефон одразу відкриває анкету без введення коду.
"""
import io
from pathlib import Path

from reportlab.graphics import renderPDF, renderSVG
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

FONTS = Path(__file__).resolve().parent / "fonts"


def _fonts():
    if "DejaVu" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("DejaVu", str(FONTS / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont("DejaVu-Bold", str(FONTS / "DejaVuSans-Bold.ttf")))


def qr_drawing(text, size):
    w = QrCodeWidget(text)
    b = w.getBounds()
    bw, bh = b[2] - b[0], b[3] - b[1]
    d = Drawing(size, size, transform=[size / bw, 0, 0, size / bh, 0, 0])
    d.add(w)
    return d


def qr_svg(text, size=220):
    """QR як SVG-рядок для показу на екрані (наприклад, на проєкторі)."""
    svg = renderSVG.drawToString(qr_drawing(text, size))
    return svg[svg.find("<svg"):]


def sheet(codes, base_url, institution, cycle_title, groups):
    """codes: список (код, категорія). Повертає PDF-байти."""
    _fonts()
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(f"Коди доступу — {cycle_title}")
    W, H = A4
    cols, rows = 2, 5
    mx, my = 12 * mm, 12 * mm
    cw, ch = (W - 2 * mx) / cols, (H - 2 * my) / rows
    host = base_url.rstrip("/")
    for i, (code, grp) in enumerate(codes):
        k = i % (cols * rows)
        if i and k == 0:
            c.showPage()
        col, row = k % cols, k // cols
        x0, y0 = mx + col * cw, H - my - (row + 1) * ch
        c.setDash(2, 3)
        c.setStrokeColorRGB(0.7, 0.7, 0.7)
        c.rect(x0, y0, cw, ch)
        c.setDash()
        qs = 34 * mm
        renderPDF.draw(qr_drawing(f"{host}/s/{code}", qs), c, x0 + 4 * mm, y0 + (ch - qs) / 2)
        tx = x0 + qs + 7 * mm
        c.setFillColorRGB(0.12, 0.14, 0.19)
        c.setFont("DejaVu", 7)
        c.setFillColorRGB(0.11, 0.42, 0.39)
        c.drawString(tx, y0 + ch - 6.5 * mm, "КАМЕРТОН")
        c.setFillColorRGB(0.12, 0.14, 0.19)
        c.setFont("DejaVu-Bold", 10)
        c.drawString(tx, y0 + ch - 11 * mm, "Анонімне опитування")
        c.setFont("DejaVu", 8)
        c.drawString(tx, y0 + ch - 15.5 * mm, institution)
        c.drawString(tx, y0 + ch - 19.5 * mm, cycle_title)
        c.setFont("DejaVu", 8)
        c.setFillColorRGB(0.11, 0.42, 0.39)
        c.drawString(tx, y0 + ch - 25 * mm, groups[grp])
        c.setFillColorRGB(0.12, 0.14, 0.19)
        c.setFont("DejaVu-Bold", 15)
        c.drawString(tx, y0 + 17 * mm, code)
        c.setFont("DejaVu", 7)
        c.drawString(tx, y0 + 11.5 * mm, "Скануйте QR або введіть код на")
        c.setFont("DejaVu", 6)
        c.drawString(tx, y0 + 8 * mm, host.split("://")[-1] + "/survey")
        c.setFillColorRGB(0.42, 0.44, 0.5)
        c.setFont("DejaVu", 6)
        c.drawString(tx, y0 + 3.5 * mm, "Код одноразовий, не пов'язаний з вами.")
    c.save()
    return buf.getvalue()
