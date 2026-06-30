import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from tsundokensaku.pdf_export import (
    compact_page_selection,
    default_output_path,
    export_selected_pages,
    parse_page_selection,
)


class ExportPdfPagesTest(unittest.TestCase):
    def test_parse_page_selection_supports_ranges_and_lists(self) -> None:
        self.assertEqual(parse_page_selection("1-3,5,7-8", 10), [1, 2, 3, 5, 7, 8])
        self.assertEqual(parse_page_selection(" 2 , 4-5 ", 5), [2, 4, 5])

    def test_compact_page_selection_merges_consecutive_pages(self) -> None:
        self.assertEqual(compact_page_selection([1, 2, 3, 5, 7, 8]), "1-3_5_7-8")

    def test_default_output_path_uses_page_selection(self) -> None:
        output = default_output_path(Path("/tmp/book.pdf"), [11, 12, 13, 20])
        self.assertEqual(output.name, "book_p11-13_20.pdf")

    def test_export_selected_pages_writes_only_requested_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_pdf = root / "input.pdf"
            output_pdf = root / "output.pdf"

            writer = PdfWriter()
            for _ in range(4):
                writer.add_blank_page(width=72, height=72)
            with input_pdf.open("wb") as handle:
                writer.write(handle)

            export_selected_pages(input_pdf, output_pdf, [2, 4])

            reader = PdfReader(str(output_pdf))
            self.assertEqual(len(reader.pages), 2)


if __name__ == "__main__":
    unittest.main()
