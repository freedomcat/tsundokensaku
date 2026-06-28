import tempfile
import unittest
from pathlib import Path

from tsundokensaku.database import (
    PageRecord,
    replace_memos,
    connect,
    initialize,
    replace_pages,
    search,
    upsert_book,
)
from tsundokensaku.metadata import ScrapboxMemo


class DatabaseSearchTest(unittest.TestCase):
    def test_search_all_scope_returns_book_page_and_snippet(self) -> None:
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

            results = search(connection, "FTS5", scope="all")

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].title, "sqlite-guide")
            self.assertEqual(results[0].page_number, 2)
            self.assertIn("FTS5", results[0].snippet)
            connection.close()

    def test_search_title_scope_returns_one_result_per_book(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            connection = connect(db_path)
            initialize(connection)
            book_id = upsert_book(
                connection,
                path=Path("books/tech/python.pdf"),
                title="python dataclasses",
                size_bytes=123,
                modified_at=1.0,
            )
            replace_pages(
                connection,
                book_id=book_id,
                title="python dataclasses",
                pages=[
                    PageRecord(page_number=3, text="dataclasses make records simple"),
                    PageRecord(page_number=4, text="another page"),
                ],
            )

            results = search(connection, "python", scope="title")

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].snippet, "python dataclasses")
            self.assertEqual(results[0].page_number, 1)
            connection.close()

    def test_search_all_scope_includes_title_and_body_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            connection = connect(db_path)
            initialize(connection)
            book_id = upsert_book(
                connection,
                path=Path("books/tech/python.pdf"),
                title="python dataclasses",
                size_bytes=123,
                modified_at=1.0,
            )
            replace_pages(
                connection,
                book_id=book_id,
                title="python dataclasses",
                pages=[
                    PageRecord(page_number=3, text="dataclasses make records simple"),
                    PageRecord(page_number=4, text="python appears in body too"),
                ],
            )

            results = search(connection, "python", scope="all")

            self.assertGreaterEqual(len(results), 2)
            self.assertEqual(results[0].snippet, "python dataclasses")
            self.assertEqual(results[0].page_number, 1)
            self.assertTrue(any(result.page_number == 4 for result in results))
            connection.close()

    def test_search_body_scope_ignores_title_and_uses_page_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            connection = connect(db_path)
            initialize(connection)
            book_id = upsert_book(
                connection,
                path=Path("books/tech/body-only.pdf"),
                title="body-only",
                size_bytes=123,
                modified_at=1.0,
            )
            replace_pages(
                connection,
                book_id=book_id,
                title="body-only",
                pages=[PageRecord(page_number=7, text="bookscan style body query")],
            )

            results = search(connection, "style", scope="body")

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].page_number, 7)
            self.assertIn("style", results[0].snippet)
            connection.close()

    def test_search_memo_scope_returns_scrapbox_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            connection = connect(db_path)
            initialize(connection)
            replace_memos(
                connection,
                [
                    ScrapboxMemo(
                        title="メモ1",
                        body="検索対象のメモ本文",
                        scrapbox_url="https://scrapbox.io/custom-project/%E3%83%A1%E3%83%A21",
                        cover_url="https://example.com/cover.jpg",
                    )
                ],
            )

            results = search(connection, "検索対象", scope="memo")

            self.assertEqual(len(results), 1)
            self.assertIsNone(results[0].page_number)
            self.assertEqual(results[0].open_url, "https://scrapbox.io/custom-project/%E3%83%A1%E3%83%A21")
            self.assertEqual(results[0].cover_url, "https://example.com/cover.jpg")
            connection.close()

    def test_search_all_scope_includes_memo_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            connection = connect(db_path)
            initialize(connection)
            replace_memos(
                connection,
                [
                    ScrapboxMemo(
                        title="メモ1",
                        body="検索対象のメモ本文",
                        scrapbox_url="https://scrapbox.io/custom-project/%E3%83%A1%E3%83%A21",
                        cover_url="https://example.com/cover.jpg",
                    )
                ],
            )

            results = search(connection, "検索対象", scope="all")

            self.assertTrue(any(result.page_number is None for result in results))
            self.assertTrue(any(result.open_url == "https://scrapbox.io/custom-project/%E3%83%A1%E3%83%A21" for result in results))
            connection.close()


if __name__ == "__main__":
    unittest.main()
