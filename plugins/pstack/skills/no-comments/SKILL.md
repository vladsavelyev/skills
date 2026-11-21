---
name: no-comments
description: "Spawn Comment Sicko, fix accepted findings, and offer encodings for claimed constraints."
disable-model-invocation: true
---

# No comments

Spawn Comment Sicko. Act on accepted findings.

Defer to Comment Sicko's fresh perspective.

The skills named below ship in this plugin. Read `../<name>/SKILL.md` from this file and follow it; the `Skill` tool refuses them.

## Scope

Use the caller's files or diff. Otherwise use the current diff against the base branch, default `main`, including the working tree.

## Steps

1. Spawn an `Agent` with `subagent_type: "pstack:comment-sicko"`. Pass the scope. Do not restate its rules.
2. Inspect its report and diff. Reject application-code edits, scope escapes, exception-protected deletions, misstated `MUST KILL` reasons, and flags that treat kept intentional code as guilty. Reshape flags on our-code surprises stay actionable. Do not restore those comments. A keep survives only with proof it is about something we cannot change. Audit missed lint and type-checker suppressions in scope. Correctness or safety suppressions stay actionable `MUST KILL`s. Restore deletions only with exact exceptions and scoped proof. Before accepting thin `IMPORTANT` or `do not remove` kills or keeps, trace their symbol or run the **why** skill on it. If a kill is ambiguous, do not restore. If a keep is refuted or still ambiguous, delete it. Revert and rerun one rejected report with the failure named. Reject a second, report it open, and fail `no-comments`.
3. Fix trivial accepted flags directly by deleting a dead path, dropping a parameter, or using the real API. If any fix needs a shape, run the **architect** skill once for the accepted set and surrounding code. Stop at the sketch. Architect shapes. Step 4 implements.
4. Implement the smallest root-cause fix in scope. Remove every named workaround. If the root cause is out of scope, land the smallest in-scope fix and draft an issue for the rest: a title and a few lines naming the code. File drafts in the project's tracker only with interactive approval or the caller's pre-approval; otherwise they go in the report. The **principle-fix-root-causes** and **principle-redesign-from-first-principles** skills guide intent only. Neither authorizes widening the fence nor fixing instances outside it. Never bolt on symptom guards.
5. A deleted comment may have explained a trap: behaviour that would look like a bug or an easy improvement to an agent that does not know why, where changing it breaks something that matters. Do not lose it. Where the project keeps such comments at the code, it survives as a keep (step 2). Otherwise, if the project records decisions (its `AGENTS.md` or `CLAUDE.md` says where, such as `docs/adr/` or `docs/decisions/`), write one there in that project's format, naming the human who decided and the PR. If no one can be named, report it open. A behaviour nobody would change without a reason gets no record, however important its area.
6. Constraint comments say `do not remove`, `do not change wording`, or `talk to X before changing`. Leave keeps about things we cannot change. Offer the cheapest in-scope type, runtime, test, or CI lint. Wait for interactive approval. Unattended and eval runs require caller pre-approval. If approved, encode then delete. Otherwise delete, report the constraint open, and sketch out-of-scope work.
7. Report the deletion count, restored comments, reruns, architect sketch, fixes, decisions recorded, drafted issues, encoding offers, encodings, unenforced constraints, and other open work.
