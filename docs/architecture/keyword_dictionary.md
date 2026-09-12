# Fireball キーワード台帳 (Keyword Dictionary & Registry)

この文書は、Fireball プロジェクトにおける**全仕様・アーキテクチャ・コンポーネント・リンクキーワード（全 215 件）の正本台帳**である。

章節項番号（`§3.3` 等）や見出し文字列、ファイルパスによる直接参照は、仕様改訂やリファクタリングに伴う見出し変更・章番号ズレによって容易に陳腐化・リンク切れを起こす。これを防ぐため、Fireball では中括弧で囲まれた一意なキーワード（`{...}`）をアンカーとして定義し、すべての設計書・テスト仕様書・結合テスト・形式検証モデルを機械的に相互リンクする。

---

## 1. キーワード運用ルールと分類基準

### 1.1 5大分類体系

本リポジトリに存在するすべてのキーワードは、その責務と検証スコープに応じて以下の 5 つのカテゴリに明確に分類・整理される。

| 分類カテゴリ | 命名規則・識別子 | 責務とスコープ | 主な定義元正本 |
| :--- | :--- | :--- | :--- |
| **META** | `{META_*}` | システム横断的な非機能要求、共通設計思想、アーキテクチャ哲学。 | `document_structure.md` |
| **GLOBAL** | `{GLOBAL_*}` | システム全体（多数の仕様書）にまたがって適用される広域ポリシー、プラットフォーム要件。 | `document_structure.md`, `requirement_list.md` |
| **ARCHITECTURE** | `{ADR_*}`, `{Challenge_*}`, 原則名 | 全体アーキテクチャ決定（ADR）、設計上の挑戦課題・制約（Challenge）、およびクリーンアーキテクチャ等の共通設計原則。 | `requirement_list.md`, `architecture_overview.md` |
| **COMPONENT & GOTCHA** | 各コンポーネント名, `{GOTCHA-<COMPONENT>-<連番>}` | 各コンポーネント（Tier 1〜3）の具体的な機能・インターフェース仕様要求、および実装・テスト上の勘所・落とし穴（GOTCHA）。 | `requirement_list.md`, 各コンポーネント設計書 |
| **LINK** | `{*_Layout}`, `{*_FastCall}`, 機構名 | コンポーネント間・ドキュメント間の物理メモリレイアウト整合、低層ディスパッチ規約、内部バイパス・状態連携のための専用リンクアンカー。 | `keyword_dictionary.md`, 各コンポーネント設計書 |

### 1.2 アンカー運用とトレーサビリティ検証ルール

1. **章節項番号依存の禁止**: ドキュメント間の参照において「§3.3を参照」「第4章を参照」といった章番号依存の記述を禁止し、キーワードアンカーを用いて紐付ける。
2. **台帳一元管理と一意性の保証**: すべてのキーワードは本台帳に登録され、一意な定義元と仕様概要が保証される。
3. **spec-integrator による完全検証**: [`check-doc.ps1`](tools/check-doc.ps1)（`spec-integrator` パイプライン）が全 Markdown 文書をパースし、`DocGraph` による静的リンク検証、トレーサビリティ検証、Tier 階層違反（逆流）検証を自動実行する。

### 1.3 キーワードとテストケースIDの採番規則

- **キーワードは意味を持つ名前にする**: `{Keyword}` は要求・設計判断・コンポーネント仕様・設計の勘所を表す意味的なアンカーであり、単なる連番を割り当てない。「接頭辞＋数字」だけのキーワードは新設禁止とする。
- **GOTCHA の形式**: `GOTCHA-<COMPONENT>-<連番>` は、実装の勘所・不変条件を表す固有IDとして使用してよい。ただし、台帳に設計理由を記載し、対応するテスト仕様書のテストケースIDと紐づけること。これは意味を持つ GOTCHA 分類のIDであり、汎用的な連番キーワードではない。
- **純粋なリンクアンカーだけは `LINK-<連番>`**: 要求や設計内容を表さず、機械的な文書間リンクのためだけに必要なアンカーは `LINK-<連番>` とする。物理レイアウトやレジスタ規約など、それ自体に意味がある連携契約は、既存の `{ExecutionContext_Layout}` のような意味名を使用する。
- **テストケースIDはキーワードと別物**: `TEST-<コンポーネント>-<テスト番号>`（例: `TEST-INT-01`、`TEST-MEM-14`、`TEST-JITR-26`）をテスト仕様書のテストケースIDに使用し、`{...}` で囲んだキーワードとして扱わない。台帳・仕様書では列名を「対応テストケースID（キーワードではない）」とし、キーワードとの紐づけを示す場合は、意味キーワードとテストケースIDを別欄・別表で記載する。
- **ベンチマークIDはテストケースIDと別物**: `BENCHMARK-<コンポーネント>-<ID>`（例: `BENCHMARK-MEM-01`、`BENCHMARK-VMMIO-01`）をベンチマーク仕様書・実行スクリプトの識別子に使用する。
- **意味のあるキーワードを連番へ置換しない**: 要求仕様・ADR・Challenge・コンポーネント仕様に由来する意味キーワードは、参照が機械的に見えても `LINK-<連番>` へ置換してはならない。まず本文の不要な参照を削除し、それでも静的ゲート上必要な純粋リンクだけを `LINK-<連番>` とする。

---

## 2. システム横断 メタ & グローバルキーワード (META / GLOBAL)

### 2.1 メタキーワード (`{META_*}`) (20 件)

システム全体の非機能要件、アーキテクチャ方針、C++20/23 ゼロコスト抽象化の設計基準を定義する。

| キーワード | 定義元正本 | 対象コンポーネント | 仕様概要・検証内容 |
| :--- | :--- | :--- | :--- |
| `{META_3TierSeparation}` | `document_structure.md` | `architecture_overview.md` | 設計複雑度に応じた3階層のデコンポジション（分解）とカプセル化された依存関係管理 |
| `{META_ContractImplSplit}` | `document_structure.md` | `system_memory.md` | 抽象契約を上位Tier、物理実装を下位Tierへ意図的に分割するデコンポジションパターン |
| `{META_AI_Native_Dev}` | `document_structure.md` | `architecture_overview.md` | 定型的な実装はLLMを活用し、設計と形式検証の品質を重視する開発方針 |
| `{META_ServiceIsWasmResident}` | `document_structure.md` | `architecture_overview.md` | 「サービス」はWASM上で実行される常駐タスクを指し、HAL・ロギング等のネイティブ常駐基盤機能（サブシステム）とは区別する |
| `{META_AccessDictionary}` | `document_structure.md` | `jit_runtime.md` | データの索引化と、それを用いたランタイムアクセスの最適化 |
| `{META_BinarySearch}` | `document_structure.md` | `system_containers.md` | 静的ソート済み配列に対する $O(\log N)$ の高速二分探索 |
| `{META_BumpAllocator}` | `document_structure.md` | `runtime_memory.md` | メモリ断片化を防ぎ高速な領域確保と一括解放を行うバンプアロケータ |
| `{META_CompileTimeValidation}` | `document_structure.md` | `system_containers.md` | 静的な型チェックや constexpr によるコンパイル時不正検知 |
| `{META_ConfigurableSystem}` | `document_structure.md` | `system_config.md` | ヘッダマクロ定義および constexpr 定数によるシステムパラメータの静的確定 |
| `{META_FaultIsolation}` | `document_structure.md` | `runtime_memory.md` | メモリパーティションによるコンポーネント間の障害伝播防止 |
| `{META_FlatMapIndexed}` | `document_structure.md` | `runtime_vmmio.md` | ソート済み配列や二段テーブルを用いた順序維持・省メモリ高速検索 |
| `{META_NoStdVector}` | `document_structure.md` | `system_containers.md` | 動的ヒープ再配置を行う std::vector の禁止と固定長カスタムコンテナの強制 |
| `{META_RecoveryStrategy}` | `document_structure.md` | `interface_wit.md` | エラーコードの代わりに自己修復リカバリー動作（Retry/Panic等）を返す |
| `{META_RestrictedPhysicalAccess}` | `document_structure.md` | `platform_driver.md` | 物理ハードウェアリソースへの直接アクセスを許可テーブルで厳格に制限 |
| `{META_Risk_Tiering}` | `document_structure.md` | `architecture_overview.md` | リスクベースの設計階層化。重要度・不確実性に応じた検証レベル調整 |
| `{META_SpecificationFirst}` | `document_structure.md` | `interface_wit.md` | 実装に先立ち形式仕様や契約を先行定義する仕様駆動開発方針 |
| `{META_StaticDI}` | `document_structure.md` | `ipc_router.md` | コンパイル時設定・静的バインディングによる依存性の注入（DI） |
| `{META_Static_Resolution}` | `document_structure.md` | `runtime_vmmio.md` | 実行時解決を排しコンパイル時・初期化時に静的決定してオーバーヘッド最小化 |
| `{META_ZeroCostAbstraction}` | `document_structure.md` | `architecture_overview.md` | 抽象化のコストを実行時に支払わないC++ゼロコスト抽象化の徹底 |
| `{META_ZeroOverhead}` | `document_structure.md` | `architecture_overview.md` | ゼロオーバーヘッド原則。余分な仮想関数テーブルや動的バインディングの排除 |

---

### 2.2 グローバルキーワード (`{GLOBAL_*}`) (10 件)

複数のコンポーネントにまたがって適用されるシステム広域ポリシーおよび共通動作要件を定義する。

| キーワード | 定義元正本 | 対象コンポーネント | 仕様概要・検証内容 |
| :--- | :--- | :--- | :--- |
| `{GLOBAL_ComponentHarness}` | `document_structure.md` | `architecture_overview.md` | テスト・検証・サブコンポーネント統合のための共通ハーネスパターン |
| `{GLOBAL_IdleDetection}` | `document_structure.md` | `os_coos.md` | アイドル状態の検出と、登録済みコールバックの起動（呼び出し先の内部処理内容には関与しない） |
| `{GLOBAL_IndependentHeap}` | `document_structure.md` | `system_memory.md` | 各コンポーネントが互いに独立したヒープメモリ領域を確保する設計 |
| `{GLOBAL_InterruptWakeup}` | `document_structure.md` | `os_coos.md` | 割り込み契機による待機タスクのウェイクアップ・復帰処理 |
| `{GLOBAL_PeriodicTask}` | `document_structure.md` | `os_coos.md` | システムティックまたはアイドルループを利用した周期実行タスク |
| `{GLOBAL_Policy_Memory}` | `document_structure.md` | `system_memory.md` | メモリ管理・パーティション分離・専用アロケータに関する横断共通ポリシー |
| `{GLOBAL_StaticScalability}` | `document_structure.md` | `system_config.md` | テンプレート引数・静的定数によるコンパイル時スケーラビリティ |
| `{GLOBAL_StrictMemoryLimit}` | `document_structure.md` | `system_memory.md` | メモリ消費上限が厳格に制限された組み込み動作保証 |
| `{GLOBAL_UseCpp20Coroutine}` | `document_structure.md` | `os_coos.md` | C++20 コルーチンを活用した言語組み込みコンテキストスイッチ |
| `{GLOBAL_UseCpp23Library}` | `document_structure.md` | `system_containers.md` | C++23 標準ライブラリ語彙（std::span 等）の活用方針 |

---

## 3. 全体アーキテクチャ & 設計決定 (ARCHITECTURE / ADR / Challenge)

### 3.1 アーキテクチャ基本設計原則・共通モデル (13 件)

Fireball の全体構造、依存性の方向、リソース予算、品質保証方針を規定するアーキテクチャ原則。

| キーワード | 定義元正本 | 対象コンポーネント | 仕様概要・検証内容 | 対応テストケースID（キーワードではない） |
| :--- | :--- | :--- | :--- | :--- |
| `{CleanArchitecture}` | `requirement_list.md` | `architecture_overview.md` | Clean Architecture の依存ルールに基づく内側への単方向依存・上位Tier優先原則 | - |
| `{ConceptHarnessDI}` | `requirement_list.md` | `architecture_overview.md` | テスト・形式検証容易性のための依存性注入ハーネス設計 | - |
| `{ConsolidatedHeap}` | `requirement_list.md` | `system_memory.md` | システム全体の静的統合ヒープ管理によるメモリ枯渇の排除 | - |
| `{EliminateDataRace}` | `requirement_list.md` | `os_coos.md` | シングルスレッド協調マルチタスクによるデータ競合の構造的排除 | - |
| `{Errorcode_To_Strategy}` | `requirement_list.md` | `interface_wit.md` | 数値エラーコードを廃止し、呼び出し側が判断可能な自己修復ストラテジを返却 | - |
| `{FaultTolerant}` | `requirement_list.md` | `architecture_overview.md` | 部分障害の局所化・安全停止・自動再起動によるフォールトトレラント性 | - |
| `{IoC}` | `requirement_list.md` | `architecture_overview.md` | 制御の反転によるコンポーネント間結合の疎結合化 | - |
| `{LowOverhead}` | `requirement_list.md` | `architecture_overview.md` | 超低消費リソース・高速起動のためのオーバーヘッド最小化 | - |
| `{NotRTOS}` | `requirement_list.md` | `os_coos.md` | リアルタイム性（プリエンプション）よりもメモリ効率と決定論的移植性を最優先 | - |
| `{Pairwise_Combinatorial_Testing}` | `combinatorial_test_spec.md` | `combinatorial_test_spec.md` | 7因子288組の全2因子間ペアを100%網羅する All-Pairs 組み合わせテスト | TEST-PAIR-01〜TEST-PAIR-26 |
| `{Resource_Estimation_Model}` | `requirement_list.md` | `architecture_overview.md` | メモリ・ROM・サイクルバジェットのリソース見積もり予測モデル | - |
| `{Size_15KLOC}` | `requirement_list.md` | `architecture_overview.md` | コード規模を 15,000 行以内に抑制するフットプリント最小化制約 | - |
| `{ZeroRuntimeOverhead}` | `requirement_list.md` | `architecture_overview.md` | インライン展開と直接ディスパッチによる実行時オーバーヘッドゼロの達成 | - |

---

### 3.2 アーキテクチャ判定記録 (`{ADR_*}`) (12 件)

設計上の重大なトレードオフに対して下された技術的判定の記録。

| キーワード | 定義元正本 | 対象コンポーネント | 仕様概要・検証内容 | 対応テストケースID（キーワードではない） |
| :--- | :--- | :--- | :--- | :--- |
| `{ADR_CoosPureRoundRobin}` | `requirement_list.md` | `os_scheduler.md` | COOS スケジューラにおける純粋ラウンドロビン方式の採用（複雑な動的優先度を排除） | - |
| `{ADR_EventDrivenWakeQueue}` | `requirement_list.md` | `os_coos.md` | ポーリングを排しイベントドリブンなウェイクアップキューへの分離 | - |
| `{ADR_FivePoolMemoryModel}` | `system_memory.md` | `system_memory.md` | メモリマネージャを5プール（ホスト用ヒープ・タスクヒープ・共有メモリ用ヒープ・ランタイム用バンプアロケータ・JITキャッシュアロケータ）の統一契約として再定義 | - |
| `{ADR_IntrusiveTcbList}` | `requirement_list.md` | `os_scheduler.md` | 動的アロケーションを排除するための侵入型 TCB（Task Control Block）リスト構造 | - |
| `{ADR_MemoryManagerMinimalSurface}` | `requirement_list.md` | `system_memory.md` | メモリマネージャの公開インターフェース最小化・内部詳細のカプセル化 | - |
| `{ADR_PageGranularPermissionIsolation}` | `runtime_memory.md` | `runtime_memory.md` | 共有メモリの4KB物理ページ単位での排他所有権管理とアクセス権限分離（owner_idフィールドは持たず、マッピング有無で判定） | TEST-MEM-14, TEST-MEM-15 |
| `{ADR_RendezvousChannel}` | `requirement_list.md` | `os_coos.md` | チャネル通信における純粋ランデブー方式（バッファリングなし即時所有権移譲）採用 | - |
| `{ADR_SafeQueuingOnHotMiss}` | `requirement_list.md` | `jit_runtime.md` | JIT キャッシュミス時の安全なキューイングとインタープリタ実行継続 | - |
| `{ADR_ScalableCodeOffset}` | `requirement_list.md` | `jit_compiler.md` | 可変長コードオフセットによる Thumb-2 / AArch64 ジャンプ命令最適化 | - |
| `{ADR_SharedBlockRaii}` | `requirement_list.md` | `system_memory.md` | RAII ガードによる共有メモリブロックの安全かつ確実なスコープ解放 | - |
| `{ADR_TosCacheAsymmetry}` | `requirement_list.md` | `runtime_interpreter.md` | トップ・オブ・スタック（TOS）レジスタキャッシュの非対称同期アーキテクチャ | - |
| `{ADR_TraceBoundaryYield}` | `runtime_interpreter.md` | `runtime_interpreter.md` | インタープリタの命令ハンドラが vSoC へ制御を返す頻度をトレース境界に限定する設計判断 | Scenario 6 (TEST-INT-50) |

---

### 3.3 設計課題・制約追跡 (`{Challenge_*}`) (8 件)

組み込み環境の厳しい制約（メモリ極小、リアルタイム性、省電力）下で克服すべき技術課題の追跡。

| キーワード | 定義元正本 | 対象コンポーネント | 仕様概要・検証内容 |
| :--- | :--- | :--- | :--- |
| `{Challenge_ApproximateYield}` | `requirement_list.md` | `runtime_vsoc.md` | 命令カウント概算に基づく Yield 判定の精度とレイテンシのトレードオフ解決 |
| `{Challenge_CoosBlockedList}` | `requirement_list.md` | `os_scheduler.md` | ブロック状態タスクの走査オーバーヘッド抑制と O(1) 状態遷移の保証 |
| `{Challenge_CspHandoffStarvation}` | `requirement_list.md` | `ipc_router.md` | 連続する CSP 直接ハンドオフによる他タスクの飢餓防止（上限回数制限） |
| `{Challenge_DebuggerResource}` | `requirement_list.md` | `debug_manager.md` | リソース制約の厳しい組み込み環境におけるデバッガ常駐 RAM/ROM 最小化 |
| `{Challenge_InterruptSafety}` | `requirement_list.md` | `os_coos.md` | 割り込みハンドラ（ISR）とスケジューラコルーチン間の非同期データ競合防止 |
| `{Challenge_JITCacheEfficiency}` | `requirement_list.md` | `jit_runtime.md` | 固定容量リングバッファ/3面バンクにおけるキャッシュ局所性と代謝効率の最適化 |
| `{Challenge_SyscallMemorySafety}` | `requirement_list.md` | `runtime_syscall.md` | システムコール境界を跨ぐゲストポインタの正当性検証とメモリ破壊防止 |
| `{Challenge_WasiFdWriteLoop}` | `requirement_list.md` | `runtime_syscall.md` | WASI fd_write における多要素 iovec ギャザー出力ループのオーバーヘッド抑制 |

---

## 4. コンポーネント別 要求キーワード & 設計の勘所 (COMPONENT & GOTCHA)

### 4.1 Tier 1 Core: OS・スケジューラ・基盤

OSスケジューラ（`os_coos`, `os_scheduler`）、静的コンテナ（`system_containers`）、設定基盤（`system_config`）の機能要求と設計の勘所。

#### 4.1.1 Tier 1 Core 要求キーワード (15 件)

| キーワード | 定義元正本 | 対象コンポーネント | 仕様概要・検証内容 | 対応テストケースID（キーワードではない） |
| :--- | :--- | :--- | :--- | :--- |
| `{CooperativeMultitasking}` | `requirement_list.md` | `os_coos.md` | コルーチン協調型マルチタスク実行エンジン | Scenario 6 (TEST-INT-50) |
| `{COOS_Deterministic}` | `requirement_list.md` | `os_coos.md` | 完全決定論的なタスク切り替えと実行順序保証 | - |
| `{COOS_Scheduling_Refine}` | `requirement_list.md` | `os_coos.md` | スケジューラディスパッチサイクルの最適化と省電力スタンバイ | - |
| `{COOS_Transparent}` | `requirement_list.md` | `os_coos.md` | タスクコンテキストを意識させない透過的コルーチン抽象化 | - |
| `{CSPCommunication}` | `requirement_list.md` | `os_coos.md` | ホーアCSPに基づく所有権移譲ゼロコピーメッセージパッシング | Scenario 9 (TEST-INT-80) |
| `{CSP_Handoff}` | `requirement_list.md` | `os_coos.md` | チャネルランデブーによるタスク間所有権移譲と直接コンテキストスイッチ | - |
| `{DirectContextSwitch}` | `requirement_list.md` | `os_scheduler.md` | READYキューを経由しないコルーチン直接ジャンプ超低レイテンシ遷移 | Scenario 6, 9 (TEST-INT-50, TEST-INT-80) |
| `{LowOverheadSwitch}` | `requirement_list.md` | `os_scheduler.md` | レジスタ退避を最小限に抑えた超高速コンテキストスイッチ | - |
| `{TaskPollInterruptEvent}` | `requirement_list.md` | `os_scheduler.md` | タスク切り替え境界での原因付き割り込みイベント安全配送 | - |
| `{PackedBitView}` | `requirement_list.md` | `system_containers.md` | ビット単位でのパック構造とメモリ効率の高いフラットビットビュー | - |
| `{FlatViewNarrowing}` | `requirement_list.md` | `system_containers.md` | フラットビューのスコープ限定・スライシングによる安全な部分アクセス | - |
| `{LightweightVerifier}` | `requirement_list.md` | `system_config.md` | 実行前バリデーションを行う軽量静的検証エンジン | - |
| `{ServiceFacade}` | `requirement_list.md` | `system_config.md` | システム共通サービスへのアクセスを一元化するファサード | - |
| `{ServiceSelfReboot}` | `requirement_list.md` | `system_config.md` | 異常検知時におけるサービス自己再起動シーケンス | - |
| `{SelfReboot_via_Event}` | `requirement_list.md` | `system_config.md` | イベント通知契機による協調的セルフリブート | - |

#### 4.1.2 Tier 1 Core 設計の勘所 (GOTCHA) (1 件)

| キーワード | 定義元正本 | 対象コンポーネント | 仕様概要・検証内容 | 対応テストケースID（キーワードではない） |
| :--- | :--- | :--- | :--- | :--- |
| `{GOTCHA-SCHED-01}` | `os_scheduler.md` | `os_scheduler_test_spec.md` | 連続直接ハンドオフ上限到達時、直接遷移を打ち切りタスクをREADYキュー末尾へ戻してメイン巡回ループへ強制復帰する | TEST-SCHED-01 |

---

### 4.2 Tier 1 Interface: IPC Router・システムサービス・WIT

マイクロカーネル間通信ルータ（`ipc_router`）、システムサービス・WASI（`system_service`）、WIT インターフェース定義（`interface_wit`）の機能要求。

#### 4.2.1 Tier 1 Interface 要求キーワード (19 件)

| キーワード | 定義元正本 | 対象コンポーネント | 仕様概要・検証内容 |
| :--- | :--- | :--- | :--- |
| `{IPCRouter}` | `requirement_list.md` | `ipc_router.md` | URIルーティングとRBAC認可を司る中央IPCルータ |
| `{IPC_HandleBased}` | `requirement_list.md` | `ipc_router.md` | ハンドルベースのセキュアかつ高速なIPCエンドポイント管理 |
| `{URIAbstraction}` | `requirement_list.md` | `ipc_router.md` | サービス識別子のURI抽象化による物理アドレス非依存通信 |
| `{IPCDI}` | `requirement_list.md` | `ipc_router.md` | IPCチャネルへの依存性注入による疎結合コンポーネント接続 |
| `{RoleBasedAccessControl}` | `requirement_list.md` | `ipc_router.md` | ロールベースのIPCアクセス制御によるサービス保護 |
| `{OwnershipTransfer}` | `requirement_list.md` | `ipc_router.md` | ゼロコピーメッセージの排他所有権完全移譲モデル |
| `{IPC_ZeroCopy}` | `requirement_list.md` | `ipc_router.md` | ポインタ・記述子の受け渡しによるコピーレスIPC |
| `{IPC_Resource_Isolation}` | `requirement_list.md` | `ipc_router.md` | タスク間での通信リソース枯渇波及を防ぐリソース隔離 |
| `{LowLatencyLookup}` | `requirement_list.md` | `ipc_router.md` | ハッシュ/フラットマップによるURIルーティングの低レイテンシルックアップ |
| `{Asynchronous_Notification}` | `requirement_list.md` | `ipc_router.md` | 非同期イベント通知とタスクウェイクアップの連携 |
| `{IPCRegistry}` | `requirement_list.md` | `ipc_router.md` | コンパイル時または初期化時に確定する静的サービスレジストリ |
| `{WASI_Implementation}` | `requirement_list.md` | `interface_wit.md` | WASI (Preview 1) システムインターフェースの最小サブセット実装 |
| `{WASI_Async_Bridge}` | `requirement_list.md` | `interface_wit.md` | WASI 同期I/O呼び出しとCOOS協調マルチタスクの非同期ブリッジ |
| `{WIT_Interface_Spec}` | `requirement_list.md` | `interface_wit.md` | WebAssembly Component Model WIT形式による型安全インターフェース定義 |
| `{WIT_Common_Types}` | `requirement_list.md` | `interface_wit.md` | コンポーネント間で共通利用される標準型語彙定義 |
| `{WIT_Interface_Purpose}` | `requirement_list.md` | `interface_wit.md` | 明確なインターフェース責務定義と自己修復ストラテジの結合 |
| `{WIT_First}` | `requirement_list.md` | `interface_wit.md` | 実装コードに先行してWITインターフェース契約を定義する開発スタンス |
| `{Type_Vocabulary}` | `requirement_list.md` | `interface_wit.md` | システム全体で整合した標準型ボキャブラリの策定 |
| `{TypeSafeMessaging}` | `requirement_list.md` | `ipc_router.md` | メッセージペイロードの静的型安全性とアライメント保証 |

---

### 4.3 Tier 2 Runtime: vSoC・インタープリタ・ローダ・vMMIO・デバッガ・HAL公開IF

WASM 実行エンジン（`runtime_vsoc`）、CPS スレッドインタープリタ（`runtime_interpreter`）、ゼロコピーローダ（`runtime_loader`）、仮想メモリ管理（`runtime_vmmio`）、GDB RSP デバッガ（`debug_manager`）、ロギングサブシステム（`runtime_logging`）、システムコールランタイム（`runtime_syscall`）の機能要求と設計の勘所。

#### 4.3.1 Tier 2 Runtime 要求キーワード (37 件)

| キーワード | 定義元正本 | 対象コンポーネント | 仕様概要・検証内容 | 対応テストケースID（キーワードではない） |
| :--- | :--- | :--- | :--- | :--- |
| `{EnvironmentPointer}` | `requirement_list.md` | `runtime_vsoc.md` | ゲスト実行環境 execution_context ポインタのレジスタ保持と統一参照 | - |
| `{WasmPageAlignment}` | `requirement_list.md` | `runtime_vsoc.md` | WASM 64KB ページ単位のアライメントとメモリ保護 | - |
| `{UnifiedAccessModel}` | `requirement_list.md` | `runtime_vsoc.md` | リニアメモリ・vMMIO・共有メモリの統一アドレス変換モデル | - |
| `{NativeAPI_Export}` | `requirement_list.md` | `runtime_vsoc.md` | ホストネイティブ関数をゲスト環境へ安全に公開するエクスポート機構 | - |
| `{MultiModule_Support}` | `requirement_list.md` | `runtime_vsoc.md` | 複数 WASM モジュールの独立インスタンス化と名前空間分離 | - |
| `{JIT_Safepoint}` | `requirement_list.md` | `runtime_vsoc.md` | JIT 生成コードおよびインタープリタ内の協調的セーフポイントポーリング | - |
| `{OneRuntimeOneGuest}` | `requirement_list.md` | `runtime_vsoc.md` | 1ランタイム1ゲストの直交分離。マルチインスタンスは独立ランタイム並行起動とIPC協調で実現 | - |
| `{Runtime_BumpAllocator}` | `requirement_list.md` | `runtime_loader.md` | ランタイム単位の固定長データバンプアロケータ所有。モジュール内のシステムコンテナストレージ確保とアンロード時 $O(1)$ 一括解放（W^Xコード分離） | - |
| `{ThreadedInterpreter}` | `requirement_list.md` | `runtime_interpreter.md` | CPS 4引数ディスパッチ、UnifiedStack、レジスタ保持による高速命令実行 | Scenario 1〜11 |
| `{MemoryBoundaryCheck}` | `requirement_list.md` | `runtime_interpreter.md` | ゲストリニアメモリ境界外アクセスのトラップ遮断 | Scenario 1, 8, 10 |
| `{FastAddressCheck}` | `requirement_list.md` | `runtime_interpreter.md` | オフセット境界判定のビット演算による高速アドレスチェック | - |
| `{Interpreter_LazyJITSwitch}` | `requirement_list.md` | `runtime_interpreter.md` | ホットスポット検出時のインタープリタからJITコードへの遅延遷移 | - |
| `{InterpreterContextStackless}` | `requirement_list.md` | `runtime_interpreter.md` | ホストC++コールスタックを消費しないスタックレスインタープリタ設計 | - |
| `{ROMParsing}` | `requirement_list.md` | `runtime_loader.md` | WASM バイナリの Zero-Copy ロード・直接解析 | Scenario 1 |
| `{SinglePassCompilation}` | `requirement_list.md` | `runtime_loader.md` | WASM ロード時における単一パスでのセクション解析・インデックス構築 | - |
| `{Wasm32Only}` | `requirement_list.md` | `runtime_loader.md` | wasm32 アーキテクチャに特化したフットプリント最適化 | - |
| `{ZeroCopyIndexing}` | `requirement_list.md` | `runtime_loader.md` | ROM上のバイナリ構造をコピーせず直接指すゼロコピーインデックス | - |
| `{vMMIO_TrapAndEmulate}` | `requirement_list.md` | `runtime_vmmio.md` | 仮想デバイスアクセス時のトラップ・ホストフック代理ディスパッチ | Scenario 10 (TEST-INT-91) |
| `{DynamicMmap}` | `requirement_list.md` | `runtime_vmmio.md` | 共有メモリID指定による外部バッファの動的 vMMIO マッピング | Scenario 10 |
| `{vMMIO_Isolation}` | `requirement_list.md` | `runtime_vmmio.md` | 仮想MMIO領域のタスク間排他と不正アクセスからの保護 | - |
| `{vMMIO_TLB}` | `requirement_list.md` | `runtime_vmmio.md` | Direct-Mapped TLB による仮想アドレスから物理アドレスへの高速変換 | - |
| `{VDMA}` | `requirement_list.md` | `runtime_vmmio.md` | 仮想ダイレクトメモリアクセス（vDMA）によるバルクデータ転送アクセラレーション | - |
| `{Debug_Integrated}` | `requirement_list.md` | `debug_manager.md` | ランタイムコアに統合された軽量デバッグ支援サブシステム | - |
| `{Debug_Standard_Env}` | `requirement_list.md` | `debug_manager.md` | 標準的なGDBクライアントから透過的に接続可能なデバッグ環境 | - |
| `{RSPMinimalSet}` | `requirement_list.md` | `debug_manager.md` | GDB RSP 最小コマンドセット（?, g/G, m/M, Z0/z0, s, c）の実ソケット対話 | Scenario 7, 8 (TEST-INT-60〜TEST-INT-64) |
| `{RSP_Transport_Selectable}` | `requirement_list.md` | `debug_manager.md` | UART/TCP 等のトランスポート層を切り替え可能な GDB RSP 設計 | - |
| `{DebuggerLabelTableSwitch}` | `requirement_list.md` | `debug_manager.md` | デバッガアタッチ時のインタープリタハンドラテーブル動的切り替え | Scenario 7 |
| `{Debugger_Jit_Flush}` | `requirement_list.md` | `debug_manager.md` | デバッガからのメモリ書き込み（M パケット）時の JIT キャッシュ全バンク即時無効化 | Scenario 7, 8 (TEST-INT-62, TEST-INT-72) |
| `{MemoryIsolation}` | `requirement_list.md` | `runtime_memory.md` | MPU によるコード領域・スタック領域・共有メモリ領域のハードウェア保護 | - |
| `{System_Allocator}` | `requirement_list.md` | `runtime_memory.md` | システム基盤用 dlmalloc アロケータ（`system_allocator`）。システムコンテナ内部ストレージの動的確保・個別解放 | - |
| `{Shm_Allocator}` | `requirement_list.md` | `runtime_memory.md` | IPC 共有メモリ領域（MPU Region 6）用 dlmalloc アロケータ。可変長 shared_block の切り出し・RAII解放時合体 | - |
| `{HAL_Interface}` | `requirement_list.md` | `hal_dispatch.md` | 物理ハードウェアと上位層を抽象化する統一 HAL インターフェース | - |
| `{BufferedLogging}` | `requirement_list.md` | `runtime_logging.md` | 実行時リングバッファ蓄積と COOS idle_hook での一括 UART フラッシュ | Scenario 9 (TEST-INT-82) |
| `{DictionaryBasedIPC}` | `requirement_list.md` | `runtime_logging.md` | 静的 LogDictionary、危険書式（%s/%p）の登録時静的拒絶 | Scenario 9 (TEST-INT-82) |
| `{Syscall_Mapping}` | `requirement_list.md` | `runtime_syscall.md` | WASM システムコール番号から内部ハンドラへの決定論的マッピング | - |
| `{Syscall_Return_Value}` | `requirement_list.md` | `runtime_syscall.md` | システムコール実行結果・エラーコードの規格化された返却規約 | - |
| `{Trap_Interface}` | `requirement_list.md` | `runtime_syscall.md` | ゲスト不正動作検知時のトラップ発行と安全停止インターフェース | - |

#### 4.3.2 Tier 2 Runtime 設計の勘所 (GOTCHA) (15 件)

| キーワード | 定義元正本 | 対象コンポーネント | 仕様概要・検証内容 | 対応テストケースID（キーワードではない） |
| :--- | :--- | :--- | :--- | :--- |
| `{GOTCHA-VSOC-01}` | `runtime_vsoc.md` | `runtime_vsoc_test_spec.md` | JITキャッシュ再判定の主体分離——インタープリタ自身はJITキャッシュを保持・参照せず、トレース境界で vSoC の step() が再判定する | TEST-VSOC-01 |
| `{GOTCHA-VSOC-02}` | `runtime_vsoc.md` | `runtime_vsoc_test_spec.md` | 概算Yieldの主体分離——インタープリタ/JITトレース自身は co_yield を発行せず、vSoC が戻り値を受けて yield_threshold を評価する | TEST-VSOC-02 |
| `{GOTCHA-INTP-01}` | `runtime_interpreter.md` | `runtime_interpreter_test_spec.md` | CPS第4引数 tos（R3）とスタックメモリの境界同期——スタック空時は tos=0、push/pop のたびに tos とスタックメモリ間で退避・復元する | TEST-INTP-01 |
| `{GOTCHA-INTP-02}` | `runtime_interpreter.md` | `runtime_interpreter_test_spec.md` | Label Arity スタック巻き戻し時、宣言アリティ分の結果値のうち最上位値を tos レジスタへ正しく復元する | TEST-INTP-02 |
| `{GOTCHA-INTP-03}` | `runtime_interpreter.md` | `runtime_interpreter_test_spec.md` | if 条件偽（else節なし）で分岐した際、制御フレームを積まずにジャンプし、フレームスタックの深さを不変に保つ | TEST-INTP-03 |
| `{GOTCHA-LOAD-01}` | `runtime_loader.md` | `runtime_loader_test_spec.md` | ハッシュ衝突時のシンボル誤認防止——ハッシュ一致後に ROM 上の文字列を1回比較し完全一致を確認する | TEST-LOAD-01 |
| `{GOTCHA-LOAD-02}` | `runtime_loader.md` | `runtime_loader_test_spec.md` | 検証失敗時のバンプアロケータ完全ロールバック——パース失敗時にバンプポインタをロード開始前の位置へ巻き戻しメモリリークを防ぐ | TEST-LOAD-02 |
| `{GOTCHA-VMMIO-01}` | `runtime_vmmio.md` | `runtime_vmmio_test_spec.md` | Bit 31 RAM 高速バイパス経路はページテーブル走査・TLB検索を一切行わない | TEST-VMMIO-01 |
| `{GOTCHA-VMMIO-02}` | `runtime_vmmio.md` | `runtime_vmmio_test_spec.md` | Direct-Mapped TLB の 4-bit Folding XOR Hash（単純な下位マスクでは異なるFCの同一下位ページが衝突する） | TEST-VMMIO-14 |
| `{GOTCHA-VMMIO-03}` | `runtime_vmmio.md` | `runtime_vmmio_test_spec.md` | SHM Revoke 時、PTEをアンマップし対象TLBスロットを即時破棄してin-flightアクセスを TRAP_UNREGISTERED_PAGE で遮断する | TEST-VMMIO-22, TEST-VMMIO-23 |
| `{GOTCHA-DBG-01}` | `debug_manager.md` | `debug_manager_test_spec.md` | デバッガからのメモリ書き込み（M パケット）実行と同時に JIT キャッシュ全バンクを即時無効化する（{Debugger_Jit_Flush} の勘所） | TEST-DBG-06 |
| `{GOTCHA-DBG-03}` | `debug_manager.md` | `debug_manager_test_spec.md` | GDB RSP チェックサム不一致パケットはサーバーが破棄しNAK（-）を返して再送を要求する | TEST-DBG-03 |
| `{GOTCHA-DBG-04}` | `debug_manager.md` | `debug_manager_test_spec.md` | 協調スケジューラ下での RSP 応答分割送出と複数 yield 跨ぎ耐性（長小応答 g の分割バッファ蓄積） | TEST-DBG-04 |
| `{GOTCHA-MEM-03}` | `runtime_memory.md` | `runtime_memory_test_spec.md` | 送信中状態（FB_TASK_ID_FLIGHT）は TLB を即時破棄し送受信双方からのアクセスを遮断する；転送失敗時は rollback_transfer() で送信元 owner_id へ復元する | TEST-MEM-10b, TEST-MEM-10c |
| `{GOTCHA-MEM-04}` | `runtime_memory.md` | `runtime_memory_test_spec.md` | W^X 切り替えは命令単位ではなくトランザクションバッチ化し、パッチ完了時に一括で RO+X とキャッシュバリア（DSB/ISB）を発行する | TEST-MEM-24 |

---

### 4.4 Tier 3 JIT: JIT コンパイラ・ランタイム・ゲストアダプタ・プラットフォームドライバ

Copy-and-Patch JIT コンパイラ（`jit_compiler`）および 3 面循環キャッシュ・ホットスポット管理（`jit_runtime`）の機能要求と設計の勘所。

#### 4.4.1 Tier 3 JIT 要求キーワード (14 件)

| キーワード | 定義元正本 | 対象コンポーネント | 仕様概要・検証内容 | 対応テストケースID（キーワードではない） |
| :--- | :--- | :--- | :--- | :--- |
| `{HistoryBuffer}` | `requirement_list.md` | `jit_runtime.md` | JIT ホットスポット検出のための短期実行履歴保持リングバッファ | - |
| `{JIT_CopyAndPatch}` | `requirement_list.md` | `jit_compiler.md` | ステンシル展開とバイナリパッチによる Copy-and-Patch 高速コード生成 | Scenario 4, 5, 8 (TEST-INT-30, TEST-INT-40) |
| `{JIT_Encoder}` | `requirement_list.md` | `jit_compiler.md` | ターゲット命令セット向けパッチ埋め込みコードエンコーダ | - |
| `{JIT_LazyChaining}` | `requirement_list.md` | `jit_compiler.md` | トレース実行完了時の後続ブロックへの遅延直接分岐チェイニング | - |
| `{JIT_ReverseCompilationOrder}` | `requirement_list.md` | `jit_compiler.md` | LIFO 逆順コンパイルによる後続ブロック事前解決と即時チェイニング | - |
| `{LowLatencyJIT}` | `requirement_list.md` | `jit_compiler.md` | 極小コンパイルレイテンシによるリアルタイムJITコード生成 | - |
| `{SimpleJITArchitecture}` | `requirement_list.md` | `jit_compiler.md` | IR（中間表現）生成を省きバイトコードから直結パッチするシンプル構造 | - |
| `{JIT_RegisterMapping}` | `requirement_list.md` | `jit_compiler.md` | ARM AAPCS / Thumb-2 レジスタと VM 状態の決定論的固定マッピング | - |
| `{ContextPointerRegister}` | `requirement_list.md` | `jit_compiler.md` | execution_context を特定物理レジスタに常駐させ間接参照を極小化 | - |
| `{PositionIndependentCode}` | `requirement_list.md` | `jit_compiler.md` | キャッシュバンク配置に依存しない位置独立コード（PIC）生成 | - |
| `{JIT_MultiBuffer_Cache}` | `requirement_list.md` | `jit_runtime.md` | Active / Warm / Oldest 3面バンク循環キャッシュ管理 | Scenario 4, 5 (TEST-INT-31) |
| `{JIT_OldestOnly_Promote}` | `requirement_list.md` | `jit_runtime.md` | 3面キャッシュにおいて Oldest バンクでヒットしたコードのみを Active バンクへ昇格させる Oldest 限定昇格ポリシー | Scenario 4, 5 (TEST-INT-31, TEST-INT-41) |
| `{JIT_RuntimeAPI_Fallback}` | `requirement_list.md` | `jit_runtime.md` | 複雑命令・トラップ発生時のインタープリタランタイムヘルパー安全フォールバック | - |
| `{JIT_ZeroCompileCostTheorem}` | `requirement_list.md` | `jit_runtime.md` | メモリコピーとオフセット加算のみで完了するゼロコンパイルコスト定理の保証 | - |

#### 4.4.2 Tier 3 JIT 設計の勘所 (GOTCHA) (6 件)

| キーワード | 定義元正本 | 対象コンポーネント | 仕様概要・検証内容 | 対応テストケースID（キーワードではない） |
| :--- | :--- | :--- | :--- | :--- |
| `{GOTCHA-JITC-01}` | `jit_compiler.md` | `jit_compiler_test_spec.md` | CPS引数レジスタ（R0-R3）とJIT内部一時レジスタ（R4-R6, R8-R11）が呼び出し境界を越えて物理的に重複しない | TEST-JITC-01 |
| `{GOTCHA-JITC-02}` | `jit_compiler.md` | `jit_compiler_test_spec.md` | mem_base/mem_size は execution_context（[R0, #0x28], [R0, #0x2C]）から一度だけピン留めロードする（独立した env 引数レジスタは廃止済み） | TEST-JITC-02 |
| `{GOTCHA-JITC-05}` | `jit_compiler.md` | `jit_compiler_test_spec.md` | トラップ分岐（BHS.W）はアドレス未確定のままオフセット0で仮発行し、エピローグ生成後に実アドレスへ2パスバックパッチする | TEST-JITC-05 |
| `{GOTCHA-JITC-07}` | `jit_compiler.md` | `jit_compiler_test_spec.md` | トレースの残余値（VM オペランドスタック状態）は `sp` 経由でメモリへ書き込み、トレースは常に void を返す——C/AAPCS の戻り値レジスタとは無関係 | TEST-JITC-07 |
| `{GOTCHA-JITR-02}` | `jit_runtime.md` | `jit_runtime_test_spec.md` | Oldestバンクからの昇格時、被チェイン登録（inbound_sources）を昇格先バンクへ移管しダングリングジャンプを防ぐ | TEST-JITR-09 |
| `{GOTCHA-JITR-03}` | `jit_runtime.md` | `jit_runtime_test_spec.md` | LIFO逆順コンパイル（後入れ先出し）により、先行ブロックコンパイル時点で後続ブロックが既にキャッシュ常駐し即時チェイニングが成立する | TEST-JITR-12 |

---

### 4.5 Tier 3 Platform: HAL ドライバ実装

ハードウェア抽象化ドライバの機能要求と設計の勘所。契約/実装分割パターン（`{META_ContractImplSplit}`）により、抽象化層（URI Resolver、コマンドプロトコル）は Tier 2（`hal_dispatch.md`）、物理ドライバ実装は Tier 3（`platform_driver.md`）に配置される。メモリマネージャも同パターンにより Tier 1（`system_memory.md`）と Tier 2（`runtime_memory.md`）へ移設済みのため、本書内の Tier 1 Core（`system_memory.md`）および Tier 2 Runtime（`runtime_memory.md`）の項を参照。

#### 4.5.1 Tier 3 Platform 要求キーワード (2 件)

| キーワード | 定義元正本 | 対象コンポーネント | 仕様概要・検証内容 |
| :--- | :--- | :--- | :--- |
| `{Fast_Path_GPIO}` | `requirement_list.md` | `platform_driver.md` | コンテキストスイッチを介さず直接ポート操作を行う GPIO 高速パス |
| `{PhysicalPassthrough}` | `requirement_list.md` | `platform_driver.md` | 認可された特定周辺ペリフェラルへのゼロオーバーヘッド直接物理パススルー |

#### 4.5.2 Tier 3 Platform 設計の勘所 (GOTCHA) (1 件)

| キーワード | 定義元正本 | 対象コンポーネント | 仕様概要・検証内容 | 対応テストケースID（キーワードではない） |
| :--- | :--- | :--- | :--- | :--- |
| `{GOTCHA-HAL-01}` | `platform_driver.md` | `platform_driver_test_spec.md` | HalBufferPool は固定サイズを超えるスライス要求を即座にエラー/アサーション違反で拒絶する（隣接バッファ汚染防止） | TEST-HAL-06 |

---

## 5. ドキュメント構造・内部連携用 リンクキーワード (LINK)

ドキュメント間の物理メモリレイアウト整合、低層ディスパッチ規約、内部バイパス・状態連携のための専用リンクアンカー（全 43 件）。

### 5.1 物理メモリレイアウト・実行環境アンカー (4 件)

コンポーネント間を跨いで厳密に一致しなければならない C++ 構造体のバイトレイアウトアンカー。

| キーワード | 定義元正本 | 対象コンポーネント | 仕様概要・検証内容 | 対応テストケースID（キーワードではない） |
| :--- | :--- | :--- | :--- | :--- |
| `{ExecutionContext_Layout}` | `architecture_overview.md` | `runtime_interpreter.md` | execution_context 60バイト物理フィールド配置（15フィールド、R0起点。3独立バッファ頂点・境界、リニアメモリ情報、グローバル基底・上限、ハンドラテーブルを内包、ADR-INTERP-03） | Scenario 1〜11 |
| `{CallFrame_Layout}` | `runtime_interpreter.md` | `architecture_overview.md` | call_frame 12バイト、LocalStack 専用の独立固定容量バッファへのインライン物理配置 | Scenario 3, 8 |
| `{ControlFrame_Layout}` | `runtime_interpreter.md` | `architecture_overview.md` | control_frame 16バイト、OperandStack/LocalStack とは独立した専用固定容量バッファへの物理配置 | Scenario 3 |
| `{VsocRuntime_Layout}` | `architecture_overview.md` | `runtime_vsoc.md` | execution_context 内包 vsoc_runtime 16バイト物理実行環境配置 (+0x28〜+0x37) | Scenario 1〜11 |

---

### 5.2 ディスパッチ・レジスタ規約アンカー (2 件)

インタープリタ・JIT・ネイティブ境界における低層レジスタ渡し・関数呼び出し規約アンカー。

| キーワード | 定義元正本 | 対象コンポーネント | 仕様概要・検証内容 | 対応テストケースID（キーワードではない） |
| :--- | :--- | :--- | :--- | :--- |
| `{AAPCS_FastCall}` | `architecture_overview.md` | `runtime_interpreter.md` | CPS 4引数 AAPCS レジスタマッピング規約 (R0=ctx, R1=sp, R2=local_base, R3=tos) | Scenario 1〜11 |
| `{CPS_4Args}` | `runtime_interpreter.md` | `runtime_interpreter.md` | ctx, sp, local_base, tos による 4 引数 CPS ディスパッチ規約 | Scenario 1〜11 |

---

### 5.3 内部バイパス・最適化・状態連携アンカー (37 件)

複数コンポーネント間の連携経路、最適化バイパス、非同期状態同期を紐付けるアンカー。

| キーワード | 定義元正本 | 対象コンポーネント | 仕様概要・検証内容 | 対応テストケースID（キーワードではない） |
| :--- | :--- | :--- | :--- | :--- |
| `{ActiveDataSegments}` | `runtime_loader.md` | `runtime_loader.md` | ロード時のアクティブデータセグメント自動リニアメモリ展開 | Scenario 1 (TEST-INT-01) |
| `{BitView_CardMarking}` | `system_containers.md` | `jit_runtime.md` | 関数ごと 8バイト/カード 2-bit カードマーキング Hotspot 検出（UNEXEC → EXEC → HOT → COMPILED） | Scenario 4 (TEST-INT-30) |
| `{ControlFrameCleanup}` | `runtime_interpreter.md` | `runtime_interpreter.md` | br_table / block / loop / if 偽分岐時のスタックフレーム自動復元 | Scenario 3 (TEST-INT-20, TEST-INT-22) |
| `{DeterministicRingBuffer}` | `runtime_logging.md` | `runtime_logging_test_spec.md` | リングバッファ満杯時、ブロックやエラーを起こさず最古エントリを上書きして直近ログを保存する非ブロック不変条件 | TEST-LOG-02 |
| `{DirectBytecodeExecution}` | `runtime_interpreter.md` | `runtime_interpreter.md` | ROM/Flash バイトコード直接デコード、命令オブジェクト生成ゼロ、およびポインタ加算（ip + len）によるO(1)命令実行 | Scenario 1〜11 (TEST-INTP-50) |
| `{DirectMappedJIT16}` | `jit_runtime.md` | `jit_runtime.md` | 32-bit UnifiedPC の 4-bit Folding XOR Hash による 16エントリ Direct-Mapped JIT キャッシュ一撃検索 | Scenario 4, 5 (TEST-JITR-26) |
| `{DirectMappedTLB16}` | `runtime_vmmio.md` | `runtime_vmmio.md` | 20-bit VPN の 4-bit Folding XOR Hash による Direct-Mapped TLB | Scenario 10 (TEST-INT-92) |
| `{FlatMapView_BinarySearch}` | `system_containers.md` | `system_containers.md` | 静的ソート配列に対する $O(\log N)$ バイナリサーチ（動的割当なし） | Scenario 1, 9 (TEST-INT-01, TEST-INT-80) |
| `{FuelExhaustion_Yield}` | `os_scheduler.md` | `os_scheduler.md` | Fuel 枯渇（トレース境界での quantum 判定）での決定論的な中断と再開 | Scenario 6 (TEST-INT-50) |
| `{HAL_PeripheralDrivers}` | `platform_driver.md` | `platform_driver.md` | GPIO（入出力・エッジIRQ）、I2C（LM75）、SPI（EEPROM）、Timer ダミードライバ | Scenario 11 (TEST-INT-100〜TEST-INT-102) |
| `{ISR_Safety}` | `os_coos.md` | `os_coos_test_spec.md` | ISRコンテキストとスケジューラ境界の分離——ISRはイベントキューへの記録のみ行い、run_step 開始時の割り込みドレインで初めてタスクがREADYへ遷移する | TEST-COOS-03 |
| `{InterruptibleFlush}` | `runtime_logging.md` | `runtime_logging_test_spec.md` | flush 実行中に interrupt_pending() が真を返した時点で全フラッシュを強行せずループを抜けてスケジューラへ制御を戻す | TEST-LOG-03 |
| `{JIT_CandidateBitmap}` | `runtime_loader.md` | `runtime_loader.md` | WASMロード時にJITコンパイル対象と判定された基本ブロックをCard単位1bitでマーキングするJIT候補ビットマップ（非候補カードでのtouchスキップ連携） | TEST-LOAD-49, TEST-LOAD-50 |
| `{JIT_StaticBenefitScoring}` | `runtime_loader.md` | `runtime_loader.md` | 128B BitView<4>のint4_tテーブルによる機械語短縮数ベースの静的基本ブロック適格性スコアリング（閾値9点判定） | TEST-LOAD-49 |
| `{JitBranchChainingHandler}` | `jit_compiler.md` | `jit_compiler.md` | JIT 専用チェイニングハンドラと純粋インタープリタ分岐ハンドラの分離 | Scenario 4, 5 |
| `{Libgcc_Runtime_Helper}` | `runtime_interpreter.md` | `runtime_interpreter.md` | i64 / f32 / f64 の libgcc 依存演算をランタイムヘルパー関数経由で実行する設計 | Scenario 1, 8 |
| `{Loader_BasicBlockIndex}` | `runtime_loader.md` | `runtime_loader.md` | WASMローダによる全ベーシックブロックメタ情報（BasicBlock）の不変抽出と RadixBinaryTreeView（bswap32キー）索引の所有・公開 | TEST-LOAD-48 |
| `{MPU_WX_Enforcement}` | `runtime_memory.md` | `runtime_memory.md` | JITコンパイル時のMPU属性切り替え（RW+XN ⇔ RO+X）とキャッシュコヒーレンシバリア発行のトランザクションバッチ化ポリシー | TEST-MEM-04 |
| `{MainLoopReturnGuarantee}` | `os_coos.md` | `os_coos.md` | 連続ハンドオフ上限到達時のメインループ強制復帰形式保証 | Scenario 6 |
| `{Orthogonal_Design}` | `os_coos.md` | `os_coos_test_spec.md` | 1チャネル1待機者の強制——多重待機はプログラミングエラーとして即座にアサーション違反で停止する（待機列によるキューイングを設計上排除） | TEST-COOS-02 |
| `{OwnerMismatchTrap}` | `runtime_vmmio.md` | `runtime_vmmio.md` | タスク間共有メモリ（FC=0xE）の所有権移動に伴うアンマップによる未登録ページフォルト（TRAP_UNREGISTERED_PAGE）遮断 | Scenario 10 (TEST-INT-93) |
| `{PageGranularPermissionIsolation}` | `runtime_memory.md` | `runtime_memory.md` | 共有メモリの4KB物理ページ単位での排他所有権管理とアクセス権限分離 | TEST-MEM-14 |
| `{PreflightRejection}` | `ipc_router.md` | `ipc_router.md` | Revoke前の静的チェック（RBAC拒否・メッセージサイズ超過）失敗時、所有権は送信側から一度も動かない | Scenario 9 (TEST-INT-81) |
| `{RAM_Bypass_Bit31}` | `runtime_vmmio.md` | `runtime_vmmio.md` | Bit 31 == 0 アドレスに対するページテーブル不使用 $O(1)$ 高速バイパス | Scenario 10 (TEST-INT-90) |
| `{RSPChecksumVerify}` | `gdb_rsp_protocol.md` | `debug_manager.md` | GDB RSP パケットのチェックサム検証と、不一致時のNAK応答による再送制御ポリシー | TEST-DBG-03 |
| `{RadixBinaryTreeView_bswap32}` | `system_containers.md` | `jit_runtime.md` | UnifiedPC（func_idx << 20 | pc）の bswap32 による Radix 検索 | Scenario 5 (TEST-INT-40, TEST-INT-41) |
| `{RingBuffer_Overwrite}` | `system_containers.md` | `system_containers.md` | 静的容量リングバッファ、満杯時の最古エントリ自動上書き | Scenario 9 (TEST-INT-82) |
| `{SignZeroExtension}` | `runtime_interpreter.md` | `runtime_interpreter.md` | 8/16/32-bit メモリ読み書きの符号付き・符号なしゼロ/符号拡張 | Scenario 8 (TEST-INT-70) |
| `{Syscall_ProcExit}` | `runtime_syscall.md` | `runtime_syscall.md` | proc_exit システムコールによるゲストタスク停止および終了コード伝播 | Scenario 2 (TEST-INT-11) |
| `{ThreeBankCacheEviction}` | `jit_runtime.md` | `jit_runtime.md` | 3面バンク代謝と Oldest ヒット時の Active 昇格・局所アンリンク | Scenario 4, 5 (TEST-INT-31, TEST-INT-41) |
| `{ThreeStageRouting}` | `ipc_router.md` | `ipc_router.md` | Stage 1 URI検索 → Stage 2 RBAC判定 → Stage 3 Zero-Copy CSP Rendezvous 所有権移譲 | Scenario 9 (TEST-INT-80, TEST-INT-81) |
| `{TraceBoundaryInvariant}` | `jit_compiler.md` | `jit_compiler.md` | トレース境界でのスタック自己完結性、メモリ同期、およびフォールバック | Scenario 4, 5 (TEST-INT-31, TEST-INT-41) |
| `{TrackableBlockMask}` | `jit_runtime.md` | `jit_runtime.md` | ロード時に一度だけ確定する 1-bit ブロック追跡可否マスク。カードマーキング表の更新対象かをディスパッチ時に $O(1)$ 判定 | Scenario 4, 5 (GOTCHA-JITR-07) |
| `{VSOC_Lifecycle}` | `runtime_vsoc.md` | `runtime_vsoc.md` | vSoC Engine の状態遷移とインタープリタ／JIT切り替えライフサイクル | Scenario 7, 8 |
| `{VmmioShmDelegation}` | `runtime_vmmio.md` | `runtime_memory.md` | vMMIO FC=14共有メモリマッピングと権限・TLB無効化のメモリマネージャリスナー移譲 | TEST-MEM-15 |
| `{WASI_InMemVFS}` | `libfireball.md` | `libfireball.md` | WASI互換ゲストアダプタによる fd_seek, fd_read, fd_write, random_get, clock_time_get | Scenario 11 (TEST-INT-103〜TEST-INT-105) |
| `{WASI_ScatteredIO}` | `libfireball.md` | `libfireball.md` | 分散ギャザー fd_write / スキャッター fd_read による多要素 iovec 転送 | Scenario 2, 11 (TEST-INT-10, TEST-INT-104) |
