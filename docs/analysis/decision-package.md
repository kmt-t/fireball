# Fireball Decision Package

**Status**: 🚧 企画GO / 実装Conditional GO
**Target**: Antigravity Technical Lead

## 1. 市場・競合 (vs WAMR)

**Target**: Ultra-Constrained (32-128KB RAM). e.g., STM32L0/F4.
**Winner**: Fireball (80KB) vs WAMR (104KB). **24KB (25%) Margin**.

| Feature | WAMR | Fireball | Note |
|---|---|---|---|
| **Memory** | 104KB (Risk) | 80KB (Safe) | 6-partition isolation guarantees limit |
| **Fault** | Global Heap | Isolated | P3(System) failure != Guest failure |
| **Debug** | High Overhead | Low Overhead | **FDA/DO-178C Compliance Key** |
| **Speed** | Fast (AOT) | Slow (Interp) | Phase 4 JIT で改善予定 |


## 3. Go/No-Go Criteria

-   **Phase 0 (Kernel)**: ROM < 15KB, RAM < 8KB
-   **Phase 1 (Interp)**: ROM < 33KB, RAM < 30KB
-   **Phase 3 (System)**: ROM < 85KB, RAM < 80KB
-   **Condition**: 各フェーズで実測値が超過したら **STOP/Redesign**

## 4. Antigravity への質問

1.  **Memory Fail**: 目標未達なら？ (H7へ移行 / 機能削減 / WAMRへ転向)
2.  **Verification**: Formal Proof Budget? (Full / Semi / None)
3.  **JIT**: Optional or Mandatory?
