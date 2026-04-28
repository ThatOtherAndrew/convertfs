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

from fontTools.ttLib import TTFont
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

        font = TTFont(str(source))
        font.flavor = self._FLAVOR_BY_EXT[out_ext]
        buf = io.BytesIO()
        font.save(buf)
        return buf.getvalue()
