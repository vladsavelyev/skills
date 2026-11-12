"""`fleet [--apply]`: check (or sync) the hosts' Claude Code setup against this machine's.
`fleet set KEY`: give one ~/.claude/settings.json key a new value on every machine."""

import argparse
import sys

from . import setkey, sync
from .shell import ToolMissing


def main() -> int:
    parser = argparse.ArgumentParser(prog="fleet", description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Fix the drift instead of only reporting it")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")
    set_parser = commands.add_parser(
        "set",
        help="Give one ~/.claude/settings.json key a new value on every machine (e.g. rotate a secret)",
        description=setkey.__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    set_parser.add_argument(
        "key", help="dotted key path, e.g. env.AWS_BEARER_TOKEN_BEDROCK; the value is read from stdin or a prompt"
    )
    args = parser.parse_args()
    if args.command == "set" and args.apply:
        parser.error("`fleet set` always applies: drop --apply")
    try:
        if args.command == "set":
            return setkey.run(args.key)
        return sync.sync(apply=args.apply)
    except (ToolMissing, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
