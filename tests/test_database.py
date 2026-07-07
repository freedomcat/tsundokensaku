import tempfile
import unittest
import sqlite3
from pathlib import Path

from tsundokensaku.database import (
    PageRecord,
    BookNoteRecord,
    replace_memos,
    connect,
    initialize,
    replace_pages,
    replace_book_notes,
    refresh_pdf_titles,
    sync_kindle_books,
    search,
    upsert_book,
)
from tsundokensaku.metadata import BookMetadata, ScrapboxMemo


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

    def test_search_body_scope_finds_japanese_compound_words(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            connection = connect(db_path)
            initialize(connection)
            book_id = upsert_book(
                connection,
                path=Path("books/tech/japanese.pdf"),
                title="japanese",
                size_bytes=123,
                modified_at=1.0,
            )
            replace_pages(
                connection,
                book_id=book_id,
                title="japanese",
                pages=[PageRecord(page_number=2, text="伝わるコードレビューには何が必要なんだろう？")],
            )

            results = search(connection, "コードレビュー", scope="body")

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].page_number, 2)
            self.assertIn("コードレビュー", results[0].snippet.replace(" ", ""))
            connection.close()

    def test_search_body_scope_matches_server_long_vowel_variants(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            connection = connect(db_path)
            initialize(connection)
            short_book_id = upsert_book(
                connection,
                path=Path("books/tech/server-short.pdf"),
                title="server-short",
                size_bytes=123,
                modified_at=1.0,
            )
            long_book_id = upsert_book(
                connection,
                path=Path("books/tech/server-long.pdf"),
                title="server-long",
                size_bytes=123,
                modified_at=1.0,
            )
            replace_pages(
                connection,
                book_id=short_book_id,
                title="server-short",
                pages=[PageRecord(page_number=1, text="サーバ構成を確認する")],
            )
            replace_pages(
                connection,
                book_id=long_book_id,
                title="server-long",
                pages=[PageRecord(page_number=1, text="サーバー構成を確認する")],
            )

            results = search(connection, "サーバー", scope="body", limit=10)

            self.assertEqual(len(results), 2)
            self.assertEqual({result.title for result in results}, {"server-short", "server-long"})
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

    def test_search_all_scope_includes_book_note_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            connection = connect(db_path)
            initialize(connection)
            book_id = upsert_book(
                connection,
                path=None,
                source_type="kindle",
                external_id="kindle-123",
                title="Kindle Book",
            )
            replace_book_notes(
                connection,
                book_id=book_id,
                notes=[
                    BookNoteRecord(
                        title="読書メモ",
                        body="検索対象のノート本文",
                        scrapbox_url="https://scrapbox.io/custom-project/読書メモ",
                        cover_url="https://example.com/cover.jpg",
                    )
                ],
            )

            results = search(connection, "検索対象", scope="all")

            self.assertTrue(any(result.kind == "note" for result in results))
            self.assertTrue(any(result.open_url == "https://scrapbox.io/custom-project/読書メモ" for result in results))
            connection.close()

    def test_search_title_scope_returns_kindle_books(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            connection = connect(db_path)
            initialize(connection)
            upsert_book(
                connection,
                path=None,
                source_type="kindle",
                external_id="kindle-999",
                title="Kindle Search Book",
            )

            results = search(connection, "Kindle", scope="title")

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].kind, "kindle")
            self.assertIsNone(results[0].page_number)
            self.assertEqual(results[0].path, "kindle-999")
            connection.close()

    def test_sync_kindle_books_imports_scrapbox_kindle_links(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            export_json = root / "shino-books_20260628_000000.json"
            export_json.write_text(
                """
                {
                  "pages": [
                    {
                      "title": "JavaScript: The Definitive Guide",
                      "lines": [
                        {"text": "[https://m.media-amazon.com/images/I/cover.jpg https://www.amazon.co.jp/dp/B004XQX4K0]"},
                        {"text": "[https://read.amazon.co.jp/?asin=B004XQX4K0 Kindleで開く]"},
                        {"text": "#Kindle #技術書"}
                      ]
                    }
                  ]
                }
                """,
                encoding="utf-8",
            )
            db_path = root / "index.db"
            connection = connect(db_path)
            initialize(connection)

            imported = sync_kindle_books(connection, export_json, project_url="https://scrapbox.io/custom-project")
            results = search(connection, "Definitive", scope="title")

            self.assertEqual(imported, 1)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].kind, "kindle")
            self.assertEqual(results[0].path, "B004XQX4K0")
            self.assertEqual(results[0].open_url, "https://read.amazon.co.jp/?asin=B004XQX4K0")
            self.assertEqual(
                results[0].scrapbox_url,
                "https://scrapbox.io/custom-project/JavaScript%3A%20The%20Definitive%20Guide",
            )
            self.assertEqual(results[0].cover_url, "https://m.media-amazon.com/images/I/cover.jpg")
            connection.close()

    def test_initialize_migrates_legacy_books_table(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            connection = sqlite3.connect(db_path)
            connection.row_factory = sqlite3.Row
            connection.execute(
                """
                CREATE TABLE books (
                    id INTEGER PRIMARY KEY,
                    path TEXT NOT NULL UNIQUE,
                    filename TEXT,
                    title TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    modified_at REAL NOT NULL,
                    indexed_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT INTO books(id, path, filename, title, size_bytes, modified_at, indexed_at) VALUES (1, ?, ?, ?, ?, ?, ?)",
                ("books/tech/legacy.pdf", "legacy.pdf", "legacy", 10, 1.0, "2026-06-28T00:00:00+00:00"),
            )
            connection.commit()

            initialize(connection)

            columns = {row[1] for row in connection.execute("PRAGMA table_info(books)").fetchall()}
            self.assertIn("source_type", columns)
            self.assertIn("filename", columns)
            row = connection.execute("SELECT source_type, external_id, filename FROM books WHERE id = 1").fetchone()
            self.assertEqual(row["source_type"], "pdf")
            self.assertIsNone(row["external_id"])
            self.assertEqual(row["filename"], "legacy.pdf")
            connection.close()

    def test_initialize_creates_search_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            connection = connect(db_path)
            initialize(connection)

            tables = {
                str(row["name"])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
                ).fetchall()
            }
            indexes = {
                str(row["name"])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index'"
                ).fetchall()
            }

            self.assertIn("books_fts", tables)
            self.assertIn("idx_books_title_path", indexes)
            self.assertIn("idx_memos_title", indexes)
            self.assertIn("idx_book_notes_book_title", indexes)
            self.assertIn("pages_trigram", tables)
            connection.close()

    def test_refresh_pdf_titles_updates_display_title_and_fts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            connection = connect(db_path)
            initialize(connection)
            book_id = upsert_book(
                connection,
                path=Path("books/tech/legacy.pdf"),
                filename="legacy.pdf",
                title="legacy",
                size_bytes=123,
                modified_at=1.0,
            )
            replace_pages(
                connection,
                book_id=book_id,
                title="legacy",
                pages=[PageRecord(page_number=1, text="Actual Title appears here")],
            )
            page_before = connection.execute(
                "SELECT id, page_number, text FROM pages WHERE book_id = ?",
                (book_id,),
            ).fetchone()

            updated = refresh_pdf_titles(connection, {"legacy": BookMetadata(title="Actual Title")})
            results = search(connection, "Actual Title", scope="title")
            page_after = connection.execute(
                "SELECT id, page_number, text FROM pages WHERE book_id = ?",
                (book_id,),
            ).fetchone()
            page_fts_title = connection.execute(
                "SELECT title FROM pages_fts WHERE book_id = ?",
                (book_id,),
            ).fetchone()

            self.assertEqual(updated, 1)
            self.assertEqual(dict(page_after), dict(page_before))
            self.assertEqual(page_fts_title["title"], "actual title")
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].title, "Actual Title")
            self.assertEqual(results[0].page_number, 1)
            connection.close()

    def test_search_body_uses_trigram_for_partial_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            connection = connect(db_path)
            initialize(connection)
            book_id = upsert_book(
                connection,
                path=Path("books/tech/linux.pdf"),
                title="linux guide",
                size_bytes=123,
                modified_at=1.0,
            )
            replace_pages(
                connection,
                book_id=book_id,
                title="linux guide",
                pages=[PageRecord(page_number=1, text="The Linux kernel lives inside the OS.")],
            )

            results = search(connection, "ernel liv", scope="body")

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].page_number, 1)
            self.assertIn("kernel", results[0].snippet)
            connection.close()


class SearchMatchModeTest(unittest.TestCase):
    def _make_connection(self, temp_dir: str):
        db_path = Path(temp_dir) / "index.db"
        connection = connect(db_path)
        initialize(connection)
        return connection

    def _add_book(self, connection, *, path: str, title: str, pages: list[PageRecord]) -> int:
        book_id = upsert_book(
            connection,
            path=Path(path),
            title=title,
            size_bytes=123,
            modified_at=1.0,
        )
        replace_pages(connection, book_id=book_id, title=title, pages=pages)
        return book_id

    def _add_mixed_pages(self, connection) -> None:
        self._add_book(
            connection,
            path="books/tech/sqlite-guide.pdf",
            title="database guide",
            pages=[
                PageRecord(page_number=1, text="SQLite stores data in local files."),
                PageRecord(page_number=2, text="FTS5 provides full text search."),
                PageRecord(page_number=3, text="SQLite works well with FTS5 indexes."),
            ],
        )

    def test_multi_term_default_requires_every_term(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = self._make_connection(temp_dir)
            self._add_mixed_pages(connection)

            results = search(connection, "sqlite fts5", scope="body")

            self.assertEqual([result.page_number for result in results], [3])
            connection.close()

    def test_multi_term_any_returns_pages_with_either_term(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = self._make_connection(temp_dir)
            self._add_mixed_pages(connection)

            results = search(connection, "sqlite fts5", scope="body", match="any")

            self.assertEqual(sorted(result.page_number for result in results), [1, 2, 3])
            self.assertEqual(results[0].page_number, 3)
            connection.close()

    def test_single_japanese_word_stays_and_even_in_any_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = self._make_connection(temp_dir)
            self._add_book(
                connection,
                path="books/tech/ml.pdf",
                title="ml guide",
                pages=[
                    PageRecord(page_number=1, text="機械学習の基礎を学ぶ。"),
                    PageRecord(page_number=2, text="機械の設計について述べる。"),
                ],
            )

            results = search(connection, "機械学習", scope="body", match="any")

            self.assertEqual([result.page_number for result in results], [1])
            connection.close()

    def test_trigram_any_matches_partial_terms(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = self._make_connection(temp_dir)
            self._add_book(
                connection,
                path="books/tech/linux.pdf",
                title="linux guide",
                pages=[PageRecord(page_number=1, text="The Linux kernel lives inside the OS.")],
            )

            any_results = search(connection, "ernel qqzzxx", scope="body", match="any")
            all_results = search(connection, "ernel qqzzxx", scope="body", match="all")

            self.assertEqual([result.page_number for result in any_results], [1])
            self.assertEqual(all_results, [])
            connection.close()

    def test_title_scope_any_returns_books_with_either_term(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = self._make_connection(temp_dir)
            self._add_book(
                connection,
                path="books/tech/python.pdf",
                title="python dataclasses",
                pages=[PageRecord(page_number=1, text="python page")],
            )
            self._add_book(
                connection,
                path="books/tech/rust.pdf",
                title="rust guide",
                pages=[PageRecord(page_number=1, text="rust page")],
            )

            any_results = search(connection, "python rust", scope="title", match="any")
            all_results = search(connection, "python rust", scope="title", match="all")

            self.assertEqual(sorted(result.title for result in any_results), ["python dataclasses", "rust guide"])
            self.assertEqual(all_results, [])
            connection.close()

    def test_invalid_match_falls_back_to_all(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = self._make_connection(temp_dir)
            self._add_mixed_pages(connection)

            results = search(connection, "sqlite fts5", scope="body", match="bogus")

            self.assertEqual([result.page_number for result in results], [3])
            connection.close()


if __name__ == "__main__":
    unittest.main()
