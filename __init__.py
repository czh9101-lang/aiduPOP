"""hermes-lark-streaming — Feishu/Lark CardKit v2.0 streaming cards for Hermes Agent.

Bridge module: Hermes plugin loader discovers this package from the repo root,
so we re-export the public API from the actual sub-package.
"""

import importlib
import os
import sys

# The actual Python package lives in hermes_lark_streaming/ sub-directory.
# Add the repo root to sys.path so that `import hermes_lark_streaming` finds
# the real package (not this bridge file).
_repo_root = os.path.dirname(os.path.abspath(__file__))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

# If this bridge module is registered as 'hermes_lark_streaming' in
# sys.modules (Hermes spec_from_file_location scenario), temporarily
# remove it so the real package can be imported.
_bridge_key = "hermes_lark_streaming"
_saved = sys.modules.pop(_bridge_key, None)

try:
    _pkg = importlib.import_module("hermes_lark_streaming")
    register = _pkg.register
    __version__ = _pkg.__version__
finally:
    # Restore bridge module registration if it was there before
    if _saved is not None:
        sys.modules[_bridge_key] = _saved
    else:
        sys.modules.pop(_bridge_key, None)

__all__ = ["register", "__version__"]
