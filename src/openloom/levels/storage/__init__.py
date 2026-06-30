"""
Storage level — poll a storage backend for task files, push to harness,
subscribe to EventBus for lifecycle events, write status + result files back.

Users implement ``Connector`` (5 methods) and register via
``~/.openloom/config.yaml`` → ``storage.class``.
"""

from __future__ import annotations

from .base import Connector, FileEntry
from .config import StorageConfig
from .runner import StorageRunner

__all__ = [
    "Connector",
    "FileEntry",
    "StorageConfig",
    "StorageRunner",
]
