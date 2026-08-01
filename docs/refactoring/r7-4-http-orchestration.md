# R7-4: HTTPオーケストレーションの詳細設計

[R7全体設計](../central-file-refactoring-inventory.md) / [R7-1](r7-1-pdf-upload-storage.md) / [R7-2](r7-2-pdf-directory-import.md) / [R7-3](r7-3-scrapbox-sync.md) / [ROADMAP](../../ROADMAP.md)

状態: 詳細設計済み・未実装。実装とテスト変更はまだ行っていない。

調査基準: 2026-08-01、`develop`のコミット`8a0cff0cb996c2ace2c2032016470bf7f5c6820c`。最新のroute定義から、`web.py`内helper、既存PDF module、database・metadata、資料エクスポートのcallback注入、テンプレート・JavaScript、Python・Playwrightテストまで呼び出し元と呼び出し先を再調査した。

## 背景と目的

R7-1〜R7-3は、外部からPDFまたはScrapbox JSONを取り込む書込み責務をHTTP層から分離する。R7-4は、既存PDFの閲覧、アウトライン・サムネイル、ページ本文検索、単体PDF/Markdown書き出し、server側保存を対象に、HTTP固有処理と再利用可能な処理の境界を確定する。

目的はrouteを別ファイルへ移すことではない。`web.py`に残るFastAPI routeが、入力を検証し、HTTP非依存APIへ値を渡し、戻り値または例外を既存responseへ変換する薄いオーケストレーションになることを目的とする。公開URL、HTTP method、query parameter、status、response body、Content-Type、Content-Disposition、画面導線を維持する。

## 対象ルート一覧

### R7-4の直接対象

| method・URL | 関数 | query/path入力 | 成功response |
|---|---|---|---|
| `GET /pdf/{pdf_path:path}` | `open_pdf` | path `pdf_path` | PDF実ファイルの`FileResponse` |
| `GET /pdf-outline` | `pdf_outline` | required query `pdf_path` | `page_count`と`chapters`のJSON |
| `GET /pdf-thumbnails` | `pdf_thumbnails` | required `pdf_path`, `pages`; optional `size=thumbnail` | JPEGをbase64化した`pages`配列のJSON |
| `GET /export-pdf` | `export_pdf` | required `pdf_path`, `pages` | 選択ページPDFのdownload response |
| `GET /search-pages` | `search_pages` | required `pdf_path`; optional `q=""` | `indexed`とpage hitのJSON |
| `GET /export-md` | `export_markdown` | required `pdf_path`, `pages` | Markdownのdownload response |
| `POST /export-pdf/save` | `save_export_pdf` | required `pdf_path`, `pages` | `saved_path`のJSON |
| `GET /view/{pdf_path:path}` | `view_pdf` | path `pdf_path`; optional integer `page=1` | `pdf_viewer.html` |

### 同じ非HTTP APIを利用する既存route

- `GET /api/packs/{pack_id}/export/preview`: chapter profile時にPDF解決と`pdf_outline.list_chapters`を利用する。
- `GET /api/packs/{pack_id}/export`: `RenderContext`へPDF解決、PDF生成、Markdown生成をcallbackとして注入する。

この2routeの資料取得、profile解決、ExportPlan、ZIP・manifest組立、export event記録はR8の責務でありR7-4へ移さない。R7-4は、R8が注入するPDF操作をHTTP非依存APIへ載せ替えても既存400/404を維持できるadapterだけを定義する。

### R7-1〜R7-3 serviceを呼ぶroute

| R7項目 | route | serviceとの境界 |
|---|---|---|
| R7-1 | `POST /settings/pdf-upload` | `pdf_import_service.save_uploaded_pdf`へ正規化済みfilename、bytes、books_dir、relative_pathを渡す。実装済み |
| R7-2 | `GET /settings/pdf-import` | `pdf_import_service.import_pdfs_from_directory`へsourceとbooks_dirを渡し、結果・専用例外をredirectへ変換する。詳細設計済み・未実装 |
| R7-3 | `GET /settings/scrapbox-import`、`POST /settings/scrapbox-upload` | `scrapbox_import_service`へsourceまたはbytes、cache_path、db_pathを渡し、結果・例外を既存redirect/plain textへ変換する。詳細設計済み・未実装 |

R7-4はこれらのservice処理を再実装・統合しない。HTTP adapterの原則が一貫していることを確認し、R7-2・R7-3実装後の呼び出しを前提として最終的な`web.py`の境界を揃える。

## 各ルートの現在の責務

### PDF閲覧・outline・thumbnail

- `open_pdf`は`_resolve_pdf_file_or_404`でBOOKS_DIR内の実ファイルを解決し、`application/pdf`の`FileResponse`を返す。
- `pdf_outline`はPDFを解決し、`pdf_outline.list_chapters`と`get_page_count`を呼び、`Chapter`をJSON用dictとpage range文字列へ変換する。
- `pdf_thumbnails`はpages空、size、detail単一page形式、60件上限を検証し、`parse_page_selection`をguard page count 10,000で呼ぶ。thumbnail/detailのpresetを選び、既存`pdf_thumbnail` moduleでJPEGを生成し、base64 JSONへ変換する。
- `view_pdf`は`raw_pdf_url`を作り、DBを優先してScrapbox URLを解決し、template contextを組み立てる。

### 単体書き出し・server保存

- `render_pdf_export`はpages必須、実ページ数取得、page spec parse、選択ページPDF生成、filename生成を行うが、`HTTPException`も送出する。
- `render_markdown_export`は同じpage spec検証に加え、DB上の書名・本文を優先し、欠落ページをPDF抽出で補い、現在時刻付きMarkdownとfilenameを生成する。これも`HTTPException`を送出する。
- `save_pdf_export_to_configured_dir`はsave dir検証、PDF解決・生成、保存先境界、重複filename回避、writeを行う。内部でHTTP helperを呼ぶためHTTP非依存ではない。
- `export_pdf`、`export_markdown`は上記戻り値からdownload responseを作る。
- `save_export_pdf`は例外を400/404へ変換し、成功時にpath文字列のJSONを返す。

### ページ本文検索・metadata解決

- `_get_indexed_book`は相対pathとBOOKS_DIR基準の絶対pathの2候補でDB recordを探し、`sqlite3.OperationalError`を未インデックス扱いにする。
- `load_pages_text`はDB本文を優先し、選択pageの不足分だけ`pdf_extract.extract_pages`で補う。
- `search_book_pages`はLIKE wildcardをescapeし、page順・最大100件で検索し、`_page_snippet`で表示用snippetを作る。
- `resolve_pdf_scrapbox_url`はDB recordのURLを優先し、DBが利用できなければexport JSON由来metadataへfallbackする。
- `export_stats._find_indexed_book`には同じ2候補・`OperationalError`抑制が重複している。

## HTTP層に残す責務

- FastAPI decoratorとroute関数。
- path/query/bodyの受け取り、`.strip()`等のHTTP入力正規化、FastAPIによるrequired/type検証。
- `get_books_dir()`、`get_db_path()`、`get_pdf_export_save_dir()`でrequest時の設定を解決し、serviceへ明示的に渡す。
- `paths.resolve_pdf_path`の`None`を404 `PDF not found`へ変換する`_resolve_pdf_file_or_404`。これは名前どおりHTTP adapterなので`web.py`に残す。
- serviceの`ValueError`等を既存400/404へ変換するadapter。
- JSON用dict、base64文字列、template context、`Response`、`FileResponse`、`JSONResponse`の生成。
- Content-Type、Content-Disposition、UTF-8 filenameのquote。
- R8の`RenderContext`へ、既存HTTPエラー契約を保つadapterを注入する処理。
- R7-1〜R7-3 serviceの結果・例外を各route固有の既存応答へ変換する処理。

## HTTP層から移す責務

- page specに基づくPDF生成と出力filename決定。
- PDF exportのserver directory検証、重複回避、ファイル書込み。
- DB recordの2候補検索と、`export_stats`との重複解消。
- DB本文の取得、PDF本文fallback、page内検索、snippet生成。
- Markdown本文とfilenameの生成に必要な非HTTPオーケストレーション。
- PDFに対応するScrapbox URLのDB優先・export JSON fallback。

outline/thumbnailのPDF処理本体は既に`pdf_outline.py`、`pdf_thumbnail.py`へ分離済みなので、新しいserviceへ包み直さない。routeには入力検証とresponse shapingだけが残る。

## 分離先と根拠

### 既存`pdf_export.py`を拡張

現行moduleはpage spec parse、選択ページPDF生成、default output pathをすでに所有する。同じ変更理由である次を追加し、新しい`pdf_export_service.py`は作らない。

```python
class PdfSourceNotFoundError(FileNotFoundError):
    pass


def render_pdf_export(candidate: Path, pages: str) -> tuple[bytes, str]: ...

def save_pdf_export_to_configured_dir(
    pdf_path: str,
    pages: str,
    *,
    books_dir: Path,
    save_dir: Path | None,
) -> Path: ...
```

両関数はFastAPIをimportせず、入力不正を`ValueError`、保存先不存在を通常の`FileNotFoundError`、非directoryを`NotADirectoryError`として返す。保存先の検証後に`paths.resolve_pdf_path`を呼び、PDF解決不可だけを`PdfSourceNotFoundError`に分類する。これにより、保存先とPDFが両方不正な場合も、現在と同じく保存先の400を先に返せる。

### 新規`pdf_metadata_service.py`

PDF pathとDB/export metadataの対応付けを所有する。

```python
def find_indexed_book(
    connection: sqlite3.Connection,
    pdf_path: str | Path,
    *,
    books_dir: Path,
) -> BookRecord | None: ...

def get_indexed_book(
    pdf_path: str | Path,
    *,
    books_dir: Path,
    db_path: Path,
) -> BookRecord | None: ...

def resolve_pdf_scrapbox_url(
    pdf_path: str,
    *,
    books_dir: Path,
    db_path: Path,
    project_root: Path,
) -> str | None: ...
```

`find_indexed_book`を`export_stats.py`と`pdf_text_service.py`から共有し、相対・絶対2候補ロジックを一箇所にする。books/pages table未作成時の`sqlite3.OperationalError`は現在どおり`None`へ変換する。`get_indexed_book`と`resolve_pdf_scrapbox_url`は接続を必ずcloseする。`project_root`は、DBにURLがない場合にだけ現行`find_export_json(project_root)`と`load_metadata_by_pdf_stem`を遅延実行するために受け取る。moduleはdatabase・metadata・pathsへ依存するがFastAPIとwebへ依存しない。

### 新規`pdf_text_service.py`

既存PDFの索引済みpage text、PDF抽出fallback、page検索、Markdown生成を所有する。

```python
@dataclass(frozen=True)
class PdfPageSearchHit:
    page_number: int
    snippet: str


@dataclass(frozen=True)
class PdfPageSearchResult:
    indexed: bool
    pages: tuple[PdfPageSearchHit, ...]


def load_pages_text(
    candidate: Path,
    page_numbers: list[int],
    *,
    books_dir: Path,
    db_path: Path,
) -> dict[int, str]: ...


def search_book_pages(
    candidate: Path,
    query: str,
    *,
    books_dir: Path,
    db_path: Path,
    limit: int = 100,
) -> PdfPageSearchResult: ...


def render_markdown_export(
    candidate: Path,
    pages: str,
    *,
    books_dir: Path,
    db_path: Path,
    exported_at: datetime,
) -> tuple[str, str]: ...
```

`exported_at`を引数にすることでservice内にwebの`_now_jst`を持ち込まない。web adapterは現在と同じ呼び出し時点のJSTを渡す。Markdownのformat自体は既存`markdown_export.render_markdown_pages`に維持する。

## データ構造と戻り値

- PDF/Markdown exportは既存`RenderContext`のcallback型と互換な2要素tupleを維持する。位置0がcontent、位置1がdownload用filename。
- `load_pages_text`は1-based page番号をkey、本文をvalueとするdictを維持する。DBと抽出のどちらにも存在しないpageはkeyを持たない。
- page検索だけはHTTP dictから`PdfPageSearchResult`へ変え、serviceがJSON構造を所有しないようにする。webが`{"indexed": ..., "pages": [...]}`へ変換する。
- `Chapter`、thumbnailの`list[tuple[int, bytes]]`は既存moduleの契約を維持し、webがJSONへ変換する。
- `BookRecord`はdatabaseの既存型をそのまま返し、新しい重複DTOを作らない。

## ルートごとの処理フロー

### `GET /pdf/{pdf_path:path}`

1. webがbooks_dirを取得しPDFを404 adapterで解決する。
2. webがcandidateを`FileResponse(media_type="application/pdf")`へ渡す。

ファイルstreamingとrange等のStarlette挙動をserviceでbytes化しない。

### `GET /pdf-outline`

1. required queryをFastAPIが検証し、webがPDFを解決する。
2. 既存`list_chapters`と`get_page_count`を呼ぶ。
3. webがchapter fieldと`pages`表示文字列をJSONへ写像する。

### `GET /pdf-thumbnails`

1. webがPDFを解決する。現在どおりpages/size検証より先に404判定する。
2. webがpages空、size、detail形式、最大60件を検証し、parseの`ValueError`を400へ変換する。
3. 既存thumbnail moduleへpreset値を渡す。
4. detail範囲外の`None`を404 `page not found`へ変換し、JPEG bytesをbase64 JSONにする。

### `GET /export-pdf`

1. webがPDFを解決する。
2. `pdf_export.render_pdf_export`をHTTP adapter経由で呼び、`ValueError`を400へ変換する。
3. webが`application/pdf`とUTF-8 Content-Dispositionを付ける。

### `GET /search-pages`

1. webが`q.strip()`する。
2. 空queryなら現在どおりPDF存在確認もDB接続も行わず、200 `{"indexed": true, "pages": []}`を返す。
3. 非空ならPDFを解決し、`pdf_text_service.search_book_pages`へ値を渡す。
4. 結果dataclassを既存JSONへ変換する。

### `GET /export-md`

1. webがPDFを解決する。
2. HTTP adapterが`pdf_text_service.render_markdown_export`へ現在時刻、books_dir、db_pathを渡し、`ValueError`を400へ変換する。
3. webが`text/markdown; charset=utf-8`とUTF-8 Content-Dispositionを付ける。

### `POST /export-pdf/save`

1. webが`pdf_export.save_pdf_export_to_configured_dir(pdf_path, pages, books_dir=..., save_dir=...)`を呼ぶ。service内は現在どおりsave dirを先に検証し、その後にPDFを解決する。
2. `PdfSourceNotFoundError`を404 `PDF not found`へ変換する。
3. 通常の`FileNotFoundError`、`NotADirectoryError`、`ValueError`を既存400 detailへ変換する。
4. webが200 `{"saved_path": str(path)}`を返す。

### `GET /view/{pdf_path:path}`

1. webがbooks_dir/db_pathを取得し、`raw_pdf_url`でPDF URLを作る。`None`を404へ変換する。
2. `pdf_metadata_service.resolve_pdf_scrapbox_url`へ`PROJECT_ROOT`も渡して呼ぶ。
3. webが既存6 fieldのtemplate contextを組み立てる。

## リクエスト契約

- required query省略、`page`等の型不正はFastAPIの既存422形式を維持する。
- `pages`は`pdf_export.parse_page_selection`の現行文法を維持する。comma、単一page、closed/open range、重複除去、入力順を変更しない。
- `q`は前後空白だけを除去し、case-insensitive LIKE検索と`%`、`_`、backslash escapeを維持する。
- thumbnailの`size`は`thumbnail|detail`、detailはASCIIの正整数1件のみ、1request最大60pageを維持する。
- viewerの`page`はFastAPI integer変換以外の範囲検証を追加しない。

## レスポンス契約とstatus code

| route | 正常 | 現在固定される失敗 |
|---|---|---|
| `/pdf/{pdf_path}` | 200 `application/pdf` FileResponse | PDF解決不可は404 `PDF not found` |
| `/pdf-outline` | 200 JSON。読めないPDFは既存lower moduleにより`page_count: null`, `chapters: []`になり得る | PDF解決不可404 |
| `/pdf-thumbnails` | 200 `{"pages":[{"page": int,"data": base64}]}` | 入力不正400、detail範囲外404、PDF解決不可404 |
| `/export-pdf` | 200 `application/pdf` attachment | pages不正400、PDF解決不可404 |
| `/search-pages` | 200 JSON | 非空queryでPDF解決不可404。未索引/DB OperationalErrorは200 `indexed:false` |
| `/export-md` | 200 `text/markdown; charset=utf-8` attachment | pages不正400、PDF解決不可404 |
| `/export-pdf/save` | 200 `saved_path` JSON | PDF解決不可404、save dir/pages不正400 |
| `/view/{pdf_path}` | 200 HTML | PDF URL解決不可404 |

PDF parser、fitz、filesystem、SQLiteの上表以外の例外は新たに包括変換せず、現在どおり500になり得る。R7-4で新しいerror envelopeを導入しない。

## 例外変換表

| service/lower moduleの値・例外 | webでの変換 |
|---|---|
| `paths.resolve_pdf_path(...) is None` | 404 `HTTPException(detail="PDF not found")` |
| PDF/Markdown serviceの空pages `ValueError` | 400 `pages is required` |
| `parse_page_selection`の`ValueError` | 400、既存`str(exc)` |
| detail thumbnailの`None` | 404 `page not found` |
| save serviceの`PdfSourceNotFoundError` | 404 `PDF not found` |
| save dirの`FileNotFoundError` | 400 `保存先フォルダが存在しません: ...` |
| save dirの`NotADirectoryError` | 400 `保存先がフォルダではありません: ...` |
| save serviceのその他`ValueError` | 400、既存`str(exc)` |
| indexed book/page queryの`sqlite3.OperationalError` | service内で未索引・DB本文なしとして扱う |
| その他の例外 | 変換せず伝播 |

serviceはFastAPIをimportしない。R8 callbackでは、webに残す`render_pdf_export`・`render_markdown_export`の薄いHTTP adapterを注入し、資料エクスポートの既存400/404を維持する。

## ファイルレスポンスとファイル書込み

- 原本PDFは`FileResponse`のpath渡しを維持し、serviceで全bytesを読み込まない。
- 選択PDFとMarkdownは現在どおりメモリ上で生成して`Response`へ渡す。streamingへ変更しない。
- Content-Dispositionは`attachment; filename*=UTF-8''{quote(filename)}`を維持する。
- server保存は設定済みdirectoryを`expanduser().resolve()`し、存在・directoryを検証する。serviceが返すfilenameは`Path(filename).name`でbasename化し、resolve後にsave root配下であることを確認する。
- 衝突時は`paths.unique_export_destination_path`の`_2`〜規則を維持する。atomic write、一時ファイル、lockは導入しない。
- pack exportのZIP bytes、manifest、entry filename、event記録はR8のままとする。

R7-4の現行処理は一時ファイルを作らない。`FileResponse`のファイルhandle管理はStarletteへ委ね、メモリ生成responseにcleanup対象はない。server保存もdestinationへ直接`write_bytes`するため、中途書込みの既知リスクを維持する。

直接対象8routeはredirectを返さない。R7-2・R7-3の設定routeが返す303は各専用設計のHTTP adapter契約であり、R7-4で共通redirect helperへまとめない。

## R7-1〜R7-3公開APIとの対応

- R7-1の`save_uploaded_pdf`はPDFをBOOKS_DIRへ持ち込む。R7-4は配置済みPDFを`paths.resolve_pdf_path`で読むだけで、upload保存を呼ばない。
- R7-2の`import_pdfs_from_directory`完了後もindex更新は別操作である。R7-4の検索/Markdownは未索引PDFを既存どおり`indexed:false`またはPDF抽出fallbackとして扱う。
- R7-3のScrapbox同期結果はDB/cacheへ反映される。R7-4の`pdf_metadata_service.resolve_pdf_scrapbox_url`はそのDBまたはexport metadataを読み取るが、R7-3 serviceを呼ばず同期副作用を起こさない。
- 各serviceの例外を共通基底へ統合しない。upload、directory import、Scrapbox import、PDF閲覧では外部HTTP契約が異なるため、各route adapterが個別に変換する。

## 依存方向

```text
templates / static JavaScript
  -> web.py (FastAPI入力、HTTP例外変換、Response/template生成)
      -> paths.py
      -> pdf_outline.py / pdf_thumbnail.py
      -> pdf_export.py
      -> pdf_text_service.py
          -> pdf_metadata_service.py
          -> database.py / pdf_extract.py / pdf_export.py / markdown_export.py
      -> pdf_metadata_service.py
          -> database.py / metadata.py / paths.py
      -> R7-1 pdf_import_service.py
      -> R7-2 pdf_import_service.py (実装後)
      -> R7-3 scrapbox_import_service.py (実装後)

export_stats.py -> pdf_metadata_service.py
export_profiles.py -> webが注入するadapter callable
```

下位moduleは`web.py`、FastAPI、template、JavaScriptをimportしない。`database.py`・`metadata.py`も新serviceをimportしない。`pdf_text_service -> pdf_metadata_service`の一方向だけを許し、逆依存を作らない。

## 移動対象となる関数

- `render_pdf_export`本体 -> `pdf_export.py`。`web.py`にはValueErrorを400へ変換する薄い同名adapterを残す。
- `save_pdf_export_to_configured_dir`本体 -> `pdf_export.py`。serviceが現行順序でsave dir検証とPDF解決を行い、webのrouteは専用例外を404、保存先例外を400へ変換する。
- `_get_indexed_book` -> `pdf_metadata_service.get_indexed_book`。
- `export_stats._find_indexed_book`の重複本体 -> `pdf_metadata_service.find_indexed_book`呼び出しへ置換。
- `load_pages_text`、`_page_snippet`、`search_book_pages` -> `pdf_text_service.py`。
- `render_markdown_export`本体 -> `pdf_text_service.py`。`web.py`には時刻注入とValueError変換を行う薄い同名adapterを残す。
- `resolve_pdf_scrapbox_url` -> `pdf_metadata_service.py`。web固有の探索基準は`project_root`引数で渡す。

`_resolve_pdf_file_or_404`はHTTP固有なので移動しない。outline/thumbnailの既存module関数も移動しない。

## `web.py`に残す処理

- 対象routeとR8 route。
- `_resolve_pdf_file_or_404`。
- thumbnailのHTTP入力制約・preset選択・base64 JSON化。
- outlineのJSON shape生成。
- PDF/Markdownのdownload headers。
- service例外の400/404変換。
- viewer template context。
- R8へのcallback組立、資料・profile・ZIP・export eventの既存処理。
- R7-1〜R7-3のroute adapter。

R7-4を「残ったもの全部」の置き場所にはしない。設定、資料CRUD、検索結果画面整形、pack export plan、cache import、PDF取込み、DB schema/CRUDはそれぞれ既存責務に維持する。

## テスト方針

### 下位moduleテスト

- `tests/test_pdf_export.py`を新設し、page spec、PDF bytes/filename、save dir検証、境界、重複名、write失敗を直接検証する。
- `tests/test_pdf_metadata_service.py`を新設し、相対/絶対DB path候補、OperationalError、DB優先Scrapbox URL、export metadata fallback、接続closeを検証する。
- `tests/test_pdf_text_service.py`を新設し、DB本文優先、page単位fallback、未索引、LIKE escape、limit/order/snippet、Markdown title/本文/filename/時刻を検証する。
- `export_stats`の既存テストで共有`find_indexed_book`への載せ替え後も寛容な未索引判定を確認する。

現在`tests/test_web.py::HighlightQueryTest`に同居するhelper中心テストは所有moduleへ移す。同じロジックのassertをwebとserviceへ重複して残さない。

### HTTP契約テスト

`tests/test_web.py`では各routeの入力、status、body shape、header、例外変換だけを維持・補完する。

- PDF missing 404を対象8routeで確認する。
- required query省略の422、空pagesの400、page range文言をTestClientで固定する。
- `/search-pages`の空queryがPDF存在確認前に200を返す順序を固定する。
- thumbnail size/detail/60件上限/base64 shapeを維持する。
- PDF/Markdown Content-Type・Content-Disposition・UTF-8 filenameを維持する。
- save routeの3分類と`saved_path`を維持する。
- viewer contextとScrapbox link fallbackを維持する。
- pack exportの既存backward compatibility testでZIP構造、entry内容、400/404が変わらないことを確認する。

既存Playwrightの`pdf_modal_overlay.spec.js`、`workspace_add_pdf.spec.js`がoutline、thumbnail、page検索、PDF modal導線を覆う。UIを変更しないためspec追加は必須としないが、実装PRではPython・Playwright全件を実行する。

## 実装手順

1. 対象routeの不足するHTTP characterization testを追加する。
2. `pdf_export.py`へHTTP非依存のPDF生成・保存APIを追加し、下位テストを移す。
3. `pdf_metadata_service.py`を追加し、webと`export_stats.py`の2候補検索を共通化する。
4. `pdf_text_service.py`を追加し、DB本文・fallback・検索・Markdown生成を移す。
5. `web.py`を薄いadapterとresponse生成へ置き換え、R8 `RenderContext`注入契約を維持する。
6. route/API一覧、循環import、Python全件、Playwright全件、`git diff --check`を確認する。

characterization test、PDF export、metadata共通化、text service、web接続は目的別commitに分けられる。実装PRの分割単位は差分量に応じて決めるが、パッケージ・directory移動は行わない。

## 完了条件

- 対象8routeがHTTP入力・変換・response生成中心になっている。
- 非HTTP helper本体が`pdf_export.py`、`pdf_metadata_service.py`、`pdf_text_service.py`へ責務どおり分離されている。
- serviceがFastAPI型・HTTPException・Responseを使用しない。
- R8が同じPDF/Markdown処理をcallback利用し、ZIP・manifest・error契約が維持される。
- R7-1〜R7-3 routeが各serviceを一方向に呼び、非HTTP処理がwebへ重複していない。
- 外部URL、HTTP method/query/status/body/header、CLI、template、JavaScriptの振る舞いが変わらない。
- Python・Playwright全件が成功し、循環importがない。
- ROADMAPはR7-4だけを実装済みに更新する。R7親項目はR7-2・R7-3を含む全子責務の実装完了まで未完了とする。

## 非目標

- R7-4の実装・テスト変更（本PRは設計のみ）。
- routeのAPIRouter分割、URL・method・query名変更。
- R8のExportPlan、ZIP、manifest、profile、export eventの再設計。
- PDF parser、outline、thumbnail、extract、Markdown formatのアルゴリズム変更。
- streaming export、file size/page count上限の統一、cache、非同期job化。
- atomic save、並行書込みlock、一時ファイル導入。
- DB schema、transaction、search SQL、metadata parserの変更。
- R7-1〜R7-3の実装または公開API変更。
- CLI command追加、テンプレート・JavaScript・CSS変更。
- backend package構成見直し。これはR7と`database.py`の責務分離完了後に行う。

## 実装着手時の停止条件

- 本文記載のstatus/body/headerを維持できない。
- R7-1〜R7-3、R8、database.pyの公開契約変更が必要になる。
- `export_profiles.RenderContext`のcallback型変更が必要になる。
- PDF処理module間またはdatabaseとの循環依存が生じる。
- serviceへFastAPI型を渡す必要が生じる。
- 現行コード・テストと本設計の観測事実が一致しない。
- PythonまたはPlaywrightの必須checkが失敗する。
