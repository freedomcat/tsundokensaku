import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tsundokensaku.cli import main
from tsundokensaku.database import PageRecord, connect, initialize, replace_pages, search, upsert_book


class CliTest(unittest.TestCase):
    def test_refresh_titles_updates_existing_db_without_reindexing_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            export_json = root / "shino-books_20260628_000000.json"
            export_json.write_text(
                """
                {
                  "pages": [
                    {
                      "title": "Actual Title",
                      "lines": [
                        {"text": "#Bookscan #技術書"},
                        {"text": "https://system.bookscan.co.jp/sample?f=legacy.pdf"}
                      ]
                    }
                  ]
                }
                """,
                encoding="utf-8",
            )

            connection = connect(db_path)
            initialize(connection)
            book_id = upsert_book(
                connection,
                path=root / "legacy.pdf",
                filename="legacy.pdf",
                title="legacy",
                size_bytes=123,
                modified_at=1.0,
            )
            replace_pages(
                connection,
                book_id=book_id,
                title="legacy",
                pages=[PageRecord(page_number=1, text="body stays unchanged")],
            )
            page_before = connection.execute(
                "SELECT id, page_number, text FROM pages WHERE book_id = ?",
                (book_id,),
            ).fetchone()
            connection.close()

            stdout = io.StringIO()
            with patch("tsundokensaku.cli.PROJECT_ROOT", root), contextlib.redirect_stdout(stdout):
                exit_code = main(["refresh-titles", "--db", str(db_path)])

            connection = connect(db_path)
            try:
                results = search(connection, "Actual Title", scope="title")
                page_after = connection.execute(
                    "SELECT id, page_number, text FROM pages WHERE book_id = ?",
                    (book_id,),
                ).fetchone()
            finally:
                connection.close()

            self.assertEqual(exit_code, 0)
            self.assertIn("Updated PDF titles: 1", stdout.getvalue())
            self.assertEqual(dict(page_after), dict(page_before))
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].title, "Actual Title")

    def test_refresh_titles_dry_run_with_path_like_does_not_update(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            export_json = root / "shino-books_20260628_000000.json"
            export_json.write_text(
                """
                {
                  "pages": [
                    {
                      "title": "Target Title",
                      "lines": [
                        {"text": "#Bookscan #技術書"},
                        {"text": "https://system.bookscan.co.jp/sample?f=target.pdf"}
                      ]
                    },
                    {
                      "title": "Other Title",
                      "lines": [
                        {"text": "#Bookscan #技術書"},
                        {"text": "https://system.bookscan.co.jp/sample?f=other.pdf"}
                      ]
                    }
                  ]
                }
                """,
                encoding="utf-8",
            )

            connection = connect(db_path)
            initialize(connection)
            target_id = upsert_book(
                connection,
                path=root / "target.pdf",
                filename="target.pdf",
                title="target",
                size_bytes=123,
                modified_at=1.0,
            )
            other_id = upsert_book(
                connection,
                path=root / "other.pdf",
                filename="other.pdf",
                title="other",
                size_bytes=123,
                modified_at=1.0,
            )
            replace_pages(
                connection,
                book_id=target_id,
                title="target",
                pages=[PageRecord(page_number=1, text="target body")],
            )
            replace_pages(
                connection,
                book_id=other_id,
                title="other",
                pages=[PageRecord(page_number=1, text="other body")],
            )
            connection.close()

            stdout = io.StringIO()
            with patch("tsundokensaku.cli.PROJECT_ROOT", root), contextlib.redirect_stdout(stdout):
                exit_code = main(["refresh-titles", "--db", str(db_path), "--path-like", "target", "--dry-run"])

            connection = connect(db_path)
            try:
                rows = connection.execute("SELECT filename, title FROM books ORDER BY filename").fetchall()
            finally:
                connection.close()

            output = stdout.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("Target PDFs: 1", output)
            self.assertIn("target.pdf", output)
            self.assertNotIn("other.pdf", output)
            self.assertIn("Dry run: 1 title updates would be applied.", output)
            self.assertEqual([(row["filename"], row["title"]) for row in rows], [("other.pdf", "other"), ("target.pdf", "target")])

    def test_refresh_titles_pdf_path_updates_only_that_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            export_json = root / "shino-books_20260628_000000.json"
            target_pdf = root / "target.pdf"
            other_pdf = root / "other.pdf"
            export_json.write_text(
                """
                {
                  "pages": [
                    {
                      "title": "Target Title",
                      "lines": [
                        {"text": "#Bookscan #技術書"},
                        {"text": "https://system.bookscan.co.jp/sample?f=target.pdf"}
                      ]
                    },
                    {
                      "title": "Other Title",
                      "lines": [
                        {"text": "#Bookscan #技術書"},
                        {"text": "https://system.bookscan.co.jp/sample?f=other.pdf"}
                      ]
                    }
                  ]
                }
                """,
                encoding="utf-8",
            )

            connection = connect(db_path)
            initialize(connection)
            target_id = upsert_book(
                connection,
                path=target_pdf,
                filename="target.pdf",
                title="target",
                size_bytes=123,
                modified_at=1.0,
            )
            other_id = upsert_book(
                connection,
                path=other_pdf,
                filename="other.pdf",
                title="other",
                size_bytes=123,
                modified_at=1.0,
            )
            replace_pages(
                connection,
                book_id=target_id,
                title="target",
                pages=[PageRecord(page_number=1, text="target body")],
            )
            replace_pages(
                connection,
                book_id=other_id,
                title="other",
                pages=[PageRecord(page_number=1, text="other body")],
            )
            connection.close()

            stdout = io.StringIO()
            with patch("tsundokensaku.cli.PROJECT_ROOT", root), contextlib.redirect_stdout(stdout):
                exit_code = main(["refresh-titles", "--db", str(db_path), "--pdf-path", str(target_pdf)])

            connection = connect(db_path)
            try:
                rows = connection.execute("SELECT filename, title FROM books ORDER BY filename").fetchall()
            finally:
                connection.close()

            output = stdout.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("Target PDFs: 1", output)
            self.assertIn("Updated PDF titles: 1", output)
            self.assertEqual([(row["filename"], row["title"]) for row in rows], [("other.pdf", "other"), ("target.pdf", "Target Title")])


if __name__ == "__main__":
    unittest.main()
