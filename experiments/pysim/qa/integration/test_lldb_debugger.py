"""Exercise LLDB's DWARF breakpoint flow against Fireball's real RSP server.

This integration test requires system-installed ``clang`` and ``lldb`` binaries.
They are external tools rather than Python QA dependencies.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from qa.private.debugger_support import make_debug_execution
from tier2_runtime.wasm.reader import parse
from tier3_plugins.debugger.debugger import DebuggerManager, InterpreterExecutionControl
from tier3_plugins.debugger.gdb_server import GDBServer


class _ObservedDebuggerManager(DebuggerManager):
    """Retain the RSP breakpoint addresses even after LLDB detaches and clears them."""

    __slots__ = ("inserted_breakpoints",)

    def __init__(self, engine: InterpreterExecutionControl) -> None:
        super().__init__(engine=engine)
        self.inserted_breakpoints: list[int] = []

    def add_breakpoint(self, pc: int) -> bool:
        inserted = super().add_breakpoint(pc)
        if inserted:
            self.inserted_breakpoints.append(pc)
        return inserted


def _target_definition_source() -> str:
    register_names = ("pc", "sp", "fp", "tos", *(f"local{index}" for index in range(16)))
    lines = [
        "from lldb import *",
        f"register_names = {register_names!r}",
        "registers = []",
        "for index, name in enumerate(register_names):",
        "    info = {'name': name, 'set': 0, 'bitsize': 32, 'encoding': eEncodingUint,",
        "            'format': eFormatHex if name not in ('pc', 'sp') else eFormatAddressInfo,",
        "            'offset': index * 4, 'gdb': index}",
        "    if name == 'pc': info['generic'] = LLDB_REGNUM_GENERIC_PC",
        "    elif name == 'sp': info['generic'] = LLDB_REGNUM_GENERIC_SP",
        "    elif name == 'fp': info['generic'] = LLDB_REGNUM_GENERIC_FP",
        "    registers.append(info)",
        "target_definition = {'sets': ['WASM Virtual Registers'], 'registers': registers,",
        "                     'host-info': {'triple': 'wasm32-unknown-unknown',",
        "                                   'endian': eByteOrderLittle},",
        "                     'g-packet-size': 80}",
        "def get_dynamic_setting(target, setting_name):",
        "    if setting_name == 'gdb-server-target-definition':",
        "        return target_definition",
    ]
    return "\n".join(lines) + "\n"


def test_lldb_resolves_dwarf_breakpoint_and_stops_in_fireball(tmp_path: Path) -> None:
    """TEST-INT-129: LLDB sends Z0 after the WASM code section is mapped."""
    clang = shutil.which("clang")
    lldb = shutil.which("lldb")
    if clang is None or lldb is None:
        missing = tuple(name for name, path in (("clang", clang), ("lldb", lldb)) if path is None)
        pytest.skip("external debugger integration requires: " + ", ".join(missing))

    source_path = tmp_path / "add_one.c"
    wasm_path = tmp_path / "add_one.wasm"
    target_definition_path = tmp_path / "fireball_wasm_target_definition.py"
    command_path = tmp_path / "lldb_commands.txt"
    source_path.write_text(
        "int add_one(int value) {\n  return value + 1;\n}\n",
        encoding="utf-8",
    )
    target_definition_path.write_text(_target_definition_source(), encoding="utf-8")

    compile_result = subprocess.run(
        [
            clang,
            "--target=wasm32-unknown-unknown",
            "-g",
            "-O0",
            "-nostdlib",
            "-Wl,--no-entry",
            "-Wl,--export=add_one",
            str(source_path),
            "-o",
            str(wasm_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert compile_result.returncode == 0, compile_result.stderr

    wasm_bytes = wasm_path.read_bytes()
    module = parse(memoryview(wasm_bytes))
    function_index = module.export_func_index("add_one")
    execution = make_debug_execution(
        module,
        function_index,
        (41,),
    )
    debugger = _ObservedDebuggerManager(engine=execution)
    blocks = {block.head_pc: block for block in module.blocks}
    entry_pc = execution.call.current_pc()
    server = GDBServer(dbg=debugger, host="127.0.0.1", port=0)

    try:
        port = server.start(current_pc=entry_pc, ctx=execution.context, blocks=blocks)
        command_path.write_text(
            "\n".join(
                (
                    f"settings set plugin.process.gdb-remote.target-definition-file {target_definition_path}",
                    f"target create {wasm_path}",
                    f"gdb-remote 127.0.0.1:{port}",
                    f"target modules load --file {wasm_path} code 0x0",
                    "breakpoint set -n add_one",
                    "breakpoint list",
                    "process continue",
                    "process status",
                    f"source list -f {source_path} -l 2 -c 1",
                    "frame info",
                )
            )
            + "\n",
            encoding="utf-8",
        )
        lldb_result = subprocess.run(
            [lldb, "--no-lldbinit", "--batch", "--source", str(command_path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = lldb_result.stdout + lldb_result.stderr

        assert lldb_result.returncode == 0, output
        assert "breakpoint set -n add_one" in output
        assert "stop reason = breakpoint 1.1" in output, output
        assert "add_one" in output, output
        assert "return value + 1;" in output, output
        stopped_pc = execution.call.current_pc()
        assert debugger.inserted_breakpoints
        assert stopped_pc in debugger.inserted_breakpoints
        assert not execution.call.finished
    finally:
        server.stop()
