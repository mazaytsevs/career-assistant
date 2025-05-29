import httpx
from typing import List, Dict, Any

def get_vacancy_details(vacancy_id: int | str) -> Dict:
    """
    Получает полный JSON вакансии, включая description и key_skills.
    """
    url = f"https://api.hh.ru/vacancies/{vacancy_id}"
    response = httpx.get(url)
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
    **extra_filters: Dict[str, Any]
):
    """
    Выполняет поиск вакансий на hh.ru по заданным фильтрам.

    :param text: (обязательно) Ключевые слова для поиска
    :param experience: (обязательно) Опыт работы ('noExperience', 'between1And3', 'between3And6', 'moreThan6')
    :param employment: (обязательно) Тип занятости ('full', 'part', 'project', 'volunteer', 'probation')
    :param area: (опц.) Регион (по умолчанию None)
    :param schedule: (опц.) График работы ('remote', 'fullDay', 'shift', и т.д.)
    :param salary: (опц.) Минимальная зарплата (целое число)
    :param page: Номер страницы результатов (по умолчанию 0)
    :param per_page: Кол-во результатов на странице (по умолчанию 100)
    :param enrich: Если True, добавляет описание и ключевые навыки к каждой вакансии
    :param extra_filters: Дополнительные фильтры для API
    :return: JSON с результатами поиска
    """
    url = "https://api.hh.ru/vacancies"
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

    response = httpx.get(url, params=params)
    response.raise_for_status()
    data = response.json()

    if enrich:
        enriched_items: List[Dict] = []
        for it in data.get("items", []):
            try:
                details = get_vacancy_details(it["id"])
                it["description"] = details.get("description", "")
                it["key_skills"] = details.get("key_skills", [])
            except Exception:
                # если запрос деталей упал, пропускаем без enrichment
                pass
            enriched_items.append(it)
        data["items"] = enriched_items

    return data


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)

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