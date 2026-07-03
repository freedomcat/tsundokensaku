from __future__ import annotations

import os
import re
import sqlite3
import threading
import shutil
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import quote, urlencode
from urllib.parse import unquote, urlparse
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from tsundokensaku.database import SEARCH_SCOPES, connect, list_books, search, sync_kindle_books, sync_memos
from tsundokensaku.database import initialize
from tsundokensaku.indexer import find_pdfs, index_books
from tsundokensaku.metadata import (
    BookMetadata,
    find_export_json,
    load_metadata_by_pdf_stem,
    metadata_for_pdf,
    get_scrapbox_project_url,
)
from tsundokensaku.pdf_export import default_output_path, parse_page_selection, render_selected_pages
from tsundokensaku.tokenizer import tokenize_query


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BOOKS_DIR = Path("books/tech")
CONTAINER_BOOKS_DIR = Path("/books/tech")
DEFAULT_DB_PATH = Path("data/index.db")


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


def get_metadata() -> dict[str, BookMetadata]:
    return load_metadata_by_pdf_stem(find_export_json(PROJECT_ROOT))


def highlight_query(text: str, query: str) -> Markup:
    if not text:
        return Markup("")

    normalized_query = query.strip()
    compact_query = normalized_query.replace(" ", "")
    terms = [normalized_query, compact_query]
    terms.extend(term for term in tokenize_query(query) if term)
    terms = sorted({term for term in terms if term}, key=len, reverse=True)
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
                    "cover_url": (
                        metadata.cover_url
                        if (metadata := metadata_for_pdf(result.path or "", metadata_by_stem))
                        else None
                    ),
                    "open_url": raw_pdf_url(result.path or "", books_dir, page_number=result.page_number),
                    "scrapbox_url": (
                        metadata.scrapbox_url
                        if (metadata := metadata_for_pdf(result.path or "", metadata_by_stem))
                        else None
                    ),
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


def build_search_result_rows_context(
    query: str,
    *,
    sort: str,
    scope: str,
    group: str,
    books_dir: Path,
    db_path: Path,
) -> tuple[list[dict[str, object]], str]:
    export_json = find_export_json(PROJECT_ROOT)
    metadata_by_stem = load_metadata_by_pdf_stem(export_json)
    normalized_scope = scope if scope in SEARCH_SCOPES else "all"
    connection = connect(db_path)
    try:
        results = search(connection, query, limit=50, scope=normalized_scope) if query.strip() else []
    finally:
        connection.close()

    rendered_results = build_search_result_rows(results, books_dir=books_dir, metadata_by_stem=metadata_by_stem)
    rendered_results = finalize_search_result_rows(rendered_results, books_dir=books_dir, sort=sort, group=group)
    return rendered_results, normalized_scope


def build_search_scrapbox_body(
    *,
    query: str,
    scope: str,
    sort: str,
    group: str,
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
        path = str(result.get("path") or "")
        scrapbox_url = str(result.get("scrapbox_url") or "")
        page_summary = str(result.get("page_summary") or "")
        detail_parts = [part for part in [kind, page_summary, path] if part]
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


def resolve_pdf_path(pdf_path: str | Path, books_dir: Path) -> Path | None:
    candidate = Path(pdf_path)
    books_root = books_dir.resolve()

    candidates: list[Path] = []
    if candidate.is_absolute():
        candidates.append(candidate)
        try:
            candidates.append(books_root / candidate.relative_to(CONTAINER_BOOKS_DIR))
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
    pdf_paths = list(find_pdfs(books_dir))
    indexed_paths: dict[str, str] = {}
    kindle_books = []
    connection = None
    try:
        connection = connect(db_path)
        books = list_books(connection)
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
            "path": pdf_path,
            "title": pdf_path.stem,
            "indexed": str(pdf_path.resolve()) in indexed_paths or str(pdf_path) in indexed_paths,
            "indexed_at": indexed_paths.get(str(pdf_path.resolve())) or indexed_paths.get(str(pdf_path)),
            "cover_url": (
                metadata.cover_url
                if (metadata := metadata_for_pdf(pdf_path, metadata_by_stem))
                else None
            ),
            "open_url": raw_pdf_url(pdf_path, books_dir),
            "scrapbox_url": (
                metadata.scrapbox_url
                if (metadata := metadata_for_pdf(pdf_path, metadata_by_stem))
                else None
            ),
        }
        for pdf_path in pdf_paths
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
        "pdf_count": len(pdf_paths),
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
        },
    )


@app.get("/search", response_class=HTMLResponse)
def search_page(
    request: Request,
    q: str = "",
    sort: str = "rank",
    scope: str = "all",
    group: str = "none",
) -> HTMLResponse:
    books_dir = get_books_dir()
    db_path = get_db_path()
    normalized_scope = scope if scope in SEARCH_SCOPES else "all"
    rendered_results, normalized_scope = build_search_result_rows_context(
        q,
        sort=sort,
        scope=normalized_scope,
        group=group,
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
        scrapbox_export_url = f"/search/scrapbox?{urlencode({'q': q, 'sort': sort, 'scope': normalized_scope, 'group': group})}"
    return templates.TemplateResponse(
        request,
        "search.html",
        {
            "request": request,
            "query": q,
            "sort": sort,
            "group": group,
            "sort_options": sort_options,
            "scope": normalized_scope,
            "scope_options": SEARCH_SCOPE_OPTIONS,
            "books_dir": books_dir,
            "db_path": db_path,
            "results": rendered_results,
            "result_count": len(rendered_results),
            "scrapbox_export_url": scrapbox_export_url,
        },
    )


@app.get("/search/scrapbox")
def search_scrapbox_export(
    q: str = "",
    sort: str = "rank",
    scope: str = "all",
    group: str = "none",
) -> RedirectResponse:
    books_dir = get_books_dir()
    db_path = get_db_path()
    rendered_results, normalized_scope = build_search_result_rows_context(
        q,
        sort=sort,
        scope=scope,
        group=group,
        books_dir=books_dir,
        db_path=db_path,
    )
    page_title, body = build_search_scrapbox_body(
        query=q,
        scope=normalized_scope,
        sort=sort,
        group=group,
        results=rendered_results,
    )
    url = build_scrapbox_page_url(page_title, body)
    if url is None:
        raise HTTPException(status_code=400, detail="SCRAPBOX_BASE_URL が設定されていません")
    return RedirectResponse(url=url, status_code=303)


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
            "message": message,
            "index_progress": _get_index_progress(),
        },
    )


@app.get("/settings/share", response_class=HTMLResponse)
def settings_share_page(request: Request, message: str = "") -> HTMLResponse:
    books_dir = get_books_dir()
    db_path = get_db_path()
    db_stats = get_db_stats(db_path)
    library = get_library_items(books_dir, db_path)
    return templates.TemplateResponse(
        request,
        "settings_share.html",
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
            "message": message,
        },
    )


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
            "pdf_count": library["pdf_count"],
            "book_count": db_stats["book_count"],
            "kindle_count": db_stats["kindle_count"],
            "page_count": db_stats["page_count"],
            "message": message,
        },
    )


@app.get("/manage")
def manage_index(message: str = "") -> RedirectResponse:
    target = "/settings"
    if message:
        target = f"{target}?message={message}"
    return RedirectResponse(url=target, status_code=302)


@app.get("/settings/scrapbox-import")
def import_scrapbox_json(export_json_path: str = "") -> RedirectResponse:
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
    books_dir = get_books_dir()
    target = "/settings"
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


@app.post("/manage/index")
def manage_run_index(force: list[str] = Form(default=[])) -> RedirectResponse:
    return run_index(force=force)


@app.get("/settings/progress")
def settings_progress() -> JSONResponse:
    return JSONResponse(_get_index_progress())


@app.get("/pdf/{pdf_path:path}")
def open_pdf(pdf_path: str) -> FileResponse:
    books_dir = get_books_dir().resolve()
    candidate = (books_dir / pdf_path).resolve()
    try:
        candidate.relative_to(books_dir)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="PDF not found") from exc
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="PDF not found")
    return FileResponse(candidate, media_type="application/pdf")


@app.get("/export-pdf")
def export_pdf(pdf_path: str, pages: str) -> Response:
    books_dir = get_books_dir().resolve()
    candidate = (books_dir / pdf_path).resolve()
    try:
        candidate.relative_to(books_dir)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="PDF not found") from exc
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="PDF not found")

    page_spec = pages.strip()
    if not page_spec:
        raise HTTPException(status_code=400, detail="pages is required")

    from pypdf import PdfReader

    try:
        page_numbers = parse_page_selection(page_spec, len(PdfReader(str(candidate)).pages))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    content = render_selected_pages(candidate, page_numbers)
    filename = default_output_path(candidate, page_numbers).name
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
        },
    )


@app.get("/view/{pdf_path:path}", response_class=HTMLResponse)
def view_pdf(request: Request, pdf_path: str, page: int = 1) -> HTMLResponse:
    books_dir = get_books_dir()
    pdf_src = raw_pdf_url(pdf_path, books_dir, page_number=page)
    if pdf_src is None:
        raise HTTPException(status_code=404, detail="PDF not found")
    return templates.TemplateResponse(
        request,
        "pdf_viewer.html",
        {
            "request": request,
            "books_dir": books_dir,
            "db_path": get_db_path(),
            "pdf_src": pdf_src,
            "pdf_path": pdf_path,
            "page": page,
        },
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
