# 注釈プール縮小と整数型の計測結果

2026-10-13に、注釈プールを実行時に必要な値へ絞り、WASM上の位置と番号に意味別の整数型を導入した状態を計測した。実装に計数用instrumentationは含めていない。変更は未commitである。

AO-Benchは64×36から320×180まで5解像度、CPU 2固定、解像度ごとに3独立プロセス、各プロセスで3 warmup・7 timed callsを実行した。Interpreter、JIT、WAMR Fastの各実行でframe SHA-256が一致した。計測条件、全sample、WASM・source・library SHA-256は[`aobench.json`](aobench.json)にある。最終計測は2026-10-13 08:00 JSTである。

## 上限と型幅

| 項目 | 設定上限・型幅 | 根拠と検査 |
| :--- | ---: | :--- |
| 関数index空間 | import 32 + 定義256 = 288 | `FB_CONF_MAX_IMPORTS`、`FB_CONF_MAX_FUNCTIONS`。読み込み時に合計を検査 |
| 型index | 256件、u16 | `FB_CONF_MAX_TYPES=256`。256件を受理し、257件を拒否 |
| function parameters | 64 values、u8 | `FB_CONF_MAX_VALUE_STACK=64`。section count prevalidationで検査 |
| local count | 256件、u16 | `FB_CONF_MAX_LOCALS=256`。256件を受理し、257件を拒否 |
| local frame words | 現設定では最大640 words、u16 offset | 最大64 parameters + 256 locals、各i64/f64を2 wordsとして計算。section count prevalidationが設定上限を確認し、`LocalLayout`はu16幅をassertで保護 |
| BasicBlock | 最大65535件、u16 index | 有効indexは0..65534、`0xFFFF`はinvalid。65535件を受理し、65536件を拒否 |
| 制御構造の深さ | 32、u8 | `FB_CONF_MAX_NESTING_DEPTH=32`。32階層を受理し、33階層を拒否 |
| operand words | 最大128 words、u8 | `FB_CONF_MAX_VALUE_STACK=64` values、最大2 words/value |
| JIT trace body count | 64命令、u8 | `kMaxTraceInstructions=64`、C++ `static_assert`でu8上限内を確認 |
| JIT stack words | 最大128 words、u8 | Interpreterの最大raw operand wordsに一致 |
| 可変長命令幅 | 最大255 bytes、u8 | 可変LEB immediateを含む命令のみ記録し、超過はunsupported featureとして拒否 |

`br_table`には命令幅recordを持たせず、u16のlabel countと各labelのu16 BasicBlock indexを格納する。253 labelsで元命令幅が255 bytesを超えるケースを受理し、65536 labelsは拒否した。固定長命令の幅はopcodeから求める。

縮小対象から外した型は、`wasm_pc_t`・`code_ip_t`・`code_offset_t`・`annotation_offset_t`である。WASM PCとcode位置は96 KiB ROMを扱うためu32を維持し、annotation poolのbyte offsetもu32とした。`card_index_t`もu32のままとした。現行JIT cache/ROM領域に対するu8上限契約がなく、将来拡張できるindexだからである。`uint32_t`のraw stack word、WASM i32 bit pattern、memory effective offset、trap/status/counter、JIT cache byte数も座標・個数とは別の値なので縮小していない。

## 型定義と置換範囲

C/C++用のtypedefは[`integer_types.hxx`](../../../tier2_runtime/abi/integer_types.hxx)、Python用の`NewType`は[`integer_types.py`](../../../tier2_runtime/abi/integer_types.py)に置いた。主な利用先はInterpreter ABIとnative ABI、`native_interpreter.cxx`、`native_wasm_operations.hxx/.cxx`、`module.py`、`control_flow.py`、`reader.py`、JITの`jit_types.hxx`、`jit_runtime.hxx/.cxx`、`profiling.hxx`、`trace_compiler.cxx`、`jit_manager.py`である。

対象6ディレクトリのC/C++・Python sourceを検索したところ、意味型名は644箇所で使われている。これはtypedef定義、メンバー、関数引数、戻り値、ローカル宣言を含む参照数であり、644個の独立した変数を意味しない。単純な宣言パターンで拾える型付きdeclaratorは、C/C++側421箇所、Python側31箇所、合計452箇所である。関数宣言も含むため、これも独立変数の数ではない。PythonではNewTypeが16種類、C/C++ typedefも同じ16種類である。

| 型 | 基底型 | 用途・上限 |
| :--- | :--- | :--- |
| `block_index_t` | u16 | block indexとinvalid `0xFFFF` |
| `frame_word_offset_t` | u16 | local frame内word offset。現在の最大は640 words未満 |
| `annotation_offset_t` | u32 | annotation pool内のbyte offsetとbyte count |
| `wasm_pc_t` | u32 | WASM PC。96 KiB ROMのPC幅を保持 |
| `code_ip_t` | u32 | 実行中のcode IP |
| `code_offset_t` | u32 | code内offset、JIT blob offset |
| `function_index_t` | u16 | 最大288 function entries |
| `type_index_t` | u16 | 最大256 types |
| `global_index_t` | u16 | `FB_CONF_MAX_GLOBALS=32`内のglobal index |
| `local_count_t` | u16 | 最大256 locals、local slots |
| `operand_words_t` | u8 | operand、parameter、resultのword count。最大128 |
| `frame_depth_t` | u8 | 制御frame深さ。最大32 |
| `instruction_width_t` | u8 | 可変LEB命令のbyte幅。最大255 |
| `jit_stack_words_t` | u8 | JIT traceのstack words。最大128 |
| `jit_body_count_t` | u8 | JIT body命令数。最大64 |
| `card_index_t` | u32 | JIT card index。縮小対象外 |

`interpreter_abi.hxx`の16 typedefに対する`sizeof` static_assert、構造体field offset、Interpreter ABI Python mirrorを検査した。block numberとPCの混在箇所はindexとPCの両方を照合する。ABI layoutは現在、`BasicBlock` 32 B、`FunctionExecutionViewNative` 88 B、`ModuleExecutionViewNative` 128 B、`ExecutionContextABI` 168 B、`CallFrameNative` 48 B、`CallStackNative` 1544 B、`JitTraceExitNative` 20 Bである。

残した`uint32_t`は次の意味を持つ。ここでは対象ABIとネイティブ実装の宣言を名前でまとめ、各演算で使う一時値を列挙しない。

| ファイル | 残した宣言・値 | 理由 |
| :--- | :--- | :--- |
| `interpreter_abi.hxx` | `stack_base`、`stack_pointer`、`stack_limit`、`local_stack_base`、`local_stack_pointer`、`local_stack_limit`、`stack_checkpoint`、`local_base`、`values[]`、`stack_height` | ポインターとWebAssemblyのraw 32-bit stack word。i32のbit patternを保持する。 |
| `interpreter_abi.hxx` | `runtime_flags`、`loop_jump_count`、`loop_jump_threshold`、`trap_code`、`status`、`fault_code`、`flags`、`param_offset`、`result_offset`、`block_execution_counts`、`reserved0` | 実行状態・trap/status値・計数値・署名blob内offset・ABI予約領域。これらに対応する縮小型の契約はない。 |
| `native_abi.hxx` | `stack`、`locals`、`idle_budget`、`stack_size`、`stack_capacity`、`breakpoint_count`、`single_step`、`executed`、`stopped`、`enabled`、`status`、`error_code`、callback `budget` | stack/local pointerはraw stack word領域、他はホストAPIのbudget、状態、診断カウンター。型の縮小上限を持たない。 |
| `native_wasm_operations.hxx/.cxx` | `read_u32`/`peek_u32`、`value`、演算の`result`・`condition`、`offset`、`length`、`trap_code`、CPS exit kind | u32即値やi32のraw bit、WASM32 memory effective offset/length、trap/statusを表す。`table_slot`は入力のi32値を全幅のまま範囲検査する。 |
| `native_wasm_operations.cxx` | `table_index` | Loader契約で0に固定された一時値。注釈には格納しない。 |
| `native_interpreter.cxx` | `trace_count`、`body_count`、`dispatcher_trace_transitions`、`control_handler_count`、`interpreted_block_count`、block-search hit/miss、`eligible_block_visits`、`executed_bodies`、`status`、`trap_code` | 実行全体を通じて255を超え得る診断カウンターと状態値。call stack indexは`frame_depth_t`に変更した。 |
| `jit_types.hxx`、`profiling.hxx`、`jit_runtime.hxx/.cxx`、`profiling.cxx` | profile mask/threshold、history/queue `capacity`・`head`・`count`、cache `entry_count`・region byte size・offset・capacity、`pc_index_count`、`dirty_mask_bytes`、aging cursor/count、`compiled`、`processed`、`block_count`、`module_id`、`budget` | JIT cache構成、byte数、長期カウンター。トレース内stack/body countとは異なり、u8上限を置かず将来の領域拡張を許す。 |
| `trace_compiler.cxx`、`jit_runtime.cxx` | LEB復号`value`、`operand`、`depth`、function/type/table indexの復号scratch、`case_count`、WASM memory `offset`、i32 bit pattern、出力/relocation byte count、LEB byte index | 不正な大きい即値を範囲検査する前の復号scratch、WASM値、または生成blobのbyte数。code cursor/endは`code_ip_t`または`code_offset_t`、JIT stack/body countは対応typedefに変更した。 |

上記以外の生`uint32_t`の出現は、型定義の基底型、コンパイル時定数、C ABIの32-bit値を含む。上限で守られる座標・番号は、該当する意味型へ置換した。

## 記録領域の増減

AO-Bench WASMは937 code bytes、6 defined functions、107 BasicBlocksである。比較前は依頼A適用後のsnapshotで、annotation pool 2944 Bを基準とした。

| 項目 | 変更前 | 変更後 | 増減 | 増減率 |
| :--- | ---: | ---: | ---: | ---: |
| annotation pool | 2944 B | 1712 B | −1232 B | −41.85% |
| BasicBlock records | 3852 B（107×36） | 3424 B（107×32） | −428 B | −11.11% |
| block PC index | 856 B（107×8） | 642 B（107×6） | −214 B | −25.00% |
| radix table | 68 B（17×4） | 34 B（17×2） | −34 B | −50.00% |
| loader arena実測 | 27100 B | 21444 B | −5656 B | −20.87% |

比較用のprofile suiteは14419 code bytes、28 functions、885 BasicBlocksである。

| 項目 | 変更前 | 変更後 | 増減 | 増減率 |
| :--- | ---: | ---: | ---: | ---: |
| annotation pool | 36090 B | 20121 B | −15969 B | −44.25% |
| BasicBlock records | 31860 B（885×36） | 28320 B（885×32） | −3540 B | −11.11% |
| block PC index | 7080 B（885×8） | 5310 B（885×6） | −1770 B | −25.00% |
| radix table | 68 B（17×4） | 34 B（17×2） | −34 B | −50.00% |
| loader arena実測 | 366520 B | 287530 B | −78990 B | −21.55% |

Loader arena全体の差分には、上記4項目に分解できない差がAO-Benchで−3748 B、profile suiteで−57677 B含まれる。これは比較snapshot全体の差であり、列挙したrecord幅の効果へ割り当てない。現在のarena容量に対してAO-Benchは16.36%、profile suiteは14.38%を使用する。

JIT側ではtrace headerが40→32 B（−8 B、−20%）、trace exit metadataが24→20 B（−4 B、−16.67%）となった。`jit_cache_entry`はu32 code offsetを維持するため8→12 B（+4 B）で、72 entry全体では+288 B。72 trace分のheaderとexitを合わせると−864 B、entry増分と差し引いて−576 Bとなる。AO-Benchの6関数では`FunctionExecutionViewNative`が112→88 Bとなり−144 Bである。Loader arena外のJIT recordとfunction viewは、arena削減量とは分けて報告する。

## AO-Bench時間と出力

Release識別子はClang 21.1.8、C++23、`-O3 -DNDEBUG -flto=thin -fuse-ld=lld`である。InterpreterとJITは診断counterなしでbuildした。WAMR Fastはcommit `f5f57c09aee623436f5fb87a90798fdd2cdf39fd`、Release/O3/ThinLTOである。

各値は3独立プロセスの7-call中央値の中央値である。括弧内は3プロセスの中央値範囲である。

| 解像度 | Interpreter ms | JIT ms | WAMR Fast ms | Interpreter/JIT/WAMR frame SHA-256 |
| :--- | ---: | ---: | ---: | :--- |
| 64×36 | 23.533（22.626–23.741） | 23.946（23.842–24.147） | 4.768（4.753–4.821） | `d44fed786dbb555722932e901bd3f56c56aa2daca6c594a67a858c4591e24567` |
| 128×72 | 92.629（91.426–92.685） | 93.392（92.671–93.912） | 19.464（19.073–19.838） | `e9f20b8f0414f7406aa2f3ea96798e1c7755d0148090bf72f8703cffd3d374a4` |
| 192×108 | 200.707（199.873–204.731） | 210.423（205.477–216.807） | 43.021（42.311–43.689） | `9f2df94afaad4a3750053789ce6be52a0b9b14d66d9b8584edf2718d1ac3c4f1` |
| 256×144 | 360.071（358.652–365.858） | 371.914（365.967–373.440） | 76.214（75.929–77.101） | `f35a578dc277b57ca3bb5f74bc9381297fab5afce787bc92c54e46536fa8d2a4` |
| 320×180 | 557.532（556.329–558.227） | 575.044（574.673–577.582） | 119.288（118.882–119.792） | `6d04ea80d9a1fd773c85d413057d64797f32dbb257a753d767a378d730d7d699` |

全解像度でInterpreter、JIT、WAMR Fastの出力hashが一致した。320×180ではInterpreter/WAMR比4.67倍、JIT/WAMR比4.82倍である。annotation pool縮小とABI record縮小は確認できたが、AO-Bench時間は、依頼Aの記録（Interpreter 411.346 ms、JIT 484.947 ms、WAMR 119.411 ms）より大きい。今回との比較ではInterpreterが35.54%遅く、JITが18.58%遅く、WAMRは0.10%速い。別々のsource SHAと依頼A以降の複数変更を含むため、この差をIJだけの因果効果とは言えない。時間改善を示す計測結果ではなく、遅くなった処理も特定できていない。

## 検証結果

- `interpreter_abi.hxx`の16 typedefのサイズ、C++ struct static_assert、Python ABI field offsetsとサイズの検査に合格した。
- 上限ちょうど（288 functions、256 types、256 locals、65535 blocks、32 nesting）を受理するテスト、上限超過を拒否するテストに合格した。
- 253-label `br_table`（元命令幅255 B超）を受理し、65536 labelsを拒否する境界テストに合格した。
- Interpreter、CPS、reader、ABI関連pytestは163 passed。WASM Core Spec trap選択テストは2 passed、36 deselected。
- JIT runtime、exec memory、JIT differentialは133 passed、1 deselected。`jitr_59_clang_kernel_suite_matches_wasmtime_on_the_jit`は独立実行で300秒timeoutとなったため、除外して報告した。
- Interpreter stack formal modelは通常検査と`guards=False` mutation検査の全propertyに合格した。
- C++ `check-src`は対象5ファイルでpass、0 warning。pysim `check-src`は対象ファイルに対する静的規約違反は残らなかったが、規定の`qa/run_all.py`が60秒でtimeoutした。
- 指定文書の`check-doc`は全体traceability/obligationの既存エラーを含みfailした。報告追加後のlink・format結果は`commands.md`に記録した。

## 再実行コマンドと識別子

全コマンドは[`commands.md`](commands.md)、成果物のSHA-256は[`SHA256SUMS`](SHA256SUMS)に記録した。Loaderメモリ計測JSONは[`loader_memory_impact.json`](loader_memory_impact.json)である。最終runは`2026-10-13T08:00:58.862857+09:00`で、benchmark JSON内のsource SHAは`native_interpreter.cxx=70e81e0902178cc24fd11ac8686477bbc6df49b754af52068dd51d7885a4988f`、`native_wasm_operations.cxx=75f9384bffb78045c92091af2192de53d5ecd17fa5b3e211a53870793bf9f2d6`、`trace_compiler.cxx=4e5943360b91a625fa7beba29cec44c94ef66b8dbefc238c3061976db037d588`、`jit_runtime.cxx=b260c4f60832810814729965f71db96f7b127e2ab7320a823b2558c8dbf7fb81`である。

主要SHA-256は以下である。

| artifact | SHA-256 |
| :--- | :--- |
| AO-Bench final result JSON | 後続の`SHA256SUMS`参照 |
| release NativeInterpreter shared library | `ec8e0b94d52822bc738bbb3715418e2d81142cbc54f79e46cae96cd4e8590681` |
| release JIT shared library | `b00e56fa017685ee47ac1b24d2141654a723be0e09be680b5381dc5add081ae8` |
| WAMR Fast shared library | `2a288b7b4e74de2bc26693cb51c6e34b9160f45b4f037a5e9e4deaa8f708a960` |
| AO-Bench WASM | `9f5dc300e96641bf040551f9f56cb6e5bda084e98bde83a3fb1cff9a719d49e9` |

NativeInterpreterのテスト用assert有効buildは計測後に作成した。benchmark JSONにはrelease library SHAが保存され、テスト用artifactは計時に使っていない。
