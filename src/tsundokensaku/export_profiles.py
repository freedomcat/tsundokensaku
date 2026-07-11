from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from tsundokensaku.export_stats import ItemStats
from tsundokensaku.token_estimate import TextStats, TokenEstimator, estimate_tokens
from tsundokensaku.zip_export import build_entry_filename, build_pack_zip_filename, sanitize_filename_component


@dataclass(frozen=True)
class ExportWarning:
    code: str
    item_id: int | None
    message: str


@dataclass(frozen=True)
class ExportChunk:
    index: int
    items: tuple[ItemStats, ...]
    total_pages: int
    estimated_tokens: int


@dataclass(frozen=True)
class ExportPlan:
    profile_name: str
    chunks: tuple[ExportChunk, ...]
    warnings: tuple[ExportWarning, ...]


@dataclass(frozen=True)
class RenderContext:
    """render_chunk が I/O を行うための注入ポイント（B-2 で接続する）。

    プロファイルにDB接続やbooks_dirを直接持たせず、web.py側が組み立てた
    関数（PDF実体解決・本文取得）を経由させる（設計書 13.2 / 13.3）。
    """

    pack_name: str
    exported_at: datetime
    resolve_pdf: Callable[[str], Path]
    load_texts: Callable[[Path, list[int]], dict[int, str]]


def _sum_stats(item_stats: list[ItemStats]) -> TextStats:
    return TextStats(
        cjk_chars=sum(entry.stats.cjk_chars for entry in item_stats),
        other_chars=sum(entry.stats.other_chars for entry in item_stats),
    )


class ExportProfile(ABC):
    name: str
    primary_format: str

    # --- 概算 ---
    def estimator(self) -> TokenEstimator:
        return estimate_tokens

    # --- 分割判断（plan の基底実装から呼ばれるフック） ---
    @abstractmethod
    def item_weight(self, stats: ItemStats) -> int:
        """分割判断に使う項目の重み（chat=トークン数, notebooklm=ページ数）。"""

    @abstractmethod
    def chunk_limit(self) -> int | None:
        """1チャンクあたりの重み上限。None は上限なし（分割・結合ロジックを行わない）。"""

    def can_merge(self, current: ExportChunk, stats: ItemStats) -> bool:
        """current（構築中のチャンク）に stats を結合してよいか。既定は常に許可。"""
        return True

    # --- プラン（9.2節の貪欲法。基底実装1つ、純粋ロジック） ---
    def plan(self, item_stats: list[ItemStats]) -> ExportPlan:
        limit = self.chunk_limit()
        estimator = self.estimator()
        chunks: list[ExportChunk] = []
        warnings: list[ExportWarning] = []
        pending: list[ItemStats] = []

        def build_chunk(items: list[ItemStats], index: int) -> ExportChunk:
            return ExportChunk(
                index=index,
                items=tuple(items),
                total_pages=sum(len(entry.page_numbers) for entry in items),
                estimated_tokens=estimator(_sum_stats(items)),
            )

        def flush_pending() -> None:
            if pending:
                chunks.append(build_chunk(list(pending), len(chunks) + 1))
                pending.clear()

        for stats in item_stats:
            if limit is None:
                # 上限なし = 結合ロジックを行わず、各項目を独立チャンクにする
                # （standard プロファイルはこの経路で自然に「1項目=1チャンク」になる）
                flush_pending()
                chunks.append(build_chunk([stats], len(chunks) + 1))
                continue

            item_weight = self.item_weight(stats)
            if item_weight > limit:
                # 1項目単独で上限超過。切り捨てず単独チャンクとして確定し警告する
                flush_pending()
                chunks.append(build_chunk([stats], len(chunks) + 1))
                warnings.append(
                    ExportWarning(
                        code="item_exceeds_limit",
                        item_id=stats.item.id,
                        message=f"「{stats.item.title}」は1ファイルの上限を超えるため単独で出力します",
                    )
                )
                continue

            if pending:
                pending_weight = sum(self.item_weight(entry) for entry in pending)
                provisional_chunk = build_chunk(pending, len(chunks) + 1)
                fits_limit = pending_weight + item_weight <= limit
                if fits_limit and self.can_merge(provisional_chunk, stats):
                    pending.append(stats)
                    continue
                flush_pending()

            pending.append(stats)

        flush_pending()

        plan = ExportPlan(profile_name=self.name, chunks=tuple(chunks), warnings=tuple(warnings))
        extra = self.extra_warnings(plan)
        if extra:
            plan = ExportPlan(profile_name=plan.profile_name, chunks=plan.chunks, warnings=plan.warnings + tuple(extra))
        return plan

    def extra_warnings(self, plan: ExportPlan) -> tuple[ExportWarning, ...]:
        """プロファイル固有の追加警告（notebooklmのソース数警告等）。既定はなし。"""
        return ()

    # --- 命名 ---
    @abstractmethod
    def chunk_filename(self, chunk: ExportChunk, *, pack_name: str) -> str:
        """チャンク1つの出力ファイル名。"""

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

    B-1時点では既存のエクスポート処理（web.py の api_export_pack）へは接続しない。
    バイト互換を守るため、命名は既存の zip_export 関数をそのまま呼ぶだけにとどめ、
    新しいフォーマット規則は導入しない。
    """

    name = "standard"
    primary_format = "pdf"

    def item_weight(self, stats: ItemStats) -> int:
        return len(stats.page_numbers)

    def chunk_limit(self) -> int | None:
        return None

    def chunk_filename(self, chunk: ExportChunk, *, pack_name: str) -> str:
        # limitがNoneのため plan() は常に1項目=1チャンクを作る。現行の
        # build_entry_filename（{NN}_{書名}_p{範囲}.{ext}）をそのまま再利用する
        item_stats = chunk.items[0]
        return build_entry_filename(chunk.index, item_stats.item.title, item_stats.item.pages, self.primary_format)

    def archive_filename(self, *, pack_name: str, exported_at: datetime) -> str:
        # 現行のZIP名（{資料名}_{YYYYMMDD}.zip、profile名を含まない）を維持する
        return build_pack_zip_filename(pack_name, exported_at)

    def render_chunk(self, chunk: ExportChunk, ctx: RenderContext) -> bytes:
        raise NotImplementedError("render_chunk は B-2 で既存エクスポート処理へ接続する")


PROFILES: dict[str, ExportProfile] = {profile.name: profile for profile in (StandardProfile(),)}
