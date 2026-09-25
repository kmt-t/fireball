# JIT ステンシル仕様（ARMv8-M: TBD） {VERIFY_LLM}
<!-- traceability: {JIT_CopyAndPatch} {JIT_RegisterMapping} {PositionIndependentCode} {JIT_MultiBuffer_Cache} -->

## 適用状態

ARMv8-M向けJITの物理仕様は **TBD** とする。x64で確認した実行契約をARMv8-Mの命令列、ABI、メモリ保護方式へ外挿しない。

x64の確認済み契約は [`jit_abi.md`](docs/components/tier2_runtime/jit_abi.md) と [`jit_compiler.md`](docs/components/tier3_executer/jit_compiler.md) を正本とする。本書はARMv8-Mの物理設計が確定するまで、未確定項目の入口としてのみ残す。

## 未確定項目

- 対象ARMv8-Mプロファイル、コンパイラ・フラグ、必要なCPU機能。
- Interpreter/JIT境界の物理ABI、レジスタ割当、callee-save規則、スタック整列。
- Trace header、entry stub、共通コード領域、共通chain dispatcherの配置と形式。
- 命令Stencil、リロケーション、命令対応範囲、分岐・helper呼出しと復帰の生成方式。
- 線形メモリ境界検査、コードキャッシュ配置、MPU/W^X、命令キャッシュ同期。
- ROM/RAM使用量、実機性能、形式モデルとハードウェア検証の受け入れ条件。

上記はすべて **TBD** である。
