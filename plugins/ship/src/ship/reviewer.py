"""Who reviews a PR: a model the review skill delegates to through T3 Code, and the
`delegate_task` options that make it review at a round's depth."""

from dataclasses import dataclass

from .errors import Refusal


@dataclass(frozen=True)
class Effort:
    """How a T3 provider takes a reasoning effort: the option's id, and its value for `ultra`, the
    tier above `max` a round takes only when the user asks. `low` to `max` are spelled the same by
    every provider here."""

    option: str
    ultra: str


# Each provider names its effort option its own way; `delegate_task` rejects any other name.
EFFORTS = {
    "codex": Effort(option="reasoningEffort", ultra="ultra"),
    "claudeAgent": Effort(option="effort", ultra="ultrathink"),
}


@dataclass(frozen=True)
class Reviewer:
    provider: str  # a T3 provider instance
    family: str  # a model family; a round takes the newest model of it the provider lists

    def options(self, depth: str) -> dict[str, str]:
        """`delegate_task`'s `options` for a review at `depth` (one of `assess.DEPTHS`, or `ultra`)."""
        effort = EFFORTS.get(self.provider)
        if effort is None:
            raise Refusal(
                f"No effort option known for T3 provider '{self.provider}'. Add it to "
                f"`reviewer.EFFORTS` from the model's options in `orchestrator_capabilities`."
            )
        return {effort.option: effort.ultra if depth == "ultra" else depth}


# Who reviews when the user names no other reviewer when they ask to ship.
DEFAULT = Reviewer(provider="codex", family="sol")
