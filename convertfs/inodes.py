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
    # For VIRTUAL_FILE: the source file's inode (used to invoke the converter
    # on read and to evict virtuals when the source is removed).
    source_inode: int | None = None
    # Times in nanoseconds. Used for purely virtual entities (root, synthetic
    # dirs, virtual files). For real files/dirs, the FUSE layer reads these
    # from the underlying filesystem via fstat/stat with dir_fd.
    atime_ns: int = 0
    mtime_ns: int = 0
    ctime_ns: int = 0
    # File mode permission bits (the type bits are derived from kind).
    # Same caveat: only authoritative for purely virtual entities.
    perm: int = 0o644
    # Whether this directory was synthesised by a converter's OUTPUT_DIRS
    # declaration. Synthesised dirs are auto-removed when they become empty.
    is_synthetic: bool = False
    # For VIRTUAL_FILE: the materialised converter output, populated lazily
    # on first open. The output is held on disk in a tempfile (not in RAM)
    # so large outputs don't pin memory; reads are served via os.pread on
    # `cached_fd`. While None, the file's reported size is 0. Invalidated
    # when the source's content changes.
    cached_path: Path | None = None
    cached_fd: int | None = None
    cached_size: int = 0
    # For VIRTUAL_FILE: True once at least one open of this virtual has
    # been released (i.e. someone opened it and let it go). Used as the
    # heuristic for 'was successfully extracted' before deciding whether
    # an unlink is a drag-out vs a plain `rm`. Set on release rather than
    # on read because some applications (e.g. Nautilus using copy_file_range
    # / splice) never call our read handler explicitly — they get bytes
    # via kernel-side fast paths or via the size-aware copy machinery.
    was_opened_then_released: bool = False


class InodeStore:
    """Owns the inode <-> entry mapping and the directory tree."""

    def __init__(self, ctime_ns: int) -> None:
        self._next_inode: int = pyfuse3.ROOT_INODE + 1
        self._entries: dict[int, Entry] = {}
        # Mount-relative path -> inode. Root maps from Path().
        self._path_to_inode: dict[Path, int] = {}
        # Map source-file inode -> set of virtual entry inodes derived from
        # it. Used to cascade removal when a real file is unlinked.
        self._derivatives: dict[int, set[int]] = {}

        root = Entry(
            inode=pyfuse3.ROOT_INODE,
            name='',
            parent_inode=pyfuse3.ROOT_INODE,
            kind=EntryKind.DIRECTORY,
            atime_ns=ctime_ns,
            mtime_ns=ctime_ns,
            ctime_ns=ctime_ns,
            perm=0o755,
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

    def derivatives_of(self, source_inode: int) -> set[int]:
        return self._derivatives.get(source_inode, set())

    def all_entries(self) -> list[Entry]:
        """Snapshot of every entry currently tracked. Used for shutdown sweeps."""
        return list(self._entries.values())

    # ---- mutation ----

    def ensure_directory(
        self,
        path: Path,
        *,
        now_ns: int,
        is_synthetic: bool = False,
    ) -> Entry:
        """Create directory entries for `path` and any missing ancestors."""
        if path == Path():
            return self.root()
        existing = self.by_path(path)
        if existing is not None:
            if existing.kind != EntryKind.DIRECTORY:
                msg = f'path {path} exists and is not a directory ({existing.kind})'
                raise ValueError(msg)
            return existing

        parent_path = path.parent if path.parent != path else Path()
        parent = self.ensure_directory(
            parent_path, now_ns=now_ns, is_synthetic=is_synthetic,
        )
        return self._create_child(
            parent,
            path.name,
            EntryKind.DIRECTORY,
            now_ns=now_ns,
            is_synthetic=is_synthetic,
        )

    def add_file(
        self,
        parent: Entry,
        name: str,
        kind: EntryKind,
        *,
        now_ns: int,
        source_inode: int | None = None,
        perm: int = 0o644,
    ) -> Entry:
        """Create or fetch a file entry under `parent`."""
        existing = self.child(parent, name)
        if existing is not None:
            return existing
        return self._create_child(
            parent,
            name,
            kind,
            now_ns=now_ns,
            source_inode=source_inode,
            perm=perm,
        )

    def add_directory(
        self,
        parent: Entry,
        name: str,
        *,
        now_ns: int,
        perm: int = 0o755,
        is_synthetic: bool = False,
    ) -> Entry:
        """Create a single directory under `parent` (no recursion)."""
        existing = self.child(parent, name)
        if existing is not None:
            if existing.kind != EntryKind.DIRECTORY:
                msg = f'{name} exists under inode {parent.inode} as {existing.kind}'
                raise ValueError(msg)
            return existing
        return self._create_child(
            parent,
            name,
            EntryKind.DIRECTORY,
            now_ns=now_ns,
            perm=perm,
            is_synthetic=is_synthetic,
        )

    def remove(self, entry: Entry) -> None:
        """Remove `entry`. Cascades to derivatives if it's a real file.

        Synthetic ancestor directories that become empty as a result are
        also removed. Non-synthetic empty directories are kept.
        """
        # Real files: cascade-remove all virtual derivatives first.
        if entry.kind == EntryKind.REAL_FILE:
            derivatives = list(self._derivatives.get(entry.inode, set()))
            for d_inode in derivatives:
                d_entry = self._entries.get(d_inode)
                if d_entry is not None:
                    self._detach(d_entry)
            self._derivatives.pop(entry.inode, None)

        # Detach the entry itself.
        self._detach(entry)

    def move(
        self, entry: Entry, new_parent: Entry, new_name: str, *, now_ns: int,
    ) -> None:
        """Rename/move `entry` to live under `new_parent` with `new_name`.

        Updates the path-to-inode index and the parent's children mapping.
        Does not re-run any converter resolution; the FS layer is responsible
        for re-registering derivatives if `entry` is a real file.
        """
        if entry.inode == pyfuse3.ROOT_INODE:
            msg = 'cannot move the root entry'
            raise ValueError(msg)

        # Remove derivatives tied to this real file under its old name.
        if entry.kind == EntryKind.REAL_FILE:
            derivatives = list(self._derivatives.get(entry.inode, set()))
            for d_inode in derivatives:
                d_entry = self._entries.get(d_inode)
                if d_entry is not None:
                    self._detach(d_entry)
            self._derivatives.pop(entry.inode, None)

        old_parent = self._entries[entry.parent_inode]
        old_path = self.path_for(entry.inode)

        # Detach from old parent and old path index.
        old_parent.children.pop(entry.name, None)
        if old_path is not None:
            self._path_to_inode.pop(old_path, None)

        # Reparent.
        entry.name = new_name
        entry.parent_inode = new_parent.inode
        entry.ctime_ns = now_ns
        new_parent.children[new_name] = entry.inode

        # Reindex this entry and any descendants by their new paths.
        self._reindex_subtree(entry)

        # Tidy up synthetic ancestors of the old location if they're now empty.
        self._collapse_empty_synthetic_ancestors(old_parent)

    # ---- internals ----

    def _create_child(
        self,
        parent: Entry,
        name: str,
        kind: EntryKind,
        *,
        now_ns: int,
        source_inode: int | None = None,
        perm: int | None = None,
        is_synthetic: bool = False,
    ) -> Entry:
        inode = self._next_inode
        self._next_inode += 1
        if perm is None:
            perm = 0o755 if kind == EntryKind.DIRECTORY else 0o644
        entry = Entry(
            inode=inode,
            name=name,
            parent_inode=parent.inode,
            kind=kind,
            source_inode=source_inode,
            atime_ns=now_ns,
            mtime_ns=now_ns,
            ctime_ns=now_ns,
            perm=perm,
            is_synthetic=is_synthetic,
        )
        self._entries[inode] = entry
        parent.children[name] = inode
        # Record the mount-relative path.
        parent_path = self.path_for(parent.inode) or Path()
        child_path = Path(name) if parent_path == Path() else parent_path / name
        self._path_to_inode[child_path] = inode
        # Track derivative relationships for cascade removal.
        if kind == EntryKind.VIRTUAL_FILE and source_inode is not None:
            self._derivatives.setdefault(source_inode, set()).add(inode)
        return entry

    def _detach(self, entry: Entry) -> None:
        """Remove an entry from all indexes. Recurses into directory children."""
        if entry.inode == pyfuse3.ROOT_INODE:
            return

        # Recurse into children if a directory (shouldn't normally happen for
        # real files, but handles the rmdir/cleanup case).
        for child_inode in list(entry.children.values()):
            child_entry = self._entries.get(child_inode)
            if child_entry is not None:
                self._detach(child_entry)
        entry.children.clear()

        path = self.path_for(entry.inode)
        if path is not None:
            self._path_to_inode.pop(path, None)
        parent = self._entries.get(entry.parent_inode)
        if parent is not None:
            parent.children.pop(entry.name, None)
        # If this is a virtual file, drop it from its source's derivative set.
        if entry.kind == EntryKind.VIRTUAL_FILE and entry.source_inode is not None:
            d_set = self._derivatives.get(entry.source_inode)
            if d_set is not None:
                d_set.discard(entry.inode)
        self._entries.pop(entry.inode, None)
        # If the parent is now an empty synthetic dir, collapse it.
        if parent is not None:
            self._collapse_empty_synthetic_ancestors(parent)

    def _collapse_empty_synthetic_ancestors(self, dir_entry: Entry) -> None:
        """Remove `dir_entry` (and its synthetic ancestors) if empty + synthetic."""
        current = dir_entry
        while (
            current.kind == EntryKind.DIRECTORY
            and current.is_synthetic
            and not current.children
            and current.inode != pyfuse3.ROOT_INODE
        ):
            parent = self._entries.get(current.parent_inode)
            path = self.path_for(current.inode)
            if path is not None:
                self._path_to_inode.pop(path, None)
            if parent is not None:
                parent.children.pop(current.name, None)
            self._entries.pop(current.inode, None)
            if parent is None:
                break
            current = parent

    def _reindex_subtree(self, entry: Entry) -> None:
        """Rebuild the path index for `entry` and all descendants."""
        path = self.path_for(entry.inode)
        if path is not None:
            self._path_to_inode[path] = entry.inode
        for child_inode in entry.children.values():
            child = self._entries.get(child_inode)
            if child is not None:
                # Remove any stale path entry for this child (under its old
                # location) before recomputing.
                stale = [
                    p
                    for p, ino in self._path_to_inode.items()
                    if ino == child.inode
                ]
                for p in stale:
                    self._path_to_inode.pop(p, None)
                self._reindex_subtree(child)
