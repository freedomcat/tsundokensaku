from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Chapter:
    title: str
    level: int
    start_page: int
    end_page: int


def list_chapters(pdf_path: Path) -> list[Chapter]:
    """Return outline entries as chapters with 1-based page ranges.

    A chapter ends on the start page of the next entry at the same or
    shallower level, because chapter text often runs into the page where
    the next chapter begins. Entries with subsections keep their full
    page span. Returns an empty list when the PDF has no outline or
    PyMuPDF is unavailable.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return []

    try:
        doc = fitz.open(str(pdf_path))
    except Exception:
        return []

    try:
        toc = doc.get_toc()
        page_count = doc.page_count
    finally:
        doc.close()

    entries = [
        (int(level), str(title).strip(), int(page))
        for level, title, page in toc
        if 1 <= int(page) <= page_count
    ]

    chapters: list[Chapter] = []
    for index, (level, title, start_page) in enumerate(entries):
        end_page = page_count
        for next_level, _next_title, next_page in entries[index + 1 :]:
            if next_level <= level:
                end_page = max(start_page, next_page)
                break
        chapters.append(
            Chapter(
                title=title or f"p.{start_page}",
                level=level,
                start_page=start_page,
                end_page=end_page,
            )
        )
    return chapters
