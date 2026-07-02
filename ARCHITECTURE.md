# tsundokensaku Architecture

## 最小構成

```text
tsundokensaku/
  books/tech/                   # 検索対象PDFを置く場所
  scripts/
    import_books_from_cosense.py # Cosense/ScrapboxエクスポートからPDFを取り込む補助ツール
  src/tsundokensaku/
    cli.py                      # CLI入口
    database.py                 # SQLite schema / 保存 / 検索
    pdf_extract.py              # PDFページ単位テキスト抽出
    indexer.py                  # PDF探索とDB投入
  tests/
    test_database.py            # DBと検索の最小テスト
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
