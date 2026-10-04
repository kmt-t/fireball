"""QA-only registry and linker model for the future runtime-loader contract.

This harness consumes modules parsed by the active reference parser. It does
not provide or replace a runtime linking implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from bump_allocator import BumpAllocator
from tier2_runtime.wasm.module import Module
from tier2_runtime.wasm.reader import parse

QA_MAX_REGISTERED_MODULES = 4


@dataclass(frozen=True, slots=True)
class QAFunctionTarget:
    module_name: str
    function_index: int


@dataclass(slots=True)
class QALinkModule:
    name: str
    module: Module
    resolved_imports: tuple[QAFunctionTarget, ...] = ()

    @property
    def is_ready(self) -> bool:
        return len(self.resolved_imports) == len(self.module.imports)


@dataclass(slots=True)
class QAModuleLinkHarness:
    """Keep registry/link state inside QA while reusing the active WASM parser."""

    allocator: BumpAllocator = field(default_factory=BumpAllocator)
    _modules: dict[str, QALinkModule] = field(default_factory=dict, repr=False)

    def prepare(self, name: str, binary: bytes) -> QALinkModule:
        assert name not in self._modules, f"duplicate module name: {name}"
        assert len(self._modules) < QA_MAX_REGISTERED_MODULES, (
            "QA module registry capacity exceeded"
        )
        module = parse(memoryview(binary), self.allocator)
        registered = QALinkModule(name, module)
        self._modules[name] = registered
        return registered

    def lookup(self, name: str) -> QALinkModule | None:
        return self._modules.get(name)

    def resolve_imports(self, module: QALinkModule) -> bool:
        assert self._modules.get(module.name) is module, "module is not registered"
        resolved: list[QAFunctionTarget] = []
        for import_index in range(len(module.module.imports)):
            dependency_name = module.module.import_module_name(import_index)
            dependency = self._modules.get(dependency_name)
            assert dependency is not None, f"dependency module is unresolved: {dependency_name}"
            field_name = module.module.import_field_name(import_index)
            function_index = _find_exported_function(dependency.module, field_name)
            assert _signature(module.module, import_index) == _signature(
                dependency.module, function_index
            ), f"function signature mismatch for {dependency_name}.{field_name}"
            resolved.append(QAFunctionTarget(dependency_name, function_index))

        module.resolved_imports = tuple(resolved)
        return module.is_ready


def _find_exported_function(module: Module, name: str) -> int:
    assert module.source is not None
    encoded_name = name.encode("utf-8")
    for export in module.exports:
        if export.kind == 0 and module.source[
            export.name_offset : export.name_offset + export.name_size
        ] == encoded_name:
            return export.index
    assert False, f"unresolved function export: {name}"


def _signature(module: Module, function_index: int) -> tuple[tuple[int, ...], tuple[int, ...]]:
    function_type = module.func_type(function_index)
    assert function_type.params is not None
    assert function_type.results is not None
    return tuple(function_type.params), tuple(function_type.results)
