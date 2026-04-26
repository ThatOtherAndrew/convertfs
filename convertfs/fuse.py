import errno
import logging
import os
import stat

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
)
from typing_extensions import override


class FUSE(Operations):
    def __init__(self, ctime: int) -> None:
        super().__init__()
        self.ctime = ctime
        self.logger = logging.getLogger('fuse')

    @override
    async def getattr(self, inode: InodeT, ctx: RequestContext) -> EntryAttributes:
        self.logger.debug('getattr: inode %d', inode)

        entry = EntryAttributes()
        entry.st_ino = inode
        entry.st_atime_ns = self.ctime
        entry.st_ctime_ns = self.ctime
        entry.st_mtime_ns = self.ctime
        entry.st_uid = os.getuid()
        entry.st_gid = os.getgid()

        if inode == pyfuse3.ROOT_INODE:
            entry.st_mode = stat.S_IFDIR | 0o755
            entry.st_size = 0
        else:
            raise pyfuse3.FUSEError(errno.ENOENT)

        return entry

    @override
    async def lookup(
        self, parent_inode: InodeT, name: FileNameT, ctx: RequestContext
    ) -> EntryAttributes:
        self.logger.debug('lookup: %s', name)

        # TODO: actual lookup
        await super().lookup(parent_inode, name, ctx)

    @override
    async def opendir(self, inode: InodeT, ctx: RequestContext) -> FileHandleT:
        self.logger.debug('opendir: inode %d', inode)

        return FileHandleT(inode)

    @override
    async def readdir(
        self, fh: FileHandleT, start_id: int, token: ReaddirToken
    ) -> None:
        self.logger.debug('readdir: handle %d (id %d)', fh, start_id)

        # TODO: readdir_reply

    @override
    async def create(
        self,
        parent_inode: InodeT,
        name: FileNameT,
        mode: ModeT,
        flags: FlagT,
        ctx: RequestContext,
    ) -> tuple[FileInfo, EntryAttributes]:
        self.logger.debug('create: %s (%o)', name, mode)

        return FileInfo(), EntryAttributes()
        # TODO: actually implement this

    @override
    async def open(self, inode: InodeT, flags: FlagT, ctx: RequestContext) -> FileInfo:
        self.logger.debug('open: inode %d', inode)

        # TODO: actual file open

    @override
    async def read(self, fh: FileHandleT, off: int, size: int) -> bytes:
        self.logger.debug('read: handle %d (offset %d, size %d)', fh, off, size)

        # TODO: return actual bytes
        return b'hello'

    @override
    async def write(self, fh: FileHandleT, off: int, buf: bytes) -> int:
        self.logger.debug('write: handle %d (offset %d, length %d)', fh, off, len(buf))

        # TODO: actual write
