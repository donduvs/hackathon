"""Генератор синтетичних (вигаданих) даних для демонстрації.

Модельний об'єкт — ВВНЗ «Умовний». Жодних реальних осіб чи підрозділів.
Три цикли оцінювання: 2024 і 2025 — закриті, 2026 — активний.

Закладені знахідки:
  * 2024: робоча група помітно оцінює відкритість вище за особовий склад (розрив);
          завищена самооцінка в розділі 4;
          респонденти вважають навчання регулярним, а пункт чек-листа 2.6
          має статус «Підлягає впровадженню» (розбіжність).
  * Рекомендації 2024: навчання (D2.6) і ризики (D2.4) виконано — у 2025–2026
    рівні цих питань зростають; відкритість (D2.8) прийнято, але не виконано —
    проблема лишається.
  * 2026: адміністративного персоналу відповіло лише 4 особи — результати
    категорії приховуються порогом анонімності.

Запуск:  python scripts/generate_demo_data.py
"""
import hashlib
import json
import math
import random
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import db  # noqa: E402

rnd = random.Random(2026)

# «Справжній» рівень особового складу за питаннями і бонус робочої групи
TRUE = {
    2024: {"leader": 3.4, "culture": 2.5, "education": 3.0, "openness": 1.8, "officer": 2.6,
           "strategy": 2.3, "risks": 1.8, "screening": 2.2, "whistle": 2.4, "audit": 1.6},
    2025: {"leader": 3.5, "culture": 2.7, "education": 3.3, "openness": 1.9, "officer": 2.8,
           "strategy": 2.5, "risks": 2.4, "screening": 2.3, "whistle": 2.6, "audit": 1.7},
    2026: {"leader": 3.6, "culture": 2.9, "education": 3.7, "openness": 2.0, "officer": 3.0,
           "strategy": 2.8, "risks": 3.1, "screening": 2.5, "whistle": 2.8, "audit": 1.9},
}
RG_BONUS = {2024: 0.35, 2025: 0.3, 2026: 0.2}
RG_EXTRA = {"openness": 1.6}          # розрив: РГ бачить відкритість значно краще
SUB_SHIFT = {"NPP": 0.1, "STUD": -0.1, "ADM": 0.0}
SELF = {2024: {"RG": 4.0, "OS": 3.3}, 2025: {"RG": 3.8, "OS": 3.0}, 2026: {"RG": 3.4, "OS": 2.9}}
SIZES = {
    2024: {"RG": (12, 12), "NPP": (40, 40), "STUD": (90, 88), "ADM": (20, 19)},
    2025: {"RG": (12, 12), "NPP": (40, 38), "STUD": (90, 84), "ADM": (20, 18)},
    2026: {"RG": (12, 11), "NPP": (40, 36), "STUD": (90, 78), "ADM": (20, 4)},   # (видано кодів, відповіло)
}
CHECK = {
    2024: {"1.1": "partial", "1.2": "full", "1.3": "full", "2.1": "full", "2.3": "full", "2.4": "todo",
           "2.5": "partial", "2.6": "todo", "2.7": "partial", "2.8": "todo", "4.1": "partial", "4.5": "todo"},
    2025: {"1.1": "full", "1.2": "full", "1.3": "full", "2.1": "full", "2.3": "full", "2.4": "partial",
           "2.5": "partial", "2.6": "partial", "2.7": "partial", "2.8": "todo", "4.1": "partial", "4.5": "partial"},
    2026: {"1.1": "full", "1.2": "full", "1.3": "full", "2.1": "full", "2.3": "full", "2.4": "full",
           "2.5": "full", "2.6": "full", "2.7": "partial", "2.8": "partial", "4.1": "full", "4.5": "partial"},
}


def sample(mu, spread=0.8):
    w = [math.exp(-((k - mu) ** 2) / (2 * spread ** 2)) for k in range(1, 6)]
    return rnd.choices(range(1, 6), weights=w)[0]


def answers_for(year, grp):
    a = {}
    for qid, mu in TRUE[year].items():
        if qid == "leader" and grp != "RG":
            continue                                    # питання лише в Анкеті 1
        if grp == "RG":
            mu = mu + RG_BONUS[year] + (RG_EXTRA.get(qid, 0) if year < 2026 or qid == "openness" else 0)
        else:
            mu = mu + SUB_SHIFT[grp]
        a[qid] = sample(min(5.2, mu))
    a["self_level"] = sample(SELF[year]["RG" if grp == "RG" else "OS"], 0.6)
    return a


def code():
    return "-".join("".join(rnd.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(4)) for _ in range(2))


def main():
    rnd.seed(2026)  # однакові демо-дані після кожного відновлення
    db.init(drop=True)
    with db.tx() as con:
        for cid, year in enumerate((2024, 2025, 2026), start=1):
            status = "active" if year == 2026 else "closed"
            con.execute("INSERT INTO cycles (id, year, title, status, opened, closed) VALUES (?,?,?,?,?,?)",
                        (cid, year, f"Оцінювання {year} року", status, f"{year}-05-12",
                         None if status == "active" else f"{year}-06-02"))
            for grp, (issued, answered) in SIZES[year].items():
                codes = [code() for _ in range(issued)]
                for i, c in enumerate(codes):
                    used = i < answered
                    con.execute("INSERT INTO codes (code_hash, cycle_id, grp, used) VALUES (?,?,?,?)",
                                (hashlib.sha256(c.encode()).hexdigest(), cid, grp, int(used)))
                    if not used and status == "active":
                        con.execute("INSERT INTO demo_codes (code, cycle_id, grp) VALUES (?,?,?)", (c, cid, grp))
                for _ in range(answered):
                    con.execute("INSERT INTO responses (id, cycle_id, grp, answers) VALUES (?,?,?,?)",
                                (uuid.UUID(int=rnd.getrandbits(128)).hex, cid, grp, json.dumps(answers_for(year, grp))))
            for no, stt in CHECK[year].items():
                con.execute("INSERT INTO checklist_status VALUES (?,?,?,?,?,?)",
                            (cid, no, stt, "Уповноважена особа", "Голова робочої групи", ""))
        # історія рекомендацій
        recs = [
            (1, "education", "D2.6", "розбіжність із чек-листом (правило P2)", "done", "Антикорупційний уповноважений", "2025-03-01",
             "Затверджено графік навчань, проведено 6 занять для всіх категорій."),
            (1, "risks", "D2.4", "рівень нижче «Визначеного» (правило P1)", "done", "Робоча група", "2025-09-01",
             "Оновлено реєстр ризиків, затверджено заходи мінімізації."),
            (1, "openness", "D2.8", "розрив між групами (правило P2)", "accepted", "Антикорупційний уповноважений", "2025-02-01", ""),
            (1, "audit", "D4.4", "рівень нижче «Визначеного» (правило P1)", "rejected", "", "",
             "Відкладено до реорганізації фінансової служби."),
            (2, "openness", "D2.8", "розрив між групами (правило P2)", "in_progress", "Антикорупційний уповноважений", "2026-04-01",
             "Структуру розділу на сайті погоджено, наповнення не завершено."),
            (2, "screening", "D4.1", "рівень нижче «Визначеного» (правило P1)", "done", "Відділ кадрів", "2026-03-01",
             "Затверджено порядок обліку близьких осіб."),
        ]
        for cid, qid, mid, reason, status, resp, due, note in recs:
            con.execute("""INSERT INTO recommendations (cycle_id, question_id, measure_id, reason, status, responsible, due,
                           decided_by, decision_note, result_note) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (cid, qid, mid, reason, status, resp, due, "Керівник", "", note))
        db.log(con, "система", "Завантажено демонстраційні дані", "ВВНЗ «Умовний», цикли 2024–2026")
    print("Демо-дані створено:", db.DB_PATH)


if __name__ == "__main__":
    main()
