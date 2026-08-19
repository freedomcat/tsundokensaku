## Why

`web.py`の責務分離（R8: エクスポート業務ロジック）は[詳細設計](../../../docs/refactoring/r8-export-service.md)（PR1）まで完了しているが、実装（`export_service.py`の作成と関数移動）はまだ着手していない。設計書 §19.2は、移動前にcharacterization testで固定すべき現行契約（JSON bytes、warning順序とcodeごとのUI blocking判定、PDF解決callbackの失敗境界、HTTPヘッダー、DB/event記録順序など）を列挙しているが、これらは現状まだテストとして固定されていない。既存契約を先にテストで固定しないまま関数移動（PR3以降）へ進むと、意図しない振る舞い変更を検知できないリスクがある。

これは[R8のPR分割案](../../../docs/refactoring/r8-export-service.md#22-pr分割案)のPR2にあたり、R7系列と同じ「移動前に契約を固定してから移動する」進め方を踏襲する。

## What Changes

- `docs/refactoring/r8-export-service.md` §19.2に列挙された現行契約を、既存テストへcharacterization testとして追加する。
  - 全item warningのexact dict、同一項目内の優先順位、複数項目のposition順、item warning→plan warningの連結順
  - `_preview_base_stats`相当の7 field exact値と、`/api/packs/stats`の共有consumerが同じ4集計値を得ること
  - standard/profile previewのfield集合（standardに拡張fieldがないこと、拡張chunk itemの全field）
  - profile解決のunlisted分岐、未知profile、invalid format、conflict、pack不在のvalidation順
  - JSONのfixed clockによるexact body bytes、UTF-8日本語、key順・indent・LF・末尾改行、filename、MIME、Content-Disposition、空pack・不正資料でも200になること
  - ZIPのfixed clockによるarchive名、Content-Disposition、manifest日時（ZIP全bytesではなくentry名・順・展開bytesを比較する）
  - standard経路が`collect_item_stats`を呼ばずplaceholderを使うこと、chat/chapterだけがstats接続を使うこと
  - DB接続順とclose（pack read接続を生成前にclose、eventは生成/Response成功後の別接続であること）
  - profile/format/pack/空/pages/PDF/render/ZIPの各失敗経路でeventを記録しないこと、previewは常にeventを記録しないこと
  - event接続の作成・schema・INSERT・commitの失敗が200 responseを壊さないこと
  - 同一exportの再実行で2行記録されること、chapter fragmentではなく元item snapshotを記録すること
  - `_now_jst`の呼出し回数と、archive日時・Markdown抽出日・UTC event日時が別時計である現在挙動（統一はこのPRの対象外）
  - `TestClient`でactual status、`{"detail": ...}`、Content-Type、Content-Dispositionを確認すること（直接関数呼出しだけに依存しない）
  - `tsundokensaku.web`のR8対象関数へのmonkeypatchが0件であることの再確認
  - Python側のwarning codeと`workspace.html`のblocking判定文字列が一致するcross-layer契約をPlaywrightで固定する（`empty_pack`/`missing_pdf`/`missing_pages`/`invalid_pages`はblockingでexport操作無効、`unindexed_pages`やplan warningはnon-blockingで操作可能）
  - `RenderContext.resolve_pdf`とchapter preview/archiveの`chapter_loader`について、現状は`_resolve_pdf_file_or_404`を直接呼び出し`HTTPException`をcallback内部から直接送出する実装であることを固定する。この非HTTP境界の分離自体はR7-4完了・`resolve_pdf`相当の非HTTP契約確定後のPR5以降のスコープであり、本PRでは「現状はcallbackから直接送出される」という事実と、previewのwarning 200契約が維持されていることを固定するに留める
- 変更対象は既存テストファイルのみ（`tests/test_web.py`が中心。R8所有moduleがまだ存在しないため新規テストファイルは作らない）。
- production code、公開API、出力内容（JSON/ZIP/Markdown/PDFの実体）は変更しない。
- ROADMAP.md・r8-export-service.mdの実装状態表記（「詳細設計済み・実装未着手」）は変更しない。R8全体は本PRでは未完了のまま。
- `export_service.py`の作成や関数移動など、本体の責務分離には着手しない（PR3以降のスコープ）。
- r8-export-service.md §26の未確定4点（`PreparedPackExport`等の候補型名称、PDF不在の非HTTP表現、共有集計helperの名称、archiveの`exported_at`統一方針）は、このPRでは判断・確定しない。現行契約の固定のみを行う。

## Capabilities

このchangeは既存の振る舞いを一切変更せず、既存契約をテストで固定するだけであるため、spec-level capabilityの新規追加・変更はない（`skip_specs: true`）。

### New Capabilities

なし

### Modified Capabilities

なし

## Impact

- 変更ファイル: `tests/test_web.py`（主）。必要に応じて既存のPlaywright specファイル（warning codeとUI blocking判定のcross-layer契約用）。
- 変更しないファイル: `src/tsundokensaku/web.py`、`export_profiles.py`、`export_stats.py`、`zip_export.py`、`database.py`、`pdf_export.py`、`markdown_export.py`等のproduction code一式。
- 依存: PR1（R8詳細設計）が完了済みであることが前提。R7-4（PDF/Markdownの非HTTPオーケストレーション整理）は完了済み。
- 後続PR（PR3〜PR6）が本PRで固定した契約を回帰検知の基盤として利用する。
- リスク: ZIP entryのtimestamp等、実行時刻に依存するmetadataを固定するとflaky testになる。固定するのはlogical content（entry名・順・展開bytes）と、テスト側で注入したclockの値のみとし、`zipfile`が実行時に付与するtimestampそのものは比較しない。
