"""aiduPOP Studio (Visual Card Studio) package.

Optional visual configuration tool for open-source users. Not used by the
production gateway.
"""

from .server import main, run_studio_server

__all__ = ["main", "run_studio_server"]
