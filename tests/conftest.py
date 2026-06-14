"""Test-suite local fixtures.

Event loop cleanup: tests that create ``asyncio.new_event_loop()`` via
helpers (e.g. ``_make_session``) must register the loop in
``_loops_to_cleanup`` so the autouse fixture below can drain pending
async generators and close the loop after each test — preventing
ResourceWarning / "coroutine never awaited" warnings.
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.fixture(autouse=True)
def _cleanup_event_loops():
    """Close any event loops that test helpers created but did not clean up."""
    yield
    # Import late: the list is populated at test execution time.
    try:
        from tests.test_controller import _loops_to_cleanup

        loops = list(_loops_to_cleanup)
        _loops_to_cleanup.clear()
    except ImportError:
        loops = []

    for loop in loops:
        if loop.is_closed() or loop.is_running():
            continue
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        finally:
            loop.close()
