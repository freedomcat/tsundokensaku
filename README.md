# つんどけんさく
## もう、積読にはさせない

つんどけんさくは、単なる蔵書検索ツールではありません。

積読を知識に変える——そのために必要なページを集め、AIへ渡す資料を組み立てる。すべてローカルで完結するワークスペースです。

## 背景

このツールは [masui-shelf](https://scrapbox.io/masui-shelf/) に触発されて作りました。  
Scrapboxでの蔵書管理は書誌情報の一覧性に優れていますが、PDFの本文内容までは検索できません。  
つんどけんさくは、その欠けている部分、PDF本文の全文検索を補うために作ったツールです。

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

Scrapbox/Cosense関連を設定しない場合、メモ検索やScrapboxリンク表示は使わずにPDF検索だけで動きます。

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