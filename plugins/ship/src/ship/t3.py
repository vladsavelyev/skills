"""T3 Code, where a ship run's rounds run as child threads (`delegate_task`), asked through its CLI:
the Stop hook asks it whether a round still runs (`hold.py`)."""

import json
import os

from .errors import Refusal
from .shell import run

# T3 sets it for every provider session it runs, and Claude Code passes it on to hooks and commands.
CLI_ENV = "T3CODE_CLI_PATH"


def thread_running(thread: str) -> bool:
    """Whether T3 thread `thread` has a run going. Raises if T3 doesn't know the thread."""
    cli = os.environ.get(CLI_ENV)
    if cli is None:
        raise Refusal(f"${CLI_ENV} isn't set, so this isn't a T3 Code session: can't ask T3 about thread {thread}.")
    shown = json.loads(run([cli, "thread", "show", "--json", "--messages", "0", thread], timeout=30))
    return shown["thread"]["activeRunId"] is not None
