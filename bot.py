import os
import json
import logging
import threading
import re
import requests
from datetime import date, timedelta, time, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# ─── Конфигурация ───────────────────────────────────────────────────────────
TELEGRAM_TOKEN            = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_API_KEY         = os.environ["ANTHROPIC_API_KEY"]
NOTION_TOKEN              = os.environ["NOTION_TOKEN"]
NOTION_DATABASE_ID        = os.environ["NOTION_DATABASE_ID"]
NOTION_PERSONAL_DB_ID     = os.environ["NOTION_PERSONAL_DATABASE_ID"]
ALLOWED_CHAT_ID           = int(os.environ["ALLOWED_CHAT_ID"])
ANDREY_TG_ID              = 5106438154
MARGO_TG_ID               = 263775863
PORT                      = int(os.environ.get("PORT", 8080))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── Константы ──────────────────────────────────────────────────────────────
VALID_NAMES      = ["Марго", "Галия", "Ольга", "Андрей"]
VALID_DIRS       = ["АЭлит", "Контент", "Фокус-группа", "Общее"]
VALID_STATUSES   = ["В работе", "Ждём", "Идея", "Готово", "Отменено"]
VALID_SECTIONS   = ["Цели и приоритеты", "Семья и быт", "Обучение"]

DIR_ICONS    = {"АЭлит": "🏭", "Контент": "📱", "Фокус-группа": "🎓", "Общее": "📋"}
PERSON_ICONS = {"Марго": "👩‍💼", "Галия": "👩‍💻", "Ольга": "👩‍📋", "Андрей": "👨‍🔧"}
SECTION_ICONS= {"Цели и приоритеты": "🎯", "Семья и быт": "🧡", "Обучение": "📚"}
STATUS_ICONS = {"В работе": "🔵", "Ждём": "🟡", "Идея": "⚪", "Готово": "✅", "Отменено": "❌"}

# ─── Системный промпт ───────────────────────────────────────────────────────
SYSTEM_PROMPT = """Ты ассистент Марго, управляешь задачами в Notion.

КОМАНДА: Марго, Галия, Ольга, Андрей
НАПРАВЛЕНИЯ: АЭлит, Контент, Фокус-группа, Общее
ПРИОРИТЕТЫ: 🔥 Срочно, ⚡ Важно, 📌 Обычное
СТАТУСЫ: В работе, Ждём, Идея, Готово, Отменено
ЛИЧНЫЕ РАЗДЕЛЫ: Цели и приоритеты, Семья и быт, Обучение

Верни ТОЛЬКО валидный JSON без markdown. Типы:

show_all — все рабочие задачи ("покажи задачи", "что в работе")
{"type": "show_all"}

show_person — задачи человека ("задачи Ольги", "что у Марго")
{"type": "show_person", "person": "Ольга"}

show_my_work — мои рабочие задачи ("мои задачи", "что у меня" — подставь имя отправителя)
{"type": "show_my_work", "person": "имя отправителя"}

show_direction — задачи по направлению ("задачи АЭлит", "что по Контенту")
{"type": "show_direction", "direction": "АЭлит"}

show_person_direction — задачи человека по направлению ("задачи Марго АЭлит")
{"type": "show_person_direction", "person": "Марго", "direction": "АЭлит"}

show_priority — задачи по приоритету ("срочные", "важные Галии", "срочное по АЭлит")
{"type": "show_priority", "priority": "🔥 Срочно", "person": null, "direction": null}

show_ideas — задачи со статусом Идея ("идеи", "идеи Марго", "идеи по АЭлит")
{"type": "show_ideas", "person": null, "direction": null}

show_deadlines — дедлайны: просроченные + сегодня + завтра ("дедлайны", "дедлайны Андрея")
{"type": "show_deadlines", "person": null}

show_overdue — только просроченные ("просрочено", "просроченные Галии")
{"type": "show_overdue", "person": null}

show_personal_all — личные задачи Марго ("мои личные задачи", "личные")
{"type": "show_personal_all"}

show_personal_section — личные по разделу ("мои цели", "семья", "обучение")
{"type": "show_personal_section", "section": "Цели и приоритеты"}

done — выполнить одну задачу ("выполнила: КП", "сделала: позвонила")
{"type": "done", "task_name": "название", "responsible": "Марго", "status": "Готово"}

done_many — выполнить несколько ("закрыть: задача1, задача2")
{"type": "done_many", "tasks": ["задача1", "задача2"], "responsible": "Марго"}

new — новая рабочая задача. Дедлайн: YYYY-MM-DD, "today", "tomorrow" или null.
("добавь задачу: X — Галия — АЭлит — срочно", "задача: X, Ольга, дедлайн 8.07")
{"type": "new", "task_name": "название", "responsible": "Марго", "direction": "Общее", "priority": "📌 Обычное", "deadline": null, "status": "В работе"}

new_many — несколько новых задач ("добавить задачи: 1. X — Ольга 2. Y — Марго")
{"type": "new_many", "tasks": [{"task_name": "X", "responsible": "Ольга", "direction": "Общее", "priority": "📌 Обычное", "deadline": null, "status": "В работе"}]}

new_personal — личная задача ("моя задача: купить", "добавь в цели: X", "личное: Y")
{"type": "new_personal", "task_name": "название", "section": "Семья и быт", "priority": "📌 Обычное"}

change_direction — изменить направление ("перенеси X в Контент", "измени направление X на АЭлит")
{"type": "change_direction", "task_name": "название", "new_direction": "Контент"}

change_responsible — изменить ответственного ("передай X Андрею", "назначь Галию на X")
{"type": "change_responsible", "task_name": "название", "new_responsible": "Андрей"}

skip — не про задачи
{"type": "skip"}

ВАЖНО: сообщения со словами "задача", "добавь", "создай", "нужно сделать" — почти всегда new или new_many."""


# ─── Keep-alive веб-сервер ──────────────────────────────────────────────────
class KeepAlive(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *args):
        pass

def start_web():
    HTTPServer(("0.0.0.0", PORT), KeepAlive).serve_forever()


# ─── Claude API ─────────────────────────────────────────────────────────────
def ask_claude(text: str, sender: str) -> dict:
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": "claude-sonnet-4-6", "max_tokens": 1000, "system": SYSTEM_PROMPT,
              "messages": [{"role": "user", "content": f"Отправитель: {sender}\nСообщение: {text}"}]},
        timeout=30
    )
    r.raise_for_status()
    raw = r.json()["content"][0]["text"].strip()
    # Убираем markdown если есть
    if raw.startswith("```"):
        lines = [l for l in raw.split("\n") if not l.startswith("```")]
        raw = "\n".join(lines).strip()
    logger.info(f"Claude: {raw[:200]}")
    if not raw:
        return {"type": "skip"}
    return json.loads(raw)


# ─── Notion API ─────────────────────────────────────────────────────────────
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json"
}

def notion_query(db_id: str, body: dict) -> list:
    r = requests.post(f"https://api.notion.com/v1/databases/{db_id}/query",
                      headers=NOTION_HEADERS, json=body, timeout=15)
    r.raise_for_status()
    return r.json().get("results", [])

def notion_create(db_id: str, props: dict):
    requests.post("https://api.notion.com/v1/pages",
                  headers=NOTION_HEADERS,
                  json={"parent": {"database_id": db_id}, "properties": props},
                  timeout=15)

def notion_update(page_id: str, props: dict) -> bool:
    r = requests.patch(f"https://api.notion.com/v1/pages/{page_id}",
                       headers=NOTION_HEADERS, json={"properties": props}, timeout=15)
    return r.status_code == 200


# ─── Хелперы для задач ──────────────────────────────────────────────────────
def parse_deadline(raw) -> str | None:
    """Конвертируем дедлайн в YYYY-MM-DD или None."""
    if not raw or raw in ("null", ""):
        return None
    if raw == "today":
        return date.today().isoformat()
    if raw == "tomorrow":
        return (date.today() + timedelta(days=1)).isoformat()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", str(raw)):
        return str(raw)
    return None

def fix_direction(responsible: str, direction: str) -> str:
    """Андрей всегда в АЭлит."""
    if responsible == "Андрей":
        return "АЭлит"
    return direction

def parse_work_task(task: dict) -> dict:
    props = task["properties"]
    name = (props.get("Задача", {}).get("title") or [{}])
    name = name[0]["text"]["content"] if name else ""
    responsible = (props.get("Ответственный", {}).get("select") or {}).get("name", "—")
    direction   = (props.get("Направление", {}).get("select") or {}).get("name", "Общее")
    priority    = (props.get("Приоритет", {}).get("select") or {}).get("name", "")
    deadline    = (props.get("Дедлайн", {}).get("date") or {}).get("start", "")
    p_icon = "🔥" if "Срочно" in priority else "⚡" if "Важно" in priority else "📌"
    return {"name": name, "responsible": responsible, "direction": direction,
            "p_icon": p_icon, "deadline": deadline}

def parse_personal_task(task: dict) -> dict:
    props = task["properties"]
    name     = (props.get("Задача", {}).get("title") or [{}])
    name     = name[0]["text"]["content"] if name else ""
    section  = (props.get("Раздел", {}).get("select") or {}).get("name", "Общее")
    priority = (props.get("Приоритет", {}).get("select") or {}).get("name", "")
    deadline = (props.get("Дедлайн", {}).get("date") or {}).get("start", "")
    p_icon = "🔥" if "Срочно" in priority else "⚡" if "Важно" in priority else "📌"
    return {"name": name, "section": section, "p_icon": p_icon, "deadline": deadline}


# ─── Запросы к Notion ───────────────────────────────────────────────────────
def get_work_tasks(person=None, direction=None, status="В работе", priority=None) -> list:
    filters = [{"property": "Статус", "select": {"equals": status}}]
    if person:
        filters.append({"property": "Ответственный", "select": {"equals": person}})
    if direction:
        filters.append({"property": "Направление", "select": {"equals": direction}})
    if priority:
        filters.append({"property": "Приоритет", "select": {"equals": priority}})
    f = filters[0] if len(filters) == 1 else {"and": filters}
    results = notion_query(NOTION_DATABASE_ID, {
        "filter": f,
        "sorts": [{"property": "Направление", "direction": "ascending"},
                  {"property": "Приоритет", "direction": "ascending"}]
    })
    tasks = [parse_work_task(t) for t in results]
    tasks = [t for t in tasks if t["name"]]
    logger.info(f"get_work_tasks(person={person}, dir={direction}, status={status}): {len(tasks)} задач")
    return tasks

def get_overdue_tasks(person=None) -> list:
    today = date.today().isoformat()
    filters = [
        {"property": "Дедлайн", "date": {"before": today}},
        {"property": "Статус", "select": {"equals": "В работе"}},
    ]
    if person:
        filters.append({"property": "Ответственный", "select": {"equals": person}})
    results = notion_query(NOTION_DATABASE_ID, {
        "filter": {"and": filters},
        "sorts": [{"property": "Дедлайн", "direction": "ascending"}]
    })
    return [t for t in (parse_work_task(r) for r in results) if t["name"]]

def get_deadline_tasks(person=None):
    today    = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    def make_filter(date_f):
        if person:
            return {"and": [date_f, {"property": "Ответственный", "select": {"equals": person}}]}
        return date_f

    overdue       = get_overdue_tasks(person)
    today_results = notion_query(NOTION_DATABASE_ID, {
        "filter": make_filter({"property": "Дедлайн", "date": {"equals": today}}),
        "sorts": [{"property": "Приоритет", "direction": "ascending"}]
    })
    tmrw_results  = notion_query(NOTION_DATABASE_ID, {
        "filter": make_filter({"property": "Дедлайн", "date": {"equals": tomorrow}}),
        "sorts": [{"property": "Приоритет", "direction": "ascending"}]
    })
    today_tasks    = [t for t in (parse_work_task(r) for r in today_results) if t["name"]]
    tomorrow_tasks = [t for t in (parse_work_task(r) for r in tmrw_results) if t["name"]]
    return overdue, today_tasks, tomorrow_tasks

def get_personal_tasks(section=None) -> list:
    filters = [{"property": "Статус", "select": {"equals": "В работе"}}]
    if section:
        filters.append({"property": "Раздел", "select": {"equals": section}})
    f = filters[0] if len(filters) == 1 else {"and": filters}
    results = notion_query(NOTION_PERSONAL_DB_ID, {
        "filter": f,
        "sorts": [{"property": "Раздел", "direction": "ascending"},
                  {"property": "Приоритет", "direction": "ascending"}]
    })
    return [t for t in (parse_personal_task(r) for r in results) if t["name"]]

def find_task(name: str) -> str | None:
    results = notion_query(NOTION_DATABASE_ID, {
        "filter": {"property": "Задача", "title": {"contains": name[:20]}}
    })
    return results[0]["id"] if results else None

def create_work_task(name, responsible, direction="Общее", priority="📌 Обычное",
                     status="В работе", deadline=None):
    direction = fix_direction(responsible, direction)
    props = {
        "Задача":         {"title": [{"text": {"content": name}}]},
        "Статус":         {"select": {"name": status}},
        "Ответственный":  {"select": {"name": responsible}},
        "Направление":    {"select": {"name": direction}},
        "Приоритет":      {"select": {"name": priority}},
    }
    if deadline:
        props["Дедлайн"] = {"date": {"start": deadline}}
    notion_create(NOTION_DATABASE_ID, props)

def create_personal_task(name, section="Цели и приоритеты", priority="📌 Обычное"):
    notion_create(NOTION_PERSONAL_DB_ID, {
        "Задача":        {"title": [{"text": {"content": name}}]},
        "Статус":        {"select": {"name": "В работе"}},
        "Ответственный": {"select": {"name": "Марго"}},
        "Раздел":        {"select": {"name": section}},
        "Приоритет":     {"select": {"name": priority}},
    })


# ─── Форматирование ─────────────────────────────────────────────────────────
def task_line(t: dict, show_person=True, show_dir=True) -> str:
    dl = f" · 📅 {t['deadline']}" if t.get("deadline") else ""
    person = f"\n   👤 {t['responsible']}" if show_person else ""
    d_icon = DIR_ICONS.get(t.get("direction", ""), "📁")
    direction = f" · {d_icon} {t['direction']}" if show_dir and t.get("direction") else ""
    return f"{t['p_icon']} {t['name']}{person}{direction}{dl}"

def fmt_work_tasks(tasks: list, title: str) -> str:
    if not tasks:
        return f"✅ {title} — нет задач!"
    by_dir = {}
    for t in tasks:
        by_dir.setdefault(t["direction"], []).append(t)
    lines = [f"📋 *{title}*\n"]
    for d, items in by_dir.items():
        lines.append(f"{DIR_ICONS.get(d,'📁')} *{d}*")
        for t in items:
            dl = f" · 📅 {t['deadline']}" if t["deadline"] else ""
            lines.append(f"{t['p_icon']} {t['name']}\n   👤 {t['responsible']}{dl}")
        lines.append("")
    lines.append(f"_Всего: {len(tasks)}_")
    return "\n".join(lines)

def fmt_person_tasks(tasks: list, name: str) -> str:
    icon = PERSON_ICONS.get(name, "👤")
    if not tasks:
        return f"✅ У {name} нет задач в работе!"
    lines = [f"{icon} *Задачи — {name}*\n"]
    for t in tasks:
        dl = f" · 📅 {t['deadline']}" if t["deadline"] else ""
        d_icon = DIR_ICONS.get(t["direction"], "📁")
        lines.append(f"{t['p_icon']} {t['name']}\n   {d_icon} {t['direction']}{dl}")
    lines.append(f"\n_Всего: {len(tasks)}_")
    return "\n".join(lines)

def fmt_direction_tasks(tasks: list, direction: str) -> str:
    icon = DIR_ICONS.get(direction, "📁")
    if not tasks:
        return f"✅ Нет задач по {icon} {direction}!"
    lines = [f"{icon} *Задачи — {direction}*\n"]
    for t in tasks:
        dl = f" · 📅 {t['deadline']}" if t["deadline"] else ""
        lines.append(f"{t['p_icon']} {t['name']}\n   👤 {t['responsible']}{dl}")
    lines.append(f"\n_Всего: {len(tasks)}_")
    return "\n".join(lines)

def fmt_deadline_message(overdue, today_tasks, tomorrow_tasks, person=None) -> str:
    suffix = f" — {person}" if person else ""
    if not overdue and not today_tasks and not tomorrow_tasks:
        return f"✅ Нет задач с дедлайном{suffix}!"
    lines = [f"📅 *Дедлайны{suffix}*\n"]
    if overdue:
        lines.append("🚨 *Просроченные задачи:*")
        for t in overdue:
            lines.append(f"{t['p_icon']} {t['name']}\n   👤 {t['responsible']} · {DIR_ICONS.get(t['direction'],'📁')} {t['direction']} · 📅 {t['deadline']}")
        lines.append("")
    if today_tasks:
        lines.append("🔴 *Задачи на сегодня:*")
        for t in today_tasks:
            lines.append(f"{t['p_icon']} {t['name']}\n   👤 {t['responsible']} · {DIR_ICONS.get(t['direction'],'📁')} {t['direction']}")
        lines.append("")
    if tomorrow_tasks:
        lines.append("🟡 *Задачи с дедлайном завтра:*")
        for t in tomorrow_tasks:
            lines.append(f"{t['p_icon']} {t['name']}\n   👤 {t['responsible']} · {DIR_ICONS.get(t['direction'],'📁')} {t['direction']}")
    return "\n".join(lines)

def fmt_personal_tasks(tasks: list, section=None) -> str:
    title = f"*{SECTION_ICONS.get(section,'')} {section}*" if section else "*🌸 Мои личные задачи*"
    if not tasks:
        return f"✅ {title.replace('*','')} — нет задач!"
    lines = [f"{title}\n"]
    if not section:
        by_sec = {}
        for t in tasks:
            by_sec.setdefault(t["section"], []).append(t)
        for sec, items in by_sec.items():
            lines.append(f"{SECTION_ICONS.get(sec,'📌')} *{sec}*")
            for t in items:
                dl = f" · 📅 {t['deadline']}" if t["deadline"] else ""
                lines.append(f"{t['p_icon']} {t['name']}{dl}")
            lines.append("")
    else:
        for t in tasks:
            dl = f" · 📅 {t['deadline']}" if t["deadline"] else ""
            lines.append(f"{t['p_icon']} {t['name']}{dl}")
    lines.append(f"\n_Всего: {len(tasks)}_")
    return "\n".join(lines)


# ─── Напоминания ─────────────────────────────────────────────────────────────
async def send_morning_reminder(context):
    try:
        overdue, today_tasks, tomorrow_tasks = get_deadline_tasks()
        if not overdue and not today_tasks and not tomorrow_tasks:
            return
        text = fmt_deadline_message(overdue, today_tasks, tomorrow_tasks)
        await context.bot.send_message(chat_id=ALLOWED_CHAT_ID, text=text, parse_mode="Markdown")
        logger.info("Утреннее напоминание отправлено")
    except Exception as e:
        logger.error(f"Ошибка утреннего напоминания: {e}", exc_info=True)

async def send_evening_reminder(context):
    try:
        team_text = "🌙 *Добрый вечер!*\n\nНе забудьте отметить все выполненные сегодня задачи.\nНапишите: «Выполнила: название задачи»"
        await context.bot.send_message(chat_id=ALLOWED_CHAT_ID, text=team_text, parse_mode="Markdown")
        andrey_text = f"💰 [Андрей](tg://user?id={ANDREY_TG_ID}), нужно внести все платежи в чат ТГ."
        await context.bot.send_message(chat_id=ALLOWED_CHAT_ID, text=andrey_text, parse_mode="Markdown")
        logger.info("Вечернее напоминание отправлено")
    except Exception as e:
        logger.error(f"Ошибка вечернего напоминания: {e}", exc_info=True)

async def send_margo_evening_reminder(context):
    try:
        text = f"[Марго](tg://user?id={MARGO_TG_ID}), не забудьте прислать скрин выписки из банка за сегодня с комментариями, если требуются. 🫶"
        await context.bot.send_message(chat_id=ALLOWED_CHAT_ID, text=text, parse_mode="Markdown")
        logger.info("Напоминание Марго о выписке отправлено")
    except Exception as e:
        logger.error(f"Ошибка напоминания Марго: {e}", exc_info=True)


async def post_init(app):
    """При старте — проверяем не пропустили ли утреннее напоминание."""
    from datetime import datetime
    now = datetime.now(timezone.utc)
    if 8 <= now.hour < 11:  # 11:00–14:00 МСК
        logger.info("Старт после 08:00 UTC — отправляю пропущенное напоминание")
        try:
            overdue, today_tasks, tomorrow_tasks = get_deadline_tasks()
            if overdue or today_tasks or tomorrow_tasks:
                text = fmt_deadline_message(overdue, today_tasks, tomorrow_tasks)
                await app.bot.send_message(chat_id=ALLOWED_CHAT_ID, text=text, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Ошибка post_init напоминания: {e}")


# ─── Обработчик сообщений ───────────────────────────────────────────────────
async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return
    msg = update.message
    if not msg or not msg.text:
        return
    sender = msg.from_user.first_name or "Неизвестный"
    logger.info(f"Сообщение от {sender}: {msg.text[:100]}")

    try:
        result = ask_claude(msg.text, sender)
        t = result.get("type")
        logger.info(f"Тип: {t}")

        if t == "skip":
            return

        # ── Показ задач ──────────────────────────────────────────────────
        elif t == "show_all":
            await msg.reply_text("⏳ Загружаю...")
            tasks = get_work_tasks()
            await msg.reply_text(fmt_work_tasks(tasks, "Все рабочие задачи"), parse_mode="Markdown")

        elif t == "show_my_work":
            name = result.get("person", sender).strip().capitalize()
            if name not in VALID_NAMES:
                name = sender.strip().capitalize()
            if name not in VALID_NAMES:
                await msg.reply_text("❓ Не могу определить кто ты. Напиши «задачи Галии» явно.")
                return
            await msg.reply_text(f"⏳ Загружаю твои задачи...")
            tasks = get_work_tasks(person=name)
            await msg.reply_text(fmt_person_tasks(tasks, name), parse_mode="Markdown")

        elif t == "show_person":
            name = result.get("person", "").strip().capitalize()
            if name not in VALID_NAMES:
                await msg.reply_text(f"❓ Доступные: {', '.join(VALID_NAMES)}")
                return
            await msg.reply_text(f"⏳ Загружаю задачи {name}...")
            tasks = get_work_tasks(person=name)
            await msg.reply_text(fmt_person_tasks(tasks, name), parse_mode="Markdown")

        elif t == "show_direction":
            direction = result.get("direction", "").strip()
            if direction not in VALID_DIRS:
                await msg.reply_text(f"❓ Доступные: {', '.join(VALID_DIRS)}")
                return
            tasks = get_work_tasks(direction=direction)
            await msg.reply_text(fmt_direction_tasks(tasks, direction), parse_mode="Markdown")

        elif t == "show_person_direction":
            name      = result.get("person", "").strip().capitalize()
            direction = result.get("direction", "").strip()
            tasks = get_work_tasks(person=name, direction=direction)
            icon = PERSON_ICONS.get(name, "👤")
            d_icon = DIR_ICONS.get(direction, "📁")
            if not tasks:
                await msg.reply_text(f"✅ У {name} нет задач по {direction}!")
            else:
                lines = [f"{icon} {d_icon} *{name} — {direction}*\n"]
                for task in tasks:
                    dl = f" · 📅 {task['deadline']}" if task["deadline"] else ""
                    lines.append(f"{task['p_icon']} {task['name']}{dl}")
                lines.append(f"\n_Всего: {len(tasks)}_")
                await msg.reply_text("\n".join(lines), parse_mode="Markdown")

        elif t == "show_priority":
            priority  = result.get("priority", "").strip()
            person    = result.get("person") or None
            direction = result.get("direction") or None
            if person == "null": person = None
            if direction == "null": direction = None
            if priority not in ["🔥 Срочно", "⚡ Важно", "📌 Обычное"]:
                await msg.reply_text("❓ Доступные приоритеты: срочные, важные, обычные")
                return
            tasks = get_work_tasks(person=person, direction=direction, priority=priority)
            p_icon = "🔥" if "Срочно" in priority else "⚡" if "Важно" in priority else "📌"
            parts = [priority]
            if person: parts.append(person)
            if direction: parts.append(direction)
            title = f"{p_icon} " + " — ".join(parts)
            await msg.reply_text(fmt_work_tasks(tasks, title), parse_mode="Markdown")

        elif t == "show_ideas":
            person    = result.get("person") or None
            direction = result.get("direction") or None
            if person == "null": person = None
            if direction == "null": direction = None
            tasks = get_work_tasks(person=person, direction=direction, status="Идея")
            parts = []
            if person: parts.append(person)
            if direction: parts.append(direction)
            suffix = " — " + " / ".join(parts) if parts else ""
            await msg.reply_text(fmt_work_tasks(tasks, f"💡 Идеи{suffix}"), parse_mode="Markdown")

        elif t == "show_deadlines":
            person = result.get("person") or None
            if person == "null": person = None
            await msg.reply_text("⏳ Проверяю дедлайны...")
            try:
                overdue, today_tasks, tomorrow_tasks = get_deadline_tasks(person=person)
                text = fmt_deadline_message(overdue, today_tasks, tomorrow_tasks, person=person)
                await msg.reply_text(text, parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Ошибка дедлайнов: {e}", exc_info=True)
                await msg.reply_text(f"⚠️ Ошибка: {str(e)[:200]}")

        elif t == "show_overdue":
            person = result.get("person") or None
            if person == "null": person = None
            tasks = get_overdue_tasks(person=person)
            suffix = f" — {person}" if person else ""
            if not tasks:
                await msg.reply_text(f"✅ Просроченных задач нет{suffix}!")
            else:
                lines = [f"🚨 *Просроченные задачи{suffix}*\n"]
                for task in tasks:
                    lines.append(f"{task['p_icon']} {task['name']}\n   👤 {task['responsible']} · {DIR_ICONS.get(task['direction'],'📁')} {task['direction']} · 📅 {task['deadline']}")
                lines.append(f"\n_Всего: {len(tasks)}_")
                await msg.reply_text("\n".join(lines), parse_mode="Markdown")

        elif t == "show_personal_all":
            tasks = get_personal_tasks()
            await msg.reply_text(fmt_personal_tasks(tasks), parse_mode="Markdown")

        elif t == "show_personal_section":
            section = result.get("section", "").strip()
            if section not in VALID_SECTIONS:
                await msg.reply_text(f"❓ Доступные разделы: {', '.join(VALID_SECTIONS)}")
                return
            tasks = get_personal_tasks(section=section)
            await msg.reply_text(fmt_personal_tasks(tasks, section=section), parse_mode="Markdown")

        # ── Создание задач ───────────────────────────────────────────────
        elif t == "new":
            name        = result.get("task_name", "")
            responsible = result.get("responsible", "Марго")
            direction   = fix_direction(responsible, result.get("direction", "Общее"))
            priority    = result.get("priority", "📌 Обычное")
            status      = result.get("status", "В работе")
            deadline    = parse_deadline(result.get("deadline"))
            if status not in VALID_STATUSES:
                status = "В работе"
            if not name:
                await msg.reply_text("❓ Не поняла название задачи.")
                return
            create_work_task(name, responsible, direction, priority, status, deadline)
            dl_str = f" · 📅 {deadline}" if deadline else ""
            await msg.reply_text(
                f"✅ Задача добавлена!\n\n📌 *{name}*\n👤 {responsible} · {direction} · {priority}\n{STATUS_ICONS.get(status,'🔵')} {status}{dl_str}",
                parse_mode="Markdown"
            )

        elif t == "new_many":
            items = result.get("tasks", [])
            if not items:
                await msg.reply_text("❓ Не поняла список задач.")
                return
            await msg.reply_text(f"⏳ Добавляю {len(items)} задач...")
            added = []
            for item in items:
                name        = item.get("task_name", "").strip()
                if not name: continue
                responsible = item.get("responsible", "Марго")
                direction   = fix_direction(responsible, item.get("direction", "Общее"))
                priority    = item.get("priority", "📌 Обычное")
                status      = item.get("status", "В работе")
                deadline    = parse_deadline(item.get("deadline"))
                if status not in VALID_STATUSES: status = "В работе"
                create_work_task(name, responsible, direction, priority, status, deadline)
                dl_str = f" · 📅 {deadline}" if deadline else ""
                added.append(f"📌 *{name}*\n   👤 {responsible} · {direction}{dl_str}")
            lines = [f"✅ Добавлено: {len(added)}\n"]
            lines.extend(added)
            await msg.reply_text("\n\n".join(lines), parse_mode="Markdown")

        elif t == "new_personal":
            name     = result.get("task_name", "")
            section  = result.get("section", "Цели и приоритеты")
            priority = result.get("priority", "📌 Обычное")
            if not name:
                await msg.reply_text("❓ Не поняла название личной задачи.")
                return
            if section not in VALID_SECTIONS:
                section = "Цели и приоритеты"
            create_personal_task(name, section, priority)
            icon = SECTION_ICONS.get(section, "📌")
            await msg.reply_text(
                f"✅ Личная задача добавлена!\n\n{icon} *{name}*\n{section} · {priority}",
                parse_mode="Markdown"
            )

        # ── Изменение задач ──────────────────────────────────────────────
        elif t == "done":
            name        = result.get("task_name", "")
            responsible = result.get("responsible", sender)
            status      = result.get("status", "Готово")
            page_id = find_task(name)
            if page_id:
                ok = notion_update(page_id, {
                    "Статус": {"select": {"name": status}},
                    "Ответственный": {"select": {"name": responsible}}
                })
                if ok:
                    await msg.reply_text(f"✅ Зафиксировано! «{name}» → {status}")
                else:
                    await msg.reply_text("⚠️ Нашла задачу, но не смогла обновить.")
            else:
                create_work_task(name, responsible, status=status)
                await msg.reply_text(f"✅ Создала и закрыла «{name}» → {status}")

        elif t == "done_many":
            task_names  = result.get("tasks", [])
            responsible = result.get("responsible", sender)
            if not task_names:
                await msg.reply_text("❓ Напиши: «закрыть: задача1, задача2»")
                return
            await msg.reply_text(f"⏳ Закрываю {len(task_names)} задач...")
            done_list, not_found = [], []
            for name in task_names:
                name = name.strip()
                page_id = find_task(name)
                if page_id and notion_update(page_id, {
                    "Статус": {"select": {"name": "Готово"}},
                    "Ответственный": {"select": {"name": responsible}}
                }):
                    done_list.append(name)
                else:
                    not_found.append(name)
            lines = []
            if done_list:
                lines.append(f"✅ *Закрыто ({len(done_list)}):*")
                for n in done_list: lines.append(f"· {n}")
            if not_found:
                lines.append(f"\n❓ *Не нашла ({len(not_found)}):*")
                for n in not_found: lines.append(f"· {n}")
            await msg.reply_text("\n".join(lines), parse_mode="Markdown")

        elif t == "change_direction":
            task_name     = result.get("task_name", "")
            new_direction = result.get("new_direction", "").strip()
            if new_direction not in VALID_DIRS:
                await msg.reply_text(f"❓ Доступные направления: {', '.join(VALID_DIRS)}")
                return
            page_id = find_task(task_name)
            if page_id:
                ok = notion_update(page_id, {"Направление": {"select": {"name": new_direction}}})
                if ok:
                    await msg.reply_text(f"✅ Задача «{task_name}» перенесена в {DIR_ICONS.get(new_direction,'')} {new_direction}")
                else:
                    await msg.reply_text("⚠️ Не смогла обновить направление.")
            else:
                await msg.reply_text(f"❓ Не нашла задачу «{task_name}».")

        elif t == "change_responsible":
            task_name       = result.get("task_name", "")
            new_responsible = result.get("new_responsible", "").strip().capitalize()
            if new_responsible not in VALID_NAMES:
                await msg.reply_text(f"❓ Доступные: {', '.join(VALID_NAMES)}")
                return
            page_id = find_task(task_name)
            if page_id:
                ok = notion_update(page_id, {"Ответственный": {"select": {"name": new_responsible}}})
                if ok:
                    icon = PERSON_ICONS.get(new_responsible, "👤")
                    await msg.reply_text(f"✅ Задача «{task_name}» теперь у {icon} {new_responsible}")
                else:
                    await msg.reply_text("⚠️ Не смогла обновить ответственного.")
            else:
                await msg.reply_text(f"❓ Не нашла задачу «{task_name}».")

    except Exception as e:
        logger.error(f"Ошибка: {e}", exc_info=True)


# ─── Запуск ─────────────────────────────────────────────────────────────────
def main():
    import time as time_module

    threading.Thread(target=start_web, daemon=True).start()
    logger.info(f"Веб-сервер на порту {PORT}")

    while True:
        try:
            app = (Application.builder()
                   .token(TELEGRAM_TOKEN)
                   .post_init(post_init)
                   .build())
            app.add_handler(MessageHandler(filters.TEXT, handle))

            jq = app.job_queue
            jq.run_daily(send_morning_reminder,
                         time=time(hour=8, minute=0, tzinfo=timezone.utc),
                         name="morning")
            jq.run_daily(send_evening_reminder,
                         time=time(hour=15, minute=0, tzinfo=timezone.utc),
                         name="evening")
            jq.run_daily(send_margo_evening_reminder,
                         time=time(hour=17, minute=0, tzinfo=timezone.utc),
                         name="margo_bank")

            logger.info("Бот запущен ✅  (утро 11:00 МСК, вечер 18:00 МСК, Марго 20:00 МСК)")
            app.run_polling(drop_pending_updates=True, allowed_updates=["message"])
            break
        except Exception as e:
            logger.error(f"Ошибка запуска, перезапуск через 30 сек: {e}")
            time_module.sleep(30)


if __name__ == "__main__":
    main()
