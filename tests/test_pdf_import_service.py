import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tsundokensaku.pdf_import_service import (
    PdfDirectoryBoundaryError,
    PdfDirectoryFilesystemError,
    PdfDirectoryImportResult,
    PdfDirectoryOverlapError,
    PdfDirectorySourceNotDirectoryError,
    PdfDirectorySourceNotFoundError,
    import_pdfs_from_directory,
    save_uploaded_pdf,
)


class PdfImportServiceTest(unittest.TestCase):
    def _symlink_or_skip(self, link: Path, target: Path, *, target_is_directory: bool = False) -> None:
        try:
            link.symlink_to(target, target_is_directory=target_is_directory)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"symlink creation is unavailable: {exc}")

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

    def test_pdf_directory_import_result_accepts_valid_counts(self) -> None:
        result = PdfDirectoryImportResult(copied=2, skipped=1, total=3)

        self.assertEqual(result.copied, 2)
        self.assertEqual(result.skipped, 1)
        self.assertEqual(result.total, 3)

    def test_pdf_directory_import_result_rejects_negative_counts(self) -> None:
        with self.assertRaises(ValueError):
            PdfDirectoryImportResult(copied=-1, skipped=1, total=0)

    def test_pdf_directory_import_result_rejects_mismatched_total(self) -> None:
        with self.assertRaises(ValueError):
            PdfDirectoryImportResult(copied=1, skipped=1, total=3)

    def test_import_pdfs_from_directory_keeps_nested_structure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "source"
            books_dir = root / "books"
            nested_dir = source_dir / "nested"
            nested_dir.mkdir(parents=True)
            (source_dir / "a.pdf").write_bytes(b"%PDF-1.4 a")
            (nested_dir / "b.pdf").write_bytes(b"%PDF-1.4 b")

            result = import_pdfs_from_directory(source_dir, books_dir)

            self.assertEqual(result, PdfDirectoryImportResult(copied=2, skipped=0, total=2))
            self.assertEqual((books_dir / "a.pdf").read_bytes(), b"%PDF-1.4 a")
            self.assertEqual((books_dir / "nested" / "b.pdf").read_bytes(), b"%PDF-1.4 b")

    def test_import_pdfs_from_directory_skips_existing_regular_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "source"
            books_dir = root / "books"
            source_dir.mkdir()
            books_dir.mkdir()
            (source_dir / "a.pdf").write_bytes(b"%PDF-1.4 a")
            existing = books_dir / "a.pdf"
            existing.write_bytes(b"existing")

            result = import_pdfs_from_directory(source_dir, books_dir)

            self.assertEqual(result, PdfDirectoryImportResult(copied=0, skipped=1, total=1))
            self.assertEqual(existing.read_bytes(), b"existing")

    def test_import_pdfs_from_directory_rejects_directory_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "source"
            books_dir = root / "books"
            source_dir.mkdir()
            (source_dir / "a.pdf").write_bytes(b"%PDF-1.4 a")
            (books_dir / "a.pdf").mkdir(parents=True)

            with self.assertRaises(PdfDirectoryFilesystemError):
                import_pdfs_from_directory(source_dir, books_dir)

    def test_import_pdfs_from_directory_classifies_source_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            missing_source = root / "missing"
            file_source = root / "file.pdf"
            file_source.write_bytes(b"%PDF-1.4 source")

            with self.assertRaises(PdfDirectorySourceNotFoundError):
                import_pdfs_from_directory(missing_source, root / "books")
            with self.assertRaises(PdfDirectorySourceNotDirectoryError):
                import_pdfs_from_directory(file_source, root / "books")

    def test_import_pdfs_from_directory_rejects_source_root_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            real_source = root / "real-source"
            real_source.mkdir()
            link = root / "source-link"
            self._symlink_or_skip(link, real_source, target_is_directory=True)

            with self.assertRaises(PdfDirectoryBoundaryError):
                import_pdfs_from_directory(link, root / "books")

    def test_import_pdfs_from_directory_rejects_broken_source_root_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            link = root / "source-link"
            self._symlink_or_skip(link, root / "missing", target_is_directory=True)

            with self.assertRaises(PdfDirectoryBoundaryError):
                import_pdfs_from_directory(link, root / "books")

    def test_import_pdfs_from_directory_allows_books_dir_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "source"
            books_target = root / "books-target"
            books_link = root / "books-link"
            source_dir.mkdir()
            books_target.mkdir()
            self._symlink_or_skip(books_link, books_target, target_is_directory=True)
            (source_dir / "a.pdf").write_bytes(b"%PDF-1.4 a")

            result = import_pdfs_from_directory(source_dir, books_link)

            self.assertEqual(result, PdfDirectoryImportResult(copied=1, skipped=0, total=1))
            self.assertEqual((books_target / "a.pdf").read_bytes(), b"%PDF-1.4 a")

    def test_import_pdfs_from_directory_rejects_broken_books_dir_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "source"
            source_dir.mkdir()
            link = root / "books-link"
            self._symlink_or_skip(link, root / "missing", target_is_directory=True)

            with self.assertRaises(PdfDirectoryBoundaryError):
                import_pdfs_from_directory(source_dir, link)

    def test_import_pdfs_from_directory_rejects_overlapping_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "source"
            books_dir = root / "books"
            source_dir.mkdir()
            books_dir.mkdir()
            nested_source = books_dir / "source"
            nested_source.mkdir()
            cases = (
                (source_dir, source_dir),
                (nested_source, books_dir),
                (source_dir, source_dir / "books"),
            )

            for source, books in cases:
                with self.subTest(source=source, books=books):
                    with self.assertRaises(PdfDirectoryOverlapError):
                        import_pdfs_from_directory(source, books)

    def test_import_pdfs_from_directory_empty_source_counts_zero(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "source"
            source_dir.mkdir()

            result = import_pdfs_from_directory(source_dir, root / "books")

            self.assertEqual(result, PdfDirectoryImportResult(copied=0, skipped=0, total=0))

    def test_import_pdfs_from_directory_uses_only_lowercase_pdf_and_preserves_names(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "source"
            nested_dir = source_dir / "空 白"
            nested_dir.mkdir(parents=True)
            (source_dir / "upper.PDF").write_bytes(b"%PDF-1.4 upper")
            (source_dir / "note.txt").write_text("not pdf")
            (nested_dir / "読書 メモ.pdf").write_bytes(b"%PDF-1.4 unicode")

            result = import_pdfs_from_directory(source_dir, root / "books")

            self.assertEqual(result, PdfDirectoryImportResult(copied=1, skipped=0, total=1))
            self.assertEqual((root / "books" / "空 白" / "読書 メモ.pdf").read_bytes(), b"%PDF-1.4 unicode")
            self.assertFalse((root / "books" / "upper.PDF").exists())

    def test_import_pdfs_from_directory_skips_pdf_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "source"
            outside = root / "outside.pdf"
            source_dir.mkdir()
            outside.write_bytes(b"%PDF-1.4 outside")
            (source_dir / "real.pdf").write_bytes(b"%PDF-1.4 real")
            self._symlink_or_skip(source_dir / "inside-link.pdf", source_dir / "real.pdf")
            self._symlink_or_skip(source_dir / "outside-link.pdf", outside)

            result = import_pdfs_from_directory(source_dir, root / "books")

            self.assertEqual(result, PdfDirectoryImportResult(copied=1, skipped=0, total=1))
            self.assertTrue((root / "books" / "real.pdf").exists())
            self.assertFalse((root / "books" / "inside-link.pdf").exists())
            self.assertFalse((root / "books" / "outside-link.pdf").exists())

    def test_import_pdfs_from_directory_does_not_recurse_symlink_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "source"
            outside_dir = root / "outside"
            source_dir.mkdir()
            outside_dir.mkdir()
            (outside_dir / "outside.pdf").write_bytes(b"%PDF-1.4 outside")
            self._symlink_or_skip(source_dir / "linked-dir", outside_dir, target_is_directory=True)

            result = import_pdfs_from_directory(source_dir, root / "books")

            self.assertEqual(result, PdfDirectoryImportResult(copied=0, skipped=0, total=0))
            self.assertFalse((root / "books" / "linked-dir" / "outside.pdf").exists())

    def test_import_pdfs_from_directory_rejects_destination_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for target_name, target in (
                ("inside", root / "books" / "inside-target.pdf"),
                ("outside", root / "outside.pdf"),
            ):
                with self.subTest(target=target_name):
                    source_dir = root / f"source-{target_name}"
                    books_dir = root / f"books-{target_name}"
                    source_dir.mkdir()
                    books_dir.mkdir()
                    (source_dir / "a.pdf").write_bytes(b"%PDF-1.4 a")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(b"target")
                    self._symlink_or_skip(books_dir / "a.pdf", target)

                    with self.assertRaises(PdfDirectoryBoundaryError):
                        import_pdfs_from_directory(source_dir, books_dir)

    def test_import_pdfs_from_directory_rejects_broken_destination_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "source"
            books_dir = root / "books"
            source_dir.mkdir()
            books_dir.mkdir()
            (source_dir / "a.pdf").write_bytes(b"%PDF-1.4 a")
            self._symlink_or_skip(books_dir / "a.pdf", root / "missing.pdf")

            with self.assertRaises(PdfDirectoryBoundaryError):
                import_pdfs_from_directory(source_dir, books_dir)

    def test_import_pdfs_from_directory_rejects_intermediate_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "source"
            outside_dir = root / "outside"
            source_nested = source_dir / "nested"
            books_dir = root / "books"
            source_nested.mkdir(parents=True)
            outside_dir.mkdir()
            books_dir.mkdir()
            (source_nested / "a.pdf").write_bytes(b"%PDF-1.4 a")
            self._symlink_or_skip(books_dir / "nested", outside_dir, target_is_directory=True)

            with self.assertRaises(PdfDirectoryBoundaryError):
                import_pdfs_from_directory(source_dir, books_dir)

    def test_import_pdfs_from_directory_rejects_broken_intermediate_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "source"
            source_nested = source_dir / "nested"
            books_dir = root / "books"
            source_nested.mkdir(parents=True)
            books_dir.mkdir()
            (source_nested / "a.pdf").write_bytes(b"%PDF-1.4 a")
            self._symlink_or_skip(books_dir / "nested", root / "missing", target_is_directory=True)

            with self.assertRaises(PdfDirectoryBoundaryError):
                import_pdfs_from_directory(source_dir, books_dir)

    def test_import_pdfs_from_directory_converts_walk_mkdir_and_copy_os_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "source"
            source_dir.mkdir()
            (source_dir / "a.pdf").write_bytes(b"%PDF-1.4 a")

            with patch("tsundokensaku.pdf_import_service.os.walk", side_effect=OSError("walk failed")):
                with self.assertRaises(PdfDirectoryFilesystemError) as walk_context:
                    import_pdfs_from_directory(source_dir, root / "books-walk")
            self.assertIsInstance(walk_context.exception.__cause__, OSError)

            blocking_parent = root / "books-mkdir" / "nested"
            blocking_source = root / "source-mkdir"
            (blocking_source / "nested").mkdir(parents=True)
            (blocking_source / "nested" / "a.pdf").write_bytes(b"%PDF-1.4 a")
            blocking_parent.parent.mkdir()
            blocking_parent.write_bytes(b"not a directory")
            with self.assertRaises(PdfDirectoryFilesystemError) as mkdir_context:
                import_pdfs_from_directory(blocking_source, root / "books-mkdir")
            self.assertIsInstance(mkdir_context.exception.__cause__, OSError)

            with patch("tsundokensaku.pdf_import_service.shutil.copy2", side_effect=OSError("copy failed")):
                with self.assertRaises(PdfDirectoryFilesystemError) as copy_context:
                    import_pdfs_from_directory(source_dir, root / "books-copy")
            self.assertIsInstance(copy_context.exception.__cause__, OSError)

    def test_import_pdfs_from_directory_does_not_convert_programming_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "source"
            source_dir.mkdir()
            (source_dir / "a.pdf").write_bytes(b"%PDF-1.4 a")

            with patch("tsundokensaku.pdf_import_service.shutil.copy2", side_effect=RuntimeError("bug")):
                with self.assertRaises(RuntimeError):
                    import_pdfs_from_directory(source_dir, root / "books")

    def test_import_pdfs_from_directory_is_deterministic_and_fails_fast_without_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "source"
            books_dir = root / "books"
            ok_dir = source_dir / "01-ok"
            fail_dir = source_dir / "02-fail"
            ok_dir.mkdir(parents=True)
            fail_dir.mkdir()
            (ok_dir / "a.pdf").write_bytes(b"%PDF-1.4 a")
            (fail_dir / "b.pdf").write_bytes(b"%PDF-1.4 b")
            books_dir.mkdir()
            (books_dir / "02-fail").write_bytes(b"file blocks mkdir")

            with self.assertRaises(PdfDirectoryFilesystemError):
                import_pdfs_from_directory(source_dir, books_dir)

            self.assertEqual((books_dir / "01-ok" / "a.pdf").read_bytes(), b"%PDF-1.4 a")
            self.assertFalse((books_dir / "02-fail" / "b.pdf").exists())
