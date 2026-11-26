"""Who reviews a PR: a model the review skill delegates to through T3 Code."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Reviewer:
    provider: str  # a T3 provider instance
    family: str  # a model family; a round takes the newest model of it the provider lists


# Who reviews when the user names no other reviewer when they ask to ship.
DEFAULT = Reviewer(provider="codex", family="sol")
