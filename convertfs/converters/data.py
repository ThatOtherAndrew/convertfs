"""Data/config interconversion: json <-> yaml <-> toml <-> xml.

Round-trips between hierarchical config formats. The XML representation uses
a simple convention: dicts become elements with child elements named after
keys, lists become repeated elements named ``item``, scalars become element
text. Round-tripping XML through other formats is therefore lossy for
attributes and mixed content; this is fine for typical config payloads.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any, ClassVar
from xml.etree import ElementTree as ET

import tomli_w
import yaml
from typing_extensions import override

from convertfs.converter import Converter


class DataConverter(Converter):
    INPUTS = (re.compile(r'^(.*)\.(json|yaml|yml|toml|xml)$'),)
    OUTPUT_FILES = (
        Path('{}.json'),
        Path('{}.yaml'),
        Path('{}.yml'),
        Path('{}.toml'),
        Path('{}.xml'),
    )

    _LOADERS: ClassVar[dict[str, str]] = {
        'json': 'json',
        'yaml': 'yaml',
        'yml': 'yaml',
        'toml': 'toml',
        'xml': 'xml',
    }

    @override
    def process(self, source: Path, requested: Path) -> bytes:
        src_ext = source.suffix.lstrip('.').lower()
        out_ext = requested.suffix.lstrip('.').lower()

        src_kind = self._LOADERS.get(src_ext)
        out_kind = self._LOADERS.get(out_ext)
        if src_kind is None or out_kind is None:
            msg = f'Unsupported data conversion: {src_ext} -> {out_ext}'
            raise ValueError(msg)

        data = self._load(source, src_kind)
        return self._dump(data, out_kind, root_name=source.stem or 'root')

    @staticmethod
    def _load(source: Path, kind: str) -> Any:
        if kind == 'json':
            return json.loads(source.read_text(encoding='utf-8'))
        if kind == 'yaml':
            return yaml.safe_load(source.read_text(encoding='utf-8'))
        if kind == 'toml':
            with source.open('rb') as f:
                return tomllib.load(f)
        if kind == 'xml':
            tree = ET.parse(source)
            return _xml_element_to_data(tree.getroot())
        msg = f'Unknown data kind: {kind}'
        raise ValueError(msg)

    @staticmethod
    def _dump(data: Any, kind: str, root_name: str) -> bytes:
        if kind == 'json':
            return json.dumps(data, indent=2, ensure_ascii=False).encode('utf-8')
        if kind == 'yaml':
            return yaml.safe_dump(
                data, sort_keys=False, allow_unicode=True
            ).encode('utf-8')
        if kind == 'toml':
            if not isinstance(data, dict):
                msg = 'TOML output requires a top-level mapping'
                raise ValueError(msg)
            return tomli_w.dumps(_toml_sanitize(data)).encode('utf-8')
        if kind == 'xml':
            root = _data_to_xml_element(_xml_safe_name(root_name), data)
            ET.indent(root, space='  ')
            return ET.tostring(root, encoding='utf-8', xml_declaration=True)
        msg = f'Unknown data kind: {kind}'
        raise ValueError(msg)


def _xml_safe_name(name: str) -> str:
    safe = re.sub(r'[^A-Za-z0-9_.-]', '_', name)
    if not safe or not (safe[0].isalpha() or safe[0] == '_'):
        safe = f'_{safe}'
    return safe


def _data_to_xml_element(tag: str, value: Any) -> ET.Element:
    elem = ET.Element(tag)
    if isinstance(value, dict):
        for k, v in value.items():
            elem.append(_data_to_xml_element(_xml_safe_name(str(k)), v))
    elif isinstance(value, list):
        for item in value:
            elem.append(_data_to_xml_element('item', item))
    elif value is None:
        pass
    else:
        elem.text = str(value)
    return elem


def _xml_element_to_data(elem: ET.Element) -> Any:
    children = list(elem)
    if not children:
        text = (elem.text or '').strip()
        return text if text else None

    if all(child.tag == 'item' for child in children):
        return [_xml_element_to_data(child) for child in children]

    result: dict[str, Any] = {}
    for child in children:
        value = _xml_element_to_data(child)
        if child.tag in result:
            existing = result[child.tag]
            if isinstance(existing, list):
                existing.append(value)
            else:
                result[child.tag] = [existing, value]
        else:
            result[child.tag] = value
    return result


def _toml_sanitize(value: Any) -> Any:
    """tomli_w refuses None; replace with empty string for round-trip."""
    if isinstance(value, dict):
        return {k: _toml_sanitize(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_toml_sanitize(v) for v in value if v is not None]
    return value
