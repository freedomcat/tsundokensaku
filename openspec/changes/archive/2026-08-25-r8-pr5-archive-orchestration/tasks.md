## 1. 準備

- [x] 1.1 `src/tsundokensaku/web.py`の`_export_pack_archive`（826行付近）、`_resolve_pdf_file_or_404`（382行付近）、`render_pdf_export`（389行付近）、`api_export_pack`（924行付近）の現状実装を再確認する。
- [x] 1.2 `tests/test_web.py`の`ExportArchiveBackwardCompatibilityTest`・`ExportProfileParameterTest`・`ExportZipFixedClockContractTest`・`ExportStatsCollectionStrategyTest`・`ExportClockCallCountTest`・`ExportPdfResolutionCallbackBoundaryTest`の現状内容を確認し、各テストがservice/webどちらに移るかの対応表を作る（design.md決定6。ZIPの中身そのものを検証しているテストはservice側、HTTPステータス・ヘッダーのみのテストはweb側）。

## 2. `export_service.py`へのarchive生成関数の追加

- [x] 2.1 `PreparedArchiveExport`（`content: bytes`, `filename: str`の`dataclass`。design.md決定7）を定義する。
- [x] 2.2 空pack検証を`ValueError("資料が空です")`、pages未指定検証（position順で最初の1件のみ）を`ValueError(f"{item.title}: ページを指定してください")`で表現する（design.md決定1）。
- [x] 2.3 統計収集の分岐（`profile.chunk_limit()`が`None`でなければ`collect_item_stats`、そうでなければプレースホルダー統計）をそのまま移す。
- [x] 2.4 `chapter_loader`・`RenderContext.resolve_pdf`を、`pdf_export_service.resolve_pdf_source`を直接使う形に変える（`_resolve_pdf_file_or_404`を経由しない。design.md決定2）。
- [x] 2.5 `RenderContext.render_pdf`を、`pdf_export_service.render_pdf_export`を直接使う形に変える（`web.py`内`render_pdf_export`ラッパーを経由しない。design.md決定2）。
- [x] 2.6 `profile.plan()`呼び出し・`RenderContext`構築・各chunkの本文組み立て・ZIP生成（`build_pack_zip`/`build_pack_zip_with_manifest`）・filename決定（`profile.archive_filename`）を移す。
- [x] 2.7 service関数・callbackが`PdfSourceNotFoundError`・`ValueError`を捕捉せずそのまま呼び出し元へ伝播させることを確認する（`HTTPException`を一切送出しない。design.md決定1・Risks参照）。
- [x] 2.8 DB接続の開閉順序（design.md決定4、設計書§16.1のパターン）を変えていないことを確認する。

## 3. `web.py`の更新

- [x] 3.1 `_export_pack_archive`を、2.系のservice関数を呼び出す形に書き換える。
- [x] 3.2 service関数が送出する`ValueError`を捕捉して400（`str(exc)`をdetailとする）、`PdfSourceNotFoundError`を捕捉して404（`"PDF not found"`）へ変換する（design.md決定1）。
- [x] 3.3 service関数が返したZIP bytes・filenameから、`Response`（`media_type="application/zip"`、`Content-Disposition`）を組み立てる。
- [x] 3.4 event記録（`try/except`ブロック）の位置・順序を変えないことを確認する（design.md決定5）。
- [x] 3.5 `_resolve_pdf_file_or_404`・`render_pdf_export`（wrapper関数自体）は削除・整理せずそのまま残すことを確認する（design.md決定2で確定済み。他routeでの利用状況の確認・整理は本PRのスコープ外とし、別PRで扱う）。

## 4. テスト配置の変更

- [x] 4.1 1.2の対応表に基づき、ZIPのexact logical content（entry名・順・展開bytes、fixed clock注入下のmanifest日時）、空pack・pages未指定時の`ValueError`文言、PDF不在時の`PdfSourceNotFoundError`伝播、統計収集の分岐を、service関数を直接呼び出すテストとして`tests/test_export_service.py`へ移す・追加する。
- [x] 4.2 `tests/test_web.py`には、`TestClient`経由の400・404・200 status、`Content-Type`・`Content-Disposition`ヘッダー契約と、service関数をスタブへ差し替えてHTTP層の受け渡しだけを見るテスト（PR4の`test_json_export_returns_service_content_via_http_response`と同じパターン）を残す。ZIPの中身そのものを確認するテストは残さない（design.md決定6）。
- [x] 4.3 `ExportEventRecordingTest`内のarchive export event記録テストは変更せず、そのまま`tests/test_web.py`に残すことを確認する。

## 5. 全体検証

- [x] 5.1 Python全件テストを実行し、既存テストを含め全件成功することを確認する。
- [x] 5.2 Playwright全件テストを実行し、既存テストを含め全件成功することを確認する。
- [x] 5.3 `src/tsundokensaku/`配下の差分が、design.mdで決めた範囲（`web.py`の縮小、`export_service.py`への追加）に収まっていることを確認する。
- [x] 5.4 `ROADMAP.md`・`docs/refactoring/r8-export-service.md`の実装状態表記は本PRでは変更しないことを確認する（PR5完了の反映は別のdocs PRで行う）。
