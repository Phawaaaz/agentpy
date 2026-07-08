"""Stage prompt templates for the autonomous pipeline.

Each stage is one fresh, bounded Orchestrator.run() call -- a normal
single-loop run of the *unmodified* orchestrator -- seeded with the task, the
progress log, and the current diff, rather than a growing in-memory
conversation carried across stages. That keeps every stage's context small
and lets `core/context.py` compaction stay irrelevant here: a stage's whole
job fits in one bounded run.
"""

COMPLETION_INSTRUCTIONS = """
When you judge this stage's work is fully done, end your final reply with exactly:
<promise>COMPLETE</promise>
If you are blocked and cannot proceed (missing information, conflicting
requirements, a failure you cannot resolve), end your final reply with:
<promise>ABORT</promise>
followed by a short explanation of what's blocking you."""


def implement_prompt(task: str, progress: str, diff_stat: str, iteration: int) -> str:
    return f"""You are implementing the following task in an isolated git worktree \
(implement iteration {iteration}).

TASK:
{task}

PROGRESS SO FAR (from previous iterations):
{progress}

CURRENT UNCOMMITTED DIFF (--stat):
{diff_stat}

Continue the implementation. Make real, verifiable progress this iteration:
inspect before you change, and prefer small steps you can check.
{COMPLETION_INSTRUCTIONS}"""


def self_review_prompt(task: str, diff_stat: str) -> str:
    return f"""Review your own work on this task with a critical eye, as if
reviewing someone else's pull request.

TASK:
{task}

CHANGES SO FAR (--stat):
{diff_stat}

Read the changed files, look for bugs, missed edge cases, or incomplete work,
and fix anything you find. If everything looks correct and complete, say so.
{COMPLETION_INSTRUCTIONS}"""


def verify_prompt(task: str, diff_stat: str) -> str:
    return f"""Verify the implementation actually satisfies the task by
exercising it (not just reading code): run relevant commands, scripts, or
manual checks that prove the behavior works.

TASK:
{task}

CHANGES (--stat):
{diff_stat}

Report concretely what you verified and the result.
{COMPLETION_INSTRUCTIONS}"""


def test_prompt(task: str, diff_stat: str) -> str:
    return f"""Run this project's test suite (find and use its existing test
command; if the project genuinely has none, say so rather than inventing one).

TASK:
{task}

CHANGES (--stat):
{diff_stat}

End your reply with exactly one of:
<tests>PASS</tests>
<tests>FAIL</tests>
<tests>NONE</tests>
{COMPLETION_INSTRUCTIONS}"""


def repair_prompt(task: str, test_output: str, progress: str) -> str:
    return f"""The test stage reported a failure. Fix it.

TASK:
{task}

TEST STAGE OUTPUT:
{test_output}

PROGRESS SO FAR:
{progress}

Make the tests pass, then stop.
{COMPLETION_INSTRUCTIONS}"""


def sync_docs_prompt(task: str, diff_stat: str) -> str:
    return f"""Update any documentation (README, docstrings, comments) that is
now stale because of this change. Only touch docs actually affected by the
change -- do not add new documentation files unless the task required them.

TASK:
{task}

CHANGES (--stat):
{diff_stat}
{COMPLETION_INSTRUCTIONS}"""
