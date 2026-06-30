"""Storage configuration — parsed from ``openloom.yaml`` ``storage:`` section."""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .base import Connector

# Paths where user connector classes live — added to sys.path automatically.
_USER_CONNECTORS_DIRS = [
    Path.home() / ".openloom" / "connectors",  # pip / uv tool install
    Path("connectors"),                            # portable / zip deployment
]


@dataclass(frozen=True)
class StorageConfig:
    """Resolved storage configuration. ``kwargs`` is passed verbatim to
    the connector constructor."""

    connector_class: type[Connector]
    connector_kwargs: dict[str, Any] = field(default_factory=dict)
    inbox_dir: str = "/tasks/incoming"
    outbox_dir: str = "/tasks/results"
    archive_dir: str = ""
    poll_interval_seconds: int = 10
    task_prefix: str = "task-"

    @property
    def enabled(self) -> bool:
        return self.connector_class is not None  # type: ignore[has-type]

    @classmethod
    def empty(cls) -> StorageConfig:
        return cls(connector_class=None)  # type: ignore[arg-type]

    @classmethod
    def from_mapping(cls, raw: Any) -> StorageConfig:
        if raw is None or (isinstance(raw, dict) and not raw):
            return cls.empty()

        if not isinstance(raw, dict):
            raise ValueError("storage config must be a mapping")

        class_path = raw.get("class")
        if not class_path:
            return cls.empty()

        # Add user connectors dir to sys.path so "my_cloud.MyConnector" resolves.
        _ensure_connectors_path()

        cls_ = _import_class(str(class_path))
        if not (isinstance(cls_, type) and issubclass(cls_, Connector)):
            raise TypeError(f"{class_path!r} must subclass Connector")

        kwargs = dict(raw.get("kwargs") or {})
        if not isinstance(kwargs, dict):
            raise ValueError("storage.kwargs must be a mapping")

        paths = raw.get("paths") or {}
        if isinstance(paths, str):
            paths = {"inbox": paths}

        return cls(
            connector_class=cls_,
            connector_kwargs=kwargs,
            inbox_dir=str(raw.get("inbox") or paths.get("inbox") or "/tasks/incoming"),
            outbox_dir=str(raw.get("outbox") or paths.get("outbox") or "/tasks/results"),
            archive_dir=str(raw.get("archive") or paths.get("archive") or ""),
            poll_interval_seconds=int(raw.get("poll_interval_seconds") or 10),
            task_prefix=str(raw.get("task_prefix") or "task-"),
        )


def _ensure_connectors_path() -> None:
    for d in _USER_CONNECTORS_DIRS:
        d_abs = d.resolve()
        if not d_abs.exists():
            d.mkdir(parents=True, exist_ok=True)
        sys_path = str(d_abs)
        if sys_path not in sys.path:
            sys.path.insert(0, sys_path)


def _import_class(dotted: str) -> type:
    module_path, _, class_name = dotted.rpartition(".")
    if not module_path:
        raise ImportError(f"invalid class path: {dotted!r} (expected MODULE.CLASS)")
    mod = importlib.import_module(module_path)
    cls_ = getattr(mod, class_name, None)
    if cls_ is None:
        raise ImportError(f"class {class_name!r} not found in {module_path!r}")
    return cls_
