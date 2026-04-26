"""Maps incoming files to converter outputs.

When a file appears in the mount, we check each converter's INPUTS patterns.
For each match, we substitute the captured stem (group 1) into the converter's
OUTPUT_DIRS and OUTPUT_FILES templates to obtain the virtual paths to expose.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from convertfs.converter import Converter


@dataclass(frozen=True)
class OutputEntry:
    """A virtual entry that should appear because of a matching input."""

    path: Path
    is_dir: bool
    converter: Converter
    source_path: Path


def resolve_outputs(
    input_path: Path,
    converters: list[Converter],
) -> list[OutputEntry]:
    """Return the virtual outputs that should appear for `input_path`.

    `input_path` is the leaf name of the file that was created or moved in
    (mount-relative path; for v1 we only support files at the root).
    """
    name = input_path.name
    outputs: list[OutputEntry] = []

    for converter in converters:
        for pattern in converter.INPUTS:
            match = pattern.match(name)
            if match is None:
                continue
            stem = match.group(1) if match.groups() else name

            for raw_dir in converter.OUTPUT_DIRS:
                rendered = _render_template(raw_dir, stem)
                outputs.append(
                    OutputEntry(
                        path=rendered,
                        is_dir=True,
                        converter=converter,
                        source_path=input_path,
                    )
                )

            for raw_file in converter.OUTPUT_FILES:
                rendered = _render_template(raw_file, stem)
                outputs.append(
                    OutputEntry(
                        path=rendered,
                        is_dir=False,
                        converter=converter,
                        source_path=input_path,
                    )
                )
            # Don't try later patterns of the same converter: one match per
            # converter per file is enough.
            break

    return outputs


def _render_template(template: Path, stem: str) -> Path:
    """Substitute {} placeholders in each component with `stem`."""
    parts = [part.format(stem) for part in template.parts]
    return Path(*parts)
