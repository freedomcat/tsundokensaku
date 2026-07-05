import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
import json

from pypdf import PdfReader, PdfWriter

from tsundokensaku.web import (
    build_scrapbox_page_url,
    build_search_scrapbox_body,
    export_pdf,
    group_pdf_results,
    highlight_query,
    import_pdfs_from_directory,
    import_scrapbox_export_bytes,
    format_indexed_at,
    resolve_pdf_scrapbox_url,
    save_pdf_export_to_configured_dir,
    save_uploaded_pdf,
)
from tsundokensaku.database import connect, initialize, upsert_book


class HighlightQueryTest(unittest.TestCase):
    def test_highlight_query_marks_matches(self) -> None:
        rendered = str(highlight_query("伝わるコードレビューには何が必要なんだろう？", "コードレビュー"))
        self.assertIn("<mark>コードレビュー</mark>", rendered)

    def test_highlight_query_escapes_html(self) -> None:
        rendered = str(highlight_query("<script>alert(1)</script> レビュー", "レビュー"))
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", rendered)
        self.assertIn("<mark>レビュー</mark>", rendered)

    def test_group_pdf_results_combines_pages_by_title(self) -> None:
        grouped = group_pdf_results(
            [
                {
                    "kind": "pdf",
                    "title": "本A",
                    "path": "book-a.pdf",
                    "page_number": 2,
                    "snippet": "2ページ目",
                    "open_url": "/pdf/book-a.pdf#page=2",
                    "scrapbox_url": None,
                    "cover_url": None,
                },
                {
                    "kind": "pdf",
                    "title": "本A",
                    "path": "book-a.pdf",
                    "page_number": 5,
                    "snippet": "5ページ目",
                    "open_url": "/pdf/book-a.pdf#page=5",
                    "scrapbox_url": None,
                    "cover_url": None,
                },
                {
                    "kind": "memo",
                    "title": "メモ",
                    "path": "メモ",
                    "page_number": None,
                    "snippet": "メモ本文",
                    "open_url": "https://scrapbox.io/example/メモ",
                    "scrapbox_url": "https://scrapbox.io/example/メモ",
                    "cover_url": None,
                },
            ]
        )

        self.assertEqual(len(grouped), 2)
        book = grouped[0]
        self.assertEqual(book["title"], "本A")
        self.assertEqual(book["page_summary"], "p.2, p.5")
        self.assertEqual(book["page_numbers"], [2, 5])
        self.assertEqual(book["hit_count"], 2)
        self.assertEqual(book["snippet"], "2ページ目")
        self.assertEqual(grouped[1]["kind"], "memo")

    def test_import_pdfs_from_directory_copies_into_books_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "source"
            books_dir = root / "books"
            nested_dir = source_dir / "nested"
            nested_dir.mkdir(parents=True)
            pdf_a = source_dir / "a.pdf"
            pdf_b = nested_dir / "b.pdf"
            pdf_a.write_bytes(b"%PDF-1.4 a")
            pdf_b.write_bytes(b"%PDF-1.4 b")

            copied, skipped, total = import_pdfs_from_directory(source_dir, books_dir)

            self.assertEqual((copied, skipped, total), (2, 0, 2))
            self.assertTrue((books_dir / "a.pdf").exists())
            self.assertTrue((books_dir / "nested" / "b.pdf").exists())

    def test_save_uploaded_pdf_writes_unique_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            books_dir = Path(temp_dir) / "books"
            first = save_uploaded_pdf("sample.pdf", b"%PDF-1.4 first", books_dir)
            second = save_uploaded_pdf("sample.pdf", b"%PDF-1.4 second", books_dir)

            self.assertTrue(first.exists())
            self.assertTrue(second.exists())
            self.assertNotEqual(first, second)
            self.assertEqual(first.read_bytes(), b"%PDF-1.4 first")
            self.assertEqual(second.read_bytes(), b"%PDF-1.4 second")

    def test_format_indexed_at_renders_jst(self) -> None:
        self.assertEqual(format_indexed_at("2026-06-29T03:55:59.999358+00:00"), "2026/06/29 12:55")

    def test_import_scrapbox_export_bytes_syncs_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            cache_path = Path(temp_dir) / "scrapbox.json"
            payload = {
                "pages": [
                    {
                        "title": "メモ1",
                        "lines": [{"text": "検索対象のメモ本文"}],
                    },
                    {
                        "title": "Kindle Book",
                        "lines": [
                            {"text": "#Kindle #技術書"},
                            {"text": "https://read.amazon.co.jp/?asin=B012345678"},
                        ],
                    },
                ]
            }

            with patch("tsundokensaku.web.SCRAPBOX_EXPORT_CACHE", cache_path):
                imported, imported_kindle = import_scrapbox_export_bytes(json.dumps(payload).encode("utf-8"), db_path)

            self.assertEqual(imported, 2)
            self.assertEqual(imported_kindle, 1)
            self.assertTrue(cache_path.exists())

    def test_export_pdf_returns_selected_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books" / "tech"
            books_dir.mkdir(parents=True)
            pdf_path = books_dir / "sample.pdf"

            writer = PdfWriter()
            for _ in range(4):
                writer.add_blank_page(width=72, height=72)
            with pdf_path.open("wb") as handle:
                writer.write(handle)

            with patch("tsundokensaku.web.get_books_dir", return_value=books_dir):
                response = export_pdf(pdf_path="sample.pdf", pages="2-3")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["content-type"], "application/pdf")
            self.assertIn("attachment", response.headers["content-disposition"])

            with tempfile.NamedTemporaryFile(suffix=".pdf") as output:
                output.write(response.body)
                output.flush()
                reader = PdfReader(output.name)
                self.assertEqual(len(reader.pages), 2)

    def test_export_pdf_accepts_absolute_book_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books" / "tech"
            books_dir.mkdir(parents=True)
            pdf_path = books_dir / "sample.pdf"

            writer = PdfWriter()
            for _ in range(3):
                writer.add_blank_page(width=72, height=72)
            with pdf_path.open("wb") as handle:
                writer.write(handle)

            with patch("tsundokensaku.web.get_books_dir", return_value=books_dir):
                response = export_pdf(pdf_path=str(pdf_path), pages="1-2")

            self.assertEqual(response.status_code, 200)
            with tempfile.NamedTemporaryFile(suffix=".pdf") as output:
                output.write(response.body)
                output.flush()
                reader = PdfReader(output.name)
                self.assertEqual(len(reader.pages), 2)

    def test_save_pdf_export_requires_configured_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            books_dir = Path(temp_dir) / "books"
            books_dir.mkdir()
            pdf_path = books_dir / "sample.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=72, height=72)
            with pdf_path.open("wb") as handle:
                writer.write(handle)

            with self.assertRaises(ValueError):
                save_pdf_export_to_configured_dir("sample.pdf", "1", books_dir=books_dir, save_dir=None)

    def test_save_pdf_export_errors_when_save_dir_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"
            books_dir.mkdir()
            pdf_path = books_dir / "sample.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=72, height=72)
            with pdf_path.open("wb") as handle:
                writer.write(handle)

            with self.assertRaises(FileNotFoundError):
                save_pdf_export_to_configured_dir("sample.pdf", "1", books_dir=books_dir, save_dir=root / "missing")

    def test_save_pdf_export_writes_unique_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"
            save_dir = root / "exports"
            books_dir.mkdir()
            save_dir.mkdir()
            pdf_path = books_dir / "日本語の本.pdf"
            writer = PdfWriter()
            for _ in range(3):
                writer.add_blank_page(width=72, height=72)
            with pdf_path.open("wb") as handle:
                writer.write(handle)
            existing = save_dir / "日本語の本_p1-2.pdf"
            existing.write_bytes(b"existing")

            saved = save_pdf_export_to_configured_dir("日本語の本.pdf", "1-2", books_dir=books_dir, save_dir=save_dir)

            self.assertEqual(saved.name, "日本語の本_p1-2_2.pdf")
            self.assertTrue(saved.exists())
            reader = PdfReader(str(saved))
            self.assertEqual(len(reader.pages), 2)

    def test_resolve_pdf_scrapbox_url_prefers_database_value(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"
            books_dir.mkdir()
            pdf_path = books_dir / "sample.pdf"
            pdf_path.write_bytes(b"%PDF-1.4 sample")
            db_path = root / "index.db"
            connection = connect(db_path)
            try:
                initialize(connection)
                upsert_book(
                    connection,
                    path=pdf_path,
                    title="sample",
                    size_bytes=pdf_path.stat().st_size,
                    modified_at=pdf_path.stat().st_mtime,
                    scrapbox_url="https://scrapbox.io/custom-project/sample",
                )
                connection.commit()
            finally:
                connection.close()

            self.assertEqual(
                resolve_pdf_scrapbox_url("sample.pdf", books_dir=books_dir, db_path=db_path),
                "https://scrapbox.io/custom-project/sample",
            )

    def test_build_scrapbox_page_url_includes_prefilled_body(self) -> None:
        with patch.dict("os.environ", {"SCRAPBOX_BASE_URL": "https://scrapbox.io/custom-project"}, clear=False):
            url = build_scrapbox_page_url("検索結果 SQLite 2026-06-29 23:15", "検索語: SQLite\n結果一覧")

        self.assertIsNotNone(url)
        self.assertIn("https://scrapbox.io/custom-project/", url)
        self.assertIn("body=", url)

    def test_build_search_scrapbox_body_includes_results(self) -> None:
        page_title, body = build_search_scrapbox_body(
            query="SQLite",
            scope="all",
            sort="rank",
            group="none",
            results=[
                {
                    "title": "SQLite入門",
                    "kind": "pdf",
                    "snippet": "FTS5",
                    "path": "books/tech/sqlite.pdf",
                    "open_url": "https://example.com/pdf",
                    "scrapbox_url": "https://scrapbox.io/custom-project/SQLite%E5%85%A5%E9%96%80",
                }
            ],
        )

        self.assertIn("SQLite", page_title)
        self.assertIn("#つんどけんさく", body)
        self.assertIn("検索語: SQLite", body)
        self.assertIn("SQLite入門", body)
        self.assertIn("scrapbox: [SQLite入門]", body)
        self.assertNotIn("books/tech/sqlite.pdf", body)
        self.assertNotIn("open:", body)

    def test_build_search_scrapbox_body_keeps_all_results(self) -> None:
        results = [
            {
                "title": f"本{i}",
                "kind": "pdf",
                "snippet": f"snippet {i}",
                "path": f"books/tech/book-{i}.pdf",
                "scrapbox_url": f"https://scrapbox.io/custom-project/%E6%9C%AC{i}",
            }
            for i in range(1, 22)
        ]

        _, body = build_search_scrapbox_body(
            query="SQLite",
            scope="all",
            sort="rank",
            group="none",
            results=results,
        )

        self.assertIn("21. 本21", body)
        self.assertNotIn("他 ", body)


if __name__ == "__main__":
    unittest.main()
