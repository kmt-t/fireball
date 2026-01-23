# アーキテクチャ設計書：Fireball システム概要

## 1. アーキテクチャコンセプト

Fireballは、極小リソース環境での柔軟性と高性能を両立させるため、以下の設計思想を採用する。

- **クリーンアーキテクチャとDI**: URIベースの抽象化とIPCルータによる依存性の注入により、コンポーネント間の結合度を下げ、移植性を向上させる。 `{CleanArchitecture}` `{URIAbstraction}` `{IPCDI}`
- **協調型マルチタスク (COOS)**: C++20コルーチンを活用し、スタックレスで低オーバーヘッドなタスク切り替えを実現する。 `{UseCpp20Coroutine}` `{LowOverheadSwitch}`
- **高速JIT (Copy-and-Patch)**: コンパイルレイテンシを最小化し、小規模なコードキャッシュで効率的に動作するJITアーキテクチャを採用する。 `{LowLatencyJIT}` `{SimpleJITArchitecture}`
- **静的構成**: システムパラメータや依存関係の多くをコンパイル時に決定し、実行時の動的メモリ確保や探索コストを排除する。 `{StaticDI}` `{ConfigurableSystem}`

## 2. 静的構造 (Static Model)

### 2.1 レイヤー構成

| レイヤー | 構成要素 | 説明 |
| :--- | :--- | :--- |
| **ゲストアプリケーション** | WASMバイナリ | ユーザー提供のWASMバイナリアプリケーション。 |
| **サービス** | WASMプラグイン | システム機能を拡張するユーザー提供のWASMサービス。 |
| **vSoC** | インタープリタ, JIT, デバッガ, vMMIO | WASM実行環境と仮想ハードウェア抽象化を提供。 |
| **COOSカーネル** | スケジューラ, CSP, メモリ | 協調型マルチタスクと安全な通信の基盤。 |
| **サブシステム** | IPCルータ, HAL, ロギング | システムの共通機能とハードウェア抽象化層。 |
| **デバイスドライバ** | 各種ドライバ | 物理デバイス制御（UART, GPIO等）。 |
| **ハードウェア** | CPU, 周辺機器 | 物理基盤（ARM Cortex-M, RISC-V等）。 |

### 2.2 コンポーネント俯瞰図

矢印は**仕様の依存関係 (Dependency)** を示す。`{CleanArchitecture}` `{IoC}`

```mermaid
graph TD
    subgraph Guest_Layer [Guest Layer]
        App[Guest Application]
        Svc[WASM Services]
    end

    subgraph Runtime_Layer [Runtime Layer]
        vSoC[vSoC / WASM Runtime]
    end

    subgraph Kernel_Layer [Kernel Layer]
        COOS[COOS Kernel]
        IPCR[IPC Router]
    end

    subgraph Subsystem_Layer [Subsystem Layer]
        HAL[HAL Implementation]
        Log[Logging Implementation]
    end

    subgraph Hardware_Layer [Hardware Layer]
        HW[Hardware]
    end

    %% 依存性の方向 (Implementation -> Interface/Specification)
    App --> vSoC
    Svc --> vSoC
    vSoC --> IPCR
    vSoC --> COOS
    HAL --> IPCR
    Log --> IPCR
    IPCR --> COOS
    HAL --> HW
```

#### 依存性ルール
- **内側への依存**: 上位レイヤー（Kernel, IPCR）は下位レイヤー（HAL, Driver）の実装に依存してはならない。下位レイヤーが上位レイヤーの定義したインターフェイスを実装することで、依存性の逆転 (IoC) を実現する。
- **URIベースの疎結合**: コンポーネント間の具体的な依存は `fireball://` URI を介したルックアップにより解決され、コンパイル時の静的DIによって結合される。

## 3. 動的構造 (Dynamic Model)

### 3.1 主要シーケンス

#### 起動およびタスク登録
```mermaid
sequenceDiagram
    participant Boot as Bootloader
    participant IPCR as IPC Router
    participant HAL as HAL
    participant COOS as COOS Kernel
    
    Boot->>HAL: Initialize Hardware
    Boot->>IPCR: Register System Services (URI)
    Boot->>COOS: Initialize Scheduler
    COOS->>COOS: Start Idle Task
```

#### IPC通信 (URIベース)
```mermaid
sequenceDiagram
    participant App as Guest App
    participant vSoC as vSoC
    participant IPCR as IPC Router
    participant Svc as Target Service
    
    App->>vSoC: System Call (URI)
    vSoC->>IPCR: Lookup(URI)
    IPCR-->>vSoC: Handle (Pointer)
    vSoC->>Svc: Send Message (Zero-copy)
    Svc-->>vSoC: Reply
    vSoC-->>App: Return
```

## 4. 設計判断 (ADR)

- **決定事項**: `{Challenge_ApproximateYield}`
  - **背景**: タイマ割り込みによる厳密なプリエンプションはオーバーヘッドが大きい。
  - **選択肢と評価**: 
    - 案1: タイマ割り込みによるプリエンプション（高精度だが重い）
    - 案2: トレース数ベースの概算Yield（低オーバーヘッドだが実行時間が逸脱する可能性あり）
  - **結論**: 案2を採用。実行時間の逸脱はログで検知し、設計にフィードバックする。

- **決定事項**: `{Challenge_InterruptSafety}`
  - **背景**: 割り込みハンドラによる実行コンテキスト破壊の防止。
  - **結論**: Poll方式（`co_yield` 後のフラグチェック）を基本とする。将来的にJITスキャンやスタック分離を検討。

- **決定事項**: `{Challenge_JITCacheEfficiency}`
  - **背景**: RAM 64KB制約下での効率的なキャッシュ管理。
  - **結論**: Active/Oldダブルバッファを採用し、`co_yield` 時に一括してホットスポット判定を行う。

## 5. 設計完了チェックリスト（網羅性確認）

- [x] システムレイヤー構成が定義され、各レイヤーの責務が明確か
- [x] コンポーネント間の依存方向がアーキテクチャ原則に従っているか
- [x] 主要な動的振る舞い（シーケンス）が定義されているか
- [x] 重要な設計上のトレードオフが ADR として記録されているか
- [x] 共通ポリシー（エラー、メモリ、ログ）が定義されているか

## 6. 共通ポリシー

### ヒープパーティション
システムRAMを独立したヒープに分割し、障害隔離を実現する。 `{IndependentHeap}` `{FaultIsolation}` `{StrictMemoryLimit}`

| パーティション名 | 目的 | 最小サイズ | メモリ確保失敗時の影響 |
|---|---|---|---|
| COOSカーネルヒープ | スケジューラ, CSP等 | 4.0KB | システムパニック |
| WASMランタイムヒープ | インタープリタ, コンテキスト等 | 2.0KB | システムパニック |
| サブシステムヒープ | IPCルータ, HAL等 | 1.0KB | IPC停止 |
| サービスヒープ | その他サービス | 1.0KB | サービスのみ終了 |
| ゲストモジュールヒープ | ゲストアプリケーション | 24KB | ゲストのみ終了 |

### 設定方式
ヘッダファイル形式のコンフィグファイルでシステムパラメータを定義し、コンパイル時に固定する。 `{ConfigurableSystem}`
