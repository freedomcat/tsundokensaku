## Context

[R8詳細設計](../../../docs/refactoring/r8-export-service.md)（PR1）と[PR2の現行契約characterization test](../archive/2026-08-19-r8-pr2-characterization-tests/)により、`web.py`に残るexport業務ロジックの現状と、移動前に固定すべき契約はテストとして確定済みである。本設計は、その安全網の上でPR3（previewとrequest policyの分離）を実装できる粒度まで具体化する。

現在`src/tsundokensaku/web.py`に存在する対象関数（行番号は本設計作成時点）:

| 関数/定数 | 現在行 |
|---|---:|
| `EXTERNALLY_AVAILABLE_EXPORT_PROFILES` | 90 |
| `_now_jst` | 163 |
| `_resolve_pdf_file_or_404` | 383 |
| `_pack_connection` | 570 |
| `_export_preview_warning` | 785 |
| `build_export_preview_warnings` | 789 |
| `_preview_base_stats` | 828 |
| `build_export_preview_payload` | 847 |
| `build_export_preview_payload_for_profile` | 856 |
| `api_preview_pack_export` | 921 |
| `_export_pack_json` | 952 |
| `_placeholder_item_stats_for_export` | 978 |
| `_export_pack_archive` | 998 |
| `_resolve_export_profile_or_400` | 1095 |
| `api_export_pack` | 1105 |
| `api_list_pack_stats` | 629 |

R8詳細設計書§19.2で列挙された16契約項目は、`tests/test_web.py`の次のクラスに固定済みである。本PRはこれらのテストが（配置先の変更以外で）壊れないことを完了条件とする。

- `ExportPreviewWarningContractTest`（warning exact dict・優先順位・順序・item→plan連結順）
- `PackStatsApiTest.test_stats_and_export_preview_share_the_same_four_aggregate_values`（stats共有4値）
- `BuildExportPreviewPayloadTest` / `BuildExportPreviewPayloadForProfileTest`（field集合・拡張chunk item）
- `ExportProfileParameterTest`（profile/format validation順序、既存間接テスト）
- `ExportJsonContractTest` / `ExportZipFixedClockContractTest`（JSON/ZIP fixed clock契約。PR3では移動しないため無変更）
- `ExportStatsCollectionStrategyTest`（standard/chat/chapterのstats収集戦略。PR3では移動しないため無変更）
- `ExportEventRecordingTest.test_read_connection_closes_before_event_connection_opens`（DB接続順序。PR3では移動しないため無変更）
- `ExportPdfResolutionCallbackBoundaryTest`（PDF解決callbackの現状実装詳細。previewの`chapter_loader`部分は本PRで意図的に置き換える）

`src/tsundokensaku/pdf_export.py`には既に`PdfSourceNotFoundError(FileNotFoundError)`が存在し、`save_pdf_export_to_configured_dir`（R7-1のPDF保存API）が使用している。`web.py`の`save_export_pdf`ルート（1534行目付近）は、この非HTTP例外を捕捉して`HTTPException(404, "PDF not found")`へ変換する既存パターンを持つ。6章でこの既存資産を再利用する。

## Goals / Non-Goals

**Goals:**

- FastAPI非依存の`export_service.py`を新設し、profile/format policy、preview warning、preview projection、preview DB orchestrationを移す。
- `_preview_base_stats`が使う共有基礎集計（`TextStats`合算を含む中間値）を`export_stats.py`へ切り出し、`GET /api/packs/stats`が`export_service.py`へ依存しない依存グラフにする。
- previewの`chapter_loader`から`HTTPException`経路を排除し、`export_service.py`・そこへ渡すcallbackがFastAPI型を一切扱わない状態にする。
- `web.py`の2ルートを、HTTP入力・HTTP変換・Response生成中心のadapterへ近づける（JSON/archiveオーケストレーション自体はPR3の対象外のため、`web.py`にまだ残る）。
- 責務移動に応じてテスト配置を整理する（`tests/test_export_service.py`新設、`tests/test_export_stats.py`拡張、`tests/test_web.py`はHTTP adapter契約中心へ縮小）。

**Non-Goals:**

- `_export_pack_json`・`_export_pack_archive`・`record_export_event`呼び出しの移動（PR4〜PR6）。
- archiveの`RenderContext.resolve_pdf`・archive用`chapter_loader`の非HTTP化（PR5）。
- profile/format/warning/JSON/ZIPのアルゴリズムや文言の変更。
- DB schema・transaction境界の変更。

## Decisions

### 1. 依存方向

```text
templates / JavaScript
  -> web.py (FastAPI route定義、HTTP変換、Response/header生成)
      -> export_service.py (request policy, preview warning/projection/orchestration)
          -> export_stats.py (ItemStats収集、共有基礎集計)
          -> export_profiles.py (profile解決の実体、plan)
          -> pdf_export.py (resolve_pdf_source、PdfSourceNotFoundError — 非HTTP PDF解決)
          -> pdf_outline.py (list_chapters)
          -> database.py (connect, ensure_pack_schema, pack/items read)

export_stats.py, export_profiles.py, pdf_export.py, pdf_outline.py, database.py -X-> export_service.py
export_service.py -X-> web.py / FastAPI
```

`export_service.py`は`fastapi`を一切importしない。`web.py`から`export_service.py`への一方向依存のみを許可し、`export_service.py`が`web.py`をimportして呼び戻す設計（callback越しのHTTP例外送出を含む）は禁止する。`api_list_pack_stats`（R5/R9、PR3対象外）は`export_stats.py`を直接importし、`export_service.py`を経由しない。

### 2. `export_service.py`の責務（現在の関数からの移動一覧）

| 現在の関数/定数 | 現在地 | 分類 | PR3での扱い |
|---|---|---|---|
| `EXTERNALLY_AVAILABLE_EXPORT_PROFILES` | `web.py`定数 | request policy | `export_service.py`へ移動 |
| `_resolve_export_profile_or_400` | `web.py` | request policy | 非HTTP部分を`resolve_external_profile`として`export_service.py`へ。400変換のみ`web.py`に残す |
| format既定値解決・validation（現状`api_export_pack`冒頭にinline） | `web.py` | request policy | `resolve_export_format`として`export_service.py`へ新規抽出。400変換のみ`web.py`に残す |
| `_export_preview_warning` | `web.py` | preview warning | `export_service.py`のprivate helperへ移動 |
| `build_export_preview_warnings` | `web.py` | preview warning | `export_service.py`のpublicへ移動 |
| `_preview_base_stats` | `web.py` | 共有基礎集計 + projection | 共有基礎集計（`PackItemStatsSummary`: `book_count`/`item_count`/`total_pages`/`combined_stats: TextStats`）の算出を`export_stats.summarize_item_stats`へ切り出し、7 field dictへのprojection（`estimation`/`estimator`/`estimated_chars`/`estimated_tokens`の算出）は`export_service.py`に残す（後述5章） |
| `build_export_preview_payload` | `web.py` | preview projection | `export_service.py`のpublicへ移動 |
| `build_export_preview_payload_for_profile` | `web.py` | preview projection | `export_service.py`のpublicへ移動 |
| `api_preview_pack_export`のDB read/close/plan進行部分（pack/items取得、`collect_item_stats`、close、chapter_loader構築、payload組み立て呼び出し） | `web.py` inline | preview DB orchestration | `export_service.py`の新規公開関数`build_pack_export_preview`へ抽出 |
| previewの`chapter_loader`構築（`lambda pdf_path: list_chapters(_resolve_pdf_file_or_404(...))`） | `web.py` inline | preview DB orchestration + PDF解決の非HTTP化 | `export_service.py`内で`pdf_export.resolve_pdf_source`（新設）を使って構築。`HTTPException`を経由せず、不在時は既存`PdfSourceNotFoundError`を送出する |
| `_export_pack_json` | `web.py` | JSON export orchestration | **PR3では移動しない**（PR4） |
| `_placeholder_item_stats_for_export` | `web.py` | archiveのstats収集戦略 | **PR3では移動しない**（PR5） |
| `_export_pack_archive` | `web.py` | archive orchestration | **PR3では移動しない**（PR5） |
| `_pack_connection` | `web.py` | DB adapter（汎用） | **`web.py`に残す**（他routeも使用）。`export_service.py`は同等ロジックを内部で独自に持つ（後述7章） |
| `record_export_event`呼び出し | `web.py`（`api_export_pack`末尾） | 成功履歴 | **PR3では移動しない**（PR6） |
| `_resolve_pdf_file_or_404` | `web.py` | HTTP変換（archive/PDF不在の404） | **`web.py`に残す**。内部実装は`pdf_export.resolve_pdf_source`を呼び`PdfSourceNotFoundError`を捕捉して404へ変換する薄いwrapperへ整理する（外部契約不変） |
| `api_list_pack_stats` | `web.py` | R5/R9（対象外） | 変更しない。`export_stats.py`の共有関数を直接使うようimportを差し替えるのみ |

### 3. request policyの契約

現在のvalidation順序（`ExportProfileParameterTest`で固定済み）:

1. profile解決（`resolve_profile`→unlisted判定）。失敗は400 `不明なエクスポートプロファイルです: {name}`。
2. （exportのみ）format解決・validation。失敗は400 `format は pdf, md, または json を指定してください`、または400 `profile={name} では format={primary} のみ指定できます`。
3. pack存在確認。失敗は404 `資料が見つかりません`。

`export_service.py`はHTTP statusを一切知らない形でこれを提供する。

```python
def resolve_external_profile(name: str | None) -> ExportProfile:
    """既存 export_profiles.resolve_profile を呼び、外部公開profileかを検証する。
    不明・非公開なら ValueError(profile_name) を送出する。"""

def resolve_export_format(profile: ExportProfile, requested_format: str | None) -> str:
    """format省略時はprofile.primary_formatまたは既定"pdf"を返す。
    pdf/md/json以外、またはprimary_formatとの不一致は ValueError(message) を送出する。
    メッセージ文言は現行HTTP detailと同一にする。"""
```

- `resolve_external_profile`が送出する`ValueError`のメッセージは、unlisted・unknownいずれも解決名（`profile_name`または`profile.name`）そのものとする。`web.py`は`f"不明なエクスポートプロファイルです: {exc}"`という現行文言で400へ変換する（`ExceptionのstrがValueError引数と一致するため、unlisted/unknownの2パターンとも現行メッセージを維持できる）。
- `resolve_export_format`が送出する`ValueError`のメッセージは、現行の2種類の文言（`format は...`、`profile=...では format=...のみ指定できます`）をそのまま使う。`web.py`は`str(exc)`をdetailとして400へ変換する。
- `web.py`はHTTPExceptionを使わず、`ValueError`の`try/except`だけで変換する。validation順序（profile→format→pack）は`web.py`のroute内での呼び出し順で維持する。
- `resolve_export_format`は`api_export_pack`でのみ呼ぶ。`api_preview_pack_export`はprofile解決のみでよい（現状どおりformatパラメータを受け取らない）。

### 4. preview契約

PR2の`tests/test_web.py`で固定済みの契約と、移動後の対応:

| 契約 | 固定済みテスト | 移動後の対応 |
|---|---|---|
| standard previewのfield集合（`estimation`, `estimator`, `book_count`, `item_count`, `total_pages`, `estimated_chars`, `estimated_tokens`, `warnings`の8個。`profile`/`file_count`/`archive`/`chunks`を含まない） | `BuildExportPreviewPayloadTest.test_seven_base_stat_fields_exact_values`、`PackExportPreviewTest` | `build_export_preview_payload`が`export_service.py`に移った後も同一dictを返す |
| profile previewの拡張field（`profile`, `file_count`, `archive`, `chunks`。chunk itemは`item_id`/`title`/`pdf_path`/`pages`/`label`/`fragment_index`/`fragment_count`/`estimated_tokens`） | `BuildExportPreviewPayloadForProfileTest` | `build_export_preview_payload_for_profile`が`export_service.py`に移った後も同一dictを返す |
| warning code 4種（`missing_pdf`/`missing_pages`/`invalid_pages`/`unindexed_pages`）のexact dict、同一項目内優先順位（`missing_pdf`→`missing_pages`→`invalid_pages`→`unindexed_pages`） | `ExportPreviewWarningContractTest` | `build_export_preview_warnings`が`export_service.py`に移った後も同一ロジック・同一文言 |
| 複数項目のwarningがposition順に出ること、item warning→plan warningの連結順 | `ExportPreviewWarningContractTest.test_multiple_items_warnings_follow_position_order` / `test_item_warnings_precede_plan_warnings_in_profile_payload` | 変更なし。`build_export_preview_payload_for_profile`内の`item_warnings + plan_warnings`という連結順を維持する |
| blocking/non-blocking UI契約（`empty_pack`/`missing_pdf`/`missing_pages`/`invalid_pages`はblocking、`unindexed_pages`とplan warningはnon-blocking） | `tests/playwright/ai_export_flow.spec.js` | Python側のwarning code・メッセージが不変である限りPlaywright側の変更は不要 |
| empty pack（`item_stats`が空リスト） | `build_export_preview_payload([])` / `build_export_preview_payload_for_profile([], ...)` | `empty_pack` warning・`book_count=0`等を返す現行ロジックのまま移動 |
| missing PDF / missing pages / invalid pages / unindexed pages | `ExportPreviewWarningContractTest`各テスト | `collect_item_stats`（`export_stats.py`、変更なし）が返す`ItemStats`をそのまま`build_export_preview_warnings`へ渡す現行フローを維持 |

standard previewとprofile previewの分岐（`resolved_profile.uses_plan_output`によるルーティング）は`export_service.py`の`build_pack_export_preview`内に置く（現状`api_preview_pack_export`にある分岐をそのまま移す）。

### 5. `/api/packs/stats`との境界

現状`_preview_base_stats`（web.py 828-844行目）は、`book_count`/`item_count`/`total_pages`の3値に加え、`TextStats`合算値（`cjk_chars`合計・`other_chars`合計）から`estimated_chars`（単純な加算）と`estimated_tokens`（`estimate_tokens()`呼び出し）を導出している。`api_list_pack_stats`（R5/R9）は`book_count`/`item_count`/`total_pages`/`estimated_tokens`の4値だけを使う（`estimation`/`estimator`/`estimated_chars`は使わない）。

これをR8がそのまま抱えると、R5（`api_list_pack_stats`）からR8（`export_service.py`）への逆依存が生じる（設計書§10.3・§25.1で名指しされているリスク）。一方、「4値の完成品」だけを共有関数にすると、`estimated_chars`を組み立てる`export_service.py`側で`TextStats`合算（`cjk_chars`/`other_chars`の集計）をもう一度やり直すことになり、二重計算になる（SHOULD 1）。

**採用する境界**: `TextStats`合算を含む中間集計値そのものを共有し、7 field / 4 fieldへのprojection（`estimation`/`estimator`/`estimated_chars`の付加、あるいは`estimated_tokens`だけの抽出）は各consumer側で行う。

```python
# export_stats.py
@dataclass(frozen=True)
class PackItemStatsSummary:
    book_count: int
    item_count: int
    total_pages: int
    combined_stats: TextStats  # cjk_chars/other_charsの合算値。estimated_chars/estimated_tokensの元になる中間値

def summarize_item_stats(item_stats: list[ItemStats]) -> PackItemStatsSummary:
    """book_count（distinct pdf_path数）、item_count、total_pages、TextStats合算のみを返す。
    estimation/estimator/estimated_chars/estimated_tokensの算出（projection）はconsumer側が行う。"""
```

- `PackItemStatsSummary`は既存`ItemStats`・`TextStats`と同じ`@dataclass(frozen=True)`パターンに揃えた1個のdataclassであり、新しい抽象レイヤーを追加するものではない。
- `export_service.py`の`_preview_base_stats`相当（7 field）は、`summarize_item_stats`が返す`PackItemStatsSummary`から`book_count`/`item_count`/`total_pages`をそのまま使い、`estimated_chars = combined_stats.cjk_chars + combined_stats.other_chars`、`estimated_tokens = estimate_tokens(combined_stats)`を算出し、`estimation`/`estimator`（`ESTIMATOR_NAME`）を追加してdictを組み立てる。
- `api_list_pack_stats`（`web.py`、R5/R9）は`export_stats.summarize_item_stats`を直接importし、`book_count`/`item_count`/`total_pages`をそのまま使い、`estimated_tokens = estimate_tokens(summary.combined_stats)`だけを追加で計算する（`token_estimate.estimate_tokens`は既存の共有関数であり、R8依存ではない）。`export_service.py`をimportしない。
- これにより、`item_stats`から`TextStats`を合算する計算（`sum(entry.stats.cjk_chars for entry in item_stats)`等）は`summarize_item_stats`内の1箇所だけで行われ、previewと`/api/packs/stats`のどちらも二重に計算しない。
- `PackStatsApiTest.test_stats_and_export_preview_share_the_same_four_aggregate_values`が、移動後も`api_list_pack_stats`と`api_preview_pack_export`の4値一致を検証し続ける。
- 将来`/api/packs/stats`が`estimated_chars`等を追加で必要とする場合は、`PackItemStatsSummary.combined_stats`から同様に導出できる。現時点でこれ以上の汎用化（複数の推定方式、キャッシュ等）は行わない。

### 6. chapter previewの非HTTP失敗契約

現状（PR2 characterization testで確認済み）:

- `_resolve_pdf_file_or_404(pdf_path, books_dir)`は、PDFが存在しない場合`HTTPException(404, "PDF not found")`を直接送出する（`ExportPdfResolutionCallbackBoundaryTest.test_resolve_pdf_file_or_404_raises_http_exception_directly`）。
- previewの`chapter_loader`（`lambda pdf_path: list_chapters(_resolve_pdf_file_or_404(...))`）はこの関数をそのまま呼ぶ。
- ただし`export_profiles.ChapterProfile.split_items_with_warnings`は、`stats.missing_pdf`な項目に対して`chapter_loader`を呼ばずにfragment化するショートサーキットを持つ（`ExportPdfResolutionCallbackBoundaryTest.test_chapter_loader_is_not_invoked_for_missing_pdf_items`）。そのため、PDF欠損項目単体ではchapter previewが`HTTPException`へ到達せず、既存の200 warning契約が保たれている。

#### 6.1 既存の`PdfSourceNotFoundError`を再利用する

`src/tsundokensaku/pdf_export.py`には既に次の型が存在する。

```python
class PdfSourceNotFoundError(FileNotFoundError):
    pass
```

これは`pdf_export.save_pdf_export_to_configured_dir`（R7-1のPDF保存API、`paths.resolve_pdf_path`が`None`を返した場合に送出）が既に使っている型であり、`web.py`の`save_export_pdf`ルートは次のように、**service側が`PdfSourceNotFoundError`を送出し、web adapterがそれを捕捉して404へ変換する**という、まさに本PRが必要とするパターンを既に実装済みである。

```python
# web.py 既存コード（save_export_pdf内）
except PdfSourceNotFoundError as exc:
    raise HTTPException(status_code=404, detail="PDF not found") from exc
```

plain `FileNotFoundError`をservice境界の契約にすると、一般のfilesystem/保存先の`FileNotFoundError`（設定ミス等）と「PDF sourceが見つからない」という意味を区別できなくなる（レビュー指摘MUST 2）。`PdfSourceNotFoundError`はこの区別のために既に導入されている専用型であり、これを再利用する。

**比較した配置案と採否**:

| 案 | 内容 | 採否 |
|---|---|---|
| 1. 既存`PdfSourceNotFoundError`を再利用 | `pdf_export.py`にある型をそのまま使う | **採用** |
| 2. PDF source解決責務を持つmoduleへ共通例外を配置 | `paths.py`に新しい例外を作る、または`PdfSourceNotFoundError`を`paths.py`へ移動する | 不採用。`paths.py`の`resolve_pdf_path`は現状`None`を返す設計で例外を送出しない。`PdfSourceNotFoundError`を送出する既存コード（`pdf_export.save_pdf_export_to_configured_dir`、`web.py`の`save_export_pdf`）は`paths.py`ではなく`pdf_export.py`に依存しており、型を移動すると影響範囲がPR3のスコープを超えて広がる |
| 3. PR3専用の非HTTP source-not-found例外を新設 | `export_service.py`に新しい例外を作る | 不採用。既に同じ意味の例外型（`PdfSourceNotFoundError`）が存在するため、新設は「同じ意味の例外型を不要に複数作らない」という制約に反する |

**採用理由**: `pdf_export.py`は既に「PDF sourceを解決してレンダリングする」責務（`save_pdf_export_to_configured_dir`）を持ち、`paths.py`（path文字列処理・URL生成、R3）よりも「PDF source」というdomain的意味の所有元として自然である。`export_stats.py`・`export_profiles.py`は既に`pdf_export.py`をimportしており（`export_stats.py -> pdf_export.py`、`export_profiles.py -> pdf_export.py`）、`export_service.py`が新たに`pdf_export.py`をimportしても既存の依存方向と整合し、循環importは生じない。

#### 6.2 採用する非HTTP化の方式

1. `pdf_export.py`に非HTTPの解決関数を新設する。

   ```python
   # pdf_export.py
   def resolve_pdf_source(pdf_path: str, books_dir: Path) -> Path:
       """books_dir配下のPDFを解決する。存在しない・境界外なら PdfSourceNotFoundError を送出する。
       HTTP型を一切扱わない。"""
       relative = paths.resolve_pdf_path(pdf_path, books_dir)
       if relative is None:
           raise PdfSourceNotFoundError(pdf_path)
       return books_dir.expanduser().resolve() / relative
   ```

   `save_pdf_export_to_configured_dir`内にある同等ロジック（`paths.resolve_pdf_path`→`None`→`PdfSourceNotFoundError`）はこの新関数の呼び出しへ置き換え、重複を解消する（実装はPR3のtaskで行う。design時点では方針の明記に留める）。
2. `web.py`の`_resolve_pdf_file_or_404`は、`pdf_export.resolve_pdf_source`を呼び`PdfSourceNotFoundError`を捕捉して`HTTPException(404, "PDF not found")`に変換する薄いwrapperへ整理する。シグネチャ・例外型（呼び出し元から見た`HTTPException`という結果）・メッセージは現行と完全に同一のため、`_resolve_pdf_file_or_404`を直接呼ぶ既存テスト（`ExportPdfResolutionCallbackBoundaryTest.test_resolve_pdf_file_or_404_raises_http_exception_directly`）は変更なく通る。archiveの`RenderContext.resolve_pdf`とarchive用`chapter_loader`はこの関数を引き続き使い、挙動は一切変わらない（PR3ではarchive経路を変更しない）。
3. previewの`chapter_loader`は`export_service.py`内で`pdf_export.resolve_pdf_source`を直接使うよう新規に構築する（`_resolve_pdf_file_or_404`を経由しない）。`list_chapters(pdf_export.resolve_pdf_source(str(pdf_path), books_dir))`という形になる。`export_service.py`・このcallbackのいずれも`HTTPException`を送出しない。
4. `ChapterProfile.split_items_with_warnings`のショートサーキットにより、previewでPDF欠損項目に対して`chapter_loader`（＝`resolve_pdf_source`）が呼ばれることは現状通りない。したがって、通常のmissing PDF warning経路（`build_export_preview_warnings`が`missing_pdf` warningを返す経路）はHTTP 200を維持する。これは変更しない既存契約である。
5. **race時の404契約**: `missing_pdf`ではないと判定された項目（`collect_item_stats`のPDF存在確認を通過した項目）について、`chapter_loader`呼び出し時点でファイルが消失しているレースケースでは、`pdf_export.resolve_pdf_source`が`PdfSourceNotFoundError`を送出する。これは`export_service.build_pack_export_preview`から`web.py`まで素通しし、`api_preview_pack_export`が`except PdfSourceNotFoundError`で捕捉して`HTTPException(404, "PDF not found")`へ変換する（`save_export_pdf`の既存パターンと同一の変換）。これにより、現状同じレースケースで404だった挙動が維持される。`web.py`のadapterだけがこの変換を行い、`export_service.py`・callbackは`HTTPException`を一切扱わない。

archive側（`_export_pack_archive`のRenderContext.resolve_pdfとarchive用chapter_loader）は、PR3では一切変更しない。これらはPR5の先行条件（R7-4の非HTTP PDF/Markdown APIと`resolve_pdf`相当の非HTTP契約が確定・実装済みであること）が満たされてから着手する。

### 7. DB connection lifetime

previewの現行順序（design書§5.1、PR2で変更されていない）を`export_service.py`の`build_pack_export_preview`内でそのまま維持する。

1. `connect(db_path)` → `ensure_pack_schema(connection)`。
2. `get_pack(connection, pack_id)`。`None`ならこの時点で接続をcloseして`None`を返す（`web.py`が404へ変換する）。
3. `get_pack_items(connection, pack_id)`。
4. `collect_item_stats(connection, items, books_dir=books_dir)`。
5. `connection.close()`。
6. closeの後で、`resolved_profile.uses_plan_output`によりstandard/profile分岐。profile分岐では`resolved_profile.needs_chapter_loader`に応じて`chapter_loader`を構築し、`profile.plan(item_stats, chapter_loader=chapter_loader)`を呼ぶ（`plan()`内で`chapter_loader`経由のPDF読み取りが必要な場合だけ発生する）。

`export_service.py`は`_pack_connection`（`web.py`、他routeも使う汎用ヘルパー）をimportしない。`export_service.py`内に同等の処理を用意する。

```python
# export_service.py（private、preview限定の最小helper。または直書き）
def _open_pack_connection(db_path: Path):
    connection = database.connect(db_path)
    database.ensure_pack_schema(connection)
    return connection
```

これは`_pack_connection`とロジックが重複するが、設計書§9・§11のとおり`_pack_connection`はR8専用でないため`web.py`に残す方針であり、`export_service.py`が`web.py`をimportして使うことは循環依存になるため避ける。

**過剰抽象化しないための非目標（D2/D3の先取り禁止）**: この2行はD2（接続管理）・D3（schema初期化・保証・移行）の責務先取りにあたらない、`build_pack_export_preview`専用の最小限の配線として扱う。次のものは本PRで導入しない。

- 汎用のtransaction管理（複数routeで共有するcontext manager等）
- connection pool
- repository abstraction（DAO層の新設）
- schema migrationの方針決定
- D2/D3で検討予定の共通接続管理の先取り実装

`_open_pack_connection`という名前・関数化自体が必要かどうか（`build_pack_export_preview`内に2行のまま直書きするか、private helperとして切り出すか）は実装時のコードの見た目で判断してよいが、**独立した新しいDB接続管理の抽象を導入しない**という制約は固定する。

PR3ではtransaction境界・接続本数を変更しない。previewは現状どおり1接続で完結する（chat/chapterでも追加のstats接続は開かない。追加のstats接続を開くのはarchiveだけであり、これはPR5スコープ）。

### 8. `web.py`に残すもの

PR3完了時点で`web.py`に残る責務:

- 2つのGET route定義とFastAPIのpath/query受領（`pack_id: int`、`profile: str | None`、`format: str | None`）。
- `export_service.resolve_external_profile` / `resolve_export_format`が送出する`ValueError`を400へ変換する処理（detail文言はValueErrorのメッセージをそのまま使う）。
- `export_service.build_pack_export_preview`が返す`None`を404 `資料が見つかりません`へ変換する処理。
- `export_service.build_pack_export_preview`から伝播しうる`PdfSourceNotFoundError`を404 `PDF not found`へ変換する処理（6章2.5）。
- `JSONResponse`の生成（preview）。
- `_export_pack_json`、`_placeholder_item_stats_for_export`、`_export_pack_archive`（PR3では未移動。PR4/PR5で移す）。
- `_pack_connection`（他routeも使う汎用DB adapter）。
- `_resolve_pdf_file_or_404`（archiveから引き続き使われる。previewの`PdfSourceNotFoundError`を404へ変換する処理も含む。内部実装は`pdf_export.resolve_pdf_source`を呼ぶ薄いwrapperへ整理する）。
- `record_export_event`呼び出し（`api_export_pack`末尾、PR3では変更しない）。
- `api_list_pack_stats`（R5/R9、対象外。importだけ`export_stats.py`へ差し替え）。
- `EXTERNALLY_AVAILABLE_EXPORT_PROFILES`は`export_service.py`へ移すため`web.py`からは削除する。

### 9. テスト配置

| 配置先 | 内容 |
|---|---|
| 新設`tests/test_export_service.py` | pure preview logic: `resolve_external_profile`/`resolve_export_format`の単体テスト（現行`ExportProfileParameterTest`のうち、直接関数呼び出しで完結する部分を移設）、`build_export_preview_warnings`/`build_export_preview_payload`/`build_export_preview_payload_for_profile`の直接テスト（`BuildExportPreviewPayloadTest`、`ExportPreviewWarningContractTest`、`BuildExportPreviewPayloadForProfileTest`を移設）。DB orchestration: `build_pack_export_preview`のDB統合テスト（`PackExportPreviewTest`を移設、直接関数呼び出しレベル）。previewの`chapter_loader`が`PdfSourceNotFoundError`を送出する場合／送出しない場合の直接テスト（`ExportPdfResolutionCallbackBoundaryTest`のうちpreviewの`chapter_loader`に関する部分を移設し、`PdfSourceNotFoundError`ベースへ更新）。payload generation: 上記projectionの直接テスト |
| `tests/test_export_stats.py`（既存拡張） | `export_stats.summarize_item_stats`（`PackItemStatsSummary`を返す）の単体テストを追加 |
| `tests/test_web.py`（縮小、ただしHTTP contract testは明示的に残す） | route/TestClient経由の契約に限定し、以下を**最低限**残す：(1) pack不在時のHTTP status/detail（404 `資料が見つかりません`）、(2) profile/format不正時のHTTP status/detail（400、各メッセージ文言）、(3) preview成功時の200とJSON response shape（standard/profile双方、`TestClient`経由でContent-Type確認を含む）、(4) previewで`PdfSourceNotFoundError`が送出された場合に`api_preview_pack_export`が404 `PDF not found`へ変換すること（`export_service.build_pack_export_preview`をmonkeypatchして`PdfSourceNotFoundError`を強制発生させ、web adapterの変換だけを検証する新規テスト）、(5) 通常のmissing PDF warning経路が200を維持すること（`test_chapter_preview_missing_pdf_item_keeps_200_warning_contract`相当をTestClient経由で残す）。`_resolve_pdf_file_or_404`がarchiveから呼ばれる際に404を返すこと（`test_resolve_pdf_file_or_404_raises_http_exception_directly`）も残す。`PackStatsApiTest`（`api_list_pack_stats`本体、R8対象外）は残す。`ExportJsonContractTest`/`ExportZipFixedClockContractTest`/`ExportStatsCollectionStrategyTest`/`ExportClockCallCountTest`/`ExportArchiveBackwardCompatibilityTest`/`ExportEventRecordingTest`はPR3で移動しない`_export_pack_json`/`_export_pack_archive`/`record_export_event`のテストのためそのまま残す |

**service testとHTTP testの重複回避**: (1)〜(5)のHTTP contract testは「HTTPステータス・detail・JSON shapeがadapterとして正しく変換されること」だけを検証し、warning生成ロジックの全パターン（優先順位・複数項目順など）の再検証は行わない（それは`tests/test_export_service.py`の責務）。逆に`tests/test_export_service.py`はHTTPステータスやHTTPヘッダーを一切アサートしない。同じ入力に対して同じアサートを両ファイルへ重複コピーしない。

`pack stats test`（`PackStatsApiTest`）は`api_list_pack_stats`がR8対象外routeであるため`tests/test_web.py`に残し、`export_service.py`のテストへ重複させない。`profile test`のうち、`export_profiles.py`本体（plan/warningアルゴリズム）の単体テストは既存`tests/test_export_profiles.py`のまま変更しない。Playwrightは、warning code・メッセージ・順序が不変であるため、`tests/playwright/ai_export_flow.spec.js`の変更は不要と見込む。

`tests/test_web.py`から`export_service.py`へ移す対象関数へのimportは、既存の`from tsundokensaku import web`ではなく`from tsundokensaku import export_service`へ差し替える。R6/R7-1と同じ判断基準（設計書§20）に従い、`web.py`に互換importのwrapperは残さない。ただし`api_preview_pack_export`・`api_export_pack`という2つのroute関数のimport pathは`web.py`のまま維持する。

### 10. 循環依存

新しい依存グラフ:

```text
web.py -> export_service.py -> export_stats.py, export_profiles.py, pdf_export.py, pdf_outline.py, database.py
web.py -> pdf_export.py (既存 _resolve_pdf_file_or_404 が新設 resolve_pdf_source を呼び、PdfSourceNotFoundError を捕捉する)
web.py -> export_stats.py (api_list_pack_stats が summarize_item_stats を直接使う)

# 既存（変更しない）
export_stats.py -> pdf_export.py -> paths.py
export_profiles.py -> pdf_export.py, export_stats.py
```

`export_service.py`、`export_stats.py`、`export_profiles.py`、`pdf_export.py`、`paths.py`、`pdf_outline.py`、`database.py`のいずれも`web.py`をimportしない。`export_service.py`が`pdf_export.py`をimportするのは、既存の`export_stats.py -> pdf_export.py`・`export_profiles.py -> pdf_export.py`という依存方向と同じ向きであり、新しい循環を作らない。`export_service.py`は`web.py`の`_pack_connection`や`_resolve_pdf_file_or_404`をimportして使うことは行わない（7章・6章のとおり、同等ロジックを`export_service.py`・`pdf_export.py`側に独自に持つ）。これにより`export_service -> web`という逆依存経路は生じない。

## Risks / Trade-offs

- [`_preview_base_stats`を素朴に`export_service.py`へ丸ごと移すと、`api_list_pack_stats`（R5）が`export_service.py`（R8）へ依存する逆向き依存になる] → `TextStats`合算を含む中間集計（`PackItemStatsSummary`）を`export_stats.py`へ切り出し、`api_list_pack_stats`はそちらを直接参照する（5章）。
- [previewの`chapter_loader`を非HTTP化する際、レースコンディション（`missing_pdf`ではないと判定された項目のPDFが読み取り直前に消失する）で現状404だったものが挙動を変えてしまう] → `pdf_export.resolve_pdf_source`が送出する既存`PdfSourceNotFoundError`を`web.py`の`api_preview_pack_export`が捕捉し、`save_export_pdf`と同じパターンで404 `PDF not found`へ変換する（6章2.5）。挙動は現状と同一のまま維持される。
- [`export_service.py`内に`_pack_connection`相当のロジックを複製することで、`ensure_pack_schema`呼び出し漏れなどの保守コストが2箇所に分散する] → 複製は`connect`+`ensure_pack_schema`の2行のみであり、D2/D3側の変更で将来的に共通ヘルパー化する余地を残す。PR3ではtransaction境界を変えないこと・新しいDB接続管理抽象を導入しないことを優先し、複製を許容する（7章）。
- [テスト移動時に、`export_service.py`のimport元を`tests/test_web.py`から`tests/test_export_service.py`へ切り替える過程で、意図せず契約の一部を検証し漏らす] → PR2で追加した16契約項目のテストクラス一覧（Context章）を移動チェックリストとして使い、移動前後で対応関係を確認する。service testとHTTP contract testの責務分担（9章）に沿って、重複させず両方を残す。
- [`pdf_export.py`に`resolve_pdf_source`を追加し既存`save_pdf_export_to_configured_dir`の重複ロジックを置き換える際、既存の`/export-pdf/save`エンドポイントの挙動（保存先チェック等）を誤って変えてしまう] → 置き換えるのは「PDF source解決→`PdfSourceNotFoundError`」の部分のみとし、保存先ディレクトリの検証・ファイル名決定など他のロジックには触れない。既存テスト（`tests/test_pdf_export.py`等）で回帰を確認する。

## Migration Plan

本changeは責務移動のみで、外部契約（URL、status、body、header）を変えない。ロールバックは実装PR単位のrevertで完結する。`export_service.py`・`export_stats.py`・`pdf_export.py`の新規関数はPR3のコミット内でのみ導入されるため、途中状態を公開ブランチに残さない（1PR内で完結させる）。

## Open Questions

なし。PDF source不在の非HTTP契約（既存`PdfSourceNotFoundError`の再利用、`pdf_export.py`への配置、web adapterでの404変換）は6章で確定した。共有基礎集計の責務境界（5章）、DB接続helperの非目標（7章）、service/HTTP testの分担（9章）もそれぞれ確定済みである。
