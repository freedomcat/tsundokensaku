# import_books_from_cosense.py

Cosense（旧Scrapbox）のエクスポートJSONから `#Bookscan` と `#技術書` の両方のタグが付いたページを探し、対応するBookScan由来のPDFをつんどけんさく用のディレクトリへコピーするCLIツールです。

**これはコマンドラインツールであり、Web UIではありません。** ターミナルから `python` で直接実行します。

## 何をするか

1. エクスポートJSONの全ページを走査し、本文に `#Bookscan` と `#技術書` の**両方**を含むページだけを対象にする
2. 対象ページの本文からPDFのファイル名を推定する
   - BookScanのダウンロードURL（`https://system.bookscan.co.jp/...?f=ファイル名.pdf`）の `f` パラメータから取得
   - 見つからなければ `https://books.freedomcat.com/〜.pdf` のURL末尾から取得
3. `--source-root` 以下にあるそのPDFを、`--destination` へ **`ISBN(またはASIN)_タイトル.pdf`** という名前でコピーする
4. コピー結果の一覧を `import_manifest.csv` に出力する

### コピー後のファイル名

元のファイル名の末尾からISBN-10 / ISBN-13 / ASIN（`B` + 9文字）を取り出し、Cosenseページのタイトルと組み合わせて次の形式に変換します。

```
4798153982_More Effective C# 6．0／7．0.pdf
B08XXXXXXX_タイトル.pdf
```

- タイトル部分はファイル名に使えない文字を `_` に置換し、最大48文字に切り詰めます
- 元ファイル名からISBN/ASINが取れない場合は、元ファイル名の先頭24文字がIDの代わりになります

この `ID_タイトル.pdf` 形式は、つんどけんさく本体のタイトル推定（`resolve_pdf_display_title`）がそのまま解釈できる取り込み形式です。

## 前提条件

### Cosense/Scrapbox側

対象にしたい本のページに、次の両方が必要です。

- `#Bookscan` と `#技術書` のタグ（どちらか片方だけでは対象外）
- PDFファイル名を推定できるURL（BookScanのダウンロードURL、または books.freedomcat.com のPDF URL）

### PDFの置き場所（source-root）

BookScanからダウンロードしたPDFの置き場所を `--source-root` で指定します。既定値は `G:\マイドライブ\books`（Google DriveをGドライブとしてマウントしたWindows環境を想定）。Google Drive上にBookScanのPDFをそのままのファイル名で置いておく運用が前提ですが、ローカルの任意のディレクトリでも構いません。

### エクスポートJSON

`--json` で指定します。省略時はカレントディレクトリ → `~/Downloads` の順に `shino-books_*.json` を探し、最も新しいものを使います。

## 使い方

まずは **dry-run で確認** してから実行することを推奨します。

```bash
# 1. 対象ページの一覧だけ確認（コピーしない）
python scripts/import_books_from_cosense.py --list-titles

# 2. 何がどこへコピーされるか確認（コピーしない）
python scripts/import_books_from_cosense.py --dry-run

# 3. 問題なければ実行
python scripts/import_books_from_cosense.py

# source-root と destination を明示する場合
python scripts/import_books_from_cosense.py \
    --json ~/Downloads/shino-books_20260707_000000.json \
    --source-root "G:\マイドライブ\books" \
    --destination books/tech
```

## オプション

| オプション | 既定値 | 説明 |
|---|---|---|
| `--json` | 最新の `shino-books_*.json`（cwd → Downloads） | Cosense/ScrapboxのエクスポートJSON |
| `--source-root` | `G:\マイドライブ\books` | コピー元PDFのルートディレクトリ（Google Drive上のBookScan PDF置き場を想定） |
| `--destination` | `<リポジトリ>/books/tech` | コピー先ディレクトリ |
| `--manifest` | `<リポジトリ>/data/import_manifest.csv` | マニフェストCSVの出力先 |
| `--dry-run` | off | コピー予定の内容を表示するだけで、ファイルはコピーしない |
| `--overwrite` | off | コピー先に同名PDFがあっても上書きする（既定ではスキップ） |
| `--list-titles` | off | 対象ページのタイトル一覧を表示して終了（コピーもマニフェスト出力もしない） |
| `--quiet` | off | 1冊ごとの進捗を出さず、末尾のサマリーだけ表示 |

## 出力

### 実行ログ

1冊ごとに結果が表示されます。

- `COPIED` — コピーした
- `DRY-RUN` — dry-run時のコピー予定
- `EXISTS` — コピー先に既に存在（`--overwrite` なしのためスキップ）
- `MISSING` — source-root に元PDFが見つからない
- `SKIP` — 本文からPDFファイル名を推定できなかった

最後にサマリー（対象ページ数、コピー数、スキップ数、ファイル名不明数、元PDF欠落数）が出ます。

### import_manifest.csv

dry-run でない実行のたびに `--manifest` のパス（既定: `data/import_manifest.csv`）へCSVが出力されます。ファイル名を推定できた本について、次の列を持ちます。

| 列 | 内容 |
|---|---|
| `title` | Cosenseページのタイトル |
| `source_filename` | 推定した元PDFファイル名 |
| `destination_filename` | 変換後の `ID_タイトル.pdf` |
| `source_path` | コピー元のフルパス |
| `destination_path` | コピー先のフルパス |
| `inferred_from` | ファイル名の推定元（`Bookscan f parameter` / `books.freedomcat.com URL`） |

Excelで開けるようBOM付きUTF-8で出力されます。

## 取り込み後

コピーしたPDFをつんどけんさくに反映するには、インデックスを実行します。

```bash
python -m tsundokensaku index --books-dir books/tech
```

## 補足

- Windowsの長いパス（260文字超）にも対応しています（`\\?\` プレフィックスを自動付与）
- コピー先に書き込めない場合はエラーメッセージ内で一時ディレクトリを使った回避手順を案内します
