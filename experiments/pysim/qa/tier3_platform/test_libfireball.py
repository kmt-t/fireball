"""正本: docs/components/tier3_platform/libfireball.md。ケース: docs/qa/tier3_platform/libfireball_test_spec.md。"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_TEST_FILE = Path(__file__).resolve()
_PYSIM_DIR = _TEST_FILE.parents[2]

from libfireball import Libfireball


class RecordingHostCalls:
    __slots__ = (
        "calls",
        "result",
        "vdma_calls",
        "virq_register_calls",
        "virq_unregister_calls",
    )

    def __init__(self, result: int = 0) -> None:
        self.result = result
        self.calls: list[tuple[int, int, int, int, int, int, int]] = []
        self.virq_register_calls: list[tuple[int, int]] = []
        self.virq_unregister_calls: list[int] = []
        self.vdma_calls: list[tuple[int, int, int]] = []

    def fireball_call(
        self,
        syscall_id: int,
        arg0: int,
        arg1: int,
        arg2: int,
        arg3: int,
        arg4: int,
        arg5: int,
    ) -> int:
        self.calls.append((syscall_id, arg0, arg1, arg2, arg3, arg4, arg5))
        return self.result

    def virq_register(self, node_id: int, function_index: int) -> int:
        self.virq_register_calls.append((node_id, function_index))
        return self.result

    def virq_unregister(self, node_id: int) -> int:
        self.virq_unregister_calls.append(node_id)
        return self.result

    def vdma_start(self, source: int, destination: int, byte_count: int) -> int:
        self.vdma_calls.append((source, destination, byte_count))
        return self.result


@pytest.mark.parametrize("result", (0, 17, 0xFFFF_FFFF))
def test_libfireball_host_call_argument_packing(result: int) -> None:
    """TEST-LIBFB-01: 引数順・ゼロ補完を保ち、hostの戻り値をそのまま返す。"""
    host_calls = RecordingHostCalls(result=result)
    lib = Libfireball(host_calls)
    assert lib.fireball_call0(0x01) == result
    assert lib.fireball_call1(0x10, 1) == result
    assert lib.fireball_call2(0x11, 1, 2) == result
    assert lib.fireball_call3(0x12, 1, 2, 3) == result
    assert lib.fireball_call4(0x13, 1, 2, 3, 4) == result
    assert lib.fireball_call5(0x14, 1, 2, 3, 4, 5) == result
    assert lib.fireball_call6(0x15, 1, 2, 3, 4, 5, 6) == result
    assert host_calls.calls == [
        (0x01, 0, 0, 0, 0, 0, 0),
        (0x10, 1, 0, 0, 0, 0, 0),
        (0x11, 1, 2, 0, 0, 0, 0),
        (0x12, 1, 2, 3, 0, 0, 0),
        (0x13, 1, 2, 3, 4, 0, 0),
        (0x14, 1, 2, 3, 4, 5, 0),
        (0x15, 1, 2, 3, 4, 5, 6),
    ]


@pytest.mark.parametrize("field", range(7))
@pytest.mark.parametrize("invalid", (-1, 0x1_0000_0000))
def test_libfireball_rejects_non_u32_host_call_values(field: int, invalid: int) -> None:
    """TEST-LIBFB-02: IDと全引数のu32外値をhost呼び出し前に拒否する。"""
    host_calls = RecordingHostCalls()
    lib = Libfireball(host_calls)
    fields = [10, 1, 2, 3, 4, 5, 6]
    fields[field] = invalid
    with pytest.raises(AssertionError):
        lib.fireball_call6(*fields)
    assert host_calls.calls == []


@pytest.mark.parametrize("invalid", (-1, 0x1_0000_0000))
def test_libfireball_rejects_non_u32_host_call_result(invalid: int) -> None:
    """TEST-LIBFB-03: hostが返したu32外値を正常なerrnoとして渡さない。"""
    host_calls = RecordingHostCalls(result=invalid)
    lib = Libfireball(host_calls)
    with pytest.raises(AssertionError):
        lib.fireball_call0(0x10)
    assert host_calls.calls == [(0x10, 0, 0, 0, 0, 0, 0)]


@pytest.mark.parametrize("result", (0, 17, 0xFFFF_FFFF))
def test_libfireball_dedicated_host_calls(result: int) -> None:
    """TEST-LIBFB-04 / TEST-WIT-12,13: 専用portの引数と戻り値を保ち、汎用portを呼ばない。"""
    host_calls = RecordingHostCalls(result=result)
    lib = Libfireball(host_calls)
    assert lib.fireball_virq_register(5, 12) == result
    assert lib.fireball_virq_unregister(5) == result
    assert lib.fireball_vdma_start(1, 2, 3) == result
    assert host_calls.virq_register_calls == [(5, 12)]
    assert host_calls.virq_unregister_calls == [5]
    assert host_calls.vdma_calls == [(1, 2, 3)]
    assert host_calls.calls == []


@pytest.mark.parametrize("invalid", (-1, 0x1_0000_0000))
@pytest.mark.parametrize("field", range(6))
def test_libfireball_rejects_invalid_dedicated_arguments(field: int, invalid: int) -> None:
    """TEST-LIBFB-02: 専用portの全入力にもu32境界を適用し、副作用前に拒否する。"""
    host = RecordingHostCalls()
    lib = Libfireball(host)
    with pytest.raises(AssertionError):
        if field == 0:
            lib.fireball_virq_register(invalid, 1)
        elif field == 1:
            lib.fireball_virq_register(1, invalid)
        elif field == 2:
            lib.fireball_virq_unregister(invalid)
        elif field == 3:
            lib.fireball_vdma_start(invalid, 2, 3)
        elif field == 4:
            lib.fireball_vdma_start(1, invalid, 3)
        else:
            lib.fireball_vdma_start(1, 2, invalid)
    assert host.calls == []
    assert host.virq_register_calls == []
    assert host.virq_unregister_calls == []
    assert host.vdma_calls == []


@pytest.mark.parametrize("invalid", (-1, 0x1_0000_0000))
@pytest.mark.parametrize("port", ("register", "unregister", "start"))
def test_libfireball_rejects_invalid_dedicated_results(port: str, invalid: int) -> None:
    """TEST-LIBFB-03: 専用portからも不正な戻り値を透過させない。"""
    lib = Libfireball(RecordingHostCalls(result=invalid))
    with pytest.raises(AssertionError):
        if port == "register":
            lib.fireball_virq_register(1, 2)
        elif port == "unregister":
            lib.fireball_virq_unregister(1)
        else:
            lib.fireball_vdma_start(1, 2, 3)


def test_wit_15_hal_world_imports_host_services_without_guest_exports() -> None:
    """TEST-WIT-15: HALのworld宣言はホスト提供types/resolverをimportする。"""
    wit_path = (
        _PYSIM_DIR.parents[1] / "docs/components/tier3_platform/wit/fireball_hal_contract.wit"
    )
    source = wit_path.read_text(encoding="utf-8")
    source = re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.DOTALL)
    worlds = re.findall(r"\bworld\s+fireball-hal\s*\{([^{}]*)\}", source)
    assert len(worlds) == 1
    declarations = re.findall(r"\b(import|export)\s+([a-z][a-z0-9-]*)\s*;", worlds[0])
    assert set(declarations) == {("import", "types"), ("import", "resolver")}
    assert len(declarations) == 2
    remainder = re.sub(r"\b(?:import|export)\s+[a-z][a-z0-9-]*\s*;", "", worlds[0])
    assert remainder.strip() == ""


@pytest.mark.parametrize(
    ("operation", "arguments", "result"),
    (
        ("clock-subscribe", "nanos:u64", "result<u32,recovery-strategy-category>"),
        ("poll-check", "handle:u32", "result<bool,recovery-strategy-category>"),
        ("poll-wait", "handle:u32", "result<bool,recovery-strategy-category>"),
        ("poll-drop", "handle:u32", "operation-result"),
    ),
)
def test_wit_23_hal_pollable_signatures_follow_existing_contract(
    operation: str, arguments: str, result: str
) -> None:
    """TEST-WIT-23: 購読と単一ハンドルのpoll操作は既存HAL契約の型を公開する。"""
    wit_path = (
        _PYSIM_DIR.parents[1] / "docs/components/tier3_platform/wit/fireball_hal_contract.wit"
    )
    source = wit_path.read_text(encoding="utf-8")
    source = re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.DOTALL)
    resolvers = re.findall(r"\binterface\s+resolver\s*\{(.*?)^\}", source, re.DOTALL | re.MULTILINE)
    assert len(resolvers) == 1
    declarations = re.findall(
        rf"(?<![a-z0-9-]){re.escape(operation)}\s*:\s*func\(([^()]*)\)\s*->\s*([^;]+);",
        resolvers[0],
    )
    assert len(declarations) == 1
    actual_arguments, actual_result = declarations[0]
    assert re.sub(r"\s+", "", actual_arguments) == arguments
    assert re.sub(r"\s+", "", actual_result) == result


def test_wit_14_core_wasm_import_names_select_the_matching_host_port() -> None:
    """TEST-WIT-14: Core Wasmのmodule/fieldを解決し、対応portへ引数順を保って渡す。"""
    from system import System
    from tier3_platform.drivers.wasi.context import WasiHostContext

    sysv = System()
    host = RecordingHostCalls(result=17)
    sysv.host_calls = host
    try:
        resolver = WasiHostContext(sysv)
        for name, args in (
            ("fireball_call", (10, 1, 2, 3, 4, 5, 6)),
            ("virq_register", (7, 8)),
            ("virq_unregister", (9,)),
            ("vdma_start", (11, 12, 13)),
        ):
            handler = resolver.get_handler_for_import("fireball", name)
            assert handler is not None
            assert handler(*args) == 17
            assert resolver.get_handler_for_import("other-module", name) is None
            assert resolver.get_handler_for_import("fireball", "unknown-" + name) is None
        assert host.calls == [(10, 1, 2, 3, 4, 5, 6)]
        assert host.virq_register_calls == [(7, 8)]
        assert host.virq_unregister_calls == [9]
        assert host.vdma_calls == [(11, 12, 13)]
    finally:
        sysv.shutdown()


@pytest.fixture(scope="module")
def compiled_raw_guest(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """WIT生成→object→静的archive→guestリンクを、検査対象として実行する。"""
    import subprocess
    import sys

    output = tmp_path_factory.mktemp("fireball-raw-guest")
    root = _PYSIM_DIR.parents[1]
    subprocess.run(
        [
            sys.executable,
            str(root / "tools/guest_bindings/build_guest.py"),
            "--guest",
            str(Path(__file__).parent / "guest/libfireball_probe.cxx"),
            "--output",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert (output / "libfireball.a").is_file()
    return output / "guest.wasm"


@pytest.mark.parametrize("result", (0, 17, 0xFFFFFFFF))
def test_wit_generated_static_guest_raw_four_imports(compiled_raw_guest: Path, result: int) -> None:
    """TEST-WIT-10/14, TEST-LIBFB-01/04: 実guestでu32、引数配置、専用入口を観測する。"""
    from helpers import make_native_interpreter
    from scheduler import TaskState
    from system import System
    from tier3_platform.drivers.wasi.context import WasiHostContext
    from wasm_reader import parse

    module = parse(compiled_raw_guest.read_bytes())
    observed_imports: dict[str, int] = {}
    for index in range(len(module.imports)):
        assert module.import_module_name(index) == "fireball"
        function_type = module.func_type(index)
        assert tuple(function_type.params) == (0x7F,) * len(function_type.params)
        assert tuple(function_type.results) == (0x7F,)
        field = module.import_field_name(index)
        assert field not in observed_imports
        observed_imports[field] = len(function_type.params)
    assert observed_imports == {
        "fireball_call": 7,
        "virq_register": 2,
        "virq_unregister": 1,
        "vdma_start": 3,
    }

    system = System()
    recorder = RecordingHostCalls(result)
    system.host_calls = recorder
    try:
        host = WasiHostContext(system)
        interpreter = make_native_interpreter(
            module,
            memory=host.guest_memory,
            host_functions=host.build_interpreter_host_functions(module),
        )
        task_id = system.scheduler.spawn(
            "compiled_guest",
            system.run_guest(interpreter, module.export_func_index("probe"), (128,)),
        )
        system.scheduler.run_until_idle()
        task = system.scheduler.get_task(task_id)
        assert task is not None and task.state == TaskState.TERMINATED
        assert tuple(task.result) == (10,)
        assert bytes(host.guest_memory[128:168]) == result.to_bytes(4, "little") * 10
        assert recorder.calls == [
            (16, 0, 0, 0, 0, 0, 0),
            (17, 0x80000000, 0, 0, 0, 0, 0),
            (18, 0x80000000, 0xFFFFFFFF, 0, 0, 0, 0),
            (19, 0x80000000, 2, 0xFFFFFFFF, 0, 0, 0),
            (20, 0x80000000, 2, 3, 0xFFFFFFFF, 0, 0),
            (21, 0x80000000, 2, 3, 4, 0xFFFFFFFF, 0),
            (22, 0x80000000, 2, 3, 4, 5, 0xFFFFFFFF),
        ]
        assert recorder.virq_register_calls == [(0x80000000, 0xFFFFFFFF)]
        assert recorder.virq_unregister_calls == [0xFFFFFFFF]
        assert recorder.vdma_calls == [(0x80000000, 0xFFFFFFFF, 7)]
    finally:
        system.shutdown()


def test_wit_guest_requires_static_archive(compiled_raw_guest: Path) -> None:
    """TEST-WIT-10: archiveを省いたリンクが失敗し、空の静的リンク証拠を排除する。"""
    import os
    import shutil
    import subprocess

    linker = os.environ.get("FIREBALL_WASM_LD") or next(
        (
            path
            for candidate in (
                "wasm-ld",
                "wasm-ld-21",
                "wasm-ld-20",
                "wasm-ld-19",
                "wasm-ld-18",
                "wasm-ld-17",
            )
            if (path := shutil.which(candidate)) is not None
        ),
        None,
    )
    assert linker is not None, "wasm-ld is required for the guest ABI test"
    missing_archive = subprocess.run(
        [
            linker,
            "--no-entry",
            str(compiled_raw_guest.parent / "guest.o"),
            "-o",
            str(compiled_raw_guest.parent / "missing_archive.wasm"),
        ],
        capture_output=True,
        text=True,
    )
    assert missing_archive.returncode != 0
    assert "undefined symbol" in missing_archive.stderr


@pytest.mark.parametrize(
    "mutation",
    ("unsupported-type", "extra-declaration", "extra-imported-interface", "missing-import"),
)
def test_wit_raw_generator_rejects_unsupported_contract(tmp_path: Path, mutation: str) -> None:
    """TEST-WIT-10: 未対応契約を無視して古いbindingを生成しない。"""
    import subprocess
    import sys

    root = _PYSIM_DIR.parents[1]
    source = (
        root / "docs/components/tier3_platform/wit/fireball_hostcall_contract.wit"
    ).read_text()
    if mutation == "unsupported-type":
        source = source.replace("id: u32", "id: u64", 1)
    elif mutation == "extra-declaration":
        source += "\ninterface extra {}\n"
    elif mutation == "extra-imported-interface":
        source = source.replace(
            "world fireball-hostcall {",
            "interface extra {}\nworld fireball-hostcall {\n  import extra;",
        )
    else:
        source = source.replace("import vdma;", "")
    wit = tmp_path / "invalid.wit"
    wit.write_text(source)
    output = tmp_path / "generated"
    result = subprocess.run(
        [
            sys.executable,
            str(root / "tools/guest_bindings/generate_hostcall_bindings.py"),
            "--wit",
            str(wit),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert not (output / "fireball_hostcall.cxx").exists()
