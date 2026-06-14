"""
Re-export notify config types from core/ for backward compatibility.

The data classes and their factory methods (``from_mapping``, ``from_env``)
now live in ``openloom.core.notify_config``; this module keeps the original
import paths working.
"""

from __future__ import annotations

from openloom.core.notify_config import (
    FileEntry,
    NotifyConfig,
    WebhookEntry,
)

__all__ = [
    "FileEntry",
    "NotifyConfig",
    "WebhookEntry",
]
