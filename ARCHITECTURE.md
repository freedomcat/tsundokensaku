# tsundokensaku Architecture

## 目的

つんどけんさくは、ローカルに置いた技術書PDF、Kindle本情報、Scrapbox/Cosenseメモを横断検索する個人蔵書の検索ハブです。

主役はWeb UIです。CLIはインデックス作成と検索を自動化・検証するための補助入口として残しています。

## 構成

```text
tsundokensaku/
  data/books/                   # デフォルトのPDF置き場。同梱サンプルPDFもここに置く
  data/index.db                 # SQLite DB。git管理対象外
  scripts/
    export_pdf_pages.py         # NotebookLM等へ渡すページ切り出し
    import_books_from_cosense.py # Cosense/Scrapboxエクスポート補助
    reindex_pdf_pages.py        # 既存DBのページ再インデックス補助
  src/tsundokensaku/
    web.py                      # FastAPI Web UI
    cli.py                      # CLI入口
    database.py                 # SQLite schema / 保存 / 検索
    indexer.py                  # PDF探索とDB投入
    metadata.py                 # PDF/Scrapbox/Kindleメタデータ解決
    pdf_extract.py              # PDFページ単位テキスト抽出
    pdf_export.py               # PDFページ切り出し
    pdf_outline.py              # PDFアウトライン（しおり）から章ページ範囲を解決
    markdown_export.py          # ページ範囲の本文をMarkdownとして整形
    tokenizer.py                # Sudachi分かち書きと検索用正規化
  templates/                    # Web UIテンプレート
  static/                       # Web UI静的ファイル
  tests/                        # DB、Web、PDF、メタデータのテスト
```

Docker Composeでは、`.env`がない場合も動くように次の既定値を使います。

| 環境変数 | 既定値 | 用途 |
| --- | --- | --- |
| `BOOKS_DIR` | `./data/books` | ホスト側PDF置き場 |
| `DB_DIR` | `./data` | ホスト側DB保存先 |
| `WEB_PORT` | `8000` | Web UI公開ポート |

コンテナ内ではPDF置き場を `/data/books`、DB保存先を `/app/data` として扱います。過去DBとの互換性のため、`/books/tech/...` で保存されたPDFパスも解決できるようにしています。

## データモデル

中心はSQLiteです。外部AI APIやクラウド検索サービスには依存しません。

- `books`: PDF/Kindleを同じ一覧で扱う本テーブル
- `books.title`: UI表示用タイトル
- `books.filename`: PDF実ファイル名
- `pages`: PDF本文をページ単位で保存
- `book_notes`: Scrapbox/Cosense由来の本ごとの注記
- `memos`: Scrapbox/Cosenseメモ
- `books_fts`, `pages_fts`, `book_notes_fts`, `memos_fts`: FTS5検索用
- `pages_trigram`: 本文部分一致を高速化するFTS5 trigramテーブル

UI、検索結果、PDF一覧、モーダル、CLI表示では `title` を表示します。`filename` はPDFファイル解決など内部処理に限定します。

## メモデータの前提

Scrapbox/Cosense連携は、`#Bookscan` と `#技術書` を含むページを本情報として取り込む前提で設計しています。

Scrapbox連携のサンプルデータは同梱していません。作者の蔵書メモは非公開運用のためです。

## タイトル解決

PDFの表示タイトルは次の順で決めます。

1. PDFメタデータの `Title`
2. Scrapbox/Cosense由来の書籍タイトル
3. ファイル名整形

ただし、`C:/Temp/magicpot.dvi` のような生成元ファイルパスや `.dvi` / `.ps` / `.tex` / `.pdf` で終わるファイル名らしいPDF Titleは、書名ではないため無視します。その場合はScrapbox/Cosenseタイトルまたはファイル名整形へフォールバックします。

既存DBでは、`books.title` と `books.filename` を分離するマイグレーションを `initialize()` 時に行います。

## PDFインデックス

`indexer.py` が `BOOKS_DIR` 配下のPDFを探索し、次の流れでDBへ保存します。

1. PDFファイルを列挙
2. 表示タイトルを解決
3. サイズ・mtime・タイトル・ファイル名が既存DBと同じならスキップ
4. PDF本文をページ単位で抽出
5. `books`, `pages`, FTSテーブル、trigramテーブルを更新
6. PDF置き場から消えたPDFはDBから削除

初回インデックスは本文抽出とFTS投入のため時間がかかります。2回目以降は差分判定でスキップできます。

## PDF本文抽出

本文抽出は `pdf_extract.py` が担当します。

- まずPyMuPDFで抽出する
- PyMuPDFで開けない場合のみpypdfにフォールバックする
- 抽出後に制御文字を除去し、ページ単位のテキストへ正規化する

PyMuPDFを優先する理由は、古い日本語PDFでpypdfがエラーを出さずに文字化けした本文を返すケースがあるためです。同梱サンプルの `noosphere.pdf` はこの再発防止テストに使っています。

## 検索

検索スコープはWeb UI/CLI共通です。

| scope | 対象 |
| --- | --- |
| `all` | タイトル、PDF本文、Kindle本、本ごとのメモ、Scrapbox/Cosenseメモ |
| `title` | PDF/Kindleタイトル |
| `body` | PDF本文 |
| `memo` | Scrapbox/Cosenseメモ |

本文検索は2系統です。

- `pages_fts`: Sudachiで分かち書きしたトークンベース検索
- `pages_trigram`: 部分一致救済用のtrigram検索

以前は `LIKE '%query%'` のフォールバックで本文全件スキャンが発生していました。現在はtrigram FTSへ寄せ、検索速度と部分一致の両立を狙っています。

## Web UI

`web.py` はFastAPIアプリです。

主な機能:

- 検索画面
- PDF一覧/書籍一覧
- PDFモーダル表示
- PDFページ切り出し
- PDFアウトラインからの章選択切り出し（複数章選択可）
- ページ範囲のMarkdownエクスポート（出典ヘッダ付き）
- 検索結果からのページ追加とワークスペースでの編集・一括エクスポート
- インデックス実行と進捗表示
- Scrapbox/Cosense JSONアップロードと同期
- Kindle本情報同期

PDF表示URLは、DBに保存されたパスを現在の `BOOKS_DIR` 配下へ解決してから作ります。これにより、Docker内パスとホスト側パスの差分、旧 `/books/tech` パスのDBにも対応します。

## PDF章切り出し

`pdf_outline.py` がPyMuPDFの `get_toc()` でPDFアウトライン（しおり）を読み、章ごとの1始まりページ範囲へ変換します。

- 章の終端は、同じ階層以浅の次エントリの開始ページまで含めます。章末の本文が次章の開始ページに続くケースを取りこぼさないためです
- サブセクションを持つ章は、サブセクションを含む全ページ範囲になります
- 最終章は文書の最終ページまでです

Web UIは `GET /pdf-outline?pdf_path=...` で章一覧を取得し、PDFモーダルにチェックボックスの章リストを表示します。選択した章のページ範囲は `3-7,20-35` の形式でページ指定欄へ反映され、既存の `parse_page_selection` によるページ切り出しをそのまま使います。

アウトラインがないPDFでは章リストを表示せず、従来どおり手動のページ指定にフォールバックします。

## Markdownエクスポート

`markdown_export.py` が、指定ページ範囲の本文を出典ヘッダ付きMarkdownへ整形します。ChatGPTなどテキスト貼り付けが速いAIツール向けの出力です。

- `GET /export-md?pdf_path=...&pages=...` で取得します。ページ指定はPDF切り出しと同じ `3-7,20-35` 形式です
- 冒頭に出典ヘッダ（書名、元ファイル名、ページ範囲、抽出日）を付け、本文はページごとに `## p.N` 見出しで区切ります。AIがページ単位で出典を答えられるようにするためです
- 本文はインデックス済みの `pages` テーブルを優先し、未インデックスのPDFは `pdf_extract.py` でその場抽出します
- 出力は1リクエスト1ファイルで、ファイル名はPDF切り出しと同じ規則（`{stem}_p3-7_20-35.md`）です

PDFモーダルでは「指定ページ切出」の隣の「MD切出」から使います。ページ指定欄を共有するため、章選択・手動指定のどちらでも動きます。

## ワークスペース

検索結果から集めたページ範囲を編集し、AIへ渡す資料を組み立てる画面です。検索ページは「追加する場所」、ワークスペース（`/workspace`）は「編集してエクスポートする場所」として役割を分けています。

- 検索結果のPDFヒットの「カートに追加」で本単位に追加します。検索ページのバーは件数表示とワークスペースへの導線だけで、エクスポートはワークスペースから行います
- データは `sessionStorage` に `{ version: 2, books: { "<pdf_path>": { title, pages, collapsed, addedAt } } }` の形式で保持します。`pages` はページ指定spec文字列（例: `3-7,20-35`）そのままで、開区間（`39-`）も表現できます
- ワークスペースでは本ごとの折りたたみカードで、ページ範囲の直接編集（即時バリデーション）、章の選び直し（`/pdf-outline` 再利用、アウトラインありPDFのみ）、削除、全クリアができます
- ページ範囲欄が常に正で、章チェックは範囲欄へ反映するための入力手段です。手動編集後の章チェック状態との乖離は許容します
- 「ページを追加」モーダルで、ワークスペース内のPDF本文をキーワード検索してページを追加できます。`GET /search-pages?pdf_path=...&q=...` が1冊分の `pages` テーブルを部分一致検索し、ページ番号と抜粋を返します。PDFアウトラインの有無に依存しないため、BookScan由来のアウトラインなしPDFでも使えます。追加済みページは選択不可で表示し、重複追加を防ぎます
- エクスポートは本ごとに1ファイルで、既存の `/export-pdf` と `/export-md` を順に呼び出すだけです。サーバ側にワークスペース専用の処理はありません（`GET /workspace` はテンプレートを返すだけです）
- 複数冊では連続ダウンロードになるため、ブラウザが複数ファイルのダウンロード許可を求めることがあります

ページ指定specの解析・結合・検証・圧縮は `static/pages-spec.js` に共通化し、PDFモーダル・検索ページ・ワークスペースで共用しています。カートの読み書きとナビのバッジ更新は `static/export-cart.js` が担当します。

## CLI

CLIは補助入口です。

```bash
python -m tsundokensaku index --books-dir data/books --db data/index.db
python -m tsundokensaku search "SQLite" --db data/index.db --limit 10
```

CLI表示もWeb UIと同じく `title` を使い、ファイル名を直接見せない方針です。

## 依存ライブラリ

- `fastapi` / `uvicorn`: Web UI
- `jinja2`: HTMLテンプレート
- `pypdf`: PDFメタデータ取得、ページ切り出し、PyMuPDF失敗時の本文抽出フォールバック
- `pymupdf`: PDF本文抽出
- `sudachipy` / `sudachidict_core`: 日本語検索用の分かち書き
- `sqlite3`: Python標準ライブラリ。FTS5対応SQLiteが必要

## 配布データ

動作確認用サンプルとして、山形浩生さんのサイトで公開されている次のPDFを `data/books/` に同梱しています。

- `cathedral.pdf`
- `noosphere.pdf`
- `magicpot.pdf`

同梱理由と配布元URLは `README.md` に記載しています。コードとドキュメントはMIT Licenseですが、同梱PDFはMIT Licenseの対象外です。個人のBookscan PDF、Scrapbox/Cosense実データ、Kindle個人情報、`.env`、実DBはgit管理対象外です。
