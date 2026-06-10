"""Pytest configuration — register repo root as hermes_lark_streaming package.

The plugin code lives at the repo root (no nested hermes_lark_streaming/
subdirectory).  The repo directory is named ``hermes-lark-streaming`` (hyphens)
but the Python package is ``hermes_lark_streaming`` (underscores).  This
conftest bridges that gap by pre-registering the package in ``sys.modules``
before any test module is collected, mirroring what Hermes's
``_load_directory_module`` does at runtime.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent

# Prevent pytest from trying to collect/import root-level .py files as tests.
# The root __init__.py is a package init with relative imports that require
# the package to be registered in sys.modules first — which conftest.py does.
collect_ignore = [
    str(_REPO_ROOT / "__init__.py"),
    str(_REPO_ROOT / "__main__.py"),
    str(_REPO_ROOT / "setup.py"),
    str(_REPO_ROOT / "scripts"),
]


def _register_repo_as_package() -> None:
    """Ensure ``import hermes_lark_streaming`` resolves to the repo root.

    Uses ``importlib.util.spec_from_file_location`` with
    ``submodule_search_locations`` set to the repo root — identical to
    how Hermes's plugin loader discovers and loads directory plugins.
    """
    if "hermes_lark_streaming" in sys.modules:
        return

    init_file = _REPO_ROOT / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        "hermes_lark_streaming",
        str(init_file),
        submodule_search_locations=[str(_REPO_ROOT)],
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot create module spec for {init_file}")

    module = importlib.util.module_from_spec(spec)
    module.__package__ = "hermes_lark_streaming"
    module.__path__ = [str(_REPO_ROOT)]
    sys.modules["hermes_lark_streaming"] = module
    spec.loader.exec_module(module)


# Register eagerly so that test modules can ``import hermes_lark_streaming``
# at their top-level.
_register_repo_as_package()
