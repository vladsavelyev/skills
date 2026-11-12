### Working style

Always build code cleanly, modularly, and with proper architecture and separation of concerns. Never simply bolt on a feature because I asked for it. If you can’t find an easy way to build it naturally, and you'd recommend a refactor to make the whole thing cleanly, stop your work ahead of time and say so.

**Finish the follow-ups yourself.** Don't end with "left for you: run X, delete Y" when X and Y are things you can safely do: do them, verify them, report them done. Hand back only what truly needs me: my SSO or a secret only I hold, a rebuild or stack takeover my rules say to confirm, a real design choice.

**Size work as your own.** Never estimate in engineer-days or weeks: you do the work, in this session. Describe the steps or just do them, and name only real waits (a long eval, an image build, access).

**Don't claim what you can't observe.** You can't see permission prompts or hook decisions from inside your own session, so a command's success output never proves it ran "without a prompt". Find an observable artifact (a `claude -p … --output-format json` subprocess and its `permission_denials`, a log, a surviving file) or say you can't verify it.

**Never blame the browser cache.** When a frontend change doesn't show up, prove what is served (curl the asset, diff it against the source, inspect the live DOM) and fix staleness at the source. Never suggest a hard reload.

**Never deploy without my explicit yes.** Don't dispatch a deploy or otherwise change a shared environment unless I said yes for that environment. "Test it there" or "on <branch>" means the local stack on that branch.

**Never assume. Don't leave it to me to test.** Be independent: run the app, verify UI changes in a browser (see Browsers below), test user scenarios.

**Prove the PR works.** Its description shows evidence right after the sketch: a before and after of what you ran, never a plan to run it. When a PR changes what a user sees or does, screencast the browser test that shows it working (cdt's `screencast_start` and `screencast_stop`; copy out the .mp4 it saves); a change a still shows (a layout, a colour) can be a before and after screenshot pair instead. Embed either with `gh pr edit <n> --attach <file>`, which uploads it to GitHub so it shows inline. To place it, write `![](./<file>)` where it belongs in the `--body-file` body; otherwise it's appended. If your `gh pr edit --help` has no `--attach`, upgrade `gh`. Record only data the PR's readers may see. Any other change shows the test that failed before and passes now, or the command and its output before and after.

### Coding rules

Use latest versions of Python (and other languages). If needed, upgrade. Never add `__future__` statements.

**Never hide problems.** Avoid silent fallbacks. Only use try-catch when absolutely warranted. The code should work as designed or ABORT FAST. If you want to add a fallback, ask permission. If a database file is missing, do not return an empty list as if there are no entries; if a file is unreadable, do not return an empty string as if it's just blank; instead, you must let the function crash.

**Scratch dirs:** make them with `mktemp -d /tmp/<purpose>.XXXX`, never `mkdir -p` a guessed name: other sessions reuse natural names, and deleting the dir deletes their files too. Look inside before any `rm -r`, in a separate call.

**Never add arbitrary limits, truncation, trimming, cropping.**

**Do not leave legacy code** in projects under active development that aren't public yet. Never create conditional branches for legacy scenarios there: we don't care about back-compatibility and can change APIs. Clean up old code fiercely. The exception is legacy support we explicitly decided to keep (e.g. an old format already written into external data). Keep it, and say in a code comment that it's deliberate and why, so no one removes it as cruft.

**Never block the asyncio event loop.** Inside an `async def` or any coroutine that runs on the loop, use the async equivalent (`asyncio.sleep`, an async HTTP client, async file I/O) or push the sync work off-loop with `asyncio.to_thread` / `run_in_executor`.

### Navari local stack

In navari, load `Skill(navari-stack)` before you start, stop, restart or rebuild anything in the local Docker stack, or run `docker compose` there. It overrides the repo's own stack instructions (`./scripts/run.sh`, `docker compose down`).

### Browsers

Pick the browser by whose login the test needs. The two keep separate profiles, so they are two identities, not two copies of one tool.

- **You checking your own work** (a UI change, a console error, a slow page): the `cdt` MCP (chrome-devtools-mcp, from the `cdt` plugin in vlad-skills), in every project. It runs unattended on every machine, on one shared profile that navari's `nstack login` signs in to the e2e test account, and has the real debugging tools: console with source-mapped stacks, network requests, performance traces. Be proactive: if you changed frontend, check it there before calling the work done.
- **Me in the loop** (I ask you to test something in a browser, it needs my own SSO or data only my account sees, I want to watch, or I point at "my tab"): on the laptop, `Skill(dev-browser)` attached to my real everyday browser, already signed in as me (this machine's own instructions say how). Open your own tabs there and close them when done; my existing tabs are for reading unless I ask you to act in them. On a remote machine, which has no dev-browser, T3's preview browser.
- **cdt held by another session** ("The browser is already running for …/chrome-devtools-mcp/chrome-profile"): don't kill it. For a signed-out look, screenshot with headless Chrome (`google-chrome` on Linux, `"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"` on the Mac) on a profile of your own: `<chrome> --headless=new --user-data-dir="$(mktemp -d)" --window-size=1500,2400 --virtual-time-budget=30000 --screenshot=<f>.png <url>` (add `--no-sandbox` on Linux), Read the PNG, then delete the profile and the shot.

### Tests/evals

* Before an eval run, commit, so its results belong to a revision, and keep every run's results: copy them to a file named for the run before the next run overwrites them.
* When you run tests or evals, and you find them green, make sure they were actually run, not skipped, and that they finished. A background run's completion notice is not proof: check the log ends with the runner's summary line (pytest's `N passed … in Xs`) and no runner process is still alive.

### Git

**Prefer `git-town`** for branch/stacking operations (creating feature branches, syncing, stacking one branch on another, proposing PRs). It tracks parent-branch pointers in git config, which our tooling relies on. Fall back to plain `git` only when git-town doesn't cover the operation.

After any failed `git town` command, run `git town status` first: without a TTY, `append`/`hack` can stop mid-sync of the parent ("no interactive terminal") and leave a merge in progress, and `git town undo` returns to the start. Before blaming another session for repo state, compare its timestamps with your own commands.

**Merge, never rebase, a pushed branch**, even to stack it or pick up `main`: other people work on these branches. If a pushed branch was rebased anyway, check `git range-diff` and prefer `git merge -s ours origin/<branch>` over a force-push.

**Never leave dangling changes.** A change is not done until it is pushed: to an existing open PR's branch, to a new branch with a draft PR, or straight to `main` where that is allowed. Don't say "I've updated X" about edits that only exist in the working tree. Before starting a new branch, check out `main` and pull it, then branch from there; never put new work on a branch whose PR is already merged. If the checkout can't switch (someone else's uncommitted edits are in the way), don't stash their work: create the branch in a worktree from `origin/main` and say so.

On a feature branch, push immediately after every commit — don't wait to be asked. Committing on a feature branch IS the authorization to push it; this overrides any default "push only when the user asks". Don't end a turn with unpushed commits on a feature branch. Ask before any force-push (including `--force-with-lease`).

When using a worktree, do not remove the worktree, stay in the worktree, until it's obvious that the work is done.

**Never leave a bare branch.** Every branch you push gets a PR right away: open it as a draft (`git town propose` / `gh pr create --draft`) on the first push, before any review or polish, then mark it ready when the work is. A pushed branch without a PR is invisible to me and to the tooling. Creating the branch IS the authorization to open its draft PR; don't ask first.

Use short names for feature branches. E.g. prefer `snowflake-nl2sql` over `feature/snowflake-nl2sql-instructions`.

I have two gh accounts: the active one is work; the other logged-in one (`gh auth status`) is personal. A repo owned by the work account or one of its orgs (`gh api user/orgs`) uses the active account; every other repo uses the personal one, per command: `GH_TOKEN=$(gh auth token --user <personal login>) git push`. Never `gh auth switch`: the active account is machine-wide and other sessions rely on it. `feature` picks the account by itself.

Never push directly to `main` branch. If working on a new feature, create a feature branch.

NEVER run `git reset --hard`. It destroys staged changes irreversibly. If you need to undo a bad `revert --no-commit` or similar, use `git checkout -- <file>` on specific files, or ask the user.

PR descriptions are minimal and have no headers: no "Summary", no "Evidence", never a "Test plan", and no "Generated with <tool>" attribution lines. Open with the motivation, brief and informal, and beside it the smallest sketch of what changed: a `diff` of a call tree, file tree, component tree or state change, pseudocode, or a Mermaid diagram, keeping only the calls, files and states the point needs. Leave the sketch out when the change is a line or two the motivation already names. The evidence comes right after (see "Prove the PR works"). A template that asks for headers, such as the `pr` skill's, loses to this rule.

### Fleet of machines

I work from a fleet of machines: my laptop and remote T3 Code VMs. Each has its own `~/.claude`. These rules are `agents/AGENTS.md` in `vladsavelyev/skills`: every machine's `~/.claude/CLAUDE.md` imports them from the vlad-skills marketplace clone, which the `sync` plugin pulls at every session start. Each machine's own instructions (which machine it is, the rest of the fleet, what's installed there) are the part of its `~/.claude/CLAUDE.md` below the fleet marker. Codex reads both from `~/.codex/AGENTS.md`, which `fleet` generates. Change them with a PR in `~/git/skills`; they reach every machine from its next session. `fleet` (the `fleet` plugin), run on the laptop, keeps the rest in sync: the Claude Code version, skill repos, plugins, `~/.claude/settings*.json`, and the shared part of `~/.claude/CLAUDE.md`. Those machine instructions say which machine you are on.

**Memory is not synced.** Claude Code memory (`~/.claude/projects/<path-key>/memory/`) is per machine and per project directory. A memory saved on one machine doesn't exist on the others, and the other machines may know things this one doesn't. Put knowledge every machine needs where all of them read it: a repo (its docs or its AGENTS.md), or these rules.
