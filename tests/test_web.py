import tempfile
import unittest
import zipfile
from io import BytesIO
from unittest.mock import patch
from pathlib import Path
import json

from pypdf import PdfReader, PdfWriter

from fastapi import HTTPException

from tsundokensaku.web import (
    api_activate_pack,
    api_create_pack,
    api_delete_pack,
    api_export_pack,
    api_get_pack,
    api_import_pack,
    api_list_packs,
    api_replace_pack_books,
    api_update_pack,
    build_scrapbox_page_url,
    build_search_scrapbox_body,
    export_markdown,
    export_pdf,
    group_pdf_results,
    highlight_query,
    import_pdfs_from_directory,
    pdf_outline,
    import_scrapbox_export_bytes,
    format_indexed_at,
    normalize_search_group,
    normalize_search_match,
    resolve_pdf_scrapbox_url,
    save_pdf_export_to_configured_dir,
    save_uploaded_pdf,
    search_pages,
    workspace_page,
)
from tsundokensaku.database import connect, initialize, upsert_book


class HighlightQueryTest(unittest.TestCase):
    def test_highlight_query_marks_matches(self) -> None:
        rendered = str(highlight_query("伝わるコードレビューには何が必要なんだろう？", "コードレビュー"))
        self.assertIn("<mark>コードレビュー</mark>", rendered)

    def test_highlight_query_escapes_html(self) -> None:
        rendered = str(highlight_query("<script>alert(1)</script> レビュー", "レビュー"))
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", rendered)
        self.assertIn("<mark>レビュー</mark>", rendered)

    def test_highlight_query_skips_excluded_terms(self) -> None:
        rendered = str(highlight_query("RubyとRailsの本", "Ruby -Rails"))
        self.assertIn("<mark>Ruby</mark>", rendered)
        self.assertNotIn("<mark>Rails</mark>", rendered)

    def test_highlight_query_exclusion_only_marks_nothing(self) -> None:
        rendered = str(highlight_query("-Linux と Linux の話", "-Linux"))
        self.assertNotIn("<mark>", rendered)

    def test_highlight_query_phrase_marks_without_quotes(self) -> None:
        rendered = str(highlight_query('彼は"The Cathedral and the Bazaar"を読んだ', '"The Cathedral and the Bazaar"'))
        self.assertIn("<mark>The Cathedral and the Bazaar</mark>", rendered)
        self.assertNotIn('<mark>"', rendered)
        self.assertNotIn('"</mark>', rendered)

    def test_group_pdf_results_combines_pages_by_title(self) -> None:
        grouped = group_pdf_results(
            [
                {
                    "kind": "pdf",
                    "title": "本A",
                    "path": "book-a.pdf",
                    "page_number": 2,
                    "snippet": "2ページ目",
                    "open_url": "/pdf/book-a.pdf#page=2",
                    "scrapbox_url": None,
                    "cover_url": None,
                },
                {
                    "kind": "pdf",
                    "title": "本A",
                    "path": "book-a.pdf",
                    "page_number": 5,
                    "snippet": "5ページ目",
                    "open_url": "/pdf/book-a.pdf#page=5",
                    "scrapbox_url": None,
                    "cover_url": None,
                },
                {
                    "kind": "memo",
                    "title": "メモ",
                    "path": "メモ",
                    "page_number": None,
                    "snippet": "メモ本文",
                    "open_url": "https://scrapbox.io/example/メモ",
                    "scrapbox_url": "https://scrapbox.io/example/メモ",
                    "cover_url": None,
                },
            ]
        )

        self.assertEqual(len(grouped), 2)
        book = grouped[0]
        self.assertEqual(book["title"], "本A")
        self.assertEqual(book["page_summary"], "p.2, p.5")
        self.assertEqual(book["page_numbers"], [2, 5])
        self.assertEqual(book["hit_count"], 2)
        self.assertEqual(book["snippet"], "2ページ目")
        self.assertEqual(grouped[1]["kind"], "memo")

    def test_import_pdfs_from_directory_copies_into_books_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "source"
            books_dir = root / "books"
            nested_dir = source_dir / "nested"
            nested_dir.mkdir(parents=True)
            pdf_a = source_dir / "a.pdf"
            pdf_b = nested_dir / "b.pdf"
            pdf_a.write_bytes(b"%PDF-1.4 a")
            pdf_b.write_bytes(b"%PDF-1.4 b")

            copied, skipped, total = import_pdfs_from_directory(source_dir, books_dir)

            self.assertEqual((copied, skipped, total), (2, 0, 2))
            self.assertTrue((books_dir / "a.pdf").exists())
            self.assertTrue((books_dir / "nested" / "b.pdf").exists())

    def test_save_uploaded_pdf_writes_unique_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            books_dir = Path(temp_dir) / "books"
            first = save_uploaded_pdf("sample.pdf", b"%PDF-1.4 first", books_dir)
            second = save_uploaded_pdf("sample.pdf", b"%PDF-1.4 second", books_dir)

            self.assertTrue(first.exists())
            self.assertTrue(second.exists())
            self.assertNotEqual(first, second)
            self.assertEqual(first.read_bytes(), b"%PDF-1.4 first")
            self.assertEqual(second.read_bytes(), b"%PDF-1.4 second")

    def test_format_indexed_at_renders_jst(self) -> None:
        self.assertEqual(format_indexed_at("2026-06-29T03:55:59.999358+00:00"), "2026/06/29 12:55")

    def test_import_scrapbox_export_bytes_syncs_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            cache_path = Path(temp_dir) / "scrapbox.json"
            payload = {
                "pages": [
                    {
                        "title": "メモ1",
                        "lines": [{"text": "検索対象のメモ本文"}],
                    },
                    {
                        "title": "Kindle Book",
                        "lines": [
                            {"text": "#Kindle #技術書"},
                            {"text": "https://read.amazon.co.jp/?asin=B012345678"},
                        ],
                    },
                ]
            }

            with patch("tsundokensaku.web.SCRAPBOX_EXPORT_CACHE", cache_path):
                imported, imported_kindle = import_scrapbox_export_bytes(json.dumps(payload).encode("utf-8"), db_path)

            self.assertEqual(imported, 2)
            self.assertEqual(imported_kindle, 1)
            self.assertTrue(cache_path.exists())

    def test_pdf_outline_returns_chapters_with_page_specs(self) -> None:
        import fitz

        with tempfile.TemporaryDirectory() as temp_dir:
            books_dir = Path(temp_dir) / "books"
            books_dir.mkdir()
            pdf_path = books_dir / "sample.pdf"

            doc = fitz.open()
            for _ in range(6):
                doc.new_page(width=72, height=72)
            doc.set_toc([[1, "第1章", 1], [1, "第2章", 4]])
            doc.save(str(pdf_path))
            doc.close()

            with patch("tsundokensaku.web.get_books_dir", return_value=books_dir):
                response = pdf_outline(pdf_path="sample.pdf")

            self.assertEqual(response.status_code, 200)
            payload = json.loads(response.body)
            self.assertEqual(payload["page_count"], 6)
            self.assertEqual(
                payload["chapters"],
                [
                    {"title": "第1章", "level": 1, "start_page": 1, "end_page": 4, "pages": "1-4"},
                    {"title": "第2章", "level": 1, "start_page": 4, "end_page": 6, "pages": "4-6"},
                ],
            )

    def test_pdf_outline_returns_empty_chapters_without_toc(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            books_dir = Path(temp_dir) / "books"
            books_dir.mkdir()
            pdf_path = books_dir / "sample.pdf"

            writer = PdfWriter()
            writer.add_blank_page(width=72, height=72)
            with pdf_path.open("wb") as handle:
                writer.write(handle)

            with patch("tsundokensaku.web.get_books_dir", return_value=books_dir):
                response = pdf_outline(pdf_path="sample.pdf")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(json.loads(response.body), {"page_count": 1, "chapters": []})

    def test_export_pdf_returns_selected_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books" / "tech"
            books_dir.mkdir(parents=True)
            pdf_path = books_dir / "sample.pdf"

            writer = PdfWriter()
            for _ in range(4):
                writer.add_blank_page(width=72, height=72)
            with pdf_path.open("wb") as handle:
                writer.write(handle)

            with patch("tsundokensaku.web.get_books_dir", return_value=books_dir):
                response = export_pdf(pdf_path="sample.pdf", pages="2-3")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["content-type"], "application/pdf")
            self.assertIn("attachment", response.headers["content-disposition"])

            with tempfile.NamedTemporaryFile(suffix=".pdf") as output:
                output.write(response.body)
                output.flush()
                reader = PdfReader(output.name)
                self.assertEqual(len(reader.pages), 2)

    def test_export_pdf_accepts_absolute_book_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books" / "tech"
            books_dir.mkdir(parents=True)
            pdf_path = books_dir / "sample.pdf"

            writer = PdfWriter()
            for _ in range(3):
                writer.add_blank_page(width=72, height=72)
            with pdf_path.open("wb") as handle:
                writer.write(handle)

            with patch("tsundokensaku.web.get_books_dir", return_value=books_dir):
                response = export_pdf(pdf_path=str(pdf_path), pages="1-2")

            self.assertEqual(response.status_code, 200)
            with tempfile.NamedTemporaryFile(suffix=".pdf") as output:
                output.write(response.body)
                output.flush()
                reader = PdfReader(output.name)
                self.assertEqual(len(reader.pages), 2)

    def test_workspace_page_renders(self) -> None:
        from unittest.mock import MagicMock

        request = MagicMock()
        request.url.path = "/workspace"
        response = workspace_page(request)

        self.assertEqual(response.status_code, 200)
        body = response.body.decode("utf-8")
        self.assertIn("資料棚", body)
        self.assertIn("ws-export-pdf", body)
        self.assertIn("ws-export-md", body)

    def test_search_pages_returns_matching_pages_with_snippets(self) -> None:
        from tsundokensaku.database import PageRecord, replace_pages

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"
            books_dir.mkdir()
            db_path = root / "index.db"
            pdf_path = books_dir / "sample.pdf"

            writer = PdfWriter()
            for _ in range(3):
                writer.add_blank_page(width=72, height=72)
            with pdf_path.open("wb") as handle:
                writer.write(handle)

            connection = connect(db_path)
            initialize(connection)
            book_id = upsert_book(
                connection,
                path=pdf_path,
                title="テスト本",
                size_bytes=pdf_path.stat().st_size,
                modified_at=pdf_path.stat().st_mtime,
            )
            replace_pages(
                connection,
                book_id=book_id,
                title="テスト本",
                pages=[
                    PageRecord(page_number=1, text="SQLiteの話はここには出てこない"),
                    PageRecord(page_number=2, text="全文検索エンジンとしてSQLite FTS5を使う"),
                    PageRecord(page_number=3, text="100%_LIKE記号のエスケープ確認"),
                ],
            )
            connection.commit()
            connection.close()

            with (
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
            ):
                response = search_pages(pdf_path="sample.pdf", q="FTS5")
                payload = json.loads(response.body)
                self.assertTrue(payload["indexed"])
                self.assertEqual([hit["page_number"] for hit in payload["pages"]], [2])
                self.assertIn("FTS5", payload["pages"][0]["snippet"])

                escaped = json.loads(search_pages(pdf_path="sample.pdf", q="%_LIKE").body)
                self.assertEqual([hit["page_number"] for hit in escaped["pages"]], [3])

                no_hit = json.loads(search_pages(pdf_path="sample.pdf", q="存在しない語").body)
                self.assertEqual(no_hit["pages"], [])

                empty_query = json.loads(search_pages(pdf_path="sample.pdf", q="  ").body)
                self.assertEqual(empty_query["pages"], [])

    def test_search_pages_reports_unindexed_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"
            books_dir.mkdir()
            pdf_path = books_dir / "sample.pdf"

            writer = PdfWriter()
            writer.add_blank_page(width=72, height=72)
            with pdf_path.open("wb") as handle:
                writer.write(handle)

            with (
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
                patch("tsundokensaku.web.get_db_path", return_value=root / "missing.db"),
            ):
                payload = json.loads(search_pages(pdf_path="sample.pdf", q="キーワード").body)

            self.assertFalse(payload["indexed"])
            self.assertEqual(payload["pages"], [])

    def test_export_markdown_uses_indexed_text(self) -> None:
        from tsundokensaku.database import PageRecord, replace_pages

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"
            books_dir.mkdir()
            db_path = root / "index.db"
            pdf_path = books_dir / "sample.pdf"

            writer = PdfWriter()
            for _ in range(3):
                writer.add_blank_page(width=72, height=72)
            with pdf_path.open("wb") as handle:
                writer.write(handle)

            connection = connect(db_path)
            initialize(connection)
            book_id = upsert_book(
                connection,
                path=pdf_path,
                title="テスト本",
                size_bytes=pdf_path.stat().st_size,
                modified_at=pdf_path.stat().st_mtime,
            )
            replace_pages(
                connection,
                book_id=book_id,
                title="テスト本",
                pages=[
                    PageRecord(page_number=2, text="2ページ目の本文"),
                    PageRecord(page_number=3, text="3ページ目の本文"),
                ],
            )
            connection.commit()
            connection.close()

            with (
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
            ):
                response = export_markdown(pdf_path="sample.pdf", pages="2-3")

            self.assertEqual(response.status_code, 200)
            self.assertIn("text/markdown", response.headers["content-type"])
            self.assertIn("attachment", response.headers["content-disposition"])
            body = response.body.decode("utf-8")
            self.assertIn("# テスト本（抜粋）", body)
            self.assertIn("- ページ: 2-3", body)
            self.assertIn("## p.2", body)
            self.assertIn("2ページ目の本文", body)
            self.assertIn("3ページ目の本文", body)

    def test_export_markdown_falls_back_to_extraction_without_db(self) -> None:
        import fitz

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"
            books_dir.mkdir()
            pdf_path = books_dir / "sample.pdf"

            doc = fitz.open()
            page = doc.new_page(width=400, height=400)
            page.insert_text((50, 100), "live extracted text")
            doc.save(str(pdf_path))
            doc.close()

            with (
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
                patch("tsundokensaku.web.get_db_path", return_value=root / "missing.db"),
            ):
                response = export_markdown(pdf_path="sample.pdf", pages="1")

            self.assertEqual(response.status_code, 200)
            body = response.body.decode("utf-8")
            self.assertIn("# sample（抜粋）", body)
            self.assertIn("live extracted text", body)

    def test_save_pdf_export_requires_configured_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            books_dir = Path(temp_dir) / "books"
            books_dir.mkdir()
            pdf_path = books_dir / "sample.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=72, height=72)
            with pdf_path.open("wb") as handle:
                writer.write(handle)

            with self.assertRaises(ValueError):
                save_pdf_export_to_configured_dir("sample.pdf", "1", books_dir=books_dir, save_dir=None)

    def test_save_pdf_export_errors_when_save_dir_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"
            books_dir.mkdir()
            pdf_path = books_dir / "sample.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=72, height=72)
            with pdf_path.open("wb") as handle:
                writer.write(handle)

            with self.assertRaises(FileNotFoundError):
                save_pdf_export_to_configured_dir("sample.pdf", "1", books_dir=books_dir, save_dir=root / "missing")

    def test_save_pdf_export_writes_unique_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"
            save_dir = root / "exports"
            books_dir.mkdir()
            save_dir.mkdir()
            pdf_path = books_dir / "日本語の本.pdf"
            writer = PdfWriter()
            for _ in range(3):
                writer.add_blank_page(width=72, height=72)
            with pdf_path.open("wb") as handle:
                writer.write(handle)
            existing = save_dir / "日本語の本_p1-2.pdf"
            existing.write_bytes(b"existing")

            saved = save_pdf_export_to_configured_dir("日本語の本.pdf", "1-2", books_dir=books_dir, save_dir=save_dir)

            self.assertEqual(saved.name, "日本語の本_p1-2_2.pdf")
            self.assertTrue(saved.exists())
            reader = PdfReader(str(saved))
            self.assertEqual(len(reader.pages), 2)

    def test_resolve_pdf_scrapbox_url_prefers_database_value(self) -> None:
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
                    title="sample",
                    size_bytes=pdf_path.stat().st_size,
                    modified_at=pdf_path.stat().st_mtime,
                    scrapbox_url="https://scrapbox.io/custom-project/sample",
                )
                connection.commit()
            finally:
                connection.close()

            self.assertEqual(
                resolve_pdf_scrapbox_url("sample.pdf", books_dir=books_dir, db_path=db_path),
                "https://scrapbox.io/custom-project/sample",
            )

    def test_build_scrapbox_page_url_includes_prefilled_body(self) -> None:
        with patch.dict("os.environ", {"SCRAPBOX_BASE_URL": "https://scrapbox.io/custom-project"}, clear=False):
            url = build_scrapbox_page_url("検索結果 SQLite 2026-06-29 23:15", "検索語: SQLite\n結果一覧")

        self.assertIsNotNone(url)
        self.assertIn("https://scrapbox.io/custom-project/", url)
        self.assertIn("body=", url)

    def test_normalize_search_match_defaults_to_all(self) -> None:
        # match 未指定（旧URL・ブックマーク）は AND 扱い
        self.assertEqual(normalize_search_match(None), "all")
        self.assertEqual(normalize_search_match([]), "all")
        self.assertEqual(normalize_search_match(["bogus"]), "all")

    def test_normalize_search_match_checkbox_on_sends_both_values(self) -> None:
        # hidden match=any + checked match=all の併送 → AND
        self.assertEqual(normalize_search_match(["any", "all"]), "all")
        self.assertEqual(normalize_search_match(["all", "any"]), "all")

    def test_normalize_search_match_checkbox_off_sends_any_only(self) -> None:
        self.assertEqual(normalize_search_match(["any"]), "any")
        self.assertEqual(normalize_search_match("any"), "any")

    def test_normalize_search_group_defaults_to_book(self) -> None:
        # 未指定（旧URL・ホームからの検索）は「同じ書籍をまとめる」がデフォルト
        self.assertEqual(normalize_search_group(None), "book")
        self.assertEqual(normalize_search_group([]), "book")
        self.assertEqual(normalize_search_group(["bogus"]), "book")

    def test_normalize_search_group_checkbox_states(self) -> None:
        # hidden group=none + checked group=book の併送 → book
        self.assertEqual(normalize_search_group(["none", "book"]), "book")
        self.assertEqual(normalize_search_group(["book", "none"]), "book")
        # unchecked は none のみ → 個別表示
        self.assertEqual(normalize_search_group(["none"]), "none")
        self.assertEqual(normalize_search_group("none"), "none")

    def test_build_search_scrapbox_body_includes_match_mode(self) -> None:
        _, body_all = build_search_scrapbox_body(
            query="SQLite FTS5",
            scope="all",
            sort="rank",
            group="none",
            match="all",
            results=[],
        )
        _, body_any = build_search_scrapbox_body(
            query="SQLite FTS5",
            scope="all",
            sort="rank",
            group="none",
            match="any",
            results=[],
        )

        self.assertIn("語の一致: すべての語を含む", body_all)
        self.assertIn("語の一致: いずれかの語を含む", body_any)

    def test_build_search_scrapbox_body_includes_results(self) -> None:
        page_title, body = build_search_scrapbox_body(
            query="SQLite",
            scope="all",
            sort="rank",
            group="none",
            results=[
                {
                    "title": "SQLite入門",
                    "kind": "pdf",
                    "snippet": "FTS5",
                    "path": "books/tech/sqlite.pdf",
                    "open_url": "https://example.com/pdf",
                    "scrapbox_url": "https://scrapbox.io/custom-project/SQLite%E5%85%A5%E9%96%80",
                }
            ],
        )

        self.assertIn("SQLite", page_title)
        self.assertIn("#つんどけんさく", body)
        self.assertIn("検索語: SQLite", body)
        self.assertIn("SQLite入門", body)
        self.assertIn("scrapbox: [SQLite入門]", body)
        self.assertNotIn("books/tech/sqlite.pdf", body)
        self.assertNotIn("open:", body)

    def test_build_search_scrapbox_body_keeps_all_results(self) -> None:
        results = [
            {
                "title": f"本{i}",
                "kind": "pdf",
                "snippet": f"snippet {i}",
                "path": f"books/tech/book-{i}.pdf",
                "scrapbox_url": f"https://scrapbox.io/custom-project/%E6%9C%AC{i}",
            }
            for i in range(1, 22)
        ]

        _, body = build_search_scrapbox_body(
            query="SQLite",
            scope="all",
            sort="rank",
            group="none",
            results=results,
        )

        self.assertIn("21. 本21", body)
        self.assertNotIn("他 ", body)


class PackApiTest(unittest.TestCase):
    def _payload(self, response) -> dict:
        return json.loads(response.body)

    def test_pack_api_new_db_starts_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            with patch("tsundokensaku.web.get_db_path", return_value=db_path):
                # 新規DB: 資料0件・アクティブなし。自動作成しない
                listing = self._payload(api_list_packs())
                self.assertEqual(listing["packs"], [])
                self.assertIsNone(listing["active_pack_id"])

    def test_pack_api_create_then_add_books_flow(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            with patch("tsundokensaku.web.get_db_path", return_value=db_path):
                # 資料0件からの作成フロー: 作成 → 自動アクティブ化 → 追加
                created = self._payload(api_create_pack({"name": "新しい資料"}))
                listing = self._payload(api_list_packs())
                self.assertEqual(listing["active_pack_id"], created["id"])

                books = {
                    "books/a.pdf": {"title": "本A", "pages": "1-3", "collapsed": False, "addedAt": "2026-01-01T00:00:00Z"},
                }
                replaced = self._payload(api_replace_pack_books(created["id"], {"books": books}))
                self.assertEqual(replaced["cart"]["books"], books)
                fetched = self._payload(api_get_pack(created["id"]))
                self.assertEqual(fetched["book_count"], 1)
                self.assertEqual(fetched["cart"]["books"], books)

    def test_pack_api_full_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            with patch("tsundokensaku.web.get_db_path", return_value=db_path):
                first = self._payload(api_create_pack({"name": "一つ目"}))
                created = self._payload(api_create_pack({"name": "調査資料"}))
                self.assertEqual(created["name"], "調査資料")
                listing = self._payload(api_list_packs())
                self.assertEqual(listing["active_pack_id"], created["id"])

                # 改名
                renamed = self._payload(api_update_pack(created["id"], {"name": "改名後"}))
                self.assertEqual(renamed["name"], "改名後")

                # activate で戻す
                self._payload(api_activate_pack(first["id"]))
                self.assertEqual(self._payload(api_list_packs())["active_pack_id"], first["id"])

                # 削除 → 残った資料へフォールバック
                deleted = self._payload(api_delete_pack(created["id"]))
                self.assertEqual(deleted["deleted"], created["id"])
                self.assertEqual(deleted["active_pack_id"], first["id"])

                # 最後の1つも削除できる → アクティブは None
                deleted = self._payload(api_delete_pack(first["id"]))
                self.assertIsNone(deleted["active_pack_id"])
                listing = self._payload(api_list_packs())
                self.assertEqual(listing["packs"], [])
                self.assertIsNone(listing["active_pack_id"])

    def test_pack_api_not_found_and_bad_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            with patch("tsundokensaku.web.get_db_path", return_value=db_path):
                with self.assertRaises(HTTPException) as ctx:
                    api_get_pack(9999)
                self.assertEqual(ctx.exception.status_code, 404)
                with self.assertRaises(HTTPException):
                    api_update_pack(9999, {"name": "x"})
                with self.assertRaises(HTTPException):
                    api_delete_pack(9999)
                with self.assertRaises(HTTPException):
                    api_activate_pack(9999)
                with self.assertRaises(HTTPException):
                    api_replace_pack_books(9999, {"books": {}})
                created = self._payload(api_create_pack({"name": "p"}))
                with self.assertRaises(HTTPException) as ctx:
                    api_replace_pack_books(created["id"], {"books": "not a dict"})
                self.assertEqual(ctx.exception.status_code, 400)

    def test_pack_api_import_cart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            with patch("tsundokensaku.web.get_db_path", return_value=db_path):
                cart = {
                    "version": 2,
                    "books": {
                        "books/a.pdf": {"title": "本A", "pages": "2-4", "collapsed": True, "addedAt": "2026-01-01T00:00:00Z"},
                    },
                }

                imported = self._payload(api_import_pack({"cart": cart}))

                self.assertEqual(imported["name"], "移行された資料")
                self.assertEqual(imported["cart"]["books"], cart["books"])
                self.assertEqual(self._payload(api_list_packs())["active_pack_id"], imported["id"])

                with self.assertRaises(HTTPException) as ctx:
                    api_import_pack({"cart": {"version": 2, "books": {}}})
                self.assertEqual(ctx.exception.status_code, 400)

    def test_pack_api_export_zip_contains_manifest_and_ordered_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"
            books_dir.mkdir(parents=True)

            def make_pdf(name: str, pages: int) -> None:
                writer = PdfWriter()
                for _ in range(pages):
                    writer.add_blank_page(width=72, height=72)
                with (books_dir / name).open("wb") as handle:
                    writer.write(handle)

            make_pdf("a.pdf", 5)
            make_pdf("b.pdf", 5)

            with patch("tsundokensaku.web.get_db_path", return_value=db_path), \
                    patch("tsundokensaku.web.get_books_dir", return_value=books_dir):
                created = self._payload(api_create_pack({"name": "調査資料"}))
                books = {
                    "a.pdf": {"title": "本A", "pages": "1-2", "collapsed": False, "addedAt": "2026-01-01T00:00:00Z"},
                    "b.pdf": {"title": "本B", "pages": "3", "collapsed": False, "addedAt": "2026-01-01T00:00:01Z"},
                }
                self._payload(api_replace_pack_books(created["id"], {"books": books}))

                response = api_export_pack(created["id"], format="pdf")

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.media_type, "application/zip")
                self.assertIn("attachment", response.headers["content-disposition"])

                with zipfile.ZipFile(BytesIO(response.body)) as archive:
                    names = archive.namelist()
                    self.assertEqual(names[0], "manifest.md")
                    # books 辞書の列挙順（＝資料内の並び順）が連番ファイル名に反映される
                    self.assertEqual(names[1], "01_本A_p1-2.pdf")
                    self.assertEqual(names[2], "02_本B_p3.pdf")

                    manifest = archive.read("manifest.md").decode("utf-8")
                    self.assertIn("調査資料", manifest)
                    self.assertIn("本A", manifest)
                    self.assertIn("本B", manifest)

                    reader = PdfReader(BytesIO(archive.read(names[1])))
                    self.assertEqual(len(reader.pages), 2)

    def test_pack_api_export_zip_supports_markdown_format(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"
            books_dir.mkdir(parents=True)

            writer = PdfWriter()
            for _ in range(3):
                writer.add_blank_page(width=72, height=72)
            with (books_dir / "a.pdf").open("wb") as handle:
                writer.write(handle)

            with patch("tsundokensaku.web.get_db_path", return_value=db_path), \
                    patch("tsundokensaku.web.get_books_dir", return_value=books_dir):
                created = self._payload(api_create_pack({"name": "資料"}))
                books = {
                    "a.pdf": {"title": "本A", "pages": "1", "collapsed": False, "addedAt": "2026-01-01T00:00:00Z"},
                }
                self._payload(api_replace_pack_books(created["id"], {"books": books}))

                response = api_export_pack(created["id"], format="md")

                self.assertEqual(response.media_type, "application/zip")
                with zipfile.ZipFile(BytesIO(response.body)) as archive:
                    names = archive.namelist()
                    # ファイル名は pack_items.title（追加時点のスナップショット）ベース
                    self.assertEqual(names, ["manifest.md", "01_本A_p1.md"])
                    # 本文の見出しは render_markdown_export 既存仕様どおり
                    # books テーブル未登録なら元PDFファイル名にフォールバックする
                    content = archive.read("01_本A_p1.md").decode("utf-8")
                    self.assertIn("## p.1", content)
                    self.assertIn("本A", archive.read("manifest.md").decode("utf-8"))

    def test_pack_api_export_zip_rejects_empty_pack_and_bad_format(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            with patch("tsundokensaku.web.get_db_path", return_value=db_path):
                created = self._payload(api_create_pack({"name": "空の資料"}))

                with self.assertRaises(HTTPException) as ctx:
                    api_export_pack(created["id"], format="pdf")
                self.assertEqual(ctx.exception.status_code, 400)

                with self.assertRaises(HTTPException) as ctx:
                    api_export_pack(created["id"], format="epub")
                self.assertEqual(ctx.exception.status_code, 400)

                with self.assertRaises(HTTPException) as ctx:
                    api_export_pack(9999, format="pdf")
                self.assertEqual(ctx.exception.status_code, 404)

    def test_pack_api_export_zip_requires_pages_on_every_item(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"
            books_dir.mkdir(parents=True)
            writer = PdfWriter()
            writer.add_blank_page(width=72, height=72)
            with (books_dir / "a.pdf").open("wb") as handle:
                writer.write(handle)

            with patch("tsundokensaku.web.get_db_path", return_value=db_path), \
                    patch("tsundokensaku.web.get_books_dir", return_value=books_dir):
                created = self._payload(api_create_pack({"name": "資料"}))
                books = {
                    "a.pdf": {"title": "本A", "pages": "", "collapsed": False, "addedAt": "2026-01-01T00:00:00Z"},
                }
                self._payload(api_replace_pack_books(created["id"], {"books": books}))

                with self.assertRaises(HTTPException) as ctx:
                    api_export_pack(created["id"], format="pdf")
                self.assertEqual(ctx.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
