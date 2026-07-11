# 資料棚・資料の永続化 設計メモ

作成: 2026-07-07 / 最終更新: 2026-07-08
状態: **実装済み**（Phase 1 完了 + Phase 2 の組み立て体験まで反映）
前提: [ROADMAP.md](../ROADMAP.md) Phase 1〜2

用語: 利用者向けの概念は「**資料棚**」（画面、URL は `/workspace`）と「**資料**」（AIへ渡すために組み立てる成果物）。**Pack / packs / pack_items は内部実装名**（コード・API・DBテーブル）としてのみ使う。「ワークスペース」は旧名称。

## 1. 経緯（旧実装）

かつてのワークスペースは `static/export-cart.js` が sessionStorage（キー `tsundokensaku-export-cart`）に保存する揮発性の無名カート1つだった。タブを閉じると組み立てた構成が消えるため、これを名前付き資料としてサーバ側（SQLite）に永続化した。旧カートのデータ形式（version 2）:

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

この形式はクライアントのメモリキャッシュ・API の cart 表現として現在も使っている。旧 sessionStorage カートが残っているブラウザでは、初回アクセス時に「移行された資料」という資料として自動取り込みし、キーを削除する（失敗時は残して次回リトライ）。

## 2. DB 設計（実装済み）

`database.py` の `initialize()` と、API リクエスト経路の軽量版 `ensure_pack_schema()` が冪等に作成する。

```sql
CREATE TABLE packs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',      -- UI 未提供（列のみ）
    created_at TEXT NOT NULL,           -- UTC ISO8601
    updated_at TEXT NOT NULL,
    archived_at TEXT                    -- 未使用（列のみ）
);

CREATE TABLE pack_items (
    id INTEGER PRIMARY KEY,
    pack_id INTEGER NOT NULL REFERENCES packs(id) ON DELETE CASCADE,
    pdf_path TEXT NOT NULL,             -- books.path と同じ文字列
    title TEXT NOT NULL,                -- 追加時点のスナップショット（出典情報）
    pages TEXT NOT NULL DEFAULT '',     -- spec 文字列（TsundokuPages / parse_page_selection と共通文法）
    collapsed INTEGER NOT NULL DEFAULT 0,
    position INTEGER NOT NULL DEFAULT 0, -- 並び順。books 辞書の送信順で振り直す
    added_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(pack_id, pdf_path)
);

CREATE TABLE app_state (
    key TEXT PRIMARY KEY,               -- 'active_pack_id' = 現在の資料
    value TEXT NOT NULL
);
```

### 設計判断

- **本の参照は `pdf_path` 文字列**（`books.id` ではない）。旧カートのキー・エクスポート API の引数・再インデックス時の同一性、すべてパスが軸。books 行が消えても資料項目は出典情報として残る
- **title はスナップショット**。タイトル推定が改善されても「追加時点で何と認識していたか」を保持（出典の再現性）
- **ページ範囲は spec 文字列のまま**保存。クライアントとサーバが同じ文法を解釈するので真実は1つの文字列で足りる
- **現在の資料はサーバ側で単一**（`app_state`）。シングルユーザー前提で全タブが同じ資料を見る
- **資料の自動作成はしない**。新規 DB は資料0件から始まり、利用者が必要になったタイミングで作る（`resolve_active_pack_id()` は 0件なら None を返す。旧 `ensure_active_pack`＝デフォルト資料の自動作成は廃止）
- **position は一括置換時に books 辞書の列挙順で振り直す**。資料棚の「上へ」「下へ」並び替えがそのまま永続化され、エクスポート順にも反映される（`added_at` は保持）

## 3. API（実装済み、`/api/packs` 配下）

- `GET    /api/packs` — 一覧 + `active_pack_id`（資料0件なら null。自動作成しない）
- `POST   /api/packs` — `{name}` で作成し、その資料へ切替
- `GET    /api/packs/{id}` — メタ情報 + `cart`（カート形式）
- `PATCH  /api/packs/{id}` — `{name?, note?}` 改名等
- `DELETE /api/packs/{id}` — 削除。現在の資料だった場合は残る資料へフォールバック、0件なら null
- `POST   /api/packs/{id}/activate` — 現在の資料の切替
- `PUT    /api/packs/{id}/books` — カート形式 `{books}` での一括置換（クライアントの save に対応する唯一の書込み経路。設計当初の項目単位 PUT/DELETE は採用せず）
- `POST   /api/packs/import` — 旧 sessionStorage カートの取り込み（移行用）

競合解決は last-write-wins。シングルユーザー・ローカル前提のため楽観ロックなし。複数タブはフォーカス時の再取得で追随する。

## 4. クライアント（static/pack-store.js）

旧 `TsundokuCart` の公開 API（`load/save/bookCount/totalPages/summaryLabel/updateBadge`）を互換のまま、サーバ同期ストアに置き換えた。

- `load()` はメモリキャッシュの複製を返す同期 API のまま。初回にサーバから現在の資料を取得し、完了時に `tsundoku-cart-updated` イベントで各画面を再描画させる
- `save()` は楽観更新 + 400ms デバウンスで `PUT /api/packs/{id}/books`。タブ離脱時は keepalive で書き込む
- 資料管理 API を追加提供: `getActivePack / ensureActivePackInteractive / listPacks / createPack / activatePack / renamePack / deletePack / refresh`
- `ensureActivePackInteractive()`: 資料がなければ「資料がありません。新しい資料を作成しますか？」→ 名前入力 → 作成・切替まで対話的に行う。検索結果・PDFプレビューの追加操作から共通で使う

### 誤書込み防止（切替とデバウンスのレース対策）

- 書込み予約時に**宛先の資料 ID を固定**し、実行時に現在の資料が変わっていたら破棄（編集直後の新規作成で旧資料の内容が新資料へコピーされる事故の防止）
- 資料が切り替わった取得では未送信編集をマージせず破棄（初回ロード・同一資料の再取得のみ保護マージ）
- 資料の作成・切替の前に未送信分の書込み完了を await

## 5. UI（実装済み）

```
ナビ「資料棚」（冊数バッジ付き）
  └─ /workspace … 現在の資料の編集画面
       ├─ 「現在の資料：」セレクト / 新しい資料 / 名前を変更 / 資料を削除
       ├─ 資料0件時: 「資料はまだありません。」+「＋ 新しい資料」（操作ボタンは無効化）
       ├─ 書籍カード: 上へ / 下へ / PDFで選ぶ / ページを追加 / 削除
       └─ PDF・MDエクスポート / この資料を空にする（確認ダイアログ付き）

検索結果 (/search)
  ├─ 各PDFヒットに「資料に追加」チェックボックス
  └─ 下部バー: 追加先: [資料名（n冊）▼] [資料棚で編集]
       ├─ セレクトで現在の資料を切替（チェック状態も切替先で再計算）
       └─ 末尾「＋ 新しい資料…」で名前入力 → 作成 → 切替

PDFプレビューモーダル（検索起点・資料棚起点の両方）
  └─ 「「資料名」に追加」ボタン（現在の資料名を表示、成功メッセージにも表示）
```

一覧管理の専用画面（設計当初の `/workspace/packs`）は作らず、資料棚と検索バーのセレクトで代替している。

## 6. テスト

- `tests/test_database.py`: 資料 CRUD、現在の資料のライフサイクル（0件開始・フォールバック・全削除で None）、一括置換の並び順・added_at 保持、カート相互変換、initialize 冪等性
- `tests/test_web.py`: `/api/packs` 系の関数直呼びテスト（新規DBは0件開始、作成→追加フロー、404/400、import）
- クライアントは Playwright による手動 E2E（永続化・移行・切替・並び替え・0件フロー・検索起点モーダルからの追加・コピー事故再現シナリオ）で確認。自動化はしていない

## 7. 既知の注意点

- **pdf_path の正規化**: `books.path` には `/data/books/...` と旧 `/books/tech/...` が混在し得る。資料のキーは追加時点の books.path をそのまま使う。再インデックスでパスが変わった項目の「本が見つかりません」表示は未実装（残TODO）
- **同一PDFの複数項目化**: 現状は `UNIQUE(pack_id, pdf_path)` と `cart.books[pdf_path]` により、同じ資料内に同じPDFを複数項目として追加できない。分冊して別々のページ範囲・位置・折りたたみ状態で扱う設計案は [pack-item-identity-design.md](pack-item-identity-design.md) に分離
- **複数タブ**: last-write-wins + フォーカス時再取得。デバウンス中のタブ切替で数秒の巻き戻りがあり得るが許容
- **エクスポート経路は資料と独立**: `/export-pdf` / `/export-md` は従来どおり `pdf_path` + `pages` を都度受ける。資料は「何をどの順で渡すかの記憶」に徹する

## 8. 残TODO（必要になったら）

- [ ] note 欄の UI（DB 列は用意済み）
- [ ] アーカイブ（`archived_at` 列は用意済み）・複製
- [ ] ドラッグ＆ドロップでの並び替え（現状は上下ボタン）
- [ ] Kindle 本・メモの資料への追加（現状 PDF のみ）
- [ ] パス変更で本が見つからない項目の「本が見つかりません」表示
- [ ] 資料一覧の専用管理画面（資料が増えて一覧性が必要になったら）
