import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from typing_extensions import override

from convertfs.converter import Converter

if TYPE_CHECKING:
    import pyvips


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
    # Formats the libvips build we ship cannot save (no bmpsave/heifsave
    # in the binary wheel). For these we round-trip through Pillow:
    # libvips decodes the source, we hand the raw pixels to Pillow, and
    # Pillow writes the output. pillow-heif registers as the HEIF saver
    # for the .heic extension.
    _PILLOW_OUTPUT_FORMATS: ClassVar[frozenset[str]] = frozenset(
        {'bmp', 'heic'}
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
    def process(self, source: Path, requested: Path, dest: Path) -> None:
        top = requested.parts[0] if len(requested.parts) > 1 else ''

        if top == 'quality':
            self._process_quality(source, requested, dest)
            return
        if top == 'resolutions':
            self._process_resolution(source, requested, dest)
            return
        if top == 'presets':
            self._process_preset(source, requested, dest)
            return
        self._process_flat(source, requested, dest)

    def _process_flat(self, source: Path, requested: Path, dest: Path) -> None:
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
            shutil.copyfile(source, dest)
            return

        # Pillow fallback for formats libvips can't save in the wheel
        # build (bmp, heic). Decode with libvips for speed, encode with
        # Pillow for compatibility.
        if output_format in self._PILLOW_OUTPUT_FORMATS:
            self._save_via_pillow(source, output_format, dest)
            return

        # Lazy: pyvips pulls in libvips bindings (a few MB of native code)
        # at import time. Keep it off startup so users that never touch
        # images don't pay for it. Python caches the import after the
        # first call, so subsequent conversions are free.
        import pyvips

        image = pyvips.Image.new_from_file(str(source), access='sequential')
        target = f'{dest}[Q=90]' if output_format == 'jpeg' else str(dest)
        image.write_to_file(target)

    @staticmethod
    def _save_via_pillow(source: Path, output_format: str, dest: Path) -> None:
        """Encode `source` as `output_format` (bmp|heic) through Pillow.

        Pillow can read what libvips writes (and the other way round) for
        every input format the converter accepts, so we don't need to
        bridge raw pixels — Pillow opens the source path directly.
        """
        # Lazy: Pillow + pillow-heif each pull in native libs; only load
        # them when a bmp/heic output is actually requested.
        from PIL import Image

        if output_format == 'heic':
            # pillow-heif registers a HEIF saver under format='HEIF';
            # the .heic and .heif containers are the same MIAF/ISO box
            # structure, so this produces a valid .heic file.
            import pillow_heif

            pillow_heif.register_heif_opener()
            pil_format = 'HEIF'
        else:
            pil_format = output_format.upper()

        with Image.open(str(source)) as image:
            # JPEG/HEIF require an RGB-family mode; convert anything
            # exotic (e.g. palette, CMYK) up front so the save doesn't
            # error out.
            if image.mode not in ('RGB', 'RGBA', 'L'):
                image = image.convert('RGB')
            image.save(str(dest), format=pil_format)

    def _process_quality(self, source: Path, requested: Path, dest: Path) -> None:
        match = re.search(
            r'\.(very-low|low|medium|high|very-high)\.(jpg|jpeg|webp|avif)$',
            requested.name,
        )
        if match is None:
            msg = f'Unsupported quality output: {requested.name}'
            raise ValueError(msg)
        level_name, ext = match.group(1), match.group(2).replace('jpeg', 'jpg')
        q = self._QUALITY_LEVELS[level_name]
        # Lazy: see _process_flat for rationale.
        import pyvips

        image = pyvips.Image.new_from_file(str(source), access='sequential')
        image.write_to_file(f'{dest}[Q={q}]')

    def _process_resolution(self, source: Path, requested: Path, dest: Path) -> None:
        match = re.search(
            r'\.(4k|2k|1080p|720p|480p|thumbnail)\.(jpg|jpeg|webp|png)$',
            requested.name,
        )
        if match is None:
            msg = f'Unsupported resolution output: {requested.name}'
            raise ValueError(msg)
        size_name, ext = match.group(1), match.group(2).replace('jpeg', 'jpg')
        long_side = self._RESOLUTIONS[size_name]
        # Lazy: see _process_flat for rationale.
        import pyvips

        image = pyvips.Image.thumbnail(
            str(source), long_side, height=long_side, size='down'
        )
        if ext == 'jpg':
            image.write_to_file(f'{dest}[Q=85,strip]')
            return
        if ext == 'webp':
            image.write_to_file(f'{dest}[Q=85,strip]')
            return
        image.write_to_file(f'{dest}[strip]')

    def _process_preset(self, source: Path, requested: Path, dest: Path) -> None:
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
            image.write_to_file(f'{dest}[{opts}]')
            return
        opts = 'strip' if strip else ''
        image.write_to_file(f'{dest}[{opts}]')

    @staticmethod
    def _render_preset(source: Path, preset: dict[str, object]) -> 'pyvips.Image':
        # Lazy: see _process_flat for rationale.
        import pyvips

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
