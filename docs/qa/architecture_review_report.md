# アーキテクチャレビュー報告

- 実施日: 2026-09-16
- 判定: **FAIL — 仕様間・仕様実装間の重大な不整合が残る**
- 対象: [`architecture_overview.md`](docs/architecture/architecture_overview.md)、Tier 1〜3設計書、WIT、形式モデル、pysim参照実装、JITステンシルカタログ
- 方法: アーキテクチャレビュー規約に基づく4領域の並行監査と根拠箇所の統合確認
- 初回実行確認: pysim統合シナリオ12件中11件成功、1件失敗（Scenario 5、AR-14）

初回レビューでは指摘箇所の修正を行っていない。以下の追従状況を加えたが、初回の重大度判定と未解決指摘は再レビュー完了まで有効とする。

## 追従更新（2026-09-16）

初回レビュー後、次の事項を個別に実装・検証した。これはアーキテクチャ全体の最終判定ではなく、未解決指摘を含めた再レビューを継続中である。

| 対象 | 状態 | 根拠・確認 |
| :--- | :--- | :--- |
| AR-05：JIT Radix検索とPySimの差 | **検索方式は解消** | JITエントリ数が少ないためRadix表を廃止し、4スロットXORキャッシュと各バンクのソート配列二分探索へ仕様・コンセプト・PySim・テスト・ベンチマークを統一。Oldestヒットは即時昇格、Warmヒットは昇格なしへ統一。ネイティブの直接chainとPySimのRuntimeEngine再検索は境界実装の抽象度差として再確認する。 |
| JITキャッシュ物理配置 | **仕様・定数・テスト同期済み、再レビュー待ち** | 連続8KB（4KBページ×2）、共通コード2KB、Active/Warm/Oldest各2KBへ統一。関連定数テストと形式モデルを実行。 |
| AR-14：Scenario 5の復帰時assert | **初回失敗は再現せず** | 現行統合シナリオを12/12実行成功。失敗修正の因果確認は未了のため、初回結果は履歴に残す。 |

その他の初回レビュー指摘は、解消済みと推定せず、証拠確認と再監査の対象に残す。

## 重大な指摘

### AR-01 — JITチェイン境界のエピローグ契約が設計書間で逆

`jit_stencil_catalog.md` は、チェインの有無にかかわらずAAPCS準拠終了エピローグでスタックキャッシュをflushし、共有実行コンテキストを同期してからvariant互換性を判定し、必要ならsetup codeを挟む契約を定義する（[jit_stencil_catalog.md](docs/specs/jit_stencil_catalog.md)）。一方、`jit_compiler.md` は解決済みチェインでエピローグのFlush/POPを飛ばして後続トレース本体へ直接分岐すると記述する（[jit_compiler.md](docs/components/tier3_jit/jit_compiler.md)）。両者は同時に成立しない。

**追従結果:** AAPCS準拠開始プロローグ、末尾の共有スタック同期を伴うchain epilogue、variant不一致時のsetup code、およびAAPCS準拠終了エピローグの順序をステンシル仕様と概念コードへ反映した。コンパイラ仕様・テスト期待値の再監査は残す。

### AR-02 — 外部関数呼出し時のAAPCSスタック整列が崩れる

開始プロローグは7レジスタをpushし28バイト消費する（[jit_stencil_catalog.md](docs/specs/jit_stencil_catalog.md)）。その後、外部call stubは6レジスタをpushする（[jit_stencil_catalog.md](docs/specs/jit_stencil_catalog.md)）。AAPCS32では公開呼出し境界でSPを8バイト整列する必要がある（[AAPCS32](https://github.com/ARM-software/abi-aa/blob/main/aapcs32/aapcs32.rst)）。開始時に8バイト整列したSPは、28バイトpush後に4バイトずれ、さらに24バイトpushしてもずれが残ったまま`BL`される。

**追従結果:** 開始プロローグの保存量をAAPCS整列を維持する32バイトへ統一し、不要な`sub/add sp,#4`を除去した。外部call stubを含む実機命令列のABI検査は残件である。

### AR-03 — 関数呼出し記述子の物理配置がアーキテクチャ概要とTier 2仕様で矛盾

アーキテクチャ概要は関数呼出し記述子の12バイトヘッダとローカル値を同じ領域にインライン配置するとする（[architecture_overview.md](docs/architecture/architecture_overview.md)）。Tier 2仕様はメタデータをローカル値領域へ混在させず、独立した実行時記述子として管理すると明記する（[runtime_interpreter.md](docs/components/tier2_runtime/runtime_interpreter.md)）。参照実装も関数呼出し記述子領域とローカル値領域を別々に保持する（[interpreter.py](experiments/pysim/tier2_runtime/interpreter.py)）。

**修正案:** 実際の共有実行コンテキスト／Tier 2仕様を正本として、概要図と`CallFrame_Layout`辞書を更新するか、インライン配置へ設計を戻すなら実装・形式モデル・テストをまとめて変更する。

**追従結果:** 概要とTier 2仕様は、関数呼出し記述子をローカル値領域から分離する論理契約へ統一した。参照シミュレータも、記述子メタデータとローカル値領域を別々に保持している。制御ブロック復帰情報は、`wasm_interop.hxx`の`kind/start/match_end/stack_height/result_arity`および20バイト配置へ同期した。C++23製品ヘッダの物理サイズ・オフセットは、C++実装開始後に別途確定する。現行のpysim・仕様・形式モデルの範囲では、AR-03の論理的な配置矛盾は解消済みとする。

## 主要な指摘

### AR-04 — ハンドラの戻り値型が不一致

Tier 2仕様はインタープリタ命令ハンドラを`handler_result`戻り値、JIT入口を`void`とする（[runtime_interpreter.md](docs/components/tier2_runtime/runtime_interpreter.md)）。しかし、JITコンパイラ仕様の`opcode_handler_t`は命令ハンドラとJITトレース共通の呼出規約と説明しつつ、戻り値を`void`とする（[jit_compiler.md](docs/components/tier3_jit/jit_compiler.md)）。同じハンドラ型なのか、引数ABIだけ共通する別型なのか明確でない。

**修正案:** `interpreter_opcode_handler_t`、`jit_trace_entry_t`等の別名型に分け、各境界の戻り値・trap伝達・末尾呼出し条件を定義する。

### AR-05 — JIT検索アルゴリズムの設計とpysim参照実装が異なる（検索方式は解消）

当初の指摘では、仕様にJIT Radix索引があり、PySimは各bankの`bisect_left`による二分探索で異なっていた。設計判断を「JITエントリ数は少ないためRadix表を持たない」に統一し、仕様・コンセプト・PySim・テストをソート済み配列の二分探索へ揃えた。したがって検索方式の不一致は解消した。

**残件:** 昇格条件の仕様とPySimの差、およびネイティブ実装のトレース間chainとPySimのRuntimeEngine再検索の抽象度差は別指摘として残す。これらの状態遷移・最適化上の差を、参照モデルで同一に表すべき契約とターゲット固有最適化に分けて確認する。

昇格条件は、Oldest bankのlookup hitを追加hotness判定なしで即時Activeへ昇格し、Warm hitは昇格させないことで仕様・pysimを一致させた（[jit_runtime.md](docs/components/tier3_jit/jit_runtime.md)、[jit_cache.py](experiments/pysim/tier3_jit/jit_cache.py)）。

また、仕様はtrace headerから後続traceへ直接chainすると記述する一方、pysimはtrace呼出しが戻った後にRuntimeEngineが次PCをlookupする（[jit_compiler.md](docs/components/tier3_jit/jit_compiler.md)、[runtime_engine.py](experiments/pysim/tier2_runtime/runtime_engine.py)）。target設計と参照モデルのどちらを正とするか明記する。

### AR-06 — HOTからCOMPILEDへの遷移時点が不一致

`jit_runtime.md`は閾値到達時に`HOT`から`COMPILED`へ遷移してからコンパイル待ち列へ登録すると書く（[jit_runtime.md](docs/components/tier3_jit/jit_runtime.md)）。pysimはキュー登録時点では`HOT`を維持し、コンパイルとcache挿入の成功後にだけ`COMPILED`へ遷移する（[runtime_engine.py](experiments/pysim/tier2_runtime/runtime_engine.py)）。失敗時に再計測するという仕様判断とも、現行記述は合わない。

**追従結果:** 状態遷移を「閾値到達・キュー待ち・コンパイル成功／失敗」に分解した。成功時だけ`COMPILED`へ遷移し、失敗時は候補bitを恒久的に解除する。evictionまたは明示flushでは候補bitを維持して`UNEXECUTED`へ戻し、hotnessを再計測する。

### AR-07 — コンパイル失敗時の再計測方針が過去の合意と逆

レビューで確定した動作は、コンパイル失敗とキャッシュ追い出しを分離することである。コンパイル失敗は同じ結果を繰り返すため候補bitを恒久解除し、再履歴・再キュー・再コンパイルしない。一方、evictionまたは明示flushは候補bitを維持してカードを`UNEXECUTED`へ戻し、閾値までhotnessを再計測する（[jit_runtime_test_spec.md](docs/qa/tier3_jit/jit_runtime_test_spec.md)）。pysimもこの契約で実装している（[runtime_engine.py](experiments/pysim/tier2_runtime/runtime_engine.py)）。

**追従結果:** テスト仕様・pysim・コンセプトコードを上記の区別へ同期した。形式モデルはカード状態の安全性を検証し、コンパイル失敗の候補除外はランタイム契約としてテストで検証する。

### AR-08 — 「O(k)ローテーション」の主張がpysimの処理量と一致しない

仕様は被チェイン元k件のみを処理してO(k)でbankを再利用するとする（[jit_runtime.md](docs/components/tier3_jit/jit_runtime.md)）。pysimはローテーション時に`clear()`でbank内の全キー／値を走査し、退避PC一覧も生成する（[jit_cache.py](experiments/pysim/tier3_jit/jit_cache.py)）。実際の上限は少なくともO(n+k)であり、文書の有界性主張と異なる。

**修正案:** 全件走査をなくすデータ構造へ変更するか、走査対象nを含む計算量・停止時間上限を仕様とベンチマークへ明記する。

### AR-09 — schedulerへ戻ることを「餓死防止」と表現している

アーキテクチャ概要は連続handoff上限到達時にmain loopへ戻ることで餓死を防止すると主張する（[architecture_overview.md](docs/architecture/architecture_overview.md)）。しかしscheduler仕様は、協調型schedulerはタスクを強制プリエンプトせず、全タスク公平性・有界応答時間を保証しないと明記する（[os_scheduler.md](docs/components/tier1_core/os_scheduler.md)）。これは保証範囲を超えた説明である。

**修正案:** 「handoff連続回数を制限し、上限到達後にmain loopへ制御を戻す」と限定し、公平性・応答時間の保証は明示的に否定する。

### AR-10 — CSPのliveness証明が相手到達をモデル上必須にしている

IPC仕様は`AG(in_flight -> AF(not in_flight))`を有限解決性の証明として掲げ、相手タスクの有限到達を公正性仮定に挙げる（[ipc_router.md](docs/components/tier1_interface/ipc_router.md)）。形式モデルでは`awaiting_peer`から`receiver_holds`への遷移が必ず存在し、相手が到達しないまま待ち続ける遷移がない（[csp_handoff_model.py](docs/components/tier1_interface/formal/csp_handoff_model.py)）。したがって証明できているのはモデルに埋め込んだ到達前提の下での性質であり、CSP一般の公平性や有界応答ではない。

**修正案:** 非到達／無期限待機をモデルに含める。相手到達を環境仮定として置く場合は、証明対象をその仮定付きの条件付き性質として明記し、無条件の公平性・有界応答を主張しない。

**追従結果:** runtime memory形式モデルに`in_flight`自己ループを追加し、転送完了を無条件CTL性質から外した。CSPの相手到達・公平性・有界応答を共有メモリ層の保証として扱わない。

### AR-11 — 「unmap」による拒否という説明と実際の所有者検査が混在

概要は他タスク所有SHMを仮想アドレス空間からunmapすると説明する（[architecture_overview.md](docs/architecture/architecture_overview.md)）。pysimはPTEが存在する場合に`owner_id`と現在タスクを比較し、不一致なら`OWNER_MISMATCH` trapを返す（[vmmio.py](experiments/pysim/tier2_runtime/vmmio.py)）。拒否という安全性は成立しても、PTE不在によるunmap trapとは異なる機構である。

**修正案:** `unmap`が所有権移譲／Revoke後のPTE削除だけを指すのか、非所有者accessの拒否全般を指すのかを分け、trapとPTE状態を仕様に対応付ける。

### AR-12 — 共有メモリのRAII契約とWIT公開面の対応が未説明

概要はmove-only RAII `shared-block`を型で所有権移譲・回収すると説明する（[architecture_overview.md](docs/architecture/architecture_overview.md)）。WITは数値handleとbase address、size、ownerを含む`shm-handle` recordと`claim`／`release`操作を公開する（[memory.wit](docs/components/tier1_interface/wit/memory.wit)）。言語境界の薄いhandleをC++ RAII wrapperが包む設計なら両立するが、そのwrapperと所有権の責任者が文書化されていない。

**修正案:** WIT handleとC++ RAII resourceの対応、複製可否、release/revokeの責任、失敗時の所有権をインターフェース契約として明記する。

### AR-14 — UnifiedPC統合シナリオでJIT復帰時に実行時assertが発生

`uv run --system-certs --with wasmtime python [run_all.py](experiments/pysim/qa/scenarios/run_all.py)`の初回実行では、Scenario 5だけが失敗した。JIT trace後にRuntimeEngineがreturn sentinelを判定して次PCを読む経路で、`InterpreterCall.current_pc()`の`0 <= ip < len(frame.code)`が失敗する（[runtime_engine.py](experiments/pysim/tier2_runtime/runtime_engine.py)、[interpreter.py](experiments/pysim/tier2_runtime/interpreter.py)）。統合シナリオの期待はInterpreterとJITの同一結果および複数関数のUnifiedPCである（[scenario5_multimodule_unified_pc.py](experiments/pysim/qa/scenarios/scenario5_multimodule_unified_pc.py)）。追従確認では現行統合シナリオ12/12が成功したため、再現性のある不具合としては保留しない。

**追従結果:** return sentinel、callee復帰後のcaller continuation、`func_index`と`frame.code`の対応を含む統合シナリオを再実行し、現行経路で12/12成功した。再現性のある不具合としては保留しない。

## 文書品質・追跡性

### AR-13 — 正本の概要に空の根拠欄・空アンカーが残る

`architecture_overview.md`には空の`()`、`ADR-INTERP-03、`、空の`設計根拠:`が複数残る（例: [architecture_overview.md](docs/architecture/architecture_overview.md)）。設計判断の出典や理由が空欄で、レビュー・保守時に根拠を追えない。

**修正案:** 空欄を参照先と一義的な理由で埋める。未決定事項なら空括弧で残さず、未決定として明示しバックログへ登録する。

`JIT_RegisterMapping`、`GOTCHA-JITR-02`等を本文中で使う一方、文書構造規約が要求する区間単位のtraceabilityコメントが一部にない箇所もある。本文アンカーと規約上のtraceabilityコメントを分けて点検する（軽微）。

## 対象外・要確認

- 制御ブロック復帰情報は設計書が16バイトのターゲット表現を定義する一方、x64ホストシミュレータのABIヘッダは20バイトを`static_assert`する。ヘッダはx64ホストシミュレータABIを示すため、ターゲット配置との差が意図的かを確認するが、このレビューでは即時矛盾と断定しない。
- Concept `Channel`は送信側を実際にはsuspendせず単一in-flight slotへ置き、2件目をassertする。ただしコード自身がscheduler統合を範囲外と明記しているため、scheduler協調コードとの責務境界の追跡事項とする。
- architecture_overview.md:330-337のシーケンス図はサービスの応答結果が呼出元へ戻るように読める一方、ipc_router.wit:52, 55はroute-messageのipc-statusとreceive-messageを別APIにする。応答メッセージを別途ルーティングする設計なら、その経路を図で明示する。
- 4KBページ粒度とSHM予算の差は矛盾として扱わない。仮想アドレス／ページ粒度と物理確保予算は別の制約である。
- WIT handleをRAII wrapperが包む可能性など、境界実装を未確認の点は確定欠陥ではなく、契約を明記すべき未解決事項として記録した。

## 推奨する修正順

1. AR-01とAR-02を先に解消し、JIT call/chain ABIとレジスタ・スタック境界を固定する。
2. AR-03、AR-04、AR-14で共有コンテキスト、call frame、ハンドラABI、callee復帰後のcontinuationを正本間で統一する。
3. AR-06〜AR-10でJIT・CSPの状態遷移、計算量、保証範囲を修正し、形式モデルとpysimを同じ主張に揃える。
4. AR-05、AR-11、AR-12で検索・memory/IPC境界の実装差と契約を整理する。
5. AR-13とtraceability不足を直し、アーキテクチャレビューを再実行する。
