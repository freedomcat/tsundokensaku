"""search_view.py の境界・ドリフト防止を確認する契約テスト。

R4分離（docs/central-file-refactoring-inventory.md 段階3）の第2コミットで追加。
検索結果整形の意味的挙動そのものは tests/test_web.py の characterization test
（第1コミットで追加）が固定しているため、ここでは新モジュールの境界契約のみ扱う。
"""

import inspect
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from tsundokensaku import search_view
from tsundokensaku import web


class NowJstContractTest(unittest.TestCase):
    """web._now_jst と search_view._now_jst の複製によるドリフトを検知する。"""

    def test_search_view_now_jst_is_timezone_aware(self) -> None:
        self.assertIsNotNone(search_view._now_jst().tzinfo)

    def test_search_view_now_jst_uses_asia_tokyo_offset(self) -> None:
        expected_offset = datetime.now(ZoneInfo("Asia/Tokyo")).utcoffset()
        self.assertEqual(search_view._now_jst().utcoffset(), expected_offset)

    def test_web_now_jst_is_timezone_aware(self) -> None:
        self.assertIsNotNone(web._now_jst().tzinfo)

    def test_web_now_jst_uses_asia_tokyo_offset(self) -> None:
        expected_offset = datetime.now(ZoneInfo("Asia/Tokyo")).utcoffset()
        self.assertEqual(web._now_jst().utcoffset(), expected_offset)

    def test_web_and_search_view_now_jst_share_same_contract(self) -> None:
        web_now = web._now_jst()
        search_view_now = search_view._now_jst()
        self.assertIsNotNone(web_now.tzinfo)
        self.assertIsNotNone(search_view_now.tzinfo)
        self.assertEqual(web_now.utcoffset(), search_view_now.utcoffset())


class SearchViewImportTest(unittest.TestCase):
    """新モジュールから12関数を直接importできることを確認する。"""

    def test_twelve_functions_importable_from_search_view(self) -> None:
        from tsundokensaku.search_view import (  # noqa: F401
            highlight_query,
            format_indexed_at,
            _sanitize_scrapbox_title,
            build_scrapbox_page_url,
            _scrapbox_page_label,
            build_search_result_rows,
            finalize_search_result_rows,
            normalize_search_group,
            normalize_search_match,
            build_search_scrapbox_body,
            sort_results,
            group_pdf_results,
        )


class WebModuleCompatibilityTest(unittest.TestCase):
    """既存の `from tsundokensaku.web import ...` が引き続き成功することを確認する。"""

    def test_twelve_functions_still_importable_from_web(self) -> None:
        from tsundokensaku.web import (  # noqa: F401
            highlight_query,
            format_indexed_at,
            _sanitize_scrapbox_title,
            build_scrapbox_page_url,
            _scrapbox_page_label,
            build_search_result_rows,
            finalize_search_result_rows,
            normalize_search_group,
            normalize_search_match,
            build_search_scrapbox_body,
            sort_results,
            group_pdf_results,
        )


class SearchViewDependencyDirectionTest(unittest.TestCase):
    """search_view.py が web.py に依存しないことをソース上で確認する。"""

    def test_search_view_source_does_not_reference_web_module(self) -> None:
        source = inspect.getsource(search_view)
        self.assertNotIn("tsundokensaku.web", source)
        self.assertNotIn("from tsundokensaku import web", source)
