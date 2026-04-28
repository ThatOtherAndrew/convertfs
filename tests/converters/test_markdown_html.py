from __future__ import annotations

from pathlib import Path

from convertfs.converters.markdown_html import MarkdownToHtml


def _convert(tmp_path: Path, body: str, *, name: str = 'doc') -> str:
	source = tmp_path / f'{name}.md'
	source.write_text(body, encoding='utf-8')
	dest = tmp_path / f'{name}.html'
	MarkdownToHtml().process(source, dest, dest)
	return dest.read_text(encoding='utf-8')


def test_markdown_to_html_emits_doctype_and_charset(tmp_path: Path) -> None:
	html = _convert(tmp_path, '# Hello')

	assert html.startswith('<!DOCTYPE html>')
	assert '<meta charset="utf-8">' in html
	assert '<html lang="en">' in html


def test_markdown_to_html_uses_source_stem_as_title(tmp_path: Path) -> None:
	html = _convert(tmp_path, '# Heading', name='my-document')

	assert '<title>my-document</title>' in html


def test_markdown_to_html_renders_inline_formatting(tmp_path: Path) -> None:
	html = _convert(tmp_path, 'Some **bold** and *italic* text.')

	assert '<strong>bold</strong>' in html
	assert '<em>italic</em>' in html


def test_markdown_to_html_renders_headings(tmp_path: Path) -> None:
	html = _convert(tmp_path, '# H1\n\n## H2\n\n### H3\n')

	assert '<h1' in html and 'H1</h1>' in html
	assert '<h2' in html and 'H2</h2>' in html
	assert '<h3' in html and 'H3</h3>' in html


def test_markdown_to_html_renders_lists(tmp_path: Path) -> None:
	html = _convert(tmp_path, '- one\n- two\n- three\n')

	assert '<ul>' in html
	assert '<li>one</li>' in html
	assert '<li>two</li>' in html
	assert '<li>three</li>' in html


def test_markdown_to_html_renders_fenced_code(tmp_path: Path) -> None:
	# fenced_code is in the converter's extension list; backtick fences
	# must be parsed (a plain markdown.markdown without that extension
	# would emit literal backticks).
	html = _convert(tmp_path, '```\nhello = 42\n```\n')

	assert '<pre>' in html or '<code>' in html
	assert 'hello = 42' in html


def test_markdown_to_html_renders_tables(tmp_path: Path) -> None:
	# tables is enabled via the extensions tuple; a default markdown
	# install without it would render the input as a plain paragraph.
	body = (
		'| col1 | col2 |\n'
		'|------|------|\n'
		'| a    | b    |\n'
	)
	html = _convert(tmp_path, body)

	assert '<table>' in html
	assert '<th>col1</th>' in html
	assert '<td>a</td>' in html


def test_markdown_to_html_smart_quotes(tmp_path: Path) -> None:
	# smarty extension turns straight quotes into curly entities.
	html = _convert(tmp_path, "It's a 'test'")

	# Either entity-encoded or literal smart punctuation is acceptable.
	assert any(token in html for token in ('&rsquo;', '’', '&lsquo;', '‘'))


def test_markdown_to_html_round_trip_preserves_unicode(tmp_path: Path) -> None:
	html = _convert(tmp_path, 'Hello — 世界')

	assert '世界' in html
	assert '—' in html
