"""Observe resource rejection and error transport without pytest rewriting."""

from __future__ import annotations

import json
import sys

from scheduler import Scheduler
from system import System
from tier2_runtime.interpreter.control_flow import build_control_map, count_basic_blocks
from tier2_runtime.wasm.validation import InvalidWasm
from tier3_platform.drivers.hal.dummy import DummyDriver, Timer
from tier3_platform.drivers.wasi.context import Wasi03pEngine


def main() -> None:
    scheduler = Scheduler(max_tasks=3)
    scheduler.spawn("first").unwrap()
    scheduler.spawn("second").unwrap()
    before_duplicate = tuple(task.task_id for task in scheduler._ready)
    duplicate = scheduler.spawn("duplicate", task_id=1)
    duplicate_unchanged = before_duplicate == tuple(task.task_id for task in scheduler._ready)
    scheduler.spawn("third").unwrap()
    before_full = (tuple(task.task_id for task in scheduler._ready), scheduler._next_id)
    full = scheduler.spawn("overflow")
    full_unchanged = before_full == (
        tuple(task.task_id for task in scheduler._ready),
        scheduler._next_id,
    )

    timer = Timer()
    handles = tuple(timer.subscribe(0).unwrap() for _ in range(16))
    timer_before = tuple(
        (item.active, item.generation, item.deadline_ns) for item in timer._subscriptions
    )
    timer_full = timer.subscribe(0)
    timer_unchanged = timer_before == tuple(
        (item.active, item.generation, item.deadline_ns) for item in timer._subscriptions
    )
    timer.drop(handles[0])
    reused = timer.subscribe(0).unwrap()

    invalid = []
    for operation in (build_control_map, count_basic_blocks):
        try:
            operation(memoryview(b"\xff\x0b"))
        except InvalidWasm as trap:
            invalid.append(int(trap.code))

    system = System()
    try:
        system.start_runtime_task().unwrap()
        driver = DummyDriver(system.pool)
        uri = "fireball://hal/timer/0"
        system.start_hal_driver(driver, uri).unwrap()
        for _ in range(16):
            driver.timer.subscribe(0).unwrap()
        wire = Wasi03pEngine(system).clock_subscribe(uri, 0)
        wire_observation = [
            wire.is_ok,
            wire.error,
            wire.value,
            system.hal_task_for(uri).last_response_code,
        ]
        for _ in range(16):
            system.scheduler.spawn("busy")
        before_sender = tuple(task.task_id for task in system.scheduler._ready)
        sender_failure = Wasi03pEngine(system).clock_subscribe(uri, 0)
        sender_observation = [
            sender_failure.is_ok,
            sender_failure.error,
            before_sender == tuple(task.task_id for task in system.scheduler._ready),
        ]
        system.scheduler.current_task = None
        bound = system.bind_runtime(bytearray(65536))
        binding_observation = [bound.is_ok, bound.error.name, system._guest_memory is None]
    finally:
        system.shutdown()
    print(
        json.dumps(
            {
                "optimization": sys.flags.optimize,
                "scheduler": [
                    duplicate.is_ok,
                    duplicate.error.name,
                    duplicate_unchanged,
                    full.is_ok,
                    full.error.name,
                    full_unchanged,
                ],
                "timer": [
                    timer_full.is_ok,
                    int(timer_full.error),
                    timer_full.value,
                    timer_unchanged,
                    reused,
                ],
                "invalid_wasm": invalid,
                "wire": wire_observation,
                "sender": sender_observation,
                "binding": binding_observation,
            }
        )
    )


if __name__ == "__main__":
    main()
