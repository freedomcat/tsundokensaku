## 1. 準備

- [x] 1.1 `tests/test_web.py`の既存クラス構成（§19.1: `ExportArchiveBackwardCompatibilityTest`、`ExportProfileParameterTest`、`BuildExportPreviewPayloadTest`、`BuildExportPreviewPayloadForProfileTest`、`PackExportPreviewTest`、`ExportEventRecordingTest`）を確認し、§19.2の各項目をどのクラスへ追記するか、または新規クラスにするかを対応表として洗い出す
- [x] 1.2 DB接続順・event記録順を検証するためのテストヘルパー（spy/monkeypatchによる呼び出し順記録）を用意する（design.md 決定3）。sqlite3.Connectionの`close`はインスタンス属性として上書き不可のため、`_ConnectionCloseSpy`委譲プロキシで代替した
- [x] 1.3 Fixed clock注入の既存手法（`_now_jst`のmonkeypatch）を確認し、archive/JSON/event用に必要なテストfixtureを用意する（design.md 決定2）。既存の`search_view._now_jst`パターンを踏襲し`tsundokensaku.web._now_jst`へ同様のmonkeypatchを新設した

## 2. warning契約の固定

- [x] 2.1 item warning（`missing_pdf`/`missing_pages`/`invalid_pages`/`unindexed_pages`）のexact dict（code, item_id, message）を固定するテストを追加する
- [x] 2.2 同一項目内の優先順位（`missing_pdf`→`missing_pages`→`invalid_pages`→`unindexed_pages`）を、複数warning条件を同時に満たす項目で固定するテストを追加する
- [x] 2.3 複数項目がある場合、warningがposition順に出ることを固定するテストを追加する
- [x] 2.4 item warning→plan warningの連結順（item warningが先、plan warningが後）を固定するテストを追加する

## 3. 統計・payload契約の固定

- [x] 3.1 `_preview_base_stats`相当の7 fieldのexact値を固定するテストを追加する
- [x] 3.2 `/api/packs/stats`の共有consumerが、previewと同じ4集計値（book_count, item_count, total_pages, estimated_tokens）を得ることを固定するテストを追加する
- [x] 3.3 standard previewのfield集合を固定し、`profile`/`file_count`/`archive`/`chunks`が含まれないことを確認するテストを追加する（既存`PackExportPreviewTest.test_preview_profile_unspecified_and_standard_are_byte_identical`で既にカバー済みのため新規追加なし）
- [x] 3.4 profile previewの拡張chunk item全field（`item_id`, `title`, `pdf_path`, `pages`, `label`, `fragment_index`, `fragment_count`, `estimated_tokens`）を固定するテストを追加する（既存`BuildExportPreviewPayloadForProfileTest.test_single_chunk_lists_items_with_per_item_token_estimates`で既にカバー済みのため新規追加なし）

## 4. profile/format解決の固定

- [x] 4.1 unlisted profile（存在するが外部非公開）と完全に未知のprofile名の両方について、区別された動作を新設するのではなく「どちらも同一の400・同一メッセージになる」現状を、それぞれ個別の入力で固定するテストを追加する（design.md 決定5）。現状`PROFILES`と`EXTERNALLY_AVAILABLE_EXPORT_PROFILES`の登録名が完全一致しており(b)パターンが自然発生しないため、`EXTERNALLY_AVAILABLE_EXPORT_PROFILES`を一時的に絞って直接検証した
- [x] 4.2 invalid format、profile/format不一致（conflict）、pack不在のvalidation順序（profile→format→整合→pack）を固定するテストを追加する（既存`test_unknown_profile_takes_priority_over_invalid_format`・`test_missing_pack_returns_404_after_profile_and_format_pass`・`test_profile_chapter_rejects_conflicting_format`で既にカバー済みのため新規追加なし）

## 5. JSON export契約の固定

- [x] 5.1 fixed clock注入のうえ、JSON exportのexact body bytes（UTF-8日本語含む、key順、indent 2、LF、末尾改行なし）を固定するテストを追加する
- [x] 5.2 JSON exportのfilename、`application/json` MIME、`Content-Disposition: attachment; filename*=UTF-8''...`ヘッダーを`TestClient`で固定するテストを追加する
- [x] 5.3 空pack、PDF不在、pages不正な資料でもJSON exportが200になることを固定するテストを追加する

## 6. ZIP export契約の固定

- [x] 6.1 fixed clock注入のうえ、standard/chat/chapterのarchive名、Content-Dispositionヘッダーを固定するテストを追加する
- [x] 6.2 manifest日時が注入したfixed clockの値と一致することを固定するテストを追加する（ZIP全bytes比較はしない。design.md 決定2）
- [x] 6.3 entry名・順・展開bytes（logical content）を固定するテストを追加する（既存`ExportArchiveBackwardCompatibilityTest`・`ExportProfileParameterTest`の各テストで既にカバー済みのため新規追加なし）
- [x] 6.4 standard経路が`collect_item_stats`を呼ばず`_placeholder_item_stats_for_export`を使うこと、chat/chapterだけがstats接続を使うことを固定するテストを追加する

## 7. DB接続・event記録契約の固定

- [x] 7.1 pack read接続が生成前にcloseされ、eventがResponse/生成成功後の別接続で記録されることを、1.2で用意したヘルパーで固定するテストを追加する（sqlite3.Connectionの`close`はインスタンス属性として上書き不可のため、委譲プロキシで代替した）
- [x] 7.2 profile/format/pack/空/pages/PDF/render/ZIPの各失敗経路でeventが記録されないことを固定するテストを追加する（空pack失敗は既存テストでカバー済みのため、pages未指定・PDF不在・不正pages範囲を新規追加。render/ZIP失敗は現状のarchive処理では検証済みの事前バリデーション(空/pages)を通過した時点でrender/ZIPは成功することが既存テストで示されており、意図的に失敗させる経路が現状存在しないため対象外とした）
- [x] 7.3 previewが常にeventを記録しないことを固定するテストを追加する
- [x] 7.4 event接続の作成・schema・INSERT・commitのいずれかが失敗しても200 responseが壊れないことを固定するテストを追加する（既存`test_record_failure_does_not_break_export`は`database.record_export_event`全体をmonkeypatchしている。web.py側は`record_export_event`呼び出し全体を単一の`except Exception`で捕捉する実装のため、接続作成・schema・INSERT・commitのどの段階の失敗でも同じ経路で握りつぶされる。個別に区別してテストする意味がないため新規追加なし）
- [x] 7.5 同一exportを再実行すると2行記録され、chapter fragmentではなく元item snapshot（`pdf_path`/`title`/`pages`/`position`の4 field）が記録されることを固定するテストを追加する（再実行2行は既存カバー済み、chapter fragmentでなく元item snapshotの部分を新規追加）

## 8. 時計契約の固定

- [x] 8.1 `_now_jst`の呼出し回数（archive全体で何回か、Markdown entryごとに呼ばれるか）を固定するテストを追加する
- [x] 8.2 archive日時（JST）・Markdown抽出日（JST）・event記録日時（UTC）が別々の時計呼び出しであることを固定するテストを追加する（統一はしない）

## 9. PDF解決callbackの失敗境界の固定（現状はHTTPException直接送出。非HTTP境界化はPR5以降）

- [x] 9.1 `_export_pack_archive`の`RenderContext.resolve_pdf`が、現状`_resolve_pdf_file_or_404`を直接呼び出しており、PDF不在時に`HTTPException(404, "PDF not found")`を直接送出する実装であることを、直接関数呼び出しレベルのテストで固定する（design.md 決定6）。既存の`ExportArchiveBackwardCompatibilityTest`はTestClient経由ではなく直接`api_export_pack`呼び出しでの404レスポンス（結果としての契約）を固定済みのため、`_resolve_pdf_file_or_404`自体を直接呼び出す形でcallbackの実装詳細を追加固定した
- [x] 9.2 `export_profiles.py`の`plan()`が、PDF欠損項目に対して`chapter_loader`を実際に呼び出すか（呼び出さないなら現状warning経路が優先される）を先に確認したうえで、chapter preview/archiveの`chapter_loader`がPDF不在時にどう振る舞うか（現状の実装通り）を固定するテストを追加する。previewでは既存のwarning契約（200）が維持されることを確認する（`ChapterProfile.split_items_with_warnings`が`stats.missing_pdf`な項目に対して`chapter_loader`を呼ばないことをspyで確認し、200 warning契約の維持も固定した）
- [x] 9.3 本タスクで固定したテストは、PR5で非HTTP境界（callbackから`HTTPException`を排除する変更）が入った時点で意図的に置き換える前提であることをテストコード中にコメントで残す

## 10. monkeypatch再確認

- [x] 10.1 `tsundokensaku.web`のR8対象9関数へのmonkeypatchが既存テスト全体で0件であることを確認する（テスト追加ではなく確認作業。既存で崩れていれば記録する）。本changeで追加したテストを含め`tests/test_web.py`全体で0件を再確認した

## 11. cross-layer契約（Playwright）

- [x] 11.1 warning code（`empty_pack`/`missing_pdf`/`missing_pages`/`invalid_pages`）がUIでexportボタンを無効化するblocking判定になることを、既存Playwright specへの追記で固定する（design.md 決定4: 最低blocking 1件・non-blocking 1件をE2Eで確認）。`empty_pack`（空packでモーダルを開くだけで確実に再現可能）をE2Eで固定した
- [x] 11.2 `unindexed_pages`とplan warningがnon-blockingでexport操作可能であることを、既存Playwright specへの追記で固定する。実PDFの内容に依存して自然発生させるのではなく、PlaywrightのAPI interceptionでpreviewレスポンスに`unindexed_pages`と`item_exceeds_limit`を含め、warningが実際に表示されていてもexportボタンが有効であることを直接固定した。モーダル既定選択はchatプロファイルのため、モックレスポンスは`profile`/`file_count`/`archive`/`chunks`を含む拡張構造とし、`build_export_preview_payload_for_profile(item_stats, ChatProfile(), pack_name="資料")`を実際に呼び出して得た値（warning文言含む）をそのまま転記してproduction実装との一致を担保した
- [x] 11.3 残りのwarning code（11.1/11.2で扱わなかった`missing_pdf`/`missing_pages`/`invalid_pages`）は、Python側のwarning生成テストで代替検証されていることを確認し、design.mdの分担方針どおりであることを確認する（`ExportPreviewWarningContractTest`で全codeのexact dict・優先順位を直接固定済み）

## 12. 全体検証

- [x] 12.1 Python全件テストを実行し、既存テストを含め全件成功することを確認する（607件中602件成功。残り5件は`test_cli.py`/`test_indexer.py`の環境固有failureで、本changeとは無関係な事前存在の問題であることを`git stash`によるベースライン比較で確認した）
- [x] 12.2 Playwright全件テストを実行し、既存テストを含め全件成功することを確認する（31件全件成功）
- [x] 12.3 production code（`src/tsundokensaku/`配下）に差分がないことを確認する（`git diff --stat -- src/`で差分0を確認）
- [x] 12.4 ROADMAP.mdおよびdocs/refactoring/r8-export-service.mdの実装状態表記に変更がないことを確認する（本PRでは「詳細設計済み・実装未着手」のまま）（差分0を確認済み）
