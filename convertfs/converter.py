from pathlib import Path
from re import Pattern


class Converter:
    INPUTS: tuple[Pattern, ...] = ()
    OUTPUT_FILES: tuple[Path, ...] = ()
    OUTPUT_DIRS: tuple[Path, ...] = ()
