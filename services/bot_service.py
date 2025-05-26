import os
import asyncio
import httpx
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv
from config.logger import setup_logger

load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
logger = setup_logger(__name__)

def check_environment():
    """Проверка необходимых переменных окружения"""
    if not TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN не найден в .env файле")
    if not CHAT_ID:
        raise ValueError("TELEGRAM_CHAT_ID не найден в .env файле")
    logger.info(f"Токен бота: {TOKEN[:5]}...")
    logger.info(f"ID чата: {CHAT_ID}")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    await update.message.reply_text("Привет! Я бот для помощи в карьере. Чем могу помочь?")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    help_text = """
    Доступные команды:
    /start - Начать работу с ботом
    /help - Показать это сообщение
    """
    await update.message.reply_text(help_text)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик входящих сообщений"""
    message_type = update.message.chat.type
    text = update.message.text

    logger.info(f'Пользователь ({update.message.chat.id}) в {message_type}: "{text}"')
    
    # Здесь можно добавить логику обработки сообщений
    response = f"Получил ваше сообщение: {text}"
    await update.message.reply_text(response)

async def error(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок"""
    logger.error(f'Произошла ошибка: {context.error}')

async def send_startup_message(application: Application):
    """Отправка сообщения при запуске бота"""
    try:
        logger.info("Попытка отправить стартовое сообщение...")
        await application.bot.send_message(
            chat_id=CHAT_ID,
            text="🚀 Бот запущен и готов к работе! Используйте /help для просмотра доступных команд."
        )
        logger.info("Стартовое сообщение успешно отправлено")
    except httpx.ConnectError as e:
        logger.error(f"Ошибка подключения при отправке стартового сообщения: {str(e)}")
        logger.error("Проверьте подключение к интернету и доступность серверов Telegram")
        raise
    except Exception as e:
        logger.error(f"Ошибка при отправке стартового сообщения: {str(e)}")
        raise

def run_bot():
    """Запуск бота с автоматическим управлением циклом событий"""
    check_environment()
    logger.info("Создание приложения бота...")
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler('start', start_command))
    application.add_handler(CommandHandler('help', help_command))
    application.add_handler(MessageHandler(filters.TEXT, handle_message))
    application.add_error_handler(error)

    # Запускаем бота, Telegram-bot API сам управляет асинхронным циклом
    logger.info("Запуск polling...")
    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )