## Why

`web.py`の`_export_pack_archive`は現在、資料のZIP書き出しに必要な業務ロジック（空pack検証、pages未指定検証、統計収集、`profile.plan()`呼び出し、PDF/Markdown本文の組み立て、ZIP生成、filename決定）と、HTTP専用の失敗表現（400・404の組み立て）が同じ関数内に同居している。とくに`RenderContext.resolve_pdf`・`chapter_loader`・`render_pdf`へ渡すcallbackは、PDF不在時・pages不正時に`HTTPException`を直接送出する`web.py`内の関数（`_resolve_pdf_file_or_404`、`render_pdf_export`）をそのまま使っており、業務ロジックの奥深くでHTTP応答の形が決まってしまっている。この状態のままでは、業務ロジックをFastAPI非依存の`export_service.py`へ移せない。

これは[R8のPR分割案](../../../docs/refactoring/r8-export-service.md#22-pr分割案)のPR5にあたる。[PR2（現行契約のcharacterization test）](../archive/2026-08-19-r8-pr2-characterization-tests/)で400/404の文言・検証順序・ZIP logical contentが既にテストとして固定されている。[PR3（previewとrequest policyの分離）](../archive/2026-08-20-r8-pr3-preview-request-policy/)で、PDF不在を表す非HTTP契約（`pdf_export.resolve_pdf_source`／`PdfSourceNotFoundError`）が既に新設され、previewのchapter読み込みで使われている。[PR4（JSON export準備の分離）](../archive/2026-08-21-r8-pr4-json-export-service/)で、「serviceが結果（bytes・filename）を返し、webがHTTP変換する」という境界パターンが確立されている。本PRはこれらの安全網とパターンの上に、archive生成の業務ロジックを同じ境界で`export_service.py`へ移す。

## What Changes

- 空pack検証、pages未指定検証（position順で最初の1件のみ報告する現行挙動を含む）、統計収集の分岐、`profile.plan()`呼び出し、`RenderContext`構築、各chunkの本文組み立て、ZIP生成、filename決定を、FastAPI非依存の`export_service.py`の新しい公開関数へ移す。この関数はHTTPの型を一切返さず、失敗時は非HTTPの表現（後述のDecisionsで確定）で失敗の事実だけを伝える。
- `RenderContext.resolve_pdf`・`chapter_loader`・`render_pdf`に渡すcallbackを、`HTTPException`を送出する`web.py`内のwrapper（`_resolve_pdf_file_or_404`、`render_pdf_export`）ではなく、既存の非HTTP関数（`pdf_export_service.resolve_pdf_source`、`pdf_export_service.render_pdf_export`）を直接使う形に変える。これにより、archive生成の業務ロジック全体（service関数本体とcallback）から`HTTPException`が排除される。
- `web.py`の`_export_pack_archive`は、上記のservice関数を呼び、非HTTPの失敗表現を受け取って現在どおりの400・404応答へ変換し、成功時はservice関数が返したbytes・filenameから`Response`（`media_type="application/zip"`、`Content-Disposition`）を組み立てるだけに縮小する。
- 対応するテストを、責務移動に合わせて`tests/test_web.py`から`tests/test_export_service.py`へ一部移す。

## Non-goals

- profile algorithm（`export_profiles.py`のplan・分割ロジック本体）を変更しない。
- stats algorithm（`export_stats.py`の集計ロジック本体）を変更しない。
- ZIP/Markdown/PDF生成規則そのもの（`zip_export.py`・`markdown_export.py`・`pdf_export.py`の生成本体）を変更しない。
- R7-4（PDF/Markdownの非HTTPオーケストレーション）の責務を再実装しない。既存の非HTTP APIをそのまま利用する。
- DB schemaを変更しない。
- export event（`record_export_event`呼び出しの実装そのもの、event schema）を変更しない。PR6のスコープ。
- profile/format policy（PR3で分離済み）を変更しない。
- JSON export（PR4で分離済み）を変更しない。
- 利用者に返るHTTPステータス・メッセージ・ダウンロード可否を変更しない。空資料・ページ指定なし・複数件で最初の不正項目だけを返す現在の挙動を維持する。
- API URL・HTTPメソッド・queryパラメータ名を変更しない。
- ZIPのentry名・順・展開bytes・manifest内容を変更しない。

## Capabilities

このchangeは既存の振る舞いを一切変更せず、`web.py`内の既存ロジックを`export_service.py`へ移す責務移動のみであるため、spec-level capabilityの新規追加・変更はない（`skip_specs: true`）。

### New Capabilities

なし

### Modified Capabilities

なし

## Impact

- 変更ファイル: `src/tsundokensaku/web.py`（`_export_pack_archive`の縮小、`_resolve_pdf_file_or_404`・`render_pdf_export`wrapperの扱い見直し）、`src/tsundokensaku/export_service.py`（archive生成関数の追加）。テストは`tests/test_web.py`（該当分を削減、HTTP contract testと event記録契約は残す）、`tests/test_export_service.py`（該当分を追加）。
- 変更しないファイル: `export_profiles.py`、`export_stats.py`、`zip_export.py`、`markdown_export.py`、`database.py`、`token_estimate.py`。`pdf_export.py`は既存の`resolve_pdf_source`／`render_pdf_export`をそのまま利用し、新規追加は行わない想定（design.mdで確定）。
- 依存: PR2（現行契約のcharacterization test）・PR3（previewとrequest policyの分離）・PR4（JSON export準備の分離）が完了済みであることが前提。PR3で新設された`pdf_export.resolve_pdf_source`／`PdfSourceNotFoundError`を再利用する。
- 後続: PR6（成功履歴とHTTP adapterの仕上げ）が、本PRで確立するarchive生成の構造（serviceが非HTTP失敗表現を返し、webがHTTP変換する境界）を前提にevent記録helperを整理する。
- リスク: PDF解決・PDF生成・ZIP生成の順序や接続ライフサイクルを移動時にうっかり変えてしまうこと、400/404の文言や検証順序（position順で最初の1件）を移動時に崩すこと。詳細はdesign.mdのRisksを参照。
