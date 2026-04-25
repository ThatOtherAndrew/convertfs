from convertfs.main import ConvertFS
from pathlib import Path

from convertfs.converters.dummy import DummyConverter

if __name__ == '__main__':
    convertfs = ConvertFS(Path())
    convertfs.add_converter(DummyConverter())
    convertfs.run()
