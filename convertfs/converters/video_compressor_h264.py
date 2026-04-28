import io
import re
from pathlib import Path
from typing import ClassVar

from convertfs.converter import Converter


class VideoCompresserH264(Converter):
    INPUTS = (re.compile(r'^(.*)\.(mp4|avi|mkv|mov|mxf)$'),)
    OUTPUT_DIRS = (Path('resolutions'), Path('quality'), Path('presets'))
    OUTPUT_FILES = (
        Path('resolutions/{}.2160p.mp4'),
        Path('resolutions/{}.1080p.mp4'),
        Path('resolutions/{}.720p.mp4'),
        Path('resolutions/{}.480p.mp4'),
        Path('resolutions/{}.360p.mp4'),
        Path('resolutions/{}.240p.mp4'),
        Path('quality/{}.very-low.mp4'),
        Path('quality/{}.low.mp4'),
        Path('quality/{}.medium.mp4'),
        Path('quality/{}.high.mp4'),
        Path('quality/{}.very-high.mp4'),
        Path('presets/{}.youtube-1080p.mp4'),
        Path('presets/{}.youtube-1440p.mp4'),
        Path('presets/{}.youtube-2160p.mp4'),
        Path('presets/{}.youtube-720p.mp4'),
        Path('presets/{}.youtube-480p.mp4'),
    )

    QUALITY_PROFILES: ClassVar = {
        'very-low': {
            'bit_rate': 400_000,
            'crf': '36',
            'preset': 'veryfast',
            'rate_control': 'crf',
        },
        'low': {
            'bit_rate': 800_000,
            'crf': '32',
            'preset': 'veryfast',
            'rate_control': 'crf',
        },
        'medium': {
            'bit_rate': 1_500_000,
            'crf': '28',
            'preset': 'medium',
            'rate_control': 'crf',
        },
        'high': {
            'bit_rate': 3_000_000,
            'crf': '24',
            'preset': 'slow',
            'rate_control': 'crf',
        },
        'very-high': {
            'bit_rate': 6_000_000,
            'crf': '20',
            'preset': 'slow',
            'rate_control': 'crf',
        },
    }

    YOUTUBE_PRESETS: ClassVar = {
        'youtube-1080p': {
            'target_short_side': 1080,
            'bit_rate': 8_000_000,
            'preset': 'medium',
            'rate_control': 'cbr',
        },
        'youtube-1440p': {
            'target_short_side': 1440,
            'bit_rate': 16_000_000,
            'preset': 'medium',
            'rate_control': 'cbr',
        },
        'youtube-2160p': {
            'target_short_side': 2160,
            'bit_rate': 40_000_000,
            'preset': 'medium',
            'rate_control': 'cbr',
        },
        'youtube-720p': {
            'target_short_side': 720,
            'bit_rate': 5_000_000,
            'preset': 'medium',
            'rate_control': 'cbr',
        },
        'youtube-480p': {
            'target_short_side': 480,
            'bit_rate': 2_500_000,
            'preset': 'medium',
            'rate_control': 'cbr',
        },
    }

    # Cache of probed encoders. Populated on first call to
    # _encoder_candidates and reused thereafter — the underlying device
    # nodes don't appear or disappear during a process lifetime, so the
    # filesystem probes don't need to repeat per conversion.
    _encoder_cache: ClassVar[tuple[str, ...] | None] = None

    @classmethod
    def _encoder_candidates(cls) -> tuple[str, ...]:
        if cls._encoder_cache is None:
            cls._encoder_cache = cls._probe_encoders()
        return cls._encoder_cache

    @staticmethod
    def _probe_encoders() -> tuple[str, ...]:
        candidates: list[str] = []

        if (
            Path('/dev/nvidiactl').exists()
            or Path('/proc/driver/nvidia/version').exists()
        ):
            candidates.append('h264_nvenc')

        if Path('/dev/dri/renderD128').exists() or Path('/dev/dri/renderD129').exists():
            candidates.append('h264_qsv')

        candidates.append('libx264')
        return tuple(candidates)

    @staticmethod
    def _make_even(value: float) -> int:
        even_value = int(round(value / 2.0) * 2)
        return max(2, even_value)

    def _compress_with_encoder(
        self,
        source: Path,
        encoder_name: str,
        target_short_side: int | None = None,
        encoding_profile: dict[str, str | int] | None = None,
    ) -> bytes:
        # Lazy: PyAV is a heavy native import. Defer until a compression
        # job actually runs so the import cost doesn't land on startup.
        import av

        buffer = io.BytesIO()

        with av.open(str(source), 'r') as input_container:
            video_stream = next(
                (
                    stream
                    for stream in input_container.streams
                    if stream.type == 'video'
                ),
                None,
            )
            if video_stream is None:
                msg = 'Input file does not contain a video stream'
                raise ValueError(msg)

            source_width = video_stream.codec_context.width or video_stream.width
            source_height = video_stream.codec_context.height or video_stream.height
            if not source_width or not source_height:
                msg = 'Could not determine source resolution'
                raise ValueError(msg)

            if target_short_side is None:
                target_width = self._make_even(source_width)
                target_height = self._make_even(source_height)
            else:
                scale_factor = target_short_side / min(source_width, source_height)
                target_width = self._make_even(source_width * scale_factor)
                target_height = self._make_even(source_height * scale_factor)

            frame_rate = video_stream.average_rate or 30

            with av.open(buffer, 'w', format='mp4') as output_container:
                output_stream = output_container.add_stream(
                    encoder_name, rate=frame_rate
                )
                output_stream.width = target_width
                output_stream.height = target_height
                output_stream.pix_fmt = 'yuv420p'

                profile = encoding_profile or {
                    'bit_rate': 1_500_000,
                    'crf': '23',
                    'preset': 'medium',
                    'rate_control': 'crf',
                }
                output_stream.bit_rate = int(profile['bit_rate'])

                if encoder_name == 'libx264':
                    if profile.get('rate_control') == 'cbr':
                        bit_rate = str(profile['bit_rate'])
                        output_stream.options = {
                            'preset': str(profile['preset']),
                            'b:v': bit_rate,
                            'maxrate': bit_rate,
                            'minrate': bit_rate,
                            'bufsize': str(int(profile['bit_rate']) * 2),
                        }
                    else:
                        output_stream.options = {
                            'crf': str(profile['crf']),
                            'preset': str(profile['preset']),
                        }

                for frame in input_container.decode(video_stream):
                    scaled_frame = frame.reformat(
                        width=target_width, height=target_height, format='yuv420p'
                    )
                    for packet in output_stream.encode(scaled_frame):
                        output_container.mux(packet)

                for packet in output_stream.encode():
                    output_container.mux(packet)

        return buffer.getvalue()

    def process(self, source: Path, requested: Path) -> bytes:
        resolution_match = re.search(
            r'\.(2160|1080|720|480|360|240)p\.mp4$', requested.name
        )
        quality_match = re.search(
            r'\.(very-low|low|medium|high|very-high)\.mp4$', requested.name
        )
        preset_match = re.search(
            r'\.(youtube-(1080|1440|2160|720|480)p)\.mp4$', requested.name
        )
        if resolution_match is None and quality_match is None and preset_match is None:
            msg = f'Unsupported output file name: {requested.name}'
            raise ValueError(msg)

        target_short_side = int(resolution_match.group(1)) if resolution_match else None
        encoding_profile = (
            self.QUALITY_PROFILES[quality_match.group(1)] if quality_match else None
        )
        if preset_match:
            preset = self.YOUTUBE_PRESETS[preset_match.group(1)]
            target_short_side = int(preset['target_short_side'])
            encoding_profile = preset
        encoders = self._encoder_candidates()

        last_error: Exception | None = None
        for encoder_name in encoders:
            try:
                return self._compress_with_encoder(
                    source,
                    encoder_name,
                    target_short_side=target_short_side,
                    encoding_profile=encoding_profile,
                )
            except Exception as exc:
                last_error = exc

        msg = 'No usable H.264 encoder was available'
        raise RuntimeError(msg) from last_error
