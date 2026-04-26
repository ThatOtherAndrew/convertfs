import errno
import logging
import os
import stat
import tempfile
from pathlib import Path
from time import time_ns

import pyfuse3
import trio
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
from convertfs.resolver import OutputEntry, resolve_outputs

_ATTR_TIMEOUT = 5.0
_ENTRY_TIMEOUT = 5.0


class FUSE(Operations):
    def __init__(self, ctime: int, converters: list[Converter]) -> None:
        super().__init__()
        self.ctime = ctime
        self.converters = converters
        self.logger = logging.getLogger('convertfs.fuse')
        self.inodes = InodeStore(ctime)

    # ---- attribute helpers ----

    def _make_attrs(self, entry: Entry) -> EntryAttributes:
        attrs = EntryAttributes()
        attrs.st_ino = entry.inode
        attrs.st_atime_ns = entry.atime_ns
        attrs.st_ctime_ns = entry.ctime_ns
        attrs.st_mtime_ns = entry.mtime_ns
        attrs.st_uid = os.getuid()
        attrs.st_gid = os.getgid()
        attrs.attr_timeout = _ATTR_TIMEOUT
        attrs.entry_timeout = _ENTRY_TIMEOUT

        if entry.kind == EntryKind.DIRECTORY:
            attrs.st_mode = stat.S_IFDIR | (entry.perm & 0o7777)
            attrs.st_size = 0
            attrs.st_nlink = 2
        elif entry.kind == EntryKind.REAL_FILE:
            attrs.st_mode = stat.S_IFREG | (entry.perm & 0o7777)
            attrs.st_size = len(entry.content)
            attrs.st_nlink = 1
        else:
            # Virtual files: real size is unknown until conversion runs. We
            # report a large placeholder so tools like `cat` will keep
            # reading until our `read` returns EOF (an empty bytes), rather
            # than truncating at 0.
            attrs.st_mode = stat.S_IFREG | (entry.perm & 0o7777)
            attrs.st_size = 1 << 32  # 4 GiB: arbitrary large sentinel
            attrs.st_nlink = 1
        return attrs

    # ---- conversion-trigger logic ----

    def _register_outputs_for(self, real_entry: Entry, *, now_ns: int) -> None:
        """Run the resolver against `real_entry` and register virtual outputs."""
        mount_path = self.inodes.path_for(real_entry.inode)
        if mount_path is None:
            return
        outputs = resolve_outputs(mount_path, self.converters)
        if not outputs:
            return

        # Outputs from the resolver are expressed relative to the source
        # file's containing directory (the templates only see the leaf
        # name). Anchor them there so `subdir/foo.txt` produces
        # `subdir/foo.txt.copy`, not `foo.txt.copy` at the mount root.
        anchor = (
            mount_path.parent if mount_path.parent != mount_path else Path()
        )

        self.logger.info(
            'registering %d virtual outputs for %s', len(outputs), mount_path,
        )
        for output in outputs:
            absolute = (
                output.path if anchor == Path() else anchor / output.path
            )
            self._register_one_output(output, absolute, real_entry, now_ns=now_ns)

    def _register_one_output(
        self,
        output: OutputEntry,
        absolute: Path,
        real_entry: Entry,
        *,
        now_ns: int,
    ) -> None:
        if output.is_dir:
            self.inodes.ensure_directory(absolute, now_ns=now_ns, is_synthetic=True)
            return

        parent_path = (
            absolute.parent if absolute.parent != absolute else Path()
        )
        # Skip outputs whose path equals an existing real file (e.g. a
        # PNG-to-PNG identity output for a real PNG); we don't want to shadow
        # the real file with a virtual entry.
        existing = self.inodes.by_path(absolute)
        if existing is not None and existing.kind == EntryKind.REAL_FILE:
            return

        parent_entry = self.inodes.ensure_directory(
            parent_path, now_ns=now_ns, is_synthetic=True,
        )
        self.inodes.add_file(
            parent_entry,
            absolute.name,
            EntryKind.VIRTUAL_FILE,
            now_ns=now_ns,
            source_inode=real_entry.inode,
        )
        self.logger.debug('  + %s', absolute)

    def _find_converter(self, source_name: str) -> Converter | None:
        for converter in self.converters:
            for pattern in converter.INPUTS:
                if pattern.match(source_name):
                    return converter
        return None

    async def _materialise_virtual(self, virtual: Entry) -> bytes:
        """Run the matching converter against the source's in-memory content."""
        if virtual.source_inode is None:
            raise pyfuse3.FUSEError(errno.EIO)
        source = self.inodes.get(virtual.source_inode)
        if source is None or source.kind != EntryKind.REAL_FILE:
            raise pyfuse3.FUSEError(errno.ENOENT)

        source_name = source.name
        converter = self._find_converter(source_name)
        if converter is None:
            raise pyfuse3.FUSEError(errno.EIO)

        virtual_path = self.inodes.path_for(virtual.inode)
        if virtual_path is None:
            raise pyfuse3.FUSEError(errno.ENOENT)

        # Write the source bytes to a temp file so the converter (which
        # works with on-disk paths) can read it. Pass `requested` as a
        # mount-relative-style path with the right suffix so the converter
        # can disambiguate output formats.
        def _run() -> bytes:
            with tempfile.NamedTemporaryFile(
                suffix=Path(source_name).suffix or '',
                delete=True,
            ) as src_tmp:
                src_tmp.write(bytes(source.content))
                src_tmp.flush()
                return converter.process(Path(src_tmp.name), virtual_path)

        try:
            return await trio.to_thread.run_sync(_run)
        except pyfuse3.FUSEError:
            raise
        except Exception:
            self.logger.exception(
                'converter %s failed on %s',
                type(converter).__name__,
                virtual_path,
            )
            raise pyfuse3.FUSEError(errno.EIO) from None

    # ---- FUSE ops: directory / lookup ----

    @override
    async def getattr(self, inode: InodeT, ctx: RequestContext) -> EntryAttributes:
        self.logger.debug('getattr: inode %d', inode)
        entry = self.inodes.get(inode)
        if entry is None:
            raise pyfuse3.FUSEError(errno.ENOENT)
        return self._make_attrs(entry)

    @override
    async def lookup(
        self, parent_inode: InodeT, name: FileNameT, ctx: RequestContext,
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
        self, fh: FileHandleT, start_id: int, token: ReaddirToken,
    ) -> None:
        self.logger.debug('readdir: handle %d (start_id %d)', fh, start_id)
        entry = self.inodes.get(int(fh))
        if entry is None or entry.kind != EntryKind.DIRECTORY:
            return

        children = list(entry.children.items())
        for index, (name, child_inode) in enumerate(children, start=1):
            if index <= start_id:
                continue
            child = self.inodes.get(child_inode)
            if child is None:
                continue
            attrs = self._make_attrs(child)
            if not pyfuse3.readdir_reply(token, os.fsencode(name), attrs, index):
                return

    @override
    async def releasedir(self, fh: FileHandleT) -> None:
        self.logger.debug('releasedir: handle %d', fh)

    # ---- FUSE ops: directory mutation ----

    @override
    async def mkdir(
        self,
        parent_inode: InodeT,
        name: FileNameT,
        mode: ModeT,
        ctx: RequestContext,
    ) -> EntryAttributes:
        name_str = os.fsdecode(name)
        self.logger.info('mkdir: parent %d / %s', parent_inode, name_str)

        parent = self.inodes.get(parent_inode)
        if parent is None or parent.kind != EntryKind.DIRECTORY:
            raise pyfuse3.FUSEError(errno.ENOENT)
        if self.inodes.child(parent, name_str) is not None:
            raise pyfuse3.FUSEError(errno.EEXIST)

        now = time_ns()
        new_dir = self.inodes.add_directory(
            parent,
            name_str,
            now_ns=now,
            perm=mode & 0o777,
            is_synthetic=False,
        )
        return self._make_attrs(new_dir)

    @override
    async def rmdir(
        self, parent_inode: InodeT, name: FileNameT, ctx: RequestContext,
    ) -> None:
        name_str = os.fsdecode(name)
        self.logger.info('rmdir: parent %d / %s', parent_inode, name_str)

        parent = self.inodes.get(parent_inode)
        if parent is None or parent.kind != EntryKind.DIRECTORY:
            raise pyfuse3.FUSEError(errno.ENOENT)
        target = self.inodes.child(parent, name_str)
        if target is None:
            raise pyfuse3.FUSEError(errno.ENOENT)
        if target.kind != EntryKind.DIRECTORY:
            raise pyfuse3.FUSEError(errno.ENOTDIR)
        if target.children:
            raise pyfuse3.FUSEError(errno.ENOTEMPTY)
        self.inodes.remove(target)

    @override
    async def unlink(
        self, parent_inode: InodeT, name: FileNameT, ctx: RequestContext,
    ) -> None:
        name_str = os.fsdecode(name)
        self.logger.info('unlink: parent %d / %s', parent_inode, name_str)

        parent = self.inodes.get(parent_inode)
        if parent is None or parent.kind != EntryKind.DIRECTORY:
            raise pyfuse3.FUSEError(errno.ENOENT)
        target = self.inodes.child(parent, name_str)
        if target is None:
            # Idempotent: if the target is already gone (e.g. cascaded away
            # when its source was unlinked earlier in a `rm -r`), don't
            # fail. The kernel may have a stale readdir cache.
            return
        if target.kind == EntryKind.DIRECTORY:
            raise pyfuse3.FUSEError(errno.EISDIR)
        # Real files cascade to their derivatives via InodeStore.remove.
        # Virtual files just disappear (a re-touch of the source would
        # reinstate them, but we don't currently auto-recreate).
        self.inodes.remove(target)

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
            parent_inode_old, old_name, parent_inode_new, new_name,
        )

        old_parent = self.inodes.get(parent_inode_old)
        new_parent = self.inodes.get(parent_inode_new)
        if old_parent is None or new_parent is None:
            raise pyfuse3.FUSEError(errno.ENOENT)
        if new_parent.kind != EntryKind.DIRECTORY:
            raise pyfuse3.FUSEError(errno.ENOTDIR)

        target = self.inodes.child(old_parent, old_name)
        if target is None:
            raise pyfuse3.FUSEError(errno.ENOENT)

        # Refuse to overwrite an existing entry at the destination.
        existing = self.inodes.child(new_parent, new_name)
        if existing is not None and existing.inode != target.inode:
            if existing.kind == EntryKind.DIRECTORY:
                raise pyfuse3.FUSEError(errno.EISDIR)
            # Replace the existing destination file.
            self.inodes.remove(existing)

        now = time_ns()
        self.inodes.move(target, new_parent, new_name, now_ns=now)

        # Re-run the resolver on real files in case the new name matches a
        # different set of converter inputs.
        if target.kind == EntryKind.REAL_FILE:
            self._register_outputs_for(target, now_ns=now)

    # ---- FUSE ops: file IO ----

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
            'create: parent %d / %s (mode %o)', parent_inode, name_str, mode,
        )

        parent = self.inodes.get(parent_inode)
        if parent is None or parent.kind != EntryKind.DIRECTORY:
            raise pyfuse3.FUSEError(errno.ENOENT)

        existing = self.inodes.child(parent, name_str)
        now = time_ns()
        if existing is not None:
            if existing.kind != EntryKind.REAL_FILE:
                # Refuse to overwrite a directory or virtual file via create.
                raise pyfuse3.FUSEError(errno.EEXIST)
            real_file = existing
        else:
            real_file = self.inodes.add_file(
                parent,
                name_str,
                EntryKind.REAL_FILE,
                now_ns=now,
                perm=mode & 0o777,
            )

        # If O_TRUNC was requested, clear the buffer.
        if flags & os.O_TRUNC:
            real_file.content = bytearray()
            real_file.mtime_ns = now

        # Discover/refresh virtual outputs for this file.
        self._register_outputs_for(real_file, now_ns=now)

        attrs = self._make_attrs(real_file)
        return FileInfo(fh=FileHandleT(real_file.inode)), attrs

    @override
    async def open(
        self, inode: InodeT, flags: FlagT, ctx: RequestContext,
    ) -> FileInfo:
        self.logger.debug('open: inode %d (flags %o)', inode, flags)
        entry = self.inodes.get(inode)
        if entry is None:
            raise pyfuse3.FUSEError(errno.ENOENT)
        if entry.kind == EntryKind.DIRECTORY:
            raise pyfuse3.FUSEError(errno.EISDIR)
        if entry.kind == EntryKind.VIRTUAL_FILE and (flags & 0x3) != os.O_RDONLY:
            # Virtual files are read-only.
            raise pyfuse3.FUSEError(errno.EROFS)
        if entry.kind == EntryKind.REAL_FILE and (flags & os.O_TRUNC):
            entry.content = bytearray()
            entry.mtime_ns = time_ns()
        return FileInfo(fh=FileHandleT(inode))

    @override
    async def read(self, fh: FileHandleT, off: int, size: int) -> bytes:
        self.logger.debug('read: handle %d (off %d, size %d)', fh, off, size)
        entry = self.inodes.get(int(fh))
        if entry is None:
            raise pyfuse3.FUSEError(errno.ENOENT)
        if entry.kind == EntryKind.REAL_FILE:
            return bytes(entry.content[off : off + size])
        if entry.kind == EntryKind.VIRTUAL_FILE:
            data = await self._materialise_virtual(entry)
            return data[off : off + size]
        raise pyfuse3.FUSEError(errno.EISDIR)

    @override
    async def write(self, fh: FileHandleT, off: int, buf: bytes) -> int:
        self.logger.debug('write: handle %d (off %d, len %d)', fh, off, len(buf))
        entry = self.inodes.get(int(fh))
        if entry is None:
            raise pyfuse3.FUSEError(errno.ENOENT)
        if entry.kind != EntryKind.REAL_FILE:
            raise pyfuse3.FUSEError(errno.EROFS)

        end = off + len(buf)
        if end > len(entry.content):
            entry.content.extend(b'\x00' * (end - len(entry.content)))
        entry.content[off:end] = buf
        entry.mtime_ns = time_ns()
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

        now = time_ns()
        if fields.update_size and entry.kind == EntryKind.REAL_FILE:
            new_size = attr.st_size
            current_size = len(entry.content)
            if new_size < current_size:
                del entry.content[new_size:]
            elif new_size > current_size:
                entry.content.extend(b'\x00' * (new_size - current_size))
            entry.mtime_ns = now
        if fields.update_mode:
            entry.perm = attr.st_mode & 0o7777
            entry.ctime_ns = now
        if fields.update_atime:
            entry.atime_ns = attr.st_atime_ns
        if fields.update_mtime:
            entry.mtime_ns = attr.st_mtime_ns
        return self._make_attrs(entry)

    @override
    async def forget(self, inode_list: list[tuple[InodeT, int]]) -> None:
        self.logger.debug('forget: %s', inode_list)
        # We don't currently evict entries; the kernel can ask again later.
