from pathlib import Path
from convertfs.converter import Converter

from re import compile, Pattern


class DummyConverter(Converter):
    INPUTS = {compile(r'^(.*)\.txt$')}
    OUTPUT_FILES = {Path('{}.txt.copy')}
