from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from convertfs.converters.markitdown import MarkItDownDocuments


def _write_minimal_xlsx(path: Path, text: str) -> None:
	workbook = Workbook()
	sheet = workbook.active
	sheet['A1'] = text
	workbook.save(path)


def test_markitdown_documents_converts_xlsx_to_markdown(tmp_path: Path) -> None:
	source = tmp_path / 'hello.xlsx'
	_write_minimal_xlsx(source, 'Hello World')

	result = MarkItDownDocuments().process(source, tmp_path / 'hello.md').decode('utf-8')

	assert 'Hello World' in result
	assert result.strip()