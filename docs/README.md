# Fireball

**Status**: 🚧 企画GO / 実装Conditional GO (メモリ制約注視)
**Target**: STM32F401 (RAM 96KB)

## 必読ドキュメント

1. **意思決定**: [analysis/decision-package.md](analysis/decision-package.md) (市場優位性、Go/No-Go基準)
2. **仕様**: [specifications/overview.md](specifications/overview.md) (システム構成)
3. **計画**: [plans/phases.md](plans/phases.md) (実装計画)
4. **規約**: [CODING_STYLE.md](CODING_STYLE.md) (コーディング規約)
6. **ADR**: [adr/list-adr.md](adr/list-adr.md)] (仕様から分離したADR)

## メモリアーキテクチャ (Target: 80KB)

```
Total: 63-80KB ✅ (Target < 80KB, Margin 16KB)
├─ P1: COOS Kernel    (5.7KB)
├─ P2: WASM Runtime   (4.7KB)
├─ P3: Subsystems     (8.8KB)
├─ P4: Services       (0.5KB)
├─ P5: Guest Module   (32-48KB)
├─ P6: System Reserve (2-4KB)
└─ Stacks             (8KB)
```

## ディレクトリ構成

- `docs/analysis/`: 意思決定資料 (decision-package.md)
- `docs/specifications/`: 技術仕様書 (overview, protocols, HAL)
- `docs/component-design/`: 詳細設計 (JIT, Debugger, Plugin)
- `docs/plans/`: 計画・予算
- `docs/adr/`: ADR

