## Why

`web.py`の`api_export_pack`は、profile/format解決・pack取得・出力生成（JSON/archive）の呼び出しに加え、出力成功後の「成功履歴（export event）のベストエフォート記録」処理を直接抱えている。この記録処理は、別接続を開いて`database.record_export_event`を呼び、失敗時は`logging.exception`でログ出力のみ行い例外を握りつぶす、という一連の非HTTPロジックである。[R8のPR分割案](../../../docs/refactoring/r8-export-service.md#22-pr分割案)のPR3〜PR5により、profile/format解決・preview・JSON準備・archive生成は既にFastAPI非依存の`export_service.py`へ移っているが、この成功履歴記録処理だけは`web.py`に残ったままである。

このPRは、[R8詳細設計書](../../../docs/refactoring/r8-export-service.md)の§9「`export_service.py`へ移す責務」に明記された「成功履歴を別接続でベストエフォート記録する非HTTP helper」を実装し、`web.py`をquery受領・HTTP変換・Response構築・Response構築後の記録指示に絞る、R8シリーズ最後のPRである。

## What Changes

- `api_export_pack`内の成功履歴記録ブロック（別接続の生成・`record_export_event`呼び出し・`try/except`によるログのみの失敗握りつぶし・接続close）を、`export_service.py`の新しい非HTTP公開関数へ移す。この関数はDB接続の生成・close・例外握りつぶしを内包し、失敗時に例外を外へ伝播させない。
- `web.py`の`api_export_pack`は、Response構築が成功した後にこの新関数を呼び出す1行の指示だけを残す。
- `database.py`の`record_export_event`・関連SQL・`export_events`テーブルschema・event payload（`items_json`のversion・フィールド構成）は変更しない。
- 対応するテスト（記録失敗の無害性、pack read接続とevent接続の生成・close順序）を、責務移動に合わせて`tests/test_web.py`から`tests/test_export_service.py`へ一部移す。

## Non-goals

- `record_export_event`本体・SQL・`export_events`テーブルschemaを変更しない。
- event payload（`items_json`のversion・フィールド構成）を変更しない。
- `api_export_pack`に残るpack/items読取、profile/format解決、JSON/archiveへの振り分けをserviceへ移すかどうかは、design.mdのDecisionsで判断を示す（詳細はdesign.md参照）。
- `GET /api/export-events`（履歴一覧API）を変更しない。
- D7（永続化層の再設計）を先取りしない。
- 出力生成（JSON/archive本体）のロジック・契約を変更しない（PR3〜PR5で確立済み）。
- 利用者に返るHTTPステータス・メッセージ・ZIP/JSON内容を変更しない。
- 成功履歴の記録タイミング（Response構築後）・記録失敗時にexportのレスポンスへ影響させない挙動・再実行時に去重せず毎回1行増える挙動を維持する。

## Capabilities

このchangeは既存の振る舞いを一切変更せず、`web.py`内の既存ロジックを`export_service.py`へ移す責務移動のみであるため、spec-level capabilityの新規追加・変更はない（`skip_specs: true`）。

### New Capabilities

なし

### Modified Capabilities

なし

## Impact

- 変更ファイル: `src/tsundokensaku/web.py`（`api_export_pack`内の成功履歴記録ブロックの縮小）、`src/tsundokensaku/export_service.py`（成功履歴記録helperの追加）。テストは`tests/test_web.py`（該当分を削減、HTTP契約・record呼び出し指示の確認は残す）、`tests/test_export_service.py`（該当分を追加）。
- 変更しないファイル: `database.py`（`record_export_event`本体・schema）、`export_profiles.py`、`export_stats.py`、`zip_export.py`、`pdf_export.py`、`pdf_text_service.py`、`token_estimate.py`。
- 依存: PR2（現行契約のcharacterization test）・PR3（previewとrequest policyの分離）・PR4（JSON export準備の分離）・PR5（archiveオーケストレーションの分離）が完了済みであることが前提。
- 後続: なし。本PRの完了によりR8「エクスポート業務ロジック」全体が完了する。ROADMAP・詳細設計書へのR8完了反映は、本PR実装後の別のdocs PRで行う。
- リスク: 成功履歴記録をResponse構築より前に呼んでしまう、または記録失敗時にexport本体を失敗させてしまうこと。DB接続の生成・close順序（pack read接続→close→出力生成→別接続でevent記録→close）を移動時に崩すこと。詳細はdesign.mdのRisksを参照。
