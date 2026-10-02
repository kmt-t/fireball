"""Generate Fireball's explicit raw Core Wasm adapter from its restricted WIT contract.

This adapter implements interface_wit.md's raw four-import mapping. It does not
implement the Component Model Canonical ABI or the separate HAL WIT contract.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HostFunction:
    field: str
    parameters: tuple[str, ...]


_MAPPING = {
    ("trap", "fireball-call"): "fireball_call",
    ("virq", "register"): "virq_register",
    ("virq", "unregister"): "virq_unregister",
    ("vdma", "start"): "vdma_start",
}
_NAME = r"[a-z][a-z0-9-]*"


def parse_contract(source: str) -> tuple[HostFunction, ...]:
    source = re.sub(r"//[^\n]*", "", source)
    package = re.match(r"\s*package fireball:host@\d+\.\d+\.\d+;", source)
    if package is None:
        raise ValueError("expected fireball:host package")
    position = package.end()
    interfaces: list[str] = []
    functions: list[HostFunction] = []
    while match := re.match(rf"\s*interface ({_NAME})\s*\{{([^{{}}]*)\}}", source[position:]):
        interface, body = match.groups()
        if interface not in ("trap", "virq", "vdma") or interface in interfaces:
            raise ValueError("unsupported or duplicate interface")
        interfaces.append(interface)
        offset = 0
        while declaration := re.match(
            rf"\s*({_NAME}):\s*func\(([^()]*)\)\s*->\s*u32;", body[offset:]
        ):
            name, arguments = declaration.groups()
            field = _MAPPING.get((interface, name))
            if field is None or any(function.field == field for function in functions):
                raise ValueError("unsupported or duplicate raw import")
            parameters: list[str] = []
            for argument in arguments.split(",") if arguments.strip() else []:
                parameter = re.fullmatch(rf"\s*({_NAME}):\s*u32\s*", argument)
                if parameter is None:
                    raise ValueError("raw adapter supports only named u32 parameters")
                identifier = parameter[1].replace("-", "_")
                if identifier in parameters:
                    raise ValueError("duplicate parameter")
                parameters.append(identifier)
            functions.append(HostFunction(field, tuple(parameters)))
            offset += declaration.end()
        if body[offset:].strip():
            raise ValueError("unsupported interface declaration or return type")
        position += match.end()
    world = re.fullmatch(r"\s*world fireball-hostcall\s*\{([^{}]*)\}\s*", source[position:])
    if world is None:
        raise ValueError("expected exactly one fireball-hostcall world")
    imports: list[str] = []
    offset = 0
    while declaration := re.match(rf"\s*import ({_NAME});", world[1][offset:]):
        imports.append(declaration[1])
        offset += declaration.end()
    if world[1][offset:].strip() or sorted(imports) != sorted(interfaces):
        raise ValueError("world must import each declared interface exactly once")
    if {function.field for function in functions} != set(_MAPPING.values()):
        raise ValueError("raw contract must contain the four mapped functions")
    return tuple(functions)


def generate(wit: Path, destination: Path) -> None:
    functions = parse_contract(wit.read_text())
    destination.mkdir(parents=True, exist_ok=True)
    header = [
        "// Generated from fireball_hostcall_contract.wit. Do not edit.",
        "#pragma once",
        "namespace fireball {",
        "using u32 = __UINT32_TYPE__;",
        "static_assert(sizeof(u32) == 4);",
    ]
    source = ['#include "fireball_hostcall.hxx"']
    for function in functions:
        parameters = ", ".join("fireball::u32 " + name for name in function.parameters)
        arguments = ", ".join(function.parameters)
        raw = "fb_raw_" + function.field
        source.extend(
            [
                'extern "C" __attribute__((import_module("fireball"),',
                f'    import_name("{function.field}"))) fireball::u32 {raw}({parameters});',
                f"fireball::u32 fireball::{function.field}({parameters}) {{",
                f"  return {raw}({arguments});",
                "}",
            ]
        )
        header.append(f"u32 {function.field}({parameters});")
    header.append("}")
    (destination / "fireball_hostcall.hxx").write_text("\n".join(header) + "\n")
    (destination / "fireball_hostcall.cxx").write_text("\n".join(source) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    generate(arguments.wit, arguments.output)
