import os
import logging
import hashlib
import requests
import json
import base64
import tempfile
from datetime import datetime, date
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
import anthropic

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
IIKO_SERVER = os.environ.get("IIKO_SERVER", "xan-kokand.iiko.it")
IIKO_LOGIN = os.environ.get("IIKO_LOGIN", "SUPERADMIN")
IIKO_PASSWORD = os.environ.get("IIKO_PASSWORD", "asdfghjkl")
ALLOWED_USERS = list(map(int, os.environ.get("ALLOWED_USERS", "7871931220,514275093,5028786313,182606553").split(",")))

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


def transcribe_voice(audio_bytes: bytes) -> str:
    """Распознавание голоса через OpenAI Whisper"""
    if not OPENAI_API_KEY:
        raise Exception("OPENAI_API_KEY не задан")
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
        f.write(audio_bytes)
        tmp_path = f.name
    try:
        with open(tmp_path, "rb") as audio_file:
            resp = requests.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                files={"file": ("voice.ogg", audio_file, "audio/ogg")},
                data={"model": "whisper-1", "language": "ru"},
                timeout=30
            )
            resp.raise_for_status()
            return resp.json().get("text", "").strip()
    finally:
        os.unlink(tmp_path)


# ===================== ИНСТРУМЕНТЫ =====================

def get_revenue(from_date: str = None, to_date: str = None) -> dict:
    fd = from_date or today()
    td = to_date or today()
    data = iiko_olap({
        "reportType": "SALES", "buildSummary": "true",
        "groupByRowFields": ["OpenDate.Typed"], "groupByColFields": [],
        "aggregateFields": ["DishSumInt", "DishDiscountSumInt", "GuestNum"],
        "filters": date_filter(fd, td)
    })
    rows = [{"date": r.get("OpenDate.Typed"), "revenue": r.get("DishDiscountSumInt", 0), "gross": r.get("DishSumInt", 0), "guests": r.get("GuestNum", 0), "discount": r.get("DishSumInt", 0) - r.get("DishDiscountSumInt", 0)} for r in data.get("data", [])]
    return {"rows": rows, "from": fd, "to": td}


def get_waiters(from_date: str = None, to_date: str = None) -> dict:
    fd = from_date or today()
    td = to_date or today()
    data = iiko_olap({
        "reportType": "SALES", "buildSummary": "true",
        "groupByRowFields": ["WaiterName"], "groupByColFields": [],
        "aggregateFields": ["DishSumInt", "DishDiscountSumInt", "GuestNum"],
        "filters": date_filter(fd, td)
    })
    waiters = sorted([{"name": r.get("WaiterName","—"), "revenue": r.get("DishDiscountSumInt",0), "gross": r.get("DishSumInt",0), "guests": r.get("GuestNum",0)} for r in data.get("data",[])], key=lambda x: x["revenue"], reverse=True)
    return {"waiters": waiters, "date": fd}


def get_payment_types(from_date: str = None, to_date: str = None) -> dict:
    fd = from_date or today()
    td = to_date or today()
    data = iiko_olap({
        "reportType": "SALES", "buildSummary": "true",
        "groupByRowFields": ["PayTypes"], "groupByColFields": [],
        "aggregateFields": ["DishDiscountSumInt"],
        "filters": date_filter(fd, td)
    })
    payments = sorted([{"type": r.get("PayTypes","—"), "amount": r.get("DishDiscountSumInt",0)} for r in data.get("data",[])], key=lambda x: x["amount"], reverse=True)
    return {"payments": payments, "date": fd}


def get_deleted_dishes(from_date: str = None, to_date: str = None) -> dict:
    fd = from_date or today()
    td = to_date or today()
    data = iiko_olap({
        "reportType": "SALES", "buildSummary": "true",
        "groupByRowFields": ["DishName", "DeletedWithWriteoff", "WaiterName"], "groupByColFields": [],
        "aggregateFields": ["DishAmountInt", "DishSumInt"],
        "filters": {**date_filter(fd, td), "DeletedWithWriteoff": {"filterType": "IncludeValues", "values": ["DELETED_WITH_WRITEOFF", "DELETED_WITHOUT_WRITEOFF"]}}
    })
    dishes = [{"name": r.get("DishName","—"), "amount": r.get("DishAmountInt",0), "sum": r.get("DishSumInt",0), "waiter": r.get("WaiterName","—"), "writeoff": r.get("DeletedWithWriteoff") == "DELETED_WITH_WRITEOFF"} for r in data.get("data",[])]
    return {"dishes": dishes, "total": len(dishes), "date": fd}


def get_top_dishes(from_date: str = None, to_date: str = None, limit: int = 10) -> dict:
    fd = from_date or today()
    td = to_date or today()
    data = iiko_olap({
        "reportType": "SALES", "buildSummary": "false",
        "groupByRowFields": ["DishName", "DishCategory"], "groupByColFields": [],
        "aggregateFields": ["DishAmountInt", "DishDiscountSumInt"],
        "filters": {**date_filter(fd, td), "DeletedWithWriteoff": {"filterType": "IncludeValues", "values": ["NOT_DELETED"]}}
    })
    dishes = sorted([{"name": r.get("DishName","—"), "category": r.get("DishCategory","—"), "amount": r.get("DishAmountInt",0), "sum": r.get("DishDiscountSumInt",0)} for r in data.get("data",[])], key=lambda x: x["amount"], reverse=True)
    return {"dishes": dishes[:limit], "date": fd}


def get_hourly_revenue(from_date: str = None) -> dict:
    fd = from_date or today()
    data = iiko_olap({
        "reportType": "SALES", "buildSummary": "false",
        "groupByRowFields": ["CloseHour"], "groupByColFields": [],
        "aggregateFields": ["DishDiscountSumInt", "GuestNum"],
        "filters": {**date_filter(fd, fd), "DeletedWithWriteoff": {"filterType": "IncludeValues", "values": ["NOT_DELETED"]}}
    })
    hours = sorted([{"hour": r.get("CloseHour","—"), "revenue": r.get("DishDiscountSumInt",0), "guests": r.get("GuestNum",0)} for r in data.get("data",[])], key=lambda x: str(x["hour"]))
    return {"hours": hours, "date": fd}


def get_category_revenue(from_date: str = None, to_date: str = None) -> dict:
    fd = from_date or today()
    td = to_date or today()
    data = iiko_olap({
        "reportType": "SALES", "buildSummary": "false",
        "groupByRowFields": ["DishCategory"], "groupByColFields": [],
        "aggregateFields": ["DishAmountInt", "DishDiscountSumInt"],
        "filters": {**date_filter(fd, td), "DeletedWithWriteoff": {"filterType": "IncludeValues", "values": ["NOT_DELETED"]}}
    })
    cats = sorted([{"category": r.get("DishCategory","—"), "amount": r.get("DishAmountInt",0), "sum": r.get("DishDiscountSumInt",0)} for r in data.get("data",[])], key=lambda x: x["sum"], reverse=True)
    return {"categories": cats, "date": fd}


def get_discounts(from_date: str = None, to_date: str = None) -> dict:
    fd = from_date or today()
    td = to_date or today()
    data = iiko_olap({
        "reportType": "SALES", "buildSummary": "true",
        "groupByRowFields": ["DiscountType", "WaiterName"], "groupByColFields": [],
        "aggregateFields": ["DiscountSum"],
        "filters": date_filter(fd, td)
    })
    discounts = [{"type": r.get("DiscountType","—"), "waiter": r.get("WaiterName","—"), "sum": r.get("DiscountSum",0)} for r in data.get("data",[]) if r.get("DiscountSum",0) != 0]
    return {"discounts": discounts, "date": fd}


def get_cash_shifts(from_date: str = None, to_date: str = None) -> dict:
    fd = from_date or today()
    td = to_date or today()
    try:
        data = iiko_get("v2/cashshifts/list", {"from": fd, "to": td, "status": "ANY"})
        if isinstance(data, list):
            shifts = [{"open_date": s.get("openDate"), "close_date": s.get("closeDate"), "status": s.get("status"), "cash_income": s.get("cashIncome",0), "cash_outcome": s.get("cashOutcome",0), "waiter": s.get("waiter",{}).get("name","—") if isinstance(s.get("waiter"),dict) else "—"} for s in data]
            return {"shifts": shifts, "count": len(shifts), "date": fd}
        return {"raw": str(data), "date": fd}
    except Exception as e:
        return {"error": str(e), "date": fd}


def get_stop_list() -> dict:
    try:
        data = iiko_get("stoplist")
        items = [{"name": i.get("product",{}).get("name","—"), "balance": i.get("balance",0)} for i in (data.get("stopListItems",[]) if isinstance(data,dict) else [])]
        return {"items": items, "count": len(items)}
    except Exception as e:
        return {"error": str(e)}


def get_active_orders() -> dict:
    try:
        data = iiko_get("orders/list", {"includeDeleted": "false", "includeClosed": "false"})
        orders = [{"number": o.get("number"), "table": o.get("table",{}).get("name","—") if isinstance(o.get("table"),dict) else "—", "waiter": o.get("waiter",{}).get("name","—") if isinstance(o.get("waiter"),dict) else "—", "sum": o.get("sum",0), "guests": o.get("guestsCount",0)} for o in (data[:20] if isinstance(data,list) else [])]
        return {"orders": orders, "count": len(orders)}
    except Exception as e:
        return {"error": str(e)}


def get_product_balance(product_name: str = None) -> dict:
    try:
        token = get_iiko_token()
        body = {"reportType": "STORAGES", "buildSummary": "false", "groupByRowFields": ["Product.Name", "Store.Name"], "groupByColFields": [], "aggregateFields": ["Amount", "SumPrice"], "filters": {}}
        if product_name:
            body["filters"]["Product.Name"] = {"filterType": "IncludeValues", "values": [product_name]}
        resp = requests.post(f"https://{IIKO_SERVER}/resto/api/v2/reports/olap?key={token}", json=body, verify=False, timeout=30)
        data = resp.json()
        items = sorted([{"product": r.get("Product.Name","—"), "store": r.get("Store.Name","—"), "amount": r.get("Amount",0), "sum": r.get("SumPrice",0)} for r in data.get("data",[])], key=lambda x: x["product"])
        return {"items": items[:50], "count": len(items)}
    except Exception as e:
        return {"error": str(e)}


def get_employees() -> dict:
    try:
        data = iiko_get("employees", {"washighlyqualified": "false", "dontlimitrecords": "true"})
        employees = [{"name": e.get("firstName","")+" "+e.get("lastName",""), "role": e.get("mainRole",{}).get("name","—") if isinstance(e.get("mainRole"),dict) else "—"} for e in (data if isinstance(data,list) else [])]
        return {"employees": employees, "count": len(employees)}
    except Exception as e:
        return {"error": str(e)}


def get_writeoffs(from_date: str = None, to_date: str = None) -> dict:
    fd = from_date or today()
    td = to_date or today()
    try:
        token = get_iiko_token()
        body = {"reportType": "STORAGES", "buildSummary": "true", "groupByRowFields": ["Product.Name", "Store.Name", "Document.Date"], "groupByColFields": [], "aggregateFields": ["Amount", "SumPrice"], "filters": {"Document.Date": {"filterType": "DateRange", "periodType": "CUSTOM", "from": fd, "to": td, "includeLow": "true", "includeHigh": "true"}, "Document.Type": {"filterType": "IncludeValues", "values": ["WRITE_OFF"]}}}
        resp = requests.post(f"https://{IIKO_SERVER}/resto/api/v2/reports/olap?key={token}", json=body, verify=False, timeout=30)
        data = resp.json()
        items = [{"product": r.get("Product.Name","—"), "store": r.get("Store.Name","—"), "date": r.get("Document.Date","—"), "amount": r.get("Amount",0), "sum": r.get("SumPrice",0)} for r in data.get("data",[])]
        return {"items": items, "count": len(items), "date": fd}
    except Exception as e:
        return {"error": str(e)}


def get_incoming_invoices(from_date: str = None, to_date: str = None) -> dict:
    fd = from_date or today()
    td = to_date or today()
    try:
        token = get_iiko_token()
        body = {"reportType": "STORAGES", "buildSummary": "true", "groupByRowFields": ["Product.Name", "Store.Name", "Document.Date", "Supplier.Name"], "groupByColFields": [], "aggregateFields": ["Amount", "SumPrice"], "filters": {"Document.Date": {"filterType": "DateRange", "periodType": "CUSTOM", "from": fd, "to": td, "includeLow": "true", "includeHigh": "true"}, "Document.Type": {"filterType": "IncludeValues", "values": ["INCOMING_INVOICE"]}}}
        resp = requests.post(f"https://{IIKO_SERVER}/resto/api/v2/reports/olap?key={token}", json=body, verify=False, timeout=30)
        data = resp.json()
        items = [{"product": r.get("Product.Name","—"), "supplier": r.get("Supplier.Name","—"), "date": r.get("Document.Date","—"), "amount": r.get("Amount",0), "sum": r.get("SumPrice",0)} for r in data.get("data",[])]
        return {"items": items, "count": len(items), "date": fd}
    except Exception as e:
        return {"error": str(e)}


def get_tech_card(dish_name: str) -> dict:
    """Технологическая карта блюда — состав и ингредиенты"""
    try:
        # Получаем список всех продуктов/блюд
        data = iiko_get("v2/entities/products/list", {"includeDeleted": "false"})
        if not isinstance(data, list):
            return {"error": "Не удалось получить список блюд"}

        # Ищем блюдо по названию
        found = None
        dish_lower = dish_name.lower()
        for item in data:
            name = item.get("name", "")
            if dish_lower in name.lower():
                found = item
                break

        if not found:
            # Попробуем частичное совпадение
            matches = [i for i in data if dish_lower in i.get("name","").lower()]
            if matches:
                found = matches[0]
            else:
                return {"error": f"Блюдо '{dish_name}' не найдено в системе", "hint": "Используй get_menu для просмотра списка блюд"}

        product_id = found.get("id")
        name = found.get("name")
        dish_type = found.get("type")

        # Получаем детали блюда с составом
        detail = iiko_get(f"v2/entities/products/{product_id}")
        if not isinstance(detail, dict):
            return {"name": name, "type": dish_type, "error": "Детали не найдены"}

        # Извлекаем ингредиенты
        ingredients = []
        for ing in detail.get("ingredients", []):
            product = ing.get("product", {})
            ingredients.append({
                "name": product.get("name", "—"),
                "amount": ing.get("amount", 0),
                "unit": ing.get("unit", {}).get("name", "—") if isinstance(ing.get("unit"), dict) else "—",
                "brutto": ing.get("amountOfGrossWeight", ing.get("amount", 0)),
                "netto": ing.get("amount", 0)
            })

        return {
            "name": name,
            "type": dish_type,
            "price": found.get("price", 0),
            "weight": found.get("weight", 0),
            "energy": found.get("energyFullAmount", 0),
            "proteins": found.get("proteinsFullAmount", 0),
            "fats": found.get("fatsFullAmount", 0),
            "carbs": found.get("carbohydratesFullAmount", 0),
            "ingredients": ingredients,
            "cooking_time": found.get("cookingPlaceName", "—"),
            "description": found.get("description", "")
        }
    except Exception as e:
        return {"error": str(e)}


def get_menu(category: str = None) -> dict:
    """Список всех блюд меню с ценами"""
    try:
        data = iiko_get("v2/entities/products/list", {"includeDeleted": "false"})
        if not isinstance(data, list):
            return {"error": "Не удалось получить меню"}

        dishes = []
        for item in data:
            if item.get("type") not in ["DISH", "GOOD", "MODIFIER"]:
                continue
            cat = item.get("productCategory", {})
            cat_name = cat.get("name", "—") if isinstance(cat, dict) else "—"
            if category and category.lower() not in cat_name.lower():
                continue
            dishes.append({
                "name": item.get("name", "—"),
                "category": cat_name,
                "price": item.get("price", 0),
                "weight": item.get("weight", 0),
                "type": item.get("type", "—")
            })

        dishes.sort(key=lambda x: (x["category"], x["name"]))
        return {"dishes": dishes, "count": len(dishes), "category_filter": category}
    except Exception as e:
        return {"error": str(e)}


def get_dish_cost(dish_name: str) -> dict:
    """Себестоимость блюда"""
    try:
        data = iiko_get("v2/entities/products/list", {"includeDeleted": "false"})
        if not isinstance(data, list):
            return {"error": "Не удалось получить список"}

        dish_lower = dish_name.lower()
        found = next((i for i in data if dish_lower in i.get("name","").lower()), None)
        if not found:
            return {"error": f"Блюдо '{dish_name}' не найдено"}

        product_id = found.get("id")
        detail = iiko_get(f"v2/entities/products/{product_id}")

        cost = detail.get("costPrice", 0) if isinstance(detail, dict) else 0
        price = found.get("price", 0)
        margin = price - cost if price and cost else 0
        margin_pct = round((margin / price * 100), 1) if price else 0

        return {
            "name": found.get("name"),
            "price": price,
            "cost": cost,
            "margin": margin,
            "margin_percent": margin_pct
        }
    except Exception as e:
        return {"error": str(e)}



def create_incoming_invoice(supplier_name: str, store_name: str, items: list,
                             invoice_date: str = None, invoice_number: str = None) -> dict:
    """Создать приходную накладную в iiko"""
    try:
        fd = invoice_date or today()

        # Получаем список поставщиков
        suppliers = iiko_get("suppliers")
        supplier = None
        if isinstance(suppliers, list):
            for s in suppliers:
                if supplier_name.lower() in s.get("name","").lower():
                    supplier = s
                    break
        if not supplier:
            return {"error": f"Поставщик '{supplier_name}' не найден. Проверьте название."}

        # Получаем список складов
        stores = iiko_get("v2/entities/stores/list", {"includeDeleted": "false"})
        store = None
        if isinstance(stores, list):
            for s in stores:
                if store_name.lower() in s.get("name","").lower():
                    store = s
                    break
        if not store:
            return {"error": f"Склад '{store_name}' не найден. Проверьте название."}

        # Получаем список продуктов для сопоставления
        products = iiko_get("v2/entities/products/list", {"includeDeleted": "false"})
        product_map = {}
        if isinstance(products, list):
            for p in products:
                product_map[p.get("name","").lower()] = p

        # Формируем позиции накладной
        invoice_items = []
        not_found = []
        for item in items:
            name = item.get("name", "")
            name_lower = name.lower()
            # Ищем точное совпадение
            product = product_map.get(name_lower)
            # Если не нашли — ищем частичное
            if not product:
                for pname, prod in product_map.items():
                    if name_lower in pname or pname in name_lower:
                        product = prod
                        break
            if not product:
                not_found.append(name)
                continue

            # Получаем единицу измерения
            units = product.get("units", [])
            unit_id = None
            if units:
                unit_id = units[0].get("id")

            invoice_items.append({
                "product": {"id": product.get("id")},
                "amount": item.get("amount", 1),
                "price": item.get("price", 0),
                "sum": round(item.get("amount", 1) * item.get("price", 0), 2),
                "unit": {"id": unit_id} if unit_id else None
            })

        if not invoice_items:
            return {"error": "Ни один товар не найден в системе iiko", "not_found": not_found}

        # Формируем тело накладной
        import uuid
        body = {
            "documentNumber": invoice_number or f"AUTO-{fd}-{str(uuid.uuid4())[:8].upper()}",
            "dateIncoming": fd,
            "supplier": {"id": supplier.get("id")},
            "defaultStore": {"id": store.get("id")},
            "items": invoice_items
        }

        token = get_iiko_token()
        resp = requests.post(
            f"https://{IIKO_SERVER}/resto/api/documents/import/incomingInvoice?key={token}",
            json=body, verify=False, timeout=30
        )

        if resp.status_code in [200, 201]:
            total = sum(i["sum"] for i in invoice_items)
            return {
                "success": True,
                "document_number": body["documentNumber"],
                "date": fd,
                "supplier": supplier.get("name"),
                "store": store.get("name"),
                "items_created": len(invoice_items),
                "items_not_found": not_found,
                "total_sum": total
            }
        else:
            return {"error": f"Ошибка сервера: {resp.status_code} — {resp.text[:200]}"}

    except Exception as e:
        return {"error": str(e)}

# ===================== TOOLS =====================

TOOLS = [
    {"name": "get_revenue", "description": "Выручка, гости, скидки за период.", "input_schema": {"type": "object", "properties": {"from_date": {"type": "string"}, "to_date": {"type": "string"}}, "required": []}},
    {"name": "get_waiters", "description": "Выручка и гости по каждому официанту.", "input_schema": {"type": "object", "properties": {"from_date": {"type": "string"}, "to_date": {"type": "string"}}, "required": []}},
    {"name": "get_payment_types", "description": "Выручка по типам оплаты: наличные, Click, Payme, карта.", "input_schema": {"type": "object", "properties": {"from_date": {"type": "string"}, "to_date": {"type": "string"}}, "required": []}},
    {"name": "get_deleted_dishes", "description": "Удалённые блюда из заказов с указанием официанта.", "input_schema": {"type": "object", "properties": {"from_date": {"type": "string"}, "to_date": {"type": "string"}}, "required": []}},
    {"name": "get_top_dishes", "description": "Топ продаваемых блюд по количеству.", "input_schema": {"type": "object", "properties": {"from_date": {"type": "string"}, "to_date": {"type": "string"}, "limit": {"type": "integer"}}, "required": []}},
    {"name": "get_hourly_revenue", "description": "Почасовая выручка и количество гостей.", "input_schema": {"type": "object", "properties": {"from_date": {"type": "string"}}, "required": []}},
    {"name": "get_category_revenue", "description": "Выручка по категориям блюд.", "input_schema": {"type": "object", "properties": {"from_date": {"type": "string"}, "to_date": {"type": "string"}}, "required": []}},
    {"name": "get_discounts", "description": "Скидки по официантам и типам скидок.", "input_schema": {"type": "object", "properties": {"from_date": {"type": "string"}, "to_date": {"type": "string"}}, "required": []}},
    {"name": "get_cash_shifts", "description": "Кассовые смены, приход и расход наличных.", "input_schema": {"type": "object", "properties": {"from_date": {"type": "string"}, "to_date": {"type": "string"}}, "required": []}},
    {"name": "get_stop_list", "description": "Стоп-лист — недоступные блюда прямо сейчас.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_active_orders", "description": "Открытые заказы — сколько столов занято сейчас.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_product_balance", "description": "Остатки товаров на складе.", "input_schema": {"type": "object", "properties": {"product_name": {"type": "string"}}, "required": []}},
    {"name": "get_employees", "description": "Список сотрудников ресторана.", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_writeoffs", "description": "Списания продуктов со склада.", "input_schema": {"type": "object", "properties": {"from_date": {"type": "string"}, "to_date": {"type": "string"}}, "required": []}},
    {"name": "get_incoming_invoices", "description": "Приходные накладные — поступление товаров от поставщиков.", "input_schema": {"type": "object", "properties": {"from_date": {"type": "string"}, "to_date": {"type": "string"}}, "required": []}},
    {"name": "get_tech_card", "description": "Технологическая карта блюда — состав, ингредиенты, вес брутто/нетто, БЖУ, калории.", "input_schema": {"type": "object", "properties": {"dish_name": {"type": "string", "description": "Название блюда"}}, "required": ["dish_name"]}},
    {"name": "get_menu", "description": "Список всех блюд меню с ценами и категориями.", "input_schema": {"type": "object", "properties": {"category": {"type": "string", "description": "Фильтр по категории (необязательно)"}}, "required": []}},
    {"name": "get_dish_cost", "description": "Себестоимость блюда, цена продажи и маржинальность.", "input_schema": {"type": "object", "properties": {"dish_name": {"type": "string", "description": "Название блюда"}}, "required": ["dish_name"]}},
    {"name": "create_incoming_invoice", "description": "Создать приходную накладную в iiko — оприходовать товары на склад. Используй когда пользователь хочет создать накладную или прислал фото/список товаров для оприходования.", "input_schema": {"type": "object", "properties": {"supplier_name": {"type": "string", "description": "Название поставщика"}, "store_name": {"type": "string", "description": "Название склада"}, "invoice_date": {"type": "string", "description": "Дата YYYY-MM-DD"}, "invoice_number": {"type": "string", "description": "Номер накладной"}, "items": {"type": "array", "description": "Список товаров", "items": {"type": "object", "properties": {"name": {"type": "string"}, "amount": {"type": "number"}, "price": {"type": "number"}}, "required": ["name", "amount", "price"]}}}, "required": ["supplier_name", "store_name", "items"]}}
]

TOOL_FUNCTIONS = {t["name"]: globals()[t["name"]] for t in TOOLS}

SYSTEM_PROMPT = f"""Ты — умный ИИ-ассистент для работы с данными ресторана через систему iiko.

Сегодня: {date.today().isoformat()}

ВАЖНО:
- Никогда не упоминай название ресторана в ответах
- Всегда используй инструменты для получения реальных данных
- Никогда не говори "нет доступа" — сначала попробуй вызвать инструмент
- Если нужно несколько данных — вызывай несколько инструментов

Форматирование:
- Используй эмодзи
- Числа: 1 234 567 сум
- Суммы уже в сумах (не делить)
- Отвечай на языке пользователя (русский/узбекский)"""


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
                    logger.info(f"Tool: {block.name} {block.input}")
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


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USERS:
        await update.message.reply_text("⛔ У вас нет доступа.")
        return
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    try:
        # Берём фото максимального размера
        photo = update.message.photo[-1]
        tg_file = await context.bot.get_file(photo.file_id)
        resp = requests.get(tg_file.file_path, timeout=30)
        resp.raise_for_status()
        image_b64 = base64.b64encode(resp.content).decode()

        # Подпись к фото если есть
        caption = update.message.caption or ""
        user_hint = f"\nКомментарий пользователя: {caption}" if caption else ""

        # Claude читает фото и решает что делать
        prompt = f"""Пользователь прислал фото.{user_hint}

Проанализируй изображение:
1. Если это накладная/счёт/список товаров — извлеки все данные (поставщик, товары, количества, цены, дата, номер) и создай приходную накладную в iiko используя инструмент create_incoming_invoice
2. Если это что-то другое — опиши что видишь и помоги пользователю

При создании накладной:
- Если поставщик не указан — используй "Неизвестный поставщик" и уточни у пользователя
- Если склад не указан — используй основной склад ресторана
- Сообщи результат: какие товары добавлены, какие не найдены в системе"""

        response = anthropic_client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=3000,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
                    {"type": "text", "text": prompt}
                ]
            }]
        )

        # Обрабатываем ответ с возможными tool_use
        messages = [
            {"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
                {"type": "text", "text": prompt}
            ]},
            {"role": "assistant", "content": response.content}
        ]

        for _ in range(8):
            if response.stop_reason != "tool_use":
                break
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    logger.info(f"Photo tool: {block.name} {block.input}")
                    try:
                        result = TOOL_FUNCTIONS[block.name](**block.input)
                        tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(result, ensure_ascii=False)})
                    except Exception as e:
                        tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": f"Ошибка: {str(e)}", "is_error": True})
            messages.append({"role": "user", "content": tool_results})
            response = anthropic_client.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=2000,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages
            )
            messages.append({"role": "assistant", "content": response.content})

        answer = ""
        for block in response.content:
            if hasattr(block, "text"):
                answer = block.text
                break

        await update.message.reply_text(f"🖼 {answer}" if answer else "✅ Готово!", parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Фото ошибка: {e}")
        await update.message.reply_text(f"❌ Ошибка обработки фото: {str(e)}")


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
        recognized = transcribe_voice(resp.content)
        logger.info(f"Голос распознан: {recognized}")
        if not recognized:
            await update.message.reply_text("❌ Не удалось распознать речь. Попробуйте ещё раз.")
            return
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        answer = await process_message(recognized)
        await update.message.reply_text(f"🎤 _{recognized}_\n\n{answer}", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Голос ошибка: {e}")
        await update.message.reply_text(f"❌ Ошибка голосового: {str(e)}")


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name or "Дорогой пользователь"

    if user_id not in ALLOWED_USERS:
        await update.message.reply_text("⛔ У вас нет доступа к этому боту.")
        return

    welcome = f"""👋 Привет, *{user_name}*!

🤖 Я ваш *AI-ассистент* для работы с данными ресторана.

Я умею отвечать на вопросы о:
💰 Выручке и оплатах
👨‍💼 Официантах
🍽 Меню и тех картах
📦 Складе и накладных
📈 Аналитике и отчётах

💬 Пишите текстом или 🎤 голосом — отвечу на русском или узбекском!"""

    await update.message.reply_text(welcome, parse_mode="Markdown")


def main():
    from telegram.ext import CommandHandler
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    logger.info("Бот запущен!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
