import base64
import hashlib
import hmac
import json
import logging
import os
import requests
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
GOOGLE_SCRIPT_URL = os.getenv("GOOGLE_SCRIPT_URL")  # можно удалить, если не нужно
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))

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

# --- ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ: ОПРЕДЕЛЕНИЕ ЭТАПА ПО ВРЕМЕНИ ---
def get_stage_and_on_time(current_time: datetime) -> tuple[Optional[str], int]:
    """Возвращает (stage, on_time) где stage: 'start','prep','clean' или None, on_time: 1/0."""
    hour = current_time.hour
    minute = current_time.minute
    # Этап 1: начало смены 9:00-9:15
    if hour == 9 and 0 <= minute <= 15:
        return "start", 1
    # Этап 2: заготовки 10:00-10:15
    elif hour == 10 and 0 <= minute <= 15:
        return "prep", 1
    # Этап 3: уборка 21:30-22:00
    elif hour == 21 and minute >= 30 or hour == 22 and minute == 0:
        return "clean", 1
    else:
        # Если время вне интервалов, этап не определён, on_time=0
        return None, 0

# --- ФУНКЦИЯ ВАЛИДАЦИИ INIT DATA (без изменений) ---
def validate_init_data(init_data: str) -> tuple[bool, Optional[dict]]:
    try:
        parsed_data = parse_qs(init_data, keep_blank_values=True)
        data = {key: value[0] for key, value in parsed_data.items()}
        hash_value = data.pop('hash', None)
        if not hash_value:
            return False, None
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
        return computed_hash == hash_value, user_data
    except Exception as e:
        logger.error(f"Validation error: {e}")
        return False, None

# --- ОБРАБОТЧИК POST /upload-photo ---
@app.post("/upload-photo")
async def upload_photo(request: Request):
    try:
        body = await request.json()
        init_data = body.get("initData")
        photo_base64 = body.get("photo")
        timestamp = body.get("timestamp")

        if not all([init_data, photo_base64, timestamp]):
            raise HTTPException(status_code=400, detail="Missing required fields")

        is_valid, user_data = validate_init_data(init_data)
        if not is_valid:
            raise HTTPException(status_code=403, detail="Invalid init data")
        if not user_data:
            raise HTTPException(status_code=400, detail="No user data")

        # Декодируем фото
        try:
            photo_data = photo_base64.split(',')[1]
            photo_bytes = base64.b64decode(photo_data)
        except Exception as e:
            logger.error(f"Ошибка декодирования фото: {e}")
            raise HTTPException(status_code=400, detail="Invalid photo data")

        # Текущее время (можно использовать UTC, но для этапов лучше локальное, если бэкенд в UTC+0)
        # Предположим, сервер в UTC. Для корректной работы этапов нужно либо передавать временную зону,
        # либо настроить сервер на Екатеринбург. Пока используем UTC, но позже можно заменить на локальное.
        current_time = datetime.now()  # в реальности нужно заменить на datetime.now(TIMEZONE)
        # Определяем этап и вовремя ли
        stage, on_time = get_stage_and_on_time(current_time)

        # Если время вне интервалов, всё равно принимаем фото, но stage=None. В таком случае,
        # нужно будет попросить пользователя выбрать этап (через бота). Но сейчас Mini App не поддерживает выбор,
        # поэтому пока сохраняем как "unknown".
        if stage is None:
            stage = "unknown"
            # on_time уже 0

        # Данные пользователя
        user_id = user_data.get("id")
        username = user_data.get("username", "")
        first_name = user_data.get("first_name", "")
        last_name = user_data.get("last_name", "")
        full_name = f"{first_name} {last_name}".strip() or username or f"User {user_id}"

        # Подпись для админа
        admin_caption = (
            f"📸 Новое фото от сотрудника (Mini App)\n"
            f"👤 Имя: {full_name}\n"
            f"🆔 ID: {user_id}\n"
            f"⏰ Время: {current_time.strftime('%H:%M:%S')} UTC\n"
            f"📌 Этап: {stage}\n"
            f"✅ {'Вовремя' if on_time else 'Опоздал/вне окна'}\n"
        )

        # 1. Отправляем фото админу
        await bot.send_photo(
            chat_id=ADMIN_ID,
            photo=BufferedInputFile(photo_bytes, filename=f"photo_{user_id}.jpg"),
            caption=admin_caption
        )

        # 2. Отправляем копию в группу
        group_caption = (
            f"📸 Новое фото от сотрудника\n"
            f"👤 {full_name}\n"
            f"🆔 {user_id}\n"
            f"⏰ {current_time.strftime('%H:%M:%S')} UTC\n"
            f"📌 Этап: {stage}\n"
            f"✅ {'Вовремя' if on_time else 'Опоздал/вне окна'}\n"
            f"📸 Mini App"
        )
        sent_message = await bot.send_photo(
            chat_id=CHANNEL_ID,
            photo=BufferedInputFile(photo_bytes, filename=f"photo_{user_id}.jpg"),
            caption=group_caption
        )
        file_id = sent_message.photo[-1].file_id

        # 3. **ВАЖНО**: отправляем служебное сообщение для бота-статистики
        await bot.send_message(
            chat_id=CHANNEL_ID,
            text=f"#miniapp_report: {user_id}, {stage}, {on_time}, {file_id}"
        )
        logger.info(f"✅ Служебное сообщение отправлено для user {user_id}, stage {stage}")

        # 4. Отправка в Google Sheets (опционально, можно убрать)
        send_time_str = current_time.strftime("%H:%M:%S")
        # send_to_google_sheet(...)  # закомментируйте, если не нужно

        # 5. Уведомление пользователю
        try:
            if on_time:
                await bot.send_message(chat_id=user_id, text="✅ Ваше фото успешно отправлено и принято вовремя!")
            else:
                await bot.send_message(chat_id=user_id, text="⚠️ Фото отправлено, но вне временного окна. В следующий раз старайтесь укладываться в интервалы!")
        except Exception as e:
            logger.error(f"Не удалось уведомить пользователя {user_id}: {e}")

        return {"status": "success"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка в upload_photo: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

# --- HEALTH CHECK ---
@app.get("/health")
async def health():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}