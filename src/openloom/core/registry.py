"""
Plugin registry for notify sinks.

The 0.12 harness does not need a checker / source / plan
registry (those existed for the manual-mode runtime). The only
plugin surface that survives is the sink registry: every
``register_sink("name")`` call adds a class to the
``_sinks`` map, and ``get_sink("name")`` returns it for
harness wiring.
"""

from __future__ import annotations

from .sink import Sink

_sinks: dict[str, type[Sink]] = {}


def register_sink(name: str):
    """Decorator: ``@register_sink("web") class WebSink(Sink): ...``.

    The decorator pattern keeps the import side-effect local to
    the sink module — the harness factory calls ``get_sink("web")``
    to look up the class without having to know its symbol.
    """

    def decorator(cls: type[Sink]) -> type[Sink]:
        _sinks[name] = cls
        return cls

    return decorator


def get_sink(name: str) -> type[Sink]:
    if name not in _sinks:
        raise KeyError(
            f"Unknown sink: {name}. Available: {sorted(_sinks)}",
        )
    return _sinks[name]
