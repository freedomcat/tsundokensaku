import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter

from tsundokensaku import pdf_export


def _write_pdf(path: Path, pages: int) -> None:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    with path.open("wb") as handle:
        writer.write(handle)


class PdfExportServiceTest(unittest.TestCase):
    def test_parse_page_selection_keeps_existing_grammar(self) -> None:
        self.assertEqual(pdf_export.parse_page_selection("1, 3-4, -2, 5-", 5), [1, 3, 4, 2, 5])

    def test_parse_page_selection_rejects_empty_and_invalid_pages(self) -> None:
        with self.assertRaisesRegex(ValueError, "No pages selected"):
            pdf_export.parse_page_selection(" , ", 3)
        with self.assertRaisesRegex(ValueError, "Invalid page range: 3-1"):
            pdf_export.parse_page_selection("3-1", 3)
        with self.assertRaisesRegex(ValueError, "Page number out of range: 4 \\(1-3\\)"):
            pdf_export.parse_page_selection("4", 3)

    def test_render_pdf_export_returns_pdf_bytes_and_filename(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "日本語.pdf"
            _write_pdf(pdf_path, 4)

            content, filename = pdf_export.render_pdf_export(pdf_path, "2-3")

            self.assertEqual(filename, "日本語_p2-3.pdf")
            self.assertEqual(len(PdfReader(BytesIO(content)).pages), 2)

    def test_render_pdf_export_rejects_empty_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "sample.pdf"
            _write_pdf(pdf_path, 1)
            with self.assertRaisesRegex(ValueError, "pages is required"):
                pdf_export.render_pdf_export(pdf_path, "  ")

    def test_save_pdf_export_validates_save_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"
            books_dir.mkdir()
            _write_pdf(books_dir / "sample.pdf", 1)

            with self.assertRaisesRegex(ValueError, "保存先フォルダが未設定です"):
                pdf_export.save_pdf_export_to_configured_dir("sample.pdf", "1", books_dir=books_dir, save_dir=None)
            with self.assertRaises(FileNotFoundError):
                pdf_export.save_pdf_export_to_configured_dir("sample.pdf", "1", books_dir=books_dir, save_dir=root / "missing")
            not_dir = root / "not-dir"
            not_dir.write_text("x", encoding="utf-8")
            with self.assertRaises(NotADirectoryError):
                pdf_export.save_pdf_export_to_configured_dir("sample.pdf", "1", books_dir=books_dir, save_dir=not_dir)

    def test_save_pdf_export_reports_pdf_source_missing_separately(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"
            save_dir = root / "exports"
            books_dir.mkdir()
            save_dir.mkdir()

            with self.assertRaises(pdf_export.PdfSourceNotFoundError):
                pdf_export.save_pdf_export_to_configured_dir("missing.pdf", "1", books_dir=books_dir, save_dir=save_dir)

    def test_save_pdf_export_validates_save_dir_before_pdf_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"
            books_dir.mkdir()

            with self.assertRaises(FileNotFoundError):
                pdf_export.save_pdf_export_to_configured_dir("missing.pdf", "1", books_dir=books_dir, save_dir=root / "missing")

    def test_save_pdf_export_basename_boundary_and_duplicate_filename(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"
            save_dir = root / "exports"
            books_dir.mkdir()
            save_dir.mkdir()
            _write_pdf(books_dir / "sample.pdf", 2)
            (save_dir / "evil.pdf").write_bytes(b"existing")

            with patch("tsundokensaku.pdf_export.render_pdf_export", return_value=(b"%PDF-1.4\n", "../evil.pdf")):
                saved = pdf_export.save_pdf_export_to_configured_dir("sample.pdf", "1", books_dir=books_dir, save_dir=save_dir)

            self.assertEqual(saved, save_dir / "evil_2.pdf")
            self.assertTrue(saved.exists())

    def test_save_pdf_export_propagates_write_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"
            save_dir = root / "exports"
            books_dir.mkdir()
            save_dir.mkdir()
            _write_pdf(books_dir / "sample.pdf", 1)

            with patch("pathlib.Path.write_bytes", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    pdf_export.save_pdf_export_to_configured_dir("sample.pdf", "1", books_dir=books_dir, save_dir=save_dir)
