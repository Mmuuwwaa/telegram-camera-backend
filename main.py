import base64
import hashlib
import hmac
import json
import logging
from datetime import datetime
from typing import Optional
from PIL import Image
from io import BytesIO

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from aiogram import Bot
from aiogram.types import BufferedInputFile
import os
from dotenv import load_dotenv

load_dotenv()

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Настройка CORS для разрешения запросов с вашего фронтенда
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # В продакшене замените на URL вашего фронтенда
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Инициализация бота
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
bot = Bot(token=BOT_TOKEN)

def validate_init_data(init_data: str) -> tuple[bool, Optional[dict]]:
    """
    Проверяет подлинность данных инициализации от Telegram.
    Возвращает (успех, данные пользователя)
    """
    try:
        # Парсим init_data
        data_pairs = {}
        for item in init_data.split('&'):
            if '=' in item:
                key, value = item.split('=', 1)
                data_pairs[key] = value
        
        # Извлекаем хеш
        hash_value = data_pairs.pop('hash', None)
        if not hash_value:
            return False, None
        
        # Сортируем данные для проверки
        sorted_data = '\n'.join(f"{k}={v}" for k, v in sorted(data_pairs.items()))
        
        # Создаем секретный ключ из токена бота
        secret_key = hmac.new(
            b"WebAppData", 
            BOT_TOKEN.encode(), 
            hashlib.sha256
        ).digest()
        
        # Вычисляем ожидаемый хеш
        computed_hash = hmac.new(
            secret_key, 
            sorted_data.encode(), 
            hashlib.sha256
        ).hexdigest()
        
        # Проверяем хеш
        is_valid = computed_hash == hash_value
        
        # Парсим данные пользователя, если они есть
        user_data = None
        if 'user' in data_pairs:
            import urllib.parse
            user_json = urllib.parse.unquote(data_pairs['user'])
            user_data = json.loads(user_json)
        
        return is_valid, user_data
        
    except Exception as e:
        logger.error(f"Ошибка валидации init_data: {e}")
        return False, None

def check_photo_metadata(photo_bytes: bytes) -> dict:
    """
    Проверяет метаданные фото (EXIF) на наличие признаков манипуляции
    """
    result = {
        "is_suspicious": False,
        "reasons": []
    }
    
    try:
        img = Image.open(BytesIO(photo_bytes))
        
        # Проверяем наличие EXIF данных
        if hasattr(img, '_getexif') and img._getexif():
            exif = img._getexif()
            
            # Проверяем дату съемки (тег 36867 = DateTimeOriginal)
            if 36867 in exif:
                photo_date = exif[36867]
                result["photo_date"] = photo_date
                
                # Здесь можно добавить проверку, что фото сделано недавно
                # Для этого нужно парсить дату и сравнивать с текущей
        else:
            # Отсутствие EXIF может быть признаком обработки фото
            result["reasons"].append("no_exif_data")
            result["is_suspicious"] = True
            
    except Exception as e:
        logger.error(f"Ошибка при анализе фото: {e}")
        result["reasons"].append("analysis_error")
    
    return result

@app.post("/upload-photo")
async def upload_photo(request: Request):
    """
    Эндпоинт для приема фото от Mini App
    """
    try:
        data = await request.json()
        
        # 1. Проверяем наличие всех необходимых полей
        if not all(k in data for k in ["initData", "photo", "timestamp"]):
            raise HTTPException(status_code=400, detail="Missing required fields")
        
        # 2. Валидируем initData от Telegram
        is_valid, user_data = validate_init_data(data["initData"])
        if not is_valid:
            logger.warning(f"Недействительные данные инициализации: {data['initData'][:100]}...")
            raise HTTPException(status_code=403, detail="Invalid init data")
        
        if not user_data:
            raise HTTPException(status_code=400, detail="No user data in init data")
        
        # 3. Извлекаем фото из base64
        try:
            # Убираем префикс data:image/jpeg;base64,
            photo_base64 = data["photo"].split(',')[1]
            photo_bytes = base64.b64decode(photo_base64)
        except Exception as e:
            logger.error(f"Ошибка декодирования фото: {e}")
            raise HTTPException(status_code=400, detail="Invalid photo data")
        
        # 4. Анализируем фото на признаки манипуляции
        photo_analysis = check_photo_metadata(photo_bytes)
        
        # 5. Проверяем время отправки
        current_time = datetime.now()
        hour = current_time.hour
        minute = current_time.minute
        
        # Проверяем, что время в интервале 9:00 - 9:15
        is_on_time = (hour == 9 and 0 <= minute <= 15)
        
        # 6. Формируем сообщение для админа
        user_id = user_data.get("id", "unknown")
        username = user_data.get("username", "unknown")
        first_name = user_data.get("first_name", "")
        last_name = user_data.get("last_name", "")
        
        full_name = f"{first_name} {last_name}".strip()
        if not full_name:
            full_name = username or f"User {user_id}"
        
        caption = (
            f"📸 Новое фото от сотрудника\n"
            f"👤 Имя: {full_name}\n"
            f"🆔 ID: {user_id}\n"
            f"⏰ Время: {current_time.strftime('%H:%M:%S')}\n"
            f"✅ Вовремя: {'Да' if is_on_time else 'Нет'}\n"
        )
        
        # Добавляем предупреждение, если фото подозрительное
        if photo_analysis["is_suspicious"]:
            caption += f"⚠️ ВНИМАНИЕ: Фото подозрительное!\n"
            caption += f"Причины: {', '.join(photo_analysis['reasons'])}\n"
        
        # 7. Отправляем фото админу
        try:
            await bot.send_photo(
                chat_id=ADMIN_ID,
                photo=BufferedInputFile(
                    photo_bytes,
                    filename=f"photo_{user_id}_{current_time.strftime('%Y%m%d_%H%M%S')}.jpg"
                ),
                caption=caption
            )
            
            logger.info(f"Фото успешно отправлено от пользователя {user_id}")
            
            # Также отправляем уведомление самому пользователю (если нужно)
            try:
                if is_on_time:
                    await bot.send_message(
                        chat_id=user_id,
                        text="✅ Ваше фото успешно отправлено и принято вовремя!"
                    )
                else:
                    await bot.send_message(
                        chat_id=user_id,
                        text="⚠️ Фото отправлено, но вы опоздали. В следующий раз отправляйте до 9:15!"
                    )
            except Exception as e:
                logger.error(f"Не удалось отправить уведомление пользователю {user_id}: {e}")
            
            return {"status": "success", "message": "Photo uploaded successfully"}
            
        except Exception as e:
            logger.error(f"Ошибка при отправке фото в Telegram: {e}")
            raise HTTPException(status_code=500, detail="Failed to send photo to Telegram")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Неожиданная ошибка: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/health")
async def health_check():
    """Проверка работоспособности сервера"""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

@app.on_event("shutdown")
async def shutdown():
    """Закрываем соединение с ботом при остановке сервера"""
    await bot.session.close()