from __future__ import annotations

import os
import sqlite3
from urllib.parse import quote
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from tsundokensaku.database import SEARCH_SCOPES, connect, list_books, search
from tsundokensaku.indexer import find_pdfs, index_books
from tsundokensaku.metadata import BookMetadata, find_export_json, load_metadata_by_pdf_stem, load_scrapbox_memos, metadata_for_pdf, search_scrapbox_memos


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BOOKS_DIR = Path("books/tech")
CONTAINER_BOOKS_DIR = Path("/books/tech")
DEFAULT_DB_PATH = Path("data/index.db")
TEMPLATES_DIR = PROJECT_ROOT / "templates"
STATIC_DIR = PROJECT_ROOT / "static"

app = FastAPI(title="tsundokensaku")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def get_books_dir() -> Path:
    return Path(os.environ.get("BOOKS_DIR", str(DEFAULT_BOOKS_DIR)))


def get_db_path() -> Path:
    db_dir = Path(os.environ.get("DB_DIR", str(DEFAULT_DB_PATH.parent)))
    return db_dir / DEFAULT_DB_PATH.name


def get_metadata() -> dict[str, BookMetadata]:
    return load_metadata_by_pdf_stem(find_export_json(PROJECT_ROOT))


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


def get_db_stats(db_path: Path) -> dict[str, int]:
    try:
        connection = connect(db_path)
        books = list_books(connection)
        page_count = connection.execute("SELECT COUNT(*) AS count FROM pages").fetchone()["count"]
        connection.close()
        return {"book_count": len(books), "page_count": int(page_count)}
    except sqlite3.OperationalError:
        return {"book_count": 0, "page_count": 0}


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
            "page_count": db_stats["page_count"],
            "scope": "all",
            "scope_options": SEARCH_SCOPE_OPTIONS,
        },
    )


@app.get("/search", response_class=HTMLResponse)
def search_page(request: Request, q: str = "", sort: str = "rank", scope: str = "all") -> HTMLResponse:
    books_dir = get_books_dir()
    db_path = get_db_path()
    export_json = find_export_json(PROJECT_ROOT)
    metadata_by_stem = load_metadata_by_pdf_stem(export_json)
    normalized_scope = scope if scope in SEARCH_SCOPES else "all"
    connection = connect(db_path)
    title_results = search(connection, q, limit=50, scope="title") if q.strip() and normalized_scope in {"all", "title"} else []
    body_results = search(connection, q, limit=50, scope="body") if q.strip() and normalized_scope in {"all", "body"} else []
    connection.close()
    memo_results = search_scrapbox_memos(export_json, q, limit=50) if q.strip() and normalized_scope in {"all", "memo"} else []
    rendered_results = []
    if normalized_scope in {"all", "title", "body"}:
        rendered_results.extend(
            {
                "title": result.title,
                "path": result.path,
                "page_number": result.page_number,
                "snippet": result.snippet,
                "result_type": "pdf",
                "cover_url": (
                    metadata.cover_url
                    if (metadata := metadata_for_pdf(result.path, metadata_by_stem))
                    else None
                ),
                "open_url": raw_pdf_url(result.path, books_dir, page_number=result.page_number),
                "scrapbox_url": (
                    metadata.scrapbox_url
                    if (metadata := metadata_for_pdf(result.path, metadata_by_stem))
                    else None
                ),
            }
            for result in title_results + body_results
        )
    if normalized_scope in {"all", "memo"}:
        rendered_results.extend(
            {
                "title": memo.title,
                "path": memo.title,
                "page_number": None,
                "snippet": memo.body,
                "result_type": "memo",
                "cover_url": memo.cover_url,
                "open_url": memo.scrapbox_url,
                "scrapbox_url": memo.scrapbox_url,
            }
            for memo in memo_results
        )
    rendered_results = sort_results(rendered_results, sort)
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
            "sort_options": sort_options,
            "scope": normalized_scope,
            "scope_options": SEARCH_SCOPE_OPTIONS,
            "books_dir": books_dir,
            "db_path": db_path,
            "results": rendered_results,
            "result_count": len(rendered_results),
        },
    )


@app.get("/manage", response_class=HTMLResponse)
def manage_index(request: Request, message: str = "") -> HTMLResponse:
    books_dir = get_books_dir()
    db_path = get_db_path()
    metadata_by_stem = get_metadata()
    pdf_paths = list(find_pdfs(books_dir))
    try:
        connection = connect(db_path)
        indexed_paths = {row["path"] for row in connection.execute("SELECT path FROM books").fetchall()}
        connection.close()
    except sqlite3.OperationalError:
        indexed_paths = set()
    items = [
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
    return templates.TemplateResponse(
        request,
        "manage.html",
        {
            "request": request,
            "books_dir": books_dir,
            "db_path": db_path,
            "pdf_count": len(pdf_paths),
            "items": items,
            "message": message,
        },
    )


@app.post("/manage/index")
def run_index() -> RedirectResponse:
    books_dir = get_books_dir()
    db_path = get_db_path()
    indexed = index_books(books_dir=books_dir, db_path=db_path)
    message = quote(f"Indexed {len(indexed)} books under {books_dir}")
    return RedirectResponse(url=f"/manage?message={message}", status_code=303)


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
