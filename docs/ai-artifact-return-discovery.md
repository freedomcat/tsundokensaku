# Phase 4「AI成果物の帰還」事前調査

作成: 2026-07-12
状態: 調査・要求整理（実装なし・設計確定前）
前提: [ROADMAP.md](../ROADMAP.md) Phase 4 / [ai-export-optimization-design.md](ai-export-optimization-design.md) / [phase3c-3d-design-review.md](phase3c-3d-design-review.md)

> **位置付け**: 本文書は Phase 4 に向けた調査・要求整理の記録であり、エクスポート履歴の現行仕様の正本ではない。確定仕様は [export-events-design.md](export-events-design.md) を参照。

## 1. 目的

Phase 4 は「AI から得た回答・ノートを取り込み、生成元の資料に紐づけて保存し、蔵書横断検索の対象にする」段階。ここで初めて「積読 → 資料 → AI → 知識」のループが閉じる。本文書は実装前に、取り込み方法・データモデル候補・既存機構との責務境界・Phase 3 側で先に仕込むべきことを整理する。

## 2. 現行実装の関連調査結果

### 2.1 検索対象ソースの実装パターン（Phase 4 の前例になる）

現在の蔵書横断検索は 3 種類のソースを別々の機構で持つ。

| ソース | テーブル | FTS | 検索スコープ |
|---|---|---|---|
| PDF 本文 | `pages(book_id, page_number, text)` | `pages_fts` + `pages_trigram` | body |
| 書籍タイトル（PDF/Kindle） | `books`（`source_type` は CHECK 制約で `'pdf','kindle'` のみ） | `books_fts` | title |
| Scrapbox メモ | `memos(title, body, scrapbox_url, cover_url)` | `memos_fts` | memo |
| 書籍ノート | `book_notes(book_id, title, body, ...)` | `book_notes_fts` | memo 系 |

重要な発見: **`books.source_type` には CHECK 制約があり、'artifact' のような新種を books に相乗りさせるにはテーブル再作成マイグレーションが必要**。一方、`memos` は books と独立した専用テーブル + 専用 FTS + 検索スコープという構成で、新ソース追加の前例として最も再利用しやすい。

### 2.2 資料項目の出典情報

`pack_items(id, pack_id, pdf_path, title, pages, collapsed, position, added_at, updated_at)`。`title` は追加時点のスナップショット、`pdf_path` は文字列参照（books 行が消えても残る）。ただし **pack_items は現在形のみ**で、項目の削除・変更で過去の構成は失われる。エクスポート時点の構成を後から復元する手段は現状ない。

### 2.3 Phase 3 の manifest

`manifest.md` は人間向けの一覧（書名・ページ範囲・ファイル名）で、機械可読な構造化データではない。Phase 3C/3D で導入予定の plan 由来 manifest も Markdown のまま。

## 3. 取り込み方法の要求整理

### 3.1 入力手段の優先順位（案）

1. **Markdown 貼り付け**（テキストエリアへペースト）— 最優先。ChatGPT/Claude/NotebookLM いずれも回答を Markdown/テキストでコピーでき、追加ソフトなしで完結する。既存 UI 資産（`/settings` のアップロード類・資料棚のモーダル）と同じ作法で作れる
2. **ファイル読み込み**（.md / .txt）— 次点。NotebookLM のノートエクスポートや、長い成果物の受け皿。既存の JSON インポート（`ws-import-json`）と同じ `<input type="file">` パターン
3. **手入力** — 貼り付けと同じテキストエリアで自然に満たされるため、専用機能は不要

外部 API による自動取得は対象外（ローカル完結の原則。AI サービスへの接続は行わない）。

### 3.2 元資料との紐づけ

紐づけの候補キーを評価する。

| 候補 | 耐性 | 評価 |
|---|---|---|
| `pack_id` | 資料の削除・改名・項目変更で意味が変わる/消える | 参照として弱い。補助情報に留める |
| `pack_item_id` | 項目削除で dangling。項目の pages 変更で「当時渡した範囲」とズレる | 生参照は不適 |
| `pdf_path` + ページ範囲（スナップショット） | 本の削除でも文字列として残る。改名・再インデックスの影響なし | **主キーに適する**（pack_items の出典設計と同じ思想） |
| エクスポートスナップショット参照 | エクスポート時点の構成を丸ごと保持 | **最も再現性が高い**。後述のイベント記録が前提 |

結論（案）: 成果物の出典は **「エクスポート時点のスナップショット（あれば）」+「pdf_path + pages spec + title スナップショットの明細」** の二段構えにする。live な `pack_id`/`pack_item_id` は「どの資料の文脈だったか」を示す補助（nullable、削除されたら NULL のまま表示だけ工夫）とする。

### 3.3 スナップショットの必要性

必要。理由:

- 資料は使い回す（項目の追加・削除・範囲変更が日常操作）。成果物が生成された時点の「AI に何を渡したか」は、現在の pack_items からは復元できない
- 同じ資料から複数回生成した場合の履歴比較（3.4）にもスナップショットが要る

スナップショットの発生点はエクスポート実行時が自然である。これは「書き出した」事実を記録するもので、AI に実際に渡したかどうかは示さない。**[export-events-design.md](export-events-design.md) のエクスポート履歴と同一のデータで満たせる**ため、テーブルを分けず共用する（§5）。

### 3.4 同一資料からの複数成果物

同じ資料（同じ/違うスナップショット）から複数の成果物が生まれる。1 成果物 = 1 行とし、`export_event_id`（nullable）で束ねる。「この資料から生まれた成果物一覧」は pack_id 補助列 or スナップショット経由で辿る。版管理（成果物の上書き履歴）は初期実装では持たない（編集したら updated_at 更新のみ）。

### 3.5 AI サービス名・モデル名・プロンプト

- `source_service`（例: "ChatGPT" / "Claude" / "NotebookLM"）: 任意入力の短い文字列。プルダウン + 自由入力
- `source_model`: 任意。空でよい
- `prompt`: 任意。保存すると再現性が上がるが、入力の手間が増える。**必須にしない**。プライバシー面はローカル SQLite 完結のため新たな懸念はないが、デモモード（`DEMO_MODE`）では成果物取り込み自体を無効化する（既存のアップロード無効化と同じ扱い）

## 4. データモデル候補

### 4.1 新規テーブル案

`export_events` は [export-events-design.md](export-events-design.md) で仕様確定済みであり、同文書を正本とする。以下の `export_events` 定義は、Phase 4 の候補モデルとの関係を検討した当時の案であり、`items_json` の `version` などを欠くため現在の確定仕様ではない。

```sql
-- エクスポートイベント（Phase 5 の利用履歴と共用。usage-history-discovery.md 参照）
CREATE TABLE export_events (
    id INTEGER PRIMARY KEY,
    exported_at TEXT NOT NULL,            -- UTC ISO8601
    pack_id INTEGER,                      -- 参照補助（資料削除後は dangling を許容）
    pack_name TEXT NOT NULL,              -- スナップショット
    profile TEXT NOT NULL,                -- 'standard' | 'chat' | 'notebooklm'
    format TEXT NOT NULL,                 -- 'pdf' | 'md' | 'json'
    items_json TEXT NOT NULL              -- [{pdf_path, title, pages, position}, ...]
);

-- AI 成果物
CREATE TABLE artifacts (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    body TEXT NOT NULL,                   -- Markdown 原文
    source_service TEXT NOT NULL DEFAULT '',
    source_model TEXT NOT NULL DEFAULT '',
    prompt TEXT NOT NULL DEFAULT '',
    export_event_id INTEGER,              -- 生成元スナップショット（任意）
    pack_id INTEGER,                      -- 文脈の補助参照（任意）
    created_at TEXT NOT NULL,             -- 取り込み日時
    updated_at TEXT NOT NULL
);

-- 成果物の出典明細（スナップショットから複製 or 手動指定）
CREATE TABLE artifact_sources (
    id INTEGER PRIMARY KEY,
    artifact_id INTEGER NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
    pdf_path TEXT NOT NULL,
    title TEXT NOT NULL,                  -- 書名スナップショット
    pages TEXT NOT NULL                   -- spec 文字列
);

-- 検索用 FTS（memos_fts と同じパターン）
CREATE VIRTUAL TABLE artifacts_fts USING fts5(artifact_id, title, body, ...);
```

### 4.2 既存 books / pages / FTS との責務境界

- `books` / `pages`: 蔵書（外部から来た読む対象）。**成果物は蔵書ではない**ので books に入れない。CHECK 制約の件（§2.1）からも独立テーブルが自然
- `memos`: 外部サービス（Scrapbox）からの同期で全置換される揮発キャッシュ。成果物はローカルが原本（一次データ）なので、置換型の memos に相乗りしない
- 検索: `search()` に新しい kind（例: `artifact`）とスコープを追加し、`memos_fts` と同じ流儀で `artifacts_fts` を UNION する。検索結果カードから成果物表示 → 出典（artifact_sources）→ 該当 PDF ページへジャンプ、が Phase 4 の UI の芯になる

### 4.3 元資料の変化への耐性

- 改名: artifact_sources.title がスナップショットなので影響なし
- 削除: pdf_path 文字列は残る。PDF 実体解決に失敗したら「本が見つかりません」表示（pack-design.md の残 TODO と同じ扱い）
- ページ変更（再スキャン等）: pages spec は「当時渡した範囲」の記録として正。現在の PDF とズレる可能性は表示上の注記で許容

## 5. Phase 3 エクスポート manifest の Phase 4 への利用

判定: **manifest.md（Markdown）そのままでは取り込みに使いにくいが、エクスポートイベント記録（export_events.items_json）が同じ情報を機械可読で持てば、manifest の解析は不要になる**。

したがって推奨は「manifest.md をパースする」ではなく:

1. Phase 3C/3D でエクスポート実行時に `export_events` へ 1 行記録する（[usage-history-discovery.md](usage-history-discovery.md) の提案と同一）
2. Phase 4 の取り込み UI は「最近のエクスポート」を export_events から一覧表示し、選ぶだけで出典明細が artifact_sources へ複製される
3. ZIP 同梱の manifest.json（機械可読版）は、別マシンで生成した ZIP からの取り込みなど将来ニーズが出たときの追加手段とする（初期は不要 = 未決事項）

## 6. ローカル完結とプライバシー

- 成果物・プロンプトはローカル SQLite のみに保存。外部送信なし
- デモモードでは取り込み UI を無効化（既存 `DEMO_MODE` パターン）
- エクスポート機能（JSON）に成果物を含めるかは未決。含める場合は資料データと成果物データの境界を JSON スキーマで分ける

## 7. 未決事項

1. artifacts の検索スコープ名・UI 上の呼称（「帰ってきた知識」「AIノート」等）
2. 成果物と資料の多対多（1 成果物が複数資料の対比から生まれるケース）— 初期は export_event_id 1 本で開始し、必要になったら関連テーブル追加
3. manifest.json の ZIP 同梱（§5-3）
4. 成果物の編集 UI（取り込み後の追記・修正）をどこまで持つか — 初期は表示 + 削除のみで開始する案
5. 資料データ JSON エクスポートへの成果物同梱（§6）
6. trigram（部分一致）を artifacts にも張るか — 日本語検索の使い勝手と DB サイズのトレードオフ。memos の現状に合わせるのが無難
