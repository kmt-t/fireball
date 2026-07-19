# コンポーネント設計：constexpr Assembler

## 1. コンセプト
<!-- traceability: {JIT_Encoder} {META_Static_Resolution} {META_CompileTimeValidation} {PositionIndependentCode} -->
constexpr Assembler は、C++のコンパイル時計算（`constexpr`）機能を活用し、ターゲットアーキテクチャの命令バイナリを型安全かつ効率的に生成するための DSL (Domain Specific Language) である。手動でのビット演算による命令生成を排除し、ビルド時に命令テンプレートを確定させることで、実行時のJITオーバーヘッドを「ゼロ」に近づけるとともに、不正なレジスタ指定や即値溢れをコンパイル時に検知する。また、生成される命令はすべて位置独立コード（Position Independent Code）の規則に準拠し、実行時のロード先アドレス（配置先）に依存しない配置と実行を可能とする。 `{JIT_Encoder}` `{META_Static_Resolution}` `{META_CompileTimeValidation}` `{PositionIndependentCode}`

## 2. アーキテクチャ分類
<!-- traceability: {META_3TierSeparation} {META_Static_Resolution} {ZeroRuntimeOverhead} -->
本コンポーネントは **Tier 3 (実装ドメイン)** に属する。C++のコンパイル時機能に依存したスタティックなライブラリとして機能し、実行時のオーバーヘッドを持たない。 `{META_3TierSeparation}` `{META_Static_Resolution}` `{ZeroRuntimeOverhead}`

## 3. 静的モデル

### 3.1 データ構造
- **Instruction Format Structs**: R/I/S/B/U/J などの命令形式ごとにビットフィールドを定義した構造体。
- **Type-safe Enums**: 物理レジスタ (`reg::a0`, `reg::s1` 等) や命令コード (`opcode::load` 等) を型として定義。

### 3.2 内部ブロック図
```mermaid
graph TD
    Source[C++ Code] -->|constexpr| DSL[Assembler DSL]
    DSL -->|Static Analysis| Encoder[Bitfield Encoder]
    Encoder -->|Compile-time| Template[Native Binary Template]
    Template -->|Link| JIT_Engine[JIT Copy-and-Patch Engine]
```

### 3.3 主要なクラス・構造体・配列・定数


#### `fireball::riscv::i_type`

<!-- traceability: {JIT_Encoder} {META_ZeroCostAbstraction} -->
即値演算やロード命令に使用される形式。
本構造体は C++ `constexpr` を用いることで、名前空間 `fireball::riscv` 内でビルド時に命令エンコード（`JIT_Encoder`）を静的に完了させ、実行時のエンコード/デコードコストを完全に排除（`META_ZeroCostAbstraction`）する。 `{JIT_Encoder}` `{META_ZeroCostAbstraction}`

| 項目名 | 機能と役割 | 型分類 | サイズ・制約 |
| :--- | :--- | :--- | :--- |
| 命令階層 | RISC-Vの基本命令セットにおける大分類（OP-IMM等） | ビットフィールド | 7bit |
| 対象レジスタ | 演算結果が書き込まれるデスティネーション | ビットフィールド | 5bit |
| 詳細機能 | 対応するOpcode定義（ADD、SUB、SLL等）に基づき演算の種類を特定する funct3 ビットフィールド | ビットフィールド | 3bit |
| 基点レジスタ | 演算またはアドレス計算の基点となるソース | ビットフィールド | 5bit |
| 組込即値 | 12ビットの符号付き即値データ | ビットフィールド | 12bit |

#### `fireball::arm::add_imm`

<!-- traceability: {JIT_Encoder} {META_ZeroOverhead} -->
`ADD`, `SUB` などの即値演算（32ビット命令）で使用される形式。
本構造体は C++ `constexpr` コンストラクタを備え、名前空間 `fireball::arm` 内でビルド時に各 Thumb-2 演算の命令テンプレートを静的にエンコードして生成（`JIT_Encoder`）する。 `{JIT_Encoder}`

| 項目名 | 機能と役割 | 型分類 | サイズ・制約 |
| :--- | :--- | :--- | :--- |
| 基本命令部 | Thumb-2 命令の主要な操作コードを格納するビットパターン（ビット 16〜31） | ビットフィールド | 16bit |
| 結果レジスタ | 命令の出力先 | ビットフィールド | 4bit |
| 演算レジスタ | 命令の入力元 | ビットフィールド | 4bit |
| 合成即値 | 複数のフィールドを組み合わせて作成される数値データ | ビットフィールド | 12bit |
| 更新フラグ | 条件フラグ（APSR）を更新するかどうかを指定 | ブール値 | 1bit |

#### `x64::mov_ri`
レジスタへの即値代入命令。x64では32ビット即値を直接埋め込めるため、RISC-V/ARMのようなリテラルプール（PC相対ロード）を介さずに定数をパッチ可能。

| 項目名 | 機能と役割 | 型分類 | サイズ・制約 |
| :--- | :--- | :--- | :--- |
| 基本命令コード | レジスタ番号を含む命令の先頭バイト | ビットフィールド | 8bit |
| 32bit即値 | 命令の後に続く符号付き整数データ。パッチ対象 | 値 | 32bit |
| `rex_w` | 64ビット拡張プレフィックス | ビットフィールド | 8bit (Optional) |

## 4. 動的モデル

### 4.1 アルゴリズム

#### コンパイル時エンコード
1. プログラマが `constexpr` 関数や構造体として命令を記述する。
2. C++コンパイラがビットフィールドの詰め込み（Pack）を処理する。
3. `static_assert` により、即値が12ビットを超えていないか、レジスタ番号が有効か等がチェックされる。
4. 最終的な `uint32_t` のバイナリ値がソースコード内の定数として埋め込まれる。

### 4.2 状態遷移図
本コンポーネントはビルド時に完結するため、実行時の状態遷移は存在しない。

## 5. インターフェイス定義

### 5.1 公開API (C++ DSL)


#### Instruction Constructor

| 項目 | 内容 |
| :--- | :--- |
| 機能概要 | 命令を構築するコンパイル時関数群。 |
| シグネチャ | `constexpr auto fireball::make_instruction(fireball::riscv::opcode_t op, fireball::riscv::reg_t rd, fireball::riscv::reg_t rs1, fireball::riscv::imm_t imm) noexcept -> fireball::InstructionToken` |
| 引数 | `op`: 命令の種類を表す `riscv::opcode_t` 列挙型<br>`rd`, `rs1`: 物理レジスタを特定する `riscv::reg_t` 列挙型<br>`imm`: 符号付き即値を表す `riscv::imm_t` (int32_t のエイリアス) |
| 戻り値 | `InstructionToken`（エンコード済みの 32 ビット命令値を保持する `uint32_t` 型のエイリアス） |
| 備考 | `constexpr` 修飾されており、定数式として評価可能。 |

## 6. 制約達成の方策

### 6.1 性能制約
<!-- traceability: {META_Static_Resolution} {ZeroRuntimeOverhead} -->
- **静的解像 (Static Resolution)**: `{META_Static_Resolution}` により、命令生成に関わるあらゆるビットシフトや論理和（OR）の演算を実行時から排除し、ビルド時に完全に定数へと評価（解像）しておく。
- **実行時オーバーヘッドの完全排除 (Zero Runtime Overhead)**: constexpr関数内で事前評価された命令バイト列は実行時に直接メモリアライメントされた命令バッファに転記されるため、実行時のアセンブル処理オーバーヘッドは単なる `memcpy` と同等の超高速なメモリ転送のみとなる。これにより、抽象化のための余分な実行時オーバーヘッドを完全にゼロにする。 `{ZeroRuntimeOverhead}`

### 6.2 安全性制約
<!-- traceability: {META_Static_Resolution} -->
- **方策**: C++の型システムと `static_assert` を利用し、アセンブラレベルのバグ（誤ったレジスタ使用等）を開発段階で完全に排除する。

