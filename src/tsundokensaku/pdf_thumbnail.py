from __future__ import annotations

from pathlib import Path


def render_thumbnails(
    pdf_path: Path,
    page_numbers: list[int],
    *,
    zoom: float = 0.3,
    quality: int = 70,
) -> list[tuple[int, bytes]]:
    """指定ページをまとめてJPEGサムネイルとしてレンダリングする。

    fitz.open() のコストはページ数・ファイルサイズにほぼ比例し、蔵書に多い
    大きなスキャンPDF（100〜300MB級）では1回で数百msから1秒程度かかる
    （実測）。ページ単体のレンダリング自体は10ms未満と軽いため、1回の
    open() で複数ページをまとめて処理できるようこのシグネチャにしている。

    範囲外のページ番号は無視する（呼び出し側の spec パース由来で page_count
    を厳密に知らなくても安全に呼べるようにするため）。
    """
    import fitz  # PyMuPDF

    doc = fitz.open(str(pdf_path))
    try:
        matrix = fitz.Matrix(zoom, zoom)
        results: list[tuple[int, bytes]] = []
        for page_number in page_numbers:
            if page_number < 1 or page_number > doc.page_count:
                continue
            page = doc[page_number - 1]
            pix = page.get_pixmap(matrix=matrix)
            data = pix.tobytes("jpeg", jpg_quality=quality)
            results.append((page_number, data))
        return results
    finally:
        doc.close()
