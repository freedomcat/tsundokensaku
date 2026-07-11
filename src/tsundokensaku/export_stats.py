from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from tsundokensaku.database import PackItemRecord, get_book
from tsundokensaku.pdf_export import parse_page_selection
from tsundokensaku.pdf_outline import get_page_count
from tsundokensaku.token_estimate import TextStats, count_text_stats


# web.CONTAINER_BOOKS_DIRS のミラー。旧 /books/tech/... パスの books.path が
# 資料項目の pdf_path として残っているケースを解決するために必要。
# web.py 側の実装（唯一の正）は変更しないため、Phase 3A では意図的に複製する。
# 統合は Phase 3B（web.py をどのみち変更する段階）で検討する。
_CONTAINER_BOOKS_DIRS = (Path("/data/books"), Path("/books/tech"))


def _resolve_pdf_path(pdf_path: str | Path, books_dir: Path) -> Path | None:
    """web.resolve_pdf_path と同じ解決方針の複製（意図は上記コメント参照）。"""
    candidate = Path(pdf_path)
    books_root = books_dir.resolve()

    candidates: list[Path] = []
    if candidate.is_absolute():
        candidates.append(candidate)
        for container_books_dir in _CONTAINER_BOOKS_DIRS:
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


def _find_indexed_book(connection: sqlite3.Connection, relative: Path, *, books_dir: Path):
    """web._get_indexed_book と同じ二重候補チェック（books.path の新旧表記ゆれ対応）。"""
    for path_candidate in (relative, books_dir.expanduser().resolve() / relative):
        book = get_book(connection, path=path_candidate)
        if book is not None:
            return book
    return None


@dataclass(frozen=True)
class ItemStats:
    item: PackItemRecord
    page_numbers: list[int]
    stats: TextStats
    unindexed_pages: int
    missing_pdf: bool


def _empty_item_stats(item: PackItemRecord, *, missing_pdf: bool) -> ItemStats:
    return ItemStats(
        item=item,
        page_numbers=[],
        stats=TextStats(cjk_chars=0, other_chars=0),
        unindexed_pages=0,
        missing_pdf=missing_pdf,
    )


def _resolve_total_page_count(
    connection: sqlite3.Connection,
    *,
    book_id: int | None,
    absolute_pdf_path: Path,
) -> int | None:
    """spec展開に使う総ページ数を得る。

    インデックス済みならDBの pages テーブルの最大ページ番号を使い、PDFファイルは
    開かない（8.3節: プレビュー時の負荷を抑えるため）。未インデックス、または
    pages テーブルに行がない場合のみ fitz でページ数だけを取得する。
    """
    if book_id is not None:
        row = connection.execute(
            "SELECT MAX(page_number) AS max_page FROM pages WHERE book_id = ?",
            (book_id,),
        ).fetchone()
        max_page = row["max_page"] if row is not None else None
        if max_page is not None:
            return int(max_page)

    return get_page_count(absolute_pdf_path)


def _collect_single_item_stats(
    connection: sqlite3.Connection,
    item: PackItemRecord,
    *,
    books_dir: Path,
) -> ItemStats:
    relative = _resolve_pdf_path(item.pdf_path, books_dir)
    if relative is None:
        return _empty_item_stats(item, missing_pdf=True)

    absolute_pdf_path = books_dir.expanduser().resolve() / relative
    book = _find_indexed_book(connection, relative, books_dir=books_dir)
    book_id = book.id if book is not None else None

    total_page_count = _resolve_total_page_count(connection, book_id=book_id, absolute_pdf_path=absolute_pdf_path)

    page_spec = item.pages.strip()
    if not page_spec or total_page_count is None:
        # ページ未指定、またはページ数を確定できない（fitz不可・破損PDF等の稀な
        # ケース）。実行系エクスポートと異なりプレビュー系は落とさず空扱いにする。
        return _empty_item_stats(item, missing_pdf=False)

    try:
        page_numbers = parse_page_selection(page_spec, total_page_count)
    except ValueError:
        # 保存後にPDFの実ページ数が変わった等でspecが現在のページ数と整合しない
        # 場合。実行系エクスポートは400にするが、集計は落とさず空扱いにする。
        return _empty_item_stats(item, missing_pdf=False)

    if book_id is None:
        # books テーブルに行がない = 全ページ未インデックス
        return ItemStats(
            item=item,
            page_numbers=page_numbers,
            stats=TextStats(cjk_chars=0, other_chars=0),
            unindexed_pages=len(page_numbers),
            missing_pdf=False,
        )

    placeholders = ",".join("?" for _ in page_numbers)
    rows = connection.execute(
        f"SELECT page_number, text FROM pages WHERE book_id = ? AND page_number IN ({placeholders})",
        [book_id, *page_numbers],
    ).fetchall()
    texts_by_page = {int(row["page_number"]): str(row["text"]) for row in rows}

    total_cjk = 0
    total_other = 0
    for text in texts_by_page.values():
        page_stats = count_text_stats(text)
        total_cjk += page_stats.cjk_chars
        total_other += page_stats.other_chars

    unindexed_pages = sum(1 for page_number in page_numbers if page_number not in texts_by_page)

    return ItemStats(
        item=item,
        page_numbers=page_numbers,
        stats=TextStats(cjk_chars=total_cjk, other_chars=total_other),
        unindexed_pages=unindexed_pages,
        missing_pdf=False,
    )


def collect_item_stats(
    connection: sqlite3.Connection,
    items: Sequence[PackItemRecord],
    *,
    books_dir: Path,
) -> list[ItemStats]:
    return [_collect_single_item_stats(connection, item, books_dir=books_dir) for item in items]
