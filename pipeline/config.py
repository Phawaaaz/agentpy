"""Pipeline safety-rail settings, resolved once from env vars.

Separate from config.Config (which governs one Orchestrator.run() call) since
these bound the *outer* loop: how many implement iterations, how long to wait
for progress before giving up, and how many repair attempts a failing test
stage gets.
"""

import os
from dataclasses import dataclass


@dataclass
class PipelineConfig:
    max_iterations: int = 20
    stuck_after: int = 3  # consecutive no-diff iterations before auto-stop
    slice_timeout_s: int = 1800  # wall-clock budget per slice
    max_repair_attempts: int = 5  # extra implement rounds after a failed test stage

    @classmethod
    def load(cls) -> "PipelineConfig":
        return cls(
            max_iterations=int(
                os.getenv("HARNESS_PIPELINE_MAX_ITERATIONS", str(cls.max_iterations))
            ),
            stuck_after=int(os.getenv("HARNESS_PIPELINE_STUCK_AFTER", str(cls.stuck_after))),
            slice_timeout_s=int(
                os.getenv("HARNESS_PIPELINE_SLICE_TIMEOUT", str(cls.slice_timeout_s))
            ),
            max_repair_attempts=int(
                os.getenv(
                    "HARNESS_PIPELINE_MAX_REPAIR_ATTEMPTS", str(cls.max_repair_attempts)
                )
            ),
        )
