from pathlib import Path

from tsundokensaku import paths


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
