"""v2.6.0 tests — token 吊销运行时恢复 + clarify 拆卡.

借鉴来源：
- token 吊销恢复：fry-cards (techysy/hermes-fry-cards) — 99991663 清 SDK token
  缓存后重试一次，无需重启网关。
- clarify 拆卡：Cheerwhy PR #99 — clarify 前后输出分卡呈现（本实现复用自有
  续写链路 _reactivate_session_for_continuation，忽略 _streaming_closed 守卫）。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes_lark_streaming.feishu.client import (
    FeishuAPIError,
    FeishuClient,
    FeishuClientConfig,
    TOKEN_INVALIDATED,
)
from hermes_lark_streaming.cardkit.elements import _T  # noqa: F401 — 确保包导入路径健康


# ── token 吊销运行时恢复 ──


def _make_client() -> FeishuClient:
    cfg = FeishuClientConfig(app_id="cli_test_app", app_secret="secret")
    with patch.object(FeishuClient, "__init__", lambda self, c: None):
        client = FeishuClient(cfg)
    client.config = cfg
    return client


@pytest.mark.asyncio
async def test_retry_recovers_from_token_invalidation() -> None:
    """99991663 → 清缓存 → 立即重试成功（单次恢复）。"""
    client = _make_client()
    calls = {"n": 0}

    async def _do():
        calls["n"] += 1
        if calls["n"] == 1:
            raise FeishuAPIError("99991663 invalid access token", code=TOKEN_INVALIDATED)
        return "ok"

    with patch.object(client, "invalidate_token_cache") as mock_invalidate:
        result = await client._retry_transient("cardkit_update", _do)

    assert result == "ok"
    assert calls["n"] == 2
    mock_invalidate.assert_called_once()


@pytest.mark.asyncio
async def test_token_rescue_only_once() -> None:
    """第二次 99991663 不再触发恢复（token_rescue_used 守卫）。"""
    client = _make_client()

    async def _do():
        raise FeishuAPIError("99991663 invalid access token", code=TOKEN_INVALIDATED)

    with patch.object(client, "invalidate_token_cache") as mock_invalidate:
        with pytest.raises(FeishuAPIError):
            await client._retry_transient("cardkit_update", _do)

    assert mock_invalidate.call_count == 1


@pytest.mark.asyncio
async def test_token_rescue_not_triggered_for_other_codes() -> None:
    """非 99991663 错误不走吊销恢复路径。"""
    client = _make_client()

    async def _do():
        raise FeishuAPIError("card schema error", code=300315)

    with patch.object(client, "invalidate_token_cache") as mock_invalidate:
        with pytest.raises(FeishuAPIError):
            await client._retry_transient("cardkit_update", _do)

    mock_invalidate.assert_not_called()


def test_invalidate_token_cache_clears_sdk_localcache() -> None:
    """invalidate_token_cache 移除 SDK LocalCache 中本 app 的 tenant token."""
    from lark_oapi.core.token.manager import TokenManager

    client = _make_client()
    cache = getattr(TokenManager, "cache", None)
    assert cache is not None, "SDK TokenManager.cache 缺失，吊销恢复方案不成立"
    inner = getattr(cache, "cache", None)
    assert isinstance(inner, dict), "SDK LocalCache 内部结构非 dict，需适配"

    key = f"self_tenant_token:{client.config.app_id}"
    inner[key] = "stale-token-value"

    client.invalidate_token_cache()

    assert key not in inner


def test_invalidate_token_cache_silent_on_missing_cache() -> None:
    """TokenManager.cache 缺失时静默返回（防御性）。"""
    client = _make_client()
    with patch("lark_oapi.core.token.manager.TokenManager", MagicMock(spec=[])):
        client.invalidate_token_cache()  # 不应抛异常


# ── clarify 拆卡 ──


class _FakeController:
    """maybe_split_for_clarify 的行为测试用轻量 stub（绕开完整控制器依赖）。"""

    def __init__(self, sessions: dict, reactivate_result, continuation_map: dict | None = None):
        self._sessions = sessions
        self._sessions_lock = __import__("threading").RLock()
        self._reactivate_result = reactivate_result
        self._continuation_map = continuation_map or {}
        self.reactivate_calls: list = []
        self.flushed: list = []

    def _resolve_continuation_id(self, mid):
        return self._continuation_map.get(mid)

    def _register_continuation(self, old, new):
        self._continuation_map[old] = new

    def _get_loop(self):
        return asyncio.get_event_loop()

    def _reactivate_session_for_continuation(self, stale):
        self.reactivate_calls.append(stale)
        return self._reactivate_result

    def _do_unified_flush(self, session):
        self.flushed.append(session)
        return asyncio.sleep(0)


def _make_session(mid: str, chat_id: str, *, terminal: bool = False, streaming_closed: bool = False, dirty: bool = False):
    sess = MagicMock()
    sess.message_id = mid
    sess.chat_id = chat_id
    sess.is_terminal_phase = terminal
    sess._streaming_closed = streaming_closed
    sess.unified_state = MagicMock()
    sess.unified_state.has_dirty = dirty
    sess._loop = asyncio.new_event_loop()
    return sess


def test_maybe_split_for_clarify_ignores_streaming_closed_guard() -> None:
    """clarify 场景流式仍健康（_streaming_closed=False）也强制切卡 — 与普通续写的关键区别。"""
    from hermes_lark_streaming.controller.core import StreamCardController

    sess = _make_session("om_active1234567890", "oc_chat1")
    ctrl = StreamCardController.maybe_split_for_clarify.__get__(
        _FakeController({"om_active1234567890": sess}, reactivate_result=MagicMock(message_id="om_new"))
    )
    result = ctrl("oc_chat1")
    assert result == "om_new"


def test_maybe_split_for_clarify_no_active_session() -> None:
    """无活跃 session（终态）→ 返回 None 不切卡。"""
    from hermes_lark_streaming.controller.core import StreamCardController

    sess = _make_session("om_done12345678901", "oc_chat1", terminal=True)
    ctrl = StreamCardController.maybe_split_for_clarify.__get__(
        _FakeController({"om_done12345678901": sess}, reactivate_result=None)
    )
    assert ctrl("oc_chat1") is None
    assert ctrl("oc_unknown") is None


def test_maybe_split_for_clarify_idempotent() -> None:
    """同一 session 已触发过切卡 → 幂等返回 existing。"""
    from hermes_lark_streaming.controller.core import StreamCardController

    sess = _make_session("om_active1234567890", "oc_chat1")
    fake = _FakeController(
        {"om_active1234567890": sess},
        reactivate_result=MagicMock(message_id="om_new"),
        continuation_map={"om_active1234567890": "om_new"},
    )
    ctrl = StreamCardController.maybe_split_for_clarify.__get__(fake)
    assert ctrl("oc_chat1") == "om_new"
    assert fake.reactivate_calls == []  # 未再触发重激活
