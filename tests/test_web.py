import asyncio
import os
import tempfile
import unittest
import zipfile
from datetime import datetime
from io import BytesIO
from unittest.mock import patch
from pathlib import Path
from urllib.parse import quote, unquote
from zoneinfo import ZoneInfo
import json

from pypdf import PdfReader, PdfWriter

from fastapi import HTTPException
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

import tsundokensaku.web as web
import tsundokensaku.index_job as index_job
from tsundokensaku.pdf_export import PdfSourceNotFoundError
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
    build_scrapbox_page_url,
    build_search_result_rows,
    build_search_scrapbox_body,
    _now_jst,
    _scrapbox_page_label,
    _sanitize_scrapbox_title,
    _unique_destination_path,
    _unique_export_destination_path,
    export_markdown,
    export_pdf,
    finalize_search_result_rows,
    group_pdf_results,
    highlight_query,
    import_pdf_directory,
    import_scrapbox_json,
    pdf_outline,
    pdf_url,
    raw_pdf_url,
    format_indexed_at,
    get_books_dir,
    get_db_path,
    get_pdf_export_save_dir,
    is_demo_mode,
    normalize_search_group,
    normalize_search_match,
    pdf_thumbnails,
    resolve_pdf_path,
    resolve_pdf_scrapbox_url,
    search_pages,
    sort_results,
    update_env_setting,
    update_pdf_export_save_dir,
    upload_pdf,
    upload_scrapbox_json,
    pack_list_page,
    workspace_page,
)
from tsundokensaku.web import app as tsundokensaku_app
from tsundokensaku.database import PackItemRecord, SearchResult, connect, initialize, upsert_book
from tsundokensaku.export_service import PreparedArchiveExport, PreparedJsonExport
from tsundokensaku.export_stats import ItemStats
from tsundokensaku import scrapbox_import_service
from tsundokensaku.pdf_import_service import (
    PdfDirectoryBoundaryError,
    PdfDirectoryFilesystemError,
    PdfDirectoryImportResult,
    PdfDirectoryOverlapError,
    PdfDirectorySourceNotDirectoryError,
    PdfDirectorySourceNotFoundError,
)
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

    def test_pdf_import_route_preserves_query_redirect_and_success_message(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"
            source_dir = root / "source input"
            books_dir.mkdir()
            source_dir.mkdir()
            with patch("tsundokensaku.web.get_books_dir", return_value=books_dir), \
                    patch(
                        "tsundokensaku.web.pdf_import_service.import_pdfs_from_directory",
                        return_value=PdfDirectoryImportResult(copied=3, skipped=2, total=5),
                    ) as import_mock, \
                    patch.dict(os.environ, {"DEMO_MODE": "false"}):
                client = TestClient(tsundokensaku_app)
                response = client.get("/settings/pdf-import", params={"source_dir": str(source_dir)}, follow_redirects=False)

            self.assertEqual(response.status_code, 303)
            location = unquote(response.headers["location"])
            self.assertTrue(location.startswith("/settings?message="))
            message = location.split("message=", 1)[1]
            expected_parts = ("PDF を 3 件", str(books_dir), "スキップ 2 件", "5 件中", str(source_dir))
            positions = [message.index(part) for part in expected_parts]
            self.assertEqual(positions, sorted(positions))
            import_mock.assert_called_once_with(source_dir, books_dir)

    def test_pdf_import_route_requires_source_without_calling_import(self) -> None:
        with patch("tsundokensaku.web.pdf_import_service.import_pdfs_from_directory") as import_mock, \
                patch.dict(os.environ, {"DEMO_MODE": "false"}):
            client = TestClient(tsundokensaku_app)
            missing_response = client.get("/settings/pdf-import", follow_redirects=False)
            blank_response = client.get("/settings/pdf-import", params={"source_dir": "   "}, follow_redirects=False)

        for response in (missing_response, blank_response):
            self.assertEqual(response.status_code, 303)
            self.assertIn("PDF の取り込み元フォルダを指定してください", unquote(response.headers["location"]))
        import_mock.assert_not_called()

    def test_pdf_import_route_demo_mode_does_not_import_or_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "source"
            books_dir = root / "books"
            source_dir.mkdir()
            books_dir.mkdir()
            (source_dir / "a.pdf").write_bytes(b"%PDF-1.4 a")

            with patch("tsundokensaku.web.pdf_import_service.import_pdfs_from_directory") as import_mock, \
                    patch("tsundokensaku.web.index_job.start") as index_mock, \
                    patch.dict(os.environ, {"DEMO_MODE": "true"}):
                client = TestClient(tsundokensaku_app)
                response = client.get("/settings/pdf-import", params={"source_dir": str(source_dir)}, follow_redirects=False)

            self.assertEqual(response.status_code, 303)
            self.assertIn("デモモードのため無効です", unquote(response.headers["location"]))
            import_mock.assert_not_called()
            index_mock.assert_not_called()
            self.assertFalse((books_dir / "a.pdf").exists())

    def test_pdf_import_route_maps_service_errors_to_safe_messages(self) -> None:
        cases = (
            (PdfDirectorySourceNotFoundError("/secret/source"), "入力元フォルダが見つかりません"),
            (PdfDirectorySourceNotDirectoryError("/secret/file.pdf"), "入力元がフォルダではありません"),
            (PdfDirectoryOverlapError("/secret/overlap"), "入力元と保存先には重ならないフォルダを指定してください"),
            (PdfDirectoryBoundaryError("/secret/link"), "安全でないパスが含まれているため取り込めません"),
            (PdfDirectoryFilesystemError("/secret/oserror"), "ファイルの読み取りまたはコピーに失敗しました"),
        )

        for exc, expected_message in cases:
            with self.subTest(exc=type(exc).__name__):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    books_dir = root / "books"
                    source_dir = root / "source"
                    books_dir.mkdir()
                    source_dir.mkdir()
                    with patch("tsundokensaku.web.get_books_dir", return_value=books_dir), \
                            patch("tsundokensaku.web.pdf_import_service.import_pdfs_from_directory", side_effect=exc), \
                            patch("tsundokensaku.web.LOGGER.exception") as log_mock, \
                            patch.dict(os.environ, {"DEMO_MODE": "false"}):
                        response = import_pdf_directory(source_dir=str(source_dir))

                self.assertEqual(response.status_code, 303)
                location = unquote(response.headers["location"])
                self.assertIn(expected_message, location)
                self.assertNotIn("/secret", location)
                self.assertNotIn("oserror", location)
                log_mock.assert_called_once()

    def test_pdf_import_route_maps_unexpected_error_to_safe_message(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"
            source_dir = root / "source"
            books_dir.mkdir()
            source_dir.mkdir()
            with patch("tsundokensaku.web.get_books_dir", return_value=books_dir), \
                    patch(
                        "tsundokensaku.web.pdf_import_service.import_pdfs_from_directory",
                        side_effect=RuntimeError("/secret/path permission denied"),
                    ), \
                    patch("tsundokensaku.web.LOGGER.exception") as log_mock, \
                    patch.dict(os.environ, {"DEMO_MODE": "false"}):
                response = import_pdf_directory(source_dir=str(source_dir))

        self.assertEqual(response.status_code, 303)
        location = unquote(response.headers["location"])
        self.assertIn("予期しないエラーが発生しました", location)
        self.assertNotIn("/secret/path", location)
        self.assertNotIn("permission denied", location)
        log_mock.assert_called_once()

    def test_format_indexed_at_renders_jst(self) -> None:
        self.assertEqual(format_indexed_at("2026-06-29T03:55:59.999358+00:00"), "2026/06/29 12:55")

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

    def test_pdf_http_routes_keep_required_query_validation(self) -> None:
        client = TestClient(tsundokensaku_app)

        cases = [
            ("GET", "/pdf-outline"),
            ("GET", "/pdf-thumbnails?pdf_path=sample.pdf"),
            ("GET", "/export-pdf?pdf_path=sample.pdf"),
            ("GET", "/export-md?pdf_path=sample.pdf"),
            ("POST", "/export-pdf/save?pdf_path=sample.pdf"),
            ("GET", "/view/sample.pdf?page=abc"),
        ]
        for method, url in cases:
            with self.subTest(method=method, url=url):
                response = client.request(method, url)
                self.assertEqual(response.status_code, 422)

    def test_pdf_http_routes_return_404_for_missing_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"
            save_dir = root / "exports"
            books_dir.mkdir()
            save_dir.mkdir()

            with (
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
                patch("tsundokensaku.web.get_db_path", return_value=root / "index.db"),
                patch("tsundokensaku.web.get_pdf_export_save_dir", return_value=save_dir),
            ):
                client = TestClient(tsundokensaku_app)
                cases = [
                    ("GET", "/pdf/missing.pdf"),
                    ("GET", "/pdf-outline?pdf_path=missing.pdf"),
                    ("GET", "/pdf-thumbnails?pdf_path=missing.pdf&pages=1"),
                    ("GET", "/export-pdf?pdf_path=missing.pdf&pages=1"),
                    ("GET", "/search-pages?pdf_path=missing.pdf&q=query"),
                    ("GET", "/export-md?pdf_path=missing.pdf&pages=1"),
                    ("POST", "/export-pdf/save?pdf_path=missing.pdf&pages=1"),
                    ("GET", "/view/missing.pdf"),
                ]
                for method, url in cases:
                    with self.subTest(method=method, url=url):
                        response = client.request(method, url)
                        self.assertEqual(response.status_code, 404)
                        self.assertEqual(response.json()["detail"], "PDF not found")

    def test_pdf_http_routes_keep_empty_pages_and_invalid_pages_details(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"
            save_dir = root / "exports"
            books_dir.mkdir()
            save_dir.mkdir()
            pdf_path = books_dir / "sample.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=72, height=72)
            with pdf_path.open("wb") as handle:
                writer.write(handle)

            with (
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
                patch("tsundokensaku.web.get_db_path", return_value=root / "index.db"),
                patch("tsundokensaku.web.get_pdf_export_save_dir", return_value=save_dir),
            ):
                client = TestClient(tsundokensaku_app)
                empty_pages_cases = [
                    ("GET", "/pdf-thumbnails?pdf_path=sample.pdf&pages=%20%20"),
                    ("GET", "/export-pdf?pdf_path=sample.pdf&pages=%20%20"),
                    ("GET", "/export-md?pdf_path=sample.pdf&pages=%20%20"),
                    ("POST", "/export-pdf/save?pdf_path=sample.pdf&pages=%20%20"),
                ]
                for method, url in empty_pages_cases:
                    with self.subTest(method=method, url=url):
                        response = client.request(method, url)
                        self.assertEqual(response.status_code, 400)
                        self.assertEqual(response.json()["detail"], "pages is required")

                invalid_cases = [
                    ("GET", "/export-pdf?pdf_path=sample.pdf&pages=2", "Page number out of range: 2 (1-1)"),
                    ("GET", "/export-md?pdf_path=sample.pdf&pages=2", "Page number out of range: 2 (1-1)"),
                    ("POST", "/export-pdf/save?pdf_path=sample.pdf&pages=2", "Page number out of range: 2 (1-1)"),
                    ("GET", "/pdf-thumbnails?pdf_path=sample.pdf&pages=2&size=detail", "page not found"),
                ]
                for method, url, detail in invalid_cases:
                    with self.subTest(method=method, url=url):
                        response = client.request(method, url)
                        self.assertEqual(response.json()["detail"], detail)
                        self.assertIn(response.status_code, {400, 404})

    def test_search_pages_empty_query_returns_before_pdf_or_db_resolution(self) -> None:
        with (
            patch("tsundokensaku.web.get_books_dir", side_effect=AssertionError("books dir must not be read")),
            patch("tsundokensaku.web.get_db_path", side_effect=AssertionError("db path must not be read")),
        ):
            client = TestClient(tsundokensaku_app)
            response = client.get("/search-pages", params={"pdf_path": "missing.pdf", "q": "  "})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"indexed": True, "pages": []})

    def test_export_downloads_keep_media_type_and_utf8_filename(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"
            books_dir.mkdir()
            db_path = root / "index.db"
            pdf_path = books_dir / "日本語の本.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=72, height=72)
            with pdf_path.open("wb") as handle:
                writer.write(handle)

            with (
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
            ):
                client = TestClient(tsundokensaku_app)
                pdf_response = client.get("/export-pdf", params={"pdf_path": "日本語の本.pdf", "pages": "1"})
                md_response = client.get("/export-md", params={"pdf_path": "日本語の本.pdf", "pages": "1"})

        self.assertEqual(pdf_response.status_code, 200)
        self.assertEqual(pdf_response.headers["content-type"], "application/pdf")
        self.assertIn("filename*=UTF-8''", pdf_response.headers["content-disposition"])
        self.assertIn(quote("日本語の本_p1.pdf"), pdf_response.headers["content-disposition"])
        self.assertEqual(md_response.status_code, 200)
        self.assertIn("text/markdown", md_response.headers["content-type"])
        self.assertIn("filename*=UTF-8''", md_response.headers["content-disposition"])
        self.assertIn(quote("日本語の本_p1.md"), md_response.headers["content-disposition"])

    def test_save_export_pdf_http_success_returns_saved_path_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"
            save_dir = root / "exports"
            expected_path = save_dir / "sample_p1-2.pdf"
            books_dir.mkdir()
            save_dir.mkdir()

            with (
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
                patch("tsundokensaku.web.get_pdf_export_save_dir", return_value=save_dir),
                patch(
                    "tsundokensaku.web.pdf_export_service.save_pdf_export_to_configured_dir",
                    return_value=expected_path,
                ) as save_mock,
            ):
                client = TestClient(tsundokensaku_app)
                response = client.post("/export-pdf/save", params={"pdf_path": "sample.pdf", "pages": "1-2"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"saved_path": str(expected_path)})
        self.assertNotIn("path", response.json())
        self.assertNotIn("file", response.json())
        save_mock.assert_called_once_with(
            "sample.pdf",
            "1-2",
            books_dir=books_dir,
            save_dir=save_dir,
        )

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
        self.assertIn("資料机", body)
        self.assertIn('class="card ws-controls-card"', body)
        self.assertIn('.ws-controls-card { position: relative; z-index: 1; overflow: visible; }', body)
        self.assertIn('id="ws-management"', body)
        self.assertEqual(body.count('id="ws-management"'), 1)
        self.assertIn("⋯ 資料管理", body)
        self.assertIn("新しい資料", body)
        self.assertIn("名前を変更", body)
        self.assertIn("バックアップ", body)
        self.assertIn("危険な操作", body)
        self.assertIn("この資料を空にする", body)
        self.assertNotIn('id="ws-pack-delete"', body)
        self.assertIn('id="ws-count"', body)
        self.assertIn('id="ws-export-preview"', body)
        self.assertIn('id="ws-pack-list-link" href="/packs">資料一覧へ', body)
        self.assertIn('id="ws-add-open">資料内からページを追加', body)
        self.assertIn('id="ws-add-title">追加するページを選ぶ', body)
        self.assertIn('id="ws-add-open-pdf" disabled>PDFを開く', body)
        self.assertIn('id="ws-search-link" href="/">検索画面で本を探す', body)
        self.assertIn('id="ws-export"', body)
        self.assertNotIn("ws-export-pdf", body)
        self.assertNotIn("ws-export-md", body)
        self.assertIn("/api/packs/${pack.id}/export/preview", body)
        self.assertIn("Markdown分冊", body)
        self.assertIn("章単位PDF", body)
        self.assertIn("PDF一式", body)
        self.assertIn("Markdown一式", body)

    def test_home_page_renders_single_workspace_link(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"
            books_dir.mkdir()
            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
            ):
                client = TestClient(tsundokensaku_app)
                self.assertEqual(client.post("/api/packs", json={"name": "ホーム確認資料"}).status_code, 201)
                response = client.get("/")

            self.assertEqual(response.status_code, 200)
            body = response.text
            self.assertIn('class="button" href="/workspace">資料机で整理する', body)
            self.assertNotIn("資料棚で編集", body)
            self.assertNotIn("PDF一式を書き出す", body)
            self.assertNotIn("MD一式を書き出す", body)
            self.assertEqual(body.count('class="button" href="/workspace"'), 1)

    def test_pack_list_page_renders(self) -> None:
        from unittest.mock import MagicMock

        request = MagicMock()
        request.url.path = "/packs"
        response = pack_list_page(request)

        self.assertEqual(response.status_code, 200)
        body = response.body.decode("utf-8")
        self.assertIn("資料一覧", body)
        self.assertIn("/api/packs/stats", body)
        self.assertIn('id="pl-list"', body)
        self.assertIn('id="pl-delete-selected"', body)

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

    def test_view_pdf_keeps_template_context(self) -> None:
        from unittest.mock import MagicMock

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"
            books_dir.mkdir()
            (books_dir / "sample.pdf").write_bytes(b"%PDF-1.4 sample")
            db_path = root / "index.db"
            request = MagicMock()
            request.url.path = "/view/sample.pdf"
            captured: dict[str, object] = {}

            def fake_template_response(request_arg, template_name, context):
                captured["request"] = request_arg
                captured["template_name"] = template_name
                captured["context"] = context
                return HTMLResponse("ok")

            with (
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.resolve_pdf_scrapbox_url", return_value="https://scrapbox.io/x/sample"),
                patch("tsundokensaku.web.templates.TemplateResponse", side_effect=fake_template_response),
            ):
                response = web.view_pdf(request, "sample.pdf", page=3)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["template_name"], "pdf_viewer.html")
        context = captured["context"]
        self.assertEqual(context["request"], request)
        self.assertEqual(context["books_dir"], books_dir)
        self.assertEqual(context["db_path"], db_path)
        self.assertEqual(context["pdf_src"], "/pdf/sample.pdf#page=3")
        self.assertEqual(context["pdf_path"], "sample.pdf")
        self.assertEqual(context["page"], 3)
        self.assertEqual(context["scrapbox_url"], "https://scrapbox.io/x/sample")

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

    # --- R4検索結果整形の回帰挙動（search_view.py分離前のcharacterization test） ---

    def test_highlight_query_returns_empty_markup_for_blank_text(self) -> None:
        self.assertEqual(str(highlight_query("", "query")), "")

    def test_highlight_query_escapes_text_when_no_terms_match(self) -> None:
        rendered = str(highlight_query("<b>plain</b> text", ""))
        self.assertIn("&lt;b&gt;plain&lt;/b&gt;", rendered)
        self.assertNotIn("<mark>", rendered)

    def test_highlight_query_marks_multiple_ordinary_terms(self) -> None:
        rendered = str(highlight_query("SQLiteとFTS5の話", "SQLite FTS5"))
        self.assertIn("<mark>SQLite</mark>", rendered)
        self.assertIn("<mark>FTS5</mark>", rendered)

    def test_highlight_query_returns_markup_instance(self) -> None:
        from markupsafe import Markup

        self.assertIsInstance(highlight_query("コードレビュー", "コードレビュー"), Markup)
        self.assertIsInstance(highlight_query("", "query"), Markup)

    def test_format_indexed_at_returns_empty_for_none_and_blank(self) -> None:
        self.assertEqual(format_indexed_at(None), "")
        self.assertEqual(format_indexed_at(""), "")

    def test_format_indexed_at_assumes_utc_when_timezone_missing(self) -> None:
        self.assertEqual(format_indexed_at("2026-06-29T03:55:59"), "2026/06/29 12:55")

    def test_format_indexed_at_keeps_explicit_timezone(self) -> None:
        self.assertEqual(format_indexed_at("2026-06-29T12:55:59+09:00"), "2026/06/29 12:55")

    def test_format_indexed_at_raises_for_invalid_value(self) -> None:
        with self.assertRaises(ValueError):
            format_indexed_at("not-a-date")

    def test_build_scrapbox_page_url_returns_none_when_unset(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(build_scrapbox_page_url("title", "body"))

    def test_build_scrapbox_page_url_returns_none_when_blank(self) -> None:
        with patch.dict("os.environ", {"SCRAPBOX_BASE_URL": ""}, clear=False):
            self.assertIsNone(build_scrapbox_page_url("title", "body"))

    def test_build_scrapbox_page_url_encodes_title_and_body(self) -> None:
        with patch.dict(
            "os.environ", {"SCRAPBOX_BASE_URL": "https://scrapbox.io/project"}, clear=False
        ):
            url = build_scrapbox_page_url("日本語 タイトル/本#1", "body#1")
        self.assertTrue(url.startswith("https://scrapbox.io/project/"))
        self.assertIn("%E6%97%A5%E6%9C%AC%E8%AA%9E", url)  # 日本語
        self.assertIn("%20", url)  # 空白
        self.assertIn("%2F", url)  # /
        self.assertIn("%23", url)  # #
        self.assertIn("body=body%231", url)

    def test_scrapbox_page_label_extracts_page_name(self) -> None:
        label = _scrapbox_page_label(
            "https://scrapbox.io/project/%E6%9C%AC%E4%B8%80", "fallback"
        )
        self.assertEqual(label, "本一")

    def test_scrapbox_page_label_falls_back_when_url_missing(self) -> None:
        self.assertEqual(_scrapbox_page_label(None, "fallback"), "fallback")
        self.assertEqual(_scrapbox_page_label("", "fallback"), "fallback")

    def test_scrapbox_page_label_falls_back_when_path_has_no_name(self) -> None:
        self.assertEqual(_scrapbox_page_label("https://scrapbox.io/", "fallback"), "fallback")

    def test_sanitize_scrapbox_title_trims_and_normalizes_whitespace(self) -> None:
        self.assertEqual(_sanitize_scrapbox_title("  タイトル  "), "タイトル")
        self.assertEqual(_sanitize_scrapbox_title("タイトル   本文"), "タイトル 本文")

    def test_sanitize_scrapbox_title_converts_newlines_to_space(self) -> None:
        self.assertEqual(_sanitize_scrapbox_title("行1\n行2\r行3"), "行1 行2 行3")

    def test_sanitize_scrapbox_title_replaces_slash_with_fullwidth(self) -> None:
        self.assertEqual(_sanitize_scrapbox_title("A/B"), "A／B")

    def test_sanitize_scrapbox_title_truncates_long_titles(self) -> None:
        long_title = "あ" * 100
        result = _sanitize_scrapbox_title(long_title)
        self.assertEqual(result, "あ" * 80)

    def test_sanitize_scrapbox_title_falls_back_to_default_for_blank_input(self) -> None:
        self.assertEqual(_sanitize_scrapbox_title(""), "検索結果")
        self.assertEqual(_sanitize_scrapbox_title("   "), "検索結果")

    def test_sanitize_scrapbox_title_raises_typeerror_for_non_string_input(self) -> None:
        for value in (None, 123, ["a"], {"a": 1}):
            with self.subTest(value=value):
                with self.assertRaises(TypeError):
                    _sanitize_scrapbox_title(value)

    def test_sort_results_orders_by_title(self) -> None:
        results = [
            {"title": "B", "page_number": 1, "scrapbox_url": None},
            {"title": "A", "page_number": 2, "scrapbox_url": None},
        ]
        self.assertEqual([r["title"] for r in sort_results(results, "title")], ["A", "B"])

    def test_sort_results_orders_by_page(self) -> None:
        results = [
            {"title": "A", "page_number": 5, "scrapbox_url": None},
            {"title": "B", "page_number": 1, "scrapbox_url": None},
        ]
        self.assertEqual(
            [r["page_number"] for r in sort_results(results, "page")], [1, 5]
        )

    def test_sort_results_orders_by_scrapbox_availability(self) -> None:
        results = [
            {"title": "A", "page_number": 1, "scrapbox_url": None},
            {"title": "B", "page_number": 1, "scrapbox_url": "https://scrapbox.io/x/B"},
        ]
        self.assertEqual(
            [r["title"] for r in sort_results(results, "scrapbox")], ["B", "A"]
        )

    def test_sort_results_keeps_input_order_for_unknown_sort(self) -> None:
        results = [
            {"title": "B", "page_number": 1, "scrapbox_url": None},
            {"title": "A", "page_number": 2, "scrapbox_url": None},
        ]
        for sort_value in ("bogus", None, ""):
            with self.subTest(sort=sort_value):
                sorted_results = sort_results(results, sort_value)
                self.assertEqual(sorted_results, results)
                # 未知sort値では新しいリストを作らず、入力リストと要素をidentityのまま返す
                self.assertIs(sorted_results, results)
                for original, returned in zip(results, sorted_results):
                    self.assertIs(original, returned)

    def test_sort_results_empty_list(self) -> None:
        self.assertEqual(sort_results([], "title"), [])

    def test_sort_results_is_stable_for_equal_keys(self) -> None:
        first = {"title": "A", "page_number": 1, "scrapbox_url": None, "tag": "first"}
        second = {"title": "A", "page_number": 1, "scrapbox_url": None, "tag": "second"}
        sorted_results = sort_results([first, second], "title")
        self.assertEqual([r["tag"] for r in sorted_results], ["first", "second"])

    def test_group_pdf_results_keeps_separate_titles_as_distinct_groups(self) -> None:
        results = [
            {
                "kind": "pdf",
                "title": "本A",
                "path": "a.pdf",
                "page_number": 1,
                "snippet": "s1",
                "open_url": "/pdf/a.pdf#page=1",
                "scrapbox_url": None,
                "cover_url": None,
            },
            {
                "kind": "pdf",
                "title": "本B",
                "path": "b.pdf",
                "page_number": 3,
                "snippet": "s2",
                "open_url": "/pdf/b.pdf#page=3",
                "scrapbox_url": None,
                "cover_url": None,
            },
        ]
        grouped = group_pdf_results(results)
        self.assertEqual([g["title"] for g in grouped], ["本A", "本B"])

    def test_group_pdf_results_preserves_non_pdf_order_when_mixed(self) -> None:
        memo1 = {
            "kind": "memo",
            "title": "メモ1",
            "path": "メモ1",
            "page_number": None,
            "snippet": "m1",
            "open_url": "https://scrapbox.io/x/メモ1",
            "scrapbox_url": "https://scrapbox.io/x/メモ1",
            "cover_url": None,
        }
        pdf_entry = {
            "kind": "pdf",
            "title": "本A",
            "path": "a.pdf",
            "page_number": 1,
            "snippet": "s1",
            "open_url": "/pdf/a.pdf#page=1",
            "scrapbox_url": None,
            "cover_url": None,
        }
        memo2 = {
            "kind": "memo",
            "title": "メモ2",
            "path": "メモ2",
            "page_number": None,
            "snippet": "m2",
            "open_url": "https://scrapbox.io/x/メモ2",
            "scrapbox_url": "https://scrapbox.io/x/メモ2",
            "cover_url": None,
        }
        results = [memo1, pdf_entry, memo2]
        grouped = group_pdf_results(results)
        self.assertEqual([g["kind"] for g in grouped], ["memo", "pdf", "memo"])
        self.assertEqual(grouped[0]["title"], "メモ1")
        self.assertEqual(grouped[2]["title"], "メモ2")
        # 非PDF結果は元の辞書オブジェクトをそのまま保持する
        self.assertIs(grouped[0], memo1)
        self.assertIs(grouped[2], memo2)
        # PDFのグループ結果は新しい辞書として生成される（元のresultではない）
        self.assertIsNot(grouped[1], pdf_entry)
        # 入力リスト自体をそのまま返すわけではない
        self.assertIsNot(grouped, results)

    def test_group_pdf_results_empty_input(self) -> None:
        self.assertEqual(group_pdf_results([]), [])

    def test_group_pdf_results_page_number_missing_or_none_yields_empty_page_numbers(self) -> None:
        # page_numberキー欠落・None のいずれも例外にならず、page_numbers/page_summaryが
        # 空のままフォールバックする（現在挙動）。
        cases = {
            "missing_key": {"kind": "pdf", "title": "本X", "path": "x.pdf", "snippet": "s"},
            "none_value": {
                "kind": "pdf", "title": "本X", "path": "x.pdf", "page_number": None, "snippet": "s",
            },
        }
        for label, result in cases.items():
            with self.subTest(case=label):
                grouped = group_pdf_results([result])
                self.assertEqual(len(grouped), 1)
                self.assertEqual(grouped[0]["page_numbers"], [])
                self.assertEqual(grouped[0]["page_summary"], "")

    def test_group_pdf_results_empty_string_page_number_is_kept_as_is(self) -> None:
        # 空文字はNoneと異なり除外判定に引っかからず、そのままpage_numbers/page_summaryへ反映される
        result = {"kind": "pdf", "title": "本E", "path": "e.pdf", "page_number": "", "snippet": "s"}
        grouped = group_pdf_results([result])
        self.assertEqual(grouped[0]["page_numbers"], [""])
        self.assertEqual(grouped[0]["page_summary"], "p.")

    def test_group_pdf_results_missing_title_key_groups_under_empty_string_key(self) -> None:
        # titleキー欠落は空文字titleとして扱われ、同じ空文字titleを持つ結果同士が
        # 同一グループへ集約される（例外にならない）。グループ辞書自体にもtitleキーは含まれない。
        result_1 = {"kind": "pdf", "path": "z1.pdf", "page_number": 1, "snippet": "s1"}
        result_2 = {"kind": "pdf", "path": "z2.pdf", "page_number": 2, "snippet": "s2"}
        grouped = group_pdf_results([result_1, result_2])
        self.assertEqual(len(grouped), 1)
        self.assertNotIn("title", grouped[0])
        self.assertEqual(grouped[0]["page_numbers"], [1, 2])

    def test_build_search_result_rows_pdf_result_with_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            books_dir = Path(temp_dir)
            (books_dir / "book-a.pdf").write_bytes(b"%PDF-1.4")
            results = [
                SearchResult(
                    title="本A", path="book-a.pdf", page_number=3, snippet="snippet", kind="pdf"
                )
            ]
            rows = build_search_result_rows(results, books_dir=books_dir, metadata_by_stem={})
        row = rows[0]
        self.assertEqual(row["title"], "本A")
        self.assertEqual(row["kind"], "pdf")
        self.assertEqual(row["page_number"], 3)
        self.assertIsNotNone(row["open_url"])
        self.assertEqual(row["page_urls"], [row["open_url"]])

    def test_build_search_result_rows_pdf_result_with_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            books_dir = Path(temp_dir)
            results = [
                SearchResult(
                    title="本B", path="missing.pdf", page_number=1, snippet="snippet", kind="pdf"
                )
            ]
            rows = build_search_result_rows(results, books_dir=books_dir, metadata_by_stem={})
        row = rows[0]
        self.assertIsNone(row["open_url"])
        self.assertEqual(row["page_urls"], [None])

    def test_build_search_result_rows_non_pdf_result_keeps_urls(self) -> None:
        results = [
            SearchResult(
                title="メモ",
                path="メモ",
                page_number=None,
                snippet="本文",
                kind="memo",
                open_url="https://scrapbox.io/x/メモ",
                scrapbox_url="https://scrapbox.io/x/メモ",
                cover_url=None,
            )
        ]
        rows = build_search_result_rows(results, books_dir=Path("."), metadata_by_stem={})
        row = rows[0]
        self.assertEqual(row["kind"], "memo")
        self.assertEqual(row["open_url"], "https://scrapbox.io/x/メモ")
        self.assertEqual(row["scrapbox_url"], "https://scrapbox.io/x/メモ")
        self.assertNotIn("page_urls", row)

    def test_build_search_result_rows_keeps_duplicate_pdf_hits_as_separate_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            books_dir = Path(temp_dir)
            (books_dir / "a.pdf").write_bytes(b"%PDF-1.4")
            results = [
                SearchResult(title="本A", path="a.pdf", page_number=1, snippet="s1", kind="pdf"),
                SearchResult(title="本A", path="a.pdf", page_number=5, snippet="s5", kind="pdf"),
            ]
            rows = build_search_result_rows(results, books_dir=books_dir, metadata_by_stem={})
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["page_number"] for r in rows], [1, 5])

    def test_finalize_search_result_rows_applies_sort(self) -> None:
        rows = [
            {"title": "B", "page_number": 1, "scrapbox_url": None, "kind": "memo"},
            {"title": "A", "page_number": 2, "scrapbox_url": None, "kind": "memo"},
        ]
        finalized = finalize_search_result_rows(
            rows, books_dir=Path("."), sort="title", group="none"
        )
        self.assertEqual([r["title"] for r in finalized], ["A", "B"])

    def test_finalize_search_result_rows_groups_pdf_when_group_is_book(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            books_dir = Path(temp_dir)
            (books_dir / "a.pdf").write_bytes(b"%PDF-1.4")
            row1 = {
                "title": "本A", "path": "a.pdf", "page_number": 1, "page_numbers": [1],
                "snippet": "s1", "kind": "pdf", "open_url": None, "scrapbox_url": None,
                "cover_url": None, "page_summary": "p.1",
            }
            row2 = {
                "title": "本A", "path": "a.pdf", "page_number": 3, "page_numbers": [3],
                "snippet": "s2", "kind": "pdf", "open_url": None, "scrapbox_url": None,
                "cover_url": None, "page_summary": "p.3",
            }
            rows = [row1, row2]
            finalized = finalize_search_result_rows(
                rows, books_dir=books_dir, sort="rank", group="book"
            )
        self.assertEqual(len(finalized), 1)
        self.assertEqual(finalized[0]["page_numbers"], [1, 3])
        self.assertEqual(len(finalized[0]["page_urls"]), 2)
        # group="book"ではPDFグループの辞書は新規生成される（元の行辞書ではない）
        self.assertIsNot(finalized[0], row1)
        self.assertIsNot(finalized[0], row2)

    def test_finalize_search_result_rows_keeps_individual_results_when_group_none(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            books_dir = Path(temp_dir)
            (books_dir / "a.pdf").write_bytes(b"%PDF-1.4")
            row1 = {
                "title": "本A", "path": "a.pdf", "page_number": 1, "page_numbers": [1],
                "snippet": "s1", "kind": "pdf", "open_url": None, "scrapbox_url": None,
                "cover_url": None,
            }
            row2 = {
                "title": "本A", "path": "a.pdf", "page_number": 3, "page_numbers": [3],
                "snippet": "s2", "kind": "pdf", "open_url": None, "scrapbox_url": None,
                "cover_url": None,
            }
            rows = [row1, row2]
            finalized = finalize_search_result_rows(
                rows, books_dir=books_dir, sort="rank", group="none"
            )
        self.assertEqual(len(finalized), 2)
        # group="none"では元の行辞書が破壊的に更新されたうえで、そのまま参照される
        self.assertIs(finalized[0], row1)
        self.assertIs(finalized[1], row2)
        self.assertIn("page_urls", row1)
        self.assertIn("page_urls", row2)

    def test_finalize_search_result_rows_keeps_non_pdf_results(self) -> None:
        memo_row = {
            "title": "メモ", "path": "メモ", "page_number": None, "snippet": "m",
            "kind": "memo", "open_url": "url", "scrapbox_url": "url", "cover_url": None,
        }
        rows = [memo_row]
        finalized = finalize_search_result_rows(
            rows, books_dir=Path("."), sort="rank", group="book"
        )
        self.assertEqual(len(finalized), 1)
        self.assertEqual(finalized[0]["kind"], "memo")
        # 非PDF結果はgroup="book"でも元の辞書オブジェクトのまま参照される
        self.assertIs(finalized[0], memo_row)

    def test_finalize_search_result_rows_unknown_group_bypasses_grouping(self) -> None:
        # normalize_search_groupを経由しない未知group値（"book"以外）は、
        # 現在の実装では group_pdf_results を呼ばず、group="none"相当としてそのまま扱われる。
        with tempfile.TemporaryDirectory() as temp_dir:
            books_dir = Path(temp_dir)
            (books_dir / "a.pdf").write_bytes(b"%PDF-1.4")
            row1 = {
                "title": "本A", "path": "a.pdf", "page_number": 1, "page_numbers": [1],
                "snippet": "s1", "kind": "pdf", "open_url": None, "scrapbox_url": None,
                "cover_url": None,
            }
            row2 = {
                "title": "本A", "path": "a.pdf", "page_number": 3, "page_numbers": [3],
                "snippet": "s2", "kind": "pdf", "open_url": None, "scrapbox_url": None,
                "cover_url": None,
            }
            rows = [row1, row2]
            finalized = finalize_search_result_rows(
                rows, books_dir=books_dir, sort="rank", group="bogus"
            )
        self.assertEqual(len(finalized), 2)
        # 入力リスト自体・各行辞書のidentityが維持されたまま、page_urlsだけ破壊的に更新される
        self.assertIs(finalized, rows)
        self.assertIs(finalized[0], row1)
        self.assertIs(finalized[1], row2)
        self.assertIn("page_urls", row1)
        self.assertIn("page_urls", row2)

    def test_finalize_search_result_rows_empty_input(self) -> None:
        self.assertEqual(
            finalize_search_result_rows([], books_dir=Path("."), sort="rank", group="book"), []
        )

    def test_normalize_search_group_blank_values_default_to_book(self) -> None:
        self.assertEqual(normalize_search_group(""), "book")
        self.assertEqual(normalize_search_group(" "), "book")

    def test_normalize_search_group_case_and_whitespace_sensitive(self) -> None:
        self.assertEqual(normalize_search_group(["BOOK"]), "book")
        self.assertEqual(normalize_search_group(["book "]), "book")

    def test_normalize_search_match_blank_values_default_to_all(self) -> None:
        self.assertEqual(normalize_search_match(""), "all")
        self.assertEqual(normalize_search_match(" "), "all")

    def test_normalize_search_match_case_and_whitespace_sensitive(self) -> None:
        self.assertEqual(normalize_search_match(["ALL"]), "all")
        self.assertEqual(normalize_search_match(["all "]), "all")

    def test_build_search_scrapbox_body_uses_fixed_creation_time(self) -> None:
        fixed_now = datetime(2026, 7, 29, 12, 34, tzinfo=ZoneInfo("Asia/Tokyo"))
        with patch("tsundokensaku.search_view._now_jst", return_value=fixed_now):
            page_title, body = build_search_scrapbox_body(
                query="SQLite", scope="all", sort="rank", group="none", results=[]
            )
        self.assertIn("作成日時: 2026/07/29 12:34 JST", body)
        self.assertIn("2026-07-29 12:34", page_title)

    def test_build_search_scrapbox_body_empty_results(self) -> None:
        _, body = build_search_scrapbox_body(
            query="SQLite", scope="all", sort="rank", group="none", results=[]
        )
        self.assertIn("結果一覧", body)

    def test_build_search_scrapbox_body_keeps_slash_in_result_title(self) -> None:
        results = [
            {
                "title": "A/B",
                "kind": "pdf",
                "snippet": "s",
                "path": "a.pdf",
                "scrapbox_url": None,
            }
        ]
        _, body = build_search_scrapbox_body(
            query="SQLite", scope="all", sort="rank", group="none", results=results
        )
        self.assertIn("A/B", body)

    def test_build_search_scrapbox_body_line_structure_and_snippet_newlines(self) -> None:
        # snippet内の \n のみ半角空白へ正規化され、\r はそのまま残る（現在挙動）。
        # 1件ごとの本文構造（タイトル行/詳細行/snippet行/scrapbox行/区切りの空行）と、
        # 本文全体の改行形式（\n区切り・末尾strip）を完全一致で固定する。
        fixed_now = datetime(2026, 7, 29, 12, 34, tzinfo=ZoneInfo("Asia/Tokyo"))
        results = [
            {
                "title": "本A",
                "kind": "pdf",
                "page_summary": "p.1",
                "snippet": "行1\n行2\r\n行3",
                "path": "a.pdf",
                "scrapbox_url": "https://scrapbox.io/x/本A",
            },
            {
                "title": "本B",
                "kind": "memo",
                "page_summary": "",
                "snippet": "",
                "path": "本B",
                "scrapbox_url": None,
            },
        ]
        with patch("tsundokensaku.search_view._now_jst", return_value=fixed_now):
            _, body = build_search_scrapbox_body(
                query="q", scope="all", sort="rank", group="none", results=results
            )
        expected = "\n".join(
            [
                "#つんどけんさく",
                "",
                "検索語: q",
                "検索範囲: all",
                "語の一致: すべての語を含む",
                "並び順: rank",
                "まとめ方: none",
                "作成日時: 2026/07/29 12:34 JST",
                "",
                "結果一覧",
                "1. 本A",
                "   pdf / p.1",
                "   行1 行2\r 行3",
                "   scrapbox: [本A]",
                "",
                "2. 本B",
                "   memo",
            ]
        )
        self.assertEqual(body, expected)

    def test_build_search_scrapbox_body_keeps_input_order(self) -> None:
        results = [
            {"title": "本Z", "kind": "pdf", "snippet": "s-Z", "path": "z.pdf", "scrapbox_url": None},
            {"title": "本A", "kind": "pdf", "snippet": "s-A", "path": "a.pdf", "scrapbox_url": None},
            {"title": "本M", "kind": "pdf", "snippet": "s-M", "path": "m.pdf", "scrapbox_url": None},
        ]
        _, body = build_search_scrapbox_body(
            query="q", scope="all", sort="rank", group="none", results=results
        )
        # 並べ替えは行わず、入力順のまま本文へ反映される
        self.assertLess(body.index("本Z"), body.index("本A"))
        self.assertLess(body.index("本A"), body.index("本M"))


class IndexJobCharacterizationTest(unittest.TestCase):
    INITIAL_PROGRESS = {
        "running": False,
        "current": 0,
        "total": 0,
        "title": "",
        "message": "",
        "updated_at": "",
    }

    def setUp(self) -> None:
        self._saved_progress = index_job.get_progress()
        self._replace_progress(self.INITIAL_PROGRESS)

    def tearDown(self) -> None:
        self._replace_progress(self._saved_progress)

    def _replace_progress(self, progress: dict[str, object]) -> None:
        with index_job.INDEX_PROGRESS_LOCK:
            index_job.INDEX_PROGRESS.clear()
            index_job.INDEX_PROGRESS.update(progress)

    def test_post_index_rejects_new_job_while_running(self) -> None:
        self._replace_progress(
            {
                "running": True,
                "current": 2,
                "total": 5,
                "title": "処理中の本",
                "message": "INDEX 処理中の本",
                "updated_at": "",
            }
        )
        progress_before_request = index_job.get_progress()
        start_observation = {"called": False}

        def start_stub(_force_paths: set[str] | None = None) -> None:
            start_observation["called"] = True

        with patch("tsundokensaku.web.index_job.start", start_stub):
            response = TestClient(tsundokensaku_app).post("/settings/index", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(
            response.headers["location"],
            f"/settings?message={quote('インデックス実行中です')}",
        )
        self.assertEqual(start_observation, {"called": False})
        self.assertEqual(index_job.get_progress(), progress_before_request)

    def test_post_index_deduplicates_force_paths(self) -> None:
        start_observation: dict[str, object] = {}

        def start_stub(force_paths: set[str] | None = None) -> None:
            start_observation["force_paths"] = force_paths
            start_observation["called"] = True

        with patch("tsundokensaku.web.index_job.start", start_stub):
            response = TestClient(tsundokensaku_app).post(
                "/settings/index",
                data={"force": ["books/a.pdf", "books/a.pdf", "books/b.pdf"]},
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(
            response.headers["location"],
            f"/settings?message={quote('選択した 2 件の強制再インデックスを開始しました')}",
        )
        self.assertEqual(start_observation["force_paths"], {"books/a.pdf", "books/b.pdf"})
        self.assertTrue(start_observation["called"])

    def test_post_index_converts_empty_force_to_none(self) -> None:
        start_observation: dict[str, object] = {}

        def start_stub(force_paths: set[str] | None = None) -> None:
            start_observation["force_paths"] = force_paths
            start_observation["called"] = True

        with patch("tsundokensaku.web.index_job.start", start_stub):
            response = TestClient(tsundokensaku_app).post(
                "/settings/index",
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(
            response.headers["location"],
            f"/settings?message={quote('インデックスを開始しました')}",
        )
        self.assertIsNone(start_observation["force_paths"])
        self.assertTrue(start_observation["called"])

    def test_settings_progress_returns_current_snapshot_as_json(self) -> None:
        expected = {
            "running": True,
            "current": 4,
            "total": 9,
            "title": "JSON対象",
            "message": "INDEX JSON対象",
            "updated_at": "",
        }
        self._replace_progress(expected)

        response = TestClient(tsundokensaku_app).get("/settings/progress")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload, expected)
        self.assertEqual(set(payload), set(self.INITIAL_PROGRESS))
        payload["message"] = "レスポンス側の変更"
        self.assertEqual(index_job.INDEX_PROGRESS["message"], "INDEX JSON対象")


class ResolvePdfPathTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.books_dir = Path(self._tmpdir.name) / "books"
        self.books_dir.mkdir()

    def _touch(self, relative: str) -> Path:
        path = self.books_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-1.4")
        return path

    def test_resolves_relative_path_under_books_dir(self) -> None:
        self._touch("tech/example.pdf")
        result = resolve_pdf_path("tech/example.pdf", self.books_dir)
        self.assertEqual(result, Path("tech/example.pdf"))

    def test_resolves_absolute_path_under_books_dir(self) -> None:
        absolute = self._touch("tech/example.pdf")
        result = resolve_pdf_path(absolute, self.books_dir)
        self.assertEqual(result, Path("tech/example.pdf"))

    def test_resolves_container_data_books_path(self) -> None:
        self._touch("tech/example.pdf")
        container_path = Path("/data/books/tech/example.pdf")
        result = resolve_pdf_path(container_path, self.books_dir)
        self.assertEqual(result, Path("tech/example.pdf"))

    def test_resolves_container_books_tech_path(self) -> None:
        self._touch("example.pdf")
        container_path = Path("/books/tech/example.pdf")
        result = resolve_pdf_path(container_path, self.books_dir)
        self.assertEqual(result, Path("example.pdf"))

    def test_falls_back_to_filename_lookup_in_books_dir_root(self) -> None:
        self._touch("example.pdf")
        result = resolve_pdf_path("nested/missing/example.pdf", self.books_dir)
        self.assertEqual(result, Path("example.pdf"))

    def test_returns_none_for_missing_file(self) -> None:
        result = resolve_pdf_path("does-not-exist.pdf", self.books_dir)
        self.assertIsNone(result)

    def test_returns_none_for_parent_traversal(self) -> None:
        outside_dir = Path(self._tmpdir.name) / "outside"
        outside_dir.mkdir()
        (outside_dir / "secret.pdf").write_bytes(b"%PDF-1.4")
        result = resolve_pdf_path("../outside/secret.pdf", self.books_dir)
        self.assertIsNone(result)

    def test_returns_none_for_absolute_path_outside_books_dir(self) -> None:
        outside_dir = Path(self._tmpdir.name) / "outside"
        outside_dir.mkdir()
        outside_file = outside_dir / "secret.pdf"
        outside_file.write_bytes(b"%PDF-1.4")
        result = resolve_pdf_path(outside_file, self.books_dir)
        self.assertIsNone(result)

    def test_returns_none_for_symlink_escaping_books_dir(self) -> None:
        outside_dir = Path(self._tmpdir.name) / "outside"
        outside_dir.mkdir()
        outside_file = outside_dir / "secret.pdf"
        outside_file.write_bytes(b"%PDF-1.4")
        link = self.books_dir / "link.pdf"
        os.symlink(outside_file, link)
        result = resolve_pdf_path("link.pdf", self.books_dir)
        self.assertIsNone(result)


class PdfUrlTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.books_dir = Path(self._tmpdir.name) / "books"
        self.books_dir.mkdir()

    def _touch(self, relative: str) -> Path:
        path = self.books_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-1.4")
        return path

    def test_pdf_url_returns_view_path(self) -> None:
        self._touch("tech/example.pdf")
        url = pdf_url("tech/example.pdf", self.books_dir)
        self.assertEqual(url, "/view/tech/example.pdf")

    def test_pdf_url_appends_page_query(self) -> None:
        self._touch("example.pdf")
        url = pdf_url("example.pdf", self.books_dir, page_number=5)
        self.assertEqual(url, "/view/example.pdf?page=5")

    def test_pdf_url_returns_none_when_unresolvable(self) -> None:
        url = pdf_url("missing.pdf", self.books_dir)
        self.assertIsNone(url)

    def test_pdf_url_encodes_special_characters(self) -> None:
        relative = "資料 本棚/日本語 タイトル.pdf"
        self._touch(relative)
        url = pdf_url(relative, self.books_dir)
        expected = f"/view/{quote(relative)}"
        self.assertEqual(url, expected)

    def test_raw_pdf_url_returns_pdf_path(self) -> None:
        self._touch("tech/example.pdf")
        url = raw_pdf_url("tech/example.pdf", self.books_dir)
        self.assertEqual(url, "/pdf/tech/example.pdf")

    def test_raw_pdf_url_appends_page_fragment(self) -> None:
        self._touch("example.pdf")
        url = raw_pdf_url("example.pdf", self.books_dir, page_number=5)
        self.assertEqual(url, "/pdf/example.pdf#page=5")

    def test_raw_pdf_url_returns_none_when_unresolvable(self) -> None:
        url = raw_pdf_url("missing.pdf", self.books_dir)
        self.assertIsNone(url)

    def test_raw_pdf_url_encodes_special_characters(self) -> None:
        relative = "資料 本棚/日本語 タイトル.pdf"
        self._touch(relative)
        url = raw_pdf_url(relative, self.books_dir)
        expected = f"/pdf/{quote(relative)}"
        self.assertEqual(url, expected)


class UniqueDestinationPathTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.tmp_path = Path(self._tmpdir.name)

    def test_returns_same_path_when_missing(self) -> None:
        destination = self.tmp_path / "example.pdf"
        result = _unique_destination_path(destination)
        self.assertEqual(result, destination)

    def test_increments_suffix_in_parentheses_when_exists(self) -> None:
        destination = self.tmp_path / "example.pdf"
        destination.write_bytes(b"%PDF-1.4")
        result = _unique_destination_path(destination)
        self.assertEqual(result, self.tmp_path / "example (2).pdf")

    def test_increments_further_when_second_also_exists(self) -> None:
        destination = self.tmp_path / "example.pdf"
        destination.write_bytes(b"%PDF-1.4")
        (self.tmp_path / "example (2).pdf").write_bytes(b"%PDF-1.4")
        result = _unique_destination_path(destination)
        self.assertEqual(result, self.tmp_path / "example (3).pdf")


class UniqueExportDestinationPathTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.tmp_path = Path(self._tmpdir.name)

    def test_returns_same_path_when_missing(self) -> None:
        destination = self.tmp_path / "example.pdf"
        result = _unique_export_destination_path(destination)
        self.assertEqual(result, destination)

    def test_increments_with_underscore_when_exists(self) -> None:
        destination = self.tmp_path / "example.pdf"
        destination.write_bytes(b"%PDF-1.4")
        result = _unique_export_destination_path(destination)
        self.assertEqual(result, self.tmp_path / "example_2.pdf")

    def test_increments_further_when_second_also_exists(self) -> None:
        destination = self.tmp_path / "example.pdf"
        destination.write_bytes(b"%PDF-1.4")
        (self.tmp_path / "example_2.pdf").write_bytes(b"%PDF-1.4")
        result = _unique_export_destination_path(destination)
        self.assertEqual(result, self.tmp_path / "example_3.pdf")


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

    def test_stats_and_export_preview_share_the_same_four_aggregate_values(self) -> None:
        # R8 PR2 characterization test（設計書§19.2-2）: /api/packs/stats と
        # エクスポートプレビューが同一資料に対して同じ4集計値
        # （book_count, item_count, total_pages, estimated_tokens）を返すこと。
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
                created = self._payload(api_create_pack({"name": "共有集計テスト"}))
                items = [
                    {"pdf_path": "a.pdf", "title": "本A前半", "pages": "1-3", "collapsed": False, "position": 0},
                    {"pdf_path": "a.pdf", "title": "本A後半", "pages": "4-5", "collapsed": False, "position": 1},
                ]
                self._payload(api_replace_pack_items(created["id"], {"items": items}))

                stats_payload = self._payload(api_list_pack_stats())
                preview_payload = self._payload(api_preview_pack_export(created["id"]))

            pack_stats = next(p for p in stats_payload["packs"] if p["id"] == created["id"])
            for key in ("book_count", "item_count", "total_pages", "estimated_tokens"):
                self.assertEqual(pack_stats[key], preview_payload[key], msg=key)
            self.assertGreater(pack_stats["estimated_tokens"], 0)


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


class ExportJsonContractTest(unittest.TestCase):
    """R8 PR4: JSON export準備は`export_service.prepare_json_export`へ移動済み。

    ここではHTTP契約（`TestClient`経由のstatus・Content-Type・
    Content-Disposition・レスポンスbodyの受け渡し）のみを、通常の成功
    ケース1件で固定する（design.md決定5）。JSON構造のexact値・空pack・
    PDF不在・pages不正な資料でも生成される契約は
    `tests/test_export_service.py`の`PrepareJsonExportTest`が担う。
    """

    def _payload(self, response) -> dict:
        return json.loads(response.body)

    def test_json_export_returns_service_content_via_http_response(self) -> None:
        """R8 PR4決定5: web.pyはserviceが返したcontent/filenameを変形せずHTTP Responseへ渡す。

        JSON構造のexact値・UTF-8・key順・indent・末尾改行などの生成契約は
        `tests/test_export_service.py`の`PrepareJsonExportTest`が担う。ここでは
        HTTP層（status・Content-Type・Content-Disposition・body）の受け渡しと、
        `_now_jst()`の戻り値がそのまま`exported_at`としてserviceへ渡ることだけを確認する。
        `filename`は日本語を含む値にし、`Content-Disposition`のパーセントエンコード
        （`quote()`によるweb.py固有の責務。design.md決定3）が働くことも確認する。
        """
        fixed_now = datetime(2026, 8, 19, 9, 30, tzinfo=ZoneInfo("Asia/Tokyo"))
        prepared = PreparedJsonExport(
            content=b'{"stub": "json-export-content"}',
            filename="日本語資料名_20260819.json",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            with patch("tsundokensaku.web.get_db_path", return_value=db_path):
                created = self._payload(api_create_pack({"name": "資料"}))
                items = [{"pdf_path": "a.pdf", "title": "本A", "pages": "1-2", "collapsed": False, "position": 0}]
                self._payload(api_replace_pack_items(created["id"], {"items": items}))

                client = TestClient(tsundokensaku_app)
                with (
                    patch("tsundokensaku.web._now_jst", return_value=fixed_now),
                    patch("tsundokensaku.web.export_service.prepare_json_export", return_value=prepared) as prepare_mock,
                ):
                    response = client.get(f"/api/packs/{created['id']}/export", params={"format": "json"})

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["content-type"], "application/json")
            self.assertEqual(
                response.headers["content-disposition"],
                "attachment; filename*=UTF-8''%E6%97%A5%E6%9C%AC%E8%AA%9E%E8%B3%87%E6%96%99%E5%90%8D_20260819.json",
            )
            self.assertEqual(response.content, prepared.content)
            self.assertEqual(prepare_mock.call_args.kwargs["exported_at"], fixed_now)


class ExportArchiveContractTest(unittest.TestCase):
    """R8 PR5: archive exportは`export_service.prepare_archive_export`へ移動済み。

    ここではHTTP契約（`TestClient`を介さない直接呼び出しでの、status・
    Content-Type・Content-Disposition・レスポンスbodyの受け渡し）のみを、
    通常の成功ケース1件で固定する（design.md決定5）。ZIPのexact logical
    contentの生成契約は`tests/test_export_service.py`の
    `PrepareArchiveExportTest`が担う（design.md決定6）。
    """

    def _payload(self, response) -> dict:
        return json.loads(response.body)

    def test_archive_export_returns_service_content_via_http_response(self) -> None:
        """R8 PR5決定5: web.pyはserviceが返したcontent/filenameを変形せずHTTP Responseへ渡す。

        ZIPのexact logical content・fixed clock注入下のmanifest日時などの
        生成契約は`tests/test_export_service.py`の`PrepareArchiveExportTest`が
        担う。ここではHTTP層（status・Content-Type・Content-Disposition・
        body）の受け渡しと、`_now_jst()`の戻り値がそのまま`exported_at`として
        serviceへ渡ることだけを確認する。`filename`は日本語を含む値にし、
        `Content-Disposition`のパーセントエンコード（`quote()`によるweb.py
        固有の責務）が働くことも確認する。
        """
        fixed_now = datetime(2026, 8, 19, 9, 30, tzinfo=ZoneInfo("Asia/Tokyo"))
        prepared = PreparedArchiveExport(
            content=b"stub-zip-content",
            filename="日本語資料名_20260819.zip",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            with patch("tsundokensaku.web.get_db_path", return_value=db_path):
                created = self._payload(api_create_pack({"name": "資料"}))
                items = [{"pdf_path": "a.pdf", "title": "本A", "pages": "1-2", "collapsed": False, "position": 0}]
                self._payload(api_replace_pack_items(created["id"], {"items": items}))

                client = TestClient(tsundokensaku_app)
                with (
                    patch("tsundokensaku.web._now_jst", return_value=fixed_now),
                    patch(
                        "tsundokensaku.web.export_service.prepare_archive_export", return_value=prepared
                    ) as prepare_mock,
                ):
                    response = client.get(f"/api/packs/{created['id']}/export", params={"format": "pdf"})

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["content-type"], "application/zip")
            self.assertEqual(
                response.headers["content-disposition"],
                "attachment; filename*=UTF-8''%E6%97%A5%E6%9C%AC%E8%AA%9E%E8%B3%87%E6%96%99%E5%90%8D_20260819.zip",
            )
            self.assertEqual(response.content, prepared.content)
            self.assertEqual(prepare_mock.call_args.kwargs["exported_at"], fixed_now)


class ExportClockCallCountTest(unittest.TestCase):
    """R8 PR2 characterization test（設計書§19.2-12）。

    `_now_jst`の呼出し回数（archive全体で1回か、Markdown entryごとに
    追加で呼ばれるか）と、archive日時（JST）・event記録日時（UTC）が
    別々の時計呼び出しである現状を固定する。統一はこのPRの対象外。
    """

    def _payload(self, response) -> dict:
        return json.loads(response.body)

    def _make_pdf(self, path: Path, page_count: int = 2) -> None:
        writer = PdfWriter()
        for _ in range(page_count):
            writer.add_blank_page(width=72, height=72)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            writer.write(handle)

    def test_pdf_archive_calls_now_jst_exactly_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"
            self._make_pdf(books_dir / "a.pdf")

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
            ):
                created = self._payload(api_create_pack({"name": "資料"}))
                items = [{"pdf_path": "a.pdf", "title": "本A", "pages": "1-2", "collapsed": False, "position": 0}]
                self._payload(api_replace_pack_items(created["id"], {"items": items}))

                with patch("tsundokensaku.web._now_jst", wraps=web._now_jst) as spy:
                    response = api_export_pack(created["id"], format="pdf")
                    self.assertEqual(response.status_code, 200)
                    # PDF archiveはrender_pdf_exportが_now_jstを使わないため、
                    # archive全体のexported_at取得（1回）のみ
                    spy.assert_called_once()

    def test_markdown_archive_calls_now_jst_once_per_chunk_plus_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"
            self._make_pdf(books_dir / "a.pdf")
            self._make_pdf(books_dir / "b.pdf")

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
            ):
                created = self._payload(api_create_pack({"name": "資料"}))
                items = [
                    {"pdf_path": "a.pdf", "title": "本A", "pages": "1-2", "collapsed": False, "position": 0},
                    {"pdf_path": "b.pdf", "title": "本B", "pages": "1-2", "collapsed": False, "position": 1},
                ]
                self._payload(api_replace_pack_items(created["id"], {"items": items}))

                with patch("tsundokensaku.web._now_jst", wraps=web._now_jst) as spy:
                    response = api_export_pack(created["id"], format="md")
                    self.assertEqual(response.status_code, 200)
                    # standardのMarkdown archiveは項目ごとにrender_markdown_export
                    # ラッパー(web.py内)が個別に_now_jst()を呼ぶため、
                    # archive全体分(1) + 項目数分(2) = 3回
                    self.assertEqual(spy.call_count, 3)

    def test_archive_datetime_and_event_datetime_are_independent_clocks(self) -> None:
        # archiveのexported_at（JST、_now_jst）と、export_eventのexported_at
        # （UTC、database.record_export_event内のdatetime.now(timezone.utc)）は
        # 別々の時計呼び出しであり、値の形式も異なる（統一しない）。
        fixed_now = datetime(2026, 8, 19, 9, 30, tzinfo=ZoneInfo("Asia/Tokyo"))
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"
            self._make_pdf(books_dir / "a.pdf")

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
            ):
                created = self._payload(api_create_pack({"name": "資料"}))
                items = [{"pdf_path": "a.pdf", "title": "本A", "pages": "1-2", "collapsed": False, "position": 0}]
                self._payload(api_replace_pack_items(created["id"], {"items": items}))

                with patch("tsundokensaku.web._now_jst", return_value=fixed_now):
                    response = api_export_pack(created["id"], format="pdf")
                self.assertEqual(response.status_code, 200)
                disposition = response.headers["content-disposition"]
                # archive名には、注入したJST日付表記（%Y%m%d）が使われる
                self.assertIn("資料_20260819.zip", unquote(disposition))

            import sqlite3
            conn = sqlite3.connect(str(db_path))
            try:
                row = conn.execute("SELECT exported_at FROM export_events ORDER BY id DESC LIMIT 1").fetchone()
            finally:
                conn.close()
            # eventのexported_atはISO8601のUTC表記（末尾+00:00やZを含む、または
            # タイムゾーン情報付き）であり、archive名のJST日付とは独立した値
            self.assertIsNotNone(row)
            self.assertRegex(row[0], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


class ExportPdfResolutionCallbackBoundaryTest(unittest.TestCase):
    """R8 PR2 characterization test（設計書§19.2-16）由来、R8 PR5で更新。

    R8 PR5で `_export_pack_archive` のresolve_pdf・chapter_loader・render_pdf
    callbackは `pdf_export.resolve_pdf_source` / `pdf_export.render_pdf_export`
    を直接使う非HTTP化に変更された（design.md決定2）。よってarchive経路の
    callback自体はもうHTTPExceptionを送出しない
    （`tests/test_export_service.py` の `PrepareArchiveExportTest`が
    `PdfSourceNotFoundError`の伝播を確認する）。

    `_resolve_pdf_file_or_404`（`web.py`内wrapper関数自体）は、他route
    （`/export-pdf`等）から引き続き使われており削除・整理しない
    （design.md決定2）。この関数単体の挙動はここで固定する。

    previewの chapter_loader に関する非HTTP化後のテストは
    tests/test_export_service.py の PreviewChapterLoaderPdfSourceBoundaryTest
    へ移設した。
    """

    def _payload(self, response) -> dict:
        return json.loads(response.body)

    def test_resolve_pdf_file_or_404_raises_http_exception_directly(self) -> None:
        # web.py内の _resolve_pdf_file_or_404 単体の挙動（HTTPExceptionへの
        # 変換）を固定する。他route（/export-pdf等）がこの関数を使い続ける
        # ため、関数自体は削除・整理しない（design.md決定2）。
        with tempfile.TemporaryDirectory() as temp_dir:
            books_dir = Path(temp_dir)
            with self.assertRaises(HTTPException) as ctx:
                web._resolve_pdf_file_or_404("missing.pdf", books_dir)
            self.assertEqual(ctx.exception.status_code, 404)
            self.assertEqual(ctx.exception.detail, "PDF not found")


class ExportArchiveBackwardCompatibilityTest(unittest.TestCase):
    """B-2: api_export_pack を StandardProfile 経由へ載せ替えた前後の出力互換性。

    R8 PR5: ZIP構造・エントリ内容のゴールデンテスト（かつてのB-2後方互換性
    確認）は、archive生成が`export_service.prepare_archive_export`へ移った
    ことに伴い`tests/test_export_service.py`の`PrepareArchiveExportTest`へ
    移設した（design.md決定6）。ここにはHTTPエラー応答（status・message）の
    契約のみを残す。
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

    def test_unlisted_but_resolvable_profile_returns_same_400_as_unknown(self) -> None:
        # R8 PR2 characterization test（設計書§19.2-4 / design.md決定5）:
        # _resolve_export_profile_or_400 は (a) resolve_profile自体が知らない
        # 完全未知のprofile名と、(b) resolve_profileには存在するが
        # EXTERNALLY_AVAILABLE_EXPORT_PROFILESに含まれないprofile名の
        # 2つの異なるコードパスを持つ。現状PROFILESと
        # EXTERNALLY_AVAILABLE_EXPORT_PROFILESの登録名は完全一致しており
        # (b)は自然発生しないため、EXTERNALLY_AVAILABLE_EXPORT_PROFILESを
        # 一時的に絞ってこの分岐を直接検証する。両方とも同一の400・
        # 同一メッセージ（異なる動作を新設するわけではない）であることを固定する。
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.export_service.EXTERNALLY_AVAILABLE_EXPORT_PROFILES", frozenset({"chat", "chapter"})),
            ):
                created = self._payload(api_create_pack({"name": "資料"}))
                with self.assertRaises(HTTPException) as ctx:
                    api_export_pack(created["id"], profile="standard", format="pdf")
                self.assertEqual(ctx.exception.status_code, 400)
                self.assertEqual(ctx.exception.detail, "不明なエクスポートプロファイルです: standard")

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
                        ["manifest.md", "01_本A_第1章_p1-3.pdf", "02_本A_第2章_p4-6.pdf"],
                    )
                    manifest = archive.read("manifest.md").decode("utf-8")
                    self.assertIn("第1章 — p.1-3", manifest)
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


class PackExportPreviewTest(unittest.TestCase):
    """`/api/packs/{pack_id}/export/preview`のHTTP契約テスト（route adapter層）。

    pure preview logic・DB orchestrationの詳細は
    tests/test_export_service.py の BuildPackExportPreviewTest へ移設した。
    ここではHTTPステータス・detail・JSON responseの外形のみを確認する
    （design.md §9: (1)〜(5)）。
    """

    def _payload(self, response) -> dict:
        return json.loads(response.body)

    def test_preview_returns_404_for_missing_pack(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            with patch("tsundokensaku.web.get_db_path", return_value=db_path):
                with self.assertRaises(HTTPException) as ctx:
                    api_preview_pack_export(9999)
                self.assertEqual(ctx.exception.status_code, 404)
                self.assertEqual(ctx.exception.detail, "資料が見つかりません")

    def test_preview_unknown_profile_returns_400_matching_export_api(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            with patch("tsundokensaku.web.get_db_path", return_value=db_path):
                created = self._payload(api_create_pack({"name": "資料"}))
                with self.assertRaises(HTTPException) as ctx:
                    api_preview_pack_export(created["id"], profile="unknown")
                self.assertEqual(ctx.exception.status_code, 400)
                self.assertEqual(ctx.exception.detail, "不明なエクスポートプロファイルです: unknown")

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

    def test_preview_success_returns_200_with_json_response_shape(self) -> None:
        # design.md §9 (3): preview成功時200・JSON response shape
        # （standard/profile双方）をTestClient経由で確認する。
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            with patch("tsundokensaku.web.get_db_path", return_value=db_path):
                client = TestClient(tsundokensaku_app)
                created = client.post("/api/packs", json={"name": "資料"}).json()

                standard_response = client.get(f"/api/packs/{created['id']}/export/preview")
                self.assertEqual(standard_response.status_code, 200)
                self.assertEqual(standard_response.headers["content-type"], "application/json")
                standard_body = standard_response.json()
                for key in ("estimation", "estimator", "book_count", "item_count", "total_pages", "estimated_chars", "estimated_tokens", "warnings"):
                    self.assertIn(key, standard_body)

                profile_response = client.get(f"/api/packs/{created['id']}/export/preview", params={"profile": "chat"})
                self.assertEqual(profile_response.status_code, 200)
                profile_body = profile_response.json()
                for key in ("profile", "file_count", "archive", "chunks"):
                    self.assertIn(key, profile_body)

    def test_preview_pdf_source_not_found_error_converts_to_404(self) -> None:
        # design.md §9 (4): previewでexport_service.build_pack_export_previewが
        # PdfSourceNotFoundErrorを送出した場合、api_preview_pack_exportが
        # 404 "PDF not found"へ変換すること（save_export_pdfと同じパターン）。
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            with patch("tsundokensaku.web.get_db_path", return_value=db_path):
                created = self._payload(api_create_pack({"name": "資料"}))

                with patch(
                    "tsundokensaku.export_service.build_pack_export_preview",
                    side_effect=PdfSourceNotFoundError("a.pdf"),
                ):
                    with self.assertRaises(HTTPException) as ctx:
                        api_preview_pack_export(created["id"])
                    self.assertEqual(ctx.exception.status_code, 404)
                    self.assertEqual(ctx.exception.detail, "PDF not found")

    def test_preview_missing_pdf_warning_keeps_200_via_test_client(self) -> None:
        # design.md §9 (5): 通常のmissing PDF warning経路がTestClient経由でも
        # 200を維持すること（HTTPExceptionへの逆戻りではない）。
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"
            books_dir.mkdir(parents=True)

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
            ):
                client = TestClient(tsundokensaku_app)
                created = client.post("/api/packs", json={"name": "資料"}).json()
                items = [{"pdf_path": "missing.pdf", "title": "消えた本", "pages": "1-3", "collapsed": False, "position": 0}]
                client.put(f"/api/packs/{created['id']}/items", json={"items": items})

                response = client.get(f"/api/packs/{created['id']}/export/preview")

                self.assertEqual(response.status_code, 200)
                body = response.json()
                self.assertEqual(len(body["warnings"]), 1)
                self.assertEqual(body["warnings"][0]["code"], "missing_pdf")


class _FakeUploadRequest:
    """request.body() だけを使う upload エンドポイント用の最小スタブ。"""

    def __init__(self, body: bytes) -> None:
        self._body = body

    async def body(self) -> bytes:
        return self._body


class PdfUploadHttpCharacterizationTest(unittest.TestCase):
    def test_upload_pdf_rejects_empty_filename_before_body_and_service(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            books_dir = Path(temp_dir) / "books"
            for filename in ("", "   "):
                with self.subTest(filename=filename), \
                        patch("tsundokensaku.web.get_books_dir", return_value=books_dir), \
                        patch(
                            "tsundokensaku.pdf_import_service.save_uploaded_pdf",
                            side_effect=AssertionError("service must not be used"),
                        ), \
                        patch.dict(os.environ, {"DEMO_MODE": "false"}):
                    response = asyncio.run(
                        upload_pdf(
                            request=None,
                            filename=filename,
                            relative_path="nested/valid.pdf",
                        )
                    )

                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.body, "filename が必要です".encode())

    def test_upload_pdf_rejects_empty_body(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            books_dir = Path(temp_dir) / "books"
            with patch("tsundokensaku.web.get_books_dir", return_value=books_dir), \
                    patch.dict(os.environ, {"DEMO_MODE": "false"}):
                response = asyncio.run(upload_pdf(request=_FakeUploadRequest(b""), filename="sample.pdf"))

            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.body, b"empty body")
            self.assertFalse((books_dir / "sample.pdf").exists())

    def test_upload_pdf_rejects_body_without_pdf_magic(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            books_dir = Path(temp_dir) / "books"
            with patch("tsundokensaku.web.get_books_dir", return_value=books_dir), \
                    patch.dict(os.environ, {"DEMO_MODE": "false"}):
                response = asyncio.run(
                    upload_pdf(request=_FakeUploadRequest(b"not a PDF"), filename="sample.pdf")
                )

            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.body, "PDF 以外は受け付けません".encode())
            self.assertFalse((books_dir / "sample.pdf").exists())

    def test_upload_pdf_uses_filename_when_relative_path_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            books_dir = Path(temp_dir) / "books"
            with patch("tsundokensaku.web.get_books_dir", return_value=books_dir), \
                    patch.dict(os.environ, {"DEMO_MODE": "false"}):
                response = asyncio.run(
                    upload_pdf(
                        request=_FakeUploadRequest(b"%PDF-1.4 lowercase"),
                        filename="sample.pdf",
                        relative_path="",
                    )
                )

            saved = books_dir / "sample.pdf"
            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.body.decode(), str(saved))
            self.assertEqual(saved.read_bytes(), b"%PDF-1.4 lowercase")

    def test_upload_pdf_passes_blank_relative_path_to_storage_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            books_dir = Path(temp_dir) / "books"
            with patch("tsundokensaku.web.get_books_dir", return_value=books_dir), \
                    patch.dict(os.environ, {"DEMO_MODE": "false"}):
                response = asyncio.run(
                    upload_pdf(
                        request=_FakeUploadRequest(b"%PDF-1.4 blank-relative-path"),
                        filename="sample.pdf",
                        relative_path=" ",
                    )
                )

            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.body, "PDF ファイルのみ受け付けます".encode())
            self.assertFalse((books_dir / "sample.pdf").exists())

    def test_upload_pdf_saves_nested_relative_path_and_returns_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            books_dir = Path(temp_dir) / "books"
            with patch("tsundokensaku.web.get_books_dir", return_value=books_dir), \
                    patch.dict(os.environ, {"DEMO_MODE": "false"}):
                response = asyncio.run(
                    upload_pdf(
                        request=_FakeUploadRequest(b"%PDF-1.4 nested"),
                        filename="original.pdf",
                        relative_path="nested/path/saved.pdf",
                    )
                )

            saved = books_dir / "nested" / "path" / "saved.pdf"
            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.body.decode(), str(saved))
            self.assertEqual(saved.read_bytes(), b"%PDF-1.4 nested")
            self.assertFalse((books_dir / "original.pdf").exists())

    def test_upload_pdf_preserves_supported_filename_characters(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            books_dir = Path(temp_dir) / "books"
            cases = (
                ("sample.PDF", b"%PDF-1.4 uppercase"),
                ("読書メモ.pdf", b"%PDF-1.4 unicode"),
                (" 読書 メモ.pdf", b"%PDF-1.4 spaces"),
            )
            with patch("tsundokensaku.web.get_books_dir", return_value=books_dir), \
                    patch.dict(os.environ, {"DEMO_MODE": "false"}):
                for filename, content in cases:
                    with self.subTest(filename=filename):
                        response = asyncio.run(upload_pdf(request=_FakeUploadRequest(content), filename=filename))
                        saved = books_dir / filename
                        self.assertEqual(response.status_code, 201)
                        self.assertEqual(response.body.decode(), str(saved))
                        self.assertEqual(saved.read_bytes(), content)

    def test_upload_pdf_rejects_space_after_pdf_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            books_dir = Path(temp_dir) / "books"
            with patch("tsundokensaku.web.get_books_dir", return_value=books_dir), \
                    patch.dict(os.environ, {"DEMO_MODE": "false"}):
                response = asyncio.run(
                    upload_pdf(
                        request=_FakeUploadRequest(b"%PDF-1.4 trailing-space"),
                        filename="sample.pdf ",
                    )
                )

            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.body, "PDF ファイルのみ受け付けます".encode())
            self.assertFalse((books_dir / "sample.pdf ").exists())


class ScrapboxImportHttpCharacterizationTest(unittest.TestCase):
    def test_scrapbox_import_redirects_when_source_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            missing = Path(temp_dir) / "missing.json"

            with patch("tsundokensaku.web.get_db_path", return_value=db_path), \
                    patch.dict(os.environ, {"DEMO_MODE": "false"}):
                response = import_scrapbox_json(export_json_path=str(missing))

            self.assertEqual(response.status_code, 303)
            location = unquote(response.headers["location"])
            self.assertEqual(location, "/settings?message=Scrapbox の export JSON が見つかりませんでした")

    def test_scrapbox_import_explicit_source_success_redirect_and_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            source = root / "source.json"
            cache_path = root / "cache.json"
            source.write_text('{"pages":[]}', encoding="utf-8")
            result = scrapbox_import_service.ScrapboxImportResult(
                imported_memos=3,
                imported_kindle_books=2,
            )

            with patch("tsundokensaku.web.get_db_path", return_value=db_path), \
                    patch("tsundokensaku.web.SCRAPBOX_EXPORT_CACHE", cache_path), \
                    patch(
                        "tsundokensaku.web.scrapbox_import_service.import_scrapbox_export_file",
                        return_value=result,
                    ) as import_mock, \
                    patch.dict(os.environ, {"DEMO_MODE": "false"}):
                response = import_scrapbox_json(export_json_path=str(source))

            self.assertEqual(response.status_code, 303)
            location = unquote(response.headers["location"])
            self.assertEqual(location, "/settings?message=Scrapbox JSON を同期しました: メモ 3 件 / Kindle 2 件 (source.json)")
            import_mock.assert_called_once_with(source, cache_path=cache_path, db_path=db_path)

    def test_scrapbox_import_uses_default_source_when_query_blank(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            source = root / "default.json"
            cache_path = root / "cache.json"
            source.write_text('{"pages":[]}', encoding="utf-8")

            with patch("tsundokensaku.web.get_db_path", return_value=db_path), \
                    patch("tsundokensaku.web.SCRAPBOX_EXPORT_CACHE", cache_path), \
                    patch("tsundokensaku.web.find_export_json", return_value=source) as find_mock, \
                    patch(
                        "tsundokensaku.web.scrapbox_import_service.import_scrapbox_export_file",
                        return_value=scrapbox_import_service.ScrapboxImportResult(1, 0),
                    ) as import_mock, \
                    patch.dict(os.environ, {"DEMO_MODE": "false"}):
                response = import_scrapbox_json(export_json_path="   ")

            self.assertEqual(response.status_code, 303)
            find_mock.assert_called_once_with(web.PROJECT_ROOT)
            import_mock.assert_called_once_with(source, cache_path=cache_path, db_path=db_path)
            self.assertIn("default.json", unquote(response.headers["location"]))

    def test_scrapbox_import_does_not_rewrite_when_source_is_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            cache_path = root / "cache.json"
            cache_path.write_text('{"pages":[]}', encoding="utf-8")
            before_mtime = cache_path.stat().st_mtime_ns

            with patch("tsundokensaku.web.get_db_path", return_value=db_path), \
                    patch("tsundokensaku.web.SCRAPBOX_EXPORT_CACHE", cache_path), \
                    patch(
                        "tsundokensaku.web.scrapbox_import_service.import_scrapbox_export_file",
                        return_value=scrapbox_import_service.ScrapboxImportResult(0, 0),
                    ) as import_mock, \
                    patch.dict(os.environ, {"DEMO_MODE": "false"}):
                response = import_scrapbox_json(export_json_path=str(cache_path))

            self.assertEqual(response.status_code, 303)
            self.assertEqual(cache_path.stat().st_mtime_ns, before_mtime)
            import_mock.assert_called_once_with(cache_path, cache_path=cache_path, db_path=db_path)

    def test_scrapbox_import_demo_mode_does_not_touch_db_or_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            cache_path = Path(temp_dir) / "cache.json"
            with patch("tsundokensaku.web.get_db_path", return_value=db_path), \
                    patch("tsundokensaku.web.SCRAPBOX_EXPORT_CACHE", cache_path), \
                    patch(
                        "tsundokensaku.web.scrapbox_import_service.import_scrapbox_export_file",
                        side_effect=AssertionError("service must not be used"),
                    ), \
                    patch.dict(os.environ, {"DEMO_MODE": "true"}):
                response = import_scrapbox_json(export_json_path="")

            self.assertEqual(response.status_code, 303)
            self.assertIn("デモモードのため無効です", unquote(response.headers["location"]))
            self.assertFalse(db_path.exists())
            self.assertFalse(cache_path.exists())

    def test_scrapbox_upload_rejects_empty_filename_before_body_and_import(self) -> None:
        for filename in ("", "   "):
            with self.subTest(filename=filename), \
                    patch(
                        "tsundokensaku.web.scrapbox_import_service.import_scrapbox_export_bytes",
                        side_effect=AssertionError("service must not be used"),
                    ), \
                    patch.dict(os.environ, {"DEMO_MODE": "false"}):
                response = asyncio.run(upload_scrapbox_json(request=None, filename=filename))

            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.body, "filename が必要です".encode())

    def test_scrapbox_upload_rejects_non_json_extension_before_body_and_import(self) -> None:
        with patch(
            "tsundokensaku.web.scrapbox_import_service.import_scrapbox_export_bytes",
            side_effect=AssertionError("service must not be used"),
        ), \
                patch.dict(os.environ, {"DEMO_MODE": "false"}):
            response = asyncio.run(upload_scrapbox_json(request=None, filename="sample.txt"))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.body, "JSON ファイルのみ受け付けます".encode())

    def test_scrapbox_upload_accepts_uppercase_json_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            content = b'{"pages":[]}'
            with patch("tsundokensaku.web.get_db_path", return_value=db_path), \
                    patch(
                        "tsundokensaku.web.scrapbox_import_service.import_scrapbox_export_bytes",
                        return_value=scrapbox_import_service.ScrapboxImportResult(4, 1),
                    ) as import_mock, \
                    patch("tsundokensaku.web.SCRAPBOX_EXPORT_CACHE", Path(temp_dir) / "cache.json"), \
                    patch.dict(os.environ, {"DEMO_MODE": "false"}):
                response = asyncio.run(upload_scrapbox_json(request=_FakeUploadRequest(content), filename="sample.JSON"))

            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.body, "Scrapbox JSON を同期しました: メモ 4 件 / Kindle 1 件 (sample.JSON)".encode())
            self.assertEqual(response.media_type, "text/plain")
            import_mock.assert_called_once_with(content, cache_path=Path(temp_dir) / "cache.json", db_path=db_path)

    def test_scrapbox_upload_rejects_empty_body_before_import(self) -> None:
        with patch(
            "tsundokensaku.web.scrapbox_import_service.import_scrapbox_export_bytes",
            side_effect=AssertionError("service must not be used"),
        ), \
                patch.dict(os.environ, {"DEMO_MODE": "false"}):
            response = asyncio.run(upload_scrapbox_json(request=_FakeUploadRequest(b""), filename="sample.json"))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.body, b"empty body")

    def test_scrapbox_upload_success_passes_content_and_db_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            content = b'{"pages":[{"title":"a","lines":[]}]}'
            with patch("tsundokensaku.web.get_db_path", return_value=db_path), \
                    patch(
                        "tsundokensaku.web.scrapbox_import_service.import_scrapbox_export_bytes",
                        return_value=scrapbox_import_service.ScrapboxImportResult(1, 0),
                    ) as import_mock, \
                    patch("tsundokensaku.web.SCRAPBOX_EXPORT_CACHE", Path(temp_dir) / "cache.json"), \
                    patch.dict(os.environ, {"DEMO_MODE": "false"}):
                response = asyncio.run(upload_scrapbox_json(request=_FakeUploadRequest(content), filename="sample.json"))

            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.body, "Scrapbox JSON を同期しました: メモ 1 件 / Kindle 0 件 (sample.json)".encode())
            self.assertEqual(response.media_type, "text/plain")
            import_mock.assert_called_once_with(content, cache_path=Path(temp_dir) / "cache.json", db_path=db_path)

    def test_scrapbox_upload_import_exception_returns_400_plain_text(self) -> None:
        with patch("tsundokensaku.web.scrapbox_import_service.import_scrapbox_export_bytes", side_effect=ValueError("bad json")), \
                patch.dict(os.environ, {"DEMO_MODE": "false"}):
            response = asyncio.run(upload_scrapbox_json(request=_FakeUploadRequest(b"bad"), filename="sample.json"))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.body, b"bad json")
        self.assertEqual(response.media_type, "text/plain")

    def test_scrapbox_upload_demo_mode_does_not_read_body_or_import(self) -> None:
        with patch(
            "tsundokensaku.web.scrapbox_import_service.import_scrapbox_export_bytes",
            side_effect=AssertionError("service must not be used"),
        ), \
                patch.dict(os.environ, {"DEMO_MODE": "true"}):
            response = asyncio.run(upload_scrapbox_json(request=None, filename="sample.json"))

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.body, b"Upload is disabled in demo mode.")


class ConfigResolutionTest(unittest.TestCase):
    """R2（設定・環境変数の解決）の現在挙動を固定する characterization test。

    config.py への切り出し前の挙動そのものを固定する（望ましい仕様への変更ではない）。
    """

    def test_get_books_dir_uses_env_var_as_is(self) -> None:
        with patch.dict(os.environ, {"BOOKS_DIR": "/tmp/example-books"}):
            self.assertEqual(get_books_dir(), Path("/tmp/example-books"))

    def test_get_books_dir_keeps_relative_env_var_as_is(self) -> None:
        with patch.dict(os.environ, {"BOOKS_DIR": "./relative/books"}):
            self.assertEqual(get_books_dir(), Path("./relative/books"))

    def test_get_books_dir_defaults_when_unset(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(get_books_dir(), Path("data/books"))

    def test_get_db_path_uses_env_var_dir_with_fixed_filename(self) -> None:
        with patch.dict(os.environ, {"DB_DIR": "/tmp/example-db"}):
            self.assertEqual(get_db_path(), Path("/tmp/example-db/index.db"))

    def test_get_db_path_keeps_relative_env_var_as_is(self) -> None:
        with patch.dict(os.environ, {"DB_DIR": "./relative/db"}):
            self.assertEqual(get_db_path(), Path("relative/db/index.db"))

    def test_get_db_path_defaults_when_unset(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(get_db_path(), Path("data/index.db"))

    def test_get_pdf_export_save_dir_none_when_unset(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(get_pdf_export_save_dir())

    def test_get_pdf_export_save_dir_none_when_blank(self) -> None:
        with patch.dict(os.environ, {"PDF_EXPORT_SAVE_DIR": "   "}):
            self.assertIsNone(get_pdf_export_save_dir())

    def test_get_pdf_export_save_dir_expands_user(self) -> None:
        with patch.dict(os.environ, {"PDF_EXPORT_SAVE_DIR": "~/exports"}):
            self.assertEqual(get_pdf_export_save_dir(), Path("~/exports").expanduser())

    def test_get_pdf_export_save_dir_keeps_absolute_path_as_is(self) -> None:
        with patch.dict(os.environ, {"PDF_EXPORT_SAVE_DIR": "/mnt/c/exports"}):
            self.assertEqual(get_pdf_export_save_dir(), Path("/mnt/c/exports"))

    def test_update_env_setting_updates_existing_key_and_keeps_other_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            env_file.write_text(
                "# comment line\n"
                "BOOKS_DIR=./data/books\n"
                "\n"
                "DB_DIR=./data\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                update_env_setting("DB_DIR", "/tmp/new-db", env_file=env_file)
                self.assertEqual(os.environ["DB_DIR"], "/tmp/new-db")
            self.assertEqual(
                env_file.read_text(encoding="utf-8"),
                "# comment line\n"
                "BOOKS_DIR=./data/books\n"
                "\n"
                "DB_DIR=/tmp/new-db\n",
            )

    def test_update_env_setting_appends_new_key_with_blank_line_separator(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            env_file.write_text("BOOKS_DIR=./data/books\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                update_env_setting("NEW_KEY", "new-value", env_file=env_file)
                self.assertEqual(os.environ["NEW_KEY"], "new-value")
            self.assertEqual(
                env_file.read_text(encoding="utf-8"),
                "BOOKS_DIR=./data/books\n\nNEW_KEY=new-value\n",
            )

    def test_update_env_setting_creates_file_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            self.assertFalse(env_file.exists())
            with patch.dict(os.environ, {}, clear=True):
                update_env_setting("PDF_EXPORT_SAVE_DIR", "/tmp/out", env_file=env_file)
            self.assertEqual(env_file.read_text(encoding="utf-8"), "PDF_EXPORT_SAVE_DIR=/tmp/out\n")

    def test_update_env_setting_ignores_commented_key_and_appends_instead(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            env_file.write_text("# BOOKS_DIR=old-commented-out\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                update_env_setting("BOOKS_DIR", "/tmp/new-books", env_file=env_file)
            self.assertEqual(
                env_file.read_text(encoding="utf-8"),
                "# BOOKS_DIR=old-commented-out\n\nBOOKS_DIR=/tmp/new-books\n",
            )

    def test_update_env_setting_preserves_value_with_spaces_and_symbols(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            env_file.write_text("PDF_EXPORT_SAVE_DIR=\n", encoding="utf-8")
            value = "/mnt/c/Users/name/Google Drive/PDF切り出し (2026)"
            with patch.dict(os.environ, {}, clear=True):
                update_env_setting("PDF_EXPORT_SAVE_DIR", value, env_file=env_file)
                self.assertEqual(os.environ["PDF_EXPORT_SAVE_DIR"], value)
            self.assertEqual(
                env_file.read_text(encoding="utf-8"),
                f"PDF_EXPORT_SAVE_DIR={value}\n",
            )


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

    def test_read_connection_closes_before_event_connection_opens(self) -> None:
        # R8 PR2 characterization test（設計書§19.2-8 / design.md決定3）:
        # pack読み取り接続はレスポンス生成前にcloseされ、eventは生成成功後の
        # 別接続で記録される。connect/close/record_export_eventの呼び出し順を
        # spyで直接固定する。
        events: list[str] = []
        original_connect = web.connect
        original_record = web.record_export_event

        class _ConnectionCloseSpy:
            # sqlite3.Connection の close はインスタンス属性として
            # 上書きできない（read-only）ため、委譲プロキシで代替する。
            def __init__(self, real_connection) -> None:
                object.__setattr__(self, "_real", real_connection)

            def close(self) -> None:
                events.append("close")
                self._real.close()

            def __getattr__(self, name):
                return getattr(self._real, name)

        def spy_connect(db_path):
            events.append("connect")
            return _ConnectionCloseSpy(original_connect(db_path))

        def spy_record(*args, **kwargs):
            events.append("record_export_event")
            return original_record(*args, **kwargs)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"
            self._make_pdf(books_dir / "a.pdf")

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
            ):
                created = self._payload(api_create_pack({"name": "接続順序テスト"}))
                items = [{"pdf_path": "a.pdf", "title": "本A", "pages": "1-2", "collapsed": False, "position": 0}]
                self._payload(api_replace_pack_items(created["id"], {"items": items}))

                with (
                    patch("tsundokensaku.web.connect", side_effect=spy_connect),
                    patch("tsundokensaku.web.record_export_event", side_effect=spy_record),
                ):
                    response = api_export_pack(created["id"], format="pdf")
                    self.assertEqual(response.status_code, 200)

        # pack読み取り接続の connect/close が、event記録用の2つ目の
        # connect/record_export_event/close より必ず先行する
        self.assertEqual(events, ["connect", "close", "connect", "record_export_event", "close"])

    def test_preview_never_records_event(self) -> None:
        # R8 PR2 characterization test（設計書§19.2-9後半）:
        # previewは（成功・警告いずれの場合も）export_eventsへ記録しない。
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"
            self._make_pdf(books_dir / "a.pdf")

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
            ):
                created = self._payload(api_create_pack({"name": "プレビュー記録テスト"}))
                items = [{"pdf_path": "a.pdf", "title": "本A", "pages": "1-2", "collapsed": False, "position": 0}]
                self._payload(api_replace_pack_items(created["id"], {"items": items}))

                before = self._count_events(db_path)
                api_preview_pack_export(created["id"])
                api_preview_pack_export(created["id"], profile="chat")
                api_preview_pack_export(created["id"], profile="chapter")
                after = self._count_events(db_path)

            self.assertEqual(after, before)

    def test_missing_pages_and_missing_pdf_and_invalid_range_do_not_record(self) -> None:
        # R8 PR2 characterization test（設計書§19.2-9前半の拡充）:
        # 空pack以外の失敗経路（pages未指定・PDF不在・不正pages範囲）でも
        # eventが記録されないことを固定する。
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"
            self._make_pdf(books_dir / "a.pdf")

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
            ):
                missing_pages_pack = self._payload(api_create_pack({"name": "ページ未指定"}))
                self._payload(api_replace_pack_items(missing_pages_pack["id"], {"items": [
                    {"pdf_path": "a.pdf", "title": "本A", "pages": "", "collapsed": False, "position": 0},
                ]}))
                before = self._count_events(db_path)
                with self.assertRaises(HTTPException):
                    api_export_pack(missing_pages_pack["id"], format="pdf")
                self.assertEqual(self._count_events(db_path), before)

                missing_pdf_pack = self._payload(api_create_pack({"name": "PDF欠損"}))
                self._payload(api_replace_pack_items(missing_pdf_pack["id"], {"items": [
                    {"pdf_path": "does-not-exist.pdf", "title": "消えた本", "pages": "1", "collapsed": False, "position": 0},
                ]}))
                before = self._count_events(db_path)
                with self.assertRaises(HTTPException):
                    api_export_pack(missing_pdf_pack["id"], format="pdf")
                self.assertEqual(self._count_events(db_path), before)

                invalid_range_pack = self._payload(api_create_pack({"name": "不正範囲"}))
                self._payload(api_replace_pack_items(invalid_range_pack["id"], {"items": [
                    {"pdf_path": "a.pdf", "title": "本A", "pages": "99-100", "collapsed": False, "position": 0},
                ]}))
                before = self._count_events(db_path)
                with self.assertRaises(HTTPException):
                    api_export_pack(invalid_range_pack["id"], format="pdf")
                self.assertEqual(self._count_events(db_path), before)

    def test_chapter_export_records_original_item_snapshot_not_fragments(self) -> None:
        # R8 PR2 characterization test（設計書§19.2-11後半）:
        # chapterプロファイルでexportしても、items_jsonにはchapter fragment
        # ではなく元のpack item（position順・4フィールド）がそのまま記録される。
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"
            self._make_pdf(books_dir / "a.pdf", page_count=6)

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
                patch.dict(os.environ, {"TSUNDOKENSAKU_CHAPTER_MAX_PAGES_PER_FILE": "2"}),
            ):
                created = self._payload(api_create_pack({"name": "章分割記録テスト"}))
                items = [{"pdf_path": "a.pdf", "title": "本A", "pages": "1-6", "collapsed": False, "position": 0}]
                self._payload(api_replace_pack_items(created["id"], {"items": items}))

                response = api_export_pack(created["id"], profile="chapter")
                self.assertEqual(response.status_code, 200)
                # アウトラインなしのため連続ページで3ファイルに分割されるが、
                # 記録される items は分割前の元項目1件のままであるはず
                with zipfile.ZipFile(BytesIO(response.body)) as archive:
                    self.assertEqual(len(archive.namelist()), 4)  # manifest + 3チャンク

            import sqlite3
            conn = sqlite3.connect(str(db_path))
            try:
                row = conn.execute("SELECT items_json FROM export_events ORDER BY id DESC LIMIT 1").fetchone()
            finally:
                conn.close()
            payload = json.loads(row[0])
            self.assertEqual(len(payload["items"]), 1)
            item = payload["items"][0]
            self.assertEqual(item["pdf_path"], "a.pdf")
            self.assertEqual(item["title"], "本A")
            self.assertEqual(item["pages"], "1-6")
            self.assertEqual(item["position"], 0)


class ArtifactRemovalApiTest(unittest.TestCase):
    def test_artifact_routes_are_not_registered_and_export_events_remains_available(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            connection = connect(db_path)
            initialize(connection)
            connection.execute(
                "INSERT INTO export_events(exported_at, pack_id, pack_name, profile, format, items_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "2026-07-20T00:00:00+00:00",
                    None,
                    "履歴資料",
                    "standard",
                    "pdf",
                    '{"version": 1, "items": []}',
                ),
            )
            connection.commit()
            connection.close()

            with patch("tsundokensaku.web.get_db_path", return_value=db_path):
                client = TestClient(tsundokensaku_app)
                self.assertEqual(client.get("/artifacts").status_code, 404)
                self.assertEqual(client.get("/api/artifacts").status_code, 404)
                self.assertEqual(client.post("/api/artifacts", json={}).status_code, 404)
                self.assertEqual(client.get("/api/artifacts/1").status_code, 404)
                response = client.get("/api/export-events")
                openapi_paths = client.get("/openapi.json").json()["paths"]

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["export_events"][0]["pack_name"], "履歴資料")
            self.assertIn("/api/export-events", openapi_paths)
            self.assertNotIn("/api/artifacts", openapi_paths)
            self.assertNotIn("/api/artifacts/{artifact_id}", openapi_paths)




if __name__ == "__main__":
    unittest.main()
