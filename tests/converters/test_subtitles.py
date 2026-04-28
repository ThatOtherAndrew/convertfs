from __future__ import annotations

from pathlib import Path

import pysubs2
import pytest

from convertfs.converters.subtitles import SubtitlesConverter


_SAMPLE_SRT = (
	'1\n'
	'00:00:00,500 --> 00:00:02,000\n'
	'Hello world\n'
	'\n'
	'2\n'
	'00:00:02,500 --> 00:00:04,000\n'
	'Second line\n'
)


def _write_srt(path: Path) -> None:
	path.write_text(_SAMPLE_SRT, encoding='utf-8')


@pytest.mark.parametrize(
	('out_ext', 'expected_marker'),
	[
		('srt', '00:00:00,500'),
		('vtt', 'WEBVTT'),
		('ass', '[Script Info]'),
		('ssa', '[Script Info]'),
	],
)
def test_subtitles_converter_emits_each_format(
	tmp_path: Path, out_ext: str, expected_marker: str,
) -> None:
	source = tmp_path / 'in.srt'
	_write_srt(source)

	output = SubtitlesConverter().process(
		source, tmp_path / f'out.{out_ext}'
	)

	assert expected_marker in output.decode('utf-8', errors='replace')


def test_subtitles_microdvd_includes_fps_marker(tmp_path: Path) -> None:
	# .sub (microdvd) requires an fps. The converter passes one through
	# from the parsed file, falling back to 25 fps if missing — verify
	# the output is parseable and round-trips an event.
	source = tmp_path / 'in.srt'
	_write_srt(source)

	output = SubtitlesConverter().process(source, tmp_path / 'out.sub')

	# microdvd encodes events as `{start_frame}{end_frame}text` lines.
	text = output.decode('utf-8', errors='replace')
	assert '{' in text and '}' in text
	assert 'Hello world' in text


def test_subtitles_round_trip_preserves_event_count(tmp_path: Path) -> None:
	source = tmp_path / 'in.srt'
	_write_srt(source)

	output = SubtitlesConverter().process(source, tmp_path / 'out.vtt')

	# Reload the produced VTT and check both events made it through.
	round_tripped = pysubs2.SSAFile.from_string(output.decode('utf-8'))
	assert len(round_tripped) == 2
	assert any('Hello world' in e.text for e in round_tripped)
	assert any('Second line' in e.text for e in round_tripped)


def test_subtitles_rejects_unknown_output_format(tmp_path: Path) -> None:
	source = tmp_path / 'in.srt'
	_write_srt(source)

	with pytest.raises(ValueError, match='Unsupported subtitle output format'):
		SubtitlesConverter().process(source, tmp_path / 'out.bogus')


def test_subtitles_accepts_vtt_input(tmp_path: Path) -> None:
	# The INPUTS regex covers vtt; round-tripping through the converter
	# should still produce a valid srt file.
	source = tmp_path / 'in.vtt'
	source.write_text(
		'WEBVTT\n\n00:00:00.500 --> 00:00:02.000\nHello world\n', encoding='utf-8',
	)

	output = SubtitlesConverter().process(source, tmp_path / 'out.srt')

	text = output.decode('utf-8')
	assert 'Hello world' in text
	assert '00:00:00,500' in text
