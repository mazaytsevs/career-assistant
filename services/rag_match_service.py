"""
RAG‑утилиты для сопоставления резюме и вопросов.

* Чанкуем текст резюме (≈ 800‑1000 символов, overlap 100).
* Кэшируем эмбеддинги в Chroma – если такое `resume_id`
  уже есть в векторной базе, повторно вектора не считаем.
* Демо‑запрос: «Какими технологиями обладает соискатель?»

Зависимости (есть в requirements.txt):
    langchain
    langchain-gigachat
    langchain-chroma
"""

import hashlib
import os
import warnings
import logging
from pathlib import Path
from typing import List, Optional

from langchain import hub
from langchain.schema import HumanMessage
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_chroma import Chroma
from langchain_gigachat.chat_models import GigaChat
from langchain_gigachat.embeddings.gigachat import GigaChatEmbeddings
from dotenv import load_dotenv

# Отключаем предупреждения LangSmith
warnings.filterwarnings("ignore", category=UserWarning, module="langsmith")

# Загружаем переменные окружения
load_dotenv()

# ----- load GigaChat credentials -----
GIGACHAT_CREDENTIALS = os.getenv("GIGA_CHAT_ACCESS_KEY") or os.getenv("GIGACHAT_TOKEN")
if not GIGACHAT_CREDENTIALS:
    raise EnvironmentError("Не найден токен GigaChat. Установи переменную GIGA_CHAT_ACCESS_KEY.")

# ---------- config ---------- #
CHROMA_DIR = Path("data") / "chroma_resume"
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100
RESUME_PREVIEW_LENGTH = 100  # Количество символов для хеширования

logger = logging.getLogger(__name__)

class RAGService:
    def __init__(self):
        self.llm = GigaChat(verify_ssl_certs=False, credentials=GIGACHAT_CREDENTIALS)
        self.embeddings = GigaChatEmbeddings(
            credentials=GIGACHAT_CREDENTIALS, 
            verify_ssl_certs=False
        )
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
            add_start_index=True
        )
        self.db = None
        self.prompt = hub.pull("rlm/rag-prompt")
        self._initialize_db()

    def _initialize_db(self):
        """Инициализация базы данных"""
        CHROMA_DIR.mkdir(parents=True, exist_ok=True)
        self.db = Chroma(
            collection_name="resumes",
            embedding_function=self.embeddings,
            persist_directory=str(CHROMA_DIR)
        )

    def _generate_resume_id(self, resume_text: str, user_id: str | int) -> str:
        """Генерация уникального ID резюме на основе хеша начала текста и ID пользователя"""
        preview = resume_text[:RESUME_PREVIEW_LENGTH].strip()
        preview_hash = hashlib.md5(preview.encode("utf-8")).hexdigest()[:8]
        return f"{user_id}_{preview_hash}"

    def _is_duplicate(self, resume_text: str, user_id: str | int) -> bool:
        """Проверка на дубликат резюме"""
        resume_id = self._generate_resume_id(resume_text, user_id)
        existing = self.db.get(where={"resume_id": {"$eq": resume_id}})
        is_duplicate = bool(existing and existing["ids"])
        if is_duplicate:
            logger.info(f"Найдено существующее резюме с ID={resume_id}")
        return is_duplicate

    def index_resume_if_needed(self, resume_text: str, user_id: str | int) -> str:
        """
        Кладёт чанки резюме в Chroma, если такого резюме ещё нет.
        Возвращает ID резюме.
        """
        # Генерируем ID для резюме
        resume_id = self._generate_resume_id(resume_text, user_id)
        
        # Проверяем на дубликат
        if self._is_duplicate(resume_text, user_id):
            return resume_id

        # Разбиваем текст на чанки
        chunks = self.text_splitter.split_text(resume_text)
        
        # Создаем метаданные для каждого чанка
        metas = [
            {
                "resume_id": resume_id,
                "user_id": str(user_id),
                "chunk_ix": ix,
            }
            for ix in range(len(chunks))
        ]
        
        # Добавляем чанки в базу
        self.db.add_texts(chunks, metadatas=metas)
        logger.info(f"Сохранено новое резюме с ID={resume_id}")
        return resume_id

    def ask_resume(self, question: str, resume_id: str, k: int = 6) -> str:
        """
        Делает RAG‑запрос к конкретному резюме.
        """
        # Получаем релевантные документы
        retrieved_docs = self.db.similarity_search(
            question,
            k=k,
            filter={"resume_id": {"$eq": resume_id}}
        )
        
        # Формируем контекст из документов
        docs_content = "\n\n".join(doc.page_content for doc in retrieved_docs)
        
        # Формируем промпт с контекстом
        messages = self.prompt.invoke({
            "question": question, 
            "context": docs_content
        })
        
        # Получаем ответ от модели
        response = self.llm.invoke(messages)
        return response.content

# Создаем глобальный экземпляр сервиса
rag_service = RAGService()

# Для обратной совместимости
def index_resume_if_needed(resume_text: str, user_id: str | int) -> str:
    """Алиас для обратной совместимости"""
    return rag_service.index_resume_if_needed(resume_text, user_id)

def ask_resume(question: str, resume_id: str, k: int = 6) -> str:
    """Алиас для обратной совместимости"""
    return rag_service.ask_resume(question, resume_id, k)

# ---------- demo ---------- #
if __name__ == "__main__":
    # Тестовый запрос к существующему резюме
    test_resume_id = "673970524_f8b84e54"
    test_question = """Создай сопроводительное письмо от первого лица для отклика на вакансию. 
    В письме подробно опиши:
    1. Мой опыт работы с технологиями
    2. Мои ключевые навыки
    3. Почему я подхожу на позицию
    Используй информацию из моего резюме."""
    
    print(f"\nТестовый запрос к резюме {test_resume_id}")
    print(f"Вопрос: {test_question}")
    answer = ask_resume(test_question, test_resume_id)
    print("\n--- Ответ LLM ---\n", answer)
