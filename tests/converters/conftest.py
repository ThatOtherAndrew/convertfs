"""Shared helpers for converter tests.

Converters now write their output into a pre-allocated `dest` file rather
than returning bytes. To keep the existing test assertions terse, this
module exposes a `convert_bytes` fixture that runs the converter against
a fresh tempfile and returns the bytes written.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from convertfs.converter import Converter


@pytest.fixture
def convert_bytes(tmp_path: Path) -> Callable[..., bytes]:
	"""Run `converter.process(source, requested, dest)` and return dest bytes.

	A unique dest path is allocated under `tmp_path` for each call so
	consecutive invocations within a test don't collide.
	"""

	counter = {'n': 0}

	def _run(
		converter: Converter,
		source: Path,
		requested: Path,
		*,
		dest: Path | None = None,
	) -> bytes:
		if dest is None:
			counter['n'] += 1
			dest = tmp_path / f'_convertfs_test_dest_{counter["n"]}_{requested.name}'
		converter.process(source, requested, dest)
		return dest.read_bytes()

	return _run
