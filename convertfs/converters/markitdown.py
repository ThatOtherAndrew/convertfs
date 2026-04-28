import re
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from typing_extensions import override

from convertfs.converter import Converter

if TYPE_CHECKING:
    from markitdown import MarkItDown


class MarkItDownDocuments(Converter):
    INPUTS = (
        re.compile(r'^(.*)\.pdf$'),
        re.compile(r'^(.*)\.docx$'),
        re.compile(r'^(.*)\.pptx$'),
        re.compile(r'^(.*)\.xlsx$'),
        re.compile(r'^(.*)\.xls$'),
    )

    OUTPUT_FILES = (Path('{}.md'),)

    # MarkItDown is heavy to construct (loads many subprocessor plugins);
    # build it on first use rather than at class-definition time so just
    # importing this module doesn't drag the whole stack in.
    _md: ClassVar['MarkItDown | None'] = None

    @classmethod
    def _client(cls) -> 'MarkItDown':
        if cls._md is None:
            from markitdown import MarkItDown

            cls._md = MarkItDown(enable_plugins=True)
        return cls._md

    @override
    def process(self, source: Path, requested: Path, dest: Path) -> None:
        text = self._client().convert(str(source)).text_content
        dest.write_text(text, encoding='utf-8')
