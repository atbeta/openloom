from __future__ import annotations

from typing import Any

from .checker import Checker
from .sink import Sink
from .source import TaskSource

_sources: dict[str, type[TaskSource]] = {}
_checkers: dict[str, type[Checker]] = {}
_sinks: dict[str, type[Sink]] = {}


def _register(map_: dict[str, type], name: str):
    def decorator(cls):
        map_[name] = cls
        return cls
    return decorator


def register_source(name: str):
    return _register(_sources, name)


def register_checker(name: str):
    return _register(_checkers, name)


def register_sink(name: str):
    return _register(_sinks, name)


def get_source(name: str) -> type[TaskSource]:
    if name not in _sources:
        raise KeyError(f"Unknown source: {name}. Available: {list(_sources)}")
    return _sources[name]


def get_checker(name: str) -> type[Checker]:
    if name not in _checkers:
        raise KeyError(f"Unknown checker: {name}. Available: {list(_checkers)}")
    return _checkers[name]


def get_sink(name: str) -> type[Sink]:
    if name not in _sinks:
        raise KeyError(f"Unknown sink: {name}. Available: {list(_sinks)}")
    return _sinks[name]


def list_all() -> dict[str, dict[str, Any]]:
    return {
        "sources": {n: {"module": c.__module__} for n, c in _sources.items()},
        "checkers": {n: {"module": c.__module__} for n, c in _checkers.items()},
        "sinks": {n: {"module": c.__module__} for n, c in _sinks.items()},
    }
