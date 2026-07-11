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


def _sum_stats(item_stats: list[ItemStats]) -> TextStats:
    return TextStats(
        cjk_chars=sum(entry.stats.cjk_chars for entry in item_stats),
        other_chars=sum(entry.stats.other_chars for entry in item_stats),
    )


class ExportProfile(ABC):
    name: str
    # standard は format=pdf|md|json を実行時に選べるため固定値を持たない（None）。
    # chat/notebooklm は将来それぞれ "md"/"pdf" を固定値として持つ想定
    # （設計書7.3/7.4）。固定値を持たないプロファイルは、実際に使う形式を
    # RenderContext.format 経由で実行時に受け取る
    primary_format: str | None

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

    def item_weight(self, stats: ItemStats) -> int:
        return len(stats.page_numbers)

    def chunk_limit(self) -> int | None:
        return None

    def chunk_filename(self, chunk: ExportChunk, *, pack_name: str, format: str | None = None) -> str:
        # limitがNoneのため plan() は常に1項目=1チャンクを作る。現行の
        # build_entry_filename（{NN}_{書名}_p{範囲}.{ext}）をそのまま再利用する
        if format is None:
            raise ValueError("StandardProfile.chunk_filename には format（拡張子）が必要です")
        item_stats = chunk.items[0]
        return build_entry_filename(chunk.index, item_stats.item.title, item_stats.item.pages, format)

    def archive_filename(self, *, pack_name: str, exported_at: datetime) -> str:
        # 現行のZIP名（{資料名}_{YYYYMMDD}.zip、profile名を含まない）を維持する
        return build_pack_zip_filename(pack_name, exported_at)

    def render_chunk(self, chunk: ExportChunk, ctx: RenderContext) -> bytes:
        # 1項目=1チャンクなので items[0] だけを見ればよい。実際のPDF解決・
        # ページ範囲検証・本文レンダリングは、web.py が注入した既存関数
        # （render_pdf_export / render_markdown_export）にそのまま委ねる
        item = chunk.items[0].item
        candidate = ctx.resolve_pdf(item.pdf_path)
        if ctx.format == "pdf":
            content, _filename = ctx.render_pdf(candidate, item.pages)
        else:
            content, _filename = ctx.render_markdown(candidate, item.pages)
        return content


PROFILES: dict[str, ExportProfile] = {profile.name: profile for profile in (StandardProfile(),)}
