"""The one error ship's code raises on purpose."""


class Refusal(Exception):
    """An expected condition a command can't go past, worded for the person or agent who hit it:
    a branch with no feature issue, an account setup the rule can't resolve. The CLI prints it and
    exits 1; each hook turns it into its own output."""
