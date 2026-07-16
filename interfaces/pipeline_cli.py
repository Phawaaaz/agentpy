"""Pipeline CLI -- the autonomous multi-stage entry point (see pipeline/).

Deliberately thin, same shape as interfaces/cli.py: build the provider and
tool registry through the existing factory, construct a PipelineRunner, print
its progress. All pipeline logic lives in pipeline/; no agent logic here.
"""

import sys

from config import Config
from engine.registry import registry
from pipeline.config import PipelineConfig
from pipeline.runner import PipelineRunner
from providers.factory import build_provider

# Importing these modules registers their tools onto the shared registry.
import engine.builtin.filesystem  # noqa: F401
import engine.builtin.git_tool  # noqa: F401
import engine.builtin.github_tool  # noqa: F401
import engine.builtin.offload
import engine.builtin.planning  # noqa: F401
import engine.builtin.search_files  # noqa: F401
import engine.builtin.shell  # noqa: F401
import engine.builtin.web  # noqa: F401
from engine.builtin.search import build_search_tool


def _on_event(kind: str, *details) -> None:
    if kind == "slice_started":
        slice_id, worktree_path, branch = details
        print(f"[{slice_id}] worktree: {worktree_path}  branch: {branch}")
    elif kind == "slice_complete":
        print(f"  -> implement loop signaled COMPLETE after {details[0]} iteration(s)")
    elif kind == "slice_aborted":
        iteration, answer = details
        print(f"  -> implement loop ABORTED at iteration {iteration}:\n{answer}")
    elif kind == "slice_stuck":
        iteration, count = details
        print(f"  -> stuck: {count} consecutive iterations with no change (at iteration {iteration})")
    elif kind == "slice_timeout":
        print(f"  -> slice timed out at iteration {details[0]}")
    elif kind == "slice_max_iterations":
        print(f"  -> reached max iterations ({details[0]}) without completing")
    elif kind == "repair_exhausted":
        print(f"  -> repair attempts exhausted (attempt {details[0]}); tests still failing")
    elif kind == "slice_finished":
        slice_id, status, diff = details
        print(f"\n[{slice_id}] finished: {status}\n{diff}")
    # tool_call/tool_result/thinking are intentionally not printed here --
    # the pipeline's output stays focused on stage/slice progress; use the
    # interactive CLI (main.py) when you want to watch every tool call.


def main() -> None:
    if len(sys.argv) < 2:
        print('usage: python pipeline.py "<task description>"')
        sys.exit(1)
    task = " ".join(sys.argv[1:])

    config = Config.load()
    # The autonomous pipeline isolates via git worktrees (it chdir's into a
    # per-slice worktree), not the per-session workspace/container model the
    # sandbox is built around. Rather than silently run commands on the host
    # when the user set HARNESS_SANDBOX=docker (making them believe otherwise
    # on the *unattended* path), fail loud -- fixed the silent-ignore bug the
    # verification review found.
    if config.sandbox == "docker":
        raise SystemExit(
            "HARNESS_SANDBOX=docker is not supported by the autonomous pipeline "
            "(it isolates via git worktrees, not per-session containers). Unset "
            "HARNESS_SANDBOX for `python pipeline.py`, or use the interactive CLI "
            "(main.py) for sandboxed command execution."
        )
    if config.permission_mode == "ask":
        print(
            "warning: HARNESS_PERMISSION_MODE is 'ask', but no human is present to answer "
            "prompts during an autonomous run -- write/dangerous actions will be denied and "
            "the pipeline will likely get stuck immediately. Set it to 'allowlist' or 'auto' "
            "for the pipeline to make real progress.\n"
        )
    engine.builtin.offload.set_offload_root(config.offload_dir)
    # One web_search tool, two backends: Tavily with a key, DuckDuckGo
    # fallback without (D25).
    registry.register(build_search_tool(config.search_api_key))
    provider = build_provider(config)
    runner = PipelineRunner(provider, registry, config, PipelineConfig.load(), on_event=_on_event)

    try:
        result = runner.run(task)
    except Exception as exc:
        # A provider failure that survives retries/fallback (D26), or a git
        # failure, shouldn't end as a raw traceback: report and exit
        # non-zero so scripts can react (I3 -- graceful loop-level errors).
        print(f"\npipeline failed: {exc}")
        sys.exit(1)
    print("\n" + "=" * 60)
    print(f"status:      {result.status}")
    print(f"stage:       {result.stage}")
    print(f"iterations:  {result.iterations}")
    print(f"branch:      {result.branch}")
    print(f"worktree:    {result.worktree_path}")
    print(f"diff --stat:\n{result.diff_stat}")
    print("=" * 60)
    print(
        f"\nNothing was pushed. Review the branch in {result.worktree_path}, "
        "then push and open a PR yourself when you're happy with it."
    )


if __name__ == "__main__":
    main()
