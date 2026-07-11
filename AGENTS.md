# Repository Instructions

When creating commits in this repository, keep the human user as the Git author.
Add Codex as a co-author using the standard Git trailer:

Co-authored-by: Codex <codex@openai.com>

## コミットメッセージ

Conventional Commits形式を使用する。

- typeは英語の小文字
- コロンの後に半角スペースを入れる
- 説明は日本語で簡潔に書く
- 1コミットにつき1つの目的とする

使用するtype:

- feat: 新機能
- fix: 不具合修正
- docs: ドキュメント変更
- refactor: 動作を変えない内部改善
- test: テスト追加・修正
- chore: 設定変更や依存関係の更新
- ci: CI設定の変更
- build: ビルド関連の変更
- perf: 性能改善

例:

feat: PDFプレビューにページ選択機能を追加
fix: ページ番号がずれる不具合を修正
docs: デモ環境の構築手順を追記
