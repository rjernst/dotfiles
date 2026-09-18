"""Shared pytest fixtures for the ralph test suite."""

import pytest


@pytest.fixture(autouse=True)
def isolate_sandbox_state_dir(tmp_path_factory, monkeypatch):
    """Redirect SANDBOX_STATE_DIR at the real home into a temp directory.

    ralph.runtime resolves SANDBOX_STATE_DIR at import time, so a
    monkeypatched HOME does not reach it.  Without this, any test that walks
    ensure_sandbox/cleanup_sandbox leaves stray timestamp files in the
    developer's ~/.ralph/sandbox-used.  Tests that need to inspect the files
    patch the same name themselves; that patch nests inside this one.
    """
    state_dir = tmp_path_factory.mktemp("sandbox-used")
    monkeypatch.setattr("ralph.runtime.SANDBOX_STATE_DIR", str(state_dir))
    yield str(state_dir)
