import unittest
from pathlib import Path

from tsundokensaku.pdf_extract import extract_pages


class PdfExtractTest(unittest.TestCase):
    def test_bundled_noosphere_pdf_extracts_readable_japanese(self) -> None:
        pdf_path = Path("data/books/noosphere.pdf")

        first_page = next(extract_pages(pdf_path))

        self.assertIn("ノウアスフィアの開墾", first_page.text)
        self.assertIn("オープンソース", first_page.text)


if __name__ == "__main__":
    unittest.main()
