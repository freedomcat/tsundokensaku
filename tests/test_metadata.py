import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tsundokensaku.metadata import load_metadata_by_pdf_stem, metadata_for_pdf


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

            metadata = load_metadata_by_pdf_stem(export_json)
            self.assertEqual(metadata, {})

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


if __name__ == "__main__":
    unittest.main()
