import os
import json
import logging
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# Настройки из переменных окружения
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
Направления задач: АЭлит, Контент, Фокус-группа, Общее.

Когда кто-то из команды пишет о выполнении задачи — определи:
1. Что за задача выполнена (кратко)
2. Кто выполнил (имя из команды)
3. Статус: "Готово"

Если сообщение НЕ про выполнение задачи — ответь: НЕТ_ЗАДАЧИ

Отвечай ТОЛЬКО в формате JSON (без markdown, без ```):
{"task_name": "название задачи", "responsible": "имя", "status": "Готово"}

Или если не задача:
{"action": "НЕТ_ЗАДАЧИ"}"""


# --- Маленький веб-сервер чтобы Render не засыпал ---
class KeepAlive(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")
    def log_message(self, *args):
        pass  # Не засорять логи

def start_web_server():
    server = HTTPServer(("0.0.0.0", PORT), KeepAlive)
    server.serve_forever()


# --- Claude ---
def ask_claude(message_text: str, sender_name: str) -> dict:
    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-sonnet-4-6",
            "max_tokens": 300,
            "system": SYSTEM_PROMPT,
            "messages": [
                {
                    "role": "user",
                    "content": f"Отправитель: {sender_name}\nСообщение: {message_text}",
                }
            ],
        },
    )
    response.raise_for_status()
    text = response.json()["content"][0]["text"].strip()
    return json.loads(text)


# --- Notion ---
def find_notion_task(task_name: str) -> str | None:
    response = requests.post(
        f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
        headers={
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        },
        json={
            "filter": {
                "property": "Задача",
                "title": {"contains": task_name[:20]},
            }
        },
    )
    response.raise_for_status()
    results = response.json().get("results", [])
    return results[0]["id"] if results else None


def update_notion_task(page_id: str, status: str, responsible: str) -> bool:
    response = requests.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers={
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        },
        json={
            "properties": {
                "Статус": {"select": {"name": status}},
                "Ответственный": {"select": {"name": responsible}},
            }
        },
    )
    return response.status_code == 200


def create_notion_task(task_name: str, responsible: str, status: str):
    requests.post(
        "https://api.notion.com/v1/pages",
        headers={
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        },
        json={
            "parent": {"database_id": NOTION_DATABASE_ID},
            "properties": {
                "Задача": {"title": [{"text": {"content": task_name}}]},
                "Статус": {"select": {"name": status}},
                "Ответственный": {"select": {"name": responsible}},
                "Направление": {"select": {"name": "Общее"}},
            },
        },
    )


# --- Telegram ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return

    message = update.message
    if not message or not message.text:
        return

    sender = message.from_user.first_name or "Неизвестный"
    text = message.text
    logger.info(f"Сообщение от {sender}: {text}")

    try:
        result = ask_claude(text, sender)

        if result.get("action") == "НЕТ_ЗАДАЧИ":
            return

        task_name = result.get("task_name", "")
        responsible = result.get("responsible", sender)
        status = result.get("status", "Готово")

        page_id = find_notion_task(task_name)

        if page_id:
            success = update_notion_task(page_id, status, responsible)
            if success:
                await message.reply_text(f"✅ Зафиксировано! Задача «{task_name}» → {status}")
            else:
                await message.reply_text("⚠️ Нашла задачу, но не смогла обновить. Проверь Notion.")
        else:
            create_notion_task(task_name, responsible, status)
            await message.reply_text(f"✅ Зафиксировано! Создала новую задачу «{task_name}» → {status}")

    except Exception as e:
        logger.error(f"Ошибка: {e}")


def main():
    # Запускаем веб-сервер в отдельном потоке
    thread = threading.Thread(target=start_web_server, daemon=True)
    thread.start()
    logger.info(f"Веб-сервер запущен на порту {PORT}")

    # Запускаем бота
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Бот запущен ✅")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
