## 利用者から見て変わらないこと

- 書き出しボタンを押して得られるダウンロード結果（ZIPの中身・ファイル名）は変わらない。
- PDFファイルが見つからない場合の404応答、資料が空の場合・ページ指定がない場合の400応答、それぞれのメッセージ文言も変わらない。
- 複数の項目に同時にページ指定がない場合、並び順で最初の1件だけがエラーメッセージに出る、という現在の挙動も変わらない。
- 書き出しが成功した後に記録される履歴（いつ・どの資料を・どの形式で書き出したか）の記録タイミング・記録失敗時の扱いも変わらない。

## 今回内部で移すこと

「ZIPを作る作業」の内部に埋め込まれていた、「これは404/400としてWebに見せる」という判断を、資料の入り口（`web.py`）だけに集める。ZIPを作る作業そのもの（PDFのページを切り出す・目次を作る・ZIPにまとめる、といった一連の処理）は、「PDFが見つからない」「ページ指定がない」という**事実だけ**を報告する形に変え、Webの応答形式を一切知らない状態にする。これにより、ZIPを作る作業を`export_service.py`（Webと無関係な場所）へ移せるようにする。

## レビューで確認済みの判断点

1. **失敗の事実をどう表現するか**（Decisions 1）: 「資料が空」「ページ指定がない」は`ValueError`、「PDFが見つからない」はPR3で新設済みの`PdfSourceNotFoundError`をそのまま使う。利用者に返るステータス・メッセージを維持できる設計として、レビューで妥当と確認済み。
2. **archive生成の戻り値の型**（Decisions 7）: `PreparedArchiveExport`（archive専用の型、フィールド構成はPR4の`PreparedJsonExport`と同じ）として確定。外部契約ではなく内部APIの命名に関する事項であり、この場で確定してよいとレビューで確認済み。
3. **callback非HTTP化の範囲**（Decisions 2）: archive経路のcallbackだけを非HTTP化し、既存wrapper関数の削除・整理は別PRに切り出す方針。レビューで妥当と確認済み、以後は確定した設計判断として扱う。

本designに残る未決定事項はない（Open Questions参照）。

---

## Context

現状（PR4完了時点）、`web.py`の`_export_pack_archive(pack, items, *, format, profile)`（826行付近）が次をすべて1関数内で行っている。

- 空pack検証（`items`が空なら`HTTPException(400, "資料が空です")`）
- pages未指定検証（各itemを順に見て、最初に見つかった`pages`未指定項目で`HTTPException(400, f"{item.title}: ページを指定してください")`）
- 統計収集の分岐（`profile.chunk_limit()`が`None`でなければ`collect_item_stats`、そうでなければプレースホルダー統計）
- `chapter_loader`構築（`profile.needs_chapter_loader`なら`lambda pdf_path: list_chapters(_resolve_pdf_file_or_404(str(pdf_path), books_dir))`）
- `profile.plan(item_stats, chapter_loader=chapter_loader)`呼び出し
- `RenderContext`構築（`resolve_pdf=lambda pdf_path: _resolve_pdf_file_or_404(pdf_path, books_dir)`、`render_pdf=render_pdf_export`、`render_markdown=...`）
- 各chunkについて`profile.render_chunk(chunk, ctx)`で本文を組み立て、`PackExportEntry`に集約
- `build_pack_zip`または`build_pack_zip_with_manifest`でZIP bytes生成
- `profile.archive_filename(...)`でfilename決定
- `Response`生成（`media_type="application/zip"`、`Content-Disposition`）

このうち、`_resolve_pdf_file_or_404`（382行付近）は次の実装になっている。

```python
def _resolve_pdf_file_or_404(pdf_path: str, books_dir: Path) -> Path:
    try:
        return pdf_export_service.resolve_pdf_source(pdf_path, books_dir)
    except PdfSourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail="PDF not found") from exc
```

つまり非HTTPの解決処理（`pdf_export.resolve_pdf_source`、失敗時`PdfSourceNotFoundError`）はPR3で既に存在するが、archive側は`HTTPException`へ変換済みのこのwrapperをそのままcallbackとして`RenderContext`・`chapter_loader`に渡しており、業務ロジックの内部から`HTTPException`が直接送出され得る状態になっている。`render_pdf_export`（389行付近、`ValueError`を`HTTPException(400)`へ変換するwrapper）も同様に`RenderContext.render_pdf`へそのまま渡されている。

一方、[PR3で確立したpreview経路](../archive/2026-08-20-r8-pr3-preview-request-policy/design.md)は、`build_pack_export_preview`（`export_service.py`）が`PdfSourceNotFoundError`を捕捉せずそのまま`web.py`まで伝播させ、`api_preview_pack_export`が捕捉して404へ変換する、という非HTTP例外伝播パターンを既に持っている。本PRはこのパターンをarchive経路にも適用する。

## Goals / Non-Goals

**Goals:**
- archive生成の業務ロジック（検証・統計収集・plan・render・ZIP生成・filename決定）を`export_service.py`の新しい公開関数へ移す。この関数と、そこに渡すcallback（`resolve_pdf`・`chapter_loader`・`render_pdf`）から`HTTPException`を排除する。
- `web.py`の`_export_pack_archive`を、service関数の呼び出しと、非HTTP失敗表現→400/404変換、成功結果→`Response`変換だけに絞る。
- PR2で固定した400/404の文言・検証順序・ZIP logical contentを、移動の前後で一致させる。

**Non-Goals（proposal.mdのNon-goalsに加え、design-level境界）:**
- `web.py`内の`_resolve_pdf_file_or_404`・`render_pdf_export`（wrapper関数自体）の削除・整理は行わない。archive経路のcallbackだけを、これらのwrapperを経由しない非HTTP関数の直接呼び出しに置き換える。

## Decisions

### 1. 失敗の事実の表現方法

serviceは次の非HTTP表現を使い、`HTTPException`を一切送出しない。

| 失敗 | 表現 | 現行のHTTP変換（web.py側、維持） |
|---|---|---|
| 空pack | `ValueError("資料が空です")` | 400、`"資料が空です"` |
| pages未指定（position順最初の1件） | `ValueError(f"{item.title}: ページを指定してください")` | 400、そのメッセージ |
| PDF source不在 | 既存`pdf_export.PdfSourceNotFoundError`（新設しない） | 404、`"PDF not found"` |
| pages parse/range不正 | 既存下位モジュール（`pdf_export_service.render_pdf_export`等）が送出する`ValueError` | 400、`str(exc)` |

`web.py`側は`try/except ValueError`・`except PdfSourceNotFoundError`でこれらを捕捉し、設計書§15の表どおりの400/404へ変換する。service本体・callbackはHTTP statusやdetail文言をfieldに持たない（設計書§14の既存方針をそのまま踏襲）。

代替案として、専用の例外型（`ArchiveEmptyPackError`等）を新設する案も検討したが、設計書§14が「`ValueError`系を第一候補とする」と既に方針を示しており、PR3の`resolve_external_profile`/`resolve_export_format`も同じ`ValueError`パターンを採用済みのため、一貫性を優先し新設しない。

### 2. callbackの非HTTP化は archive経路の呼び出しだけに限定する

`RenderContext`・`chapter_loader`に渡すcallbackを次のように変える。

- `resolve_pdf`: `lambda pdf_path: pdf_export_service.resolve_pdf_source(pdf_path, books_dir)`（`_resolve_pdf_file_or_404`を経由しない）
- `chapter_loader`: 同様に`pdf_export_service.resolve_pdf_source`を直接使う
- `render_pdf`: `pdf_export_service.render_pdf_export`を直接使う（`web.py`内`render_pdf_export`ラッパーを経由しない）

`web.py`内の`_resolve_pdf_file_or_404`・`render_pdf_export`（`HTTPException`変換wrapper）自体は削除しない、とここで確定する。これらは他route（`/export-pdf`、`/export-md`等）からも呼ばれている可能性があり、その利用状況の確認・整理（不要になったwrapperの整理を含む）は、利用箇所を確認した別PRで扱う（本PRのスコープ外）。archiveのcallbackだけを、wrapperを経由しない直接呼び出しへ切り替える。

### 3. 検証順序を維持する

現行の「空pack → pages未指定（position順で最初の1件） → 統計収集 → plan生成 → render」という順序を、service関数内でもそのまま維持する。

### 4. DB接続ライフサイクルを維持する

設計書§16.1が既に整理している接続パターン（standard: pack/items read後にclose、統計接続なし。chat/chapter: pack/items read後にclose、別接続でstats read後にclose）を、service関数内に移してもそのまま保つ。接続の開閉順序自体は変えない。

### 5. event記録の順序を変えない

service関数はevent記録を一切行わない（現状どおり`web.py`側の責務、PR6スコープ）。service関数がZIP bytes・filenameを返した後、`web.py`は現行と同じ位置（`Response`オブジェクト構築後、handlerが`return`する前）でevent記録を試みる。記録失敗時に握りつぶし、ダウンロード応答自体を失敗させない現行の`try/except`はそのまま維持する。

### 6. テスト配置

PR3・PR4のテスト配置方針（route/TestClient経由の契約に限定して`test_web.py`に残す）を踏襲する。

- `tests/test_export_service.py`が担当する（詳細な生成契約）:
  - ZIPのexact logical content（entry名・順・展開bytes、fixed clock注入下のmanifest日時）
  - 空pack・pages未指定時の`ValueError`文言（position順で最初の1件のみ）
  - PDF不在時に`PdfSourceNotFoundError`がservice関数から捕捉されず伝播すること
  - 統計収集の分岐（standard経路が`collect_item_stats`を呼ばずプレースホルダーを使うこと、chat/chapterのみ実統計を使うこと）
- `tests/test_web.py`が担当する（HTTP契約。ZIPの中身そのものは確認しない）:
  - `TestClient`経由の400・404・200 status、`Content-Type`・`Content-Disposition`ヘッダー
  - service関数の戻り値（ZIP bytes・filename）が変形されずそのままHTTP応答として渡ることの確認。PR4の`test_json_export_returns_service_content_via_http_response`と同じパターン（service関数をスタブへ差し替え、HTTP層の受け渡しだけを見る）を踏襲する。
  - event記録順序（`ExportEventRecordingTest`内の該当テスト、変更なし）
  - 既存`ExportArchiveBackwardCompatibilityTest`・`ExportProfileParameterTest`のうち、ZIPのentry名・順・展開bytesそのものを検証しているテストは`tests/test_export_service.py`へ移す。HTTPステータス・ヘッダーのみを見ているテストは`tests/test_web.py`に残す。

### 7. archive生成の戻り値の型を`PreparedArchiveExport`に確定する

レビューで、この型名は外部契約ではなく内部APIの命名に関する事項であり、実装時に確定して問題ないと確認された。PR4の`PreparedJsonExport`とは別の、archive専用の型として次を採用する。

```python
@dataclass(frozen=True)
class PreparedArchiveExport:
    content: bytes
    filename: str
```

フィールド構成（`content: bytes`、`filename: str`）はPR4の`PreparedJsonExport`と同じにし、命名だけをJSON専用／archive専用で分ける。JSON/ZIP共通型（設計書§12.3の`PreparedPackExport`候補）への統合はPR5では行わない（PR4のdesign.mdと同じ判断を踏襲する）。

## Risks / Trade-offs

- [PDF解決・生成の順序や接続ライフサイクルを移動時にうっかり変えてしまう] → 既存テストの期待値をそのまま移動先のテストへ引き継ぎ、移動前後で同一の期待値を使う。
- [callbackの非HTTP化がarchive経路以外（`/export-pdf`等）に意図せず影響する] → decision 2のとおり、archive専用のlambda式だけを変更し、`web.py`内の既存wrapper関数自体・他routeでの利用は変更しない。
- [400/404の文言・検証順序を移動時に崩す] → PR2のcharacterization testの期待値をそのまま移動先へ引き継ぐ。
- [`PdfSourceNotFoundError`が意図せずservice内部で捕捉され、404への変換がweb.py側に届かない] → serviceおよびcallbackは例外を捕捉せず素通しすることをテストで確認する（PR3の`build_pack_export_preview`と同じ確認方法）。

## Migration Plan

本changeはコード移動のみで、production configや外部依存の変更はない。ロールバックは追加コミットのrevertで完結する。

## Open Questions

なし。archive生成の戻り値の型（決定7）、`web.py`内の既存wrapper関数の扱い（決定2）、`tests/test_web.py`に残すテストの範囲（決定6）は、いずれもレビューで確認・確定した。
