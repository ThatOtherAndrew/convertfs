from pathlib import Path
import re
from markitdown import MarkItDown

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

	def process(self, source: Path, requested: Path) -> bytes:
		return self.md.convert(str(source)).text_content.encode('utf-8')