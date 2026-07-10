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
    auto_push: bool = False
    auto_pr: bool = False

    @classmethod
    def load(cls) -> "PipelineConfig":
        yaml_data = {}
        for filename in (".harness.yaml", ".harness.yml"):
            if os.path.exists(filename):
                try:
                    import yaml
                    with open(filename, "r", encoding="utf-8") as f:
                        parsed = yaml.safe_load(f) or {}
                        if isinstance(parsed, dict):
                            yaml_data = parsed.get("pipeline", {}) or {}
                        break
                except Exception:
                    pass

        def get_val(env_name: str, yaml_key: str, default, type_conv=None):
            env_val = os.getenv(env_name)
            if env_val is not None:
                if type_conv == bool:
                    return str(env_val).lower() in ("true", "yes", "1")
                return type_conv(env_val) if type_conv else env_val
            yaml_val = yaml_data.get(yaml_key)
            if yaml_val is not None:
                if type_conv == bool:
                    if isinstance(yaml_val, str):
                        return yaml_val.lower() in ("true", "yes", "1")
                    return bool(yaml_val)
                return type_conv(yaml_val) if type_conv else yaml_val
            return default

        return cls(
            max_iterations=get_val("HARNESS_PIPELINE_MAX_ITERATIONS", "max_iterations", cls.max_iterations, int),
            stuck_after=get_val("HARNESS_PIPELINE_STUCK_AFTER", "stuck_after", cls.stuck_after, int),
            slice_timeout_s=get_val("HARNESS_PIPELINE_SLICE_TIMEOUT", "slice_timeout_s", cls.slice_timeout_s, int),
            max_repair_attempts=get_val("HARNESS_PIPELINE_MAX_REPAIR_ATTEMPTS", "max_repair_attempts", cls.max_repair_attempts, int),
            auto_push=get_val("HARNESS_PIPELINE_AUTO_PUSH", "auto_push", cls.auto_push, bool),
            auto_pr=get_val("HARNESS_PIPELINE_AUTO_PR", "auto_pr", cls.auto_pr, bool),
        )
