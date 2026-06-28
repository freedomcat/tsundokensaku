import json
import tempfile
import unittest
from pathlib import Path

from tsundokensaku.metadata import load_metadata_by_pdf_stem, metadata_for_pdf


class MetadataTest(unittest.TestCase):
    def test_loads_scrapbox_url_by_imported_pdf_stem(self) -> None:
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
            item = metadata_for_pdf("/books/tech/4274217884_テスト駆動開発 Kent Beck.pdf", metadata)

            self.assertIsNotNone(item)
            self.assertEqual(item.title, "テスト駆動開発 Kent Beck")
            self.assertEqual(
                item.scrapbox_url,
                "https://scrapbox.io/shino-books/%E3%83%86%E3%82%B9%E3%83%88%E9%A7%86%E5%8B%95%E9%96%8B%E7%99%BA%20Kent%20Beck",
            )
            self.assertNotIn("4000000000_技術書ではない本", metadata)


if __name__ == "__main__":
    unittest.main()
