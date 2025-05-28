import os
import asyncio
import tempfile
from services.pdf_parser_service import pdf_to_text
from services.rag_match_service import index_resume_if_needed
import uuid
import httpx
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)
from dotenv import load_dotenv
from config.logger import setup_logger
from services.message_service import send_message

load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
logger = setup_logger(__name__)

# Conversation states for /find_job command
KEYWORDS, EXPERIENCE, SCHEDULE, RESUME = range(4)

# Читаемые лейблы → значения, которые ждёт HH API
EXPERIENCE_MAP = {
    "Без опыта": "noExperience",
    "1-3 года": "between1And3",
    "3-6 лет": "between3And6",
    "6+ лет": "moreThan6",
    "Без разницы": None,
}

SCHEDULE_MAP = {
    "Удалёнка": "remote",
    "Полный день": "fullDay",
    "Сменный график": "shift",
    "Без разницы": None,
}

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
    await send_message("привет друг")

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


# --- Conversation handler for /find_job ---
async def hh_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Шаг 0 — старт команды /find_job"""
    await update.message.reply_text(
        "Давай подберём вакансии!\n"
        "Сначала напиши ключевые слова, например: *Node.js developer*",
        parse_mode="Markdown"
    )
    return KEYWORDS

async def hh_keywords(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["keywords"] = update.message.text
    keyboard = [
        ["Без опыта", "1-3 года"],
        ["3-6 лет", "6+ лет"],
        ["Без разницы"]
    ]
    await update.message.reply_text(
        "Выбери свой опыт работы:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard, resize_keyboard=True, one_time_keyboard=True
        ),
    )
    return EXPERIENCE

async def hh_experience(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.strip()
    context.user_data["experience"] = EXPERIENCE_MAP.get(user_input)
    keyboard = [
        ["Удалёнка", "Полный день"],
        ["Сменный график", "Без разницы"]
    ]
    await update.message.reply_text(
        "Выбери график работы:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard, resize_keyboard=True, one_time_keyboard=True
        ),
    )
    return SCHEDULE

async def hh_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.strip()
    context.user_data["schedule"] = SCHEDULE_MAP.get(user_input)
    await update.message.reply_text(
        "Пришли своё резюме *текстом* или *файлом*. "
        "Если пропустить шаг — напиши `нет`.",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )
    return RESUME

async def hh_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Обрабатываем текст или документ
    if update.message.document:
        file_obj = await update.message.document.get_file()
        file_bytes = await file_obj.download_as_bytearray()
        file_name = update.message.document.file_name or ""
        # --- PDF branch ---
        if file_name.lower().endswith(".pdf"):
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(file_bytes)
                tmp_path = tmp.name
            try:
                resume_text = pdf_to_text(tmp_path)
            except Exception as exc:
                logger.error("Ошибка pdf_to_text: %s", exc)
                resume_text = ""
            context.user_data["resume"] = resume_text
            logger.info(
                "Резюме (PDF) извлечено, первые 200 символов: %s…",
                resume_text[:200]
            )
            # --- RAG indexing ---
            resume_id = index_resume_if_needed(resume_text, user_id=update.message.chat.id)
            logger.info("Резюме сохранено в векторной базе под ID=%s", resume_id)
            await update.message.reply_text("Резюме успешно сохранено.")
        # --- fallback: обычный текстовый файл ---
        else:
            context.user_data["resume"] = file_bytes.decode("utf-8", errors="ignore")
            logger.info(
                "Резюме (файл) получено: %s…",
                str(context.user_data["resume"])[:200]
            )
            # --- RAG indexing ---
            resume_id = index_resume_if_needed(context.user_data["resume"], user_id=update.message.chat.id)
            logger.info("Резюме сохранено в векторной базе под ID=%s", resume_id)
            await update.message.reply_text("Резюме успешно сохранено.")
    else:
        txt = update.message.text or ""
        context.user_data["resume"] = None if txt.lower().strip() == "нет" else txt
        logger.info(f"Резюме (текст) получено: {str(context.user_data['resume'])[:200]}…")
        if context.user_data["resume"]:
            resume_id = index_resume_if_needed(context.user_data["resume"], user_id=update.message.chat.id)
            logger.info("Резюме сохранено в векторной базе под ID=%s", resume_id)
            await update.message.reply_text("Резюме успешно сохранено.")

    await update.message.reply_text("Ищу подходящие вакансии, подожди пару секунд…")

    from services.head_hunter import search_vacancies

    vacancies = search_vacancies(
        text=context.user_data["keywords"],
        experience=context.user_data["experience"],
        schedule=context.user_data["schedule"],
        per_page=10
    )

    items = vacancies.get("items", [])[:5]
    if not items:
        await update.message.reply_text("К сожалению, ничего не нашёл 😔")
    else:
        reply_parts = []
        for v in items:
            reply_parts.append(f"{v['name']} — {v['employer']['name']}\n{v['alternate_url']}")
        await update.message.reply_text("\n\n".join(reply_parts))

    # Можно отправить лог в личный чат (пример)
    await send_message(f"Пользователь {update.message.chat.id} завершил поиск вакансий.")

    return ConversationHandler.END

async def hh_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Поиск отменён.")
    return ConversationHandler.END

def run_bot():
    """Запуск бота с автоматическим управлением циклом событий"""
    check_environment()
    logger.info("Создание приложения бота...")
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler('start', start_command))
    application.add_handler(CommandHandler('help', help_command))

    hh_conv = ConversationHandler(
        entry_points=[CommandHandler('find_job', hh_start)],
        states={
            KEYWORDS: [MessageHandler(filters.TEXT & ~filters.COMMAND, hh_keywords)],
            EXPERIENCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, hh_experience)],
            SCHEDULE: [MessageHandler(filters.TEXT & ~filters.COMMAND, hh_schedule)],
            RESUME: [MessageHandler(~filters.COMMAND, hh_resume)],
        },
        fallbacks=[CommandHandler('cancel', hh_cancel)],
    )
    application.add_handler(hh_conv)

    application.add_handler(MessageHandler(filters.TEXT, handle_message))
    application.add_error_handler(error)

    # Запускаем бота, Telegram-bot API сам управляет асинхронным циклом
    logger.info("Запуск polling...")
    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )