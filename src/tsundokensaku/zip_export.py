from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO


MAX_FILENAME_BYTES = 255
FILENAME_ELLIPSIS = "…"


def sanitize_filename_component(value: str) -> str:
    cleaned = re.sub(r"[^\w.-]+", "_", value).strip("_")
    return cleaned or "untitled"


def build_pack_zip_filename(pack_name: str, exported_at: datetime) -> str:
    return f"{sanitize_filename_component(pack_name)}_{exported_at:%Y%m%d}.zip"


def _count_pages_in_spec(page_spec: str) -> int:
    """spec文字列のページ数を概算する（ファイル名短縮の表示用）。

    pdf_export.parse_page_selection は実ページ数を超える番号があると
    ValueError を送出する検証込みの実装で、ここでは検証不要な概算件数
    だけが欲しいため使わず、専用の簡易カウントにしている。
    """
    total = 0
    for part in page_spec.split(","):
        chunk = part.strip()
        if not chunk:
            continue
        if "-" in chunk:
            start_text, end_text = chunk.split("-", 1)
            try:
                start, end = int(start_text), int(end_text)
            except ValueError:
                total += 1
                continue
            total += max(end - start + 1, 1)
        else:
            total += 1
    return total


def _truncate_to_byte_limit(text: str, max_bytes: int) -> str:
    """UTF-8バイト長が max_bytes に収まるまで、文字単位で末尾から削る。"""
    truncated = text
    while truncated and len(truncated.encode("utf-8")) > max_bytes:
        truncated = truncated[:-1]
    return truncated


def build_entry_filename(index: int, title: str, page_spec: str, extension: str) -> str:
    """ZIP内の個別ファイル名を組み立てる。

    render_pdf_export / render_markdown_export が返すファイル名は元PDFの
    ファイル名（stem）ベースで書籍タイトルを含まないため使わず、
    連番 + 書籍タイトル + ページ範囲から独自に組み立てる。

    255バイト（多くのファイルシステムの上限）を超える場合は、優先順位
    「連番 > 書籍名 > ページ範囲」で短縮する。まずページ範囲を「Nページ」
    表記に縮め、それでも収まらなければ書籍名を … 付きで切り詰める。
    詳細なページ範囲は常に manifest.md 側（page_label）に残る。
    """
    safe_title = sanitize_filename_component(title)
    safe_pages = sanitize_filename_component(page_spec)
    prefix = f"{index:02d}_"
    suffix = f".{extension}"

    full = f"{prefix}{safe_title}_p{safe_pages}{suffix}"
    if len(full.encode("utf-8")) <= MAX_FILENAME_BYTES:
        return full

    short_pages = f"{_count_pages_in_spec(page_spec)}ページ"
    shortened = f"{prefix}{safe_title}_{short_pages}{suffix}"
    if len(shortened.encode("utf-8")) <= MAX_FILENAME_BYTES:
        return shortened

    fixed = f"{prefix}{FILENAME_ELLIPSIS}_{short_pages}{suffix}"
    available_bytes = MAX_FILENAME_BYTES - len(fixed.encode("utf-8"))
    truncated_title = _truncate_to_byte_limit(safe_title, max(available_bytes, 0))
    return f"{prefix}{truncated_title}{FILENAME_ELLIPSIS}_{short_pages}{suffix}"


def build_sequenced_filename(base_name: str, profile_name: str, index: int, ext: str) -> str:
    safe_base = sanitize_filename_component(base_name)
    prefix = f"{safe_base}_{profile_name}_"
    suffix = f"{index:02d}.{ext}"
    full = f"{prefix}{suffix}"
    if len(full.encode("utf-8")) <= MAX_FILENAME_BYTES:
        return full

    fixed = f"_{profile_name}_{suffix}"
    available_bytes = MAX_FILENAME_BYTES - len(fixed.encode("utf-8"))
    truncated_base = _truncate_to_byte_limit(safe_base, max(available_bytes, 0))
    return f"{truncated_base}{fixed}"


def _build_capped_filename(index: int, title: str, detail_candidates: list[str], extension: str) -> str:
    safe_title = sanitize_filename_component(title)
    prefix = f"{index:02d}_"
    suffix = f".{extension}"

    for detail in detail_candidates:
        full = f"{prefix}{safe_title}_{detail}{suffix}"
        if len(full.encode("utf-8")) <= MAX_FILENAME_BYTES:
            return full

    fallback_detail = detail_candidates[-1]
    fixed = f"{prefix}{FILENAME_ELLIPSIS}_{fallback_detail}{suffix}"
    available_bytes = MAX_FILENAME_BYTES - len(fixed.encode("utf-8"))
    truncated_title = _truncate_to_byte_limit(safe_title, max(available_bytes, 0))
    return f"{prefix}{truncated_title}{FILENAME_ELLIPSIS}_{fallback_detail}{suffix}"


def build_chunk_filename(
    index: int,
    title: str,
    page_specs: list[str],
    *,
    label: str | None = None,
) -> str:
    safe_page_specs = [sanitize_filename_component(page_spec) for page_spec in page_specs]
    total_pages = sum(_count_pages_in_spec(page_spec) for page_spec in page_specs)

    detail_candidates: list[str] = []
    if len(page_specs) == 1:
        pages_detail = f"p{safe_page_specs[0]}"
        if label:
            detail_candidates.append(f"{sanitize_filename_component(label)}_{pages_detail}")
        detail_candidates.append(pages_detail)
        detail_candidates.append(f"{total_pages}ページ")
    else:
        detail_candidates.append(f"p{'_'.join(safe_page_specs)}")
        detail_candidates.append(f"{total_pages}ページ")

    return _build_capped_filename(index, title, list(dict.fromkeys(detail_candidates)), "pdf")


@dataclass(frozen=True)
class PackExportEntry:
    index: int
    title: str
    page_label: str
    filename: str
    content: bytes


@dataclass(frozen=True)
class PlanManifestFragment:
    title: str
    pages: str
    label: str | None = None


@dataclass(frozen=True)
class PlanManifestChunk:
    filename: str
    fragments: list[PlanManifestFragment]


def render_pack_manifest(*, pack_name: str, exported_at: datetime, entries: list[PackExportEntry]) -> str:
    lines = [
        f"# {pack_name}（資料一式）",
        "",
        "- 生成: つんどけんさく",
        f"- 書き出し日時: {exported_at:%Y-%m-%d %H:%M}",
        f"- 収録: {len(entries)}冊",
        "",
        "## 収録内容",
        "",
    ]
    for entry in entries:
        lines.append(f"{entry.index}. {entry.title} — p.{entry.page_label} （{entry.filename}）")
    lines.append("")
    lines.append(
        f"NotebookLM等にアップロードする場合、上記{len(entries)}個のファイルがそれぞれ1ソースになります。"
    )
    return "\n".join(lines).rstrip() + "\n"


def build_pack_zip(*, pack_name: str, entries: list[PackExportEntry], exported_at: datetime) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.md", render_pack_manifest(pack_name=pack_name, exported_at=exported_at, entries=entries))
        for entry in entries:
            archive.writestr(entry.filename, entry.content)
    return buffer.getvalue()


def render_plan_manifest(
    *,
    pack_name: str,
    exported_at: datetime,
    profile_name: str,
    chunks: list[tuple[str, list[tuple[str, str]]] | PlanManifestChunk],
    header_lines: list[str],
    warnings: list[str],
) -> str:
    """profile 指定エクスポート用の manifest（設計書 10.3 / 14）。

    standard の render_pack_manifest は PackExportEntry（1項目=1エントリ）
    前提のため、複数項目チャンク（chat の分冊・chapter の結合）の項目
    内訳を表現できない。ExportPlan から呼び出し側が組み立てた
    「チャンク（出力ファイル） → 収録項目（書名・ページ範囲）」の一覧と、
    plan の警告メッセージをそのまま並べる。export_profiles.py の型には
    依存させず（循環import回避）プレーンなタプル/文字列で受け取る。
    """
    lines = [
        f"# {pack_name}（資料一式・{profile_name}）",
        "",
        "- 生成: つんどけんさく",
        f"- 書き出し日時: {exported_at:%Y-%m-%d %H:%M}",
        f"- プロファイル: {profile_name}",
        f"- 出力ファイル数: {len(chunks)}",
    ]
    lines.extend(header_lines)
    lines.append("")
    lines.append("## 収録内容")
    lines.append("")
    for index, chunk in enumerate(chunks, start=1):
        if isinstance(chunk, PlanManifestChunk):
            lines.append(f"{index}. {chunk.filename}")
            current_title: str | None = None
            for fragment in chunk.fragments:
                if fragment.title != current_title:
                    lines.append(f"   - {fragment.title}")
                    current_title = fragment.title
                if fragment.label:
                    lines.append(f"     - {fragment.label} — p.{fragment.pages}")
                else:
                    lines.append(f"     - p.{fragment.pages}")
            continue

        filename, items = chunk
        lines.append(f"{index}. {filename}")
        for title, pages in items:
            lines.append(f"   - {title} — p.{pages}")
    if warnings:
        lines.append("")
        lines.append("## 警告")
        lines.append("")
        for warning in warnings:
            lines.append(f"- {warning}")
    return "\n".join(lines).rstrip() + "\n"


def build_pack_zip_with_manifest(*, entries: list[PackExportEntry], manifest: str) -> bytes:
    """既に組み立て済みの manifest 文字列でZIP化する（profile指定エクスポート用）。

    standard 用の build_pack_zip は render_pack_manifest をハードコードして
    呼ぶバイト互換維持のための既存関数のため変更せず、別関数として追加する。
    """
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.md", manifest)
        for entry in entries:
            archive.writestr(entry.filename, entry.content)
    return buffer.getvalue()
