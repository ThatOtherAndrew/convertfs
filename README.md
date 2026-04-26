# convertfs
An FUSE filesystem that converts files on the fly using a variety of libraries.

The idea if you copy a file into a directory that convertfs is mounted on, and then can move out a converted file. The converted file will be generated on the fly when you read it.

## Usage
1. `nix develop` to setup a development environment
2. `uv sync` to isntall dependencies
3. `uv run convertfs --help` to see usage instructions for the CLI
4. `uv run convertfs --mount <mountpoint>` to start the filesystem, and then you can copy files into the mountpoint to convert them.


## Supported Filetypes
1. Video
  - Remux MP4, AVI and MKV files
	- Compress MP4 files using H.264, automatically selecting the best encoder available on the system (NVENC, QSV or x264), and providing a variety of presets.
2. Images
	- Convert between a variety of formats, including JPEG, PNG, TIFF and WebP.
3. Text files
	- Convert PDF and Office files to markdown via Microsoft's `markitdown` library.
