"""Build a temporary QA-only counter library, measure AO-Bench, then restore files."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
RESULTS = Path(__file__).resolve().parent
SOURCE = ROOT / "experiments/pysim/native/tier2_runtime/interpreter/native_wasm_operations.cxx"
RUNTIME_LIBRARY = ROOT / "experiments/pysim/tier2_runtime/interpreter/libnative_interpreter.so"
WORKER = RESULTS / "measure_call_diagnostic.py"

DIAGNOSTIC = r"""
#if defined(FB_RUNTIME_CALL_DIAGNOSTIC)
namespace fireball::measurement {
std::uint64_t runtime_call_counters[4]{};
}
extern "C" __attribute__((visibility("default"))) void fb_runtime_call_reset() {
  for (auto &value : fireball::measurement::runtime_call_counters) {
    value = 0;
  }
}
extern "C" __attribute__((visibility("default"))) std::uint64_t
fb_runtime_call_read(std::uint32_t index) {
  assert(index < 4);
  return fireball::measurement::runtime_call_counters[index];
}
#define FB_RUNTIME_CALL_ADD(index, amount) \
  (fireball::measurement::runtime_call_counters[index] += static_cast<std::uint64_t>(amount))
#else
#define FB_RUNTIME_CALL_ADD(index, amount) ((void)0)
#endif
"""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    original_source = SOURCE.read_bytes()
    original_runtime = RUNTIME_LIBRARY.read_bytes()
    source_hash = hashlib.sha256(original_source).hexdigest()
    runtime_hash = hashlib.sha256(original_runtime).hexdigest()
    source = original_source.decode()
    include_marker = "#include <type_traits>\n"
    call_marker = "  auto &current = *context;\n"
    assert source.count(include_marker) == 1 and source.count(call_marker) >= 1
    source = source.replace(include_marker, include_marker + DIAGNOSTIC, 1)
    function_start = source.index("bool runtime_call(")
    entry_marker = source.index(call_marker, function_start) + len(call_marker)
    source = source[:entry_marker] + "  FB_RUNTIME_CALL_ADD(0, 1);\n" + source[entry_marker:]
    frame_marker = "  auto *callee_local_base = current.local_stack_pointer;\n"
    assert source.count(frame_marker) == 1
    source = source.replace(
        frame_marker,
        "  FB_RUNTIME_CALL_ADD(1, 1);\n"
        "  FB_RUNTIME_CALL_ADD(2, target.param_packed_slot_count);\n"
        "  FB_RUNTIME_CALL_ADD(3, target.local_slot_count - target.param_packed_slot_count);\n"
        + frame_marker,
        1,
    )
    with tempfile.TemporaryDirectory(prefix="fireball-call-diag-") as temporary:
        temporary_path = Path(temporary)
        diagnostic_library = temporary_path / "libnative_interpreter_call_diagnostic.so"
        backup_runtime = temporary_path / RUNTIME_LIBRARY.name
        backup_runtime.write_bytes(original_runtime)
        command = [
            "clang++",
            "-DFB_RUNTIME_CALL_DIAGNOSTIC=1",
            "-std=c++23",
            "-O3",
            "-DNDEBUG",
            "-flto=thin",
            "-fuse-ld=lld",
            "-fvisibility=hidden",
            "-fno-exceptions",
            "-fno-rtti",
            "-fomit-frame-pointer",
            "-Wall",
            "-Wextra",
            "-Wpedantic",
            "-shared",
            "-fPIC",
            str(ROOT / "experiments/pysim/native/tier2_runtime/interpreter/native_interpreter.cxx"),
            str(SOURCE),
            "-o",
            str(diagnostic_library),
        ]
        try:
            SOURCE.write_text(source)
            subprocess.run(command, cwd=ROOT, check=True)
        finally:
            assert sha256(SOURCE) != source_hash
            SOURCE.write_bytes(original_source)
            assert sha256(SOURCE) == source_hash

        try:
            shutil.copy2(diagnostic_library, RUNTIME_LIBRARY)
            assert sha256(RUNTIME_LIBRARY) != runtime_hash
            rows = []
            for width in (64, 128, 192, 256, 320):
                for trial in range(1, 4):
                    command = [
                        "taskset",
                        "-c",
                        "2",
                        str(ROOT / ".venv/bin/python"),
                        str(WORKER),
                        "--width",
                        str(width),
                    ]
                    row = json.loads(subprocess.check_output(command, cwd=ROOT, text=True))
                    row["trial"] = trial
                    rows.append(row)
                    print(f"{width}x{width * 9 // 16} trial={trial}: counters captured", flush=True)
            (RESULTS / "call_diagnostic.json").write_text(
                json.dumps(
                    {
                        "source_sha256": source_hash,
                        "release_library_sha256": runtime_hash,
                        "diagnostic_library_sha256": sha256(diagnostic_library),
                        "build_command": " ".join(command),
                        "rows": rows,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n"
            )
        finally:
            shutil.copy2(backup_runtime, RUNTIME_LIBRARY)
        assert sha256(RUNTIME_LIBRARY) == runtime_hash


if __name__ == "__main__":
    main()
