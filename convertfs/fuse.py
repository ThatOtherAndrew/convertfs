import errno
import os
import stat

import pyfuse3
from pyfuse3 import (
    EntryAttributes,
    FileInfo,
    FileNameT,
    FlagT,
    InodeT,
    ModeT,
    Operations,
    RequestContext,
)
from typing_extensions import override


class FUSE(Operations):
    def __init__(self, ctime: int) -> None:
        super().__init__()
        self.ctime = ctime

    @override
    async def getattr(self, inode: InodeT, ctx: RequestContext) -> EntryAttributes:
        entry = EntryAttributes()
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
    async def create(
        self,
        parent_inode: InodeT,
        name: FileNameT,
        mode: ModeT,
        flags: FlagT,
        ctx: RequestContext,
    ) -> tuple[FileInfo, EntryAttributes]:
        print('New file created:', name)
        return FileInfo(), EntryAttributes()
