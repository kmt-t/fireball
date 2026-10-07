"""Observe valid product behavior without relying on assertions in this process."""

from __future__ import annotations

import json
import mmap
import sys

import wasmtime
from bump_allocator import BumpAllocator
from qa.shared.helpers import make_interpreter, make_native_interpreter
from qa.shared.runtime_stats import RuntimeStatsEngine
from scheduler import Scheduler
from tier2_runtime.abi.interpreter_abi import NativeValueStack
from tier2_runtime.vmmio.controller import VMMIOController
from tier2_runtime.wasm.reader import parse
from tier3_plugins.debugger.debugger import DebuggerManager
from tier3_plugins.jit.jit_manager import JITRuntimeManager


def main() -> None:
    scheduler = Scheduler()
    channel = scheduler.create_channel()
    sender = scheduler.get_task(scheduler.spawn("sender"))
    receiver = scheduler.get_task(scheduler.spawn("receiver"))
    assert sender is not None and receiver is not None
    scheduler.detach(sender)
    scheduler.activate_task(sender)
    channel.send(99)
    scheduler.current_task = None
    scheduler.detach(receiver)
    scheduler.activate_task(receiver)
    action, target = channel.recv()

    module = parse(
        wasmtime.wat2wasm("""(module
      (func (export "id") (param i32) (result i32) local.get 0)
      (func (export "sum") (param $n i32) (result i64)
        (local $i i32) (local $s i64)
        (block $exit (loop $again
          (br_if $exit (i32.ge_u (local.get $i) (local.get $n)))
          (local.set $s (i64.add (local.get $s) (i64.extend_i32_u (local.get $i))))
          (local.set $i (i32.add (local.get $i) (i32.const 1)))
          (br $again)))
        local.get $s)
      (func (export "sum32") (param $n i32) (result i32)
        (local $i i32) (local $s i32)
        (block $exit (loop $again
          (br_if $exit (i32.ge_u (local.get $i) (local.get $n)))
          (local.set $s (i32.add (local.get $s) (local.get $i)))
          (local.set $i (i32.add (local.get $i) (i32.const 1)))
          (br $again)))
        local.get $s)
      (func (export "f32") (result f32)
        (f32.add (f32.sqrt (f32.const 36)) (f32.abs (f32.const -3))))
      (func (export "f64") (result f64)
        (f64.add (f64.const 1.25) (f64.const 2.5)))
      (func (export "table") (result i32)
        (block (result i32) i32.const 33 i32.const 0 br_table 0 0)))""")
    )
    interpreter = make_interpreter(module)
    allocator = BumpAllocator()

    def reserve_region(size: int, alignment: int) -> memoryview:
        allocator.allocate(size, alignment)
        return memoryview(mmap.mmap(-1, size))

    plugin = JITRuntimeManager(reserve_region)
    engine = RuntimeStatsEngine(
        jit_runtime=plugin,
        collect_runtime_stats=True,
        bump_allocator=allocator,
    )
    engine.register_module_blocks(module)
    native = make_native_interpreter(module, bump_allocator=engine.bump_allocator)
    reference_results = []
    native_results = []
    for name, arguments in (
        ("id", (37,)),
        ("sum", (2000,)),
        ("sum32", (2000,)),
        ("f32", ()),
        ("f64", ()),
        ("table", ()),
    ):
        index = module.export_func_index(name)
        reference_results.append(interpreter.call(index, arguments)[0])
        native_results.append(engine.call(native, index, arguments)[0])

    vmmio = VMMIOController(scheduler=scheduler)
    vmmio.map_static_device(0xC0000)
    debugger = DebuggerManager()
    debugger.sample_pc(17)
    debugger.sample_pc(17)
    stack = NativeValueStack(capacity=4)
    stack.push_i64(-7)
    stack.push_f64(1.25)
    stack.clear()
    print(
        json.dumps(
            {
                "optimization": sys.flags.optimize,
                "rendezvous": [
                    action.name,
                    target,
                    sender in tuple(scheduler._ready),
                    receiver.received_val,
                ],
                "local_types": tuple(module.local_types(0)),
                "reference_results": reference_results,
                "native_results": native_results,
                "jit_used": engine.stat_jit_invocations > 0,
                "pte_registered": vmmio.ptes.view().find(0xC0000) is not None,
                "pc_frequency": debugger.pc_sample_counts.find(17),
                "stack_size_after_clear": len(stack),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
