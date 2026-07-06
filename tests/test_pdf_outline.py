import tempfile
import unittest
from pathlib import Path

import fitz

from tsundokensaku.pdf_outline import get_page_count, list_chapters


def _make_pdf_with_toc(path: Path, page_count: int, toc: list[list]) -> None:
    doc = fitz.open()
    for _ in range(page_count):
        doc.new_page(width=72, height=72)
    if toc:
        doc.set_toc(toc)
    doc.save(str(path))
    doc.close()


class ListChaptersTest(unittest.TestCase):
    def test_list_chapters_resolves_page_ranges_across_levels(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "book.pdf"
            _make_pdf_with_toc(
                pdf_path,
                10,
                [
                    [1, "第1章", 1],
                    [2, "1.1節", 2],
                    [1, "第2章", 5],
                    [1, "第3章", 9],
                ],
            )

            chapters = list_chapters(pdf_path)

            self.assertEqual(len(chapters), 4)
            self.assertEqual((chapters[0].title, chapters[0].start_page, chapters[0].end_page), ("第1章", 1, 5))
            self.assertEqual((chapters[1].title, chapters[1].start_page, chapters[1].end_page), ("1.1節", 2, 5))
            self.assertEqual((chapters[2].title, chapters[2].start_page, chapters[2].end_page), ("第2章", 5, 9))
            self.assertEqual((chapters[3].title, chapters[3].start_page, chapters[3].end_page), ("第3章", 9, 10))
            self.assertEqual(chapters[1].level, 2)

    def test_list_chapters_clamps_same_page_siblings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "book.pdf"
            _make_pdf_with_toc(pdf_path, 4, [[1, "A", 3], [1, "B", 3]])

            chapters = list_chapters(pdf_path)

            self.assertEqual((chapters[0].start_page, chapters[0].end_page), (3, 3))
            self.assertEqual((chapters[1].start_page, chapters[1].end_page), (3, 4))

    def test_list_chapters_skips_out_of_range_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "book.pdf"
            _make_pdf_with_toc(pdf_path, 3, [[1, "壊れた", -1], [1, "正常", 2]])

            chapters = list_chapters(pdf_path)

            self.assertEqual(len(chapters), 1)
            self.assertEqual(chapters[0].title, "正常")

    def test_get_page_count_returns_document_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "book.pdf"
            _make_pdf_with_toc(pdf_path, 5, [])

            self.assertEqual(get_page_count(pdf_path), 5)

    def test_get_page_count_returns_none_for_broken_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "broken.pdf"
            pdf_path.write_bytes(b"not a pdf")

            self.assertIsNone(get_page_count(pdf_path))

    def test_list_chapters_returns_empty_without_toc(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "book.pdf"
            _make_pdf_with_toc(pdf_path, 3, [])

            self.assertEqual(list_chapters(pdf_path), [])


if __name__ == "__main__":
    unittest.main()
