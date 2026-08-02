from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import stat

from tsundokensaku import paths


class PdfDirectoryImportError(Exception):
    pass


class PdfDirectorySourceNotFoundError(PdfDirectoryImportError):
    pass


class PdfDirectorySourceNotDirectoryError(PdfDirectoryImportError):
    pass


class PdfDirectoryOverlapError(PdfDirectoryImportError):
    pass


class PdfDirectoryBoundaryError(PdfDirectoryImportError):
    pass


class PdfDirectoryFilesystemError(PdfDirectoryImportError):
    pass


@dataclass(frozen=True)
class PdfDirectoryImportResult:
    copied: int
    skipped: int
    total: int

    def __post_init__(self) -> None:
        values = (self.copied, self.skipped, self.total)
        if not all(isinstance(value, int) for value in values):
            raise TypeError("copied, skipped, total must be integers")
        if any(value < 0 for value in values):
            raise ValueError("copied, skipped, total must be non-negative")
        if self.total != self.copied + self.skipped:
            raise ValueError("total must equal copied + skipped")


def save_uploaded_pdf(
    filename: str,
    content: bytes,
    books_dir: Path,
    *,
    relative_path: str | None = None,
) -> Path:
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
    destination = paths.unique_destination_path(destination)
    destination.write_bytes(content)
    return destination


def import_pdfs_from_directory(
    source_dir: Path,
    books_dir: Path,
) -> PdfDirectoryImportResult:
    source_path = Path(source_dir).expanduser()
    books_path = Path(books_dir).expanduser()

    source_root = _resolve_source_root(source_path)
    books_root = _resolve_books_root(books_path)
    _reject_overlapping_roots(source_root, books_root)

    pdf_paths = _list_source_pdfs(source_root)
    try:
        books_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PdfDirectoryFilesystemError() from exc

    copied = 0
    skipped = 0
    for pdf_path in pdf_paths:
        destination = books_root / pdf_path.relative_to(source_root)
        _validate_destination(books_root, destination)
        if _destination_exists(destination):
            if _destination_is_file(destination):
                skipped += 1
                continue
            if _destination_is_dir(destination):
                raise PdfDirectoryFilesystemError()
            raise PdfDirectoryFilesystemError()

        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise PdfDirectoryFilesystemError() from exc
        try:
            shutil.copy2(pdf_path, destination)
        except OSError as exc:
            raise PdfDirectoryFilesystemError() from exc
        copied += 1

    return PdfDirectoryImportResult(copied=copied, skipped=skipped, total=len(pdf_paths))


def _resolve_source_root(source_path: Path) -> Path:
    try:
        source_stat = source_path.lstat()
    except FileNotFoundError as exc:
        raise PdfDirectorySourceNotFoundError() from exc
    except OSError as exc:
        raise PdfDirectoryFilesystemError() from exc

    if stat.S_ISLNK(source_stat.st_mode):
        raise PdfDirectoryBoundaryError()

    try:
        source_root = source_path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise PdfDirectorySourceNotFoundError() from exc
    except OSError as exc:
        raise PdfDirectoryFilesystemError() from exc

    try:
        if not source_root.is_dir():
            raise PdfDirectorySourceNotDirectoryError()
    except OSError as exc:
        raise PdfDirectoryFilesystemError() from exc
    return source_root


def _resolve_books_root(books_path: Path) -> Path:
    _reject_broken_symlinks_in_existing_path(books_path)
    try:
        return books_path.resolve(strict=False)
    except OSError as exc:
        raise PdfDirectoryFilesystemError() from exc


def _reject_broken_symlinks_in_existing_path(path: Path) -> None:
    absolute_path = path if path.is_absolute() else Path.cwd() / path
    current = Path(absolute_path.anchor)
    for part in absolute_path.parts[1:]:
        current = current / part
        try:
            current_stat = current.lstat()
        except FileNotFoundError:
            if current.is_symlink():
                raise PdfDirectoryBoundaryError()
            break
        except OSError as exc:
            raise PdfDirectoryFilesystemError() from exc
        if stat.S_ISLNK(current_stat.st_mode) and not current.exists():
            raise PdfDirectoryBoundaryError()


def _reject_overlapping_roots(source_root: Path, books_root: Path) -> None:
    if source_root == books_root or source_root.is_relative_to(books_root) or books_root.is_relative_to(source_root):
        raise PdfDirectoryOverlapError()


def _list_source_pdfs(source_root: Path) -> list[Path]:
    def raise_filesystem_error(exc: OSError) -> None:
        raise PdfDirectoryFilesystemError() from exc

    pdf_paths: list[Path] = []
    try:
        walker = os.walk(source_root, topdown=True, followlinks=False, onerror=raise_filesystem_error)
        for root, dirs, files in walker:
            root_path = Path(root)
            dirs[:] = sorted(name for name in dirs if not (root_path / name).is_symlink())
            for filename in sorted(files):
                if not filename.endswith(".pdf"):
                    continue
                candidate = root_path / filename
                if candidate.is_symlink():
                    continue
                try:
                    if candidate.is_file():
                        pdf_paths.append(candidate)
                except OSError as exc:
                    raise PdfDirectoryFilesystemError() from exc
    except OSError as exc:
        raise PdfDirectoryFilesystemError() from exc
    return sorted(pdf_paths, key=lambda path: path.relative_to(source_root).as_posix())


def _validate_destination(books_root: Path, destination: Path) -> None:
    _reject_destination_symlinks(books_root, destination)
    try:
        normalized_destination = destination.resolve(strict=False)
    except OSError as exc:
        raise PdfDirectoryFilesystemError() from exc
    if normalized_destination != books_root and not normalized_destination.is_relative_to(books_root):
        raise PdfDirectoryBoundaryError()


def _reject_destination_symlinks(books_root: Path, destination: Path) -> None:
    current = books_root
    try:
        relative = destination.relative_to(books_root)
    except ValueError as exc:
        raise PdfDirectoryBoundaryError() from exc
    for part in relative.parts:
        current = current / part
        try:
            current_stat = current.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise PdfDirectoryFilesystemError() from exc
        if stat.S_ISLNK(current_stat.st_mode):
            raise PdfDirectoryBoundaryError()


def _destination_exists(destination: Path) -> bool:
    try:
        return destination.exists()
    except OSError as exc:
        raise PdfDirectoryFilesystemError() from exc


def _destination_is_file(destination: Path) -> bool:
    try:
        return destination.is_file()
    except OSError as exc:
        raise PdfDirectoryFilesystemError() from exc


def _destination_is_dir(destination: Path) -> bool:
    try:
        return destination.is_dir()
    except OSError as exc:
        raise PdfDirectoryFilesystemError() from exc
