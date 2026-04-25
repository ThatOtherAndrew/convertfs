from pathlib import Path


class ConvertFS:
    def __init__(self, mount_dir: Path):
        self.mount_dir = mount_dir
        self.converters = []

    def add_converter(self, converter):
        self.converters.append(converter)

    def run(self):
        # TODO
        pass
