import logging
from argparse import ArgumentParser
from pathlib import Path

from convertfs.converters import discover_converters
from convertfs.main import ConvertFS


def main() -> None:
    parser = ArgumentParser(description='File format conversion as a FUSE filesystem')
    parser.add_argument('mount_dir', type=Path)
    parser.add_argument('-v', '--verbose', action='store_true')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='[%(asctime)s] %(levelname)s %(name)s: %(message)s',
        datefmt='%H:%M:%S',
    )

    convertfs = ConvertFS(args.mount_dir)
    for converter in discover_converters():
        convertfs.add_converter(converter)
    convertfs.run()


if __name__ == '__main__':
    main()
