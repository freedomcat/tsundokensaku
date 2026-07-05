import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter

from tsundokensaku.database import connect
from tsundokensaku.indexer import _progress_bar, index_books
from tsundokensaku.pdf_extract import ExtractedPage


class IndexerIncrementalTest(unittest.TestCase):
    def test_progress_bar_uses_fixed_width(self) -> None:
        self.assertEqual(_progress_bar(0, 10), "[........................] 0/10")
        self.assertEqual(_progress_bar(5, 10), "[############............] 5/10")
        self.assertEqual(_progress_bar(10, 10), "[########################] 10/10")

    def test_index_books_skips_unchanged_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books" / "tech"
            books_dir.mkdir(parents=True)
            pdf_path = books_dir / "sample.pdf"
            pdf_path.write_bytes(b"%PDF-1.4 sample")

            db_path = root / "data" / "index.db"

            with patch("tsundokensaku.indexer.extract_pages", return_value=[ExtractedPage(1, "hello")]) as extract_pages:
                first = index_books(books_dir=books_dir, db_path=db_path)
                second = index_books(books_dir=books_dir, db_path=db_path)

            self.assertEqual(len(first), 1)
            self.assertEqual(len(second), 0)
            self.assertEqual(extract_pages.call_count, 1)

    def test_index_books_uses_pdf_metadata_title(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books" / "tech"
            books_dir.mkdir(parents=True)
            pdf_path = books_dir / "Programming_Ruby_5th_ja.pdf"

            writer = PdfWriter()
            writer.add_blank_page(width=72, height=72)
            writer.add_metadata({"/Title": "Programming Ruby 5th Edition"})
            with pdf_path.open("wb") as handle:
                writer.write(handle)

            db_path = root / "data" / "index.db"

            with patch("tsundokensaku.indexer.extract_pages", return_value=[ExtractedPage(1, "hello")]):
                index_books(books_dir=books_dir, db_path=db_path)

            connection = connect(db_path)
            try:
                row = connection.execute("SELECT title, filename FROM books WHERE path = ?", (str(pdf_path),)).fetchone()
            finally:
                connection.close()

            self.assertIsNotNone(row)
            self.assertEqual(row["title"], "Programming Ruby 5th Edition")
            self.assertEqual(row["filename"], "Programming_Ruby_5th_ja.pdf")


if __name__ == "__main__":
    unittest.main()
