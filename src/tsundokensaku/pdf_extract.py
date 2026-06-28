from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ExtractedPage:
    page_number: int
    text: str


def extract_pages(pdf_path: Path) -> Iterator[ExtractedPage]:
    """Yield 1-based PDF pages with extracted text."""
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    for index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        yield ExtractedPage(page_number=index, text=normalize_text(text))


def normalize_text(text: str) -> str:
    lines = [line.strip() for line in text.replace("\x00", "").splitlines()]
    return "\n".join(line for line in lines if line)
