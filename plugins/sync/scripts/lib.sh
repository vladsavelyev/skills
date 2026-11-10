# Shared by sync.sh and guard.sh. POSIX sh, sourced.

MARKETPLACE=vlad-skills
CLAUDE_HOME=${CLAUDE_CONFIG_DIR:-$HOME/.claude}
PLUGINS="$CLAUDE_HOME/plugins"

# Where Claude Code keeps its clone of a marketplace.
marketplace_root() {
  jq -er --arg m "$1" '.[$m].installLocation' "$PLUGINS/known_marketplaces.json"
}

# Where a marketplace comes from, as a URL or path a person can open.
marketplace_source() {
  jq -er --arg m "$1" '.[$m].source | if .repo then "https://github.com/\(.repo)" else (.url // .path) end' \
    "$PLUGINS/known_marketplaces.json"
}

# How a change to this marketplace's file $1 (relative to the repo root) reaches every machine.
# The fleet's rules are in its agents/AGENTS.md, section "Fleet of machines".
how_to_change() {
  echo "Make the change in a checkout of $(marketplace_source "$MARKETPLACE") (the laptop keeps one at ~/git/skills;" \
    "elsewhere, clone it), in $1, on a branch with a PR. Once it's merged, each machine's Claude Code has it" \
    "from its second session start: the sync plugin pulls the marketplace in the background at each start."
  if [ "$1" = agents/AGENTS.md ]; then
    echo "That file holds the rules every machine shares; Codex gets them from the next \`fleet --apply\` on the" \
      "laptop, which regenerates ~/.codex/AGENTS.md everywhere. What only this machine needs goes below the" \
      "fleet marker in ~/.claude/CLAUDE.md instead: edit it there directly."
  fi
}
