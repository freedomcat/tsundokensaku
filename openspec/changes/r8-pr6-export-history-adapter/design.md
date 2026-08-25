## 利用者から見て変わらないこと

- 書き出しボタンを押して得られるダウンロード結果（JSON/ZIPの中身・ファイル名）・HTTPステータス・エラーメッセージは変わらない。
- 書き出し成功後に記録される履歴（いつ・どの資料を・どの形式で書き出したか）の記録タイミング（レスポンス構築後）は変わらない。
- 履歴記録が失敗しても、書き出し自体のレスポンスには影響しない（ベストエフォート）という現在の挙動は変わらない。
- 同一資料を複数回書き出すと、去重せず毎回1行記録される現在の挙動は変わらない。
- `GET /api/export-events`（履歴一覧API）の挙動・レスポンス形式は変わらない。

## 今回内部で移すこと

`web.py`の`api_export_pack`に直書きされている「書き出し成功後、別のDB接続を開いて履歴を1行記録し、失敗してもログに残すだけで書き出し自体は失敗させない」という一連の処理を、Web（FastAPI）と無関係な`export_service.py`側の関数へ移す。`web.py`側には「Responseを組み立てたら、この関数へ記録を指示する」という1行の呼び出しだけが残る。`database.py`の`record_export_event`本体・SQL・テーブル定義・記録するJSONの形は変更しない。

## 実装前に確定が必要な事項（最大3件）

本designは、以下3点についてレビューでの確認・確定を要する。着手時点の判断を示すが、レビューで異なる結論になった場合はdesign.mdを更新してから実装する。

### (1) `api_export_pack`に残るpack/items読取・JSON/archive振り分けをPR6でserviceへ移すか

**現時点の判断: 移さない（web.pyに残す）。**

根拠:
- 詳細設計書§8「`web.py`に残す責務」（160行目）は「serviceが生成したResponseの構築に成功した後で、成功履歴のベストエフォート記録をserviceへ指示する順序」とだけ述べ、pack/items読取の移動には触れていない。
- §9「`export_service.py`へ移す責務」でpack/items取得への言及があるのはpreview用途（`build_pack_export_preview`が単独ユースケースとしてpack取得からplanまで一括で担う）のみで、JSON/archiveの各準備関数（`prepare_json_export`／`prepare_archive_export`）は既にpack/itemsを引数として受け取る設計になっている（PR4・PR5で確定済み）。これは、JSON/archiveが同一のpack/itemsを共有するため、web.py側で1回だけ読み取って両関数へ渡す、という現在の構造を前提にしている。
- §22 PR6の「変更対象」（612行目）は「`export_service.py`、`web.py`、event/web tests、R8文書/ROADMAP/棚卸しの完了反映」であり、pack/items読取・振り分けの移動は挙げられていない。PR6の「目的」も「ベストエフォートevent helperをserviceへ寄せ、2 routeをHTTP入力・変換・Response中心にする」に限定されている。
- ただし、§27「完了条件」（688行目）は「`web.py`に残るのがquery受領、HTTP変換、Response/header、Response後の記録指示である」とR8全体の最終ゴールを述べており、字義通りに読むとpack取得もここに含まれない可能性がある。この文言とPR6個別の変更対象記述との間に軽微な緊張があるため、レビューでの確認を要する。

この判断を採用する場合、pack不在時の404判定（`get_pack(connection, pack_id) is None`）は現状どおりweb.py側に残る。

### (2) 新設helper関数の名称・シグネチャ

以下を候補として提案する。PR4の`PreparedJsonExport`／PR5の`PreparedArchiveExport`と同様、内部API命名でありレビュー時に確定してよい事項として扱う。

```python
def record_export_event_best_effort(
    *,
    db_path: Path,
    pack_id: int | None,
    pack_name: str,
    profile: str,
    format: str,
    items: list[PackItemRecord],
) -> None:
    """書き出し成功後の履歴記録をベストエフォートで試みる。FastAPI非依存。

    `_open_pack_connection(db_path)`（schema保証込み）で別接続を開いて
    `database.record_export_event`を呼び、成功・失敗に関わらず接続をcloseする。
    記録に失敗しても例外を外へ伝播させず、ログ出力のみ行う
    （呼び出し元のレスポンスを壊さない）。
    """
    connection = _open_pack_connection(db_path)
    try:
        database.record_export_event(
            connection,
            pack_id=pack_id,
            pack_name=pack_name,
            profile=profile,
            format=format,
            items=items,
        )
    except Exception:
        logging.exception("export_events の記録に失敗しました（エクスポート本体は正常）")
    finally:
        connection.close()
```

`db_path`を引数として受け取り、`web.py`側は`get_db_path()`の結果をそのまま渡す（PR3〜PR5で確立した「時刻・パス解決はweb.py側で行い、値として渡す」パターンを踏襲する）。

接続生成には`database.connect(db_path)`を直接使わず、既存の`export_service._open_pack_connection(db_path)`（`connect()`後に`ensure_pack_schema(connection)`を実行する）を再利用する。現行の`web._pack_connection()`はpack読取・event記録どちらの接続でもこのschema保証を行っており、`database.connect(db_path)`へ単純に置き換えるとevent用接続のschema保証が欠落する。schema保証自体が失敗した場合も、helper内の`try/except Exception`が捕捉するため、既存のベストエフォート契約（event記録の失敗がexport成功を壊さない）は変わらない。

### (3) 既存テストの移動方法・patch対象

`tests/test_web.py`の`ExportEventRecordingTest`のうち、以下2件は`tsundokensaku.web`名前空間の関数を直接`patch`している。

- `test_record_failure_does_not_break_export`: `patch("tsundokensaku.web.record_export_event", side_effect=RuntimeError(...))`
- `test_read_connection_closes_before_event_connection_opens`: `patch("tsundokensaku.web.connect", ...)`、`patch("tsundokensaku.web.record_export_event", ...)`で接続・記録の呼び出し順序をspyする

記録処理が`export_service.py`側へ移ると、これらのpatch対象（`tsundokensaku.web.record_export_event`・`tsundokensaku.web.connect`）はもう記録処理の実行経路に乗らなくなる（PR5で`tsundokensaku.web.collect_item_stats`のpatchが効かなくなったのと同型の問題）。

**現時点の判断（レビュー指摘により訂正）:** 当初`patch("tsundokensaku.database.record_export_event", ...)`・`patch("tsundokensaku.database.connect", ...)`に統一する案を検討したが、`web.py`は`from tsundokensaku.database import connect`のように`connect`・`record_export_event`を個別関数importしている（`tsundokensaku.web`名前空間へ束縛済み）。`patch("tsundokensaku.database.connect", ...)`は`tsundokensaku.database`モジュール側の属性を差し替えるだけで、既にimport済みの`tsundokensaku.web.connect`という別の名前束縛には影響しない。そのため、この案ではpack読取側の接続（web.py経由）を観測できず、成り立たない。

代わりに、serviceの責務とroute全体の時系列契約を混ぜず、以下のようにテストを分ける。

- `tests/test_export_service.py`: `database.connect`・`database.record_export_event`をpatchし（`export_service.py`は`from tsundokensaku import database`でモジュールごとimportしているため、この形でpatchが効く）、`record_export_event_best_effort`を直接呼び出して、event helper単体の「接続→記録→close」「記録失敗を外へ伝播させない」を確認する。
- `tests/test_web.py`: `patch("tsundokensaku.web.connect", ...)`でpack読取側の接続を観測しつつ、`export_service.record_export_event_best_effort`をモックして、read接続のclose後・Response構築成功後にhelperが指示されることを確認する。

この方式で、接続順序spyテスト（`test_read_connection_closes_before_event_connection_opens`相当）は`tests/test_web.py`側（`web.connect`観測＋`record_export_event_best_effort`モック）と`tests/test_export_service.py`側（helper単体の接続→記録→close）に分割移設する。

`tests/test_web.py`には、「Response構築成功後にhelperが呼ばれること」「Response構築前（＝出力生成が失敗した経路）ではhelperが呼ばれないこと」を、PR4の`test_json_export_returns_service_content_via_http_response`と同様に`export_service.record_export_event_best_effort`をモックして確認するHTTP契約テストを残す。

## Context

現状（PR5完了時点）、`web.py`の`api_export_pack`（828行目付近）は次の責務を持つ。

1. profile解決（`export_service.resolve_external_profile`） — `ValueError`を400へ変換。
2. format解決（`export_service.resolve_export_format`） — `ValueError`を400へ変換。
3. pack/items読取（`_pack_connection()`で接続を開き、`get_pack`が`None`なら404、`get_pack_items`で項目取得後に接続close）。
4. format振り分け（`format == "json"`なら`_export_pack_json`、それ以外なら`_export_pack_archive`）でResponseを構築。
5. 成功履歴のベストエフォート記録（別接続を`_pack_connection()`で開き、`record_export_event`を呼び、`try/except Exception`で失敗をログのみに留め、`finally`で接続close）。
6. Responseを返す。

このうち5.が本PRの移動対象である。`record_export_event`（`database.py`1450行目）自体は既に接続・pack_id・pack_name・profile・format・itemsを引数に取る純粋関数であり、FastAPI非依存である。`web.py`側が担っているのは「別接続を開く」「例外を握りつぶす」という進行制御だけであり、これは詳細設計書§9が「成功履歴を別接続でベストエフォート記録する非HTTP helper」として`export_service.py`側の責務に位置付けているものである。

## Goals / Non-Goals

**Goals:**
- 成功履歴のベストエフォート記録処理（接続生成・`record_export_event`呼び出し・失敗握りつぶし・接続close）を`export_service.py`の新しい公開関数へ移す。
- `web.py`の`api_export_pack`を、Response構築後にこの関数を呼ぶ1行の指示に縮小する。
- DB接続の生成・close順序（pack read接続を出力生成前にclose、eventは出力生成・Response構築成功後の別接続）を維持する。

**Non-Goals（proposal.mdのNon-goalsに加え、design-level境界）:**
- pack/items読取・JSON/archiveへの振り分けの移動可否は前掲(1)のとおりレビュー確定事項とし、本designでは「移さない」を暫定案として採用する。
- `record_export_event`・`export_events`テーブルschema・`items_json`のフィールド構成は変更しない（D7スコープ）。

## Decisions

### 1. 失敗の扱いはservice側のhelper内で完結させる

現状どおり、履歴記録の失敗は`logging.exception`でログのみ行い、例外を呼び出し元（`web.py`）へ伝播させない。この`try/except`をhelper関数内に移すことで、`web.py`側は例外処理を持たない単純な呼び出しになる。

### 2. `db_path`はweb.pyが解決して引数で渡す

`get_db_path()`の呼び出しはweb.py側に残し、解決済みの`Path`をhelper関数へ渡す。PR3〜PR5で確立した「時刻・パス等の環境依存値はweb.py側で解決し、service関数へは値として渡す」パターンをそのまま踏襲する。

### 3. 呼び出し順序を変えない

`api_export_pack`は、Response（`_export_pack_json`または`_export_pack_archive`の戻り値）を構築した**後**にhelperを呼ぶ。Response構築が失敗した場合（`HTTPException`発生時）はhelperを呼ばない、という現在の制御フロー（例外がhelper呼び出しに到達する前に送出される）をそのまま維持する。

### 4. DB接続ライフサイクルを維持する

詳細設計書§16.1・§19.2-8が固定する「pack読取接続は出力生成前にclose、eventは出力生成/Response構築成功後の別接続」という順序を、helper関数内に処理を移してもそのまま保つ。event用接続の生成には`database.connect(db_path)`を直接使わず、既存の`export_service._open_pack_connection(db_path)`（schema保証込み）を再利用し、現行`web._pack_connection()`が行っているschema保証を欠落させない。

### 5. テスト配置

PR3〜PR5のテスト配置方針（route/TestClient経由の契約に限定して`test_web.py`に残す）を踏襲する。

- `tests/test_export_service.py`が担当する（詳細な記録契約。`database.connect`・`database.record_export_event`をpatch）:
  - `record_export_event_best_effort`を直接呼び出し、`database.record_export_event`が正しい引数で呼ばれること
  - `database.record_export_event`が例外を送出しても、`record_export_event_best_effort`が例外を外へ伝播させないこと
  - 接続の生成・close順序（`database.connect`→`database.record_export_event`→close）
- `tests/test_web.py`が担当する（HTTP契約。前掲(3)を踏まえた移設後の構成）:
  - `TestClient`経由の200 status・レスポンスbody（`ExportJsonContractTest`・`ExportArchiveContractTest`と同様、`export_service`側をスタブに差し替えてHTTP層の受け渡しだけを見る）
  - Response構築成功後に`export_service.record_export_event_best_effort`が呼ばれること、失敗経路（空pack・pages未指定・PDF不在等）では呼ばれないこと
  - pack読取接続とevent記録指示の呼び出し順序（前掲(3)の方針で`patch("tsundokensaku.web.connect", ...)`によりpack読取側を観測しつつ、`export_service.record_export_event_best_effort`をモックしてread接続close後に呼ばれることを確認する形に更新）
  - `profile`未指定時に`"standard"`として記録されること、chapterプロファイルで元item snapshotが記録されること等、既存の`items_json`内容確認テストはHTTP経由の統合テストとして残す（`record_export_event`本体の契約はPR6のスコープ外であり、`database.py`側の変更を伴わないため、統合テストとして残すことに問題はない）

## Risks / Trade-offs

- [成功履歴記録をResponse構築より前に呼んでしまう] → 既存テスト（`test_read_connection_closes_before_event_connection_opens`相当）の期待値をそのまま移動先へ引き継ぎ、呼び出し順序を固定する。
- [記録失敗時にexport本体を失敗させてしまう] → `record_export_event_best_effort`内の`try/except Exception`が確実に全ての例外を捕捉することをテストで確認する。
- [pack/items読取・振り分けの移動要否判断を誤ると、後続のR8完了判定に影響する] → 前掲(1)の判断根拠を明記し、レビューで確定させる。
- [既存テストのpatch対象文字列変更に伴い、意図せずテストの検証範囲が緩くなる] → `web.py`は`connect`・`record_export_event`を個別関数importしているため、`tsundokensaku.database`側のシンボルをpatchしてもpack読取側（web.py経由）の呼び出しは観測できない。前掲(3)のとおり、`tests/test_web.py`は`patch("tsundokensaku.web.connect", ...)`でpack読取側を観測しつつ`export_service.record_export_event_best_effort`をモックする方式、`tests/test_export_service.py`は`database.connect`・`database.record_export_event`をpatchする方式、とserviceの責務・route全体の時系列契約を分けて検証し、検証範囲が緩まないことを確認する。

## Migration Plan

本changeはコード移動のみで、production configや外部依存の変更はない。ロールバックは追加コミットのrevertで完結する。本PRの完了により詳細設計書§27の完了条件が満たされ、R8「エクスポート業務ロジック」全体が完了する。ROADMAP・詳細設計書の完了反映は本PR実装完了後の別docsPRで行う。

## Open Questions

前掲「実装前に確定が必要な事項」(1)〜(3)を参照。とくに(1)は、R8全体の完了条件（詳細設計書§27）の解釈に関わるため、実装着手前にレビューで明示的に確定させることを推奨する。
