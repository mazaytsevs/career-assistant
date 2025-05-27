import os
from telegram import Bot
from dotenv import load_dotenv
from config.logger import setup_logger

load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
logger = setup_logger(__name__)

async def send_message(text: str):
    """
    Отправляет сообщение в Telegram и логирует это.
    """
    try:
        if not TOKEN:
            logger.error("TELEGRAM_BOT_TOKEN не найден в .env файле")
            return
        if not CHAT_ID:
            logger.error("TELEGRAM_CHAT_ID не найден в .env файле")
            return
            
        logger.info(f"Попытка отправки сообщения с токеном: {TOKEN[:5]}... и chat_id: {CHAT_ID}")
        bot = Bot(token=TOKEN)
        await bot.send_message(chat_id=CHAT_ID, text=text)
        logger.info(f"Сообщение успешно отправлено: {text}")
    except Exception as e:
        logger.error(f"Ошибка при отправке сообщения: {str(e)}")
        logger.error(f"Тип ошибки: {type(e).__name__}")