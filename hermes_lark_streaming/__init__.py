"""hermes-lark-streaming — Feishu/Lark CardKit v2.0 streaming cards for Hermes Agent.

Architecture Overview
────────────────────
This plugin intercepts Hermes's message pipeline and renders real-time
streaming cards with typewriter effect, tool panels, reasoning panels,
and progressive card splitting.

Module Organization
──────────────────
Core Controller:
  controller.py              StreamCardController (singleton, manages sessions)
  controller_mixin.py         ControllerMixin (non-linear card API orchestration)
  controller_linear_mixin.py  LinearControllerMixin (linear mode API orchestration)
  session.py                  CardSession (per-message state)
  flush.py                    FlushController (throttle scheduler)
  patch.py                    Hook functions (on_message_started, on_answer_delta, etc.)

Card Building:
  cardkit.py                  Facade — re-exports from cardkit_*
  cardkit_elements.py         Primitive element builders (panels, footers, etc.)
  cardkit_cards.py            Card assemblers (streaming, complete, linear)
  cardkit_special.py          Specialized cards (cron, gateway, clarify)
  cardkit_md.py               Markdown processing (downgrade, split, optimize)
  cardkit_i18n.py             i18n zh/en bilingual text mapping

Feishu API:
  feishu.py                   FeishuClient (Lark SDK wrapper, transient retry)
  unavailable_guard.py        UnavailableGuard (message-deleted protection)
  image.py                    ImageResolver (async image upload)

State & Data:
  linear.py                   LinearState + Segment (flat segment management)
  linear_split.py             Element threshold, split estimation helpers
  text.py                     TextState (incremental text tracking)
  tooluse.py                  ToolUseTracker (tool call visualization + redaction)
  config.py                   Config reader (Hermes config.yaml)

Monkey Patching:
  monkey_patch.py              Entry point + shared state (apply_patches)
  monkey_patch_gateway.py      GatewayRunner wrappers, inject_time, cron
  monkey_patch_callbacks.py    Callback wrapping (answer, thinking, tool, reasoning)
  monkey_patch_adapter.py      FeishuAdapter interception (send, edit, reactions, clarify)

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

_plugin_yaml = Path(__file__).resolve().parent.parent / "plugin.yaml"
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

from .plugin import register

__all__ = ["register", "__version__"]
