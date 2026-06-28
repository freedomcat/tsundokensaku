from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from tsundokensaku.metadata import ScrapboxMemo, load_scrapbox_memos


@dataclass(frozen=True)
class BookRecord:
    id: int
    path: str
    title: str
    size_bytes: int
    modified_at: float
    indexed_at: str


@dataclass(frozen=True)
class PageRecord:
    page_number: int
    text: str


@dataclass(frozen=True)
class SearchResult:
    title: str
    path: str
    page_number: int | None
    snippet: str
    open_url: str | None = None
    cover_url: str | None = None


SEARCH_SCOPES = {"all", "title", "body", "memo"}


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
    return connection


def initialize(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS books (
            id INTEGER PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            modified_at REAL NOT NULL,
            indexed_at TEXT NOT NULL
        );

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
    connection.commit()


def upsert_book(
    connection: sqlite3.Connection,
    *,
    path: Path,
    title: str,
    size_bytes: int,
    modified_at: float,
) -> int:
    indexed_at = datetime.now(timezone.utc).isoformat()
    connection.execute(
        """
        INSERT INTO books(path, title, size_bytes, modified_at, indexed_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            title = excluded.title,
            size_bytes = excluded.size_bytes,
            modified_at = excluded.modified_at,
            indexed_at = excluded.indexed_at
        """,
        (str(path), title, size_bytes, modified_at, indexed_at),
    )
    row = connection.execute("SELECT id FROM books WHERE path = ?", (str(path),)).fetchone()
    if row is None:
        raise RuntimeError(f"Failed to store book: {path}")
    return int(row["id"])


def get_book(connection: sqlite3.Connection, *, path: Path) -> BookRecord | None:
    row = connection.execute(
        """
        SELECT id, path, title, size_bytes, modified_at, indexed_at
        FROM books
        WHERE path = ?
        """,
        (str(path),),
    ).fetchone()
    if row is None:
        return None
    return BookRecord(
        id=int(row["id"]),
        path=str(row["path"]),
        title=str(row["title"]),
        size_bytes=int(row["size_bytes"]),
        modified_at=float(row["modified_at"]),
        indexed_at=str(row["indexed_at"]),
    )


def list_books(connection: sqlite3.Connection) -> list[BookRecord]:
    rows = connection.execute(
        """
        SELECT id, path, title, size_bytes, modified_at, indexed_at
        FROM books
        ORDER BY title, path
        """
    ).fetchall()
    return [
        BookRecord(
            id=int(row["id"]),
            path=str(row["path"]),
            title=str(row["title"]),
            size_bytes=int(row["size_bytes"]),
            modified_at=float(row["modified_at"]),
            indexed_at=str(row["indexed_at"]),
        )
        for row in rows
    ]


def delete_book(connection: sqlite3.Connection, *, book_id: int) -> None:
    connection.execute("DELETE FROM books WHERE id = ?", (book_id,))
    connection.commit()


def replace_pages(
    connection: sqlite3.Connection,
    *,
    book_id: int,
    title: str,
    pages: Iterable[PageRecord],
) -> int:
    connection.execute("DELETE FROM pages WHERE book_id = ?", (book_id,))
    connection.execute("DELETE FROM pages_fts WHERE book_id = ?", (book_id,))

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
            (cursor.lastrowid, book_id, page.page_number, title, page.text),
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
            (cursor.lastrowid, cursor.lastrowid, memo.title, memo.body),
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


def search(
    connection: sqlite3.Connection,
    query: str,
    *,
    limit: int = 20,
    scope: str = "all",
) -> list[SearchResult]:
    normalized_query = query.strip()
    if not normalized_query:
        return []

    normalized_scope = scope if scope in SEARCH_SCOPES else "all"
    if normalized_scope == "title":
        try:
            rows = _search_title(connection, normalized_query, limit=limit)
        except sqlite3.OperationalError:
            rows = []
    elif normalized_scope == "body":
        try:
            rows = _search_body(connection, normalized_query, limit=limit)
        except sqlite3.OperationalError:
            rows = []
    elif normalized_scope == "memo":
        try:
            rows = _search_memo(connection, normalized_query, limit=limit)
        except sqlite3.OperationalError:
            rows = []
    else:
        try:
            title_rows = _search_title(connection, normalized_query, limit=limit)
        except sqlite3.OperationalError:
            title_rows = []
        try:
            body_rows = _search_body(connection, normalized_query, limit=limit)
        except sqlite3.OperationalError:
            body_rows = []
        try:
            memo_rows = _search_memo(connection, normalized_query, limit=limit)
        except sqlite3.OperationalError:
            memo_rows = []
        rows = title_rows + body_rows + memo_rows

    return [
        SearchResult(
            title=row["title"],
            path=row["path"],
            page_number=(int(row["page_number"]) if row["page_number"] is not None else None),
            snippet=_clean_snippet(row["snippet"]),
            open_url=row["open_url"] if "open_url" in row.keys() else None,
            cover_url=row["cover_url"] if "cover_url" in row.keys() else None,
        )
        for row in rows
    ]


def _search_title(connection: sqlite3.Connection, query: str, *, limit: int) -> list[sqlite3.Row]:
    terms = _query_terms(query)
    if not terms:
        return []

    where_clause = " AND ".join(["title LIKE ?" for _ in terms])
    return list(
        connection.execute(
            f"""
            SELECT
                title,
                path,
                1 AS page_number,
                title AS snippet
            FROM books
            WHERE {where_clause}
            ORDER BY title, path
            LIMIT ?
            """,
            tuple([f"%{term}%" for term in terms] + [limit]),
        )
    )


def _search_body(connection: sqlite3.Connection, query: str, *, limit: int) -> list[sqlite3.Row]:
    try:
        return _search_body_fts(connection, query, limit=limit)
    except sqlite3.OperationalError:
        return _search_body_like(connection, query, limit=limit)


def _search_body_fts(connection: sqlite3.Connection, query: str, *, limit: int) -> list[sqlite3.Row]:
    fts_query = _to_scoped_fts_query(query, column="text")
    return list(
        connection.execute(
            """
            SELECT
                b.title,
                b.path,
                f.page_number,
                snippet(pages_fts, 3, '[', ']', ' ... ', 24) AS snippet
            FROM pages_fts AS f
            JOIN books AS b ON b.id = f.book_id
            WHERE pages_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (fts_query, limit),
        )
    )


def _search_body_like(connection: sqlite3.Connection, query: str, *, limit: int) -> list[sqlite3.Row]:
    like_query = f"%{query}%"
    return list(
        connection.execute(
            """
            SELECT
                b.title,
                b.path,
                p.page_number,
                p.text AS snippet
            FROM pages AS p
            JOIN books AS b ON b.id = p.book_id
            WHERE p.text LIKE ?
            ORDER BY b.title, p.page_number
            LIMIT ?
            """,
            (like_query, limit),
        )
    )


def _search_memo(connection: sqlite3.Connection, query: str, *, limit: int) -> list[sqlite3.Row]:
    try:
        return _search_memo_fts(connection, query, limit=limit)
    except sqlite3.OperationalError:
        return _search_memo_like(connection, query, limit=limit)


def _search_memo_fts(connection: sqlite3.Connection, query: str, *, limit: int) -> list[sqlite3.Row]:
    fts_query = _to_fts_query(query)
    return list(
        connection.execute(
            """
            SELECT
                title,
                title AS path,
                NULL AS page_number,
                snippet(memos_fts, 2, '[', ']', ' ... ', 24) AS snippet,
                scrapbox_url AS open_url,
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


def _search_memo_like(connection: sqlite3.Connection, query: str, *, limit: int) -> list[sqlite3.Row]:
    like_query = f"%{query}%"
    return list(
        connection.execute(
            """
            SELECT
                title,
                title AS path,
                NULL AS page_number,
                body AS snippet,
                scrapbox_url AS open_url,
                cover_url
            FROM memos
            WHERE title LIKE ? OR body LIKE ?
            ORDER BY title
            LIMIT ?
            """,
            (like_query, like_query, limit),
        )
    )


def _query_terms(query: str) -> list[str]:
    terms = re.findall(r'"[^"]+"|\S+', query)
    return [term.strip(chr(34)) for term in terms if term.strip(chr(34))]


def _to_fts_query(query: str) -> str:
    quoted_terms = [f'"{term}"' for term in _query_terms(query)]
    return " ".join(quoted_terms)


def _to_scoped_fts_query(query: str, *, column: str) -> str:
    terms = re.findall(r'"[^"]+"|\S+', query)
    prefixed_terms = [f'{column}:"{term.strip(chr(34))}"' for term in terms if term.strip(chr(34))]
    return " ".join(prefixed_terms)


def _clean_snippet(snippet: str) -> str:
    one_line = " ".join(snippet.split())
    if len(one_line) <= 240:
        return one_line
    return f"{one_line[:237]}..."
