# Native実行系リファクタリング作業一覧

更新日: 2026-10-12。
追加依頼を本一覧へ登録し、実装、検証、計測の順に進める。
優先度P0は正当性・契約と未達の性能目標、P1は責務・型・配置、P2は検証の同期を示す。
未達の性能目標は、コード変更やビルド成功だけで完了扱いにしない。

<!-- traceability: {ThreadedInterpreter} {LowLatencyJIT} {JIT_MultiBuffer_Cache} {JIT_OldestExecuteOnly} {GOTCHA-LOAD-02} {GLOBAL_Policy_Memory} -->

| ID | 優先度 | 依頼 | 状態 | 完了条件 |
| :--- | :--- | :--- | :--- | :--- |
| NR-01 | P0 | 不正WASMはtrap、内部契約は早期assertに分ける | 検証済み | assert無効時の不正入力trap、内部assertの原因保持、アリーナ復元を検証する |
| NR-02 | P0 | 防衛的分岐、重複検索、assert専用走査を減らす | 実施・検証済み、残存費用はNR-12で追跡 | 契約の確定位置を明示し、下流の回復経路と重複処理を削る |
| NR-03 | P1 | 責務、型名、メンバー名、変数名を整理する | 実施・検証済み | キャッシュ、観測・候補処理、provider接続、命令意味論の状態と手続きを揃える |
| NR-04 | P1 | ROMに置くべき不変データの配置を直す | 不変化・Linux配置検証済み | 不変表の構築・保持・配置を確認し、可変状態と分け、ホストビルドの読み取り専用配置を検査する |
| NR-05 | P1 | `load_jit_immediate`をテンプレートにする | 検証済み | 値型と固定位置をテンプレート引数とし、メモリ命令とbulk memoryを回帰検証する |
| NR-06 | P1 | C++へDoxygenを追加し、実装ヘッダを廃止する | 実施・検証済み | 宣言・型をヘッダへ、手続きを翻訳単位へ置き、実装ファイルのincludeをなくす |
| NR-07 | P1 | 制御文の波括弧を省略しない | 実装済み | 規約とClang-Format設定を揃え、変更C++を整形する |
| NR-08 | P1 | Active/Warm/Oldestを各24件にし、昇格を廃止する | 検証済み | 合計72件、Oldestは実行のみ、新規chain先にしない、退役時に既存chainを解除する |
| NR-09 | P1 | 72件のchain逆引き表でコピー・リンクを軽くする | 検証済み。表単独の速度改善は未確認 | 逆引き候補のPCと実アドレスを照合し、リンク・アンリンクの副作用を検証する |
| NR-10 | P1 | x64 Linux/Windowsの実行権限を初回に設定し、保護切替手続きと経緯を残す | Linux検証済み | OSの権限と書き換え後の実行結果を確認する。Windows実行は別環境で確認する |
| NR-11 | P1 | `min_trace_bytes=1`を修正する | 実装・検証済み | 製品初期化をカード幅に揃え、短い非候補ブロックの除外を保つ |
| NR-12 | P0 | O2/O3と処理時間を同じ条件で測り直す | 計測済み、半減目標未達・費用分解を継続 | 同じソース、NDEBUG、入力、CPU、出力hashで比較する。JIT時間がInterpreterの半分以下かを明示する |
| NR-13 | P2 | ルール・スキルと仕様・形式モデル・テストを同期する | 変更同期・静的ゲート検証済み | 関連テスト、ASan/UBSan、変異検査、静的ゲートを確認する |
| NR-14 | P0 | 現行Interpreter・JITをWAMRと比較する | 計測・出力検証済み | 同じWASM、O3、CPU、warmup、試行数で再計測し、全出力hashと時間比を記録する |
| NR-15 | P0 | フレームポインタ省略オプションの効果を確認する | A/B・出力・生成コード検証済み。Linux既定で省略指定あり | 省略・保持以外を揃えたA/Bで時間、出力hash、生成コードを比較する |
| NR-16 | P0 | Core Specの`left-to-right`失敗を直す | 実装・回帰・形式検証済み | 混在シグネチャのテーブルを実行でき、選択先の型不一致はtrapとなることを検証する |

## 現在の優先作業

1. NR-12の処理費用を分解する。最新のWAMR比較は320×180でInterpreter 484.928ms、JIT 568.319ms、WAMR Fast 119.919msだった。JITはInterpreterより17.2%遅く、処理時間の半減は未達である。
2. WAMR Fastがロード時に行う`local.get`の消去と定数の位置解決を参考に、Fireballに残る命令とRuntime API境界費用を調べる。dispatcher、未常駐ブロックの検索・観測費用も分解する。候補の寄与率は未測定であり、キャッシュのRAM予算を増やすだけの変更は性能改善の根拠にしない。
3. Linuxで検証した実行権限設定をWindows環境でも確認する。ARMv8-Mの物理配置と予算は別に確認する。

## 確認済みの証拠と制限

- 読み取り専用表の借用を含むASan/UBSan回帰は196件通過した。構築専用constructorの削除後は関連JITテスト128件が通過した。
- NR-16修正前のLoader、Interpreter、JIT、Gotchas、統合、選択Core Suiteの直接回帰は337件通過、1件失敗だった。
- `left-to-right`の失敗原因は、MVPに対して過剰な全テーブル要素の型制約だった。NR-16で選択先の実行時照合へ修正した。Python参照とNativeで全95アサーションが成功した。
- trapコードとログ辞書の同期を修正し、直接の同期テストが通過した。pysim静的ゲートは30ファイル、0警告で通過した。C++の検査対象をNativeと共有ABIへ広げ、21ファイル、0警告で通過した。
- JIT形式モデルは正常系12性質、`guards=False`、aging単独変異を確認した。抽象W^X性質はx64のRWX権限を証明しない。
- C++モジュール分割で残ったRuntime API呼出しをLTOで融合し、生成コードから余分な直接呼出しが消えたことを確認した。
- O2/O3比較は同じソース、NDEBUG、入力、CPU 2固定、3独立プロセス、各3 warmup・7 timed callsで行った。全出力hashが一致した。
- 最終値はO2がInterpreter 19.707ms・JIT 22.988ms、O3がInterpreter 19.791ms・JIT 23.090msだった。O3の差はプロセス間のばらつきより小さく、倍速は再現しなかった。
- API関数表の各キャッシュへの1,472Bの複製を8Bの借用参照へ置き換えた。独立した二つのキャッシュが同じ読み取り専用表を借用することを確認した。コード領域の予約量は8KBである。
- 文書ゲートは0エラー、60警告、44/44検証義務で通過した。
- 読み取り専用のNative表6個をLinuxの実マッピングで確認した。pointer再配置後の表も`r--p`だった。物理ARM ROM配置の証拠にはしない。
- ARMv8-Mの物理ROM/RAM適合とWindowsでの実行結果は、このLinux参照環境の結果から換算しない。
- NR-14は5解像度、各3独立プロセス、3準備実行・7計時、CPU 2固定、O3・NDEBUG・ThinLTOで比較した。全45プロセスの出力hashが一致した。WAMR FastはInterpreterより4.03〜4.17倍、JITより4.73〜4.90倍速かった。
- NR-15は64×36と320×180で省略・保持をA/B比較した。320×180では省略によりInterpreterが2.55%、JITが6.68%短かった。省略は既存ビルドで有効であり、追加改善とは扱わない。出力hashと、生成したJIT stencilが両条件で一致した。
- NR-16の関連回帰は181件成功した。Python参照の選択Core Specセット37ファイル、15,230アサーションも成功し、skipは0件だった。直接回帰18件はASan/UBSanでも通過した。公式Core Suite全体の適合は主張しない。

計測データは[`native_runtime_o2_o3_20261012.json`](experiments/pysim/benchmarks/results/native_runtime_o2_o3_20261012.json)、配置検査は[`native_runtime_readonly_20261012.json`](experiments/pysim/benchmarks/results/native_runtime_readonly_20261012.json)に保存した。
最新WAMR比較は[`aobench_interpreter_jit_wamr_20261012_refactor.json`](experiments/pysim/benchmarks/results/aobench_interpreter_jit_wamr_20261012_refactor.json)に保存した。
フレームポインタ比較は[`native_frame_pointer_ab_20261012.json`](experiments/pysim/benchmarks/results/native_frame_pointer_ab_20261012.json)に保存した。
