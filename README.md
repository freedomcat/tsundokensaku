# つんどけんさく
## もう、積読にはさせない

つんどけんさくは、単なる蔵書検索ツールではありません。

積読を知識に変える——そのために必要なページを集め、AIへ渡す資料を組み立てる。すべてローカルで完結するワークスペースです。

想定しているのは、自分で適法に保有する多数の PDF 資料から、問いに関係する箇所を探して活用したい個人利用者です。PDF 本文を検索し、必要なページを選び、並べて資料として組み立て、ChatGPT や Claude、NotebookLM などの外部 AI へ渡せる形で書き出す作業を支援します。

PDF を外部 AI へ自動送信することはありません。PDF はローカル環境に保持され、何を使うかは利用者自身が選択・確認してから書き出します。中心的な価値は、眠っている積読を検索可能にし、必要な箇所を AI との学習に使える状態へ変えることです。

## 背景

このツールは [masui-shelf](https://scrapbox.io/masui-shelf/) に触発されて作りました。  
Scrapboxでの蔵書管理は書誌情報の一覧性に優れていますが、PDFの本文内容までは検索できません。  
つんどけんさくは、その欠けている部分である PDF 本文の全文検索から始まり、見つけた箇所を問いごとの資料として組み立て、外部 AI で活用できる形にするところまでを支えるツールです。

## 動作確認

前提:

- Docker
- Docker Compose

起動:

```bash
docker compose up --build
```

ブラウザで開きます。

```text
http://localhost:8000
```

このリポジトリには、動作確認用サンプルとして山形浩生さんによるオープンソース関連文書のPDFを `data/books/` に同梱しています。起動後、Web UIの設定画面でインデックスを実行すると、すぐにPDF本文検索とPDFモーダル表示を試せます。

初回はPDFの本文抽出とインデックス作成に時間がかかります。すでにインデックス済みのPDFは差分判定されるため、次回以降はすぐ検索できます。

### 同梱サンプルPDFについて

同梱しているPDFは次の3点です。

| ファイル | 文書 | 配布元 |
| --- | --- | --- |
| `data/books/cathedral.pdf` | 伽藍とバザール | <https://cruel.org/freeware/cathedral.pdf> |
| `data/books/noosphere.pdf` | ノウアスフィアの開墾 | <https://cruel.org/freeware/noosphere.pdf> |
| `data/books/magicpot.pdf` | 魔法のおなべ | <https://cruel.org/freeware/magicpot.pdf> |

これらは山形浩生さんのサイト <https://cruel.org/> で公開されているPDFです。`cathedral.pdf` と `noosphere.pdf` の本文には、版権表示を残す限り商業利用を含む複製・再配布が自由に認められる旨の記載があります。また、山形浩生さんのリンクポリシー <https://cruel.org/linkpolicy.html> では、丸ごとコピーする場合も文章自体を変えず、元URL・版権・転載自由であることを明記すればよい旨が示されています。

このリポジトリでは、サンプルPDFを内容変更せずに同梱し、元URLと版権者が分かる形で表示しています。自分の蔵書PDFを追加する場合は、`data/books/` に置いてからWeb UIの設定画面でインデックスを実行してください。git管理対象に追加するPDFは、再配布できるものだけにしてください。

## クイックスタート

すでに clone 済みなら 1 は飛ばしてかまいません。

1. リポジトリを取得して移動します。

```bash
git clone git@github.com:freedomcat/tsundokensaku.git
cd tsundokensaku
```

2. 必要に応じて設定ファイルを作ります。`.env` がなくても既定値で起動できます。

```bash
cp .env.example .env
```

3. PDFを置くディレクトリを確認します。サンプルPDFは最初から `data/books/` に入っています。

```bash
ls data/books
```

4. Web UIを起動します。

```bash
docker compose up --build
```

5. `http://localhost:8000` を開きます。

## 環境変数

`.env` はリポジトリに含めません。必要な場合だけ `.env.example` をコピーして使います。

| 項目 | 既定値 | 内容 |
| --- | --- | --- |
| `BOOKS_DIR` | `./data/books` | ホスト側のPDF保存ディレクトリ。WSL/Windowsでは `/mnt/c/Users/<name>/...` も使えます。 |
| `DB_DIR` | `./data` | SQLite DBの保存先ディレクトリ |
| `PDF_EXPORT_SAVE_DIR` | 空 | PDF切り出し結果を直接保存するフォルダ。Google Drive同期フォルダなどを指定できます。 |
| `WEB_PORT` | `8000` | Web UIの公開ポート |
| `SCRAPBOX_BASE_URL` | 空 | Scrapbox/Cosense連携用URL |
| `SCRAPBOX_EXPORT_JSON` | 空 | Scrapbox/CosenseエクスポートJSONのパス。例: `./shino-books_imported.json` |
| `DEMO_MODE` | `false` | `true` にすると、外部から書き込み・パス指定を伴う機能をすべて無効化します（UIは非表示 + APIも拒否）。Cloudflare Tunnel等で一時的に外部公開するときに使います。 |

Scrapbox/Cosense関連を設定しない場合、メモ検索やScrapboxリンク表示は使わずにPDF検索だけで動きます。

### デモモードでの外部公開について

`DEMO_MODE=true` で無効化される機能:

- PDF/scrapbox.jsonのアップロード（`/settings/pdf-upload`, `/settings/scrapbox-upload`） → 403 `"Upload is disabled in demo mode."`
- フォルダからまとめて追加（`/settings/pdf-import`）→ サーバー側の任意ローカルパスを指定してPDFをコピーできる機能のため対象
- scrapbox.jsonインポート（`/settings/scrapbox-import`）→ 同様にサーバー側の任意パスを指定できる機能のため対象
- PDF切り出し保存先の変更（`/settings/pdf-export-save-dir`）→ `.env` に任意パスを書き込める機能のため対象

いずれも「第三者がサーバー側の任意パスを指定してファイルシステムを操作できる」という同じ理由でブロック対象にしている。上記いずれもRedirectResponse + `"デモモードのため無効です"` メッセージで、実際の処理（コピー・DB同期・`.env`書き込み）は実行されない。

動作確認手順:

```bash
# 通常モード（デフォルト）: 全機能が使える
docker compose up -d app
curl -s http://localhost:8000/settings | grep pdf-dropzone   # ドロップゾーンあり
curl -s -X POST "http://localhost:8000/settings/pdf-upload?filename=test.pdf" --data-binary $'%PDF-1.4\ndummy'
# -> 201, /data/books/test.pdf が保存される

# デモモード: 上記すべてが無効
DEMO_MODE=true docker compose up -d app
curl -s http://localhost:8000/settings | grep "デモモードのため"   # 案内文のみ、フォーム類は非表示
curl -s -o /dev/null -w "%{http_code}\n" -X POST "http://localhost:8000/settings/pdf-upload?filename=test.pdf" --data-binary $'%PDF-1.4\ndummy'
# -> 403 "Upload is disabled in demo mode."
curl -s -D - -o /dev/null "http://localhost:8000/settings/pdf-import?source_dir=/etc" | grep -i location
# -> /settings?message=... (デモモードのため無効です)
```

## Web UIでできること

- PDF本文の全文検索
- Kindle本情報の検索
- Scrapbox/Cosenseメモの検索
- 検索結果からPDFの該当ページを表示
- 必要ページだけのPDF切り出し
- PDF、Kindle本、メモを横断した検索

検索対象は画面上で切り替えられます。

| 範囲 | 検索対象 |
| --- | --- |
| `all` | PDFのタイトルと本文、Kindle本のタイトル、本ごとのメモ、Scrapbox/Cosenseメモ |
| `title` | PDFとKindleの書籍タイトル |
| `body` | PDF本文 |
| `memo` | Scrapbox/Cosenseメモ |

本文検索はSQLite FTS5を使います。日本語はSudachiで分かち書きし、部分一致の救済にはFTS5 trigramインデックスを使います。

## NotebookLM用にページを抜き出す

検索結果のPDFから、指定したページだけを抜き出した小さいPDFを作れます。NotebookLMなどに必要なページだけ渡したいときに使います。Web UIではPDFモーダルから「ダウンロード」または「指定フォルダへ保存」を選べます。直接保存を使う場合は、設定画面の「PDF切り出し保存先」か `.env` の `PDF_EXPORT_SAVE_DIR` に保存先フォルダを指定してください。

```bash
PYTHONPATH=src python3 scripts/export_pdf_pages.py "data/books/cathedral.pdf" --pages 11-15
```

出力先を変える場合は `--output` を使います。

```bash
PYTHONPATH=src python3 scripts/export_pdf_pages.py "data/books/cathedral.pdf" --pages 11-15 --output /tmp/sample_pages.pdf
```

## CLI

Web UIが主役ですが、CLIでもインデックス作成と検索ができます。

```bash
python -m tsundokensaku index --books-dir data/books --db data/index.db
python -m tsundokensaku search "SQLite" --db data/index.db --limit 10
```

Makefile経由でも実行できます。

```bash
make run
make search QUERY=SQLite
```

## 補助スクリプト

運用・取り込み・移行・開発補助のための単独CLIツール群が `scripts/` にあります。Web UIの機能ではなく、ターミナルから直接実行するものです。一覧と使い方は [scripts/README.md](scripts/README.md) を参照してください。

## Cosense/Scrapbox連携

Cosense/ScrapboxのエクスポートJSONを取り込むと、メモ検索やPDFに対応するScrapboxページへのリンク表示ができます。

Web UIの設定画面からJSONをアップロードできます。CLI補助ツールを使う場合は、次のように実行します。

```bash
python scripts/import_books_from_cosense.py --json path/to/export.json --dry-run
python scripts/import_books_from_cosense.py --json path/to/export.json
```

この補助ツールは必須ではありません。PDFが `data/books/` にあれば、Web UIからそのままインデックスできます。

## 管理対象に含めないもの

このリポジトリでは、次の個人データや再配布できないファイルをgit管理対象にしない方針です。

- Bookscanなどで電子化したPDF
- 個人のScrapbox/Cosense export JSON実データ
- Kindleの個人情報を含むデータ
- `.env`
- APIキー、トークン、認証情報
- 個人メールアドレス
- 個人環境に強く依存した絶対パス

このリポジトリでは `.gitignore` と `.dockerignore` で、PDF、実DB、`.env`、個人用JSONを除外しています。

## ライセンス

コードとドキュメントはMIT Licenseです。詳細は [LICENSE](LICENSE) を参照してください。

`data/books/` に同梱しているPDFはMIT Licenseの対象外です。各PDFの権利と再配布条件は、このREADMEの「同梱サンプルPDFについて」に記載した配布元と条件に従います。

## テスト

```bash
python -m unittest discover -s tests
```

## 設計メモ

詳細な設計メモは [ARCHITECTURE.md](ARCHITECTURE.md) に分けています。

今後の機能計画は [ROADMAP.md](ROADMAP.md) にまとめています。

## あえて入れていないもの

- AI要約
- ベクトル検索
- RAG

現時点では、ローカルで安定して動く全文検索と、AIに渡すページを自分の蔵書から探して切り出す体験を優先しています。

## 利用上の注意

つんどけんさくは、利用者が適法に入手・保有している資料を、自分の知識活用のために検索・整理することを目的としたツールです。

- 著作権法および各サービスの利用規約を遵守して利用してください。
- DRMの解除やアクセス制限の回避など、不正な手段でコンテンツを取得する機能は提供しません。
- 違法に入手したコンテンツの利用は想定していません。
- AIへ資料を渡す場合も、利用する資料やサービスの利用条件を確認してください。
