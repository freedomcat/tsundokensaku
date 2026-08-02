from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pypdf import PdfReader

from tsundokensaku import paths
from tsundokensaku.database import connect
from tsundokensaku.markdown_export import default_markdown_output_name, render_markdown_pages
from tsundokensaku.pdf_export import parse_page_selection
from tsundokensaku.pdf_extract import extract_pages
from tsundokensaku.pdf_metadata_service import get_indexed_book


@dataclass(frozen=True)
class PdfPageSearchHit:
    page_number: int
    snippet: str


@dataclass(frozen=True)
class PdfPageSearchResult:
    indexed: bool
    pages: tuple[PdfPageSearchHit, ...]


def load_pages_text(
    candidate: Path,
    page_numbers: list[int],
    *,
    books_dir: Path,
    db_path: Path,
) -> dict[int, str]:
    texts: dict[int, str] = {}
    relative = paths.resolve_pdf_path(candidate, books_dir)
    book = None
    if relative is not None:
        book = get_indexed_book(relative, books_dir=books_dir, db_path=db_path)

    if book is not None and page_numbers:
        connection = connect(db_path)
        try:
            placeholders = ",".join("?" for _ in page_numbers)
            rows = connection.execute(
                f"SELECT page_number, text FROM pages WHERE book_id = ? AND page_number IN ({placeholders})",
                [book.id, *page_numbers],
            ).fetchall()
            texts = {int(row["page_number"]): str(row["text"]) for row in rows}
        except sqlite3.OperationalError:
            texts = {}
        finally:
            connection.close()

    missing = {number for number in page_numbers if number not in texts}
    if missing:
        for page in extract_pages(candidate):
            if page.page_number in missing:
                texts[page.page_number] = page.text
    return texts


def _page_snippet(text: str, query: str, *, width: int = 80) -> str:
    flat = " ".join(text.split())
    index = flat.lower().find(query.lower())
    if index < 0:
        return flat[:width]
    start = max(0, index - 20)
    end = min(len(flat), index + len(query) + width - 20)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(flat) else ""
    return f"{prefix}{flat[start:end]}{suffix}"


def search_book_pages(
    candidate: Path,
    query: str,
    *,
    books_dir: Path,
    db_path: Path,
    limit: int = 100,
) -> PdfPageSearchResult:
    relative = paths.resolve_pdf_path(candidate, books_dir)
    if relative is None:
        return PdfPageSearchResult(indexed=False, pages=())

    book = get_indexed_book(relative, books_dir=books_dir, db_path=db_path)
    if book is None:
        return PdfPageSearchResult(indexed=False, pages=())

    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    connection = connect(db_path)
    try:
        rows = connection.execute(
            "SELECT page_number, text FROM pages "
            "WHERE book_id = ? AND text LIKE ? ESCAPE '\\' "
            "ORDER BY page_number LIMIT ?",
            (book.id, f"%{escaped}%", limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return PdfPageSearchResult(indexed=False, pages=())
    finally:
        connection.close()

    return PdfPageSearchResult(
        indexed=True,
        pages=tuple(
            PdfPageSearchHit(
                page_number=int(row["page_number"]),
                snippet=_page_snippet(str(row["text"]), query),
            )
            for row in rows
        ),
    )


def render_markdown_export(
    candidate: Path,
    pages: str,
    *,
    books_dir: Path,
    db_path: Path,
    exported_at: datetime,
) -> tuple[str, str]:
    page_spec = pages.strip()
    if not page_spec:
        raise ValueError("pages is required")

    page_numbers = parse_page_selection(page_spec, len(PdfReader(str(candidate)).pages))
    relative = paths.resolve_pdf_path(candidate, books_dir)
    book = None
    if relative is not None:
        book = get_indexed_book(relative, books_dir=books_dir, db_path=db_path)
    title = book.title if book is not None else candidate.stem
    texts = load_pages_text(candidate, page_numbers, books_dir=books_dir, db_path=db_path)
    content = render_markdown_pages(
        title=title,
        source_name=candidate.name,
        page_numbers=page_numbers,
        texts=texts,
        exported_at=exported_at,
    )
    return content, default_markdown_output_name(candidate, page_numbers)
