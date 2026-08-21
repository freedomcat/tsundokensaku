## Context

現状（PR3完了時点）、`web.py`の`_export_pack_json(pack, items)`（798行付近）が次をすべて1関数内で行っている。

- `items`配列の組み立て（`pdf_path`/`title`/`pages`/`collapsed`/`addedAt`/`position`）
- `json.dumps(export_data, ensure_ascii=False, indent=2).encode("utf-8")`によるbytes化
- `_now_jst()`を直接呼んでのfilename決定（`{sanitize_filename_component(pack.name)}_{JST YYYYMMDD}.json`）
- `Response`生成（`media_type="application/json"`、`Content-Disposition`ヘッダー）

呼び出し元の`api_export_pack`（958行付近）は、`format == "json"`のときこの関数を呼ぶだけで、profile/format解決（PR3で`export_service.py`へ既に分離済み）とは異なり、JSON生成部分はまだ`web.py`に残っている。

[PR2](../archive/2026-08-19-r8-pr2-characterization-tests/)で、`tests/test_web.py`の`ExportJsonContractTest`が次を固定済み。

- fixed clock注入下でのexact body bytes（UTF-8日本語、key順、indent 2、LF、末尾改行なし）
- filename、`application/json` MIME、Content-Disposition
- 空pack、PDF不在、pages不正な資料でも200になること

[PR3](../archive/2026-08-20-r8-pr3-preview-request-policy/)は、profile/format policyとpreview系をFastAPI非依存の`export_service.py`へ移す際、「route/TestClient経由の契約に限定してテストを残す」という配置方針を確立している。本designはこの方針をJSON exportにも踏襲する。

## Goals / Non-Goals

**Goals:**
- JSON export準備（データ組み立て・bytes化・filename決定）を、FastAPI型を返さない`export_service.py`の関数へ移す。
- `web.py`側の責務を、service関数の戻り値（bytes・filename）をHTTP `Response`へ変換するだけに絞る。
- PR2で固定したexact bytes/header/空pack等の契約を、移動の前後で一致させる。
- 責務移動に応じてテストを`tests/test_export_service.py`と`tests/test_web.py`に分担する。

**Non-Goals（proposal.mdのNon-goalsに加え、design-level境界）:**
- 設計書§12.3の`PreparedPackExport`/`prepare_pack_export`（JSON/ZIP統一型）をこのPRで実装しない。JSON専用の狭い戻り値を採用する（決定1）。
- `_now_jst()`自体の実装や意味を変更しない。呼び出し元を変えるだけである（決定2）。
- event記録（`record_export_event`）のタイミング・実装に触れない。

## Decisions

### 1. 戻り値はJSON専用の狭い型にする（統一型は先取りしない）

設計書§12.3は`PreparedPackExport`（`content: bytes`, `download_filename: str`, `output_kind: Literal["json", "zip"]`等）というJSON/ZIP共通型を候補として示しているが、これは「候補であり実装前に確定する」とも明記されている。ZIP側（PR5スコープ）の要件が固まっていない現時点で共通型を先取りすると、PR4の変更対象が本来のJSON exportの範囲を超え、PR5の設計判断を先取りしてしまう。

そのため、PR4では戻り値をJSON専用の狭い型とし、次の`dataclass`を採用する。

```python
@dataclass(frozen=True)
class PreparedJsonExport:
    content: bytes
    filename: str
```

`tuple[bytes, str]`ではなく`dataclass`を選ぶ理由は次の3点である。

- **意味の明確化**: `tuple[bytes, str]`は呼び出し側で`result[0]`/`result[1]`のように位置で取り出すことになり、どちらがcontentでどちらがfilenameかがコード上自明でない。フィールド名を持つ`dataclass`はこの取り違えを防ぐ。
- **将来の拡張耐性**: PR5でZIP側の要件が確定し、この型をJSON/ZIP共通の`PreparedPackExport`へ発展・統合する判断になった場合、`dataclass`はフィールド追加（例: `output_kind`）が既存コードを壊さずに行える。`tuple`は要素追加が位置ズレのリスクを伴う。
- **命名の一貫性**: 設計書§12.3の`PreparedPackExport`と同じ「Prepared+名詞」という命名にすることで、JSON専用型がPR5以降の統合候補であることが名前から分かる。ただし`PreparedPackExport`という名前そのものは§12.3が「候補であり実装前に確定する」としているため、ここでは`PreparedJsonExport`というJSON専用の名前を採用し、`PreparedPackExport`への改名・統合はPR5着手時の判断に持ち越す。

代替案として§12.3の共通型（`output_kind: Literal["json", "zip"]`を含む`PreparedPackExport`）を今回前倒しで実装する案も検討したが、ZIP側のフィールド要件（`output_kind`分岐の必要性、archive特有のmanifest情報を含めるか等）がPR5で初めて確定するため、今回は採用しない。`tuple[bytes, str]`のまま実装する案も検討したが、上記の理由により`dataclass`を優先する。

### 2. `_now_jst()`の呼び出し元は`web.py`に残す

現状`_export_pack_json`は内部で`_now_jst()`を直接呼んでいる。移動後のservice関数は`datetime.now()`相当を呼ばず、`web.py`が`_now_jst()`で解決したJST時刻を引数として渡す。

これにより、PR2で確立した既存のfixed clock注入パターン（`patch("tsundokensaku.web._now_jst", return_value=fixed_now)`）がそのまま機能する。時刻取得の呼び出し元を変えないことで、テストのmonkeypatch対象を変えずに済む。

### 3. filenameの組み立てはservice側が行い、HTTPヘッダー用のエンコードはweb側が行う

`sanitize_filename_component(pack.name)`とJST日付を使ったfilename文字列の組み立て自体はservice関数の責務とする。`Content-Disposition`ヘッダーに必要な`quote()`によるパーセントエンコードは、HTTP表現の詳細であるため`web.py`側の責務として残す。これは設計書§12.3が示す分担（「contentとfilenameを返すが、MIME typeやContent-Dispositionはwebが組み立てる」）と一致する。

### 4. 検証なしという現状の性質を維持する

JSON exportは現状、`_export_pack_archive`と異なり、空pack・PDF不在・pages不正のいずれについても事前検証を行わず、そのまま200で生成する。この「検証しない」という性質は、責務を移してもservice関数側で新たに検証を追加しない。

### 5. テスト配置

PR3のテスト配置方針（route/TestClient経由の契約に限定して`test_web.py`に残す）を踏襲する。

最終的な分担は次のとおり確定する。

- `tests/test_export_service.py`が担当する（詳細な生成契約）:
  - JSON構造のexact値テスト（`version`/`name`/`items`各fieldの値、UTF-8日本語、key順、indent 2、LF、末尾改行なし）
  - 空pack・PDF不在・pages不正な資料でも検証なしで生成されること
  - filenameの組み立て結果（sanitize後の文字列＋日付）
  - いずれも`PreparedJsonExport`を返すservice関数を直接呼び出すテストとする。
- `tests/test_web.py`が担当する（HTTP契約。通常成功ケース1件に絞る）:
  - 通常の成功ケース（PDF欠損等の異常がない、項目が1件以上あるpack）1件について、`TestClient`経由で次を確認する: HTTPステータス200、`Content-Type: application/json`、`Content-Disposition`ヘッダー、レスポンスbodyがservice関数の`content`と一致すること（HTTP層がbytesを変形せずそのまま受け渡していることの確認）。
  - JSON export成功時のevent記録（`ExportEventRecordingTest`内の該当テスト）。
  - 空pack・PDF不在・pages不正等のJSON生成詳細（exact bytesの内容そのもの）は`test_web.py`側では検証しない。これらは`test_export_service.py`側の責務とし、`test_web.py`は「HTTP層が正しく受け渡しているか」だけを見る。
  - 既存`ExportJsonContractTest`の3テストのうち、`test_json_export_empty_pack_returns_200`・`test_json_export_missing_pdf_and_invalid_pages_returns_200`は`test_export_service.py`へ移し、`test_web.py`からは削除する（4.1参照）。残る1件（HTTP契約の代表）は、実装時にレビュー指摘を受けて`export_service.prepare_json_export`をスタブの`PreparedJsonExport`へ差し替える形へ改め、`test_json_export_returns_service_content_via_http_response`に改名した。JSON文字列の完全一致比較は行わず、レスポンスbodyがservice戻り値の`content`と一致すること、`_now_jst()`の戻り値がそのまま`exported_at`としてserviceへ渡ることのみを確認する。

## Risks / Trade-offs

- [JSON生成のexact bytes（key順・indent・改行・エンコード）を移動時にうっかり変えてしまう] → 既存`ExportJsonContractTest`の期待値をそのまま移動後のテストに引き継ぎ、移動前後で同一の期待値を使う。
- [`_now_jst()`の呼び出し元変更でfixed clock注入パターンが崩れる] → 決定2のとおり、呼び出し元を`web.py`に残すことで既存のmonkeypatch対象（`tsundokensaku.web._now_jst`）を変えない。
- [event記録順序を移動時に崩す] → event記録の実装・タイミングはPR4で変更しない。Response構築成功後に記録する既存の`try/except`ブロックは`web.py`内にそのまま残す。
- [filenameのsanitize処理をservice/webどちらに置くか実装時に揺れる] → 決定3で「filename全体はservice側」と確定したため、実装時にこれに従う。

## Migration Plan

本changeはコード移動のみで、production configや外部依存の変更はない。ロールバックは追加コミットのrevertで完結する。

## Open Questions

なし。service関数の戻り値の型（決定1）と`test_web.py`に残すテストの粒度（決定5）は、レビューでの指摘を受けて本designで確定した。
