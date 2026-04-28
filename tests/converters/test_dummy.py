from __future__ import annotations

from pathlib import Path

from convertfs.converters.dummy import DummyConverter


def test_dummy_converter_returns_fixed_payload(tmp_path: Path, convert_bytes) -> None:
	source = tmp_path / 'note.txt'
	source.write_text('any contents — ignored', encoding='utf-8')

	output = convert_bytes(DummyConverter(), source, tmp_path / 'note.txt.copy')

	assert output == b'lol hi'


def test_dummy_converter_ignores_source_contents(tmp_path: Path, convert_bytes) -> None:
	# The dummy converter is intentionally content-blind: regardless of
	# what's in the source, the output is the same canary payload. Tests
	# that rely on this in higher layers (e.g. drag-out detection in the
	# FUSE layer) shouldn't be silently broken by changing this behavior.
	a = tmp_path / 'a.txt'
	b = tmp_path / 'b.txt'
	a.write_text('alpha', encoding='utf-8')
	b.write_text('beta', encoding='utf-8')

	output_a = convert_bytes(DummyConverter(), a, tmp_path / 'a.txt.copy')
	output_b = convert_bytes(DummyConverter(), b, tmp_path / 'b.txt.copy')

	assert output_a == output_b == b'lol hi'
