import unittest
import zipfile
from datetime import datetime
from io import BytesIO

from tsundokensaku.zip_export import (
    MAX_FILENAME_BYTES,
    PackExportEntry,
    PlanManifestChunk,
    PlanManifestFragment,
    build_chunk_filename,
    build_entry_filename,
    build_pack_zip,
    build_pack_zip_filename,
    build_pack_zip_with_manifest,
    build_sequenced_filename,
    render_pack_manifest,
    render_plan_manifest,
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


class BuildSequencedFilenameTest(unittest.TestCase):
    def test_combines_pack_name_profile_and_index(self) -> None:
        name = build_sequenced_filename("コードとログ", "chat", 1, "md")
        self.assertEqual(name, "コードとログ_chat_01.md")

    def test_pads_index_to_two_digits(self) -> None:
        name = build_sequenced_filename("資料", "chat", 9, "md")
        self.assertTrue(name.startswith("資料_chat_09"))

    def test_index_can_exceed_two_digits(self) -> None:
        name = build_sequenced_filename("資料", "chat", 123, "md")
        self.assertEqual(name, "資料_chat_123.md")

    def test_shortens_long_pack_name_when_over_byte_limit(self) -> None:
        long_name = "非常に長い資料名" * 40
        name = build_sequenced_filename(long_name, "chat", 1, "md")
        self.assertTrue(name.endswith("_chat_01.md"))
        self.assertLessEqual(len(name.encode("utf-8")), MAX_FILENAME_BYTES)

    def test_short_name_is_unaffected_by_truncation_logic(self) -> None:
        name = build_sequenced_filename("短い資料", "chat", 1, "md")
        self.assertEqual(name, "短い資料_chat_01.md")


class BuildChunkFilenameTest(unittest.TestCase):
    def test_uses_title_and_pages_for_single_fragment(self) -> None:
        self.assertEqual(build_chunk_filename(1, "本A", ["1-20"]), "01_本A_p1-20.pdf")

    def test_uses_label_when_single_fragment_has_label(self) -> None:
        self.assertEqual(build_chunk_filename(1, "本A", ["1-20"], label="第1章"), "01_本A_第1章_p1-20.pdf")

    def test_uses_joined_ranges_for_multiple_fragments(self) -> None:
        self.assertEqual(build_chunk_filename(3, "本A", ["1-10", "5-8"]), "03_本A_p1-10_5-8.pdf")

    def test_sanitizes_label_and_page_ranges(self) -> None:
        self.assertEqual(build_chunk_filename(1, "本A", ["3-7,20-35"], label="第1章/導入"), "01_本A_第1章_導入_p3-7_20-35.pdf")

    def test_shortens_long_name_to_fit_255_bytes(self) -> None:
        name = build_chunk_filename(1, "非常に長い書名" * 30, ["1-300"], label="非常に長い章名" * 30)
        self.assertLessEqual(len(name.encode("utf-8")), MAX_FILENAME_BYTES)
        self.assertTrue(name.startswith("01_"))
        self.assertTrue(name.endswith(".pdf"))


class RenderPlanManifestTest(unittest.TestCase):
    def test_lists_chunks_with_item_breakdown(self) -> None:
        manifest = render_plan_manifest(
            pack_name="コードとログ",
            exported_at=datetime(2026, 7, 12, 9, 30),
            profile_name="chat",
            chunks=[
                ("コードとログ_chat_01.md", [("伽藍とバザール", "1-15"), ("ノウアスフィアの開墾", "3-7")]),
                ("コードとログ_chat_02.md", [("本C", "1-100")]),
            ],
            header_lines=[],
            warnings=[],
        )

        self.assertIn("# コードとログ（資料一式・chat）", manifest)
        self.assertIn("- プロファイル: chat", manifest)
        self.assertIn("- 出力ファイル数: 2", manifest)
        self.assertIn("1. コードとログ_chat_01.md", manifest)
        self.assertIn("   - 伽藍とバザール — p.1-15", manifest)
        self.assertIn("   - ノウアスフィアの開墾 — p.3-7", manifest)
        self.assertIn("2. コードとログ_chat_02.md", manifest)
        self.assertIn("   - 本C — p.1-100", manifest)

    def test_includes_header_lines_and_warnings(self) -> None:
        manifest = render_plan_manifest(
            pack_name="資料",
            exported_at=datetime(2026, 7, 12, 9, 30),
            profile_name="chat",
            chunks=[("資料_chat_01.md", [("巨大本", "1-1000")])],
            header_lines=["- 備考: テスト用のヘッダ行"],
            warnings=["「巨大本」は1ファイルの上限を超えるため単独で出力します"],
        )

        self.assertIn("- 備考: テスト用のヘッダ行", manifest)
        self.assertIn("## 警告", manifest)
        self.assertIn("「巨大本」は1ファイルの上限を超えるため単独で出力します", manifest)

    def test_no_warnings_section_when_warnings_empty(self) -> None:
        manifest = render_plan_manifest(
            pack_name="資料",
            exported_at=datetime(2026, 7, 12, 9, 30),
            profile_name="chat",
            chunks=[("資料_chat_01.md", [("本A", "1-1")])],
            header_lines=[],
            warnings=[],
        )
        self.assertNotIn("## 警告", manifest)

    def test_renders_notebooklm_single_fragment_hierarchy(self) -> None:
        manifest = render_plan_manifest(
            pack_name="資料",
            exported_at=datetime(2026, 7, 12, 9, 30),
            profile_name="notebooklm",
            chunks=[
                PlanManifestChunk(
                    filename="01_本A_第1章_p1-20.pdf",
                    fragments=[PlanManifestFragment(title="本A", pages="1-20", label="第1章")],
                )
            ],
            header_lines=[],
            warnings=[],
        )

        self.assertIn("1. 01_本A_第1章_p1-20.pdf", manifest)
        self.assertIn("   - 本A", manifest)
        self.assertIn("     - 第1章 — p.1-20", manifest)

    def test_renders_notebooklm_multiple_fragment_hierarchy(self) -> None:
        manifest = render_plan_manifest(
            pack_name="資料",
            exported_at=datetime(2026, 7, 12, 9, 30),
            profile_name="notebooklm",
            chunks=[
                PlanManifestChunk(
                    filename="02_本A_p21-30_31-40.pdf",
                    fragments=[
                        PlanManifestFragment(title="本A", pages="21-30", label="第2章"),
                        PlanManifestFragment(title="本A", pages="31-40", label="第3章"),
                    ],
                )
            ],
            header_lines=[],
            warnings=[],
        )

        self.assertIn("1. 02_本A_p21-30_31-40.pdf", manifest)
        self.assertIn("   - 本A", manifest)
        self.assertIn("     - 第2章 — p.21-30", manifest)
        self.assertIn("     - 第3章 — p.31-40", manifest)

    def test_renders_notebooklm_fragment_without_label_with_pages_only(self) -> None:
        manifest = render_plan_manifest(
            pack_name="資料",
            exported_at=datetime(2026, 7, 12, 9, 30),
            profile_name="notebooklm",
            chunks=[
                PlanManifestChunk(
                    filename="01_本A_p1-20.pdf",
                    fragments=[PlanManifestFragment(title="本A", pages="1-20", label=None)],
                )
            ],
            header_lines=[],
            warnings=[],
        )

        self.assertIn("   - 本A", manifest)
        self.assertIn("     - p.1-20", manifest)


class BuildPackZipWithManifestTest(unittest.TestCase):
    def test_zip_contains_given_manifest_and_entries_in_order(self) -> None:
        entries = [
            PackExportEntry(index=1, title="本A", page_label="1-2", filename="資料_chat_01.md", content=b"MD-A"),
            PackExportEntry(index=2, title="本B", page_label="5", filename="資料_chat_02.md", content=b"MD-B"),
        ]
        zip_bytes = build_pack_zip_with_manifest(entries=entries, manifest="# カスタムmanifest\n")

        with zipfile.ZipFile(BytesIO(zip_bytes)) as archive:
            names = archive.namelist()
            self.assertEqual(names, ["manifest.md", "資料_chat_01.md", "資料_chat_02.md"])
            self.assertEqual(archive.read("manifest.md").decode("utf-8"), "# カスタムmanifest\n")
            self.assertEqual(archive.read("資料_chat_01.md"), b"MD-A")
            self.assertEqual(archive.read("資料_chat_02.md"), b"MD-B")


if __name__ == "__main__":
    unittest.main()
