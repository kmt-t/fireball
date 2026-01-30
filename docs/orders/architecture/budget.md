# Fireball リソース予算

システム全体のメモリおよびSLOC（ソースコード行数）の予算配分を管理する。 `{Resource_Estimation_Model}` `{Size_15KLOC}`

## 1. メモリ予算

### 1.1 ヒープパーティション

| パーティション名 | 目的 | 最小構成 (32KB) | 想定構成 (64KB) | 備考 |
|:---|:---|---:|---:|:---|
| ネイティブヒープ | COOS (スケジューラ, CSP, 共有メモリ) | 4.0 KB | 6.0 KB | タスク数 < 10 |
| vSoCヒープ | JITメタデータ, WASMコンテキスト | 2.0 KB | 4.0 KB | |
| サブシステムヒープ | IPCルータ, HAL | 4.0 KB | 6.0 KB | |
| JITコードキャッシュ | Active/Old ダブルバッファ | 4.0 KB | 4.0 KB | 2KB x 2 |
| WASMリニアメモリ | ゲストアプリ・サービス作業領域 | 16.0 KB | 40.0 KB | スケーラブル |
| **合計** | | **30.0 KB** | **60.0 KB** | |

### 1.2 主要構造体サイズ見積もり

| 構造体 | コンポーネント | 推定サイズ | 備考 |
|:---|:---|---:|:---|
| `task` (TCB) | scheduler | ~48 B | id, state, coro_handle, heap_base/size, timeout_tick, next |
| `callback_task` | scheduler | ~24 B | id, func, interval_ticks, next_run_tick |
| `channel` | coos (CSP) | ~16 B | buffer (ptr), sender/receiver wait queue |
| JIT Entry | jit_entry_index | ~12 B | wasm_offset, native_ptr, flags |

### 1.3 ROM 予算

システム全体を **64KB (ベスト)** または **96KB (最小構成)** 以内に収める。

| コンポーネント | 推定サイズ | 備考 |
|:---|---:|:---|
| COOS (scheduler, CSP, mem) | ~4 KB | カーネル基盤 |
| vSoC (インタープリタ) | ~12 KB | テーブルディスパッチ、命令実装 |
| vSoC (JIT Copy-and-Patch) | ~8 KB | テンプレート生成ロジック |
| JIT テンプレート (バイナリ) | ~8 KB | 単一アーキテクチャ想定 |
| IPC Router | ~2 KB | URI解決、ハンドル管理 |
| HAL | ~4 KB | ドライバ抽象化 (ポート依存) |
| Allocator | ~1 KB | ヒープ管理 |
| Utils / Glue | ~2 KB | 共通ユーティリティ |
| C++ Runtime (minimal) | ~8 KB | コルーチン等、`-fno-exceptions -fno-rtti` 適用 |
| **合計** | **~49 KB** | 64KB 達成可能 (単一アーキテクチャ) |

> [!NOTE]
> ARM/RISC-V 両対応の場合、JITテンプレートが +8KB 程度増加し、合計 ~60KB 前後となる。

## 2. SLOC 予算

システム全体を **15,000 LOC** 以内に収める。 `{Size_15KLOC}`

| コンポーネント | 予算 (LOC) | 備考 |
|:---|---:|:---|
| COOS (scheduler, CSP, mem) | 1,500 | カーネル基盤 |
| vSoC (interpreter, JIT, vMMIO) | 6,000 | 実行エンジンが最大 |
| IPC Router | 800 | URI解決, ハンドル管理 |
| HAL | 1,200 | ドライバ抽象化 (ポート依存) |
| Allocator | 500 | ヒープパーティション管理 |
| Utils | 500 | 共通ユーティリティ |
| Config / Glue | 500 | システム設定, ブート |
| **予備** | 4,000 | 拡張・調整用 |
| **合計** | **15,000** | |

## 3. 設計完了チェックリスト

- [x] メモリ予算が最小構成 (32KB) で収まるか
- [x] SLOC予算が 15KLOC 以内か
- [ ] 各コンポーネントの実装後に実測値を更新する
