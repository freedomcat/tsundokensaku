import os
import tempfile
import unittest
from datetime import datetime
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo
import json
import zipfile

from pypdf import PdfWriter

from tsundokensaku import export_service
from tsundokensaku import pdf_export
from tsundokensaku.pdf_export import PdfSourceNotFoundError
from tsundokensaku.database import PackItemRecord, PackRecord, connect, initialize, upsert_book
from tsundokensaku.export_stats import ItemStats
from tsundokensaku.export_service import (
    build_export_preview_payload,
    build_export_preview_payload_for_profile,
    build_export_preview_warnings,
    prepare_json_export,
)
from tsundokensaku.token_estimate import TextStats
from tsundokensaku.web import api_create_pack, api_export_pack, api_replace_pack_items


class ResolveExternalProfileTest(unittest.TestCase):
    """request policyの非HTTP契約。design.md §3。

    export_service.resolve_external_profile はHTTP statusを一切知らず、
    不明・非公開なprofile名を ValueError(profile_name) として送出する。
    """

    def _payload(self, response) -> dict:
        return json.loads(response.body)

    def test_unknown_profile_raises_value_error_with_name(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            export_service.resolve_external_profile("unknown")
        self.assertEqual(str(ctx.exception), "unknown")

    def test_none_resolves_to_standard(self) -> None:
        profile = export_service.resolve_external_profile(None)
        self.assertEqual(profile.name, "standard")

    def test_standard_chat_chapter_are_resolvable(self) -> None:
        for name in ("standard", "chat", "chapter"):
            profile = export_service.resolve_external_profile(name)
            self.assertEqual(profile.name, name)

    def test_unlisted_but_resolvable_profile_raises_same_value_error_as_unknown(self) -> None:
        # R8 PR2 characterization test（設計書§19.2-4 / design.md決定5）の直接テスト版:
        # resolve_profile自体が知らない完全未知のprofile名と、resolve_profileには
        # 存在するがEXTERNALLY_AVAILABLE_EXPORT_PROFILESに含まれないprofile名は、
        # 異なる動作を新設するわけではなく、どちらも同一のValueError(name)になる。
        with patch("tsundokensaku.export_service.EXTERNALLY_AVAILABLE_EXPORT_PROFILES", frozenset({"chat", "chapter"})):
            with self.assertRaises(ValueError) as ctx:
                export_service.resolve_external_profile("standard")
            self.assertEqual(str(ctx.exception), "standard")

    def test_old_profile_name_notebooklm_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            export_service.resolve_external_profile("notebooklm")


class ResolveExportFormatTest(unittest.TestCase):
    """request policyの非HTTP契約。design.md §3。

    export_service.resolve_export_format はHTTP statusを一切知らず、
    現行HTTP detailと同一メッセージのValueErrorを送出する。
    """

    def test_format_none_defaults_to_profile_primary_format(self) -> None:
        chat = export_service.resolve_external_profile("chat")
        self.assertEqual(export_service.resolve_export_format(chat, None), "md")

    def test_format_none_defaults_to_pdf_for_standard(self) -> None:
        standard = export_service.resolve_external_profile("standard")
        self.assertEqual(export_service.resolve_export_format(standard, None), "pdf")

    def test_standard_accepts_pdf_md_json(self) -> None:
        standard = export_service.resolve_external_profile("standard")
        for fmt in ("pdf", "md", "json"):
            self.assertEqual(export_service.resolve_export_format(standard, fmt), fmt)

    def test_invalid_format_raises_value_error(self) -> None:
        standard = export_service.resolve_external_profile("standard")
        with self.assertRaises(ValueError) as ctx:
            export_service.resolve_export_format(standard, "txt")
        self.assertEqual(str(ctx.exception), "format は pdf, md, または json を指定してください")

    def test_conflicting_format_for_chat_raises_value_error(self) -> None:
        chat = export_service.resolve_external_profile("chat")
        with self.assertRaises(ValueError) as ctx:
            export_service.resolve_export_format(chat, "pdf")
        self.assertEqual(str(ctx.exception), "profile=chat では format=md のみ指定できます")

    def test_conflicting_format_for_chapter_raises_value_error(self) -> None:
        chapter = export_service.resolve_external_profile("chapter")
        with self.assertRaises(ValueError) as ctx:
            export_service.resolve_export_format(chapter, "md")
        self.assertEqual(str(ctx.exception), "profile=chapter では format=pdf のみ指定できます")


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

    def test_seven_base_stat_fields_exact_values(self) -> None:
        # R8 PR2 characterization test（設計書§19.2-2）: _preview_base_stats
        # 相当の7 field（estimation, estimator, book_count, item_count,
        # total_pages, estimated_chars, estimated_tokens）を同時に固定する。
        stats = [
            ItemStats(item=self._item(1, pdf_path="a.pdf", pages="1-2"), page_numbers=[1, 2], stats=TextStats(cjk_chars=10, other_chars=0), unindexed_pages=0, missing_pdf=False),
            ItemStats(item=self._item(2, pdf_path="a.pdf", pages="5"), page_numbers=[5], stats=TextStats(cjk_chars=0, other_chars=4), unindexed_pages=0, missing_pdf=False),
        ]
        payload = build_export_preview_payload(stats)
        self.assertEqual(
            {key: payload[key] for key in ("estimation", "estimator", "book_count", "item_count", "total_pages", "estimated_chars", "estimated_tokens")},
            {
                "estimation": "approximate",
                "estimator": "char-class-v1",
                "book_count": 1,
                "item_count": 2,
                "total_pages": 3,
                "estimated_chars": 14,
                # cjk_chars合計10 x 1.0 + other_chars合計4 x 0.25 = 11.0 -> ceil(11.0) = 11
                "estimated_tokens": 11,
            },
        )

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


class ExportPreviewWarningContractTest(unittest.TestCase):
    """R8 PR2 characterization test（設計書§19.2-1）。

    build_export_preview_warnings の4種類のwarning codeそれぞれのexact
    dict、同一項目内の優先順位（missing_pdf > missing_pages > invalid_pages
    > unindexed_pages）の全組み合わせ、複数項目でのposition順、item
    warning→plan warningの連結順を、移動前の現状値として固定する。
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

    def test_missing_pdf_warning_exact_dict(self) -> None:
        stats = [ItemStats(item=self._item(1, title="消えた本"), page_numbers=[], stats=TextStats(0, 0), unindexed_pages=0, missing_pdf=True)]
        warnings = build_export_preview_warnings(stats)
        self.assertEqual(warnings, [{"code": "missing_pdf", "item_id": 1, "message": "「消えた本」はPDFファイルが見つかりません"}])

    def test_missing_pages_warning_exact_dict(self) -> None:
        stats = [ItemStats(item=self._item(1, pages="", title="本A"), page_numbers=[], stats=TextStats(0, 0), unindexed_pages=0, missing_pdf=False)]
        warnings = build_export_preview_warnings(stats)
        self.assertEqual(warnings, [{"code": "missing_pages", "item_id": 1, "message": "「本A」はページが指定されていません"}])

    def test_invalid_pages_warning_exact_dict(self) -> None:
        # pages は非空だが page_numbers が空 = ページ指定を解釈できなかった現状の表現
        stats = [ItemStats(item=self._item(1, pages="???", title="本A"), page_numbers=[], stats=TextStats(0, 0), unindexed_pages=0, missing_pdf=False)]
        warnings = build_export_preview_warnings(stats)
        self.assertEqual(warnings, [{"code": "invalid_pages", "item_id": 1, "message": "「本A」のページ指定を解釈できませんでした"}])

    def test_unindexed_pages_warning_exact_dict(self) -> None:
        stats = [ItemStats(item=self._item(1, pages="1-3", title="本A"), page_numbers=[1, 2, 3], stats=TextStats(0, 0), unindexed_pages=3, missing_pdf=False)]
        warnings = build_export_preview_warnings(stats)
        self.assertEqual(warnings, [{"code": "unindexed_pages", "item_id": 1, "message": "「本A」は未インデックスのため3ページ分を概算に含めていません"}])

    def test_missing_pdf_takes_priority_over_invalid_pages(self) -> None:
        stats = [ItemStats(item=self._item(1, pages="???"), page_numbers=[], stats=TextStats(0, 0), unindexed_pages=0, missing_pdf=True)]
        warnings = build_export_preview_warnings(stats)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["code"], "missing_pdf")

    def test_missing_pdf_takes_priority_over_unindexed_pages(self) -> None:
        stats = [ItemStats(item=self._item(1, pages="1-3"), page_numbers=[1, 2, 3], stats=TextStats(0, 0), unindexed_pages=3, missing_pdf=True)]
        warnings = build_export_preview_warnings(stats)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["code"], "missing_pdf")

    def test_missing_pages_takes_priority_over_invalid_pages(self) -> None:
        # pages="" かつ page_numbers=[] の組み合わせは実運用では両方の条件を満たすが、
        # missing_pages 判定（pages.strip() が空）が invalid_pages 判定より先に評価される
        stats = [ItemStats(item=self._item(1, pages=""), page_numbers=[], stats=TextStats(0, 0), unindexed_pages=0, missing_pdf=False)]
        warnings = build_export_preview_warnings(stats)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["code"], "missing_pages")

    def test_missing_pages_takes_priority_over_unindexed_pages(self) -> None:
        stats = [ItemStats(item=self._item(1, pages=""), page_numbers=[], stats=TextStats(0, 0), unindexed_pages=5, missing_pdf=False)]
        warnings = build_export_preview_warnings(stats)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["code"], "missing_pages")

    def test_invalid_pages_takes_priority_over_unindexed_pages(self) -> None:
        stats = [ItemStats(item=self._item(1, pages="???"), page_numbers=[], stats=TextStats(0, 0), unindexed_pages=5, missing_pdf=False)]
        warnings = build_export_preview_warnings(stats)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["code"], "invalid_pages")

    def test_multiple_items_warnings_follow_position_order(self) -> None:
        stats = [
            ItemStats(item=self._item(1, pages="", title="1件目"), page_numbers=[], stats=TextStats(0, 0), unindexed_pages=0, missing_pdf=False),
            ItemStats(item=self._item(2, title="2件目"), page_numbers=[], stats=TextStats(0, 0), unindexed_pages=0, missing_pdf=True),
            ItemStats(item=self._item(3, pages="1-3", title="3件目"), page_numbers=[1, 2, 3], stats=TextStats(0, 0), unindexed_pages=3, missing_pdf=False),
        ]
        warnings = build_export_preview_warnings(stats)
        self.assertEqual(
            [(w["item_id"], w["code"]) for w in warnings],
            [(1, "missing_pages"), (2, "missing_pdf"), (3, "unindexed_pages")],
        )

    def test_item_warnings_precede_plan_warnings_in_profile_payload(self) -> None:
        from tsundokensaku.export_profiles import ChatProfile

        item_stats = [
            ItemStats(
                item=self._item(1, title="未インデックス本"),
                page_numbers=[1, 2], stats=TextStats(cjk_chars=0, other_chars=0),
                unindexed_pages=2, missing_pdf=False,
            ),
            ItemStats(
                item=self._item(2, title="巨大本"),
                page_numbers=[1], stats=TextStats(cjk_chars=90_000, other_chars=0),
                unindexed_pages=0, missing_pdf=False,
            ),
        ]
        payload = build_export_preview_payload_for_profile(item_stats, ChatProfile(), pack_name="資料")

        codes = [warning["code"] for warning in payload["warnings"]]
        # item warning（unindexed_pages）が先、plan warning（item_exceeds_limit）が後
        self.assertEqual(codes, ["unindexed_pages", "item_exceeds_limit"])


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


class BuildPackExportPreviewTest(unittest.TestCase):
    """export_service.build_pack_export_preview のDB統合レベルのテスト。

    元 tests/test_web.py の PackExportPreviewTest から、DB read/plan進行を
    伴う正常系（HTTPステータスを扱わない部分）を移設した。pack不在→404、
    profile不明→400のHTTP変換はtests/test_web.pyに残す。
    """

    def _payload(self, response) -> dict:
        return json.loads(response.body)

    def test_pack_not_found_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            result = export_service.build_pack_export_preview(
                9999, profile_name=None, db_path=db_path, books_dir=Path(temp_dir)
            )
            self.assertIsNone(result)

    def test_preview_returns_empty_pack_warning_for_pack_with_no_items(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            with patch("tsundokensaku.web.get_db_path", return_value=db_path):
                created = self._payload(api_create_pack({"name": "空の資料"}))

            preview = export_service.build_pack_export_preview(
                created["id"], profile_name=None, db_path=db_path, books_dir=Path(temp_dir)
            )

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

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
            ):
                created = self._payload(api_create_pack({"name": "資料"}))
                items = [{"pdf_path": "a.pdf", "title": "本A", "pages": "1-3", "collapsed": False, "position": 0}]
                self._payload(api_replace_pack_items(created["id"], {"items": items}))

            preview = export_service.build_pack_export_preview(
                created["id"], profile_name=None, db_path=db_path, books_dir=books_dir
            )

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

            preview = export_service.build_pack_export_preview(
                created["id"], profile_name=None, db_path=db_path, books_dir=books_dir
            )

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

            preview = export_service.build_pack_export_preview(
                created["id"], profile_name=None, db_path=db_path, books_dir=books_dir
            )

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
            preview = export_service.build_pack_export_preview(
                created["id"], profile_name=None, db_path=db_path, books_dir=books_dir
            )

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

            preview = export_service.build_pack_export_preview(
                created["id"], profile_name=None, db_path=db_path, books_dir=books_dir
            )

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

            unspecified = export_service.build_pack_export_preview(
                created["id"], profile_name=None, db_path=db_path, books_dir=Path(temp_dir)
            )
            standard = export_service.build_pack_export_preview(
                created["id"], profile_name="standard", db_path=db_path, books_dir=Path(temp_dir)
            )

            self.assertEqual(unspecified, standard)
            for key in ("profile", "chunks", "file_count", "archive"):
                self.assertNotIn(key, unspecified)

    def test_preview_profile_chapter_returns_empty_pack_with_extended_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            with patch("tsundokensaku.web.get_db_path", return_value=db_path):
                created = self._payload(api_create_pack({"name": "資料"}))

            preview = export_service.build_pack_export_preview(
                created["id"], profile_name="chapter", db_path=db_path, books_dir=Path(temp_dir)
            )
            self.assertEqual(preview["profile"], "chapter")
            self.assertEqual(preview["file_count"], 0)
            self.assertEqual(preview["archive"], "zip")
            self.assertEqual(preview["chunks"], [])
            self.assertEqual(
                preview["warnings"],
                [{"code": "empty_pack", "item_id": None, "message": "この資料には資料項目がありません"}],
            )

    def test_preview_profile_chat_returns_empty_pack_with_extended_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            with patch("tsundokensaku.web.get_db_path", return_value=db_path):
                created = self._payload(api_create_pack({"name": "空の資料"}))

            preview = export_service.build_pack_export_preview(
                created["id"], profile_name="chat", db_path=db_path, books_dir=Path(temp_dir)
            )

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
                export_response = api_export_pack(created["id"], profile="chat")

            preview = export_service.build_pack_export_preview(
                created["id"], profile_name="chat", db_path=db_path, books_dir=books_dir
            )

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
            doc.set_toc([
                [1, "第1章", 1],
                [2, "1.1 導入", 2],
                [2, "1.2 基礎", 3],
                [1, "第2章", 4],
                [2, "2.1 応用", 5],
            ])
            doc.save(str(pdf_path))
            doc.close()

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

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
                patch.dict(os.environ, {"TSUNDOKENSAKU_CHAPTER_MAX_PAGES_PER_FILE": "4"}),
            ):
                created = self._payload(api_create_pack({"name": "資料"}))
                self._payload(api_replace_pack_items(created["id"], {"items": [
                    {"pdf_path": "a.pdf", "title": "本A", "pages": "1-6", "collapsed": False, "position": 0},
                ]}))

                preview = export_service.build_pack_export_preview(
                    created["id"], profile_name="chapter", db_path=db_path, books_dir=books_dir
                )

                self.assertEqual(preview["profile"], "chapter")
                self.assertEqual(preview["file_count"], 2)
                self.assertEqual(
                    [chunk["filename"] for chunk in preview["chunks"]],
                    ["01_本A_第1章_p1-3.pdf", "02_本A_第2章_p4-6.pdf"],
                )
                self.assertEqual(preview["chunks"][0]["items"][0]["label"], "第1章")
                self.assertEqual(preview["chunks"][1]["items"][0]["label"], "第2章")
                self.assertEqual(preview["chunks"][0]["items"][0]["pages"], "1-3")
                self.assertEqual(preview["chunks"][1]["items"][0]["pages"], "4-6")
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

                preview = export_service.build_pack_export_preview(
                    created["id"], profile_name="chapter", db_path=db_path, books_dir=books_dir
                )

                self.assertEqual(preview["file_count"], 3)
                self.assertEqual(preview["chunks"][0]["items"][0]["label"], "part1")
                self.assertEqual(preview["chunks"][1]["items"][0]["label"], "part2")
                codes = [warning["code"] for warning in preview["warnings"]]
                self.assertIn("no_outline_fallback", codes)


class PreviewChapterLoaderPdfSourceBoundaryTest(unittest.TestCase):
    """R8 PR2 characterization test（設計書§19.2-16）のPR3後の姿。

    PR3完了により、previewの chapter_loader は pdf_export.resolve_pdf_source
    （PdfSourceNotFoundErrorを送出する非HTTP関数）を直接使うようになった。
    HTTPExceptionはservice/callbackから一切送出されない。
    """

    def _payload(self, response) -> dict:
        return json.loads(response.body)

    def test_chapter_loader_is_not_invoked_for_missing_pdf_items(self) -> None:
        # export_profiles.py の ChapterProfile.split_items_with_warnings は
        # stats.missing_pdf な項目に対して chapter_loader
        # （内部でPdfSourceNotFoundErrorを送出しうる pdf_export.resolve_pdf_source
        # 経由）を呼ばず、そのままfragment化する（395行のショートサーキット）。
        # このため、PDF欠損項目単体ではchapter previewで例外へ到達しない
        # 現状を、chapter_loaderの呼び出し有無で直接固定する。
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            books_dir = Path(temp_dir) / "books"
            books_dir.mkdir()

            with patch("tsundokensaku.web.get_db_path", return_value=db_path):
                created = self._payload(api_create_pack({"name": "資料"}))
                items = [{"pdf_path": "missing.pdf", "title": "消えた本", "pages": "1-3", "collapsed": False, "position": 0}]
                self._payload(api_replace_pack_items(created["id"], {"items": items}))

            with patch("tsundokensaku.pdf_export.resolve_pdf_source") as resolve_spy:
                preview = export_service.build_pack_export_preview(
                    created["id"], profile_name="chapter", db_path=db_path, books_dir=books_dir
                )
                resolve_spy.assert_not_called()

            self.assertEqual(preview["profile"], "chapter")
            codes = [warning["code"] for warning in preview["warnings"]]
            self.assertIn("missing_pdf", codes)

    def test_chapter_preview_missing_pdf_item_keeps_normal_result_without_raising(self) -> None:
        # chapter previewでPDF欠損項目があっても、例外を送出せず既存のwarning
        # 契約（HTTPException/PdfSourceNotFoundErrorへの逆戻りではない）が
        # 維持されることを固定する。
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "index.db"
            books_dir = Path(temp_dir) / "books"
            books_dir.mkdir()

            with patch("tsundokensaku.web.get_db_path", return_value=db_path):
                created = self._payload(api_create_pack({"name": "資料"}))
                items = [{"pdf_path": "missing.pdf", "title": "消えた本", "pages": "1-3", "collapsed": False, "position": 0}]
                self._payload(api_replace_pack_items(created["id"], {"items": items}))

            # 例外を送出せず結果が返ることそのものがテスト対象
            preview = export_service.build_pack_export_preview(
                created["id"], profile_name="chapter", db_path=db_path, books_dir=books_dir
            )

            self.assertEqual(preview["file_count"], 1)
            warning = next(w for w in preview["warnings"] if w["code"] == "missing_pdf")
            self.assertIn("消えた本", warning["message"])

    def test_race_missing_pdf_source_propagates_pdf_source_not_found_error(self) -> None:
        # missing_pdfではないと判定された項目のPDFが、chapter_loader呼び出し
        # 時点で消失しているレースケースでは、PdfSourceNotFoundErrorが
        # build_pack_export_previewからそのまま伝播する（HTTPExceptionでは
        # ない）。web adapterがこれを捕捉して404へ変換する（design.md §6.2-5）。
        # chapter_loaderが実際に呼ばれるよう、chunk_limitを1へ絞り、
        # limitを超えるページ数（2ページ）の項目を用意する。
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "index.db"
            books_dir = root / "books"
            books_dir.mkdir(parents=True)
            pdf_path = books_dir / "a.pdf"
            writer = PdfWriter()
            for _ in range(2):
                writer.add_blank_page(width=72, height=72)
            with pdf_path.open("wb") as handle:
                writer.write(handle)

            with (
                patch("tsundokensaku.web.get_db_path", return_value=db_path),
                patch("tsundokensaku.web.get_books_dir", return_value=books_dir),
            ):
                created = self._payload(api_create_pack({"name": "資料"}))
                items = [{"pdf_path": "a.pdf", "title": "本A", "pages": "1-2", "collapsed": False, "position": 0}]
                self._payload(api_replace_pack_items(created["id"], {"items": items}))

            with (
                patch.dict(os.environ, {"TSUNDOKENSAKU_CHAPTER_MAX_PAGES_PER_FILE": "1"}),
                patch(
                    "tsundokensaku.pdf_export.resolve_pdf_source",
                    side_effect=PdfSourceNotFoundError("a.pdf"),
                ),
            ):
                with self.assertRaises(PdfSourceNotFoundError):
                    export_service.build_pack_export_preview(
                        created["id"], profile_name="chapter", db_path=db_path, books_dir=books_dir
                    )


class PrepareJsonExportTest(unittest.TestCase):
    """R8 PR4: JSON export準備（`prepare_json_export`）の生成契約。

    PR2で`tests/test_web.py`の`ExportJsonContractTest`がTestClient経由で
    固定していたJSON構造のexact値・空pack・PDF不在・pages不正でも生成
    される契約を、service関数を直接呼び出す形でここに引き継ぐ
    （design.md決定5）。HTTP契約（status/header）は`test_web.py`が担う。
    """

    def _pack(self, *, pack_id: int = 1, name: str = "資料") -> PackRecord:
        return PackRecord(
            id=pack_id,
            name=name,
            note="",
            created_at="2026-07-11T00:00:00.000Z",
            updated_at="2026-07-11T00:00:00.000Z",
        )

    def _item(
        self,
        item_id: int,
        *,
        pdf_path: str = "a.pdf",
        title: str = "本",
        pages: str = "1-2",
        collapsed: bool = False,
        position: int = 0,
        added_at: str = "2026-07-11T00:00:00.000Z",
    ) -> PackItemRecord:
        return PackItemRecord(
            id=item_id,
            pdf_path=pdf_path,
            title=title,
            pages=pages,
            collapsed=collapsed,
            position=position,
            added_at=added_at,
            updated_at=added_at,
        )

    def test_exact_structure_with_fixed_clock(self) -> None:
        # R8 PR2 characterization test（設計書§19.2-5）由来。UTF-8日本語、
        # key順、indent 2、LF、末尾改行なしを、service関数直接呼び出しで固定する。
        exported_at = datetime(2026, 8, 19, 9, 30, tzinfo=ZoneInfo("Asia/Tokyo"))
        pack = self._pack(name="日本語資料名")
        items = [self._item(1, pdf_path="a.pdf", title="本A", pages="1-2", added_at="2026-08-19T00:00:00.000Z")]

        prepared = prepare_json_export(pack, items, exported_at=exported_at)

        expected_content = (
            "{\n"
            '  "version": 3,\n'
            '  "name": "日本語資料名",\n'
            '  "items": [\n'
            "    {\n"
            '      "pdf_path": "a.pdf",\n'
            '      "title": "本A",\n'
            '      "pages": "1-2",\n'
            '      "collapsed": false,\n'
            '      "addedAt": "2026-08-19T00:00:00.000Z",\n'
            '      "position": 0\n'
            "    }\n"
            "  ]\n"
            "}"
        )
        self.assertEqual(prepared.content, expected_content.encode("utf-8"))
        self.assertFalse(prepared.content.endswith(b"\n"))
        self.assertEqual(prepared.filename, "日本語資料名_20260819.json")

    def test_empty_pack_returns_items_empty_list(self) -> None:
        exported_at = datetime(2026, 8, 19, 9, 30, tzinfo=ZoneInfo("Asia/Tokyo"))
        prepared = prepare_json_export(self._pack(name="空資料"), [], exported_at=exported_at)

        body = json.loads(prepared.content)
        self.assertEqual(body["items"], [])
        self.assertEqual(prepared.filename, "空資料_20260819.json")

    def test_missing_pdf_and_invalid_pages_included_without_validation(self) -> None:
        # JSON exportは検証を行わず、PDF不在・pages不正な項目もそのまま
        # 出力する現状の性質を維持する（design.md決定4）。
        exported_at = datetime(2026, 8, 19, 9, 30, tzinfo=ZoneInfo("Asia/Tokyo"))
        items = [
            self._item(1, pdf_path="missing.pdf", title="消えた本", pages="1-3"),
            self._item(2, pdf_path="b.pdf", title="本B", pages=""),
        ]

        prepared = prepare_json_export(self._pack(), items, exported_at=exported_at)

        body = json.loads(prepared.content)
        self.assertEqual(len(body["items"]), 2)
        self.assertEqual(body["items"][0]["pdf_path"], "missing.pdf")
        self.assertEqual(body["items"][1]["pages"], "")

    def test_filename_sanitizes_pack_name_and_appends_exported_date(self) -> None:
        exported_at = datetime(2026, 1, 2, 3, 4, tzinfo=ZoneInfo("Asia/Tokyo"))
        prepared = prepare_json_export(self._pack(name="資料/名前:テスト"), [], exported_at=exported_at)

        self.assertEqual(prepared.filename, "資料_名前_テスト_20260102.json")

    def test_does_not_call_datetime_now(self) -> None:
        # design.md決定2: exported_atは呼び出し元が渡した値のみを使い、
        # 関数内部でdatetime.now()相当を呼ばない。
        first = prepare_json_export(self._pack(), [], exported_at=datetime(2020, 1, 1))
        second = prepare_json_export(self._pack(), [], exported_at=datetime(2020, 1, 1))
        self.assertEqual(first, second)
