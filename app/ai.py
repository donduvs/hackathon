"""Чернетка аналітичної записки до звіту.

ШІ тут лише формулює текст за вже порахованими числами. Розрахунки, рівні,
сигнали й рекомендації формуються за явними правилами (app/engine.py),
а записку затверджує людина. Без ключа або в разі збою використовується
шаблонний текст, тож система працює повністю і без ШІ.

Налаштування (змінні середовища):
  AI_PROVIDER  groq | anthropic | openai   (openai — будь-який сумісний сервіс,
               зокрема локальна модель Ollama у закритому контурі)
  AI_API_KEY   ключ доступу (не зберігається в репозиторії)
  AI_MODEL     назва моделі, напр. openai/gpt-oss-120b (відкрита модель OpenAI на Groq)
  AI_BASE_URL  адреса сумісного сервісу для AI_PROVIDER=openai,
               напр. http://localhost:11434/v1
Китайські моделі (Qwen, DeepSeek тощо) не використовуються відповідно до Політики ШІ хакатону.
"""
import json
import os
import re
import urllib.error
import urllib.request

from . import engine as E

BANNED = ("qwen", "deepseek", "kimi", "moonshot", "glm", "yi-", "baichuan", "ernie", "doubao")

SYSTEM = (
    "Ти аналітик з питань доброчесності у вищому військовому навчальному закладі. "
    "Пишеш українською мовою аналітичну записку до звіту про оцінювання рівня зрілості системи доброчесності. "
    "Використовуй ЛИШЕ числа й факти з наданих даних, нічого не вигадуй і не додавай нових чисел. "
    "Не оцінюй і не згадуй окремих посадових осіб чи підрозділів: оцінювання — інструмент розвитку, а не покарання. "
    "Не роби висновків про корупційні правопорушення: сигнали — це лише підстави для додаткового аналізу. "
    "Структура: 1) загальний висновок про рівень зрілості та динаміку; 2) сильні сторони; "
    "3) проблемні напрями; 4) розбіжності між групами, із чек-листом і з об'єктивними показниками; "
    "5) пріоритети на наступний рік (лише із запропонованих заходів). "
    "Integrity Index — це бал зі 100, а не відсоток: пиши «42,7 з 100». Рівні питань і компонентів — за шкалою від 1 до 5. "
    "Не вигадуй порогів і порівнянь, яких немає в даних; пороги є лише в описі сигналів. "
    "Суворі правила: (а) не стверджуй, що групи оцінили однаково, якщо в даних немає обох оцінок; "
    "(б) не роби висновків про швидкість, причини чи тенденції, яких немає в даних; "
    "(в) завищену самооцінку тлумач лише як схильність до соціально бажаних відповідей, а не як ваду методики; "
    "(г) кожен захід згадуй тільки разом із тим питанням, на яке він спрямований за даними; "
    "(ґ) не обіцяй результатів заходів (жодних «забезпечить», «гарантує»); "
    "(д) обов'язково згадай кожну розбіжність з об'єктивними показниками з її числами. "
    "Обсяг 250–400 слів, офіційно-діловий стиль, без маркованих списків і без заголовків, 4–6 абзаців."
)


def config():
    prov = (os.environ.get("AI_PROVIDER") or "").strip().lower()
    key = (os.environ.get("AI_API_KEY") or "").strip()
    model = (os.environ.get("AI_MODEL") or "").strip()
    base = (os.environ.get("AI_BASE_URL") or "").strip()
    if not model:
        model = {"groq": "openai/gpt-oss-120b", "anthropic": "claude-haiku-5-5"}.get(prov, "")
    enabled = bool(prov and model and (key or prov == "openai"))
    if any(b in model.lower() for b in BANNED):
        enabled = False
    return {"provider": prov, "key": key, "model": model, "base": base, "enabled": enabled}


def facts(cycle, res, prev=None, proposals=()):
    """Знеособлені агреговані дані, які передаються моделі. Жодних персональних даних."""
    q = {}
    for qq in E.QUESTIONS:
        r = res["questions"][qq["id"]]
        row = {"рівень": E.fmt(r["level"]), "РГ": E.fmt(r["median"]["RG"]), "ОС": E.fmt(r["median"]["OS"])}
        if r["median"]["OS"] is None:
            row["примітка"] = "питання є лише в Анкеті 1: його оцінює тільки робоча група, порівнювати з ОС не можна"
        q[f"{qq['report_no']} {qq['title']}"] = row
    return {
        "заклад": E.SETTINGS["institution"], "рік": cycle["year"],
        "Integrity Index": E.fmt(res["index"]), "рівень зрілості": E.level_name(res["level"]),
        "попередній рік": ({"Integrity Index": E.fmt(prev["index"]), "рівень": E.level_name(prev["level"])} if prev else None),
        "компоненти": {c["title"]: E.fmt(res["components"][c["id"]]) for c in E.COMPONENTS},
        "стійкість до ваг": "висновок стійкий" if res["robust"] else "рівень залежить від ваг груп",
        "питання": q,
        "сильні сторони": [E.QBY[x]["title"] for x in res["strong"]],
        "проблемні напрями": [E.QBY[x]["title"] for x in res["weak"]],
        "сигнали: розрив між групами": [s["title"] + ": " + s["text"].split(" Правило")[0] for s in res["signals"] if s["type"] == "gap"],
        "сигнали: завищена самооцінка (тлумачити як схильність до соціально бажаних відповідей)":
            [s["text"].split(" Правило")[0] for s in res["signals"] if s["type"] == "self"],
        "сигнали: розбіжність із чек-листом": [s["title"] + ": " + s["text"].split(" Правило")[0] for s in res["signals"] if s["type"] == "checklist"],
        "сигнали: розбіжність з об'єктивними показниками (ОБОВ'ЯЗКОВО згадати з числами)":
            [s["text"].split(" Правило")[0] for s in res["signals"] if s["type"] == "objective"],
        "впровадження інструментів за чек-листом, %": E.fmt(res["implementation"], 0),
        "запропоновані заходи (код — назва — на яке питання спрямований; не змінюй цієї відповідності)": [
            f"{p['measure']['id']} — {p['measure']['title']} — питання {E.QBY[p['question']]['report_no']} {E.QBY[p['question']]['title']}"
            for p in proposals][:6],
    }


def template(cycle, res, prev=None, proposals=()):
    """Запасний варіант: текст зі звичайних правил, без ШІ."""
    p = []
    dyn = ""
    if prev:
        d = res["index"] - prev["index"]
        dyn = f" Порівняно з попереднім циклом показник {'зріс' if d >= 0 else 'знизився'} на {E.fmt(abs(d))}."
    p.append(f"За результатами оцінювання {cycle['year']} року Integrity Index закладу становить {E.fmt(res['index'])} "
             f"з 100, що відповідає рівню зрілості «{E.level_name(res['level'])}».{dyn} "
             + ("Висновок стійкий до зміни ваг груп респондентів." if res["robust"] else
                "Рівень залежить від ваг груп респондентів, тому висновок потребує обережного тлумачення."))
    comps = sorted(E.COMPONENTS, key=lambda c: res["components"][c["id"]] or 0)
    p.append(f"Найвищий рівень має компонент «{comps[-1]['title']}» ({E.fmt(res['components'][comps[-1]['id']])}), "
             f"найнижчий — «{comps[0]['title']}» ({E.fmt(res['components'][comps[0]['id']])}). "
             f"Сильні сторони: {', '.join(E.QBY[x]['title'].lower() for x in res['strong'])}. "
             f"Проблемні напрями: {', '.join(E.QBY[x]['title'].lower() for x in res['weak'])}.")
    if res["signals"]:
        kinds = {"gap": "розрив між оцінками робочої групи й особового складу", "self": "завищена самооцінка",
                 "checklist": "розбіжність відповідей із чек-листом", "objective": "розбіжність з об'єктивними показниками"}
        found = sorted({kinds[s["type"]] for s in res["signals"]})
        p.append(f"Виявлено {len(res['signals'])} сигналів для додаткового аналізу: {'; '.join(found)}. "
                 "Сигнали не є висновком про порушення, а вказують, де оцінки потребують перевірки.")
    if proposals:
        p.append("Пріоритетні заходи на наступний рік: " +
                 "; ".join(f"{x['measure']['title'].rstrip('.')} ({x['measure']['id']})" for x in proposals[:4]) + ".")
    return "\n\n".join(p)


def _post(url, headers, body, timeout=40):
    # Власний User-Agent: деякі сервіси (зокрема Groq за Cloudflare) відхиляють стандартний «Python-urllib».
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Accept": "application/json",
                                          "User-Agent": "integrity-dashboard/0.5", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def generate(cycle, res, prev=None, proposals=()):
    """Повертає (текст, джерело, модель, попередження)."""
    cfg = config()
    data = facts(cycle, res, prev, proposals)
    if not cfg["enabled"]:
        return template(cycle, res, prev, proposals), "template", None, "ШІ не налаштовано: використано шаблонний текст."
    user = "Дані оцінювання (JSON):\n" + json.dumps(data, ensure_ascii=False, indent=1)
    try:
        if cfg["provider"] == "anthropic":
            out = _post("https://api.anthropic.com/v1/messages",
                        {"x-api-key": cfg["key"], "anthropic-version": "2023-06-01"},
                        {"model": cfg["model"], "max_tokens": 1500, "system": SYSTEM,
                         "messages": [{"role": "user", "content": user}]})
            text = "".join(b.get("text", "") for b in out.get("content", []))
        else:
            base = {"groq": "https://api.groq.com/openai/v1"}.get(cfg["provider"], cfg["base"] or "http://localhost:11434/v1")
            hdr = {"Authorization": f"Bearer {cfg['key']}"} if cfg["key"] else {}
            req = {"model": cfg["model"], "temperature": 0.1, "max_tokens": 4000,
                   "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]}
            if "gpt-oss" in cfg["model"]:
                req["reasoning_effort"] = "low"   # модель міркує стисло, більше токенів лишається на текст
            out = _post(base.rstrip("/") + "/chat/completions", hdr, req, timeout=60)
            text = out["choices"][0]["message"]["content"]
        text = text.strip()
        if not text:
            raise ValueError("порожня відповідь")
    except Exception as e:  # будь-який збій — запасний шаблон
        detail = type(e).__name__
        if isinstance(e, urllib.error.HTTPError):
            try:
                body = e.read().decode(errors="replace")[:160]
            except Exception:
                body = ""
            detail = f"HTTP {e.code}" + (f": {body}" if body else "")
        return (template(cycle, res, prev, proposals), "template", None,
                f"Сервіс ШІ недоступний ({detail}): використано шаблонний текст.")
    return text, "ai", f"{cfg['provider']}: {cfg['model']}", check_numbers(text, data)


RISKY = ("забезпечить", "гарантує", "гарантуватиме", "неодмінно", "швидше", "методик", "порушник", "корупціонер")


def check_numbers(text, data):
    """Запобіжники для людини, яка затверджує записку. Нічого не виправляють самі, лише вказують, що перевірити."""
    warn = []

    def nums(t):
        out = set()
        for n in re.findall(r"\d+(?:[.,]\d+)?", t):
            try:
                out.add(round(float(n.replace(",", ".")), 1))
            except ValueError:
                pass
        return out
    # 1) числа, яких немає у вхідних даних («46» і «46,0» вважаються однаковими)
    allowed = nums(json.dumps(data, ensure_ascii=False)) | {1.0, 2.0, 3.0, 4.0, 5.0, 100.0}
    odd = sorted(n for n in nums(text) if n not in allowed and n not in {float(data["рік"]), float(data["рік"]) - 1})
    if odd:
        warn.append("числа, яких немає у вхідних даних: " + ", ".join(E.fmt(n) for n in odd))
    # 2) коди заходів: лише запропоновані
    key = next(k for k in data if k.startswith("запропоновані заходи"))
    codes_ok = {x.split(" — ")[0] for x in data[key]}
    bad = sorted(set(re.findall(r"D\d\.\d", text)) - codes_ok)
    if bad:
        warn.append("заходи, яких система не пропонувала: " + ", ".join(bad))
    # 3) обов'язкові розбіжності з об'єктивними показниками
    okey = next(k for k in data if k.startswith("сигнали: розбіжність з об'єктивними"))
    for sig in data[okey]:
        m = re.search(r"становить (\d+(?:,\d+)?)", sig)
        if m and round(float(m.group(1).replace(",", ".")), 1) not in nums(text):
            warn.append("не згадано розбіжність з об'єктивним показником: " + sig.split(", але")[1].strip()[:90])
    # 4) ризиковані формулювання
    low = text.lower()
    risky = [w for w in RISKY if w in low]
    if risky:
        warn.append("перевірте формулювання зі словами: " + ", ".join(risky))
    return ("Перевірте перед затвердженням: " + "; ".join(warn) + ".") if warn else ""
