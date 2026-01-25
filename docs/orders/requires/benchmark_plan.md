# vSoC ベンチマーク計画 (改定案)

## 1. 背景と仮説
Fireball vSoCの設計が、既存のWAMR（WebAssembly Micro Runtime）に対して、特にインタープリタ性能とメモリ効率の面で優位性を持つことを証明する必要がある。

## 2. 理論的裏付け

### 2.1 評価指標 (Metrics)
1.  **実行性能 (Performance)**: CoreMark等の計算タスクの完了時間。
2.  **メモリフットプリント (RAM)**: ピーク時のメモリ消費量。
    - 内訳: `RAM_static`, `RAM_stack`, `RAM_heap`, `RAM_guest`, `RAM_cache`
3.  **バイナリサイズ (ROM)**: ランタイム自体のコードサイズ。
    - 内訳: `ROM_text`, `ROM_rodata`, `ROM_static`

### 2.2 理論モデル: 比較構成

```mermaid
graph LR
    WASM[WASM Binary] --> Fireball[Fireball vSoC]
    WASM --> WAMR[WAMR Interpreter]
    Fireball --> Metrics[Metrics: Perf/RAM/ROM]
    WAMR --> Metrics
```

## 3. 検証とシミュレーション

### 3.1 ベンチマーク環境
- **ホスト**: Linux (x86_64) / Cortex-M33 (QEMU/Real Hardware)
- **比較条件 (固定)**:
    - 実行系: interpreter-only
    - AOT/JIT: 無効
    - WASI: 無効
    - 浮動小数点: 無効
- **制約ターゲット**: RAM 32KB / ROM 96KB
- **タスク**: 算術演算ループ、再帰呼び出し、メモリアクセス。

### 3.2 ROM/RAM概算手法 (実測前評価)
実測が困難な設計フェーズにおいて、以下の係数を用いた概算レンジで評価を行う。

#### 標準ライブラリ (libc/libc++) ベースライン
- `ROM_stdlib`: 10.0 - 20.0 KB (最小構成)
- `RAM_stdlib`: 2.0 - 4.0 KB (静的+初期ヒープ)

#### ROM概算係数 (1 KLOC当たり)
- `ROM_text`: 2.0 KB
- `ROM_rodata`: 0.5 KB
- `ROM_static`: 0.2 KB

#### RAM構成要素
- `RAM_stack`: `Stack_bytes` + `Control_stack_bytes` + `N_tasks * task_stack_bytes`
- `RAM_heap`: `N_modules * (module_view_t + section_index_t) + N_exports * dict_entry` 等

## 4. 設計完了チェックリスト（網羅性確認）

- [x] 解決したい課題と仮説が論理的に結びついているか
- [x] 評価指標が具体的かつ定量的か
- [x] 比較対象（WAMR）との条件が公平に設定されているか
- [x] ROM/RAMの内訳カテゴリが定義されているか

## 5. 設計へのフィードバック
- **反映先**: `{vSoC_Benchmark_Goal}`
- **反映内容**: 概算レンジが制約（RAM 32KB / ROM 96KB）を超える場合、Loaderの辞書構造やInterpreterのスタック配置を再設計する。

## 6. 参考文献・リソース
- **WAMR**: [GitHub](https://github.com/bytecodealliance/wasm-micro-runtime)
- **CoreMark**: [EEMBC](https://www.eembc.org/coremark/)
