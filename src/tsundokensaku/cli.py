from __future__ import annotations

import argparse
from pathlib import Path

from tsundokensaku.database import connect, initialize, search
from tsundokensaku.indexer import index_books


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
        choices=("all", "title", "body"),
        default="all",
        help="Search scope: all, title, or body.",
    )

    args = parser.parse_args(argv)

    if args.command == "index":
        index_books(books_dir=args.books_dir, db_path=args.db)
        return 0

    if args.command == "search":
        connection = connect(args.db)
        initialize(connection)
        results = search(connection, args.query, limit=args.limit, scope=args.scope)
        connection.close()

        if not results:
            print("No results.")
            return 0

        for result in results:
            print(f"{result.title} p.{result.page_number}")
            print(f"  {result.snippet}")
            print(f"  {result.path}")
            print()
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
