import asyncio
import os
import tempfile
import unittest
import zipfile
from io import BytesIO
from unittest.mock import patch
from pathlib import Path
from urllib.parse import quote, unquote
import json

from pypdf import PdfReader, PdfWriter

from fastapi import HTTPException
from fastapi.testclient import TestClient

from tsundokensaku.web import (
    api_activate_pack,
    api_create_pack,
    api_delete_pack,
    api_export_pack,
    api_get_pack,
    api_import_pack,
    api_list_pack_stats,
    api_list_packs,
    api_preview_pack_export,
    api_replace_pack_books,
    api_replace_pack_items,
    api_update_pack,
    build_export_preview_payload,
    build_export_preview_payload_for_profile,
    build_export_preview_warnings,
    build_scrapbox_page_url,
    build_search_scrapbox_body,
    _now_jst,
    export_markdown,
    export_pdf,
    group_pdf_results,
    highlight_query,
    import_pdf_directory,
    import_pdfs_from_directory,
    import_scrapbox_json,
    pdf_outline,
    import_scrapbox_export_bytes,
    format_indexed_at,
    is_demo_mode,
    normalize_search_group,
    normalize_search_match,
    pdf_thumbnails,
    resolve_pdf_scrapbox_url,
    save_pdf_export_to_configured_dir,
    save_uploaded_pdf,
    search_pages,
    update_pdf_export_save_dir,
    upload_pdf,
    upload_scrapbox_json,
    workspace_page,
)
from tsundokensaku.web import app as tsundokensaku_app
from tsundokensaku.database import PackItemRecord, connect, initialize, upsert_book
from tsundokensaku.export_stats import ItemStats
from tsundokensaku.token_estimate import TextStats


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

    def test_pdf_thumbnails_returns_base64_jpeg_for_requested_pages(self) -> None:
        import base64

        import fitz

        with tempfile.TemporaryDirectory() as temp_dir:
            books_dir = Path(temp_dir) / "books"
            books_dir.mkdir()
            pdf_path = books_dir / "sample.pdf"

            doc = fitz.open()
            for _ in range(5):
                doc.new_page(width=200, height=280)
            doc.save(str(pdf_path))
            doc.close()

            with patch("tsundokensaku.web.get_books_dir", return_value=books_dir):
                response = pdf_thumbnails(pdf_path="sample.pdf", pages="2,4")

            self.assertEqual(response.status_code, 200)
            payload = json.loads(response.body)
            self.assertEqual([p["page"] for p in payload["pages"]], [2, 4])
            for item in payload["pages"]:
                decoded = base64.b64decode(item["data"])
                self.assertTrue(decoded.startswith(b"\xff\xd8"))

    def test_pdf_thumbnails_allows_sixty_thumbnail_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            books_dir = Path(temp_dir) / "books"
            books_dir.mkdir()
            pdf_path = books_dir / "sample.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=72, height=72)
            with pdf_path.open("wb") as handle:
                writer.write(handle)

            rendered = [(page, b"\xff\xd8thumb") for page in range(1, 61)]
            with (
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
                patch("tsundokensaku.web.render_thumbnails", return_value=rendered) as render,
            ):
                response = pdf_thumbnails(pdf_path="sample.pdf", pages="1-60")

            self.assertEqual(response.status_code, 200)
            render.assert_called_once()

    def test_pdf_thumbnails_uses_thumbnail_preset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            books_dir = Path(temp_dir) / "books"
            books_dir.mkdir()
            pdf_path = books_dir / "sample.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=72, height=72)
            with pdf_path.open("wb") as handle:
                writer.write(handle)

            with (
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
                patch("tsundokensaku.web.render_thumbnails", return_value=[(1, b"\xff\xd8thumb")]) as render,
            ):
                response = pdf_thumbnails(pdf_path="sample.pdf", pages="1", size="thumbnail")

            self.assertEqual(response.status_code, 200)
            render.assert_called_once_with(pdf_path, [1], zoom=0.3, quality=70)

    def test_pdf_thumbnails_uses_detail_preset_for_single_page(self) -> None:
        import base64

        with tempfile.TemporaryDirectory() as temp_dir:
            books_dir = Path(temp_dir) / "books"
            books_dir.mkdir()
            pdf_path = books_dir / "sample.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=72, height=72)
            with pdf_path.open("wb") as handle:
                writer.write(handle)

            with (
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
                patch("tsundokensaku.web.render_thumbnail_detail", return_value=(1, b"\xff\xd8detail")) as render,
            ):
                response = pdf_thumbnails(pdf_path="sample.pdf", pages="1", size="detail")

            self.assertEqual(response.status_code, 200)
            payload = json.loads(response.body)
            self.assertEqual(payload["pages"], [{"page": 1, "data": base64.b64encode(b"\xff\xd8detail").decode("ascii")}])
            render.assert_called_once_with(pdf_path, 1, zoom=1.0, quality=85)

    def test_pdf_thumbnails_detail_allows_first_and_last_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            books_dir = Path(temp_dir) / "books"
            books_dir.mkdir()
            pdf_path = books_dir / "sample.pdf"
            writer = PdfWriter()
            for _ in range(3):
                writer.add_blank_page(width=72, height=72)
            with pdf_path.open("wb") as handle:
                writer.write(handle)

            with patch("tsundokensaku.web.get_books_dir", return_value=books_dir):
                first = pdf_thumbnails(pdf_path="sample.pdf", pages="1", size="detail")
                last = pdf_thumbnails(pdf_path="sample.pdf", pages="3", size="detail")

            self.assertEqual(first.status_code, 200)
            self.assertEqual(last.status_code, 200)

    def test_pdf_thumbnails_rejects_multiple_detail_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            books_dir = Path(temp_dir) / "books"
            books_dir.mkdir()
            pdf_path = books_dir / "sample.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=72, height=72)
            with pdf_path.open("wb") as handle:
                writer.write(handle)

            with patch("tsundokensaku.web.get_books_dir", return_value=books_dir):
                for pages in ["1,2", "1-2"]:
                    with self.subTest(pages=pages):
                        with self.assertRaises(HTTPException) as ctx:
                            pdf_thumbnails(pdf_path="sample.pdf", pages=pages, size="detail")
                        self.assertEqual(ctx.exception.status_code, 400)

    def test_pdf_thumbnails_rejects_invalid_detail_page_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            books_dir = Path(temp_dir) / "books"
            books_dir.mkdir()
            pdf_path = books_dir / "sample.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=72, height=72)
            with pdf_path.open("wb") as handle:
                writer.write(handle)

            with patch("tsundokensaku.web.get_books_dir", return_value=books_dir):
                for pages in ["0", "-1", "1.5", "１", "abc", " "]:
                    with self.subTest(pages=pages):
                        with self.assertRaises(HTTPException) as ctx:
                            pdf_thumbnails(pdf_path="sample.pdf", pages=pages, size="detail")
                        self.assertEqual(ctx.exception.status_code, 400)

    def test_pdf_thumbnails_returns_404_for_out_of_range_detail_page(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            books_dir = Path(temp_dir) / "books"
            books_dir.mkdir()
            pdf_path = books_dir / "sample.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=72, height=72)
            with pdf_path.open("wb") as handle:
                writer.write(handle)

            with (
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
                patch("tsundokensaku.web.render_thumbnail_detail", return_value=None) as render,
            ):
                with self.assertRaises(HTTPException) as ctx:
                    pdf_thumbnails(pdf_path="sample.pdf", pages="2", size="detail")

            self.assertEqual(ctx.exception.status_code, 404)
            render.assert_called_once_with(pdf_path, 2, zoom=1.0, quality=85)

    def test_pdf_thumbnails_rejects_unknown_size(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            books_dir = Path(temp_dir) / "books"
            books_dir.mkdir()
            pdf_path = books_dir / "sample.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=72, height=72)
            with pdf_path.open("wb") as handle:
                writer.write(handle)

            with patch("tsundokensaku.web.get_books_dir", return_value=books_dir):
                with self.assertRaises(HTTPException) as ctx:
                    pdf_thumbnails(pdf_path="sample.pdf", pages="1", size="full")
                self.assertEqual(ctx.exception.status_code, 400)

    def test_pdf_thumbnails_rejects_too_many_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            books_dir = Path(temp_dir) / "books"
            books_dir.mkdir()
            pdf_path = books_dir / "sample.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=72, height=72)
            with pdf_path.open("wb") as handle:
                writer.write(handle)

            with patch("tsundokensaku.web.get_books_dir", return_value=books_dir):
                with self.assertRaises(HTTPException) as ctx:
                    pdf_thumbnails(pdf_path="sample.pdf", pages="1-61")
                self.assertEqual(ctx.exception.status_code, 400)

    def test_pdf_thumbnails_requires_pages_param(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            books_dir = Path(temp_dir) / "books"
            books_dir.mkdir()
            pdf_path = books_dir / "sample.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=72, height=72)
            with pdf_path.open("wb") as handle:
                writer.write(handle)

            with patch("tsundokensaku.web.get_books_dir", return_value=books_dir):
                with self.assertRaises(HTTPException) as ctx:
                    pdf_thumbnails(pdf_path="sample.pdf", pages="")
                self.assertEqual(ctx.exception.status_code, 400)

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
        self.assertIn('class="card ws-controls-card"', body)
        self.assertIn('.ws-controls-card { position: relative; z-index: 1; overflow: visible; }', body)
        self.assertIn('id="ws-management"', body)
        self.assertIn("バックアップ", body)
        self.assertIn("この資料を空にする", body)
        self.assertIn('id="ws-count"', body)
        self.assertIn('id="ws-export-preview"', body)
        self.assertIn('href="/">検索画面で本を探す', body)
        self.assertIn('id="ws-export"', body)
        self.assertNotIn("ws-export-pdf", body)
        self.assertNotIn("ws-export-md", body)
        self.assertIn("/api/packs/${pack.id}/export/preview", body)
        self.assertIn("Markdown分冊", body)
        self.assertIn("章単位PDF", body)
        self.assertIn("PDF一式", body)
        self.assertIn("Markdown一式", body)

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

    def test_pack_api_round_trip_and_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            with patch("tsundokensaku.web.get_db_path", return_value=db_path):
                # 1. テストデータのインポート (v3形式)
                # 項目A, 項目B (同一PDF同一項目), 項目C
                payload = {
                    "version": 3,
                    "name": "ラウンドトリップ資料",
                    "items": [
                        {
                            "pdf_path": "same.pdf",
                            "title": "項目A",
                            "pages": "1-10",
                            "collapsed": False,
                            "addedAt": "2026-07-11T00:00:00Z",
                            "position": 0
                        },
                        {
                            "pdf_path": "same.pdf",
                            "title": "項目B",
                            "pages": "50-70",
                            "collapsed": True,
                            "addedAt": "2026-07-11T00:01:00Z",
                            "position": 1
                        },
                        {
                            "pdf_path": "other.pdf",
                            "title": "項目C",
                            "pages": "3-5",
                            "collapsed": False,
                            "addedAt": "2026-07-11T00:02:00Z",
                            "position": 2
                        }
                    ]
                }

                # 5. エクスポートデータを新しい資料へインポートできる
                imported = self._payload(api_import_pack(payload))
                pack_id = imported["id"]
                self.assertEqual(imported["name"], "ラウンドトリップ資料")

                # 6. インポート後も項目数が3件である
                # 13. インポート後のDB idは新規発行される
                self.assertEqual(len(imported["items"]), 3)
                for item in imported["items"]:
                    self.assertIsInstance(item["id"], int)
                    self.assertTrue(item["id"] > 0)

                # 2. JSONエクスポートの実行
                # 1. version: 3でエクスポートされる
                # 2. itemsが3件含まれる
                # 3. same.pdfの2件が統合されない
                # 4. itemsの順序がposition順である
                export_resp = api_export_pack(pack_id, format="json")
                self.assertEqual(export_resp.status_code, 200)
                self.assertEqual(export_resp.media_type, "application/json")
                
                import json
                exported = json.loads(export_resp.body.decode("utf-8"))
                
                self.assertEqual(exported["version"], 3)
                self.assertEqual(exported["name"], "ラウンドトリップ資料")
                self.assertEqual(len(exported["items"]), 3)
                
                # 7-12. 各フィールドが一致することの検証
                items = exported["items"]
                self.assertEqual(items[0]["pdf_path"], "same.pdf")
                self.assertEqual(items[0]["title"], "項目A")
                self.assertEqual(items[0]["pages"], "1-10")
                self.assertEqual(items[0]["collapsed"], False)
                self.assertEqual(items[0]["addedAt"], "2026-07-11T00:00:00Z")
                self.assertEqual(items[0]["position"], 0)

                self.assertEqual(items[1]["pdf_path"], "same.pdf")
                self.assertEqual(items[1]["title"], "項目B")
                self.assertEqual(items[1]["pages"], "50-70")
                self.assertEqual(items[1]["collapsed"], True)
                self.assertEqual(items[1]["addedAt"], "2026-07-11T00:01:00Z")
                self.assertEqual(items[1]["position"], 1)

                self.assertEqual(items[2]["pdf_path"], "other.pdf")
                self.assertEqual(items[2]["title"], "項目C")
                self.assertEqual(items[2]["pages"], "3-5")
                self.assertEqual(items[2]["collapsed"], False)
                self.assertEqual(items[2]["addedAt"], "2026-07-11T00:02:00Z")
                self.assertEqual(items[2]["position"], 2)

                # 14. 再エクスポートしたversion: 3データが意味的に一致する (Round Trip)
                re_imported = self._payload(api_import_pack(exported))
                re_export_resp = api_export_pack(re_imported["id"], format="json")
                re_exported = json.loads(re_export_resp.body.decode("utf-8"))
                
                self.assertEqual(re_exported["version"], exported["version"])
                self.assertEqual(re_exported["name"], exported["name"])
                self.assertEqual(len(re_exported["items"]), len(exported["items"]))
                for item_re, item_ex in zip(re_exported["items"], exported["items"]):
                    self.assertEqual(item_re["pdf_path"], item_ex["pdf_path"])
                    self.assertEqual(item_re["title"], item_ex["title"])
                    self.assertEqual(item_re["pages"], item_ex["pages"])
                    self.assertEqual(item_re["collapsed"], item_ex["collapsed"])
                    self.assertEqual(item_re["addedAt"], item_ex["addedAt"])
                    self.assertEqual(item_re["position"], item_ex["position"])

                # 15. v2データをインポートし、v3として再エクスポートできる
                v2_payload = {
                    "version": 2,
                    "books": {
                        "v2_book.pdf": {"title": "v2本", "pages": "10-20", "collapsed": True, "addedAt": "2026-07-11T05:00:00Z"}
                    }
                }
                v2_imported = self._payload(api_import_pack(v2_payload))
                v2_export_resp = api_export_pack(v2_imported["id"], format="json")
                v2_exported = json.loads(v2_export_resp.body.decode("utf-8"))
                
                self.assertEqual(v2_exported["version"], 3)
                self.assertEqual(v2_exported["items"][0]["pdf_path"], "v2_book.pdf")
                self.assertEqual(v2_exported["items"][0]["title"], "v2本")
                self.assertEqual(v2_exported["items"][0]["pages"], "10-20")
                self.assertEqual(v2_exported["items"][0]["collapsed"], True)
                self.assertEqual(v2_exported["items"][0]["addedAt"], "2026-07-11T05:00:00Z")

                # 16. 不正なv3データでは部分インポートされない
                invalid_payload = {
                    "version": 3,
                    "name": "不正資料",
                    "items": [
                        {
                            "pdf_path": "valid.pdf",
                            "title": "有効項目",
                            "pages": "1-5",
                            "collapsed": False,
                            "position": 0
                        },
                        {
                            "pdf_path": "invalid.pdf",
                            "title": "無効項目",
                            "pages": "99-10", # 不正範囲 (start > end)
                            "collapsed": False,
                            "position": 1
                        }
                    ]
                }
                pack_count_before = len(self._payload(api_list_packs())["packs"])
                
                with self.assertRaises(HTTPException) as ctx:
                    api_import_pack(invalid_payload)
                self.assertEqual(ctx.exception.status_code, 400)
                
                pack_count_after = len(self._payload(api_list_packs())["packs"])
                self.assertEqual(pack_count_before, pack_count_after)

                # 17. 様々な position パターンの検証
                # (1) position = [0, 1, 2] （正常）
                p1_payload = {
                    "version": 3,
                    "name": "pos1",
                    "items": [
                        {"pdf_path": "a.pdf", "title": "A", "pages": "1", "collapsed": False, "position": 0},
                        {"pdf_path": "b.pdf", "title": "B", "pages": "1", "collapsed": False, "position": 1},
                        {"pdf_path": "c.pdf", "title": "C", "pages": "1", "collapsed": False, "position": 2},
                    ]
                }
                res1 = self._payload(api_import_pack(p1_payload))
                self.assertEqual([item["position"] for item in res1["items"]], [0, 1, 2])
                self.assertEqual([item["title"] for item in res1["items"]], ["A", "B", "C"])

                # (2) position = [0, 2, 5] （隙間・欠番あり。順序関係を維持して 0, 1, 2 に正規化される）
                p2_payload = {
                    "version": 3,
                    "name": "pos2",
                    "items": [
                        {"pdf_path": "a.pdf", "title": "A", "pages": "1", "collapsed": False, "position": 0},
                        {"pdf_path": "b.pdf", "title": "B", "pages": "1", "collapsed": False, "position": 2},
                        {"pdf_path": "c.pdf", "title": "C", "pages": "1", "collapsed": False, "position": 5},
                    ]
                }
                res2 = self._payload(api_import_pack(p2_payload))
                self.assertEqual([item["position"] for item in res2["items"]], [0, 1, 2])
                self.assertEqual([item["title"] for item in res2["items"]], ["A", "B", "C"])

                # (3) position = [2, 0, 1] （配列順とposition指定が不一致。position値の昇順に並べ替えられて [0, 1, 2] に再採番）
                p3_payload = {
                    "version": 3,
                    "name": "pos3",
                    "items": [
                        {"pdf_path": "c.pdf", "title": "C", "pages": "1", "collapsed": False, "position": 2},
                        {"pdf_path": "a.pdf", "title": "A", "pages": "1", "collapsed": False, "position": 0},
                        {"pdf_path": "b.pdf", "title": "B", "pages": "1", "collapsed": False, "position": 1},
                    ]
                }
                res3 = self._payload(api_import_pack(p3_payload))
                self.assertEqual([item["position"] for item in res3["items"]], [0, 1, 2])
                self.assertEqual([item["title"] for item in res3["items"]], ["A", "B", "C"])

                # (4) position 重複 (例: [1, 1, 0] -> 不正扱いとなり配列内の順序 A -> B -> C を基準に [0, 1, 2] に再採番)
                p4_payload = {
                    "version": 3,
                    "name": "pos4",
                    "items": [
                        {"pdf_path": "a.pdf", "title": "A", "pages": "1", "collapsed": False, "position": 1},
                        {"pdf_path": "b.pdf", "title": "B", "pages": "1", "collapsed": False, "position": 1},
                        {"pdf_path": "c.pdf", "title": "C", "pages": "1", "collapsed": False, "position": 0},
                    ]
                }
                res4 = self._payload(api_import_pack(p4_payload))
                self.assertEqual([item["position"] for item in res4["items"]], [0, 1, 2])
                self.assertEqual([item["title"] for item in res4["items"]], ["A", "B", "C"])

                # (5) position 負数 (例: [-1, 0, 2] -> 配列順を基準に [0, 1, 2] に再採番)
                p5_payload = {
                    "version": 3,
                    "name": "pos5",
                    "items": [
                        {"pdf_path": "a.pdf", "title": "A", "pages": "1", "collapsed": False, "position": -1},
                        {"pdf_path": "b.pdf", "title": "B", "pages": "1", "collapsed": False, "position": 0},
                        {"pdf_path": "c.pdf", "title": "C", "pages": "1", "collapsed": False, "position": 2},
                    ]
                }
                res5 = self._payload(api_import_pack(p5_payload))
                self.assertEqual([item["position"] for item in res5["items"]], [0, 1, 2])
                self.assertEqual([item["title"] for item in res5["items"]], ["A", "B", "C"])

                # (6) position 欠落 (positionキーなし -> 配列順を基準に [0, 1, 2] に再採番)
                p6_payload = {
                    "version": 3,
                    "name": "pos6",
                    "items": [
                        {"pdf_path": "a.pdf", "title": "A", "pages": "1", "collapsed": False},
                        {"pdf_path": "b.pdf", "title": "B", "pages": "1", "collapsed": False},
                        {"pdf_path": "c.pdf", "title": "C", "pages": "1", "collapsed": False},
                    ]
                }
                res6 = self._payload(api_import_pack(p6_payload))
                self.assertEqual([item["position"] for item in res6["items"]], [0, 1, 2])
                self.assertEqual([item["title"] for item in res6["items"]], ["A", "B", "C"])

                # 18. 各種エラー時の例外およびHTTPステータスコード検証 (不正JSON, pages形式エラー, version不正, SQLite例外, RuntimeError)
                import sqlite3

                # (1) 不正JSON (dictでない型、例: 文字列) -> HTTPException(400) を期待
                with self.assertRaises(HTTPException) as ctx:
                    api_import_pack("invalid_json") # type: ignore
                self.assertEqual(ctx.exception.status_code, 400)

                # (2) pages形式エラー -> HTTPException(400)
                p_pages_err = {
                    "version": 3,
                    "name": "pages_err",
                    "items": [{"pdf_path": "a.pdf", "title": "A", "pages": "99-10", "collapsed": False}]
                }
                with self.assertRaises(HTTPException) as ctx:
                    api_import_pack(p_pages_err)
                self.assertEqual(ctx.exception.status_code, 400)

                # (3) version不正 -> HTTPException(400)
                p_version_err = {
                    "version": 99,
                    "name": "version_err",
                    "items": [{"pdf_path": "a.pdf", "title": "A", "pages": "1", "collapsed": False}]
                }
                with self.assertRaises(HTTPException) as ctx:
                    api_import_pack(p_version_err)
                self.assertEqual(ctx.exception.status_code, 400)

                # (4) SQLite例外 -> HTTPExceptionに変換されず sqlite3.Error がそのままスローされること (FastAPI既定の500になることを意味する)
                from unittest.mock import patch as mock_patch
                with mock_patch("tsundokensaku.database.replace_pack_item_entries", side_effect=sqlite3.Error("Mock DB Error")):
                    pack_count_before_db_err = len(self._payload(api_list_packs())["packs"])
                    
                    with self.assertRaises(sqlite3.Error):
                        api_import_pack(p6_payload)
                    
                    pack_count_after_db_err = len(self._payload(api_list_packs())["packs"])
                    self.assertEqual(pack_count_before_db_err, pack_count_after_db_err)

                # (5) RuntimeError -> HTTPExceptionに変換されず RuntimeError がそのままスローされること (FastAPI既定の500になることを意味する)
                with mock_patch("tsundokensaku.database.replace_pack_item_entries", side_effect=RuntimeError("Runtime Error")):
                    pack_count_before_rt_err = len(self._payload(api_list_packs())["packs"])
                    
                    with self.assertRaises(RuntimeError):
                        api_import_pack(p6_payload)
                    
                    pack_count_after_rt_err = len(self._payload(api_list_packs())["packs"])
                    self.assertEqual(pack_count_before_rt_err, pack_count_after_rt_err)

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

    def test_pack_api_export_zip_supports_duplicate_items(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"
            books_dir.mkdir(parents=True)

            # 10ページのPDFを作成
            writer = PdfWriter()
            for _ in range(10):
                writer.add_blank_page(width=72, height=72)
            with (books_dir / "a.pdf").open("wb") as handle:
                writer.write(handle)

            with patch("tsundokensaku.web.get_db_path", return_value=db_path), \
                    patch("tsundokensaku.web.get_books_dir", return_value=books_dir):
                created = self._payload(api_create_pack({"name": "重複資料"}))
                
                # 同一 pdf_path (a.pdf) を2件、異なるページ範囲で追加
                items = [
                    {"pdf_path": "a.pdf", "title": "本Aのパート1", "pages": "1-3", "collapsed": False, "position": 0},
                    {"pdf_path": "a.pdf", "title": "本Aのパート2", "pages": "5-8", "collapsed": False, "position": 1},
                ]
                from tsundokensaku.web import api_replace_pack_items
                self._payload(api_replace_pack_items(created["id"], {"items": items}))

                # エクスポートAPI呼び出し
                response = api_export_pack(created["id"], format="pdf")

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.media_type, "application/zip")

                with zipfile.ZipFile(BytesIO(response.body)) as archive:
                    names = archive.namelist()
                    # 2件が別ファイルとしてZIPに含まれ、衝突せずに出力順 (position順) になっている
                    self.assertEqual(names[0], "manifest.md")
                    self.assertEqual(names[1], "01_本Aのパート1_p1-3.pdf")
                    self.assertEqual(names[2], "02_本Aのパート2_p5-8.pdf")

                    # manifest の内容検証
                    manifest = archive.read("manifest.md").decode("utf-8")
                    self.assertIn("重複資料", manifest)
                    self.assertIn("本Aのパート1", manifest)
                    self.assertIn("本Aのパート2", manifest)

                    # 1件目のPDF（p.1-3 = 3ページ）
                    reader1 = PdfReader(BytesIO(archive.read(names[1])))
                    self.assertEqual(len(reader1.pages), 3)

                    # 2件目のPDF（p.5-8 = 4ページ）
                    reader2 = PdfReader(BytesIO(archive.read(names[2])))
                    self.assertEqual(len(reader2.pages), 4)

    def test_pack_api_export_zip_handles_missing_file_and_invalid_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"
            books_dir.mkdir(parents=True)

            with patch("tsundokensaku.web.get_db_path", return_value=db_path), \
                    patch("tsundokensaku.web.get_books_dir", return_value=books_dir):
                created = self._payload(api_create_pack({"name": "エラー資料"}))
                
                # 1. 存在しない PDF
                items_missing = [
                    {"pdf_path": "non_existent.pdf", "title": "消えた本", "pages": "1-3", "collapsed": False, "position": 0},
                ]
                from tsundokensaku.web import api_replace_pack_items
                self._payload(api_replace_pack_items(created["id"], {"items": items_missing}))

                with self.assertRaises(HTTPException) as ctx:
                    api_export_pack(created["id"], format="pdf")
                self.assertEqual(ctx.exception.status_code, 404)

                # 2. 存在するPDFだがページ範囲が不正
                writer = PdfWriter()
                writer.add_blank_page(width=72, height=72)
                with (books_dir / "valid.pdf").open("wb") as handle:
                    writer.write(handle)

                items_invalid_pages = [
                    {"pdf_path": "valid.pdf", "title": "本A", "pages": "99-100", "collapsed": False, "position": 0},
                ]
                self._payload(api_replace_pack_items(created["id"], {"items": items_invalid_pages}))

                with self.assertRaises(HTTPException) as ctx:
                    api_export_pack(created["id"], format="pdf")
                self.assertEqual(ctx.exception.status_code, 400)


class PackStatsApiTest(unittest.TestCase):
    """Phase 2C: GET /api/packs/stats の集計内容そのものの正しさ。"""

    def _payload(self, response) -> dict:
        return json.loads(response.body)

    def _make_pdf(self, path: Path, page_count: int) -> None:
        writer = PdfWriter()
        for _ in range(page_count):
            writer.add_blank_page(width=72, height=72)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            writer.write(handle)

    def _index_pages(self, db_path: Path, pdf_path: Path, *, title: str, texts: list[str]) -> None:
        from tsundokensaku.database import PageRecord, replace_pages

        connection = connect(db_path)
        initialize(connection)
        book_id = upsert_book(
            connection,
            path=pdf_path,
            title=title,
            size_bytes=pdf_path.stat().st_size,
            modified_at=pdf_path.stat().st_mtime,
        )
        replace_pages(
            connection,
            book_id=book_id,
            title=title,
            pages=[PageRecord(page_number=index, text=text) for index, text in enumerate(texts, start=1)],
        )
        connection.commit()
        connection.close()

    def test_returns_item_count_pages_and_estimated_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"
            self._make_pdf(books_dir / "a.pdf", 5)
            self._index_pages(
                db_path, books_dir / "a.pdf", title="本A",
                texts=["あ" * 10 for _ in range(5)],
            )

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
            ):
                created = self._payload(api_create_pack({"name": "統計テスト資料"}))
                items = [{"pdf_path": "a.pdf", "title": "本A", "pages": "1-3", "collapsed": False, "position": 0}]
                self._payload(api_replace_pack_items(created["id"], {"items": items}))

                payload = self._payload(api_list_pack_stats())

            pack = next(p for p in payload["packs"] if p["id"] == created["id"])
            self.assertEqual(pack["book_count"], 1)
            self.assertEqual(pack["item_count"], 1)
            self.assertEqual(pack["total_pages"], 3)
            self.assertGreater(pack["estimated_tokens"], 0)

    def test_duplicate_pdf_path_counts_as_one_book_but_two_items(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"
            self._make_pdf(books_dir / "a.pdf", 20)
            self._index_pages(
                db_path, books_dir / "a.pdf", title="本A",
                texts=["あ" * 10 for _ in range(20)],
            )

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
            ):
                created = self._payload(api_create_pack({"name": "分冊資料"}))
                items = [
                    {"pdf_path": "a.pdf", "title": "本A-前半", "pages": "1-5", "collapsed": False, "position": 0},
                    {"pdf_path": "a.pdf", "title": "本A-後半", "pages": "10-15", "collapsed": False, "position": 1},
                ]
                self._payload(api_replace_pack_items(created["id"], {"items": items}))

                payload = self._payload(api_list_pack_stats())

            pack = next(p for p in payload["packs"] if p["id"] == created["id"])
            self.assertEqual(pack["book_count"], 1)
            self.assertEqual(pack["item_count"], 2)
            self.assertEqual(pack["total_pages"], 11)

    def test_empty_pack_returns_zeroed_stats(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            with patch("tsundokensaku.web.get_db_path", return_value=db_path):
                created = self._payload(api_create_pack({"name": "空の資料"}))
                payload = self._payload(api_list_pack_stats())

            pack = next(p for p in payload["packs"] if p["id"] == created["id"])
            self.assertEqual(pack["book_count"], 0)
            self.assertEqual(pack["item_count"], 0)
            self.assertEqual(pack["total_pages"], 0)
            self.assertEqual(pack["estimated_tokens"], 0)


class PackStatsRoutingTest(unittest.TestCase):
    """Phase 2C: /api/packs/stats が /api/packs/{pack_id} と競合しないことをHTTPルーティング層で確認する。

    他のテストはハンドラ関数を直接呼び出しているが、ルーティング競合は
    FastAPIのルーター自体を経由しないと再現できないため、ここだけ
    TestClient で実際のHTTPディスパッチを検証する。
    """

    def test_stats_route_is_not_shadowed_by_pack_id_route(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            with patch("tsundokensaku.web.get_db_path", return_value=db_path):
                client = TestClient(tsundokensaku_app)
                response = client.get("/api/packs/stats")

                self.assertEqual(response.status_code, 200)
                body = response.json()
                self.assertIn("packs", body)
                self.assertIn("active_pack_id", body)

    def test_numeric_pack_id_route_still_works_after_stats_route_added(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            with patch("tsundokensaku.web.get_db_path", return_value=db_path):
                client = TestClient(tsundokensaku_app)
                created = client.post("/api/packs", json={"name": "ルーティング確認資料"}).json()

                response = client.get(f"/api/packs/{created['id']}")

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["id"], created["id"])
                self.assertEqual(response.json()["name"], "ルーティング確認資料")

    def test_nonexistent_pack_id_returns_404_as_before(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            with patch("tsundokensaku.web.get_db_path", return_value=db_path):
                client = TestClient(tsundokensaku_app)
                response = client.get("/api/packs/999999")

                self.assertEqual(response.status_code, 404)

    def test_openapi_schema_includes_new_stats_endpoint(self) -> None:
        client = TestClient(tsundokensaku_app)
        schema = client.get("/openapi.json").json()

        self.assertIn("/api/packs/stats", schema["paths"])
        self.assertIn("get", schema["paths"]["/api/packs/stats"])


class ExportArchiveBackwardCompatibilityTest(unittest.TestCase):
    """B-2: api_export_pack を StandardProfile 経由へ載せ替えた前後の出力互換性。

    載せ替え前のコード（web.py に直書きされていたループ）はもう存在しないため、
    「変更前の実装と突き合わせる」形式のテストは書けない。代わりに、載せ替えの
    リスクが最も高い項目（ZIP構造・エントリ内容・エラー応答）を、載せ替え前の
    挙動から導出した期待値に対して固定的に検証する（設計書の「既存動作を
    変えない」という要求に対するゴールデンテスト）。

    ZIP内エントリのタイムスタンプは zipfile.writestr が実行時刻から都度
    生成するため、バイト列そのものの完全一致は再現性がなく検証しない。
    ZIPを展開した論理的な内容（エントリ名・順序・各エントリの中身）の
    完全一致で後方互換性を確認する。
    """

    def _payload(self, response) -> dict:
        return json.loads(response.body)

    def _make_pdf(self, path: Path, page_heights: list[int]) -> None:
        # 各ページの高さを変えておくと、出力後のページから元のページ番号を
        # 復元でき、「どのページが選択されたか」を内容レベルで検証できる
        writer = PdfWriter()
        for height in page_heights:
            writer.add_blank_page(width=72, height=height)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            writer.write(handle)

    def _page_numbers_in_pdf(self, pdf_bytes: bytes) -> list[int]:
        # _make_pdf の height=100+page_number という規則から元のページ番号を逆算する
        reader = PdfReader(BytesIO(pdf_bytes))
        return [int(page.mediabox.height) - 100 for page in reader.pages]

    def test_export_default_format_is_pdf(self) -> None:
        # format のシグネチャ既定値は素の None（Query(...) ではない）にしたため、
        # 直接関数呼び出しで format を省略しても正しく解決される
        # （profile 未指定 → standard.primary_format は None → 既定 "pdf"）
        import inspect

        default = inspect.signature(api_export_pack).parameters["format"].default
        self.assertIsNone(default)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"
            self._make_pdf(books_dir / "a.pdf", [100 + n for n in range(1, 4)])

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
            ):
                created = self._payload(api_create_pack({"name": "資料"}))
                items = [{"pdf_path": "a.pdf", "title": "本A", "pages": "1-2", "collapsed": False, "position": 0}]
                self._payload(api_replace_pack_items(created["id"], {"items": items}))

                response = api_export_pack(created["id"])  # profile・format とも省略

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.media_type, "application/zip")
                with zipfile.ZipFile(BytesIO(response.body)) as archive:
                    self.assertEqual(archive.namelist(), ["manifest.md", "01_本A_p1-2.pdf"])

    def test_pdf_export_zip_structure_with_duplicate_pdf_and_multi_range(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"
            # ページ1〜10。高さ = 100+ページ番号
            self._make_pdf(books_dir / "a.pdf", [100 + n for n in range(1, 11)])
            self._make_pdf(books_dir / "b.pdf", [100 + n for n in range(1, 4)])

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
            ):
                created = self._payload(api_create_pack({"name": "後方互換確認資料"}))
                items = [
                    # 同一PDF(a.pdf)を離れた範囲で2項目 + 複数区間のページ範囲
                    {"pdf_path": "a.pdf", "title": "本Aの前半", "pages": "1-3", "collapsed": False, "position": 0},
                    {"pdf_path": "b.pdf", "title": "本B", "pages": "2", "collapsed": False, "position": 1},
                    {"pdf_path": "a.pdf", "title": "本Aの後半", "pages": "6,8-10", "collapsed": False, "position": 2},
                ]
                self._payload(api_replace_pack_items(created["id"], {"items": items}))

                response = api_export_pack(created["id"], format="pdf")

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.media_type, "application/zip")
                self.assertIn("attachment", response.headers["content-disposition"])
                self.assertIn("filename*=UTF-8''", response.headers["content-disposition"])

                with zipfile.ZipFile(BytesIO(response.body)) as archive:
                    names = archive.namelist()
                    # エントリ名・エントリ順（=position順）
                    self.assertEqual(
                        names,
                        [
                            "manifest.md",
                            "01_本Aの前半_p1-3.pdf",
                            "02_本B_p2.pdf",
                            "03_本Aの後半_p6_8-10.pdf",
                        ],
                    )

                    # 各PDFの実際の内容（選択されたページ番号そのもの）
                    self.assertEqual(self._page_numbers_in_pdf(archive.read(names[1])), [1, 2, 3])
                    self.assertEqual(self._page_numbers_in_pdf(archive.read(names[2])), [2])
                    self.assertEqual(self._page_numbers_in_pdf(archive.read(names[3])), [6, 8, 9, 10])

                    # manifest.md の内容
                    manifest = archive.read("manifest.md").decode("utf-8")
                    self.assertIn("# 後方互換確認資料（資料一式）", manifest)
                    self.assertIn("- 収録: 3冊", manifest)
                    self.assertIn("1. 本Aの前半 — p.1-3 （01_本Aの前半_p1-3.pdf）", manifest)
                    self.assertIn("2. 本B — p.2 （02_本B_p2.pdf）", manifest)
                    self.assertIn("3. 本Aの後半 — p.6,8-10 （03_本Aの後半_p6_8-10.pdf）", manifest)

                    # ZIP名
                    disposition = response.headers["content-disposition"]
                    self.assertIn(quote(f"後方互換確認資料_{_now_jst():%Y%m%d}.zip"), disposition)

    def test_markdown_export_zip_matches_indexed_source_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"
            pdf_path = books_dir / "a.pdf"
            self._make_pdf(pdf_path, [100 + n for n in range(1, 4)])

            connection = connect(db_path)
            initialize(connection)
            book_id = upsert_book(
                connection,
                path=pdf_path,
                title="本A",
                size_bytes=pdf_path.stat().st_size,
                modified_at=pdf_path.stat().st_mtime,
            )
            from tsundokensaku.database import PageRecord, replace_pages

            replace_pages(
                connection,
                book_id=book_id,
                title="本A",
                pages=[
                    PageRecord(page_number=1, text="第1ページの本文"),
                    PageRecord(page_number=2, text="第2ページの本文"),
                    PageRecord(page_number=3, text="第3ページの本文"),
                ],
            )
            connection.commit()
            connection.close()

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
            ):
                created = self._payload(api_create_pack({"name": "MD資料"}))
                items = [{"pdf_path": "a.pdf", "title": "本A", "pages": "1,3", "collapsed": False, "position": 0}]
                self._payload(api_replace_pack_items(created["id"], {"items": items}))

                response = api_export_pack(created["id"], format="md")

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.media_type, "application/zip")
                with zipfile.ZipFile(BytesIO(response.body)) as archive:
                    names = archive.namelist()
                    self.assertEqual(names, ["manifest.md", "01_本A_p1_3.md"])

                    content = archive.read("01_本A_p1_3.md").decode("utf-8")
                    self.assertIn("# 本A（抜粋）", content)
                    self.assertIn("- 元ファイル: a.pdf", content)
                    self.assertIn("## p.1", content)
                    self.assertIn("第1ページの本文", content)
                    self.assertIn("## p.3", content)
                    self.assertIn("第3ページの本文", content)
                    # 選択範囲外の2ページ目の本文は含まれない
                    self.assertNotIn("第2ページの本文", content)

    def test_export_error_responses_keep_status_and_message(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"
            books_dir.mkdir(parents=True)

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
            ):
                # 空資料
                empty_pack = self._payload(api_create_pack({"name": "空資料"}))
                with self.assertRaises(HTTPException) as ctx:
                    api_export_pack(empty_pack["id"], format="pdf")
                self.assertEqual(ctx.exception.status_code, 400)
                self.assertEqual(ctx.exception.detail, "資料が空です")

                # ページ未指定
                self._make_pdf(books_dir / "a.pdf", [100 + n for n in range(1, 4)])
                missing_pages_pack = self._payload(api_create_pack({"name": "ページ未指定資料"}))
                items = [{"pdf_path": "a.pdf", "title": "本A", "pages": "", "collapsed": False, "position": 0}]
                self._payload(api_replace_pack_items(missing_pages_pack["id"], {"items": items}))
                with self.assertRaises(HTTPException) as ctx:
                    api_export_pack(missing_pages_pack["id"], format="pdf")
                self.assertEqual(ctx.exception.status_code, 400)
                self.assertEqual(ctx.exception.detail, "本A: ページを指定してください")

                # PDF欠損
                missing_pdf_pack = self._payload(api_create_pack({"name": "PDF欠損資料"}))
                items = [{"pdf_path": "does-not-exist.pdf", "title": "消えた本", "pages": "1", "collapsed": False, "position": 0}]
                self._payload(api_replace_pack_items(missing_pdf_pack["id"], {"items": items}))
                with self.assertRaises(HTTPException) as ctx:
                    api_export_pack(missing_pdf_pack["id"], format="pdf")
                self.assertEqual(ctx.exception.status_code, 404)
                self.assertEqual(ctx.exception.detail, "PDF not found")

                # 不正なページ範囲
                invalid_range_pack = self._payload(api_create_pack({"name": "不正範囲資料"}))
                items = [{"pdf_path": "a.pdf", "title": "本A", "pages": "99-100", "collapsed": False, "position": 0}]
                self._payload(api_replace_pack_items(invalid_range_pack["id"], {"items": items}))
                with self.assertRaises(HTTPException) as ctx:
                    api_export_pack(invalid_range_pack["id"], format="pdf")
                self.assertEqual(ctx.exception.status_code, 400)
                self.assertIn("out of range", ctx.exception.detail)


class ExportProfileParameterTest(unittest.TestCase):
    """B-3: /api/packs/{id}/export への profile クエリパラメータ対応。

    profile 未指定は standard と完全互換であることが目的のため、多くの
    テストは「未指定」と「profile=standard」の2通りを同一資料に対して
    実行し、結果が一致することを検証する形にしている。
    """

    def _payload(self, response) -> dict:
        return json.loads(response.body)

    def _make_pdf(self, path: Path, page_count: int) -> None:
        writer = PdfWriter()
        for _ in range(page_count):
            writer.add_blank_page(width=72, height=72)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            writer.write(handle)

    def _make_pdf_with_toc(self, path: Path, page_count: int, toc: list[list[object]]) -> None:
        import fitz

        path.parent.mkdir(parents=True, exist_ok=True)
        doc = fitz.open()
        for _ in range(page_count):
            doc.new_page(width=72, height=72)
        doc.set_toc(toc)
        doc.save(str(path))
        doc.close()

    def _index_pages(self, db_path: Path, pdf_path: Path, *, title: str, texts: list[str]) -> None:
        from tsundokensaku.database import PageRecord, replace_pages

        connection = connect(db_path)
        initialize(connection)
        book_id = upsert_book(
            connection,
            path=pdf_path,
            title=title,
            size_bytes=pdf_path.stat().st_size,
            modified_at=pdf_path.stat().st_mtime,
        )
        replace_pages(
            connection,
            book_id=book_id,
            title=title,
            pages=[PageRecord(page_number=index, text=text) for index, text in enumerate(texts, start=1)],
        )
        connection.commit()
        connection.close()

    def _count_events(self, db_path: Path) -> int:
        import sqlite3

        connection = sqlite3.connect(str(db_path))
        try:
            return connection.execute("SELECT COUNT(*) FROM export_events").fetchone()[0]
        finally:
            connection.close()

    def _setup_pack_with_one_item(self, books_dir: Path) -> dict:
        self._make_pdf(books_dir / "a.pdf", 5)
        created = self._payload(api_create_pack({"name": "資料"}))
        items = [{"pdf_path": "a.pdf", "title": "本A", "pages": "1-3", "collapsed": False, "position": 0}]
        self._payload(api_replace_pack_items(created["id"], {"items": items}))
        return created

    def test_default_profile_is_none_and_default_format_is_none(self) -> None:
        # format は「省略された」ことを判別できるよう素の None を既定値にする
        # （resolve_profile(None) が standard に解決した後、primary_format が
        # None なら "pdf" にフォールバックする。§12.2）
        import inspect

        parameters = inspect.signature(api_export_pack).parameters
        self.assertIsNone(parameters["profile"].default)
        self.assertIsNone(parameters["format"].default)

    def test_profile_unspecified_and_all_formats_succeed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
            ):
                created = self._setup_pack_with_one_item(books_dir)

                for fmt, media_type in (("pdf", "application/zip"), ("md", "application/zip"), ("json", "application/json")):
                    response = api_export_pack(created["id"], format=fmt)
                    self.assertEqual(response.status_code, 200, msg=f"format={fmt}")
                    self.assertEqual(response.media_type, media_type, msg=f"format={fmt}")

    def test_profile_standard_and_all_formats_succeed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
            ):
                created = self._setup_pack_with_one_item(books_dir)

                for fmt, media_type in (("pdf", "application/zip"), ("md", "application/zip"), ("json", "application/json")):
                    response = api_export_pack(created["id"], profile="standard", format=fmt)
                    self.assertEqual(response.status_code, 200, msg=f"format={fmt}")
                    self.assertEqual(response.media_type, media_type, msg=f"format={fmt}")

    def test_profile_unspecified_and_standard_use_same_default_format(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
            ):
                created = self._setup_pack_with_one_item(books_dir)

                unspecified = api_export_pack(created["id"])
                standard = api_export_pack(created["id"], profile="standard")

                self.assertEqual(unspecified.status_code, 200)
                self.assertEqual(standard.status_code, 200)
                self.assertEqual(unspecified.media_type, standard.media_type)
                with zipfile.ZipFile(BytesIO(unspecified.body)) as archive:
                    self.assertEqual(archive.namelist(), ["manifest.md", "01_本A_p1-3.pdf"])

    def _assert_responses_are_identical(self, a, b) -> None:
        self.assertEqual(a.status_code, b.status_code)
        self.assertEqual(a.media_type, b.media_type)
        self.assertEqual(a.headers["content-disposition"], b.headers["content-disposition"])

    def test_profile_unspecified_matches_standard_for_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
            ):
                created = self._setup_pack_with_one_item(books_dir)

                unspecified = api_export_pack(created["id"], format="pdf")
                standard = api_export_pack(created["id"], profile="standard", format="pdf")

                self._assert_responses_are_identical(unspecified, standard)
                with (
                    zipfile.ZipFile(BytesIO(unspecified.body)) as archive_a,
                    zipfile.ZipFile(BytesIO(standard.body)) as archive_b,
                ):
                    self.assertEqual(archive_a.namelist(), archive_b.namelist())
                    for name in archive_a.namelist():
                        self.assertEqual(archive_a.read(name), archive_b.read(name), msg=name)

    def test_profile_unspecified_matches_standard_for_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
            ):
                created = self._setup_pack_with_one_item(books_dir)

                unspecified = api_export_pack(created["id"], format="md")
                standard = api_export_pack(created["id"], profile="standard", format="md")

                self._assert_responses_are_identical(unspecified, standard)
                with (
                    zipfile.ZipFile(BytesIO(unspecified.body)) as archive_a,
                    zipfile.ZipFile(BytesIO(standard.body)) as archive_b,
                ):
                    self.assertEqual(archive_a.namelist(), archive_b.namelist())
                    for name in archive_a.namelist():
                        self.assertEqual(archive_a.read(name), archive_b.read(name), msg=name)

    def test_profile_unspecified_matches_standard_for_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
            ):
                created = self._setup_pack_with_one_item(books_dir)

                unspecified = api_export_pack(created["id"], format="json")
                standard = api_export_pack(created["id"], profile="standard", format="json")

                self._assert_responses_are_identical(unspecified, standard)
                self.assertEqual(unspecified.body, standard.body)

    def test_profile_standard_zip_filename_matches_unspecified(self) -> None:
        # profile=standard を付けても現行のZIP名（{資料名}_{YYYYMMDD}.zip）を維持する
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
            ):
                created = self._setup_pack_with_one_item(books_dir)

                standard = api_export_pack(created["id"], profile="standard", format="pdf")
                disposition = standard.headers["content-disposition"]
                self.assertIn(quote(f"資料_{_now_jst():%Y%m%d}.zip"), disposition)
                self.assertNotIn("standard", disposition)

    def test_unknown_profile_returns_400_with_available_values_hint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            with patch("tsundokensaku.web.get_db_path", return_value=db_path):
                created = self._payload(api_create_pack({"name": "資料"}))
                with self.assertRaises(HTTPException) as ctx:
                    api_export_pack(created["id"], profile="unknown", format="pdf")
                self.assertEqual(ctx.exception.status_code, 400)
                self.assertEqual(ctx.exception.detail, "不明なエクスポートプロファイルです: unknown")

    def test_old_profile_name_notebooklm_is_rejected(self) -> None:
        # notebooklm は chapter へ改名済み。旧名を受理しないことを明示的に確認する
        # （docs/export-profile-naming-review.md）
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            with patch("tsundokensaku.web.get_db_path", return_value=db_path):
                created = self._payload(api_create_pack({"name": "資料"}))
                with self.assertRaises(HTTPException) as ctx:
                    api_export_pack(created["id"], profile="notebooklm", format="pdf")
                self.assertEqual(ctx.exception.status_code, 400)
                self.assertEqual(ctx.exception.detail, "不明なエクスポートプロファイルです: notebooklm")

    def test_profile_chapter_format_omitted_resolves_to_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"
            self._make_pdf_with_toc(books_dir / "a.pdf", 6, [[1, "第1章", 1], [1, "第2章", 4]])
            self._index_pages(db_path, books_dir / "a.pdf", title="本A", texts=[f"page {i}" for i in range(1, 7)])

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
                patch.dict(os.environ, {"TSUNDOKENSAKU_CHAPTER_MAX_PAGES_PER_FILE": "4"}),
            ):
                created = self._payload(api_create_pack({"name": "資料"}))
                items = [{"pdf_path": "a.pdf", "title": "本A", "pages": "1-6", "collapsed": False, "position": 0}]
                self._payload(api_replace_pack_items(created["id"], {"items": items}))

                response = api_export_pack(created["id"], profile="chapter")

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.media_type, "application/zip")
                with zipfile.ZipFile(BytesIO(response.body)) as archive:
                    self.assertEqual(
                        archive.namelist(),
                        ["manifest.md", "01_本A_第1章_p1-4.pdf", "02_本A_第2章_p4-6.pdf"],
                    )
                    manifest = archive.read("manifest.md").decode("utf-8")
                    self.assertIn("第1章 — p.1-4", manifest)
                    self.assertIn("第2章 — p.4-6", manifest)
                    self.assertIn("章単位に分割して出力します", manifest)

    def test_profile_chapter_rejects_conflicting_format(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
            ):
                created = self._setup_pack_with_one_item(books_dir)

                with self.assertRaises(HTTPException) as ctx:
                    api_export_pack(created["id"], profile="chapter", format="md")
                self.assertEqual(ctx.exception.status_code, 400)
                self.assertEqual(ctx.exception.detail, "profile=chapter では format=pdf のみ指定できます")

    def test_profile_chapter_falls_back_to_page_blocks_without_outline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"
            self._make_pdf(books_dir / "a.pdf", 5)
            self._index_pages(db_path, books_dir / "a.pdf", title="本A", texts=[f"page {i}" for i in range(1, 6)])

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
                patch.dict(os.environ, {"TSUNDOKENSAKU_CHAPTER_MAX_PAGES_PER_FILE": "2"}),
            ):
                created = self._payload(api_create_pack({"name": "資料"}))
                items = [{"pdf_path": "a.pdf", "title": "本A", "pages": "1-5", "collapsed": False, "position": 0}]
                self._payload(api_replace_pack_items(created["id"], {"items": items}))

                response = api_export_pack(created["id"], profile="chapter", format="pdf")

                with zipfile.ZipFile(BytesIO(response.body)) as archive:
                    self.assertEqual(
                        archive.namelist(),
                        ["manifest.md", "01_本A_part1_p1-2.pdf", "02_本A_part2_p3-4.pdf", "03_本A_part3_p5.pdf"],
                    )
                    manifest = archive.read("manifest.md").decode("utf-8")
                    self.assertIn("part1 — p.1-2", manifest)
                    self.assertIn("アウトラインがないため連続ページ単位で分割します", manifest)

    def test_profile_chapter_records_export_event_on_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"
            self._make_pdf(books_dir / "a.pdf", 2)
            self._index_pages(db_path, books_dir / "a.pdf", title="本A", texts=["a", "b"])

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
            ):
                created = self._payload(api_create_pack({"name": "資料"}))
                items = [{"pdf_path": "a.pdf", "title": "本A", "pages": "1-2", "collapsed": False, "position": 0}]
                self._payload(api_replace_pack_items(created["id"], {"items": items}))
                before = self._count_events(db_path)

                response = api_export_pack(created["id"], profile="chapter")

                self.assertEqual(response.status_code, 200)
                self.assertEqual(self._count_events(db_path), before + 1)

    def test_invalid_format_still_returns_400_with_standard_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            with patch("tsundokensaku.web.get_db_path", return_value=db_path):
                created = self._payload(api_create_pack({"name": "資料"}))
                with self.assertRaises(HTTPException) as ctx:
                    api_export_pack(created["id"], profile="standard", format="epub")
                self.assertEqual(ctx.exception.status_code, 400)
                self.assertEqual(ctx.exception.detail, "format は pdf, md, または json を指定してください")

    def test_unknown_profile_takes_priority_over_invalid_format(self) -> None:
        # 検証順序: 1.profile解決 2.format検証 3.profile/format整合性 4.pack取得
        # 不明profile・不正format・存在しないpackが同時に揃っても、最初に
        # 検出されるのは不明profileであることを固定する
        with self.assertRaises(HTTPException) as ctx:
            api_export_pack(9999, profile="unknown", format="epub")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.detail, "不明なエクスポートプロファイルです: unknown")

    def test_missing_pack_returns_404_after_profile_and_format_pass(self) -> None:
        # 既存のpack不存在時の挙動（404）は、profile解決・format検証の後段で
        # そのまま維持されることを確認する
        with self.assertRaises(HTTPException) as ctx:
            api_export_pack(9999, profile="standard", format="pdf")
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(ctx.exception.detail, "資料が見つかりません")

    def test_chat_profile_registered_and_uses_md_as_primary_format(self) -> None:
        from tsundokensaku.export_profiles import PROFILES

        self.assertIn("chat", PROFILES)
        self.assertEqual(PROFILES["chat"].primary_format, "md")

    def test_profile_chat_format_omitted_resolves_to_md(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
            ):
                created = self._setup_pack_with_one_item(books_dir)

                response = api_export_pack(created["id"], profile="chat")  # format省略

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.media_type, "application/zip")
                with zipfile.ZipFile(BytesIO(response.body)) as archive:
                    names = archive.namelist()
                    self.assertEqual(names, ["manifest.md", "資料_chat_01.md"])

    def test_profile_chat_rejects_conflicting_format(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
            ):
                created = self._setup_pack_with_one_item(books_dir)

                with self.assertRaises(HTTPException) as ctx:
                    api_export_pack(created["id"], profile="chat", format="pdf")
                self.assertEqual(ctx.exception.status_code, 400)
                self.assertEqual(ctx.exception.detail, "profile=chat では format=md のみ指定できます")

    def test_profile_chat_combines_small_items_and_lists_them_in_manifest(self) -> None:
        from tsundokensaku.database import PageRecord, replace_pages

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"
            pdf_path_a = books_dir / "a.pdf"
            pdf_path_b = books_dir / "b.pdf"
            self._make_pdf(pdf_path_a, 2)
            self._make_pdf(pdf_path_b, 2)

            connection = connect(db_path)
            initialize(connection)
            book_id_a = upsert_book(
                connection, path=pdf_path_a, title="本A",
                size_bytes=pdf_path_a.stat().st_size, modified_at=pdf_path_a.stat().st_mtime,
            )
            book_id_b = upsert_book(
                connection, path=pdf_path_b, title="本B",
                size_bytes=pdf_path_b.stat().st_size, modified_at=pdf_path_b.stat().st_mtime,
            )
            replace_pages(connection, book_id=book_id_a, title="本A", pages=[
                PageRecord(page_number=1, text="本Aの1ページ目"),
                PageRecord(page_number=2, text="本Aの2ページ目"),
            ])
            replace_pages(connection, book_id=book_id_b, title="本B", pages=[
                PageRecord(page_number=1, text="本Bの1ページ目"),
                PageRecord(page_number=2, text="本Bの2ページ目"),
            ])
            connection.commit()
            connection.close()

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
            ):
                created = self._payload(api_create_pack({"name": "対比資料"}))
                items = [
                    {"pdf_path": "a.pdf", "title": "本A", "pages": "1-2", "collapsed": False, "position": 0},
                    {"pdf_path": "b.pdf", "title": "本B", "pages": "1-2", "collapsed": False, "position": 1},
                ]
                self._payload(api_replace_pack_items(created["id"], {"items": items}))

                response = api_export_pack(created["id"], profile="chat")

                self.assertEqual(response.status_code, 200)
                with zipfile.ZipFile(BytesIO(response.body)) as archive:
                    names = archive.namelist()
                    # 小さい2項目は80,000トークン以内なので1チャンクに結合される
                    self.assertEqual(names, ["manifest.md", "対比資料_chat_01.md"])

                    manifest = archive.read("manifest.md").decode("utf-8")
                    self.assertIn("- プロファイル: chat", manifest)
                    self.assertIn("1. 対比資料_chat_01.md", manifest)
                    # 結合されたチャンクでも両方の項目の出典がmanifestに残る
                    self.assertIn("本A — p.1-2", manifest)
                    self.assertIn("本B — p.1-2", manifest)
                    self.assertNotIn("## 警告", manifest)

                    content = archive.read("対比資料_chat_01.md").decode("utf-8")
                    self.assertIn("対比資料（分冊 1/1）", content)
                    self.assertIn("本Aの1ページ目", content)
                    self.assertIn("本Bの1ページ目", content)

    def test_profile_chat_isolates_and_warns_for_item_exceeding_token_limit(self) -> None:
        from tsundokensaku.database import PageRecord, replace_pages

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"
            pdf_path = books_dir / "huge.pdf"
            page_count = 12
            self._make_pdf(pdf_path, page_count)

            connection = connect(db_path)
            initialize(connection)
            book_id = upsert_book(
                connection, path=pdf_path, title="巨大本",
                size_bytes=pdf_path.stat().st_size, modified_at=pdf_path.stat().st_mtime,
            )
            # 1ページ 8,000 CJK文字 x 12ページ = 96,000文字 -> 推定96,000トークン相当。
            # chatの上限80,000を超える（Sudachiの1呼び出しあたりバイト上限を避けるため複数ページに分割）
            replace_pages(connection, book_id=book_id, title="巨大本", pages=[
                PageRecord(page_number=n, text="あ" * 8_000) for n in range(1, page_count + 1)
            ])
            connection.commit()
            connection.close()

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
            ):
                created = self._payload(api_create_pack({"name": "資料"}))
                items = [{"pdf_path": "huge.pdf", "title": "巨大本", "pages": f"1-{page_count}", "collapsed": False, "position": 0}]
                self._payload(api_replace_pack_items(created["id"], {"items": items}))

                response = api_export_pack(created["id"], profile="chat")

                self.assertEqual(response.status_code, 200)
                with zipfile.ZipFile(BytesIO(response.body)) as archive:
                    names = archive.namelist()
                    # 単独で上限超過でも切り捨てず単独チャンクとして出力される
                    self.assertEqual(names, ["manifest.md", "資料_chat_01.md"])

                    manifest = archive.read("manifest.md").decode("utf-8")
                    self.assertIn("## 警告", manifest)
                    self.assertIn("「巨大本」は1ファイルの上限を超えるため単独で出力します", manifest)


class BuildExportPreviewPayloadTest(unittest.TestCase):
    """collect_item_stats の結果からプレビューJSONを組み立てる純粋関数のテスト。

    DB/PDFを介さず ItemStats を直接組み立てるため、集計ロジックの境界値
    （トークン数の丸め方・警告の優先順位）だけを高速に検証できる。
    """

    def _item(self, item_id: int, *, pdf_path: str = "a.pdf", pages: str = "1-2", title: str = "本") -> PackItemRecord:
        return PackItemRecord(
            id=item_id,
            pdf_path=pdf_path,
            title=title,
            pages=pages,
            collapsed=False,
            position=item_id,
            added_at="2026-07-11T00:00:00.000Z",
            updated_at="2026-07-11T00:00:00.000Z",
        )

    def test_aggregates_token_estimate_instead_of_summing_per_item_ceils(self) -> None:
        # other_chars=1 は単独だと ceil で 1トークンだが、集約してから丸めるため
        # 0.25+0.25=0.5 -> 1トークンになる（個別ceilの合計=2とは異なる）
        stats = [
            ItemStats(item=self._item(1), page_numbers=[1], stats=TextStats(cjk_chars=0, other_chars=1), unindexed_pages=0, missing_pdf=False),
            ItemStats(item=self._item(2), page_numbers=[1], stats=TextStats(cjk_chars=0, other_chars=1), unindexed_pages=0, missing_pdf=False),
        ]
        payload = build_export_preview_payload(stats)
        self.assertEqual(payload["estimated_tokens"], 1)
        self.assertEqual(payload["estimated_chars"], 2)
        self.assertEqual(payload["estimation"], "approximate")
        self.assertEqual(payload["estimator"], "char-class-v1")

    def test_empty_list_returns_empty_pack_warning(self) -> None:
        payload = build_export_preview_payload([])
        self.assertEqual(
            payload["warnings"],
            [{"code": "empty_pack", "item_id": None, "message": "この資料には資料項目がありません"}],
        )
        self.assertEqual(payload["book_count"], 0)
        self.assertEqual(payload["item_count"], 0)

    def test_duplicate_pdf_path_counts_as_one_book(self) -> None:
        stats = [
            ItemStats(item=self._item(1, pages="1-3"), page_numbers=[1, 2, 3], stats=TextStats(0, 0), unindexed_pages=0, missing_pdf=False),
            ItemStats(item=self._item(2, pages="8-10"), page_numbers=[8, 9, 10], stats=TextStats(0, 0), unindexed_pages=0, missing_pdf=False),
        ]
        payload = build_export_preview_payload(stats)
        self.assertEqual(payload["item_count"], 2)
        self.assertEqual(payload["book_count"], 1)
        self.assertEqual(payload["total_pages"], 6)

    def test_missing_pdf_takes_priority_over_missing_pages_warning(self) -> None:
        stats = [
            ItemStats(item=self._item(1, pages=""), page_numbers=[], stats=TextStats(0, 0), unindexed_pages=0, missing_pdf=True),
        ]
        warnings = build_export_preview_warnings(stats)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["code"], "missing_pdf")


class BuildExportPreviewPayloadForProfileTest(unittest.TestCase):
    """C-4: standard以外（chat等）向け拡張プレビューを組み立てる純粋関数のテスト。

    DB/PDFを介さず ItemStats を直接組み立てるため、chunks構造・警告の合流
    ロジックだけを高速に検証できる（B-2以降のテスト方針を踏襲）。
    """

    def _item(self, item_id: int, *, pdf_path: str = "a.pdf", pages: str = "1-2", title: str = "本") -> PackItemRecord:
        return PackItemRecord(
            id=item_id,
            pdf_path=pdf_path,
            title=title,
            pages=pages,
            collapsed=False,
            position=item_id,
            added_at="2026-07-11T00:00:00.000Z",
            updated_at="2026-07-11T00:00:00.000Z",
        )

    def test_empty_list_returns_profile_name_and_zero_counts(self) -> None:
        from tsundokensaku.export_profiles import ChatProfile

        payload = build_export_preview_payload_for_profile([], ChatProfile(), pack_name="資料")

        self.assertEqual(payload["profile"], "chat")
        self.assertEqual(payload["book_count"], 0)
        self.assertEqual(payload["item_count"], 0)
        self.assertEqual(payload["file_count"], 0)
        self.assertEqual(payload["archive"], "zip")
        self.assertEqual(payload["chunks"], [])
        self.assertEqual(
            payload["warnings"],
            [{"code": "empty_pack", "item_id": None, "message": "この資料には資料項目がありません"}],
        )

    def test_single_chunk_lists_items_with_per_item_token_estimates(self) -> None:
        from tsundokensaku.export_profiles import ChatProfile

        item_stats = [
            ItemStats(
                item=self._item(1, pdf_path="a.pdf", pages="1-2", title="本A"),
                page_numbers=[1, 2], stats=TextStats(cjk_chars=10, other_chars=0),
                unindexed_pages=0, missing_pdf=False,
            ),
            ItemStats(
                item=self._item(2, pdf_path="b.pdf", pages="5", title="本B"),
                page_numbers=[5], stats=TextStats(cjk_chars=20, other_chars=0),
                unindexed_pages=0, missing_pdf=False,
            ),
        ]
        payload = build_export_preview_payload_for_profile(item_stats, ChatProfile(), pack_name="資料")

        self.assertEqual(payload["profile"], "chat")
        self.assertEqual(payload["file_count"], 1)
        self.assertEqual(len(payload["chunks"]), 1)
        chunk = payload["chunks"][0]
        self.assertEqual(chunk["filename"], "資料_chat_01.md")
        self.assertEqual(chunk["pages"], 3)
        self.assertEqual(len(chunk["items"]), 2)
        self.assertEqual(chunk["items"][0]["item_id"], 1)
        self.assertEqual(chunk["items"][0]["title"], "本A")
        self.assertEqual(chunk["items"][0]["pdf_path"], "a.pdf")
        self.assertEqual(chunk["items"][0]["pages"], "1-2")
        self.assertEqual(chunk["items"][0]["estimated_tokens"], 10)
        self.assertIsNone(chunk["items"][0]["label"])
        self.assertEqual(chunk["items"][0]["fragment_index"], 1)
        self.assertEqual(chunk["items"][0]["fragment_count"], 1)
        self.assertEqual(chunk["items"][1]["item_id"], 2)
        self.assertEqual(chunk["items"][1]["title"], "本B")
        self.assertEqual(chunk["items"][1]["pdf_path"], "b.pdf")
        self.assertEqual(chunk["items"][1]["pages"], "5")
        self.assertEqual(chunk["items"][1]["estimated_tokens"], 20)
        self.assertEqual(payload["warnings"], [])

    def test_item_exceeding_limit_produces_plan_warning(self) -> None:
        from tsundokensaku.export_profiles import ChatProfile

        item_stats = [
            ItemStats(
                item=self._item(1, title="巨大本"),
                page_numbers=[1], stats=TextStats(cjk_chars=90_000, other_chars=0),
                unindexed_pages=0, missing_pdf=False,
            ),
        ]
        payload = build_export_preview_payload_for_profile(item_stats, ChatProfile(), pack_name="資料")

        # 切り捨てず単独チャンクとして残る
        self.assertEqual(len(payload["chunks"]), 1)
        self.assertEqual(
            payload["warnings"],
            [{"code": "item_exceeds_limit", "item_id": 1, "message": "「巨大本」は1ファイルの上限を超えるため単独で出力します"}],
        )

    def test_combines_item_warnings_and_plan_warnings(self) -> None:
        from tsundokensaku.export_profiles import ChatProfile

        item_stats = [
            ItemStats(
                item=self._item(1, title="未インデックス本"),
                page_numbers=[1, 2], stats=TextStats(cjk_chars=0, other_chars=0),
                unindexed_pages=2, missing_pdf=False,
            ),
        ]
        payload = build_export_preview_payload_for_profile(item_stats, ChatProfile(), pack_name="資料")

        codes = [warning["code"] for warning in payload["warnings"]]
        self.assertIn("unindexed_pages", codes)


class PackExportPreviewTest(unittest.TestCase):
    def _payload(self, response) -> dict:
        return json.loads(response.body)

    def test_preview_returns_404_for_missing_pack(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            with patch("tsundokensaku.web.get_db_path", return_value=db_path):
                with self.assertRaises(HTTPException) as ctx:
                    api_preview_pack_export(9999)
                self.assertEqual(ctx.exception.status_code, 404)

    def test_preview_returns_empty_pack_warning_for_pack_with_no_items(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            with patch("tsundokensaku.web.get_db_path", return_value=db_path):
                created = self._payload(api_create_pack({"name": "空の資料"}))
                preview = self._payload(api_preview_pack_export(created["id"]))

                self.assertEqual(preview["book_count"], 0)
                self.assertEqual(preview["item_count"], 0)
                self.assertEqual(preview["total_pages"], 0)
                self.assertEqual(preview["estimated_chars"], 0)
                self.assertEqual(preview["estimated_tokens"], 0)
                self.assertEqual(
                    preview["warnings"],
                    [{"code": "empty_pack", "item_id": None, "message": "この資料には資料項目がありません"}],
                )

    def test_preview_returns_estimation_for_indexed_pack(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"
            books_dir.mkdir(parents=True)
            pdf_path = books_dir / "a.pdf"

            writer = PdfWriter()
            for _ in range(3):
                writer.add_blank_page(width=72, height=72)
            with pdf_path.open("wb") as handle:
                writer.write(handle)

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
            ):
                from tsundokensaku.database import PageRecord, replace_pages

                connection = connect(db_path)
                initialize(connection)
                book_id = upsert_book(
                    connection,
                    path=pdf_path,
                    title="本A",
                    size_bytes=pdf_path.stat().st_size,
                    modified_at=pdf_path.stat().st_mtime,
                )
                replace_pages(
                    connection,
                    book_id=book_id,
                    title="本A",
                    pages=[
                        PageRecord(page_number=1, text="はじめに"),
                        PageRecord(page_number=2, text="Chapter 1"),
                        PageRecord(page_number=3, text="おわりに"),
                    ],
                )
                connection.commit()
                connection.close()

                created = self._payload(api_create_pack({"name": "資料"}))
                items = [{"pdf_path": "a.pdf", "title": "本A", "pages": "1-3", "collapsed": False, "position": 0}]
                self._payload(api_replace_pack_items(created["id"], {"items": items}))

                preview = self._payload(api_preview_pack_export(created["id"]))

                self.assertEqual(preview["book_count"], 1)
                self.assertEqual(preview["item_count"], 1)
                self.assertEqual(preview["total_pages"], 3)
                self.assertGreater(preview["estimated_chars"], 0)
                self.assertGreater(preview["estimated_tokens"], 0)
                self.assertEqual(preview["warnings"], [])

    def test_preview_flags_missing_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"
            books_dir.mkdir(parents=True)

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
            ):
                created = self._payload(api_create_pack({"name": "資料"}))
                items = [{"pdf_path": "missing.pdf", "title": "消えた本", "pages": "1-3", "collapsed": False, "position": 0}]
                self._payload(api_replace_pack_items(created["id"], {"items": items}))

                preview = self._payload(api_preview_pack_export(created["id"]))

                self.assertEqual(preview["total_pages"], 0)
                self.assertEqual(len(preview["warnings"]), 1)
                warning = preview["warnings"][0]
                self.assertEqual(warning["code"], "missing_pdf")
                self.assertIn("消えた本", warning["message"])
                self.assertIsInstance(warning["item_id"], int)

    def test_preview_flags_missing_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"
            books_dir.mkdir(parents=True)
            writer = PdfWriter()
            writer.add_blank_page(width=72, height=72)
            with (books_dir / "a.pdf").open("wb") as handle:
                writer.write(handle)

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
            ):
                created = self._payload(api_create_pack({"name": "資料"}))
                items = [{"pdf_path": "a.pdf", "title": "本A", "pages": "", "collapsed": False, "position": 0}]
                self._payload(api_replace_pack_items(created["id"], {"items": items}))

                preview = self._payload(api_preview_pack_export(created["id"]))

                self.assertEqual(len(preview["warnings"]), 1)
                self.assertEqual(preview["warnings"][0]["code"], "missing_pages")

    def test_preview_flags_unindexed_pages_and_still_counts_pages(self) -> None:
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

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
            ):
                created = self._payload(api_create_pack({"name": "資料"}))
                items = [{"pdf_path": "a.pdf", "title": "本A", "pages": "1-3", "collapsed": False, "position": 0}]
                self._payload(api_replace_pack_items(created["id"], {"items": items}))

                # books テーブルに未登録（一度もインデックスしていない）ケース
                preview = self._payload(api_preview_pack_export(created["id"]))

                self.assertEqual(preview["total_pages"], 3)
                self.assertEqual(preview["estimated_chars"], 0)
                self.assertEqual(len(preview["warnings"]), 1)
                self.assertEqual(preview["warnings"][0]["code"], "unindexed_pages")
                self.assertIn("3ページ分", preview["warnings"][0]["message"])

    def test_preview_counts_duplicate_pdf_items_as_one_book(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"
            books_dir.mkdir(parents=True)
            writer = PdfWriter()
            for _ in range(10):
                writer.add_blank_page(width=72, height=72)
            with (books_dir / "a.pdf").open("wb") as handle:
                writer.write(handle)

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
            ):
                created = self._payload(api_create_pack({"name": "資料"}))
                items = [
                    {"pdf_path": "a.pdf", "title": "本A前半", "pages": "1-3", "collapsed": False, "position": 0},
                    {"pdf_path": "a.pdf", "title": "本A後半", "pages": "8-10", "collapsed": False, "position": 1},
                ]
                self._payload(api_replace_pack_items(created["id"], {"items": items}))

                preview = self._payload(api_preview_pack_export(created["id"]))

                self.assertEqual(preview["item_count"], 2)
                self.assertEqual(preview["book_count"], 1)
                self.assertEqual(preview["total_pages"], 6)

    def test_preview_profile_unspecified_and_standard_are_byte_identical(self) -> None:
        # C-4完了条件: profile未指定 と profile=standard は完全に同一レスポンス
        # （chunks/file_count/archive/profile 等の拡張フィールドを含まない）
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            with patch("tsundokensaku.web.get_db_path", return_value=db_path):
                created = self._payload(api_create_pack({"name": "資料"}))

                unspecified = self._payload(api_preview_pack_export(created["id"]))
                standard = self._payload(api_preview_pack_export(created["id"], profile="standard"))

                self.assertEqual(unspecified, standard)
                for key in ("profile", "chunks", "file_count", "archive"):
                    self.assertNotIn(key, unspecified)

    def test_preview_unknown_profile_returns_400_matching_export_api(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            with patch("tsundokensaku.web.get_db_path", return_value=db_path):
                created = self._payload(api_create_pack({"name": "資料"}))
                with self.assertRaises(HTTPException) as ctx:
                    api_preview_pack_export(created["id"], profile="unknown")
                self.assertEqual(ctx.exception.status_code, 400)
                self.assertEqual(ctx.exception.detail, "不明なエクスポートプロファイルです: unknown")

    def test_preview_profile_chapter_returns_empty_pack_with_extended_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            with patch("tsundokensaku.web.get_db_path", return_value=db_path):
                created = self._payload(api_create_pack({"name": "資料"}))
                preview = self._payload(api_preview_pack_export(created["id"], profile="chapter"))
                self.assertEqual(preview["profile"], "chapter")
                self.assertEqual(preview["file_count"], 0)
                self.assertEqual(preview["archive"], "zip")
                self.assertEqual(preview["chunks"], [])
                self.assertEqual(
                    preview["warnings"],
                    [{"code": "empty_pack", "item_id": None, "message": "この資料には資料項目がありません"}],
                )

    def test_preview_unknown_profile_checked_before_pack_lookup(self) -> None:
        # エクスポートAPIと同じ検証順序（profile解決が先）。存在しないpack_idでも
        # 先にprofile不明の400が返る
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            with patch("tsundokensaku.web.get_db_path", return_value=db_path):
                with self.assertRaises(HTTPException) as ctx:
                    api_preview_pack_export(9999, profile="unknown")
                self.assertEqual(ctx.exception.status_code, 400)
                self.assertEqual(ctx.exception.detail, "不明なエクスポートプロファイルです: unknown")

    def test_preview_missing_pack_returns_404_with_profile_specified(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            with patch("tsundokensaku.web.get_db_path", return_value=db_path):
                with self.assertRaises(HTTPException) as ctx:
                    api_preview_pack_export(9999, profile="chat")
                self.assertEqual(ctx.exception.status_code, 404)

    def test_preview_profile_chat_returns_empty_pack_with_extended_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            with patch("tsundokensaku.web.get_db_path", return_value=db_path):
                created = self._payload(api_create_pack({"name": "空の資料"}))
                preview = self._payload(api_preview_pack_export(created["id"], profile="chat"))

                self.assertEqual(preview["profile"], "chat")
                self.assertEqual(preview["file_count"], 0)
                self.assertEqual(preview["archive"], "zip")
                self.assertEqual(preview["chunks"], [])
                self.assertEqual(
                    preview["warnings"],
                    [{"code": "empty_pack", "item_id": None, "message": "この資料には資料項目がありません"}],
                )

    def test_preview_profile_chat_combines_items_into_chunks(self) -> None:
        from tsundokensaku.database import PageRecord, replace_pages

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"
            books_dir.mkdir(parents=True)
            pdf_path_a = books_dir / "a.pdf"
            pdf_path_b = books_dir / "b.pdf"
            for path in (pdf_path_a, pdf_path_b):
                writer = PdfWriter()
                for _ in range(2):
                    writer.add_blank_page(width=72, height=72)
                with path.open("wb") as handle:
                    writer.write(handle)

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
            ):
                connection = connect(db_path)
                initialize(connection)
                book_id_a = upsert_book(
                    connection, path=pdf_path_a, title="本A",
                    size_bytes=pdf_path_a.stat().st_size, modified_at=pdf_path_a.stat().st_mtime,
                )
                book_id_b = upsert_book(
                    connection, path=pdf_path_b, title="本B",
                    size_bytes=pdf_path_b.stat().st_size, modified_at=pdf_path_b.stat().st_mtime,
                )
                replace_pages(connection, book_id=book_id_a, title="本A", pages=[
                    PageRecord(page_number=1, text="本Aの1ページ目"),
                    PageRecord(page_number=2, text="本Aの2ページ目"),
                ])
                replace_pages(connection, book_id=book_id_b, title="本B", pages=[
                    PageRecord(page_number=1, text="本Bの1ページ目"),
                    PageRecord(page_number=2, text="本Bの2ページ目"),
                ])
                connection.commit()
                connection.close()

                created = self._payload(api_create_pack({"name": "対比資料"}))
                items = [
                    {"pdf_path": "a.pdf", "title": "本A", "pages": "1-2", "collapsed": False, "position": 0},
                    {"pdf_path": "b.pdf", "title": "本B", "pages": "1-2", "collapsed": False, "position": 1},
                ]
                self._payload(api_replace_pack_items(created["id"], {"items": items}))

                preview = self._payload(api_preview_pack_export(created["id"], profile="chat"))

                self.assertEqual(preview["profile"], "chat")
                self.assertEqual(preview["file_count"], 1)
                self.assertEqual(len(preview["chunks"]), 1)
                chunk = preview["chunks"][0]
                self.assertEqual(chunk["filename"], "対比資料_chat_01.md")
                self.assertEqual(len(chunk["items"]), 2)
                self.assertEqual(chunk["items"][0]["title"], "本A")
                self.assertEqual(chunk["items"][1]["title"], "本B")
                self.assertEqual(preview["warnings"], [])

                # プレビューが示した分冊結果は実エクスポートと一致する
                export_response = api_export_pack(created["id"], profile="chat")
                with zipfile.ZipFile(BytesIO(export_response.body)) as archive:
                    self.assertEqual(
                        [name for name in archive.namelist() if name != "manifest.md"],
                        [chunk["filename"]],
                    )

    def test_preview_profile_chapter_splits_by_chapters_with_labels(self) -> None:
        import fitz
        from tsundokensaku.database import PageRecord, replace_pages

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"
            books_dir.mkdir(parents=True)
            pdf_path = books_dir / "a.pdf"

            doc = fitz.open()
            for _ in range(6):
                doc.new_page(width=72, height=72)
            doc.set_toc([[1, "第1章", 1], [1, "第2章", 4]])
            doc.save(str(pdf_path))
            doc.close()

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
                patch.dict(os.environ, {"TSUNDOKENSAKU_CHAPTER_MAX_PAGES_PER_FILE": "4"}),
            ):
                connection = connect(db_path)
                initialize(connection)
                book_id = upsert_book(
                    connection,
                    path=pdf_path,
                    title="本A",
                    size_bytes=pdf_path.stat().st_size,
                    modified_at=pdf_path.stat().st_mtime,
                )
                replace_pages(connection, book_id=book_id, title="本A", pages=[
                    PageRecord(page_number=1, text="1"),
                    PageRecord(page_number=2, text="2"),
                    PageRecord(page_number=3, text="3"),
                    PageRecord(page_number=4, text="4"),
                    PageRecord(page_number=5, text="5"),
                    PageRecord(page_number=6, text="6"),
                ])
                connection.commit()
                connection.close()

                created = self._payload(api_create_pack({"name": "資料"}))
                self._payload(api_replace_pack_items(created["id"], {"items": [
                    {"pdf_path": "a.pdf", "title": "本A", "pages": "1-6", "collapsed": False, "position": 0},
                ]}))

                preview = self._payload(api_preview_pack_export(created["id"], profile="chapter"))

                self.assertEqual(preview["profile"], "chapter")
                self.assertEqual(preview["file_count"], 2)
                self.assertEqual(
                    [chunk["filename"] for chunk in preview["chunks"]],
                    ["01_本A_第1章_p1-4.pdf", "02_本A_第2章_p4-6.pdf"],
                )
                self.assertEqual(preview["chunks"][0]["items"][0]["label"], "第1章")
                self.assertEqual(preview["chunks"][1]["items"][0]["label"], "第2章")
                self.assertEqual(preview["chunks"][0]["items"][0]["pages"], "1-4")
                codes = [warning["code"] for warning in preview["warnings"]]
                self.assertIn("item_split_by_chapters", codes)

                export_response = api_export_pack(created["id"], profile="chapter")
                with zipfile.ZipFile(BytesIO(export_response.body)) as archive:
                    self.assertEqual(
                        [name for name in archive.namelist() if name != "manifest.md"],
                        [chunk["filename"] for chunk in preview["chunks"]],
                    )

    def test_preview_profile_chapter_falls_back_without_outline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"
            books_dir.mkdir(parents=True)
            pdf_path = books_dir / "a.pdf"

            writer = PdfWriter()
            for _ in range(5):
                writer.add_blank_page(width=72, height=72)
            with pdf_path.open("wb") as handle:
                writer.write(handle)

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
                patch.dict(os.environ, {"TSUNDOKENSAKU_CHAPTER_MAX_PAGES_PER_FILE": "2"}),
            ):
                created = self._payload(api_create_pack({"name": "資料"}))
                self._payload(api_replace_pack_items(created["id"], {"items": [
                    {"pdf_path": "a.pdf", "title": "本A", "pages": "1-5", "collapsed": False, "position": 0},
                ]}))

                preview = self._payload(api_preview_pack_export(created["id"], profile="chapter"))

                self.assertEqual(preview["file_count"], 3)
                self.assertEqual(preview["chunks"][0]["items"][0]["label"], "part1")
                self.assertEqual(preview["chunks"][1]["items"][0]["label"], "part2")
                codes = [warning["code"] for warning in preview["warnings"]]
                self.assertIn("no_outline_fallback", codes)


class _FakeUploadRequest:
    """request.body() だけを使う upload エンドポイント用の最小スタブ。"""

    def __init__(self, body: bytes) -> None:
        self._body = body

    async def body(self) -> bytes:
        return self._body


class DemoModeUploadTest(unittest.TestCase):
    def test_is_demo_mode_reads_env_var_case_insensitively(self) -> None:
        with patch.dict(os.environ, {"DEMO_MODE": "True"}):
            self.assertTrue(is_demo_mode())
        with patch.dict(os.environ, {"DEMO_MODE": "false"}):
            self.assertFalse(is_demo_mode())
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(is_demo_mode())

    def test_pdf_upload_returns_403_in_demo_mode(self) -> None:
        with patch.dict(os.environ, {"DEMO_MODE": "true"}):
            # DEMO_MODE チェックは request.body() を読む前に早期returnするため、
            # request には未使用のダミーを渡すだけでよい
            response = asyncio.run(upload_pdf(request=None, filename="a.pdf"))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.body, b"Upload is disabled in demo mode.")

    def test_scrapbox_upload_returns_403_in_demo_mode(self) -> None:
        with patch.dict(os.environ, {"DEMO_MODE": "true"}):
            response = asyncio.run(upload_scrapbox_json(request=None, filename="a.json"))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.body, b"Upload is disabled in demo mode.")

    def test_pdf_upload_succeeds_when_demo_mode_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            books_dir = Path(temp_dir) / "books"
            books_dir.mkdir()
            with patch("tsundokensaku.web.get_books_dir", return_value=books_dir), \
                    patch.dict(os.environ, {"DEMO_MODE": "false"}):
                request = _FakeUploadRequest(b"%PDF-1.4 dummy")
                response = asyncio.run(upload_pdf(request=request, filename="sample.pdf"))
            self.assertEqual(response.status_code, 201)
            self.assertTrue((books_dir / "sample.pdf").exists())

    def test_pdf_import_directory_blocked_in_demo_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_dir = Path(temp_dir) / "source"
            source_dir.mkdir()
            (source_dir / "a.pdf").write_bytes(b"%PDF-1.4 dummy")
            books_dir = Path(temp_dir) / "books"
            books_dir.mkdir()
            with patch("tsundokensaku.web.get_books_dir", return_value=books_dir), \
                    patch.dict(os.environ, {"DEMO_MODE": "true"}):
                response = import_pdf_directory(source_dir=str(source_dir))
            self.assertEqual(response.status_code, 303)
            self.assertIn("デモモードのため無効です", unquote(response.headers["location"]))
            # 取り込み処理自体が実行されていないこと（コピーされていない）を確認
            self.assertFalse((books_dir / "a.pdf").exists())

    def test_scrapbox_import_blocked_in_demo_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            with patch("tsundokensaku.web.get_db_path", return_value=db_path), \
                    patch.dict(os.environ, {"DEMO_MODE": "true"}):
                response = import_scrapbox_json(export_json_path="")
            self.assertEqual(response.status_code, 303)
            self.assertIn("デモモードのため無効です", unquote(response.headers["location"]))
            # DB接続すら発生していないこと（db_path のファイルが作られない）を確認
            self.assertFalse(db_path.exists())

    def test_update_pdf_export_save_dir_blocked_in_demo_mode(self) -> None:
        # update_env_setting はデフォルト引数で実 .env のパスを束縛しているため、
        # ここではパスをpatchせず関数呼び出し自体が起きないことで安全に検証する
        with patch("tsundokensaku.web.update_env_setting") as mock_update_env, \
                patch.dict(os.environ, {"DEMO_MODE": "true"}):
            response = update_pdf_export_save_dir(save_dir="/tmp/somewhere")
        self.assertEqual(response.status_code, 303)
        self.assertIn("デモモードのため無効です", unquote(response.headers["location"]))
        mock_update_env.assert_not_called()



class ExportEventRecordingTest(unittest.TestCase):
    """C-6: export_events テーブルへの記録（設計書 export-events-design.md §10）。"""

    def _make_pdf(self, path: Path, page_count: int = 2) -> None:
        writer = PdfWriter()
        for i in range(page_count):
            writer.add_blank_page(width=72, height=100 + i + 1)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            writer.write(handle)

    def _payload(self, response):
        return json.loads(response.body)

    def _count_events(self, db_path: Path) -> int:
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        try:
            return conn.execute("SELECT COUNT(*) FROM export_events").fetchone()[0]
        finally:
            conn.close()

    def test_schema_is_idempotent(self) -> None:
        """ensure_pack_schema を2回呼んでもエラーにならない（冪等）。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            from tsundokensaku.database import connect as db_connect, ensure_pack_schema
            conn = db_connect(db_path)
            try:
                ensure_pack_schema(conn)
                ensure_pack_schema(conn)  # 2回目もエラーなし
            finally:
                conn.close()

    def test_successful_export_records_event(self) -> None:
        """エクスポート成功後に export_events へ1行増える（pdf format）。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"
            self._make_pdf(books_dir / "a.pdf")

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
            ):
                created = self._payload(api_create_pack({"name": "記録テスト資料"}))
                items = [{"pdf_path": "a.pdf", "title": "本A", "pages": "1-2", "collapsed": False, "position": 0}]
                self._payload(api_replace_pack_items(created["id"], {"items": items}))

                before = self._count_events(db_path)
                response = api_export_pack(created["id"], format="pdf")
                self.assertEqual(response.status_code, 200)
                after = self._count_events(db_path)

            self.assertEqual(after - before, 1)

    def test_successful_json_export_also_records(self) -> None:
        """json format でもエクスポートイベントが記録される（設計書 §4: 全format記録）。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
            ):
                created = self._payload(api_create_pack({"name": "JSON記録テスト"}))
                items = [{"pdf_path": "a.pdf", "title": "本A", "pages": "1", "collapsed": False, "position": 0}]
                self._payload(api_replace_pack_items(created["id"], {"items": items}))

                before = self._count_events(db_path)
                response = api_export_pack(created["id"], format="json")
                self.assertEqual(response.status_code, 200)
                after = self._count_events(db_path)

            self.assertEqual(after - before, 1)

    def test_failed_export_does_not_record(self) -> None:
        """空資料（400エラー）ではイベントが記録されない。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
            ):
                created = self._payload(api_create_pack({"name": "空資料"}))

                before = self._count_events(db_path)
                with self.assertRaises(HTTPException):
                    api_export_pack(created["id"], format="pdf")
                after = self._count_events(db_path)

            self.assertEqual(after, before)

    def test_record_failure_does_not_break_export(self) -> None:
        """record_export_event の失敗はエクスポートのレスポンスに影響しない（ベストエフォート）。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"
            self._make_pdf(books_dir / "a.pdf")

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
                patch("tsundokensaku.web.record_export_event", side_effect=RuntimeError("DB失敗")),
            ):
                created = self._payload(api_create_pack({"name": "エラー耐性テスト"}))
                items = [{"pdf_path": "a.pdf", "title": "本A", "pages": "1-2", "collapsed": False, "position": 0}]
                self._payload(api_replace_pack_items(created["id"], {"items": items}))

                # record_export_event が例外を出しても 200 が返ること
                response = api_export_pack(created["id"], format="pdf")
            self.assertEqual(response.status_code, 200)

    def test_profile_unspecified_records_as_standard(self) -> None:
        """profile 未指定でも export_events には 'standard' が記録される（設計書 §5）。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"
            self._make_pdf(books_dir / "a.pdf")

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
            ):
                created = self._payload(api_create_pack({"name": "standard記録テスト"}))
                items = [{"pdf_path": "a.pdf", "title": "本A", "pages": "1-2", "collapsed": False, "position": 0}]
                self._payload(api_replace_pack_items(created["id"], {"items": items}))
                api_export_pack(created["id"])  # profile 省略

            import sqlite3
            conn = sqlite3.connect(str(db_path))
            try:
                row = conn.execute("SELECT profile FROM export_events ORDER BY id DESC LIMIT 1").fetchone()
            finally:
                conn.close()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "standard")

    def test_items_json_schema_version_and_fields(self) -> None:
        """items_json に version:1 と 4フィールド（pdf_path/title/pages/position）が含まれる（設計書 §3）。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"
            self._make_pdf(books_dir / "a.pdf")

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
            ):
                created = self._payload(api_create_pack({"name": "スキーマテスト"}))
                items = [{"pdf_path": "a.pdf", "title": "本A", "pages": "1-2", "collapsed": False, "position": 3}]
                self._payload(api_replace_pack_items(created["id"], {"items": items}))
                api_export_pack(created["id"], format="pdf")

            import sqlite3
            conn = sqlite3.connect(str(db_path))
            try:
                row = conn.execute("SELECT items_json FROM export_events ORDER BY id DESC LIMIT 1").fetchone()
            finally:
                conn.close()
            payload = json.loads(row[0])
            self.assertEqual(payload["version"], 1)
            self.assertIn("items", payload)
            item = payload["items"][0]
            self.assertEqual(item["pdf_path"], "a.pdf")
            self.assertEqual(item["title"], "本A")
            self.assertEqual(item["pages"], "1-2")
            self.assertIn("position", item)

    def test_re_export_records_twice(self) -> None:
        """同一資料を2回エクスポートすると2行になる（去重しない。設計書 §8）。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"
            self._make_pdf(books_dir / "a.pdf")

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
            ):
                created = self._payload(api_create_pack({"name": "再エクスポートテスト"}))
                items = [{"pdf_path": "a.pdf", "title": "本A", "pages": "1-2", "collapsed": False, "position": 0}]
                self._payload(api_replace_pack_items(created["id"], {"items": items}))
                api_export_pack(created["id"], format="pdf")
                api_export_pack(created["id"], format="pdf")

            self.assertEqual(self._count_events(db_path), 2)


if __name__ == "__main__":
    unittest.main()
