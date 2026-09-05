# JITコンパイラ (コード生成コア) テスト仕様書 (Test Specification)

## 1. 目的と対象範囲

正本: [`jit_compiler.md`](docs/components/tier3_jit/jit_compiler.md)

Copy-and-Patchエンジンによるネイティブコード生成、`__fastcall` CPS 4引数レジスタ規約とJITトレース独自のTOS/NOSキャッシュの非対称性（`{ADR_TosCacheAsymmetry}`）、JITトレースヘッダのメモリレイアウト、`code_offset`スケーラビリティ（`{ADR_ScalableCodeOffset}`）、および位置独立性（`{PositionIndependentCode}`）を検証する。

## 2. テストケース一覧

### Copy-and-Patchエンジン ({JIT_CopyAndPatch})

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| JITC-01 | テンプレートのコピー＋パッチのみ（最適化なし） | 単純な直線コード（`i32.const`→`i32.add`等） | コンパイル | 生成ネイティブコードは事前定義テンプレートの連結+即値/分岐先パッチのみで構成される（IRを介した最適化パスが存在しない） | 「Zero Compile Cost」, `{SinglePassCompilation}` |
| JITC-02 | 未対応命令のエラー | ステンシル未定義のWASM opcode | コンパイル | 明確なエラー（`NO_STENCIL_FOR`相当）で失敗し、無音の誤コンパイルをしない | runtime_engine_concept.py `CopyPatchCompiler.compile_trace`のraise |
| JITC-03 | ステンシルの必須リロケーションホール検証 | 必要な`imm_lo`/`imm_hi`等を渡さない | `emit`を呼ぶ | `KeyError`相当で拒否される | runtime_engine_concept.py `test_stencil_requires_its_relocation_holes` |
| JITC-04 | AAPCS境界フォールバック | 複雑な命令・ホスト関数呼び出し | コンパイル | ランタイムAPI呼び出しスタブへフォールバックする |  `{JIT_RuntimeAPI_Fallback}` |
| JITC-05 | 命令キャッシュ同期バリア | パッチ完了後 | コンパイル完了を確認 | `__DSB()`/`__ISB()`相当のバリアが発行される | {JIT_CopyAndPatch} |
| JITC-06 | インタープリタ⇔JIT境界でのレジスタ書き戻しコスト | JITトレースから脱出 | 脱出処理を確認 | ダーティなTOS/NOS書き戻しが`STR`2〜3命令相当の有界コストに収まる（コンテキスト再構築が発生しない） |  `{ADR_TosCacheAsymmetry}` |

### レジスタ規約とTOS/NOS非対称性

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| JITC-10 | インタープリタとJITが共有するCPS 4引数規約 | - | 両エンジンの呼び出し規約を比較 | `R0: ip, R1: stack_bot, R2: local_base, R3: tos`が完全一致する | `{AAPCS_FastCall}` |
| JITC-11 | JITトレース内部でのみTOS/NOSキャッシュ(R4/R5)を使用 | JITトレース生成 | 生成コードのレジスタ使用を確認 | トレース内部の最上段キャッシュ（TOS `R4`）はトレース境界でもレジスタのまま維持され、次段キャッシュ `R5`（NOS）以降はJIT内部でのみ占有する | `{ADR_TosCacheAsymmetry}` |
| JITC-12 | トレース境界での正準アドレス書き戻し | 2つの連結されたトレースが異なるバリアント(TOSレジスタ割当)を選択 | チェイン実行 | 境界では`stack_bot`相対の正準アドレスへ書き戻され、次のトレース/ハンドラはそこから読む（バリアント違いでも安全） | 「トレース境界とチェイニングの安全性」 |
| JITC-13 | ローカル変数アクセスの静的オフセット畳み込み | 同一関数フレーム内のローカル変数アクセス | コンパイル | `frame_offset + local_offset + idx*4`がコンパイル時に即値定数としてパッチされ、追加のベースレジスタを消費しない | 「ローカル変数アクセスの静的オフセット畳み込み」`{ContextPointerRegister}` |

### JITトレースヘッダ

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| JITC-20 | ヘッダサイズは固定16バイト | 任意のトレース | ヘッダを解析 | `+0x00 head_wasm_pc(u32)`, `+0x04 trace_byte_size(u16)`, `+0x06 flags(u8)`, `+0x07 variant_id(u8)`, `+0x08 chain_next_pc(u32)`, `+0x0C chain_target_addr(u32)`の16バイト構造 | 「JIT トレース物理メモリレイアウト」 |
| JITC-21 | flagsビットの意味 | PROMOTED済み/LOOP_HEADERのトレース | flagsを確認 | `0x01: PROMOTED`, `0x02: LOOP_HEADER`が正しく設定される | 同上 |
| JITC-22 | ネイティブコード列は+0x10から展開 | 任意のトレース | メモリレイアウトを確認 | ヘッダ直後(+0x10)からThumb-2命令列が始まる | 同上 |

### ADR_ScalableCodeOffset

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| JITC-30 | code_offsetはアライメントシフト保持 | エントリテーブル | `code_offset`の格納方式を確認 | `actual_offset >> code_align_shift`で16bitに収めている（32bit化していない） | ADR_ScalableCodeOffset 結論 |
| JITC-31 | 最大キャッシュサイズの計算 | `code_align_shift`設定値 | 最大アドレス可能範囲を確認 | `65535 << code_align_shift`が理論上限と一致する | 同上 |

### 安全性制約

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| JITC-40 | 生成コードの位置独立性 (PIC) | 同一トレースバイナリを別のバッファ/オフセットにコピー | 実行 | 再コンパイルやリロケーション修正なしで完全に同一の結果を出力する | `{PositionIndependentCode}` |
| JITC-41 | ゲストメモリ境界チェックのインライン埋め込み | メモリアクセス命令を含むトレース | コンパイル | `CMP addr, mem_size; BHS.W <trap>`相当（マスクなし比較）が埋め込まれ、境界外で安全にトラップする | `{MemoryBoundaryCheck}` `{FastAddressCheck}` |
| JITC-42 | キャッシュ溢れの3面ローテーション処理 | キャッシュ容量超過 | コンパイル試行 | Oldestバンクを破棄して再利用し、破棄されたトレースへのインバウンドチェインをO(k)でアンリンクする | 「Cache Capacity Check」, `{JIT_LazyChaining}` |
| JITC-43 | ホストコール (WASI / fireball_call) の ABI 整合 | 0〜6引数のホスト関数呼び出し | トレース実行 | 32バイトシャドウスペース・16バイトスタックアライメントおよびCaller-savedレジスタ（R10/R11）が退避・復元される |  `{JIT_RuntimeAPI_Fallback}` |

### トレース境界不変条件とハンドラ委譲

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| JITC-50 | スタック自己完結性検査 | 先頭で `local.set` や二項演算が先行するブロック | コンパイル試行 | 累積スタック深さが負になるため JIT 化を安全に拒否（`None` 返却）し、インタープリタ実行にフォールバックする | 「スタック自己完結性不変条件」 |
| JITC-51 | 制御フロー命令の直前ブロック終端 | `BR`, `IF`, `CALL` を含む関数 | BasicBlock 抽出 | 制御フロー命令の直前で BasicBlock が終端され、制御フロー自体は JIT トレース外でハンドラへ委譲される | 「制御フロー・コール境界の委譲」 |
| JITC-52 | トレース境界でのメモリ同期 | JIT トレース実行終了時 | レジスタおよびメモリ確認 | ローカル変数および戻り値（stack_depth 0/1）がメモリ/スタックへ完全に同期され、未確定レジスタが残らない | 「メモリ同期不変条件」 |
| JITC-53 | JIT専用チェイニングハンドラ分離 | JIT トレース末尾での分岐 | `jit_chain_branch_handler` 実行 | ターゲットが Active/Warm キャッシュにあればインプレースパッチして `BX` tail-call し、未コンパイルならインタープリタへ復帰する | 「専用分岐ハンドラ分離」`{JIT_LazyChaining}` |
| JITC-54 | 制御コードスキップ表を用いた直接チェイニング | 制御構文デリミタPCを含むブロックとスキップ先後続ブロック | トレース登録とチェイニング実行 | `control_skip_tree`（`bswap32`反転キー `RadixBinaryTreeView`）によりデリミタからフォールスルー先PCが一撃で解決され、Active/Warmキャッシュ間での直接ネイティブチェイニング（`chain_next`）が確立される | 「制御コードスキップ表と直接チェイニング連携」`{JIT_LazyChaining}` |

### 実装の勘所・不変条件（Gotchas & Implementation Invariants）

| ID | 検証項目 | 前提条件 | 手順 | 期待結果 | 紐付け |
| :--- | :--- | :--- | :--- | :--- | :--- |
| JITC-GOTCHA-01 | CPS引数レジスタとJIT内部一時レジスタの物理競合防止 | トレース生成 | 各ステンシルのレジスタ割り当てを走査 | CPS引数レジスタ（R0-R3）は呼び出し境界でのみ用いられ、JIT内部一時レジスタ（assignable pool: R4-R6, R8-R11）と物理的に一切重複しない。**実装の勘所**: x86-64 等のホストシミュレータ上で `__fastcall` 引数（RCX, RDX, R8, R9）と実機 Thumb-2 の R8/R9（mem_base/mem_size）を混同して同一レジスタとして扱ってはならない | `{JIT_RegisterMapping}` |
| JITC-GOTCHA-02 | メモリ基底・サイズのロード起点（`execution_context` 統合） | メモリアクセス命令を含むトレース | プロローグ命令列を検証 | `mem_base` と `mem_size` は、R1（`stack_bot`）の `[R1, #0x20]` および `[R1, #0x24]` から一度だけ `LDR.W` でピン留めロードされる。**実装の勘所**: 独立した `env` 引数レジスタは存在せず、`execution_context` 内包オフセットから取得しなければ不正メモリアクセスとなる | `{ExecutionContext_Layout}` `{JIT_RegisterMapping}` |
| JITC-GOTCHA-03 | Callee-saved TOS（R4）の無退避性 | トレースエピローグ生成 | エピローグ命令列を検証 | トレース内部でキャッシュされたスタックトップ値 `R4: TOS` はエピローグでスタック書き戻し（`STR`）や読み戻しを経由せずレジスタで直接保持し、スタック次段キャッシュ `R5: NOS`（ダーティな場合）のみが `STR` でスタックメモリへ書き戻される。`R3`（CPS 第4引数の `tos`）はトレース開始時に一度読まれるだけで、エピローグでは一切使われない。**実装の勘所**: `R4` を無駄にスタック退避・復元するとトレース境界のゼロコスト受け渡し利点が失われる | `{ADR_TosCacheAsymmetry}` |
| JITC-GOTCHA-04 | 境界チェック先行性と副作用ゼロ（Wrapping禁止） | メモリアクセス命令 | アドレス≧`mem_size` でトレース実行 | メモリアクセス（LDR/STR）前に必ず `CMP addr, r9; BHS.W <trap>` が評価され、境界外時はメモリ書き込みや値更新の副作用が一切発生せず即座にトラップテールへ分岐する。**実装の勘所**: マスク等でアドレスを巡回（Wrapping）させて継続実行することは安全上絶対に許容されない | `{MemoryBoundaryCheck}` `{FastAddressCheck}` |
| JITC-GOTCHA-05 | トラップ分岐（`BHS.W`）の2パスバックパッチ | トレース生成 | トラップ分岐命令のパッチ履歴を検証 | `BHS.W` の発行時点ではトラップテールのアドレスが未確定なため、オフセット0で仮発行した上で位置を記録し、エピローグ・トラップテール生成後に実アドレスへバックパッチされる。**実装の勘所**: 1パスで未確定アドレスへ分岐命令を発行すると未定義ジャンプを引き起こす | `{FastAddressCheck}` |
| JITC-GOTCHA-06 | ARM MLS 命令のオペランド順序 | `i32.rem_s` / `i32.rem_u` を含むトレース | 生成されたネイティブコードを実行 | `mls r4, r12, r4, r5` が $Rd(r4) = Ra(r5) - Rn(r12) \times Rm(r4)$（被除数 - 商×除数 = 剰余）を算出する。**実装の勘所**: ARM MLS 命令は $Rd = Ra - (Rn \times Rm)$ という引数順序規約を持ち、$Ra - Rn \times Rm$ の順序を逆にすると負の剰余値を出力するバグとなる | `{JIT_CopyAndPatch}` |
| JITC-GOTCHA-07 | トレース結果値と C 呼び出し規約の無関係性 | 残余値を持つトレース（`stack_depth == 1`） | エピローグ命令列と呼び出し元の受け取り方を検証 | トレースの残余値（VM オペランドスタックの状態）は `stack_bot` 経由でメモリへ書き込まれ、トレースは常に void を返す。呼び出し元はその値を戻り値からではなくこのメモリ位置から読む。**実装の勘所**: ホストシミュレータ上の呼び出しは ctypes 経由の実 C 関数呼び出しであるため、C の戻り値レジスタに「ついでに」結果を乗せたくなるが、VM のオペランドスタック状態と呼び出し規約上の戻り値は無関係であり、これを混同すると `stack_bot` に何も書き込まれず、分岐条件や結果値が常に0として誤って読まれる | `{ADR_TosCacheAsymmetry}` |

## 3. テスト検証実績と網羅状況

- **JITC-01〜06 (Copy-and-Patch)**: 単一パスによる命令テンプレートのコピー＆パッチおよび必須リロケーションホールの検証を完了。
- **JITC-10 (CPS 4引数規約)**: `(ip, stack_bot, local_base, tos)` を物理レジスタにマップし、インタープリタと共通のシグネチャで直接 C 関数呼び出しできることを実証済み。
- **JITC-20〜22 (16バイト物理ヘッダ)**: `jit_trace_header`（`head_wasm_pc`, `trace_byte_size`, `flags`, `variant_id`, `chain_next_pc`, `chain_target_addr`）が `+0x00` に配置され、ネイティブ命令列が `+0x10` から展開されることを実証済み。
- **JITC-40 (PIC 位置独立性)**: トレースバイナリを別のメモリ領域・オフセットへコピーして再コンパイルなしで直接実行し、完全同一の演算結果を返すことを実証済み。
- **JITC-42 (3面キャッシュ代謝 & O(k) アンリンク)**: 3面マルチバッファキャッシュのローテーションおよび破棄バンクの被チェイン逆引きテーブルに基づく O(k) アンリンクを実証済み。
- **JITC-43 (ホストコール ABI)**: 0〜6引数のホスト関数呼び出しにおけるスタックアライメントおよびCaller-savedレジスタの完全保護を実証済み。

## 4. 未検証・スコープ外

- Thumb-2/RISC-V 実機ターゲットでの `constexpr` アセンブラ生成バイナリの実機検証。
