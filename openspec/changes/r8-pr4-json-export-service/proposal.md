## Why

`web.py`には現在も、JSON export（`_export_pack_json`）のデータ組み立て・bytes生成・filename決定がHTTP Response生成と同居している。これは[R8のPR分割案](../../../docs/refactoring/r8-export-service.md#22-pr分割案)のPR4にあたる。[PR2（現行契約のcharacterization test）](../archive/2026-08-19-r8-pr2-characterization-tests/)で、JSON exportのexact body bytes・header・空pack時の挙動が既にテストとして固定されている。[PR3（previewとrequest policyの分離）](../archive/2026-08-20-r8-pr3-preview-request-policy/)で、profile/format解決とpreview系の責務がFastAPI非依存の`export_service.py`へ既に移動済みである。この安全網の上で、JSON export準備の責務を同じ`export_service.py`へ移し、`web.py`をHTTP変換（Response生成・media type・Content-Disposition）に絞る。

## What Changes

- `_export_pack_json`が担っている「JSON export準備」（`items`配列の組み立て、`json.dumps`によるbytes化、filenameの決定）を、FastAPI非依存の`export_service.py`の新しい公開関数へ移す。この関数はJSON bytesとfilenameの組を返し、FastAPIの型（`Response`等）を一切返さない。
- `web.py`の`api_export_pack`内、`format == "json"`の分岐は、service関数の戻り値（bytes・filename）を受け取り、`Response`生成・`media_type="application/json"`・`Content-Disposition`ヘッダー組み立てだけを行う形に絞る。
- JSON export成功後にevent記録を行う既存の順序（Response構築対象の処理が成功した後にベストエフォートで記録する）は変更しない。event記録自体の実装（`record_export_event`呼び出し）は移動しない。
- 対応するテストを、責務移動に合わせて`tests/test_web.py`の`ExportJsonContractTest`から`tests/test_export_service.py`へ一部移す。`tests/test_web.py`には、`TestClient`経由のHTTP status・header・実際のdownload responseの契約、およびJSON export成功時のevent記録順序の契約を残す。

## Non-goals

- ZIP/archive export（`_export_pack_archive`）の実行経路を変更しない。PR5のスコープ。
- export event（`record_export_event`呼び出しとその記録責務・記録順序そのもの）を変更しない。PR6のスコープ。
- profile/format policy（PR3で`export_service.py`へ既に分離済み）を変更しない。
- JSON schema（`version`/`name`/`items`のfield構成）、JSON bytes形式（key順・indent・改行の有無・UTF-8エンコード）、filename規則（`{sanitize_filename_component(pack.name)}_{JST YYYYMMDD}.json`）、時刻の意味（`_now_jst()`が何を表すか）を変更しない。
- warning仕様を変更しない（JSON exportはそもそもwarningを含まない経路であり、本PRの対象外）。
- `tests/test_web.py`全体の整理はしない。JSON export関連のテストのみを対象とする。
- [設計書§12.3](../../../docs/refactoring/r8-export-service.md#12-export_servicepyの公開api案)が候補として示すJSON/ZIP統一型（`PreparedPackExport`/`prepare_pack_export`）を今回実装しない。JSON専用の狭い戻り値（bytesとfilenameの組）を採用し、ZIP側と統一した型にするかどうかの判断はPR5以降に委ねる（design.md参照）。

## Capabilities

このchangeは既存の振る舞いを一切変更せず、`web.py`内の既存ロジックを`export_service.py`へ移す責務移動のみであるため、spec-level capabilityの新規追加・変更はない（`skip_specs: true`）。

### New Capabilities

なし

### Modified Capabilities

なし

## Impact

- 変更ファイル: `src/tsundokensaku/web.py`（`_export_pack_json`の削減、`api_export_pack`のJSON分岐をHTTP変換に絞る）、`src/tsundokensaku/export_service.py`（JSON export準備関数の追加）。テストは`tests/test_web.py`（`ExportJsonContractTest`の該当分を削減、ただしHTTP contract testとevent記録契約は残す）、`tests/test_export_service.py`（該当分を追加）。
- 変更しないファイル: `export_profiles.py`、`zip_export.py`、`markdown_export.py`、`database.py`、`export_stats.py`、`pdf_export.py`、`token_estimate.py`。
- 依存: PR2（現行契約のcharacterization test）・PR3（previewとrequest policyの分離）が完了済みであることが前提。本PRはPR2で固定した契約をそのまま維持する。
- 後続: PR5（archiveオーケストレーションの分離）・PR6（成功履歴とHTTP adapterの仕上げ）が、本PRで確立するJSON export準備の構造（service側がbytes/filenameを返し、web側がHTTP変換のみを担う境界）を参考にする可能性がある。
- リスク: JSON生成のexact bytes（key順・indent・改行・エンコード）を移動時にうっかり変えてしまうこと、filename生成に使う時刻取得（`_now_jst`）の注入方法を変えたことでテストのfixed clock注入が崩れること、event記録順序（Response構築成功後に記録する現行順序）を移動時に崩すこと。詳細はdesign.mdのRisksを参照。
