from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Generic, TypeVar


T = TypeVar("T")
Factory = Callable[[Mapping[str, Any]], T]


class Registry(Generic[T]):
    """Small explicit registry used by runtime component factories.

    Registries keep configuration dispatch open for extension without growing
    `if/elif` chains in `config.py`. Third-party or lab-only modules can add a
    factory at startup without modifying the core runtime.
    """

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._factories: dict[str, Factory[T]] = {}

    def register(
        self,
        name: str,
        factory: Factory[T],
        *,
        aliases: tuple[str, ...] = (),
        replace: bool = False,
    ) -> None:
        names = (name, *aliases)
        for candidate in names:
            if not candidate or not candidate.strip():
                raise ValueError(f"{self.kind} name must be non-empty")
            if candidate in self._factories and not replace:
                raise ValueError(f"{self.kind} already registered: {candidate}")

        for candidate in names:
            self._factories[candidate] = factory

    def build(self, name: str, config: Mapping[str, Any]) -> T:
        try:
            factory = self._factories[name]
        except KeyError as exc:
            available = ", ".join(sorted(self._factories)) or "<none>"
            raise ValueError(
                f"unknown {self.kind}: {name}; available: {available}"
            ) from exc
        return factory(config)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))


PROVIDER_REGISTRY: Registry[Any] = Registry("provider")
TRANSPORT_REGISTRY: Registry[Any] = Registry("transport")
CLOCK_REGISTRY: Registry[Any] = Registry("clock")
