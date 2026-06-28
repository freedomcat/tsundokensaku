from __future__ import annotations

from collections.abc import Iterator
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
from tsundokensaku.pdf_extract import extract_pages


@dataclass(frozen=True)
class IndexedBook:
    path: Path
    title: str
    page_count: int


def find_pdfs(books_dir: Path) -> Iterator[Path]:
    if not books_dir.exists():
        return
    yield from sorted(path for path in books_dir.rglob("*.pdf") if path.is_file())


def index_books(*, books_dir: Path, db_path: Path) -> list[IndexedBook]:
    connection = connect(db_path)
    initialize(connection)

    indexed: list[IndexedBook] = []
    pdf_paths = list(find_pdfs(books_dir))
    if not pdf_paths:
        print(f"No PDF files found under {books_dir}")
        connection.close()
        return indexed

    total = len(pdf_paths)
    print(f"Indexing {total} PDF files under {books_dir}")

    current_paths = {str(path.resolve()) for path in pdf_paths}
    skipped = 0
    updated = 0

    for index, pdf_path in enumerate(pdf_paths, start=1):
        stat = pdf_path.stat()
        title = pdf_path.stem
        existing = get_book(connection, path=pdf_path)
        resolved_path = str(pdf_path.resolve())

        if existing and existing.size_bytes == stat.st_size and existing.modified_at == stat.st_mtime:
            skipped += 1
            print(f"[{index}/{total}] SKIP {title}")
            continue

        action = "UPDATE" if existing else "INDEX"
        print(f"[{index}/{total}] {action} {title}")

        book_id = upsert_book(
            connection,
            path=pdf_path,
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
        print(f"[{index}/{total}] DONE {title} ({page_count} pages)")

    removed = 0
    for book in list_books(connection):
        if str(Path(book.path).resolve()) in current_paths:
            continue
        delete_book(connection, book_id=book.id)
        removed += 1
        print(f"REMOVE {book.title}")

    print(f"Done: indexed={updated}, skipped={skipped}, removed={removed}")

    connection.close()
    return indexed
