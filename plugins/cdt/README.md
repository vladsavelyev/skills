# cdt

[chrome-devtools-mcp](https://github.com/ChromeDevTools/chrome-devtools-mcp) as the `cdt` MCP
server, in every project. It's the agent's own Chrome with the real debugging tools: console with
source-mapped stacks, network requests, performance traces.

It runs with no flags, so it uses chrome-devtools-mcp's default persistent profile,
`~/.cache/chrome-devtools-mcp/chrome-profile`. That's the profile navari-stack's `nstack login` signs
in to the e2e test account, so the browser keeps that login in any project. One profile also means
one browser at a time per machine: a second session that calls cdt while another holds the profile
gets "The browser is already running".

Navari's own `navari-dev` plugin runs the identical server. Claude Code starts it only once when
both are installed (checked in the navari checkout and a navari worktree), so they don't compete
for the profile.

Needs Node 20.19+ and Chrome on the machine.
