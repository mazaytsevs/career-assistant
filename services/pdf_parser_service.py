

"""
PDF → TEXT helper.

Алгоритм:
1) Быстрый чек, есть ли текстовый слой (pypdf).
2) Если есть → вытягиваем текст через pdfminer.six (точнее).
3) Если текста нет → PDF‑>images (pdf2image) → OCR (pytesseract).

Зависимости (добавь в requirements.txt если их ещё нет):
    pdfminer.six
    pypdf
    pdf2image
    pytesseract
    pillow   # вытягивается pdf2image

Для OCR нужен установленный бинарь Tesseract + языковые
   пакеты rus и eng.
"""

from pathlib import Path
from typing import Union, List

import logging

from pypdf import PdfReader
from pdfminer.high_level import extract_text as pdfminer_extract_text

from pdf2image import convert_from_path
from PIL import Image  # noqa: pillow
import pytesseract

logger = logging.getLogger(__name__)


# ---------- helpers ---------- #
def _pdf_has_text(path: Union[str, Path], max_pages: int = 3) -> bool:
    """True, if first `max_pages` contain any extractable text."""
    reader = PdfReader(str(path))
    for page in reader.pages[:max_pages]:
        if (page.extract_text() or "").strip():
            return True
    return False


def _extract_text_layer(path: Union[str, Path]) -> str:
    """Extracts text from a PDF with text layer via pdfminer (unicode‑aware)."""
    logger.info("Extracting text layer with pdfminer…")
    try:
        return pdfminer_extract_text(str(path)) or ""
    except Exception as exc:
        logger.warning("pdfminer failed with %s — fallback to OCR", type(exc).__name__)
        return ""


def _ocr_pdf(path: Union[str, Path], dpi: int = 300, languages: str = "rus+eng") -> str:
    """OCR each page image and concatenate result."""
    logger.info("Running OCR via Tesseract on scanned PDF…")
    images: List[Image.Image] = convert_from_path(str(path), dpi=dpi)
    texts: List[str] = []
    for idx, img in enumerate(images):
        # Препроцессинг: серый + простой порог — повышает качество
        gray = img.convert("L")
        text = pytesseract.image_to_string(gray, lang=languages, config="--psm 4")
        logger.debug("OCR page %d len=%d", idx + 1, len(text))
        texts.append(text)
    return "\n".join(texts)


# ---------- public API ---------- #
def pdf_to_text(path: Union[str, Path]) -> str:
    """
    Main entry: returns best‑effort text extracted from PDF.

    :param path: str | pathlib.Path to .pdf
    :return: str (can be empty if completely unrecognisable)
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)

    if _pdf_has_text(path):
        text = _extract_text_layer(path)
        if text.strip():
            logger.info("Finished with text layer extraction, length=%d chars", len(text))
            return text

    # Fallback to OCR
    text = _ocr_pdf(path)
    logger.info("Finished OCR extraction, length=%d chars", len(text))
    return text