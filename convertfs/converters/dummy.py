import re
from pathlib import Path

from typing_extensions import override

from convertfs.converter import Converter


class DummyConverter(Converter):
    INPUTS = (re.compile(r'^(.*)\.txt$'),)
    OUTPUT_FILES = (Path('{}.txt.copy'),)

    @override
    def process(self, source: Path, requested: Path, dest: Path) -> None:
        dest.write_bytes(b'lol hi')
