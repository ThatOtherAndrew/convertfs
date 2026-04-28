import re
from pathlib import Path

import markdown
from typing_extensions import override

from convertfs.converter import Converter


class MarkdownToHtml(Converter):
    INPUTS = (re.compile(r'^(.*)\.(md|markdown)$'),)
    OUTPUT_FILES = (Path('{}.html'),)

    _EXTENSIONS = ('extra', 'sane_lists', 'smarty', 'toc', 'tables', 'fenced_code')

    @override
    def process(self, source: Path, requested: Path) -> bytes:
        text = source.read_text(encoding='utf-8')
        body = markdown.markdown(text, extensions=list(self._EXTENSIONS))
        title = source.stem
        html = (
            '<!DOCTYPE html>\n'
            '<html lang="en">\n'
            '<head>\n'
            '<meta charset="utf-8">\n'
            f'<title>{title}</title>\n'
            '</head>\n'
            '<body>\n'
            f'{body}\n'
            '</body>\n'
            '</html>\n'
        )
        return html.encode('utf-8')
