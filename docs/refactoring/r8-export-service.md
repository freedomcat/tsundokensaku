# R8: エクスポート業務ロジックの詳細設計 — 詳細設計済み・実装未着手

[中心ファイル責務棚卸し](../central-file-refactoring-inventory.md) / [ROADMAP](../../ROADMAP.md) / [R7-4詳細設計](r7-4-http-orchestration.md)

状態: 2026-08-01の`develop` HEAD `5cd645cbb116983e5b8c77ee61fcb3ee22948a06`を基準に詳細設計済み。characterization test追加、`export_service.py`作成、関数移動、HTTP adapter整理はすべて未着手である。本書の型名・関数名のうち「候補」「要判断」と記したものは実コードにまだ存在せず、契約固定PRの結果を見て実装前に確定する。

## 1. 背景

中心ファイルの責務棚卸しでは、`web.py`に残るエクスポートの警告・概算・プロファイル適用・JSON/ZIP組み立てをR8「エクスポート業務ロジック」とし、`export_service.py`を分離先候補にしていた。ただし従来の記録は対象関数と大まかな依存を列挙した候補整理であり、HTTP、DB、ファイル生成、成功履歴の境界や移行順を確定した詳細設計ではなかった。

R7では、HTTP adapterと非HTTP処理を分け、既存のstatus、本文、header、ファイル副作用を先にcharacterization testで固定してから移動する進め方を採用した。R8も同じ原則で、現在の外部契約と実行順を変えずに移せる単位を決める。

## 2. 現在の問題

- `api_preview_pack_export`と`api_export_pack`が、HTTP入力、DB接続、profile/format判断、ファイル生成、HTTP例外、Response生成、履歴記録を同じ層で進行している。
- `_export_pack_json`と`_export_pack_archive`がFastAPIの`Response`を返し、後者は`HTTPException`も直接送出する。出力内容の組み立てとHTTP表現を独立にテストしにくい。
- `_resolve_export_profile_or_400`が、非HTTPの`resolve_profile`による検索、外部公開profileの許可判定、HTTP 400変換を1関数に混在させる。
- `_preview_base_stats`はエクスポートプレビューだけでなく`GET /api/packs/stats`からも使われる。機械的に`export_service.py`へ移すと、資料一覧統計であるR5からエクスポートサービスへの不自然な依存が生じる。
- archive生成は既存の`export_profiles.py`、`export_stats.py`、`zip_export.py`へかなり分離済みだが、現在の`RenderContext`には`web.py`の`render_pdf_export`、`render_markdown_export`、`_resolve_pdf_file_or_404`をcallbackとして渡している。型上はFastAPI非依存でも、実行時には`HTTPException`がサービス境界を通過し得る。
- 成功履歴は、レスポンスbytes生成後に別DB接続でベストエフォート記録される。この順序と非原子性を崩すと、失敗時の記録有無が変わる。

## 3. R8の対象範囲

### 3.1 直接対象

棚卸しに記載された次の9関数は、基準HEADの`src/tsundokensaku/web.py`にすべて存在する。旧行番号から移動しているが、対応する処理の削除・改名はない。

| 関数 | 現在行 | R8で扱う理由 |
|---|---:|---|
| `_export_preview_warning` | 962 | 警告JSONの純粋な組み立て |
| `build_export_preview_warnings` | 966 | 項目状態から警告を決める純粋ロジック |
| `_preview_base_stats` | 1005 | 項目統計の集約。previewとpack statsで共有 |
| `build_export_preview_payload` | 1024 | standard previewの外部JSON契約 |
| `build_export_preview_payload_for_profile` | 1033 | profile planをpreview JSONへ射影 |
| `_export_pack_json` | 1129 | pack snapshotのJSONシリアライズとHTTP Responseが混在 |
| `_placeholder_item_stats_for_export` | 1155 | standard plan用の互換性判断 |
| `_export_pack_archive` | 1175 | profile plan、描画、manifest、ZIP、HTTP Responseが混在 |
| `_resolve_export_profile_or_400` | 1272 | profile解決・公開許可・HTTP 400が混在 |

### 3.2 関連するオーケストレーション

- `GET /api/packs/{pack_id}/export/preview`（`api_preview_pack_export`）のうち、profile解決、pack/items読取、統計収集、plan適用、payload組み立て。
- `GET /api/packs/{pack_id}/export`（`api_export_pack`）のうち、profile/format解決、pack/items読取、生成方式選択、成功後の履歴記録指示。
- `api_export_pack`末尾の`record_export_event`ベストエフォート呼び出し。永続化本体は移さないが、呼び出す時点と失敗方針はR8のユースケース契約である。
- `GET /api/packs/stats`からの`_preview_base_stats`利用。route全体はR5/R9だが、共有集計の置き場所をR8設計で確定する必要がある。

### 3.3 対象漏れではない関連処理

- `EXTERNALLY_AVAILABLE_EXPORT_PROFILES`は現在`web.py`の定数だが、「UI/APIから選択可能なprofile」というアプリケーションポリシーであり、HTTP型ではない。R8実装時はprofile解決ロジックと同じ側へ寄せる候補とする。
- `_now_jst`はJSON/ZIP名とmanifest日時で使う横断時刻関数である。R8へ移動せず、時刻を引数注入してサービスから時計依存を外す方針を優先する。
- `render_pdf_export`、`render_markdown_export`、`_resolve_pdf_file_or_404`はR7-4で整理するHTTPオーケストレーションに関連する。R8はR7-4の責務範囲や外部契約を変更しないが、`resolve_pdf`相当のPDF解決を含め、HTTP例外をサービスへ漏らさず接続できることをarchive移動の先行条件とする。

## 4. 対象外・非目標

- URL、HTTP method、query名、status code、JSON shape、MIME type、Content-Disposition、ファイル名、ZIP/Markdown/JSON内容の変更。
- `GET /api/export-events`の一覧JSON化、`database.record_export_event`/`list_export_events`のSQL・schema分離。これはD7の責務である。
- `GET /api/packs/stats`のroute、資料一覧集計、R5の実装。
- pack CRUD、JSON import、version 2/3正規化、DB record dataclassの再設計。
- `export_profiles.py`のplanアルゴリズム、閾値、警告、命名、描画戦略の変更。
- `export_stats.py`のPDF探索・ページ数・本文集計アルゴリズムの変更。
- `zip_export.py`のmanifest、ファイル名短縮、ZIP方式の変更。
- PDF/Markdownの抽出品質、streaming、temporary file、atomic write、非同期job化。
- DB schema、transaction方式、export eventのスキーマ・去重方針の変更。
- R7-2〜R7-4、R5、D7の実装をR8へ取り込むこと。
- template、JavaScript、CSS、Playwright specの変更。ただし後続の契約固定PRでは既存利用側を検証するテスト追加を行う。

## 5. 現在の処理フロー

### 5.1 preview

1. `profile`を`_resolve_export_profile_or_400`で解決する。未指定は`standard`、未知・非公開profileはpack存在確認より先に400となる。
2. `_pack_connection()`がDB接続、pack schema保証、commitを行う。
3. `get_pack`でpackを読み、存在しなければ404にする。
4. `get_pack_items`で`position, id`順に項目を読む。
5. 同じ接続で`collect_item_stats`を実行する。PDF欠損、ページ未指定、不正ページ、未索引は例外にせず`ItemStats`へ縮退する。
6. 接続をcloseする。profile planとoutline読取はclose後に行う。
7. standardは既存の7統計fieldと`warnings`だけを返す。`profile`や`chunks`は足さない。
8. chat/chapterは`profile.plan`を呼び、分冊名・統計・fragment・警告を含む拡張payloadを返す。chapterのoutline読取は必要な大項目だけで遅延実行される。
9. `JSONResponse`を返す。export eventは記録しない。

注意: `_pack_connection()`のschema保証により、新規DBではpreviewでもschema作成というDB書込みが起こり得る。既存packの構成や履歴行は更新しない。

### 5.2 JSON export

1. profile解決、format既定値解決、format許可判定、profileとの整合判定をこの順で行う。
2. DB接続でpack/itemsを読み、接続を閉じる。
3. 項目の空、PDF存在、pages内容を検証せず、version 3 JSON bytesをメモリ上で作る。空packも200で出力できる。
4. JST日付を使うdownload filenameと`application/json`の`Response`を作る。
5. Response生成成功後、別DB接続でexport eventを1行commitする。失敗はログだけで握りつぶす。
6. Responseを返す。

### 5.3 PDF/Markdown archive export

1. profile/format/pack読取まではJSONと同じ順序で行う。
2. 空packを400にする。
3. 全項目を`position`順に見て、最初のpages未指定を400にする。planより前に全項目を検証する。
4. JSTの`exported_at`を1回取得する。
5. `profile.chunk_limit() is not None`（chat/chapter）なら別DB接続で実統計を集め、closeする。standardは厳格な下位レンダラのエラー文言を保つためplaceholder `ItemStats`を作り、寛容な`collect_item_stats`を使わない。
6. chapterだけoutline loaderを用意し、`profile.plan`を実行する。
7. `RenderContext`を作り、各chunkをPDFまたはMarkdown bytesへ描画する。現在のcallbackは`web.py`のHTTP adapterである。
8. standardは`build_pack_zip`、chat/chapterはplan由来manifestと`build_pack_zip_with_manifest`を使う。すべてメモリ上の`BytesIO`で構築する。
9. ZIPの`Response`を作る。
10. Response生成成功後、JSONと同じ別DB接続でexport eventを記録する。

ファイル生成またはResponse生成より前にexport eventは書かれない。履歴INSERTと出力生成は同一transactionではなく、履歴失敗でも出力成功を返す。

## 6. 現在の依存関係

```text
workspace.html / browser
  -> web.py (FastAPI route, DB session, policy, HTTP error/Response)
      -> database.py (pack/items read, schema ensure, export event write)
      -> export_stats.py (ItemStats, tolerant stats collection)
          -> database.py / paths.py / pdf_export.py / pdf_outline.py
      -> export_profiles.py (profile resolution, plan, chunk render)
          -> export_stats.py / markdown_export.py / pdf_export.py / zip_export.py
      -> zip_export.py (filename, manifest, ZIP serialization)
      -> web.py内のR7-4処理 (PDF resolve/render, Markdown render)
      -> token_estimate.py
```

確認済みの循環importはない。`export_profiles.py`と`zip_export.py`はFastAPIをimportしない。一方、`RenderContext`へ注入される実体が現在は`web.py`関数であり、例外の実行時境界はHTTP非依存になっていない。

## 7. 関数・責務の分類

### 7.1 候補9関数

| 処理 | 分類・現在地 | 呼び出し元 | 入力 / 戻り値 | 主な依存・副作用 | 現在の例外 | 設計上の配置 | 既存test / 追加要否 |
|---|---|---|---|---|---|---|---|
| `_export_preview_warning` | 純粋な外部payload整形、`web.py` | warnings builderのみ | code/item_id/message → 3-key dict | なし | 通常なし | `export_service.py` private helper | 直接なし。key集合をbuilder testで固定 |
| `build_export_preview_warnings` | ドメイン寄りの純粋判断、`web.py` | standard/profile preview | `list[ItemStats]` → warning dict列 | ItemStatsのみ。副作用なし | 通常なし | `export_service.py` | 直接testあり。ただし全4種類、複数項目順、優先順位の補完が必要 |
| `_preview_base_stats` | 純粋集計、`web.py` | preview 2関数、`api_list_pack_stats` | ItemStats列 → 7-field dict | token estimator。副作用なし | estimator由来以外は通常なし | 集計値は`export_stats.py`の共有helper候補、previewへのdict射影はservice。関数名は要判断 | 直接なし、間接あり。共有consumer回帰testが必要 |
| `build_export_preview_payload` | application output mapping、`web.py` | preview route、直接test | ItemStats列 → standard preview dict | 上記2helper | 通常なし | `export_service.py` public | 直接4件＋HTTP統合あり。raw field集合・順序の補完が必要 |
| `build_export_preview_payload_for_profile` | application service / pure plan配線、`web.py` | preview route、直接test | stats/profile/pack_name/chapter_loader → 拡張dict | `profile.plan`, filename, token estimate。loader経由でFS読取し得る | profile/loader由来が透過 | `export_service.py` public | 直接4件＋HTTP統合が厚い。全plan警告順とloader失敗境界を補完 |
| `_export_pack_json` | シリアライズ＋HTTP Response、`web.py` | export route | pack/items → Response | `json.dumps`, filename sanitize, clock, quote。メモリ生成 | 通常なし。型不整合等は透過 | bytes/filename生成はservice private/public helper、Responseはweb | HTTP統合のみ。exact bytes/header/空packを追加 |
| `_placeholder_item_stats_for_export` | 互換性用純粋helper、`web.py` | archive standard経路 | item → zeroed ItemStats | なし | 通常なし | `export_service.py` private | 直接なし。standardがstats収集を呼ばず厳格エラーを保つtestが必要 |
| `_export_pack_archive` | application service＋ファイル生成配線＋HTTP、`web.py` | export route | pack/items/format/profile → Response | config、DB read、clock、profile、R7 callback、ZIP。FS/DB read、メモリ大 | HTTPException、ValueError、PDF/SQLite/FS例外等 | serviceがplan/描画/ZIPを進行、既存生成moduleへ委譲。Response/HTTP変換はweb | 直接なし、HTTP/ZIP統合は非常に厚い。依存呼出し順、固定時刻、失敗時履歴を補完 |
| `_resolve_export_profile_or_400` | application policy＋HTTP adapter、`web.py` | preview/export route | name or None → ExportProfile | `resolve_profile`、外部許可set | unknown/unlistedをHTTP 400 | 検索・許可はservice、`ValueError`等から400への変換だけweb | 間接testあり。unlisted profile分岐の直接testが必要 |

### 7.2 周辺処理

| 処理 | 分類 | 現在の副作用 | 設計上の扱い |
|---|---|---|---|
| `api_preview_pack_export` | HTTP adapter＋DB/application進行 | schema保証、DB/FS読取 | routeはquery受領、HTTP変換、JSONResponseに限定。DB sessionをserviceが所有するかは公開API案で明示 |
| `api_export_pack` | HTTP adapter＋DB/application進行 | DB/FS読取、event書込 | routeはquery受領、Response構築、失敗のstatus変換、Response構築後の成功記録呼出しだけを残す |
| `_pack_connection` | DB adapter | DB file作成、schema保証、commit | R8専用処理ではないためwebに残す。serviceからwebをimportして使わない。service側でDB sessionを所有する場合はdatabase APIから同じ順序を再現する |
| `record_export_event` | 永続化、`database.py` | UTC日時採取、INSERT、commit | `database.py`または将来D7 repoに残す。R8は成功後に別接続で呼ぶ時点だけを所有 |
| `api_list_pack_stats` | R5/R9 | DB/FS読取 | routeはR8対象外。共有集計を`export_stats.py`へ置き、`api_list_pack_stats`が`export_service`へ依存しない形を優先 |
| PDF/Markdown renderer | R7-4＋ファイル生成 | PDF/DB/FS読取、bytes/text生成 | R7-4の既存/予定moduleに残す。R8で複製しない |
| plan/filename/manifest/ZIP | 純粋ロジック・ファイル生成 | メモリ上のbytes生成 | `export_profiles.py`/`zip_export.py`に残す |

## 8. `web.py`に残す責務

- 2つのGET route定義とFastAPIによるpath/query受領。
- FastAPIの422を含む入力型変換。
- serviceの「profile/format不正」「pack不在」「空pack/pages不正」「PDF不在」等を現在と同じ400/404へ変換する処理。
- `JSONResponse`/`Response`の生成。
- `application/json`、`application/zip`の選択。
- `Content-Disposition: attachment; filename*=UTF-8''...`の生成と`quote`。
- serviceが生成したResponseの構築に成功した後で、成功履歴のベストエフォート記録をserviceへ指示する順序。
- `_pack_connection`など、他routeも使う汎用DB adapter。
- `/api/packs/stats`、`/api/export-events`を含むR8対象外route。

`HTTPException`、`Request`、`Response`、`JSONResponse`、`StreamingResponse`を`export_service.py`の公開APIへ渡さない。R8は現在もstreamingを使っておらず、実装時に導入しない。

## 9. `export_service.py`へ移す責務

- 外部利用可能profileの解決とformat既定値・整合性の判断。
- preview用の警告、統計、standard/profile payloadの組み立て。
- previewのpack/items取得、統計収集、接続close後のplanというユースケース順序。
- JSON snapshotのschema組み立て、UTF-8シリアライズ、download filenameの組み立て。ただしHTTP Responseは作らない。
- archiveの空/pages事前検証、実統計またはplaceholderの選択、profile plan、entry/manifestの組み立て、既存ZIP builderの呼び出し。
- 生成済みcontent、download filename、出力種別をHTTP非依存の値として返すこと。
- 成功履歴を別接続でベストエフォート記録する非HTTP helper。呼び出し時点はResponse構築後のweb側が決める。
- 例外または失敗結果にHTTP statusを埋め込まず、webが対応表でstatusへ変換できる失敗表現を返すこと。

## 10. 既存モジュールへ残す責務

- `export_profiles.py`: `ExportProfile`、`ExportPlan`、fragment/chunk、分割判断、profile固有warning、chunk filename、chunk rendering。
- `export_stats.py`: `ItemStats`、PDF/DB本文からの寛容な統計収集。R5との共有のため、基礎集計値を表すhelperをここへ追加する案を優先するが、名称・戻り型は実装前に確定する。
- `zip_export.py`: filename sanitize/上限、standard/plan manifest、ZIP serialization、root entry順。
- `pdf_export.py`、`markdown_export.py`、R7-4で予定するservice: PDF/Markdownの低レベル生成、本文抽出、ページ検証。
- `database.py`: pack/items recordとread、schema保証、export event INSERT/commit。D7実装後はrepoへ委譲してよいが、R8からD7を先取りしない。
- `token_estimate.py`: estimator名、文字統計、token推定。

JSON snapshotには現在専用の下位moduleがない。version 3の小さい単一シリアライザであり再利用元も1箇所なので、R8開始時点ではservice内のprivate helperでよい。JSON形式の再利用者や独立した変更理由が生じた場合だけ補助module化を再検討する。

## 11. 目標とする依存方向

```text
templates / JavaScript
  -> web.py (FastAPI入力、HTTP status/detail、Response/header)
      -> export_service.py (R8 use case)
          -> database.py (read / event persistence)
          -> export_stats.py
          -> export_profiles.py
          -> zip_export.py
          -> R7-4の非HTTP PDF/Markdown API
          -> paths.py / pdf_outline.py（PDF sourceとchapter読取）

export_profiles.py -> export_stats.py / pdf_export.py / markdown_export.py / zip_export.py
database.py, export_stats.py, zip_export.py, R7-4 service -X-> export_service.py
export_service.py -X-> web.py / FastAPI
```

`export_service.py`が`web.py`をimportする、またはHTTP adapter callbackを受けて`HTTPException`を透過させる形は完成形として認めない。

## 12. `export_service.py`の公開API案

以下は概念APIである。`PreparedPackExport`等の名称は**候補であり、実コードには存在しないため実装前に確定する**。新しいHTTP非依存の結果型を1個だけ置く案を推奨する。

### 12.1 profileとformatの解決

```python
def resolve_external_profile(name: str | None) -> ExportProfile: ...

def resolve_export_format(profile: ExportProfile, requested_format: str | None) -> str: ...
```

- 入力: queryから得た`str | None`。HTTP型は受けない。
- 戻り: 既存`ExportProfile`と、解決済み`"pdf" | "md" | "json"`。
- 副作用: なし。
- 失敗: `ValueError`を候補とし、webが400へ変換する。detail文言は現在契約を維持する。
- `resolve_external_profile`は既存`resolve_profile`を再利用し、外部許可setを確認する。profileを重複生成しない。

### 12.2 preview

```python
def build_pack_export_preview(
    pack_id: int,
    *,
    profile_name: str | None,
    db_path: Path,
    books_dir: Path,
) -> dict[str, object] | None: ...
```

- `None`はpack不在という業務結果の候補。webが404へ変換する。専用result/exceptionにするかは契約固定PR後に要判断。
- 接続、schema保証、pack/items/統計read、close、その後のplanという現在順序を維持する。
- 戻りdictは現在のJSON shapeそのもの。`JSONResponse`は返さない。
- 空pack、PDF欠損、pages不正、未索引は200 payloadのwarningであり失敗にしない。
- chapter読取はHTTP非依存のpath解決と`list_chapters`を使う。`chapter_loader`から`HTTPException`を送出させず、PDF不在は実装前に確定する非HTTP例外またはresultで通知する。既に統計で判定できるPDF不在をpreview warningからHTTP例外へ逆戻りさせず、routeで変換すべき失敗だけをweb adapterが現在のHTTP契約へ変換する。
- DB schema保証以外のwrite、event記録、pack更新は行わない。

純粋関数としての`build_export_preview_warnings`、`build_export_preview_payload`、`build_export_preview_payload_for_profile`は直接単体テスト価値が高いため、service moduleの公開関数として維持する案を採る。`_export_preview_warning`はprivateのままとする。

### 12.3 export準備

```python
@dataclass(frozen=True)
class PreparedPackExport:  # 候補名。実装前に確定
    content: bytes
    download_filename: str
    output_kind: Literal["json", "zip"]
    pack_id: int
    pack_name: str
    profile_name: str
    format: str
    items: tuple[PackItemRecord, ...]


def prepare_pack_export(
    pack_id: int,
    *,
    profile_name: str | None,
    requested_format: str | None,
    db_path: Path,
    books_dir: Path,
    exported_at: datetime,
) -> PreparedPackExport | None: ...
```

- profile/format検証をDB接続より先に行う。
- pack/itemsを読んだ接続はファイル生成前に閉じる。
- `format=json`は項目・PDF・pagesを検証せず、空packも生成する。
- archiveは空packと全項目pagesを先に検証する。
- serviceが低レベルPDF/Markdown/ZIPを再実装せず、既存moduleを呼ぶ。
- contentとfilenameを返すが、MIME typeやContent-Dispositionはwebが`output_kind`から組み立てる。
- eventはまだ記録しない。これによりwebのResponse構築失敗時に履歴を残さない現在順序を維持する。
- ファイルシステム、PDF parser、SQLite等の予期しない例外は包括変換せず伝播させ、現在どおり500になり得る。

`exported_at`はwebがJST aware datetimeを渡す。service内で`datetime.now()`を呼ばない。ただし現在のMarkdown抽出日はentry描画ごとに`_now_jst()`を呼ぶため、archive全体の`exported_at`へ統一すると分境界の挙動が変わる可能性がある。この点は契約固定PRで時刻呼出し回数を確認し、R7-4 APIとの接続前に確定する。

### 12.4 成功履歴

```python
def record_export_success_best_effort(
    db_path: Path,
    prepared: PreparedPackExport,
) -> None: ...
```

- webがResponseを構築した後、return直前に呼ぶ。
- 新しい別接続でschema保証と`database.record_export_event`を行い、同関数がcommitする。
- 接続作成、schema、INSERT、commit、closeのどの失敗も捕捉して現在と同じログメッセージを出し、呼び出し元へ例外を返さない。
- 同じ内容を再実行しても毎回1行記録する。
- `PreparedPackExport.items`はposition順の元資料項目であり、chapter fragment/chunkへ置き換えない。

## 13. 入力と戻り値

| API | 入力 | 戻り値 | 読取 | 書込 |
|---|---|---|---|---|
| profile/format解決 | `str | None` | 既存profile、解決format | なし | なし |
| preview builder | pack id、db/books path、profile名 | 現行payload dictまたはpack不在 | DB、PDF、outline | schema保証のみ。履歴なし |
| export preparer | pack id、db/books path、profile/format、JST時刻 | HTTP非依存の生成結果またはpack不在 | DB、PDF、outline | なし |
| success recorder | db path、生成結果のsnapshot | `None` | なし | export event 1行、commit |

公開APIに`Request`、`Response`、`HTTPException`、`StreamingResponse`を含めない。`sqlite3.Connection`を公開引数にする案も可能だが、現在の「read接続を生成前にclose」「eventは別接続」を呼び出し側が誤って崩しやすい。上記では`db_path`を渡しserviceが短命接続を所有する案を第一候補とする。

## 14. 例外・失敗表現の方針

- profile/formatの利用者入力不正、空pack、pages未指定、ページ指定不正は`ValueError`系を第一候補とする。メッセージは現在のHTTP detailを維持する。
- pack不在は`None`結果を第一候補とする。専用`ExportPackNotFoundError`等を新設する場合は、契約固定PR後に名称と用途を確定し、本書を更新してから実装する。
- PDF source不在は404へ区別できる非HTTP表現が必要である。専用例外、`FileNotFoundError`の明確なサブクラス、または明示resultのいずれかを、現行のarchiveとchapter previewの契約をcharacterization testで確認してから確定する。一般の保存先/DBの`FileNotFoundError`と混同せず、service本体、`RenderContext.resolve_pdf`、`chapter_loader`のいずれも`HTTPException`を送出しない。
- unexpectedなSQLite、PDF parser、ZIP、memory、filesystem例外は捕捉しない。現在どおり500になり得る。
- event記録だけは例外種別を問わず内部で捕捉する。
- HTTP statusやFastAPIのdetailをservice例外のfieldに持たせない。

## 15. HTTPステータスへの変換方針

| serviceの結果・失敗 | webの現在契約 |
|---|---|
| unknown/unlisted profile | 400、`不明なエクスポートプロファイルです: {name}` |
| formatがpdf/md/json以外 | 400、`format は pdf, md, または json を指定してください` |
| 固定profileとformat不一致 | 400、`profile={name} では format={primary} のみ指定できます` |
| pack不在 | 404、`資料が見つかりません` |
| archiveで空pack | 400、`資料が空です` |
| pages未指定 | 400、`{title}: ページを指定してください`。position順で最初の1件 |
| PDF source不在 | 404、`PDF not found` |
| pages parse/range不正 | 400、既存lower moduleの`str(exc)` |
| previewの空/PDF欠損/pages不正/未索引 | 200、warning payload |
| JSONの空/PDF欠損/pages不正 | 200、snapshot JSON |
| event記録失敗 | 生成済み200を維持 |
| その他 | 新たに変換せず500になり得る |

serviceやcallbackはこの表のHTTP statusを知らず、`web.py`のadapterだけが非HTTPの結果・例外をstatusとdetailへ変換する。特にPDF source不在は、archiveなど現在404となる経路でのみ`PDF not found`へ変換し、previewの既存warning経路を404へ変更しない。

profile→format→profile/format整合→packの検証順を維持する。previewはprofile→packの順を維持する。`pack_id`の型不正等はFastAPIの422に委ねる。

## 16. DB・履歴・統計の扱い

### 16.1 readとwrite

- preview: 1接続でschema保証、pack/items/stats read。close後にplan。event writeなし。
- JSON export: 1接続でpack/items readしてclose。生成後、別接続でevent write。
- standard PDF archive: 1接続でpack/items readしてclose。統計接続なし。描画後、別接続でevent write。
- chat/chapter archive: pack/items接続をclose後、別接続でstats readしてclose。描画後、さらに別接続でevent write。
- Markdown描画は現在、項目ごとにindex metadata/page text用の短命DB接続を開く。R7-4実装で最適化する場合もR8で勝手にtransactionを統合しない。

### 16.2 transaction境界

出力生成とevent INSERTに共通transactionはない。eventは`record_export_event`内の単独commitである。生成失敗ならrecord処理に到達しない。record失敗なら出力を失敗させない。この非原子性は意図された契約であり、R8で改善しない。

### 16.3 preview、再実行、失敗

- previewはexport eventを記録しない。
- 同一exportの再実行は去重せず毎回1行増える。
- profile/format/pack/空/pages/PDF/描画/ZIPのどこかで失敗した場合は記録しない。
- Response object生成後にevent記録を試すため、クライアントのdownload完了は保証しない。
- eventの`exported_at`は記録時のUTC ISO8601、download名/manifestは生成時のJSTであり、同じ時計表現ではない。

## 17. ファイル生成との境界

- JSON: service内private serializerがversion 3 snapshot bytesを作る。HTTP headerはweb。
- PDF: R7-4の非HTTP rendererへ委譲する。ページ選択・PDF bytesをR8で再実装しない。
- Markdown: R7-4/`markdown_export.py`へ委譲する。本文取得・header・改行をR8で再実装しない。
- profile: `export_profiles.py`がplan、filename、chunk contentを所有する。
- manifest/ZIP: `zip_export.py`が所有する。serviceはentry/manifest入力を組み立てるだけにする。
- filesystem: archiveとJSONはtemporary fileを作らず、メモリ上で完成bytesを返す。streamingへ変更しない。

R7-4未実装の現状では、archiveの`RenderContext.resolve_pdf`とchapter preview/archiveの`chapter_loader`に`_resolve_pdf_file_or_404`が渡され、callback経由で`HTTPException(404)`が送出され得る。R7-4の非HTTP PDF/Markdown rendererが実装されるだけでは、このPDF解決依存は解消しない。preview純粋ロジックとJSONは先行移動できるが、callbackを含む完成形では次の境界をすべて満たす。

1. `resolve_pdf`相当のPDF解決処理に、HTTPに依存しない契約を用意する。具体的な例外型またはresult型は現行契約をcharacterization testで確認してから確定し、根拠なく本設計PRで新設しない。
2. service本体とserviceへ渡す`RenderContext`/`chapter_loader` callbackは`HTTPException`を送出せず、PDF不在を上記の非HTTP表現で通知する。
3. `web.py`だけがPDF不在の非HTTP表現を捕捉し、archiveなど現在のroute契約で必要なHTTP 404 `PDF not found`へ変換する。chapter previewで既にwarningとなるPDF不在は200 warningのまま維持する。
4. chapter previewの`chapter_loader`にもarchiveの`resolve_pdf`と同じ方針を適用し、preview service移動後にHTTP例外経路を残さない。

archiveの完成形への移動は、R7-4の非HTTP PDF/Markdown APIが利用可能であることに加え、`resolve_pdf`相当の非HTTP契約が確定・実装済みで、`RenderContext`と`chapter_loader`の接続から`HTTPException`が排除されるまで行わない。この条件はR8からR7-4の責務範囲を拡張するものではなく、R8が利用する下位境界または協調する先行PRで満たす接続条件である。

## 18. 既存の外部契約

### 18.1 endpointとrequest

| endpoint | method | query | 成功 |
|---|---|---|---|
| `/api/packs/{pack_id}/export/preview` | GET | `profile: str | None = None` | 200 JSON |
| `/api/packs/{pack_id}/export` | GET | `profile: str | None = None`, `format: str | None = None` | 200 JSON bytesまたはZIP bytes |

`profile=None`はstandard。exportで`format=None`はprofileの`primary_format`、standardだけpdfへfallbackする。利用可能な組合せはstandard×pdf/md/json、chat×md、chapter×pdfである。demo modeでもpreview/exportは許可される。request bodyはない。

### 18.2 standard preview JSON

fieldは次の8個で、profile未指定と`profile=standard`は同じpayloadである。

```json
{
  "estimation": "approximate",
  "estimator": "char-class-v1",
  "book_count": 0,
  "item_count": 0,
  "total_pages": 0,
  "estimated_chars": 0,
  "estimated_tokens": 0,
  "warnings": []
}
```

- `book_count`: `pdf_path`文字列のdistinct数。PDF実在数ではない。
- `item_count`: ItemStats件数。
- `total_pages`: 各`page_numbers`長の合計。重複pageを項目横断で去重しない。
- `estimated_chars`: CJKとother文字数の合計。
- `estimated_tokens`: 全項目のTextStatsを合算してから1回estimateする。項目別ceilの合計ではない。
- standard payloadには`profile`、`file_count`、`archive`、`chunks`を追加しない。

### 18.3 profile preview JSON

chat/chapterではstandardの基礎fieldに次を加える。

- `profile`: 解決profile名。
- `file_count`: plan chunk数。
- `archive`: 常に`"zip"`。
- `chunks`: plan順。各chunkは`filename`, `estimated_tokens`, `pages`, `items`。
- chunk itemは`item_id`, `title`, `pdf_path`, `pages`, `label`, `fragment_index`, `fragment_count`, `estimated_tokens`。
- `warnings`: item warningを先、plan warningを後に連結する。

空packでも200で、`file_count=0`、`chunks=[]`、`empty_pack` warningを返す。

### 18.4 warning契約

item warningは項目のposition順で最大1件ずつ出し、同一項目内の優先順位は`missing_pdf`→`missing_pages`→`invalid_pages`→`unindexed_pages`である。

| code | item_id | 現在文言 |
|---|---|---|
| `empty_pack` | `null` | `この資料には資料項目がありません` |
| `missing_pdf` | item id | `「{title}」はPDFファイルが見つかりません` |
| `missing_pages` | item id | `「{title}」はページが指定されていません` |
| `invalid_pages` | item id | `「{title}」のページ指定を解釈できませんでした` |
| `unindexed_pages` | item id | `「{title}」は未インデックスのため{n}ページ分を概算に含めていません` |

plan warningは`export_profiles.py`が所有し、現在は`item_exceeds_limit`、`too_many_sources`、`estimated_chars_exceed_guideline`、`chapter_exceeds_limit`、`item_split_by_chapters`、`no_outline_fallback`がある。文言・順序もpreviewとprofile manifestで利用されるため、R8移動で変更しない。

UIは`empty_pack`、`missing_pdf`、`missing_pages`、`invalid_pages`をblocking warningとして扱い、`unindexed_pages`とplan warningではexport buttonを有効にする。codeの改名はUI契約変更になる。

### 18.5 JSON download

- status 200。
- 明示MIME: `application/json`。現在のResponse headerにもcharset parameterはない。
- filename: `{sanitize_filename_component(pack.name)}_{JST YYYYMMDD}.json`。
- Content-Disposition: `attachment; filename*=UTF-8''{quote(filename)}`。
- body: UTF-8、`ensure_ascii=False`、indent 2、LF、末尾改行なし。
- top-level key順は現在`version`, `name`, `items`。item key順は`pdf_path`, `title`, `pages`, `collapsed`, `addedAt`, `position`。
- `version`は3。`id`と`updatedAt`は含めない。
- itemsはDBの`position, id`順。同一pdf_pathを統合しない。
- pack名はbodyでは未sanitizeのまま、filenameだけsanitizeする。
- 空pack、PDF不在、空/不正pagesでもsnapshotを200で出力する。

JSONのkey順・indent・末尾改行はJSONの意味論では不要で、現在の利用側もkey参照である。ただし責務移動中の不要なbytes差分を検知するcompatibility constraintとしてcharacterization testに固定し、将来の恒久仕様にするかは別変更で判断する。

### 18.6 ZIP download

- status 200、明示MIME `application/zip`、charsetなし。
- Content-DispositionはJSONと同じRFC 5987形式。
- standard archive名: `{sanitized pack}_{JST YYYYMMDD}.zip`。`standard`を含めない。
- chat/chapter archive名: `{sanitized pack}_{profile}_{JST YYYYMMDD}.zip`。
- 常にZIP。出力fileが1件でも直接file responseにはしない。
- root直下の先頭entryは`manifest.md`、続いてplan/position順のcontent entry。directory entryは作らない。
- standard entry名: `{NN}_{sanitized title}_p{sanitized pages}.{pdf|md}`。長名は`zip_export.py`の255 UTF-8 bytes上限規則。
- chat entry名: `{sanitized pack}_chat_{NN}.md`。
- chapter entry名: 連番、書名、章labelまたはpage範囲を`zip_export.py`規則で構築。
- standard manifestは`render_pack_manifest`、profile manifestは`render_plan_manifest`。UTF-8、LF、末尾LF。
- standard manifestの日時はJSTの`%Y-%m-%d %H:%M`表記だが、文字列にtimezone名を付けない。
- ZIP entryのtimestamp等のmetadataは`zipfile.writestr`既定に依存して実行時刻で変動するため、ZIP全bytes一致は契約ではない。entry名・順・展開contentを固定する。

### 18.7 MarkdownとPDF

- PDF entryは選択page順を維持し、複数rangeを勝手にsort/去重しない。
- Markdown entryはUTF-8、LF、末尾LF。`markdown_export.render_markdown_pages`の見出し、出典、元file、page、抽出日、空本文文言を維持する。
- chatはchunk headerと各Markdownを`\n\n---\n\n`で連結し、UTF-8 bytes化する。
- chapterは必要に応じてPDF fragmentを結合する。manifestに書名、label、page範囲を残す。
- R8は内容生成規則を所有せず、既存moduleのcontractとして参照する。

### 18.8 日時

- download名とmanifest: `_now_jst()`、Asia/Tokyo。
- individual Markdownの抽出日: 現在はMarkdown renderer呼出しごとに`_now_jst()`。
- export event: 生成成功後に`datetime.now(timezone.utc).isoformat()`。
- ZIP entry metadata: `zipfile`既定の実行時刻。

これらを1時刻へ統一することはR8の非目標であり、実装前に現行呼出し回数をtestで固定する。

### 18.9 export eventと履歴API

- `api_export_pack`でJSON/PDF/Markdownの生成に成功した場合だけ、解決済みprofile名とformatを記録する。
- DBの`items_json`は`{"version": 1, "items": [...]}`。各itemは`pdf_path`, `title`, `pages`, `position`の4 fieldで、pack itemのposition順snapshotである。`collapsed`、`addedAt`、chapter fragment/chunkは記録しない。
- `exported_at`は記録時点のUTC ISO8601。`pack_id`はpack削除後もdanglingを許容し、`pack_name`はsnapshotとして残る。
- 同一内容を再exportしても去重せず毎回1行増える。
- `GET /api/export-events`はR8の移動対象外だが、R8が書く履歴の外部readerである。queryは`limit: int = 20`、成功は200 `{"export_events": [...]}`。eventは`id`, `exported_at`, `pack_id`, `pack_name`, `profile`, `format`, `items`を返し、itemsは上記4 fieldを返す。
- `limit`の型不正はFastAPI 422、負数はdatabase層で0件へ丸められる。R8でこのreader契約を変更しない。

### 18.10 `/api/packs/stats`との共有集計

`GET /api/packs/stats`はR5/R9のrouteでありR8対象外だが、現在`_preview_base_stats`を再利用している。各packの`book_count`, `item_count`, `total_pages`, `estimated_tokens`はpreviewと同じ計算規則である。R8移動時にこのrouteを`export_service.py`へ依存させたり、field値を変えたりしない。共有する純粋集計だけを`export_stats.py`へ置く案を優先する。

## 19. characterization testで固定する契約

### 19.1 既存で厚く固定済み

- `ExportArchiveBackwardCompatibilityTest`: standard ZIPのentry名・順・PDF実page・Markdown本文・manifest・主要400/404。
- `ExportProfileParameterTest`: profile/format既定値、standard互換、chat/chapter、validation順、manifest、履歴。
- `BuildExportPreviewPayloadTest`/`BuildExportPreviewPayloadForProfileTest`: 基礎統計、chunk shape、warning合流。
- `PackExportPreviewTest`: HTTP相当のpack不在、空、missing、unindexed、profile planと実export一致。
- `ExportEventRecordingTest`: 成功、JSON、失敗非記録、記録失敗の無害性、standard解決、snapshot schema、再実行。
- `tests/test_export_profiles.py`、`tests/test_export_stats.py`、`tests/test_zip_export.py`、`tests/test_markdown_export.py`: 下位module contract。
- Playwright: modalのpreview、profile/format送信、JSON取得、ZIP downloadと主要entry。

### 19.2 契約固定PRで追加する

1. 全item warningのexact dict、同一項目優先順位、複数項目のposition順、item warning→plan warning順。
2. `_preview_base_stats`相当の7 field exact値と、`/api/packs/stats`の共有consumerが同じ4集計値を得ること。
3. standard/profile previewのfield集合。standardに拡張fieldがないこと、拡張chunk itemの全field。
4. profile解決のunlisted分岐。未知profile、invalid format、conflict、pack不在のvalidation順。
5. JSONのfixed clockによるexact body bytes、UTF-8日本語、key/indent/LF/末尾、filename、MIME、Content-Disposition、空packと不正資料の200。
6. ZIPのfixed clockによるarchive名、Content-Disposition、manifest日時。ZIP全bytesではなくentry名・順・展開bytesを比較する。
7. standard経路が`collect_item_stats`を呼ばず、placeholderを使って既存の詳細なpage errorを返すこと。chat/chapterだけstats接続を使うこと。
8. DB接続順とclose: pack read接続を生成前にcloseし、eventは生成/Response成功後の別接続であること。
9. profile/format/pack/空/pages/PDF/render/ZIP失敗ではeventを記録しないこと。previewは常にeventを記録しないこと。
10. event接続作成、schema、INSERT、commitの失敗が200 responseを壊さないこと。
11. 同じexportを再実行すると2行、chapter fragmentではなく元item snapshotを記録すること。
12. `_now_jst`の呼出し回数と、archive日時・Markdown抽出日・UTC event日時が別時計である現在挙動。統一する場合は別の仕様変更PRにする。
13. TestClientでactual status、`{"detail": ...}`、Content-Type、Content-Dispositionを確認する。直接関数呼出しだけに依存しない。
14. `tsundokensaku.web`のR8対象関数に対するmonkeypatchが0件であることを再確認する。
15. Python側のwarning codeと`workspace.html`のblocking判定文字列が一致するcross-layer契約をPlaywrightで固定する。`empty_pack`、`missing_pdf`、`missing_pages`、`invalid_pages`のblocking warningがある場合はexport操作が無効になり、`unindexed_pages`やplan warningなどnon-blocking warningだけの場合は操作可能であることを確認する。4種類すべてをE2Eで検証するか、代表E2Eと全codeのPython testへ分担するかはcharacterization test実装時に決定する。
16. `RenderContext.resolve_pdf`とchapter preview/archiveの`chapter_loader`へ非HTTPのPDF不在を注入し、service境界から`HTTPException`が出ないこと、web adapterだけが必要な経路を404へ変換すること、preview warningの200契約を維持することを固定する。

## 20. 必要なテスト配置

- 契約固定PR: `tests/test_web.py`へHTTP/現在import pathのcharacterization testを追加し、Playwrightでwarning codeとblocking判定のcross-layer契約を固定する。production codeは変更しない。
- pure preview移動PR: `tests/test_export_service.py`を新設し、直接関数testを移す。`tests/test_web.py`はroute adapter契約だけ残す。
- shared summaryを`export_stats.py`へ置く場合: `tests/test_export_stats.py`に純粋集計testを置く。
- JSON/archive移動PR: `tests/test_export_service.py`でHTTP非依存の生成結果、依存呼出し、失敗を検証し、`tests/test_web.py`でheader/status/historyを検証する。
- `tests/test_export_profiles.py`、`tests/test_zip_export.py`、`tests/test_markdown_export.py`は所有ロジックを引き続き直接検証する。assertをservice testへ重複コピーしない。
- 実装PRではPython全件とPlaywright全件を実行する。文書PRではコードtest追加を行わない。

現在、`build_export_preview_warnings`、`build_export_preview_payload`、`build_export_preview_payload_for_profile`は`tests/test_web.py`から直接importされるが、対象関数へのmonkeypatchは0件である。R6/R7-1と同じ判断基準で、実装時はtest importをserviceへ移し、`web.py`に互換wrapperを残さない案を採る。ただし`api_preview_pack_export`と`api_export_pack`のPython import pathはrouteとして維持する。

## 21. 実装手順

1. 本書の設計PRをレビューし、R7-4との順序、結果型、PDF source失敗表現、`resolve_pdf`/`chapter_loader`の非HTTP境界、時刻契約を確定する。
2. production codeを変えず、§19.2のcharacterization testを追加する。
3. `export_service.py`を作り、profile/format policyとpure preview logicを移す。共有基礎集計は`export_stats.py`へ置き、`/api/packs/stats`のR5境界を守る。
4. preview use caseのDB read/close/planをserviceへ移し、`chapter_loader`からHTTP例外経路を排除したうえでweb routeをHTTP adapter化する。
5. JSON serializerとHTTP非依存生成結果をserviceへ移す。JSON response構築と成功記録順をwebで維持する。
6. R7-4の非HTTP PDF/Markdown APIと`resolve_pdf`相当の非HTTP契約が利用可能になり、`RenderContext`/`chapter_loader` callbackからHTTP例外経路を排除した後、archive validation、placeholder/stats分岐、plan、entry/manifest/ZIP配線をserviceへ移す。
7. eventの別接続ベストエフォートhelperをserviceへ移し、Response構築後に呼ぶ。
8. import、循環依存、route/OpenAPI、Python、Playwright、差分、文書状態を検証する。

## 22. PR分割案

### PR1: R8詳細設計

- 目的: 現状、境界、契約、依存順、後続PRを確定する。
- 変更対象: 本書、ROADMAP、中心ファイル棚卸し。
- 変更しないもの: Python、test、template/static、DB、API。
- 先行条件: cleanな最新develop。
- 必要なtest: link/state/diff/Markdownの軽量検証。
- 完了条件: R8が「詳細設計済み・実装未着手」で3文書一致。
- リスク: 観測事実の漏れ、R7-4設計との矛盾。

### PR2: 現行契約のcharacterization test

- 目的: §19.2、とくにJSON bytes、warning順、warning codeとUI blocking判定、PDF解決callbackの失敗境界、HTTP header、DB/event順を移動前に固定する。
- 変更対象: 既存testのみ。必要なら新規test fileは作らず、まだ所有moduleがないR8 testは`test_web.py`に置く。
- 変更しないもの: production code、API、output、文書上の実装状態。
- 先行条件: PR1 merge、未確定4点のレビュー判断。
- 必要なtest: 追加対象、Python全件、Playwright全件。
- 完了条件: 挙動を変えず不足契約が再現可能なexpected値で固定され、blocking/non-blocking warningとUI操作可否のcross-layer契約が検証される。
- リスク: 偶然のZIP metadataやclockを固定してflakyにすること。logical contentと注入clockだけを比較する。

### PR3: previewとrequest policyの分離

- 目的: FastAPI非依存のserviceを作り、profile/format policy、warning、preview projection、preview DB進行を移す。
- 変更対象: 新規`export_service.py`、`web.py`、`export_stats.py`（共有基礎集計を採用する場合）、test配置。
- 変更しないもの: JSON/ZIP実行経路、event、R7-4、output contract。
- 先行条件: PR2、shared summaryの名称/型確定、chapter previewで使うPDF解決の非HTTP失敗契約確定。
- 必要なtest: pure service、preview HTTP、pack stats、profile tests、全件。
- 完了条件: preview routeがHTTP変換中心、serviceと`chapter_loader`がFastAPI/webをimportせず`HTTPException`を送出しない状態で、standard/profile payload互換。
- リスク: `_preview_base_stats`をserviceに閉じてR5依存を悪化させること、chapter loaderのHTTP例外漏れ。

### PR4: JSON export準備の分離

- 目的: version 3 JSON bytes/filename生成とprofile/format/pack読取をHTTP Responseから分離する。
- 変更対象: `export_service.py`、`web.py`、service/web tests。
- 変更しないもの: ZIP/Markdown/PDF生成、JSON schema/format、event schema。
- 先行条件: PR2/PR3。
- 必要なtest: exact JSON bytes/header、空/不正資料、event成功/失敗/再実行、全件。
- 完了条件: JSON生成serviceがHTTP型を返さず、Response後のevent順が同じ。
- リスク: key/indent/newline、時刻、空pack挙動、event timingのドリフト。

### PR5: archiveオーケストレーションの分離

- 目的: validation、placeholder/stats、plan、render、manifest、ZIP配線をserviceへ移す。
- 変更対象: `export_service.py`、`web.py`、service/web tests。R7-4の既存非HTTP APIを利用するだけとする。
- 変更しないもの: profile algorithm、stats algorithm、ZIP/Markdown/PDF生成規則、R7-4本体、DB schema。
- 先行条件: PR2〜PR4、R7-4の非HTTP PDF/Markdown APIが利用可能、かつ`resolve_pdf`相当の非HTTP契約が確定・実装済み。`RenderContext`と`chapter_loader`のcallbackから`HTTPException`が排除されていること。
- 必要なtest: standard/chat/chapter、PDF/MD、全error、fixed time、preview/export filename一致、Python/Playwright全件。
- 完了条件: service本体と`RenderContext`/`chapter_loader` callbackにFastAPI型/`HTTPException`経路がなく、PDF不在はweb adapterだけが404へ変換し、既存ZIP logical contentが一致。
- リスク: R7-4依存、標準経路で誤って寛容statsを使うこと、connection lifetime、メモリ使用。

### PR6: 成功履歴とHTTP adapterの仕上げ

- 目的: ベストエフォートevent helperをserviceへ寄せ、2 routeをHTTP入力・変換・Response中心にする。
- 変更対象: `export_service.py`、`web.py`、event/web tests、R8文書/ROADMAP/棚卸しの完了反映。
- 変更しないもの: D7 persistence本体、schema、export event payload、route/API/output。
- 先行条件: PR5、全contract維持。
- 必要なtest: 全失敗地点の非記録、record失敗無害、UTC snapshot、再実行、Python/Playwright全件、循環import。
- 完了条件: R8対象の非HTTP進行がserviceに集まり、webはadapter、R8だけを完了に更新できる。
- リスク: Responseより前に記録する順序変更、例外握りつぶし範囲の縮小、D7との責務重複。

PR5はR7-4の非HTTP rendererだけでなく、`resolve_pdf`相当の非HTTP契約の確定・実装にも依存する。R7-4を待つ間もPR1〜PR4は各先行条件の範囲で進められるが、R8全体を完了扱いにしない。

## 23. 移行中の互換性方針

- URL、method、query default、status/detail、MIME/header、payload/file contentを変えない。
- profile未指定とstandardの互換経路を維持する。
- pure helperの`web.py` import pathは内部APIとし、monkeypatch 0件を再確認後にwrapperなしでtest importを移す。
- route関数のimport pathは維持する。
- PRごとに旧routeと新serviceを二重実行して副作用を重複させない。test fixture上のlogical output比較で検証する。
- eventの二重記録を避けるため、記録責務は1段階でのみ切り替える。
- R7-4未実装中はHTTP callbackをserviceへ仮移動して完成扱いにしない。
- rollbackは各実装PR単位のrevertで可能にする。contract test PRは残してよい。

## 24. 単一`export_service.py`案の妥当性評価

**判定: B — 単一サービスから開始できるが、内部の補助モジュール境界を設計上明示すべき。**

根拠:

- previewと実exportは、同じprofile解決、ItemStats、plan、filename、warning、pack snapshotを共有し、「packを指定条件で事前確認または生成する」という同じ変更理由を持つ。
- JSONとarchiveは出力形式が違うが、pack/profile/format解決と成功履歴を共有する。公開APIはpreview準備、export準備、成功記録の小さい集合に凝集できる。
- 低レベル責務は既に`export_profiles.py`、`export_stats.py`、`zip_export.py`、PDF/Markdown modules、`database.py`に分かれている。`export_service.py`はそれらを抱えず進行を組み立てるため、単一fileでも変更理由を過度に混在させない。
- file sizeだけを理由に新しい`export_preview_service.py`、`archive_service.py`、`export_history_service.py`へ分けると、現在はprofile/plan契約を跨ぐ引数受渡しが増え、公開APIの凝集性が下がる。
- ただし`_preview_base_stats`の基礎集計はpack statsにも使われるため`export_stats.py`へ、ZIP/manifestは`zip_export.py`へ、event INSERTは`database.py`/将来D7へ残す。この境界を崩して単一fileへ詰め込む案は採らない。

将来、JSON snapshotが別route/CLIでも再利用される、またはevent orchestrationが独立したretry/queueを持つ場合は変更理由が分かれる。その時点で補助module分割を再評価する。現時点でCの複数R8子項目化は不要である。

## 25. リスクと停止条件

### 25.1 主なリスク

- `RenderContext.resolve_pdf`またはchapter preview/archiveの`chapter_loader`から`HTTPException`が漏れ、見かけだけFastAPI非依存になる。
- standardで`collect_item_stats`を使い、invalid pageのdetailやPDF欠損の発生時点を変える。
- `_preview_base_stats`をserviceへ閉じ、R5からR8への逆向き依存を作る。
- DB接続を生成中まで保持、またはeventと生成を同一transactionにして現行順序を変える。
- JSONの空pack許可をarchiveの厳格検証に揃えてしまう。
- `Response`前にeventを記録する、またはrecord失敗を500にする。
- JST download/manifest、entryごとのMarkdown日付、UTC event日時を無自覚に統一する。
- ZIP全bytes比較で実行時metadata由来のflaky testを作る。
- warning code/message/orderを変え、UIのblocking判断を壊す。
- serviceが`web.py`をimportして循環依存を作る。

### 25.2 実装着手・継続の停止条件

- §18のstatus/body/header/file契約を維持できない。
- service公開APIにFastAPI型またはHTTP statusを持ち込む必要がある。
- preview/archive移動に`HTTPException`を送出する`resolve_pdf`/`chapter_loader` callbackが必須で、非HTTPのPDF解決境界を確定・実装できない。
- PDF source不在と一般FileNotFoundを404/500へ正しく区別できない。
- eventをResponse生成後・別接続・best effortのまま維持できない。
- R5、R7、D7の大規模再設計やDB schema変更が必要になる。
- 現行コード・testが本書の確認済み事実と一致しない。
- Python/Playwrightの必須checkが再現性をもって失敗する。
- `export_service -> web`、下位module→serviceの循環依存が必要になる。

停止条件に該当した場合、範囲を拡張せず設計文書を更新するPRへ戻す。

## 26. 未確認・実装前に確定する事項

- 候補結果型`PreparedPackExport`とpack不在表現の最終名称。実コードには未存在。
- PDF source不在を`RenderContext.resolve_pdf`と`chapter_loader`で共通して通知できる非HTTP例外/result、およびR7-4とR8各PRの実装順。現行契約を確認するまで具体型を確定しない。
- `export_stats.py`へ置く共有基礎集計helperの名称と、dict/dataclassのどちらを返すか。
- archiveの単一`exported_at`と、現在のMarkdown rendererごとの時刻取得をどう接続するか。現状変更はしない。
- JSONのkey/whitespaceを移動中互換だけでなく恒久的bytes contractとするか。今回は変更しない。
- frameworkが自動付与する`Content-Length`やZIP entry timestampを恒久契約にする必要は確認できていない。明示契約には含めない。
- productionからR8 helperを直接importする外部利用者の有無はrepo内検索では確認できない。repo内monkeypatchは0件であり、内部APIとしてwrapperなし移動を提案する。

## 27. 完了条件

- 2 routeのHTTP契約とJSON/ZIP/Markdown/PDF logical contentが維持される。
- `web.py`に残るのがquery受領、HTTP変換、Response/header、Response後の記録指示である。
- profile/format、preview、JSON/archive進行、best-effort記録がFastAPI非依存の`export_service.py`へ移る。
- 基礎統計、profile、ZIP、PDF/Markdown、DB永続化がそれぞれ既存module境界に残る。
- serviceが`web.py`/FastAPIをimportせず、`RenderContext.resolve_pdf`とchapter preview/archiveの`chapter_loader`を含むcallback経由でも`HTTPException`を通さない。PDF不在の非HTTP表現からHTTP 404への変換はweb adapterだけが行う。
- read/生成/eventの接続・transaction・失敗順が§16どおりである。
- characterization test、service test、HTTP test、Python全件、Playwright全件が成功する。
- 循環importがなく、R5/R7/D7の進捗を誤って変更しない。
- 実装完了時だけROADMAPと棚卸しのR8を完了へ更新する。本設計PR時点では未完了のままとする。
