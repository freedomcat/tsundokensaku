from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from tsundokensaku.pdf_export import compact_page_selection


def page_selection_label(page_numbers: list[int]) -> str:
    return compact_page_selection(page_numbers).replace("_", ", ")


def default_markdown_output_name(input_pdf: Path, page_numbers: list[int]) -> str:
    selection = compact_page_selection(page_numbers)
    safe_selection = re.sub(r"[^\w.-]+", "_", selection)
    return f"{input_pdf.stem}_p{safe_selection}.md"


def render_markdown_pages(
    *,
    title: str,
    source_name: str,
    page_numbers: list[int],
    texts: dict[int, str],
    exported_at: datetime,
) -> str:
    lines = [
        f"# {title}（抜粋）",
        "",
        f"- 出典: {title}",
        f"- 元ファイル: {source_name}",
        f"- ページ: {page_selection_label(page_numbers)}",
        f"- 抽出日: {exported_at:%Y-%m-%d}",
        "- 生成: つんどけんさく",
        "",
        "---",
        "",
    ]
    for page_number in page_numbers:
        text = (texts.get(page_number) or "").strip()
        lines.append(f"## p.{page_number}")
        lines.append("")
        lines.append(text if text else "（このページから抽出できたテキストはありません）")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_chat_chunk_header(
    *,
    pack_name: str,
    chunk_index: int,
    total_chunks: int,
    items: list[tuple[str, str]],
) -> str:
    lines = [
        f"# {pack_name}（分冊 {chunk_index}/{total_chunks}）",
        "",
        "## 収録項目",
    ]
    for title, pages in items:
        lines.append(f"- {title} ({pages})")
    lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)
