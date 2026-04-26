import signal
from pathlib import Path
from time import time_ns

import pyfuse3
import trio

from convertfs.converter import Converter
from convertfs.fuse import FUSE


class ConvertFS(pyfuse3.Operations):
    def __init__(self, mount_dir: Path) -> None:
        self.mount_dir = mount_dir.resolve()
        self.converters = []

    def add_converter(self, converter: Converter) -> None:
        self.converters.append(converter)

    async def _serve(self) -> None:
        async with trio.open_nursery() as nursery:
            nursery.start_soon(self._watch_signals, nursery.cancel_scope)
            nursery.start_soon(pyfuse3.main)

    async def _watch_signals(self, cancel_scope: trio.CancelScope) -> None:
        with trio.open_signal_receiver(
            signal.SIGINT, signal.SIGTERM, signal.SIGHUP
        ) as signals:
            async for signum in signals:
                name = signal.Signals(signum).name
                print(f'\nconvertfs: received {name}, unmounting...')
                cancel_scope.cancel()
                return

    def run(self) -> None:
        fuse = FUSE(time_ns())
        options = set(pyfuse3.default_options)
        options.add('auto_unmount')

        pyfuse3.init(fuse, self.mount_dir.as_posix(), options)
        print(f'convertfs: mounted on {self.mount_dir}')

        try:
            trio.run(self._serve)
        finally:
            pyfuse3.close(unmount=True)
