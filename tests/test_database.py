import tempfile
import unittest
import sqlite3
from pathlib import Path

from tsundokensaku.database import (
    FALLBACK_PACK_NAME,
    PageRecord,
    BookNoteRecord,
    QueryTerm,
    create_pack,
    delete_pack,
    get_active_pack_id,
    get_pack,
    get_pack_items,
    import_cart_as_pack,
    list_packs,
    pack_items_as_cart,
    parse_query,
    replace_pack_items,
    resolve_active_pack_id,
    set_active_pack,
    update_pack,
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


class ParseQueryTest(unittest.TestCase):
    def test_parse_query_extracts_phrases_and_exclusions(self) -> None:
        terms = parse_query('Ruby -Rails "Martin Fowler" -"Ruby on Rails"')

        self.assertEqual(
            terms,
            [
                QueryTerm(text="Ruby"),
                QueryTerm(text="Rails", exclude=True),
                QueryTerm(text="Martin Fowler", phrase=True),
                QueryTerm(text="Ruby on Rails", phrase=True, exclude=True),
            ],
        )

    def test_parse_query_skips_empty_parts(self) -> None:
        self.assertEqual(parse_query('- "" -""'), [])
        self.assertEqual(parse_query(""), [])


class SearchSyntaxTest(unittest.TestCase):
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

    def test_phrase_search_requires_adjacent_words(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = self._make_connection(temp_dir)
            self._add_book(
                connection,
                path="books/tech/refactoring.pdf",
                title="refactoring",
                pages=[
                    PageRecord(page_number=1, text="Martin Fowler wrote the refactoring book."),
                    PageRecord(page_number=2, text="Martin admires the Fowler patterns catalog."),
                ],
            )

            phrase_results = search(connection, '"martin fowler"', scope="body")
            loose_results = search(connection, "martin fowler", scope="body")

            self.assertEqual([result.page_number for result in phrase_results], [1])
            self.assertEqual(sorted(result.page_number for result in loose_results), [1, 2])
            connection.close()

    def test_japanese_phrase_search_requires_adjacency(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = self._make_connection(temp_dir)
            self._add_book(
                connection,
                path="books/tech/dist.pdf",
                title="dist guide",
                pages=[
                    PageRecord(page_number=1, text="分散システムの設計原則を学ぶ。"),
                    PageRecord(page_number=2, text="分散処理とシステム運用を扱う。"),
                ],
            )

            phrase_results = search(connection, '"分散システム"', scope="body")
            loose_results = search(connection, "分散システム", scope="body")

            self.assertEqual([result.page_number for result in phrase_results], [1])
            self.assertEqual(sorted(result.page_number for result in loose_results), [1, 2])
            connection.close()

    def test_exclusion_removes_pages_with_term(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = self._make_connection(temp_dir)
            self._add_book(
                connection,
                path="books/tech/ruby.pdf",
                title="ruby books",
                pages=[
                    PageRecord(page_number=1, text="ruby sinatra micro framework guide"),
                    PageRecord(page_number=2, text="ruby on rails web framework guide"),
                ],
            )

            excluded_results = search(connection, "ruby -rails", scope="body")
            plain_results = search(connection, "ruby", scope="body")

            self.assertEqual([result.page_number for result in excluded_results], [1])
            self.assertEqual(sorted(result.page_number for result in plain_results), [1, 2])
            connection.close()

    def test_phrase_exclusion_differs_from_word_exclusion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = self._make_connection(temp_dir)
            self._add_book(
                connection,
                path="books/tech/ruby2.pdf",
                title="ruby tips",
                pages=[
                    PageRecord(page_number=1, text="ruby gems and rails tips collected"),
                    PageRecord(page_number=2, text="ruby on rails tutorial for beginners"),
                ],
            )

            phrase_excluded = search(connection, 'ruby -"ruby on rails"', scope="body")
            word_excluded = search(connection, "ruby -rails", scope="body")

            self.assertEqual([result.page_number for result in phrase_excluded], [1])
            self.assertEqual(word_excluded, [])
            connection.close()

    def test_exclusion_only_query_returns_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = self._make_connection(temp_dir)
            self._add_book(
                connection,
                path="books/tech/any.pdf",
                title="any book",
                pages=[PageRecord(page_number=1, text="some page text here")],
            )

            self.assertEqual(search(connection, "-rails", scope="all"), [])
            self.assertEqual(search(connection, '-"ruby on rails"', scope="all"), [])
            connection.close()

    def test_trigram_partial_match_respects_exclusion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = self._make_connection(temp_dir)
            self._add_book(
                connection,
                path="books/tech/linux2.pdf",
                title="linux notes",
                pages=[
                    PageRecord(page_number=1, text="the kernel lives inside the OS"),
                    PageRecord(page_number=2, text="the kernel rails against limits"),
                ],
            )

            results = search(connection, "ernel -rails", scope="body")

            self.assertEqual([result.page_number for result in results], [1])
            connection.close()

    def test_title_scope_supports_exclusion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = self._make_connection(temp_dir)
            self._add_book(
                connection,
                path="books/tech/py1.pdf",
                title="python dataclasses",
                pages=[PageRecord(page_number=1, text="dataclass page")],
            )
            self._add_book(
                connection,
                path="books/tech/py2.pdf",
                title="python web guide",
                pages=[PageRecord(page_number=1, text="web page")],
            )

            results = search(connection, "python -web", scope="title")

            self.assertEqual([result.title for result in results], ["python dataclasses"])
            connection.close()


class PackTest(unittest.TestCase):
    def _make_connection(self, temp_dir: str):
        db_path = Path(temp_dir) / "index.db"
        connection = connect(db_path)
        initialize(connection)
        return connection

    def test_create_get_list_update_delete_pack(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = self._make_connection(temp_dir)

            pack_id = create_pack(connection, name="分散システム調査")
            pack = get_pack(connection, pack_id)
            self.assertIsNotNone(pack)
            self.assertEqual(pack.name, "分散システム調査")
            self.assertEqual(pack.book_count, 0)

            self.assertTrue(update_pack(connection, pack_id, name="合意アルゴリズム調査"))
            self.assertEqual(get_pack(connection, pack_id).name, "合意アルゴリズム調査")

            other_id = create_pack(connection, name="別パック")
            names = {pack.name for pack in list_packs(connection)}
            self.assertEqual(names, {"合意アルゴリズム調査", "別パック"})

            self.assertTrue(delete_pack(connection, other_id))
            self.assertIsNone(get_pack(connection, other_id))
            self.assertFalse(delete_pack(connection, other_id))
            self.assertFalse(update_pack(connection, other_id, name="消えたはず"))
            connection.close()

    def test_blank_pack_name_falls_back_to_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = self._make_connection(temp_dir)
            pack_id = create_pack(connection, name="   ")
            self.assertEqual(get_pack(connection, pack_id).name, FALLBACK_PACK_NAME)
            connection.close()

    def test_new_db_starts_with_no_packs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = self._make_connection(temp_dir)

            # 新規DBでは資料0件・アクティブなし。自動作成しない
            self.assertEqual(list_packs(connection), [])
            self.assertIsNone(resolve_active_pack_id(connection))
            self.assertEqual(list_packs(connection), [])
            connection.close()

    def test_active_pack_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = self._make_connection(temp_dir)

            first_id = create_pack(connection, name="一つ目")
            set_active_pack(connection, first_id)
            self.assertEqual(resolve_active_pack_id(connection), first_id)

            second_id = create_pack(connection, name="二つ目")
            self.assertTrue(set_active_pack(connection, second_id))
            self.assertEqual(get_active_pack_id(connection), second_id)
            self.assertFalse(set_active_pack(connection, 9999))

            # アクティブを削除 → 残っている資料へフォールバック
            delete_pack(connection, second_id)
            self.assertIsNone(get_active_pack_id(connection))
            self.assertEqual(resolve_active_pack_id(connection), first_id)

            # 全削除 → None に戻る
            delete_pack(connection, first_id)
            self.assertIsNone(resolve_active_pack_id(connection))
            connection.close()

    def test_existing_db_keeps_packs_after_reinitialize(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = self._make_connection(temp_dir)
            pack_id = create_pack(connection, name="既存の資料")
            set_active_pack(connection, pack_id)
            replace_pack_items(connection, pack_id, {"books/a.pdf": {"title": "本A", "pages": "1"}})

            # 既存ユーザーのDB相当: initialize を再実行しても資料・アクティブが維持される
            initialize(connection)

            self.assertEqual(resolve_active_pack_id(connection), pack_id)
            self.assertEqual(get_pack(connection, pack_id).name, "既存の資料")
            self.assertEqual(get_pack(connection, pack_id).book_count, 1)
            connection.close()

    def test_replace_pack_items_preserves_added_at_and_follows_payload_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = self._make_connection(temp_dir)
            pack_id = create_pack(connection, name="p")

            replace_pack_items(
                connection,
                pack_id,
                {
                    "books/a.pdf": {"title": "本A", "pages": "1-3", "collapsed": False, "addedAt": "2026-01-01T00:00:00Z"},
                    "books/b.pdf": {"title": "本B", "pages": "10", "collapsed": True},
                },
            )
            items = {item.pdf_path: item for item in get_pack_items(connection, pack_id)}
            self.assertEqual(set(items), {"books/a.pdf", "books/b.pdf"})
            self.assertEqual(items["books/a.pdf"].added_at, "2026-01-01T00:00:00Z")
            first_added_at = items["books/a.pdf"].added_at

            replace_pack_items(
                connection,
                pack_id,
                {
                    "books/a.pdf": {"title": "本A", "pages": "1-5", "collapsed": True},
                    "books/c.pdf": {"title": "本C", "pages": "7"},
                },
            )
            items = {item.pdf_path: item for item in get_pack_items(connection, pack_id)}
            self.assertEqual(set(items), {"books/a.pdf", "books/c.pdf"})
            self.assertEqual(items["books/a.pdf"].pages, "1-5")
            self.assertTrue(items["books/a.pdf"].collapsed)
            self.assertEqual(items["books/a.pdf"].added_at, first_added_at)

            self.assertEqual(get_pack(connection, pack_id).book_count, 2)
            self.assertFalse(replace_pack_items(connection, 9999, {}))
            connection.close()

    def test_replace_pack_items_saves_reordered_books(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = self._make_connection(temp_dir)
            pack_id = create_pack(connection, name="p")
            replace_pack_items(
                connection,
                pack_id,
                {
                    "books/a.pdf": {"title": "本A", "pages": "1", "addedAt": "2026-01-01T00:00:00Z"},
                    "books/b.pdf": {"title": "本B", "pages": "2"},
                    "books/c.pdf": {"title": "本C", "pages": "3"},
                },
            )
            self.assertEqual(
                [item.pdf_path for item in get_pack_items(connection, pack_id)],
                ["books/a.pdf", "books/b.pdf", "books/c.pdf"],
            )

            # 並び替え後の順序で送信 → position が振り直されて順序が保存される
            replace_pack_items(
                connection,
                pack_id,
                {
                    "books/c.pdf": {"title": "本C", "pages": "3"},
                    "books/a.pdf": {"title": "本A", "pages": "1"},
                    "books/b.pdf": {"title": "本B", "pages": "2"},
                },
            )
            items = get_pack_items(connection, pack_id)
            self.assertEqual(
                [item.pdf_path for item in items],
                ["books/c.pdf", "books/a.pdf", "books/b.pdf"],
            )
            # 並び替えでも added_at は保持される
            by_path = {item.pdf_path: item for item in items}
            self.assertEqual(by_path["books/a.pdf"].added_at, "2026-01-01T00:00:00Z")

            # cart 形式でも同じ順序で返る
            cart = pack_items_as_cart(connection, pack_id)
            self.assertEqual(list(cart["books"]), ["books/c.pdf", "books/a.pdf", "books/b.pdf"])
            connection.close()

    def test_pack_items_as_cart_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = self._make_connection(temp_dir)
            pack_id = create_pack(connection, name="p")
            books = {
                "books/a.pdf": {"title": "本A", "pages": "1-3,7", "collapsed": False, "addedAt": "2026-01-01T00:00:00Z"},
            }
            replace_pack_items(connection, pack_id, books)

            cart = pack_items_as_cart(connection, pack_id)

            self.assertEqual(cart["version"], 2)
            self.assertEqual(cart["books"], books)
            connection.close()

    def test_import_cart_as_pack(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = self._make_connection(temp_dir)
            cart = {
                "version": 2,
                "books": {
                    "books/a.pdf": {"title": "本A", "pages": "2-4", "collapsed": True, "addedAt": "2026-01-01T00:00:00Z"},
                },
            }

            pack_id = import_cart_as_pack(connection, cart, name="移行された資料")

            self.assertIsNotNone(pack_id)
            self.assertEqual(get_pack(connection, pack_id).name, "移行された資料")
            self.assertEqual(pack_items_as_cart(connection, pack_id)["books"], cart["books"])

            self.assertIsNone(import_cart_as_pack(connection, {"version": 2, "books": {}}, name="空"))
            self.assertIsNone(import_cart_as_pack(connection, {"version": 1}, name="旧"))
            self.assertIsNone(import_cart_as_pack(connection, "not a dict", name="壊"))
            connection.close()

    def test_initialize_is_idempotent_for_pack_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = self._make_connection(temp_dir)
            pack_id = create_pack(connection, name="残る")
            initialize(connection)
            self.assertEqual(get_pack(connection, pack_id).name, "残る")
            connection.close()

    def test_initialize_drops_legacy_pack_items_pdf_path_unique_constraint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            connection = connect(db_path)
            connection.executescript(
                """
                CREATE TABLE packs (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    archived_at TEXT
                );
                CREATE TABLE pack_items (
                    id INTEGER PRIMARY KEY,
                    pack_id INTEGER NOT NULL REFERENCES packs(id) ON DELETE CASCADE,
                    pdf_path TEXT NOT NULL,
                    title TEXT NOT NULL,
                    pages TEXT NOT NULL DEFAULT '',
                    collapsed INTEGER NOT NULL DEFAULT 0,
                    position INTEGER NOT NULL DEFAULT 0,
                    added_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(pack_id, pdf_path)
                );
                CREATE TABLE app_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT INTO packs(id, name, note, created_at, updated_at) VALUES (1, 'p', '', 'a', 'a');
                INSERT INTO pack_items(id, pack_id, pdf_path, title, pages, collapsed, position, added_at, updated_at)
                VALUES (10, 1, 'books/a.pdf', '本A', '1', 0, 7, 'old', 'old');
                """
            )
            connection.commit()

            initialize(connection)
            connection.execute(
                """
                INSERT INTO pack_items(pack_id, pdf_path, title, pages, collapsed, position, added_at, updated_at)
                VALUES (1, 'books/a.pdf', '本A', '2', 0, 8, 'new', 'new')
                """
            )
            connection.commit()

            items = get_pack_items(connection, 1)
            self.assertEqual([item.id for item in items], [10, 11])
            self.assertEqual([item.position for item in items], [7, 8])
            self.assertEqual([item.pages for item in items], ["1", "2"])
            connection.close()


if __name__ == "__main__":
    unittest.main()
