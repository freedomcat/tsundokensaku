from __future__ import annotations

import os
import re
import sqlite3
import threading
from urllib.parse import quote
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from tsundokensaku.actions import build_tomorrow_actions
from tsundokensaku.database import SEARCH_SCOPES, connect, list_books, search, sync_memos
from tsundokensaku.database import initialize
from tsundokensaku.indexer import find_pdfs, index_books
from tsundokensaku.metadata import (
    BookMetadata,
    find_export_json,
    load_metadata_by_pdf_stem,
    metadata_for_pdf,
)
from tsundokensaku.tokenizer import tokenize_query


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BOOKS_DIR = Path("books/tech")
CONTAINER_BOOKS_DIR = Path("/books/tech")
DEFAULT_DB_PATH = Path("data/index.db")
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


def _run_index_job() -> None:
    books_dir = get_books_dir()
    db_path = get_db_path()
    try:
        index_books(books_dir=books_dir, db_path=db_path, progress_callback=_set_index_progress)
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
    indexed_paths: set[str] = set()
    kindle_books = []
    connection = None
    try:
        connection = connect(db_path)
        books = list_books(connection)
        indexed_paths = {row["path"] for row in connection.execute("SELECT path FROM books").fetchall()}
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
    export_json = find_export_json(PROJECT_ROOT)
    metadata_by_stem = load_metadata_by_pdf_stem(export_json)
    normalized_scope = scope if scope in SEARCH_SCOPES else "all"
    connection = connect(db_path)
    results = search(connection, q, limit=50, scope=normalized_scope) if q.strip() else []
    connection.close()
    rendered_results = []
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
                    "scrapbox_url": result.open_url,
                }
            )
    rendered_results = sort_results(rendered_results, sort)
    if group == "book":
        rendered_results = group_pdf_results(rendered_results)
    for result in rendered_results:
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
    actions = build_tomorrow_actions(rendered_results, q, limit=3) if q.strip() else []
    sort_options = [
        {"value": "rank", "label": "関連度順"},
        {"value": "title", "label": "書名順"},
        {"value": "page", "label": "ページ番号順"},
        {"value": "scrapbox", "label": "Scrapboxあり優先"},
    ]
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
            "actions": actions,
        },
    )


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, message: str = "") -> HTMLResponse:
    books_dir = get_books_dir()
    db_path = get_db_path()
    db_stats = get_db_stats(db_path)
    library = get_library_items(books_dir, db_path)
    return templates.TemplateResponse(
        request,
        "settings.html",
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
    connection.close()
    message = quote(f"Scrapbox JSON を同期しました: {imported} 件 ({source.name})")
    return RedirectResponse(url=f"/settings?message={message}", status_code=303)


@app.post("/settings/index")
def run_index() -> RedirectResponse:
    progress = _get_index_progress()
    if bool(progress.get("running")):
        message = quote("インデックス実行中です")
        return RedirectResponse(url=f"/settings?message={message}", status_code=303)

    _set_index_progress(True, 0, 0, "", "準備中")
    thread = threading.Thread(target=_run_index_job, daemon=True)
    thread.start()
    message = quote("インデックスを開始しました")
    return RedirectResponse(url=f"/settings?message={message}", status_code=303)


@app.post("/manage/index")
def manage_run_index() -> RedirectResponse:
    return run_index()


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
