"""Build real wasi-libc reactors and an explicit test-only Fireball ABI fixture."""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import re
import subprocess
import tarfile
from pathlib import Path
from urllib.request import urlopen

from generate_hostcall_bindings import generate

ROOT = Path(__file__).resolve().parents[2]
SDK_DIRECTORY = "wasi-sdk-27.0-x86_64-linux"
SDK_SHA256 = "b7d4d944c88503e4f21d84af07ac293e3440b1b6210bfd7fe78e0afd92c23bc2"
SDK_URL = (
    f"https://github.com/WebAssembly/wasi-sdk/releases/download/wasi-sdk-27/{SDK_DIRECTORY}.tar.gz"
)
EXPORTS = ("result_address", "write_probe", "read_probe", "clock_probe", "close_probe")


def sdk_path() -> Path:
    configured = os.environ.get("FIREBALL_WASI_SDK")
    return Path(configured) if configured else ROOT / "build/wasi-sdk" / SDK_DIRECTORY


def prepare_sdk() -> Path:
    """Fetch the pinned Linux SDK only during an explicit setup command."""
    assert platform.system() == "Linux" and platform.machine() == "x86_64"
    destination = ROOT / "build/wasi-sdk"
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / f"{SDK_DIRECTORY}.tar.gz"
    if not archive.is_file():
        partial = archive.with_suffix(".part")
        with urlopen(SDK_URL, timeout=60) as source, partial.open("wb") as output:
            while chunk := source.read(1024 * 1024):
                output.write(chunk)
        partial.replace(archive)
    with archive.open("rb") as source:
        assert hashlib.file_digest(source, "sha256").hexdigest() == SDK_SHA256
    with tarfile.open(archive) as package:
        package.extractall(destination, filter="data")
    return destination / SDK_DIRECTORY


def build(output: Path, *, fireball: bool, driver_stub: bool = False) -> Path:
    assert not (fireball and driver_stub), "driver-stub imports are a separate QA profile"
    sdk = sdk_path()
    suffix = ".exe" if os.name == "nt" else ""
    clang = sdk / f"bin/clang{suffix}"
    assert clang.is_file(), (
        "WASI-SDK is required: run tools/guest_bindings/build_wasi_guest.py --prepare-sdk "
        "or set FIREBALL_WASI_SDK; tests never download or silently skip it"
    )
    compiler_version = subprocess.run(
        [str(clang), "--version"], check=True, capture_output=True, text=True
    ).stdout
    major = re.search(r"clang version (\d+)", compiler_version)
    assert major is not None and int(major[1]) >= 17, "Fireball requires Clang 17 or later"
    libc = sdk / "share/wasi-sysroot/lib/wasm32-wasip1/libc.a"
    assert libc.is_file(), "the actual wasi-libc archive is required"
    output.mkdir(parents=True, exist_ok=True)
    guest_directory = ROOT / "experiments/pysim/qa/tier3_platform/guest"
    source = "driver_stub_probe.c" if driver_stub else "wasi_libc_probe.c"
    exports = ("command_probe", "stream_probe") if driver_stub else EXPORTS
    common = [
        str(clang),
        "--target=wasm32-wasip1",
        "-mcpu=mvp",
        "-O2",
        "-D_POSIX_C_SOURCE=200809L",
        "-Wall",
        "-Wextra",
        "-Werror",
    ]
    guest_object = output / "guest.o"
    subprocess.run(
        [
            *common,
            "-std=c23",
            "-c",
            str(guest_directory / source),
            "-o",
            str(guest_object),
        ],
        check=True,
    )
    objects = [str(guest_object)]
    link_options: list[str] = []
    if fireball:
        generate(ROOT / "docs/components/tier3_platform/wit/fireball_hostcall_contract.wit", output)
        for source in (
            output / "fireball_hostcall.cxx",
            guest_directory / "wasi_syscall_fixture.cxx",
        ):
            target = output / f"{source.stem}.o"
            subprocess.run(
                [
                    *common,
                    "-std=c++23",
                    "-fno-exceptions",
                    "-fno-rtti",
                    "-I",
                    str(output),
                    "-I",
                    str(ROOT / "experiments/pysim/native/tier3_platform/libfireball"),
                    "-c",
                    str(source),
                    "-o",
                    str(target),
                ],
                check=True,
            )
            objects.append(str(target))
        link_options = [
            f"-Wl,--wrap=__wasi_{name}"
            for name in ("fd_write", "fd_read", "fd_close", "clock_time_get")
        ]
    wasm = output / "guest.wasm"
    subprocess.run(
        [
            *common,
            "-mexec-model=reactor",
            *objects,
            *link_options,
            *(f"-Wl,--export={name}" for name in exports),
            "-Wl,-z,stack-size=8192",
            "-Wl,--initial-memory=65536",
            "-Wl,--max-memory=65536",
            f"-Wl,-Map,{output / 'guest.map'}",
            "-o",
            str(wasm),
        ],
        check=True,
    )
    (output / "compiler.txt").write_text(compiler_version)
    return wasm


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-sdk", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--fireball", action="store_true")
    parser.add_argument("--driver-stub", action="store_true")
    arguments = parser.parse_args()
    if arguments.prepare_sdk:
        print(prepare_sdk())
    else:
        assert arguments.output is not None
        print(
            build(
                arguments.output.resolve(),
                fireball=arguments.fireball,
                driver_stub=arguments.driver_stub,
            )
        )
