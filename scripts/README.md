# scripts/ — 補助CLIツール群

**ここにあるのは Web UI の機能ではなく、ターミナルから単独で実行するCLIツール群です。**

## 位置づけ

- 本体（Web UI / `python -m tsundokensaku`）からは呼ばれません。実行経路は完全に独立しています
- `src/tsundokensaku/` のライブラリコードだけを共有します（例: `export_pdf_pages.py` は Web UI のPDF切り出し機能と同じ `pdf_export` モジュールを使います）
- 本体が「検索して読む」ためのアプリなのに対し、scripts/ はその周辺にある**運用・取り込み・移行・開発補助**のためのツール置き場です
  - 日常の検索では使いませんが、蔵書を増やすとき・DBを作り直すとき・開発するときに登場します

## ツール一覧

### 開発支援

#### dev.sh

| | |
|---|---|
| 用途 | Dockerコンテナ内で任意の tsundokensaku コマンドを実行するラッパー |
| 実行例 | `./scripts/dev.sh index --books-dir /books/tech --db data/index.db` |
| 想定シーン | ローカルにPython環境を作らず、開発中のコードをコンテナで試したいとき |

### デモ公開支援

#### cloudflare_tunnel.sh

| | |
|---|---|
| 用途 | cloudflared Quick Tunnel（trycloudflare.com）を落ちても自動再起動しながら動かす監視ループ |
| 実行例 | `./scripts/cloudflare_tunnel.sh` |
| 想定シーン | Cloudflare Tunnelで一時的に外部公開するデモ環境の可用性を上げたいとき |

Quick Tunnelは一時利用向けの仕様で接続が切れることがあります。落ちても5秒後に再起動し、最新URLを `data/cloudflared_url.txt` に書き出します。再起動のたびに発行URLが変わる点はQuick Tunnelの仕様上避けられません（固定URLが要る場合はNamed Tunnelの利用を検討してください）。

`.env` に `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` / `NOTIFY_EMAIL_TO` を設定していると、URLが変わるたびに `notify_email.py` 経由で通知メールを送ります。未設定ならTunnel自体は通知なしで動きます。外部公開前に `DEMO_MODE=true` の設定も合わせて確認してください（ルートREADMEの「デモモードでの外部公開について」参照）。

#### notify_email.py

| | |
|---|---|
| 用途 | Gmail SMTP経由で通知メールを1通送るだけの薄いスクリプト |
| 実行例 | `python3 scripts/notify_email.py "件名" "本文"` |
| 想定シーン | `cloudflare_tunnel.sh` からURL変更を通知するとき。単独でも呼び出せます |

`GMAIL_APP_PASSWORD` は通常のGmailパスワードではなく、Googleアカウントの「アプリパスワード」（2段階認証が前提）を発行して使います。

### PDF操作

#### export_pdf_pages.py

| | |
|---|---|
| 用途 | PDFから指定ページだけを抜き出した小さいPDFを作る |
| 実行例 | `PYTHONPATH=src python3 scripts/export_pdf_pages.py "data/books/cathedral.pdf" --pages 11-15` |
| 想定シーン | NotebookLMなどのAIツールに必要な章だけ渡したいとき |

ページ指定は `11-15` / `1,3,5` / `1-3,8-10` 形式。出力先は `--output` で変更できます（既定は `<入力名>_p<ページ>.pdf`）。同じ機能は Web UI のPDFモーダルからも使えます。こちらはブラウザを開かずに済ませたいとき用です。

### 蔵書取り込み

#### import_books_from_cosense.py

| | |
|---|---|
| 用途 | Cosense/ScrapboxエクスポートJSONから `#Bookscan` `#技術書` 付きページを探し、対応するPDFを `ISBN(ASIN)_タイトル.pdf` にリネームして取り込む |
| 実行例 | `python scripts/import_books_from_cosense.py --dry-run` |
| 想定シーン | BookScanで電子化した本を（Google Drive経由などで）つんどけんさくの蔵書に追加するとき |
| 詳細 | [README_import_books_from_cosense.md](README_import_books_from_cosense.md) |

まず `--list-titles` → `--dry-run` で確認してから本実行する運用を推奨します。実行後は `import_manifest.csv` が出力されます。

### DBメンテナンス

#### reindex_pdf_pages.py

| | |
|---|---|
| 用途 | トークナイザ変更後に `pages` / `pages_fts` テーブルだけを強制再構築する（メモ等ほかのテーブルは保持） |
| 実行例 | `python3 scripts/reindex_pdf_pages.py --dry-run` |
| 想定シーン | 形態素解析器（Sudachi）の導入・変更などで、既存DBの全文検索インデックスを作り直すとき |

DBバックアップ、state ファイルによる中断・再開（`--resume`）、再構築前後の検索ヒット数比較、`~/wiki/inbox/` への結果メモ出力までを一括で行う移行用ツールです。日常運用で使うものではありません。

## 共通の注意

- どのツールもリポジトリルートから実行する前提です
- `export_pdf_pages.py` は `PYTHONPATH=src` が必要です（ほかは不要）
- PDFや実DBなどの個人データはgit管理対象外です（ルートREADMEの「管理対象に含めないもの」参照）
