#!/bin/sh
# SessionStart hook: update the vlad-skills marketplace and its plugins in the background;
# updates apply from the next session on. Claude Code's own marketplace autoUpdate never fired in
# testing, so this does it explicitly. A failed sync is printed at the next session start, so it
# lands in front of the agent instead of disappearing.
set -eu
. "$(dirname "$0")/lib.sh"
data="$1"
mkdir -p "$data"

if [ -e "$data/sync-failed.log" ]; then
  echo "The $MARKETPLACE plugin sync failed last time ($data/sync-failed.log):"
  cat "$data/sync-failed.log"
fi

# A change made in the clone itself is on this machine only, and an update would install it into
# the plugins or replace it. Stop updating until it's moved to the repo.
root=$(marketplace_root "$MARKETPLACE")
changes=$(git -C "$root" status --porcelain)
if [ -n "$changes" ]; then
  echo "The $MARKETPLACE marketplace was not updated: its clone at $root, an installed copy that"
  echo "updates replace, has local changes (see \`git -C $root diff\`):"
  echo "$changes"
  how_to_change "the same paths"
  echo "Then discard them in the clone, so updates resume."
  exit 0
fi

# Update every plugin the marketplace lists, installing the ones this environment doesn't have yet,
# and uninstall the ones it installed that the marketplace no longer lists (moved into another plugin
# or dropped), so a stale copy doesn't stay frozen next to its replacement. Only user scope, where
# this installs; a project-scope install is the project's own choice.
# `set -e` is off inside the `if` below, so every step returns its own failure.
update_all() {
  claude plugin marketplace update "$MARKETPLACE" || return
  listed=$(jq -r '.plugins[].name' "$root/.claude-plugin/marketplace.json") || return
  installed=$(claude plugin list --json) || return
  ids=$(printf '%s' "$installed" | jq -r '.[].id') || return
  for name in $listed; do
    if printf '%s\n' "$ids" | grep -qxF "$name@$MARKETPLACE"; then
      claude plugin update "$name@$MARKETPLACE" || return
    else
      claude plugin install "$name@$MARKETPLACE" || return
    fi
  done
  user_ids=$(printf '%s' "$installed" | jq -r --arg m "@$MARKETPLACE" \
    '.[] | select(.scope == "user" and (.id | endswith($m))) | .id') || return
  for id in $user_ids; do
    if ! printf '%s\n' "$listed" | grep -qxF "${id%@"$MARKETPLACE"}"; then
      claude plugin uninstall "$id" || return
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
