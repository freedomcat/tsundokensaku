#!/usr/bin/env bash
# cloudflared Quick Tunnel（trycloudflare.com）を落ちても自動再起動しながら動かす監視ループ。
# Quick Tunnel は一時利用向けの仕様で接続が切れることがあるため、
# プロセスが終了したら数秒待って再起動する。再起動のたびに発行URLが
# 変わる点はQuick Tunnelの仕様上避けられない（固定URLが要るならNamed Tunnelを使う）。
#
# URLが発行されるたびに、.env に GMAIL_ADDRESS / GMAIL_APP_PASSWORD /
# NOTIFY_EMAIL_TO を設定していれば notify_email.py 経由で通知メールを送る
# （未設定ならURL変更通知は行わず、Tunnel自体はそのまま動く）。
set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${WEB_PORT:-8000}"
DATA_DIR="${PROJECT_ROOT}/data"
LOG_FILE="${DATA_DIR}/cloudflared.log"
URL_FILE="${DATA_DIR}/cloudflared_url.txt"
NOTIFY_SCRIPT="${PROJECT_ROOT}/scripts/notify_email.py"

if [[ -f "${PROJECT_ROOT}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${PROJECT_ROOT}/.env"
  set +a
fi

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared が見つかりません。先にインストールしてください。" >&2
  exit 1
fi

mkdir -p "$DATA_DIR"

echo "cloudflared Quick Tunnel を起動します（対象: http://localhost:${PORT}）"
echo "ログ: ${LOG_FILE}"
echo "最新URL: ${URL_FILE}"
if [[ -n "${GMAIL_ADDRESS:-}" && -n "${GMAIL_APP_PASSWORD:-}" && -n "${NOTIFY_EMAIL_TO:-}" ]]; then
  echo "URL変更通知: 有効（${NOTIFY_EMAIL_TO} 宛）"
else
  echo "URL変更通知: 無効（.envにGMAIL_ADDRESS/GMAIL_APP_PASSWORD/NOTIFY_EMAIL_TOを設定すると送信します）"
fi
echo "Ctrl+C で終了します"
echo

trap 'echo; echo "終了します"; exit 0' INT TERM

notify_new_url() {
  local url="$1"
  if [[ -z "${GMAIL_ADDRESS:-}" || -z "${GMAIL_APP_PASSWORD:-}" || -z "${NOTIFY_EMAIL_TO:-}" ]]; then
    return 0
  fi
  if python3 "$NOTIFY_SCRIPT" "つんどけんさく デモURL更新" "新しいデモURL: ${url}"; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') 通知メール送信: ${url}" | tee -a "$LOG_FILE"
  else
    echo "$(date '+%Y-%m-%d %H:%M:%S') 通知メール送信に失敗しました" | tee -a "$LOG_FILE"
  fi
}

while true; do
  : > "$URL_FILE"
  cloudflared tunnel --url "http://localhost:${PORT}" 2>&1 | tee -a "$LOG_FILE" | while IFS= read -r line; do
    echo "$line"
    if [[ "$line" == *"trycloudflare.com"* ]]; then
      new_url="$(echo "$line" | grep -oE 'https://[a-zA-Z0-9.-]+\.trycloudflare\.com' || true)"
      if [[ -n "$new_url" ]]; then
        echo "$new_url" >> "$URL_FILE"
        notify_new_url "$new_url"
      fi
    fi
  done
  echo "$(date '+%Y-%m-%d %H:%M:%S') cloudflared が終了しました。5秒後に再起動します" | tee -a "$LOG_FILE"
  sleep 5
done
