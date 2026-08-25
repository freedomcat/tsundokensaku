## 0. 前提: レビューで確定させる事項

- [ ] 0.1 design.md「実装前に確定が必要な事項」(1)〜(3)をレビューし、暫定判断（pack/items読取・振り分けはweb.pyに残す／helper関数名は`record_export_event_best_effort`／テストpatch対象は`tests/test_export_service.py`側で`tsundokensaku.database`のシンボル、`tests/test_web.py`側で`tsundokensaku.web.connect`＋`export_service.record_export_event_best_effort`モックに分ける）を確定する。レビューで異なる結論になった場合はdesign.mdを更新してから1.以降に進む。

## 1. 準備

- [ ] 1.1 `src/tsundokensaku/web.py`の`api_export_pack`（828行付近）の現状実装を再確認する。
- [ ] 1.2 `tests/test_web.py`の`ExportEventRecordingTest`の各テストを確認し、service側（`tests/test_export_service.py`）とweb側（`tests/test_web.py`）のどちらに残す・移すかの対応表を作る（design.md決定5）。とくに`tsundokensaku.web.record_export_event`・`tsundokensaku.web.connect`をpatchしている2件（`test_record_failure_does_not_break_export`、`test_read_connection_closes_before_event_connection_opens`）の移設方針を確定する。

## 2. `export_service.py`への成功履歴記録helperの追加

- [ ] 2.1 `record_export_event_best_effort`（design.md決定2のシグネチャ、または0.1で確定した名称・シグネチャ）を定義する。
- [ ] 2.2 外側の`try`ブロック内で`_open_pack_connection(db_path)`（schema保証込み。`database.connect(db_path)`を直接使わない）により別接続を開き、その内側にネストした`try/finally`で`database.record_export_event`を呼び`finally`で`connection.close()`する（`close()`自体の例外も内側`try`の例外として外側`except`まで伝播させ、`finally`節の中で握りつぶさない）。
- [ ] 2.3 外側の`try/except Exception`が接続生成・schema保証・記録・closeの全段階を囲むようにし、いずれの段階の例外も捕捉して`logging.exception(...)`でログ出力のみ行い、例外を外へ伝播させない（design.md決定1）。`export_service.py`に`logging`のimportを追加する。
- [ ] 2.4 関数のdocstringに、ベストエフォートである旨・呼び出し元がResponse構築後に呼ぶ前提であることを明記する。

## 3. `web.py`の更新

- [ ] 3.1 `api_export_pack`内の成功履歴記録ブロック（別接続生成・`record_export_event`呼び出し・`try/except`・接続close）を削除し、`export_service.record_export_event_best_effort(...)`の呼び出し1行に置き換える（design.md決定3）。
- [ ] 3.2 `get_db_path()`の呼び出し元をweb.py側に残し、解決済み`db_path`をhelperへ引数として渡す（design.md決定2）。
- [ ] 3.3 Response構築が成功した後にのみhelperが呼ばれる制御フロー（`HTTPException`発生時は呼ばれない）を変えていないことを確認する。
- [ ] 3.4 `web.py`内で`record_export_event`・`connect`のimportが他に使われていないか確認し、未使用になった場合はimportを整理する（他routeで使われている場合は残す）。

## 4. テスト配置の変更

- [ ] 4.1 1.2の対応表に基づき、`record_export_event_best_effort`の直接呼び出しテスト（正常記録、記録失敗の例外非伝播、`close()`失敗時も例外を外へ伝播させないこと、接続生成・close順序）を`tests/test_export_service.py`へ追加する。
- [ ] 4.2 `tests/test_web.py`の既存2件を、design.md決定5の方針に従い個別に処理する。
  - `test_read_connection_closes_before_event_connection_opens`は`tests/test_web.py`に残し、`patch("tsundokensaku.web.connect", ...)`でpack読取側の接続を観測しつつ`export_service.record_export_event_best_effort`をモックする形（read接続close後・Response構築成功後にhelperが呼ばれること）に書き換える。
  - `test_record_failure_does_not_break_export`は`tests/test_export_service.py`側の`record_export_event_best_effort`単体テストへ移設し、`tsundokensaku.database.record_export_event`をpatchして例外を外へ伝播させないことを確認する形にする。
- [ ] 4.3 `tests/test_web.py`に、PR4の`test_json_export_returns_service_content_via_http_response`と同様の「Response構築成功後に`export_service.record_export_event_best_effort`が呼ばれること」を確認するHTTP契約テストを追加する。
- [ ] 4.4 `ExportEventRecordingTest`のうち、`items_json`内容・schema・再実行時の去重なし・profile未指定時の記録名等、`record_export_event`本体の契約を確認しているテストは、変更せずそのまま`tests/test_web.py`（または該当する統合テスト）に残すことを確認する。

## 5. 全体検証

- [ ] 5.1 Python全件テストを実行し、既存テストを含め全件成功することを確認する。
- [ ] 5.2 Playwright全件テストを実行し、既存テストを含め全件成功することを確認する。
- [ ] 5.3 `src/tsundokensaku/`配下の差分が、design.mdで決めた範囲（`web.py`の縮小、`export_service.py`への追加）に収まっていることを確認する。
- [ ] 5.4 `ROADMAP.md`・`docs/refactoring/r8-export-service.md`の実装状態表記は本PRでは変更しないことを確認する（R8完了の反映は別のdocs PRで行う）。
