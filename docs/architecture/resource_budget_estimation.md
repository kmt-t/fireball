# 物理リソース予算 & C++ 実装規模見積もり仕様書 {Resource_Estimation_Model}

## 1. 目的

<!-- traceability: {Resource_Estimation_Model} {Size_20KSLOC} {GLOBAL_StrictMemoryLimit} {ConsolidatedHeap} {ROMParsing} {META_ZeroCostAbstraction} -->
本ドキュメントは、Python リファレンスシミュレータ（`experiments/pysim`）の検証結果および各コンポーネントのアルゴリズムに基づき、Clang 17+ 組み込み C++（静的配置、ゼロ動的アロケーション、AoS `flat_map_view`、`[[clang::musttail]]`）へ本実装した際の**実装規模（LOC）**および**物理リソース予算（ROM / RAM）**の厳密な見積もりを定義するアーキテクチャ仕様書である。

`backlog_list.md` の物理リソース予算の厳密な再見積もりタスクにおける正本ドキュメントとして、ROM（`.rodata` / `.text`）に配置可能な不変データと、RAM（SRAM / `.data` / `.bss`）に配置すべき可変状態・バッファ・スタックを厳密に区別して算出する。

---

## 2. C++ 実装規模見積もり（テストコード除く）

最新の作業ツリーにある pysim 製品コード46ファイルを再計測した結果、物理行数は18,701行である。この値には空行とコメントを含み、QA、ベンチマーク、シナリオ、`main.py`、`aobench.py` は含めない。

旧見積もりと同じ移行係数1.15〜1.25を参考適用すると、C++ 実装規模は約21.5〜23.4 KSLOCとなる。この係数は暫定値であり、Pythonの物理行数と要求上のSLOCは計測定義が異なるため、この結果だけでは20 KSLOC予算への適合を判定できない。

### Tier 別の pysim 実測と C++ 規模の参考推定

| 対象 | pysim 製品コード物理行数 | C++23 規模の参考推定 |
| :--- | ---: | ---: |
| Tier 1 Core | 1,786 | 約2.1〜2.2 KSLOC |
| Tier 1 Interface | 759 | 約0.9〜1.0 KSLOC |
| Tier 2 Runtime | 12,644 | 約14.5〜15.8 KSLOC |
| Tier 3 JIT | 2,336 | 約2.7〜2.9 KSLOC |
| Tier 3 Platform | 345 | 約0.4 KSLOC |
| 共通入口（`system.py`、`__init__.py`） | 831 | 約1.0 KSLOC |
| **合計** | **18,701** | **約21.5〜23.4 KSLOC** |

要求上のコード規模上限は20 KSLOCである。現時点の参考推定は数値上この上限を上回るため、達成済みとは判定しない。C++実装時には同じ対象範囲・SLOC定義で計測し、実測値に基づいて構成要素別の見積もりを更新する。

---

## 3. 物理リソース予算見積もり（ROM vs RAM）

評価ターゲット環境：**最小構成 SRAM 32KB / Flash 96KB**（想定構成: SRAM 64KB / Flash 128KB、`{Resource_Estimation_Model}`、`requirement_list.md` の制約事項を正本とする）。
動的ヒープ確保（`malloc` / `new`）を一切排除し、全メモリをコンパイル時に静的割り当て（`constexpr` / `.bss` / `.data`）する。 `{GLOBAL_StrictMemoryLimit}` `{ConsolidatedHeap}`

### 3.1 RAM（SRAM: 可変状態・バッファ・スタック）予算内訳

RAM 領域は、主動作用の**統合物理メモリプール（`ConsolidatedHeap`: 23,552 B）**と、システム起動・割り込み処理用の**プール外静的変数・OS スタック（約 3.5 KB）**に明確に分類される。

```
+-------------------------------------------------------------------------------+
|                       TOTAL SRAM BUDGET: 32,768 Bytes                         |
+-------------------------------------------------------+-----------------------+
|  統合物理メモリプール (FB_CONF_MEMORY_POOL_SIZE): 23,552 B | OSスタック/静的変数:   |
|  [Kernel] 4KB  [Runtime] 2KB  [Subsys] 3KB               | ~3,500 B              |
|  [JIT Cache] 8KB  [Stack] 2KB  [Guest RAM] 4KB           | (余裕: ~5.6 KB)       |
+-------------------------------------------------------+-----------------------+
```

| メモリ領域 / データ実体 | RAM サイズ | ライフサイクル・用途・保護 |
| :--- | :---: | :--- |
| **1. 統合物理メモリプール (`ConsolidatedHeap`)** | **23,552 B** | システム共通の静的事前確保物理プール |
| - **JIT コード領域** (`FB_CONF_JIT_CACHE_SIZE`) | 8,192 B | 連続8KB。共通コード2,048 B（非エビクション）+ 2,048 B $\times$ 3面（Active / Warm / Oldest）。MPU $W \oplus X$ 保護 |
| - **ゲスト仮想タスク RAM** (`sum(FB_CONF_TASK_HEAP_SIZES)`) | 4,096 B | ゲスト WASM リニアメモリ実体（`0x0000_0000`起点、スロット別ROM配列の総和、FastAddressCheck 対象） |
| - **カーネルプール** (`FB_CONF_KERNEL_HEAP_SIZE`) | 4,096 B | TCB（16件 $\times$ 96B $\approx$ 1.5KB）、コルーチンフレーム、<br>**共有メモリバッファ (`FB_CONF_SHM_SIZE`: 1,024 B)** を内包 |
| - **サブシステムプール** (`FB_CONF_SUBSYS_HEAP_SIZE`) | 3,072 B | HAL 通信バッファ（256B $\times$ 4面 = 1KB）、GDB RSP バッファ（1KB）、<br>リングバッファロガー（512B） |
| - **ランタイムプール** (`FB_CONF_RUNTIME_HEAP_SIZE`) | 2,048 B | `execution_context`（Tier 2 ABI 152B）、WASM モジュールインスタンス状態、<br>`HotspotBitmap`（128B）、`JITCandidateBitmap`（128B）、`HistoryRing`（64B） |
| - **インタープリタ統合スタック** (`FB_CONF_INTERP_STACK_SIZE`) | 2,048 B | `OperandStack`（1KB）、`LocalStack`（768B）、`ControlFrame`（256B） |
| **2. システム静的変数 & OS スタック（プール外）** | **~3,500 B** | |
| - vMMIO ソフトウェア TLB キャッシュ配列 | 256 B | 32 エントリ $\times$ 8B（VPN + PTE）ダイレクトマップ高速 TLB |
| - ブレークポイント集合 / プロファイラバッファ | 320 B | ブレークポイント（8件 $\times$ 4B）＋ PC サンプル配列（64件 $\times$ 4B） |
| - ISR 割り込み通知リングバッファ | 64 B | 16 エントリ $\times$ 4B（原子キュー） |
| - ベアメタル OS システムスタック（Cortex-M MSP） | 2,048 B | 例外・割り込みハンドラ（ISR）実行用ハードウェアスタック |
| - グローバルポインタ・フラグ・TCBインデックス | ~500 B | カーネル・ディスパッチャ状態変数 |
| **RAM 合計使用量** | **~27,052 B** | **32KB SRAM に対し約 5,716 B（約 17.4%）の余裕を確保** |

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
| - **IPC RBAC 権限マトリックス** | 81 B | 9 $\times$ 9 ロール間通信可否 `bool` 配列（ビットパックなし、1セル1バイト、`constexpr`） |
| - **ログ辞書 (LogDictionary)** | ~1,500 B | ビルド時登録の `printf` フォーマット文字列テーブル（`{DictionaryBasedIPC}`） |
| - **vMMIO 静的領域定義テーブル** | ~128 B | 静的デバイス領域（FC=12等）のベース・サイズ・アクセス権限定義 |
| - **WASM ゲストバイナリ (Zero-Copy)** | (可変) | Flash 上のバイト列を直接パース・実行（RAM 展開不要） |
| **2. ハイパーバイザ機械語コード (`.text`)** | **~45〜55 KB** | |
| - インタープリタ実行エンジン（256命令） | ~18 KB | `[[clang::musttail]]` 直結末尾呼び出しハンドラ群 |
| - JIT コンパイラ & アセンブラコア | ~12 KB | Stencil コピー、リロケーション計算、逆順キュー処理 |
| - COOS カーネル & スケジューラ & IPC | ~8 KB | コルーチンスイッチ、同期ランデブー、所有権管理 |
| - WASM ローダー & デコーダ | ~7 KB | セクション解析、型チェック、CandidateBitmap スコアリング |
| - HAL / `libfireball` / GDB デバッガ | ~8 KB | UART/GPIO/Timer ドライバ、RSP パーサー、ゲストABIアダプタ |
| **ROM 合計使用量** | **~53〜63 KB** | **最小構成 Flash 96KB に対し約 34〜45% の空き容量で収容可能** |

---

## 4. 予算整合性と成立性総評

1. **実装規模の成立性**:
   - コード規模要求 `{Size_20KSLOC}` の上限は20 KSLOCである。pysim物理行数からの参考推定は約21.5〜23.4 KSLOCとなるため、SLOC定義でのC++実測まで予算達成を確定しない。
2. **RAM リソースの成立性**:
   - 統合物理プール（23,552 B）＋ システムスタック・静的変数（約 3,500 B）＝ 約 27,052 B。
   - 32KB SRAM の評価ターゲット環境において、約 5,716 B（約 17.4%）の余裕を確保する。
3. **ROM リソースの成立性**:
   - 不変テーブル（約 8.2 KB）とコード本体（約 45〜55 KB）の合計は約 53〜63 KB であり、最小構成の 96KB Flash に対して約 34〜45% の空き容量を残して安全に格納できる。
4. **JITCandidateBitmap 機能追加の影響**:
   - ロード時基本ブロック判定機能の追加による純増リソースは、ROM 128 バイト（`OpcodeBenefitTable`）および RAM 128 バイト（`JITCandidateBitmap`）のみであり、本予算計画の枠内に完全に収まる。
