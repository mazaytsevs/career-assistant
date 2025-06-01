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
from services.head_hunter import (
    search_vacancies,
    get_resume_list,
    get_resume_details,
    get_vacancy_details,
    apply_for_vacancy,
    auto_apply_vacancies,
)
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

from services.gigachat_service import generate_cover_letter

load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
logger = setup_logger(__name__)

RESUME_ID = os.getenv("RESUME_ID")


WAITING_FOR_RESUME, WAITING_FOR_HH_AUTH_CODE = range(2)          # 0, 1
KEYWORDS, EXPERIENCE, EMPLOYMENT, SCHEDULE, SALARY, PREFS, RESUME = range(2, 9)  # 2–8
WAITING_FOR_COVER_LETTER = 9  # Новое состояние для ожидания сопроводительного письма
WAITING_FOR_APPLY_CHOICE = 10
WAITING_FOR_AUTO_APPLY_COVER = 11  # Ожидание сопроводительного письма для автоотклика
WAITING_FOR_AUTO_APPLY_COUNT = 12  # Ожидание количества вакансий для автоотклика

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
        [InlineKeyboardButton("🤖 Автоотклик", callback_data="auto_apply")],
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
        return WAITING_FOR_RESUME

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

async def auto_apply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработчик автоотклика"""
    query = update.callback_query
    await query.answer()
    
    # Устанавливаем флаг автоотклика
    context.user_data["is_auto_apply"] = True
    
    await query.edit_message_text(
        "Давай настроим автоотклик!\n"
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
    context.user_data["prefs"] = update.message.text.strip()
    
    # Проверяем, находимся ли мы в режиме автоотклика
    if context.user_data.get("is_auto_apply"):
        await update.message.reply_text(
            "Напиши сопроводительное письмо, которое будет использоваться для всех откликов.\n"
            "Или напиши 'нет', если хочешь откликаться без сопроводительного письма."
        )
        return WAITING_FOR_AUTO_APPLY_COVER
    else:
        await update.message.reply_text(
            "Загрузи резюме в формате PDF или TXT, или напиши его текст.\n"
            "Если нет резюме — напиши «нет»."
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
            # Сохраняем HH resume ID для откликов
            context.user_data["hh_resume_id"] = hh_resume_id
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
            title_percent = it.get("title_score", 0)
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

            # Создаем клавиатуру с кнопкой "ОТКЛИКНУТЬСЯ"
            keyboard = [[InlineKeyboardButton("📝 ОТКЛИКНУТЬСЯ", callback_data=f"show_vacancy:{it['id']}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            # Отправляем каждую вакансию отдельным сообщением
            await update.message.reply_text(text_md, parse_mode="MarkdownV2", reply_markup=reply_markup)

    # Можно отправить лог в личный чат (пример)
    await send_message(f"Пользователь {update.message.chat.id} завершил поиск вакансий.")

    return WAITING_FOR_RESUME

async def show_vacancy_details(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показывает детали вакансии и предлагает написать сопроводительное письмо"""
    query = update.callback_query
    await query.answer()

    # --- Логируем и валидируем callback_data ---
    cb_data = query.data
    logger.info("show_vacancy_details: callback_data=%s", cb_data)

    # Проверяем формат
    if ':' not in cb_data:
        await query.message.reply_text("Не удалось распознать выбранную вакансию.")
        return WAITING_FOR_RESUME

    vacancy_id = cb_data.split(':')[1]

    # Пробуем получить детали вакансии
    try:
        vacancy = get_vacancy_details(vacancy_id)
    except Exception as exc:
        logger.error("get_vacancy_details error id=%s: %s", vacancy_id, exc)
        await query.message.reply_text(
            "❌ Не удалось получить детали вакансии. Попробуйте позже или выберите другую."
        )
        return WAITING_FOR_RESUME

    if not vacancy:
        await query.message.reply_text(
            "Вакансия не найдена. Возможно, она уже закрыта."
        )
        return WAITING_FOR_RESUME

    # Сохраняем ID вакансии
    context.user_data["current_vacancy_id"] = vacancy_id
    context.user_data["current_vacancy_url"] = vacancy.get("alternate_url")

    # Собираем текст
    name = escape_markdown(vacancy.get("name", "Без названия"), version=2)
    employer = escape_markdown(vacancy.get("employer", {}).get("name", "Не указано"), version=2)

    salary_obj = vacancy.get("salary") or {}
    salary_from = salary_obj.get("from")
    salary_to = salary_obj.get("to")
    salary_currency = salary_obj.get("currency", "RUR")
    if salary_from or salary_to:
        salary_txt = f"{salary_from or ''}–{salary_to or ''} {salary_currency}"
    else:
        salary_txt = "Не указана"

    description = escape_markdown(
        vacancy.get("description", "Нет описания"), version=2
    )[:MAX_VACANCY_CHARS]

    requirement = escape_markdown(
        vacancy.get("snippet", {}).get("requirement", "Не указаны"), version=2
    )

    text = (
        f"*{name}*\n\n"
        f"Компания: {employer}\n"
        f"Зарплата: {salary_txt}\n\n"
        f"*Описание:* \n{description}\n\n"
        f"*Требования:* \n{requirement}\n\n"
        f"Хотите написать сопроводительное письмо?"
    )

    keyboard = [
        [
            InlineKeyboardButton("✍️ Написать самому", callback_data=f"write_cover:{vacancy_id}"),
            InlineKeyboardButton("🤖 Сгенерировать с AI", callback_data=f"generate_cover:{vacancy_id}"),
        ],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel_cover")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="MarkdownV2")
    return WAITING_FOR_COVER_LETTER

async def handle_cover_letter_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает выбор способа написания сопроводительного письма или отмену"""
    query = update.callback_query
    await query.answer()

    action = query.data.split(':')[0]  # write_cover, generate_cover, cancel_cover
    vacancy_id = context.user_data.get('current_vacancy_id')

    # Отмена
    if action == 'cancel_cover':
        await query.message.reply_text(
            "Окей, вернулись к списку вакансий. Используйте /start, чтобы начать заново."
        )
        context.user_data.pop('current_vacancy_id', None)
        context.user_data.pop('generated_cover_letter', None)
        return ConversationHandler.END

    if not vacancy_id:
        await query.message.reply_text("Ошибка: не найдена информация о вакансии")
        return ConversationHandler.END

    # Ручной ввод письма
    if action == 'write_cover':
        await query.message.reply_text(
            "Пожалуйста, напишите ваше сопроводительное письмо одним сообщением."
        )
        return WAITING_FOR_COVER_LETTER

    # Генерация письма
    resume_text = context.user_data.get('resume')
    if not resume_text:
        await query.message.reply_text(
            "Сначала загрузите резюме через кнопку «📝 Загрузить резюме»."
        )
        return ConversationHandler.END

    vacancy_details = get_vacancy_details(vacancy_id)
    if not vacancy_details:
        await query.message.reply_text("Ошибка: не найдена информация о вакансии")
        return ConversationHandler.END

    await query.message.reply_text("Генерирую сопроводительное письмо…")
    cover_letter = generate_cover_letter(resume_text, vacancy_details)

    if not cover_letter:
        await query.message.reply_text(
            "Не удалось сгенерировать сопроводительное письмо. Попробуйте написать его самостоятельно."
        )
        return WAITING_FOR_COVER_LETTER

    await query.message.reply_text(
        f"Сгенерированное сопроводительное письмо:\n\n{cover_letter}\n\n"
        "Отправьте финальный вариант письма, если нужно изменить — отредактируйте."
    )
    context.user_data['generated_cover_letter'] = cover_letter
    return WAITING_FOR_COVER_LETTER

async def handle_cover_letter_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает введенное пользователем сопроводительное письмо"""
    cover_letter = update.message.text
    vacancy_id = context.user_data.get('current_vacancy_id')

    if not vacancy_id:
        await update.message.reply_text("Ошибка: не найдена информация о вакансии")
        return ConversationHandler.END

    # Сохраняем письмо
    context.user_data['final_cover_letter'] = cover_letter

    # Кнопки выбора
    vacancy_url = context.user_data.get('current_vacancy_url', '')
    keyboard = [
        [InlineKeyboardButton("🖐 Откликнусь сам", callback_data=f"apply_manual:{vacancy_id}")],
        [InlineKeyboardButton("🤖 Откликнуться автоматически", callback_data=f"apply_auto:{vacancy_id}")],
        [InlineKeyboardButton("❌ Отмена", callback_data="apply_cancel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "Сопроводительное письмо сохранено.\n"
        "Как поступим с откликом?",
        reply_markup=reply_markup
    )
    return WAITING_FOR_APPLY_CHOICE


# --- Вспомогательная функция очистки apply context ---
def _clear_apply_context(context: ContextTypes.DEFAULT_TYPE):
    """Очищает временные данные, связанные с текущим откликом"""
    for key in ("current_vacancy_id", "current_vacancy_url",
                "generated_cover_letter", "final_cover_letter"):
        context.user_data.pop(key, None)


# --- Новый обработчик выбора способа отклика ---
async def handle_apply_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает выбор способа отклика"""
    query = update.callback_query
    await query.answer()

    action = query.data.split(':')[0]          # apply_manual / apply_auto / apply_cancel
    vacancy_id = context.user_data.get('current_vacancy_id')
    resume_id = context.user_data.get('hh_resume_id')
    cover_letter = context.user_data.get('final_cover_letter')
    vacancy_url = context.user_data.get('current_vacancy_url', '')

    # Отмена
    if action == 'apply_cancel':
        await query.message.reply_text("Действие отменено. Используйте /start для нового поиска.")
        _clear_apply_context(context)
        return ConversationHandler.END

    # Ручной отклик
    if action == 'apply_manual':
        if vacancy_url:
            await query.message.reply_text(
                f"Откликнись вручную по ссылке:\n{vacancy_url}"
            )
        else:
            await query.message.reply_text("Не могу найти ссылку на вакансию 😔")
        _clear_apply_context(context)
        return ConversationHandler.END

    # Автоматический отклик
    if action == 'apply_auto':
        print(context.user_data["resume_id"])
        success = apply_for_vacancy(vacancy_id, resume_id, cover_letter)
        if success:
            await query.message.reply_text("✅ Отклик успешно отправлен!")
        else:
            await query.message.reply_text("❌ Не удалось отправить отклик автоматически.")
        _clear_apply_context(context)
        return ConversationHandler.END

    # На всякий случай
    await query.message.reply_text("Неизвестное действие.")
    return ConversationHandler.END

async def handle_auto_apply_cover(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработчик ввода сопроводительного письма для автоотклика"""
    cover_letter = update.message.text.strip()
    if cover_letter.lower() != "нет":
        context.user_data["auto_apply_cover_letter"] = cover_letter
    
    keyboard = [
        ["2", "5"],
        ["25", "50"],
        ["200"]
    ]
    await update.message.reply_text(
        "На сколько вакансий откликнуться?",
        reply_markup=ReplyKeyboardMarkup(
            keyboard, resize_keyboard=True, one_time_keyboard=True
        ),
    )
    return WAITING_FOR_AUTO_APPLY_COUNT

async def handle_auto_apply_count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработчик выбора количества вакансий для автоотклика"""
    try:
        count = int(update.message.text.strip())
        if count not in [2, 5, 25, 50, 200]:
            raise ValueError("Invalid count")
        context.user_data["auto_apply_count"] = count
    except ValueError:
        await update.message.reply_text(
            "Пожалуйста, выберите одно из предложенных значений: 2, 5, 25, 50 или 200"
        )
        return WAITING_FOR_AUTO_APPLY_COUNT

    # Вызываем функцию автоотклика с собранными данными
    success = auto_apply_vacancies(
        resume_id=RESUME_ID,
        keywords=context.user_data.get('keywords'),
        count=context.user_data.get('auto_apply_count'),
        experience=context.user_data.get('experience'),
        employment=context.user_data.get('employment'),
        schedule=context.user_data.get('schedule'),
        salary=context.user_data.get('salary'),
        prefs=context.user_data.get('prefs'),
        cover_letter=context.user_data.get('auto_apply_cover_letter')
    )

    if success:
        await update.message.reply_text("✅ Автоотклик успешно настроен!")
    else:
        await update.message.reply_text("❌ Произошла ошибка при настройке автоотклика")

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
                CallbackQueryHandler(auto_apply_handler, pattern="^auto_apply$"),
                CallbackQueryHandler(show_vacancy_details, pattern="^show_vacancy:"),
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
            WAITING_FOR_COVER_LETTER: [
                CallbackQueryHandler(handle_cover_letter_choice, pattern=r"^(write_cover|generate_cover|cancel_cover)(:.+)?$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_cover_letter_text),
            ],
            WAITING_FOR_APPLY_CHOICE: [
                CallbackQueryHandler(handle_apply_choice, pattern=r"^(apply_manual|apply_auto|apply_cancel)(:.+)?$"),
            ],
            WAITING_FOR_AUTO_APPLY_COVER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_auto_apply_cover),
            ],
            WAITING_FOR_AUTO_APPLY_COUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_auto_apply_count),
            ],
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