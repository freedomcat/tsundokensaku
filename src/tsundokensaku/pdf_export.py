from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader, PdfWriter


def parse_page_selection(spec: str, page_count: int) -> list[int]:
    selected: list[int] = []
    seen: set[int] = set()

    for part in spec.split(","):
        chunk = part.strip()
        if not chunk:
            continue

        if "-" in chunk:
            start_text, end_text = chunk.split("-", 1)
            start = int(start_text) if start_text.strip() else 1
            end = int(end_text) if end_text.strip() else page_count
            if start > end:
                raise ValueError(f"Invalid page range: {chunk}")
            numbers = range(start, end + 1)
        else:
            numbers = [int(chunk)]

        for number in numbers:
            if number < 1 or number > page_count:
                raise ValueError(f"Page number out of range: {number} (1-{page_count})")
            if number in seen:
                continue
            seen.add(number)
            selected.append(number)

    if not selected:
        raise ValueError("No pages selected")

    return selected


def compact_page_selection(page_numbers: list[int]) -> str:
    if not page_numbers:
        return "selected"

    ranges: list[tuple[int, int]] = []
    start = end = page_numbers[0]
    for number in page_numbers[1:]:
        if number == end + 1:
            end = number
            continue
        ranges.append((start, end))
        start = end = number
    ranges.append((start, end))

    pieces = []
    for start, end in ranges:
        pieces.append(str(start) if start == end else f"{start}-{end}")
    return "_".join(pieces)


def default_output_path(input_pdf: Path, page_numbers: list[int]) -> Path:
    selection = compact_page_selection(page_numbers)
    safe_selection = re.sub(r"[^\w.-]+", "_", selection)
    return input_pdf.with_name(f"{input_pdf.stem}_p{safe_selection}.pdf")


def export_selected_pages(input_pdf: Path, output_pdf: Path, page_numbers: list[int]) -> None:
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    output_pdf.write_bytes(render_selected_pages(input_pdf, page_numbers))


def render_selected_pages(input_pdf: Path, page_numbers: list[int]) -> bytes:
    reader = PdfReader(str(input_pdf))
    writer = PdfWriter()

    for page_number in page_numbers:
        writer.add_page(reader.pages[page_number - 1])

    metadata = reader.metadata
    if metadata:
        writer.add_metadata({key: str(value) for key, value in metadata.items() if value is not None})

    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def merge_rendered_pdfs(rendered_pdfs: list[bytes]) -> bytes:
    if not rendered_pdfs:
        raise ValueError("rendered_pdfs must not be empty")

    readers = [PdfReader(BytesIO(pdf_bytes)) for pdf_bytes in rendered_pdfs]
    writer = PdfWriter()

    for reader in readers:
        for page in reader.pages:
            writer.add_page(page)

    metadata = readers[0].metadata
    if metadata:
        safe_metadata: dict[str, str] = {}
        for key, value in metadata.items():
            if value is None:
                continue
            metadata_key = str(key)
            if not metadata_key:
                continue
            if not metadata_key.startswith("/"):
                metadata_key = f"/{metadata_key}"
            safe_metadata[metadata_key] = str(value)
        if safe_metadata:
            try:
                writer.add_metadata(safe_metadata)
            except Exception:
                pass

    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()
