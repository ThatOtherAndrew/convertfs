import re
from pathlib import Path
from typing import ClassVar

from typing_extensions import override

from convertfs.converter import Converter


class SubtitlesConverter(Converter):
    INPUTS = (re.compile(r'^(.*)\.(srt|vtt|ass|ssa|sub)$'),)
    OUTPUT_FILES = (
        Path('{}.srt'),
        Path('{}.vtt'),
        Path('{}.ass'),
        Path('{}.ssa'),
        Path('{}.sub'),
    )

    _FORMAT_BY_EXT: ClassVar[dict[str, str]] = {
        'srt': 'srt',
        'vtt': 'vtt',
        'ass': 'ass',
        'ssa': 'ssa',
        'sub': 'microdvd',
    }

    @override
    def process(self, source: Path, requested: Path, dest: Path) -> None:
        ext = requested.suffix.lstrip('.').lower()
        format_ = self._FORMAT_BY_EXT.get(ext)
        if format_ is None:
            msg = f'Unsupported subtitle output format: {ext}'
            raise ValueError(msg)

        # Lazy: pysubs2 is only needed for actual subtitle conversions, so
        # importing it inside process() keeps it off the startup path for
        # users who never touch subtitle files.
        import pysubs2

        subs = pysubs2.load(str(source))
        kwargs: dict[str, object] = {}
        if format_ == 'microdvd':
            kwargs['fps'] = subs.fps or 25.0
        dest.write_text(subs.to_string(format_, **kwargs), encoding='utf-8')
