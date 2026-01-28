# Concept: constexpr Encoder + Literal Pool

## 概要
constexpr命令エンコーダにより生成された命令テンプレートと、実行時のリテラルプールへのパッチ処理を組み合わせることで、高速かつ型安全なJIT生成を実現する。

### 1. constexprエンコーダ (RV32I例)
命令フォーマット（R/I/S/B/U/J）ごとに構造体を定義し、コンパイル時にバイナリを生成する。

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

### 2. 命令テンプレートの静的定義
WASM命令ごとに、どのネイティブ命令を出力し、どこをパッチするかを定義する。

```cpp
static constexpr uint32_t TEMPLATE_I32_CONST[] = {
    rv32::i_type(rv32::opcode::load, rv32::reg::a0, 0b010, rv32::reg::pc_base, 0).raw
};
// パッチ情報: offset 0 の命令のリテラルプール・オフセットを埋める
```

### 3. 実行時のパッチ処理 (Simplified with Implicit Metadata)
パッチ情報のメタデータを極小化するため、パッチ対象となる「命令の種類」を実行時に決め打ち（Implicit Metadata）にする。

```cpp
/**
 * @brief パッチ種別 (アーキテクチャごとに定義)
 */
enum class jit_patch_type : uint8_t {
    RV32_LW_POOL,  // Literal Poolからのロード (LW rd, imm(s1))
    RV32_ADDI_PC,  // WASM_PCの更新 (ADDI s3, s3, imm)
    // ...
};

// JIT実行時のコード
void process_patch(uint32_t* patch_addr, jit_patch_type type, int32_t imm) {
    switch(type) {
        case jit_patch_type::RV32_LW_POOL:
            // Literal Pool Load は常に「rd=a0, rs1=s1 (Context)」と決め打ち
            *patch_addr = rv32::i_type(rv32::opcode::load, rv32::reg::a0, 0b010, rv32::reg::s1, imm).raw;
            break;
    }
}
```

## 利点
1. **パッチ情報の極小化**: 保持すべきは `patch_addr` と `imm` のみで、詳細なレジスタ構成などはJITエンジン内の `switch` に閉じ込められる。
2. **高速化**: 実行時のパッチループにおいて、複雑なメタデータのパースが不要。
3. **15KLOC制限**: パッチロジックが定型化され、メタデータ構造体の定義と管理コストが削減される。
