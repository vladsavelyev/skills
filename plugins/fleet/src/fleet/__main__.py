"""`fleet [--apply]`: check (or sync) the hosts' Claude Code setup against this machine's."""

import argparse
import sys

from . import sync
from .shell import ToolMissing


def main() -> int:
    parser = argparse.ArgumentParser(prog="fleet", description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Fix the drift instead of only reporting it")
    args = parser.parse_args()
    try:
        return sync.sync(apply=args.apply)
    except (ToolMissing, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
