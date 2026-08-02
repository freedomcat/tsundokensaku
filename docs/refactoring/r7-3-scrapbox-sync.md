# R7-3: Scrapbox JSON保存・同期の詳細設計

[R7全体設計](../central-file-refactoring-inventory.md) / [R7-2詳細設計](r7-2-pdf-directory-import.md) / [ROADMAP](../../ROADMAP.md)

状態: 実装済み（2026-08-02、ブランチ`refactor/r7-3-scrapbox-import`）。

調査基準: 2026-08-01、`develop`のコミット`6d39d24526a63506cebd0cb595aeca0c9904a585`。検索語だけで範囲を決めず、2つのHTTP入口から`web.py`、`database.py`、`metadata.py`、設定画面のJavaScript、CLI補助スクリプト、既存テストまで呼び出し元と呼び出し先を追跡した。

## 背景と目的

`web.py`には、Scrapbox/Cosense export JSONを`PROJECT_ROOT/shino-books_imported.json`へ保存し、SQLiteへメモとKindle本を同期する処理がある。`POST /settings/scrapbox-upload`は`import_scrapbox_export_bytes`を経由する一方、`GET /settings/scrapbox-import`は同等の保存・DB同期をルート内に重複して持つ。

R7-3では、このファイル保存とDB同期のオーケストレーションを専用モジュールへ移す。`web.py`にはFastAPI固有の入力受け取り、demo mode、レスポンス生成、既存例外のHTTP変換だけを残す。公開URL、HTTP method、画面動作、JSON解釈、DB schema、既存の同期順序は変えない。

## 現在の責務と呼び出し経路

### HTTP入口

| 入口 | 現在の関数 | 入力 | 現在の処理 |
|---|---|---|---|
| `GET /settings/scrapbox-import` | `web.import_scrapbox_json` | query `export_json_path: str = ""` | demo mode判定、DB接続・初期化、source選択、固定キャッシュへのコピー、メモ同期、Kindle同期、303 redirect |
| `POST /settings/scrapbox-upload` | `web.upload_scrapbox_json` | query `filename: str = ""`、raw request body | demo mode判定、filename・拡張子・空body検証、`import_scrapbox_export_bytes`呼び出し、成功または失敗のplain text応答 |

設定画面の`templates/settings_index.html`は、GET formから`export_json_path`を送り、dropzoneでは最初の`.json`ファイルだけを`POST /settings/scrapbox-upload?filename=...`へraw bodyで送る。成功したPOSTの本文を`/settings?message=...`へ渡し、失敗時はレスポンス本文を画面内に表示する。このJavaScriptとテンプレートはR7-3では変更しない。

### 現在の共通処理

`web.import_scrapbox_export_bytes(content, db_path)`は次を順番に行う。

1. `SCRAPBOX_EXPORT_CACHE`へ`Path.write_bytes`で直接上書きする。
2. `database.connect(db_path)`で接続する。
3. `database.initialize(connection)`でschemaを保証する。
4. `database.sync_memos(connection, cache_path)`でメモとFTSを全置換する。
5. `database.sync_kindle_books(connection, cache_path)`でKindle本をupsertする。
6. `finally`で接続を閉じ、位置依存tuple `(imported, imported_kindle)`を返す。

GET入口は同じ手順をルート内に再実装している。ただし、DB接続・初期化をsource存在確認より先に行い、例外時の`finally`がないという差がある。

### 下位モジュールの責務

- `metadata.find_export_json(project_root)`は`SCRAPBOX_EXPORT_JSON`が存在すればそれを選び、なければ`project_root`直下の`shino-books_*.json`からmtimeが最新のものを返す。CLIとindexerも利用するため移動しない。
- `metadata.load_scrapbox_memos`はJSONの`pages`をメモへ変換する。`lines`は文字列と`{"text": ...}`の両形式を扱う。
- `metadata.load_kindle_books`は`#Kindle`と`#技術書`を含みASINを抽出できるページをKindle本へ変換し、同一ASINを入力内で重複排除する。
- `database.sync_memos`はcacheの絶対pathとmtimeが`memo_sources`の記録に完全一致すると0件で終了する。それ以外はメモ・FTSを全置換し、source情報を更新する。
- `database.sync_kindle_books`はKindle本を`(source_type, external_id)`でupsertする。新しいexportに存在しない既存Kindle本は削除しない。

## 分離対象

新規`src/tsundokensaku/scrapbox_import_service.py`へ次を移す。

- upload byte列の固定キャッシュへの保存。
- path指定exportの読取りと、sourceが固定キャッシュと異なる場合のコピー。
- SQLite接続の取得・初期化・close。
- `sync_memos`、`sync_kindle_books`の順次呼び出し。
- 同期件数を名前付き結果へまとめる処理。
- source不存在をHTTP非依存の例外として通知する処理。

モジュール名は、現行関数`import_scrapbox_export_bytes`、設定画面の「インポート」、既存候補名`scrapbox_import_service.py`に合わせる。汎用的な`service.py`や、外部APIとの双方向通信を連想させる`sync_client.py`は採用しない。実際の処理はローカルJSONのimportであり、Scrapbox/CosenseへHTTP通信しない。

## 分離しない責務

- `metadata.py`のJSON解析、URL生成、PDF metadata対応付け。
- `database.py`のメモ全置換、FTS更新、Kindle upsert、schema、commit境界。
- `metadata.find_export_json`の環境変数と最新ファイル選択。CLI・indexerとの共有処理として現位置を維持する。
- `scripts/import_books_from_cosense.py`。これはBookscan PDFの探索・改名・コピー用の独立CLIで、メモ・KindleのDB同期を行わない。
- 検索結果からScrapboxページを開く処理、`search_view.py`の表示整形、`resolve_pdf_scrapbox_url`。
- FastAPIのroute、request body読取り、query parameter検証、redirect・plain text生成、demo mode判定。

## 公開API

```python
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ScrapboxImportResult:
    imported_memos: int
    imported_kindle_books: int


class ScrapboxExportNotFoundError(ValueError):
    pass


def import_scrapbox_export_bytes(
    content: bytes,
    *,
    cache_path: Path,
    db_path: Path,
) -> ScrapboxImportResult: ...


def import_scrapbox_export_file(
    source: Path | None,
    *,
    cache_path: Path,
    db_path: Path,
) -> ScrapboxImportResult: ...
```

2関数は入力元だけが異なり、保存後のDB同期を非公開helperで共有する。一つの`bytes | Path` union引数にはせず、呼び出し側が選ぶ処理と型を明確にする。

`cache_path`と`db_path`は引数で受け取る。serviceが`web.PROJECT_ROOT`、`SCRAPBOX_EXPORT_CACHE`、`config.get_db_path()`を参照すると`service -> web/config`の逆依存とテスト時のglobal patchを生むためである。

既存の`web.import_scrapbox_export_bytes`はリポジトリ内では`tests/test_web.py`から直接importされるだけで、他のproduction moduleやmonkeypatchから参照されない。実装時にテストをservice側へ移し、`web.py`に互換wrapperは残さない。

## データ構造と既存データ契約

- service入力の`content`はraw `bytes`。UTF-8・JSON・`pages`構造の検証をservice独自に重複実装せず、既存`metadata` parserへ委ねる。
- JSON top-levelは現在dictを前提に`.get("pages", [])`を呼ぶ。`pages`省略時は空配列相当、未知fieldは無視される。top-levelやpageが期待型でない場合の例外は、現行parserからの例外をそのまま扱う。
- `ScrapboxImportResult`は現在のtupleの2値を名前付きにするだけで、件数の意味を変えない。
- `imported_memos`は`sync_memos`の戻り値であり、mtime一致によるskip時は0。`imported_kindle_books`は今回のexportから解析してupsertした件数で、DBに新規作成された件数だけを表す値ではない。

## 正常系の処理フロー

### byte列からのimport

1. 呼び出し元が`content`、固定`cache_path`、`db_path`を渡す。
2. serviceが`cache_path.write_bytes(content)`で直接上書きする。
3. 共通同期helperがDBへ接続し、schemaを初期化する。
4. `sync_memos`、`sync_kindle_books`の順に呼ぶ。
5. 接続を閉じ、`ScrapboxImportResult`を返す。

### pathからのimport

1. serviceが`source is None`または`not source.exists()`を`ScrapboxExportNotFoundError`に分類する。
2. `source.expanduser().resolve()`でsourceを解決する。
3. sourceと`cache_path`が異なる場合だけ`source.read_bytes()`と`cache_path.write_bytes()`でコピーする。同一の場合は再書込みせず、現在のmtime判定を維持する。
4. byte列経路と同じ共通同期helperを呼ぶ。
5. 結果を返す。

source不存在の確認をDB接続より前に置く。現行GETは不存在時にもDBファイル・親directoryを作成し得るが、これは公開HTTP契約ではなく、分離後は不要な副作用を起こさない。redirect先・status・messageは維持する。

## エラー時の処理フローと例外契約

serviceはFastAPIをimportせず、`HTTPException`、`Response`、redirect用messageを生成しない。

| 発生箇所 | serviceからの例外 | HTTP変換 |
|---|---|---|
| sourceが`None`または存在しない | `ScrapboxExportNotFoundError` | GETのみ既存の「Scrapbox の export JSON が見つかりませんでした」を持つ303 redirect |
| source読取り、cache書込み | `OSError`の具体型を保持 | GETは現行どおり未捕捉、POSTは既存の包括catchで400 plain text |
| UTF-8 decode・JSON parse | `UnicodeDecodeError`、`json.JSONDecodeError` | GETは現行どおり未捕捉、POSTは既存の包括catchで400 plain text |
| JSON構造・parser処理 | 現行`metadata`関数が送出する`TypeError`、`AttributeError`等 | GETは未捕捉、POSTは400 plain text |
| DB接続・schema・同期 | `RuntimeError`、`sqlite3.Error`等 | GETは未捕捉、POSTは400 plain text |

新しい包括例外やエラー文言の正規化は導入しない。POSTが`str(exc)`を返す現状には内部pathやOS詳細が露出し得るが、文言を安全な固定値へ変えると外部応答契約が変わるため、R7-3実装とは分けて扱う。GETの未捕捉例外を303へ変換することも非目標とする。

DB接続は、接続成功後の全経路で`finally`によりcloseする。これは既存byte列helperの契約を共通化するもので、GETの例外時connection leakは維持しない。

## ファイル・パスの安全性

- uploadの`filename`は表示と`.json`拡張子検証にだけ使い、保存先の構築には使わない。保存先は常に`web.py`から渡す固定`SCRAPBOX_EXPORT_CACHE`とする。
- path importは利用者が指定したserver filesystem上のpathを読む既存機能であり、通常modeではproject root配下へ制限しない。demo modeではserviceを呼ぶ前に拒否する。
- `expanduser()`と`resolve()`によりsymlinkを追跡する現行挙動を維持する。source symlink拒否や許可root制限は新しいセキュリティ仕様になるため本PRでは導入しない。
- `cache_path.parent`の自動作成は行わない。現行固定pathの親であるproject rootは存在する前提で、失敗時は`OSError`を伝播する。
- cacheはJSON parse前に保存される。したがって不正JSONでも既存cacheを上書きし得る。この順序をcharacterization testで固定し、責務分離とatomic化・事前検証を混在させない。

## 一時ファイルのライフサイクル

現行処理は一時ファイルを作らず、`Path.write_bytes`でcacheを直接上書きする。R7-3でも一時ファイル、rename、backupは導入しない。書込み途中の失敗でcacheが部分状態になり得ることを既知リスクとして残す。atomic writeと失敗時復元は、公開失敗挙動・並行実行・cleanupをまとめて設計する別課題とする。

## DB更新と部分成功

現在の下位APIは一つのtransactionに統合されていない。

1. `initialize`がcommitする。
2. `replace_memos`がメモとFTSの全置換をcommitする。
3. `sync_memos`が`memo_sources`更新を別commitする。
4. `sync_kindle_books`がKindle upsertをcommitする。

したがって後半で失敗しても、cache保存や先行commitは残る。R7-3 serviceは新しいtransactionを張らず、commit/rollbackを横取りしない。transaction統合には`database.py`の公開契約変更が必要になるため、database責務分離または専用のデータ整合性改善として別途設計する。

## 既存HTTP契約との対応

### `GET /settings/scrapbox-import`

- method・URL・query名とデフォルト値を維持する。
- demo modeはserviceを呼ばず、`/settings?message=...`への303を返す。
- 空入力時は`find_export_json(PROJECT_ROOT)`を使う。明示path時は`Path(value).expanduser()`を渡す。
- source不存在時の303と日本語messageを維持する。
- 成功時は`Scrapbox JSON を同期しました: メモ {n} 件 / Kindle {n} 件 ({source.name})`を持つ`/settings`への303を維持する。
- その他の例外を新たにredirectへ変換しない。

### `POST /settings/scrapbox-upload`

- demo modeの403と`Upload is disabled in demo mode.`を維持する。
- filename空の400 `filename が必要です`、大小文字を区別しない`.json`検証と400 `JSON ファイルのみ受け付けます`を維持する。
- body空の400 `empty body`を維持する。
- service例外を包括catchし、`str(exc)`の400 plain textへ変換する現状を維持する。
- 成功時の201、content-type、件数・filenameを含む本文を維持する。

## 依存方向

```text
templates/settings_index.html
  -> web.py (FastAPI入力、demo mode、HTTP応答)
      -> metadata.find_export_json (既存source選択)
      -> scrapbox_import_service.py
          -> database.connect / initialize / sync_memos / sync_kindle_books
              -> metadata.load_scrapbox_memos / load_kindle_books
          -> pathlib / filesystem
```

`scrapbox_import_service.py`は`web.py`、FastAPI、templates、JavaScript、`config.py`をimportしない。`database.py`と`metadata.py`もserviceをimportしないため循環しない。

## 移動対象となる関数と処理

- `web.import_scrapbox_export_bytes`はserviceへ移し、tuple返却を`ScrapboxImportResult`へ置き換える。
- `web.import_scrapbox_json`内のsource存在確認後の読取り・cacheコピー・DB接続・初期化・2同期・closeを`import_scrapbox_export_file`へ移す。
- 2経路に共通するDB同期とcloseをservice内の非公開helperへまとめる。
- `web.py`から`sync_memos`、`sync_kindle_books`の直接importを除く。

## `web.py`に残す処理

- 2つのroute定義と関数名。
- demo modeの早期return。
- GETのquery受取り、default source選択、成功・不存在message、303 redirect。
- POSTのfilename、拡張子、request body検証。
- service結果から既存本文を組み立てる処理。
- service例外から既存HTTP応答への変換。
- `SCRAPBOX_EXPORT_CACHE`の配置決定とserviceへの引数渡し。
- 設定画面contextの`default_export_json`。

## R7-2、R7-4との境界

- R7-2はPDF source directoryからBOOKS_DIRへの複数PDFコピーだけを`pdf_import_service.py`で所有する。R7-3はScrapbox JSONの固定cache保存とDB同期だけを所有し、BOOKS_DIR、PDF列挙、destination衝突処理、`paths.py`へ依存しない。
- R7-2のsymlink拒否・destination境界・専用例外5分類はPDF directory import固有であり、R7-3へ機械的に流用しない。
- R7-4にはPDF閲覧・変換・本文検索と、それらのHTTPオーケストレーションを残す。`resolve_pdf_scrapbox_url`は既存PDF表示のmetadata解決なのでR7-4で扱い、R7-3へ移さない。
- `GET /settings/scrapbox-import`と`POST /settings/scrapbox-upload`のHTTP薄層はR7-3実装後も`web.py`に残るが、その非HTTP処理をR7-4へ再分類しない。

## CLIとの関係

`src/tsundokensaku/cli.py`と`indexer.py`は`find_export_json`とPDF title metadataを共有するだけで、メモ・Kindle DB同期を呼ばない。`scripts/import_books_from_cosense.py`はPDFコピー用の別ツールである。R7-3でCLI commandを追加せず、既存CLIを新serviceへ接続しない。将来DB同期CLIが必要になった場合は同じ公開serviceを利用できるが、現時点の契約には含めない。

## テスト方針

実装時は、まず現在挙動を固定するcharacterization commit、その後にservice移動commitを分ける。

### service単体・統合テスト

新規`tests/test_scrapbox_import_service.py`で次を確認する。

- byte列が指定cacheへそのまま保存され、メモ・Kindle件数が名前付き結果で返る。
- file経路が別sourceをcacheへコピーし、同一source/cacheでは再書込みしない。
- source `None`・不存在が`ScrapboxExportNotFoundError`になる。
- 不正UTF-8、不正JSON、期待外top-levelで既存parser例外が伝播し、不正contentがcacheに残る現在順序。
- `sync_memos`後に`sync_kindle_books`が失敗した場合のcache・メモ残存という部分成功。
- 例外時にも接続をcloseする。
- `SCRAPBOX_BASE_URL`あり・なしのURL生成は既存metadata/databaseテストへ委ね、service側で重複しない。

既存`tests/test_web.py::HighlightQueryTest.test_import_scrapbox_export_bytes_syncs_metadata`相当はserviceテストへ移す。service APIに対するテストを`test_web.py`へ重複して残さない。

### HTTP契約テスト

`tests/test_web.py`では次をroute単位で固定する。

- GET: demo mode、source不存在、明示source成功、default source成功、source/cache同一、成功303のlocationとmessage。
- POST: demo mode、filename空、非JSON拡張子、空body、成功201、service例外400。
- demo modeでserviceが呼ばれず、DB/cache副作用がない。
- routeがserviceへ`SCRAPBOX_EXPORT_CACHE`と`get_db_path()`を渡す。

既存のmetadata parser、memo検索、Kindle upsertのテストは各所有moduleに残す。画面契約を変えないためPlaywright test追加は必須とせず、Python全件と既存Playwright全件を実装PRで実行する。

## 実装手順

1. GET/POSTの不足するHTTP characterization testと、失敗順序・部分成功を固定するservice候補テストを追加する。
2. `scrapbox_import_service.py`へ結果型、source不存在例外、byte/file公開関数、共通同期helperを追加する。
3. 既存helperテストをservice testへ移し、`web.py`の直接DB同期importを除く。
4. GET/POST routeをservice呼び出しへ置き換え、既存HTTP応答を維持する。
5. Python全件、Playwright全件、循環import確認、`git diff --check`を実行する。

実装コミットは「現行契約のテスト固定」と「service分離」の2目的に分ける。パッケージ構成見直しやmodule移動はR7と`database.py`の責務分離完了後まで行わない。

## 完了条件

- 2つのHTTP入口が同じserviceの保存・DB同期処理を利用し、重複がない。
- `web.py`がScrapbox cacheの読書き、DB接続・初期化、`sync_memos`、`sync_kindle_books`を直接実行しない。
- serviceがFastAPI型とHTTP例外を使用しない。
- cache、source選択、DB同期順序、件数の意味、部分成功が文書とテストで一致する。
- 既存URL、method、query、status、redirect、plain text、画面導線が維持される。
- R7-2とR7-4の責務を取り込まない。
- Python・Playwright全件が成功し、循環importがない。
- ROADMAPはR7-3だけを実装済みに更新し、R7親項目・R7-2・R7-4を未完了のまま維持する。

## 実装結果

実装日: 2026-08-02

実装ブランチ: `refactor/r7-3-scrapbox-import`

新設したサービス: `src/tsundokensaku/scrapbox_import_service.py`

公開API:

- `ScrapboxImportResult(imported_memos: int, imported_kindle_books: int)`
- `ScrapboxExportNotFoundError(ValueError)`
- `import_scrapbox_export_bytes(content: bytes, *, cache_path: Path, db_path: Path) -> ScrapboxImportResult`
- `import_scrapbox_export_file(source: Path | None, *, cache_path: Path, db_path: Path) -> ScrapboxImportResult`

`web.py`に残した責務:

- `GET /settings/scrapbox-import`と`POST /settings/scrapbox-upload`のroute定義。
- demo mode判定。
- GET queryの受け取り、空入力時の`find_export_json(PROJECT_ROOT)`、明示path時の`Path(...).expanduser()`。
- POSTのfilename空チェック、`.json`拡張子チェック、空bodyチェック。
- `SCRAPBOX_EXPORT_CACHE`と`get_db_path()`の決定。
- service結果から既存の303 redirectまたは201 plain textを組み立てる処理。
- `ScrapboxExportNotFoundError`をGETの既存303 redirectへ変換する処理。
- POSTでservice例外を既存どおり400 plain textの`str(exc)`へ変換する処理。

`web.py`から移動した責務:

- Scrapbox export JSONの固定cacheへの直接保存。
- 指定source JSONの読取りと固定cacheへのコピー。
- DB接続、DB初期化、`sync_memos`、`sync_kindle_books`、DB接続close。
- 同期件数を結果として返す処理。
- source不存在をHTTP非依存の例外で表す処理。

追加・移動したテスト:

- `tests/test_web.py::ScrapboxImportHttpCharacterizationTest`を追加し、GET/POSTのHTTP契約、demo mode早期return、serviceへ固定cache pathとDB pathを渡す契約を固定した。
- 旧`tests/test_web.py::HighlightQueryTest.test_import_scrapbox_export_bytes_syncs_metadata`相当は`tests/test_scrapbox_import_service.py`へ移した。
- `tests/test_scrapbox_import_service.py`を新設し、byte/file経路、source不存在、不正UTF-8、不正JSON、期待外top-level、cache残存、部分成功、例外時close、同期順序を検証した。

実行した検証:

- 対象Pythonテスト: `docker compose run -T --rm --entrypoint python app -m unittest tests.test_scrapbox_import_service tests.test_web.ScrapboxImportHttpCharacterizationTest`
- サービス単体テスト: `docker compose run -T --rm --entrypoint python app -m unittest tests.test_scrapbox_import_service`（12件成功）
- HTTP契約テスト: `docker compose run -T --rm --entrypoint python app -m unittest tests.test_web.ScrapboxImportHttpCharacterizationTest`（12件成功）
- Python全件: `docker compose run -T --rm --entrypoint python app -m unittest discover -s tests`（548件成功）
- Playwright全件: `npm run test:ui`（29件成功）
- `git diff --check`: 成功
- 依存確認: `scrapbox_import_service.py`がFastAPI、`web.py`、`config.py`をimportしないこと、`database.py`/`metadata.py`からserviceへの逆依存がないこと、`web.py`がScrapbox同期目的で`sync_memos`/`sync_kindle_books`を直接呼ばないこと、互換用`web.import_scrapbox_export_bytes`が残っていないことを確認した。
- 循環import確認: `docker compose run -T --rm --entrypoint python app -c "import tsundokensaku.scrapbox_import_service; import tsundokensaku.web; import tsundokensaku.database; import tsundokensaku.metadata"`が成功。

設計との差異:

- なし。source不存在時にDB接続やcache変更を行わない点、cacheの直接上書き、事前JSON検証なし、transaction/rollback追加なし、同期順序維持を設計どおり実装した。

## 非目標

- Scrapbox/Cosense APIへのHTTP通信、双方向同期、認証。
- JSON schemaの新設、file size上限、streaming upload。
- cacheのatomic write、backup、rollback、lock、並行実行制御。
- DB transactionの統合、memo/Kindle schema変更、古いKindle本の削除同期。
- GET更新操作のPOST化、失敗status・レスポンス文言の改善。
- server path importの許可root制限やsymlink拒否。
- `metadata.py`、`database.py`、CLI補助スクリプトの責務分離。
- R7-2、R7-4、R5、R8、database.py系列の設計・実装。
- バックエンドのパッケージ構成見直し。

## 実装着手時の停止条件

- 現行HTTP契約を維持できない。
- `database.sync_memos`または`sync_kindle_books`の公開契約・commit境界変更が必要になる。
- source pathの安全境界を本実装と同時に変更する必要が生じる。
- R7-2またはR7-4の責務をserviceへ取り込む必要が生じる。
- 現行テストと本設計の観測事実が一致しない。
- PythonまたはPlaywrightの必須checkが失敗する。
