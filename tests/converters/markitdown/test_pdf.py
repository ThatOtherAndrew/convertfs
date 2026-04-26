from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from convertfs.converters.markitdown import MarkItDownDocuments


def _write_minimal_pdf(path: Path, text: str) -> None:
	pdf = canvas.Canvas(str(path), pagesize=letter)
	pdf.drawString(72, 720, text)
	pdf.showPage()
	pdf.save()


def test_markitdown_documents_converts_pdf_to_markdown(tmp_path: Path) -> None:
	source = tmp_path / 'hello.pdf'
	_write_minimal_pdf(source, 'Hello World')

	result = MarkItDownDocuments().process(source, tmp_path / 'hello.md').decode('utf-8')

	assert 'Hello World' in result
	assert result.strip()