from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


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
    page_number: int
    snippet: str


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


def search(connection: sqlite3.Connection, query: str, *, limit: int = 20) -> list[SearchResult]:
    normalized_query = query.strip()
    if not normalized_query:
        return []

    try:
        rows = _search_fts(connection, normalized_query, limit=limit)
    except sqlite3.OperationalError:
        rows = []

    if not rows:
        rows = _search_like(connection, normalized_query, limit=limit)

    return [
        SearchResult(
            title=row["title"],
            path=row["path"],
            page_number=int(row["page_number"]),
            snippet=_clean_snippet(row["snippet"]),
        )
        for row in rows
    ]


def _search_fts(connection: sqlite3.Connection, query: str, *, limit: int) -> list[sqlite3.Row]:
    fts_query = _to_fts_query(query)
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


def _search_like(connection: sqlite3.Connection, query: str, *, limit: int) -> list[sqlite3.Row]:
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
            WHERE p.text LIKE ? OR b.title LIKE ?
            ORDER BY b.title, p.page_number
            LIMIT ?
            """,
            (like_query, like_query, limit),
        )
    )


def _to_fts_query(query: str) -> str:
    terms = re.findall(r'"[^"]+"|\S+', query)
    quoted_terms = [f'"{term.strip(chr(34))}"' for term in terms if term.strip(chr(34))]
    return " ".join(quoted_terms)


def _clean_snippet(snippet: str) -> str:
    one_line = " ".join(snippet.split())
    if len(one_line) <= 240:
        return one_line
    return f"{one_line[:237]}..."
