import tempfile
import unittest
from pathlib import Path

from tsundokensaku.database import (
    PageRecord,
    connect,
    initialize,
    replace_pages,
    search,
    upsert_book,
)


class DatabaseSearchTest(unittest.TestCase):
    def test_search_returns_book_page_and_snippet(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            connection = connect(db_path)
            initialize(connection)
            book_id = upsert_book(
                connection,
                path=Path("books/tech/sqlite-guide.pdf"),
                title="sqlite-guide",
                size_bytes=123,
                modified_at=1.0,
            )
            replace_pages(
                connection,
                book_id=book_id,
                title="sqlite-guide",
                pages=[
                    PageRecord(page_number=1, text="SQLite stores data in local files."),
                    PageRecord(page_number=2, text="FTS5 provides full text search."),
                ],
            )

            results = search(connection, "FTS5")

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].title, "sqlite-guide")
            self.assertEqual(results[0].page_number, 2)
            self.assertIn("FTS5", results[0].snippet)
            connection.close()

    def test_search_falls_back_to_like_for_substring(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            connection = connect(db_path)
            initialize(connection)
            book_id = upsert_book(
                connection,
                path=Path("books/tech/python.pdf"),
                title="python",
                size_bytes=123,
                modified_at=1.0,
            )
            replace_pages(
                connection,
                book_id=book_id,
                title="python",
                pages=[PageRecord(page_number=3, text="dataclasses make records simple")],
            )

            results = search(connection, "class")

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].page_number, 3)
            connection.close()


if __name__ == "__main__":
    unittest.main()
