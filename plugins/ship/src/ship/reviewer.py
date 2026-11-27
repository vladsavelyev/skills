"""Who reviews a PR, and the `delegate_task` option that runs each reviewer at the review effort.

Every round runs two reviewers side by side: the reviewer, of another model family than the
author's, with ship's own brief (the user can name another for a run); and Claude running Claude
Code's `/code-review`, which brings its own finders.

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
CODE_REVIEW = Reviewer(provider="claudeAgent", family="opus")

# The reasoning effort of every review round, whatever the PR's size or risk.
EFFORT = "high"
# `/code-review`'s own level, on a scale of its own (low…max) that sets how many finders it runs.
CODE_REVIEW_LEVEL = "high"
# The values that mark a model's option as its reasoning effort. Every model T3 listed on 2026-11-27
# has an effort option with all of them, and none had another option with the whole scale: a
# `low`/`medium`/`high` option alone could be something else.
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


def code_review_command(pr: str) -> str:
    """The command the `/code-review` reviewer runs on a PR. `--max-findings all`, or it reports
    only up to its level's limit, or up to whatever the user last typed in their own sessions."""
    return f"/code-review {CODE_REVIEW_LEVEL} {pr} --max-findings all"
