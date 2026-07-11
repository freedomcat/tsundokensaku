# export_events 最小仕様（Phase 3C C-6）

作成: 2026-07-12
状態: 仕様確定（実装は Phase 3C の C-6）
前提: [usage-history-discovery.md](usage-history-discovery.md) / [ai-artifact-return-discovery.md](ai-artifact-return-discovery.md) / [ai-export-optimization-design.md](ai-export-optimization-design.md) §12.2・§19

## 1. 目的

エクスポート成功時に「いつ・どの資料の・どの構成を・どの宛先向けに書き出したか」を 1 行記録する。Phase 4（AI 成果物と生成元スナップショットの紐づけ）と Phase 5（消化マップ・共起提示）の共通データ基盤であり、記録開始が遅れるほど実績の空白期間が伸びるため Phase 3C から開始する。

## 2. テーブル定義（確定）

```sql
CREATE TABLE IF NOT EXISTS export_events (
    id INTEGER PRIMARY KEY,
    exported_at TEXT NOT NULL,            -- UTC ISO8601（§7）
    pack_id INTEGER,                      -- 参照補助。FK制約は張らない（§9）
    pack_name TEXT NOT NULL,              -- エクスポート時点のスナップショット
    profile TEXT NOT NULL,                -- 'standard' | 'chat' | 'notebooklm'
    format TEXT NOT NULL,                 -- 'pdf' | 'md' | 'json'
    items_json TEXT NOT NULL              -- §3 のスキーマ
);
```

- スキーマ作成は `ensure_pack_schema` と同じ冪等 `CREATE TABLE IF NOT EXISTS` 方式（専用マイグレーション不要）
- インデックスは v1 では張らない（行数が手動操作回数オーダーで、全走査で足りる。Phase 5 の集計で必要になったら `exported_at` 等に追加）

**選ばなかった案**: `packs`/`pack_items` への FK 参照。資料・項目は削除される前提のデータであり、履歴は資料のライフサイクルから独立していることに価値がある（CASCADE で履歴が消えては本末転倒）。

## 3. items_json スキーマ（確定）

```json
{
  "version": 1,
  "items": [
    {
      "pdf_path": "data/books/book-a.pdf",
      "title": "本A",
      "pages": "10-20",
      "position": 0
    }
  ]
}
```

- `version: 1` を必ず含める。将来フィールドを足す場合は version を上げ、読み手（Phase 4/5 の集計・UI）が判別できるようにする
- `items` は position 順。各要素は `pdf_path`（文字列参照）・`title`（スナップショット）・`pages`（spec 文字列）・`position` の 4 フィールドのみ
- `pack_items.id` / `collapsed` / `added_at` は**含めない**。id は削除後に意味を失い、collapsed は表示状態で出典情報ではない。含める情報は「AI に何を渡したか」の再現に必要な最小限に絞る

**選ばなかった案**: チャンク構成（分冊の切れ目）まで記録する案。Phase 4/5 の要求は「何を渡したか」であり、分冊の切れ目は profile と items から再計算できる。記録を薄く保つ方を優先。

## 4. 記録対象 format（確定）

`pdf` / `md` / `json` の**全 format を記録する**。

- json（資料データの書き出し）は「AI に渡す」行為ではないが、format 列で区別できるため、消化マップ等の集計時に `format != 'json'` で除外する運用とする
- 記録段階でフィルタすると、後から「json エクスポートも見たい」となったとき復元できない。記録は広く、集計で絞る

**選ばなかった案**: json を記録対象外にする案。上記の非可逆性が理由。

単体切り出し（`/export-pdf`, `/export-md`, `/export-pdf/save`）は v1 では記録しない（[usage-history-discovery.md](usage-history-discovery.md) §4.3 のとおり v2 候補）。

## 5. profile 未指定時の記録値（確定）

`'standard'` を記録する。

`resolve_profile(None)` が standard を返す実装と一致させ、「profile 列は解決後のプロファイル名」という単一の意味を持たせる。「未指定だったか明示だったか」の区別は Phase 4/5 のどの要求にも不要。

**選ばなかった案**: NULL や空文字で「未指定」を表す案。列の意味が二重になり、集計側に COALESCE が漏れなく必要になるだけで得るものがない。

## 6. 記録タイミングと失敗時の扱い（確定）

- **タイミング**: `api_export_pack` がレスポンス（ZIP / JSON バイト列）の生成に成功した直後、`Response` を return する直前に 1 行 INSERT する。4xx/404/500 で中断した場合は記録しない（「成功したエクスポートのみ」）
- **失敗時**: INSERT の失敗（ディスク・ロック等）は捕捉してログ出力のみ行い、**エクスポートのレスポンスは正常に返す**（ベストエフォート。履歴のためにエクスポート本体を落とさない）
- 厳密には「レスポンス生成成功 = ダウンロード完了」ではない（クライアントが受信に失敗しても記録は残る）が、サーバ側で観測できる最良の成功シグナルとして許容する

## 7. UTC 日時形式（確定）

`datetime.now(timezone.utc).isoformat()`（例: `2026-07-12T03:45:12.345678+00:00`）。

`packs.created_at` / `pack_items.added_at` と同じ既存流儀（`_pack_now()` 相当）に合わせる。JST 変換は表示時に行う（`format_indexed_at` と同じ役割分担）。

**選ばなかった案**: `_now_jst()`（ZIP ファイル名に使う JST）に合わせる案。DB 内の日時は既存テーブルが UTC で統一されており、混在させない。

## 8. 同一内容の再エクスポート（確定）

**去重しない。毎回 1 行記録する。**

同じ構成を 2 回エクスポートした事実こそが利用実績（Phase 5 の消化・共起の重み）であり、重複排除は情報の破壊になる。UNIQUE 制約も張らない。

**選ばなかった案**: (pack_id, items_json) での去重・最新のみ保持。「何回使ったか」「いつ使ったか」が消え、消化マップの時系列が成立しなくなる。

## 9. pack 削除後の pack_id（確定）

**そのまま残す（dangling を許容）**。FK 制約・CASCADE は張らず、JOIN 前提の設計にしない。

- 表示・集計は `pack_name`（スナップショット）と `items_json` だけで自足する
- `pack_id` は「その資料がまだ存在する場合に資料棚へリンクする」ための補助。解決できなければリンクを出さないだけ
- SQLite の rowid 再利用により、削除後に作られた別資料へ pack_id が偶然一致する可能性は理論上あるが、pack_id を同一性の根拠に使わない（表示時に pack_name の一致も確認する）ため実害なし

## 10. テスト方針（確定）

`tests/test_database.py`（テーブル・記録関数）と `tests/test_web.py`（API 経由）に追加する。

1. 冪等スキーマ: `ensure_pack_schema`（または相当の初期化）を 2 回呼んでもエラーにならない
2. 成功時の記録: pdf / md / json 各 format のエクスポート成功後に 1 行増え、`profile='standard'`・`items_json` が position 順スナップショットになっている（profile 未指定→'standard' の解決を含む）
3. 失敗時の非記録: 空資料 400・PDF 欠損 404 のとき行が増えない
4. 記録失敗の無害性: INSERT を失敗させても（例: 記録関数をモックで例外化）エクスポートのレスポンスは 200 で返る
5. 再エクスポート: 同一資料を 2 回エクスポートすると 2 行になる
6. dangling: 資料を削除しても export_events の行は残る
7. items_json スキーマ: `version: 1` と 4 フィールド構成の検証
8. 既存テスト全通過（エクスポート出力・エラー応答が不変であること）

## 11. 実装ステップの位置（確定）

**独立ステップ C-6 とする（C-3 に含めない）**。

理由:

- C-3（export API 配線 + 実統計分岐 + plan 由来 manifest）は Phase 3C で最もリスクの高い変更であり、新テーブル + 記録という別関心事を混ぜるとレビューと問題切り分けが難しくなる
- C-6 は依存が C-0（エクスポート経路の下準備）だけで、chat の完成を待たず standard エクスポートの記録として独立に価値が出る。C-3 と並行開発も可能
- 失敗しても（revert しても）エクスポート機能に影響しない粒度を保てる

**選ばなかった案**: C-3 に同梱。コミット数は減るが、上記のレビュー性・切り分け性を損なう。「同一マイルストーン（3C）内で必ず出荷する」ことで空白期間の懸念（記録開始の遅れ）は同等に解消できる。
