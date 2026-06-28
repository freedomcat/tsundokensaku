#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from tsundokensaku.database import PageRecord, connect, initialize, replace_pages, search, upsert_book
from tsundokensaku.indexer import find_pdfs
from tsundokensaku.pdf_extract import extract_pages

DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "index.db"
DEFAULT_STATE_PATH = PROJECT_ROOT / "data" / "reindex_pdf_pages_state.json"
DEFAULT_QUERY = "サーバー"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Force rebuild PDF pages/pages_fts with the current tokenizer, preserving memos and other tables."
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument(
        "--books-dir",
        type=Path,
        default=None,
        help="PDF root. If omitted, derive it from existing PDF paths in the DB.",
    )
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--query", action="append", default=None, help="Search query to count before/after.")
    parser.add_argument("--expected", type=int, default=57, help="Expected hit count for the primary query.")
    parser.add_argument("--reset-state", action="store_true", help="Ignore and replace an existing state file.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would run without changing the DB.")
    parser.add_argument("--no-backup", action="store_true", help="Skip DB backup creation.")
    args = parser.parse_args()

    db_path = args.db.resolve()
    state_path = args.state.resolve()
    queries = args.query or [DEFAULT_QUERY]

    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")

    books_dir = (args.books_dir.resolve() if args.books_dir else _derive_books_dir(db_path))
    pdf_paths = list(find_pdfs(books_dir))
    if not pdf_paths:
        raise SystemExit(f"No PDF files found under {books_dir}")

    if args.dry_run:
        print(f"DB: {db_path}")
        print(f"Books dir: {books_dir}")
        print(f"PDF files: {len(pdf_paths)}")
        print(f"State: {state_path}")
        return 0

    state = _load_state(state_path, reset=args.reset_state)
    if not state:
        state = _new_state(db_path=db_path, books_dir=books_dir, queries=queries, expected=args.expected)
        if not args.no_backup:
            backup_path = _backup_db(db_path)
            state["backup_path"] = str(backup_path)
            print(f"Backup: {backup_path}", flush=True)

        state["before_counts"] = _search_counts(db_path, queries)
        _save_state(state_path, state)
    else:
        _validate_resume_state(state, db_path=db_path, books_dir=books_dir)
        print(f"Resume state: {state_path}", flush=True)
        if state.get("backup_path"):
            print(f"Backup: {state['backup_path']}", flush=True)

    completed: dict[str, Any] = state.setdefault("completed", {})
    connection = connect(db_path)
    initialize(connection)

    total = len(pdf_paths)
    done_pages = sum(int(item.get("pages", 0)) for item in completed.values())
    print(f"Force reindexing {total} PDF files under {books_dir}", flush=True)
    print(f"Already completed: {len(completed)}/{total} files, {done_pages} pages", flush=True)

    indexed = 0
    page_total = 0
    try:
        for index, pdf_path in enumerate(pdf_paths, start=1):
            key = str(pdf_path.resolve())
            stat = pdf_path.stat()
            existing_state = completed.get(key)
            if _is_completed(existing_state, stat):
                print(f"[{index}/{total}] SKIP {pdf_path.stem}", flush=True)
                continue

            print(f"[{index}/{total}] INDEX {pdf_path.stem}", flush=True)
            book_id = upsert_book(
                connection,
                path=pdf_path,
                title=pdf_path.stem,
                size_bytes=stat.st_size,
                modified_at=stat.st_mtime,
            )
            pages = [PageRecord(page_number=page.page_number, text=page.text) for page in extract_pages(pdf_path)]
            page_count = replace_pages(connection, book_id=book_id, title=pdf_path.stem, pages=pages)

            completed[key] = {
                "title": pdf_path.stem,
                "pages": page_count,
                "size_bytes": stat.st_size,
                "modified_at": stat.st_mtime,
                "finished_at": _now(),
            }
            state["last_updated_at"] = _now()
            _save_state(state_path, state)

            indexed += 1
            page_total += page_count
            print(f"[{index}/{total}] DONE {pdf_path.stem} ({page_count} pages)", flush=True)
    finally:
        connection.close()

    state["after_counts"] = _search_counts(db_path, queries)
    state["finished_at"] = _now()
    _save_state(state_path, state)

    total_pages = sum(int(item.get("pages", 0)) for item in completed.values())
    print(f"Done: indexed_now={indexed}, indexed_pages_now={page_total}", flush=True)
    print(f"State total: completed={len(completed)}/{total}, pages={total_pages}", flush=True)
    _print_counts(state)
    return 0


def _derive_books_dir(db_path: Path) -> Path:
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            "SELECT path FROM books WHERE source_type = 'pdf' AND path IS NOT NULL ORDER BY path"
        ).fetchall()
    finally:
        connection.close()

    parents = [Path(row[0]).parent for row in rows if row[0] and Path(row[0]).exists()]
    if not parents:
        return PROJECT_ROOT / "books" / "tech"
    common = Path(__import__("os").path.commonpath([str(parent) for parent in parents]))
    return common


def _new_state(*, db_path: Path, books_dir: Path, queries: list[str], expected: int) -> dict[str, Any]:
    return {
        "version": 1,
        "started_at": _now(),
        "last_updated_at": _now(),
        "db_path": str(db_path),
        "books_dir": str(books_dir),
        "queries": queries,
        "expected": expected,
        "completed": {},
    }


def _load_state(path: Path, *, reset: bool) -> dict[str, Any]:
    if reset or not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp_path.replace(path)


def _validate_resume_state(state: dict[str, Any], *, db_path: Path, books_dir: Path) -> None:
    if state.get("db_path") != str(db_path):
        raise SystemExit(f"State DB mismatch: {state.get('db_path')} != {db_path}")
    if state.get("books_dir") != str(books_dir):
        raise SystemExit(f"State books-dir mismatch: {state.get('books_dir')} != {books_dir}")


def _backup_db(db_path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = db_path.with_name(f"{db_path.name}.backup-{timestamp}")
    shutil.copy2(db_path, backup_path)
    return backup_path


def _is_completed(item: Any, stat) -> bool:
    if not isinstance(item, dict):
        return False
    return item.get("size_bytes") == stat.st_size and item.get("modified_at") == stat.st_mtime


def _search_counts(db_path: Path, queries: list[str]) -> dict[str, Any]:
    connection = connect(db_path)
    try:
        counts = {}
        for query in queries:
            results = search(connection, query, scope="body", limit=100000)
            counts[query] = {
                "body_results": len(results),
                "distinct_titles": len({result.title for result in results}),
            }
        return counts
    finally:
        connection.close()


def _print_counts(state: dict[str, Any]) -> None:
    expected = state.get("expected")
    before = state.get("before_counts", {})
    after = state.get("after_counts", {})
    for query in state.get("queries", []):
        before_count = before.get(query, {}).get("body_results")
        after_count = after.get(query, {}).get("body_results")
        print(f"Query: {query}", flush=True)
        print(f"  before body results: {before_count}", flush=True)
        print(f"  after body results: {after_count}", flush=True)
        if expected is not None and after_count is not None:
            print(f"  expected: {expected}, delta: {after_count - int(expected)}", flush=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
