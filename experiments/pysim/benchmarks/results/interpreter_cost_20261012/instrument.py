"""計数パッチを生成する。復元は保存した元バイト列を用いる。"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[4]
SOURCE = ROOT / "experiments/pysim/native/tier2_runtime/interpreter"
BACKUP = Path("/tmp/fireball-interpreter-cost-20261012")
DECLARATION = """
#if defined(FB_COST_DIAGNOSTIC)
namespace fireball::measurement {
extern std::uint64_t counters[12];
}
#define FB_COST_COUNT(index) (++fireball::measurement::counters[index])
#else
#define FB_COST_COUNT(index) ((void)0)
#endif
"""
DEFINITION = """
#if defined(FB_COST_DIAGNOSTIC)
namespace fireball::measurement {
std::uint64_t counters[12]{};
}
extern "C" __attribute__((visibility("default"))) void fb_cost_reset() {
  for (auto &value : fireball::measurement::counters) {
    value = 0;
  }
}
extern "C" __attribute__((visibility("default"))) std::uint64_t fb_cost_read(std::uint32_t index) {
  return fireball::measurement::counters[index];
}
#endif
"""


def replace_once(source: str, old: str, new: str) -> str:
    assert source.count(old) == 1, old
    return source.replace(old, new, 1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("install", "restore"))
    args = parser.parse_args()
    names = ("native_interpreter.cxx", "native_wasm_operations.cxx")
    manifest = json.loads((OUT / "source_manifest_before.json").read_text())
    if args.action == "restore":
        installed = json.loads((OUT / "diagnostic_source_manifest.json").read_text())
        for name in (*names, "build_native.sh"):
            assert hashlib.sha256((SOURCE / name).read_bytes()).hexdigest() == installed[name]
            (SOURCE / name).write_bytes((BACKUP / name).read_bytes())
        jit_build = ROOT / "experiments/pysim/native/tier3_plugins/jit/build_native.sh"
        assert (
            hashlib.sha256(jit_build.read_bytes()).hexdigest() == installed["jit_build_native.sh"]
        )
        jit_build.write_bytes((BACKUP / "jit_build_native.sh").read_bytes())
        return
    for name in names:
        path = SOURCE / name
        original = path.read_text()
        assert (
            hashlib.sha256(path.read_bytes()).hexdigest() == manifest[str(path.relative_to(ROOT))]
        )
        (BACKUP / name).write_bytes(path.read_bytes())
        text = replace_once(
            original, "#include <type_traits>\n", "#include <type_traits>\n" + DECLARATION
        )
        if name == "native_interpreter.cxx":
            text = replace_once(
                text,
                "  const auto head_pc = static_cast<std::uint32_t>(absolute_pc);",
                "  FB_COST_COUNT(0);\n  const auto head_pc = static_cast<std::uint32_t>(absolute_pc);",
            )
            text = replace_once(
                text,
                "    return entry.block_index;",
                "    FB_COST_COUNT(1);\n    return entry.block_index;",
            )
            text = replace_once(
                text,
                "  const auto folded_pc = head_pc *",
                "  FB_COST_COUNT(2);\n  const auto folded_pc = head_pc *",
            )
            text = replace_once(
                text, "  const auto lookup =\n", "  FB_COST_COUNT(8);\n  const auto lookup =\n"
            )
            text = replace_once(
                text,
                "  prepare_native_dispatch(state);",
                "  FB_COST_COUNT(11);\n  prepare_native_dispatch(state);",
            )
            text = replace_once(
                text,
                "  while (true) {\n    auto &frame = active_frame(context);",
                "  while (true) {\n    FB_COST_COUNT(7);\n    auto &frame = active_frame(context);",
            )
        else:
            text += DEFINITION
            for function, index in (("runtime_block", 10), ("runtime_if", 9), ("runtime_call", 5)):
                start = text.index(f"bool {function}(")
                marker = text.index("  auto &current = *context;", start)
                text = text[:marker] + f"  FB_COST_COUNT({index});\n" + text[marker:]
            marker = "  auto &frame = current.control_stack->frames[current.control_stack->size++];"
            assert text.count(marker) == 2
            first = text.index(marker)
            text = text[:first] + "  FB_COST_COUNT(3);\n" + text[first:]
            last = text.rindex(marker)
            text = text[:last] + "  FB_COST_COUNT(4);\n" + text[last:]
            text = replace_once(
                text,
                "  auto &callee = current.call_stack->frames[current.call_stack->size];",
                "  FB_COST_COUNT(6);\n  auto &callee = current.call_stack->frames[current.call_stack->size];",
            )
        (OUT / f"{name}.patch").write_text(
            "".join(
                difflib.unified_diff(
                    original.splitlines(True),
                    text.splitlines(True),
                    fromfile=str(path.relative_to(ROOT)),
                    tofile=str(path.relative_to(ROOT)),
                )
            )
        )
        path.write_text(text)
    installed = {name: hashlib.sha256((SOURCE / name).read_bytes()).hexdigest() for name in names}
    for key, path in (
        ("build_native.sh", SOURCE / "build_native.sh"),
        (
            "jit_build_native.sh",
            ROOT / "experiments/pysim/native/tier3_plugins/jit/build_native.sh",
        ),
    ):
        original = path.read_text()
        assert (
            hashlib.sha256(path.read_bytes()).hexdigest() == manifest[str(path.relative_to(ROOT))]
        )
        (BACKUP / key).write_bytes(path.read_bytes())
        text = replace_once(
            original, "clang++ -std=c++23", "clang++ -DFB_COST_DIAGNOSTIC=1 -std=c++23"
        )
        (OUT / f"{key}.patch").write_text(
            "".join(
                difflib.unified_diff(
                    original.splitlines(True),
                    text.splitlines(True),
                    fromfile=str(path.relative_to(ROOT)),
                    tofile=str(path.relative_to(ROOT)),
                )
            )
        )
        path.write_text(text)
        installed[key] = hashlib.sha256(path.read_bytes()).hexdigest()
    (OUT / "diagnostic_source_manifest.json").write_text(json.dumps(installed, indent=2) + "\n")


if __name__ == "__main__":
    main()
