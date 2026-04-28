from __future__ import annotations

from pathlib import Path

import pyvips
import pytest

from convertfs.converters.images import ImagesConverter


def _write_sample_image(path: Path, *, width: int = 8, height: int = 8) -> None:
	red = pyvips.Image.black(width, height).new_from_image(255)
	green = pyvips.Image.black(width, height)
	blue = pyvips.Image.black(width, height)
	rgb = red.bandjoin([green, blue])
	rgb.write_to_file(str(path))


def _run(source: Path, requested: Path, tmp_path: Path) -> bytes:
	# Helper: run the converter against a physical dest under tmp_path,
	# return the bytes written. The `requested` path is a virtual path
	# (may include 'quality/' / 'resolutions/' / 'presets/' prefixes)
	# so it is not used as the dest itself.
	dest = tmp_path / f'out-{requested.name}'
	ImagesConverter().process(source, requested, dest)
	return dest.read_bytes()


def _dimensions(data: bytes) -> tuple[int, int]:
	# pyvips reads from a buffer with new_from_buffer; we don't pass a
	# format hint because each output sets its own magic bytes.
	image = pyvips.Image.new_from_buffer(data, '')
	return image.width, image.height


# ----- flat-route conversions and no-op passthrough -----


def test_images_converter_png_to_jpeg(tmp_path: Path) -> None:
	source = tmp_path / 'sample.png'
	_write_sample_image(source)

	output = _run(source, tmp_path / 'sample.jpg', tmp_path)

	assert output[:3] == b'\xff\xd8\xff'


def test_images_converter_jpeg_to_png(tmp_path: Path) -> None:
	source = tmp_path / 'sample.jpg'
	_write_sample_image(source)

	output = _run(source, tmp_path / 'sample.png', tmp_path)

	assert output[:8] == b'\x89PNG\r\n\x1a\n'


@pytest.mark.parametrize(
	('out_ext', 'magic'),
	[
		('webp', b'RIFF'),
		('tif', b'II*\x00'),
		('tiff', b'II*\x00'),
		('gif', b'GIF8'),
		('avif', b''),  # ftyp box at offset 4; checked separately below
		('bmp', b'BM'),
		# heic uses the ISO BMFF container, same ftyp-at-offset-4
		# structure as avif; checked in the heic-specific branch.
		('heic', b''),
	],
)
def test_images_converter_png_to_each_flat_format(
	tmp_path: Path, out_ext: str, magic: bytes,
) -> None:
	source = tmp_path / 'sample.png'
	_write_sample_image(source)

	output = _run(source, tmp_path / f'sample.{out_ext}', tmp_path)

	if out_ext in ('avif', 'heic'):
		assert output[4:8] == b'ftyp'
		return
	assert output[: len(magic)] == magic


def test_images_converter_same_format_is_passthrough(tmp_path: Path) -> None:
	# JPEG round-trip would otherwise re-encode and lose quality; the
	# converter must short-circuit and copy source bytes verbatim.
	source = tmp_path / 'sample.jpg'
	_write_sample_image(source)
	original = source.read_bytes()

	output = _run(source, tmp_path / 'sample.jpeg', tmp_path)

	assert output == original


def test_images_converter_format_alias_jpg_jpeg_passthrough(tmp_path: Path) -> None:
	# .jpg and .jpeg are aliases; either direction should be a no-op.
	source = tmp_path / 'sample.jpeg'
	_write_sample_image(source)
	original = source.read_bytes()

	output = _run(source, tmp_path / 'sample.jpg', tmp_path)

	assert output == original


def test_images_converter_format_alias_tif_tiff_passthrough(tmp_path: Path) -> None:
	source = tmp_path / 'sample.tif'
	_write_sample_image(source)
	original = source.read_bytes()

	output = _run(source, tmp_path / 'sample.tiff', tmp_path)

	assert output == original


def test_images_converter_rejects_extensionless_request(tmp_path: Path) -> None:
	source = tmp_path / 'sample.png'
	_write_sample_image(source)

	dest = tmp_path / 'out-noext'
	with pytest.raises(ValueError, match='no extension'):
		ImagesConverter().process(source, tmp_path / 'noext', dest)


# ----- quality/ subdir -----


def _write_noisy_image(path: Path, *, width: int = 256, height: int = 256) -> None:
	# A solid-color image hits each codec's small-image floor and makes
	# quality differences vanish; gradients give the encoders something
	# to actually quantise.
	image = pyvips.Image.xyz(width, height) % 256  # 0..255 in each band
	image = image.cast('uchar')
	image.write_to_file(str(path))


@pytest.mark.parametrize('ext', ['jpg', 'webp', 'avif'])
def test_images_quality_levels_produce_monotonically_smaller_files(
	tmp_path: Path, ext: str,
) -> None:
	# Lower-quality settings should produce smaller-or-equal files for
	# lossy codecs. Use a gradient image (rather than a solid colour) so
	# the encoders have content to quantise — solid images compress to
	# near-floor sizes that defeat the comparison.
	source = tmp_path / 'sample.png'
	_write_noisy_image(source)

	tiers = ['very-low', 'low', 'medium', 'high', 'very-high']
	sizes: list[int] = []
	for tier in tiers:
		dest = tmp_path / f'out-{tier}.{ext}'
		ImagesConverter().process(
			source, Path('quality') / f'sample.{tier}.{ext}', dest
		)
		sizes.append(dest.stat().st_size)

	# Monotonic non-decreasing across tiers, low → high.
	assert sizes == sorted(sizes)
	# very-high is strictly larger than very-low — different codecs
	# compress at different absolute ratios, so we don't pin a multiple,
	# only the ordering.
	assert sizes[-1] > sizes[0]


def test_images_quality_rejects_unknown_level(tmp_path: Path) -> None:
	source = tmp_path / 'sample.png'
	_write_sample_image(source)

	dest = tmp_path / 'out-bogus'
	with pytest.raises(ValueError, match='Unsupported quality output'):
		ImagesConverter().process(
			source, Path('quality') / 'sample.medium.bogus', dest,
		)


# ----- resolutions/ subdir -----


@pytest.mark.parametrize(
	('size_name', 'expected_long_side'),
	[
		('4k', 4096),
		('2k', 2048),
		('1080p', 1920),
		('720p', 1280),
		('480p', 854),
		('thumbnail', 256),
	],
)
def test_images_resolutions_cap_long_side_for_oversized_input(
	tmp_path: Path, size_name: str, expected_long_side: int,
) -> None:
	# Input is larger than 4k so every tier must downscale; the long
	# side of the output should match the configured cap exactly.
	source = tmp_path / 'sample.png'
	_write_sample_image(source, width=5000, height=3000)

	output = _run(
		source, Path('resolutions') / f'sample.{size_name}.jpg', tmp_path,
	)

	w, h = _dimensions(output)
	assert max(w, h) == expected_long_side
	# Aspect ratio is preserved (within 1px rounding).
	assert abs((w / h) - (5000 / 3000)) < 0.01


def test_images_resolutions_does_not_upscale_smaller_input(tmp_path: Path) -> None:
	# size='down' means the resizer never scales up; small inputs come
	# through at their native size.
	source = tmp_path / 'sample.png'
	_write_sample_image(source, width=200, height=150)

	output = _run(
		source, Path('resolutions') / 'sample.4k.jpg', tmp_path,
	)

	w, h = _dimensions(output)
	assert (w, h) == (200, 150)


def test_images_resolutions_supports_png_output(tmp_path: Path) -> None:
	source = tmp_path / 'sample.png'
	_write_sample_image(source, width=2000, height=1000)

	output = _run(
		source, Path('resolutions') / 'sample.720p.png', tmp_path,
	)

	assert output[:8] == b'\x89PNG\r\n\x1a\n'
	w, h = _dimensions(output)
	assert max(w, h) == 1280


def test_images_resolutions_rejects_unknown_size(tmp_path: Path) -> None:
	source = tmp_path / 'sample.png'
	_write_sample_image(source)

	dest = tmp_path / 'out-bogus.jpg'
	with pytest.raises(ValueError, match='Unsupported resolution output'):
		ImagesConverter().process(
			source, Path('resolutions') / 'sample.5k.jpg', dest,
		)


# ----- presets/ subdir -----


def test_images_preset_web_caps_at_1920(tmp_path: Path) -> None:
	source = tmp_path / 'sample.png'
	_write_sample_image(source, width=4000, height=2000)

	output = _run(source, Path('presets') / 'sample.web.jpg', tmp_path)

	assert output[:3] == b'\xff\xd8\xff'
	w, h = _dimensions(output)
	assert max(w, h) == 1920


def test_images_preset_email_caps_at_1280(tmp_path: Path) -> None:
	source = tmp_path / 'sample.png'
	_write_sample_image(source, width=4000, height=2000)

	output = _run(source, Path('presets') / 'sample.email.jpg', tmp_path)

	w, h = _dimensions(output)
	assert max(w, h) == 1280


def test_images_preset_print_keeps_native_dimensions(tmp_path: Path) -> None:
	source = tmp_path / 'sample.png'
	_write_sample_image(source, width=600, height=400)

	output = _run(source, Path('presets') / 'sample.print.jpg', tmp_path)

	w, h = _dimensions(output)
	assert (w, h) == (600, 400)


def test_images_preset_thumbnail_uses_png(tmp_path: Path) -> None:
	source = tmp_path / 'sample.png'
	_write_sample_image(source, width=2000, height=1500)

	output = _run(source, Path('presets') / 'sample.thumbnail.png', tmp_path)

	assert output[:8] == b'\x89PNG\r\n\x1a\n'
	w, h = _dimensions(output)
	assert max(w, h) == 256


def test_images_preset_social_square_crops_to_square(tmp_path: Path) -> None:
	source = tmp_path / 'sample.png'
	_write_sample_image(source, width=3000, height=2000)

	output = _run(
		source, Path('presets') / 'sample.social-square.jpg', tmp_path,
	)

	w, h = _dimensions(output)
	assert (w, h) == (1080, 1080)


def test_images_preset_social_story_uses_portrait_box(tmp_path: Path) -> None:
	source = tmp_path / 'sample.png'
	_write_sample_image(source, width=3000, height=2000)

	output = _run(
		source, Path('presets') / 'sample.social-story.jpg', tmp_path,
	)

	w, h = _dimensions(output)
	assert (w, h) == (1080, 1920)


def test_images_preset_rejects_mismatched_extension(tmp_path: Path) -> None:
	# The web preset is jpg-only; asking for it under .png should error.
	source = tmp_path / 'sample.png'
	_write_sample_image(source)

	dest = tmp_path / 'out-bad.png'
	with pytest.raises(ValueError, match='Preset web expects'):
		ImagesConverter().process(
			source, Path('presets') / 'sample.web.png', dest,
		)


def test_images_preset_rejects_unknown_name(tmp_path: Path) -> None:
	source = tmp_path / 'sample.png'
	_write_sample_image(source)

	dest = tmp_path / 'out-bogus.jpg'
	with pytest.raises(ValueError, match='Unsupported preset output'):
		ImagesConverter().process(
			source, Path('presets') / 'sample.bogus.jpg', dest,
		)
