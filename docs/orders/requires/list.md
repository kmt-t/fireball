# 要件リスト

## 何をつくるのか
- wasm32ゲストを仮想化するハイパーバイザである。
- 小規模な組み込み向けである。
- 設計の良し悪しのベンチマーク対象としてWAMRを用いる。
  - 要件においてWAMRより優れているものを開発するのが目標である。
  - まずWAMRの要件を分析すること。
  - 設計前にWAMRの設計がどうなっているか確認する。
  - Fireballの仕様がWAMRの設計と比べ、Fireballの要件に対して優れているか評価する。

## パフォーマンス目標
1. **フットプリント**: WAMRインタープリタと同等以下のROM/RAMサイズで動作すること。
2. **パフォーマンス**: AOTを使用しない条件下で、WAMRインタープリタを上回る実行速度を達成すること。
3. **透明性**: 全ソースコードが監査可能であり、非決定論的な挙動が排除されていること。

## 開発方針
- **静的解決とメタプログラミング**: 実行時に決定可能な事項はコンパイル時に決定する。`constexpr` 等を最大限活用し、実行時のオーバーヘッドを最小化する。 `{Static_Resolution}`
- **コード規模の抑制**: システム全域のソースコードを15KLOC以内に収める。 `{Size_15KLOC}`
- **AIネイティブ開発**: 命令エンコード等の定型的な実装はLLMによる生成を活用し、設計と検証の品質を重視する。 `{AI_Native_Dev}`

## 開発環境
- コンパイラ: clang (C99, C++20, libstdc++)

## ターゲット
- アーキテクチャ: Cortex-M33, RISC-V/32, Linux
- 最小構成: Cortex-M33/RAM 64KB/ROM 128KB
- 依存性: 標準C/C++ライブラリ以外の外部ライブラリは用いない。
- 移植性: 移植性の高いHALを提供し、Zephyr等のRTOS上での動作もサポートする。

## ゲスト
- WASMバイナリ形式で提供され、サンドボックス内で動作する。
- 単独でのOTA（Over-the-Air）更新が可能であること。
- GDBによるデバッグをサポートすること。

## OS (COOS)
- コルーチンを用いた協調型OSを独自設計する。 `{CooperativeMultitasking}`
- リアルタイム性（決定論的応答）よりもメモリ効率と移植性を最優先する。 `{NotRTOS}`
- **確定的な実行**: コンテキストスイッチを明示的なポイント（`co_await`等）に限定する。 `{COOS_Deterministic}`
- **透明性の確保**: スケジューラは各タスクの待ち状態（URI等）を可視化可能とする。 `{COOS_Transparent}`
- 通信はホーアCSPに基づき、所有権移譲によるゼロコピーメッセージパッシングを行う。 `{CSPCommunication}` `{EliminateDataRace}` `{IPC_ZeroCopy}`
- 割り込み発生時、関連タスクの割り込みハンドラをウェイクアップする。 `{InterruptWakeup}`

## vSoC (WASM Runtime)
- wasm32ランタイム（浮動小数点除外）とvMMIOを備える。
- **JIT基本方針**: コンパイルレイテンシの最小化を最優先する。 `{LowLatencyJIT}`
  - **Zero Compile Cost**: プロファイラによる最適化が不要なほど高速なコンパイルを実現する。 `{JIT_ZeroCompileCostTheorem}`
    - 定義: `C_prof + C_opt < (T_interp - T_jit) * N` が成立しない小規模な `N` の領域をターゲットとし、`C_prof + C_opt ≈ 0` を目指す。
  - **Copy-and-Patch**: 命令テンプレートを連結しパッチを当てる方式を採用。 `{JIT_CopyAndPatch}`
  - **Constexpr Assembler**: JITテンプレートはC++の `constexpr` アセンブラでコンパイル時に生成する。 `{JIT_ConstexprAssembler}`
  - **Runtime Fallback**: 複雑な命令はC++で記述されたランタイムAPIへジャンプする。 `{JIT_RuntimeAPI_Fallback}`
- **実行最適化**:
  - 重要変数（Context, StackTop, PC等）を物理レジスタに固定する。 `{JIT_RegisterMapping}` `{ContextPointerRegister}`
  - 出力バイナリはPIC（位置独立コード）とする。 `{PositionIndependentCode}`
- **キャッシュ管理**: Active/Oldの2領域によるダブルバッファ管理を行い、小規模なJITキャッシュ領域で効率的に運用する。 `{JIT_DoubleBuffer_Cache}` `{SimpleJITArchitecture}`

## システムコール・IPC
- 全てのシステムコールはIPCルータを経由して行われる。 `{IPCRouter}`
- **ハンドルベース通信**: URIによる名前解決は初回のみとし、以降は取得したハンドル（ポインタ）で直接通信する。 `{IPC_HandleBased}`

## HAL
- 独自仕様のHALを定義し、ベアメタル、Zephyr、Linuxをバックエンドとしてサポートする。
- WASIラッパーをプラグインとして提供する。

## デバッグ・運用
- プロファイラ、動的テストツールの機能をハイパーバイザに内蔵する。 `{Debug_Integrated}`

## 設定
- ヘッダファイルのマクロ定義により、ヒープサイズや容量等のシステムパラメータをコンパイル時に固定する。 `{ConfigurableSystem}` `{StaticDI}`
