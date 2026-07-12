import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from tsundokensaku.pdf_export import (
    compact_page_selection,
    default_output_path,
    export_selected_pages,
    merge_rendered_pdfs,
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

    def test_merge_rendered_pdfs_returns_single_pdf(self) -> None:
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        writer.add_metadata({"/Title": "single"})
        buffer = BytesIO()
        writer.write(buffer)

        merged = merge_rendered_pdfs([buffer.getvalue()])
        reader = PdfReader(BytesIO(merged))
        self.assertEqual(len(reader.pages), 1)
        self.assertEqual(reader.metadata.get("/Title"), "single")

    def test_merge_rendered_pdfs_preserves_page_order(self) -> None:
        first = PdfWriter()
        first.add_blank_page(width=72, height=72)
        first.add_blank_page(width=72, height=72)
        first_buffer = BytesIO()
        first.write(first_buffer)

        second = PdfWriter()
        second.add_blank_page(width=72, height=72)
        second_buffer = BytesIO()
        second.write(second_buffer)

        merged = merge_rendered_pdfs([first_buffer.getvalue(), second_buffer.getvalue()])
        reader = PdfReader(BytesIO(merged))
        self.assertEqual(len(reader.pages), 3)

    def test_merge_rendered_pdfs_copies_metadata_from_first_pdf(self) -> None:
        first = PdfWriter()
        first.add_blank_page(width=72, height=72)
        first.add_metadata({"/Title": "first", "/Author": "alice"})
        first_buffer = BytesIO()
        first.write(first_buffer)

        second = PdfWriter()
        second.add_blank_page(width=72, height=72)
        second.add_metadata({"/Title": "second"})
        second_buffer = BytesIO()
        second.write(second_buffer)

        merged = merge_rendered_pdfs([first_buffer.getvalue(), second_buffer.getvalue()])
        reader = PdfReader(BytesIO(merged))
        self.assertEqual(reader.metadata.get("/Title"), "first")
        self.assertEqual(reader.metadata.get("/Author"), "alice")

    def test_merge_rendered_pdfs_succeeds_when_metadata_missing(self) -> None:
        first = PdfWriter()
        first.add_blank_page(width=72, height=72)
        first_buffer = BytesIO()
        first.write(first_buffer)

        second = PdfWriter()
        second.add_blank_page(width=72, height=72)
        second_buffer = BytesIO()
        second.write(second_buffer)

        merged = merge_rendered_pdfs([first_buffer.getvalue(), second_buffer.getvalue()])
        reader = PdfReader(BytesIO(merged))
        self.assertEqual(len(reader.pages), 2)

    def test_merge_rendered_pdfs_rejects_empty_input(self) -> None:
        with self.assertRaises(ValueError):
            merge_rendered_pdfs([])


if __name__ == "__main__":
    unittest.main()
