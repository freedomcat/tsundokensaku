# Phase 3C / 3D 実装前設計監査

作成: 2026-07-11
状態: レビュー結果（実装前）
前提: [ai-export-optimization-design.md](ai-export-optimization-design.md) / Phase 3A・3B 実装済みコード

> **位置付け**: 本文書は Phase 3C / 3D の設計監査と判断経緯であり、現行仕様の正本ではない。監査結果を反映した現行仕様は [ai-export-optimization-design.md](ai-export-optimization-design.md) と該当する個別設計書を参照。

## 1. 監査の範囲

Phase 3B までの実装（`export_profiles.py` / `export_stats.py` / `token_estimate.py` / `web.py` のエクスポート経路）と設計書を照合し、Phase 3C（ChatProfile）・3D（NotebookLMProfile）を現在の基盤に載せたときに壊れる箇所・設計書に不足している決定を洗い出した。

## 2. Phase 3B 実装が置いた前提（3C/3D に影響するもの）

1. `ExportProfile.plan()` は基底 1 実装。`chunk_limit()` が None なら 1 項目 = 1 チャンク、整数なら「重み合計が上限内 かつ can_merge」で貪欲結合
2. `web._export_pack_archive` は **`_placeholder_item_stats_for_export`（ページ数 0・文字数 0 のダミー統計）** を plan() に渡している。standard は重みを使わないため成立しているが、**実統計が必要なプロファイルでは使えない**
3. 項目のページ未指定チェック（400）は **チャンクループ内で `chunk.items[0]` のみ**を見ている。1 項目 = 1 チャンクの standard では全項目を網羅するが、複数項目チャンクでは 2 番目以降の検証が抜ける
4. `RenderContext` は `pack_name / exported_at / format / resolve_pdf / render_pdf / render_markdown` を持つ。**分冊総数（n/全m 表記に必要）を持たない**
5. `format` パラメータは `Query("pdf")` がデフォルト。**`profile=chat` を format 省略で叩くと、デフォルトの "pdf" が primary_format="md" と矛盾して 400 になる**
6. manifest は `render_pack_manifest`（`PackExportEntry` = 1 項目 = 1 エントリ前提）のみ。複数項目チャンクの項目内訳を表現する手段がない
7. `plan()` の警告（`item_exceeds_limit` 等）はプレビューでは返せるが、**エクスポート実行のレスポンスには載る場所がない**

## 3. Phase 3C: ChatProfile レビュー

### 3.1 修正が必要な問題（ブロッカー）

#### (a) format デフォルトの矛盾 — API 設計の修正が必要

`GET /api/packs/{id}/export?profile=chat`（format 省略）が現実装では 400 になる。`Query("pdf")` のデフォルトが「省略」と「明示的な pdf」を区別できないため。

**推奨修正**: `format: str | None = Query(None)` に変更し、次の順で解決する。

```
1. format 省略時: profile.primary_format があればそれ、なければ "pdf"（現行既定）
2. format 明示時: 従来どおり検証し、primary_format と矛盾すれば 400
```

profile 未指定 + format 省略 → "pdf" は維持されるため後方互換。設計書 12.2 に「format 省略時は profile の主形式を既定とする」を明記する。

#### (b) プレースホルダ統計では chat の分割ができない

chat の `item_weight` は推定トークン数であり、`collect_item_stats`（DB 読み）による実統計が必須。一方で `collect_item_stats` は寛容（不正 spec・欠損 PDF を空扱い）なので、そのまま使うと現行の厳格な 400/404 エラーメッセージが失われる。

**推奨修正**: `_export_pack_archive` を次の構造にする。

```
1. 全項目の事前検証ループ（plan の前）:
   - pages 空 → 400 "{title}: ページを指定してください"（現行文言）
   ※ PDF 欠損 404・不正 spec 400 は従来どおり render 時（render_pdf_export 等）に発生させる
2. 統計収集:
   - profile が実統計を要するか（chunk_limit() is not None で判定できる）
     に応じて collect_item_stats / プレースホルダを使い分ける
3. plan → chunk ごとに render_chunk
```

事前検証を plan の前の全項目ループへ移すことで、§2-3 の「チャンク先頭しか検証しない」構造も同時に解消する。standard の出力・エラー応答は不変（検証位置が変わるだけで、position 順に最初のページ未指定項目が 400 になる順序性は同じ）。

#### (c) 分冊総数が render_chunk に届かない

chat の各ファイル冒頭に「分冊 2/4」を書くには、チャンク描画時に総チャンク数が必要。`ExportChunk` は自分の index しか知らない。

**推奨修正**: `RenderContext` に `total_chunks: int` を追加する（web.py が plan 確定後に組み立てる。プロファイルへの plan 全体の受け渡しよりも注入点が小さい）。

#### (d) manifest の拡張ポイント不足

`PackExportEntry(index, title, page_label, filename, content)` は 1 項目 = 1 エントリ前提。chat のチャンク（複数項目）を 1 エントリにすると、title / page_label が単一項目表現になり項目内訳が消える。

**推奨修正**: profile 指定エクスポート用に **`ExportPlan` から manifest を組み立てる新レンダラ**を追加する（`zip_export.render_plan_manifest(pack_name, exported_at, plan, filenames, warnings)` のような形。チャンク → 収録項目（書名・ページ範囲）の階層で出力し、`manifest_header_lines()` の内容と plan 警告も記載する）。standard は現行 `render_pack_manifest` をそのまま使い続けてバイト互換を守る。

#### (e) plan 警告の出力先

`item_exceeds_limit` は「警告して単独出力」する設計だが、エクスポート実行(200)のレスポンスに警告を載せるヘッダ等はない。

**推奨**: 警告は (1) プレビュー API（実行前に見える）と (2) manifest.md（成果物に残る）の 2 箇所に出す。エクスポート実行のレスポンス形式は変えない。設計書 14 章にこの方針を追記する。

### 3.2 設計判断の確認（問題なし・明文化のみ）

- **ChatProfile と StandardProfile の差分**: `name="chat"` / `primary_format="md"` / `item_weight=estimator()(stats.stats)` / `chunk_limit()=CHAT_TOKEN_LIMIT_DEFAULT(80,000)` / `can_merge=常にTrue（基底のまま）` / `chunk_filename={資料名}_chat_{NN}.md` / `archive_filename=基底実装（{資料名}_chat_{YYYYMMDD}.zip）がそのまま使える` / `render_chunk=MD連結`
- **Markdown 連結の構造**: 項目単位の本文は既存 `render_markdown_export`（`ctx.render_markdown`）の出力をそのまま再利用する。各項目の MD には既に書名・元 PDF 名・ページ範囲・抽出日のヘッダ（`# {書名}（抜粋）` ほか）が含まれるため、**チャンク側で追加するのは「資料名・分冊 n/全m・収録項目一覧」のチャンクヘッダのみ**。ヘッダ生成は `markdown_export.render_chat_chunk_header()` として追加し、項目間は `---` 区切りで連結する。既存ロジックのコピーは発生しない
- **同一 PDF の複数項目**: 独立項目としてチャンクに入る（隣接し上限内なら同一ファイル、それ以外は別ファイル）。各項目が自分のヘッダを持つため出典は失われない
- **1 項目でトークン上限超過**: 基底 plan() が既に単独チャンク化 + `item_exceeds_limit` 警告を実装済み（B-1 でテスト済み）。追加実装不要
- **空本文・OCR ノイズ・日英混在**: 空ページは `render_markdown_pages` の既存注記（「このページから抽出できたテキストはありません」）がそのまま出る。OCR ノイズはそのまま流す（浄化は対象外）。日英混在はトークン概算の係数設計（8.1 節）で吸収済み。特別処理なし
- **manifest との情報重複**: 各 MD ファイルは単体で出典が自足し、manifest は ZIP 全体の一覧性を担う。重複は意図的（役割が違う）と明文化する
- **ファイル名と 255 バイト**: `{資料名}_chat_{NN}.md` は資料名が長い場合に超過し得る。`build_entry_filename` は項目ベース（書名+ページ範囲）で流用できないため、`zip_export` に連番ベースの小さなヘルパ（`build_sequenced_filename(base_name, profile_name, index, ext)`、同じ 255 バイト切り詰め方針）を追加する
- **profile=chat と format**: (a) の修正後、`profile=chat`（format 省略）→ md、`profile=chat&format=md` → md、`profile=chat&format=pdf|json` → 400。json は chat の対象外（B-3 で導入済みの primary_format 検証がそのまま弾く）

### 3.3 既知の制約として設計書へ明記するもの

- **未インデックスページの過小評価**: chat の重みは DB のテキスト統計に基づくため、未インデックスページ分は 0 トークン扱いになり、実際には上限を超える分冊ができ得る。v1 では許容し、プレビューの `unindexed_pages` 警告で利用者に補足する（エクスポート時にその場抽出はしない — 8.3 節の方針維持）
- **チャンクヘッダ・項目ヘッダ自体のトークン消費**: 分割判定は本文統計のみで行い、ヘッダ分（数百トークン）は誤差として扱う

### 3.4 Phase 3C 実装分割案（レビュー・コミット可能な単位）

| # | ステップ | 変更対象 | テスト | 出力への影響 |
|---|---|---|---|---|
| C-0 | 実行経路の下準備: format=None デフォルト解決（§3.1a）+ 項目事前検証の plan 前移動（§3.1b の 1）| `web.py` | 既存テスト無修正全通過 + format 省略の互換テスト | なし（回帰確認が主目的）|
| C-1 | ChatProfile の plan 系（定数・item_weight・chunk_limit・命名）+ 連番ファイル名ヘルパ | `export_profiles.py`, `zip_export.py` | `test_export_profiles.py` / `test_zip_export.py` 追記 | なし（未配線）|
| C-2 | chat チャンク描画（チャンクヘッダレンダラ + render_chunk + RenderContext.total_chunks）| `markdown_export.py`, `export_profiles.py` | `test_markdown_export.py` / `test_export_profiles.py` 追記（フェイク注入）| なし（未配線）|
| C-3 | export API 配線（実統計分岐 + plan 由来 manifest + PROFILES 登録）| `web.py`, `zip_export.py` | `test_web.py` 追記（分冊 ZIP・警告 manifest・重複項目・超過項目）| profile=chat が有効になる。standard は不変 |
| C-4 | preview API の profile パラメータ + 分割予定・警告（設計書 12.1 の後方互換注記どおり、省略時 standard 相当）| `web.py` | `test_web.py` 追記 | preview の応答に chunks が増える（省略時互換）|
| C-5 | UI: エクスポートモーダル（宛先選択・概算・分割予定・警告。3A の常時表示は 1 行要約に縮小）| `workspace.html` | 手動確認（Playwright）| UI のみ |
| C-6 | エクスポートイベント記録（`export_events` テーブル + 成功時 INSERT。仕様: [export-events-design.md](export-events-design.md)）| `database.py`, `web.py` | `test_database.py` / `test_web.py` 追記 | なし（既存動作不変・ベストエフォート記録）|

C-0 が独立した下準備コミットである点が設計書 19 章（C-1〜C-5）からの変更。B-2/B-3 の実装を踏まえて必要になった。C-6 は本監査の未決事項だったが、[export-events-design.md](export-events-design.md) で「3C 内の独立ステップ」として確定した（依存は C-0 のみ。C-3 と並行可）。

## 4. Phase 3D: NotebookLMProfile レビュー

> **2026-07-12 追記**: 本節のレビューは「隣接する同一 PDF 項目の結合によるソース数削減」を主目的とする旧 Phase 3D 設計に対するもの。その後の設計改訂で、Phase 3D の主目的は「上限を超える資料項目の章単位分割（アウトラインがなければページブロック分割）」へ変更され、結合は補助的な最適化に位置づけ直された（実装ステップも D-0〜D-3 へ再構成）。現行仕様は [ai-export-optimization-design.md](ai-export-optimization-design.md) §7.3 / §9.3 / §19 を参照。本節の個別の決定（閾値の環境変数化 §4.1a・重複ページ非去重 §4.1b・manifest の階層表示 §4.1c・結合の実装方式 §4.2）は改訂後も有効。

### 4.1 修正・決定が必要な問題

#### (a) ソース数警告のハードコード禁止 — 設定可能な閾値へ

NotebookLM のソース数上限は契約で異なり（無料 ~50 / 有料プラン ~300 前後）、外部サービス都合で変わる。設計書 7.3 の「`NOTEBOOKLM_MAX_SOURCES`（初期値 50）」を絶対値として埋め込まない。

**推奨**: モジュール定数をデフォルトとし、環境変数で上書き可能にする（既存の `PDF_EXPORT_SAVE_DIR` と同じ流儀）。

```
TSUNDOKENSAKU_NOTEBOOKLM_MAX_SOURCES      （既定 50。警告閾値であり、出力は止めない）
TSUNDOKENSAKU_NOTEBOOKLM_MAX_PAGES_PER_FILE（既定 300。分割の上限）
```

**実装上の注意**: `PROFILES` はモジュールロード時に生成されるシングルトンのため、環境変数を `__init__` で読むと値が固定化される。`chunk_limit()` / `extra_warnings()` の呼び出し時に都度読む（テスト・設定変更が再起動なしで効く）。

#### (b) 重複ページの扱い — 決定が必要

隣接する同一 PDF 項目（例: p.1-10 と p.5-8）を結合すると、p.5-8 が 2 回入る。

**推奨: 重複を保持する（去重しない）**。理由: 資料項目は「別々の文脈で使う引用単位」であり（pack-item-identity-design.md の設計思想）、勝手にまとめると項目境界と出典追跡が壊れる。manifest に各項目の範囲が残るため、重複は利用者が資料構成として意図したものとして扱う。設計書 9.1 に追記する。

#### (c) manifest からの項目境界追跡

結合 PDF（例: `03_本A_p1-10_5-8.pdf`）に 2 項目入っている場合、どのページ範囲がどの項目かを manifest が示す必要がある。§3.1(d) の plan 由来 manifest レンダラをチャンク → 項目の階層表示にすることで解決する（3C と共通basis。3C 側 C-3 で導入し、3D はそれを使う）。

#### (d) ページ上限を分割基準にすることの妥当性

NotebookLM の実制限は「1 ソース 50 万語 / 200MB」であり、ページ数ではない。

**評価**: ページ重みは v1 として妥当（PDF を開かずに決まる・利用者が直感的に把握できる）。ただし補助として、`collect_item_stats` の文字数統計は既に取れるため、**チャンク推定文字数が語数上限の目安（例: 40 万字）を超える場合の警告**を `extra_warnings()` に追加するのは低コストで価値がある（分割基準は変えず、警告のみ）。ファイルサイズ（200MB）は描画後にしか分からないため v1 では扱わず、未決事項とする。

### 4.2 設計判断の確認（問題なし・明文化のみ）

- **隣接同一 PDF の結合方針**: 基底 plan() の `fits_limit and can_merge` で実現できる。`can_merge` を「`current.items[-1].item.pdf_path == stats.item.pdf_path`」に上書きすれば、異なる書籍は結合せず（ソースの独立性維持）、同一書籍の隣接項目だけが上限内で結合される。設計書 9.2 の疑似コードと基底実装は「上限超過なら同一書籍でも分割する」点で一致しており矛盾はない
- **項目順序とページ順**: 結合 PDF 内の順序は「項目の position 順に、各項目の spec 記載順」。spec は "8,1-3" のような列挙順を保持するため、結合後のページは昇順にならないことがある。**項目の並び = 利用者が意図した提示順**であり、昇順ソートはしない（設計書 9.1「並び順を維持」の帰結として明記）
- **PDF メタデータ**: チャンクは単一書籍由来なので、結合時に先頭項目の描画結果（`render_selected_pages` が元 PDF のメタデータを引き継ぎ済み）からメタデータをコピーする
- **結合の実装方式**: `RenderContext` は変更せず、**各項目を `ctx.render_pdf` で描画してから pypdf で連結**する（`PdfWriter` に各 `PdfReader(BytesIO(...))` のページを順に追加）。spec 解析・範囲検証・404/400 の発生点が standard と完全に同じになり、エラー互換が自動的に保たれる
- **エントリ名**: `{NN}_{書名}_p{範囲1}_{範囲2}.pdf`。各項目の pages spec を `_` 連結した文字列を既存 `build_entry_filename` の page_spec 引数に渡せば、255 バイト超過時の「Nページ表記」短縮も既存ロジックがそのまま効く
- **UI 表示**: プレビューモーダル（C-5）に「出力予定ファイル数 n / ソース数目安 m」と警告一覧を出す。3D 固有の新 UI は不要（宛先ラジオに NotebookLM を足すだけ）
- **テスト用 PDF**: B-2 で確立した「ページ高さ = 100 + ページ番号」方式で、結合後 PDF のページ出所（どの項目のどのページか）を内容レベルで検証できる。メタデータは `PdfWriter.add_metadata` で作成して引き継ぎを検証する

### 4.3 Phase 3D 実装分割案

| # | ステップ | 変更対象 | テスト | 依存 |
|---|---|---|---|---|
| D-1 | NotebookLMProfile の plan 系（ページ重み・env 可変上限・can_merge・ソース数/文字数警告） | `export_profiles.py` | `test_export_profiles.py` 追記（結合・分割・警告・env 上書き） | C-1 |
| D-2 | PDF 結合描画（render_pdf 出力の pypdf 連結 + メタデータ維持 + エントリ名） | `export_profiles.py`（+必要なら `pdf_export.py` に結合ヘルパ） | `test_export_profiles.py` / `test_export_pdf_pages.py` 追記 | D-1 |
| D-3 | API/preview/UI への notebooklm 追加（C-3/C-4/C-5 の拡張。manifest は C-3 の plan 由来レンダラを共用） | `web.py`, `workspace.html` | `test_web.py` 追記 + 手動確認 | C-3〜C-5, D-2 |

## 5. Phase 3C 開始前に設計書へ反映すべき事項（まとめ）

1. §12.2: format 省略時の解決規則（profile の primary_format を既定とする。profile 未指定は従来どおり pdf）
2. §13.2: `RenderContext` に `total_chunks` を追加
3. §13.3 / §10: profile エクスポートの manifest は `ExportPlan` から組み立てる新レンダラを使う（standard は現行維持）。チャンク → 項目の階層で項目境界と警告を記載
4. §14: plan 警告の出力先はプレビュー API + manifest.md の 2 箇所。実行レスポンスは変えない
5. §7.3: NotebookLM の上限値は環境変数で上書き可能なデフォルトとする（ハードコード禁止）。値の読み取りは呼び出し時
6. §9.1: 重複ページは去重しない。結合 PDF 内の順序は項目 position 順 + spec 記載順（昇順ソートしない）
7. §8.3 / §7.4: chat の未インデックスページ過小評価は既知の制約（プレビュー警告で補足）
8. §19: C-0（下準備ステップ）の追加。C-1〜C-5 の内容更新（本文書 §3.4）

## 6. 未決事項

（本監査後の確定: §5 の反映事項は設計書 [ai-export-optimization-design.md](ai-export-optimization-design.md) へ反映済み。旧 4「manifest.json 同梱」は初期実装では不要と確定 — export_events が機械可読スナップショットを担うため（設計書 §20-9）。旧 5「イベント記録の時期」は 3C 内の独立ステップ C-6 として確定 — [export-events-design.md](export-events-design.md)）

1. NotebookLM のファイルサイズ上限（200MB）への対応（描画後にしか分からない。v1 は対象外）
2. chat の分冊内文字数警告（語数上限相当）の要否 — notebooklm 側にだけ入れるか、chat にも入れるか
3. `TSUNDOKENSAKU_NOTEBOOKLM_MAX_PAGES_PER_FILE` の既定値 300 の妥当性（3E の実測で調整）
