import httpx
from typing import List, Dict

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
    area: int = None,
    experience: str = None,
    employment: str = None,
    schedule: str = None,
    page: int = 0,
    per_page: int = 20,
    enrich: bool = False
):
    """
    Выполняет поиск вакансий на hh.ru по заданным фильтрам.

    :param text: Ключевые слова для поиска
    :param area: Регион (по умолчанию 1 — Москва)
    :param experience: Опыт работы ('noExperience', 'between1And3', 'between3And6', 'moreThan6')
    :param employment: Тип занятости ('full', 'part', 'project', 'volunteer', 'probation')
    :param schedule: График работы ('remote', 'fullDay', 'shift', и т.д.)
    :param page: Номер страницы результатов
    :param per_page: Кол-во результатов на странице
    :param enrich: Если True, добавляет описание и ключевые навыки к каждой вакансии
    :return: JSON с результатами поиска
    """
    url = "https://api.hh.ru/vacancies"
    params = {
        "text": text,
        "page": page,
        "per_page": per_page
    }

    if area is not None:
        params["area"] = area
    if experience:
        params["experience"] = experience
    if employment:
        params["employment"] = employment
    if schedule:
        params["schedule"] = schedule

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
        schedule="remote",
        enrich=True
    )

    for item in result.get("items", []):
        logging.info(f"{item['name']} — {item['employer']['name']}")
        if item.get("key_skills"):
            logging.info("Skills: %s", ", ".join(k['name'] for k in item['key_skills']))
        logging.info(f"URL: {item['alternate_url']}\n")