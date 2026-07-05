# つんどけんさく 提出メモ

## アプリ名

つんどけんさく

## 概要

つんどけんさくは、積読になっている技術書PDFをローカルで全文検索し、必要なページを開いたり切り出したりできる、個人蔵書の検索ハブです。

AIに質問する前に、自分の蔵書やメモから根拠になりそうなページを探し、必要な部分だけをNotebookLMなどへ渡せるようにすることを目的にしています。

## コードURL

https://github.com/freedomcat/tsundokensaku

## 動作方法

```bash
docker compose up --build
```

起動後、以下を開きます。

```text
http://localhost:8000
```

サンプルPDFは `data/books/` に同梱しています。Web UIの設定画面でインデックスを実行すると、PDF本文検索とPDFモーダル表示を試せます。

同梱PDF:

* `data/books/cathedral.pdf` - 伽藍とバザール
* `data/books/noosphere.pdf` - ノウアスフィアの開墾
* `data/books/magicpot.pdf` - 魔法のおなべ

これらは山形浩生さんのサイト <https://cruel.org/> で公開されているPDFです。配布元URLとリンクポリシーはREADMEに記載しています。

## 企画内容

技術書PDFが増えるほど、「どの本に何が書いてあったか」を探すのが難しくなります。

つんどけんさくは、ローカルに置いた技術書PDF、Kindle本情報、Scrapbox/Cosenseメモを横断検索し、必要なページをすぐ開けるようにする個人用検索ハブです。

検索結果からPDFを開き、必要なページだけを切り出してNotebookLMなどに渡せるため、AIに丸ごと本を預けるのではなく、自分の蔵書から必要な材料を選んでAIに渡せます。

## 使用したAI

* ChatGPT
* Codex

## AIを使用して役に立ったこと

* 企画の方向性整理
* READMEや提出資料の構成案作成
* Web UI改善の壁打ち
* Docker構成の整理
* 実装方針の検討
* テスト観点の整理
* エラー調査や修正方針の相談

## 審査時に見てほしいポイント

* PDF、Kindle情報、Scrapbox/Cosenseメモを横断検索できること
* 検索結果からPDFをページ単位で開けること
* 必要ページだけを切り出してNotebookLMなどに渡しやすくしていること
* 外部AI APIや有料クラウド検索に依存せず、ローカルで追加課金なしに動くこと
* CLIとWeb UIの両方を持っていること
