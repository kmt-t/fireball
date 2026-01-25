# ROM/RAM概算レンジ（20KSLOC前提：Fireball 15K + dlmalloc 5K）

## 1. 概算係数（KLOCベース）
以下は**概算用の係数**。

### 1.1 ROM係数
- **ROM_text換算**: 1 KLOC = **2.0 KB**
- **ROM_rodata換算**: 1 KLOC = **0.5 KB**
- **ROM_static換算**: 1 KLOC = **0.2 KB**

## 2. 20KSLOCの換算結果
- **ROM_text**: 20 * 2.0 KB = **40.0 KB**
- **ROM_rodata**: 20 * 0.5 KB = **10.0 KB**
- **ROM_static**: 20 * 0.2 KB = **4.0 KB**
- **ROM合計（KLOC由来）**: **54.0 KB**

## 3. 構成別ベースライン合算（ROM 96KB制約に対する評価）

| 項目 | 概算値 | 備考 |
| --- | --- | --- |
| Fireball + dlmalloc (20KSLOC) | 54.0 KB | text/rodata/static 合計 |
| libc/libc++ (ベースライン) | 15.0 KB | 10-20KBの中間値 |
| **合計 (ROM_total)** | **69.0 KB** | **制約 96KB に対して 27KB (約28%) の余裕** |

## 4. RAM見積りへの影響
- **dlmalloc管理領域**: 
    - 静的管理領域: 約 1-2 KB
    - ヒープ断片化/オーバーヘッド: 確保量の約 10-15% を見込む必要あり。
- **20KSLOC由来の静的データ**: 4.0 KB (ROM_staticと重複)

## 5. RAM内訳の概算（最小構成ターゲット：32KB）

### 5.1 コンフィグマクロに基づく固定割り当て (大物)
| 項目 | マクロ名 | 最小構成値 | 備考 |
| --- | --- | --- | --- |
| **Kernel Heap** | `FB_CONF_KERNEL_HEAP_SIZE` | 4.0 KB | COOSカーネル用 |
| **Runtime Heap** | `FB_CONF_RUNTIME_HEAP_SIZE` | 2.0 KB | WASMランタイム用 |
| **Subsystem Heap** | `FB_CONF_SUBSYSTEM_HEAP_SIZE` | 2.0 KB | その他サブシステム |
| **Guest RAM** | `FB_CONF_GUEST_RAM_SIZE` | 16.0 KB | WASMリニアメモリ (最小構成) |
| **JIT Cache** | `FB_CONF_JIT_CACHE_SIZE` | 4.0 KB | Interp-only時は 0KB |
| **HAL Buffer** | `FB_CONF_HAL_BUFFER_SIZE` * `MAX_BUFFERS` | 4.0 KB | 1KB * 4 |
| **Log Buffer** | `FB_CONF_LOG_BUFFER_SIZE` | 0.5 KB | |
| **固定割当合計** | - | **28.5 KB** | (JIT Cache 0KB時) |

### 5.2 合算評価 (最小構成 32KB ターゲット)
上記の固定割り当てに加え、静的データやスタックを合算すると以下の通り。

| カテゴリ | 概算値 | 根拠・内訳 |
| --- | --- | --- |
| **Fixed (Config)** | 28.5 KB | 5.1項の合計 (JIT=0, Guest=16KB想定) |
| **RAM_static** | 4.0 KB | 20KSLOC由来 (.data + .bss) |
| **RAM_stdlib** | 3.0 KB | libc/libc++ 静的+初期管理領域 |
| **RAM_stack** | 6.0 KB | COOS/Interp/System |
| **合計 (RAM_total)** | **41.5 KB** | **制約 32KB に対して 9.5KB 超過** |

## 6. 結論
dlmallocを含めた20KSLOC構成でも、ROM 96KBに対しては約28%の余裕がありますが、**RAM 32KBに対しては9.5KB 超過**です。

### RAM制約達成のための設計指針
1. **WASMリニアメモリの仮想化**: 64KB単位ではなく、必要な分だけ物理RAMを割り当てるページ管理が必須。
2. **Loaderのストリーム処理**: WASMバイナリ全体をRAMに載せず、セクションごとに処理して破棄する。
3. **スタックの共有**: COOSタスク間でスタックを極力小さく保ち、深い再帰は制限する。

## 6. 参照
- [plans/rom_ram_estimation_method.md](plans/rom_ram_estimation_method.md:1)
- [docs/orders/requires/benchmark_plan.md](docs/orders/requires/benchmark_plan.md:1)
