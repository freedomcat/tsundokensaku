from __future__ import annotations

import os
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from tsundokensaku.export_stats import ItemStats
from tsundokensaku.markdown_export import render_chat_chunk_header
from tsundokensaku.pdf_export import compact_page_selection, merge_rendered_pdfs
from tsundokensaku.token_estimate import TextStats, TokenEstimator, estimate_tokens
from tsundokensaku.zip_export import (
    build_chunk_filename,
    build_entry_filename,
    build_pack_zip_filename,
    build_sequenced_filename,
    sanitize_filename_component,
)


@dataclass(frozen=True)
class ExportWarning:
    code: str
    item_id: int | None
    message: str


@dataclass(frozen=True)
class ItemFragment:
    """D-0: plan/render の中間単位。

    現時点では 1資料項目 = 1フラグメントだが、chapter プロファイルの細分化では
    ItemStats を壊さずにページ範囲や章名などのラベルを持てるよう、この層を挟む。
    """

    item_stats: ItemStats
    page_numbers: tuple[int, ...]
    page_spec: str
    stats: TextStats
    label: str | None = None
    fragment_index: int = 1
    fragment_count: int = 1

    @property
    def item(self):
        return self.item_stats.item


@dataclass(frozen=True)
class ExportChunk:
    index: int
    fragments: tuple[ItemFragment, ...]
    total_pages: int
    estimated_tokens: int

    @property
    def items(self) -> tuple[ItemStats, ...]:
        """既存呼び出し側との後方互換用。D-0 では維持する。"""
        return tuple(fragment.item_stats for fragment in self.fragments)


@dataclass(frozen=True)
class ExportPlan:
    profile_name: str
    chunks: tuple[ExportChunk, ...]
    warnings: tuple[ExportWarning, ...]


@dataclass(frozen=True)
class RenderContext:
    """render_chunk が I/O を行うための注入ポイント（B-2 で接続）。

    プロファイルにDB接続やbooks_dir、FastAPIのRequest/Responseを直接持たせず、
    web.py側が組み立てた関数を経由させる（設計書 13.2 / 13.3）。

    render_pdf / render_markdown は web.py 既存の render_pdf_export /
    render_markdown_export をそのまま注入する想定。両関数は内部で
    HTTPException を送出するが、export_profiles.py 側は fastapi を一切
    import せず、注入された関数を呼ぶだけに留める（web.py への逆依存を
    作らないため。既存ロジックのコピーもしない）。
    """

    pack_name: str
    exported_at: datetime
    format: str
    resolve_pdf: Callable[[str], Path]
    render_pdf: Callable[[Path, str], tuple[bytes, str]]
    render_markdown: Callable[[Path, str], tuple[str, str]]
    total_chunks: int = 1

    def render_pdf_fragment(self, fragment: ItemFragment) -> tuple[bytes, str]:
        candidate = self.resolve_pdf(fragment.item.pdf_path)
        return self.render_pdf(candidate, fragment.page_spec)

    def render_markdown_fragment(self, fragment: ItemFragment) -> tuple[str, str]:
        candidate = self.resolve_pdf(fragment.item.pdf_path)
        return self.render_markdown(candidate, fragment.page_spec)


class ChapterLike(Protocol):
    title: str
    start_page: int
    end_page: int


ChapterLoader = Callable[[Path], list[ChapterLike]]


def _sum_stats(fragments: list[ItemFragment]) -> TextStats:
    return TextStats(
        cjk_chars=sum(entry.stats.cjk_chars for entry in fragments),
        other_chars=sum(entry.stats.other_chars for entry in fragments),
    )


def _default_fragment_page_spec(stats: ItemStats) -> str:
    return stats.item.pages


def _page_spec_from_numbers(page_numbers: tuple[int, ...]) -> str:
    return compact_page_selection(list(page_numbers)).replace("_", ",")


CHAPTER_MAX_PAGES_PER_FILE_DEFAULT = 300
CHAPTER_MAX_SOURCES_DEFAULT = 50
CHAPTER_ESTIMATED_CHARS_WARNING_GUIDELINE = 400_000


def _read_positive_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip()
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    if parsed <= 0:
        return default
    return parsed


class ExportProfile(ABC):
    name: str
    # standard は format=pdf|md|json を実行時に選べるため固定値を持たない（None）。
    # chat/chapter は将来それぞれ "md"/"pdf" を固定値として持つ想定
    # （設計書7.3/7.4）。固定値を持たないプロファイルは、実際に使う形式を
    # RenderContext.format 経由で実行時に受け取る
    primary_format: str | None

    # --- 能力フラグ（web.py が profile 名の文字列比較で分岐しないための宣言） ---
    # プレビュー・manifest を ExportPlan 由来で組み立てるか。False は現行の
    # バイト互換経路（build_export_preview_payload / build_pack_zip）を使う
    uses_plan_output: bool = True
    # plan 時に PDF アウトライン（章）情報を必要とするか
    needs_chapter_loader: bool = False
    # manifest の項目内訳に章ラベル付きの形式（PlanManifestChunk）を使うか
    manifest_uses_fragment_labels: bool = False

    # --- 概算 ---
    def estimator(self) -> TokenEstimator:
        return estimate_tokens

    def split_items(self, item_stats: list[ItemStats], *, chapter_loader: ChapterLoader | None = None) -> list[ItemFragment]:
        """将来の項目細分化ポイント。既定は 1項目 = 1フラグメント。"""
        return [
            ItemFragment(
                item_stats=stats,
                page_numbers=tuple(stats.page_numbers),
                page_spec=_default_fragment_page_spec(stats),
                stats=stats.stats,
                label=None,
            )
            for stats in item_stats
        ]

    def split_items_with_warnings(
        self,
        item_stats: list[ItemStats],
        *,
        chapter_loader: ChapterLoader | None = None,
    ) -> tuple[list[ItemFragment], tuple[ExportWarning, ...]]:
        return self.split_items(item_stats, chapter_loader=chapter_loader), ()

    # --- 分割判断（plan の基底実装から呼ばれるフック） ---
    @abstractmethod
    def item_weight(self, fragment: ItemFragment) -> int:
        """分割判断に使う重み（chat=トークン数, chapter=ページ数）。"""

    @abstractmethod
    def chunk_limit(self) -> int | None:
        """1チャンクあたりの重み上限。None は上限なし（分割・結合ロジックを行わない）。"""

    def can_merge(self, current: ExportChunk, fragment: ItemFragment) -> bool:
        """current（構築中のチャンク）に fragment を結合してよいか。既定は常に許可。"""
        return True

    # --- プラン（9.2節の貪欲法。基底実装1つ、純粋ロジック） ---
    def plan(
        self,
        item_stats: list[ItemStats],
        *,
        chapter_loader: ChapterLoader | None = None,
    ) -> ExportPlan:
        limit = self.chunk_limit()
        estimator = self.estimator()
        fragments, split_warnings = self.split_items_with_warnings(item_stats, chapter_loader=chapter_loader)
        chunks: list[ExportChunk] = []
        warnings: list[ExportWarning] = list(split_warnings)
        pending: list[ItemFragment] = []

        def build_chunk(chunk_fragments: list[ItemFragment], index: int) -> ExportChunk:
            return ExportChunk(
                index=index,
                fragments=tuple(chunk_fragments),
                total_pages=sum(len(entry.page_numbers) for entry in chunk_fragments),
                estimated_tokens=estimator(_sum_stats(chunk_fragments)),
            )

        def flush_pending() -> None:
            if pending:
                chunks.append(build_chunk(list(pending), len(chunks) + 1))
                pending.clear()

        for fragment in fragments:
            if limit is None:
                # 上限なし = 結合ロジックを行わず、各項目を独立チャンクにする
                # （standard プロファイルはこの経路で自然に「1項目=1チャンク」になる）
                flush_pending()
                chunks.append(build_chunk([fragment], len(chunks) + 1))
                continue

            item_weight = self.item_weight(fragment)
            if item_weight > limit:
                # 1項目単独で上限超過。切り捨てず単独チャンクとして確定し警告する
                flush_pending()
                chunks.append(build_chunk([fragment], len(chunks) + 1))
                warnings.append(
                    ExportWarning(
                        code="item_exceeds_limit",
                        item_id=fragment.item.id,
                        message=f"「{fragment.item.title}」は1ファイルの上限を超えるため単独で出力します",
                    )
                )
                continue

            if pending:
                pending_weight = sum(self.item_weight(entry) for entry in pending)
                provisional_chunk = build_chunk(pending, len(chunks) + 1)
                fits_limit = pending_weight + item_weight <= limit
                if fits_limit and self.can_merge(provisional_chunk, fragment):
                    pending.append(fragment)
                    continue
                flush_pending()

            pending.append(fragment)

        flush_pending()

        plan = ExportPlan(profile_name=self.name, chunks=tuple(chunks), warnings=tuple(warnings))
        extra = self.extra_warnings(plan)
        if extra:
            plan = ExportPlan(profile_name=plan.profile_name, chunks=plan.chunks, warnings=plan.warnings + tuple(extra))
        return plan

    def extra_warnings(self, plan: ExportPlan) -> tuple[ExportWarning, ...]:
        """プロファイル固有の追加警告（chapterのソース数警告等）。既定はなし。"""
        return ()

    # --- 命名 ---
    @abstractmethod
    def chunk_filename(self, chunk: ExportChunk, *, pack_name: str, format: str | None = None) -> str:
        """チャンク1つの出力ファイル名。

        format は primary_format が None のプロファイル（standard）が、
        実行時に選ばれた形式を拡張子に反映するためのオプション引数。
        primary_format を固定値で持つプロファイルは無視してよい。
        """

    def archive_filename(self, *, pack_name: str, exported_at: datetime) -> str:
        return f"{sanitize_filename_component(pack_name)}_{self.name}_{exported_at:%Y%m%d}.zip"

    # --- 出力 ---
    @abstractmethod
    def render_chunk(self, chunk: ExportChunk, ctx: RenderContext) -> bytes:
        """チャンク1つをバイト列へ描画する（B-2で実装・接続する）。"""

    def manifest_header_lines(self, plan: ExportPlan) -> list[str]:
        """manifest.md へ追記する行（分冊情報等）。既定はなし。"""
        return []


class StandardProfile(ExportProfile):
    """現行のPDF/MDエクスポート動作をそのまま位置づけるプロファイル（設計書7.2）。

    format=pdf|md|json を実行時に選べる現行仕様のため primary_format は
    固定値を持たない（None）。バイト互換を守るため、命名・出力は既存の
    zip_export / web.py の関数をそのまま呼ぶだけにとどめ、新しい規則は
    導入しない。
    """

    name = "standard"
    primary_format = None
    # バイト互換を守るため、プレビュー・ZIP とも現行経路を使う
    uses_plan_output = False

    def item_weight(self, fragment: ItemFragment) -> int:
        return len(fragment.page_numbers)

    def chunk_limit(self) -> int | None:
        return None

    def chunk_filename(self, chunk: ExportChunk, *, pack_name: str, format: str | None = None) -> str:
        # limitがNoneのため plan() は常に1項目=1チャンクを作る。現行の
        # build_entry_filename（{NN}_{書名}_p{範囲}.{ext}）をそのまま再利用する
        if format is None:
            raise ValueError("StandardProfile.chunk_filename には format（拡張子）が必要です")
        fragment = chunk.fragments[0]
        return build_entry_filename(chunk.index, fragment.item.title, fragment.page_spec, format)

    def archive_filename(self, *, pack_name: str, exported_at: datetime) -> str:
        # 現行のZIP名（{資料名}_{YYYYMMDD}.zip、profile名を含まない）を維持する
        return build_pack_zip_filename(pack_name, exported_at)

    def render_chunk(self, chunk: ExportChunk, ctx: RenderContext) -> bytes:
        # 1項目=1チャンクなので items[0] だけを見ればよい。実際のPDF解決・
        # ページ範囲検証・本文レンダリングは、web.py が注入した既存関数
        # （render_pdf_export / render_markdown_export）にそのまま委ねる
        fragment = chunk.fragments[0]
        if ctx.format == "pdf":
            content, _filename = ctx.render_pdf_fragment(fragment)
        else:
            content, _filename = ctx.render_markdown_fragment(fragment)
        return content


class ChatProfile(ExportProfile):
    name = "chat"
    primary_format = "md"

    def item_weight(self, fragment: ItemFragment) -> int:
        return self.estimator()(fragment.stats)

    def chunk_limit(self) -> int | None:
        return 80000

    def chunk_filename(self, chunk: ExportChunk, *, pack_name: str, format: str | None = None) -> str:
        return build_sequenced_filename(pack_name, self.name, chunk.index, "md")

    def render_chunk(self, chunk: ExportChunk, ctx: RenderContext) -> bytes:
        rendered_parts = []
        for fragment in chunk.fragments:
            content_str, _ = ctx.render_markdown_fragment(fragment)
            rendered_parts.append(content_str)

        items_summary = [(fragment.item.title, fragment.page_spec) for fragment in chunk.fragments]
        header = render_chat_chunk_header(
            pack_name=ctx.pack_name,
            chunk_index=chunk.index,
            total_chunks=ctx.total_chunks,
            items=items_summary
        )

        full_md = header + "\n\n---\n\n".join(rendered_parts)
        return full_md.encode("utf-8")


class ChapterProfile(ExportProfile):
    name = "chapter"
    primary_format = "pdf"
    needs_chapter_loader = True
    manifest_uses_fragment_labels = True

    def item_weight(self, fragment: ItemFragment) -> int:
        return len(fragment.page_numbers)

    def split_items_with_warnings(
        self,
        item_stats: list[ItemStats],
        *,
        chapter_loader: ChapterLoader | None = None,
    ) -> tuple[list[ItemFragment], tuple[ExportWarning, ...]]:
        limit = self.chunk_limit()
        if limit is None:
            return self.split_items(item_stats, chapter_loader=chapter_loader), ()

        fragments: list[ItemFragment] = []
        warnings: list[ExportWarning] = []
        for stats in item_stats:
            if len(stats.page_numbers) <= limit or stats.missing_pdf or not stats.item.pages.strip():
                fragments.append(
                    ItemFragment(
                        item_stats=stats,
                        page_numbers=tuple(stats.page_numbers),
                        page_spec=_default_fragment_page_spec(stats),
                        stats=stats.stats,
                    )
                )
                continue

            item_fragments, item_warnings = self._split_oversized_item(
                stats,
                limit=limit,
                chapter_loader=chapter_loader,
            )
            fragments.extend(item_fragments)
            warnings.extend(item_warnings)

        return fragments, tuple(warnings)

    def chunk_limit(self) -> int | None:
        return _read_positive_int_env(
            "TSUNDOKENSAKU_CHAPTER_MAX_PAGES_PER_FILE",
            CHAPTER_MAX_PAGES_PER_FILE_DEFAULT,
        )

    def can_merge(self, current: ExportChunk, fragment: ItemFragment) -> bool:
        if not current.fragments:
            return True
        return current.fragments[-1].item.pdf_path == fragment.item.pdf_path

    def extra_warnings(self, plan: ExportPlan) -> tuple[ExportWarning, ...]:
        warnings: list[ExportWarning] = []
        max_sources = _read_positive_int_env(
            "TSUNDOKENSAKU_CHAPTER_MAX_SOURCES",
            CHAPTER_MAX_SOURCES_DEFAULT,
        )
        if len(plan.chunks) > max_sources:
            warnings.append(
                ExportWarning(
                    code="too_many_sources",
                    item_id=None,
                    message=f"出力ファイル数が上限目安（{max_sources}件）を超えています。読み込み先サービス（NotebookLM無料枠など）の上限を確認してください",
                )
            )

        for chunk in plan.chunks:
            estimated_chars = sum(
                fragment.stats.cjk_chars + fragment.stats.other_chars
                for fragment in chunk.fragments
            )
            if estimated_chars > CHAPTER_ESTIMATED_CHARS_WARNING_GUIDELINE:
                warnings.append(
                    ExportWarning(
                        code="estimated_chars_exceed_guideline",
                        item_id=None,
                        message=(
                            f"分冊 {chunk.index} は推定文字数が{CHAPTER_ESTIMATED_CHARS_WARNING_GUIDELINE:,}字の目安を超えています"
                        ),
                    )
                )
        return tuple(warnings)

    def chunk_filename(self, chunk: ExportChunk, *, pack_name: str, format: str | None = None) -> str:
        primary_fragment = chunk.fragments[0]
        return build_chunk_filename(
            chunk.index,
            primary_fragment.item.title,
            [fragment.page_spec for fragment in chunk.fragments],
            label=primary_fragment.label if len(chunk.fragments) == 1 else None,
        )

    def render_chunk(self, chunk: ExportChunk, ctx: RenderContext) -> bytes:
        rendered_pdfs = [ctx.render_pdf_fragment(fragment)[0] for fragment in chunk.fragments]
        if len(rendered_pdfs) == 1:
            return rendered_pdfs[0]
        return merge_rendered_pdfs(rendered_pdfs)

    def manifest_header_lines(self, plan: ExportPlan) -> list[str]:
        return [
            "- 章などの単位で分割したPDFです",
            f"- 出力ファイル数: {len(plan.chunks)}",
            "- 分割・結合は出力時の最適化であり、資料棚の構成自体は変更していません",
        ]

    def _split_oversized_item(
        self,
        stats: ItemStats,
        *,
        limit: int,
        chapter_loader: ChapterLoader | None,
    ) -> tuple[list[ItemFragment], list[ExportWarning]]:
        warnings: list[ExportWarning] = []
        page_numbers = tuple(stats.page_numbers)
        chapter_fragments: list[ItemFragment] = []

        if chapter_loader is not None:
            chapters = chapter_loader(Path(stats.item.pdf_path))
            chapter_levels = [
                level
                for chapter in chapters
                if isinstance((level := getattr(chapter, "level", None)), int)
            ]
            top_level = min(chapter_levels) if chapter_levels else None
            split_chapters = [
                chapter
                for chapter in chapters
                if top_level is None or getattr(chapter, "level", None) == top_level
            ]
            for index, chapter in enumerate(split_chapters):
                # list_chapters() は次の同階層エントリの開始ページを前章の終端にも
                # 含める。出力PDF間では重複させないため、次の採用章の開始ページは
                # 次章側へ割り当てる。子階層は最小levelだけを採用して除外する。
                end_page = chapter.end_page
                if index + 1 < len(split_chapters):
                    end_page = min(end_page, split_chapters[index + 1].start_page - 1)
                chapter_pages = tuple(
                    page_number
                    for page_number in page_numbers
                    if chapter.start_page <= page_number <= end_page
                )
                if not chapter_pages:
                    continue
                if len(chapter_pages) > limit:
                    oversized_blocks = self._split_page_block_fragments(
                        stats,
                        chapter_pages,
                        limit=limit,
                        label_prefix=chapter.title,
                    )
                    chapter_fragments.extend(oversized_blocks)
                    warnings.append(
                        ExportWarning(
                            code="chapter_exceeds_limit",
                            item_id=stats.item.id,
                            message=f"「{stats.item.title}」の章「{chapter.title}」は大きいためさらに分割します",
                        )
                    )
                else:
                    chapter_fragments.append(
                        ItemFragment(
                            item_stats=stats,
                            page_numbers=chapter_pages,
                            page_spec=_page_spec_from_numbers(chapter_pages),
                            stats=stats.stats,
                            label=chapter.title,
                        )
                    )

        if chapter_fragments:
            chapter_fragments = self._finalize_fragment_indexes(chapter_fragments)
            if len(chapter_fragments) > 1:
                warnings.append(
                    ExportWarning(
                        code="item_split_by_chapters",
                        item_id=stats.item.id,
                        message=f"「{stats.item.title}」は章単位に分割して出力します",
                    )
                )
            return chapter_fragments, warnings

        fallback_fragments = self._finalize_fragment_indexes(
            self._split_page_block_fragments(stats, page_numbers, limit=limit, label_prefix=None)
        )
        warnings.append(
            ExportWarning(
                code="no_outline_fallback",
                item_id=stats.item.id,
                message=f"「{stats.item.title}」はアウトラインがないため連続ページ単位で分割します",
            )
        )
        return fallback_fragments, warnings

    def _split_page_block_fragments(
        self,
        stats: ItemStats,
        page_numbers: tuple[int, ...],
        *,
        limit: int,
        label_prefix: str | None,
    ) -> list[ItemFragment]:
        fragments: list[ItemFragment] = []
        for index, start in enumerate(range(0, len(page_numbers), limit), start=1):
            chunk_numbers = page_numbers[start:start + limit]
            if label_prefix:
                label = f"{label_prefix} part{index}"
            else:
                label = f"part{index}"
            fragments.append(
                ItemFragment(
                    item_stats=stats,
                    page_numbers=chunk_numbers,
                    page_spec=_page_spec_from_numbers(chunk_numbers),
                    stats=stats.stats,
                    label=label,
                )
            )
        return fragments

    def _finalize_fragment_indexes(self, fragments: list[ItemFragment]) -> list[ItemFragment]:
        total = len(fragments)
        return [
            ItemFragment(
                item_stats=fragment.item_stats,
                page_numbers=fragment.page_numbers,
                page_spec=fragment.page_spec,
                stats=fragment.stats,
                label=fragment.label,
                fragment_index=index,
                fragment_count=total,
            )
            for index, fragment in enumerate(fragments, start=1)
        ]


PROFILES: dict[str, ExportProfile] = {
    profile.name: profile
    for profile in (StandardProfile(), ChapterProfile(), ChatProfile())
}


def resolve_profile(name: str | None) -> ExportProfile:
    """profile名からプロファイルを解決する。未指定（None）は standard を返す。

    FastAPI非依存の純粋ロジック。不明な名前は ValueError（指定名そのものを
    メッセージに持つ）を送出する。HTTPException への変換は呼び出し側
    （web.py）の責務とし、ここでは行わない（設計書13.1: web.pyの責務は配線のみ）。
    """
    profile_name = name if name is not None else "standard"
    profile = PROFILES.get(profile_name)
    if profile is None:
        raise ValueError(profile_name)
    return profile
