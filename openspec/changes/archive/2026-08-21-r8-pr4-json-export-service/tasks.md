## 1. 準備

- [x] 1.1 `src/tsundokensaku/web.py`の`_export_pack_json`（798行付近）と`api_export_pack`内`format == "json"`分岐（958行付近）の現状実装を再確認する。
- [x] 1.2 `tests/test_web.py`の`ExportJsonContractTest`（2740行付近）3テストと、`ExportEventRecordingTest`内のJSON export event記録テストの現状内容を確認する。
- [x] 1.3 `tests/test_export_service.py`の既存クラス構成（`ResolveExternalProfileTest`等）を確認し、JSON export準備のテストクラスをどう追加するかを決める。

## 2. `export_service.py`へのJSON export準備関数の追加

- [x] 2.1 `PreparedJsonExport`（`content: bytes`, `filename: str`の`dataclass`。design.md決定1）を定義する。
- [x] 2.2 JSON export準備関数を実装する。引数として`pack`・`items`・解決済みJST時刻（`exported_at`）を受け取り、`items`配列の組み立て・`json.dumps(..., ensure_ascii=False, indent=2).encode("utf-8")`によるbytes化・filename文字列の組み立て（`sanitize_filename_component`呼び出しを含む）を行い、`PreparedJsonExport`を返す。FastAPIの型（`Response`等）を一切importしない。
- [x] 2.3 関数内部で`datetime.now()`相当を呼ばないこと（design.md決定2）を確認する。時刻は呼び出し元から渡された`exported_at`のみを使う。
- [x] 2.4 空pack・PDF不在・pages不正な資料でも検証を行わずそのまま生成する現状の性質（design.md決定4）を、実装時に変えていないことを確認する。

## 3. `web.py`の更新

- [x] 3.1 `api_export_pack`内`format == "json"`分岐を、2.2の関数を呼び出し・`_now_jst()`で解決したJST時刻を渡す形に書き換える。
- [x] 3.2 service関数が返したfilenameを使い、`quote()`によるパーセントエンコードと`Content-Disposition`ヘッダー、`media_type="application/json"`の`Response`生成を`web.py`側の責務として残す（design.md決定3）。
- [x] 3.3 `_export_pack_json`をservice呼び出し＋HTTP変換のみの薄い関数に整理する、または呼び出し元（`api_export_pack`）へ直接インライン化するかを実装時に選び、いずれの場合もJSON export成功後のevent記録（`try/except`ブロック）の位置・順序を変えないことを確認する。

## 4. テスト配置の変更

- [x] 4.1 `ExportJsonContractTest`の`test_json_export_empty_pack_returns_200`・`test_json_export_missing_pdf_and_invalid_pages_returns_200`相当を、2.2の関数を直接呼び出すテストとして`tests/test_export_service.py`へ移す。
- [x] 4.2 JSON構造のexact値テスト（`version`/`name`/`items`各fieldの値、UTF-8日本語、key順、indent 2、LF、末尾改行なし）とfilenameの組み立て結果（sanitize後の文字列＋日付）を、2.2の関数を直接呼び出すテストとして`tests/test_export_service.py`に追加する。
- [x] 4.3 `tests/test_web.py`の`ExportJsonContractTest`は、通常の成功ケース（PDF欠損等の異常がなく項目が1件以上あるpack）1件のみを残す（design.md決定5）。`TestClient`経由でHTTPステータス200・`Content-Type: application/json`・`Content-Disposition`ヘッダー・レスポンスbodyがservice関数の`content`と一致することを確認する。空pack・PDF不在・pages不正のケース（4.1で`test_export_service.py`へ移した分）は`test_web.py`側からは削除する。
- [x] 4.4 `ExportEventRecordingTest`内のJSON export event記録テストは変更せず、そのまま`tests/test_web.py`に残すことを確認する。

## 5. 全体検証

- [x] 5.1 Python全件テストを実行し、既存テストを含め全件成功することを確認する。
- [x] 5.2 `src/tsundokensaku/`配下の差分が、design.mdで決めた範囲（`web.py`の縮小、`export_service.py`への追加）に収まっていることを確認する。
- [x] 5.3 `ROADMAP.md`・`docs/refactoring/r8-export-service.md`の実装状態表記は本PRでは変更しないことを確認する（PR4完了の反映は別のdocs PRで行う）。
