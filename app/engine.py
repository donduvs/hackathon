"""Розрахунковий модуль: рівні зрілості, Integrity Index, сигнали.

Усі висновки формуються за явними правилами, записаними тут і в
data/settings.json. Штучний інтелект у розрахунках не використовується.

Методика (коротко):
1. Рівень питання для групи — медіана відповідей (шкала 1–5).
   Якщо в групі менше відповідей, ніж поріг (min_group_size), результат приховується.
2. Особовий склад (ОС) = середнє медіан його підкатегорій
   (НПП, здобувачі освіти, адміністративний персонал) з рівними частками.
3. Рівень питання для закладу = 0,3 × РГ + 0,7 × ОС (ваги з settings.json).
   Якщо питання є лише в одній анкеті (роль керівника), береться наявна група.
4. Рівень компонента = середнє рівнів його питань.
5. Integrity Index = зважене середнє компонентів (по 1/3), переведене у шкалу 0–100:
   (рівень − 1) / 4 × 100.
6. Самооцінка (розділ 4 анкет) до індексу не входить; використовується
   лише для перевірки завищеної самооцінки.
"""
import json
from pathlib import Path
from statistics import median

DATA = Path(__file__).resolve().parent.parent / "data"


def load(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


Q = load("questionnaires.json")
CHECKLIST = load("checklist.json")
MEASURES = load("measures.json")
SETTINGS = load("settings.json")

LEVELS = [lv["title"] for lv in Q["levels"]]
QUESTIONS = [q for q in Q["questions"] if q["component"]]          # без самооцінки
QBY = {q["id"]: q for q in Q["questions"]}
COMPONENTS = Q["components"]
GROUPS = {g["id"]: g for g in Q["groups"]}
OS_SUB = [g["id"] for g in Q["groups"] if g["parent"] == "OS"]


def level_num(x):
    """Досягнутий рівень — ціла частина (рівень вважається досягнутим, лише коли
    значення його досягло). Так само стримано радить оцінювати методологія НАЗК:
    за неповної відповідності обирати нижчий рівень."""
    if x is None:
        return None
    return min(5, max(1, int(x + 1e-9)))


def level_name(x):
    n = level_num(x)
    if n is None:
        return "—"
    return f"{n}. {LEVELS[n - 1]}"


def to_index(level):
    return None if level is None else round((level - 1) / 4 * 100, 1)


def _combine(values_weights):
    vals = [(v, w) for v, w in values_weights if v is not None and w > 0]
    if not vals:
        return None
    s = sum(w for _, w in vals)
    return round(sum(v * w for v, w in vals) / s, 4)


def compute(responses, checklist=None, settings=None):
    """responses: список (група, {питання: рівень}). Повертає повну картину циклу."""
    st = settings or SETTINGS
    w = st["weights"]
    th = st["thresholds"]
    k_min = th["min_group_size"]

    by_group = {g: [a for grp, a in responses if grp == g] for g in GROUPS}
    counts = {g: len(v) for g, v in by_group.items()}
    visible = {g: counts[g] >= k_min for g in GROUPS}

    res = {"counts": counts, "visible": visible, "questions": {}, "signals": []}

    for q in Q["questions"]:
        qid = q["id"]
        row = {"id": qid, "dist": {}, "median": {}}
        for g in GROUPS:
            answers = [a[qid] for a in by_group[g] if qid in a]
            if not answers or not visible[g]:
                row["dist"][g] = None
                row["median"][g] = None
                continue
            row["dist"][g] = [round(100 * answers.count(lv) / len(answers), 1) for lv in range(1, 6)]
            row["median"][g] = float(median(answers))
        # ОС: розподіл (зважений порівну між підкатегоріями) і середнє медіан
        subs = [g for g in OS_SUB if row["median"][g] is not None]
        if subs:
            ws = {g: w["os_subgroups"][g] for g in subs}
            tot = sum(ws.values())
            row["dist"]["OS"] = [round(sum(row["dist"][g][i] * ws[g] for g in subs) / tot, 1) for i in range(5)]
            row["median"]["OS"] = round(sum(row["median"][g] * ws[g] for g in subs) / tot, 2)
        else:
            row["dist"]["OS"] = None
            row["median"]["OS"] = None
        row["level"] = _combine([(row["median"]["RG"], w["groups"]["RG"]),
                                 (row["median"]["OS"], w["groups"]["OS"])])
        res["questions"][qid] = row

    # компоненти та індекс
    comps = {}
    for c in COMPONENTS:
        levels = [res["questions"][q["id"]]["level"] for q in QUESTIONS if q["component"] == c["id"]]
        levels = [x for x in levels if x is not None]
        comps[c["id"]] = round(sum(levels) / len(levels), 4) if levels else None
    res["components"] = comps
    overall = _combine([(comps[c["id"]], w["components"][c["id"]]) for c in COMPONENTS])
    res["level"] = overall
    res["index"] = to_index(overall)

    # окремі індекси груп (для перевірки самооцінки і порівняння)
    def group_level(src):
        cl = []
        for c in COMPONENTS:
            vals = [res["questions"][q["id"]]["median"][src] for q in QUESTIONS if q["component"] == c["id"]]
            vals = [v for v in vals if v is not None]
            cl.append((sum(vals) / len(vals) if vals else None, w["components"][c["id"]]))
        return _combine(cl)
    res["group_level"] = {"RG": group_level("RG"), "OS": group_level("OS")}

    # аналіз чутливості до ваг груп
    res["sensitivity"] = []
    for var in st["sensitivity_variants"]:
        alt = json.loads(json.dumps(st))
        alt["weights"]["groups"] = {"RG": var["RG"], "OS": var["OS"]}
        alt["sensitivity_variants"] = []
        r = _index_only(res, alt)
        res["sensitivity"].append({"title": var["title"], "level": r, "index": to_index(r), "level_name": level_name(r)})
    names = {s["level_name"] for s in res["sensitivity"] if s["level"] is not None}
    res["robust"] = len(names) == 1

    # ---------- сигнали ----------
    for q in QUESTIONS:
        row = res["questions"][q["id"]]
        rg, os_ = row["median"]["RG"], row["median"]["OS"]
        if rg is not None and os_ is not None and abs(rg - os_) >= th["gap_levels"]:
            higher = "робоча група" if rg > os_ else "особовий склад"
            res["signals"].append({
                "type": "gap", "question": q["id"],
                "title": f"Розрив між групами: {q['report_no']} {q['title']}",
                "text": f"Медіана РГ — {fmt(rg)}, ОС — {fmt(os_)}; вище оцінює {higher}. "
                        f"Правило: різниця медіан ≥ {fmt(th['gap_levels'])} рівня.",
                "rule": "R1"})
    for g, label in (("RG", "Робоча група"), ("OS", "Особовий склад")):
        selfm = res["questions"]["self_level"]["median"][g]
        calc = res["group_level"][g]
        if selfm is not None and calc is not None and selfm - level_num(calc) >= th["self_inflation_levels"]:
            res["signals"].append({
                "type": "self", "question": "self_level",
                "title": f"Завищена самооцінка: {label.lower()}",
                "text": f"{label} називає загальний рівень {fmt(selfm)} ({level_name(selfm)}), "
                        f"а з відповідей на розділи 1–3 виходить {fmt(calc)} ({level_name(calc)}). "
                        f"Правило: самооцінка вища за досягнутий рівень на {fmt(th['self_inflation_levels'], 0)} і більше.",
                "rule": "R2"})
    res["checklist"] = {}
    if checklist:
        for item in CHECKLIST["items"]:
            stt = checklist.get(item["no"])
            res["checklist"][item["no"]] = stt
            if stt != "todo":
                continue
            for qid in item["questions"]:
                lv = res["questions"][qid]["level"]
                if lv is not None and lv >= th["checklist_discrepancy_min_level"]:
                    q = QBY[qid]
                    res["signals"].append({
                        "type": "checklist", "question": qid, "item": item["no"],
                        "title": f"Розбіжність із чек-листом: {q['report_no']} {q['title']}",
                        "text": f"Респонденти оцінюють рівень як {fmt(lv)} ({level_name(lv)}), "
                                f"але пункт чек-листа {item['no']} «{item['title']}» має статус «Підлягає впровадженню». "
                                f"Правило: рівень ≥ {fmt(th['checklist_discrepancy_min_level'])} при статусі «Підлягає впровадженню».",
                        "rule": "R3"})
        done = sum(1 for v in checklist.values() if v == "full")
        part = sum(1 for v in checklist.values() if v == "partial")
        total = len(CHECKLIST["items"])
        res["implementation"] = round(100 * (done + 0.5 * part) / total, 1) if total else None
    else:
        res["implementation"] = None

    # сильні та слабкі сторони (за рівнем питання)
    ranked = sorted([q for q in QUESTIONS if res["questions"][q["id"]]["level"] is not None],
                    key=lambda q: res["questions"][q["id"]]["level"])
    res["weak"] = [q["id"] for q in ranked[:3]]
    res["strong"] = [q["id"] for q in ranked[::-1][:2]]
    return res


def _index_only(res, st):
    w = st["weights"]
    comps = []
    for c in COMPONENTS:
        levels = []
        for q in QUESTIONS:
            if q["component"] != c["id"]:
                continue
            m = res["questions"][q["id"]]["median"]
            lv = _combine([(m["RG"], w["groups"]["RG"]), (m["OS"], w["groups"]["OS"])])
            if lv is not None:
                levels.append(lv)
        comps.append((sum(levels) / len(levels) if levels else None, w["components"][c["id"]]))
    return _combine(comps)


def fmt(x, nd=1):
    if x is None:
        return "—"
    s = f"{x:.{nd}f}".replace(".", ",")
    return s


def describe(qid, res, checklist_items=None):
    """Текстовий опис під таблицею питання — за шаблонами (правила D1–D4)."""
    q = QBY[qid]
    row = res["questions"][qid]
    out = []
    names = q["short_options"]
    tops = {}
    for g in ("RG", "OS"):
        d = row["dist"].get(g)
        if d:
            i = max(range(5), key=lambda k: d[k])
            tops[g] = (i, d[i])
    if "RG" in tops and "OS" in tops:
        (i1, p1), (i2, p2) = tops["RG"], tops["OS"]
        if i1 == i2:
            out.append(("D1", f"Обидві групи найчастіше обирають варіант рівня {i1 + 1} «{names[i1]}» "
                              f"({fmt(p1, 0)} % і {fmt(p2, 0)} %)."))
        else:
            out.append(("D1", f"Робоча група найчастіше обирає варіант рівня {i1 + 1} «{names[i1]}» ({fmt(p1, 0)} %), "
                              f"особовий склад — рівня {i2 + 1} «{names[i2]}» ({fmt(p2, 0)} %)."))
    elif tops:
        g, (i, p) = next(iter(tops.items()))
        who = "Робоча група" if g == "RG" else "Особовий склад"
        out.append(("D1", f"{who} найчастіше обирає варіант рівня {i + 1} «{names[i]}» ({fmt(p, 0)} %). "
                          f"Питання є лише в одній анкеті, тому порівняння груп не здійснюється."))
    for s in res["signals"]:
        if s["question"] != qid:
            continue
        if s["type"] == "gap":
            out.append(("D2", s["text"].split(" Правило")[0] + " Можлива схильність до соціально бажаних відповідей — рекомендовано додатковий аналіз."))
        if s["type"] == "checklist":
            out.append(("D3", s["text"].split(" Правило")[0] + " Потрібна перевірка фактичного стану."))
    if row["level"] is not None and row["level"] < SETTINGS["thresholds"]["weak_question_level"]:
        out.append(("D4", f"Загальний рівень питання — {fmt(row['level'])} ({level_name(row['level'])}), "
                          f"нижче за «Визначений»: напрям потребує першочергової уваги."))
    hidden = [GROUPS[g]["title"] for g in GROUPS if not res["visible"][g] and res["counts"][g] > 0]
    if hidden:
        out.append(("D5", "Результати категорій «" + "», «".join(hidden) + "» не показано: "
                          f"відповідей менше за поріг ({SETTINGS['thresholds']['min_group_size']})."))
    return out


def propose(res):
    """Рекомендації для слабких питань і питань із сигналами (правила P1–P2)."""
    th = SETTINGS["thresholds"]
    reasons = {}
    for q in QUESTIONS:
        lv = res["questions"][q["id"]]["level"]
        if lv is not None and lv < th["weak_question_level"]:
            reasons.setdefault(q["id"], []).append(f"рівень {fmt(lv)} нижче «Визначеного» (правило P1)")
    for s in res["signals"]:
        if s["type"] in ("gap", "checklist"):
            reasons.setdefault(s["question"], []).append(
                {"gap": "розрив між групами", "checklist": "розбіжність із чек-листом"}[s["type"]] + " (правило P2)")
    out = []
    for qid, why in reasons.items():
        for m in MEASURES["measures"]:
            if qid in m["questions"]:
                out.append({"question": qid, "measure": m, "reason": "; ".join(why)})
                break
    out.sort(key=lambda r: res["questions"][r["question"]]["level"] or 9)
    return out
