#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
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
from tsundokensaku.metadata import find_export_json, load_metadata_by_pdf_stem, resolve_pdf_display_title
from tsundokensaku.pdf_extract import extract_pages

DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "index.db"
DEFAULT_STATE_PATH = PROJECT_ROOT / "data" / "reindex_pdf_pages_state.json"


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
    parser.add_argument("--resume", action="store_true", help="Resume from an existing state file.")
    parser.add_argument("--reset-state", action="store_true", help="Ignore and replace an existing state file.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would run without changing the DB.")
    parser.add_argument("--no-backup", action="store_true", help="Skip DB backup creation.")
    parser.add_argument(
        "--memo",
        type=Path,
        default=None,
        help="Markdown memo path. Defaults to ~/wiki/inbox/YYYYmmdd_sudachi_reindex_result.md.",
    )
    parser.add_argument("--no-memo", action="store_true", help="Do not write a markdown memo.")
    parser.add_argument("--no-commit-memo", action="store_true", help="Do not commit the generated memo in ~/wiki.")
    args = parser.parse_args()

    db_path = args.db.resolve()
    state_path = args.state.resolve()
    queries = args.query or []

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
        print(f"Queries: {queries or '(required unless --dry-run)'}")
        return 0

    state = _load_state(state_path, reset=args.reset_state)
    if state and not args.resume:
        raise SystemExit(
            f"State file already exists: {state_path}\n"
            "Use --resume to continue it, or --reset-state after restoring/choosing the correct DB."
        )

    if not state:
        if not queries:
            raise SystemExit("At least one --query is required so before/after counts are meaningful.")
        state = _new_state(db_path=db_path, books_dir=books_dir, queries=queries, expected=args.expected)
        if not args.no_backup:
            _assert_db_available(db_path)
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
    metadata_by_stem = load_metadata_by_pdf_stem(find_export_json(PROJECT_ROOT))

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
            title = resolve_pdf_display_title(pdf_path, metadata_by_stem)
            existing_state = completed.get(key)
            if _is_completed(existing_state, stat, title=title):
                print(f"[{index}/{total}] SKIP {title}", flush=True)
                continue

            print(f"[{index}/{total}] INDEX {title}", flush=True)
            book_id = upsert_book(
                connection,
                path=pdf_path,
                filename=pdf_path.name,
                title=title,
                size_bytes=stat.st_size,
                modified_at=stat.st_mtime,
            )
            pages = [PageRecord(page_number=page.page_number, text=page.text) for page in extract_pages(pdf_path)]
            page_count = replace_pages(connection, book_id=book_id, title=title, pages=pages)

            completed[key] = {
                "title": title,
                "pages": page_count,
                "size_bytes": stat.st_size,
                "modified_at": stat.st_mtime,
                "finished_at": _now(),
            }
            state["last_updated_at"] = _now()
            _save_state(state_path, state)

            indexed += 1
            page_total += page_count
            print(f"[{index}/{total}] DONE {title} ({page_count} pages)", flush=True)
    finally:
        connection.close()

    state["after_counts"] = _search_counts(db_path, queries)
    state["finished_at"] = _now()
    _save_state(state_path, state)

    total_pages = sum(int(item.get("pages", 0)) for item in completed.values())
    print(f"Done: indexed_now={indexed}, indexed_pages_now={page_total}", flush=True)
    print(f"State total: completed={len(completed)}/{total}, pages={total_pages}", flush=True)
    _print_counts(state)

    if not args.no_memo:
        memo_path = (args.memo.resolve() if args.memo else _default_memo_path())
        _write_memo(memo_path, state, total=total, total_pages=total_pages)
        print(f"Memo: {memo_path}", flush=True)
        if not args.no_commit_memo:
            _commit_memo(memo_path)

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
    source = sqlite3.connect(db_path)
    try:
        destination = sqlite3.connect(backup_path)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()
    shutil.copystat(db_path, backup_path)
    return backup_path


def _assert_db_available(db_path: Path) -> None:
    try:
        connection = sqlite3.connect(db_path, timeout=1)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("ROLLBACK")
            quick_check = connection.execute("PRAGMA quick_check").fetchone()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise SystemExit(f"DB is not ready for backup/reindex: {db_path}\nOriginal error: {exc}") from exc

    if quick_check is None or quick_check[0] != "ok":
        raise SystemExit(f"DB quick_check failed before backup: {quick_check}")


def _is_completed(item: Any, stat, *, title: str) -> bool:
    if not isinstance(item, dict):
        return False
    return (
        item.get("size_bytes") == stat.st_size
        and item.get("modified_at") == stat.st_mtime
        and item.get("title") == title
    )


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


def _default_memo_path() -> Path:
    date = datetime.now().strftime("%Y%m%d")
    return Path.home() / "wiki" / "inbox" / f"{date}_sudachi_reindex_result.md"


def _write_memo(path: Path, state: dict[str, Any], *, total: int, total_pages: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Sudachi再インデックス結果",
        "",
        f"Date: {datetime.now().strftime('%Y-%m-%d')}",
        "",
        "## 対象",
        "",
        f"- DB: {state.get('db_path')}",
        f"- Backup: {state.get('backup_path', '(skipped)')}",
        f"- Books dir: {state.get('books_dir')}",
        f"- PDF files: {total}",
        f"- Pages reindexed: {total_pages}",
        f"- Expected: {state.get('expected')}",
        "",
        "## 検索件数",
        "",
    ]

    before = state.get("before_counts", {})
    after = state.get("after_counts", {})
    expected = state.get("expected")
    for query in state.get("queries", []):
        before_count = before.get(query, {}).get("body_results")
        before_titles = before.get(query, {}).get("distinct_titles")
        after_count = after.get(query, {}).get("body_results")
        after_titles = after.get(query, {}).get("distinct_titles")
        lines.extend(
            [
                f"### {query}",
                "",
                f"- Before body results: {before_count}",
                f"- Before distinct titles: {before_titles}",
                f"- After body results: {after_count}",
                f"- After distinct titles: {after_titles}",
            ]
        )
        if expected is not None and after_count is not None:
            lines.append(f"- Expected delta: {after_count - int(expected)}")
        lines.append("")

    lines.extend(
        [
            "## 実施内容",
            "",
            "- SQLite online backup APIで既存DBをバックアップ",
            "- PDF本文の `pages` / `pages_fts` を現在のtokenizerで強制再構築",
            "- `memos` などPDF以外のテーブルは保持",
            "- stateファイルで中断再開できるようにした",
            "",
            "## State",
            "",
            f"- State file started at: {state.get('started_at')}",
            f"- Finished at: {state.get('finished_at')}",
            f"- Completed files: {len(state.get('completed', {}))}",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _commit_memo(path: Path) -> None:
    wiki_root = Path.home() / "wiki"
    if not (wiki_root / ".git").exists():
        print(f"Skip memo commit: {wiki_root} is not a Git repository", flush=True)
        return

    try:
        relative_path = path.resolve().relative_to(wiki_root.resolve())
    except ValueError:
        print(f"Skip memo commit: {path} is outside {wiki_root}", flush=True)
        return

    subprocess.run(["git", "-C", str(wiki_root), "add", str(relative_path)], check=True)
    diff = subprocess.run(
        ["git", "-C", str(wiki_root), "diff", "--cached", "--quiet", "--", str(relative_path)],
        check=False,
    )
    if diff.returncode == 0:
        print(f"Skip memo commit: no staged changes for {relative_path}", flush=True)
        return

    commit = subprocess.run(
        ["git", "-C", str(wiki_root), "commit", "-m", "Add Sudachi reindex result memo", "--", str(relative_path)],
        check=False,
    )
    if commit.returncode != 0:
        print(f"Memo commit failed for {relative_path}; memo file was still written.", flush=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
