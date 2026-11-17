"""Діаграми у форматі SVG без зовнішніх бібліотек — працюють офлайн у закритому контурі."""
from html import escape
import math

# Палітра 2026: основа — Pantone Cloud Dancer (теплий білий), акцент — бірюзовий.
# Рівні зрілості — послідовна шкала від світлого до насиченого (без пари «червоний–зелений»).
LEVEL_COLORS = ["#E4EFED", "#B9DAD4", "#82BDB3", "#3F968B", "#1D6B64"]
INK = "#1F2430"
MUTED = "#6B7080"
GRID = "#E3E0D9"
RG_COLOR = "#C08A3E"     # робоча група — теплий бурштиновий
OS_COLOR = "#1D6B64"     # особовий склад — глибокий бірюзовий


def _f(x):
    return f"{x:.1f}".replace(".", ",")


def gauge(index, label):
    """Півколо 0–100 для Integrity Index."""
    w, h, r, cx, cy = 260, 190, 100, 130, 125
    def pt(v):
        a = math.pi * (1 - v / 100)
        return cx + r * math.cos(a), cy - r * math.sin(a)
    segs = []
    for i in range(5):
        x1, y1 = pt(i * 20)
        x2, y2 = pt((i + 1) * 20)
        segs.append(f'<path d="M{x1:.1f},{y1:.1f} A{r},{r} 0 0 1 {x2:.1f},{y2:.1f}" stroke="{LEVEL_COLORS[i]}" stroke-width="22" fill="none"/>')
    v = 0 if index is None else index
    nx, ny = pt(v)
    val = "—" if index is None else _f(index)
    return (f'<svg viewBox="0 0 {w} {h}" class="gauge" role="img" aria-label="Integrity Index {val}">'
            + "".join(segs)
            + f'<circle cx="{nx:.1f}" cy="{ny:.1f}" r="9" fill="#fff" stroke="{INK}" stroke-width="3"/>'
            f'<text x="{cx}" y="{cy - 10}" text-anchor="middle" class="g-val">{val}</text>'
            f'<text x="{cx}" y="{cy + 14}" text-anchor="middle" class="g-lab">з 100</text>'
            f'<text x="{cx}" y="{cy + 50}" text-anchor="middle" class="g-lev">{escape(label)}</text></svg>')


def level_bars(rows, lw=210, bw=300):
    """Горизонтальні смуги рівня 1–5: rows = [(назва, рівень, посилання)]."""
    rh = 34
    h = rh * len(rows) + 26
    out = [f'<svg viewBox="0 0 {lw + bw + 60} {h}" class="bars" role="img">']
    for i in range(5):
        x = lw + bw * i / 4
        out.append(f'<line x1="{x:.0f}" y1="0" x2="{x:.0f}" y2="{h - 22}" stroke="{GRID}"/>'
                   f'<text x="{x:.0f}" y="{h - 6}" text-anchor="middle" class="ax">{i + 1}</text>')
    for k, (name, lv, href) in enumerate(rows):
        y = k * rh + 6
        if lv is not None:
            wpx = bw * (lv - 1) / 4
            col = LEVEL_COLORS[min(4, max(0, int(lv + 1e-9) - 1))]
            out.append(f'<rect x="{lw}" y="{y}" width="{max(wpx, 2):.1f}" height="20" rx="4" fill="{col}"/>'
                       f'<text x="{lw + wpx + 8:.1f}" y="{y + 15}" class="val">{_f(lv)}</text>')
        maxc = int(lw / 7.4)  # скільки символів уміщується в колонку підписів
        short = name if len(name) <= maxc else name[:maxc - 1].rstrip() + "…"
        label = f"<title>{escape(name)}</title>{escape(short)}"
        if href:
            label = f'<a href="{href}">{label}</a>'
        out.append(f'<text x="{lw - 10}" y="{y + 15}" text-anchor="end" class="lab">{label}</text>')
    out.append("</svg>")
    return "".join(out)


def stacked(dist_rows):
    """100 % смуги розподілу відповідей за рівнями: [(назва, [p1..p5] або None)]."""
    rh, lw, bw = 32, 250, 420
    h = rh * len(dist_rows) + 4
    out = [f'<svg viewBox="0 0 {lw + bw + 10} {h}" class="stacked" role="img">']
    for k, (name, d) in enumerate(dist_rows):
        y = k * rh + 4
        out.append(f'<text x="{lw - 10}" y="{y + 15}" text-anchor="end" class="lab">{escape(name)}</text>')
        if d is None:
            out.append(f'<rect x="{lw}" y="{y}" width="{bw}" height="22" rx="4" fill="#F3F1EC" stroke="{GRID}"/>'
                       f'<text x="{lw + bw / 2}" y="{y + 15}" text-anchor="middle" class="ax">приховано або немає даних</text>')
            continue
        x = lw
        for i, p in enumerate(d):
            wpx = bw * p / 100
            if wpx <= 0:
                continue
            out.append(f'<rect x="{x:.1f}" y="{y}" width="{wpx:.1f}" height="22" fill="{LEVEL_COLORS[i]}"><title>Рівень {i + 1}: {_f(p)} %</title></rect>')
            if wpx > 30:
                tc = "#fff" if i >= 3 else INK
                out.append(f'<text x="{x + wpx / 2:.1f}" y="{y + 15}" text-anchor="middle" class="in" fill="{tc}">{p:.0f}%</text>')
            x += wpx
    out.append("</svg>")
    return "".join(out)


def line(series, labels, ymin=0, ymax=100, unit="", w=520):
    """Лінійна діаграма динаміки: series = [(назва, колір, [значення])]."""
    h, l, r, t, b = 220, 40, 120, 16, 30
    pw, ph = w - l - r, h - t - b
    n = len(labels)
    def xy(i, v):
        x = l + (pw * i / (n - 1) if n > 1 else pw / 2)
        y = t + ph * (1 - (v - ymin) / (ymax - ymin))
        return x, y
    out = [f'<svg viewBox="0 0 {w} {h}" class="line" role="img">']
    for gv in range(int(ymin), int(ymax) + 1, int((ymax - ymin) / 4) or 1):
        _, y = xy(0, gv)
        out.append(f'<line x1="{l}" y1="{y:.1f}" x2="{l + pw}" y2="{y:.1f}" stroke="{GRID}"/>'
                   f'<text x="{l - 6}" y="{y + 4:.1f}" text-anchor="end" class="ax">{gv}</text>')
    for i, lab in enumerate(labels):
        x, _ = xy(i, ymin)
        out.append(f'<text x="{x:.1f}" y="{h - 8}" text-anchor="middle" class="ax">{escape(str(lab))}</text>')
    for name, col, vals in series:
        pts = [xy(i, v) for i, v in enumerate(vals) if v is not None]
        if not pts:
            continue
        out.append('<polyline fill="none" stroke="{}" stroke-width="2.5" points="{}"/>'.format(
            col, " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)))
        for (x, y), v in zip(pts, [v for v in vals if v is not None]):
            out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{col}"><title>{_f(v)}{unit}</title></circle>')
        lx, ly = pts[-1]
        out.append(f'<text x="{lx + 8:.1f}" y="{ly + 4:.1f}" class="lab" fill="{col}">{escape(name)}</text>')
    out.append("</svg>")
    return "".join(out)


def radar(axes, series):
    """Радарна діаграма за питаннями: axes = [назви], series = [(назва, колір, [рівні])]."""
    size, cx, cy, R = 360, 180, 175, 120
    n = len(axes)
    def pt(i, v):
        a = -math.pi / 2 + 2 * math.pi * i / n
        rr = R * (v - 1) / 4 if v is not None else 0
        return cx + rr * math.cos(a), cy + rr * math.sin(a)
    out = [f'<svg viewBox="0 0 {size} {size}" class="radar" role="img">']
    for lv in range(2, 6):
        ring = " ".join(f"{x:.1f},{y:.1f}" for x, y in (pt(i, lv) for i in range(n)))
        out.append(f'<polygon points="{ring}" fill="none" stroke="{GRID}"/>')
    for i, name in enumerate(axes):
        x, y = pt(i, 5)
        lx, ly = pt(i, 5.55)
        anchor = "middle" if abs(lx - cx) < 10 else ("start" if lx > cx else "end")
        out.append(f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" stroke="{GRID}"/>'
                   f'<text x="{lx:.1f}" y="{ly + 4:.1f}" text-anchor="{anchor}" class="ax">{escape(name)}</text>')
    for name, col, vals in series:
        if all(v is None for v in vals):
            continue
        poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in (pt(i, v if v is not None else 1) for i, v in enumerate(vals)))
        out.append(f'<polygon points="{poly}" fill="{col}" fill-opacity="0.15" stroke="{col}" stroke-width="2"/>')
    out.append("</svg>")
    return "".join(out)
