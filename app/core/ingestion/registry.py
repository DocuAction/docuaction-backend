"""Which sources exist, for which program.

Keyed by `program:source_name`, so two programs may both have a source called
`NPPES` without one resolving the other's connector. That is the ingestion-side
expression of the Option D boundary: a TEFCA run cannot reach an ERP source by
asking for a name that happens to match.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

from app.core.ingestion.contracts import SourceConnector, SourceDescriptor


class SourceRegistry:
    """A program-scoped registry of source connectors."""

    def __init__(self) -> None:
        self._factories: Dict[str, Callable[[], SourceConnector]] = {}
        self._descriptors: Dict[str, SourceDescriptor] = {}

    def register(self, descriptor: SourceDescriptor,
                 factory: Callable[[], SourceConnector]) -> SourceDescriptor:
        key = descriptor.key()
        if key in self._factories:
            raise ValueError(
                f"{key} is already registered. Two connectors under one key "
                f"makes which one ran unanswerable from a stored run.")
        self._factories[key] = factory
        self._descriptors[key] = descriptor
        return descriptor

    def get(self, program: str, source_name: str) -> SourceConnector:
        key = f"{program}:{source_name}"
        try:
            return self._factories[key]()
        except KeyError:
            available = sorted(k for k in self._factories
                               if k.startswith(f"{program}:"))
            raise LookupError(
                f"no connector registered for {key}. Registered for "
                f"{program}: {available}") from None

    def describe(self, program: str, source_name: str) -> SourceDescriptor:
        return self._descriptors[f"{program}:{source_name}"]

    def for_program(self, program: str) -> List[SourceDescriptor]:
        return sorted(
            (d for k, d in self._descriptors.items() if k.startswith(f"{program}:")),
            key=lambda d: d.source_name)

    def programs(self) -> List[str]:
        return sorted({d.program for d in self._descriptors.values()})

    def __contains__(self, key: str) -> bool:
        return key in self._factories

    def __len__(self) -> int:
        return len(self._factories)


#: The process-wide registry. A program registers into it at import time.
REGISTRY = SourceRegistry()
