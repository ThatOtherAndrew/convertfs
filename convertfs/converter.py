from abc import ABC, abstractmethod
from pathlib import Path
from re import Pattern


class Converter(ABC):
    INPUTS: tuple[Pattern, ...] = ()
    OUTPUT_FILES: tuple[Path, ...] = ()
    OUTPUT_DIRS: tuple[Path, ...] = ()

    @abstractmethod
    def process(self, source: Path, requested: Path) -> bytes:
        pass
