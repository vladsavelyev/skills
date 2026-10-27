#!/bin/sh
# Set up this marketplace in an environment: add it, turn on auto-update, install every plugin it
# lists. Run once per environment; Claude Code keeps it all synced from GitHub after that.
set -eu

claude plugin marketplace add vladsavelyev/skills

settings="$HOME/.claude/settings.json"
jq '.extraKnownMarketplaces["vlad-skills"].autoUpdate = true' "$settings" > "$settings.tmp"
mv "$settings.tmp" "$settings"

manifest="$HOME/.claude/plugins/marketplaces/vlad-skills/.claude-plugin/marketplace.json"
for plugin in $(jq -r '.plugins[].name' "$manifest"); do
  claude plugin install "$plugin@vlad-skills"
done
