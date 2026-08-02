import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter

from tsundokensaku import pdf_text_service
from tsundokensaku.database import PageRecord, connect, initialize, replace_pages, upsert_book
from tsundokensaku.pdf_extract import ExtractedPage


def _write_pdf(path: Path, pages: int) -> None:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    with path.open("wb") as handle:
        writer.write(handle)


def _index_pdf(root: Path, pdf_path: Path, pages: list[PageRecord], *, title: str = "索引本") -> Path:
    db_path = root / "index.db"
    connection = connect(db_path)
    try:
        initialize(connection)
        book_id = upsert_book(
            connection,
            path=pdf_path,
            title=title,
            size_bytes=pdf_path.stat().st_size,
            modified_at=pdf_path.stat().st_mtime,
        )
        replace_pages(connection, book_id=book_id, title=title, pages=pages)
        connection.commit()
    finally:
        connection.close()
    return db_path


class PdfTextServiceTest(unittest.TestCase):
    def test_load_pages_text_prefers_database_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"
            books_dir.mkdir()
            pdf_path = books_dir / "sample.pdf"
            _write_pdf(pdf_path, 1)
            db_path = _index_pdf(root, pdf_path, [PageRecord(page_number=1, text="DB本文")])

            with patch("tsundokensaku.pdf_text_service.extract_pages", side_effect=AssertionError("PDF fallback must not run")):
                texts = pdf_text_service.load_pages_text(pdf_path, [1], books_dir=books_dir, db_path=db_path)

        self.assertEqual(texts, {1: "DB本文"})

    def test_load_pages_text_falls_back_per_missing_page(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"
            books_dir.mkdir()
            pdf_path = books_dir / "sample.pdf"
            _write_pdf(pdf_path, 2)
            db_path = _index_pdf(root, pdf_path, [PageRecord(page_number=1, text="DB本文")])

            with patch(
                "tsundokensaku.pdf_text_service.extract_pages",
                return_value=[
                    ExtractedPage(page_number=1, text="PDF本文1"),
                    ExtractedPage(page_number=2, text="PDF本文2"),
                ],
            ):
                texts = pdf_text_service.load_pages_text(pdf_path, [1, 2], books_dir=books_dir, db_path=db_path)

        self.assertEqual(texts, {1: "DB本文", 2: "PDF本文2"})

    def test_load_pages_text_omits_pages_missing_from_db_and_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"
            books_dir.mkdir()
            pdf_path = books_dir / "sample.pdf"
            _write_pdf(pdf_path, 2)

            with patch(
                "tsundokensaku.pdf_text_service.extract_pages",
                return_value=[ExtractedPage(page_number=1, text="PDF本文1")],
            ):
                texts = pdf_text_service.load_pages_text(pdf_path, [1, 2], books_dir=books_dir, db_path=root / "missing.db")

        self.assertEqual(texts, {1: "PDF本文1"})

    def test_search_book_pages_reports_unindexed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"
            books_dir.mkdir()
            pdf_path = books_dir / "sample.pdf"
            _write_pdf(pdf_path, 1)

            result = pdf_text_service.search_book_pages(pdf_path, "query", books_dir=books_dir, db_path=root / "missing.db")

        self.assertFalse(result.indexed)
        self.assertEqual(result.pages, ())

    def test_search_book_pages_escapes_like_wildcards_and_limits_in_page_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"
            books_dir.mkdir()
            pdf_path = books_dir / "sample.pdf"
            _write_pdf(pdf_path, 5)
            db_path = _index_pdf(
                root,
                pdf_path,
                [
                    PageRecord(page_number=1, text="100%_LIKE literal"),
                    PageRecord(page_number=2, text="100XLIKE wildcard would match without escape"),
                    PageRecord(page_number=3, text="100%_LIKE second"),
                    PageRecord(page_number=4, text="100%_LIKE third"),
                    PageRecord(page_number=5, text="100%_LIKE fourth"),
                ],
            )

            result = pdf_text_service.search_book_pages(pdf_path, "%_LIKE", books_dir=books_dir, db_path=db_path, limit=3)

        self.assertTrue(result.indexed)
        self.assertEqual([hit.page_number for hit in result.pages], [1, 3, 4])
        self.assertTrue(all("%_LIKE" in hit.snippet for hit in result.pages))

    def test_page_snippet_keeps_existing_window_contract(self) -> None:
        text = "前置き " + "あ" * 30 + "needle" + "い" * 100
        snippet = pdf_text_service._page_snippet(text, "needle", width=30)

        self.assertTrue(snippet.startswith("…"))
        self.assertIn("needle", snippet)
        self.assertTrue(snippet.endswith("…"))

    def test_render_markdown_export_uses_indexed_title_text_filename_and_injected_time(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"
            books_dir.mkdir()
            pdf_path = books_dir / "sample.pdf"
            _write_pdf(pdf_path, 2)
            db_path = _index_pdf(
                root,
                pdf_path,
                [
                    PageRecord(page_number=1, text="1ページ目の本文"),
                    PageRecord(page_number=2, text="2ページ目の本文"),
                ],
                title="DBタイトル",
            )
            exported_at = datetime(2026, 8, 2, 12, 34, tzinfo=timezone.utc)

            markdown, filename = pdf_text_service.render_markdown_export(
                pdf_path,
                "1-2",
                books_dir=books_dir,
                db_path=db_path,
                exported_at=exported_at,
            )

        self.assertEqual(filename, "sample_p1-2.md")
        self.assertIn("# DBタイトル（抜粋）", markdown)
        self.assertIn("- 抽出日: 2026-08-02", markdown)
        self.assertIn("## p.1", markdown)
        self.assertIn("1ページ目の本文", markdown)
        self.assertIn("2ページ目の本文", markdown)

    def test_render_markdown_export_rejects_empty_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            books_dir = Path(temp_dir) / "books"
            books_dir.mkdir()
            pdf_path = books_dir / "sample.pdf"
            _write_pdf(pdf_path, 1)

            with self.assertRaisesRegex(ValueError, "pages is required"):
                pdf_text_service.render_markdown_export(
                    pdf_path,
                    " ",
                    books_dir=books_dir,
                    db_path=Path(temp_dir) / "missing.db",
                    exported_at=datetime.now(timezone.utc),
                )
