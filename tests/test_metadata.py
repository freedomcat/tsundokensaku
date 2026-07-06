import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter

from tsundokensaku.metadata import (
    BookMetadata,
    extract_bookscan_filename_title,
    extract_id_prefix_filename_title,
    extract_structured_filename_title,
    load_kindle_books,
    load_metadata_by_pdf_stem,
    load_scrapbox_memos,
    metadata_for_pdf,
    resolve_pdf_display_title,
)


def _write_blank_pdf(path: Path, *, title: str | None = None) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    if title is not None:
        writer.add_metadata({"/Title": title})
    with path.open("wb") as handle:
        writer.write(handle)


def _write_cover_pdf(path: Path, title: str, *, metadata_title: str | None = None) -> None:
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=400, height=600)
    page.insert_text((50, 120), title, fontsize=28)
    page.insert_text((50, 180), "著者名", fontsize=12)
    if metadata_title is not None:
        doc.set_metadata({"title": metadata_title})
    doc.save(str(path))
    doc.close()


def _write_outline_pdf(path: Path, outline_title: str) -> None:
    import fitz

    doc = fitz.open()
    doc.new_page(width=300, height=300)
    doc.set_toc([[1, outline_title, 1]])
    doc.save(str(path))
    doc.close()


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

    def test_resolve_pdf_display_title_prefers_scrapbox_then_pdf_metadata_then_filename(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf_with_metadata = root / "metadata.pdf"
            pdf_with_both = root / "scrapbox.pdf"
            pdf_fallback = root / "Programming_Ruby_5th_ja.pdf"

            _write_blank_pdf(pdf_with_metadata, title="PDF Metadata Title")
            _write_blank_pdf(pdf_with_both, title="PDF Metadata Title")
            _write_blank_pdf(pdf_fallback)

            metadata_by_stem = {
                "scrapbox": BookMetadata(title="Scrapbox Title"),
            }

            self.assertEqual(resolve_pdf_display_title(pdf_with_metadata, metadata_by_stem), "PDF Metadata Title")
            self.assertEqual(resolve_pdf_display_title(pdf_with_both, metadata_by_stem), "Scrapbox Title")
            self.assertEqual(resolve_pdf_display_title(pdf_fallback, {}), "Programming Ruby 5th ja")

    def test_resolve_pdf_display_title_prefers_metadata_over_cover(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "cover.pdf"
            _write_cover_pdf(pdf_path, "Cover Title", metadata_title="Metadata Title")

            self.assertEqual(resolve_pdf_display_title(pdf_path, {}), "Metadata Title")

    def test_resolve_pdf_display_title_falls_back_from_bad_metadata_to_cover(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "cover.pdf"
            _write_cover_pdf(pdf_path, "Readable Cover Title", metadata_title="C:/Temp/source.dvi")

            self.assertEqual(resolve_pdf_display_title(pdf_path, {}), "Readable Cover Title")

    def test_resolve_pdf_display_title_reads_cover_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "cover.pdf"
            _write_cover_pdf(pdf_path, "Natural Book Title")

            self.assertEqual(resolve_pdf_display_title(pdf_path, {}), "Natural Book Title")

    def test_resolve_pdf_display_title_falls_back_from_empty_cover_to_outline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "outline.pdf"
            _write_outline_pdf(pdf_path, "Outline Book Title")

            self.assertEqual(resolve_pdf_display_title(pdf_path, {}), "Outline Book Title")

    def test_resolve_pdf_display_title_uses_scrapbox_after_pdf_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "scrapbox.pdf"
            _write_blank_pdf(pdf_path)

            metadata_by_stem = {"scrapbox": BookMetadata(title="Scrapbox Title")}

            self.assertEqual(resolve_pdf_display_title(pdf_path, metadata_by_stem), "Scrapbox Title")

    def test_resolve_pdf_display_title_prefers_scrapbox_over_cover(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "scrapbox.pdf"
            _write_cover_pdf(pdf_path, "Cover Title")

            metadata_by_stem = {"scrapbox": BookMetadata(title="Scrapbox Title")}

            self.assertEqual(resolve_pdf_display_title(pdf_path, metadata_by_stem), "Scrapbox Title")

    def test_resolve_pdf_display_title_extracts_title_from_bookscan_filename(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "More Effective C# 6．0／7．0 Bill Wagner 320p_4798153982.pdf"
            _write_blank_pdf(pdf_path)

            self.assertEqual(resolve_pdf_display_title(pdf_path, {}), "More Effective C# 6．0／7．0")

    def test_resolve_pdf_display_title_prefers_bookscan_filename_over_cover(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "Real Book Title 著者 320p_4798153982.pdf"
            _write_cover_pdf(pdf_path, "Cover Title")

            self.assertEqual(resolve_pdf_display_title(pdf_path, {}), "Real Book Title 著者")

    def test_resolve_pdf_display_title_rejects_noise_metadata_title(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            for noise in ("- Ⅱ1幅 -", "オム", "I7t"):
                pdf_path = Path(temp_dir) / f"CODE_COMPLETE_{len(noise)}.pdf"
                _write_blank_pdf(pdf_path, title=noise)

                self.assertEqual(
                    resolve_pdf_display_title(pdf_path, {}),
                    f"CODE COMPLETE {len(noise)}",
                )

    def test_extract_bookscan_filename_title_strips_author_tokens(self) -> None:
        cases = {
            "More Effective C# 6．0／7．0 Bill Wagner 320p_4798153982.pdf": "More Effective C# 6．0／7．0",
            "オープンソースの成功-政治学者が分析するコミュニティの可能性 スティーブン ウェバー 354p_4839916586.pdf": "オープンソースの成功-政治学者が分析するコミュニティの可能性",
            "チームが機能するとはどういうことか──「学習力」と「実行力」を高める実践アプローチ エイミー・C・エドモンドソン 392p_4862761828.pdf": "チームが機能するとはどういうことか──「学習力」と「実行力」を高める実践アプローチ",
        }
        for filename, expected in cases.items():
            self.assertEqual(extract_bookscan_filename_title(filename), expected)

    def test_extract_bookscan_filename_title_keeps_ambiguous_body(self) -> None:
        # Title and author are both katakana/Latin words: cannot split safely.
        self.assertEqual(
            extract_bookscan_filename_title("リーダブルコード Dustin Boswell 260p_4873115655.pdf"),
            "リーダブルコード Dustin Boswell",
        )

    def test_extract_bookscan_filename_title_ignores_non_bookscan_names(self) -> None:
        self.assertIsNone(extract_bookscan_filename_title("cathedral.pdf"))
        self.assertIsNone(extract_bookscan_filename_title("Programming_Ruby_5th_ja.pdf"))
        self.assertIsNone(extract_bookscan_filename_title("4798153982_More Effective C#.pdf"))

    def test_extract_id_prefix_filename_title(self) -> None:
        cases = {
            "9784798153988_More Effective C#.pdf": "More Effective C#",
            "4274217884_テスト駆動開発 Kent Beck.pdf": "テスト駆動開発 Kent Beck",
            "B08XYZ1234_プログラマー脳.pdf": "プログラマー脳",
        }
        for filename, expected in cases.items():
            self.assertEqual(extract_id_prefix_filename_title(filename), expected)

    def test_extract_id_prefix_filename_title_ignores_other_names(self) -> None:
        self.assertIsNone(extract_id_prefix_filename_title("cathedral.pdf"))
        self.assertIsNone(extract_id_prefix_filename_title("More Effective C# Bill Wagner 320p_4798153982.pdf"))
        self.assertIsNone(extract_id_prefix_filename_title("12345_短すぎるID.pdf"))

    def test_extract_structured_filename_title_tries_parsers_in_order(self) -> None:
        self.assertEqual(
            extract_structured_filename_title("More Effective C# Bill Wagner 320p_4798153982.pdf"),
            "More Effective C#",
        )
        self.assertEqual(
            extract_structured_filename_title("4798153982_More Effective C#.pdf"),
            "More Effective C#",
        )
        self.assertIsNone(extract_structured_filename_title("cathedral.pdf"))

    def test_resolve_pdf_display_title_extracts_title_from_id_prefix_filename(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "4798153982_More Effective C#.pdf"
            _write_blank_pdf(pdf_path)

            self.assertEqual(resolve_pdf_display_title(pdf_path, {}), "More Effective C#")

    def test_resolve_pdf_display_title_uses_filename_when_all_candidates_are_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "Programming_Ruby_5th_ja.pdf"
            _write_blank_pdf(pdf_path)

            self.assertEqual(resolve_pdf_display_title(pdf_path, {}), "Programming Ruby 5th ja")

    def test_resolve_pdf_display_title_ignores_source_file_metadata_title(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "魔法のおなべ.pdf"

            writer = PdfWriter()
            writer.add_blank_page(width=72, height=72)
            writer.add_metadata({"/Title": "C:/Temp/magicpot.dvi"})
            with pdf_path.open("wb") as handle:
                writer.write(handle)

            self.assertEqual(resolve_pdf_display_title(pdf_path, {}), "魔法のおなべ")


if __name__ == "__main__":
    unittest.main()
