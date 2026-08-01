import tempfile
import unittest
from pathlib import Path

from tsundokensaku.pdf_import_service import save_uploaded_pdf


class PdfImportServiceTest(unittest.TestCase):
    def test_save_uploaded_pdf_writes_unique_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            books_dir = Path(temp_dir) / "books"
            first = save_uploaded_pdf("sample.pdf", b"%PDF-1.4 first", books_dir)
            second = save_uploaded_pdf("sample.pdf", b"%PDF-1.4 second", books_dir)

            self.assertEqual(first, books_dir / "sample.pdf")
            self.assertEqual(second, books_dir / "sample (2).pdf")
            self.assertEqual(first.read_bytes(), b"%PDF-1.4 first")
            self.assertEqual(second.read_bytes(), b"%PDF-1.4 second")

    def test_save_uploaded_pdf_writes_nested_relative_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            books_dir = Path(temp_dir) / "books"

            saved = save_uploaded_pdf(
                "ignored.pdf",
                b"%PDF-1.4 nested",
                books_dir,
                relative_path="sub/dir/book.pdf",
            )

            self.assertEqual(saved, books_dir / "sub" / "dir" / "book.pdf")
            self.assertEqual(saved.read_bytes(), b"%PDF-1.4 nested")

    def test_save_uploaded_pdf_rejects_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"

            with self.assertRaisesRegex(ValueError, "保存先が不正です"):
                save_uploaded_pdf(
                    "ignored.pdf",
                    b"%PDF-1.4 traversal",
                    books_dir,
                    relative_path="../escape.pdf",
                )

            self.assertFalse((root / "escape.pdf").exists())

    def test_save_uploaded_pdf_rejects_absolute_path_outside_books_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"
            outside = root / "outside.pdf"

            with self.assertRaisesRegex(ValueError, "保存先が不正です"):
                save_uploaded_pdf(
                    "ignored.pdf",
                    b"%PDF-1.4 absolute",
                    books_dir,
                    relative_path=str(outside),
                )

            self.assertFalse(outside.exists())

    def test_save_uploaded_pdf_rejects_intermediate_symlink_to_outside(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"
            outside_dir = root / "outside"
            books_dir.mkdir()
            outside_dir.mkdir()
            link = books_dir / "outside-link"
            try:
                link.symlink_to(outside_dir, target_is_directory=True)
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"symlink creation is unavailable: {exc}")

            with self.assertRaisesRegex(ValueError, "保存先が不正です"):
                save_uploaded_pdf(
                    "ignored.pdf",
                    b"%PDF-1.4 symlink",
                    books_dir,
                    relative_path="outside-link/escape.pdf",
                )

            self.assertFalse((outside_dir / "escape.pdf").exists())

    def test_save_uploaded_pdf_rejects_destination_symlink_to_outside(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"
            books_dir.mkdir()
            outside = root / "outside.pdf"
            outside.write_bytes(b"outside content")
            link = books_dir / "linked.pdf"
            try:
                link.symlink_to(outside)
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"symlink creation is unavailable: {exc}")

            with self.assertRaisesRegex(ValueError, "保存先が不正です"):
                save_uploaded_pdf("linked.pdf", b"%PDF-1.4 replacement", books_dir)

            self.assertEqual(outside.read_bytes(), b"outside content")

    def test_save_uploaded_pdf_accepts_uppercase_pdf_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            books_dir = Path(temp_dir) / "books"

            saved = save_uploaded_pdf("sample.PDF", b"%PDF-1.4 uppercase", books_dir)

            self.assertEqual(saved, books_dir / "sample.PDF")
            self.assertEqual(saved.read_bytes(), b"%PDF-1.4 uppercase")

    def test_save_uploaded_pdf_rejects_space_after_pdf_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            books_dir = Path(temp_dir) / "books"

            with self.assertRaisesRegex(ValueError, "PDF ファイルのみ受け付けます"):
                save_uploaded_pdf("sample.pdf ", b"%PDF-1.4 trailing-space", books_dir)

    def test_save_uploaded_pdf_preserves_supported_filename_characters(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            books_dir = Path(temp_dir) / "books"
            cases = (
                ("読書メモ.pdf", b"%PDF-1.4 unicode"),
                (" 読書 メモ.pdf", b"%PDF-1.4 spaces"),
            )

            for filename, content in cases:
                with self.subTest(filename=filename):
                    saved = save_uploaded_pdf(filename, content, books_dir)
                    self.assertEqual(saved, books_dir / filename)
                    self.assertEqual(saved.read_bytes(), content)
