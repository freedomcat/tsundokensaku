# profile リネーム実装計画（安全チェックリスト）

作成: 2026-07-19 / 正本: [export-profile-naming-review.md](export-profile-naming-review.md) / 状態: 計画のみ（実装未着手）

新しい profile 名は **`chapter`**（表示名「章単位PDF」、英語 Chapter Split Export）に確定済み（2026-07-19、正本の「決定事項」を参照）。

## 前提（調査で確定した事実）

- エクスポート履歴（`export_events`）は記録のみで、履歴を表示するUIはまだ存在しない。よって旧値 `"notebooklm"` の表示名マッピングは現時点では不要。DBの既存行は書き換えない
- APIクライアントはWeb UIのみ。`?profile=notebooklm` の受理エイリアスは設けない（ハードリネーム）
- 環境変数の設定者は利用者自身のみ。旧名フォールバックは設けず、ドキュメントで案内する

## 1. 実装順序

### Step 1: web.py の profile 名分岐を能力フラグへ寄せる（動作不変）

- **変更対象**: `src/tsundokensaku/export_profiles.py`（基底クラスに能力フラグ追加）、`src/tsundokensaku/web.py`
- **変更内容**: `profile.name == "notebooklm"` の文字列比較（プレビューの chapter_loader 生成、エクスポート本体の chapter_loader 生成、PlanManifestChunk 分岐）と `profile.name == "standard"` 分岐を、profile の属性（例: `needs_chapter_loader: bool`、`uses_plan_manifest: bool`）へ置き換える。名前文字列への依存を web.py から消し、後続の改名が web.py に波及しない状態にする
- **完了条件**: `grep -n '== "notebooklm"' src/` が 0 件。全テストが変更なしで通過（挙動不変の証明）
- **関連テスト**: `tests/test_web.py`（standard / chat / notebooklm 全プロファイルの既存テストがそのまま通ること。テスト修正が必要になったら挙動が変わっているサイン）

### Step 2: zip_export.py の内部関数リネーム（動作不変）

- **変更対象**: `src/tsundokensaku/zip_export.py`、`src/tsundokensaku/export_profiles.py`（import と呼び出し）、`tests/test_zip_export.py`
- **変更内容**: `build_notebooklm_filename()` → `build_chapter_filename()`。生成されるファイル名文字列自体は変えない
- **完了条件**: `grep -rn build_notebooklm_filename src tests` が 0 件。全テスト通過
- **関連テスト**: `tests/test_zip_export.py`（14箇所）、`tests/test_export_profiles.py` の filename 系

### Step 3: profile 本体のリネーム（外部挙動が変わる中心ステップ）

- **変更対象**: `src/tsundokensaku/export_profiles.py`、`src/tsundokensaku/web.py:87`、`templates/workspace.html`（profile 送信値のみ）、`tests/test_export_profiles.py`、`tests/test_web.py`
- **変更内容**:
  - `NotebookLMProfile` → `ChapterProfile`、`name = "notebooklm"` → `"chapter"`
  - 定数 `NOTEBOOKLM_MAX_PAGES_PER_FILE_DEFAULT` / `NOTEBOOKLM_MAX_SOURCES_DEFAULT` / `NOTEBOOKLM_ESTIMATED_CHARS_WARNING_GUIDELINE` → `CHAPTER_...`
  - 環境変数 `TSUNDOKENSAKU_NOTEBOOKLM_MAX_PAGES_PER_FILE` / `TSUNDOKENSAKU_NOTEBOOKLM_MAX_SOURCES` → `TSUNDOKENSAKU_CHAPTER_...`
  - `EXTERNALLY_AVAILABLE_EXPORT_PROFILES` の `"notebooklm"` → `"chapter"`
  - `workspace.html:153` の `profile: 'notebooklm'` → `profile: 'chapter'`（**同一コミット必須**。API受理値と送信値がずれると書き出しが即400になる）
  - 警告文「出力ファイル数がNotebookLMのソース数目安…」→「出力ファイル数が上限目安（n件）を超えています。読み込み先サービス（NotebookLM無料枠など）の上限を確認してください」
  - manifest 行「NotebookLM向けのPDFです」→「章などの単位で分割したPDFです」
  - コメント内の notebooklm 言及（export_profiles.py / web.py / zip_export.py）を追従
  - 副作用: ZIP名が `{資料名}_chapter_{日付}.zip` に変わる（意図した変更）
- **完了条件**: `grep -rni notebooklm src/ templates/` が 0 件。全テスト通過。手動でモーダルから書き出し成功
- **関連テスト**: `tests/test_export_profiles.py`（115箇所を機械的追従）、`tests/test_web.py`（23箇所。profile パラメータ・警告文言・ZIP名のアサーション）

### Step 4: UI 表示文言の変更

- **変更対象**: `templates/workspace.html`（選択肢の `id` / `label` / `description`）
- **変更内容**: `{ id: 'notebooklm', label: 'NotebookLM', description: '大きな資料項目を章などの単位に分割し、PDFとして書き出します。' }` → `{ id: 'chapter', label: '章単位PDF', description: '大きな資料項目を章などの単位に分割したPDF。NotebookLMなどソース読み込み型AIに向いています。' }`
- **完了条件**: モーダルの選択肢に機能名が表示され、サービス名は説明内の例示のみ。選択→プレビュー→書き出しが動作
- **関連テスト**: `tests/test_web.py` のテンプレート文字列検査があれば追従。基本は手動確認（Playwright E2E は Phase 3E で追加予定）

### Step 5: ドキュメント更新

- **変更対象**: `README.md`、`ROADMAP.md`、`.env.example`、`templates/settings_index.html`、`docs/ai-export-optimization-design.md`（注記のみ）、`docs/phase3c-3d-design-review.md`（注記のみ）
- **変更内容**:
  - README:151 見出し「NotebookLM用にページを抜き出す」→「必要なページだけを小さなPDFに切り出す」、本文を例示形へ
  - ROADMAP Phase 3D 見出し「NotebookLMへそのまま渡せる」→「章単位で分割したPDFとして書き出せる」、到達状態も例示形へ
  - ROADMAP:115 優先順位要約「次は 3D」→ 3D 完了済みへ修正（既知の不整合、ついでに直す）
  - `.env.example` / `settings_index.html` の placeholder `/path/to/NotebookLM-folder` → 汎用パス例
  - 歴史的設計文書2本の冒頭に「profile名 notebooklm は後に `chapter` へ改名（[export-profile-naming-review.md](export-profile-naming-review.md)）」の注記1行
- **完了条件**: 下記「4. 完了チェック」の grep 許可リスト条件を満たす
- **関連テスト**: なし（ドキュメントのみ）

## 2. コミット単位

5コミットに分割する。各コミット後に全テスト通過を確認してから次へ進む。

1. `refactor: エクスポートprofileの名前分岐を能力フラグへ寄せる`（Step 1 — 動作不変）
2. `refactor: build_notebooklm_filenameを汎用名に変更する`（Step 2 — 動作不変）
3. `feat: notebooklmプロファイルをchapterに改名する`（Step 3 — API値・env・ZIP名・警告文言・UI送信値。**workspace.html の profile 値を必ず含める**）
4. `feat: エクスポート先選択を機能名主体の表示に変更する`（Step 4 — UI文言のみ）
5. `docs: エクスポート機能の説明からサービス名を機能名へ置き換える`（Step 5）

Step 3 が最大だが、これ以上分割すると中間状態で UI が壊れるため分けない。Step 3+4 を1コミットにまとめるのは可（表示と値の変更を一度にレビューしたい場合）。

## 3. リスク

### Step 1

- **壊れやすい箇所**: `standard` の早期リターン（プレビューAPI）と manifest のバイト互換分岐。フラグ化の際に standard / chat の経路を変えてしまうと既存出力が変わる
- **リネーム漏れが起こりそうな箇所**: プレビューAPI側の `== "notebooklm"`（web.py:1383。エクスポート本体側と2系統あることを忘れやすい）
- **テストで確認すべき点**: 3プロファイル全部のプレビューAPI・エクスポートAPIの既存テストが**無修正で**通ること

### Step 2

- **壊れやすい箇所**: `_build_capped_filename` の detail_candidates 順序と255バイト切り詰め。関数名だけ変えるつもりで中身に触れないこと
- **リネーム漏れが起こりそうな箇所**: export_profiles.py 冒頭の import 行
- **テストで確認すべき点**: 生成ファイル名のアサーションが1文字も変わらないこと

### Step 3

- **壊れやすい箇所**: `EXTERNALLY_AVAILABLE_EXPORT_PROFILES` と `workspace.html` の profile 値の不一致（即400）。ZIP名アサーションを持つテスト
- **リネーム漏れが起こりそうな箇所**:
  - テスト内の文字列リテラル `"notebooklm"`（合計150箇所前後。機械置換後に文脈確認）
  - 環境変数名（テストの env 上書きテストが旧名のまま残ると「デフォルト値のテスト」に化けて静かに意味が変わる — 最重要）
  - docstring・コメント内の言及（export_profiles.py:35, 151, 184, 263 / web.py:1518, 1561 / zip_export.py:206）
  - 警告 `code`（`too_many_sources` 等）は挙動仕様のため**変えない**
- **テストで確認すべき点**: env 上書きテストが新変数名で実際に効いていること（旧名で設定して効かないことも1本確認できると堅い）。旧 profile 値 `?profile=notebooklm` が 400 になること（意図した破壊の明文化）

### Step 4

- **壊れやすい箇所**: `selectedExportDestination()?.profile` の参照。`id` を変えると選択状態の保存・復元（あれば）に影響
- **リネーム漏れが起こりそうな箇所**: 成功メッセージ・ステータス表示文字列に宛先名を埋め込んでいる箇所
- **テストで確認すべき点**: 手動でモーダル開閉→選択→プレビュー→書き出し→ZIP名と manifest 内容

### Step 5

- **壊れやすい箇所**: なし（ドキュメントのみ）
- **リネーム漏れが起こりそうな箇所**: README の行番号ずれ（見出しだけでなく本文の説明も直す）。ROADMAP:115 の「次は 3D」
- **テストで確認すべき点**: なし。grep 許可リストで確認

## 4. 完了チェック

全 Step 完了後、以下を順に実施する。

- [ ] **grep 残存確認**: `grep -rni notebooklm . --exclude-dir=.git` の残存が以下の**許可リストのみ**であること
  - README.md:8（例示）・README の切り出し節の例示1箇所
  - ROADMAP.md:8（コンセプト行の例示）・Phase 3D 到達状態の例示
  - submission.md（提出済み文書、3箇所）
  - ARCHITECTURE.md:16 / scripts/README.md / scripts/export_pdf_pages.py（例示形）
  - docs/ の歴史的設計文書（design / review / discovery / notes 類）と本計画・正本
  - zip_export.py の manifest 例示文・export_profiles.py の警告例示文・workspace.html の description 例示（「NotebookLMなど」の形のみ）
- [ ] **識別子の完全消滅確認**: `grep -rn "NOTEBOOKLM\|NotebookLMProfile\|build_notebooklm" src/ tests/ templates/` が 0 件
- [ ] **全テスト**: `make test` 全通過（ユニットテスト一式）
- [ ] **手動確認**: 資料棚モーダルから `章単位PDF` を選択 → プレビュー（警告表示含む）→ 書き出し → ZIP名 `{資料名}_chapter_{日付}.zip`・manifest 冒頭行・分割ファイル名を確認
- [ ] **README 確認**: 通読し、機能説明が「章単位・構造単位のエクスポート」主体、サービス名が例示のみであること
- [ ] **ROADMAP 確認**: Phase 3D の見出し・到達状態が機能主体、優先順位要約の不整合（「次は 3D」）が解消されていること
- [ ] **実環境の env 確認**: `.env` に旧環境変数 `TSUNDOKENSAKU_NOTEBOOKLM_*` を設定していれば新名へ移行
- [ ] **正本の更新**: 本計画のチェックボックスを埋めて完了記録とする（確定名は正本の「決定事項」に反映済み）
