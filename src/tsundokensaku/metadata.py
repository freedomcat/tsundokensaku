from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse


BOOKSCAN_TAG = "#Bookscan"
TECH_BOOK_TAG = "#技術書"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"
_ENV_LOADED = False


@dataclass(frozen=True)
class BookMetadata:
    title: str
    scrapbox_url: str


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
        return path if path.exists() else None

    exports = sorted(project_root.glob("shino-books_*.json"), key=lambda path: path.stat().st_mtime)
    return exports[-1] if exports else None


def load_metadata_by_pdf_stem(export_json: Path | None, *, project_url: str | None = None) -> dict[str, BookMetadata]:
    if export_json is None or not export_json.exists():
        return {}

    base_url = (project_url or get_scrapbox_project_url())
    if not base_url:
        return {}
    base_url = base_url.rstrip("/")
    data = json.loads(export_json.read_text(encoding="utf-8"))
    metadata: dict[str, BookMetadata] = {}

    for page in data.get("pages", []):
        title = page.get("title", "")
        text = "\n".join(line.get("text", "") for line in page.get("lines", []))
        if BOOKSCAN_TAG not in text or TECH_BOOK_TAG not in text:
            continue

        source_filename = extract_bookscan_filename(text) or extract_freedomcat_filename(text)
        if not title or not source_filename:
            continue

        stem = make_destination_stem(title, source_filename)
        metadata[stem] = BookMetadata(title=title, scrapbox_url=f"{base_url}/{quote(title, safe='')}")

    return metadata


def metadata_for_pdf(path: str | Path, metadata_by_stem: dict[str, BookMetadata]) -> BookMetadata | None:
    stem = Path(path).stem
    if stem in metadata_by_stem:
        return metadata_by_stem[stem]

    # DB paths created inside Docker often use /books/tech/..., while the Web UI
    # may resolve files through a WSL path. The basename is stable across both.
    name_stem = Path(Path(path).name).stem
    return metadata_by_stem.get(name_stem)


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
