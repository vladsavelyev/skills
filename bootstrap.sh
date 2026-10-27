#!/bin/sh
# Set up this marketplace in an environment: add it and install every plugin it lists. Run once
# per environment; the `sync` plugin keeps it all updated from GitHub after that.
set -eu

claude plugin marketplace add vladsavelyev/skills

root=$(claude plugin marketplace list --json | jq -er '.[] | select(.name == "vlad-skills") | .installLocation')
for plugin in $(jq -r '.plugins[].name' "$root/.claude-plugin/marketplace.json"); do
  claude plugin install "$plugin@vlad-skills"
done
