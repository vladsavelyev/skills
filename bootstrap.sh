#!/bin/sh
# Set up this marketplace in an environment: add it and install every plugin it lists. Run once
# per environment; the `sync` plugin keeps it all updated from GitHub after that.
set -eu

claude plugin marketplace add vladsavelyev/skills

manifest="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/marketplaces/vlad-skills/.claude-plugin/marketplace.json"
for plugin in $(jq -r '.plugins[].name' "$manifest"); do
  claude plugin install "$plugin@vlad-skills"
done
