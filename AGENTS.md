# skills

The `vlad-skills` Claude Code marketplace: every plugin lives under `plugins/<name>/`. See
README.md for the layout.

- `plugins/ship` and `plugins/fleet` are Python (uv) projects, stdlib only. Run tests and lint from
  inside the plugin's directory (from the repo root pytest collects every plugin's tests):
  `uv run pytest -q`, `uv run ruff check . && uv run ruff format --check .`.
  ship's design and history are in `plugins/ship/docs/`.
- `plugins/sync` is POSIX sh with `jq`; its tests are `plugins/sync/tests/test.sh`.
