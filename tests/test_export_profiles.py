import unittest
from datetime import datetime
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter

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


def _fragment(
    item_id: int,
    *,
    title: str = "本",
    pdf_path: str = "a.pdf",
    pages: str = "1-1",
    label: str | None = None,
    fragment_index: int = 1,
    fragment_count: int = 1,
    cjk_chars: int = 0,
    other_chars: int = 0,
) -> ItemFragment:
    numbers: list[int] = []
    for part in pages.split(","):
        chunk = part.strip()
        if not chunk:
            continue
        if "-" in chunk:
            start_text, end_text = chunk.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            numbers.extend(range(start, end + 1))
        else:
            numbers.append(int(chunk))

    stats = ItemStats(
        item=_pack_item(item_id, pdf_path=pdf_path, title=title, pages=pages, position=item_id - 1),
        page_numbers=numbers,
        stats=TextStats(cjk_chars=cjk_chars, other_chars=other_chars),
        unindexed_pages=0,
        missing_pdf=False,
    )
    return ItemFragment(
        item_stats=stats,
        page_numbers=tuple(numbers),
        page_spec=pages,
        stats=stats.stats,
        label=label,
        fragment_index=fragment_index,
        fragment_count=fragment_count,
    )


def _pdf_bytes(page_count: int, *, metadata: dict[str, str] | None = None) -> bytes:
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=72, height=72)
    if metadata:
        writer.add_metadata(metadata)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


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

        self.assertEqual(len(plan.chunks), 2)
        self.assertEqual(plan.chunks[0].total_pages, 5)
        self.assertEqual(plan.chunks[1].total_pages, 1)
        self.assertEqual(plan.warnings[0].code, "no_outline_fallback")
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

    def test_chunk_filename_uses_label_when_single_fragment_has_label(self) -> None:
        chunk = ExportChunk(
            index=1,
            fragments=(_fragment(1, title="本A", pages="1-20", label="第1章"),),
            total_pages=20,
            estimated_tokens=0,
        )
        filename = NotebookLMProfile().chunk_filename(chunk, pack_name="資料")
        self.assertEqual(filename, "01_本A_第1章_p1-20.pdf")

    def test_chunk_filename_uses_pages_when_single_fragment_has_no_label(self) -> None:
        chunk = ExportChunk(
            index=2,
            fragments=(_fragment(1, title="本A", pages="21-30"),),
            total_pages=10,
            estimated_tokens=0,
        )
        filename = NotebookLMProfile().chunk_filename(chunk, pack_name="資料")
        self.assertEqual(filename, "02_本A_p21-30.pdf")

    def test_chunk_filename_uses_joined_page_ranges_for_merged_fragments(self) -> None:
        chunk = ExportChunk(
            index=3,
            fragments=(
                _fragment(1, title="本A", pages="1-10", label="第1章"),
                _fragment(1, title="本A", pages="5-8", label="第2章", fragment_index=2, fragment_count=2),
            ),
            total_pages=14,
            estimated_tokens=0,
        )
        filename = NotebookLMProfile().chunk_filename(chunk, pack_name="資料")
        self.assertEqual(filename, "03_本A_p1-10_5-8.pdf")

    def test_chunk_filename_stays_within_255_bytes_for_long_japanese_title_and_label(self) -> None:
        chunk = ExportChunk(
            index=1,
            fragments=(
                _fragment(1, title="非常に長い書名" * 30, pages="1-300", label="非常に長い章名" * 30),
            ),
            total_pages=300,
            estimated_tokens=0,
        )
        filename = NotebookLMProfile().chunk_filename(chunk, pack_name="資料")
        self.assertLessEqual(len(filename.encode("utf-8")), 255)
        self.assertTrue(filename.startswith("01_"))
        self.assertTrue(filename.endswith(".pdf"))

    def test_render_chunk_returns_single_fragment_pdf(self) -> None:
        chunk = ExportChunk(
            index=1,
            fragments=(_fragment(1, title="本A", pages="1-2"),),
            total_pages=2,
            estimated_tokens=0,
        )
        calls: list[tuple[str, str]] = []
        expected_pdf = _pdf_bytes(2, metadata={"/Title": "first"})
        ctx = RenderContext(
            pack_name="資料",
            exported_at=datetime(2026, 7, 12, 1, 0),
            format="pdf",
            resolve_pdf=lambda path: Path(path),
            render_pdf=lambda path, pages: (calls.append((path.name, pages)) or expected_pdf, "x.pdf"),
            render_markdown=lambda path, pages: ("", "x.md"),
        )

        rendered = NotebookLMProfile().render_chunk(chunk, ctx)
        self.assertEqual(rendered, expected_pdf)
        self.assertEqual(calls, [("a.pdf", "1-2")])
        self.assertEqual(len(PdfReader(BytesIO(rendered)).pages), 2)

    def test_render_chunk_merges_fragments_in_order_without_deduplicating_pages(self) -> None:
        chunk = ExportChunk(
            index=1,
            fragments=(
                _fragment(1, title="本A", pages="1-10"),
                _fragment(1, title="本A", pages="5-8", fragment_index=2, fragment_count=2),
            ),
            total_pages=14,
            estimated_tokens=0,
        )
        rendered_by_pages = {
            "1-10": _pdf_bytes(10, metadata={"/Title": "first", "/Author": "alice"}),
            "5-8": _pdf_bytes(4, metadata={"/Title": "second"}),
        }
        calls: list[str] = []
        ctx = RenderContext(
            pack_name="資料",
            exported_at=datetime(2026, 7, 12, 1, 0),
            format="pdf",
            resolve_pdf=lambda path: Path(path),
            render_pdf=lambda path, pages: (calls.append(pages) or rendered_by_pages[pages], "x.pdf"),
            render_markdown=lambda path, pages: ("", "x.md"),
        )

        rendered = NotebookLMProfile().render_chunk(chunk, ctx)
        reader = PdfReader(BytesIO(rendered))
        self.assertEqual(calls, ["1-10", "5-8"])
        self.assertEqual(len(reader.pages), 14)
        self.assertEqual(reader.metadata.get("/Title"), "first")
        self.assertEqual(reader.metadata.get("/Author"), "alice")

    def test_render_chunk_succeeds_when_first_pdf_has_no_metadata(self) -> None:
        chunk = ExportChunk(
            index=1,
            fragments=(
                _fragment(1, title="本A", pages="1-2"),
                _fragment(1, title="本A", pages="3-4", fragment_index=2, fragment_count=2),
            ),
            total_pages=4,
            estimated_tokens=0,
        )
        ctx = RenderContext(
            pack_name="資料",
            exported_at=datetime(2026, 7, 12, 1, 0),
            format="pdf",
            resolve_pdf=lambda path: Path(path),
            render_pdf=lambda path, pages: (_pdf_bytes(2), "x.pdf"),
            render_markdown=lambda path, pages: ("", "x.md"),
        )

        rendered = NotebookLMProfile().render_chunk(chunk, ctx)
        reader = PdfReader(BytesIO(rendered))
        self.assertEqual(len(reader.pages), 4)

    def test_manifest_header_lines_describe_notebooklm_export(self) -> None:
        plan = ExportPlan(
            profile_name="notebooklm",
            chunks=(ExportChunk(index=1, fragments=(_fragment(1),), total_pages=1, estimated_tokens=0),),
            warnings=(),
        )
        lines = NotebookLMProfile().manifest_header_lines(plan)
        self.assertIn("- NotebookLM向けのPDFです", lines)
        self.assertIn("- 出力ファイル数: 1", lines)


class NotebookLMChapterSplitTest(unittest.TestCase):
    """chapter_loaderを直接モックして章分割ロジックを検証する純粋ユニットテスト。

    fitz / PDF ファイルへの依存なしに実行できる。
    """

    def _make_chapter(self, title: str, start_page: int, end_page: int):
        from types import SimpleNamespace

        return SimpleNamespace(title=title, start_page=start_page, end_page=end_page)

    def _chapter_loader(self, chapters):
        def loader(pdf_path):
            return chapters

        return loader

    # ── 上限以内ではアウトラインを読み込まない ──────────────────────────────────

    def test_chapter_loader_not_called_when_item_is_within_limit(self) -> None:
        """上限以内の項目では chapter_loader を一切呼ばない。"""
        called = []

        def loader(pdf_path):
            called.append(pdf_path)
            return []

        items = [_item_stats(1, page_count=3, pdf_path="a.pdf", title="本", position=0)]
        with patch.dict("os.environ", {"TSUNDOKENSAKU_NOTEBOOKLM_MAX_PAGES_PER_FILE": "5"}):
            NotebookLMProfile().plan(items, chapter_loader=loader)

        self.assertEqual(called, [])

    # ── 選択ページと章範囲の交差 ─────────────────────────────────────────────────

    def test_only_selected_pages_within_chapter_range_are_included(self) -> None:
        """資料の選択ページと章ページ範囲の交差だけが出力される。"""
        chapters = [
            self._make_chapter("章A", 1, 5),
            self._make_chapter("章B", 6, 10),
        ]
        # 1-4 ページのみ選択（章Aは1-4、章Bはヒットしない）
        item = _item_stats(1, page_count=4, pdf_path="a.pdf", title="本", position=0)
        # page_numbers を 1-4 に限定
        from tsundokensaku.database import PackItemRecord
        from tsundokensaku.export_stats import ItemStats
        from tsundokensaku.token_estimate import TextStats

        item_custom = ItemStats(
            item=item.item,
            page_numbers=list(range(1, 5)),  # 1-4
            stats=TextStats(cjk_chars=0, other_chars=0),
            unindexed_pages=0,
            missing_pdf=False,
        )
        with patch.dict("os.environ", {"TSUNDOKENSAKU_NOTEBOOKLM_MAX_PAGES_PER_FILE": "3"}):
            fragments, warnings = NotebookLMProfile().split_items_with_warnings(
                [item_custom], chapter_loader=self._chapter_loader(chapters)
            )

        # 章Aとしての交差(1-3, 4)となるが上限3なので再分割される
        # 章Bは交差なし → 出力なし
        all_pages = [p for f in fragments for p in f.page_numbers]
        for p in all_pages:
            self.assertLessEqual(p, 4, "選択ページ外のページが含まれている")
        self.assertNotIn(6, all_pages)
        self.assertNotIn(7, all_pages)

    def test_pages_outside_chapter_range_not_included(self) -> None:
        """選択ページのうち、どの章にも属さないページは出力されない。"""
        chapters = [self._make_chapter("章A", 3, 8)]
        # 1-10 選択、章Aは 3-8 なので 1,2,9,10 は除外
        items = [_item_stats(1, page_count=10, pdf_path="a.pdf", title="本", position=0)]
        with patch.dict("os.environ", {"TSUNDOKENSAKU_NOTEBOOKLM_MAX_PAGES_PER_FILE": "5"}):
            fragments, warnings = NotebookLMProfile().split_items_with_warnings(
                items, chapter_loader=self._chapter_loader(chapters)
            )

        all_pages = [p for f in fragments for p in f.page_numbers]
        for page in all_pages:
            self.assertIn(page, range(3, 9), f"章範囲外のページ {page} が含まれている")
        for excluded in [1, 2, 9, 10]:
            self.assertNotIn(excluded, all_pages)

    # ── アウトラインなし時のフォールバック ────────────────────────────────────────

    def test_no_outline_fallback_when_chapter_loader_returns_empty(self) -> None:
        """chapter_loader が空リストを返すと no_outline_fallback 警告で連続ページ分割。"""
        items = [_item_stats(1, page_count=8, pdf_path="a.pdf", title="本", position=0)]
        with patch.dict("os.environ", {"TSUNDOKENSAKU_NOTEBOOKLM_MAX_PAGES_PER_FILE": "5"}):
            fragments, warnings = NotebookLMProfile().split_items_with_warnings(
                items, chapter_loader=self._chapter_loader([])
            )

        codes = [w.code for w in warnings]
        self.assertIn("no_outline_fallback", codes)
        # 連続分割: 5 + 3
        self.assertEqual(len(fragments), 2)
        self.assertEqual(len(fragments[0].page_numbers), 5)
        self.assertEqual(len(fragments[1].page_numbers), 3)

    def test_no_outline_fallback_when_chapter_loader_is_none(self) -> None:
        """chapter_loader=None のとき超過項目は no_outline_fallback になる。"""
        items = [_item_stats(1, page_count=6, pdf_path="a.pdf", title="本", position=0)]
        with patch.dict("os.environ", {"TSUNDOKENSAKU_NOTEBOOKLM_MAX_PAGES_PER_FILE": "4"}):
            fragments, warnings = NotebookLMProfile().split_items_with_warnings(items, chapter_loader=None)

        codes = [w.code for w in warnings]
        self.assertIn("no_outline_fallback", codes)
        self.assertNotIn("item_split_by_chapters", codes)
        self.assertEqual(len(fragments), 2)

    # ── 巨大章の再分割（chapter_exceeds_limit） ───────────────────────────────────

    def test_chapter_exceeds_limit_triggers_page_block_split(self) -> None:
        """章ページ数が上限を超えるとページブロック再分割と chapter_exceeds_limit 警告。"""
        chapters = [self._make_chapter("大章", 1, 12)]
        items = [_item_stats(1, page_count=12, pdf_path="a.pdf", title="本", position=0)]
        with patch.dict("os.environ", {"TSUNDOKENSAKU_NOTEBOOKLM_MAX_PAGES_PER_FILE": "5"}):
            fragments, warnings = NotebookLMProfile().split_items_with_warnings(
                items, chapter_loader=self._chapter_loader(chapters)
            )

        codes = [w.code for w in warnings]
        self.assertIn("chapter_exceeds_limit", codes)
        # 12ページ → 5+5+2 = 3フラグメント
        self.assertEqual(len(fragments), 3)
        # label は "大章 part1", "大章 part2", "大章 part3"
        self.assertTrue(all(f.label is not None and "大章" in f.label for f in fragments))

    # ── item_split_by_chapters 警告 ──────────────────────────────────────────────

    def test_item_split_by_chapters_warning_when_multiple_chapters(self) -> None:
        """複数章に分割された場合に item_split_by_chapters 警告が発生する。"""
        chapters = [
            self._make_chapter("第1章", 1, 5),
            self._make_chapter("第2章", 6, 11),
        ]
        items = [_item_stats(1, page_count=11, pdf_path="a.pdf", title="本", position=0)]
        with patch.dict("os.environ", {"TSUNDOKENSAKU_NOTEBOOKLM_MAX_PAGES_PER_FILE": "6"}):
            fragments, warnings = NotebookLMProfile().split_items_with_warnings(
                items, chapter_loader=self._chapter_loader(chapters)
            )

        codes = [w.code for w in warnings]
        self.assertIn("item_split_by_chapters", codes)
        self.assertEqual(len(fragments), 2)

    def test_item_split_by_chapters_not_emitted_for_single_chapter_fragment(self) -> None:
        """1章のみで上限以内に収まる場合は item_split_by_chapters 警告は不要。"""
        chapters = [self._make_chapter("第1章", 1, 5)]
        items = [_item_stats(1, page_count=10, pdf_path="a.pdf", title="本", position=0)]
        # 第1章: 1-5 ページが交差, 6-10 は章外 → 第1章のみで上限6以内
        with patch.dict("os.environ", {"TSUNDOKENSAKU_NOTEBOOKLM_MAX_PAGES_PER_FILE": "6"}):
            fragments, warnings = NotebookLMProfile().split_items_with_warnings(
                items, chapter_loader=self._chapter_loader(chapters)
            )

        codes = [w.code for w in warnings]
        self.assertNotIn("item_split_by_chapters", codes)
        # 1フラグメントのみ
        self.assertEqual(len(fragments), 1)
        self.assertEqual(fragments[0].label, "第1章")

    # ── ページ順維持・重複ページ不除去 ─────────────────────────────────────────────

    def test_page_order_is_preserved_after_chapter_split(self) -> None:
        """章分割後もフラグメント内のページ順は元の選択順を維持する。"""
        chapters = [
            self._make_chapter("第1章", 1, 5),
            self._make_chapter("第2章", 6, 10),
        ]
        items = [_item_stats(1, page_count=10, pdf_path="a.pdf", title="本", position=0)]
        with patch.dict("os.environ", {"TSUNDOKENSAKU_NOTEBOOKLM_MAX_PAGES_PER_FILE": "6"}):
            fragments, _ = NotebookLMProfile().split_items_with_warnings(
                items, chapter_loader=self._chapter_loader(chapters)
            )

        self.assertEqual(list(fragments[0].page_numbers), list(range(1, 6)))
        self.assertEqual(list(fragments[1].page_numbers), list(range(6, 11)))

    def test_duplicate_pages_not_removed_during_page_block_split(self) -> None:
        """ページブロック分割では重複ページを除去しない（重複はそのまま保持）。"""
        chapters = [self._make_chapter("章A", 1, 5)]
        from tsundokensaku.database import PackItemRecord
        from tsundokensaku.export_stats import ItemStats
        from tsundokensaku.token_estimate import TextStats

        # ページが重複している（3,3 を含む）
        item = ItemStats(
            item=_pack_item(1, pdf_path="a.pdf", title="本", pages="1-5", position=0),
            page_numbers=[1, 2, 3, 3, 4, 5],
            stats=TextStats(cjk_chars=0, other_chars=0),
            unindexed_pages=0,
            missing_pdf=False,
        )
        with patch.dict("os.environ", {"TSUNDOKENSAKU_NOTEBOOKLM_MAX_PAGES_PER_FILE": "4"}):
            fragments, warnings = NotebookLMProfile().split_items_with_warnings(
                [item], chapter_loader=self._chapter_loader(chapters)
            )

        all_pages = [p for f in fragments for p in f.page_numbers]
        # 章Aは 1-5 なので交差は [1,2,3,3,4,5] 全部 → 上限4で 4+2 に分割
        self.assertEqual(all_pages.count(3), 2, "重複ページが除去されてはいけない")

    # ── fragment_index / fragment_count ─────────────────────────────────────────

    def test_fragment_index_and_count_are_correct_after_chapter_split(self) -> None:
        """chapter分割後のフラグメントは fragment_index と fragment_count が正確。"""
        chapters = [
            self._make_chapter("第1章", 1, 3),
            self._make_chapter("第2章", 4, 7),
            self._make_chapter("第3章", 8, 10),
        ]
        items = [_item_stats(1, page_count=10, pdf_path="a.pdf", title="本", position=0)]
        with patch.dict("os.environ", {"TSUNDOKENSAKU_NOTEBOOKLM_MAX_PAGES_PER_FILE": "4"}):
            fragments, _ = NotebookLMProfile().split_items_with_warnings(
                items, chapter_loader=self._chapter_loader(chapters)
            )

        self.assertEqual(len(fragments), 3)
        for i, frag in enumerate(fragments, start=1):
            self.assertEqual(frag.fragment_index, i, f"fragment_index at position {i}")
            self.assertEqual(frag.fragment_count, 3, f"fragment_count at position {i}")

    def test_fragment_index_and_count_are_correct_after_fallback_split(self) -> None:
        """フォールバック分割後も fragment_index と fragment_count が正確。"""
        items = [_item_stats(1, page_count=11, pdf_path="a.pdf", title="本", position=0)]
        with patch.dict("os.environ", {"TSUNDOKENSAKU_NOTEBOOKLM_MAX_PAGES_PER_FILE": "5"}):
            fragments, _ = NotebookLMProfile().split_items_with_warnings(
                items, chapter_loader=self._chapter_loader([])
            )

        # 11 → 5+5+1 = 3フラグメント
        self.assertEqual(len(fragments), 3)
        for i, frag in enumerate(fragments, start=1):
            self.assertEqual(frag.fragment_index, i)
            self.assertEqual(frag.fragment_count, 3)

    # ── label ───────────────────────────────────────────────────────────────────

    def test_label_is_chapter_title_when_split_by_chapters(self) -> None:
        """章分割フラグメントの label は章名になる。"""
        chapters = [
            self._make_chapter("はじめに", 1, 5),
            self._make_chapter("第1章 概要", 6, 10),
        ]
        items = [_item_stats(1, page_count=10, pdf_path="a.pdf", title="本", position=0)]
        with patch.dict("os.environ", {"TSUNDOKENSAKU_NOTEBOOKLM_MAX_PAGES_PER_FILE": "6"}):
            fragments, _ = NotebookLMProfile().split_items_with_warnings(
                items, chapter_loader=self._chapter_loader(chapters)
            )

        self.assertEqual(fragments[0].label, "はじめに")
        self.assertEqual(fragments[1].label, "第1章 概要")

    def test_label_is_partN_when_fallback_split(self) -> None:
        """フォールバック分割フラグメントの label は 'part1', 'part2' ... になる。"""
        items = [_item_stats(1, page_count=9, pdf_path="a.pdf", title="本", position=0)]
        with patch.dict("os.environ", {"TSUNDOKENSAKU_NOTEBOOKLM_MAX_PAGES_PER_FILE": "5"}):
            fragments, _ = NotebookLMProfile().split_items_with_warnings(
                items, chapter_loader=self._chapter_loader([])
            )

        self.assertEqual(len(fragments), 2)
        self.assertEqual(fragments[0].label, "part1")
        self.assertEqual(fragments[1].label, "part2")

    def test_label_for_oversized_chapter_block_includes_chapter_name(self) -> None:
        """巨大章のページブロック分割では label に章名が含まれる（'章名 part1' 形式）。"""
        chapters = [self._make_chapter("巨大章", 1, 15)]
        items = [_item_stats(1, page_count=15, pdf_path="a.pdf", title="本", position=0)]
        with patch.dict("os.environ", {"TSUNDOKENSAKU_NOTEBOOKLM_MAX_PAGES_PER_FILE": "6"}):
            fragments, _ = NotebookLMProfile().split_items_with_warnings(
                items, chapter_loader=self._chapter_loader(chapters)
            )

        for frag in fragments:
            self.assertIsNotNone(frag.label)
            self.assertIn("巨大章", frag.label)

    # ── standard / chat は恒等変換のまま ────────────────────────────────────────

    def test_standard_profile_ignores_chapter_loader(self) -> None:
        """StandardProfile は chapter_loader を渡しても恒等変換のまま。"""
        called = []

        def loader(pdf_path):
            called.append(pdf_path)
            return []

        items = [_item_stats(1, page_count=5, pdf_path="a.pdf", title="本", position=0)]
        fragments = StandardProfile().split_items(items, chapter_loader=loader)

        self.assertEqual(called, [])
        self.assertEqual(len(fragments), 1)
        self.assertIsNone(fragments[0].label)
        self.assertEqual(fragments[0].page_numbers, tuple(range(1, 6)))

    def test_chat_profile_ignores_chapter_loader(self) -> None:
        """ChatProfile は chapter_loader を渡しても恒等変換のまま。"""
        called = []

        def loader(pdf_path):
            called.append(pdf_path)
            return []

        items = [_item_stats(1, page_count=5, pdf_path="a.pdf", title="本", position=0)]
        fragments = ChatProfile().split_items(items, chapter_loader=loader)

        self.assertEqual(called, [])
        self.assertEqual(len(fragments), 1)
        self.assertIsNone(fragments[0].label)

    def test_chat_item_exceeds_limit_still_warned_not_split(self) -> None:
        """ChatProfile では上限超過でも item_exceeds_limit 警告（分割なし）。"""
        items = [_item_stats(1, page_count=1, cjk_chars=90000, title="巨大本", position=0)]
        plan = ChatProfile().plan(items)

        self.assertEqual(len(plan.chunks), 1)
        codes = [w.code for w in plan.warnings]
        self.assertIn("item_exceeds_limit", codes)
        self.assertNotIn("no_outline_fallback", codes)

    # ── 複数項目の混在 ───────────────────────────────────────────────────────────

    def test_within_limit_item_is_not_split_even_when_other_items_need_splitting(self) -> None:
        """上限以内の項目は chapter_loader を呼ばず、他の項目が分割されても影響なし。"""
        chapters = [self._make_chapter("第1章", 1, 10)]
        call_log = []

        def loader(pdf_path):
            call_log.append(str(pdf_path))
            return chapters

        items = [
            _item_stats(1, page_count=3, pdf_path="small.pdf", title="小さい本", position=0),
            _item_stats(2, page_count=12, pdf_path="big.pdf", title="大きい本", position=1),
        ]
        with patch.dict("os.environ", {"TSUNDOKENSAKU_NOTEBOOKLM_MAX_PAGES_PER_FILE": "5"}):
            fragments, warnings = NotebookLMProfile().split_items_with_warnings(
                items, chapter_loader=loader
            )

        # small.pdf の loader 呼び出しはない
        self.assertNotIn("small.pdf", call_log)
        # big.pdf は呼ばれる
        self.assertIn("big.pdf", call_log)
        # small.pdf は 1 フラグメント（label なし）
        small_frags = [f for f in fragments if f.item.pdf_path == "small.pdf"]
        self.assertEqual(len(small_frags), 1)
        self.assertIsNone(small_frags[0].label)


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
