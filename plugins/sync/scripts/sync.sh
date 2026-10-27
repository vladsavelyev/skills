#!/bin/sh
# SessionStart hook: update the vlad-skills marketplace and its installed plugins in the
# background; updates apply from the next session on. Claude Code's own marketplace autoUpdate
# never fired in testing, so this does it explicitly. A failed sync is printed at the next
# session start, so it lands in front of the agent instead of disappearing.
set -eu
data="$1"
mkdir -p "$data"

if [ -e "$data/sync-failed.log" ]; then
  echo "The vlad-skills plugin sync failed last time ($data/sync-failed.log):"
  cat "$data/sync-failed.log"
fi

# `set -e` is off inside the `if` below, so every step returns its own failure.
update_all() {
  claude plugin marketplace update vlad-skills || return
  installed=$(claude plugin list --json) || return
  plugins=$(printf '%s' "$installed" | jq -r '[.[].id | select(endswith("@vlad-skills"))] | unique[]') || return
  for plugin in $plugins; do
    claude plugin update "$plugin" || return
  done
}

(
  if update_all > "$data/sync.log" 2>&1; then
    rm -f "$data/sync-failed.log"
  else
    mv "$data/sync.log" "$data/sync-failed.log"
  fi
) < /dev/null > /dev/null 2>&1 &
