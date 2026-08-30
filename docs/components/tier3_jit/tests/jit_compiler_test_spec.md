# JITコンパイラ (コード生成コア) テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: `docs/components/tier3_jit/jit_compiler.md`
参考実装: `docs/components/tier3_jit/concepts/jit_copy_patch_concept.py`, `jit_assembler_constexpr_concept.py`（**いずれも未読。§4に明記のとおりハルシネーション回避のため、これらに基づくテストケースは書いていない**）。本書のケースは`jit_compiler.md`本文と、`runtime_engine_concept.py`内の`CopyPatchCompiler`セクション（読了済み）のみを根拠とする。
現行実装: `experiments/pysim/x64_stencils.py`, `x64_jit.py`, `x64_asm.py`

Copy-and-Patchエンジンによるネイティブコード生成、`__fastcall` CPS 4引数レジスタ規約とJITトレース独自のTOS/NOSキャッシュの非対称性（`{ADR_TosCacheAsymmetry}`）、JITトレースヘッダのメモリレイアウト、`code_offset`スケーラビリティ（`{ADR_ScalableCodeOffset}`）を検証する。

## 2. テストケース一覧

### Copy-and-Patchエンジン (§3.1, §4.1)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| JITC-01 | テンプレートのコピー＋パッチのみ（最適化なし） | 単純な直線コード（`i32.const`→`i32.add`等） | コンパイル | 生成ネイティブコードは事前定義テンプレートの連結+即値/分岐先パッチのみで構成される（IRを介した最適化パスが存在しない） | §1「Zero Compile Cost」, `{SinglePassCompilation}` |
| JITC-02 | 未対応命令のエラー | ステンシル未定義のWASM opcode | コンパイル | 明確なエラー（`NO_STENCIL_FOR`相当）で失敗し、無音の誤コンパイルをしない | runtime_engine_concept.py `CopyPatchCompiler.compile_trace`のraise |
| JITC-03 | ステンシルの必須リロケーションホール検証 | 必要な`imm_lo`/`imm_hi`等を渡さない | `emit`を呼ぶ | `KeyError`相当で拒否される | runtime_engine_concept.py `test_stencil_requires_its_relocation_holes` |
| JITC-04 | AAPCS境界フォールバック | 複雑な命令・ホスト関数呼び出し | コンパイル | ランタイムAPI呼び出しスタブへフォールバックする | §4.1手順3 `{JIT_RuntimeAPI_Fallback}` |
| JITC-05 | 命令キャッシュ同期バリア | パッチ完了後 | コンパイル完了を確認 | `__DSB()`/`__ISB()`相当のバリアが発行される | §4.1手順4 |
| JITC-06 | インタープリタ⇔JIT境界でのレジスタ書き戻しコスト | JITトレースから脱出 | 脱出処理を確認 | ダーティなTOS/NOS書き戻しが`STR`2〜3命令相当の有界コストに収まる（コンテキスト再構築が発生しない） | §4.1手順5 `{ADR_TosCacheAsymmetry}` |

### レジスタ規約とTOS/NOS非対称性 (§3.3, §8 ADR_TosCacheAsymmetry)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| JITC-10 | インタープリタとJITが共有するCPS 4引数規約 | - | 両エンジンの呼び出し規約を比較 | `R0: ip, R1: stack_bot, R2: env, R3: local_base`が完全一致する | §3.3, jit_compiler.md ADR背景 |
| JITC-11 | JITトレース内部でのみTOS/NOSキャッシュ(R4/R5)を使用 | JITトレース生成 | 生成コードのレジスタ使用を確認 | インタープリタ側はR4/R5について不変条件を負わない（callee-savedとして通常どおり扱う） | §8 ADR_TosCacheAsymmetry 結論 |
| JITC-12 | トレース境界での正準アドレス書き戻し | 2つの連結されたトレースが異なるバリアント(TOSレジスタ割当)を選択 | チェイン実行 | 境界では`stack_bot`相対の正準アドレスへ書き戻され、次のトレース/ハンドラはそこから読む（バリアント違いでも安全） | §8「トレース境界とチェイニングの安全性」 |
| JITC-13 | ローカル変数アクセスの静的オフセット畳み込み | 同一関数フレーム内のローカル変数アクセス | コンパイル | `frame_offset + local_offset + idx*4`がコンパイル時に即値定数としてパッチされ、追加のベースレジスタを消費しない | §8「ローカル変数アクセスの静的オフセット畳み込み」`{ContextPointerRegister}` |

### JITトレースヘッダ (§3.3)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| JITC-20 | ヘッダサイズは固定16バイト | 任意のトレース | ヘッダを解析 | `+0x00 head_wasm_pc(u32)`, `+0x04 trace_byte_size(u16)`, `+0x06 flags(u8)`, `+0x07 variant_id(u8)`, `+0x08 chain_next_pc(u32)`, `+0x0C chain_target_addr(u32)`の16バイト構造 | §3.3「JIT トレース物理メモリレイアウト」 |
| JITC-21 | flagsビットの意味 | PROMOTED済み/LOOP_HEADERのトレース | flagsを確認 | `0x01: PROMOTED`, `0x02: LOOP_HEADER`が正しく設定される | 同上 |
| JITC-22 | ネイティブコード列は+0x10から展開 | 任意のトレース | メモリレイアウトを確認 | ヘッダ直後(+0x10)からThumb-2命令列が始まる | 同上 |

### ADR_ScalableCodeOffset (§8)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| JITC-30 | code_offsetはアライメントシフト保持 | エントリテーブル | `code_offset`の格納方式を確認 | `actual_offset >> code_align_shift`で16bitに収めている（32bit化していない） | §8 ADR_ScalableCodeOffset 結論 |
| JITC-31 | 最大キャッシュサイズの計算 | `code_align_shift`設定値 | 最大アドレス可能範囲を確認 | `65535 << code_align_shift`が理論上限と一致する | 同上 |

### 安全性制約 (§7.2)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| JITC-40 | 生成コードの位置独立性 | 同一トレースを異なるキャッシュ位置に配置 | 実行 | 動作が変わらない（絶対アドレス埋め込みに依存しない） | §7.2 `{PositionIndependentCode}` |
| JITC-41 | ゲストメモリ境界チェックのインライン埋め込み | メモリアクセス命令を含むトレース | コンパイル | `CMP addr, mem_size; BHS.W <trap>`相当（マスクなし比較）が埋め込まれ、境界外でインタープリタへフォールバックする | §7.2 `{MemoryBoundaryCheck}` `{FastAddressCheck}` |
| JITC-42 | キャッシュ溢れの3面ローテーション処理 | キャッシュ容量超過 | コンパイル試行 | Oldestバンクを破棄して再利用する（jit_runtime.md側と共同責務） | §7.2「Cache Capacity Check」 |

## 3. 現状のギャップ（pysim実装との差分）

- `experiments/pysim/x64_jit.py`は関数単位の即時コンパイルであり、「トレース」という単位・トレースヘッダ（JITC-20〜22）・`chain_next`/`chain_target_addr`のヘッダフィールドを持たない（`jit_runtime_test_spec.md`と同根の根本的アーキテクチャ差異）。
- JITC-10（CPS 4引数規約の一致）は部分的に成立する: pysimのx64版インタープリタ(`interpreter.py`)とJIT(`x64_jit.py`)は共に`(ip, stack_bot/frame, env, local_base)`相当の引数を持つが、レジスタではなくPythonの関数引数として実装されている（x64ネイティブレジスタとしての物理規約はJIT側にしか存在しない）。
- JITC-11〜13（TOS/NOSキャッシュ非対称性）: pysimのx64stencilはスタック値を毎回`push`/`pop`で明示的にメモリ(ネイティブスタック)へ出し入れしており、`R4`/`R5`相当のTOS/NOSキャッシュ最適化そのものを行っていない（都度メモリアクセスするため、この最適化が要求する「トレース脱出時のみ書き戻す」という設計とは異なる、より単純なモデル）。ADRが問題にしている性能上のトレードオフ自体がpysimには存在しない。
- JITC-30/31（code_offsetのスケーラビリティ）: pysimは単一の連続バイト列に全関数を配置し、オフセットはPythonのintでそのまま扱っているため、16bit制約自体が存在しない（該当なし）。
- JITC-40〜42はpysimでも別形で実装済み: 境界チェック(`_gen_bounds_check`)は存在するが、位置独立性(JITC-40)は「単一の連続バッファ内」という前提でのみ成立し、3面ローテーション(JITC-42)は不在。

## 4. 未検証・スコープ外

- `jit_copy_patch_concept.py`, `jit_assembler_constexpr_concept.py`, `jit_trace_execution_verifier.py`, `thumb2_stencil_semantic_verifier.py`（いずれも未読。読了後、本仕様書を更新しテストケースを追加すること）。
- Thumb-2/RISC-V実機命令エンコーディングの正確性そのもの（pysimはx64のみを対象とするため、命令セット自体が異なる）。
