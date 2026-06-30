from __future__ import annotations

import argparse
from pathlib import Path

from pypdf import PdfReader

from tsundokensaku.pdf_export import default_output_path, export_selected_pages, parse_page_selection


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export selected PDF pages for sharing, e.g. to NotebookLM."
    )
    parser.add_argument("input_pdf", type=Path, help="Source PDF file.")
    parser.add_argument(
        "--pages",
        default="1-",
        help="Page selection such as '11-15', '1,3,5' or '1-3,8-10'. Defaults to all pages.",
    )
    parser.add_argument("--output", type=Path, help="Output PDF path. Defaults to <input>_p<pages>.pdf")
    args = parser.parse_args()

    reader = PdfReader(str(args.input_pdf))
    page_numbers = parse_page_selection(args.pages, len(reader.pages))
    output_pdf = args.output or default_output_path(args.input_pdf, page_numbers)
    export_selected_pages(args.input_pdf, output_pdf, page_numbers)
    print(f"Wrote {output_pdf} ({len(page_numbers)} pages)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
