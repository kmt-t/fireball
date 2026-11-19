# JIT 実行コンテキスト ABI 契約

<!-- evidence:
     contract-only: true
     test: docs/qa/tier3_plugins/jit_runtime_test_spec.md
-->

<!-- traceability: {ContextPointerRegister} {PositionIndependentCode} -->

## 1. コンセプト

本書は、Tier 2 ランタイムと Tier 3 JIT 拡張が共有するネイティブ ABI 契約の正本である。

- **4引数ディスパッチ契約**: Interpreter ハンドラおよび JIT trace entry は、実行状態を表す 3 論理引数（`ctx`, `ip`, `sp`: `{CPS_3Args}`）に、定数・注釈領域ポインタ `rel` を加えた 4 引数を共有する。 <!-- definition: {CPS_3Args} -->
- **構造体レイアウトの正本**: `execution_context` と CallStack の配置は [`interpreter.md`](docs/components/tier2_runtime/interpreter.md) を正本とし、本書は呼び出し規約・トレースヘッダ・Runtime API 記録・拡張接続契約を定義する。
- **対象アーキテクチャ**: 現行 ABI は x64 を対象とし、ARMv8-M の物理配置・ポインタ幅・ABI は TBD とする。

## 2. アーキテクチャ分類

本書は Tier 2 Runtime が所有する契約である。Tier 2 Interpreter と Tier 3 JIT 拡張がこの ABI を利用する。

## 3. 静的モデル

実行状態の構造体正本は [`interpreter.md`](docs/components/tier2_runtime/interpreter.md) の `{ExecutionContext_Layout}`（x64 では 144 バイト）であり、本書ではレイアウトを重複定義しない（`{ContextPointerRegister}`）。 <!-- definition: {ContextPointerRegister} --> インタープリタ単独実行と JIT 遷移で `ctx` の型を変えず、呼び出し側が参照先の寿命を保証する。 `{META_ZeroCostAbstraction}`

### 整数型と座標系

ネイティブ ABI は C 互換 `typedef` を用い、用途ごとに整数型を区別する（16bit 無効値は `0xFFFF`、8bit 無効値は `0xFF`）。

| 型 | 基底型 | 用途と上限根拠 |
| :--- | :--- | :--- |
| `block_index_t` | `uint16_t` | BasicBlock 番号。`0xFFFF` を予約し、最大有効番号 65,534 とする |
| `frame_word_offset_t` | `uint16_t` | ローカルフレーム内ワードオフセット。`2 × (FB_CONF_MAX_LOCALS + FB_CONF_MAX_FUNCTION_ARGUMENTS)` ワードを収容する |
| `annotation_offset_t` | `uint32_t` | 注釈プール内バイトオフセット／長 |
| `wasm_pc_t` | `uint32_t` | WASM module Code section 相対 PC |
| `code_ip_t` | `uint32_t` | 関数コード内の実行 IP・復帰 IP |
| `code_offset_t` | `uint32_t` | コード領域の長さ・オフセット・命令長 |
| `function_index_t` | `uint16_t` | import と定義関数の合計番号を収容する |
| `type_index_t` | `uint16_t` | 型数上限 `FB_CONF_MAX_TYPES` |
| `global_index_t` | `uint16_t` | global 数上限 `FB_CONF_MAX_GLOBALS` |
| `local_count_t` | `uint16_t` | ローカル数およびフレームワード総数 |
| `operand_words_t` | `uint8_t` | オペランドスタック上限 `2 × FB_CONF_MAX_VALUE_STACK` ワード |
| `frame_depth_t` | `uint8_t` | 制御構造の入れ子上限 `FB_CONF_MAX_NESTING_DEPTH` |
| `instruction_width_t` | `uint8_t` | 命令符号化幅（255 バイト超はロード時拒否） |
| `jit_stack_words_t` | `uint8_t` | トレース使用スタックワード数 |
| `jit_body_count_t` | `uint8_t` | JIT trace 命令上限 `FB_CONF_JIT_MAX_TRACE_INSTRUCTIONS` |
| `card_index_t` | `uint32_t` | JIT カード番号 |

Native `ModuleExecutionView` は PC から BasicBlock 番号を引く専用索引を持たない。

### コンテキストと PIC 契約

- **後方辺 yield（`{JIT_BackedgeYield}`）**: <!-- definition: {JIT_BackedgeYield} --> `loop_jump_count` は実際に取得した LOOP 後方辺を一度だけ数え、閾値は `FB_CONF_INTERPRETER_YIELD_THRESHOLD` を正本とする。`if` と `br_if` は Interpreter CPS ハンドラが処理し、`br` は trace 内でスタック移しとチェインを行う（後方 `br` は閾値到達時に CPS exit する）。
- **リニアメモリビュー**: 借用ホスト基点とサイズは `execution_context` の `+0x60` と `+0x68` に配置する。
- **位置独立コード（`{PositionIndependentCode}`）**: <!-- definition: {PositionIndependentCode} --> 共通 chain dispatcher は `lir/x64/common.S` から生成し、trace ヘッダの `common_code_relative` で相対参照する。共通コード領域には Runtime API 関数ポインタ表への 8 バイト借用ポインタだけを置き、trace にはテーブル添字のみを埋め込む（GOT/PLT や絶対アドレスを埋め込まない）。

### x64 継続引数と assembly ABI
<!-- traceability: {CPS_3Args} -->

Interpreter ハンドラと JIT trace entry は `ctx`, `ip`, `sp`, `rel` の 4 引数を受け取る（`{JIT_RegisterMapping}`）。 <!-- definition: {JIT_RegisterMapping} -->

- **SysV AMD64**: `rdi`, `rsi`, `rdx`, `rcx`
- **Windows x64**: `rcx`, `rdx`, `r8`, `r9`
- **WASM 仮想レジスタ**: 導入しない。ABI probe が本番 C++ 型と構造体オフセットを照合する。

### x64 トレースヘッダと境界レイアウト

x64 の常駐トレースヘッダ（32 バイト）のオフセット定義を以下に示す。trace 本体は `+0x20` から開始する。

| オフセット | フィールド | サイズ | 用途 |
| :--- | :--- | ---: | :--- |
| `+0x00` | `chain_target_addr` | 8 バイト | chain dispatcher が読み取る次 trace body の機械語アドレス（未接続時は `0`） |
| `+0x08` | `common_code_relative` | 4 バイト | ヘッダ位置から PIC 共通コード基点への符号付き相対オフセット |
| `+0x0C` | `head_pc` | 4 バイト | キャッシュ常駐 entry の WASM 開始 PC |
| `+0x10` | `blob_bytes` | 4 バイト | ヘッダから定数領域末尾までの配置サイズ |
| `+0x14` | `stack_words` | 1 バイト | 当該トレースが書き込む最大スタックワード数 |
| `+0x15` | `chain_words` | 1 バイト | chain 全体が必要とする最大スタックワード数 |
| `+0x16` | `chain_body_count` | 1 バイト | root から終端までに含む trace body 数 |
| `+0x17` | `frame_depth` | 1 バイト | ブロック開始位置の制御フレーム深さ |
| `+0x18` | `exit_kind` | 1 バイト | trace 終了種別（`0`: normal、`1`: call_cps、`2`: terminator_pending） |
| `+0x19` | 予約 | 1 バイト | 2 バイト境界パディング |
| `+0x1A` | `block_index` | 2 バイト | Loader の `block_index_t`（`0xFFFF` は無効） |
| `+0x1C` | `const_rel_offset` | 4 バイト | ヘッダ先頭から trace 定数領域先頭までの符号なし相対オフセット |

#### 境界メタデータと Runtime API 記録

- **境界メタデータ（20 バイト）**: trace 末尾と定数プールの間に配置する。詳細は [`jit_compiler.md`](docs/components/tier3_plugins/jit_compiler.md) を正本とする。
- **Runtime API 記録形式**: `u8 slot`, `u8 即値個数`, `u8 即値幅和`, `u32 code_ip_t`, `即値列`, `u32 再開位置`（注釈プールオフセット）の順に並ぶ。終端 API 記録では先頭即値に命令の注釈開始位置、末尾に実行後の再開位置を置く。
- **チェイン時の制御受け渡し**: 共通 chain dispatcher は連結先の `const_rel_offset` から `rel` を初期化して tail-jump する。未接続時は境界メタデータポインタを `execution_context.rel` に、再開 IP を `execution_context.ip` に設定して `true` で戻る。

## 4. 動的モデル

### JIT ランタイム呼出し契約

Tier 2 Interpreter は、ブロック遷移の判定点において対象ブロックの `jit_cache_reference`（1 バイト）のみを読み取り、JIT 拡張の呼出し要否を決める。

| `jit_cache_reference` 値 | 意味 | Interpreter の動作 |
| :--- | :--- | :--- |
| `FB_NATIVE_JIT_NO_TRACE_OBSERVED`（`0xFF`） | trace なし（要観測） | 観測コールバックを呼び、Interpreter を実行する |
| `FB_NATIVE_JIT_NO_TRACE_UNOBSERVED`（`0xFE`） | trace なし（観測不要・既定値） | JIT 拡張を呼ばず Interpreter を継続する |
| 上記以外の値 | 常駐 trace のスロット番号 | `code_base + trace_offsets[slot]` の trace を実行する |

- **Provider 接続契約**: `trace_entry_address`、`code_base`、`trace_offsets` は接続時に一度だけ設定し、実行中に変更しない。観測コールバックの戻り値（`0` または `1`）および境界処理コールバックの戻り値（`1`）に違反した場合は、呼出し直後で即座に `assert` 停止する。
- **状態とレイアウトの非重複**: `execution_context`・CallFrame・CallStack・値スタックの配置は [`interpreter.md`](docs/components/tier2_runtime/interpreter.md) を正本とする。境界を跨ぐデータ参照には標準レイアウト構造体のみを用い、`std::vector` や仮想関数・例外を含めない（`{META_NoStdVector}`）。
- **ローカル操作の解決**: JIT の `local.get/set/tee` はコンパイル時に Loader のオフセット表からワードオフセットを解決し、命令へ直接書き込む。

## 5. インターフェース定義

### 委譲シグネチャ

- **JIT trace entry**: `bool (execution_context*, const uint8_t*, uint32_t*, const uint8_t*) noexcept`
  - `true`: 通常完了（未接続 chain 終端時は `execution_context` に境界メタデータと `body_end_ip` を設定済み）。
  - `false`: trap または CPS exit（終了理由は `execution_context` へ記録済み）。
- **Runtime API**: `bool` を返し、`true` なら後続 JIT operation へ継続し、`false` なら abort slot へ分岐する（`{JIT_RuntimeAPI}`）。 <!-- definition: {JIT_RuntimeAPI} --> Interpreter と JIT の opcode/API テーブルは独立させる。

## 6. 制約達成の方策

ABI は標準レイアウト、固定サイズ、固定オフセットを維持する。x64 以外のポインタ幅とレイアウトは未確定であり、確定前に他アーキテクチャとの互換性を主張しない。

## 7. 形式検証・テスト仕様との対応

### 7.1 検証対象の不変条件
本書で定めた状態、境界、所有権、およびエラー処理を検証対象とする。

### 7.2 検証モデルと反証可能性
独立した形式検証モデルは設定しない。x64 ABI の物理サイズ、オフセット、呼び出し結果はテスト仕様と実行テストで検証する。

### 7.3 テスト仕様書との連携
対応するテスト仕様は [jit_runtime_test_spec.md](docs/qa/tier3_plugins/jit_runtime_test_spec.md) である。テストケース ID と実行可能テストは同仕様を正本とする。

### 7.4 既知の制限・対象外
ホスト実機依存の挙動、未実装アーキテクチャ、およびテスト仕様が明示する対象外条件は未検証として扱う。

## 8. 設計判断と参考実装

特記すべき独立した ADR はない。採用方針は本書の各契約節に記載する。
