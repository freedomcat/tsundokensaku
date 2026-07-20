import unittest
from datetime import datetime
from pathlib import Path

from tsundokensaku.markdown_export import (
    default_markdown_output_name,
    page_selection_label,
    render_chat_chunk_header,
    render_markdown_pages,
)


class MarkdownExportTest(unittest.TestCase):
    def test_page_selection_label_joins_ranges(self) -> None:
        self.assertEqual(page_selection_label([3, 4, 5, 20, 21]), "3-5, 20-21")

    def test_default_markdown_output_name_matches_pdf_naming(self) -> None:
        name = default_markdown_output_name(Path("/tmp/book.pdf"), [3, 4, 5, 20])
        self.assertEqual(name, "book_p3-5_20.md")

    def test_render_markdown_pages_includes_source_header_and_pages(self) -> None:
        content = render_markdown_pages(
            title="テスト本",
            source_name="test.pdf",
            page_numbers=[3, 4],
            texts={3: "3ページ目の本文", 4: "4ページ目の本文"},
            exported_at=datetime(2026, 7, 6, 12, 0),
        )

        self.assertIn("# テスト本（抜粋）", content)
        self.assertIn("- 出典: テスト本", content)
        self.assertIn("- 元ファイル: test.pdf", content)
        self.assertIn("- ページ: 3-4", content)
        self.assertIn("- 抽出日: 2026-07-06", content)
        self.assertIn("## p.3", content)
        self.assertIn("3ページ目の本文", content)
        self.assertIn("## p.4", content)
        self.assertIn("4ページ目の本文", content)

    def test_render_markdown_pages_marks_empty_pages(self) -> None:
        content = render_markdown_pages(
            title="テスト本",
            source_name="test.pdf",
            page_numbers=[1],
            texts={},
            exported_at=datetime(2026, 7, 6, 12, 0),
        )

        self.assertIn("## p.1", content)
        self.assertIn("（このページから抽出できたテキストはありません）", content)

    def test_render_chat_chunk_header(self) -> None:
        header = render_chat_chunk_header(
            pack_name="テスト資料",
            chunk_index=1,
            total_chunks=3,
            items=[("本A", "1-10"), ("本B", "5-8")]
        )
        self.assertIn("# テスト資料（分冊 1/3）", header)
        self.assertIn("## 収録項目", header)
        self.assertIn("- 本A (1-10)", header)
        self.assertIn("- 本B (5-8)", header)
        self.assertIn("---", header)


if __name__ == "__main__":
    unittest.main()
