"""Tests for version reading logic in __init__.py and setup.py.

Covers:
  - hermes_lark_streaming.__version__ reads "0.10.1" from plugin.yaml
  - Fallback to "unknown" when plugin.yaml is missing
  - Fallback to "unknown" when plugin.yaml has no version: field
  - setup.py raises FileNotFoundError when plugin.yaml is missing
  - setup.py raises ValueError when plugin.yaml has no version: field
  - setup.py successfully extracts version and calls setup()
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import hermes_lark_streaming

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SETUP_PY = Path(__file__).resolve().parent.parent / "setup.py"


def _is_plugin_yaml_path(p: Path) -> bool:
    """Return True if *p* points to a file named ``plugin.yaml``.

    This is used to scope global ``Path`` mocks so they only affect the
    plugin.yaml lookups performed by the modules under test.
    """
    return p.name == "plugin.yaml"


# ---------------------------------------------------------------------------
# __init__.py  –  hermes_lark_streaming.__version__
# ---------------------------------------------------------------------------


class TestInitVersion:
    """Tests for ``hermes_lark_streaming.__version__``."""

    def test_version_reads_correct_value(self) -> None:
        """__version__ should be '0.10.2' as defined in plugin.yaml."""
        assert hermes_lark_streaming.__version__ == "0.10.2"

    def test_version_fallback_when_plugin_yaml_missing(self) -> None:
        """__version__ falls back to 'unknown' when plugin.yaml does not exist."""
        original_exists = Path.exists

        def mock_exists(self: Path) -> bool:
            if _is_plugin_yaml_path(self):
                return False
            return original_exists(self)

        try:
            with patch.object(Path, "exists", mock_exists):
                importlib.reload(hermes_lark_streaming)
            assert hermes_lark_streaming.__version__ == "unknown"
        finally:
            # Restore the module to its correct state regardless of test outcome
            importlib.reload(hermes_lark_streaming)

    def test_version_fallback_when_no_version_field(self) -> None:
        """__version__ falls back to 'unknown' when plugin.yaml has no version: field."""
        original_exists = Path.exists
        original_read_text = Path.read_text

        def mock_exists(self: Path) -> bool:
            if _is_plugin_yaml_path(self):
                return True
            return original_exists(self)

        def mock_read_text(self: Path, *args: object, **kwargs: object) -> str:
            if _is_plugin_yaml_path(self):
                return "name: test\ndescription: no version here\n"
            return original_read_text(self, *args, **kwargs)

        try:
            with patch.object(Path, "exists", mock_exists), \
                 patch.object(Path, "read_text", mock_read_text):
                importlib.reload(hermes_lark_streaming)
            assert hermes_lark_streaming.__version__ == "unknown"
        finally:
            importlib.reload(hermes_lark_streaming)


# ---------------------------------------------------------------------------
# setup.py  –  version extraction & error handling
# ---------------------------------------------------------------------------
# setup.py is difficult to test directly because it calls setup() at module
# level.  We load it via importlib.util with setuptools.setup patched out,
# and scope Path mocks to only affect plugin.yaml lookups.


class TestSetupVersion:
    """Tests for setup.py version-reading logic."""

    def _load_setup(self) -> MagicMock:
        """Execute setup.py with ``setuptools.setup`` replaced by a mock.

        Returns the mock so callers can assert on how setup() was called.
        """
        mock_setup = MagicMock()
        with patch("setuptools.setup", mock_setup):
            spec = importlib.util.spec_from_file_location(
                "_test_setup_version", _SETUP_PY
            )
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        # Clean up in case the module was registered
        sys.modules.pop("_test_setup_version", None)
        return mock_setup

    # -- error cases --------------------------------------------------------

    def test_file_not_found_error_when_plugin_yaml_missing(self) -> None:
        """FileNotFoundError is raised when plugin.yaml does not exist."""
        original_exists = Path.exists

        def mock_exists(self: Path) -> bool:
            if _is_plugin_yaml_path(self):
                return False
            return original_exists(self)

        with patch.object(Path, "exists", mock_exists):
            with pytest.raises(FileNotFoundError, match="plugin.yaml not found"):
                self._load_setup()

    def test_value_error_when_no_version_field(self) -> None:
        """ValueError is raised when plugin.yaml exists but has no version: field."""
        original_exists = Path.exists
        original_read_text = Path.read_text

        def mock_exists(self: Path) -> bool:
            if _is_plugin_yaml_path(self):
                return True
            return original_exists(self)

        def mock_read_text(self: Path, *args: object, **kwargs: object) -> str:
            if _is_plugin_yaml_path(self):
                return "name: test\ndescription: no version here\n"
            return original_read_text(self, *args, **kwargs)

        with patch.object(Path, "exists", mock_exists), \
             patch.object(Path, "read_text", mock_read_text):
            with pytest.raises(ValueError, match="No 'version:' field found"):
                self._load_setup()

    # -- success case -------------------------------------------------------

    def test_successful_version_extraction(self) -> None:
        """setup() is called with the version extracted from plugin.yaml."""
        original_exists = Path.exists
        original_read_text = Path.read_text

        def mock_exists(self: Path) -> bool:
            if _is_plugin_yaml_path(self):
                return True
            return original_exists(self)

        def mock_read_text(self: Path, *args: object, **kwargs: object) -> str:
            if _is_plugin_yaml_path(self):
                return 'name: test\nversion: "1.2.3"\ndescription: test\n'
            return original_read_text(self, *args, **kwargs)

        with patch.object(Path, "exists", mock_exists), \
             patch.object(Path, "read_text", mock_read_text):
            mock_setup = self._load_setup()

        mock_setup.assert_called_once_with(version="1.2.3")
