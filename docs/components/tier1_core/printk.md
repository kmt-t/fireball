# printk 診断出力契約

<!-- traceability: {META_3TierSeparation} -->

## 1. 目的

`printk` は、COOS、IPC、Tier 2 Runtimeロガーが利用できない起動初期や障害時にも診断情報を出力するTier 1契約である。物理出力先への同期書込みを提供し、COOS、IPC、Runtimeロガー、アイドルフック、動的メモリに依存しない。物理デバイスへの接続はTier 3 Platformのドライバが実装する。

通常の内部ログはTier 2の [`runtime_logging.md`](docs/components/tier2_runtime/runtime_logging.md) が固定長リングバッファへ格納し、アイドル時に本契約のバイト出力を使って転送する。COOSとIPCの診断イベントはリングバッファを通さず、`write_event` から直接出力する。

## 2. 静的モデル

### 2.1 レコード形式

printkイベントとTier 2ロガーの出力には、同じ辞書ID依存の可変長レコードを使う。

| オフセット | フィールド | 符号化 |
| :--- | :--- | :--- |
| `0` | `level` | `u8` |
| `1..3` | `event_id` | `u24` little-endian |
| `4..7` | `arg0` | `u32` little-endian |
| `8..11` | `arg1` | `u32` little-endian |
| `12..15` | `arg2` | `u32` little-endian |
| `16..19` | `arg3` | `u32` little-endian |

レコード長は `4 + 4n` バイトとし、書式の数値指定子数 $n$（0〜4）をビルド時に確定する。表の引数フィールドは先頭 $n$ 個だけを転送する。引数数とレコード長のフィールドは送信しない。送受信側は同じIDと引数数の対応を共有する。Tier 1の送信側はTier 1辞書から生成した不変の引数数表を参照し、実行時に書式を走査しない。復号と未知ID・欠損レコードの扱いは [`runtime_logging.md`](docs/components/tier2_runtime/runtime_logging.md) 4.2に従う。

レコード内に文字列ポインタを含めない。可読文字列への展開はホスト側が静的辞書で行う。

### 2.2 Tier 1診断イベント

COOSとIPCは下表の異常系イベントのみを出力する。イベントIDと書式は [`printk.py`](experiments/pysim/tier1_core/printk.py) およびTier 3 Sinkと同期する。

| イベントID | 呼出し元 | レベル | 書式 | 引数 `arg0..arg3` |
| :--- | :--- | :--- | :--- | :--- |
| `0x0101` | COOS | `WARN` | `COOS: handoff limit reached (task=%d, count=%d)` | `task_id`, `handoff_count`, `0`, `0` |
| `0x0102` | COOS | `ERROR` | `COOS: task capacity exceeded (max=%d, attempted=%d)` | `max_tasks`, `attempted_count`, `0`, `0` |
| `0x0103` | COOS | `ERROR` | `COOS: duplicate task id rejected (task=%d)` | `task_id`, `0`, `0`, `0` |
| `0x0104` | COOS | `WARN` | `COOS: irq queue overflow dropped (vector=%d, dropped_total=%d)` | `vector_id`, `dropped_total`, `0`, `0` |
| `0x0201` | IPC | `WARN` | `IPC: rbac denied (sender_role=%d, target_role=%d)` | `sender_role`, `target_role`, `0`, `0` |
| `0x0202` | IPC | `WARN` | `IPC: unknown uri routing failed (uri_handle=%d)` | `uri_handle`, `0`, `0`, `0` |
| `0x0203` | IPC | `ERROR` | `IPC: message too large (kv_count=%d, max=%d)` | `kv_count`, `max_kv_pairs`, `0`, `0` |
| `0x0204` | IPC | `ERROR` | `IPC: invalid ownership state (current_state=%d, op=%d)` | `ownership_state`, `operation`, `0`, `0` |

## 3. インターフェース

| 操作 | 契約 |
| :--- | :--- |
| `write(data)` | 指定範囲のバイトを物理出力へ書き込む。戻り値は書込バイト数。Tier 2ロガーもこの操作だけを使う。 |
| `write_event(level, event, arg0..arg3)` | Tier 1診断イベントを辞書IDに対応する長さで同期出力する。リングバッファやSchedulerを介さない。 |

`write_event` は診断出力の失敗を処理継続条件にしない。出力先が利用できない場合も、元の状態遷移・拒否結果・契約アサーションを維持する。

## 4. 依存方向

Tier 1のCOOSとIPCは `printk` 契約だけを参照する。Tier 2 Runtimeロガーは `write` を使い、Tier 3 Platformは [`platform_driver.md`](docs/components/tier3_platform/platform_driver.md) の物理Sinkで契約を実装する。Tier 1からTier 2のロギングAPIへの依存は禁止する。
