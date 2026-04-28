import re
from pathlib import Path

import av
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
    def process(self, source: Path, requested: Path, dest: Path) -> None:
        output_ext = requested.suffix.lstrip('.')

        format_map = {'mp4': 'mp4', 'avi': 'avi', 'mkv': 'matroska'}
        output_format = format_map[output_ext]

        with (
            av.open(str(source), 'r') as input_container,
            av.open(str(dest), 'w', format=output_format) as output_container,
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
