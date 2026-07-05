from __future__ import annotations

import argparse
from pathlib import Path

from tsundokensaku.database import connect, search
from tsundokensaku.indexer import index_books
from tsundokensaku.metadata import find_export_json, load_metadata_by_pdf_stem, metadata_for_pdf


PROJECT_ROOT = Path(__file__).resolve().parents[2]


DEFAULT_BOOKS_DIR = Path("data/books")
DEFAULT_DB_PATH = Path("data/index.db")


def _render_results(results, metadata_by_stem: dict[str, object]) -> list[dict[str, object]]:
    rendered_results = []
    for result in results:
        if result.kind == "memo":
            rendered_results.append(
                {
                    "kind": "memo",
                    "title": result.title,
                    "page_number": None,
                    "snippet": result.snippet,
                    "path": result.path,
                    "open_url": result.open_url,
                    "scrapbox_url": result.open_url,
                }
            )
        elif result.kind == "note":
            rendered_results.append(
                {
                    "kind": "note",
                    "title": result.title,
                    "page_number": None,
                    "snippet": result.snippet,
                    "path": result.path,
                    "open_url": result.open_url,
                    "scrapbox_url": result.scrapbox_url or result.open_url,
                    "cover_url": result.cover_url,
                }
            )
        elif result.kind == "kindle":
            rendered_results.append(
                {
                    "kind": "kindle",
                    "title": result.title,
                    "page_number": None,
                    "snippet": result.snippet,
                    "path": result.path or result.title,
                    "open_url": result.open_url,
                    "scrapbox_url": result.scrapbox_url,
                    "cover_url": result.cover_url,
                }
            )
        else:
            metadata = metadata_for_pdf(result.path or "", metadata_by_stem)
            rendered_results.append(
                {
                    "kind": "pdf",
                    "title": result.title,
                    "page_number": result.page_number,
                    "page_summary": f"p.{result.page_number}" if result.page_number is not None else "",
                    "page_numbers": [result.page_number] if result.page_number is not None else [],
                    "page_urls": [],
                    "snippet": result.snippet,
                    "path": result.path,
                    "open_url": result.path,
                    "scrapbox_url": metadata.scrapbox_url if metadata else None,
                }
            )
    return rendered_results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tsundokensaku")
    subparsers = parser.add_subparsers(dest="command", required=True)

    index_parser = subparsers.add_parser("index", help="Index PDFs under data/books.")
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
        results = search(connection, args.query, limit=args.limit, scope=args.scope)
        connection.close()
        rendered_results = _render_results(results, metadata_by_stem)

        if not rendered_results:
            print("No results.")
            return 0

        for result in rendered_results:
            kind_label = {
                "pdf": "PDF",
                "kindle": "KINDLE",
                "note": "NOTE",
                "memo": "MEMO",
            }.get(result["kind"], result["kind"].upper())
            if result["kind"] == "pdf" and result["page_number"] is not None:
                header = f"[{kind_label}] {result['title']} p.{result['page_number']}"
            else:
                header = f"[{kind_label}] {result['title']}"
            print(header)
            print(f"  {result['snippet']}")
            if result["kind"] != "pdf":
                print(f"  {result['open_url'] or result['path']}")
            print()
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
