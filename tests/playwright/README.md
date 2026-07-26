# Playwright E2E テストについて

## Dockerなしの隔離実行

ローカルでの正式な実行コマンドは `npm run test:ui` です。Git管理された
`data/books/cathedral.pdf`、`magicpot.pdf`、`noosphere.pdf` だけを一時BOOKS_DIRへ
コピーし、一時DB_DIRへインデックスを構築してから、127.0.0.1:8003でアプリを起動します。
Docker、既存の `data/index.db`、未追跡PDF、リポジトリ直下の`.env`には依存しません。

### 初回セットアップ

Python 3.13（または `pyproject.toml` の要求を満たすPython）とNode.js/npmを用意し、
リポジトリ直下で次を一度実行します。

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install -e .
npm ci
npx playwright install --with-deps chromium
```

以後は次の1コマンドで、インデックス構築、サーバー起動、readiness確認、Playwright
全29件の実行、サーバー停止、一時データ削除まで行います。

```sh
npm run test:ui
```

`playwright.config.js` はChromiumのみ、`http://127.0.0.1:8003`をbaseURL、workers=1
を設定しています。失敗時のtrace・screenshot・videoを保持し、ローカルではlist、CI
環境ではGitHub reporterを使用します。必要なテストだけを実行する場合は、例えば
`npm run test:ui -- tests/playwright/search_multiple_adds.spec.js` とします。

実行スクリプトは `.env` をsourceせず、アプリが自動読込する設定項目もBOOKS_DIR、
DB_DIR、SCRAPBOX、PDF保存先、章分割上限、DEMO_MODEなどを明示的に隔離値へ設定します。終了時は
成功・失敗にかかわらずtrapでサーバーを停止し、一時ディレクトリを削除します。

GitHub Actionsのworkflowはまだ実装していません。

## --workers=1 が必要な理由（暫定措置）

現在、Playwright の E2E テストは `--workers=1` を指定して直列で実行されています。
これは、以下の共有状態が並行テスト間で競合し、ランダムなテスト失敗（Flaky test）を引き起こすためです。

- **同一の 127.0.0.1:8003 へのアクセス**
- **同一の SQLite データベース**
- **ブラウザ側で共有される localStorage (特に `tsundoku-cart`, `active_pack` キー)**
- **テスト間で共有されるアプリケーション状態**

並行実行時にテストAが `active_pack` を切り替えたタイミングで、テストBが資料への追加を実行しようとすると、テストB側で「アクティブな資料が見つかりません」などのエラーとなり、意図しないテスト失敗が発生します。
この競合はアプリケーション自体の制約ではなく、テスト実行環境において同じ DB・オリジン・ブラウザコンテキストの状態を共有していることによるものです。

---

## 将来対応 TODO

テストを完全に独立させ、将来的に並列実行を安全に再解禁するために、以下のいずれかのアプローチによるテスト基盤の改善を計画しています。

- [ ] **workerごとの別DBの分離**: Playwright の各 worker プロセスごとに、独立したテスト用 SQLite データベースファイル（例: `data/test_w1.db`, `data/test_w2.db`）を生成して使用する。
- [ ] **workerごとの別サーバー・別ポート化**: 各 worker ごとに異なるポート番号（例: `8003`, `8004`, ...）で FastAPI 開発サーバーのプロセスを個別に起動して実行する。
- [ ] **DBとlocalStorageの完全な初期化**: テストの開始（`beforeEach`）および終了（`afterEach`）時に、テスト用の DB レコードを完全にロールバック/クリーンアップし、ブラウザの localStorage / Cookie を完全にクリアする。
- [ ] **テスト単位での active_pack 分離**: 同時に実行されるテスト同士が干渉しないよう、ランダムな UUID 等を用いた固有の名前で pack を作成・アクティブ化し、DB/ローカルストレージ上でのキーの衝突を防ぐ。
