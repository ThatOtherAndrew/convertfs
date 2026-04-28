import re
from pathlib import Path

from markitdown import MarkItDown
from typing_extensions import override

from convertfs.converter import Converter


class MarkItDownDocuments(Converter):
    INPUTS = (
        re.compile(r'^(.*)\.pdf$'),
        re.compile(r'^(.*)\.docx$'),
        re.compile(r'^(.*)\.pptx$'),
        re.compile(r'^(.*)\.xlsx$'),
        re.compile(r'^(.*)\.xls$'),
    )

    OUTPUT_FILES = (Path('{}.md'),)

    md = MarkItDown(enable_plugins=True)

    @override
    def process(self, source: Path, requested: Path, dest: Path) -> None:
        text = self.md.convert(str(source)).text_content
        dest.write_text(text, encoding='utf-8')
