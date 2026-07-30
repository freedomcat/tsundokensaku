from __future__ import annotations

import threading

from tsundokensaku.config import get_books_dir, get_db_path
from tsundokensaku.indexer import index_books


INDEX_PROGRESS_LOCK = threading.Lock()
INDEX_PROGRESS: dict[str, object] = {
    "running": False,
    "current": 0,
    "total": 0,
    "title": "",
    "message": "",
    "updated_at": "",
}


def _set_index_progress(running: bool, current: int, total: int, title: str = "", message: str = "") -> None:
    with INDEX_PROGRESS_LOCK:
        INDEX_PROGRESS.update(
            {
                "running": running,
                "current": current,
                "total": total,
                "title": title,
                "message": message,
            }
        )


def get_progress() -> dict[str, object]:
    """進捗状態の独立したコピーを返す。"""
    with INDEX_PROGRESS_LOCK:
        return dict(INDEX_PROGRESS)


def is_running() -> bool:
    """現在ジョブが実行中かどうかを返す。"""
    return bool(get_progress().get("running"))


def _run_index_job(force_paths: set[str] | None = None) -> None:
    books_dir = get_books_dir()
    db_path = get_db_path()
    try:
        index_books(
            books_dir=books_dir,
            db_path=db_path,
            progress_callback=_set_index_progress,
            force_paths=force_paths,
        )
        _set_index_progress(
            False,
            int(get_progress().get("current", 0)),
            int(get_progress().get("total", 0)),
            "",
            f"Indexed books under {books_dir}",
        )
    except Exception as exc:  # pragma: no cover - surfaced in browser
        _set_index_progress(
            False,
            int(get_progress().get("current", 0)),
            int(get_progress().get("total", 0)),
            "",
            f"Error: {exc}",
        )


def start(force_paths: set[str] | None = None) -> None:
    """進捗状態を更新し、バックグラウンドでインデックスを実行する。"""
    _set_index_progress(True, 0, 0, "", "準備中")
    thread = threading.Thread(target=_run_index_job, args=(force_paths,), daemon=True)
    thread.start()
