import base64
import hashlib
import hmac
import json
import logging
import os  # уже есть
import requests  # ← НОВОЕ
from datetime import datetime
from typing import Optional
from io import BytesIO
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from aiogram import Bot
from aiogram.types import BufferedInputFile
from dotenv import load_dotenv

load_dotenv()

# --- НАСТРОЙКИ ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
GOOGLE_SCRIPT_URL = os.getenv("GOOGLE_SCRIPT_URL")  # ← НОВОЕ

# --- ЛОГИРОВАНИЕ ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

bot = Bot(token=BOT_TOKEN)

# --- ФУНКЦИЯ ОТПРАВКИ В GOOGLE SHEETS (НОВАЯ) ---
def send_to_google_sheet(user_name: str, user_id: int, send_time: str, on_time: int, is_suspicious: int, from_mini_app: int):
    """Отправляет данные в Google Sheets через Apps Script"""
    try:
        data = {
            "date": datetime.now().strftime("%d.%m.%Y"),
            "time": send_time,
            "employee_name": user_name,
            "employee_id": str(user_id),
            "status": "✅ Вовремя" if on_time else "❌ Опоздал",
            "suspicious": "⚠️ Подозрительно" if is_suspicious else "✓ Нормально",
            "source": "📸 Mini App" if from_mini_app else "📱 Обычное",
            "full_datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        response = requests.post(GOOGLE_SCRIPT_URL, json=data, timeout=10)
        if response.status_code == 200:
            logger.info(f"✅ Данные отправлены в Google Sheets: {user_name}")
        else:
            logger.error(f"❌ Ошибка отправки в Google Sheets: {response.status_code}")
    except Exception as e:
        logger.error(f"❌ Исключение при отправке в Google Sheets: {e}")

# --- ФУНКЦИЯ ВАЛИДАЦИИ (оставляем как есть) ---
def validate_init_data(init_data: str) -> tuple[bool, Optional[dict]]:
    try:
        parsed_data = parse_qs(init_data, keep_blank_values=True)
        data = {key: value[0] for key, value in parsed_data.items()}
        
        hash_value = data.pop('hash', None)
        if not hash_value:
            return False, None  # ← важно: всегда возвращаем кортеж

        sorted_items = sorted(data.items())
        data_check_string = '\n'.join(f"{k}={v}" for k, v in sorted_items)

        secret_key = hmac.new(
            b"WebAppData", 
            BOT_TOKEN.encode(), 
            hashlib.sha256
        ).digest()

        computed_hash = hmac.new(
            secret_key, 
            data_check_string.encode(), 
            hashlib.sha256
        ).hexdigest()

        user_data = None
        if 'user' in data:
            user_data = json.loads(unquote(data['user']))

        return computed_hash == hash_value, user_data  # всегда кортеж

    except Exception as e:
        logger.error(f"Validation error: {e}")
        return False, None  # ← критически важно!

# --- ОБРАБОТЧИК POST /upload-photo ---
@app.post("/upload-photo")
async def upload_photo(request: Request):
    try:
        data = await request.json()
        if not all(k in data for k in ["initData", "photo", "timestamp"]):
            raise HTTPException(status_code=400, detail="Missing required fields")

        is_valid, user_data = validate_init_data(data["initData"])
        if not is_valid:
            raise HTTPException(status_code=403, detail="Invalid init data")
        if not user_data:
            raise HTTPException(status_code=400, detail="No user data")

        # Декодируем фото
        photo_base64 = data["photo"].split(',')[1]
        photo_bytes = base64.b64decode(photo_base64)

        # Определяем время отправки и проверяем, вовремя ли
        current_time = datetime.now()
        hour, minute = current_time.hour, current_time.minute
        is_on_time = 1 if (hour == 9 and 0 <= minute <= 15) else 0

        # Здесь можно добавить анализ на подозрительность (если используется EXIF)
        is_suspicious = 0  # пока всегда 0, можно доработать позже

        # Отправляем фото админу
        user_id = user_data.get("id", "unknown")
        username = user_data.get("username", "unknown")
        first_name = user_data.get("first_name", "")
        last_name = user_data.get("last_name", "")
        full_name = f"{first_name} {last_name}".strip() or username or f"User {user_id}"

        caption = (
            f"📸 Новое фото от сотрудника (Mini App)\n"
            f"👤 {full_name}\n"
            f"🆔 {user_id}\n"
            f"⏰ {current_time.strftime('%H:%M:%S')}\n"
            f"✅ {'Вовремя' if is_on_time else 'Опоздал'}\n"
        )

        await bot.send_photo(
            chat_id=ADMIN_ID,
            photo=BufferedInputFile(photo_bytes, filename=f"photo_{user_id}.jpg"),
            caption=caption
        )

        # --- ОТПРАВКА В GOOGLE SHEETS (НОВОЕ) ---
        send_time_str = current_time.strftime("%H:%M:%S")
        send_to_google_sheet(
            user_name=full_name,
            user_id=user_id,
            send_time=send_time_str,
            on_time=is_on_time,
            is_suspicious=is_suspicious,
            from_mini_app=1  # это фото точно из Mini App
        )

        # Отправляем уведомление пользователю
        try:
            if is_on_time:
                await bot.send_message(chat_id=user_id, text="✅ Ваше фото успешно отправлено и принято вовремя!")
            else:
                await bot.send_message(chat_id=user_id, text="⚠️ Фото отправлено, но вы опоздали. В следующий раз до 9:15!")
        except Exception as e:
            logger.error(f"Не удалось уведомить пользователя {user_id}: {e}")

        return {"status": "success"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

# --- HEALTH CHECK ---
@app.get("/health")
async def health():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}