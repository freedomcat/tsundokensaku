import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
import json

from tsundokensaku.web import (
    group_pdf_results,
    highlight_query,
    import_pdfs_from_directory,
    import_scrapbox_export_bytes,
    format_indexed_at,
    save_uploaded_pdf,
)


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


if __name__ == "__main__":
    unittest.main()
