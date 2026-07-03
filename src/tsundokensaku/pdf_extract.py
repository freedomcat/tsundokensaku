from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ExtractedPage:
    page_number: int
    text: str


class _UnsupportedEncodingDetector(logging.Handler):
    """Flags pypdf's "Advanced encoding ... not implemented" errors.

    pypdf logs these instead of raising, so extract_text() silently
    returns garbled text for legacy CID fonts (e.g. non-Identity
    Adobe-Japan1 CMaps like "H") instead of failing.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.triggered = False

    def emit(self, record: logging.LogRecord) -> None:
        self.triggered = True


def _extract_with_pypdf(page: object) -> tuple[str, bool]:
    detector = _UnsupportedEncodingDetector()
    logger = logging.getLogger("pypdf")
    logger.addHandler(detector)
    try:
        text = page.extract_text() or ""  # type: ignore[attr-defined]
    finally:
        logger.removeHandler(detector)
    return text, detector.triggered


def _open_fitz_doc(pdf_path: Path):
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return None
    try:
        return fitz.open(str(pdf_path))
    except Exception:
        return None


def extract_pages(pdf_path: Path) -> Iterator[ExtractedPage]:
    """Yield 1-based PDF pages with extracted text.

    Extracts with pypdf by default. If pypdf can't decode a page's
    CID font encoding (garbled text instead of an error), that page
    is re-extracted with PyMuPDF as a fallback.
    """
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    fitz_doc = None
    try:
        for index, page in enumerate(reader.pages, start=1):
            text, unsupported_encoding = _extract_with_pypdf(page)
            if unsupported_encoding:
                if fitz_doc is None:
                    fitz_doc = _open_fitz_doc(pdf_path)
                if fitz_doc is not None:
                    text = fitz_doc[index - 1].get_text() or text
            yield ExtractedPage(page_number=index, text=normalize_text(text))
    finally:
        if fitz_doc is not None:
            fitz_doc.close()


def normalize_text(text: str) -> str:
    lines = [line.strip() for line in text.replace("\x00", "").splitlines()]
    return "\n".join(line for line in lines if line)
