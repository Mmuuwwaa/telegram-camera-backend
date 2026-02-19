import base64
import hashlib
import hmac
import json
import logging
import os  # уже есть
import requests  # ← НОВОЕ
from urllib.parse import parse_qs, unquote
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
        # ... (вся предыдущая логика до отправки админу)

        # Отправляем фото админу (как и раньше)
        sent_message = await bot.send_photo(
            chat_id=ADMIN_ID,
            photo=BufferedInputFile(photo_bytes, filename=f"photo_{user_id}.jpg"),
            caption=caption
        )
        file_id = sent_message.photo[-1].file_id  # можно сохранить, если пригодится

        # --- НОВЫЙ БЛОК: отправка в группу ---
        try:
            group_caption = (
                f"📸 Новое фото от сотрудника\n"
                f"👤 {full_name}\n"
                f"🆔 {user_id}\n"
                f"⏰ {current_time.strftime('%H:%M:%S')}\n"
                f"✅ {'Вовремя' if is_on_time else 'Опоздал'}\n"
                f"📸 Mini App"
            )
            await bot.send_photo(
                chat_id=CHANNEL_ID,  # используем переменную окружения
                photo=BufferedInputFile(photo_bytes, filename=f"photo_{user_id}.jpg"),
                caption=group_caption
            )
            logger.info(f"✅ Фото отправлено в группу {CHANNEL_ID}")
        except Exception as e:
            logger.error(f"❌ Ошибка отправки в группу: {e}")

        # --- Отправка в Google Sheets (уже есть) ---
        send_time_str = current_time.strftime("%H:%M:%S")
        send_to_google_sheet(
            user_name=full_name,
            user_id=user_id,
            send_time=send_time_str,
            on_time=is_on_time,
            is_suspicious=0,  # или ваша логика
            from_mini_app=1,
            photo_link=""  # пока пусто, но можно добавить ссылку на сообщение позже
        )

        # ... (остальной код: уведомление пользователю и return)
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

# --- HEALTH CHECK ---
@app.get("/health")
async def health():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}