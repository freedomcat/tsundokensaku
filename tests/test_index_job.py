import unittest
from pathlib import Path
from unittest.mock import patch

import tsundokensaku.index_job as index_job


class IndexJobCharacterizationTest(unittest.TestCase):
    INITIAL_PROGRESS = {
        "running": False,
        "current": 0,
        "total": 0,
        "title": "",
        "message": "",
        "updated_at": "",
    }
    IMPORTED_PROGRESS = index_job.get_progress()

    def setUp(self) -> None:
        self._saved_progress = index_job.get_progress()
        self._replace_progress(self.INITIAL_PROGRESS)

    def tearDown(self) -> None:
        self._replace_progress(self._saved_progress)

    def _replace_progress(self, progress: dict[str, object]) -> None:
        with index_job.INDEX_PROGRESS_LOCK:
            index_job.INDEX_PROGRESS.clear()
            index_job.INDEX_PROGRESS.update(progress)

    def test_index_progress_initial_state(self) -> None:
        self.assertEqual(self.IMPORTED_PROGRESS, self.INITIAL_PROGRESS)

    def test_get_index_progress_returns_independent_snapshot(self) -> None:
        self._replace_progress(
            {
                "running": True,
                "current": 2,
                "total": 5,
                "title": "現在の本",
                "message": "INDEX 現在の本",
                "updated_at": "",
            }
        )

        snapshot = index_job.get_progress()

        self.assertIsNot(snapshot, index_job.INDEX_PROGRESS)
        self.assertEqual(snapshot, index_job.INDEX_PROGRESS)
        snapshot["message"] = "変更済み"
        self.assertEqual(index_job.INDEX_PROGRESS["message"], "INDEX 現在の本")

    def test_set_index_progress_updates_shared_dict_in_place(self) -> None:
        shared_progress = index_job.INDEX_PROGRESS
        self._replace_progress(
            {
                "running": False,
                "current": 1,
                "total": 4,
                "title": "以前の本",
                "message": "以前のメッセージ",
                "updated_at": "",
            }
        )

        index_job._set_index_progress(True, 2, 4)

        self.assertIs(index_job.INDEX_PROGRESS, shared_progress)
        self.assertEqual(
            index_job.INDEX_PROGRESS,
            {
                "running": True,
                "current": 2,
                "total": 4,
                "title": "",
                "message": "",
                "updated_at": "",
            },
        )

    def test_set_index_progress_does_not_update_updated_at(self) -> None:
        index_job.INDEX_PROGRESS["updated_at"] = "2026-07-30T12:34:56+09:00"

        index_job._set_index_progress(True, 1, 3, "対象の本", "INDEX 対象の本")

        self.assertEqual(index_job.INDEX_PROGRESS["updated_at"], "2026-07-30T12:34:56+09:00")

    def test_start_updates_progress_before_starting_thread(self) -> None:
        thread_observation: dict[str, object] = {}

        class FakeThread:
            def __init__(self, *_args: object, **kwargs: object) -> None:
                thread_observation["job_args"] = kwargs["args"]

            def start(self) -> None:
                thread_observation["progress"] = index_job.get_progress()

        with patch("tsundokensaku.index_job.threading.Thread", FakeThread):
            index_job.start({"books/a.pdf"})

        self.assertEqual(thread_observation["job_args"], ({"books/a.pdf"},))
        self.assertEqual(
            thread_observation["progress"],
            {
                "running": True,
                "current": 0,
                "total": 0,
                "title": "",
                "message": "準備中",
                "updated_at": "",
            },
        )

    def test_run_index_job_preserves_final_counts_on_success(self) -> None:
        books_dir = Path("/virtual/books")
        db_path = Path("/virtual/index.db")
        index_job.INDEX_PROGRESS["updated_at"] = "unchanged"

        def index_books_stub(*, progress_callback, **_kwargs: object) -> list[object]:
            progress_callback(True, 7, 7, "最後の本", "DONE 最後の本")
            return []

        with (
            patch("tsundokensaku.index_job.get_books_dir", return_value=books_dir),
            patch("tsundokensaku.index_job.get_db_path", return_value=db_path),
            patch("tsundokensaku.index_job.index_books", index_books_stub),
        ):
            index_job._run_index_job({"books/last.pdf"})

        self.assertEqual(
            index_job.get_progress(),
            {
                "running": False,
                "current": 7,
                "total": 7,
                "title": "",
                "message": f"Indexed books under {books_dir}",
                "updated_at": "unchanged",
            },
        )

    def test_run_index_job_catches_error_and_preserves_progress(self) -> None:
        index_job.INDEX_PROGRESS["updated_at"] = "unchanged"
        index_job.INDEX_PROGRESS["extension"] = "keep"

        def failing_index_books(*, progress_callback, **_kwargs: object) -> list[object]:
            progress_callback(True, 3, 8, "壊れた本", "INDEX 壊れた本")
            raise RuntimeError("broken PDF")

        with (
            patch("tsundokensaku.index_job.get_books_dir", return_value=Path("/virtual/books")),
            patch("tsundokensaku.index_job.get_db_path", return_value=Path("/virtual/index.db")),
            patch("tsundokensaku.index_job.index_books", failing_index_books),
        ):
            index_job._run_index_job()

        self.assertEqual(
            index_job.get_progress(),
            {
                "running": False,
                "current": 3,
                "total": 8,
                "title": "",
                "message": "Error: broken PDF",
                "updated_at": "unchanged",
                "extension": "keep",
            },
        )
