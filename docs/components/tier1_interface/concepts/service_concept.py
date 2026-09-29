"""
docs/components/tier1_interface/concepts/service_concept.py
Reference Concept Implementation: isolated WASM service lifecycle

The concept models the service boundary only. IPC routing and physical
subsystem behavior remain responsibilities of their respective components.
"""

from dataclasses import dataclass

BACKS = [
    "components/tier1_interface/system_service.md",
]


class ServiceState:
    LOADED = "Loaded"
    RUNNING = "Running"
    FAILED = "Failed"


class LoadResult:
    SUCCESS = "SUCCESS"
    RETRY = "RETRY"
    RESTART = "RESTART"
    PANIC = "PANIC"


@dataclass
class ServiceRecord:
    uri: str
    state: str = ServiceState.LOADED
    tcb_generation: int = 0
    heap_generation: int = 0


class ServiceManager:
    """Fixed-configuration service registry with isolated restart semantics."""

    def __init__(self, configured_uris: tuple[str, ...]) -> None:
        self._configured_uris = frozenset(configured_uris)
        self._services: dict[str, ServiceRecord] = {}

    def load_service(self, uri: str, initialization_result: str = LoadResult.SUCCESS) -> str:
        """Model the lower-level initializer result; the product API accepts only ``uri``."""
        if uri not in self._configured_uris:
            # A URI outside the compile-time configuration is a fatal
            # configuration error; the public contract exposes PANIC.
            return LoadResult.PANIC
        assert (
            initialization_result == LoadResult.SUCCESS
            or initialization_result == LoadResult.RETRY
            or initialization_result == LoadResult.RESTART
            or initialization_result == LoadResult.PANIC
        )
        if initialization_result != LoadResult.SUCCESS:
            return initialization_result
        record = self._services.get(uri)
        if record is None:
            record = ServiceRecord(uri=uri)
            self._services[uri] = record
        record.state = ServiceState.LOADED
        return LoadResult.SUCCESS

    def start_guest(self, uri: str) -> None:
        """Start a successfully loaded guest at the explicit lifecycle boundary."""
        record = self._require_service(uri)
        assert record.state == ServiceState.LOADED
        record.state = ServiceState.RUNNING

    def fail_service(self, uri: str) -> None:
        record = self._require_service(uri)
        record.state = ServiceState.FAILED

    def handle_fault_event(self, uri: str) -> None:
        record = self._require_service(uri)
        if record.state != ServiceState.FAILED:
            raise ValueError("fault event requires a failed service")
        record.tcb_generation += 1
        record.heap_generation += 1
        record.state = ServiceState.LOADED

    def state(self, uri: str) -> str:
        return self._require_service(uri).state

    def generations(self, uri: str) -> tuple[int, int]:
        record = self._require_service(uri)
        return record.tcb_generation, record.heap_generation

    def _require_service(self, uri: str) -> ServiceRecord:
        record = self._services.get(uri)
        if record is None:
            raise KeyError(f"service is not loaded: {uri}")
        return record


def test_fixed_uri_load_and_rejection() -> None:
    service_uri = "fireball://service/demo/0"
    manager = ServiceManager((service_uri,))
    assert manager.load_service(service_uri) == LoadResult.SUCCESS
    assert manager.state(service_uri) == ServiceState.LOADED
    manager.start_guest(service_uri)
    assert manager.state(service_uri) == ServiceState.RUNNING
    assert manager.load_service("fireball://service/unknown/0") == LoadResult.PANIC


def test_fault_isolation_and_targeted_restart() -> None:
    service_a = "fireball://service/a/0"
    service_b = "fireball://service/b/0"
    manager = ServiceManager((service_a, service_b))
    assert manager.load_service(service_a) == LoadResult.SUCCESS
    manager.start_guest(service_a)
    assert manager.load_service(service_b) == LoadResult.SUCCESS
    manager.start_guest(service_b)
    before_b = manager.generations(service_b)

    manager.fail_service(service_a)
    assert manager.state(service_a) == ServiceState.FAILED
    assert manager.state(service_b) == ServiceState.RUNNING

    manager.handle_fault_event(service_a)
    assert manager.state(service_a) == ServiceState.LOADED
    manager.start_guest(service_a)
    assert manager.state(service_a) == ServiceState.RUNNING
    assert manager.generations(service_a) == (1, 1)
    assert manager.generations(service_b) == before_b


def test_load_failures_return_recovery_strategy_without_starting_service() -> None:
    service_uri = "fireball://service/demo/0"
    for outcome in (LoadResult.RETRY, LoadResult.RESTART, LoadResult.PANIC):
        manager = ServiceManager((service_uri,))
        assert manager.load_service(service_uri, initialization_result=outcome) == outcome
        assert service_uri not in manager._services


if __name__ == "__main__":
    test_fixed_uri_load_and_rejection()
    test_fault_isolation_and_targeted_restart()
    test_load_failures_return_recovery_strategy_without_starting_service()
    print("[PASS] Service concept lifecycle and fault-isolation tests passed.")
