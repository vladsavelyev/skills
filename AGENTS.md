# skills

The `vlad-skills` Claude Code marketplace: every plugin lives under `plugins/<name>/`. See
README.md for the layout.

- `plugins/ship` and `plugins/fleet` are Python (uv) projects, stdlib only. Run tests and lint from
  inside the plugin's directory (from the repo root pytest collects every plugin's tests):
  `uv run pytest -q`, `uv run ruff check . && uv run ruff format --check .`.
  ship's design and history are in `plugins/ship/docs/`.
- `plugins/sync` is POSIX sh with `jq`; its tests are `plugins/sync/tests/test.sh`.

## Agent skills

### Issue tracker

GitHub Issues on `vladsavelyev/skills`, via `gh`. See `docs/agents/issue-tracker.md`.

### Triage labels

The five default roles, each label named after its role (`needs-triage`, `needs-info`,
`ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
