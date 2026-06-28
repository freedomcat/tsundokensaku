#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ "$#" -eq 0 ]; then
  echo "Usage: ./scripts/dev.sh <tsundokensaku args...>" >&2
  echo "Example: ./scripts/dev.sh index --books-dir /books/tech --db data/index.db" >&2
  exit 1
fi

docker compose run --rm app "$@"
