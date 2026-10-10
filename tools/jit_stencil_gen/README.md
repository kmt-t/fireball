# JIT ステンシル生成器

## 入力と担当

| パス | 担当 |
| :--- | :--- |
| [`operations.S`](../../experiments/pysim/native/tier3_plugins/jit/lir/x64/operations.S) | x64 の操作ステンシルを記述する。直接実行する演算、型別 local template、融合 variant、Runtime API 呼出しを定義する。即値slotとlocal instruction slotを明示する。 |
| [`trace.S`](../../experiments/pysim/native/tier3_plugins/jit/lir/x64/trace.S) | trace entry、出口、trap 経路を記述する。 |
| [`common.S`](../../experiments/pysim/native/tier3_plugins/jit/lir/x64/common.S) | 共通コード領域の chain dispatcher を記述する。 |
| [`generate_assembly.py`](generate_assembly.py) | アセンブリを PIC object にし、`.text` の raw binary、C++ byte array、symbol・分岐 metadata を生成する。 |
| [`abi_probe.cxx`](abi_probe.cxx) | 本番 C++ 宣言を対象 ABI でコンパイルし、関数型と実行時構造体の offset を検証・抽出する。 |

JIT の Linux/WSL と Windows の build script は同じ生成器を三度実行し、`trace.S`、`operations.S`、`common.S` の生成物を build 用ディレクトリへ出力する。LLVM IR、LLVM IR 生成器、別個の common-code 生成器は使わない。

## 生成手順

`generate_assembly.py` は各入力に対し、次の順で処理する。

1. `abi_probe.cxx` を Clang C++ で対象 ABI 向けにコンパイルする。関数型の `static_assert` と `.fb_abi` section から本番 ABI の構造体 offset を取得する。
2. Clang integrated assembler で入力 `.S` を PIC object にする。ABI probe と Tier 1 構成から得た値を assembly macro に渡す。
3. `objdump` で relocation を検査し、`.text` に残る relocation、GOT、PLT、絶対64-bit relocation を拒否する。
4. `objcopy` で `.text` を raw binary に抽出し、`nm` と `objdump` から symbol 範囲、接続 marker、即値slot、local instruction slot、内部分岐の再配置 metadata を得る。
5. raw binary と metadata を `.bin` と C++ `.hxx` に出力する。C++ 配列は native JIT build に含まれる。

生成された操作列の成功接続 marker は trace compile 時に次の操作本体で置き換わる。現行実装は即値slotにコンパイル時に確定した数値を書き、local instruction slotに型とcompact offsetに応じたx64 memory instructionを書き込む。完成後にこれらの命令byteは書き換えない。内部条件分岐は合成後の位置に合わせて C++ trace builder が再配置する。生成コードは trace 間の接続でのみ共通 chain dispatcher を使う。

`rel`読み出しはデータロードとカーソル更新を伴う一方、trace codeを小さくできる場合がある。現行trace compilerは即値を命令へ埋め込み、`rel`読み出し方式は未実装である。追加する場合は代表traceの実測性能とコードサイズを比較して採否を決める。

## 単独生成

build script と同じ三つの role を個別に実行できる。

```bash
python3 tools/jit_stencil_gen/generate_assembly.py \
  experiments/pysim/native/tier3_plugins/jit/lir/x64/operations.S \
  --role operations --output-dir /tmp/jit-stencils \
  --header asm_stencils.hxx --binary asm_stencils.bin \
  --namespace asm_generated

python3 tools/jit_stencil_gen/generate_assembly.py \
  experiments/pysim/native/tier3_plugins/jit/lir/x64/trace.S \
  --role trace --output-dir /tmp/jit-stencils \
  --header stencils.hxx --binary stencils.bin --namespace generated

python3 tools/jit_stencil_gen/generate_assembly.py \
  experiments/pysim/native/tier3_plugins/jit/lir/x64/common.S \
  --role common --output-dir /tmp/jit-stencils \
  --header common_code.hxx --binary common_code.bin --namespace generated
```

通常の JIT build は Linux/WSL の `build_native.sh` または Windows の `build_native.ps1` から生成器を実行する。別の target ABI には、対応する assembly source と ABI 契約が必要である。

## ABI と PIC 契約

- Trace entry は `ctx`, `ip`, `sp`, `rel` の四つの論理引数を受け取る。`operations.S` は SysV AMD64 と Windows x64 の物理引数レジスタを明示する。
- assembly source が一時レジスタと呼出し規約を明示する。各操作は成功接続 marker を除いて他の操作へ call/jump しない。
- Runtime API 用ステンシルは共通コード領域の API table から該当 API を呼び、API が通常継続を返した場合は後続の合成済み操作へ進む。
- `abi_probe.cxx` が本番 C++ ABI の関数型と構造体 offset を検査し、必要な offset を assembly 定義として渡す。
- 生成物は PIC とし、GOT、PLT、絶対アドレス、未解決 relocation を持たない。Runtime API table と trace header の相対参照は実行時に解決する。

## 生成物

- `asm_stencils.bin` / `asm_stencils.hxx`: 操作ステンシルの raw code、byte array、variant・即値slot・命令slot・分岐 metadata。
- `stencils.bin` / `stencils.hxx`: trace entry・出口ステンシルとその metadata。
- `common_code.bin` / `common_code.hxx`: 共通 chain dispatcher の raw code と配置 metadata。
- `*.preprocessed.S`: `--assembly-output` を指定したときの、ABI 定義を適用した assembly source。

生成 object は一時ディレクトリで削除される。実行時共有ライブラリには LLVM runtime や IR payload を含めない。

生成には Clang 17 以降、Clang C++、および `objcopy`、`objdump`、`nm` が必要である。LLVM 配布物の `llvm-objcopy`、`llvm-objdump`、`llvm-nm` と GNU binutils のどちらも使える。
