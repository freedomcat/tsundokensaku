from __future__ import annotations

import sqlite3
from pathlib import Path

from tsundokensaku import paths
from tsundokensaku.database import BookRecord, connect, get_book
from tsundokensaku.metadata import find_export_json, load_metadata_by_pdf_stem, metadata_for_pdf


def find_indexed_book(
    connection: sqlite3.Connection,
    resolved_relative_pdf_path: Path,
    *,
    books_dir: Path,
) -> BookRecord | None:
    try:
        for path_candidate in (
            resolved_relative_pdf_path,
            books_dir.expanduser().resolve() / resolved_relative_pdf_path,
        ):
            book = get_book(connection, path=path_candidate)
            if book is not None:
                return book
    except sqlite3.OperationalError:
        return None
    return None


def get_indexed_book(
    resolved_relative_pdf_path: Path,
    *,
    books_dir: Path,
    db_path: Path,
) -> BookRecord | None:
    connection = connect(db_path)
    try:
        return find_indexed_book(connection, resolved_relative_pdf_path, books_dir=books_dir)
    finally:
        connection.close()


def resolve_pdf_scrapbox_url(
    pdf_path: str,
    *,
    books_dir: Path,
    db_path: Path,
    project_root: Path,
) -> str | None:
    relative = paths.resolve_pdf_path(pdf_path, books_dir)
    if relative is None:
        return None

    book = get_indexed_book(relative, books_dir=books_dir, db_path=db_path)
    if book is not None and book.scrapbox_url:
        return book.scrapbox_url

    export_json = find_export_json(project_root)
    metadata = metadata_for_pdf(str(relative), load_metadata_by_pdf_stem(export_json))
    return metadata.scrapbox_url if metadata else None
