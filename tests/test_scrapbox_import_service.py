import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tsundokensaku import database
from tsundokensaku.scrapbox_import_service import (
    ScrapboxExportNotFoundError,
    ScrapboxImportResult,
    import_scrapbox_export_bytes,
    import_scrapbox_export_file,
)


def _payload_bytes() -> bytes:
    payload = {
        "pages": [
            {
                "title": "メモ1",
                "lines": [{"text": "検索対象のメモ本文"}],
            },
            {
                "title": "Kindle Book",
                "lines": [
                    {"text": "#Kindle #技術書"},
                    {"text": "https://read.amazon.co.jp/?asin=B012345678"},
                ],
            },
        ]
    }
    return json.dumps(payload).encode("utf-8")


class ScrapboxImportServiceTest(unittest.TestCase):
    def test_bytes_are_saved_to_cache_and_result_counts_are_returned(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache_path = root / "scrapbox.json"
            db_path = root / "index.db"
            content = _payload_bytes()

            result = import_scrapbox_export_bytes(content, cache_path=cache_path, db_path=db_path)

            self.assertEqual(result, ScrapboxImportResult(imported_memos=2, imported_kindle_books=1))
            self.assertEqual(cache_path.read_bytes(), content)

    def test_file_import_copies_different_source_to_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.json"
            cache_path = root / "cache.json"
            db_path = root / "index.db"
            source.write_bytes(_payload_bytes())

            result = import_scrapbox_export_file(source, cache_path=cache_path, db_path=db_path)

            self.assertEqual(result, ScrapboxImportResult(imported_memos=2, imported_kindle_books=1))
            self.assertEqual(cache_path.read_bytes(), source.read_bytes())

    def test_file_import_does_not_rewrite_when_source_is_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache_path = root / "cache.json"
            db_path = root / "index.db"
            cache_path.write_bytes(_payload_bytes())
            before_mtime = cache_path.stat().st_mtime_ns

            result = import_scrapbox_export_file(cache_path, cache_path=cache_path, db_path=db_path)

            self.assertEqual(result, ScrapboxImportResult(imported_memos=2, imported_kindle_books=1))
            self.assertEqual(cache_path.stat().st_mtime_ns, before_mtime)

    def test_file_import_none_source_raises_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(ScrapboxExportNotFoundError):
                import_scrapbox_export_file(
                    None,
                    cache_path=Path(temp_dir) / "cache.json",
                    db_path=Path(temp_dir) / "index.db",
                )

    def test_file_import_missing_source_raises_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaises(ScrapboxExportNotFoundError):
                import_scrapbox_export_file(
                    root / "missing.json",
                    cache_path=root / "cache.json",
                    db_path=root / "index.db",
                )

    def test_missing_source_does_not_connect_or_change_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache_path = root / "cache.json"
            cache_path.write_bytes(b"existing")

            with patch("tsundokensaku.scrapbox_import_service.database.connect") as connect_mock, \
                    self.assertRaises(ScrapboxExportNotFoundError):
                import_scrapbox_export_file(
                    root / "missing.json",
                    cache_path=cache_path,
                    db_path=root / "index.db",
                )

            connect_mock.assert_not_called()
            self.assertEqual(cache_path.read_bytes(), b"existing")

    def test_invalid_utf8_propagates_and_content_remains_in_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache_path = root / "cache.json"
            content = b"\xff"

            with self.assertRaises(UnicodeDecodeError):
                import_scrapbox_export_bytes(content, cache_path=cache_path, db_path=root / "index.db")

            self.assertEqual(cache_path.read_bytes(), content)

    def test_invalid_json_propagates_and_content_remains_in_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache_path = root / "cache.json"
            content = b"{bad json"

            with self.assertRaises(json.JSONDecodeError):
                import_scrapbox_export_bytes(content, cache_path=cache_path, db_path=root / "index.db")

            self.assertEqual(cache_path.read_bytes(), content)

    def test_unexpected_top_level_parser_exception_propagates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache_path = root / "cache.json"
            content = b"[]"

            with self.assertRaises(AttributeError):
                import_scrapbox_export_bytes(content, cache_path=cache_path, db_path=root / "index.db")

            self.assertEqual(cache_path.read_bytes(), content)

    def test_sync_kindle_failure_keeps_prior_cache_and_memo_update(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache_path = root / "cache.json"
            db_path = root / "index.db"

            with patch(
                "tsundokensaku.scrapbox_import_service.database.sync_kindle_books",
                side_effect=RuntimeError("kindle failed"),
            ), self.assertRaisesRegex(RuntimeError, "kindle failed"):
                import_scrapbox_export_bytes(_payload_bytes(), cache_path=cache_path, db_path=db_path)

            self.assertTrue(cache_path.exists())
            connection = database.connect(db_path)
            try:
                memo_count = connection.execute("SELECT COUNT(*) FROM memos").fetchone()[0]
                self.assertEqual(memo_count, 2)
            finally:
                connection.close()

    def test_connection_is_closed_when_sync_raises(self) -> None:
        connection = Mock()
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch("tsundokensaku.scrapbox_import_service.database.connect", return_value=connection), \
                patch("tsundokensaku.scrapbox_import_service.database.initialize"), \
                patch(
                    "tsundokensaku.scrapbox_import_service.database.sync_memos",
                    side_effect=RuntimeError("sync failed"),
                ), \
                self.assertRaisesRegex(RuntimeError, "sync failed"):
            import_scrapbox_export_bytes(
                b'{"pages":[]}',
                cache_path=Path(temp_dir) / "cache.json",
                db_path=Path(temp_dir) / "index.db",
            )

        connection.close.assert_called_once_with()

    def test_sync_order_is_memos_then_kindle_books(self) -> None:
        connection = Mock()
        calls: list[str] = []

        def sync_memos(*args: object, **kwargs: object) -> int:
            calls.append("memos")
            return 7

        def sync_kindle_books(*args: object, **kwargs: object) -> int:
            calls.append("kindle")
            return 5

        with tempfile.TemporaryDirectory() as temp_dir, \
                patch("tsundokensaku.scrapbox_import_service.database.connect", return_value=connection), \
                patch("tsundokensaku.scrapbox_import_service.database.initialize"), \
                patch("tsundokensaku.scrapbox_import_service.database.sync_memos", side_effect=sync_memos), \
                patch("tsundokensaku.scrapbox_import_service.database.sync_kindle_books", side_effect=sync_kindle_books):
            result = import_scrapbox_export_bytes(
                b'{"pages":[]}',
                cache_path=Path(temp_dir) / "cache.json",
                db_path=Path(temp_dir) / "index.db",
            )

        self.assertEqual(calls, ["memos", "kindle"])
        self.assertEqual(result, ScrapboxImportResult(imported_memos=7, imported_kindle_books=5))
        connection.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
