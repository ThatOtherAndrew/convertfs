import contextlib
import logging
import os
import signal
from pathlib import Path
from time import time_ns

import pyfuse3
import trio

from convertfs.converter import Converter
from convertfs.fuse import FUSE


class ConvertFS:
    """Coordinates the FUSE mount, the underlying-dir fd, and signal handling.

    The mountpoint is *also* the source directory: we open a directory file
    descriptor (with O_PATH | O_DIRECTORY) before pyfuse3.init mounts FUSE
    on top, so we retain access to the underlying directory tree even while
    the mountpoint is shadowed by FUSE for everyone else. Real-file ops use
    *at-syscalls relative to that fd.
    """

    def __init__(self, mount_dir: Path) -> None:
        self.mount_dir = mount_dir.resolve()
        self.converters: list[Converter] = []
        self.logger = logging.getLogger('convertfs')

    def add_converter(self, converter: Converter) -> None:
        self.converters.append(converter)

    async def _serve(self, fuse: FUSE) -> None:
        async with trio.open_nursery() as nursery:
            # Hand the FUSE instance the nursery so it can spawn its own
            # background tasks (e.g. debounced source-consumption).
            fuse.nursery = nursery
            nursery.start_soon(self._watch_signals, nursery.cancel_scope)
            nursery.start_soon(pyfuse3.main)

    async def _watch_signals(self, cancel_scope: trio.CancelScope) -> None:
        with trio.open_signal_receiver(
            signal.SIGINT, signal.SIGTERM, signal.SIGHUP,
        ) as signals:
            async for signum in signals:
                name = signal.Signals(signum).name
                self.logger.warning('received %s, unmounting...', name)
                cancel_scope.cancel()
                return

    def run(self) -> None:
        # Auto-create the mountpoint if it doesn't exist; it'll also serve as
        # the underlying source directory.
        if not self.mount_dir.exists():
            self.logger.info('creating mountpoint %s', self.mount_dir)
            self.mount_dir.mkdir(parents=True, exist_ok=True)
        elif not self.mount_dir.is_dir():
            msg = f'{self.mount_dir} exists and is not a directory'
            raise SystemExit(msg)

        # Open a dir-fd to the underlying directory *before* mounting FUSE on
        # top. This fd will keep referring to the underlying inode even when
        # the path is shadowed by FUSE.
        underlying_fd = os.open(
            self.mount_dir.as_posix(),
            os.O_RDONLY | os.O_DIRECTORY,
        )
        self.logger.debug(
            'opened underlying dir-fd %d on %s', underlying_fd, self.mount_dir,
        )

        try:
            fuse = FUSE(
                ctime=time_ns(),
                converters=self.converters,
                underlying_fd=underlying_fd,
            )
            options = set(pyfuse3.default_options)
            options.add('auto_unmount')

            pyfuse3.init(fuse, self.mount_dir.as_posix(), options)
            self.logger.info('mounted on %s', self.mount_dir)

            try:
                trio.run(self._serve, fuse)
            finally:
                pyfuse3.close(unmount=True)
                fuse.shutdown()
        finally:
            with contextlib.suppress(OSError):
                os.close(underlying_fd)
