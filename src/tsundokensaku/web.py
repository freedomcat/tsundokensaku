from __future__ import annotations

import logging
import os
import base64
import json
import re
import sqlite3
import time
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import quote, urlencode
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Body, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from tsundokensaku.database import (
    SEARCH_MATCH_MODES,
    SEARCH_SCOPES,
    clear_active_pack,
    connect,
    create_pack,
    delete_pack,
    ensure_pack_schema,
    get_pack,
    get_pack_items,
    import_cart_as_pack,
    list_books,
    list_export_events,
    list_packs,
    pack_items_as_cart,
    pack_items_as_items,
    parse_query,
    record_export_event,
    replace_pack_item_entries,
    replace_pack_items,
    resolve_active_pack_id,
    search,
    set_active_pack,
    update_pack,
)
from tsundokensaku.database import initialize
from tsundokensaku.export_profiles import PROFILES, ExportProfile, RenderContext, resolve_profile
from tsundokensaku.export_stats import ItemStats, collect_item_stats
from tsundokensaku.indexer import find_pdfs
from tsundokensaku.metadata import (
    BookMetadata,
    ENV_FILE,
    find_export_json,
    load_metadata_by_pdf_stem,
    metadata_for_pdf,
    get_scrapbox_project_url,
)
from tsundokensaku import config
from tsundokensaku import index_job
from tsundokensaku import paths
from tsundokensaku import pdf_export as pdf_export_service
from tsundokensaku import pdf_import_service
from tsundokensaku import pdf_metadata_service
from tsundokensaku import pdf_text_service
from tsundokensaku import scrapbox_import_service
from tsundokensaku import search_view
from tsundokensaku.pdf_export import PdfSourceNotFoundError, parse_page_selection
from tsundokensaku.pdf_outline import get_page_count, list_chapters
from tsundokensaku.pdf_thumbnail import render_thumbnail_detail, render_thumbnails
from tsundokensaku.token_estimate import ESTIMATOR_NAME, TextStats, estimate_tokens
from tsundokensaku.zip_export import (
    PackExportEntry,
    PlanManifestChunk,
    PlanManifestFragment,
    build_entry_filename,
    build_pack_zip,
    build_pack_zip_filename,
    build_pack_zip_with_manifest,
    render_plan_manifest,
    sanitize_filename_component,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BOOKS_DIR = config.DEFAULT_BOOKS_DIR
CONTAINER_BOOKS_DIRS = paths.CONTAINER_BOOKS_DIRS
DEFAULT_DB_PATH = config.DEFAULT_DB_PATH
PDF_EXPORT_SAVE_DIR_ENV = config.PDF_EXPORT_SAVE_DIR_ENV
EXTERNALLY_AVAILABLE_EXPORT_PROFILES = frozenset({"standard", "chat", "chapter"})


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

app = FastAPI(title="tsundokensaku")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["highlight_query"] = lambda text, query="": highlight_query(text, query)
templates.env.filters["format_indexed_at"] = lambda value: format_indexed_at(value)


def get_books_dir() -> Path:
    return config.get_books_dir()


def get_db_path() -> Path:
    return config.get_db_path()


def get_pdf_export_save_dir() -> Path | None:
    return config.get_pdf_export_save_dir()


templates.env.globals["pdf_export_save_dir"] = get_pdf_export_save_dir
templates.env.globals["is_pdf_export_save_dir_configured"] = lambda: get_pdf_export_save_dir() is not None


DEMO_MODE_UPLOAD_MESSAGE = "Upload is disabled in demo mode."
DEMO_MODE_SETTING_MESSAGE = "デモモードのため無効です"


def is_demo_mode() -> bool:
    return config.is_demo_mode()


templates.env.globals["is_demo_mode"] = is_demo_mode


def get_metadata() -> dict[str, BookMetadata]:
    return load_metadata_by_pdf_stem(find_export_json(PROJECT_ROOT))


def highlight_query(text: str, query: str) -> Markup:
    return search_view.highlight_query(text, query)


def format_indexed_at(value: str | None) -> str:
    return search_view.format_indexed_at(value)


def _now_jst() -> datetime:
    return datetime.now(ZoneInfo("Asia/Tokyo"))


def _sanitize_scrapbox_title(value: str, *, max_length: int = 80) -> str:
    return search_view._sanitize_scrapbox_title(value, max_length=max_length)


def build_scrapbox_page_url(title: str, body: str) -> str | None:
    return search_view.build_scrapbox_page_url(title, body)


def _scrapbox_page_label(scrapbox_url: str | None, fallback: str) -> str:
    return search_view._scrapbox_page_label(scrapbox_url, fallback)


def build_search_result_rows(
    results,
    *,
    books_dir: Path,
    metadata_by_stem: dict[str, BookMetadata],
) -> list[dict[str, object]]:
    return search_view.build_search_result_rows(results, books_dir=books_dir, metadata_by_stem=metadata_by_stem)


def finalize_search_result_rows(rendered_results: list[dict[str, object]], *, books_dir: Path, sort: str, group: str) -> list[dict[str, object]]:
    return search_view.finalize_search_result_rows(rendered_results, books_dir=books_dir, sort=sort, group=group)


def normalize_search_group(values: list[str] | str | None) -> str:
    return search_view.normalize_search_group(values)


def normalize_search_match(values: list[str] | str | None) -> str:
    return search_view.normalize_search_match(values)


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
    return search_view.build_search_scrapbox_body(
        query=query, scope=scope, sort=sort, group=group, match=match, results=results
    )


def resolve_pdf_path(pdf_path: str | Path, books_dir: Path) -> Path | None:
    return paths.resolve_pdf_path(pdf_path, books_dir)


def pdf_url(pdf_path: str | Path, books_dir: Path, *, page_number: int | None = None) -> str | None:
    return paths.pdf_url(pdf_path, books_dir, page_number=page_number)


def raw_pdf_url(pdf_path: str | Path, books_dir: Path, *, page_number: int | None = None) -> str | None:
    return paths.raw_pdf_url(pdf_path, books_dir, page_number=page_number)


def get_pdf_stats(books_dir: Path) -> dict[str, int]:
    pdf_paths = list(find_pdfs(books_dir))
    return {"pdf_count": len(pdf_paths)}


def sort_results(results: list[dict], sort: str) -> list[dict]:
    return search_view.sort_results(results, sort)


def group_pdf_results(results: list[dict]) -> list[dict]:
    return search_view.group_pdf_results(results)


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


def _unique_destination_path(destination: Path) -> Path:
    return paths.unique_destination_path(destination)


def _unique_export_destination_path(destination: Path) -> Path:
    return paths.unique_export_destination_path(destination)


def update_env_setting(key: str, value: str, env_file: Path = ENV_FILE) -> None:
    return config.update_env_setting(key, value, env_file)


def _resolve_pdf_file_or_404(pdf_path: str, books_dir: Path) -> Path:
    books_root = books_dir.expanduser().resolve()
    relative = resolve_pdf_path(pdf_path, books_root)
    if relative is None:
        raise HTTPException(status_code=404, detail="PDF not found")
    return books_root / relative


def render_pdf_export(candidate: Path, pages: str) -> tuple[bytes, str]:
    try:
        return pdf_export_service.render_pdf_export(candidate, pages)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def search_book_pages(candidate: Path, query: str, *, books_dir: Path, db_path: Path, limit: int = 100) -> dict[str, object]:
    result = pdf_text_service.search_book_pages(candidate, query, books_dir=books_dir, db_path=db_path, limit=limit)
    return {
        "indexed": result.indexed,
        "pages": [
            {
                "page_number": hit.page_number,
                "snippet": hit.snippet,
            }
            for hit in result.pages
        ],
    }


def render_markdown_export(candidate: Path, pages: str, *, books_dir: Path, db_path: Path) -> tuple[str, str]:
    try:
        return pdf_text_service.render_markdown_export(
            candidate,
            pages,
            books_dir=books_dir,
            db_path=db_path,
            exported_at=_now_jst(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def resolve_pdf_scrapbox_url(pdf_path: str, *, books_dir: Path, db_path: Path) -> str | None:
    return pdf_metadata_service.resolve_pdf_scrapbox_url(
        pdf_path,
        books_dir=books_dir,
        db_path=db_path,
        project_root=PROJECT_ROOT,
    )


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
            "index_progress": index_job.get_progress(),
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
            "index_progress": index_job.get_progress(),
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


@app.get("/packs", response_class=HTMLResponse)
def pack_list_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "pack_list.html", {"request": request})


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


def _export_event_to_json(event) -> dict:
    return {
        "id": event.id,
        "exported_at": event.exported_at,
        "pack_id": event.pack_id,
        "pack_name": event.pack_name,
        "profile": event.profile,
        "format": event.format,
        "items": [
            {
                "pdf_path": item.pdf_path,
                "title": item.title,
                "pages": item.pages,
                "position": item.position,
            }
            for item in event.items
        ],
    }


@app.get("/api/export-events")
def api_list_export_events(limit: int = Query(default=20)) -> JSONResponse:
    connection = _pack_connection()
    try:
        events = [_export_event_to_json(event) for event in list_export_events(connection, limit=limit)]
    finally:
        connection.close()
    return JSONResponse({"export_events": events})


@app.get("/api/packs")
def api_list_packs() -> JSONResponse:
    connection = _pack_connection()
    try:
        active_pack_id = resolve_active_pack_id(connection)
        packs = [_pack_to_json(pack) for pack in list_packs(connection)]
    finally:
        connection.close()
    return JSONResponse({"packs": packs, "active_pack_id": active_pack_id})


@app.get("/api/packs/stats")
def api_list_pack_stats() -> JSONResponse:
    """資料一覧画面専用。冊数に加え項目数・ページ数・推定トークン数を返す。

    GET /api/packs は資料棚の資料切替がデバウンス保存完了のたびに呼ぶ
    ホットパスのため、そちらのレスポンス形状は変えず本エンドポイントを
    別に用意する（docs/workspace-ui-information-architecture.md Phase 2C）。
    インデックス済みの本は collect_item_stats が PDF ファイルを開かず
    SQLite のみで集計するため（export_stats.py の設計）、資料横断の集計
    でも軽量に収まる想定。

    ルーティング上、/api/packs/{pack_id} より先に定義する必要がある
    （先に定義しないと "stats" が pack_id として解釈され 422 になる）。
    """
    books_dir = get_books_dir()
    connection = _pack_connection()
    try:
        active_pack_id = resolve_active_pack_id(connection)
        packs = []
        for pack in list_packs(connection):
            items = get_pack_items(connection, pack.id)
            item_stats = collect_item_stats(connection, items, books_dir=books_dir)
            stats = _preview_base_stats(item_stats)
            packs.append(
                {
                    "id": pack.id,
                    "name": pack.name,
                    "note": pack.note,
                    "created_at": pack.created_at,
                    "updated_at": pack.updated_at,
                    "book_count": stats["book_count"],
                    "item_count": stats["item_count"],
                    "total_pages": stats["total_pages"],
                    "estimated_tokens": stats["estimated_tokens"],
                }
            )
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


def _preview_base_stats(item_stats: list[ItemStats]) -> dict[str, object]:
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
    }


def build_export_preview_payload(item_stats: list[ItemStats]) -> dict[str, object]:
    # Phase 3A からの既存レスポンス形式（profile未指定・profile=standard用）。
    # フィールド集合・値とも Phase 3C 導入前から不変（設計書12.1の後方互換方針）
    return {
        **_preview_base_stats(item_stats),
        "warnings": build_export_preview_warnings(item_stats),
    }


def build_export_preview_payload_for_profile(
    item_stats: list[ItemStats],
    profile: ExportProfile,
    *,
    pack_name: str,
    chapter_loader=None,
) -> dict[str, object]:
    """standard以外（chat等）向けの拡張プレビュー。設計書12.1のchunks付きレスポンス。

    実エクスポート（_export_pack_archive）と同じ plan() / chunk_filename() を
    呼ぶことで、分冊結果・ファイル名・警告をエクスポート実行前に一致させる。
    """
    item_warnings = build_export_preview_warnings(item_stats)

    if not item_stats:
        return {
            "profile": profile.name,
            **_preview_base_stats(item_stats),
            "file_count": 0,
            "archive": "zip",
            "chunks": [],
            "warnings": item_warnings,
        }

    plan = profile.plan(item_stats, chapter_loader=chapter_loader)
    # primary_format を持たないプロファイル（standardのみ）はこの関数の対象外のため
    # 実際には使われないが、chunk_filename の型契約上フォーマット文字列が必要
    format_for_naming = profile.primary_format or "pdf"

    chunks_payload = [
        {
            "filename": profile.chunk_filename(chunk, pack_name=pack_name, format=format_for_naming),
            "estimated_tokens": chunk.estimated_tokens,
            "pages": chunk.total_pages,
            "items": [
                {
                    "item_id": fragment.item.id,
                    "title": fragment.item.title,
                    "pdf_path": fragment.item.pdf_path,
                    "pages": fragment.page_spec,
                    "label": fragment.label,
                    "fragment_index": fragment.fragment_index,
                    "fragment_count": fragment.fragment_count,
                    "estimated_tokens": estimate_tokens(fragment.stats),
                }
                for fragment in chunk.fragments
            ],
        }
        for chunk in plan.chunks
    ]
    plan_warnings = [
        {"code": warning.code, "item_id": warning.item_id, "message": warning.message}
        for warning in plan.warnings
    ]

    return {
        "profile": profile.name,
        **_preview_base_stats(item_stats),
        "file_count": len(plan.chunks),
        "archive": "zip",
        "chunks": chunks_payload,
        "warnings": item_warnings + plan_warnings,
    }


@app.get("/api/packs/{pack_id}/export/preview")
def api_preview_pack_export(pack_id: int, profile: str | None = None) -> JSONResponse:
    resolved_profile = _resolve_export_profile_or_400(profile)

    connection = _pack_connection()
    try:
        pack = get_pack(connection, pack_id)
        if pack is None:
            raise HTTPException(status_code=404, detail="資料が見つかりません")
        items = get_pack_items(connection, pack_id)
        item_stats = collect_item_stats(connection, items, books_dir=get_books_dir())
    finally:
        connection.close()

    if not resolved_profile.uses_plan_output:
        return JSONResponse(build_export_preview_payload(item_stats))

    chapter_loader = None
    if resolved_profile.needs_chapter_loader:
        chapter_loader = lambda pdf_path: list_chapters(_resolve_pdf_file_or_404(str(pdf_path), get_books_dir()))

    return JSONResponse(
        build_export_preview_payload_for_profile(
            item_stats,
            resolved_profile,
            pack_name=pack.name,
            chapter_loader=chapter_loader,
        )
    )


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

    # 全項目の事前検証
    for item in items:
        if not item.pages.strip():
            raise HTTPException(status_code=400, detail=f"{item.title}: ページを指定してください")

    books_dir = get_books_dir()
    db_path = get_db_path()
    exported_at = _now_jst()

    # 実統計を必要とするプロファイル（chunk_limit が None 以外）なら collect_item_stats、そうでなければプレースホルダ
    if profile.chunk_limit() is not None:
        connection = _pack_connection()
        try:
            item_stats = collect_item_stats(connection, items, books_dir=books_dir)
        finally:
            connection.close()
    else:
        item_stats = [_placeholder_item_stats_for_export(item) for item in items]

    chapter_loader = None
    if profile.needs_chapter_loader:
        chapter_loader = lambda pdf_path: list_chapters(_resolve_pdf_file_or_404(str(pdf_path), books_dir))

    plan = profile.plan(item_stats, chapter_loader=chapter_loader)
    ctx = RenderContext(
        pack_name=pack.name,
        exported_at=exported_at,
        format=format,
        resolve_pdf=lambda pdf_path: _resolve_pdf_file_or_404(pdf_path, books_dir),
        render_pdf=render_pdf_export,
        render_markdown=lambda candidate, pages: render_markdown_export(
            candidate, pages, books_dir=books_dir, db_path=db_path
        ),
        total_chunks=len(plan.chunks),
    )

    entries: list[PackExportEntry] = []
    manifest_chunks: list[tuple[str, list[tuple[str, str]]] | PlanManifestChunk] = []
    for chunk in plan.chunks:
        filename = profile.chunk_filename(chunk, pack_name=pack.name, format=format)
        primary_fragment = chunk.fragments[0]
        entries.append(
            PackExportEntry(
                index=chunk.index,
                title=primary_fragment.item.title,
                page_label=primary_fragment.page_spec,
                filename=filename,
                content=profile.render_chunk(chunk, ctx),
            )
        )
        if profile.manifest_uses_fragment_labels:
            manifest_chunks.append(
                PlanManifestChunk(
                    filename=filename,
                    fragments=[
                        PlanManifestFragment(
                            title=fragment.item.title,
                            pages=fragment.page_spec,
                            label=fragment.label,
                        )
                        for fragment in chunk.fragments
                    ],
                )
            )
        else:
            manifest_chunks.append((filename, [(fragment.item.title, fragment.page_spec) for fragment in chunk.fragments]))

    if not profile.uses_plan_output:
        # 現行 manifest（PackExportEntry 前提、1項目=1エントリ）をそのまま使い
        # バイト互換を守る（設計書 10.3）
        zip_bytes = build_pack_zip(pack_name=pack.name, entries=entries, exported_at=exported_at)
    else:
        # 複数項目チャンク（chat の分冊・chapter の結合）でも項目内訳が
        # 失われないよう、ExportPlan から組み立てた manifest を使う。
        # plan の警告（item_exceeds_limit 等）もここに記載する（設計書 14）
        manifest = render_plan_manifest(
            pack_name=pack.name,
            exported_at=exported_at,
            profile_name=profile.name,
            chunks=manifest_chunks,
            header_lines=profile.manifest_header_lines(plan),
            warnings=[warning.message for warning in plan.warnings],
        )
        zip_bytes = build_pack_zip_with_manifest(entries=entries, manifest=manifest)

    zip_filename = profile.archive_filename(pack_name=pack.name, exported_at=exported_at)
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(zip_filename)}"},
    )


def _resolve_export_profile_or_400(name: str | None) -> ExportProfile:
    try:
        profile = resolve_profile(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"不明なエクスポートプロファイルです: {exc}") from exc
    if profile.name not in EXTERNALLY_AVAILABLE_EXPORT_PROFILES:
        raise HTTPException(status_code=400, detail=f"不明なエクスポートプロファイルです: {profile.name}")
    return profile


@app.get("/api/packs/{pack_id}/export")
def api_export_pack(pack_id: int, profile: str | None = None, format: str | None = None) -> Response:
    resolved_profile = _resolve_export_profile_or_400(profile)

    if format is None:
        # 省略時は profile の主形式（standard は None なので現行既定の pdf）
        format = resolved_profile.primary_format if resolved_profile.primary_format is not None else "pdf"

    if format not in ("pdf", "md", "json"):
        raise HTTPException(status_code=400, detail="format は pdf, md, または json を指定してください")

    # standard は primary_format=None（format を実行時に選べる）ため、この時点では
    # 常にスキップされる。chat/chapter 追加時に固定形式との矛盾を弾く構造だけ
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
        response = _export_pack_json(pack, items)
    else:
        response = _export_pack_archive(pack, items, format=format, profile=resolved_profile)

    # エクスポート成功後にイベントを記録する（ベストエフォート。設計書 C-6 / export-events-design.md §6）
    try:
        connection = _pack_connection()
        try:
            record_export_event(
                connection,
                pack_id=pack_id,
                pack_name=pack.name,
                profile=resolved_profile.name,
                format=format,
                items=list(items),
            )
        finally:
            connection.close()
    except Exception:
        logging.exception("export_events の記録に失敗しました（エクスポート本体は正常）")

    return response


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
            "index_progress": index_job.get_progress(),
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
    source = Path(export_json_path).expanduser() if export_json_path.strip() else find_export_json(PROJECT_ROOT)
    try:
        result = scrapbox_import_service.import_scrapbox_export_file(
            source,
            cache_path=SCRAPBOX_EXPORT_CACHE,
            db_path=db_path,
        )
    except scrapbox_import_service.ScrapboxExportNotFoundError:
        message = quote("Scrapbox の export JSON が見つかりませんでした")
        return RedirectResponse(url=f"/settings?message={message}", status_code=303)

    source_name = source.expanduser().resolve().name if source is not None else ""
    message = quote(
        f"Scrapbox JSON を同期しました: メモ {result.imported_memos} 件 / "
        f"Kindle {result.imported_kindle_books} 件 ({source_name})"
    )
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
        result = scrapbox_import_service.import_scrapbox_export_bytes(
            content,
            cache_path=SCRAPBOX_EXPORT_CACHE,
            db_path=get_db_path(),
        )
    except Exception as exc:
        return PlainTextResponse(str(exc), status_code=400)

    return PlainTextResponse(
        f"Scrapbox JSON を同期しました: メモ {result.imported_memos} 件 / "
        f"Kindle {result.imported_kindle_books} 件 ({filename})",
        status_code=201,
    )


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
        result = pdf_import_service.import_pdfs_from_directory(source, books_dir)
    except pdf_import_service.PdfDirectoryImportError as exc:
        LOGGER.exception("PDFディレクトリ取り込みに失敗しました")
        message = quote(_pdf_directory_import_failure_message(exc))
        return RedirectResponse(url=f"{target}?message={message}", status_code=303)
    except Exception:
        LOGGER.exception("PDFディレクトリ取り込みで予期しないエラーが発生しました")
        message = quote("PDFインポートに失敗しました: 予期しないエラーが発生しました")
        return RedirectResponse(url=f"{target}?message={message}", status_code=303)

    message = quote(
        f"PDF を {result.copied} 件 {books_dir} にインポートしました / "
        f"スキップ {result.skipped} 件 ({result.total} 件中, {source})"
    )
    return RedirectResponse(url=f"{target}?message={message}", status_code=303)


def _pdf_directory_import_failure_message(exc: pdf_import_service.PdfDirectoryImportError) -> str:
    if isinstance(exc, pdf_import_service.PdfDirectorySourceNotFoundError):
        return "PDFインポートに失敗しました: 入力元フォルダが見つかりません"
    if isinstance(exc, pdf_import_service.PdfDirectorySourceNotDirectoryError):
        return "PDFインポートに失敗しました: 入力元がフォルダではありません"
    if isinstance(exc, pdf_import_service.PdfDirectoryOverlapError):
        return "PDFインポートに失敗しました: 入力元と保存先には重ならないフォルダを指定してください"
    if isinstance(exc, pdf_import_service.PdfDirectoryBoundaryError):
        return "PDFインポートに失敗しました: 安全でないパスが含まれているため取り込めません"
    if isinstance(exc, pdf_import_service.PdfDirectoryFilesystemError):
        return "PDFインポートに失敗しました: ファイルの読み取りまたはコピーに失敗しました"
    return "PDFインポートに失敗しました: 予期しないエラーが発生しました"


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
        saved = pdf_import_service.save_uploaded_pdf(
            filename,
            content,
            books_dir,
            relative_path=relative_path or None,
        )
    except Exception as exc:
        return PlainTextResponse(str(exc), status_code=400)

    return PlainTextResponse(str(saved), status_code=201)


@app.post("/settings/index")
def run_index(force: list[str] = Form(default=[])) -> RedirectResponse:
    if index_job.is_running():
        message = quote("インデックス実行中です")
        return RedirectResponse(url=f"/settings?message={message}", status_code=303)

    force_paths = set(force) if force else None
    index_job.start(force_paths)
    message = quote(
        f"選択した {len(force_paths)} 件の強制再インデックスを開始しました" if force_paths else "インデックスを開始しました"
    )
    return RedirectResponse(url=f"/settings?message={message}", status_code=303)


@app.get("/settings/progress")
def settings_progress() -> JSONResponse:
    return JSONResponse(index_job.get_progress())


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
        saved = pdf_export_service.save_pdf_export_to_configured_dir(
            pdf_path,
            pages,
            books_dir=get_books_dir(),
            save_dir=get_pdf_export_save_dir(),
        )
    except PdfSourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail="PDF not found") from exc
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
