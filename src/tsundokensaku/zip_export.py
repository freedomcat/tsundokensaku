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


@dataclass(frozen=True)
class PackExportEntry:
    index: int
    title: str
    page_label: str
    filename: str
    content: bytes


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
