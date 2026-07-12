import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from tsundokensaku.database import PackItemRecord
from tsundokensaku.export_profiles import (
    NOTEBOOKLM_ESTIMATED_CHARS_WARNING_GUIDELINE,
    NOTEBOOKLM_MAX_PAGES_PER_FILE_DEFAULT,
    NOTEBOOKLM_MAX_SOURCES_DEFAULT,
    PROFILES,
    ChatProfile,
    ExportChunk,
    ExportPlan,
    ExportProfile,
    ExportWarning,
    ItemFragment,
    NotebookLMProfile,
    RenderContext,
    StandardProfile,
    resolve_profile,
)
from tsundokensaku.export_stats import ItemStats
from tsundokensaku.token_estimate import TextStats, estimate_tokens
from tsundokensaku.zip_export import build_entry_filename, build_pack_zip_filename


def _pack_item(item_id: int, *, pdf_path: str = "a.pdf", title: str = "本", pages: str = "1-1", position: int = 0) -> PackItemRecord:
    return PackItemRecord(
        id=item_id,
        pdf_path=pdf_path,
        title=title,
        pages=pages,
        collapsed=False,
        position=position,
        added_at="2026-07-11T00:00:00.000Z",
        updated_at="2026-07-11T00:00:00.000Z",
    )


def _item_stats(
    item_id: int,
    *,
    page_count: int,
    pdf_path: str = "a.pdf",
    title: str = "本",
    position: int = 0,
    cjk_chars: int = 0,
    other_chars: int = 0,
    unindexed_pages: int = 0,
    missing_pdf: bool = False,
) -> ItemStats:
    item = _pack_item(item_id, pdf_path=pdf_path, title=title, pages=f"1-{page_count}" if page_count else "", position=position)
    return ItemStats(
        item=item,
        page_numbers=list(range(1, page_count + 1)),
        stats=TextStats(cjk_chars=cjk_chars, other_chars=other_chars),
        unindexed_pages=unindexed_pages,
        missing_pdf=missing_pdf,
    )


class _LimitedTestProfile(ExportProfile):
    """基底 plan() の分割ロジック検証専用の最小プロファイル。"""

    name = "test-limited"
    primary_format = "pdf"

    def __init__(self, limit, *, merge_allowed: bool = True, extra_warnings_result: tuple[ExportWarning, ...] = ()):
        self._limit = limit
        self._merge_allowed = merge_allowed
        self._extra_warnings_result = extra_warnings_result

    def item_weight(self, fragment: ItemFragment) -> int:
        return len(fragment.page_numbers)

    def chunk_limit(self):
        return self._limit

    def can_merge(self, current: ExportChunk, fragment: ItemFragment) -> bool:
        return self._merge_allowed

    def extra_warnings(self, plan: ExportPlan) -> tuple[ExportWarning, ...]:
        return self._extra_warnings_result

    def chunk_filename(self, chunk: ExportChunk, *, pack_name: str, format: str | None = None) -> str:
        raise NotImplementedError

    def render_chunk(self, chunk: ExportChunk, ctx) -> bytes:
        raise NotImplementedError


class ExportProfileAbstractTest(unittest.TestCase):
    def test_cannot_instantiate_export_profile_directly(self) -> None:
        with self.assertRaises(TypeError):
            ExportProfile()  # type: ignore[abstract]


class StandardProfileTest(unittest.TestCase):
    def test_split_items_is_identity_by_default(self) -> None:
        stats = _item_stats(1, page_count=3, title="本A", position=0)
        fragments = StandardProfile().split_items([stats])

        self.assertEqual(len(fragments), 1)
        fragment = fragments[0]
        self.assertIs(fragment.item_stats, stats)
        self.assertEqual(fragment.page_numbers, (1, 2, 3))
        self.assertEqual(fragment.page_spec, "1-3")
        self.assertEqual(fragment.stats, stats.stats)
        self.assertIsNone(fragment.label)
        self.assertEqual(fragment.fragment_index, 1)
        self.assertEqual(fragment.fragment_count, 1)

    def test_one_chunk_per_item(self) -> None:
        items = [
            _item_stats(1, page_count=3, title="本A", position=0),
            _item_stats(2, page_count=2, title="本B", position=1),
        ]
        plan = StandardProfile().plan(items)

        self.assertEqual(len(plan.chunks), 2)
        self.assertEqual(len(plan.chunks[0].fragments), 1)
        self.assertEqual(len(plan.chunks[1].fragments), 1)
        self.assertEqual(plan.chunks[0].items, (items[0],))
        self.assertEqual(plan.chunks[1].items, (items[1],))
        self.assertEqual(plan.warnings, ())

    def test_preserves_input_order(self) -> None:
        items = [
            _item_stats(1, page_count=1, title="C", position=0),
            _item_stats(2, page_count=1, title="A", position=1),
            _item_stats(3, page_count=1, title="B", position=2),
        ]
        plan = StandardProfile().plan(items)

        titles = [chunk.items[0].item.title for chunk in plan.chunks]
        self.assertEqual(titles, ["C", "A", "B"])

    def test_duplicate_pdf_items_become_separate_chunks(self) -> None:
        items = [
            _item_stats(1, page_count=3, pdf_path="same.pdf", title="前半", position=0),
            _item_stats(2, page_count=3, pdf_path="same.pdf", title="後半", position=1),
        ]
        plan = StandardProfile().plan(items)

        self.assertEqual(len(plan.chunks), 2)
        self.assertEqual(plan.chunks[0].items[0].item.title, "前半")
        self.assertEqual(plan.chunks[1].items[0].item.title, "後半")
        self.assertIsNot(plan.chunks[0].items[0], plan.chunks[1].items[0])

    def test_empty_list_returns_empty_plan(self) -> None:
        plan = StandardProfile().plan([])
        self.assertEqual(plan.chunks, ())
        self.assertEqual(plan.warnings, ())

    def test_chunk_index_starts_at_one(self) -> None:
        items = [_item_stats(i, page_count=1, title=f"本{i}", position=i - 1) for i in range(1, 4)]
        plan = StandardProfile().plan(items)
        self.assertEqual([chunk.index for chunk in plan.chunks], [1, 2, 3])

    def test_total_pages_matches_page_numbers_length(self) -> None:
        items = [_item_stats(1, page_count=7, title="本A")]
        plan = StandardProfile().plan(items)
        self.assertEqual(plan.chunks[0].total_pages, 7)

    def test_estimated_tokens_uses_profile_estimator_on_chunk_stats(self) -> None:
        items = [_item_stats(1, page_count=2, title="本A", cjk_chars=10, other_chars=20)]
        plan = StandardProfile().plan(items)
        expected = estimate_tokens(TextStats(cjk_chars=10, other_chars=20))
        self.assertEqual(plan.chunks[0].estimated_tokens, expected)

    def test_chunk_filename_reuses_build_entry_filename(self) -> None:
        items = [_item_stats(1, page_count=3, title="伽藍とバザール")]
        # pages spec は _item_stats のヘルパーが "1-3" を生成する
        plan = StandardProfile().plan(items)
        chunk = plan.chunks[0]

        actual = StandardProfile().chunk_filename(chunk, pack_name="資料", format="pdf")
        expected = build_entry_filename(1, "伽藍とバザール", "1-3", "pdf")
        self.assertEqual(actual, expected)

    def test_chunk_filename_requires_format_when_primary_format_is_none(self) -> None:
        # standard は primary_format=None のため、format 未指定は呼び出し側の
        # 誤りとして明示的に失敗させる（設計上「不整合」の是正点）
        items = [_item_stats(1, page_count=1, title="本A")]
        plan = StandardProfile().plan(items)
        with self.assertRaises(ValueError):
            StandardProfile().chunk_filename(plan.chunks[0], pack_name="資料")

    def test_archive_filename_reuses_build_pack_zip_filename(self) -> None:
        exported_at = datetime(2026, 7, 11, 9, 0)
        actual = StandardProfile().archive_filename(pack_name="コードとログ", exported_at=exported_at)
        expected = build_pack_zip_filename("コードとログ", exported_at)
        self.assertEqual(actual, expected)

    def test_render_chunk_calls_render_pdf_when_format_is_pdf(self) -> None:
        items = [_item_stats(1, page_count=1, title="本A", pdf_path="a.pdf")]
        plan = StandardProfile().plan(items)
        chunk = plan.chunks[0]
        calls: dict[str, object] = {}

        def fake_resolve_pdf(pdf_path):
            calls["resolved_path"] = pdf_path
            return Path("/resolved/a.pdf")

        def fake_render_pdf(candidate, pages):
            calls["render_pdf_args"] = (candidate, pages)
            return b"PDF-BYTES", "ignored.pdf"

        def fake_render_markdown(candidate, pages):
            raise AssertionError("render_markdown は呼ばれないはず")

        ctx = RenderContext(
            pack_name="資料",
            exported_at=datetime(2026, 7, 11, 9, 0),
            format="pdf",
            resolve_pdf=fake_resolve_pdf,
            render_pdf=fake_render_pdf,
            render_markdown=fake_render_markdown,
        )

        content = StandardProfile().render_chunk(chunk, ctx)

        self.assertEqual(content, b"PDF-BYTES")
        self.assertEqual(calls["resolved_path"], "a.pdf")
        self.assertEqual(calls["render_pdf_args"], (Path("/resolved/a.pdf"), "1-1"))

    def test_render_chunk_calls_render_markdown_when_format_is_md(self) -> None:
        items = [_item_stats(1, page_count=1, title="本A", pdf_path="a.pdf")]
        plan = StandardProfile().plan(items)
        chunk = plan.chunks[0]
        calls: dict[str, object] = {}

        def fake_render_pdf(candidate, pages):
            raise AssertionError("render_pdf は呼ばれないはず")

        def fake_render_markdown(candidate, pages):
            calls["render_markdown_args"] = (candidate, pages)
            return "# md content", "ignored.md"

        ctx = RenderContext(
            pack_name="資料",
            exported_at=datetime(2026, 7, 11, 9, 0),
            format="md",
            resolve_pdf=lambda pdf_path: Path("/resolved/a.pdf"),
            render_pdf=fake_render_pdf,
            render_markdown=fake_render_markdown,
        )

        content = StandardProfile().render_chunk(chunk, ctx)

        self.assertEqual(content, "# md content")
        self.assertEqual(calls["render_markdown_args"], (Path("/resolved/a.pdf"), "1-1"))

    def test_chunk_items_property_maps_back_to_item_stats(self) -> None:
        items = [_item_stats(1, page_count=2, title="本A")]
        chunk = StandardProfile().plan(items).chunks[0]
        self.assertEqual(chunk.items, tuple(fragment.item_stats for fragment in chunk.fragments))

    def test_manifest_header_lines_defaults_to_empty(self) -> None:
        plan = StandardProfile().plan([_item_stats(1, page_count=1)])
        self.assertEqual(StandardProfile().manifest_header_lines(plan), [])


class BasePlanTest(unittest.TestCase):
    def test_plan_builds_fragment_based_chunks(self) -> None:
        items = [
            _item_stats(1, page_count=2, title="A"),
            _item_stats(2, page_count=2, title="B"),
        ]
        plan = _LimitedTestProfile(limit=10).plan(items)

        self.assertEqual(len(plan.chunks), 1)
        self.assertEqual([fragment.item.title for fragment in plan.chunks[0].fragments], ["A", "B"])

    def test_merges_items_within_limit(self) -> None:
        items = [
            _item_stats(1, page_count=3, title="A"),
            _item_stats(2, page_count=3, title="B"),
        ]
        plan = _LimitedTestProfile(limit=10).plan(items)

        self.assertEqual(len(plan.chunks), 1)
        self.assertEqual(plan.chunks[0].items, (items[0], items[1]))
        self.assertEqual(plan.chunks[0].total_pages, 6)

    def test_splits_when_limit_exceeded(self) -> None:
        items = [
            _item_stats(1, page_count=6, title="A"),
            _item_stats(2, page_count=6, title="B"),
        ]
        plan = _LimitedTestProfile(limit=10).plan(items)

        self.assertEqual(len(plan.chunks), 2)
        self.assertEqual(plan.chunks[0].items, (items[0],))
        self.assertEqual(plan.chunks[1].items, (items[1],))
        self.assertEqual(plan.warnings, ())

    def test_single_item_exceeding_limit_warns_and_is_isolated(self) -> None:
        items = [
            _item_stats(1, page_count=3, title="前"),
            _item_stats(2, page_count=15, title="超過項目"),
            _item_stats(3, page_count=3, title="後"),
        ]
        plan = _LimitedTestProfile(limit=10).plan(items)

        self.assertEqual(len(plan.chunks), 3)
        self.assertEqual(plan.chunks[0].items, (items[0],))
        self.assertEqual(plan.chunks[1].items, (items[1],))
        self.assertEqual(plan.chunks[2].items, (items[2],))
        self.assertEqual(len(plan.warnings), 1)
        self.assertEqual(plan.warnings[0].code, "item_exceeds_limit")
        self.assertEqual(plan.warnings[0].item_id, 2)
        self.assertIn("超過項目", plan.warnings[0].message)

    def test_can_merge_false_forces_split_even_within_limit(self) -> None:
        items = [
            _item_stats(1, page_count=3, title="A"),
            _item_stats(2, page_count=3, title="B"),
        ]
        plan = _LimitedTestProfile(limit=10, merge_allowed=False).plan(items)

        self.assertEqual(len(plan.chunks), 2)
        self.assertEqual(plan.chunks[0].items, (items[0],))
        self.assertEqual(plan.chunks[1].items, (items[1],))

    def test_extra_warnings_are_appended_to_plan(self) -> None:
        extra = (ExportWarning(code="too_many_sources", item_id=None, message="ソース数が多すぎます"),)
        items = [_item_stats(1, page_count=1, title="A")]
        plan = _LimitedTestProfile(limit=10, extra_warnings_result=extra).plan(items)

        self.assertEqual(plan.warnings, extra)

    def test_merged_chunk_keeps_items_separate(self) -> None:
        items = [
            _item_stats(1, page_count=3, pdf_path="same.pdf", title="前半"),
            _item_stats(2, page_count=3, pdf_path="same.pdf", title="後半"),
        ]
        plan = _LimitedTestProfile(limit=10).plan(items)

        self.assertEqual(len(plan.chunks), 1)
        self.assertEqual(len(plan.chunks[0].items), 2)
        self.assertEqual([entry.item.title for entry in plan.chunks[0].items], ["前半", "後半"])
        self.assertEqual([entry.page_numbers for entry in plan.chunks[0].items], [[1, 2, 3], [1, 2, 3]])


class ProfilesRegistryTest(unittest.TestCase):
    def test_standard_is_registered(self) -> None:
        profile = PROFILES["standard"]
        self.assertIsInstance(profile, StandardProfile)
        self.assertEqual(profile.name, "standard")

    def test_notebooklm_is_registered(self) -> None:
        profile = PROFILES["notebooklm"]
        self.assertIsInstance(profile, NotebookLMProfile)
        self.assertEqual(profile.name, "notebooklm")
        self.assertEqual(profile.primary_format, "pdf")

    def test_chat_is_registered(self) -> None:
        profile = PROFILES["chat"]
        self.assertIsInstance(profile, ChatProfile)
        self.assertEqual(profile.name, "chat")
        self.assertEqual(profile.primary_format, "md")


class NotebookLMProfileTest(unittest.TestCase):
    def test_basic_attributes(self) -> None:
        profile = NotebookLMProfile()
        self.assertEqual(profile.name, "notebooklm")
        self.assertEqual(profile.primary_format, "pdf")
        self.assertIsInstance(PROFILES["notebooklm"], NotebookLMProfile)

    def test_item_weight_uses_page_count_not_tokens(self) -> None:
        stats = _item_stats(1, page_count=5, cjk_chars=999_999, title="本A")
        fragment = NotebookLMProfile().split_items([stats])[0]
        self.assertEqual(NotebookLMProfile().item_weight(fragment), 5)

    def test_adjacent_same_pdf_items_merge_within_limit(self) -> None:
        items = [
            _item_stats(1, page_count=3, pdf_path="same.pdf", title="前半", position=0),
            _item_stats(2, page_count=4, pdf_path="same.pdf", title="後半", position=1),
        ]
        with patch.dict("os.environ", {"TSUNDOKENSAKU_NOTEBOOKLM_MAX_PAGES_PER_FILE": "10"}):
            plan = NotebookLMProfile().plan(items)

        self.assertEqual(len(plan.chunks), 1)
        self.assertEqual([fragment.item.title for fragment in plan.chunks[0].fragments], ["前半", "後半"])
        self.assertEqual(plan.chunks[0].total_pages, 7)

    def test_different_pdf_items_do_not_merge_even_within_limit(self) -> None:
        items = [
            _item_stats(1, page_count=3, pdf_path="a.pdf", title="A", position=0),
            _item_stats(2, page_count=3, pdf_path="b.pdf", title="B", position=1),
        ]
        with patch.dict("os.environ", {"TSUNDOKENSAKU_NOTEBOOKLM_MAX_PAGES_PER_FILE": "10"}):
            plan = NotebookLMProfile().plan(items)

        self.assertEqual(len(plan.chunks), 2)
        self.assertEqual([chunk.fragments[0].item.pdf_path for chunk in plan.chunks], ["a.pdf", "b.pdf"])

    def test_non_adjacent_same_pdf_items_do_not_merge_across_other_pdf(self) -> None:
        items = [
            _item_stats(1, page_count=2, pdf_path="a.pdf", title="A1", position=0),
            _item_stats(2, page_count=2, pdf_path="b.pdf", title="B", position=1),
            _item_stats(3, page_count=2, pdf_path="a.pdf", title="A2", position=2),
        ]
        with patch.dict("os.environ", {"TSUNDOKENSAKU_NOTEBOOKLM_MAX_PAGES_PER_FILE": "10"}):
            plan = NotebookLMProfile().plan(items)

        self.assertEqual(len(plan.chunks), 3)
        self.assertEqual([chunk.fragments[0].item.title for chunk in plan.chunks], ["A1", "B", "A2"])

    def test_page_limit_exactly_fits_but_one_page_over_splits(self) -> None:
        exact_items = [
            _item_stats(1, page_count=3, pdf_path="same.pdf", position=0),
            _item_stats(2, page_count=2, pdf_path="same.pdf", position=1),
        ]
        over_items = [
            _item_stats(1, page_count=3, pdf_path="same.pdf", position=0),
            _item_stats(2, page_count=3, pdf_path="same.pdf", position=1),
        ]
        with patch.dict("os.environ", {"TSUNDOKENSAKU_NOTEBOOKLM_MAX_PAGES_PER_FILE": "5"}):
            exact_plan = NotebookLMProfile().plan(exact_items)
            over_plan = NotebookLMProfile().plan(over_items)

        self.assertEqual(len(exact_plan.chunks), 1)
        self.assertEqual(exact_plan.chunks[0].total_pages, 5)
        self.assertEqual(len(over_plan.chunks), 2)

    def test_duplicate_pages_are_counted_without_deduplication(self) -> None:
        first = _item_stats(1, page_count=10, pdf_path="same.pdf", title="前", position=0)
        second = _item_stats(2, page_count=4, pdf_path="same.pdf", title="後", position=1)
        second = ItemStats(
            item=_pack_item(2, pdf_path="same.pdf", title="後", pages="5-8", position=1),
            page_numbers=[5, 6, 7, 8],
            stats=second.stats,
            unindexed_pages=0,
            missing_pdf=False,
        )
        with patch.dict("os.environ", {"TSUNDOKENSAKU_NOTEBOOKLM_MAX_PAGES_PER_FILE": "20"}):
            plan = NotebookLMProfile().plan([first, second])

        self.assertEqual(len(plan.chunks), 1)
        self.assertEqual(plan.chunks[0].total_pages, 14)
        self.assertEqual(plan.chunks[0].fragments[0].page_numbers, tuple(range(1, 11)))
        self.assertEqual(plan.chunks[0].fragments[1].page_numbers, (5, 6, 7, 8))
        self.assertEqual([fragment.page_spec for fragment in plan.chunks[0].fragments], ["1-10", "5-8"])

    def test_single_fragment_exceeding_limit_warns_but_plan_is_generated(self) -> None:
        items = [_item_stats(1, page_count=6, pdf_path="same.pdf", title="巨大本", position=0)]
        with patch.dict("os.environ", {"TSUNDOKENSAKU_NOTEBOOKLM_MAX_PAGES_PER_FILE": "5"}):
            plan = NotebookLMProfile().plan(items)

        self.assertEqual(len(plan.chunks), 1)
        self.assertEqual(plan.warnings[0].code, "item_exceeds_limit")
        self.assertEqual(plan.warnings[0].item_id, 1)

    def test_source_count_warning_only_when_threshold_is_exceeded(self) -> None:
        items = [
            _item_stats(1, page_count=1, pdf_path="a.pdf", position=0),
            _item_stats(2, page_count=1, pdf_path="b.pdf", position=1),
            _item_stats(3, page_count=1, pdf_path="c.pdf", position=2),
        ]
        with patch.dict("os.environ", {
            "TSUNDOKENSAKU_NOTEBOOKLM_MAX_PAGES_PER_FILE": "10",
            "TSUNDOKENSAKU_NOTEBOOKLM_MAX_SOURCES": "3",
        }):
            no_warning = NotebookLMProfile().plan(items)
        with patch.dict("os.environ", {
            "TSUNDOKENSAKU_NOTEBOOKLM_MAX_PAGES_PER_FILE": "10",
            "TSUNDOKENSAKU_NOTEBOOKLM_MAX_SOURCES": "2",
        }):
            warning = NotebookLMProfile().plan(items)

        self.assertFalse(any(entry.code == "too_many_sources" for entry in no_warning.warnings))
        source_warnings = [entry for entry in warning.warnings if entry.code == "too_many_sources"]
        self.assertEqual(len(source_warnings), 1)
        self.assertIsNone(source_warnings[0].item_id)
        self.assertEqual(len(warning.chunks), 3)

    def test_environment_values_are_read_at_call_time(self) -> None:
        profile = PROFILES["notebooklm"]
        items = [
            _item_stats(1, page_count=2, pdf_path="same.pdf", position=0),
            _item_stats(2, page_count=2, pdf_path="same.pdf", position=1),
        ]
        with patch.dict("os.environ", {
            "TSUNDOKENSAKU_NOTEBOOKLM_MAX_PAGES_PER_FILE": "10",
            "TSUNDOKENSAKU_NOTEBOOKLM_MAX_SOURCES": "10",
        }):
            merged = profile.plan(items)
        with patch.dict("os.environ", {
            "TSUNDOKENSAKU_NOTEBOOKLM_MAX_PAGES_PER_FILE": "3",
            "TSUNDOKENSAKU_NOTEBOOKLM_MAX_SOURCES": "1",
        }):
            split = profile.plan(items)

        self.assertEqual(len(merged.chunks), 1)
        self.assertEqual(len(split.chunks), 2)
        self.assertTrue(any(entry.code == "too_many_sources" for entry in split.warnings))

    def test_invalid_environment_values_fall_back_to_defaults(self) -> None:
        profile = NotebookLMProfile()

        with patch.dict("os.environ", {
            "TSUNDOKENSAKU_NOTEBOOKLM_MAX_PAGES_PER_FILE": "",
            "TSUNDOKENSAKU_NOTEBOOKLM_MAX_SOURCES": "abc",
        }):
            self.assertEqual(profile.chunk_limit(), NOTEBOOKLM_MAX_PAGES_PER_FILE_DEFAULT)
            self.assertEqual(profile.extra_warnings(ExportPlan(profile_name="notebooklm", chunks=(), warnings=())), ())

        with patch.dict("os.environ", {
            "TSUNDOKENSAKU_NOTEBOOKLM_MAX_PAGES_PER_FILE": "0",
            "TSUNDOKENSAKU_NOTEBOOKLM_MAX_SOURCES": "-1",
        }):
            self.assertEqual(profile.chunk_limit(), NOTEBOOKLM_MAX_PAGES_PER_FILE_DEFAULT)
            warning = profile.extra_warnings(
                ExportPlan(
                    profile_name="notebooklm",
                    chunks=tuple(ExportChunk(index=i, fragments=(), total_pages=0, estimated_tokens=0) for i in range(1, NOTEBOOKLM_MAX_SOURCES_DEFAULT + 2)),
                    warnings=(),
                )
            )
            self.assertEqual(len([entry for entry in warning if entry.code == "too_many_sources"]), 1)

    def test_estimated_chars_warning_is_not_emitted_below_guideline(self) -> None:
        items = [
            _item_stats(
                1,
                page_count=10,
                pdf_path="same.pdf",
                position=0,
                cjk_chars=NOTEBOOKLM_ESTIMATED_CHARS_WARNING_GUIDELINE - 1,
            ),
        ]
        with patch.dict("os.environ", {
            "TSUNDOKENSAKU_NOTEBOOKLM_MAX_PAGES_PER_FILE": "100",
            "TSUNDOKENSAKU_NOTEBOOKLM_MAX_SOURCES": "10",
        }):
            plan = NotebookLMProfile().plan(items)

        self.assertFalse(any(entry.code == "estimated_chars_exceed_guideline" for entry in plan.warnings))

    def test_estimated_chars_warning_is_not_emitted_at_guideline(self) -> None:
        items = [
            _item_stats(
                1,
                page_count=10,
                pdf_path="same.pdf",
                position=0,
                cjk_chars=NOTEBOOKLM_ESTIMATED_CHARS_WARNING_GUIDELINE,
            ),
        ]
        with patch.dict("os.environ", {
            "TSUNDOKENSAKU_NOTEBOOKLM_MAX_PAGES_PER_FILE": "100",
            "TSUNDOKENSAKU_NOTEBOOKLM_MAX_SOURCES": "10",
        }):
            plan = NotebookLMProfile().plan(items)

        self.assertFalse(any(entry.code == "estimated_chars_exceed_guideline" for entry in plan.warnings))

    def test_estimated_chars_warning_is_emitted_one_char_over_guideline(self) -> None:
        items = [
            _item_stats(
                1,
                page_count=10,
                pdf_path="same.pdf",
                position=0,
                cjk_chars=NOTEBOOKLM_ESTIMATED_CHARS_WARNING_GUIDELINE + 1,
            ),
        ]
        with patch.dict("os.environ", {
            "TSUNDOKENSAKU_NOTEBOOKLM_MAX_PAGES_PER_FILE": "100",
            "TSUNDOKENSAKU_NOTEBOOKLM_MAX_SOURCES": "10",
        }):
            plan = NotebookLMProfile().plan(items)

        self.assertEqual(len(plan.chunks), 1)
        char_warnings = [entry for entry in plan.warnings if entry.code == "estimated_chars_exceed_guideline"]
        self.assertEqual(len(char_warnings), 1)
        self.assertIsNone(char_warnings[0].item_id)

    def test_estimated_chars_warning_uses_sum_of_cjk_and_other_chars_across_fragments(self) -> None:
        items = [
            _item_stats(
                1,
                page_count=10,
                pdf_path="same.pdf",
                position=0,
                cjk_chars=NOTEBOOKLM_ESTIMATED_CHARS_WARNING_GUIDELINE - 2,
            ),
            _item_stats(
                2,
                page_count=10,
                pdf_path="same.pdf",
                position=1,
                other_chars=2,
            ),
        ]
        with patch.dict("os.environ", {
            "TSUNDOKENSAKU_NOTEBOOKLM_MAX_PAGES_PER_FILE": "100",
            "TSUNDOKENSAKU_NOTEBOOKLM_MAX_SOURCES": "10",
        }):
            at_guideline = NotebookLMProfile().plan(items)
        with patch.dict("os.environ", {
            "TSUNDOKENSAKU_NOTEBOOKLM_MAX_PAGES_PER_FILE": "100",
            "TSUNDOKENSAKU_NOTEBOOKLM_MAX_SOURCES": "10",
        }):
            over_guideline = NotebookLMProfile().plan([
                items[0],
                _item_stats(
                    2,
                    page_count=10,
                    pdf_path="same.pdf",
                    position=1,
                    other_chars=3,
                ),
            ])

        self.assertEqual(len(at_guideline.chunks), 1)
        self.assertFalse(any(entry.code == "estimated_chars_exceed_guideline" for entry in at_guideline.warnings))
        over_warnings = [entry for entry in over_guideline.warnings if entry.code == "estimated_chars_exceed_guideline"]
        self.assertEqual(len(over_warnings), 1)

    def test_estimated_chars_warning_does_not_change_chunking(self) -> None:
        items = [
            _item_stats(
                1,
                page_count=10,
                pdf_path="same.pdf",
                position=0,
                cjk_chars=NOTEBOOKLM_ESTIMATED_CHARS_WARNING_GUIDELINE - 1,
            ),
            _item_stats(
                2,
                page_count=10,
                pdf_path="same.pdf",
                position=1,
                other_chars=2,
            ),
        ]
        with patch.dict("os.environ", {
            "TSUNDOKENSAKU_NOTEBOOKLM_MAX_PAGES_PER_FILE": "100",
            "TSUNDOKENSAKU_NOTEBOOKLM_MAX_SOURCES": "10",
        }):
            plan = NotebookLMProfile().plan(items)

        self.assertEqual(len(plan.chunks), 1)
        self.assertEqual(plan.chunks[0].total_pages, 20)
        char_warnings = [entry for entry in plan.warnings if entry.code == "estimated_chars_exceed_guideline"]
        self.assertEqual(len(char_warnings), 1)
        self.assertIsNone(char_warnings[0].item_id)


class ChatProfileTest(unittest.TestCase):
    def test_split_items_is_identity_by_default(self) -> None:
        stats = _item_stats(1, page_count=2, title="本A", position=0)
        fragments = ChatProfile().split_items([stats])

        self.assertEqual(len(fragments), 1)
        self.assertIs(fragments[0].item_stats, stats)
        self.assertEqual(fragments[0].page_spec, "1-2")
        self.assertIsNone(fragments[0].label)

    def test_item_weight_is_token_count(self) -> None:
        # CJK: 10文字 (10.0), Other: 8文字 (2.0) -> ceil(12.0) = 12
        stats = _item_stats(1, page_count=1, cjk_chars=10, other_chars=8)
        fragment = ChatProfile().split_items([stats])[0]
        self.assertEqual(ChatProfile().item_weight(fragment), 12)

    def test_chunk_limit_is_80000(self) -> None:
        self.assertEqual(ChatProfile().chunk_limit(), 80000)

    def test_chunk_filename(self) -> None:
        items = [_item_stats(1, page_count=1)]
        plan = ChatProfile().plan(items)
        filename = ChatProfile().chunk_filename(plan.chunks[0], pack_name="MyPack")
        self.assertEqual(filename, "MyPack_chat_01.md")

    def test_greedy_split_by_tokens(self) -> None:
        # limit を 100 としたテスト用 ChatProfile を使うこともできるが、
        # ChatProfile の chunk_limit() は 80000 固定なので、
        # 80000 を超えるかどうかの構成でテストする。
        # 1項目目: 50,000トークン (cjk=50000)
        # 2項目目: 40,000トークン (cjk=40000)
        # 合計 90,000トークン > 80,000 なので分割されるはず
        items = [
            _item_stats(1, page_count=1, cjk_chars=50000, title="本A", position=0),
            _item_stats(2, page_count=1, cjk_chars=40000, title="本B", position=1),
        ]
        plan = ChatProfile().plan(items)
        self.assertEqual(len(plan.chunks), 2)
        self.assertEqual(len(plan.chunks[0].items), 1)
        self.assertEqual(plan.chunks[0].items[0].item.title, "本A")
        self.assertEqual(len(plan.chunks[1].items), 1)
        self.assertEqual(plan.chunks[1].items[0].item.title, "本B")

    def test_single_item_exceeds_limit(self) -> None:
        # 1項目だけで上限 (80000) を超える場合
        items = [
            _item_stats(1, page_count=1, cjk_chars=90000, title="巨大本", position=0),
        ]
        plan = ChatProfile().plan(items)
        self.assertEqual(len(plan.chunks), 1)
        self.assertEqual(len(plan.warnings), 1)
        self.assertEqual(plan.warnings[0].code, "item_exceeds_limit")
        self.assertEqual(plan.warnings[0].item_id, 1)
        self.assertIn("巨大本", plan.warnings[0].message)

    def test_render_chunk_combines_markdown_with_header(self) -> None:
        items = [
            _item_stats(1, page_count=1, pdf_path="book-a.pdf", title="本A", position=0),
            _item_stats(2, page_count=1, pdf_path="book-b.pdf", title="本B", position=1),
        ]
        plan = ChatProfile().plan(items)
        chunk = plan.chunks[0]

        ctx = RenderContext(
            pack_name="テスト資料",
            exported_at=datetime(2026, 7, 12, 1, 0),
            format="md",
            resolve_pdf=lambda path: Path(path),
            render_pdf=lambda path, pages: (b"pdf", "file.pdf"),
            render_markdown=lambda path, pages: (f"# {path.name} pages={pages}", "file.md"),
            total_chunks=1,
        )

        content = ChatProfile().render_chunk(chunk, ctx).decode("utf-8")
        self.assertIn("# テスト資料（分冊 1/1）", content)
        self.assertIn("## 収録項目", content)
        self.assertIn("- 本A (1-1)", content)
        self.assertIn("- 本B (1-1)", content)
        self.assertIn("# book-a.pdf pages=1-1", content)
        self.assertIn("# book-b.pdf pages=1-1", content)
        # 項目間が --- で区切られていること
        self.assertIn("\n\n---\n\n", content)


class ResolveProfileTest(unittest.TestCase):
    def test_none_resolves_to_standard(self) -> None:
        self.assertIsInstance(resolve_profile(None), StandardProfile)

    def test_standard_name_resolves_to_standard(self) -> None:
        self.assertIsInstance(resolve_profile("standard"), StandardProfile)

    def test_chat_name_resolves_to_chat(self) -> None:
        self.assertIsInstance(resolve_profile("chat"), ChatProfile)

    def test_unknown_name_raises_value_error_with_name_as_message(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            resolve_profile("unknown")
        self.assertEqual(str(ctx.exception), "unknown")


if __name__ == "__main__":
    unittest.main()
