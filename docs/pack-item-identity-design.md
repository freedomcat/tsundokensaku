# 同一PDFを複数の資料項目として扱う設計メモ

作成: 2026-07-11
状態: 設計案
前提: [ROADMAP.md](../ROADMAP.md) Phase 2 / [docs/pack-design.md](pack-design.md)

## 背景

資料棚では、検索結果やPDFプレビューからPDFのページ範囲を資料へ追加し、並び替えてPDF/Markdownとしてエクスポートできる。

現状の資料データは、同じ資料内で `pdf_path` を一意キーとして扱う。つまり、同じPDFを複数回追加すると、既存項目のページ範囲を更新する形になり、別々の資料項目としては保持できない。

しかし実際の資料作成では、同じ本から離れた章やページ範囲を複数の文脈で使いたいことがある。

例:

- 本A p.10-20 を前提説明として追加する
- 同じ本A p.80-95 を実装例として後ろに追加する
- 同じ本A p.130-145 を比較対象として別位置に追加する

この場合、同じPDFを参照していても、それぞれ別の資料項目として、ページ範囲・並び順・折りたたみ状態・追加時刻を独立して持てる必要がある。

## 現状の仕様

クライアント/API上の資料内容は `version: 2` のカート形式で表現している。

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

DB側も `pack_items` に `UNIQUE(pack_id, pdf_path)` があるため、同じ資料内に同じPDFを複数行保存できない。

```sql
CREATE TABLE pack_items (
    id INTEGER PRIMARY KEY,
    pack_id INTEGER NOT NULL REFERENCES packs(id) ON DELETE CASCADE,
    pdf_path TEXT NOT NULL,
    title TEXT NOT NULL,
    pages TEXT NOT NULL DEFAULT '',
    collapsed INTEGER NOT NULL DEFAULT 0,
    position INTEGER NOT NULL DEFAULT 0,
    added_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(pack_id, pdf_path)
);
```

この設計では `pdf_path` が「本の参照」と「資料項目の識別子」を兼ねている。そのため、同一PDFの複数項目化ができない。

## 変更したい仕様

資料内の項目は「PDFそのもの」ではなく、「PDFのページ範囲を持つ資料項目」として扱う。

期待する仕様:

1. 同じ `pdf_path` を持つ項目を、同じ資料内に複数追加できる。
2. 各項目は独立したIDを持つ。
3. 各項目は独立した `pages`、`collapsed`、`position`、`added_at`、`updated_at` を持つ。
4. 資料棚では同じ本が複数カードとして表示される。
5. エクスポートでは資料項目の並び順どおりに、同じPDF由来の範囲も別エントリとして出力される。

## 採用案

資料項目の識別子を `pdf_path` から `pack_items.id` へ移す。

DBでは `UNIQUE(pack_id, pdf_path)` を廃止し、同じ `pack_id` と `pdf_path` の組み合わせを複数行許可する。

クライアント/API表現は、辞書形式の `books` ではなく、配列形式の `items` を中心にする。

例:

```json
{
  "version": 3,
  "items": [
    {
      "id": 101,
      "pdf_path": "data/books/book-a.pdf",
      "title": "本A",
      "pages": "10-20",
      "collapsed": false,
      "addedAt": "2026-07-11T00:00:00.000Z"
    },
    {
      "id": 102,
      "pdf_path": "data/books/book-a.pdf",
      "title": "本A",
      "pages": "80-95",
      "collapsed": true,
      "addedAt": "2026-07-11T00:05:00.000Z"
    }
  ]
}
```

`version: 3` は仮称であり、既存の `version: 2` カート形式と区別するための新しいクライアント/API表現を指す。

## DB変更方針

`pack_items.id` を資料項目IDとして明示的に扱う。

変更後の概念モデル:

```sql
CREATE TABLE pack_items (
    id INTEGER PRIMARY KEY,
    pack_id INTEGER NOT NULL REFERENCES packs(id) ON DELETE CASCADE,
    pdf_path TEXT NOT NULL,
    title TEXT NOT NULL,
    pages TEXT NOT NULL DEFAULT '',
    collapsed INTEGER NOT NULL DEFAULT 0,
    position INTEGER NOT NULL DEFAULT 0,
    added_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

移行では既存の `pack_items` を維持しつつ、`UNIQUE(pack_id, pdf_path)` 制約を落とす。SQLiteでは制約削除が直接できないため、実装時は新テーブル作成、既存データコピー、旧テーブル削除、リネームの形になる。

移行処理は既存の `id`、`position` および表示順を維持する。コピー順は `ORDER BY pack_id, position, id` とし、移行処理の一環としての再採番は行わない。`position` の重複・欠番を解消する必要がある場合は、別途正規化処理として実施する。

現時点では `pack_items.id` を参照する別テーブルはない。将来、項目メモやアノテーションなど `pack_item_id` を持つテーブルが追加されている場合は、テーブル再作成時に外部キー整合性を保つ必要がある。

`UNIQUE(pack_id, position)` は今回は追加しない。並び順の一意性は保存処理で同じ資料内の `position` を振り直して保証する。DB制約としての追加は、ドラッグ＆ドロップなど一括並び替え時の一時衝突を避ける更新方式が固まってから検討する。

## API変更方針

新しい読み取り形式では `items` 配列を返す。

既存の `cart.books` 形式は、旧sessionStorageカートの移行と後方互換のために読み取り可能にする。ただし、新しいUIと保存APIは `items` 配列を正とする。

正規データ形式は `version: 3` の `items` とする。移行期間中、`GET /api/packs/{id}` は旧UI向けの `cart` と、新UI向けの `items` を併設してよい。ただし、新規実装は `items` を正として扱い、`cart.books` への書き込みは行わない。

旧 `version: 2` から新 `version: 3` への読み取り・移行は対応する。一方、同じ `pdf_path` を複数項目として保持できる `version: 3` を、`pdf_path` が辞書キーである `version: 2` へ損失なく逆変換することはできない。そのため、`version: 3` から `version: 2` への逆変換や書き戻し互換は保証しない。

`PUT /api/packs/{id}/items` は一括置換APIとする。リクエスト内の `id` がある項目は既存項目として更新し、`id` がない項目は新規作成する。リクエストから消えた既存項目は削除する。別の `pack_id` に属する `id` は受け付けない。新規項目の `id` はサーバが採番し、クライアントは新規IDを作らない。応答では新規作成された項目を含む、保存後の確定 `items` を返す。

`PUT /api/packs/{id}/books` は新UIでは使わない。既存UIの移行が完了した段階で非推奨化し、最終的に削除する。同一PDFの複数項目化後は、旧API経由の書き込みでは複数項目を保持できないため、新機能の保存経路としては扱わない。

## UI変更方針

検索結果の「資料に追加」は、現在チェックボックスとして振る舞う。これは「その本が資料に入っているか」を表すUIであり、同じPDFを複数回追加する操作とは相性が悪い。

同一PDFの複数項目化では、検索結果・PDFプレビューともに「追加」操作を項目追加として扱う。

検索結果UIは、チェックボックスから「資料に追加」ボタンへ変更する。追加済みのPDFでは「追加済み（n件）」と「もう一つ追加」を表示する。チェックボックスは「その本が資料内にある/ない」という二値状態を表すため、同じPDFを複数回追加できる操作には使わない。

PDFプレビューは、開いた文脈に応じて `item_id` を持つ。検索結果や通常のPDFリンクから開いた場合は `item_id` なしで、新規項目として追加する。資料棚の特定カードから開いた場合は `item_id` ありで、既存項目のページ範囲を更新する。

資料棚では、各カードは `item.id` を持ち、ページ編集・折りたたみ・削除・並び替えは項目ID単位で行う。

## エクスポート方針

エクスポートは `pack_items.position` の順に、資料項目単位で処理する。

同じPDFが複数項目あっても、それぞれ独立したエントリとしてPDF/Markdownを書き出す。

既存のZIPエクスポートはファイル名に連番を含めるため、同じタイトルの項目が複数あっても衝突しにくい。既存の `01_タイトル_pページ範囲.pdf` / `.md` 形式を維持し、連番を衝突回避の主キーとして扱う。ページ範囲はファイル名として使える文字へサニタイズする。詳細なページ範囲は manifest にも残す。

## 移行方針

既存データは、各 `pack_items` 行がそのまま1つの資料項目になる。

旧 `version: 2` カート:

```json
{
  "version": 2,
  "books": {
    "data/books/book-a.pdf": {
      "title": "本A",
      "pages": "10-20"
    }
  }
}
```

新形式への変換:

```json
{
  "version": 3,
  "items": [
    {
      "pdf_path": "data/books/book-a.pdf",
      "title": "本A",
      "pages": "10-20"
    }
  ]
}
```

既存形式は `pdf_path` キーが一意なので、移行時に情報は失われない。

## 採用しない案

### `pdf_path#1` のような疑似キーを使う

採用しない。

理由は、PDF解決やエクスポートAPIが本物の `pdf_path` を期待しているため。疑似キーを使うと、各所でキーから本物のパスを復元する処理が必要になり、実装が壊れやすくなる。

### 1つの項目の `pages` に複数の分冊範囲を詰め込む

採用しない。

理由は、分冊したい目的が「別々の文脈・別々の位置・別々の折りたたみ状態で扱うこと」だから。`pages` だけを `10-20,80-95` のようにまとめると、資料内での意味単位を分けられない。

### `books` 辞書形式を維持したまま値だけ配列にする

例:

```json
{
  "books": {
    "data/books/book-a.pdf": [
      {"pages": "10-20"},
      {"pages": "80-95"}
    ]
  }
}
```

採用しない。

理由は、並び順が「本ごとのグループ」と「資料全体の項目順」の二重管理になるため。資料は最終的にAIへ渡す並びが重要なので、全項目を1つの配列で保持する方が自然。

### `books.id` を資料項目IDとして使う

採用しない。

理由は、`books.id` は蔵書側の本レコードIDであり、資料内の追加インスタンスではないため。同じPDFを複数回追加するには、同じ `books.id` を参照する複数の `pack_items.id` が必要になる。

## テスト方針

- DBで同じ `pack_id` と `pdf_path` の複数 `pack_items` 行を保存できること。
- `get_pack_items()` が `id` と `position` を含めて順序通り返すこと。
- 新しい `items` 形式で一括保存・読み戻しできること。
- 旧 `version: 2` カートを新形式へ移行できること。
- 同じPDFの複数項目を別々のページ範囲でエクスポートできること。
- 資料棚UIで同じPDFの複数カードを削除・並び替え・折りたたみ・ページ編集できること。

## 決定事項

- APIの正規データ形式は `version: 3` の `items` とする。移行期間中に限り、`GET /api/packs/{id}` で `cart` と `items` の一時併設を許可する。
- `PUT /api/packs/{id}/books` は新UIでは使用しない。新UI移行後に非推奨化し、最終的に削除する。
- 検索結果の追加UIはチェックボックスから「資料に追加」ボタンへ置き換える。追加済みの場合は「追加済み（n件）」と「もう一つ追加」を表示する。
- 既存の `TsundokuCart` 名は今回は維持する。API・UI移行後に、`PackItem` / `PackItemList` など資料項目中心の名前へ段階的に改める。
- PDFプレビューは、`item_id` なしなら新規追加、`item_id` ありなら既存項目更新として扱う。
- v2互換は、v2入力の読み取り・v3移行のみ対応する。v3からv2への逆変換は保証しない。

## 実装の確定内容 (2026-07-11 追記)

本設計案に基づき、Phase 3A, 3B, 4A, 4B, 5 のすべての実装が完了しました。確定した仕様および運用方針は以下の通りです。

### 1. 識別子と永続化
- **データベース上の識別**: 永続化された各項目は `pack_items.id` をプライマリキーとして一意に識別されます。
- **クライアント上での識別（未保存時）**: 新規追加された項目にはクライアント側で `clientId` (例: `new:timestamp:random`) が割り当てられ、サーバーへの保存（`PUT /api/packs/{pack_id}/items`）が完了して正式な `id` が決定するまでの競合・状態管理に使われます。

### 2. データ互換性とフォーマット
- **books (version: 2) 形式**: `pdf_path` をキーとする辞書データのため、同一のPDFを重複して保持することができないという制約がありました。旧形式のインポートは下位互換性のためサポートし、インポート時に配列へ平坦化して `version: 3` に自動移行されます。
- **items (version: 3) 形式**: 重複する `pdf_path` をそのまま配列順で個別に保持可能なフォーマットです。
- **インポート／エクスポート**: UI上のボタンから JSON 形式でエクスポート・インポートできます。インポート時はDB内の `id` / クライアントの `clientId` などの環境依存値は無視され、常に新しい ID が自動採番されます。

### 3. PDF一式書き出し
- 資料棚の position 順に、同じPDFであっても別の出力ファイル（連番付き）として独立して出力されるよう、 `zip_export.py` にて position を考慮した ZIP エクスポート処理を実装しました。

### 4. position の正規化・再採番方針
- インポート時に、 JSON 内の `position` フィールドの値が全項目で非負の整数かつ一意（重複なし）であれば、その値の順序関係を維持して昇順ソートした上で、 `0` からの連続した連番（`0, 1, 2...`）に再採番（正規化）して DB に保存します。
- それ以外のケース（重複、負数、欠落など position が壊れている場合）は、配列内の並び順（インデックス順）をそのまま基準として `0` からの連続連番に再採番して正規化します。

### 5. Playwright テストの workers=1 制約（暫定措置）
- E2Eテスト並列実行時に、複数の worker が同一の SQLite データベースおよび同一の `active_pack` / localStorage 状態を参照・更新するため、テスト間で競合が発生してテストが Flaky になる現象が判明しました。
- このため、 `--workers=1` の直列実行に制限しています。将来的な解消手段として、テストごとに隔離された DB ファイルや dynamic port 上でサーバーインスタンスを立ち上げる方式への移行が TODO として残されています。
