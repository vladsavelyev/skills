"""Who reviews a PR, and the `delegate_task` option that runs it at a round's depth.

T3 lists each model's options (`orchestrator_capabilities`), and models name and bound their effort
differently: one calls it `reasoningEffort` and another `effort`, and only some list a tier above
`max`. So the effort option is found by its values, never by a provider's name for it.
"""

from dataclasses import dataclass

from .assess import DEPTHS
from .errors import Refusal


@dataclass(frozen=True)
class Reviewer:
    provider: str
    family: str


DEFAULT = Reviewer(provider="codex", family="sol")


def effort_options(model_options: list[dict], depth: str) -> dict[str, str]:
    """`delegate_task`'s `options` for a model at `depth` (one of `DEPTHS`, or `ultra`), from the
    model's options as T3 lists them: the one option whose values include every depth from `low`
    to `max`, set to `depth`, or for `ultra` to the value T3 lists after `max`."""
    efforts = [o for o in model_options if set(DEPTHS) <= set(o["values"])]
    if len(efforts) != 1:
        ids = [o["id"] for o in model_options]
        raise Refusal(f"The model has no option, or more than one, whose values run {DEPTHS[0]}…{DEPTHS[-1]}: {ids}.")
    [effort] = efforts
    if depth != "ultra":
        return {effort["id"]: depth}
    above = effort["values"][effort["values"].index("max") + 1 :]
    if not above:
        raise Refusal(
            f"The model's {effort['id']} has nothing above max, so it can't review at ultra: review at max, "
            f"or name another reviewer."
        )
    return {effort["id"]: above[0]}
