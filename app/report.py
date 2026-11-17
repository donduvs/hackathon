"""Звіт за структурою Додатка Г Рекомендацій НУОУ: DOCX (для редагування і підпису) і PDF (для перегляду).

Тексти описів формуються тими самими правилами, що й на екрані (engine.describe),
тому звіт і панель завжди збігаються.
"""
import io
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, Cm
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

from . import engine as E

NB = " "  # нерозривний пробіл: «№ 1», «від 12 травня» тощо не розриваються
FONTS = Path(__file__).resolve().parent / "fonts"
GROUP_COLS = [("RG", "Робоча група, %"), ("OS", "Особовий склад, %")]


def _opt_pct(res, qid, g, i):
    d = res["questions"][qid]["dist"].get(g)
    return "—" if d is None else E.fmt(d[i], 1)


def _sections():
    for comp in E.COMPONENTS:
        yield comp, [q for q in E.QUESTIONS if q["component"] == comp["id"]]


def _conclusion(res):
    strong = [E.QBY[q] for q in res["strong"]]
    weak = [E.QBY[q] for q in res["weak"]]
    lv = res["level"]
    nxt = min(5, E.level_num(lv) + 1)
    return strong, weak, E.level_name(lv), f"{nxt}. {E.LEVELS[nxt - 1]}"


def report_docx(cycle, res):
    inst = E.SETTINGS["institution"]
    d = Document()
    st = d.styles["Normal"]
    st.font.name = "Times New Roman"
    st.font.size = Pt(14)
    for s in d.sections:
        s.left_margin, s.right_margin, s.top_margin, s.bottom_margin = Cm(3), Cm(1.5), Cm(2), Cm(2)

    p = d.add_paragraph(f"ЗАТВЕРДЖУЮ\nГолова робочої групи\n\n____________________\n«___»{NB}____________{NB}{cycle['year']}{NB}року")
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    t = d.add_paragraph()
    t.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = t.add_run(f"ЗВІТ\nпро результати опитувань з визначення поточного рівня зрілості доброчесності {inst}")
    r.bold = True

    n_rg = res["counts"]["RG"]
    n_os = sum(res["counts"][g] for g in E.OS_SUB)
    # Якщо якусь підгрупу приховано, загальну суму не показуємо: з неї можна вирахувати приховане число.
    os_total = f"{n_os}{NB}респондентів: " if all(res["visible"][g] for g in E.OS_SUB) else ""
    d.add_paragraph(
        f"Проведено анонімне опитування двох категорій респондентів: членів робочої групи "
        f"(Анкета{NB}1, {n_rg}{NB}респондентів) та особового складу (Анкета{NB}2, {os_total}"
        + ", ".join(f"{E.GROUPS[g]['title'].lower()} — "
               + (str(res['counts'][g]) if res['visible'][g] else f"менше{NB}{E.SETTINGS['thresholds']['min_group_size']}")
               for g in E.OS_SUB) +
        "). Опитування проведено у вебсистемі за одноразовими анонімними кодами; відповіді зберігаються "
        "без зв'язку з кодом, часом і мережевою адресою респондента.")
    d.add_paragraph(
        f"Integrity Index становить {E.fmt(res['index'])} з 100; досягнутий рівень зрілості — {E.level_name(res['level'])}. "
        f"Методика: рівень питання — медіана відповідей; особовий склад — середнє підкатегорій з рівними частками; "
        f"рівень питання для закладу — {int(E.SETTINGS['weights']['groups']['RG']*100)}{NB}% робоча група і "
        f"{int(E.SETTINGS['weights']['groups']['OS']*100)}{NB}% особовий склад; компоненти — рівними частками.")

    for comp, qs in _sections():
        h = d.add_paragraph()
        h.add_run(f"Розділ {comp['report_no']}. {comp['title']}").bold = True
        for q in qs:
            d.add_paragraph().add_run(f"{q['report_no']}. {q['title']}").bold = True
            tb = d.add_table(rows=1, cols=4)
            tb.style = "Table Grid"
            hdr = ["Варіант відповіді", "Робоча група, %", "Особовий склад, %", "Рівень зрілості"]
            for i, txt in enumerate(hdr):
                tb.rows[0].cells[i].text = txt
            for i, name in enumerate(q["short_options"]):
                cells = tb.add_row().cells
                cells[0].text = name
                cells[1].text = _opt_pct(res, q["id"], "RG", i)
                cells[2].text = _opt_pct(res, q["id"], "OS", i)
                cells[3].text = f"{i + 1}. {E.LEVELS[i]}"
            for row in tb.rows:
                for c in row.cells:
                    for par in c.paragraphs:
                        for run in par.runs:
                            run.font.size = Pt(11)
            for _, text in E.describe(q["id"], res):
                d.add_paragraph(text)

    d.add_paragraph().add_run("Висновок").bold = True
    strong, weak, lvname, nxt = _conclusion(res)
    d.add_paragraph(f"На основі відповідей обох груп респондентів загальний рівень зрілості доброчесності "
                    f"{inst} оцінено як «{lvname}» (Integrity Index {E.fmt(res['index'])}).")
    d.add_paragraph("Найбільш ефективні напрями:")
    for i, q in enumerate(strong, 1):
        d.add_paragraph(f"{i}. {q['title']} ({E.fmt(res['questions'][q['id']]['level'])}).")
    d.add_paragraph("Напрями, що потребують більшої уваги:")
    for i, q in enumerate(weak, 1):
        d.add_paragraph(f"{i}. {q['title']} ({E.fmt(res['questions'][q['id']]['level'])}).")
    if res["signals"]:
        d.add_paragraph("Виявлені сигнали для додаткового аналізу:")
        for s in res["signals"]:
            d.add_paragraph(f"– {s['title']}. {s['text']}")
    d.add_paragraph("Пропозиції:")
    d.add_paragraph("1. Врахувати отримані результати під час розробки Плану заходів забезпечення функціонування "
                    f"{inst} на засадах прозорості та доброчесності.")
    d.add_paragraph(f"2. Передбачити заходи для переходу з рівня «{lvname}» на рівень «{nxt}».")
    d.add_paragraph("\nЗаступник голови робочої групи\t\t____________________")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def report_pdf(cycle, res):
    pdfmetrics.registerFont(TTFont("DejaVu", str(FONTS / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("DejaVu-Bold", str(FONTS / "DejaVuSans-Bold.ttf")))
    inst = E.SETTINGS["institution"]
    base = ParagraphStyle("b", fontName="DejaVu", fontSize=10, leading=14)
    bold = ParagraphStyle("h", parent=base, fontName="DejaVu-Bold", fontSize=11, spaceBefore=8, spaceAfter=4)
    title = ParagraphStyle("t", parent=bold, fontSize=13, alignment=1, leading=17)
    right = ParagraphStyle("r", parent=base, alignment=2)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=2.5 * cm, rightMargin=1.5 * cm, topMargin=1.8 * cm, bottomMargin=1.8 * cm,
                            title=f"Звіт {cycle['year']}")
    el = [Paragraph(f"ЗАТВЕРДЖУЮ<br/>Голова робочої групи<br/><br/>____________________<br/>«___»{NB}____________{NB}{cycle['year']}{NB}року", right),
          Spacer(1, 10),
          Paragraph(f"ЗВІТ<br/>про результати опитувань з визначення поточного рівня зрілості доброчесності {inst}", title),
          Paragraph(f"Integrity Index — <b>{E.fmt(res['index'])}</b> з 100; досягнутий рівень — <b>{E.level_name(res['level'])}</b>.", base)]
    for comp, qs in _sections():
        el.append(Paragraph(f"Розділ {comp['report_no']}. {comp['title']}", bold))
        for q in qs:
            el.append(Paragraph(f"{q['report_no']}. {q['title']}", bold))
            data = [["Варіант відповіді", "РГ, %", "ОС, %", "Рівень"]]
            for i, name in enumerate(q["short_options"]):
                data.append([Paragraph(name, base), _opt_pct(res, q["id"], "RG", i), _opt_pct(res, q["id"], "OS", i), f"{i + 1}. {E.LEVELS[i]}"])
            tb = Table(data, colWidths=[7.5 * cm, 1.8 * cm, 1.8 * cm, 3.6 * cm])
            tb.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "DejaVu", 9), ("FONT", (0, 0), (-1, 0), "DejaVu-Bold", 9),
                                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#B9B5AC")),
                                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F0EEE9")),
                                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
            el.append(tb)
            for _, text in E.describe(q["id"], res):
                el.append(Paragraph(text, base))
    strong, weak, lvname, nxt = _conclusion(res)
    el.append(Paragraph("Висновок", bold))
    el.append(Paragraph(f"Загальний рівень зрілості доброчесності оцінено як «{lvname}».", base))
    el.append(Paragraph("Найбільш ефективні напрями: " + "; ".join(q["title"] for q in strong) + ".", base))
    el.append(Paragraph("Напрями, що потребують більшої уваги: " + "; ".join(q["title"] for q in weak) + ".", base))
    for s in res["signals"]:
        el.append(Paragraph(f"– {s['title']}. {s['text']}", base))
    el.append(Paragraph(f"Пропозиція: передбачити заходи для переходу з рівня «{lvname}» на рівень «{nxt}».", base))
    doc.build(el)
    return buf.getvalue()
