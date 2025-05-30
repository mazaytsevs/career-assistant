"""
Сервис для работы с GigaChat API
"""

import os
import logging
from typing import Optional
from langchain_gigachat.chat_models import GigaChat
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

def get_gigachat_client() -> Optional[GigaChat]:
    """Получает клиент GigaChat"""
    token = os.getenv("GIGA_CHAT_ACCESS_KEY")
    if not token:
        logger.error("GIGA_CHAT_ACCESS_KEY не найден в переменных окружения")
        return None
        
    try:
        return GigaChat(verify_ssl_certs=False, credentials=token)
    except Exception as exc:
        logger.error(f"Ошибка при создании клиента GigaChat: {exc}")
        return None

def generate_cover_letter(resume_text: str, vacancy_details: dict) -> Optional[str]:
    """
    Генерирует сопроводительное письмо с помощью GigaChat.
    
    :param resume_text: Текст резюме
    :param vacancy_details: Детали вакансии
    :return: Сгенерированное сопроводительное письмо или None в случае ошибки
    """
    client = get_gigachat_client()
    if not client:
        return None
        
    try:
        prompt = f"""
        Напиши сопроводительное письмо для отклика на вакансию.
        
        Резюме кандидата:
        {resume_text}
        
        Вакансия:
        Название: {vacancy_details['name']}
        Компания: {vacancy_details['employer']['name']}
        Описание: {vacancy_details.get('description', '')}
        Требования: {vacancy_details.get('snippet', {}).get('requirement', '')}
        
        Письмо должно быть:
        1. От первого лица (используй "Я")
        2. Профессиональным и убедительным
        3. Показывать соответствие требованиям вакансии
        4. Содержать конкретные примеры из опыта
        5. Быть не длиннее 300 слов
        """
        
        response = client.invoke(prompt)
        return response.content
        
    except Exception as exc:
        logger.error(f"Ошибка при генерации письма: {exc}")
        return None 