"""Камертон — вебзастосунок Integrity Management Dashboard (прототип).

Запуск:  uvicorn app.main:app --reload
"""
import hashlib
import json
import os
import secrets
import uuid
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from . import charts, codes_pdf, db
from . import engine as E

BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE / "templates")
T = templates.env
T.globals.update(E=E, charts=charts, fmt=E.fmt, level_name=E.level_name, level_num=E.level_num,
                 LEVEL_COLORS=charts.LEVEL_COLORS, settings=E.SETTINGS)

ROLES = {
    "kerivnyk": "Керівник",
    "upov": "Уповноважена особа",
    "vykonavets": "Відповідальний за виконання",
}
REC_STATUS = {"proposed": "Запропоновано", "accepted": "Прийнято", "rejected": "Відхилено",
              "in_progress": "Виконується", "done": "Виконано"}
T.globals.update(ROLES=ROLES, REC_STATUS=REC_STATUS)


# ---------------------------------------------------------------- допоміжне
def role(request):
    return request.session.get("role")


def need(request, *allowed):
    r = role(request)
    return r if r in allowed else None


def render(request, name, **ctx):
    ctx.setdefault("role", role(request))
    ctx.setdefault("flash", request.session.pop("flash", None))
    return templates.TemplateResponse(request, name, ctx)


def go(url, request=None, flash=None):
    if request is not None and flash:
        request.session["flash"] = flash
    return RedirectResponse(url, status_code=303)


def cycles(con):
    return [dict(r) for r in con.execute("SELECT * FROM cycles ORDER BY year")]


def closed_cycles(con):
    return [c for c in cycles(con) if c["status"] == "closed"]


def checklist_of(con, cid):
    return {r["item_no"]: r["status"] for r in con.execute("SELECT * FROM checklist_status WHERE cycle_id=?", (cid,))}


def indicators_of(con, cid):
    """Значення об'єктивних показників: внесені (файл, API, вручну) і ті, що система рахує сама."""
    vals = {r["ind_id"]: r["value"] for r in con.execute("SELECT * FROM indicators WHERE cycle_id=?", (cid,))}
    issued = con.execute("SELECT COUNT(*) FROM codes WHERE cycle_id=?", (cid,)).fetchone()[0]
    if issued:
        used = con.execute("SELECT COUNT(*) FROM codes WHERE cycle_id=? AND used=1", (cid,)).fetchone()[0]
        vals["response_rate"] = round(100 * used / issued, 1)
    prev = con.execute("""SELECT status FROM recommendations WHERE cycle_id<? AND status IN
                          ('accepted','in_progress','done')""", (cid,)).fetchall()
    if prev:
        vals["measures_on_time"] = round(100 * sum(1 for r in prev if r["status"] == "done") / len(prev), 1)
    return vals


RG_OPTIONS = {"30": (0.3, 0.7), "50": (0.5, 0.5), "0": (0.0, 1.0)}


def result(con, cid, rg=None):
    st = None
    if rg in RG_OPTIONS and rg != "30":
        st = json.loads(json.dumps(E.SETTINGS))
        st["weights"]["groups"] = {"RG": RG_OPTIONS[rg][0], "OS": RG_OPTIONS[rg][1]}
    return E.compute(db.responses(con, cid), checklist_of(con, cid), st, indicators_of(con, cid))


def pick_cycle(request, con):
    cl = closed_cycles(con)
    if not cl:
        return None, cl
    cid = request.query_params.get("cycle")
    cur = next((c for c in cl if str(c["id"]) == cid), cl[-1])
    return cur, cl


# ---------------------------------------------------------------- загальні сторінки
async def home(request):
    return render(request, "index.html")


async def login(request):
    r = request.path_params["role"]
    if r not in ROLES:
        return go("/")
    request.session["role"] = r
    with db.tx() as con:
        db.log(con, ROLES[r], "Вхід у систему (демо)")
    return go({"kerivnyk": "/dashboard", "upov": "/cycles", "vykonavets": "/recommendations"}[r])


async def logout(request):
    request.session.clear()
    return go("/")


async def dashboard(request):
    if not need(request, *ROLES):
        return go("/", request, "Увійдіть, щоб переглянути панель.")
    con = db.connect()
    cur, cl = pick_cycle(request, con)
    if not cur:
        return render(request, "dashboard.html", cur=None)
    rg = request.query_params.get("rg", "30")
    rg = rg if rg in RG_OPTIONS else "30"
    res = result(con, cur["id"], rg)
    hist = [(c, result(con, c["id"], rg)) for c in cl]
    prev = next((r for c, r in reversed(hist) if c["year"] < cur["year"]), None)
    draft = con.execute("SELECT * FROM drafts WHERE cycle_id=?", (cur["id"],)).fetchone()
    recs = [dict(r) for r in con.execute("SELECT * FROM recommendations ORDER BY id")]
    active = next((c for c in cycles(con) if c["status"] == "active"), None)
    series = {
        "years": [c["year"] for c, _ in hist],
        "index": [r["index"] for _, r in hist],
        **{c["id"]: [r["components"][c["id"]] for _, r in hist] for c in E.COMPONENTS},
        "radar_rg": [res["questions"][q["id"]]["median"]["RG"] for q in E.QUESTIONS],
        "radar_os": [res["questions"][q["id"]]["median"]["OS"] for q in E.QUESTIONS],
    }
    return render(request, "dashboard.html", cur=cur, cl=cl, res=res, hist=hist, prev=prev, recs=recs,
                  active=active, series=series, rg=rg, draft=dict(draft) if draft else None)


async def question(request):
    if not need(request, *ROLES):
        return go("/", request, "Увійдіть, щоб переглянути дані.")
    qid = request.path_params["qid"]
    if qid not in E.QBY:
        return go("/dashboard")
    con = db.connect()
    cur, cl = pick_cycle(request, con)
    res = result(con, cur["id"])
    hist = [(c, result(con, c["id"])) for c in cl]
    items = [i for i in E.CHECKLIST["items"] if qid in i["questions"]]
    recs = [dict(r) for r in con.execute("SELECT * FROM recommendations WHERE question_id=? ORDER BY cycle_id", (qid,))]
    inds = [(E.IND_BY[r["id"]], r) for r in res["indicators"] if E.IND_BY[r["id"]]["question"] == qid]
    return render(request, "question.html", q=E.QBY[qid], cur=cur, cl=cl, res=res, hist=hist,
                  desc=E.describe(qid, res), items=items, recs=recs, inds=inds,
                  measures={m["id"]: m for m in E.MEASURES["measures"]})


async def methodology(request):
    con = db.connect()
    cur, cl = pick_cycle(request, con)
    res = result(con, cur["id"]) if cur else None
    return render(request, "methodology.html", cur=cur, res=res, Q=E.Q, CHECKLIST=E.CHECKLIST, MEASURES=E.MEASURES)


# ---------------------------------------------------------------- уповноважена особа
async def cycles_page(request):
    if not need(request, "upov", "kerivnyk"):
        return go("/", request, "Розділ доступний уповноваженій особі.")
    con = db.connect()
    rows = []
    for c in cycles(con):
        cov = {}
        for g in E.GROUPS:
            issued = con.execute("SELECT COUNT(*) FROM codes WHERE cycle_id=? AND grp=?", (c["id"], g)).fetchone()[0]
            used = con.execute("SELECT COUNT(*) FROM codes WHERE cycle_id=? AND grp=? AND used=1", (c["id"], g)).fetchone()[0]
            cov[g] = (used, issued)
        printable = con.execute("SELECT COUNT(*) FROM demo_codes WHERE cycle_id=?", (c["id"],)).fetchone()[0]
        rows.append((c, cov, printable))
    active = any(c["status"] == "active" for c, _, _ in rows)
    from datetime import date
    return render(request, "cycles.html", rows=rows, active=active, next_year=max(c["year"] for c, _, _ in rows) + 1 if rows else date.today().year,
                  survey_qr=codes_pdf.qr_svg(str(request.base_url) + "survey", 200), base=str(request.base_url))


async def cycle_close(request):
    if not need(request, "upov"):
        return go("/")
    cid = int(request.path_params["cid"])
    with db.tx() as con:
        con.execute("UPDATE cycles SET status='closed', closed=date('now') WHERE id=? AND status='active'", (cid,))
        con.execute("DELETE FROM demo_codes WHERE cycle_id=?", (cid,))
        db.log(con, ROLES["upov"], "Завершено цикл оцінювання", f"цикл {cid}")
    return go("/cycles", request, "Цикл завершено. Результати доступні на панелі керівника.")


async def checklist_page(request):
    if not need(request, "upov", "kerivnyk"):
        return go("/")
    cid = int(request.path_params["cid"])
    con = db.connect()
    c = dict(con.execute("SELECT * FROM cycles WHERE id=?", (cid,)).fetchone())
    rows = {r["item_no"]: dict(r) for r in con.execute("SELECT * FROM checklist_status WHERE cycle_id=?", (cid,))}
    if request.method == "POST":
        form = await request.form()
        with db.tx() as con2:
            if role(request) == "upov":
                for item in E.CHECKLIST["items"]:
                    st = form.get("st_" + item["no"])
                    if st in ("full", "partial", "todo"):
                        con2.execute("""INSERT INTO checklist_status (cycle_id, item_no, status, entered_by, confirmed_by, note)
                                        VALUES (?,?,?,?,NULL,?) ON CONFLICT(cycle_id, item_no) DO UPDATE SET
                                        status=excluded.status, entered_by=excluded.entered_by,
                                        confirmed_by=CASE WHEN checklist_status.status=excluded.status THEN checklist_status.confirmed_by ELSE NULL END,
                                        note=excluded.note""",
                                     (cid, item["no"], st, ROLES["upov"], form.get("note_" + item["no"], "")))
                db.log(con2, ROLES["upov"], "Внесено статуси чек-листа", f"цикл {cid}")
            else:
                con2.execute("UPDATE checklist_status SET confirmed_by=? WHERE cycle_id=?", (ROLES["kerivnyk"], cid))
                db.log(con2, ROLES["kerivnyk"], "Підтверджено чек-лист", f"цикл {cid}")
        return go(f"/checklist/{cid}", request, "Збережено.")
    return render(request, "checklist.html", c=c, rows=rows, CHECKLIST=E.CHECKLIST)


async def audit_page(request):
    if not need(request, "upov", "kerivnyk"):
        return go("/")
    con = db.connect()
    rows = [dict(r) for r in con.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 200")]
    return render(request, "audit.html", rows=rows)


ALPH = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def new_code():
    return "-".join("".join(secrets.choice(ALPH) for _ in range(4)) for _ in range(2))


async def cycle_new(request):
    if not need(request, "upov"):
        return go("/")
    f = await request.form()
    with db.tx() as con:
        if con.execute("SELECT 1 FROM cycles WHERE status='active'").fetchone():
            return go("/cycles", request, "Спершу завершіть активний цикл: одночасно може тривати лише один.")
        try:
            year = int(f.get("year"))
            counts = {g: max(0, min(2000, int(f.get("n_" + g) or 0))) for g in E.GROUPS}
        except ValueError:
            return go("/cycles", request, "Перевірте рік і кількість кодів.")
        cur = con.execute("INSERT INTO cycles (year, title, status, opened) VALUES (?,?, 'active', date('now'))",
                          (year, f"Оцінювання {year} року"))
        cid = cur.lastrowid
        for g, n in counts.items():
            for _ in range(n):
                c = new_code()
                con.execute("INSERT INTO codes (code_hash, cycle_id, grp, used) VALUES (?,?,?,0)", (h(c), cid, g))
                con.execute("INSERT INTO demo_codes (code, cycle_id, grp) VALUES (?,?,?)", (c, cid, g))
        db.log(con, ROLES["upov"], "Створено цикл оцінювання і згенеровано коди",
               f"{year}: " + ", ".join(f"{E.GROUPS[g]['title']} — {n}" for g, n in counts.items()))
    return go("/cycles", request, "Цикл створено. Завантажте аркуш кодів для друку.")


async def cycle_sheet(request):
    if not need(request, "upov"):
        return go("/")
    cid = int(request.path_params["cid"])
    con = db.connect()
    c = con.execute("SELECT * FROM cycles WHERE id=?", (cid,)).fetchone()
    order = {g: i for i, g in enumerate(E.GROUPS)}
    codes = sorted([(r["code"], r["grp"]) for r in con.execute("SELECT code, grp FROM demo_codes WHERE cycle_id=?", (cid,))],
                   key=lambda x: (order[x[1]], x[0]))
    if not codes:
        return go("/cycles", request, "Для цього циклу немає кодів для друку (їх уже видалено після друку).")
    with db.tx() as con2:
        db.log(con2, ROLES["upov"], "Завантажено аркуш кодів для друку", f"цикл {c['year']}, кодів: {len(codes)}")
    pdf = codes_pdf.sheet(codes, str(request.base_url), E.SETTINGS["institution"], c["title"],
                          {g: m["title"] for g, m in E.GROUPS.items()})
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="kody-dostupu-{c["year"]}.pdf"'})


async def cycle_purge(request):
    if not need(request, "upov"):
        return go("/")
    cid = int(request.path_params["cid"])
    with db.tx() as con:
        n = con.execute("DELETE FROM demo_codes WHERE cycle_id=?", (cid,)).rowcount
        db.log(con, ROLES["upov"], "Видалено відкриті коди після друку", f"цикл {cid}, кодів: {n}")
    return go("/cycles", request, "Відкриті коди видалено з системи. У базі лишилися тільки їхні хеші.")


async def survey_link(request):
    """Перехід за QR-кодом: /s/<код> одразу відкриває анкету."""
    return await _start(request, request.path_params["code"])


# ---------------------------------------------------------------- рекомендації
async def recommendations(request):
    if not need(request, *ROLES):
        return go("/", request, "Увійдіть, щоб переглянути рекомендації.")
    con = db.connect()
    cl = closed_cycles(con)
    cur = cl[-1]
    res = result(con, cur["id"])
    existing = [dict(r) for r in con.execute("SELECT * FROM recommendations ORDER BY cycle_id DESC, id")]
    taken = {(r["cycle_id"], r["question_id"]) for r in existing}
    proposals = [p for p in E.propose(res) if (cur["id"], p["question"]) not in taken]
    # перевірка результату наступним циклом
    results = {c["id"]: result(con, c["id"]) for c in cl}
    order = [c["id"] for c in cl]
    for r in existing:
        r["level_then"] = results.get(r["cycle_id"], {}).get("questions", {}).get(r["question_id"], {}).get("level")
        nxt = order[order.index(r["cycle_id"]) + 1] if r["cycle_id"] in order and order.index(r["cycle_id"]) + 1 < len(order) else None
        r["level_next"] = results[nxt]["questions"][r["question_id"]]["level"] if nxt else None
        r["year"] = next(c["year"] for c in cycles(con) if c["id"] == r["cycle_id"])
    from datetime import date
    return render(request, "recommendations.html", cur=cur, res=res, proposals=proposals, existing=existing,
                  today=date.today().isoformat(),
                  measures={m["id"]: m for m in E.MEASURES["measures"]})


async def rec_decide(request):
    if not need(request, "kerivnyk"):
        return go("/recommendations", request, "Рішення щодо рекомендацій ухвалює керівник.")
    f = await request.form()
    action = f.get("action")
    if action != "accept" and not (f.get("note") or "").strip():
        return go("/recommendations", request, "Щоб відхилити рекомендацію, вкажіть причину: вона потрапить у журнал дій.")
    with db.tx() as con:
        con.execute("""INSERT INTO recommendations (cycle_id, question_id, measure_id, reason, status, responsible, due,
                       decided_by, decision_note, result_note) VALUES (?,?,?,?,?,?,?,?,?,'')""",
                    (int(f["cycle_id"]), f["question_id"], f["measure_id"], f["reason"],
                     "accepted" if action == "accept" else "rejected",
                     f.get("responsible", ""), f.get("due", ""), ROLES["kerivnyk"], f.get("note", "")))
        db.log(con, ROLES["kerivnyk"], "Рекомендацію " + ("прийнято" if action == "accept" else "відхилено"),
               f"{f['measure_id']} ({f['question_id']})" + (f": {f['note'].strip()}" if (f.get("note") or "").strip() else ""))
    return go("/recommendations", request, "Рішення збережено.")


async def rec_status(request):
    if not need(request, "vykonavets", "upov"):
        return go("/recommendations", request, "Статус виконання оновлює відповідальний.")
    f = await request.form()
    st = f.get("status")
    if st in ("in_progress", "done"):
        with db.tx() as con:
            con.execute("UPDATE recommendations SET status=?, result_note=? WHERE id=?", (st, f.get("result_note", ""), int(f["id"])))
            db.log(con, ROLES[role(request)], "Оновлено статус рекомендації", f"№ {f['id']}: {REC_STATUS[st]}")
    return go("/recommendations", request, "Статус оновлено.")


# ---------------------------------------------------------------- опитування (анонімне)
def h(code):
    return hashlib.sha256(code.strip().upper().encode()).hexdigest()


async def survey(request):
    con = db.connect()
    active = next((c for c in cycles(con) if c["status"] == "active"), None)
    demo = {}
    if active and E.SETTINGS.get("demo_mode"):
        for g in E.GROUPS:
            r = con.execute("SELECT code FROM demo_codes WHERE cycle_id=? AND grp=? LIMIT 1", (active["id"], g)).fetchone()
            demo[g] = r["code"] if r else None
    return render(request, "survey_code.html", active=active, demo=demo)


async def survey_start(request):
    f = await request.form()
    return await _start(request, f.get("code") or "")


async def _start(request, code):
    code = code.strip().upper()
    con = db.connect()
    row = con.execute("""SELECT codes.*, cycles.status FROM codes JOIN cycles ON cycles.id = codes.cycle_id
                         WHERE code_hash=?""", (h(code),)).fetchone()
    if not row:
        return go("/survey", request, "Код не знайдено. Перевірте, чи правильно його введено.")
    if row["status"] != "active":
        # картка з кодом — «квиток» до результатів: після завершення циклу той самий QR відкриває результати
        request.session["respondent_grp"] = row["grp"]
        return go(f"/results?grp={row['grp']}", request, "Опитування завершено. Ось узагальнені результати.")
    if row["used"]:
        return go("/survey", request, "Ви вже пройшли опитування за цим кодом. Після завершення циклу цей самий QR відкриє результати.")
    request.session["survey"] = {"h": row["code_hash"], "cycle": row["cycle_id"], "grp": row["grp"]}
    return go("/survey/form")


async def survey_form(request):
    s = request.session.get("survey")
    if not s:
        return go("/survey")
    qn = E.GROUPS[s["grp"]]["questionnaire"]
    qs = [q for q in E.Q["questions"] if qn in q["text"]]
    if request.method == "POST":
        f = await request.form()
        answers = {}
        for q in qs:
            v = f.get(q["id"])
            if v not in ("1", "2", "3", "4", "5"):
                return render(request, "survey_form.html", s=s, qs=qs, qn=qn, error="Дайте відповідь на всі питання.", given=dict(f))
            answers[q["id"]] = int(v)
        with db.tx() as con:
            cur = con.execute("UPDATE codes SET used=1 WHERE code_hash=? AND used=0", (s["h"],))
            if cur.rowcount != 1:
                request.session.pop("survey", None)
                return go("/survey", request, "Цей код уже використано.")
            # відповідь зберігається без коду, часу та IP; ідентифікатор випадковий
            con.execute("INSERT INTO responses (id, cycle_id, grp, answers) VALUES (?,?,?,?)",
                        (uuid.uuid4().hex, s["cycle"], s["grp"], json.dumps(answers)))
            # демо-режим: прибрати використаний код зі списку підказок
            for r in con.execute("SELECT code FROM demo_codes WHERE cycle_id=? AND grp=?", (s["cycle"], s["grp"])).fetchall():
                if h(r["code"]) == s["h"]:
                    con.execute("DELETE FROM demo_codes WHERE code=?", (r["code"],))
        request.session.pop("survey", None)
        request.session["respondent_grp"] = s["grp"]
        return go("/survey/thanks")
    return render(request, "survey_form.html", s=s, qs=qs, qn=qn, error=None, given={})


async def survey_thanks(request):
    return render(request, "survey_thanks.html", grp=request.session.get("respondent_grp"))


async def results_public(request):
    """Екран учасника: узагальнені результати його категорії, без можливості ідентифікації."""
    con = db.connect()
    cl = closed_cycles(con)
    cur = cl[-1]
    res = result(con, cur["id"])
    grp = request.query_params.get("grp") or request.session.get("respondent_grp") or "STUD"
    if grp not in E.GROUPS:
        grp = "STUD"
    cmp = [{"id": q["id"], "title": f"{q['report_no']} {q['title']}", "level": res["questions"][q["id"]]["level"],
            "groups": {g: res["questions"][q["id"]]["median"][g] for g in E.GROUPS}} for q in E.QUESTIONS]
    return render(request, "results.html", cur=cur, res=res, grp=grp, cmp=cmp)


# ---------------------------------------------------------------- демо і API
async def demo_reset(request):
    if not E.SETTINGS.get("demo_mode"):
        return go("/")
    from scripts.generate_demo_data import main as regenerate
    regenerate()
    request.session.clear()
    return go("/", request, "Демонстраційні дані відновлено.")


async def api_cycles(request):
    con = db.connect()
    return JSONResponse([{k: c[k] for k in ("id", "year", "title", "status")} for c in cycles(con)])


async def api_summary(request):
    con = db.connect()
    cid = int(request.path_params["cid"])
    c = con.execute("SELECT * FROM cycles WHERE id=?", (cid,)).fetchone()
    if not c or c["status"] != "closed":
        return JSONResponse({"error": "Результати доступні лише для завершених циклів"}, status_code=404)
    res = result(con, cid)
    return JSONResponse({
        "cycle": {"id": cid, "year": c["year"]},
        "integrity_index": res["index"], "maturity_level": E.level_name(res["level"]),
        "components": {k: v for k, v in res["components"].items()},
        "implementation_rate": res["implementation"],
        "questions": {q: {"level": r["level"], "median": r["median"]} for q, r in res["questions"].items()},
        "signals": [{k: s[k] for k in ("type", "question", "title", "rule")} for s in res["signals"]],
        "respondents": {g: n if res["visible"][g] else f"< {E.SETTINGS['thresholds']['min_group_size']}" for g, n in res["counts"].items()},
        "methodology": "/methodology",
    })


from .report import report_docx, report_pdf  # noqa: E402


async def report(request):
    if not need(request, *ROLES):
        return go("/")
    con = db.connect()
    cid = int(request.path_params["cid"])
    fmt_ = request.path_params["fmt"]
    c = dict(con.execute("SELECT * FROM cycles WHERE id=?", (cid,)).fetchone())
    res = result(con, cid)
    dr = con.execute("SELECT text FROM drafts WHERE cycle_id=? AND status='approved'", (cid,)).fetchone()
    c["draft"] = dr["text"] if dr else None
    with db.tx() as con2:
        db.log(con2, ROLES[role(request)], f"Сформовано звіт {fmt_.upper()}", f"цикл {c['year']}")
    if fmt_ == "docx":
        data = report_docx(c, res)
        mt = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    else:
        data = report_pdf(c, res)
        mt = "application/pdf"
    return Response(data, media_type=mt,
                    headers={"Content-Disposition": f'attachment; filename="camerton-zvit-{c["year"]}.{fmt_}"'})


# ---------------------------------------------------------------- об'єктивні показники
def _save_indicators(con, cid, values, source, who):
    from datetime import datetime
    saved = []
    for k, v in values.items():
        if k not in E.IND_BY or E.IND_BY[k]["auto"]:
            continue
        try:
            v = float(str(v).replace(",", ".").replace("%", "").strip())
        except ValueError:
            continue
        con.execute("""INSERT INTO indicators (cycle_id, ind_id, value, source, entered_by, ts) VALUES (?,?,?,?,?,?)
                       ON CONFLICT(cycle_id, ind_id) DO UPDATE SET value=excluded.value, source=excluded.source,
                       entered_by=excluded.entered_by, ts=excluded.ts""",
                    (cid, k, v, source, who, datetime.now().strftime("%Y-%m-%d %H:%M")))
        saved.append(k)
    return saved


def _parse_table(name, data):
    """CSV або Excel з колонками: показник (id або назва), значення."""
    rows = []
    if name.lower().endswith(".xlsx"):
        import io
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        rows = [[c for c in r] for r in wb.active.iter_rows(values_only=True)]
    else:
        import csv
        import io
        text = data.decode("utf-8-sig", errors="replace")
        dialect = csv.Sniffer().sniff(text[:2000], delimiters=",;\t") if text.strip() else csv.excel
        rows = list(csv.reader(io.StringIO(text), dialect))
    by_title = {i["title"].lower(): i["id"] for i in E.INDICATORS["indicators"]}
    out = {}
    for r in rows:
        if not r or len(r) < 2 or r[0] is None:
            continue
        key = str(r[0]).strip()
        key = key if key in E.IND_BY else by_title.get(key.lower())
        if key and r[1] not in (None, ""):
            out[key] = r[1]
    return out


async def indicators_page(request):
    if not need(request, "upov", "kerivnyk"):
        return go("/", request, "Розділ доступний уповноваженій особі.")
    cid = int(request.path_params["cid"])
    con = db.connect()
    c = dict(con.execute("SELECT * FROM cycles WHERE id=?", (cid,)).fetchone())
    if request.method == "POST":
        if role(request) != "upov":
            return go(f"/indicators/{cid}", request, "Показники вносить уповноважена особа.")
        f = await request.form()
        up = f.get("file")
        with db.tx() as con2:
            if up is not None and getattr(up, "filename", ""):
                try:
                    vals = _parse_table(up.filename, await up.read())
                except Exception:
                    return go(f"/indicators/{cid}", request, "Не вдалося прочитати файл. Потрібен CSV або Excel з колонками «показник» і «значення».")
                saved = _save_indicators(con2, cid, vals, f"файл {up.filename}", ROLES["upov"])
                db.log(con2, ROLES["upov"], "Імпортовано об'єктивні показники з файлу", f"{up.filename}: {len(saved)}")
                msg = f"Імпортовано показників: {len(saved)}."
            else:
                vals = {k[4:]: v for k, v in f.items() if k.startswith("ind_") and str(v).strip()}
                saved = _save_indicators(con2, cid, vals, "внесено вручну", ROLES["upov"])
                db.log(con2, ROLES["upov"], "Внесено об'єктивні показники вручну", f"цикл {c['year']}: {len(saved)}")
                msg = "Збережено."
        return go(f"/indicators/{cid}", request, msg)
    rows = {r["ind_id"]: dict(r) for r in con.execute("SELECT * FROM indicators WHERE cycle_id=?", (cid,))}
    res = result(con, cid)
    state = {r["id"]: r for r in res["indicators"]}
    return render(request, "indicators.html", c=c, rows=rows, state=state, res=res, IND=E.INDICATORS,
                  api_on=bool(os.environ.get("IMD_API_TOKEN")))


async def indicators_template(request):
    lines = ["показник;значення;назва (довідково)"] + [f"{i['id']};;{i['title']}" for i in E.INDICATORS["indicators"] if not i["auto"]]
    return Response("﻿" + "\n".join(lines) + "\n", media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="obiektyvni-pokaznyky-shablon.csv"'})


async def api_indicators(request):
    """Прийом показників від інших систем: POST з ключем у заголовку Authorization: Bearer <IMD_API_TOKEN>."""
    token = os.environ.get("IMD_API_TOKEN")
    if not token:
        return JSONResponse({"error": "Прийом даних вимкнено: не задано IMD_API_TOKEN"}, status_code=403)
    if request.headers.get("authorization", "") != f"Bearer {token}":
        return JSONResponse({"error": "Невірний ключ доступу"}, status_code=401)
    try:
        body = await request.json()
        year = int(body["year"])
        values = {i["id"]: i["value"] for i in body["indicators"]}
    except Exception:
        return JSONResponse({"error": 'Очікується JSON: {"year": 2026, "indicators": [{"id": "...", "value": 0}]}'}, status_code=400)
    with db.tx() as con:
        c = con.execute("SELECT * FROM cycles WHERE year=?", (year,)).fetchone()
        if not c:
            return JSONResponse({"error": f"Цикл {year} року не знайдено"}, status_code=404)
        saved = _save_indicators(con, c["id"], values, str(body.get("source") or "API"), "Інша система (API)")
        db.log(con, "Інша система (API)", "Отримано об'єктивні показники через API", f"{year}: {', '.join(saved)}")
    unknown = [k for k in values if k not in saved]
    return JSONResponse({"saved": saved, "ignored": unknown})


# ---------------------------------------------------------------- аналітична записка (ШІ-чернетка)
from . import ai  # noqa: E402


def _draft_ctx(con, cid):
    cl = closed_cycles(con)
    c = next(x for x in cl if x["id"] == cid)
    res = result(con, cid)
    prevc = next((x for x in reversed(cl) if x["year"] < c["year"]), None)
    prev = result(con, prevc["id"]) if prevc else None
    return c, res, prev, E.propose(res)


async def draft_page(request):
    if not need(request, "kerivnyk", "upov"):
        return go("/")
    cid = int(request.path_params["cid"])
    con = db.connect()
    c = dict(con.execute("SELECT * FROM cycles WHERE id=?", (cid,)).fetchone())
    if c["status"] != "closed":
        return go("/dashboard", request, "Записка формується лише для завершеного циклу.")
    if request.method == "POST":
        f = await request.form()
        act = f.get("action")
        from datetime import datetime
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        with db.tx() as con2:
            if act == "generate":
                c2, res, prev, props = _draft_ctx(con2, cid)
                text, src, model, warn = ai.generate(c2, res, prev, props)
                con2.execute("""INSERT INTO drafts (cycle_id, text, source, model, warnings, status, created, approved_by)
                                VALUES (?,?,?,?,?,'draft',?,NULL) ON CONFLICT(cycle_id) DO UPDATE SET text=excluded.text,
                                source=excluded.source, model=excluded.model, warnings=excluded.warnings, status='draft',
                                created=excluded.created, approved_by=NULL""", (cid, text, src, model, warn, now))
                db.log(con2, ROLES[role(request)], "Сформовано чернетку аналітичної записки",
                       f"цикл {c['year']}: " + (f"ШІ ({model})" if src == "ai" else "шаблон"))
                msg = "Чернетку сформовано. Перевірте й відредагуйте текст перед затвердженням."
            elif act == "save":
                con2.execute("UPDATE drafts SET text=?, source=CASE WHEN source='template' THEN 'template' ELSE 'edited' END, status='draft', approved_by=NULL WHERE cycle_id=?",
                             ((f.get("text") or "").strip(), cid))
                db.log(con2, ROLES[role(request)], "Відредаговано чернетку аналітичної записки", f"цикл {c['year']}")
                msg = "Зміни збережено."
            elif act == "approve" and role(request) == "kerivnyk":
                con2.execute("UPDATE drafts SET text=?, status='approved', approved_by=? WHERE cycle_id=?",
                             ((f.get("text") or "").strip(), ROLES["kerivnyk"], cid))
                db.log(con2, ROLES["kerivnyk"], "Затверджено аналітичну записку", f"цикл {c['year']}")
                msg = "Записку затверджено. Вона увійде до звіту DOCX і PDF."
            else:
                msg = "Затверджує записку керівник."
        return go(f"/draft/{cid}", request, msg)
    d = con.execute("SELECT * FROM drafts WHERE cycle_id=?", (cid,)).fetchone()
    return render(request, "draft.html", c=c, d=dict(d) if d else None, cfg=ai.config())


# ---------------------------------------------------------------- тижневе зведення
def digest_data(con):
    from datetime import date, timedelta
    today = date.today()
    soon = (today + timedelta(days=7)).isoformat()
    open_ = [dict(r) for r in con.execute("""SELECT * FROM recommendations WHERE status IN ('accepted','in_progress')
                                               ORDER BY due""")]
    meas = {m["id"]: m for m in E.MEASURES["measures"]}
    for r in open_:
        r["measure"] = meas.get(r["measure_id"], {}).get("title", r["measure_id"])
    week_ago = (today - timedelta(days=7)).isoformat()
    changes = [dict(r) for r in con.execute("""SELECT * FROM audit WHERE ts>=? AND action IN
        ('Оновлено статус рекомендації','Рекомендацію прийнято','Рекомендацію відхилено') ORDER BY id DESC""", (week_ago,))]
    return {
        "date": today.isoformat(),
        "overdue": [r for r in open_ if r["due"] and r["due"] < today.isoformat()],
        "due_soon": [r for r in open_ if r["due"] and today.isoformat() <= r["due"] <= soon],
        "in_work": [r for r in open_ if not r["due"] or r["due"] > soon],
        "changes": changes,
    }


async def digest(request):
    if not need(request, "kerivnyk", "upov"):
        return go("/")
    con = db.connect()
    return render(request, "digest.html", d=digest_data(con))


async def api_digest(request):
    con = db.connect()
    d = digest_data(con)
    slim = lambda rs: [{k: r[k] for k in ("measure_id", "measure", "responsible", "due", "status")} for r in rs]  # noqa: E731
    return JSONResponse({"date": d["date"], "overdue": slim(d["overdue"]), "due_soon": slim(d["due_soon"]),
                         "in_work": slim(d["in_work"]), "changes_last_7_days": len(d["changes"])})


if not db.DB_PATH.exists():
    from scripts.generate_demo_data import main as _seed
    _seed()

routes = [
    Route("/", home), Route("/login/{role}", login), Route("/logout", logout),
    Route("/dashboard", dashboard), Route("/question/{qid}", question), Route("/methodology", methodology),
    Route("/cycles", cycles_page), Route("/cycles/{cid:int}/close", cycle_close, methods=["POST"]),
    Route("/cycles/new", cycle_new, methods=["POST"]), Route("/cycles/{cid:int}/sheet.pdf", cycle_sheet),
    Route("/cycles/{cid:int}/purge", cycle_purge, methods=["POST"]), Route("/s/{code}", survey_link),
    Route("/checklist/{cid:int}", checklist_page, methods=["GET", "POST"]), Route("/audit", audit_page),
    Route("/recommendations", recommendations), Route("/recommendations/decide", rec_decide, methods=["POST"]),
    Route("/recommendations/status", rec_status, methods=["POST"]),
    Route("/survey", survey), Route("/survey/start", survey_start, methods=["POST"]),
    Route("/survey/form", survey_form, methods=["GET", "POST"]), Route("/survey/thanks", survey_thanks),
    Route("/results", results_public),
    Route("/report/{cid:int}.{fmt:str}", report),
    Route("/demo/reset", demo_reset, methods=["POST"]),
    Route("/api/cycles", api_cycles), Route("/api/cycles/{cid:int}/summary", api_summary),
    Route("/indicators/{cid:int}", indicators_page, methods=["GET", "POST"]),
    Route("/indicators/template.csv", indicators_template),
    Route("/api/indicators", api_indicators, methods=["POST"]),
    Route("/draft/{cid:int}", draft_page, methods=["GET", "POST"]),
    Route("/digest", digest), Route("/api/digest", api_digest),
    Mount("/static", StaticFiles(directory=BASE / "static"), name="static"),
]

app = Starlette(routes=routes, middleware=[
    Middleware(SessionMiddleware, secret_key=os.environ.get("IMD_SECRET", secrets.token_hex(16)),
               same_site="lax", https_only=False)])
