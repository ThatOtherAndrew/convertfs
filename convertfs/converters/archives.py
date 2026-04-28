"""Archive interconversion: zip / tar(.gz/.bz2/.xz) / 7z.

Members are decompressed fully into memory and recompressed; this is fine for
typical archive sizes (the FUSE layer already buffers full converter output).
Symlinks, hard links, and directory permissions are not preserved.
"""

from __future__ import annotations

import bz2
import gzip
import io
import lzma
import re
import tarfile
import zipfile
from pathlib import Path
from typing import ClassVar

from typing_extensions import override

from convertfs.converter import Converter

_FORMATS = ('tar.gz', 'tar.bz2', 'tar.xz', 'tar', 'tgz', 'tbz2', 'txz', 'zip', '7z')
_INPUT_PATTERN = re.compile(
    r'^(.*)\.(' + '|'.join(re.escape(fmt) for fmt in _FORMATS) + r')$'
)


class ArchivesConverter(Converter):
    INPUTS = (_INPUT_PATTERN,)
    OUTPUT_DIRS = (Path('formats'), Path('compression'))
    OUTPUT_FILES = (
        Path('formats/{}.zip'),
        Path('formats/{}.tar'),
        Path('formats/{}.tar.gz'),
        Path('formats/{}.tar.bz2'),
        Path('formats/{}.tar.xz'),
        Path('formats/{}.7z'),
        Path('compression/{}.fast.zip'),
        Path('compression/{}.balanced.zip'),
        Path('compression/{}.max.zip'),
        Path('compression/{}.fast.7z'),
        Path('compression/{}.balanced.7z'),
        Path('compression/{}.max.7z'),
    )

    _ZIP_LEVELS: ClassVar[dict[str, int]] = {'fast': 1, 'balanced': 6, 'max': 9}
    _SEVENZ_LEVELS: ClassVar[dict[str, int]] = {'fast': 1, 'balanced': 5, 'max': 9}

    @override
    def process(self, source: Path, requested: Path) -> bytes:
        top = requested.parts[0] if len(requested.parts) > 1 else ''
        name = requested.name

        if top == 'formats':
            fmt = _strip_known_suffix(name, _FORMATS)
            # No-op: input and output are the same archive format
            # (treating tar.gz/tgz, tar.bz2/tbz2, tar.xz/txz as equivalent).
            # Skip extracting + recompressing every member when the user
            # is just asking for the same archive under the formats/ dir.
            if _normalise_format(fmt) == _normalise_format(
                _strip_known_suffix(source.name, _FORMATS)
            ):
                return source.read_bytes()
            members = _read_archive(source)
            return _write_archive(members, fmt, level=None)

        members = _read_archive(source)

        if top == 'compression':
            level_match = re.search(r'\.(fast|balanced|max)\.(zip|7z)$', name)
            if level_match is None:
                msg = f'Unsupported compression output: {name}'
                raise ValueError(msg)
            level_name, fmt = level_match.group(1), level_match.group(2)
            if fmt == 'zip':
                return _write_archive(members, 'zip', level=self._ZIP_LEVELS[level_name])
            return _write_archive(members, '7z', level=self._SEVENZ_LEVELS[level_name])

        msg = f'Unsupported archives output path: {requested}'
        raise ValueError(msg)


def _strip_known_suffix(name: str, suffixes: tuple[str, ...]) -> str:
    for suffix in sorted(suffixes, key=len, reverse=True):
        if name.endswith('.' + suffix):
            return suffix
    msg = f'Unsupported archive format suffix in: {name}'
    raise ValueError(msg)


def _normalise_format(fmt: str) -> str:
    """Collapse tgz/tbz2/txz aliases to their canonical tar.<comp> form."""
    aliases = {'tgz': 'tar.gz', 'tbz2': 'tar.bz2', 'txz': 'tar.xz'}
    return aliases.get(fmt, fmt)


def _read_archive(source: Path) -> list[tuple[str, bytes]]:
    name = source.name.lower()
    if name.endswith(('.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tbz2', '.tar.xz', '.txz')):
        return _read_tar(source)
    if name.endswith('.zip'):
        return _read_zip(source)
    if name.endswith('.7z'):
        return _read_7z(source)
    msg = f'Unsupported archive input: {source.name}'
    raise ValueError(msg)


def _read_tar(source: Path) -> list[tuple[str, bytes]]:
    members: list[tuple[str, bytes]] = []
    with tarfile.open(str(source), 'r:*') as tar:
        for info in tar.getmembers():
            if not info.isfile():
                continue
            f = tar.extractfile(info)
            if f is None:
                continue
            members.append((info.name, f.read()))
    return members


def _read_zip(source: Path) -> list[tuple[str, bytes]]:
    members: list[tuple[str, bytes]] = []
    with zipfile.ZipFile(str(source), 'r') as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            members.append((info.filename, z.read(info)))
    return members


def _read_7z(source: Path) -> list[tuple[str, bytes]]:
    # Lazy: py7zr is only needed for .7z archives. Users who only round-trip
    # tar/zip don't pay the import cost; standard library zipfile/tarfile
    # cover those formats without any extra dependency loading.
    import py7zr
    from py7zr.io import BytesIOFactory

    members: list[tuple[str, bytes]] = []
    with py7zr.SevenZipFile(str(source), 'r') as z:
        factory = BytesIOFactory(limit=1024 * 1024 * 1024)
        z.extractall(factory=factory)
        for name, py7z_io in factory.products.items():
            data = py7z_io.read()
            members.append((name, data))
    return members


def _write_archive(
    members: list[tuple[str, bytes]],
    fmt: str,
    *,
    level: int | None,
) -> bytes:
    if fmt == 'zip':
        return _write_zip(members, level)
    if fmt == 'tar':
        return _write_tar(members, compression=None, level=level)
    if fmt in ('tar.gz', 'tgz'):
        return _write_tar(members, compression='gz', level=level)
    if fmt in ('tar.bz2', 'tbz2'):
        return _write_tar(members, compression='bz2', level=level)
    if fmt in ('tar.xz', 'txz'):
        return _write_tar(members, compression='xz', level=level)
    if fmt == '7z':
        return _write_7z(members, level)
    msg = f'Unsupported archive output format: {fmt}'
    raise ValueError(msg)


def _write_zip(members: list[tuple[str, bytes]], level: int | None) -> bytes:
    buf = io.BytesIO()
    if level == 0:
        compression = zipfile.ZIP_STORED
        compresslevel = None
    else:
        compression = zipfile.ZIP_DEFLATED
        compresslevel = level
    with zipfile.ZipFile(
        buf, 'w', compression=compression, compresslevel=compresslevel
    ) as z:
        for name, data in members:
            z.writestr(name, data)
    return buf.getvalue()


def _write_tar(
    members: list[tuple[str, bytes]],
    *,
    compression: str | None,
    level: int | None,
) -> bytes:
    buf = io.BytesIO()
    if compression is None:
        fileobj: io.IOBase = buf
        mode = 'w'
    elif compression == 'gz':
        fileobj = gzip.GzipFile(
            fileobj=buf, mode='wb', compresslevel=level if level is not None else 6
        )
        mode = 'w'
    elif compression == 'bz2':
        fileobj = bz2.BZ2File(
            buf, 'wb', compresslevel=level if level is not None else 9
        )
        mode = 'w'
    elif compression == 'xz':
        fileobj = lzma.LZMAFile(
            buf, 'wb', preset=level if level is not None else 6
        )
        mode = 'w'
    else:
        msg = f'Unsupported tar compression: {compression}'
        raise ValueError(msg)

    try:
        with tarfile.open(fileobj=fileobj, mode=mode) as tar:
            for name, data in members:
                info = tarfile.TarInfo(name=name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    finally:
        if fileobj is not buf:
            fileobj.close()

    return buf.getvalue()


def _write_7z(members: list[tuple[str, bytes]], level: int | None) -> bytes:
    # Lazy: same rationale as _read_7z — py7zr stays out of startup unless
    # the user actually asks for a .7z output.
    import py7zr

    buf = io.BytesIO()
    filters: list[dict[str, int]] | None = None
    if level is not None:
        filters = [{'id': py7zr.FILTER_LZMA2, 'preset': level}]
    with py7zr.SevenZipFile(buf, 'w', filters=filters) as z:
        for name, data in members:
            z.writestr(data, name)
    return buf.getvalue()
