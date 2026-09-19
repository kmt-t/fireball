# BUG-0004 終了したタスクのTCBスロットが返却されない

| 項目 | 内容 |
| :--- | :--- |
| 重大度 | 高 |
| 状態 | 修正済み |
| 対象 | `scheduler`（[`scheduler.py`](experiments/pysim/tier1_core/scheduler.py)） |
| 発見日 | 2026-09-19 |

## 1. 現象

タスクが終了しても、TCBスロットが再利用されない。生成できるタスクの総数は、同時実行数ではなく、寿命全体で `FB_CONF_MAX_TASKS`（16）に制限される。

## 2. 仕様との差

[`os_scheduler.md`](docs/components/tier1_core/os_scheduler.md) は、終了時にTCBスロットを返却して再利用すると定める。

- 状態遷移表の「RUNNING → [*]」は、「TCBスロットの返却（再利用化）」を要求する。
- 終了操作の事後条件は、「TCB等が解放され、全キューから除外される」ことを要求する。

実装の `Scheduler.spawn` は、終了済みタスクを含む全タスクの件数（`_all`）で上限を判定する。`_all` へは `push_back` だけを行い、削除しない。

## 3. 再現手順

```python
from scheduler import Scheduler, FB_CONF_MAX_TASKS


def quick():
    return
    yield


s = Scheduler()
for i in range(FB_CONF_MAX_TASKS):
    s.spawn(f"t{i}", quick())
s.run_to_completion()  # 16 タスクすべてが TERMINATED になる
s.spawn("one_more", quick())  # ここで停止する
```

期待結果は、17個目の生成が成功することである。

実際の結果は、`AssertionError: Task capacity exceeded (max 16)` である（2026-09-19 実測）。

## 4. 影響

- 異常終了したサービスの自律的な再起動（サービス自己再起動の要求）が、16回目の生成以降にできない。
- 大規模ワークロードの `os_mix` は、17個のゲストを1つのスケジューラで動かせない。回避策として、8タスクずつ新しい `System` を作って実行している。

## 5. 原因

`Scheduler.spawn` が、終了済みタスクを含む全タスクの件数で上限を判定していた。`_all` からの削除経路が存在しなかった。

## 6. 修正と検証

- `spawn` は、TCBが満杯のときだけ、最も古い終了済みタスクのスロットを解放する（`_reclaim_terminated_slot`）。
- 終了直後の結果は、次の生成まで `get_task` で読める。
- タスクIDは単調増加であり、スロットの再利用後も重複しない。
- 検証は、`TEST-SCHED-16` である。修正前のスケジューラでは、このテストが失敗することを確認した。
- 大規模ワークロードの `os_mix` は、1つの `System` で8タスクずつの波を実行する。スロットは回収される。
