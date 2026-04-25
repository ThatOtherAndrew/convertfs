import re
from pathlib import Path

from convertfs.converter import Converter


class DummyConverter(Converter):
    INPUTS = (re.compile(r'^(.*)\.txt$'),)
    OUTPUT_FILES = (Path('{}.txt.copy'),)
