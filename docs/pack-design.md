# パック永続化 設計メモ（Phase 1: 名前付きパック）

作成: 2026-07-07
状態: 設計のみ（実装未着手）
前提: [ROADMAP.md](../ROADMAP.md) Phase 1

## 1. 現状調査まとめ

### ワークスペースの実装

- ストア: `static/export-cart.js` の `window.TsundokuCart`。**sessionStorage** キー `tsundokensaku-export-cart` に保存（タブを閉じると消える）
- ページ指定ユーティリティ: `static/pages-spec.js` の `window.TsundokuPages`（spec 文字列 `"3-7,20-35"` の検証・結合・差分・ページ数計算）
- 編集画面: `templates/workspace.html`（約570行、インライン JS）
- ストアの利用箇所は3つ:
  - `templates/workspace.html` — 一覧表示・ページ範囲編集・章選択・本文検索モーダル・エクスポート
  - `templates/search.html` — 検索結果のチェックボックスで本+ページを追加/削除、件数表示
  - `templates/base.html` — PDF プレビューモーダルの「ワークスペースに追加」、ナビの冊数バッジ。更新時に `tsundoku-cart-updated` カスタムイベントを発火

### sessionStorage のデータ構造（version 2）

```json
{
  "version": 2,
  "books": {
    "<pdf_path>": {
      "title": "表示タイトル",
      "pages": "3-7,20-35",
      "collapsed": false,
      "addedAt": "2026-07-07T00:00:00.000Z"
    }
  }
}
```

- キーは PDF パス（`books.path` と同じ文字列）。PDF のみ対象（Kindle・メモは追加不可）
- version なしの旧形式（pages が番号配列）からの移行コードが `migrateLegacy()` に残っている
- サーバ側は完全にステートレス。エクスポートは `GET /export-pdf` / `GET /export-md` に `pdf_path` と `pages` を都度渡すだけ

### 関係するサーバエンドポイント（既存・変更不要）

- `GET /pdf-outline?pdf_path=` — 章一覧とページ数
- `GET /search-pages?pdf_path=&q=` — 本文ページ検索
- `GET /export-pdf` / `GET /export-md` — ページ切り出し
- `POST /export-pdf/save` — 設定ディレクトリへの保存

## 2. DB 設計案

`database.py` の `initialize()` に冪等な `CREATE TABLE IF NOT EXISTS` として追加（既存スキーマ管理の流儀に合わせる）。

```sql
CREATE TABLE IF NOT EXISTS packs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,          -- UTC ISO8601（既存の indexed_at と同様）
    updated_at TEXT NOT NULL,
    archived_at TEXT                   -- NULL = アクティブ。MVPでは未使用でも列だけ用意
);

CREATE TABLE IF NOT EXISTS pack_items (
    id INTEGER PRIMARY KEY,
    pack_id INTEGER NOT NULL REFERENCES packs(id) ON DELETE CASCADE,
    pdf_path TEXT NOT NULL,            -- books.path と同じ文字列（現カートのキーを踏襲）
    title TEXT NOT NULL,               -- 追加時点のタイトルのスナップショット（出典情報）
    pages TEXT NOT NULL DEFAULT '',    -- spec 文字列。解釈は TsundokuPages / parse_page_selection と共通
    collapsed INTEGER NOT NULL DEFAULT 0,
    position INTEGER NOT NULL DEFAULT 0,  -- 表示順。MVPは追加順の連番でよい
    added_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(pack_id, pdf_path)
);

CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- app_state('active_pack_id') = 現在編集中のパックID
```

### 設計判断

- **本の参照は `pdf_path` 文字列**（`books.id` ではない）。理由: 現カートのキー・エクスポート API の引数・PDF 再インデックス時の同一性、すべてパスが軸。books 行が消えてもパック項目は出典情報として残せる。表示時に `books` と LEFT JOIN して最新タイトル・存在チェックを解決する
- **title はスナップショット**。タイトル推定が改善されて `books.title` が変わっても「追加時点で何と認識していたか」を保持（出典の再現性）。表示は「最新タイトル優先、なければスナップショット」
- **ページ範囲は spec 文字列のまま**保存。正規化（区間展開）はしない。クライアントの `TsundokuPages` とサーバの `parse_page_selection`（`pdf_export.py`）が既に同じ文法を解釈しており、真実は1つの文字列で十分
- **アクティブパックはサーバ側で単一**（`app_state`）。個人用シングルユーザー前提なので、タブごとの独立編集はやらない。全タブが同じパックを見る
- 利用履歴（Phase 3/4 の土台）は `added_at` / `updated_at` で最低限が始まる。イベントログテーブルは MVP では作らない

## 3. API 設計案（JSON、`/api/packs` 配下）

- `GET    /api/packs` — 一覧（各パックの冊数・ページ数集計・updated_at 含む）+ `active_pack_id`
- `POST   /api/packs` — `{name}` で作成。作成したパックをアクティブに切替
- `GET    /api/packs/{id}` — パック本体と items（現カート JSON とほぼ同形で返す）
- `PATCH  /api/packs/{id}` — `{name?, note?}` 改名等
- `DELETE /api/packs/{id}` — 削除（アクティブだった場合は別パックへ自動切替、なければ新規デフォルト作成）
- `POST   /api/packs/{id}/activate` — アクティブ切替
- `PUT    /api/packs/{id}/items` — `{pdf_path, title, pages, collapsed}` を upsert
- `DELETE /api/packs/{id}/items?pdf_path=` — 項目削除
- `POST   /api/packs/import` — sessionStorage カート JSON（version 2）を丸ごと受けて1パックに変換（移行用）

競合解決は **last-write-wins**。シングルユーザー・ローカル前提のため楽観ロックは入れない。複数タブはフォーカス時の再フェッチで追随（後述）。

## 4. Web UI 画面遷移案

```
ナビ「ワークスペース」
  └─ /workspace                        … 現在のアクティブパックの編集画面（今の画面とほぼ同じ）
       ├─ ヘッダに [パック名 ▼] ドロップダウン
       │    ├─ 他のパックへ切替（activate）
       │    ├─ 「新しいパック...」（名前入力 → 作成 → 切替）
       │    └─ 「パックの管理...」 → /workspace/packs
       ├─ パック名クリックで改名（インライン編集）
       └─ 既存機能そのまま: ページ追加モーダル / PDFで選ぶ / 章選択 /
          PDF・MDエクスポート / クリア（=アクティブパックの全項目削除）

/workspace/packs                       … パック一覧（新規画面）
  ├─ 名前・冊数/ページ数・更新日時の一覧
  ├─ 開く（activate して /workspace へ）
  ├─ 改名 / 削除
  └─ 新規作成

検索結果 (/search)・PDFプレビュー（全画面共通モーダル）
  └─ 「ワークスペースに追加」= アクティブパックへの追加（挙動は今と同じ、行き先が永続化されただけ）
      ナビバッジはアクティブパックの冊数を表示
```

原則: **既存の操作体験は変えない**。増えるのは「パックの切替・作成・一覧」だけ。検索ボックスを増やさない方針（ROADMAP）と同様、ワークスペースも画面を増やしすぎない。

## 5. 既存ワークスペースとの互換方針

- **クライアントストアの置き換え**: `TsundokuCart` の公開 API（`load/save/bookCount/totalPages/summaryLabel/updateBadge`）を、サーバ同期版 `pack-store.js` に差し替える。呼び出し側3ファイルのインターフェースを保つため:
  - ページ読み込み時に `GET /api/packs/{active}` で取得しメモリキャッシュ
  - `load()` は同期のままキャッシュを返す（既存呼び出しコードを壊さない）
  - `save()` は楽観更新（キャッシュ即時反映）+ デバウンス付き `PUT` でサーバへ
  - `window` の `focus` / `pageshow` で再フェッチし、他タブの変更に追随
- **自動移行**: 初回ロード時に sessionStorage に version 2 カートが残っていて中身が空でなければ、`POST /api/packs/import` で「移行されたワークスペース」という名前のパックを作成してアクティブ化し、sessionStorage を削除。移行は一度きり（成功時にキー削除で担保）
- **旧version（配列形式）→ v2 の migrateLegacy はそのまま生かす**（import 前に既存コードで v2 化されるため追加対応不要）
- エクスポート系エンドポイントの引数（`pdf_path` + `pages`）は変更しない。パックはあくまで「何を渡すかの記憶」であり、エクスポートの経路は既存のまま

## 6. 最小実装スコープ（MVP）

やる:

- packs / pack_items / app_state テーブルと CRUD（database.py）
- `/api/packs` 系 API（web.py）
- pack-store.js（サーバ同期版 TsundokuCart）+ sessionStorage 自動移行
- /workspace のパック切替ドロップダウン・新規作成・改名
- /workspace/packs 一覧（開く・改名・削除・新規のみ）

やらない（後続 Phase / 余力があれば）:

- アーカイブ・複製・並び替え（D&D）
- 行レベルの出典管理・エクスポート履歴・イベントログ
- Kindle本・メモのパック追加（PDF のみ、現状踏襲）
- note 欄の UI（列だけ用意）
- エクスポートプロファイル・トークン概算（Phase 2）

## 7. 実装ステップ

1. **database.py**: スキーマ追加 + パック CRUD 関数（`create_pack` / `list_packs` / `get_pack` / `rename_pack` / `delete_pack` / `set_active_pack` / `get_active_pack_id` / `upsert_pack_item` / `delete_pack_item` / `import_cart_as_pack`）+ テスト
2. **web.py**: `/api/packs` 系ルート（薄く、ロジックは database.py へ）+ テスト
3. **static/pack-store.js**: サーバ同期ストア。`TsundokuCart` と同名 API を提供し、base.html の読み込みを export-cart.js から差し替え。sessionStorage 移行処理を含む
4. **templates/workspace.html**: パック名表示・切替ドロップダウン・改名。クリアの対象文言を「このパックを空にする」に変更
5. **templates/workspace_packs.html**（新規）: 一覧画面 + `/workspace/packs` ルート
6. **search.html / base.html**: 動作確認中心（ストア API 互換なら変更最小のはず）。バッジ文言を「パック名 n冊」にするか検討
7. **ドキュメント**: ARCHITECTURE.md（データモデル・ワークスペース節）、README（Web UIでできること）、ROADMAP.md（Phase 1 を実装済みに）
8. **後片付け**: export-cart.js を削除（pack-store.js に吸収）

ステップ1-2（サーバ側）とステップ3-6（クライアント側）は独立性が高い。1日目にサーバ側+テスト、2日目にクライアント側+手動確認、が目安。

## 8. テスト方針

- **database テスト**（unittest、既存の tempfile + connect パターン）:
  - パック作成・一覧・改名・削除、削除時の items カスケード
  - item upsert（同一 pdf_path の UNIQUE 上書き）・削除
  - アクティブパックの切替、アクティブ削除時の自動切替
  - import_cart_as_pack: v2 カート JSON → パック変換、title/pages/collapsed/addedAt の引き継ぎ
  - initialize() の冪等性（既存DBに2回実行して壊れない）
- **web テスト**: 既存流儀に合わせ関数直呼び（TestClient/httpx 不使用）。API 関数にリクエストボディ相当の dict を渡して JSON レスポンス形を検証
- **クライアント**: 自動テストなし（既存も同様）。手動確認チェックリストを PR に書く。`/do-playwright` スキルで以下を確認:
  - 検索→追加→ブラウザ再起動→残っている（永続化の本丸）
  - sessionStorage カートがある状態で初回ロード→移行される
  - パック切替でバッジ・一覧が変わる
  - エクスポートが従来どおり動く

## 9. リスクと注意点

- **同期→非同期の境界**: `TsundokuCart.load()` は同期 API で、search.html・base.html がページ描画中に呼ぶ。キャッシュ未取得のタイミング（初回描画直後）にチェックボックス状態が空になる可能性 → 取得完了時に `tsundoku-cart-updated` を発火して再描画させる（イベントは既存）
- **複数タブの同時編集**: LWW + フォーカス時再フェッチで妥協。デバウンス中のタブ切替で数秒の巻き戻りがあり得るが、シングルユーザーでは許容
- **pdf_path の正規化**: books.path には `/data/books/...` と旧 `/books/tech/...` が混在し得る（ARCHITECTURE.md 参照）。パックのキーは「その時点の books.path」をそのまま使い、正規化は既存のパス解決に任せる。再インデックスでパスが変わった項目は「本が見つかりません」表示 + 手動削除できれば MVP は十分
- **移行の一回性**: import 成功のレスポンスを確認してから sessionStorage を削除。失敗時は残す（次回リトライ）
- **DB マイグレーション**: 新テーブルのみで既存テーブルに触れないため後方互換リスクは低い。`initialize()` は毎起動走る前提なので `IF NOT EXISTS` 徹底
- **「すべてクリア」の意味変化**: 従来は揮発カートのクリア、今後は永続データの削除。誤操作の影響が大きくなるため確認ダイアログを付ける
- **アクティブパック未存在**: 初回起動・全削除後は「デフォルト」パックを自動作成して常に1つはある状態を保つ（UI の null 分岐を減らす）
