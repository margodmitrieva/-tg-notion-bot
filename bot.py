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
NOTION_PERSONAL_DATABASE_ID = os.environ["NOTION_PERSONAL_DATABASE_ID"]
ALLOWED_CHAT_ID = int(os.environ["ALLOWED_CHAT_ID"])
PORT = int(os.environ.get("PORT", 8080))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты ассистент, который управляет задачами в Notion для Марго.

РАБОЧИЕ задачи:
- Команда: Марго, Галия, Ольга, Андрей
- Направления: АЭлит, Контент, Фокус-группа, Общее

ЛИЧНЫЕ задачи:
- Ответственный: Марго
- Разделы: Цели и приоритеты, Семья и быт, Обучение

Приоритеты: 🔥 Срочно, ⚡ Важно, 📌 Обычное

Определи тип сообщения и ответь ТОЛЬКО JSON без markdown:

1. Показать ВСЕ рабочие задачи (все задачи, покажи задачи, что в работе):
{"type": "show_all"}

2. Рабочие задачи конкретного ЧЕЛОВЕКА (задачи Ольги, что у Марго):
{"type": "show_person", "person": "имя"}

3. Рабочие задачи по НАПРАВЛЕНИЮ (задачи по АЭлит, что по Контенту):
{"type": "show_direction", "direction": "АЭлит или Контент или Фокус-группа или Общее"}

4. Рабочие задачи ЧЕЛОВЕКА по НАПРАВЛЕНИЮ (задачи Марго АЭлит, Ольга Контент):
{"type": "show_person_direction", "person": "имя", "direction": "направление"}

5. Показать ВСЕ личные задачи (мои задачи, личные задачи, что у меня):
{"type": "show_personal_all"}

6. Личные задачи по РАЗДЕЛУ (цели, семья, обучение, мои цели, моя семья):
{"type": "show_personal_section", "section": "Цели и приоритеты или Семья и быт или Обучение"}

7. ВЫПОЛНЕНИЕ рабочей задачи (выполнила, сделала, готово — про рабочее):
{"type": "done", "task_name": "название", "responsible": "имя", "status": "Готово"}

8. НОВАЯ рабочая задача (одна задача). Форматы: "добавь задачу: X", "задача: X", "добавь задачу:\nX — человек — направление", "нужно сделать X":
{"type": "new", "task_name": "название", "responsible": "имя или Марго", "direction": "направление", "priority": "приоритет", "deadline": "YYYY-MM-DD или null", "status": "В работе или Ждём или Идея или Готово"}

8б. НЕСКОЛЬКО НОВЫХ задач сразу (добавить задачи:, список задач, пронумерованный список):
{"type": "new_many", "tasks": [{"task_name": "название", "responsible": "имя", "direction": "направление", "priority": "приоритет", "deadline": "YYYY-MM-DD или null", "status": "В работе"}, ...]}

9. НОВАЯ личная задача (моя задача, добавь в цели, напомни купить, личное):
{"type": "new_personal", "task_name": "название", "section": "Цели и приоритеты или Семья и быт или Обучение", "priority": "приоритет"}

10. ИЗМЕНИТЬ НАПРАВЛЕНИЕ задачи (перенеси, измени направление, переместить в, сменить на):
{"type": "change_direction", "task_name": "название задачи", "new_direction": "АЭлит или Контент или Фокус-группа или Общее"}

11. Показать задачи по ПРИОРИТЕТУ (срочные, важные, обычные — можно с фильтром по человеку и/или направлению):
{"type": "show_priority", "priority": "🔥 Срочно или ⚡ Важно или 📌 Обычное", "person": "имя или null", "direction": "направление или null"}

12. ЗАКРЫТЬ НЕСКОЛЬКО задач сразу (закрыть:, выполнены:, готово: — перечисление через запятую):
{"type": "done_many", "tasks": ["название1", "название2", "название3"], "responsible": "имя или Марго"}

13. НЕ про задачи:
{"type": "skip"}"""


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
        json={"model": "claude-sonnet-4-6", "max_tokens": 300, "system": SYSTEM_PROMPT,
              "messages": [{"role": "user", "content": f"Отправитель: {sender}\nСообщение: {text}"}]},
        timeout=30
    )
    r.raise_for_status()
    return json.loads(r.json()["content"][0]["text"].strip())


def query_notion(database_id, body):
    r = requests.post(
        f"https://api.notion.com/v1/databases/{database_id}/query",
        headers={"Authorization": f"Bearer {NOTION_TOKEN}", "Notion-Version": "2022-06-28", "Content-Type": "application/json"},
        json=body, timeout=15
    )
    r.raise_for_status()
    return r.json().get("results", [])


# --- Рабочие задачи ---
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

# --- Личные задачи ---
def get_personal_all():
    return query_notion(NOTION_PERSONAL_DATABASE_ID, {
        "filter": {"property": "Статус", "select": {"equals": "В работе"}},
        "sorts": [{"property": "Раздел", "direction": "ascending"}, {"property": "Приоритет", "direction": "ascending"}]
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
    priority_icon = "🔥" if "Срочно" in priority else "⚡" if "Важно" in priority else "📌"
    return {"name": name, "responsible": responsible, "direction": direction, "priority_icon": priority_icon, "deadline": deadline}

def parse_personal_task(task):
    props = task["properties"]
    name = props.get("Задача", {}).get("title", [{}])
    name = name[0]["text"]["content"] if name else ""
    section = (props.get("Раздел", {}).get("select") or {}).get("name", "Общее")
    priority = (props.get("Приоритет", {}).get("select") or {}).get("name", "")
    deadline = (props.get("Дедлайн", {}).get("date") or {}).get("start", "")
    priority_icon = "🔥" if "Срочно" in priority else "⚡" if "Важно" in priority else "📌"
    return {"name": name, "section": section, "priority_icon": priority_icon, "deadline": deadline}


DIR_ICONS = {"АЭлит": "🏭", "Контент": "📱", "Фокус-группа": "🎓", "Общее": "📋"}
PERSON_ICONS = {"Марго": "👩‍💼", "Галия": "👩‍💻", "Ольга": "👩‍📋", "Андрей": "👨‍🔧"}
SECTION_ICONS = {"Цели и приоритеты": "🎯", "Семья и быт": "🧡", "Обучение": "📚"}


def format_all(tasks):
    if not tasks:
        return "✅ Нет рабочих задач в работе!"
    by_dir = {}
    for task in tasks:
        t = parse_work_task(task)
        by_dir.setdefault(t["direction"], []).append(t)
    lines = ["📋 *Все рабочие задачи*\n"]
    for direction, items in by_dir.items():
        lines.append(f"{DIR_ICONS.get(direction, '📁')} *{direction}*")
        for t in items:
            dl = f" · {t['deadline']}" if t['deadline'] else ""
            lines.append(f"{t['priority_icon']} {t['name']}\n   👤 {t['responsible']}{dl}")
        lines.append("")
    lines.append(f"_Всего: {len(tasks)}_")
    return "\n".join(lines)

def format_by_person(tasks, name):
    icon = PERSON_ICONS.get(name, "👤")
    if not tasks:
        return f"✅ У {name} нет задач в работе!"
    lines = [f"{icon} *Задачи — {name}*\n"]
    for task in tasks:
        t = parse_work_task(task)
        dl = f" · {t['deadline']}" if t['deadline'] else ""
        lines.append(f"{t['priority_icon']} {t['name']}\n   {DIR_ICONS.get(t['direction'], '📁')} {t['direction']}{dl}")
    lines.append(f"\n_Всего: {len(tasks)}_")
    return "\n".join(lines)

def format_by_direction(tasks, direction):
    icon = DIR_ICONS.get(direction, "📁")
    if not tasks:
        return f"✅ Нет задач по направлению {icon} {direction}!"
    lines = [f"{icon} *Задачи — {direction}*\n"]
    for task in tasks:
        t = parse_work_task(task)
        dl = f" · {t['deadline']}" if t['deadline'] else ""
        lines.append(f"{t['priority_icon']} {t['name']}\n   👤 {t['responsible']}{dl}")
    lines.append(f"\n_Всего: {len(tasks)}_")
    return "\n".join(lines)

def format_personal_all(tasks):
    if not tasks:
        return "✅ Нет личных задач в работе!"
    by_section = {}
    for task in tasks:
        t = parse_personal_task(task)
        by_section.setdefault(t["section"], []).append(t)
    lines = ["🌸 *Мои личные задачи*\n"]
    for section, items in by_section.items():
        lines.append(f"{SECTION_ICONS.get(section, '📌')} *{section}*")
        for t in items:
            dl = f" · {t['deadline']}" if t['deadline'] else ""
            lines.append(f"{t['priority_icon']} {t['name']}{dl}")
        lines.append("")
    lines.append(f"_Всего: {len(tasks)}_")
    return "\n".join(lines)

def format_personal_section(tasks, section):
    icon = SECTION_ICONS.get(section, "📌")
    if not tasks:
        return f"✅ Нет задач в разделе {icon} {section}!"
    lines = [f"{icon} *{section}*\n"]
    for task in tasks:
        t = parse_personal_task(task)
        dl = f" · {t['deadline']}" if t['deadline'] else ""
        lines.append(f"{t['priority_icon']} {t['name']}{dl}")
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

def create_work_task(name, responsible, direction="Общее", priority="📌 Обычное", status="В работе"):
    requests.post(
        "https://api.notion.com/v1/pages",
        headers={"Authorization": f"Bearer {NOTION_TOKEN}", "Notion-Version": "2022-06-28", "Content-Type": "application/json"},
        json={"parent": {"database_id": NOTION_DATABASE_ID}, "properties": {
            "Задача": {"title": [{"text": {"content": name}}]},
            "Статус": {"select": {"name": status}},
            "Ответственный": {"select": {"name": responsible}},
            "Направление": {"select": {"name": direction}},
            "Приоритет": {"select": {"name": priority}},
        }}, timeout=15
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
        }}, timeout=15
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
        t = result.get("type")

        if t == "skip":
            return

        elif t == "show_all":
            await msg.reply_text("⏳ Загружаю...")
            tasks = get_all_tasks()
            await msg.reply_text(format_all(tasks), parse_mode="Markdown")

        elif t == "show_person":
            name = result.get("person", "").strip().capitalize()
            if name not in ["Марго", "Галия", "Ольга", "Андрей"]:
                await msg.reply_text("❓ Не знаю такого человека. Доступные: Марго, Галия, Ольга, Андрей")
                return
            await msg.reply_text(f"⏳ Загружаю задачи {name}...")
            tasks = get_tasks_by_person(name)
            await msg.reply_text(format_by_person(tasks, name), parse_mode="Markdown")

        elif t == "show_direction":
            direction = result.get("direction", "").strip()
            if direction not in ["АЭлит", "Контент", "Фокус-группа", "Общее"]:
                await msg.reply_text("❓ Доступные направления: АЭлит, Контент, Фокус-группа, Общее")
                return
            await msg.reply_text(f"⏳ Загружаю задачи по {direction}...")
            tasks = get_tasks_by_direction(direction)
            await msg.reply_text(format_by_direction(tasks, direction), parse_mode="Markdown")

        elif t == "show_person_direction":
            name = result.get("person", "").strip().capitalize()
            direction = result.get("direction", "").strip()
            await msg.reply_text(f"⏳ Загружаю задачи {name} по {direction}...")
            tasks = get_tasks_by_person_direction(name, direction)
            icon = PERSON_ICONS.get(name, "👤")
            dir_icon = DIR_ICONS.get(direction, "📁")
            if not tasks:
                await msg.reply_text(f"✅ У {name} нет задач по направлению {direction}!")
            else:
                lines = [f"{icon} {dir_icon} *{name} — {direction}*\n"]
                for task in tasks:
                    t2 = parse_work_task(task)
                    dl = f" · {t2['deadline']}" if t2['deadline'] else ""
                    lines.append(f"{t2['priority_icon']} {t2['name']}{dl}")
                lines.append(f"\n_Всего: {len(tasks)}_")
                await msg.reply_text("\n".join(lines), parse_mode="Markdown")

        elif t == "show_personal_all":
            await msg.reply_text("⏳ Загружаю личные задачи...")
            tasks = get_personal_all()
            await msg.reply_text(format_personal_all(tasks), parse_mode="Markdown")

        elif t == "show_personal_section":
            section = result.get("section", "").strip()
            if section not in ["Цели и приоритеты", "Семья и быт", "Обучение"]:
                await msg.reply_text("❓ Доступные разделы: Цели и приоритеты, Семья и быт, Обучение")
                return
            await msg.reply_text(f"⏳ Загружаю...")
            tasks = get_personal_by_section(section)
            await msg.reply_text(format_personal_section(tasks, section), parse_mode="Markdown")

        elif t == "done_many":
            tasks_list = result.get("tasks", [])
            responsible = result.get("responsible", sender)
            if not tasks_list:
                await msg.reply_text("❓ Не поняла какие задачи закрыть. Напиши: «закрыть: задача1, задача2, задача3»")
                return
            await msg.reply_text(f"⏳ Закрываю {len(tasks_list)} задач...")
            done_list = []
            not_found_list = []
            for task_name in tasks_list:
                task_name = task_name.strip()
                page_id = find_task(task_name)
                if page_id:
                    ok = update_task(page_id, "Готово", responsible)
                    if ok:
                        done_list.append(task_name)
                    else:
                        not_found_list.append(task_name)
                else:
                    not_found_list.append(task_name)
            lines = []
            if done_list:
                lines.append(f"✅ *Закрыто ({len(done_list)}):*")
                for name in done_list:
                    lines.append(f"· {name}")
            if not_found_list:
                lines.append(f"\n❓ *Не нашла ({len(not_found_list)}):*")
                for name in not_found_list:
                    lines.append(f"· {name}")
                lines.append("_Проверь названия — возможно написаны иначе в Notion_")
            await msg.reply_text("\n".join(lines), parse_mode="Markdown")

        elif t == "show_priority":
            priority_map = {
                "🔥 Срочно": "🔥 Срочно",
                "⚡ Важно": "⚡ Важно",
                "📌 Обычное": "📌 Обычное"
            }
            priority = result.get("priority", "").strip()
            person = result.get("person")
            direction = result.get("direction")
            if not person or person == "null":
                person = None
            if not direction or direction == "null":
                direction = None

            if priority not in priority_map:
                await msg.reply_text("❓ Не поняла приоритет. Доступные: срочные, важные, обычные")
                return

            # Строим фильтр
            filters = [
                {"property": "Статус", "select": {"equals": "В работе"}},
                {"property": "Приоритет", "select": {"equals": priority}}
            ]
            if person:
                filters.append({"property": "Ответственный", "select": {"equals": person}})
            if direction:
                filters.append({"property": "Направление", "select": {"equals": direction}})

            await msg.reply_text("⏳ Загружаю...")
            tasks = query_notion(NOTION_DATABASE_ID, {
                "filter": {"and": filters},
                "sorts": [{"property": "Направление", "direction": "ascending"}]
            })

            # Формируем заголовок
            priority_icons = {"🔥 Срочно": "🔥", "⚡ Важно": "⚡", "📌 Обычное": "📌"}
            icon = priority_icons.get(priority, "📌")
            title_parts = [f"{icon} *{priority} задачи"]
            if person:
                title_parts.append(f" — {person}")
            if direction:
                title_parts.append(f" / {direction}")
            title_parts.append("*")
            title = "".join(title_parts)

            if not tasks:
                await msg.reply_text(f"✅ {''.join(title_parts[:-1])}* — нет задач!")
                return

            lines = [title, ""]
            for task in tasks:
                t2 = parse_work_task(task)
                dl = f" · {t2['deadline']}" if t2['deadline'] else ""
                person_str = f"\n   👤 {t2['responsible']}" if not person else ""
                dir_str = f" · {DIR_ICONS.get(t2['direction'], '📁')} {t2['direction']}" if not direction else ""
                lines.append(f"{icon} {t2['name']}{person_str}{dir_str}{dl}")
            lines.append(f"\n_Всего: {len(tasks)}_")
            await msg.reply_text("\n".join(lines), parse_mode="Markdown")

        elif t == "change_direction":
            task_name = result.get("task_name", "")
            new_direction = result.get("new_direction", "")
            valid_dirs = ["АЭлит", "Контент", "Фокус-группа", "Общее"]
            if new_direction not in valid_dirs:
                await msg.reply_text(f"❓ Не знаю такого направления. Доступные: {', '.join(valid_dirs)}")
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
                    dir_icon = DIR_ICONS.get(new_direction, "📁")
                    await msg.reply_text(f"✅ Готово! Задача «{task_name}» перенесена в {dir_icon} {new_direction}")
                else:
                    await msg.reply_text("⚠️ Не смогла обновить направление.")
            else:
                await msg.reply_text(f"❓ Не нашла задачу «{task_name}». Проверь название.")

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
                await msg.reply_text(f"✅ Зафиксировано! Создала «{name}» → {status}")

        elif t == "new_many":
            tasks_list = result.get("tasks", [])
            if not tasks_list:
                await msg.reply_text("❓ Не поняла задачи. Напиши каждую с новой строки или пронумеруй.")
                return
            await msg.reply_text(f"⏳ Добавляю {len(tasks_list)} задач...")
            added = []
            for item in tasks_list:
                name = item.get("task_name", "").strip()
                if not name:
                    continue
                responsible = item.get("responsible", sender)
                direction = item.get("direction", "Общее")
                priority = item.get("priority", "📌 Обычное")
                deadline = item.get("deadline")
                status = item.get("status", "В работе")
                if deadline == "null":
                    deadline = None
                valid_statuses = ["В работе", "Ждём", "Идея", "Готово", "Отменено"]
                if status not in valid_statuses:
                    status = "В работе"
                create_work_task(name, responsible, direction, priority, status=status, deadline=deadline)
                dl_str = f" · 📅 {deadline}" if deadline else ""
                added.append(f"📌 *{name}*\n   👤 {responsible} · {direction}{dl_str}")
            lines = [f"✅ Добавлено задач: {len(added)}\n"]
            lines.extend(added)
            await msg.reply_text("\n\n".join(lines), parse_mode="Markdown")

        elif t == "new":
            name = result.get("task_name", "")
            responsible = result.get("responsible", sender)
            direction = result.get("direction", "Общее")
            priority = result.get("priority", "📌 Обычное")
            deadline = result.get("deadline")
            status = result.get("status", "В работе")
            valid_statuses = ["В работе", "Ждём", "Идея", "Готово", "Отменено"]
            if status not in valid_statuses:
                status = "В работе"
            if deadline == "null":
                deadline = None
            create_work_task(name, responsible, direction, priority, status=status, deadline=deadline)
            dl_str = f" · 📅 {deadline}" if deadline else ""
            status_icons = {"В работе": "🔵", "Ждём": "🟡", "Идея": "⚪", "Готово": "✅", "Отменено": "❌"}
            st_icon = status_icons.get(status, "🔵")
            await msg.reply_text(
                f"✅ Задача добавлена!\n\n📌 *{name}*\n👤 {responsible} · {direction} · {priority}\n{st_icon} {status}{dl_str}",
                parse_mode="Markdown"
            )

        elif t == "new_personal":
            name = result.get("task_name", "")
            section = result.get("section", "Цели и приоритеты")
            priority = result.get("priority", "📌 Обычное")
            create_personal_task(name, section, priority)
            icon = SECTION_ICONS.get(section, "📌")
            await msg.reply_text(
                f"✅ Личная задача добавлена!\n\n{icon} *{name}*\n{section} · {priority}",
                parse_mode="Markdown"
            )

    except Exception as e:
        logger.error(f"Ошибка: {e}")


def main():
    threading.Thread(target=start_web, daemon=True).start()
    logger.info(f"Веб-сервер на порту {PORT}")
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT, handle))
    logger.info("Бот запущен ✅")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
