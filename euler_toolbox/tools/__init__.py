"""Auto-discover and import all tool modules in the ``tools/`` package.

Each module is expected to use the ``@tool`` decorator at module level,
which registers the tool in the global registry as a side-effect of import.
"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path


def discover_tools() -> None:
    package_dir = Path(__file__).parent
    for _finder, module_name, _is_pkg in pkgutil.iter_modules([str(package_dir)]):
        if module_name.startswith("_"):
            continue
        importlib.import_module(f"{__package__}.{module_name}")
