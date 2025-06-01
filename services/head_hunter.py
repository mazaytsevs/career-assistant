"""
Сервис для работы с API HH.ru
"""

import httpx
import logging
from typing import List, Dict, Any, Optional
from config.hh_config import (
    HH_API_BASE_URL,
    HH_SIMILAR_VACANCIES_URL,
    get_auth_headers,
    check_hh_config,
    get_access_token,
)
from config.logger import setup_logger

logger = setup_logger(__name__)

def get_vacancy_details(vacancy_id: int | str) -> Dict:
    """
    Получает полный JSON вакансии, включая description и key_skills.
    """
    url = f"{HH_API_BASE_URL}/vacancies/{vacancy_id}"
    headers = get_auth_headers(get_access_token())
    response = httpx.get(url, headers=headers)
    response.raise_for_status()
    return response.json()

def auto_apply_vacancies(
    resume_id: int | str,
    keywords: str,
    count: int,
    experience: str | None,
    employment: str | None,
    schedule: str | None,
    salary: int | None,
    prefs: str | None,
    cover_letter: str | None
) -> bool:
    """
    Настраивает автоотклик на вакансии с заданными параметрами.
    
    :param resume_id: ID резюме пользователя
    :param keywords: Ключевые слова для поиска
    :param count: Количество вакансий для отклика
    :param experience: Опыт работы
    :param employment: Тип занятости
    :param schedule: График работы
    :param salary: Минимальная зарплата
    :param prefs: Предпочтения пользователя
    :param cover_letter: Сопроводительное письмо
    :return: True если автоотклик успешно настроен, False в случае ошибки
    """
    # Логируем все собранные данные
    
    logger.info("=== Собранные данные для автоотклика ===")
    logger.info(f"айди вакансии: {resume_id}")

    logger.info(f"Ключевые слова: {keywords}")
    logger.info(f"Опыт работы: {experience}")
    logger.info(f"Тип занятости: {employment}")
    logger.info(f"График работы: {schedule}")
    logger.info(f"Зарплата: {salary}")
    logger.info(f"Предпочтения: {prefs}")
    logger.info(f"Сопроводительное письмо: {cover_letter}")
    logger.info(f"Количество вакансий: {count}")
    logger.info("=======================================")

    pages = [0]
    per_page = []

    if count > 100 and count <= 200:
        pages.append(1)
        per_page.append(100)
        per_page.append(count - 100)
    elif count <= 200:
        per_page.append(count)


    print(f"pages: {pages}")

    for page in pages:
        vacancies = get_similar_vacancies(
            resume_id=resume_id,
            text=keywords,
            experience=experience,
            employment=employment,
            schedule=schedule,
            salary=salary,
            page=page,
            per_page=per_page[page],
        )
        print(f"vacancies: {vacancies['items']}")
        for vacancy in vacancies['items']:
            apply_for_vacancy(vacancy['id'], resume_id, cover_letter)

    return True

def get_similar_vacancies(
    resume_id: str,
    text: str | None = None,
    experience: str | None = None,
    employment: str | None = None,
    area: int | None = None,
    schedule: str | None = None,
    salary: int | None = None,
    page: int = 0,
    per_page: int = 20,
    **extra_filters: Dict[str, Any]
) -> Dict:
    """
    Получает вакансии, похожие на резюме.
    
    :param resume_id: ID резюме в HH.ru
    :param text: Ключевые слова для поиска
    :param experience: Опыт работы
    :param employment: Тип занятости
    :param area: Регион
    :param schedule: График работы
    :param salary: Минимальная зарплата
    :param page: Номер страницы
    :param per_page: Количество вакансий на странице
    :param extra_filters: Дополнительные фильтры
    :return: JSON с результатами поиска
    """
    url = f"{HH_SIMILAR_VACANCIES_URL}/{resume_id}/similar_vacancies"
    
    headers = get_auth_headers(get_access_token())
    params = {
        "page": page,
        "per_page": per_page,
    }
    
    # Добавляем параметры поиска, если они указаны
    if text:
        params["text"] = text
    if experience:
        params["experience"] = experience
    if employment:
        params["employment"] = employment
    if area is not None:
        params["area"] = area
    if schedule:
        params["schedule"] = schedule
    if salary is not None:
        params["salary"] = salary
        
    # Добавляем дополнительные фильтры
    for key, value in extra_filters.items():
        params[key] = value
    
    response = httpx.get(url, headers=headers, params=params)
    response.raise_for_status()
    return response.json()

def search_vacancies(
    text: str,
    experience: str,
    employment: str,
    area: int | None = None,
    schedule: str | None = None,
    salary: int | None = None,
    page: int = 0,
    per_page: int = 100,
    enrich: bool = False,
    resume_id: str | None = None,
    **extra_filters: Dict[str, Any]
) -> Dict:
    """
    Выполняет поиск вакансий на hh.ru по заданным фильтрам.
    Если указан resume_id, использует API похожих вакансий.

    :param text: (обязательно) Ключевые слова для поиска
    :param experience: (обязательно) Опыт работы ('noExperience', 'between1And3', 'between3And6', 'moreThan6')
    :param employment: (обязательно) Тип занятости ('full', 'part', 'project', 'volunteer', 'probation')
    :param area: (опц.) Регион (по умолчанию None)
    :param schedule: (опц.) График работы ('remote', 'fullDay', 'shift', и т.д.)
    :param salary: (опц.) Минимальная зарплата (целое число)
    :param page: Номер страницы результатов (по умолчанию 0)
    :param per_page: Кол-во результатов на странице (по умолчанию 100)
    :param enrich: Если True, добавляет описание и ключевые навыки к каждой вакансии
    :param resume_id: ID резюме в HH.ru для поиска похожих вакансий
    :param extra_filters: Дополнительные фильтры для API
    :return: JSON с результатами поиска
    """
    # Проверяем конфигурацию
    check_hh_config()
    
    # Если есть resume_id, используем API похожих вакансий
    if resume_id:
        data = get_similar_vacancies(
            resume_id=resume_id,
            text=text,
            experience=experience,
            employment=employment,
            area=area,
            schedule=schedule,
            salary=salary,
            page=page,
            per_page=per_page,
            **extra_filters
        )
    else:
        # Обычный поиск по параметрам
        url = f"{HH_API_BASE_URL}/vacancies"
        headers = get_auth_headers(get_access_token())
        params = {
            "text": text,
            "experience": experience,
            "employment": employment,
            "page": page,
            "per_page": per_page
        }

        if area is not None:
            params["area"] = area
        if schedule:
            params["schedule"] = schedule
        if salary is not None:
            params["salary"] = salary

        for key, value in extra_filters.items():
            params[key] = value

        response = httpx.get(url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()

    if enrich:
        enriched_items: List[Dict] = []
        for it in data.get("items", []):
            try:
                details = get_vacancy_details(it["id"])
                it["description"] = details.get("description", "")
                it["key_skills"] = details.get("key_skills", [])
            except Exception as exc:
                logger.warning(f"Ошибка при получении деталей вакансии {it['id']}: {exc}")
                # если запрос деталей упал, пропускаем без enrichment
                pass
            enriched_items.append(it)
        data["items"] = enriched_items

    return data

def get_resume_list() -> List[Dict]:
    """
    Получает список резюме пользователя.
    """
    headers = get_auth_headers(get_access_token())
    print(f"headers: {headers}")    
    print(f"{HH_API_BASE_URL}/resumes/mine")
    response = httpx.get(f"{HH_API_BASE_URL}/resumes/mine", headers=headers)
    response.raise_for_status()
    return response.json()

def get_resume_details(resume_id: str) -> Dict:
    """
    Получает детали резюме по ID.
    """
    headers = get_auth_headers(get_access_token())
    response = httpx.get(f"{HH_API_BASE_URL}/resumes/{resume_id}", headers=headers)
    response.raise_for_status()
    return response.json()

def apply_for_vacancy(vacancy_id: str, resume_id: str, message: str = None) -> bool:
    """
    Отправляет отклик на вакансию.
    
    :param vacancy_id: ID вакансии
    :param resume_id: ID резюме
    :param message: Сопроводительное письмо (опционально)
    :return: True если отклик успешно отправлен, False в случае ошибки
    """
    try:
        
        url = f"{HH_API_BASE_URL}/negotiations"
        headers = get_auth_headers(get_access_token())
        
        data = {
            'vacancy_id': vacancy_id,
            'resume_id': resume_id
        }
        
        if message:
            data['message'] = message
            
        # Отправляем форму multipart/form-data
        files = {
            "vacancy_id": (None, vacancy_id),
            "resume_id": (None, resume_id),
            "message": (None, message or "")
        }
        response = httpx.post(url, headers=headers, files=files)
        
        if response.status_code == 201:
            logger.info(f"Успешный отклик на вакансию {vacancy_id} с резюме {resume_id}")
            return True
        else:
            logger.error(f"Ошибка при отклике на вакансию: {response.status_code} - {response.text}")
            return False
            
    except Exception as exc:
        logger.error(f"Ошибка при отклике на вакансию: {exc}")
        return False

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)

    # Тест поиска вакансий
    result = search_vacancies(
        text="node js backend разработчик",
        experience="between3And6",
        employment="full",
        schedule="remote",
        enrich=True
    )

    for item in result.get("items", []):
        logging.info(f"{item['name']} — {item['employer']['name']}")
        if item.get("key_skills"):
            logging.info("Skills: %s", ", ".join(k['name'] for k in item['key_skills']))
        logging.info(f"URL: {item['alternate_url']}\n")