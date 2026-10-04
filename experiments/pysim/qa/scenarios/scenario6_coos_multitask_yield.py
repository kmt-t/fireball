import struct
from pathlib import Path

_PYSIM_DIR = Path(__file__).resolve().parent
while not (_PYSIM_DIR / "tier1_core").is_dir():
    _PYSIM_DIR = _PYSIM_DIR.parent


"""Scenario 6: native guest producer/consumer yield through RuntimeEngine and COOS.

Fresh shared memory, LOOP thresholds, complete interleaving snapshots and both
completion results are observed through the existing System.run_guest path.
"""

from qa.shared.helpers import make_native_interpreter, wat_to_wasm
from scheduler import ChannelAction, TaskState
from system import System
from tier2_runtime.runtime.engine import RuntimeDriveMode, RuntimeEngine
from tier2_runtime.wasm.reader import parse

SCENARIO6_WAT = """
(module
  (memory (export "memory") 1)
  ;; Task A: Producer - writes sequence into memory buffer starting at offset 512
  (func (export "producer_task") (param $count i32) (result i32)
    (local $i i32)
    (local $ptr i32)
    (local.set $i (i32.const 0))
    (local.set $ptr (i32.const 512))
    (block $b_exit
      (loop $l_top
        (br_if $b_exit (i32.ge_s (local.get $i) (local.get $count)))
        ;; Store (i + 1) * 10
        (i32.store (local.get $ptr) (i32.mul (i32.add (local.get $i) (i32.const 1)) (i32.const 10)))
        (local.set $ptr (i32.add (local.get $ptr) (i32.const 4)))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (i32.store (i32.const 0) (local.get $i))
        (br $l_top)
      )
    )
    (local.get $i)
  )
  ;; Task B: Consumer - reads sequence from memory buffer and calculates sum
  (func (export "consumer_task") (param $count i32) (result i32)
    (local $i i32)
    (local $ptr i32)
    (local $sum i32)
    (local.set $i (i32.const 0))
    (local.set $ptr (i32.const 512))
    (local.set $sum (i32.const 0))
    (block $b_exit
      (loop $l_top
        (br_if $b_exit (i32.ge_s (local.get $i) (local.get $count)))
        (local.set $sum (i32.add (local.get $sum) (i32.load (local.get $ptr))))
        (local.set $ptr (i32.add (local.get $ptr) (i32.const 4)))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (i32.store (i32.const 4) (local.get $i))
        (br $l_top)
      )
    )
    (local.get $sum)
  )
)
"""


def test_scenario_coos_multitask():
    """TEST-INT-50/51: real native threshold handoffs preserve guest progress."""
    module = parse(wat_to_wasm(SCENARIO6_WAT))
    memory = bytearray(65536)
    producer = make_native_interpreter(module, memory=memory)
    consumer = make_native_interpreter(module, memory=memory)
    system = System()
    system.runtime_engine = RuntimeEngine(yield_threshold=4, drive_mode=RuntimeDriveMode.COOS)
    observed: list[tuple[int, int]] = []

    def monitor():
        for _ in range(24):
            prod = system.scheduler.get_task(prod_id)
            cons = system.scheduler.get_task(cons_id)
            assert prod is not None and prod.state == TaskState.READY
            assert cons is not None and cons.state == TaskState.READY
            observed.append(struct.unpack_from("<2I", memory))
            yield (ChannelAction.YIELD, None)

    try:
        prod_id = system.scheduler.spawn(
            "producer",
            system.run_guest(
                producer,
                module.export_func_index("producer_task"),
                (100,),
            ),
        )
        cons_id = system.scheduler.spawn(
            "consumer",
            system.run_guest(
                consumer,
                module.export_func_index("consumer_task"),
                (100,),
            ),
        )
        system.scheduler.spawn("monitor", monitor())
        system.scheduler.run_until_idle()
        prod = system.scheduler.get_task(prod_id)
        cons = system.scheduler.get_task(cons_id)
        assert prod is not None and prod.state == TaskState.TERMINATED and prod.result == [100]
        assert cons is not None and cons.state == TaskState.TERMINATED and cons.result == [50500]
        assert observed == [(value, value) for value in range(4, 100, 4)]
        assert struct.unpack_from("<2I", memory) == (100, 100)
        assert struct.unpack_from("<100I", memory, 512) == tuple(range(10, 1001, 10))
        assert memory[8:512] == bytes(504)
        assert memory[912:] == bytes(65536 - 912)
        print(
            "[PASS] Scenario 6: native LOOP threshold handoffs, fresh-memory interleaving and results."
        )
    finally:
        system.shutdown()


if __name__ == "__main__":
    test_scenario_coos_multitask()
