#!/bin/sh
# SessionStart hook: update the vlad-skills marketplace and its plugins in the background;
# updates apply from the next session on. Claude Code's own marketplace autoUpdate never fired in
# testing, so this does it explicitly. A failed sync is printed at the next session start, so it
# lands in front of the agent instead of disappearing.
set -eu
data="$1"
mkdir -p "$data"

if [ -e "$data/sync-failed.log" ]; then
  echo "The vlad-skills plugin sync failed last time ($data/sync-failed.log):"
  cat "$data/sync-failed.log"
fi

# Update every plugin the marketplace lists, installing the ones this environment doesn't have yet.
# `set -e` is off inside the `if` below, so every step returns its own failure.
update_all() {
  claude plugin marketplace update vlad-skills || return
  marketplaces=$(claude plugin marketplace list --json) || return
  root=$(printf '%s' "$marketplaces" | jq -er '.[] | select(.name == "vlad-skills") | .installLocation') || return
  listed=$(jq -r '.plugins[].name' "$root/.claude-plugin/marketplace.json") || return
  installed=$(claude plugin list --json) || return
  ids=$(printf '%s' "$installed" | jq -r '.[].id') || return
  for name in $listed; do
    if printf '%s\n' "$ids" | grep -qxF "$name@vlad-skills"; then
      claude plugin update "$name@vlad-skills" || return
    else
      claude plugin install "$name@vlad-skills" || return
    fi
  done
}

(
  if update_all > "$data/sync.log" 2>&1; then
    rm -f "$data/sync-failed.log"
  else
    mv "$data/sync.log" "$data/sync-failed.log"
  fi
) < /dev/null > /dev/null 2>&1 &
