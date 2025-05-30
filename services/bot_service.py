"""
Сервис для работы с Telegram ботом
"""

import os
import asyncio
import tempfile
from services.pdf_parser_service import pdf_to_text
import uuid
import httpx
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram import BotCommand
from telegram.helpers import escape_markdown
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from dotenv import load_dotenv
from config.logger import setup_logger
from services.message_service import send_message
from services.head_hunter import search_vacancies, get_resume_list, get_resume_details
from services.resume_vacancy_matcher import match_resume_to_vacancy
from services.vector_store import index_resume, search_similar_resumes
from config.hh_config import (
    HH_CLIENT_ID,
    HH_AUTH_URL,
    get_tokens,
    refresh_tokens,
    TOKENS_FILE
)

# --- Additional imports for RAG matching ---
import json
from services.rag_match_service import index_resume_if_needed

from services.resume_vacancy_matcher import (
    parse_resume,
    parse_vacancy,
    match_resume_to_vacancy,
)

load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
logger = setup_logger(__name__)

WAITING_FOR_RESUME, WAITING_FOR_HH_AUTH_CODE = range(2)          # 0, 1
KEYWORDS, EXPERIENCE, EMPLOYMENT, SCHEDULE, SALARY, PREFS, RESUME = range(2, 9)  # 2–8

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

# Тип занятости
EMPLOYMENT_MAP = {
    "Полная занятость": "full",
    "Частичная занятость": "part",
    "Проектная работа": "project",
    "Волонтёрство": "volunteer",
    "Стажировка": "probation",
}

# Максимальная длина текста вакансии для GigaChat (≈ 300 токенов)
MAX_VACANCY_CHARS = 1000

# Показываем вакансии с мэтчем ≥ 50 %
MATCH_THRESHOLD = 0.5  # показываем вакансии с мэтчем ≥ 50 %

def check_environment():
    """Проверка необходимых переменных окружения"""
    if not TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN не найден в .env файле")
    if not CHAT_ID:
        raise ValueError("TELEGRAM_CHAT_ID не найден в .env файле")
    logger.info(f"Токен бота: {TOKEN[:5]}...")
    logger.info(f"ID чата: {CHAT_ID}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработчик команды /start"""
    logger.info("Вызван обработчик /start")
    keyboard = [
        [InlineKeyboardButton("🔍 Поиск вакансий", callback_data="search_vacancies")],
        [InlineKeyboardButton("📝 Загрузить резюме", callback_data="upload_resume")],
        [InlineKeyboardButton("🔗 Авторизация в HH.ru", callback_data="hh_auth")]
    ]
    
    # Логируем данные кнопок
    for row in keyboard:
        for button in row:
            logger.info(f"Создана кнопка: text='{button.text}', callback_data='{button.callback_data}'")
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "👋 Привет! Я помогу тебе найти работу.\n\n"
        "Выбери действие:",
        reply_markup=reply_markup
    )
    logger.info("Отправлено меню с кнопками")
    return WAITING_FOR_RESUME

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    help_text = (
        "Доступные команды:\n"
        "/start — начать работу\n"
        "/find_job — подобрать вакансии\n"
        "/help — показать это сообщение"
    )
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

async def hh_auth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработчик авторизации в HH.ru"""
    logger.info("Вызван обработчик hh_auth")
    try:
        query = update.callback_query
        logger.info(f"Получен callback_query: {query.data}")
        await query.answer()
        
        # Проверяем, есть ли уже токены
        try:
            tokens = get_tokens()
            if tokens:
                await query.edit_message_text(
                    "✅ Вы уже авторизованы в HH.ru!\n"
                    "Используйте /start для возврата в главное меню."
                )
                return WAITING_FOR_RESUME
        except Exception as e:
            logger.error(f"Ошибка при проверке токенов: {e}")
        
        # Формируем URL для авторизации
        auth_url = f"https://hh.ru/oauth/authorize?response_type=code&client_id={HH_CLIENT_ID}"
        logger.info(f"Сформирован URL для авторизации: {auth_url}")
        
        keyboard = [[InlineKeyboardButton("🔗 Авторизоваться в HH.ru", url=auth_url)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "Для авторизации в HH.ru:\n\n"
            "1. Нажмите кнопку ниже\n"
            "2. Войдите в свой аккаунт HH.ru\n"
            "3. Разрешите доступ приложению\n"
            "4. Скопируйте код авторизации из адресной строки\n"
            "5. Отправьте его мне в следующем сообщении\n\n"
            "Код будет выглядеть примерно так: 1234567890",
            reply_markup=reply_markup
        )
        logger.info("Отправлено сообщение с инструкциями по авторизации")
        return WAITING_FOR_HH_AUTH_CODE
    except Exception as e:
        logger.error(f"Ошибка в обработчике hh_auth: {e}")
        if update and update.effective_message:
            await update.effective_message.reply_text(
                "Произошла ошибка при обработке запроса. Попробуйте еще раз или используйте /start"
            )
        return ConversationHandler.END

async def handle_auth_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработчик получения кода авторизации"""
    auth_code = update.message.text.strip()

    print(f"auth_code: {auth_code}")
    
    try:
        # Получаем токены
        tokens = refresh_tokens(auth_code)
        
        # Сохраняем токены
        os.makedirs(os.path.dirname(TOKENS_FILE), exist_ok=True)
        with open(TOKENS_FILE, 'w') as f:
            json.dump(tokens, f)
        
        await update.message.reply_text(
            "✅ Авторизация успешно завершена!\n"
            "Теперь вы можете использовать все функции бота с HH.ru.\n\n"
            "Используйте /start для возврата в главное меню."
        )
        return WAITING_FOR_RESUME
        
    except Exception as e:
        logger.error(f"Ошибка при авторизации: {e}")
        await update.message.reply_text(
            "❌ Ошибка при авторизации. Пожалуйста, попробуйте еще раз.\n"
            "Используйте /start для возврата в главное меню."
        )
        return WAITING_FOR_RESUME

async def upload_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработчик загрузки резюме"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "Пришли своё резюме текстом или файлом.\n"
        "Если хочешь пропустить шаг — напиши «нет»."
    )
    return WAITING_FOR_RESUME

async def search_vacancies_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработчик поиска вакансий"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "Давай подберём вакансии!\n"
        "Сначала напиши ключевые слова, например: Node.js developer"
    )
    return KEYWORDS

async def set_bot_commands(app: Application):
    """Регистрируем список команд, чтобы они появились в меню Telegram‑клиента."""
    await app.bot.set_my_commands(
        [
            BotCommand("start", "Начать"),
            BotCommand("help", "Помощь"),
            BotCommand("find_job", "Подобрать вакансии"),
        ]
    )

async def hh_keywords(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработчик ввода ключевых слов"""
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

async def hh_experience(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработчик выбора опыта работы"""
    user_input = update.message.text.strip()
    context.user_data["experience"] = EXPERIENCE_MAP.get(user_input)
    keyboard = [
        ["Полная занятость", "Частичная занятость"],
        ["Проектная работа", "Волонтёрство"],
        ["Стажировка"]
    ]
    await update.message.reply_text(
        "Выбери тип занятости:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard, resize_keyboard=True, one_time_keyboard=True
        ),
    )
    return EMPLOYMENT

async def hh_employment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработчик выбора типа занятости"""
    user_input = update.message.text.strip()
    context.user_data["employment"] = EMPLOYMENT_MAP.get(user_input)
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

async def hh_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработчик выбора графика работы"""
    user_input = update.message.text.strip()
    context.user_data["schedule"] = SCHEDULE_MAP.get(user_input)
    keyboard = [
        ["150000", "200000", "250000"],
        ["Без разницы"]
    ]
    await update.message.reply_text(
        "Минимальная зарплата в рублях (можешь ввести число вручную или нажми «Без разницы»):",
        reply_markup=ReplyKeyboardMarkup(
            keyboard, resize_keyboard=True, one_time_keyboard=True
        ),
    )
    return SALARY

async def hh_salary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработчик ввода зарплаты"""
    user_input = update.message.text.strip()
    if user_input.lower() == "без разницы":
        context.user_data["salary"] = None
    else:
        try:
            context.user_data["salary"] = int(user_input.replace("k", "").replace("K", "").replace(" ", ""))
        except ValueError:
            context.user_data["salary"] = None
    await update.message.reply_text(
        "Опиши, что для тебя важно в вакансии (одно‑два предложения).\n"
        "Например: «не хочу фронтенд», «нужна сильная соцпакет», «только ИП‑оформление».\n"
        "Если нет пожеланий — напиши «нет».",
        reply_markup=ReplyKeyboardRemove(),
    )
    return PREFS

async def hh_prefs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработчик ввода предпочтений"""
    prefs = update.message.text or ""
    context.user_data["prefs"] = "" if prefs.lower().strip() == "нет" else prefs
    text = (
        "Пришли своё резюме текстом или файлом. "
        "Если хочешь пропустить шаг — напиши «нет»."
    )
    await update.message.reply_text(
        escape_markdown(text, version=2),
        parse_mode="MarkdownV2"
    )
    return RESUME

async def hh_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработчик загрузки резюме"""
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
            context.user_data["resume_id"] = resume_id
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
            context.user_data["resume_id"] = resume_id
            logger.info("Резюме сохранено в векторной базе под ID=%s", resume_id)
            await update.message.reply_text("Резюме успешно сохранено.")
    else:
        txt = update.message.text or ""
        context.user_data["resume"] = None if txt.lower().strip() == "нет" else txt
        logger.info(f"Резюме (текст) получено: {str(context.user_data['resume'])[:200]}…")
        if context.user_data["resume"]:
            resume_id = index_resume_if_needed(context.user_data["resume"], user_id=update.message.chat.id)
            context.user_data["resume_id"] = resume_id
            logger.info("Резюме сохранено в векторной базе под ID=%s", resume_id)
            await update.message.reply_text("Резюме успешно сохранено.")

    await update.message.reply_text("Ищу подходящие вакансии, подожди пару секунд…")

    # Получаем список резюме пользователя
    try:
        resumes = get_resume_list()
        if resumes:
            # Используем первое резюме для поиска похожих вакансий
            hh_resume_id = resumes.items[0]["id"]
            vacancies = search_vacancies(
                text=context.user_data["keywords"],
                experience=context.user_data["experience"],
                employment=context.user_data["employment"],
                schedule=context.user_data["schedule"],
                salary=context.user_data.get("salary"),
                per_page=20,
                enrich=True,
                resume_id=hh_resume_id  # Используем ID резюме для поиска похожих вакансий
            )
        else:
            # Если резюме нет, используем обычный поиск
            vacancies = search_vacancies(
                text=context.user_data["keywords"],
                experience=context.user_data["experience"],
                employment=context.user_data["employment"],
                schedule=context.user_data["schedule"],
                salary=context.user_data.get("salary"),
                per_page=20,
                enrich=True
            )
    except Exception as exc:
        logger.error(f"Ошибка при поиске вакансий: {exc}")
        # Fallback на обычный поиск
        vacancies = search_vacancies(
            text=context.user_data["keywords"],
            experience=context.user_data["experience"],
            employment=context.user_data["employment"],
            schedule=context.user_data["schedule"],
            salary=context.user_data.get("salary"),
            per_page=20,
            enrich=True
        )

    # Структурируем резюме для мэтчинга
    resume_struct = parse_resume(context.user_data.get("resume") or "")

    # --- Мэтчинг резюме ↔ вакансий ---
    items = vacancies.get("items", [])
    good_items = []

    prefs_text = context.user_data.get("prefs", "")
    search_title = context.user_data.get("keywords", "")  # Используем ключевые слова как заголовок

    for v in items:
        # Собираем текст вакансии
        vac_raw = f"{v['name']}\n{v.get('description') or ''}"
        vacancy_struct = parse_vacancy(vac_raw)
        vacancy_struct["raw_text"] = vac_raw  # для проверки prefs

        # Считаем мэтч
        match = match_resume_to_vacancy(
            resume_struct,
            vacancy_struct,
            prefs_text=prefs_text,
            search_title=search_title  # Передаем заголовок для сравнения
        )
        score = match["score"]

        if score >= MATCH_THRESHOLD:
            v["match_score"] = score
            v["title_score"] = match["title_score"]  # Добавляем скор заголовка
            v["matched_skills"] = match["matched_skills"]
            v["missing_skills"] = match["missing_skills"]
            good_items.append(v)

    if not good_items:
        await update.message.reply_text("Ничего подходящего не нашёл 😔")
    else:
        # Покажем топ‑5, по одному сообщению, чтобы не превысить лимит 4096 символов
        good_items.sort(key=lambda x: x["match_score"], reverse=True)
        for it in good_items[:5]:
            percent = it["match_score"]
            title_percent = it.get("title_score", 0)  # Получаем скор заголовка
            emoji = "🟢" if percent >= 0.80 else ("🟡" if percent >= 0.50 else "🔴")
            name_md = escape_markdown(it['name'], version=2)
            ms_md = escape_markdown(", ".join(it.get("matched_skills", []) or ["—"]), version=2)
            miss_md = escape_markdown(", ".join(it.get("missing_skills", []) or ["—"]), version=2)
            url_md = escape_markdown(it['alternate_url'], version=2)
            percent_md = escape_markdown(f"{int(percent*100)}%", version=2)
            title_percent_md = escape_markdown(f"{int(title_percent*100)}%", version=2)

            text_md = (
                f"{emoji} *{name_md}*\n"
                f"{percent_md} мэтча \(заголовок: {title_percent_md}\)\n"
                f"Совпало: {ms_md}\n"
                f"Не хватает: {miss_md}\n"
                f"[Ссылка на вакансию]({url_md})"
            )

            # Отправляем каждую вакансию отдельным сообщением
            await update.message.reply_text(text_md, parse_mode="MarkdownV2")

    # Можно отправить лог в личный чат (пример)
    await send_message(f"Пользователь {update.message.chat.id} завершил поиск вакансий.")

    return ConversationHandler.END

def run_bot():
    """Запуск бота с автоматическим управлением циклом событий"""
    check_environment()
    logger.info("Создание приложения бота...")
    application = Application.builder().token(TOKEN).build()

    application.post_init = set_bot_commands

    # Основной обработчик команд
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            WAITING_FOR_RESUME: [
                CallbackQueryHandler(hh_auth, pattern="^hh_auth$"),
                CallbackQueryHandler(upload_resume, pattern="^upload_resume$"),
                CallbackQueryHandler(search_vacancies_handler, pattern="^search_vacancies$"),
                MessageHandler(filters.Document.ALL, hh_resume),
                MessageHandler(filters.TEXT & ~filters.COMMAND, hh_resume),
            ],
            WAITING_FOR_HH_AUTH_CODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_auth_code),
            ],
            KEYWORDS: [MessageHandler(filters.TEXT & ~filters.COMMAND, hh_keywords)],
            EXPERIENCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, hh_experience)],
            EMPLOYMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, hh_employment)],
            SCHEDULE: [MessageHandler(filters.TEXT & ~filters.COMMAND, hh_schedule)],
            SALARY: [MessageHandler(filters.TEXT & ~filters.COMMAND, hh_salary)],
            PREFS: [MessageHandler(filters.TEXT & ~filters.COMMAND, hh_prefs)],
            RESUME: [MessageHandler(~filters.COMMAND, hh_resume)],
        },
        fallbacks=[CommandHandler("start", start)],
        name="main_conversation",
        persistent=False,
    )
    
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler('help', help_command))

    # Обработчик ошибок
    async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработчик ошибок"""
        logger.error(f"Произошла ошибка: {context.error}")
        if update and update.effective_message:
            await update.effective_message.reply_text(
                "Произошла ошибка при обработке запроса. Попробуйте еще раз или используйте /start"
            )
        elif update and update.callback_query:
            await update.callback_query.answer(
                "Произошла ошибка. Попробуйте еще раз или используйте /start"
            )

    application.add_error_handler(error_handler)

    # Запускаем бота, Telegram-bot API сам управляет асинхронным циклом
    logger.info("Запуск polling...")
    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )