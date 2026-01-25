# ROM/RAM見積りテンプレ（コンポーネント別）

## 1. 目的
設計ドキュメントからROM/RAMの概算レンジを再評価可能にするための、**コンポーネント別テンプレ**を定義する。

## 2. 共通フォーマット
各コンポーネントで以下の形式に統一する。

### 2.1 ROM内訳
- `ROM_text`: 実行コード（.text）
- `ROM_rodata`: 定数/テーブル/辞書（.rodata）
- `ROM_static`: 静的初期化データ（.data）

### 2.2 RAM内訳
- `RAM_static`: 静的データ（.data + .bss）
- `RAM_stack`: スタック（タスク/割り込み/内部スタック）
- `RAM_heap`: ヒープ（動的バッファ/辞書/キュー）
- `RAM_guest`: ゲストリニアメモリ
- `RAM_cache`: JITキャッシュ/履歴バッファ

### 2.3 入力パラメータ
- `N_modules`, `N_funcs`, `N_exports`, `N_services`, `N_tasks`
- `Stack_bytes`, `Control_stack_bytes`, `Guest_linear_bytes`
- `JIT_cache_bytes`（interp-onlyは0）

## 3. コンポーネント別テンプレ

### 3.1 vSoC
- ROM: `ROM_text(vsoc)` + `ROM_rodata(vsoc)` + `ROM_static(vsoc)`
- RAM: `RAM_static(vsoc)` + `RAM_heap(vsoc)` + `RAM_cache(vsoc)`
- 係数例:
  - `RAM_cache(vsoc)` = `JIT_cache_bytes`

### 3.2 Interpreter
- ROM: `ROM_text(interp)` + `ROM_rodata(interp)` + `ROM_static(interp)`
- RAM: `RAM_static(interp)` + `RAM_stack(interp)`
- 係数例:
  - `RAM_stack(interp)` = `Stack_bytes` + `Control_stack_bytes`

### 3.3 Loader
- ROM: `ROM_text(loader)` + `ROM_rodata(loader)` + `ROM_static(loader)`
- RAM: `RAM_static(loader)` + `RAM_heap(loader)`
- 係数例:
  - `RAM_heap(loader)` = `N_modules * module_view_t + N_modules * section_index_t + N_exports * dict_entry`

### 3.4 COOS
- ROM: `ROM_text(coos)` + `ROM_rodata(coos)` + `ROM_static(coos)`
- RAM: `RAM_static(coos)` + `RAM_stack(coos)` + `RAM_heap(coos)`
- 係数例:
  - `RAM_stack(coos)` = `N_tasks * task_stack_bytes`

### 3.5 IPC Router
- ROM: `ROM_text(ipc)` + `ROM_rodata(ipc)` + `ROM_static(ipc)`
- RAM: `RAM_static(ipc)`
- 係数例:
  - `RAM_static(ipc)` = `N_services * registry_entry_t`

### 3.6 Debugger
- ROM: `ROM_text(debug)` + `ROM_rodata(debug)` + `ROM_static(debug)`
- RAM: `RAM_static(debug)` + `RAM_heap(debug)`
- 係数例:
  - `RAM_heap(debug)` = `debug_buffer_bytes`

### 3.7 vMMIO
- ROM: `ROM_text(vmmio)` + `ROM_rodata(vmmio)` + `ROM_static(vmmio)`
- RAM: `RAM_static(vmmio)`

### 3.8 Services/HAL/Logging
- ROM: `ROM_text(x)` + `ROM_rodata(x)` + `ROM_static(x)`
- RAM: `RAM_static(x)` + `RAM_heap(x)`

### 3.9 Standard Libraries (libc/libc++)
- ROM: `ROM_text(stdlib)` + `ROM_rodata(stdlib)` + `ROM_static(stdlib)`
- RAM: `RAM_static(stdlib)` + `RAM_heap(stdlib)`

## 4. 合算テンプレ
```
ROM_total = Σ ROM_text(c) + Σ ROM_rodata(c) + Σ ROM_static(c)
RAM_total = Σ RAM_static(c) + Σ RAM_stack(c) + Σ RAM_heap(c) + RAM_guest + RAM_cache
```

## 5. 評価基準
- 制約: **RAM 32KB / ROM 96KB**
- 余裕率 = `(制約 - 合算値) / 制約`

## 6. 参照
- [plans/rom_ram_estimation_method.md](plans/rom_ram_estimation_method.md:1)
