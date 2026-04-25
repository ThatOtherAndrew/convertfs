from pathlib import Path
from re import Pattern


class Converter:
    INPUTS: set[Pattern] = set()
    OUTPUT_FILES: set[Path] = set()
    OUTPUT_DIRS: set[Path] = set()
