from __future__ import annotations

import io
from pathlib import Path
from random import Random

import av
import pytest

from convertfs.converters.video_compressor_h264 import VideoCompresserH264


def _make_noise_mp4(path: Path, width: int, height: int) -> None:
	container = av.open(str(path), mode='w', format='mp4')
	stream = container.add_stream('mpeg4', rate=1)
	stream.width = width
	stream.height = height
	stream.pix_fmt = 'yuv420p'

	frame = av.VideoFrame(width, height, 'rgb24')
	frame.planes[0].update(Random(0).randbytes(width * height * 3))

	for packet in stream.encode(frame):
		container.mux(packet)
	for packet in stream.encode():
		container.mux(packet)

	container.close()


@pytest.mark.parametrize(
	('source_size', 'expected_size'),
	[
		((320, 160), (480, 240)),
		((160, 320), (240, 480)),
	],
)
def test_video_compressor_h264_scales_by_shortest_side(
	tmp_path: Path,
	source_size: tuple[int, int],
	expected_size: tuple[int, int],
) -> None:
	source = tmp_path / 'clip.mp4'
	_make_noise_mp4(source, width=source_size[0], height=source_size[1])

	requested = tmp_path / 'resolutions' / 'clip.240p.mp4'
	output = VideoCompresserH264().process(source, requested)

	with av.open(io.BytesIO(output), mode='r') as container:
		video_stream = next(stream for stream in container.streams if stream.type == 'video')
		assert (video_stream.codec_context.width, video_stream.codec_context.height) == expected_size
		assert video_stream.codec_context.name == 'h264'


def test_video_compressor_h264_rejects_unknown_target_pattern(tmp_path: Path) -> None:
	source = tmp_path / 'clip.mp4'
	_make_noise_mp4(source, width=16, height=16)

	with pytest.raises(ValueError, match='Unsupported output file name'):
		VideoCompresserH264().process(source, tmp_path / 'resolutions' / 'clip.mp4')


def test_video_compressor_h264_tries_hardware_encoders_then_falls_back(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
	converter = VideoCompresserH264()
	attempted: list[str] = []

	def fake_compress(source: Path, target_short_side: int, encoder_name: str) -> bytes:
		attempted.append(encoder_name)
		if encoder_name == 'libx264':
			return b'final-output'
		raise RuntimeError('encoder unavailable')

	monkeypatch.setattr(converter, '_compress_with_encoder', fake_compress)

	output = converter.process(tmp_path / 'source.mp4', tmp_path / 'resolutions' / 'clip.240p.mp4')

	assert output == b'final-output'
	assert attempted == ['h264_nvenc', 'h264_qsv', 'h264_v4l2m2m', 'libx264']