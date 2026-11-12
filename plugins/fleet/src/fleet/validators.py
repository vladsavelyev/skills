"""Checks a new settings value must pass before `fleet set` writes it anywhere, by settings key.

A validator gets the new value and this machine's current `~/.claude/settings.json` (for the
settings that say where and how to use it), and raises `Rejected` when the value doesn't work.
Its messages must never contain the value, so they don't quote what a server answered either.
"""

import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable


class Rejected(RuntimeError):
    """The new value failed its validator."""


Validator = Callable[[str, dict], None]


class NoRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect would carry the key to another URL, and its answer isn't the gateway's: fail on the 3xx."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(NoRedirects)


def bedrock_gateway(token: str, settings: dict) -> None:
    """One `max_tokens: 1` call to the Bedrock gateway Claude Code uses, authenticated with the new token.

    The gateway disables Bedrock's free CountTokens operation (403 for any key), so the cheapest call
    that proves the key works is an invoke of the configured haiku model: about ten input tokens.
    """
    if "\n" in token or "\r" in token:
        raise Rejected("the new gateway key spans several lines; a key is one line")
    env = settings.get("env", {})
    for needed in ("ANTHROPIC_BEDROCK_BASE_URL", "ANTHROPIC_DEFAULT_HAIKU_MODEL"):
        if needed not in env:
            raise RuntimeError(f"can't validate the gateway key: ~/.claude/settings.json has no env.{needed}")
    base = env["ANTHROPIC_BEDROCK_BASE_URL"].rstrip("/")
    # Claude Code's model settings can carry a context-window tag, e.g. `...-sonnet-5-5[1m]`: not part of the id.
    model = re.sub(r"\[[^]]*\]$", "", env["ANTHROPIC_DEFAULT_HAIKU_MODEL"])
    body = {"anthropic_version": "bedrock-2023-05-31", "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]}
    url = f"{base}/model/{model}/invoke"
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with OPENER.open(request, timeout=60):
            return
    except urllib.error.HTTPError as exc:
        # The status only: an answer can echo the key, in forms a redaction would miss.
        raise Rejected(f"POST {url} with the new key: HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"can't reach the gateway to validate the key: POST {url}: {exc.reason}") from None


VALIDATORS: dict[str, Validator] = {
    "env.AWS_BEARER_TOKEN_BEDROCK": bedrock_gateway,
}
