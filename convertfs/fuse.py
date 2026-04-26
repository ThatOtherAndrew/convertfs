"""FUSE Operations implementation backed by a real on-disk directory.

The mountpoint is *also* the source directory. We hold a directory fd
(`underlying_fd`) opened before pyfuse3 mounted FUSE over the path, and use
*at-syscalls relative to it for all real-file I/O. This lets us read and
write the underlying inode even though the path is shadowed by FUSE for
everyone else.

The InodeStore mirrors the on-disk tree as Entries, plus virtual entries
synthesised by converters. Real entries point to a relative path under
`underlying_fd`; virtual entries trigger their converter on read.
"""

from __future__ import annotations

import errno
import logging
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from time import time_ns
from typing import TYPE_CHECKING

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

from convertfs.inodes import Entry, EntryKind, InodeStore
from convertfs.resolver import OutputEntry, resolve_outputs

if TYPE_CHECKING:
    from convertfs.converter import Converter

_ATTR_TIMEOUT = 1.0
_ENTRY_TIMEOUT = 1.0


@dataclass
class _OpenHandle:
    """Per-open-call state. One per `open`/`create` call."""

    inode: int
    # For real files: the fd we opened with openat(). None for virtuals.
    fd: int | None = None


class FUSE(Operations):
    def __init__(
        self,
        ctime: int,
        converters: list[Converter],
        underlying_fd: int,
    ) -> None:
        super().__init__()
        self.ctime = ctime
        self.converters = converters
        self.logger = logging.getLogger('convertfs.fuse')
        self.inodes = InodeStore(ctime)
        self.underlying_fd = underlying_fd

        # Open file handles, keyed by an integer we allocate.
        self._open_handles: dict[int, _OpenHandle] = {}
        self._next_handle: int = 1

        # Run the eager scan of the underlying directory before any FUSE
        # ops can arrive. (pyfuse3 hasn't started its loop yet at this
        # point — Operations.__init__ is called synchronously during
        # ConvertFS.run() before pyfuse3.init.)
        self._scan_initial_tree()

    # ---- helpers ----

    def _alloc_handle(self, handle: _OpenHandle) -> int:
        hid = self._next_handle
        self._next_handle += 1
        self._open_handles[hid] = handle
        return hid

    def _release_handle(self, hid: int) -> None:
        handle = self._open_handles.pop(hid, None)
        if handle is None:
            return
        if handle.fd is not None:
            try:
                os.close(handle.fd)
            except OSError as exc:
                self.logger.warning('error closing fd %d: %s', handle.fd, exc)

    def _invalidate_derivatives(self, source_inode: int) -> None:
        """Drop cached converter outputs for all virtuals derived from `source_inode`.

        Called after a real file's content changes (write/truncate) so the
        next read of any virtual triggers a fresh conversion.
        """
        for d_inode in self.inodes.derivatives_of(source_inode):
            d_entry = self.inodes.get(d_inode)
            if d_entry is None or d_entry.cached_bytes is None:
                continue
            d_entry.cached_bytes = None
            try:
                pyfuse3.invalidate_inode(d_inode, attr_only=True)
            except Exception:
                self.logger.debug(
                    'invalidate_inode failed for %d', d_inode, exc_info=True,
                )

    def _invalidate_kernel_entries_for_derivatives(
        self, source_inode: int,
    ) -> None:
        """Tell the kernel to forget directory entries for derivatives.

        Must be called *before* detaching them from the InodeStore (otherwise
        we lose the parent_inode and name needed to identify the entry).
        Without this, the kernel's lookup cache keeps the virtuals visible
        (e.g. `stat foo.txt.copy` returns success) even after the source has
        been removed.
        """
        for d_inode in self.inodes.derivatives_of(source_inode):
            d_entry = self.inodes.get(d_inode)
            if d_entry is None:
                continue
            try:
                pyfuse3.invalidate_entry_async(
                    d_entry.parent_inode,
                    os.fsencode(d_entry.name),
                    deleted=d_entry.inode,
                    ignore_enoent=True,
                )
            except Exception:
                self.logger.debug(
                    'invalidate_entry_async failed for %d/%s',
                    d_entry.parent_inode, d_entry.name, exc_info=True,
                )

    def _underlying_relpath(self, entry: Entry) -> str:
        """Return the path of `entry` relative to the underlying dir-fd.

        The empty string represents the root of the underlying directory.
        """
        path = self.inodes.path_for(entry.inode)
        if path is None or path == Path():
            return ''
        return path.as_posix()

    def _stat_underlying(self, entry: Entry) -> os.stat_result | None:
        """os.stat the underlying file/dir for a real entry.

        Returns None if the file no longer exists or is not accessible.
        """
        relpath = self._underlying_relpath(entry)
        try:
            if relpath == '':
                return os.fstat(self.underlying_fd)
            return os.stat(
                relpath, dir_fd=self.underlying_fd, follow_symlinks=False,
            )
        except OSError:
            return None

    # ---- attribute helpers ----

    def _make_attrs(self, entry: Entry) -> EntryAttributes:
        attrs = EntryAttributes()
        attrs.st_ino = entry.inode
        attrs.st_uid = os.getuid()
        attrs.st_gid = os.getgid()
        attrs.attr_timeout = _ATTR_TIMEOUT
        attrs.entry_timeout = _ENTRY_TIMEOUT
        attrs.st_nlink = 2 if entry.kind == EntryKind.DIRECTORY else 1

        # For real files/dirs, prefer the underlying filesystem's metadata.
        backed = (
            entry.kind in (EntryKind.REAL_FILE, EntryKind.DIRECTORY)
            and not entry.is_synthetic
        )
        backed_stat = self._stat_underlying(entry) if backed else None

        if backed_stat is not None:
            attrs.st_mode = backed_stat.st_mode
            attrs.st_size = backed_stat.st_size
            attrs.st_atime_ns = backed_stat.st_atime_ns
            attrs.st_mtime_ns = backed_stat.st_mtime_ns
            attrs.st_ctime_ns = backed_stat.st_ctime_ns
            return attrs

        # Purely virtual entity: use the entry's stored values.
        attrs.st_atime_ns = entry.atime_ns or self.ctime
        attrs.st_mtime_ns = entry.mtime_ns or self.ctime
        attrs.st_ctime_ns = entry.ctime_ns or self.ctime

        if entry.kind == EntryKind.DIRECTORY:
            attrs.st_mode = stat.S_IFDIR | (entry.perm & 0o7777)
            attrs.st_size = 0
        elif entry.kind == EntryKind.VIRTUAL_FILE:
            attrs.st_mode = stat.S_IFREG | (entry.perm & 0o7777)
            # Report 0 until the converter has actually run; then the real
            # size of the produced bytes. Tools that decide whether to
            # call read() based on st_size (e.g. some archivers) will see
            # 0 for unconverted files. Reads still trigger conversion via
            # our read handler, which is when the cache populates and a
            # subsequent stat returns the true size.
            attrs.st_size = (
                len(entry.cached_bytes) if entry.cached_bytes is not None else 0
            )
        else:
            # A real file but we couldn't stat it — fall back to zero.
            attrs.st_mode = stat.S_IFREG | (entry.perm & 0o7777)
            attrs.st_size = 0
        return attrs

    # ---- initial scan ----

    def _scan_initial_tree(self) -> None:
        """Walk the underlying directory and register all real entries.

        After this, real-file and real-directory entries exist in the
        InodeStore, and virtual outputs have been registered for each.
        """
        self.logger.info('scanning underlying directory...')
        count_dirs = 0
        count_files = 0

        # BFS so parents are created before children.
        # Each queue item is (parent_entry, parent_relpath).
        queue: list[tuple[Entry, str]] = [(self.inodes.root(), '')]
        now = time_ns()
        while queue:
            parent_entry, parent_relpath = queue.pop(0)
            try:
                if parent_relpath == '':
                    names = sorted(os.listdir(self.underlying_fd))
                else:
                    names = sorted(
                        _listdir_at(self.underlying_fd, parent_relpath),
                    )
            except OSError as exc:
                self.logger.warning(
                    'cannot list %r in underlying dir: %s', parent_relpath, exc,
                )
                continue

            for name in names:
                child_relpath = (
                    name if parent_relpath == '' else f'{parent_relpath}/{name}'
                )
                try:
                    child_stat = os.stat(
                        child_relpath,
                        dir_fd=self.underlying_fd,
                        follow_symlinks=False,
                    )
                except OSError as exc:
                    self.logger.warning(
                        'cannot stat %r: %s', child_relpath, exc,
                    )
                    continue

                if stat.S_ISLNK(child_stat.st_mode):
                    # Hide symlinks for v1.
                    continue

                if stat.S_ISDIR(child_stat.st_mode):
                    child_entry = self.inodes.add_directory(
                        parent_entry,
                        name,
                        now_ns=now,
                        perm=child_stat.st_mode & 0o7777,
                        is_synthetic=False,
                    )
                    count_dirs += 1
                    queue.append((child_entry, child_relpath))
                elif stat.S_ISREG(child_stat.st_mode):
                    real_entry = self.inodes.add_file(
                        parent_entry,
                        name,
                        EntryKind.REAL_FILE,
                        now_ns=now,
                        perm=child_stat.st_mode & 0o7777,
                    )
                    self._register_outputs_for(real_entry, now_ns=now)
                    count_files += 1
                # Other types (sockets, fifos, devs) ignored.

        self.logger.info(
            'scan complete: %d dirs, %d files', count_dirs, count_files,
        )

    # ---- conversion-trigger logic ----

    def _register_outputs_for(self, real_entry: Entry, *, now_ns: int) -> None:
        """Run the resolver against `real_entry` and register virtual outputs."""
        mount_path = self.inodes.path_for(real_entry.inode)
        if mount_path is None:
            return
        outputs = resolve_outputs(mount_path, self.converters)
        if not outputs:
            return

        anchor = (
            mount_path.parent if mount_path.parent != mount_path else Path()
        )

        self.logger.debug(
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
        # Skip outputs whose path equals an existing real file (real wins).
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

    def _find_converter(self, source_name: str) -> Converter | None:
        for converter in self.converters:
            for pattern in converter.INPUTS:
                if pattern.match(source_name):
                    return converter
        return None

    async def _materialise_virtual(self, virtual: Entry) -> bytes:
        """Run the matching converter against the source's on-disk path."""
        if virtual.source_inode is None:
            raise pyfuse3.FUSEError(errno.EIO)
        source = self.inodes.get(virtual.source_inode)
        if source is None or source.kind != EntryKind.REAL_FILE:
            raise pyfuse3.FUSEError(errno.ENOENT)

        converter = self._find_converter(source.name)
        if converter is None:
            raise pyfuse3.FUSEError(errno.EIO)

        virtual_path = self.inodes.path_for(virtual.inode)
        source_relpath = self._underlying_relpath(source)
        if virtual_path is None or source_relpath == '':
            raise pyfuse3.FUSEError(errno.ENOENT)

        # Build an absolute path to the source on the underlying FS by way
        # of /proc/self/fd, so the converter can read it directly. This
        # works even though the mountpoint is shadowed by FUSE: /proc/self/fd
        # bypasses the mount namespace's directory mapping for our process.
        proc_path = f'/proc/self/fd/{self.underlying_fd}/{source_relpath}'

        def _run() -> bytes:
            return converter.process(Path(proc_path), virtual_path)

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

        # Create the directory on the underlying filesystem first.
        parent_relpath = self._underlying_relpath(parent)
        new_relpath = (
            name_str if parent_relpath == '' else f'{parent_relpath}/{name_str}'
        )
        try:
            os.mkdir(new_relpath, mode=mode & 0o777, dir_fd=self.underlying_fd)
        except OSError as exc:
            raise pyfuse3.FUSEError(exc.errno or errno.EIO) from exc

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

        # Synthetic dirs don't exist on disk; just detach in-memory.
        if not target.is_synthetic:
            relpath = self._underlying_relpath(target)
            try:
                os.rmdir(relpath, dir_fd=self.underlying_fd)
            except OSError as exc:
                raise pyfuse3.FUSEError(exc.errno or errno.EIO) from exc
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
            # Idempotent: cascaded virtual already removed elsewhere.
            return
        if target.kind == EntryKind.DIRECTORY:
            raise pyfuse3.FUSEError(errno.EISDIR)

        if target.kind == EntryKind.REAL_FILE:
            relpath = self._underlying_relpath(target)
            try:
                os.unlink(relpath, dir_fd=self.underlying_fd)
            except OSError as exc:
                raise pyfuse3.FUSEError(exc.errno or errno.EIO) from exc
            # Snapshot derivative entries' (parent, name) pairs so the kernel
            # can be told they're gone — otherwise its lookup cache will keep
            # serving them as if they still existed.
            self._invalidate_kernel_entries_for_derivatives(target.inode)

        # Detach in-memory; cascades virtuals if real.
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
        if flags != 0:
            # RENAME_NOREPLACE / RENAME_EXCHANGE: not yet supported.
            raise pyfuse3.FUSEError(errno.EINVAL)

        old_parent = self.inodes.get(parent_inode_old)
        new_parent = self.inodes.get(parent_inode_new)
        if old_parent is None or new_parent is None:
            raise pyfuse3.FUSEError(errno.ENOENT)
        if new_parent.kind != EntryKind.DIRECTORY:
            raise pyfuse3.FUSEError(errno.ENOTDIR)

        target = self.inodes.child(old_parent, old_name)
        if target is None:
            raise pyfuse3.FUSEError(errno.ENOENT)
        if target.kind == EntryKind.VIRTUAL_FILE:
            # Renaming a virtual file makes no sense (it's regenerated from
            # a real source on read).
            raise pyfuse3.FUSEError(errno.EROFS)

        existing = self.inodes.child(new_parent, new_name)
        if existing is not None and existing.inode != target.inode:
            if existing.kind == EntryKind.DIRECTORY:
                raise pyfuse3.FUSEError(errno.EISDIR)
            # Will be replaced; let the underlying os.rename do the dirty
            # work, then drop our in-memory record.
            if existing.kind == EntryKind.REAL_FILE:
                # We'll let os.rename overwrite it on disk too.
                pass
            self.inodes.remove(existing)

        # Do the on-disk rename for real entries (synthetic dirs don't
        # exist on disk).
        if not (target.kind == EntryKind.DIRECTORY and target.is_synthetic):
            old_parent_relpath = self._underlying_relpath(old_parent)
            new_parent_relpath = self._underlying_relpath(new_parent)
            old_relpath = (
                old_name if old_parent_relpath == ''
                else f'{old_parent_relpath}/{old_name}'
            )
            new_relpath = (
                new_name if new_parent_relpath == ''
                else f'{new_parent_relpath}/{new_name}'
            )
            try:
                os.rename(
                    old_relpath, new_relpath,
                    src_dir_fd=self.underlying_fd,
                    dst_dir_fd=self.underlying_fd,
                )
            except OSError as exc:
                raise pyfuse3.FUSEError(exc.errno or errno.EIO) from exc

        # Before move() drops the old-name derivatives, tell the kernel
        # to forget them. Otherwise its lookup cache keeps them visible
        # under the old name.
        if target.kind == EntryKind.REAL_FILE:
            self._invalidate_kernel_entries_for_derivatives(target.inode)

        now = time_ns()
        self.inodes.move(target, new_parent, new_name, now_ns=now)
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
        if existing is not None and existing.kind != EntryKind.REAL_FILE:
            raise pyfuse3.FUSEError(errno.EEXIST)

        # Open/create on the underlying filesystem.
        parent_relpath = self._underlying_relpath(parent)
        relpath = (
            name_str if parent_relpath == ''
            else f'{parent_relpath}/{name_str}'
        )
        # Ensure O_CREAT is set for create; honour O_TRUNC.
        open_flags = (flags | os.O_CREAT) & ~os.O_EXCL
        try:
            fd = os.open(
                relpath,
                open_flags,
                mode & 0o777,
                dir_fd=self.underlying_fd,
            )
        except OSError as exc:
            raise pyfuse3.FUSEError(exc.errno or errno.EIO) from exc

        now = time_ns()
        if existing is None:
            real_entry = self.inodes.add_file(
                parent,
                name_str,
                EntryKind.REAL_FILE,
                now_ns=now,
                perm=mode & 0o777,
            )
        else:
            real_entry = existing
            # Re-creating an existing file (with O_TRUNC, typically): any
            # cached converter outputs from the prior content are stale.
            self._invalidate_derivatives(real_entry.inode)

        self._register_outputs_for(real_entry, now_ns=now)

        handle = _OpenHandle(inode=real_entry.inode, fd=fd)
        hid = self._alloc_handle(handle)
        attrs = self._make_attrs(real_entry)
        return FileInfo(fh=FileHandleT(hid)), attrs

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

        if entry.kind == EntryKind.VIRTUAL_FILE:
            if (flags & 0x3) != os.O_RDONLY:
                raise pyfuse3.FUSEError(errno.EROFS)
            handle = _OpenHandle(inode=entry.inode, fd=None)
            hid = self._alloc_handle(handle)
            # direct_io tells the kernel to bypass its page cache and
            # forward every read() syscall to us, regardless of the
            # st_size we report. Without this, the kernel sees st_size=0
            # for an unconverted virtual file and short-circuits read()
            # to return EOF, never giving us a chance to materialise.
            return FileInfo(fh=FileHandleT(hid), direct_io=True)

        # Real file: open on the underlying filesystem via the dir-fd.
        relpath = self._underlying_relpath(entry)
        try:
            fd = os.open(relpath, flags, dir_fd=self.underlying_fd)
        except OSError as exc:
            raise pyfuse3.FUSEError(exc.errno or errno.EIO) from exc
        handle = _OpenHandle(inode=entry.inode, fd=fd)
        hid = self._alloc_handle(handle)
        return FileInfo(fh=FileHandleT(hid))

    @override
    async def read(self, fh: FileHandleT, off: int, size: int) -> bytes:
        self.logger.debug('read: handle %d (off %d, size %d)', fh, off, size)
        handle = self._open_handles.get(int(fh))
        if handle is None:
            raise pyfuse3.FUSEError(errno.EBADF)

        if handle.fd is not None:
            # Real file.
            try:
                return os.pread(handle.fd, size, off)
            except OSError as exc:
                raise pyfuse3.FUSEError(exc.errno or errno.EIO) from exc

        # Virtual file.
        entry = self.inodes.get(handle.inode)
        if entry is None:
            raise pyfuse3.FUSEError(errno.ENOENT)
        if entry.cached_bytes is None:
            entry.cached_bytes = await self._materialise_virtual(entry)
            # Tell the kernel to drop its cached attributes for this inode
            # so the next stat() picks up the now-known size.
            try:
                pyfuse3.invalidate_inode(entry.inode, attr_only=True)
            except Exception:
                self.logger.debug(
                    'invalidate_inode failed for %d', entry.inode, exc_info=True,
                )
        return entry.cached_bytes[off : off + size]

    @override
    async def write(self, fh: FileHandleT, off: int, buf: bytes) -> int:
        self.logger.debug('write: handle %d (off %d, len %d)', fh, off, len(buf))
        handle = self._open_handles.get(int(fh))
        if handle is None or handle.fd is None:
            raise pyfuse3.FUSEError(errno.EBADF)
        try:
            written = os.pwrite(handle.fd, buf, off)
        except OSError as exc:
            raise pyfuse3.FUSEError(exc.errno or errno.EIO) from exc
        # The source content changed; any cached converter outputs are stale.
        self._invalidate_derivatives(handle.inode)
        return written

    @override
    async def flush(self, fh: FileHandleT) -> None:
        self.logger.debug('flush: handle %d', fh)
        # No-op: we already write through to the underlying FS on each write.

    @override
    async def fsync(self, fh: FileHandleT, datasync: bool) -> None:
        self.logger.debug('fsync: handle %d (datasync=%s)', fh, datasync)
        handle = self._open_handles.get(int(fh))
        if handle is None or handle.fd is None:
            return
        try:
            if datasync:
                os.fdatasync(handle.fd)
            else:
                os.fsync(handle.fd)
        except OSError as exc:
            raise pyfuse3.FUSEError(exc.errno or errno.EIO) from exc

    @override
    async def release(self, fh: FileHandleT) -> None:
        self.logger.debug('release: handle %d', fh)
        self._release_handle(int(fh))

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

        # Determine where to apply changes: prefer fh's fd if open, else
        # use the relpath via the dir-fd.
        fd: int | None = None
        if fh is not None:
            handle = self._open_handles.get(int(fh))
            if handle is not None:
                fd = handle.fd

        is_real = (
            entry.kind == EntryKind.REAL_FILE
            or (entry.kind == EntryKind.DIRECTORY and not entry.is_synthetic)
        )
        relpath = self._underlying_relpath(entry) if is_real else None

        try:
            if fields.update_size and entry.kind == EntryKind.REAL_FILE:
                if fd is not None:
                    os.ftruncate(fd, attr.st_size)
                elif relpath is not None:
                    # Open just to truncate; close immediately.
                    tfd = os.open(
                        relpath, os.O_WRONLY, dir_fd=self.underlying_fd,
                    )
                    try:
                        os.ftruncate(tfd, attr.st_size)
                    finally:
                        os.close(tfd)
                # The source content changed; cached outputs are stale.
                self._invalidate_derivatives(entry.inode)
            if fields.update_mode:
                if is_real and relpath is not None:
                    if fd is not None:
                        os.fchmod(fd, attr.st_mode & 0o7777)
                    else:
                        os.chmod(
                            relpath,
                            attr.st_mode & 0o7777,
                            dir_fd=self.underlying_fd,
                            follow_symlinks=False,
                        )
                entry.perm = attr.st_mode & 0o7777
            if fields.update_atime or fields.update_mtime:
                # We can only set both atomically; fill in the missing one
                # from current state.
                cur = self._stat_underlying(entry) if is_real else None
                atime_ns = (
                    attr.st_atime_ns if fields.update_atime
                    else (cur.st_atime_ns if cur else entry.atime_ns)
                )
                mtime_ns = (
                    attr.st_mtime_ns if fields.update_mtime
                    else (cur.st_mtime_ns if cur else entry.mtime_ns)
                )
                if is_real and relpath is not None:
                    if fd is not None:
                        os.utime(fd, ns=(atime_ns, mtime_ns))
                    else:
                        os.utime(
                            relpath,
                            ns=(atime_ns, mtime_ns),
                            dir_fd=self.underlying_fd,
                            follow_symlinks=False,
                        )
                entry.atime_ns = atime_ns
                entry.mtime_ns = mtime_ns
        except OSError as exc:
            raise pyfuse3.FUSEError(exc.errno or errno.EIO) from exc

        return self._make_attrs(entry)

    @override
    async def forget(self, inode_list: list[tuple[InodeT, int]]) -> None:
        self.logger.debug('forget: %s', inode_list)
        # We don't currently evict entries; the kernel can ask again later.


def _listdir_at(dir_fd: int, relpath: str) -> list[str]:
    """List entries in a subdirectory of `dir_fd`."""
    sub_fd = os.open(relpath, os.O_RDONLY | os.O_DIRECTORY, dir_fd=dir_fd)
    try:
        return os.listdir(sub_fd)
    finally:
        os.close(sub_fd)
