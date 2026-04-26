from pathlib import Path
import io
import re

import av

from convertfs.converter import Converter


class VideoCompresserH264(Converter):
		INPUTS = (
			re.compile(r'^(.*)\.(mp4|avi|mkv|mov|mxf)$'),
		)
		OUTPUT_DIRS = (Path('resolutions'), Path('quality'))
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
		)

		QUALITY_PROFILES = {
			'very-low': {'bit_rate': 400_000, 'crf': '36', 'preset': 'veryfast'},
			'low': {'bit_rate': 800_000, 'crf': '32', 'preset': 'veryfast'},
			'medium': {'bit_rate': 1_500_000, 'crf': '28', 'preset': 'medium'},
			'high': {'bit_rate': 3_000_000, 'crf': '24', 'preset': 'slow'},
			'very-high': {'bit_rate': 6_000_000, 'crf': '20', 'preset': 'slow'},
		}

		@staticmethod
		def _encoder_candidates() -> tuple[str, ...]:
			candidates: list[str] = []

			if Path('/dev/nvidiactl').exists() or Path('/proc/driver/nvidia/version').exists():
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
			quality_profile: dict[str, str | int] | None = None,
		) -> bytes:
			buffer = io.BytesIO()

			with av.open(str(source), 'r') as input_container:
				video_stream = next((stream for stream in input_container.streams if stream.type == 'video'), None)
				if video_stream is None:
					raise ValueError('Input file does not contain a video stream')

				source_width = video_stream.codec_context.width or video_stream.width
				source_height = video_stream.codec_context.height or video_stream.height
				if not source_width or not source_height:
					raise ValueError('Could not determine source resolution')

				if target_short_side is None:
					target_width = self._make_even(source_width)
					target_height = self._make_even(source_height)
				else:
					scale_factor = target_short_side / min(source_width, source_height)
					target_width = self._make_even(source_width * scale_factor)
					target_height = self._make_even(source_height * scale_factor)

				frame_rate = video_stream.average_rate or 30

				with av.open(buffer, 'w', format='mp4') as output_container:
					output_stream = output_container.add_stream(encoder_name, rate=frame_rate)
					output_stream.width = target_width
					output_stream.height = target_height
					output_stream.pix_fmt = 'yuv420p'

					profile = quality_profile or {'bit_rate': 1_500_000, 'crf': '23', 'preset': 'medium'}
					output_stream.bit_rate = int(profile['bit_rate'])

					if encoder_name == 'libx264':
						output_stream.options = {
							'crf': str(profile['crf']),
							'preset': str(profile['preset']),
						}

					for frame in input_container.decode(video_stream):
						scaled_frame = frame.reformat(width=target_width, height=target_height, format='yuv420p')
						for packet in output_stream.encode(scaled_frame):
							output_container.mux(packet)

					for packet in output_stream.encode():
						output_container.mux(packet)

			return buffer.getvalue()

		def process(self, source: Path, requested: Path) -> bytes:
			resolution_match = re.search(r'\.(2160|1080|720|480|360|240)p\.mp4$', requested.name)
			quality_match = re.search(r'\.(very-low|low|medium|high|very-high)\.mp4$', requested.name)
			if resolution_match is None and quality_match is None:
				raise ValueError(f'Unsupported output file name: {requested.name}')

			target_short_side = int(resolution_match.group(1)) if resolution_match else None
			quality_profile = self.QUALITY_PROFILES[quality_match.group(1)] if quality_match else None
			encoders = self._encoder_candidates()

			last_error: Exception | None = None
			for encoder_name in encoders:
				try:
					return self._compress_with_encoder(
						source,
						encoder_name,
						target_short_side=target_short_side,
						quality_profile=quality_profile,
					)
				except Exception as exc:
					last_error = exc

			raise RuntimeError('No usable H.264 encoder was available') from last_error
