import os
import logging
import hashlib
import requests
from datetime import datetime, date
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
import anthropic
import json

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
IIKO_SERVER = os.environ.get("IIKO_SERVER", "xan-kokand.iiko.it")
IIKO_LOGIN = os.environ.get("IIKO_LOGIN", "SUPERADMIN")
IIKO_PASSWORD = os.environ.get("IIKO_PASSWORD", "asdfghjkl")
ALLOWED_USERS = list(map(int, os.environ.get("ALLOWED_USERS", "7871931220,514275093,5028786313").split(",")))

anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

iiko_token = None
iiko_token_time = None


def get_iiko_token():
    global iiko_token, iiko_token_time
    now = datetime.now()
    if iiko_token and iiko_token_time and (now - iiko_token_time).seconds < 3600:
        return iiko_token
    pass_hash = hashlib.sha1(IIKO_PASSWORD.encode()).hexdigest()
    url = f"https://{IIKO_SERVER}/resto/api/auth?login={IIKO_LOGIN}&pass={pass_hash}"
    resp = requests.get(url, verify=False, timeout=10)
    resp.raise_for_status()
    iiko_token = resp.text.strip()
    iiko_token_time = now
    logger.info(f"iiko token получен: {iiko_token[:8]}...")
    return iiko_token


def iiko_olap(body: dict) -> dict:
    token = get_iiko_token()
    url = f"https://{IIKO_SERVER}/resto/api/v2/reports/olap?key={token}"
    resp = requests.post(url, json=body, verify=False, timeout=30)
    resp.raise_for_status()
    return resp.json()


def get_today_revenue() -> dict:
    today = date.today().isoformat()
    data = iiko_olap({
        "reportType": "SALES",
        "buildSummary": "true",
        "groupByRowFields": ["OpenDate.Typed"],
        "groupByColFields": [],
        "aggregateFields": ["DishSumInt", "DishDiscountSumInt", "GuestNum"],
        "filters": {
            "OpenDate.Typed": {
                "filterType": "DateRange",
                "periodType": "CUSTOM",
                "from": today,
                "to": today,
                "includeLow": "true",
                "includeHigh": "true"
            }
        }
    })
    if data.get("data"):
        r = data["data"][0]
        return {
            "date": today,
            "revenue": r.get("DishDiscountSumInt", 0),
            "gross": r.get("DishSumInt", 0),
            "guests": r.get("GuestNum", 0),
            "discount": r.get("DishSumInt", 0) - r.get("DishDiscountSumInt", 0)
        }
    return {"date": today, "revenue": 0, "gross": 0, "guests": 0, "discount": 0}


def get_revenue_by_date(from_date: str, to_date: str) -> dict:
    data = iiko_olap({
        "reportType": "SALES",
        "buildSummary": "true",
        "groupByRowFields": ["OpenDate.Typed"],
        "groupByColFields": [],
        "aggregateFields": ["DishSumInt", "DishDiscountSumInt", "GuestNum"],
        "filters": {
            "OpenDate.Typed": {
                "filterType": "DateRange",
                "periodType": "CUSTOM",
                "from": from_date,
                "to": to_date,
                "includeLow": "true",
                "includeHigh": "true"
            }
        }
    })
    result = []
    for r in data.get("data", []):
        result.append({
            "date": r.get("OpenDate.Typed"),
            "revenue": r.get("DishDiscountSumInt", 0),
            "gross": r.get("DishSumInt", 0),
            "guests": r.get("GuestNum", 0)
        })
    return {"rows": result}


def get_waiters_revenue(from_date: str = None, to_date: str = None) -> dict:
    today = date.today().isoformat()
    from_date = from_date or today
    to_date = to_date or today
    data = iiko_olap({
        "reportType": "SALES",
        "buildSummary": "true",
        "groupByRowFields": ["WaiterName"],
        "groupByColFields": [],
        "aggregateFields": ["DishSumInt", "DishDiscountSumInt", "GuestNum"],
        "filters": {
            "OpenDate.Typed": {
                "filterType": "DateRange",
                "periodType": "CUSTOM",
                "from": from_date,
                "to": to_date,
                "includeLow": "true",
                "includeHigh": "true"
            }
        }
    })
    waiters = []
    for r in data.get("data", []):
        waiters.append({
            "name": r.get("WaiterName", "Неизвестно"),
            "revenue": r.get("DishDiscountSumInt", 0),
            "guests": r.get("GuestNum", 0)
        })
    waiters.sort(key=lambda x: x["revenue"], reverse=True)
    return {"waiters": waiters, "date": from_date}


def get_payment_types(from_date: str = None, to_date: str = None) -> dict:
    today = date.today().isoformat()
    from_date = from_date or today
    to_date = to_date or today
    data = iiko_olap({
        "reportType": "SALES",
        "buildSummary": "true",
        "groupByRowFields": ["PayTypes"],
        "groupByColFields": [],
        "aggregateFields": ["DishDiscountSumInt"],
        "filters": {
            "OpenDate.Typed": {
                "filterType": "DateRange",
                "periodType": "CUSTOM",
                "from": from_date,
                "to": to_date,
                "includeLow": "true",
                "includeHigh": "true"
            }
        }
    })
    payments = []
    for r in data.get("data", []):
        payments.append({
            "type": r.get("PayTypes", "—"),
            "amount": r.get("DishDiscountSumInt", 0)
        })
    payments.sort(key=lambda x: x["amount"], reverse=True)
    return {"payments": payments, "date": from_date}


def get_deleted_dishes(from_date: str = None, to_date: str = None) -> dict:
    today = date.today().isoformat()
    from_date = from_date or today
    to_date = to_date or today
    data = iiko_olap({
        "reportType": "SALES",
        "buildSummary": "true",
        "groupByRowFields": ["DishName", "DeletedWithWriteoff"],
        "groupByColFields": [],
        "aggregateFields": ["DishAmountInt", "DishSumInt"],
        "filters": {
            "OpenDate.Typed": {
                "filterType": "DateRange",
                "periodType": "CUSTOM",
                "from": from_date,
                "to": to_date,
                "includeLow": "true",
                "includeHigh": "true"
            },
            "DeletedWithWriteoff": {
                "filterType": "IncludeValues",
                "values": ["DELETED_WITH_WRITEOFF", "DELETED_WITHOUT_WRITEOFF"]
            }
        }
    })
    dishes = []
    for r in data.get("data", []):
        dishes.append({
            "name": r.get("DishName", "—"),
            "amount": r.get("DishAmountInt", 0),
            "sum": r.get("DishSumInt", 0),
            "writeoff": r.get("DeletedWithWriteoff") == "DELETED_WITH_WRITEOFF"
        })
    return {"dishes": dishes, "date": from_date}


TOOLS = [
    {
        "name": "get_today_revenue",
        "description": "Получить выручку, количество гостей и сумму скидок за сегодня",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "get_revenue_by_date",
        "description": "Получить выручку за произвольный период",
        "input_schema": {
            "type": "object",
            "properties": {
                "from_date": {"type": "string", "description": "Дата начала в формате YYYY-MM-DD"},
                "to_date": {"type": "string", "description": "Дата конца в формате YYYY-MM-DD"}
            },
            "required": ["from_date", "to_date"]
        }
    },
    {
        "name": "get_waiters_revenue",
        "description": "Получить список официантов, их выручку и количество гостей",
        "input_schema": {
            "type": "object",
            "properties": {
                "from_date": {"type": "string", "description": "Дата начала YYYY-MM-DD (по умолчанию сегодня)"},
                "to_date": {"type": "string", "description": "Дата конца YYYY-MM-DD (по умолчанию сегодня)"}
            },
            "required": []
        }
    },
    {
        "name": "get_payment_types",
        "description": "Получить выручку по типам оплаты (наличные, Click, Payme и др.)",
        "input_schema": {
            "type": "object",
            "properties": {
                "from_date": {"type": "string", "description": "Дата начала YYYY-MM-DD (по умолчанию сегодня)"},
                "to_date": {"type": "string", "description": "Дата конца YYYY-MM-DD (по умолчанию сегодня)"}
            },
            "required": []
        }
    },
    {
        "name": "get_deleted_dishes",
        "description": "Получить список удалённых блюд из заказов",
        "input_schema": {
            "type": "object",
            "properties": {
                "from_date": {"type": "string", "description": "Дата начала YYYY-MM-DD (по умолчанию сегодня)"},
                "to_date": {"type": "string", "description": "Дата конца YYYY-MM-DD (по умолчанию сегодня)"}
            },
            "required": []
        }
    }
]

TOOL_FUNCTIONS = {
    "get_today_revenue": get_today_revenue,
    "get_revenue_by_date": get_revenue_by_date,
    "get_waiters_revenue": get_waiters_revenue,
    "get_payment_types": get_payment_types,
    "get_deleted_dishes": get_deleted_dishes,
}

SYSTEM_PROMPT = f"""Ты — умный ИИ-ассистент ресторана Xan Kokand. Ты помогаешь управляющим и владельцам получать данные из системы iiko.

Сегодняшняя дата: {date.today().isoformat()}

У тебя есть инструменты для получения данных из iiko. Когда пользователь задаёт вопрос о выручке, официантах, оплатах или удалённых блюдах — используй нужный инструмент.

Правила форматирования ответов:
- Используй эмодзи для наглядности
- Форматируй числа с разделителями (1 234 567 сум)
- Всегда указывай дату данных
- Отвечай на том языке, на котором пишет пользователь (русский, узбекский)
- Будь кратким и по делу
- Суммы всегда указывай в сумах (значения из iiko уже в сумах, не делить)"""


def fmt_num(n) -> str:
    return f"{int(n):,}".replace(",", " ")


async def process_message(text: str) -> str:
    messages = [{"role": "user", "content": text}]

    while True:
        response = anthropic_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages
        )

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    tool_name = block.name
                    tool_input = block.input
                    logger.info(f"Вызов инструмента: {tool_name} {tool_input}")
                    try:
                        result = TOOL_FUNCTIONS[tool_name](**tool_input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result, ensure_ascii=False)
                        })
                    except Exception as e:
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": f"Ошибка: {str(e)}",
                            "is_error": True
                        })

            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})

        else:
            for block in response.content:
                if hasattr(block, "text"):
                    return block.text
            return "Не удалось получить ответ."


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USERS:
        await update.message.reply_text("⛔ У вас нет доступа к этому боту.")
        return

    text = update.message.text or update.message.caption or ""
    if not text:
        await update.message.reply_text("Напишите ваш вопрос текстом.")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        answer = await process_message(text)
        await update.message.reply_text(answer, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎤 Голосовые сообщения пока не поддерживаются. Напишите текстом.")


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    logger.info("Бот запущен...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
