"""Fetch a pinned selection of the upstream WebAssembly Core Spec Tests."""

from __future__ import annotations

from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
SPEC_REVISION = "970c4116e644e2bf7acb39aab8b733db14ccdf28"
FILES = (
    "address",
    "comments",
    "const",
    "endianness",
    "f32",
    "f32_bitwise",
    "f32_cmp",
    "f64",
    "f64_bitwise",
    "f64_cmp",
    "float_exprs",
    "float_literals",
    "float_memory",
    "float_misc",
    "forward",
    "i32",
    "i64",
    "int_exprs",
    "int_literals",
    "labels",
    "left-to-right",
    "load",
    "local_get",
    "local_set",
    "memory_redundancy",
    "memory_size",
    "memory_trap",
    "nop",
    "return",
    "stack",
    "store",
    "switch",
    "traps",
    "unreachable",
    "utf8-custom-section-id",
    "utf8-import-field",
    "utf8-import-module",
)
BASE_URL = f"https://raw.githubusercontent.com/WebAssembly/spec/{SPEC_REVISION}"
TARGET = ROOT / "build" / "wasm-core-spec" / SPEC_REVISION


def fetch(relative_path: str, destination: Path) -> None:
    if destination.is_file():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    with urlopen(f"{BASE_URL}/{relative_path}", timeout=60) as response:
        temporary.write_bytes(response.read())
    assert temporary.stat().st_size > 0, f"empty upstream test file: {relative_path}"
    temporary.replace(destination)


def prepare() -> Path:
    fetch("LICENSE", TARGET / "LICENSE")
    fetch("README.md", TARGET / "README.md")
    test_directory = TARGET / "test" / "core"
    for name in FILES:
        fetch(f"test/core/{name}.wast", test_directory / f"{name}.wast")
    (TARGET / "revision.txt").write_text(f"{SPEC_REVISION}\n", encoding="utf-8")
    return test_directory


if __name__ == "__main__":
    print(prepare())
