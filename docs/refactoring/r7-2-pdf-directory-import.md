# R7-2: PDFディレクトリ取り込みの切り出し — 詳細設計の独立レビュー指摘反映済み・未実装

[R7全体設計](../central-file-refactoring-inventory.md) / [ROADMAP](../../ROADMAP.md)

状態: 詳細設計済み・未実装。以下はPR #23で確定した既存設計を移したものであり、内容は変更していない。

本項は、PR #22で記録した横断調査と、`develop`のコミット`b3d6d73072d9bc725df92a5b7cf398e1f8a64450`を基準にしたR7-2単独の再調査に基づく詳細設計である。横断調査で「候補・未確定」とした分離先、公開service API、例外方針、symlink境界を本項で確定し、独立レビューの指摘を反映した。characterization test追加と責務分離の実装はまだ行っていない。R7-1の確定済み契約は変更せず、R7-3・R7-4の設計にも踏み込まない。

### 目的

`web.py`の`import_pdfs_from_directory`に混在するsource検証、PDF列挙、destination決定、ディレクトリ作成、コピー、衝突処理、件数集計を`pdf_import_service.py`へ移し、`import_pdf_directory`をHTTPオーケストレーションだけへ縮小する。同時に、現行のsource PDF symlinkによる境界外読取り、BOOKS_DIR内の中間symlinkによる境界逸脱、内部パス・OSエラーの画面露出を、維持対象ではない既知リスクとして修正する。

今回の主目的は責務分離である。公開HTTP契約と安全なファイル配置契約を明示しつつ、ファイル形式の拡張、HTTP method変更、自動インデックス、処理量制限等の別のプロダクト変更は混在させない。

### 現在の責務と処理フロー

- HTTP入口: `GET /settings/pdf-import`（`import_pdf_directory(source_dir: str = "")`）。設定画面のGET formがquery parameter `source_dir`を送る。
- `web.py`のハンドラがdemo mode、空入力、BOOKS_DIR取得、service相当処理の呼出し、成功・失敗message、`/settings`への303 redirectを所有する。
- 同じ`web.py`の`import_pdfs_from_directory(source_dir, books_dir)`が両パスを`expanduser().resolve()`し、sourceの存在・directory種別・source/BOOKS_DIRの同一または包含関係を検査する。
- `sorted(source_root.rglob("*.pdf"))`から`is_file()`の候補を集め、sourceからの相対階層をBOOKS_DIRへ維持する。既存destinationは内容比較なしでskipし、それ以外を`shutil.copy2`でコピーする。
- 戻り値は位置依存tuple`(copied, skipped, total)`。コピー途中の例外はfail-fastで呼出し元へ伝播し、それ以前のコピーは残る。
- HTTP層は`Exception`を包括catchし、`str(exc)`を失敗messageへ連結する。R7-2はDB・indexerを呼ばず、取り込み後のインデックス更新は別操作である。

### 維持する公開HTTP契約

今回の責務分離では次を維持する。

- URL・method: `GET /settings/pdf-import`。
- 入力: query parameter `source_dir`（未指定時のデフォルトは空文字）。
- demo mode: serviceを呼ばずファイル副作用を起こさず、`デモモードのため無効です`をmessageに持つ`/settings`への303 redirect。
- source未指定または空白のみ: `PDF の取り込み元フォルダを指定してください`をmessageに持つ`/settings`への303 redirect。
- 成功時: `/settings`への303 redirect。現行どおり`copied` → BOOKS_DIR → `skipped` → `total` → 入力sourceの順序で、`PDF を {copied} 件 {books_dir} にインポートしました / スキップ {skipped} 件 ({total} 件中, {source})`というmessageを表示する。
- service失敗時: statusを直接4xx/5xxへ変更せず、`PDFインポートに失敗しました:`という接頭辞を持つ安全な利用者向けmessageで`/settings`へ303 redirectする。ただし後述のとおり、内部絶対パスとOSエラー全文は表示しない。
- 取り込みとインデックス更新は別操作のままとする。

GETによる更新操作は望ましい設計とはしないが、POSTへの変更は公開HTTP契約と画面動作を変えるため本責務分離の非目標とし、後続課題として扱う。

### 意図的に変更する危険な現行挙動

次は互換性維持の対象ではなく、本設計の実装で安全側へ変更する。

- source内のPDF symlinkを通常ファイルとして追跡・コピーし得る挙動を廃止する。symlinkのPDF候補は列挙対象外とし、その指す内容を取り込まない。symlink directoryも探索しない。
- 入力されたsource root自体がsymlinkでも探索を開始し得る挙動を廃止する。通常のsource root symlinkとbroken source root symlinkを、解決前に境界違反として拒否する。
- destinationまたはその中間要素に、処理前からsymlinkまたはbroken symlinkが存在しても書込み得る挙動を廃止する。コピー前にそれらを検出して拒否し、並行するpath差替えがない条件下で解決後のdestinationがBOOKS_DIR境界外なら処理を拒否する。
- `str(exc)`をそのままredirect messageへ含める挙動を廃止する。利用者には分類済みの安全な文言を返し、詳細は既存の`LOGGER`へ記録する。

これらを「現在挙動を維持するcharacterization test」として固定しない。テストでは検査時点ですでに構成されたsymlinkとbroken symlinkの拒否、および並行するpath差替えがない条件下でのdestination境界検査を保証する。検査後のpathまたはsymlink差替えによるTOCTOUまで保証するものではない。

### 分離先・責務境界・依存方向

分離先はR7-1と同じ`src/tsundokensaku/pdf_import_service.py`に確定する。ただし、R7-1とR7-2は変更理由の近い「PDF取り込み」として同居するだけであり、入力・件数・衝突・部分成功の契約が異なるため、一つの公開関数へ統合しない。

依存方向は次とする。

```text
web -> pdf_import_service -> pathlib / filesystem
```

R7-1の`save_uploaded_pdf`は既存どおり必要な範囲で`paths`を利用する。R7-2固有のsymlink・destination境界処理は、まず`pdf_import_service.py`内部に置く。複数serviceで同じ安全処理を必要とする事実が確認できた場合にだけ、将来`paths.py`等への共通化を検討する。

`pdf_import_service`からFastAPI、`Request`、`RedirectResponse`、`HTTPException`、`web.py`、template、DB、indexer、config、Web message生成へは依存しない。どのBOOKS_DIRを使うかはHTTP/application側が`get_books_dir()`で決め、serviceへ明示的に渡す。

**`web.py`に残す責務**:

- route、GET、query parameter `source_dir`の受付。
- demo mode判定と空入力のHTTPレベル検査。
- `get_books_dir()`による設定取得。
- service呼出し。
- 結果から成功messageを組み立てる処理。
- service例外の分類、既存の`LOGGER`への記録、安全な失敗messageへの変換。
- `/settings`への303 `RedirectResponse`生成。

**`pdf_import_service.py`へ移す責務**:

- sourceとbooks_dirの`Path`化、`expanduser()`、絶対・実体パス解決。
- sourceの存在・directory種別・source/books_dir関係の検証。
- 対象PDFの再帰列挙と決定的な並び順。
- sourceからの相対パスとdestinationの決定。
- BOOKS_DIRとdestination親directoryの作成。
- source/destinationのsymlink・境界検査。
- 既存の通常ファイルdestinationのskip、その他の衝突分類、コピー、件数集計。
- filesystem例外からservice例外への変換。

### service公開APIと結果モデル

R7-2専用の公開関数は次で確定する。`web.import_pdfs_from_directory`という内部Python import pathに互換wrapperは残さず、既存の直接単体テストをservice側へ移す。現行コード・テストにこの関数へのmonkeypatchはないため、テスト都合だけのwrapperは設けない。

```python
def import_pdfs_from_directory(
    source_dir: Path,
    books_dir: Path,
) -> PdfDirectoryImportResult:
    ...
```

- `source_dir`: サーバー上の入力directory。service内部で`Path`化、`expanduser()`、実体パス解決を行う。
- `books_dir`: HTTP/application層が設定から取得して渡す保存先。service内部で同じく解決する。service自身はconfigを参照しない。
- 戻り値: immutableな`@dataclass(frozen=True)`である`PdfDirectoryImportResult`。本プロジェクトの値オブジェクトが`@dataclass(frozen=True)`を一貫して利用しているため、位置依存tupleではなく同じ表現を採用する。

```python
@dataclass(frozen=True)
class PdfDirectoryImportResult:
    copied: int
    skipped: int
    total: int

    def __post_init__(self) -> None:
        # 3値が整数か、非負か、合計が一致するかを検証する。
        ...
```

`copied`、`skipped`、`total`は非負整数とし、正常な結果では常に`total == copied + skipped`を満たす。`@dataclass(frozen=True)`の`__post_init__`で、3値が整数であること、負数でないこと、合計が一致することを検証し、不正な結果オブジェクトの生成を拒否する。`bool`の扱いなど、今回の実装に不要な型体系上の細部は本設計では固定しない。

`total`は取り込み対象となった小文字`.pdf`の通常ファイル数で、既存の通常ファイルdestinationによりskipした件数も含む。symlinkや大文字`.PDF`等、列挙対象外のものは含めない。通常の成功結果へファイル一覧、source、destination、内部絶対パスは含めない。失敗時は結果オブジェクトを返さずservice例外を送出し、先行ファイルのコピー後に失敗した場合も不完全な結果オブジェクトは返さない。

### 列挙・ファイル名・destination衝突

- source以下を再帰探索し、sourceからの相対directory構造をBOOKS_DIR配下へ維持する。
- 小文字`.pdf`だけを対象とし、大文字`.PDF`は対象外とする。
- Unicodeや空白を含むファイル名を許可し、独自のUnicode正規化、strip、renameを行わない。
- 列挙順は相対パスに基づく決定的な順序とする。`rglob()`等の具体的APIや内部呼出し順は公開契約にしない。
- 既存destinationが通常ファイルの場合は、内容・hashを比較せず、上書きせずにskipする。R7-1のような`" (2)"`による一意名生成は行わない。directoryやsymlinkを「既存destination」としてskipする契約ではない。
- `total`には既存destinationを含め、`copied + skipped == total`は正常完了時に成立する。

destinationごとの判定順は、(1) destinationおよびbooks rootからdestinationまでの既存の中間要素にsymlink／broken symlinkがないか検査、(2) `resolve(strict=False)`相当で正規化したdestinationが解決済みbooks root自身または配下か検査、(3) 通常の既存path衝突を判定、(4) 問題がなければ親directoryを作成してコピー、の順に確定する。

| destinationの状態 | 処理 |
|---|---|
| 不存在 | 境界検査後に親directoryを作成してコピー |
| 通常ファイル | 既存destinationとしてskip |
| directory | 期待するファイル型と一致しないため`PdfDirectoryFilesystemError` |
| 境界内を指すsymlink | `PdfDirectoryBoundaryError`で拒否 |
| 境界外を指すsymlink | `PdfDirectoryBoundaryError`で拒否 |
| broken symlink | `PdfDirectoryBoundaryError`で拒否 |

大文字`.PDF`対応、PDF内容検証、内容hashによる衝突判定、同名ファイルのrenameは責務移動ではなくプロダクト挙動変更となるため非目標とする。

### source・books_dir・destinationの安全境界

sourceは、`resolve()`によってsymlinkだった事実を失う前に、入力rootそのものを`is_symlink()`または`lstat()`相当で検査する。通常のsource root symlinkとbroken source root symlinkはいずれも`PdfDirectoryBoundaryError`として拒否する。その後、symlinkではないsource rootを`expanduser()`して実体パスへ解決する。通常のsourceが存在しなければ`PdfDirectorySourceNotFoundError`、通常ファイルなら`PdfDirectorySourceNotDirectoryError`とする。存在・種別の検査中に権限不足等の`OSError`が発生した場合はsource不存在に誤分類せず、`PdfDirectoryFilesystemError`とする。

books_dirはsourceと意図的に非対称な契約とする。books_dir自身を含む既存path要素は、実体パス解決前にsymlink／broken symlinkか検査する。通常のsymlinkは許可して参照先をたどり、最終的な実体パスを境界rootとして固定する一方、broken symlinkは`PdfDirectoryBoundaryError`として拒否する。books_dirが存在しない通常pathなら、この既存要素の検査とは区別して`resolve(strict=False)`相当で将来の実体パスを正規化し、作成時の`OSError`は`PdfDirectoryFilesystemError`へ変換する。source_dirは利用者が入力する探索開始地点なので入力境界を明確にするためroot symlinkを拒否する一方、books_dirはアプリケーション設定による保存先なので通常運用上のsymlink配置を許容し、その参照先を境界rootに固定する。

解決後のsourceとbooks rootについて、次の関係を満たさなければ処理を開始しない。

- sourceとbooks rootが同一でない。
- sourceがbooks root配下でない。
- books rootがsource配下でない。

source探索ではsymlink directoryを再帰しない。`.pdf`という名前でもsymlinkである候補は対象外とし、通常ファイルだけを扱う。これによりsource外を指すPDF symlinkだけでなくsource内を指すPDF symlinkも一律に取り込まない。source root自体のsymlink拒否、source配下のsymlink directoryを探索しないこと、source配下のPDF symlinkを対象外とすることは、それぞれ別の契約である。

各destinationは前節の判定順に従う。検査時点で存在するdestinationおよび中間要素がsymlinkなら、参照先が境界内か境界外かを問わず`PdfDirectoryBoundaryError`とし、broken symlinkも同じく拒否する。そのうえで、正規化したdestinationが解決済みbooks root自身または配下でない場合も`PdfDirectoryBoundaryError`とする。

この保証は、検査時点ですでに存在するsymlinkおよびbroken symlinkを検出し、並行するpathまたはsymlinkの差替えがない条件下で境界外destinationを拒否する範囲に限る。検査後のpath差替えによるTOCTOUは今回の保証対象外である。atomic copy、`openat`、`O_NOFOLLOW`等による完全な競合耐性は導入せず、後続の安全性改善課題とする。

### 衝突・fail-fast・部分成功

既存destinationが通常ファイルの場合だけ、上書きせずskipする。destinationがdirectoryの場合はfilesystem失敗とし、symlink／broken symlinkの場合は境界違反とする。複数ファイルの処理中にdirectory作成、読取り、copy等が失敗した場合はfail-fastとし、後続ファイルを処理しない。directory全体のstaging、全体rollback、ファイル集合をまたぐtransactionは導入せず、失敗前にcopyが完了したファイルは残す。

この部分成功は、現在の重要な内部挙動であり、移動前のcharacterization testで残存状態を確認する。一方、利用者向けの永続的な公開契約として積極的に保証するものではなく、将来のstagingや一括rollbackを妨げない。失敗結果には不完全な件数オブジェクトを返さず、service例外を送出する。

各ファイルを同じdestination directory内の一時ファイルへcopyし、完了後に確定するatomic copyは、個々の壊れたdestinationを防ぐ改善候補である。しかし、既存destination非上書きと並行実行時の排他的な確定、metadata保持、一時ファイルcleanupまで同時に設計する必要があり、現在の単純移動に対して変更量が大きい。したがってR7-2責務分離の必須条件にはせず、後続の安全性改善とする。実装では現在と同じ直接copyを用い、copy失敗時に中途ファイルが残り得ること、`exists()`とcopyの間のTOCTOUで競合時の上書きを完全には防げないことを既知リスクとして残す。

### service例外とログ

生の`ValueError`や`OSError`をそのまま利用する案はクラス数が少ない一方、source/books関係の不正と安全境界違反を型で区別できず、`OSError`の詳細を安全なHTTP messageへ変換しにくい。R7-2固有例外へ変換する案は、web層が原因を安全に分類でき、`raise ... from exc`でログ用の原因も保持できる。本設計では後者を採用する。

例外クラスは必要な失敗分類に限定し、`PdfDirectoryImportError`を基底として次の5サブクラスを`pdf_import_service.py`に定義する。

- `PdfDirectorySourceNotFoundError`: sourceが存在しない。
- `PdfDirectorySourceNotDirectoryError`: sourceがdirectoryではない。
- `PdfDirectoryOverlapError`: sourceとbooks_dirが同一または包含関係にある。
- `PdfDirectoryBoundaryError`: source root symlink、broken books_dir symlink、destinationまたは中間要素のsymlink／broken symlink、destinationの安全境界に違反する。
- `PdfDirectoryFilesystemError`: 権限不足、resolve、列挙、mkdir、読取り、copy等のfilesystem操作が`OSError`で失敗する。元の`OSError`を`__cause__`に保持する。

service層の例外変換は原則としてfilesystem操作ごとに`OSError`だけを捕捉し、`PdfDirectoryFilesystemError`を`raise ... from exc`で送出する。`except Exception`による包括変換は行わず、すでに生成したR7-2専用例外も再変換しない。通常のsource不存在と通常ファイル指定は上記の専用例外へ分類するが、`PermissionError`をsource不存在として扱わない。symlink／broken symlinkの検出は`PdfDirectoryBoundaryError`、権限不足、列挙失敗、mkdir失敗、copy失敗等は`PdfDirectoryFilesystemError`とする。プログラミングエラーは`PdfDirectoryFilesystemError`へ隠さず、そのままweb層へ伝播させる。service層ではログを出力しない。

serviceはFastAPI例外を送出しない。安定した利用者向け文言を例外の`str()`へ埋め込んでHTTPへ透過させるのではなく、web層が例外型を次のような安全な分類messageへ変換する。

- source不存在: `PDFインポートに失敗しました: 入力元フォルダが見つかりません`
- 非directory: `PDFインポートに失敗しました: 入力元がフォルダではありません`
- source/books重複: `PDFインポートに失敗しました: 入力元と保存先には重ならないフォルダを指定してください`
- 安全境界違反: `PDFインポートに失敗しました: 安全でないパスが含まれているため取り込めません`
- filesystem失敗: `PDFインポートに失敗しました: ファイルの読み取りまたはコピーに失敗しました`
- 想定外の例外: `PDFインポートに失敗しました: 予期しないエラーが発生しました`

既存の接頭辞と303 redirectは維持し、内部絶対パス、入力sourceの実体パス、OSエラー全文は失敗messageへ含めない。web層は既存module loggerの`LOGGER.exception(...)`を利用し、service例外と想定外の例外のいずれも一度だけ記録して、例外型、原因、内部パス、stack traceをログへ残す。新しいlogging基盤は導入しない。想定外の例外も安全なmessageへ変換して現在のredirect挙動を維持するが、握り潰さずstack traceを記録する。

### DB・indexerとの境界

R7-2 serviceはDB接続、schema初期化、book/page登録、FTS更新、indexer呼出しを行わない。取り込み後のインデックス更新は現状どおり設定画面上の別操作であり、R7-2内に「copy成功後にDB登録が失敗する」transactionは存在しない。後続の別操作でindexerが失敗しても、取り込み済みファイルは残る。

自動インデックス更新は利用者向けworkflowと失敗時の整合性境界を変えるため非目標とする。

### characterization test計画

テスト実装時は、移動前に現在の公開契約・維持対象を固定するテストと、安全性変更を保証するテストを区別する。危険な現行挙動を「維持すべき現在契約」として固定しない。具体的なtest名は実装時に既存命名規則へ合わせるが、保証内容は次とする。

**HTTP契約（`tests/test_web.py`）**:

- 正常時に303で`/settings`へredirectし、成功messageの全要素が`copied` → BOOKS_DIR → `skipped` → `total` → 入力sourceという現行順序で反映される。
- source未指定時に303で既存の入力不備messageを表示し、serviceを呼ばない。
- 各service失敗を303と安全な分類messageへ変換し、内部パス・OSエラー全文を含めない。
- service失敗と想定外例外の双方について、web層だけが既存の`LOGGER.exception(...)`でstack traceを一度だけ記録する。
- demo modeではserviceを呼ばず、ファイル副作用がない。既存の直接handler testに加え、必要ならroute経由でも確認する。

**service単体（`tests/test_pdf_import_service.py`）**:

- nested directory構造を維持する。
- destinationが通常ファイルなら上書きせずskipし、`total`にskip対象を含める。
- destinationがdirectoryなら`PdfDirectoryFilesystemError`になる。
- source不存在と非directoryを別のservice例外で表す。
- source root symlinkとbroken source root symlinkをそれぞれ`PdfDirectoryBoundaryError`で拒否する。
- 通常のbooks_dir symlinkでは参照先を境界rootとし、broken books_dir symlinkは`PdfDirectoryBoundaryError`で拒否する。
- sourceとbooks_dirが同一、sourceがbooks_dir配下、books_dirがsource配下の3関係を拒否する。
- 空directoryでは`copied=0`、`skipped=0`、`total=0`となる。
- 小文字`.pdf`だけを対象とし、大文字`.PDF`は対象外となる。
- Unicode・空白を含む相対パスとfilenameを変更しない。
- source外を指すPDF symlinkを対象外とし、その内容を読まない。source内を指すPDF symlinkとsymlink directoryも同じ規則で探索対象外とする。
- 事前に構成されたdestination symlinkは境界内・境界外の参照先をそれぞれ試していずれも拒否し、事前に構成されたdestinationのbroken symlinkも拒否する。
- 事前に構成された中間symlinkとbroken symlinkを拒否する。このテストは並行するsymlink差替えまで保証するものではない。
- mkdir、列挙、copy等、filesystem操作の`OSError`を`PdfDirectoryFilesystemError`へ変換し、元例外を原因として保持する。
- プログラミングエラーを`PdfDirectoryFilesystemError`へ変換しない。
- 決定的な相対パス順で処理する。
- 結果型が負数を拒否し、`total != copied + skipped`も拒否する。
- 部分成功後に後続ファイルで失敗した場合、後続を処理せず、失敗前に完了したファイルは残るが、結果を返さず例外になる。再現にはOSや実行ユーザーに依存する権限変更を主に使わず、決定的な列挙順で先行ファイルをコピーした後、後続ファイルのdestination親に通常ファイルを置くなどの安定した衝突を用いる。具体的なtest codeやmockの呼出し回数は固定しない。

symlink安全性と安全なHTTPエラーは現行の危険な挙動を変更する回帰テストであり、現在挙動のcharacterizationとしては扱わない。実装PRでは安全性変更を先に失敗するテストとして示し、service実装と同じPR内で成功させる。

**固定しない危険な挙動・内部詳細**:

- source外symlinkの内容を取り込めること。
- 事前に構成されたsymlinkを介した境界逸脱を許容すること。
- 内部絶対パスやOSエラー全文を画面へ出すこと。
- 並行するpathまたはsymlink差替えに対する完全な競合耐性。
- 処理量上限がないこと。
- `rglob()`、`shutil.copy2()`、`Path.resolve()`等の具体的呼出し回数やprivate helper名。

### 実装PR構成

characterization testと責務分離・安全性変更をレビュー可能な単位に分ける。

1. **characterization test**: HTTP redirect、件数、nested構造、既存の通常ファイルdestinationのskip、大小文字、Unicode、source/books関係、空directory、決定的順序、fail-fastと部分成功等、現在の維持対象・要観測挙動を追加する。危険なsymlink escapeと内部エラー露出を維持契約として固定しない。
2. **責務分離と安全性変更**: `PdfDirectoryImportResult`・service例外・`import_pdfs_from_directory`を`pdf_import_service.py`へ実装し、`web.py`のhelper本体を除去してservice呼出し、logging、安全なmessage変換へ置き換える。同じPR内でsymlink境界と安全なエラー表示の回帰テストを追加する。

実装PRを1本にするか2本に分けるかは着手時の差分量で決められるが、コミットは上記2目的を分け、危険な現行挙動をcharacterization commitに含めない。

### 非目標（今回行わないこと）

- `GET /settings/pdf-import`のPOST化、URL、query parameter、303 redirect、設定画面UIの変更。
- 大文字`.PDF`対応、PDF内容・magic bytes検証、Unicode正規化、filename変更。
- 内容hash比較、既存destinationのrename・更新・上書き。
- directory全体のtransaction、staging、一括rollback。
- atomic copy、`openat`/`O_NOFOLLOW`等によるTOCTOU完全対策、並行実行制御。
- ファイル数・総容量・処理時間の上限値導入、非同期job化。同期HTTP処理で上限なく列挙・コピーする現状は既知リスクとして残し、根拠ある上限を別途設計する。
- 自動インデックス、DB・FTS更新。
- R7-1のAPI・衝突・保存境界の再設計。
- R7-3、R7-4、R5、R8、database.py系列の設計・実装。
- `paths.py`へのR7-2専用処理の先行共通化。
- 新しいlogging基盤。

### 実装着手時の停止条件

- 公開HTTP契約の維持に、URL・method・query parameter・303 redirectの変更が必要になる。
- serviceがFastAPI、`web.py`、DB、indexer、configへ依存しなければ実装できない。
- 既存destination非上書きと新しいdestination境界検査を両立できない。
- 一時directory内でsymlink境界テストを安全・決定的に再現できない。
- 安全な利用者向けmessageと原因を保持したログを分離できない。
- R7-1の公開APIまたは確定済み契約の変更が必要になる。

該当時は実装範囲を拡張せず、本設計を再検討する。

### R7-2実装完了条件

- `import_pdfs_from_directory`が`pdf_import_service.py`にあり、`web.py`からfilesystem列挙・mkdir・copy・path境界処理が除かれている。
- `web.py`にはroute、demo mode、HTTP入力、BOOKS_DIR取得、service呼出し、logging、message、303 redirectだけが残る。
- `PdfDirectoryImportResult`が非負整数の`copied`、`skipped`、`total`を名前付きで返し、`__post_init__`で`total == copied + skipped`を保証する。失敗時と部分成功後の失敗時には結果を返さない。
- service例外が5分類を区別し、FastAPI例外を使用しない。filesystem操作ごとに`OSError`だけを変換して原因を保持し、R7-2専用例外を再変換せず、プログラミングエラーをfilesystem失敗として隠さない。
- serviceがDB、indexer、config、template、Web message生成へ依存しない。
- URL、GET、`source_dir`、303 redirect、demo mode、成功messageの全要素と現行順序、通常ファイルdestinationの非上書き、directory構造、小文字`.pdf`対象が維持される。
- source/books_dirの同一・両方向の包含関係が拒否される。
- source root symlinkとbroken source root symlinkを拒否し、source配下のsymlink directoryを探索せず、PDF symlinkを対象外とする。通常のbooks_dir symlinkは参照先を境界rootとし、broken books_dir symlinkを拒否する。
- 検査時点で存在するdestinationと中間要素のsymlink／broken symlinkを拒否し、並行するpath差替えがない条件下で境界外destinationを拒否する。検査後の差替えによるTOCTOUは保証対象外として後続課題に残す。
- destinationの通常ファイル、directory、境界内symlink、境界外symlink、broken symlinkが設計どおりに分類される。
- 内部絶対パスとOSエラー全文を利用者向け失敗messageへ含めず、service層ではログを出さず、web層が詳細とstack traceを`LOGGER.exception(...)`で一度だけ記録する。
- fail-fastとrollbackなしの部分成功が移動前後で確認される。
- 予定したHTTP契約テスト、service単体テスト、既存Python・Playwrightテストが成功する。
- 本設計書、実装、テストが一致する。ROADMAP更新は設計レビュー・実装の進捗に応じて別途判断する。

### R7-1との関係と残るR7子責務

R7-1とR7-2は「外部からPDFをBOOKS_DIRへ持ち込む」という変更理由と、`web -> pdf_import_service -> pathlib/filesystem`の依存方向を共有するため同じmoduleに置く。一方、R7-1の`save_uploaded_pdf`は単一bytes、大小文字を区別しない`.pdf`、一意名生成、保存先`Path`という契約を持ち、R7-2はdirectory、複数の小文字`.pdf`、既存の通常ファイルdestinationのskip、件数結果、部分成功という別契約を持つ。公開関数、結果、例外処理を統合せず、R7-1を再設計しない。

R7-2は本詳細設計の独立レビュー指摘反映済み・未実装である。R7-3（Scrapbox JSON保存・同期）・R7-4（PDF閲覧・変換・本文検索のHTTPオーケストレーション）は未設計・未実装のままで、R7-2のmodule・API・例外判断を自動的に適用しない。R7親項目も未完了のままとする。
