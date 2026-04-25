from pathlib import Path

import pyfuse3
import trio
from pyfuse3 import EntryAttributes, FileInfo

from convertfs.converter import Converter


class ConvertFS(pyfuse3.Operations):
    def __init__(self, mount_dir: Path) -> None:
        self.mount_dir = mount_dir.resolve()
        self.converters = []

    async def create(
        self,
        parent_inode: pyfuse3.InodeT,
        name: pyfuse3.FileNameT,
        mode: pyfuse3.ModeT,
        flags: pyfuse3.FlagT,
        ctx: pyfuse3.RequestContext,
    ) -> tuple[FileInfo, EntryAttributes]:
        print('New file created:', name)
        return FileInfo(), EntryAttributes()

    def add_converter(self, converter: Converter) -> None:
        self.converters.append(converter)

    def run(self) -> None:
        print('Running')

        pyfuse3.init(self, self.mount_dir.as_posix(), set(pyfuse3.default_options))
        try:
            trio.run(pyfuse3.main)
        finally:
            pyfuse3.close(unmount=True)
