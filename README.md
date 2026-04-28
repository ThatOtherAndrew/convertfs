# convertfs

File format conversion as a FUSE filesystem

## About

`convertfs` is a FUSE filesystem that converts files on the fly using a variety of libraries.

The idea if you copy a file into a directory that convertfs is mounted on, and then can move out a converted file. The converted file will be generated on the fly when you read it.

3. `uv run convertfs --help` to see usage instructions for the CLI
4. `uv run convertfs --mount <mountpoint>` to start the filesystem, and then you can copy files into the mountpoint to convert them.

## Installation
You can install with the following command:

```shell
pip install git+https://github.com/ThatOtherAndrew/convertfs
```

Or, if using `uv`:

```shell
pip install git+https://github.com/ThatOtherAndrew/convertfs
```

## Usage
Mount convertfs over a directory:

```shell
convertfs my_dir
```

Then, `mv` or drag files into the directory to be magically converted!

## Supported Filetypes
1. Video
	- Remux MP4, AVI and MKV files.
	- Compress MP4 files using H.264, automatically selecting the best encoder available on the system (NVENC, QSV or x264), with `resolutions/`, `quality/` and `presets/` dial directories.
2. Audio
	- Convert between MP3, FLAC, WAV, OGG, Opus, M4A, AAC and AIFF.
	- `quality/` (very-low → very-high) and `bitrate/` (64k → 320k) dial directories for lossy formats.
	- `presets/` directory with named profiles: `podcast.mp3`, `audiobook.m4a`, `music-cd.flac`, `music-hires.flac`, `voice-memo.opus`.
3. Images
	- Convert between JPEG, PNG, TIFF, WebP, BMP, GIF, HEIC and AVIF.
	- `quality/` directory (very-low → very-high) for JPEG/WebP/AVIF.
	- `resolutions/` directory (4k, 2k, 1080p, 720p, 480p, thumbnail) using libvips fast shrink-on-load.
	- `presets/` directory: `web.jpg`, `email.jpg`, `print.jpg`, `thumbnail.png`, `social-square.jpg`, `social-story.jpg`.
4. Subtitles
	- Convert between SRT, WebVTT, ASS, SSA and MicroDVD (`.sub`) via `pysubs2`.
5. Documents
	- Convert PDF and Office files (DOCX/PPTX/XLSX/XLS) to Markdown via Microsoft's `markitdown` library.
	- Convert Markdown to standalone HTML.
6. Data / config
	- Round-trip JSON, YAML, TOML and XML.
7. Tabular
	- Round-trip CSV, TSV and XLSX.
8. Archives
	- Repack between ZIP, TAR, TAR.GZ, TAR.BZ2, TAR.XZ and 7z.
	- `compression/` directory with `fast`, `balanced` and `max` levels for ZIP and 7z.
9. Fonts
	- Convert between TTF, OTF, WOFF and WOFF2 via `fontTools`.

## Always-on service (systemd)
Run convertfs as a `systemd --user` service so it mounts at login and shows up in your file manager automatically.

1. Install the system prerequisites:

```shell
sudo apt install libfuse3-dev pkg-config libvips42
```

2. From the repo root, run the installer:

```shell
./scripts/install-service.sh                 # mounts at ~/convert
./scripts/install-service.sh ~/somewhere     # or pick your own path
```

The script runs `uv sync`, writes `~/.config/systemd/user/convertfs.service`, and enables it immediately.

3. (Optional) Keep the mount alive even when you're logged out:

```shell
sudo loginctl enable-linger "$USER"
```

4. Manage the service with:

```shell
systemctl --user status convertfs
journalctl --user -u convertfs -f
systemctl --user restart convertfs
systemctl --user disable --now convertfs    # stop autostart
```

5. To make the mount appear in the GNOME Files sidebar, open it once and press `Ctrl+D` to bookmark it.

## Development
1. `nix develop` to setup a development environment
2. `uv sync` to isntall dependencies
