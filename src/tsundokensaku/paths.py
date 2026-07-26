from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote


CONTAINER_BOOKS_DIRS = (Path("/data/books"), Path("/books/tech"))


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


def unique_destination_path(destination: Path) -> Path:
    if not destination.exists():
        return destination

    stem = destination.stem
    suffix = destination.suffix
    for index in range(2, 10_000):
        candidate = destination.with_name(f"{stem} ({index}){suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(destination)


def unique_export_destination_path(destination: Path) -> Path:
    if not destination.exists():
        return destination

    stem = destination.stem
    suffix = destination.suffix
    for index in range(2, 10_000):
        candidate = destination.with_name(f"{stem}_{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(destination)
