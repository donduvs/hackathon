"""Робота з базою даних SQLite.

Головний принцип анонімності: таблиця відповідей (responses) не містить
ані коду доступу, ані часу, ані IP-адреси. Код зберігається окремо лише як
хеш і позначка «використано». Тому за даними неможливо встановити, хто
дав яку відповідь.
"""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

DB_PATH = Path(os.environ.get("IMD_DB", Path(__file__).resolve().parent.parent / "integrity.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS cycles (
    id INTEGER PRIMARY KEY,
    year INTEGER NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,          -- active | closed
    opened TEXT, closed TEXT
);
-- Одноразові коди доступу: лише хеш, категорія і позначка використання.
CREATE TABLE IF NOT EXISTS codes (
    code_hash TEXT PRIMARY KEY,
    cycle_id INTEGER NOT NULL,
    grp TEXT NOT NULL,
    used INTEGER NOT NULL DEFAULT 0
);
-- Коди відкритим текстом, які ще не надруковано (для аркуша з QR).
-- Після друку уповноважена особа видаляє їх кнопкою — лишаються тільки хеші.
-- У демо-режимі звідси ж беруться коди для демо-кнопок.
CREATE TABLE IF NOT EXISTS demo_codes (
    code TEXT PRIMARY KEY,
    cycle_id INTEGER NOT NULL,
    grp TEXT NOT NULL
);
-- Відповіді: випадковий ідентифікатор, без коду, часу та IP.
-- WITHOUT ROWID: записи впорядковані за випадковим ключем, а не за часом надходження.
CREATE TABLE IF NOT EXISTS responses (
    id TEXT PRIMARY KEY,
    cycle_id INTEGER NOT NULL,
    grp TEXT NOT NULL,
    answers TEXT NOT NULL
) WITHOUT ROWID;
-- Інституційне оцінювання (чек-лист): хто вніс і хто підтвердив.
CREATE TABLE IF NOT EXISTS checklist_status (
    cycle_id INTEGER NOT NULL,
    item_no TEXT NOT NULL,
    status TEXT NOT NULL,          -- full | partial | todo
    entered_by TEXT, confirmed_by TEXT, note TEXT,
    PRIMARY KEY (cycle_id, item_no)
);
CREATE TABLE IF NOT EXISTS recommendations (
    id INTEGER PRIMARY KEY,
    cycle_id INTEGER NOT NULL,
    question_id TEXT NOT NULL,
    measure_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL,          -- proposed | accepted | rejected | in_progress | done
    responsible TEXT, due TEXT,
    decided_by TEXT, decision_note TEXT, result_note TEXT
);
-- Журнал дій посадових осіб (дії респондентів не журналюються).
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    role TEXT NOT NULL,
    action TEXT NOT NULL,
    details TEXT
);
"""


def connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


@contextmanager
def tx():
    con = connect()
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init(drop=False):
    if drop and DB_PATH.exists():
        DB_PATH.unlink()
    with tx() as con:
        con.executescript(SCHEMA)


def log(con, role, action, details=""):
    con.execute("INSERT INTO audit (ts, role, action, details) VALUES (?,?,?,?)",
                (datetime.now().strftime("%Y-%m-%d %H:%M"), role, action, details))


def responses(con, cycle_id):
    rows = con.execute("SELECT grp, answers FROM responses WHERE cycle_id=?", (cycle_id,)).fetchall()
    return [(r["grp"], json.loads(r["answers"])) for r in rows]
