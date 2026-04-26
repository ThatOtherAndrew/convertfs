"""Dynamic discovery of converter implementations.

Modules in this package that define non-abstract subclasses of
:class:`convertfs.converter.Converter` are picked up automatically by
:func:`discover_converters`. There is no registration step beyond putting
the file here.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from typing import TYPE_CHECKING

from convertfs.converter import Converter

if TYPE_CHECKING:
    from types import ModuleType


_logger = logging.getLogger(__name__)


def discover_converters() -> list[Converter]:
    """Import every module in this package and instantiate each Converter subclass.

    Modules that fail to import (e.g. due to a missing optional dependency)
    are skipped with a warning rather than aborting startup.
    """
    instances: list[Converter] = []
    seen: set[type[Converter]] = set()

    for module_info in pkgutil.iter_modules(__path__, prefix=f'{__name__}.'):
        if module_info.ispkg:
            continue
        try:
            module = importlib.import_module(module_info.name)
        except Exception:
            _logger.warning(
                'failed to import converter module %s', module_info.name,
                exc_info=True,
            )
            continue

        for cls in _converter_classes_in(module):
            if cls in seen:
                continue
            seen.add(cls)
            try:
                instances.append(cls())
            except Exception:
                _logger.warning(
                    'failed to instantiate %s.%s',
                    module.__name__, cls.__name__,
                    exc_info=True,
                )

    instances.sort(key=lambda c: type(c).__name__)
    _logger.info(
        'discovered %d converters: %s',
        len(instances),
        ', '.join(type(c).__name__ for c in instances) or '(none)',
    )
    return instances


def _converter_classes_in(module: ModuleType) -> list[type[Converter]]:
    """Return non-abstract Converter subclasses defined in `module`."""
    found: list[type[Converter]] = []
    for _, obj in inspect.getmembers(module, inspect.isclass):
        if obj is Converter or not issubclass(obj, Converter):
            continue
        # Only pick up classes actually defined in this module (not
        # imported into it from elsewhere).
        if obj.__module__ != module.__name__:
            continue
        if inspect.isabstract(obj):
            continue
        found.append(obj)
    return found
