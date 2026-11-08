# skills

The `vlad-skills` Claude Code marketplace: every plugin lives under `plugins/<name>/`. See
README.md for the layout.

- `plugins/ship` and `plugins/fleet` are Python (uv) projects, stdlib only. Run tests and lint from
  inside the plugin's directory (from the repo root pytest collects every plugin's tests):
  `uv run pytest -q`, `uv run ruff check . && uv run ruff format --check .`.
  ship's design and history are in `plugins/ship/docs/`.
- The repo is the personal `vladsavelyev/skills`. Run each `gh`/`git push` as that account, per
  command, never `gh auth switch` (the active account is machine-wide and other sessions rely on
  the work one):
  ```
  GH_TOKEN=$(gh auth token --user vladsavelyev) git push
  GH_TOKEN=$(gh auth token --user vladsavelyev) gh pr create ...
  ```
