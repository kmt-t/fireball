# JITコンパイラ (コード生成コア) テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: `docs/components/tier3_jit/jit_compiler.md`

Copy-and-Patchエンジンによるネイティブコード生成、`__fastcall` CPS 4引数レジスタ規約とJITトレース独自のTOS/NOSキャッシュの非対称性（`{ADR_TosCacheAsymmetry}`）、JITトレースヘッダのメモリレイアウト、`code_offset`スケーラビリティ（`{ADR_ScalableCodeOffset}`）、および位置独立性（`{PositionIndependentCode}`）を検証する。

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
| JITC-40 | 生成コードの位置独立性 (PIC) | 同一トレースバイナリを別のバッファ/オフセットにコピー | 実行 | 再コンパイルやリロケーション修正なしで完全に同一の結果を出力する | §7.2 `{PositionIndependentCode}` |
| JITC-41 | ゲストメモリ境界チェックのインライン埋め込み | メモリアクセス命令を含むトレース | コンパイル | `CMP addr, mem_size; BHS.W <trap>`相当（マスクなし比較）が埋め込まれ、境界外で安全にトラップする | §7.2 `{MemoryBoundaryCheck}` `{FastAddressCheck}` |
| JITC-42 | キャッシュ溢れの3面ローテーション処理 | キャッシュ容量超過 | コンパイル試行 | Oldestバンクを破棄して再利用し、破棄されたトレースへのインバウンドチェインをO(k)でアンリンクする | §7.2「Cache Capacity Check」, `{JIT_LazyChaining}` |
| JITC-43 | ホストコール (WASI / fireball_call) の ABI 整合 | 0〜6引数のホスト関数呼び出し | トレース実行 | 32バイトシャドウスペース・16バイトスタックアライメントおよびCaller-savedレジスタ（R10/R11）が退避・復元される | §4.1手順3 `{JIT_RuntimeAPI_Fallback}` |

### トレース境界不変条件とハンドラ委譲 (§3.3, §4.1)

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| JITC-50 | スタック自己完結性検査 | 先頭で `local.set` や二項演算が先行するブロック | コンパイル試行 | 累積スタック深さが負になるため JIT 化を安全に拒否（`None` 返却）し、インタープリタ実行にフォールバックする | §3.3「スタック自己完結性不変条件」 |
| JITC-51 | 制御フロー命令の直前ブロック終端 | `BR`, `IF`, `CALL` を含む関数 | BasicBlock 抽出 | 制御フロー命令の直前で BasicBlock が終端され、制御フロー自体は JIT トレース外でハンドラへ委譲される | §3.3「制御フロー・コール境界の委譲」 |
| JITC-52 | トレース境界でのメモリ同期 | JIT トレース実行終了時 | レジスタおよびメモリ確認 | ローカル変数および戻り値（stack_depth 0/1）がメモリ/スタックへ完全に同期され、未確定レジスタが残らない | §3.3「メモリ同期不変条件」 |
| JITC-53 | JIT専用チェイニングハンドラ分離 | JIT トレース末尾での分岐 | `jit_chain_branch_handler` 実行 | ターゲットが Active/Warm キャッシュにあればインプレースパッチして `BX` tail-call し、未コンパイルならインタープリタへ復帰する | §4.1「専用分岐ハンドラ分離」`{JIT_LazyChaining}` |

## 3. テスト検証実績と網羅状況

- **JITC-01〜06 (Copy-and-Patch)**: 単一パスによる命令テンプレートのコピー＆パッチおよび必須リロケーションホールの検証を完了。
- **JITC-10 (CPS 4引数規約)**: `(ip, stack_bot, env, local_base)` を物理レジスタにマップし、インタープリタと共通のシグネチャで直接 C 関数呼び出しできることを実証済み。
- **JITC-20〜22 (16バイト物理ヘッダ)**: `jit_trace_header`（`head_wasm_pc`, `trace_byte_size`, `flags`, `variant_id`, `chain_next_pc`, `chain_target_addr`）が `+0x00` に配置され、ネイティブ命令列が `+0x10` から展開されることを実証済み。
- **JITC-40 (PIC 位置独立性)**: トレースバイナリを別のメモリ領域・オフセットへコピーして再コンパイルなしで直接実行し、完全同一の演算結果を返すことを実証済み。
- **JITC-42 (3面キャッシュ代謝 & O(k) アンリンク)**: 3面マルチバッファキャッシュのローテーションおよび破棄バンクの被チェイン逆引きテーブルに基づく O(k) アンリンクを実証済み。
- **JITC-43 (ホストコール ABI)**: 0〜6引数のホスト関数呼び出しにおけるスタックアライメントおよびCaller-savedレジスタの完全保護を実証済み。

## 4. 未検証・スコープ外

- Thumb-2/RISC-V 実機ターゲットでの `constexpr` アセンブラ生成バイナリの実機検証。

