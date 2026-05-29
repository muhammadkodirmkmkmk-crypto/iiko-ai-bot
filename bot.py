import os
import logging
import hashlib
import requests
import json
import base64
from datetime import datetime, date, timedelta
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
import anthropic

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
    if iiko_token and iiko_token_time and (now - iiko_token_time).seconds < 3500:
        return iiko_token
    pass_hash = hashlib.sha1(IIKO_PASSWORD.encode()).hexdigest()
    url = f"https://{IIKO_SERVER}/resto/api/auth?login={IIKO_LOGIN}&pass={pass_hash}"
    resp = requests.get(url, verify=False, timeout=10)
    resp.raise_for_status()
    iiko_token = resp.text.strip()
    iiko_token_time = now
    return iiko_token


def iiko_get(path: str, params: dict = None) -> any:
    token = get_iiko_token()
    p = params or {}
    p["key"] = token
    resp = requests.get(f"https://{IIKO_SERVER}/resto/api/{path}", params=p, verify=False, timeout=30)
    resp.raise_for_status()
    try:
        return resp.json()
    except:
        return resp.text


def iiko_olap(body: dict) -> dict:
    token = get_iiko_token()
    resp = requests.post(f"https://{IIKO_SERVER}/resto/api/v2/reports/olap?key={token}", json=body, verify=False, timeout=30)
    resp.raise_for_status()
    return resp.json()


def today() -> str:
    return date.today().isoformat()


def date_filter(from_date: str, to_date: str) -> dict:
    return {
        "OpenDate.Typed": {
            "filterType": "DateRange", "periodType": "CUSTOM",
            "from": from_date, "to": to_date,
            "includeLow": "true", "includeHigh": "true"
        }
    }


# ===================== ИНСТРУМЕНТЫ =====================

def get_revenue(from_date: str = None, to_date: str = None) -> dict:
    """Выручка за период"""
    fd = from_date or today()
    td = to_date or today()
    data = iiko_olap({
        "reportType": "SALES", "buildSummary": "true",
        "groupByRowFields": ["OpenDate.Typed"], "groupByColFields": [],
        "aggregateFields": ["DishSumInt", "DishDiscountSumInt", "GuestNum"],
        "filters": date_filter(fd, td)
    })
    rows = []
    for r in data.get("data", []):
        rows.append({
            "date": r.get("OpenDate.Typed"),
            "revenue": r.get("DishDiscountSumInt", 0),
            "gross": r.get("DishSumInt", 0),
            "guests": r.get("GuestNum", 0),
            "discount": r.get("DishSumInt", 0) - r.get("DishDiscountSumInt", 0)
        })
    total = data.get("summary", [[]])
    total_sum = 0
    total_guests = 0
    if total and len(total) > 1:
        for item in total[1]:
            if isinstance(item, dict):
                total_sum = item.get("DishDiscountSumInt", 0)
                total_guests = item.get("GuestNum", 0)
    return {"rows": rows, "total_revenue": total_sum, "total_guests": total_guests, "from": fd, "to": td}


def get_waiters(from_date: str = None, to_date: str = None) -> dict:
    """Выручка по официантам"""
    fd = from_date or today()
    td = to_date or today()
    data = iiko_olap({
        "reportType": "SALES", "buildSummary": "true",
        "groupByRowFields": ["WaiterName"], "groupByColFields": [],
        "aggregateFields": ["DishSumInt", "DishDiscountSumInt", "GuestNum"],
        "filters": date_filter(fd, td)
    })
    waiters = []
    for r in data.get("data", []):
        waiters.append({
            "name": r.get("WaiterName", "—"),
            "revenue": r.get("DishDiscountSumInt", 0),
            "gross": r.get("DishSumInt", 0),
            "guests": r.get("GuestNum", 0)
        })
    waiters.sort(key=lambda x: x["revenue"], reverse=True)
    return {"waiters": waiters, "date": fd}


def get_payment_types(from_date: str = None, to_date: str = None) -> dict:
    """Типы оплаты"""
    fd = from_date or today()
    td = to_date or today()
    data = iiko_olap({
        "reportType": "SALES", "buildSummary": "true",
        "groupByRowFields": ["PayTypes"], "groupByColFields": [],
        "aggregateFields": ["DishDiscountSumInt"],
        "filters": date_filter(fd, td)
    })
    payments = [{"type": r.get("PayTypes", "—"), "amount": r.get("DishDiscountSumInt", 0)} for r in data.get("data", [])]
    payments.sort(key=lambda x: x["amount"], reverse=True)
    return {"payments": payments, "date": fd}


def get_deleted_dishes(from_date: str = None, to_date: str = None) -> dict:
    """Удалённые блюда"""
    fd = from_date or today()
    td = to_date or today()
    data = iiko_olap({
        "reportType": "SALES", "buildSummary": "true",
        "groupByRowFields": ["DishName", "DeletedWithWriteoff", "WaiterName"], "groupByColFields": [],
        "aggregateFields": ["DishAmountInt", "DishSumInt"],
        "filters": {
            **date_filter(fd, td),
            "DeletedWithWriteoff": {"filterType": "IncludeValues", "values": ["DELETED_WITH_WRITEOFF", "DELETED_WITHOUT_WRITEOFF"]}
        }
    })
    dishes = [{"name": r.get("DishName","—"), "amount": r.get("DishAmountInt",0), "sum": r.get("DishSumInt",0), "waiter": r.get("WaiterName","—"), "writeoff": r.get("DeletedWithWriteoff") == "DELETED_WITH_WRITEOFF"} for r in data.get("data", [])]
    return {"dishes": dishes, "total": len(dishes), "date": fd}


def get_top_dishes(from_date: str = None, to_date: str = None, limit: int = 10) -> dict:
    """Топ блюд по продажам"""
    fd = from_date or today()
    td = to_date or today()
    data = iiko_olap({
        "reportType": "SALES", "buildSummary": "false",
        "groupByRowFields": ["DishName", "DishCategory"], "groupByColFields": [],
        "aggregateFields": ["DishAmountInt", "DishDiscountSumInt"],
        "filters": {
            **date_filter(fd, td),
            "DeletedWithWriteoff": {"filterType": "IncludeValues", "values": ["NOT_DELETED"]}
        }
    })
    dishes = [{"name": r.get("DishName","—"), "category": r.get("DishCategory","—"), "amount": r.get("DishAmountInt",0), "sum": r.get("DishDiscountSumInt",0)} for r in data.get("data", [])]
    dishes.sort(key=lambda x: x["amount"], reverse=True)
    return {"dishes": dishes[:limit], "date": fd}


def get_hourly_revenue(from_date: str = None) -> dict:
    """Почасовая выручка"""
    fd = from_date or today()
    data = iiko_olap({
        "reportType": "SALES", "buildSummary": "false",
        "groupByRowFields": ["CloseHour"], "groupByColFields": [],
        "aggregateFields": ["DishDiscountSumInt", "GuestNum"],
        "filters": {
            **date_filter(fd, fd),
            "DeletedWithWriteoff": {"filterType": "IncludeValues", "values": ["NOT_DELETED"]}
        }
    })
    hours = [{"hour": r.get("CloseHour","—"), "revenue": r.get("DishDiscountSumInt",0), "guests": r.get("GuestNum",0)} for r in data.get("data", [])]
    hours.sort(key=lambda x: str(x["hour"]))
    return {"hours": hours, "date": fd}


def get_category_revenue(from_date: str = None, to_date: str = None) -> dict:
    """Выручка по категориям блюд"""
    fd = from_date or today()
    td = to_date or today()
    data = iiko_olap({
        "reportType": "SALES", "buildSummary": "false",
        "groupByRowFields": ["DishCategory"], "groupByColFields": [],
        "aggregateFields": ["DishAmountInt", "DishDiscountSumInt"],
        "filters": {
            **date_filter(fd, td),
            "DeletedWithWriteoff": {"filterType": "IncludeValues", "values": ["NOT_DELETED"]}
        }
    })
    cats = [{"category": r.get("DishCategory","—"), "amount": r.get("DishAmountInt",0), "sum": r.get("DishDiscountSumInt",0)} for r in data.get("data", [])]
    cats.sort(key=lambda x: x["sum"], reverse=True)
    return {"categories": cats, "date": fd}


def get_discounts(from_date: str = None, to_date: str = None) -> dict:
    """Скидки и надбавки"""
    fd = from_date or today()
    td = to_date or today()
    data = iiko_olap({
        "reportType": "SALES", "buildSummary": "true",
        "groupByRowFields": ["DiscountType", "WaiterName"], "groupByColFields": [],
        "aggregateFields": ["DiscountSum", "DishDiscountSumInt"],
        "filters": date_filter(fd, td)
    })
    discounts = [{"type": r.get("DiscountType","—"), "waiter": r.get("WaiterName","—"), "discount_sum": r.get("DiscountSum",0)} for r in data.get("data", []) if r.get("DiscountSum",0) != 0]
    return {"discounts": discounts, "date": fd}


def get_cash_shifts(from_date: str = None, to_date: str = None) -> dict:
    """Кассовые смены"""
    fd = from_date or today()
    td = to_date or today()
    try:
        data = iiko_get("v2/cashshifts/list", {"from": fd, "to": td, "status": "ANY"})
        if isinstance(data, list):
            shifts = []
            for s in data:
                shifts.append({
                    "id": s.get("id"),
                    "open_date": s.get("openDate"),
                    "close_date": s.get("closeDate"),
                    "status": s.get("status"),
                    "cash_income": s.get("cashIncome", 0),
                    "cash_outcome": s.get("cashOutcome", 0),
                    "waiter": s.get("waiter", {}).get("name","—") if isinstance(s.get("waiter"), dict) else "—"
                })
            return {"shifts": shifts, "count": len(shifts), "date": fd}
        return {"error": str(data), "date": fd}
    except Exception as e:
        return {"error": str(e), "date": fd}


def get_stop_list() -> dict:
    """Стоп-лист (блюда которых нет)"""
    try:
        data = iiko_get("stoplist")
        items = []
        if isinstance(data, dict):
            for item in data.get("stopListItems", []):
                items.append({
                    "name": item.get("product", {}).get("name","—"),
                    "balance": item.get("balance", 0)
                })
        return {"items": items, "count": len(items)}
    except Exception as e:
        return {"error": str(e)}


def get_active_orders() -> dict:
    """Активные (открытые) заказы прямо сейчас"""
    try:
        data = iiko_get("orders/list", {"includeDeleted": "false", "includeClosed": "false"})
        orders = []
        if isinstance(data, list):
            for o in data[:20]:
                orders.append({
                    "number": o.get("number"),
                    "table": o.get("table", {}).get("name","—") if isinstance(o.get("table"), dict) else "—",
                    "waiter": o.get("waiter", {}).get("name","—") if isinstance(o.get("waiter"), dict) else "—",
                    "sum": o.get("sum", 0),
                    "guests": o.get("guestsCount", 0)
                })
        return {"orders": orders, "count": len(orders)}
    except Exception as e:
        return {"error": str(e)}


def get_product_balance(product_name: str = None) -> dict:
    """Остатки на складе"""
    try:
        data = iiko_get("v2/reports/olap", {})
        # Используем OLAP для складских остатков
        token = get_iiko_token()
        body = {
            "reportType": "STORAGES", "buildSummary": "false",
            "groupByRowFields": ["Product.Name", "Store.Name"], "groupByColFields": [],
            "aggregateFields": ["Amount", "SumPrice"],
            "filters": {}
        }
        if product_name:
            body["filters"]["Product.Name"] = {"filterType": "IncludeValues", "values": [product_name]}
        resp = requests.post(f"https://{IIKO_SERVER}/resto/api/v2/reports/olap?key={token}", json=body, verify=False, timeout=30)
        data = resp.json()
        items = [{"product": r.get("Product.Name","—"), "store": r.get("Store.Name","—"), "amount": r.get("Amount",0), "sum": r.get("SumPrice",0)} for r in data.get("data", [])]
        items.sort(key=lambda x: x["product"])
        return {"items": items[:50], "count": len(items)}
    except Exception as e:
        return {"error": str(e)}


def get_employees() -> dict:
    """Список сотрудников на смене"""
    try:
        data = iiko_get("employees", {"washighlyqualified": "false", "dontlimitrecords": "true"})
        employees = []
        if isinstance(data, list):
            for e in data:
                employees.append({
                    "name": e.get("firstName","") + " " + e.get("lastName",""),
                    "role": e.get("mainRole", {}).get("name","—") if isinstance(e.get("mainRole"), dict) else "—",
                    "code": e.get("code","—")
                })
        return {"employees": employees, "count": len(employees)}
    except Exception as e:
        return {"error": str(e)}


def get_writeoffs(from_date: str = None, to_date: str = None) -> dict:
    """Списания со склада"""
    fd = from_date or today()
    td = to_date or today()
    try:
        token = get_iiko_token()
        body = {
            "reportType": "STORAGES", "buildSummary": "true",
            "groupByRowFields": ["Product.Name", "Store.Name", "Document.Date"],
            "groupByColFields": [],
            "aggregateFields": ["Amount", "SumPrice"],
            "filters": {
                "Document.Date": {"filterType": "DateRange", "periodType": "CUSTOM", "from": fd, "to": td, "includeLow": "true", "includeHigh": "true"},
                "Document.Type": {"filterType": "IncludeValues", "values": ["WRITE_OFF"]}
            }
        }
        resp = requests.post(f"https://{IIKO_SERVER}/resto/api/v2/reports/olap?key={token}", json=body, verify=False, timeout=30)
        data = resp.json()
        items = [{"product": r.get("Product.Name","—"), "store": r.get("Store.Name","—"), "date": r.get("Document.Date","—"), "amount": r.get("Amount",0), "sum": r.get("SumPrice",0)} for r in data.get("data", [])]
        return {"items": items, "count": len(items), "date": fd}
    except Exception as e:
        return {"error": str(e)}


def get_incoming_invoices(from_date: str = None, to_date: str = None) -> dict:
    """Приходные накладные (поступление товаров)"""
    fd = from_date or today()
    td = to_date or today()
    try:
        token = get_iiko_token()
        body = {
            "reportType": "STORAGES", "buildSummary": "true",
            "groupByRowFields": ["Product.Name", "Store.Name", "Document.Date", "Supplier.Name"],
            "groupByColFields": [],
            "aggregateFields": ["Amount", "SumPrice"],
            "filters": {
                "Document.Date": {"filterType": "DateRange", "periodType": "CUSTOM", "from": fd, "to": td, "includeLow": "true", "includeHigh": "true"},
                "Document.Type": {"filterType": "IncludeValues", "values": ["INCOMING_INVOICE"]}
            }
        }
        resp = requests.post(f"https://{IIKO_SERVER}/resto/api/v2/reports/olap?key={token}", json=body, verify=False, timeout=30)
        data = resp.json()
        items = [{"product": r.get("Product.Name","—"), "store": r.get("Store.Name","—"), "supplier": r.get("Supplier.Name","—"), "date": r.get("Document.Date","—"), "amount": r.get("Amount",0), "sum": r.get("SumPrice",0)} for r in data.get("data", [])]
        return {"items": items, "count": len(items), "date": fd}
    except Exception as e:
        return {"error": str(e)}


# ===================== TOOLS ДЛЯ CLAUDE =====================

TOOLS = [
    {"name": "get_revenue", "description": "Выручка, количество гостей, скидки за период. Используй для вопросов о выручке, обороте, деньгах.", "input_schema": {"type": "object", "properties": {"from_date": {"type": "string", "description": "Дата начала YYYY-MM-DD"}, "to_date": {"type": "string", "description": "Дата конца YYYY-MM-DD"}}, "required": []}},
    {"name": "get_waiters", "description": "Список официантов, их выручка и количество гостей.", "input_schema": {"type": "object", "properties": {"from_date": {"type": "string"}, "to_date": {"type": "string"}}, "required": []}},
    {"name": "get_payment_types", "description": "Выручка по типам оплаты: наличные, Click, Payme, банковские карты.", "input_schema": {"type": "object", "properties": {"from_date": {"type": "string"}, "to_date": {"type": "string"}}, "required": []}},
    {"name": "get_deleted_dishes", "description": "Удалённые блюда из заказов — кто удалил и на какую сумму.", "input_schema": {"type": "object", "properties": {"from_date": {"type": "string"}, "to_date": {"type": "string"}}, "required": []}},
    {"name": "get_top_dishes", "description": "Топ продаваемых блюд по количеству и сумме.", "input_schema": {"type": "object", "properties": {"from_date": {"type": "string"}, "to_date": {"type": "string"}, "limit": {"type": "integer", "description": "Сколько блюд показать, по умолчанию 10"}}, "required": []}},
    {"name": "get_hourly_revenue", "description": "Почасовая выручка — в какое время больше всего гостей и продаж.", "input_schema": {"type": "object", "properties": {"from_date": {"type": "string"}}, "required": []}},
    {"name": "get_category_revenue", "description": "Выручка по категориям блюд (еда, напитки, десерты и др.).", "input_schema": {"type": "object", "properties": {"from_date": {"type": "string"}, "to_date": {"type": "string"}}, "required": []}},
    {"name": "get_discounts", "description": "Скидки и надбавки — кто давал скидки и на какую сумму.", "input_schema": {"type": "object", "properties": {"from_date": {"type": "string"}, "to_date": {"type": "string"}}, "required": []}},
    {"name": "get_cash_shifts", "description": "Кассовые смены — открытые и закрытые смены, приход/расход наличных.", "input_schema": {"type": "object", "properties": {"from_date": {"type": "string"}, "to_date": {"type": "string"}}, "required": []}},
    {"name": "get_stop_list", "description": "Стоп-лист — какие блюда сейчас недоступны.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_active_orders", "description": "Текущие открытые заказы — сколько столов сейчас занято.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_product_balance", "description": "Остатки товаров на складе.", "input_schema": {"type": "object", "properties": {"product_name": {"type": "string", "description": "Название товара для поиска (необязательно)"}}, "required": []}},
    {"name": "get_employees", "description": "Список сотрудников ресторана.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_writeoffs", "description": "Списания со склада — что и сколько было списано.", "input_schema": {"type": "object", "properties": {"from_date": {"type": "string"}, "to_date": {"type": "string"}}, "required": []}},
    {"name": "get_incoming_invoices", "description": "Приходные накладные — какие товары поступили в ресторан от поставщиков.", "input_schema": {"type": "object", "properties": {"from_date": {"type": "string"}, "to_date": {"type": "string"}}, "required": []}}
]

TOOL_FUNCTIONS = {
    "get_revenue": get_revenue,
    "get_waiters": get_waiters,
    "get_payment_types": get_payment_types,
    "get_deleted_dishes": get_deleted_dishes,
    "get_top_dishes": get_top_dishes,
    "get_hourly_revenue": get_hourly_revenue,
    "get_category_revenue": get_category_revenue,
    "get_discounts": get_discounts,
    "get_cash_shifts": get_cash_shifts,
    "get_stop_list": get_stop_list,
    "get_active_orders": get_active_orders,
    "get_product_balance": get_product_balance,
    "get_employees": get_employees,
    "get_writeoffs": get_writeoffs,
    "get_incoming_invoices": get_incoming_invoices,
}

SYSTEM_PROMPT = f"""Ты — умный ИИ-ассистент ресторана Xan Kokand. Ты помогаешь управляющим и владельцам получать любые данные из системы iiko.

Сегодняшняя дата: {date.today().isoformat()}

ВАЖНО: У тебя есть доступ ко ВСЕМ данным ресторана через iiko API. Всегда используй инструменты для получения реальных данных. Никогда не говори "у меня нет доступа" — сначала попробуй вызвать инструмент.

Если нужно несколько данных сразу — вызывай несколько инструментов последовательно.

Правила форматирования:
- Используй эмодзи для наглядности
- Числа форматируй: 1 234 567 сум
- Всегда указывай дату данных
- Отвечай на языке пользователя (русский или узбекский)
- Суммы в сумах (не делить на 100)
- Будь конкретным и информативным"""


async def process_message(text: str) -> str:
    messages = [{"role": "user", "content": text}]
    for _ in range(10):
        response = anthropic_client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages
        )
        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    logger.info(f"Инструмент: {block.name} {block.input}")
                    try:
                        result = TOOL_FUNCTIONS[block.name](**block.input)
                        tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(result, ensure_ascii=False)})
                    except Exception as e:
                        tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": f"Ошибка: {str(e)}", "is_error": True})
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})
        else:
            for block in response.content:
                if hasattr(block, "text"):
                    return block.text
            return "Не удалось получить ответ."
    return "Превышено количество попыток."


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USERS:
        await update.message.reply_text("⛔ У вас нет доступа.")
        return
    text = update.message.text or ""
    if not text:
        return
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    try:
        answer = await process_message(text)
        await update.message.reply_text(answer, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USERS:
        await update.message.reply_text("⛔ У вас нет доступа.")
        return
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    try:
        tg_file = await context.bot.get_file(update.message.voice.file_id)
        resp = requests.get(tg_file.file_path, timeout=30)
        resp.raise_for_status()
        audio_b64 = base64.b64encode(resp.content).decode()
        transcribe = anthropic_client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=500,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": "Транскрибируй это голосовое сообщение. Верни только текст без пояснений."},
                {"type": "document", "source": {"type": "base64", "media_type": "audio/ogg", "data": audio_b64}}
            ]}]
        )
        recognized = transcribe.content[0].text.strip()
        logger.info(f"Голос: {recognized}")
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        answer = await process_message(recognized)
        await update.message.reply_text(f"🎤 _{recognized}_\n\n{answer}", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Голос ошибка: {e}")
        await update.message.reply_text("❌ Не удалось распознать голос. Напишите текстом.")


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    logger.info("Бот запущен!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
