# 注釈プールと整数型の事前試算

試算は実装変更前に、現行Loaderの実サイズと、各命令種別に対する提案レコード長から算出した。入力WASMと現行ソースはSHA-256を`pool_estimate.json`に記録した。AO-Benchのほか、リポジトリ内で実際に使う大きなWASMであるprofile guest suiteを対象とした。suiteは現行arena上限より大きいため、試算時だけ2,000,000 byteのarenaを使った。

| モジュール | 命令数 | ブロック数 | 注釈プール現状 | 提案 | 削減 | 削減率 | Loader arena現状 | 提案arena | arena削減 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| AO-Bench | 510 | 107 | 2,944 B | 1,720 B | 1,224 B | 41.58% | 27,100 B | 25,414 B | 1,686 B |
| profile guest suite | 6,573 | 885 | 36,090 B | 20,479 B | 15,611 B | 43.26% | 366,520 B | 347,335 B | 19,185 B |

BasicBlock descriptorは36 Bから32 Bへ縮む想定で、AO-Benchは428 B、suiteは3,540 B減る。block-index entryは`uint32_t folded_pc + uint16_t block_index + 16-bit padding`で自然alignmentにより8 Bのまま残る。radix tableの各値は最大ブロック数以下なのでu16化でき、17要素を持つ両モジュールで68 Bから34 Bへ縮む。JIT境界metadataは既存24 B recordのblock-index欄をu16にして予約領域を広げるため、recordサイズは24 Bのまま。

`br_table`はu32のラベル数と、ラベルごとのu8 lexical frame index・u16 target block indexを記録する。`br_table`は必ず分岐するため命令幅byteを持たない。大きなラベル表に対する個別の可変長幅形式は不要であり、ラベル数は元WASMコードの長さに制約される。通常のLEB128即値を持つ命令の符号化幅はu8で保持する。固定幅命令には幅recordを作らず、opcodeから幅を得る。

上限確認の根拠は`FB_CONF_MAX_IMPORTS=32`、`FB_CONF_MAX_FUNCTIONS=256`、`FB_CONF_MAX_TYPES=256`、`FB_CONF_MAX_LOCALS=256`、`FB_CONF_MAX_VALUE_STACK=64`、Interpreterの`FB_CONF_MAX_NESTING_DEPTH=32`、JITの`kMaxTraceInstructions=64`、cache内の最大72 trace entriesである。これらはu8/u16の範囲内にある。WASM PCとinstruction pointerは96 KiB ROM上限を扱うためu32のままとする。card indexも最大16 WASM page・4-byte cardに対してu32のままとする。

コマンド:

```bash
cd experiments/pysim
PYTHONPATH=.:tier1_core:tier1_interface:tier3_plugins:tier3_platform:qa/private \
  /home/t-matsu/workspace/mysrc/fireball/.venv/bin/python \
  benchmarks/results/annotation_types_20261013/estimate_pool.py
```

これは計数用のLoader式試算であり、実装後の計測結果ではない。
