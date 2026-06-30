# つんどけんさく

個人用の技術書PDF全文検索CLIです。MVPでは `books/tech/` 配下のPDFをページ単位で読み取り、SQLite FTS5に保存してキーワード検索します。追加課金なしで動きます。

## 最小構成

```text
tsundokensaku/
  books/tech/                 # 検索対象PDFを置く場所
  scripts/
    import_books_from_cosense.py # Cosense/ScrapboxエクスポートからPDFを取り込む補助ツール
  src/tsundokensaku/
    cli.py                    # CLI入口
    database.py               # SQLite schema / 保存 / 検索
    pdf_extract.py            # PDFページ単位テキスト抽出
    indexer.py                # PDF探索とDB投入
  tests/
    test_database.py          # DBと検索の最小テスト
  pyproject.toml
  README.md
```

## 実装方針

- PDFは `pypdf` でページ単位にテキスト抽出する。
- SQLiteは標準ライブラリ `sqlite3` を使う。
- 検索用にSQLite FTS5の仮想テーブルを作る。
- CLIは `index` と `search` の2コマンドに絞る。
- Web UIを後で追加しやすいように、CLIから直接DB処理を書かず、`indexer.py` と `database.py` に分ける。
- テストしやすいように、DB処理は一時ファイルDBでも動く純粋な関数に寄せる。
- `books` には `source_type` を持たせ、PDF と Kindle を同じ一覧で扱えるようにする。
- Scrapbox 由来のキャッシュは `memos` に残し、本ごとの注記は `book_notes` に分ける。

## 依存ライブラリ

- `pypdf`: PDFからテキストを抽出するため。
- `sudachipy` / `sudachidict_core`: 本文検索の分かち書きに使います。未導入でも、簡易フォールバックで動きます。
- `sqlite3`: Python標準ライブラリ。FTS5対応SQLiteが必要。

この環境のバンドルPythonではSQLite `3.50.4`、`pypdf 6.10.0` を確認済みです。

## 使い方

PDFを置きます。

```powershell
New-Item -ItemType Directory -Force books/tech
```

開発中はインストールせず、`PYTHONPATH` で実行できます。

```powershell
$env:PYTHONPATH="src"
python -m tsundokensaku index
python -m tsundokensaku search "SQLite"
```

DBの保存先やPDFディレクトリを変える場合:

```powershell
$env:PYTHONPATH="src"
python -m tsundokensaku index --books-dir books/tech --db data/index.db
python -m tsundokensaku search "Python" --db data/index.db --limit 10
```

検索結果には、書籍名、ページ番号、抜粋が表示されます。Kindle 本はページ番号なしで表示され、本ごとのメモは `book_notes` として一緒に検索対象になります。

## WSL2 (Ubuntu) + Docker で使う

この例では、WSL2 上の Ubuntu で開発し、PDF は Windows 側に保存したまま使う構成を想定しています。

プロジェクトは次の場所に置きます。

```text
~/work/tsundokensaku
```

PDF は Windows 側の次の場所に置きます。

```text
C:\tsundokensaku-books\tech
```

WSL2 からは次のパスとして参照できます。

```text
/mnt/c/tsundokensaku-books/tech
```

Docker コンテナには、このディレクトリを read-only でマウントします。これにより、コンテナから誤って PDF を書き換えたり削除したりするのを防げます。

`make run` は差分更新でインデックスを作ります。

- 新しく追加された PDF をインデックスに登録します。
- 更新された PDF だけ再解析します。
- 削除された PDF はインデックスから削除します。

普段は `make run` で十分です。

`make reindex` は `data/index.db` を削除してから、全件を最初から作り直します。本文抽出方法や検索アルゴリズムを変えたときはこちらを使います。

`BOOKS_DIR` には PDF を保存しているディレクトリを、`DB_DIR` には DB の置き場所を指定できます。

```bash
BOOKS_DIR=/path/to/your/books/tech DB_DIR=./data make run
```

## Web UI

CLI に加えて、ローカル環境で使える Web UI も用意しています。

### 起動

```bash
.venv/bin/uvicorn tsundokensaku.web:app --reload
```

`Ctrl+C` で終了します。

ブラウザで次の画面を開きます。

- `http://127.0.0.1:8000/`
- `http://127.0.0.1:8000/settings`

### 設定

設定は `.env` で管理できます。まずサンプルをコピーします。

```bash
cp .env.example .env
```

主な設定項目は次のとおりです。

| 項目 | 内容 |
| --- | --- |
| `BOOKS_DIR` | PDF を保存しているフォルダ |
| `DB_DIR` | インデックス DB の保存先 |
| `SCRAPBOX_BASE_URL` | Scrapbox 連携用 URL |
| `BASE_URL` | アプリのベース URL |
| `SCRAPBOX_PROJECT_URL` | Scrapbox プロジェクト URL |

Scrapbox 関連を設定しない場合は、Scrapbox リンクは表示しません。

`Web UI` でも `BOOKS_DIR` と `DB_DIR` を使えます。

### 設定画面

`/settings` では次の操作ができます。

- `scrapbox.json` の再同期
- PDF を追加するフォルダの確認
- インデックス実行
- PDF 一覧の表示
- Kindle 本一覧の表示

### 検索

検索対象は 4 種類から選べます。

| 範囲 | 検索対象 |
| --- | --- |
| `all` | PDF のタイトルと本文、Kindle 本のタイトル、本ごとのメモ、Scrapbox のメモをまとめて検索します |
| `title` | PDF と Kindle の書籍タイトルを検索します |
| `body` | PDF の本文だけを検索します |
| `memo` | Scrapbox のメモだけを検索します |

CLI では `--scope`、Web UI では検索フォームのプルダウンで切り替えられます。

### 検索結果の並び順

並び順は画面上のセレクトボックスで切り替えられます。

- 関連度順: SQLite FTS5 の `rank` 順です。通常は検索語に近い結果が上に出ます。
- 書名順: 書籍名で並べ、同じ書籍内ではページ番号順に表示します。
- ページ番号順: ページ番号が小さい順に並べ、同じページ番号では書籍名順に表示します。
- Scrapboxあり優先: `shino-books_*.json` から対応する Scrapbox ページが見つかった結果を先に表示します。

### 本文検索について

本文検索は SQLite FTS5 を利用しています。

日本語は Sudachi で分かち書きを行います。

Sudachi がインストールされていない環境では簡易トークナイザに切り替わり、FTS5 を利用できない場合は `LIKE` 検索で動作します。

### CLI

インデックス更新:

```bash
make run
```

これは Web サーバーの起動ではなく、PDF をインデックスへ登録・更新するコマンドです。

全件再構築:

```bash
make reindex
```

検索:

```bash
make search QUERY=SQLite
```

## NotebookLM 用にページを抜き出す

検索結果の PDF から、指定したページだけを抜き出した小さい PDF を作れます。NotebookLM に渡したいときに使う想定です。

```bash
PYTHONPATH=src python3 scripts/export_pdf_pages.py "books/tech/理科系の作文技術.pdf" --pages 11-15
```

出力先を変える場合は `--output` を使います。

```bash
PYTHONPATH=src python3 scripts/export_pdf_pages.py "books/tech/理科系の作文技術.pdf" --pages 11-15 --output /tmp/rika_pdf.pdf
```

直接使う場合:

```bash
./scripts/dev.sh index --books-dir /books/tech --db data/index.db
./scripts/dev.sh search --db data/index.db SQLite
```

Windowsセキュリティがプロジェクト配下への書き込みを止める場合は、PDF置き場とDBを短い固定パスに置くのが安定です。

```powershell
$env:PYTHONPATH="src"
py -3.13 -m tsundokensaku index --books-dir "C:\tsundokensaku-books\tech" --db "C:\tsundokensaku-books\index.db"
py -3.13 -m tsundokensaku search "SQLite" --db "C:\tsundokensaku-books\index.db"
```

## Cosense/Scrapbox から技術書 PDF を取り込む

Cosense/Scrapbox のエクスポート JSON から、`#技術書` と `#Bookscan` が付いたページだけを拾い、Bookscan 由来の技術書 PDF を `books/tech/` に集める補助ツールです。

このスクリプトは、JSON 内の `pages` から両方のタグを含むページを探し、PDF の場所が書かれているページだけを取り込み対象にします。既定では `G:\マイドライブ\books` をコピー元、`books/tech/` をコピー先として使います。コピー元は `--source-root` で変更できます。PDF が見つからない場合は、コピーをスキップします。

このツールは必須ではありません。PDF が `books/tech/` にあれば、`make run` や `python -m tsundokensaku index` でそのままインデックスできます。

`--json` を省略した場合は、カレントディレクトリか `Downloads` にある最新の `shino-books_*.json` を使います。

まずコピー予定だけ確認します。

```powershell
python scripts/import_books_from_cosense.py --json "C:\Users\shino\Downloads\shino-books_20260625_001153.json" --dry-run
```

問題なければコピーします。既に `books/tech/` にある PDF は既定でスキップされます。

```powershell
python scripts/import_books_from_cosense.py --json "C:\Users\shino\Downloads\shino-books_20260625_001153.json"
```

上書きしたい場合だけ `--overwrite` を付けます。コピー後は `data/import_manifest.csv` に取り込み結果の一覧を出力します。

```powershell
python scripts/import_books_from_cosense.py --overwrite
```

`--source-root` でコピー元、`--destination` でコピー先、`--manifest` で一覧ファイルの保存先を変えられます。

Codexなどの制限付き実行環境で作業ツリー内にDBを作れない場合は、一時ディレクトリを指定してください。

```powershell
$env:PYTHONPATH="src"
python -m tsundokensaku index --db "$env:TEMP\tsundokensaku-index.db"
python -m tsundokensaku search "SQLite" --db "$env:TEMP\tsundokensaku-index.db"
```

## テスト

```powershell
$env:PYTHONPATH="src"
python -m unittest discover -s tests
```

## 今回あえて入れていないもの

- AI要約
- ベクトル検索
- 差分インデックスの細かい最適化

まずはローカルで確実に動く全文検索の芯を作るためです。
