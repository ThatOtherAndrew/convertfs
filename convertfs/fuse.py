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
    @override
    async def getattr(self, inode: InodeT, ctx: RequestContext) -> EntryAttributes:
        print('Stat root')
        entry = EntryAttributes()
        if inode == pyfuse3.ROOT_INODE:
            entry.st_mode = stat.S_IFDIR | 0o755
            entry.st_size = 0
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
