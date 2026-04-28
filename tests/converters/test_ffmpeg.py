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


def test_ffmpeg_converter_same_container_is_passthrough(tmp_path: Path) -> None:
	# mp4 -> mp4 is a stream-copy remux that produces the same bytes
	# semantically; the converter short-circuits and returns the source
	# verbatim, avoiding a useless encode/mux pass.
	source = tmp_path / 'clip.mp4'
	_make_noise_mp4(source)
	original = source.read_bytes()

	output = FFMpegConverter().process(source, tmp_path / 'clip.converted.mp4')

	assert output == original


def test_ffmpeg_converter_remux_preserves_video_stream_codec(tmp_path: Path) -> None:
	# Stream-copy via add_stream_from_template: the decoded codec on the
	# output should match what we wrote to the source (mpeg4 here).
	source = tmp_path / 'clip.mp4'
	_make_noise_mp4(source)

	output = FFMpegConverter().process(
		source, tmp_path / 'clip.converted.mkv'
	)

	with av.open(io.BytesIO(output), mode='r') as container:
		video_stream = next(
			stream for stream in container.streams if stream.type == 'video'
		)
		assert video_stream.codec_context.name == 'mpeg4'


def test_ffmpeg_converter_avi_to_mkv_round_trip(tmp_path: Path) -> None:
	# Build an AVI source and remux to MKV — covers the non-mp4 input
	# path, which uses the same stream-copy logic but a different
	# demuxer/muxer pair.
	source = tmp_path / 'clip.avi'
	container = av.open(str(source), mode='w', format='avi')
	stream = container.add_stream('mpeg4', rate=1)
	stream.width = 16
	stream.height = 16
	stream.pix_fmt = 'yuv420p'
	frame = av.VideoFrame(16, 16, 'rgb24')
	frame.planes[0].update(Random(1).randbytes(16 * 16 * 3))
	for packet in stream.encode(frame):
		container.mux(packet)
	for packet in stream.encode():
		container.mux(packet)
	container.close()

	output = FFMpegConverter().process(
		source, tmp_path / 'clip.converted.mkv'
	)

	# MKV magic.
	assert output[:4] == b'\x1aE\xdf\xa3'
	with av.open(io.BytesIO(output), mode='r') as outc:
		assert any(s.type == 'video' for s in outc.streams)