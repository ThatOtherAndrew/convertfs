import re
from pathlib import Path
from typing import ClassVar

import pyvips
from typing_extensions import override

from convertfs.converter import Converter


class ImagesConverter(Converter):
    INPUTS = (re.compile(r'^(.*)\.(png|jpg|jpeg|webp|tif|tiff|bmp|gif|heic|avif)$'),)
    OUTPUT_DIRS = (
        Path('quality'),
        Path('resolutions'),
        Path('presets'),
    )
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
        # Quality dials (JPEG / WebP / AVIF — codecs with a Q parameter).
        Path('quality/{}.very-low.jpg'),
        Path('quality/{}.low.jpg'),
        Path('quality/{}.medium.jpg'),
        Path('quality/{}.high.jpg'),
        Path('quality/{}.very-high.jpg'),
        Path('quality/{}.very-low.webp'),
        Path('quality/{}.low.webp'),
        Path('quality/{}.medium.webp'),
        Path('quality/{}.high.webp'),
        Path('quality/{}.very-high.webp'),
        Path('quality/{}.very-low.avif'),
        Path('quality/{}.low.avif'),
        Path('quality/{}.medium.avif'),
        Path('quality/{}.high.avif'),
        Path('quality/{}.very-high.avif'),
        # Fit-inside resizes (longest side capped).
        Path('resolutions/{}.4k.jpg'),
        Path('resolutions/{}.2k.jpg'),
        Path('resolutions/{}.1080p.jpg'),
        Path('resolutions/{}.720p.jpg'),
        Path('resolutions/{}.480p.jpg'),
        Path('resolutions/{}.thumbnail.jpg'),
        Path('resolutions/{}.4k.webp'),
        Path('resolutions/{}.2k.webp'),
        Path('resolutions/{}.1080p.webp'),
        Path('resolutions/{}.720p.webp'),
        Path('resolutions/{}.480p.webp'),
        Path('resolutions/{}.thumbnail.webp'),
        Path('resolutions/{}.4k.png'),
        Path('resolutions/{}.2k.png'),
        Path('resolutions/{}.1080p.png'),
        Path('resolutions/{}.720p.png'),
        Path('resolutions/{}.480p.png'),
        Path('resolutions/{}.thumbnail.png'),
        # Named output presets.
        Path('presets/{}.web.jpg'),
        Path('presets/{}.email.jpg'),
        Path('presets/{}.print.jpg'),
        Path('presets/{}.thumbnail.png'),
        Path('presets/{}.social-square.jpg'),
        Path('presets/{}.social-story.jpg'),
    )

    _FORMAT_ALIASES: ClassVar[dict[str, str]] = {
        'jpg': 'jpeg',
        'tif': 'tiff',
    }
    _FLAT_FORMATS: ClassVar[frozenset[str]] = frozenset(
        {'png', 'jpeg', 'webp', 'tiff', 'bmp', 'gif', 'heic', 'avif'}
    )

    _QUALITY_LEVELS: ClassVar[dict[str, int]] = {
        'very-low': 40,
        'low': 60,
        'medium': 75,
        'high': 88,
        'very-high': 95,
    }

    # Long-side cap in pixels.
    _RESOLUTIONS: ClassVar[dict[str, int]] = {
        '4k': 4096,
        '2k': 2048,
        '1080p': 1920,
        '720p': 1280,
        '480p': 854,
        'thumbnail': 256,
    }

    # Each preset describes how to render. ``box`` is (width, height) for a
    # cover-crop; ``long_side`` is a fit-inside cap. ``q`` is the JPEG quality
    # if the output is jpeg.
    _PRESETS: ClassVar[dict[str, dict[str, object]]] = {
        'web': {'long_side': 1920, 'q': 82, 'ext': 'jpg', 'strip': True},
        'email': {'long_side': 1280, 'q': 78, 'ext': 'jpg', 'strip': True},
        'print': {'long_side': None, 'q': 95, 'ext': 'jpg', 'strip': False},
        'thumbnail': {'long_side': 256, 'ext': 'png', 'strip': True},
        'social-square': {
            'box': (1080, 1080),
            'q': 85,
            'ext': 'jpg',
            'strip': True,
        },
        'social-story': {
            'box': (1080, 1920),
            'q': 85,
            'ext': 'jpg',
            'strip': True,
        },
    }

    @override
    def process(self, source: Path, requested: Path) -> bytes:
        top = requested.parts[0] if len(requested.parts) > 1 else ''

        if top == 'quality':
            return self._process_quality(source, requested)
        if top == 'resolutions':
            return self._process_resolution(source, requested)
        if top == 'presets':
            return self._process_preset(source, requested)
        return self._process_flat(source, requested)

    def _process_flat(self, source: Path, requested: Path) -> bytes:
        requested_ext = requested.suffix.lstrip('.').lower()
        if not requested_ext:
            msg = f'Requested output file has no extension: {requested.name}'
            raise ValueError(msg)

        output_format = self._FORMAT_ALIASES.get(requested_ext, requested_ext)
        if output_format not in self._FLAT_FORMATS:
            msg = f'Unsupported output format: {requested_ext}'
            raise ValueError(msg)

        # No-op: source and requested format are the same. Skip re-encoding
        # so JPEG/WebP/AVIF round-trips don't lose quality and PNG/TIFF
        # round-trips don't pay the encode cost.
        source_ext = source.suffix.lstrip('.').lower()
        source_format = self._FORMAT_ALIASES.get(source_ext, source_ext)
        if source_format == output_format:
            return source.read_bytes()

        image = pyvips.Image.new_from_file(str(source), access='sequential')
        if output_format == 'jpeg':
            return image.write_to_buffer('.jpg[Q=90]')
        return image.write_to_buffer(f'.{output_format}')

    def _process_quality(self, source: Path, requested: Path) -> bytes:
        match = re.search(
            r'\.(very-low|low|medium|high|very-high)\.(jpg|jpeg|webp|avif)$',
            requested.name,
        )
        if match is None:
            msg = f'Unsupported quality output: {requested.name}'
            raise ValueError(msg)
        level_name, ext = match.group(1), match.group(2).replace('jpeg', 'jpg')
        q = self._QUALITY_LEVELS[level_name]
        image = pyvips.Image.new_from_file(str(source), access='sequential')
        return image.write_to_buffer(f'.{ext}[Q={q}]')

    def _process_resolution(self, source: Path, requested: Path) -> bytes:
        match = re.search(
            r'\.(4k|2k|1080p|720p|480p|thumbnail)\.(jpg|jpeg|webp|png)$',
            requested.name,
        )
        if match is None:
            msg = f'Unsupported resolution output: {requested.name}'
            raise ValueError(msg)
        size_name, ext = match.group(1), match.group(2).replace('jpeg', 'jpg')
        long_side = self._RESOLUTIONS[size_name]
        image = pyvips.Image.thumbnail(
            str(source), long_side, height=long_side, size='down'
        )
        if ext == 'jpg':
            return image.write_to_buffer('.jpg[Q=85,strip]')
        if ext == 'webp':
            return image.write_to_buffer('.webp[Q=85,strip]')
        return image.write_to_buffer('.png[strip]')

    def _process_preset(self, source: Path, requested: Path) -> bytes:
        match = re.search(
            r'\.(web|email|print|thumbnail|social-square|social-story)\.(jpg|jpeg|png)$',
            requested.name,
        )
        if match is None:
            msg = f'Unsupported preset output: {requested.name}'
            raise ValueError(msg)
        preset_name = match.group(1)
        ext = match.group(2).replace('jpeg', 'jpg')
        preset = self._PRESETS[preset_name]
        if preset.get('ext') != ext:
            msg = f'Preset {preset_name} expects .{preset["ext"]}; got .{ext}'
            raise ValueError(msg)

        image = self._render_preset(source, preset)
        strip = bool(preset.get('strip'))
        q = preset.get('q')
        if ext == 'jpg':
            opts = f'Q={q}' if q is not None else 'Q=85'
            if strip:
                opts += ',strip'
            return image.write_to_buffer(f'.jpg[{opts}]')
        opts = 'strip' if strip else ''
        return image.write_to_buffer(f'.png[{opts}]')

    @staticmethod
    def _render_preset(source: Path, preset: dict[str, object]) -> pyvips.Image:
        box = preset.get('box')
        if isinstance(box, tuple):
            w, h = box
            return pyvips.Image.thumbnail(
                str(source), w, height=h, crop='centre', size='both'
            )
        long_side = preset.get('long_side')
        if isinstance(long_side, int):
            return pyvips.Image.thumbnail(
                str(source), long_side, height=long_side, size='down'
            )
        # No resize: just decode at native size.
        return pyvips.Image.new_from_file(str(source), access='sequential')
