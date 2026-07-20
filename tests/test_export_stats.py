import tempfile
import unittest
from pathlib import Path

from pypdf import PdfWriter

from tsundokensaku.database import PackItemRecord, PageRecord, connect, initialize, replace_pages, upsert_book
from tsundokensaku.export_stats import collect_item_stats
from tsundokensaku.token_estimate import TextStats, count_text_stats


def _write_blank_pdf(path: Path, page_count: int) -> None:
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=72, height=72)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        writer.write(handle)


def _pack_item(
    *,
    item_id: int,
    pdf_path: str,
    title: str = "テスト本",
    pages: str,
    position: int = 0,
) -> PackItemRecord:
    return PackItemRecord(
        id=item_id,
        pdf_path=pdf_path,
        title=title,
        pages=pages,
        collapsed=False,
        position=position,
        added_at="2026-07-11T00:00:00.000Z",
        updated_at="2026-07-11T00:00:00.000Z",
    )


class CollectItemStatsTest(unittest.TestCase):
    def test_indexed_pdf_reports_full_stats_without_unindexed_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"
            db_path = root / "index.db"
            pdf_path = books_dir / "sample.pdf"
            _write_blank_pdf(pdf_path, 3)

            connection = connect(db_path)
            initialize(connection)
            book_id = upsert_book(
                connection,
                path=pdf_path,
                title="テスト本",
                size_bytes=pdf_path.stat().st_size,
                modified_at=pdf_path.stat().st_mtime,
            )
            replace_pages(
                connection,
                book_id=book_id,
                title="テスト本",
                pages=[
                    PageRecord(page_number=1, text="はじめに"),
                    PageRecord(page_number=2, text="Chapter 1"),
                    PageRecord(page_number=3, text="おわりに"),
                ],
            )
            connection.commit()

            item = _pack_item(item_id=1, pdf_path="sample.pdf", pages="1-3")
            [result] = collect_item_stats(connection, [item], books_dir=books_dir)
            connection.close()

            self.assertEqual(result.page_numbers, [1, 2, 3])
            self.assertEqual(result.unindexed_pages, 0)
            self.assertFalse(result.missing_pdf)
            expected = TextStats(cjk_chars=0, other_chars=0)
            for text in ("はじめに", "Chapter 1", "おわりに"):
                page_stats = count_text_stats(text)
                expected = TextStats(
                    cjk_chars=expected.cjk_chars + page_stats.cjk_chars,
                    other_chars=expected.other_chars + page_stats.other_chars,
                )
            self.assertEqual(result.stats, expected)

    def test_partially_indexed_pdf_counts_missing_rows_as_unindexed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"
            db_path = root / "index.db"
            pdf_path = books_dir / "sample.pdf"
            _write_blank_pdf(pdf_path, 4)

            connection = connect(db_path)
            initialize(connection)
            book_id = upsert_book(
                connection,
                path=pdf_path,
                title="テスト本",
                size_bytes=pdf_path.stat().st_size,
                modified_at=pdf_path.stat().st_mtime,
            )
            # 意図的にページ3を欠落させる（部分インデックス状態を再現）
            replace_pages(
                connection,
                book_id=book_id,
                title="テスト本",
                pages=[
                    PageRecord(page_number=1, text="1ページ目"),
                    PageRecord(page_number=2, text="2ページ目"),
                    PageRecord(page_number=4, text="4ページ目"),
                ],
            )
            connection.commit()

            item = _pack_item(item_id=1, pdf_path="sample.pdf", pages="1-4")
            [result] = collect_item_stats(connection, [item], books_dir=books_dir)
            connection.close()

            self.assertEqual(result.page_numbers, [1, 2, 3, 4])
            self.assertEqual(result.unindexed_pages, 1)
            self.assertFalse(result.missing_pdf)

    def test_fully_unindexed_pdf_uses_fitz_page_count_and_reports_all_as_unindexed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"
            db_path = root / "index.db"
            pdf_path = books_dir / "sample.pdf"
            _write_blank_pdf(pdf_path, 5)

            connection = connect(db_path)
            initialize(connection)

            item = _pack_item(item_id=1, pdf_path="sample.pdf", pages="1-5")
            [result] = collect_item_stats(connection, [item], books_dir=books_dir)
            connection.close()

            self.assertEqual(result.page_numbers, [1, 2, 3, 4, 5])
            self.assertEqual(result.unindexed_pages, 5)
            self.assertFalse(result.missing_pdf)
            self.assertEqual(result.stats, TextStats(cjk_chars=0, other_chars=0))

    def test_db_without_books_table_is_treated_as_unindexed(self) -> None:
        # パックAPIは ensure_pack_schema（packs/pack_items/app_state のみ）で足りる
        # ため、一度も index を実行していないDBでは books テーブル自体が存在しない。
        # get_book が sqlite3.OperationalError を送出する状況を、web._get_indexed_book
        # と同様に「未インデックス」として扱えることを確認する。
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"
            db_path = root / "index.db"
            pdf_path = books_dir / "sample.pdf"
            _write_blank_pdf(pdf_path, 2)

            connection = connect(db_path)
            # initialize() を呼ばない = books/pages テーブルが存在しない状態

            item = _pack_item(item_id=1, pdf_path="sample.pdf", pages="1-2")
            [result] = collect_item_stats(connection, [item], books_dir=books_dir)
            connection.close()

            self.assertEqual(result.page_numbers, [1, 2])
            self.assertEqual(result.unindexed_pages, 2)
            self.assertFalse(result.missing_pdf)
            self.assertEqual(result.stats, TextStats(cjk_chars=0, other_chars=0))

    def test_missing_pdf_file_reports_missing_pdf_and_empty_stats(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"
            books_dir.mkdir()
            db_path = root / "index.db"

            connection = connect(db_path)
            initialize(connection)

            item = _pack_item(item_id=1, pdf_path="does-not-exist.pdf", pages="1-3")
            [result] = collect_item_stats(connection, [item], books_dir=books_dir)
            connection.close()

            self.assertTrue(result.missing_pdf)
            self.assertEqual(result.page_numbers, [])
            self.assertEqual(result.unindexed_pages, 0)
            self.assertEqual(result.stats, TextStats(cjk_chars=0, other_chars=0))

    def test_multi_range_page_spec_only_counts_selected_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"
            db_path = root / "index.db"
            pdf_path = books_dir / "sample.pdf"
            _write_blank_pdf(pdf_path, 4)

            connection = connect(db_path)
            initialize(connection)
            book_id = upsert_book(
                connection,
                path=pdf_path,
                title="テスト本",
                size_bytes=pdf_path.stat().st_size,
                modified_at=pdf_path.stat().st_mtime,
            )
            replace_pages(
                connection,
                book_id=book_id,
                title="テスト本",
                pages=[
                    PageRecord(page_number=1, text="1"),
                    PageRecord(page_number=2, text="2"),
                    PageRecord(page_number=3, text="3"),
                    PageRecord(page_number=4, text="4"),
                ],
            )
            connection.commit()

            item = _pack_item(item_id=1, pdf_path="sample.pdf", pages="1-2,4")
            [result] = collect_item_stats(connection, [item], books_dir=books_dir)
            connection.close()

            self.assertEqual(result.page_numbers, [1, 2, 4])
            self.assertEqual(result.unindexed_pages, 0)
            # 3ページ目は選択範囲外なので集計に含まれない
            self.assertEqual(result.stats, count_text_stats("124"))

    def test_same_pdf_as_two_pack_items_computed_independently(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"
            db_path = root / "index.db"
            pdf_path = books_dir / "sample.pdf"
            _write_blank_pdf(pdf_path, 10)

            connection = connect(db_path)
            initialize(connection)
            book_id = upsert_book(
                connection,
                path=pdf_path,
                title="本A",
                size_bytes=pdf_path.stat().st_size,
                modified_at=pdf_path.stat().st_mtime,
            )
            replace_pages(
                connection,
                book_id=book_id,
                title="本A",
                pages=[PageRecord(page_number=n, text=f"前提説明その{n}" if n <= 5 else f"実装例その{n}") for n in range(1, 11)],
            )
            connection.commit()

            item_intro = _pack_item(item_id=1, pdf_path="sample.pdf", title="本A", pages="1-3", position=0)
            item_impl = _pack_item(item_id=2, pdf_path="sample.pdf", title="本A", pages="8-10", position=1)
            results = collect_item_stats(connection, [item_intro, item_impl], books_dir=books_dir)
            connection.close()

            self.assertEqual(len(results), 2)
            intro_result, impl_result = results
            self.assertEqual(intro_result.page_numbers, [1, 2, 3])
            self.assertEqual(impl_result.page_numbers, [8, 9, 10])
            self.assertNotEqual(intro_result.stats, impl_result.stats)

    def test_empty_page_text_yields_zero_stats_for_that_page(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"
            db_path = root / "index.db"
            pdf_path = books_dir / "sample.pdf"
            _write_blank_pdf(pdf_path, 1)

            connection = connect(db_path)
            initialize(connection)
            book_id = upsert_book(
                connection,
                path=pdf_path,
                title="白紙本",
                size_bytes=pdf_path.stat().st_size,
                modified_at=pdf_path.stat().st_mtime,
            )
            replace_pages(
                connection,
                book_id=book_id,
                title="白紙本",
                pages=[PageRecord(page_number=1, text="")],
            )
            connection.commit()

            item = _pack_item(item_id=1, pdf_path="sample.pdf", pages="1")
            [result] = collect_item_stats(connection, [item], books_dir=books_dir)
            connection.close()

            self.assertEqual(result.page_numbers, [1])
            self.assertEqual(result.unindexed_pages, 0)
            self.assertEqual(result.stats, TextStats(cjk_chars=0, other_chars=0))

    def test_mixed_japanese_and_english_text_splits_stats_by_char_class(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            books_dir = root / "books"
            db_path = root / "index.db"
            pdf_path = books_dir / "sample.pdf"
            _write_blank_pdf(pdf_path, 1)

            connection = connect(db_path)
            initialize(connection)
            book_id = upsert_book(
                connection,
                path=pdf_path,
                title="混在本",
                size_bytes=pdf_path.stat().st_size,
                modified_at=pdf_path.stat().st_mtime,
            )
            text = "PythonでのFizzBuzz実装例を解説する"
            replace_pages(
                connection,
                book_id=book_id,
                title="混在本",
                pages=[PageRecord(page_number=1, text=text)],
            )
            connection.commit()

            item = _pack_item(item_id=1, pdf_path="sample.pdf", pages="1")
            [result] = collect_item_stats(connection, [item], books_dir=books_dir)
            connection.close()

            expected = count_text_stats(text)
            self.assertEqual(result.stats, expected)
            self.assertGreater(result.stats.cjk_chars, 0)
            self.assertGreater(result.stats.other_chars, 0)


if __name__ == "__main__":
    unittest.main()
