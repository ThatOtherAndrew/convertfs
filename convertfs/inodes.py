"""Inode allocation and entry tracking for the FUSE filesystem.

The filesystem maintains a tree of entries rooted at ROOT_INODE. Each entry is
either:

  - a directory (synthetic; the root or a converter-declared OUTPUT_DIR)
  - a real input file (registered when the kernel creates or renames a file
    into the mount; we record the canonical path the user wrote to)
  - a virtual output file (a converter-declared OUTPUT_FILE template, with the
    captured stem substituted in)

Inodes are allocated from a monotonic counter starting at ROOT_INODE + 1.
Allocation is keyed by mount-relative path so the same path always resolves
to the same inode for the lifetime of the process.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import pyfuse3


class EntryKind(Enum):
    DIRECTORY = 'directory'
    REAL_FILE = 'real_file'
    VIRTUAL_FILE = 'virtual_file'


@dataclass
class Entry:
    inode: int
    name: str  # leaf name, empty for the root
    parent_inode: int  # equal to own inode for the root
    kind: EntryKind
    # Children (for directories) keyed by leaf name.
    children: dict[str, int] = field(default_factory=dict)
    # For VIRTUAL_FILE: the source file's mount-relative path (used to invoke
    # the converter on read).
    source_path: Path | None = None


class InodeStore:
    """Owns the inode <-> entry mapping and the directory tree."""

    def __init__(self) -> None:
        self._next_inode: int = pyfuse3.ROOT_INODE + 1
        self._entries: dict[int, Entry] = {}
        # Mount-relative path -> inode. Root maps from PurePosixPath('.').
        self._path_to_inode: dict[Path, int] = {}

        root = Entry(
            inode=pyfuse3.ROOT_INODE,
            name='',
            parent_inode=pyfuse3.ROOT_INODE,
            kind=EntryKind.DIRECTORY,
        )
        self._entries[pyfuse3.ROOT_INODE] = root
        self._path_to_inode[Path()] = pyfuse3.ROOT_INODE

    # ---- read access ----

    def root(self) -> Entry:
        return self._entries[pyfuse3.ROOT_INODE]

    def get(self, inode: int) -> Entry | None:
        return self._entries.get(inode)

    def by_path(self, path: Path) -> Entry | None:
        inode = self._path_to_inode.get(path)
        return self._entries.get(inode) if inode is not None else None

    def child(self, parent: Entry, name: str) -> Entry | None:
        inode = parent.children.get(name)
        return self._entries.get(inode) if inode is not None else None

    def path_for(self, inode: int) -> Path | None:
        entry = self._entries.get(inode)
        if entry is None:
            return None
        if entry.inode == pyfuse3.ROOT_INODE:
            return Path()
        # Walk up to the root.
        parts: list[str] = []
        current = entry
        while current.inode != pyfuse3.ROOT_INODE:
            parts.append(current.name)
            current = self._entries[current.parent_inode]
        return Path(*reversed(parts))

    # ---- mutation ----

    def ensure_directory(self, path: Path) -> Entry:
        """Create directory entries for `path` and any missing ancestors."""
        if path == Path() or path == Path():
            return self.root()
        existing = self.by_path(path)
        if existing is not None:
            if existing.kind != EntryKind.DIRECTORY:
                msg = f'path {path} exists and is not a directory ({existing.kind})'
                raise ValueError(msg)
            return existing

        parent_path = path.parent if path.parent != path else Path()
        parent = self.ensure_directory(parent_path)
        return self._create_child(parent, path.name, EntryKind.DIRECTORY, None)

    def add_file(
        self,
        parent: Entry,
        name: str,
        kind: EntryKind,
        source_path: Path | None = None,
    ) -> Entry:
        """Create or fetch a file entry under `parent`."""
        existing = self.child(parent, name)
        if existing is not None:
            return existing
        return self._create_child(parent, name, kind, source_path)

    def _create_child(
        self,
        parent: Entry,
        name: str,
        kind: EntryKind,
        source_path: Path | None,
    ) -> Entry:
        inode = self._next_inode
        self._next_inode += 1
        entry = Entry(
            inode=inode,
            name=name,
            parent_inode=parent.inode,
            kind=kind,
            source_path=source_path,
        )
        self._entries[inode] = entry
        parent.children[name] = inode
        # Record the mount-relative path.
        parent_path = self.path_for(parent.inode) or Path()
        child_path = Path(name) if parent_path == Path() else parent_path / name
        self._path_to_inode[child_path] = inode
        return entry
