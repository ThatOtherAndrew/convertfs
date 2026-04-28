"""Font interconversion: ttf <-> otf <-> woff <-> woff2.

Conversion is just a re-flavor of the underlying SFNT container. ttf and
otf are identical container-wise (the ``sfntVersion`` differs, derived from
the table set, so we leave it alone). woff/woff2 add a compression wrapper
over the same tables.
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import ClassVar

from typing_extensions import override

from convertfs.converter import Converter


class FontsConverter(Converter):
    INPUTS = (re.compile(r'^(.*)\.(ttf|otf|woff|woff2)$'),)
    OUTPUT_FILES = (
        Path('{}.ttf'),
        Path('{}.otf'),
        Path('{}.woff'),
        Path('{}.woff2'),
    )

    _FLAVOR_BY_EXT: ClassVar[dict[str, str | None]] = {
        'ttf': None,
        'otf': None,
        'woff': 'woff',
        'woff2': 'woff2',
    }

    @override
    def process(self, source: Path, requested: Path) -> bytes:
        out_ext = requested.suffix.lstrip('.').lower()
        if out_ext not in self._FLAVOR_BY_EXT:
            msg = f'Unsupported font output: {out_ext}'
            raise ValueError(msg)

        # No-op: same flavor (or a ttf/otf swap, which is identical at the
        # SFNT container level — fontTools only updates sfntVersion based
        # on the table set, which doesn't change here). Skip the parse +
        # repack; the woff/woff2 cases still need fontTools to (re)apply
        # the compression wrapper.
        src_ext = source.suffix.lstrip('.').lower()
        if src_ext == out_ext or {src_ext, out_ext} == {'ttf', 'otf'}:
            return source.read_bytes()

        # Lazy: fontTools is only needed for woff/woff2 (re)wrapping; ttf/otf
        # swaps are handled by the no-op shortcut above. Defer the import so
        # users who never touch fonts don't pay for it on startup.
        from fontTools.ttLib import TTFont

        font = TTFont(str(source))
        font.flavor = self._FLAVOR_BY_EXT[out_ext]
        buf = io.BytesIO()
        font.save(buf)
        return buf.getvalue()
