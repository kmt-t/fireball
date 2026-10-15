# 組み込み資源予算と実装規模の見積もり仕様書
<!-- traceability: {Resource_Estimation_Model} -->

## 1. 目的と評価範囲

<!-- traceability: {Resource_Estimation_Model} {Size_20KSLOC} {GLOBAL_StrictMemoryLimit} {ConsolidatedHeap} {ROMParsing} {META_ZeroCostAbstraction} -->
本書は、組み込み実装のROM / RAM予算とソース規模を見積もる方法を定める。容量条件の正本は [`requirement_list.md`](docs/requires/requirement_list.md) とする。

最小構成はCortex-M33 / RAM 32KB / ROM 96KB、想定構成はCortex-M33 / RAM 64KB / ROM 128KBとする。最小構成の静的合計21KBと物理RAM 32KBの適合を確認する。容量計算では21KBを21,504バイト、32KBを32,768バイト、96KBを98,304バイト、128KBを131,072バイトとして扱う。

見積もりは対象ABI、ボード構成、同時実行数、メモリ配置、リンカmapを固定して行う。参照実装やホスト計測の値から、組み込み対象の適合を推定しない。

## 2. 計測方法と計上範囲

<!-- traceability: {Runtime_BumpAllocator} {META_BumpAllocator} {Resource_Estimation_Model} -->
資源は生存期間と所有者ごとに分けて計上する。アリーナ、実行領域、静的領域、呼出しworkspace、stack、共有領域を区別し、同じ記憶域を複数の区分へ重複計上しない。各領域では要求容量に加えて、アラインメントによる空きと配置単位への丸めを含める。

ランタイムが所有する領域は、ロード、インスタンス生成、呼出し、コンパイル、実行、退役の各段階で必要な容量を整理する。呼出しごとに再利用するworkspaceは同時生存数に基づく最大値を計上する。固定容量のキャッシュや履歴領域は設定容量を一度だけ計上し、内部の配列やrecordを重ねて加算しない。

| 領域 | 計上の基準 |
| :--- | :--- |
| WASM解析metadata | 入力内の型、関数、import/export、global、table、block等の要素数と対象ABI上のrecordサイズ |
| インスタンス状態 | 実体のglobal、table、linear memory、および初期化時に必要な領域 |
| 実行metadata | module、function、block、index、execution context、call frameの個数と対象ABI上のサイズ |
| JIT領域 | 管理状態、profile情報、trace cache、共通コード、コンパイル一時領域、実行領域をそれぞれ一度計上 |
| OS・driver領域 | TCB、IPC、共有heap、driver、logger、debugger、native stack、interrupt stackの構成別容量 |

全体RAMは、同時に生存する各所有領域の合計と共有領域から求める。WASMの論理linear memoryと、その内容を置く物理領域の容量を分ける。ROM上の入力を直接参照する場合は解析metadataだけをRAMに計上し、書込み可能領域や初期化後の実体は設計された配置先へ計上する。

## 3. ランタイムメモリの計上

<!-- traceability: {Resource_Estimation_Model} {Runtime_BumpAllocator} {JIT_MultiBuffer_Cache} {GLOBAL_StrictMemoryLimit} -->
各入力について、Interpreter単独、JIT有効、最大同時実行数の各構成を個別に評価する。正常系と失敗系の両方で、割当て、rollback、再利用、退役後の解放を確認する。評価結果は入力、対象ABI、構成値、最大使用量、反復条件とともに記録する。

アリーナの使用量には、確保したrecordとalignment paddingを含める。解放後に予約量が縮まらない領域は、同じ実行期間で到達する最大offsetを使用量とする。JITが要求する実行領域はアリーナと分け、要求サイズとアラインメントを一度だけ加える。構造体内部の領域を構造体全体に重ねて足さない。

## 4. 対象実装の配置で確定する領域

<!-- traceability: {Resource_Estimation_Model} {ConsolidatedHeap} {GLOBAL_StrictMemoryLimit} {ROMParsing} -->
全体RAMは、各ランタイムの予約量、linear memory、JIT領域、OS・driver専有領域、および共有領域の合計とする。対象実装では次の項目を確定する。

- ARMv8-Mの管理構造体、ポインタ幅、アラインメント、静的容量を計上する。
- JIT実行領域の保護方式とMPU境界による空きを計上する。
- コンパイル時stackを同時に生存する実行時領域へ加える。
- TCB、IPC、共有heap、driver、logger、debuggerを構成ごとに計上する。
- Native機械stack、OS stack、割込みstackの各上限を計上する。
- WASMバイナリをROMから参照する場合、そのバイト列をROM側へ計上する。

最終的な予約容量は、対象入力、同時実行数、設定容量、対象ABIに基づいて決める。最小構成のRAM容量を超える構成を採用しない。対象board、ARMv8-M ABI、JIT命令生成、メモリ保護方式、linker配置が未確定の間は容量適合を確定しない。

## 5. JITコンパイル時のRAM

<!-- traceability: {Resource_Estimation_Model} {GLOBAL_StrictMemoryLimit} {JIT_AssemblyStencilCodegen} {JIT_RuntimeAPI} -->
コンパイル時のpeak RAMには、生成code、decode staging、operation view、定数record、stack model、再配置情報、および呼出し階層のstack frameを含める。compile中に同時に生存する領域をcall graphとlifetimeから特定し、別段階の一時領域を同時使用として誤算しない。

出力容量、decode対象の上限、定数record数、論理stack深さはJIT componentの正本で定める。生成codeとcompiler staging領域は所有者と寿命を分け、同一storageを複数回計上しない。コンパイル中に別のassemblerやlinkerを起動する構成では、その実行環境の追加RAMも別途評価する。

最終評価では、対象ABIの最適化buildからstack usageを取り、全calleeと割込み条件を含む最大経路を確認する。特定host向けのstack計測値を別ABIへ換算しない。

## 6. ROM容量とWASM入力

<!-- traceability: {Resource_Estimation_Model} {ROMParsing} {JIT_AssemblyStencilCodegen} -->
ROM上限は最小構成で98,304バイト、想定構成で131,072バイトとする。ROM使用量にはfirmwareのcode、read-only data、初期値付きdataのload image、常駐WASM入力、その他の固定資産を含める。複数のWASM moduleをROMへ置く場合は、同時に配置する全入力byte lengthを加える。

RAMの`.bss`、実行時に生成するcode、debug情報、再配置情報などはROM load imageとの重複を避け、対象linker mapの各sectionに従って分類する。対象CPUのlinker mapがないhost用機械語サイズを、組み込みROM容量へ換算しない。

## 7. 実装規模

<!-- traceability: {Size_20KSLOC} {Resource_Estimation_Model} -->
SLOCは空行、コメント、docstringを除いた製品コード行数とする。C/C++は字句解析でコメントを除き、プリプロセッサ指令と文字列中のコメント記号はコードとして数える。計測器の版、ファイル一覧、各ファイルのhashを結果に保存する。

`third_party/`、QA、概念コード、形式モデル、benchmark、scenarioは製品SLOCの対象外とする。20,000 SLOC制約の判定は、firmwareへ実際に含めるファイル一覧とbuild構成を固定して行う。ソース行数からROM/RAM容量を外挿しない。

## 8. 適合判定

<!-- traceability: {Resource_Estimation_Model} {GLOBAL_StrictMemoryLimit} {Size_20KSLOC} {ROMParsing} -->
最小構成では、要件に定める静的合計21,504バイトとRAM 32,768バイトの関係を確認する。差分は名目上の余裕であり、未計上領域を含む実行時空き容量の確定値ではない。

適合判定には、構成別の静的領域、同時生存するworkspace、linear memory、JIT領域、各種stack、driver、IPC、ROM load imageを重複なく計上する。ROM/RAM両方について、対象ABIと実buildのlinker mapを保存する。すべての要素を含む測定が完了するまで、ARMv8-Mの適合は未判定とする。

## 9. Interpreter BasicBlock index metadata

<!-- traceability: {Resource_Estimation_Model} {GLOBAL_StrictMemoryLimit} -->
BasicBlock indexはLoader metadataとInterpreterの検索領域の両方へ影響する。見積もりでは、metadataの保存場所、indexのentry数、参照関係、および同じ情報を別領域へ複製していないことを確認する。

## 10. 注釈poolと座標型

<!-- traceability: {Resource_Estimation_Model} {GLOBAL_StrictMemoryLimit} {Loader_BasicBlockIndex} -->
Interpreter注釈と座標indexは、対象入力の命令数、block数、関数数、各fieldの幅から容量を見積もる。field幅、alignment、検索用indexをそれぞれ分けて記録し、削減前後の結果を比較する際は同一入力と同一ABIを使う。

## 11. JIT定数pool

<!-- traceability: {Resource_Estimation_Model} {GLOBAL_StrictMemoryLimit} -->
JIT定数poolの生成量と同時常駐量は別々に見積もる。生成した全traceの累積byte数をpeak RAMとして扱わず、cache容量、occupancy、eviction、compiler stagingの同時生存量を個別に確認する。

## 12. `br_table` metadata

<!-- traceability: {Resource_Estimation_Model} {GLOBAL_StrictMemoryLimit} {Loader_BasicBlockIndex} -->
可変長の分岐metadataは、label数、default entry、各target recordの幅から容量を計算する。容量見積もりの式と実書込量が一致することを検証し、入力ごとの件数を記録する。

## 13. Interpreter実行metadataとruntime frame

<!-- traceability: {Resource_Estimation_Model} {GLOBAL_StrictMemoryLimit} {Loader_BasicBlockIndex} -->
Interpreter実行metadata、execution context、call frame、call stackは、関数数と最大同時呼出し深さを基に計上する。共有される領域とframeごとに増える領域を分離し、Loader arena外のruntime metadataをarena使用量へ混ぜない。機能削減後のROM/RAM効果は、対象buildの前後mapと同じ入力で比較する。
