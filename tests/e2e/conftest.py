"""E2E test fixtures — single runner, auto mock/real switching.

v1.1.0: The E2ETestRunner automatically uses real Feishu API when
FEISHU_E2E_APP_ID + FEISHU_E2E_APP_SECRET + FEISHU_E2E_CHAT_ID +
FEISHU_E2E_OPEN_ID are all set. Otherwise uses mock server.

v1.1.1: chat_id 和 open_id 都必填（分别测群聊和私聊场景）。
v1.1.1: 真飞书模式下测试间加 1 秒延迟，避免触发飞书 API 频率限制
        （CardKit 流式模式豁免 QPS，但 create/send/close 计入 1000/分 & 50/秒）。

Test code is identical in both modes — only the underlying client differs.
"""

from __future__ import annotations

import asyncio
import os
import pytest

from .framework import E2ETestRunner


def _has_real_feishu_creds() -> bool:
    """Check if real Feishu credentials are available.

    v1.1.1: 4 variables required: app_id, app_secret, chat_id, open_id.
    chat_id 和 open_id 都需要（分别测群聊和私聊）。
    """
    return bool(
        os.environ.get("FEISHU_E2E_APP_ID")
        and os.environ.get("FEISHU_E2E_APP_SECRET")
        and os.environ.get("FEISHU_E2E_CHAT_ID")
        and os.environ.get("FEISHU_E2E_OPEN_ID")
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


@pytest.fixture(autouse=True)
async def _rate_limit_guard():
    """v1.1.1: 真飞书模式下测试间加延迟，避免触发飞书 API 频率限制.

    飞书 CardKit API 限制：
    - API 级：1000 次/分 & 50 次/秒（流式模式豁免）
    - 单卡片级：10 次/秒
    - create/send/close 计入配额

    v1.1.1: 延迟从 1 秒加到 2 秒，确保不触发限流。
    mock 模式不需要延迟。
    """
    yield
    if _has_real_feishu_creds():
        await asyncio.sleep(2.0)


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
