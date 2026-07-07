from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from tsundokensaku.metadata import (
    BookMetadata,
    KindleBookMetadata,
    ScrapboxMemo,
    load_kindle_books,
    load_scrapbox_memos,
    resolve_pdf_display_title,
)
from tsundokensaku.tokenizer import build_excerpt, normalize_trigram_text, prepare_index_text, tokenize_text


@dataclass(frozen=True)
class BookRecord:
    id: int
    path: str | None
    filename: str | None
    source_type: str
    external_id: str | None
    title: str
    size_bytes: int | None
    modified_at: float | None
    indexed_at: str
    open_url: str | None = None
    scrapbox_url: str | None = None
    cover_url: str | None = None


@dataclass(frozen=True)
class PdfTitleRefreshTarget:
    id: int
    path: str
    filename: str | None
    current_title: str
    resolved_title: str


@dataclass(frozen=True)
class PageRecord:
    page_number: int
    text: str


@dataclass(frozen=True)
class BookNoteRecord:
    title: str
    body: str
    scrapbox_url: str | None = None
    cover_url: str | None = None


@dataclass(frozen=True)
class PackRecord:
    id: int
    name: str
    note: str
    created_at: str
    updated_at: str
    archived_at: str | None = None
    book_count: int = 0


@dataclass(frozen=True)
class PackItemRecord:
    pdf_path: str
    title: str
    pages: str
    collapsed: bool
    position: int
    added_at: str
    updated_at: str


@dataclass(frozen=True)
class SearchResult:
    title: str
    path: str | None
    page_number: int | None
    snippet: str
    kind: str = "pdf"
    open_url: str | None = None
    scrapbox_url: str | None = None
    cover_url: str | None = None


SEARCH_SCOPES = {"all", "title", "body", "memo"}
SEARCH_MATCH_MODES = {"all", "any"}


@dataclass(frozen=True)
class QueryTerm:
    text: str
    phrase: bool = False
    exclude: bool = False


def parse_query(query: str) -> list[QueryTerm]:
    """検索クエリを語単位に分解する。

    - 空白区切りの各チャンクが1語
    - "..." で囲むとフレーズ（語順・隣接を保った一致）
    - 先頭 - で除外（-語 / -"フレーズ"）
    """
    parts = re.findall(r'-?"[^"]*"|\S+', query)
    terms: list[QueryTerm] = []
    for part in parts:
        exclude = part.startswith("-")
        if exclude:
            part = part[1:]
        phrase = len(part) >= 2 and part.startswith('"') and part.endswith('"')
        text = part.strip('"').strip()
        if not text:
            continue
        terms.append(QueryTerm(text=text, phrase=phrase, exclude=exclude))
    return terms


def connect(db_path: Path) -> sqlite3.Connection:
    db_path = db_path.resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        connection = sqlite3.connect(db_path)
    except sqlite3.OperationalError as exc:
        raise RuntimeError(
            "Could not open the SQLite database.\n"
            f"DB path: {db_path}\n"
            f"Original error: {exc}\n\n"
            "If Windows Security is blocking writes under the project folder, "
            "try a shorter external DB path, for example:\n"
            '  py -3.13 -m tsundokensaku index --books-dir "C:\\tsundokensaku-books\\tech" '
            '--db "C:\\tsundokensaku-books\\index.db"'
        ) from exc
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def initialize(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys = OFF")
    _ensure_books_schema(connection)
    _ensure_pages_schema(connection)
    _ensure_memo_schema(connection)
    _ensure_book_notes_schema(connection)
    _ensure_search_schema(connection)
    _ensure_pack_schema(connection)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.commit()


def upsert_book(
    connection: sqlite3.Connection,
    *,
    path: Path | None = None,
    filename: str | None = None,
    title: str,
    size_bytes: int | None = None,
    modified_at: float | None = None,
    source_type: str = "pdf",
    external_id: str | None = None,
    open_url: str | None = None,
    scrapbox_url: str | None = None,
    cover_url: str | None = None,
) -> int:
    indexed_at = datetime.now(timezone.utc).isoformat()
    if source_type == "pdf":
        if path is None:
            raise ValueError("PDF books require a path.")
        if size_bytes is None or modified_at is None:
            raise ValueError("PDF books require size_bytes and modified_at.")
        filename = filename or path.name
        connection.execute(
            """
            INSERT INTO books(path, filename, source_type, external_id, title, size_bytes, modified_at, indexed_at, open_url, scrapbox_url, cover_url)
            VALUES (?, ?, 'pdf', NULL, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                filename = excluded.filename,
                title = excluded.title,
                size_bytes = excluded.size_bytes,
                modified_at = excluded.modified_at,
                indexed_at = excluded.indexed_at,
                open_url = excluded.open_url,
                scrapbox_url = excluded.scrapbox_url,
                cover_url = excluded.cover_url
            """,
            (str(path), filename, title, size_bytes, modified_at, indexed_at, open_url, scrapbox_url, cover_url),
        )
        row = connection.execute("SELECT id FROM books WHERE path = ?", (str(path),)).fetchone()
    else:
        if external_id is None:
            raise ValueError("Non-PDF books require an external_id.")
        connection.execute(
            """
            INSERT INTO books(path, filename, source_type, external_id, title, size_bytes, modified_at, indexed_at, open_url, scrapbox_url, cover_url)
            VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_type, external_id) DO UPDATE SET
                filename = excluded.filename,
                title = excluded.title,
                size_bytes = excluded.size_bytes,
                modified_at = excluded.modified_at,
                indexed_at = excluded.indexed_at,
                open_url = excluded.open_url,
                scrapbox_url = excluded.scrapbox_url,
                cover_url = excluded.cover_url
            """,
            (filename, source_type, external_id, title, size_bytes, modified_at, indexed_at, open_url, scrapbox_url, cover_url),
        )
        row = connection.execute(
            "SELECT id FROM books WHERE source_type = ? AND external_id = ?",
            (source_type, external_id),
        ).fetchone()
    if row is None:
        raise RuntimeError(f"Failed to store book: {path or external_id}")
    _replace_book_search_index(connection, book_id=int(row["id"]), title=title)
    return int(row["id"])


def get_book(connection: sqlite3.Connection, *, path: Path) -> BookRecord | None:
    columns = _table_columns(connection, "books")
    filename_expr = "filename" if "filename" in columns else "NULL AS filename"
    row = connection.execute(
        """
        SELECT id, path, {filename_expr}, source_type, external_id, title, size_bytes, modified_at, indexed_at, open_url, scrapbox_url, cover_url
        FROM books
        WHERE path = ?
        """.format(filename_expr=filename_expr),
        (str(path),),
    ).fetchone()
    if row is None:
        return None
    return BookRecord(
        id=int(row["id"]),
        path=str(row["path"]) if row["path"] is not None else None,
        filename=str(row["filename"]) if "filename" in row.keys() and row["filename"] is not None else None,
        source_type=str(row["source_type"]),
        external_id=str(row["external_id"]) if row["external_id"] is not None else None,
        title=str(row["title"]),
        size_bytes=(int(row["size_bytes"]) if row["size_bytes"] is not None else None),
        modified_at=(float(row["modified_at"]) if row["modified_at"] is not None else None),
        indexed_at=str(row["indexed_at"]),
        open_url=str(row["open_url"]) if row["open_url"] is not None else None,
        scrapbox_url=str(row["scrapbox_url"]) if row["scrapbox_url"] is not None else None,
        cover_url=str(row["cover_url"]) if row["cover_url"] is not None else None,
    )


def list_books(connection: sqlite3.Connection) -> list[BookRecord]:
    columns = _table_columns(connection, "books")
    filename_expr = "filename" if "filename" in columns else "NULL AS filename"
    rows = connection.execute(
        """
        SELECT id, path, {filename_expr}, source_type, external_id, title, size_bytes, modified_at, indexed_at, open_url, scrapbox_url, cover_url
        FROM books
        ORDER BY title, path
        """.format(filename_expr=filename_expr)
    ).fetchall()
    return [
        BookRecord(
            id=int(row["id"]),
            path=str(row["path"]) if row["path"] is not None else None,
            filename=str(row["filename"]) if "filename" in row.keys() and row["filename"] is not None else None,
            source_type=str(row["source_type"]),
            external_id=str(row["external_id"]) if row["external_id"] is not None else None,
            title=str(row["title"]),
            size_bytes=(int(row["size_bytes"]) if row["size_bytes"] is not None else None),
            modified_at=(float(row["modified_at"]) if row["modified_at"] is not None else None),
            indexed_at=str(row["indexed_at"]),
            open_url=str(row["open_url"]) if row["open_url"] is not None else None,
            scrapbox_url=str(row["scrapbox_url"]) if row["scrapbox_url"] is not None else None,
            cover_url=str(row["cover_url"]) if row["cover_url"] is not None else None,
        )
        for row in rows
    ]


def delete_book(connection: sqlite3.Connection, *, book_id: int) -> None:
    page_ids = [int(row["id"]) for row in connection.execute("SELECT id FROM pages WHERE book_id = ?", (book_id,)).fetchall()]
    if page_ids:
        placeholders = ",".join("?" for _ in page_ids)
        connection.execute(f"DELETE FROM pages_fts WHERE rowid IN ({placeholders})", page_ids)
        connection.execute(f"DELETE FROM pages_trigram WHERE rowid IN ({placeholders})", page_ids)
    connection.execute("DELETE FROM books_fts WHERE rowid = ?", (book_id,))
    connection.execute("DELETE FROM books WHERE id = ?", (book_id,))
    connection.commit()


def replace_pages(
    connection: sqlite3.Connection,
    *,
    book_id: int,
    title: str,
    pages: Iterable[PageRecord],
) -> int:
    page_ids = [int(row["id"]) for row in connection.execute("SELECT id FROM pages WHERE book_id = ?", (book_id,)).fetchall()]
    if page_ids:
        placeholders = ",".join("?" for _ in page_ids)
        connection.execute(f"DELETE FROM pages_fts WHERE rowid IN ({placeholders})", page_ids)
        connection.execute(f"DELETE FROM pages_trigram WHERE rowid IN ({placeholders})", page_ids)
    connection.execute("DELETE FROM pages WHERE book_id = ?", (book_id,))

    count = 0
    for page in pages:
        cursor = connection.execute(
            "INSERT INTO pages(book_id, page_number, text) VALUES (?, ?, ?)",
            (book_id, page.page_number, page.text),
        )
        connection.execute(
            """
            INSERT INTO pages_fts(rowid, book_id, page_number, title, text)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                cursor.lastrowid,
                book_id,
                page.page_number,
                prepare_index_text(title),
                prepare_index_text(page.text),
            ),
        )
        connection.execute(
            """
            INSERT INTO pages_trigram(rowid, text)
            VALUES (?, ?)
            """,
            (
                cursor.lastrowid,
                normalize_trigram_text(page.text),
            ),
        )
        count += 1

    connection.commit()
    return count


def replace_book_notes(
    connection: sqlite3.Connection,
    *,
    book_id: int,
    notes: Iterable[BookNoteRecord],
) -> int:
    connection.execute("DELETE FROM book_notes WHERE book_id = ?", (book_id,))
    connection.execute("DELETE FROM book_notes_fts WHERE book_id = ?", (book_id,))

    count = 0
    for note in notes:
        cursor = connection.execute(
            """
            INSERT INTO book_notes(book_id, title, body, scrapbox_url, cover_url, indexed_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                book_id,
                note.title,
                note.body,
                note.scrapbox_url,
                note.cover_url,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        connection.execute(
            """
            INSERT INTO book_notes_fts(rowid, book_id, title, body)
            VALUES (?, ?, ?, ?)
            """,
            (
                cursor.lastrowid,
                book_id,
                prepare_index_text(note.title),
                prepare_index_text(note.body),
            ),
        )
        count += 1

    connection.commit()
    return count


def replace_memos(connection: sqlite3.Connection, memos: Iterable[ScrapboxMemo]) -> int:
    connection.execute("DELETE FROM memos")
    connection.execute("DELETE FROM memos_fts")

    count = 0
    for memo in memos:
        cursor = connection.execute(
            """
            INSERT INTO memos(title, body, scrapbox_url, cover_url)
            VALUES (?, ?, ?, ?)
            """,
            (memo.title, memo.body, memo.scrapbox_url, memo.cover_url),
        )
        connection.execute(
            """
            INSERT INTO memos_fts(rowid, memo_id, title, body)
            VALUES (?, ?, ?, ?)
            """,
            (
                cursor.lastrowid,
                cursor.lastrowid,
                prepare_index_text(memo.title),
                prepare_index_text(memo.body),
            ),
        )
        count += 1

    connection.commit()
    return count


def sync_memos(connection: sqlite3.Connection, export_json: Path | None, *, project_url: str | None = None) -> int:
    if export_json is None or not export_json.exists():
        return 0

    source_path = str(export_json.resolve())
    source_mtime = export_json.stat().st_mtime
    current = connection.execute(
        "SELECT source_path, source_mtime FROM memo_sources WHERE id = 1"
    ).fetchone()
    if current and current["source_path"] == source_path and float(current["source_mtime"]) == source_mtime:
        return 0

    memos = load_scrapbox_memos(export_json, project_url=project_url)
    replace_memos(connection, memos)
    indexed_at = datetime.now(timezone.utc).isoformat()
    connection.execute(
        """
        INSERT INTO memo_sources(id, source_path, source_mtime, indexed_at)
        VALUES (1, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            source_path = excluded.source_path,
            source_mtime = excluded.source_mtime,
            indexed_at = excluded.indexed_at
        """,
        (source_path, source_mtime, indexed_at),
    )
    connection.commit()
    return len(memos)


def sync_kindle_books(connection: sqlite3.Connection, export_json: Path | None, *, project_url: str | None = None) -> int:
    kindle_books = load_kindle_books(export_json, project_url=project_url)
    for book in kindle_books:
        upsert_book(
            connection,
            path=None,
            source_type="kindle",
            external_id=book.external_id,
            title=book.title,
            open_url=book.kindle_url,
            scrapbox_url=book.scrapbox_url,
            cover_url=book.cover_url,
        )
    connection.commit()
    return len(kindle_books)


def list_pdf_title_refresh_targets(
    connection: sqlite3.Connection,
    metadata_by_stem: dict[str, BookMetadata],
    *,
    pdf_path: str | Path | None = None,
    path_like: str | None = None,
) -> list[PdfTitleRefreshTarget]:
    where_clauses = ["source_type = 'pdf'", "path IS NOT NULL"]
    parameters: list[object] = []

    if pdf_path is not None:
        raw_path = str(pdf_path)
        path_candidates = [raw_path, str(Path(raw_path).resolve())]
        placeholders = ", ".join("?" for _ in path_candidates)
        where_clauses.append(f"path IN ({placeholders})")
        parameters.extend(path_candidates)

    if path_like:
        like_value = f"%{path_like}%"
        where_clauses.append("(path LIKE ? OR filename LIKE ?)")
        parameters.extend([like_value, like_value])

    rows = connection.execute(
        f"""
        SELECT id, path, filename, title
        FROM books
        WHERE {" AND ".join(where_clauses)}
        ORDER BY id
        """,
        parameters,
    ).fetchall()

    targets: list[PdfTitleRefreshTarget] = []
    for row in rows:
        path = str(row["path"])
        targets.append(
            PdfTitleRefreshTarget(
                id=int(row["id"]),
                path=path,
                filename=str(row["filename"]) if row["filename"] is not None else None,
                current_title=str(row["title"]),
                resolved_title=resolve_pdf_display_title(path, metadata_by_stem),
            )
        )
    return targets


def refresh_pdf_titles(
    connection: sqlite3.Connection,
    metadata_by_stem: dict[str, BookMetadata],
    *,
    pdf_path: str | Path | None = None,
    path_like: str | None = None,
) -> int:
    targets = list_pdf_title_refresh_targets(
        connection,
        metadata_by_stem,
        pdf_path=pdf_path,
        path_like=path_like,
    )

    updated = 0
    for target in targets:
        if target.current_title == target.resolved_title:
            continue

        connection.execute(
            "UPDATE books SET title = ? WHERE id = ?",
            (target.resolved_title, target.id),
        )
        _replace_book_search_index(connection, book_id=target.id, title=target.resolved_title)
        connection.execute(
            "UPDATE pages_fts SET title = ? WHERE book_id = ?",
            (prepare_index_text(target.resolved_title), target.id),
        )
        updated += 1

    if updated:
        connection.commit()
    return updated


def search(
    connection: sqlite3.Connection,
    query: str,
    *,
    limit: int = 20,
    scope: str = "all",
    match: str = "all",
) -> list[SearchResult]:
    normalized_query = query.strip()
    if not normalized_query:
        return []

    normalized_scope = scope if scope in SEARCH_SCOPES else "all"
    normalized_match = match if match in SEARCH_MATCH_MODES else "all"
    if normalized_scope == "title":
        try:
            rows = _search_title(connection, normalized_query, limit=limit, match=normalized_match)
        except sqlite3.OperationalError:
            rows = []
    elif normalized_scope == "body":
        try:
            rows = _search_body(connection, normalized_query, limit=limit, match=normalized_match)
        except sqlite3.OperationalError:
            rows = []
    elif normalized_scope == "memo":
        try:
            rows = _search_memo(connection, normalized_query, limit=limit, match=normalized_match)
        except sqlite3.OperationalError:
            rows = []
    else:
        try:
            title_rows = _search_title(connection, normalized_query, limit=limit, match=normalized_match)
        except sqlite3.OperationalError:
            title_rows = []
        try:
            body_rows = _search_body(connection, normalized_query, limit=limit, match=normalized_match)
        except sqlite3.OperationalError:
            body_rows = []
        try:
            note_rows = _search_book_notes(connection, normalized_query, limit=limit, match=normalized_match)
        except sqlite3.OperationalError:
            note_rows = []
        try:
            memo_rows = _search_memo(connection, normalized_query, limit=limit, match=normalized_match)
        except sqlite3.OperationalError:
            memo_rows = []
        rows = title_rows + body_rows + note_rows + memo_rows

    return [
        SearchResult(
            title=row["title"],
            path=row["path"],
            page_number=(int(row["page_number"]) if row["page_number"] is not None else None),
            snippet=_build_search_snippet(row, normalized_query),
            kind=str(row["kind"]) if "kind" in row.keys() and row["kind"] is not None else "pdf",
            open_url=row["open_url"] if "open_url" in row.keys() else None,
            scrapbox_url=row["scrapbox_url"] if "scrapbox_url" in row.keys() else None,
            cover_url=row["cover_url"] if "cover_url" in row.keys() else None,
        )
        for row in rows
    ]


def _search_title(connection: sqlite3.Connection, query: str, *, limit: int, match: str = "all") -> list[sqlite3.Row]:
    rows: list[sqlite3.Row] = []
    try:
        rows.extend(_search_title_fts(connection, query, limit=limit, match=match))
    except sqlite3.OperationalError:
        pass
    try:
        rows.extend(_search_title_like(connection, query, limit=limit, match=match))
    except sqlite3.OperationalError:
        pass
    return _dedupe_rows(rows, limit=limit)


def _search_title_fts(connection: sqlite3.Connection, query: str, *, limit: int, match: str = "all") -> list[sqlite3.Row]:
    fts_query = _to_fts_query(query, match=match)
    if not fts_query:
        return []
    return list(
        connection.execute(
            """
            SELECT
                b.title,
                COALESCE(b.path, b.external_id, b.title) AS path,
                CASE WHEN b.source_type = 'pdf' THEN 1 ELSE NULL END AS page_number,
                b.title AS snippet,
                b.source_type AS kind,
                b.open_url,
                b.scrapbox_url,
                b.cover_url
            FROM books_fts AS f
            JOIN books AS b ON b.id = f.rowid
            WHERE books_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (fts_query, limit),
        )
    )


def _search_title_like(connection: sqlite3.Connection, query: str, *, limit: int, match: str = "all") -> list[sqlite3.Row]:
    where_clause, parameters = _like_terms_clause(query, columns=("title",), match=match)
    if not where_clause:
        return []
    return list(
        connection.execute(
            f"""
            SELECT
                title,
                COALESCE(path, external_id, title) AS path,
                CASE WHEN source_type = 'pdf' THEN 1 ELSE NULL END AS page_number,
                title AS snippet,
                source_type AS kind,
                open_url,
                scrapbox_url,
                cover_url
            FROM books
            WHERE {where_clause}
            ORDER BY title, path
            LIMIT ?
            """,
            tuple(parameters + [limit]),
        )
    )


def _search_body(connection: sqlite3.Connection, query: str, *, limit: int, match: str = "all") -> list[sqlite3.Row]:
    rows: list[sqlite3.Row] = []
    try:
        rows.extend(_search_body_fts(connection, query, limit=limit, match=match))
    except sqlite3.OperationalError:
        pass
    try:
        rows.extend(_search_body_trigram(connection, query, limit=limit, match=match))
    except sqlite3.OperationalError:
        pass
    return _dedupe_rows(rows, limit=limit)


def _search_body_fts(connection: sqlite3.Connection, query: str, *, limit: int, match: str = "all") -> list[sqlite3.Row]:
    fts_query = _to_scoped_fts_query(query, column="text", match=match)
    if not fts_query:
        return []
    return list(
        connection.execute(
            """
            SELECT
                b.title,
                b.path,
                f.page_number,
                p.text AS body_text,
                'pdf' AS kind
            FROM pages_fts AS f
            JOIN pages AS p ON p.id = f.rowid
            JOIN books AS b ON b.id = f.book_id
            WHERE pages_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (fts_query, limit),
        )
    )


def _search_body_trigram(connection: sqlite3.Connection, query: str, *, limit: int, match: str = "all") -> list[sqlite3.Row]:
    trigram_query = _to_trigram_query(query, match=match)
    if not trigram_query:
        return []
    return list(
        connection.execute(
            """
            SELECT
                b.title,
                b.path,
                p.page_number,
                p.text AS body_text,
                'pdf' AS kind
            FROM pages_trigram AS t
            JOIN pages AS p ON p.id = t.rowid
            JOIN books AS b ON b.id = p.book_id
            WHERE pages_trigram MATCH ?
            ORDER BY b.title, p.page_number
            LIMIT ?
            """,
            (trigram_query, limit),
        )
    )


def _search_memo(connection: sqlite3.Connection, query: str, *, limit: int, match: str = "all") -> list[sqlite3.Row]:
    try:
        return _search_memo_fts(connection, query, limit=limit, match=match)
    except sqlite3.OperationalError:
        return _search_memo_like(connection, query, limit=limit, match=match)


def _search_memo_fts(connection: sqlite3.Connection, query: str, *, limit: int, match: str = "all") -> list[sqlite3.Row]:
    fts_query = _to_fts_query(query, match=match)
    if not fts_query:
        return []
    return list(
        connection.execute(
            """
            SELECT
                title,
                title AS path,
                NULL AS page_number,
                snippet(memos_fts, 2, '[', ']', ' ... ', 24) AS snippet,
                'memo' AS kind,
                scrapbox_url AS open_url,
                scrapbox_url,
                cover_url
            FROM memos_fts
            JOIN memos ON memos.id = memos_fts.memo_id
            WHERE memos_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (fts_query, limit),
        )
    )


def _search_memo_like(connection: sqlite3.Connection, query: str, *, limit: int, match: str = "all") -> list[sqlite3.Row]:
    where_clause, parameters = _like_terms_clause(query, columns=("title", "body"), match=match)
    if not where_clause:
        return []
    return list(
        connection.execute(
            f"""
            SELECT
                title,
                title AS path,
                NULL AS page_number,
                body AS snippet,
                'memo' AS kind,
                scrapbox_url AS open_url,
                scrapbox_url,
                cover_url
            FROM memos
            WHERE {where_clause}
            ORDER BY title
            LIMIT ?
            """,
            tuple(parameters + [limit]),
        )
    )


def _search_book_notes(connection: sqlite3.Connection, query: str, *, limit: int, match: str = "all") -> list[sqlite3.Row]:
    try:
        return _search_book_notes_fts(connection, query, limit=limit, match=match)
    except sqlite3.OperationalError:
        return _search_book_notes_like(connection, query, limit=limit, match=match)


def _search_book_notes_fts(connection: sqlite3.Connection, query: str, *, limit: int, match: str = "all") -> list[sqlite3.Row]:
    fts_query = _to_fts_query(query, match=match)
    if not fts_query:
        return []
    return list(
        connection.execute(
            """
            SELECT
                b.title,
                n.title AS path,
                NULL AS page_number,
                n.title || char(10) || n.body AS body_text,
                'note' AS kind,
                n.scrapbox_url AS open_url,
                n.scrapbox_url AS scrapbox_url,
                n.cover_url AS cover_url
            FROM book_notes_fts AS f
            JOIN book_notes AS n ON n.id = f.rowid
            JOIN books AS b ON b.id = f.book_id
            WHERE book_notes_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (fts_query, limit),
        )
    )


def _search_book_notes_like(connection: sqlite3.Connection, query: str, *, limit: int, match: str = "all") -> list[sqlite3.Row]:
    where_clause, parameters = _like_terms_clause(query, columns=("n.title", "n.body"), match=match)
    if not where_clause:
        return []
    return list(
        connection.execute(
            f"""
            SELECT
                b.title,
                n.title AS path,
                NULL AS page_number,
                n.title || char(10) || n.body AS body_text,
                'note' AS kind,
                n.scrapbox_url AS open_url,
                n.scrapbox_url AS scrapbox_url,
                n.cover_url AS cover_url
            FROM book_notes AS n
            JOIN books AS b ON b.id = n.book_id
            WHERE {where_clause}
            ORDER BY b.title, n.title
            LIMIT ?
            """,
            tuple(parameters + [limit]),
        )
    )


def _split_query_terms(query: str) -> tuple[list[QueryTerm], list[QueryTerm]]:
    includes: list[QueryTerm] = []
    excludes: list[QueryTerm] = []
    for term in parse_query(query):
        (excludes if term.exclude else includes).append(term)
    return includes, excludes


def _fts_term_expression(term: QueryTerm, *, prefix: str = "") -> str:
    # OR の単位はユーザーが空白区切りした語。Sudachi が1語を複数トークンに
    # 分割しても、そのトークン群は同一グループとして常に AND で結合する。
    # フレーズ語はトークン列を1つの FTS5 フレーズ（隣接一致）にする。
    tokens = [token.replace('"', '""') for token in tokenize_text(term.text) if token]
    if not tokens:
        return ""
    if term.phrase:
        quoted = '"{}"'.format(" ".join(tokens))
        return f"{prefix}:{quoted}" if prefix else quoted
    quoted = " ".join(f'"{token}"' for token in tokens)
    if prefix:
        return f"{prefix}:({quoted})"
    return f"({quoted})" if len(tokens) > 1 else quoted


def _build_fts_query(query: str, *, match: str = "all", prefix: str = "") -> str:
    includes, excludes = _split_query_terms(query)
    include_exprs = [expr for expr in (_fts_term_expression(term, prefix=prefix) for term in includes) if expr]
    if not include_exprs:
        # 除外語だけの検索は結果なし扱い
        return ""
    separator = " OR " if match == "any" else " "
    expression = separator.join(include_exprs)
    exclude_exprs = [expr for expr in (_fts_term_expression(term, prefix=prefix) for term in excludes) if expr]
    if exclude_exprs:
        expression = f"({expression})" + "".join(f" NOT ({expr})" for expr in exclude_exprs)
    return expression


def _to_fts_query(query: str, *, match: str = "all") -> str:
    return _build_fts_query(query, match=match)


def _to_scoped_fts_query(query: str, *, column: str, match: str = "all") -> str:
    return _build_fts_query(query, match=match, prefix=column)


def _to_trigram_query(query: str, *, match: str = "all") -> str:
    includes, excludes = _split_query_terms(query)
    include_phrases: list[str] = []
    for term in includes:
        normalized = normalize_trigram_text(term.text)
        if len(normalized) < 3:
            # trigram で表現できない短い語。AND では全語一致を保証できないため
            # trigram 検索自体を諦める（FTS 側が短い語を拾う）。OR では単に読み飛ばす。
            if match != "any":
                return ""
            continue
        include_phrases.append('"{}"'.format(normalized.replace('"', '""')))
    if not include_phrases:
        return ""
    exclude_phrases: list[str] = []
    for term in excludes:
        normalized = normalize_trigram_text(term.text)
        if len(normalized) < 3:
            # 除外語を trigram で表現できないと除外漏れが起きるため trigram 検索ごと諦める
            return ""
        exclude_phrases.append('"{}"'.format(normalized.replace('"', '""')))
    separator = " OR " if match == "any" else " "
    expression = separator.join(include_phrases)
    if exclude_phrases:
        expression = f"({expression})" + "".join(f" NOT {phrase}" for phrase in exclude_phrases)
    return expression


def _like_terms_clause(query: str, *, columns: tuple[str, ...], match: str = "all") -> tuple[str, list[str]]:
    includes, excludes = _split_query_terms(query)
    if not includes:
        return "", []
    clauses: list[str] = []
    parameters: list[str] = []
    for term in includes:
        clauses.append("(" + " OR ".join(f"{column} LIKE ?" for column in columns) + ")")
        parameters.extend([f"%{term.text}%"] * len(columns))
    connector = " OR " if match == "any" else " AND "
    clause = "(" + connector.join(clauses) + ")"
    for term in excludes:
        clause += " AND NOT (" + " OR ".join(f"{column} LIKE ?" for column in columns) + ")"
        parameters.extend([f"%{term.text}%"] * len(columns))
    return clause, parameters


DEFAULT_PACK_NAME = "無題の資料"


def _pack_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_pack(connection: sqlite3.Connection, *, name: str, note: str = "") -> int:
    now = _pack_now()
    cursor = connection.execute(
        "INSERT INTO packs(name, note, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (name.strip() or DEFAULT_PACK_NAME, note, now, now),
    )
    connection.commit()
    return int(cursor.lastrowid)


def get_pack(connection: sqlite3.Connection, pack_id: int) -> PackRecord | None:
    row = connection.execute(
        """
        SELECT p.id, p.name, p.note, p.created_at, p.updated_at, p.archived_at,
               (SELECT COUNT(*) FROM pack_items i WHERE i.pack_id = p.id) AS book_count
        FROM packs p
        WHERE p.id = ?
        """,
        (pack_id,),
    ).fetchone()
    if row is None:
        return None
    return _pack_record_from_row(row)


def list_packs(connection: sqlite3.Connection) -> list[PackRecord]:
    rows = connection.execute(
        """
        SELECT p.id, p.name, p.note, p.created_at, p.updated_at, p.archived_at,
               (SELECT COUNT(*) FROM pack_items i WHERE i.pack_id = p.id) AS book_count
        FROM packs p
        WHERE p.archived_at IS NULL
        ORDER BY p.updated_at DESC, p.id DESC
        """
    ).fetchall()
    return [_pack_record_from_row(row) for row in rows]


def update_pack(connection: sqlite3.Connection, pack_id: int, *, name: str | None = None, note: str | None = None) -> bool:
    if get_pack(connection, pack_id) is None:
        return False
    assignments: list[str] = []
    parameters: list[object] = []
    if name is not None and name.strip():
        assignments.append("name = ?")
        parameters.append(name.strip())
    if note is not None:
        assignments.append("note = ?")
        parameters.append(note)
    if not assignments:
        return True
    assignments.append("updated_at = ?")
    parameters.extend([_pack_now(), pack_id])
    connection.execute(f"UPDATE packs SET {', '.join(assignments)} WHERE id = ?", parameters)
    connection.commit()
    return True


def delete_pack(connection: sqlite3.Connection, pack_id: int) -> bool:
    if get_pack(connection, pack_id) is None:
        return False
    connection.execute("DELETE FROM pack_items WHERE pack_id = ?", (pack_id,))
    connection.execute("DELETE FROM packs WHERE id = ?", (pack_id,))
    if get_active_pack_id(connection) == pack_id:
        connection.execute("DELETE FROM app_state WHERE key = 'active_pack_id'")
    connection.commit()
    return True


def get_active_pack_id(connection: sqlite3.Connection) -> int | None:
    row = connection.execute("SELECT value FROM app_state WHERE key = 'active_pack_id'").fetchone()
    if row is None:
        return None
    try:
        return int(row["value"])
    except (TypeError, ValueError):
        return None


def set_active_pack(connection: sqlite3.Connection, pack_id: int) -> bool:
    if get_pack(connection, pack_id) is None:
        return False
    connection.execute(
        """
        INSERT INTO app_state(key, value) VALUES ('active_pack_id', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (str(pack_id),),
    )
    connection.commit()
    return True


def ensure_active_pack(connection: sqlite3.Connection) -> int:
    """アクティブパックIDを返す。無効・未設定なら既存の最新パックか新規デフォルトパックを充てる。"""
    active_id = get_active_pack_id(connection)
    if active_id is not None and get_pack(connection, active_id) is not None:
        return active_id
    packs = list_packs(connection)
    pack_id = packs[0].id if packs else create_pack(connection, name=DEFAULT_PACK_NAME)
    set_active_pack(connection, pack_id)
    return pack_id


def get_pack_items(connection: sqlite3.Connection, pack_id: int) -> list[PackItemRecord]:
    rows = connection.execute(
        """
        SELECT pdf_path, title, pages, collapsed, position, added_at, updated_at
        FROM pack_items
        WHERE pack_id = ?
        ORDER BY position, id
        """,
        (pack_id,),
    ).fetchall()
    return [
        PackItemRecord(
            pdf_path=str(row["pdf_path"]),
            title=str(row["title"]),
            pages=str(row["pages"]),
            collapsed=bool(row["collapsed"]),
            position=int(row["position"]),
            added_at=str(row["added_at"]),
            updated_at=str(row["updated_at"]),
        )
        for row in rows
    ]


def replace_pack_items(connection: sqlite3.Connection, pack_id: int, books: dict) -> bool:
    """パックの内容をカート形式の books 辞書で丸ごと置き換える。

    既存の pdf_path は added_at / position を保持し、消えたものは削除、
    新規は末尾に追加する。クライアントの save(cart) に対応する一括書込み。
    """
    if get_pack(connection, pack_id) is None:
        return False
    if not isinstance(books, dict):
        return False
    now = _pack_now()
    existing = {item.pdf_path: item for item in get_pack_items(connection, pack_id)}
    next_position = max((item.position for item in existing.values()), default=-1) + 1

    for pdf_path in set(existing) - set(books):
        connection.execute("DELETE FROM pack_items WHERE pack_id = ? AND pdf_path = ?", (pack_id, pdf_path))

    for pdf_path, entry in books.items():
        if not isinstance(pdf_path, str) or not pdf_path or not isinstance(entry, dict):
            continue
        title = entry.get("title") if isinstance(entry.get("title"), str) and entry.get("title") else pdf_path
        pages = entry.get("pages") if isinstance(entry.get("pages"), str) else ""
        collapsed = 1 if entry.get("collapsed") else 0
        current = existing.get(pdf_path)
        if current is not None:
            connection.execute(
                """
                UPDATE pack_items SET title = ?, pages = ?, collapsed = ?, updated_at = ?
                WHERE pack_id = ? AND pdf_path = ?
                """,
                (title, pages, collapsed, now, pack_id, pdf_path),
            )
        else:
            added_at = entry.get("addedAt") if isinstance(entry.get("addedAt"), str) and entry.get("addedAt") else now
            connection.execute(
                """
                INSERT INTO pack_items(pack_id, pdf_path, title, pages, collapsed, position, added_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (pack_id, pdf_path, title, pages, collapsed, next_position, added_at, now),
            )
            next_position += 1

    connection.execute("UPDATE packs SET updated_at = ? WHERE id = ?", (now, pack_id))
    connection.commit()
    return True


def pack_items_as_cart(connection: sqlite3.Connection, pack_id: int) -> dict:
    """パック内容をクライアントのカート形式（version 2）で返す。"""
    books: dict[str, dict[str, object]] = {}
    for item in get_pack_items(connection, pack_id):
        books[item.pdf_path] = {
            "title": item.title,
            "pages": item.pages,
            "collapsed": item.collapsed,
            "addedAt": item.added_at,
        }
    return {"version": 2, "books": books}


def import_cart_as_pack(connection: sqlite3.Connection, cart: dict, *, name: str) -> int | None:
    """sessionStorage のカート（version 2）を新規パックとして取り込む。"""
    if not isinstance(cart, dict) or cart.get("version") != 2:
        return None
    books = cart.get("books")
    if not isinstance(books, dict) or not books:
        return None
    pack_id = create_pack(connection, name=name)
    replace_pack_items(connection, pack_id, books)
    return pack_id


def _pack_record_from_row(row: sqlite3.Row) -> PackRecord:
    return PackRecord(
        id=int(row["id"]),
        name=str(row["name"]),
        note=str(row["note"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        archived_at=str(row["archived_at"]) if row["archived_at"] is not None else None,
        book_count=int(row["book_count"]),
    )


def ensure_pack_schema(connection: sqlite3.Connection) -> None:
    """パック関連テーブルだけを保証する軽量版。APIリクエスト経路で使う。"""
    _ensure_pack_schema(connection)
    connection.commit()


def _ensure_pack_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS packs (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            archived_at TEXT
        );

        CREATE TABLE IF NOT EXISTS pack_items (
            id INTEGER PRIMARY KEY,
            pack_id INTEGER NOT NULL REFERENCES packs(id) ON DELETE CASCADE,
            pdf_path TEXT NOT NULL,
            title TEXT NOT NULL,
            pages TEXT NOT NULL DEFAULT '',
            collapsed INTEGER NOT NULL DEFAULT 0,
            position INTEGER NOT NULL DEFAULT 0,
            added_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(pack_id, pdf_path)
        );

        CREATE TABLE IF NOT EXISTS app_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )


def _clean_snippet(snippet: str) -> str:
    one_line = " ".join(snippet.split())
    if len(one_line) <= 240:
        return one_line
    return f"{one_line[:237]}..."


def _build_search_snippet(row: sqlite3.Row, query: str) -> str:
    if "body_text" in row.keys():
        return _clean_snippet(build_excerpt(str(row["body_text"]), query))
    if "snippet" in row.keys():
        return _clean_snippet(str(row["snippet"]))
    return ""


def _table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


def _ensure_books_schema(connection: sqlite3.Connection) -> None:
    columns = _table_columns(connection, "books")
    if not columns:
        connection.executescript(
            """
            CREATE TABLE books (
                id INTEGER PRIMARY KEY,
                path TEXT UNIQUE,
                filename TEXT,
                source_type TEXT NOT NULL DEFAULT 'pdf' CHECK (source_type IN ('pdf', 'kindle')),
                external_id TEXT,
                title TEXT NOT NULL,
                size_bytes INTEGER,
                modified_at REAL,
                indexed_at TEXT NOT NULL,
                open_url TEXT,
                scrapbox_url TEXT,
                cover_url TEXT,
                UNIQUE(source_type, external_id)
            );
            """
        )
        return

    if "source_type" in columns and "external_id" in columns:
        if "filename" not in columns:
            connection.execute("ALTER TABLE books ADD COLUMN filename TEXT")
            _backfill_book_filenames(connection)
        for column in ("open_url", "scrapbox_url", "cover_url"):
            if column not in columns:
                connection.execute(f"ALTER TABLE books ADD COLUMN {column} TEXT")
        return

    connection.executescript(
        """
        CREATE TABLE books_new (
            id INTEGER PRIMARY KEY,
            path TEXT UNIQUE,
            filename TEXT,
            source_type TEXT NOT NULL DEFAULT 'pdf' CHECK (source_type IN ('pdf', 'kindle')),
            external_id TEXT,
            title TEXT NOT NULL,
            size_bytes INTEGER,
            modified_at REAL,
            indexed_at TEXT NOT NULL,
            open_url TEXT,
            scrapbox_url TEXT,
            cover_url TEXT,
            UNIQUE(source_type, external_id)
        );

        INSERT INTO books_new(id, path, filename, source_type, external_id, title, size_bytes, modified_at, indexed_at, open_url, scrapbox_url, cover_url)
        SELECT
            id,
            path,
            NULL,
            'pdf',
            NULL,
            title,
            size_bytes,
            modified_at,
            indexed_at,
            NULL,
            NULL,
            NULL
        FROM books;

        DROP TABLE books;
        ALTER TABLE books_new RENAME TO books;
        """
    )
    _backfill_book_filenames(connection)


def _ensure_pages_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS pages (
            id INTEGER PRIMARY KEY,
            book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            page_number INTEGER NOT NULL,
            text TEXT NOT NULL,
            UNIQUE(book_id, page_number)
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
            book_id UNINDEXED,
            page_number UNINDEXED,
            title,
            text,
            tokenize = 'unicode61'
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS pages_trigram USING fts5(
            text,
            tokenize = 'trigram'
        );
        """
    )
    _backfill_pages_trigram(connection)


def _ensure_memo_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS memos (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            scrapbox_url TEXT,
            cover_url TEXT
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS memos_fts USING fts5(
            memo_id UNINDEXED,
            title,
            body,
            tokenize = 'unicode61'
        );

        CREATE TABLE IF NOT EXISTS memo_sources (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            source_path TEXT,
            source_mtime REAL,
            indexed_at TEXT NOT NULL
        );
        """
    )


def _ensure_book_notes_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS book_notes (
            id INTEGER PRIMARY KEY,
            book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            scrapbox_url TEXT,
            cover_url TEXT,
            indexed_at TEXT NOT NULL
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS book_notes_fts USING fts5(
            book_id UNINDEXED,
            title,
            body,
            tokenize = 'unicode61'
        );
        """
    )


def _ensure_search_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS books_fts USING fts5(
            book_id UNINDEXED,
            title,
            tokenize = 'unicode61'
        );

        CREATE INDEX IF NOT EXISTS idx_books_title_path ON books(title, path);
        CREATE INDEX IF NOT EXISTS idx_memos_title ON memos(title);
        CREATE INDEX IF NOT EXISTS idx_book_notes_book_title ON book_notes(book_id, title);
        """
    )
    has_books_fts_rows = connection.execute("SELECT 1 FROM books_fts LIMIT 1").fetchone() is not None
    if not has_books_fts_rows:
        connection.execute(
            """
            INSERT INTO books_fts(rowid, book_id, title)
            SELECT id, id, title
            FROM books
            """
        )


def _replace_book_search_index(connection: sqlite3.Connection, *, book_id: int, title: str) -> None:
    connection.execute("DELETE FROM books_fts WHERE rowid = ?", (book_id,))
    connection.execute(
        """
        INSERT INTO books_fts(rowid, book_id, title)
        VALUES (?, ?, ?)
        """,
        (book_id, book_id, prepare_index_text(title)),
    )


def _backfill_pages_trigram(connection: sqlite3.Connection) -> None:
    has_rows = connection.execute("SELECT 1 FROM pages_trigram LIMIT 1").fetchone() is not None
    if has_rows:
        return
    rows = connection.execute("SELECT id, text FROM pages ORDER BY id").fetchall()
    for row in rows:
        connection.execute(
            """
            INSERT INTO pages_trigram(rowid, text)
            VALUES (?, ?)
            """,
            (int(row["id"]), normalize_trigram_text(str(row["text"]))),
        )


def _backfill_book_filenames(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        """
        SELECT id, path
        FROM books
        WHERE source_type = 'pdf' AND path IS NOT NULL AND (filename IS NULL OR filename = '')
        """
    ).fetchall()
    for row in rows:
        connection.execute(
            "UPDATE books SET filename = ? WHERE id = ?",
            (Path(str(row["path"])).name, int(row["id"])),
        )


def _dedupe_rows(rows: list[sqlite3.Row], *, limit: int) -> list[sqlite3.Row]:
    unique_rows: list[sqlite3.Row] = []
    seen: set[tuple[object, ...]] = set()
    for row in rows:
        key = (
            row["kind"] if "kind" in row.keys() else None,
            row["title"],
            row["path"],
            row["page_number"] if "page_number" in row.keys() else None,
            row["snippet"] if "snippet" in row.keys() else row["body_text"] if "body_text" in row.keys() else None,
        )
        if key in seen:
            continue
        seen.add(key)
        unique_rows.append(row)
        if len(unique_rows) >= limit:
            break
    return unique_rows
