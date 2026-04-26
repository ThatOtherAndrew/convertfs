import re
from pathlib import Path
from typing import ClassVar

import pyvips
from typing_extensions import override

from convertfs.converter import Converter


class ImagesConverter(Converter):
    INPUTS = (re.compile(r'^(.*)\.(png|jpg|jpeg|webp|tif|tiff|bmp|gif|heic|avif)$'),)
    OUTPUT_FILES = (
        Path('{}.png'),
        Path('{}.jpg'),
        Path('{}.jpeg'),
        Path('{}.webp'),
        Path('{}.tif'),
        Path('{}.tiff'),
        Path('{}.bmp'),
        Path('{}.gif'),
        Path('{}.heic'),
        Path('{}.avif'),
    )

    _FORMAT_ALIASES: ClassVar[dict[str, str]] = {
        'jpg': 'jpeg',
        'tif': 'tiff',
    }

    @override
    def process(self, source: Path, requested: Path) -> bytes:
        requested_ext = requested.suffix.lstrip('.').lower()
        if not requested_ext:
            msg = f'Requested output file has no extension: {requested.name}'
            raise ValueError(msg)

        output_format = self._FORMAT_ALIASES.get(requested_ext, requested_ext)
        if output_format not in {
            'png',
            'jpeg',
            'webp',
            'tiff',
            'bmp',
            'gif',
            'heic',
            'avif',
        }:
            msg = f'Unsupported output format: {requested_ext}'
            raise ValueError(msg)

        image = pyvips.Image.new_from_file(str(source), access='sequential')
        if output_format == 'jpeg':
            return image.write_to_buffer('.jpg[Q=90]')

        return image.write_to_buffer(f'.{output_format}')
