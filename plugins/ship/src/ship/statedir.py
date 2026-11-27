"""Ship's own files on this machine, kept outside any repo: the sessions' holds and the features' locks."""

import os
from pathlib import Path


def ship_state_dir() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "ship"
