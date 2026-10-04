# Runtime 観測イベント テスト仕様書

## 1. 目的と対象範囲

正本: [`runtime_observability.md`](docs/components/tier2_runtime/runtime_observability.md)

本仕様は、Runtime Event Sink のイベント記録、固定幅レコード、C++/Python転送ABI、構成選択、および安全点での
配送を検証する。JIT履歴は本仕様のイベント契約に含めない。

## 2. テストケース一覧

| テストケースID | 検証項目 | 前提条件 | 手順 | 期待結果 |
| :--- | :--- | :--- | :--- | :--- |
| TEST-OBS-01 | RuntimeEventV1 の固定レイアウト | 対象C++ ABIが選択済み | `sizeof`、`alignof`、各フィールドの`offsetof`を確認する | レコードが32バイト、8バイト境界であり、全フィールド位置が契約と一致する |
| TEST-OBS-02 | 意味上のイベントと識別子 | Sink有効構成でモジュール登録、関数呼出、JIT移行、ホスト呼出、yield、trapを実行する | 各境界で生成したレコードを読み出す | イベント種別・識別子・相関ID・終了理由が契約どおりであり、命令ごとのイベントを生成しない |
| TEST-OBS-03 | Runtime Event Sink の容量超過 | 容量Nのイベントリングを初期化し、N+K件を記録する | リングを安全点でエクスポートする | 先に記録したN件を順序どおり保持し、後続K件を破棄し、累積`dropped_count`へKを加える |
| TEST-OBS-04 | Versioned batch ABI の復号 | 既知イベント列をリングへ記録する | little-endian batchをPython Adapterで復号する | ヘッダ、レコード数、clock metadata、およびPython値がABI契約と一致し、C++レコード配置をPython APIへ露出しない |
| TEST-OBS-05 | 不足バッファとバージョン拒否 | 未出力イベントがリングに存在する | 容量不足、未対応ABI major、不正ハンドルを指定してexportする | 状態コードと必要サイズを返し、成功以外ではリングの読出位置を変更しない |
| TEST-OBS-06 | 安全点での一括配送と所有権 | Runtimeがイベントを記録して実行中、または停止境界にある | 実行中と停止点の両方でexportを要求する | 実行中の要求を拒否し、安全点では一括出力する。出力バッファを保持せず、Python callbackを実行ホットパスから呼ばない |
| TEST-OBS-07 | Runtime構成とゼロオーバーヘッド | Event Sink有効・無効のRuntime型を同一プログラムで生成する | 両方を実行し、型構成・生成コード・ROM/RAMを比較する | 2構成が共存する。無効構成にはSink、リング、時計、発行経路が含まれない |
| TEST-OBS-08 | JIT拡張からの独立性 | Event SinkとJIT拡張を独立に有効・無効化したRuntime構成を用意する | 全有効組合せでJIT履歴とイベント履歴を確認する | JITの記録・容量超過・無効化がRuntimeイベントの状態や実行経路を変更しない |

## 3. 判定条件

各テストは、イベント値とABIバイト列、Runtimeの実行結果、容量超過件数、生成コード、および資源量を直接比較する。
Sinkの有効・無効で戻り値、trap、yield、ゲスト可視状態が変化した場合は不合格とする。

## 4. 対象外

- Guest Profilerのコールグラフ・時間集計。詳細は [`guest_profiler_test_spec.md`](docs/qa/tier3_plugins/guest_profiler_test_spec.md) に置く。
- 基本ブロック単位のPC履歴とカード更新。詳細はTier 3 JIT拡張の [`jit_runtime_test_spec.md`](docs/qa/tier3_plugins/jit_runtime_test_spec.md)「JIT拡張ホットスポット履歴」に置く。
- 外部ログの書式、UI、ホストI/O、およびABIに含めない診断用snapshot。
