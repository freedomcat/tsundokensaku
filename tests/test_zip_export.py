import unittest
import zipfile
from datetime import datetime
from io import BytesIO

from tsundokensaku.zip_export import (
    PackExportEntry,
    build_entry_filename,
    build_pack_zip,
    build_pack_zip_filename,
    render_pack_manifest,
    sanitize_filename_component,
)


class SanitizeFilenameComponentTest(unittest.TestCase):
    def test_replaces_symbols_and_spaces(self) -> None:
        self.assertEqual(sanitize_filename_component("コード と ログ/2冊"), "コード_と_ログ_2冊")

    def test_falls_back_to_untitled_for_empty_result(self) -> None:
        self.assertEqual(sanitize_filename_component("///"), "untitled")


class BuildPackZipFilenameTest(unittest.TestCase):
    def test_uses_pack_name_and_date(self) -> None:
        name = build_pack_zip_filename("コードとログ", datetime(2026, 7, 10, 9, 0))
        self.assertEqual(name, "コードとログ_20260710.zip")


class BuildEntryFilenameTest(unittest.TestCase):
    def test_uses_index_title_and_pages_not_source_filename(self) -> None:
        name = build_entry_filename(1, "伽藍とバザール", "1-15", "pdf")
        self.assertEqual(name, "01_伽藍とバザール_p1-15.pdf")

    def test_pads_index_to_two_digits(self) -> None:
        name = build_entry_filename(9, "本", "1", "md")
        self.assertTrue(name.startswith("09_"))

    def test_sanitizes_comma_separated_pages(self) -> None:
        name = build_entry_filename(2, "本B", "3-7,20-35", "pdf")
        self.assertEqual(name, "02_本B_p3-7_20-35.pdf")

    def test_shortens_long_page_range_to_page_count_when_over_byte_limit(self) -> None:
        # 飛び番ページを大量指定 → フルのページ範囲表記だと255バイトを超える
        pages = ",".join(str(n) for n in range(1, 2000, 5))  # 400個
        name = build_entry_filename(1, "Programming_Ruby", pages, "pdf")
        self.assertEqual(name, "01_Programming_Ruby_400ページ.pdf")
        self.assertLessEqual(len(name.encode("utf-8")), 255)

    def test_truncates_long_title_with_ellipsis_when_still_over_limit(self) -> None:
        long_title = "非常に長い書籍タイトル" * 20
        pages = ",".join(str(n) for n in range(1, 2000, 5))
        name = build_entry_filename(1, long_title, pages, "pdf")
        self.assertTrue(name.startswith("01_"))
        self.assertIn("…", name)
        self.assertTrue(name.endswith("400ページ.pdf"))
        self.assertLessEqual(len(name.encode("utf-8")), 255)

    def test_index_prefix_always_preserved_even_when_truncated(self) -> None:
        long_title = "書籍" * 100
        name = build_entry_filename(7, long_title, "1-500", "pdf")
        self.assertTrue(name.startswith("07_"))
        self.assertLessEqual(len(name.encode("utf-8")), 255)

    def test_short_filename_is_unaffected_by_truncation_logic(self) -> None:
        name = build_entry_filename(1, "短い本", "1-5", "pdf")
        self.assertEqual(name, "01_短い本_p1-5.pdf")


class RenderPackManifestTest(unittest.TestCase):
    def test_lists_entries_in_order_with_notebooklm_note(self) -> None:
        entries = [
            PackExportEntry(index=1, title="伽藍とバザール", page_label="1-15", filename="01_cathedral_p1-15.pdf", content=b""),
            PackExportEntry(index=2, title="ノウアスフィアの開墾", page_label="3-7,20-35", filename="02_noosphere_p3-7_20-35.pdf", content=b""),
        ]
        manifest = render_pack_manifest(
            pack_name="コードとログ",
            exported_at=datetime(2026, 7, 10, 9, 30),
            entries=entries,
        )

        self.assertIn("# コードとログ（資料一式）", manifest)
        self.assertIn("- 書き出し日時: 2026-07-10 09:30", manifest)
        self.assertIn("- 収録: 2冊", manifest)
        self.assertIn("1. 伽藍とバザール — p.1-15 （01_cathedral_p1-15.pdf）", manifest)
        self.assertIn("2. ノウアスフィアの開墾 — p.3-7,20-35 （02_noosphere_p3-7_20-35.pdf）", manifest)
        self.assertIn("2個のファイルがそれぞれ1ソースになります", manifest)


class BuildPackZipTest(unittest.TestCase):
    def test_zip_contains_manifest_and_entries_in_order(self) -> None:
        entries = [
            PackExportEntry(index=1, title="本A", page_label="1-2", filename="01_a_p1-2.pdf", content=b"PDF-A"),
            PackExportEntry(index=2, title="本B", page_label="5", filename="02_b_p5.pdf", content=b"PDF-B"),
        ]
        zip_bytes = build_pack_zip(
            pack_name="テスト資料",
            entries=entries,
            exported_at=datetime(2026, 7, 10, 9, 0),
        )

        with zipfile.ZipFile(BytesIO(zip_bytes)) as archive:
            names = archive.namelist()
            self.assertEqual(names, ["manifest.md", "01_a_p1-2.pdf", "02_b_p5.pdf"])
            self.assertIn("テスト資料（資料一式）", archive.read("manifest.md").decode("utf-8"))
            self.assertEqual(archive.read("01_a_p1-2.pdf"), b"PDF-A")
            self.assertEqual(archive.read("02_b_p5.pdf"), b"PDF-B")


if __name__ == "__main__":
    unittest.main()
