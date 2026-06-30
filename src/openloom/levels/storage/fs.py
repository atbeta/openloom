"""Local filesystem connector — zero-dependency, always available.

Reference in config.yaml:

    storage:
      class: openloom.levels.storage.fs.FilesystemConnector
      kwargs:
        root: /path/to/watch
      inbox: inbox
"""

from __future__ import annotations

from pathlib import Path as FSPath
from pathlib import PurePosixPath

from openloom.levels.storage.base import Connector, FileEntry


class FilesystemConnector(Connector):
    """5-method connector backed by the local filesystem.

    ``root`` is the base directory; all paths the runner passes are
    relative to ``root``.
    """

    def __init__(self, root: str, **kwargs):
        super().__init__()
        self._root = FSPath(root).expanduser().resolve()
        if not self._root.is_dir():
            self._root.mkdir(parents=True, exist_ok=True)

    # -- path resolution -------------------------------------------------
    def _resolve(self, path: str) -> FSPath:
        p = PurePosixPath(path)
        resolved = self._root.joinpath(*p.parts)
        # Guard against path traversal
        resolved = resolved.resolve()
        if not str(resolved).startswith(str(self._root)):
            raise ValueError(f"path traversal denied: {path!r}")
        return resolved

    # -- Connector methods -----------------------------------------------
    def ls(self, path: str) -> list[FileEntry]:
        target = self._resolve(path)
        if not target.is_dir():
            return []
        entries: list[FileEntry] = []
        for item in sorted(target.iterdir()):
            if item.is_file():
                st = item.stat()
                entries.append(FileEntry(
                    path=str(item.relative_to(self._root)),
                    name=item.name,
                    size=st.st_size,
                ))
        return entries

    def download(self, path: str) -> bytes | None:
        target = self._resolve(path)
        if not target.is_file():
            return None
        return target.read_bytes()

    def upload(self, path: str, content: bytes) -> None:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    def delete(self, path: str) -> None:
        target = self._resolve(path)
        if target.is_file():
            target.unlink(missing_ok=True)

    def move(self, source: str, dest: str) -> None:
        src = self._resolve(source)
        dst = self._resolve(dest)
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
