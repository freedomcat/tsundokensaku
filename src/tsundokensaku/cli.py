from __future__ import annotations

import argparse
from pathlib import Path

from tsundokensaku.database import connect, initialize, list_pdf_title_refresh_targets, refresh_pdf_titles, search
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
    search_parser.add_argument(
        "--match",
        choices=("all", "any"),
        default="all",
        help="Match mode: all (every term must appear) or any (any term may appear).",
    )

    refresh_titles_parser = subparsers.add_parser(
        "refresh-titles",
        help="Refresh PDF display titles without re-extracting page text.",
    )
    refresh_titles_parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    refresh_titles_parser.add_argument("--pdf-path", type=Path, default=None, help="Refresh only one PDF path.")
    refresh_titles_parser.add_argument("--path-like", default=None, help="Refresh PDFs whose path or filename contains this text.")
    refresh_titles_parser.add_argument("--dry-run", action="store_true", help="Show targets without updating the DB.")

    args = parser.parse_args(argv)

    if args.command == "index":
        index_books(books_dir=args.books_dir, db_path=args.db)
        return 0

    if args.command == "search":
        connection = connect(args.db)
        export_json = find_export_json(PROJECT_ROOT)
        metadata_by_stem = load_metadata_by_pdf_stem(export_json)
        results = search(connection, args.query, limit=args.limit, scope=args.scope, match=args.match)
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

    if args.command == "refresh-titles":
        connection = connect(args.db)
        try:
            initialize(connection)
            export_json = find_export_json(PROJECT_ROOT)
            metadata_by_stem = load_metadata_by_pdf_stem(export_json)
            targets = list_pdf_title_refresh_targets(
                connection,
                metadata_by_stem,
                pdf_path=args.pdf_path,
                path_like=args.path_like,
            )
            changed = [target for target in targets if target.current_title != target.resolved_title]
            print(f"Target PDFs: {len(targets)}")
            for target in targets:
                filename = target.filename or Path(target.path).name
                marker = "CHANGE" if target.current_title != target.resolved_title else "KEEP"
                print(f"[{marker}] {target.current_title} -> {target.resolved_title}")
                print(f"  {filename}")
                print(f"  {target.path}")

            if args.dry_run:
                print(f"Dry run: {len(changed)} title updates would be applied.")
                return 0

            updated = refresh_pdf_titles(
                connection,
                metadata_by_stem,
                pdf_path=args.pdf_path,
                path_like=args.path_like,
            )
        finally:
            connection.close()
        print(f"Updated PDF titles: {updated}")
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
