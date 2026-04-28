from __future__ import annotations

import json
import tomllib
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
import yaml

from convertfs.converters.data import DataConverter


def _write(path: Path, content: str) -> Path:
	path.write_text(content, encoding='utf-8')
	return path


def test_data_json_to_yaml_round_trips_payload(tmp_path: Path, convert_bytes) -> None:
	source = _write(
		tmp_path / 'cfg.json',
		json.dumps({'name': 'alice', 'tags': ['x', 'y'], 'count': 3}),
	)

	output = convert_bytes(DataConverter(), source, tmp_path / 'cfg.yaml')

	parsed = yaml.safe_load(output.decode('utf-8'))
	assert parsed == {'name': 'alice', 'tags': ['x', 'y'], 'count': 3}


def test_data_yaml_to_json_preserves_unicode(tmp_path: Path, convert_bytes) -> None:
	source = _write(tmp_path / 'cfg.yaml', 'greeting: "Hello — 世界"\n')

	output = convert_bytes(DataConverter(), source, tmp_path / 'cfg.json')

	parsed = json.loads(output.decode('utf-8'))
	assert parsed == {'greeting': 'Hello — 世界'}


def test_data_toml_to_yaml_handles_nested_tables(tmp_path: Path, convert_bytes) -> None:
	source = _write(
		tmp_path / 'cfg.toml',
		'[server]\nhost = "localhost"\nport = 8080\n\n[server.tls]\nenabled = true\n',
	)

	output = convert_bytes(DataConverter(), source, tmp_path / 'cfg.yaml')

	parsed = yaml.safe_load(output.decode('utf-8'))
	assert parsed == {
		'server': {'host': 'localhost', 'port': 8080, 'tls': {'enabled': True}},
	}


def test_data_yaml_to_toml_outputs_valid_toml(tmp_path: Path, convert_bytes) -> None:
	source = _write(tmp_path / 'cfg.yaml', 'a: 1\nb: hello\nc:\n  d: 2\n')

	output = convert_bytes(DataConverter(), source, tmp_path / 'cfg.toml')

	# tomllib will accept the output as a top-level mapping.
	parsed = tomllib.loads(output.decode('utf-8'))
	assert parsed == {'a': 1, 'b': 'hello', 'c': {'d': 2}}


def test_data_toml_rejects_non_mapping_root(tmp_path: Path) -> None:
	# JSON allows an array at the top level; TOML doesn't. The converter
	# should refuse rather than producing garbage.
	source = _write(tmp_path / 'cfg.json', '[1, 2, 3]')

	with pytest.raises(ValueError, match='top-level mapping'):
		DataConverter().process(source, tmp_path / 'cfg.toml', tmp_path / "_dest.bin")


def test_data_xml_to_json_uses_repeated_elements_as_lists(tmp_path: Path, convert_bytes) -> None:
	source = _write(
		tmp_path / 'cfg.xml',
		'<?xml version="1.0"?>\n'
		'<root><tag>a</tag><tag>b</tag><tag>c</tag></root>\n',
	)

	output = convert_bytes(DataConverter(), source, tmp_path / 'cfg.json')

	parsed = json.loads(output.decode('utf-8'))
	assert parsed == {'tag': ['a', 'b', 'c']}


def test_data_xml_to_json_uses_item_tag_as_list(tmp_path: Path, convert_bytes) -> None:
	# When every child has the same tag "item", the converter treats the
	# list of items as a JSON array (rather than a dict keyed by tag).
	source = _write(
		tmp_path / 'cfg.xml',
		'<?xml version="1.0"?>\n<root><item>a</item><item>b</item></root>\n',
	)

	output = convert_bytes(DataConverter(), source, tmp_path / 'cfg.json')

	parsed = json.loads(output.decode('utf-8'))
	assert parsed == ['a', 'b']


def test_data_json_to_xml_emits_declaration_and_safe_root_name(
	tmp_path: Path,
	convert_bytes,
) -> None:
	# `99-bottles` has a leading digit, which isn't a valid XML name; the
	# converter should escape it (prefix with `_`).
	source = _write(tmp_path / '99-bottles.json', json.dumps({'verses': 99}))

	output = convert_bytes(DataConverter(), source, tmp_path / '99-bottles.xml')

	# It's wrapped with the XML declaration and uses the sanitized root.
	text = output.decode('utf-8')
	assert text.startswith('<?xml')
	root = ET.fromstring(text)
	assert root.tag.startswith('_')
	assert root.find('verses') is not None


def test_data_json_to_xml_handles_lists_as_item_elements(tmp_path: Path, convert_bytes) -> None:
	source = _write(tmp_path / 'cfg.json', json.dumps({'colors': ['red', 'blue']}))

	output = convert_bytes(DataConverter(), source, tmp_path / 'cfg.xml')

	root = ET.fromstring(output.decode('utf-8'))
	colors = root.find('colors')
	assert colors is not None
	assert [item.text for item in colors.findall('item')] == ['red', 'blue']


def test_data_yml_alias_resolves_to_yaml_loader(tmp_path: Path, convert_bytes) -> None:
	# The `.yml` extension should be treated as YAML.
	source = _write(tmp_path / 'cfg.yml', 'key: value\n')

	output = convert_bytes(DataConverter(), source, tmp_path / 'cfg.json')

	assert json.loads(output.decode('utf-8')) == {'key': 'value'}


def test_data_rejects_unsupported_extension(tmp_path: Path) -> None:
	source = _write(tmp_path / 'cfg.json', '{}')

	with pytest.raises(ValueError, match='Unsupported data conversion'):
		DataConverter().process(source, tmp_path / 'cfg.ini', tmp_path / "_dest.bin")
