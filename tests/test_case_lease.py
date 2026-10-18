"""CLI and UI share case ownership; stale status files cannot own a case."""

import pytest
from test_capabilities import public_case

from factor_demo.case_lease import case_lease, case_running
from factor_demo.research_workspace import prepare_workspace


def test_lease_releases_after_failure_and_prevents_second_owner(tmp_path):
    case = tmp_path / "case"
    assert not case_running(case)
    with pytest.raises(RuntimeError), case_lease(case):
        assert case_running(case)
        with pytest.raises(ValueError, match="active in another process"), case_lease(case):
            raise AssertionError("second owner")
        raise RuntimeError("cancelled")
    assert not case_running(case)


def test_new_case_can_be_prepared_while_lease_exists(tmp_path):
    source = public_case(tmp_path / "source") / "input"
    run = tmp_path / "new"
    with case_lease(run):
        prepare_workspace(source, run)
    assert (run / "session.json").is_file()
