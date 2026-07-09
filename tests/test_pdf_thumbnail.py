import tempfile
import unittest
from pathlib import Path

import fitz

from tsundokensaku.pdf_thumbnail import render_thumbnails


def _make_pdf(path: Path, page_count: int) -> None:
    doc = fitz.open()
    for _ in range(page_count):
        doc.new_page(width=200, height=280)
    doc.save(str(path))
    doc.close()


class RenderThumbnailsTest(unittest.TestCase):
    def test_returns_jpeg_bytes_for_requested_pages_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "book.pdf"
            _make_pdf(pdf_path, 10)

            results = render_thumbnails(pdf_path, [3, 1, 5])

            self.assertEqual([page for page, _ in results], [3, 1, 5])
            for _, data in results:
                self.assertTrue(data.startswith(b"\xff\xd8"))  # JPEG magic bytes
                self.assertGreater(len(data), 0)

    def test_ignores_out_of_range_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "book.pdf"
            _make_pdf(pdf_path, 5)

            results = render_thumbnails(pdf_path, [0, 3, 6, 100])

            self.assertEqual([page for page, _ in results], [3])

    def test_higher_zoom_produces_larger_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "book.pdf"
            _make_pdf(pdf_path, 1)

            small = render_thumbnails(pdf_path, [1], zoom=0.1)
            large = render_thumbnails(pdf_path, [1], zoom=1.0)

            self.assertLess(len(small[0][1]), len(large[0][1]))


if __name__ == "__main__":
    unittest.main()
