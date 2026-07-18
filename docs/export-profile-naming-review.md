# 「NotebookLM対応」名称見直し 調査メモ

作成: 2026-07-19 / 状態: 調査のみ（実装未着手）

## 方針

つんどけんさくは、特定のAIサービス向けツールではなく「利用者が所有する書籍を検索・整理・構造化して活用するツール」である。この立ち位置を明確にするため、「NotebookLM向け」という名称を機能名・見出し・説明文では使用しない。NotebookLMは「利用例の一つ」として扱う。

調査時点の対象: リポジトリ全体で NotebookLM 関連表現は 296箇所・22ファイル。

## 1. 現状の構造（重要な前提）

エクスポートモーダルのUIは既に「**宛先**（利用例）と **profile**（書き出し方法）」の2層分離を実装済みである。

- 宛先 ChatGPT / Claude → profile `chat`（サービス中立な名前）✅
- 宛先 NotebookLM → profile `notebooklm`（サービス名がそのまま機構名）❌

つまり `chat` が前例となる。ChatGPT / Claude という2サービスを「会話AI向けMarkdown分冊」という機構名に既に抽象化しており、`notebooklm` だけが今回の方針に反している。README冒頭・ROADMAPコンセプト行も既に「NotebookLM や ChatGPT など」の例示スタイルを採用しているため、今回の方針は既存の設計思想・ユーザーストーリーと整合し、矛盾はない。

## 2. 影響範囲の分類

### A. 機能名・識別子としての使用（変更対象の本丸）

- `src/tsundokensaku/export_profiles.py` — `NotebookLMProfile` クラス、`name = "notebooklm"`、定数 `NOTEBOOKLM_MAX_PAGES_PER_FILE_DEFAULT`（300）/ `NOTEBOOKLM_MAX_SOURCES_DEFAULT`（50）/ `NOTEBOOKLM_ESTIMATED_CHARS_WARNING_GUIDELINE`（40万字）、環境変数 `TSUNDOKENSAKU_NOTEBOOKLM_MAX_PAGES_PER_FILE` / `TSUNDOKENSAKU_NOTEBOOKLM_MAX_SOURCES`
- `src/tsundokensaku/web.py:87` — API受理値 `EXTERNALLY_AVAILABLE_EXPORT_PROFILES = {"standard", "chat", "notebooklm"}` と、`profile.name == "notebooklm"` の文字列比較による分岐4箇所
- `src/tsundokensaku/zip_export.py:119` — `build_notebooklm_filename()`
- ZIP名 — `{資料名}_notebooklm_{日付}.zip`（`archive_filename` が `self.name` を埋め込む）
- `templates/workspace.html:153` — 選択肢 `{ id: 'notebooklm', profile: 'notebooklm', label: 'NotebookLM', ... }`
- DB — `export_events.profile` 列に `"notebooklm"` が永続記録される（履歴表示に出る）

### B. 説明文・見出しでの「NotebookLM向け」（変更対象）

- `README.md:151` — 見出し「NotebookLM用にページを抜き出す」
- `ROADMAP.md:79-81` — 見出し「Phase 3D: NotebookLMへそのまま渡せる」
- manifest内の行「- NotebookLM向けのPDFです」（`export_profiles.py` の `manifest_header_lines`）
- 警告文「出力ファイル数がNotebookLMのソース数目安（50件）を超えています」

### C. 利用例としての言及（方針上そのままでよい）

- `README.md:8`「ChatGPT や Claude、NotebookLM などの外部 AI」
- `ROADMAP.md:8` コンセプト行、`submission.md`（コンテスト提出文書・歴史的記録）
- `ARCHITECTURE.md:16`「NotebookLM等へ渡すページ切り出し」、`scripts/README.md`「NotebookLMなどのAIツールに」
- `zip_export.py` manifest文「NotebookLM等にアップロードする場合、上記n個のファイルがそれぞれ1ソースになります」

いずれも既に例示形。ただし `.env.example:10` と `templates/settings_index.html:118` の placeholder `/path/to/NotebookLM-folder` は汎用パス例への変更を推奨（軽微）。

### D. 歴史的設計文書（変更しない）

`docs/ai-export-optimization-design.md`（75箇所）、`docs/phase3c-3d-design-review.md`（13箇所）、discovery / notes 類は当時の判断記録であり、書き換えると判断根拠（ページ300上限の由来など）が追えなくなる。冒頭に「profile名は後に改名した」旨の注記1行のみ追加する。

## 3. 機能名の提案

profileの実体は「章単位分割（アウトラインがない場合は連続ページ分割 + part番号）+ 隣接同一書籍の結合 + ソース数・文字数警告 + PDF出力」。

- **推奨: `chapter` ／ 日本語表示「章単位PDF」／ 英語 Chapter Split Export**
  - 既存の `standard`・`chat` と同じ一語小文字スタイル
  - Phase 3D の設計見直し自体が「章単位分割中心の設計」であり、自らの設計言語と一致する
  - ZIP名 `{資料名}_chapter_{日付}.zip` が自然
- 次点: `section`（アウトラインなしのフォールバック分割も包含する語だが、章より訴求が弱い）
- 非推奨: `structured`（曖昧で standard との差が不明瞭）、`pdf_split`（形式名で利用者価値が見えない）

UIの選択肢は「渡す先」ではなく機能主体へ変更する。

```
label: '章単位PDF'
description: '大きな資料項目を章などの単位に分割したPDF。NotebookLMなどソース読み込み型AIに向いています。'
```

サービス名は説明文内の例示に降格する。

## 4. 後方互換性の比較

| 面 | ハードリネーム | エイリアス維持 |
|---|---|---|
| API `?profile=notebooklm` | 即400 | 旧値を新profileへ解決 |
| DB履歴の旧値 | 表示名マッピングで吸収（行は書き換え不要） | 同左 |
| 環境変数 | 旧名は無視（設定者は自分のみ） | 旧名フォールバック |
| ZIP名の変化 | あり（利用者影響は実質なし） | — |

**推奨: ほぼハードリネーム + 最小限の吸収。**

理由: ローカル個人ツールであり、APIクライアントはWeb UIのみ、環境変数の設定者も利用者自身。

1. API・profile名・クラス名・環境変数を一括改名（例: `TSUNDOKENSAKU_CHAPTER_MAX_PAGES_PER_FILE` / `TSUNDOKENSAKU_CHAPTER_MAX_FILES`）
2. `export_events.profile` の既存 `"notebooklm"` 行は書き換えず、履歴表示時に「notebooklm → 章単位PDF（旧名）」の表示名マッピングで吸収。マイグレーション不要
3. API旧値 `notebooklm` の受理エイリアスは不要（外部クライアント不在）。必要ならリダイレクトマップ1行で足りる
4. ついでの改善候補: `web.py` の `profile.name == "notebooklm"` 文字列分岐4箇所を、profileの能力フラグ（`needs_chapter_loader` など）へ寄せると、今後の改名・profile追加に強くなる

## 5. 警告文・manifest の文言案

- 警告「出力ファイル数がNotebookLMのソース数目安（50件）を超えています」→「出力ファイル数が上限目安（50件）を超えています。読み込み先サービス（NotebookLM無料枠など）の上限を確認してください」
- manifest「NotebookLM向けのPDFです」→「章などの単位で分割したPDFです」
- 「NotebookLM等にアップロードする場合、上記n個のファイルがそれぞれ1ソースになります」は既に例示形のため維持可

## 6. README・ROADMAP 変更案

- `README.md:151` 見出し「NotebookLM用にページを抜き出す」→「必要なページだけを小さなPDFに切り出す」。本文は「NotebookLMなどに渡したいとき**にも**使えます」と例示に降格
- ROADMAP Phase 3D 見出し「NotebookLMへそのまま渡せる」→「章単位で分割したPDFとして書き出せる」。到達状態は「…分割されたPDFとして書き出せる。NotebookLMなどソース読み込み型AIへの受け渡しが利用例」
- `ROADMAP.md:8` のコンセプト行は例示形のため維持

## 7. 変更ファイル一覧

必須（機能名・識別子・説明文）:

1. `src/tsundokensaku/export_profiles.py` — クラス・name・定数・環境変数・警告・manifest行
2. `src/tsundokensaku/web.py` — 受理セット・分岐4箇所・コメント
3. `src/tsundokensaku/zip_export.py` — `build_notebooklm_filename` の改名・コメント
4. `templates/workspace.html` — 選択肢の id / profile / label / description
5. `README.md` — 見出し1件・説明文
6. `ROADMAP.md` — Phase 3D 見出し・到達状態・実装記述
7. `tests/test_export_profiles.py`（115箇所）・`tests/test_web.py`（23箇所）・`tests/test_zip_export.py`（14箇所） — 機械的追従

任意（例示の質を上げる）:

8. `.env.example`・`templates/settings_index.html` — placeholder の汎用化
9. `docs/ai-export-optimization-design.md` ほか設計文書 — 冒頭注記1行のみ
10. 履歴表示箇所（`workspace.html` / `web.py`） — 旧profile値の表示名マッピング

変更不要: `submission.md`（提出済み文書）、`ARCHITECTURE.md`・`scripts/README.md`・`scripts/export_pdf_pages.py`（例示形）、discovery / notes 系 docs（歴史的記録）。

## 未決事項

最終的なprofile名は実装開始前に確定する。

候補
- chapter
- section
- outline
- segmented
