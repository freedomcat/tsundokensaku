from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_ROOT = Path(r"G:\マイドライブ\books")
DEFAULT_DESTINATION_DIR = PROJECT_ROOT / "books" / "tech"
BOOKSCAN_TAG = "#Bookscan"
TECH_BOOK_TAG = "#技術書"


@dataclass(frozen=True)
class BookItem:
    title: str
    filename: str | None
    inferred_from: str
    destination_filename: str | None


@dataclass(frozen=True)
class ImportSummary:
    matched_pages: int
    copied: int
    skipped_existing: int
    missing_filenames: int
    missing_sources: int


def find_default_export_json() -> Path:
    local_exports = sorted(Path.cwd().glob("shino-books_*.json"), key=lambda path: path.stat().st_mtime)
    if local_exports:
        return local_exports[-1]

    downloads = Path.home() / "Downloads"
    download_exports = sorted(downloads.glob("shino-books_*.json"), key=lambda path: path.stat().st_mtime)
    if download_exports:
        return download_exports[-1]

    return Path("shino-books_YYYYMMDD_HHMMSS.json")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def page_text(page: dict) -> str:
    return "\n".join(line.get("text", "") for line in page.get("lines", []))


def decode_repeatedly(value: str) -> str:
    current = value
    while True:
        decoded = unquote(current)
        if decoded == current:
            return decoded
        current = decoded


def extract_bookscan_filename(text: str) -> str | None:
    for match in re.finditer(r"https?://system\.bookscan\.co\.jp/\S+", text, re.IGNORECASE):
        raw_url = match.group(0).strip("[]()<> \t\r\n")
        parsed = urlparse(raw_url)
        values = parse_qs(parsed.query).get("f")
        if not values:
            continue

        filename = decode_repeatedly(values[0])
        if filename.lower().endswith(".pdf"):
            return filename

    return None


def extract_freedomcat_filename(text: str) -> str | None:
    for match in re.finditer(r"https?://books\.freedomcat\.com/\S+?\.pdf", text, re.IGNORECASE):
        raw_url = match.group(0).strip("[]()<> \t\r\n")
        parsed = urlparse(raw_url)
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


def make_destination_filename(title: str, filename: str | None) -> str | None:
    if filename is None:
        return None
    book_id = extract_book_id(filename)
    short_title = sanitize_filename_part(title, max_length=48)
    return f"{book_id}_{short_title}.pdf"


def collect_books(export_json: Path) -> list[BookItem]:
    data = load_json(export_json)
    books: list[BookItem] = []

    for page in data.get("pages", []):
        text = page_text(page)
        if BOOKSCAN_TAG not in text or TECH_BOOK_TAG not in text:
            continue

        filename = extract_bookscan_filename(text)
        inferred_from = "Bookscan f parameter"
        if filename is None:
            filename = extract_freedomcat_filename(text)
            inferred_from = "books.freedomcat.com URL"

        books.append(
            BookItem(
                title=page.get("title", ""),
                filename=filename,
                inferred_from=inferred_from if filename else "not found",
                destination_filename=make_destination_filename(page.get("title", ""), filename),
            )
        )

    return books


def windows_extended_path(path: Path) -> str:
    """Return a path string that works with long Windows paths."""
    resolved = path.resolve()
    path_text = str(resolved)

    if os.name != "nt" or path_text.startswith("\\\\?\\"):
        return path_text

    if path_text.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path_text.lstrip("\\")

    return "\\\\?\\" + path_text


def copy_pdf(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(windows_extended_path(source), destination)


def ensure_destination_writable(destination_dir: Path) -> None:
    destination_dir.mkdir(parents=True, exist_ok=True)
    probe_path = destination_dir / f".write-test-{os.getpid()}.tmp"
    try:
        with probe_path.open("wb") as handle:
            handle.write(b"ok")
    except OSError as exc:
        fallback = Path(tempfile.gettempdir()) / "tsundokensaku-books-tech"
        raise RuntimeError(
            "Could not create files in the destination directory.\n"
            f"Destination: {destination_dir}\n"
            f"Original error: {exc}\n\n"
            "Try a shorter/unprotected destination first, for example:\n"
            f"  python scripts\\import_books_from_cosense.py --destination \"{fallback}\"\n\n"
            "After that, index with:\n"
            f"  python -m tsundokensaku index --books-dir \"{fallback}\""
        ) from exc
    finally:
        try:
            probe_path.unlink()
        except FileNotFoundError:
            pass


def write_manifest(books: list[BookItem], *, source_root: Path, destination_dir: Path, manifest_path: Path) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "title",
                "source_filename",
                "destination_filename",
                "source_path",
                "destination_path",
                "inferred_from",
            ],
        )
        writer.writeheader()
        for book in books:
            if not book.filename or not book.destination_filename:
                continue
            writer.writerow(
                {
                    "title": book.title,
                    "source_filename": book.filename,
                    "destination_filename": book.destination_filename,
                    "source_path": str(source_root / book.filename),
                    "destination_path": str(destination_dir / book.destination_filename),
                    "inferred_from": book.inferred_from,
                }
            )


def import_books(
    books: list[BookItem],
    *,
    source_root: Path,
    destination_dir: Path,
    dry_run: bool,
    overwrite: bool,
    quiet: bool,
    manifest_path: Path,
) -> ImportSummary:
    copied = 0
    skipped_existing = 0
    missing_filenames = 0
    missing_sources = 0

    destination_dir = destination_dir.resolve()
    if not dry_run:
        destination_dir.mkdir(parents=True, exist_ok=True)
        if not destination_dir.is_dir():
            raise RuntimeError(f"Destination directory was not created: {destination_dir}")
        ensure_destination_writable(destination_dir)

    for index, book in enumerate(books, start=1):
        if not quiet:
            print(f"{index}. {book.title}")

        if not book.filename:
            missing_filenames += 1
            if not quiet:
                print("   SKIP: PDF filename could not be inferred.")
            continue

        if not book.destination_filename:
            missing_filenames += 1
            if not quiet:
                print("   SKIP: destination filename could not be generated.")
            continue

        source = source_root / book.filename
        destination = destination_dir / book.destination_filename

        if destination.exists() and not overwrite:
            skipped_existing += 1
            if not quiet:
                print(f"   EXISTS: {destination}")
            continue

        if not source.exists():
            missing_sources += 1
            if not quiet:
                print(f"   MISSING: {source}")
            continue

        if dry_run:
            if not quiet:
                print(f"   DRY-RUN: Copy {source} -> {destination}")
        else:
            copy_pdf(source, destination)
            copied += 1
            if not quiet:
                print(f"   COPIED: {destination}")

    if not dry_run:
        write_manifest(books, source_root=source_root, destination_dir=destination_dir, manifest_path=manifest_path)

    return ImportSummary(
        matched_pages=len(books),
        copied=copied,
        skipped_existing=skipped_existing,
        missing_filenames=missing_filenames,
        missing_sources=missing_sources,
    )


def print_summary(summary: ImportSummary, *, export_json: Path, source_root: Path, destination_dir: Path) -> None:
    print()
    print(f"Export JSON: {export_json}")
    print(f"Source root: {source_root}")
    print(f"Destination: {destination_dir}")
    print(f"Matched pages: {summary.matched_pages}")
    print(f"Copied: {summary.copied}")
    print(f"Skipped existing: {summary.skipped_existing}")
    print(f"Missing filenames: {summary.missing_filenames}")
    print(f"Missing source PDFs: {summary.missing_sources}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import #Bookscan and #技術書 PDFs from a Cosense/Scrapbox export."
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=find_default_export_json(),
        help="Cosense/Scrapbox export JSON. Defaults to the newest shino-books_*.json in cwd or Downloads.",
    )
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION_DIR)
    parser.add_argument("--manifest", type=Path, default=PROJECT_ROOT / "data" / "import_manifest.csv")
    parser.add_argument("--dry-run", action="store_true", help="Show planned copies without copying files.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite PDFs that already exist in destination.")
    parser.add_argument("--quiet", action="store_true", help="Only print the summary.")
    parser.add_argument("--list-titles", action="store_true", help="Print matched titles and exit without copying.")
    args = parser.parse_args()

    books = collect_books(args.json)
    if args.list_titles:
        for index, book in enumerate(books, start=1):
            print(f"{index}. {book.title}")
        print(f"\nMatched pages: {len(books)}")
        return 0

    summary = import_books(
        books,
        source_root=args.source_root,
        destination_dir=args.destination,
        dry_run=args.dry_run,
        overwrite=args.overwrite,
        quiet=args.quiet,
        manifest_path=args.manifest,
    )
    print_summary(
        summary,
        export_json=args.json,
        source_root=args.source_root,
        destination_dir=args.destination,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
