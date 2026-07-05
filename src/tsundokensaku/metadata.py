from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse


BOOKSCAN_TAG = "#Bookscan"
TECH_BOOK_TAG = "#技術書"
KINDLE_TAG = "#Kindle"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"
_ENV_LOADED = False


@dataclass(frozen=True)
class BookMetadata:
    title: str
    scrapbox_url: str | None = None
    cover_url: str | None = None


@dataclass(frozen=True)
class ScrapboxMemo:
    title: str
    body: str
    scrapbox_url: str | None = None
    cover_url: str | None = None


@dataclass(frozen=True)
class KindleBookMetadata:
    title: str
    external_id: str
    kindle_url: str
    amazon_url: str | None = None
    scrapbox_url: str | None = None
    cover_url: str | None = None


def load_env_file(path: Path = ENV_FILE) -> None:
    global _ENV_LOADED
    if _ENV_LOADED or not path.exists():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value

    _ENV_LOADED = True


load_env_file()


def get_scrapbox_project_url() -> str | None:
    configured = (
        os.environ.get("SCRAPBOX_BASE_URL")
        or os.environ.get("BASE_URL")
        or os.environ.get("SCRAPBOX_PROJECT_URL")
    )
    return configured.rstrip("/") if configured else None


def find_export_json(project_root: Path) -> Path | None:
    configured = os.environ.get("SCRAPBOX_EXPORT_JSON")
    if configured:
        path = Path(configured)
        if path.exists():
            return path
        # Configured path may be a host-only path (e.g. set for native CLI
        # use but not mounted in the container). Fall through to search
        # instead of giving up, so an uploaded/imported export is still found.

    exports = sorted(project_root.glob("shino-books_*.json"), key=lambda path: path.stat().st_mtime)
    return exports[-1] if exports else None


def _line_text(line: object) -> str:
    """Scrapbox export JSON represents lines as plain strings; older/other
    export variants nest the text under a "text" key. Support both."""
    if isinstance(line, str):
        return line
    if isinstance(line, dict):
        return str(line.get("text", ""))
    return str(line)


def load_metadata_by_pdf_stem(export_json: Path | None, *, project_url: str | None = None) -> dict[str, BookMetadata]:
    if export_json is None or not export_json.exists():
        return {}

    base_url = (project_url or get_scrapbox_project_url())
    if base_url:
        base_url = base_url.rstrip("/")
    data = json.loads(export_json.read_text(encoding="utf-8"))
    metadata: dict[str, BookMetadata] = {}

    for page in data.get("pages", []):
        title = page.get("title", "")
        lines = page.get("lines", [])
        text = "\n".join(_line_text(line) for line in lines)
        if BOOKSCAN_TAG not in text or TECH_BOOK_TAG not in text:
            continue

        source_filename = extract_bookscan_filename(text) or extract_freedomcat_filename(text)
        if not title or not source_filename:
            continue

        stem = make_destination_stem(title, source_filename)
        book_id = extract_book_id(source_filename)
        cover_url = extract_cover_image_url(lines)
        scrapbox_url = f"{base_url}/{quote(title, safe='')}" if base_url else None
        entry = BookMetadata(title=title, scrapbox_url=scrapbox_url, cover_url=cover_url)
        metadata[stem] = entry
        # Actual PDF filenames don't always follow the {book_id}_{title}
        # naming this app generates (e.g. Google Drive exports keep their
        # own "{title} {pages}p_{book_id}" names), so also index by the
        # bare ID for matching against those.
        metadata[book_id] = entry

    return metadata


def load_scrapbox_memos(export_json: Path | None, *, project_url: str | None = None) -> list[ScrapboxMemo]:
    if export_json is None or not export_json.exists():
        return []

    base_url = (project_url or get_scrapbox_project_url())
    if base_url:
        base_url = base_url.rstrip("/")
    data = json.loads(export_json.read_text(encoding="utf-8"))
    memos: list[ScrapboxMemo] = []

    for page in data.get("pages", []):
        title = page.get("title", "")
        lines = page.get("lines", [])
        body = "\n".join(_line_text(line) for line in lines)
        if not title and not body:
            continue

        cover_url = extract_cover_image_url(lines)
        scrapbox_url = f"{base_url}/{quote(title, safe='')}" if base_url and title else None
        memos.append(
            ScrapboxMemo(
                title=title,
                body=body,
                scrapbox_url=scrapbox_url,
                cover_url=cover_url,
            )
        )

    return memos


def load_kindle_books(export_json: Path | None, *, project_url: str | None = None) -> list[KindleBookMetadata]:
    if export_json is None or not export_json.exists():
        return []

    base_url = (project_url or get_scrapbox_project_url())
    if base_url:
        base_url = base_url.rstrip("/")

    data = json.loads(export_json.read_text(encoding="utf-8"))
    books: list[KindleBookMetadata] = []
    seen: set[str] = set()
    for page in data.get("pages", []):
        title = page.get("title", "")
        lines = page.get("lines", [])
        text = "\n".join(_line_text(line) for line in lines)
        if KINDLE_TAG not in text or TECH_BOOK_TAG not in text:
            continue

        asin = extract_asin(text)
        if asin is None:
            continue

        key = asin.upper()
        if key in seen:
            continue
        seen.add(key)

        scrapbox_url = f"{base_url}/{quote(title, safe='')}" if base_url and title else None
        books.append(
            KindleBookMetadata(
                title=title,
                external_id=key,
                kindle_url=extract_kindle_url(text) or f"https://read.amazon.co.jp/?asin={key}",
                amazon_url=extract_amazon_url(text, asin=key),
                scrapbox_url=scrapbox_url,
                cover_url=extract_cover_image_url(lines),
            )
        )
    return books


def search_scrapbox_memos(
    export_json: Path | None,
    query: str,
    *,
    limit: int = 20,
    project_url: str | None = None,
) -> list[ScrapboxMemo]:
    normalized_query = query.strip()
    if not normalized_query:
        return []

    memos = load_scrapbox_memos(export_json, project_url=project_url)
    terms = [term.strip('"') for term in re.findall(r'"[^"]+"|\S+', normalized_query) if term.strip('"')]
    if not terms:
        terms = [normalized_query]

    matches = []
    for memo in memos:
        haystack = f"{memo.title}\n{memo.body}".lower()
        if all(term.lower() in haystack for term in terms):
            matches.append(memo)

    return matches[:limit]


def metadata_for_pdf(path: str | Path, metadata_by_stem: dict[str, BookMetadata]) -> BookMetadata | None:
    stem = Path(path).stem
    if stem in metadata_by_stem:
        return metadata_by_stem[stem]

    # DB paths created inside Docker may use /data/books/... or legacy /books/tech/...,
    # while the Web UI may resolve files through a WSL path. The basename is stable.
    name_stem = Path(Path(path).name).stem
    if name_stem in metadata_by_stem:
        return metadata_by_stem[name_stem]

    # Filenames not generated by this app (e.g. Google Drive exports) won't
    # match the {book_id}_{title} stem pattern; fall back to the bare ISBN/ASIN.
    return metadata_by_stem.get(extract_book_id(name_stem))


def _normalize_title_value(value: object) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value).replace("\x00", " ")).strip()
    return text or None


def read_pdf_metadata_title(pdf_path: str | Path) -> str | None:
    try:
        from pypdf import PdfReader
    except Exception:
        return None

    try:
        reader = PdfReader(str(pdf_path))
    except Exception:
        return None

    metadata = getattr(reader, "metadata", None)
    if metadata is None:
        return None

    title = getattr(metadata, "title", None)
    if not title and hasattr(metadata, "get"):
        title = metadata.get("/Title") or metadata.get("Title")
    return _normalize_title_value(title)


def sanitize_pdf_filename_title(filename: str | Path) -> str:
    stem = Path(filename).stem
    cleaned = stem.replace("_", " ").replace("-", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned or "book"


def resolve_pdf_display_title(path: str | Path, metadata_by_stem: dict[str, BookMetadata]) -> str:
    pdf_title = read_pdf_metadata_title(path)
    if pdf_title:
        return pdf_title

    metadata = metadata_for_pdf(path, metadata_by_stem)
    if metadata:
        metadata_title = _normalize_title_value(metadata.title)
        if metadata_title:
            return metadata_title

    return sanitize_pdf_filename_title(path)


def decode_repeatedly(value: str) -> str:
    current = value
    while True:
        decoded = unquote(current)
        if decoded == current:
            return decoded
        current = decoded


def extract_bookscan_filename(text: str) -> str | None:
    for match in re.finditer(r"https?://system\.bookscan\.co\.jp/\S+", text, re.IGNORECASE):
        parsed = urlparse(match.group(0).strip("[]()<> \t\r\n"))
        values = parse_qs(parsed.query).get("f")
        if not values:
            continue
        filename = decode_repeatedly(values[0])
        if filename.lower().endswith(".pdf"):
            return filename
    return None


def extract_freedomcat_filename(text: str) -> str | None:
    for match in re.finditer(r"https?://books\.freedomcat\.com/\S+?\.pdf", text, re.IGNORECASE):
        parsed = urlparse(match.group(0).strip("[]()<> \t\r\n"))
        filename = unquote(Path(parsed.path).name)
        if filename.lower().endswith(".pdf"):
            return filename
    return None


def extract_kindle_url(text: str) -> str | None:
    for match in re.finditer(r"https?://read\.amazon\.co\.jp/[^\s\]]+", text, re.IGNORECASE):
        return match.group(0).strip("[]()<> \t\r\n")
    return None


def extract_amazon_url(text: str, *, asin: str) -> str | None:
    for match in re.finditer(r"https?://(?:www\.)?amazon\.co\.jp/(?:[^/\s\]]+/)?dp/([0-9A-Z]{10})[^\s\]]*", text, re.IGNORECASE):
        if match.group(1).upper() == asin.upper():
            return match.group(0).strip("[]()<> \t\r\n")
    return f"https://www.amazon.co.jp/dp/{asin}"


def extract_asin(text: str) -> str | None:
    kindle_url = extract_kindle_url(text)
    if kindle_url:
        values = parse_qs(urlparse(kindle_url).query).get("asin")
        if values and re.fullmatch(r"[0-9A-Z]{10}", values[0], re.IGNORECASE):
            return values[0].upper()

    for match in re.finditer(r"amazon\.co\.jp/(?:[^/\s\]]+/)?dp/([0-9A-Z]{10})", text, re.IGNORECASE):
        return match.group(1).upper()

    return None


def extract_cover_image_url(lines: list[object]) -> str | None:
    image_pattern = re.compile(r"\[(https?://[^\s\]]+\.(?:png|jpe?g|gif|webp))(?:\s+[^\]]+)?\]", re.IGNORECASE)
    for line in lines:
        text = _line_text(line)
        match = image_pattern.search(text)
        if match:
            return match.group(1)
    return None


def sanitize_filename_part(value: str, *, max_length: int) -> str:
    sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    sanitized = re.sub(r"\s+", " ", sanitized).strip(" .")
    return sanitized[:max_length].strip(" .") or "book"


def extract_book_id(filename: str) -> str:
    stem = Path(filename).stem
    match = re.search(r"_([0-9]{9}[0-9X]|[0-9]{13}|B[0-9A-Z]{9})$", stem, re.IGNORECASE)
    if match:
        return match.group(1)
    return sanitize_filename_part(stem, max_length=24)


def make_destination_stem(title: str, filename: str) -> str:
    book_id = extract_book_id(filename)
    short_title = sanitize_filename_part(title, max_length=48)
    return f"{book_id}_{short_title}"
