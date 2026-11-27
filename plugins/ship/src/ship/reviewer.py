"""Who reviews a PR, and the `delegate_task` option that runs it at the review effort.

T3 lists each model's options (`orchestrator_capabilities`), and models name their effort
differently: one calls it `reasoningEffort` and another `effort`. So the effort option is found by
its values, never by a provider's name for it.
"""

from dataclasses import dataclass

from .errors import Refusal


@dataclass(frozen=True)
class Reviewer:
    provider: str
    family: str


DEFAULT = Reviewer(provider="codex", family="sol")

# The reasoning effort of every review round, whatever the PR's size or risk.
EFFORT = "high"
# The values that mark a model's option as its reasoning effort: every provider's effort option
# lists all of them, and no other option does.
_EFFORT_SCALE = ("low", "medium", "high", "xhigh", "max")


def effort_options(model_options: list[dict]) -> dict[str, str]:
    """`delegate_task`'s `options` that run a model at `EFFORT`, from the model's options as T3 lists
    them: the one option whose values include the whole effort scale."""
    efforts = [o for o in model_options if set(_EFFORT_SCALE) <= set(o["values"])]
    if len(efforts) != 1:
        ids = [o["id"] for o in model_options]
        raise Refusal(
            f"The model has no option, or more than one, whose values run {_EFFORT_SCALE[0]}…{_EFFORT_SCALE[-1]}: {ids}."
        )
    [effort] = efforts
    return {effort["id"]: EFFORT}
