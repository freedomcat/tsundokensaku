import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tsundokensaku.indexer import index_books
from tsundokensaku.pdf_extract import ExtractedPage


class IndexerIncrementalTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
