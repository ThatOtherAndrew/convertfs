from __future__ import annotations

import bz2
import io
import lzma
import tarfile
import zipfile
from pathlib import Path

import py7zr
import pytest

from convertfs.converters.archives import ArchivesConverter


# A small but representative member set: short text, longer text, and a
# binary blob. Used everywhere in this file so the read/write round-trips
# are clearly verifiable.
_MEMBERS: list[tuple[str, bytes]] = [
	('readme.txt', b'hello world\n'),
	('docs/notes.md', b'# notes\n\nbody body body\n'),
	('blob.bin', bytes(range(256))),
]


def _build_zip(path: Path, members: list[tuple[str, bytes]]) -> Path:
	with zipfile.ZipFile(str(path), 'w', compression=zipfile.ZIP_DEFLATED) as z:
		for name, data in members:
			z.writestr(name, data)
	return path


def _build_tar(
	path: Path,
	members: list[tuple[str, bytes]],
	*,
	compression: str | None = None,
) -> Path:
	mode = {'gz': 'w:gz', 'bz2': 'w:bz2', 'xz': 'w:xz', None: 'w'}[compression]
	with tarfile.open(str(path), mode) as t:
		for name, data in members:
			info = tarfile.TarInfo(name=name)
			info.size = len(data)
			t.addfile(info, io.BytesIO(data))
	return path


def _build_7z(path: Path, members: list[tuple[str, bytes]]) -> Path:
	with py7zr.SevenZipFile(str(path), 'w') as z:
		for name, data in members:
			z.writestr(data, name)
	return path


def _read_zip(data: bytes) -> dict[str, bytes]:
	with zipfile.ZipFile(io.BytesIO(data), 'r') as z:
		return {info.filename: z.read(info) for info in z.infolist() if not info.is_dir()}


def _read_tar(data: bytes, *, compression: str | None = None) -> dict[str, bytes]:
	mode = {'gz': 'r:gz', 'bz2': 'r:bz2', 'xz': 'r:xz', None: 'r'}[compression]
	out: dict[str, bytes] = {}
	with tarfile.open(fileobj=io.BytesIO(data), mode=mode) as t:
		for info in t.getmembers():
			if not info.isfile():
				continue
			f = t.extractfile(info)
			if f is None:
				continue
			out[info.name] = f.read()
	return out


def _read_7z(data: bytes) -> dict[str, bytes]:
	from py7zr.io import BytesIOFactory

	with py7zr.SevenZipFile(io.BytesIO(data), 'r') as z:
		factory = BytesIOFactory(limit=1024 * 1024 * 1024)
		z.extractall(factory=factory)
		return {name: bio.read() for name, bio in factory.products.items()}


@pytest.mark.parametrize(
	('out_suffix', 'reader', 'reader_kwargs'),
	[
		('zip', _read_zip, {}),
		('tar', _read_tar, {'compression': None}),
		('tar.gz', _read_tar, {'compression': 'gz'}),
		('tar.bz2', _read_tar, {'compression': 'bz2'}),
		('tar.xz', _read_tar, {'compression': 'xz'}),
		('7z', _read_7z, {}),
	],
)
def test_archives_zip_to_each_format_round_trips_members(
	tmp_path: Path,
	out_suffix: str,
	reader,
	reader_kwargs: dict,
) -> None:
	source = _build_zip(tmp_path / 'src.zip', _MEMBERS)

	output = ArchivesConverter().process(
		source, Path('formats') /f'src.{out_suffix}'
	)

	assert reader(output, **reader_kwargs) == dict(_MEMBERS)


def test_archives_tar_xz_input_round_trips_to_zip(tmp_path: Path) -> None:
	source = _build_tar(tmp_path / 'src.tar.xz', _MEMBERS, compression='xz')

	output = ArchivesConverter().process(
		source, Path('formats') /'src.zip'
	)

	assert _read_zip(output) == dict(_MEMBERS)


def test_archives_7z_input_round_trips_to_tar_gz(tmp_path: Path) -> None:
	source = _build_7z(tmp_path / 'src.7z', _MEMBERS)

	output = ArchivesConverter().process(
		source, Path('formats') /'src.tar.gz'
	)

	assert _read_tar(output, compression='gz') == dict(_MEMBERS)


def test_archives_zip_to_zip_format_subdir_is_passthrough(tmp_path: Path) -> None:
	# No-op: same-format request under formats/. Source bytes returned
	# verbatim, no extract+repack.
	source = _build_zip(tmp_path / 'src.zip', _MEMBERS)
	original = source.read_bytes()

	output = ArchivesConverter().process(
		source, Path('formats') /'src.zip'
	)

	assert output == original


def test_archives_tar_gz_to_tgz_alias_is_passthrough(tmp_path: Path) -> None:
	# tar.gz and tgz are the same format under different filenames; the
	# converter should treat the alias as a no-op.
	source = _build_tar(tmp_path / 'src.tar.gz', _MEMBERS, compression='gz')
	original = source.read_bytes()

	output = ArchivesConverter().process(
		source, Path('formats') /'src.tgz'
	)

	assert output == original


def test_archives_compression_levels_produce_smaller_files_at_max(
	tmp_path: Path,
) -> None:
	# Use highly compressible content so fast vs max produces a measurable
	# difference; deterministic so the test is stable.
	highly_compressible = [('blob.bin', b'A' * 100_000)]
	source = _build_zip(tmp_path / 'src.zip', highly_compressible)

	fast = ArchivesConverter().process(
		source, Path('compression') /'src.fast.zip'
	)
	balanced = ArchivesConverter().process(
		source, Path('compression') /'src.balanced.zip'
	)
	max_ = ArchivesConverter().process(
		source, Path('compression') /'src.max.zip'
	)

	# All three must contain the same data.
	assert _read_zip(fast) == _read_zip(balanced) == _read_zip(max_) == dict(
		highly_compressible
	)
	# max should be no larger than balanced, which should be no larger
	# than fast. (Bounds rather than strict <, since on tiny payloads two
	# levels can tie.)
	assert len(max_) <= len(balanced) <= len(fast)


def test_archives_7z_compression_levels_extract_correctly(tmp_path: Path) -> None:
	source = _build_zip(tmp_path / 'src.zip', _MEMBERS)

	output = ArchivesConverter().process(
		source, Path('compression') /'src.balanced.7z'
	)

	assert _read_7z(output) == dict(_MEMBERS)


def test_archives_compression_subdir_does_not_short_circuit_on_same_format(
	tmp_path: Path,
) -> None:
	# Even if the source is already a zip, requests under compression/
	# are explicitly asking for a specific level — must NOT be treated
	# as a no-op (otherwise the level dial does nothing). Use highly
	# compressible content so different levels produce visibly different
	# sizes; the no-op path would always return the source bytes.
	highly_compressible = [('blob.bin', b'A' * 200_000)]
	source = _build_zip(tmp_path / 'src.zip', highly_compressible)

	fast = ArchivesConverter().process(
		source, Path('compression') / 'src.fast.zip'
	)
	max_ = ArchivesConverter().process(
		source, Path('compression') / 'src.max.zip'
	)

	# Both decode back to the original payload …
	assert _read_zip(fast) == _read_zip(max_) == dict(highly_compressible)
	# … but they differ from each other, which is the proof the level
	# dial is applied. If the converter had short-circuited on
	# same-format, both would equal source.read_bytes() and therefore
	# equal each other.
	assert fast != max_


def test_archives_rejects_unknown_archive_input(tmp_path: Path) -> None:
	# A `.rar` source isn't covered by either the input or output format
	# table; the no-op same-format check trips first because it has to
	# normalise the source extension before deciding. Either way, the
	# converter must refuse the request rather than silently producing
	# garbage.
	source = tmp_path / 'mystery.rar'
	source.write_bytes(b'not an archive')

	with pytest.raises(ValueError):
		ArchivesConverter().process(
			source, Path('formats') / 'mystery.zip'
		)


def test_archives_rejects_unknown_compression_target(tmp_path: Path) -> None:
	source = _build_zip(tmp_path / 'src.zip', _MEMBERS)

	with pytest.raises(ValueError, match='Unsupported compression output'):
		ArchivesConverter().process(
			source, Path('compression') /'src.bogus.zip'
		)


def test_archives_emits_format_specific_magic_bytes(tmp_path: Path) -> None:
	# Sanity check: each format's expected leading magic shows up in the
	# converter output. This catches accidental container swaps.
	source = _build_zip(tmp_path / 'src.zip', _MEMBERS)
	convert = ArchivesConverter().process

	zip_out = convert(source, Path('formats') /'src.zip')
	tar_out = convert(source, Path('formats') /'src.tar')
	tgz_out = convert(source, Path('formats') /'src.tar.gz')
	tbz_out = convert(source, Path('formats') /'src.tar.bz2')
	txz_out = convert(source, Path('formats') /'src.tar.xz')
	sevenz_out = convert(source, Path('formats') /'src.7z')

	# zip: PK signature.
	assert zip_out[:2] == b'PK'
	# tar: ustar at offset 257.
	assert tar_out[257:262] == b'ustar'
	# gzip: 1f 8b.
	assert tgz_out[:2] == b'\x1f\x8b'
	# bzip2: BZh.
	assert tbz_out[:3] == b'BZh'
	# xz: fd 7z XZ 00.
	assert txz_out[:6] == b'\xfd7zXZ\x00'
	# 7z: 7z\xbc\xaf'\x1c
	assert sevenz_out[:6] == b"7z\xbc\xaf'\x1c"

	# Cross-check that nested compression layers are valid where applicable.
	assert lzma.decompress(txz_out)[:1]
	assert bz2.decompress(tbz_out)[:1]
