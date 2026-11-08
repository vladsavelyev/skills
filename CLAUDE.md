# skills

The `vlad-skills` Claude Code marketplace: every plugin lives under `plugins/<name>/`. See
README.md for the layout.

- `plugins/ship` is a Python (uv) project. Run its tests and lint from there:
  `uv run --project plugins/ship pytest` and `uv run --project plugins/ship ruff check plugins/ship`.
  Its design and history are in `plugins/ship/docs/`.
- The repo is the personal `vladsavelyev/skills`. Run each `gh`/`git push` as that account, per
  command, never `gh auth switch` (the active account is machine-wide and other sessions rely on
  the work one):
  ```
  GH_TOKEN=$(gh auth token --user vladsavelyev) git push
  GH_TOKEN=$(gh auth token --user vladsavelyev) gh pr create ...
  ```
