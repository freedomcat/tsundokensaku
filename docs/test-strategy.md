# つんどけんさく テスト戦略

## 1. 目的

この文書は、テスト件数を増やすことではなく、つんどけんさくの振る舞いをどの境界で保証するかを決めるための基準である。現在のPython 371件・Playwright 29件という規模を記録するだけでなく、新機能、バグ修正、構造改善のたびに「最も低い適切な層で原因を検知し、必要な利用者フローだけを上位層で確認する」判断を可能にする。

特に、`web.py`・`database.py`・フロントエンドの責務分離では、外部URL、HTTP API、DBスキーマ、画面操作を維持する安全装置として既存テストを使う。内部構造を変える場合も、テストが実装詳細のコピーにならないよう、利用者向けの結果とモジュール間の契約を固定する。

テストは次を目的とする。

- 純粋な計算・変換の誤りを高速に検知する
- DB、HTTP、ファイル、ブラウザという境界ごとの契約を検証する
- 利用者が主要な検索・資料作成・ページ追加・書き出しを完了できることを確認する
- バグの再発条件を、原因に近いテストとして残す
- 構造改善の前後で外部仕様が変わっていないことを段階的に確認する

## 2. 現在のテスト全体

### 層の一覧

| 層 | 現在の実装 | 主な対象 | 現在の実行場所 |
| --- | --- | --- | --- |
| 純粋ロジック・単体 | Python `unittest`、一部のJSロジックはPlaywright内で検証 | 文字列、ページ指定、検索条件、メタデータ、エクスポート計画、トークン概算 | Python CI、Playwright CI |
| DB・永続化 | Python `unittest` + 一時SQLite | スキーマ初期化、移行、CRUD、検索、pack、トランザクションに近い保存契約 | Python CI |
| HTTP API・FastAPI | Python `unittest`、`web.py`の関数とTestClient相当の境界 | HTTP入力、ステータス、JSON、APIライフサイクル、デモモード | Python CI |
| PDF・ファイル統合 | Python `unittest` + 一時ディレクトリ + 実PDF/生成PDF | 抽出、アウトライン、サムネイル、ページ切り出し、Markdown、ZIP、インデックス | Python CI |
| CLI | Python `unittest` | `index`、`search`、タイトル更新などのCLI入口と終了結果 | Python CI |
| ブラウザE2E | Playwright 5 spec、29 test、Chromium、workers=1 | 画面をまたぐ主要操作、DOM状態、ブラウザ側同期、ダウンロード | Playwright CI |
| CI実行 | `.github/workflows/ci.yml` | 上記suiteを再現可能なUbuntu環境で実行 | GitHub Actions |

Pythonの371件は`python -m unittest discover -s tests`で構成されるsuiteの件数であり、ファイルごとのメソッド数の単純合計とは異なる。Playwrightは`playwright.config.js`の5 spec・Chromium project・`workers: 1`で29件を実行する。

### 層の関係

```text
純粋ロジック
    ↓
DB・PDF/ファイル・CLI・HTTP API
    ↓
ブラウザE2E
    ↓
GitHub Actions（隔離環境で全件実行）
```

上位層は下位層の代替ではない。Playwrightだけでページ指定の全組み合わせやSQLの全分岐を保証しないし、単体テストだけでブラウザの導線やモーダルの状態同期を保証しない。

## 3. 各テスト層の保証範囲

### 3.1 純粋ロジック・単体テスト

対象は、DOM、HTTPサーバー、実DBを必要としない関数である。現在は`tokenizer.py`、`token_estimate.py`、`metadata.py`、`export_profiles.py`、`export_stats.py`、`zip_export.py`、`markdown_export.py`、`pdf_outline.py`の変換・計画・整形ロジックが中心である。`tests/test_web.py`にも検索結果整形、ハイライト、グループ化、入力正規化、書き出しpayloadの純粋寄りテストがある。

保証するもの:

- 同じ入力に対する検索語、ページ範囲、メタデータ、ファイル名、警告、エクスポート計画の決定結果
- 境界値、空値、無効値、順序、重複を含む変換規則
- 章分割やトークン概算のような、外部I/Oを含まない業務規則

新しい変換関数や既存関数の規則変更では、まずこの層へ置く。速く、失敗箇所が原因に近いからである。代表例は`tests/test_tokenizer.py`、`tests/test_token_estimate.py`、`tests/test_export_profiles.py`、`tests/test_zip_export.py`である。

この層に置かないもの:

- FastAPIのHTTPステータスや実際のJSON配信
- SQLiteのスキーマ、commit、FTSの動作
- PDFファイルを開くこと自体、実ファイルの出力
- DOMの表示やクリック順序

JSのページ指定ロジックは`static/pages-spec.js`などへ共通化されているが、現状は独立したJS単体テスト基盤を持たない。DOM不要のJSロジックを大きく追加する場合は、既存構成に自然な単体テスト手段があるかを先に判断し、Playwrightへ無理に詰め込まない。

### 3.2 DB・永続化テスト

対象は`database.py`の`connect`、`initialize`、スキーマ保証・移行、書籍・ページ・メモ・pack・検索・エクスポート履歴の保存境界である。各テストは`tempfile.TemporaryDirectory()`内のSQLiteを作成し、`initialize()`してから操作する。

保証するもの:

- テーブル、FTS、制約、既存DBの再初期化、旧スキーマ移行
- CRUD、順序、重複、削除、active packのフォールバック、pack itemの保存
- 検索scope、FTS/trigramの検索結果とページ・snippetの対応
- 同一接続での保存結果、保存後の再読込、データを失わない更新契約

代表例は`tests/test_database.py`の`DatabaseSearchTest`、`SearchSyntaxTest`、`PackTest`、`ArtifactRemovalDatabaseTest`である。`replace_pack_item_entries`のように資料項目の順序やIDを扱う処理は、同一PDFの複数項目も含めてここでDB上の区別を固定する。

DB関数を追加・変更したときは、HTTPを通さない最小ケースをこの層に追加する。HTTP APIのJSON変換や画面表示を同じテストで検証しない。スキーマ変更を伴う場合は、ROADMAPの「データ保全とスキーマ管理の方針決定」に従い、互換性・移行・ロールバックの確認を別途明確にする。

### 3.3 HTTP API・FastAPIテスト

対象は`web.py`のFastAPI routeと、routeが公開する入力検証・ステータス・レスポンス形式である。現在は`tests/test_web.py`がpack API、統計、書き出し、デモモード、エクスポート履歴などを検証する。テストは一時DBをpatchしてアプリケーションのAPI境界を呼び出し、レスポンスbodyや`HTTPException`を確認する。

保証するもの:

- endpointの成功・失敗ステータスと入力検証
- JSONのキー、型、ページ範囲、pack item、エクスポートpreviewの契約
- routeからDB・サービスへの引き渡しと、エラーが利用者向けレスポンスになること
- APIとしての後方互換性、デモモードで書き込みを拒否すること

代表例は`tests/test_web.py`の`PackApiTest`、`PackStatsApiTest`、`ExportProfileParameterTest`、`PackExportPreviewTest`、`DemoModeUploadTest`である。

この層では、細かいDOM配置、ブラウザのfocus、クリック順序、CSSの見た目を保証しない。route内部の純粋な計算は単体テストへ、利用者が複数画面を遷移するフローはPlaywrightへ分ける。

### 3.4 PDF・ファイル処理の統合テスト

対象は複数モジュールとファイルシステムをまたぐ処理である。`pdf_extract.py`、`pdf_outline.py`、`pdf_thumbnail.py`、`pdf_export.py`、`markdown_export.py`、`zip_export.py`、`indexer.py`を、生成PDFまたは同梱サンプルPDFと一時ディレクトリで検証する。

保証するもの:

- PDFのページ単位本文抽出、PyMuPDF優先とpypdf fallbackの契約
- アウトラインからの章範囲、サムネイル、指定ページの切り出し
- PDF/Markdown/ZIPの出力内容、順序、manifest、ファイル名
- `BOOKS_DIR`の探索、差分index、DB投入、削除されたPDFの扱い

代表例は`tests/test_pdf_extract.py`、`tests/test_pdf_outline.py`、`tests/test_pdf_thumbnail.py`、`tests/test_export_pdf_pages.py`、`tests/test_markdown_export.py`、`tests/test_zip_export.py`、`tests/test_indexer.py`である。`noosphere.pdf`など同梱PDFを使う実体験に近い検証と、テスト内で生成する小さなPDFを使う境界検証を使い分ける。

破損・暗号化などの異常PDFは、現状の通常系統合テストの保証範囲に含めない。新たに対応する場合は、まず抽出・ファイル処理の最小再現をPython統合テストへ置き、ユーザーが画面で復旧操作を行う機能まで変わる場合だけE2Eを追加する。

### 3.5 CLIテスト

対象は`src/tsundokensaku/cli.py`のコマンド入口、引数、終了コード、標準出力、DBに対する結果である。代表例は`tests/test_cli.py`のindex/search/title refresh系テストで、TemporaryDirectory内にDB・JSON・PDFを作り、`main([...])`を直接呼び出す。

保証するもの:

- コマンド引数が正しい処理へ到達すること
- 終了コード、表示、dry-run、対象限定、DB更新結果
- CLIとWebが共有するdatabase/indexerの公開契約が壊れていないこと

CLIの内部関数の細かい文字列変換は単体層、CLIから起動したWeb画面はPlaywright層で検証する。CLI変更が検索・index・タイトル更新のDB結果に影響する場合は、CLIテストだけでなく最小のDB契約も維持する。

### 3.6 ブラウザE2E・Playwright

対象は、利用者がブラウザ上で完了する主要操作と、ブラウザ状態・API・DOMの接続である。現在の5 specは次を分担する。

- `pack_store_identity.spec.js`: サーバーID付与、clientId維持、同時保存、順序・削除、古い応答、重複項目
- `workspace_add_pdf.spec.js`: 検索対象選択、PDFプレビュー、ページ範囲更新、アウトラインなしPDF、ページ追加
- `search_multiple_adds.spec.js`: 画面導線、同一/異なるPDFの複数追加、個別編集、書き出し
- `pdf_modal_overlay.spec.js`: overlay、focus trap、zoom、サムネイルの非同期リクエスト
- `ai_export_flow.spec.js`: 書き出し先説明、概算表示、ZIPダウンロード

保証するもの:

- 検索から資料への追加、資料の編集、PDFページ選択、書き出しという主要な利用者フロー
- DOM上の操作可能性、表示される主要文言、モーダルの状態、ダウンロード結果
- `pack-store.js`のブラウザキャッシュ・サーバー同期・重複項目の論理識別子
- 非同期応答や意図的なPUT保留を含む、利用者操作と保存の競合

今回の識別子競合では、保存応答をPlaywright routeで保留し、モーダルを開いてから応答を解放することで、保存前のモーダル参照がサーバーID付与後も同じ項目を更新できることを検証している。これは固定待機ではなく、競合イベントを観測してから次の操作へ進む回帰テストである。

Playwrightだけで保証しないもの:

- 全てのページ範囲・検索語・SQL分岐の組み合わせ
- ピクセル単位のデザイン品質、全ブラウザ・全OSの見た目
- 実蔵書の規模、個人の`.env`、本番DBの移行
- DB内部の制約やPDF抽出アルゴリズムの全境界

### 3.7 GitHub Actions

`.github/workflows/ci.yml`は、`pull_request`（`master`・`develop`向けが対象）と`push`（`master`・`develop`・`feature/**`向けが対象）で、`python-tests`と`playwright`を別jobで実行する。両jobは`ubuntu-latest`、Python 3.13を使う。Playwright jobだけNode.js 22、`npm ci`、Chromiumの`npx playwright install --with-deps chromium`、`npm run test:ui`を実行する。

CI成功が保証するもの:

- clean checkoutしたリポジトリでPython suite 371件が成功すること
- 隔離されたLinux環境でPlaywright 29件がChromium・workers=1で成功すること
- package依存のインストール、サンプルPDFのindex、ローカルサーバー起動、readiness確認が通ること

CI成功だけでは保証しないもの:

- Windows/WSL/macOS、Docker Compose、本番構成、実蔵書、個人DB、個人`.env`
- Playwrightの並列実行、他ブラウザ、低速/高負荷環境
- CIにまだ含まれない異常PDF、視覚品質、アクセシビリティ、セキュリティ、性能基準
- GitHubのbranch protection、required status checks、マージ運用そのもの

## 4. 新規テストの配置基準

次の表で、最初に置く層を決める。上位層へ同じケースを複製するのではなく、下位層で原因を検知し、利用者影響がある場合だけ上位層を補う。

| 変更 | 第一候補 | 上位層を追加する条件 |
| --- | --- | --- |
| 純粋関数・変換ロジック | Python単体、または既存構成に合うJS単体 | API形式や画面結果が変わるときだけAPI/E2Eを追加 |
| DB操作・スキーマ・検索 | DBテスト | HTTP公開契約が変わるときAPI、主要導線が変わるときE2E |
| HTTP API | FastAPI/TestClientテスト | 利用者が複数画面を操作する新導線ならE2E |
| PDF処理 | PDF/ファイル統合テスト | 画面上の選択・表示・ダウンロードまで変わるときE2E |
| CLI | CLIテスト | Webと同じ利用者フローを壊すとき、共有契約を別層で補う |
| UI操作フロー | Playwright | 核となる計算・保存規則は単体/DB/APIにも置く |
| 表示文言だけ | 影響する既存テストの最小更新 | 文言が操作完了条件やAPIエラー契約なら該当層を更新 |
| バグ修正 | 修正原因に最も近い層 | 利用者の重要操作が壊れた場合のみE2Eを追加 |
| 内部構造だけの変更 | 既存テストを維持して全件確認 | 新しい境界の契約が生じた場合だけ小さな契約テストを追加 |
| 複数層の新機能 | 各層の責務に分解 | 最終的な利用者フローを代表するE2Eを1本以上追加 |

判断フローは次のとおりである。

1. DOM・HTTP・DB・ファイルを必要としないか。そうなら単体へ置く。
2. SQLiteの状態やトランザクションが結果の一部か。そうならDB層へ置く。
3. HTTP status、入力検証、JSON形式が契約か。そうならAPI層へ置く。
4. 実ファイル、PDF、複数モジュールの副作用が必要か。そうなら統合層へ置く。
5. CLIの引数・終了コード・標準出力が契約か。そうならCLI層へ置く。
6. 利用者が複数画面・モーダル・非同期処理を完了することが目的か。そうならPlaywrightへ置く。
7. 複数層を通る場合も、各層にはその層固有の最小契約だけを置き、同じ入力・同じassertを無目的に重複させない。

## 5. 回帰テストの基準

バグ修正では、原則として修正前に失敗し、修正後に成功する再現テストを追加または調整する。テストは原因に最も近い層へ置く。画面上の重要操作まで壊れた場合だけ、原因に近いテストに加えてPlaywrightで利用者フローを固定する。

- 固定sleepや大きなtimeoutで偶然通るテストにしない
- 非同期競合はリクエスト開始・応答・状態変化を観測して再現する
- 実装のprivate変数やDOM構造の細部ではなく、外部動作・論理項目・レスポンスを検証する
- 重複PDFのように同じ値だけでは区別できない場合は、論理識別子、順序、対象だけが変わることを検証する
- テスト名から「何が再発してはいけないか」が分かるようにする
- 失敗テストをskip、retry、固定待機で隠さない

識別子競合の回帰テストは、`pack-store.js`のclientId維持をブラウザの同期境界で確認し、同一PDFの2項目が2件のまま一方だけ更新されることを固定している。この方針は、今後のキャッシュ・サーバー応答・UI参照の変更にも適用する。

## 6. CIで保証する範囲

Python jobはcheckout、Python 3.13 setup、`pip install -e .`、`python -m unittest discover -s tests`を実行する。Playwright jobはcheckout、Python 3.13、依存導入、Node.js 22、`npm ci`、Chromium導入、`npm run test:ui`を実行する。2026-07-26のrunではPython 371件、Playwright 29件が成功している。

Playwrightの`run_playwright.sh`は、`mktemp`で一時rootを作り、その下にBOOKS_DIRとDB_DIRを作る。`cathedral.pdf`、`magicpot.pdf`、`noosphere.pdf`だけをコピーし、一時DBへindexを構築して127.0.0.1:8003のサーバーをreadiness確認後に起動する。終了時のEXIT/INT/TERM trapでサーバーを停止し、一時rootを削除する。`.env`、`data/index.db`、個人蔵書、未追跡PDFには依存しない。

`tests/playwright/README.md`には、同一ポート、同一SQLite、ブラウザ状態を共有するためworkers=1が必要だと記載されている。独立DB・独立サーバー・状態初期化が整うまでは、CIでも並列化しない。

なお、同READMEに「GitHub Actionsのworkflowはまだ実装していません」という古い記述が残っているが、現在の正本である`.github/workflows/ci.yml`ではPlaywright jobが実装済みである。実行方法の詳細は既存READMEを参照し、この戦略では責務と保証範囲を正本とする。

## 7. 非保証範囲と将来候補

以下は現在の欠陥と断定するものではなく、通常系中心の現行suiteが保証していない範囲である。

| 項目 | 現在の扱い | 将来の候補 |
| --- | --- | --- |
| 破損・暗号化・本文抽出不能・巨大・空PDF | 通常系のPDF統合テスト外 | Should相当の異常PDF fixtureとエラー契約を先に定義 |
| 不正メタデータ | 一部のfallbackは検証するが網羅しない | Pythonのmetadata/PDF統合テスト |
| 登録後のファイル削除・移動・改名 | indexerの通常削除・タイトル系以外は限定的 | DB・ファイル同期の契約テスト |
| 複数操作の競合 | client/server保存競合の一部をPlaywrightで検証 | DB同時書き込み、複数タブ、失敗応答の体系化 |
| SQLite同時書き込み | 単一接続・通常保存中心 | 負荷・ロック・復旧方針が決まった後に統合テスト |
| Windows・WSL・macOS差 | CIのUbuntuのみ | 環境差が実利用上問題になった時の代表環境検証 |
| 性能・負荷 | 数値基準なし | 100/1,000冊規模の測定後に基準化（ROADMAP Should） |
| 視覚的デザイン品質 | 主要要素の存在・文言・操作性のみ | UI設計基盤と利用者検証（ROADMAP Should） |
| アクセシビリティ | focus trap等の一部のみ | キーボード、role、screen reader、axe等の方針決定後 |
| セキュリティ | 実装上の境界対策はあるが体系的回帰なし | path traversal、ZIP Slip、XSS、資源枯渇、SQLite競合の脅威分析と回帰（ROADMAP Should） |

異常系をすべて直ちに自動化するのではなく、利用者影響、再発可能性、実装されたエラー契約が明確になった順に追加する。現時点のMustはテスト戦略とCIの運用基盤であり、性能・セキュリティ・実利用者検証はROADMAPのShould、認証やリリース管理は将来検討に置かれている。

## 8. テストデータと隔離方針

Pythonテストは原則として`TemporaryDirectory`内のDB・ファイルを使い、接続を初期化して各テストの状態を作る。PDF処理ではテスト内生成PDFまたはリポジトリ同梱のサンプルPDFを使う。実DB、個人BOOKS_DIR、Scrapbox/Cosense実データ、Kindle個人情報、`.env`を変更しない。

Playwrightはスクリプトが用意する一時BOOKS_DIR・DB_DIRと同梱3PDFだけを使う。testごとのpack作成、route mock、ページコンテキストを使い、テスト間のデータを共有しない。現在はサーバー、SQLite、127.0.0.1:8003、ブラウザ状態の共有があるためworkers=1で実行する。固定IDに依存するmockでは項目のclientIdや位置も確認し、同一PDFをpathだけで同一視しない。

全実行はtrapでサーバー停止と一時ディレクトリ削除を行う。テストが本番・個人データへ書き込まないことは、隔離パスを環境変数で明示することと、実蔵書全体をコピーしないことの両方で守る。

## 9. 実行方法

### Python

依存を導入したPython環境で次を実行する。

```sh
python -m unittest discover -s tests
```

CIは`pip install -e .`後に同じコマンドを実行する。個別確認は`python -m unittest tests.test_database.PackTest`のようにmodule/classを指定する。DB・PDF・CLIの変更はまず該当moduleを絞って確認し、PR前または構造変更時は全件を実行する。

### Playwright

初回のみ、Python依存、Node依存、Chromiumを導入する。

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install -e .
npm ci
npx playwright install --with-deps chromium
```

通常の全件実行は次である。

```sh
npm run test:ui
```

必要なspecだけなら、例えば次のように指定する。

```sh
npm run test:ui -- tests/playwright/workspace_add_pdf.spec.js
```

ローカルとCIは同じスクリプトを使うが、CIはGitHub reporter、Ubuntu標準runner、fresh checkoutを使う。実装が単一モジュールの純粋ロジックに限られる場合は対象Pythonテストだけでよい。HTTP、PDF、DB、UIの境界を変える場合、または構造改善ではPython・Playwright全件をPR前に確認する。

詳細な隔離方法、workers=1の理由、失敗時artifactは`tests/playwright/README.md`と`.github/workflows/ci.yml`を参照する。

## 10. 構造改善時の適用

`docs/central-file-refactoring-inventory.md`の方針どおり、行数削減ではなく責務・変更理由・依存方向を整理し、外部URL、HTTP API、DBスキーマ、画面表示を変えずに分割する。

### `web.py`の責務分離

routeのHTTP入力・status・responseはAPIテストで固定する。検索結果整形、パス解決、エクスポート計画などを新モジュールへ移すときは、既存の`test_web.py`純粋ロジックテストを最小の単体テストへ移し、routeテストはHTTP契約に絞る。PDFパス境界や保存副作用を動かす段階ではファイル統合テストを追加確認し、主要画面導線に影響があればPlaywrightを実行する。

### `database.py`の責務分離

スキーマ初期化・移行、books/search/packs/export eventsの単位を、既存のDBテストとトランザクション境界を保ったまま分ける。公開関数の互換ラッパーを残し、SQL・commit・position・IDの契約をDBテストで固定する。スキーマSQLを変える段階はデータ保全方針が先であり、単なるファイル移動ならGit差分と全件回帰で可逆性を確認する。

### フロントエンドの責務分離

API通信、`pack-store.js`の状態管理、DOM描画、PDFモーダルを分ける場合、clientId・server id、キャッシュ、サーバー応答マージの契約を既存のPlaywright identityテストで維持する。ページ指定の純粋変換は低い層へ置き、主要導線はworkspace/search/pdf/exportのPlaywrightで確認する。表示だけの変更は影響するlocator・文言テストを最小更新し、ピクセル比較を導入したことにはしない。

各段階で一つのPRに一つの責務移動だけを含め、既存テストを先に維持したまま実装を変える。段階ごとにPython 371件とPlaywright 29件を全件実行し、CIが緑であることを次の責務移動へ進む条件とする。

## 11. CI運用とROADMAPとの関係

Pythonテストは技術的には`.github/workflows/ci.yml`へ導入済みで、pull requestとpushで常時実行され、直近CIで371件成功している。Playwrightも29件成功している。

一方、ROADMAPの「CIでのPythonテスト常時実行」は、CI jobが存在することだけでなく「失敗状態ではマージしない」「required status checksやbranch protectionをどうするか」を完了条件にしている。個人開発の現状では、GitHub設定の必須化をこの文書だけで勝手に決めず、運用判断が残るため未完了とする。CI導入済みという事実と、マージ運用の未確定をROADMAPに併記する。

「CIで失敗した状態ではマージしない」という運用方針は、テスト戦略の実行手順として本書に記録する。required checkの設定、develop/masterの保護、PRレビューの必須化は、ROADMAPの「ブランチ運用とPRの定着」で決める。両項目の責務を重複させず、前者はテスト実行基盤、後者はGitHub上の承認・マージ運用を扱う。
