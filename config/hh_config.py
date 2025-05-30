"""
Конфигурация для работы с API HH.ru
"""

import os
import json
import time
from typing import Dict, Optional
from dotenv import load_dotenv
import httpx

load_dotenv()

# API HH.ru
HH_CLIENT_ID = os.getenv("HH_CLIENT_ID")
HH_CLIENT_SECRET = os.getenv("HH_CLIENT_SECRET")

# Базовые URL
HH_API_BASE_URL = "https://api.hh.ru"
HH_AUTH_URL = f"{HH_API_BASE_URL}/token"
HH_RESUME_URL = f"{HH_API_BASE_URL}/resumes/mine"
HH_SIMILAR_VACANCIES_URL = f"{HH_API_BASE_URL}/resumes"

# Файл для хранения токенов
TOKENS_FILE = "config/hh_tokens.json"

def get_auth_headers(access_token: str) -> dict:
    """Возвращает заголовки для авторизованных запросов"""

    return {
        "Authorization": f"Bearer {access_token}",

    }

def get_tokens() -> Dict[str, str]:
    """Получает токены из файла или создает новые"""
    if os.path.exists(TOKENS_FILE):
        with open(TOKENS_FILE, 'r') as f:
            tokens = json.load(f)
            # Проверяем, не истек ли access token
            if time.time() < tokens.get('expires_at', 0):
                return tokens
    
    # Если файла нет или токен истек, возвращаем None
    return None

def refresh_tokens(auth_code: str) -> Dict[str, str]:
    """Получает новые токены через OAuth"""
    if not HH_CLIENT_ID or not HH_CLIENT_SECRET:
        raise ValueError("Отсутствуют HH_CLIENT_ID или HH_CLIENT_SECRET в .env файле")

    # Получаем токены
    data = {
        "grant_type": "authorization_code",
        "client_id": HH_CLIENT_ID,
        "client_secret": HH_CLIENT_SECRET,
        "code": auth_code
    }
    
    response = httpx.post(HH_AUTH_URL, data=data)
    response.raise_for_status()
    tokens = response.json()
    
    # Добавляем время истечения токена
    tokens['expires_at'] = time.time() + tokens.get('expires_in', 3600)
    
    return tokens

def get_access_token() -> str:
    """Получает действующий access token"""
    tokens = get_tokens()
    if not tokens:
        raise ValueError("Требуется авторизация в HH.ru. Используйте команду /start и выберите 'Авторизация в HH.ru'")
    return tokens['access_token']

# Проверка конфигурации
def check_hh_config():
    """Проверяет наличие необходимых переменных окружения"""
    required_vars = ["HH_CLIENT_ID", "HH_CLIENT_SECRET"]
    missing = [var for var in required_vars if not os.getenv(var)]
    if missing:
        raise ValueError(f"Отсутствуют необходимые переменные окружения: {', '.join(missing)}") 