import unittest
from datetime import datetime
from pathlib import Path

from tsundokensaku.database import PackItemRecord
from tsundokensaku.export_profiles import (
    PROFILES,
    ExportChunk,
    ExportPlan,
    ExportProfile,
    ExportWarning,
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

    def item_weight(self, stats: ItemStats) -> int:
        return len(stats.page_numbers)

    def chunk_limit(self):
        return self._limit

    def can_merge(self, current: ExportChunk, stats: ItemStats) -> bool:
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
    def test_one_chunk_per_item(self) -> None:
        items = [
            _item_stats(1, page_count=3, title="本A", position=0),
            _item_stats(2, page_count=2, title="本B", position=1),
        ]
        plan = StandardProfile().plan(items)

        self.assertEqual(len(plan.chunks), 2)
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

    def test_manifest_header_lines_defaults_to_empty(self) -> None:
        plan = StandardProfile().plan([_item_stats(1, page_count=1)])
        self.assertEqual(StandardProfile().manifest_header_lines(plan), [])


class BasePlanTest(unittest.TestCase):
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


class ResolveProfileTest(unittest.TestCase):
    def test_none_resolves_to_standard(self) -> None:
        self.assertIsInstance(resolve_profile(None), StandardProfile)

    def test_standard_name_resolves_to_standard(self) -> None:
        self.assertIsInstance(resolve_profile("standard"), StandardProfile)

    def test_unknown_name_raises_value_error_with_name_as_message(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            resolve_profile("unknown")
        self.assertEqual(str(ctx.exception), "unknown")


if __name__ == "__main__":
    unittest.main()
