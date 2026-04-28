"""Audio interconversion plus quality / bitrate / preset dials.

Mirrors the directory-driven pattern from ``video_compressor_h264.py``:

- ``formats/<name>.<ext>`` — straight format swap with sensible default bitrate.
- ``quality/<name>.<level>.<ext>`` — five lossy bitrate tiers.
- ``bitrate/<name>.<bitrate>.<ext>`` — explicit bitrates for fine control.
- ``presets/<name>.<preset>.<ext>`` — named scenarios (podcast, audiobook, …).

Transcoding is done with PyAV (already a dependency). Each encode rebuilds
the output stream from scratch — we don't try to remux compatible codecs as
that would defeat the point of the quality dials.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from typing_extensions import override

from convertfs.converter import Converter

if TYPE_CHECKING:
    import av

_LOSSY_FORMATS = ('mp3', 'opus', 'm4a', 'aac', 'ogg')
_ALL_FORMATS = (*_LOSSY_FORMATS, 'flac', 'wav', 'aiff')


class AudioConverter(Converter):
    INPUTS = (re.compile(r'^(.*)\.(mp3|wav|flac|ogg|m4a|aac|opus|aiff|wma)$'),)
    OUTPUT_DIRS = (
        Path('formats'),
        Path('quality'),
        Path('bitrate'),
        Path('presets'),
    )
    OUTPUT_FILES = (
        # Format swaps with sensible defaults.
        *(Path(f'formats/{{}}.{ext}') for ext in _ALL_FORMATS),
        # Quality dials per lossy format.
        *(
            Path(f'quality/{{}}.{level}.{ext}')
            for ext in _LOSSY_FORMATS
            for level in ('very-low', 'low', 'medium', 'high', 'very-high')
        ),
        # Explicit bitrate (mp3 only — the format users most often dial).
        *(
            Path(f'bitrate/{{}}.{rate}.mp3')
            for rate in ('64k', '96k', '128k', '192k', '256k', '320k')
        ),
        # Named scenarios.
        Path('presets/{}.podcast.mp3'),
        Path('presets/{}.audiobook.m4a'),
        Path('presets/{}.music-cd.flac'),
        Path('presets/{}.music-hires.flac'),
        Path('presets/{}.voice-memo.opus'),
    )

    # Per-output-extension encoder + container wiring. ``fmt`` is the sample
    # format the encoder accepts; ``rate`` is the target sample rate.
    _PROFILES: ClassVar[dict[str, dict[str, object]]] = {
        'mp3':  {'codec': 'mp3',       'container': 'mp3',  'fmt': 's16p', 'rate': 44100, 'default_bitrate': 192000},
        'aac':  {'codec': 'aac',       'container': 'adts', 'fmt': 'fltp', 'rate': 44100, 'default_bitrate': 192000},
        'm4a':  {'codec': 'aac',       'container': 'ipod', 'fmt': 'fltp', 'rate': 44100, 'default_bitrate': 192000},
        'opus': {'codec': 'libopus',   'container': 'ogg',  'fmt': 's16',  'rate': 48000, 'default_bitrate': 128000},
        'ogg':  {'codec': 'libvorbis', 'container': 'ogg',  'fmt': 'fltp', 'rate': 44100, 'default_bitrate': 160000},
        'flac': {'codec': 'flac',      'container': 'flac', 'fmt': 's16',  'rate': 44100, 'default_bitrate': None},
        'wav':  {'codec': 'pcm_s16le', 'container': 'wav',  'fmt': 's16',  'rate': 44100, 'default_bitrate': None},
        'aiff': {'codec': 'pcm_s16be', 'container': 'aiff', 'fmt': 's16',  'rate': 44100, 'default_bitrate': None},
    }

    # Bitrate tiers per lossy codec (bits/s).
    _QUALITY_TIERS: ClassVar[dict[str, dict[str, int]]] = {
        'mp3':  {'very-low': 64_000,  'low': 96_000,  'medium': 128_000, 'high': 192_000, 'very-high': 320_000},
        'opus': {'very-low': 32_000,  'low': 48_000,  'medium': 96_000,  'high': 128_000, 'very-high': 192_000},
        'm4a':  {'very-low': 64_000,  'low': 96_000,  'medium': 128_000, 'high': 192_000, 'very-high': 256_000},
        'aac':  {'very-low': 64_000,  'low': 96_000,  'medium': 128_000, 'high': 192_000, 'very-high': 256_000},
        'ogg':  {'very-low': 64_000,  'low': 96_000,  'medium': 128_000, 'high': 192_000, 'very-high': 256_000},
    }

    _PRESETS: ClassVar[dict[str, dict[str, object]]] = {
        'podcast':      {'ext': 'mp3',  'bitrate': 64_000,  'channels': 1, 'rate': 44100},
        'audiobook':    {'ext': 'm4a',  'bitrate': 32_000,  'channels': 1, 'rate': 22050},
        'music-cd':     {'ext': 'flac', 'bitrate': None,    'channels': 2, 'rate': 44100},
        'music-hires':  {'ext': 'flac', 'bitrate': None,    'channels': 2, 'rate': 96000},
        'voice-memo':   {'ext': 'opus', 'bitrate': 24_000,  'channels': 1, 'rate': 48000},
    }

    @override
    def process(self, source: Path, requested: Path, dest: Path) -> None:
        top = requested.parts[0] if len(requested.parts) > 1 else ''

        if top == 'formats':
            ext = requested.suffix.lstrip('.').lower()
            # No-op: same format requested as source. Skip re-encode so
            # lossy formats (mp3, opus, m4a, aac, ogg) don't take a
            # generation-loss hit on a round-trip.
            if source.suffix.lstrip('.').lower() == ext:
                shutil.copyfile(source, dest)
                return
            self._transcode(source, dest, ext)
            return

        if top == 'quality':
            match = re.search(
                r'\.(very-low|low|medium|high|very-high)\.(' + '|'.join(_LOSSY_FORMATS) + r')$',
                requested.name,
            )
            if match is None:
                msg = f'Unsupported quality output: {requested.name}'
                raise ValueError(msg)
            level, ext = match.group(1), match.group(2)
            bitrate = self._QUALITY_TIERS[ext][level]
            self._transcode(source, dest, ext, bitrate=bitrate)
            return

        if top == 'bitrate':
            match = re.search(r'\.(\d+)k\.mp3$', requested.name)
            if match is None:
                msg = f'Unsupported bitrate output: {requested.name}'
                raise ValueError(msg)
            kbps = int(match.group(1))
            self._transcode(source, dest, 'mp3', bitrate=kbps * 1000)
            return

        if top == 'presets':
            match = re.search(
                r'\.(podcast|audiobook|music-cd|music-hires|voice-memo)\.(mp3|m4a|flac|opus)$',
                requested.name,
            )
            if match is None:
                msg = f'Unsupported preset output: {requested.name}'
                raise ValueError(msg)
            preset_name, ext = match.group(1), match.group(2)
            preset = self._PRESETS[preset_name]
            if preset['ext'] != ext:
                msg = f'Preset {preset_name} expects .{preset["ext"]}; got .{ext}'
                raise ValueError(msg)
            self._transcode(
                source,
                dest,
                ext,
                bitrate=preset.get('bitrate'),
                channels=preset.get('channels'),
                rate_override=preset.get('rate'),
            )
            return

        msg = f'Unsupported audio output path: {requested}'
        raise ValueError(msg)

    def _transcode(
        self,
        source: Path,
        dest: Path,
        ext: str,
        *,
        bitrate: int | None = None,
        channels: int | None = None,
        rate_override: int | None = None,
    ) -> None:
        profile = self._PROFILES.get(ext)
        if profile is None:
            msg = f'Unsupported audio output format: {ext}'
            raise ValueError(msg)

        codec = str(profile['codec'])
        container = str(profile['container'])
        fmt = str(profile['fmt'])
        rate = int(rate_override) if rate_override else int(profile['rate'])
        if bitrate is None:
            bitrate = profile.get('default_bitrate')

        # Lazy: PyAV pulls the FFmpeg shared libs at import time. Defer
        # so just having the audio converter registered doesn't pay that
        # cost on startup.
        import av

        with av.open(str(source), 'r') as input_container:
            in_stream = next(
                (s for s in input_container.streams if s.type == 'audio'), None
            )
            if in_stream is None:
                msg = 'Input has no audio stream'
                raise ValueError(msg)

            layout = self._target_layout(in_stream, channels)

            with av.open(str(dest), 'w', format=container) as output_container:
                out_stream = output_container.add_stream(
                    codec, rate=rate, layout=layout
                )
                out_stream.format = fmt
                if bitrate is not None:
                    out_stream.bit_rate = int(bitrate)

                resampler = av.AudioResampler(format=fmt, layout=layout, rate=rate)
                for frame in input_container.decode(in_stream):
                    for resampled in resampler.resample(frame):
                        for packet in out_stream.encode(resampled):
                            output_container.mux(packet)
                for resampled in resampler.resample(None):
                    for packet in out_stream.encode(resampled):
                        output_container.mux(packet)
                for packet in out_stream.encode():
                    output_container.mux(packet)

    @staticmethod
    def _target_layout(in_stream: 'av.AudioStream', channels: int | None) -> str:
        if channels == 1:
            return 'mono'
        if channels == 2:
            return 'stereo'
        try:
            nb = in_stream.layout.nb_channels
            name = in_stream.layout.name
        except Exception:
            return 'stereo'
        if nb == 1:
            return 'mono'
        if nb == 2:
            return 'stereo'
        # Anything wider (5.1, 7.1, …) — downmix to stereo for compatibility.
        if name and nb <= 2:
            return name
        return 'stereo'
