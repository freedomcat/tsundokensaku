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

そのため、PR4では戻り値をJSON専用の狭い型（`bytes`と`filename: str`の組）とする。具体的な型（`tuple[bytes, str]`か小さい`dataclass`か、フィールド名を`PreparedPackExport`と揃えるか）は実装時に確定する（Open Questions参照）。ZIP側と統一するかどうかの判断はPR5着手時に持ち越す。

代替案として§12.3の共通型を今回前倒しで実装する案も検討したが、ZIP側のフィールド要件（`output_kind`分岐の必要性、archive特有のmanifest情報を含めるか等）がPR5で初めて確定するため、今回は採用しない。

### 2. `_now_jst()`の呼び出し元は`web.py`に残す

現状`_export_pack_json`は内部で`_now_jst()`を直接呼んでいる。移動後のservice関数は`datetime.now()`相当を呼ばず、`web.py`が`_now_jst()`で解決したJST時刻を引数として渡す。

これにより、PR2で確立した既存のfixed clock注入パターン（`patch("tsundokensaku.web._now_jst", return_value=fixed_now)`）がそのまま機能する。時刻取得の呼び出し元を変えないことで、テストのmonkeypatch対象を変えずに済む。

### 3. filenameの組み立てはservice側が行い、HTTPヘッダー用のエンコードはweb側が行う

`sanitize_filename_component(pack.name)`とJST日付を使ったfilename文字列の組み立て自体はservice関数の責務とする。`Content-Disposition`ヘッダーに必要な`quote()`によるパーセントエンコードは、HTTP表現の詳細であるため`web.py`側の責務として残す。これは設計書§12.3が示す分担（「contentとfilenameを返すが、MIME typeやContent-Dispositionはwebが組み立てる」）と一致する。

### 4. 検証なしという現状の性質を維持する

JSON exportは現状、`_export_pack_archive`と異なり、空pack・PDF不在・pages不正のいずれについても事前検証を行わず、そのまま200で生成する。この「検証しない」という性質は、責務を移してもservice関数側で新たに検証を追加しない。

### 5. テスト配置

PR3のテスト配置方針（route/TestClient経由の契約に限定して`test_web.py`に残す）を踏襲する。

- `tests/test_export_service.py`へ移す・追加する: JSON構造のexact値テスト（`version`/`name`/`items`各fieldの値）、空pack・PDF不在・pages不正でも生成されること、filenameの組み立て結果（sanitize後の文字列＋日付）。いずれもservice関数を直接呼び出すテストとする。
- `tests/test_web.py`に残す: `TestClient`経由のHTTP status・`Content-Type`・`Content-Disposition`ヘッダーの形状確認、およびJSON export成功時のevent記録順序（`ExportEventRecordingTest`内の該当テスト）。exact body bytesの一字一句比較は、service関数を直接呼ぶ形で`test_export_service.py`側に主として置き、`test_web.py`側はHTTP経由でも同じ結果が得られることを確認する最小限のケース（1件）に絞る候補とする（tasks.mdで具体的な割り振りを確定する）。

## Risks / Trade-offs

- [JSON生成のexact bytes（key順・indent・改行・エンコード）を移動時にうっかり変えてしまう] → 既存`ExportJsonContractTest`の期待値をそのまま移動後のテストに引き継ぎ、移動前後で同一の期待値を使う。
- [`_now_jst()`の呼び出し元変更でfixed clock注入パターンが崩れる] → 決定2のとおり、呼び出し元を`web.py`に残すことで既存のmonkeypatch対象（`tsundokensaku.web._now_jst`）を変えない。
- [event記録順序を移動時に崩す] → event記録の実装・タイミングはPR4で変更しない。Response構築成功後に記録する既存の`try/except`ブロックは`web.py`内にそのまま残す。
- [filenameのsanitize処理をservice/webどちらに置くか実装時に揺れる] → 決定3で「filename全体はservice側」と確定したため、実装時にこれに従う。

## Migration Plan

本changeはコード移動のみで、production configや外部依存の変更はない。ロールバックは追加コミットのrevertで完結する。

## Open Questions

- service関数の戻り値の具体的な型（`tuple[bytes, str]`か小さい`dataclass`か、フィールド名を設計書§12.3の`PreparedPackExport`と揃えるか）は実装時に確定する。この判断はJSON専用スコープの範囲では仕様やタスク分解を変えないため、tasksの着手を妨げない。
- `test_web.py`に残すexact body bytesテストの件数（1件に絞るか、既存3件をすべて残すか）は、tasks.md実装時に既存テストの重複度を見て確定する。
