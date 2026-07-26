#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
    PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"
  else
    PYTHON_BIN="python3"
  fi
fi

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1 && [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python executable not found: $PYTHON_BIN" >&2
  exit 1
fi

tmp_root=$(mktemp -d "${TMPDIR:-/tmp}/tsundokensaku-playwright.XXXXXX")
books_dir="$tmp_root/books"
db_dir="$tmp_root/db"
mkdir -p "$books_dir" "$db_dir"
server_pid=""

cleanup() {
  local exit_status=$?
  if [[ -n "$server_pid" ]] && kill -0 "$server_pid" 2>/dev/null; then
    kill "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
  fi
  rm -rf "$tmp_root"
  exit "$exit_status"
}
trap cleanup EXIT INT TERM

# Copy only the PDFs committed as repository samples; never use data/books as a whole.
for sample_pdf in cathedral.pdf magicpot.pdf noosphere.pdf; do
  cp "$PROJECT_ROOT/data/books/$sample_pdf" "$books_dir/$sample_pdf"
done

env \
  BOOKS_DIR="$books_dir" \
  DB_DIR="$db_dir" \
  HOST_BOOKS_DIR="$books_dir" \
  HOST_DB_DIR="$db_dir" \
  SCRAPBOX_BASE_URL="" \
  SCRAPBOX_EXPORT_JSON="" \
  PDF_EXPORT_SAVE_DIR="" \
  TSUNDOKENSAKU_CHAPTER_MAX_PAGES_PER_FILE="" \
  TSUNDOKENSAKU_CHAPTER_MAX_SOURCES="" \
  DEMO_MODE="false" \
  TSUNDOKENSAKU_ROOT="$PROJECT_ROOT" \
  PYTHONPATH="$PROJECT_ROOT/src" \
  "$PYTHON_BIN" -m tsundokensaku index --books-dir "$books_dir" --db "$db_dir/index.db"

env \
  BOOKS_DIR="$books_dir" \
  DB_DIR="$db_dir" \
  HOST_BOOKS_DIR="$books_dir" \
  HOST_DB_DIR="$db_dir" \
  SCRAPBOX_BASE_URL="" \
  SCRAPBOX_EXPORT_JSON="" \
  PDF_EXPORT_SAVE_DIR="" \
  TSUNDOKENSAKU_CHAPTER_MAX_PAGES_PER_FILE="" \
  TSUNDOKENSAKU_CHAPTER_MAX_SOURCES="" \
  DEMO_MODE="false" \
  TSUNDOKENSAKU_ROOT="$PROJECT_ROOT" \
  PYTHONPATH="$PROJECT_ROOT/src" \
  "$PYTHON_BIN" -m uvicorn tsundokensaku.web:app --host 127.0.0.1 --port 8003 \
  >"$tmp_root/server.log" 2>&1 &
server_pid=$!

for attempt in $(seq 1 60); do
  if ! kill -0 "$server_pid" 2>/dev/null; then
    cat "$tmp_root/server.log" >&2
    echo "Playwright server exited before becoming ready" >&2
    exit 1
  fi
  if "$PYTHON_BIN" -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8003/", timeout=1)' >/dev/null 2>&1; then
    break
  fi
  if [[ "$attempt" == 60 ]]; then
    cat "$tmp_root/server.log" >&2
    echo "Timed out waiting for Playwright server" >&2
    exit 1
  fi
  sleep 0.5
done

npx playwright test --workers=1 "$@"
