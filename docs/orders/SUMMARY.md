# Fireball システム設計サマライズ

本ドキュメントは、Fireballハイパーバイザの設計における主要なパターン、コンセプト、およびコンポーネント設計を統合的にサマライズしたものである。設計のホールネス（全体性）とインテグリティ（整合性）を維持するための重要情報を網羅し、各詳細ドキュメントへのポインタを提供する。

---

## 1. 設計パターンサマライズ
Fireballにおける設計パターンは、リソース制約の厳しい組み込み環境において、移植性、拡張性、およびメモリ安全性を確保するための基盤である。

### 1.1 制御の反転 (IoC) パターン
コンポーネント間の結合度を下げ、移植性を最大化するためのインターフェイス設計原則である。クリーンアーキテクチャの思想に基づき、インターフェイスの仕様は「利用側（内側の層）」が定義する。サービスはURI（例：`fireball://hal/uart/0`）によって抽象化され、具体的な実装クラスは隠蔽される。IPCのハンドル管理やメッセージ構築のボイラープレートは、内側の層が定義する「サービスファサード」に閉じ込められ、依存性の逆転を実現する。DTOの型安全性を重視し、`void*`の使用を禁止、構造化データによるやり取りを強制する。
- 関連: [`docs/orders/patterns/ioc.md`](docs/orders/patterns/ioc.md)

### 1.2 ソート済みインデックス付き配列パターン
`std::map`等の動的メモリ確保を伴うコンテナが禁止される環境で、効率的なKey-Value検索を実現する。データがROM上にある場合や、検索頻度が高い場合に最適化されている。Key-Valueペアを直接ソートして保持する方式と、インデックス配列を用いて間接的にソート状態を管理する方式を使い分ける。検索には`std::lower_bound`相当の二分探索を用い、O(log N)の計算量を保証しつつ、メモリ断片化を完全に排除する。
- 関連: [`docs/orders/patterns/sorted_indexed_array.md`](docs/orders/patterns/sorted_indexed_array.md)

### 1.3 標準ライブラリ利用パターン
C++20のモダンな機能を安全に利用するためのガイドラインである。メモリ断片化を防ぐため、`std::vector`や`std::map`の使用を禁止し、`std::array`や`std::span`を推奨する。動的メモリ管理は`dlmalloc`の`mspace`によるヒープパーティション隔離を行い、`new`/`delete`のオーバーロードにより静的な依存性注入を実現する。文字列操作には`std::string_view`を、バイナリデータには`std::span`を用いることで、境界チェックとゼロコピーを両立させる。
- 関連: [`docs/orders/patterns/stdlib.md`](docs/orders/patterns/stdlib.md)

---

## 2. コンセプト・テクニックサマライズ
Fireballが目指す「極小リソースでの高速WASM実行」を実現するための革新的なテクニックとその概念、解決する問題を詳述する。

### 2.1 Zero Compile Cost 定理と Copy-and-Patch JIT
従来のJITコンパイラは、高度な最適化（レジスタ割り当て、命令スケジューリング等）に多大なCPU時間とメモリを消費する。Fireballでは「コンパイルコストを極小化することが、トータルの実行時間を最短にする」という「Zero Compile Cost 定理」を提唱する。これを具現化するのが **Copy-and-Patch** 方式である。
- **概念**: WASM命令に対応するネイティブコードの「テンプレート」をビルド時に生成しておき、実行時にはそれらを連結し、即値やアドレスを「パッチ」するだけでコンパイルを完了する。
- **狙い**: コンパイル時間をほぼゼロにすることで、ループ回数が少ないコードでもJITの恩恵を受けられるようにする。
- **解決する問題**: RAM 64KBという極小環境でのJIT実装の困難さと、コンパイルオーバーヘッドによる性能低下。
- 関連: [`docs/orders/concept/jit_theory_and_prospects.md`](docs/orders/concept/jit_theory_and_prospects.md)

### 2.2 2-bit ホットスポット検知と遅延バッチ判定
実行時のプロファイリング負荷は、インタープリタの性能を著しく阻害する。Fireballでは、実行中のオーバーヘッドを最小化するため、**遅延バッチ判定**を採用する。
- **概念**: インタープリタ実行中はPCの履歴をバッファに記録するのみに留め、`co_yield` によるアイドル時間に一括してホットスポット判定を行う。判定には2ビットのビットマップを用い、「未実行」「実行済み」「HOT」の状態を管理する。
- **狙い**: 実行パス上の分岐コストを排除し、アイドル時間を有効活用してコンパイル要求を生成する。
- **解決する問題**: 実行時プロファイリングによるレイテンシ増大。
- 関連: [`docs/orders/concept/jit_compile_queue.md`](docs/orders/concept/jit_compile_queue.md)

### 2.3 Threaded Interpreter と Environment Pointer
インタープリタの実行効率を最大化するため、**Threaded Dispatch** と **Environment Pointer** を採用する。
- **概念**: 各命令ハンドラを末尾呼び出し（`[[clang::musttail]]`）で連鎖させ、中央の巨大な `switch` 文を排除する。また、周辺コンポーネントへの参照を `env` ポインタに集約し、ハンドラの引数を最小化する。
- **狙い**: 分岐予測の的中率向上と、ハンドラ呼び出しのオーバーヘッド削減。
- **解決する問題**: 従来のインタープリタにおけるディスパッチコストの増大。
- 関連: [`docs/orders/components/interpreter.md`](docs/orders/components/interpreter.md)

### 2.4 URIベースIPCとロールベースアクセス制御 (RBAC)
コンポーネント間の疎結合と安全性を両立させるため、URIによるサービス抽象化と厳密な認可機構を導入する。
- **概念**: サービスは `fireball://` 形式のURIで識別され、IPCルータがチャンネルIDへの解決とアクセス権限のチェックを行う。通信は所有権移譲を伴うメッセージパッシング（CSP）で行われる。
- **狙い**: 実装の隠蔽、動的なサービス差し替え、および不正なタスク間通信の防止。
- **解決する問題**: 密結合な設計による移植性の低下と、共有メモリ起因のデータ競合。
- 関連: [`docs/orders/components/router.md`](docs/orders/components/router.md)

### 2.5 辞書参照IPCとロギング
通信帯域とメモリ消費を抑えるため、静的な辞書を活用した通信方式を採用する。
- **概念**: ログメッセージ等の定型文をビルド時に辞書化し、実行時はそのオフセットと引数のみを転送する。
- **狙い**: 文字列コピーの排除と、通信パケットサイズの極小化。
- **解決する問題**: ログ出力による性能低下とメモリ圧迫。
- 関連: [`docs/orders/components/logging.md`](docs/orders/components/logging.md)

### 2.6 vMMIO Trap-and-Emulate
WASMゲストに対して、物理ハードウェアを直接触らせることなく、仮想的なレジスタ操作インターフェイスを提供する。
- **概念**: 特定のメモリ範囲へのアクセスをインタープリタ/JITレベルでトラップし、登録されたコールバックを呼び出す。
- **狙い**: ハードウェアの仮想化、安全なパススルー、およびゲストOSの移植性向上。
- **解決する問題**: ゲストアプリケーションによる物理リソースの不正操作。
- 関連: [`docs/orders/components/vmmio.md`](docs/orders/components/vmmio.md)

---

## 3. コンポーネント設計サマライズ
Fireballを構成する各コンポーネントの設計意図、構造、および主要なメカニズムを詳述する。

### 3.1 vSoC (Virtual System-on-Chip)
vSoCは、WASM実行環境の統合マネージャであり、ハイパーバイザの核となるコンポーネントである。
- **設計意図**: Loader、Interpreter、JIT、vMMIO、Debuggerを一つの「仮想チップ」として統合し、ゲストに対して一貫した実行環境を提供する。
- **主要設計**:
    - `vsoc_runtime_t` による全サブコンポーネントのライフサイクル管理。
    - `exec_trace` を介した、実行エンジンの透過的な切り替え（インタープリタ ↔ JIT）。
    - ゲストRAMを `0x0` から配置し、高速な境界チェックを実現。
    - `fireball_call` による単一トラップ方式のネイティブAPIエクスポート。
- 関連: [`docs/orders/components/vsoc.md`](docs/orders/components/vsoc.md)

### 3.2 Interpreter (Virtual CPU)
WASM命令をスレッドインタープリタ方式で実行する、仮想CPUの実装である。
- **設計意図**: JITが未完了のコードや、デバッグ時の確実な実行を担う、低レイテンシ・小フットプリントな実行エンジン。
- **主要設計**:
    - `execution_context_t` を仮想レジスタセットとして定義。
    - `[[clang::musttail]]` を用いた継続渡し（CPS）による高速ディスパッチ。
    - `control_frame_t` による制御構造（block/loop/if）の管理と、ジャンプ先 `exec_trace` のキャッシュ。
    - デバッグ時にはハンドラテーブルを `debug_handler_table` へ動的に切り替え。
- 関連: [`docs/orders/components/interpreter.md`](docs/orders/components/interpreter.md)

### 3.3 JIT Compiler (Native Engine)
Copy-and-Patch方式を採用した、極小リソース向けネイティブコード生成エンジンである。
- **設計意図**: RAM 64KB環境において、コンパイルコストを最小化しつつ、インタープリタを凌駕する実行性能を提供する。
- **主要設計**:
    - `JIT_RegisterMapping`: `Context`, `StackTop`, `WASM_PC` を物理レジスタに固定。
    - `JIT_DoubleBuffer_Cache`: Active/Old 領域のダブルバッファによる、断片化のないコード管理とCopy-GC。
    - `Card Marking + Binary Search`: カードグループインデックスを用いた、対数時間でのトレース検索。
    - テンプレートコピーと、即値・アドレスのパッチによる高速コンパイル。
- 関連: [`docs/orders/components/jit_compiler.md`](docs/orders/components/jit_compiler.md)

### 3.4 COOS (Cooperative OS)
C++20コルーチンを活用した、シングルスレッド・スタックレスの協調型OSである。
- **設計意図**: 割り込み応答性を維持しつつ、タスク切り替えのオーバーヘッドを極限まで削減する。
- **主要設計**:
    - ホーアCSP（Communicating Sequential Processes）に基づくタスク間通信。
    - `task_t` (TCB) による、コルーチンハンドルとタスク固有ヒープの管理。
    - `notify_interrupt` による、ISRからの安全なタスクウェイクアップ。
    - 所有権移譲を伴う `co_value_t` による、データ競合の原理的排除。
- 関連: [`docs/orders/components/coos.md`](docs/orders/components/coos.md)

### 3.5 IPC Router
URIベースのメッセージルーティングと、アクセス制御を担う通信基盤である。
- **設計意図**: コンポーネント間の依存性をURIで抽象化し、システム全体の構成柔軟性と安全性を高める。
- **主要設計**:
    - `registry_entry_t` 配列の二分探索による、高速なサービス検索。
    - ロールベースアクセス制御（RBAC）マトリックスによる、通信許可判定。
    - `indexed_array_adapter_t` による、メッセージ内のKey-Valueペアの高速検索。
    - IPCメッセージ転送時の、`co_value_t` 所有権の自動移譲。
- 関連: [`docs/orders/components/router.md`](docs/orders/components/router.md)

### 3.6 WASM Loader
ROM上のWASMバイナリを直接パースし、実行用索引を構築するローダである。
- **設計意図**: RAMへの全展開を避け、ROMデータを直接参照することで、メモリ消費を極小化する。
- **主要設計**:
    - `module_view_t` による、ROM上のセクション範囲と索引の保持。
    - `AccessDictionary`: 関数名やエクスポート情報をソート済み配列として索引化。
    - `LightweightVerifier`: マジック値、バージョン、セクション整合性の最小限の検証。
    - バンプアロケータを用いた、断片化のない管理情報の構築。
- 関連: [`docs/orders/components/loader.md`](docs/orders/components/loader.md)

### 3.7 HAL (Hardware Abstraction Layer)
物理ハードウェアへのアクセス抽象化と、デバッグプロトコルの解析を担う。
- **設計意図**: ターゲット依存部を隔離し、vSoCやサービスに対して統一されたI/Oインターフェイスを提供する。
- **主要設計**:
    - `device_t` レジストリによる、静的なデバイス管理。
    - GDB Remote Serial Protocol (RSP) のパケット解析と、デバッガへのコマンド供給。
    - UART/RTTを選択可能なRSPトランスポート。
    - 割り込みフラグ通知による、タスクコンテキストへの処理委譲。
- 関連: [`docs/orders/components/hal.md`](docs/orders/components/hal.md)

### 3.8 Debugger
GDB RSPに基づく実行制御と、ゲスト状態の可視化を行う。
- **設計意図**: VSCode等の標準的なデバッグ環境から、WASMゲストのデバッグを可能にする。
- **主要設計**:
    - `debug_command_queue_t` を介した、HALからの解析済みコマンドの消費。
    - デバッグ中のJIT無効化と、インタープリタ実行への強制フォールバック。
    - WASMリニアメモリの境界チェックを伴う、安全なメモリアクセス。
    - J-Link RTOS Awarenessに対応した、タスクリストシンボルの公開。
- 関連: [`docs/orders/components/debugger.md`](docs/orders/components/debugger.md)

### 3.9 vMMIO (Virtual MMIO)
ゲストに対する仮想レジスタインターフェイスの提供と、物理アクセスの制限を行う。
- **設計意図**: ゲストOSやドライバに対して、標準化された仮想ハードウェア操作環境を提供する。
- **主要設計**:
    - `vmmio_region_t` による、アドレス範囲ごとのエミュレーション/パススルー設定。
    - `SYSCTL` レジスタによる、Yield、Halt、システムコール引数の受け渡し。
    - `VDMA` による、リニアメモリとvMMIO空間の高速データ転送。
    - `shared_mem_id` を用いた、動的な共有メモリマッピング（mmap）。
- 関連: [`docs/orders/components/vmmio.md`](docs/orders/components/vmmio.md)

### 3.10 Logging
辞書参照とリングバッファを用いた、低負荷な診断情報記録コンポーネントである。
- **設計意図**: 実行性能への影響を最小限に抑えつつ、詳細なシステムログを外部へ出力する。
- **主要設計**:
    - `constexpr` ログ辞書による、文字列転送の排除。
    - `log_ring_buffer_t` による、IPCハンドラからの即時解放と遅延出力。
    - 独立したヒープパーティションによる、メモリ干渉の防止。
- 関連: [`docs/orders/components/logging.md`](docs/orders/components/logging.md)

### 3.11 Services
WASMゲストに対して、WASIやlibc等の共有ライブラリ機能を提供する。
- **設計意図**: 信頼度に応じたTier分離により、ゲストとサービスの障害隔離を実現する。
- **主要設計**:
    - Tier 0: ゲストに直接リンクされる高速サービス（libc, GC, WASI等）。
    - Tier 1: 独立タスクとして動作し、IPC経由で提供されるサービス。
    - サービスごとの独立ヒープパーティション割り当て。
- 関連: [`docs/orders/components/services.md`](docs/orders/components/services.md)

---

## 4. 設計のホールネスとインテグリティ
Fireballの設計は、以下の三原則によって一貫性が保たれている。

1.  **静的構成の徹底**: `{ConfigurableSystem}` `{Static_Resolution}`
    実行時の動的なリソース確保や探索を排除し、すべての制限値や構成をコンパイル時に確定させることで、予測可能性と性能を担保する。
2.  **隔離による安全性**: `{MemoryIsolation}` `{FaultIsolation}`
    タスクごとの独立ヒープ、URIベースの認可、WASMリニアメモリの境界チェックにより、単一のコンポーネントの不具合がシステム全体に波及することを防ぐ。
3.  **リソース効率の極大化**: `{LowLatencyJIT}` `{LowOverheadSwitch}`
    Copy-and-Patch JIT、スタックレスコルーチン、辞書参照IPC等のテクニックを組み合わせ、RAM 64KBという極限環境での実用的な動作を実現する。

---
*Generated by Fireball Summary Workflow*
