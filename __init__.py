"""hermes-lark-streaming — Feishu/Lark CardKit v2.0 streaming cards for Hermes Agent.

Architecture Overview
────────────────────
This plugin intercepts Hermes's message pipeline and renders real-time
streaming cards with typewriter effect, tool panels, reasoning panels,
and progressive card splitting.

Module Organization
──────────────────
Configuration:
  config/                     Sub-package
    __init__.py               Re-exports: Config
    reader.py                 Config reader (Hermes config.yaml)

Feishu API:
  feishu/                     Sub-package
    __init__.py               Re-exports: FeishuClient, FeishuAPIError, UnavailableGuard, etc.
    client.py                 FeishuClient (Lark SDK wrapper, transient retry)
    guard.py                  UnavailableGuard (message-deleted protection)

Flush Throttle:
  flush/                      Sub-package
    __init__.py               Re-exports: FlushController, constants
    controller.py             FlushController (throttle scheduler)

Core Controller:
  controller/                 Sub-package
    __init__.py               Re-exports: StreamCardController, CardSession, states
    core.py                   StreamCardController (singleton, manages sessions)
    mixin.py                  ControllerMixin (non-linear card API orchestration)
    linear_mixin.py           LinearControllerMixin (linear mode API orchestration)

Card Building:
  cardkit/                    Sub-package
    __init__.py               Re-exports from elements/cards/special
    elements.py               Primitive element builders (panels, footers, etc.)
    cards.py                  Card assemblers (streaming, complete, linear)
    special.py                Specialized cards (cron, gateway, clarify)
    md.py                     Markdown processing (downgrade, split, optimize)
    i18n.py                   i18n zh/en bilingual text mapping

State & Data:
  state/                      Sub-package
    __init__.py               Re-exports: CardSession, TextState, LinearState, etc.
    session.py                CardSession (per-message state)
    linear.py                 LinearState + Segment (flat segment management)
    linear_split.py           Element threshold, split estimation helpers
    text.py                   TextState (incremental text tracking)
    tooluse.py                ToolUseTracker (tool call visualization + redaction)

Runtime Patching:
  patching/                   Sub-package
    __init__.py               Entry point + shared state (apply_patches) + re-exports
    gateway.py                GatewayRunner wrappers, inject_time, cron
    callbacks.py              Callback wrapping (answer, thinking, tool, reasoning)
    adapter.py                FeishuAdapter interception (send, edit, reactions, clarify)
    hooks.py                  Hook functions (on_message_started, on_answer_delta, etc.)

Entry Points:
  plugin.py                   Plugin register/unregister (Hermes entry point)
  __main__.py                 CLI entry (status, verify, cleanup)

Logging
───────
Plugin logger name: ``hermes_lark_streaming``
  - Inherits level from Hermes root logger (set by config.yaml ``logging.level``)
  - Logs to ``agent.log`` (catch-all), NOT routed to ``gateway.log``
  - No explicit ``setLevel()`` — level follows Hermes config automatically

Streaming Mode
──────────────
This plugin uses CardKit v2.0 native streaming mode:
  - Creates cards with ``streaming_mode: True``
  - Uses ``cardkit_stream_element`` for text increment updates
  - Uses ``cardkit_batch_update`` for structural changes (add/modify elements)
  - Uses ``cardkit_close_streaming`` to exit streaming on completion

The "linear mode" is a content organization strategy on top of native
streaming — it renders segments (reasoning → tool → answer) in sequence
with independent element_ids, supporting card splitting and progressive
degradation when exceeding Feishu's 200-element limit.
"""

import logging
from pathlib import Path

_logger = logging.getLogger("hermes_lark_streaming")

_plugin_yaml = Path(__file__).resolve().parent / "plugin.yaml"
if _plugin_yaml.exists():
    for _line in _plugin_yaml.read_text(encoding="utf-8").splitlines():
        if _line.startswith("version:"):
            __version__ = _line.split(":", 1)[1].strip().strip('"').strip("'")
            break
    else:
        __version__ = "unknown"
        _logger.warning("plugin.yaml exists but no 'version:' field found")
else:
    __version__ = "unknown"
    _logger.warning("plugin.yaml not found at %s — installation may be broken", _plugin_yaml)

# Conditional import: relative import works when loaded by Hermes's
# plugin loader (spec_from_file_location with __package__ set);
# absolute import works when pytest imports this file directly
# (conftest.py pre-registers the package in sys.modules).
try:
    from .plugin import register
except ImportError:
    from hermes_lark_streaming.plugin import register  # type: ignore[no-redef]

__all__ = ["register", "__version__"]
