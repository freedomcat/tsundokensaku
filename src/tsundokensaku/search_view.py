from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote, urlparse
from zoneinfo import ZoneInfo

from markupsafe import Markup, escape

from tsundokensaku import paths
from tsundokensaku.metadata import BookMetadata, get_scrapbox_project_url, metadata_for_pdf
from tsundokensaku.tokenizer import query_highlight_terms


def highlight_query(text: str, query: str) -> Markup:
    if not text:
        return Markup("")

    # 検索パーサーと同じ解析結果を使う。除外語と演算子（- や "）は候補に入らない
    terms = sorted(set(query_highlight_terms(query)), key=len, reverse=True)
    if not terms:
        return escape(text)

    pattern = re.compile("|".join(re.escape(term) for term in terms), re.IGNORECASE)
    result = Markup("")
    last_index = 0
    for match in pattern.finditer(text):
        start, end = match.span()
        if start > last_index:
            result += escape(text[last_index:start])
        result += Markup("<mark>") + escape(text[start:end]) + Markup("</mark>")
        last_index = end
    if last_index < len(text):
        result += escape(text[last_index:])
    return result


def format_indexed_at(value: str | None) -> str:
    if not value:
        return ""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo("Asia/Tokyo")).strftime("%Y/%m/%d %H:%M")


def _now_jst() -> datetime:
    return datetime.now(ZoneInfo("Asia/Tokyo"))


def _sanitize_scrapbox_title(value: str, *, max_length: int = 80) -> str:
    cleaned = re.sub(r"\s+", " ", value).strip()
    cleaned = cleaned.replace("\n", " ").replace("\r", " ")
    cleaned = cleaned.replace("/", "／")
    return cleaned[:max_length].strip() or "検索結果"


def build_scrapbox_page_url(title: str, body: str) -> str | None:
    base_url = get_scrapbox_project_url()
    if not base_url:
        return None
    return f"{base_url}/{quote(title, safe='')}?body={quote(body, safe='')}"


def _scrapbox_page_label(scrapbox_url: str | None, fallback: str) -> str:
    if not scrapbox_url:
        return fallback
    page_name = unquote(Path(urlparse(scrapbox_url).path).name)
    return page_name or fallback


def build_search_result_rows(
    results,
    *,
    books_dir: Path,
    metadata_by_stem: dict[str, BookMetadata],
) -> list[dict[str, object]]:
    rendered_results: list[dict[str, object]] = []
    for result in results:
        if result.kind == "pdf":
            metadata = metadata_for_pdf(result.path or "", metadata_by_stem)
            rendered_results.append(
                {
                    "title": result.title,
                    "path": result.path,
                    "page_number": result.page_number,
                    "page_numbers": [result.page_number] if result.page_number is not None else [],
                    "page_summary": f"p.{result.page_number}" if result.page_number is not None else "",
                    "page_urls": [
                        paths.raw_pdf_url(result.path or "", books_dir, page_number=result.page_number)
                    ]
                    if result.page_number is not None
                    else [],
                    "snippet": result.snippet,
                    "kind": "pdf",
                    "cover_url": metadata.cover_url if metadata else None,
                    "open_url": paths.raw_pdf_url(result.path or "", books_dir, page_number=result.page_number),
                    "scrapbox_url": metadata.scrapbox_url if metadata else None,
                }
            )
        else:
            rendered_results.append(
                {
                    "title": result.title,
                    "path": result.path,
                    "page_number": result.page_number,
                    "snippet": result.snippet,
                    "kind": result.kind,
                    "cover_url": result.cover_url,
                    "open_url": result.open_url,
                    "scrapbox_url": result.scrapbox_url or result.open_url,
                }
            )

    return rendered_results


def finalize_search_result_rows(rendered_results: list[dict[str, object]], *, books_dir: Path, sort: str, group: str) -> list[dict[str, object]]:
    sorted_results = sort_results(rendered_results, sort)
    if group == "book":
        sorted_results = group_pdf_results(sorted_results)

    for result in sorted_results:
        if result.get("kind") == "pdf":
            page_numbers = result.get("page_numbers") or []
            if page_numbers:
                result["page_urls"] = [
                    paths.raw_pdf_url(result.get("path") or "", books_dir, page_number=page_number)
                    for page_number in page_numbers
                ]
            elif result.get("page_number") is not None:
                result["page_urls"] = [
                    paths.raw_pdf_url(result.get("path") or "", books_dir, page_number=int(result["page_number"]))
                ]
            else:
                result["page_urls"] = []
    return sorted_results


def normalize_search_group(values: list[str] | str | None) -> str:
    """group パラメータを正規化する。

    フォームは hidden の group=none とチェックボックスの group=book を併送する。
    "book" があればまとめ表示、"none" のみなら個別表示、未指定（旧URL・ホームからの
    検索）はまとめ表示をデフォルトとする。
    """
    if values is None:
        values = []
    elif isinstance(values, str):
        values = [values]
    if "book" in values:
        return "book"
    if "none" in values:
        return "none"
    return "book"


def normalize_search_match(values: list[str] | str | None) -> str:
    """match パラメータを正規化する。

    フォームは hidden の match=any とチェックボックスの match=all を併送するため
    値がリストで届く。"all" があれば AND、"any" のみなら OR、未指定（旧URL）は AND。
    """
    if values is None:
        values = []
    elif isinstance(values, str):
        values = [values]
    if "all" in values:
        return "all"
    if "any" in values:
        return "any"
    return "all"


def build_search_scrapbox_body(
    *,
    query: str,
    scope: str,
    sort: str,
    group: str,
    match: str = "all",
    results: list[dict[str, object]],
) -> tuple[str, str]:
    now = _now_jst()
    title_query = _sanitize_scrapbox_title(query or "検索結果")
    page_title = _sanitize_scrapbox_title(f"検索結果 {title_query} {now.strftime('%Y-%m-%d %H:%M')}")
    lines = [
        "#つんどけんさく",
        "",
        f"検索語: {query or '(未入力)'}",
        f"検索範囲: {scope}",
        f"語の一致: {'すべての語を含む' if match == 'all' else 'いずれかの語を含む'}",
        f"並び順: {sort}",
        f"まとめ方: {group}",
        f"作成日時: {now.strftime('%Y/%m/%d %H:%M')} JST",
        "",
        "結果一覧",
    ]
    for index, result in enumerate(results, start=1):
        title = str(result.get("title") or "")
        kind = str(result.get("kind") or "")
        snippet = str(result.get("snippet") or "").replace("\n", " ").strip()
        scrapbox_url = str(result.get("scrapbox_url") or "")
        page_summary = str(result.get("page_summary") or "")
        detail_parts = [part for part in [kind, page_summary] if part]
        lines.append(f"{index}. {title}")
        if detail_parts:
            lines.append(f"   {' / '.join(detail_parts)}")
        if snippet:
            lines.append(f"   {snippet}")
        if scrapbox_url:
            lines.append(f"   scrapbox: [{_scrapbox_page_label(scrapbox_url, title)}]")
        lines.append("")

    return page_title, "\n".join(lines).strip()


def sort_results(results: list[dict], sort: str) -> list[dict]:
    if sort == "title":
        return sorted(results, key=lambda result: (result["title"], result["page_number"] is None, result["page_number"] or 0))
    if sort == "page":
        return sorted(results, key=lambda result: (result["page_number"] is None, result["page_number"] or 0, result["title"]))
    if sort == "scrapbox":
        return sorted(results, key=lambda result: (result["scrapbox_url"] is None, result["title"], result["page_number"] is None, result["page_number"] or 0))
    return results


def group_pdf_results(results: list[dict]) -> list[dict]:
    grouped: list[dict] = []
    pdf_groups: dict[str, dict] = {}

    for result in results:
        if result.get("kind") != "pdf":
            grouped.append(result)
            continue

        title = str(result.get("title") or "")
        if title not in pdf_groups:
            pdf_groups[title] = {
                **result,
                "page_numbers": [],
                "snippets": [],
                "hit_count": 0,
                "page_summary": "",
                "page_urls": [],
            }
            grouped.append(pdf_groups[title])

        group = pdf_groups[title]
        page_number = result.get("page_number")
        if page_number is not None and page_number not in group["page_numbers"]:
            group["page_numbers"].append(page_number)
        snippet = result.get("snippet")
        if snippet:
            group["snippets"].append(snippet)
        group["hit_count"] += 1

    for group in pdf_groups.values():
        group["page_numbers"] = sorted(group["page_numbers"])
        page_numbers = group["page_numbers"]
        if page_numbers:
            if len(page_numbers) <= 4:
                group["page_summary"] = ", ".join(f"p.{page}" for page in page_numbers)
            else:
                group["page_summary"] = ", ".join(f"p.{page}" for page in page_numbers[:4]) + f" +{len(page_numbers) - 4}件"
        else:
            group["page_summary"] = ""
        if group["snippets"]:
            group["snippet"] = group["snippets"][0]

    return grouped
