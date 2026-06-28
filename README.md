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

## Ubuntu と Docker で使う

Ubuntu 側では、プロジェクトを `~/work/tsundokensaku` に置いて起動する前提にしています。

PDF は Windows 側の `C:\tsundokensaku-books\tech` をそのまま read-only でマウントします。Ubuntu からは `/mnt/c/tsundokensaku-books/tech` として見えます。

`make run` は、未変更のPDFを飛ばす差分インデックスとして動きます。削除済みPDFもDBから消します。
`make reindex` は、`data/index.db` を消してから全件を作り直します。本文抽出や検索アルゴリズムを変えたときはこちらを使います。

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
- `http://127.0.0.1:8000/settings`

Web UI でも `BOOKS_DIR` と `DB_DIR` を使えます。
設定をまとめて書くなら、まず `.env.example` を `.env` にコピーして使えます。

```bash
cp .env.example .env
```

`.env` には `BOOKS_DIR`、`DB_DIR`、Scrapbox を使う場合は `SCRAPBOX_BASE_URL` などを入れます。
`BASE_URL` や `SCRAPBOX_PROJECT_URL` も読みます。未設定なら Scrapbox リンクは表示しません。
`/settings` では、`scrapbox.json` の再同期、PDF を追加するフォルダの確認、インデックス実行、PDF一覧、Kindle本一覧をまとめて見られます。

検索範囲は `all` / `title` / `body` / `memo` の4種類です。
- `all`: PDF のタイトルと本文、Kindle 本のタイトル、本ごとのメモ、Scrapbox のメモをまとめて検索します。
- `title`: PDF と Kindle の書籍タイトルを検索します。
- `body`: PDF の本文だけを検索します。
- `memo`: Scrapbox のメモだけを検索します。

本文検索は、Sudachi で分かち書きした SQLite FTS5 を基本にしつつ、既存インデックスや未導入環境では `LIKE` も併用します。Sudachi が入っていない環境でも、簡易トークナイザにフォールバックします。

CLI では `--scope`、Web UI では検索フォームのプルダウンで切り替えられます。

検索結果の並び順は、画面上のセレクトボックスで切り替えられます。

- 関連度順: SQLite FTS5 の `rank` 順です。通常は検索語に近い結果が上に出ます。
- 書名順: 書籍名で並べ、同じ書籍内ではページ番号順に表示します。
- ページ番号順: ページ番号が小さい順に並べ、同じページ番号では書籍名順に表示します。
- Scrapboxあり優先: `shino-books_*.json` から対応する Scrapbox ページが見つかった結果を先に表示します。

インデックス作成:

```bash
make run
```

全件再構築:

```bash
make reindex
```

検索:

```bash
make search QUERY=SQLite
```

次にやること 3件:

```bash
make action QUERY=SQLite
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
- 差分インデックスの細かい最適化

まずはローカルで確実に動く全文検索の芯を作るためです。
