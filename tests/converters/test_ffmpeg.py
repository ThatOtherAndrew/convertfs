from __future__ import annotations

import io
from pathlib import Path
from random import Random

import av
import pytest

from convertfs.converters.ffmpeg import FFMpegConverter


def _make_noise_mp4(path: Path) -> None:
	width = 16
	height = 16
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
	('requested_name', 'signature_check'),
	[
		('clip.converted.mp4', lambda data: data[4:8] == b'ftyp'),
		('clip.converted.avi', lambda data: data[:4] == b'RIFF' and data[8:12] == b'AVI '),
		('clip.converted.mkv', lambda data: data[:4] == b'\x1aE\xdf\xa3'),
	],
)
def test_ffmpeg_converter_produces_valid_container_headers(
	tmp_path: Path,
	requested_name: str,
	signature_check,
) -> None:
	source = tmp_path / 'clip.mp4'
	_make_noise_mp4(source)

	requested = tmp_path / requested_name
	output = FFMpegConverter().process(source, requested)

	assert signature_check(output)

	with av.open(io.BytesIO(output), mode='r') as container:
		assert len(container.streams) > 0
		assert any(stream.type == 'video' for stream in container.streams)