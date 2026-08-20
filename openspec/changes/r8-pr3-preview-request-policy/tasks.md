## 1. PR2 characterization testと現行production codeの対応確認

- [x] 1.1 design.md Context章の対応表（16契約項目とテストクラスの対応）を実コードと突き合わせ、行番号・関数シグネチャがdesign.md作成時点から変わっていないかを確認する。変わっていた場合はdesign.mdを先に更新してから着手する。
- [x] 1.2 `export_profiles.ChapterProfile.split_items_with_warnings`が`stats.missing_pdf`な項目に対して`chapter_loader`を呼ばないショートサーキットを再確認し、design.md §6の前提（previewでは実質`chapter_loader`のPDF不在に到達しない）が現状のコードと一致することを確認する。
- 維持する契約: この段階ではproduction code・test codeを変更しない。設計と実装の食い違いを発見した場合は実装を止め、design.mdを更新してから次のtaskへ進む。

## 2. request policyの非HTTP化

- [x] 2.1 `export_service.py`を新設し、`EXTERNALLY_AVAILABLE_EXPORT_PROFILES`を移す。
- [x] 2.2 `resolve_external_profile(name: str | None) -> ExportProfile`を実装する。`export_profiles.resolve_profile`を呼び、unlisted判定を行い、不明・非公開なら`ValueError(profile_name)`を送出する。
- [x] 2.3 `resolve_export_format(profile: ExportProfile, requested_format: str | None) -> str`を実装する。現行`api_export_pack`冒頭のformat既定値解決・pdf/md/json検証・primary_format整合検証を移し、現行と同一の`ValueError`メッセージを送出する。
- [x] 2.4 `web.py`の`_resolve_export_profile_or_400`を削除し、`api_preview_pack_export`・`api_export_pack`の該当部分を、`export_service.resolve_external_profile`呼び出し＋`ValueError`を400へ変換する`try/except`に置き換える。`api_export_pack`にはさらに`resolve_export_format`呼び出し＋400変換を追加する。
- 維持する契約: validation順序（profile→format→pack）、400のdetail文言（`不明なエクスポートプロファイルです: {name}`、`format は pdf, md, または json を指定してください`、`profile={name} では format={primary} のみ指定できます`）、`ExportProfileParameterTest`が固定する既存間接テストが変更なしで通ること。

## 3. pure preview logicのservice移動

- [x] 3.1 `_export_preview_warning`、`build_export_preview_warnings`、`build_export_preview_payload`、`build_export_preview_payload_for_profile`を`export_service.py`へ移す（ロジック・文言は変更しない）。
- [x] 3.2 `web.py`側のimportを`export_service`からの参照に差し替える（後述task 7で`web.py`から完全に呼び出しごと除去するまでの中間状態としてよい）。
- 維持する契約: warning code 4種のexact dict・同一項目内優先順位（`missing_pdf`→`missing_pages`→`invalid_pages`→`unindexed_pages`）、複数項目のposition順、item warning→plan warningの連結順、standard previewのfield集合（8個）、profile previewの拡張field（`profile`/`file_count`/`archive`/`chunks`と拡張chunk item全field）。

## 4. 共有基礎集計を`export_stats.py`へ整理

- [x] 4.1 `export_stats.py`に`PackItemStatsSummary`（`book_count`/`item_count`/`total_pages`/`combined_stats: TextStats`を持つ`@dataclass(frozen=True)`）と`summarize_item_stats(item_stats: list[ItemStats]) -> PackItemStatsSummary`を新設する（現行`_preview_base_stats`が行う`TextStats`合算・`book_count`/`item_count`/`total_pages`集計ロジックを移植。`estimation`/`estimator`/`estimated_chars`/`estimated_tokens`の算出はここに含めない）。
- [x] 4.2 `export_service.py`に`_preview_base_stats`相当の関数を用意し、`summarize_item_stats`が返す`PackItemStatsSummary`から`estimated_chars = combined_stats.cjk_chars + combined_stats.other_chars`、`estimated_tokens = estimate_tokens(combined_stats)`を算出し、`estimation`/`estimator`（`ESTIMATOR_NAME`）を加えた7 field dictを組み立てる。
- [x] 4.3 `web.py`の`api_list_pack_stats`のimportを`export_stats.summarize_item_stats`直接参照へ差し替え、`PackItemStatsSummary`から`book_count`/`item_count`/`total_pages`をそのまま使い、`estimated_tokens = estimate_tokens(summary.combined_stats)`を追加で算出する（`export_service.py`をimportしない）。
- [x] 4.4 `_open_pack_connection`（task 5）と同様、`PackItemStatsSummary`をD2/D3的な汎用集計基盤へ拡張しない。4値・7値それぞれのprojectionに必要な最小のdataclass1個にとどめる。
- 維持する契約: `_preview_base_stats`相当の7 field exact値、`PackStatsApiTest.test_stats_and_export_preview_share_the_same_four_aggregate_values`が固定する`api_list_pack_stats`とpreviewの4値一致。`api_list_pack_stats`から`export_service.py`への依存が発生しないこと。`TextStats`合算計算が`summarize_item_stats`内の1箇所だけで行われ、previewと`/api/packs/stats`のどちらも二重計算しないこと。

## 5. preview DB orchestrationのservice移動

- [x] 5.1 `export_service.py`に、`database.connect`＋`database.ensure_pack_schema`を行う最小限の処理を用意する（`web.py`の`_pack_connection`をimportせず、同等ロジックを独自に持つ）。`_open_pack_connection`のようなprivate helperとして切り出すか、`build_pack_export_preview`内に2行のまま直書きするかは実装時のコードの見た目で判断してよいが、汎用transaction管理・connection pool・repository abstraction・schema migration方針・D2/D3の共通接続管理を先取りする抽象は導入しない（design.md §7）。
- [x] 5.2 `export_service.py`に`build_pack_export_preview(pack_id: int, *, profile_name: str | None, db_path: Path, books_dir: Path) -> dict[str, object] | None`を新設する。内部で`resolve_external_profile`→接続→`get_pack`（`None`ならclose後`None`を返す）→`get_pack_items`→`collect_item_stats`→close→（chat/chapterなら）task 6のchapter_loader構築→`build_export_preview_payload`または`build_export_preview_payload_for_profile`呼び出し、という現行順序をそのまま実装する。
- [x] 5.3 `web.py`の`api_preview_pack_export`を、`build_pack_export_preview`呼び出し＋`None`→404変換＋`PdfSourceNotFoundError`→404変換（task 6）＋`JSONResponse`生成だけに書き換える。
- 維持する契約: DB接続順序（connect→schema保証→pack/items/stats read→close→plan生成）、pack不在時404 `資料が見つかりません`、previewは常にevent記録しないこと（`ExportEventRecordingTest.test_preview_never_records_event`）、standard/chat/chapterいずれのpreviewも既存のexact payloadを返すこと（`PackExportPreviewTest`全件）。新しいDB接続管理の抽象を導入しないこと。

## 6. chapter preview callbackからHTTP依存を除去

- [x] 6.1 `pdf_export.py`に`resolve_pdf_source(pdf_path: str, books_dir: Path) -> Path`を新設する（既存`paths.resolve_pdf_path`を呼び、`None`なら既存`PdfSourceNotFoundError`を送出する薄い関数。新しい例外型は作らない）。`save_pdf_export_to_configured_dir`内にある同等ロジック（`paths.resolve_pdf_path`→`None`→`PdfSourceNotFoundError`）をこの新関数の呼び出しへ置き換え、重複を解消する。既存の`/export-pdf/save`エンドポイントの挙動（保存先チェック・ファイル名決定等）には触れない。
- [x] 6.2 `web.py`の`_resolve_pdf_file_or_404`を、`pdf_export.resolve_pdf_source`を呼び`PdfSourceNotFoundError`を捕捉して`HTTPException(404, "PDF not found")`に変換する薄いwrapperへ書き換える。シグネチャ・例外型（呼び出し元から見た挙動）・メッセージは変更しない。
- [x] 6.3 `export_service.py`のpreview用`chapter_loader`構築を、`pdf_export.resolve_pdf_source`を直接使う形（`lambda pdf_path: list_chapters(pdf_export.resolve_pdf_source(str(pdf_path), books_dir))`）にする。`_resolve_pdf_file_or_404`を経由しない。`export_service.py`・このcallbackは`HTTPException`を一切送出しない。
- [x] 6.4 `web.py`の`api_preview_pack_export`に、`export_service.build_pack_export_preview`（内部で`chapter_loader`経由の`PdfSourceNotFoundError`が伝播しうる）を`except PdfSourceNotFoundError`で捕捉し`HTTPException(404, "PDF not found")`へ変換する処理を追加する（`save_export_pdf`の既存パターンと同じ変換）。これは選択式ではなく、本PRの完了条件として実装する。
- 維持する契約: archiveの`RenderContext.resolve_pdf`・archive用`chapter_loader`（`_export_pack_archive`内）は無変更（`_resolve_pdf_file_or_404`を経由し続ける）。`_resolve_pdf_file_or_404`を直接呼ぶ既存テスト（`ExportPdfResolutionCallbackBoundaryTest.test_resolve_pdf_file_or_404_raises_http_exception_directly`）が無変更で通ること。`ChapterProfile.split_items_with_warnings`が`missing_pdf`項目に対して`chapter_loader`を呼ばないことにより、previewの通常missing PDF warning経路が既存どおりHTTP 200を維持すること（`test_chapter_preview_missing_pdf_item_keeps_200_warning_contract`相当）。`export_service.py`・previewの`chapter_loader`から`HTTPException`が一切送出されないこと。`PdfSourceNotFoundError`という単一の非HTTP例外型のみが使われ、plain `FileNotFoundError`をservice境界の契約にしないこと。`web.py` adapterだけがHTTP変換を担当すること。

## 7. `web.py`をHTTP adapterへ縮小

- [x] 7.1 `api_preview_pack_export`・`api_export_pack`の実装を、design.md §8「`web.py`に残すもの」に列挙した責務だけへ整理する。`export_service.py`へ移した関数（`_export_preview_warning`等）へのimportが`web.py`に残っていないか確認し、不要な互換importを削除する。
- [x] 7.2 `EXTERNALLY_AVAILABLE_EXPORT_PROFILES`が`web.py`から完全に削除され、`export_service.py`のもののみが存在することを確認する。
- 維持する契約: `api_preview_pack_export`・`api_export_pack`のimport pathは`web.py`のまま維持する（route関数自体は移動しない）。`_export_pack_json`・`_placeholder_item_stats_for_export`・`_export_pack_archive`・`record_export_event`呼び出しは`web.py`に残ったまま無変更で動作する。

## 8. service test / web testの責務移動

- [x] 8.1 新設`tests/test_export_service.py`へ、`ExportProfileParameterTest`のうち直接関数呼び出しで完結する部分、`BuildExportPreviewPayloadTest`、`ExportPreviewWarningContractTest`、`BuildExportPreviewPayloadForProfileTest`、`PackExportPreviewTest`（直接関数呼び出しレベルのDB統合テスト）、`ExportPdfResolutionCallbackBoundaryTest`のうちpreviewの`chapter_loader`に関する部分を移す。importを`tsundokensaku.export_service`へ差し替え、テスト内容自体は変更しない（`PdfSourceNotFoundError`ベースへの更新が必要な箇所のみ最小限更新する）。このファイルではHTTPステータス・HTTPヘッダーを一切アサートしない。
- [x] 8.2 `tests/test_export_stats.py`へ`summarize_item_stats`（`PackItemStatsSummary`を返す）の単体テストを追加する。
- [x] 8.3 `tests/test_web.py`に、route/TestClient経由のHTTP契約を次の5点、**最低限残す**（削除しない）：(1) pack不在→404 `資料が見つかりません`、(2) profile/format不正→400（各メッセージ文言）、(3) preview成功時200・JSON response shape（standard/profile双方）、(4) previewで`export_service.build_pack_export_preview`が`PdfSourceNotFoundError`を送出した場合に`api_preview_pack_export`が404 `PDF not found`へ変換すること（`build_pack_export_preview`をmonkeypatchして`PdfSourceNotFoundError`を強制発生させる新規テスト）、(5) 通常のmissing PDF warning経路がTestClient経由でも200を維持すること。これらはwarning生成ロジックの全パターンを再検証せず、adapterとしてのHTTP変換だけを検証する（`tests/test_export_service.py`と同じアサートを重複させない）。`PackStatsApiTest`、`ExportJsonContractTest`、`ExportZipFixedClockContractTest`、`ExportStatsCollectionStrategyTest`、`ExportClockCallCountTest`、`ExportArchiveBackwardCompatibilityTest`、`ExportEventRecordingTest`、`_resolve_pdf_file_or_404`が archive経路で404を返すテストはPR3で移動しない関数のテストのためそのまま残す。
- [x] 8.4 `tsundokensaku.web`のR8対象関数へのmonkeypatchが0件であることを再確認する（設計書§19.2-14と同じ基準。ただしtask 8.3の(4)で新規に追加する`build_pack_export_preview`へのmonkeypatchは`export_service`モジュールに対するものであり対象外）。
- 維持する契約: テスト移動によってテストケース数・アサーション内容が減らないこと（重複削除を除く）。移動前後でPR2のcharacterization testが検証していた契約が、配置先が変わっても同じ内容で検証され続けること。service testとHTTP contract testの責務が重複しないこと。

## 9. Python全件

- [x] 9.1 Python全件テストを実行し、607件（本PR開始前確認時点の基準）に対して増減の理由（新規追加・移動による重複削除）を説明できる状態で全件成功することを確認する。
- 維持する契約: production code・出力内容に対する既存テストの期待値を変更しない（テスト配置・importの変更のみ）。

## 10. Playwright全件

- [x] 10.1 Playwright全件テストを実行し、31件（本PR開始前確認時点の基準）が全件成功することを確認する。
- 維持する契約: warning codeとUIのblocking/non-blocking判定の対応（`empty_pack`/`missing_pdf`/`missing_pages`/`invalid_pages`はblocking、`unindexed_pages`とplan warningはnon-blocking）が変わらないこと。

## 11. import / dependency / diff確認

- [x] 11.1 `export_service.py`が`web.py`・`fastapi`をimportしていないことを確認する（`grep -n "import"`等での目視確認）。
- [x] 11.2 `export_stats.py`・`export_profiles.py`・`pdf_export.py`・`paths.py`・`database.py`のいずれも`export_service.py`・`web.py`をimportしていないこと（循環依存がないこと）を確認する。`export_service.py -> pdf_export.py`という新規依存が、既存の`export_stats.py -> pdf_export.py`・`export_profiles.py -> pdf_export.py`と同じ向きであることも確認する。
- [x] 11.3 `git diff --stat -- src/tsundokensaku/`で変更ファイル一覧を確認し、design.md Impactに列挙したファイル（`web.py`、`export_service.py`、`export_stats.py`、`pdf_export.py`）以外に意図しない変更が無いことを確認する。`paths.py`に差分がないことも確認する。
- [ ] 11.4 ROADMAP.mdおよびdocs/refactoring/r8-export-service.mdの実装状態表記（R8は「PR3 previewとrequest policyの分離」が未完了→完了に更新可）を、実装完了後にのみ更新する。今回のセッションではユーザー指示によりROADMAP更新自体を行わないため、実装（tasks 1〜11.3）は完了したが本項目は次回セッションで対応する。
- 維持する契約: `openapi.json`のroute一覧・schemaが変わらないこと（`PackStatsRoutingTest.test_openapi_schema_includes_new_stats_endpoint`等、既存のroute定義に影響がないこと）。
