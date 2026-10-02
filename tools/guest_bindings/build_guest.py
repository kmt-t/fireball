"""Build the WIT-derived raw host-call archive and link a freestanding wasm32 guest."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path

from generate_hostcall_bindings import generate

REPO_ROOT = Path(__file__).resolve().parents[2]


def tool(variable: str, candidates: tuple[str, ...]) -> str:
    configured = os.environ.get(variable)
    if configured:
        return configured
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    raise FileNotFoundError(f"required tool missing: {variable} ({', '.join(candidates)})")


def build(guest: Path, output: Path) -> Path:
    generate(
        REPO_ROOT / "docs/components/tier3_platform/wit/fireball_hostcall_contract.wit", output
    )
    clang = tool("FIREBALL_CLANG", ("clang",))
    archive = tool(
        "FIREBALL_LLVM_AR",
        ("llvm-ar", "llvm-ar-21", "llvm-ar-20", "llvm-ar-19", "llvm-ar-18", "llvm-ar-17"),
    )
    linker = tool(
        "FIREBALL_WASM_LD",
        ("wasm-ld", "wasm-ld-21", "wasm-ld-20", "wasm-ld-19", "wasm-ld-18", "wasm-ld-17"),
    )
    common = [
        clang,
        "--target=wasm32",
        "-mcpu=mvp",
        "-std=c++23",
        "-O2",
        "-ffreestanding",
        "-fno-builtin",
        "-fno-exceptions",
        "-fno-rtti",
        "-Wall",
        "-Wextra",
        "-Wpedantic",
        "-Werror",
        "-I",
        str(output),
        "-I",
        str(REPO_ROOT / "experiments/pysim/native/tier3_platform/libfireball"),
    ]
    object_file = output / "fireball_hostcall.o"
    library = output / "libfireball.a"
    guest_object = output / "guest.o"
    wasm = output / "guest.wasm"
    subprocess.run(
        [*common, "-c", str(output / "fireball_hostcall.cxx"), "-o", str(object_file)], check=True
    )
    subprocess.run([archive, "rcs", str(library), str(object_file)], check=True)
    subprocess.run([*common, "-c", str(guest), "-o", str(guest_object)], check=True)
    subprocess.run(
        [
            linker,
            "--no-entry",
            "-z",
            "stack-size=4096",
            "--initial-memory=65536",
            "--max-memory=65536",
            str(guest_object),
            str(library),
            "-o",
            str(wasm),
        ],
        check=True,
    )
    return wasm


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--guest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    print(build(arguments.guest.resolve(), arguments.output.resolve()))
