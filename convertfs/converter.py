from abc import ABC, abstractmethod
from pathlib import Path
from re import Pattern


class Converter(ABC):
    INPUTS: tuple[Pattern, ...] = ()
    OUTPUT_FILES: tuple[Path, ...] = ()
    OUTPUT_DIRS: tuple[Path, ...] = ()

    @abstractmethod
    def process(self, source: Path, requested: Path, dest: Path) -> None:
        """Write the converted output to `dest`.

        `source` is the absolute path to the input file. `requested` is the
        mount-relative path the user requested (used by converters that vary
        their behaviour by output filename, e.g. quality presets). `dest` is
        an empty file the converter must populate; it will be read back by
        the FUSE layer to serve `read()` calls.
        """
