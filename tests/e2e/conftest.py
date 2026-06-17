"""E2E test fixtures — single runner, auto mock/real switching.

v1.1.0: The E2ETestRunner automatically uses real Feishu API when
FEISHU_E2E_APP_ID + FEISHU_E2E_APP_SECRET + FEISHU_E2E_CHAT_ID +
FEISHU_E2E_MESSAGE_ID are all set. Otherwise uses mock server.

Test code is identical in both modes — only the underlying client differs.
"""

from __future__ import annotations

import os
import pytest

from .framework import E2ETestRunner


def _has_real_feishu_creds() -> bool:
    """Check if real Feishu credentials are available.

    Only 3 variables required: app_id, app_secret, chat_id.
    message_id is obtained automatically by sending a text message.
    """
    return bool(
        os.environ.get("FEISHU_E2E_APP_ID")
        and os.environ.get("FEISHU_E2E_APP_SECRET")
        and os.environ.get("FEISHU_E2E_CHAT_ID")
    )


# ── Single fixture — auto mock/real ──

@pytest.fixture
async def runner():
    """E2E test runner.

    Automatically selects mode:
    - Real Feishu API if FEISHU_E2E_* env vars are set
    - Mock server otherwise

    Tests use this fixture the same way regardless of mode.
    """
    r = E2ETestRunner()
    await r.setup()
    if r.is_real_mode:
        pytest.mark.real_feishu  # noqa: B018 — marker for reporting
    yield r
    await r.teardown()


# ── Pytest configuration ──

def pytest_configure(config):
    """Register custom markers and log mode."""
    config.addinivalue_line(
        "markers",
        "real_feishu: test runs against real Feishu API (auto-detected from env vars)",
    )
    if _has_real_feishu_creds():
        config._hermes_e2e_mode = "real"
    else:
        config._hermes_e2e_mode = "mock"


def pytest_report_header(config):
    """Add e2e mode to pytest header output."""
    mode = getattr(config, "_hermes_e2e_mode", "unknown")
    return [f"hermes-lark-streaming e2e mode: {mode}"]
