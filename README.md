# Career Assistant

## Настройка

1. Создайте файл `.env` в корне проекта и добавьте необходимые переменные окружения:

```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_chat_id
HH_CLIENT_ID=your_hh_client_id
HH_CLIENT_SECRET=your_hh_client_secret
GIGACHAT_TOKEN=your_gigachat_token
```

2. При первом запуске после авторизации в HH.ru будет создан файл `config/hh_tokens.json` с токенами доступа. Этот файл не должен попадать в git репозиторий.

## Установка

```bash
pip install -r requirements.txt
```

## Запуск

```bash
python main.py
```

Python-проект для автоматизации откликов на вакансии.
Включает Telegram-бота, логирование и модульную структуру.
