"""The autonomous multi-stage pipeline: composes core.orchestrator.Orchestrator
into a higher-level loop (implement -> self-review -> verify -> test ->
sync-docs) over an isolated git worktree, with stuck detection, iteration
caps, timeouts, and repair caps. See DESIGN.md D15 for why this is a separate
package rather than a change to the base loop.
"""
