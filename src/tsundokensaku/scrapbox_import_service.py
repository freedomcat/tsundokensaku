from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tsundokensaku import database


@dataclass(frozen=True)
class ScrapboxImportResult:
    imported_memos: int
    imported_kindle_books: int


class ScrapboxExportNotFoundError(ValueError):
    pass


def import_scrapbox_export_bytes(
    content: bytes,
    *,
    cache_path: Path,
    db_path: Path,
) -> ScrapboxImportResult:
    cache_path.write_bytes(content)
    return _sync_scrapbox_export(cache_path=cache_path, db_path=db_path)


def import_scrapbox_export_file(
    source: Path | None,
    *,
    cache_path: Path,
    db_path: Path,
) -> ScrapboxImportResult:
    if source is None:
        raise ScrapboxExportNotFoundError()

    source_path = source.expanduser()
    if not source_path.exists():
        raise ScrapboxExportNotFoundError()

    source_path = source_path.resolve()
    if source_path != cache_path.expanduser().resolve():
        cache_path.write_bytes(source_path.read_bytes())

    return _sync_scrapbox_export(cache_path=cache_path, db_path=db_path)


def _sync_scrapbox_export(*, cache_path: Path, db_path: Path) -> ScrapboxImportResult:
    connection = database.connect(db_path)
    try:
        database.initialize(connection)
        imported_memos = database.sync_memos(connection, cache_path)
        imported_kindle_books = database.sync_kindle_books(connection, cache_path)
    finally:
        connection.close()

    return ScrapboxImportResult(
        imported_memos=imported_memos,
        imported_kindle_books=imported_kindle_books,
    )
