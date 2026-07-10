#!/usr/bin/env python3
"""Gmail SMTP経由で通知メールを1通送るだけの薄いスクリプト。

環境変数 GMAIL_ADDRESS / GMAIL_APP_PASSWORD / NOTIFY_EMAIL_TO を使う。
GMAIL_APP_PASSWORD は通常のGmailパスワードではなく、Googleアカウントの
「アプリパスワード」を発行して使う（2段階認証が前提）。
"""
from __future__ import annotations

import os
import smtplib
import sys
from email.mime.text import MIMEText


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: notify_email.py <subject> <body>", file=sys.stderr)
        return 1

    subject, body = sys.argv[1], sys.argv[2]

    address = os.environ.get("GMAIL_ADDRESS", "").strip()
    app_password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    to_address = os.environ.get("NOTIFY_EMAIL_TO", "").strip()

    if not address or not app_password or not to_address:
        print(
            "GMAIL_ADDRESS / GMAIL_APP_PASSWORD / NOTIFY_EMAIL_TO を設定してください（.env参照）",
            file=sys.stderr,
        )
        return 1

    message = MIMEText(body)
    message["Subject"] = subject
    message["From"] = address
    message["To"] = to_address

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(address, app_password)
        server.send_message(message)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
