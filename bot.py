import os
import json
import logging
import threading
import requests
from datetime import date, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes, JobQueue

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
NOTION_TOKEN = os.environ["NOTION_TOKEN"]
NOTION_DATABASE_ID = os.environ["NOTION_DATABASE_ID"]
NOTION_PERSONAL_DATABASE_ID = os.environ["NOTION_PERSONAL_DATABASE_ID"]
ALLOWED_CHAT_ID = int(os.environ["ALLOWED_CHAT_ID"])
PORT = int(os.environ.get("PORT", 8080))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты ассистент Марго, который управляет задачами в Notion.

КОМАНДА: Марго, Галия, Ольга, Андрей
НАПРАВЛЕНИЯ: АЭлит, Контент, Фокус-группа, Общее
ПРИОРИТЕТЫ: 🔥 Срочно, ⚡ Важно, 📌 Обычное
СТАТУСЫ: В работе, Ждём, Идея, Готово, Отменено
ЛИЧНЫЕ РАЗДЕЛЫ: Цели и приоритеты, Семья и быт, Обучение

Проанализируй сообщение и верни ТОЛЬКО валидный JSON без markdown и без пояснений.

ТИПЫ:

show_all — показать все рабочие задачи в работе
Пример: "покажи все задачи", "что в работе", "задачи"
{"type": "show_all"}

show_person — задачи конкретного человека
Пример: "задачи Ольги", "что у Марго", "покажи Андрея"
{"type": "show_person", "person": "Марго"}

show_direction — задачи по направлению
Пример: "задачи по АЭлит", "что по Контенту"
{"type": "show_direction", "direction": "АЭлит"}

show_person_direction — задачи человека по направлению
Пример: "задачи Марго АЭлит", "Ольга Контент"
{"type": "show_person_direction", "person": "Марго", "direction": "АЭлит"}

show_priority — задачи по приоритету (можно с фильтрами)
Пример: "срочные задачи", "важные задачи Галии", "срочное по АЭлит"
{"type": "show_priority", "priority": "🔥 Срочно", "person": null, "direction": null}

show_personal_all — все личные задачи Марго
Пример: "мои задачи", "личные задачи", "что у меня"
{"type": "show_personal_all"}

show_personal_section — личные задачи по разделу
Пример: "мои цели", "семья", "обучение"
{"type": "show_personal_section", "section": "Цели и приоритеты"}

done — отметить одну задачу выполненной
Пример: "выполнила: отправила КП", "сделала: позвонила клиенту", "готово: написала пост"
{"type": "done", "task_name": "название", "responsible": "Марго", "status": "Готово"}

done_many — отметить несколько задач выполненными
Пример: "закрыть: задача1, задача2, задача3", "выполнены: X, Y"
{"type": "done_many", "tasks": ["задача1", "задача2"], "responsible": "Марго"}

new — добавить одну новую задачу
Пример: "добавь задачу: название — Галия — АЭлит — важно"
Пример: "задача: позвонить клиенту, Ольга, АЭлит, срочно, дедлайн 8.07"
Пример: "Добавь задачу: Протестировать Битрикс - Галия - АЭлит - важно - в работе"
{"type": "new", "task_name": "название", "responsible": "Марго", "direction": "Общее", "priority": "📌 Обычное", "deadline": null, "status": "В работе"}

new_many — добавить несколько задач сразу
Пример: "добавить задачи: 1. X — Ольга — АЭлит 2. Y — Марго — Контент"
{"type": "new_many", "tasks": [{"task_name": "X", "responsible": "Ольга", "direction": "АЭлит", "priority": "📌 Обычное", "deadline": null, "status": "В работе"}]}

new_personal — добавить личную задачу
Пример: "моя задача: купить продукты", "добавь в цели: пробежать 5км", "личное: записаться к врачу"
{"type": "new_personal", "task_name": "название", "section": "Семья и быт", "priority": "📌 Обычное"}

change_direction — изменить направление задачи
Пример: "перенеси задачу X в Контент", "измени направление X на АЭлит"
{"type": "change_direction", "task_name": "название", "new_direction": "Контент"}

skip — сообщение не про задачи
{"type": "skip"}

ВАЖНО: Если сообщение содержит слова "задача", "добавь", "создай", "поставь", "нужно сделать" — это почти всегда тип new или new_many. Не возвращай skip для таких сообщений."""


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


def ask_claude(text, sender):
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={
            "model": "claude-sonnet-4-6",
            "max_tokens": 1000,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": f"Отправитель: {sender}\nСообщение: {text}"}]
        },
        timeout=30
    )
    r.raise_for_status()
    raw = r.json()["content"][0]["text"].strip()
    # Убираем markdown обёртку если есть
    if raw.startswith("```"):
        lines = raw.split("\n")
        lines = [l for l in lines if not l.startswith("```")]
        raw = "\n".join(lines).strip()
    logger.info(f"Claude raw response: {raw}")
    if not raw:
        logger.error("Claude вернул пустой ответ!")
        return {"type": "skip"}
    return json.loads(raw)


def query_notion(database_id, body):
    r = requests.post(
        f"https://api.notion.com/v1/databases/{database_id}/query",
        headers={"Authorization": f"Bearer {NOTION_TOKEN}", "Notion-Version": "2022-06-28", "Content-Type": "application/json"},
        json=body, timeout=15
    )
    r.raise_for_status()
    return r.json().get("results", [])


def get_all_tasks():
    return query_notion(NOTION_DATABASE_ID, {
        "filter": {"property": "Статус", "select": {"equals": "В работе"}},
        "sorts": [{"property": "Направление", "direction": "ascending"}, {"property": "Ответственный", "direction": "ascending"}]
    })

def get_tasks_by_person(name):
    return query_notion(NOTION_DATABASE_ID, {
        "filter": {"and": [
            {"property": "Статус", "select": {"equals": "В работе"}},
            {"property": "Ответственный", "select": {"equals": name}}
        ]},
        "sorts": [{"property": "Приоритет", "direction": "ascending"}]
    })

def get_tasks_by_direction(direction):
    return query_notion(NOTION_DATABASE_ID, {
        "filter": {"and": [
            {"property": "Статус", "select": {"equals": "В работе"}},
            {"property": "Направление", "select": {"equals": direction}}
        ]},
        "sorts": [{"property": "Ответственный", "direction": "ascending"}]
    })

def get_tasks_by_person_direction(name, direction):
    return query_notion(NOTION_DATABASE_ID, {
        "filter": {"and": [
            {"property": "Статус", "select": {"equals": "В работе"}},
            {"property": "Ответственный", "select": {"equals": name}},
            {"property": "Направление", "select": {"equals": direction}}
        ]},
        "sorts": [{"property": "Приоритет", "direction": "ascending"}]
    })

def get_personal_all():
    return query_notion(NOTION_PERSONAL_DATABASE_ID, {
        "filter": {"property": "Статус", "select": {"equals": "В работе"}},
        "sorts": [{"property": "Раздел", "direction": "ascending"}]
    })

def get_personal_by_section(section):
    return query_notion(NOTION_PERSONAL_DATABASE_ID, {
        "filter": {"and": [
            {"property": "Статус", "select": {"equals": "В работе"}},
            {"property": "Раздел", "select": {"equals": section}}
        ]},
        "sorts": [{"property": "Приоритет", "direction": "ascending"}]
    })


def parse_work_task(task):
    props = task["properties"]
    name = props.get("Задача", {}).get("title", [{}])
    name = name[0]["text"]["content"] if name else ""
    responsible = (props.get("Ответственный", {}).get("select") or {}).get("name", "—")
    direction = (props.get("Направление", {}).get("select") or {}).get("name", "Общее")
    priority = (props.get("Приоритет", {}).get("select") or {}).get("name", "")
    deadline = (props.get("Дедлайн", {}).get("date") or {}).get("start", "")
    icon = "🔥" if "Срочно" in priority else "⚡" if "Важно" in priority else "📌"
    return {"name": name, "responsible": responsible, "direction": direction, "icon": icon, "deadline": deadline}

def parse_personal_task(task):
    props = task["properties"]
    name = props.get("Задача", {}).get("title", [{}])
    name = name[0]["text"]["content"] if name else ""
    section = (props.get("Раздел", {}).get("select") or {}).get("name", "Общее")
    priority = (props.get("Приоритет", {}).get("select") or {}).get("name", "")
    deadline = (props.get("Дедлайн", {}).get("date") or {}).get("start", "")
    icon = "🔥" if "Срочно" in priority else "⚡" if "Важно" in priority else "📌"
    return {"name": name, "section": section, "icon": icon, "deadline": deadline}


DIR_ICONS = {"АЭлит": "🏭", "Контент": "📱", "Фокус-группа": "🎓", "Общее": "📋"}
PERSON_ICONS = {"Марго": "👩‍💼", "Галия": "👩‍💻", "Ольга": "👩‍📋", "Андрей": "👨‍🔧"}
SECTION_ICONS = {"Цели и приоритеты": "🎯", "Семья и быт": "🧡", "Обучение": "📚"}


def fmt_all(tasks):
    if not tasks:
        return "✅ Нет задач в работе!"
    by_dir = {}
    for task in tasks:
        t = parse_work_task(task)
        by_dir.setdefault(t["direction"], []).append(t)
    lines = ["📋 *Все рабочие задачи*\n"]
    for d, items in by_dir.items():
        lines.append(f"{DIR_ICONS.get(d,'📁')} *{d}*")
        for t in items:
            dl = f" · {t['deadline']}" if t['deadline'] else ""
            lines.append(f"{t['icon']} {t['name']}\n   👤 {t['responsible']}{dl}")
        lines.append("")
    lines.append(f"_Всего: {len(tasks)}_")
    return "\n".join(lines)

def fmt_person(tasks, name):
    icon = PERSON_ICONS.get(name, "👤")
    if not tasks:
        return f"✅ У {name} нет задач в работе!"
    lines = [f"{icon} *Задачи — {name}*\n"]
    for task in tasks:
        t = parse_work_task(task)
        dl = f" · {t['deadline']}" if t['deadline'] else ""
        lines.append(f"{t['icon']} {t['name']}\n   {DIR_ICONS.get(t['direction'],'📁')} {t['direction']}{dl}")
    lines.append(f"\n_Всего: {len(tasks)}_")
    return "\n".join(lines)

def fmt_direction(tasks, direction):
    icon = DIR_ICONS.get(direction, "📁")
    if not tasks:
        return f"✅ Нет задач по {icon} {direction}!"
    lines = [f"{icon} *Задачи — {direction}*\n"]
    for task in tasks:
        t = parse_work_task(task)
        dl = f" · {t['deadline']}" if t['deadline'] else ""
        lines.append(f"{t['icon']} {t['name']}\n   👤 {t['responsible']}{dl}")
    lines.append(f"\n_Всего: {len(tasks)}_")
    return "\n".join(lines)

def fmt_personal_all(tasks):
    if not tasks:
        return "✅ Нет личных задач в работе!"
    by_sec = {}
    for task in tasks:
        t = parse_personal_task(task)
        by_sec.setdefault(t["section"], []).append(t)
    lines = ["🌸 *Мои личные задачи*\n"]
    for sec, items in by_sec.items():
        lines.append(f"{SECTION_ICONS.get(sec,'📌')} *{sec}*")
        for t in items:
            dl = f" · {t['deadline']}" if t['deadline'] else ""
            lines.append(f"{t['icon']} {t['name']}{dl}")
        lines.append("")
    lines.append(f"_Всего: {len(tasks)}_")
    return "\n".join(lines)

def fmt_personal_section(tasks, section):
    icon = SECTION_ICONS.get(section, "📌")
    if not tasks:
        return f"✅ Нет задач в разделе {icon} {section}!"
    lines = [f"{icon} *{section}*\n"]
    for task in tasks:
        t = parse_personal_task(task)
        dl = f" · {t['deadline']}" if t['deadline'] else ""
        lines.append(f"{t['icon']} {t['name']}{dl}")
    lines.append(f"\n_Всего: {len(tasks)}_")
    return "\n".join(lines)


def find_task(name):
    r = requests.post(
        f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
        headers={"Authorization": f"Bearer {NOTION_TOKEN}", "Notion-Version": "2022-06-28", "Content-Type": "application/json"},
        json={"filter": {"property": "Задача", "title": {"contains": name[:20]}}},
        timeout=15
    )
    results = r.json().get("results", [])
    return results[0]["id"] if results else None

def update_task(page_id, status, responsible):
    r = requests.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers={"Authorization": f"Bearer {NOTION_TOKEN}", "Notion-Version": "2022-06-28", "Content-Type": "application/json"},
        json={"properties": {
            "Статус": {"select": {"name": status}},
            "Ответственный": {"select": {"name": responsible}}
        }},
        timeout=15
    )
    return r.status_code == 200

def create_work_task(name, responsible, direction="Общее", priority="📌 Обычное", status="В работе", deadline=None):
    props = {
        "Задача": {"title": [{"text": {"content": name}}]},
        "Статус": {"select": {"name": status}},
        "Ответственный": {"select": {"name": responsible}},
        "Направление": {"select": {"name": direction}},
        "Приоритет": {"select": {"name": priority}},
    }
    if deadline and deadline != "null":
        props["Дедлайн"] = {"date": {"start": deadline}}
    requests.post(
        "https://api.notion.com/v1/pages",
        headers={"Authorization": f"Bearer {NOTION_TOKEN}", "Notion-Version": "2022-06-28", "Content-Type": "application/json"},
        json={"parent": {"database_id": NOTION_DATABASE_ID}, "properties": props},
        timeout=15
    )

def create_personal_task(name, section="Цели и приоритеты", priority="📌 Обычное"):
    requests.post(
        "https://api.notion.com/v1/pages",
        headers={"Authorization": f"Bearer {NOTION_TOKEN}", "Notion-Version": "2022-06-28", "Content-Type": "application/json"},
        json={"parent": {"database_id": NOTION_PERSONAL_DATABASE_ID}, "properties": {
            "Задача": {"title": [{"text": {"content": name}}]},
            "Статус": {"select": {"name": "В работе"}},
            "Ответственный": {"select": {"name": "Марго"}},
            "Раздел": {"select": {"name": section}},
            "Приоритет": {"select": {"name": priority}},
        }},
        timeout=15
    )


async def send_deadline_reminders(context: ContextTypes.DEFAULT_TYPE):
    """Утреннее напоминание о задачах с дедлайном сегодня и завтра"""
    try:
        today = date.today().isoformat()
        tomorrow = (date.today() + timedelta(days=1)).isoformat()

        # Ищем задачи с дедлайном сегодня или завтра
        results = query_notion(NOTION_DATABASE_ID, {
            "filter": {
                "or": [
                    {"property": "Дедлайн", "date": {"equals": today}},
                    {"property": "Дедлайн", "date": {"equals": tomorrow}},
                ]
            },
            "sorts": [{"property": "Дедлайн", "direction": "ascending"}]
        })

        if not results:
            return

        today_tasks = []
        tomorrow_tasks = []

        for task in results:
            t = parse_work_task(task)
            if not t["name"]:
                continue
            dl = (task["properties"].get("Дедлайн", {}).get("date") or {}).get("start", "")
            if dl == today:
                today_tasks.append(t)
            elif dl == tomorrow:
                tomorrow_tasks.append(t)

        lines = ["📅 *Напоминание о дедлайнах*\n"]

        if today_tasks:
            lines.append("🔴 *Сегодня:*")
            for t in today_tasks:
                lines.append(f"{t['icon']} {t['name']}\n   👤 {t['responsible']} · {DIR_ICONS.get(t['direction'],'📁')} {t['direction']}")
            lines.append("")

        if tomorrow_tasks:
            lines.append("🟡 *Завтра:*")
            for t in tomorrow_tasks:
                lines.append(f"{t['icon']} {t['name']}\n   👤 {t['responsible']} · {DIR_ICONS.get(t['direction'],'📁')} {t['direction']}")

        text_to_send = "\n".join(lines)
        await context.bot.send_message(
            chat_id=ALLOWED_CHAT_ID,
            text=text_to_send,
            parse_mode="Markdown"
        )
        logger.info(f"Напоминание отправлено: {len(today_tasks)} сегодня, {len(tomorrow_tasks)} завтра")

    except Exception as e:
        logger.error(f"Ошибка при отправке напоминания: {e}", exc_info=True)


async def send_evening_reminders(context: ContextTypes.DEFAULT_TYPE):
    """Вечернее напоминание — отметить выполненные задачи и напоминание Андрею"""
    try:
        # Напоминание для всей команды
        team_text = "🌙 *Добрый вечер!*\n\nНе забудьте отметить все выполненные сегодня задачи.\nНапишите: «Выполнила: название задачи»"
        await context.bot.send_message(
            chat_id=ALLOWED_CHAT_ID,
            text=team_text,
            parse_mode="Markdown"
        )
        # Напоминание для Андрея с упоминанием
        andrey_text = "💰 [Андрей](tg://user?id=5106438154), нужно внести все платежи в чат ТГ."
        await context.bot.send_message(
            chat_id=ALLOWED_CHAT_ID,
            text=andrey_text,
            parse_mode="Markdown"
        )
        logger.info("Вечерние напоминания отправлены")
    except Exception as e:
        logger.error(f"Ошибка при отправке вечерних напоминаний: {e}", exc_info=True)


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return
    msg = update.message
    if not msg or not msg.text:
        return
    sender = msg.from_user.first_name or "Неизвестный"
    text = msg.text
    logger.info(f"Сообщение от {sender}: {text[:100]}")

    try:
        result = ask_claude(text, sender)
        t = result.get("type")
        logger.info(f"Тип: {t}")

        if t == "skip":
            return

        elif t == "show_all":
            await msg.reply_text("⏳ Загружаю...")
            tasks = get_all_tasks()
            await msg.reply_text(fmt_all(tasks), parse_mode="Markdown")

        elif t == "show_person":
            name = result.get("person", "").strip().capitalize()
            if name not in ["Марго", "Галия", "Ольга", "Андрей"]:
                await msg.reply_text("❓ Доступные: Марго, Галия, Ольга, Андрей")
                return
            await msg.reply_text(f"⏳ Загружаю задачи {name}...")
            tasks = get_tasks_by_person(name)
            await msg.reply_text(fmt_person(tasks, name), parse_mode="Markdown")

        elif t == "show_direction":
            direction = result.get("direction", "").strip()
            if direction not in ["АЭлит", "Контент", "Фокус-группа", "Общее"]:
                await msg.reply_text("❓ Доступные: АЭлит, Контент, Фокус-группа, Общее")
                return
            await msg.reply_text(f"⏳ Загружаю по {direction}...")
            tasks = get_tasks_by_direction(direction)
            await msg.reply_text(fmt_direction(tasks, direction), parse_mode="Markdown")

        elif t == "show_person_direction":
            name = result.get("person", "").strip().capitalize()
            direction = result.get("direction", "").strip()
            await msg.reply_text(f"⏳ Загружаю {name} / {direction}...")
            tasks = get_tasks_by_person_direction(name, direction)
            icon = PERSON_ICONS.get(name, "👤")
            dir_icon = DIR_ICONS.get(direction, "📁")
            if not tasks:
                await msg.reply_text(f"✅ У {name} нет задач по {direction}!")
            else:
                lines = [f"{icon} {dir_icon} *{name} — {direction}*\n"]
                for task in tasks:
                    t2 = parse_work_task(task)
                    dl = f" · {t2['deadline']}" if t2['deadline'] else ""
                    lines.append(f"{t2['icon']} {t2['name']}{dl}")
                lines.append(f"\n_Всего: {len(tasks)}_")
                await msg.reply_text("\n".join(lines), parse_mode="Markdown")

        elif t == "show_priority":
            priority = result.get("priority", "").strip()
            person = result.get("person")
            direction = result.get("direction")
            if not person or person == "null":
                person = None
            if not direction or direction == "null":
                direction = None
            valid = ["🔥 Срочно", "⚡ Важно", "📌 Обычное"]
            if priority not in valid:
                await msg.reply_text("❓ Доступные приоритеты: срочные, важные, обычные")
                return
            filters = [
                {"property": "Статус", "select": {"equals": "В работе"}},
                {"property": "Приоритет", "select": {"equals": priority}}
            ]
            if person:
                filters.append({"property": "Ответственный", "select": {"equals": person}})
            if direction:
                filters.append({"property": "Направление", "select": {"equals": direction}})
            await msg.reply_text("⏳ Загружаю...")
            tasks = query_notion(NOTION_DATABASE_ID, {"filter": {"and": filters}})
            p_icon = "🔥" if "Срочно" in priority else "⚡" if "Важно" in priority else "📌"
            title = f"{p_icon} *{priority} задачи"
            if person:
                title += f" — {person}"
            if direction:
                title += f" / {direction}"
            title += "*"
            if not tasks:
                await msg.reply_text(f"✅ {title.replace('*','')} — нет задач!")
                return
            lines = [title, ""]
            for task in tasks:
                t2 = parse_work_task(task)
                dl = f" · {t2['deadline']}" if t2['deadline'] else ""
                person_str = f"\n   👤 {t2['responsible']}" if not person else ""
                dir_str = f" · {DIR_ICONS.get(t2['direction'],'📁')} {t2['direction']}" if not direction else ""
                lines.append(f"{p_icon} {t2['name']}{person_str}{dir_str}{dl}")
            lines.append(f"\n_Всего: {len(tasks)}_")
            await msg.reply_text("\n".join(lines), parse_mode="Markdown")

        elif t == "show_personal_all":
            await msg.reply_text("⏳ Загружаю личные задачи...")
            tasks = get_personal_all()
            await msg.reply_text(fmt_personal_all(tasks), parse_mode="Markdown")

        elif t == "show_personal_section":
            section = result.get("section", "").strip()
            if section not in ["Цели и приоритеты", "Семья и быт", "Обучение"]:
                await msg.reply_text("❓ Доступные разделы: Цели и приоритеты, Семья и быт, Обучение")
                return
            tasks = get_personal_by_section(section)
            await msg.reply_text(fmt_personal_section(tasks, section), parse_mode="Markdown")

        elif t == "done":
            name = result.get("task_name", "")
            responsible = result.get("responsible", sender)
            status = result.get("status", "Готово")
            page_id = find_task(name)
            if page_id:
                ok = update_task(page_id, status, responsible)
                if ok:
                    await msg.reply_text(f"✅ Зафиксировано! «{name}» → {status}")
                else:
                    await msg.reply_text("⚠️ Нашла задачу, но не смогла обновить.")
            else:
                create_work_task(name, responsible, status=status)
                await msg.reply_text(f"✅ Создала «{name}» → {status}")

        elif t == "done_many":
            tasks_list = result.get("tasks", [])
            responsible = result.get("responsible", sender)
            if not tasks_list:
                await msg.reply_text("❓ Напиши: «закрыть: задача1, задача2»")
                return
            await msg.reply_text(f"⏳ Закрываю {len(tasks_list)} задач...")
            done_list, not_found = [], []
            for task_name in tasks_list:
                task_name = task_name.strip()
                page_id = find_task(task_name)
                if page_id and update_task(page_id, "Готово", responsible):
                    done_list.append(task_name)
                else:
                    not_found.append(task_name)
            lines = []
            if done_list:
                lines.append(f"✅ *Закрыто ({len(done_list)}):*")
                for n in done_list:
                    lines.append(f"· {n}")
            if not_found:
                lines.append(f"\n❓ *Не нашла ({len(not_found)}):*")
                for n in not_found:
                    lines.append(f"· {n}")
            await msg.reply_text("\n".join(lines), parse_mode="Markdown")

        elif t == "new":
            name = result.get("task_name", "")
            responsible = result.get("responsible", "Марго")
            direction = result.get("direction", "Общее")
            priority = result.get("priority", "📌 Обычное")
            deadline = result.get("deadline")
            status = result.get("status", "В работе")
            if not name:
                await msg.reply_text("❓ Не поняла название задачи.")
                return
            if deadline == "null":
                deadline = None
            valid_statuses = ["В работе", "Ждём", "Идея", "Готово", "Отменено"]
            if status not in valid_statuses:
                status = "В работе"
            create_work_task(name, responsible, direction, priority, status, deadline)
            dl_str = f" · 📅 {deadline}" if deadline else ""
            st_icons = {"В работе": "🔵", "Ждём": "🟡", "Идея": "⚪", "Готово": "✅", "Отменено": "❌"}
            await msg.reply_text(
                f"✅ Задача добавлена!\n\n📌 *{name}*\n👤 {responsible} · {direction} · {priority}\n{st_icons.get(status,'🔵')} {status}{dl_str}",
                parse_mode="Markdown"
            )

        elif t == "new_many":
            tasks_list = result.get("tasks", [])
            if not tasks_list:
                await msg.reply_text("❓ Не поняла список задач.")
                return
            await msg.reply_text(f"⏳ Добавляю {len(tasks_list)} задач...")
            added = []
            for item in tasks_list:
                name = item.get("task_name", "").strip()
                if not name:
                    continue
                responsible = item.get("responsible", "Марго")
                direction = item.get("direction", "Общее")
                priority = item.get("priority", "📌 Обычное")
                deadline = item.get("deadline")
                status = item.get("status", "В работе")
                if deadline == "null":
                    deadline = None
                if status not in ["В работе", "Ждём", "Идея", "Готово", "Отменено"]:
                    status = "В работе"
                create_work_task(name, responsible, direction, priority, status, deadline)
                dl_str = f" · 📅 {deadline}" if deadline else ""
                added.append(f"📌 *{name}*\n   👤 {responsible} · {direction}{dl_str}")
            lines = [f"✅ Добавлено: {len(added)}\n"]
            lines.extend(added)
            await msg.reply_text("\n\n".join(lines), parse_mode="Markdown")

        elif t == "new_personal":
            name = result.get("task_name", "")
            section = result.get("section", "Цели и приоритеты")
            priority = result.get("priority", "📌 Обычное")
            if not name:
                await msg.reply_text("❓ Не поняла название личной задачи.")
                return
            create_personal_task(name, section, priority)
            icon = SECTION_ICONS.get(section, "📌")
            await msg.reply_text(
                f"✅ Личная задача добавлена!\n\n{icon} *{name}*\n{section} · {priority}",
                parse_mode="Markdown"
            )

        elif t == "change_direction":
            task_name = result.get("task_name", "")
            new_direction = result.get("new_direction", "")
            valid_dirs = ["АЭлит", "Контент", "Фокус-группа", "Общее"]
            if new_direction not in valid_dirs:
                await msg.reply_text(f"❓ Доступные направления: {', '.join(valid_dirs)}")
                return
            page_id = find_task(task_name)
            if page_id:
                r = requests.patch(
                    f"https://api.notion.com/v1/pages/{page_id}",
                    headers={"Authorization": f"Bearer {NOTION_TOKEN}", "Notion-Version": "2022-06-28", "Content-Type": "application/json"},
                    json={"properties": {"Направление": {"select": {"name": new_direction}}}},
                    timeout=15
                )
                if r.status_code == 200:
                    await msg.reply_text(f"✅ Задача «{task_name}» перенесена в {DIR_ICONS.get(new_direction,'📁')} {new_direction}")
                else:
                    await msg.reply_text("⚠️ Не смогла обновить направление.")
            else:
                await msg.reply_text(f"❓ Не нашла задачу «{task_name}».")

    except Exception as e:
        logger.error(f"Ошибка: {e}", exc_info=True)


def main():
    threading.Thread(target=start_web, daemon=True).start()
    logger.info(f"Веб-сервер на порту {PORT}")
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT, handle))

    # Напоминания каждый день в 9:00 по московскому времени (UTC+3 = 06:00 UTC)
    import datetime as dt
    job_queue = app.job_queue
    job_queue.run_daily(
        send_deadline_reminders,
        time=dt.time(hour=6, minute=0, tzinfo=dt.timezone.utc),
        name="deadline_reminders"
    )
    # Вечернее напоминание в 18:00 МСК (15:00 UTC)
    job_queue.run_daily(
        send_evening_reminders,
        time=dt.time(hour=15, minute=0, tzinfo=dt.timezone.utc),
        name="evening_reminders"
    )
    logger.info("Бот запущен ✅ (утро 9:00 МСК, вечер 18:00 МСК)")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
