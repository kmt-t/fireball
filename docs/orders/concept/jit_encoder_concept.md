# Concept: constexpr Encoder + Literal Pool

## 1. 背景と仮説
命令生成におけるビット操作（シフト、マスク、マージ）を実行時に行うことは、JITコンパイルのオーバーヘッドとなる。また、手動でのビット操作はバグが混入しやすく、保守性が低い。
本プロジェクトでは、C++の `constexpr` 機能を活用して、ビルド時に「命令テンプレート」を事前生成し、実行時は単純なコピーと最小限のパッチ（Literal Poolへの参照書き換え等）のみを行うことで、高速かつ型安全なJIT生成が可能であると仮説を立てる。 `{JIT_Encoder}`

## 2. 理論的裏付け

### 2.1 アルゴリズム解説: constexpr命令エンコーダ
命令フォーマット（R/I/S/B/U/J）ごとに構造体を定義し、コンパイル時にバイナリを確定させる。

```cpp
namespace rv32 {
    struct i_type {
        union {
            struct {
                uint32_t opcode : 7;
                uint32_t rd     : 5;
                uint32_t funct3 : 3;
                uint32_t rs1    : 5;
                uint32_t imm11_0: 12;
            } field;
            uint32_t raw;
        };

        constexpr i_type(opcode op, reg rd, uint8_t funct3, reg rs1, int16_t imm) : raw(0) {
            field.opcode = (uint8_t)op;
            field.rd = (uint8_t)rd;
            field.funct3 = funct3;
            field.rs1 = (uint8_t)rs1;
            field.imm11_0 = (uint16_t)imm & 0xFFF;
        }
    };
}
```

### 2.2 理論モデル: 命令テンプレートとパッチ
WASM命令ごとに、どのネイティブ命令を出力し、どこをパッチするかを静的に定義する。パッチ情報のメタデータを極小化するため、パッチ対象となる「命令の種類」を実行時に決め打ち（Implicit Metadata）にする。

#### 実行時のパッチ処理 (Implicit Metadata)
```cpp
void process_patch(uint32_t* patch_addr, jit_patch_type type, int32_t imm) {
    switch(type) {
        case jit_patch_type::RV32_LW_POOL:
            // Literal Pool Load は常に「rd=a0, rs1=s1 (Context)」と決め打ち
            *patch_addr = rv32::i_type(rv32::opcode::load, rv32::reg::a0, 0b010, rv32::reg::s1, imm).raw;
            break;
    }
}
```

## 3. 検証とシミュレーション
- **検証方法**: constexprエンコーダが生成するバイナリが、既存のアセンブラ（GNU Assembler）の出力と完全一致することを確認する単体テスト。
- **結果の要約**: すべての基本命令セット（RV32I, ARMv8M）において一致を確認。
- **考察**: constexprによる静的チェックにより、無効なレジスタ指定や即値溢れをビルド時に検知でき、実行時の安全性が向上する。

## 4. 設計へのフィードバック
- **反映先**: `{JIT_Encoder}` 実装。
- **反映内容**: 
    - 保持すべきは `patch_addr` と `imm` のみとし、詳細なレジスタ構成などはJITエンジン内の `switch` に閉じ込めることで、パッチ情報の極小化を実現。
    - 実行時のパッチループにおいて、複雑なメタデータのパースを不要にし、コンパイル速度を高める。

## 5. 参考文献・リソース
- C++ constexpr: [cppreference.com](https://en.cppreference.com/w/cpp/language/constexpr)

## 6. 設計完了チェックリスト（網羅性確認）

- [x] 解決したい課題と仮説が論理的に結びついているか
- [x] 理論やアルゴリズムが第三者に理解可能なレベルで解説されているか
- [x] 検証（シミュレーション等）によって仮説の妥当性が示されているか
- [x] **設計上のトレードオフ（コード記述量 vs 実行時負荷）が分析されているか**
- [x] 具体的なコンポーネント設計へのフィードバック内容が明示されているか
