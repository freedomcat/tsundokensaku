import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tsundokensaku import pdf_metadata_service
from tsundokensaku.database import connect, initialize, upsert_book
from tsundokensaku.metadata import BookMetadata


class PdfMetadataServiceTest(unittest.TestCase):
    def test_find_indexed_book_uses_resolved_relative_path_without_re_resolving(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"
            books_dir.mkdir()
            db_path = root / "index.db"
            connection = connect(db_path)
            try:
                initialize(connection)
                upsert_book(
                    connection,
                    path=Path("sub/sample.pdf"),
                    title="相対本",
                    size_bytes=10,
                    modified_at=1.0,
                )
                connection.commit()

                with patch(
                    "tsundokensaku.pdf_metadata_service.paths.resolve_pdf_path",
                    side_effect=AssertionError("service must not re-resolve"),
                ):
                    book = pdf_metadata_service.find_indexed_book(connection, Path("sub/sample.pdf"), books_dir=books_dir)
            finally:
                connection.close()

        self.assertIsNotNone(book)
        self.assertEqual(book.title, "相対本")

    def test_find_indexed_book_matches_absolute_db_path_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"
            books_dir.mkdir()
            pdf_path = books_dir / "sample.pdf"
            db_path = root / "index.db"
            connection = connect(db_path)
            try:
                initialize(connection)
                upsert_book(
                    connection,
                    path=pdf_path,
                    title="絶対本",
                    size_bytes=10,
                    modified_at=1.0,
                )
                connection.commit()
                book = pdf_metadata_service.find_indexed_book(connection, Path("sample.pdf"), books_dir=books_dir)
            finally:
                connection.close()

        self.assertIsNotNone(book)
        self.assertEqual(book.title, "絶対本")

    def test_find_indexed_book_treats_operational_error_as_unindexed(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        try:
            self.assertIsNone(pdf_metadata_service.find_indexed_book(connection, Path("sample.pdf"), books_dir=Path("/books")))
        finally:
            connection.close()

    def test_get_indexed_book_closes_connection(self) -> None:
        class FakeConnection:
            closed = False

            def close(self) -> None:
                self.closed = True

        fake = FakeConnection()
        with (
            patch("tsundokensaku.pdf_metadata_service.connect", return_value=fake),
            patch("tsundokensaku.pdf_metadata_service.find_indexed_book", return_value=None),
        ):
            result = pdf_metadata_service.get_indexed_book(Path("sample.pdf"), books_dir=Path("/books"), db_path=Path("db.sqlite"))

        self.assertIsNone(result)
        self.assertTrue(fake.closed)

    def test_resolve_pdf_scrapbox_url_prefers_database_and_skips_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"
            books_dir.mkdir()
            pdf_path = books_dir / "sample.pdf"
            pdf_path.write_bytes(b"%PDF-1.4 sample")
            db_path = root / "index.db"
            connection = connect(db_path)
            try:
                initialize(connection)
                upsert_book(
                    connection,
                    path=pdf_path,
                    title="DB本",
                    size_bytes=pdf_path.stat().st_size,
                    modified_at=pdf_path.stat().st_mtime,
                    scrapbox_url="https://scrapbox.io/db/sample",
                )
                connection.commit()
            finally:
                connection.close()

            with patch("tsundokensaku.pdf_metadata_service.find_export_json", side_effect=AssertionError("fallback must be lazy")):
                url = pdf_metadata_service.resolve_pdf_scrapbox_url(
                    "sample.pdf",
                    books_dir=books_dir,
                    db_path=db_path,
                    project_root=root,
                )

        self.assertEqual(url, "https://scrapbox.io/db/sample")

    def test_resolve_pdf_scrapbox_url_uses_export_metadata_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"
            books_dir.mkdir()
            (books_dir / "sample.pdf").write_bytes(b"%PDF-1.4 sample")

            with (
                patch("tsundokensaku.pdf_metadata_service.find_export_json", return_value=root / "export.json") as find_mock,
                patch(
                    "tsundokensaku.pdf_metadata_service.load_metadata_by_pdf_stem",
                    return_value={"sample": BookMetadata(title="sample", scrapbox_url="https://scrapbox.io/export/sample")},
                ) as load_mock,
            ):
                url = pdf_metadata_service.resolve_pdf_scrapbox_url(
                    "sample.pdf",
                    books_dir=books_dir,
                    db_path=root / "missing.db",
                    project_root=root,
                )

        self.assertEqual(url, "https://scrapbox.io/export/sample")
        find_mock.assert_called_once_with(root)
        load_mock.assert_called_once_with(root / "export.json")
