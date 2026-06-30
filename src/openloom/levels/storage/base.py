"""Base types for storage connectors.

Implement ``Connector`` (5 methods: ``ls``, ``download``, ``upload``,
``move``, ``delete``) and configure via ``storage.class`` in
``~/.openloom/config.yaml``.

``Connector`` methods are pure path operations — the runner maintains
the inbox/outbox/archive directory semantics.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class FileEntry:
    """A file in storage. ``name`` and ``size`` are best-effort hints."""

    path: str
    name: str = ""
    size: int = 0


class Connector(ABC):
    """5-method storage contract. Directory semantics live in the runner.

    ``move`` has a default fallback (download + upload + delete);
    override it for backends with native copy/rename to save API calls.
    """

    @abstractmethod
    def ls(self, path: str) -> list[FileEntry]:
        """List files in *path*."""

    @abstractmethod
    def download(self, path: str) -> bytes | None:
        """Download *path* contents. Return ``None`` if not found."""

    @abstractmethod
    def upload(self, path: str, content: bytes) -> None:
        """Upload *content* to *path*."""

    @abstractmethod
    def delete(self, path: str) -> None:
        """Delete *path*. No-op if missing."""

    def move(self, source: str, dest: str) -> None:
        """Move/rename *source* to *dest*.

        Default fallback: download + upload + delete. Override for
        backends with native rename (one API call instead of three).
        """
        data = self.download(source)
        if data is not None:
            self.upload(dest, data)
            self.delete(source)
