from pathlib import Path
import io
import re

import av

from convertfs.converter import Converter


class VideoCompresserH264(Converter):
		INPUTS = (
			re.compile(r'^(.*)\.(mp4|avi|mkv|mov|mxf)$'),
		)
		OUTPUT_DIRS = (Path('resolutions'),)
		OUTPUT_FILES = (
			Path('resolutions/{}.2160p.mp4'),
			Path('resolutions/{}.1080p.mp4'),
			Path('resolutions/{}.720p.mp4'),
			Path('resolutions/{}.480p.mp4'),
			Path('resolutions/{}.360p.mp4'),
			Path('resolutions/{}.240p.mp4'),
		)

		@staticmethod
		def _make_even(value: float) -> int:
			even_value = int(round(value / 2.0) * 2)
			return max(2, even_value)

		def _compress_with_encoder(self, source: Path, target_short_side: int, encoder_name: str) -> bytes:
			buffer = io.BytesIO()

			with av.open(str(source), 'r') as input_container:
				video_stream = next((stream for stream in input_container.streams if stream.type == 'video'), None)
				if video_stream is None:
					raise ValueError('Input file does not contain a video stream')

				source_width = video_stream.codec_context.width or video_stream.width
				source_height = video_stream.codec_context.height or video_stream.height
				if not source_width or not source_height:
					raise ValueError('Could not determine source resolution')

				scale_factor = target_short_side / min(source_width, source_height)
				target_width = self._make_even(source_width * scale_factor)
				target_height = self._make_even(source_height * scale_factor)

				frame_rate = video_stream.average_rate or 30

				with av.open(buffer, 'w', format='mp4') as output_container:
					output_stream = output_container.add_stream(encoder_name, rate=frame_rate)
					output_stream.width = target_width
					output_stream.height = target_height
					output_stream.pix_fmt = 'yuv420p'

					if encoder_name == 'libx264':
						output_stream.options = {'crf': '23', 'preset': 'medium'}

					for frame in input_container.decode(video_stream):
						scaled_frame = frame.reformat(width=target_width, height=target_height, format='yuv420p')
						for packet in output_stream.encode(scaled_frame):
							output_container.mux(packet)

					for packet in output_stream.encode():
						output_container.mux(packet)

			return buffer.getvalue()

		def process(self, source: Path, requested: Path) -> bytes:
			resolution_match = re.search(r'\.(2160|1080|720|480|360|240)p\.mp4$', requested.name)
			if resolution_match is None:
				raise ValueError(f'Unsupported output file name: {requested.name}')

			target_short_side = int(resolution_match.group(1))
			encoders = ('h264_nvenc', 'h264_qsv', 'h264_v4l2m2m', 'libx264')

			last_error: Exception | None = None
			for encoder_name in encoders:
				try:
					return self._compress_with_encoder(source, target_short_side, encoder_name)
				except Exception as exc:
					last_error = exc

			raise RuntimeError('No usable H.264 encoder was available') from last_error
