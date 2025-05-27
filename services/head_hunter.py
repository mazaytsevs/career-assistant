import httpx

def search_vacancies(
    text: str,
    area: int = None,
    experience: str = None,
    employment: str = None,
    schedule: str = None,
    page: int = 1,
    per_page: int = 20
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
    return response.json()


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)

    result = search_vacancies(
        text="node js backend разработчик",
        experience="between3And6",
        schedule="remote"
    )

    for item in result.get("items", []):
        logging.info(f"{item['name']} — {item['employer']['name']}")
        logging.info(f"URL: {item['alternate_url']}\n")