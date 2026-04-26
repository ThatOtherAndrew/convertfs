import errno
import logging
import os
import stat
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import pyfuse3
from pyfuse3 import (
    EntryAttributes,
    FileHandleT,
    FileInfo,
    FileNameT,
    FlagT,
    InodeT,
    ModeT,
    Operations,
    ReaddirToken,
    RequestContext,
    SetattrFields,
)
from typing_extensions import override

from convertfs.converter import Converter
from convertfs.inodes import Entry, EntryKind, InodeStore
from convertfs.resolver import resolve_outputs

_ATTR_TIMEOUT = 5.0
_ENTRY_TIMEOUT = 5.0


class FUSE(Operations):
    def __init__(self, ctime: int, converters: list[Converter]) -> None:
        super().__init__()
        self.ctime = ctime
        self.converters = converters
        self.logger = logging.getLogger('convertfs.fuse')
        self.inodes = InodeStore()

    # ---- attribute helpers ----

    def _make_attrs(self, entry: Entry) -> EntryAttributes:
        attrs = EntryAttributes()
        attrs.st_ino = cast('InodeT', entry.inode)
        attrs.st_atime_ns = self.ctime
        attrs.st_ctime_ns = self.ctime
        attrs.st_mtime_ns = self.ctime
        attrs.st_uid = os.getuid()
        attrs.st_gid = os.getgid()
        attrs.attr_timeout = _ATTR_TIMEOUT
        attrs.entry_timeout = _ENTRY_TIMEOUT

        if entry.kind == EntryKind.DIRECTORY:
            attrs.st_mode = stat.S_IFDIR | 0o755
            attrs.st_size = 0
            attrs.st_nlink = 2
        else:
            attrs.st_mode = stat.S_IFREG | 0o644
            attrs.st_size = 0
            attrs.st_nlink = 1
        return attrs

    # ---- conversion-trigger logic ----

    def _register_input(self, mount_path: Path) -> None:
        """Run the resolver against `mount_path` and register virtual outputs.

        `mount_path` is the mount-relative path of the file that was created
        or moved in.
        """
        outputs = resolve_outputs(mount_path, self.converters)
        if not outputs:
            return

        self.logger.info(
            'registering %d virtual outputs for %s', len(outputs), mount_path
        )
        for output in outputs:
            if output.is_dir:
                self.inodes.ensure_directory(output.path)
                continue

            parent_path = (
                output.path.parent if output.path.parent != output.path else Path()
            )
            parent_entry = self.inodes.ensure_directory(parent_path)
            self.inodes.add_file(
                parent_entry,
                output.path.name,
                EntryKind.VIRTUAL_FILE,
                source_path=output.source_path,
            )
            self.logger.debug('  + %s', output.path)

    # ---- FUSE ops ----

    @override
    async def getattr(self, inode: InodeT, ctx: RequestContext) -> EntryAttributes:
        self.logger.debug('getattr: inode %d', inode)
        entry = self.inodes.get(inode)
        if entry is None:
            raise pyfuse3.FUSEError(errno.ENOENT)
        return self._make_attrs(entry)

    @override
    async def lookup(
        self, parent_inode: InodeT, name: FileNameT, ctx: RequestContext
    ) -> EntryAttributes:
        name_str = os.fsdecode(name)
        self.logger.debug('lookup: parent %d / %s', parent_inode, name_str)

        parent = self.inodes.get(parent_inode)
        if parent is None or parent.kind != EntryKind.DIRECTORY:
            raise pyfuse3.FUSEError(errno.ENOENT)

        if name_str == '.':
            return self._make_attrs(parent)
        if name_str == '..':
            grandparent = self.inodes.get(parent.parent_inode)
            return self._make_attrs(grandparent or parent)

        child = self.inodes.child(parent, name_str)
        if child is None:
            raise pyfuse3.FUSEError(errno.ENOENT)
        return self._make_attrs(child)

    @override
    async def opendir(self, inode: InodeT, ctx: RequestContext) -> FileHandleT:
        self.logger.debug('opendir: inode %d', inode)
        entry = self.inodes.get(inode)
        if entry is None or entry.kind != EntryKind.DIRECTORY:
            raise pyfuse3.FUSEError(errno.ENOTDIR)
        return FileHandleT(inode)

    @override
    async def readdir(
        self, fh: FileHandleT, start_id: int, token: ReaddirToken
    ) -> None:
        self.logger.debug('readdir: handle %d (start_id %d)', fh, start_id)
        entry = self.inodes.get(int(fh))
        if entry is None or entry.kind != EntryKind.DIRECTORY:
            return

        # Iterate children in stable insertion order. start_id is the
        # next_id we returned previously; we use simple 1-based indices.
        children = list(entry.children.items())
        for index, (name, child_inode) in enumerate(children, start=1):
            if index <= start_id:
                continue
            child = self.inodes.get(child_inode)
            if child is None:
                continue
            attrs = self._make_attrs(child)
            if not pyfuse3.readdir_reply(token, os.fsencode(name), attrs, index):
                # Buffer full; the kernel will call us again with a larger
                # start_id.
                return

    @override
    async def releasedir(self, fh: FileHandleT) -> None:
        self.logger.debug('releasedir: handle %d', fh)

    @override
    async def create(
        self,
        parent_inode: InodeT,
        name: FileNameT,
        mode: ModeT,
        flags: FlagT,
        ctx: RequestContext,
    ) -> tuple[FileInfo, EntryAttributes]:
        name_str = os.fsdecode(name)
        self.logger.info(
            'create: parent %d / %s (mode %o)', parent_inode, name_str, mode
        )

        parent = self.inodes.get(parent_inode)
        if parent is None or parent.kind != EntryKind.DIRECTORY:
            raise pyfuse3.FUSEError(errno.ENOENT)

        # Register the real file as a child of its parent dir so subsequent
        # stat/lookup calls succeed. Reading/writing its content is not yet
        # implemented.
        real_file = self.inodes.add_file(parent, name_str, EntryKind.REAL_FILE)

        # Discover virtual outputs for this newly arrived file.
        mount_path = self.inodes.path_for(real_file.inode) or Path(name_str)
        self._register_input(mount_path)

        attrs = self._make_attrs(real_file)
        return FileInfo(fh=FileHandleT(real_file.inode)), attrs

    @override
    async def rename(
        self,
        parent_inode_old: InodeT,
        name_old: FileNameT,
        parent_inode_new: InodeT,
        name_new: FileNameT,
        flags: int,
        ctx: RequestContext,
    ) -> None:
        old_name = os.fsdecode(name_old)
        new_name = os.fsdecode(name_new)
        self.logger.info(
            'rename: %d/%s -> %d/%s',
            parent_inode_old,
            old_name,
            parent_inode_new,
            new_name,
        )

        old_parent = self.inodes.get(parent_inode_old)
        new_parent = self.inodes.get(parent_inode_new)
        if old_parent is None or new_parent is None:
            raise pyfuse3.FUSEError(errno.ENOENT)

        existing = self.inodes.child(old_parent, old_name)
        if existing is None:
            # Cross-filesystem moves arrive as create+write+unlink, not
            # rename, so this branch handles in-mount renames only. Treat
            # as if the file just appeared with the new name.
            real_file = self.inodes.add_file(new_parent, new_name, EntryKind.REAL_FILE)
            mount_path = self.inodes.path_for(real_file.inode) or Path(new_name)
            self._register_input(mount_path)
            return

        # In-mount rename: re-register the entry under the new name. This is
        # rough (we don't update the inode's `name` field cleanly) but is
        # sufficient for triggering the resolver.
        real_file = self.inodes.add_file(new_parent, new_name, EntryKind.REAL_FILE)
        mount_path = self.inodes.path_for(real_file.inode) or Path(new_name)
        self._register_input(mount_path)

    # ---- file IO stubs ----

    @override
    async def open(self, inode: InodeT, flags: FlagT, ctx: RequestContext) -> FileInfo:
        self.logger.debug('open: inode %d (flags %o)', inode, flags)
        entry = self.inodes.get(inode)
        if entry is None:
            raise pyfuse3.FUSEError(errno.ENOENT)
        return FileInfo(fh=FileHandleT(inode))

    @override
    async def read(self, fh: FileHandleT, off: int, size: int) -> bytes:
        self.logger.debug('read: handle %d (offset %d, size %d)', fh, off, size)
        # Content read is not implemented in this phase.
        return b''

    @override
    async def write(self, fh: FileHandleT, off: int, buf: bytes) -> int:
        self.logger.debug('write: handle %d (offset %d, length %d)', fh, off, len(buf))
        # Pretend the write succeeded so userspace tools (touch, editors)
        # don't fail. Real persistence is a later phase.
        return len(buf)

    @override
    async def flush(self, fh: FileHandleT) -> None:
        self.logger.debug('flush: handle %d', fh)

    @override
    async def fsync(self, fh: FileHandleT, datasync: bool) -> None:
        self.logger.debug('fsync: handle %d (datasync=%s)', fh, datasync)

    @override
    async def release(self, fh: FileHandleT) -> None:
        self.logger.debug('release: handle %d', fh)

    @override
    async def setattr(
        self,
        inode: InodeT,
        attr: EntryAttributes,
        fields: SetattrFields,
        fh: FileHandleT | None,
        ctx: RequestContext,
    ) -> EntryAttributes:
        self.logger.debug('setattr: inode %d', inode)
        entry = self.inodes.get(inode)
        if entry is None:
            raise pyfuse3.FUSEError(errno.ENOENT)
        # We don't actually update anything; just echo the current attrs so
        # callers (truncate, chmod, etc.) don't error.
        return self._make_attrs(entry)

    @override
    async def forget(self, inode_list: Sequence[tuple[InodeT, int]]) -> None:
        self.logger.debug('forget: %s', inode_list)
        # We don't currently evict entries; the kernel can ask again later.
