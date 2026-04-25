from argparse import ArgumentParser
from pathlib import Path

from convertfs.converters.dummy import DummyConverter
from convertfs.main import ConvertFS


def main() -> None:
    parser = ArgumentParser(description='File format conversion as a FUSE filesystem')
    parser.add_argument('mount_dir', type=Path)
    args = parser.parse_args()

    convertfs = ConvertFS(args.mount_dir)
    convertfs.add_converter(DummyConverter())
    convertfs.run()


if __name__ == '__main__':
    main()
