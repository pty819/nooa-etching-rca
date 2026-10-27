"""Platform-specific process-limit policy without changing the test runner's limits."""

from unittest.mock import Mock

import pytest

from factor_demo import sandboxing


@pytest.mark.parametrize(
    ("platform", "configured", "expected"),
    [("darwin", None, None), ("darwin", "128", 128), ("linux", None, 512)],
)
def test_nproc_platform_policy(monkeypatch, platform, configured, expected):
    import resource

    monkeypatch.setattr(sandboxing.sys, "platform", platform)
    monkeypatch.delenv("FACTOR_CELL_NPROC", raising=False)
    if configured is not None:
        monkeypatch.setenv("FACTOR_CELL_NPROC", configured)
    get_limit = Mock(return_value=(1024, 2048))
    set_limit = Mock()
    monkeypatch.setattr(resource, "getrlimit", get_limit)
    monkeypatch.setattr(resource, "setrlimit", set_limit)

    sandboxing._apply_nproc_rlimit()

    if expected is None:
        get_limit.assert_not_called()
        set_limit.assert_not_called()
    else:
        set_limit.assert_called_once_with(resource.RLIMIT_NPROC, (expected, expected))
