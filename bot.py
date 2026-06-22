import os
import json
import logging
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
NOTION_TOKEN = os.environ["NOTION_TOKEN"]
NOTION_DATABASE_ID = os.environ["NOTION_DATABASE_ID"]
ALLOWED_CHAT_ID = int(os.environ["ALLOWED_CHAT_ID"])
PORT = int(os.environ.get("PORT", 8080))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты ассистент, который управляет задачами в Notion для команды Марго.
Команда: Марго, Галия, Ольга, Андрей.
Направления: АЭлит, Контент, Фокус-группа, Общее.
Приоритеты: 🔥 Срочно, ⚡ Важно, 📌 Обычное.

Определи тип сообщения и ответь ТОЛЬКО JSON без markdown:

1. Если сообщение про ВЫПОЛНЕНИЕ задачи:
{"type": "done", "task_name": "название", "responsible": "имя", "status": "Готово"}

2. Если сообщение про НОВУЮ задачу (слова: задача, добавь, нужно сделать, поставь задачу):
{"type": "new", "task_name": "название задачи", "responsible": "имя или Марго", "direction": "АЭлит или Контент или Фокус-группа или Общее", "priority": "🔥 Срочно или ⚡ Важно или 📌 Обычное"}

3. Если сообщение НЕ про задачи:
{"type": "skip"}"""


class KeepAlive(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
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
        json={"model": "claude-sonnet-4-6", "max_tokens": 300, "system": SYSTEM_PROMPT,
              "messages": [{"role": "user", "content": f"Отправитель: {sender}\nСообщение: {text}"}]},
        timeout=30
    )
    r.raise_for_status()
    return json.loads(r.json()["content"][0]["text"].strip())


def query_notion(filters_body):
    """Универсальный запрос к Notion"""
    r = requests.post(
        f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
        headers={"Authorization": f"Bearer {NOTION_TOKEN}", "Notion-Version": "2022-06-28", "Content-Type": "application/json"},
        json=filters_body,
        timeout=15
    )
    r.raise_for_status()
    return r.json().get("results", [])


def get_tasks_in_progress():
    return query_notion({
        "filter": {"property": "Статус", "select": {"equals": "В работе"}},
        "sorts": [{"property": "Направление", "direction": "ascending"}, {"property": "Ответственный", "direction": "ascending"}]
    })


def get_tasks_by_person(name):
    """Задачи конкретного человека в работе"""
    return query_notion({
        "filter": {
            "and": [
                {"property": "Статус", "select": {"equals": "В работе"}},
                {"property": "Ответственный", "select": {"equals": name}}
            ]
        },
        "sorts": [{"property": "Приоритет", "direction": "ascending"}]
    })


def parse_task(task):
    """Разбираем задачу из Notion в словарь"""
    props = task["properties"]
    name = ""
    if props.get("Задача", {}).get("title"):
        name = props["Задача"]["title"][0]["text"]["content"]
    responsible = props.get("Ответственный", {}).get("select", {})
    responsible = responsible.get("name", "—") if responsible else "—"
    direction = props.get("Направление", {}).get("select", {})
    direction = direction.get("name", "Общее") if direction else "Общее"
    priority = props.get("Приоритет", {}).get("select", {})
    priority = priority.get("name", "") if priority else ""
    deadline = props.get("Дедлайн", {}).get("date", {})
    deadline = deadline.get("start", "") if deadline else ""
    priority_icon = "🔥" if "Срочно" in priority else "⚡" if "Важно" in priority else "📌"
    return {"name": name, "responsible": responsible, "direction": direction, "priority_icon": priority_icon, "deadline": deadline}


def format_tasks(tasks):
    """Форматируем список задач по направлениям"""
    if not tasks:
        return "✅ Нет задач в работе!"
    by_direction = {}
    for task in tasks:
        t = parse_task(task)
        if t["direction"] not in by_direction:
            by_direction[t["direction"]] = []
        by_direction[t["direction"]].append(t)
    direction_icons = {"АЭлит": "🏭", "Контент": "📱", "Фокус-группа": "🎓", "Общее": "📋"}
    lines = ["📋 *Задачи в работе*\n"]
    for direction, items in by_direction.items():
        icon = direction_icons.get(direction, "📁")
        lines.append(f"{icon} *{direction}*")
        for t in items:
            deadline_str = f" · {t['deadline']}" if t['deadline'] else ""
            lines.append(f"{t['priority_icon']} {t['name']}\n   👤 {t['responsible']}{deadline_str}")
        lines.append("")
    lines.append(f"_Всего: {len(tasks)}_")
    return "\n".join(lines)


def format_tasks_by_person(tasks, name):
    """Форматируем задачи конкретного человека"""
    if not tasks:
        return f"✅ У {name} нет задач в работе!"
    person_icons = {"Марго": "👩‍💼", "Галия": "👩‍💻", "Ольга": "👩‍📋", "Андрей": "👨‍🔧"}
    icon = person_icons.get(name, "👤")
    lines = [f"{icon} *Задачи {name}*\n"]
    for task in tasks:
        t = parse_task(task)
        deadline_str = f" · {t['deadline']}" if t['deadline'] else ""
        direction_icons = {"АЭлит": "🏭", "Контент": "📱", "Фокус-группа": "🎓", "Общее": "📋"}
        dir_icon = direction_icons.get(t["direction"], "📁")
        lines.append(f"{t['priority_icon']} {t['name']}\n   {dir_icon} {t['direction']}{deadline_str}")
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
        json={"properties": {"Статус": {"select": {"name": status}}, "Ответственный": {"select": {"name": responsible}}}},
        timeout=15
    )
    return r.status_code == 200


def create_task(name, responsible, direction="Общее", priority="📌 Обычное", status="В работе"):
    r = requests.post(
        "https://api.notion.com/v1/pages",
        headers={"Authorization": f"Bearer {NOTION_TOKEN}", "Notion-Version": "2022-06-28", "Content-Type": "application/json"},
        json={"parent": {"database_id": NOTION_DATABASE_ID}, "properties": {
            "Задача": {"title": [{"text": {"content": name}}]},
            "Статус": {"select": {"name": status}},
            "Ответственный": {"select": {"name": responsible}},
            "Направление": {"select": {"name": direction}},
            "Приоритет": {"select": {"name": priority}},
        }},
        timeout=15
    )
    return r.status_code == 200


async def cmd_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return
    try:
        await update.message.reply_text("⏳ Загружаю задачи...")
        tasks = get_tasks_in_progress()
        await update.message.reply_text(format_tasks(tasks), parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Ошибка /tasks: {e}")
        await update.message.reply_text("⚠️ Не удалось загрузить задачи.")


async def cmd_who(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /who Имя — задачи конкретного человека"""
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return
    if not context.args:
        await update.message.reply_text(
            "👤 Напиши имя после команды:\n"
            "`/who Ольга`\n\n"
            "Доступные имена: Марго, Галия, Ольга, Андрей",
            parse_mode="Markdown"
        )
        return
    name = context.args[0].strip().capitalize()
    valid_names = ["Марго", "Галия", "Ольга", "Андрей"]
    if name not in valid_names:
        await update.message.reply_text(
            f"❓ Не знаю такого человека. Доступные имена:\n{', '.join(valid_names)}"
        )
        return
    try:
        await update.message.reply_text(f"⏳ Загружаю задачи {name}...")
        tasks = get_tasks_by_person(name)
        await update.message.reply_text(format_tasks_by_person(tasks, name), parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Ошибка /who: {e}")
        await update.message.reply_text("⚠️ Не удалось загрузить задачи.")


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return
    if not context.args:
        await update.message.reply_text(
            "📝 Напиши задачу после команды:\n"
            "`/add Позвонить дизайнеру — Ольга — АЭлит — срочно`",
            parse_mode="Markdown"
        )
        return
    text = " ".join(context.args)
    sender = update.message.from_user.first_name or "Марго"
    try:
        result = ask_claude(f"Новая задача: {text}", sender)
        if result.get("type") == "new":
            name = result.get("task_name", text)
            responsible = result.get("responsible", sender)
            direction = result.get("direction", "Общее")
            priority = result.get("priority", "📌 Обычное")
            ok = create_task(name, responsible, direction, priority)
            if ok:
                await update.message.reply_text(
                    f"✅ Задача добавлена!\n\n📌 *{name}*\n👤 {responsible} · {direction} · {priority}",
                    parse_mode="Markdown"
                )
            else:
                await update.message.reply_text("⚠️ Не удалось добавить задачу.")
        else:
            await update.message.reply_text("⚠️ Не поняла задачу. Попробуй написать подробнее.")
    except Exception as e:
        logger.error(f"Ошибка /add: {e}")
        await update.message.reply_text("⚠️ Ошибка при добавлении задачи.")


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return
    msg = update.message
    if not msg or not msg.text:
        return
    sender = msg.from_user.first_name or "Неизвестный"
    try:
        result = ask_claude(msg.text, sender)
        msg_type = result.get("type")
        if msg_type == "skip":
            return
        elif msg_type == "done":
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
                create_task(name, responsible, status=status)
                await msg.reply_text(f"✅ Зафиксировано! Создала «{name}» → {status}")
        elif msg_type == "new":
            name = result.get("task_name", "")
            responsible = result.get("responsible", sender)
            direction = result.get("direction", "Общее")
            priority = result.get("priority", "📌 Обычное")
            ok = create_task(name, responsible, direction, priority)
            if ok:
                await msg.reply_text(
                    f"✅ Задача добавлена!\n\n📌 *{name}*\n👤 {responsible} · {direction} · {priority}",
                    parse_mode="Markdown"
                )
    except Exception as e:
        logger.error(f"Ошибка: {e}")


def main():
    threading.Thread(target=start_web, daemon=True).start()
    logger.info(f"Веб-сервер на порту {PORT}")
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("tasks", cmd_tasks))
    app.add_handler(CommandHandler("who", cmd_who))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
    logger.info("Бот запущен ✅")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
