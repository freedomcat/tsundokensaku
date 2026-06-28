# つんどけんさく

個人用の技術書PDF全文検索CLIです。MVPでは `books/tech/` 配下のPDFをページ単位で読み取り、SQLite FTS5に保存してキーワード検索します。

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

## 依存ライブラリ

- `pypdf`: PDFからテキストを抽出するため。
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

検索結果には、書籍名、ページ番号、抜粋が表示されます。

## Ubuntu と Docker で使う

Ubuntu 側では、プロジェクトを `~/work/tsundokensaku` に置いて起動する前提にしています。

PDF は Windows 側の `C:\tsundokensaku-books\tech` をそのまま read-only でマウントします。Ubuntu からは `/mnt/c/tsundokensaku-books/tech` として見えます。

`make run` は、未変更のPDFを飛ばす差分インデックスとして動きます。削除済みPDFもDBから消します。

`BOOKS_DIR` で PDF のマウント元を、`DB_DIR` で DB の置き場所を変えられます。

```bash
BOOKS_DIR=/path/to/your/books/tech DB_DIR=./data make run
```

## Web UI

CLI を残したまま、ローカル用の Web UI も使えます。

起動:

```bash
uvicorn tsundokensaku.web:app --reload
```

ブラウザで次の画面を開きます。

- `http://127.0.0.1:8000/`
- `http://127.0.0.1:8000/manage`

Web UI でも `BOOKS_DIR` と `DB_DIR` を使えます。

インデックス作成:

```bash
make run
```

検索:

```bash
make search QUERY=SQLite
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

## Cosense/ScrapboxエクスポートからPDFを取り込む

蔵書が増えたら、Cosense/Scrapboxから新しいJSONをエクスポートして取り込みます。

この補助ツールは、JSON内の `pages` から `#Bookscan` と `#技術書` の両方を含むページを探し、Bookscanまたは `books.freedomcat.com` のPDFファイル名を推定して、`G:\マイドライブ\books` から `books/tech/` へコピーします。

まずコピー予定だけ確認します。

```powershell
python scripts/import_books_from_cosense.py --json "C:\Users\shino\Downloads\shino-books_20260625_001153.json" --dry-run
```

問題なければコピーします。既に `books/tech/` にあるPDFは既定でスキップされるので、増えた分だけ取り込めます。

```powershell
python scripts/import_books_from_cosense.py --json "C:\Users\shino\Downloads\shino-books_20260625_001153.json"
```

エクスポートJSONは、次のどちらかに置きます。

- `C:\Users\shino\Downloads\shino-books_*.json`
- プロジェクト直下、つまり `tsundokensaku\shino-books_*.json`

おすすめは、今回のようにプロジェクト直下へコピーして残しておく運用です。どのJSONから取り込んだかがプロジェクト内で分かりやすくなります。

プロジェクト直下に置いた場合は、最新の `shino-books_*.json` が自動で使われます。

```powershell
python scripts/import_books_from_cosense.py --dry-run --quiet
python scripts/import_books_from_cosense.py
```

上書きしたい場合だけ `--overwrite` を付けます。

```powershell
python scripts/import_books_from_cosense.py --overwrite
```

取り込みスクリプトの正式な置き場所は `scripts/import_books_from_cosense.py` です。

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
- Web UI
- 差分インデックスの細かい最適化

まずはローカルで確実に動く全文検索の芯を作るためです。
