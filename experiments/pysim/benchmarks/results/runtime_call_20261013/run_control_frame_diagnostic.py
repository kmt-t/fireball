"""Build a temporary release counter library, capture frame deltas, restore files."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
RESULTS = Path(__file__).resolve().parent
OPS = ROOT / "experiments/pysim/native/tier2_runtime/interpreter/native_wasm_operations.cxx"
DISPATCH = ROOT / "experiments/pysim/native/tier2_runtime/interpreter/native_interpreter.cxx"
LIBRARY = ROOT / "experiments/pysim/tier2_runtime/interpreter/libnative_interpreter.so"
WORKER = RESULTS / "measure_control_frames.py"

DIAGNOSTIC = r"""
#if defined(FB_CONTROL_FRAME_DIAGNOSTIC)
namespace fireball::measurement {
std::uint64_t control_frame_counters[6]{};
}
extern "C" __attribute__((visibility("default"))) void fb_control_frame_reset() {
  for (auto &value : fireball::measurement::control_frame_counters) value = 0;
}
extern "C" __attribute__((visibility("default"))) void fb_control_frame_add(
    std::uint32_t index, std::uint64_t amount) {
  assert(index < 6);
  fireball::measurement::control_frame_counters[index] += amount;
}
extern "C" __attribute__((visibility("default"))) std::uint64_t
fb_control_frame_read(std::uint32_t index) {
  assert(index < 6);
  return fireball::measurement::control_frame_counters[index];
}
#define FB_CONTROL_FRAME_ADD(index, amount) \
  fb_control_frame_add(index, static_cast<std::uint64_t>(amount))
#else
#define FB_CONTROL_FRAME_ADD(index, amount) ((void)0)
#endif
"""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    original_source = OPS.read_bytes()
    original_library = LIBRARY.read_bytes()
    source_hash = sha256(original_source)
    library_hash = sha256(original_library)
    source = original_source.decode()
    marker = "#include <type_traits>\n"
    assert source.count(marker) == 1
    source = source.replace(marker, marker + DIAGNOSTIC, 1)

    replacements = (
        (
            "  current.control_stack->size = control_frame_index(current, finished.control_frame_base);",
            "  FB_CONTROL_FRAME_ADD(5, current.control_stack->size -\n"
            "                            control_frame_index(current, finished.control_frame_base));\n"
            "  current.control_stack->size = control_frame_index(current, finished.control_frame_base);",
        ),
        (
            "    current.control_stack->size = control_frame_base_index(current);\n"
            "    next_ip = kSentinel;",
            "    FB_CONTROL_FRAME_ADD(3, current.control_stack->size -\n"
            "                              control_frame_base_index(current));\n"
            "    current.control_stack->size = control_frame_base_index(current);\n"
            "    next_ip = kSentinel;",
        ),
        (
            "    current.control_stack->size = control_frame_base_index(current) + target_index + 1;",
            "    FB_CONTROL_FRAME_ADD(3, current.control_stack->size -\n"
            "                              (control_frame_base_index(current) + target_index + 1));\n"
            "    current.control_stack->size = control_frame_base_index(current) + target_index + 1;",
        ),
        (
            "    current.control_stack->size = control_frame_base_index(current) + target_index;",
            "    FB_CONTROL_FRAME_ADD(3, current.control_stack->size -\n"
            "                              (control_frame_base_index(current) + target_index));\n"
            "    current.control_stack->size = control_frame_base_index(current) + target_index;",
        ),
        (
            "    --current.control_stack->size;\n  }\n}\n\nbool pop(",
            "    --current.control_stack->size;\n"
            "    FB_CONTROL_FRAME_ADD(4, 1);\n"
            "  }\n}\n\nbool pop(",
        ),
        (
            "  current.control_stack->size -= 1;\n  const auto &call_frame = active_frame(current);",
            "  current.control_stack->size -= 1;\n"
            "  FB_CONTROL_FRAME_ADD(1, 1);\n"
            "  const auto &call_frame = active_frame(current);",
        ),
        (
            "  auto &frame = current.control_stack->frames[current.control_stack->size++];\n"
            "  frame.start = source_ip;\n  frame.stack_height = sp;\n"
            "  consume_instruction_width(rel);",
            "  auto &frame = current.control_stack->frames[current.control_stack->size++];\n"
            "  FB_CONTROL_FRAME_ADD(0, 1);\n  frame.start = source_ip;\n"
            "  frame.stack_height = sp;\n  consume_instruction_width(rel);",
        ),
        (
            "    current.control_stack->size -= 1;\n  }\n  advance_instruction_ip(instruction_ip, rel);",
            "    current.control_stack->size -= 1;\n"
            "    FB_CONTROL_FRAME_ADD(2, 1);\n"
            "  }\n  advance_instruction_ip(instruction_ip, rel);",
        ),
        (
            "  auto &frame = current.control_stack->frames[current.control_stack->size++];\n"
            "  frame.start = source_ip;\n  frame.stack_height = sp;\n"
            "  const auto next_ip = condition == 0 ? entry->else_offset + 1 : entry->next_pc;",
            "  auto &frame = current.control_stack->frames[current.control_stack->size++];\n"
            "  FB_CONTROL_FRAME_ADD(0, 1);\n  frame.start = source_ip;\n"
            "  frame.stack_height = sp;\n"
            "  const auto next_ip = condition == 0 ? entry->else_offset + 1 : entry->next_pc;",
        ),
    )
    for old, new in replacements:
        assert source.count(old) == 1, old
        source = source.replace(old, new, 1)

    with tempfile.TemporaryDirectory(prefix="fireball-control-frame-diag-") as temporary:
        temp = Path(temporary)
        diagnostic_library = temp / "libnative_interpreter_control_frame_diagnostic.so"
        command = [
            "clang++",
            "-DFB_CONTROL_FRAME_DIAGNOSTIC=1",
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
            str(DISPATCH),
            str(OPS),
            "-o",
            str(diagnostic_library),
        ]
        try:
            OPS.write_text(source)
            subprocess.run(command, cwd=ROOT, check=True)
        finally:
            assert sha256(OPS.read_bytes()) != source_hash
            OPS.write_bytes(original_source)
            assert sha256(OPS.read_bytes()) == source_hash

        backup = temp / LIBRARY.name
        backup.write_bytes(original_library)
        try:
            shutil.copy2(diagnostic_library, LIBRARY)
            rows = []
            for width in (64, 128, 192, 256, 320):
                for trial in range(1, 4):
                    output = subprocess.check_output(
                        [
                            "taskset",
                            "-c",
                            "2",
                            str(ROOT / ".venv/bin/python"),
                            str(WORKER),
                            "--width",
                            str(width),
                        ],
                        cwd=ROOT,
                        text=True,
                    )
                    row = json.loads(output)
                    row["trial"] = trial
                    rows.append(row)
                    print(f"{width}x{width * 9 // 16} trial={trial}: counters captured", flush=True)
            (RESULTS / "control_frame_diagnostic.json").write_text(
                json.dumps(
                    {
                        "source_sha256": source_hash,
                        "release_library_sha256": library_hash,
                        "diagnostic_library_sha256": sha256(diagnostic_library.read_bytes()),
                        "build_command": "clang++ -O3 -DNDEBUG -flto=thin with temporary FB_CONTROL_FRAME_DIAGNOSTIC counters",
                        "counter_order": [
                            "push_block_loop_if",
                            "pop_else",
                            "pop_end",
                            "pop_branch",
                            "pop_static_prune",
                            "pop_function_return",
                        ],
                        "rows": rows,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n"
            )
        finally:
            LIBRARY.write_bytes(original_library)
            assert sha256(LIBRARY.read_bytes()) == library_hash


if __name__ == "__main__":
    main()
