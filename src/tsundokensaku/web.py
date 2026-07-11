from __future__ import annotations

import logging
import os
import base64
import re
import sqlite3
import threading
import time
import shutil
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import quote, urlencode
from urllib.parse import unquote, urlparse
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Body, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from tsundokensaku.database import (
    SEARCH_MATCH_MODES,
    SEARCH_SCOPES,
    clear_active_pack,
    connect,
    create_pack,
    delete_pack,
    ensure_pack_schema,
    get_book,
    get_pack,
    get_pack_items,
    import_cart_as_pack,
    list_books,
    list_packs,
    pack_items_as_cart,
    pack_items_as_items,
    parse_query,
    replace_pack_item_entries,
    replace_pack_items,
    resolve_active_pack_id,
    search,
    set_active_pack,
    sync_kindle_books,
    sync_memos,
    update_pack,
)
from tsundokensaku.database import initialize
from tsundokensaku.export_profiles import PROFILES, ExportProfile, RenderContext, resolve_profile
from tsundokensaku.export_stats import ItemStats, collect_item_stats
from tsundokensaku.indexer import find_pdfs, index_books
from tsundokensaku.metadata import (
    BookMetadata,
    ENV_FILE,
    find_export_json,
    load_metadata_by_pdf_stem,
    metadata_for_pdf,
    get_scrapbox_project_url,
)
from tsundokensaku.markdown_export import default_markdown_output_name, render_markdown_pages
from tsundokensaku.pdf_export import default_output_path, parse_page_selection, render_selected_pages
from tsundokensaku.pdf_outline import get_page_count, list_chapters
from tsundokensaku.pdf_thumbnail import render_thumbnail_detail, render_thumbnails
from tsundokensaku.token_estimate import ESTIMATOR_NAME, TextStats, estimate_tokens
from tsundokensaku.tokenizer import query_highlight_terms
from tsundokensaku.zip_export import (
    PackExportEntry,
    build_entry_filename,
    build_pack_zip,
    build_pack_zip_filename,
    sanitize_filename_component,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BOOKS_DIR = Path("data/books")
CONTAINER_BOOKS_DIRS = (Path("/data/books"), Path("/books/tech"))
DEFAULT_DB_PATH = Path("data/index.db")
PDF_EXPORT_SAVE_DIR_ENV = "PDF_EXPORT_SAVE_DIR"


def _find_project_root() -> Path:
    candidates = [
        Path(os.environ.get("TSUNDOKENSAKU_ROOT", "")) if os.environ.get("TSUNDOKENSAKU_ROOT") else None,
        Path.cwd(),
        Path(__file__).resolve().parents[2],
    ]
    for candidate in candidates:
        if candidate and (candidate / "templates").is_dir() and (candidate / "static").is_dir():
            return candidate
    return Path(__file__).resolve().parents[2]


PROJECT_ROOT = _find_project_root()
TEMPLATES_DIR = PROJECT_ROOT / "templates"
STATIC_DIR = PROJECT_ROOT / "static"
SCRAPBOX_EXPORT_CACHE = PROJECT_ROOT / "shino-books_imported.json"
LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.INFO)
if not LOGGER.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    LOGGER.addHandler(handler)
LOGGER.propagate = False

INDEX_PROGRESS_LOCK = threading.Lock()
INDEX_PROGRESS: dict[str, object] = {
    "running": False,
    "current": 0,
    "total": 0,
    "title": "",
    "message": "",
    "updated_at": "",
}

app = FastAPI(title="tsundokensaku")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["highlight_query"] = lambda text, query="": highlight_query(text, query)
templates.env.filters["format_indexed_at"] = lambda value: format_indexed_at(value)


def get_books_dir() -> Path:
    return Path(os.environ.get("BOOKS_DIR", str(DEFAULT_BOOKS_DIR)))


def get_db_path() -> Path:
    db_dir = Path(os.environ.get("DB_DIR", str(DEFAULT_DB_PATH.parent)))
    return db_dir / DEFAULT_DB_PATH.name


def get_pdf_export_save_dir() -> Path | None:
    configured = os.environ.get(PDF_EXPORT_SAVE_DIR_ENV, "").strip()
    return Path(configured).expanduser() if configured else None


templates.env.globals["pdf_export_save_dir"] = get_pdf_export_save_dir
templates.env.globals["is_pdf_export_save_dir_configured"] = lambda: get_pdf_export_save_dir() is not None


DEMO_MODE_UPLOAD_MESSAGE = "Upload is disabled in demo mode."
DEMO_MODE_SETTING_MESSAGE = "デモモードのため無効です"


def is_demo_mode() -> bool:
    return os.environ.get("DEMO_MODE", "").strip().lower() == "true"


templates.env.globals["is_demo_mode"] = is_demo_mode


def get_metadata() -> dict[str, BookMetadata]:
    return load_metadata_by_pdf_stem(find_export_json(PROJECT_ROOT))


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
                        raw_pdf_url(result.path or "", books_dir, page_number=result.page_number)
                    ]
                    if result.page_number is not None
                    else [],
                    "snippet": result.snippet,
                    "kind": "pdf",
                    "cover_url": metadata.cover_url if metadata else None,
                    "open_url": raw_pdf_url(result.path or "", books_dir, page_number=result.page_number),
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
                    raw_pdf_url(result.get("path") or "", books_dir, page_number=page_number)
                    for page_number in page_numbers
                ]
            elif result.get("page_number") is not None:
                result["page_urls"] = [
                    raw_pdf_url(result.get("path") or "", books_dir, page_number=int(result["page_number"]))
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


def build_search_result_rows_context(
    query: str,
    *,
    sort: str,
    scope: str,
    group: str,
    match: str = "all",
    books_dir: Path,
    db_path: Path,
) -> tuple[list[dict[str, object]], str]:
    started_at = time.perf_counter()
    export_json = find_export_json(PROJECT_ROOT)
    metadata_started_at = time.perf_counter()
    metadata_by_stem = load_metadata_by_pdf_stem(export_json)
    metadata_elapsed = time.perf_counter() - metadata_started_at
    normalized_scope = scope if scope in SEARCH_SCOPES else "all"
    normalized_match = match if match in SEARCH_MATCH_MODES else "all"
    connection = connect(db_path)
    search_elapsed = 0.0
    try:
        search_started_at = time.perf_counter()
        results = (
            search(connection, query, limit=50, scope=normalized_scope, match=normalized_match)
            if query.strip()
            else []
        )
        search_elapsed = time.perf_counter() - search_started_at
    finally:
        connection.close()

    render_started_at = time.perf_counter()
    rendered_results = build_search_result_rows(results, books_dir=books_dir, metadata_by_stem=metadata_by_stem)
    render_elapsed = time.perf_counter() - render_started_at
    finalize_started_at = time.perf_counter()
    rendered_results = finalize_search_result_rows(rendered_results, books_dir=books_dir, sort=sort, group=group)
    finalize_elapsed = time.perf_counter() - finalize_started_at
    total_elapsed = time.perf_counter() - started_at
    LOGGER.info(
        "search timing query=%r scope=%s match=%s sort=%s group=%s metadata=%.4fs db=%.4fs render=%.4fs finalize=%.4fs total=%.4fs results=%d",
        query,
        normalized_scope,
        normalized_match,
        sort,
        group,
        metadata_elapsed,
        search_elapsed,
        render_elapsed,
        finalize_elapsed,
        total_elapsed,
        len(rendered_results),
    )
    return rendered_results, normalized_scope


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


def _set_index_progress(running: bool, current: int, total: int, title: str = "", message: str = "") -> None:
    with INDEX_PROGRESS_LOCK:
        INDEX_PROGRESS.update(
            {
                "running": running,
                "current": current,
                "total": total,
                "title": title,
                "message": message,
            }
        )


def _get_index_progress() -> dict[str, object]:
    with INDEX_PROGRESS_LOCK:
        return dict(INDEX_PROGRESS)


def _run_index_job(force_paths: set[str] | None = None) -> None:
    books_dir = get_books_dir()
    db_path = get_db_path()
    try:
        index_books(
            books_dir=books_dir,
            db_path=db_path,
            progress_callback=_set_index_progress,
            force_paths=force_paths,
        )
        _set_index_progress(
            False,
            int(_get_index_progress().get("current", 0)),
            int(_get_index_progress().get("total", 0)),
            "",
            f"Indexed books under {books_dir}",
        )
    except Exception as exc:  # pragma: no cover - surfaced in browser
        _set_index_progress(
            False,
            int(_get_index_progress().get("current", 0)),
            int(_get_index_progress().get("total", 0)),
            "",
            f"Error: {exc}",
        )


# TODO(phase3b-path-resolution-dedup): export_stats._resolve_pdf_path が本関数と
# CONTAINER_BOOKS_DIRS を意図的に複製している（循環import回避のため。詳細は
# export_stats.py 冒頭のコメントと docs/ai-export-optimization-design.md 5.9）。
# Phase 3B で共通モジュールへ統合する想定。
def resolve_pdf_path(pdf_path: str | Path, books_dir: Path) -> Path | None:
    candidate = Path(pdf_path)
    books_root = books_dir.resolve()

    candidates: list[Path] = []
    if candidate.is_absolute():
        candidates.append(candidate)
        for container_books_dir in CONTAINER_BOOKS_DIRS:
            try:
                candidates.append(books_root / candidate.relative_to(container_books_dir))
            except ValueError:
                pass
    else:
        candidates.append(books_root / candidate)

    candidates.append(books_root / candidate.name)

    for path in candidates:
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(books_root)
        except ValueError:
            continue
        if resolved.is_file():
            return relative

    return None


def pdf_url(pdf_path: str | Path, books_dir: Path, *, page_number: int | None = None) -> str | None:
    relative = resolve_pdf_path(pdf_path, books_dir)
    if relative is None:
        return None
    url = f"/view/{quote(str(relative).replace(os.sep, '/'))}"
    if page_number is not None:
        url = f"{url}?page={page_number}"
    return url


def raw_pdf_url(pdf_path: str | Path, books_dir: Path, *, page_number: int | None = None) -> str | None:
    relative = resolve_pdf_path(pdf_path, books_dir)
    if relative is None:
        return None
    url = f"/pdf/{quote(str(relative).replace(os.sep, '/'))}"
    if page_number is not None:
        url = f"{url}#page={page_number}"
    return url


def get_pdf_stats(books_dir: Path) -> dict[str, int]:
    pdf_paths = list(find_pdfs(books_dir))
    return {"pdf_count": len(pdf_paths)}


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


def get_db_stats(db_path: Path) -> dict[str, int]:
    connection = None
    try:
        connection = connect(db_path)
        books = list_books(connection)
        page_count = connection.execute("SELECT COUNT(*) AS count FROM pages").fetchone()["count"]
        grouped = {row["source_type"]: int(row["count"]) for row in connection.execute(
            "SELECT source_type, COUNT(*) AS count FROM books GROUP BY source_type"
        ).fetchall()}
        return {
            "book_count": len(books),
            "pdf_count": grouped.get("pdf", 0),
            "kindle_count": grouped.get("kindle", 0),
            "page_count": int(page_count),
        }
    except sqlite3.OperationalError:
        return {"book_count": 0, "pdf_count": 0, "kindle_count": 0, "page_count": 0}
    finally:
        if connection is not None:
            connection.close()


def get_library_items(books_dir: Path, db_path: Path) -> dict[str, object]:
    metadata_by_stem = get_metadata()
    indexed_paths: dict[str, str] = {}
    kindle_books = []
    pdf_books: list[object] = []
    connection = None
    try:
        connection = connect(db_path)
        books = list_books(connection)
        pdf_books = [book for book in books if book.source_type == "pdf" and book.path is not None]
        indexed_paths = {
            str(row["path"]): str(row["indexed_at"])
            for row in connection.execute(
                "SELECT path, indexed_at FROM books WHERE source_type = 'pdf' AND path IS NOT NULL"
            ).fetchall()
        }
        kindle_books = [book for book in books if book.source_type == "kindle"]
    except sqlite3.OperationalError:
        books = []
    finally:
        if connection is not None:
            connection.close()

    pdf_items = [
        {
            "path": Path(book.path) if book.path is not None else None,
            "title": book.title,
            "indexed": True,
            "indexed_at": indexed_paths.get(str(book.path)),
            "cover_url": (metadata.cover_url if (metadata := metadata_for_pdf(book.path or "", metadata_by_stem)) else None),
            "open_url": raw_pdf_url(book.path or "", books_dir),
            "scrapbox_url": metadata.scrapbox_url if metadata else None,
        }
        for book in pdf_books
    ]
    kindle_items = [
        {
            "title": book.title,
            "external_id": book.external_id,
            "indexed_at": book.indexed_at,
            "path": book.external_id or book.title,
            "open_url": book.open_url,
            "scrapbox_url": book.scrapbox_url,
            "cover_url": book.cover_url,
        }
        for book in kindle_books
    ]
    return {
        "pdf_count": len(pdf_books),
        "books_count": len(books),
        "kindle_count": len(kindle_books),
        "pdf_items": pdf_items,
        "kindle_items": kindle_items,
    }


def import_pdfs_from_directory(source_dir: Path, books_dir: Path) -> tuple[int, int, int]:
    source_root = source_dir.expanduser().resolve()
    books_root = books_dir.expanduser().resolve()

    if not source_root.exists():
        raise FileNotFoundError(source_root)
    if not source_root.is_dir():
        raise NotADirectoryError(source_root)
    if source_root == books_root or source_root.is_relative_to(books_root) or books_root.is_relative_to(source_root):
        raise ValueError("source_dir と books_dir は重ならない場所を指定してください")

    pdf_paths = [path for path in sorted(source_root.rglob("*.pdf")) if path.is_file()]
    books_root.mkdir(parents=True, exist_ok=True)

    copied = 0
    skipped = 0
    for pdf_path in pdf_paths:
        destination = books_root / pdf_path.relative_to(source_root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            skipped += 1
            continue
        shutil.copy2(pdf_path, destination)
        copied += 1

    return copied, skipped, len(pdf_paths)


def _unique_destination_path(destination: Path) -> Path:
    if not destination.exists():
        return destination

    stem = destination.stem
    suffix = destination.suffix
    for index in range(2, 10_000):
        candidate = destination.with_name(f"{stem} ({index}){suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(destination)


def _unique_export_destination_path(destination: Path) -> Path:
    if not destination.exists():
        return destination

    stem = destination.stem
    suffix = destination.suffix
    for index in range(2, 10_000):
        candidate = destination.with_name(f"{stem}_{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(destination)


def update_env_setting(key: str, value: str, env_file: Path = ENV_FILE) -> None:
    lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
    updated = False
    rendered: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            current_key, _current_value = stripped.split("=", 1)
            if current_key.strip() == key:
                rendered.append(f"{key}={value}")
                updated = True
                continue
        rendered.append(line)

    if not updated:
        if rendered and rendered[-1].strip():
            rendered.append("")
        rendered.append(f"{key}={value}")

    env_file.write_text("\n".join(rendered) + "\n", encoding="utf-8")
    os.environ[key] = value


def save_uploaded_pdf(filename: str, content: bytes, books_dir: Path, *, relative_path: str | None = None) -> Path:
    books_root = books_dir.expanduser().resolve()
    books_root.mkdir(parents=True, exist_ok=True)

    base_name = Path(relative_path or filename)
    if base_name.name.lower().endswith(".pdf") is False:
        raise ValueError("PDF ファイルのみ受け付けます")

    destination = (books_root / base_name).resolve()
    try:
        destination.relative_to(books_root)
    except ValueError as exc:
        raise ValueError("保存先が不正です") from exc

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination = _unique_destination_path(destination)
    destination.write_bytes(content)
    return destination


def _resolve_pdf_file_or_404(pdf_path: str, books_dir: Path) -> Path:
    books_root = books_dir.expanduser().resolve()
    relative = resolve_pdf_path(pdf_path, books_root)
    if relative is None:
        raise HTTPException(status_code=404, detail="PDF not found")
    return books_root / relative


def render_pdf_export(candidate: Path, pages: str) -> tuple[bytes, str]:
    page_spec = pages.strip()
    if not page_spec:
        raise HTTPException(status_code=400, detail="pages is required")

    from pypdf import PdfReader

    try:
        page_numbers = parse_page_selection(page_spec, len(PdfReader(str(candidate)).pages))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return render_selected_pages(candidate, page_numbers), default_output_path(candidate, page_numbers).name


def save_pdf_export_to_configured_dir(pdf_path: str, pages: str, *, books_dir: Path, save_dir: Path | None) -> Path:
    if save_dir is None:
        raise ValueError("保存先フォルダが未設定です。設定画面で指定してください。")

    save_root = save_dir.expanduser().resolve()
    if not save_root.exists():
        raise FileNotFoundError(save_root)
    if not save_root.is_dir():
        raise NotADirectoryError(save_root)

    candidate = _resolve_pdf_file_or_404(pdf_path, books_dir)
    content, filename = render_pdf_export(candidate, pages)
    destination = (save_root / Path(filename).name).resolve()
    try:
        destination.relative_to(save_root)
    except ValueError as exc:
        raise ValueError("保存先が不正です") from exc

    destination = _unique_export_destination_path(destination)
    destination.write_bytes(content)
    return destination


def _get_indexed_book(candidate: Path, *, books_dir: Path, db_path: Path):
    relative = resolve_pdf_path(candidate, books_dir)
    if relative is None:
        return None
    connection = None
    try:
        connection = connect(db_path)
        for path_candidate in [relative, books_dir.expanduser().resolve() / relative]:
            book = get_book(connection, path=path_candidate)
            if book:
                return book
    except sqlite3.OperationalError:
        pass
    finally:
        if connection is not None:
            connection.close()
    return None


def load_pages_text(candidate: Path, page_numbers: list[int], *, books_dir: Path, db_path: Path) -> dict[int, str]:
    texts: dict[int, str] = {}
    book = _get_indexed_book(candidate, books_dir=books_dir, db_path=db_path)
    if book is not None:
        connection = None
        try:
            connection = connect(db_path)
            placeholders = ",".join("?" for _ in page_numbers)
            rows = connection.execute(
                f"SELECT page_number, text FROM pages WHERE book_id = ? AND page_number IN ({placeholders})",
                [book.id, *page_numbers],
            ).fetchall()
            texts = {int(row["page_number"]): str(row["text"]) for row in rows}
        except sqlite3.OperationalError:
            texts = {}
        finally:
            if connection is not None:
                connection.close()

    missing = {number for number in page_numbers if number not in texts}
    if missing:
        from tsundokensaku.pdf_extract import extract_pages

        for page in extract_pages(candidate):
            if page.page_number in missing:
                texts[page.page_number] = page.text
    return texts


def _page_snippet(text: str, query: str, *, width: int = 80) -> str:
    flat = " ".join(text.split())
    index = flat.lower().find(query.lower())
    if index < 0:
        return flat[:width]
    start = max(0, index - 20)
    end = min(len(flat), index + len(query) + width - 20)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(flat) else ""
    return f"{prefix}{flat[start:end]}{suffix}"


def search_book_pages(candidate: Path, query: str, *, books_dir: Path, db_path: Path, limit: int = 100) -> dict[str, object]:
    book = _get_indexed_book(candidate, books_dir=books_dir, db_path=db_path)
    if book is None:
        return {"indexed": False, "pages": []}

    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    connection = connect(db_path)
    try:
        rows = connection.execute(
            "SELECT page_number, text FROM pages "
            "WHERE book_id = ? AND text LIKE ? ESCAPE '\\' "
            "ORDER BY page_number LIMIT ?",
            (book.id, f"%{escaped}%", limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return {"indexed": False, "pages": []}
    finally:
        connection.close()

    return {
        "indexed": True,
        "pages": [
            {
                "page_number": int(row["page_number"]),
                "snippet": _page_snippet(str(row["text"]), query),
            }
            for row in rows
        ],
    }


def render_markdown_export(candidate: Path, pages: str, *, books_dir: Path, db_path: Path) -> tuple[str, str]:
    page_spec = pages.strip()
    if not page_spec:
        raise HTTPException(status_code=400, detail="pages is required")

    from pypdf import PdfReader

    try:
        page_numbers = parse_page_selection(page_spec, len(PdfReader(str(candidate)).pages))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    book = _get_indexed_book(candidate, books_dir=books_dir, db_path=db_path)
    title = book.title if book is not None else candidate.stem
    texts = load_pages_text(candidate, page_numbers, books_dir=books_dir, db_path=db_path)
    content = render_markdown_pages(
        title=title,
        source_name=candidate.name,
        page_numbers=page_numbers,
        texts=texts,
        exported_at=_now_jst(),
    )
    return content, default_markdown_output_name(candidate, page_numbers)


def resolve_pdf_scrapbox_url(pdf_path: str, *, books_dir: Path, db_path: Path) -> str | None:
    relative = resolve_pdf_path(pdf_path, books_dir)
    if relative is None:
        return None

    connection = None
    try:
        connection = connect(db_path)
        path_candidates = [relative, books_dir.expanduser().resolve() / relative]
        for path_candidate in path_candidates:
            book = get_book(connection, path=path_candidate)
            if book and book.scrapbox_url:
                return book.scrapbox_url
    except sqlite3.OperationalError:
        pass
    finally:
        if connection is not None:
            connection.close()

    metadata = metadata_for_pdf(str(relative), get_metadata())
    return metadata.scrapbox_url if metadata else None


def import_scrapbox_export_bytes(content: bytes, db_path: Path) -> tuple[int, int]:
    target = SCRAPBOX_EXPORT_CACHE
    target.write_bytes(content)

    connection = connect(db_path)
    try:
        initialize(connection)
        imported = sync_memos(connection, target)
        imported_kindle = sync_kindle_books(connection, target)
    finally:
        connection.close()

    return imported, imported_kindle


SEARCH_SCOPE_OPTIONS = [
    {"value": "all", "label": "すべて"},
    {"value": "title", "label": "タイトルのみ"},
    {"value": "body", "label": "本文のみ"},
    {"value": "memo", "label": "メモのみ"},
]


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    books_dir = get_books_dir()
    db_path = get_db_path()
    pdf_stats = get_pdf_stats(books_dir)
    db_stats = get_db_stats(db_path)
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "request": request,
            "query": "",
            "books_dir": books_dir,
            "db_path": db_path,
            "pdf_count": pdf_stats["pdf_count"],
            "book_count": db_stats["book_count"],
            "pdf_db_count": db_stats["pdf_count"],
            "kindle_count": db_stats["kindle_count"],
            "page_count": db_stats["page_count"],
            "scope": "all",
            "scope_options": SEARCH_SCOPE_OPTIONS,
            "match": "all",
            "index_progress": _get_index_progress(),
        },
    )


@app.get("/search", response_class=HTMLResponse)
def search_page(
    request: Request,
    q: str = "",
    sort: str = "rank",
    scope: str = "all",
    group: list[str] = Query(default=[]),
    match: list[str] = Query(default=[]),
) -> HTMLResponse:
    books_dir = get_books_dir()
    db_path = get_db_path()
    normalized_scope = scope if scope in SEARCH_SCOPES else "all"
    normalized_match = normalize_search_match(match)
    normalized_group = normalize_search_group(group)
    rendered_results, normalized_scope = build_search_result_rows_context(
        q,
        sort=sort,
        scope=normalized_scope,
        group=normalized_group,
        match=normalized_match,
        books_dir=books_dir,
        db_path=db_path,
    )
    sort_options = [
        {"value": "rank", "label": "関連度順"},
        {"value": "title", "label": "書名順"},
        {"value": "page", "label": "ページ番号順"},
        {"value": "scrapbox", "label": "Scrapboxあり優先"},
    ]
    scrapbox_export_url = None
    if q.strip() and get_scrapbox_project_url():
        scrapbox_export_url = (
            f"/search/scrapbox?{urlencode({'q': q, 'sort': sort, 'scope': normalized_scope, 'group': normalized_group, 'match': normalized_match})}"
        )
    return templates.TemplateResponse(
        request,
        "search.html",
        {
            "request": request,
            "query": q,
            "sort": sort,
            "group": normalized_group,
            "sort_options": sort_options,
            "scope": normalized_scope,
            "scope_options": SEARCH_SCOPE_OPTIONS,
            "match": normalized_match,
            "query_terms": parse_query(q),
            "books_dir": books_dir,
            "db_path": db_path,
            "results": rendered_results,
            "result_count": len(rendered_results),
            "scrapbox_export_url": scrapbox_export_url,
            "index_progress": _get_index_progress(),
        },
    )


@app.get("/search/scrapbox")
def search_scrapbox_export(
    q: str = "",
    sort: str = "rank",
    scope: str = "all",
    group: str = "book",
    match: list[str] = Query(default=[]),
) -> RedirectResponse:
    books_dir = get_books_dir()
    db_path = get_db_path()
    normalized_match = normalize_search_match(match)
    rendered_results, normalized_scope = build_search_result_rows_context(
        q,
        sort=sort,
        scope=scope,
        group=group,
        match=normalized_match,
        books_dir=books_dir,
        db_path=db_path,
    )
    page_title, body = build_search_scrapbox_body(
        query=q,
        scope=normalized_scope,
        sort=sort,
        group=group,
        match=normalized_match,
        results=rendered_results,
    )
    url = build_scrapbox_page_url(page_title, body)
    if url is None:
        raise HTTPException(status_code=400, detail="SCRAPBOX_BASE_URL が設定されていません")
    return RedirectResponse(url=url, status_code=303)


@app.get("/workspace", response_class=HTMLResponse)
def workspace_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "workspace.html", {"request": request})


def _pack_connection():
    connection = connect(get_db_path())
    ensure_pack_schema(connection)
    return connection


def _pack_to_json(pack) -> dict:
    return {
        "id": pack.id,
        "name": pack.name,
        "note": pack.note,
        "book_count": pack.book_count,
        "created_at": pack.created_at,
        "updated_at": pack.updated_at,
    }


@app.get("/api/packs")
def api_list_packs() -> JSONResponse:
    connection = _pack_connection()
    try:
        active_pack_id = resolve_active_pack_id(connection)
        packs = [_pack_to_json(pack) for pack in list_packs(connection)]
    finally:
        connection.close()
    return JSONResponse({"packs": packs, "active_pack_id": active_pack_id})


@app.post("/api/packs")
def api_create_pack(payload: dict = Body(default={})) -> JSONResponse:
    name = payload.get("name") if isinstance(payload.get("name"), str) else ""
    connection = _pack_connection()
    try:
        pack_id = create_pack(connection, name=name)
        set_active_pack(connection, pack_id)
        pack = get_pack(connection, pack_id)
    finally:
        connection.close()
    return JSONResponse(_pack_to_json(pack), status_code=201)


@app.get("/api/packs/{pack_id}")
def api_get_pack(pack_id: int) -> JSONResponse:
    connection = _pack_connection()
    try:
        pack = get_pack(connection, pack_id)
        if pack is None:
            raise HTTPException(status_code=404, detail="資料が見つかりません")
        cart = pack_items_as_cart(connection, pack_id)
        items = pack_items_as_items(connection, pack_id)
    finally:
        connection.close()
    return JSONResponse({**_pack_to_json(pack), "cart": cart, **items})


@app.patch("/api/packs/{pack_id}")
def api_update_pack(pack_id: int, payload: dict = Body(default={})) -> JSONResponse:
    name = payload.get("name") if isinstance(payload.get("name"), str) else None
    note = payload.get("note") if isinstance(payload.get("note"), str) else None
    connection = _pack_connection()
    try:
        if not update_pack(connection, pack_id, name=name, note=note):
            raise HTTPException(status_code=404, detail="資料が見つかりません")
        pack = get_pack(connection, pack_id)
    finally:
        connection.close()
    return JSONResponse(_pack_to_json(pack))


@app.delete("/api/packs/{pack_id}")
def api_delete_pack(pack_id: int) -> JSONResponse:
    connection = _pack_connection()
    try:
        if not delete_pack(connection, pack_id):
            raise HTTPException(status_code=404, detail="資料が見つかりません")
        active_pack_id = resolve_active_pack_id(connection)
    finally:
        connection.close()
    return JSONResponse({"deleted": pack_id, "active_pack_id": active_pack_id})


@app.post("/api/packs/{pack_id}/activate")
def api_activate_pack(pack_id: int) -> JSONResponse:
    connection = _pack_connection()
    try:
        if not set_active_pack(connection, pack_id):
            raise HTTPException(status_code=404, detail="資料が見つかりません")
    finally:
        connection.close()
    return JSONResponse({"active_pack_id": pack_id})


@app.post("/api/packs/deactivate")
def api_deactivate_pack() -> JSONResponse:
    connection = _pack_connection()
    try:
        clear_active_pack(connection)
    finally:
        connection.close()
    return JSONResponse({"active_pack_id": None})


@app.put("/api/packs/{pack_id}/books")
def api_replace_pack_books(pack_id: int, payload: dict = Body(default={})) -> JSONResponse:
    books = payload.get("books")
    if not isinstance(books, dict):
        raise HTTPException(status_code=400, detail="books オブジェクトが必要です")
    connection = _pack_connection()
    try:
        if not replace_pack_items(connection, pack_id, books):
            raise HTTPException(status_code=404, detail="資料が見つかりません")
        cart = pack_items_as_cart(connection, pack_id)
        items_payload = pack_items_as_items(connection, pack_id)
    finally:
        connection.close()
    return JSONResponse({"pack_id": pack_id, "cart": cart, **items_payload})


@app.put("/api/packs/{pack_id}/items")
def api_replace_pack_items(pack_id: int, payload: dict = Body(default={})) -> JSONResponse:
    items = payload.get("items")
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="items 配列が必要です")
    connection = _pack_connection()
    try:
        try:
            saved_items = replace_pack_item_entries(connection, pack_id, items)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if saved_items is None:
            raise HTTPException(status_code=404, detail="資料が見つかりません")
        items_payload = pack_items_as_items(connection, pack_id)
        cart = pack_items_as_cart(connection, pack_id)
    finally:
        connection.close()
    return JSONResponse({"pack_id": pack_id, "cart": cart, **items_payload})


# エクスポート前の概算（トークンバジェット）。実行系エクスポートと異なり、
# 空資料・PDF欠損・不正なページ範囲・未インデックスのいずれも例外にせず、
# warnings として列挙して返す（設計書 14章「プレビューは寛容、実行は厳格」）。
# Phase 3A では profile パラメータを受け付けず、常に standard 相当の概算を返す。
# profile 追加時（Phase 3C）も、省略時はこの挙動を後方互換として維持する
# （設計書 12.1）。
def _export_preview_warning(code: str, *, item_id: int | None, message: str) -> dict[str, object]:
    return {"code": code, "item_id": item_id, "message": message}


def build_export_preview_warnings(item_stats: list[ItemStats]) -> list[dict[str, object]]:
    if not item_stats:
        return [_export_preview_warning("empty_pack", item_id=None, message="この資料には資料項目がありません")]

    warnings: list[dict[str, object]] = []
    for entry in item_stats:
        item = entry.item
        if entry.missing_pdf:
            warnings.append(
                _export_preview_warning(
                    "missing_pdf", item_id=item.id, message=f"「{item.title}」はPDFファイルが見つかりません"
                )
            )
            continue
        if not item.pages.strip():
            warnings.append(
                _export_preview_warning(
                    "missing_pages", item_id=item.id, message=f"「{item.title}」はページが指定されていません"
                )
            )
            continue
        if not entry.page_numbers:
            warnings.append(
                _export_preview_warning(
                    "invalid_pages", item_id=item.id, message=f"「{item.title}」のページ指定を解釈できませんでした"
                )
            )
            continue
        if entry.unindexed_pages > 0:
            warnings.append(
                _export_preview_warning(
                    "unindexed_pages",
                    item_id=item.id,
                    message=f"「{item.title}」は未インデックスのため{entry.unindexed_pages}ページ分を概算に含めていません",
                )
            )
    return warnings


def build_export_preview_payload(item_stats: list[ItemStats]) -> dict[str, object]:
    book_count = len({entry.item.pdf_path for entry in item_stats})
    total_pages = sum(len(entry.page_numbers) for entry in item_stats)
    total_stats = TextStats(
        cjk_chars=sum(entry.stats.cjk_chars for entry in item_stats),
        other_chars=sum(entry.stats.other_chars for entry in item_stats),
    )

    return {
        "estimation": "approximate",
        "estimator": ESTIMATOR_NAME,
        "book_count": book_count,
        "item_count": len(item_stats),
        "total_pages": total_pages,
        "estimated_chars": total_stats.cjk_chars + total_stats.other_chars,
        "estimated_tokens": estimate_tokens(total_stats),
        "warnings": build_export_preview_warnings(item_stats),
    }


@app.get("/api/packs/{pack_id}/export/preview")
def api_preview_pack_export(pack_id: int) -> JSONResponse:
    connection = _pack_connection()
    try:
        pack = get_pack(connection, pack_id)
        if pack is None:
            raise HTTPException(status_code=404, detail="資料が見つかりません")
        items = get_pack_items(connection, pack_id)
        item_stats = collect_item_stats(connection, items, books_dir=get_books_dir())
    finally:
        connection.close()

    return JSONResponse(build_export_preview_payload(item_stats))


def _export_pack_json(pack, items: list) -> Response:
    import json
    export_data = {
        "version": 3,
        "name": pack.name,
        "items": [
            {
                "pdf_path": item.pdf_path,
                "title": item.title,
                "pages": item.pages,
                "collapsed": item.collapsed,
                "addedAt": item.added_at,
                "position": item.position,
            }
            for item in items
        ]
    }
    json_bytes = json.dumps(export_data, ensure_ascii=False, indent=2).encode("utf-8")
    filename = f"{sanitize_filename_component(pack.name)}_{_now_jst():%Y%m%d}.json"
    return Response(
        content=json_bytes,
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


def _placeholder_item_stats_for_export(item) -> ItemStats:
    """StandardProfile.plan() へ渡す最小限のItemStats。

    standard は chunk_limit=None のため item_weight は使われず、plan() は
    1項目=1チャンクの構造（position順）を作るだけに使う。実際のページ数・
    本文検証・レンダリングは render_chunk 内で既存の render_pdf_export /
    render_markdown_export が行うため、ここでは重複計算しない
    （collect_item_stats の寛容なエラー処理をそのまま使うと、不正な
    ページ範囲の詳細なエラーメッセージが失われ後方互換性が壊れるため
    採用していない）。
    """
    return ItemStats(
        item=item,
        page_numbers=[],
        stats=TextStats(cjk_chars=0, other_chars=0),
        unindexed_pages=0,
        missing_pdf=False,
    )


def _export_pack_archive(pack, items: list, *, format: str, profile: ExportProfile) -> Response:
    if not items:
        raise HTTPException(status_code=400, detail="資料が空です")

    books_dir = get_books_dir()
    db_path = get_db_path()
    exported_at = _now_jst()

    plan = profile.plan([_placeholder_item_stats_for_export(item) for item in items])
    ctx = RenderContext(
        pack_name=pack.name,
        exported_at=exported_at,
        format=format,
        resolve_pdf=lambda pdf_path: _resolve_pdf_file_or_404(pdf_path, books_dir),
        render_pdf=render_pdf_export,
        render_markdown=lambda candidate, pages: render_markdown_export(
            candidate, pages, books_dir=books_dir, db_path=db_path
        ),
    )

    entries: list[PackExportEntry] = []
    for chunk in plan.chunks:
        item = chunk.items[0].item
        if not item.pages.strip():
            raise HTTPException(status_code=400, detail=f"{item.title}: ページを指定してください")
        entries.append(
            PackExportEntry(
                index=chunk.index,
                title=item.title,
                page_label=item.pages,
                filename=profile.chunk_filename(chunk, pack_name=pack.name, format=format),
                content=profile.render_chunk(chunk, ctx),
            )
        )

    zip_bytes = build_pack_zip(pack_name=pack.name, entries=entries, exported_at=exported_at)
    zip_filename = profile.archive_filename(pack_name=pack.name, exported_at=exported_at)
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(zip_filename)}"},
    )


def _resolve_export_profile_or_400(name: str | None) -> ExportProfile:
    try:
        return resolve_profile(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"不明なエクスポートプロファイルです: {exc}") from exc


@app.get("/api/packs/{pack_id}/export")
def api_export_pack(pack_id: int, profile: str | None = None, format: str = Query("pdf")) -> Response:
    resolved_profile = _resolve_export_profile_or_400(profile)

    if format not in ("pdf", "md", "json"):
        raise HTTPException(status_code=400, detail="format は pdf, md, または json を指定してください")

    # standard は primary_format=None（format を実行時に選べる）ため、この時点では
    # 常にスキップされる。chat/notebooklm 追加時に固定形式との矛盾を弾く構造だけ
    # 用意しておく（B-3では仮実装や分岐を追加しない）
    if resolved_profile.primary_format is not None and format != resolved_profile.primary_format:
        raise HTTPException(
            status_code=400,
            detail=f"profile={resolved_profile.name} では format={resolved_profile.primary_format} のみ指定できます",
        )

    connection = _pack_connection()
    try:
        pack = get_pack(connection, pack_id)
        if pack is None:
            raise HTTPException(status_code=404, detail="資料が見つかりません")
        items = get_pack_items(connection, pack_id)
    finally:
        connection.close()

    if format == "json":
        return _export_pack_json(pack, items)

    return _export_pack_archive(pack, items, format=format, profile=resolved_profile)


@app.post("/api/packs/import")
def api_import_pack(payload: dict = Body(default={})) -> JSONResponse:
    cart = payload.get("cart") if "cart" in payload else payload
    
    if not isinstance(cart, dict) or ("version" not in cart and "items" not in cart and "books" not in cart):
        raise HTTPException(status_code=400, detail="取り込めるカートデータがありません")

    if cart.get("version") == 3 and not isinstance(cart.get("items"), list):
        raise HTTPException(status_code=400, detail="items はリストでなければなりません")
    if cart.get("version") == 2 and not isinstance(cart.get("books"), dict):
        raise HTTPException(status_code=400, detail="books は辞書でなければなりません")

    name = payload.get("name") if isinstance(payload.get("name"), str) and payload.get("name") else "移行された資料"
    connection = _pack_connection()
    try:
        pack_id = import_cart_as_pack(connection, cart, name=name)
        if pack_id is None:
            raise HTTPException(status_code=400, detail="取り込めるカートデータがありません")
        set_active_pack(connection, pack_id)
        pack = get_pack(connection, pack_id)
        imported_cart = pack_items_as_cart(connection, pack_id)
        imported_items = pack_items_as_items(connection, pack_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        connection.close()
    return JSONResponse({**_pack_to_json(pack), "cart": imported_cart, **imported_items}, status_code=201)


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, message: str = "") -> HTMLResponse:
    books_dir = get_books_dir()
    db_path = get_db_path()
    db_stats = get_db_stats(db_path)
    library = get_library_items(books_dir, db_path)
    return templates.TemplateResponse(
        request,
        "settings_index.html",
        {
            "request": request,
            "books_dir": books_dir,
            "db_path": db_path,
            "pdf_count": library["pdf_count"],
            "book_count": db_stats["book_count"],
            "kindle_count": db_stats["kindle_count"],
            "page_count": db_stats["page_count"],
            "pdf_items": library["pdf_items"],
            "kindle_items": library["kindle_items"],
            "default_export_json": find_export_json(PROJECT_ROOT),
            "pdf_export_save_dir": get_pdf_export_save_dir(),
            "message": message,
            "index_progress": _get_index_progress(),
        },
    )


def get_host_books_dir() -> str | None:
    """Host-side path of the books dir, passed in by docker-compose as
    HOST_BOOKS_DIR. Unset outside Docker, where BOOKS_DIR itself is the
    host path."""
    configured = os.environ.get("HOST_BOOKS_DIR", "").strip()
    return configured or None


def get_host_db_path() -> str | None:
    configured = os.environ.get("HOST_DB_DIR", "").strip()
    if not configured:
        return None
    return f"{configured.rstrip('/')}/{get_db_path().name}"


@app.get("/settings/info", response_class=HTMLResponse)
def settings_info_page(request: Request, message: str = "") -> HTMLResponse:
    books_dir = get_books_dir()
    db_path = get_db_path()
    db_stats = get_db_stats(db_path)
    library = get_library_items(books_dir, db_path)
    return templates.TemplateResponse(
        request,
        "settings_info.html",
        {
            "request": request,
            "books_dir": books_dir,
            "db_path": db_path,
            "host_books_dir": get_host_books_dir(),
            "host_db_path": get_host_db_path(),
            "pdf_count": library["pdf_count"],
            "book_count": db_stats["book_count"],
            "kindle_count": db_stats["kindle_count"],
            "page_count": db_stats["page_count"],
            "message": message,
        },
    )


@app.get("/settings/scrapbox-import")
def import_scrapbox_json(export_json_path: str = "") -> RedirectResponse:
    if is_demo_mode():
        message = quote(DEMO_MODE_SETTING_MESSAGE)
        return RedirectResponse(url=f"/settings?message={message}", status_code=303)
    db_path = get_db_path()
    connection = connect(db_path)
    initialize(connection)
    source = Path(export_json_path).expanduser() if export_json_path.strip() else find_export_json(PROJECT_ROOT)
    if source is None or not source.exists():
        connection.close()
        message = quote("Scrapbox の export JSON が見つかりませんでした")
        return RedirectResponse(url=f"/settings?message={message}", status_code=303)

    source = source.resolve()
    target = SCRAPBOX_EXPORT_CACHE
    if source != target:
        target.write_bytes(source.read_bytes())
    imported = sync_memos(connection, target)
    imported_kindle = sync_kindle_books(connection, target)
    connection.close()
    message = quote(f"Scrapbox JSON を同期しました: メモ {imported} 件 / Kindle {imported_kindle} 件 ({source.name})")
    return RedirectResponse(url=f"/settings?message={message}", status_code=303)


@app.post("/settings/scrapbox-upload")
async def upload_scrapbox_json(request: Request, filename: str = "") -> PlainTextResponse:
    if is_demo_mode():
        return PlainTextResponse(DEMO_MODE_UPLOAD_MESSAGE, status_code=403)
    if not filename.strip():
        return PlainTextResponse("filename が必要です", status_code=400)
    if not filename.lower().endswith(".json"):
        return PlainTextResponse("JSON ファイルのみ受け付けます", status_code=400)

    content = await request.body()
    if not content:
        return PlainTextResponse("empty body", status_code=400)

    try:
        imported, imported_kindle = import_scrapbox_export_bytes(content, get_db_path())
    except Exception as exc:
        return PlainTextResponse(str(exc), status_code=400)

    return PlainTextResponse(f"Scrapbox JSON を同期しました: メモ {imported} 件 / Kindle {imported_kindle} 件 ({filename})", status_code=201)


@app.get("/settings/pdf-import")
def import_pdf_directory(source_dir: str = "") -> RedirectResponse:
    target = "/settings"
    if is_demo_mode():
        message = quote(DEMO_MODE_SETTING_MESSAGE)
        return RedirectResponse(url=f"{target}?message={message}", status_code=303)
    books_dir = get_books_dir()
    source = Path(source_dir).expanduser() if source_dir.strip() else None
    if source is None:
        message = quote("PDF の取り込み元フォルダを指定してください")
        return RedirectResponse(url=f"{target}?message={message}", status_code=303)

    try:
        copied, skipped, total = import_pdfs_from_directory(source, books_dir)
    except Exception as exc:
        message = quote(f"PDFインポートに失敗しました: {exc}")
        return RedirectResponse(url=f"{target}?message={message}", status_code=303)

    message = quote(
        f"PDF を {copied} 件 {books_dir} にインポートしました / スキップ {skipped} 件 ({total} 件中, {source})"
    )
    return RedirectResponse(url=f"{target}?message={message}", status_code=303)


@app.post("/settings/pdf-upload")
async def upload_pdf(request: Request, filename: str = "", relative_path: str = "") -> PlainTextResponse:
    if is_demo_mode():
        return PlainTextResponse(DEMO_MODE_UPLOAD_MESSAGE, status_code=403)
    books_dir = get_books_dir()
    if not filename.strip():
        return PlainTextResponse("filename が必要です", status_code=400)

    content = await request.body()
    if not content:
        return PlainTextResponse("empty body", status_code=400)
    if not content.startswith(b"%PDF"):
        return PlainTextResponse("PDF 以外は受け付けません", status_code=400)

    try:
        saved = save_uploaded_pdf(filename, content, books_dir, relative_path=relative_path or None)
    except Exception as exc:
        return PlainTextResponse(str(exc), status_code=400)

    return PlainTextResponse(str(saved), status_code=201)


@app.post("/settings/index")
def run_index(force: list[str] = Form(default=[])) -> RedirectResponse:
    progress = _get_index_progress()
    if bool(progress.get("running")):
        message = quote("インデックス実行中です")
        return RedirectResponse(url=f"/settings?message={message}", status_code=303)

    force_paths = set(force) if force else None
    _set_index_progress(True, 0, 0, "", "準備中")
    thread = threading.Thread(target=_run_index_job, args=(force_paths,), daemon=True)
    thread.start()
    message = quote(
        f"選択した {len(force_paths)} 件の強制再インデックスを開始しました" if force_paths else "インデックスを開始しました"
    )
    return RedirectResponse(url=f"/settings?message={message}", status_code=303)


@app.get("/settings/progress")
def settings_progress() -> JSONResponse:
    return JSONResponse(_get_index_progress())


@app.post("/settings/pdf-export-save-dir")
def update_pdf_export_save_dir(save_dir: str = Form(default="")) -> RedirectResponse:
    if is_demo_mode():
        message = quote(DEMO_MODE_SETTING_MESSAGE)
        return RedirectResponse(url=f"/settings?message={message}", status_code=303)
    normalized = save_dir.strip()
    update_env_setting(PDF_EXPORT_SAVE_DIR_ENV, normalized)
    message = quote("PDF切り出し保存先を保存しました" if normalized else "PDF切り出し保存先を未設定にしました")
    return RedirectResponse(url=f"/settings?message={message}", status_code=303)


@app.get("/pdf/{pdf_path:path}")
def open_pdf(pdf_path: str) -> FileResponse:
    candidate = _resolve_pdf_file_or_404(pdf_path, get_books_dir())
    return FileResponse(candidate, media_type="application/pdf")


@app.get("/pdf-outline")
def pdf_outline(pdf_path: str) -> JSONResponse:
    candidate = _resolve_pdf_file_or_404(pdf_path, get_books_dir())
    chapters = [
        {
            "title": chapter.title,
            "level": chapter.level,
            "start_page": chapter.start_page,
            "end_page": chapter.end_page,
            "pages": str(chapter.start_page)
            if chapter.start_page == chapter.end_page
            else f"{chapter.start_page}-{chapter.end_page}",
        }
        for chapter in list_chapters(candidate)
    ]
    return JSONResponse({"page_count": get_page_count(candidate), "chapters": chapters})


MAX_THUMBNAIL_PAGES_PER_REQUEST = 60
THUMBNAIL_SIZE_PRESETS = {
    "thumbnail": {"zoom": 0.3, "quality": 70},
    "detail": {"zoom": 1.0, "quality": 85},
}
# spec の展開上限。実際のページ数を知るには fitz.open() が必要（コストが
# 支配的なため二重に開きたくない）ので、蔵書の実ページ数を十分に超える
# 仮の上限を渡し、範囲外ページは render_thumbnails 側で無視させる
_THUMBNAIL_SPEC_PAGE_COUNT_GUARD = 10_000
_DETAIL_THUMBNAIL_PAGE_PATTERN = re.compile(r"^[1-9][0-9]*$")


@app.get("/pdf-thumbnails")
def pdf_thumbnails(pdf_path: str, pages: str, size: str = "thumbnail") -> JSONResponse:
    candidate = _resolve_pdf_file_or_404(pdf_path, get_books_dir())
    page_spec = pages.strip()
    if not page_spec:
        raise HTTPException(status_code=400, detail="pages is required")
    preset = THUMBNAIL_SIZE_PRESETS.get(size)
    if preset is None:
        raise HTTPException(status_code=400, detail="size must be thumbnail or detail")
    if size == "detail" and not _DETAIL_THUMBNAIL_PAGE_PATTERN.fullmatch(page_spec):
        raise HTTPException(status_code=400, detail="detail size requires a single page number")

    try:
        page_numbers = parse_page_selection(page_spec, _THUMBNAIL_SPEC_PAGE_COUNT_GUARD)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if len(page_numbers) > MAX_THUMBNAIL_PAGES_PER_REQUEST:
        raise HTTPException(
            status_code=400,
            detail=f"一度に取得できるページ数は{MAX_THUMBNAIL_PAGES_PER_REQUEST}件までです",
        )
    if size == "detail" and len(page_numbers) != 1:
        raise HTTPException(status_code=400, detail="detail size requires a single page number")
    if size == "detail":
        rendered_detail = render_thumbnail_detail(
            candidate,
            page_numbers[0],
            zoom=preset["zoom"],
            quality=preset["quality"],
        )
        if rendered_detail is None:
            raise HTTPException(status_code=404, detail="page not found")
        rendered = [rendered_detail]
    else:
        rendered = render_thumbnails(candidate, page_numbers, zoom=preset["zoom"], quality=preset["quality"])

    return JSONResponse(
        {
            "pages": [
                {"page": page_number, "data": base64.b64encode(data).decode("ascii")}
                for page_number, data in rendered
            ]
        }
    )


@app.get("/export-pdf")
def export_pdf(pdf_path: str, pages: str) -> Response:
    candidate = _resolve_pdf_file_or_404(pdf_path, get_books_dir())
    content, filename = render_pdf_export(candidate, pages)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
        },
    )


@app.get("/search-pages")
def search_pages(pdf_path: str, q: str = "") -> JSONResponse:
    query = q.strip()
    if not query:
        return JSONResponse({"indexed": True, "pages": []})
    candidate = _resolve_pdf_file_or_404(pdf_path, get_books_dir())
    return JSONResponse(
        search_book_pages(candidate, query, books_dir=get_books_dir(), db_path=get_db_path())
    )


@app.get("/export-md")
def export_markdown(pdf_path: str, pages: str) -> Response:
    candidate = _resolve_pdf_file_or_404(pdf_path, get_books_dir())
    content, filename = render_markdown_export(
        candidate,
        pages,
        books_dir=get_books_dir(),
        db_path=get_db_path(),
    )
    return Response(
        content=content,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
        },
    )


@app.post("/export-pdf/save")
def save_export_pdf(pdf_path: str, pages: str) -> JSONResponse:
    try:
        saved = save_pdf_export_to_configured_dir(
            pdf_path,
            pages,
            books_dir=get_books_dir(),
            save_dir=get_pdf_export_save_dir(),
        )
    except HTTPException:
        raise
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=f"保存先フォルダが存在しません: {exc.filename or exc}") from exc
    except NotADirectoryError as exc:
        raise HTTPException(status_code=400, detail=f"保存先がフォルダではありません: {exc.filename or exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return JSONResponse({"saved_path": str(saved)})


@app.get("/view/{pdf_path:path}", response_class=HTMLResponse)
def view_pdf(request: Request, pdf_path: str, page: int = 1) -> HTMLResponse:
    books_dir = get_books_dir()
    db_path = get_db_path()
    pdf_src = raw_pdf_url(pdf_path, books_dir, page_number=page)
    if pdf_src is None:
        raise HTTPException(status_code=404, detail="PDF not found")
    scrapbox_url = resolve_pdf_scrapbox_url(pdf_path, books_dir=books_dir, db_path=db_path)
    return templates.TemplateResponse(
        request,
        "pdf_viewer.html",
        {
            "request": request,
            "books_dir": books_dir,
            "db_path": db_path,
            "pdf_src": pdf_src,
            "pdf_path": pdf_path,
            "page": page,
            "scrapbox_url": scrapbox_url,
        },
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
