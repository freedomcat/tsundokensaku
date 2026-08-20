from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from tsundokensaku.database import PackItemRecord
from tsundokensaku import paths
from tsundokensaku.pdf_metadata_service import find_indexed_book
from tsundokensaku.pdf_export import parse_page_selection
from tsundokensaku.pdf_outline import get_page_count
from tsundokensaku.token_estimate import TextStats, count_text_stats


@dataclass(frozen=True)
class ItemStats:
    item: PackItemRecord
    page_numbers: list[int]
    stats: TextStats
    unindexed_pages: int
    missing_pdf: bool


@dataclass(frozen=True)
class PackItemStatsSummary:
    """previewと`/api/packs/stats`が共有する純粋な基礎集計。

    book_count/item_count/total_pagesと、estimated_chars/estimated_tokensの
    元になるTextStats合算値（combined_stats）のみを持つ。estimation/estimator
    といった表示用フィールドや、それらの値そのものの算出（projection）は
    含まない。各consumerが必要な値だけをここから導出する。
    """

    book_count: int
    item_count: int
    total_pages: int
    combined_stats: TextStats


def summarize_item_stats(item_stats: Sequence[ItemStats]) -> PackItemStatsSummary:
    book_count = len({entry.item.pdf_path for entry in item_stats})
    total_pages = sum(len(entry.page_numbers) for entry in item_stats)
    combined_stats = TextStats(
        cjk_chars=sum(entry.stats.cjk_chars for entry in item_stats),
        other_chars=sum(entry.stats.other_chars for entry in item_stats),
    )
    return PackItemStatsSummary(
        book_count=book_count,
        item_count=len(item_stats),
        total_pages=total_pages,
        combined_stats=combined_stats,
    )


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
    開かない（8.3節: プレビュー時の負荷を抑えるため）。未インデックス、
    pages テーブルに行がない、または pages テーブル自体が存在しない場合のみ
    fitz でページ数だけを取得する。
    """
    if book_id is not None:
        try:
            row = connection.execute(
                "SELECT MAX(page_number) AS max_page FROM pages WHERE book_id = ?",
                (book_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            row = None
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
    relative = paths.resolve_pdf_path(item.pdf_path, books_dir)
    if relative is None:
        return _empty_item_stats(item, missing_pdf=True)

    absolute_pdf_path = books_dir.expanduser().resolve() / relative
    book = find_indexed_book(connection, relative, books_dir=books_dir)
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

    # book_id が取れた時点で pages テーブルは存在するはず（books/pages は
    # initialize() が常に一緒に作る）だが、web.load_pages_text と同じ防御を揃える。
    placeholders = ",".join("?" for _ in page_numbers)
    try:
        rows = connection.execute(
            f"SELECT page_number, text FROM pages WHERE book_id = ? AND page_number IN ({placeholders})",
            [book_id, *page_numbers],
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
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
