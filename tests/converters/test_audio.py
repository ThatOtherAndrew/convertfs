from __future__ import annotations

import io
import math
import struct
import wave
from pathlib import Path

import av
import pytest

from convertfs.converters.audio import AudioConverter


def _write_wav(
	path: Path,
	*,
	rate: int = 44100,
	duration_s: float = 0.25,
	channels: int = 1,
	freq: float = 440.0,
) -> Path:
	"""Synthesize a short sine wave; PyAV/FFmpeg can decode this directly."""
	frames = int(rate * duration_s)
	samples = bytearray()
	for n in range(frames):
		# 16-bit signed PCM, scaled to ~half full-scale to avoid clipping.
		value = int(16384 * math.sin(2 * math.pi * freq * n / rate))
		for _ in range(channels):
			samples += struct.pack('<h', value)

	with wave.open(str(path), 'wb') as w:
		w.setnchannels(channels)
		w.setsampwidth(2)
		w.setframerate(rate)
		w.writeframes(bytes(samples))
	return path


def _probe_audio(data: bytes, *, fmt: str | None = None) -> dict:
	# FFmpeg picks a decoder for the file's format on read; for some codecs
	# the decoder name differs from the canonical codec name (e.g. mp3 is
	# decoded as `mp3float`). Normalise to a stable family name so callers
	# can compare against the encoder name they asked for.
	open_kwargs = {'format': fmt} if fmt else {}
	with av.open(io.BytesIO(data), 'r', **open_kwargs) as container:
		stream = next(s for s in container.streams if s.type == 'audio')
		raw_name = stream.codec_context.name
		family_aliases = {
			'mp3float': 'mp3',
			'libopus': 'opus',
			'libvorbis': 'vorbis',
		}
		codec = family_aliases.get(raw_name, raw_name)
		return {
			'codec': codec,
			'rate': stream.codec_context.sample_rate,
			'channels': stream.codec_context.channels,
			'bitrate': stream.codec_context.bit_rate,
		}


@pytest.fixture
def wav_source(tmp_path: Path) -> Path:
	return _write_wav(tmp_path / 'tone.wav')


@pytest.fixture
def stereo_wav_source(tmp_path: Path) -> Path:
	return _write_wav(tmp_path / 'tone-stereo.wav', channels=2)


@pytest.mark.parametrize(
	('out_ext', 'expected_codec', 'fmt_hint'),
	[
		('mp3', 'mp3', None),
		('flac', 'flac', None),
		('opus', 'opus', 'ogg'),
		('m4a', 'aac', None),
		('aac', 'aac', 'aac'),
		('wav', 'pcm_s16le', None),
		('aiff', 'pcm_s16be', None),
	],
)
def test_audio_formats_route_emits_expected_codec(
	wav_source: Path,
	out_ext: str,
	expected_codec: str,
	fmt_hint: str | None,
) -> None:
	output = AudioConverter().process(
		wav_source, Path('formats') / f'tone.{out_ext}'
	)

	probe = _probe_audio(output, fmt=fmt_hint)
	assert probe['codec'] == expected_codec


def test_audio_formats_same_extension_is_passthrough(wav_source: Path) -> None:
	original = wav_source.read_bytes()

	output = AudioConverter().process(wav_source, Path('formats') / 'tone.wav')

	assert output == original


def test_audio_quality_levels_apply_distinct_bitrates(wav_source: Path) -> None:
	# Pick mp3 because its bitrate dial spans a clear range from very-low
	# (64k) to very-high (320k).
	low = AudioConverter().process(
		wav_source, Path('quality') / 'tone.very-low.mp3'
	)
	high = AudioConverter().process(
		wav_source, Path('quality') / 'tone.very-high.mp3'
	)

	low_probe = _probe_audio(low)
	high_probe = _probe_audio(high)

	assert low_probe['codec'] == 'mp3'
	assert high_probe['codec'] == 'mp3'
	# bitrate fields should reflect roughly the configured tiers.
	assert low_probe['bitrate'] is not None
	assert high_probe['bitrate'] is not None
	assert high_probe['bitrate'] > low_probe['bitrate']


def test_audio_quality_rejects_unknown_level(wav_source: Path) -> None:
	with pytest.raises(ValueError, match='Unsupported quality output'):
		AudioConverter().process(
			wav_source, Path('quality') / 'tone.bogus.mp3'
		)


@pytest.mark.parametrize('rate', ['64', '128', '256'])
def test_audio_bitrate_route_applies_explicit_kbps(
	wav_source: Path, rate: str,
) -> None:
	output = AudioConverter().process(
		wav_source, Path('bitrate') / f'tone.{rate}k.mp3'
	)

	probe = _probe_audio(output)
	assert probe['codec'] == 'mp3'
	# The encoder may not hit the requested bitrate exactly on a short
	# clip, but it should land within ±25% of the target — comfortably
	# distinguishing 64k from 256k.
	expected = int(rate) * 1000
	assert probe['bitrate'] is not None
	assert 0.75 * expected <= probe['bitrate'] <= 1.25 * expected


def test_audio_bitrate_rejects_non_mp3_extension(wav_source: Path) -> None:
	with pytest.raises(ValueError, match='Unsupported bitrate output'):
		AudioConverter().process(
			wav_source, Path('bitrate') / 'tone.128k.opus'
		)


def test_audio_preset_podcast_outputs_mono_mp3(wav_source: Path) -> None:
	output = AudioConverter().process(
		wav_source, Path('presets') / 'tone.podcast.mp3'
	)

	probe = _probe_audio(output)
	assert probe['codec'] == 'mp3'
	assert probe['channels'] == 1


def test_audio_preset_audiobook_outputs_aac_mono_22050(wav_source: Path) -> None:
	output = AudioConverter().process(
		wav_source, Path('presets') / 'tone.audiobook.m4a'
	)

	probe = _probe_audio(output)
	assert probe['codec'] == 'aac'
	assert probe['channels'] == 1
	assert probe['rate'] == 22050


def test_audio_preset_music_cd_outputs_stereo_flac(stereo_wav_source: Path) -> None:
	output = AudioConverter().process(
		stereo_wav_source, Path('presets') / 'tone.music-cd.flac'
	)

	probe = _probe_audio(output)
	assert probe['codec'] == 'flac'
	assert probe['channels'] == 2
	assert probe['rate'] == 44100


def test_audio_preset_music_hires_uses_96khz_rate(stereo_wav_source: Path) -> None:
	output = AudioConverter().process(
		stereo_wav_source, Path('presets') / 'tone.music-hires.flac'
	)

	probe = _probe_audio(output)
	assert probe['codec'] == 'flac'
	assert probe['rate'] == 96000


def test_audio_preset_voice_memo_outputs_mono_opus(wav_source: Path) -> None:
	output = AudioConverter().process(
		wav_source, Path('presets') / 'tone.voice-memo.opus'
	)

	probe = _probe_audio(output, fmt='ogg')
	assert probe['codec'] == 'opus'
	assert probe['channels'] == 1


def test_audio_preset_rejects_mismatched_extension(wav_source: Path) -> None:
	# The audiobook preset is m4a-only; asking for it under a .mp3 name
	# is a contradiction the converter should refuse.
	with pytest.raises(ValueError, match='expects .m4a'):
		AudioConverter().process(
			wav_source, Path('presets') / 'tone.audiobook.mp3'
		)


def test_audio_unsupported_top_level_path(wav_source: Path) -> None:
	# Any subdir other than the four documented ones is unsupported.
	with pytest.raises(ValueError, match='Unsupported audio output path'):
		AudioConverter().process(
			wav_source, Path('mystery') / 'tone.mp3'
		)


def test_audio_input_with_no_audio_stream_raises(
	monkeypatch: pytest.MonkeyPatch, wav_source: Path,
) -> None:
	# Hard to construct an "audio-extension file with no audio stream"
	# fixture that PyAV will agree to open; instead, monkeypatch av.open
	# to surface a stream list with no audio entries. The guard we're
	# testing is a single line in _transcode; this is a faithful exercise.
	import convertfs.converters.audio as audio_mod

	class _FakeStream:
		type = 'video'

	class _FakeContainer:
		streams = [_FakeStream()]

		def __enter__(self) -> '_FakeContainer':
			return self

		def __exit__(self, *_) -> None:
			pass

	def _fake_open(_path: str, _mode: str) -> _FakeContainer:
		return _FakeContainer()

	# av is imported inside _transcode lazily; patch the module attribute
	# the converter resolves on first use.
	import av as av_module

	monkeypatch.setattr(av_module, 'open', _fake_open)
	# The audio converter imports `av` inside _transcode, so the patch
	# applied to the module is what gets used.
	_ = audio_mod  # silence unused-import warning while keeping the import
	# above to anchor the patched module.

	with pytest.raises(ValueError, match='no audio stream'):
		AudioConverter().process(
			wav_source, Path('formats') / 'tone.mp3'
		)
