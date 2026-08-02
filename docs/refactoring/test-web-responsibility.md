# Web責務分離に伴うテスト移行設計

[R7全体設計](../central-file-refactoring-inventory.md) / [テスト戦略](../test-strategy.md) / [ROADMAP](../../ROADMAP.md)

状態: 設計済み・未実施。本文書はテストの現状分類と移行方針を確定するものであり、テストコードとプロダクションコードは変更していない。

### 位置づけ

本文書は、ROADMAP「Phase 5着手前: 構造改善と回帰保証」の「`web.py`の責務分離」に付随する成果物である。

- 上位方針は`docs/test-strategy.md`とする。テスト層の定義、各層の保証範囲、新規テストの配置基準、回帰テストの基準は同文書を正本とし、本文書はそれを`web.py`責務分離という具体的な状況へ適用する。
- 責務境界そのもの（R番号の定義、分離先モジュール、依存方向）は`docs/central-file-refactoring-inventory.md`と各R個別設計書を正本とする。本文書はそれらを前提に、既存テストの所有先だけを扱う。
- 本文書の目的は`tests/test_web.py`の行数削減ではない。プロダクションコードの責務境界とテストの所有境界を一致させ、変更影響範囲をテスト側でも分離することが目的である。
- 本PRではテストコードを移動しない。実際の移動は後続のPRで行う。

### 背景と問題

#### 調査時点の事実

以下は2026-08-02、`develop`のコミット`7cfb9f3e465962fcd9b4580cc9840a0e7c53c957`時点で確認した値である。行数・件数は現状把握のための観測値であり、削減目標ではない。

| 観測項目 | 値 | 確認方法 |
|---|---|---|
| `tests/test_web.py`の行数 | 4518行 | `wc -l tests/test_web.py` |
| `tests/test_web.py`のテストクラス数 | 19（他にhelperクラス`_FakeUploadRequest`が1） | `grep -c '^class .*Test' tests/test_web.py` |
| `tests/test_web.py`のテストメソッド数 | 234件 | `grep -c '^    def test_' tests/test_web.py` / `unittest tests.test_web` |
| Pythonテスト全件 | 525件 | `python -m unittest discover -s tests` |
| Playwrightテスト | 29件 | `npm run test:ui` |

既存の責務別テストファイルは次のとおりである。

| ファイル | 件数 | 内容 |
|---|---|---|
| `tests/test_search_view.py` | 8件 | `_now_jst`契約、12関数のimport可否、`web`からの互換import、依存方向の検証のみ。表示整形ロジック自体の単体テストは含まない |
| `tests/test_index_job.py` | 7件 | 進捗辞書、スナップショット、`start`、`_run_index_job`のmodule単体テスト |
| `tests/test_pdf_import_service.py` | 33件 | R7-1の`save_uploaded_pdf`とR7-2の`import_pdfs_from_directory`のservice単体テスト |
| `tests/test_export_stats.py` | 11件 | `collect_item_stats`と`paths`共有の検証 |

`tests/test_config.py`と`tests/test_paths.py`は存在しない。R7-3・R7-4・R8が新設を予定している`tests/test_scrapbox_import_service.py`・`tests/test_pdf_export.py`・`tests/test_pdf_metadata_service.py`・`tests/test_pdf_text_service.py`・`tests/test_export_service.py`も未作成である。

#### 問題

1. **`test_web.py`が複数責務を抱えている**。最大の`HighlightQueryTest`は99件のテストメソッドを持ち、その内訳はR4（表示整形）67件、R7-4（PDF閲覧・変換・本文検索）23件、R7-2（PDFディレクトリ取り込みのHTTP契約）5件、R9（画面route）3件、R7-3（Scrapbox同期）1件である。クラス名が示す責務と実際の内容が一致していない。
2. **本体分離後も旧`web`境界のテストが残る**。R2（`config.py`）・R3（`paths.py`）・R4（`search_view.py`）は`web.py`に薄い委譲ラッパーを残す方針を採ったため、実装を移動しても既存テストはそのまま通過した。結果として、`ConfigResolutionTest`（15件）・`ResolvePdfPathTest`（9件）・`PdfUrlTest`（8件）・`UniqueDestinationPathTest`（3件）・`UniqueExportDestinationPathTest`（3件）とR4の表示整形67件が、HTTPを一切経由しないにもかかわらず`test_web.py`に残っている。
3. **ファイル行数そのものは問題の定義ではない**。問題は、`search_view.py`や`paths.py`を変更したときに`test_web.py`が壊れる／`test_web.py`を読んでも何がWeb層の契約なのか判別できない、という所有関係の不一致である。
4. **完了済みR項目と未実装R項目で移行方法が異なる**。未実装のR7-3・R7-4・R8は各設計書に既にテスト移行計画があり、実装PRの中で移動できる。一方、完了済みのR2・R3・R4には対応する実装PRがもう存在しないため、別の回収経路を決めない限り放置される。
5. **意図的に維持されている互換入口と、単なる残存の区別が必要である**。`test_web.py`には`patch("tsundokensaku.web.get_books_dir")`が74箇所、`patch("tsundokensaku.web.get_db_path")`が70箇所ある。これらはR2の設計判断（棚卸し文書§R2）で「133箇所超のmonkeypatchを維持するため`web.py`に明示的な委譲関数を残す」と決めたものであり、Web層のテストがBOOKS_DIR・DB_PATHを差し替えるための正当な手段である。これを「旧境界への不要なpatch」と誤認して一律に置き換える対象にしてはならない。

### 設計原則

#### テスト種別の定義

`docs/test-strategy.md`第2章・第3章の層区分を、`web.py`責務分離の文脈では次のように使い分ける。

- **サービス単体テスト**: `config.py`・`paths.py`・`search_view.py`・`index_job.py`・`pdf_import_service.py`など、分離済みまたは分離予定のモジュールを直接importして呼び出すテスト。HTTPを経由しない。内部の分岐、境界値、副作用、例外分類を詳細に検証する。
- **Web／HTTP契約テスト**: FastAPIのrouteまたはハンドラ関数を通して、入力検証、ステータス、レスポンス形式、redirect、template context、例外変換を検証するテスト。`TestClient`経由とハンドラ関数直接呼び出しの双方を含む。
- **統合テスト**: 複数モジュールと実ファイル・実DBをまたぐテスト。PDF生成・ZIP出力・index構築など。所有moduleが明確な場合はそのモジュールのテストファイルに置く。
- **Playwright E2E**: 利用者がブラウザ上で完了する主要操作。`docs/test-strategy.md`第3.6節の分担を変えない。
- **characterization test**: 実装移動の直前に、現在の観測可能な挙動を固定するテスト。「望ましい仕様」ではなく「現在の挙動」を固定するものであり、危険な挙動を恒久契約として固定しない（R7-2で確立した区別を踏襲する）。

#### 重複検証の判断基準

> 同一の内部分岐や副作用を複数のテスト層で詳細に再検証しない。ただし、各層が公開する契約を確認するため、同じ利用シナリオを異なる粒度で検証することは許容する。

「詳細な再検証」と「異なる粒度での契約確認」は次で区別する。

- 禁じるのは、サービス側テストと同じ入力集合・同じ境界値・同じ内部分岐を、Web層テストでもう一度網羅することである。
- 許容するのは、Web層がサービスを呼び出して結果をHTTPへ変換できていることの確認である。Web層では代表的な成功系1件と、変換が必要な失敗系の分類だけを持てばよい。
- 実例として、R7-1の`test_upload_pdf_preserves_supported_filename_characters`（`test_web.py`）と`test_save_uploaded_pdf_preserves_supported_filename_characters`（`test_pdf_import_service.py`）は同じファイル名集合を扱うが、前者はHTTP 201とレスポンスbodyが保存先パス文字列になることを、後者は戻り値の`Path`と書き込み内容を検証しており、粒度が異なる。これは許容する重複である。
- 逆に、サービス側で既にsymlink境界・OSError変換・部分成功を網羅しているR7-2について、Web層で同じ境界条件を再現するテストは追加しない。Web層は5分類の例外がそれぞれ安全なmessageへ変換されることだけを確認する。

#### ファイルとモジュールの対応

テストファイルとプロダクションモジュールの機械的な1対1対応は要求しない。所有者が明確で、変更理由が同じテストがまとまっていることを基準とする。既に`tests/test_pdf_import_service.py`がR7-1とR7-2の2責務を同居させているように、変更理由が近ければ1ファイルに複数責務が入ってよい。

### Web層に残す契約

`tests/test_web.py`には次を残す。

- **HTTP入力とFastAPIの入力検証**: query parameter、Form、path parameter、request body、必須・省略時の既定値、型不一致時の422。
- **routeからサービス・リポジトリへの引き渡し**: 呼び出しの有無、引数、呼ばれないこと（demo mode・入力不備時）。
- **HTTPステータス**: 200／201／303／400／403／404／422と、失敗時にstatusを変えない契約。
- **redirect**: リダイレクト先URL、`message` query parameterの内容と順序、303であること。
- **レスポンスJSONやレスポンス形式**: キー集合、型、後方互換性、`Content-Disposition`、media type、ZIP／JSON／PlainTextの選択。
- **template context**: 画面が描画されること、テンプレートへ渡す主要な値。
- **flash／status message**: 利用者向け文言そのもの。内部絶対パスやOSエラー全文を含まないこと。
- **サービス例外からHTTPレスポンスへの変換**: 例外分類ごとの表示文言と、`LOGGER.exception`の呼び出し回数。
- **demo modeなどWeb境界の制御**: 書き込み系routeが副作用を起こさないこと。
- **主要なroute配線**: routeの登録有無、URLの優先順位、OpenAPIスキーマ、削除済みrouteが404であること。

Web層のテストでサービス内部の全分岐を再検証しない。サービスが複数の失敗分類を持つ場合、Web層は各分類が一意なHTTP表現へ写ることだけを確認し、その分類が発生する具体的なfilesystem条件やDB状態はサービス側テストで扱う。

### サービス側へ移す契約

次はサービス側または既存の所有moduleのテストで検証する。実際の所有先は下表の「現在の所有先」列で確認する。

- **純粋な表示整形**: ハイライト、グループ化、並び替え、日時整形、Scrapbox本文生成、入力正規化（現在の所有先は`search_view.py`）。
- **パスやURL生成の純粋または非HTTPロジック**: BOOKS_DIR配下への解決、traversal・symlink拒否、`/view/`・`/pdf/`のURL文字列生成、一意名生成（現在の所有先は`paths.py`）。
- **ファイル保存・コピー・変換**: アップロードbyte列の配置、ディレクトリ再帰コピー、PDF切り出し、Markdown生成、ZIP組み立て（現在の所有先は`pdf_import_service.py`と、R7-4・R8で分離予定のmodule、および既存の`pdf_export.py`・`markdown_export.py`・`zip_export.py`）。
- **集計・正規化・並び替え**: トークン概算、chunk計画、warning生成（現在の所有先は`export_stats.py`・`export_profiles.py`と、R8で分離予定のmodule）。
- **成功・失敗・部分成功**: 件数の意味、fail-fast、失敗前の副作用が残ること。
- **rollback方針**: rollbackしない契約そのもの。
- **非HTTP例外**: service例外の分類、`__cause__`の保持、プログラミングエラーを変換しないこと。
- **サービス固有の境界値**: 大小文字、Unicode、空白、空入力、境界外パス、broken symlink。
- **PDFやScrapbox、エクスポート固有ロジック**: 既存の`pdf_extract.py`・`pdf_outline.py`・`pdf_thumbnail.py`・`metadata.py`・`database.py`が既に所有している範囲は移動しない。

### 現状分類表

`tests/test_web.py`の234件を論理グループへ分けた結果を示す。1クラス内で責務が混在する`HighlightQueryTest`と`PackApiTest`、`DemoModeUploadTest`は論理グループへ分割した。件数の合計は234件である。行番号は調査時点の補助情報であり、恒久的な識別子ではない。

| テスト群 | 現在の主な検証対象 | 対応責務 | 現在の所有先 | 将来の扱い | 移行時期 | 根拠 |
|---|---|---|---|---|---|---|
| `HighlightQueryTest`／表示整形群（67件） | `highlight_query`・`sort_results`・`group_pdf_results`・`build_search_result_rows`・`finalize_search_result_rows`・`normalize_search_*`・`build_search_scrapbox_body`・`_sanitize_scrapbox_title`・`_scrapbox_page_label`・`format_indexed_at`・`build_scrapbox_page_url` | R4 | `search_view.py`（`web.py`は委譲ラッパー） | 既存の責務別テストへ移す（`tests/test_search_view.py`） | 独立したテスト整理PR | 実装は`search_view.py`にあり、テストはHTTPを一切経由しない。棚卸し文書§R4は既存テストの一括書き換えを見送り、配置は後続PRで判断すると記録している |
| `HighlightQueryTest`／R7-2 HTTP群（5件） | `GET /settings/pdf-import`のredirect・成功message・例外5分類の変換・demo mode | R7-2 | `web.py`（route） | `test_web.py`に残す | — | R7-2設計書の完了条件がWeb層の保持対象として明記。サービス詳細は`tests/test_pdf_import_service.py`が所有済み |
| `HighlightQueryTest`／R7-3群（1件） | `import_scrapbox_export_bytes`の直接呼び出しとDB同期結果 | R7-3 | `web.py` | 今後のR実装PRで移す（`scrapbox_import_service.py`側） | R7-3実装時 | R7-3設計書が「既存`test_import_scrapbox_export_bytes_syncs_metadata`相当はserviceテストへ移す」と明記 |
| `HighlightQueryTest`／R7-4群（23件） | `pdf_outline`・`pdf_thumbnails`・`export_pdf`・`search_pages`・`export_markdown`・`save_pdf_export_to_configured_dir`・`resolve_pdf_scrapbox_url` | R7-4 | `web.py` | Webとサービスの異なる粒度で双方に必要 | R7-4実装時 | R7-4設計書が`tests/test_pdf_export.py`・`tests/test_pdf_metadata_service.py`・`tests/test_pdf_text_service.py`の新設と、`test_web.py`にroute契約だけ残すことを明記 |
| `HighlightQueryTest`／画面route群（3件） | `workspace_page`・`home`・`pack_list_page`の描画 | R9 | `web.py` | `test_web.py`に残す | — | R9はHTTP層として`web.py`に維持する分離対象外責務 |
| `IndexJobCharacterizationTest`（4件） | `POST /settings/index`・`GET /settings/progress`のredirect・message・JSON・実行中の分岐 | R6のHTTP境界 | `web.py`（route）＋`index_job.py` | `test_web.py`に残す | — | 棚卸し文書§R6が「HTTP契約は`test_web.py`に残す」と明記。module単体テストは`tests/test_index_job.py`へ移動済みで、patch対象も`tsundokensaku.web.index_job.start`という新境界になっている。クラス名が`tests/test_index_job.py`の同名クラスと重複している点だけは要検討 |
| `ResolvePdfPathTest`（9件）・`PdfUrlTest`（8件） | `resolve_pdf_path`・`pdf_url`・`raw_pdf_url`。traversal・symlink・URLエンコード | R3 | `paths.py`（`web.py`は委譲ラッパー） | 新しい責務別テストファイル候補（`tests/test_paths.py`） | 独立したテスト整理PR | 棚卸し文書§8段階0aで`test_web.py`へ追加された23件。実装は`paths.py`へ移動済みだがテストは未移動。HTTPを経由しない |
| `UniqueDestinationPathTest`（3件）・`UniqueExportDestinationPathTest`（3件） | `unique_destination_path`・`unique_export_destination_path`の一意名生成 | R3 | `paths.py`（`web.py`は委譲ラッパー） | 新しい責務別テストファイル候補（`tests/test_paths.py`） | 独立したテスト整理PR | 同上 |
| `PackApiTest`／CRUD群（6件） | pack作成・更新・削除・activate・import・round tripのHTTPライフサイクル | R9＋D6 | `web.py`（route）＋`database.py` | `test_web.py`に残す | — | HTTP APIの後方互換契約。DB側の保存契約は`tests/test_database.py`が別途所有 |
| `PackApiTest`／export群（6件） | export ZIPのmanifest・順序・エラーstatus | R8 | `web.py` | 今後のR実装PRで移す（一部）／HTTP契約は残す | R8実装時 | R8設計書が「JSON/archive移動PRでは`test_export_service.py`が生成結果、`test_web.py`がheader/status/historyを検証」と分担を明記 |
| `PackStatsApiTest`（3件）・`PackStatsRoutingTest`（4件） | `/api/packs/stats`の集計値とrouting優先順位 | R5候補＋R9 | `web.py`＋`export_stats.py` | R5または`database`系列の所有先決定待ち（集計）／`test_web.py`に残す（routing） | R5の所有先確定後 | R8設計書が`/api/packs/stats`をR5側の責務として範囲外に置いている。routing優先順位はHTTP固有 |
| `ExportArchiveBackwardCompatibilityTest`（4件） | ZIP構造・Markdown内容・エラー応答の後方互換 | R8 | `web.py`＋`zip_export.py` | 今後のR実装PRで判断 | R8実装時 | R8設計書がバイト互換維持を要件としており、移動可否は実装時に判断する |
| `ExportProfileParameterTest`（22件） | profile／formatの組み合わせ、400・404の優先順位、chunk分割結果 | R8 | `web.py` | `test_web.py`に残す（HTTP契約）。chunk分割の詳細検証は要追加調査 | R8実装時 | 大半がHTTPの入力検証とstatus契約。ただしchat／chapterのchunk内訳を検証する一部は`export_profiles.py`側と粒度が重なる可能性があり、R8実装時に精査する |
| `BuildExportPreviewPayloadTest`（4件）・`BuildExportPreviewPayloadForProfileTest`（4件） | `build_export_preview_payload`系の直接呼び出し。HTTPを経由しない | R8 | `web.py` | 今後のR実装PRで移す（`tests/test_export_service.py`） | R8実装時 | R8設計書が「pure preview移動PRで`tests/test_export_service.py`を新設し直接関数testを移す」と明記 |
| `PackExportPreviewTest`（16件） | preview APIの404・warning・profile別レスポンス | R8 | `web.py` | `test_web.py`に残す（HTTP契約） | R8実装時 | R8設計書が「`test_web.py`はroute adapter契約だけ残す」と明記 |
| `PdfUploadHttpCharacterizationTest`（8件） | `upload_pdf`ハンドラのstatus・body・検証順序 | R7-1 | `web.py`（route）＋`pdf_import_service.py` | Webとサービスの異なる粒度で双方に必要 | — | ハンドラ経由でHTTP 201／400とレスポンスbodyを検証しており、service側の`Path`戻り値検証とは粒度が異なる。R7-1設計書が想定した分担どおり |
| `ConfigResolutionTest`（15件） | `get_books_dir`・`get_db_path`・`get_pdf_export_save_dir`・`update_env_setting` | R2 | `config.py`（`web.py`は委譲ラッパー） | 新しい責務別テストファイル候補（`tests/test_config.py`） | 独立したテスト整理PR | 棚卸し文書§R2がR2のcharacterization testとして記録。実装は`config.py`へ移動済み。HTTPを経由しない |
| `DemoModeUploadTest`／`is_demo_mode`（1件） | 環境変数の大小文字解釈 | R2 | `config.py` | 新しい責務別テストファイル候補（`tests/test_config.py`） | 独立したテスト整理PR | 同上。ただし移動時は残りの6件がdemo mode判定を前提にしていることを壊さない |
| `DemoModeUploadTest`／HTTP群（6件） | 各書き込み系routeがdemo modeで403または303を返し副作用を起こさないこと | R9／Web境界 | `web.py` | `test_web.py`に残す | — | 横断的関心事のWeb境界制御。棚卸し文書「補助（デモモード制御）」の位置づけ |
| `ExportEventRecordingTest`（8件） | export成功後のevent記録、失敗時に記録しないこと、記録失敗がexportを壊さないこと | R8＋D7 | `web.py`＋`database.py` | HTTP経路の記録契約は`test_web.py`に残す／永続化本体はD7側 | R8・D7実装時 | R8設計書が「event永続化本体はD7の責務に残す」と明記。記録タイミングはroute内にあるためWeb契約でもある |
| `ArtifactRemovalApiTest`（1件） | 削除済みrouteが404であること、OpenAPIに現れないこと | R9 | `web.py` | `test_web.py`に残す | — | route登録そのものの契約 |

責務ごとの該当有無は次のとおりである。

- **R2**: 該当あり（16件）。完了済みだが残存。
- **R3**: 該当あり（23件）。完了済みだが残存。
- **R4**: 該当あり（67件）。完了済みだが残存。
- **R5候補**: 直接の単体テストは`test_web.py`に存在しない。`get_pdf_stats`・`get_db_stats`・`get_library_items`への直接テストはなく、`/api/packs/stats`のHTTPテストが間接的に関連するのみ。
- **R6**: 該当あり（4件）。ただしHTTP契約として意図的に残されており、移行対象ではない。
- **R7-1**: 該当あり（8件）。HTTP契約として残す。service詳細は移動済み。
- **R7-2**: 該当あり（5件）。HTTP契約として残す。service詳細は移動済み。
- **R7-3**: 該当あり（1件）。R7-3実装時に移す。
- **R7-4**: 該当あり（23件）。R7-4実装時に分割する。
- **R8**: 該当あり（64件）。うち直接関数テスト8件はservice側へ、残りはHTTP契約として精査する。
- **R9／Web固有**: 該当あり（14件）。残す。
- **Pack／`database`系列**: 該当あり（6件）。HTTP APIの契約として残し、DB側契約は`tests/test_database.py`が所有する。

### 完了済みR項目の移行方針

#### 残存状況

| R項目 | 分離先 | `test_web.py`の残存 | 対応する責務別テストファイル |
|---|---|---|---|
| R2 | `config.py` | `ConfigResolutionTest`15件＋`is_demo_mode`1件 | 未作成 |
| R3 | `paths.py` | パス・URL・一意名の23件 | 未作成 |
| R4 | `search_view.py` | 表示整形67件 | `tests/test_search_view.py`は存在するが境界契約8件のみ |
| R6 | `index_job.py` | HTTP契約4件のみ | `tests/test_index_job.py`（7件、移動済み） |
| R7-1 | `pdf_import_service.py` | HTTP契約8件のみ | `tests/test_pdf_import_service.py`（移動済み） |
| R7-2 | `pdf_import_service.py` | HTTP契約5件のみ | `tests/test_pdf_import_service.py`（移動済み） |

R6・R7-1・R7-2は既に望ましい状態にある。移行が必要なのはR2・R3・R4の3項目、合計106件である。

#### 回収経路

これらは次のいずれで移してもよい。

1. **関連する後続実装PRで回収する**。移動対象のモジュールを触るPRであれば、その責務のテストを同じPRで移してよい。例として、`paths.py`の関数を利用するR7-4の実装PRでR3のテストを併せて移すことは、変更理由が近いため許容する。
2. **独立した小さなテスト整理PRで移す**。プロダクションコードを変更せず、テストの所有先だけを移すPRを作る。

いずれの経路でも、次のスコープ基準を守る。

- 1つのPRで移す責務は1つとする。R2とR3を同じPRで移さない。
- プロダクションコードの振る舞いを変えるPRに、その変更と無関係な責務のテスト移動を混ぜない。R7-4の実装PRでR2のテストを移すことは認めない。
- 1回のPRで移動するテスト件数の上限値は定めない。ただし、責務が1つに限定され、差分がテストファイルの移動とimport変更に閉じていることを条件とする。R4の67件のように単一責務でまとまっている場合は1PRでよい。
- テストの移動と、テスト内容の変更（assertの追加・削除、命名変更を超える書き換え）を同じコミットに混ぜない。

#### 独立PRの分割候補

現状調査に基づく候補を示す。いずれも所有先が既に確定しており、追加の設計判断を必要としない。この順序を実施順として確定するものではない。

- **候補A: R3のパス・URLテストを`tests/test_paths.py`へ移す**。対象は`ResolvePdfPathTest`・`PdfUrlTest`・`UniqueDestinationPathTest`・`UniqueExportDestinationPathTest`の23件。importを`tsundokensaku.paths`へ切り替える。`web.py`の委譲ラッパーが生きていることは`tests/test_search_view.py`の`WebModuleCompatibilityTest`と同様の最小テストで別途担保するか、既存のHTTPテストによる間接的な利用で足りるかを移行時に判断する。
- **候補B: R2の設定解決テストを`tests/test_config.py`へ移す**。対象は`ConfigResolutionTest`15件と`DemoModeUploadTest.test_is_demo_mode_reads_env_var_case_insensitively`1件。`patch("tsundokensaku.web.get_books_dir")`等の既存monkeypatchは委譲ラッパーを対象とする正当な用法なので変更しない。
- **候補C: R4の表示整形テストを`tests/test_search_view.py`へ移す**。対象は67件。件数が多く、`HighlightQueryTest`から他責務のテストを残したまま抽出する必要があるため、移動前に`HighlightQueryTest`内の責務別グループ境界を確認する。必要なら表示整形の下位グループ（ハイライト系、並び替え・グループ化系、Scrapbox本文系）へさらに分けてよい。
- **候補D: 残存Webテストの再編**。候補A〜Cの完了後、`test_web.py`に残ったテストクラスの名称と分類を見直す。`HighlightQueryTest`という実態と合わないクラス名、`IndexJobCharacterizationTest`が`tests/test_index_job.py`のクラス名と重複している点をここで扱う。これは命名と再配置だけを行い、assertを変更しない。

### 未実装R項目の移行方針

R7-3・R7-4・R8は、それぞれの設計書が既にテスト移行計画を持っている。本文書はそれらを置き換えず、共通原則だけを定める。着手順は決めない。

- **characterization testで既存契約を保護する**。実装移動の前に、現在のHTTP契約と観測が必要な内部挙動を固定するコミットを置く。危険な現行挙動を恒久契約として固定しない（R7-2で確立した区別）。
- **サービス実装時に詳細テストを責務側へ置く**。新設するサービスのテストファイルは各設計書の指定に従う。R7-3は`tests/test_scrapbox_import_service.py`、R7-4は`tests/test_pdf_export.py`・`tests/test_pdf_metadata_service.py`・`tests/test_pdf_text_service.py`、R8は`tests/test_export_service.py`である。
- **Web側にはHTTP契約を残す**。route単位の入力、status、body shape、header、例外変換を`test_web.py`に維持する。
- **移動と振る舞い変更を混在させない**。ただし、R実装PRが責務移動と対応テスト移動を同時に行うことは許容する。これは同一の変更理由に属するためである。
- **既存テストを削除するだけにしない**。移動先で同じ保証が成立していることを確認してから削除する。
- **patch対象を実際の参照先へ更新する**。R7-2が`patch("tsundokensaku.web.pdf_import_service.import_pdfs_from_directory")`へ更新したように、サービス呼び出しのmonkeypatchは新しい境界を指すようにする。ただし`get_books_dir`・`get_db_path`のようにR2で意図的に維持された委譲ラッパーへのpatchは対象外とする。

### R5の扱い

R5（ライブラリ／統計の集計）は`books_repo.py`または`database.py`系列へ合流する候補であり、所有先は現時点では未確定である。テスト移行の完了判定は次とする。

- R5の完了を`web.py`側に独立モジュールを作ることに限定しない。`database.py`系列のモジュールへ合流した場合も完了とみなす。
- 最終的な所有先が確定し、該当するテストがその境界へ整理された時点をR5のテスト移行完了とする。
- `database.py`系列へ合流した場合、D1〜D7全体の完了を待たない。R5相当の集計責務が移った先のテストが整った時点で判定する。
- R5の所有先が未確定であることを理由に、`test_web.py`全体の整理が完了不能にならないようにする。本文書の完了条件はR5以外の項目で個別に判定でき、R5は「所有先確定後にその境界へ整理済み」という条件だけを残す。
- 現時点で`test_web.py`にR5の直接単体テストは存在しない。関連するのは`/api/packs/stats`のHTTPテスト7件であり、このうちrouting優先順位の4件はHTTP固有として残る。集計値を検証する3件の所有先が、R5確定後の判断対象である。

### ファイル構成方針

- 本体モジュールとテストファイルの機械的な1対1対応は要求しない。`tests/test_pdf_import_service.py`がR7-1とR7-2を同居させている構成を否定しない。
- 現在のフラットな`tests/`構成を直ちに`tests/web/`等へ変更しない。
- 将来のバックエンドパッケージ構成見直し（ROADMAPの別項目）を先取りしない。パッケージ構成が変わる際にテスト配置も再検討する余地を残す。
- 既存の責務別テストファイルがある場合は原則として活用する。R4は`tests/test_search_view.py`が既にあるため、新規ファイルを作らずそこへ移す。
- 新しいファイルは、変更理由とテスト所有者が明確になる場合だけ作成する。`tests/test_paths.py`・`tests/test_config.py`は所有moduleが確定しているため作成してよい。
- ファイル名変更自体を目的にしない。`HighlightQueryTest`のような実態と合わないクラス名の是正は候補Dで扱うが、これは分類の可読性のためであり、命名規則の統一を目的としない。

### 移行手順

各テスト移動PRでは次の手順を標準とする。

1. **移動対象の保証内容を確認する**。何を検証しているテストか、移動先で同じ前提（一時ディレクトリ、環境変数、patch対象）が成立するかを確認する。
2. **既存テストを先に移動または複製する**。移動先ファイルへ配置する。同じ保証をWeb側にも残す必要がある場合は、この時点では複製とする。
3. **import・patch境界を新しい所有先へ更新する**。`from tsundokensaku.web import X`を所有moduleからのimportへ切り替える。サービス呼び出しのpatch対象を新しい参照先へ更新する。R2で意図的に維持された委譲ラッパーへのpatchは変更しない。
4. **同じ保証が通ることを確認する**。移動先で移動前と同じテストが成功することを確認する。
5. **Web側に必要な最小契約テストを残す**。routeを経由する契約が失われないことを確認する。
6. **不要になった詳細重複だけを削除する**。移動元の重複を削除する。粒度が異なるものは残す。
7. **Pythonテスト全件を実行する**（`docker compose run --rm --entrypoint python app -m unittest discover -s tests`、またはCIと同じ`python -m unittest discover -s tests`）。移動前後で件数が減った場合は、意図した重複削除の分だけかを説明できるようにする。
8. **Web表示・操作へ影響する場合はPlaywright全件を実行する**（`npm run test:ui`）。テスト移動だけでプロダクションコードを変更しないPRでは必須としないが、判断に迷う場合は実行する。
9. **`git diff --check`を実行する**。
10. **ROADMAPまたは設計書の進捗を更新する**。どの責務のテストを回収したかを記録する。

テストの移動と振る舞い変更を同じPRに混在させない。ただし、今後のR実装PRで責務移動と対応するテスト移動を同時に行うことは許容する。この場合も、プロダクションコードの振る舞いを変えないことを前提とする。

### 完了条件

R2〜R8という番号の一括完了だけには依存しない。次をすべて満たした時点で「`test_web.py`の責務整理」を完了とする。

- `test_web.py`に残る各テスト群が、Web／HTTP層の契約として説明できる。本文書の「Web層に残す契約」のいずれかに対応づけられる。
- サービス内部の詳細ロジックが、原則として責務側テストへ移っている。
- 完了済みR項目（R2・R3・R4・R6・R7-1・R7-2）の残存テストが分類・処理済みである。移動したか、Web契約として残す判断をしたかのいずれかが記録されている。
- 未実装R項目（R7-3・R7-4・R8）については、各実装時に移行する計画が各設計書で確定している。実装完了を待たない。
- R5は最終所有先が確定し、その境界へテストが整理済みである。所有先確定前は本項目のみ未充足として扱い、他の条件の判定を妨げない。
- 同一内部ロジックの無目的な詳細重複がない。異なる粒度での契約確認は重複に数えない。
- 移動前の保証内容が失われていない。削除したテストがある場合、同じ保証が移動先に存在することを説明できる。
- 旧`tsundokensaku.web`を不必要にpatchするテストが残っていない。ここでいう「不必要」は、所有moduleへ直接patchできるにもかかわらず`web.py`を経由しているものを指す。R2の設計判断で意図的に維持している`get_books_dir`・`get_db_path`等の委譲ラッパーへのpatchは含まない。
- Pythonテスト全件が成功する。
- 必要なPlaywrightテスト全件が成功する。
- `docs/test-strategy.md`と矛盾しない。
- 行数やテスト件数を完了基準にしない。件数の増減は結果であり、目標ではない。

### 非目標

- 本PRでのテスト移動。本PRは設計のみで、テストコードを変更しない。
- プロダクションコードの変更。
- テスト件数の削減。
- カバレッジ率目標の導入。
- `tests/`の全面的なディレクトリ再編。
- R7-3・R7-4・R8の着手順の決定。
- R5の所有先の確定。
- `database.py`責務分離（D1〜D7）の設計変更。
- Playwrightへの過度な移管。Python層で検知すべき失敗をE2Eへ移さない。
- 既存HTTP契約の変更。URL、method、status、レスポンス形式、message文言を変えない。
- `docs/test-strategy.md`の改訂。上位方針は現行のままとする。

### 実装PR候補

現状調査に基づく候補である。ROADMAP上の次の実装順として確定するものではなく、テスト整理の候補にとどまる。R7-3・R7-4・R8の実装PRは各設計書が所有するため、ここでは扱わない。

#### 候補A: R3のパス・URLテスト移動

- **対象テスト群**: `ResolvePdfPathTest`（9件）・`PdfUrlTest`（8件）・`UniqueDestinationPathTest`（3件）・`UniqueExportDestinationPathTest`（3件）の23件。
- **移動先候補**: 新規`tests/test_paths.py`。
- **残すWeb契約**: なし。これら4クラスはHTTPを経由しない。`/view/`・`/pdf/`のURLがHTTPレスポンスに現れることは既存のPDF系routeテストが間接的に確認している。
- **非目標**: `paths.py`の実装変更、`web.py`の委譲ラッパー削除、traversal・symlink検証の強化。
- **実行テスト**: Pythonテスト全件。Playwrightは必須としない。
- **依存条件**: なし。所有moduleが確定済みで、他の移行と競合しない。
- **リスク**: 低。ただし`resolve_pdf_path`はパストラバーサル検証を含むため、移動時に9件すべてが移動先で成立することを個別に確認する。移動漏れがセキュリティ回帰の見落としにつながる。

#### 候補B: R2の設定解決テスト移動

- **対象テスト群**: `ConfigResolutionTest`（15件）と`DemoModeUploadTest.test_is_demo_mode_reads_env_var_case_insensitively`（1件）の16件。
- **移動先候補**: 新規`tests/test_config.py`。
- **残すWeb契約**: `DemoModeUploadTest`の残り6件（各routeがdemo modeで副作用を起こさないこと）は`test_web.py`に残す。
- **非目標**: `config.py`の実装変更、`web.py`の委譲ラッパー削除、`patch("tsundokensaku.web.get_books_dir")`等144箇所のpatch対象の書き換え。
- **実行テスト**: Pythonテスト全件。
- **依存条件**: なし。
- **リスク**: 低〜中。`update_env_setting`のテストは`.env`ファイルを扱うため、移動先でも一時ディレクトリへ隔離されていることを確認する。`DemoModeUploadTest`から1件だけ抜くとクラスの意図が変わるため、残る6件がdemo modeのWeb境界テストであることをクラス名または説明で明確にする。

#### 候補C: R4の表示整形テスト移動

- **対象テスト群**: `HighlightQueryTest`内の表示整形67件。
- **移動先候補**: 既存`tests/test_search_view.py`。
- **残すWeb契約**: 検索画面・検索結果のHTTPレスポンス。ただし現在`test_web.py`には`/search`routeの直接テストが存在しないため、移動時に不足するWeb契約テストを追加すべきかを判断する。これは追加調査が必要な事項である。
- **非目標**: `search_view.py`の実装変更、`web.py`の委譲ラッパー削除、表示整形ロジックの仕様変更。
- **実行テスト**: Pythonテスト全件。表示に関わるためPlaywright全件も実行する。
- **依存条件**: なし。ただし件数が多いため、候補A・Bの後に行うと`HighlightQueryTest`の残存内容が把握しやすい。
- **リスク**: 中。67件は`HighlightQueryTest`の他責務32件と同一クラス内に散在しており、行の連続した範囲ではない。抽出漏れ・誤抽出を避けるため、移動前後でテストメソッド名の集合を突き合わせる。`build_search_result_rows`と`finalize_search_result_rows`は`paths.resolve_pdf_path`経由でファイル実存確認を行うため、移動先でも一時ディレクトリの前提が必要である。

#### 候補D: 残存Webテストの再編

- **対象テスト群**: 候補A〜C完了後に`test_web.py`へ残るテストクラス。
- **移動先候補**: なし（同一ファイル内の再編）。
- **残すWeb契約**: すべて。assertを変更しない。
- **非目標**: テストの追加・削除、assertの変更、ファイル分割。
- **実行テスト**: Pythonテスト全件。
- **依存条件**: 候補A・B・Cの完了。
- **リスク**: 低。ただしクラス名の変更は`docs/`内の参照（棚卸し文書・各R設計書がクラス名で既存テストを参照している箇所）と不整合を生じ得るため、変更する場合は参照側の更新要否を確認する。本PRでは既存設計書を変更しないため、この確認は候補D実施時に行う。

### 未確定事項

- `HighlightQueryTest`内の`ExportProfileParameterTest`に相当するchunk分割検証の一部が、`tests/test_export_profiles.py`と粒度で重なる可能性がある。R8実装時に精査する。現時点では重複と断定しない。
- 候補Cで`/search`routeのWeb契約テストを追加すべきかは追加調査が必要である。本文書では要否を確定しない。
- R5に属する`/api/packs/stats`の集計値検証3件の最終的な所有先は、R5の所有先確定後に決まる。現時点では未確定である。
- `IndexJobCharacterizationTest`のクラス名変更の要否は候補Dで扱う。本文書では必須としない。
