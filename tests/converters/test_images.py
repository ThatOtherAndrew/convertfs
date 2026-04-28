from __future__ import annotations

from pathlib import Path

import pyvips

from convertfs.converters.images import ImagesConverter


def _write_sample_image(path: Path) -> None:
	red = pyvips.Image.black(8, 8).new_from_image(255)
	green = pyvips.Image.black(8, 8)
	blue = pyvips.Image.black(8, 8)
	rgb = red.bandjoin([green, blue])
	rgb.write_to_file(str(path))


def test_images_converter_png_to_jpeg(tmp_path: Path) -> None:
	source = tmp_path / 'sample.png'
	_write_sample_image(source)

	dest = tmp_path / 'sample.jpg'
	ImagesConverter().process(source, tmp_path / 'sample.jpg', dest)

	assert dest.read_bytes()[:3] == b'\xff\xd8\xff'


def test_images_converter_jpeg_to_png(tmp_path: Path) -> None:
	source = tmp_path / 'sample.jpg'
	_write_sample_image(source)

	dest = tmp_path / 'sample.png'
	ImagesConverter().process(source, tmp_path / 'sample.png', dest)

	assert dest.read_bytes()[:8] == b'\x89PNG\r\n\x1a\n'
