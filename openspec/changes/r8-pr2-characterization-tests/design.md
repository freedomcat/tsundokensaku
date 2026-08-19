## Context

R8の詳細設計（[docs/refactoring/r8-export-service.md](../../../docs/refactoring/r8-export-service.md)）は完了しており、§19.2に移動前固定すべき契約16項目を列挙している。現状、これらの一部（`ExportArchiveBackwardCompatibilityTest`、`ExportProfileParameterTest`、`BuildExportPreviewPayloadTest`等）は既に`tests/test_web.py`で厚く固定済みだが（§19.1）、§19.2の16項目は未固定である。

対象9関数（`_export_preview_warning`、`build_export_preview_warnings`、`_preview_base_stats`、`build_export_preview_payload`、`build_export_preview_payload_for_profile`、`_export_pack_json`、`_placeholder_item_stats_for_export`、`_export_pack_archive`、`_resolve_export_profile_or_400`）はまだ`web.py`に存在し、本PRではここへテストを追加する形で契約を固定する。所有moduleが移動する（PR3以降）まで、新規テストファイルは作らず`tests/test_web.py`に追記する（設計書§20の方針どおり）。

## Goals / Non-Goals

**Goals:**
- r8-export-service.md §19.2の16項目すべてに対応するテストケースを`tests/test_web.py`（および必要な場合Playwright spec）へ追加する。
- 各テストが「現在の実装が実際に返す値」を期待値として固定する（新しい仕様を作らない）。
- ZIP/clock関連のテストで、実行時刻由来のmetadataではなくlogical contentと注入clockのみを比較する設計にする。
- DB接続順・event記録順など、副作用の順序を検証できるテスト手法（spy/monkeypatchでの呼び出し順記録、または実DBでの行数・タイミング確認）を決める。
- Playwrightでwarning codeとUI blocking判定のcross-layer契約を固定する。

**Non-Goals:**
- production codeの変更、`export_service.py`の作成、関数移動（PR3以降）。
- r8-export-service.md §26の未確定4点の確定（候補型名称、PDF不在の非HTTP表現、共有集計helperの名称、`exported_at`統一方針）。
- 新規テストファイルの作成（所有moduleがまだ存在しないため）。
- JSON/ZIPのkey順・indentなどを恒久仕様として設計書を更新すること（設計書§18.5が既に「compatibility constraintとして固定するが恒久仕様にするかは別変更」としている方針を踏襲するのみ）。

## Decisions

### 1. 既存の厚いテスト（§19.1）と新規追加（§19.2）の関係

既存の`ExportArchiveBackwardCompatibilityTest`等は残し、変更しない。§19.2の16項目は、既存クラスへのテストメソッド追加、または内容が独立している場合は`tests/test_web.py`内の新しいテストクラスとして追加する（ファイルは分けない）。

代替案として全項目を1つの新テストクラスにまとめる案も検討したが、既存クラス構成（機能ごとにクラス分割済み）に合わせて関連クラスへ追記する方が、後続PRでの移動時に対応が追いやすい。

### 2. Fixed clockの注入方法

archive名・manifest日時・JSON body・event記録のテストでは、`_now_jst`および`datetime.now(timezone.utc)`相当の呼び出しを固定するために既存のmonkeypatch手法（設計書内で言及される`_now_jst`呼出し）を使う。ZIPの`zipfile.writestr`が付与するentry timestampはテスト対象にしない（設計書§18.6・§25.1のとおりflaky要因のため）。

比較対象は次の2種類に限定する:
- logical content: entry名、entry順、展開後bytes、manifest本文（日時部分は注入したfixed clockの値と一致することを確認）。
- 呼び出し回数: `_now_jst`が何回呼ばれるか（archive全体で1回か、Markdown entryごとかを含む）。

### 3. DB接続順・event記録順の検証方法

「pack read接続を生成前にclose」「eventは生成/Response成功後の別接続」は、実装を変更せずに検証する必要がある。次のいずれか（実装時に既存テストの慣習に合わせて選択）:
- SQLite接続のopen/close/commitをspyし、呼び出し順序（read接続close → response生成 → event接続open → INSERT → commit → close）をassertする。
- または、`database.py`の関数呼び出し回数・引数をmonkeypatchで記録し、順序をリストで比較する。

いずれの方法でも、既存のproduction codeは変更しない。テスト側のみで検証手段を用意する。

### 4. Playwrightでのcross-layer契約固定

warning code（`empty_pack`/`missing_pdf`/`missing_pages`/`invalid_pages`はblocking、`unindexed_pages`とplan warningはnon-blocking）とUIのexportボタン活性/非活性の対応は、既存Playwright specへ追記する。4種類全てをE2Eで確認するか、代表1〜2種を E2E、残りをPython側のwarning生成テストで分担するかは実装時に判断する（設計書§19.2-15が同様の裁量を許容している）。本設計では「blockingとnon-blockingの境界が両方最低1件ずつE2Eで確認される」ことを最低条件とする。

**実装結果の追記**: blocking側は`empty_pack`（空packでモーダルを開くだけで確実に再現できる）をE2Eで固定した。non-blocking側は、`unindexed_pages`やplan warning（`item_exceeds_limit`等）をテスト環境のサンプルPDF（`cathedral.pdf`等、CIが動的に生成する一時`books_dir`）で確実に再現する条件（未インデックスページの存在、8万トークン超の単一項目など）を安定して用意できず、テストデータの実PDF内容に強く依存し不確実になるため、「warningが0件でexportSubmitButtonが有効」という代替の非blocking代表ケースをE2Eで固定した。残りのwarning code（`missing_pdf`/`missing_pages`/`invalid_pages`/`unindexed_pages`/plan warning群）は全てPython側（`ExportPreviewWarningContractTest`等）でcode・優先順位・メッセージが直接固定されており、BLOCKING_EXPORT_WARNINGSとPython側codeの対応自体は`workspace.html`の定数とPython側codeを突き合わせる形で維持している。

### 5. `_resolve_export_profile_or_400`のunlisted分岐

`EXTERNALLY_AVAILABLE_EXPORT_PROFILES`に含まれないprofile名には2つの入力パターンがある: (a) `resolve_profile`が知っているが外部非公開のprofile名、(b) `resolve_profile`自体が知らない完全に未知のprofile名。r8-export-service.md §15のとおり、この2パターンは区別された動作を持たず、どちらも同一の400・同一メッセージ（`不明なエクスポートプロファイルです: {name}`）になる。本PRではこの「2パターンとも同じ結果になること」を、それぞれ個別の入力でテストして固定する（異なる動作を新設するわけではない）。既存の間接テストに加え、直接的な単体テストケースを追加する。

### 6. PDF解決callbackの境界は「現状のHTTPException送出」を固定する（将来の非HTTP境界そのものは固定しない）

`web.py`の現在の実装を確認すると、`_export_pack_archive`の`RenderContext.resolve_pdf`（`resolve_pdf=lambda pdf_path: _resolve_pdf_file_or_404(pdf_path, books_dir)`）と、archive/previewの`chapter_loader`（`lambda pdf_path: list_chapters(_resolve_pdf_file_or_404(...))`）は、いずれも`_resolve_pdf_file_or_404`を直接呼び出しており、これは`HTTPException(404, "PDF not found")`を直接送出する実装である。

r8-export-service.md §17・§25.2は、「serviceおよびcallbackが`HTTPException`を送出せず、web adapterだけがPDF不在をHTTPへ変換する」という非HTTP境界は、R7-4完了＋`resolve_pdf`相当の非HTTP契約の確定・実装まで解消しないと明記している。つまりこの非HTTP境界はPR5以降で実現するものであり、PR2（本change）の時点ではまだ存在しない。

したがって本PRで固定するのは「service境界からHTTPExceptionが出ないこと」ではなく、逆に**現状はcallback内部（`_resolve_pdf_file_or_404`）から直接`HTTPException`が送出され、それがそのまま`_export_pack_archive`・`chapter_loader`呼び出し元を通じて伝播すること**である。具体的には:

- archiveでPDFが存在しない場合、直接関数呼び出しレベルで`_export_pack_archive`が`HTTPException(404, "PDF not found")`を送出すること（callbackから直接漏れる現状の実装詳細）を記録する。TestClient経由での404レスポンス自体は既存の`ExportArchiveBackwardCompatibilityTest`で固定済みの可能性が高いため、重複させずcallbackの実装詳細に限定する。
- chapter previewで`chapter_loader`が呼ばれる経路が、PDF欠損項目に対して実際にどう振る舞うか（`export_profiles.py`の`plan()`がmissing_pdf項目をchapter_loader呼び出し対象から除外しているか等）を、既存の`export_profiles.py`のplanロジックを読んでから確認する。その上で、現状200 warning契約が維持されている事実を固定する。この関係が未確認のまま「HTTPExceptionが出ない」と断定しない。

将来PR5でこの非HTTP境界自体を実装する際、本PRで固定した「現状はcallbackから直接送出される」というテストは意図的に置き換える（境界実装後は失敗するテストになるべきものであり、それ自体が回帰検知として機能する）。

## Risks / Trade-offs

- [ZIPのentry timestampやbyte順が環境依存で変動し、テストがflakyになる] → logical content（entry名・順・展開bytes）とmanifest内の注入clock値のみを比較し、`zipfile`が付与する生のtimestampやZIP全体のbyte一致は検証しない。
- [DB接続順序の検証がテスト実装依存になり、後続PRでの関数移動時に検証手法ごと書き直しになる] → 検証ロジックはテストヘルパー関数として独立させ、対象関数のシグネチャ変更にのみ追随できる形にする。
- [Playwright E2Eの追加がCI実行時間を増やす] → 既存specファイルへの追記に留め、新規specファイル・新規ブラウザ設定は増やさない。
- [warning文言・順序を「現状の値」として固定した後、実装時に設計書と食い違いが見つかる] → 食い違いを発見した場合はテスト追加を止め、設計書(r8-export-service.md)の記述と実装のどちらが正か確認してから追加する。本PRはあくまで現状の実測値を固定する作業であり、仕様の再定義は行わない。
- [r8-export-service.md §19.2-16の文言をそのまま「HTTPExceptionが出ないこと」を固定するテストとして書いてしまうと、現状の実装（callbackが直接`HTTPException`を送出する）と矛盾し、実装不能または常に失敗するテストになる] → 決定6のとおり、本PRでは逆に「現状はcallbackから直接送出される」という事実を固定する。非HTTP境界そのものの固定はPR5以降に委ねる。

## Migration Plan

本changeはテスト追加のみで、production codeのデプロイ・ロールバックは発生しない。ロールバックは追加したテストコミットのrevertで完結する。

## Open Questions

なし。r8-export-service.md §26の未確定事項は本PRの対象外として明示的にスコープ外とした。
