# R7-1: PDFアップロード保存の切り出し — 完了（PR #20・#21、マージコミット `6cee6830e4afd9fe22374b21a57c7c49d1de560d`）

[R7全体設計](../central-file-refactoring-inventory.md) / [ROADMAP](../../ROADMAP.md)

状態: 実装済み。以下は既存の詳細設計と、実装によって確定した契約・結果を移したものであり、内容は変更していない。

PR #20（マージコミット `ecd0faaa48e2fab65bd3882d83f70e9a910ca8e7`）で現在のHTTP・保存・境界契約をcharacterization testとして固定し、PR #21（マージコミット `6cee6830e4afd9fe22374b21a57c7c49d1de560d`）で`save_uploaded_pdf`のファイル配置責務を`pdf_import_service.py`へ分離した。公開APIは`save_uploaded_pdf(filename, content, books_dir, *, relative_path=None) -> Path`。`web.py`にはHTTP入力・demo mode判定・bodyと`%PDF`の検証・例外のHTTP 400変換・HTTP 201レスポンスを残し、互換ラッパーは設けていない。BOOKS_DIR自身を指す特殊ケース、TOCTOU、非atomic書き込みなどの既知制約は改善せず維持した。PR #20ではPython 496件・Playwright 29件、PR #21ではPython 497件・Playwright 29件が成功し、PR #21の独立レビューはAPPROVEだった。以下は実装時の詳細設計・記録として維持する。

### 目的

- `web.py`からPDFアップロードのファイル配置責務（保存先解決・境界検証・衝突回避・書き込み）を分離する。
- HTTP入力の受け取り・レスポンス生成・demo mode判定は`web.py`に残す。
- 公開URL・HTTPメソッド・status code・レスポンス本文・保存位置・既存ファイル非上書きを変更しない。
- セキュリティ改善と責務分離を混在させない。今回は現状挙動の固定と移動のみを行い、新しい防御は追加しない。

### 対象（分離前の行番号、`develop` HEAD `c720f1f`時点で確認済み）

- `POST /settings/pdf-upload`（`upload_pdf`、web.py 1520-1539行）。
- `save_uploaded_pdf`（web.py 411-428行）。

### `pdf_import_service.py`へ移した責務（`save_uploaded_pdf`本体、ロジック無変更で移動）

- BOOKS_DIRを基準とした保存先解決（`books_dir.expanduser().resolve()`）。
- BOOKS_DIRの自動作成（`books_root.mkdir(parents=True, exist_ok=True)`）。
- `relative_path`優先・`filename`フォールバックによる保存先の構築（`base_name = Path(relative_path or filename)`。`relative_path`が指定されていれば`filename`は拡張子チェック・パス構築のいずれにも使われない、という現在挙動を含む）。
- `.pdf`拡張子検証（大文字小文字を無視。`.lower().endswith(".pdf")`）。
- 解決後パスがBOOKS_DIR配下であることの確認（`destination.relative_to(books_root)`）。
- 保存先の親ディレクトリ作成（`destination.parent.mkdir(parents=True, exist_ok=True)`）。
- 同名衝突時の一意な保存先選択（`paths.unique_destination_path`経由、既存の`_unique_destination_path`ラッパーはR3で委譲済み）。
- byte列の書き込み（`destination.write_bytes(content)`）。
- 保存先Pathの返却。

移動後は`pdf_import_service.py`から`paths.unique_destination_path`を直接呼ぶ。`web.py`の`_unique_destination_path`ラッパーや同等処理の再実装には依存しない。

### `web.py`に残すもの

- `POST /settings/pdf-upload`のルート定義そのもの（`upload_pdf`）。
- demo mode判定（`is_demo_mode()`。bodyを読む前に早期returnする現在の順序を維持する）。
- `filename`のクエリパラメータ受け取りと空判定（`filename.strip()`）。
- `relative_path`のクエリパラメータ受け取りと空文字→`None`変換（`relative_path or None`）。
- raw bodyの読み取り（`await request.body()`）。
- body空判定。
- `%PDF`マジックバイト判定。
- serviceの例外（`ValueError`等）からHTTP 400への変換（`except Exception as exc: return PlainTextResponse(str(exc), status_code=400)`。現状は`Exception`を包括的に捕捉しており、これを維持する）。
- HTTP 201のレスポンス生成（`PlainTextResponse(str(saved), status_code=201)`）。

### HTTP入力正規化とservice入力契約の境界（判断とその理由）

- `filename`の空判定（`.strip()`）と`relative_path`の空文字→`None`変換は、HTTPクエリパラメータ特有の「送信されたが空文字列」という入力形式に起因するため`web.py`に残す。R2（`config.py`）・R6（`index_job.py`）で踏襲してきた「HTTPフォーム/クエリ特有の入力形式変換はハンドラ側に残す」というパターン（§8「段階6」の`force`パラメータ変換と同様）を踏襲する。
- 一方、`base_name = Path(relative_path or filename)`という「`relative_path`が`filename`より優先される」という判断は、実際の保存先パス決定ロジックの一部であり、service側（移動後は`pdf_import_service.save_uploaded_pdf`）の内部に残す。これは「HTTPパラメータの正規化」ではなく「2つの入力候補からどちらを保存先の基準にするか」という業務判断であり、既存の公開シグネチャ（`filename`・`relative_path`を両方受け取る）を維持したまま移動すれば自然にservice側へ移る。web.py側で事前に1つの値へ畳み込む変更は、公開APIの形を変えることになり「単純な移動」の範囲を超えるため今回は行わない。

### 分離先モジュール: `pdf_import_service.py`

R7-1（PDFアップロード保存）とR7-2（PDFディレクトリ取り込み、未設計）はいずれも「外部からPDFをBOOKS_DIRへ新規に持ち込む」という点で変更理由が近く、"import"という語で束ねられる。一方R7-3（Scrapbox JSON）はPDF以外のフォーマットの取り込みであり、R7-4（既存PDFの閲覧・変換・検索）は「取り込み」ではなく「既存ファイルへの操作」であるため、いずれも`pdf_import_service.py`には混在させない。したがって`pdf_import_service.py`は、今回のR7-1と将来のR7-2を同居させる名称として妥当と判断する。R7全体を指す`pdf_service.py`という名前は不採用とする（§3参照）。

### 依存方向: `books_dir`引数渡しを採用（`config.get_books_dir()`直接呼び出しとの比較）

現行の`save_uploaded_pdf`は既に`books_dir: Path`を呼び出し側から受け取る設計であり、この形をそのまま維持する。依存方向は`web` → `pdf_import_service` → `paths`およびファイルシステムとする。`pdf_import_service.py`は`config`・`database`・`web`・FastAPIをimportせず、`paths`から`pdf_import_service`への逆依存も作らない。この一方向の依存で循環importは発生しない。

理由:
- **依存方向**: `pdf_import_service.py`の依存を`paths`とファイルシステムに限定する。「どのBOOKS_DIRを使うか」（R2の責務）と「どこに保存するか」（R7-1の責務）を混在させず、一意名生成はR3で分離済みの`paths.unique_destination_path`を再利用する。
- **テスト容易性**: 呼び出し側が任意の`books_dir`（`tempfile.TemporaryDirectory()`等）を直接渡せるため、`os.environ`のmonkeypatchなしにテストできる。既存の`test_save_uploaded_pdf_writes_unique_file`も`books_dir`を直接渡す形で書かれており、この形を崩さない。
- **R2の責務境界**: R2完了時点で「`config.py`は...`web.py`・...のいずれもimportしない」という一方向の依存が確立している（§3 R2「実装内容」）。今回は既存シグネチャを変えない方が影響範囲が小さく、`upload_pdf`ハンドラが`get_books_dir()`を呼んでから`save_uploaded_pdf`へ渡す既存の流れとも一致する。
- **対比**: R6（`index_job.py`）は`config.get_books_dir()`を直接呼ぶ設計を採用したが、これは`index_job.start()`がバックグラウンドスレッド内で非同期に実行され、リクエストハンドラから都度`books_dir`を明示的に受け渡す経路がなかったためである（§8「段階6」）。R7-1は同期的にHTTPハンドラから直接呼ばれ、既存シグネチャが`books_dir`を引数に持つため、この理由はR7-1には当てはまらない。

### service公開APIとimport互換性

HTTP公開契約は維持し、新しいservice関数の引数形も既存関数と同じ形を維持する（ロジック無変更の純粋移動）。一方、`tsundokensaku.web.save_uploaded_pdf`というPython import pathは削除する。このimport pathは内部APIとして扱い、直接monkeypatchが0件であることを根拠に互換ラッパーは残さない（詳細は下記「互換ラッパー方針」参照）。

```python
def save_uploaded_pdf(
    filename: str,
    content: bytes,
    books_dir: Path,
    *,
    relative_path: str | None = None,
) -> Path:
    ...
```

- `filename: str`（必須、位置引数）: `web.py`側で`.strip()`による空判定済みだが、値自体はstrip前の生の文字列のまま渡される（呼び出し元は正規化しない）。`relative_path`が指定されている場合、この値は保存先の決定に使われない。
- `content: bytes`（必須、位置引数）: `web.py`側でempty判定・`%PDF`判定済みのbytesがそのまま渡される。service側は内容を検証しない。
- `books_dir: Path`（必須、位置引数）: `config.get_books_dir()`の戻り値を呼び出し元が渡す。`expanduser().resolve()`はservice内部で行う（既存通り）。
- `relative_path: str | None`（キーワード専用、任意、デフォルト`None`）: `web.py`側で空文字列を`None`に変換してから渡す契約とする（既存通り）。空文字列がそのままservice層に渡ることは呼び出し元の変換により発生しない。path separatorとして扱われるのは`/`のみ（現在の実行環境=Linux/Dockerでは`\`はファイル名の一部として扱われ、ディレクトリ区切りとして機能しない。現在挙動として記録し、Windows環境での挙動保証はしない）。
- 戻り値: `Path`（保存先の絶対パス。`_unique_destination_path`による一意化後の実際の保存先）。
- 例外: 下記「例外方針」参照。
- ファイル副作用: BOOKS_DIR（存在しなければ作成）・保存先の親ディレクトリ（存在しなければ作成）・保存先ファイルへの書き込み。通常の境界外パスは拒否されるが、BOOKS_DIR自身を指す特殊条件には既知制約があるため、BOOKS_DIR外への副作用が絶対に発生しないとは扱わない（下記セキュリティ契約参照）。

### 例外方針

service層（`pdf_import_service.save_uploaded_pdf`）は`HTTPException`を送出しない。明示的な入力エラー・保存境界エラーは`ValueError`とし、一意名候補の上限到達時は`FileExistsError`、`resolve()`・`mkdir()`・`write_bytes()`等の失敗は`OSError`系が透過する現在挙動を維持する。

現在の失敗条件と対応する例外（現状のまま維持、メッセージ文言も変更しない）:

- `.pdf`以外（`relative_path`または`filename`の末尾）: `ValueError("PDF ファイルのみ受け付けます")`。
- 解決後パスがBOOKS_DIR外（`../`・絶対パス・symlink経由を含む）: `ValueError("保存先が不正です")`。
- 一意名選択の試行上限超過（`stem (2)`〜`stem (9999)`が全て衝突）: `FileExistsError`（`paths.unique_destination_path`が`destination`のPathオブジェクトを引数として送出し、そのまま透過する。`str(exc)`は通常そのパス文字列となる）。
- 書き込み失敗（権限・ディスク容量等）: `OSError`系（透過、未捕捉）。
- 不正なfilename/relative_path（NUL文字等、ファイルシステムが拒否する文字）: `OSError`系（透過、未捕捉）。

`web.py`側は現在`except Exception as exc: return PlainTextResponse(str(exc), status_code=400)`という包括的な捕捉で、上記いずれの例外も一律400に変換している。これは意味的には`OSError`系を500として扱う方が適切に見えるが、**今回はstatus code・レスポンス本文を変更しないため、この`except Exception`による一律400変換をそのまま維持する**。専用例外クラスへの置き換えは、`except Exception`が全ての例外を捕捉する現状では外部観測される挙動を変えないが、今回は最小差分を優先し導入しない。characterization testでは安定した`ValueError`の文言を対象とし、OS依存の`OSError`文言や`FileExistsError`のパス文字列をHTTP契約として完全固定しない。必要になれば別PRで検討する。

### 互換ラッパー方針

`web.save_uploaded_pdf`について、既存コード・テスト・文書を調査した。

- `tests/test_web.py`が`from tsundokensaku.web import (..., save_uploaded_pdf, ...)`で直接importし、`test_save_uploaded_pdf_writes_unique_file`で直接呼び出している（68行目・172-182行目）。
- `tsundokensaku.web.save_uploaded_pdf`への`monkeypatch`（`patch("tsundokensaku.web.save_uploaded_pdf", ...)`）は0件（既存143箇所超のmonkeypatchの中に含まれない）。
- `upload_pdf`ハンドラ内部からの呼び出しが1箇所（web.py 1535行目）。

**方針: B（内部実装であり外部からの直接patchがないため、`web.py`から除去する）を採用する。**

R2・R4がA案（委譲ラッパーを残す）を採用した理由は「既存133箇所超のmonkeypatchを維持するため」であり、`save_uploaded_pdf`にはこの前提が成立しない（monkeypatch 0件）。R6（`index_job.py`）と同じ状況であり、同じ判断基準を適用する。`tests/test_web.py`の直接import（テストコードのみ）は、PR2で新設する`tests/test_pdf_import_service.py`へテストを移動し、importを`tsundokensaku.pdf_import_service`へ切り替えることで解消する（R6のPR2と同じ手法）。テストの都合だけで本番コードにラッパーを残さない。

`upload_pdf`ハンドラ内部の呼び出しも、`save_uploaded_pdf(...)`から`pdf_import_service.save_uploaded_pdf(...)`への直接呼び出しに書き換える（`index_job.start()`と同じパターン）。

### HTTP契約（現状記録、`develop` HEAD `c720f1f`時点で確認済み）

- URL・method: `POST /settings/pdf-upload`。
- query parameter: `filename: str = ""`（デフォルト空文字）、`relative_path: str = ""`（デフォルト空文字）。
- body形式: raw bytes（`Content-Type`検証なし、`await request.body()`で読み取り）。
- demo mode時: `status_code=403`、body=`"Upload is disabled in demo mode."`（`DEMO_MODE_UPLOAD_MESSAGE`、web.py 140行目）。**bodyを読む前に早期returnする**（`is_demo_mode()`判定が`await request.body()`より前にある）。
- `filename`なし・空白のみ: `status_code=400`、body=`"filename が必要です"`。有効な`relative_path`が指定されていても`filename`の空判定が先に行われ、この場合serviceは呼ばれず、request bodyも読まれない。
- `filename.strip()`は空判定にのみ使用し、空でなければstrip前の生の`filename`を保存処理へ渡す。
- `relative_path`は空文字だけを`None`へ変換する。空白だけの値は真値のため生のままserviceへ渡され、通常は`.pdf`拡張子を満たさず`"PDF ファイルのみ受け付けます"`となる。
- body空: `status_code=400`、body=`"empty body"`。
- `%PDF`で始まらないbody: `status_code=400`、body=`"PDF 以外は受け付けません"`。
- `.pdf`以外の拡張子: `status_code=400`、body=`"PDF ファイルのみ受け付けます"`。末尾を小文字化して判定するため`.PDF`は許可される一方、`.pdf`の後ろに空白がある名前は拒否される。
- Linux上ではUnicode filenameをそのまま保存する。`filename`または`relative_path`の先頭・途中の空白も保持され、末尾が`.pdf`または大文字小文字違いの同拡張子であれば許可される。
- traversal等の境界外: `status_code=400`、body=`"保存先が不正です"`。
- 正常時: `status_code=201`、body=保存先パスの文字列表現（`str(saved)`）。
- 衝突時: `status_code=201`のまま、一意化された別名（例: `sample (2).pdf`）で保存され、bodyはその新しいパス文字列。

日本語・英語のレスポンス文言は上記の通り現物を引用した（`web.py`411-428行目・1520-1539行目・140行目を直接確認済み、推測ではない）。

### セキュリティ契約（現状固定。新しい防御は今回追加しない）

**path traversal**（`(books_root / base_name).resolve()` → `destination.relative_to(books_root)`という現在の実装パターンで検証済み。`pathlib`単体動作で確認済み）:

- `../../etc/passwd.pdf`のような相対traversal: `resolve()`後にBOOKS_DIR外となり、`relative_to`が`ValueError`を送出 → 現在は拒否される。
- `sub/../../escape.pdf`のような多段traversal: 同様に`resolve()`で正規化された結果がBOOKS_DIR外になれば拒否される。
- URL decode後の`../`: `filename`・`relative_path`はFastAPIのクエリパラメータとして受け取る時点で既にURLデコード済みの文字列がPythonの`str`として渡ってくる。service層はデコード処理を行わない・関与しない。デコード後の文字列に対して上記traversal検証が働く。
- 絶対パス（例: `/etc/passwd.pdf`）: `Path("/etc/passwd.pdf").is_absolute()`は`True`となり、`books_root / base_name`は`pathlib`の仕様上`base_name`（絶対パス）がそのまま返る（`books_root`部分は無視される）。この結果も`resolve()`後に`relative_to(books_root)`で拒否される。
- Windows形式区切り文字（`\`）: 現在のデプロイ環境（Linux/Docker）では`\`はパス区切り文字として機能せず、単一のファイル名コンポーネントの一部として扱われる。したがって`sub\evil.pdf`のような入力は`books_root`直下の1階層のファイル名として扱われ、traversalベクタにならない（現在の実行環境における現在挙動として記録。Windows環境での挙動保証はしない）。
- BOOKS_DIR外へ解決される通常のパス: 上記`relative_to`検証で拒否される。一方、`destination`が`books_root`自身と等しい場合、`destination.relative_to(books_root)`は拒否せず`.`を返す。通常はBOOKS_DIR名が`.pdf`でないため先行する拡張子検証で拒否されるが、BOOKS_DIR自身の名前が`.pdf`で終わり、absolute `relative_path`でBOOKS_DIR自身を指定した場合は境界検証を通過しうる。その後、既存ディレクトリとの衝突回避によってBOOKS_DIR外の兄弟パスが候補となる可能性がある。これは現行実装の既知制約として記録し、今回の責務分離では挙動を変更しない。境界強化は別のセキュリティ課題とし、今回のcharacterization testで新しい安全性を保証したことにしない。

**symlink**（`resolve()`のsymlink解決特性により、以下は現在すべて拒否されることを`pathlib`単体動作で確認済み）:

- BOOKS_DIR内の中間ディレクトリが外部を指すsymlinkの場合: `(books_root / base_name).resolve()`がsymlinkを辿って実体パス（BOOKS_DIR外）に解決され、`relative_to`チェックで拒否される。
- 保存先ファイル自体が既存のsymlinkであり、その実体がBOOKS_DIR外を指す場合: 同じ`.resolve()`呼び出しの時点でsymlinkが実体パスに解決され、`relative_to`チェックで拒否される（＝アップロード先ファイル名が既存の外部symlinkと衝突しても、実体パスとして境界検証を通過するため安全）。
- `resolve()`前後の確認: 現在の実装は境界検証を`resolve()`**後**の1回のみ行う（`resolve()`前のパスに対しては検証しない）。これは意図的な多層防御ではなく、`resolve()`がsymlink解決を含むためにpre-resolve検証が不要になっている、という現在の実装の結果である。
- **TOCTOUの可能性**: `destination = (books_root / base_name).resolve()`で境界検証を行った**後**、`destination.parent.mkdir(...)`・`_unique_destination_path(destination)`（`exists()`確認）・`destination.write_bytes(content)`という複数ステップが続く。この間に外部プロセスが`destination`パス（またはその親ディレクトリ）にsymlinkを追加・変更した場合、境界検証をすり抜けて書き込みが行われる可能性は理論上排除されていない。**これは現在から存在する制約であり、今回の移動で悪化させないことを条件とするが、新しい対策（atomic操作・O_NOFOLLOW等）は今回のPRの対象外とする。** 別途セキュリティ課題として扱う（ROADMAP Should「セキュリティ検証の体系化」参照）。

**上書き・競合**:

- 既存ファイルを上書きしない: `paths.unique_destination_path`が`destination.exists()`を確認し、存在すれば`" (2)"`, `" (3)"`, ...という命名規則で衝突を避ける（`stem`+`" ("+index+")"`+`suffix`、`paths.py` 60-70行目）。
- 一意名生成規則: 2から始まり9999まで試行、全て衝突なら`FileExistsError`を送出（現状維持）。
- 一意名選択と書き込みがatomicでない現在制約: `exists()`確認と`write_bytes()`の間に別プロセスが同名で書き込む競合が理論上ありうる（TOCTOU）。今回改善しない。
- 同時upload競合: 今回改善しない（非目標、下記参照）。

### characterization test計画

**既存テストによる保証**:

- `test_save_uploaded_pdf_writes_unique_file`（tests/test_web.py 172-182行目）: `save_uploaded_pdf`の直接テスト。一意名生成・内容の正しい書き分けのみを検証。拡張子検証・境界検証・symlink検証は含まれない。
- `test_pdf_upload_returns_403_in_demo_mode`（3993-3999行目）: demo mode時の403とメッセージを検証。
- `test_pdf_upload_succeeds_when_demo_mode_disabled`（4007-4016行目）: 正常系201とファイル存在のみ検証。レスポンス本文の内容（`str(saved)`形式）は未検証。`patch("tsundokensaku.web.get_books_dir", ...)`を使用（`get_books_dir`へのmonkeypatchであり、`save_uploaded_pdf`自体へのpatchではない）。
- `tests/playwright/workspace_add_pdf.spec.js`はアップロードAPIの返却本文を`pdf_path`として後続の`/pdf-outline`へ渡している。正常時の返却本文が後続処理で利用可能な保存パスであることを間接的に保証している。
- demo modeテストは`request=None`を渡しており、bodyへ触れれば失敗するため、「bodyを読む前に早期returnする」契約を既に強く保証している。追加の呼び出しカウンタテストは必須としない。

**PR1で新規追加する必須候補**（既存テストで保証されていない項目）:

- **service側**:
  - 通常PDFの保存位置と内容。
  - nested `relative_path`（例: `sub/dir/book.pdf`）での保存位置。
  - 同名衝突時に既存ファイルを上書きせず、正確に`sample (2).pdf`へ保存すること。
  - `../`traversalとBOOKS_DIR外の絶対パスを拒否すること。
  - symlink経由の外部書き込み拒否（中間ディレクトリsymlink・保存先ファイル自体のsymlinkの両方。`os.symlink`で一時ディレクトリ内に作成し、実行環境がsymlink作成をサポートしない場合はスキップする）。
  - 大文字`.PDF`を許可し、`.pdf`の後ろに空白がある名前を拒否すること。
  - 必要に応じ、Linux上でUnicodeと先頭・途中の空白を保持すること。
- **HTTP側**:
  - 有効な`relative_path`があっても、`filename`が空または空白だけなら400となり、serviceもrequest bodyも使用しないこと。
  - body空と非`%PDF`をそれぞれ400にすること。
  - 代表的なserviceの`ValueError`を、安定した既存文言を含む400へ変換すること。
  - 正常時201となり、返却本文が実際の保存先パスと一致すること。
  - demo mode時にbodyへ触れない契約は既存テストを維持すること（追加カウンタは必須としない）。

**実装と同時でよい**:

- BOOKS_DIR自動作成（既存動作の確認のみ）。
- 複数階層の親ディレクトリ作成。

**不要・過剰固定として除外**:

- `Path.resolve()`・`mkdir()`・`write_bytes()`の呼び出し回数。
- private helper名（`_unique_destination_path`等の内部実装詳細）。
- 一時変数。
- OS依存の`OSError`文言や、`FileExistsError`のパス文字列をHTTPレスポンス契約として完全固定すること。
- BOOKS_DIR自身を指す既知制約について、現状にない安全な拒否を期待するテスト。
- PDF内部構造の完全妥当性（`%PDF`マジックバイトの確認のみが現在契約であり、それ以上のPDF構造検証は行っていないため、それを新たに要求するテストは書かない）。
- 巨大PDF fixture。
- file descriptorやOS内部挙動。

### PR構成

**PR1: characterization test — 完了（PR #20、マージコミット `ecd0faaa48e2fab65bd3882d83f70e9a910ca8e7`）**

- 想定ブランチ: `test/r7-pdf-upload-storage-characterization`。
- 目的: `pdf_import_service.py`分離前に、現在のHTTP契約・保存位置・path境界・symlink境界・非上書き契約をテストで固定する。
- 変更予定ファイル: `tests/test_web.py`のみ。
- 変更しないファイル: `src/`配下すべて。
- 完了条件: 上記「必須候補」のテストが全て追加され、Python全件が成功する。
- 停止条件: symlink境界の現在挙動が安全に再現できない、またはtraversal拒否が本設計書の記述と一致しない場合は、実装を止めて設計を再検討する。
- レビュー観点: 現在挙動の記述のみを固定しているか（改善を混ぜていないか）、symlinkテストが一時ディレクトリ内で完結しているか（ユーザーの蔵書ディレクトリ・実PDFを使っていないか）。
- Pythonテスト: 新規テストを含め全件成功。
- Playwrightテスト: 変更なし（対象外、UI変更を伴わない）。

**PR2: 責務分離 — 完了（PR #21、マージコミット `6cee6830e4afd9fe22374b21a57c7c49d1de560d`）**

- 想定ブランチ: `refactor/extract-pdf-upload-storage`。
- 目的: `save_uploaded_pdf`を`pdf_import_service.py`へ移動し、`upload_pdf`ハンドラを薄くする。
- 変更予定ファイル: 新規`src/tsundokensaku/pdf_import_service.py`、`src/tsundokensaku/web.py`（`save_uploaded_pdf`本体の削除、`upload_pdf`内の呼び出しを`pdf_import_service.save_uploaded_pdf(...)`へ変更）、`tests/test_web.py`（該当テストの移動・import変更）、新規`tests/test_pdf_import_service.py`。
- 変更しないファイル: `src/tsundokensaku/paths.py`・`src/tsundokensaku/config.py`・その他の`web.py`内の責務（R7-2〜R7-4・R5・R8関連）。
- 完了条件: PR1の契約テストが（import元の変更を除き）内容を変えず全て通過する、Python全件成功、Playwright全件成功、`pdf_import_service.py`が`web.py`をimportしない（`python -c "import tsundokensaku.pdf_import_service; import tsundokensaku.web"`で確認）。
- 停止条件: 既存URL・HTTP契約・レスポンス本文・保存位置の変更が必要になる、`pdf_import_service.py`が`web.py`または`database`への依存を必要とする、のいずれかに該当する場合は実装を止め設計を再検討する。
- レビュー観点: `web.py`に残るのがHTTP入力・レスポンス生成・demo mode判定のみになっているか、境界検証・symlink拒否の挙動が変わっていないか、一意名生成規則が変わっていないか。
- Pythonテスト: 全件成功（移動後のimport切り替えを含む）。
- Playwrightテスト: 全件成功（UI変更を伴わないため無変更確認用途）。

### 非目標（今回行わないこと）

PDF内容の完全検証、ファイルサイズ上限、streaming upload、atomic write、同時upload競合の解消、自動インデックス作成、ウイルススキャン、demo mode全体のセキュリティ見直し、`POST /export-pdf/save`のdemo mode対応、PDFディレクトリ取り込み（R7-2）、Scrapbox取り込み（R7-3）、サムネイル・アウトライン・PDF変換の再設計（R7-4）、URL・HTTP status・レスポンス本文の変更、UI変更、R5・R8・D系列の変更。これらは将来の改善候補であり、必要になった段階で個別に検討する。

### 停止条件（実装着手時点で再確認すること）

- 現行の保存境界がコードから一意に読み取れない。
- symlinkの現在挙動を安全に再現できない。
- path検証とHTTP検証の境界が確定できない。
- `pdf_import_service.py`の所有範囲がR7-1（PDFアップロード保存）を超えて広がる。
- 実装にAPI変更（公開シグネチャ・HTTP契約の変更）が必要になる。
- 現在のレスポンス契約がテストから確認できない。
- 明確なセキュリティ脆弱性があり、単純移動で悪化する。

### R7-1全体の完了条件

- `pdf_import_service.py`が保存先解決・境界検証・衝突回避・書き込みを所有する。
- `web.py`がHTTP入力の受け取り・demo mode判定・レスポンス生成中心の薄い層になる。
- `pdf_import_service.py`が`web.py`をimportしない。
- 既存の公開URL・HTTPメソッド・status code・レスポンス本文・保存位置・既存ファイル非上書きが変更されていない。
- PR1で固定した契約が全て維持されている。
- Python全件成功。
- Playwright全件成功。
- 本設計書の記述と実装が一致している。
- ROADMAPではR7-1を完了、R7-2〜R7-4を未設計・未実装として要約し、R7親項目・`web.py`の責務分離親項目は未完了のまま維持する（R7-2〜R7-4・R5・R8が残るため）。

### 残るR7子責務

R7-2（PDFディレクトリ取り込み）は[R7-2専用文書](r7-2-pdf-directory-import.md)の詳細設計に独立レビューの指摘を反映済み・未実装、R7-3（Scrapbox JSON保存・同期）・R7-4（PDF閲覧・変換・本文検索のHTTPオーケストレーション）は未設計・未実装のまま。R7内の詳細設計はR7-2 → R7-3 → R7-4の順とし、各子責務の設計PRで責務境界と公開service APIを個別に決定する（§3「R7-2〜R7-4横断調査」参照）。
