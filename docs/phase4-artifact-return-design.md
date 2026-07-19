# Phase 4「AI成果物の帰還」設計書

作成: 2026-07-19 / 状態: 設計確定（実装は Phase 4A〜4C として実施）
前提: [ROADMAP.md](../ROADMAP.md) Phase 4 / 事前調査: [ai-artifact-return-discovery.md](ai-artifact-return-discovery.md) / エクスポート履歴の確定仕様: [export-events-design.md](export-events-design.md)

## 1. 背景と目的

Phase 3 までで「検索 → 資料へ追加 → 整える → AI へ書き出す」が完成した。しかし AI との対話で得た回答・整理内容は AI サービス側に置き去りになり、「積読 → 資料 → AI → 知識」のループが閉じていない。

Phase 4 は、AI から得た成果物（本アプリでの利用者向け呼称は **AIノート**）をローカルに取り込み、生成元の資料と紐づけて保存し、蔵書横断検索の対象にする。ここでコンセプト「積読を知識に変える」が完結する。

## 2. 利用者の到達状態

- **Phase 4A**: AI サービスで得た回答や整理内容を貼り付けて保存でき、必要に応じて生成元のエクスポート履歴と紐づけられる
- **Phase 4B**: 保存した AIノートが蔵書横断検索で見つかり、検索結果から本文を読める
- **Phase 4C**: AIノートから元資料の該当箇所へ戻れ、資料側から関連 AIノートを辿れる

## 3. スコープと非スコープ

### スコープ

- Markdown テキストの貼り付けによる取り込み（手入力も同じテキストエリアで満たされる）
- エクスポート履歴（export_events）からの出典引き継ぎ
- 一覧・詳細表示・削除
- 蔵書横断検索への統合（4B）
- 出典明細から PDF プレビューへのリンク（4C）

### 非スコープ（今回見送り）

- 外部 AI サービス API による自動取得（ローカル完結の原則）
- ファイル読み込み（.md/.txt）による取り込み — 貼り付けで大半が満たせるため初期は見送り。要望が出たら `ws-import-json` と同じ `<input type="file">` パターンで追加できる
- 取り込み後の編集 UI — 初期実装は詳細表示と削除のみ
- 出典の多対多（1 つの AIノートを複数のエクスポートへ紐づける）
- manifest.json の ZIP 同梱と、そこからの取り込み
- 資料データ JSON エクスポートへの AIノート同梱
- AIノートの版管理（上書き履歴）

## 4. 用語

| 用語 | 意味 |
|---|---|
| AIノート | AI サービスから持ち帰った回答・整理内容。利用者向け呼称 |
| artifact | AIノートの内部名称（テーブル名・API パス・コードで使用） |
| 出典明細 | AIノートの生成元となった書籍・ページ範囲のスナップショット（artifact_sources） |
| エクスポート履歴 | export_events テーブルの記録。書き出した事実と当時の資料構成 |

## 5. Phase 4A / 4B / 4C の境界

| Phase | 含む | 含まない |
|---|---|---|
| 4A | テーブル追加・取り込み API・取り込み/一覧/詳細/削除の UI・export_events の読み出し | 検索統合・出典からのジャンプ |
| 4B | artifacts_fts・検索 kind「artifact」・検索スコープ・結果カード・本文表示 | 出典からのジャンプ |
| 4C | 出典明細から PDF プレビューへのリンク・資料一覧/資料棚から関連 AIノートへの導線 | — |

各 Phase は単独でリリース可能とし、完了時点でコミット・マージする（Phase 3 と同じ運用）。

## 6. DB スキーマ

```sql
CREATE TABLE IF NOT EXISTS artifacts (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    body TEXT NOT NULL,                   -- Markdown 原文
    source_service TEXT NOT NULL DEFAULT '',  -- 例: "ChatGPT" / "Claude" / "NotebookLM"。自由入力
    source_model TEXT NOT NULL DEFAULT '',
    prompt TEXT NOT NULL DEFAULT '',
    export_event_id INTEGER,              -- 生成元エクスポート（任意・FK制約なし）
    pack_id INTEGER,                      -- 文脈の補助参照（任意・FK制約なし）
    pack_name TEXT NOT NULL DEFAULT '',   -- 資料名スナップショット
    created_at TEXT NOT NULL,             -- UTC ISO8601
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifact_sources (
    id INTEGER PRIMARY KEY,
    artifact_id INTEGER NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
    pdf_path TEXT NOT NULL,
    title TEXT NOT NULL,                  -- 書名スナップショット
    pages TEXT NOT NULL,                  -- spec 文字列
    position INTEGER NOT NULL DEFAULT 0   -- 当時の並び順
);
```

4B で追加:

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS artifacts_fts USING fts5(
    artifact_id UNINDEXED,
    title,
    body,
    tokenize = 'unicode61'
);
```

### 設計判断

- **artifact_sources は必要か**: 必要。export_event_id だけでも 4A は動くが、(1) エクスポート履歴に紐づけず手動で取り込む AIノートにも出典を持たせられる、(2) export_events の行が将来的に欠落（DB 移行・手動削除）しても出典明細が AIノート側に残る、(3) 4C のページジャンプは明細テーブルを直接参照する方が単純、の 3 点から二段構え（export_event_id 参照 + 明細複製）とする。事前調査 §3.2 の結論どおり
- **最小スキーマとの関係**: 「1 artifact に 1 export_event」は artifacts.export_event_id（nullable 単一列）で表現する。関連テーブルは作らない
- **将来の多対多化**: 必要になったら `artifact_export_events(artifact_id, export_event_id)` 関連テーブルを追加し、既存列は「主イベント」として残す。現行スキーマは多対多化を妨げない
- **外部キーと削除時の挙動**: artifact_sources → artifacts のみ FK（ON DELETE CASCADE）。artifacts.export_event_id / pack_id には FK を張らない（export-events-design.md §9 と同じ思想。履歴・ノートは資料のライフサイクルから独立している方に価値がある）。export_event や pack が消えても AIノート本文・出典明細はそのまま残り、表示時に「エクスポート履歴なし」と扱う
- **books テーブルに相乗りしない**: books.source_type には CHECK 制約があり（事前調査 §2.1）、AIノートは蔵書ではない。memos とも性質が違う（memos は Scrapbox 同期で全置換される揮発キャッシュ、AIノートはローカルが原本）。独立テーブルとする

### export_events との関係

- 取り込み時に export_event を選ぶと、その items_json（version 1: pdf_path / title / pages / position）を artifact_sources へ複製し、export_event_id と pack_id・pack_name も記録する
- export_events 側は変更しない（記録専用の現行仕様を維持）。読み出し関数（最近の履歴一覧・単一取得）を新設する
- 複製後は artifact_sources が正。export_events の後変更・欠落の影響を受けない

## 7. API

すべて JSON。既存 `/api/packs` 系の作法（`JSONResponse` / `HTTPException`）に合わせる。

| メソッド・パス | 内容 | Phase |
|---|---|---|
| `GET /api/export-events?limit=20` | 最近のエクスポート履歴一覧（exported_at 降順）。取り込み UI の出典選択用 | 4A |
| `POST /api/artifacts` | AIノート作成。body: `{title, body, source_service?, source_model?, prompt?, export_event_id?}` | 4A |
| `GET /api/artifacts` | 一覧（id・title・source_service・pack_name・出典冊数・created_at。body は含めない） | 4A |
| `GET /api/artifacts/{id}` | 詳細（本文・出典明細・エクスポート履歴参照を含む） | 4A |
| `DELETE /api/artifacts/{id}` | 削除（artifact_sources は CASCADE） | 4A |

### バリデーション

- `title`: 必須。空白のみは 400。上限 200 文字
- `body`: 必須。空白のみは 400。上限は設けない（SQLite TEXT）
- `export_event_id`: 指定時に該当行がなければ 400（「エクスポート履歴が見つかりません」）。省略・null 可
- `source_service` / `source_model` / `prompt`: 任意。既定は空文字
- 不明なフィールドは無視（既存 API と同じ寛容さ）

### エラー時の挙動

- 404: artifact が存在しない（詳細・削除）
- 400: バリデーション違反。メッセージは日本語で具体的に
- 取り込み成功時に artifact_sources の複製が失敗した場合はトランザクションごとロールバック（本文だけ保存されて出典が欠ける中途半端な状態を作らない）

### デモモード制約

`DEMO_MODE` 有効時、`POST /api/artifacts` と `DELETE /api/artifacts/{id}` は 403 と `DEMO_MODE_SETTING_MESSAGE` を返す。読み出し系（一覧・詳細・export-events）は許可する。

## 8. UI 導線

### 画面: AIノート一覧（`/artifacts`、4A）

- ナビゲーションを「資料一覧｜資料棚｜AIノート｜設定」の 4 タブへ拡張する
- 画面構成は資料一覧（pack_list.html）のパターンを踏襲: サーバーレンダリングのシェル + JS で `/api/artifacts` を取得して描画
- 一覧行: タイトル・取り込み元サービス・生成元資料名・出典冊数・取り込み日
- 行の「開く」で同画面内の詳細パネル（本文 Markdown をテキストとして表示・出典明細一覧・削除ボタン）を開く。専用の詳細ページは作らない
- 削除は確認ダイアログ付き（資料一覧の削除と同じ作法）

### 取り込みフォーム（同画面内、4A）

- 「AIノートを追加」ボタンでフォーム表示: タイトル・本文（テキストエリア、貼り付け前提）・取り込み元サービス（datalist によるプルダウン + 自由入力: ChatGPT / Claude / Gemini / NotebookLM）・モデル名（任意）・プロンプト（任意・折りたたみ）
- 「生成元のエクスポート」セレクト: `/api/export-events` の一覧を「資料名 / profile / 書き出し日時」で表示。「紐づけない」を既定にする
- 保存成功で一覧を再描画し、状態メッセージ表示（`role="status"` / `aria-live="polite"`。Phase 2 総点検で指摘した実装差を増やさないよう、pack_list.html の setStatus と同じ作法にする）

### 検索統合（4B）

- ホーム（検索）のスコープに「AIノート」を追加
- 検索結果カード: kind=artifact 用のカードを追加（タイトル・スニペット・取り込み元サービス）。クリックで `/artifacts` の該当ノート詳細を開く（`/artifacts#note-{id}` 等のフラグメント遷移）

### 出典への往復（4C）

- 詳細パネルの出典明細各行に「この本を開く」リンク: 既存の PDF プレビュー（`/view/{pdf_path}`）へ、該当ページ範囲の先頭ページを指定して遷移
- PDF 実体が解決できない場合は「本が見つかりません」を行内表示（pack-design.md の残 TODO と同じ扱い）
- 資料一覧の各行に、その資料（pack_id 一致）から生まれた AIノート件数と `/artifacts` への絞り込みリンクを表示

## 9. 検索統合方針（4B）

- `SEARCH_SCOPES` に `artifact` を追加し、`search()` に memo と同じ流儀の分岐を追加する（`_search_artifact_fts` + LIKE フォールバック）
- kind は `artifact`。SearchResult の既存フィールドで表現し（title / snippet / open_url=`/artifacts#note-{id}`）、SearchResult 型の変更は最小限にする
- **日本語部分一致は memos と同じ方式**: memos_fts（unicode61）+ Sudachi 分かち書きクエリ + LIKE フォールバックの組み合わせを踏襲する。専用の trigram テーブルは張らない（事前調査 §7-6 の「memos の現状に合わせる」を採用。なお memos に trigram テーブルは存在せず、部分一致は LIKE フォールバックで実現している。trigram 追加は将来 memos と同時に検討する）
- FTS 行の同期: artifacts の INSERT / DELETE 時に artifacts_fts を更新する（memos の全置換方式とは異なり、個別更新。AIノートはローカル原本で全置換の機会がないため）

## 10. マイグレーション方針

- `CREATE TABLE IF NOT EXISTS` による冪等スキーマ作成。既存の initialize / connect 経路で ensure_pack_schema と同様に毎回実行する（専用マイグレーションなし。export_events と同じ方式）
- 既存テーブルへの変更は一切ない。既存 366 件のテストに影響しない
- 4B の artifacts_fts も同方式で追加する

## 11. テスト方針

### Phase 4A

- DB 層（test_database.py）: artifacts / artifact_sources の作成・取得・削除、CASCADE 動作、スキーマ冪等性（二重 initialize）、export_events 読み出しの並び順・limit・items_json の複製
- Web 層（test_web.py）: POST の正常系（出典なし / export_event_id 指定で artifact_sources へ複製）、バリデーション 400 各種、存在しない export_event_id、GET 一覧・詳細、DELETE、404、デモモードで書き込み 403・読み出し許可、ロールバック（複製失敗時に artifacts 行が残らない）
- 規模感: Python +20〜30 件

### Phase 4B

- DB 層: kind=artifact のヒット、スコープ artifact / all、日本語部分一致（LIKE フォールバック）、既存 kind との混在結果
- Playwright: 取り込み → 検索 → ヒット → 詳細表示の一連（+1〜2 件）

### Phase 4C

- Web 層: 出典リンクの生成、PDF 欠落時の挙動
- Playwright: 詳細パネルから PDF プレビューへの遷移（+1 件）

## 12. 実装順序

1. **4A-1: DB 層のみ** — スキーマ + CRUD 関数 + export_events 読み出し関数 + test_database.py。UI・API 配線なしで完結し、単独でレビュー・マージ可能
2. **4A-2: API** — /api/artifacts 系 + /api/export-events + test_web.py
3. **4A-3: UI** — /artifacts 画面 + ナビ拡張 + 手動確認
4. **4A-4: 文書** — ROADMAP・本設計書の状態更新
5. 4B、4C も同様に「DB/検索 → API/UI → 文書」の順で刻む

## 13. 完了条件

### Phase 4A

- AIノートを貼り付けて保存でき、エクスポート履歴を選ぶと出典明細が複製される
- 一覧・詳細・削除が /artifacts 画面で完結する
- デモモードで書き込みが 403 になる
- 既存テスト全件 + 追加テストが通過する

### Phase 4B

- 蔵書横断検索のスコープ「AIノート」と all で AIノートがヒットし、結果カードから本文へ到達できる

### Phase 4C

- AIノートの出典明細から該当 PDF プレビューへ遷移できる
- 資料一覧から関連 AIノートへ辿れる

## 14. 将来拡張（見送り事項の再掲と拡張余地）

- ファイル読み込み（.md/.txt）取り込み
- 出典の多対多（artifact_export_events 関連テーブルの追加で対応可能）
- 取り込み後の編集 UI
- manifest.json 同梱と別マシン生成 ZIP からの取り込み
- 資料データ JSON への同梱
- trigram による部分一致強化（memos と同時に検討）
- Phase 5 での活用: export_events × artifacts で「書き出した → 帰ってきた」の対応が取れ、消化マップ・共起のシグナルになり得る
