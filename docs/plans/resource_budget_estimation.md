# 物理リソース予算 & C++ 実装規模見積もり計画書 {Resource_Estimation_Model}

## 1. 目的

<!-- traceability: {Resource_Estimation_Model} {Size_15KLOC} {GLOBAL_StrictMemoryLimit} {ConsolidatedHeap} {ROMParsing} {META_ZeroCostAbstraction} -->
本ドキュメントは、Python リファレンスシミュレータ（`experiments/pysim`）の検証結果および各コンポーネントのアルゴリズムに基づき、Clang 17+ 組み込み C++（静的配置、ゼロ動的アロケーション、AoS `flat_map_view`、`[[clang::musttail]]`）へ本実装した際の**実装規模（LOC）**および**物理リソース予算（ROM / RAM）**の厳密な見積もりを定義する計画書である。 `{Resource_Estimation_Model}`

Phase 0 の「Step 2.4: 物理リソース予算の厳密な再見積もり」における正本ドキュメントとして、ROM（`.rodata` / `.text`）に配置可能な不変データと、RAM（SRAM / `.data` / `.bss`）に配置すべき可変状態・バッファ・スタックを厳密に区別して算出する。

---

## 2. C++ 実装規模見積もり（テストコード除く）

Python シミュレータ（`experiments/pysim`）の実装行数（実測 12,580 行）をベースに、型安全な C++ 静的ヘッダ（`.hxx`）、不変条件アサーション、および実装コード（`.cxx`）への移行係数（約 1.15〜1.25 倍）を適用して算出した。

全サブシステムの合計規模は **約 14,500 LOC** となり、非機能要求 **`{Size_15KLOC}`（システム全域 15,000 行以内）** を確実に満足する。 `{Size_15KLOC}`

### サブシステム別 実装規模（LOC）一覧

| サブシステム | pysim 実装行数 (実測) | C++23 見積行数 (LOC) | 主な構成要素と C++ 実装設計方針 |
| :--- | :---: | :---: | :--- |
| **Tier 1 Core** | **1,993** | **~2,950** | |
| - `system_containers` | 1,154 | ~1,200 | `flat_map_view`, `flat_set_view`, `radix_binary_tree_view`, `bit_view`, `mutable_*_storage`（ヘッダオンリー） |
| - `os_coos` & `os_scheduler` | 483 | ~650 | C++20 コルーチン（対称遷移）、侵入型 READY/WAIT リスト、CSP チャネル同期 |
| - `system_config` & `recovery` | 163 | ~200 | コンフィグマクロ、`constexpr` 定数群、リカバリー戦略列挙型 |
| - `system_logging` | 192 | ~300 | 辞書参照リングバッファ、アイドル時 DMA フラッシュフック |
| - `system_syscall` | (wasi/hal連携) | ~600 | `fireball_call` トラップハンドラ、引数レジスタ直接マッピング |
| **Tier 1 Interface** | **582** | **~850** | |
| - `ipc_router` | 582 | ~850 | URI レジストリ（ROM AoS FlatMap）、4x4 RBAC マトリックス、所有権移譲（Revoke/Grant） |
| **Tier 2 Runtime (vSoC)** | **6,552** | **~7,200** | |
| - `runtime_loader` | 1,001 | ~1,100 | Zero-Copy ROM パーサー、128B `OpcodeBenefitTable`、`JITCandidateBitmap` スコアラー |
| - `runtime_interpreter` | 1,948 | ~2,100 | CPS 継続渡し (`[[clang::musttail]]`)、テーブルディスパッチ、非候補 touch バイパス |
| - `runtime_control_flow` | 875 | ~900 | ブロック/ループ/IF 制御フレームスタック管理、ラベル脱出解決 |
| - `runtime_vmmio` | 324 | ~450 | 16 エントリダイレクトマップ TLB、PTE FlatMap、Guest RAM バイパス |
| - `runtime_engine` (ハーネス) | 1,415 | ~1,650 | `execution_context`、3本独立スタック、`HotspotBitmap`、`HistoryRing` |
| - `debug_manager` & GDB RSP | 436 | ~500 | RSP パケットパーサー、ブレークポイント集合、実行頻度プロファイラ |
| - その他 (LEB128/WASM型) | 553 | ~500 | 高速デコーダ、WASM 定数・シグネチャテーブル |
| **Tier 3 JIT Compiler & Runtime** | **1,344** | **~1,700** | |
| - Copy-and-Patch JIT コア | 530 | ~700 | Stencil 解決、リロケーション適用、逆順コンパイル（LIFO） |
| - Stencil カタログ (Thumb-2) | 617 | ~700 | `constexpr` Thumb-2 機械語バイナリテンプレート配列 |
| - JIT コードキャッシュ代謝 | 197 | ~300 | 3面世代交代（Active/Warm/Oldest）、Oldest限定昇格、MPU $W \oplus X$ 制御 |
| **Tier 3 Platform & HAL** | **2,109** | **~1,800** | |
| - `platform_memory` | 737 | ~700 | 統合物理プール（ConsolidatedHeap）、静的パーティショニング、SHM マネージャ |
| - `platform_hal` & Drivers | 611 | ~600 | 協調 HAL タスク、UART/RTT/GPIO/I2C/SPI ドライバ、ISR リングバッファ |
| - WASI Preview 1 Adapter | 760 | ~500 | `fd_write`, `fd_read`, `clock_time_get` 等の薄い HAL ラッパー |
| **合計** | **12,580** | **~14,500 LOC** | **`{Size_15KLOC}` (15,000 LOC 以内) を完全に達成** |

---

## 3. 物理リソース予算見積もり（ROM vs RAM）

評価ターゲット環境：**最小構成 SRAM 32KB / Flash 128KB〜256KB**。
動的ヒープ確保（`malloc` / `new`）を一切排除し、全メモリをコンパイル時に静的割り当て（`constexpr` / `.bss` / `.data`）する。 `{GLOBAL_StrictMemoryLimit}` `{ConsolidatedHeap}`

### 3.1 RAM（SRAM: 可変状態・バッファ・スタック）予算内訳

RAM 領域は、主動作用の**統合物理メモリプール（`ConsolidatedHeap`: 21,504 B）**と、システム起動・割り込み処理用の**プール外静的変数・OS スタック（約 3.5 KB）**に明確に分類される。

```
+-------------------------------------------------------------------------------+
|                       TOTAL SRAM BUDGET: 32,768 Bytes                         |
+-------------------------------------------------------+-----------------------+
|  統合物理メモリプール (FB_CONF_MEMORY_POOL_SIZE): 21,504 B | OSスタック/静的変数:   |
|  [Kernel] 4KB  [Runtime] 2KB  [Subsys] 3KB            | ~3,500 B              |
|  [JIT Cache] 6KB  [Stack] 2KB  [Guest RAM] 4KB        | (安全余裕: ~7.7 KB)   |
+-------------------------------------------------------+-----------------------+
```

| メモリ領域 / データ実体 | RAM サイズ | ライフサイクル・用途・保護 |
| :--- | :---: | :--- |
| **1. 統合物理メモリプール (`ConsolidatedHeap`)** | **21,504 B** | システム共通の静的事前確保物理プール |
| - **JIT コードキャッシュ** (`FB_CONF_JIT_CACHE_SIZE`) | 6,144 B | 2,048 B $\times$ 3面（Active / Warm / Oldest）。MPU $W \oplus X$ 保護 |
| - **ゲスト仮想タスク RAM** (`FB_CONF_TASK_HEAP_SIZE`) | 4,096 B | ゲスト WASM リニアメモリ実体（`0x0000_0000`、FastAddressCheck 対象） |
| - **カーネルプール** (`FB_CONF_KERNEL_HEAP_SIZE`) | 4,096 B | TCB（16件 $\times$ 96B $\approx$ 1.5KB）、コルーチンフレーム、<br>**共有メモリバッファ (`FB_CONF_SHM_SIZE`: 1,024 B)** を内包 |
| - **サブシステムプール** (`FB_CONF_SUBSYS_HEAP_SIZE`) | 3,072 B | HAL 通信バッファ（256B $\times$ 4面 = 1KB）、GDB RSP バッファ（1KB）、<br>リングバッファロガー（512B） |
| - **ランタイムプール** (`FB_CONF_RUNTIME_HEAP_SIZE`) | 2,048 B | `execution_context`（44B）、WASM モジュールインスタンス状態、<br>`HotspotBitmap`（128B）、`JITCandidateBitmap`（128B）、`HistoryRing`（64B） |
| - **インタープリタ統合スタック** (`FB_CONF_INTERP_STACK_SIZE`) | 2,048 B | `OperandStack`（1KB）、`LocalStack`（768B）、`ControlFrame`（256B） |
| **2. システム静的変数 & OS スタック（プール外）** | **~3,500 B** | |
| - vMMIO ソフトウェア TLB キャッシュ配列 | 128 B | 16 エントリ $\times$ 8B（VPN + PTE）ダイレクトマップ高速 TLB |
| - ブレークポイント集合 / プロファイラバッファ | 320 B | ブレークポイント（8件 $\times$ 4B）＋ PC サンプル配列（64件 $\times$ 4B） |
| - ISR 割り込み通知リングバッファ | 64 B | 16 エントリ $\times$ 4B（原子キュー） |
| - ベアメタル OS システムスタック（Cortex-M MSP） | 2,048 B | 例外・割り込みハンドラ（ISR）実行用ハードウェアスタック |
| - グローバルポインタ・フラグ・TCBインデックス | ~500 B | カーネル・ディスパッチャ状態変数 |
| **RAM 合計使用量** | **~25,000 B** | **32KB SRAM に対し ~7.7KB（約 24%）の安全マージンを確保** |

---

### 3.2 ROM（Flash: 不変データ `.rodata` & 機械語コード `.text`）予算内訳

ROM 領域は、コンパイル時に静的に確定する不変ルックアップテーブル・辞書（`.rodata`）と、ハイパーバイザ本体の機械語コード（`.text`）で構成される。WASM ゲストバイナリ自体は Flash 上のバイト列を直接パース・実行するため、RAM への展開を伴わない。 `{ROMParsing}`

| データ実体 / テーブル名 | ROM サイズ | 配置理由・不変条件（なぜ ROM に置けるか） |
| :--- | :---: | :--- |
| **1. 不変ルックアップテーブル & 辞書 (`.rodata`)** | **~8.2 KB** | |
| - **`OpcodeBenefitTable`** | 128 B | 256 命令 $\times$ 4-bit（`BitView<4>`）。ロード時スコアリング用定数表 |
| - **WASM 命令ハンドラテーブル** | 1,024 B | 256 命令 $\times$ 4B（インタープリタ CPS 関数ポインタ配列） |
| - **JIT Stencil カタログ (Thumb-2)** | ~4,500 B | Copy-and-Patch 用の Thumb-2 機械語バイナリテンプレート群 |
| - **IPC サービスレジストリ** | ~512 B | ソート済み `flat_map_entry<std::string_view, registry_entry>` 定数配列 |
| - **IPC RBAC 権限マトリックス** | 16 B | 4 $\times$ 4 ロール間通信可否ビット配列（`constexpr`） |
| - **ログ辞書 (LogDictionary)** | ~1,500 B | ビルド時登録の `printf` フォーマット文字列テーブル（`{DictionaryBasedIPC}`） |
| - **vMMIO 静的領域定義テーブル** | ~128 B | 静的デバイス領域（FC=12等）のベース・サイズ・アクセス権限定義 |
| - **WASM ゲストバイナリ (Zero-Copy)** | (可変) | Flash 上のバイト列を直接パース・実行（RAM 展開不要） |
| **2. ハイパーバイザ機械語コード (`.text`)** | **~45〜55 KB** | |
| - インタープリタ実行エンジン（256命令） | ~18 KB | `[[clang::musttail]]` 直結末尾呼び出しハンドラ群 |
| - JIT コンパイラ & アセンブラコア | ~12 KB | Stencil コピー、リロケーション計算、逆順キュー処理 |
| - COOS カーネル & スケジューラ & IPC | ~8 KB | コルーチンスイッチ、同期ランデブー、所有権管理 |
| - WASM ローダー & デコーダ | ~7 KB | セクション解析、型チェック、CandidateBitmap スコアリング |
| - HAL / WASI ドライバ & GDB デバッガ | ~8 KB | UART/GPIO/Timer ドライバ、RSP パーサー、Shim レイヤ |
| **ROM 合計使用量** | **~53〜63 KB** | **最小 Flash 128KB に対し約 50% の容量で収容可能** |

---

## 4. 予算整合性と成立性総評

1. **実装規模の成立性**:
   - `pysim`（12,580 行）から算出した C++ 本実装は約 14,500 LOC であり、要求 `{Size_15KLOC}`（15,000 行以内）の制約を完全に充足する。
2. **RAM リソースの成立性**:
   - 統合物理プール（21.5 KB）＋ システムスタック・静的変数（約 3.5 KB）＝ 約 25.0 KB。
   - 32KB SRAM の評価ターゲット環境において、約 7.7 KB（24%）の安全マージンが確保されており、不測のスタック拡張やバッファ調整に十分耐えうる。
3. **ROM リソースの成立性**:
   - 不変テーブル（約 8.2 KB）とコード本体（約 45〜55 KB）の合計は約 53〜63 KB であり、最小構成の 128KB Flash に対して約 50% の空き容量を残して安全に格納できる。
4. **JITCandidateBitmap 機能追加の影響**:
   - ロード時基本ブロック判定機能の追加による純増リソースは、ROM 128 バイト（`OpcodeBenefitTable`）および RAM 128 バイト（`JITCandidateBitmap`）のみであり、本予算計画の枠内に完全に収まる。
