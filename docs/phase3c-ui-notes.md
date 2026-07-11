## C-5 UI設計メモ

### 採用

- workspace.html に資料棚専用モーダルを追加
- base.html の既存 modal CSS を利用
- pack-store.js は変更しない
- profile は配列定数で管理
- 現時点では chat のみ表示
- exportPack() を profile 対応に一般化
- fetchExportPreview() を共通化

### 採用しない

- profile一覧API
- notebooklm のUI
- JSファイル分割
- workspace.html の大規模リファクタ

### 理由

C-5だけで完結させ、既存UIとの後方互換を維持するため。