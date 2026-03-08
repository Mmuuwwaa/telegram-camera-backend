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
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))  # ID группы для отчётов
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

def validate_init_data(init_data: str) -> tuple[bool, Optional[dict]]:
    try:
        parsed_data = parse_qs(init_data, keep_blank_values=True)
        data = {key: value[0] for key, value in parsed_data.items()}
        user_data = None
        if 'user' in data:
            user_data = json.loads(unquote(data['user']))
        return True, user_data
    except Exception as e:
        logger.error(f"Validation error: {e}")
        return False, None

# Хранилище текстов заданий (task_id -> текст)
task_texts = {}

@app.post("/task_created")
async def task_created(request: Request):
    data = await request.json()
    task_id = data.get("task_id")
    text = data.get("text")
    if not task_id or not text:
        raise HTTPException(status_code=400, detail="Missing task_id or text")
    task_texts[task_id] = text
    logger.info(f"Сохранён текст задания {task_id}: {text}")
    return {"status": "ok"}
    
@app.post("/upload-photo")
async def upload_photo(request: Request):
    try:
        body = await request.json()
        init_data = body.get("initData")
        photo_base64 = body.get("photo")
        timestamp = body.get("timestamp")
        task_id = body.get("task_id")

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

        user_id = user_data.get("id")
        username = user_data.get("username", "")
        first_name = user_data.get("first_name", "")
        last_name = user_data.get("last_name", "")
        full_name = f"{first_name} {last_name}".strip() or username or f"User {user_id}"

        current_time = datetime.now(TIMEZONE)
        time_str = current_time.strftime("%H:%M:%S")

        # Отправляем фото в группу с подписью о выполнении задания
        task_text = task_texts.get(str(task_id), f"Задание #{task_id}")
        caption = f"📸 Фото для задания\n📋 {task_text}\n👤 {full_name}\n⏰ {time_str}"
        sent_message = await bot.send_photo(
            chat_id=CHANNEL_ID,
            photo=BufferedInputFile(photo_bytes, filename=f"task_{task_id}_{user_id}.jpg"),
            caption=caption
        )
        file_id = sent_message.photo[-1].file_id

        # Отправляем служебное сообщение боту для обновления статуса задания
        await bot.send_message(
            chat_id=-1003772065180,
            text=f"#task_done: {user_id}, {task_id}, {file_id}"
        )

        # Уведомляем пользователя
        await bot.send_message(chat_id=user_id, text="✅ Задание выполнено! Спасибо.")

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