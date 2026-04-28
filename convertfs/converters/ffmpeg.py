import io
import re
from pathlib import Path

from typing_extensions import override

from convertfs.converter import Converter


class FFMpegConverter(Converter):
    INPUTS = (re.compile(r'^(.*)\.(mp4|avi|mkv)$'),)
    OUTPUT_FILES = (
        Path('{}.converted.mp4'),
        Path('{}.converted.avi'),
        Path('{}.converted.mkv'),
    )

    @override
    def process(self, source: Path, requested: Path) -> bytes:
        output_ext = requested.suffix.lstrip('.')

        # No-op: same container in and out. The existing path is a stream
        # copy via add_stream_from_template, so the only thing skipping
        # avoids is rewriting the headers — but that's a per-file decode
        # of every packet for no end-user benefit. Just return the source.
        if source.suffix.lstrip('.').lower() == output_ext.lower():
            return source.read_bytes()

        format_map = {'mp4': 'mp4', 'avi': 'avi', 'mkv': 'matroska'}
        output_format = format_map[output_ext]

        # Lazy: PyAV pulls in the full FFmpeg shared libraries (tens of MB
        # of native code) at import time. Defer until we actually need to
        # remux so startup stays fast for users that never touch video.
        import av

        buffer = io.BytesIO()

        with (
            av.open(str(source), 'r') as input_container,
            av.open(buffer, 'w', format=output_format) as output_container,
        ):
            stream_map = {}
            for stream in input_container.streams:
                out_stream = output_container.add_stream_from_template(stream)
                stream_map[stream.index] = out_stream

            for packet in input_container.demux():
                if packet.dts is None:
                    continue
                packet.stream = stream_map[packet.stream.index]
                output_container.mux(packet)

        return buffer.getvalue()
