"""
Plugin registries for sinks and webhook source parsers.

Two parallel registries:

- **Sink registry** — maps a name to a ``Sink`` subclass.
  ``register_sink("web")`` decorates the class; ``get_sink("web")``
  retrieves it for harness wiring.

- **Source registry** — maps a name to a ``SourceParser`` instance.
  ``register_source("github")`` decorates the class (instantiated
  automatically); ``get_source("github")`` retrieves the instance
  for the inbound webhook route.
"""

from __future__ import annotations

from .sink import Sink
from .webhook_types import SourceParser

# ── Sink registry ──────────────────────────────────────────────────────────

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


# ── Source parser registry ─────────────────────────────────────────────────

_sources: dict[str, SourceParser] = {}


def register_source(name: str):
    """Decorator: ``@register_source("github") class GitHubSource(SourceParser): ...``.

    The class is instantiated once and stored. The inbound webhook
    route calls ``get_source(name)`` to retrieve the parser.
    """

    def decorator(cls: type[SourceParser]) -> type[SourceParser]:
        _sources[name] = cls()
        return cls

    return decorator


def get_source(name: str) -> SourceParser:
    if name not in _sources:
        raise KeyError(
            f"Unknown webhook source: {name}. Available: {sorted(_sources)}",
        )
    return _sources[name]


def list_sources() -> list[str]:
    """Return registered source names."""
    return sorted(_sources.keys())
