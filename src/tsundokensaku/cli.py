from __future__ import annotations

import argparse
from pathlib import Path

from tsundokensaku.database import connect, search
from tsundokensaku.indexer import index_books
from tsundokensaku.metadata import find_export_json, load_metadata_by_pdf_stem, metadata_for_pdf, search_scrapbox_memos


PROJECT_ROOT = Path(__file__).resolve().parents[2]


DEFAULT_BOOKS_DIR = Path("books/tech")
DEFAULT_DB_PATH = Path("data/index.db")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tsundokensaku")
    subparsers = parser.add_subparsers(dest="command", required=True)

    index_parser = subparsers.add_parser("index", help="Index PDFs under books/tech.")
    index_parser.add_argument("--books-dir", type=Path, default=DEFAULT_BOOKS_DIR)
    index_parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)

    search_parser = subparsers.add_parser("search", help="Search indexed PDF text.")
    search_parser.add_argument("query")
    search_parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    search_parser.add_argument("--limit", type=int, default=20)
    search_parser.add_argument(
        "--scope",
        choices=("all", "title", "body", "memo"),
        default="all",
        help="Search scope: all, title, body, or memo.",
    )

    args = parser.parse_args(argv)

    if args.command == "index":
        index_books(books_dir=args.books_dir, db_path=args.db)
        return 0

    if args.command == "search":
        connection = connect(args.db)
        export_json = find_export_json(PROJECT_ROOT)
        metadata_by_stem = load_metadata_by_pdf_stem(export_json)
        title_results = search(connection, args.query, limit=args.limit, scope="title") if args.scope in {"all", "title"} else []
        body_results = search(connection, args.query, limit=args.limit, scope="body") if args.scope in {"all", "body"} else []
        memo_results = search_scrapbox_memos(export_json, args.query, limit=args.limit) if args.scope in {"all", "memo"} else []
        connection.close()

        results = []
        for result in title_results + body_results:
            results.append(
                {
                    "kind": "pdf",
                    "title": result.title,
                    "page_number": result.page_number,
                    "snippet": result.snippet,
                    "path": result.path,
                    "open_url": None,
                    "scrapbox_url": (
                        metadata.scrapbox_url
                        if (metadata := metadata_for_pdf(result.path, metadata_by_stem))
                        else None
                    ),
                }
            )
        for memo in memo_results:
            results.append(
                {
                    "kind": "memo",
                    "title": memo.title,
                    "page_number": None,
                    "snippet": memo.body,
                    "path": memo.title,
                    "open_url": memo.scrapbox_url,
                    "scrapbox_url": memo.scrapbox_url,
                }
            )

        if not results:
            print("No results.")
            return 0

        for result in results:
            if result["kind"] == "memo":
                print(f"[MEMO] {result['title']}")
            else:
                print(f"[PDF] {result['title']} p.{result['page_number']}")
            print(f"  {result['snippet']}")
            print(f"  {result['open_url'] or result['path']}")
            print()
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
