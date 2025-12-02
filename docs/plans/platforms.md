# Fireball ターゲットハードウェアプラットフォーム

**Version:** 0.1.0
**Date:** 2025-11-30
**Author:** Takuya Matsunaga

---

## 概要

Fireball のターゲットプラットフォームは以下の通り：

1. **ARM Cortex-M シリーズ（第一優先）** - IoT・組み込みシステムの標準
2. **RISC-V（第二優先）** - オープン ISA、成長中のエコシステム
3. **x86/x64（開発・テスト用）** - 本番運用非対応

---

## 1. ターゲット: ARM Cortex-M

### 1.1 なぜ ARM Cortex-M か

| 利点 | 説明 |
|-----------|--------|
| **普及率** | 毎年数十億個が出荷される、組み込みシステムの事実上の標準 |
| **エコシステム** | 優秀なツールチェーン（ARM Compiler、GCC） |
| **メモリ範囲** | Cortex-M0（32KB）から Cortex-M7（2MB）まで対応 |
| **電力効率** | バッテリー駆動 IoT デバイス向けに設計 |
| **WASM 最適化** | パフォーマンスとコード密度のバランス最適 |

### 1.2 Cortex-M バリエーション

#### グレード 1: 第一優先対応

| MCU ファミリ | CPU | RAM | Flash | 用途 | 対応予定 |
|------------|-----|-----|-------|----------|--------|
| **STM32L0** | M0 | 8-20KB | 32-192KB | 超低消費電力、低コスト | ✅ Phase 2-3 |
| **STM32L4** | M4F | 128KB | 256KB | 中程度、FPU搭載 | ✅ Phase 2-3 |
| **STM32H7** | M7 | 512KB | 2MB | 高性能 | ✅ Phase 3 |
| **nRF52840** | M4F | 256KB | 1MB | Bluetooth + 高性能 | ✅ Phase 2-3 |
| **nRF5340** | M33 | 512KB | 1MB | デュアルコア、最新アーキ | ✅ Phase 3 |

#### グレード 2: 第二優先対応

| MCU ファミリ | CPU | RAM | Flash | 用途 |
|------------|-----|-----|-------|--------|
| **STM32F1** | M3 | 64KB | 128KB | レガシー、低優先度 |
| **ATSAMD21** | M0 | 32KB | 256KB | Arduino エコシステム |
| **NXP LPC1769** | M3 | 64KB | 256KB | 産業用応用 |

### 1.3 Zephyr RTOS との統合

Fireball は Zephyr RTOS のボード定義を活用：

```
Fireball レイヤー
    ↓
Zephyr RTOS（ボード定義、デバイスツリー）
    ↓
マイクロコントローラ（STM32、nRF など）
```

メリット：Zephyr のボード定義を再利用し、HAL の重複実装を回避。

---

## 2. セカンダリターゲット: RISC-V

### 2.1 RISC-V を選ぶ理由

| 要素 | 利点 |
|--------|-----------|
| **オープン ISA** | ライセンス不要、完全な透明性 |
| **シンプルな設計** | 最小限の ISA で WASM インタプリタ/JIT 実装が簡潔 |
| **成長中のエコシステム** | SoC 選択肢が増加中 |
| **差別化要因** | オープン ISA の採用でFireball を差別化 |

### 2.2 推奨 RISC-V プラットフォーム

#### T-Head

推奨 SoC：

| SoC | コア | RAM | Flash | 説明 |
|-----|------|-----|-------|-------|
| **TH1100** | C906 (32-bit) | 4-8MB | 16MB | 組み込み用途の最適ターゲット |
| **TH1520** | C910 (64-bit) | 512MB | 32MB | 高性能用途 |

**推奨：** まず **TH1100**（組み込み）から開始、その後 **TH1520**（高性能）へ。

#### WCH（WinChip Electronics）

推奨 SoC：

| SoC | コア | RAM | Flash | 消費電力 | 説明 |
|-----|------|-----|-------|-------|-------|
| **CH32V203** | RV32IMC | 20KB | 64KB | <5mW | 超低消費電力、エントリーレベル |
| **CH32V307** | RV32IMC | 64KB | 256KB | <10mW | 中程度 |

**推奨：** **CH32V203** を主要ターゲット（消費電力とパフォーマンスのバランス最適）。

#### SiFive

| SoC | コア | RAM | 説明 |
|-----|------|-----|-------|
| **FE310-G002** | E31 (32-bit) | 16KB | RISC-V パイオニア、成熟したエコシステム |

**状態：** 成熟しているが高コスト、先進機能検討時のみ。

### 2.3 RISC-V インタプリタ実装

RISC-V は ISA がシンプルなため、WASM インタプリタ実装も簡潔：

```
x86 インタプリタ: 複雑（多数のレジスタ、モード）
ARM Cortex-M インタプリタ: 中程度の複雑さ
RISC-V インタプリタ: シンプル、明快な設計
```

**JIT コンパイル：** RISC-V は ISA がシンプルなため、コード生成も効率的。

---

## 3. 開発・テスト: x86-64

### 3.1 用途

x86-64 は以下の用途 **のみ** に使用：
- COOS コア機能のユニットテスト
- パフォーマンス分析、プロファイリング
- ハードウェア実装前の CI/CD テスト
- 本番運用には使用しない

### 3.2 ビルド設定

```cmake
# プラットフォーム選択（CMakeLists.txt）
set(FIREBALL_PLATFORM "arm-cortex-m4" CACHE STRING "ターゲットプラットフォーム")
# 選択肢: arm-cortex-m0, arm-cortex-m4, riscv32, riscv64, x86-64
```

---

## 4. プラットフォーム固有の実装戦略

### 4.1 共有抽象層

```
Fireball コア
  ├─ COOS カーネル（アーキテクチャ非依存）
  ├─ vSoC ランタイム（インタプリタ/JIT、ほぼ非依存）
  └─ IPC ルータ（完全非依存）

HAL（ハードウェア抽象化レイヤー）
  ├─ GPIO ドライバ
  ├─ UART ドライバ
  ├─ I2C ドライバ
  ├─ SPI ドライバ
  ├─ Timer ドライバ
  └─ 割り込みハンドリング

プラットフォーム固有の実装
  ├─ ARM Cortex-M: STM32 SDK、Nordic SDK など
  └─ RISC-V: T-Head SDK、WCH SDK、SiFive ツール など
```

### 4.2 ボード定義ディレクトリ構造

```
fireball/
  ├─ platform/
  │   ├─ cortex-m/
  │   │   ├─ cortex-m0.cmake
  │   │   ├─ cortex-m4.cmake
  │   │   └─ cortex-m7.cmake
  │   ├─ riscv/
  │   │   ├─ rv32.cmake
  │   │   ├─ rv64.cmake
  │   │   ├─ thead-th1100.cmake
  │   │   └─ wch-ch32v203.cmake
  │   ├─ x86-64/
  │   │   └─ x86-64-posix.cmake
  │   ├─ boards/
  │   │   ├─ stm32l476g-disco/
  │   │   ├─ nrf52840-dk/
  │   │   └─ wch-ch32v203-evt/
```

---

## 5. マイグレーション計画

### Phase 2: ARM Cortex-M4 ベースライン

**主要:** STM32L4（STM32L476）or nRF52840
- Zephyr 成熟対応
- 機能・コストのバランス最適
- RAM 128-256 KB、Flash 256 KB-1 MB

### Phase 3: 超低消費電力対応

**追加:** STM32L0 or nRF52（低電力バリエーション）
- 4 KB RAM 予算での COOS 検証
- 小コードキャッシュでのインタプリタ効率測定

### Phase 4: RISC-V 対応

**追加:** T-Head TH1100 or WCH CH32V203
- RISC-V インタプリタ実装完成
- ARM vs RISC-V クロスプラットフォームテスト
- アーキテクチャ非依存性の実証

### Phase 5: 高性能バリエーション

**追加:** STM32H7、nRF5340、T-Head TH1520
- マルチコア対応（nRF5340）
- GPU/DSP アクセラレータ（STM32H7）
- 大メモリシステム（TH1520）

---

## 6. ビルド・テストマトリックス

### 6.1 CI/CD プラットフォーム対応

| ターゲット | CPU | RAM | Flash | CI テスト | 実装予定 |
|--------|-----|-----|-------|---------|---------|
| x86-64 (POSIX) | - | - | - | ✅ 全コミット | Phase 2+ |
| STM32L476 | M4F | 128KB | 256KB | エミュレーション | ✅ Phase 2 |
| nRF52840 | M4F | 256KB | 1MB | エミュレーション | ✅ Phase 2 |
| WCH CH32V203 | RV32IMC | 20KB | 64KB | エミュレーション | ✅ Phase 3 |
| T-Head TH1100 | RV32IMC | 4-8MB | 16MB | エミュレーション | ✅ Phase 3 |

### 6.2 エミュレーションツール

- **ARM Cortex-M:** QEMU ARM（優秀なサポート）
- **RISC-V:** QEMU RISC-V（成熟、TH1100・CH32V203 対応）
- **x86-64:** ネイティブ POSIX（Linux、macOS、Windows）

---

## 7. 実装チェックリスト

### ARM Cortex-M 対応
- [ ] STM32L4 HAL アダプター設定
- [ ] nRF52840 ボード定義追加
- [ ] Zephyr デバイスツリー統合
- [ ] STM32L476 実機での COOS テスト
- [ ] メモリ予算検証（最小 128 KB RAM）
- [ ] 実機でのパフォーマンス測定

### RISC-V 対応
- [ ] T-Head or WCH 選択決定
- [ ] RISC-V インタプリタバックエンド実装
- [ ] ボード定義作成（TH1100、CH32V203）
- [ ] RISC-V 実機での COOS テスト
- [ ] ARM vs RISC-V パフォーマンス比較
- [ ] ツールチェーン検証（GCC、LLVM）

### CI/CD
- [ ] QEMU ベース CI（ARM）構築
- [ ] QEMU ベース CI（RISC-V）構築
- [ ] プラットフォーム固有テスト追加
- [ ] 全対応ターゲットのビルド自動化

