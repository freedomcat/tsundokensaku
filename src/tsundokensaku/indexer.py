from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from tsundokensaku.database import (
    PageRecord,
    connect,
    delete_book,
    get_book,
    initialize,
    list_books,
    replace_pages,
    upsert_book,
)
from tsundokensaku.metadata import find_export_json, load_metadata_by_pdf_stem, resolve_pdf_display_title
from tsundokensaku.pdf_extract import extract_pages


@dataclass(frozen=True)
class IndexedBook:
    path: Path
    title: str
    page_count: int


def _progress_bar(current: int, total: int, *, width: int = 24) -> str:
    total = max(total, 1)
    current = min(max(current, 0), total)
    filled = round(width * current / total)
    return f"[{'#' * filled}{'.' * (width - filled)}] {current}/{total}"


def _emit_progress(current: int, total: int, message: str) -> None:
    line = f"{_progress_bar(current, total)} {message}"
    if sys.stdout.isatty():
        end = "\r" if current < total else "\n"
        print(line, end=end, flush=True)
    else:
        print(line)


def find_pdfs(books_dir: Path) -> Iterator[Path]:
    if not books_dir.exists():
        return
    yield from sorted(path for path in books_dir.rglob("*.pdf") if path.is_file())


def index_books(
    *,
    books_dir: Path,
    db_path: Path,
    progress_callback: Callable[[bool, int, int, str, str], None] | None = None,
    force_paths: set[str] | None = None,
) -> list[IndexedBook]:
    return index_books_with_progress(
        books_dir=books_dir,
        db_path=db_path,
        progress_callback=progress_callback,
        force_paths=force_paths,
    )


def index_books_with_progress(
    *,
    books_dir: Path,
    db_path: Path,
    progress_callback: Callable[[bool, int, int, str, str], None] | None = None,
    force_paths: set[str] | None = None,
) -> list[IndexedBook]:
    connection = connect(db_path)
    initialize(connection)
    metadata_by_stem = load_metadata_by_pdf_stem(find_export_json(Path(__file__).resolve().parents[2]))

    indexed: list[IndexedBook] = []
    pdf_paths = list(find_pdfs(books_dir))
    if not pdf_paths:
        print(f"No PDF files found under {books_dir}")
        connection.close()
        return indexed

    total = len(pdf_paths)
    print(f"Indexing {total} PDF files under {books_dir}")
    if progress_callback is not None:
        progress_callback(True, 0, total, "", "準備中")

    current_paths = {str(path.resolve()) for path in pdf_paths}
    skipped = 0
    updated = 0

    for index, pdf_path in enumerate(pdf_paths, start=1):
        stat = pdf_path.stat()
        title = resolve_pdf_display_title(pdf_path, metadata_by_stem)
        existing = get_book(connection, path=pdf_path)
        forced = force_paths is not None and (
            str(pdf_path) in force_paths or str(pdf_path.resolve()) in force_paths
        )

        if (
            not forced
            and existing
            and existing.size_bytes == stat.st_size
            and existing.modified_at == stat.st_mtime
            and existing.title == title
            and existing.filename == pdf_path.name
        ):
            skipped += 1
            _emit_progress(index, total, f"SKIP {title}")
            if progress_callback is not None:
                progress_callback(True, index, total, title, f"SKIP {title}")
            continue

        action = "FORCE" if forced and existing else ("UPDATE" if existing else "INDEX")
        _emit_progress(index, total, f"{action} {title}")
        if progress_callback is not None:
            progress_callback(True, index, total, title, f"{action} {title}")

        book_id = upsert_book(
            connection,
            path=pdf_path,
            filename=pdf_path.name,
            title=title,
            size_bytes=stat.st_size,
            modified_at=stat.st_mtime,
        )
        pages = [
            PageRecord(page_number=page.page_number, text=page.text)
            for page in extract_pages(pdf_path)
        ]
        page_count = replace_pages(connection, book_id=book_id, title=title, pages=pages)
        indexed.append(IndexedBook(path=pdf_path, title=title, page_count=page_count))
        updated += 1
        _emit_progress(index, total, f"DONE {title} ({page_count} pages)")
        if progress_callback is not None:
            progress_callback(True, index, total, title, f"DONE {title} ({page_count} pages)")

    removed = 0
    for book in list_books(connection):
        if book.source_type != "pdf" or book.path is None:
            continue
        if str(Path(book.path).resolve()) in current_paths:
            continue
        delete_book(connection, book_id=book.id)
        removed += 1
        print(f"REMOVE {book.title}")

    print(f"Done: indexed={updated}, skipped={skipped}, removed={removed}")
    if progress_callback is not None:
        progress_callback(False, total, total, "", f"Done: indexed={updated}, skipped={skipped}, removed={removed}")

    connection.close()
    return indexed
