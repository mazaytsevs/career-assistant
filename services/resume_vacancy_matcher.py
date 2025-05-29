"""
resume_vacancy_matcher.py
~~~~~~~~~~~~~~~~~~~~~~~~~
Утилиты для «умного» сравнения резюме и вакансии.

Зависимости
-----------
* langchain-gigachat >= 0.0.10  (pip install langchain-gigachat)
* Для работы LLM‑части нужен GIGACHAT_TOKEN в окружении.

Алгоритм
--------
1. LLM (function calling) парсит резюме и вакансию в компактные JSON‑структуры.
2. Считаем скор:
   skills 60 %, experience 15 %, schedule 15 %, prefs 10 %.
3. Для skills берём максимум из:
   ‑ буквального пересечения навыков
   ‑ косинусной близости эмбеддингов GigaChat.

При отсутствии токена или ошибок LLM/эмбеддингов
используются упрощённые эвристики — код не падает.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from typing import Any, Dict, List, Tuple

try:
    from langchain_gigachat.chat_models import GigaChat
    from langchain_gigachat.embeddings import GigaChatEmbeddings
    from langchain.schema import SystemMessage, HumanMessage
except ImportError:  # библиотека может быть не установлена на dev‑машине
    GigaChat = None  # type: ignore
    GigaChatEmbeddings = None  # type: ignore

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

# --------------------------------------------------
# Настраиваемые веса метрик
WEIGHTS = {
    "skills": 0.60,
    "experience": 0.15,
    "schedule": 0.15,
    "prefs": 0.10,
}
EMBEDDING_DIM = 1024  # размер вектора GigaChat на момент написания

# --------------------------------------------------
# Служебные функции
def _cosine(a: List[float], b: List[float]) -> float:
    """Косинусное сходство двух одинаково‑длинных векторов."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


# --------------------------------------------------
# JSON‑схемы для function calling
_RESUME_SCHEMA = {
    "name": "extract_resume",
    "description": "Извлеки из резюме ключевые данные для мэтчинга.",
    "parameters": {
        "type": "object",
        "properties": {
            "skills": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Ключевые навыки кандидата",
            },
            "experience_years": {
                "type": "number",
                "description": "Общий коммерческий стаж в годах",
            },
            "preferred_schedule": {
                "type": "string",
                "description": "Желаемый график (remote, fullDay, etc.)",
            },
            "preferences_raw": {
                "type": "string",
                "description": "Текст пожеланий кандидата",
            },
        },
        "required": ["skills"],
    },
}

_VACANCY_SCHEMA = {
    "name": "extract_vacancy",
    "description": "Извлеки из описания вакансии структурированные данные.",
    "parameters": {
        "type": "object",
        "properties": {
            "skills_required": {"type": "array", "items": {"type": "string"}},
            "experience_level": {"type": "string"},
            "schedule": {"type": "string"},
            "salary": {"type": "number"},
        },
        "required": ["skills_required"],
    },
}


# --------------------------------------------------
# Вспомогательные парсеры
_SKILL_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9+\-#.]{2,}\b")


def _simple_skill_extract(text: str) -> List[str]:
    """Очень грубый fallback‑парсер навыков."""
    return list({m.group(0).lower() for m in _SKILL_RE.finditer(text)})


def _llm_extract(text: str, schema: Dict[str, Any]) -> Dict[str, Any]:
    """LLM‑парсинг текста в JSON по заданной схеме."""
    if not (GigaChat and os.getenv("GIGACHAT_TOKEN")):
        return {}

    llm = GigaChat(credentials=os.getenv("GIGACHAT_TOKEN"), verify_ssl_certs=False)
    messages = [
        SystemMessage(content="Ты JSON‑extractor. Верни аргументы функции строго по схеме."),
        HumanMessage(content=text[:4000]),  # ограничиваем токены
    ]
    try:
        res = llm.invoke(messages, functions=[schema])
        args = res.additional_kwargs.get("function_call", {}).get("arguments")
        result = json.loads(args) if args else {}
        
        # Логируем результат парсинга
        schema_name = schema.get("name", "unknown")
        logger.info(f"Результат парсинга {schema_name}:")
        logger.info(json.dumps(result, indent=2, ensure_ascii=False))
        
        return result
    except Exception as exc:  # noqa: BLE001
        logger.warning("GigaChat extraction failed: %s", exc)
        return {}


def parse_resume(text: str) -> Dict[str, Any]:
    """Вернуть JSON‑структуру резюме."""
    data = _llm_extract(text, _RESUME_SCHEMA)
    if not data:  # fallback
        data = {
            "skills": _simple_skill_extract(text),
            "experience_years": None,
            "preferred_schedule": None,
            "preferences_raw": "",
        }
    return data


def parse_vacancy(text: str) -> Dict[str, Any]:
    """Вернуть JSON‑структуру вакансии."""
    data = _llm_extract(text, _VACANCY_SCHEMA)
    if not data:  # fallback
        data = {
            "skills_required": _simple_skill_extract(text),
            "experience_level": None,
            "schedule": None,
            "salary": None,
        }
    return data


# --------------------------------------------------
# Эмбеддинги и скоринг
def _embed_skills(skills: List[str]) -> List[float]:
    """Получить эмбеддинг набора навыков."""
    if not skills:
        return [0.0] * EMBEDDING_DIM

    if GigaChatEmbeddings and os.getenv("GIGACHAT_TOKEN"):
        try:
            emb = GigaChatEmbeddings(
                credentials=os.getenv("GIGACHAT_TOKEN"), verify_ssl_certs=False
            )
            return emb.embed_query(" ".join(skills))
        except Exception as exc:  # noqa: BLE001
            logger.warning("GigaChat embeddings failed: %s", exc)

    # fallback — плоский вектор
    return [1.0] * EMBEDDING_DIM


# --------------------------------------------------
# Skill matching
def _skill_score(
    resume_skills: List[str], vacancy_skills: List[str]
) -> Tuple[float, List[str], List[str]]:
    """Оценить совпадение навыков и вернуть скор + списки."""
    if not resume_skills or not vacancy_skills:
        return 0.0, [], vacancy_skills

    resume_set = {s.lower() for s in resume_skills}
    vacancy_set = {s.lower() for s in vacancy_skills}
    matched = resume_set & vacancy_set
    literal_ratio = len(matched) / len(vacancy_set) if vacancy_set else 0.0

    # Эмбед‑сходство (учит. синонимы)
    cosine_ratio = _cosine(
        _embed_skills(list(resume_set)),
        _embed_skills(list(vacancy_set)),
    )

    score = max(literal_ratio, cosine_ratio)
    return score, list(matched), list(vacancy_set - matched)


# --------------------------------------------------
# Preferences handling
_NEGATIVE_TRIGGERS = (
    "не хочу",
    "не интересует",
    "не занимаюсь",
    "не хотелось бы",
    "не беру",
    "не нужен",
    "без",
)


def _evaluate_prefs(
    prefs_text: str,
    vacancy_struct: Dict[str, Any],
    raw_text: str = "",
) -> Tuple[float, List[str]]:
    """
    Оценивает, нарушает ли вакансия пожелания кандидата.

    Поддерживаются простые NEG‑предложения, например:
    «не хочу фронтенд», «без онкола», «неинтересен angular».

    Возвращает
    ---------
    score : float  (1.0 если всё ок, 0.0 если есть нарушения)
    violations : list[str]  список сработавших ключевых слов
    """
    if not prefs_text or prefs_text.strip().lower() in {"нет", "none", "no"}:
        return 1.0, []

    lowered_prefs = prefs_text.lower()
    neg_keywords: set[str] = set()

    for trig in _NEGATIVE_TRIGGERS:
        if trig in lowered_prefs:
            # слова после триггера до запятой/точки
            pattern = rf"{trig}\s+([a-zа-я0-9+\-#._ ]{{2,}})"
            for match in re.findall(pattern, lowered_prefs):
                for token in match.replace(",", " ").split():
                    if len(token) >= 3:
                        neg_keywords.add(token)

    # Явные ключевые слова «фронтенд» / frontend
    if "фронтенд" in lowered_prefs or "frontend" in lowered_prefs:
        neg_keywords.update({"фронтенд", "frontend", "front‑end", "front_end"})

    violations: list[str] = []
    skills_lower = {s.lower() for s in vacancy_struct.get("skills_required", [])}
    raw_lower = raw_text.lower()

    for kw in neg_keywords:
        if kw in skills_lower or kw in raw_lower:
            violations.append(kw)

    return (0.0 if violations else 1.0), violations


# --------------------------------------------------
# Публичная функция мэтчинга
def match_resume_to_vacancy(
    resume: Dict[str, Any],
    vacancy: Dict[str, Any],
    prefs_text: str | None = None,
    weights: Dict[str, float] | None = None,
) -> Dict[str, Any]:
    """
    Сравнить резюме и вакансию, вернуть JSON с подробностями.

    :param resume: результат parse_resume()
    :param vacancy: результат parse_vacancy()
    :param prefs_text: свободный текст пожеланий пользователя
    :param weights: кастомные веса метрик (иначе WEIGHTS)
    """
    w = weights or WEIGHTS

    # --- Skills ---
    skills_score, matched, missing = _skill_score(
        resume.get("skills", []), vacancy.get("skills_required", [])
    )

    # --- Experience ---
    exp_score = 1.0
    if vacancy.get("experience_level"):
        lvl = vacancy["experience_level"]
        years = resume.get("experience_years")
        mapping = {
            "noExperience": (0, 0),
            "between1And3": (1, 3),
            "between3And6": (3, 6),
            "moreThan6": (6, 100),
        }
        if years is not None and lvl in mapping:
            low, high = mapping[lvl]
            exp_score = 1.0 if low <= years <= high else 0.0

    # --- Schedule ---
    sched_score = 1.0
    res_sched = resume.get("preferred_schedule")
    if res_sched and vacancy.get("schedule"):
        sched_score = 1.0 if res_sched == vacancy["schedule"] else 0.0

    # --- Preferences ---
    prefs_score, pref_violations = _evaluate_prefs(
        prefs_text or "",
        vacancy,
        raw_text=vacancy.get("raw_text", ""),
    )

    # --- Итог ---
    final = (
        skills_score * w["skills"]
        + exp_score * w["experience"]
        + sched_score * w["schedule"]
        + prefs_score * w["prefs"]
    )

    result = {
        "score": round(final, 3),
        "skills_score": round(skills_score, 3),
        "experience_ok": bool(exp_score),
        "schedule_ok": bool(sched_score),
        "prefs_ok": bool(prefs_score),
        "matched_skills": matched,
        "missing_skills": missing,
        "pref_violations": pref_violations,
    }
    
    # Логируем результаты сравнения
    logger.info("Результаты сравнения резюме и вакансии:")
    logger.info(json.dumps(result, indent=2, ensure_ascii=False))
    
    return result