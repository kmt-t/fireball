# ROM/RAM概算レンジの計算手順（実測なし）

## 1. 目的
設計ドキュメントの構成要素から、FireballのROM/RAMフットプリントの**概算レンジ**を再評価可能な形で算出する。実測は行わず、構成要素とスケーリングパラメータに基づく推定とする。

## 2. 前提条件（比較条件）
- WAMR/Fireballともに **interpreter-only**。
- AOT/JIT無効、WASI無効、浮動小数点無効。
- ROM/RAM制約は **32KB/96KB** に統一して評価する。

## 3. 変数定義（再評価のための入力）
### 3.1 構成パラメータ
- `N_modules`: 同時ロードされるWASMモジュール数
- `N_funcs`: 1モジュール当たりの関数数（平均）
- `N_exports`: 1モジュール当たりのエクスポート数（平均）
- `N_services`: IPC登録サービス数
- `N_tasks`: COOSタスク数
- `Stack_bytes`: Interpreterオペランドスタックサイズ
- `Control_stack_bytes`: Interpreter制御スタックサイズ
- `Guest_linear_bytes`: ゲストリニアメモリサイズ
- `JIT_cache_bytes`: JITキャッシュサイズ（interp-only時は0）

### 3.2 ROM内訳カテゴリ
- `ROM_text`: 実行コード（.text）
- `ROM_rodata`: 定数テーブル/辞書/命令ハンドラ表（.rodata）
- `ROM_static`: 静的初期化データ（.data）

### 3.3 RAM内訳カテゴリ
- `RAM_static`: 静的データ（.data + .bss）
- `RAM_stack`: スタック（COOSタスク/割り込み/起動スタック）
- `RAM_heap`: ヒープ（vSoC/サブシステム/サービス/デバッガ）
- `RAM_guest`: ゲストリニアメモリ
- `RAM_cache`: JITキャッシュ/履歴バッファ

## 4. 概算テンプレ（コンポーネント別）
**ROM/RAMの寄与をコンポーネント単位で積み上げる**。各コンポーネントに最小・想定の2レンジを設定し、最後に合算する。

### 4.1 vSoC
- ROM: `ROM_text(vsoc)` + `ROM_rodata(vsoc)`
- RAM: `RAM_static(vsoc)` + `RAM_heap(vsoc)` + `RAM_cache(vsoc)`

### 4.2 Interpreter
- ROM: `ROM_text(interp)` + `ROM_rodata(interp)`
- RAM: `RAM_static(interp)` + `RAM_stack(interp)`
  - `RAM_stack(interp)` = `Stack_bytes` + `Control_stack_bytes`

### 4.3 Loader
- ROM: `ROM_text(loader)` + `ROM_rodata(loader)`
- RAM: `RAM_static(loader)` + `RAM_heap(loader)`
  - `RAM_heap(loader)` は `N_modules` と `N_funcs` に比例（module_view_t, section_index_t, 辞書）

### 4.4 COOS
- ROM: `ROM_text(coos)` + `ROM_rodata(coos)`
- RAM: `RAM_static(coos)` + `RAM_stack(coos)` + `RAM_heap(coos)`
  - `RAM_stack(coos)` は `N_tasks` に比例

### 4.5 IPC Router
- ROM: `ROM_text(ipc)` + `ROM_rodata(ipc)`
- RAM: `RAM_static(ipc)`
  - `RAM_static(ipc)` は `N_services` に比例（registry_entry_t 等）

### 4.6 Debugger/vMMIO/Services/HAL/Logging
- ROM: 各コンポーネントの `ROM_text` + `ROM_rodata`
- RAM: `RAM_static` + `RAM_heap`（必要時のみ）

### 4.7 Standard Libraries (libc/libc++)
- ROM: `ROM_text(stdlib)` + `ROM_rodata(stdlib)`
- RAM: `RAM_static(stdlib)` + `RAM_heap(stdlib)`
- 備考: new/delete, malloc, printf, string/vector 等のオーバーヘッド。

## 5. 合算ルール
### 5.1 ROM合算
`ROM_total = Σ ROM_text(c) + Σ ROM_rodata(c) + Σ ROM_static(c)`

### 5.2 RAM合算
`RAM_total = Σ RAM_static(c) + Σ RAM_stack(c) + Σ RAM_heap(c) + RAM_guest + RAM_cache`

### 5.3 レンジの扱い
- **最小構成**: 低い構成パラメータを採用（例: `N_modules=1`, `N_tasks=2`）
- **想定構成**: 運用で必要な上限近傍の値を採用
- 各構成で `ROM_total` と `RAM_total` を算出し、**32KB/96KB** の制約に対する余裕率を計算

## 6. 再評価の手順
1. 入力パラメータ（`N_modules` など）を更新する
2. コンポーネント別のROM/RAM寄与を更新する
3. 合算式で `ROM_total`/`RAM_total` を再計算する
4. 制約レンジに対する余裕率を再評価する

## 7. 参照ドキュメント
- [docs/orders/concept/benchmark_plan.md](docs/orders/concept/benchmark_plan.md:1)
- [docs/orders/requires/list.md](docs/orders/requires/list.md:1)
- [docs/orders/components/vsoc.md](docs/orders/components/vsoc.md:1)
- [docs/orders/components/interpreter.md](docs/orders/components/interpreter.md:1)
- [docs/orders/components/loader.md](docs/orders/components/loader.md:1)
- [docs/orders/components/coos.md](docs/orders/components/coos.md:1)
- [docs/orders/components/router.md](docs/orders/components/router.md:1)
