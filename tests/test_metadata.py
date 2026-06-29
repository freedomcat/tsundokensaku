import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tsundokensaku.metadata import load_kindle_books, load_metadata_by_pdf_stem, load_scrapbox_memos, metadata_for_pdf


class MetadataTest(unittest.TestCase):
    def test_does_not_create_scrapbox_url_without_base_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            export_json = Path(temp_dir) / "shino-books_20260628_000000.json"
            export_json.write_text(
                json.dumps(
                    {
                        "pages": [
                            {
                                "title": "テスト駆動開発 Kent Beck",
                                "lines": [
                                    {"text": "テスト駆動開発 Kent Beck"},
                                    {"text": "[https://images-na.ssl-images-amazon.com/images/P/4274217884.09.MZZZZZZZ.jpg https://www.amazon.co.jp/dp/4274217884]"},
                                    {"text": "#Bookscan #技術書"},
                                    {"text": "https://system.bookscan.co.jp/sample?f=book_4274217884.pdf"},
                                ],
                            },
                            {
                                "title": "技術書ではない本",
                                "lines": [
                                    {"text": "技術書ではない本"},
                                    {"text": "#Bookscan"},
                                    {"text": "https://system.bookscan.co.jp/sample?f=book_4000000000.pdf"},
                                ],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with patch.dict("os.environ", {}, clear=True):
                metadata = load_metadata_by_pdf_stem(export_json)

            item = metadata_for_pdf("/books/tech/4274217884_テスト駆動開発 Kent Beck.pdf", metadata)
            self.assertIsNotNone(item)
            self.assertIsNone(item.scrapbox_url)
            self.assertEqual(item.cover_url, "https://images-na.ssl-images-amazon.com/images/P/4274217884.09.MZZZZZZZ.jpg")

    def test_scrapbox_base_url_can_be_overridden(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            export_json = Path(temp_dir) / "shino-books_20260628_000000.json"
            export_json.write_text(
                json.dumps(
                    {
                        "pages": [
                            {
                                "title": "テスト駆動開発 Kent Beck",
                                "lines": [
                                    {"text": "テスト駆動開発 Kent Beck"},
                                    {"text": "[https://images-na.ssl-images-amazon.com/images/P/4274217884.09.MZZZZZZZ.jpg https://www.amazon.co.jp/dp/4274217884]"},
                                    {"text": "#Bookscan #技術書"},
                                    {"text": "https://system.bookscan.co.jp/sample?f=book_4274217884.pdf"},
                                ],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with patch.dict("os.environ", {"SCRAPBOX_BASE_URL": "https://scrapbox.io/custom-project"}, clear=False):
                metadata = load_metadata_by_pdf_stem(export_json)

            item = metadata_for_pdf("/books/tech/4274217884_テスト駆動開発 Kent Beck.pdf", metadata)
            self.assertIsNotNone(item)
            self.assertEqual(
                item.scrapbox_url,
                "https://scrapbox.io/custom-project/%E3%83%86%E3%82%B9%E3%83%88%E9%A7%86%E5%8B%95%E9%96%8B%E7%99%BA%20Kent%20Beck",
            )
            self.assertEqual(item.cover_url, "https://images-na.ssl-images-amazon.com/images/P/4274217884.09.MZZZZZZZ.jpg")

    def test_loads_scrapbox_memos_from_export(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            export_json = Path(temp_dir) / "shino-books_20260628_000000.json"
            export_json.write_text(
                json.dumps(
                    {
                        "pages": [
                            {
                                "title": "メモ1",
                                "lines": [
                                    {"text": "メモ1"},
                                    {"text": "検索対象のメモ本文"},
                                    {"text": "[https://example.com/cover.jpg]"},
                                ],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with patch.dict("os.environ", {"SCRAPBOX_BASE_URL": "https://scrapbox.io/custom-project"}, clear=False):
                memos = load_scrapbox_memos(export_json)

            self.assertEqual(len(memos), 1)
            self.assertEqual(memos[0].title, "メモ1")
            self.assertEqual(memos[0].body, "メモ1\n検索対象のメモ本文\n[https://example.com/cover.jpg]")
            self.assertEqual(memos[0].scrapbox_url, "https://scrapbox.io/custom-project/%E3%83%A1%E3%83%A21")
            self.assertEqual(memos[0].cover_url, "https://example.com/cover.jpg")

    def test_loads_kindle_books_from_export(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            export_json = Path(temp_dir) / "shino-books_20260628_000000.json"
            export_json.write_text(
                json.dumps(
                    {
                        "pages": [
                            {
                                "title": "JavaScript: The Definitive Guide",
                                "lines": [
                                    {
                                        "text": "[https://m.media-amazon.com/images/I/cover.jpg https://www.amazon.co.jp/dp/B004XQX4K0]"
                                    },
                                    {"text": "[https://read.amazon.co.jp/?asin=B004XQX4K0 Kindleで開く]"},
                                    {"text": "#Kindle #David_Flanagan"},
                                    {"text": "#技術書"},
                                ],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with patch.dict("os.environ", {"SCRAPBOX_BASE_URL": "https://scrapbox.io/custom-project"}, clear=False):
                books = load_kindle_books(export_json)

            self.assertEqual(len(books), 1)
            self.assertEqual(books[0].title, "JavaScript: The Definitive Guide")
            self.assertEqual(books[0].external_id, "B004XQX4K0")
            self.assertEqual(books[0].kindle_url, "https://read.amazon.co.jp/?asin=B004XQX4K0")
            self.assertEqual(books[0].amazon_url, "https://www.amazon.co.jp/dp/B004XQX4K0")
            self.assertEqual(books[0].scrapbox_url, "https://scrapbox.io/custom-project/JavaScript%3A%20The%20Definitive%20Guide")
            self.assertEqual(books[0].cover_url, "https://m.media-amazon.com/images/I/cover.jpg")


if __name__ == "__main__":
    unittest.main()
