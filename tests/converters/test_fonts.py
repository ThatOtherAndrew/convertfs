from __future__ import annotations

from pathlib import Path

import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

from convertfs.converters.fonts import FontsConverter


def _build_minimal_ttf(path: Path) -> None:
	"""Synthesize a 1-glyph TTF with the minimum tables fontTools requires.

	Built on the fly so the test is hermetic: no system fonts needed.
	"""
	fb = FontBuilder(1024, isTTF=True)
	fb.setupGlyphOrder(['.notdef', 'A'])
	fb.setupCharacterMap({0x41: 'A'})

	pen = TTGlyphPen(None)
	pen.moveTo((0, 0))
	pen.lineTo((500, 0))
	pen.lineTo((500, 700))
	pen.lineTo((0, 700))
	pen.closePath()
	glyph = pen.glyph()
	fb.setupGlyf({'.notdef': glyph, 'A': glyph})
	fb.setupHorizontalMetrics({'.notdef': (500, 0), 'A': (500, 0)})
	fb.setupHorizontalHeader(ascent=800, descent=-200)
	fb.setupNameTable({'familyName': 'Test', 'styleName': 'Regular'})
	fb.setupOS2(
		sTypoAscender=800,
		sTypoDescender=-200,
		usWinAscent=800,
		usWinDescent=200,
	)
	fb.setupPost()
	fb.font.save(str(path))


@pytest.fixture
def sample_ttf(tmp_path: Path) -> Path:
	path = tmp_path / 'sample.ttf'
	_build_minimal_ttf(path)
	return path


def test_fonts_converter_ttf_to_woff_wraps_in_zlib(sample_ttf: Path, convert_bytes) -> None:
	output = convert_bytes(FontsConverter(), sample_ttf, sample_ttf.with_name('out.woff'),
	)

	# WOFF magic is "wOFF" (0x774F4646).
	assert output[:4] == b'wOFF'


def test_fonts_converter_ttf_to_woff2_uses_brotli_wrapper(sample_ttf: Path, convert_bytes) -> None:
	output = convert_bytes(FontsConverter(), sample_ttf, sample_ttf.with_name('out.woff2'),
	)

	# WOFF2 magic is "wOF2".
	assert output[:4] == b'wOF2'


def test_fonts_converter_woff_round_trip_back_to_ttf(sample_ttf: Path, convert_bytes) -> None:
	woff = convert_bytes(FontsConverter(), sample_ttf, sample_ttf.with_name('inter.woff'),
	)
	woff_path = sample_ttf.with_name('inter.woff')
	woff_path.write_bytes(woff)

	back_path = sample_ttf.with_name('back.ttf')
	out = convert_bytes(FontsConverter(), woff_path, back_path)
	back_path.write_bytes(out)

	# A bare TTF starts with the SFNT scaler tag (\x00\x01\x00\x00 for
	# TrueType outlines) — the woff unwrapping should produce that.
	assert out[:4] == b'\x00\x01\x00\x00'

	# Make sure fontTools can still read the round-tripped font and find
	# the glyph we put in.
	font = TTFont(str(back_path))
	assert 'A' in font.getGlyphOrder()


def test_fonts_converter_no_op_ttf_to_ttf_passthrough(sample_ttf: Path, convert_bytes) -> None:
	# Source bytes should round-trip verbatim — no fontTools repack.
	original = sample_ttf.read_bytes()
	output = convert_bytes(FontsConverter(), sample_ttf, sample_ttf.with_name('again.ttf'))
	assert output == original


def test_fonts_converter_ttf_otf_swap_is_passthrough(sample_ttf: Path, convert_bytes) -> None:
	# ttf and otf are identical at the SFNT container level for a given
	# table set; the converter treats this swap as a no-op rather than
	# round-tripping through fontTools.
	original = sample_ttf.read_bytes()
	output = convert_bytes(FontsConverter(), sample_ttf, sample_ttf.with_name('out.otf'))
	assert output == original


def test_fonts_converter_rejects_unknown_output(tmp_path: Path, sample_ttf: Path) -> None:
	with pytest.raises(ValueError, match='Unsupported font output'):
		FontsConverter().process(
			sample_ttf, sample_ttf.with_name('out.eot'), tmp_path / '_dest.bin'
		)


def test_fonts_converter_woff_passthrough(sample_ttf: Path, convert_bytes) -> None:
	# Same-flavour conversion (woff -> woff) should also be a no-op.
	woff = convert_bytes(FontsConverter(), sample_ttf, sample_ttf.with_name('w.woff'))
	woff_path = sample_ttf.with_name('w.woff')
	woff_path.write_bytes(woff)

	output = convert_bytes(FontsConverter(), woff_path, sample_ttf.with_name('w2.woff'))
	assert output == woff
