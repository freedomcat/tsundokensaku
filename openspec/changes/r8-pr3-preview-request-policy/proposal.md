## Why

`web.py`には現在も、エクスポートプレビュー（`GET /api/packs/{pack_id}/export/preview`）とエクスポート実行（`GET /api/packs/{pack_id}/export`）に関わる次の業務ロジックがHTTP層と同居している。

- profile / formatの解決・整合性検証（request policy）
- プレビュー用warningの組み立て
- standard/profile previewのpayload組み立て（projection）
- previewのDB接続・pack/items/統計read・close・plan生成という一連の進行（DB orchestration）
- previewのchapter previewで使うPDF解決callback（`chapter_loader`）が、現状`_resolve_pdf_file_or_404`経由で`HTTPException`を直接送出しうる実装になっている

これは[R8のPR分割案](../../../docs/refactoring/r8-export-service.md#22-pr分割案)のPR3にあたる。[PR2（現行契約のcharacterization test）](../archive/2026-08-19-r8-pr2-characterization-tests/)で、warning順序・payload field集合・DB接続順・PDF解決callbackの現状実装詳細が既にテストとして固定されている。この安全網の上で、FastAPI非依存の`export_service.py`を新設し、HTTP層と業務ロジックの責務境界に沿って上記の処理を移す。

R8全体（PR1〜PR6）のうち、JSON export・archive（ZIP）実行のオーケストレーション、成功履歴の記録責務はPR4〜PR6の対象であり、本PRでは移動しない。

## What Changes

- `EXTERNALLY_AVAILABLE_EXPORT_PROFILES`とprofile/format解決ロジック（現行`_resolve_export_profile_or_400`、および`api_export_pack`冒頭のformat既定値解決・validation）を、FastAPI非依存の`export_service.py`へ移す。HTTPステータスへの変換のみ`web.py`に残す。
- プレビュー用warning生成（`_export_preview_warning`、`build_export_preview_warnings`）を`export_service.py`へ移す。
- standard/profile previewのpayload組み立て（`build_export_preview_payload`、`build_export_preview_payload_for_profile`）を`export_service.py`へ移す。
- previewのDB read・close・plan生成というユースケース進行（現行`api_preview_pack_export`のうち、pack/items取得・`collect_item_stats`・close・chapter_loader構築・payload組み立て呼び出しの部分）を、`export_service.py`の新しい公開関数へ移す。
- `_preview_base_stats`が使う共有基礎集計（`TextStats`合算を含む中間値。`book_count`/`item_count`/`total_pages`とprojection元の統計値）を、`GET /api/packs/stats`と共有する純粋集計として`export_stats.py`へ切り出す。`export_service.py`はこれを使って7 field（`estimation`, `estimator`, `estimated_chars`, `estimated_tokens`を追加）のpreview基礎統計を組み立てる。`GET /api/packs/stats`のroute（`api_list_pack_stats`）は`export_stats.py`を直接参照し、`export_service.py`には依存させない。
- previewのchapter previewで使うPDF解決を非HTTP化する。**PDF source不在を表す非HTTP失敗契約として、既存の`pdf_export.PdfSourceNotFoundError`（`FileNotFoundError`のサブクラス）を再利用する**。`pdf_export.py`に新設する`resolve_pdf_source`はこの例外を送出し、previewの`chapter_loader`はこれを直接使う（`HTTPException`を経由しない）。previewの通常のmissing PDF warning経路（`ChapterProfile`が`missing_pdf`項目に対して`chapter_loader`を呼ばないショートサーキット）は既存どおりHTTP 200 + warningを維持する。処理途中のレースでPDF sourceが消失し`PdfSourceNotFoundError`が`export_service.py`から`web.py`まで伝播した場合のみ、`web.py`のadapterがこれを捕捉して既存どおり404 `PDF not found`へ変換する（`save_export_pdf`が既に同じパターンを実装済み）。`export_service.py`・previewの`chapter_loader`は`HTTPException`を一切送出しない。archiveの`RenderContext.resolve_pdf`・archive用`chapter_loader`（PR3では移動しないPR5スコープ）は、既存の`_resolve_pdf_file_or_404`をそのまま使い続け、契約・実装とも変更しない。ただし`_resolve_pdf_file_or_404`自体の内部実装は、新設`resolve_pdf_source`を呼んで`PdfSourceNotFoundError`を`HTTPException`へ変換する薄いwrapperへ整理する（外部から見た型・例外・メッセージは不変）。
- `web.py`の2ルート（`api_preview_pack_export`、`api_export_pack`）を、HTTP入力受領・`export_service.py`呼び出し・非HTTP結果／失敗のHTTPステータス変換・`JSONResponse`/`Response`生成に絞る。
- 対応するテストを、責務移動に合わせて`tests/test_web.py`から新設`tests/test_export_service.py`・`tests/test_export_stats.py`へ移す。

## Non-goals

- JSON export（`_export_pack_json`）の実行経路を変更しない。PR4のスコープ。
- ZIP/archive export（`_export_pack_archive`）の実行経路を変更しない。PR5のスコープ。
- export event（`record_export_event`呼び出しとその記録責務）を変更しない。PR6のスコープ。
- R7-4（PDF/Markdownの非HTTPオーケストレーション）の責務を再実装しない。既存の非HTTP APIをそのまま利用する。
- API URL・HTTPメソッド・queryパラメータ名を変更しない。
- request/response schema（preview JSONのfield集合・key名・JSON exportのkey順・ZIPのentry名や順など）を変更しない。
- warning code・warning message・warning順を変更しない。
- preview payload（standard/profile とも）の内容を変更しない。
- UI挙動（blocking/non-blocking warningによるexportボタンの活性・非活性を含む）を変更しない。
- DB schemaを変更しない。
- 新機能を追加しない。
- PR4以降（JSON export準備の分離・archiveオーケストレーションの分離・成功履歴とHTTP adapterの仕上げ）の責務を先取りしない。archiveの`RenderContext.resolve_pdf`／`chapter_loader`をPR3で非HTTP化しない。

## Capabilities

このchangeは既存の振る舞いを一切変更せず、`web.py`内の既存ロジックを`export_service.py`・`export_stats.py`・`pdf_export.py`へ移す責務移動のみであるため、spec-level capabilityの新規追加・変更はない（`skip_specs: true`）。

### New Capabilities

なし

### Modified Capabilities

なし

## Impact

- 変更ファイル: `src/tsundokensaku/web.py`（削減）、新規`src/tsundokensaku/export_service.py`、`src/tsundokensaku/export_stats.py`（共有集計関数の追加）、`src/tsundokensaku/pdf_export.py`（`resolve_pdf_source`の追加、`save_pdf_export_to_configured_dir`内の重複ロジック解消）。テストは`tests/test_web.py`（該当分を削減、ただしHTTP contract testは残す）、新規`tests/test_export_service.py`、`tests/test_export_stats.py`（該当分を追加）。
- 変更しないファイル: `export_profiles.py`（plan/warningアルゴリズム本体）、`zip_export.py`、`markdown_export.py`、`database.py`（schema・event記録本体）、`token_estimate.py`、`paths.py`（`resolve_pdf_path`は無変更）。
- 依存: [PR2（現行契約のcharacterization test）](../archive/2026-08-19-r8-pr2-characterization-tests/)が完了済みであることが前提。本PRはPR2で固定した契約をそのまま維持する。
- 後続: PR4（JSON export準備の分離）・PR5（archiveオーケストレーションの分離）・PR6（成功履歴とHTTP adapterの仕上げ）が本PRで確立した`export_service.py`の構造を踏襲する。
- リスク: `_preview_base_stats`の分割で`GET /api/packs/stats`から`export_service.py`への依存が発生すること、`pdf_export.py`の`save_pdf_export_to_configured_dir`内の重複ロジック解消で既存`/export-pdf/save`エンドポイントの挙動を誤って変えること、`export_service.py`が`web.py`をimportする循環依存を作ること。詳細はdesign.mdのRisksを参照。
