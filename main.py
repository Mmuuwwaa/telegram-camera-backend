import base64
import hashlib
import hmac
import json
import logging
import os
import pytz
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

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
TIMEZONE = pytz.timezone("Asia/Yekaterinburg")

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

def get_stage_and_on_time(current_time: datetime) -> tuple[Optional[str], int]:
    hour = current_time.hour
    minute = current_time.minute
    if hour == 9 and 0 <= minute <= 15:
        return "start", 1
    elif hour == 10 and 0 <= minute <= 15:
        return "prep", 1
    elif (hour == 21 and minute >= 30) or (hour == 22 and minute == 0):
        return "clean", 1
    else:
        return None, 0

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

@app.post("/upload-photo")
async def upload_photo(request: Request):
    try:
        body = await request.json()
        init_data = body.get("initData")
        photo_base64 = body.get("photo")
        timestamp = body.get("timestamp")
        stage_from_client = body.get("stage")
        task_id = body.get("task_id")  # может быть None или строка

        if not all([init_data, photo_base64, timestamp]):
            raise HTTPException(status_code=400, detail="Missing required fields")

        is_valid, user_data = validate_init_data(init_data)
        if not is_valid:
            raise HTTPException(status_code=403, detail="Invalid init data")
        if not user_data:
            raise HTTPException(status_code=400, detail="No user data")

        try:
            photo_data = photo_base64.split(',')[1]
            photo_bytes = base64.b64decode(photo_data)
        except Exception as e:
            logger.error(f"Ошибка декодирования фото: {e}")
            raise HTTPException(status_code=400, detail="Invalid photo data")

        current_time = datetime.now(TIMEZONE)

        # Определяем этап
        if stage_from_client and stage_from_client in ["start", "prep", "clean"]:
            stage = stage_from_client
            _, on_time = get_stage_and_on_time(current_time)
        else:
            stage, on_time = get_stage_and_on_time(current_time)

        # Определяем этап
if stage_from_client and stage_from_client in ["start", "prep", "clean"]:
    stage = stage_from_client
    _, on_time = get_stage_and_on_time(current_time)
elif task_id:
    stage = None  # для заданий этап не нужен
    on_time = 0
else:
    stage, on_time = get_stage_and_on_time(current_time)

# Теперь формируем отображаемое название этапа
stage_display = stage if stage else "unknown"

user_id = user_data.get("id")
username = user_data.get("username", "")
first_name = user_data.get("first_name", "")
last_name = user_data.get("last_name", "")
full_name = f"{first_name} {last_name}".strip() or username or f"User {user_id}"

time_str = current_time.strftime("%H:%M:%S")

# Подпись для фото в группе
if task_id:
    group_caption = (
        f"📸 Фото для задания\n"
        f"👤 {full_name}\n"
        f"🆔 {user_id}\n"
        f"⏰ {time_str} (Екатеринбург)\n"
        f"📌 Задание\n"
        f"✅ {'Вовремя' if on_time else 'Вне окна'}\n"
        f"📸 Mini App"
    )
else:
    group_caption = (
        f"📸 Новое фото от сотрудника\n"
        f"👤 {full_name}\n"
        f"🆔 {user_id}\n"
        f"⏰ {time_str} (Екатеринбург)\n"
        f"📌 Этап: {stage_display}\n"
        f"✅ {'Вовремя' if on_time else 'Вне окна'}\n"
        f"📸 Mini App"
    )

        sent_message = await bot.send_photo(
            chat_id=CHANNEL_ID,
            photo=BufferedInputFile(photo_bytes, filename=f"photo_{user_id}.jpg"),
            caption=group_caption
        )
        file_id = sent_message.photo[-1].file_id

        # Служебное сообщение для бота (этапы)

        # Уведомление пользователю
        try:
    if task_id:
        await bot.send_message(chat_id=user_id, text="✅ Фото для задания отправлено!")
    else:
        if on_time:
            await bot.send_message(chat_id=user_id, text="✅ Ваше фото принято вовремя!")
        else:
            await bot.send_message(chat_id=user_id, text="⚠️ Фото принято, но вне временного окна.")
except Exception as e:
    logger.error(f"Не удалось уведомить пользователя {user_id}: {e}")

        return {"status": "success"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка в upload_photo: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/health")
async def health():
    now = datetime.now(TIMEZONE)
    return {"status": "healthy", "timestamp": now.isoformat()}