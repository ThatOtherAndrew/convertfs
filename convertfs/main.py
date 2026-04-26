from pathlib import Path

import pyfuse3
import trio

from convertfs.converter import Converter
from convertfs.fuse import FUSE


class ConvertFS(pyfuse3.Operations):
    def __init__(self, mount_dir: Path) -> None:
        self.fuse = FUSE()
        self.mount_dir = mount_dir.resolve()
        self.converters = []

    def add_converter(self, converter: Converter) -> None:
        self.converters.append(converter)

    def run(self) -> None:
        print('Running')

        pyfuse3.init(self.fuse, self.mount_dir.as_posix(), set(pyfuse3.default_options))
        try:
            trio.run(pyfuse3.main)
        finally:
            pyfuse3.close(unmount=True)
