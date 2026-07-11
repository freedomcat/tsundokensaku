# AI向けエクスポート最適化 設計書（Phase 3）

作成: 2026-07-11 / 最終更新: 2026-07-12
状態: Phase 3A・3B 実装済み。Phase 3C 実装前監査（[phase3c-3d-design-review.md](phase3c-3d-design-review.md)）の確定事項を反映済み
前提: [ROADMAP.md](../ROADMAP.md) Phase 3 / [docs/pack-design.md](pack-design.md) / [docs/pack-item-identity-design.md](pack-item-identity-design.md)

## 1. 背景

つんどけんさくの基本の流れは「検索 → 資料へ追加 → 並び替え → エクスポート」である。Phase 2 までで、資料（パック）を SQLite に永続化し、資料項目（ページ範囲）単位で並び替え、PDF / Markdown / JSON を ZIP 一括でエクスポートできるようになった。同一 PDF を複数の資料項目として扱う機能も実装済みである。

一方、現在のエクスポートは宛先を意識しない汎用出力であり、AI サービスへ渡す際に以下の課題がある。

- NotebookLM にはソース数・1 ソースあたりの分量に上限があるが、出力ファイル数・分量を事前に把握できない
- ChatGPT / Claude にはコンテキスト量の上限があるが、資料が何トークン相当なのか分からず「渡しすぎ / 足りない」が起きる
- 上限を超えた場合の分割を利用者が手作業（資料の作り直し）で行う必要がある

## 2. 目的

- 宛先（NotebookLM / ChatGPT・Claude）ごとに適した形式・分割・命名でエクスポートできる「エクスポートプロファイル」を導入する
- エクスポート前に書籍数・ページ数・推定トークン数・出力ファイル数などの「トークンバジェット」を表示する
- 上限を超える資料を、資料項目を壊さずに自動分割する

つんどけんさく自体には AI 要約・RAG・ベクトル検索は内蔵しない。本機能は外部 AI サービスへ渡す資料の整形・分割に徹する。

## 3. 対象範囲

- 資料（パック）単位のエクスポート（`GET /api/packs/{pack_id}/export`）へのプロファイル追加
- エクスポート前の集計・概算を返すプレビュー API の新設
- 資料棚（/workspace）のエクスポート UI の最小限の拡張
- 自動分割ロジックとファイル命名規則
- トークン概算ロジック（依存ライブラリを増やさない近似式）

## 4. 対象外

- AI 要約・RAG・ベクトル検索の内蔵（ROADMAP「やらないこと」）
- 外部 API（AI サービス、トークナイザー API 等）の呼び出し
- モデル別の正確なトークナイザー導入（将来差し替え可能な構造のみ用意する）
- 単体 PDF エクスポート（`/export-pdf`, `/export-md`, `/export-pdf/save`）の変更
- Phase 4（AI 成果物の帰還）に関わる機能
- Kindle 本・メモの資料項目化（現状パックは PDF のみ。従来どおり）

## 5. 現行実装の調査結果

### 5.1 PDF エクスポート処理

- `src/tsundokensaku/pdf_export.py`
  - `parse_page_selection(spec, page_count)`: `"3-7,20"` 形式の spec 文字列をページ番号リストへ展開。範囲外・空は `ValueError`
  - `compact_page_selection(page_numbers)`: ページ番号リストを `"3-7_20"` 形式へ圧縮（ファイル名用）
  - `render_selected_pages(input_pdf, page_numbers)`: pypdf で選択ページのみの PDF バイト列を生成（メタデータ引き継ぎ）
  - `default_output_path`: `{元PDF stem}_p{選択}.pdf`
- `src/tsundokensaku/web.py`
  - `render_pdf_export(candidate, pages)` (web.py:750): spec 検証＋レンダリング。**PdfReader をページ数取得とレンダリングで 2 回開いている**
  - エンドポイント: `GET /export-pdf`（単体）、`POST /export-pdf/save`（設定フォルダへ保存）

### 5.2 Markdown エクスポート処理

- `src/tsundokensaku/markdown_export.py`
  - `render_markdown_pages(...)`: タイトル・出典・元ファイル・ページ・抽出日のヘッダ + `## p.N` ごとの本文。テキストなしページは注記
- `src/tsundokensaku/web.py`
  - `load_pages_text(candidate, page_numbers, ...)` (web.py:806): 本文はまず DB の `pages` テーブルから取得し、欠けたページのみ `pdf_extract.extract_pages` でその場抽出
  - `render_markdown_export(...)` (web.py:878): spec 検証 → タイトル解決（DB の book.title、なければ stem）→ 本文取得 → レンダリング
  - エンドポイント: `GET /export-md`（単体）

### 5.3 ZIP 一括エクスポート処理

- エンドポイント: `GET /api/packs/{pack_id}/export?format=pdf|md|json` (web.py:1210)
  - `format=json`: 資料構成（version 3 の items）を JSON 1 ファイルで返す（ZIP ではない）
  - `format=pdf|md`: 資料項目を position 順にループし、項目ごとに `render_pdf_export` / `render_markdown_export` を呼んで `PackExportEntry` を作り、ZIP にまとめる
  - 空資料は 400、ページ未指定項目があれば 400、PDF 実体がなければ 404（`_resolve_pdf_file_or_404`）
- `src/tsundokensaku/zip_export.py`
  - `build_entry_filename(index, title, page_spec, ext)`: `{NN}_{書名}_p{範囲}.{ext}`。255 バイト超過時は「ページ範囲→Nページ表記」→「書名を…で切り詰め」の順で短縮
  - `render_pack_manifest(...)`: `manifest.md`（収録一覧 + NotebookLM 向け注記）
  - `build_pack_zip(...)`: manifest.md + エントリ順の ZIP
  - `build_pack_zip_filename`: `{資料名}_{YYYYMMDD}.zip`
  - `sanitize_filename_component`: `[^\w.-]+` → `_`、空なら `untitled`

### 5.4 データ構造（資料・資料項目・ページ範囲・並び順）

- `database.py` の `PackRecord` / `PackItemRecord` (database.py:67-87)
  - `pack_items(id, pack_id, pdf_path, title, pages, collapsed, position, added_at, updated_at)`
  - `pages` は spec 文字列（クライアント `pages-spec.js` とサーバ `parse_page_selection` が同じ文法）
  - `title` は追加時点のスナップショット（出典の再現性）
  - 並び順は `position`（`ORDER BY position, id`）。保存時に 0 からの連番へ正規化
- 本文テキストは `pages(book_id, page_number, text)` テーブルにインデックス済み全ページ分が保存されている（FTS とは別に原文を保持）→ **トークン概算の文字数はここから取得できる**

### 5.5 同一 PDF の複数資料項目

- `UNIQUE(pack_id, pdf_path)` は撤廃済み。項目の識別子は `pack_items.id`（[pack-item-identity-design.md](pack-item-identity-design.md)）
- API 形式は `version: 3` の `items` 配列。エクスポートは position 順に項目単位で処理し、同一 PDF 由来でも別エントリ（連番付き）で出力される（test_web.py `test_pack_api_export_zip_supports_duplicate_items` で保証）

### 5.6 エクスポート関連の API・サービス・テンプレート・JS

| 種別 | 場所 |
|---|---|
| API | `GET /api/packs/{id}/export`, `GET /export-pdf`, `GET /export-md`, `POST /export-pdf/save`, `POST /api/packs/import` |
| サービス | `pdf_export.py`, `markdown_export.py`, `zip_export.py`, web.py 内の `render_*_export` / `load_pages_text` |
| テンプレート | `templates/workspace.html`（エクスポートボタン 3 つ + `exportPackZip()` JS を同居） |
| JS | `static/pack-store.js`（資料の同期ストア）、`static/pages-spec.js`（spec 文法のクライアント実装）、`static/pdf-modal.js`（単体切り出し） |

UI は資料棚ツールバーの「PDF一式を書き出す」「MD一式を書き出す」「資料データを書き出す（JSON）」の 3 ボタン。`exportPackZip(format)` が spec の構文検証 → `flushPendingSave()` → fetch → Blob ダウンロードを行う。

### 5.7 テスト構成

- `tests/test_export_pdf_pages.py`: spec 解析・圧縮・出力パス・ページ抽出
- `tests/test_markdown_export.py`: ヘッダ・ページ見出し・空ページ注記
- `tests/test_zip_export.py`: サニタイズ・ZIP 名・エントリ名（255 バイト短縮含む）・manifest・ZIP 構造
- `tests/test_web.py`: エクスポート API（順序・重複項目・空資料 400・欠損 404・不正 spec 400・Markdown 形式・単体エクスポート）
- `tests/playwright/`: E2E（workers=1 制約あり）。エクスポートの E2E は未整備
- 実行: `make test`（Docker、`--entrypoint` 上書きが必要な既知事情あり）

### 5.8 エクスポート処理で重複している責務

1. **spec のページ数カウント**が 3 実装ある: `pdf_export.parse_page_selection`（検証込み展開）、`zip_export._count_pages_in_spec`（検証なし概算）、`pages-spec.js`（クライアント）
2. **ファイル名サニタイズ**が 2 実装: `pdf_export.py` / `markdown_export.py` の `re.sub(r"[^\w.-]+", "_", ...)` と `zip_export.sanitize_filename_component`
3. **PdfReader の二重オープン**: `render_pdf_export` がページ数取得とレンダリングで同じ PDF を 2 回開く
4. **項目ループ＋エントリ生成**が web.py の `api_export_pack` に直書き（プロファイル分岐を足すと肥大化する）

Phase 3 では 1（サーバ側）と 4 を整理する。2・3 は挙動を変えないため今回は温存し、必要になったら別リファクタとする。

### 5.9 プロファイル化で変更が必要な箇所

- `web.py api_export_pack`: 項目ループを分割プラン駆動へ（プロファイルなし時は現行ロジック維持）
- `zip_export.py`: ZIP 名・エントリ名のプロファイル対応（既存関数はそのまま残す）
- 新規モジュール: トークン概算、集計、プロファイル定義＋分割プラン
- `workspace.html`: プロファイル選択＋プレビュー表示の UI
- `markdown_export.py`: 複数項目を 1 ファイルへ連結するレンダラ（chat 用）
- pypdf の複数 PDF 結合（notebooklm 用。`PdfWriter` に複数 reader のページを足すだけで新規依存なし）

### 5.10 後方互換性を維持するための条件

- `GET /api/packs/{id}/export` の `format` パラメータ（pdf/md/json）と、**profile 未指定時の挙動**（ZIP 構造・manifest.md・エントリ名 `{NN}_{書名}_p{範囲}.{ext}`・ZIP 名 `{資料名}_{YYYYMMDD}.zip`・並び順・重複項目・エラー応答）を変えない
- `/export-pdf`, `/export-md`, `/export-pdf/save` を変えない
- 既存テスト（5.7）を修正なしで通す
- 空資料 400 / ページ未指定 400 / PDF 欠損 404 の挙動を profile 未指定時に維持する

## 6. ユースケース

（`~/.claude/CLAUDE.md` のテンプレートに準拠）

---
ユースケース：資料を NotebookLM へ渡す形式でエクスポートする

概要：
・組み立て済みの資料を、資料棚から NotebookLM のソースとして読み込ませやすい PDF 群としてエクスポートする

アクター：
・利用者（つんどけんさくのシングルユーザー）

事前条件：
・資料に 1 件以上の資料項目があり、全項目にページ範囲が指定されている

事後条件：
・PDF（複数の場合は ZIP）がダウンロードされ、各ファイル名と manifest から出典（書名・ページ範囲）が分かる

基本系列：
1. 利用者は、資料棚で「AI向けに書き出す」を押す
2. システムは、プロファイル選択と概算（書籍数・ページ数・推定トークン数・出力ファイル数・分割予定・警告）を表示する
3. 利用者は、「NotebookLM」を選び「書き出す」を押す
4. システムは、分割プランに従って PDF 群を生成し、ZIP をダウンロードさせる

代替系列：
1. 1 項目だけで上限を超える項目がある場合、システムは警告を表示した上で、その項目を単独ファイルとして出力する
---

---
ユースケース：資料を ChatGPT / Claude へ渡す形式でエクスポートする

概要：
・組み立て済みの資料を、推定トークン数を基準に分割された Markdown 群としてエクスポートする

アクター：
・利用者

事前条件：
・資料に 1 件以上の資料項目があり、全項目にページ範囲が指定されている

事後条件：
・連番付き Markdown（複数の場合は ZIP）がダウンロードされ、各ファイル冒頭に資料名・書名・元 PDF・ページ範囲が記載されている

基本系列：
1. 利用者は、資料棚で「AI向けに書き出す」を押す
2. システムは、プロファイル選択と概算を表示する
3. 利用者は、「ChatGPT / Claude」を選び「書き出す」を押す
4. システムは、トークンバジェットに従って項目単位で分割した Markdown 群を生成し、ZIP をダウンロードさせる

代替系列：
1. 全体が 1 ファイルに収まる場合、システムは分割せず 1 ファイル入りの ZIP を出力する
---

## 7. エクスポートプロファイル

### 7.1 プロファイル一覧

| プロファイル | 主形式 | 分割基準 | まとめ方 | 出力 |
|---|---|---|---|---|
| `standard` | PDF / MD（format で指定） | なし（1 項目 = 1 ファイル） | 現行どおり | ZIP（現行と同一構造） |
| `notebooklm` | PDF | 1 ファイルあたりのページ上限 | 隣接する同一書籍の項目を 1 PDF に結合 | ZIP（manifest 付き） |
| `chat` | Markdown | 1 ファイルあたりの推定トークン上限 | 上限内で複数項目を 1 MD に連結 | ZIP（manifest 付き） |

### 7.2 standard

現在の `format=pdf|md|json` の動作をそのまま「standard プロファイル」と位置づける。**実装上は現行コードパスを維持し、profile 未指定・`profile=standard` のどちらでも現行と同一の出力**（ZIP 名・エントリ名・manifest・エラー応答）とする。

### 7.3 notebooklm

NotebookLM はソース数上限（無料枠で約 50）と 1 ソースあたりの分量上限（約 50 万語 / 200MB）を持つ。方針:

- PDF を主形式とする（NotebookLM は PDF を直接ソース化できるため）
- **隣接する同一書籍（同一 `pdf_path`）の項目を 1 つの PDF に結合**し、ソース数を減らす。結合しても項目境界は manifest とページ範囲で追跡できる
- 1 ファイルのページ数が上限（既定 300。§20 参照）を超える場合は項目単位で分割する
- 項目の途中（ページ範囲の途中）では絶対に分割しない
- 出力ファイル数がソース数の目安（既定 50）を超える場合は警告する（出力自体は行う）
- **上限・閾値はハードコードしない**。NotebookLM の制限値は契約（無料/有料プラン）で異なり、外部サービス都合で変更されるため、モジュール定数をデフォルトとし環境変数で上書き可能にする（既存 `PDF_EXPORT_SAVE_DIR` と同じ流儀）
  - `TSUNDOKENSAKU_NOTEBOOKLM_MAX_PAGES_PER_FILE`（分割上限。既定 300）
  - `TSUNDOKENSAKU_NOTEBOOKLM_MAX_SOURCES`（ソース数の警告閾値。既定 50。出力は止めない）
  - 実装上の注意: `PROFILES` はモジュールロード時に生成されるシングルトンのため、環境変数を `__init__` で読むと値が固定化される。`chunk_limit()` / `extra_warnings()` の**呼び出し時に都度読む**（テスト・設定変更が再起動なしで効く）
- 出典が分かるエントリ名 `{NN}_{書名}_p{範囲}.pdf` を維持し、複数ファイルは ZIP にまとめる。結合チャンクは各項目の範囲を `_` 連結した表記（例: `03_本A_p1-10_5-8.pdf`）とし、255 バイト超過時は既存の短縮ロジックに委ねる

### 7.4 chat（ChatGPT / Claude 共通）

- Markdown を主形式とする
- 推定トークン数を基準に、**項目単位の貪欲法**で分割する（§9）
- 1 ファイルの上限は `CHAT_TOKEN_LIMIT_DEFAULT`（初期値 80,000 トークン）
- 各ファイル冒頭に**チャンクヘッダ**（資料名・分冊番号 n/全m・収録項目一覧）を置く。項目本文は既存 `render_markdown_export` の出力（書名・元 PDF 名・ページ範囲・抽出日のヘッダを含む）をそのまま `---` 区切りで連結する。チャンク側で追加するのはチャンクヘッダのみで、既存ロジックのコピーは行わない（ヘッダ生成は `markdown_export.render_chat_chunk_header()` として追加）
- 複数ファイルには連番（`_01`, `_02`, …）を付け、ZIP にまとめる
- **既知の制約（未インデックスページによるトークン過小評価）**: chat の分割重みは DB のテキスト統計に基づくため、未インデックスページは 0 トークン扱いになり、実際にはトークン上限を超える分冊ができ得る。v1 では許容し、プレビューの `unindexed_pages` 警告で利用者に補足する（エクスポート時にその場抽出はしない — §8.3 の方針を維持）。チャンクヘッダ・項目ヘッダ自体のトークン消費（数百トークン）も誤差として扱う

### 7.5 ChatGPT と Claude を分けるか

**初期実装では共通の `chat` プロファイル 1 本とする。** 理由:

1. 両者とも Markdown をそのまま受け付け、貼り付け・添付の作法に差がない
2. コンテキスト上限は異なる（ChatGPT ~128k、Claude ~200k）が、本機能のトークン数は概算（§8）であり、モデル差より概算誤差の方が支配的。保守的な共通上限（80k）でどちらにも安全に収まる
3. プロファイルは戦略クラス（§13）として定義するため、将来モデル別の上限・トークナイザー・命名が必要になれば `ChatProfile` のサブクラス（`ClaudeProfile` 等）を 1 つ追加して `PROFILES` に登録するだけで分離できる。分割・命名・描画の共通部は基底実装をそのまま継承する

分離が必要になる将来のトリガー: 上限の大きい資料を Claude だけに最大限詰めたい要望が実際に出たとき。

## 8. トークン概算方式

### 8.1 計算式

新しい依存ライブラリは追加せず、文字種別の係数近似を使う。

```
推定トークン数 = ceil( CJK文字数 × 1.0 + その他文字数 × 0.25 )
```

- **CJK 文字**: ひらがな・カタカナ・CJK 統合漢字・CJK 記号/約物・全角英数（Unicode ブロック判定。正規表現 1 本で判定できる）
- **その他文字**: ASCII 英数・記号・空白・改行など

根拠: 主要トークナイザー（cl100k / o200k / Claude）で日本語はおおむね 1 文字 ≈ 0.8〜1.5 トークン、英語は 1 トークン ≈ 4 文字。係数 1.0 / 0.25 は日英混在の技術書で極端に外れにくい中庸値とし、**モジュール定数**（`CJK_TOKENS_PER_CHAR`, `OTHER_TOKENS_PER_CHAR`）として一箇所にまとめる。

推定文字数は同じ走査で得られる総文字数（空白正規化後）をそのまま使う。

### 8.2 計算場所と実装構造

- 新規モジュール `src/tsundokensaku/token_estimate.py`
  - `TextStats(cjk_chars, other_chars)` dataclass と `count_text_stats(text) -> TextStats`
  - `estimate_tokens(stats: TextStats) -> int`
  - 将来のモデル別トークナイザー差し替えのため、推定関数は `TokenEstimator = Callable[[TextStats], int]` として抽象化し、**どの推定関数を使うかは各プロファイルが `estimator()` で返す**（§13）。既定は全プロファイル共通で `estimate_tokens`。正確なトークナイザーを入れる場合は、該当プロファイルの `estimator()` を差し替えるだけでよく、集計・分割・API 層は変更不要
- 集計は web.py ではなく新規 `src/tsundokensaku/export_stats.py` に置き、DB 接続と `pack_items` を受けて項目別統計を返す（web.py はエンドポイントの薄い層に留める）

### 8.3 PDF 本文の取得タイミングと処理負荷

- **プレビュー（概算）時は DB の `pages` テーブルのみ**を読む。インデックス済みの本なら本文は既に DB にあり、PDF ファイルを開かない
  - ページ数の展開（spec → ページ番号リスト）に必要な総ページ数は `SELECT MAX(page_number) FROM pages WHERE book_id = ?` で得る。未インデックスの本のみ `pdf_outline.get_page_count`（fitz）へフォールバック
  - 未インデックスページの本文は概算に含めず、「未インデックスのため n ページ分を概算に含めていません」警告を出す（プレビューでその場抽出はしない。抽出は遅くプレビューの応答性を損なうため）
- **エクスポート実行時は現行どおり**: PDF は pypdf で開いてページを切り出し、Markdown 本文は DB 優先 + 欠落ページのみその場抽出（`load_pages_text` の既存挙動）
- 処理負荷の目安: 資料 1 件は多くても数百ページ × 数 KB のテキスト読み出しで、SQLite ローカル読みなら数十 ms オーダー。文字種判定は正規表現 1 パスで済む

### 8.4 キャッシュの要否

初期実装では**キャッシュ不要**とする。プレビュー API は操作のたびに呼ばれる程度（毎キーストロークではない）で、上記負荷なら都度計算で足りる。実測で遅い場合の将来案として、`pages` テーブルへ文字種別カウント列を追加（インデックス時に前計算）する拡張余地をコメントで残す。

### 8.5 概算であることの表示

- UI では必ず「約」を付け、「推定トークン数（概算）」と表記する
- プレビュー API のレスポンスに `"estimation": "approximate"` と係数バージョン（`"estimator": "char-class-v1"`）を含め、将来トークナイザーを差し替えた際に区別できるようにする

## 9. 分割アルゴリズム

### 9.1 方針

- 資料項目（ページ範囲）の途中では分割しない
- 可能な限り書籍単位でまとめる（notebooklm: 隣接する同一 `pdf_path` の項目を優先的に同一ファイルへ）
- 上限を超える場合は資料項目単位で分割する
- 1 項目だけで上限を超える場合は警告し、その項目単独で 1 ファイルとして出力する（切り捨てない）
- 分割後も元の資料の並び順（position 順）を維持する。ファイル間・ファイル内ともに順序を入れ替えない
- 同一 PDF の別資料項目は別項目として扱う（結合対象になるのは「隣接」している場合のみ。間に別の本が挟まっていれば別ファイルでよい — 並び順維持を優先）
- **重複ページは除去しない**。隣接する同一 PDF 項目の結合（notebooklm）で範囲が重なる場合（例: p.1-10 と p.5-8）、p.5-8 は 2 回入る。資料項目は「別々の文脈で使う引用単位」であり（pack-item-identity-design.md）、去重すると項目境界と出典追跡が壊れるため
- **結合 PDF 内の順序は「項目の position 順 + 各項目の spec 記載順」を維持し、昇順ソートしない**。spec は "8,1-3" のような列挙順を保持するため結合後のページは昇順にならないことがあるが、項目の並び = 利用者が意図した提示順として扱う
- 出典情報（書名・元 PDF・ページ範囲）は manifest と各ファイル（chat はファイル冒頭ヘッダ、notebooklm はファイル名 + manifest）に必ず残す

### 9.2 アルゴリズム（貪欲法）

入力: position 順の項目統計リスト `[(item, pages, tokens)]`、プロファイルの上限（chat: トークン、notebooklm: ページ数）

```
chunks = []
current = 新しい空チャンク
for item in items (position順):
    if プロファイルが notebooklm かつ current の末尾項目と同一 pdf_path かつ ページ合計が上限内:
        current に追加   # 書籍単位の結合を優先
    elif current が空でなく、追加すると上限超過:
        chunks に current を確定し、current = item だけの新チャンク
    else:
        current に追加
    if item 単独で上限超過:
        警告リストに追加（チャンクはその項目単独で確定）
chunks に最後の current を確定
```

- 貪欲法の骨格は `ExportProfile.plan()` の基底実装として 1 つだけ持ち、「項目の重み（トークン or ページ）」「上限」「結合してよいか」の判断は各プロファイルのフックメソッドへ委譲する（§13）。plan は DB・ファイル I/O から独立した純粋ロジックとしてユニットテスト可能にする
- `ExportPlan` は各チャンクの項目・ページ合計・トークン合計・出力ファイル名と、警告一覧を持つ（§13）
- standard プロファイルは分割を行わず、1 項目 = 1 チャンク（現行の 1 項目 = 1 エントリと同じ）

## 10. ファイル命名規則

### 10.1 命名

| 対象 | 形式 | 例 |
|---|---|---|
| ZIP（standard） | `{資料名}_{YYYYMMDD}.zip`（現行維持） | `コードとログ_20260711.zip` |
| ZIP（profile 指定） | `{資料名}_{profile}_{YYYYMMDD}.zip` | `コードとログ_notebooklm_20260711.zip` |
| エントリ（standard） | `{NN}_{書名}_p{範囲}.{ext}`（現行維持） | `01_伽藍とバザール_p1-15.pdf` |
| エントリ（notebooklm） | `{NN}_{書名}_p{範囲}.pdf` | `03_本A_p10-20_80-95.pdf`（結合時は範囲を連結） |
| エントリ（chat） | `{資料名}_chat_{NN}.md` | `コードとログ_chat_01.md` |

- notebooklm のエントリ名は「出典が分かるファイル名」の要件を優先し、資料名ではなく**書名**を使う（NotebookLM のソース一覧にファイル名が表示されるため、書名が見える方が出典として機能する）。要件例の `資料名_notebooklm_01.pdf` からの意図的な変更であり、資料名と profile は ZIP 名が担う
- chat のエントリ名は分冊の連続性が重要なので `資料名 + 連番` とし、出典はファイル冒頭ヘッダと manifest に記載する
- 連番 `NN` は 2 桁ゼロ埋め。100 を超える場合は自然に 3 桁になる（`{index:02d}` の仕様どおり）

### 10.2 使えない文字・重複・長さ

- サニタイズは既存 `zip_export.sanitize_filename_component`（`[^\w.-]+` → `_`、空なら `untitled`）に一本化して再利用する。OS 予約文字（`/ \ : * ? " < > |`）はこの規則で全て除去される
- 重複名: ZIP 内は連番 prefix / suffix が主キーとなり衝突しない。同名書籍・同一 PDF の複数項目も連番で区別される（現行方式の踏襲）
- 長さ: 既存の 255 バイト制限ロジック（`build_entry_filename`）を汎用化して使う。短縮の優先順位は「連番・profile・拡張子 > ページ範囲（→ Nページ表記）> 書名/資料名（… で切り詰め）」。詳細なページ範囲は常に manifest 側に残る
- chat の `{資料名}_chat_{NN}.md` は項目ベースの `build_entry_filename` では組み立てられないため、連番ベースの小さなヘルパ（`build_sequenced_filename(base_name, profile_name, index, ext)`。同じ 255 バイト切り詰め方針）を `zip_export.py` に追加する

### 10.3 manifest（profile 指定時）

- **profile 指定エクスポートの manifest は `ExportPlan` から組み立てる新レンダラで生成する**。既存 `render_pack_manifest` は `PackExportEntry`（1 項目 = 1 エントリ）前提のため、複数項目チャンク（notebooklm の結合・chat の分冊）の項目内訳を表現できない
- 新レンダラは「チャンク（出力ファイル） → 収録項目（書名・元 PDF・ページ範囲）」の階層で一覧し、`manifest_header_lines()` の内容と **plan の警告（`item_exceeds_limit` 等）** も記載する（§14 参照）
- standard（profile 未指定・`profile=standard`）は現行 `render_pack_manifest` をそのまま使い続け、バイト互換を守る

## 11. UI 設計

### 11.1 方針

- 資料棚ツールバーの既存 3 ボタン（PDF一式 / MD一式 / 資料データ JSON）は**そのまま残す**（standard 相当。既存動線を壊さない）
- 「AI向けに書き出す」ボタンを 1 つ追加し、押すとモーダルを開く。初心者は宛先を選んで実行するだけで完了する
- 段階導入（§19）: Phase 3A ではまずツールバー付近に概算（ページ数・約トークン）を常時表示するだけとし、モーダルはプロファイル出力が使えるようになる Phase 3C で導入する。モーダル導入後、常時表示は要約（1 行）に縮小する

### 11.2 エクスポートモーダル（新設）

```
┌─ AI向けに書き出す ──────────────────────────┐
│ 渡す先:  (●) NotebookLM      ( ) ChatGPT / Claude │
│                                                    │
│ この資料の概算                                     │
│   書籍数: 3冊 / 資料項目: 5件 / 合計 128ページ     │
│   推定文字数: 約21万字 / 推定トークン数: 約19万     │
│   出力予定: 4ファイル（ZIP）                        │
│                                                    │
│ 分割予定                                           │
│   1. 本A p.10-20, 80-95        （約4.2万トークン）  │
│   2. 本B p.1-50                （約7.8万トークン）  │
│   ...                                              │
│                                                    │
│ ⚠ 「本C p.1-400」は1項目で上限を超えるため単独出力  │
│                                                    │
│ ▸ 詳細設定（初期状態では折りたたみ）                │
│     1ファイルあたりの上限:  [80000] トークン        │
│                                                    │
│ ※ トークン数は文字種からの概算です                  │
│               [キャンセル]  [書き出す]              │
└────────────────────────────────────────┘
```

- モーダルを開いたとき・プロファイルを切り替えたときに `GET /api/packs/{id}/export/preview?profile=...` を呼び、概算・分割予定・警告を表示する
- 設定項目は「渡す先」の 1 択のみを初期表示とし、詳細設定（トークン上限の変更）は折りたたむ。上限変更は初期実装では見送り可（§20）
- 実行時は既存 `exportPackZip` と同じ流れ（構文検証 → `flushPendingSave()` → fetch → Blob ダウンロード）に `profile` パラメータを足す
- 実装は `workspace.html` 内の既存スクリプトへの追記で収め、新規 JS ファイルは作らない（現行構成の踏襲）

## 12. API 設計

### 12.1 プレビュー API（新設）

```
GET /api/packs/{pack_id}/export/preview?profile=standard|notebooklm|chat
```

段階導入（§19）: Phase 3A では `profile` パラメータなしで導入し、Phase 3C（C-3）で `profile` パラメータを追加する。後方互換のため、追加後も `profile` 省略時は standard 相当の概算を返す。

レスポンス例:

```json
{
  "profile": "chat",
  "estimation": "approximate",
  "estimator": "char-class-v1",
  "book_count": 3,
  "item_count": 5,
  "total_pages": 128,
  "estimated_chars": 210000,
  "estimated_tokens": 190000,
  "file_count": 4,
  "archive": "zip",
  "chunks": [
    {
      "filename": "コードとログ_chat_01.md",
      "estimated_tokens": 42000,
      "pages": 27,
      "items": [
        {"item_id": 101, "title": "本A", "pdf_path": "...", "pages": "10-20", "estimated_tokens": 18000}
      ]
    }
  ],
  "warnings": [
    {"code": "item_exceeds_limit", "item_id": 105, "message": "「本C p.1-400」は1ファイルの上限を超えるため単独で出力します"},
    {"code": "unindexed_pages", "item_id": 103, "message": "未インデックスのため 12 ページ分を概算に含めていません"}
  ]
}
```

- 空資料は 400 にせず、全カウント 0 + 警告 `empty_pack` で 200 を返す（プレビューは「実行できない理由」を表示する場でもあるため）。ページ未指定項目・PDF 欠損も同様に警告として返し、UI 側で「書き出す」を無効化する
- 警告は `code`（機械可読）+ `message`（表示用）の組とする

### 12.2 エクスポート API（拡張）

```
GET /api/packs/{pack_id}/export?format=pdf|md|json          … 現行どおり（変更なし）
GET /api/packs/{pack_id}/export?profile=notebooklm|chat     … 新設パス
GET /api/packs/{pack_id}/export?profile=standard&format=... … 現行と同一出力
```

- `profile` 未指定 → 現行コードパス。レスポンスはバイト単位で現行互換
- **format 省略時の解決規則（Phase 3C で導入）**: `format` パラメータの既定値を `Query("pdf")` から `Query(None)` へ変更し、次の順で解決する
  1. format 省略時: `profile.primary_format` があればそれ（chat→md、notebooklm→pdf）、なければ "pdf"（profile 未指定・standard の現行既定を維持）
  2. format 明示時: 従来どおり `pdf|md|json` を検証し、`primary_format` と矛盾すれば 400（誤用を黙って解釈しない）

  この変更がないと `profile=chat`（format 省略）が既定値 "pdf" との矛盾で 400 になる。profile 未指定 + format 省略 → pdf は変わらないため後方互換
- `profile=standard` は `format`（既定 pdf）に従い現行出力
- profile 指定時の出力は**ファイル数にかかわらず常に ZIP**（manifest.md を必ず同梱し出典を保証する。単一ファイル直ダウンロードは §20 の未決事項）
- エラー: profile 指定時も空資料 400 / ページ未指定 400 / PDF 欠損 404 は standard と同じ（実行 API は従来どおり厳格、プレビュー API だけ寛容）。**ページ未指定の検証は plan の前に全項目を対象に行う**（§13.3 参照。B-2 時点の「チャンク先頭項目のみ検証」は複数項目チャンクで検証漏れするため、Phase 3C の C-0 で全項目の事前検証ループへ移す。エラー文言・position 順の検出順序は現行と同一）
- エクスポート成功時は `export_events` へ 1 行記録する（Phase 3C の C-6。[export-events-design.md](export-events-design.md)）。記録失敗はエクスポートを失敗させない

### 12.3 変更しないもの

`GET /export-pdf`, `GET /export-md`, `POST /export-pdf/save`, `format=json`, `/api/packs` 系 CRUD は一切変更しない。

## 13. 内部データ構造

### 13.1 設計方針: プロファイルを戦略クラスにする

プロファイルを「フラグの入れ物 dataclass + 外部関数がフラグを解釈」にすると、分岐が `plan_export`・命名関数・web.py に散らばり、プロファイル追加のたびに複数箇所へ if 分岐が増える。そこで **`ExportProfile` を抽象基底クラス（戦略）とし、宛先ごとの差分をすべてプロファイル自身の責務にする**。

`ExportProfile` が持つ責務:

1. **概算**: どのトークン推定関数を使うか（`estimator()`）
2. **分割判断**: 項目の重み（トークンかページか）・上限・隣接項目を結合してよいか
3. **警告**: 上限超過・ソース数超過などプロファイル固有の警告生成
4. **命名**: チャンクのファイル名・ZIP 名
5. **出力**: チャンク 1 つをバイト列へ描画する方法（PDF 結合 / MD 連結）と manifest への追記

web.py（プレビュー・エクスポート両エンドポイント）の責務は「プロファイル解決 → 統計収集 → `plan()` → （実行時のみ）`render_chunk()` → ZIP 組み立て」の配線だけに限定する。**判断基準: 新しいプロファイルの追加・変更で触るのが `export_profiles.py`（+ テスト）だけで済むこと。**

### 13.2 モジュール構成

```python
# src/tsundokensaku/token_estimate.py（純粋ロジック）
CJK_TOKENS_PER_CHAR = 1.0
OTHER_TOKENS_PER_CHAR = 0.25

@dataclass(frozen=True)
class TextStats:
    cjk_chars: int
    other_chars: int

TokenEstimator = Callable[[TextStats], int]

def count_text_stats(text: str) -> TextStats: ...
def estimate_tokens(stats: TextStats) -> int: ...   # 既定の推定関数（char-class-v1）
```

```python
# src/tsundokensaku/export_stats.py（DB アクセス層。プロファイル非依存）
@dataclass(frozen=True)
class ItemStats:
    item: PackItemRecord
    page_numbers: list[int]
    stats: TextStats
    unindexed_pages: int
    missing_pdf: bool

def collect_item_stats(connection, items, *, books_dir) -> list[ItemStats]: ...
```

```python
# src/tsundokensaku/export_profiles.py（プロファイル定義 = Phase 3 の中核）
@dataclass(frozen=True)
class ExportWarning:
    code: str
    item_id: int | None
    message: str

@dataclass(frozen=True)
class ExportChunk:
    index: int                    # 1 始まりの連番
    items: tuple[ItemStats, ...]
    total_pages: int
    estimated_tokens: int

@dataclass(frozen=True)
class ExportPlan:
    profile_name: str
    chunks: tuple[ExportChunk, ...]
    warnings: tuple[ExportWarning, ...]

class ExportProfile(ABC):
    name: str                     # "standard" | "notebooklm" | "chat"
    # standard は format=pdf|md|json を実行時に選べるため固定値を持たない（None）。
    # chat/notebooklm は将来それぞれ "md"/"pdf" を固定値として持つ想定
    primary_format: str | None    # "pdf" | "md" | None（standard）

    # --- 概算 ---
    def estimator(self) -> TokenEstimator:
        return estimate_tokens    # 既定。モデル別トークナイザーはここを差し替える

    # --- 分割判断（plan の基底実装から呼ばれるフック） ---
    @abstractmethod
    def item_weight(self, stats: ItemStats) -> int: ...     # chat=トークン, notebooklm=ページ数
    @abstractmethod
    def chunk_limit(self) -> int | None: ...                # standard は None（分割なし）
    def can_merge(self, current: ExportChunk, stats: ItemStats) -> bool:
        return True               # notebooklm は「同一 pdf_path なら上限を超えても優先結合しない」等を上書き

    # --- プラン（§9.2 の貪欲法。基底実装 1 つ、純粋ロジック） ---
    def plan(self, item_stats: list[ItemStats]) -> ExportPlan: ...
    def extra_warnings(self, plan: ExportPlan) -> tuple[ExportWarning, ...]:
        return ()                 # notebooklm のソース数警告など

    # --- 命名 ---
    @abstractmethod
    def chunk_filename(self, chunk: ExportChunk, *, pack_name: str, format: str | None = None) -> str: ...
                                  # format は primary_format が None のプロファイル（standard）が
                                  # 実行時に選ばれた形式を拡張子に反映するためのオプション引数
    def archive_filename(self, *, pack_name: str, exported_at: datetime) -> str: ...
                                  # 既定 {資料名}_{name}_{YYYYMMDD}.zip。standard は現行名へ上書き

    # --- 出力 ---
    @abstractmethod
    def render_chunk(self, chunk: ExportChunk, ctx: RenderContext) -> bytes: ...
    def manifest_header_lines(self, plan: ExportPlan) -> list[str]:
        return []                 # 分冊情報等の manifest 追記。standard は空（現行 manifest 維持）

class StandardProfile(ExportProfile): ...   # 1項目=1チャンク・現行命名・現行出力（バイト互換）
class NotebookLMProfile(ExportProfile): ... # ページ上限・同一書籍結合・PDF 結合描画
class ChatProfile(ExportProfile): ...       # トークン上限・MD 連結描画・出典ヘッダ

PROFILES: dict[str, ExportProfile] = {p.name: p for p in (StandardProfile(), NotebookLMProfile(), ChatProfile())}
```

```python
# RenderContext（web.py が組み立てて渡す。プロファイルに DB 接続や
# FastAPI の Request/Response を持たせない）
@dataclass(frozen=True)
class RenderContext:
    pack_name: str
    exported_at: datetime
    format: str                                        # 実行時に選ばれた形式（standard用）
    total_chunks: int                                   # 分冊総数（chat の「分冊 n/全m」表記用。plan 確定後に設定）
    resolve_pdf: Callable[[str], Path]                  # _resolve_pdf_file_or_404 相当
    render_pdf: Callable[[Path, str], tuple[bytes, str]]        # render_pdf_export 相当
    render_markdown: Callable[[Path, str], tuple[str, str]]     # render_markdown_export 相当
```

B-2実装時の変更: 当初案の `load_texts`（本文取得のみ）ではなく、`render_pdf` / `render_markdown` という一段高いレベルの関数を注入する形にした。理由は、`load_texts` だけを注入すると、Markdown生成のヘッダ組み立て（`render_markdown_pages` 呼び出し・タイトル解決）を `export_profiles.py` 側に再実装する必要が生じ、「既存ロジックをコピーしない」という制約に反するため。`render_pdf_export` / `render_markdown_export`（web.py 既存、HTTPException を内包する薄いラッパー）をそのまま注入することで、`export_profiles.py` は `fastapi` を一切 import せずに済む。

### 13.3 分離の境界

- **`export_stats.py`（DB）と `export_profiles.py`（判断・描画）を分ける**: 統計収集はプロファイル非依存で 1 回だけ行い、同じ `ItemStats` 列を全プロファイルが解釈する。`plan()` は I/O なしでユニットテスト可能
- **エクスポート実行時の統計の使い分け**: 分割に実統計を必要とするプロファイル（`chunk_limit()` が整数を返すもの = chat/notebooklm）は `collect_item_stats`（DB 読み）を使い、standard（`chunk_limit()` = None、重みを使わない）は空のプレースホルダ統計のままでよい。`collect_item_stats` は寛容（不正 spec・欠損 PDF を空扱い）だが、厳格な 400/404 は plan 前の全項目事前検証（ページ未指定）と render 時の既存関数（PDF 欠損 404・不正 spec 400）が従来どおり発生させるため、エラー互換は保たれる
- **項目の事前検証は plan の前に全項目を対象に行う**: ページ未指定の 400 チェックをチャンクループ内（先頭項目のみ）ではなく、plan 前の position 順ループで実施する（Phase 3C の C-0）。複数項目チャンクでの検証漏れを防ぎ、standard のエラー文言・検出順序は不変
- **`render_chunk` は I/O を `RenderContext` の関数経由でのみ行う**: standard は注入された `render_pdf` / `render_markdown`（web.py の既存関数）をそのまま呼ぶ。chat は各項目を `render_markdown` で描画してチャンクヘッダとともに連結、notebooklm は各項目を `render_pdf` で描画してから pypdf で連結する（spec 解析・範囲検証・404/400 の発生点が standard と完全に同じになり、エラー互換が自動的に保たれる。結合 PDF のメタデータは先頭項目の描画結果から引き継ぐ）。テストではフェイクの `resolve_pdf` / `render_pdf` / `render_markdown` を注入できる
- **既存 `PackExportEntry` / `build_pack_zip` は再利用**: プロファイルは「エントリ列を作るまで」を担い、ZIP 化は既存関数に任せる。standard の manifest・エントリ名は現行実装を `StandardProfile` がそのまま呼ぶことでバイト互換を守る

## 14. エラー処理

| 状況 | プレビュー API | エクスポート API（profile 指定時） |
|---|---|---|
| 資料が存在しない | 404 | 404（現行同） |
| 空資料 | 200 + `empty_pack` 警告 | 400（現行同） |
| ページ未指定の項目 | 200 + `missing_pages` 警告 | 400（現行同、項目名入りメッセージ） |
| PDF 実体が見つからない | 200 + `missing_pdf` 警告 | 404（現行同） |
| 不正な spec（範囲外等） | 200 + `invalid_pages` 警告 | 400（現行同） |
| 未インデックスの本 | 200 + `unindexed_pages` 警告（概算から除外） | 出力可（MD は現行のその場抽出、PDF は影響なし） |
| 1 項目で上限超過 | 200 + `item_exceeds_limit` 警告 | 出力可（単独ファイル化） |
| 不明な profile 値 | 400 | 400 |
| profile と format の矛盾 | —（preview に format なし） | 400 |

方針: **プレビューは寛容（問題を列挙して返す）、実行は厳格（現行の 4xx を踏襲）**。想定外の例外は Phase 4B で整理済みの既存ハンドリング方針（バリデーション由来の 4xx と想定外の 500 の分離）に従う。

**plan 警告（`item_exceeds_limit`、notebooklm のソース数超過等）の出力先は 2 箇所**: (1) プレビュー API のレスポンス（実行前に見える）、(2) profile 指定エクスポートの manifest.md（成果物に残る。§10.3）。エクスポート実行(200)のレスポンス形式（ヘッダ・ボディ構造）には警告を載せず、現行と変えない。

## 15. 後方互換性

| 項目 | 保証内容 |
|---|---|
| 既存 URL | `/export-pdf`, `/export-md`, `/export-pdf/save`, `/api/packs/{id}/export` すべて変更なし |
| 既存 API パラメータ | `format=pdf\|md\|json` の意味・既定値（pdf）・エラー応答を維持。`profile` は追加の任意パラメータ |
| 既存テスト | 修正なしで全通過することを Phase 3 の受け入れ条件とする |
| 現在のファイル名 | profile 未指定時の ZIP 名 `{資料名}_{YYYYMMDD}.zip`・エントリ名 `{NN}_{書名}_p{範囲}.{ext}` を維持 |
| ZIP 構造 | profile 未指定時は manifest.md + エントリ順の現行構造を維持 |
| 並び順 | position 順の出力を全プロファイルで維持 |
| ページ範囲 | spec 文法・検証（`parse_page_selection`）を変更しない |
| 同一 PDF の複数項目 | 別項目として出力する現行設計を全プロファイルで維持 |
| 空資料・削除済み PDF | 実行 API の 400 / 404 を維持（プレビューのみ新設のため互換対象外） |
| UI | 既存 3 ボタンを残す。新ボタン追加のみ |

既存動作の変更は行わない。唯一の追加的変更は `api_export_pack` 内部のリファクタ（項目ループの関数抽出）だが、出力バイト列と HTTP 応答は不変とし、既存テストで担保する。

## 16. セキュリティ

- PDF パス解決は既存 `_resolve_pdf_file_or_404`（books_dir 外へのトラバーサル拒否）を全プロファイルで共通利用する
- ファイル名は `sanitize_filename_component` で生成し、利用者入力（資料名・書名）が ZIP エントリ名・Content-Disposition に入る箇所は既存同様サニタイズ + `quote()` を通す
- 外部 API 呼び出しなし・ローカル完結を維持する。トークン概算も完全ローカル計算
- プレビュー API は読み取り専用で、DB への書き込みを行わない
- デモモード（`DEMO_MODE`）: エクスポートは現行でもデモモードで許可されているため、プレビュー・profile エクスポートも同じ扱いとする

## 17. パフォーマンス

- プレビュー: DB 読みのみ（本文テキスト取得 + Python 1 パス走査）。想定規模（資料 1 件 ≤ 数百ページ）で数十 ms。PDF ファイルは未インデックス本のページ数取得（fitz）以外開かない
- エクスポート実行: 現行と同等（項目ごとに pypdf でページコピー）。notebooklm の結合は既存処理の Writer 共有化であり追加コストなし
- キャッシュ: 初期実装では持たない（§8.4）。遅延が問題化したらインデックス時の文字種カウント前計算を検討
- ZIP はメモリ上（BytesIO）で構築する現行方式を踏襲。資料が GB 級になるケースは現状想定外（未決事項に記載）

## 18. テスト方針

- **ユニット（新規）**
  - `tests/test_token_estimate.py`: 日本語のみ / 英語のみ / 混在 / 空文字 / 記号・空白の係数計算
  - `tests/test_export_profiles.py`: `plan_export` の分割（上限内 1 チャンク、超過分割、1 項目超過の単独化 + 警告、notebooklm の隣接同一書籍結合、並び順維持、同一 PDF 別項目の独立性）
  - `tests/test_export_stats.py`: DB からの項目統計収集（インデックス済み / 未インデックス / PDF 欠損 / 空 spec）
  - `tests/test_zip_export.py` 追記: profile 付き ZIP 名・chat エントリ名・拡張 manifest
- **API（`tests/test_web.py` 追記）**
  - preview: 正常系（各プロファイル）、空資料 200 + 警告、不明 profile 400
  - export: `profile=chat` の MD 分冊 ZIP、`profile=notebooklm` の結合 PDF ZIP、profile と format の矛盾 400、**profile 未指定時の現行出力が変わらないこと**（既存テストがそのまま担保）
- **回帰**: 既存テスト全件を修正なしで通す（`make test`）
- **E2E（Playwright）**: モーダルを開く → 概算表示 → プロファイル選択 → ダウンロードまでの 1 本。既存の workers=1 制約に従う

## 19. 実装ステップ

Phase 3 を、**それぞれ単独でリリース（マージして日常利用）できる 5 つのマイルストーン**に分ける。各マイルストーンは前のものが本番相当で動いている前提で始め、途中で止めても中途半端な状態が残らない。マイルストーン内の各ステップも独立してレビュー・マージ可能な単位とする。

### Phase 3A: トークンバジェット可視化

**利用者価値**: エクスポート前に「この資料は何ページ・約何トークンか」が見える。「渡しすぎ / 足りない」の判断が可能になる（分割・プロファイルはまだない）。

| # | ステップ | 変更対象 | テスト | 依存 |
|---|---|---|---|---|
| A-1 | トークン概算ロジック | `token_estimate.py`（新規） | `test_token_estimate.py`（新規） | なし |
| A-2 | 資料集計（spec 展開・文字数・未インデックス/欠損検出） | `export_stats.py`（新規） | `test_export_stats.py`（新規） | A-1 |
| A-3 | プレビュー API（概算のみ。profile パラメータなし、分割予定なし） | `web.py` | `test_web.py` 追記 | A-2 |
| A-4 | 資料棚ツールバーへの概算表示（書籍数・項目数・ページ数・約トークン。モーダルはまだ作らない） | `workspace.html` | 手動確認 | A-3 |

**完了条件**: 資料棚で概算が常時見える。既存テスト全件が無修正で通る。エクスポート動作は一切変わっていない。

### Phase 3B: プロファイル基盤（出力不変の内部整理）

**利用者価値**: なし（意図的）。`ExportProfile` 戦略クラスを導入し、現行エクスポートを `StandardProfile` 経由に載せ替える。**出力バイト列・HTTP 応答は不変**で、リスクの高い配線替えをプロファイル追加と切り離して単独レビューする。

| # | ステップ | 変更対象 | テスト | 依存 |
|---|---|---|---|---|
| B-1 | `ExportProfile` 抽象基底 + `plan()` 貪欲法の基底実装 + `StandardProfile`（純粋ロジック） | `export_profiles.py`（新規） | `test_export_profiles.py`（新規） | A-2 |
| B-2 | `api_export_pack` の項目ループを `StandardProfile` 経由へ載せ替え（`RenderContext` 導入） | `web.py` | 既存テストは無修正で全通過を確認 + `test_web.py` へ後方互換性テストを追記（ZIP構造・エントリ内容・エラー応答の一致） | B-1 |
| B-3 | `profile` パラメータ受付（`standard` のみ有効。不明値 400、format 矛盾 400） | `web.py` | `test_web.py` 追記 | B-2 |

**完了条件**: `profile` 未指定・`profile=standard` の出力が現行とバイト互換（既存テスト + ZIP 内容比較テストで担保）。

### Phase 3C: chat プロファイル

**利用者価値**: 「ChatGPT / Claude へ渡す」を選ぶだけで、トークン上限で分冊された Markdown ZIP が得られる。

（B-2/B-3 実装後の監査 [phase3c-3d-design-review.md](phase3c-3d-design-review.md) を反映し、下準備の C-0 とイベント記録の C-6 を追加）

| # | ステップ | 変更対象 | テスト | 依存 |
|---|---|---|---|---|
| C-0 | 実行経路の下準備: format=None デフォルト解決（§12.2）+ ページ未指定検証の plan 前移動（§13.3）。出力不変 | `web.py` | 既存テスト無修正全通過 + format 省略の互換テスト | B-3 |
| C-1 | `ChatProfile` の plan 系（定数・トークン重み・上限・命名）+ 連番ファイル名ヘルパ（§10.2） | `export_profiles.py`, `zip_export.py` | `test_export_profiles.py` / `test_zip_export.py` 追記 | B-1 |
| C-2 | chat チャンク描画（チャンクヘッダレンダラ + `render_chunk` + `RenderContext.total_chunks`） | `markdown_export.py`, `export_profiles.py` | `test_markdown_export.py` / `test_export_profiles.py` 追記（フェイク注入） | C-1 |
| C-3 | export API 配線（実統計分岐 §13.3 + `ExportPlan` 由来 manifest §10.3 + `PROFILES` 登録） | `web.py`, `zip_export.py` | `test_web.py` 追記（分冊 ZIP・警告 manifest・重複項目・超過項目） | C-0, C-2 |
| C-4 | プレビュー API の profile パラメータ + 分割予定・警告（§12.1 の後方互換注記どおり省略時 standard 相当） | `web.py` | `test_web.py` 追記 | A-3, C-1 |
| C-5 | UI: エクスポートモーダル（宛先選択・概算・分割予定・警告。3A の常時表示は 1 行要約に縮小） | `workspace.html` | 手動確認 | C-3, C-4 |
| C-6 | エクスポートイベント記録（`export_events` テーブル + 成功時 INSERT。[export-events-design.md](export-events-design.md)） | `database.py`, `web.py` | `test_database.py` / `test_web.py` 追記 | C-0 |

**完了条件**: モーダルから chat エクスポートが完了し、各 MD に資料名・書名・元 PDF・ページ範囲が入っている。1 項目超過の警告と単独出力が動く。エクスポート成功が export_events に記録される。

### Phase 3D: notebooklm プロファイル

**利用者価値**: 「NotebookLM へ渡す」を選ぶだけで、ソース数を抑えた結合 PDF 群が得られる。

| # | ステップ | 変更対象 | テスト | 依存 |
|---|---|---|---|---|
| D-1 | `NotebookLMProfile` の plan 系（ページ重み・環境変数で可変な上限/警告閾値 §7.3・隣接同一書籍の can_merge・ソース数警告） | `export_profiles.py` | `test_export_profiles.py` 追記（結合・分割・警告・env 上書き） | C-1 |
| D-2 | PDF 結合描画（`render_pdf` 出力の pypdf 連結 + メタデータ引き継ぎ §13.3 + エントリ名 §10.1） | `export_profiles.py`（+必要なら `pdf_export.py` に結合ヘルパ） | `test_export_profiles.py` / `test_export_pdf_pages.py` 追記 | D-1 |
| D-3 | export API / プレビュー / モーダルへの notebooklm 追加（manifest は C-3 の plan 由来レンダラを共用） | `web.py`, `workspace.html` | `test_web.py` 追記 + 手動確認 | C-3〜C-5, D-2 |

**完了条件**: notebooklm エクスポートが完了し、隣接する同一書籍の項目が 1 PDF に結合され（重複ページは非去重・項目順維持 §9.1）、manifest とファイル名から出典が追える。ソース数の警告閾値（既定 50、環境変数で変更可）超過で警告が出る。

### Phase 3E: 仕上げ・実測調整

**利用者価値**: 品質確定。概算の信頼性と回帰防止。

| # | ステップ | 変更対象 | テスト | 依存 |
|---|---|---|---|---|
| E-1 | 概算係数・上限既定値の実測調整（未決事項 1, 2, 7。開発機でのみ実トークナイザーと突き合わせ） | `token_estimate.py`, `export_profiles.py` の定数 | 実測レポートを本設計書へ追記 | 3C, 3D |
| E-2 | 255 バイト短縮の汎用化・命名エッジケースの補強 | `zip_export.py` | `test_zip_export.py` 追記 | 3C, 3D |
| E-3 | E2E: モーダル → 概算 → プロファイル選択 → ダウンロード | `tests/playwright/`（新規 spec） | Playwright（workers=1） | 3C, 3D |
| E-4 | ドキュメント更新（ROADMAP・README・本設計書の状態更新） | `ROADMAP.md` ほか | — | E-1〜E-3 |

**完了条件**: E2E グリーン、係数の実測結果が記録され、ROADMAP の Phase 3 が完了扱いになる。

### マイルストーンの依存関係

```
3A（可視化） → 3B（基盤・出力不変） → 3C（chat） → 3D（notebooklm） → 3E（仕上げ）
```

3C と 3D は 3B 完了後なら並行開発も可能だが、モーダル UI（C-5）を共有するため 3C を先行させる。各マイルストーン末尾で `make test` 全通過＋資料棚の手動確認を行ってからリリースする。

## 20. 未決事項

（Phase 3C 実装前監査で確定したもの: format 省略時の解決規則 → §12.2、NotebookLM 閾値の環境変数化 → §7.3、重複ページ非去重・ページ順非ソート → §9.1、plan 警告の出力先 → §14、manifest の ExportPlan 由来化 → §10.3、未インデックス過小評価の許容 → §7.4、エクスポートイベント記録の開始 → C-6 / [export-events-design.md](export-events-design.md)）

1. **トークン係数の妥当性**: 係数（CJK 1.0 / その他 0.25）は文献ベースの中庸値。手元の蔵書数冊で実トークナイザー（tiktoken 等を開発機でのみ使用）と突き合わせ、±30% に収まるか実測してから確定したい
2. **notebooklm のページ上限既定値**: NotebookLM の公称制限は「1 ソース 50 万語 / 200MB」でページ数基準ではない。既定 300 ページは仮置きであり、実際のアップロード検証（3E）で調整する。値自体は環境変数で上書き可能（§7.3）
3. **profile 出力を常に ZIP とするか**: 分割が発生しない場合に単一ファイルを直接ダウンロードさせる方が手数は少ない。ただし manifest（出典）が失われるため、初期実装は常に ZIP とし、利用感を見て再検討する
4. **詳細設定（トークン上限変更 UI）を初期実装に含めるか**: 含めない案を推奨（プロファイル既定値のみ）。要望が出たら折りたたみ内に追加する
5. **未インデックス本の扱い**: プレビューで概算から除外 + 警告としたが、「プレビュー時にもその場抽出する」選択肢もある（応答性とのトレードオフ）。使いながら判断する
6. **巨大資料（メモリ上 ZIP 構築の限界）**: 現行から続く制約。GB 級の資料が現実に発生するかを見てから streaming 化を検討する。NotebookLM のファイルサイズ上限（200MB/ソース）への対応も描画後にしか判定できないため v1 では扱わない
7. **chat プロファイルの上限既定値 80,000 トークン**: ChatGPT/Claude の実効コンテキストと会話余白を見込んだ仮値。実測（1 と同時）で調整する
8. **Kindle 本・メモの資料項目化との関係**: 現状パックは PDF のみ。将来 Kindle・メモが項目化された場合、notebooklm（PDF 主形式）での扱いは未設計
9. **機械可読 manifest（manifest.json）の ZIP 同梱**: Phase 4 の取り込みは export_events で賄えるため初期は不要（[ai-artifact-return-discovery.md](ai-artifact-return-discovery.md) §5）。別マシン生成 ZIP からの取り込みニーズが出たら再検討
10. **chat 側の分冊内文字数警告**: notebooklm にはソース数警告があるが、chat に語数系の補助警告を足すかは 3E の実測後に判断
