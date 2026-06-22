import os
import json
import logging
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

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
Когда кто-то пишет о выполнении задачи — определи:
1. Что за задача (кратко)
2. Кто выполнил
3. Статус: Готово
Если сообщение НЕ про выполнение — ответь: НЕТ_ЗАДАЧИ
Отвечай ТОЛЬКО JSON без markdown:
{"task_name": "название", "responsible": "имя", "status": "Готово"}
Или: {"action": "НЕТ_ЗАДАЧИ"}"""


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


def create_task(name, responsible, status):
    requests.post(
        "https://api.notion.com/v1/pages",
        headers={"Authorization": f"Bearer {NOTION_TOKEN}", "Notion-Version": "2022-06-28", "Content-Type": "application/json"},
        json={"parent": {"database_id": NOTION_DATABASE_ID}, "properties": {
            "Задача": {"title": [{"text": {"content": name}}]},
            "Статус": {"select": {"name": status}},
            "Ответственный": {"select": {"name": responsible}},
            "Направление": {"select": {"name": "Общее"}},
        }},
        timeout=15
    )


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return
    msg = update.message
    if not msg or not msg.text:
        return
    sender = msg.from_user.first_name or "Неизвестный"
    try:
        result = ask_claude(msg.text, sender)
        if result.get("action") == "НЕТ_ЗАДАЧИ":
            return
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
            create_task(name, responsible, status)
            await msg.reply_text(f"✅ Создала новую задачу «{name}» → {status}")
    except Exception as e:
        logger.error(f"Ошибка: {e}")


def main():
    threading.Thread(target=start_web, daemon=True).start()
    logger.info(f"Веб-сервер на порту {PORT}")
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
    logger.info("Бот запущен ✅")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
