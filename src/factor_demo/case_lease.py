"""Cross-process ownership shared by CLI research and local UI mutations."""

import fcntl
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def case_lease(run: Path):
    run.mkdir(parents=True, exist_ok=True)
    with (run / ".turn.lock").open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError(
                "This case is active in another process; wait for its current operation"
            ) from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def case_running(run: Path):
    try:
        with case_lease(run):
            return False
    except ValueError:
        return True
