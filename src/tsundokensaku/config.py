from __future__ import annotations

import os
from pathlib import Path

from tsundokensaku.metadata import ENV_FILE


DEFAULT_BOOKS_DIR = Path("data/books")
DEFAULT_DB_PATH = Path("data/index.db")
PDF_EXPORT_SAVE_DIR_ENV = "PDF_EXPORT_SAVE_DIR"


def get_books_dir() -> Path:
    return Path(os.environ.get("BOOKS_DIR", str(DEFAULT_BOOKS_DIR)))


def get_db_path() -> Path:
    db_dir = Path(os.environ.get("DB_DIR", str(DEFAULT_DB_PATH.parent)))
    return db_dir / DEFAULT_DB_PATH.name


def get_pdf_export_save_dir() -> Path | None:
    configured = os.environ.get(PDF_EXPORT_SAVE_DIR_ENV, "").strip()
    return Path(configured).expanduser() if configured else None


def is_demo_mode() -> bool:
    return os.environ.get("DEMO_MODE", "").strip().lower() == "true"


def update_env_setting(key: str, value: str, env_file: Path = ENV_FILE) -> None:
    lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
    updated = False
    rendered: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            current_key, _current_value = stripped.split("=", 1)
            if current_key.strip() == key:
                rendered.append(f"{key}={value}")
                updated = True
                continue
        rendered.append(line)

    if not updated:
        if rendered and rendered[-1].strip():
            rendered.append("")
        rendered.append(f"{key}={value}")

    env_file.write_text("\n".join(rendered) + "\n", encoding="utf-8")
    os.environ[key] = value
