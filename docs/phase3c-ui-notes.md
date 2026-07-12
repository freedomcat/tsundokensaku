## C-5 UI設計メモ

> **位置付け**: 本文書は C-5 の判断経緯を残す補助メモであり、現行仕様の正本ではない。現行仕様は [ai-export-optimization-design.md](ai-export-optimization-design.md) と該当する個別設計書を参照。

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
